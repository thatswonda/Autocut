"""Job dispatch: RQ + Redis in normal operation, inline for tests and single-process demos."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from .settings import get_settings

log = logging.getLogger(__name__)

ANALYSIS_QUEUE = "analysis"


@lru_cache
def redis_conn() -> Any:
    from redis import Redis

    return Redis.from_url(get_settings().redis_url)


def enqueue_clip_analysis(clip_id: str) -> None:
    settings = get_settings()
    if settings.queue_mode == "inline":
        from .jobs import analyze_clip_job

        try:
            analyze_clip_job(clip_id)
        except Exception:
            log.exception("inline analysis failed for %s", clip_id)
        return

    from rq import Queue, Retry

    queue = Queue(ANALYSIS_QUEUE, connection=redis_conn())
    retries = settings.job_retries
    queue.enqueue(
        "autoedit_api.jobs.analyze_clip_job",
        clip_id,
        job_id=f"analyze-{clip_id}",  # one job per clip: re-enqueueing is a no-op while it is pending
        job_timeout=settings.job_timeout_s,
        retry=Retry(max=retries, interval=[15, 60, 180][:retries] or [15]) if retries else None,
        result_ttl=3600,
        failure_ttl=7 * 24 * 3600,
        description=f"Stage A analysis for clip {clip_id}",
    )


def ping() -> bool:
    if get_settings().queue_mode == "inline":
        return True
    try:
        return bool(redis_conn().ping())
    except Exception:
        return False
