"""Tests for the RAGnos local streaming TTS overlay."""
from __future__ import annotations

import io
import importlib.util
import wave
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "ragnos_governance_local_tts", Path(__file__).parent / "local_tts.py"
)
local_tts = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(local_tts)


def _pcm_wav(payload=b"\x01\x00\x02\x00", *, rate=24000):
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(payload)
    return out.getvalue()


def test_available_requires_explicit_loopback_url(monkeypatch):
    monkeypatch.setattr(local_tts, "_config", lambda: {"url": "http://127.0.0.1:8766/api/voice/stream"})
    assert local_tts.RagnosLocalHTTPStreamer.available() is True
    monkeypatch.setattr(local_tts, "_config", lambda: {"url": "https://tts.example.com/render"})
    assert local_tts.RagnosLocalHTTPStreamer.available() is False
    monkeypatch.setattr(local_tts, "_config", lambda: {"url": "http://127.0.0.1/api/voice/stream"})
    assert local_tts.RagnosLocalHTTPStreamer.available() is False


def test_stream_posts_text_and_yields_pcm(monkeypatch):
    captured = {}

    class _Raw:
        def read(self, _limit):
            return _pcm_wav()

    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.raw = _Raw()

    def _post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return response

    monkeypatch.setattr("requests.post", _post)
    provider = local_tts.RagnosLocalHTTPStreamer(
        {}, {"url": "http://localhost:8766/api/voice/stream", "timeout": 12}
    )

    assert b"".join(provider.stream("Local voice.")) == b"\x01\x00\x02\x00"
    assert captured["json"] == {"text": "Local voice."}
    assert captured["stream"] is True


def test_stream_rejects_wrong_pcm_shape(monkeypatch):
    class _Raw:
        def read(self, _limit):
            return _pcm_wav(rate=16000)

    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.raw = _Raw()
    monkeypatch.setattr("requests.post", lambda *_args, **_kwargs: response)
    provider = local_tts.RagnosLocalHTTPStreamer({}, {"url": "http://127.0.0.1:8766/tts"})

    with pytest.raises(RuntimeError, match="24 kHz mono 16-bit"):
        list(provider.stream("Wrong rate."))


def test_registration_is_idempotent(monkeypatch):
    calls = []
    monkeypatch.setattr(local_tts, "_REGISTERED", False)
    monkeypatch.setattr(local_tts, "register", lambda name: lambda cls: calls.append((name, cls)))

    local_tts.register_local_http_streamer()
    local_tts.register_local_http_streamer()

    assert calls == [("ragnos_local", local_tts.RagnosLocalHTTPStreamer)]
