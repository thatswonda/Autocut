from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


def _clear_caches() -> None:
    from autoedit_api import db, queue, settings, storage

    settings.get_settings.cache_clear()
    db.get_engine.cache_clear()
    db.get_sessionmaker.cache_clear()
    storage.get_storage.cache_clear()
    queue.redis_conn.cache_clear()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("AUTOEDIT_ENV", "test")
    monkeypatch.setenv("AUTOEDIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTOEDIT_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("AUTOEDIT_QUEUE_MODE", "inline")
    monkeypatch.setenv("AUTOEDIT_STORAGE_BACKEND", "local")
    _clear_caches()
    from autoedit_api.main import create_app

    with TestClient(create_app()) as c:
        yield c
    _clear_caches()


@pytest.fixture(scope="session")
def short_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """6 s 320x240 clip: 2 s black + 4 s moving pattern, with a tone. Fast to analyse."""
    dst = tmp_path_factory.mktemp("api-media") / "short.mp4"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=320x240:r=24:d=2",
         "-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=4",
         "-f", "lavfi", "-i", "sine=f=330:d=6",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]", "-map", "2:a",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(dst)],
        check=True,
    )  # fmt: skip
    return dst


def upload_file(
    client: TestClient, path: Path, chunk: int = 64 * 1024, user: str | None = None
) -> dict[str, Any]:
    """Drive the resumable protocol the way a client would."""
    headers = {"X-User-Id": user} if user else {}
    data = path.read_bytes()
    r = client.post("/v1/uploads", json={"filename": path.name, "size_bytes": len(data)}, headers=headers)
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    offset = 0
    while offset < len(data):
        part = data[offset : offset + chunk]
        r = client.patch(
            f"/v1/uploads/{uid}",
            content=part,
            headers={
                **headers,
                "Upload-Offset": str(offset),
                "Content-Type": "application/offset+octet-stream",
            },
        )
        assert r.status_code == 204, r.text
        offset = int(r.headers["Upload-Offset"])
    r = client.post(f"/v1/uploads/{uid}/complete", json={}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()
