"""Photon-owned regression coverage for the startup delivery-obligation
redelivery sweep (``GatewayRunner._redeliver_pending_obligations`` +
``gateway/delivery_ledger.py``) through the REAL ``PhotonAdapter.send`` path.

PhotonAdapter dedupes INBOUND sidecar deliveries only (``_handled_deliveries``
/ ``_accepted_handle_deliveries``); it has no outbound dedup, so the ledger's
crash-ambiguity contract is the only guard against a silent duplicate
iMessage. These tests pin that contract on the photon platform:

- an ambiguous (attempting/failed) row redelivers WITH the visible
  ``RECOVERED_MARKER`` prefix, surviving all the way into the sidecar
  ``/send`` body; a pending row redelivers plainly
- a successful redelivery marks the row delivered and a later boot's sweep
  never sends it again (exactly one ``/send`` total)
- a sidecar send failure marks the row failed and leaves it claimed, so only
  the attempts cap / stale cutoff can retire it

The sidecar transport is stubbed at ``_sidecar_call`` (no Node sidecar, no
ports) so the real ``send`` / ``format_message`` / ``_sidecar_send`` chain
runs, mirroring test_inbound.py's no-sidecar style. The runner harness
mirrors tests/gateway/test_delivery_ledger.py's ``TestGatewayRedeliverySweep``.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway import delivery_ledger as dl
from gateway.config import Platform, PlatformConfig
from gateway.run import GatewayRunner
from plugins.platforms.photon.adapter import PhotonAdapter

SPACE_ID = "+15551234567"
SESSION_KEY = f"agent:main:photon:dm:{SPACE_ID}"


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path, monkeypatch):
    """Isolated state.db per test (autouse HERMES_HOME isolation already
    redirects get_hermes_home; make the redirect explicit and per-test)."""
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(dl, "_db_path", lambda: home / "state.db")
    yield


def _make_adapter(monkeypatch: pytest.MonkeyPatch) -> PhotonAdapter:
    monkeypatch.setenv("PHOTON_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("PHOTON_PROJECT_SECRET", "test-project-secret")
    cfg = PlatformConfig(enabled=True, token="", extra={})
    return PhotonAdapter(cfg)


def _stub_sidecar(
    adapter: PhotonAdapter,
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail: bool = False,
) -> List[Tuple[str, Dict[str, Any]]]:
    """Capture sidecar calls at the HTTP seam; the real _sidecar_send runs."""
    calls: List[Tuple[str, Dict[str, Any]]] = []

    async def fake_call(path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        calls.append((path, dict(body)))
        if fail:
            raise RuntimeError("sidecar unreachable")
        return {"messageId": f"sent-{len(calls)}"}

    monkeypatch.setattr(adapter, "_sidecar_call", fake_call)
    return calls


def _runner(adapter: PhotonAdapter) -> GatewayRunner:
    """One simulated gateway boot holding a connected photon adapter."""
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform("photon"): adapter}
    store = MagicMock()
    store.clear_resume_pending = AsyncMock()
    store._store = None
    runner.session_store = None
    runner._async_session_store = store
    return runner


def _record(oid: str = "ob-photon-1", content: str = "the final answer") -> None:
    dl.record_obligation(
        obligation_id=oid,
        session_key=SESSION_KEY,
        platform="photon",
        chat_id=SPACE_ID,
        thread_id=None,
        content=content,
    )


def _orphan(oid: str) -> None:
    """Make the row look like it belongs to a dead gateway process."""
    with dl._connect() as conn:
        conn.execute(
            "UPDATE delivery_obligations SET owner_pid=999999999, "
            "owner_started_at=1 WHERE obligation_id=?",
            (oid,),
        )


def _row(oid: str) -> Dict[str, Any]:
    with dl._connect() as conn:
        state, attempts, owner_pid = conn.execute(
            """SELECT state, attempts, owner_pid
               FROM delivery_obligations WHERE obligation_id=?""",
            (oid,),
        ).fetchone()
    return {"state": state, "attempts": attempts, "owner_pid": owner_pid}


@pytest.mark.asyncio
async def test_ambiguous_row_redelivers_with_recovered_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crash mid-send (state='attempting') → the sidecar /send body carries
    the visible recovered-reply marker, never a silent possible-duplicate."""
    _record()
    dl.mark_attempting("ob-photon-1")
    _orphan("ob-photon-1")
    adapter = _make_adapter(monkeypatch)
    calls = _stub_sidecar(adapter, monkeypatch)

    n = await _runner(adapter)._redeliver_pending_obligations()

    assert n == 1
    assert len(calls) == 1
    path, body = calls[0]
    assert path == "/send"
    assert body["spaceId"] == SPACE_ID
    assert body["text"].startswith(dl.RECOVERED_MARKER)
    assert body["text"].endswith("the final answer")
    assert _row("ob-photon-1")["state"] == "delivered"


@pytest.mark.asyncio
async def test_pending_row_redelivers_plain_and_clears_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A send that never started (state='pending') has no dup risk: no
    marker, and the session's resume_pending is cleared so the turn is not
    re-run on top of the redelivery."""
    _record()
    _orphan("ob-photon-1")
    adapter = _make_adapter(monkeypatch)
    calls = _stub_sidecar(adapter, monkeypatch)
    runner = _runner(adapter)

    n = await runner._redeliver_pending_obligations()

    assert n == 1
    _, body = calls[0]
    assert body["text"] == "the final answer"
    assert _row("ob-photon-1")["state"] == "delivered"
    runner._async_session_store.clear_resume_pending.assert_awaited_once_with(
        SESSION_KEY
    )


@pytest.mark.asyncio
async def test_successful_redelivery_never_sends_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once a redelivery succeeds the row is delivered; a later boot's sweep
    must not claim it again even if the delivering process looks dead —
    PhotonAdapter has no outbound dedup, so a second send here would be a
    duplicate iMessage."""
    _record()
    dl.mark_attempting("ob-photon-1")
    _orphan("ob-photon-1")
    adapter = _make_adapter(monkeypatch)
    calls = _stub_sidecar(adapter, monkeypatch)

    assert await _runner(adapter)._redeliver_pending_obligations() == 1
    assert _row("ob-photon-1")["state"] == "delivered"

    # Next boot: the previous owner is "dead" again, but the delivered row
    # must stay out of the sweep.
    _orphan("ob-photon-1")
    assert await _runner(adapter)._redeliver_pending_obligations() == 0
    assert len(calls) == 1, "a delivered obligation was sent a second time"


@pytest.mark.asyncio
async def test_send_failure_leaves_row_claimed_for_attempts_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sidecar failure marks the row failed but claimed by this boot; each
    failing boot spends one attempt until the cap abandons the row."""
    _record()
    _orphan("ob-photon-1")
    adapter = _make_adapter(monkeypatch)
    calls = _stub_sidecar(adapter, monkeypatch, fail=True)

    n = await _runner(adapter)._redeliver_pending_obligations()

    assert n == 0
    row = _row("ob-photon-1")
    assert row["state"] == "failed"
    assert row["attempts"] == 1
    assert row["owner_pid"] == os.getpid(), "claim must re-stamp ownership"

    # Every later failing boot spends exactly one attempt on a real send...
    for expected_attempts in range(2, dl.MAX_ATTEMPTS + 1):
        _orphan("ob-photon-1")
        assert await _runner(adapter)._redeliver_pending_obligations() == 0
        row = _row("ob-photon-1")
        assert row["state"] == "failed"
        assert row["attempts"] == expected_attempts
    assert len(calls) == dl.MAX_ATTEMPTS

    # ...and the boot after the cap abandons the row without sending.
    _orphan("ob-photon-1")
    assert await _runner(adapter)._redeliver_pending_obligations() == 0
    assert _row("ob-photon-1")["state"] == "abandoned"
    assert len(calls) == dl.MAX_ATTEMPTS
