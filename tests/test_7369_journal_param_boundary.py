"""#7369 — the parameter boundary is TOTAL, so a corrupt record degrades.

THE DEFECT THIS PINS
--------------------
A journal record is untrusted input: it is a JSONL file on disk that can be
hand-edited, produced by another version, or torn. Values read from it ride
into FalkorDB Cypher parameters, and FalkorDB rejects a non-primitive property
value, a non-finite float, and a string carrying NUL or a lone surrogate.

The folds used to gate this BY HAND, per field, through four different
predicates at ~21 call sites. So each newly recorded field had to remember to
gate itself, and the ones that did not — ``valid_to`` on the terminalizer
folds, and every fixed SET clause on the primary creation path — raised from
inside a replay:

    redis.exceptions.ResponseError: Property values can only be of primitive types

That is not a local failure. ``rebuild_all``'s pass-1a and pass-1b have **no
per-event try/except**, so the raise lands AFTER ``_wipe_all_nodes``: the
rebuild aborts having already destroyed the graph, leaving it wiped or
half-built — in the measured case a RETRACTED claim still LIVE, i.e. the graph
serving retracted content as current.

THE SEAM, WHICH IS THE ACTUAL FIX
---------------------------------
Not four more per-field gates. ``_GuardedGraph`` (``self.g``) is the single
handle every projection write goes through — the live ``apply()`` and all
three replay engines — and it already carries three sibling cross-cutting
decisions (the #3595 operator refusal, ``_is_bulk_wipe``, the #3359 op count).
The writability policy is now enforced THERE, at the parameter boundary, so a
recorded field is safe BY CONSTRUCTION and a field recorded LATER cannot
reopen the hole. The predicate is ``_annotator_value_ok`` — the policy the
annotator dims already used — so the rule keeps ONE home rather than gaining a
fifth copy.

WHY THE GATE DEGRADES TO ``None`` RATHER THAN DROPPING THE KEY
--------------------------------------------------------------
A parameter the Cypher still references must stay BOUND, and for a
``SET n += $map`` an OMITTED key leaves the pre-existing value in place —
state the journal never justified, which is the very outcome this gate exists
to prevent. ``None`` is also what the folds' own ``ev.get(key)`` fallbacks
already produce for an absent field, so the degradation matches the existing
contract instead of inventing one.

WHAT IS DELIBERATELY *NOT* COVERED HERE
---------------------------------------
  * **Gated is not the same as FAITHFUL.** ``_journal_instant`` substitutes the
    REPLAY CLOCK for a corrupt or absent instant, so ``expired_at``/``ts``
    cannot raise but can still write a value the journal never stated — and
    ``consistency.py``'s reference fold does not mirror that fallback, so the
    two disagree. That is the #5048 "Total" clause, not this change.
  * **``supersedes_by`` is only half closed.** ``str(ev.get(...) or "")`` at
    ``entities.py`` coerces a map into a repr STRING (so it cannot raise on the
    non-primitive class) but passes a NUL/lone-surrogate string through
    unchanged. The boundary gate catches the latter now; the silent
    ``repr``-of-a-map is a fidelity defect owned by #5048's family, not this
    file.
  * **``consistency.py``'s ``_fold_journal`` cannot raise at all** — it builds
    an in-memory ``{id: props}`` dict and never sends a parameter, so it is a
    false positive as a "replay surface" for THIS class. Its exposure is
    comparison false-positives, not a ``ResponseError``.
  * **``recover_from_log`` already could not abort** — it has a per-event
    try/except (``consistency.py``) and silently skips the record. The gate
    makes it correct rather than merely survivable; the abort-after-wipe shape
    belongs to ``rebuild_all`` / ``rebuild`` / ``backup.restore``.

Run (embedded carve-out):
  TORTOISE_TEST_CARVE_OUT=1 python -m pytest \\
      tests/test_7369_journal_param_boundary.py -q
"""
from __future__ import annotations

import json
import logging
import math
import pathlib
from typing import ClassVar

import pytest

from tortoise.projection import (
    _flat_writable,
    _GuardedGraph,
    _journal_safe_params,
    _log_identity_skip,
    _statement_writes,
    _writable_at_parse,
    _writable_id,
)
from tortoise.sdk import TortoiseSDK

# ── a strict stand-in for the driver's own rejection ─────────────────────
#
# FalkorDB rejects a map / bytes / set / non-finite float as a property value,
# and rejects a string carrying NUL or a lone surrogate at encode. This mirror
# is what makes the SEAM test below a real test: the fake raises exactly where
# the engine raises, so if the gate stops being wired into the verb, the test
# fails instead of quietly passing.

def _reject_unless_writable(key, value, depth=0):
    if isinstance(value, dict):
        if depth > 0:
            raise ValueError(
                "Property values can only be of primitive types "
                f"(param {key!r} carries a nested map)"
            )
        for k, v in value.items():
            _reject_unless_writable(f"{key}.{k}", v, depth + 1)
        return
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int,)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(
                f"Property values can only be of primitive types "
                f"(param {key!r} is non-finite)"
            )
        return
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"param {key!r} carries a NUL byte")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError(f"param {key!r} carries a lone surrogate") from None
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _reject_unless_writable(f"{key}[{i}]", item, depth + 1)
        return
    raise ValueError(
        f"Property values can only be of primitive types (param {key!r} "
        f"is {type(value).__name__})"
    )


class _StrictDriver:
    """A graph handle that rejects exactly what FalkorDB rejects."""

    def __init__(self):
        self.seen: list = []
        self.checked = 0

    def query(self, cypher, params=None, timeout=None):
        for k, v in (params or {}).items():
            _reject_unless_writable(k, v)
            self.checked += 1
        self.seen.append((cypher, params))
        return "ok"

    ro_query = query
    profile = query
    explain = query

    def _query(self, cypher, params=None, timeout=None, read_only=False):
        return self.query(cypher, params=params, timeout=timeout)


class _ProjectionStub:
    def _assert_test_graph(self, message):  # only reached for a bulk wipe
        return None


def _guarded(driver=None):
    return _GuardedGraph(driver or _StrictDriver(), _ProjectionStub())


# ── 1. the pure policy ───────────────────────────────────────────────────

def test_every_unwritable_value_class_degrades_to_null():
    """The classes that raise at parameter parse, one per row.

    No statement is supplied, so none of these params is SPREAD (``+= $x``)
    and a dict is therefore a dict IN a property — the engine rejects it.
    """
    out = _journal_safe_params({
        "a": {"evil": 1},          # a map where a scalar belongs
        "b": b"bytes",              # bytes
        "c": {1, 2},                # a set
        "d": float("nan"),          # non-finite float
        "e": "a\x00b",              # NUL
        "f": ["ok", {"x": 1}],      # an array containing a map
    })
    assert out == {
        "a": None, "b": None, "c": None, "d": None, "e": None, "f": None,
    }, out


def test_the_same_map_degrades_or_is_spread_depending_on_the_statement():
    """THE discriminating case, and the reason the container test is
    Cypher-aware rather than depth-based.

    The value does not decide it — the statement does. ``SET n += $props``
    spreads a map into properties, so gating the map itself would null every
    such write. ``SET n.validTo=$vt`` puts the map IN a property, where the
    engine rejects it — and that is the exact shape of the #7369 failure, so a
    depth-only rule silently leaves the reported defect open (measured: the
    gate passed a dict-valued ``$vt`` straight through and ``rebuild_all``
    still raised after the wipe).
    """
    assert _journal_safe_params(
        {"vt": {"evil": 1}},
        "MATCH (n:Point) SET n.validTo=$vt RETURN n",
    ) == {"vt": None}

    assert _journal_safe_params(
        {"props": {"a": 1}}, "MATCH (n:Point) SET n += $props RETURN n",
    ) == {"props": {"a": 1}}


def test_a_corrupt_ENTRY_inside_a_spread_map_degrades_alone():
    """The container is recursed into, not discarded: one unwritable entry
    must not cost the good ones their write."""
    assert _journal_safe_params(
        {"props": {"ok": 1, "bad": {"x": 1}}},
        "MATCH (n:Point) SET n += $props RETURN n",
    ) == {"props": {"ok": 1, "bad": None}}


def test_an_UNWIND_row_list_is_not_a_value_position():
    """REGRESSION PIN for the container shapes this gate must NOT null.

    ``UNWIND $turns AS turn`` holds a LIST OF ROW MAPS — rows, not property
    values. A rows-blind gate nulls the whole list, and the failure is SILENT
    rather than loud: the statement still runs, it just runs on nothing, so the
    capture turn upsert and the document version bump are skipped and the
    replay completes with the right SHAPE and the WRONG CONTENT (measured —
    ``version did not advance with the hash: ['sha-v2', 1]``).
    """
    assert _journal_safe_params(
        {"turns": [{"id": "a", "v": [0.5]}]},
        "UNWIND $turns AS turn MERGE (t:Point {id: turn.id})",
    ) == {"turns": [{"id": "a", "v": [0.5]}]}

    # ...and a genuinely corrupt entry INSIDE a row still degrades on its own.
    assert _journal_safe_params(
        {"turns": [{"id": "a", "bad": {"x": 1}}]},
        "UNWIND $turns AS turn SET t.p = turn.bad",
    ) == {"turns": [{"id": "a", "bad": None}]}


def test_a_map_that_REPLACES_props_is_a_container_too():
    """``SET n = $p`` replaces the whole property set from a map — the third
    container shape, and the one a spread-only rule misses."""
    assert _journal_safe_params(
        {"p": {"a": 1}}, "MATCH (n:Point) SET n = $p",
    ) == {"p": {"a": 1}}

    # A bare variable is not required: ``SET n.x = $p`` is a SCALAR position,
    # so the same dict must degrade there. This is what keeps the two apart.
    assert _journal_safe_params(
        {"p": {"a": 1}}, "MATCH (n:Point) SET n.x = $p",
    ) == {"p": None}


def test_none_is_kept_because_dropping_it_breaks_live_replay_parity():
    """``update_point(x=None)`` CLEARS a property; a replay that dropped the
    key would leave the prior value in place and diverge from live."""
    params = {"a": None}
    assert _journal_safe_params(params) is params


def test_a_clean_param_map_is_returned_unchanged_same_object():
    """The healthy hot path must not allocate — this runs on every query."""
    params = {"id": "p1", "content": "text", "v": [0.1, 0.2], "n": 3,
              "b": True, "props": {"ok": 1}}
    assert _journal_safe_params(params, "MATCH (n) SET n += $props") is params


def test_legitimate_nested_arrays_survive():
    """The gate must not reject what the engine genuinely accepts."""
    params = {"m": [[1, 2], [3, 4]], "ids": ["a", "b"], "e": [0.5] * 8}
    assert _journal_safe_params(params) is params


# ── 2. the seam is WIRED INTO THE BOUNDARY (not merely available) ────────
#
# These are the mutation-sensitive tests: unwire the gate from any verb and
# they fail, because the strict driver then sees the raw value and raises
# exactly as FalkorDB does.

@pytest.mark.parametrize("verb", ["query", "ro_query", "_query", "profile",
                                  "explain"])
def test_the_boundary_degrades_before_the_driver_sees_it(verb):
    """MUTATION-SENSITIVE: unwire the gate from any verb and this fails.

    The corrupt value is deliberately NOT a dict. The first version of this
    test passed one at the TOP level, and the mirror below accepts a top-level
    dict (it cannot know whether the statement spreads it), so the mirror waved
    it through and the test passed with the gate UNWIRED — eight of sixteen
    passing on an unwired gate, which made this module's claim that unwiring
    fails every verb false. ``bytes`` is rejected at every position, so the
    mirror can only accept it if the gate actually ran.
    """
    driver = _StrictDriver()
    g = _guarded(driver)
    call = getattr(g, verb)
    out = call("MATCH (n:Point) SET n.v=$v RETURN n", params={"v": b"bytes"})
    assert out == "ok", out


def test_a_read_only_statement_is_untouched():
    """A read whose parameters are already writable returns the SAME object.

    NOTE: identity alone does NOT prove the walk was skipped — the full walk
    also returns the same object when nothing is degraded, so this test passes
    with or without the fast path. The fast path's own decision function is
    asserted directly in `test_the_read_fast_path_admits_the_id_list_shape`;
    this test is here for the read path's contract, not its cost.
    """
    params = {"ids": [f"p{i}" for i in range(5000)]}
    assert _journal_safe_params(
        params, "MATCH (p:Point) WHERE p.id IN $ids RETURN p",
    ) is params


def test_the_read_fast_path_admits_the_id_list_shape():
    """The read fast path must cover the shape retrieval actually sends.

    The first version of this pre-scan admitted only SCALARS, so a 5,000-id
    read — `params={"ids": [...5000 strings...]}`, which is a LIST — fell
    through to the full walk (~22 ms/call) while the commit message claimed the
    hot path was preserved. The claim was false and this assertion is the check
    that would have caught it.
    """
    assert _flat_writable([f"p{i}" for i in range(5000)]) is True
    assert _flat_writable(("a", "b")) is True
    assert _flat_writable("a") is True
    assert _flat_writable(None) is True
    # ...and anything nested must DECLINE the fast path, so the full walk —
    # where correctness lives — still runs.
    assert _flat_writable({"a": 1}) is False
    assert _flat_writable([{"a": 1}]) is False
    assert _flat_writable([["a"]]) is False
    assert _flat_writable(b"x") is False
    assert _flat_writable({"a", "b"}) is False
    # A corrupt scalar in a flat list must NOT be admitted as writable.
    assert _flat_writable(["ok", "bad\x00id"]) is False


def test_a_READ_statement_IS_gated_because_the_engine_parses_every_param():
    """THE P1 FIX (round 3). A read cannot be skipped.

    The clause does not decide whether a parameter is PARSED — FalkorDB parses
    every parameter regardless of clause, so an unwritable value in a read
    ``MATCH {prop:$p}`` aborts ``rebuild_all`` after the wipe exactly as a
    write does. Skipping reads here RE-OPENED the hole; measured aborts in
    ``resolve_source_key`` (`MATCH (s:Source {canonicalUrl:$cu})`),
    ``_try_about_edge`` (`MATCH (e:Subject {name:$name})`), and a plain dict
    reaching a read through ``about_entities``.

    The hot path is preserved by a cheap SCALAR pre-scan instead: a read whose
    parameters are all writable scalars still returns identity, so this is a
    correctness fix that costs the retrieval path nothing.
    """
    # A corrupt scalar in a READ is degraded (this used to be returned as-is).
    assert _journal_safe_params(
        {"cu": "bad\x00url"}, "MATCH (s:Source {canonicalUrl:$cu}) RETURN s",
    ) == {"cu": None}
    # A map is a SHAPE-only reject: measured, the engine ACCEPTS a dict as a bare
    # parameter on a statement that writes nothing, and rejects it only once it is
    # STORED as a property. So on a READ it is forwarded, not nulled (#7174).
    payload = {"deep": [{"n": 1}]}
    assert _journal_safe_params(
        {"name": "x", "payload": payload},
        "MATCH (e:Subject {name:$name}) RETURN e",
    )["payload"] == payload
    # ...and the SAME map on a WRITE is degraded, because there it WOULD be stored
    # as a property and the engine raises "Property values can only be of primitive
    # types" — the abort-after-wipe this boundary exists to prevent.
    assert _journal_safe_params(
        {"name": "x", "payload": payload},
        "MATCH (e:Subject {name:$name}) SET e.payload = $payload",
    )["payload"] is None
    # PARSE-time rejects still degrade on BOTH, because the engine parses every
    # parameter regardless of clause (bytes measured on a read).
    assert _journal_safe_params(
        {"v": b"\x01\x02"}, "MATCH (n:X {v: $v}) RETURN n",
    ) == {"v": None}
    # A PARSE reject NESTED inside a container still degrades. The engine parses
    # the WHOLE parameter, so testing only the top level let this through and
    # the engine then refused it with "Failed to parse query parameter 'ids'
    # value" — the abort-after-wipe, re-opened by the map exemption itself.
    assert _journal_safe_params(
        {"ids": ["ok", "bad\x00id"]}, "MATCH (n) WHERE n.id IN $ids RETURN n",
    ) == {"ids": None}
    assert _journal_safe_params(
        {"m": {"a": "bad\x00x"}}, "MATCH (n) WHERE n.id = $m RETURN n",
    ) == {"m": None}
    # ...and a nested lone surrogate too (the driver rejects it at encode).
    assert _journal_safe_params(
        {"ids": ["ok", "bad\ud800id"]},
        "MATCH (n) WHERE n.id IN $ids RETURN n",
    ) == {"ids": None}
    # A write keyword inside a STRING LITERAL does not make a read a write —
    # matching the raw text would null this perfectly good map.
    assert _journal_safe_params(
        {"m": {"a": 1}}, "MATCH (n) WHERE n.s='SET' RETURN n",
    )["m"] == {"a": 1}
    # CALL is treated as a write: an index procedure STORES without naming a
    # write clause, and a keyword search cannot see inside the procedure name.
    assert _journal_safe_params(
        {"m": {"a": 1}},
        "CALL db.idx.vector.createNodeIndex('Point','embedding',1536,'HNSW')",
    )["m"] is None
    # ...and DROP is a write clause (it was missing from the keyword set).
    # Pinned on a BARE DROP, not `CALL db.idx.fulltext.drop(...)` — the latter is
    # already caught by the call-procedure rule, so it would pass even with DROP
    # removed from the keyword set.
    assert _journal_safe_params(
        {"m": {"a": 1}}, "DROP INDEX ON :Point(embedding)",
    )["m"] is None
    # A PROPERTY named after a keyword is not a clause: `n.set` in a read is a
    # property reference, and nulling the map there is a false refusal.
    assert _journal_safe_params(
        {"m": {"a": 1}}, "MATCH (n) WHERE n.set = $m RETURN n",
    )["m"] == {"a": 1}
    assert _journal_safe_params(
        {"m": {"a": 1}}, "MATCH (n) WHERE n.drop = $m RETURN n",
    )["m"] == {"a": 1}
    # A read-only CALL/subquery is NOT a write either — these are the retrieval
    # paths, and nulling a map there is the #7174 false refusal again.
    assert _journal_safe_params(
        {"m": {"a": 1}}, "CALL db.idx.vector.queryNodes('Point','embedding',5)",
    )["m"] == {"a": 1}
    assert _journal_safe_params(
        {"m": {"a": 1}}, "MATCH (n) CALL { WITH n RETURN n } RETURN n",
    )["m"] == {"a": 1}
    # ...and the all-scalar read still takes the cheap identity route.
    assert _journal_safe_params(
        {"name": "fine"}, "MATCH (e:Subject {name:$name}) RETURN e",
    ) == {"name": "fine"}


def test_a_SELF_REFERENTIAL_or_deep_parameter_DEGRADES_and_never_raises():
    """The recursion must be BOUNDED — its contract is "degrades, never raises".

    An unbounded walk breaks that twice: a cycle recurses forever and a deep
    container exhausts the stack. On the replay path (`_TOLERATE_ALTERED_NUMBERS`)
    the sibling `_guard_numeric_params` is a no-op, so this walk is the ONLY
    boundary there and a RecursionError would abort the rebuild AFTER the wipe.
    """
    cyclic: list = ["ok"]
    cyclic.append(cyclic)
    assert _journal_safe_params(
        {"a": cyclic}, "MATCH (n) WHERE n.id = $a RETURN n",
    ) == {"a": None}

    cyclic_map: dict = {"ok": 1}
    cyclic_map["self"] = cyclic_map
    assert _journal_safe_params(
        {"a": cyclic_map}, "MATCH (n) WHERE n.id = $a RETURN n",
    ) == {"a": None}

    deep: object = 1
    for _ in range(2000):
        deep = [deep]
    assert _journal_safe_params(
        {"a": deep}, "MATCH (n) WHERE n.id = $a RETURN n",
    ) == {"a": None}

    # A shallow, entirely writable container is STILL forwarded (the bound must
    # not become a blanket refusal).
    assert _journal_safe_params(
        {"a": {"n": [1, "two"]}}, "MATCH (n) WHERE n.id = $a RETURN n",
    )["a"] == {"n": [1, "two"]}


def test_an_EMPTY_identity_is_refused_not_admitted():
    """THE P2 FIX (round 3). ``_annotator_value_ok("")`` is True.

    So replacing the folds' ``if not name:`` / ``if not eid:`` / ``if not url``
    with ``if not _writable_id(name):`` ADMITTED the empty string, and a record
    with NO ``name`` defaults to ``""`` — so a `SubjectAdded` carrying no name
    created ``:Subject {name:""}`` where the old guard skipped it. An empty
    identity is not an identity; refusing it in ``_writable_id`` keeps the rule
    in one home instead of repeating ``... and val`` at every call site.
    """
    assert _writable_id("") is False
    assert _writable_id("a") is True
    # A record whose name is ABSENT (not merely corrupt) is skipped, not
    # materialised as an empty-keyed node.
    assert _journal_safe_params(
        {"name": ""}, "MERGE (s:Subject {name:$name}) RETURN s",
    ) == {"name": ""}  # the MERGE key is left to the FOLD, which now skips it


def test_a_MERGE_key_is_never_nulled_because_the_engine_refuses_a_null_key():
    """THE P1 FIX. FalkorDB REFUSES a null merge key (``Cannot merge node
    using null property value``), so degrading an identity parameter does not
    PREVENT an abort — it swaps in a different one.

    That was measured end-to-end: a ``PointAdded`` whose ``point.id`` carries a
    NUL passed the old ``isinstance(str)`` check, reached ``MERGE (n:Point
    {id:$id})``, was nulled here, and raised. So the boundary now leaves a MERGE
    key alone and the creation anchors SKIP the record instead, using
    ``_writable_id`` (as ``_retract`` and ``_revise_point`` already did).
    """
    assert _journal_safe_params(
        {"id": "bad\x00id"}, "MERGE (n:Point {id:$id}) RETURN n",
    ) == {"id": "bad\x00id"}

    # ...while a VALUE parameter in the SAME statement is still gated.
    assert _journal_safe_params(
        {"id": "ok", "c": {"evil": 1}},
        "MERGE (n:Point {id:$id}) SET n.content=$c",
    ) == {"id": "ok", "c": None}


def test_a_ROW_FIELD_used_as_a_MERGE_key_is_not_nulled_either():
    """The same manufactured-null-key hole by a different route.

    A MERGE key can be a row FIELD rather than a parameter — ``MERGE (t:Point
    {id: turn.id})`` over ``UNWIND $turns``. Nulling that field is refused by
    the engine exactly like a null parameter key (measured on the real turn
    statement: ``Cannot merge node using null property value``), so the field
    names appearing as ``<row>.<field>`` inside a MERGE map are excluded from
    the row walk. A NON-key field in the same row is still gated.

    ⛔ WHAT THIS DOES **NOT** CLAIM (round-7 review, finding 3). Leaving the
    field alone is NOT a fix for the corrupt value: it is still a parameter the
    engine cannot PARSE, so this exemption only avoids converting one abort
    into a SECOND one. The route is closed in the FOLD — the one writer of a
    row-MERGE statement is ``sdk._write_capture_turns``, which skips a batch
    whose session id (hence every row id) is unwritable and WARNs, pinned by
    ``test_an_unwritable_row_merge_key_is_skipped_by_the_fold_not_forwarded``.
    The assertion below pins the boundary's own contract and must not be read
    as "a corrupt row merge key is safe here".
    """
    cy = ("UNWIND $turns AS turn MERGE (t:Point {id: turn.id}) "
          "SET t.content = turn.c")
    assert _journal_safe_params(
        {"turns": [{"id": "bad\x00id", "c": "fine"}]}, cy,
    ) == {"turns": [{"id": "bad\x00id", "c": "fine"}]}

    assert _journal_safe_params(
        {"turns": [{"id": "good", "c": {"x": 1}}]}, cy,
    ) == {"turns": [{"id": "good", "c": None}]}


def test_the_boundary_preserves_a_corrupt_sibling_and_only_nulls_the_bad_one():
    driver = _StrictDriver()
    g = _guarded(driver)
    g.query("MATCH (n:Point) SET n += $props",
            params={"props": {"good": 7, "bad": {"x": 1}}})
    _cypher, params = driver.seen[-1]
    assert params == {"props": {"good": 7, "bad": None}}, params


# ── 3. the acceptance: a poisoned journal does not abort a rebuild ───────
#
# BEFORE THE FIX this is the measured end-to-end failure: `rebuild_all` raises
# `ResponseError: Property values can only be of primitive types` from pass-1b,
# AFTER `_wipe_all_nodes` has already destroyed the graph.

def _poison(events: pathlib.Path, record_type: str, key: str, value) -> int:
    """Rewrite the journal, replacing ``key`` on every record of ``type``.

    ``key`` is a DOTTED path — ``PointAdded`` nests the point payload under
    ``point`` (``point.content``), while the terminalizer fields the issue names
    (``valid_to``) sit at the top level.
    """
    path_parts = key.split(".")
    hits = 0
    for path in sorted(events.glob("*.jsonl")):
        lines = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("type") == record_type:
                node = rec
                for part in path_parts[:-1]:
                    node = node.get(part) if isinstance(node, dict) else None
                    if node is None:
                        break
                if isinstance(node, dict) and path_parts[-1] in node:
                    node[path_parts[-1]] = value
                    hits += 1
            lines.append(json.dumps(rec, ensure_ascii=False))
        path.write_text("\n".join(lines) + "\n")
    return hits


@pytest.fixture
def superseded(tmp_path):
    """(events_dir, sdk, old_id, new_id) with a supersession on the journal."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "j7369.db"),
                      event_log_path=str(events / "events.jsonl"))
    old = sdk.create_point("statement", "the old claim")["id"]
    new = sdk.create_point("statement", "the new claim")["id"]
    sdk.supersede_point(old, new)
    yield events, sdk, old, new
    sdk.close()


def test_a_corrupt_valid_to_no_longer_aborts_the_rebuild(superseded):
    """THE proof obligation for #7369.

    ``valid_to`` is the field the issue names, and it is the one the per-field
    gating missed. A rebuild must COMPLETE and must not leave the superseded
    claim looking live.
    """
    events, sdk, old, _new = superseded
    assert _poison(events, "PointSuperseded", "valid_to", {"evil": 1}) == 1

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN n.status, n.outdated",
        params={"i": old},
    ).result_set
    assert rows, "the rebuild lost the superseded point entirely"
    status, outdated = rows[0]
    assert status == "superseded" and outdated is True, (
        f"the replay left the superseded claim in a state the journal never "
        f"justified: status={status!r} outdated={outdated!r}"
    )


def test_a_corrupt_creation_field_no_longer_aborts_the_rebuild(superseded):
    """The creation path is the WIDER half of the class — ~20 ungated values
    on ``_upsert_point_props`` alone — and it is not on the issue's table."""
    events, sdk, _old, _new = superseded
    assert _poison(events, "PointAdded", "point.content", {"evil": 1}) >= 1

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)", params={},
    ).result_set
    assert rows[0][0] >= 1, "the rebuild did not re-materialise any point"


def test_a_corrupt_point_id_is_skipped_rather_than_aborting_the_rebuild(superseded):
    """THE P1 ACCEPTANCE, end to end.

    A NUL in ``point.id`` used to pass the ``isinstance(str)`` guard, reach
    ``MERGE (n:Point {id:$id})``, be degraded to ``None`` and raise ``Cannot
    merge node using null property value`` — an abort after the wipe, which is
    what the boundary alone could NOT fix. The creation anchors now skip the
    record with ``_writable_id``, so the rebuild COMPLETES and every OTHER
    point still materialises.
    """
    events, sdk, _old, _new = superseded
    assert _poison(events, "PointAdded", "point.id", "bad\x00id") >= 1

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)", params={},
    ).result_set
    assert rows[0][0] >= 1, (
        "the rebuild skipped the corrupt-id record AND lost the healthy ones"
    )


def test_a_corrupt_PROMOTE_id_is_skipped_rather_than_aborting_the_rebuild(superseded):
    """The PROMOTE folds are the second identity route into the same abort.

    ``PointPromoted`` / ``OperatorPromoted`` guarded their snapshot with bare
    truthiness (``p.get("id")``), so a corrupt id reached ``MERGE (n:Point
    {id:$id})`` — and because the boundary now (correctly) refuses to null a
    merge key, the engine rejected the parameter and pass-1b died AFTER the
    wipe. Measured on the pre-fix tree: ``Failed to parse query parameter 'id'
    value``. All four promote sites now use ``_writable_id``.
    """
    events, sdk, _old, _new = superseded
    # Append a promote record carrying a poisoned id — the journal for a
    # supersede fixture has no promote, and the guard is what is under test.
    (events / "events.jsonl").write_text(
        (events / "events.jsonl").read_text()
        + json.dumps({"type": "PointPromoted", "event_id": "e7369",
                      "ts": "2026-01-01T00:00:00+00:00",
                      "point": {"id": "bad\x00id", "content": "x",
                                "pointKind": "statement"}}) + "\n"
    )

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)", params={},
    ).result_set
    assert rows[0][0] >= 1, "the rebuild lost the healthy points"


# ── 4. the ENTITY families' MERGE keys (the second half of the class) ────
#
# Each of these folds MERGEs on a journal-derived key, and each guarded that
# key with TRUTHINESS — so a NUL-carrying value passed the guard, reached the
# MERGE, and (with the boundary correctly refusing to null a merge key) died in
# pass-1b AFTER the wipe. The fix is the same `_writable_id` gate the Point
# anchors use.

_FAMILY_RECORDS = [
    ("Subject", {"type": "SubjectAdded", "id": "sub-7369"},
     "name", "bad\x00name"),
    ("Object", {"type": "ObjectRegistered", "id": "obj-7369"},
     "name", "bad\x00name"),
    ("Source", {"type": "SourceCreated", "id": "src-7369"},
     "url", "bad\x00url"),
    ("Event", {"type": "EventRecorded", "event": {"id": "ev-7369"}},
     "event.id", "bad\x00ev"),
    # The NESTED keys `_event_plain_merge` merges on. The top-level-only
    # helper could not reach these, which is exactly the test gap that let
    # the first version of this fix ship a still-open class (#7369 review r3).
    ("Event.subject", {"type": "EventRecorded", "event": {"id": "ev-7369"}},
     "event.subject", "bad\x00subj"),
    ("Event.object", {"type": "EventRecorded", "event": {"id": "ev-7369"}},
     "event.object", "bad\x00obj"),
    ("Event.uses",
     {"type": "EventRecorded", "event": {"id": "ev-7369",
                                        "uses": [{"name": "ok"}]}},
     "event.uses[0].name", "bad\x00use"),
    ("Document", {"type": "DocumentCreated"}, "id", "bad\x00doc"),
    # `_upsert_document` forwards `source_url` verbatim to `link_source_to_entity`
    # (edges.py), which merges on it — the fourth round-3 miss.
    ("Document.source_url",
     {"type": "DocumentCreated", "id": "doc-src-7369", "title": "t"},
     "source_url", "bad\x00ref"),
]


def _poison_path(rec, key, val):
    """Set a possibly-NESTED ``key`` (``a.b[0].c``) on a record copy."""
    parts = key.split(".")

    def _step(node, part, create):
        """Descend one part, creating a {} or [] per a trailing ``[i]``."""
        idx = None
        if part.endswith("]"):
            part, _, rest = part.partition("[")
            idx = int(rest[:-1])
        if idx is None:
            return node.setdefault(part, {}) if create else node[part]
        seq = node.setdefault(part, []) if create else node[part]
        while len(seq) <= idx:
            seq.append({})
        return seq[idx]

    node = rec
    for part in parts[:-1]:
        node = _step(node, part, create=True)
    last = parts[-1]
    if last.endswith("]"):
        name, _, rest = last.partition("[")
        idx = int(rest[:-1])
        seq = node.setdefault(name, [])
        while len(seq) <= idx:
            seq.append({})
        seq[idx] = val
    else:
        node[last] = val
    return rec


@pytest.mark.parametrize("label,rec,key,val", _FAMILY_RECORDS)
def test_a_corrupt_entity_key_does_not_abort_the_rebuild(
        superseded, label, rec, key, val):
    """The identity route through the ENTITY folds, one case per key.

    Without the `_writable_id` guard each of these aborts pass-1b after the
    wipe; with it the record is skipped and the healthy points survive.
    """
    events, sdk, _old, _new = superseded
    rec = _poison_path(rec, key, val)
    (events / "events.jsonl").write_text(
        (events / "events.jsonl").read_text()
        + json.dumps(rec, ensure_ascii=False) + "\n"
    )

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)", params={},
    ).result_set
    assert rows[0][0] >= 1, (
        f"{label}: the rebuild lost the healthy points (aborted after the wipe?)"
    )


def test_two_rebuilds_of_a_poisoned_journal_agree(superseded):
    """Degrading must still be a FUNCTION of the journal."""
    events, sdk, old, _new = superseded
    _poison(events, "PointSuperseded", "valid_to", {"evil": 1})
    snapshots = []
    for _ in range(2):
        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        snapshots.append(sdk._get_proj().g.query(
            "MATCH (n:Point {id:$i}) RETURN n.status, n.outdated",
            params={"i": old},
        ).result_set)
    assert snapshots[0] == snapshots[1], snapshots


# ── the pass-1 stub auto-creation must not mint a NULL/empty-id node ─────
#
# #7369 review r5 (P1). `_create_edges` auto-creates a stub for a short source
# id that does not resolve. That `CREATE (s:Point {id:$sid})` passes `src` in a
# plain VALUE position — it is NOT a MERGE key, so the parameter boundary is
# free to degrade it. An unwritable id therefore reaches the driver as a bad
# identity, in one of two ways:
#
#   "\x00"  -> `_annotator_value_ok` refuses it, so the boundary DEGRADES it to
#              None, and the statement mints `CREATE (s:Point {id:null})` — a
#              node with no identity that no later MERGE can ever match, left
#              behind on every replay. This is the reported P1.
#   ""       -> `_annotator_value_ok("")` is True so the boundary leaves it
#              alone, and it mints `(:Point {id:""})` — an identity that is not
#              one (which is exactly why `_writable_id` adds `bool(val)`).
#
# `_journal_safe_params`'s own docstring states the rule for the first case —
# a null identity converts one abort into a DIFFERENT abort, so the FOLD must
# skip via `_writable_id` — and this was the fold site that did not.


class _MissingNodeDriver(_StrictDriver):
    """``_StrictDriver`` whose existence probe answers "the node is missing"."""

    class _Result:
        result_set: ClassVar[list[list[object]]] = [[False]]

    def query(self, cypher, params=None, timeout=None):
        super().query(cypher, params=params, timeout=timeout)
        return self._Result()


def _create_edges_seen(operator, point_id="op-1"):
    from tortoise.projection.edges import _EdgeHandlers

    driver = _MissingNodeDriver()
    handler = _EdgeHandlers()
    handler.g = _guarded(driver)
    handler._create_edges({"id": point_id, "operator": operator})
    return driver.seen


@pytest.mark.parametrize(
    "label,bad",
    [
        ("NUL-bearing (degrades to None -> :Point {id:null})", "\x00"),
        ("empty string (-> :Point {id:''})", ""),
        ("lone surrogate (degrades to None)", "\ud800"),
    ],
)
def test_a_stub_is_never_minted_from_an_unwritable_id(label, bad):
    seen = _create_edges_seen({"op_type": "IMPL", "inputs": [bad]})
    stub_creates = [(c, p) for c, p in seen if "CREATE (s:Point" in c]
    assert stub_creates == [], (
        f"{label}: a stub was minted for an unwritable source id, so the "
        f"graph gains a node with no usable identity: {stub_creates!r}"
    )
    # ...and no edge is created to the source that was never materialised.
    assert [c for c, _ in seen if "MERGE" in c] == [], seen


def test_a_WRITABLE_short_stub_source_still_autocreates():
    """The new guard must not disable the #6713 stub path it sits next to."""
    seen = _create_edges_seen({"op_type": "IMPL", "inputs": ["7"]})
    stub_creates = [(c, p) for c, p in seen if "CREATE (s:Point" in c]
    assert len(stub_creates) == 1, seen
    assert stub_creates[0][1] == {"sid": "7"}, stub_creates[0]


# ── 5. every identity skip is OBSERVABLE (round-7 review, finding 2) ─────
#
# The boundary change turned a LOUD abort into a SKIP: before it, an unwritable
# identity raised out of pass-1a/pass-1b (no per-event try/except, so AFTER the
# wipe); after it, the fold drops the record. A drop nobody can see is
# indistinguishable from data loss to the operator, which is the risk this
# whole change exists to manage. `_log_identity_skip` is the ONE reporter, and
# these tests are what make the reporting falsifiable.

def test_an_unwritable_identity_skip_is_OBSERVABLE(caplog):
    """The reporter's contract: truthy-but-unwritable WARNs; ABSENT is silent.

    Both halves matter. A skip that does not log is invisible; an ABSENT
    identity (the folds default `name`/`subject`/`object` to `""` and skip
    those by design) is ordinary, so logging it would drown the corrupt case.
    """
    caplog.set_level(logging.WARNING)
    _log_identity_skip("Subject", "bad\x00name", "name (MERGE key)")
    assert "skipping Subject" in caplog.text, caplog.text
    assert "name (MERGE key)" in caplog.text, caplog.text

    caplog.clear()
    caplog.set_level(logging.WARNING)
    _log_identity_skip("Subject", "", "name (MERGE key)")
    _log_identity_skip("Subject", None, "name (MERGE key)")
    assert caplog.text == "", (
        "an ABSENT identity was logged as a skip — that is the healthy case "
        f"and it would drown the corrupt one: {caplog.text!r}"
    )


def test_a_corrupt_entity_identity_skip_is_LOGGED_on_rebuild(superseded, caplog):
    """End-to-end: the record is dropped AND an operator can see it dropped.

    `test_a_corrupt_entity_key_does_not_abort_the_rebuild` proves the rebuild
    SURVIVES; this proves the survival is not silent.
    """
    events, sdk, _old, _new = superseded
    label, rec, key, val = _FAMILY_RECORDS[0]  # Subject / name
    assert label == "Subject", label
    (events / "events.jsonl").write_text(
        (events / "events.jsonl").read_text()
        + json.dumps(_poison_path(rec, key, val), ensure_ascii=False) + "\n"
    )

    caplog.set_level(logging.WARNING)
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    assert "skipping Subject" in caplog.text, caplog.text
    assert "name (MERGE key)" in caplog.text, caplog.text


def test_an_unwritable_row_merge_key_is_skipped_by_the_fold_not_forwarded(caplog):
    """Finding 3: the `UNWIND $turns` row MERGE key is closed by the FOLD.

    The boundary must not null a row merge key (`{id: turn.id}`) — the engine
    refuses a null key — so forwarding it would hand the engine the same
    unparseable parameter, i.e. the original abort by a different route.
    `_write_capture_turns` is the ONE writer of that statement, and it skips the
    batch when the session id (from which every row id derives) is unwritable.
    """
    from tortoise import sdk as sdk_mod

    driver = _StrictDriver()
    proj = type("P", (), {"g": _guarded(driver)})()
    caplog.set_level(logging.WARNING)
    written = sdk_mod._write_capture_turns(
        proj, type("S", (), {})(), "bad\x00session",
        [{"role": "user", "content": "hi"}],
        now="2026-01-01T00:00:00+00:00",
        turn_embs=[None],
        texts_and_counts=(["hi"], {"user": 0}),
    )
    assert written == 0, written
    assert driver.seen == [], (
        "the turn statement was issued anyway, so an unwritable ROW merge key "
        f"reached the engine: {driver.seen!r}"
    )
    assert "capture turn batch" in caplog.text, caplog.text


def test_a_RESOLVED_source_key_is_guarded_too(monkeypatch, caplog):
    """Finding 1: `_upsert_source` must guard the graph-RESOLVED key.

    `url` is this statement's MERGE key, so `_journal_safe_params` forwards it
    by design; `resolve_source_key` returns the STORED `s.url` read back from
    the graph, so an unwritable stored url would reach the engine and abort
    after the wipe. The three sibling source writers (`_mint_source_stub`,
    `link_source_to_entity`, `_materialize_connector_source`) already guard the
    resolved key; this pins the fourth.
    """
    from tortoise.projection import entities as ent_mod

    monkeypatch.setattr(
        ent_mod, "resolve_source_key", lambda g, url: "bad\x00resolved")

    driver = _StrictDriver()
    stub = type("P", (), {"g": _guarded(driver)})()
    caplog.set_level(logging.WARNING)
    out = ent_mod._EntityHandlers._upsert_source(
        stub, {"type": "SourceCreated", "id": "src-7369", "url": "https://ok"})

    assert out is None, out
    assert driver.seen == [], (
        "the Source MERGE was issued with an unwritable RESOLVED key: "
        f"{driver.seen!r}"
    )
    assert "url (resolved MERGE key)" in caplog.text, caplog.text


def test_a_MISSING_id_is_not_reported_as_an_unwritable_name(caplog):
    """Round 9: the reporter must name the identity that ACTUALLY failed.

    `_upsert_subject`/`_upsert_object` guard `if not sid or not _writable_id(name)`.
    Handing the reporter only `name` on that COMBINED condition made a record
    with a missing `id` and a healthy `name` log `skipping Subject — name
    (MERGE key) 'Alice' is not a writable identity` — false, since
    `_writable_id('Alice')` is True. An absent identity is not an anomaly
    (those records were skipped silently before this change); only the corrupt
    MERGE key is, so the conditions are split.
    """
    from tortoise.projection import entities as ent_mod

    handler = ent_mod._EntityHandlers()
    caplog.set_level(logging.WARNING)
    # Missing `id`, healthy `name`: skipped, and NOTHING is logged.
    handler._upsert_subject({"name": "Alice"})
    handler._upsert_object({"name": "Alice"})
    assert caplog.text == "", (
        f"a healthy name was reported as unwritable: {caplog.text!r}"
    )

    caplog.clear()
    caplog.set_level(logging.WARNING)
    # A present id but a corrupt MERGE key: the corrupt case IS reported.
    handler._upsert_subject({"id": "s-1", "name": "bad\x00name"})
    assert "skipping Subject" in caplog.text, caplog.text
    assert "name (MERGE key)" in caplog.text, caplog.text


def test_the_clause_classifier_PINS_each_round7_and_8_fix():
    """Every behaviour those fixes changed, ASSERTED — not merely probed by hand.

    Round 9 found the file asserted none of them: reverting the whole commit
    left every existing assertion green. Each block below fails on the pre-fix
    code.
    """
    # A backtick-quoted WRITE procedure is still a write — the dangerous
    # direction, where classifying it as a read forwards a map into a property
    # position and the engine rejects it AFTER the wipe.
    assert (
        _statement_writes("CALL `db.idx.fulltext.createNodeIndex`('P','e','t')")
        is True
    )
    # The index writers, the apoc writers that store, and bare DDL.
    for stmt in (
        "CALL db.idx.vector.createNodeIndex('P','e',3,'HNSW')",
        "CALL db.idx.fulltext.drop('Point')",
        "CALL apoc.trigger.add()",
        "CALL apoc.config.set()",
        "CALL apoc.cypher.doIt('CREATE (n) SET n.x=$m', {})",
        "CALL apoc.cypher.runMany('CREATE (n) SET n.x=1', {})",
        "CALL apoc.atomic.add(n,'p',1)",
        "CALL dbms.setConfigValue()",
        "DROP INDEX ON :Point(embedding)",
    ):
        assert _statement_writes(stmt) is True, stmt
    # ...while the READ-ONLY procedures, subqueries and keyword-named
    # properties are not writes — nulling a map there is the #7174 false
    # refusal this whole exemption exists to remove.
    for stmt in (
        "CALL db.idx.vector.queryNodes('P','e',5)",
        "CALL db.idx.fulltext.queryNodes('P','q')",
        "CALL apoc.load.json()",
        "MATCH (n) CALL { WITH n RETURN n } RETURN n",
        "MATCH (n) WHERE n.set = $m RETURN n",
        "MATCH (n) WHERE n.drop = $m RETURN n",
        "MATCH (n) WHERE n.x = $SET RETURN n",
        "MATCH (n {set: $m}) RETURN n",
        "MATCH (n:SET) RETURN n",
    ):
        assert _statement_writes(stmt) is False, stmt
        # ...and the map riding on it survives, end to end.
        assert _journal_safe_params({"m": {"a": 1}}, stmt)["m"] == {"a": 1}, stmt
    # A write keyword inside a LITERAL does not make a read a write.
    assert _statement_writes("MATCH (n) WHERE n.s='SET' RETURN n") is False
    # THE DEPTH BOUND IS A BOUND ON CONTAINERS, NOT ON SCALARS. A scalar leaf AT
    # the bound is parsed exactly like one at the top — the sibling predicate
    # `_is_persistable_prop_value` agrees — so it must still be forwarded.
    #
    # Asserted on the PREDICATE, NOT through `_journal_safe_params`: a nested list
    # of SCALARS is short-circuited by `_annotator_value_ok` before the predicate
    # is ever consulted, so routing it through the gate passes with the bound
    # reverted and pins nothing. That is how the first version of this block was
    # born inert (round 10).
    deep: object = "leaf"
    for _ in range(32):
        deep = [deep]
    assert _writable_at_parse(deep) is True  # 32 container levels, scalar leaf
    # With the guard on ENTRY this was False — the off-by-one: the scalar call at
    # depth 32 refused a leaf the engine parses like any other.
    assert _writable_at_parse([deep]) is False  # ...one more and the bound bites
