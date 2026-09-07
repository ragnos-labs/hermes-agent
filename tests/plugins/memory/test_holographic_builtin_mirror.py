"""Built-in corrections invalidate the matching mirrored fact, across restart."""

import json

import pytest

from agent.memory_manager import MemoryManager
from plugins.memory.holographic import HolographicMemoryProvider
from tools.memory_tool import MemoryStore, memory_tool


def provider(tmp_path):
    result = HolographicMemoryProvider(config={"db_path": str(tmp_path / "facts.db"), "hrr_dim": 64})
    result.initialize(session_id="mirror-test")
    return result


def contents(value):
    return {row["content"] for row in value._store.list_facts(limit=1000)}


def test_add_replace_remove_with_substring_and_restart(tmp_path):
    first = provider(tmp_path)
    first.on_memory_write("add", "memory", "The project uses SQLite.", {"session_id": "source"})
    first.shutdown()
    second = provider(tmp_path)
    try:
        second.on_memory_write("replace", "memory", "The project uses PostgreSQL.", {"old_text": "uses SQLite"})
        assert contents(second) == {"The project uses PostgreSQL."}
        assert not second._store.search_facts("SQLite")
        second.on_memory_write("remove", "memory", "", {"old_text": "PostgreSQL"})
        assert contents(second) == set()
        assert not second._store.search_facts("PostgreSQL")
    finally:
        second.shutdown()


def test_duplicate_replay_and_two_targets_keep_one_live_fact(tmp_path):
    value = provider(tmp_path)
    try:
        for target in ("memory", "memory", "user"):
            value.on_memory_write("add", target, "Use small experiments.")
        value.on_memory_write("remove", "memory", "", {"old_text": "small experiments"})
        assert contents(value) == {"Use small experiments."}
        value.on_memory_write("remove", "user", "", {"old_text": "small experiments"})
        assert contents(value) == set()
    finally:
        value.shutdown()


def test_independently_stored_facts_are_preserved(tmp_path):
    value = provider(tmp_path)
    try:
        value._store.add_fact("An independent observation.")
        value.on_memory_write("add", "memory", "An independent observation.")
        value.on_memory_write("remove", "memory", "", {"old_text": "independent observation"})
        assert contents(value) == {"An independent observation."}
    finally:
        value.shutdown()


def test_real_memory_tool_manager_bridge_handles_new_text_alias(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    builtin = MemoryStore()
    value = provider(tmp_path)
    manager = MemoryManager.__new__(MemoryManager)
    manager._providers = [value]
    try:
        for arguments in (
            {"action": "add", "content": "Check the old battery."},
            {"action": "replace", "old_text": "old battery", "new_text": "Check the new battery."},
        ):
            result = memory_tool(store=builtin, **arguments)
            assert json.loads(result)["success"]
            manager.notify_memory_tool_write(result, arguments, build_metadata=lambda: {"task_id": "test"})
        assert contents(value) == {"Check the new battery."}
        row = value._store._conn.execute("SELECT provenance FROM builtin_memory_mirrors").fetchone()
        assert json.loads(row["provenance"])["task_id"] == "test"
        # A failed or staged write must never update the fact store.
        manager.notify_memory_tool_write({"success": True, "staged": True}, {"action": "remove", "old_text": "new battery"})
        assert contents(value) == {"Check the new battery."}
    finally:
        value.shutdown()


@pytest.mark.parametrize("operations,expected", [
    ([
        {"action": "add", "content": "Check battery."},
        {"action": "add", "content": "Check lamp."},
        {"action": "replace", "old_text": "lamp", "content": "Check battery."},
        {"action": "remove", "old_text": "battery"},
    ], {"Check battery."}),
    ([
        {"action": "add", "content": "Check battery."},
        {"action": "replace", "old_text": "battery", "content": "", "new_text": "Check fuel."},
    ], {"Check fuel."}),
])
def test_committed_batch_matches_fact_store(tmp_path, monkeypatch, operations, expected):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    builtin = MemoryStore()
    value = provider(tmp_path)
    manager = MemoryManager.__new__(MemoryManager)
    manager._providers = [value]
    try:
        arguments = {"operations": operations}
        result = memory_tool(store=builtin, **arguments)
        assert json.loads(result)["success"]
        manager.notify_memory_tool_write(result, arguments)
        assert set(builtin.memory_entries) == expected == contents(value)
        # The following operation reloads and deduplicates the native entries.
        remove = {"action": "remove", "old_text": next(iter(expected))}
        result = memory_tool(store=builtin, **remove)
        manager.notify_memory_tool_write(result, remove)
        assert json.loads(result)["success"]
        assert contents(value) == set()
    finally:
        value.shutdown()
