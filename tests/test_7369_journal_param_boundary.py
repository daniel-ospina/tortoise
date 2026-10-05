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
import math
import pathlib

import pytest

from tortoise.projection import _GuardedGraph, _journal_safe_params
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
    driver = _StrictDriver()
    g = _guarded(driver)
    call = getattr(g, verb)
    out = call("MATCH (n:Point) SET n.v=$v RETURN n",
               params={"v": {"evil": 1}})
    assert out == "ok", out


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
    events, sdk, old, _new = superseded
    assert _poison(events, "PointAdded", "point.content", {"evil": 1}) >= 1

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)

    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) RETURN count(n)", params={},
    ).result_set
    assert rows[0][0] >= 1, "the rebuild did not re-materialise any point"


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
