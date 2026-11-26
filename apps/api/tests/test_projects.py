from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autoedit_schemas import Signals, parse_model

from .conftest import upload_file


def test_create_analyse_and_fetch_signals(client: TestClient, short_clip: Path) -> None:
    up = upload_file(client, short_clip)
    r = client.post("/v1/projects", json={"mode": "edit", "title": "Demo", "upload_ids": [up["id"]]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    project = client.get(f"/v1/projects/{pid}").json()
    assert project["status"] == "ready", project
    clip = project["clips"][0]
    assert clip["status"] == "ready" and clip["progress"] == 1.0
    assert project["meter"]["total_raw_s"] == pytest.approx(up["media"]["duration_s"])

    signals = parse_model(Signals, client.get(clip["signals_url"]).content)
    assert signals.clip_id == clip["id"]
    assert signals.dead_zones[0].reason == "black_frames"

    proxy = client.get(clip["proxy_url"])
    assert proxy.status_code == 200 and proxy.headers["content-type"] == "video/mp4"
    ranged = client.get(clip["proxy_url"], headers={"Range": "bytes=0-99"})
    assert ranged.status_code == 206 and len(ranged.content) == 100


def test_total_length_limit(client: TestClient, short_clip: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOEDIT_FREE_MAX_TOTAL_RAW_S", "10")
    a, b = upload_file(client, short_clip), upload_file(client, short_clip)
    pid = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [a["id"]]}).json()["id"]
    r = client.post(f"/v1/projects/{pid}/clips", json={"upload_id": b["id"]})
    assert r.status_code == 402
    err = r.json()["error"]
    assert err["code"] == "total_length_limit_exceeded"
    assert err["details"]["limit_s"] == 10 and err["details"]["upgrade_available"] is True
    assert len(client.get(f"/v1/projects/{pid}").json()["clips"]) == 1  # nothing partially added

    # All-or-nothing at creation too.
    r = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [a["id"], b["id"]]})
    assert r.status_code == 402


def test_paid_plan_has_room(client: TestClient, short_clip: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOEDIT_FREE_MAX_TOTAL_RAW_S", "10")
    from autoedit_api.db import User, session_scope

    db = session_scope()
    db.add(User(id="pro", plan="paid"))
    db.commit()
    db.close()
    ids = [upload_file(client, short_clip, user="pro")["id"] for _ in range(2)]
    r = client.post("/v1/projects", json={"mode": "edit", "upload_ids": ids}, headers={"X-User-Id": "pro"})
    assert r.status_code == 201
    assert r.json()["meter"]["limit_s"] == 1800
    assert client.get("/v1/me", headers={"X-User-Id": "pro"}).json()["limits"]["watermark"] is False


def test_reorder_remove_and_dedupe(client: TestClient, short_clip: Path) -> None:
    ups = [upload_file(client, short_clip) for _ in range(3)]
    pid = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [u["id"] for u in ups]}).json()[
        "id"
    ]
    clips = client.get(f"/v1/projects/{pid}").json()["clips"]
    ids = [c["id"] for c in clips]

    # Identical bytes: the 2nd and 3rd clip reuse the 1st clip's analysis instead of recomputing.
    from autoedit_api.db import UsageEvent, session_scope

    db = session_scope()
    assert db.query(UsageEvent).count() == 1
    db.close()
    assert all(c["status"] == "ready" for c in clips)

    bad = client.put(f"/v1/projects/{pid}/clips/order", json={"clip_ids": ids[:2]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_order"
    r = client.put(f"/v1/projects/{pid}/clips/order", json={"clip_ids": ids[::-1]})
    assert [c["id"] for c in r.json()["clips"]] == ids[::-1]
    assert [c["position"] for c in r.json()["clips"]] == [1, 2, 3]

    assert client.delete(f"/v1/projects/{pid}/clips/{ids[1]}").status_code == 204
    after = client.get(f"/v1/projects/{pid}").json()["clips"]
    assert [c["id"] for c in after] == [ids[2], ids[0]]
    assert [c["position"] for c in after] == [1, 2]

    # Insert at a position.
    extra = upload_file(client, short_clip)
    r = client.post(f"/v1/projects/{pid}/clips", json={"upload_id": extra["id"], "position": 1})
    assert r.status_code == 201 and r.json()["position"] == 1


def test_delete_project_removes_everything(client: TestClient, short_clip: Path, tmp_path: Path) -> None:
    up = upload_file(client, short_clip)
    pid = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]]}).json()["id"]
    storage_root = tmp_path / "data" / "storage"
    assert any(storage_root.rglob("signals.json"))
    assert client.delete(f"/v1/projects/{pid}").status_code == 204
    assert client.get(f"/v1/projects/{pid}").status_code == 404
    assert client.get(f"/v1/uploads/{up['id']}").status_code == 404
    assert not [p for p in storage_root.rglob("*") if p.is_file()]


def test_motion_mode_not_yet_available(client: TestClient) -> None:
    r = client.post("/v1/projects", json={"mode": "motion", "prompt": "Promo for my bakery"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "mode_not_available"


def test_monthly_project_quota(client: TestClient) -> None:
    for _ in range(3):
        assert client.post("/v1/projects", json={"mode": "edit"}).status_code == 201
    r = client.post("/v1/projects", json={"mode": "edit"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "project_quota_exceeded"
    listing = client.get("/v1/projects", params={"limit": 2}).json()
    assert len(listing["items"]) == 2 and listing["next_cursor"]
    rest = client.get("/v1/projects", params={"limit": 2, "cursor": listing["next_cursor"]}).json()
    assert len(rest["items"]) == 1


def test_projects_are_private(client: TestClient) -> None:
    pid = client.post("/v1/projects", json={"mode": "edit"}).json()["id"]
    assert client.get(f"/v1/projects/{pid}", headers={"X-User-Id": "intruder"}).status_code == 404
    assert client.delete(f"/v1/projects/{pid}", headers={"X-User-Id": "intruder"}).status_code == 404


def test_upload_must_be_complete(client: TestClient) -> None:
    uid = client.post("/v1/uploads", json={"filename": "a.mp4", "size_bytes": 100}).json()["id"]
    r = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [uid]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "upload_not_complete"


def test_events_stream_settles(client: TestClient, short_clip: Path) -> None:
    up = upload_file(client, short_clip)
    pid = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]]}).json()["id"]
    with client.stream("GET", f"/v1/projects/{pid}/events") as r:
        body = "".join(r.iter_text())
    assert "event: project" in body and "event: settled" in body
    assert '"status":"ready"' in body


def test_health(client: TestClient) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz").json()
    assert ready["ready"] is True
