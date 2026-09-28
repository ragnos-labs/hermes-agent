"""Gateway agents outside the main message path receive agent.disabled_toolsets."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

DISABLED_CFG = {"agent": {"disabled_toolsets": ["web", "memory"]}}


@patch("gateway.platforms.api_server.AIOHTTP_AVAILABLE", True)
def test_api_server_agent_gets_disabled_toolsets():
    from gateway.config import PlatformConfig
    from gateway.platforms.api_server import APIServerAdapter

    adapter = APIServerAdapter(PlatformConfig())
    with patch("gateway.run._resolve_runtime_agent_kwargs") as mock_kwargs, \
         patch("gateway.run._resolve_gateway_model", return_value="test/model"), \
         patch("gateway.run._load_gateway_config", return_value=dict(DISABLED_CFG)), \
         patch("run_agent.AIAgent") as mock_agent_cls:
        mock_kwargs.return_value = {"api_key": "test-key", "base_url": None,
                                    "provider": None, "api_mode": None,
                                    "command": None, "args": []}
        mock_agent_cls.return_value = MagicMock()
        adapter._create_agent()

    kwargs = mock_agent_cls.call_args.kwargs
    assert kwargs["disabled_toolsets"] == ["web", "memory"]
    assert "web" not in kwargs["enabled_toolsets"]


@pytest.mark.asyncio
async def test_compress_command_agent_gets_disabled_toolsets():
    from tests.gateway.test_compress_command import _make_event, _make_history, _make_runner

    history = _make_history()
    runner = _make_runner(history)
    agent_instance = MagicMock()
    agent_instance._cached_system_prompt = ""
    agent_instance.tools = None
    agent_instance.context_compressor.has_content_to_compress.return_value = True
    agent_instance.session_id = "sess-1"
    agent_instance._compress_context.return_value = (list(history), "")
    agent_instance._compression_skipped_due_to_lock = False

    with (
        patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "test-key"}),
        patch("gateway.run._resolve_gateway_model", return_value="test-model"),
        patch("hermes_cli.config.load_config", return_value=dict(DISABLED_CFG)),
        patch("run_agent.AIAgent", return_value=agent_instance) as mock_agent,
        patch("agent.model_metadata.estimate_request_tokens_rough", return_value=100),
    ):
        await runner._handle_compress_command(_make_event())

    assert mock_agent.call_count == 1
    assert mock_agent.call_args.kwargs["disabled_toolsets"] == ["web", "memory"]
