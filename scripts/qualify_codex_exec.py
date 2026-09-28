#!/usr/bin/env python3
"""Opt-in live proof of native memory/session tools over the Codex CLI.

Only synthetic content is used. A fresh private Hermes home is created under
the supplied evidence directory; the user's existing Hermes home is untouched.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    directory = args.evidence_dir.resolve()
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    home = directory / "profile"
    home.mkdir(mode=0o700)
    allowed = {"HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "CODEX_HOME",
               "SYSTEMROOT", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP"}
    for key in list(os.environ):
        if key not in allowed:
            del os.environ[key]
    os.environ["HERMES_HOME"] = str(home)
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    import yaml
    config = {
        "model": {"provider": "codex_exec", "default": args.model},
        "codex_exec": {"reasoning_effort": "high", "timeout_seconds": 120},
        "memory": {"memory_enabled": True, "user_profile_enabled": True},
        "auxiliary": {"background_review": {"provider": "codex_exec", "model": args.model}},
    }
    (home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    from agent.codex_exec_client import BASE_URL, CodexExecClient
    from agent.auxiliary_client import get_text_auxiliary_client
    from hermes_state import SessionDB
    from run_agent import AIAgent

    calls = []
    original_create = CodexExecClient.create

    def record(self, **kwargs):
        result = original_create(self, **kwargs)
        calls.append({"model": kwargs["model"], "backend": "codex_exec", "stream": bool(kwargs.get("stream"))})
        return result

    CodexExecClient.create = record
    db = SessionDB(home / "state.db")
    prior = "codex-exec-archive-" + uuid.uuid4().hex
    marker = "AMBER-RIVER-" + uuid.uuid4().hex[:8]
    db.create_session(prior, source="cli", model=args.model)
    db.append_message(prior, "user", "Synthetic archive checkword: " + marker)
    lesson = "For the synthetic lighthouse experiment, verify the battery before replacing the lamp."

    def agent(session):
        return AIAgent(
            provider="codex_exec", requested_provider="codex_exec", model=args.model,
            api_mode="chat_completions", base_url=BASE_URL, api_key="codex-exec",
            enabled_toolsets=["memory", "session_search"], max_iterations=8,
            quiet_mode=True, skip_context_files=True, skip_background_review=True,
            session_id=session, session_db=db, run_budget_seconds=300,
        )

    first = agent("codex-exec-proof-" + uuid.uuid4().hex)
    try:
        result = first.run_conversation(
            "Perform this synthetic integration test. First use the memory tool to add exactly "
            + json.dumps(lesson) + " to target memory. Then use session_search to read session_id "
            + prior + ". Report the archive checkword from its actual tool result. Do not guess it."
        )
        assert marker in result.get("final_response", ""), "session lookup did not return the archived marker"
        assert lesson in (home / "memories" / "MEMORY.md").read_text(encoding="utf-8"), "memory was not persisted"
        used = set()
        for message in result.get("messages", []):
            for tool in message.get("tool_calls") or []:
                used.add(tool.get("function", {}).get("name"))
        assert {"memory", "session_search"} <= used, "native tools were not both invoked"
    finally:
        first.client.close()
        first.shutdown_memory_provider()

    second = agent("codex-exec-recall-" + uuid.uuid4().hex)
    try:
        recall = second.run_conversation("From your retained memory, what must be verified before replacing the synthetic lighthouse lamp?")
        assert "battery" in recall.get("final_response", "").lower(), "fresh session did not recall the lesson"
    finally:
        second.client.close()
        second.shutdown_memory_provider()

    auxiliary, model = get_text_auxiliary_client("background_review")
    assert isinstance(auxiliary, CodexExecClient), "auxiliary work selected another backend"
    learned = auxiliary.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "Summarize this synthetic lesson in one sentence: " + lesson}],
    )
    assert "battery" in learned.choices[0].message.content.lower()
    auxiliary.close()
    db.close()
    receipt = {
        "schema_version": 1, "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, encoding="utf-8",
        ).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True, encoding="utf-8")),
        "model": args.model, "calls": calls,
        "checks": ["native_memory_write", "native_session_search", "fresh_session_recall", "auxiliary_learning", "cli_only"],
        "status": "passed", "runtime_qualification": False,
    }
    (directory / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "calls": len(calls), "checks": receipt["checks"]}))


if __name__ == "__main__":
    main()
