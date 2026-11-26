"""Build a self-contained, server-free preview of the Autocut web app.

    uv run autoedit analyze a.mp4 b.mov -o autoedit-out
    uv run python scripts/build_preview.py autoedit-out -o dist/preview

The output folder holds ``index.html`` (the app with its CSS and JS inlined and a stand-in API) plus
``sample/`` (signals and 540p proxies of the analyzed clips). Host it anywhere static.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps/api/src/autoedit_api/web"
DEMO_JS = ROOT / "scripts/preview/demo.js"


def build_sample(analysis_dir: Path, out: Path) -> None:
    project = json.loads((analysis_dir / "project.json").read_text())
    sample = out / "sample"
    sample.mkdir(parents=True, exist_ok=True)
    clips, signals = [], {}
    for c in project["clips"]:
        if "signals" not in c:
            continue
        sig = json.loads(Path(c["signals"]).read_text())
        cid = f"sample_{c['id']}"
        shutil.copyfile(c["proxy"], sample / f"{c['id']}.mp4")
        (sample / f"{c['id']}.signals.json").write_text(json.dumps(sig, separators=(",", ":")))
        signals[cid] = f"sample/{c['id']}.signals.json"
        clips.append(
            {
                "id": cid,
                "upload_id": f"upl_{c['id']}",
                "filename": Path(c["file"]).name,
                "position": c["order"],
                "duration_s": c["duration_s"],
                "media": sig["media"],
                "status": "ready",
                "stage": None,
                "progress": 1.0,
                "error_code": None,
                "error": None,
                "signals_url": signals[cid],
                "proxy_url": f"sample/{c['id']}.mp4",
            }
        )
    total = sum(c["duration_s"] for c in clips)
    now = datetime.now(UTC).isoformat()
    doc = {
        "project": {
            "id": "sample",
            "mode": "edit",
            "title": "Sample project",
            "prompt": "Cut this into a punchy 45-second Reel with word-by-word captions",
            "options": {},
            "status": "ready",
            "stage": None,
            "progress": 1.0,
            "meter": {
                "total_raw_s": total,
                "limit_s": 1800,
                "remaining_s": 1800 - total,
                "fraction": total / 1800,
                "clip_count": len(clips),
                "max_clips": 20,
            },
            "clips": clips,
            "created_at": now,
            "updated_at": now,
        },
        "signals": signals,
    }
    (sample / "project.json").write_text(json.dumps(doc, indent=1))


def build_page(out: Path) -> None:
    html = (WEB / "index.html").read_text()
    # The artifact host supplies the document skeleton; keep only head content and body content.
    html = re.sub(r"<!doctype html>\s*|</?html[^>]*>\s*|</?head>\s*|</?body>\s*", "", html, flags=re.I)
    html = re.sub(r'<meta charset="utf-8">\s*|<meta name="viewport"[^>]*>\s*', "", html)
    css = (WEB / "app.css").read_text()
    html = html.replace('<link rel="stylesheet" href="app.css">', f"<style>\n{css}\n</style>")
    scripts = (
        f"<script>\n{DEMO_JS.read_text()}\n</script>\n<script>\n{(WEB / 'app.js').read_text()}\n</script>"
    )
    html = html.replace('<script src="app.js"></script>', scripts)
    (out / "index.html").write_text(html)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("analysis_dir", type=Path, help="Output folder of `autoedit analyze`")
    ap.add_argument("-o", "--out", type=Path, default=Path("dist/preview"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    build_sample(args.analysis_dir, args.out)
    build_page(args.out)
    print(f"preview written to {args.out}")


if __name__ == "__main__":
    main()
