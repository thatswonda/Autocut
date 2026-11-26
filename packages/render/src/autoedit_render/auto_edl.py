"""Rule-based first cut from Stage A signals.

Until the AI story pass (M3/M4) exists, this builds an EDL that removes what the analysis flagged as
dead (black frames, empty static shots, long holds, long pauses in speech), optionally trims to a
target length by dropping the weakest pieces, and records a reason for every keep and cut.
"""

from __future__ import annotations

from dataclasses import dataclass

from autoedit_schemas.edl import EDL, ClipRef, Color, RemovedRange, Segment
from autoedit_schemas.signals import Signals

MIN_PIECE_S = 0.8
HOLD_KEEP_S = 2.0
SPEECH_GAP_S = 1.2
GAP_PAD_S = 0.3


@dataclass
class ClipInput:
    clip_id: str
    signals: Signals


def _subtract(base: list[tuple[float, float]], cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = base
    for a, b in sorted(cuts):
        nxt = []
        for x, y in out:
            if b <= x or a >= y:
                nxt.append((x, y))
                continue
            if a > x:
                nxt.append((x, a))
            if b < y:
                nxt.append((b, y))
        out = nxt
    return out


def _score(sig: Signals, a: float, b: float) -> float:
    rows = [r for r in sig.per_second if a - 0.5 <= r.t < b]
    if not rows:
        return 0.0
    motion = sum(r.motion for r in rows) / len(rows)
    speech = sum((r.speech or 0.0) for r in rows) / len(rows)
    faces = sum(1 for r in rows if r.faces or r.person_count) / len(rows)
    quality = sum(1 for r in rows if r.blurry or r.shaky) / len(rows)
    return min(1.0, motion * 6) + speech * 1.5 + faces * 0.5 - quality * 0.6


def build_auto_edl(clips: list[ClipInput], *, aspect: str = "9:16", color_preset: str = "natural",
                   target_duration_s: float | None = None, title: str = "") -> EDL:  # fmt: skip
    pieces: list[tuple[str, float, float, str, float]] = []  # clip, in, out, reason, score
    removed: list[RemovedRange] = []
    refs: list[ClipRef] = []
    for order, ci in enumerate(clips, start=1):
        sig = ci.signals
        dur = sig.media.duration_s
        refs.append(ClipRef(id=ci.clip_id, order=order, duration_s=dur))
        cuts: list[tuple[float, float, str]] = []
        for z in sig.dead_zones:
            if z.reason == "black_frames":
                cuts.append((z.t_in, z.t_out, "Black frames"))
            elif z.reason == "static_empty":
                cuts.append((z.t_in, z.t_out, "Nothing happening and nobody speaking"))
            elif z.reason == "static_subject_hold" and z.t_out - z.t_in > HOLD_KEEP_S + 1.0:
                cuts.append((z.t_in + HOLD_KEEP_S, z.t_out, "Long still hold (kept the first 2 s)"))
        speechy = (sig.summary.audio.speech_ratio if sig.summary.audio else 0.0) > 0.15
        if speechy:
            for s in sig.silences:
                if s.t_out - s.t_in >= SPEECH_GAP_S:
                    cuts.append((s.t_in + GAP_PAD_S, s.t_out - GAP_PAD_S, "Long pause"))
        kept = _subtract([(0.0, dur)], [(a, b) for a, b, _ in cuts])
        kept = [(a, b) for a, b in kept if b - a >= MIN_PIECE_S]
        if not kept:
            kept = [(0.0, dur)]  # never drop a whole clip silently
        for a, b, reason in cuts:
            removed.append(RemovedRange(clip_id=ci.clip_id, src_in=round(a, 3), src_out=round(min(b, dur), 3), reason=reason))
        for a, b in kept:
            rows = [r for r in sig.per_second if a <= r.t < b]
            why = "Speech" if any((r.speech or 0) > 0.3 for r in rows) else "Action on screen" if any(r.motion > 0.01 for r in rows) else "Context"
            pieces.append((ci.clip_id, round(a, 3), round(b, 3), why, _score(sig, a, b)))

    by_id = {c.clip_id: c.signals for c in clips}
    if target_duration_s and pieces:
        total = sum(b - a for _, a, b, _, _ in pieces)
        # Drop the weakest pieces (never the opening one) until the cut fits the target.
        while total > target_duration_s * 1.05 and len(pieces) > 1:
            idx = min(range(1, len(pieces)), key=lambda i: pieces[i][4])
            cid, a, b, _, _ = pieces.pop(idx)
            removed.append(RemovedRange(clip_id=cid, src_in=a, src_out=b, reason="Trimmed to reach the target length"))
            total -= b - a
        if total > target_duration_s * 1.05:
            cid, a, b, why, sc = pieces[-1]
            excess = total - target_duration_s
            new_b = max(a + MIN_PIECE_S, b - excess)
            # End on a scene cut or speech boundary if one is close.
            sig = by_id[cid]
            marks = [c for c in sig.scene_cuts] + [r.t_out for r in sig.speech_ranges]
            near = [m for m in marks if a + MIN_PIECE_S <= m <= new_b + 1.0]
            if near:
                new_b = min(near, key=lambda m: abs(m - new_b))
            removed.append(RemovedRange(clip_id=cid, src_in=round(new_b, 3), src_out=b, reason="Trimmed to reach the target length"))
            pieces[-1] = (cid, a, round(new_b, 3), why, sc)

    segments = [
        Segment(id=f"s{i + 1}", clip_id=cid, src_in=a, src_out=b, reason=f"Kept: {why.lower()}")
        for i, (cid, a, b, why, _) in enumerate(pieces)
    ]
    total_raw = sum(r.duration_s for r in refs)
    return EDL(
        meta={"title": title, "summary": "Rule-based first cut from the analysis.", "aspect": aspect,
              "total_raw_duration_s": round(total_raw, 3), "target_duration_s": target_duration_s,
              "style": "auto"},  # type: ignore[arg-type]
        clips=refs,
        segments=segments,
        removed=removed,
        color=Color(preset=color_preset),
        captions={"enabled": False},  # type: ignore[arg-type]
    )  # fmt: skip
