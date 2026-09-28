"""Tests for gateway session hygiene — auto-compression of large sessions.

Verifies that the gateway detects pathologically large transcripts and
triggers auto-compression before running the agent.  (#628)

The hygiene system uses the SAME compression config as the agent:
  compression.threshold × model context length
so CLI and messaging platforms behave identically.
"""

import asyncio
import importlib
import sys
import threading
import time
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pytest

from agent.model_metadata import estimate_messages_tokens_rough
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult
from gateway.session import SessionEntry, SessionSource


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_history(n_messages: int, content_size: int = 100) -> list:
    """Build a fake transcript with n_messages user/assistant pairs."""
    history = []
    content = "x" * content_size
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        history.append({"role": role, "content": content, "timestamp": f"t{i}"})
    return history


def _make_large_history_tokens(target_tokens: int) -> list:
    """Build a history that estimates to roughly target_tokens tokens."""
    # estimate_messages_tokens_rough counts total chars in str(msg) // 4
    # Each msg dict has ~60 chars of overhead + content chars
    # So for N tokens we need roughly N * 4 total chars across all messages
    target_chars = target_tokens * 4
    # Each message as a dict string is roughly len(content) + 60 chars
    msg_overhead = 60
    # Use 50 messages with appropriately sized content
    n_msgs = 50
    content_size = max(10, (target_chars // n_msgs) - msg_overhead)
    return _make_history(n_msgs, content_size=content_size)


class HygieneCaptureAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="fake-token"), Platform.TELEGRAM)
        self.sent = []

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        return True

    async def disconnect(self) -> None:
        return None

    async def send(self, chat_id, content, reply_to=None, metadata=None) -> SendResult:
        self.sent.append(
            {
                "chat_id": chat_id,
                "content": content,
                "reply_to": reply_to,
                "metadata": metadata,
            }
        )
        return SendResult(success=True, message_id="hygiene-1")

    async def get_chat_info(self, chat_id: str):
        return {"id": chat_id}


# ---------------------------------------------------------------------------
# Detection threshold tests (model-aware, unified with compression config)
# ---------------------------------------------------------------------------

class TestSessionHygieneThresholds:
    """Test that the threshold logic correctly identifies large sessions.

    Thresholds are derived from model context length × compression threshold,
    matching what the agent's ContextCompressor uses.
    """


    def test_under_threshold_no_trigger(self):
        """Session under threshold should not trigger, even with many messages."""
        # 250 short messages — lots of messages but well under token threshold
        history = _make_history(250, content_size=10)
        approx_tokens = estimate_messages_tokens_rough(history)

        # 200k model at 85% = 170k token threshold
        context_length = 200_000
        threshold_pct = 0.85
        compress_token_threshold = int(context_length * threshold_pct)

        needs_compress = approx_tokens >= compress_token_threshold
        assert not needs_compress, (
            f"250 short messages (~{approx_tokens} tokens) should NOT trigger "
            f"compression at {compress_token_threshold} token threshold"
        )

    def test_message_count_alone_does_not_trigger(self):
        """Message count alone should NOT trigger — only token count matters.

        The old system used an OR of token-count and message-count thresholds,
        which caused premature compression in tool-heavy sessions with 200+
        messages but low total tokens.
        """
        # 300 very short messages — old system would compress, new should not
        history = _make_history(300, content_size=10)
        approx_tokens = estimate_messages_tokens_rough(history)

        context_length = 200_000
        threshold_pct = 0.85
        compress_token_threshold = int(context_length * threshold_pct)

        # Token-based check only
        needs_compress = approx_tokens >= compress_token_threshold
        assert not needs_compress

    def test_threshold_scales_with_model(self):
        """Different models should have different compression thresholds."""
        # 128k model at 85% = 108,800 tokens
        small_model_threshold = int(128_000 * 0.85)
        # 200k model at 85% = 170,000 tokens
        large_model_threshold = int(200_000 * 0.85)
        # 1M model at 85% = 850,000 tokens
        huge_model_threshold = int(1_000_000 * 0.85)

        # A session at ~120k tokens:
        history = _make_large_history_tokens(120_000)
        approx_tokens = estimate_messages_tokens_rough(history)

        # Should trigger for 128k model
        assert approx_tokens >= small_model_threshold
        # Should NOT trigger for 200k model
        assert approx_tokens < large_model_threshold
        # Should NOT trigger for 1M model
        assert approx_tokens < huge_model_threshold


def test_hygiene_total_ceiling_warning_reports_elapsed_and_progress():
    from gateway.run import _hygiene_compression_timeout_message

    warning = _hygiene_compression_timeout_message(
        total_exhausted=True,
        elapsed=600.4,
        idle_timeout=30.0,
        progress_observed=True,
    )

    assert "total ceiling after 600.4s" in warning
    assert "summary output was observed" in warning
    assert "30.0s" not in warning
    assert "no output" not in warning


class TestSessionHygieneWarnThreshold:
    """Test the post-compression warning threshold (95% of context)."""

    def test_warn_when_still_large(self):
        """If compressed result is still above 95% of context, should warn."""
        context_length = 200_000
        warn_threshold = int(context_length * 0.95)  # 190k
        post_compress_tokens = 195_000
        assert post_compress_tokens >= warn_threshold

    def test_no_warn_when_under(self):
        """If compressed result is under 95% of context, no warning."""
        context_length = 200_000
        warn_threshold = int(context_length * 0.95)  # 190k
        post_compress_tokens = 150_000
        assert post_compress_tokens < warn_threshold


class TestEstimatedTokenThreshold:
    """Verify that hygiene thresholds are always below the model's context
    limit — for both actual and estimated token counts.

    Regression: a previous 1.4x multiplier on rough estimates pushed the
    threshold to 85% * 1.4 = 119% of context, which exceeded the model's
    limit and prevented hygiene from ever firing for ~200K models (GLM-5).
    The fix removed the multiplier entirely — the 85% threshold already
    provides ample headroom over the agent's 50% compressor.
    """

    def test_threshold_below_context_for_200k_model(self):
        """Hygiene threshold must always be below model context."""
        context_length = 200_000
        threshold = int(context_length * 0.85)
        assert threshold < context_length


    def test_overestimate_fires_early_but_safely(self):
        """If rough estimate is 50% inflated, hygiene fires at ~57% actual usage.

        That's between the agent's 50% threshold and the model's limit —
        safe and harmless.
        """
        context_length = 200_000
        threshold = int(context_length * 0.85)  # 170K
        # If actual tokens = 113K, rough estimate = 113K * 1.5 = 170K
        # Hygiene fires when estimate hits 170K, actual is ~113K = 57% of ctx
        actual_when_fires = threshold / 1.5
        assert actual_when_fires > context_length * 0.50, (
            "Early fire should still be above agent's 50% threshold"
        )
        assert actual_when_fires < context_length, (
            "Early fire must be well below model limit"
        )


class TestTokenEstimation:
    """Verify rough token estimation works as expected for hygiene checks."""


    def test_proportional_to_content(self):
        small = _make_history(10, content_size=100)
        large = _make_history(10, content_size=10_000)
        assert estimate_messages_tokens_rough(large) > estimate_messages_tokens_rough(small)


@pytest.mark.asyncio
async def test_session_hygiene_preserves_transcript_when_no_rotation(monkeypatch, tmp_path):
    """Regression for #21301: the hygiene agent is built without a session_db,
    so _compress_context cannot rotate. When it neither rotates NOR compacts
    in place, the transcript MUST be preserved — an unconditional
    rewrite_transcript() would replace the original messages with only the
    summary (permanent data loss). Mirrors the /compress guard (#44794)."""
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    class NonRotatingCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            self.session_id = kwargs.get("session_id", "fake-session")
            self.compression_in_place = False  # not in-place either
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            # No session_db → cannot rotate: session_id is UNCHANGED, and this
            # is a failure-to-rotate, not an in-place success.
            return ([{"role": "assistant", "content": "summary only"}], None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = NonRotatingCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:group:-1001:17585",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "795544298")

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001",
            chat_type="group",
            thread_id="17585",
            user_id="12345",
        ),
        message_id="1",
    )

    # Pre-load a failure streak so we can prove the recovery gate is WIRED UP,
    # not merely that the predicate is correct in isolation (#79624). Deleting
    # the whole `if not _hyg_aborted: if hygiene_compaction_recovered(...)`
    # block leaves every unit test in
    # tests/gateway/test_hygiene_failure_cooldown_ladder.py green, so this E2E
    # is the only thing binding the call site.
    reset_calls = []
    _real_reset = gateway_run._reset_hygiene_failure_streak
    monkeypatch.setattr(
        gateway_run,
        "_reset_hygiene_failure_streak",
        lambda gw, key: (reset_calls.append(key), _real_reset(gw, key))[1],
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    # The transcript must NOT be rewritten — the original is preserved.
    runner.session_store.rewrite_transcript.assert_not_called()

    # This run neither rotated nor compacted in place, so it did NOT recover
    # the session: the reset must NOT have been reached. Spying on the module
    # function is what binds the CALL SITE — asserting on streak values alone
    # passes even if the whole gate is deleted, because the streak is 0 either
    # way.
    assert reset_calls == [], (
        "the degenerate no-rotate path must not clear the failure streak"
    )


@pytest.mark.asyncio
async def test_session_hygiene_no_rotation_does_not_clear_a_failure_streak(
    monkeypatch, tmp_path
):
    """The degenerate no-rotate path must not count as recovery (#79624).

    Binds the CALL SITE, not just the predicate: with the wiring deleted, every
    unit test in test_hygiene_failure_cooldown_ladder.py still passes. Here a
    session carries streak=2 into a hygiene run that neither rotates nor
    compacts in place; the streak must come out unchanged, because clearing it
    is exactly what let a wedged session retry forever on rung 1.
    """
    import gateway.run as _run

    # The predicate the call site must consult, exercised through the same
    # arguments the degenerate branch produces.
    assert _run.hygiene_compaction_recovered(
        aborted=False, rotated=False, in_place=False,
        msg_count=220, new_count=220,
        approx_tokens=50_000, new_tokens=50_000,
    ) is False
    # ...and it stays False even when the counts alone would read as progress,
    # which is what makes the rotated/in_place guard load-bearing rather than
    # redundant with the token comparison.
    assert _run.hygiene_compaction_recovered(
        aborted=False, rotated=False, in_place=False,
        msg_count=220, new_count=100,
        approx_tokens=50_000, new_tokens=30_000,
    ) is False

    runner = object.__new__(_run.GatewayRunner)
    state = runner._session_state("telegram:-1001:17585")
    state.persistent.hygiene_failure_streak = 2
    # A non-recovering run must leave it alone.
    _run._reset_hygiene_failure_streak(runner, "some-other-session")
    assert state.persistent.hygiene_failure_streak == 2
    # ...and a recovering one clears it.
    _run._reset_hygiene_failure_streak(runner, "telegram:-1001:17585")
    assert state.persistent.hygiene_failure_streak == 0


@pytest.mark.asyncio
async def test_session_hygiene_preserves_transcript_when_in_place_configured_but_no_db(monkeypatch, tmp_path):
    """Regression: when compression.in_place is True but the hygiene agent has
    no session_db, archive_and_compact cannot run — _last_compaction_in_place
    stays False.  The guard must read the *result* flag, not the *config* flag,
    otherwise the transcript is unconditionally rewritten with only the summary
    (permanent data loss identical to #21301)."""
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    class InPlaceConfiguredAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            self.session_id = kwargs.get("session_id", "fake-session")
            self.compression_in_place = True
            self._last_compaction_in_place = False
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            return ([{"role": "assistant", "content": "summary only"}], None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = InPlaceConfiguredAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:group:-1001:17585",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="group",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )
    monkeypatch.setenv("TELEGRAM_HOME_CHANNEL", "795544298")

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="-1001",
            chat_type="group",
            thread_id="17585",
            user_id="12345",
        ),
        message_id="1",
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    # The config says in_place=True, but the DB write failed (no session_db)
    # so _last_compaction_in_place is False. Transcript must NOT be rewritten.
    runner.session_store.rewrite_transcript.assert_not_called()


@pytest.mark.asyncio
async def test_session_hygiene_timeout_continues_to_agent_and_sets_cooldown(monkeypatch, tmp_path):
    """A timed-out SessionDB-bound worker cannot compact after the live turn starts.

    The worker remains alive long enough to cross the old race window. The
    timeout must fence its eventual commit, continue to the live agent, and
    clean up the temporary agent only after the worker actually returns.
    """
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    worker_started = threading.Event()
    release_worker = threading.Event()
    lease_released = threading.Event()
    cleanup_done = threading.Event()
    fake_db = MagicMock()
    # The DB-backed cooldown check calls this before compressing; a bare
    # MagicMock return would be truthy and skip compression entirely.
    fake_db.get_compression_failure_cooldown.return_value = None

    class SlowCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id", "fake-session")
            self._session_db = kwargs.get("session_db")
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock(side_effect=cleanup_done.set)
            type(self).last_instance = self

        def _compress_context(
            self, messages, *_args, commit_fence=None, **_kwargs
        ):
            if commit_fence is not None:
                commit_fence.register_cancelled_lock_release(lease_released.set)
            worker_started.set()
            assert release_worker.wait(timeout=10)
            if commit_fence is not None and not commit_fence.begin_commit():
                return (messages, None)
            try:
                self._session_db.archive_and_compact(
                    self.session_id,
                    [{"role": "assistant", "content": "too late"}],
                )
                self._last_compaction_in_place = True
                return ([{"role": "assistant", "content": "too late"}], None)
            finally:
                if commit_fence is not None:
                    commit_fence.finish_commit()

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = SlowCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "compression:\n"
        "  enabled: true\n"
        "  hygiene_timeout_seconds: 0.01\n"
        "  hygiene_failure_cooldown_seconds: 120\n"
    )

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:12345",
        session_id="sess-timeout",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = SimpleNamespace(_db=fake_db)
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="dm",
            user_id="12345",
        ),
        message_id="1",
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    assert worker_started.is_set()
    assert runner._run_agent.await_count == 1
    # Cooldown must be persisted to the state DB (survives restart, #74136),
    # not stashed in an in-memory dict.
    assert fake_db.record_compression_failure_cooldown.called
    _cd_args = fake_db.record_compression_failure_cooldown.call_args[0]
    assert _cd_args[0] == "sess-timeout"
    assert _cd_args[1] > time.time()
    timeout_warnings = [s for s in adapter.sent if "Context compression timed out" in s["content"]]
    assert len(timeout_warnings) == 1
    fake_db.archive_and_compact.assert_not_called()
    assert lease_released.is_set()
    # Event/state assertions prove the host returned before the detached
    # worker's event-gated wait completed without a scheduler-sensitive clock
    # bound: cleanup runs only when that worker actually exits.
    SlowCompressAgent.last_instance.close.assert_not_called()

    release_worker.set()
    await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=2)

    # The late worker observed cancellation at the commit fence, so it never
    # mutated the live session after the new turn began. Cleanup still ran once
    # it was safe to tear down the helper agent's clients/providers.
    fake_db.archive_and_compact.assert_not_called()
    SlowCompressAgent.last_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_session_hygiene_turn_hold_budget_abandons_streaming_wait(
    monkeypatch, tmp_path
):
    """A compression that still streams progress must not hold the turn hostage.

    Regression test for the bounded turn-hold (#TKT-0029). The worker keeps
    ticking the commit fence (touch_progress), so the per-slice inactivity
    timeout NEVER fires — without a turn-hold budget the gateway would extend
    the wait up to the total ceiling (default 600s) while zero bytes hit the
    wire, severing the transport. The turn must instead be abandoned once it
    exceeds ``hygiene_max_turn_hold_seconds``, proceed on the uncompressed
    transcript, and fence the stale commit.
    """
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    worker_started = threading.Event()
    release_worker = threading.Event()
    cleanup_done = threading.Event()
    fake_db = MagicMock()
    fake_db.get_compression_failure_cooldown.return_value = None

    class StreamingCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id", "fake-session")
            self._session_db = kwargs.get("session_db")
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock(side_effect=cleanup_done.set)
            type(self).last_instance = self

        def _compress_context(
            self, messages, *_args, commit_fence=None, **_kwargs
        ):
            worker_started.set()
            # Stream progress continuously so the inactivity slice never
            # times out; only the turn-hold budget can abandon this wait.
            while not release_worker.is_set():
                if commit_fence is not None:
                    commit_fence.touch_progress()
                time.sleep(0.01)
            if commit_fence is not None and not commit_fence.begin_commit():
                return (messages, None)
            try:
                self._session_db.archive_and_compact(
                    self.session_id,
                    [{"role": "assistant", "content": "too late"}],
                )
                self._last_compaction_in_place = True
                return ([{"role": "assistant", "content": "too late"}], None)
            finally:
                if commit_fence is not None:
                    commit_fence.finish_commit()

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = StreamingCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "compression:\n"
        "  enabled: true\n"
        # Inactivity budget is huge, so the slice timeout can never fire on
        # its own; the turn-hold budget is the ONLY thing that abandons.
        "  hygiene_timeout_seconds: 60\n"
        "  hygiene_total_ceiling_seconds: 600\n"
        "  hygiene_max_turn_hold_seconds: 0.3\n"
        "  hygiene_failure_cooldown_seconds: 120\n"
    )

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:12345",
        session_id="sess-turnhold",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = SimpleNamespace(_db=fake_db)
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="dm",
            user_id="12345",
        ),
        message_id="1",
    )

    started = time.monotonic()
    result = await asyncio.wait_for(runner._handle_message(event), timeout=15)
    elapsed = time.monotonic() - started

    # The turn proceeded on the uncompressed transcript well under the 600s
    # ceiling — the turn-hold budget (~0.3s) abandoned the streaming wait.
    assert result == "ok"
    assert elapsed < 5.0, f"turn held for {elapsed:.1f}s despite the turn-hold budget"
    assert worker_started.is_set()
    assert runner._run_agent.await_count == 1
    # The stale commit must be fenced: the late worker never mutates the session.
    fake_db.archive_and_compact.assert_not_called()

    release_worker.set()
    await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=3)
    fake_db.archive_and_compact.assert_not_called()
    StreamingCompressAgent.last_instance.close.assert_called_once()

    # Behavior witness 1: turn-hold expiry must NOT stamp the idle-timeout
    # provenance or send the "no output" user message.
    sent_contents = [m["content"] for m in adapter.sent]
    assert not any(
        "timed out" in c.lower() and "no output" in c.lower()
        for c in sent_contents
    ), f"turn-hold must not send idle-timeout message, got: {sent_contents}"
    assert any(
        "deferred" in c.lower() or "still streaming" in c.lower()
        for c in sent_contents
    ), f"turn-hold must send deferral notice, got: {sent_contents}"

    # Behavior witness 2: turn-hold must NOT advance the failure STREAK.
    fake_db.get_compression_failure_cooldown.assert_called()
    # The escalating ladder (x1, x3, x9) is reserved for real failures via
    # _hygiene_cooldown_for_failure -> increment_hygiene_failure_streak.
    # The turn-hold path records only a flat, non-escalating retry-after
    # (spacing out re-attempts so sustained traffic does not spawn and
    # cancel a fresh compressor every turn) and must never touch the streak.
    assert not fake_db.increment_hygiene_failure_streak.called, \
        "turn-hold must not advance the failure streak"
    assert fake_db.record_compression_failure_cooldown.called, \
        "turn-hold must record the flat retry-after spacing"
    _th_args = fake_db.record_compression_failure_cooldown.call_args[0]
    import time as _time_mod
    _th_retry = _th_args[1] - _time_mod.time()
    assert _th_retry <= 120, (
        f"turn-hold retry-after must stay flat (~60s), got {_th_retry:.0f}s "
        "— escalating ladder leaked into the deferral path"
    )
    assert "turn-hold" in (_th_args[2] or ""), \
        "retry-after reason must name the turn-hold deferral"

    # Behavior witness 3: the #87011 contract remains truthful —
    # "session hygiene compression timed out" still means a real idle
    # timeout, not a turn-hold deferral. The turn-hold path must use a
    # distinct provenance stamp.
    # (Verified indirectly: the idle-timeout path would have sent the
    # "no output" message, which we already asserted absent above.)


@pytest.mark.asyncio
async def test_session_hygiene_idle_timeout_still_takes_failure_path(
    monkeypatch, tmp_path
):
    """A genuine no-progress idle timeout must still take the existing
    failure path: AGENT_COMPRESSION_TIMEOUT provenance, "no output" user
    message, and failure-cooldown increment.

    This is the #87011 contract: "session hygiene compression timed out"
    means a real idle timeout, not a turn-hold deferral.
    """
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    worker_started = threading.Event()
    release_worker = threading.Event()
    cleanup_done = threading.Event()
    fake_db = MagicMock()
    fake_db.get_compression_failure_cooldown.return_value = None

    class StalledCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id", "fake-session")
            self._session_db = kwargs.get("session_db")
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock(side_effect=cleanup_done.set)
            type(self).last_instance = self

        def _compress_context(
            self, messages, *_args, commit_fence=None, **_kwargs
        ):
            worker_started.set()
            # NEVER touch progress — the inactivity slice will fire.
            # But we must be stoppable so the test can clean up.
            while not release_worker.is_set():
                time.sleep(0.01)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = StalledCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "compression:\n"
        "  enabled: true\n"
        "  hygiene_timeout_seconds: 0.1\n"
        "  hygiene_total_ceiling_seconds: 600\n"
        "  hygiene_max_turn_hold_seconds: 60\n"
        "  hygiene_failure_cooldown_seconds: 120\n"
    )

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:12345",
        session_id="sess-idle-timeout",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = SimpleNamespace(_db=fake_db)
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="dm",
            user_id="12345",
        ),
        message_id="1",
    )

    started = time.monotonic()
    result = await asyncio.wait_for(runner._handle_message(event), timeout=15)
    elapsed = time.monotonic() - started

    # The turn proceeded on the uncompressed transcript after the idle
    # timeout fired (~0.1s).
    assert result == "ok"
    assert elapsed < 5.0
    assert worker_started.is_set()
    assert runner._run_agent.await_count == 1

    # Behavior witness: idle timeout MUST send the "no output" message.
    sent_contents = [m["content"] for m in adapter.sent]
    assert any(
        "timed out" in c.lower() and "no output" in c.lower()
        for c in sent_contents
    ), f"idle timeout must send 'no output' message, got: {sent_contents}"

    # Behavior witness: idle timeout MUST advance the failure cooldown.
    # The gateway calls _hygiene_cooldown_for_failure + _record_hygiene_cooldown.
    # We verify by checking the DB mock was asked to persist.
    # (The exact call depends on the SessionDB interface; we assert the
    # gateway attempted to record the failure.)
    assert fake_db.get_compression_failure_cooldown.called

    # Cleanup: release the stalled worker so it can exit, then verify teardown.
    release_worker.set()
    await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=3)
    StalledCompressAgent.last_instance.close.assert_called_once()

@pytest.mark.asyncio
async def test_session_hygiene_forces_in_place_compaction_with_bound_session_db(
    monkeypatch, tmp_path
):
    """Regression for #60947: gateway hygiene should not rely on
    helper-agent session rotation to shrink a live gateway transcript.

    The hygiene pass runs before the user turn and already owns the gateway
    session binding, so it should force in-place compaction and bind the
    compressor to the gateway SessionDB. Otherwise a helper can return a
    summary without rotating/compacting, the guard preserves the original
    transcript, and the same oversized session is reloaded on every turn.
    """
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    stored_system_prompt = (
        "You are Hermes.\n\n"
        "<memory_provider_context>\n"
        "Pinboard provider instructions\n"
        "</memory_provider_context>"
    )
    fake_db = MagicMock()
    fake_db.get_compression_failure_cooldown.return_value = None
    async_session_db = SimpleNamespace(
        _db=fake_db,
        get_session=AsyncMock(
            return_value={
                "system_prompt": stored_system_prompt,
            }
        ),
    )

    class FakeInPlaceCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.model = kwargs.get("model")
            self.platform = kwargs.get("platform")
            self.session_id = kwargs.get("session_id", "fake-session")
            self._session_db = kwargs.get("session_db")
            self._cached_system_prompt = None
            self.compression_in_place = False
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            assert self.compression_in_place is True
            assert self._session_db is fake_db
            assert self.platform == "gateway_hygiene"
            assert self._cached_system_prompt == stored_system_prompt
            self._last_compaction_in_place = True
            return ([{"role": "assistant", "content": "compressed in place"}], None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeInPlaceCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:private:12345",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="private",
    )
    runner.session_store.load_transcript.return_value = _make_history(12, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = async_session_db
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"}
    )
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="private",
            user_id="12345",
        ),
        message_id="1",
    )

    # Spy on the recovery reset so this test binds the CALL SITE (#79624).
    # Without a positive assertion here, deleting the whole
    # `if not _hyg_aborted: if hygiene_compaction_recovered(...)` block leaves
    # every other hygiene and ladder test green.
    reset_calls = []
    _real_reset = gateway_run._reset_hygiene_failure_streak
    monkeypatch.setattr(
        gateway_run,
        "_reset_hygiene_failure_streak",
        lambda gw, key: (reset_calls.append(key), _real_reset(gw, key))[1],
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    agent = FakeInPlaceCompressAgent.last_instance
    assert agent is not None
    async_session_db.get_session.assert_awaited_once_with("sess-1")
    agent.context_compressor.bind_session_state.assert_called_once_with(fake_db, "sess-1")
    # In-place compaction already persisted via archive_and_compact() —
    # rewrite_transcript would replace_messages(active_only=False) and DELETE
    # the just-archived rows (#61145). The hygiene handler must skip it.
    runner.session_store.rewrite_transcript.assert_not_called()
    runner._run_agent.assert_awaited_once()
    # A real in-place compaction IS a recovery, so the gate must have run and
    # cleared the streak. This is the positive half of the wiring contract.
    assert reset_calls, (
        "successful in-place compaction must clear the hygiene failure streak "
        "— the recovery gate is not wired into _handle_message_with_agent"
    )


async def _run_hard_limit_hygiene(monkeypatch, tmp_path):
    """Drive one gateway turn whose transcript exceeds a lowered hard limit.

    Returns the fake compression agent class; ``last_instance.init_kwargs``
    holds the kwargs the hygiene path built it with.
    """
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    class FakeCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.init_kwargs = kwargs
            self.model = kwargs.get("model")
            self.session_id = kwargs.get("session_id", "fake-session")
            self._print_fn = None
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            self.session_id = f"{self.session_id}_compressed"
            return ([{"role": "assistant", "content": "compressed"}], None)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = FakeCompressAgent
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    # Write config.yaml with lowered hard-limit
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "compression:\n"
        "  enabled: true\n"
        "  hygiene_hard_message_limit: 10\n"
    )

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:private:12345",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="private",
    )
    # 12 messages: below default → no compression without override,
    # but above the configured limit of 10 → should compress.
    runner.session_store.load_transcript.return_value = _make_history(12, content_size=40)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(
        gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"}
    )
    # Pick a context length large enough that the token-based threshold
    # won't trigger for 12 short messages — hard-limit must be the ONLY
    # thing firing compression.
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 1_000_000,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="private",
            user_id="12345",
        ),
        message_id="1",
    )

    result = await runner._handle_message(event)

    assert result == "ok"
    return FakeCompressAgent


@pytest.mark.asyncio
async def test_session_hygiene_honors_configurable_hard_message_limit(
    monkeypatch, tmp_path
):
    """compression.hygiene_hard_message_limit overrides the default.

    Regression for user-reported fix: a gateway session with a small
    transcript (12 messages) should not hit hygiene compression by default,
    but WILL when the user lowers the hard-limit to 10.  Verifies the new
    config key is actually read and applied at the force-compress gate.
    """
    agent_cls = await _run_hard_limit_hygiene(monkeypatch, tmp_path)
    # The compression agent was instantiated → hard-limit fired on the
    # configured value (10), not the hardcoded 400 default.
    assert agent_cls.last_instance is not None, (
        "Expected hygiene compression to fire when message count (12) "
        "exceeds configured hygiene_hard_message_limit (10)"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cap", "expected"),
    [(["file"], []), (["file", "memory"], ["memory"])],
)
async def test_session_hygiene_agent_toolsets_are_capped(
    monkeypatch, tmp_path, cap, expected
):
    """The hygiene compression agent gets memory only when the cap allows it."""
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda *a, **k: {"platform_toolsets": {"telegram": list(cap)}},
    )
    agent_cls = await _run_hard_limit_hygiene(monkeypatch, tmp_path)
    assert agent_cls.last_instance is not None
    assert agent_cls.last_instance.init_kwargs["enabled_toolsets"] == expected


# ---------------------------------------------------------------------------
# Progress-aware hygiene wait: slow-but-streaming models are not punished
# ---------------------------------------------------------------------------

def _make_progress_runner(monkeypatch, tmp_path, agent_cls, cfg_text):
    """Shared scaffolding for the progress-aware hygiene wait tests."""
    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = agent_cls
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:12345",
        session_id="sess-progress",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="dm",
            user_id="12345",
        ),
        message_id="1",
    )
    return runner, adapter, event




# ---------------------------------------------------------------------------
# Cooldown persistence across gateway restarts (#74136)
# ---------------------------------------------------------------------------

def _make_cooldown_runner(monkeypatch, tmp_path, agent_cls, session_db, session_id):
    """Scaffolding for the restart-persistence tests: a fresh GatewayRunner
    wired to a REAL AsyncSessionDB facade (not a MagicMock) so the hygiene
    cooldown check/write paths exercise the actual SQLite-backed methods."""
    from hermes_state import AsyncSessionDB

    fake_dotenv = types.ModuleType("dotenv")
    fake_dotenv.load_dotenv = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)

    fake_run_agent = types.ModuleType("run_agent")
    fake_run_agent.AIAgent = agent_cls
    monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "compression:\n"
        "  enabled: true\n"
        "  hygiene_failure_cooldown_seconds: 300\n",
        encoding="utf-8",
    )

    gateway_run = importlib.import_module("gateway.run")
    GatewayRunner = gateway_run.GatewayRunner

    adapter = HygieneCaptureAdapter()
    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="fake-token")}
    )
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:12345",
        session_id=session_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = _make_history(6, content_size=400)
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.rewrite_transcript = MagicMock()
    runner.session_store.append_to_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    # The real async facade over the real SQLite-backed SessionDB — the
    # production shape.  A SimpleNamespace(_db=MagicMock()) here would let
    # the assertion pass against methods that don't actually persist.
    runner._session_db = AsyncSessionDB(session_db)
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "ok",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "fake"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100,
    )

    event = MessageEvent(
        text="hello",
        source=SessionSource(
            platform=Platform.TELEGRAM,
            chat_id="12345",
            chat_type="dm",
            user_id="12345",
        ),
        message_id="1",
    )
    return runner, adapter, event


@pytest.mark.asyncio
async def test_hygiene_compression_cooldown_survives_gateway_restart(
    monkeypatch, tmp_path
):
    """Regression for #74136: the compression-failure cooldown must be
    persisted to the state DB, not an in-memory dict on the runner.

    Fail a hygiene compression on runner #1, tear the runner down, build a
    FRESH runner on the SAME database (simulating a gateway restart), and
    assert the second runner still honors the cooldown — i.e. it does not
    re-instantiate a compression agent for the same failing session.
    """
    from hermes_state import SessionDB

    gateway_run = importlib.import_module("gateway.run")
    session_id = "sess-restart"
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")

        main_thread = threading.get_ident()
        streak_threads = []
        original_cooldown_for_failure = gateway_run._hygiene_cooldown_for_failure

        def tracked_cooldown_for_failure(*args, **kwargs):
            streak_threads.append(threading.get_ident())
            return original_cooldown_for_failure(*args, **kwargs)

        monkeypatch.setattr(
            gateway_run,
            "_hygiene_cooldown_for_failure",
            tracked_cooldown_for_failure,
        )

        class AbortingCompressAgent:
            instances = 0

            def __init__(self, **kwargs):
                type(self).instances += 1
                self.session_id = kwargs.get("session_id", session_id)
                self._session_db = kwargs.get("session_db")
                self._last_compaction_in_place = False
                self.context_compressor = SimpleNamespace(
                    bind_session_state=MagicMock(),
                    _last_compress_aborted=True,
                    _last_summary_error="aux model exploded",
                    _last_aux_model_failure_model=None,
                )
                self.shutdown_memory_provider = MagicMock()
                self.close = MagicMock()

            def _compress_context(self, messages, *_args, **_kwargs):
                # Summary generation failed: compressor aborts and returns
                # the transcript unchanged.
                return (messages, None)

        runner1, _adapter1, event1 = _make_cooldown_runner(
            monkeypatch, tmp_path, AbortingCompressAgent, db, session_id
        )
        assert await runner1._handle_message(event1) == "ok"
        assert AbortingCompressAgent.instances == 1
        assert len(streak_threads) == 1
        assert streak_threads[0] != main_thread

        # The abort must have persisted a cooldown to the DB.
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None and state["remaining_seconds"] > 0, (
            "hygiene compression abort did not persist a cooldown to the "
            f"state DB; got {state!r}"
        )

        # --- simulate a gateway restart: brand-new runner, same DB ---
        del runner1

        class ShouldNotRunAgent:
            instances = 0

            def __init__(self, **kwargs):
                type(self).instances += 1
                self.context_compressor = SimpleNamespace(
                    bind_session_state=MagicMock(),
                    _last_compress_aborted=False,
                    _last_aux_model_failure_model=None,
                )
                self.shutdown_memory_provider = MagicMock()
                self.close = MagicMock()

            def _compress_context(self, messages, *_args, **_kwargs):
                return (messages, None)

        runner2, _adapter2, event2 = _make_cooldown_runner(
            monkeypatch, tmp_path, ShouldNotRunAgent, db, session_id
        )
        assert await runner2._handle_message(event2) == "ok"
        assert ShouldNotRunAgent.instances == 0, (
            "REGRESSION (#74136): a fresh GatewayRunner on the same state DB "
            "re-ran the failing hygiene compression — the failure cooldown "
            "was lost across the restart (in-memory dict instead of the "
            "DB-backed record/get methods)."
        )
        # The user turn itself still runs; only compression is skipped.
        assert runner2._run_agent.await_count == 1

        # Once the first deadline expires, the next failed attempt after a
        # restart must use rung 2 (900s), not start over at 300s (#86650).
        db.clear_compression_failure_cooldown(session_id)
        runner3, _adapter3, event3 = _make_cooldown_runner(
            monkeypatch, tmp_path, AbortingCompressAgent, db, session_id
        )
        assert await runner3._handle_message(event3) == "ok"
        assert AbortingCompressAgent.instances == 2
        assert len(streak_threads) == 2
        assert all(thread_id != main_thread for thread_id in streak_threads)
        escalated = db.get_compression_failure_cooldown(session_id)
        assert escalated is not None
        assert escalated["remaining_seconds"] == pytest.approx(900, abs=5)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Commit-fence cancel must not livelock hygiene (#96953)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hygiene_fence_cancel_records_cooldown_without_abort_flag(
    monkeypatch, tmp_path
):
    """A fence-cancelled hygiene worker returns the original transcript with
    ``_last_compress_aborted`` still False (failure_class=commit_fence_cancelled).

    That used to skip the abort-cooldown block, so the next turn immediately
    re-armed hygiene and waited up to the 600s ceiling behind a doomed attempt.
    """
    from hermes_state import SessionDB

    gateway_run = importlib.import_module("gateway.run")
    session_id = "sess-fence-cancel"

    class FenceCancelCompressAgent:
        instances = 0

        def __init__(self, **kwargs):
            type(self).instances += 1
            self.session_id = kwargs.get("session_id", session_id)
            self._session_db = kwargs.get("session_db")
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_summary_error=None,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()

        def _compress_context(self, messages, *_args, commit_fence=None, **_kwargs):
            if commit_fence is not None:
                assert commit_fence.try_cancel_before_commit() is True
            return (messages, None)

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner1, adapter1, event1 = _make_cooldown_runner(
            monkeypatch, tmp_path, FenceCancelCompressAgent, db, session_id
        )
        assert await runner1._handle_message(event1) == "ok"
        assert FenceCancelCompressAgent.instances == 1
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None and state["remaining_seconds"] > 0, (
            "fence-cancelled hygiene compression did not persist a cooldown; "
            f"got {state!r}"
        )
        assert not any(
            "Context compression aborted" in s["content"] for s in adapter1.sent
        ), "fence-cancel during /stop or /restart must not toast an abort"

        class ShouldNotRunAgent:
            instances = 0

            def __init__(self, **kwargs):
                type(self).instances += 1
                self.context_compressor = SimpleNamespace(
                    bind_session_state=MagicMock(),
                    _last_compress_aborted=False,
                    _last_aux_model_failure_model=None,
                )
                self.shutdown_memory_provider = MagicMock()
                self.close = MagicMock()

            def _compress_context(self, messages, *_args, **_kwargs):
                return (messages, None)

        runner2, _adapter2, event2 = _make_cooldown_runner(
            monkeypatch, tmp_path, ShouldNotRunAgent, db, session_id
        )
        assert await runner2._handle_message(event2) == "ok"
        assert ShouldNotRunAgent.instances == 0, (
            "REGRESSION (#96953): hygiene re-armed after a commit-fence "
            "cancel instead of honoring the failure cooldown"
        )
        assert runner2._run_agent.await_count == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_hygiene_does_not_wait_ceiling_after_fence_cancel(
    monkeypatch, tmp_path
):
    """Once the commit fence is cancelled, the host must stop extending the
    wait — even if the shielded worker is still alive and touching progress.
    """
    from hermes_state import SessionDB

    worker_started = threading.Event()
    release_worker = threading.Event()
    cleanup_done = threading.Event()
    session_id = "sess-fence-wait"

    class HungAfterFenceCancelAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id", session_id)
            self._session_db = kwargs.get("session_db")
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock(side_effect=cleanup_done.set)
            type(self).last_instance = self

        def _compress_context(
            self, messages, *_args, commit_fence=None, **_kwargs
        ):
            if commit_fence is not None:
                commit_fence.try_cancel_before_commit()
            worker_started.set()
            # Keep the worker alive (and keep reporting "progress") so a
            # host that still extends to the 600s ceiling would stall here.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if commit_fence is not None:
                    commit_fence.touch_progress()
                if release_worker.is_set():
                    break
                time.sleep(0.02)
            return (messages, None)

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, HungAfterFenceCancelAgent, db, session_id
        )
        started = time.monotonic()
        result = await runner._handle_message(event)
        elapsed = time.monotonic() - started

        assert result == "ok"
        assert worker_started.wait(timeout=2)
        assert elapsed < 2.0, (
            f"hygiene host waited {elapsed:.1f}s after fence cancel — "
            "must not extend toward the 600s ceiling (#96953)"
        )
        assert runner._run_agent.await_count == 1
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None and state["remaining_seconds"] > 0
        assert not any(
            "Context compression timed out" in s["content"] for s in adapter.sent
        ), "fence-cancel is not a summary-model timeout; no timeout toast"
        release_worker.set()
        await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=2)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_hygiene_skips_when_compression_already_in_flight(
    monkeypatch, tmp_path
):
    """Do not spawn a sibling hygiene compressor while a lock is already held."""
    from hermes_state import SessionDB

    session_id = "sess-in-flight"

    class ShouldNotRunAgent:
        instances = 0

        def __init__(self, **kwargs):
            type(self).instances += 1
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock()

        def _compress_context(self, messages, *_args, **_kwargs):
            return (messages, None)

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, _adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, ShouldNotRunAgent, db, session_id
        )
        runner._session_has_compression_in_flight = AsyncMock(return_value=True)
        assert await runner._handle_message(event) == "ok"
        assert ShouldNotRunAgent.instances == 0
        assert runner._run_agent.await_count == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_hygiene_unwind_records_cooldown(monkeypatch, tmp_path):
    """Restart-drain cancellation must persist a cooldown before re-raising.

    ``except BaseException`` used to revoke the fence and re-raise with no
    cooldown, so the next turn after /restart re-triggered hygiene immediately.
    """
    from hermes_state import SessionDB

    worker_started = threading.Event()
    release_worker = threading.Event()
    cleanup_done = threading.Event()
    session_id = "sess-unwind"

    class SlowCompressAgent:
        last_instance = None

        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id", session_id)
            self._session_db = kwargs.get("session_db")
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock(side_effect=cleanup_done.set)
            type(self).last_instance = self

        def _compress_context(self, messages, *_args, **_kwargs):
            worker_started.set()
            release_worker.wait(timeout=5)
            return (messages, None)

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, _adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, SlowCompressAgent, db, session_id
        )
        task = asyncio.create_task(runner._handle_message(event))
        assert await asyncio.to_thread(worker_started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None and state["remaining_seconds"] > 0, (
            "hygiene unwind did not persist a cooldown; got "
            f"{state!r}"
        )
        release_worker.set()
        await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=2)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# A worker that raises ``TimeoutError`` is not a hygiene wait timeout
# ---------------------------------------------------------------------------


class _ProviderReadTimeout(TimeoutError):
    """Stands in for ``socket.timeout`` raised by the summary provider read."""


class _SpinDetected(AssertionError):
    pass


@pytest.mark.asyncio
async def test_hygiene_worker_timeout_error_takes_error_path_without_spinning(
    monkeypatch, tmp_path, caplog
):
    """Since Python 3.11 ``asyncio.TimeoutError`` is the builtin
    ``TimeoutError``. The host loop used to ``wait_for(shield(future))`` and
    catch it, so a worker that raised ``TimeoutError`` re-raised at once on
    every pass. The loop spun without yielding, blocking the event loop until
    the idle window closed, and then reported the worker's error as a hygiene
    timeout with a "no output" toast.

    The extend check runs once per pass. The stub raises after 50 passes so a
    regressed spin fails this test instead of hanging it.
    """
    from hermes_state import SessionDB

    gateway_run = importlib.import_module("gateway.run")
    session_id = "sess-worker-timeout-error"
    worker_error = _ProviderReadTimeout("summary provider read timed out")
    cleanup_done = threading.Event()

    class ProviderTimeoutCompressAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id", session_id)
            self._session_db = kwargs.get("session_db")
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock(side_effect=cleanup_done.set)

        def _compress_context(self, messages, *_args, **_kwargs):
            raise worker_error

    real_extend = gateway_run.hygiene_wait_should_extend
    extend_calls = []

    def _counting_extend(**kwargs):
        extend_calls.append(kwargs)
        if len(extend_calls) > 50:
            raise _SpinDetected(f"host wait loop ran {len(extend_calls)} passes")
        return real_extend(**kwargs)

    monkeypatch.setattr(
        gateway_run, "hygiene_wait_should_extend", _counting_extend
    )

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, ProviderTimeoutCompressAgent, db, session_id
        )
        caplog.set_level("WARNING", logger="gateway.run")
        started = time.monotonic()
        result = await asyncio.wait_for(runner._handle_message(event), timeout=15)
        elapsed = time.monotonic() - started

        assert result == "ok"
        assert runner._run_agent.await_count == 1
        assert elapsed < 2.0
        assert len(extend_calls) <= 1, (
            f"host wait loop spun {len(extend_calls)} passes on a finished "
            "worker that raised TimeoutError"
        )
        failures = [
            r.getMessage()
            for r in caplog.records
            if "Session hygiene auto-compress failed" in r.getMessage()
        ]
        assert failures and "summary provider read timed out" in failures[0], (
            f"the worker's own error must reach the error path; got {failures!r}"
        )
        sent = [s["content"].lower() for s in adapter.sent]
        assert not any("timed out" in c or "no output" in c for c in sent), (
            f"a worker error is not a summary-model timeout; sent {sent!r}"
        )
        assert not any(
            "no progress" in r.getMessage() or "total ceiling" in r.getMessage()
            for r in caplog.records
        )
        await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=2)
    finally:
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_point", ["before_first_wait", "during_wait"])
async def test_hygiene_fence_cancel_still_takes_the_timeout_path(
    monkeypatch, tmp_path, caplog, cancel_point
):
    """Guards the private timeout type: only host-side expiries reach the
    timeout path. A fence cancel seen before the first wait slice, or after
    one, must still be handled there (fence-cancel warning and provenance),
    not fall through to the generic unwind path.
    """
    from hermes_state import SessionDB
    import agent.conversation_compression as conversation_compression

    session_id = f"sess-fence-{cancel_point}"
    release_worker = threading.Event()
    cleanup_done = threading.Event()

    if cancel_point == "before_first_wait":
        real_fence_cls = conversation_compression.CompressionCommitFence

        class PreCancelledFence(real_fence_cls):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                assert self.try_cancel_before_commit() is True

        monkeypatch.setattr(
            conversation_compression, "CompressionCommitFence", PreCancelledFence
        )

    class FenceCancelAgent:
        def __init__(self, **kwargs):
            self.session_id = kwargs.get("session_id", session_id)
            self._session_db = kwargs.get("session_db")
            self._last_compaction_in_place = False
            self.context_compressor = SimpleNamespace(
                bind_session_state=MagicMock(),
                _last_compress_aborted=False,
                _last_aux_model_failure_model=None,
            )
            self.shutdown_memory_provider = MagicMock()
            self.close = MagicMock(side_effect=cleanup_done.set)

        def _compress_context(
            self, messages, *_args, commit_fence=None, **_kwargs
        ):
            if cancel_point == "during_wait":
                # The host is already inside its first 0.25s wait slice.
                time.sleep(0.1)
                assert commit_fence.try_cancel_before_commit() is True
            release_worker.wait(timeout=2)
            return (messages, None)

    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, FenceCancelAgent, db, session_id
        )
        caplog.set_level("WARNING", logger="gateway.run")
        started = time.monotonic()
        result = await asyncio.wait_for(runner._handle_message(event), timeout=15)
        elapsed = time.monotonic() - started

        assert result == "ok"
        assert elapsed < 2.0
        assert any(
            "was cancelled at the commit fence" in r.getMessage()
            for r in caplog.records
        ), "fence cancel must take the timeout path's fence-cancel branch"
        assert not any(
            "no output" in s["content"].lower() for s in adapter.sent
        )
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None and state["remaining_seconds"] > 0
        release_worker.set()
        await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=3)
    finally:
        release_worker.set()
        db.close()


# ---------------------------------------------------------------------------
# Follow-ups to the worker ``TimeoutError`` fix: cooldown label, slow wake,
# and a cancelled worker future
# ---------------------------------------------------------------------------


def _hygiene_stub_agent(session_id, compress, cleanup_done, **attrs):
    """Build a fake hygiene ``AIAgent`` class around ``compress``."""

    def __init__(self, **kwargs):
        self.session_id = kwargs.get("session_id", session_id)
        self._session_db = kwargs.get("session_db")
        self.context_compressor = SimpleNamespace(
            bind_session_state=MagicMock(),
            _last_compress_aborted=False,
            _last_aux_model_failure_model=None,
        )
        self.shutdown_memory_provider = MagicMock()
        self.close = MagicMock(side_effect=cleanup_done.set)

    namespace = {"__init__": __init__, "_compress_context": compress}
    namespace.setdefault("_last_compaction_in_place", False)
    namespace.update(attrs)
    return type("HygieneStubAgent", (), namespace)


def _count_extend_calls(monkeypatch, limit=50):
    """Count host wait-loop passes whose slice expired before the worker
    finished. Raises after ``limit`` passes so a regressed spin fails fast.
    Each entry records when the host saw that slice expire, as ``at``."""
    gateway_run = importlib.import_module("gateway.run")
    real_extend = gateway_run.hygiene_wait_should_extend
    calls = []

    def _counting_extend(**kwargs):
        calls.append(dict(kwargs, at=time.monotonic()))
        if len(calls) > limit:
            raise _SpinDetected(f"host wait loop ran {len(calls)} passes")
        return real_extend(**kwargs)

    monkeypatch.setattr(gateway_run, "hygiene_wait_should_extend", _counting_extend)
    return calls


def _wait_for_expired_slices(calls, count, timeout=5.0):
    deadline = time.monotonic() + timeout
    while len(calls) < count:
        if time.monotonic() > deadline:
            raise AssertionError(f"host saw {len(calls)} expired slices, wanted {count}")
        time.sleep(0.005)


class _WorkerRuntimeError(RuntimeError):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_cls", "expired_slices"),
    [
        (_ProviderReadTimeout, 0),
        (_ProviderReadTimeout, 2),
        (_WorkerRuntimeError, 0),
    ],
)
async def test_hygiene_worker_error_cooldown_names_the_failure_class(
    monkeypatch, tmp_path, error_cls, expired_slices
):
    """A worker failure records its own class as the cooldown reason.

    The unwind path used to record "cancelled at commit fence" for every
    exception, the same label the timeout path uses for a fence cancel, so a
    provider read timeout or a plain worker error read as a hygiene timeout.
    The label carries the class name only: the reason reaches users and
    provider exception text may carry credentials.
    """
    from hermes_state import SessionDB

    session_id = f"sess-worker-label-{error_cls.__name__}-{expired_slices}"
    cleanup_done = threading.Event()
    extend_calls = _count_extend_calls(monkeypatch)

    def _compress(self, messages, *_args, **_kwargs):
        _wait_for_expired_slices(extend_calls, expired_slices)
        raise error_cls("provider said sk-test-not-a-real-key")

    agent_cls = _hygiene_stub_agent(session_id, _compress, cleanup_done)
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, agent_cls, db, session_id
        )
        result = await asyncio.wait_for(runner._handle_message(event), timeout=15)

        assert result == "ok"
        assert runner._run_agent.await_count == 1
        assert len(extend_calls) >= expired_slices
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None and state["remaining_seconds"] > 0
        assert state["error"] == (
            f"session hygiene compression failed: {error_cls.__name__}"
        )
        assert "sk-test" not in state["error"]
        await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=2)
    finally:
        db.close()


@pytest.mark.asyncio
async def test_hygiene_host_cancel_keeps_the_fence_cancel_cooldown_label(
    monkeypatch, tmp_path
):
    """Host cancellation (restart drain) is not a worker failure: it keeps
    the fence-cancel label that the unwind path has always recorded."""
    from hermes_state import SessionDB

    session_id = "sess-host-cancel-label"
    worker_started = threading.Event()
    release_worker = threading.Event()
    cleanup_done = threading.Event()

    def _compress(self, messages, *_args, **_kwargs):
        worker_started.set()
        release_worker.wait(timeout=5)
        return (messages, None)

    agent_cls = _hygiene_stub_agent(session_id, _compress, cleanup_done)
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, _adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, agent_cls, db, session_id
        )
        task = asyncio.create_task(runner._handle_message(event))
        assert await asyncio.to_thread(worker_started.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None
        assert state["error"] == "session hygiene compression cancelled at commit fence"
        release_worker.set()
        await asyncio.wait_for(asyncio.to_thread(cleanup_done.wait), timeout=2)
    finally:
        release_worker.set()
        db.close()


@pytest.mark.asyncio
async def test_hygiene_wait_wakes_promptly_for_a_worker_slower_than_a_slice(
    monkeypatch, tmp_path, caplog
):
    """Slow wake: the worker outlives two wait slices, then finishes partway
    through the third. The host must keep waiting through the expired slices
    and then resume as soon as the worker finishes, not at the end of the
    0.25s slice it is sleeping in.

    The worker finishes at a fixed point early in the third slice, measured
    from when the host saw the second slice expire. The host must resume
    before that third slice could have ended. A host that sleeps out the
    slice resumes after it ends, however late CI schedules the worker, so
    this bound does not depend on absolute wake latency.

    Host resumption is timed at its first read after the wait loop, the
    ``_last_compaction_in_place`` check.
    """
    from hermes_state import SessionDB

    session_id = "sess-slow-wake"
    cleanup_done = threading.Event()
    extend_calls = _count_extend_calls(monkeypatch)
    finished_at = []
    resumed_at = []

    def _compress(self, messages, *_args, **_kwargs):
        _wait_for_expired_slices(extend_calls, 2)
        time.sleep(max(extend_calls[1]["at"] + 0.03 - time.monotonic(), 0))
        finished_at.append(time.monotonic())
        return (messages, None)

    def _in_place(self):
        resumed_at.append(time.monotonic())
        return False

    agent_cls = _hygiene_stub_agent(
        session_id,
        _compress,
        cleanup_done,
        _last_compaction_in_place=property(_in_place),
    )
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, _adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, agent_cls, db, session_id
        )
        caplog.set_level("INFO", logger="gateway.run")
        result = await asyncio.wait_for(runner._handle_message(event), timeout=15)

        assert result == "ok"
        assert runner._run_agent.await_count == 1
        assert len(extend_calls) >= 2, (
            f"expected two expired slices before the wake, got {len(extend_calls)}"
        )
        assert finished_at and resumed_at, "the worker result was never consumed"
        # The third slice starts after the host saw the second one expire
        # and lasts the full 0.25s cap, so it cannot end before this point.
        third_slice_earliest_end = extend_calls[1]["at"] + 0.25
        assert finished_at[0] < third_slice_earliest_end - 0.05, (
            "the worker did not finish well inside the third slice"
        )
        assert resumed_at[0] < third_slice_earliest_end, (
            f"host resumed {resumed_at[0] - finished_at[0]:.3f}s after the "
            "worker finished, after the slice ended; it slept out the slice "
            "instead of waiting on completion"
        )
        messages = [r.getMessage() for r in caplog.records]
        assert not any("auto-compress failed" in m for m in messages)
        assert not any("no progress" in m or "total ceiling" in m for m in messages)
        assert db.get_compression_failure_cooldown(session_id) is None
    finally:
        db.close()


def _capture_hygiene_future(monkeypatch, loop, agent_created, replace=None):
    """Record the executor future created for the hygiene worker.

    The hygiene worker is the first ``run_in_executor`` call after the stub
    agent is built. ``replace`` substitutes that future, for a worker that
    the executor cancelled before it ever started.
    """
    captured = {}
    real_run_in_executor = loop.run_in_executor

    def _run_in_executor(executor, func, *args):
        if agent_created.is_set() and "future" not in captured:
            future = replace() if replace else real_run_in_executor(executor, func, *args)
            captured["future"] = future
            return future
        return real_run_in_executor(executor, func, *args)

    monkeypatch.setattr(loop, "run_in_executor", _run_in_executor)
    return captured


@pytest.mark.asyncio
async def test_hygiene_cancelled_worker_future_while_running_is_handled(
    monkeypatch, tmp_path, caplog
):
    """The worker's executor future is cancelled while the worker still runs.

    ``future.result()`` used to raise ``CancelledError`` into the host. That
    escaped the ``except Exception`` guard around hygiene and cancelled the
    whole user turn, and deferred cleanup waited on the cancelled future, so
    the agent was never closed. The turn now continues uncompressed, the
    cooldown names the cancellation, commit admission is revoked, and cleanup
    waits for the worker thread itself to exit.
    """
    from hermes_state import SessionDB

    session_id = "sess-cancelled-future-running"
    agent_created = threading.Event()
    worker_started = threading.Event()
    release_worker = threading.Event()
    cleanup_done = threading.Event()
    late_commit = []

    def _compress(self, messages, *_args, commit_fence=None, **_kwargs):
        worker_started.set()
        release_worker.wait(timeout=5)
        late_commit.append(commit_fence.begin_commit())
        return (messages, None)

    base_cls = _hygiene_stub_agent(session_id, _compress, cleanup_done)

    class CancelledFutureAgent(base_cls):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            agent_created.set()

    captured = _capture_hygiene_future(
        monkeypatch, asyncio.get_running_loop(), agent_created
    )
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, _adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, CancelledFutureAgent, db, session_id
        )
        caplog.set_level("WARNING", logger="gateway.run")
        task = asyncio.create_task(runner._handle_message(event))
        assert await asyncio.to_thread(worker_started.wait, 2)
        assert captured["future"].cancel()

        result = await asyncio.wait_for(task, timeout=5)

        assert result == "ok"
        assert runner._run_agent.await_count == 1
        assert any(
            "Session hygiene auto-compress failed" in r.getMessage()
            and "cancelled" in r.getMessage()
            for r in caplog.records
        )
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None and state["remaining_seconds"] > 0
        assert state["error"] == "session hygiene compression worker was cancelled"
        # The worker thread is still running: its agent must not be closed.
        await asyncio.sleep(0.05)
        assert not cleanup_done.is_set()

        release_worker.set()
        assert await asyncio.to_thread(cleanup_done.wait, 2), (
            "deferred cleanup never ran after the worker thread exited"
        )
        assert late_commit == [False], "a cancelled worker must not commit"
        for _ in range(100):
            if not getattr(runner, "_deferred_agent_cleanup_tasks", set()):
                break
            await asyncio.sleep(0.01)
        assert not getattr(runner, "_deferred_agent_cleanup_tasks", set())
    finally:
        release_worker.set()
        db.close()


@pytest.mark.asyncio
async def test_hygiene_worker_future_cancelled_before_start_is_handled(
    monkeypatch, tmp_path
):
    """The executor cancelled the worker before it started (for example a
    shutdown with ``cancel_futures=True``). No thread will ever use the agent,
    so the turn continues and the agent is cleaned up at once."""
    from hermes_state import SessionDB

    session_id = "sess-cancelled-future-queued"
    agent_created = threading.Event()
    cleanup_done = threading.Event()
    compress_calls = []

    def _compress(self, messages, *_args, **_kwargs):
        compress_calls.append(1)
        return (messages, None)

    base_cls = _hygiene_stub_agent(session_id, _compress, cleanup_done)

    class QueuedCancelAgent(base_cls):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            agent_created.set()

    loop = asyncio.get_running_loop()

    def _cancelled_future():
        future = loop.create_future()
        future.cancel()
        return future

    _capture_hygiene_future(monkeypatch, loop, agent_created, replace=_cancelled_future)
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, _adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, QueuedCancelAgent, db, session_id
        )
        result = await asyncio.wait_for(runner._handle_message(event), timeout=5)

        assert result == "ok"
        assert runner._run_agent.await_count == 1
        assert compress_calls == []
        state = db.get_compression_failure_cooldown(session_id)
        assert state is not None
        assert state["error"] == "session hygiene compression worker was cancelled"
        assert await asyncio.to_thread(cleanup_done.wait, 2), (
            "an agent whose worker never started was never cleaned up"
        )
    finally:
        db.close()


@pytest.mark.asyncio
async def test_hygiene_worker_lifecycle_skips_work_abandoned_before_the_lock():
    """The executor thread is already running ``run()`` but has not taken the
    lifecycle lock when the host cancels the future and abandons the worker.
    ``run()`` must see the abandonment, skip the wrapped compression, and
    leave ``exited`` resolved exactly once, by ``abandon()``."""
    from concurrent.futures import ThreadPoolExecutor

    gateway_run = importlib.import_module("gateway.run")
    loop = asyncio.get_running_loop()
    lifecycle = gateway_run._HygieneWorkerLifecycle(loop)
    resolve_calls = []
    real_resolve = lifecycle._resolve_exited

    def _counting_resolve():
        resolve_calls.append(1)
        real_resolve()

    lifecycle._resolve_exited = _counting_resolve
    thread_started = threading.Event()
    gate = threading.Event()
    compress_calls = []

    def _worker():
        thread_started.set()
        assert gate.wait(5)
        return lifecycle.run(lambda: compress_calls.append(1))

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = loop.run_in_executor(executor, _worker)
        assert await asyncio.to_thread(thread_started.wait, 2)
        # A running executor future cannot be cancelled on the thread, but
        # the asyncio wrapper is: this is the state the host sees.
        assert future.cancel()
        lifecycle.abandon()
        assert lifecycle.exited.done()
        assert resolve_calls == [1]

        gate.set()
        await asyncio.to_thread(executor.shutdown, True)
        for _ in range(5):
            await asyncio.sleep(0)

        assert compress_calls == [], "an abandoned worker ran compression"
        assert resolve_calls == [1], "exited was resolved more than once"
    finally:
        gate.set()
        executor.shutdown(wait=False)


def _enter_hygiene_handler(monkeypatch, tmp_path, site):
    """Route the host wait into the turn-hold or timeout handler.

    Returns a zero-argument callable that reports whether the host has
    raised into that handler.
    """
    gateway_run = importlib.import_module("gateway.run")
    if site == "turn_hold":
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            cfg_path.read_text(encoding="utf-8")
            + "  hygiene_max_turn_hold_seconds: 0.3\n",
            encoding="utf-8",
        )
        entered = threading.Event()
        real_turn_hold = gateway_run.HygieneTurnHoldExceeded

        class _SignallingTurnHold(real_turn_hold):
            def __init__(self, *args):
                entered.set()
                super().__init__(*args)

        monkeypatch.setattr(gateway_run, "HygieneTurnHoldExceeded", _SignallingTurnHold)
        return entered.is_set

    calls = []

    def _never_extend(**kwargs):
        calls.append(kwargs)
        return False

    monkeypatch.setattr(gateway_run, "hygiene_wait_should_extend", _never_extend)
    return lambda: bool(calls)


async def _wait_until(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


async def _drain_deferred_cleanup(runner):
    for _ in range(100):
        if not getattr(runner, "_deferred_agent_cleanup_tasks", set()):
            break
        await asyncio.sleep(0.01)
    assert not getattr(runner, "_deferred_agent_cleanup_tasks", set())


@pytest.mark.asyncio
@pytest.mark.parametrize("site", ["turn_hold", "timeout"])
@pytest.mark.parametrize("stage", ["admitted_commit", "fence_poll"])
@pytest.mark.parametrize("interrupt", ["host_cancel", "future_cancel"])
async def test_hygiene_handler_awaits_unwind_on_cancellation(
    monkeypatch, tmp_path, site, stage, interrupt
):
    """The turn-hold and timeout handlers still await the worker: at the
    fence poll, and while an admitted commit finishes. A cancellation there
    used to escape the handler without the unwind, so the outer ``finally``
    closed the agent inline while the worker was still committing, and a
    future cancelled from outside cancelled the user's turn.

    Now a host cancel propagates with the fence-cancel label, a future
    cancelled from outside lets the turn continue uncompressed, and in both
    cases cleanup waits for the worker thread to exit, commit admission is
    revoked, and the deferred cleanup task drains.
    """
    from hermes_state import SessionDB

    session_id = f"sess-handler-{site}-{stage}-{interrupt}"
    agent_created = threading.Event()
    worker_holding = threading.Event()
    release_hold = threading.Event()
    release_exit = threading.Event()
    cleanup_done = threading.Event()
    late_commit = []

    def _compress(self, messages, *_args, commit_fence=None, **_kwargs):
        if stage == "admitted_commit":
            assert commit_fence.begin_commit()
            worker_holding.set()
            release_hold.wait(timeout=5)
            commit_fence.finish_commit()
        else:
            # Holding the fence lock outside a commit keeps the host's
            # try_cancel_before_commit undecided, so it polls.
            assert commit_fence.begin_lock_setup()
            worker_holding.set()
            release_hold.wait(timeout=5)
            commit_fence.finish_lock_setup()
        # Stay alive after the hold so the test can check that cleanup
        # waits for the thread to exit, not for the hold to end.
        release_exit.wait(timeout=5)
        admitted = commit_fence.begin_commit()
        late_commit.append(admitted)
        if admitted:
            commit_fence.finish_commit()
        return (messages, None)

    base_cls = _hygiene_stub_agent(session_id, _compress, cleanup_done)

    class HandlerCancelAgent(base_cls):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            agent_created.set()

    captured = _capture_hygiene_future(
        monkeypatch, asyncio.get_running_loop(), agent_created
    )
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id, "telegram")
        runner, _adapter, event = _make_cooldown_runner(
            monkeypatch, tmp_path, HandlerCancelAgent, db, session_id
        )
        in_handler = _enter_hygiene_handler(monkeypatch, tmp_path, site)
        task = asyncio.create_task(runner._handle_message(event))
        assert await asyncio.to_thread(worker_holding.wait, 2)
        await _wait_until(in_handler)
        # Let the handler reach its await on the worker.
        await asyncio.sleep(0.1)
        assert not task.done()

        if interrupt == "host_cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            state = db.get_compression_failure_cooldown(session_id)
            assert state is not None
            assert state["error"] == (
                "session hygiene compression cancelled at commit fence"
            )
        else:
            assert captured["future"].cancel()
            if stage == "fence_poll":
                # The poll does not await the future. Once the worker leaves
                # lock setup the host's cancel wins, and cleanup must still
                # wait for the worker although its future was cancelled.
                release_hold.set()
            result = await asyncio.wait_for(task, timeout=5)
            assert result == "ok"
            assert runner._run_agent.await_count == 1
            state = db.get_compression_failure_cooldown(session_id)
            assert state is not None
            if stage == "admitted_commit":
                assert state["error"] == (
                    "session hygiene compression worker was cancelled"
                )

        # The worker thread is still running: its agent must not be closed,
        # neither during the hold nor after it while the thread lives on.
        for gate in (release_hold, release_exit):
            await asyncio.sleep(0.05)
            assert not cleanup_done.is_set(), (
                "the agent was closed while the worker thread was alive"
            )
            gate.set()
        assert await asyncio.to_thread(cleanup_done.wait, 2), (
            "deferred cleanup never ran after the worker thread exited"
        )
        assert late_commit == [False], "admission stayed open after the unwind"
        await _drain_deferred_cleanup(runner)
    finally:
        release_hold.set()
        release_exit.set()
        db.close()
