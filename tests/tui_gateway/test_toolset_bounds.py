"""TUI and desktop gateway agents honor agent.disabled_toolsets and no_mcp.

The bounds apply after the client-surface toolsets, the coding posture and
the HERMES_TUI_TOOLSETS pin, so none of those can re-add a removed toolset.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import tui_gateway.server as server


@pytest.fixture
def env(monkeypatch):
    monkeypatch.delenv("HERMES_DESKTOP", raising=False)
    monkeypatch.delenv("HERMES_DESKTOP_TERMINAL", raising=False)
    monkeypatch.delenv("HERMES_TUI_TOOLSETS", raising=False)
    return monkeypatch


def _use_config(monkeypatch, cfg):
    monkeypatch.setattr(server, "_load_cfg", lambda: cfg)


def test_disabled_list_applies_after_surface_toolsets(env):
    import agent.coding_context as cc

    env.setattr(cc, "coding_selection", lambda **_: ["coding"])
    _use_config(env, {"agent": {"disabled_toolsets": ["desktop_ui", "project"]}})

    assert server._load_enabled_toolsets("desktop") == ["coding"]


def test_env_pin_cannot_reenable_disabled_toolset(env):
    env.setenv("HERMES_TUI_TOOLSETS", "web,memory")
    _use_config(env, {"agent": {"disabled_toolsets": ["web"]}})

    assert server._load_enabled_toolsets("tui") == ["memory"]


def test_no_mcp_strips_mcp_from_posture_selection(env):
    import agent.coding_context as cc

    env.setattr(cc, "coding_selection", lambda **_: ["coding", "finnhub", "mcp-other"])
    _use_config(env, {"agent": {"no_mcp": True}, "mcp_servers": {"finnhub": {}}})

    assert server._load_enabled_toolsets("tui") == ["coding", "project"]


def test_unbounded_config_is_unchanged(env):
    env.setenv("HERMES_TUI_TOOLSETS", "web,memory")
    _use_config(env, {})

    assert server._load_enabled_toolsets("tui") == ["web", "memory"]


def _parent_agent(**overrides):
    base = dict(
        base_url=None, api_key=None, provider=None, api_mode=None,
        acp_command=None, acp_args=None, model="m",
        enabled_toolsets=["web", "terminal", "file"],
        disabled_toolsets=["memory"],
        ephemeral_system_prompt=None, request_overrides={},
        reasoning_config={"effort": "low"}, service_tier="auto",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def kwargs_env(env):
    env.setattr(server, "_get_db", lambda: None)
    env.setattr(server, "_agent_fallback_model", lambda agent: None)
    return env


def test_background_agent_merges_parent_and_config_disabled(kwargs_env):
    _use_config(kwargs_env, {"agent": {"disabled_toolsets": ["web"]}})

    kwargs = server._background_agent_kwargs(_parent_agent(), "task-1")

    assert kwargs["disabled_toolsets"] == ["memory", "web"]


def test_background_agent_without_any_disabled_passes_none(kwargs_env):
    _use_config(kwargs_env, {})

    kwargs = server._background_agent_kwargs(_parent_agent(disabled_toolsets=None), "task-1")

    assert kwargs["disabled_toolsets"] is None


def test_preview_agent_drops_disabled_terminal(kwargs_env):
    _use_config(kwargs_env, {"agent": {"disabled_toolsets": ["terminal"]}})

    kwargs = server._ephemeral_preview_agent_kwargs(_parent_agent(), "task-1")

    assert kwargs["enabled_toolsets"] == ["file"]
    assert "terminal" in kwargs["disabled_toolsets"]


def test_preview_agent_default_toolsets(kwargs_env):
    _use_config(kwargs_env, {})

    kwargs = server._ephemeral_preview_agent_kwargs(_parent_agent(disabled_toolsets=None), "task-1")

    assert kwargs["enabled_toolsets"] == ["terminal", "file"]


def test_enabled_toolsets_fail_closed_when_config_unreadable(env):
    def broken():
        raise RuntimeError("config unreadable")

    env.setattr(server, "_load_cfg", broken)
    assert server._load_enabled_toolsets("tui") == []


def test_disabled_toolsets_fail_closed_when_config_unreadable(env):
    import hermes_cli.tools_config as tc

    def broken(*a, **k):
        raise RuntimeError("config unreadable")

    env.setattr(tc, "load_disabled_toolsets", broken)
    with pytest.raises(RuntimeError):
        server._tui_disabled_toolsets()


def test_preview_agent_capped_by_cli_allowlist(kwargs_env):
    """preview.restart builds its agent inside the cli cap: a cap without
    terminal leaves the preview agent with file only."""
    _use_config(kwargs_env, {"platform_toolsets": {"cli": ["file", "web"]}})

    kwargs = server._ephemeral_preview_agent_kwargs(_parent_agent(disabled_toolsets=None), "task-1")

    assert kwargs["enabled_toolsets"] == ["file"]


def test_preview_agent_empty_cap_gets_no_toolsets(kwargs_env):
    _use_config(kwargs_env, {"platform_toolsets": {"cli": []}})

    kwargs = server._ephemeral_preview_agent_kwargs(_parent_agent(disabled_toolsets=None), "task-1")

    assert kwargs["enabled_toolsets"] == []


def test_preview_agent_fails_closed_when_bounds_fail(kwargs_env):
    import hermes_cli.tools_config as tc

    _use_config(kwargs_env, {})

    def broken(*a, **k):
        raise RuntimeError("bounds unavailable")

    kwargs_env.setattr(tc, "bound_enabled_toolsets", broken)
    kwargs = server._ephemeral_preview_agent_kwargs(_parent_agent(disabled_toolsets=None), "task-1")

    assert kwargs["enabled_toolsets"] == []
