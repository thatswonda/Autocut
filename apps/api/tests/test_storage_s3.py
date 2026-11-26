"""S3 backend (Cloudflare R2 / MinIO / AWS) against an in-process S3 mock."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

moto = pytest.importorskip("moto")


@pytest.fixture
def s3_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from .conftest import _clear_caches

    monkeypatch.setenv("AUTOEDIT_ENV", "test")
    monkeypatch.setenv("AUTOEDIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTOEDIT_DATABASE_URL", f"sqlite:///{tmp_path / 's3.db'}")
    monkeypatch.setenv("AUTOEDIT_QUEUE_MODE", "inline")
    monkeypatch.setenv("AUTOEDIT_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("AUTOEDIT_S3_BUCKET", "autoedit-test")
    monkeypatch.setenv("AUTOEDIT_S3_REGION", "us-east-1")
    monkeypatch.setenv("AUTOEDIT_S3_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AUTOEDIT_S3_SECRET_ACCESS_KEY", "test")
    with moto.mock_aws():
        _clear_caches()
        from autoedit_api.main import create_app
        from autoedit_api.storage import S3Storage, get_storage

        storage = get_storage()
        assert isinstance(storage, S3Storage)
        storage.ensure_bucket()
        with TestClient(create_app()) as c:
            yield c
        _clear_caches()


def test_full_flow_on_s3(s3_client: TestClient, short_clip: Path) -> None:
    from autoedit_api.storage import get_storage

    from .conftest import upload_file

    up = upload_file(s3_client, short_clip)
    pid = s3_client.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]]}).json()["id"]
    clip = s3_client.get(f"/v1/projects/{pid}").json()["clips"][0]
    assert clip["status"] == "ready", clip

    storage = get_storage()
    keys = {o["Key"] for o in storage.client.list_objects_v2(Bucket="autoedit-test")["Contents"]}  # type: ignore[attr-defined]
    assert any(k.endswith("/proxy.mp4") for k in keys)
    assert any(k.endswith("/signals.json") for k in keys)
    head = storage.client.head_object(Bucket="autoedit-test", Key=next(iter(keys)))  # type: ignore[attr-defined]
    assert head["ServerSideEncryption"] == "AES256"  # encrypted at rest

    assert s3_client.get(clip["signals_url"]).json()["clip_id"] == clip["id"]
    proxy = s3_client.get(clip["proxy_url"], follow_redirects=False)
    assert proxy.status_code == 307 and "Signature" in proxy.headers["location"]

    assert s3_client.delete(f"/v1/projects/{pid}").status_code == 204
    assert "Contents" not in storage.client.list_objects_v2(Bucket="autoedit-test")  # type: ignore[attr-defined]
