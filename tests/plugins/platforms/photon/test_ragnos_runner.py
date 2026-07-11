from __future__ import annotations

import json
from typing import Any

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.photon.adapter import (
    PhotonAdapter,
    PhotonAttachmentConsumerUnavailable,
)
from ragnos.run_photon_audition_reply import (
    _build_attachment_readiness,
    _build_sender_authorizer,
)


def _event(sender: str = "+15551234567") -> dict[str, Any]:
    return {
        "messageId": "runner-attachment",
        "deliveryId": "d" * 48,
        "space": {"id": sender, "type": "dm", "phone": sender},
        "sender": {"id": sender},
        "content": {
            "type": "attachment",
            "handle": "a" * 48,
            "name": "private.pdf",
            "mimeType": "application/pdf",
        },
        "timestamp": "2026-07-11T10:00:00.000Z",
    }


def _adapter(monkeypatch: pytest.MonkeyPatch) -> PhotonAdapter:
    monkeypatch.setenv("PHOTON_PROJECT_ID", "project")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "secret")
    return PhotonAdapter(PlatformConfig(enabled=True, token="", extra={}))


@pytest.mark.asyncio
async def test_runner_acks_only_after_secure_upload_and_durable_submit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch)
    calls: list[str] = []

    async def handler(_message: Any) -> None:
        calls.extend(["secure_upload", "durable_box_submit"])

    handler.keez_attachment_handles_owned = True  # type: ignore[attr-defined]
    adapter.set_attachment_sender_authorizer(_build_sender_authorizer(["+15551234567"]))
    adapter.set_attachment_handle_consumer(_build_attachment_readiness(handler))
    adapter.set_message_handler(handler)

    async def sidecar_call(path: str, _body: dict) -> dict:
        if path == "/inbound-ack":
            calls.append(path)
        return {"ok": True}

    monkeypatch.setattr(adapter, "_sidecar_call", sidecar_call)
    await adapter._on_inbound_line(json.dumps(_event()))

    assert calls == ["secure_upload", "durable_box_submit", "/inbound-ack"]


@pytest.mark.asyncio
async def test_runner_rejects_attachment_before_readiness_for_unknown_sender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch)
    calls: list[str] = []

    async def handler(_message: Any) -> None:
        calls.append("handler")

    handler.keez_attachment_handles_owned = True  # type: ignore[attr-defined]
    adapter.set_attachment_sender_authorizer(_build_sender_authorizer(["+15551234567"]))
    adapter.set_attachment_handle_consumer(_build_attachment_readiness(handler))
    adapter.set_message_handler(handler)

    with pytest.raises(PhotonAttachmentConsumerUnavailable):
        await adapter._on_inbound_line(json.dumps(_event("+15550000000")))

    assert calls == []


def test_runner_fails_closed_when_keez_handler_has_no_readiness_hook() -> None:
    async def handler(_message: Any) -> None:
        return None

    assert _build_attachment_readiness(handler)(_event()) is False


@pytest.mark.asyncio
async def test_runner_replay_queries_commit_before_any_second_handle_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch)
    calls: list[str] = []
    committed = False

    async def handler(_message: Any) -> None:
        nonlocal committed
        calls.append("receipt_query")
        if committed:
            return
        calls.extend(["handle_get", "secure_upload", "durable_box_submit_commit"])
        committed = True
        raise TimeoutError("response lost after durable commit")

    handler.keez_attachment_handles_owned = True  # type: ignore[attr-defined]
    adapter.set_attachment_sender_authorizer(_build_sender_authorizer(["+15551234567"]))
    adapter.set_attachment_handle_consumer(_build_attachment_readiness(handler))
    adapter.set_message_handler(handler)

    async def sidecar_call(path: str, _body: dict) -> dict:
        if path == "/inbound-ack":
            calls.append(path)
        return {"ok": True}

    monkeypatch.setattr(adapter, "_sidecar_call", sidecar_call)
    event = _event()
    attachment = event["content"]
    event["content"] = {
        "type": "group",
        "items": [
            {"content": {"type": "text", "text": "audit this"}},
            {"content": attachment},
        ],
    }
    line = json.dumps(event)
    with pytest.raises(Exception):
        await adapter._on_inbound_line(line)
    await adapter._on_inbound_line(line)

    assert calls == [
        "receipt_query",
        "handle_get",
        "secure_upload",
        "durable_box_submit_commit",
        "receipt_query",
        "/inbound-ack",
    ]
