"""Silence chatty native libraries (MediaPipe / LiteRT write straight to file descriptor 2)."""

from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections.abc import Iterator

_lock = threading.Lock()


@contextlib.contextmanager
def quiet_native_stderr() -> Iterator[None]:
    """Temporarily point fd 2 at /dev/null. Set ``AUTOEDIT_NATIVE_LOGS=1`` to keep the output."""
    if os.environ.get("AUTOEDIT_NATIVE_LOGS") == "1":
        yield
        return
    with _lock:
        sys.stderr.flush()
        saved = os.dup(2)
        devnull = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(devnull, 2)
            yield
        finally:
            os.dup2(saved, 2)
            os.close(saved)
            os.close(devnull)
