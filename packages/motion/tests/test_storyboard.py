from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from autoedit_motion.storyboard import (
    ImageInput,
    StoryboardError,
    StoryboardRequest,
    Usage,
    check_composition,
    generate_composition,
    regenerate_scene,
)
from autoedit_motion.testing import FakeClaude

BRAND = {"name": "Kora", "colors": {"primary": "#E85D04"}, "fonts": {"heading": "", "body": ""}, "tone": ""}


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    p = tmp_path / "p.png"
    Image.new("RGB", (400, 300), (200, 120, 40)).save(p)
    return p


def _req(photo: Path) -> StoryboardRequest:
    return StoryboardRequest(
        brief="Kora Coffee sells House Blend for ₦4,500. Free delivery in Lagos.",
        duration_s=20,
        images=[ImageInput("ast_photo", "photo", photo, "file:p.png")],
    )


def test_generates_valid_composition(photo: Path) -> None:
    usage = Usage()
    comp = generate_composition(_req(photo), BRAND, client=FakeClaude(), usage=usage)
    assert comp.meta.duration_s == 20 and comp.meta.style == "bold_pop"
    assert comp.scenes[-1].template == "cta_end_card"
    assert comp.scenes[1].content.assets == ["ast_photo"]
    assert comp.brand.name == "Kora"
    assert usage.calls == 1 and usage.cost_usd == pytest.approx(3000 / 1e6 * 4 + 1500 / 1e6 * 20)


def test_invented_price_is_rejected_then_fixed(photo: Path) -> None:
    fake = FakeClaude(invent_price_first=True)
    comp = generate_composition(_req(photo), BRAND, client=fake)
    assert len(fake.calls) == 2
    feedback = fake.calls[1]["messages"][-1]["content"]
    assert "9,999" in feedback and "does not appear in the sources" in feedback
    assert any(sc.content.price == "₦4,500" for sc in comp.scenes)


def test_unsourced_testimonial_and_missing_asset_flagged(photo: Path) -> None:
    comp = generate_composition(_req(photo), BRAND, client=FakeClaude())
    data = comp.model_dump(by_alias=True)
    data["scenes"][0]["template"] = "testimonial_card"
    data["scenes"][0]["content"]["headline"] = "Best coffee ever, ten out of ten"
    data["scenes"][1]["content"]["assets"] = ["ast_nope"]
    from autoedit_schemas import Composition, parse_model

    problems = check_composition(parse_model(Composition, data), _req(photo))
    assert any("verbatim" in p for p in problems)
    assert any("ast_nope" in p for p in problems)


def test_regenerate_scene_keeps_timing(photo: Path) -> None:
    comp = generate_composition(_req(photo), BRAND, client=FakeClaude())
    s1 = comp.scenes[0]
    new = regenerate_scene(comp, "s1", "make it punchier", _req(photo), client=FakeClaude())
    n1 = next(s for s in new.scenes if s.id == "s1")
    assert n1.content.headline == "Punchier hook, same facts"
    assert (n1.t_in, n1.t_out) == (s1.t_in, s1.t_out)


def test_cost_ceiling(photo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOEDIT_MAX_JOB_COST_USD", "0.001")
    with pytest.raises(StoryboardError) as exc:
        generate_composition(_req(photo), BRAND, client=FakeClaude())
    assert exc.value.code == "cost_ceiling"


def test_pasted_key_is_cleaned_and_bad_keys_are_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    from autoedit_motion.storyboard import api_key, key_problem

    monkeypatch.setenv("ANTHROPIC_API_KEY", '  "sk-ant-api03-abc"\n')
    assert api_key() == "sk-ant-api03-abc" and key_problem() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-proj-123")
    assert "start with sk-ant-" in (key_problem() or "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03 abc")
    assert "one line" in (key_problem() or "")


def test_auto_script_and_fit_to_voice() -> None:
    from autoedit_motion.narration import auto_script, fit_to_voice
    from autoedit_schemas import Composition

    comp = Composition.model_validate({"meta": {"duration_s": 6}, "scenes": [
        {"id": "s1", "template": "kinetic_text", "t_in": 0, "t_out": 2, "content": {"headline": "Fresh coffee daily"}},
        {"id": "s2", "template": "price_offer", "t_in": 2, "t_out": 4, "content": {"headline": "House Blend", "price": "₦4,500"}},
        {"id": "s3", "template": "cta_end_card", "t_in": 4, "t_out": 6, "content": {"headline": "Order today", "cta": "Shop now"}},
    ]})  # fmt: skip
    script = auto_script(comp)
    assert [ln.text for ln in script] == [
        "Fresh coffee daily.",
        "House Blend. Just 4,500 naira.",
        "Order today. Shop now.",
    ]
    fitted, starts = fit_to_voice(comp, [(ln.t_in, d) for ln, d in zip(script, (1.0, 3.5, 1.2), strict=True)])
    s2 = fitted.scenes[1]
    assert s2.t_out - s2.t_in >= 3.5 + 0.35 + 0.45 - 1e-6  # stretched to hold its 3.5 s line
    assert fitted.scenes[0].t_out - fitted.scenes[0].t_in == 2  # never shrinks
    assert starts[1] == round(s2.t_in + 0.35, 3) and fitted.meta.duration_s == fitted.scenes[-1].t_out
