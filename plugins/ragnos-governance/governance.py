"""RAGnos governance policy: a deterministic pre-tool-call gate + telemetry.

Pure, dependency-free policy so it is hermetically testable. The plugin
(``__init__.py``) wires these functions into the gateway's ``pre_tool_call`` /
``post_tool_call`` / ``on_session_end`` hooks.

OBSERVE by default: every tool call is recorded to a JSONL governance ledger.
Set ``RAGNOS_GOVERNANCE_ENFORCE=1`` (plus ``RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS``)
to BLOCK gated tools so they must route through the Hermes Hub (preview +
approval). This file edits no upstream code; it lives only under
``plugins/ragnos-governance/`` (a RAGnos-owned surface).

Settings come from the process environment, then from an optional
``$HERMES_HOME/governance.env`` file (``KEY=VALUE`` lines, ``#`` comments,
optional ``export`` prefix). The process environment wins on conflicts. Only
``RAGNOS_GOVERNANCE_*`` keys are read from the file. Services that start
Hermes without the operator's shell environment (gateway units, cron, the
desktop app) pick up the policy from the file.

``RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS`` accepts tool names and toolset names.
A toolset name (``terminal``, ``hermes-sms``, ``mcp-github``, ``all``) blocks
every tool it resolves to at call time, so tools that MCP servers or plugins
register later are covered.

Fail-open is the default: with no settings, nothing is blocked. Set
``RAGNOS_GOVERNANCE_REQUIRED=1`` to fail closed instead: while it is set and
the effective settings do not enable enforcement with a non-empty forbidden
list (for example because ``governance.env`` is missing or unreadable), every
tool call is blocked with a message that says what is missing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

ENFORCE_ENV = "RAGNOS_GOVERNANCE_ENFORCE"
FORBIDDEN_ENV = "RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS"
LEDGER_ENV = "RAGNOS_GOVERNANCE_LEDGER"
REQUIRED_ENV = "RAGNOS_GOVERNANCE_REQUIRED"
ENV_FILE_NAME = "governance.env"
_KEY_PREFIX = "RAGNOS_GOVERNANCE_"
# Set in the effective settings when governance.env exists but cannot be read
# or parsed, so the fail-closed check can report it.
ENV_FILE_ERROR_KEY = "_RAGNOS_GOVERNANCE_ENV_FILE_ERROR"

_TRUTHY = {"1", "true", "yes", "on"}

_env_file_cache: dict[str, tuple[tuple[int, int], dict[str, str]]] = {}


def _hermes_home(env: Mapping[str, str]) -> Path:
    raw = env.get("HERMES_HOME")
    if raw:
        return Path(str(raw)).expanduser()
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001 - keep the policy importable standalone.
        return Path.home() / ".hermes"


def env_file_path(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return _hermes_home(env) / ENV_FILE_NAME


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines, keeping only ``RAGNOS_GOVERNANCE_*`` keys."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key.startswith(_KEY_PREFIX):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def read_env_file(path: Path) -> dict[str, str]:
    """Read ``governance.env``; cached on mtime and size. Missing file gives {}."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {}
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _env_file_cache.get(str(path))
    if cached is not None and cached[0] == signature:
        return dict(cached[1])
    values = parse_env_file(path.read_text(encoding="utf-8"))
    _env_file_cache[str(path)] = (signature, values)
    return dict(values)


def effective_env(env: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """Merge ``$HERMES_HOME/governance.env`` under the given environment.

    Keys already present in ``env`` (default ``os.environ``) win. A file that
    exists but cannot be read adds ``ENV_FILE_ERROR_KEY`` instead of values.
    """
    env = os.environ if env is None else env
    merged: dict[str, str] = {}
    path = env_file_path(env)
    try:
        merged.update(read_env_file(path))
    except Exception as exc:  # noqa: BLE001 - reported through REQUIRED.
        merged[ENV_FILE_ERROR_KEY] = f"{path}: {type(exc).__name__}"
    merged.update({k: str(v) for k, v in env.items()})
    return merged


def is_required(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether governance enforcement is required.

    Matches ``hermes_cli.governance_startup.governance_required``: when
    ``RAGNOS_GOVERNANCE_REQUIRED`` is not set in the environment and
    ``governance.env`` exists but could not be read (``ENV_FILE_ERROR_KEY``
    from :func:`effective_env`), the setting may be in the unreadable file,
    so enforcement is required and fails closed.
    """
    env = os.environ if env is None else env
    if REQUIRED_ENV not in env and env.get(ENV_FILE_ERROR_KEY):
        return True
    return str(env.get(REQUIRED_ENV, "")).strip().lower() in _TRUTHY


def missing_required_config(env: Mapping[str, str]) -> Optional[str]:
    """Return why enforcement is not configured, or None when it is.

    Only meaningful when ``RAGNOS_GOVERNANCE_REQUIRED`` is set.
    """
    problems = []
    if env.get(ENV_FILE_ERROR_KEY):
        problems.append(f"{ENV_FILE_NAME} could not be read")
    if not is_enforcing(env):
        problems.append(f"{ENFORCE_ENV} is not enabled")
    if not forbidden_tools(env):
        problems.append(f"{FORBIDDEN_ENV} is empty")
    return "; ".join(problems) or None


def required_config_block(reason: str) -> dict[str, str]:
    return {
        "action": "block",
        "message": (
            f"RAGnos governance: {REQUIRED_ENV} is set but enforcement is not "
            f"configured ({reason}). Every tool call is blocked until the "
            f"governance settings are fixed in the environment or {ENV_FILE_NAME}."
        ),
    }


def expand_forbidden(names: frozenset[str]) -> frozenset[str]:
    """Add the tools of every entry that names a toolset.

    Entries stay in the result as tool names too, since a tool and a toolset
    can share a name (``terminal``). Resolution failures keep the raw names.
    """
    if not names:
        return names
    expanded = set(names)
    try:
        from toolsets import resolve_toolset
    except Exception:  # noqa: BLE001 - standalone use without Hermes.
        return frozenset(expanded)
    for name in names:
        try:
            expanded.update(resolve_toolset(name))
        except Exception:  # noqa: BLE001 - never break a tool call here.
            continue
    return frozenset(expanded)


def is_enforcing(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(ENFORCE_ENV, "")).strip().lower() in _TRUTHY


def forbidden_tools(env: Optional[Mapping[str, str]] = None) -> frozenset[str]:
    env = os.environ if env is None else env
    raw = str(env.get(FORBIDDEN_ENV, "")).strip()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.replace("\n", ",").split(",") if part.strip())


def evaluate_tool(
    tool_name: str,
    args: Optional[Mapping[str, Any]] = None,
    *,
    enforce: bool = False,
    forbidden: frozenset[str] = frozenset(),
) -> Optional[dict[str, str]]:
    """Return a block directive when a gated tool runs under enforcement, else None.

    The return shape matches the gateway's ``get_pre_tool_call_block_message``
    contract: ``{"action": "block", "message": "..."}``.
    """
    if enforce and tool_name in forbidden:
        return {
            "action": "block",
            "message": (
                f"RAGnos governance: tool '{tool_name}' is gated. Route it through "
                "the Hermes Hub (preview + approval) instead of calling it directly."
            ),
        }
    return None


def record_event(event_type: str, payload: Mapping[str, Any], *, env: Optional[Mapping[str, str]] = None) -> None:
    """Append a governance telemetry event to the JSONL ledger (best effort)."""
    env = os.environ if env is None else env
    path = ledger_path(env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {"event_type": event_type, **dict(payload)}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
    except Exception:  # noqa: BLE001 - telemetry must never break a tool call.
        pass


def ledger_path(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    raw = env.get(LEDGER_ENV)
    if raw:
        return Path(str(raw)).expanduser()
    home = env.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return Path(home).expanduser() / "ragnos-governance" / "governance-ledger.jsonl"
