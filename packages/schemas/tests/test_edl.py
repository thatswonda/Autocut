from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from autoedit_schemas import EDL, SchemaValidationError, json_schemas, lint_edl, parse_model

PRD_EXAMPLE: dict[str, Any] = {
    "version": 1,
    "meta": {
        "title": "",
        "summary": "",
        "aspect": "9:16",
        "total_raw_duration_s": 240,
        "target_duration_s": 45,
        "style": "energetic",
    },
    "clips": [
        {"id": "c1", "order": 1, "duration_s": 120.0},
        {"id": "c2", "order": 2, "duration_s": 120.0},
    ],
    "segments": [
        {"id": "s1", "clip_id": "c1", "src_in": 12.40, "src_out": 18.90, "speed": 1.0, "reason": "Hook"},
        {"id": "s2", "clip_id": "c2", "src_in": 3.0, "src_out": 41.5, "speed": 1.0, "reason": "Body"},
    ],
    "removed": [{"clip_id": "c1", "src_in": 0.0, "src_out": 12.4, "reason": "Setup, camera adjusting"}],
    "transitions": [{"between": ["s1", "s2"], "type": "crossfade", "duration_s": 0.2}],
    "captions": {
        "enabled": True,
        "style": "word_pop",
        "words": [{"t_in": 0.0, "t_out": 0.4, "text": "Hello", "emphasis": False}],
    },
    "effects": [{"t_in": 3.1, "t_out": 3.6, "type": "punch_zoom", "params": {}, "reason": "reveal"}],
    "overlays": [{"t_in": 5.0, "t_out": 8.0, "type": "text", "content": "Wow", "position": "bottom"}],
    "broll": [
        {"t_in": 10.0, "t_out": 13.0, "query": "busy market street", "source": "pexels", "asset_id": None}
    ],
    "audio": {
        "music": {"enabled": True, "mood": "upbeat", "asset_id": None, "duck_under_speech": True},
        "sfx": [{"t": 3.1, "type": "whoosh", "asset_id": None, "reason": ""}],
        "normalize_lufs": -14,
        "denoise": True,
    },
    "color": {
        "preset": "warm_cinematic",
        "exposure": 0,
        "contrast": 0,
        "saturation": 0,
        "temperature": 0,
        "lut": None,
    },
}


def mutate(**path_values: Any) -> dict[str, Any]:
    data = copy.deepcopy(PRD_EXAMPLE)
    for path, value in path_values.items():
        node: Any = data
        keys = path.split("__")
        for k in keys[:-1]:
            node = node[int(k)] if k.isdigit() else node[k]
        last = keys[-1]
        node[int(last) if last.isdigit() else last] = value
    return data


def test_prd_example_is_valid() -> None:
    edl = parse_model(EDL, PRD_EXAMPLE)
    assert edl.output_duration_s == pytest.approx(6.5 + 38.5 - 0.2)
    assert edl.segment_starts() == {"s1": 0.0, "s2": pytest.approx(6.3)}


def test_round_trip_json() -> None:
    edl = parse_model(EDL, json.dumps(PRD_EXAMPLE))
    again = parse_model(EDL, edl.model_dump_json())
    assert again == edl


@pytest.mark.parametrize(
    ("changes", "needle"),
    [
        ({"segments__0__clip_id": "nope"}, "unknown clip_id"),
        ({"segments__0__src_out": 130.0}, "exceeds clip"),
        ({"segments__0__src_out": 12.0}, "greater than src_in"),
        ({"segments__1__id": "s1"}, "duplicate segment id"),
        ({"clips__1__order": 1}, "duplicate order"),
        ({"transitions__0__between": ["s1", "zz"]}, "unknown segment"),
        ({"transitions__0__duration_s": 0.0}, "needs duration_s > 0"),
        ({"effects__0__t_out": 400.0}, "past output duration"),
        ({"effects__0__type": "explode"}, "effects.0.type"),
        ({"segments__0__speed": 20}, "segments.0.speed"),
        ({"meta__aspect": "21:9"}, "meta.aspect"),
        ({"color__exposure": 500}, "color.exposure"),
    ],
)
def test_invalid_edls_are_rejected(changes: dict[str, Any], needle: str) -> None:
    with pytest.raises(SchemaValidationError) as exc:
        parse_model(EDL, mutate(**changes))
    assert needle in exc.value.feedback


def test_unknown_keys_rejected() -> None:
    data = copy.deepcopy(PRD_EXAMPLE)
    data["segments"][0]["confidence"] = 0.9
    with pytest.raises(SchemaValidationError) as exc:
        parse_model(EDL, data)
    assert "Extra inputs are not permitted" in exc.value.feedback


def test_non_adjacent_transition_rejected() -> None:
    data = copy.deepcopy(PRD_EXAMPLE)
    data["segments"].append({"id": "s3", "clip_id": "c2", "src_in": 50, "src_out": 60})
    data["transitions"] = [{"between": ["s1", "s3"], "type": "cut", "duration_s": 0}]
    with pytest.raises(SchemaValidationError, match="not adjacent"):
        parse_model(EDL, data)


def test_overlapping_captions_rejected() -> None:
    data = copy.deepcopy(PRD_EXAMPLE)
    data["captions"]["words"].append({"t_in": 0.2, "t_out": 0.6, "text": "world"})
    with pytest.raises(SchemaValidationError, match="overlap"):
        parse_model(EDL, data)


def test_invalid_json_text() -> None:
    with pytest.raises(SchemaValidationError):
        parse_model(EDL, "{not json")


def test_lint_flags_soft_problems() -> None:
    data = copy.deepcopy(PRD_EXAMPLE)
    data["segments"][0]["src_in"] = 10.0  # overlaps the removed 0..12.4 range
    data["meta"]["target_duration_s"] = 10
    codes = {i.code for i in lint_edl(parse_model(EDL, data))}
    assert {"kept_and_removed", "off_target_length"} <= codes


def test_json_schemas_export() -> None:
    schemas = json_schemas()
    assert set(schemas) == {"edl", "composition", "signals", "media_info"}
    assert schemas["edl"]["additionalProperties"] is False
