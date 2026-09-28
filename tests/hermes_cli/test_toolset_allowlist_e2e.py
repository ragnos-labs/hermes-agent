"""The configured toolset allowlist holds on every path that builds an agent.

One restrictive config (an allowlist of ``file``, ``search`` and ``todo``, a
demo MCP server, and a disabled list that does NOT name ``web``, ``browser``
or ``a2a``) is run through every agent path. Each must end at exactly the
allowlisted tools: nothing from a ``hermes-<platform>`` default, a composite
such as ``hermes-acp``, a webhook route, a cron job's own list or
``HERMES_TUI_TOOLSETS=all``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import model_tools  # noqa: F401  (registers the built-in tools)
from toolsets import bundle_non_core_tools, get_toolset, resolve_toolset, validate_toolset

ALLOW = ["file", "search", "todo"]
DISABLED = [
    "clarify", "code_execution", "cronjob", "delegation", "image_gen", "kanban",
    "memory", "session_search", "skills", "terminal", "tts", "vision",
]
PLATFORMS_WITH_ENTRY = ["cli", "cron", "acp", "webhook", "api_server"]
MCP_NAMES = {"demo", "mcp-demo"}


def _config(*, no_mcp: bool) -> dict:
    entry = ALLOW + (["no_mcp"] if no_mcp else [])
    return {
        "model": {"default": "fake-model", "provider": "fake-provider"},
        "agent": {"disabled_toolsets": list(DISABLED)},
        "toolsets": list(ALLOW),
        "platform_toolsets": {p: list(entry) for p in PLATFORMS_WITH_ENTRY},
        "mcp_servers": {"demo": {"command": "true"}},
    }


def _tools(enabled, disabled) -> set[str]:
    """Tool names an agent built with these lists would be offered.

    Mirrors ``model_tools._compute_tool_definitions`` without the per-tool
    availability checks, so the result does not depend on API keys.
    """
    assert enabled is not None, "an unbounded (None) toolset list reached the agent"
    tools: set[str] = set()
    for name in enabled:
        if name in MCP_NAMES:
            continue
        assert validate_toolset(name), f"unknown toolset {name!r}"
        tools |= set(resolve_toolset(name))
    for name in disabled or []:
        if not validate_toolset(name):
            continue
        if name.startswith("hermes-") or (get_toolset(name) or {}).get("posture"):
            tools -= set(bundle_non_core_tools(name))
        else:
            tools -= set(resolve_toolset(name))
    return tools


def _expected_tools() -> set[str]:
    tools: set[str] = set()
    for name in ALLOW:
        tools |= set(resolve_toolset(name))
    return tools


def _mcp(enabled) -> set[str]:
    return {name for name in enabled if name in MCP_NAMES}


@pytest.fixture(params=[True, False], ids=["no_mcp", "mcp_on"])
def golden(request, monkeypatch):
    cfg = _config(no_mcp=request.param)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(
        "hermes_cli.plugins.get_portable_mcp_server_names_nowait", lambda: set()
    )
    monkeypatch.delenv("HERMES_TUI_TOOLSETS", raising=False)
    return SimpleNamespace(cfg=cfg, no_mcp=request.param)


def _assert_bounded(golden, enabled, disabled, *, mcp_requested=True):
    assert _tools(enabled, disabled) == _expected_tools()
    if golden.no_mcp:
        assert not _mcp(enabled)
    elif mcp_requested:
        # MCP stays on where the allowlist entry itself allows it.
        assert _mcp(enabled)


def _disabled(cfg):
    from hermes_cli.tools_config import load_disabled_toolsets

    return load_disabled_toolsets(cfg)


@pytest.mark.parametrize("platform", ["cli", "api_server", "cron", "sms", "teams", "irc", "telegram"])
def test_platform_resolution_is_capped(golden, platform):
    from hermes_cli.tools_config import _get_platform_tools

    enabled = sorted(_get_platform_tools(golden.cfg, platform))
    _assert_bounded(golden, enabled, _disabled(golden.cfg))


def test_acp_session_is_capped(golden, monkeypatch):
    from acp_adapter.session import SessionManager

    class _FakeAgent:
        model = "fake-model"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr("run_agent.AIAgent", _FakeAgent)
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
    state = SessionManager(db=None).create_session(cwd="/tmp/project")

    kwargs = state.agent.kwargs
    _assert_bounded(golden, kwargs["enabled_toolsets"], kwargs["disabled_toolsets"])


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["new", "load", "resume", "fork"])
async def test_acp_client_cannot_reuse_configured_mcp_name(golden, path):
    """A client-supplied MCP server named like the configured ``demo`` server
    is refused on every session path. With MCP on, the cap admits ``demo``
    by design, so before the collision check the client's own command was
    registered under that name. The configured server stays in the session's
    toolsets exactly as the cap resolved it."""
    from unittest.mock import MagicMock, patch

    from acp.schema import McpServerStdio

    from acp_adapter.server import HermesACPAgent
    from acp_adapter.session import SessionManager, _expand_acp_enabled_toolsets
    from hermes_cli.tools_config import bound_enabled_toolsets

    # Same expansion SessionManager uses: hermes-acp plus configured servers.
    session_toolsets = bound_enabled_toolsets(
        _expand_acp_enabled_toolsets(["hermes-acp"], mcp_server_names=["demo"]),
        golden.cfg,
        "acp",
    )

    def factory():
        agent = MagicMock(name="MockAIAgent")
        agent.enabled_toolsets = list(session_toolsets)
        agent.disabled_toolsets = _disabled(golden.cfg)
        agent.tools = []
        agent.valid_tool_names = set()
        return agent

    manager = SessionManager(agent_factory=factory)
    acp_agent = HermesACPAgent(session_manager=manager)
    servers = [McpServerStdio(name="demo", command="/tmp/client-demo", args=[], env=[])]
    with patch("tools.mcp_tool.register_mcp_servers") as mock_register, \
         patch("model_tools.get_tool_definitions", return_value=[]) as mock_defs:
        if path == "new":
            resp = await acp_agent.new_session(cwd="/tmp", mcp_servers=servers)
            sid = resp.session_id
        else:
            sid = manager.create_session(cwd="/tmp").session_id
            if path == "load":
                await acp_agent.load_session(cwd="/tmp", session_id=sid, mcp_servers=servers)
            elif path == "resume":
                await acp_agent.resume_session(cwd="/tmp", session_id=sid, mcp_servers=servers)
            else:
                resp = await acp_agent.fork_session(cwd="/tmp", session_id=sid, mcp_servers=servers)
                sid = resp.session_id

    mock_register.assert_not_called()
    mock_defs.assert_not_called()
    state = manager.get_session(sid)
    # The session keeps exactly what the cap resolved: the configured
    # server's toolset with MCP on, none with no_mcp. The client's command
    # never reached registration under that name.
    assert state.agent.enabled_toolsets == session_toolsets
    if golden.no_mcp:
        assert not _mcp(session_toolsets)
    else:
        assert _mcp(session_toolsets) == {"mcp-demo"}


def test_webhook_route_is_capped(golden):
    from gateway.run import GatewayRunner

    adapter = SimpleNamespace(toolsets_for_source=lambda source: ["web", "browser", "a2a", "file"])
    runner = SimpleNamespace(_adapter_for_source=lambda source: adapter)

    enabled = GatewayRunner._resolve_enabled_toolsets_for_source(runner, golden.cfg, None, "webhook")

    tools = _tools(enabled, _disabled(golden.cfg))
    assert tools <= _expected_tools()
    assert "web_extract" not in tools
    assert not any(t.startswith("browser_") for t in tools)
    if golden.no_mcp:
        assert not _mcp(enabled)


def test_cron_jobs_are_capped(golden):
    from cron.scheduler import _resolve_cron_disabled_toolsets, _resolve_cron_enabled_toolsets

    disabled = _resolve_cron_disabled_toolsets(golden.cfg)
    default_job = _resolve_cron_enabled_toolsets({}, golden.cfg)
    _assert_bounded(golden, default_job, disabled)

    per_job = _resolve_cron_enabled_toolsets(
        {"enabled_toolsets": ["web", "browser", "a2a", "file", "search", "todo"]}, golden.cfg
    )
    _assert_bounded(golden, per_job, disabled)


@pytest.mark.parametrize("pin", [None, "all", "web,browser,file,search,todo"])
@pytest.mark.parametrize("platform", ["tui", "desktop"])
def test_tui_sessions_are_capped(golden, monkeypatch, pin, platform):
    import tui_gateway.server as server

    monkeypatch.setattr(server, "_load_cfg", lambda: golden.cfg)
    if pin is not None:
        monkeypatch.setenv("HERMES_TUI_TOOLSETS", pin)

    enabled = server._load_enabled_toolsets(platform)
    # A pin that names no MCP server does not ask for one.
    _assert_bounded(
        golden, enabled, server._tui_disabled_toolsets(), mcp_requested=pin in (None, "all")
    )


def test_unbounded_list_becomes_the_cap(golden):
    from hermes_cli.tools_config import bound_enabled_toolsets

    enabled = bound_enabled_toolsets(None, golden.cfg, "sms")
    _assert_bounded(golden, enabled, _disabled(golden.cfg))


def test_curator_outside_allowlist_is_skipped(golden):
    from hermes_cli.tools_config import bound_enabled_toolsets

    # ``skills`` is neither allowlisted nor enabled, so the curator gets
    # nothing and skips its review (see tests/agent/test_curator_disabled_toolsets.py).
    assert bound_enabled_toolsets(["skills"], golden.cfg, "curator") == []


def test_no_cap_without_any_entry():
    """With no platform_toolsets entry at all, lists are not capped (upstream)."""
    from hermes_cli.tools_config import bound_enabled_toolsets

    cfg = {"agent": {"disabled_toolsets": ["memory"]}}
    assert bound_enabled_toolsets(["web", "memory"], cfg, "sms") == ["web"]
    assert bound_enabled_toolsets(None, cfg, "sms") is None


def test_platform_no_mcp_falls_back_to_cli():
    from hermes_cli.tools_config import mcp_disabled_for_platform

    cfg = {"platform_toolsets": {"cli": ["file", "no_mcp"], "telegram": ["file"]}}
    assert mcp_disabled_for_platform(cfg, "sms") is True
    assert mcp_disabled_for_platform(cfg, "telegram") is False
    assert mcp_disabled_for_platform(cfg, "cli") is True
