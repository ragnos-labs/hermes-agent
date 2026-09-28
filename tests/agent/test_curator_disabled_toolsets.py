"""The curator review agent honors agent.disabled_toolsets."""
from __future__ import annotations

import pytest

from tests.agent.test_curator import curator_env  # noqa: F401  (fixture)


@pytest.fixture
def real_review(curator_env, monkeypatch):  # noqa: F811
    import importlib

    curator = importlib.reload(curator_env["curator"])
    captured = {"calls": 0}

    class _StubAgent:
        def __init__(self, *args, **kwargs):
            captured["calls"] += 1
            captured["kwargs"] = kwargs
            self._memory_write_origin = "assistant_tool"
            self._memory_nudge_interval = 0
            self._skill_nudge_interval = 0
            self._session_messages = []

        def run_conversation(self, user_message=None, **kwargs):
            return {"final_response": "no change"}

        def close(self):
            pass

    monkeypatch.setattr("run_agent.AIAgent", _StubAgent)
    return curator, captured


def test_review_agent_receives_disabled_toolsets(real_review, monkeypatch):
    curator, captured = real_review
    monkeypatch.setattr(
        "hermes_cli.tools_config.load_disabled_toolsets", lambda config=None: ["web"]
    )

    meta = curator._run_llm_review("review prompt")

    assert meta.get("error") is None, meta.get("error")
    assert captured["kwargs"]["disabled_toolsets"] == ["web"]
    assert captured["kwargs"]["enabled_toolsets"] == ["skills"]


def test_review_skipped_when_skills_disabled(real_review, monkeypatch):
    curator, captured = real_review
    monkeypatch.setattr(
        "hermes_cli.tools_config.load_disabled_toolsets", lambda config=None: ["skills"]
    )

    meta = curator._run_llm_review("review prompt")

    assert captured["calls"] == 0
    assert "agent.disabled_toolsets" in meta["error"]


def test_review_fails_closed_when_disabled_list_unreadable(real_review, monkeypatch):
    curator, captured = real_review

    def _boom(config=None):
        raise RuntimeError("bad config")

    monkeypatch.setattr("hermes_cli.tools_config.load_disabled_toolsets", _boom)

    meta = curator._run_llm_review("review prompt")

    assert captured["calls"] == 0
    assert "bad config" in meta["error"]


@pytest.mark.parametrize(
    "caps",
    [{"curator": ["file"]}, {"cli": ["file"]}],
)
def test_review_skipped_when_cap_excludes_skills(real_review, monkeypatch, caps):
    curator, captured = real_review
    monkeypatch.setattr(
        "hermes_cli.config.load_config", lambda *a, **k: {"platform_toolsets": dict(caps)}
    )

    meta = curator._run_llm_review("review prompt")

    assert captured["calls"] == 0
    assert "allowlist" in meta["error"]


def test_review_agent_runs_when_cap_allows_skills(real_review, monkeypatch):
    curator, captured = real_review
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda *a, **k: {"platform_toolsets": {"curator": ["skills", "file"]}},
    )

    meta = curator._run_llm_review("review prompt")

    assert meta.get("error") is None, meta.get("error")
    assert captured["kwargs"]["enabled_toolsets"] == ["skills"]
