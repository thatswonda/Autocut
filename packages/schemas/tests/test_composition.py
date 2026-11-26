from __future__ import annotations

import copy
from typing import Any

import pytest

from autoedit_schemas import Composition, SchemaValidationError, parse_model

BASE: dict[str, Any] = {
    "version": 1,
    "meta": {"title": "Promo", "aspect": "9:16", "duration_s": 6, "fps": 30, "language": "en"},
    "brand": {"name": "Acme", "colors": {"primary": "#112233"}},
    "scenes": [
        {
            "id": "sc1",
            "template": "kinetic_text",
            "t_in": 0.0,
            "t_out": 3.0,
            "content": {"headline": "Fresh bread daily"},
            "claims": [{"text": "Baked every morning", "source": "website:/about"}],
            "animation": {"in": "slide_up", "out": "fade", "easing": "ease_out"},
            "transition_out": {"type": "wipe", "duration_s": 0.4},
            "reason": "Hook",
        },
        {"id": "sc2", "template": "cta_end_card", "t_in": 2.6, "t_out": 6.0, "reason": "CTA"},
    ],
}


def test_valid_composition() -> None:
    comp = parse_model(Composition, BASE)
    assert comp.scenes[0].animation.in_ == "slide_up"
    assert comp.model_dump(by_alias=True)["scenes"][0]["animation"]["in"] == "slide_up"


@pytest.mark.parametrize(
    ("patch", "needle"),
    [
        (lambda d: d["scenes"][0]["claims"][0].update(source=""), "claims.0.source"),
        (lambda d: d["brand"]["colors"].update(primary="red"), "colors.primary"),
        (lambda d: d["scenes"][1].update(t_in=3.5), "gap"),
        (lambda d: d["scenes"][1].update(t_in=1.0), "overlap"),
        (lambda d: d["meta"].update(duration_s=10), "meta.duration_s"),
        (lambda d: d["scenes"][1].update(template="footage"), "footage scenes require"),
        (lambda d: d["scenes"][1].update(template="hologram"), "template"),
    ],
)
def test_invalid(patch: Any, needle: str) -> None:
    data = copy.deepcopy(BASE)
    patch(data)
    with pytest.raises(SchemaValidationError) as exc:
        parse_model(Composition, data)
    assert needle in exc.value.feedback
