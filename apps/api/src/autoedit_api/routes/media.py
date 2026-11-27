"""Assets (images), motion storyboards, renders, saved templates and the template catalog."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, File, Form, Query, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import select

from autoedit_motion.storyboard import ai_available
from autoedit_motion.styles import STYLES
from autoedit_motion.voice import VOICES
from autoedit_render.presets import COLOR_PRESETS
from autoedit_schemas import Composition, SchemaValidationError, parse_model

from ..db import Asset, MotionPlan, Render, Template, new_id
from ..deps import CurrentUser, DbSession
from ..jobs_media import store_uploaded_image
from ..queue import enqueue
from ..schemas import (
    ApiError,
    AssetOut,
    BrandKitUpdate,
    RenderCreate,
    RenderOut,
    SceneRegenerate,
    TemplateCreate,
    TemplateOut,
)
from ..services import asset_out, get_project, render_out
from ..settings import get_settings
from ..storage import Storage, get_storage

router = APIRouter(tags=["media"])

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".avif", ".gif", ".bmp", ".tif", ".tiff"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024


def _serve(storage: Storage, key: str, media_type: str, download_name: str | None = None) -> Response:
    local = storage.local_path(key)
    headers = {"Content-Disposition": f'attachment; filename="{download_name}"'} if download_name else None
    if local is not None:
        return FileResponse(local, media_type=media_type, headers=headers)
    url = storage.presigned_url(key, 3600)
    if url is None:  # pragma: no cover
        raise ApiError(500, "storage_error", "File unavailable")
    return RedirectResponse(url, status_code=307)


# --------------------------------------------------------------------------------------- catalog


@router.get("/v1/catalog")
def catalog() -> dict[str, object]:
    """Everything the composer needs to draw its template pickers."""
    return {
        "ai_enabled": ai_available(),
        "color_presets": [
            {
                "key": "auto",
                "name": "Auto",
                "css": "none",
                "description": "The AI picks the look from your prompt and the footage.",
            },
            *(
                {"key": p.key, "name": p.name, "description": p.description, "css": p.css}
                for p in COLOR_PRESETS.values()
            ),
        ],
        "caption_styles": [
            {"key": "word_pop", "name": "Pop", "description": "Big words, the spoken word pops in color"},
            {"key": "karaoke", "name": "Bold", "description": "Heavy uppercase, highlighted word"},
            {"key": "bold_box", "name": "Boxed", "description": "White on a dark box, easy to read"},
            {"key": "clean", "name": "Clean", "description": "Simple white captions"},
        ],
        "motion_styles": [
            {
                "key": s.key,
                "name": s.name,
                "description": s.description,
                "colors": list(s.preview_colors),
                "display_font": s.display_font,
                "body_font": s.body_font,
                "uppercase": s.uppercase_headlines,
            }
            for s in STYLES.values()
        ],
        "aspects": ["9:16", "1:1", "4:5", "16:9"],
        "voices": [{"key": k, "name": v[2]} for k, v in VOICES.items()],
    }


# ---------------------------------------------------------------------------------------- assets


@router.post("/v1/assets", status_code=status.HTTP_201_CREATED, response_model=AssetOut)
async def upload_asset(
    db: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()] = "photo",
) -> AssetOut:
    if kind == "music":
        return await _upload_music(db, user, file)
    if kind == "video":
        return await _upload_footage(db, user, file)
    if kind not in ("logo", "photo", "screenshot"):
        raise ApiError(422, "bad_kind", "kind must be logo, photo, screenshot or music")
    name = Path(file.filename or "image").name
    if Path(name).suffix.lower() not in IMAGE_TYPES:
        raise ApiError(415, "unsupported_image", "Images must be JPG, PNG, WebP, HEIC, AVIF, GIF or TIFF")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ApiError(413, "image_too_large", "Images must be 15 MB or smaller")
    try:
        asset = await run_in_threadpool(store_uploaded_image, get_storage(), user.id, data, name, kind)
    except OSError as exc:
        raise ApiError(422, "unreadable_image", "That file could not be read as an image") from exc
    db.add(asset)
    db.commit()
    return asset_out(asset)


AUDIO_TYPES = {".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
               ".ogg": "audio/ogg"}  # fmt: skip
MAX_AUDIO_BYTES = 25 * 1024 * 1024


async def _upload_music(db: DbSession, user: CurrentUser, file: UploadFile) -> AssetOut:
    """A music bed for edits and motion videos. The user confirms they may use it (their upload)."""
    name = Path(file.filename or "music").name
    ext = Path(name).suffix.lower()
    if ext not in AUDIO_TYPES:
        raise ApiError(415, "unsupported_audio", "Music must be MP3, M4A, AAC, WAV or OGG")
    data = await file.read(MAX_AUDIO_BYTES + 1)
    if len(data) > MAX_AUDIO_BYTES:
        raise ApiError(413, "audio_too_large", "Music files must be 25 MB or smaller")

    def store() -> Asset:
        import json
        import subprocess
        import tempfile

        from autoedit_pipeline.media import FFPROBE

        with tempfile.NamedTemporaryFile(suffix=ext) as tmp:
            tmp.write(data)
            tmp.flush()
            proc = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a:0", "-show_entries",
                                   "stream=codec_type:format=duration", "-of", "json", tmp.name],
                                  capture_output=True, timeout=30, check=False)  # fmt: skip
        try:
            meta = json.loads(proc.stdout or b"{}")
            duration = float(meta.get("format", {}).get("duration") or 0)
        except ValueError:
            meta, duration = {}, 0.0
        if proc.returncode != 0 or not meta.get("streams") or duration <= 0:
            raise ApiError(422, "unreadable_audio", "That file could not be read as audio")
        aid = new_id("ast")
        key = f"users/{user.id}/assets/{aid}{ext}"
        get_storage().put_bytes(key, data, AUDIO_TYPES[ext])
        return Asset(id=aid, user_id=user.id, kind="music", filename=name[:255], content_type=AUDIO_TYPES[ext],
                     storage_key=key, size_bytes=len(data), duration_s=round(duration, 2),
                     source=f"file:{name[:200]}")  # fmt: skip

    asset = await run_in_threadpool(store)
    db.add(asset)
    db.commit()
    return asset_out(asset)


VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4", ".webm": "video/webm"}
MAX_FOOTAGE_BYTES = 60 * 1024 * 1024


async def _upload_footage(db: DbSession, user: CurrentUser, file: UploadFile) -> AssetOut:
    """A short clip of the business (shop, product in use, team) for footage scenes in motion videos."""
    name = Path(file.filename or "clip.mp4").name
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_TYPES:
        raise ApiError(415, "unsupported_video", "Clips must be MP4, MOV, M4V or WebM")
    data = await file.read(MAX_FOOTAGE_BYTES + 1)
    if len(data) > MAX_FOOTAGE_BYTES:
        raise ApiError(413, "video_too_large", "Clips for motion videos must be 60 MB or smaller")

    def store() -> Asset:
        import subprocess
        import tempfile

        from autoedit_pipeline.media import FFMPEG, MediaError, probe

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / f"clip{ext}"
            src.write_bytes(data)
            try:
                info = probe(src)
            except MediaError as exc:
                raise ApiError(422, "unreadable_video", "That file could not be read as a video") from exc
            if info.duration_s > 120:
                raise ApiError(422, "video_too_long", "Clips for motion videos must be 2 minutes or shorter")
            poster = Path(td) / "poster.jpg"
            subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-ss", f"{min(1.0, info.duration_s / 3):.2f}",
                            "-i", str(src), "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "4", str(poster)],
                           capture_output=True, timeout=60, check=False)  # fmt: skip
            if not poster.is_file():
                raise ApiError(422, "unreadable_video", "That file could not be read as a video")
            aid = new_id("ast")
            key = f"users/{user.id}/assets/{aid}{ext}"
            storage = get_storage()
            storage.put_bytes(key, data, VIDEO_TYPES[ext])
            storage.put_file(key + ".poster.jpg", poster, "image/jpeg")
        return Asset(id=aid, user_id=user.id, kind="video", filename=name[:255], content_type=VIDEO_TYPES[ext],
                     storage_key=key, size_bytes=len(data), width=info.display_width, height=info.display_height,
                     duration_s=round(info.duration_s, 2), source=f"file:{name[:200]}")  # fmt: skip

    asset = await run_in_threadpool(store)
    db.add(asset)
    db.commit()
    return asset_out(asset)


@router.get("/v1/assets/{asset_id}/file", response_model=None)
def asset_file(asset_id: str, db: DbSession, user: CurrentUser, poster: bool = False) -> Response:
    a = db.get(Asset, asset_id)
    if a is None or a.user_id != user.id:
        raise ApiError(404, "asset_not_found", "Image not found")
    if a.kind == "video" and poster:
        return _serve(get_storage(), a.storage_key + ".poster.jpg", "image/jpeg")
    return _serve(get_storage(), a.storage_key, a.content_type or "image/png")


# ---------------------------------------------------------------------------------- storyboard


def _plan(db: DbSession, user: CurrentUser, project_id: str) -> MotionPlan:
    project = get_project(db, user, project_id)
    if project.mode != "motion" or project.motion is None:
        raise ApiError(409, "wrong_mode", "This is not a motion graphics project")
    return project.motion


@router.put("/v1/projects/{project_id}/composition")
def update_composition(
    project_id: str, body: dict[str, object], db: DbSession, user: CurrentUser
) -> dict[str, object]:
    """Save the user's storyboard edits (text, timing, colors, order). Validated like Claude's output."""
    mp = _plan(db, user, project_id)
    try:
        comp = parse_model(Composition, body)
    except SchemaValidationError as exc:
        raise ApiError(
            422, "invalid_composition", "The storyboard is not valid", feedback=exc.feedback
        ) from exc
    known = {a.id for a in db.scalars(select(Asset).where(Asset.project_id == project_id)).all()}
    missing = {a for sc in comp.scenes for a in sc.content.assets if a not in known}
    missing |= {
        sc.clip_id
        for sc in comp.scenes
        if sc.template == "footage" and sc.clip_id and sc.clip_id not in known
    }
    if comp.brand.logo_asset and comp.brand.logo_asset not in known:
        missing.add(comp.brand.logo_asset)
    if missing:
        raise ApiError(422, "unknown_assets", "The storyboard refers to images that are not in this project",
                       assets=sorted(missing))  # fmt: skip
    mp.composition = comp.model_dump(by_alias=True, mode="json")
    mp.version += 1
    db.commit()
    return {"version": mp.version, "composition": mp.composition}


@router.put("/v1/projects/{project_id}/brand-kit")
def update_brand_kit(
    project_id: str, body: BrandKitUpdate, db: DbSession, user: CurrentUser
) -> dict[str, object]:
    mp = _plan(db, user, project_id)
    kit = dict(mp.brand_kit or {})
    comp = dict(mp.composition or {})
    brand = dict(comp.get("brand") or {})
    if body.name is not None:
        kit["name"] = brand["name"] = body.name
    if body.colors is not None:
        colors = {
            **(kit.get("colors") or {}),
            **{
                k: v.upper()
                for k, v in body.colors.items()
                if k in ("primary", "secondary", "accent", "background", "text")
            },
        }
        kit["colors"] = brand["colors"] = colors  # fmt: skip
    if body.logo_asset is not None:
        if body.logo_asset and db.get(Asset, body.logo_asset) is None:
            raise ApiError(404, "asset_not_found", "Logo image not found")
        kit["logo_asset"] = brand["logo_asset"] = body.logo_asset or None
    if comp:
        comp["brand"] = brand
        try:
            mp.composition = parse_model(Composition, comp).model_dump(by_alias=True, mode="json")
        except SchemaValidationError as exc:
            raise ApiError(
                422, "invalid_brand", "Those brand settings are not valid", feedback=exc.feedback
            ) from exc
        mp.version += 1
    mp.brand_kit = kit
    db.commit()
    return {"brand_kit": kit, "version": mp.version}


@router.post("/v1/projects/{project_id}/scenes/{scene_id}/regenerate", status_code=202)
def regenerate(project_id: str, scene_id: str, body: SceneRegenerate, db: DbSession, user: CurrentUser,
               background: BackgroundTasks) -> dict[str, str]:  # fmt: skip
    mp = _plan(db, user, project_id)
    if mp.status != "ready" or not mp.composition:
        raise ApiError(409, "not_ready", "The storyboard is not ready yet")
    if mp.pending:
        raise ApiError(409, "busy", "Another scene is being rewritten")
    if not ai_available():
        raise ApiError(409, "ai_unavailable", "Scene rewriting needs ANTHROPIC_API_KEY on the server")
    if not any(sc.get("id") == scene_id for sc in mp.composition.get("scenes", [])):
        raise ApiError(404, "scene_not_found", "Scene not found")
    mp.pending = {"scene_id": scene_id, "note": body.note, "at": datetime.now(UTC).isoformat()}
    mp.error_code = mp.error = None
    db.commit()
    background.add_task(enqueue, "scene_regen", project_id)
    return {"status": "queued"}


@router.post("/v1/projects/{project_id}/plan/retry", status_code=202)
def retry_plan(
    project_id: str, db: DbSession, user: CurrentUser, background: BackgroundTasks
) -> dict[str, str]:
    mp = _plan(db, user, project_id)
    if mp.status in ("queued", "planning"):
        raise ApiError(409, "busy", "The storyboard is already being prepared")
    mp.status, mp.stage, mp.progress, mp.error_code, mp.error = "queued", None, 0.0, None, None
    db.commit()
    background.add_task(enqueue, "motion_plan", project_id)
    return {"status": "queued"}


# --------------------------------------------------------------------------------------- renders


@router.post(
    "/v1/projects/{project_id}/renders", status_code=status.HTTP_201_CREATED, response_model=RenderOut
)
def create_render(project_id: str, body: RenderCreate, db: DbSession, user: CurrentUser,
                  background: BackgroundTasks) -> RenderOut:  # fmt: skip
    project = get_project(db, user, project_id, lock=True)
    if any(r.status in ("queued", "processing") for r in project.renders):
        raise ApiError(409, "render_in_progress", "A video is already being made for this project")
    opts = project.options or {}
    if project.mode == "edit":
        if not project.clips or any(c.status in ("queued", "processing") for c in project.clips):
            raise ApiError(409, "not_ready", "Wait for every clip to finish analyzing")
        if not any(c.status == "ready" for c in project.clips):
            raise ApiError(409, "not_ready", "No clip could be analyzed")
        if body.color_preset and body.color_preset != "auto" and body.color_preset not in COLOR_PRESETS:
            raise ApiError(422, "unknown_preset", "Unknown color grade")
        if body.plan is not None:
            from autoedit_render.planner import EditPlan

            try:
                EditPlan.model_validate(body.plan)
            except ValueError as exc:
                raise ApiError(422, "invalid_plan", "The edited plan is not valid") from exc
        music_id = body.music_asset_id if body.music_asset_id is not None else opts.get("music_asset_id")
        if music_id:
            m = db.get(Asset, music_id)
            if m is None or m.user_id != user.id or m.kind != "music":
                raise ApiError(404, "asset_not_found", "Music file not found. Add it again.")
    else:
        mp = project.motion
        if mp is None or mp.status != "ready" or not mp.composition:
            raise ApiError(409, "not_ready", "The storyboard is not ready yet")
        if mp.pending:
            raise ApiError(409, "busy", "Wait for the scene rewrite to finish")
    for tid in (body.intro_template_id, body.outro_template_id):
        if tid:
            t = db.get(Template, tid)
            if t is None or t.user_id != user.id:
                raise ApiError(404, "template_not_found", "Saved template not found")
    settings = {
        "resolution": body.resolution,
        "aspect": body.aspect or opts.get("aspect", "9:16"),
        "color_preset": body.color_preset or opts.get("color_preset", "auto"),
        "prompt": body.prompt if body.prompt is not None else project.prompt,
        "plan": body.plan,
        "captions": body.captions if body.captions is not None else opts.get("captions"),
        "caption_style": body.caption_style or opts.get("caption_style"),
        "music_asset_id": body.music_asset_id if body.music_asset_id is not None else opts.get("music_asset_id"),
        "target_duration_s": body.target_duration_s if body.target_duration_s is not None else opts.get("target_duration_s"),
        "intro_template_id": body.intro_template_id if body.intro_template_id is not None else opts.get("intro_template_id"),
        "outro_template_id": body.outro_template_id if body.outro_template_id is not None else opts.get("outro_template_id"),
    }  # fmt: skip
    r = Render(project_id=project.id, user_id=user.id, kind=project.mode, settings=settings)
    db.add(r)
    db.commit()
    background.add_task(enqueue, "render", r.id)
    return render_out(r)


def _render(db: DbSession, user: CurrentUser, render_id: str) -> Render:
    r = db.get(Render, render_id)
    if r is None or r.user_id != user.id:
        raise ApiError(404, "render_not_found", "Video not found")
    return r


@router.get("/v1/renders/{render_id}/video", response_model=None)
def render_video(render_id: str, db: DbSession, user: CurrentUser, download: bool = Query(False)) -> Response:
    r = _render(db, user, render_id)
    if r.status != "ready" or not r.storage_key:
        raise ApiError(409, "not_ready", "This video is not ready yet")
    name = f"autocut-{r.kind}-{r.id[-6:]}.mp4" if download else None
    return _serve(get_storage(), r.storage_key, "video/mp4", name)


@router.get("/v1/renders/{render_id}/poster", response_model=None)
def render_poster(render_id: str, db: DbSession, user: CurrentUser) -> Response:
    r = _render(db, user, render_id)
    if not r.poster_key:
        raise ApiError(404, "no_poster", "No poster")
    return _serve(get_storage(), r.poster_key, "image/jpeg")


# ------------------------------------------------------------------------------------- templates


def _template_out(t: Template) -> TemplateOut:
    return TemplateOut(id=t.id, name=t.name, kind=t.kind, duration_s=t.duration_s,
                       video_url=f"/v1/templates/{t.id}/video",
                       poster_url=f"/v1/templates/{t.id}/poster" if t.poster_key else None,
                       created_at=t.created_at)  # fmt: skip


@router.get("/v1/templates", response_model=list[TemplateOut])
def list_templates(db: DbSession, user: CurrentUser) -> list[TemplateOut]:
    rows = db.scalars(
        select(Template).where(Template.user_id == user.id).order_by(Template.created_at.desc())
    ).all()
    return [_template_out(t) for t in rows]


@router.post("/v1/templates", status_code=status.HTTP_201_CREATED, response_model=TemplateOut)
def create_template(body: TemplateCreate, db: DbSession, user: CurrentUser) -> TemplateOut:
    """Save part of a finished video as a reusable intro, outro or clip."""
    from autoedit_render.ffrender import RenderError, cut_clip, poster

    r = _render(db, user, body.render_id)
    if r.status != "ready" or not r.storage_key:
        raise ApiError(409, "not_ready", "That video is not ready yet")
    if body.t_out - body.t_in < 0.5:
        raise ApiError(422, "too_short", "Choose at least half a second")
    if body.t_out - body.t_in > 60:
        raise ApiError(422, "too_long", "Templates can be at most 60 seconds")
    storage = get_storage()
    settings = get_settings()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    tid = new_id("tpl")
    work = settings.work_dir / tid
    try:
        src = storage.local_path(r.storage_key) or storage.get_file(r.storage_key, work / "src.mp4")
        clip = work / "clip.mp4"
        dur = cut_clip(src, clip, body.t_in, body.t_out)
        jpg = poster(clip, work / "poster.jpg", t=min(0.5, dur / 2))
        key = f"users/{user.id}/templates/{tid}"
        storage.put_file(key + ".mp4", clip, "video/mp4")
        storage.put_file(key + ".jpg", jpg, "image/jpeg")
    except RenderError as exc:
        raise ApiError(500, "template_failed", "That part could not be saved") from exc
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    t = Template(id=tid, user_id=user.id, name=body.name, kind=body.kind, storage_key=key + ".mp4",
                 poster_key=key + ".jpg", duration_s=round(dur, 3), source_render_id=r.id)  # fmt: skip
    db.add(t)
    db.commit()
    return _template_out(t)


def _template(db: DbSession, user: CurrentUser, template_id: str) -> Template:
    t = db.get(Template, template_id)
    if t is None or t.user_id != user.id:
        raise ApiError(404, "template_not_found", "Template not found")
    return t


@router.get("/v1/templates/{template_id}/video", response_model=None)
def template_video(template_id: str, db: DbSession, user: CurrentUser) -> Response:
    return _serve(get_storage(), _template(db, user, template_id).storage_key, "video/mp4")


@router.get("/v1/templates/{template_id}/poster", response_model=None)
def template_poster(template_id: str, db: DbSession, user: CurrentUser) -> Response:
    t = _template(db, user, template_id)
    if not t.poster_key:
        raise ApiError(404, "no_poster", "No poster")
    return _serve(get_storage(), t.poster_key, "image/jpeg")


@router.delete("/v1/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_template(template_id: str, db: DbSession, user: CurrentUser) -> Response:
    t = _template(db, user, template_id)
    storage = get_storage()
    storage.delete_prefix(t.storage_key)
    if t.poster_key:
        storage.delete_prefix(t.poster_key)
    db.delete(t)
    db.commit()
    return Response(status_code=204)
