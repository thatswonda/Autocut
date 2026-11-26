"""Motion graphics and rendering through the HTTP API (fake Claude, real renderers)."""

from __future__ import annotations

import io
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from .conftest import upload_file

pytestmark = pytest.mark.slow
CHROMIUM = (
    os.environ.get("AUTOEDIT_CHROMIUM_PATH")
    or shutil.which("chromium")
    or shutil.which("chrome-headless-shell")
)
needs_browser = pytest.mark.skipif(not CHROMIUM and not os.environ.get("CI"), reason="no Chromium available")


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    import autoedit_motion.storyboard as sb
    from autoedit_motion.testing import FakeClaude

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setattr(sb, "CLIENT_FACTORY", FakeClaude)


def _image(client: TestClient, kind: str, color: tuple[int, int, int]) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (600, 400), color).save(buf, "PNG")
    r = client.post(
        "/v1/assets", files={"file": (f"{kind}.png", buf.getvalue(), "image/png")}, data={"kind": kind}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _motion_project(client: TestClient, **extra: object) -> dict:
    logo, photo = _image(client, "logo", (232, 93, 4)), _image(client, "photo", (90, 60, 40))
    body = {"mode": "motion", "title": "Kora promo",
            "prompt": "Kora Coffee: House Blend is ₦4,500 a bag. Order online.",
            "options": {"aspect": "9:16", "duration_s": 8, "style": "clean_minimal"},
            "asset_ids": [logo, photo], "brand_authorized": True, **extra}  # fmt: skip
    r = client.post("/v1/projects", json=body)
    assert r.status_code == 201, r.text
    return client.get(f"/v1/projects/{r.json()['id']}").json()


def test_motion_requires_authorization(client: TestClient) -> None:
    r = client.post("/v1/projects", json={"mode": "motion", "prompt": "A bakery promo"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "brand_authorization_required"


def test_motion_without_api_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    p = _motion_project(client)
    assert p["motion"]["status"] == "failed" and p["motion"]["error_code"] == "ai_unavailable"
    assert client.get("/v1/me").json()["ai_enabled"] is False


def test_storyboard_edit_and_regenerate(client: TestClient) -> None:
    p = _motion_project(client)
    m = p["motion"]
    assert m["status"] == "ready", m
    assert p["status"] == "review"
    comp = m["composition"]
    assert comp["meta"]["style"] == "clean_minimal" and comp["brand"]["logo_asset"]
    assert m["cost_usd"] > 0
    assert {a["kind"] for a in m["assets"]} == {"logo", "photo"}
    assert client.get(m["assets"][0]["url"]).status_code == 200

    comp["scenes"][0]["content"]["headline"] = "Edited by the user"
    r = client.put(f"/v1/projects/{p['id']}/composition", json=comp)
    assert r.status_code == 200 and r.json()["version"] == 2
    comp["scenes"][0]["content"]["assets"] = ["ast_missing"]
    assert client.put(f"/v1/projects/{p['id']}/composition", json=comp).status_code == 422

    r = client.post(f"/v1/projects/{p['id']}/scenes/s1/regenerate", json={"note": "punchier"})
    assert r.status_code == 202
    m2 = client.get(f"/v1/projects/{p['id']}").json()["motion"]
    assert m2["composition"]["scenes"][0]["content"]["headline"] == "Punchier hook, same facts"
    assert m2["pending_scene"] is None

    r = client.put(f"/v1/projects/{p['id']}/brand-kit", json={"colors": {"primary": "#112233"}})
    assert r.status_code == 200
    m3 = client.get(f"/v1/projects/{p['id']}").json()["motion"]
    assert m3["composition"]["brand"]["colors"]["primary"] == "#112233"


@needs_browser
def test_motion_render_template_and_reuse(client: TestClient) -> None:
    p = _motion_project(client)
    r = client.post(f"/v1/projects/{p['id']}/renders", json={"resolution": 720})
    assert r.status_code == 201, r.text
    project = client.get(f"/v1/projects/{p['id']}").json()
    rnd = project["renders"][0]
    assert rnd["status"] == "ready", rnd
    assert project["status"] == "done"
    assert rnd["width"] == 720 and rnd["height"] == 1280
    video = client.get(rnd["video_url"])
    assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
    dl = client.get(rnd["download_url"])
    assert "attachment" in dl.headers["content-disposition"]
    assert client.get(rnd["poster_url"]).status_code == 200

    # Save the last 2 seconds as an outro template and reuse it.
    t = client.post("/v1/templates", json={"render_id": rnd["id"], "t_in": 6, "t_out": 8, "name": "Kora outro",
                                           "kind": "outro"})  # fmt: skip
    assert t.status_code == 201, t.text
    tpl = t.json()
    assert tpl["duration_s"] == pytest.approx(2.0, abs=0.1)
    assert [x["id"] for x in client.get("/v1/templates").json()] == [tpl["id"]]

    p2 = _motion_project(client, options={"aspect": "1:1", "duration_s": 6, "style": "luxury",
                                          "outro_template_id": tpl["id"]})  # fmt: skip
    r2 = client.post(f"/v1/projects/{p2['id']}/renders", json={})
    assert r2.status_code == 201
    rnd2 = client.get(f"/v1/projects/{p2['id']}").json()["renders"][0]
    assert rnd2["status"] == "ready", rnd2
    assert rnd2["duration_s"] == pytest.approx(8.0, abs=0.3)  # 6 s video + 2 s outro
    assert (rnd2["width"], rnd2["height"]) == (720, 720)

    assert client.delete(f"/v1/templates/{tpl['id']}").status_code == 204
    assert client.get("/v1/templates").json() == []


def test_edit_render_with_color_grade(client: TestClient, short_clip: Path) -> None:
    up = upload_file(client, short_clip)
    pid = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]],
                                             "options": {"aspect": "1:1", "color_preset": "teal_orange"}}).json()["id"]  # fmt: skip
    assert client.get(f"/v1/projects/{pid}").json()["status"] == "ready"
    bad = client.post(f"/v1/projects/{pid}/renders", json={"color_preset": "sparkles"})
    assert bad.status_code == 422
    r = client.post(f"/v1/projects/{pid}/renders", json={"resolution": 720})
    assert r.status_code == 201, r.text
    project = client.get(f"/v1/projects/{pid}").json()
    rnd = project["renders"][0]
    assert rnd["status"] == "ready", rnd
    assert rnd["settings"]["color_preset"] == "teal_orange"
    assert (rnd["width"], rnd["height"]) == (720, 720)
    report = rnd["report"]
    assert any(x["reason"] == "Black frames" for x in report["removed"])  # the 2 s black intro was cut
    assert rnd["duration_s"] == pytest.approx(4.0, abs=0.5)
    assert project["status"] == "done"


def test_catalog(client: TestClient) -> None:
    cat = client.get("/v1/catalog").json()
    assert {p["key"] for p in cat["color_presets"]} >= {"teal_orange", "warm_cinematic", "black_white"}
    assert {s["key"] for s in cat["motion_styles"]} >= {"bold_pop", "luxury", "editorial"}
    assert cat["ai_enabled"] is True
