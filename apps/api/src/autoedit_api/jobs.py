"""Background jobs (run by the RQ worker). Every job is idempotent and safe to retry."""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from autoedit_pipeline import __version__ as pipeline_version
from autoedit_pipeline.analyze import analyze_clip
from autoedit_pipeline.media import MediaError

from .db import AnalysisArtifact, Project, ProjectClip, Upload, UsageEvent, User, session_scope
from .settings import get_settings
from .storage import clip_prefix, get_storage

log = logging.getLogger(__name__)

PROGRESS_WRITE_INTERVAL_S = 0.75


def _retries_left() -> int:
    try:
        from rq import get_current_job

        job = get_current_job()
    except Exception:  # pragma: no cover - outside a worker
        return 0
    return int(job.retries_left or 0) if job is not None else 0


def _reuse_existing(clip: ProjectClip, upload: Upload) -> bool:
    """Same user, same bytes, same pipeline version already analysed? Copy instead of recomputing.

    Scoped to the user so deleting one user's data never affects another's.
    """
    db = session_scope()
    try:
        prior = db.scalars(
            select(AnalysisArtifact)
            .join(ProjectClip, AnalysisArtifact.clip_id == ProjectClip.id)
            .where(
                AnalysisArtifact.user_id == upload.user_id,
                AnalysisArtifact.source_sha256 == upload.sha256,
                AnalysisArtifact.version == pipeline_version,
                AnalysisArtifact.clip_id != clip.id,
                ProjectClip.status == "ready",
            )
        ).all()
        kinds = {a.kind: a for a in prior}
        if not {"proxy", "signals"} <= set(kinds):
            return False
        storage = get_storage()
        prefix = clip_prefix(upload.user_id, clip.project_id, clip.id)
        for kind, filename in (("proxy", "proxy.mp4"), ("signals", "signals.json")):
            src = kinds[kind]
            dst_key = prefix + filename
            if kind == "signals":
                import json

                doc = json.loads(storage.read_bytes(src.storage_key))
                doc["clip_id"] = clip.id
                storage.put_bytes(dst_key, json.dumps(doc, indent=2).encode(), "application/json")
            else:
                storage.copy(src.storage_key, dst_key)
            _record_artifact(db, clip, upload, kind, dst_key, src.size_bytes)
        db.commit()
        return True
    finally:
        db.close()


def _record_artifact(db: Session, clip: ProjectClip, upload: Upload, kind: str, key: str, size: int) -> None:
    existing = db.scalar(
        select(AnalysisArtifact).where(
            AnalysisArtifact.clip_id == clip.id,
            AnalysisArtifact.kind == kind,
            AnalysisArtifact.version == pipeline_version,
        )
    )
    if existing is None:
        existing = AnalysisArtifact(
            clip_id=clip.id,
            project_id=clip.project_id,
            user_id=upload.user_id,
            kind=kind,
            version=pipeline_version,
        )
        db.add(existing)
    existing.source_sha256 = upload.sha256
    existing.storage_key = key
    existing.size_bytes = size


def _update(clip_id: str, **fields: object) -> bool:
    """Write clip state in a short transaction. Returns False if the clip was deleted meanwhile."""
    db = session_scope()
    try:
        clip = db.get(ProjectClip, clip_id)
        if clip is None:
            return False
        for k, v in fields.items():
            setattr(clip, k, v)
        db.commit()
        return True
    finally:
        db.close()


def analyze_clip_job(clip_id: str) -> str:
    """Stage A for one clip: fetch original -> proxy + signals -> store artifacts."""
    settings = get_settings()
    storage = get_storage()
    db = session_scope()
    try:
        clip = db.get(ProjectClip, clip_id)
        if clip is None:
            return "deleted"
        upload = clip.upload
        if clip.status == "ready" and any(a.kind == "signals" for a in clip.artifacts):
            return "already_ready"
        clip.status, clip.stage, clip.progress = "processing", "starting", 0.0
        clip.attempts += 1
        clip.error_code = clip.error = None
        db.commit()
        db.expunge(clip)
        db.expunge(upload)
    finally:
        db.close()

    t0 = time.perf_counter()
    if _reuse_existing(clip, upload):
        _update(clip_id, status="ready", stage=None, progress=1.0)
        log.info("clip %s reused existing analysis", clip_id)
        return "reused"

    settings.work_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"{clip_id}-", dir=settings.work_dir))
    last_write = 0.0

    def progress(stage: str, fraction: float) -> None:
        nonlocal last_write
        now = time.monotonic()
        if now - last_write >= PROGRESS_WRITE_INTERVAL_S or fraction >= 1.0:
            last_write = now
            _update(clip_id, stage=stage, progress=round(fraction * 0.95, 4))

    try:
        assert upload.storage_key and upload.sha256
        local = storage.local_path(upload.storage_key)
        if local is None:
            _update(clip_id, stage="downloading")
            local = storage.get_file(upload.storage_key, workdir / f"original{Path(upload.filename).suffix}")
        result = analyze_clip(
            local,
            workdir / "cache",
            clip_id=clip_id,
            sha256=upload.sha256,
            progress=progress,
        )
        _update(clip_id, stage="saving", progress=0.97)
        prefix = clip_prefix(upload.user_id, clip.project_id, clip_id)
        sizes = {
            "proxy": storage.put_file(prefix + "proxy.mp4", result.proxy_path, "video/mp4"),
            "signals": storage.put_bytes(
                prefix + "signals.json", result.signals.model_dump_json(indent=2).encode(), "application/json"
            ),
        }
        db = session_scope()
        try:
            fresh = db.get(ProjectClip, clip_id)
            if fresh is None:  # deleted while we worked: discard outputs
                storage.delete_prefix(prefix)
                return "deleted"
            _record_artifact(db, fresh, upload, "proxy", prefix + "proxy.mp4", sizes["proxy"])
            _record_artifact(db, fresh, upload, "signals", prefix + "signals.json", sizes["signals"])
            fresh.status, fresh.stage, fresh.progress = "ready", None, 1.0
            db.add(
                UsageEvent(
                    user_id=upload.user_id,
                    project_id=fresh.project_id,
                    clip_id=clip_id,
                    stage="signals",
                    wall_seconds=round(time.perf_counter() - t0, 3),
                    media_seconds=fresh.duration_s,
                    cost_usd=0.0,
                    details={"timings_s": result.signals.analysis.timings_s, "pipeline": pipeline_version},
                )
            )
            db.commit()
        finally:
            db.close()
        return "ready"
    except MediaError as exc:
        # Permanent: the file itself is the problem. Do not retry.
        _update(clip_id, status="failed", stage=None, error_code=exc.code, error=str(exc)[:2000])
        log.warning("clip %s failed permanently: %s", clip_id, exc)
        return "failed"
    except Exception as exc:
        left = _retries_left()
        _update(
            clip_id,
            status="queued" if left else "failed",
            stage="retrying" if left else None,
            error_code="retrying" if left else "internal_error",
            error=f"{type(exc).__name__}: {exc}"[:2000],
        )
        log.exception("clip %s analysis error (retries left: %s)", clip_id, left)
        raise
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def purge_expired() -> dict[str, int]:
    """Retention (PRD 6): delete projects older than ``retention_days`` and abandoned uploads."""
    from .services import delete_project

    settings = get_settings()
    storage = get_storage()
    now = datetime.now(UTC)
    counts = {"projects": 0, "uploads": 0}
    db = session_scope()
    try:
        old = db.scalars(
            select(Project).where(Project.created_at < now - timedelta(days=settings.retention_days))
        )
        for project in old.all():
            owner = db.get(User, project.user_id)
            if owner is not None:
                delete_project(db, storage, owner, project)
                counts["projects"] += 1
        stale = db.scalars(
            select(Upload).where(
                Upload.status != "complete",
                Upload.created_at < now - timedelta(hours=settings.stale_upload_hours),
            )
        )
        for up in stale.all():
            (settings.staging_dir / up.id).unlink(missing_ok=True)
            db.delete(up)
            counts["uploads"] += 1
        db.commit()
    finally:
        db.close()
    return counts


def mark_crashed(clip_id: str, exit_code: int) -> None:
    """A child job died without recording an outcome (killed for memory, timeout, crash)."""
    db = session_scope()
    try:
        clip = db.get(ProjectClip, clip_id)
        if clip is None or clip.status in ("ready", "failed"):
            return
        clip.status, clip.stage = "failed", None
        if exit_code in (-9, 137):
            clip.error_code = "out_of_memory"
            clip.error = (
                "The server ran out of memory on this clip. Try a shorter clip or a larger server plan."
            )
        elif exit_code == -1:
            clip.error_code = "timeout"
            clip.error = "Analysis took too long and was stopped."
        else:
            clip.error_code = "internal_error"
            clip.error = f"Analysis stopped unexpectedly (exit code {exit_code}). Use Retry."
        db.commit()
    finally:
        db.close()
    log.error("clip %s job process exited with %s", clip_id, exit_code)


def unfinished_clip_ids() -> list[str]:
    """Clips left queued or mid-analysis, e.g. by a restart during a deploy."""
    db = session_scope()
    try:
        rows = db.scalars(
            select(ProjectClip)
            .where(ProjectClip.status.in_(("queued", "processing")))
            .order_by(ProjectClip.created_at)
        ).all()
        for clip in rows:
            clip.status, clip.stage, clip.progress = "queued", None, 0.0
        db.commit()
        return [c.id for c in rows]
    finally:
        db.close()
