"""Projects and their clips (PRD 8). Motion mode endpoints arrive with M12-M16."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select

from autoedit_pipeline.plans import billing_enabled

from ..db import Project, session_scope
from ..deps import CurrentUser, DbSession
from ..queue import enqueue_clip_analysis
from ..schemas import ApiError, ClipAdd, ClipOrder, ClipOut, ProjectCreate, ProjectList, ProjectOut
from ..services import (
    add_clips,
    artifact,
    clip_out,
    delete_project,
    get_clip,
    get_or_create_user,
    get_project,
    limits_for,
    project_out,
    project_status,
    project_summary,
    projects_this_month,
    remove_clip,
    reorder_clips,
)
from ..storage import get_storage

router = APIRouter(prefix="/v1/projects", tags=["projects"])


def _dispatch(background: BackgroundTasks, clip_ids: list[str]) -> None:
    for cid in clip_ids:
        background.add_task(enqueue_clip_analysis, cid)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectOut)
def create_project(
    body: ProjectCreate, db: DbSession, user: CurrentUser, background: BackgroundTasks
) -> ProjectOut:
    if body.mode == "motion":
        raise ApiError(
            409,
            "mode_not_available",
            "Make motion graphics is coming soon. Use Edit video for now.",
            available_modes=["edit"],
        )
    limits = limits_for(user)
    if (
        limits.projects_per_month is not None
        and projects_this_month(db, user, body.mode) >= limits.projects_per_month
    ):
        raise ApiError(
            402,
            "project_quota_exceeded",
            f"The {limits.name} plan includes {limits.projects_per_month} projects per month.",
            projects_per_month=limits.projects_per_month,
            upgrade_available=billing_enabled(),
        )
    project = Project(
        user_id=user.id,
        mode=body.mode,
        title=body.title,
        prompt=body.prompt,
        options=body.options.model_dump(),
    )
    db.add(project)
    db.flush()
    new = add_clips(db, user, project, body.upload_ids) if body.upload_ids else []
    db.commit()
    _dispatch(background, [c.id for c in new])
    db.refresh(project)
    return project_out(project, user)


@router.get("", response_model=ProjectList)
def list_projects(
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> ProjectList:
    q = (
        select(Project)
        .where(Project.user_id == user.id)
        .order_by(Project.created_at.desc(), Project.id.desc())
    )
    if cursor:
        anchor = db.get(Project, cursor)
        if anchor is not None and anchor.user_id == user.id:
            q = q.where(Project.created_at <= anchor.created_at, Project.id != anchor.id)
    rows = db.scalars(q.limit(limit + 1)).all()
    items = [project_summary(p) for p in rows[:limit]]
    return ProjectList(items=items, next_cursor=rows[limit - 1].id if len(rows) > limit else None)


@router.get("/{project_id}", response_model=ProjectOut)
def read_project(project_id: str, db: DbSession, user: CurrentUser) -> ProjectOut:
    return project_out(get_project(db, user, project_id), user)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project(project_id: str, db: DbSession, user: CurrentUser) -> Response:
    project = get_project(db, user, project_id, lock=True)
    delete_project(db, get_storage(), user, project)
    db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------ clips


@router.post("/{project_id}/clips", status_code=status.HTTP_201_CREATED, response_model=ClipOut)
def add_clip(
    project_id: str, body: ClipAdd, db: DbSession, user: CurrentUser, background: BackgroundTasks
) -> ClipOut:
    project = get_project(db, user, project_id, lock=True)
    if project.mode != "edit":
        raise ApiError(409, "wrong_mode", "Clips belong to Edit video projects")
    (clip,) = add_clips(db, user, project, [body.upload_id], body.position)
    db.commit()
    _dispatch(background, [clip.id])
    db.refresh(clip)
    return clip_out(project, clip)


@router.delete("/{project_id}/clips/{clip_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_clip(project_id: str, clip_id: str, db: DbSession, user: CurrentUser) -> Response:
    project = get_project(db, user, project_id, lock=True)
    remove_clip(db, get_storage(), user, project, get_clip(project, clip_id))
    db.commit()
    return Response(status_code=204)


@router.put("/{project_id}/clips/order", response_model=ProjectOut)
def order_clips(project_id: str, body: ClipOrder, db: DbSession, user: CurrentUser) -> ProjectOut:
    project = get_project(db, user, project_id, lock=True)
    reorder_clips(project, body.clip_ids)
    db.commit()
    db.refresh(project)
    return project_out(project, user)


@router.post("/{project_id}/clips/{clip_id}/retry", response_model=ClipOut)
def retry_clip(
    project_id: str, clip_id: str, db: DbSession, user: CurrentUser, background: BackgroundTasks
) -> ClipOut:
    project = get_project(db, user, project_id)
    clip = get_clip(project, clip_id)
    if clip.status != "failed":
        raise ApiError(409, "not_failed", "Only failed clips can be retried")
    clip.status, clip.stage, clip.progress, clip.error_code, clip.error = "queued", None, 0.0, None, None
    db.commit()
    _dispatch(background, [clip.id])
    return clip_out(project, clip)


@router.get("/{project_id}/clips/{clip_id}/signals")
def clip_signals(project_id: str, clip_id: str, db: DbSession, user: CurrentUser) -> Response:
    clip = get_clip(get_project(db, user, project_id), clip_id)
    art = artifact(clip, "signals")
    if art is None:
        raise ApiError(409, "not_ready", "Signals are not ready yet", status=clip.status)
    return Response(content=get_storage().read_bytes(art.storage_key), media_type="application/json")


@router.get("/{project_id}/clips/{clip_id}/proxy", response_model=None)
def clip_proxy(project_id: str, clip_id: str, db: DbSession, user: CurrentUser) -> Response:
    """Low-res preview video. Local storage streams with HTTP range support; S3 redirects to a signed URL."""
    clip = get_clip(get_project(db, user, project_id), clip_id)
    art = artifact(clip, "proxy")
    if art is None:
        raise ApiError(409, "not_ready", "Proxy is not ready yet", status=clip.status)
    storage = get_storage()
    local = storage.local_path(art.storage_key)
    if local is not None:
        return FileResponse(local, media_type="video/mp4")
    url = storage.presigned_url(art.storage_key, 3600)
    if url is None:  # pragma: no cover
        raise ApiError(500, "storage_error", "Proxy unavailable")
    return RedirectResponse(url, status_code=307)


# ----------------------------------------------------------------------------------------- events


@router.get("/{project_id}/events")
async def project_events(project_id: str, request: Request, user: CurrentUser) -> StreamingResponse:
    """Server-sent events: a ``project`` event on every change, ending once analysis settles."""
    user_id = user.id

    def snapshot() -> tuple[str, bool]:
        db = session_scope()
        try:
            u = get_or_create_user(db, user_id)
            project = get_project(db, u, project_id)
            out = project_out(project, u)
            settled = project_status(project)[0] in {"ready", "failed", "partial", "empty"}
            return out.model_dump_json(), settled
        finally:
            db.close()

    await run_in_threadpool(snapshot)  # 404 before the stream starts

    async def stream() -> AsyncIterator[str]:
        last = ""
        idle = 0.0
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            try:
                data, settled = await run_in_threadpool(snapshot)
            except ApiError as exc:
                yield f"event: error\ndata: {json.dumps({'code': exc.code, 'message': exc.message})}\n\n"
                return
            if data != last:
                last, idle = data, 0.0
                yield f"event: project\ndata: {data}\n\n"
                if settled:
                    yield "event: settled\ndata: {}\n\n"
                    return
            else:
                idle += 1.0
                if idle >= 15:
                    idle = 0.0
                    yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
