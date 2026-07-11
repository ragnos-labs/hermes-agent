"""Photon-owned regression coverage for replies to the user's own messages."""

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource


@pytest.mark.asyncio
async def test_own_message_reply_prefix_marks_assistant_message() -> None:
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake")},
    )
    runner.adapters = {}
    runner._model = "openai/gpt-4.1-mini"
    runner._base_url = None
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="123",
        chat_name="DM",
        chat_type="private",
        user_name="Alice",
    )
    event = MessageEvent(
        text="this one",
        source=source,
        reply_to_message_id="42",
        reply_to_text="your previous message: Use the direct train.",
    )

    result = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    assert result is not None
    assert result.startswith(
        '[Replying to: "your previous message: Use the direct train."]'
    )
    assert result.endswith("this one")
