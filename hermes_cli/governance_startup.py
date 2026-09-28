"""Refuse to start when governance is required but its plugin is not loaded.

The ``ragnos-governance`` plugin fails closed on every tool call when
``RAGNOS_GOVERNANCE_REQUIRED`` is set and enforcement is not configured. That
check lives inside the plugin, so it cannot fire when the plugin itself is
missing, disabled or failed to load. This module closes that gap from core:
when governance is required and the plugin is not registered with a
``pre_tool_call`` hook, entry points refuse to start and ``AIAgent`` refuses
to build.

``RAGNOS_GOVERNANCE_REQUIRED`` is read the way the plugin reads it: the
process environment first, then ``$HERMES_HOME/governance.env``. A
``governance.env`` that exists but cannot be read counts as required.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

GOVERNANCE_PLUGIN = "ragnos-governance"
REQUIRED_ENV = "RAGNOS_GOVERNANCE_REQUIRED"
ENV_FILE_NAME = "governance.env"
ERROR_CODE = "governance_plugin_not_loaded"

_TRUTHY = {"1", "true", "yes", "on"}


class GovernanceNotLoadedError(RuntimeError):
    """Governance is required but the governance plugin is not registered."""


def _env_file_value(path: Path) -> Optional[str]:
    """``RAGNOS_GOVERNANCE_REQUIRED`` from ``governance.env``, if set there.

    Raises ``OSError`` or ``UnicodeDecodeError`` when the file exists but
    cannot be read; the caller treats that as required.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    value: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, rest = line.partition("=")
        if sep and key.strip() == REQUIRED_ENV:
            rest = rest.strip()
            if len(rest) >= 2 and rest[0] == rest[-1] and rest[0] in {'"', "'"}:
                rest = rest[1:-1]
            value = rest
    return value


def governance_required() -> bool:
    """Whether ``RAGNOS_GOVERNANCE_REQUIRED`` is set for this process."""
    if REQUIRED_ENV in os.environ:
        return os.environ[REQUIRED_ENV].strip().lower() in _TRUTHY
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.path.expanduser("~/.hermes"))
    try:
        value = _env_file_value(home / ENV_FILE_NAME)
    except Exception:
        return True
    return value is not None and value.strip().lower() in _TRUTHY


def governance_plugin_loaded() -> bool:
    """Whether the governance plugin is enabled, loaded and gating tool calls."""
    from hermes_cli.plugins import discover_plugins, get_plugin_manager

    discover_plugins()
    manager = get_plugin_manager()
    for key, loaded in getattr(manager, "_plugins", {}).items():
        name = getattr(getattr(loaded, "manifest", None), "name", None)
        if GOVERNANCE_PLUGIN not in (key, name):
            continue
        if (
            getattr(loaded, "enabled", False)
            and not getattr(loaded, "error", None)
            and "pre_tool_call" in (getattr(loaded, "hooks_registered", None) or [])
        ):
            return True
    return False


def governance_startup_error() -> Optional[Dict[str, Any]]:
    """A JSON-ready error when governance is required but not loaded, else None."""
    if not governance_required():
        return None
    try:
        loaded = governance_plugin_loaded()
        detail = ""
    except Exception as exc:
        loaded = False
        detail = f" (plugin discovery failed: {type(exc).__name__})"
    if loaded:
        return None
    return {
        "error": ERROR_CODE,
        "message": (
            f"{REQUIRED_ENV} is set but the {GOVERNANCE_PLUGIN} plugin is not "
            f"loaded{detail}. Enable it under plugins.enabled in config.yaml, "
            "or unset the requirement."
        ),
        "plugin": GOVERNANCE_PLUGIN,
        "required_env": REQUIRED_ENV,
    }


def enforce_governance_startup() -> None:
    """Raise :class:`GovernanceNotLoadedError` when the startup check fails."""
    error = governance_startup_error()
    if error is not None:
        raise GovernanceNotLoadedError(error["message"])
