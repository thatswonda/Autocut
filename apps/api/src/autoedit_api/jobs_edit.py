"""Edit-mode planning inside the render job: transcripts, frames, Claude's plan, EDL.

Runs in the render job's own process. Transcription loads a Whisper model (~350 MB with ``base``) and
releases it before the frames are sampled and the video is rendered, so the peaks never stack.
"""

from __future__ import annotations

import gc
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from autoedit_motion import storyboard
from autoedit_motion.storyboard import StoryboardError, Usage, ai_available, max_cost
from autoedit_render.planner import (
    ClipMaterial,
    EditPlan,
    EditRequest,
    PlanError,
    frame_budget,
    plan_rules,
    plan_to_edl,
    plan_with_ai,
    sample_frames,
)
from autoedit_schemas import EDL, Signals
from autoedit_schemas.transcript import Transcript

from .db import ProjectClip, UsageEvent
from .jobs import _record_artifact
from .storage import Storage, clip_prefix

log = logging.getLogger(__name__)


def _transcript(
    db: Session, storage: Storage, clip: ProjectClip, media: Path, sig: Signals
) -> Transcript | None:
    """The clip's transcript: the stored one, or a fresh one that is stored for next time."""
    art = next((a for a in clip.artifacts if a.kind == "transcript"), None)
    if art is not None:
        try:
            return Transcript.model_validate_json(storage.read_bytes(art.storage_key))
        except Exception:
            log.warning("stored transcript for %s unreadable; transcribing again", clip.id)
    if not sig.media.has_audio:
        return None
    speech = sig.summary.audio.speech_ratio if sig.summary.audio else 0.0
    if speech < 0.03:
        return Transcript(clip_id=clip.id, duration_s=sig.media.duration_s)
    from autoedit_pipeline.transcribe import transcribe

    try:
        tr = transcribe(media, clip_id=clip.id)
    except Exception as exc:
        log.warning("transcription failed for %s: %s", clip.id, exc)
        return None
    finally:
        gc.collect()
    key = clip_prefix(clip.upload.user_id, clip.project_id, clip.id) + "transcript.json"
    size = storage.put_bytes(key, tr.model_dump_json().encode(), "application/json")
    _record_artifact(db, clip, clip.upload, "transcript", key, size)
    db.commit()
    return tr


def plan_edit(
    db: Session,
    storage: Storage,
    *,
    project: Any,
    clips: list[ProjectClip],
    files: dict[str, Path],
    sigs: dict[str, Signals],
    settings: dict[str, Any],
    progress: Callable[[float, str], None],
) -> tuple[EDL, dict[str, Any]]:
    """Returns the EDL to render and a report (plan, how it was made, cost)."""
    req = EditRequest(
        prompt=(settings.get("prompt") or project.prompt or "").strip(),
        aspect=settings.get("aspect", "9:16"),
        target_duration_s=settings.get("target_duration_s"),
        color_preset=settings.get("color_preset") or "auto",
        captions=settings.get("captions"),
        caption_style=settings.get("caption_style"),
        title=project.title,
        music=bool(settings.get("music_asset_id")),
    )
    mats: list[ClipMaterial] = []
    usable = [c for c in clips if c.id in files]
    for i, c in enumerate(usable):
        progress(0.02 + 0.25 * i / max(1, len(usable)), "Listening to your video")
        tr = _transcript(db, storage, c, files[c.id], sigs[c.id])
        mats.append(ClipMaterial(c.id, c.position, sigs[c.id], tr))
    note = ""
    usage = Usage()
    plan: EditPlan | None = None
    if settings.get("plan"):
        plan = EditPlan.model_validate(settings["plan"]).model_copy(update={"source": "user"})
    elif ai_available():
        budget = frame_budget(mats)
        for i, m in enumerate(mats):
            progress(0.3 + 0.15 * i / max(1, len(mats)), "Watching your video")
            try:
                m.frames = sample_frames(files[m.clip_id], m.signals, budget[m.clip_id])
            except Exception as exc:
                log.warning("frame sampling failed for %s: %s", m.clip_id, exc)
        progress(0.5, "Planning the edit")
        try:
            plan = plan_with_ai(mats, req, storyboard.CLIENT_FACTORY(), usage, max_cost_usd=max_cost())
        except (PlanError, StoryboardError) as exc:
            note = f"The AI edit could not be made ({exc}). This is an automatic cut instead."
            log.warning("AI edit planning failed: %s", exc)
        for m in mats:
            m.frames = []
    else:
        note = "AI editing is off (no Anthropic key on the server), so this is an automatic cut."
    if plan is None:
        plan = plan_rules(mats, req)
    edl = plan_to_edl(plan, mats, req)
    if usage.calls:
        db.add(UsageEvent(user_id=project.user_id, project_id=project.id, stage="edit_plan",
                          cost_usd=usage.cost_usd, details={"model": usage.model, "calls": usage.calls,
                                                            "input_tokens": usage.input_tokens,
                                                            "output_tokens": usage.output_tokens}))  # fmt: skip
        db.commit()
    report = {
        **edl.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "planning": {
            "source": plan.source,
            "note": note,
            "cost_usd": usage.cost_usd,
            "transcribed": [m.clip_id for m in mats if m.transcript and m.transcript.words],
            "language": edl.meta.language,
        },
    }
    return edl, report
