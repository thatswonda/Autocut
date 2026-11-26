"""Motion graphics Composition (PRD section 5.11). Engine work lands in M13; the contract lives here now."""

from __future__ import annotations

import itertools
from typing import Literal

from pydantic import Field, model_validator

from .common import TIME_EPSILON, Aspect, Identifier, NonNegTime, PosTime, Reason, StrictModel

COMPOSITION_VERSION: Literal[1] = 1

HexColor = str  # validated by pattern below
_HEX = r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$"

TemplateName = Literal[
    "kinetic_text",
    "product_reveal",
    "device_mockup",
    "feature_list",
    "stats_counter",
    "testimonial_card",
    "price_offer",
    "logo_sting",
    "cta_end_card",
    "footage",
]


class CompositionMeta(StrictModel):
    title: str = Field(default="", max_length=200)
    aspect: Aspect = Aspect.VERTICAL
    duration_s: PosTime = 30.0
    fps: Literal[24, 25, 30, 50, 60] = 30
    language: str = Field(default="en", max_length=16)
    style: str = Field(default="bold_modern", max_length=64)
    goal: Literal["promo", "product_launch", "explainer", "ad", "other"] = "promo"
    summary: str = Field(default="", max_length=600, description="One-paragraph creative rationale")


class BrandColors(StrictModel):
    primary: str = Field(default="#000000", pattern=_HEX)
    secondary: str = Field(default="#444444", pattern=_HEX)
    accent: str = Field(default="#FF5A1F", pattern=_HEX)
    background: str = Field(default="#FFFFFF", pattern=_HEX)
    text: str = Field(default="#111111", pattern=_HEX)


class BrandFonts(StrictModel):
    heading: str = Field(default="Inter", max_length=100)
    body: str = Field(default="Inter", max_length=100)


class Brand(StrictModel):
    name: str = Field(default="", max_length=200)
    logo_asset: Identifier | None = None
    colors: BrandColors = Field(default_factory=BrandColors)
    fonts: BrandFonts = Field(default_factory=BrandFonts)
    tone: str = Field(default="friendly", max_length=64)


class Claim(StrictModel):
    """A factual statement shown on screen. ``source`` is mandatory: unsourced claims are invalid."""

    text: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=500, description="e.g. 'website:/pricing' or 'prompt'")


class Stat(StrictModel):
    value: str = Field(min_length=1, max_length=24, description="e.g. '10,000+', '4.9', '24/7'")
    label: str = Field(default="", max_length=60)


class SceneContent(StrictModel):
    kicker: str = Field(default="", max_length=60, description="Small line above the headline")
    headline: str = Field(default="", max_length=200)
    subtext: str = Field(default="", max_length=500)
    items: list[str] = Field(default_factory=list, max_length=8)
    stats: list[Stat] = Field(default_factory=list, max_length=4)
    price: str = Field(default="", max_length=40)
    cta: str = Field(default="", max_length=40, description="Button text on CTA scenes")
    assets: list[Identifier] = Field(default_factory=list)


class Animation(StrictModel):
    in_: str = Field(default="fade", alias="in", max_length=32)
    out: str = Field(default="fade", max_length=32)
    easing: Literal["linear", "ease_in", "ease_out", "ease_in_out", "spring"] = "ease_out"

    model_config = StrictModel.model_config | {"populate_by_name": True}


class SceneTransition(StrictModel):
    type: Literal["cut", "fade", "wipe", "slide", "zoom"] = "cut"
    duration_s: NonNegTime = Field(default=0.0, le=2.0)


class Scene(StrictModel):
    id: Identifier
    template: TemplateName
    t_in: NonNegTime
    t_out: PosTime
    content: SceneContent = Field(default_factory=SceneContent)
    claims: list[Claim] = Field(default_factory=list)
    animation: Animation = Field(default_factory=Animation)
    transition_out: SceneTransition = Field(default_factory=SceneTransition)
    clip_id: Identifier | None = None
    src_in: NonNegTime | None = None
    src_out: PosTime | None = None
    reason: Reason = ""

    @model_validator(mode="after")
    def _check(self) -> Scene:
        if self.t_out <= self.t_in:
            raise ValueError("t_out must be greater than t_in")
        if self.template == "footage":
            if self.clip_id is None or self.src_in is None or self.src_out is None:
                raise ValueError("footage scenes require clip_id, src_in and src_out")
            if self.src_out <= self.src_in:
                raise ValueError("src_out must be greater than src_in")
        return self


class VoiceLine(StrictModel):
    t_in: NonNegTime
    text: str = Field(min_length=1, max_length=1000)


class Voiceover(StrictModel):
    enabled: bool = False
    voice: str = Field(default="", max_length=100)
    script: list[VoiceLine] = Field(default_factory=list)


class CompositionCaptions(StrictModel):
    enabled: bool = True
    style: str = Field(default="word_pop", max_length=64)


class CompositionMusic(StrictModel):
    enabled: bool = True
    mood: str = Field(default="upbeat", max_length=64)
    asset_id: Identifier | None = None
    duck_under_voiceover: bool = True


class CompositionAudio(StrictModel):
    music: CompositionMusic = Field(default_factory=CompositionMusic)
    sfx: list[dict[str, str | float]] = Field(default_factory=list)
    normalize_lufs: float = Field(default=-14.0, ge=-31.0, le=-5.0)


class Composition(StrictModel):
    version: Literal[1] = COMPOSITION_VERSION
    meta: CompositionMeta = Field(default_factory=CompositionMeta)
    brand: Brand = Field(default_factory=Brand)
    scenes: list[Scene] = Field(min_length=1)
    voiceover: Voiceover = Field(default_factory=Voiceover)
    captions: CompositionCaptions = Field(default_factory=CompositionCaptions)
    audio: CompositionAudio = Field(default_factory=CompositionAudio)

    @model_validator(mode="after")
    def _timeline(self) -> Composition:
        ids = [s.id for s in self.scenes]
        if len(set(ids)) != len(ids):
            raise ValueError("scenes: duplicate scene id")
        ordered = sorted(self.scenes, key=lambda s: s.t_in)
        if abs(ordered[0].t_in) > TIME_EPSILON:
            raise ValueError("scenes: the first scene must start at 0")
        for a, b in itertools.pairwise(ordered):
            gap = b.t_in - a.t_out
            if gap > TIME_EPSILON:
                raise ValueError(f"scenes: gap of {gap:.3f}s between {a.id} and {b.id}")
            overlap = -gap
            if overlap > a.transition_out.duration_s + TIME_EPSILON:
                raise ValueError(f"scenes: {a.id} and {b.id} overlap beyond the transition duration")
        if abs(ordered[-1].t_out - self.meta.duration_s) > 0.05:
            raise ValueError(
                f"scenes end at {ordered[-1].t_out:.2f}s but meta.duration_s is {self.meta.duration_s:.2f}s"
            )
        for line in self.voiceover.script:
            if line.t_in > self.meta.duration_s:
                raise ValueError("voiceover line starts after the end of the composition")
        return self
