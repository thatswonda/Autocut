"""Gemini voiceover against a stand-in Gemini server (no network), and the fallback to Piper."""

from __future__ import annotations

import base64
import json
import wave
from pathlib import Path

import httpx
import pytest

from autoedit_motion import voice


def _mock(monkeypatch: pytest.MonkeyPatch, handler) -> list[dict]:  # type: ignore[no-untyped-def]
    seen: list[dict] = []
    real = httpx.Client

    def client(*a, **kw):  # type: ignore[no-untyped-def]
        def wrapped(req: httpx.Request) -> httpx.Response:
            seen.append(
                {
                    "url": str(req.url),
                    "key": req.headers.get("x-goog-api-key"),
                    "body": json.loads(req.content),
                }
            )
            return handler(req)

        kw.pop("timeout", None)
        return real(transport=httpx.MockTransport(wrapped))

    monkeypatch.setattr(httpx, "Client", client)
    return seen


def _audio(seconds: float) -> httpx.Response:
    pcm = b"\x00\x10" * int(24000 * seconds)
    body = {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;codec=pcm;rate=24000",
                                                                    "data": base64.b64encode(pcm).decode()}}]}}]}  # fmt: skip
    return httpx.Response(200, json=body)


def test_gemini_voice_lines_and_model_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", ' "test-key" ')
    monkeypatch.setenv("GEMINI_TTS_MODEL", "gemini-future-tts")

    def handler(req: httpx.Request) -> httpx.Response:
        if "gemini-future-tts" in str(req.url):
            return httpx.Response(404, json={"error": {"message": "models/gemini-future-tts is not found"}})
        return _audio(1.5)

    seen = _mock(monkeypatch, handler)
    lines, note = voice.speak_best([(0.0, "Fresh coffee."), (2.0, "Order today.")], tmp_path,
                                   voice="Puck", style="Speak in a warm Nigerian English accent.")  # fmt: skip
    assert note == "" and [ln.duration_s for ln in lines] == [1.5, 1.5]
    with wave.open(str(lines[0].path)) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (24000, 1, 2)
    first_ok = next(s for s in seen if "flash" in s["url"])
    assert first_ok["key"] == "test-key"  # pasted quotes and spaces stripped
    cfg = first_ok["body"]["generationConfig"]
    assert cfg["responseModalities"] == ["AUDIO"]
    assert cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Puck"
    assert "Nigerian English" in first_ok["body"]["contents"][0]["parts"][0]["text"]


def test_rejected_key_falls_back_to_server_voice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "bad")
    _mock(monkeypatch, lambda req: httpx.Response(403, json={"error": {"message": "API key not valid"}}))
    spoken: list[tuple[float, str]] = []

    def fake_piper(lines, out_dir, v=voice.DEFAULT_VOICE):  # type: ignore[no-untyped-def]
        spoken.extend(lines)
        return []

    monkeypatch.setattr(voice, "speak", fake_piper)
    _lines, note = voice.speak_best([(0.0, "Hello")], tmp_path, voice="Kore")
    assert spoken == [(0.0, "Hello")] and "GEMINI_API_KEY" in note
