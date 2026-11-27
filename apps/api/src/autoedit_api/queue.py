"""Job dispatch: RQ + Redis, a child process per job (single small server), or inline (tests).

Job kinds: ``analyze`` (clip id), ``motion_plan`` (project id), ``scene_regen`` (project id), ``render``
(render id).
"""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from .settings import get_settings

log = logging.getLogger(__name__)

ANALYSIS_QUEUE = "analysis"

JOBS: dict[str, str] = {
    "analyze": "autoedit_api.jobs:analyze_clip_job",
    "motion_plan": "autoedit_api.jobs_media:motion_plan_job",
    "scene_regen": "autoedit_api.jobs_media:scene_regen_job",
    "render": "autoedit_api.jobs_media:render_job",
    "motion_revise": "autoedit_api.jobs_media:motion_revise_job",
}


def job_function(kind: str) -> Callable[[str], Any]:
    module, name = JOBS[kind].split(":")
    return getattr(importlib.import_module(module), name)  # type: ignore[no-any-return,unused-ignore]


@lru_cache
def redis_conn() -> Any:
    from redis import Redis

    return Redis.from_url(get_settings().redis_url)


_process_lock = threading.Lock()

# A job that kills the whole server (out of memory) would otherwise be resumed on every restart and take
# the server down again in a loop. Each resume is counted on disk; past the limit the job is failed instead.
MAX_RESUMES = 2
RESTART_LOOP = -3  # pseudo exit code for "stopped the server repeatedly"


def _resume_marker(kind: str, obj_id: str) -> Path:
    return get_settings().data_dir / "resumes" / f"{kind}-{obj_id}"


def resume(kind: str, obj_id: str) -> bool:
    """Resume a job a restart interrupted, unless it already did so ``MAX_RESUMES`` times."""
    marker = _resume_marker(kind, obj_id)
    try:
        count = int(marker.read_text() or 0)
    except (OSError, ValueError):
        count = 0
    if count >= MAX_RESUMES:
        from .jobs import mark_job_crashed

        mark_job_crashed(kind, obj_id, RESTART_LOOP)
        marker.unlink(missing_ok=True)
        log.error("%s job for %s stopped the server %s times; not resuming it", kind, obj_id, count)
        return False
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(str(count + 1))
    enqueue(kind, obj_id)
    return True


def _run_in_child(kind: str, obj_id: str) -> None:
    """Run one job in a fresh Python process; jobs run strictly one at a time (memory stays bounded)."""
    settings = get_settings()
    with _process_lock:
        if kind == "render":
            from .jobs_edit import transcribe_before_render

            transcribe_before_render(obj_id)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "autoedit_api.manage", "run-job", kind, obj_id],
                timeout=settings.job_timeout_s,
                check=False,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = -1
    _resume_marker(kind, obj_id).unlink(
        missing_ok=True
    )  # it ended (well or not) without taking the server down
    if code != 0:
        from .jobs import mark_job_crashed

        mark_job_crashed(kind, obj_id, code)


def enqueue(kind: str, obj_id: str) -> None:
    settings = get_settings()
    if kind not in JOBS:
        raise ValueError(f"unknown job kind {kind}")
    if settings.queue_mode == "process":
        threading.Thread(
            target=_run_in_child, args=(kind, obj_id), name=f"job-{kind}-{obj_id}", daemon=True
        ).start()
        return
    if settings.queue_mode == "inline":
        try:
            job_function(kind)(obj_id)
        except Exception:
            log.exception("inline %s job failed for %s", kind, obj_id)
        return

    from rq import Queue, Retry

    queue = Queue(ANALYSIS_QUEUE, connection=redis_conn())
    retries = settings.job_retries if kind == "analyze" else 0
    queue.enqueue(
        JOBS[kind].replace(":", "."),
        obj_id,
        # One analysis job per clip; other kinds may legitimately repeat (re-render, regenerate).
        job_id=f"analyze-{obj_id}" if kind == "analyze" else f"{kind}-{obj_id}-{uuid.uuid4().hex[:8]}",
        job_timeout=settings.job_timeout_s,
        retry=Retry(max=retries, interval=[15, 60, 180][:retries] or [15]) if retries else None,
        result_ttl=3600,
        failure_ttl=7 * 24 * 3600,
        description=f"{kind} {obj_id}",
    )


def enqueue_clip_analysis(clip_id: str) -> None:
    enqueue("analyze", clip_id)


def ping() -> bool:
    if get_settings().queue_mode in ("inline", "process"):
        return True
    try:
        return bool(redis_conn().ping())
    except Exception:
        return False
