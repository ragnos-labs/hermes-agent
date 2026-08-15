"""Behavioral conformance tests for the durable execution read contract."""

from __future__ import annotations

import concurrent.futures
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hermes_cli.execution_contract import (
    CONTRACT_VERSION,
    ContractConflictError,
    ContractCursorGoneError,
    ContractDataError,
    ContractForbiddenError,
    ContractValidationError,
    ExecutionContractStore,
    UnsupportedContractVersionError,
    canonical_digest,
    contract_capabilities,
    contract_schema,
)


NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parents[1] / "fixtures" / "execution_contract"


def _digest(label: str) -> str:
    return canonical_digest({"fixture": label})


def _store(tmp_path, *, profile="default", runtime="runtime-a"):
    return ExecutionContractStore(
        database_path=tmp_path / profile / "execution_contract.sqlite3",
        profile_name=profile,
        runtime_instance_id=runtime,
    )


def _bound_execution(store: ExecutionContractStore, *, suffix="one"):
    return store.create_execution(
        lifecycle="queued",
        source_run_id=f"run-{suffix}",
        work_ref=f"work:{suffix}",
        proposal_ref=f"proposal:{suffix}",
        effect_id=f"effect:{suffix}",
        now=NOW,
    )


def _resolved_decision(store: ExecutionContractStore, execution: dict, *, suffix="one"):
    decision = store.create_decision(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        proposal_ref=execution["proposal_ref"],
        request_digest=_digest(f"request-{suffix}"),
        candidate_digest=_digest(f"candidate-{suffix}"),
        policy_digest=_digest(f"policy-{suffix}"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    return store.resolve_decision(
        decision["decision_id"],
        choice="once",
        resolution_evidence_digest=_digest(f"resolution-{suffix}"),
        now=NOW + timedelta(seconds=1),
    )


def _record_evidence(
    store: ExecutionContractStore,
    execution: dict,
    *,
    outcome="succeeded",
    decision_id=None,
    suffix="one",
    reconciliation_ref=None,
):
    return store.record_effect_evidence(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        outcome=outcome,
        subject_digest=_digest(f"subject-{suffix}"),
        evidence_digest=_digest(f"evidence-{suffix}"),
        result_digest=_digest(f"result-{suffix}"),
        decision_id=decision_id,
        reconciliation_ref=reconciliation_ref,
        now=NOW + timedelta(seconds=2),
    )


def test_absent_read_is_side_effect_free_then_create_reopen_and_migrate(tmp_path):
    store = _store(tmp_path)
    assert not store.database_path.exists()

    empty = store.list_executions()

    assert empty["items"] == []
    assert empty["page"] == {
        "cursor": 0,
        "high_water": 0,
        "minimum_available": 0,
        "has_more": False,
        "completeness": "complete",
    }
    assert not store.database_path.exists()

    execution = store.create_execution(
        lifecycle="accepted",
        source_run_id="run-create",
        work_ref="work:create",
        proposal_ref="proposal:create",
        now=NOW,
    )

    assert store.database_path.exists()
    assert os.stat(store.database_path).st_mode & 0o077 == 0
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    reopened = _store(tmp_path)
    assert reopened.get_execution(execution["execution_id"])["revision"] == 1
    assert reopened.get_execution(execution["execution_id"])["freshness"] == "live"


def test_packaged_schema_and_synthetic_fixtures_are_closed():
    schema = contract_schema()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    fixture_defs = {
        "capabilities.json": ("capabilities", None),
        "execution-list.json": ("executionList", "execution"),
        "decision-list.json": ("decisionList", "decision"),
        "event-list.json": ("eventList", "event"),
        "receipt-list.json": ("receiptList", "receipt"),
    }
    for filename, (definition_name, item_definition_name) in fixture_defs.items():
        payload = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
        definition = schema["$defs"][definition_name]
        assert definition["additionalProperties"] is False
        assert set(payload) == set(definition["required"])
        if item_definition_name:
            collection = definition_name.removesuffix("List").lower() + "s"
            item_definition = schema["$defs"][item_definition_name]
            assert item_definition["additionalProperties"] is False
            assert set(payload[collection][0]) == set(item_definition["required"])

    capabilities = contract_capabilities("synthetic")
    assert capabilities == json.loads(
        (FIXTURES / "capabilities.json").read_text(encoding="utf-8")
    )
    assert schema["$defs"]["errorDetail"]["additionalProperties"] is False


def test_unknown_store_version_fails_closed(tmp_path):
    store = _store(tmp_path)
    store.database_path.parent.mkdir(parents=True)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA user_version=99")

    with pytest.raises(UnsupportedContractVersionError):
        store.initialize()
    with pytest.raises(UnsupportedContractVersionError):
        store.list_executions()


@pytest.mark.parametrize(
    ("outcome", "lifecycle"),
    [
        ("succeeded", "terminal_succeeded"),
        ("failed", "terminal_failed"),
        ("cancelled", "terminal_cancelled"),
        ("partial", "terminal_partial"),
        ("ambiguous", "terminal_ambiguous"),
    ],
)
def test_all_evidence_backed_receipt_outcomes_publish_atomically(
    tmp_path,
    outcome,
    lifecycle,
):
    store = _store(tmp_path)
    execution = _bound_execution(store, suffix=outcome)
    store.transition_execution(execution["execution_id"], "running", now=NOW)
    evidence = _record_evidence(store, execution, outcome=outcome, suffix=outcome)

    terminal = store.transition_execution(
        execution["execution_id"],
        lifecycle,
        now=NOW + timedelta(seconds=3),
    )

    assert evidence["outcome"] == outcome
    assert terminal["lifecycle"] == lifecycle
    assert terminal["receipt_state"] == "published"
    receipt = store.get_receipt(terminal["receipt_id"])
    assert receipt["execution_id"] == execution["execution_id"]
    assert receipt["effect_id"] == execution["effect_id"]
    assert receipt["outcome"] == outcome
    assert receipt["revision"] == 1
    events = store.list_events(execution_id=execution["execution_id"])["items"]
    assert events[-2]["event_type"] == "execution.transitioned"
    assert events[-1]["event_type"] == "receipt.published"
    assert events[-1]["receipt_id"] == receipt["receipt_id"]


def test_nonterminal_lifecycle_progression_and_initial_state_are_closed(tmp_path):
    store = _store(tmp_path)
    execution = store.create_execution(lifecycle="accepted", now=NOW)
    queued = store.transition_execution(
        execution["execution_id"],
        "queued",
        now=NOW + timedelta(seconds=1),
    )
    running = store.transition_execution(
        execution["execution_id"],
        "running",
        now=NOW + timedelta(seconds=2),
    )
    cancelling = store.transition_execution(
        execution["execution_id"],
        "cancellation_requested",
        now=NOW + timedelta(seconds=3),
    )
    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_cancelled",
        now=NOW + timedelta(seconds=4),
    )
    assert [
        queued["lifecycle"],
        running["lifecycle"],
        cancelling["lifecycle"],
        terminal["lifecycle"],
    ] == ["queued", "running", "cancellation_requested", "terminal_cancelled"]
    with pytest.raises(ContractValidationError, match="initial lifecycle"):
        store.create_execution(lifecycle="awaiting_decision", now=NOW)


def test_unproven_effect_is_terminal_ambiguous_without_receipt(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store)
    store.transition_execution(execution["execution_id"], "running", now=NOW)

    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_succeeded",
        reason_code="generic_chat_completed",
        now=NOW + timedelta(seconds=1),
    )

    assert terminal["lifecycle"] == "terminal_ambiguous"
    assert terminal["receipt_state"] == "unproven"
    assert terminal["receipt_id"] is None
    assert store.list_receipts()["items"] == []


def test_execution_without_external_effect_can_terminate_without_receipt(tmp_path):
    store = _store(tmp_path)
    execution = store.create_execution(
        lifecycle="running",
        source_run_id="run-chat-only",
        work_ref="work:chat",
        proposal_ref="proposal:chat",
        now=NOW,
    )

    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_succeeded",
        now=NOW + timedelta(seconds=1),
    )

    assert terminal["lifecycle"] == "terminal_succeeded"
    assert terminal["receipt_state"] == "not_applicable"
    assert terminal["receipt_id"] is None


def test_pending_resolved_expired_and_superseded_decisions(tmp_path):
    store = _store(tmp_path)

    resolved_execution = _bound_execution(store, suffix="resolved")
    resolved = _resolved_decision(store, resolved_execution, suffix="resolved")
    assert resolved["state"] == "resolved"
    assert resolved["choice"] == "once"
    assert resolved["resolution_evidence_digest"] == _digest("resolution-resolved")

    expiring_execution = _bound_execution(store, suffix="expired")
    expiring = store.create_decision(
        execution_id=expiring_execution["execution_id"],
        effect_id=expiring_execution["effect_id"],
        proposal_ref=expiring_execution["proposal_ref"],
        request_digest=_digest("request-expired"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(seconds=1),
        now=NOW,
    )
    assert store.expire_decisions(now=NOW + timedelta(seconds=2)) == [
        expiring["decision_id"]
    ]
    assert store.get_decision(expiring["decision_id"])["state"] == "expired"
    assert store.get_execution(expiring_execution["execution_id"])["lifecycle"] == (
        "terminal_ambiguous"
    )

    superseded_execution = _bound_execution(store, suffix="superseded")
    superseded = store.create_decision(
        execution_id=superseded_execution["execution_id"],
        effect_id=superseded_execution["effect_id"],
        proposal_ref=superseded_execution["proposal_ref"],
        request_digest=_digest("request-superseded"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    superseded = store.supersede_decision(
        superseded["decision_id"],
        reason_code="proposal_replaced",
        now=NOW + timedelta(seconds=1),
    )
    assert superseded["state"] == "superseded"
    assert store.list_decisions(state="pending")["items"] == []


def test_one_pending_decision_and_terminalization_closes_it(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store)
    decision = store.create_decision(
        execution_id=execution["execution_id"],
        effect_id=execution["effect_id"],
        proposal_ref=execution["proposal_ref"],
        request_digest=_digest("request-first"),
        allowed_choices=["once", "deny"],
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    )
    with pytest.raises(ContractConflictError, match="already has a pending"):
        store.create_decision(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            proposal_ref=execution["proposal_ref"],
            request_digest=_digest("request-second"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )

    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        now=NOW + timedelta(seconds=1),
    )
    assert terminal["lifecycle"] == "terminal_ambiguous"
    assert store.get_decision(decision["decision_id"])["state"] == "superseded"
    assert store.expire_decisions(now=NOW + timedelta(minutes=10)) == []


def test_decision_and_effect_bindings_fail_closed(tmp_path):
    store = _store(tmp_path)
    first = _bound_execution(store, suffix="first")
    second = _bound_execution(store, suffix="second")

    with pytest.raises(ContractConflictError, match="effect binding"):
        store.create_decision(
            execution_id=first["execution_id"],
            effect_id=second["effect_id"],
            proposal_ref=first["proposal_ref"],
            request_digest=_digest("wrong-effect"),
            allowed_choices=["once", "deny"],
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )

    decision = _resolved_decision(store, first, suffix="first")
    with pytest.raises(ContractConflictError, match="exact decision"):
        _record_evidence(store, first, decision_id=None)
    with pytest.raises(ContractConflictError, match="not bound"):
        _record_evidence(
            store,
            first,
            decision_id="dec_"
            + store.authority.profile_key
            + "_"
            + "f" * 32,
        )
    with pytest.raises(ContractConflictError, match="effect evidence binding"):
        store.record_effect_evidence(
            execution_id=first["execution_id"],
            effect_id="effect:wrong",
            outcome="succeeded",
            subject_digest=_digest("subject"),
            evidence_digest=_digest("evidence"),
            result_digest=_digest("result"),
            decision_id=decision["decision_id"],
            now=NOW,
        )


def test_duplicate_evidence_is_idempotent_and_conflict_is_rejected(tmp_path):
    store = _store(tmp_path)
    execution = _bound_execution(store)
    first = _record_evidence(store, execution)
    duplicate = _record_evidence(store, execution)
    assert duplicate == first

    with pytest.raises(ContractConflictError, match="conflicting effect evidence"):
        _record_evidence(store, execution, outcome="partial")


def test_optimistic_revision_and_out_of_order_lifecycle_fail_closed(tmp_path):
    store = _store(tmp_path)
    execution = store.create_execution(lifecycle="queued", now=NOW)

    running = store.transition_execution(
        execution["execution_id"],
        "running",
        expected_revision=1,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ContractConflictError, match="revision"):
        store.transition_execution(
            execution["execution_id"],
            "cancellation_requested",
            expected_revision=1,
            now=NOW + timedelta(seconds=2),
        )
    terminal = store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        expected_revision=running["revision"],
        now=NOW + timedelta(seconds=2),
    )
    with pytest.raises(ContractConflictError, match="invalid execution transition"):
        store.transition_execution(
            terminal["execution_id"],
            "running",
            now=NOW + timedelta(seconds=3),
        )


def test_transaction_rolls_back_state_when_event_append_fails(tmp_path, monkeypatch):
    store = _store(tmp_path)
    execution = store.create_execution(lifecycle="queued", now=NOW)

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("synthetic crash boundary")

    monkeypatch.setattr(store, "_append_event_in_txn", fail_event)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        store.transition_execution(
            execution["execution_id"],
            "running",
            now=NOW + timedelta(seconds=1),
        )

    reopened = _store(tmp_path)
    current = reopened.get_execution(execution["execution_id"])
    assert current["lifecycle"] == "queued"
    assert current["revision"] == 1
    assert len(reopened.list_events()["items"]) == 1


def test_terminal_receipt_and_event_roll_back_together(tmp_path, monkeypatch):
    store = _store(tmp_path)
    execution = _bound_execution(store)
    store.transition_execution(execution["execution_id"], "running", now=NOW)
    _record_evidence(store, execution)
    original_append = store._append_event_in_txn

    def fail_terminal_event(*args, **kwargs):
        if kwargs.get("event_type") == "execution.transitioned":
            raise RuntimeError("synthetic crash after receipt insert")
        return original_append(*args, **kwargs)

    monkeypatch.setattr(store, "_append_event_in_txn", fail_terminal_event)
    with pytest.raises(RuntimeError, match="after receipt insert"):
        store.transition_execution(
            execution["execution_id"],
            "terminal_succeeded",
            now=NOW + timedelta(seconds=3),
        )

    reopened = _store(tmp_path)
    current = reopened.get_execution(execution["execution_id"])
    assert current["lifecycle"] == "running"
    assert current["receipt_state"] == "pending_evidence"
    assert reopened.list_receipts()["items"] == []


def test_restart_marks_orphaned_nonterminal_execution_ambiguous(tmp_path):
    first_runtime = _store(tmp_path, runtime="runtime-a")
    execution = first_runtime.create_execution(lifecycle="running", now=NOW)

    second_runtime = _store(tmp_path, runtime="runtime-b")
    before = second_runtime.get_execution(execution["execution_id"])
    assert before["freshness"] == "stale"

    recovered = second_runtime.recover_orphaned_executions(
        recovery_ref="restart:test",
        now=NOW + timedelta(seconds=1),
    )

    assert recovered == [execution["execution_id"]]
    after = second_runtime.get_execution(execution["execution_id"])
    assert after["lifecycle"] == "terminal_ambiguous"
    assert after["freshness"] == "terminal"
    assert after["recovery_ref"] == "restart:test"


def test_concurrent_append_has_monotonic_unique_sequences_and_pagination(tmp_path):
    path = tmp_path / "shared" / "execution_contract.sqlite3"

    def create(index: int) -> str:
        store = ExecutionContractStore(
            database_path=path,
            profile_name="default",
            runtime_instance_id="runtime-a",
        )
        return store.create_execution(
            lifecycle="queued",
            source_run_id=f"run-{index}",
            now=NOW + timedelta(microseconds=index),
        )["execution_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(create, range(24)))

    assert len(set(ids)) == 24
    store = ExecutionContractStore(
        database_path=path,
        profile_name="default",
        runtime_instance_id="runtime-a",
    )
    first = store.list_events(limit=7)
    second = store.list_events(after=first["page"]["cursor"], limit=200)
    sequences = [event["sequence"] for event in first["items"] + second["items"]]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences)) == 24
    assert first["page"]["has_more"] is True
    assert second["page"]["high_water"] == sequences[-1]
    assert second["page"]["has_more"] is False


def test_pruned_cursor_returns_explicit_gap(tmp_path):
    store = _store(tmp_path)
    old = NOW - timedelta(days=40)
    execution = store.create_execution(lifecycle="running", now=old)
    store.transition_execution(
        execution["execution_id"],
        "terminal_failed",
        now=old + timedelta(seconds=1),
    )
    assert store.prune_events(now=NOW) == 2

    with pytest.raises(ContractCursorGoneError) as exc:
        store.list_events(after=0)
    assert exc.value.minimum_available == 3
    assert exc.value.high_water == 2


def test_retention_does_not_prune_across_a_live_execution_gap(tmp_path):
    store = _store(tmp_path)
    old = NOW - timedelta(days=40)
    live = store.create_execution(lifecycle="running", now=old)
    terminal = store.create_execution(lifecycle="running", now=old)
    store.transition_execution(
        terminal["execution_id"],
        "terminal_failed",
        now=old + timedelta(seconds=1),
    )

    assert store.prune_events(now=NOW) == 0
    events = store.list_events(after=0)["items"]
    assert events[0]["execution_id"] == live["execution_id"]
    assert len(events) == 3


def test_profile_crossing_and_malformed_identifiers_are_distinct(tmp_path):
    first = _store(tmp_path, profile="first")
    second = _store(tmp_path, profile="second")
    execution = first.create_execution(lifecycle="queued", now=NOW)

    with pytest.raises(ContractForbiddenError):
        second.get_execution(execution["execution_id"])
    with pytest.raises(ContractValidationError):
        first.get_execution("run_not-an-execution-id")


def test_store_file_is_bound_to_exact_profile_authority(tmp_path):
    path = tmp_path / "shared" / "execution_contract.sqlite3"
    first = ExecutionContractStore(
        database_path=path,
        profile_name="first",
        runtime_instance_id="runtime-a",
    )
    first.create_execution(lifecycle="queued", now=NOW)
    misplaced = ExecutionContractStore(
        database_path=path,
        profile_name="second",
        runtime_instance_id="runtime-a",
    )

    with pytest.raises(ContractDataError, match="authority metadata"):
        misplaced.list_executions()
    with pytest.raises(ContractDataError, match="authority metadata"):
        misplaced.initialize()


def test_malformed_persisted_state_and_cursor_ahead_fail_closed(tmp_path):
    store = _store(tmp_path)
    execution = store.create_execution(lifecycle="queued", now=NOW)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE executions SET lifecycle='future_state' WHERE execution_id=?",
            (execution["execution_id"],),
        )
        connection.commit()

    with pytest.raises(ContractDataError, match="unknown state"):
        store.get_execution(execution["execution_id"])
    with pytest.raises(ContractConflictError, match="cursor"):
        store.list_events(after=999)


def test_closed_reference_and_digest_validation(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ContractValidationError):
        store.create_execution(lifecycle="queued", work_ref="bad\nref", now=NOW)
    with pytest.raises(ContractValidationError, match="exact work_ref"):
        store.create_execution(
            lifecycle="queued",
            effect_id="effect:unbound",
            now=NOW,
        )
    with pytest.raises(ContractValidationError, match="timezone-aware"):
        store.create_execution(
            lifecycle="queued",
            now=datetime(2026, 8, 15, 12, 0),
        )
    execution = _bound_execution(store)
    with pytest.raises(ContractValidationError, match="SHA-256"):
        store.record_effect_evidence(
            execution_id=execution["execution_id"],
            effect_id=execution["effect_id"],
            outcome="succeeded",
            subject_digest="not-a-digest",
            evidence_digest=_digest("evidence"),
            result_digest=_digest("result"),
            now=NOW,
        )
    assert CONTRACT_VERSION == execution["contract_version"]
