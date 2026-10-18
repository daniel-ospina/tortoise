"""#3644 (§4.6 *Versioning*, the WINDOW half) — a `:Source` version has a window.

THE GAP THIS PINS
-----------------
`ONTOLOGY.md` §4.6 declares the Source temporal model: identity is `url`,
`contentHash` identifies a **version**, there is **one node per `url`**, and::

    The graph is a projection of the journal (§3), so the Source node carries
    one version — the current one — with its contentHash, its version ordinal,
    and the current version's valid-time window (validFrom/validTo). A version
    transition appends a journal record that closes the previous version's
    window and records the new one.

    Closing the interval is part of the write. An unclosed validTo reads as
    "still true" indefinitely — the failure mode the whole model exists to
    prevent — so the journal record that opens a new version closes the old one
    in the same act; it is never a later repair.

`#5024` landed the **version-anchor** half (a hash transition journals a
`SourceVersioned` record naming `previousContentHash`; `rebuild_all` reproduces
the node field for field) and deliberately left the **window** half open. Before
this change `_upsert_source` wrote no `validFrom`/`validTo`/`expiredAt` at all
(`docs/durability-posture.md` → R2 read *"declared, never written"*), so:

  * the node had no window start;
  * the transition record named the superseded *version* but closed no window —
    "closes the previous window" was an intention, not a recorded act.

WHAT IS IMPLEMENTED HERE
------------------------
  * `create_source` mints the window instant ONCE, on the same clock as
    `updatedAt` (`_version_instant`), so the live node and every record it
    journals cannot disagree;
  * `_upsert_source` writes the node's `validFrom` through the SAME hash-diff
    gate as `version`/`updatedAt` (create, stub completion, real transition) —
    never on a no-op re-check, never on a hash-less write;
  * the `SourceVersioned` record carries **both** window facts: `validFrom`
    (the new version's open) and `previousValidTo` (the prior version's close),
    equal by construction — §4.6's "closes the old one in the same act";
  * the fold is the live writer (`_fold_source_versioned` delegates to
    `_upsert_source`), so `derived = replay(journal)` holds by construction.

WHAT IS DELIBERATELY *NOT* DONE HERE (`Refs`, not `Closes`)
-----------------------------------------------------------
  * the node's `validTo` — absent while the current version is current, and an
    open end IS the model value (Cypher cannot write a NULL property). It is
    written by nothing because no `:Source` REMOVAL path exists yet; the
    removal-field decision is `#3590` S2's (`expiredAt`), so declaring or
    writing either now would open a surface with nothing behind it;
  * `Subject`/`Object` windows — the issue's other rows (§4.2/§4.3), still
    `❌` in §4.7's matrix; Object supersession additionally needs the successor
    window on the `ObjectSuperseded` payload, which is a separate design;
  * a caller-asserted valid-time input (a known publication date). The window
    start here is an OBSERVATION boundary, which is definitional for a version
    ("the content as read at an instant") and is what §4.6 requires to be able
    to close the prior window at all. A caller-asserted start needs the
    inversion guards the Point window needed (`#3980`, `#4021`, `#5358`,
    `#5360`); until it exists, the server-minted instant is guarded by
    `_mint_source_window_instant`, which clamps the mint strictly above the
    prior mint so the wall clock cannot open a zero-width or inverted version
    window;
  * **the two OTHER `:Source` version writers** (`#3644`, PR #8016 code-review
    ISSUE 2): `_upsert_document` (the `DocumentCreated` fold — it bumps
    `contentHash` + `version` on the same hash-diff gate but writes no
    `validFrom`) and `_materialize_session_source` (the capture path — it
    rewrites `contentHash` without advancing `version`; since #5097 its
    METADATA block does replay through a trailing `SourceCreated`, which
    carries the node's `validFrom` verbatim — so a replay no longer CLEARS a
    window, but this writer still never MOVES one). Both leave a PRESENT-but-stale window start, which is why §4.6's
    "the prior window is closed in the same act" is scoped to the
    `create_source`/`_upsert_source` path in the docs. Extending the gate to
    them needs a minted-and-replayable instant (a clock/mint decision, not a
    boundary fix); the gap is PINNED — not silenced — by the two
    `test_pinned_gap__*` tests at the bottom of this file, which must be
    inverted when #3644 closes it.

DISCLOSURE (review note, not a hidden defect): five of these tests are FORWARD
guards for rejected alternatives — they assert an ABSENCE the pre-#3644 writer
also produced, so they pass against a full production revert and cannot prove
non-vacuity: `test_a_noop_recheck_moves_neither_the_window_nor_the_journal`,
`test_a_legacy_record_without_a_window_replays_to_an_open_start`,
`test_a_legacy_hash_change_does_not_inherit_the_prior_window_start`,
`test_a_stub_minted_source_has_an_open_start`, and
`test_the_transition_payload_does_not_leak_onto_the_node`. They pin behaviour
that must NOT regress once the window exists (and that the rejected
alternatives would have broken); they are not fail-before evidence.

Run (embedded carve-out lane):
    TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
        tests/test_source_version_window_3644.py -q
"""
from __future__ import annotations

import itertools
import json

import pytest

from tortoise.sdk import TortoiseSDK

_URL = "https://example.test/doc/version-window-3644"


def _props(sdk, url: str = _URL) -> dict:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN properties(n)", params={"u": url}
    ).result_set
    assert rows, f"source {url!r} missing from the graph"
    return rows[0][0]


def _records(events_dir) -> list[dict]:
    out = []
    for path in sorted(events_dir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _of_type(events_dir, type_) -> list[dict]:
    return [r for r in _records(events_dir) if r.get("type") == type_]


@pytest.fixture
def src(tmp_path):
    """(events_dir, sdk) with the journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "src3644.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield events, sdk
    sdk.close()


# ── 1. the node's window is written, and it is the RECORDED instant ──────

def test_a_new_source_records_its_window_start(src):
    """A created `:Source` carries `validFrom` — the current version's open.

    FAILS BEFORE: `_upsert_source` wrote no `validFrom` on any path, so the
    node had no window start and §4.6's "the current version's valid-time
    window" had nothing behind it.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")

    p = _props(sdk, _URL)
    assert "validFrom" in p, "the created source carries no window start"
    # ABSENT is the open end — and it is the only spelling of it (Cypher
    # cannot write a NULL property).
    assert "validTo" not in p
    assert "expiredAt" not in p

    create = _of_type(events, "SourceCreated")[0]
    assert create["validFrom"] == p["validFrom"], (
        "the recorded window start and the node's must be the same instant — "
        "the record is what a replay will read")


def test_the_window_start_is_the_recorded_instant_not_the_replay_clock(src):
    """One clock: a rebuild reproduces the window, byte for byte.

    FAILS BEFORE (as a live != replay divergence) on any field written from
    the fold's own `_now_iso()` — the #5024 `updatedAt`/`ingestedAt` class.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    live = _props(sdk, _URL)["validFrom"]

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    rebuilt = _props(sdk, _URL)

    assert rebuilt["validFrom"] == live, (
        "the window start moved across a rebuild — it came from the replay "
        "clock rather than the record")
    assert rebuilt["validFrom"] == _of_type(
        events, "SourceCreated")[0]["validFrom"]


# ── 2. a transition opens the new window AND closes the prior one ────────

def test_a_transition_opens_the_new_window_and_closes_the_prior_one(src):
    """§4.6's "closes the previous version's window and records the new one".

    FAILS BEFORE: the `SourceVersioned` record named the superseded version but
    carried no window facts, and the node's `validFrom` never moved.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")

    t = _of_type(events, "SourceVersioned")
    assert len(t) == 1
    t = t[0]
    p = _props(sdk, _URL)

    # The NEW window's open is the node's window start ...
    assert t["validFrom"] == p["validFrom"], (
        "the transition record's window start is not the node's")
    # ... and the PRIOR window's close is the same instant (contiguity by
    # construction — one minted clock, two names for the act).
    assert t["previousValidTo"] == t["validFrom"], (
        "the prior version's window close and the new version's open must be "
        "the same instant — a gap is covered by neither version and an "
        "overlap reads as two current versions")
    assert "validTo" not in p, (
        "the CURRENT version's window is open — the node must not be closed "
        "by a transition")


def test_a_noop_recheck_moves_neither_the_window_nor_the_journal(src):
    """Repeat-safety extends to the window (#5024's §9.6 cost bound).

    The window instant is minted on EVERY call, so the write gate (not the
    payload comparison) must be what protects the node: without the hash-diff
    gate a re-check would move `validFrom` live while journalling nothing and
    a rebuild would revert it — the exact `rawStateAt` divergence #5196 P2
    found.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="doc", contentHash="h-0",
                      summary="s-0")
    before = _props(sdk, _URL)
    n_before = len(_records(events))

    for _ in range(5):
        sdk.create_source(_URL, "document", title="doc", contentHash="h-0",
                          summary="s-0")

    assert _props(sdk, _URL) == before, (
        "a no-op re-check moved the live node (the window start is the "
        "usual suspect: it is minted per call)")
    assert len(_records(events)) == n_before, (
        "a no-op re-check appended a journal record")


def test_a_same_hash_write_with_new_extras_does_not_move_the_window(src):
    """The gate is the VERSION, not the record cadence.

    A same-hash write that changes an extra IS journalled (it must be — the
    extras ride the payload into `_persist_extra_props`), so the window gate
    cannot be "did we journal"; it must be the stored-hash difference.
    """
    _events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                      summary="before")
    before = _props(sdk, _URL)["validFrom"]
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0",
                      summary="after")
    assert _props(sdk, _URL)["summary"] == "after"
    assert _props(sdk, _URL)["validFrom"] == before, (
        "a same-hash write moved the window — no new version was observed")


def test_a_hashless_write_does_not_move_the_window(src):
    """The preserve-everything arm must preserve the window too."""
    _events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    before = _props(sdk, _URL)["validFrom"]
    # No contentHash: the ON MATCH clause preserves hash/version/updatedAt —
    # and the window.
    sdk.create_source(_URL, "document", title="retitle")
    p = _props(sdk, _URL)
    assert p["contentHash"] == "h-0"
    assert p["validFrom"] == before, (
        "a hash-less write moved the window — it observed no new version")


# ── 3. the prior window is a JOURNAL fact, recoverable without the graph ──

def test_the_window_timeline_is_recoverable_from_the_journal_alone(src):
    """The whole point of §4.6's "a prior version's window is a journal fact".

    There is ONE node per `url`, so the only place the history can live is the
    record chain. Every window must be reconstructible from the journal with
    the graph closed — and the chain must be exactly contiguous.
    """
    events, sdk = src
    for i in range(4):
        sdk.create_source(_URL, "document", title=f"v{i}",
                          contentHash=f"h-{i}")
    live_from = _props(sdk, _URL)["validFrom"]
    sdk.close()  # the graph is not consulted below

    create = _of_type(events, "SourceCreated")[0]
    transitions = _of_type(events, "SourceVersioned")
    assert [t["contentHash"] for t in transitions] == ["h-1", "h-2", "h-3"]

    # Rebuild the windows: each version's window is [its open, the next open).
    opens = [create["validFrom"]] + [t["validFrom"] for t in transitions]
    windows = list(itertools.pairwise(opens))
    assert len(windows) == 3, f"expected 3 closed windows, got {windows}"
    for (prev, cur), t in zip(windows, transitions, strict=True):
        assert t["previousValidTo"] == cur, (
            "the record's prior-window close must BE the next version's open")
        assert prev < cur, (
            f"the window chain is not strictly increasing — {prev!r} -> "
            f"{cur!r}; a non-monotone chain leaves instants covered by two "
            f"versions or by none")
    assert live_from == opens[-1], (
        "the node's window start is not the last version's open")


def test_a_chain_of_transitions_stays_contiguous(src):
    """Pairwise contiguity across a longer chain, live and replayed."""
    events, sdk = src
    for i in range(6):
        sdk.create_source(_URL, "document", title=f"v{i}",
                          contentHash=f"h-{i}")
    transitions = _of_type(events, "SourceVersioned")
    # Each record closes the window it opens (one minted instant, two names) ...
    for t in transitions:
        assert t["previousValidTo"] == t["validFrom"], (
            "a transition closed a window at an instant other than the instant "
            "it opened its own — the windows gapped or overlapped")
    # ... and the version opens are strictly increasing, so no instant is
    # covered by two versions and none falls between two windows.
    opens = [t["validFrom"] for t in transitions]
    for prev, cur in itertools.pairwise(opens):
        assert prev < cur, f"the version opens are not monotone: {prev} -> {cur}"

    live = _props(sdk, _URL)["validFrom"]
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _props(sdk, _URL)["validFrom"] == live == transitions[-1]["validFrom"]


# ── 4. back-compat: an unrecorded window is an OPEN start, never a stamp ──

def test_a_legacy_record_without_a_window_replays_to_an_open_start(src,
                                                                  tmp_path):
    """The write wall clock is never borrowed as a valid-time start.

    Every journal written before this change, and every record from a writer
    that mints no window, must replay to a source with NO `validFrom` —
    `coalesce($vf, $now)` here would stamp the REPLAY clock on an absent
    window, i.e. invent a version start that no observation supports, and
    would disagree with the live graph the same journal was written from.
    """
    _events, sdk = src
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    lines = []
    for i in range(3):
        lines.append(json.dumps({
            "type": "SourceCreated", "url": _URL, "id": _URL,
            "contentHash": f"h-{i}", "sourceKind": "document",
            "title": f"v{i}",
            "ingestedAt": f"2026-01-0{i + 1}T00:00:00+00:00",
            "updatedAt": f"2026-02-0{i + 1}T00:00:00+00:00",
        }))
    (legacy / "legacy.jsonl").write_text("\n".join(lines) + "\n")

    sdk._get_proj().rebuild_all(str(legacy), confirm_destructive=True)
    p = _props(sdk, _URL)
    assert (p["contentHash"], p["version"]) == ("h-2", 3)
    assert "validFrom" not in p, (
        "the replay synthesized a window start for a record that carries "
        "none — an absent start is an OPEN start (§4.1/#3654)")


def test_a_legacy_hash_change_does_not_inherit_the_prior_window_start(src,
                                                                    tmp_path):
    """A transition whose record carries NO window leaves the start UNKNOWN.

    The gate sets `$vf` itself, never `coalesce($vf, s.validFrom)`: after a
    versioned create, a hash-changing legacy record (a foreign/pre-#3644
    journal line) must not leave the node claiming the CURRENT version started
    when the PREVIOUS one did. `SET … = null` clears the property, and absent
    is the model's own honest value for an unrecorded start (open/unbounded).
    """
    _events, sdk = src
    legacy = tmp_path / "mixed"
    legacy.mkdir()
    (legacy / "mixed.jsonl").write_text("\n".join([
        json.dumps({"type": "SourceCreated", "url": _URL, "id": _URL,
                    "contentHash": "h-0", "sourceKind": "document",
                    "title": "v0", "validFrom": "2026-01-01T00:00:00+00:00"}),
        # A hash change with no window fact at all.
        json.dumps({"type": "SourceVersioned", "url": _URL, "id": _URL,
                    "contentHash": "h-1", "previousContentHash": "h-0",
                    "sourceKind": "document", "title": "v1"}),
    ]) + "\n")

    sdk._get_proj().rebuild_all(str(legacy), confirm_destructive=True)
    p = _props(sdk, _URL)
    assert (p["contentHash"], p["version"]) == ("h-1", 2)
    assert "validFrom" not in p, (
        "the new version inherited the PRIOR version's window start — an "
        "unrecorded start must be absent (open), never a value from the "
        "version it replaced")


def test_a_stub_minted_source_has_an_open_start(src):
    """The ⚠️ half of the matrix: coverage is the `create_source` path.

    `_mint_source_stub` (the `_link_source` capture path) writes no window —
    there is no observation to record on a stub — so a stub-minted `:Source`
    reads as an open start. Pinned because the ontology's matrix must not claim
    more coverage than the writers deliver.
    """
    from tortoise.projection.edges import _mint_source_stub
    _events, sdk = src
    key = _mint_source_stub(sdk._get_proj().g, _URL)
    assert key == _URL
    p = _props(sdk, _URL)
    assert p["contentHash"] == ""
    assert "validFrom" not in p, (
        "the stub writer minted a window start it cannot know")


# ── 5. the record's window facts are journal-only ────────────────────────

def test_the_transition_payload_does_not_leak_onto_the_node(src):
    """`previousContentHash`/`previousValidTo` describe the TRANSITION.

    The fold delegates to the live writer (`_fold_source_versioned` →
    `_upsert_source`), so a payload key not in `_SOURCE_HANDLED` would be
    persisted through `_persist_extra_props` — the one-sided-property class
    (#330/#3312). Asserted live AND after a rebuild (the replay passes the
    record's own payload).
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    leaked = {"previousContentHash", "previousValidTo"}
    assert not (leaked & set(_props(sdk, _URL))), (
        f"journal-only keys leaked onto the node: "
        f"{sorted(leaked & set(_props(sdk, _URL)))}")
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert not (leaked & set(_props(sdk, _URL))), (
        "journal-only keys leaked onto the node through the replay")


def test_the_transition_record_still_carries_no_redundant_derivations(src):
    """#5024's polarity, re-asserted for the window keys.

    `previousValidTo` is NOT a second derivation: it is the SAME minted instant
    as `validFrom` (one clock, two names for §4.6's act). The keys #5024
    deliberately withheld (`at`, `version`) stay withheld.
    """
    events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    sdk.create_source(_URL, "document", title="v1", contentHash="h-1")
    t = _of_type(events, "SourceVersioned")[0]
    assert "at" not in t
    assert "version" not in t
    assert t["validFrom"] == t["updatedAt"] == t["previousValidTo"]


# ── 6. the window is not a caller-assertable property ────────────────────

def test_the_window_start_cannot_be_forged_through_the_entity_surface(src):
    """`validFrom` is minted by the writer and journalled — never asserted.

    Declaring it in `_SOURCE_NODE_PROPS` makes it READABLE (the read filter
    drops undeclared keys); listing it in `_SOURCE_SERVER_MANAGED_PROPS` is
    what stops the generic entity surface from writing a window that no
    transition produced — the trap #5196 round 3 found for `__runId`.
    """
    _events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    minted = _props(sdk, _URL)["validFrom"]
    with pytest.raises(ValueError, match="server-managed"):
        sdk.update_entity(_URL, validFrom="2020-01-01T00:00:00+00:00")
    assert _props(sdk, _URL)["validFrom"] == minted, (
        "the forged window start landed on the node")


# ── 7. the window instant is monotone against a stalled/stepped clock ────

def test_a_stalled_clock_cannot_open_a_zero_width_window(src, monkeypatch):
    """#3644 review P1: a fixed wall clock must not produce duplicate opens.

    FAILS BEFORE: the instant came straight from the wall clock with no
    monotonicity guard, so two transitions under a fixed clock opened windows
    at the SAME instant (``prev == cur``) — a zero-width prior window.
    """
    events, sdk = src
    import tortoise.sdk as _sdkmod
    monkeypatch.setattr(_sdkmod, "_now_iso",
                        lambda: "2026-01-01T12:00:00+00:00")
    monkeypatch.setattr(_sdkmod, "_source_window_instant_floor", None,
                        raising=False)

    for i in range(3):
        sdk.create_source(_URL, "document", title=f"v{i}",
                          contentHash=f"h-{i}")

    create = _of_type(events, "SourceCreated")[0]
    transitions = _of_type(events, "SourceVersioned")
    opens = [create["validFrom"]] + [t["validFrom"] for t in transitions]
    assert len(set(opens)) == len(opens), (
        f"a stalled clock produced duplicate window opens: {opens}")
    for prev, cur in itertools.pairwise(opens):
        assert prev < cur, (
            f"the window chain is not strictly increasing: {prev!r} -> {cur!r}")


def test_a_backward_clock_step_cannot_invert_the_window(src, monkeypatch):
    """#3644 review P1: a clock step backward must not invert prior windows.

    FAILS BEFORE: the prior window's close (`previousValidTo`, the new open)
    landed BEFORE the prior open, so the earlier version's window was inverted
    (`validTo < validFrom`) — the class `audit.inverted_validity_window` rates
    HIGH. The monotone mint clamps above the prior mint.
    """
    events, sdk = src
    import tortoise.sdk as _sdkmod
    _vals = ["2026-01-01T12:00:10+00:00",
             "2026-01-01T12:00:05+00:00",
             "2026-01-01T12:00:07+00:00"]
    _state = {"i": 0}

    def _clock():
        v = _vals[min(_state["i"], len(_vals) - 1)]
        _state["i"] += 1
        return v

    monkeypatch.setattr(_sdkmod, "_now_iso", _clock)
    monkeypatch.setattr(_sdkmod, "_source_window_instant_floor", None,
                        raising=False)

    for i in range(3):
        sdk.create_source(_URL, "document", title=f"v{i}",
                          contentHash=f"h-{i}")

    create = _of_type(events, "SourceCreated")[0]
    transitions = _of_type(events, "SourceVersioned")
    opens = [create["validFrom"]] + [t["validFrom"] for t in transitions]
    for prev, cur in itertools.pairwise(opens):
        assert prev < cur, (
            f"the backward clock step inverted the window chain: "
            f"{prev!r} -> {cur!r}")
    for (prev_open, _), t in zip(itertools.pairwise(opens), transitions,
                                 strict=True):
        assert prev_open < t["previousValidTo"], (
            f"the prior version's window is inverted: open {prev_open!r} >= "
            f"close {t['previousValidTo']!r}")


# ── 8. the window and transition keys are server-managed (PR #8016 review) ─

_WINDOW_KEYS = ("validFrom", "validTo", "valid_to", "previousValidTo",
                "previousContentHash")


def test_a_caller_asserted_window_or_transition_key_is_refused(src):
    """#3644 review P2b + PR #8016 code-review ISSUE 1.

    FAILS BEFORE: `validFrom`/`validTo` were silently DISCARDED (the node got
    the minted instant while the journal carried the caller's value), and a
    caller `previousValidTo` collided with the explicit keyword on the
    `SourceVersioned` emit — a swallowed `TypeError` DROPPED the record while
    the live node had already moved (fail-open, live != replay).
    """
    _events, sdk = src
    for key in _WINDOW_KEYS:
        with pytest.raises(ValueError, match="server-managed"):
            sdk.create_source(_URL, "document", contentHash="h-0",
                              **{key: "2020-01-01T00:00:00+00:00"})
    # fail-closed: nothing was written before the refusal
    assert sdk._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN count(n)",
        params={"u": _URL}).result_set[0][0] == 0, (
        "a refused write still landed a :Source")


def test_the_entity_surface_refuses_window_and_transition_keys(src):
    """The same keys are unwritable through `update_entity` (write filter)."""
    _events, sdk = src
    sdk.create_source(_URL, "document", title="v0", contentHash="h-0")
    for key in _WINDOW_KEYS:
        with pytest.raises(ValueError, match="server-managed"):
            sdk.update_entity(_URL, **{key: "2020-01-01T00:00:00+00:00"})


def test_the_mcp_boundary_keeps_only_the_transition_keys():
    """PR #8016 code-review ISSUE 1 (P1): the entity-agnostic MCP boundary
    must NOT refuse the Point-legitimate window spellings.

    `_reject_server_managed_props` is the choke point for every MCP entity
    tool, and `tortoise_create_point`/`tortoise_update_point` splat caller
    `props` straight into `sdk.create_point`/`sdk.update_point`, for which
    `validFrom`/`validTo` are first-class caller props (§4.7 keeps the Point
    column caller-writable; `sdk._refuse_inverted_point_window`'s docstring
    states it). FAILS BEFORE: all five keys were in the shared set, so a
    legitimate Point write was refused at the boundary with `-32003` (the
    regression this PR introduced — origin/main did not carry the keys).

    The TRANSITION facts are Source-only and can never be a legitimate caller
    prop on any entity, so they stay in the shared set. The `:Source` window is
    guarded on the Source routes instead (`sdk.create_source`'s `**props`
    reject; `sdk._update_entity`'s `_SOURCE_SERVER_MANAGED_PROPS`), pinned by
    `test_the_mcp_source_route_still_refuses_a_forged_window` below and by
    `test_a_caller_asserted_window_or_transition_key_is_refused`.
    """
    from tortoise import mcp_server
    for key in ("validFrom", "validTo", "valid_to"):
        assert key not in mcp_server._SERVER_MANAGED_PROPS, (
            f"{key} is a Point caller prop — the entity-agnostic MCP boundary "
            f"must not refuse it")
        assert not mcp_server._reject_server_managed_props({key: "X"}), (
            f"{key} was refused at the entity-agnostic MCP boundary")
    for key in ("previousValidTo", "previousContentHash"):
        assert key in mcp_server._SERVER_MANAGED_PROPS, (
            f"{key} left the MCP server-managed boundary")
        assert mcp_server._reject_server_managed_props({key: "X"}), (
            f"{key} was not rejected at the MCP boundary")


@pytest.fixture
def mcp_sdk(monkeypatch, tmp_path):
    """(mcp_server, sdk) — an embedded SDK wired into the MCP boundary.

    Mirrors `tests/test_mcp_server.py`'s autouse transport fixture: `_safe`'s
    auth gate fail-CLOSES on an unset transport mode, so stdio dev mode must be
    set even though `_get_org_sdk` is patched.
    """
    from tortoise import mcp_server
    from tortoise.mcp_auth import (
        _current_org_id,
        _current_org_limits,
        _transport_mode,
    )
    sdk = TortoiseSDK(str(tmp_path / "mcp_window_3644.db"))
    monkeypatch.setattr("tortoise.mcp_server._get_org_sdk", lambda: sdk)
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    token = _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_org_limits.set(None)
    try:
        yield mcp_server, sdk
    finally:
        _transport_mode.reset(token)
        sdk.close()


def test_the_mcp_point_tools_accept_a_caller_window(mcp_sdk):
    """PR #8016 code-review ISSUE 1 (P1) — the Point window is writable.

    FAILS BEFORE: `tortoise_create_point(props={"validFrom": …})` returned
    `{'error': "server-managed field(s) cannot be set via props: ['validFrom']",
    'code': -32003}` before any write, and the same for
    `tortoise_update_point(props={"validTo": …})`. §4.7 keeps the Point column
    caller-writable, so this is a legitimate write the PR broke.
    """
    mcp_server, sdk = mcp_sdk
    start = "2020-01-01T00:00:00+00:00"
    end = "2021-01-01T00:00:00+00:00"

    def _point_props(pid: str) -> dict:
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$i}) RETURN properties(n)",
            params={"i": pid}).result_set
        assert rows, f"point {pid!r} missing from the graph"
        return rows[0][0]

    created = mcp_server.tortoise_create_point(
        kind="observation", content="point with a caller window",
        props={"validFrom": start})
    assert not created.get("error"), created
    pid = created["id"]
    assert _point_props(pid)["validFrom"] == start, (
        "the caller's validFrom did not reach the Point")
    updated = mcp_server.tortoise_update_point(
        id=pid, props={"validTo": end})
    assert not updated.get("error"), updated
    assert _point_props(pid)["validTo"] == end, (
        "the caller's validTo did not reach the Point")


def test_the_mcp_source_route_still_refuses_a_forged_window(mcp_sdk):
    """The Source routes stay fail-closed after the boundary narrowing.

    Narrowing `_SERVER_MANAGED_PROPS` must not re-open the `:Source` window.
    `tortoise_create_source` splats caller `props` into `create_source(**props)`,
    whose own reject fires before `ev` is built; `tortoise_update_entity` on a
    reachable `:Source` is refused through `_SOURCE_SERVER_MANAGED_PROPS`. Both
    surface as a structured error via `_safe` — and neither writes.
    """
    mcp_server, sdk = mcp_sdk
    forged = "2020-01-01T00:00:00+00:00"
    for key in ("validFrom", "validTo", "valid_to"):
        out = mcp_server.tortoise_create_source(
            url=_URL, sourceKind="document", props={key: forged})
        assert out.get("error"), (
            f"{key} was accepted on the MCP create_source route: {out}")
    assert sdk._get_proj().g.query(
        "MATCH (n:Source {url:$u}) RETURN count(n)",
        params={"u": _URL}).result_set[0][0] == 0, (
        "a refused MCP create_source still landed a :Source")

    sdk.create_source(_URL, "document", contentHash="h-0")
    minted = _props(sdk, _URL)["validFrom"]
    for key in ("validFrom", "validTo", "valid_to"):
        out = mcp_server.tortoise_update_entity(
            id=_URL, props={key: forged})
        assert out.get("error"), (
            f"{key} was accepted on the MCP update_entity route: {out}")
    assert _props(sdk, _URL)["validFrom"] == minted, (
        "a forged window landed on the node through the MCP entity route")


# ── 9. the OTHER :Source version writers do NOT maintain the window ──────
#
# PR #8016 code-review ISSUE 2 (P1) — PINNED GAP, not silence.
#
# `_upsert_document` (the `DocumentCreated` fold, which bumps `contentHash`
# and `version` on the same hash-diff gate as `_upsert_source`) and
# `_materialize_session_source` (the session-capture path, which rewrites
# `contentHash` without advancing `version` — since #5097 its metadata replays
# via a trailing `SourceCreated` that carries the node's `validFrom` verbatim,
# so a replay no longer CLEARS a window; neither writer MOVES one) are
# two further writers that move a `:Source`'s version anchor. Neither moves
# `validFrom`, so a source whose window this change created keeps the
# PREVIOUS version's start after either write: the node then asserts the new
# version has been valid since the old version's start — the stale-window
# failure §4.6 exists to prevent.
#
# These two tests PIN that current behaviour so it cannot change silently.
# They assert the WRONG value ON PURPOSE and must be inverted (assert the
# window MOVES) when the gap closes. Extending the window gate here is not a
# boundary fix: each writer needs a minted instant that is ALSO replayable —
# `_upsert_document` journals `DocumentCreated`, and
# `_materialize_session_source` replays only its metadata block, never a window
# instant — i.e. a clock/mint
# decision this PR does not take. #3644's own "Writers" section names
# `_upsert_document` among the writers the temporal model must reach, so the
# gap is tracked there; the docs (§4.6 / §4.7 † and the plan doc) state it.

def test_pinned_gap__upsert_document_moves_the_anchor_without_the_window(src):
    """#3644 (PR #8016 code-review ISSUE 2, P1) — PINNED, deliberately wrong.

    `_upsert_document` is the `DocumentCreated` fold for a document `:Source`.
    It bumps `contentHash` + `version` on the same hash-diff gate
    `_upsert_source` uses, and writes no `validFrom` — so the window start
    stays the one the PRIOR version opened. Asserting `validFrom == before`
    is asserting the defect; this test exists so the defect cannot change
    silently and is inverted when #3644 closes it.
    """
    _events, sdk = src
    sdk.create_source(_URL, "document", contentHash="h-1", title="d1")
    before = _props(sdk, _URL)["validFrom"]
    sdk._get_proj()._upsert_document(
        {"id": _URL, "contentHash": "h-2", "title": "d2"})
    p = _props(sdk, _URL)
    assert p["contentHash"] == "h-2" and p["version"] == 2, p
    assert p["validFrom"] == before, (
        "PINNED GAP (#3644): `_upsert_document` moved the version anchor — "
        "validFrom MUST equal the PRIOR version's start only while the gap is "
        f"open. Got {p['validFrom']!r}, prior start {before!r}. If the window "
        f"now moves, invert this assertion.")


def test_pinned_gap__session_capture_moves_the_anchor_without_the_window(src):
    """#3644 (PR #8016 code-review ISSUE 2, P1) — PINNED, deliberately wrong.

    `_materialize_session_source` rewrites the session `:Source`'s
    `contentHash` (and summary/topics) and writes no `validFrom`, so the
    capture path leaves the prior version's window start in place. It does not
    even advance `version`, so the ordinal and the hash already disagree here —
    a pre-existing §4.6 inconsistency this PR does not own. Asserting
    `validFrom == before` is asserting the defect; invert when #3644 closes it.
    """
    _events, sdk = src
    url = "session:pinned-gap-3644"
    sdk.create_source(url, "agentSession", contentHash="h-1")
    before = _props(sdk, url)["validFrom"]
    sdk._materialize_session_source(
        "pinned-gap-3644", None, "2026-01-02T00:00:00+00:00",
        [{"role": "user", "content": "hello pinned window gap"}])
    p = _props(sdk, url)
    assert p["contentHash"] not in (None, "", "h-1"), p
    assert p["validFrom"] == before, (
        "PINNED GAP (#3644): the session-capture writer moved the version "
        "anchor — validFrom MUST equal the PRIOR version's start only while "
        f"the gap is open. Got {p['validFrom']!r}, prior start {before!r}. If "
        f"the window now moves, invert this assertion.")
