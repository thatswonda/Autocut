from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autoedit_pipeline.cli import app

runner = CliRunner()


@pytest.mark.slow
def test_analyze_two_clips(storyboard: Path, storyboard_rot90: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = runner.invoke(
        app,
        [
            "analyze",
            str(storyboard),
            str(storyboard_rot90),
            "-o",
            str(out),
            "--cache-dir",
            str(tmp_path / "c"),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    project = json.loads((out / "project.json").read_text())
    assert [c["id"] for c in project["clips"]] == ["c1", "c2"]
    assert project["total_raw_duration_s"] == pytest.approx(60.0, abs=0.2)
    for clip in project["clips"]:
        signals = json.loads(Path(clip["signals"]).read_text())
        assert signals["clip_id"] == clip["id"]


def test_plan_limit_enforced(storyboard: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOEDIT_FREE_MAX_TOTAL_RAW_S", "45")
    result = runner.invoke(
        app, ["analyze", str(storyboard), str(storyboard), "--plan", "free", "-o", str(tmp_path)]
    )
    assert result.exit_code == 2
    assert "exceeds the free plan limit" in result.output


def test_validate_and_schema_export(tmp_path: Path) -> None:
    edl = {
        "clips": [{"id": "c1", "order": 1, "duration_s": 10}],
        "segments": [{"id": "s1", "clip_id": "c1", "src_in": 1, "src_out": 4, "reason": "hook"}],
    }
    good = tmp_path / "edl.json"
    good.write_text(json.dumps(edl))
    assert runner.invoke(app, ["validate", "edl", str(good)]).exit_code == 0
    edl["segments"][0]["src_out"] = 50
    good.write_text(json.dumps(edl))
    bad = runner.invoke(app, ["validate", "edl", str(good)])
    assert bad.exit_code == 1 and "exceeds clip" in bad.output

    result = runner.invoke(app, ["schema", "export", str(tmp_path / "s")])
    assert result.exit_code == 0
    assert (tmp_path / "s" / "edl.schema.json").is_file()


def test_probe_command(storyboard: Path) -> None:
    result = runner.invoke(app, ["probe", str(storyboard)])
    assert result.exit_code == 0
    assert '"container": "mp4"' in result.output
