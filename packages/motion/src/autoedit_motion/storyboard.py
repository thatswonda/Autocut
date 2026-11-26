"""Storyboard generation with Claude: brief + sources + images -> validated Composition.

Rules enforced in code, not only in the prompt (PRD 5.10 fact grounding):
* the reply must validate against the Composition schema;
* every number on screen must appear in a source or the brief;
* testimonials must be verbatim from a source;
* template length limits and asset references are checked.
Invalid replies are sent back once with the exact problems; a second failure fails the stage.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoedit_schemas import Composition, SchemaValidationError, parse_model
from autoedit_schemas.composition import Scene

from .styles import get_style

log = logging.getLogger(__name__)

PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = "storyboard.v1"

# $ per million tokens (input, output). Used for usage records and the per-job cost ceiling.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.0, 40.0),
}

LIMITS: dict[str, dict[str, int]] = {
    "kinetic_text": {"kicker": 24, "headline": 48, "subtext": 90},
    "product_reveal": {"kicker": 24, "headline": 36, "subtext": 80},
    "device_mockup": {"kicker": 24, "headline": 36, "subtext": 70},
    "feature_list": {"kicker": 24, "headline": 28, "item": 40},
    "stats_counter": {"headline": 32, "value": 10, "label": 24},
    "testimonial_card": {"headline": 140, "subtext": 40},
    "price_offer": {"kicker": 24, "headline": 32, "price": 14, "subtext": 60, "item": 18},
    "logo_sting": {"headline": 40, "subtext": 40},
    "cta_end_card": {"headline": 40, "cta": 18, "subtext": 40},
}
SLACK = 1.15  # tolerate small overruns; the renderer fits text anyway


class StoryboardError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class ImageInput:
    key: str
    kind: str  # logo | photo | screenshot
    path: Path
    source: str


@dataclass
class StoryboardRequest:
    brief: str
    aspect: str = "9:16"
    duration_s: float = 30.0
    style: str = "bold_pop"
    language: str = "en"
    goal: str = "promo"
    brand_name: str = ""
    sources: list[tuple[str, str]] = field(default_factory=list)  # (source_id, text)
    images: list[ImageInput] = field(default_factory=list)


@dataclass
class Usage:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    @property
    def cost_usd(self) -> float:
        pin, pout = PRICES.get(self.model, PRICES["claude-opus-5-5"])
        return round(self.input_tokens / 1e6 * pin + self.output_tokens / 1e6 * pout, 4)


def model_name() -> str:
    return os.environ.get("CLAUDE_MODEL_SMART", "claude-opus-5-5")


def max_cost() -> float:
    return float(os.environ.get("AUTOEDIT_MAX_JOB_COST_USD", "1.00"))


def ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


# --------------------------------------------------------------------------------------- client


class ClaudeClient:
    """Thin wrapper so tests can substitute a fake. Returns (text, raw assistant content, usage)."""

    def __init__(self) -> None:
        import anthropic

        self.client = anthropic.Anthropic(max_retries=3, timeout=300.0)

    def complete(self, system: str, messages: list[dict[str, Any]], usage: Usage) -> tuple[str, list[Any]]:
        import anthropic

        model = model_name()
        try:
            resp = self.client.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=system,
                messages=messages,  # type: ignore[arg-type]
                output_config={"effort": os.environ.get("CLAUDE_EFFORT", "medium")},  # type: ignore[typeddict-item]
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",  # type: ignore[arg-type]
            )
        except anthropic.AuthenticationError as exc:
            raise StoryboardError("ai_auth", "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.") from exc
        except anthropic.RateLimitError as exc:
            raise StoryboardError("ai_rate_limited", "Claude is busy right now. Try again in a minute.") from exc
        except anthropic.APIStatusError as exc:
            raise StoryboardError("ai_error", f"Claude request failed ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise StoryboardError("ai_unreachable", "Could not reach the Claude API.") from exc
        usage.model = model
        usage.calls += 1
        usage.input_tokens += int(getattr(resp.usage, "input_tokens", 0) or 0)
        usage.input_tokens += int(getattr(resp.usage, "cache_read_input_tokens", 0) or 0)
        usage.output_tokens += int(getattr(resp.usage, "output_tokens", 0) or 0)
        if resp.stop_reason == "refusal":
            raise StoryboardError("ai_refused", "Claude declined this request. Try rewording the brief.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return text, list(resp.content)


def CLIENT_FACTORY() -> Any:  # noqa: N802 - replaced in tests with a fake client
    return ClaudeClient()


# ------------------------------------------------------------------------------------- prompts


def _image_block(path: Path) -> dict[str, Any]:
    from PIL import Image

    im = Image.open(path)
    im = im.convert("RGB")
    im.thumbnail((768, 768))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def _user_content(req: StoryboardRequest) -> list[dict[str, Any]]:
    style = get_style(req.style)
    settings = {
        "aspect": req.aspect, "duration_s": req.duration_s, "style": style.key, "language": req.language,
        "goal": req.goal, "brand_name": req.brand_name,
        "style_notes": f"{style.name}: {style.description} Default transition: {style.transition} {style.transition_s}s.",
    }  # fmt: skip
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": f"# Brief (source id: prompt)\n{req.brief.strip() or '(no brief given)'}"},
        {"type": "text", "text": "# Settings\n" + json.dumps(settings, ensure_ascii=False)},
    ]
    if req.sources:
        text = "\n\n".join(f"## Source {sid}\n{body}" for sid, body in req.sources)
        blocks.append({"type": "text", "text": "# Sources\n" + text})
    else:
        blocks.append({"type": "text", "text": "# Sources\n(none besides the brief)"})
    if req.images:
        blocks.append({"type": "text", "text": "# Images"})
        for im in req.images[:8]:
            blocks.append({"type": "text", "text": f"Asset id `{im.key}` ({im.kind}, from {im.source}):"})
            blocks.append(_image_block(im.path))
    else:
        blocks.append({"type": "text", "text": "# Images\n(none: use text-only templates and logo_sting with the brand name)"})
    return blocks


def _extract_json(text: str) -> str:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    return text[start : end + 1] if start >= 0 and end > start else text


# --------------------------------------------------------------------------------- validation

_NUM_TOKEN = re.compile(r"\d[\d,.]*")


def _digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _scene_texts(sc: Scene) -> list[str]:
    c = sc.content
    out = [c.kicker, c.headline, c.subtext, c.price, c.cta, *c.items]
    out += [s.value for s in c.stats] + [s.label for s in c.stats]
    return [t for t in out if t]


def check_scene(sc: Scene, req: StoryboardRequest, source_blob: str) -> list[str]:
    """Rules beyond the JSON schema. Returns human-readable problems (empty when fine)."""
    problems: list[str] = []
    lim = LIMITS.get(sc.template, {})
    c = sc.content

    def over(name: str, value: str, limit_key: str) -> None:
        limit = lim.get(limit_key)
        if limit and len(value) > limit * SLACK:
            problems.append(f"{sc.id} {name} is {len(value)} chars; the {sc.template} limit is {limit}")

    for fname in ("kicker", "headline", "subtext", "price", "cta"):
        over(fname, getattr(c, fname), fname)
    for it in c.items:
        over("item", it, "item")
    for st in c.stats:
        over("stat value", st.value, "value")
        over("stat label", st.label, "label")

    image_keys = {im.key: im.kind for im in req.images}
    for a in c.assets:
        if a not in image_keys:
            problems.append(f"{sc.id} uses asset '{a}', which does not exist")
    if sc.template == "product_reveal" and not c.assets:
        problems.append(f"{sc.id} product_reveal needs a photo asset")
    if sc.template == "device_mockup" and not c.assets:
        problems.append(f"{sc.id} device_mockup needs a screenshot asset")
    if sc.template == "feature_list" and not 2 <= len(c.items) <= 5:
        problems.append(f"{sc.id} feature_list needs 3-5 items")
    if sc.template == "stats_counter" and not 1 <= len(c.stats) <= 3:
        problems.append(f"{sc.id} stats_counter needs 2-3 stats")
    if sc.template == "cta_end_card" and not c.cta:
        problems.append(f"{sc.id} cta_end_card needs cta button text")

    # Fact grounding: every number shown must exist in the brief or a source.
    source_digits = {_digits(m) for m in _NUM_TOKEN.findall(source_blob)}
    for text in _scene_texts(sc):
        for tok in _NUM_TOKEN.findall(text):
            d = _digits(tok)
            if len(d) >= 1 and d not in source_digits and d.lstrip("0") not in source_digits:
                problems.append(f"{sc.id}: '{tok}' in \"{text}\" does not appear in the sources or brief; remove it or use a sourced figure")
    if sc.template == "testimonial_card" and _norm(c.headline) not in _norm(source_blob):
        problems.append(f"{sc.id}: the testimonial is not a verbatim quote from the sources; remove the scene or use an exact quote")
    valid_sources = {"prompt", *(sid for sid, _ in req.sources), *(im.source for im in req.images)}
    for cl in sc.claims:
        if cl.source not in valid_sources and not any(cl.source.startswith(v) for v in valid_sources):
            problems.append(f"{sc.id}: claim source '{cl.source}' is not one of the provided source ids")
    return problems


def check_composition(comp: Composition, req: StoryboardRequest) -> list[str]:
    blob = req.brief + "\n" + "\n".join(body for _, body in req.sources)
    problems: list[str] = []
    for sc in comp.scenes:
        problems.extend(check_scene(sc, req, blob))
    if comp.scenes and sorted(comp.scenes, key=lambda s: s.t_in)[-1].template != "cta_end_card":
        problems.append("the last scene must be a cta_end_card")
    return problems


def _normalise(comp_json: dict[str, Any], req: StoryboardRequest, brand: dict[str, Any]) -> dict[str, Any]:
    """Pin the fields the user chose (Claude must not drift from them) and attach the brand kit."""
    meta = comp_json.setdefault("meta", {})
    meta.update({"aspect": req.aspect, "duration_s": req.duration_s, "style": req.style, "fps": 30,
                 "language": req.language, "goal": req.goal})  # fmt: skip
    comp_json["brand"] = brand
    scenes = comp_json.get("scenes") or []
    # Snap tiny timing gaps/overlaps so a near-miss from the model does not fail validation.
    t = 0.0
    for sc in scenes:
        if isinstance(sc, dict):
            length = max(1.0, float(sc.get("t_out", 0)) - float(sc.get("t_in", 0)))
            sc["t_in"] = round(t, 3)
            t += length
            sc["t_out"] = round(t, 3)
    if scenes and t > 0 and abs(t - req.duration_s) > 0.01:
        scale = req.duration_s / t
        acc = 0.0
        for sc in scenes:
            length = (sc["t_out"] - sc["t_in"]) * scale
            sc["t_in"] = round(acc, 3)
            acc += length
            sc["t_out"] = round(acc, 3)
        scenes[-1]["t_out"] = req.duration_s
    if scenes:
        scenes[-1]["transition_out"] = {"type": "cut", "duration_s": 0}
    return comp_json


def generate_composition(req: StoryboardRequest, brand: dict[str, Any], *, client: Any = None,
                         usage: Usage | None = None) -> Composition:  # fmt: skip
    """Ask Claude for a storyboard; validate; one corrective retry."""
    usage = usage or Usage()
    client = client or CLIENT_FACTORY()
    system = (PROMPTS / "storyboard.v1.md").read_text()
    messages: list[dict[str, Any]] = [{"role": "user", "content": _user_content(req)}]
    last_problems: list[str] = []
    for attempt in range(2):
        text, raw = client.complete(system, messages, usage)
        if usage.cost_usd > max_cost():
            raise StoryboardError("cost_ceiling", f"Stopped: this storyboard cost more than ${max_cost():.2f}.")
        try:
            data = json.loads(_extract_json(text))
            comp = parse_model(Composition, _normalise(data, req, brand))
            problems = check_composition(comp, req)
        except json.JSONDecodeError as exc:
            problems, comp = [f"Reply was not valid JSON: {exc}"], None
        except SchemaValidationError as exc:
            problems, comp = [exc.feedback], None
        if comp is not None and not problems:
            log.info("storyboard ok after %d call(s), $%.4f", usage.calls, usage.cost_usd)
            return comp
        last_problems = problems
        if attempt == 0:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Fix these problems and return the full corrected JSON only:\n- "
                             + "\n- ".join(problems[:30])})  # fmt: skip
    raise StoryboardError("invalid_storyboard", "Claude's storyboard broke the rules twice: " + "; ".join(last_problems[:5]))


def regenerate_scene(comp: Composition, scene_id: str, note: str, req: StoryboardRequest, *,
                     client: Any = None, usage: Usage | None = None) -> Composition:  # fmt: skip
    usage = usage or Usage()
    client = client or CLIENT_FACTORY()
    target = next((s for s in comp.scenes if s.id == scene_id), None)
    if target is None:
        raise StoryboardError("scene_not_found", "That scene does not exist")
    system = (PROMPTS / "scene.v1.md").read_text()
    content = _user_content(req) + [
        {"type": "text", "text": "# Current storyboard\n" + comp.model_dump_json(by_alias=True)},
        {"type": "text", "text": f"# Scene to change: {scene_id}\n# User's note\n{note.strip() or 'Make it better.'}"},
    ]
    messages: list[dict[str, Any]] = [{"role": "user", "content": content}]
    blob = req.brief + "\n" + "\n".join(body for _, body in req.sources)
    problems: list[str] = []
    for attempt in range(2):
        text, raw = client.complete(system, messages, usage)
        if usage.cost_usd > max_cost():
            raise StoryboardError("cost_ceiling", f"Stopped: this change cost more than ${max_cost():.2f}.")
        try:
            sc_json = json.loads(_extract_json(text))
            sc_json.update({"id": target.id, "t_in": target.t_in, "t_out": target.t_out})
            new_scene = parse_model(Scene, sc_json)
            data = comp.model_dump(by_alias=True)
            data["scenes"] = [new_scene.model_dump(by_alias=True) if s["id"] == scene_id else s for s in data["scenes"]]
            new_comp = parse_model(Composition, data)
            problems = check_scene(new_scene, req, blob)
        except json.JSONDecodeError as exc:
            problems, new_comp = [f"Reply was not valid JSON: {exc}"], None
        except SchemaValidationError as exc:
            problems, new_comp = [exc.feedback], None
        if new_comp is not None and not problems:
            return new_comp
        if attempt == 0:
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": "Fix these problems and return the scene JSON only:\n- " + "\n- ".join(problems)})
    raise StoryboardError("invalid_scene", "Claude's revised scene broke the rules twice: " + "; ".join(problems[:4]))
