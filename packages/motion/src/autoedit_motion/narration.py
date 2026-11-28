"""Voiceover-led timing (how explainer and product videos are cut): the narration is recorded first and
every scene lasts at least as long as what is said over it, so visuals land with the words.

* ``auto_script`` writes narration from the scenes' own on-screen text when a storyboard has none (the
  text is already fact-checked, so the voice never says anything the video does not show).
* ``fit_to_voice`` stretches scenes to their recorded lines and places each line at its scene.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from autoedit_schemas import Composition
from autoedit_schemas.composition import Scene, VoiceLine

LEAD_S = 0.35  # silence before the first line of a scene
GAP_S = 0.25  # between two lines in one scene
TAIL_S = 0.45  # after the last line, before the scene changes


def _say(text: str) -> str:
    """Make on-screen copy speakable: currency and symbols as words, no list bullets."""
    t = text.strip()
    t = re.sub(r"₦\s?([\d,.]+)", r"\1 naira", t)
    t = re.sub(r"\$\s?([\d,.]+)", r"\1 dollars", t)
    t = re.sub(r"£\s?([\d,.]+)", r"\1 pounds", t)
    t = re.sub(r"€\s?([\d,.]+)", r"\1 euros", t)
    t = t.replace("%", " percent").replace("&", " and ").replace("+", " plus ")
    return re.sub(r"\s+", " ", t).strip(" -\u2013\u2014\u00b7\u2022")


def _sentence(t: str) -> str:
    t = _say(t)
    if not t:
        return ""
    return t if t[-1] in ".!?" else t + "."


def scene_line(sc: Scene) -> str:
    c = sc.content
    parts: list[str] = []
    if sc.template == "testimonial_card" and c.headline:
        who = f" says {c.subtext}" if c.subtext else ""
        return _sentence(f"{c.headline.strip(chr(34) + '“”')}{who}")
    if c.headline:
        parts.append(_sentence(c.headline))
    if c.price:
        parts.append(_sentence(f"Just {c.price}"))
    if c.stats:
        parts.append(_sentence(", ".join(f"{s.value} {s.label}" for s in c.stats[:3])))
    if c.items and sc.template in ("feature_list", "steps"):
        if sc.template == "steps":
            parts.append(_sentence(". ".join(f"Step {i + 1}, {it}" for i, it in enumerate(c.items[:5]))))
            return " ".join(p for p in parts if p)[:400]
        parts.append(_sentence(", ".join(c.items[:4])))
    if c.subtext and sc.template not in ("cta_end_card", "testimonial_card") and len(parts) < 2:
        parts.append(_sentence(c.subtext))
    if sc.template == "cta_end_card" and c.cta:
        parts.append(_sentence(c.cta))
    return " ".join(p for p in parts if p)[:400]


def auto_script(comp: Composition) -> list[VoiceLine]:
    lines = []
    for sc in sorted(comp.scenes, key=lambda s: s.t_in):
        text = scene_line(sc)
        if text:
            lines.append(VoiceLine(t_in=round(sc.t_in + LEAD_S, 3), text=text))
    return lines


def fit_to_voice(comp: Composition, lines: Sequence[tuple[float, float]]) -> tuple[Composition, list[float]]:
    """``lines`` are (original t_in, spoken duration). Returns the re-timed composition and each line's new
    start time. Scenes never get shorter; they grow to hold their narration."""
    scenes = sorted(comp.scenes, key=lambda s: s.t_in)
    owner = []
    for t, _d in lines:
        idx = next((i for i, sc in enumerate(scenes) if sc.t_in - 0.01 <= t < sc.t_out), len(scenes) - 1)
        owner.append(idx)
    lengths = []
    for i, sc in enumerate(scenes):
        spoken = [d for (_t, d), o in zip(lines, owner, strict=True) if o == i]
        need = LEAD_S + sum(spoken) + GAP_S * max(0, len(spoken) - 1) + TAIL_S if spoken else 0.0
        lengths.append(max(sc.t_out - sc.t_in, need))
    starts, t = [], 0.0
    for ln in lengths:
        starts.append(t)
        t += ln
    new_scenes = []
    for sc, a, ln in zip(scenes, starts, lengths, strict=True):
        upd: dict[str, object] = {"t_in": round(a, 3), "t_out": round(a + ln, 3)}
        if sc.template == "footage" and sc.src_in is not None and sc.src_out is not None:
            upd["src_out"] = sc.src_out  # footage frames are re-timed to the scene by the renderer
        new_scenes.append(sc.model_copy(update=upd))
    new_starts: list[float] = []
    cursor = {i: starts[i] + LEAD_S for i in range(len(scenes))}
    for (_t, d), o in zip(lines, owner, strict=True):
        new_starts.append(round(cursor[o], 3))
        cursor[o] += d + GAP_S
    data = comp.model_dump(by_alias=True)
    data["scenes"] = [s.model_dump(by_alias=True) for s in new_scenes]
    data["meta"]["duration_s"] = round(t, 3)
    data["voiceover"]["script"] = [
        {"t_in": ns, "text": ln["text"]} for ns, ln in zip(new_starts, data["voiceover"]["script"], strict=False)
    ] if len(data["voiceover"]["script"]) == len(new_starts) else data["voiceover"]["script"]  # fmt: skip
    return Composition.model_validate(data), new_starts
