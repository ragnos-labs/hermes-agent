"""Entry points refuse to start when governance is required but not loaded."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import hermes_cli.governance_startup as gs


def _plugin(*, enabled=True, error=None, hooks=("pre_tool_call",)):
    return SimpleNamespace(
        manifest=SimpleNamespace(name=gs.GOVERNANCE_PLUGIN),
        enabled=enabled,
        error=error,
        hooks_registered=list(hooks),
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv(gs.REQUIRED_ENV, raising=False)
    return home


@pytest.fixture
def plugins(monkeypatch):
    state = {"plugins": {}}
    manager = SimpleNamespace()
    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", lambda *a, **k: None)
    monkeypatch.setattr("hermes_cli.plugins.get_plugin_manager", lambda: manager)

    def set_plugins(mapping):
        manager._plugins = mapping

    set_plugins({})
    state["set"] = set_plugins
    return state


# -- requirement -------------------------------------------------------------


def test_not_required_by_default(home, plugins):
    assert gs.governance_required() is False
    assert gs.governance_startup_error() is None


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_required_from_environment(home, plugins, monkeypatch, value):
    monkeypatch.setenv(gs.REQUIRED_ENV, value)
    assert gs.governance_required() is True


def test_environment_wins_over_env_file(home, plugins, monkeypatch):
    (home / "governance.env").write_text(f"{gs.REQUIRED_ENV}=1\n")
    monkeypatch.setenv(gs.REQUIRED_ENV, "0")
    assert gs.governance_required() is False


@pytest.mark.parametrize(
    "text, expected",
    [
        (f"{gs.REQUIRED_ENV}=1\n", True),
        (f'export {gs.REQUIRED_ENV}="true"\n', True),
        (f"# {gs.REQUIRED_ENV}=1\n", False),
        (f"{gs.REQUIRED_ENV}=0\n", False),
        ("RAGNOS_GOVERNANCE_ENFORCE=1\n", False),
    ],
)
def test_required_from_env_file(home, plugins, text, expected):
    (home / "governance.env").write_text(text)
    assert gs.governance_required() is expected


def test_unreadable_env_file_counts_as_required(home, plugins):
    (home / "governance.env").mkdir()  # exists but cannot be read as a file
    assert gs.governance_required() is True


# -- plugin state ------------------------------------------------------------


def test_error_when_required_and_plugin_missing(home, plugins, monkeypatch):
    monkeypatch.setenv(gs.REQUIRED_ENV, "1")
    error = gs.governance_startup_error()
    assert error is not None
    assert error["error"] == gs.ERROR_CODE
    assert error["plugin"] == gs.GOVERNANCE_PLUGIN
    assert error["required_env"] == gs.REQUIRED_ENV
    json.dumps(error)
    with pytest.raises(gs.GovernanceNotLoadedError):
        gs.enforce_governance_startup()


@pytest.mark.parametrize(
    "loaded",
    [
        _plugin(enabled=False),
        _plugin(error="ImportError: boom"),
        _plugin(hooks=()),
    ],
    ids=["disabled", "load_error", "no_gate_hook"],
)
def test_error_when_plugin_not_gating(home, plugins, monkeypatch, loaded):
    monkeypatch.setenv(gs.REQUIRED_ENV, "1")
    plugins["set"]({gs.GOVERNANCE_PLUGIN: loaded})
    assert gs.governance_startup_error()["error"] == gs.ERROR_CODE


def test_no_error_when_plugin_loaded(home, plugins, monkeypatch):
    monkeypatch.setenv(gs.REQUIRED_ENV, "1")
    plugins["set"]({gs.GOVERNANCE_PLUGIN: _plugin()})
    assert gs.governance_startup_error() is None
    gs.enforce_governance_startup()


def test_discovery_failure_fails_closed(home, monkeypatch):
    monkeypatch.setenv(gs.REQUIRED_ENV, "1")

    def boom(*a, **k):
        raise RuntimeError("discovery broke")

    monkeypatch.setattr("hermes_cli.plugins.discover_plugins", boom)
    error = gs.governance_startup_error()
    assert error["error"] == gs.ERROR_CODE
    assert "RuntimeError" in error["message"]


# -- entry points ------------------------------------------------------------


def test_acp_entry_emits_json_error_and_exits(home, plugins, monkeypatch, capsys):
    import acp_adapter.entry as entry

    monkeypatch.setenv(gs.REQUIRED_ENV, "1")
    monkeypatch.setattr(entry, "_setup_logging", lambda *a, **k: None)
    with pytest.raises(SystemExit) as excinfo:
        entry.main([])
    assert excinfo.value.code == 1
    err_lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
    assert json.loads(err_lines[-1])["error"] == gs.ERROR_CODE


def test_tui_entry_emits_start_failed_event_and_exits(home, plugins, monkeypatch):
    import tui_gateway.entry as entry

    monkeypatch.setenv(gs.REQUIRED_ENV, "1")
    sent = []
    monkeypatch.setattr(entry, "write_json", lambda obj: sent.append(obj) or True)
    monkeypatch.setattr(entry, "_log_exit", lambda *a, **k: None)
    with pytest.raises(SystemExit) as excinfo:
        entry.main()
    assert excinfo.value.code == 1
    assert sent[0]["params"]["type"] == "gateway.start_failed"
    assert sent[0]["params"]["payload"]["error"] == gs.ERROR_CODE


def _build_agent():
    from unittest.mock import patch

    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
        patch("hermes_cli.config.load_config", return_value={}),
        patch("hermes_cli.config.load_config_readonly", return_value={}),
    ):
        return AIAgent(
            model="openai/gpt-4.1",
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )


def test_agent_init_refuses_to_build(home, plugins, monkeypatch):
    monkeypatch.setenv(gs.REQUIRED_ENV, "1")
    with pytest.raises(gs.GovernanceNotLoadedError):
        _build_agent()


def test_agent_init_builds_when_plugin_loaded(home, plugins, monkeypatch):
    monkeypatch.setenv(gs.REQUIRED_ENV, "1")
    plugins["set"]({gs.GOVERNANCE_PLUGIN: _plugin()})
    assert _build_agent() is not None
