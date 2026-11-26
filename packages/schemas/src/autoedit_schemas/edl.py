"""Edit Decision List (PRD section 5.3).

The AI never edits pixels. It emits an EDL and the deterministic renderer turns it into video.

Time domains:
* ``src_in`` / ``src_out`` are seconds inside the clip named by ``clip_id``.
* every other ``t``, ``t_in``, ``t_out`` is on the final output timeline.
"""

from __future__ import annotations

import itertools
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from .common import (
    TIME_EPSILON,
    Aspect,
    Identifier,
    NonNegTime,
    PosTime,
    Reason,
    StrictModel,
)

EDL_VERSION: Literal[1] = 1


class TransitionType(StrEnum):
    CUT = "cut"
    CROSSFADE = "crossfade"
    WHIP = "whip"
    ZOOM = "zoom"


class EffectType(StrEnum):
    PUNCH_ZOOM = "punch_zoom"
    SHAKE = "shake"
    SLOWMO = "slowmo"
    FREEZE = "freeze"
    SPEED_RAMP = "speed_ramp"


class OverlayType(StrEnum):
    TEXT = "text"
    LOWER_THIRD = "lower_third"
    EMOJI = "emoji"
    ARROW = "arrow"
    LOGO = "logo"


class SfxType(StrEnum):
    WHOOSH = "whoosh"
    IMPACT = "impact"
    POP = "pop"
    DING = "ding"


class Meta(StrictModel):
    title: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=2000)
    aspect: Aspect = Aspect.VERTICAL
    total_raw_duration_s: NonNegTime = 0.0
    target_duration_s: PosTime | None = None
    style: str = Field(default="energetic", max_length=64)
    language: str = Field(default="en", max_length=16)


class ClipRef(StrictModel):
    id: Identifier
    order: int = Field(ge=1)
    duration_s: PosTime


class Segment(StrictModel):
    id: Identifier
    clip_id: Identifier
    src_in: NonNegTime
    src_out: PosTime
    speed: float = Field(default=1.0, gt=0.1, le=8.0)
    reason: Reason = ""

    @model_validator(mode="after")
    def _ordered(self) -> Segment:
        if self.src_out - self.src_in <= TIME_EPSILON:
            raise ValueError(f"src_out ({self.src_out}) must be greater than src_in ({self.src_in})")
        return self

    @property
    def output_duration_s(self) -> float:
        return (self.src_out - self.src_in) / self.speed


class RemovedRange(StrictModel):
    clip_id: Identifier
    src_in: NonNegTime
    src_out: PosTime
    reason: Reason = ""

    @model_validator(mode="after")
    def _ordered(self) -> RemovedRange:
        if self.src_out <= self.src_in:
            raise ValueError("src_out must be greater than src_in")
        return self


class Transition(StrictModel):
    between: tuple[Identifier, Identifier]
    type: TransitionType = TransitionType.CUT
    duration_s: NonNegTime = Field(default=0.0, le=2.0)
    reason: Reason = ""


class _Timed(StrictModel):
    t_in: NonNegTime
    t_out: PosTime

    @model_validator(mode="after")
    def _ordered(self) -> Any:
        if self.t_out <= self.t_in:
            raise ValueError(f"t_out ({self.t_out}) must be greater than t_in ({self.t_in})")
        return self


class CaptionWord(_Timed):
    text: str = Field(min_length=1, max_length=64)
    emphasis: bool = False


class Captions(StrictModel):
    enabled: bool = True
    style: str = Field(default="word_pop", max_length=64)
    language: str | None = Field(default=None, max_length=16)
    words: list[CaptionWord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _monotonic(self) -> Captions:
        for prev, cur in zip(self.words, self.words[1:], strict=False):
            if cur.t_in + TIME_EPSILON < prev.t_in:
                raise ValueError("caption words must be sorted by t_in")
            if cur.t_in + TIME_EPSILON < prev.t_out:
                raise ValueError(f"caption words overlap at t={cur.t_in:.3f} ('{prev.text}' / '{cur.text}')")
        return self


class Effect(_Timed):
    type: EffectType
    params: dict[str, float | int | str | bool] = Field(default_factory=dict)
    reason: Reason = ""


class Overlay(_Timed):
    type: OverlayType
    content: str = Field(default="", max_length=500)
    position: Literal["top", "center", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"] = (
        "bottom"
    )
    reason: Reason = ""


class BRoll(_Timed):
    query: str = Field(min_length=1, max_length=200)
    source: Literal["pexels", "pixabay", "user", "project_clip"] = "pexels"
    asset_id: Identifier | None = None
    reason: Reason = ""


class Music(StrictModel):
    enabled: bool = False
    mood: str = Field(default="upbeat", max_length=64)
    asset_id: Identifier | None = None
    duck_under_speech: bool = True
    volume_db: float = Field(default=-18.0, ge=-60.0, le=0.0)


class Sfx(StrictModel):
    t: NonNegTime
    type: SfxType
    asset_id: Identifier | None = None
    reason: Reason = ""


class Audio(StrictModel):
    music: Music = Field(default_factory=Music)
    sfx: list[Sfx] = Field(default_factory=list)
    normalize_lufs: float = Field(default=-14.0, ge=-31.0, le=-5.0)
    denoise: bool = True


class Color(StrictModel):
    preset: str = Field(default="none", max_length=64)
    exposure: float = Field(default=0.0, ge=-100.0, le=100.0)
    contrast: float = Field(default=0.0, ge=-100.0, le=100.0)
    saturation: float = Field(default=0.0, ge=-100.0, le=100.0)
    temperature: float = Field(default=0.0, ge=-100.0, le=100.0)
    lut: str | None = Field(default=None, max_length=200)


class EDL(StrictModel):
    version: Literal[1] = EDL_VERSION
    meta: Meta = Field(default_factory=Meta)
    clips: list[ClipRef] = Field(min_length=1)
    segments: list[Segment] = Field(min_length=1)
    removed: list[RemovedRange] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    captions: Captions = Field(default_factory=Captions)
    effects: list[Effect] = Field(default_factory=list)
    overlays: list[Overlay] = Field(default_factory=list)
    broll: list[BRoll] = Field(default_factory=list)
    audio: Audio = Field(default_factory=Audio)
    color: Color = Field(default_factory=Color)

    # ---- derived values ---------------------------------------------------------------------
    @property
    def output_duration_s(self) -> float:
        """Length of the rendered timeline. Transitions overlap adjacent segments by their duration."""
        total = sum(s.output_duration_s for s in self.segments)
        total -= sum(t.duration_s for t in self.transitions if t.type is not TransitionType.CUT)
        return max(total, 0.0)

    def clip(self, clip_id: str) -> ClipRef:
        for c in self.clips:
            if c.id == clip_id:
                return c
        raise KeyError(clip_id)

    def segment_starts(self) -> dict[str, float]:
        """Output-timeline start of every segment, honouring transition overlaps."""
        overlap: dict[tuple[str, str], float] = {
            (t.between[0], t.between[1]): t.duration_s
            for t in self.transitions
            if t.type is not TransitionType.CUT
        }
        starts: dict[str, float] = {}
        cursor = 0.0
        prev: Segment | None = None
        for seg in self.segments:
            if prev is not None:
                cursor -= overlap.get((prev.id, seg.id), 0.0)
            starts[seg.id] = cursor
            cursor += seg.output_duration_s
            prev = seg
        return starts

    # ---- structural validation ----------------------------------------------------------------
    @model_validator(mode="after")
    def _referential_integrity(self) -> EDL:
        errors: list[str] = []

        clip_ids = [c.id for c in self.clips]
        if len(set(clip_ids)) != len(clip_ids):
            errors.append("clips: duplicate clip id")
        orders = [c.order for c in self.clips]
        if len(set(orders)) != len(orders):
            errors.append("clips: duplicate order value")
        clips = {c.id: c for c in self.clips}

        seg_ids = [s.id for s in self.segments]
        if len(set(seg_ids)) != len(seg_ids):
            errors.append("segments: duplicate segment id")

        for i, s in enumerate(self.segments):
            clip = clips.get(s.clip_id)
            if clip is None:
                errors.append(f"segments[{i}] ({s.id}): unknown clip_id '{s.clip_id}'")
            elif s.src_out > clip.duration_s + TIME_EPSILON:
                errors.append(
                    f"segments[{i}] ({s.id}): src_out {s.src_out:.3f}s exceeds clip "
                    f"'{clip.id}' duration {clip.duration_s:.3f}s"
                )

        for i, r in enumerate(self.removed):
            clip = clips.get(r.clip_id)
            if clip is None:
                errors.append(f"removed[{i}]: unknown clip_id '{r.clip_id}'")
            elif r.src_out > clip.duration_s + TIME_EPSILON:
                errors.append(f"removed[{i}]: src_out exceeds clip '{clip.id}' duration")

        seg_index = {sid: i for i, sid in enumerate(seg_ids)}
        out_durations = {s.id: s.output_duration_s for s in self.segments}
        for i, t in enumerate(self.transitions):
            a, b = t.between
            if a not in seg_index or b not in seg_index:
                errors.append(f"transitions[{i}]: references unknown segment(s) {a!r}, {b!r}")
                continue
            if seg_index[b] != seg_index[a] + 1:
                errors.append(f"transitions[{i}]: '{a}' and '{b}' are not adjacent in segment order")
            if t.type is not TransitionType.CUT:
                if t.duration_s <= 0:
                    errors.append(f"transitions[{i}]: {t.type} needs duration_s > 0")
                if t.duration_s >= min(out_durations[a], out_durations[b]):
                    errors.append(f"transitions[{i}]: duration longer than an adjacent segment")

        if not errors:
            duration = self.output_duration_s + TIME_EPSILON
            timed: list[tuple[str, float]] = []
            timed += [(f"captions.words[{i}]", w.t_out) for i, w in enumerate(self.captions.words)]
            timed += [(f"effects[{i}]", e.t_out) for i, e in enumerate(self.effects)]
            timed += [(f"overlays[{i}]", o.t_out) for i, o in enumerate(self.overlays)]
            timed += [(f"broll[{i}]", b.t_out) for i, b in enumerate(self.broll)]
            timed += [(f"audio.sfx[{i}]", s.t) for i, s in enumerate(self.audio.sfx)]
            for name, t_end in timed:
                if t_end > duration:
                    errors.append(
                        f"{name}: ends at {t_end:.3f}s, past output duration {self.output_duration_s:.3f}s"
                    )

        if errors:
            raise ValueError("; ".join(errors))
        return self


class LintIssue(StrictModel):
    level: Literal["warning", "info"]
    code: str
    message: str


def lint_edl(edl: EDL, target_tolerance: float = 0.25) -> list[LintIssue]:
    """Soft checks that are legal but usually wrong. Shown in the Edit Report and fed to self-review."""
    issues: list[LintIssue] = []

    by_clip: dict[str, list[tuple[float, float, str]]] = {}
    for s in edl.segments:
        by_clip.setdefault(s.clip_id, []).append((s.src_in, s.src_out, s.id))
    for clip_id, ranges in by_clip.items():
        ranges.sort()
        for (a_in, a_out, a_id), (b_in, _b_out, b_id) in itertools.pairwise(ranges):
            del a_in
            if b_in < a_out - TIME_EPSILON:
                issues.append(
                    LintIssue(
                        level="warning",
                        code="segment_source_overlap",
                        message=f"segments {a_id} and {b_id} reuse overlapping footage of clip {clip_id}",
                    )
                )

    for s in edl.segments:
        for r in edl.removed:
            if r.clip_id == s.clip_id and r.src_in < s.src_out - TIME_EPSILON and s.src_in < r.src_out:
                issues.append(
                    LintIssue(
                        level="warning",
                        code="kept_and_removed",
                        message=f"segment {s.id} overlaps a removed range of clip {s.clip_id}",
                    )
                )

    target = edl.meta.target_duration_s
    if target:
        dur = edl.output_duration_s
        if abs(dur - target) > target * target_tolerance:
            issues.append(
                LintIssue(
                    level="warning",
                    code="off_target_length",
                    message=f"output is {dur:.1f}s against a {target:.1f}s target",
                )
            )

    if not any(s.reason for s in edl.segments):
        issues.append(LintIssue(level="info", code="missing_reasons", message="no segment carries a reason"))
    return issues
