"""#3998 — the absent-raw state is a FIRST-CLASS VALUE (D30 / #3919).

**Class B — mechanical architecture conformance.** The contract is already
decided, so these tests can be written first and fast. For every mechanical
test the two doctrine questions are answered in the test's own docstring:

    (1) what value makes this test fail?
    (2) does the fixture contain a row where that value is reachable?

The contract under test (issue #3998, verbatim):

    (1) a memory and its provenance are readable AND **correctly labelled**
        under **each of the three absent causes**;
    (2) **no read path raises** on an unreachable raw — the absent state is a
        **value, not an exception**;
    (3) the graph holds an **index entry, not a copy**, of the raw;
    (4) **0 raw payload bytes** retained in the graph for a hosted-elsewhere
        mode.

⛔ THE TRAP THIS FILE IS BUILT AROUND — the contract names **three** causes.
A suite that only ever exercises *"deleted"* looks green while two thirds of
the contract is unverified. So every cause-driven assertion here is driven by
``RAW_ABSENT_STATES`` — the vocabulary itself — and a test asserts the three
are pairwise **distinguishable**, not merely non-null. ``assert provenance is
not None`` is not a test of "correctly labelled".

⚠️ NOT COVERED (stated plainly, per the doctrine: silence is not acceptable):
  - **lazy detection** — nothing here probes a host or a credential to
    DISCOVER an absence. These tests cover the *recorded* state; the
    detection policy (issue decision #2, "who sets it, and when") is
    deliberately left to the caller, and is **not** implemented here.
  - **retention** — R1 gives no default retention window, so there is nothing
    to test; a test asserting "no window exists" would be decorative.
  - the search-hit provenance block (``search_engine.SearchHit.to_dict``) —
    it does not yet carry the raw state; that read path is **unmodified**.

DB-lane (live FalkorDB under ``TORTOISE_DB_URI``) and embedded-safe.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.raw_state import (
    RAW_ABSENT_STATES,
    RAW_ACCESS_REVOKED,
    RAW_DELETED,
    RAW_OFFLINE,
    RAW_PRESENT,
    RAW_STATE_PROP,
    RAW_UNRECOGNISED,
    WRITABLE_RAW_STATES,
    RawState,
    is_valid_raw_state,
    raw_availability,
    raw_entry,
    validate_raw_state,
)
from tortoise.sdk import TortoiseSDK

RAW_URL = "https://raw.example.com/conv/7"


# ── fixtures ──────────────────────────────────────────────────────────────


def _ns() -> str | None:
    """Per-test server graph name under a URI (the #5012 fixture seam)."""
    return f"test_suite_{os.urandom(4).hex()}" if os.environ.get("TORTOISE_DB_URI") else None


@pytest.fixture
def sdk(tmp_path):
    """(sdk, events_dir) — journal wired, so rebuild is exercisable."""
    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(os.path.join(str(tmp_path), "raw3998.db"),
                    namespace=_ns(),
                    event_log_path=str(events / "events.jsonl"))
    yield s, events
    s.close()


def _source_props(sdk, url: str = RAW_URL) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN properties(s)", params={"u": url}
    ).result_set
    assert rows, f"no :Source for {url}"
    return dict(rows[0][0])


def _memory(sdk, content: str = "a claim whose raw may be gone"):
    """A Point with a real ``extractedFrom`` edge to the raw's Source."""
    p = sdk.create_point("statement", content, extractedFrom=RAW_URL)
    return p["id"]


# ══════════════════════════════════════════════════════════════════════════
# 1. The RESOLVER — pure, never raises, never fails open
# ══════════════════════════════════════════════════════════════════════════


def test_three_causes_are_pairwise_distinguishable():
    """The whole deliverable: the three causes must be TELLABLE APART.

    ``deleted`` is permanent, ``offline`` is temporary, ``access revoked`` is a
    permissions fact. If they collapse to one "missing", the product says
    something false while every not-null assertion still passes.

    (1) FAILS when any two causes share a state, a label, a message, or a
        (permanent, retryable) pair — i.e. when a collapsed implementation
        returns "missing" for all three.
    (2) REACHABLE: all three constants exist, so the fixture holds a row for
        every cause by construction.
    """
    seen = {c: raw_availability({RAW_STATE_PROP: c}) for c in RAW_ABSENT_STATES}
    assert set(seen) == set(RAW_ABSENT_STATES) == {
        RAW_DELETED, RAW_OFFLINE, RAW_ACCESS_REVOKED,
    }
    assert len({s.state for s in seen.values()}) == 3, "states collapsed"
    assert len({s.label for s in seen.values()}) == 3, "labels collapsed"
    assert len({s.message for s in seen.values()}) == 3, "messages collapsed"
    # The load-bearing distinction: permanence. A caller deciding whether to
    # retry reads THIS, and `deleted` must never be retryable.
    assert seen[RAW_DELETED].permanent is True
    assert seen[RAW_OFFLINE].permanent is False
    assert seen[RAW_ACCESS_REVOKED].permanent is False
    assert seen[RAW_OFFLINE].retryable is True
    assert seen[RAW_DELETED].retryable is False, "a deleted raw must not invite a retry"
    assert seen[RAW_ACCESS_REVOKED].retryable is False
    assert all(s.absent for s in seen.values())
    # And none of the three is the present state.
    assert RAW_PRESENT not in set(seen)


def test_no_recorded_state_reads_as_present():
    """The default is the SHAPE OF THE RECORD, not a stored string.

    (1) FAILS if a source with no recorded state resolves absent, or if
        ``None``/``{}`` raise.
    (2) REACHABLE: ``{}`` is the exact shape every pre-#3998 :Source has —
        the whole existing corpus.
    """
    for empty in ({}, {RAW_STATE_PROP: None}, {RAW_STATE_PROP: ""}, None, ""):
        r = raw_availability(empty)
        assert r.state == RAW_PRESENT, empty
        assert r.absent is False
        assert r.permanent is False
    # A mapping that simply does not carry the key.
    assert raw_availability({"url": RAW_URL, "contentHash": "abc"}).state == RAW_PRESENT


def test_unrecognised_recorded_state_does_not_read_as_present():
    """ANTI-FAIL-OPEN guard. A recorded state we cannot interpret must not be
    reported as reachable.

    (1) FAILS for the natural-but-wrong ``d.get(key, PRESENT)``: a garbage
        value then reads as present, asserting a raw is reachable when the row
        plainly records that it is not.
    (2) REACHABLE: the value ``'banana'`` is constructed here, and a :Source
        written by a FUTURE build (or a hand-edited node) has exactly this
        shape.
    """
    for bad in ({"rawState": "banana"}, {"rawState": "DELETED"}, {"rawState": 7},
                {"rawState": ["deleted"]}, "banana", 7, [1, 2], object()):
        r = raw_availability(bad)
        assert r.state == RAW_UNRECOGNISED, f"{bad!r} -> {r.state}"
        assert r.absent is True, f"{bad!r} read as reachable"


def test_resolver_never_raises_on_hostile_input():
    """Criterion (2) at the seam: the state is a VALUE, not an exception.

    (1) FAILS if the resolver propagates — e.g. ``props.get`` on a mapping
        whose ``get`` raises, or attribute access on ``object()``.
    (2) REACHABLE: ``Boom`` below raises from ``get``; it is constructed in
        the fixture.
    """
    class Boom(dict):
        def get(self, *_a, **_k):
            raise RuntimeError("hostile mapping")

    for hostile in (Boom(), object(), [1, 2], 3.5, {"rawState": Boom()}, b"deleted"):
        out = raw_availability(hostile)
        assert isinstance(out, RawState), hostile
        assert out.state in (RAW_PRESENT, RAW_UNRECOGNISED), hostile


def test_write_side_is_strict_on_every_invalid_value():
    """The mirror of the fail-closed read: a WRITER that passes a value outside
    the vocabulary is a caller bug, and is refused loudly — silently dropping a
    recorded absence is the exact failure #3998 closes.

    (1) FAILS if ``validate_raw_state`` is permissive (returns the value, or
        coerces it) — then the `pytest.raises` never fires.
    (2) REACHABLE: ``'banana'`` is passed below and must not be dropped.
    """
    for good in WRITABLE_RAW_STATES:
        assert validate_raw_state(good) == good
        assert is_valid_raw_state(good)
    for bad in ("banana", "", None, 0, "missing", "unrecognised"):
        # `unrecognised` is READ-side only: a writer must never record it.
        assert not is_valid_raw_state(bad), bad
        with pytest.raises(ValueError):
            validate_raw_state(bad)


# ══════════════════════════════════════════════════════════════════════════
# 2. Criteria (1) + (2) — the memory and its provenance, per cause
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("cause", RAW_ABSENT_STATES)
def test_memory_and_provenance_are_readable_and_labelled(sdk, cause):
    """Criterion (1), driven by the vocabulary so all three causes run.

    (1) FAILS on a pre-#3998 build for the only reason that matters: the
        returned provenance carries NO raw state at all, so ``raw_state``
        cannot equal the cause. It also fails if two causes report the same
        value, or if the read raises/gives an empty chain.
    (2) REACHABLE: the fixture writes the cause onto the Source and reads the
        SAME memory back through the real ``extractedFrom`` edge.
    """
    s, _events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    pid = _memory(s)
    s.create_source(RAW_URL, "conversation", raw_state=cause)

    # The memory itself stays readable — absence is not corruption.
    assert s.get_point(pid)["content"] == "a claim whose raw may be gone"

    chain = s.get_provenance_chain(pid)
    assert len(chain) == 1, f"provenance went silent: {chain}"
    raw = chain[0]["raw"]
    assert raw["raw_state"] == cause
    assert raw["available"] is False
    assert raw["source_id"] == RAW_URL
    assert raw["content_hash"] == "h1", "the version anchor must survive absence"
    assert raw["label"] and raw["message"], "a cause must be SAYABLE"

    # And the causes are distinguishable THROUGH THE READ PATH, not just in
    # the pure resolver — this is the assertion the trap is about.
    others = [c for c in RAW_ABSENT_STATES if c != cause]
    assert all(raw["raw_state"] != o for o in others)
    assert raw["permanent"] == (cause == RAW_DELETED)


def test_reads_do_not_raise_when_the_raw_is_unreachable(sdk):
    """Criterion (2) end to end: every read path returns a VALUE.

    (1) FAILS if any of these raises for an absent raw, or returns an ``error``
        payload instead of the state.
    (2) REACHABLE: each cause is written and read below; the sourceless point
        is created by ``create_point`` with no ``extractedFrom`` and no
        session context.
    """
    s, _events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    pid = _memory(s)

    for cause in RAW_ABSENT_STATES:
        s.create_source(RAW_URL, "conversation", raw_state=cause)
        chain = s.get_provenance_chain(pid)          # must not raise
        prov = s.provenance(pid)                     # must not raise
        assert chain[0]["raw"]["raw_state"] == cause
        assert prov["raw"]["raw_state"] == cause
        assert "error" not in prov

    # A point with NO source at all: still no raise, and no raw key invented.
    orphan = s.create_point("statement", "orphan claim")["id"]
    assert s.get_provenance_chain(orphan) == []
    assert "raw" not in s.provenance(orphan)


def test_absence_is_preserved_by_a_later_upsert_that_carries_none(sdk):
    """The contentHash anchor rule, applied to availability: a re-upsert that
    carries NO state must not resurrect a raw that was deleted between the two
    writes.

    (1) FAILS for the naive ``SET s.rawState = $rawState`` on MATCH: a null
        clears the property, and the deletion is silently undone — the exact
        silent-loss failure #3998 exists to prevent.
    (2) REACHABLE: the second ``create_source`` below carries no ``raw_state``,
        which is the shape of every ordinary re-ingest.
    """
    s, _events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    s.create_source(RAW_URL, "conversation", raw_state=RAW_DELETED)
    assert _source_props(s)[RAW_STATE_PROP] == RAW_DELETED

    s.create_source(RAW_URL, "conversation", contentHash="h1")  # no raw_state
    props = _source_props(s)
    assert props[RAW_STATE_PROP] == RAW_DELETED, "absence was wiped by a later write"
    assert props["contentHash"] == "h1"
    assert props["version"] == 1, "an anchorless re-upsert must not bump the version"


def test_present_is_the_documented_way_back(sdk):
    """A raw that returns must be sayable as returned — otherwise the state is
    write-only and the product can never un-say an absence.

    (1) FAILS if a writer cannot move the state back to present.
    (2) REACHABLE: ``raw_state='present'`` below is one of WRITABLE_RAW_STATES.
    """
    s, _events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1", raw_state=RAW_OFFLINE)
    assert _source_props(s)[RAW_STATE_PROP] == RAW_OFFLINE
    s.create_source(RAW_URL, "conversation", raw_state=RAW_PRESENT)
    props = _source_props(s)
    assert props[RAW_STATE_PROP] == RAW_PRESENT
    assert raw_availability(props).absent is False


def test_props_passthrough_cannot_set_or_clear_the_state(sdk):
    """The state is SERVER-MANAGED: the node-property spellings must not be
    settable through the open props passthrough, or a payload could silently
    overwrite (or clear) a recorded absence.

    ⚠️ ``raw_state=`` IS accepted — it is the SANCTIONED keyword route, not a
    loophole, and it is how a raw that came back is marked present (covered by
    ``test_present_is_the_documented_way_back``). What must be refused is the
    pass-through spelling, which is unvalidated and unversioned.

    (1) FAILS if ``rawState`` / ``rawStateAt`` arrive as plain payload keys.
    (2) REACHABLE: both spellings are passed below, and the stored absence is
        re-read after the refusals.
    """
    s, _events = sdk
    s.create_source(RAW_URL, "conversation", raw_state=RAW_DELETED)
    for key in ("rawState", "rawStateAt"):
        with pytest.raises(ValueError):
            s.create_source(RAW_URL, "conversation", **{key: RAW_PRESENT})
    assert _source_props(s)[RAW_STATE_PROP] == RAW_DELETED, "props cleared the absence"


# ══════════════════════════════════════════════════════════════════════════
# 3. Criteria (3) + (4) — the INDEX ENTRY, and zero payload bytes
# ══════════════════════════════════════════════════════════════════════════


def test_graph_holds_an_index_entry_not_a_copy(sdk):
    """Criterion (3) and (4) together: the graph keeps identity + version +
    availability, and **0 raw payload bytes**, for a raw hosted elsewhere.

    (1) FAILS if any property value on the :Source carries the raw body — the
        moment someone "helpfully" starts storing the fetch result on the node
        (the `:Document.content` mistake D10 removes), or if the node is
        missing one of the three values.
    (2) REACHABLE: a 2 KB body is constructed and ONLY its sha256 is passed —
        the realistic hosted-elsewhere shape, where the client fetched the raw
        and sent us the hash.
    """
    s, _events = sdk
    body = ("MEETING TRANSCRIPT — " + "the raw conversation body. " * 80).strip()
    assert len(body) > 2000
    digest = hashlib.sha256(body.encode()).hexdigest()

    s.create_source(RAW_URL, "conversation", contentHash=digest, raw_state=RAW_OFFLINE,
                    title="Weekly sync")

    props = _source_props(s)
    # (3) the index entry is exactly identity + version + availability
    assert props["url"] == RAW_URL
    assert props["contentHash"] == digest
    assert props[RAW_STATE_PROP] == RAW_OFFLINE
    # (4) and NOT the bytes
    for key, value in props.items():
        text = value if isinstance(value, str) else str(value)
        assert body not in text, f"{key} carries the raw payload"
        assert "the raw conversation body." not in text, f"{key} carries raw payload fragments"
    assert not any(k.lower() == "content" for k in props), sorted(props)


def test_index_entry_shape_carries_no_payload_field():
    """The entry is a REFERENCE by construction — there is no field a raw's
    bytes could be written into.

    (1) FAILS if the entry grows a payload-bearing key (``content``, ``body``,
        ``text``, ``raw``), which is how a reference turns back into a copy.
    (2) REACHABLE: the entry is built from a props dict that DOES contain the
        source's identity and version, so every legitimate key is present and
        the assertion is not vacuously true over an empty dict.
    """
    entry = raw_entry({"url": RAW_URL, "contentHash": "abc", RAW_STATE_PROP: RAW_DELETED})
    assert set(entry) == {
        "source_id", "content_hash", "raw_state", "raw_state_at",
        "available", "permanent", "retryable", "label", "message",
    }, sorted(entry)
    assert entry["source_id"] == RAW_URL and entry["content_hash"] == "abc"
    assert entry["available"] is False and entry["permanent"] is True


def test_absence_survives_a_rebuild(sdk):
    """The issue's own checklist: an absence recorded before a rebuild is still
    recorded after — the state rides the journal (``SourceCreated``) like every
    other source fact.

    (1) FAILS if the state is written only as a node property and never
        journalled: ``rebuild_all`` wipes the derived graph and replays, and
        the absence is lost.
    (2) REACHABLE: the journal is wired in the fixture and the absence is
        recorded BEFORE the rebuild.
    """
    s, events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    s.create_source(RAW_URL, "conversation", raw_state=RAW_DELETED)
    assert _source_props(s)[RAW_STATE_PROP] == RAW_DELETED

    s._get_proj().rebuild_all(str(events))

    props = _source_props(s)
    assert props[RAW_STATE_PROP] == RAW_DELETED, "the absence did not survive rebuild"
    assert props["contentHash"] == "h1", "the version anchor did not survive rebuild"
    assert raw_availability(props).permanent is True
