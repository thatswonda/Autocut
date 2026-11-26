from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from .conftest import upload_file


def test_resumable_upload_round_trip(client: TestClient, short_clip: Path) -> None:
    up = upload_file(client, short_clip, chunk=50_000)
    assert up["status"] == "complete"
    assert up["offset"] == short_clip.stat().st_size
    assert up["media"]["duration_s"] > 5.5
    assert up["media"]["has_audio"] is True


def test_resume_after_interruption(client: TestClient, short_clip: Path) -> None:
    data = short_clip.read_bytes()
    uid = client.post("/v1/uploads", json={"filename": "a.mp4", "size_bytes": len(data)}).json()["id"]
    assert (
        client.patch(f"/v1/uploads/{uid}", content=data[:1000], headers={"Upload-Offset": "0"}).status_code
        == 204
    )

    # A client that lost track sends a stale offset and is told where to resume.
    r = client.patch(f"/v1/uploads/{uid}", content=data[:1000], headers={"Upload-Offset": "0"})
    assert r.status_code == 409
    assert r.json()["error"]["details"]["offset"] == 1000
    assert client.head(f"/v1/uploads/{uid}").headers["Upload-Offset"] == "1000"

    r = client.patch(f"/v1/uploads/{uid}", content=data[1000:], headers={"Upload-Offset": "1000"})
    assert r.status_code == 204
    sha = hashlib.sha256(data).hexdigest()
    r = client.post(f"/v1/uploads/{uid}/complete", json={"sha256": sha})
    assert r.status_code == 200 and r.json()["status"] == "complete"
    # Completing twice is idempotent.
    assert client.post(f"/v1/uploads/{uid}/complete", json={}).status_code == 200


def test_incomplete_and_oversize(client: TestClient) -> None:
    uid = client.post("/v1/uploads", json={"filename": "a.mov", "size_bytes": 10}).json()["id"]
    r = client.post(f"/v1/uploads/{uid}/complete", json={})
    assert r.status_code == 409 and r.json()["error"]["code"] == "upload_incomplete"
    r = client.patch(f"/v1/uploads/{uid}", content=b"x" * 11, headers={"Upload-Offset": "0"})
    assert r.status_code == 413
    assert client.head(f"/v1/uploads/{uid}").headers["Upload-Offset"] == "0"


def test_rejects_bad_files(client: TestClient) -> None:
    r = client.post("/v1/uploads", json={"filename": "notes.txt", "size_bytes": 10})
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_format"

    junk = b"definitely not a video" * 50
    uid = client.post("/v1/uploads", json={"filename": "fake.mp4", "size_bytes": len(junk)}).json()["id"]
    client.patch(f"/v1/uploads/{uid}", content=junk, headers={"Upload-Offset": "0"})
    r = client.post(f"/v1/uploads/{uid}/complete", json={})
    assert r.status_code == 422
    assert client.get(f"/v1/uploads/{uid}").json()["status"] == "failed"


def test_checksum_mismatch(client: TestClient, short_clip: Path) -> None:
    data = short_clip.read_bytes()
    uid = client.post("/v1/uploads", json={"filename": "a.mp4", "size_bytes": len(data)}).json()["id"]
    client.patch(f"/v1/uploads/{uid}", content=data, headers={"Upload-Offset": "0"})
    r = client.post(f"/v1/uploads/{uid}/complete", json={"sha256": "0" * 64})
    assert r.status_code == 422 and r.json()["error"]["code"] == "checksum_mismatch"


def test_uploads_are_private(client: TestClient, short_clip: Path) -> None:
    up = upload_file(client, short_clip)
    r = client.get(f"/v1/uploads/{up['id']}", headers={"X-User-Id": "someone_else"})
    assert r.status_code == 404
