"""Resumable, chunked uploads (tus-style offsets) that survive flaky networks.

1. ``POST /v1/uploads`` declares filename and size.
2. ``PATCH /v1/uploads/{id}`` with ``Upload-Offset: <n>`` appends a chunk. A wrong offset returns 409 with
   the server's offset, so a client resumes from ``HEAD /v1/uploads/{id}`` after any failure.
3. ``POST /v1/uploads/{id}/complete`` verifies size (and optional SHA-256), probes the file, and moves it
   to object storage.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from autoedit_pipeline.media import MediaError, probe
from autoedit_schemas.media import MediaInfo

from ..db import Upload
from ..deps import CurrentUser, DbSession
from ..schemas import ACCEPTED_EXTENSIONS, ApiError, UploadComplete, UploadCreate, UploadOut
from ..services import get_upload
from ..settings import get_settings
from ..storage import LocalStorage, get_storage, upload_key

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])


def _staging(upload_id: str) -> Path:
    d = get_settings().staging_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / upload_id


def _offset(upload: Upload) -> int:
    if upload.status != "uploading":
        return upload.received_bytes
    path = _staging(upload.id)
    return path.stat().st_size if path.exists() else 0


def _out(upload: Upload) -> UploadOut:
    return UploadOut(
        id=upload.id,
        filename=upload.filename,
        size_bytes=upload.size_bytes,
        offset=_offset(upload),
        status=upload.status,
        chunk_size=get_settings().recommended_chunk_bytes,
        media=MediaInfo.model_validate(upload.media) if upload.media else None,
        error_code=upload.error_code,
        error=upload.error,
        created_at=upload.created_at,
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UploadOut)
def create_upload(body: UploadCreate, db: DbSession, user: CurrentUser) -> UploadOut:
    settings = get_settings()
    name = Path(body.filename).name
    if Path(name).suffix.lower() not in ACCEPTED_EXTENSIONS:
        raise ApiError(
            415,
            "unsupported_format",
            f"'{name}' is not a supported video. Accepted: {', '.join(ACCEPTED_EXTENSIONS)}",
            accepted=list(ACCEPTED_EXTENSIONS),
        )
    if body.size_bytes > settings.max_upload_bytes:
        raise ApiError(
            413, "file_too_large", "File is larger than the upload limit", limit=settings.max_upload_bytes
        )
    upload = Upload(
        user_id=user.id, filename=name, size_bytes=body.size_bytes, content_type=body.content_type
    )
    db.add(upload)
    db.commit()
    _staging(upload.id).touch()
    return _out(upload)


@router.get("/{upload_id}", response_model=UploadOut)
def read_upload(upload_id: str, db: DbSession, user: CurrentUser) -> UploadOut:
    return _out(get_upload(db, user, upload_id))


@router.head("/{upload_id}")
def upload_offset(upload_id: str, db: DbSession, user: CurrentUser) -> Response:
    upload = get_upload(db, user, upload_id)
    return Response(
        headers={
            "Upload-Offset": str(_offset(upload)),
            "Upload-Length": str(upload.size_bytes),
            "Cache-Control": "no-store",
        }
    )


@router.patch("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def append_chunk(
    upload_id: str,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    upload_offset: Annotated[int, Header(alias="Upload-Offset", ge=0)],
) -> Response:
    settings = get_settings()
    upload = await run_in_threadpool(get_upload, db, user, upload_id)
    if upload.status != "uploading":
        raise ApiError(409, "upload_closed", f"Upload is already {upload.status}")
    path = _staging(upload.id)
    current = path.stat().st_size if path.exists() else 0
    if upload_offset != current:
        raise ApiError(409, "offset_mismatch", "Resume from the server offset", offset=current)

    written = 0
    with path.open("ab") as f:
        async for chunk in request.stream():
            written += len(chunk)
            if written > settings.max_chunk_bytes:
                raise ApiError(
                    413, "chunk_too_large", "Chunk exceeds the maximum size", limit=settings.max_chunk_bytes
                )
            if current + written > upload.size_bytes:
                f.truncate(current)
                raise ApiError(413, "exceeds_declared_size", "More bytes than declared at upload creation")
            f.write(chunk)
    new_offset = current + written
    upload.received_bytes = new_offset
    upload.updated_at = datetime.now(UTC)
    await run_in_threadpool(db.commit)
    return Response(status_code=204, headers={"Upload-Offset": str(new_offset)})


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(8 << 20):
            h.update(block)
    return h.hexdigest()


@router.post("/{upload_id}/complete", response_model=UploadOut)
def complete_upload(
    upload_id: str, db: DbSession, user: CurrentUser, body: UploadComplete | None = None
) -> UploadOut:
    upload = get_upload(db, user, upload_id)
    if upload.status == "complete":
        return _out(upload)  # idempotent
    if upload.status == "failed":
        raise ApiError(409, "upload_failed", upload.error or "Upload failed", code=upload.error_code)
    path = _staging(upload.id)
    size = path.stat().st_size if path.exists() else 0
    if size != upload.size_bytes:
        raise ApiError(
            409, "upload_incomplete", "Not all bytes have arrived", offset=size, size=upload.size_bytes
        )

    digest = _sha256(path)
    if body and body.sha256 and body.sha256 != digest:
        raise ApiError(422, "checksum_mismatch", "SHA-256 does not match; re-upload the file", sha256=digest)

    # Probe before accepting: rejects corrupt or non-video files with a clear reason.
    probe_path = path.with_name(path.name + Path(upload.filename).suffix.lower())
    path.rename(probe_path)
    try:
        info = probe(probe_path)
    except MediaError as exc:
        probe_path.unlink(missing_ok=True)
        upload.status, upload.error_code, upload.error = "failed", exc.code, str(exc)
        db.commit()
        raise ApiError(422, exc.code, f"This file can't be used: {exc}") from exc

    storage = get_storage()
    key = upload_key(user.id, upload.id, upload.filename)
    if isinstance(storage, LocalStorage):
        storage.move_in(key, probe_path)
    else:
        storage.put_file(key, probe_path, upload.content_type)
        probe_path.unlink(missing_ok=True)

    upload.sha256 = digest
    upload.media = info.model_dump(mode="json")
    upload.storage_key = key
    upload.received_bytes = size
    upload.status = "complete"
    upload.completed_at = datetime.now(UTC)
    db.commit()
    return _out(upload)


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
def abort_upload(upload_id: str, db: DbSession, user: CurrentUser) -> Response:
    upload = get_upload(db, user, upload_id)
    from ..db import ProjectClip

    if db.query(ProjectClip).filter(ProjectClip.upload_id == upload.id).first():
        raise ApiError(409, "upload_in_use", "Remove the clip from its project first")
    _staging(upload.id).unlink(missing_ok=True)
    if upload.storage_key:
        get_storage().delete_prefix(upload.storage_key)
    db.delete(upload)
    db.commit()
    return Response(status_code=204)
