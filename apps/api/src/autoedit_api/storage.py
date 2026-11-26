"""Object storage: local filesystem for dev/tests, S3-compatible (Cloudflare R2, MinIO, AWS) for prod."""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from .settings import Settings, get_settings


class Storage(Protocol):
    def put_file(self, key: str, src: Path, content_type: str | None = None) -> int: ...
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> int: ...
    def get_file(self, key: str, dest: Path) -> Path: ...
    def read_bytes(self, key: str) -> bytes: ...
    def copy(self, src_key: str, dst_key: str) -> None: ...
    def exists(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> int: ...
    def local_path(self, key: str) -> Path | None: ...
    def presigned_url(self, key: str, expires_s: int = 3600) -> str | None: ...


def _safe(key: str) -> str:
    if key.startswith("/") or ".." in key.split("/"):
        raise ValueError(f"unsafe storage key: {key!r}")
    return key


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / _safe(key)

    def put_file(self, key: str, src: Path, content_type: str | None = None) -> int:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
        return dst.stat().st_size

    def move_in(self, key: str, src: Path) -> int:
        """Cheap rename when the source is on the same filesystem (staging -> storage)."""
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dst)
        return dst.stat().st_size

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> int:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dst)
        return len(data)

    def get_file(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._path(key), dest)
        return dest

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def copy(self, src_key: str, dst_key: str) -> None:
        self.put_file(dst_key, self._path(src_key))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete_prefix(self, prefix: str) -> int:
        target = self._path(prefix.rstrip("/"))
        if target.is_dir():
            count = sum(1 for p in target.rglob("*") if p.is_file())
            shutil.rmtree(target)
            return count
        if target.is_file():
            target.unlink()
            return 1
        return 0

    def local_path(self, key: str) -> Path | None:
        path = self._path(key)
        return path if path.is_file() else None

    def presigned_url(self, key: str, expires_s: int = 3600) -> str | None:
        return None


class S3Storage:
    def __init__(self, settings: Settings):
        import boto3
        from botocore.config import Config

        self.bucket = settings.s3_bucket
        self.sse = settings.s3_server_side_encryption
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            config=Config(retries={"max_attempts": 5, "mode": "adaptive"}, s3={"addressing_style": "path"}),
        )

    def _extra(self, content_type: str | None) -> dict[str, str]:
        extra: dict[str, str] = {}
        if self.sse:
            extra["ServerSideEncryption"] = "AES256"  # encryption at rest (PRD 6)
        if content_type:
            extra["ContentType"] = content_type
        return extra

    def put_file(self, key: str, src: Path, content_type: str | None = None) -> int:
        self.client.upload_file(str(src), self.bucket, _safe(key), ExtraArgs=self._extra(content_type))
        return src.stat().st_size

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> int:
        self.client.put_object(Bucket=self.bucket, Key=_safe(key), Body=data, **self._extra(content_type))
        return len(data)

    def get_file(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, _safe(key), str(dest))
        return dest

    def read_bytes(self, key: str) -> bytes:
        return bytes(self.client.get_object(Bucket=self.bucket, Key=_safe(key))["Body"].read())

    def copy(self, src_key: str, dst_key: str) -> None:
        self.client.copy(
            {"Bucket": self.bucket, "Key": _safe(src_key)},
            self.bucket,
            _safe(dst_key),
            ExtraArgs=self._extra(None),
        )

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=_safe(key))
        except ClientError:
            return False
        return True

    def delete_prefix(self, prefix: str) -> int:
        count = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=_safe(prefix)):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
                count += len(keys)
        return count

    def local_path(self, key: str) -> Path | None:
        return None

    def presigned_url(self, key: str, expires_s: int = 3600) -> str | None:
        return str(
            self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": _safe(key)}, ExpiresIn=expires_s
            )
        )

    def ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            self.client.create_bucket(Bucket=self.bucket)


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings.storage_dir)


# Key layout. Everything a user owns lives under their prefix so deletion is one prefix sweep.
def upload_key(user_id: str, upload_id: str, filename: str) -> str:
    ext = Path(filename).suffix.lower() or ".bin"
    return f"users/{user_id}/uploads/{upload_id}/original{ext}"


def clip_prefix(user_id: str, project_id: str, clip_id: str) -> str:
    return f"users/{user_id}/projects/{project_id}/clips/{clip_id}/"


def project_prefix(user_id: str, project_id: str) -> str:
    return f"users/{user_id}/projects/{project_id}/"
