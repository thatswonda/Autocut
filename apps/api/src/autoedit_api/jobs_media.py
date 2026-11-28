"""Background jobs for motion graphics (storyboards) and rendering (edits and motion videos)."""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from autoedit_brand.kit import ImageAsset, build_kit
from autoedit_brand.palette import prepare_image
from autoedit_motion.storyboard import (
    ImageInput,
    StoryboardError,
    StoryboardRequest,
    Usage,
    ai_available,
    generate_composition,
    regenerate_scene,
    revise_composition,
)
from autoedit_motion.styles import get_style
from autoedit_schemas import Composition, Signals, parse_model

from .db import Asset, MotionPlan, Project, Render, Template, UsageEvent, new_id, session_scope
from .settings import get_settings
from .storage import Storage, get_storage, project_prefix

log = logging.getLogger(__name__)


def _local(storage: Storage, key: str, workdir: Path, name: str) -> Path:
    path = storage.local_path(key)
    if path is not None:
        return path
    return storage.get_file(key, workdir / name)


def _workdir(prefix: str) -> Path:
    settings = get_settings()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=settings.work_dir))


def _set_plan(project_id: str, **fields: Any) -> None:
    db = session_scope()
    try:
        mp = db.get(MotionPlan, project_id)
        if mp is not None:
            for k, v in fields.items():
                setattr(mp, k, v)
            db.commit()
    finally:
        db.close()


# ------------------------------------------------------------------------------- motion planning


def _storyboard_request(project: Project, mp: MotionPlan, image_assets: list[Asset], workdir: Path,
                        storage: Storage) -> StoryboardRequest:  # fmt: skip
    opts = project.options or {}
    kit = mp.brand_kit or {}
    sources = [(sid, text) for sid, text in (mp.sources_text or {}).get("items", [])]
    images = [
        ImageInput(
            key=a.id,
            kind=a.kind,
            path=(_local(storage, a.storage_key + ".poster.jpg", workdir, f"{a.id}.poster.jpg") if a.kind == "video"
                  else _local(storage, a.storage_key, workdir, f"{a.id}{Path(a.storage_key).suffix}")),
            source=a.source or f"file:{a.filename}",
            duration_s=float(a.duration_s or 0),
        )
        for a in image_assets
    ]  # fmt: skip
    return StoryboardRequest(
        brief=project.prompt,
        aspect=opts.get("aspect", "9:16"),
        duration_s=float(opts.get("duration_s", 30)),
        style=opts.get("style", "bold_pop"),
        language=opts.get("language") or kit.get("language") or "en",
        goal=opts.get("goal", "promo"),
        brand_name=kit.get("name", ""),
        sources=sources,
        images=images,
        voiceover=opts.get("voiceover", True) is not False,
        music=bool(opts.get("music_asset_id")),
    )


def _brand_for_composition(kit: dict[str, Any], style_key: str) -> dict[str, Any]:
    st = get_style(style_key)
    return {
        "name": kit.get("name", "")[:200],
        "logo_asset": kit.get("logo_asset"),
        "colors": kit.get("colors") or {},
        "fonts": {"heading": st.display_font, "body": st.body_font},
        "tone": "",
    }


def motion_plan_job(project_id: str) -> str:
    """Read sources -> brand kit -> Claude storyboard. Leaves the plan 'ready' for the user to review."""
    storage = get_storage()
    workdir = _workdir(f"plan-{project_id}-")
    db = session_scope()
    usage = Usage()
    try:
        project = db.get(Project, project_id)
        mp = db.get(MotionPlan, project_id)
        if project is None or mp is None:
            return "deleted"
        mp.status, mp.stage, mp.progress, mp.error_code, mp.error = (
            "planning",
            "Reading your sources",
            0.1,
            None,
            None,
        )
        db.commit()

        urls = list((mp.source_urls or {}).get("urls", []))
        asset_ids = list((mp.source_urls or {}).get("asset_ids", []))
        uploaded = db.scalars(
            select(Asset).where(Asset.id.in_(asset_ids), Asset.user_id == project.user_id)
        ).all()
        videos = [a for a in uploaded if a.kind == "video"]
        uploaded = [a for a in uploaded if a.kind != "video"]
        uploads = [
            ImageAsset(
                key=a.id,
                kind=a.kind,
                data=storage.read_bytes(a.storage_key),
                ext=Path(a.storage_key).suffix,
                width=a.width or 0,
                height=a.height or 0,
                source=f"file:{a.filename}",
            )
            for a in uploaded
        ]
        kit = build_kit(urls, uploads)

        _set_plan(project_id, stage="Building your brand kit", progress=0.3)
        # Store images found on the website as project assets so they can be shown and rendered.
        key_to_asset: dict[str, str] = {a.id: a.id for a in uploaded}
        for im in kit.images:
            if im.key in key_to_asset:
                continue
            aid = new_id("ast")
            key = f"{project_prefix(project.user_id, project.id)}assets/{aid}{im.ext}"
            storage.put_bytes(key, im.data, "image/png" if im.ext == ".png" else "image/jpeg")
            db.add(Asset(id=aid, user_id=project.user_id, project_id=project.id, kind=im.kind,
                         filename=f"{im.kind}{im.ext}", content_type="image/png" if im.ext == ".png" else "image/jpeg",
                         storage_key=key, size_bytes=len(im.data), width=im.width, height=im.height,
                         source=im.source))  # fmt: skip
            key_to_asset[im.key] = aid
        db.commit()
        logo_asset = key_to_asset.get(kit.logo_key) if kit.logo_key else None
        opts = project.options or {}
        brand_kit = {
            "name": (opts.get("brand_name") or kit.name or "").strip(),
            "colors": kit.colors,
            "logo_asset": logo_asset,
            "language": kit.language,
            "sources": [{"id": s.source_id, "url": s.url, "title": s.title} for s in kit.sites],
            "images": [
                {"asset_id": key_to_asset[i.key], "kind": i.kind, "source": i.source}
                for i in kit.images
                if i.key in key_to_asset
            ],
            "warnings": kit.warnings,
        }
        mp = db.get(MotionPlan, project_id)
        assert mp is not None
        mp.brand_kit = brand_kit
        mp.sources_text = {"items": [[s.source_id, s.as_source_text()] for s in kit.sites]}
        mp.stage, mp.progress = "Writing your storyboard", 0.45
        db.commit()

        if not ai_available():
            mp.status, mp.stage = "failed", None
            mp.error_code = "ai_unavailable"
            mp.error = "Storyboards need Claude: add ANTHROPIC_API_KEY to the server's environment, then press Try again."
            db.commit()
            return "failed"

        images = db.scalars(
            select(Asset).where(Asset.id.in_(list(key_to_asset.values()) + [v.id for v in videos]))
        ).all()
        req = _storyboard_request(project, mp, list(images), workdir, storage)
        comp = generate_composition(req, _brand_for_composition(brand_kit, req.style), usage=usage)
        comp = _self_review(comp, req, workdir, usage, project_id)
        mp = db.get(MotionPlan, project_id)
        assert mp is not None
        mp.composition = comp.model_dump(by_alias=True, mode="json")
        if not brand_kit.get("name") and comp.brand.name:
            mp.brand_kit = {**brand_kit, "name": comp.brand.name}
        mp.version += 1
        mp.status, mp.stage, mp.progress = "ready", None, 1.0
        mp.cost_usd = round((mp.cost_usd or 0) + usage.cost_usd, 4)
        db.add(UsageEvent(user_id=project.user_id, project_id=project.id, stage="storyboard", cost_usd=usage.cost_usd,
                          details={"model": usage.model, "input_tokens": usage.input_tokens,
                                   "output_tokens": usage.output_tokens, "calls": usage.calls}))  # fmt: skip
        db.commit()
        return "ready"
    except StoryboardError as exc:
        db.rollback()
        _set_plan(project_id, status="failed", stage=None, error_code=exc.code, error=str(exc))
        _record_cost(project_id, usage, "storyboard")
        return "failed"
    except Exception as exc:
        db.rollback()
        log.exception("motion plan failed for %s", project_id)
        _set_plan(project_id, status="failed", stage=None, error_code="internal_error",
                  error=f"Something went wrong while preparing the storyboard ({type(exc).__name__}). Try again.")  # fmt: skip
        return "failed"
    finally:
        db.close()
        shutil.rmtree(workdir, ignore_errors=True)


def _self_review(
    comp: Composition, req: StoryboardRequest, workdir: Path, usage: Usage, project_id: str
) -> Composition:
    """Lay the storyboard out; scenes whose text cannot fit get one rewrite with shorter copy."""
    from autoedit_motion.render import layout_warnings

    images = {im.key: im.path for im in req.images}
    try:
        _set_plan(project_id, stage="Checking the layout", progress=0.85)
        warnings = layout_warnings(comp, images, workdir=workdir / "review")
    except Exception as exc:  # the review is a bonus; never fail the storyboard over it
        log.warning("layout review skipped: %s", exc)
        return comp
    scene_ids = list(dict.fromkeys(w.split()[0] for w in warnings if w.split()))[:3]
    for sid in scene_ids:
        if not any(s.id == sid for s in comp.scenes):
            continue
        try:
            comp = regenerate_scene(comp, sid, "The text does not fit on screen at a readable size. Make it "
                                    "noticeably shorter, keep the same facts and template.", req, usage=usage)  # fmt: skip
        except StoryboardError as exc:
            log.info("self-review rewrite of %s skipped: %s", sid, exc)
    if scene_ids:
        log.info("self-review shortened scenes %s", scene_ids)
    return comp


def _record_cost(project_id: str, usage: Usage, stage: str) -> None:
    if not usage.calls:
        return
    db = session_scope()
    try:
        project = db.get(Project, project_id)
        mp = db.get(MotionPlan, project_id)
        if project and mp:
            mp.cost_usd = round((mp.cost_usd or 0) + usage.cost_usd, 4)
            db.add(UsageEvent(user_id=project.user_id, project_id=project_id, stage=stage, cost_usd=usage.cost_usd,
                              details={"model": usage.model, "input_tokens": usage.input_tokens,
                                       "output_tokens": usage.output_tokens}))  # fmt: skip
            db.commit()
    finally:
        db.close()


def scene_regen_job(project_id: str) -> str:
    storage = get_storage()
    workdir = _workdir(f"scene-{project_id}-")
    db = session_scope()
    usage = Usage()
    try:
        project = db.get(Project, project_id)
        mp = db.get(MotionPlan, project_id)
        if project is None or mp is None or not mp.pending or not mp.composition:
            return "nothing"
        scene_id, note = mp.pending.get("scene_id", ""), mp.pending.get("note", "")
        kit = mp.brand_kit or {}
        ids = [i["asset_id"] for i in kit.get("images", [])]
        images = db.scalars(select(Asset).where(Asset.id.in_(ids))).all()
        req = _storyboard_request(project, mp, list(images), workdir, storage)
        comp = parse_model(Composition, mp.composition)
        new_comp = regenerate_scene(comp, scene_id, note, req, usage=usage)
        mp.composition = new_comp.model_dump(by_alias=True, mode="json")
        mp.version += 1
        mp.pending = None
        mp.error_code = mp.error = None
        mp.cost_usd = round((mp.cost_usd or 0) + usage.cost_usd, 4)
        db.add(UsageEvent(user_id=project.user_id, project_id=project.id, stage="scene_regen", cost_usd=usage.cost_usd,
                          details={"model": usage.model, "scene": scene_id}))  # fmt: skip
        db.commit()
        return "ready"
    except StoryboardError as exc:
        db.rollback()
        _set_plan(project_id, pending=None, error_code=exc.code, error=str(exc))
        _record_cost(project_id, usage, "scene_regen")
        return "failed"
    except Exception as exc:
        db.rollback()
        log.exception("scene regeneration failed for %s", project_id)
        _set_plan(project_id, pending=None, error_code="internal_error",
                  error=f"The scene could not be rewritten ({type(exc).__name__}).")  # fmt: skip
        return "failed"
    finally:
        db.close()
        shutil.rmtree(workdir, ignore_errors=True)


# ------------------------------------------------------------------------------ motion revisions

_URL_RE = re.compile(r"\b((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s,;]*)?)", re.I)
_MORE_PHOTOS_RE = re.compile(
    r"\b(more|extra|additional|other|new|different|better)\s+(photos?|pictures?|images?|pics?|shots?)\b", re.I
)


def _links_in(text: str) -> list[str]:
    out = []
    for m in _URL_RE.findall(text or ""):
        u = m if m.lower().startswith(("http://", "https://")) else f"https://{m}"
        if "." in u.split("//", 1)[1].split("/", 1)[0]:
            out.append(u.rstrip(".)"))
    return list(dict.fromkeys(out))[:5]


def _store_kit_images(
    db: Any, storage: Storage, project: Project, kit: Any, *, skip_sizes: set[tuple[int, int, int]]
) -> list[dict[str, Any]]:
    """Save a fetched kit's images as project assets, skipping ones already in the project."""
    entries = []
    for im in kit.images:
        sig = (im.width, im.height, len(im.data))
        if sig in skip_sizes:
            continue
        skip_sizes.add(sig)
        aid = new_id("ast")
        ctype = "image/png" if im.ext == ".png" else "image/jpeg"
        key = f"{project_prefix(project.user_id, project.id)}assets/{aid}{im.ext}"
        storage.put_bytes(key, im.data, ctype)
        db.add(Asset(id=aid, user_id=project.user_id, project_id=project.id, kind=im.kind,
                     filename=f"{im.kind}{im.ext}", content_type=ctype, storage_key=key, size_bytes=len(im.data),
                     width=im.width, height=im.height, source=im.source))  # fmt: skip
        entries.append({"asset_id": aid, "kind": im.kind, "source": im.source})
    return entries


def motion_revise_job(project_id: str) -> str:
    """Apply a change request: new photos and links, more photos from the sites, then Claude's revision."""
    storage = get_storage()
    workdir = _workdir(f"revise-{project_id}-")
    db = session_scope()
    usage = Usage()
    pending: dict[str, Any] = {}
    try:
        project = db.get(Project, project_id)
        mp = db.get(MotionPlan, project_id)
        if (
            project is None
            or mp is None
            or not mp.pending
            or mp.pending.get("kind") not in ("revise", "photos")
        ):
            return "nothing"
        pending = dict(mp.pending)
        message = str(pending.get("message") or "")
        kit = dict(mp.brand_kit or {})
        kit_images = list(kit.get("images") or [])
        notes: list[str] = []
        existing = db.scalars(select(Asset).where(Asset.project_id == project.id)).all()
        sizes = {(a.width or 0, a.height or 0, a.size_bytes or 0) for a in existing}

        # 1. Photos and clips attached to the request.
        new_ids = [str(a) for a in pending.get("asset_ids") or []]
        attached = db.scalars(
            select(Asset).where(Asset.id.in_(new_ids), Asset.user_id == project.user_id)
        ).all()
        for a in attached:
            a.project_id = project.id
            if a.kind != "video":
                kit_images.append(
                    {"asset_id": a.id, "kind": a.kind, "source": a.source or f"file:{a.filename}"}
                )
        if attached:
            notes.append(
                f"The user attached {len(attached)} new file(s): "
                + ", ".join(f"{a.id} ({a.kind})" for a in attached)
            )

        # 2. Links in the message: read them like the first time.
        sources = [list(x) for x in (mp.sources_text or {}).get("items", [])]
        links = _links_in(message)
        if links:
            _set_plan(project_id, stage="Reading your links")
            new_kit = build_kit(links, [])
            entries = _store_kit_images(db, storage, project, new_kit, skip_sizes=sizes)
            kit_images += entries
            sources += [[st.source_id, st.as_source_text()] for st in new_kit.sites]
            kit["sources"] = [
                *kit.get("sources", []),
                *({"id": st.source_id, "url": st.url, "title": st.title} for st in new_kit.sites),
            ]
            notes.append(f"Read {len(new_kit.sites)} page(s) from the new link(s) and added {len(entries)} image(s): "
                         + ", ".join(e["asset_id"] for e in entries))  # fmt: skip
            notes += [f"Link problem: {w}" for w in new_kit.warnings[:3]]

        # 3. More photos from the sites already used.
        if pending.get("kind") == "photos" or _MORE_PHOTOS_RE.search(message):
            roots = list(dict.fromkeys(src["url"] for src in kit.get("sources", []) if src.get("url")))[:5]
            if roots:
                _set_plan(project_id, stage="Finding more photos")
                more = build_kit(roots, [], inner_pages=4, photo_total=24)
                entries = [
                    e
                    for e in _store_kit_images(db, storage, project, more, skip_sizes=sizes)
                    if e["kind"] == "photo"
                ]
                kit_images += entries
                notes.append(
                    f"Found {len(entries)} more photo(s) on the website(s)"
                    + (": " + ", ".join(e["asset_id"] for e in entries) if entries else "")
                )
            else:
                notes.append("There are no website links to look for more photos on; the user can paste one.")
        kit["images"] = kit_images
        mp.sources_text = {"items": sources}
        mp.brand_kit = kit
        db.commit()

        reply = "; ".join(n for n in notes if not n.startswith("The user attached")) or "Done."
        remove: list[str] = []
        if pending.get("kind") == "revise" and mp.composition:
            if not ai_available():
                raise StoryboardError(
                    "ai_unavailable", "Changes by prompt need ANTHROPIC_API_KEY on the server."
                )
            _set_plan(project_id, stage="Applying your changes")
            ids = [i["asset_id"] for i in kit_images]
            ids += [
                a.id
                for a in db.scalars(
                    select(Asset).where(Asset.project_id == project.id, Asset.kind == "video")
                ).all()
            ]
            images = db.scalars(select(Asset).where(Asset.id.in_(ids))).all()
            req = _storyboard_request(project, mp, list(images), workdir, storage)
            comp = parse_model(Composition, mp.composition)
            new_comp, reply, remove, sound = revise_composition(comp, message, req, usage=usage, notes=notes)
            if remove:
                for a in db.scalars(
                    select(Asset).where(Asset.id.in_(remove), Asset.project_id == project.id)
                ).all():
                    a.project_id = None
                kit["images"] = [i for i in kit_images if i["asset_id"] not in set(remove)]
                if kit.get("logo_asset") in remove:
                    kit["logo_asset"] = None
            mp.composition = new_comp.model_dump(by_alias=True, mode="json")
            opts = dict(project.options or {})
            opts.update(
                {
                    "duration_s": new_comp.meta.duration_s,
                    "style": new_comp.meta.style,
                    "aspect": new_comp.meta.aspect,
                    **sound,
                }
            )
            project.options = opts
            mp.version += 1
        log_entry = {
            "at": datetime.now(UTC).isoformat(),
            "message": message or "Find more photos",
            "reply": reply,
        }
        kit["revisions"] = [*kit.get("revisions", []), log_entry][-30:]
        mp.brand_kit = {**kit}
        flag_modified(mp, "brand_kit")  # kit was shared with the earlier commit; force the update
        mp.pending, mp.stage = None, None
        mp.error_code = mp.error = None
        mp.cost_usd = round((mp.cost_usd or 0) + usage.cost_usd, 4)
        if usage.calls:
            db.add(UsageEvent(user_id=project.user_id, project_id=project.id, stage="revision", cost_usd=usage.cost_usd,
                              details={"model": usage.model, "calls": usage.calls}))  # fmt: skip
        db.commit()
        return "ready"
    except StoryboardError as exc:
        db.rollback()
        _revision_failed(project_id, pending, str(exc), exc.code)
        _record_cost(project_id, usage, "revision")
        return "failed"
    except Exception as exc:
        db.rollback()
        log.exception("revision failed for %s", project_id)
        _revision_failed(project_id, pending, f"The change could not be applied ({type(exc).__name__}). Try again.",
                         "internal_error")  # fmt: skip
        return "failed"
    finally:
        db.close()
        shutil.rmtree(workdir, ignore_errors=True)


def _revision_failed(project_id: str, pending: dict[str, Any], error: str, code: str) -> None:
    db = session_scope()
    try:
        mp = db.get(MotionPlan, project_id)
        if mp is None:
            return
        kit = dict(mp.brand_kit or {})
        entry = {"at": datetime.now(UTC).isoformat(), "message": str(pending.get("message") or "Find more photos"),
                 "reply": f"I couldn't apply that: {error}", "failed": True}  # fmt: skip
        kit["revisions"] = [*kit.get("revisions", []), entry][-30:]
        mp.brand_kit = kit
        mp.pending, mp.stage, mp.error_code, mp.error = None, None, code, error
        db.commit()
    finally:
        db.close()


# -------------------------------------------------------------------------------------- render


def _set_render(render_id: str, **fields: Any) -> None:
    db = session_scope()
    try:
        r = db.get(Render, render_id)
        if r is not None:
            for k, v in fields.items():
                setattr(r, k, v)
            db.commit()
    finally:
        db.close()


def render_job(render_id: str) -> str:
    from autoedit_motion.render import render_composition
    from autoedit_render.ffrender import join_pieces, poster, render_edl

    storage = get_storage()
    workdir = _workdir(f"render-{render_id}-")
    t0 = time.perf_counter()
    db = session_scope()
    last = [0.0]

    def progress(fraction: float, stage: str) -> None:
        now = time.monotonic()
        if now - last[0] > 0.8 or fraction >= 0.99:
            last[0] = now
            _set_render(render_id, progress=round(0.05 + fraction * 0.9, 4), stage=stage)

    try:
        r = db.get(Render, render_id)
        if r is None:
            return "deleted"
        project = db.get(Project, r.project_id)
        if project is None:
            return "deleted"
        r.status, r.stage, r.progress, r.error_code, r.error = "processing", "Preparing", 0.02, None, None
        db.commit()
        s = r.settings or {}
        short_edge = 1080 if int(s.get("resolution", 1080)) >= 1080 else 720

        def template_file(tid: str | None) -> Path | None:
            if not tid:
                return None
            t = db.get(Template, tid)
            if t is None or t.user_id != project.user_id:
                return None
            return _local(storage, t.storage_key, workdir, f"{tid}.mp4")

        intro, outro = template_file(s.get("intro_template_id")), template_file(s.get("outro_template_id"))
        out = workdir / "final.mp4"
        if r.kind == "edit":
            clips = sorted([c for c in project.clips if c.status == "ready"], key=lambda c: c.position)
            if not clips:
                raise RuntimeError("no analyzed clips")
            files: dict[str, Path] = {}
            previews: dict[str, Path] = {}
            sigs: dict[str, Signals] = {}
            for c in clips:
                art = next((a for a in c.artifacts if a.kind == "signals"), None)
                if art is None or not c.upload.storage_key:
                    continue
                sigs[c.id] = parse_model(Signals, storage.read_bytes(art.storage_key))
                files[c.id] = _local(
                    storage, c.upload.storage_key, workdir, f"{c.id}{Path(c.upload.storage_key).suffix}"
                )
                prev = next((a for a in c.artifacts if a.kind == "proxy"), None)
                if prev is not None:
                    previews[c.id] = _local(storage, prev.storage_key, workdir, f"{c.id}.proxy.mp4")
            from .jobs_edit import plan_edit

            edl, report = plan_edit(db, storage, project=project, clips=clips, files=files, sigs=sigs,
                                    settings=s, progress=lambda f, st: progress(f * 0.3, st))  # fmt: skip
            r = db.get(Render, render_id)
            if r is None:
                return "deleted"
            r.report = report
            db.commit()
            music = None
            if s.get("music_asset_id"):
                ma = db.get(Asset, s["music_asset_id"])
                if ma is not None and ma.user_id == project.user_id:
                    music = _local(storage, ma.storage_key, workdir, f"music{Path(ma.storage_key).suffix}")
            result = render_edl(edl, files, sigs, out, workdir=workdir / "edit", short_edge=short_edge,
                                intro=intro, outro=outro, music=music, previews=previews,
                                progress=lambda f: progress(0.3 + f * 0.7, "Cutting, grading and captioning"))  # fmt: skip
            width, height, duration = result.width, result.height, result.duration_s
        else:
            mp = db.get(MotionPlan, project.id)
            if mp is None or not mp.composition:
                raise RuntimeError("no storyboard to render")
            comp = parse_model(Composition, mp.composition)
            ids = {a for sc in comp.scenes for a in sc.content.assets}
            ids |= {sc.clip_id for sc in comp.scenes if sc.template == "footage" and sc.clip_id}
            if comp.brand.logo_asset:
                ids.add(comp.brand.logo_asset)
            assets = {
                a.id: _local(storage, a.storage_key, workdir, f"{a.id}{Path(a.storage_key).suffix}")
                for a in db.scalars(select(Asset).where(Asset.id.in_(list(ids)))).all()
            }
            r.report = mp.composition
            db.commit()
            opts = project.options or {}
            music_asset = db.get(Asset, opts["music_asset_id"]) if opts.get("music_asset_id") else None
            music_choice = str(opts.get("music") or "auto")
            from autoedit_motion.narration import auto_script, fit_to_voice
            from autoedit_motion.voice import speak_best
            from autoedit_render.motion_audio import Line, mix, motion_cues
            from autoedit_render.music import bed_file, mood_for_style

            # 1. Voiceover first (on unless the user turned it off): the script from the storyboard, or one
            #    written from the scenes' own text; then every scene is stretched to fit what is said over it.
            lines: list[Line] = []
            notes: list[str] = []
            if opts.get("voiceover", True) is not False:
                script = list(comp.voiceover.script) or auto_script(comp)
                if script:
                    progress(0.02, "Recording the voiceover")
                    spoken, vo_note = speak_best([(ln.t_in, ln.text) for ln in script], workdir / "vo",
                                                 voice=str(opts.get("voice") or "auto"),
                                                 style=str(opts.get("voice_style") or ""))  # fmt: skip
                    if vo_note:
                        notes.append(vo_note)
                    data = comp.model_dump(by_alias=True)
                    data["voiceover"] = {"enabled": True, "voice": comp.voiceover.voice,
                                         "script": [{"t_in": ln.t_in, "text": ln.text} for ln in script]}  # fmt: skip
                    comp, starts = fit_to_voice(
                        Composition.model_validate(data), [(x.t_in, x.duration_s) for x in spoken]
                    )
                    lines = [
                        Line(t, x.text, x.path, x.duration_s) for t, x in zip(starts, spoken, strict=True)
                    ]
                    r.report = comp.model_dump(by_alias=True, mode="json")
                    db.commit()
            main = workdir / "motion.mp4" if (intro or outro) else out
            silent = workdir / "motion_silent.mp4"
            # 2. Animate.
            res = render_composition(comp, assets, silent, workdir=workdir / "page", short_edge=short_edge,
                                     progress=lambda f: progress(0.05 + f * 0.8, "Animating"))  # fmt: skip
            width, height, duration = res.width, res.height, res.duration_s
            # 3. Sound: voice, music ducked under it, sound effects on every move.
            music_path: Path | None = None
            if music_asset is not None and music_asset.user_id == project.user_id:
                music_path = _local(storage, music_asset.storage_key, workdir,
                                    f"music{Path(music_asset.storage_key).suffix}")  # fmt: skip
            elif music_choice != "none":
                mood = mood_for_style(comp.meta.style) if music_choice == "auto" else music_choice
                music_path = bed_file(mood, duration)
            progress(0.88, "Mixing the sound")
            mix(silent, main, workdir=workdir / "mix", total_s=duration, lines=lines, music=music_path,
                music_db=-19.0 if lines else -10.0,
                captions=bool(opts.get("voiceover_captions")) and bool(lines), caption_style="clean",
                width=width, height=height, accent=comp.brand.colors.accent or "#FFD400",
                sfx=motion_cues(comp) if opts.get("sfx", True) is not False else [])  # fmt: skip
            if notes:
                s = {**s, "warnings": [*s.get("warnings", []), *notes]}
            if res.warnings:
                s = {**s, "warnings": [*s.get("warnings", []), *res.warnings]}
            if intro or outro:
                progress(0.9, "Adding intro and outro")
                pieces = [p for p in (intro, main, outro) if p]
                duration = join_pieces(pieces, out, aspect=comp.meta.aspect, short_edge=short_edge,
                                       workdir=workdir / "join")  # fmt: skip
        prefix = f"{project_prefix(project.user_id, project.id)}renders/{render_id}"
        storage.put_file(prefix + ".mp4", out, "video/mp4")
        poster_path = poster(out, workdir / "poster.jpg", t=min(1.5, max(0.1, duration / 3)))
        storage.put_file(prefix + ".jpg", poster_path, "image/jpeg")
        r = db.get(Render, render_id)
        if r is None:
            storage.delete_prefix(prefix)
            return "deleted"
        r.status, r.stage, r.progress = "ready", None, 1.0
        r.storage_key, r.poster_key = prefix + ".mp4", prefix + ".jpg"
        r.duration_s, r.width, r.height = round(duration, 3), width, height
        r.settings = s
        db.add(UsageEvent(user_id=project.user_id, project_id=project.id, stage=f"render_{r.kind}",
                          wall_seconds=round(time.perf_counter() - t0, 2), media_seconds=duration))  # fmt: skip
        db.commit()
        return "ready"
    except Exception as exc:
        db.rollback()
        log.exception("render %s failed", render_id)
        _set_render(render_id, status="failed", stage=None, error_code="render_failed",
                    error=f"Rendering failed ({type(exc).__name__}: {str(exc)[:200]}). Try again.")  # fmt: skip
        return "failed"
    finally:
        db.close()
        shutil.rmtree(workdir, ignore_errors=True)


def store_uploaded_image(storage: Storage, user_id: str, data: bytes, filename: str, kind: str) -> Asset:
    """Normalise an uploaded image and store it as an asset (not yet attached to a project)."""
    body, ext, w, h = prepare_image(data)
    aid = new_id("ast")
    key = f"users/{user_id}/assets/{aid}{ext}"
    storage.put_bytes(key, body, "image/png" if ext == ".png" else "image/jpeg")
    return Asset(id=aid, user_id=user_id, kind=kind, filename=Path(filename).name[:255],
                 content_type="image/png" if ext == ".png" else "image/jpeg", storage_key=key,
                 size_bytes=len(body), width=w, height=h, source=f"file:{Path(filename).name[:200]}")  # fmt: skip
