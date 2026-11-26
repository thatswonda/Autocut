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
        if text.startswith("# Edit request") or "\n# Edit request" in text:
            return self._edit_plan(text), []
        assets = re.findall(r"Asset id `([^`]+)` \((\w+)", text)
        photo = next((a for a, k in assets if k == "photo"), None)
        if "Scene to change:" in text:
            sid = re.search(r"Scene to change: (\S+)", text).group(1)  # type: ignore[union-attr]
            return json.dumps(
                {
                    "id": sid,
                    "template": "kinetic_text",
                    "t_in": 0,
                    "t_out": 1,
                    "content": {"headline": "Punchier hook, same facts"},
                    "reason": "Revised",
                }
            ), []
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
        video = re.search(r"Asset id `([^`]+)` \(video, ([\d.]+) s long", text)
        if video:
            vid, vdur = video.group(1), float(video.group(2))
            scenes[1] = {
                "id": "s2",
                "template": "footage",
                "t_in": d * 0.3,
                "t_out": d * 0.6,
                "clip_id": vid,
                "src_in": 0,
                "src_out": round(min(vdur, d * 0.3), 2),
                "content": {"kicker": "In the shop", "headline": "Roasted in front of you"},
                "transition_out": {"type": "slide", "duration_s": 0.4},
                "reason": "Real footage",
            }
        comp = {"version": 1, "meta": {"title": "Test", "summary": "Test storyboard"}, "brand": {"name": "x"},
                "scenes": scenes}  # fmt: skip
        if settings.get("voiceover"):
            comp["voiceover"] = {
                "script": [
                    {"t_in": 0.1, "text": "Coffee roasted this morning."},
                    {"t_in": d * 0.3, "text": "Watch it happen in our shop."},
                    {"t_in": d * 0.8, "text": "Order yours today."},
                ]
            }
        return "```json\n" + json.dumps(comp) + "\n```", []

    @staticmethod
    def _edit_plan(text: str) -> str:
        """Keep every transcript phrase (or the middle of each silent clip), punch in on the second one."""
        segments = []
        removed = []
        for block in re.split(r"\n(?=## Clip )", text):
            head = re.match(r"## Clip (\S+) \(order \d+, ([\d.]+) s", block)
            if not head:
                continue
            cid, dur = head.group(1), float(head.group(2))
            phrases = [(float(a), float(b)) for a, b in re.findall(r"^\[([\d.]+)-([\d.]+)\]", block, re.M)]
            if phrases:
                for i, (a, b) in enumerate(phrases):
                    segments.append(
                        {
                            "clip_id": cid,
                            "src_in": a,
                            "src_out": b,
                            "reason": f"Phrase {i + 1}",
                            "punch_in": i == 1,
                        }
                    )
                if phrases[0][0] > 0.5:
                    removed.append(
                        {"clip_id": cid, "src_in": 0, "src_out": phrases[0][0], "reason": "Lead-in"}
                    )
            else:
                segments.append(
                    {
                        "clip_id": cid,
                        "src_in": round(dur * 0.1, 2),
                        "src_out": round(dur * 0.9, 2),
                        "reason": "The visible action",
                    }
                )
        plan = {
            "title": "Test edit",
            "summary": "Kept every phrase.",
            "segments": segments,
            "removed": removed,
            "tighten": True,
            "color": {"preset": "moody_film", "exposure": 8, "contrast": 10, "reason": "Dark look requested"},
            "captions": {"enabled": True, "style": "word_pop", "highlight": ["price", "naira"]},
        }
        return "```json\n" + json.dumps(plan) + "\n```"
