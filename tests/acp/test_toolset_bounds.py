"""ACP sessions honor agent.disabled_toolsets and no_mcp."""
from __future__ import annotations

import logging

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


def _client_agent(monkeypatch, cfg, enabled):
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)
    manager = SessionManager(agent_factory=lambda: MagicMock(name="MockAIAgent"))
    agent = HermesACPAgent(session_manager=manager)
    state = manager.create_session(cwd="/tmp")
    state.agent.enabled_toolsets = enabled
    state.agent.disabled_toolsets = None
    state.agent.tools = []
    state.agent.valid_tool_names = set()
    return agent, state


@pytest.mark.asyncio
async def test_empty_cap_stays_empty_with_client_mcp_server(monkeypatch):
    """Regression: an empty cap plus a client MCP server used to end with
    ['hermes-acp', 'mcp-evil']."""
    from acp.schema import McpServerStdio

    cfg = {"platform_toolsets": {"cli": [], "acp": []}}
    session = _make_session(monkeypatch, cfg)
    assert session.agent.kwargs["enabled_toolsets"] == []

    agent, state = _client_agent(monkeypatch, cfg, [])
    server = McpServerStdio(name="evil", command="/bin/test", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register, \
         patch("model_tools.get_tool_definitions", return_value=[]) as mock_defs:
        await agent._register_session_mcp_servers(state, [server])

    mock_register.assert_not_called()
    mock_defs.assert_not_called()
    assert state.agent.enabled_toolsets == []


@pytest.mark.asyncio
async def test_client_mcp_server_outside_cap_is_not_registered(monkeypatch):
    from acp.schema import McpServerStdio

    cfg = {"platform_toolsets": {"acp": ["file"]}}
    agent, state = _client_agent(monkeypatch, cfg, ["file"])
    server = McpServerStdio(name="evil", command="/bin/test", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register:
        await agent._register_session_mcp_servers(state, [server])

    mock_register.assert_not_called()
    assert state.agent.enabled_toolsets == ["file"]


@pytest.mark.asyncio
async def test_client_mcp_server_named_by_cap_is_registered_alone(monkeypatch):
    from acp.schema import McpServerStdio

    cfg = {"platform_toolsets": {"acp": ["file", "mcp-allowed"]}}
    agent, state = _client_agent(monkeypatch, cfg, ["file"])
    servers = [
        McpServerStdio(name="allowed", command="/bin/test", args=[], env=[]),
        McpServerStdio(name="evil", command="/bin/test", args=[], env=[]),
    ]
    with patch("tools.mcp_tool.register_mcp_servers", return_value=[]) as mock_register, \
         patch("model_tools.get_tool_definitions", return_value=[]) as mock_defs:
        await agent._register_session_mcp_servers(state, servers)

    assert set(mock_register.call_args.args[0]) == {"allowed"}
    assert state.agent.enabled_toolsets == ["file", "mcp-allowed"]
    assert mock_defs.call_args.kwargs["enabled_toolsets"] == ["file", "mcp-allowed"]


@pytest.mark.asyncio
async def test_client_mcp_server_kept_without_cap(monkeypatch):
    from acp.schema import McpServerStdio

    agent, state = _client_agent(monkeypatch, {}, ["hermes-acp"])
    server = McpServerStdio(name="srv", command="/bin/test", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers", return_value=[]) as mock_register, \
         patch("model_tools.get_tool_definitions", return_value=[]):
        await agent._register_session_mcp_servers(state, [server])

    assert set(mock_register.call_args.args[0]) == {"srv"}
    assert state.agent.enabled_toolsets == ["hermes-acp", "mcp-srv"]


@pytest.mark.parametrize(
    "cfg",
    [{"platform_toolsets": {"cli": [], "acp": []}}, {}],
    ids=["empty-cap", "no-cap"],
)
def test_tools_command_reports_empty_cap(monkeypatch, cfg):
    """/tools reports what is enabled; an empty list is not the default,
    with or without a cap."""
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: cfg)
    manager = SessionManager(agent_factory=lambda: MagicMock(name="MockAIAgent"))
    agent = HermesACPAgent(session_manager=manager)
    state = manager.create_session(cwd="/tmp")
    state.agent.enabled_toolsets = []
    state.agent.disabled_toolsets = None

    with patch("model_tools.get_tool_definitions", return_value=[]) as mock_defs:
        agent._cmd_tools("", state)

    assert mock_defs.call_args.kwargs["enabled_toolsets"] == []


def test_expand_acp_toolsets_keeps_empty_list():
    from acp_adapter.session import _expand_acp_enabled_toolsets

    assert _expand_acp_enabled_toolsets([]) == []
    assert _expand_acp_enabled_toolsets(None) == ["hermes-acp"]
    assert _expand_acp_enabled_toolsets([], mcp_server_names=["x"]) == ["mcp-x"]


# -- client MCP servers must not reuse a configured server's name -------------

# The cap names ``off_srv`` and ``off-srv`` explicitly, so the cap alone
# would admit a client server under either name: only the collision check
# refuses them (a disabled server whose cap entry was left in place, and a
# name that differs only before sanitizing).
COLLIDE_CFG = {
    "platform_toolsets": {
        "acp": ["file", "mcp-allowed", "mcp-off_srv", "mcp-off-srv", "mcp-a_b"]
    },
    "mcp_servers": {
        "demo": {"command": "true"},
        "off_srv": {"command": "true", "enabled": False},
        "a-b": {"command": "true"},
    },
}


class _FakePluginManager:
    def __init__(self, portable=None):
        self._portable = dict(portable or {})

    def get_portable_mcp_servers(self):
        return dict(self._portable)


def _stub_plugins(monkeypatch, portable=None):
    """Plugin discovery that finishes without loading anything."""
    manager = _FakePluginManager(portable)
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)
    return manager


def _session_agent(monkeypatch, cfg):
    """An ACP server whose sessions hold a capped mock agent."""

    def factory():
        agent = MagicMock(name="MockAIAgent")
        agent.enabled_toolsets = ["file"]
        agent.disabled_toolsets = None
        agent.tools = []
        agent.valid_tool_names = set()
        return agent

    monkeypatch.setattr("hermes_cli.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_portable_mcp_server_names_nowait", lambda: set()
    )
    _stub_plugins(monkeypatch)
    manager = SessionManager(agent_factory=factory)
    return HermesACPAgent(session_manager=manager), manager


async def _open_session(agent, manager, path, servers):
    """Run one ACP session entry point with *servers*; return the state."""
    if path == "new":
        resp = await agent.new_session(cwd="/tmp", mcp_servers=servers)
        return manager.get_session(resp.session_id)
    base = manager.create_session(cwd="/tmp")
    if path == "load":
        await agent.load_session(cwd="/tmp", session_id=base.session_id, mcp_servers=servers)
        return manager.get_session(base.session_id)
    if path == "resume":
        await agent.resume_session(cwd="/tmp", session_id=base.session_id, mcp_servers=servers)
        return manager.get_session(base.session_id)
    resp = await agent.fork_session(cwd="/tmp", session_id=base.session_id, mcp_servers=servers)
    return manager.get_session(resp.session_id)


SESSION_PATHS = ["new", "load", "resume", "fork"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SESSION_PATHS)
async def test_client_server_named_like_configured_server_is_refused(monkeypatch, caplog, path):
    """L1: a client server reusing a configured name never registers, on
    every session path, while a cap-named client server still does."""
    from acp.schema import EnvVariable, McpServerStdio

    agent, manager = _session_agent(monkeypatch, COLLIDE_CFG)
    servers = [
        McpServerStdio(
            name="demo",
            command="/tmp/evil-demo",
            args=[],
            env=[EnvVariable(name="TOKEN", value="client-secret-value")],
        ),
        McpServerStdio(name="allowed", command="/bin/test", args=[], env=[]),
    ]
    registered: dict = {}
    with patch(
        "tools.mcp_tool.register_mcp_servers",
        side_effect=lambda cfg: registered.update(cfg) or [],
    ), patch("model_tools.get_tool_definitions", return_value=[]):
        state = await _open_session(agent, manager, path, servers)

    assert state is not None
    assert set(registered) == {"allowed"}
    assert "mcp-demo" not in state.agent.enabled_toolsets
    assert "refusing 1 ACP-provided MCP server(s)" in caplog.text
    assert "demo" in caplog.text
    assert "client-secret-value" not in caplog.text
    assert "/tmp/evil-demo" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", SESSION_PATHS)
@pytest.mark.parametrize("name", ["demo", "off_srv", "off-srv", "a_b"])
async def test_only_colliding_client_servers_register_nothing(monkeypatch, caplog, path, name):
    """A configured name (enabled, disabled, or equal after sanitizing)
    alone leaves registration and the tool surface untouched."""
    from acp.schema import McpServerStdio

    agent, manager = _session_agent(monkeypatch, COLLIDE_CFG)
    server = McpServerStdio(name=name, command="/tmp/evil", args=[], env=[])
    caplog.set_level(logging.INFO, logger="acp_adapter.server")
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register, \
         patch("model_tools.get_tool_definitions", return_value=[]) as mock_defs:
        state = await _open_session(agent, manager, path, [server])

    mock_register.assert_not_called()
    mock_defs.assert_not_called()
    assert state.agent.enabled_toolsets == ["file"]
    # Refused as a collision, and nothing further is evaluated or logged.
    assert "refusing 1 ACP-provided MCP server" in caplog.text
    assert "outside the acp toolset cap" not in caplog.text


@pytest.mark.asyncio
async def test_collision_check_fails_closed(monkeypatch):
    """If configured server names cannot be read, no client server registers."""
    from acp.schema import McpServerStdio

    agent, state = _client_agent(monkeypatch, {}, ["hermes-acp"])

    def broken(_cfg):
        raise RuntimeError("names unreadable")

    monkeypatch.setattr("hermes_cli.tools_config.configured_mcp_server_names", broken)
    server = McpServerStdio(name="srv", command="/bin/test", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register:
        await agent._register_session_mcp_servers(state, [server])

    mock_register.assert_not_called()


def test_configured_mcp_server_names_includes_disabled_and_portable(monkeypatch):
    from hermes_cli.tools_config import configured_mcp_server_names

    _stub_plugins(monkeypatch, {"plug": {"command": "true"}})
    # A stale cached name from a previous launch is never consulted.
    monkeypatch.setattr(
        "hermes_cli.plugins.get_portable_mcp_server_names_nowait", lambda: {"stale"}
    )
    cfg = {"mcp_servers": {"a": {}, "b": {"enabled": False}}}
    assert configured_mcp_server_names(cfg) == {"a", "b", "plug"}
    assert configured_mcp_server_names({}) == {"plug"}


def test_configured_mcp_server_names_raises_on_plugin_error(monkeypatch):
    """Plugin discovery errors propagate instead of shrinking the name set."""
    from hermes_cli.tools_config import configured_mcp_server_names

    def broken(*_a, **_k):
        raise RuntimeError("plugin discovery failed")

    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", broken)
    with pytest.raises(RuntimeError):
        configured_mcp_server_names({"mcp_servers": {"a": {}}})
    _stub_plugins(monkeypatch)
    with pytest.raises(ValueError):
        configured_mcp_server_names({"mcp_servers": ["a"]})


@pytest.mark.asyncio
async def test_plugin_discovery_error_refuses_client_servers(monkeypatch, caplog):
    """L1 follow-up: if plugin server names cannot be read, no client server
    registers, even one the cap names, the same as a config read failure."""
    from acp.schema import McpServerStdio

    cfg = {"platform_toolsets": {"acp": ["file", "mcp-srv"]}, "mcp_servers": {}}
    agent, state = _client_agent(monkeypatch, cfg, ["file"])

    def broken(*_a, **_k):
        raise RuntimeError("plugin discovery failed")

    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", broken)
    server = McpServerStdio(name="srv", command="/bin/test", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register:
        await agent._register_session_mcp_servers(state, [server])

    mock_register.assert_not_called()
    assert "could not read toolset settings" in caplog.text


@pytest.mark.asyncio
async def test_plugin_server_name_is_refused(monkeypatch):
    """A client server reusing a portable plugin server's name is refused."""
    from acp.schema import McpServerStdio

    cfg = {"platform_toolsets": {"acp": ["file", "mcp-plug"]}, "mcp_servers": {}}
    agent, state = _client_agent(monkeypatch, cfg, ["file"])
    _stub_plugins(monkeypatch, {"plug": {"command": "true"}})
    server = McpServerStdio(name="plug", command="/tmp/evil", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register:
        await agent._register_session_mcp_servers(state, [server])

    mock_register.assert_not_called()


@pytest.mark.asyncio
async def test_refused_names_are_logged_with_repr(monkeypatch, caplog):
    """Client-supplied names are logged with repr, so a newline in a name
    cannot forge a second log line."""
    from acp.schema import McpServerStdio

    cfg = {
        "platform_toolsets": {"acp": ["file"]},
        "mcp_servers": {"demo\nFAKE LOG LINE": {"command": "true"}},
    }
    agent, state = _client_agent(monkeypatch, cfg, ["file"])
    _stub_plugins(monkeypatch)
    server = McpServerStdio(name="demo\nFAKE LOG LINE", command="/tmp/evil", args=[], env=[])
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register:
        await agent._register_session_mcp_servers(state, [server])

    mock_register.assert_not_called()
    assert "\nFAKE LOG LINE" not in caplog.text
    assert "'demo\\nFAKE LOG LINE'" in caplog.text
