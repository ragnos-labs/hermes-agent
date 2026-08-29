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
from agent.turn_finalizer import finalize_turn
from hermes_cli.oneshot import _run_agent as run_cli_oneshot_agent
from hermes_cli.oneshot import _strict_output_budget
from hermes_cli.oneshot import run_oneshot as run_cli_oneshot
from run_agent import AIAgent


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
    def test_strict_output_budget_requires_exact_2000_model_cap(self):
        assert _strict_output_budget(
            {"model": {"output_budget_mode": "strict", "max_tokens": 2000}}
        ) == 2000

        for invalid_cap in (True, 1999, 2001, 50_000, "2000"):
            with pytest.raises(ValueError, match="max_tokens == 2000"):
                _strict_output_budget(
                    {"model": {"output_budget_mode": "strict", "max_tokens": invalid_cap}}
                )

    def test_strict_output_budget_is_default_off(self):
        assert _strict_output_budget({"model": {"max_tokens": 2000}}) is None

    def test_cli_oneshot_propagates_strict_cap_and_disables_reasoning(self):
        captured_kwargs = {}

        class FakeAgent:
            def __init__(self, **kwargs):
                assert self._suppress_external_effects is True
                captured_kwargs.update(kwargs)
                self._session_messages = []

            def run_conversation(self, prompt):
                self.prompt = prompt
                return {"final_response": "ok", "completed": True}

            def close(self):
                return None

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
            patch("hermes_cli.tools_config._get_platform_tools") as platform_tools,
            patch(
                "hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build"
            ) as mcp_discovery,
            patch("hermes_cli.oneshot._create_session_db_for_oneshot") as session_db,
            patch("hermes_cli.oneshot.get_fallback_chain") as fallback_chain,
            patch("run_agent.AIAgent", FakeAgent),
        ):
            strict_prompt = "a" * 8000
            text, result = run_cli_oneshot_agent(strict_prompt)

        assert text == "ok"
        assert result["final_response"] == "ok"
        assert captured_kwargs["max_tokens"] == 2000
        assert captured_kwargs["reasoning_config"] == {"enabled": False}
        assert captured_kwargs["skip_background_review"] is True
        assert captured_kwargs["enabled_toolsets"] == []
        assert captured_kwargs["session_db"] is None
        assert captured_kwargs["fallback_model"] is None
        assert captured_kwargs["skip_context_files"] is True
        assert captured_kwargs["skip_memory"] is True
        assert captured_kwargs["clarify_callback"] is None
        assert captured_kwargs["max_tokens"] == 2000
        platform_tools.assert_not_called()
        mcp_discovery.assert_not_called()
        session_db.assert_not_called()
        fallback_chain.assert_not_called()

    @pytest.mark.parametrize("prompt", [None, "not-ascii-\N{SNOWMAN}", "a" * 8001])
    def test_public_cli_rejects_strict_prompt_before_any_effectful_setup(
        self, prompt, capsys
    ):
        strict_cfg = {
            "model": {
                "default": "test-model",
                "output_budget_mode": "strict",
                "max_tokens": 2000,
            }
        }
        with (
            patch("hermes_cli.config.load_config", return_value=strict_cfg),
            patch("hermes_cli.oneshot._validate_explicit_toolsets") as toolset_lookup,
            patch("hermes_cli.oneshot.declare_stateless_channel") as declare_channel,
            patch("hermes_cli.oneshot._run_agent") as run_agent,
            patch.dict("os.environ", {}, clear=False),
        ):
            status = run_cli_oneshot(prompt)

        assert status == 2
        assert capsys.readouterr().out == ""
        toolset_lookup.assert_not_called()
        declare_channel.assert_not_called()
        run_agent.assert_not_called()

    def test_public_cli_rejects_strict_toolsets_before_lookup_or_channel(self):
        strict_cfg = {
            "model": {
                "default": "test-model",
                "output_budget_mode": "strict",
                "max_tokens": 2000,
            }
        }
        with (
            patch("hermes_cli.config.load_config", return_value=strict_cfg),
            patch("hermes_cli.oneshot._validate_explicit_toolsets") as toolset_lookup,
            patch("hermes_cli.oneshot.declare_stateless_channel") as declare_channel,
            patch("hermes_cli.oneshot._run_agent") as run_agent,
        ):
            status = run_cli_oneshot("hello", toolsets=["web"])

        assert status == 2
        toolset_lookup.assert_not_called()
        declare_channel.assert_not_called()
        run_agent.assert_not_called()

    def test_strict_constructor_skips_plugin_and_configured_context_engines(self):
        strict_cfg = {
            "model": {
                "default": "test-model",
                "output_budget_mode": "strict",
                "max_tokens": 2000,
            },
            "context": {"engine": "configured-plugin-engine"},
            "memory": {"provider": "configured-memory-plugin"},
        }
        runtime = {
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "provider": "test",
            "requested_provider": "test",
            "api_mode": "chat_completions",
            "credential_pool": None,
        }
        with (
            patch("hermes_cli.config.load_config", return_value=strict_cfg),
            patch(
                "hermes_cli.runtime_provider.resolve_runtime_provider",
                return_value=runtime,
            ),
            patch("hermes_cli.plugins.discover_plugins") as discover_plugins,
            patch("hermes_cli.plugins.get_plugin_context_engine") as plugin_context,
            patch("plugins.context_engine.load_context_engine") as context_loader,
            patch("plugins.memory.load_memory_provider") as memory_loader,
            patch.object(AIAgent, "_create_openai_client", return_value=MagicMock()),
            patch.object(
                AIAgent,
                "run_conversation",
                return_value={"final_response": "ok", "completed": True},
            ) as run_conversation,
            patch.object(AIAgent, "close"),
        ):
            text, result = run_cli_oneshot_agent("hello")

        assert text == "ok"
        assert result["completed"] is True
        discover_plugins.assert_not_called()
        plugin_context.assert_not_called()
        context_loader.assert_not_called()
        memory_loader.assert_not_called()
        run_conversation.assert_called_once_with("hello")

    @pytest.mark.parametrize(
        ("prompt", "message"),
        [
            (None, "must be a string"),
            ("not-ascii-\N{SNOWMAN}", "ASCII only"),
            ("a" * 8001, "at most 8000 bytes"),
        ],
    )
    def test_cli_oneshot_rejects_invalid_strict_prompt_before_runtime_setup(
        self, prompt, message
    ):
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
            patch(
                "hermes_cli.runtime_provider.resolve_runtime_provider"
            ) as resolve_runtime,
            patch("hermes_cli.tools_config._get_platform_tools") as platform_tools,
            patch(
                "hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build"
            ) as mcp_discovery,
            patch("hermes_cli.oneshot._create_session_db_for_oneshot") as session_db,
            patch("hermes_cli.oneshot.get_fallback_chain") as fallback_chain,
            patch("run_agent.AIAgent") as agent_type,
            pytest.raises(ValueError, match=message),
        ):
            run_cli_oneshot_agent(prompt)

        resolve_runtime.assert_not_called()
        platform_tools.assert_not_called()
        mcp_discovery.assert_not_called()
        session_db.assert_not_called()
        fallback_chain.assert_not_called()
        agent_type.assert_not_called()

    def test_cli_oneshot_ordinary_mode_preserves_stateful_setup(self):
        fake_agent = MagicMock()
        fake_agent.run_conversation.return_value = {
            "final_response": "ok",
            "completed": True,
        }
        fake_agent._session_messages = []
        runtime = {
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "provider": "test",
            "requested_provider": "test",
            "api_mode": "chat_completions",
            "credential_pool": None,
        }
        session = object()
        fallback = [{"provider": "other", "model": "backup"}]

        with (
            patch(
                "hermes_cli.config.load_config",
                return_value={"model": {"default": "test-model"}},
            ),
            patch(
                "hermes_cli.runtime_provider.resolve_runtime_provider",
                return_value=runtime,
            ),
            patch(
                "hermes_cli.tools_config._get_platform_tools",
                return_value={"web"},
            ) as platform_tools,
            patch(
                "hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build"
            ) as mcp_discovery,
            patch(
                "hermes_cli.oneshot._create_session_db_for_oneshot",
                return_value=session,
            ) as session_db,
            patch(
                "hermes_cli.oneshot.get_fallback_chain",
                return_value=fallback,
            ) as fallback_chain,
            patch("run_agent.AIAgent", return_value=fake_agent) as agent_type,
        ):
            text, result = run_cli_oneshot_agent("hello")

        assert text == "ok"
        assert result["completed"] is True
        assert agent_type.call_args.kwargs["enabled_toolsets"] == ["web"]
        assert agent_type.call_args.kwargs["session_db"] is session
        assert agent_type.call_args.kwargs["fallback_model"] == fallback
        assert agent_type.call_args.kwargs["skip_context_files"] is False
        assert agent_type.call_args.kwargs["skip_memory"] is False
        assert agent_type.call_args.kwargs["skip_background_review"] is False
        platform_tools.assert_called_once()
        mcp_discovery.assert_called_once()
        session_db.assert_called_once()
        fallback_chain.assert_called_once()

    def test_cli_oneshot_rejects_strict_toolsets_before_runtime_setup(self):
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
            patch(
                "hermes_cli.runtime_provider.resolve_runtime_provider"
            ) as resolve_runtime,
            patch("hermes_cli.tools_config._get_platform_tools") as platform_tools,
            patch(
                "hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build"
            ) as mcp_discovery,
            patch("hermes_cli.oneshot._create_session_db_for_oneshot") as session_db,
            patch("hermes_cli.oneshot.get_fallback_chain") as fallback_chain,
            patch("run_agent.AIAgent") as agent_type,
            pytest.raises(ValueError, match="does not accept toolsets"),
        ):
            run_cli_oneshot_agent("hello", toolsets=["web"])

        resolve_runtime.assert_not_called()
        platform_tools.assert_not_called()
        mcp_discovery.assert_not_called()
        session_db.assert_not_called()
        fallback_chain.assert_not_called()
        agent_type.assert_not_called()

    def test_cli_oneshot_strict_failure_never_prints_partial_content(
        self, capsys
    ):
        with patch(
            "hermes_cli.oneshot._run_agent",
            return_value=(
                "provider text must stay hidden",
                {
                    "final_response": "provider text must stay hidden",
                    "completed": False,
                    "partial": True,
                    "failed": True,
                    "strict_output_budget": True,
                },
            ),
        ):
            status = run_cli_oneshot("hello")

        captured = capsys.readouterr()
        assert status == 2
        assert captured.out == ""
        assert "provider text must stay hidden" not in captured.err

    @pytest.mark.parametrize(
        ("strict_mode", "expected_review_calls"),
        [(True, 0), (False, 1)],
    )
    def test_cli_oneshot_background_review_policy_follows_strict_mode(
        self, strict_mode, expected_review_calls
    ):
        model_config = {"default": "test-model", "max_tokens": 2000}
        if strict_mode:
            model_config["output_budget_mode"] = "strict"
        runtime = {
            "api_key": "test-key",
            "base_url": "https://example.invalid/v1",
            "provider": "test",
            "requested_provider": "test",
            "api_mode": "chat_completions",
            "credential_pool": None,
        }
        created = []

        def build_agent(**kwargs):
            # Keep this behavioral harness isolated from local context and
            # memory while preserving the caller's explicit keyword shape.
            kwargs["skip_context_files"] = True
            kwargs["skip_memory"] = True
            with patch.object(
                AIAgent, "_create_openai_client", return_value=MagicMock()
            ):
                agent = AIAgent(**kwargs)
            agent._spawn_background_review = MagicMock()
            agent._save_trajectory = MagicMock()
            agent._cleanup_task_resources = MagicMock()
            agent._persist_session = MagicMock()
            agent._session_messages = []
            agent._file_mutation_verifier_enabled = lambda: False
            agent.clear_interrupt = MagicMock()
            agent._stream_callback = None
            agent._sync_external_memory_for_turn = MagicMock()
            agent._skill_nudge_interval = 1
            agent._iters_since_skill = 1
            agent.valid_tool_names = {"skill_manage"}
            agent.iteration_budget = MagicMock()
            agent.iteration_budget.remaining = 100
            agent.iteration_budget.used = 1
            agent.iteration_budget.max_total = 100
            agent.max_iterations = 50
            agent._emit_status = MagicMock()
            agent._safe_print = MagicMock()
            agent._apply_persist_user_message_override = MagicMock()
            agent.context_compressor = None
            agent._turn_preflight_display_snapshot = None
            agent._turn_received_provider_response = False
            agent._turn_failed_file_mutations = {}
            agent._db_flush_scan_prefix = None

            def finish_turn(_prompt):
                finalize_turn(
                    agent,
                    final_response="ok",
                    api_call_count=1,
                    interrupted=False,
                    failed=False,
                    messages=[{"role": "assistant", "content": "ok"}],
                    conversation_history=[],
                    effective_task_id="strict-oneshot",
                    turn_id="strict-oneshot-turn",
                    user_message="hello",
                    original_user_message="hello",
                    _should_review_memory=False,
                    _turn_exit_reason="text_response(1)",
                )
                return {"final_response": "ok", "completed": True}

            agent.run_conversation = MagicMock(side_effect=finish_turn)
            agent.shutdown_memory_provider = MagicMock()
            agent.close = MagicMock()
            created.append(agent)
            return agent

        with (
            patch("hermes_cli.config.load_config", return_value={"model": model_config}),
            patch("hermes_cli.runtime_provider.resolve_runtime_provider", return_value=runtime),
            patch("hermes_cli.tools_config._get_platform_tools", return_value=set()),
            patch("hermes_cli.mcp_startup.ensure_mcp_discovery_before_agent_build"),
            patch("hermes_cli.oneshot._create_session_db_for_oneshot", return_value=None),
            patch("hermes_cli.oneshot.get_fallback_chain", return_value=[]),
            patch("run_agent.AIAgent", side_effect=build_agent) as agent_type,
        ):
            text, result = run_cli_oneshot_agent("hello")

        assert text == "ok"
        assert result["final_response"] == "ok"
        assert agent_type.call_args.kwargs["skip_background_review"] is strict_mode
        assert created[0].skip_background_review is strict_mode
        assert created[0]._spawn_background_review.call_count == expected_review_calls

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

    @pytest.mark.parametrize("cap", [1, 1999, 2001])
    def test_strict_output_budget_rejects_non_exact_first_cap(self, cap):
        agent = MagicMock()
        agent._strict_output_token_budget = 2000
        agent._strict_output_tokens_reserved = 0
        agent.request_overrides = {}

        with pytest.raises(RuntimeError, match="exact configured output cap"):
            _reserve_strict_output_budget(agent, {"max_tokens": cap})

        assert agent._strict_output_tokens_reserved == 0
        assert vars(agent).get("_strict_provider_attempted", False) is False

    @pytest.mark.parametrize("budget", [1, 1999, 2001, "2000", True])
    def test_strict_output_budget_rejects_non_exact_configured_budget(self, budget):
        agent = MagicMock()
        agent._strict_output_token_budget = budget
        agent._strict_output_tokens_reserved = 0
        agent.request_overrides = {}

        with pytest.raises(RuntimeError, match="requires exactly 2000 tokens"):
            _reserve_strict_output_budget(agent, {"max_tokens": 2000})

        assert agent._strict_output_tokens_reserved == 0
        assert vars(agent).get("_strict_provider_attempted", False) is False

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
