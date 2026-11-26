"""Helpers to turn per-sample boolean masks into time ranges."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt


def mask_to_ranges(
    mask: Sequence[bool] | npt.NDArray[np.bool_],
    step_s: float,
    *,
    min_len_s: float = 0.0,
    merge_gap_s: float = 0.0,
    end_s: float | None = None,
) -> list[tuple[float, float]]:
    """Convert a boolean mask sampled every ``step_s`` seconds into ``[(t_in, t_out), ...]``.

    Gaps shorter than ``merge_gap_s`` are bridged first, then ranges shorter than ``min_len_s`` dropped.
    """
    arr = np.asarray(mask, dtype=bool)
    ranges: list[tuple[float, float]] = []
    if arr.size == 0:
        return ranges
    padded = np.concatenate([[False], arr, [False]])
    diff = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(diff == 1)
    stops = np.flatnonzero(diff == -1)
    limit = end_s if end_s is not None else arr.size * step_s
    for a, b in zip(starts, stops, strict=True):
        ranges.append((a * step_s, min(b * step_s, limit)))

    merged: list[tuple[float, float]] = []
    for r in ranges:
        if merged and r[0] - merged[-1][1] <= merge_gap_s + 1e-9:
            merged[-1] = (merged[-1][0], r[1])
        else:
            merged.append(r)
    return [(round(a, 3), round(b, 3)) for a, b in merged if b - a >= min_len_s - 1e-9]


def ranges_overlap_seconds(ranges: Sequence[tuple[float, float]], n_seconds: int) -> npt.NDArray[np.float64]:
    """For each second ``[s, s+1)``, the fraction covered by ``ranges``."""
    cover = np.zeros(n_seconds, dtype=np.float64)
    for a, b in ranges:
        for s in range(max(0, int(a)), min(n_seconds, int(np.ceil(b)))):
            cover[s] += max(0.0, min(b, s + 1) - max(a, s))
    return np.clip(cover, 0.0, 1.0)
