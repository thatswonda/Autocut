"""Against live Postgres + Redis (CI services, or the local docker compose stack).

Run with:
    AUTOEDIT_IT_DATABASE_URL=postgresql+psycopg://... AUTOEDIT_IT_REDIS_URL=redis://... pytest -m integration
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

DB = os.environ.get("AUTOEDIT_IT_DATABASE_URL")
REDIS = os.environ.get("AUTOEDIT_IT_REDIS_URL")


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    if not (DB and REDIS):
        pytest.skip("set AUTOEDIT_IT_DATABASE_URL and AUTOEDIT_IT_REDIS_URL")
    from .conftest import _clear_caches

    monkeypatch.setenv("AUTOEDIT_DATABASE_URL", DB)
    monkeypatch.setenv("AUTOEDIT_REDIS_URL", REDIS)
    monkeypatch.setenv("AUTOEDIT_QUEUE_MODE", "rq")
    monkeypatch.setenv("AUTOEDIT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUTOEDIT_STORAGE_BACKEND", "local")
    _clear_caches()
    from autoedit_api.db import Base, get_engine
    from autoedit_api.queue import redis_conn

    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    redis_conn().flushdb()
    from autoedit_api.main import create_app

    with TestClient(create_app()) as c:
        yield c
    _clear_caches()


def test_worker_processes_queue(live: TestClient, short_clip: Path) -> None:
    from rq import Queue, SimpleWorker

    from autoedit_api.queue import ANALYSIS_QUEUE, redis_conn

    from .conftest import upload_file

    up = upload_file(live, short_clip)
    pid = live.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]]}).json()["id"]
    assert live.get(f"/v1/projects/{pid}").json()["status"] == "analyzing"
    queue = Queue(ANALYSIS_QUEUE, connection=redis_conn())
    assert queue.count == 1
    SimpleWorker([queue], connection=redis_conn()).work(burst=True)
    project = live.get(f"/v1/projects/{pid}").json()
    assert project["status"] == "ready", project


def test_limit_is_race_safe(live: TestClient, short_clip: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two concurrent adds that each fit alone but not together: Postgres row lock admits exactly one."""
    monkeypatch.setenv("AUTOEDIT_FREE_MAX_TOTAL_RAW_S", "15")
    monkeypatch.setenv("AUTOEDIT_FREE_PROJECTS_PER_MONTH", "100")
    from .conftest import upload_file

    ups = [upload_file(live, short_clip)["id"] for _ in range(3)]
    for _round in range(
        6
    ):  # the race is timing dependent; several rounds make a regression near-certain to fail
        pid = live.post("/v1/projects", json={"mode": "edit", "upload_ids": [ups[0]]}).json()["id"]
        codes: list[int] = []
        barrier = threading.Barrier(2)

        def add(
            upload_id: str, pid: str = pid, codes: list[int] = codes, barrier: threading.Barrier = barrier
        ) -> None:
            barrier.wait()
            codes.append(live.post(f"/v1/projects/{pid}/clips", json={"upload_id": upload_id}).status_code)

        threads = [threading.Thread(target=add, args=(u,)) for u in ups[1:]]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(codes) == [201, 402]
        assert len(live.get(f"/v1/projects/{pid}").json()["clips"]) == 2
