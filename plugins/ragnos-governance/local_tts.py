"""RAGnos local voice adapter for Hermes sentence streaming.

The operator's existing Chatterbox service accepts JSON and returns a complete
WAV clip. Hermes already cuts replies into sentence-sized chunks, so adapting
that endpoint to StreamingTTSProvider provides clause-level playback and
barge-in without changing the local voice provider.
"""
from __future__ import annotations

import io
import wave
from typing import Iterator
from urllib.parse import urlparse

from tools.tts_streaming import StreamingTTSProvider, register
from tools.tts_tool import _load_tts_config

_MAX_SENTENCE_BYTES = 16 * 1024 * 1024
_REGISTERED = False


def _config() -> dict:
    try:
        return dict(_load_tts_config().get("ragnos_local") or {})
    except Exception:
        return {}


def _safe_loopback_http_url(raw: object) -> str:
    """Accept only explicit loopback HTTP endpoints with no URL credentials."""
    value = str(raw or "").strip()
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return ""
    if parsed.username or parsed.password or not parsed.port:
        return ""
    return value


class RagnosLocalHTTPStreamer(StreamingTTSProvider):
    """Loopback JSON-to-WAV adapter for the RAGnos Chatterbox service."""

    sample_rate = 24000

    @staticmethod
    def available() -> bool:
        return bool(_safe_loopback_http_url(_config().get("url")))

    def stream(self, text: str) -> Iterator[bytes]:
        import requests

        url = _safe_loopback_http_url(self.section.get("url"))
        if not url:
            raise RuntimeError("ragnos_local TTS requires a loopback http URL with an explicit port")
        timeout = max(1.0, min(float(self.section.get("timeout", 60)), 300.0))
        with requests.post(url, json={"text": text}, timeout=timeout, stream=True) as response:
            response.raise_for_status()
            body = response.raw.read(_MAX_SENTENCE_BYTES + 1)
        if len(body) > _MAX_SENTENCE_BYTES:
            raise RuntimeError("ragnos_local TTS response exceeded the per-sentence byte cap")

        try:
            with wave.open(io.BytesIO(body), "rb") as wav:
                if (
                    wav.getnchannels() != self.channels
                    or wav.getsampwidth() != self.sample_width
                    or wav.getframerate() != self.sample_rate
                ):
                    raise RuntimeError("ragnos_local TTS must return 24 kHz mono 16-bit PCM WAV")
                while chunk := wav.readframes(4096):
                    yield chunk
        except (EOFError, wave.Error) as exc:
            raise RuntimeError("ragnos_local TTS returned invalid WAV audio") from exc


def register_local_http_streamer() -> None:
    """Idempotently register the RAGnos local provider with Hermes."""
    global _REGISTERED
    if _REGISTERED:
        return
    register("ragnos_local")(RagnosLocalHTTPStreamer)
    _REGISTERED = True
