"""Stage B: speech to text with word timestamps (faster-whisper, on the server, no per-minute cost).

Memory: the ``base`` model peaks around 350 MB while transcribing, ``small`` around 770 MB. Run this in
a process of its own (``python -m autoedit_pipeline.transcribe``) on small servers so it never stacks on
top of the video analysis. ``AUTOEDIT_WHISPER_MODEL`` picks the model (default ``base``).
"""

from __future__ import annotations

import gc
import itertools
import logging
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from autoedit_schemas.transcript import Sentence, Transcript, Word

from .media import extract_audio, probe
from .models import models_dir

log = logging.getLogger(__name__)

WHISPER_REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
}
CHUNK_S = 600.0  # decode audio ten minutes at a time: bounded memory for long clips
PAUSE_S = 0.7

# Hesitations that are always safe to cut. Ambiguous words ("like", "so", "you know") are left for
# Claude to judge in context.
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "ahh", "hmm", "mm", "mmm", "eh"}


def whisper_model_name() -> str:
    name = os.environ.get("AUTOEDIT_WHISPER_MODEL", "base").strip().lower()
    return name if name in WHISPER_REPOS else "base"


def whisper_dir(name: str | None = None) -> Path:
    return models_dir() / f"whisper-{name or whisper_model_name()}"


def ensure_whisper(name: str | None = None, *, download: bool = True) -> Path | None:
    """Local folder of the Whisper model, downloading it once if allowed. None when unavailable."""
    name = name or whisper_model_name()
    path = whisper_dir(name)
    if (path / "model.bin").is_file():
        return path
    if not download or os.environ.get("AUTOEDIT_OFFLINE") == "1":
        return None
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(WHISPER_REPOS[name], local_dir=str(path))
        for p in path.rglob("*"):
            p.chmod(0o755 if p.is_dir() else 0o644)
    except Exception as exc:
        log.warning("whisper model %s unavailable (%s)", name, exc)
        return None
    return path if (path / "model.bin").is_file() else None


def _clean(word: str) -> str:
    return re.sub(r"[^\w']+", "", word.lower())


def transcribe(
    path: Path,
    *,
    clip_id: str = "",
    language: str | None = None,
    model: str | None = None,
    progress: Callable[[float], None] | None = None,
) -> Transcript:
    """Transcribe the first audio track of ``path``. Returns an empty transcript when there is no audio."""
    info = probe(path)
    name = model or whisper_model_name()
    out = Transcript(clip_id=clip_id, model=f"whisper-{name}", duration_s=info.duration_s)
    if not info.has_audio:
        return out
    folder = ensure_whisper(name)
    if folder is None:
        raise RuntimeError(f"Whisper model '{name}' is not available")
    from faster_whisper import WhisperModel

    threads = max(1, int(os.environ.get("AUTOEDIT_WHISPER_THREADS", "2")))
    wm = WhisperModel(str(folder), device="cpu", compute_type="int8", cpu_threads=threads, num_workers=1)
    words: list[Word] = []
    sentences: list[Sentence] = []
    lang, lang_p = language or "", 0.0
    start = 0.0
    try:
        while start < info.duration_s - 0.05:
            audio = extract_audio(path, 16000, start_s=start, duration_s=CHUNK_S)
            if audio.size < 1600:
                break
            segs, meta = wm.transcribe(
                audio, language=lang or None, word_timestamps=True, beam_size=1, vad_filter=False,
                condition_on_previous_text=False,
            )  # fmt: skip
            if not lang:
                lang, lang_p = meta.language, float(meta.language_probability)
            for s in segs:
                text = s.text.strip()
                if text:
                    sentences.append(Sentence(t_in=round(start + s.start, 3), t_out=round(start + s.end, 3),
                                              text=text[:2000]))  # fmt: skip
                for w in s.words or []:
                    t = w.word.strip()
                    if not t:
                        continue
                    c = _clean(t)
                    prev = _clean(words[-1].text) if words else ""
                    words.append(Word(
                        t_in=round(start + w.start, 3), t_out=round(max(start + w.end, start + w.start + 0.02), 3),
                        text=t[:64], prob=round(min(1.0, max(0.0, float(w.probability))), 3),
                        filler=c in FILLERS, repeat=bool(c) and c == prev and len(c) > 1,
                    ))  # fmt: skip
                if progress:
                    progress(min(0.99, (start + s.end) / max(info.duration_s, 0.1)))
            del audio
            start += CHUNK_S
    finally:
        del wm
        gc.collect()
    pauses = [(a.t_out, b.t_in) for a, b in itertools.pairwise(words) if b.t_in - a.t_out > PAUSE_S]
    return out.model_copy(update={"language": lang, "language_probability": round(lang_p, 3), "words": words,
                                  "sentences": sentences, "pauses": pauses})  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    """``python -m autoedit_pipeline.transcribe MEDIA OUT.json [CLIP_ID]``: transcribe in its own process."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print("usage: python -m autoedit_pipeline.transcribe MEDIA OUT.json [CLIP_ID]", file=sys.stderr)
        return 2
    tr = transcribe(Path(args[0]), clip_id=args[2] if len(args) > 2 else "")
    Path(args[1]).write_text(tr.model_dump_json(indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
