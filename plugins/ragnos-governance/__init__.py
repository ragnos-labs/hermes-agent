"""ragnos-governance plugin: universal pre-tool-call gate + governance telemetry.

OBSERVE by default (records every tool call to the governance ledger). Set
``RAGNOS_GOVERNANCE_ENFORCE=1`` plus ``RAGNOS_GOVERNANCE_FORBIDDEN_TOOLS`` to
BLOCK gated tools so they route through the Hermes Hub (preview + approval).
Settings may also live in ``$HERMES_HOME/governance.env`` (the process
environment wins), forbidden entries may name toolsets, and
``RAGNOS_GOVERNANCE_REQUIRED=1`` fails closed when enforcement is not
configured. See ``governance.py`` and ``README.md``.

This is the Sprint 2 governance overlay from the Hermes realignment spec. It is
additive and lives entirely in the RAGnos-owned ``plugins/ragnos-governance/``
surface; it never edits upstream core, so the upstream conformance gate stays
green.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Optional

if __package__:
    from . import governance
    from .local_tts import register_local_http_streamer
else:
    _spec = importlib.util.spec_from_file_location(
        "ragnos_governance_governance", Path(__file__).with_name("governance.py")
    )
    if _spec is None or _spec.loader is None:
        raise ImportError("unable to load RAGnos governance policy")
    governance = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(governance)
    register_local_http_streamer = None


def _on_pre_tool_call(*, tool_name: str = "", args: Optional[dict] = None, **_kwargs: Any) -> Optional[dict]:
    env = governance.effective_env()
    enforce = governance.is_enforcing(env)
    missing = governance.missing_required_config(env) if governance.is_required(env) else None
    if missing:
        verdict = governance.required_config_block(missing)
    else:
        verdict = governance.evaluate_tool(
            tool_name,
            args,
            enforce=enforce,
            forbidden=governance.expand_forbidden(governance.forbidden_tools(env)),
        )
    governance.record_event(
        "pre_tool_call",
        {
            "tool": tool_name,
            "enforce": enforce,
            "blocked": verdict is not None,
            "required_config_missing": bool(missing),
        },
        env=env,
    )
    return verdict


def _on_post_tool_call(*, tool_name: str = "", **_kwargs: Any) -> None:
    governance.record_event("post_tool_call", {"tool": tool_name}, env=governance.effective_env())


def _on_session_end(**_kwargs: Any) -> None:
    governance.record_event("session_end", {}, env=governance.effective_env())



def register(ctx) -> None:
    if register_local_http_streamer is not None:
        register_local_http_streamer()
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("on_session_end", _on_session_end)
