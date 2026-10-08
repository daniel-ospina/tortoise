"""#3516 §B — the client-timestamp floor (``client_captured_at`` + its source).

#3515 piece 12 pins the contract: the store-proven check pairs with a floor that
compares the matched record's **client-recorded** capture instant against the
**server-recorded** install time, ±5 minutes, and a timestamp whose source is
``unknown`` can NEVER be counted as a pass. The client clock is the floor's
input because the server's ``capturedAt`` is the *ingest* transaction time — a
stale spool drained after an install would otherwise read as freshly captured
and the floor could only ever pass.

Every test asserts an OBSERVABLE artifact — the property read back off the
stored ``:Session``, the key really stored in the spool entry, or the floor's
own verdict — never that a helper was called.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.test_hosted_api import TEST_ORG_ID
from tortoise import hosted_api as ha_mod
from tortoise.capture_install import (
    FLOOR_SKEW_TOLERANCE_S,
    VERDICT_DISABLED,
    VERDICT_FAILED,
    VERDICT_PASSED,
    client_capture_floor_verdict,
)

#: One fixed install instant. Both clocks are set independently around it —
#: without that, the floor test could not fail.
INSTALL = 1_700_000_000.0


@pytest.fixture(autouse=True)
def _offline_llm(monkeypatch):
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture(autouse=True)
def _warm_embedded_graph():
    """#3834/#4098: warm the graph the same way tests/test_capture_lane_3516.py
    does, so these assertions are about the stamp and not about box load."""
    ha_mod._make_sdk(namespace=TEST_ORG_ID)._get_proj().g.query("RETURN 1")


def _stamp(sdk, sid: str, key: str):
    """The property as STORED on the :Session — the artifact the floor reads."""
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s." + key,
        params={"sid": sid}).result_set
    assert rows, f"no :Session {sid!r} — the capture did not land"
    return rows[0][0]


# ── the contract: on the writer, and round-tripping through the graph ─────


def test_client_stamp_is_persisted_and_read_back():
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    sid = "3516-stamp-roundtrip"
    _write_session_and_turns(
        sdk._get_proj(), sdk, sid, [{"role": "user", "content": "hi"}],
        now="2026-10-08T00:00:00Z", harness="claude", capture_lane="hook",
        client_captured_at=1_700_000_000.5,
        client_captured_at_source="cli_observed")
    assert _stamp(sdk, sid, "client_captured_at") == pytest.approx(1_700_000_000.5)
    assert _stamp(sdk, sid, "client_captured_at_source") == "cli_observed"


def test_absent_stamp_is_never_fabricated():
    """A pre-#3516 producer POSTs without it: absence must stay absence, never a
    fabricated instant — a fabricated floor input would pass on nothing."""
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    sid = "3516-stamp-absent"
    _write_session_and_turns(
        sdk._get_proj(), sdk, sid, [{"role": "user", "content": "hi"}],
        now="2026-10-08T00:00:00Z", harness="claude", capture_lane="hook")
    assert _stamp(sdk, sid, "client_captured_at") is None
    assert _stamp(sdk, sid, "client_captured_at_source") is None


def test_store_sync_cannot_overwrite_the_hooks_stamp():
    """First writer wins, exactly like the lane. The store-sync backstop ships
    the SAME session AFTER the hook, so last-writer-wins would replace the
    hook's own observation with a weaker file mtime — and the floor would then
    be comparing an mtime against an install instant it was never about."""
    from tortoise.sdk import _write_session_and_turns

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    sid = "3516-stamp-firstwriter"
    turns = [{"role": "user", "content": "hi"}]
    _write_session_and_turns(sdk._get_proj(), sdk, sid, turns,
                             now="2026-10-08T00:00:00Z", harness="claude",
                             capture_lane="hook", client_captured_at=1000.0,
                             client_captured_at_source="cli_observed")
    _write_session_and_turns(sdk._get_proj(), sdk, sid, turns,
                             now="2026-10-08T00:00:01Z", harness="claude",
                             capture_lane="store_sync", client_captured_at=2000.0,
                             client_captured_at_source="file_mtime")
    assert _stamp(sdk, sid, "client_captured_at") == pytest.approx(1000.0)
    assert _stamp(sdk, sid, "client_captured_at_source") == "cli_observed"


def test_boundary_admits_unknown_but_refuses_an_unrecognised_source():
    """The source set is CLOSED: the floor's falsifiability depends on 'unknown'
    being an excludable value, which requires every stored value to be one the
    floor can interpret. 'unknown' is legal; anything else is a 422."""
    from tortoise.hosted_api import SessionRequest

    ok = SessionRequest(conversation=[{"role": "user", "content": "x"}],
                        client_captured_at_source="unknown")
    assert ok.client_captured_at_source == "unknown"
    with pytest.raises(ValidationError):
        SessionRequest(conversation=[{"role": "user", "content": "x"}],
                       client_captured_at_source="wall_clock")


# ── the floor: two clocks, one tolerance, and it must be falsifiable ──────


def test_clock_skew_tolerance():
    """Two clocks, independently set. Inside ±5 min passes either side of the
    install; beyond it is REJECTED WITH A REASON, never silently accepted."""
    assert FLOOR_SKEW_TOLERANCE_S == 300.0
    assert client_capture_floor_verdict(
        INSTALL + 1, "cli_observed", INSTALL)[0] == VERDICT_PASSED
    assert client_capture_floor_verdict(
        INSTALL - 299, "cli_observed", INSTALL)[0] == VERDICT_PASSED
    verdict, why = client_capture_floor_verdict(
        INSTALL - 301, "cli_observed", INSTALL)
    assert verdict == VERDICT_FAILED
    assert why, "a rejection must always carry a reason"
    # the boundary is exclusive: exactly AT the floor does not pass.
    assert client_capture_floor_verdict(
        INSTALL - 300, "cli_observed", INSTALL)[0] == VERDICT_FAILED


def test_store_proven_timestamp_floor_rejects_a_pre_install_row():
    """The pre-install NEGATIVE FIXTURE (piece 12): a row captured an hour
    before the install must not satisfy the floor. This is the case the whole
    client-clock decision exists for — the drain ships exactly such rows AFTER
    an install, and their server ``capturedAt`` is now."""
    verdict, why = client_capture_floor_verdict(
        INSTALL - 3600, "file_mtime", INSTALL)
    assert verdict == VERDICT_FAILED
    assert "behind" in why


def test_floor_mutation_control():
    """Remove the floor and the verdict MUST change. A check whose verdict does
    not move when the floor is deleted is not testing the floor."""
    pre_install = INSTALL - 3600
    assert client_capture_floor_verdict(
        pre_install, "file_mtime", INSTALL)[0] == VERDICT_FAILED
    # tolerance=inf is the floor deleted: every instant clears it.
    assert client_capture_floor_verdict(
        pre_install, "file_mtime", INSTALL,
        tolerance=float("inf"))[0] == VERDICT_PASSED


def test_unknown_source_can_never_pass_the_floor():
    """The falsifiability property itself: an admitted backfill gap is DISABLED,
    never a pass — even when its instant would satisfy the comparison."""
    verdict, why = client_capture_floor_verdict(INSTALL + 1, "unknown", INSTALL)
    assert verdict == VERDICT_DISABLED
    assert verdict != VERDICT_PASSED
    assert "unknown" in why
    # and an un-evaluable floor is DISABLED, never a pass.
    assert client_capture_floor_verdict(None, None, INSTALL)[0] == VERDICT_DISABLED
    assert client_capture_floor_verdict(
        INSTALL + 1, "cli_observed", None)[0] == VERDICT_DISABLED


# ── the producer leg: the stamp survives the spool, absent stays absent ───


def test_spool_entry_keeps_the_stamp_and_a_restamp_free_re_snapshot(tmp_path):
    """The spool is what actually gets delivered, so the stamp must survive it:
    stored on the entry, and NOT erased by a later re-snapshot that carries no
    stamp (the byte-identical-turns path)."""
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry

    root = tmp_path / "spool"
    sid = "3516-spool-stamp"
    write_spool_entry(root, Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "hi"}],
        source="t", machine_id="m", harness="claude", capture_lane="hook",
        client_captured_at=1234.5,
        client_captured_at_source="cli_observed"))
    meta = read_spool_meta(root, sid)
    assert meta["client_captured_at"] == pytest.approx(1234.5)
    assert meta["client_captured_at_source"] == "cli_observed"

    write_spool_entry(root, Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "hi"}],
        source="t", machine_id="m", harness="claude", capture_lane="hook"))
    assert read_spool_meta(root, sid)["client_captured_at"] == pytest.approx(1234.5), (
        "a stamp-less re-snapshot erased the client stamp")


def test_spool_entry_without_a_stamp_has_none(tmp_path):
    """Set-only-when-present on the producer leg too — a pre-#3516 entry must
    not acquire an instant it never observed."""
    from tortoise.capture_spool import Snapshot, read_spool_meta, write_spool_entry

    root = tmp_path / "spool"
    sid = "3516-spool-nostamp"
    write_spool_entry(root, Snapshot(
        session_id=sid, turns=[{"role": "user", "content": "hi"}],
        source="t", machine_id="m", harness="claude", capture_lane="hook"))
    meta = read_spool_meta(root, sid)
    assert "client_captured_at" not in meta
    assert "client_captured_at_source" not in meta
