"""Motion graphics and rendering through the HTTP API (fake Claude, real renderers)."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
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
    extra_ids = list(extra.pop("asset_ids", []))  # type: ignore[call-overload]
    body = {"mode": "motion", "title": "Kora promo",
            "prompt": "Kora Coffee: House Blend is ₦4,500 a bag. Order online.",
            "options": {"aspect": "9:16", "duration_s": 8, "style": "clean_minimal"},
            "asset_ids": [logo, photo, *extra_ids], "brand_authorized": True, **extra}  # fmt: skip
    r = client.post("/v1/projects", json=body)
    assert r.status_code == 201, r.text
    return client.get(f"/v1/projects/{r.json()['id']}").json()


def test_iphone_heic_photo_upload(client: TestClient) -> None:
    import io

    from PIL import Image
    from pillow_heif import from_pillow

    buf = io.BytesIO()
    from_pillow(Image.new("RGB", (640, 480), (30, 120, 200))).save(buf, format="HEIF")
    r = client.post(
        "/v1/assets", files={"file": ("IMG_0042.HEIC", buf.getvalue(), "image/heic")}, data={"kind": "photo"}
    )
    assert r.status_code == 201, r.text
    assert (r.json()["width"], r.json()["height"]) == (640, 480)
    assert client.get(r.json()["url"]).headers["content-type"] == "image/jpeg"


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
    r2 = client.post(f"/v1/projects/{p2['id']}/renders", json={"resolution": 720})
    assert r2.status_code == 201
    rnd2 = client.get(f"/v1/projects/{p2['id']}").json()["renders"][0]
    assert rnd2["status"] == "ready", rnd2
    assert rnd2["duration_s"] == pytest.approx(8.0, abs=0.3)  # 6 s video + 2 s outro
    assert (rnd2["width"], rnd2["height"]) == (720, 720)

    assert client.delete(f"/v1/templates/{tpl['id']}").status_code == 204
    assert client.get("/v1/templates").json() == []


@needs_browser
def test_motion_with_footage_voiceover_and_music(client: TestClient, tmp_path: Path) -> None:
    clip = tmp_path / "shop.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=4",
                    "-pix_fmt", "yuv420p", str(clip)], check=True)  # fmt: skip
    with clip.open("rb") as fh:
        v = client.post("/v1/assets", files={"file": ("shop.mp4", fh, "video/mp4")}, data={"kind": "video"})
    assert v.status_code == 201, v.text
    assert v.json()["url"].endswith("?poster=1") and v.json()["duration_s"] == pytest.approx(4.0, abs=0.1)
    assert client.get(v.json()["url"]).headers["content-type"] == "image/jpeg"
    music = tmp_path / "bed.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=262:d=2", "-c:a", "aac",
                    str(music)], check=True)  # fmt: skip
    with music.open("rb") as fh:
        m = client.post(
            "/v1/assets", files={"file": ("bed.m4a", fh, "audio/mp4")}, data={"kind": "music"}
        ).json()

    p = _motion_project(client, options={"aspect": "9:16", "duration_s": 8, "style": "bold_pop",
                                         "voiceover": True, "voiceover_captions": True,
                                         "music_asset_id": m["id"]})  # fmt: skip
    p = client.get(f"/v1/projects/{p['id']}").json()
    # The video joins the storyboard as a footage scene; it is not mistaken for a brand image.
    comp = p["motion"]["composition"]
    foot = [sc for sc in comp["scenes"] if sc["template"] == "footage"]
    assert p["motion"]["status"] == "ready", p["motion"]
    assert len(comp["voiceover"]["script"]) == 3 and comp["voiceover"]["enabled"]
    # The project was created with the image ids only; add the video the same way the app does.
    assert foot == [] or foot[0]["clip_id"] == v.json()["id"]

    p2 = _motion_project(client, options={"aspect": "9:16", "duration_s": 8, "style": "bold_pop",
                                          "voiceover": True, "voiceover_captions": True, "music_asset_id": m["id"]},
                         asset_ids=[v.json()["id"]])  # fmt: skip
    comp = client.get(f"/v1/projects/{p2['id']}").json()["motion"]["composition"]
    assert any(sc["template"] == "footage" for sc in comp["scenes"]), comp["scenes"]
    r = client.post(f"/v1/projects/{p2['id']}/renders", json={"resolution": 720})
    assert r.status_code == 201, r.text
    rnd = client.get(f"/v1/projects/{p2['id']}").json()["renders"][0]
    assert rnd["status"] == "ready", rnd
    assert rnd["duration_s"] == pytest.approx(8.0, abs=0.3)
    out = tmp_path / "out.mp4"
    out.write_bytes(client.get(rnd["video_url"]).content)
    vol = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr  # fmt: skip
    mean = float(vol.split("mean_volume:")[1].split("dB")[0])
    assert mean > -35, vol[-300:]  # voice and music are audible, not the old silent track


def test_change_storyboard_by_prompt_add_and_remove_photos(client: TestClient) -> None:
    p = _motion_project(client)
    pid = p["id"]
    photo = next(a["id"] for a in p["motion"]["assets"] if a["kind"] == "photo")
    new_photo = _image(client, "photo", (20, 160, 90))

    r = client.post(f"/v1/projects/{pid}/revise", json={"message": "Make it 12 seconds and punchier",
                                                       "asset_ids": [new_photo]})  # fmt: skip
    assert r.status_code == 202, r.text
    m = client.get(f"/v1/projects/{pid}").json()["motion"]
    assert m["pending_kind"] is None and m["status"] == "ready"
    comp = m["composition"]
    assert comp["meta"]["duration_s"] == 12 and comp["scenes"][-1]["t_out"] == 12
    assert comp["scenes"][0]["content"]["headline"] == "Revised hook"
    assert (
        m["revisions"][-1]["message"] == "Make it 12 seconds and punchier"
        and "hook" in m["revisions"][-1]["reply"]
    )
    assert new_photo in [a["id"] for a in m["assets"]]  # the attached photo joined the project
    assert client.get(f"/v1/projects/{pid}").json()["options"]["duration_s"] == 12

    # Remove a photo a scene uses: the scene falls back to another image or to text.
    used = [sc for sc in comp["scenes"] if photo in sc["content"].get("assets", [])]
    assert used
    d = client.delete(f"/v1/projects/{pid}/assets/{photo}")
    assert d.status_code == 200, d.text
    m = client.get(f"/v1/projects/{pid}").json()["motion"]
    assert photo not in [a["id"] for a in m["assets"]]
    assert all(photo not in sc["content"].get("assets", []) for sc in m["composition"]["scenes"])

    # Claude can take photos out of the project when asked.
    r = client.post(f"/v1/projects/{pid}/revise", json={"message": f"remove {new_photo} please"})
    assert r.status_code == 202
    m = client.get(f"/v1/projects/{pid}").json()["motion"]
    assert new_photo not in [a["id"] for a in m["assets"]]

    r = client.post(
        f"/v1/projects/{pid}/revise", json={"message": "Make the voiceover sound Nigerian and warm"}
    )
    assert r.status_code == 202
    assert (
        client.get(f"/v1/projects/{pid}").json()["options"]["voice_style"] == "warm Nigerian English accent"
    )

    assert client.post(f"/v1/projects/{pid}/revise", json={"message": ""}).status_code == 422
    assert client.post(f"/v1/projects/{pid}/more-photos").status_code == 409  # no website links here


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
    assert rnd["duration_s"] == pytest.approx(4.8, abs=0.5)  # the stand-in plan keeps the middle 80%
    assert report["planning"]["source"] == "ai"
    assert report["plan"]["color"]["preset"] == "teal_orange"  # the user's chosen grade wins
    assert project["status"] == "done"


def test_edit_without_ai_uses_rules_and_prompt_look(client: TestClient, short_clip: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:  # fmt: skip
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("AUTOEDIT_FAKE_AI", raising=False)
    up = upload_file(client, short_clip)
    pid = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]], "prompt": "dark moody look",
                                             "options": {"aspect": "9:16"}}).json()["id"]  # fmt: skip
    assert client.post(f"/v1/projects/{pid}/renders", json={}).status_code == 201
    rnd = client.get(f"/v1/projects/{pid}").json()["renders"][0]
    assert rnd["status"] == "ready", rnd
    planning = rnd["report"]["planning"]
    assert planning["source"] == "rules" and "AI editing is off" in planning["note"]
    assert rnd["report"]["color"]["preset"] == "moody_film"  # look taken from the prompt


def test_rerender_with_edited_plan_and_music(client: TestClient, short_clip: Path, tmp_path: Path) -> None:
    up = upload_file(client, short_clip)
    pid = client.post("/v1/projects", json={"mode": "edit", "upload_ids": [up["id"]]}).json()["id"]
    assert client.post(f"/v1/projects/{pid}/renders", json={}).status_code == 201
    first = client.get(f"/v1/projects/{pid}").json()["renders"][0]
    plan = first["report"]["plan"]
    seg = plan["segments"][0]
    plan["segments"] = [{**seg, "src_in": 2.5, "src_out": 4.5, "reason": "Only the best bit"}]
    plan["color"]["preset"] = "black_white"

    music = tmp_path / "bed.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=3", "-c:a", "aac",
                    str(music)], check=True)  # fmt: skip
    with music.open("rb") as fh:
        m = client.post("/v1/assets", files={"file": ("bed.m4a", fh, "audio/mp4")}, data={"kind": "music"})
    assert m.status_code == 201, m.text
    with music.open("rb") as fh:
        bad = client.post("/v1/assets", files={"file": ("bed.exe", fh, "application/octet-stream")},
                          data={"kind": "music"})  # fmt: skip
    assert bad.status_code == 415

    r = client.post(f"/v1/projects/{pid}/renders", json={"plan": plan, "music_asset_id": m.json()["id"]})
    assert r.status_code == 201, r.text
    latest = client.get(f"/v1/projects/{pid}").json()["renders"][0]
    assert latest["status"] == "ready", latest
    assert latest["report"]["planning"]["source"] == "user"
    assert latest["report"]["color"]["preset"] == "black_white"
    assert latest["duration_s"] == pytest.approx(2.0, abs=0.3)
    assert latest["report"]["audio"]["music"]["enabled"] is True
    assert client.post(f"/v1/projects/{pid}/renders", json={"plan": {"segments": []}}).status_code == 422


def test_catalog(client: TestClient) -> None:
    cat = client.get("/v1/catalog").json()
    assert {p["key"] for p in cat["color_presets"]} >= {"teal_orange", "warm_cinematic", "black_white"}
    assert {s["key"] for s in cat["motion_styles"]} >= {"bold_pop", "luxury", "editorial"}
    assert cat["ai_enabled"] is True


def test_links_in_change_requests() -> None:
    from autoedit_api.jobs_media import _MORE_PHOTOS_RE, _links_in

    msg = "Use photos from https://shop.example.com/products and bsbmarket.com/about, then drop the car."
    assert _links_in(msg) == ["https://shop.example.com/products", "https://bsbmarket.com/about"]
    assert _links_in("make it 30 seconds") == []
    assert _MORE_PHOTOS_RE.search("can you get more pictures of the shoes")
    assert not _MORE_PHOTOS_RE.search("remove the photos of cars")
