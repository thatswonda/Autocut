"""Transcript of a clip's speech with word timestamps (PRD 5.2 Stage B)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import NonNegTime, StrictModel

TRANSCRIPT_VERSION: Literal[1] = 1


class Word(StrictModel):
    t_in: NonNegTime
    t_out: NonNegTime
    text: str = Field(max_length=64)
    prob: float = Field(default=1.0, ge=0.0, le=1.0)
    filler: bool = Field(default=False, description="um, uh, er... safe to cut")
    repeat: bool = Field(default=False, description="Immediate repetition of the previous word (stutter)")


class Sentence(StrictModel):
    t_in: NonNegTime
    t_out: NonNegTime
    text: str = Field(max_length=2000)


class Transcript(StrictModel):
    version: Literal[1] = TRANSCRIPT_VERSION
    clip_id: str = ""
    language: str = ""
    language_probability: float = 0.0
    model: str = ""
    duration_s: NonNegTime = 0.0
    words: list[Word] = Field(default_factory=list)
    sentences: list[Sentence] = Field(default_factory=list)
    pauses: list[tuple[float, float]] = Field(default_factory=list, description="Gaps > 0.7 s between words")

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)
