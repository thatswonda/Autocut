"""Plan -> EDL: word-safe cuts, filler jump cuts, captions on the output timeline, removed labels."""

from __future__ import annotations

from autoedit_render.captions import build_ass, group_words
from autoedit_render.planner import (
    ClipMaterial,
    EditPlan,
    EditRequest,
    PlanCaptions,
    PlanSegment,
    check_plan,
    look_from_prompt,
    plan_to_edl,
)
from autoedit_schemas.edl import CaptionWord
from autoedit_schemas.media import MediaInfo
from autoedit_schemas.signals import Signals
from autoedit_schemas.transcript import Transcript, Word


def _signals(duration: float) -> Signals:
    # Only the fields the planner reads; the full analysis is covered by the pipeline tests.
    media = MediaInfo.model_construct(
        duration_s=duration, has_audio=True, display_width=1280, display_height=720
    )
    return Signals.model_construct(clip_id="c1", media=media, per_second=[], dead_zones=[], quality_flags=[],
                                   scene_cuts=[])  # fmt: skip


def _material() -> ClipMaterial:
    words = [
        Word(t_in=0.50, t_out=0.90, text="Hello"), Word(t_in=0.95, t_out=1.40, text="everyone."),
        Word(t_in=1.80, t_out=2.10, text="Um,", filler=True), Word(t_in=2.40, t_out=2.70, text="the"),
        Word(t_in=2.70, t_out=3.10, text="price"), Word(t_in=3.10, t_out=3.30, text="is"),
        Word(t_in=3.30, t_out=3.70, text="15"), Word(t_in=3.70, t_out=4.20, text=",000€."),
    ]  # fmt: skip
    return ClipMaterial("c1", 1, _signals(6.0), Transcript(clip_id="c1", words=words, duration_s=6.0))


def test_cuts_never_split_words_and_fillers_become_jump_cuts() -> None:
    plan = EditPlan(segments=[PlanSegment(clip_id="c1", src_in=0.7, src_out=3.9, reason="Hook")],
                    captions=PlanCaptions(enabled=True, highlight=["price"]),
                    caption_fixes={",000€.": ",000 naira."})  # fmt: skip
    edl = plan_to_edl(plan, [_material()], EditRequest())
    assert edl.segments[0].src_in <= 0.5  # moved out of "Hello", with a lead-in
    assert edl.segments[-1].src_out >= 4.2  # extended to the end of ",000€."
    assert len(edl.segments) == 2  # the "Um" was cut out of the middle
    assert any("Filler" in r.reason for r in edl.removed)
    texts = [w.text for w in edl.captions.words]
    assert "Um," not in texts and texts[-1] == "15,000 naira."  # split number glued, fix applied
    assert next(w for w in edl.captions.words if w.text == "price").emphasis
    starts = [w.t_in for w in edl.captions.words]
    assert starts == sorted(starts) and edl.captions.words[-1].t_out <= edl.output_duration_s + 1e-6


def test_plan_checks_and_prompt_looks() -> None:
    m = _material()
    bad = EditPlan(segments=[PlanSegment(clip_id="c9", src_in=0, src_out=2),
                             PlanSegment(clip_id="c1", src_in=1, src_out=9)])  # fmt: skip
    problems = check_plan(bad, [m], EditRequest())
    assert any("unknown clip_id" in p for p in problems) and any("past the end" in p for p in problems)
    assert look_from_prompt("Dark quality color grading") == "moody_film"
    assert look_from_prompt("make it black and white") == "black_white"
    assert look_from_prompt("just cut it") == "natural"


def test_caption_grouping_and_ass() -> None:
    words = [
        CaptionWord(t_in=i * 0.3, t_out=i * 0.3 + 0.25, text=t)
        for i, t in enumerate(["one", "two", "three", "four."])
    ]
    groups = group_words(words, max_words=3, max_chars=18)
    assert [len(g) for g in groups] == [3, 1]
    ass = build_ass(words, width=720, height=1280, style="karaoke")
    assert "Bebas Neue" in ass and "ONE" in ass and ass.count("Dialogue:") == 4
    assert (
        ",8,"
        in build_ass(words, width=720, height=1280, style="clean", top=True).split("Style: Cap")[1][:400]
    )
