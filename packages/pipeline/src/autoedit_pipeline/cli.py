"""``autoedit`` command line interface."""

from __future__ import annotations

import json
import logging
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TextColumn, TimeElapsedColumn
from rich.table import Table

from autoedit_schemas import EDL, Composition, SchemaValidationError, json_schemas, lint_edl, parse_model
from autoedit_schemas.media import MediaInfo
from autoedit_schemas.signals import Signals

from . import __version__
from .analyze import analyze_clip, default_cache_root
from .media import MediaError, probe, require_binaries
from .models import MODELS, download_all, models_dir
from .plans import fmt_duration, plan_limits

app = typer.Typer(
    name="autoedit",
    help="AutoEdit: turn raw footage into a finished edit.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
models_app = typer.Typer(help="Manage on-device ML models.", no_args_is_help=True)
schema_app = typer.Typer(help="Data contracts (EDL, Composition, signals).", no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(schema_app, name="schema")

console = Console()
err = Console(stderr=True)

STAGE_LABELS = {
    "hash": "Hashing",
    "probe": "Probing",
    "proxy": "Making proxy",
    "signals": "Analyzing signals",
    "done": "Done",
}


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"autoedit {__version__}")
        raise typer.Exit


@app.callback()
def main(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show debug logs.")] = False,
    version: Annotated[
        bool, typer.Option("--version", callback=_version_callback, is_eager=True, help="Show version.")
    ] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )


# ---------------------------------------------------------------------------------------- analyze


def _worker(
    src: str, cache_root: str, clip_id: str, use_cache: bool, queue: Any
) -> dict[str, Any]:  # runs in a child process
    def report(stage: str, fraction: float) -> None:
        queue.put((clip_id, stage, fraction))

    result = analyze_clip(Path(src), Path(cache_root), clip_id=clip_id, progress=report, use_cache=use_cache)
    queue.put((clip_id, "done", 1.0))
    return {
        "clip_id": result.clip_id,
        "sha256": result.sha256,
        "signals_path": str(result.signals_path),
        "proxy_path": str(result.proxy_path),
        "cached": result.cached,
        "elapsed_s": result.elapsed_s,
    }


def _limit_line(total: float, plan: str) -> str:
    lim = plan_limits(plan)
    pct = total / lim.max_total_raw_s * 100
    ok = total <= lim.max_total_raw_s
    style = "green" if ok else "red"
    return (
        f"[{style}]{fmt_duration(total)} / {fmt_duration(lim.max_total_raw_s)}[/] "
        f"({pct:.0f}% of the {lim.name} plan){'' if ok else ' [red]over limit[/]'}"
    )


@app.command()
def analyze(
    clips: Annotated[
        list[Path], typer.Argument(help="Video files, in project order.", exists=True, dir_okay=False)
    ],
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("autoedit-out"),
    plan: Annotated[
        str | None,
        typer.Option(help="Enforce a plan's total-length limit: 'free' or 'paid'. Default: report only."),
    ] = None,
    jobs: Annotated[int, typer.Option("--jobs", "-j", help="Clips analyzed in parallel.")] = 0,
    cache_dir: Annotated[
        Path | None, typer.Option(help="Analysis cache (default ./.autoedit-cache).")
    ] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Recompute everything.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Print the project summary as JSON.")] = False,
) -> None:
    """Probe, proxy and run the local signal pass on one or more clips (Stage A, no AI cost).

    Writes OUT/<clip>/signals.json for every clip plus OUT/project.json.
    """
    require_binaries()
    if plan not in (None, "free", "paid"):
        raise typer.BadParameter("plan must be 'free' or 'paid'")

    # 1. Probe everything first: fast, and lets us enforce the total-length limit up front.
    infos: list[tuple[str, Path, MediaInfo]] = []
    for i, path in enumerate(clips, start=1):
        try:
            infos.append((f"c{i}", path, probe(path)))
        except MediaError as exc:
            err.print(f"[red]✗[/] {path.name}: {exc} [dim]({exc.code})[/]")
            raise typer.Exit(1) from exc
    total = sum(info.duration_s for _, _, info in infos)

    if not as_json:
        console.print(
            f"\n[bold]AutoEdit[/] {__version__}  ·  {len(infos)} clip(s)  ·  raw length {_limit_line(total, plan or 'free')}"
        )
        if plan is None:
            console.print(f"[dim]paid plan: {_limit_line(total, 'paid')}[/]")
    if plan is not None:
        lim = plan_limits(plan)
        if len(infos) > lim.max_clips:
            err.print(f"[red]✗[/] {len(infos)} clips exceeds the {lim.max_clips}-clip limit")
            raise typer.Exit(2)
        if total > lim.max_total_raw_s:
            err.print(
                f"[red]✗[/] total raw length {fmt_duration(total)} exceeds the {lim.name} plan limit of "
                f"{fmt_duration(lim.max_total_raw_s)}. Remove a clip or upgrade."
            )
            raise typer.Exit(2)

    # 2. Analyze clips in parallel processes, each running its own passes concurrently.
    cache_root = cache_dir or default_cache_root()
    workers = jobs or max(1, min(len(infos), (os.cpu_count() or 2) // 2))
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    results: dict[str, dict[str, Any]] = {}
    failures: dict[str, str] = {}

    ctx = mp.get_context("spawn")
    with ctx.Manager() as manager:
        queue = manager.Queue()
        progress = Progress(
            TextColumn("[bold]{task.fields[cid]}[/] {task.description}", justify="left"),
            BarColumn(bar_width=28),
            TextColumn("{task.percentage:>3.0f}%"),
            TextColumn("[dim]{task.fields[stage]}[/]"),
            TimeElapsedColumn(),
            console=err,
            disable=as_json,
            transient=False,
        )
        tasks: dict[str, TaskID] = {}
        with progress, ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            futures: dict[str, Future[dict[str, Any]]] = {}
            for cid, path, info in infos:
                label = path.name if len(path.name) <= 34 else path.name[:31] + "…"
                tasks[cid] = progress.add_task(label, total=1.0, cid=cid, stage="queued")
                futures[cid] = pool.submit(_worker, str(path), str(cache_root), cid, not no_cache, queue)
                del info
            pending = set(futures)
            while pending:
                while not queue.empty():
                    cid, stage, fraction = queue.get()
                    progress.update(tasks[cid], completed=fraction, stage=STAGE_LABELS.get(stage, stage))
                for cid in list(pending):
                    fut = futures[cid]
                    if fut.done():
                        pending.discard(cid)
                        try:
                            results[cid] = fut.result()
                            progress.update(tasks[cid], completed=1.0, stage="[green]done[/]")
                        except Exception as exc:
                            failures[cid] = f"{type(exc).__name__}: {exc}"
                            progress.update(tasks[cid], stage="[red]failed[/]")
                time.sleep(0.1)

    # 3. Write outputs.
    project: dict[str, Any] = {
        "autoedit_version": __version__,
        "total_raw_duration_s": round(total, 3),
        "limits": {p: plan_limits(p).max_total_raw_s for p in ("free", "paid")},
        "elapsed_s": round(time.perf_counter() - t0, 2),
        "clips": [],
    }
    table = Table(title="Stage A · local signals", title_justify="left", header_style="bold", pad_edge=False)
    for col, justify in [
        ("clip", "left"), ("file", "left"), ("length", "right"), ("video", "left"), ("audio", "left"),
        ("scenes", "right"), ("speech", "right"), ("music", "right"), ("faces", "right"),
        ("dead", "right"), ("usable", "right"), ("flags", "left"), ("time", "right"),
    ]:  # fmt: skip
        table.add_column(col, justify=justify)  # type: ignore[arg-type]

    for order, (cid, path, info) in enumerate(infos, start=1):
        entry: dict[str, Any] = {"id": cid, "order": order, "file": str(path), "duration_s": info.duration_s}
        if cid in failures:
            entry["error"] = failures[cid]
            project["clips"].append(entry)
            table.add_row(
                cid,
                path.name,
                fmt_duration(info.duration_s),
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                f"[red]{failures[cid][:60]}[/]",
                "",
            )
            continue
        res = results[cid]
        clip_dir = out / f"{cid}-{path.stem}"[:80]
        clip_dir.mkdir(parents=True, exist_ok=True)
        signals = parse_model(Signals, Path(res["signals_path"]).read_text())
        (clip_dir / "signals.json").write_text(signals.model_dump_json(indent=2))
        entry.update(
            sha256=res["sha256"],
            signals=str(clip_dir / "signals.json"),
            proxy=res["proxy_path"],
            cached=res["cached"],
            elapsed_s=round(res["elapsed_s"], 2),
        )
        project["clips"].append(entry)

        sm = signals.summary
        flag_counts: dict[str, int] = {}
        for f in signals.quality_flags:
            flag_counts[f.reason] = flag_counts.get(f.reason, 0) + 1
        a = sm.audio
        table.add_row(
            cid,
            path.name if len(path.name) <= 28 else path.name[:27] + "…",
            fmt_duration(info.duration_s),
            f"{info.display_width}x{info.display_height} {info.fps:g}fps",
            "none"
            if a is None
            else (f"{a.integrated_lufs:.0f} LUFS" if a.integrated_lufs is not None else "silent"),
            str(sm.video.scene_count),
            "–" if a is None else f"{a.speech_ratio:.0%}",
            "–" if a is None else f"{a.music_ratio:.0%}",
            f"{sm.video.face_ratio:.0%}",
            f"{sm.dead_ratio:.0%}",
            fmt_duration(sm.usable_seconds),
            ", ".join(f"{k}×{v}" for k, v in sorted(flag_counts.items())) or "[green]clean[/]",
            "cached" if res["cached"] else f"{res['elapsed_s']:.1f}s",
        )

    (out / "project.json").write_text(json.dumps(project, indent=2))
    if as_json:
        console.print_json(json.dumps(project))
    else:
        console.print()
        console.print(table)
        console.print(
            f"\n[green]✓[/] wrote {out}/project.json and {len(results)} signals.json file(s) in {project['elapsed_s']:.1f}s"
        )
    if failures:
        raise typer.Exit(1)


# ------------------------------------------------------------------------------------------ other


@app.command("probe")
def probe_cmd(file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """Print ffprobe facts for a file as JSON."""
    require_binaries()
    try:
        console.print_json(probe(file).model_dump_json())
    except MediaError as exc:
        err.print(f"[red]✗[/] {exc} ({exc.code})")
        raise typer.Exit(1) from exc


@models_app.command("download")
def models_download() -> None:
    """Download all on-device models into the models directory."""
    for name, path in download_all().items():
        mark = "[green]✓[/]" if path else "[red]✗[/]"
        console.print(f"{mark} {name:6s} {path or 'unavailable (fallback detector will be used)'}")
    console.print(f"[dim]{models_dir()}[/]")


@models_app.command("list")
def models_list() -> None:
    """Show models and whether they are present."""
    for spec in MODELS.values():
        path = models_dir() / spec.filename
        mark = "[green]✓[/]" if path.is_file() else "[yellow]·[/]"
        console.print(f"{mark} {spec.name:6s} {spec.filename:34s} {spec.purpose}")


@schema_app.command("export")
def schema_export(
    out: Annotated[Path, typer.Argument(help="Directory for the JSON Schema files.")] = Path("schemas"),
) -> None:
    """Write JSON Schema files for every contract (used by the web client and LLM prompts)."""
    out.mkdir(parents=True, exist_ok=True)
    for name, schema in json_schemas().items():
        (out / f"{name}.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
        console.print(f"[green]✓[/] {out / f'{name}.schema.json'}")


@app.command()
def validate(
    kind: Annotated[str, typer.Argument(help="'edl', 'composition' or 'signals'.")],
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate a JSON document against its schema."""
    models: dict[str, type[EDL] | type[Composition] | type[Signals]] = {
        "edl": EDL, "composition": Composition, "signals": Signals,
    }  # fmt: skip
    if kind not in models:
        raise typer.BadParameter("kind must be edl, composition or signals")
    try:
        doc = parse_model(models[kind], file.read_text())
    except SchemaValidationError as exc:
        err.print(f"[red]✗ invalid[/]\n{exc.feedback}")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓ valid {kind}[/]")
    if isinstance(doc, EDL):
        console.print(f"  output duration {doc.output_duration_s:.2f}s, {len(doc.segments)} segment(s)")
        for issue in lint_edl(doc):
            console.print(f"  [yellow]{issue.level}[/] {issue.code}: {issue.message}")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
