from __future__ import annotations

import numpy as np
import pytest

from autoedit_pipeline.signals.ranges import mask_to_ranges, ranges_overlap_seconds


def test_mask_to_ranges_basic() -> None:
    mask = [False, True, True, False, False, True]
    assert mask_to_ranges(mask, 0.5) == [(0.5, 1.5), (2.5, 3.0)]


def test_merge_then_min_length() -> None:
    mask = [True, False, True, False, False, False, True]
    assert mask_to_ranges(mask, 1.0, merge_gap_s=1.0) == [(0.0, 3.0), (6.0, 7.0)]
    assert mask_to_ranges(mask, 1.0, merge_gap_s=1.0, min_len_s=2.0) == [(0.0, 3.0)]


def test_end_clamp_and_empty() -> None:
    assert mask_to_ranges([True, True], 1.0, end_s=1.4) == [(0.0, 1.4)]
    assert mask_to_ranges([], 1.0) == []


def test_overlap_seconds() -> None:
    cover = ranges_overlap_seconds([(0.5, 2.25)], 3)
    assert cover == pytest.approx(np.array([0.5, 1.0, 0.25]))
