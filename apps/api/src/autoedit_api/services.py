"""Business rules shared by routes and jobs: users, plan limits, clips, ordering, deletion."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from autoedit_pipeline.plans import PlanLimits, billing_enabled, effective_plan, fmt_duration, plan_limits
from autoedit_schemas.media import MediaInfo

from .db import AnalysisArtifact, Asset, Project, ProjectClip, Render, Upload, User
from .schemas import (
    ApiError,
    AssetOut,
    ClipOut,
    LengthMeter,
    LimitsOut,
    MotionOut,
    ProjectOut,
    ProjectSummary,
    RenderOut,
)
from .settings import get_settings
from .storage import Storage, clip_prefix, project_prefix

log = logging.getLogger(__name__)

# ------------------------------------------------------------------------------------------ users


def get_or_create_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, plan=get_settings().default_plan)
        db.add(user)
        db.commit()
    return user


def limits_for(user: User) -> PlanLimits:
    return plan_limits(effective_plan(user.plan))


def limits_out(limits: PlanLimits) -> LimitsOut:
    return LimitsOut(
        plan=limits.name,
        max_total_raw_s=limits.max_total_raw_s,
        max_clips=limits.max_clips,
        projects_per_month=limits.projects_per_month,
        watermark=limits.watermark,
    )


def projects_this_month(db: Session, user: User, mode: str | None = None) -> int:
    now = datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    q = select(func.count(Project.id)).where(Project.user_id == user.id, Project.created_at >= start)
    if mode:
        q = q.where(Project.mode == mode)
    return int(db.scalar(q) or 0)


# ---------------------------------------------------------------------------------------- lookup


def get_project(db: Session, user: User, project_id: str, *, lock: bool = False) -> Project:
    q = select(Project).where(Project.id == project_id, Project.user_id == user.id)
    if lock:
        q = q.with_for_update()
    project = db.scalar(q)
    if project is None:
        raise ApiError(404, "project_not_found", "Project not found")
    return project


def get_upload(db: Session, user: User, upload_id: str) -> Upload:
    upload = db.scalar(select(Upload).where(Upload.id == upload_id, Upload.user_id == user.id))
    if upload is None:
        raise ApiError(404, "upload_not_found", f"Upload {upload_id} not found")
    return upload


def get_clip(project: Project, clip_id: str) -> ProjectClip:
    for clip in project.clips:
        if clip.id == clip_id:
            return clip
    raise ApiError(404, "clip_not_found", "Clip not found in this project")


# ----------------------------------------------------------------------------------------- clips


def total_raw_s(project: Project) -> float:
    return sum(c.duration_s for c in project.clips)


def add_clips(
    db: Session, user: User, project: Project, upload_ids: list[str], position: int | None = None
) -> list[ProjectClip]:
    """Attach uploads as clips. All-or-nothing: if any would break a limit, nothing is added.

    The project row is locked (``SELECT ... FOR UPDATE`` on Postgres) so two concurrent requests
    cannot both slip under the total-length limit.
    """
    limits = limits_for(user)
    uploads = [get_upload(db, user, uid) for uid in upload_ids]
    for up in uploads:
        if up.status != "complete" or not up.media:
            raise ApiError(
                409,
                "upload_not_complete",
                f"Upload {up.id} is not complete",
                upload_id=up.id,
                status=up.status,
            )

    current = total_raw_s(project)
    adding = sum(float(up.media["duration_s"]) for up in uploads if up.media)
    count = len(project.clips) + len(uploads)
    if count > limits.max_clips:
        raise ApiError(
            409,
            "clip_limit_exceeded",
            f"A project can hold at most {limits.max_clips} clips.",
            max_clips=limits.max_clips,
            clip_count=len(project.clips),
        )
    if current + adding > limits.max_total_raw_s + 1e-6:
        raise ApiError(
            402,
            "total_length_limit_exceeded",
            (
                f"Adding {fmt_duration(adding)} would bring this project to {fmt_duration(current + adding)}, "
                f"over the {limits.name} plan limit of {fmt_duration(limits.max_total_raw_s)} total raw length. "
                + ("Remove a clip or upgrade." if billing_enabled() else "Remove a clip to make room.")
            ),
            current_total_s=round(current, 3),
            adding_s=round(adding, 3),
            limit_s=limits.max_total_raw_s,
            plan=limits.name,
            upgrade_available=billing_enabled() and limits.name == "free",
        )

    existing = sorted(project.clips, key=lambda c: c.position)
    insert_at = len(existing) if position is None else min(max(position - 1, 0), len(existing))
    new_clips = [
        ProjectClip(
            project_id=project.id, upload_id=up.id, duration_s=float(up.media["duration_s"]), position=0
        )
        for up in uploads
        if up.media
    ]
    ordered = existing[:insert_at] + new_clips + existing[insert_at:]
    for clip in new_clips:
        db.add(clip)
        project.clips.append(clip)
    for i, clip in enumerate(ordered, start=1):
        clip.position = i
    project.updated_at = datetime.now(UTC)
    db.flush()
    return new_clips


def reorder_clips(project: Project, clip_ids: list[str]) -> None:
    current = {c.id: c for c in project.clips}
    if sorted(clip_ids) != sorted(current):
        raise ApiError(
            422,
            "invalid_order",
            "clip_ids must list every clip in the project exactly once",
            expected=sorted(current),
        )
    for i, cid in enumerate(clip_ids, start=1):
        current[cid].position = i
    project.updated_at = datetime.now(UTC)


def remove_clip(db: Session, storage: Storage, user: User, project: Project, clip: ProjectClip) -> None:
    storage.delete_prefix(clip_prefix(user.id, project.id, clip.id))
    project.clips.remove(clip)  # delete-orphan cascade removes the row and its artifacts
    db.flush()
    for i, c in enumerate(sorted(project.clips, key=lambda c: c.position), start=1):
        c.position = i
    project.updated_at = datetime.now(UTC)


def delete_project(db: Session, storage: Storage, user: User, project: Project) -> dict[str, int]:
    """Delete a project and everything derived from it. Source uploads used by no other project go too."""
    upload_ids = {c.upload_id for c in project.clips}
    removed_objects = storage.delete_prefix(project_prefix(user.id, project.id))
    for asset in db.scalars(select(Asset).where(Asset.project_id == project.id)).all():
        removed_objects += storage.delete_prefix(asset.storage_key)
    db.delete(project)
    db.flush()
    still_used = set(
        db.scalars(select(ProjectClip.upload_id).where(ProjectClip.upload_id.in_(upload_ids))).all()
    )
    removed_uploads = 0
    for uid in upload_ids - still_used:
        up = db.get(Upload, uid)
        if up is not None:
            if up.storage_key:
                removed_objects += storage.delete_prefix(up.storage_key)
            db.delete(up)
            removed_uploads += 1
    return {"objects": removed_objects, "uploads": removed_uploads}


# ------------------------------------------------------------------------------------- rendering


def project_status(project: Project) -> tuple[str, str | None, float]:
    """One status for the sidebar and progress stream, covering analysis, storyboard and rendering."""
    latest = project.renders[0] if project.renders else None
    if latest is not None and latest.status in ("queued", "processing"):
        return "rendering", latest.stage or "queued", round(latest.progress, 4)
    if project.mode == "motion":
        mp = project.motion
        if mp is None:
            return "empty", None, 0.0
        if mp.status in ("queued", "planning"):
            return "planning", mp.stage or "queued", round(mp.progress, 4)
        if mp.pending:
            kind = mp.pending.get("kind")
            label = {"revise": "Applying your changes", "photos": "Finding more photos"}.get(
                str(kind), "Rewriting a scene"
            )
            return "planning", mp.stage or label, 0.5
        if mp.status == "failed":
            return "failed", None, 1.0
        if latest is not None and latest.status == "ready":
            return "done", None, 1.0
        return "review", None, 1.0
    clips = project.clips
    if not clips:
        return "empty", None, 0.0
    states = [c.status for c in clips]
    progress = sum(c.progress if c.status != "failed" else 1.0 for c in clips) / len(clips)
    if any(s in ("queued", "processing") for s in states):
        stages = [c.stage for c in clips if c.status == "processing" and c.stage]
        return "analyzing", (stages[0] if stages else "queued"), round(progress, 4)
    if all(s == "failed" for s in states):
        return "failed", None, 1.0
    if latest is not None and latest.status == "ready":
        return "done", None, 1.0
    if all(s == "ready" for s in states):
        return "ready", None, 1.0
    return "partial", None, 1.0


SETTLED = {"empty", "ready", "partial", "failed", "review", "done"}


def asset_out(a: Asset) -> AssetOut:
    return AssetOut(id=a.id, kind=a.kind, filename=a.filename, width=a.width, height=a.height, source=a.source,
                    url=f"/v1/assets/{a.id}/file" + ("?poster=1" if a.kind == "video" else ""),
                    duration_s=a.duration_s)  # fmt: skip


def render_out(r: Render) -> RenderOut:
    ready = r.status == "ready" and r.storage_key
    return RenderOut(
        id=r.id, kind=r.kind, status=r.status,
        stage=r.stage, progress=round(r.progress, 4), error_code=r.error_code, error=r.error,
        settings=r.settings or {}, report=r.report, duration_s=r.duration_s, width=r.width, height=r.height,
        video_url=f"/v1/renders/{r.id}/video" if ready else None,
        download_url=f"/v1/renders/{r.id}/video?download=1" if ready else None,
        poster_url=f"/v1/renders/{r.id}/poster" if ready and r.poster_key else None,
        created_at=r.created_at,
    )  # fmt: skip


def motion_out(db: Session, project: Project) -> MotionOut | None:
    mp = project.motion
    if mp is None:
        return None
    assets = db.scalars(select(Asset).where(Asset.project_id == project.id).order_by(Asset.created_at)).all()
    return MotionOut(
        status=mp.status,
        stage=mp.stage, progress=round(mp.progress, 4), error_code=mp.error_code, error=mp.error,
        brand_kit=mp.brand_kit, composition=mp.composition, version=mp.version,
        pending_scene=(mp.pending or {}).get("scene_id"), cost_usd=mp.cost_usd or 0.0,
        pending_kind=(mp.pending or {}).get("kind") or ("scene" if mp.pending else None),
        revisions=list((mp.brand_kit or {}).get("revisions", [])),
        assets=[asset_out(a) for a in assets],
    )  # fmt: skip


def artifact(clip: ProjectClip, kind: str) -> AnalysisArtifact | None:
    matches = [a for a in clip.artifacts if a.kind == kind]
    return max(matches, key=lambda a: a.created_at) if matches else None


def clip_out(project: Project, clip: ProjectClip) -> ClipOut:
    media = MediaInfo.model_validate(clip.upload.media) if clip.upload.media else None
    base = f"/v1/projects/{project.id}/clips/{clip.id}"
    return ClipOut(
        id=clip.id,
        upload_id=clip.upload_id,
        filename=clip.upload.filename,
        position=clip.position,
        duration_s=clip.duration_s,
        media=media,
        status=clip.status,
        stage=clip.stage,
        progress=round(clip.progress, 4),
        error_code=clip.error_code,
        error=clip.error,
        signals_url=f"{base}/signals" if artifact(clip, "signals") else None,
        proxy_url=f"{base}/proxy" if artifact(clip, "proxy") else None,
    )


def meter(project: Project, limits: PlanLimits) -> LengthMeter:
    total = total_raw_s(project)
    return LengthMeter(
        total_raw_s=round(total, 3),
        limit_s=limits.max_total_raw_s,
        remaining_s=round(max(0.0, limits.max_total_raw_s - total), 3),
        fraction=round(min(1.0, total / limits.max_total_raw_s), 4) if limits.max_total_raw_s else 1.0,
        clip_count=len(project.clips),
        max_clips=limits.max_clips,
    )


def project_out(project: Project, user: User, db: Session | None = None) -> ProjectOut:
    status, stage, progress = project_status(project)
    return ProjectOut(
        id=project.id,
        mode=project.mode,
        title=project.title,
        prompt=project.prompt,
        options=project.options,
        status=status,
        stage=stage,
        progress=progress,
        meter=meter(project, limits_for(user)),
        clips=[clip_out(project, c) for c in sorted(project.clips, key=lambda c: c.position)],
        motion=motion_out(db, project) if db is not None and project.mode == "motion" else None,
        renders=[render_out(r) for r in project.renders[:10]],
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def project_summary(project: Project) -> ProjectSummary:
    status, _, _ = project_status(project)
    return ProjectSummary(
        id=project.id,
        mode=project.mode,
        title=project.title,
        status=status,
        clip_count=len(project.clips),
        total_raw_s=round(total_raw_s(project), 3),
        created_at=project.created_at,
    )
