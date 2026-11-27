"""AI editing (PRD 5.2 stages C-E, 5.3): footage + transcript + signals + the user's prompt -> EDL.

One Claude call sees sampled frames from every clip, the word-timed transcript and the local analysis,
and returns an ``EditPlan``: which ranges to keep and why, their order, the grade and the caption style.
Code then does everything that must be exact, so the model never does timeline arithmetic:

* boundaries are snapped to word edges (never cut mid-word) and padded naturally;
* with ``tighten`` on, filler words, stutters and pauses inside kept ranges become jump cuts;
* removed ranges are labelled; captions come from the transcript, mapped to the output timeline;
* the result is a schema-validated ``EDL``.

Without Claude (no key, or AI turned off) ``plan_rules`` builds the same kind of plan from the
transcript and signals, so the renderer path is identical.
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from autoedit_schemas.edl import (
    EDL,
    Audio,
    CaptionWord,
    ClipRef,
    Color,
    Effect,
    EffectType,
    Music,
    RemovedRange,
    Segment,
    Sfx,
    SfxType,
)
from autoedit_schemas.signals import Signals
from autoedit_schemas.transcript import Transcript, Word

from .captions import STYLES as CAPTION_STYLES
from .presets import COLOR_PRESETS

log = logging.getLogger(__name__)

_MOVES = {"zoom_in", "zoom_out", "pan_left", "pan_right", "shake"}
_TRANSITIONS = {"cut", "zoom", "whip", "flash", "glitch", "fade"}
_TRANSITION_SFX = {"zoom": ("whoosh", -13.0), "whip": ("swoosh", -12.0), "flash": ("impact", -12.0),
                   "glitch": ("glitch", -15.0)}  # fmt: skip
_HIT_SFX = {"impact": ("impact", -10.0), "flash": ("impact", -11.0), "glitch": ("glitch", -14.0),
            "shake": ("boom", -10.0)}  # fmt: skip

PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = "edit.v2"
FRAME_BUDGET = 64  # frames sent to Claude across all clips (~350 tokens each at 512 px)
MIN_PIECE_S = 0.35
LEAD_PAD_S = 0.10
TAIL_PAD_S = 0.18
INNER_PAUSE_S = 0.6


class PlanError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# ------------------------------------------------------------------------------------- inputs


@dataclass
class EditRequest:
    prompt: str = ""
    aspect: str = "9:16"
    target_duration_s: float | None = None
    color_preset: str = "auto"  # "auto": chosen from the prompt
    captions: bool | None = None  # None: AI decides (on for speech)
    caption_style: str | None = None
    language: str | None = None
    title: str = ""
    music: bool = False


@dataclass
class ClipMaterial:
    clip_id: str
    order: int
    signals: Signals
    transcript: Transcript | None = None
    frames: list[tuple[float, bytes]] = field(default_factory=list)  # (t, jpeg)

    @property
    def duration_s(self) -> float:
        return self.signals.media.duration_s


# ---------------------------------------------------------------------------------- the plan


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class PlanHit(_Loose):
    t: float = Field(ge=0, description="Seconds inside the clip")
    kind: str = Field(default="impact", description="impact | glitch | flash | shake")


class PlanSegment(_Loose):
    clip_id: str
    src_in: float = Field(ge=0)
    src_out: float = Field(gt=0)
    reason: str = Field(default="", max_length=500)
    punch_in: bool = False
    speed: float = Field(default=1.0, ge=0.5, le=3.0)
    move: str = Field(default="none", description="none | zoom_in | zoom_out | pan_left | pan_right | shake")
    transition_in: str = Field(default="cut", description="cut | zoom | whip | flash | glitch | fade")
    hits: list[PlanHit] = Field(default_factory=list, max_length=4)


class PlanRemoved(_Loose):
    clip_id: str
    src_in: float = Field(ge=0)
    src_out: float = Field(gt=0)
    reason: str = Field(default="", max_length=500)


class PlanColor(_Loose):
    preset: str = "natural"
    exposure: float = Field(default=0.0, ge=-100, le=100)
    contrast: float = Field(default=0.0, ge=-100, le=100)
    saturation: float = Field(default=0.0, ge=-100, le=100)
    temperature: float = Field(default=0.0, ge=-100, le=100)
    reason: str = Field(default="", max_length=500)


class PlanCaptions(_Loose):
    enabled: bool = True
    style: str = "word_pop"
    highlight: list[str] = Field(default_factory=list, max_length=40)


class EditPlan(_Loose):
    title: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=2000)
    segments: list[PlanSegment] = Field(min_length=1)
    removed: list[PlanRemoved] = Field(default_factory=list)
    tighten: bool = True
    color: PlanColor = Field(default_factory=PlanColor)
    captions: PlanCaptions = Field(default_factory=PlanCaptions)
    source: str = Field(default="ai", description="ai | rules | user")
    energy: str = Field(default="medium", description="calm | medium | high: pacing of effects and sounds")
    sfx: bool = True
    edits: dict[str, str] = Field(default_factory=dict, description="Caption corrections: original -> new")
    caption_fixes: dict[str, str] = Field(default_factory=dict, description="Misheard words Claude corrected")


# ------------------------------------------------------------------------------------- frames


def sample_frames(
    path: Path, signals: Signals, count: int, *, long_edge: int = 512
) -> list[tuple[float, bytes]]:
    """About ``count`` evenly spaced JPEG frames, skipping black and empty dead zones."""
    import cv2

    from autoedit_pipeline.media import FrameSpec, iter_frames

    info = signals.media
    dur = max(info.duration_s, 0.1)
    count = max(1, min(count, int(dur) + 1))
    fps = min(1.0, count / dur)
    if info.display_width >= info.display_height:
        w = long_edge
    else:
        w = round(long_edge * info.display_width / info.display_height)
    w -= w % 2
    h = round(w * info.display_height / info.display_width / 2) * 2
    skip = [(z.t_in, z.t_out) for z in signals.dead_zones if z.reason in ("black_frames", "static_empty")]
    out: list[tuple[float, bytes]] = []
    for t, frame in iter_frames(path, FrameSpec(fps=fps, width=w, height=max(h, 2))):
        if any(a <= t <= b for a, b in skip):
            continue
        ok, buf = cv2.imencode(".jpg", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 72])
        if ok:
            out.append((round(t, 2), buf.tobytes()))
    return out


def frame_budget(materials: list[ClipMaterial], total: int = FRAME_BUDGET) -> dict[str, int]:
    """Share the frame budget by duration, at least 4 per clip, at most one per second."""
    durs = {m.clip_id: max(m.duration_s, 0.1) for m in materials}
    whole = sum(durs.values())
    return {cid: max(4, min(int(d) + 1, round(total * d / whole))) for cid, d in durs.items()}


# ------------------------------------------------------------------------------------- prompt


def _presets_text() -> str:
    return "\n".join(f"- `{p.key}`: {p.name}. {p.description}" for p in COLOR_PRESETS.values())


def system_prompt() -> str:
    return (
        (PROMPTS / f"{PROMPT_VERSION}.md").read_text(encoding="utf-8").replace("{presets}", _presets_text())
    )


def _fmt_t(t: float) -> str:
    return f"{t:.2f}"


def _clip_text(m: ClipMaterial) -> str:
    s = m.signals
    lines = [f"## Clip {m.clip_id} (order {m.order}, {s.media.duration_s:.2f} s, "
             f"{s.media.display_width}x{s.media.display_height}, audio: {'yes' if s.media.has_audio else 'no'})"]  # fmt: skip
    if s.scene_cuts:
        lines.append("Scene cuts at: " + ", ".join(_fmt_t(t) for t in s.scene_cuts[:60]))
    if s.dead_zones:
        lines.append("Dead zones: " + "; ".join(f"{_fmt_t(z.t_in)}-{_fmt_t(z.t_out)} {z.reason}"
                                                  for z in s.dead_zones[:40]))  # fmt: skip
    if s.quality_flags:
        lines.append("Quality problems: " + "; ".join(f"{_fmt_t(q.t_in)}-{_fmt_t(q.t_out)} {q.reason}"
                                                        for q in s.quality_flags[:40]))  # fmt: skip
    people = [r.t for r in s.per_second if r.faces or r.person_count]
    if people:
        lines.append(f"Person on screen in {len(people)} of {len(s.per_second)} seconds.")
    bright = [r.brightness for r in s.per_second if not r.black]
    if bright:
        lines.append(
            f"Average brightness {sum(bright) / len(bright):.2f} (0 black, 1 white; ~0.45 is normal)."
        )
    tr = m.transcript
    if tr and tr.sentences:
        lines.append(f"Transcript (language {tr.language or '?'}), one line per phrase:")
        for se in tr.sentences:
            lines.append(f"[{_fmt_t(se.t_in)}-{_fmt_t(se.t_out)}] {se.text}")
        fill = [w for w in tr.words if w.filler]
        if fill:
            lines.append(f"Filler words: {len(fill)}. Pauses over 0.7 s: {len(tr.pauses)}.")
    elif s.media.has_audio:
        lines.append("No speech detected." if tr is not None else "Transcript unavailable.")
    return "\n".join(lines)


def user_content(materials: list[ClipMaterial], req: EditRequest) -> list[dict[str, Any]]:
    settings = {
        "aspect": req.aspect, "target_duration_s": req.target_duration_s,
        "color_preset": req.color_preset or "auto",
        "captions": "on" if req.captions else "off" if req.captions is False else "your choice",
        "caption_style": req.caption_style or "your choice", "music_bed": req.music,
        "total_raw_s": round(sum(m.duration_s for m in materials), 2),
    }  # fmt: skip
    blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "# Edit request\n"
            + (req.prompt.strip() or "(no instructions: make a tight, engaging edit)"),
        },
        {"type": "text", "text": "# Settings\n" + json.dumps(settings)},
    ]
    for m in sorted(materials, key=lambda x: x.order):
        blocks.append({"type": "text", "text": _clip_text(m)})
        for t, jpg in m.frames:
            blocks.append({"type": "text", "text": f"{m.clip_id} @ {t:.1f}s"})
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": base64.standard_b64encode(jpg).decode()}})  # fmt: skip
    blocks.append({"type": "text", "text": "Plan the edit now. JSON only."})
    return blocks


def _extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    a, b = text.find("{"), text.rfind("}")
    return text[a : b + 1] if a >= 0 and b > a else text


# --------------------------------------------------------------------------------- validation


def check_plan(plan: EditPlan, materials: list[ClipMaterial], req: EditRequest) -> list[str]:
    problems: list[str] = []
    durs = {m.clip_id: m.duration_s for m in materials}
    by_clip: dict[str, list[tuple[float, float]]] = {}
    for i, s in enumerate(plan.segments):
        if s.clip_id not in durs:
            problems.append(f"segments[{i}]: unknown clip_id '{s.clip_id}' (clips: {', '.join(durs)})")
            continue
        if s.src_out <= s.src_in + 0.3:
            problems.append(f"segments[{i}]: {s.src_in}-{s.src_out} is shorter than 0.3 s")
        if s.src_out > durs[s.clip_id] + 0.25:
            problems.append(f"segments[{i}]: src_out {s.src_out} is past the end of {s.clip_id} "
                            f"({durs[s.clip_id]:.2f} s)")  # fmt: skip
        by_clip.setdefault(s.clip_id, []).append((s.src_in, s.src_out))
    for cid, spans in by_clip.items():
        spans.sort()
        for (_a1, b1), (a2, _b2) in itertools.pairwise(spans):
            if a2 < b1 - 0.15:
                problems.append(f"segments of {cid} overlap around {a2:.2f} s; each moment can be used once")
    total = sum((s.src_out - s.src_in) / s.speed for s in plan.segments)
    raw = sum(durs.values())
    if req.target_duration_s and raw > req.target_duration_s * 1.2:
        if total > req.target_duration_s * 1.35:
            problems.append(
                f"kept segments add up to {total:.1f} s; the target is {req.target_duration_s:.0f} s"
            )
        if total < req.target_duration_s * 0.5:
            problems.append(f"kept segments add up to only {total:.1f} s; the target is "
                            f"{req.target_duration_s:.0f} s")  # fmt: skip
    return problems


def _normalise(plan: EditPlan, req: EditRequest) -> EditPlan:
    color = plan.color
    if req.color_preset and req.color_preset != "auto":
        color = color.model_copy(update={"preset": req.color_preset})
    if color.preset not in COLOR_PRESETS:
        color = color.model_copy(update={"preset": "natural"})
    clamp = {k: max(-60.0, min(60.0, getattr(color, k))) for k in ("exposure", "contrast", "saturation",
                                                                       "temperature")}  # fmt: skip
    color = color.model_copy(update=clamp)
    caps = plan.captions
    if req.captions is not None:
        caps = caps.model_copy(update={"enabled": req.captions})
    if req.caption_style in CAPTION_STYLES:
        caps = caps.model_copy(update={"style": req.caption_style})
    if caps.style not in CAPTION_STYLES:
        caps = caps.model_copy(update={"style": "word_pop"})
    segs = [s.model_copy(update={"speed": 1.0}) if s.speed < 1.0 else s for s in plan.segments]
    return plan.model_copy(update={"color": color, "captions": caps, "segments": segs})


def plan_with_ai(materials: list[ClipMaterial], req: EditRequest, client: Any, usage: Any,
                 *, max_cost_usd: float = 1.0) -> EditPlan:  # fmt: skip
    """Ask Claude for the plan. One retry with the exact problems; then ``PlanError``."""
    system = system_prompt()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content(materials, req)}]
    for attempt in range(2):
        text, raw = client.complete(system, messages, usage)
        if usage.cost_usd > max_cost_usd:
            raise PlanError("cost_ceiling", f"Planning stopped at ${usage.cost_usd:.2f}, over the job limit.")
        problems: list[str]
        plan: EditPlan | None = None
        try:
            plan = EditPlan.model_validate_json(_extract_json(text))
            problems = check_plan(plan, materials, req)
        except ValidationError as exc:
            problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:12]]
        except ValueError as exc:
            problems = [f"reply is not valid JSON: {exc}"]
        if plan is not None and not problems:
            return _normalise(plan, req).model_copy(update={"source": "ai"})
        log.info("edit plan attempt %s rejected: %s", attempt + 1, problems)
        if attempt == 0:
            messages += [
                {"role": "assistant", "content": raw or [{"type": "text", "text": text}]},
                {
                    "role": "user",
                    "content": "Fix these problems and reply with the full JSON again:\n- "
                    + "\n- ".join(problems),
                },
            ]
    raise PlanError(
        "plan_invalid", "Claude's edit plan did not pass the checks twice: " + "; ".join(problems[:3])
    )


# ------------------------------------------------------------------------------- rules plan


_LOOK_WORDS = [
    (("black and white", "black & white", "b&w", "monochrome", "grayscale"), "black_white"),
    (("teal", "orange", "blockbuster", "hollywood"), "teal_orange"),
    (("dark", "moody", "gloomy", "night", "film grain", "gritty"), "moody_film"),
    (("vintage", "retro", "old film", "faded"), "vintage"),
    (("warm", "golden", "cinematic", "sunset"), "warm_cinematic"),
    (("bright", "clean", "airy", "product"), "clean_bright"),
    (("vibrant", "pop", "colorful", "colourful", "punchy"), "vibrant_pop"),
]


_ENERGY_WORDS = {
    "high": (
        "punchy",
        "energetic",
        "hype",
        "fast",
        "viral",
        "tiktok",
        "reel",
        "dynamic",
        "exciting",
        "trendy",
        "edgy",
    ),
    "calm": ("calm", "slow", "cinematic", "documentary", "elegant", "gentle", "soft", "relaxing", "luxury"),
}


def energy_from_prompt(prompt: str) -> str:
    p = prompt.lower()
    for level, words in _ENERGY_WORDS.items():
        if any(w in p for w in words):
            return level
    return "medium"


def auto_style(plan: EditPlan) -> EditPlan:
    """Moves, transitions and punch-ins for a plan that has none, paced by ``plan.energy``."""
    segs = []
    n = len(plan.segments)
    for i, sg in enumerate(plan.segments):
        upd: dict[str, object] = {}
        if plan.energy == "high":
            upd["move"] = ("zoom_in", "zoom_out", "zoom_in", "pan_right")[i % 4]
            upd["transition_in"] = ("zoom", "whip", "zoom", "glitch", "flash")[i % 5] if i else "cut"
            upd["punch_in"] = i % 2 == 1
        elif plan.energy == "medium":
            upd["move"] = "zoom_in" if i % 2 == 0 else "zoom_out"
            upd["transition_in"] = ("zoom" if i % 2 else "whip") if 0 < i < n else "cut"
            upd["punch_in"] = i % 3 == 2
        else:
            upd["move"] = "zoom_in"
            upd["transition_in"] = "fade" if i and i % 3 == 0 else "cut"
        segs.append(sg.model_copy(update=upd))
    return plan.model_copy(update={"segments": segs})


def look_from_prompt(prompt: str) -> str:
    p = prompt.lower()
    for words, key in _LOOK_WORDS:
        if any(w in p for w in words):
            return key
    return "natural"


def plan_rules(materials: list[ClipMaterial], req: EditRequest) -> EditPlan:
    """A plan without Claude: keep speech phrases and visible action, drop dead air, fit the target."""
    from .auto_edl import ClipInput, build_auto_edl

    edl = build_auto_edl([ClipInput(m.clip_id, m.signals) for m in sorted(materials, key=lambda x: x.order)],
                         aspect=req.aspect, target_duration_s=req.target_duration_s)  # fmt: skip
    segs = [PlanSegment(clip_id=s.clip_id, src_in=s.src_in, src_out=s.src_out, reason=s.reason)
            for s in edl.segments]  # fmt: skip
    removed = [PlanRemoved(clip_id=r.clip_id, src_in=r.src_in, src_out=r.src_out, reason=r.reason)
               for r in edl.removed]  # fmt: skip
    speech = any(m.transcript and m.transcript.words for m in materials)
    preset = (
        req.color_preset if req.color_preset and req.color_preset != "auto" else look_from_prompt(req.prompt)
    )
    plan = EditPlan(
        title=req.title, segments=segs, removed=removed, tighten=True, source="rules",
        energy=energy_from_prompt(req.prompt),
        summary="Automatic cut without AI: removed dead air, pauses and filler words.",
        color=PlanColor(preset=preset, reason="From your prompt" if req.color_preset == "auto" else "Your choice"),
        captions=PlanCaptions(enabled=speech if req.captions is None else req.captions,
                              style=req.caption_style or "word_pop"),
    )  # fmt: skip
    return auto_style(_normalise(plan, req))


# ------------------------------------------------------------------------------- plan -> EDL


def _clean(t: str) -> str:
    return re.sub(r"[^\w']+", "", t.lower())


def _snap(a: float, b: float, words: list[Word], dur: float) -> tuple[float, float]:
    """Move the boundaries out of words and give speech a natural lead-in and tail."""
    for w in words:
        if w.t_in < a < w.t_out - 0.02:
            a = w.t_in
        if w.t_in + 0.02 < b < w.t_out:
            b = w.t_out
    inside = [w for w in words if w.t_in >= a - 0.01 and w.t_out <= b + 0.01]
    if inside:
        first, last = inside[0], inside[-1]
        prev_end = max([w.t_out for w in words if w.t_out <= first.t_in] or [0.0])
        next_start = min([w.t_in for w in words if w.t_in >= last.t_out] or [dur])
        if first.t_in - a < 0.25:
            a = max(prev_end, first.t_in - LEAD_PAD_S, 0.0)
        if b - last.t_out < 0.3:
            b = min(next_start, last.t_out + TAIL_PAD_S, dur)
    return max(0.0, a), min(dur, b)


def _tighten(a: float, b: float, words: list[Word]) -> list[tuple[float, float, str]]:
    """Split [a, b] around fillers, stutters and inner pauses. Returns pieces and what was cut between."""
    inside = [w for w in words if w.t_in >= a - 0.01 and w.t_out <= b + 0.01]
    if not inside:
        return [(a, b, "")]
    cuts: list[tuple[float, float, str]] = []
    for i, w in enumerate(inside):
        if w.filler or w.repeat:
            lo = inside[i - 1].t_out + 0.04 if i > 0 else a
            hi = inside[i + 1].t_in - 0.04 if i + 1 < len(inside) else b
            what = f"Filler word '{w.text.strip(',.')}'" if w.filler else f"Stutter '{w.text.strip(',.')}'"
            cuts.append((max(lo, a), min(hi, b), what))
    for prev, nxt in itertools.pairwise(inside):
        if nxt.t_in - prev.t_out > INNER_PAUSE_S and not (prev.filler or nxt.filler):
            cuts.append((prev.t_out + 0.15, nxt.t_in - 0.12, "Pause"))
    cuts.sort()
    pieces: list[tuple[float, float, str]] = []
    cursor, why = a, ""
    for lo, hi, what in cuts:
        if hi - lo < 0.12 or lo < cursor:
            continue
        pieces.append((cursor, lo, why))
        cursor, why = hi, what
    pieces.append((cursor, b, why))
    merged: list[tuple[float, float, str]] = []
    for p in pieces:
        if p[1] - p[0] < MIN_PIECE_S and merged:
            merged[-1] = (merged[-1][0], p[1], merged[-1][2])  # too short to stand alone: keep it joined
        else:
            merged.append(p)
    return [p for p in merged if p[1] - p[0] >= MIN_PIECE_S] or [(a, b, "")]


def _subtract(span: tuple[float, float], used: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = [span]
    for a, b in sorted(used):
        nxt = []
        for x, y in out:
            if b <= x or a >= y:
                nxt.append((x, y))
                continue
            if a > x:
                nxt.append((x, a))
            if b < y:
                nxt.append((b, y))
        out = nxt
    return [(x, y) for x, y in out if y - x > 0.05]


def a_in_seg(w: Word, seg: Segment) -> bool:
    return seg.src_in - 0.02 <= w.t_in <= seg.src_out


def _unused_reason(m: ClipMaterial, words: list[Word], a: float, b: float) -> str:
    said = [w for w in words if w.t_in >= a - 0.05 and w.t_out <= b + 0.05]
    if said and all(w.filler or w.repeat for w in said):
        return "Filler: " + " ".join(w.text.strip(",.") for w in said)[:80]
    if said:
        return 'Not needed for the story: "' + " ".join(w.text for w in said)[:120] + '"'
    for z in m.signals.dead_zones:
        if z.t_in <= a + 0.2 and z.t_out >= b - 0.2:
            return {"black_frames": "Black frames", "static_empty": "Nothing happening"}.get(
                z.reason, "Dead air"
            )
    return "Silence" if m.signals.media.has_audio else "Not needed for the story"


def plan_to_edl(plan: EditPlan, materials: list[ClipMaterial], req: EditRequest) -> EDL:
    mats = {m.clip_id: m for m in materials}
    words_of = {m.clip_id: (m.transcript.words if m.transcript else []) for m in materials}
    pieces: list[tuple[PlanSegment, float, float, str]] = []
    cut_notes: list[RemovedRange] = []
    for s in plan.segments:
        m = mats.get(s.clip_id)
        if m is None:
            continue
        words = words_of[s.clip_id]
        a, b = _snap(s.src_in, min(s.src_out, m.duration_s), words, m.duration_s)
        if b - a < MIN_PIECE_S:
            continue
        parts = _tighten(a, b, words) if plan.tighten and s.speed == 1.0 else [(a, b, "")]
        prev_end = None
        for pa, pb, why in parts:
            if prev_end is not None and why and pa - prev_end > 0.05:
                cut_notes.append(RemovedRange(clip_id=s.clip_id, src_in=round(prev_end, 3),
                                              src_out=round(pa, 3), reason=why))  # fmt: skip
            pieces.append((s, pa, pb, why))
            prev_end = pb
    if not pieces:
        raise PlanError("empty_edit", "The plan kept nothing usable.")

    # Segments, with alternating punch-ins across the jump cuts of a punch_in segment.
    segments: list[Segment] = []
    effects: list[Effect] = []
    sfx: list[Sfx] = []
    cursor = 0.0
    zoom_toggle: dict[int, bool] = {}
    starts: list[float] = []
    for i, (s, a, b, _why) in enumerate(pieces):
        seg = Segment(id=f"s{i + 1}", clip_id=s.clip_id, src_in=round(a, 3), src_out=round(b, 3),
                      speed=round(s.speed, 3), reason=s.reason[:500])  # fmt: skip
        segments.append(seg)
        starts.append(cursor)
        key = id(s)
        end = cursor + seg.output_duration_s
        jump = i > 0 and pieces[i - 1][0] is s  # a jump cut inside one planned segment
        if s.punch_in:
            on = zoom_toggle.get(key, False) if sum(1 for p in pieces if p[0] is s) > 1 else True
            zoom_toggle[key] = not on
            if on:
                effects.append(Effect(t_in=round(cursor, 3), t_out=round(end, 3),
                                      type=EffectType.PUNCH_ZOOM, params={"scale": 1.18},
                                      reason="Punch-in for emphasis"))  # fmt: skip
        if s.move in _MOVES and seg.output_duration_s > 0.8:
            effects.append(Effect(t_in=round(cursor, 3), t_out=round(end, 3), type=EffectType(s.move),
                                  params={"strength": 1.0}, reason="Camera move"))  # fmt: skip
        if i > 0 and not jump and s.transition_in in _TRANSITIONS and s.transition_in != "cut":
            a, b = max(0.0, cursor - 0.28), cursor + 0.28
            effects.append(Effect(t_in=round(a, 3), t_out=round(b, 3), type=EffectType.TRANSITION,
                                  params={"style": s.transition_in}, reason=f"{s.transition_in} transition"))  # fmt: skip
            snd = _TRANSITION_SFX.get(s.transition_in)
            if plan.sfx and snd:
                lead = 0.32 if snd[0] in ("whoosh", "swoosh") else 0.04
                sfx.append(Sfx(t=round(max(0.0, cursor - lead), 3), type=SfxType(snd[0]), volume_db=snd[1],
                               reason=f"Sound for the {s.transition_in} transition"))  # fmt: skip
        for h in s.hits:
            if not (seg.src_in <= h.t < seg.src_out):
                continue
            t = cursor + (h.t - seg.src_in) / seg.speed
            if h.kind in ("glitch", "flash", "shake"):
                effects.append(Effect(t_in=round(t, 3), t_out=round(min(end, t + 0.3), 3), type=EffectType(h.kind),
                                      params={}, reason="Emphasis"))  # fmt: skip
            snd = _HIT_SFX.get(h.kind)
            if plan.sfx and snd:
                sfx.append(Sfx(t=round(max(0.0, t - 0.02), 3), type=SfxType(snd[0]), volume_db=snd[1],
                               reason="Emphasis sound"))  # fmt: skip
        cursor = end

    # Removed: notable cuts from the plan, jump-cut notes, then everything else unused.
    removed: list[RemovedRange] = list(cut_notes)
    for r in plan.removed:
        if r.clip_id in mats and r.src_out > r.src_in:
            removed.append(RemovedRange(clip_id=r.clip_id, src_in=round(r.src_in, 3),
                                        src_out=round(min(r.src_out, mats[r.clip_id].duration_s), 3),
                                        reason=r.reason[:500] or "Cut"))  # fmt: skip
    for m in materials:
        used = [(sg.src_in, sg.src_out) for sg in segments if sg.clip_id == m.clip_id]
        used += [(r.src_in, r.src_out) for r in removed if r.clip_id == m.clip_id]
        for a, b in _subtract((0.0, m.duration_s), used):
            if b - a >= 0.3:
                removed.append(RemovedRange(clip_id=m.clip_id, src_in=round(a, 3), src_out=round(b, 3),
                                            reason=_unused_reason(m, words_of[m.clip_id], a, b)))  # fmt: skip
    removed.sort(key=lambda r: (mats[r.clip_id].order, r.src_in))

    # Captions from the transcript, on the output timeline.
    caps: list[CaptionWord] = []
    if plan.captions.enabled:
        hl = {_clean(h) for h in plan.captions.highlight if _clean(h)}
        fixes = {
            _clean(k): v for k, v in {**plan.caption_fixes, **plan.edits}.items() if _clean(k) and v.strip()
        }
        for seg, t0 in zip(segments, starts, strict=True):
            for w in words_of[seg.clip_id]:
                if w.filler or w.repeat or w.t_in < seg.src_in - 0.02 or w.t_out > seg.src_out + 0.05:
                    continue
                text = fixes.get(_clean(w.text), w.text).strip()[:64]
                if not text:
                    continue
                if caps and not text[0].isalnum() and text[0] not in "\"'(#₦$£€" and a_in_seg(w, seg):
                    # Whisper splits numbers and contractions ("15" ",000"): glue onto the previous word.
                    last = caps[-1]
                    caps[-1] = last.model_copy(
                        update={
                            "text": (last.text + text)[:64],
                            "t_out": round(
                                max(last.t_out, t0 + (min(seg.src_out, w.t_out) - seg.src_in) / seg.speed), 3
                            ),
                        }
                    )
                    continue
                a = t0 + max(0.0, w.t_in - seg.src_in) / seg.speed
                b = t0 + min(seg.src_out - seg.src_in, w.t_out - seg.src_in) / seg.speed
                if caps and a < caps[-1].t_out:
                    a = max(a, caps[-1].t_in + 0.01)
                    caps[-1] = caps[-1].model_copy(update={"t_out": round(a, 3)})
                if b - a < 0.04:
                    b = a + 0.04
                caps.append(CaptionWord(t_in=round(a, 3), t_out=round(b, 3), text=text,
                                        emphasis=_clean(text) in hl))  # fmt: skip
        total = cursor
        caps = [c for c in caps if c.t_out <= total + 1e-6 and c.t_out > c.t_in]

    raw = sum(m.duration_s for m in materials)
    c = plan.color
    language = next(
        (m.transcript.language for m in materials if m.transcript and m.transcript.language), None
    )
    return EDL(
        meta={"title": (plan.title or req.title)[:200], "summary": plan.summary[:2000], "aspect": req.aspect,
              "total_raw_duration_s": round(raw, 3), "target_duration_s": req.target_duration_s,
              "style": plan.source, "language": (language or req.language or "en")[:16]},
        clips=[ClipRef(id=m.clip_id, order=i + 1, duration_s=round(m.duration_s, 3))
               for i, m in enumerate(sorted(materials, key=lambda x: x.order))],
        segments=segments,
        removed=removed,
        effects=effects,
        captions={"enabled": plan.captions.enabled and bool(caps), "style": plan.captions.style,
                  "language": language, "words": caps},
        audio=Audio(music=Music(enabled=req.music, mood="upbeat", asset_id="music" if req.music else None,
                                duck_under_speech=True, volume_db=-16.0),
                    sfx=[x for x in sorted(sfx, key=lambda x: x.t) if x.t < cursor], denoise=True, normalize_lufs=-14.0),
        color=Color(preset=c.preset, exposure=c.exposure, contrast=c.contrast, saturation=c.saturation,
                    temperature=c.temperature),
    )  # fmt: skip
