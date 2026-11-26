"""Single-server deployment features: password gate, per-clip child processes, crash recovery."""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .conftest import _clear_caches, upload_file


def _basic(pw: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"me:{pw}".encode()).decode()}


@pytest.fixture
def make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    clients: list[TestClient] = []

    def make(**env: str) -> TestClient:
        monkeypatch.setenv("AUTOEDIT_ENV", "test")
        monkeypatch.setenv("AUTOEDIT_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("AUTOEDIT_DATABASE_URL", f"sqlite:///{tmp_path / 'deploy.db'}")
        monkeypatch.setenv("AUTOEDIT_STORAGE_BACKEND", "local")
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        _clear_caches()
        from autoedit_api.main import create_app

        c = TestClient(create_app())
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)
    _clear_caches()


def test_password_gate(make_client) -> None:  # type: ignore[no-untyped-def]
    c = make_client(AUTOEDIT_QUEUE_MODE="inline", AUTOEDIT_ACCESS_PASSWORD="s3cret")
    assert c.get("/healthz").status_code == 200  # the host's health check needs no password
    r = c.get("/")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert c.get("/v1/me").status_code == 401
    assert c.get("/v1/me", headers=_basic("wrong")).status_code == 401
    assert c.get("/v1/me", headers=_basic("s3cret")).status_code == 200
    assert c.get("/", headers=_basic("s3cret")).status_code == 200


def _wait_ready(c: TestClient, pid: str, timeout: float = 120) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        p = c.get(f"/v1/projects/{pid}").json()
        if p["status"] != "analyzing":
            return p
        time.sleep(0.5)
    raise AssertionError("analysis did not finish")


@pytest.mark.slow
def test_process_mode_runs_each_clip_in_a_child(make_client, short_clip: Path) -> None:  # type: ignore[no-untyped-def]
    c = make_client(AUTOEDIT_QUEUE_MODE="process", AUTOEDIT_SIGNAL_WORKERS="1")
    ups = [upload_file(c, short_clip)["id"], upload_file(c, short_clip)["id"]]
    pid = c.post("/v1/projects", json={"mode": "edit", "upload_ids": ups}).json()["id"]
    p = _wait_ready(c, pid)
    assert p["status"] == "ready", p
    assert all(cl["signals_url"] for cl in p["clips"])


def test_crashed_child_is_reported(make_client, short_clip: Path) -> None:  # type: ignore[no-untyped-def]
    c = make_client(AUTOEDIT_QUEUE_MODE="inline")
    from autoedit_api.db import ProjectClip, session_scope
    from autoedit_api.jobs import mark_crashed, unfinished_clip_ids

    up = upload_file(c, short_clip)
    import autoedit_api.queue as q

    q_mode = q.enqueue_clip_analysis
    q.enqueue_clip_analysis = lambda _cid: None  # type: ignore[assignment]
    try:
        import autoedit_api.routes.projects as routes

        routes.enqueue_clip_analysis = lambda _cid: None  # type: ignore[assignment]
        pid = c.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]]}).json()["id"]
    finally:
        q.enqueue_clip_analysis = q_mode  # type: ignore[assignment]
        routes.enqueue_clip_analysis = q_mode  # type: ignore[assignment]
    clip_id = c.get(f"/v1/projects/{pid}").json()["clips"][0]["id"]

    # A restart finds the queued clip and hands it back for analysis.
    assert unfinished_clip_ids() == [clip_id]

    mark_crashed(clip_id, -9)
    db = session_scope()
    clip = db.get(ProjectClip, clip_id)
    assert clip is not None and clip.status == "failed" and clip.error_code == "out_of_memory"
    db.close()
    assert unfinished_clip_ids() == []


def test_job_that_keeps_killing_the_server_is_not_resumed_forever(make_client, short_clip: Path) -> None:  # type: ignore[no-untyped-def]
    c = make_client(AUTOEDIT_QUEUE_MODE="inline")
    import autoedit_api.queue as q
    import autoedit_api.routes.projects as routes
    from autoedit_api.db import ProjectClip, session_scope

    up = upload_file(c, short_clip)
    real = routes.enqueue_clip_analysis
    routes.enqueue_clip_analysis = lambda _cid: None  # type: ignore[assignment]
    try:
        pid = c.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]]}).json()["id"]
    finally:
        routes.enqueue_clip_analysis = real  # type: ignore[assignment]
    clip_id = c.get(f"/v1/projects/{pid}").json()["clips"][0]["id"]

    started: list[str] = []
    real_enqueue = q.enqueue
    q.enqueue = lambda kind, obj_id: started.append(obj_id)  # type: ignore[assignment]
    try:
        # Two restarts in a row resume the job; each time it "takes the server down" (no clean exit).
        assert q.resume("analyze", clip_id) and q.resume("analyze", clip_id)
        # The third restart gives up and records a clear failure instead of looping.
        assert not q.resume("analyze", clip_id)
    finally:
        q.enqueue = real_enqueue  # type: ignore[assignment]
    assert started == [clip_id, clip_id]
    db = session_scope()
    clip = db.get(ProjectClip, clip_id)
    assert clip is not None and clip.status == "failed" and clip.error_code == "server_restarted"
    assert "restarted more than once" in (clip.error or "")
    db.close()
