"""Configured toolset bounds hold on every resolution path.

Covers the shared helpers in ``hermes_cli.tools_config`` plus the three
paths that build toolset lists outside a ``platform_toolsets`` entry: the
``hermes-<platform>`` fallback used by plugin platforms, webhook routes that
carry their own toolset list, and cron jobs with a per-job toolset list.
"""
from __future__ import annotations

import pytest

from cron.scheduler import _merge_mcp_into_per_job_toolsets, _resolve_cron_disabled_toolsets
from gateway.run import GatewayRunner
from hermes_cli.tools_config import (
    _get_platform_tools,
    bound_enabled_toolsets,
    load_disabled_toolsets,
    mcp_disabled_for_platform,
    merge_disabled_toolsets,
)

MCP_CFG = {"mcp_servers": {"finnhub": {"enabled": True}, "playwright": {"enabled": True}}}
MCP_NAMES = {"finnhub", "playwright", "mcp-finnhub", "mcp-playwright"}

# Bundled gateway platforms that load regardless of ``plugins.enabled`` and
# have no ``platform_toolsets`` default, so they resolve through the
# ``hermes-<platform>`` fallback.
ALWAYS_LOADED_PLUGIN_PLATFORMS = (
    "a2a", "buzz", "google_chat", "irc", "line", "ntfy",
    "photon", "raft", "simplex", "sms", "teams",
)


@pytest.fixture(autouse=True)
def _no_portable_mcp(monkeypatch):
    # Keep MCP names to what the test config declares.
    import hermes_cli.tools_config as tc

    monkeypatch.setattr(
        tc,
        "enabled_mcp_server_names",
        lambda config: {
            name
            for name, cfg in (config.get("mcp_servers") or {}).items()
            if not isinstance(cfg, dict) or cfg.get("enabled", True) is not False
        },
    )


def _cfg(**agent):
    cfg = dict(MCP_CFG)
    if agent:
        cfg["agent"] = agent
    return cfg


# -- helpers ----------------------------------------------------------------


class TestHelpers:
    def test_load_disabled_toolsets_none_when_unset(self):
        assert load_disabled_toolsets({}) is None
        assert load_disabled_toolsets({"agent": {"disabled_toolsets": []}}) is None
        assert load_disabled_toolsets({"agent": "bad"}) is None

    def test_load_disabled_toolsets_dedupes_in_order(self):
        cfg = {"agent": {"disabled_toolsets": ["web", "memory", "web"]}}
        assert load_disabled_toolsets(cfg) == ["web", "memory"]

    def test_merge_disabled_toolsets(self):
        assert merge_disabled_toolsets(None, None) is None
        assert merge_disabled_toolsets(["a", "b"], None, ["b", "c"]) == ["a", "b", "c"]

    def test_mcp_disabled_for_platform(self):
        assert mcp_disabled_for_platform({}, "sms") is False
        assert mcp_disabled_for_platform({"agent": {"no_mcp": True}}, "sms") is True
        assert mcp_disabled_for_platform({"agent": {"no_mcp": "yes"}}, None) is True
        assert mcp_disabled_for_platform({"agent": {"no_mcp": False}}, "sms") is False
        pts = {"platform_toolsets": {"acp": ["no_mcp"]}}
        assert mcp_disabled_for_platform(pts, "acp") is True
        assert mcp_disabled_for_platform(pts, "cli") is False

    def test_bound_enabled_toolsets_none_is_unchanged(self):
        assert bound_enabled_toolsets(None, _cfg(disabled_toolsets=["web"], no_mcp=True)) is None

    def test_bound_enabled_toolsets_removes_disabled_and_sentinel(self):
        cfg = _cfg(disabled_toolsets=["web"])
        got = bound_enabled_toolsets(["web", "file", "no_mcp", "file", "finnhub"], cfg)
        assert got == ["file", "finnhub"]

    def test_bound_enabled_toolsets_removes_mcp_when_off(self):
        cfg = _cfg(no_mcp=True)
        got = bound_enabled_toolsets(
            ["coding", "finnhub", "mcp-playwright", "mcp-other", "project"], cfg, "cli"
        )
        assert got == ["coding", "project"]


# -- hermes-<platform> fallback (plugin platforms) ---------------------------


@pytest.mark.parametrize("platform", ALWAYS_LOADED_PLUGIN_PLATFORMS)
def test_fallback_platform_gets_mcp_by_default(platform):
    assert MCP_NAMES & _get_platform_tools(_cfg(), platform)


@pytest.mark.parametrize("platform", ALWAYS_LOADED_PLUGIN_PLATFORMS)
def test_fallback_platform_honors_global_no_mcp(platform):
    tools = _get_platform_tools(_cfg(no_mcp=True), platform)
    assert not (MCP_NAMES & tools)
    assert "no_mcp" not in tools
    assert "file" in tools or f"hermes-{platform}" in tools


@pytest.mark.parametrize("platform", ALWAYS_LOADED_PLUGIN_PLATFORMS)
def test_fallback_platform_honors_disabled_toolsets(platform):
    tools = _get_platform_tools(_cfg(disabled_toolsets=["web", "finnhub"]), platform)
    assert "web" not in tools
    assert "finnhub" not in tools


def test_platform_no_mcp_sentinel_drops_explicit_mcp_names():
    cfg = dict(MCP_CFG, platform_toolsets={"sms": ["web", "finnhub", "no_mcp"]})
    tools = _get_platform_tools(cfg, "sms")
    assert "web" in tools
    assert not (MCP_NAMES & tools)


# -- webhook route toolsets --------------------------------------------------


class _Src:
    def __init__(self, chat_id):
        self.chat_id = chat_id


def _runner_with_route(route_toolsets):
    from gateway.platforms.webhook import WebhookAdapter

    adapter = object.__new__(WebhookAdapter)
    adapter._routes = {"mon": {"secret": "x", "toolsets": list(route_toolsets)}}
    runner = object.__new__(GatewayRunner)
    runner._adapter_for_source = lambda source: adapter
    return runner


def _resolve_route(cfg, route_toolsets):
    runner = _runner_with_route(route_toolsets)
    return GatewayRunner._resolve_enabled_toolsets_for_source(
        runner, cfg, _Src("webhook:mon:d"), "webhook"
    )


class TestWebhookRouteBounds:
    def test_route_cannot_reenable_disabled_toolset(self):
        cfg = _cfg(disabled_toolsets=["terminal"])
        res = _resolve_route(cfg, ["terminal", "file"])
        assert "terminal" not in res
        assert "file" in res

    def test_route_keeps_platform_no_mcp(self):
        cfg = dict(MCP_CFG, platform_toolsets={"webhook": ["web", "no_mcp"]})
        res = _resolve_route(cfg, ["file", "finnhub"])
        assert "file" in res
        assert not (MCP_NAMES & set(res))

    def test_route_keeps_global_no_mcp(self):
        res = _resolve_route(_cfg(no_mcp=True), ["file", "mcp-finnhub"])
        assert "file" in res
        assert not (MCP_NAMES & set(res))

    def test_route_gets_mcp_without_no_mcp(self):
        res = _resolve_route(_cfg(), ["file", "finnhub"])
        assert "finnhub" in res

    def test_route_does_not_mutate_config(self):
        cfg = dict(MCP_CFG, platform_toolsets={"webhook": ["web", "no_mcp"]})
        _resolve_route(cfg, ["file"])
        assert cfg["platform_toolsets"]["webhook"] == ["web", "no_mcp"]


# -- cron per-job toolsets ---------------------------------------------------


class TestCronPerJobBounds:
    def test_global_no_mcp_strips_named_and_default_servers(self):
        got = _merge_mcp_into_per_job_toolsets(["web", "finnhub"], _cfg(no_mcp=True))
        assert got == ["web"]

    def test_cron_platform_no_mcp_applies_to_per_job_list(self):
        cfg = dict(MCP_CFG, platform_toolsets={"cron": ["web", "no_mcp"]})
        assert _merge_mcp_into_per_job_toolsets(["file"], cfg) == ["file"]

    def test_disabled_toolsets_removed_when_mcp_off(self):
        got = _merge_mcp_into_per_job_toolsets(
            ["web", "terminal"], _cfg(no_mcp=True, disabled_toolsets=["terminal"])
        )
        assert got == ["web"]

    def test_default_merge_unchanged_without_no_mcp(self):
        got = _merge_mcp_into_per_job_toolsets(["web"], _cfg())
        assert set(got) == {"web", "finnhub", "playwright"}

    def test_cron_agent_receives_disabled_toolsets(self):
        assert "terminal" in _resolve_cron_disabled_toolsets(_cfg(disabled_toolsets=["terminal"]))
