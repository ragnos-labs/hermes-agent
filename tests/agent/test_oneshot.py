"""Tests for agent.oneshot — shared one-off (stateless) LLM requests."""

from unittest.mock import MagicMock, patch

import pytest

from agent.oneshot import (
    PROMPT_TEMPLATES,
    render_template,
    run_oneshot,
    _strip_code_fence,
    _truncate,
)
from agent.conversation_loop import (
    _require_strict_single_attempt_mode,
    _reserve_strict_output_budget,
)
from hermes_cli.oneshot import _run_agent as run_cli_oneshot_agent
from hermes_cli.oneshot import _strict_output_budget


class TestRenderTemplate:


    def test_commit_message_includes_diff_and_recent(self):
        instructions, user = render_template(
            "commit_message",
            {"diff": "diff --git a/x b/x\n+new", "recent_commits": "feat: a\nfix: b"},
        )
        # Instructions describe the contract (conventional commits), not a snapshot.
        assert "Conventional Commits" in instructions
        assert "diff --git a/x b/x" in user
        assert "feat: a" in user



    def test_commit_message_avoid_forces_new_message(self):
        # Passing the previous message must instruct the model not to repeat it,
        # so "regenerate" yields a different result even on greedy models.
        _, plain = render_template("commit_message", {"diff": "d"})
        _, regen = render_template("commit_message", {"diff": "d", "avoid": "feat: prior"})
        assert "feat: prior" in regen
        assert "do not repeat" in regen
        assert "feat: prior" not in plain


class TestRunOneshot:
    def _mock_response(self, content):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = content
        resp.choices[0].message.reasoning = None
        resp.choices[0].message.reasoning_content = None
        resp.choices[0].message.reasoning_details = None
        return resp


    def test_explicit_instructions_path(self):
        with patch(
            "agent.oneshot.call_llm",
            return_value=self._mock_response("hello"),
        ) as llm:
            out = run_oneshot(instructions="be brief", user_input="say hi")

        assert out == "hello"
        messages = llm.call_args.kwargs["messages"]
        assert messages[0]["content"] == "be brief"
        assert messages[1]["content"] == "say hi"


    def test_strips_wrapping_code_fence(self):
        with patch(
            "agent.oneshot.call_llm",
            return_value=self._mock_response("```\nfix: bug\n```"),
        ):
            assert run_oneshot(instructions="x", user_input="y") == "fix: bug"


class TestHelpers:
    def test_strict_output_budget_requires_positive_model_cap(self):
        assert _strict_output_budget(
            {"model": {"output_budget_mode": "strict", "max_tokens": 2000}}
        ) == 2000

        with pytest.raises(ValueError, match="positive integer"):
            _strict_output_budget(
                {"model": {"output_budget_mode": "strict", "max_tokens": True}}
            )

    def test_strict_output_budget_is_default_off(self):
        assert _strict_output_budget({"model": {"max_tokens": 2000}}) is None

    def test_cli_oneshot_propagates_strict_cap_and_disables_reasoning(self):
        fake_agent = MagicMock()
        fake_agent.run_conversation.return_value = {"final_response": "ok"}
        fake_agent._session_messages = []
        runtime = {
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "provider": "test",
            "requested_provider": "test",
            "api_mode": "chat_completions",
            "credential_pool": None,
        }
        with (
            patch(
                "hermes_cli.config.load_config",
                return_value={
                    "model": {
                        "default": "test-model",
                        "output_budget_mode": "strict",
                        "max_tokens": 2000,
                    }
                },
            ),
            patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value=runtime),
            patch("hermes_cli.tools_config._get_platform_tools", return_value=set()),
            patch("hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build"),
            patch("hermes_cli.oneshot._create_session_db_for_oneshot", return_value=None),
            patch("hermes_cli.oneshot.get_fallback_chain", return_value=[]),
            patch("run_agent.AIAgent", return_value=fake_agent) as agent_type,
        ):
            text, result = run_cli_oneshot_agent("hello")

        assert text == "ok"
        assert result["final_response"] == "ok"
        assert agent_type.call_args.kwargs["max_tokens"] == 2000
        assert agent_type.call_args.kwargs["reasoning_config"] == {"enabled": False}
        assert fake_agent._strict_output_token_budget == 2000
        assert fake_agent._strict_output_tokens_reserved == 0

    def test_strict_output_budget_accepts_bedrock_wire_cap_once(self):
        agent = MagicMock()
        agent._strict_output_token_budget = 2000
        agent._strict_output_tokens_reserved = 0
        agent.request_overrides = {}

        _reserve_strict_output_budget(
            agent, {"inferenceConfig": {"maxTokens": 2000}}
        )

        assert agent._strict_output_tokens_reserved == 2000
        with pytest.raises(RuntimeError, match="exhausted"):
            _reserve_strict_output_budget(
                agent, {"inferenceConfig": {"maxTokens": 1}}
            )

    @pytest.mark.parametrize(
        ("provider", "api_mode"),
        [
            ("openai", "codex_responses"),
            ("anthropic", "anthropic_messages"),
            ("moa", "chat_completions"),
        ],
    )
    def test_strict_output_budget_rejects_multi_attempt_modes(
        self, provider, api_mode
    ):
        agent = MagicMock()
        agent._strict_output_token_budget = 2000
        agent.provider = provider
        agent.api_mode = api_mode

        with pytest.raises(RuntimeError, match="does not support"):
            _require_strict_single_attempt_mode(agent)

    @pytest.mark.parametrize("api_mode", ["chat_completions", "bedrock_converse"])
    def test_strict_output_budget_allows_single_attempt_modes(self, api_mode):
        agent = MagicMock()
        agent._strict_output_token_budget = 2000
        agent.provider = "test"
        agent.api_mode = api_mode

        _require_strict_single_attempt_mode(agent)

    def test_truncate_under_limit_unchanged(self):
        assert _truncate("short", 100) == "short"

    def test_truncate_over_limit_marks_truncation(self):
        out = _truncate("x" * 200, 50)
        assert out.endswith("…(truncated)")
        assert len(out) < 200

    def test_strip_code_fence_without_fence_is_noop(self):
        assert _strip_code_fence("plain text") == "plain text"
