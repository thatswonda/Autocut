"""A minimal Chrome DevTools Protocol client over ``--remote-debugging-pipe``.

The renderer only needs to open a page, evaluate JavaScript and take screenshots. Talking to Chromium
directly (no Node.js driver in between) saves ~135 MB of memory per render, which is what lets motion
graphics run on a 512 MB server.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

LEAN_FLAGS = [
    "--headless", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--font-render-hinting=none",
    "--allow-file-access-from-files", "--disable-web-security", "--single-process", "--no-zygote",
    "--renderer-process-limit=1", "--disable-extensions", "--disable-background-networking",
    "--disable-component-update", "--disable-sync", "--no-first-run", "--mute-audio",
    "--disable-features=site-per-process,Translate,MediaRouter,OptimizationHints,PaintHolding",
    "--js-flags=--max-old-space-size=128", "--num-raster-threads=1", "--hide-scrollbars", "--force-color-profile=srgb",
    "--remote-debugging-pipe",
]  # fmt: skip


class CDPError(RuntimeError):
    pass


def find_chromium() -> str:
    for cand in (
        os.environ.get("AUTOEDIT_CHROMIUM_PATH"),
        shutil.which("chrome-headless-shell"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
    ):
        if cand and Path(cand).exists():
            return cand
    raise CDPError(
        "No Chromium found. Set AUTOEDIT_CHROMIUM_PATH to a Chromium or chrome-headless-shell binary."
    )


class Browser:
    """One Chromium process with one page, driven synchronously."""

    def __init__(self, width: int, height: int, timeout: float = 60.0):
        self.timeout = timeout
        # fd 3: we write commands, Chromium reads. fd 4: Chromium writes replies, we read.
        cmd_r, self._cmd_w = os.pipe()
        self._out_r, out_w = os.pipe()

        def wire() -> None:  # runs in the child before exec
            # Duplicate first so the pipes cannot collide with 3 and 4 while being moved there.
            a, b = os.dup(cmd_r), os.dup(out_w)
            os.dup2(a, 3)
            os.dup2(b, 4)

        self.proc = subprocess.Popen(
            [find_chromium(), *LEAN_FLAGS, f"--window-size={width},{height}", "about:blank"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # 3 and 4 must be listed too, or subprocess closes them after wire() runs.
            pass_fds=(cmd_r, out_w, 3, 4), preexec_fn=wire,
        )  # fmt: skip
        os.close(cmd_r)
        os.close(out_w)
        self._reader = os.fdopen(self._out_r, "rb", buffering=0)
        self._buf = b""
        self._next = 0
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        target = self.call("Target.createTarget", {"url": "about:blank"})["targetId"]
        self.session = self.call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride",
                  {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False}, session=True)  # fmt: skip
        self.call("Page.enable", session=True)

    # ---------------------------------------------------------------------------------- protocol
    def _read_message(self) -> dict[str, Any]:
        while b"\0" not in self._buf:
            chunk = self._reader.read(1 << 20)
            if not chunk:
                raise CDPError("Chromium exited unexpectedly")
            self._buf += chunk
        raw, self._buf = self._buf.split(b"\0", 1)
        return json.loads(raw)

    def call(
        self, method: str, params: dict[str, Any] | None = None, *, session: bool = False
    ) -> dict[str, Any]:
        with self._lock:
            self._next += 1
            msg: dict[str, Any] = {"id": self._next, "method": method, "params": params or {}}
            if session:
                msg["sessionId"] = self.session
            os.write(self._cmd_w, json.dumps(msg).encode() + b"\0")
            while True:
                reply = self._read_message()
                if reply.get("id") == self._next:
                    if "error" in reply:
                        raise CDPError(f"{method}: {reply['error'].get('message')}")
                    return reply.get("result", {})
                self._events.append(reply)

    def wait_event(self, method: str) -> dict[str, Any]:
        for i, ev in enumerate(self._events):
            if ev.get("method") == method:
                return self._events.pop(i)
        while True:
            ev = self._read_message()
            if ev.get("method") == method:
                return ev
            self._events.append(ev)

    # ---------------------------------------------------------------------------------- helpers
    def goto(self, url: str) -> None:
        self._events.clear()
        self.call("Page.navigate", {"url": url}, session=True)
        self.wait_event("Page.loadEventFired")

    def evaluate(self, expression: str) -> Any:
        res = self.call("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True},
                        session=True)  # fmt: skip
        if "exceptionDetails" in res:
            detail = (
                res["exceptionDetails"].get("exception", {}).get("description") or res["exceptionDetails"]
            )
            raise CDPError(f"page script failed: {str(detail)[:300]}")
        return res.get("result", {}).get("value")

    def screenshot(self, fmt: str = "jpeg", quality: int = 92) -> bytes:
        params: dict[str, Any] = {"format": fmt, "optimizeForSpeed": True}
        if fmt == "jpeg":
            params["quality"] = quality
        return base64.b64decode(self.call("Page.captureScreenshot", params, session=True)["data"])

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.call("Browser.close")
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        for f in (self._reader,):
            with contextlib.suppress(OSError):
                f.close()
        with contextlib.suppress(OSError):
            os.close(self._cmd_w)

    def __enter__(self) -> Browser:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
