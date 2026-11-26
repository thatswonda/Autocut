"""A stand-in for Claude that returns storyboards built from the request, for tests (no API calls)."""

from __future__ import annotations

import json
import re
from typing import Any

from autoedit_motion.storyboard import Usage


class FakeClaude:
    def __init__(self, *, invent_price_first: bool = False):
        self.calls: list[dict[str, Any]] = []
        self.invent_price_first = invent_price_first

    def complete(self, system: str, messages: list[dict[str, Any]], usage: Usage) -> tuple[str, list[Any]]:
        self.calls.append({"system": system, "messages": messages})
        usage.model = "claude-opus-5-5"
        usage.calls += 1
        usage.input_tokens += 3000
        usage.output_tokens += 1500
        text = "\n".join(b.get("text", "") for m in messages if m["role"] == "user" and isinstance(m["content"], list)
                         for b in m["content"] if b.get("type") == "text")  # fmt: skip
        assets = re.findall(r"Asset id `([^`]+)` \((\w+)", text)
        photo = next((a for a, k in assets if k == "photo"), None)
        if "Scene to change:" in text:
            sid = re.search(r"Scene to change: (\S+)", text).group(1)  # type: ignore[union-attr]
            return json.dumps({"id": sid, "template": "kinetic_text", "t_in": 0, "t_out": 1,
                               "content": {"headline": "Punchier hook, same facts"}, "reason": "Revised"}), []
        settings = json.loads(re.search(r"# Settings\n(\{.*\})", text).group(1))  # type: ignore[union-attr]
        d = float(settings["duration_s"])
        price = "₦9,999" if self.invent_price_first and len(self.calls) == 1 else "₦4,500"
        scenes = [
            {"id": "s1", "template": "kinetic_text", "t_in": 0, "t_out": d * 0.3,
             "content": {"kicker": "Fresh daily", "headline": "Coffee roasted this morning"},
             "transition_out": {"type": "slide", "duration_s": 0.4}, "reason": "Hook"},
            {"id": "s2", "template": "product_reveal" if photo else "kinetic_text", "t_in": d * 0.3, "t_out": d * 0.6,
             "content": {"headline": "House Blend", "price": "" , "assets": [photo] if photo else []},
             "transition_out": {"type": "slide", "duration_s": 0.4}, "reason": "Product"},
            {"id": "s3", "template": "price_offer", "t_in": d * 0.6, "t_out": d * 0.8,
             "content": {"headline": "Your first bag", "price": price},
             "claims": [{"text": price, "source": "prompt"}],
             "transition_out": {"type": "fade", "duration_s": 0.4}, "reason": "Offer"},
            {"id": "s4", "template": "cta_end_card", "t_in": d * 0.8, "t_out": d,
             "content": {"headline": "Order today", "cta": "Order now"}, "reason": "CTA"},
        ]  # fmt: skip
        comp = {"version": 1, "meta": {"title": "Test", "summary": "Test storyboard"}, "brand": {"name": "x"},
                "scenes": scenes}  # fmt: skip
        return "```json\n" + json.dumps(comp) + "\n```", []
