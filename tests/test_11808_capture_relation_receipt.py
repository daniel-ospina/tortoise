"""#11808 — the hosted capture receipt carries the CONNECTION half of a capture.

The defect (measured at ``a7da06cc77``): ``grep -c relation_write
tortoise/hosted_api.py`` → **0**, while the SDK receipt returns it
(``sdk.py:16093-16094``) from the relation link's own accounting
(``relation_write.py`` → ``{written, dropped, folded, …}``). The hosted lane
builds its own receipt and never calls ``sdk.capture_session``, so the one
number that says whether a stated connection LANDED was dropped on the door the
fleet's agents actually use. From the client, "wrote 40 claims, 0 connections"
and "wrote 40 claims, 12 connections" were the same answer.

Two things are pinned here, and they are different contracts:

  * the KEY — ``relation_write`` on the hosted receipt, **absent (never
    zeroed)** when the extractor stated no relations, exactly as the SDK
    receipt documents it (#11237);
  * the ADVISORY — one line when the session named ≥2 entities and no
    connection between them was written. It states the observation and refuses
    the attribution, because no observable here separates "the agent stated no
    relation" from "the extractor produced none". A nudge, never a refusal
    (``product/2026-08-11-tooling-surface-consolidation.md:22``).

NOT PINNED HERE, declared: this file pins the HOSTED half only. The SDK half IS
covered elsewhere — `tests/eval/write_path/test_write_path_relation_lane.py`
reads `cap["relation_write"]["written"]` off `_capture_session`, so a renamed or
dropped key KeyErrors there — but that is an on-demand eval lane (#10194), not
selected by a source-only change, so a break would land green and red later.
Nothing pins that the two lanes agree on the same shape.

Class-B doctrine — each test states the value/state that makes it fail, and
that the state is reachable in its fixture.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from types import SimpleNamespace

import pytest

from tortoise import hosted_api as ha

CONV = [
    {"role": "user", "content": "the auth dead-end is the top issue."},
    {"role": "assistant", "content": "agreed"},
]


def _meta(entities: int, relation_write: dict | None) -> dict:
    """A capture meta shaped like ``_extract_session_v2``'s return (#9116)."""
    meta: dict = {
        "errors": [],
        "warnings": [],
        "mode": "v2",
        "stats": {"step_witness": {"commit": {"entities": entities}}},
    }
    if relation_write is not None:
        meta["relation_write"] = relation_write
    return meta


# ── the advisory: fires on ONE state, silently on the rest ─────────────────

def test_the_advisory_fires_when_entities_named_and_nothing_written():
    """FAILS while the receipt says nothing about a session that named three
    entities and connected none — the exact shape the owner's 2026-11-04
    direction is about. Reachable: the relation link reports written 0."""
    line = ha._capture_connection_warning(
        {"written": 0, "dropped": 2, "folded": 0}, 3)
    assert line is not None
    assert "named 3 entities" in line
    assert "0 written" in line and "2 dropped" in line
    assert "#11808" in line


def test_the_advisory_fires_on_the_silent_case_too():
    """The case with the MOST value: nothing was stated at all, so the link
    emits no per-edge warning and the capture is otherwise silent. FAILS if the
    advisory only fires for a stated-but-failed relation."""
    line = ha._capture_connection_warning(None, 4)
    assert line is not None
    assert "no relation was stated" in line


@pytest.mark.parametrize("entities,relation_write", [
    (1, None),                                 # nothing to join
    (0, {"written": 0, "dropped": 1, "folded": 0}),   # no entity pass ran
    (3, {"written": 2, "dropped": 0, "folded": 0}),   # connections landed
])
def test_the_advisory_is_silent_where_it_must_be(entities, relation_write):
    """FAILS if the advisory becomes blanket \"no edges\" noise — a session with
    one entity, a branch that named nothing, or a session that DID connect."""
    assert ha._capture_connection_warning(relation_write, entities) is None


def test_the_advisory_names_extractor_drops_rather_than_claiming_silence():
    """FAILS if an absent ``relation_write`` is always described as "no relation
    was stated" — ``out["relations"]`` is the SURVIVING list, so a total
    validation drop leaves the key absent exactly like silence does
    (``extractor_v2.py:11771-11772``), and the same receipt then reports
    ``stats["relations_dropped"] > 0`` next to a line saying nothing was
    stated. Two lines of one receipt contradicting each other."""
    line = ha._capture_connection_warning(None, 3, relations_dropped=2)
    assert line is not None
    assert "2 stated relation(s) were dropped before the write link ran" in line
    assert "no relation was stated" not in line


def test_the_advisory_never_claims_silence_when_the_link_did_not_report():
    """FAILS if an absent ``relation_write`` with ZERO drops is described as
    "no relation was stated": that is exactly the defensive ``except`` arm
    (``sdk.py:17388-17395``), where the leg's own warning says the stated
    relations stay uncommitted and this line would contradict it on the same
    receipt. ``stats["relations"]`` is the discriminator, and the raise path is
    ``# pragma: no cover``, so this helper is the only place it can be pinned."""
    line = ha._capture_connection_warning(None, 3, 0, relations_stated=3)
    assert line is not None
    assert "3 relation(s) were stated" in line
    assert "no relation was stated" not in line
    assert ha._capture_connection_warning(None, 3, 0, 0) is not None


def test_the_counts_never_raise_at_receipt_assembly():
    """FAILS if a count is coerced with a bare ``int()``: the advisory is
    appended AFTER the capture committed, so an unexpected shape must degrade
    to a COUNT of 0, not 500 a write that already landed. For ``written`` that
    makes the line FIRE (and report 0) rather than fall silent — the honest
    reading of a count this receipt could not interpret."""
    assert ha._capture_int([1, 2]) == 0
    assert ha._capture_int("not-a-number") == 0
    assert ha._capture_int(None) == 0
    assert ha._capture_int(-3) == 0
    line = ha._capture_connection_warning({"written": [1, 2]}, 3)
    assert line is not None
    # The key exists only when the link ran, so this must NOT read as silence.
    assert "no relation was stated" not in line
    assert "no usable counts" in line


def test_the_step_witness_entity_count_is_read_not_rederived():
    """FAILS if the count is re-derived from the receipt instead of asked of the
    run's own witness (#9116). Both the value and its absent shape are pinned."""
    assert ha._capture_step_entities(_meta(3, None)) == 3
    assert ha._capture_step_entities({"stats": {}}) == 0
    assert ha._capture_step_entities({"stats": {"step_witness": {}}}) == 0
    assert ha._capture_step_entities({}) == 0


# ── the receipt itself: driven through the REAL hosted impl ────────────────

@pytest.fixture
def _mock_extractor(monkeypatch):
    # The offline v2 seam — no provider key / network.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture
def _org():
    org_id = f"test-11808-{uuid.uuid4().hex[:10]}"
    org = {"org_id": org_id, "graph_id": None, "key_id": None, "tier": "free",
           "scopes": [], "legacy_full_access": True, "max_points": 100000,
           "max_sessions": None}
    yield SimpleNamespace(org_id=org_id, org=org)
    with contextlib.suppress(Exception):
        _drop = ha._make_sdk(namespace=org_id)
        _drop._get_proj().g.query("MATCH (n) DETACH DELETE n")
        _drop.close()


def _drive(monkeypatch, canned_meta: dict, session_id: str, org: dict) -> dict:
    """Run the hosted capture impl with a canned extraction result.

    ``_run_capture_bounded`` is the ONE seam between the receipt assembly and
    the extractor (``grep -n _run_capture_bounded hosted_api.py``), so the
    receipt's key set is exercised by the real code path, not a re-implementation.
    """
    async def _fake(slot, fn, /, *args, **kwargs):
        # The impl reads the MINTED subset off this list (#8626), so each
        # entry is a point record with an id — a bare string is not.
        return ([{"id": f"11808-{i}", "content": "c"} for i in range(2)],
                canned_meta)

    monkeypatch.setattr(ha, "_run_capture_bounded", _fake)
    body = ha.SessionRequest(conversation=CONV, session_id=session_id,
                             harness="claude")
    slot = ha._reserve_capture_slot(ha._capture_session_key(org, session_id))
    try:
        return asyncio.run(ha._capture_session_impl(body, None, org, slot=slot))
    finally:
        slot.release()


def test_the_hosted_receipt_carries_the_relation_accounting(
        monkeypatch, _mock_extractor, _org):
    """FAILS on the pre-fix state, where the key is absent: the accounting is
    computed upstream and dropped here (``grep -c relation_write`` → 0)."""
    org_dict = _org.org
    rw = {"written": 0, "dropped": 1, "folded": 0, "edges": [], "drops": [],
          "collapsed": [], "write_paths": {}}
    receipt = _drive(monkeypatch, _meta(3, rw),
                     f"s-11808-{uuid.uuid4().hex[:8]}", org_dict)
    assert receipt.get("relation_write") == rw, (
        "the hosted receipt dropped the relation accounting the SDK receipt "
        f"carries — a caller cannot see its connections: {sorted(receipt)}")
    assert any("named 3 entities and no connection between them was written" in w
               for w in receipt["warnings"]), receipt["warnings"]


def test_the_hosted_receipt_omits_the_key_when_nothing_was_stated(
        monkeypatch, _mock_extractor, _org):
    """FAILS if the key is zeroed instead of omitted: ``{written: 0}`` and
    \"the extractor stated no relations\" would read as one state (#11237's
    contract, which this lane now matches)."""
    receipt = _drive(monkeypatch, _meta(1, None),
                     f"s-11808-{uuid.uuid4().hex[:8]}", _org.org)
    assert "relation_write" not in receipt, receipt.get("relation_write")
    assert not any("#11808" in w for w in receipt["warnings"]), receipt["warnings"]
