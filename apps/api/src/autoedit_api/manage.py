"""Operational commands: ``python -m autoedit_api.manage {migrate,worker,purge,init-storage}``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False, help="AutoEdit API operations")


@app.command()
def migrate() -> None:
    """Apply database migrations (Alembic) to the configured database."""
    from alembic import command
    from alembic.config import Config

    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    cfg = Config(str(ini))
    cfg.set_main_option("script_location", str(ini.parent / "alembic"))
    command.upgrade(cfg, "head")
    typer.echo("migrations applied")


@app.command()
def worker(
    processes: int = typer.Option(
        0, "--processes", "-n", help="Clips analysed in parallel (default: half the CPU cores)."
    ),
    burst: bool = typer.Option(False, help="Exit when the queue is empty."),
) -> None:
    """Run background workers for the analysis queue. Each clip is one job, so N processes analyse
    N clips of a project at the same time (PRD 5.1b)."""
    import os

    from rq import Worker
    from rq.worker_pool import WorkerPool

    from .logging import configure_logging
    from .queue import ANALYSIS_QUEUE, redis_conn

    configure_logging()
    n = processes or max(1, (os.cpu_count() or 2) // 2)
    if n == 1:
        Worker([ANALYSIS_QUEUE], connection=redis_conn()).work(burst=burst, with_scheduler=True)
    else:
        WorkerPool([ANALYSIS_QUEUE], connection=redis_conn(), num_workers=n).start(burst=burst)


@app.command("run-job")
def run_job(kind: str, obj_id: str) -> None:
    """Run one background job in this process (used by AUTOEDIT_QUEUE_MODE=process)."""
    from .logging import configure_logging
    from .queue import job_function

    configure_logging()
    try:
        job_function(kind)(obj_id)
    except Exception:
        raise typer.Exit(1) from None


@app.command()
def purge() -> None:
    """Delete projects past the retention window and abandoned uploads."""
    from .jobs import purge_expired

    typer.echo(json.dumps(purge_expired()))


@app.command("init-storage")
def init_storage() -> None:
    """Create the S3 bucket if missing (MinIO/R2 dev setups)."""
    from .storage import S3Storage, get_storage

    storage = get_storage()
    if isinstance(storage, S3Storage):
        storage.ensure_bucket()
        typer.echo(f"bucket {storage.bucket} ready")
    else:
        typer.echo("local storage: nothing to do")


if __name__ == "__main__":
    sys.exit(app())
