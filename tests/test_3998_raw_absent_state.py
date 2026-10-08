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
  - **the search-hit provenance block** (``search_engine.SearchHit.to_dict``) —
    it does not yet carry the raw state; that read path is **unmodified**.
  - ⚠️ **VALUE-level payload bounds — the honest residual of criterion (4).**
    The declared surface is CLOSED: no undeclared key, and so no spelling of a
    raw payload that nobody enumerated, can reach a ``:Source`` on any write
    path including journal replay. It does NOT bound the *values* of the
    declared metadata fields, so a caller that deliberately writes a body into
    ``title`` still stores it (pinned by
    ``test_declared_metadata_values_are_not_length_bounded_the_stated_residual``).
    A value policy for short metadata is a product decision this lane did not
    take. So criterion (4) is established at the **surface** level, not the
    **value** level, and that distinction is stated rather than blurred.

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

#: The DECLARED ``:Source`` node-property surface (#3998, ``_SOURCE_EXTRA_PROPS``
#: + the fixed SET clauses). Written out HERE, explicitly, rather than derived
#: from the code: deriving it would let a new payload-carrying key enter the
#: allowlist and this test at the same instant, which is the failure the test
#: exists to catch.
ALLOWED_SOURCE_NODE_PROPS = frozenset({
    # identity
    "url", "id", "canonicalUrl", "urlAliases", "sourceKind", "externalId",
    # version
    "contentHash", "version", "ingestedAt", "updatedAt", "sourceDate",
    # availability (#3998 — the third value on the record)
    "rawState", "rawStateAt",
    # the transient index-merge token (`_index_source_merge`)
    "__runId",
    # declared metadata
    "title", "format", "name", "team", "credibilityTier", "is_episodic",
    "sourcePath", "_searchText", "provenance_spans",
    # the session-capture writer (sdk._materialize_session_source)
    "sessionId", "capturedAt", "summary", "topics", "eventId",
    # the reliability cache (sdk.get_source_reliability) — an in-tree producer
    "reliability", "reliabilityComponents", "reliability_derived_at",
})


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


def _raw(sdk, url: str = RAW_URL) -> dict:
    """The ``raw`` block as a READER sees it — through ``get_provenance_chain``,
    never by touching the node, so these assertions cannot pass by reading state
    the read path would not surface."""
    chain = sdk.get_provenance_chain(_memory(sdk))
    assert chain, "no provenance row — the read went silent"
    return chain[0]["raw"]


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
    # The flag a caller actually branches on, and a genuine cross-check
    # against the present state. (`RAW_PRESENT not in seen` would be trivially
    # true — `seen` is keyed BY the causes — which is a test that cannot
    # fail; this asserts the distinction instead.)
    present = raw_availability(None)
    assert present.absent is False
    assert all(s.absent is True for s in seen.values())
    assert present.state not in {s.state for s in seen.values()}
    assert present.label not in {s.label for s in seen.values()}


def test_no_recorded_state_reads_as_present():
    """The default is the SHAPE OF THE RECORD, not a stored string.

    ⚠️ ``""`` is deliberately NOT in this set — see
    ``test_empty_string_is_a_recorded_value_not_the_absence_of_one``.

    (1) FAILS if a source with no recorded state resolves absent, or if
        ``None``/``{}`` raise.
    (2) REACHABLE: ``{}`` is the exact shape every pre-#3998 :Source has —
        the whole existing corpus.
    """
    for empty in ({}, {RAW_STATE_PROP: None}, None):
        r = raw_availability(empty)
        assert r.state == RAW_PRESENT, empty
        assert r.absent is False
        assert r.permanent is False
    # A mapping that simply does not carry the key.
    assert raw_availability({"url": RAW_URL, "contentHash": "abc"}).state == RAW_PRESENT


def test_empty_string_is_a_recorded_value_not_the_absence_of_one():
    """``""`` is a value SOMEONE WROTE, so it is not "nothing recorded", and
    we cannot tell what it says — so it must not read as reachable.

    (1) FAILS for ``if value is None or value == "": return PRESENT`` (the
        first form of this code): an empty stored state then asserts the raw
        is reachable, contradicting the module's own fail-closed guarantee.
        The graph CAN hold ``""`` — unlike null, which Cypher removes.
    (2) REACHABLE: both spellings are constructed below.
    """
    assert raw_availability({"rawState": ""}).state == RAW_UNRECOGNISED
    assert raw_availability({"rawState": ""}).absent is True
    assert raw_availability("").state == RAW_UNRECOGNISED


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
    # the pure resolver — this is the assertion the trap is about. Compared
    # against the resolved state for THIS cause, so a generic "missing" label
    # (the collapse the contract forbids) fails rather than passes.
    expected = raw_availability({RAW_STATE_PROP: cause})
    assert raw["label"] == expected.label
    assert raw["message"] == expected.message
    assert raw["permanent"] == (cause == RAW_DELETED)
    assert raw["retryable"] == (cause == RAW_OFFLINE)


def test_the_three_causes_are_distinguishable_through_the_read_path(sdk):
    """The whole deliverable, asserted on the READ path rather than the pure
    resolver: read the SAME memory back under each cause and require the three
    reports to differ. A not-null check would pass for a collapsed "missing".

    (1) FAILS if any two causes report the same state, label, message, or
        ``(permanent, retryable)`` pair through the provenance read.
    (2) REACHABLE: all three causes are written to the live Source and read
        back through the real ``extractedFrom`` edge.
    """
    s, _events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    pid = _memory(s)
    seen = {}
    for cause in RAW_ABSENT_STATES:
        s.create_source(RAW_URL, "conversation", raw_state=cause)
        seen[cause] = s.get_provenance_chain(pid)[0]["raw"]
    assert len({r["raw_state"] for r in seen.values()}) == 3
    assert len({r["label"] for r in seen.values()}) == 3
    assert len({r["message"] for r in seen.values()}) == 3
    assert len({(r["permanent"], r["retryable"]) for r in seen.values()}) == 3


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


def test_chain_keeps_a_stable_key_set_when_the_source_has_no_references_edge(sdk):
    """The other half of the silence fix: a source with no ``:references``
    edge must still report its provenance, with the SAME key set, so a caller
    that iterates a non-empty chain cannot ``KeyError`` on the shape that used
    to return ``[]``.

    (1) FAILS if the entity half is OMITTED rather than present — a consumer
        reading ``item["entity"]`` on a chain it used to receive as ``[]``
        now raises KeyError.
    (2) REACHABLE: no ``references`` edge is wired below — the shape of a raw
        indexed before extraction.
    """
    s, _events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1", raw_state=RAW_OFFLINE)
    pid = _memory(s)
    chain = s.get_provenance_chain(pid)
    assert len(chain) == 1, "a source with no entity must not silence the chain"
    assert set(chain[0]) >= {"source", "raw", "entity", "labels"}
    # The entity half keeps MAIN's OPTIONAL + coalesce semantics (D10): with no
    # ``:references`` edge the coalesce falls back to the source itself, which
    # IS the terminal provenance, so ``entity`` is the source — filtered to the
    # declared surface, so the fallback cannot re-hand a raw payload. The STABLE
    # KEY SET asserted just above is what this test is about; ``entity is None``
    # was the branch's implementation choice, and pinning it here would
    # contradict
    # ``test_references_edge.py::test_provenance_chain_returns_document_without_source_url``.
    assert chain[0]["entity"]["url"] == RAW_URL
    assert "Source" in chain[0]["labels"]
    assert "content" not in chain[0]["entity"], (
        "the coalesce fallback re-handed the raw payload"
    )
    assert chain[0]["raw"]["raw_state"] == RAW_OFFLINE


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

    ⛔ This test was DECORATIVE in its first form and was WRONG in its second,
    and both failures are the point of it.

    *Form 1* asserted the payload was absent while never handing a payload to
    the code, so it held for every implementation. *Form 2* enumerated the six
    spellings the SDK blocklist knew — i.e. it tested the implementation's own
    list, so it passed for any spelling nobody had thought of. Measured on the
    open passthrough: ``text``, ``snippet``, ``excerpt``, ``transcript``,
    ``bodyText``, ``rawText``, ``raw_text``, ``data``, ``blob``, ``bytes``,
    ``payloadText``, ``contentText``, ``content_text``, ``fullText``,
    ``full_text`` and a list-valued ``chunks`` ALL persisted the 2 KB body
    verbatim. A name blocklist cannot hold this.

    (1) FAILS if the closed :Source property surface
        (``_SOURCE_EXTRA_PROPS``) is removed — then every spelling in
        ``PAYLOAD_SPELLINGS`` lands on the node; or if ``ALLOWED_SOURCE_NODE_PROPS``
        stops being an accurate description of the declared surface.
    (2) REACHABLE: a >2 KB body is handed to the code under EVERY spelling
        below, through the real write path.
    """
    s, _events = sdk
    body = ("MEETING TRANSCRIPT — " + "the raw conversation body. " * 80).strip()
    assert len(body) > 2000
    digest = hashlib.sha256(body.encode()).hexdigest()

    # (a) the common spellings get a LOUD, actionable error.
    for key in ("content", "body", "raw", "payload", "raw_content", "rawContent"):
        with pytest.raises(ValueError):
            s.create_source(RAW_URL, "conversation", contentHash=digest, **{key: body})

    # (b) EVERY OTHER spelling is denied one layer down, by the closed surface.
    #     This is the half that does not depend on anyone having thought of the
    #     name — which is the only form the guarantee can take.
    for key in ("text", "snippet", "excerpt", "transcript", "bodyText",
                "rawText", "raw_text", "data", "blob", "bytes", "payloadText",
                "contentText", "content_text", "fullText", "full_text"):
        s.create_source(RAW_URL, "conversation", contentHash=digest, **{key: body})
    s.create_source(RAW_URL, "conversation", contentHash=digest, chunks=[body])

    # (c) the sanctioned route: identity + version + availability, no bytes.
    s.create_source(RAW_URL, "conversation", contentHash=digest, raw_state=RAW_OFFLINE,
                    title="Weekly sync", format="transcript")

    props = _source_props(s)
    assert props["url"] == RAW_URL
    assert props["contentHash"] == digest
    assert props[RAW_STATE_PROP] == RAW_OFFLINE
    assert props["format"] == "transcript", "a declared metadata prop must still persist"
    assert set(props) <= ALLOWED_SOURCE_NODE_PROPS, (
        "undeclared :Source properties reached the node: "
        f"{sorted(set(props) - ALLOWED_SOURCE_NODE_PROPS)}"
    )
    for key, value in props.items():
        assert body not in repr(value), f"{key} carries the raw payload"
        assert "the raw conversation body." not in repr(value), f"{key} carries fragments"


def test_the_raw_payload_guard_is_not_vacuous(sdk):
    """Contrast that keeps the guard above load-bearing: the refusal is the
    SDK route's own work (its message names the reason), and the refused call
    created nothing at all.

    (1) FAILS if the SDK guard is removed: the ``pytest.raises`` never fires.
    (2) REACHABLE: ``content`` is one of the six refused spellings.
    """
    s, _events = sdk
    with pytest.raises(ValueError, match="raw payload"):
        s.create_source(RAW_URL, "conversation", content="body bytes")
    assert not s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN count(s)", params={"u": RAW_URL}
    ).result_set[0][0]


def test_rebuild_does_not_re_materialise_a_payload_from_the_journal(sdk):
    """Review round 2, P1: the guard must hold on the REPLAY path, not only at
    write time. ``rebuild_all`` re-materialises ``SourceCreated`` through the
    same ``_upsert_source``, so a journal written while the passthrough was
    open would otherwise restore its payload on every rebuild — the documented
    recovery path re-creating the exact bytes AC4 forbids.

    (1) FAILS if the allowlist is enforced only in ``create_source``: the
        replayed event carries the payload straight past a write-time guard.
    (2) REACHABLE: the event below is emitted with the PRE-GUARD writer's exact
        shape (``SourceCreated`` carrying raw ``content``/``text``), which is
        what every deployment that ran the shipped writer holds in its journal.
    """
    s, events = sdk
    body = "PRE-GUARD PAYLOAD " + ("raw transcript body. " * 120)
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    s._emit_event("SourceCreated", id=RAW_URL, url=RAW_URL, sourceKind="conversation",
                  contentHash="h1", content=body, text=body)

    s._get_proj().rebuild_all(str(events), confirm_destructive=True)

    props = _source_props(s)
    assert "content" not in props and "text" not in props, sorted(props)
    assert body not in repr(props), "the rebuild restored the raw payload"
    assert set(props) <= ALLOWED_SOURCE_NODE_PROPS, (
        f"undeclared props survived replay: {sorted(set(props) - ALLOWED_SOURCE_NODE_PROPS)}"
    )


def test_the_generic_entity_route_cannot_reach_a_source_with_a_payload(sdk):
    """Review round 3, P1: the closed surface was enforced only in
    `_upsert_source`, leaving the DOCUMENTED tenant route open. `update_entity`
    writes caller props with a live `SET n += $p` on every canonical label
    including `:Source`, and journals them — so the payload landed AND survived
    rebuild, falsifying the change's own claim.

    (1) FAILS if `_update_entity` does not consult the declared surface: the
        body lands on the node as `text`/`transcript`.
    (2) REACHABLE: the body is passed through the real tenant surface below.
    """
    s, _events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    body = "UPDATE_ROUTE_PAYLOAD " + ("raw transcript. " * 150)
    # ⛔ `content`/`objectKind` ARE in this list, and that is the ruling-B case
    # (#3998): the D10 retirement is refused on EVERY `:Source`, not only a
    # document one, which OVERTURNED #5026's pinned precondition ("a
    # non-document `:Source` may legitimately carry `content` or `objectKind`",
    # `test_projection.py::test_5026_b6_promotion_scrubs_inherited_retired_fields`).
    # `RAW_URL` here is exactly such a non-document `:Source`
    # (`sourceKind="conversation"`), so this pins the new behaviour rather than
    # leaning on the document predicate. Its refusal comes from the D10/D30
    # guard (a different message than the closed-surface one below).
    for key in ("content", "objectKind"):
        with pytest.raises(ValueError, match="retired field"):
            s.update_entity(RAW_URL, **{key: body})
    for key in ("text", "transcript", "body"):
        with pytest.raises(ValueError, match="cannot be set on a :Source"):
            s.update_entity(RAW_URL, **{key: body})
    props = _source_props(s)
    assert "content" not in props and "objectKind" not in props, (
        "the generic entity route carried a retired field")
    assert body not in repr(props), "the generic entity route carried the payload"
    assert set(props) <= ALLOWED_SOURCE_NODE_PROPS
    # A DECLARED property is still updatable through the same route.
    s.update_entity(RAW_URL, format="transcript")
    assert _source_props(s)["format"] == "transcript"


def test_the_read_path_filters_a_pre_existing_payload_bearing_source(sdk):
    """Review round 3, P2: a Source written BEFORE this change still holds its
    payload, and `get_provenance_chain` edits the `source` bag — so without a
    filter the read hands back the bytes the write path now refuses, and the
    "never a copy of the raw" docstring is false for every pre-existing node.

    (1) FAILS if the returned bag is the raw stored property map (`dict(row[0])`)
        — the payload comes straight back.
    (2) REACHABLE: the payload is written with raw Cypher, exactly as the
        pre-fix writer did, so the node genuinely holds it before the read.
    """
    s, _events = sdk
    body = "LEGACY_PAYLOAD " + ("old raw bytes. " * 150)
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    pid = _memory(s)
    # The PRE-GUARD writer's shape: the bytes are on the node already.
    s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) SET s.content=$b, s.text=$b",
        params={"u": RAW_URL, "b": body},
    )
    chain = s.get_provenance_chain(pid)
    assert len(chain) == 1
    assert "content" not in chain[0]["source"] and "text" not in chain[0]["source"], (
        sorted(chain[0]["source"])
    )
    assert body not in repr(chain), "the read handed the raw payload back"
    # The PRIMARY entity read leaks the same way and is exposed as the MCP tool
    # `tortoise_get_entity` (review round 4, P2) — filter both, or the fix is
    # one function away from being no fix at all.
    ent = s.get_entity(RAW_URL)
    assert "content" not in ent and "text" not in ent, sorted(ent)
    assert body not in repr(ent), "get_entity handed the raw payload back"
    # Legitimate declared props survive the filter.
    assert chain[0]["source"]["url"] == RAW_URL
    assert chain[0]["source"]["contentHash"] == "h1"
    assert ent["url"] == RAW_URL


# ══════════════════════════════════════════════════════════════════════════
# 1b. #5196 — the paths the fix was still one function away from
# ══════════════════════════════════════════════════════════════════════════

def test_the_root_read_path_filters_a_pre_existing_payload_bearing_source(sdk):
    """#5196, P1: the SAME pre-existing payload, read through the two shipped
    TOOLS. `_parse_node` filtered the CONNECTED nodes only, and `_resolve_root`
    returned `properties(n)` VERBATIM — so the ROOT handed the bytes straight
    back through `entityProfile` (MCP `tortoise_entity_profile`) and through
    `tortoise_traverse`, while `get_entity` withheld them.

    (1) FAILS if the root bag is the raw stored property map: `text` comes back
        (measured before the fix: a 2400-byte `text` returned on the root).
    (2) REACHABLE: the payload is written with raw Cypher, exactly as the
        pre-guard writer did, so the node genuinely holds it before the read.
    """
    from tortoise.navigation import entityProfile, tortoise_traverse

    s, _events = sdk
    body = "LEGACY_PAYLOAD " + ("old raw bytes. " * 150)
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) SET s.content=$b, s.text=$b",
        params={"u": RAW_URL, "b": body},
    )
    gname = s._get_proj().g.name

    prof = entityProfile(s._get_proj().db, gname, RAW_URL, hops=1)
    root = prof["entity"]
    assert "content" not in root and "text" not in root, sorted(root)
    assert body not in repr(prof), "entityProfile handed the raw payload back"
    # The filter must not eat the declared surface or the synthetic `type` key
    # (`type` is not a stored property, so it is set AFTER the filter).
    assert root.get("url") == RAW_URL and root.get("type") == "Source", root

    trav = tortoise_traverse(s._get_proj().db, gname, RAW_URL, max_hops=1)
    troot = trav["entity"]
    assert "content" not in troot and "text" not in troot, sorted(troot)
    assert body not in repr(trav), "tortoise_traverse handed the raw payload back"


def test_an_undeclared_document_prop_is_persisted_but_never_served(sdk):
    """#5196, P1: the two contracts pinned together so neither can move alone.

    `create_document` is a CALLER PASSTHROUGH by design (#228 —
    `test_create_document_persists_arbitrary_props` pins it): an undeclared prop
    is WRITTEN and echoed back on the create return, because a create return is a
    write acknowledgement to the caller that performed the write. Reading is the
    opposite contract (#3998/D30): every read path filters through
    `_SOURCE_NODE_PROP_NAMES`. The defect was the ROOT bag, which bypassed the
    filter and served the bytes straight back — measured, a 2400-byte `text`
    returned through `entityProfile`.

    (1) FAILS on EITHER half: if the prop does not reach the node, or is missing
        from the create return, #228's passthrough was closed (a contract break,
        not a fix); if a read path serves it, the #3998 leak is back.
    (2) REACHABLE: both halves run through public routes (`create_document`,
        `entityProfile`, `tortoise_traverse`) against a real node.
    """
    from tortoise.navigation import entityProfile, tortoise_traverse

    s, _events = sdk
    body = "DOC_PROP " + ("payload bytes. " * 190)
    doc = s.create_document("doc-undeclared-5196", "note",
                            text=body, transcript=body, summary="kept")
    did = doc["id"]
    # Half 1 — #228: the prop lands on the node AND rides the create return,
    # which is a write acknowledgement, not a read.
    assert doc.get("text") == body, (
        "the create return dropped a prop the call had just written — #228's "
        "contract, which a filtered read of the node cannot serve"
    )
    rows = s._get_proj().g.query(
        "MATCH (n:Source {id:$i}) RETURN properties(n)", params={"i": did}
    ).result_set
    assert rows, "the document :Source was not written"
    assert dict(rows[0][0]).get("text") == body, "the prop did not reach the node"
    # Half 2 — #3998/D30: no READ path serves an undeclared prop, and the
    # declared metadata is still served (the filter is not a blanket denial).
    gname = s._get_proj().g.name
    prof = entityProfile(s._get_proj().db, gname, did, hops=1)
    assert body not in repr(prof), "entityProfile served the undeclared prop"
    assert "text" not in prof["entity"], sorted(prof["entity"])
    assert prof["entity"].get("summary") == "kept", sorted(prof["entity"])

    trav = tortoise_traverse(s._get_proj().db, gname, did, max_hops=1)
    assert body not in repr(trav), "tortoise_traverse served the undeclared prop"
    assert "text" not in trav["entity"], sorted(trav["entity"])


def test_a_bundle_item_cannot_set_the_raw_state(sdk):
    """`raw_state` is a DECLARED parameter of `create_source`, so a bundle item
    splats it onto the sanctioned keyword and never reaches `**props`, where
    `_sanitize_props` rejects it — the same shape as `_server_id`. Without a
    shape-time reject, the ingest bundle was the one route that could set (and
    clear) the absent-raw state.

    (1) FAILS if the bundle is ACCEPTED — acceptance means the state was set.
    (2) REACHABLE: the identical bundle without the key is accepted and writes
        the source, so the refusal is caused by the key and not by the bundle.
    """
    from tortoise.exceptions import BundleValidationError

    s, _events = sdk
    s.ingest({"sources": [{"url": RAW_URL, "sourceKind": "conversation"}]})
    assert _source_props(s).get(RAW_STATE_PROP) is None, (
        "the control bundle must not record a state"
    )

    for key in ("raw_state", "rawState", "rawStateAt"):
        bad = {"sources": [{"url": RAW_URL + "-" + key,
                            "sourceKind": "conversation", key: RAW_DELETED}]}
        with pytest.raises(BundleValidationError) as exc:
            s.ingest(bad)
        assert any(key in v["message"] for v in exc.value.violations), (
            f"{key} was not named in the violation: {exc.value.violations}"
        )


def test_a_state_carrying_recheck_is_repeat_safe(sdk):
    """`rawStateAt` is minted on EVERY state-carrying call and was compared in
    `_source_payload_is_noop`, so a re-check that found the SAME state looked
    "changed" — the journal grew by one per identical re-check (measured
    before: +5 for five repeats) while the no-state control appended 0.

    (1) FAILS if the journal grows across the repeats: §9.6 bounds a version at
        "three timestamps and a hash", so a no-op re-check must not be one.
    (2) REACHABLE: the state is genuinely written first (asserted +1), so this
        cannot pass by the state never being recorded at all.
    """
    s, events = sdk

    def _records() -> int:
        return sum(
            1 for p in sorted(events.glob("*.jsonl"))
            for line in p.read_text().splitlines() if line.strip()
        )

    s.create_source(RAW_URL, "document", contentHash="h0")
    n0 = _records()
    s.create_source(RAW_URL, "document", contentHash="h0", raw_state=RAW_DELETED)
    n1 = _records()
    assert n1 == n0 + 1, "the state change must ride the journal"
    assert _source_props(s)[RAW_STATE_PROP] == RAW_DELETED

    for _ in range(5):
        s.create_source(RAW_URL, "document", contentHash="h0",
                        raw_state=RAW_DELETED)
    assert _records() == n1, (
        "a state-carrying re-check that found the same state still appended a "
        "journal record — the re-check is not repeat-safe"
    )


def test_the_raw_reference_is_deterministic_for_a_multi_source_point(sdk):
    """`_raw_entry_for_point` ran `RETURN properties(src) LIMIT 1` with NO
    `ORDER BY`, so for a Point with more than one `extractedFrom` source the
    answer was ENGINE order — unspecified. Measured before the fix: with
    `a.example.com` and `b.example.com` linked, the HIGHER key won; the order
    is now pinned to the source's identity.

    (1) FAILS if the chosen source is not the lowest identity key, i.e. if the
        choice is left to the engine again.
    (2) REACHABLE: the fixture links TWO sources, so the choice is genuinely
        ambiguous (asserted, so a one-source fixture cannot pass vacuously).
    """
    s, _events = sdk
    a = "https://a.test/order-5196"
    b = "https://b.test/order-5196"
    # Insert in REVERSE identity order — an unambiguous fixture only proves
    # nondeterminism when the engine's natural order is a WRONG answer.
    s.create_source(b, "document")
    s.create_source(a, "document")
    pt = s.create_point("statement", "body", extractedFrom=[b, a])
    pid = pt["id"]
    s._get_proj()._link_source(pid, [b, a], label="Source")

    linked = sorted(
        r[0] for r in s._get_proj().g.query(
            "MATCH (p:Point {id:$p})-[:extractedFrom]->(src:Source) "
            "RETURN src.url", params={"p": pid}).result_set
    )
    assert linked == [a, b], f"the fixture is not ambiguous: {linked}"

    seen = {s._raw_entry_for_point(pid)["source_id"] for _ in range(5)}
    assert len(seen) == 1, f"the raw reference varies between calls: {seen}"
    assert seen == {a}, f"expected the lowest identity key {a!r}, got {seen}"


def test_declared_metadata_values_are_not_length_bounded_the_stated_residual(sdk):
    """⚠️ PINS THE STATED RESIDUAL, so it is a known bound rather than an
    implied one. The closed surface establishes that the node's property set is
    DECLARED; it does NOT bound the VALUES of the declared metadata fields. A
    caller that deliberately writes a body into ``title`` still stores it.

    Bounding short-metadata fields (what IS the maximum title?) is a VALUE
    policy and a product decision — this change does not take it. This test
    exists so the boundary is visible and so that ADDING such a policy is a
    deliberate, test-updating act.

    (1) It fails the day someone adds a length policy — which is the signal to
        update this test and the documentation that describes the residual.
    (2) REACHABLE: the body is routed through a declared key below.
    """
    s, _events = sdk
    body = "TITLE_SIZED_PAYLOAD " + ("raw. " * 300)
    s.create_source(RAW_URL, "conversation", contentHash="h1", title=body)
    assert _source_props(s)["title"] == body, (
        "a length policy now exists for a declared metadata field — update this "
        "test and the residual note in projection/entities.py"
    )


def test_rebuild_drops_a_payload_carried_by_an_entity_mutated_record(sdk):
    """Review round 4, P1: the ``EntityMutated`` fold arm is a WRITE PATH.
    ``_fold_entity_mutation``'s state-op branch replays the caller-supplied map
    with ``SET n += $s`` and consulted no allowlist, so a record emitted by the
    UNGUARDED ``_update_entity`` (round 3's hole) restored its payload on every
    rebuild — on any deployment that already ran an earlier head, the bytes are
    in the journal and no fix to the live writer retires them.

    (1) FAILS if the declaration gates only the live writers: the replayed
        ``EntityMutated`` carries the body straight onto the node.
    (2) REACHABLE: the record below is emitted through the real producer with
        the exact shape the unguarded ``_update_entity`` wrote.
    """
    s, events = sdk
    body = "ENTITY_MUTATION PAYLOAD " + ("raw transcript body. " * 120)
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    s._journal_entity_mutation(
        "Source", RAW_URL, "revise", state={"text": body, "format": "transcript"}
    )

    s._get_proj().rebuild_all(str(events), confirm_destructive=True)

    props = _source_props(s)
    assert "text" not in props, sorted(props)
    assert body not in repr(props), "replay re-materialised the raw payload"
    assert set(props) <= ALLOWED_SOURCE_NODE_PROPS, (
        f"undeclared props survived replay: {sorted(set(props) - ALLOWED_SOURCE_NODE_PROPS)}"
    )
    # The DECLARED key in the same record still replays — the filter drops the
    # payload, it does not drop the mutation.
    assert props.get("format") == "transcript", sorted(props)


def test_the_generic_route_cannot_clear_or_forge_the_absent_state(sdk):
    """Review round 4, P1: the declaration HAS to admit ``rawState`` (or the
    state could not be written) — so an allowlist check alone let the tenant MCP
    route set it freely: ``rawState=None`` CLEARS a recorded absence (``SET n +=
    {k: null}`` removes the key) and ``rawState='banana'`` persists an
    unvalidated value. Both silently resurrect a raw the record says is gone —
    the failure this issue exists to prevent — and the clear is journalled, so
    it survives a rebuild.

    (1) FAILS if the guard consults only ``_SOURCE_NODE_PROPS``: ``rawState`` is
        a member, so the clear and the forgery both go through.
    (2) REACHABLE: both spellings are passed through the real tenant surface,
        and the read is checked after each to prove the state actually moved.
    """
    from tortoise.raw_state import RAW_DELETED

    s, _events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1", raw_state=RAW_DELETED)
    assert _raw(s) ["raw_state"] == RAW_DELETED

    # The CLEAR: without the server-managed guard this reads back as `present`.
    with pytest.raises(ValueError, match="server-managed"):
        s.update_entity(RAW_URL, rawState=None)
    assert _raw(s)["raw_state"] == RAW_DELETED, "the tenant route cleared the absence"

    # The FORGERY: an unvalidated value must not be persistable either.
    with pytest.raises(ValueError, match="server-managed"):
        s.update_entity(RAW_URL, rawState="banana")
    assert _raw(s)["raw_state"] == RAW_DELETED
    assert "banana" not in repr(_source_props(s))

    # The state is still reachable the sanctioned way — the refusal is not a
    # dead end, and `present` remains the documented way back.
    s.create_source(RAW_URL, "conversation", contentHash="h1", raw_state="present")
    assert _raw(s)["available"] is True


def test_the_declaration_covers_every_in_tree_source_writer(sdk):
    """Review round 4, P2: the declaration drifted from the node. It was
    written from the two writers I happened to be editing, but
    ``get_source_reliability`` also mints three keys — so the read filter
    silently DROPPED the reliability cache that the read had just returned to
    the caller. Naming keys one at a time does not scale; this asserts the
    invariant instead: every property an in-tree producer writes to a
    ``:Source`` is DECLARED.

    (1) FAILS the day a producer adds a key the declaration lacks — which is
        exactly the drift that produced the dropped-cache defect.
    (2) REACHABLE: each producer below is invoked for real and the node is read
        back, so the surface measured is the one that exists, not a literal.
    """
    from tortoise.projection.entities import _SOURCE_NODE_PROP_NAMES

    s, _events = sdk

    def _keys_at(prop: str, val: str) -> set:
        rows = s._get_proj().g.query(
            f"MATCH (n:Source {{{prop}: $v}}) RETURN properties(n)",
            params={"v": val},
        ).result_set
        return set(rows[0][0]) if rows else set()

    s.create_source(RAW_URL, "document", contentHash="h1", title="t",
                    source_path="/tmp/x.md")
    s.get_source_reliability(RAW_URL)          # the reliability cache writer
    produced = set(_source_props(s))

    # #5196 review round 2, P2: the docstring above says "each producer below is
    # invoked for real", but only two were — and the two the declaration's own
    # comment names as the reason several keys must be present (the DOCUMENT
    # writer and the session-capture writer) were never driven. A new undeclared
    # key on either could not fail here, which is the "derive the surface from
    # what I happened to edit" failure this test exists to replace.
    doc = s.create_document("cov-doc-5196", "note")
    doc_keys = _keys_at("url", doc["id"])     # `_upsert_document` MERGEs on url
    assert doc_keys, "the document writer produced no :Source"
    produced |= doc_keys

    s._materialize_session_source(
        "cov-5196", None, "2026-01-01T00:00:00+00:00")   # the session writer
    sess_keys = _keys_at("url", "session:cov-5196")
    assert sess_keys, "the session-capture writer produced no :Source"
    produced |= sess_keys

    # the index-merge writer (`_upsert_source`'s `run_clause`) — it minted
    # `__runId`, which this test could not see, so the declaration's own invariant
    # was unchecked for that producer (#5196 review round 3, P2).
    s.create_source(RAW_URL + "?merge", "document", contentHash="h1",
                    _merge_run_id="cov-rid-5196")
    merge_keys = _keys_at("url", RAW_URL + "?merge")
    assert merge_keys, "the index-merge writer produced no :Source"
    assert "__runId" in merge_keys, (
        "the writer no longer emits `__runId` — the declaration entry for it is "
        "now dead and this test would not notice")
    produced |= merge_keys

    assert produced, "the fixture produced no :Source — the assertion would be vacuous"
    undeclared = sorted(produced - set(_SOURCE_NODE_PROP_NAMES))
    assert not undeclared, (
        f"in-tree writers mint undeclared :Source properties {undeclared} — either "
        f"declare them in entities._SOURCE_NODE_PROPS or stop them at the writer; an "
        f"undeclared key is dropped from every filtered read"
    )
    # ...and the read keeps them, which is the defect this test was written for.
    chain = s.get_provenance_chain(_memory(s))
    assert chain and "reliabilityComponents" in chain[0]["source"], (
        f"the reliability cache was dropped from the read bag: "
        f"{sorted(chain[0]['source'] if chain else {})}"
    )


def test_a_url_only_source_stub_is_guarded_like_any_other_source(sdk):
    """#5196 review round 2, P1: the two `:Source` guards resolved with
    `MATCH (n:Source {id:$id})`, while the WRITE resolves a canonical label by its
    PRIMARY key then its SECONDARY — `("id", "url")` for `:Source` — falling
    through to `url` on an id miss. A url-only STUB was therefore WRITABLE while
    invisible to the guards. Measured before this fix:

        update_entity(<stub url>, text=<2 KB>)   → NOT refused, payload live
        update_entity(<stub url>, content=...)   → NOT refused, ruling B bypassed

    `test_the_declaration_covers_an_id_less_source_stub` pinned only the ALLOWED
    half (a declared key on a stub must not be refused), so this gap was
    unmeasured rather than absent.

    (1) FAILS if either refusal is missing: acceptance IS the payload landing.
    (2) REACHABLE: the stub is minted by the real `link_source_to_entity` path
        and asserted to carry `url` and no `id`.
    """
    s, _events = sdk
    body = "STUB_PAYLOAD " + ("z" * 2000)
    _memory(s)
    obj = s.create_entity("Object", "the doc's subject")["node"]["id"]
    s.link_source_to_entity(RAW_URL, obj, "Object")
    stubs = s._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN n.id", params={"u": RAW_URL}
    ).result_set
    assert stubs and stubs[0][0] is None, "the fixture node is not an id-less stub"

    with pytest.raises(ValueError, match="retired field"):
        s.update_entity(RAW_URL, content=body)
    with pytest.raises(ValueError, match="cannot be set on a :Source"):
        s.update_entity(RAW_URL, text=body)

    after = dict(s._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN properties(n)", params={"u": RAW_URL}
    ).result_set[0][0])
    assert "text" not in after and "content" not in after, sorted(after)
    assert body not in repr(after), "the stub route carried the payload"


def test_a_create_echo_does_not_return_props_the_call_never_wrote(sdk):
    """#5196 review round 2, P1: `_get_entity(_echo_written=...)` first returned
    the WHOLE node bag, so `create_source` on a MERGE-hit handed the caller a
    legacy payload that every filtered read withholds — measured, a 2400-byte
    `text` returned through the MCP tool `tortoise_create_source`.

    A create echo is a WRITE ACKNOWLEDGEMENT: it may return what THIS CALL wrote,
    never bytes the caller did not write and cannot read anywhere else.

    (1) FAILS if the create return carries `text`/`content`.
    (2) REACHABLE: the payload is planted with raw Cypher and asserted on the
        node first, so there is genuinely something to leak.
    """
    s, _events = sdk
    body = "LEGACY " + ("y" * 2400)
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) SET s.text=$b, s.content=$b",
        params={"u": RAW_URL, "b": body},
    )
    on_node = dict(s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN properties(s)", params={"u": RAW_URL}
    ).result_set[0][0])
    assert on_node.get("text") == body, "the fixture did not plant the payload"

    again = s.create_source(RAW_URL, "conversation", contentHash="h1")
    assert "text" not in again and "content" not in again, sorted(again)
    assert body not in repr(again), "the create echo handed back the legacy raw"


def test_a_create_echo_cannot_harvest_a_denied_key_from_the_node(sdk):
    """#5196 review round 3, P1: the echo re-added a key the caller merely NAMED
    and took the NODE's value for it, so ONE `create_source` naming the payload
    spellings returned every undeclared prop the node already held — measured,
    all seven, including a 2 KB body — on a call whose write DENIED them.

    (1) FAILS if any denied key comes back: the caller learns bytes it did not
        write and that no read path will serve it.
    (2) REACHABLE: the payload is planted with raw Cypher and asserted on the
        node first, and the call goes through the public `create_source`.
    """
    s, _events = sdk
    body = "LEGACY_BODY " + ("B" * 2000)
    planted = {"text": body, "snippet": body, "chunks": body, "blob": body}
    s.create_source(RAW_URL, "conversation", contentHash="h1")
    sets = ", ".join(f"s.{k}=${k}" for k in planted)
    s._get_proj().g.query(
        f"MATCH (s:Source {{url:$u}}) SET {sets}",
        params={"u": RAW_URL, **planted},
    )
    on_node = dict(s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN properties(s)", params={"u": RAW_URL}
    ).result_set[0][0])
    assert on_node.get("text") == body, "the fixture did not plant the payload"

    # One call NAMING every planted key, with a placeholder value.
    again = s.create_source(
        RAW_URL, "conversation", contentHash="h1",
        **{k: "placeholder" for k in planted},
    )
    harvested = sorted(k for k in planted if k in again)
    assert not harvested, (
        f"the create echo harvested denied key(s) {harvested} from the node: "
        f"{ {k: str(again[k])[:24] for k in harvested} }"
    )
    assert body not in repr(again), "the create echo handed back the legacy body"

    # #5196 round 3, P3: the echo must not acknowledge a value the node does NOT
    # hold. Python folds the type away (`1 == True`), the graph does not.
    s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) SET s.flag = true", params={"u": RAW_URL})
    planted_flag = s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.flag", params={"u": RAW_URL}
    ).result_set[0][0]
    assert planted_flag is True, "the fixture did not plant the bool"
    same = s.create_source(RAW_URL, "conversation", contentHash="h1", flag=1)
    assert "flag" not in same, (
        f"a DENIED write was acknowledged with a value never stored: "
        f"{same.get('flag')!r} (the node holds the bool True)")

    # ...and the other direction: a TUPLE the write persisted reads back as a
    # LIST, and the echo must still acknowledge it — the key is undeclared, so
    # every read withholds it and the caller would have no path to it (#228).
    doc = s.create_document("echo-tuple-5196", "note", tags=("a", "b"))
    assert list(doc.get("tags") or []) == ["a", "b"], (
        f"a tuple persisted as an array was not echoed: {doc.get('tags')!r}")
    assert "tags" in doc, "the key itself was dropped from the echo"


def test_the_merge_run_token_is_not_caller_settable(sdk):
    """#5196 review round 3, P2: `__runId` had to be DECLARED (the writer puts it
    on the node), but declaring it in `_SOURCE_NODE_PROPS` ALSO made it writable,
    because that set doubles as `_update_entity`'s WRITE allowlist. Measured
    before this fix: `update_entity(url, __runId="TENANT_FORGED")` was accepted,
    journalled, and SURVIVED `rebuild_all`.

    (1) FAILS if the token is accepted, or if a forged value survives a rebuild.
    (2) REACHABLE: the refusal goes through the public route, and the contrast is
        asserted — a declared-but-unsettable key is refused as server-managed,
        while a DECLARED key is still updatable, so this cannot pass by the route
        refusing everything.
    """
    s, events = sdk
    s.create_source(RAW_URL, "document", contentHash="h1")
    with pytest.raises(ValueError, match="server-managed"):
        s.update_entity(RAW_URL, __runId="TENANT_FORGED")
    # The two routes the entity-update guard does NOT cover (#5196 round 4, P1):
    # the open DOCUMENT passthrough (#228), and the ingest bundle — `entities`
    # items are splatted into `create_document(**item)`.
    with pytest.raises(ValueError, match="server-managed"):
        s.create_document("doc-forge-5196", "note", __runId="DOC_FORGED")
    # The same guard on the document route covers the PARAMETER spelling too —
    # there it does not write `__runId`, but `_upsert_document` would persist it
    # as a literal undeclared node property (round 6, P2).
    with pytest.raises(ValueError, match="server-managed"):
        s.create_document("doc-forge-5196b", "note",
                          props={"_merge_run_id": "DOC_MID"})
    from tortoise.exceptions import BundleValidationError

    with pytest.raises(BundleValidationError) as exc:
        s.ingest({"entities": [{"type": "document",
                                "name": "bundle-forge-5196",
                                "documentKind": "note",
                                "__runId": "BUNDLE_FORGED"}]})
    assert any("__runId" in v["message"] for v in exc.value.violations), (
        exc.value.violations)
    # #5196 round 5: the PARAMETER spelling of the same token, through the props
    # route. Measured before this: `props={"_merge_run_id": ...}` wrote the
    # forged token onto the node.
    with pytest.raises(ValueError, match="server-managed"):
        s.create_source(RAW_URL + "?kw", "document", contentHash="h1",
                        props={"_merge_run_id": "KW_FORGED"})

    # ...and the NESTED spelling inside a bundle item must be a Phase-1 abort.
    # Before the flatten it reached Phase 2 and aborted AFTER an earlier section
    # had already committed, so the source below WOULD have persisted.
    with pytest.raises(BundleValidationError) as exc2:
        s.ingest({"sources": [{"url": RAW_URL + "?nested",
                               "sourceKind": "document"}],
                  "entities": [{"type": "document", "name": "ie-nested-5196",
                                "documentKind": "note",
                                "props": {"_merge_run_id": "PARTIAL_NESTED"}}]})
    assert any("_merge_run_id" in v["message"] for v in exc2.value.violations), (
        exc2.value.violations)
    committed = s._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN count(n)",
        params={"u": RAW_URL + "?nested"}).result_set[0][0]
    assert committed == 0, (
        "the bundle aborted in Phase 2 — an earlier section was already committed")

    forged = s._get_proj().g.query(
        "MATCH (n:Source) WHERE n.__runId IN ['DOC_FORGED','BUNDLE_FORGED'] "
        "RETURN count(n)").result_set[0][0]
    assert forged == 0, f"{forged} node(s) carry a forged merge-run token"
    s._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _source_props(s).get("__runId") != "TENANT_FORGED", (
        "a forged merge-run token survived the rebuild")
    # The contrast: the route still accepts a DECLARED property.
    s.update_entity(RAW_URL, format="transcript")
    assert _source_props(s)["format"] == "transcript"


def test_the_mcp_boundary_refuses_both_merge_token_spellings():
    """#5196 round 6, P2: the MCP `_SERVER_MANAGED_PROPS` entry is LOAD-BEARING
    and was untested. The MCP tools splat caller `props` into
    `create_source(**props)`, where `_merge_run_id` BINDS the parameter instead of
    landing in `**props` — so the SDK guard cannot see it and this boundary is the
    only guard on that route. Measured: removing the entry broke no test and let
    the call through.

    (1) FAILS if either spelling leaves the set, or if the boundary stops
        rejecting it.
    (2) REACHABLE: the assertion reads the real set and calls the real boundary.
    """
    from tortoise import mcp_server

    for key in ("__runId", "_merge_run_id"):
        assert key in mcp_server._SERVER_MANAGED_PROPS, (
            f"{key} left the MCP server-managed boundary — the splat route binds "
            f"the keyword and the SDK guard cannot see it")
        assert mcp_server._reject_server_managed_props({key: "X"}), (
            f"{key} was not rejected at the MCP boundary")


def test_a_same_state_recheck_does_not_move_the_stamp_live(sdk):
    """#5196 review round 3, P2: `rawStateAt` is ignored by
    `_source_payload_is_noop` (it is minted per call), but the ON MATCH clause
    still BUMPED it on every state-carrying write — so a same-state re-check
    mutated the live node while journalling nothing, and a rebuild reverted the
    stamp: live != replay, the divergence this lane exists to remove.

    (1) FAILS if the live stamp moves across the re-checks, or if the rebuilt
        stamp differs from the live one.
    (2) REACHABLE: the state is genuinely written first (its stamp asserted),
        and every re-check carries the SAME state.
    """
    s, events = sdk
    s.create_source(RAW_URL, "document", contentHash="h0")
    s.create_source(RAW_URL, "document", contentHash="h0", raw_state=RAW_DELETED)
    first = _source_props(s)["rawStateAt"]
    assert first, "the state write did not stamp rawStateAt"

    for _ in range(3):
        s.create_source(RAW_URL, "document", contentHash="h0",
                        raw_state=RAW_DELETED)
    live = _source_props(s)["rawStateAt"]
    assert live == first, "a same-state re-check moved the stamp on the live node"

    s._get_proj().rebuild_all(str(events), confirm_destructive=True)
    replayed = _source_props(s)["rawStateAt"]
    assert replayed == live, (
        f"live {live!r} != replay {replayed!r} — the journal does not reproduce "
        f"the stamp the live node carries"
    )


def test_the_declaration_covers_an_id_less_source_stub(sdk):
    """Review round 4, P2: the guard resolved the node by ``n.url = $id OR
    n.id = $id`` but the write matches ``{label: {_ENTITY_ID_PROP[label]: $id}}``
    — ``:Source`` stubs minted by ``_link_source`` carry ``url`` and no ``id``. So
    a declared update to a stub was refused with a message naming a node the
    write could never have reached, while an undeclared one was refused for the
    right reason by accident. The guard must use the WRITE's predicate.

    (1) FAILS if the guard and the write use different predicates — the
        declared-key update raises ``ValueError`` here.
    (2) REACHABLE: the stub is minted by the real ``link_source_to_entity``
        path, so it has the shape the guard got wrong.
    """
    s, _events = sdk
    _memory(s)  # the Point whose raw the stub Source will belong to
    obj = s.create_entity("Object", "the doc's subject")["node"]["id"]
    s.link_source_to_entity(RAW_URL, obj, "Object")   # the real stub producer
    stubs = s._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN n.id", params={"u": RAW_URL}
    ).result_set
    assert stubs and stubs[0][0] is None, "the fixture node is not an id-less stub"
    # A declared key: must NOT be refused (previously it raised, describing a
    # node the write could not reach).
    s.update_entity(RAW_URL, format="transcript")


def test_rebuild_does_not_replay_a_state_change_from_an_entity_mutation(sdk):
    """Review round 5, P1: the fold's `:Source` arm filtered undeclared keys but
    still replayed the SERVER-MANAGED ones, so a journal line written by an
    earlier head — the ``rawState=None`` CLEAR that round 3's open guard
    journalled — resurrected a permanently deleted raw on every rebuild. A
    ``SourceCreated`` record owns the state; no legitimate ``EntityMutated``
    producer exists for it, so replay must drop it.

    (1) FAILS if the fold drops only undeclared keys: the recorded clear is
        applied and the state reads back as ``present``.
    (2) REACHABLE: the record is emitted through the real journal producer with
        the exact shape the unguarded route wrote, and the read is checked.
    """
    from tortoise.raw_state import RAW_DELETED

    s, events = sdk
    s.create_source(RAW_URL, "conversation", contentHash="h1", raw_state=RAW_DELETED)
    assert _raw(s)["raw_state"] == RAW_DELETED
    s._journal_entity_mutation("Source", RAW_URL, "revise", state={"rawState": None})

    s._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert _raw(s)["raw_state"] == RAW_DELETED, (
        "a replayed EntityMutated resurrected the raw the record says is gone"
    )
    assert _raw(s)["available"] is False


# NOTE (#3998): there is deliberately NO test here pinning the identity keys either
# way. #5438 (`58cd62ed3`, merged, and an ANCESTOR of this branch's base) made a
# url-keyed `:Source` WRITABLE through the generic entity surface, and main pins it
# in `tests/test_url_keyed_source_write_4649.py` — BOTH
# `::TestUrlKeyedSourceIsWritable::test_a_rekeying_update_returns_the_node_at_its_new_address`
# AND `::TestTheStateReadBackIsTheSameStatement::test_a_name_update_that_rekeys_a_url_source_still_journals`
# (two classes, not one). Restoring the refusal fails exactly those 2 of that
# file's 6 tests, measured. They are the pin; a second copy here could only
# disagree with them. An earlier revision of this file asserted the refusal, which
# is why this PR was red for its whole life.
#
# The residual a refusal was aimed at is NOT covered by those tests, so nothing
# here buys it back: a re-key rewriting the MERGE key, so a rebuild splits the
# `:Source` (two nodes) and the Point's `get_provenance_chain` returns EMPTY —
# re-measured live at this head with a 1517-byte `url`. It is PRE-EXISTING to #3998
# and it is #4649's structural issue (the outer label loop takes the FIRST matching
# label), recorded on #4649 — which is CLOSED, so it is neither open-tracked nor
# test-pinned. That is a real gap, stated rather than papered over: the structural
# fix belongs with #4649's loop, not with a guard on this write.


def test_a_multi_source_point_prefers_the_source_that_has_an_entity(sdk):
    """Review round 5, P2 — a REGRESSION introduced by this change's own
    ``OPTIONAL MATCH``. Making the ``references`` hop survivable is required (a
    source with no entity must still be sayable), but a bare ``LIMIT 1`` then
    returned whichever source matched FIRST — so a Point with two sources
    (#3263 many-to-many) whose first source has no entity reported
    ``entity=None`` and HID the entity-bearing source the pre-change required-hop
    query returned.

    (1) FAILS if the entity-bearing source is HIDDEN. Under the branch's
        pre-#5199 query (`OPTIONAL MATCH` + `LIMIT 1` with no ordering) the
        entity-less source won and the chain reported it alone. `origin/main`
        (#5199) now returns ONE ROW PER LINK, so the shape is two rows and the
        falsifier is that the entity-bearing source's row must be PRESENT and
        must LEAD (`ref IS NULL` sorts a resolved reference ahead of the
        self-terminal fallback), and the fallback row's `entity` is the source
        itself (the `coalesce`), never `None`.
    (2) REACHABLE: the fixture builds that exact shape — an entity-less source
        linked first, then an entity-bearing one — so both rows exist to choose
        between.
    """
    s, _events = sdk
    pid = _memory(s)
    second = "https://raw.example.com/conv/8"
    s.create_source(second, "conversation", contentHash="h2")
    obj = s.create_entity("Object", "an extracted object")["node"]["id"]
    s.link_source_to_entity(second, obj, "Object")
    s._get_proj().g.query(
        "MATCH (p:Point {id:$p}), (s:Source {url:$u}) MERGE (p)-[:extractedFrom]->(s)",
        params={"p": pid, "u": second},
    )
    chain = s.get_provenance_chain(pid)
    # #5199 (now on main): ONE ROW PER LINK — a Point with two sources yields
    # two rows, not one. The regression this test pins is unchanged in
    # substance: the entity-bearing source must not be HIDDEN behind the
    # entity-less one, so its row must be present AND lead (`ref IS NULL` sorts
    # a resolved reference ahead of the self-terminal fallback).
    assert len(chain) == 2, chain
    assert chain[0]["entity"] is not None, (
        "the entity-bearing source was hidden behind an entity-less one"
    )
    assert chain[0]["labels"], "labels must accompany the entity"
    assert "Object" in chain[0]["labels"], chain[0]["labels"]
    assert "Source" in chain[1]["labels"], chain[1]["labels"]
    assert chain[1]["entity"] is not None, (
        "the self-terminal fallback row returns the source itself, never None"
    )


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

    s._get_proj().rebuild_all(str(events), confirm_destructive=True)

    props = _source_props(s)
    assert props[RAW_STATE_PROP] == RAW_DELETED, "the absence did not survive rebuild"
    assert props["contentHash"] == "h1", "the version anchor did not survive rebuild"
    assert raw_availability(props).permanent is True


def test_a_url_only_stub_that_collides_with_a_point_id_still_guards_the_write(sdk):
    """CORRECTED BY MEASUREMENT (#5196 review round 2, P1).

    This test previously asserted that a url-only `:Source` stub whose `url`
    equals a Point's id is UNREACHABLE by `update_entity`, and therefore that the
    guard must not widen to `url = $id`. **That premise is measurably false.**
    The write resolves each canonical label by its PRIMARY key then its SECONDARY
    (`("id", "url")` for `:Source`), and it does not stop after the first label
    that matches, so it writes to BOTH:

        update_entity(pid, format="transcript")
        → stub  keys: [..., 'format']     ← the :Source WAS written
        → point keys: [..., 'format']

    A guard that followed the old premise therefore left a url-only stub
    writable-but-unguarded: measured, `update_entity(<stub url>, text=<2 KB>)`
    persisted the payload live and into the journal.

    (1) FAILS if the guard ignores the reachable colliding stub: a retired field
        is accepted and lands on a `:Source`, violating ruling B.
    (2) REACHABLE: the stub is minted by the public `extractedFrom` link and the
        update goes through the public `update_entity`.

    KNOWN CONSEQUENCE, deliberate and narrow: because the write reaches the
    colliding `:Source` too, a D10-retired field is refused even though the
    caller's `id` names the Point. The alternative is letting `content` land on a
    `:Source`. The shape needs a Point id to equal a `:Source` url — nonsense
    data (a Point id is not a URL), which is why this is bounded. The structural
    fix is to stop the label loop at the first match so one id cannot mutate two
    nodes; that belongs to #4649's OR-SET write, not here.
    """
    s, _events = sdk
    pid = s.create_point("statement", "point A content")["id"]
    # A url-only `:Source` STUB keyed by the Point's id — the shape
    # `_link_source`/`_mint_source_stub` create (a `url`, no `id`).
    s.create_point("statement", "sibling", extractedFrom=pid)
    stub = s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.id", params={"u": pid}
    ).result_set
    assert stub, "no url-only stub was minted — the setup is unreachable"
    assert stub[0][0] != pid, (
        "the stub carries `id` == the Point's id, so it does not exercise the "
        "url-only shape this test exists to pin")
    # A DECLARED field is still writable, and this asserts the measurement the
    # correction above rests on — that the write reaches the colliding stub.
    s.update_entity(pid, format="transcript")
    assert s._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.format", params={"u": pid}
    ).result_set[0][0] == "transcript", (
        "the write no longer reaches the colliding stub — the guard's premise "
        "would then need re-deriving from this measurement")
    assert s.get_point(pid)["format"] == "transcript"
    # A D10-retired field IS refused, because the write would put it on the
    # `:Source` above — ruling B applies to every `:Source` the write reaches.
    with pytest.raises(ValueError, match="retired field"):
        s.update_entity(pid, content="edited point A content")
