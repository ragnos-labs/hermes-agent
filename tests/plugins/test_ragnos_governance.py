"""ragnos-governance: governance.env, toolset expansion, and fail-closed mode.

The plugin directory name has a hyphen, so the modules load by path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugins" / "ragnos-governance"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _PLUGIN_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def governance():
    return _load("ragnos_governance_policy_under_test", "governance.py")


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    for key in (
        "RAGNOS_GOVERNANCE_ENFORCE",
        "RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS",
        "RAGNOS_GOVERNANCE_REQUIRED",
    ):
        monkeypatch.delenv(key, raising=False)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("RAGNOS_GOVERNANCE_LEDGER", str(tmp_path / "ledger.jsonl"))
    name = "ragnos_governance_plugin_under_test"
    spec = importlib.util.spec_from_file_location(
        name, _PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(_PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    module.home = home
    module.ledger = tmp_path / "ledger.jsonl"
    return module


# -- governance.env ---------------------------------------------------------


def test_parse_env_file_keeps_only_governance_keys(governance):
    text = (
        "# comment\n"
        "export RAGNOS_GOVERNANCE_ENFORCE=1\n"
        "RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS=\"terminal, write_file\"\n"
        "OPENAI_API_KEY=should-not-load\n"
        "PATH=/nowhere\n"
        "not a line\n"
    )
    assert governance.parse_env_file(text) == {
        "RAGNOS_GOVERNANCE_ENFORCE": "1",
        "RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS": "terminal, write_file",
    }


def test_env_file_fills_missing_keys_and_environment_wins(governance, tmp_path):
    (tmp_path / "governance.env").write_text(
        "RAGNOS_GOVERNANCE_ENFORCE=1\nRAGNOS_GOVERNANCE_FORBIDDEN_TOOLS=from_file\n",
        encoding="utf-8",
    )
    env = {"HERMES_HOME": str(tmp_path), "RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS": "from_env"}
    merged = governance.effective_env(env)
    assert governance.is_enforcing(merged) is True
    assert governance.forbidden_tools(merged) == frozenset({"from_env"})


def test_missing_env_file_changes_nothing(governance, tmp_path):
    merged = governance.effective_env({"HERMES_HOME": str(tmp_path)})
    assert governance.is_enforcing(merged) is False
    assert governance.ENV_FILE_ERROR_KEY not in merged


def test_env_file_reload_on_change(governance, tmp_path):
    path = tmp_path / "governance.env"
    path.write_text("RAGNOS_GOVERNANCE_ENFORCE=0\n", encoding="utf-8")
    env = {"HERMES_HOME": str(tmp_path)}
    assert governance.is_enforcing(governance.effective_env(env)) is False
    path.write_text("RAGNOS_GOVERNANCE_ENFORCE=true\n", encoding="utf-8")
    assert governance.is_enforcing(governance.effective_env(env)) is True


def test_unreadable_env_file_is_reported(governance, tmp_path):
    (tmp_path / "governance.env").mkdir()  # a directory cannot be read as text
    merged = governance.effective_env({"HERMES_HOME": str(tmp_path)})
    assert merged.get(governance.ENV_FILE_ERROR_KEY)


# -- toolset expansion ------------------------------------------------------


def test_toolset_name_expands_to_its_tools(governance):
    expanded = governance.expand_forbidden(frozenset({"terminal"}))
    assert {"terminal", "process"} <= expanded


def test_unknown_names_stay_as_tool_names(governance):
    assert governance.expand_forbidden(frozenset({"send_money"})) == frozenset({"send_money"})


def test_forbidden_toolset_blocks_member_tool(governance):
    forbidden = governance.expand_forbidden(frozenset({"terminal"}))
    verdict = governance.evaluate_tool("process", {}, enforce=True, forbidden=forbidden)
    assert verdict is not None and verdict["action"] == "block"


# -- plugin hook ------------------------------------------------------------


def test_hook_default_is_fail_open(plugin):
    assert plugin._on_pre_tool_call(tool_name="terminal", args={}) is None


def test_hook_reads_env_file_and_blocks_toolset_member(plugin):
    (plugin.home / "governance.env").write_text(
        "RAGNOS_GOVERNANCE_ENFORCE=1\nRAGNOS_GOVERNANCE_FORBIDDEN_TOOLS=terminal\n",
        encoding="utf-8",
    )
    verdict = plugin._on_pre_tool_call(tool_name="process", args={})
    assert verdict is not None and verdict["action"] == "block"
    assert plugin._on_pre_tool_call(tool_name="read_file", args={}) is None


def test_required_without_config_fails_closed(plugin, monkeypatch):
    monkeypatch.setenv("RAGNOS_GOVERNANCE_REQUIRED", "1")
    verdict = plugin._on_pre_tool_call(tool_name="read_file", args={})
    assert verdict is not None and verdict["action"] == "block"
    assert "RAGNOS_GOVERNANCE_ENFORCE" in verdict["message"]
    rows = [json.loads(line) for line in plugin.ledger.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["required_config_missing"] is True
    assert rows[-1]["blocked"] is True


def test_required_with_config_enforces_normally(plugin, monkeypatch):
    monkeypatch.setenv("RAGNOS_GOVERNANCE_REQUIRED", "1")
    (plugin.home / "governance.env").write_text(
        "RAGNOS_GOVERNANCE_ENFORCE=1\nRAGNOS_GOVERNANCE_FORBIDDEN_TOOLS=send_money\n",
        encoding="utf-8",
    )
    assert plugin._on_pre_tool_call(tool_name="read_file", args={}) is None
    assert plugin._on_pre_tool_call(tool_name="send_money", args={}) is not None


def test_required_with_unreadable_env_file_fails_closed(plugin, monkeypatch):
    monkeypatch.setenv("RAGNOS_GOVERNANCE_REQUIRED", "1")
    monkeypatch.setenv("RAGNOS_GOVERNANCE_ENFORCE", "1")
    monkeypatch.setenv("RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS", "send_money")
    (plugin.home / "governance.env").mkdir()
    verdict = plugin._on_pre_tool_call(tool_name="read_file", args={})
    assert verdict is not None and "governance.env" in verdict["message"]
