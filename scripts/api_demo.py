"""End-to-end API demo: resumable upload -> project -> live progress (SSE) -> signals.

    uv run python scripts/api_demo.py clip1.mp4 clip2.mov --api http://localhost:8000

Uses only the public HTTP API, exactly as the web app will.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx

CHUNK = 4 * 1024 * 1024


def upload(client: httpx.Client, path: Path) -> dict:
    data_len = path.stat().st_size
    r = client.post("/v1/uploads", json={"filename": path.name, "size_bytes": data_len})
    r.raise_for_status()
    up = r.json()
    sha = hashlib.sha256()
    offset = 0
    with path.open("rb") as f:
        while offset < data_len:
            f.seek(offset)
            chunk = f.read(CHUNK)
            for attempt in range(5):
                try:
                    resp = client.patch(
                        f"/v1/uploads/{up['id']}",
                        content=chunk,
                        headers={
                            "Upload-Offset": str(offset),
                            "Content-Type": "application/offset+octet-stream",
                        },
                    )
                    if resp.status_code == 409:  # resume from the server's offset
                        offset = int(client.head(f"/v1/uploads/{up['id']}").headers["Upload-Offset"])
                        break
                    resp.raise_for_status()
                    sha.update(chunk)
                    offset = int(resp.headers["Upload-Offset"])
                    break
                except httpx.TransportError:
                    time.sleep(2**attempt)
            print(f"\r  ↑ {path.name}: {offset / data_len:6.1%}", end="", flush=True)
    print()
    r = client.post(f"/v1/uploads/{up['id']}/complete", json={"sha256": sha.hexdigest()})
    if r.status_code >= 400:
        print(f"  ✗ {r.json()['error']['message']}")
        sys.exit(1)
    return r.json()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clips", nargs="+", type=Path)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--user", default="usr_demo")
    ap.add_argument("--out", type=Path, default=Path("api-demo-out"))
    ap.add_argument("--password", default=None, help="Access password, if the server sets one")
    args = ap.parse_args()

    auth = httpx.BasicAuth("autocut", args.password) if args.password else None
    with httpx.Client(base_url=args.api, headers={"X-User-Id": args.user}, timeout=120, auth=auth) as client:
        print("Uploading")
        uploads = [upload(client, p) for p in args.clips]
        for u in uploads:
            m = u["media"]
            print(f"  ✓ {u['filename']}: {m['duration_s']:.1f}s {m['display_width']}x{m['display_height']} "
                  f"{m['fps']:g}fps audio={m['has_audio']}")  # fmt: skip

        r = client.post(
            "/v1/projects",
            json={"mode": "edit", "title": "API demo", "upload_ids": [u["id"] for u in uploads]},
        )
        if r.status_code >= 400:
            print(f"✗ {r.json()['error']['code']}: {r.json()['error']['message']}")
            sys.exit(1)
        project = r.json()
        meter = project["meter"]
        print(f"\nProject {project['id']}  ·  {meter['total_raw_s']:.0f}s of {meter['limit_s']:.0f}s "
              f"({meter['fraction']:.0%})  ·  {meter['clip_count']}/{meter['max_clips']} clips")  # fmt: skip

        t0 = time.perf_counter()
        with client.stream("GET", f"/v1/projects/{project['id']}/events", timeout=None) as stream:
            event = None
            for line in stream.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and event == "project":
                    snap = json.loads(line[5:])
                    clips = " | ".join(
                        f"{c['position']}:{c['status'][:5]} {c['progress']:4.0%}" for c in snap["clips"]
                    )
                    print(
                        f"\r  {time.perf_counter() - t0:5.1f}s  {snap['status']:<9} {clips}",
                        end="",
                        flush=True,
                    )
                elif event == "settled":
                    break
        print()

        project = client.get(f"/v1/projects/{project['id']}").json()
        args.out.mkdir(parents=True, exist_ok=True)
        for c in project["clips"]:
            if c["status"] != "ready":
                print(f"  ✗ clip {c['position']} {c['filename']}: {c['error_code']} {c['error']}")
                continue
            sig = client.get(c["signals_url"]).json()
            dest = args.out / f"{c['position']:02d}-{Path(c['filename']).stem}.signals.json"
            dest.write_text(json.dumps(sig, indent=2))
            s = sig["summary"]
            print(f"  ✓ {c['position']}. {c['filename']:<38} dead {s['dead_ratio']:4.0%}  usable {s['usable_seconds']:6.1f}s  "
                  f"scenes {s['video']['scene_count']:2d}  faces {s['video']['face_ratio']:4.0%}  → {dest}")  # fmt: skip


if __name__ == "__main__":
    main()
