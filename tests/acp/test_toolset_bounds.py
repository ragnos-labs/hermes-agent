"""ACP sessions honor agent.disabled_toolsets and no_mcp."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from acp_adapter.server import HermesACPAgent
from acp_adapter.session import SessionManager


class _FakeAgent:
    model = "fake-model"

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _make_session(monkeypatch, config):
    cfg = {"model": {"default": "fake-model", "provider": "fake-provider"}, **config}
    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda requested=None, **_: {
            "provider": requested,
            "api_mode": "chat_completions",
            "base_url": "https://example.invalid",
            "api_key": "test-key",
        },
    )
    monkeypatch.setattr("acp_adapter.session._register_task_cwd", lambda task_id, cwd: None)
    return SessionManager(db=None).create_session(cwd="/tmp/project")


def test_acp_agent_gets_disabled_toolsets(monkeypatch):
    state = _make_session(monkeypatch, {"agent": {"disabled_toolsets": ["web", "memory"]}})
    assert state.agent.kwargs["disabled_toolsets"] == ["web", "memory"]


def test_acp_agent_without_disabled_toolsets_passes_none(monkeypatch):
    state = _make_session(monkeypatch, {})
    assert state.agent.kwargs["disabled_toolsets"] is None


def test_acp_agent_keeps_configured_mcp_by_default(monkeypatch):
    state = _make_session(monkeypatch, {"mcp_servers": {"finnhub": {"enabled": True}}})
    assert any("finnhub" in ts for ts in state.agent.kwargs["enabled_toolsets"])


@pytest.mark.parametrize(
    "config",
    [
        {"agent": {"no_mcp": True}},
        {"platform_toolsets": {"acp": ["no_mcp"]}},
    ],
)
def test_acp_agent_drops_configured_mcp_when_disabled(monkeypatch, config):
    state = _make_session(monkeypatch, {"mcp_servers": {"finnhub": {"enabled": True}}, **config})
    enabled = state.agent.kwargs["enabled_toolsets"]
    assert not any("finnhub" in ts for ts in enabled)
    assert "no_mcp" not in enabled


@pytest.mark.asyncio
async def test_client_supplied_mcp_servers_ignored_when_mcp_disabled(monkeypatch):
    from acp.schema import McpServerStdio

    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"no_mcp": True}})
    manager = SessionManager(agent_factory=lambda: MagicMock(name="MockAIAgent"))
    agent = HermesACPAgent(session_manager=manager)
    state = manager.create_session(cwd="/tmp")
    state.agent.enabled_toolsets = ["hermes-acp"]
    state.agent.disabled_toolsets = None

    server = McpServerStdio(name="srv", command="/bin/test", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register:
        await agent._register_session_mcp_servers(state, [server])

    mock_register.assert_not_called()
    assert state.agent.enabled_toolsets == ["hermes-acp"]


@pytest.mark.asyncio
async def test_mcp_refresh_keeps_disabled_toolsets(monkeypatch):
    from acp.schema import McpServerStdio

    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {})
    manager = SessionManager(agent_factory=lambda: MagicMock(name="MockAIAgent"))
    agent = HermesACPAgent(session_manager=manager)
    state = manager.create_session(cwd="/tmp")
    state.agent.enabled_toolsets = ["hermes-acp"]
    state.agent.disabled_toolsets = ["web"]
    state.agent.tools = []
    state.agent.valid_tool_names = set()

    server = McpServerStdio(name="srv", command="/bin/test", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers", return_value=["mcp_srv_x"]), \
         patch("model_tools.get_tool_definitions", return_value=[]) as mock_defs:
        await agent._register_session_mcp_servers(state, [server])

    assert mock_defs.call_args.kwargs["disabled_toolsets"] == ["web"]


@pytest.mark.asyncio
async def test_client_mcp_servers_ignored_when_setting_unreadable(monkeypatch):
    """Fail closed: an unreadable no_mcp setting keeps client MCP servers off."""
    from acp.schema import McpServerStdio

    def broken_config():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("hermes_cli.config.load_config", broken_config)
    manager = SessionManager(agent_factory=lambda: MagicMock(name="MockAIAgent"))
    agent = HermesACPAgent(session_manager=manager)
    state = manager.create_session(cwd="/tmp")
    state.agent.enabled_toolsets = ["hermes-acp"]
    state.agent.disabled_toolsets = None

    server = McpServerStdio(name="srv", command="/bin/test", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register:
        await agent._register_session_mcp_servers(state, [server])

    mock_register.assert_not_called()


def test_tools_command_lists_only_bounded_tools(monkeypatch):
    """/tools uses the same allowlist cap and denylist as the session agent."""
    cfg = {
        "agent": {"disabled_toolsets": ["memory"]},
        "platform_toolsets": {"cli": ["file", "memory"]},
    }
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)
    manager = SessionManager(agent_factory=lambda: MagicMock(name="MockAIAgent"))
    agent = HermesACPAgent(session_manager=manager)
    state = manager.create_session(cwd="/tmp")
    state.agent.enabled_toolsets = ["hermes-acp"]
    state.agent.disabled_toolsets = ["search"]

    with patch("model_tools.get_tool_definitions", return_value=[]) as mock_defs:
        agent._cmd_tools("", state)

    kwargs = mock_defs.call_args.kwargs
    assert kwargs["enabled_toolsets"] == ["file"]
    assert set(kwargs["disabled_toolsets"]) == {"memory", "search"}
