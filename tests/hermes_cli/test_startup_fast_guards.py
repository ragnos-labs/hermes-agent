"""Guards for hermes_cli._startup_fast — the pre-import version fast path.

Two invariants, each of which has been broken before:

1. IMPORT WEIGHT: _startup_fast must stay stdlib-only. The whole point of
   the module is to run before main.py's heavy import wall; one careless
   ``from hermes_cli.config import ...`` silently makes `hermes --version`
   slow again for everyone (the regression would be invisible — everything
   still works, just 40x slower).

2. OUTPUT PARITY / LIVENESS: the fast path must actually produce version
   output and exit 0 in a real subprocess, on and off Termux. This is the
   test that would have caught eb4040242, which changed the canonical
   version output to reference the PROJECT_ROOT module constant inside the
   fast function — a name that doesn't exist yet at the fast exit point —
   NameError-ing the Termux fast path in production for weeks.
"""

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules that must NEVER be imported by the fast path. Each one either
# pulls yaml/argparse/logging config or is itself a god-module.
_FORBIDDEN_MODULES = (
    "hermes_cli.config",
    "hermes_cli.main",
    "yaml",
    "argparse",
    "cli",
    "run_agent",
    "model_tools",
    "httpx",
    "openai",
)


def test_startup_fast_import_weight():
    """Importing _startup_fast must not drag in any heavy module."""
    probe = (
        "import sys, json\n"
        "import hermes_cli._startup_fast\n"
        "print(json.dumps(sorted(sys.modules.keys())))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    loaded = set(json.loads(result.stdout))
    offenders = [m for m in _FORBIDDEN_MODULES if m in loaded]
    assert not offenders, (
        f"hermes_cli._startup_fast imported heavy modules: {offenders} — "
        "the fast path must stay stdlib-only (see module docstring)."
    )


def _run_version(env_overrides: dict) -> subprocess.CompletedProcess:
    env = {**os.environ, **env_overrides}
    env.pop("HERMES_DEV", None)
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=REPO_ROOT,
        env=env,
    )


def test_fast_version_parity_off_termux(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    result = _run_version({"HERMES_HOME": str(home), "TERMUX_VERSION": ""})
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for field in ("Hermes Agent v", "Install directory:", "Python:", "OpenAI SDK:"):
        assert field in out, f"fast --version output missing {field!r}:\n{out}"


def test_fast_version_parity_on_termux(tmp_path):
    """The historical Termux path — the one eb4040242 broke."""
    home = tmp_path / ".hermes"
    home.mkdir()
    result = _run_version(
        {"HERMES_HOME": str(home), "TERMUX_VERSION": "0.118"}
    )
    assert result.returncode == 0, result.stderr
    assert "Hermes Agent v" in result.stdout
    assert "Traceback" not in result.stderr


def test_fast_version_reports_install_method_stamp(tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".install_method").write_text("git\n", encoding="utf-8")
    result = _run_version({"HERMES_HOME": str(home), "TERMUX_VERSION": ""})
    assert result.returncode == 0, result.stderr
    assert "Install method: git" in result.stdout


def _strict_config(home: Path, *, provider: str = "custom") -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "model:\n"
        "  output_budget_mode: strict\n"
        "  max_tokens: 2000\n"
        f"  provider: {provider}\n"
        "  default: synthetic-model\n"
        "  base_url: https://inference.example.invalid/v1\n"
        "  api_mode: chat_completions\n"
        "  key_env: STRICT_TEST_KEY\n",
        encoding="utf-8",
    )


def test_public_console_script_uses_thin_entrypoint():
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'hermes = "hermes_cli.entrypoint:main"' in text


def test_strict_rejection_stays_before_ordinary_stack(monkeypatch, tmp_path, capsys):
    from hermes_cli import entrypoint

    home = tmp_path / ".hermes"
    _strict_config(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("STRICT_TEST_KEY", raising=False)
    before = {name for name in sys.modules if name in {"hermes_cli.main", "run_agent", "model_tools"}}

    assert entrypoint.main(["-z", "synthetic prompt"]) == 2
    assert capsys.readouterr().err == "strict_cli_rejected\n"
    after = {name for name in sys.modules if name in {"hermes_cli.main", "run_agent", "model_tools"}}
    assert after == before


def test_strict_success_uses_one_admitted_boundary_without_main(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import entrypoint

    home = tmp_path / ".hermes"
    _strict_config(home)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("STRICT_TEST_KEY", "secret-not-for-output")
    captured = {}

    def fake_run(prompt, *, config, route):
        captured.update(prompt=prompt, config=config, route=route)
        return 0, "synthetic result", {"input_tokens": 12, "output_tokens": 3}

    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.oneshot",
        SimpleNamespace(run_strict_oneshot=fake_run),
    )
    before_main = sys.modules.get("hermes_cli.main")

    assert entrypoint.main(["--oneshot=synthetic prompt"]) == 0
    output = capsys.readouterr()
    assert output.out == "synthetic result\n"
    assert output.err == ""
    assert captured["route"]["model"] == "synthetic-model"
    assert captured["route"]["api_key"] == "secret-not-for-output"
    assert sys.modules.get("hermes_cli.main") is before_main


def test_strict_route_and_cli_overrides_fail_closed(monkeypatch, tmp_path, capsys):
    from hermes_cli import entrypoint

    home = tmp_path / ".hermes"
    _strict_config(home, provider="auto")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("STRICT_TEST_KEY", "secret-not-for-output")
    assert entrypoint.main(["-z", "synthetic prompt"]) == 2
    assert capsys.readouterr().err == "strict_cli_rejected\n"

    _strict_config(home)
    assert entrypoint.main(["-z", "synthetic prompt", "--model", "override"]) == 2
    assert capsys.readouterr().err == "strict_cli_rejected\n"


def test_strict_route_allowlist_rejects_unknown_or_ambiguous_fields_before_effects(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import entrypoint

    cases = {
        "conflicting-identities": "  model: conflicting-model\n",
        "credential-pool": "  credential_pool: [pool-a]\n",
        "oauth": "  oauth: true\n",
        "alias": "  alias: synthetic-alias\n",
        "autodetect": "  autodetect: true\n",
        "refresh": "  refresh: true\n",
        "model-tools": "  tools: [delegate_task]\n",
        "model-sidecar": "  extra_body: {}\n",
        "unknown-model-field": "  future_route: rejected\n",
    }
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("STRICT_TEST_KEY", "secret-not-for-output")

    for suffix in cases.values():
        _strict_config(home)
        with (home / "config.yaml").open("a", encoding="utf-8") as stream:
            stream.write(suffix)
        called = []
        monkeypatch.setitem(
            sys.modules,
            "hermes_cli.oneshot",
            SimpleNamespace(run_strict_oneshot=lambda *args, **kwargs: called.append(True)),
        )
        before = {
            name
            for name in sys.modules
            if name in {"hermes_cli.main", "openai", "run_agent", "model_tools"}
        }
        assert entrypoint.main(["-z", "synthetic prompt"]) == 2
        assert capsys.readouterr().err == "strict_cli_rejected\n"
        assert called == []
        after = {
            name
            for name in sys.modules
            if name in {"hermes_cli.main", "openai", "run_agent", "model_tools"}
        }
        assert after == before


def test_strict_root_allowlist_rejects_effect_fields_before_effects(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import entrypoint

    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("STRICT_TEST_KEY", "secret-not-for-output")

    for suffix in (
        "toolsets: [browser]\n",
        "hooks: {pre_llm_call: enabled}\n",
        "fallback_model: synthetic-fallback\n",
        "unknown_effect: true\n",
    ):
        _strict_config(home)
        with (home / "config.yaml").open("a", encoding="utf-8") as stream:
            stream.write(suffix)
        called = []
        monkeypatch.setitem(
            sys.modules,
            "hermes_cli.oneshot",
            SimpleNamespace(run_strict_oneshot=lambda *args, **kwargs: called.append(True)),
        )
        assert entrypoint.main(["-z", "synthetic prompt"]) == 2
        assert capsys.readouterr().err == "strict_cli_rejected\n"
        assert called == []


def test_strict_yaml_rejects_duplicate_keys_before_effects(
    monkeypatch, tmp_path, capsys
):
    from hermes_cli import entrypoint

    duplicate_suffixes = (
        "  default: conflicting-model\n",
        "  provider: auto\n",
        "  output_budget_mode: ordinary\n",
        "  tools: []\n  tools: [delegate_task]\n",
        "agent: {}\nagent: {}\n",
        "model:\n  output_budget_mode: strict\n",
    )
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("STRICT_TEST_KEY", "secret-not-for-output")

    for suffix in duplicate_suffixes:
        _strict_config(home)
        with (home / "config.yaml").open("a", encoding="utf-8") as stream:
            stream.write(suffix)
        called = []
        monkeypatch.setitem(
            sys.modules,
            "hermes_cli.oneshot",
            SimpleNamespace(run_strict_oneshot=lambda *args, **kwargs: called.append(True)),
        )
        before = {
            name
            for name in sys.modules
            if name in {"hermes_cli.main", "openai", "run_agent", "model_tools"}
        }
        assert entrypoint.main(["-z", "synthetic prompt"]) == 2
        assert capsys.readouterr().err == "strict_config_rejected\n"
        assert called == []
        after = {
            name
            for name in sys.modules
            if name in {"hermes_cli.main", "openai", "run_agent", "model_tools"}
        }
        assert after == before


def test_nonstrict_duplicate_keys_delegate_to_ordinary_entrypoint(
    monkeypatch, tmp_path
):
    from hermes_cli import entrypoint

    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        "model:\n"
        "  default: ordinary-model\n"
        "agent: {system_prompt: first}\n"
        "agent: {system_prompt: second}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    delegated = []
    monkeypatch.setattr(
        entrypoint,
        "_dispatch_ordinary",
        lambda: delegated.append(True) or 73,
    )
    before = {
        name
        for name in sys.modules
        if name in {"hermes_cli.oneshot", "openai", "run_agent", "model_tools"}
    }

    assert entrypoint.main(["-z", "ordinary prompt"]) == 73
    assert delegated == [True]
    after = {
        name
        for name in sys.modules
        if name in {"hermes_cli.oneshot", "openai", "run_agent", "model_tools"}
    }
    assert after == before


def test_strict_wire_call_is_exactly_one_nonstreaming_request():
    from hermes_cli.oneshot import run_strict_oneshot

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].finish_reason = "stop"
    response.choices[0].message.content = "synthetic result"
    response.choices[0].message.refusal = None
    response.choices[0].message.tool_calls = None
    response.usage.prompt_tokens = 20
    response.usage.completion_tokens = 4
    client = MagicMock()
    client.chat.completions.create.return_value = response
    config = {
        "model": {"output_budget_mode": "strict", "max_tokens": 2000},
        "agent": {"system_prompt": "bounded system prompt"},
    }
    route = {
        "model": "synthetic-model",
        "provider": "custom",
        "base_url": "https://inference.example.invalid/v1",
        "api_key": "secret-not-for-output",
    }

    with patch("agent.process_bootstrap.OpenAI", return_value=client) as factory:
        assert run_strict_oneshot("a" * 8000, config=config, route=route) == (
            0,
            "synthetic result",
            {"input_tokens": 20, "output_tokens": 4},
        )

    factory.assert_called_once_with(
        api_key="secret-not-for-output",
        base_url="https://inference.example.invalid/v1",
        max_retries=0,
        timeout=300.0,
    )
    client.chat.completions.create.assert_called_once()
    request = client.chat.completions.create.call_args.kwargs
    assert request["max_tokens"] == 2000
    assert request["stream"] is False
    assert request["messages"][1]["content"] == "a" * 8000


def test_strict_wire_failure_never_returns_partial_content():
    from hermes_cli.oneshot import run_strict_oneshot

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].finish_reason = "length"
    response.choices[0].message.content = "private partial text"
    response.choices[0].message.refusal = None
    response.choices[0].message.tool_calls = None
    response.usage.prompt_tokens = 20
    response.usage.completion_tokens = 2000
    client = MagicMock()
    client.chat.completions.create.return_value = response
    config = {"model": {"output_budget_mode": "strict", "max_tokens": 2000}}
    route = {
        "model": "synthetic-model",
        "provider": "custom",
        "base_url": "https://inference.example.invalid/v1",
        "api_key": "secret-not-for-output",
    }

    with patch("agent.process_bootstrap.OpenAI", return_value=client):
        assert run_strict_oneshot("synthetic prompt", config=config, route=route) == (
            2,
            "",
            {},
        )
    client.chat.completions.create.assert_called_once()
