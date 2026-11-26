"""Plan limits (PRD 5.1b / 16). The limit is on total raw length summed across a project's clips."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class PlanLimits:
    name: str
    max_total_raw_s: float
    max_clips: int
    projects_per_month: int | None
    watermark: bool


def _env_float(key: str, default: float) -> float:
    return float(os.environ.get(key, default))


def plan_limits(plan: str) -> PlanLimits:
    if plan == "paid":
        return PlanLimits(
            name="paid",
            max_total_raw_s=_env_float("AUTOEDIT_PAID_MAX_TOTAL_RAW_S", 30 * 60),
            max_clips=int(_env_float("AUTOEDIT_MAX_CLIPS", 20)),
            projects_per_month=None,
            watermark=False,
        )
    return PlanLimits(
        name="free",
        max_total_raw_s=_env_float("AUTOEDIT_FREE_MAX_TOTAL_RAW_S", 3 * 60),
        max_clips=int(_env_float("AUTOEDIT_MAX_CLIPS", 20)),
        projects_per_month=int(_env_float("AUTOEDIT_FREE_PROJECTS_PER_MONTH", 3)),
        watermark=True,
    )


def fmt_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    m, s = divmod(round(seconds), 60)
    return f"{m}:{s:02d}"
