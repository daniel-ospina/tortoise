"""#14011 — the per-node USAGE signal (the input to usage-recency eviction).

The owner's criterion (`#13989`, 2026-11-20): *"2 criteria should be recency of
usage (searched), not recency of creation."*

⛔ **WHY THESE TESTS ARE NOT DRIVEN OFF `tortoise/read_ledger.py`.**
`docs/architecture/SEARCH-ARCHITECTURE.md` §7 says the counter an eviction score
consumes must be *"WINDOW-LOCAL cache state — a different artifact from
`tortoise/read_ledger.py`"*, and `STORAGE-ARCHITECTURE.md` §17.4.6 says reading
that ledger for any product decision *"would be a violation"*. The ledger appears
in this file in exactly one place — ``test_ordering_matches_the_ledgers_own_record``
— where it is the **INSTRUMENT that validates the counter**, which is what an
instrument is for. That test is #14011's own "the ordering it produces for a
scripted search pattern matches the ledger's record of that pattern".

MUTATION PROOF — each guard below has been shown to MOVE (revert the guard, the
named test goes RED):

* **Re-read the whole stream on every refresh** (ignore the checkpoint offset) →
  ``test_incremental_reads_only_the_new_bytes`` RED — the bound #14011 states.
* **Let the checkpoint be authoritative** (accept an offset whose prefix no
  longer matches) → ``test_a_replaced_stream_is_replayed_not_merged`` RED.
* **Consume a torn tail** (drop the last-newline cut) → ``test_a_torn_tail_is_
  folded_exactly_once`` RED — the fragment is counted once as garbage and the
  completed line never lands.
* **Fold an unserved key in as "coldest"** →
  ``test_unserved_keys_are_not_returned_as_coldest`` RED — that would re-impose
  creation recency, the policy the owner's criterion replaces.
* **Treat an UNDATED serve as the oldest** →
  ``test_an_undated_entry_sorts_warmest`` RED — one bad ``at`` would send the
  hottest node to the front of the eviction queue.
* **Read a capped stream as a quiet one** → ``test_a_capped_stream_is_reported``
  RED — the failure mode §7's "different artifact" rule exists to prevent. The
  door must also REFUSE: ``test_a_capped_counter_refuses_to_order``.
* **Order a POOLED roll-up** → ``test_a_pooled_rollup_refuses_to_order`` RED —
  one tenant's node would look hot because another tenant read it.
* **Report an empty stream as a fresh arming** →
  ``test_an_empty_stream_is_not_a_confident_zero`` RED — after a `copytruncate`
  every node is "unserved" and the caller silently reverts to creation recency.
* **Accept a checkpoint that disables its own prefix check** (``head_len = 0``) →
  ``test_a_forged_checkpoint_cannot_disable_the_prefix_check`` RED.
* **Prune only the entries that carry a time** (the second cut) →
  ``test_pruning_ages_an_entry_with_no_time_too`` RED — that class aged by no
  path at all, so the roll-up grew unbounded at every window while the reading
  still advertised a bound.
* **Read a window of ``0`` as "no window"** →
  ``test_a_window_that_cannot_bound_is_refused_loudly`` RED — one parameter whose
  whole job is bounding memory may not be silently ignored.
* **Let a non-UTF-8 checkpoint escape ``refresh``** →
  ``test_a_corrupt_checkpoint_is_a_cache_miss`` RED — ``refresh`` promises it
  never raises, and a corrupt cache is a MISS.
* **Name a module that does not exist in the forbidden set** (so the guard reads
  as protecting something) → ``test_the_no_graph_guard_names_modules_that_EXIST``
  RED; and **miss the module that owns the graph client** (``.projection``) →
  ``test_the_no_graph_guard_covers_every_module_that_reaches_a_store`` RED.
* **Scan the tree for one store root only** (the original ``"from falkordb import"
  in source`` substring) → ``test_the_store_reach_scan_sees_the_REDIS_spelling_too``
  RED, while ``…_covers_every_module_that_reaches_a_store`` stays GREEN — measured
  with the predicate narrowed to ``falkordb`` alone. The ``falkordb``-only witness
  (``cypher_guard``) reddens the symmetric case, dropping that root, which the
  coverage row cannot see because its importers are already banned.
* **Leave the ATTRIBUTE spelling out of the reachability scan** (drop
  ``_package_root_attributes`` from ``_module_reaches_a_store``) →
  ``test_the_store_reach_scan_sees_the_REDIS_spelling_too`` RED on the synthetic
  attr-only module — measured at head ``88e771ab`` on the **102** tests this file
  collects: **2 failed / 100 passed**. The second red is
  ``…_pins_the_package_root_EXPORT_form``, which calls the same predicate, so a
  ``1 failed`` figure hid a second gate firing. The coverage row stays GREEN,
  because no tree module reaches a store ONLY that way (the leg flags 0 of 305
  files) — that is the gap the two synthetic pins exist to fill. The ban already
  saw that spelling, so the two directions disagreed about one file — the same
  defect as the substring, one spelling over.
* **Forget a root in the ban floor** (drop ``redislite`` from
  ``_FORBIDDEN_GRAPH_ROOTS``) →
  ``test_the_ban_covers_all_three_roots_the_tree_actually_uses`` RED —
  measured, together with the scan row whose ``"tortoise"`` witness reaches the
  client through ``redislite.falkordb_client``. The parametrized row cannot see it
  (removing an entry removes its own case), which is why this floor exists: it is
  the recorded decision that none of the three MEASURED roots may be dropped.
* **Let the package-root EXPORT through** (drop the
  ``_FORBIDDEN_CLIENT_EXPORTS`` intersection from ``_offending``) →
  ``test_no_graph_dependency_pins_the_package_root_EXPORT_form`` RED on
  ``from tortoise import FalkorDB``; drop ``_package_root_attributes`` from the
  same union → RED on ``import tortoise`` + ``tortoise.FalkorDB``; disable its
  rebinding fixpoint → RED on ``f = tortoise``. All three measured.
* **Leave the ``from tortoise import __init__`` binding out of the attribute leg**
  (drop the ``ast.ImportFrom`` branch from ``_package_root_attributes``) →
  ``test_no_graph_dependency_pins_the_package_root_EXPORT_form`` RED on
  ``from tortoise import __init__ as i`` + ``i.FalkorDB`` — measured at head
  ``88e771ab``, same 102 collected: 1 failed / 101 passed, every other row green.
  ``_imports_of`` folds ``tortoise.__init__`` onto the UNBANNED root, so this was
  the one spelling of the package root no leg saw, in the ban and in the direction
  scan alike. ⚠️ In the ORDINARY case the LEG is a fail-closed over-report: at
  runtime `from tortoise import __init__` binds a `method-wrapper`, not the module,
  so the read raises — but AFTER an explicit `import tortoise.__init__` the bound
  name is that submodule's module and the read really does reach the guarded client
  (§ `_package_root_attributes`).
* **Bind only the ABSOLUTE ``from tortoise import __init__`` and leave the
  relative spelling out** (drop the ``relative_root`` arm of the ``ast.ImportFrom``
  branch in ``_package_root_attributes``) →
  ``test_no_graph_dependency_pins_the_package_root_EXPORT_form`` RED on
  ``from . import __init__ as i`` + ``i.FalkorDB`` — measured at head ``88e771ab``,
  same 102 collected: 1 failed / 101 passed. ``ast`` records the relative form as
  ``module=None, level=1``, so there is no module STRING to compare and an
  absolute-only branch cannot see it;
  ``_imports_of`` folds ``tortoise.__init__`` onto the unbanned package root, so
  the ban AND the direction scan were both green on this spelling. ⚠️ Over-report
  at runtime, same as the row above: the bound name is a `method-wrapper` unless
  `tortoise.__init__` was imported first.
* **Read the relative arm as ``level == 1`` only** (narrow ``relative_root``, so
  ``from .. import __init__`` falls out) →
  ``test_no_graph_dependency_pins_the_package_root_EXPORT_form`` RED on the
  one-dot-further spelling — measured, 101 passed with the pin absent, so the
  ``level >= 1`` reading was a claim no row held.
* **Let the scan stop calling the pinned loop** (restore the pre-fix two-leg body
  inside ``_modules_reaching_a_store``, leaving ``_scan_modules`` intact and
  exercised) → ``test_the_scan_actually_calls_the_pinned_loop`` RED — measured at
  head ``1f37c999``: **101 passed** without the pin, i.e. the wiring was invisible
  to the row that fed the loop.
* **Keep every module the loop is fed** (body → ``frozenset(module for module, _ in
  sources)``) → ``test_the_store_reach_scan_sees_the_REDIS_spelling_too`` RED on its
  loop pin — measured, and it reddens THAT pin only because the pin's input carries
  a clean pair beside the reaching one. Fed a single reaching pair (as it was
  before the control was added) the same mutant gave **1 failed, 101 passed**, the
  one red being the coverage row — a different gate, several hundred lines up.
* **Leave the ban auditing nothing** (``source = ""``, ``source = "# clean\n"``, a
  misdirected path or a cached constant, all of which left every row green while
  the ban's ~20 synthetic pins fed ``_offending`` literals) →
  ``test_no_graph_dependency`` RED on its two provenance asserts, which compare the
  string it audits against a fresh read of the module it names.
* **Quote a stale count beside the ban's provenance claim** (``208 of the 305``
  modules clean) → no row can redden a prose figure, which is why the count is
  gone instead: measured at ``1f37c999`` the tree has 305 modules and ``_offending``
  finds **228** clean (77 offending), so the quoted figure was already false.
* **Exempt a banned module from the probe into a BAN exemption** (add a non-banned
  name to ``_PROBE_EXEMPT``, or a real importer to ``_COVERAGE_EXEMPT``) →
  ``test_the_no_graph_guard_covers_every_module_that_reaches_a_store`` RED —
  measured, both levers.
* **Open the door on a roll-up the window emptied** (drop ``not self.entries`` from
  :attr:`UsageReading.orderable`) →
  ``test_a_window_that_prunes_everything_refuses`` RED. ``served_total`` is
  CUMULATIVE, so it cannot see a roll-up ``prune()`` has emptied.
* **Certify a coordinate as unserved after pruning** (drop ``or self.pruned`` from
  :meth:`UsageReading.unserved_keys`) →
  ``test_a_pruned_rollup_refuses_to_certify_any_key_as_unserved`` RED — pruning
  deletes the entry and leaves nothing to tell "pruned" from "never served".
* **Trust a checkpoint written by the pre-window code** (``CHECKPOINT_VERSION`` back
  to 1) → ``test_a_pre_fix_checkpoint_is_a_cache_miss_so_the_fix_is_retroactive``
  RED — the missing ``window_days`` field read as ``None`` and MATCHED an unbounded
  caller, so the upgrade inherited a fold another window had destroyed.
* **Re-open the path to decide ``capped``** (use ``_tail_is_capped`` in ``refresh``)
  → ``test_the_capped_verdict_is_derived_from_the_read_descriptor`` RED — the fold
  would be certified by a file the refresh never read.
"""

from __future__ import annotations

import ast
import functools
import json
import subprocess
import sys
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tortoise import read_ledger, usage_signal


@pytest.fixture
def stream(tmp_path: Path) -> Path:
    return tmp_path / "usage.jsonl"


def _at(hour: int) -> str:
    return f"2026-01-01T{hour:02d}:00:00+00:00"


def _serve(stream: Path, label: str, keys: list[str], *, at: str) -> int:
    return usage_signal.record_serves(
        label, [{"id": k} for k in keys], at=at, path=stream
    )


def _serve_row(stream: Path, label: str, key: str, *, at: str) -> int:
    """Serve ONE row shaped by its label's own identity contract."""
    field_name = read_ledger.KEY_FIELDS.get(label, "id")
    return usage_signal.record_serves(
        label, [{field_name: key}], at=at, path=stream
    )


# ══════════════════════════════════════════════════════════════════════════
# The signal itself: (key -> last served, serve count)
# ══════════════════════════════════════════════════════════════════════════


def test_a_serve_is_recorded_as_a_last_time_and_a_count(stream: Path) -> None:
    _serve(stream, "Point", ["p1", "p2"], at=_at(1))
    _serve(stream, "Point", ["p1"], at=_at(5))

    got = usage_signal.refresh(stream)

    assert got.serve_count("Point", "p1") == 2
    assert got.last_served("Point", "p1") == _at(5)
    assert got.serve_count("Point", "p2") == 1
    assert got.last_served("Point", "p2") == _at(1)
    assert got.served_total == 3
    # DERIVED, NEVER AUTHORITATIVE — the reading says so on its face.
    assert got.capped is False
    assert got.error is None


def test_the_coordinate_keeps_the_label(stream: Path) -> None:
    """Two labels sharing a key string must not collapse into one row.

    `Subject`/`Object` are both keyed by `id` since #9881 and both can hold the
    same string, so a key-only counter would merge them and score one node's
    usage against another's.
    """
    _serve(stream, "Subject", ["acme"], at=_at(1))
    _serve(stream, "Object", ["acme"], at=_at(4))

    got = usage_signal.refresh(stream)

    assert got.serve_count("Subject", "acme") == 1
    assert got.serve_count("Object", "acme") == 1
    assert got.last_served("Subject", "acme") == _at(1)
    assert got.last_served("Object", "acme") == _at(4)
    assert len(got.entries) == 2


def test_a_row_without_its_contract_key_is_named_not_absorbed(stream: Path) -> None:
    """An unaddressable row is a hole in the counter, and the hole is reported."""
    usage_signal.record_serves(
        "Source", [{"sourcePath": "/a"}, {"url": "https://b"}], at=_at(1), path=stream
    )

    got = usage_signal.refresh(stream)

    assert [e.key for e in got.entries.values()] == ["https://b"]
    assert got.unresolved_skips == 1


def test_an_undated_serve_counts_but_cannot_move_a_time(stream: Path) -> None:
    """A serve that happened but cannot be placed in time is counted, and named.

    ⛔ It may NOT move `last_served_at`: a fabricated instant is worse than an
    old one, because the scorer acts on it.
    """
    _serve(stream, "Point", ["p1"], at=_at(3))
    with open(stream, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"event": "serve", "label": "Point", "key": "p1"}) + "\n"
        )

    got = usage_signal.refresh(stream)

    assert got.serve_count("Point", "p1") == 2
    assert got.last_served("Point", "p1") == _at(3)
    assert got.undated_serves == 1


# ══════════════════════════════════════════════════════════════════════════
# #14011's "BOUNDED, incremental roll-up"
# ══════════════════════════════════════════════════════════════════════════


def test_incremental_reads_only_the_new_bytes(stream: Path) -> None:
    """A refresh after a serve folds ONE line, not the whole history.

    This is the bound the issue states: *"a bounded roll-up, so a query does not
    replay the whole ledger"*.
    """
    for hour in range(1, 11):
        _serve(stream, "Point", [f"p{hour}"], at=_at(hour))

    first = usage_signal.refresh(stream)
    assert first.replayed is True
    assert first.lines_read == 10

    _serve(stream, "Point", ["p99"], at=_at(11))
    second = usage_signal.refresh(stream)

    # The checkpoint carried the first 10 lines; only the 11th was folded.
    assert second.replayed is False
    assert second.lines_read == 11
    assert second.offset > first.offset
    assert second.serve_count("Point", "p99") == 1
    assert second.serve_count("Point", "p1") == 1


def test_incremental_equals_cold_replay(stream: Path) -> None:
    """The checkpoint is a CACHE: deleting it must reproduce the same reading.

    This is what "derived, never authoritative" means as an executable claim —
    and it is #14011's "recomputable from cold".
    """
    _serve(stream, "Point", ["p1", "p2"], at=_at(1))
    _serve(stream, "Object", ["o1"], at=_at(2))
    _serve(stream, "Point", ["p1"], at=_at(3))
    _serve_row(stream, "Event", "e1", at=_at(4))

    incremental = usage_signal.refresh(stream)
    snapshot = {
        "entries": {k: v.as_dict() for k, v in incremental.entries.items()},
        "served_total": incremental.served_total,
        "offset": incremental.offset,
        "capped": incremental.capped,
    }

    usage_signal.checkpoint_path(stream).unlink()
    replayed = usage_signal.refresh(stream)

    assert replayed.replayed is True
    assert {
        "entries": {k: v.as_dict() for k, v in replayed.entries.items()},
        "served_total": replayed.served_total,
        "offset": replayed.offset,
        "capped": replayed.capped,
    } == snapshot


def test_restart_survives_on_the_checkpoint_alone(stream: Path) -> None:
    """A SECOND PROCESS reading the same files sees the same signal.

    "Survives a restart by replay" (#14011) — proven across a process boundary,
    not by re-calling a function in the same interpreter, because that is the
    thing a restart changes.
    """
    _serve(stream, "Point", ["p1", "p2"], at=_at(1))
    _serve(stream, "Point", ["p1"], at=_at(7))
    before = usage_signal.refresh(stream)

    code = (
        "import json,sys;"
        "from tortoise import usage_signal as u;"
        "r=u.refresh(__import__('pathlib').Path(sys.argv[1]));"
        "print(json.dumps({'p1c':r.serve_count('Point','p1'),"
        "'p1t':r.last_served('Point','p1'),'total':r.served_total,"
        "'replayed':r.replayed}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code, str(stream)],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        check=True,
    )
    got = json.loads(out.stdout.strip().splitlines()[-1])

    assert got["p1c"] == 2
    assert got["p1t"] == _at(7)
    assert got["total"] == before.served_total
    # ⚠️ `replayed` is TRUE in the child only if it could not use the checkpoint.
    # It must be FALSE — the child inherited a checkpoint and folded nothing new.
    assert got["replayed"] is False


def test_a_replaced_stream_is_replayed_not_merged(stream: Path) -> None:
    """A checkpoint whose consumed prefix no longer matches is a CACHE MISS.

    Folding new bytes onto another file's history is the one way this module
    could produce a WRONG ordering instead of an empty one.
    """
    _serve(stream, "Point", ["p1", "p2", "p3"], at=_at(1))
    usage_signal.refresh(stream)

    # Same length, different content — the size check alone would pass it.
    original = stream.read_text(encoding="utf-8")
    stream.write_text(original.replace("p1", "q1"), encoding="utf-8")

    got = usage_signal.refresh(stream)

    assert got.replayed is True
    assert got.serve_count("Point", "q1") == 1  # the file's actual content
    assert got.last_served("Point", "p1") is None


def test_a_truncated_stream_forces_a_cold_replay(stream: Path) -> None:
    """Rotation is a new window — the counter must not keep the old one."""
    _serve(stream, "Point", ["old"], at=_at(1))
    usage_signal.refresh(stream)

    stream.write_text(
        json.dumps(
            {"at": _at(9), "event": "serve", "label": "Point", "key": "new"}
        )
        + "\n",
        encoding="utf-8",
    )

    got = usage_signal.refresh(stream)

    assert got.replayed is True
    assert [e.key for e in got.entries.values()] == ["new"]


def test_a_torn_tail_is_folded_exactly_once(stream: Path) -> None:
    """A partial last line is left UNCONSUMED, then folded once when complete.

    Consuming it would count a fragment; skipping it forever would lose the
    serve. Both directions are wrong, and the first is silent.
    """
    _serve(stream, "Point", ["p1"], at=_at(1))
    usage_signal.refresh(stream)

    partial = json.dumps(
        {"at": _at(2), "event": "serve", "label": "Point", "key": "p2"}
    )[:40]
    with open(stream, "a", encoding="utf-8") as fh:
        fh.write(partial)

    torn = usage_signal.refresh(stream)
    assert torn.serve_count("Point", "p2") == 0
    assert torn.served_total == 1

    with open(stream, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps({"at": _at(2), "event": "serve", "label": "Point", "key": "p2"})[
                40:
            ]
            + "\n"
        )

    whole = usage_signal.refresh(stream)
    assert whole.serve_count("Point", "p2") == 1
    assert whole.served_total == 2

    again = usage_signal.refresh(stream)
    assert again.serve_count("Point", "p2") == 1, "folded twice"
    assert again.served_total == 2


def test_a_corrupt_checkpoint_is_a_cache_miss(stream: Path) -> None:
    """An unreadable checkpoint replays from cold — never a partial state.

    ⛔ TWO corruptions, not one. `{not json` is valid UTF-8, so it only reaches
    the `json.loads` handler — and that left the OTHER shape untested:
    `Path.read_text` raises **`UnicodeDecodeError`** on non-UTF-8 bytes, which is
    a `ValueError` raised BEFORE any parse, so an `except OSError` on the read let
    it escape `refresh`, the one function here that promises it never raises.
    """
    _serve(stream, "Point", ["p1"], at=_at(1))
    before = usage_signal.refresh(stream)
    ckpt = usage_signal.checkpoint_path(stream)

    for corruption in (b"{not json", b"\xff\xfe not utf8"):
        ckpt.write_bytes(corruption)

        after = usage_signal.refresh(stream)  # must not raise

        assert after.replayed is True
        assert after.serve_count("Point", "p1") == before.serve_count("Point", "p1")


def test_a_capped_stream_is_reported(stream: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """⛔ A capped counter must never read as a quiet one.

    This is the exact failure §7's "different artifact" rule exists to prevent:
    a frozen counter ages every recently-served node into "cold", and an
    eviction pass would then delete the HOTTEST nodes.
    """
    monkeypatch.setenv(usage_signal.USAGE_MAX_BYTES_ENV, "600")
    usage_signal._warned.clear()

    _serve(stream, "Point", ["p1", "p2"], at=_at(1))
    for hour in range(2, 30):  # enough to cross the bound
        _serve(stream, "Point", [f"p{hour}"], at=_at(hour % 24))

    got = usage_signal.refresh(stream)

    assert got.capped is True, "the truncation is invisible — the scorer would act on a frozen counter"


def test_a_missing_stream_is_not_a_confident_zero(tmp_path: Path) -> None:
    got = usage_signal.refresh(tmp_path / "nope.jsonl")

    assert got.served_total == 0
    assert got.stale is True
    assert got.error == "no stream"
    assert got.entries == {}


# ══════════════════════════════════════════════════════════════════════════
# The ORDERING the scorer consumes
# ══════════════════════════════════════════════════════════════════════════


def test_coldest_first_is_oldest_last_served_first(stream: Path) -> None:
    _serve(stream, "Point", ["oldest"], at=_at(1))
    _serve(stream, "Point", ["middle"], at=_at(5))
    _serve(stream, "Point", ["newest"], at=_at(9))

    got = usage_signal.refresh(stream)

    assert [k for _lbl, k in got.coldest_first("Point")] == [
        "oldest",
        "middle",
        "newest",
    ]


def test_coldest_first_is_stable_on_a_tie(stream: Path) -> None:
    """Two coordinates last served at the same instant order by count, then key.

    Stability is the requirement: two passes over the same stream must agree, or
    an eviction batch is non-deterministic and its own re-measurement disagrees
    with what it just did.
    """
    _serve(stream, "Point", ["b", "a"], at=_at(1))
    _serve(stream, "Point", ["a"], at=_at(1))
    _serve(stream, "Point", ["c"], at=_at(1))

    got = usage_signal.refresh(stream)

    assert [k for _lbl, k in got.coldest_first("Point")] == ["b", "c", "a"]
    # …and the same answer a second time, off the same checkpoint.
    assert got.coldest_first("Point") == usage_signal.refresh(stream).coldest_first(
        "Point"
    )


def test_unserved_keys_are_not_returned_as_coldest(stream: Path) -> None:
    """⛔ A key with no observed serve is NOT "the coldest thing there is".

    Folding it in would evict a node written a minute ago, i.e. re-impose the
    creation-recency policy the owner's criterion replaces. The scorer keeps its
    own tie-break for exactly this set.
    """
    _serve(stream, "Point", ["served"], at=_at(1))
    got = usage_signal.refresh(stream)

    order = got.coldest_first("Point")
    assert order == [("Point", "served")]
    assert got.unserved_keys(["brand-new", "served", "also-new"], label="Point") == [
        "brand-new",
        "also-new",
    ]


def test_coldest_first_narrows_to_one_population_and_a_candidate_set(
    stream: Path,
) -> None:
    _serve(stream, "Point", ["p1", "p2"], at=_at(1))
    _serve(stream, "Object", ["o1"], at=_at(2))

    got = usage_signal.refresh(stream)

    assert got.coldest_first("Object") == [("Object", "o1")]
    assert got.coldest_first("Point", keys=["p2"]) == [("Point", "p2")]


# ══════════════════════════════════════════════════════════════════════════
# The window bound on the ROLL-UP (opt-in; exact counts by default)
# ══════════════════════════════════════════════════════════════════════════


def test_pruning_bounds_the_rollup_and_names_the_watermark(stream: Path) -> None:
    _serve(stream, "Point", ["ancient"], at=_at(1))
    _serve(stream, "Point", ["recent"], at="2026-01-03T23:00:00+00:00")

    now = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)
    got = usage_signal.refresh(stream, now=now, window_days=2)

    assert got.entry("Point", "ancient") is None
    assert got.entry("Point", "recent") is not None
    assert got.pruned == 1
    assert got.pruned_before == _at(1)


def test_pruning_ages_an_entry_with_no_time_too(stream: Path) -> None:
    """⛔ THE BOUND MUST REACH EVERY ENTRY CLASS, or it is not a bound.

    An entry whose serves all carried an unparseable `at` is stored as
    `[None, None, n]`, and the first cut's `last_dt is not None and …` skipped it
    at EVERY window — so the roll-up grew without bound while the reading still
    printed `pruned`/`undated_serves` as counters. The bound failed OPEN and
    SILENTLY, on the one property it advertises.
    """
    _serve(stream, "Point", ["recent"], at="2026-01-03T23:00:00+00:00")
    with open(stream, "a", encoding="utf-8") as fh:
        for key in ("undated-a", "undated-b"):
            fh.write(
                json.dumps({"event": "serve", "label": "Point", "key": key}) + "\n"
            )

    now = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)
    got = usage_signal.refresh(stream, now=now, window_days=2)

    assert got.entry("Point", "undated-a") is None
    assert got.entry("Point", "undated-b") is None
    assert got.pruned_undated == 2
    # …and it is NOT reported as the dated watermark's doing: nothing places an
    # undated entry before `pruned_before`, so saying so would be a fabrication.
    assert got.pruned == 0
    assert got.pruned_before is None
    assert got.entry("Point", "recent") is not None


def test_a_window_that_cannot_bound_is_refused_loudly(
    stream: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """⛔ `=0` IS NOT "NO WINDOW" — the same refusal the typo arm makes.

    The parse-failure arm warns "A MISTYPED WINDOW IS NOT 'NO WINDOW'", and then
    the numeric arm returned `None` for `0` and every negative with no warning at
    all: the reading was byte-identical to *unconfigured* (`pruned=0`,
    `pruned_before=None`, `window_started=None`) while the roll-up ran unbounded.
    A non-finite value is worse still — `timedelta(days=nan)` RAISES, out of a
    function whose docstring says NEVER raises.
    """
    _serve(stream, "Point", ["ancient"], at=_at(1))
    now = datetime(2030, 1, 1, tzinfo=UTC)

    for raw in ("0", "-1", "nan", "inf"):
        usage_signal._warned.clear()
        caplog.clear()
        monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, raw)

        with caplog.at_level("WARNING", logger=usage_signal.logger.name):
            got = usage_signal.refresh(stream, now=now)

        assert got.window_started is None, f"{raw!r} silently became a window"
        assert got.pruned == 0
        assert any("UNBOUNDED" in r.getMessage() for r in caplog.records), (
            f"{raw!r} was ignored without a warning — the operator believes the "
            f"roll-up is bounded"
        )

    # …and the explicit argument is refused the same way, never raised out of.
    usage_signal._warned.clear()
    got = usage_signal.refresh(stream, now=now, window_days=float("nan"))
    assert got.window_started is None
    assert got.entry("Point", "ancient") is not None


def test_the_rollup_is_exact_by_default(stream: Path) -> None:
    """No window ⇒ no pruning ⇒ `serve_count` is the stream's own count."""
    _serve(stream, "Point", ["ancient"], at=_at(1))
    _serve(stream, "Point", ["recent"], at=_at(23))

    got = usage_signal.refresh(stream, now=datetime(2030, 1, 1, tzinfo=UTC))

    assert got.pruned == 0
    assert got.serve_count("Point", "ancient") == 1


# ══════════════════════════════════════════════════════════════════════════
# #14011's Done-when: the ordering matches the ledger's record of the pattern
# ══════════════════════════════════════════════════════════════════════════


def test_ordering_matches_the_ledgers_own_record(
    stream: Path, tmp_path: Path
) -> None:
    """A scripted search pattern, folded by BOTH — the orderings must agree.

    ⛔ The ledger is the INSTRUMENT here, not the product input. Feeding the same
    served rows to both artifacts and requiring the same order is what makes the
    counter trustworthy: the instrument's own record of the pattern is the
    oracle. (`SEARCH-ARCHITECTURE.md` §7 forbids the reverse direction — the
    product reading the ledger — not this one.)
    """
    ledger = tmp_path / "read_ledger.jsonl"
    pattern: list[tuple[str, str, str]] = [
        (_at(1), "Point", "p1"),
        (_at(1), "Point", "p2"),
        (_at(1), "Point", "p3"),
        (_at(3), "Object", "o1"),
        (_at(4), "Point", "p2"),
        (_at(6), "Point", "p4"),
        (_at(9), "Point", "p1"),
        (_at(9), "Event", "e1"),
    ]

    # Register the stock so the ledger's fold joins every read (a read it was
    # never told exists lands in `unregistered_reads` and carries no last time).
    for _at_ts, label, key in pattern:
        assert read_ledger.record_stock(label, key, path=ledger) is True

    # Replay the pattern through BOTH artifacts, one search at a time. Each row
    # is shaped by the label's OWN identity contract (`Event` keys on `eventId`,
    # `Source` on `url`) — a row carrying `id` for an `Event` is unaddressable to
    # both artifacts, which is a different test.
    for at_ts, label, key in pattern:
        assert read_ledger.record_read(label, key, at=at_ts, path=ledger) is True
        assert _serve_row(stream, label, key, at=at_ts) == 1

    folded = read_ledger.fold(ledger)
    # `coldest_first`'s own sort key: last served, then count, then the key. The
    # comparison is over THAT key, because that is the ordering being asserted.
    from_ledger = sorted(
        (_stamp(row.last_read_at), row.read_count, row.key)
        for row in folded.rows.values()
        if row.read_count
    )

    got = usage_signal.refresh(stream)
    # ⛔ COMPARE THE EMITTED ORDER, not a sorted multiset. Wrapping both sides in
    # `sorted(...)` makes the assertion blind to order — the exact property under
    # test — while reading as if it pinned it.
    emitted = [
        (_stamp(got.last_served(lbl, key)), got.serve_count(lbl, key), key)
        for lbl, key in got.coldest_first()
    ]

    assert emitted == from_ledger, (
        "the counter's ordering disagrees with the instrument's own record of the "
        "same search pattern"
    )


def _stamp(value: str | None) -> float:
    assert value is not None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


# ══════════════════════════════════════════════════════════════════════════
# ⛔ No write path touches a node
# ══════════════════════════════════════════════════════════════════════════


#: Package roots a serve counter may never import. A residency signal that can
#: reach one of these can write to the store it is bounding — the write
#: amplification `#14011` exists to avoid — so the reachability itself is banned.
#:
#: ⛔ THE LIST IS CHECKED IN BOTH DIRECTIONS, because it rotted in both:
#: `tortoise.manager` and `tortoise.graph_connection` DO NOT EXIST (the first cut
#: forbade two modules that are in no tree), while `tortoise.projection` — the
#: module that actually carries `from falkordb import FalkorDB` and what
#: `tortoise/sdk.py` itself reaches the client through — was absent, so
#: `from .projection import FalkorProjection` stayed GREEN. Two tests below hold
#: the two directions: every name resolves to a real module, and every module in
#: the tree that imports a store client is named here. Scope, stated rather than
#: implied: it covers DIRECT importers (the closure is not walked — this module
#: legitimately imports `read_ledger` for `_stamp`/`row_key`, and a closure would
#: have to forbid that too). ⛔ ONE MODULE IS OUT OF THE **PROBE'S** SCOPE AND STAYS
#: IN THE BAN: `tortoise.embedded_lifecycle` imports `redis`, but importing
#: `usage_signal` already pulls it in transitively (`tortoise/__init__.py` does
#: `from tortoise.embedded_lifecycle import atexit_fast_close` at module level), so
#: the subprocess probe — which asserts forbidden names are ABSENT from
#: `sys.modules` — could never name it. An earlier cut therefore dropped it from
#: the SET, which silently dropped the AST BAN too: a `from . import
#: embedded_lifecycle` added to `usage_signal` reddened nothing, through the one
#: module that owns the embedded store.
#:
#: ⛔ TWO EXEMPTIONS, TWO ROLES, TWO NAMES. An exemption is a way to switch a
#: guard OFF, so each one is scoped to the single guard it is justified for;
#: serving both roles from one list is how a coverage skip quietly becomes a ban
#: exemption. `_PROBE_EXEMPT` only ever removes a name from the subprocess probe's
#: list — every member must ALSO stay in `_FORBIDDEN_GRAPH_ROOTS`, so the AST ban
#: still bites it (asserted in the direction test below). `_COVERAGE_EXEMPT` only
#: ever excuses a module from the direction test's demand, and `tortoise` is its
#: sole member: the package root is in `sys.modules` by construction and is
#: deliberately NOT in the banned set, because banning the package would ban the
#: module itself.
_PROBE_EXEMPT = ("tortoise.embedded_lifecycle",)
_COVERAGE_EXEMPT = ("tortoise",)
_FORBIDDEN_GRAPH_ROOTS = (
    "falkordb",
    "redis",
    "redislite",
    "tortoise.__main__",
    "tortoise.acl_graph_users",
    "tortoise.backup",
    "tortoise.cypher_guard",
    "tortoise.embedded_lifecycle",
    "tortoise.ep",
    "tortoise.hosted_api",
    "tortoise.hosted_backup",
    "tortoise.projection",
    "tortoise.retry",
    "tortoise.sdk",
    "tortoise.session_indexer",
    "tortoise.tortoise_client",
)

#: ⛔ THE PACKAGE ROOT'S ATTRIBUTE FORM IS A STORE SPELLING THE SET CANNOT NAME.
#: `from tortoise import FalkorDB` is resolved by `_imports_of` to
#: `{tortoise, tortoise.FalkorDB}`, and neither name is a MODULE in the set above —
#: so a function-body import of it passed every leg: the AST ban (no match), the
#: subprocess probe (the import never executes at module scope, so `sys.modules`
#: stays clean) and the direction scan (no new module is created). It is a real
#: spelling: `tortoise/__init__.py` is a PEP-562 `__getattr__` that exports the
#: guarded redislite `FalkorDB` and `Tortoise` (→ `tortoise.sdk`), and its own
#: docstring names "code importing `tortoise.FalkorDB`" as the bypass surface.
#: A SEPARATE set, not entries in `_FORBIDDEN_GRAPH_ROOTS`:
#: `test_the_no_graph_guard_names_modules_that_EXIST` resolves every dotted entry
#: as a module path and would report these as missing.
#:
#: ⛔ AND THE `from … import` FORM IS NOT THE ONLY ONE. `_imports_of` resolves
#: `from tortoise import FalkorDB` to `tortoise.FalkorDB`, but `import tortoise`
#: followed by `tortoise.FalkorDB` yields only `tortoise` — the package root, which
#: is deliberately NOT banned (it is in `sys.modules` by construction). So the
#: ATTRIBUTE form was still invisible to every leg — the AST ban matched no name,
#: the probe sees it only when the read executes at module scope, and the direction
#: scan creates no new module — which is the same reach this set exists to close,
#: one spelling over. `_package_root_attributes` is the leg that closes it.
_FORBIDDEN_CLIENT_EXPORTS = frozenset({"tortoise.FalkorDB", "tortoise.Tortoise"})

#: Everything at the package root that reaches the store — the two export names
#: ABOVE and every banned module BELOW, because `tortoise.sdk` is reached the same
#: way `tortoise.FalkorDB` is: as an ATTRIBUTE of the imported package
#: (`import tortoise; tortoise.sdk`). Filtering the attribute leg to the export set
#: alone left `tortoise.sdk`, `tortoise.projection` and
#: `tortoise.embedded_lifecycle` invisible through that spelling — the same hole,
#: for the banned modules rather than the exports. (It is also live: `__init__.py`
#: imports `embedded_lifecycle` eagerly, so the attribute resolves with no other
#: import in the process.)
_REACHABLE_FROM_ROOT = frozenset(_FORBIDDEN_CLIENT_EXPORTS) | frozenset(
    _FORBIDDEN_GRAPH_ROOTS
)

#: The package roots that ARE a store client, DERIVED from the set above — the
#: comment here used to claim the two "cannot drift" while nothing compared them,
#: and they had already drifted twice (`falkordb` alone, then `+redis`, then a
#: hand-added `redislite`). Deriving fails CLOSED: a dotless non-store entry would
#: over-report into `uncovered` rather than go unseen.
_STORE_CLIENT_ROOTS = tuple(r for r in _FORBIDDEN_GRAPH_ROOTS if "." not in r)


def _imports_of(source: str) -> set[str]:
    """Every name this source imports or names through an imported module.

    ⛔ The name list is a SUPERSET that consumers match by PREFIX, so an entry that
    is not a module path is harmless: `from tortoise import sdk` has to record
    `tortoise.sdk` (a module) and `from tortoise import FalkorDB` has to record
    `tortoise.FalkorDB` (an export, not a module) — `_offending` then matches both
    against the two name sets, and so does the direction scan: it asks
    `_reaches_a_store_client`, which tests the dotless store roots AND the export
    names, so a module whose ONLY reach is `from tortoise import FalkorDB` is found
    there too. (An earlier version of this paragraph said the scan "only ever asks
    whether a name starts with a dotless store root, which no export can" — that
    stopped being true when the export half was added to the shared predicate, and
    the scan's own docstring said the opposite in the same file.)

    ⛔ The RELATIVE resolution is the other point. An earlier version of this guard
    walked `ast.ImportFrom` with `node.module` truthy and compared the leftover
    string, so `from . import manager` (module is None) was SKIPPED and
    `from .graph_connection import get_connection` recorded `.graph_connection`,
    which matched nothing in the forbidden set. Both spellings are the ones this
    package actually uses (`from . import read_ledger`,
    `from .config import resolve_db_path`), so the guard passed on the exact
    regression it existed for.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            # ⛔ `|=` and NOT `set.update(...)`: `update` is a RETIRED SDK name, and
            # `tools/sdk_privatise.py --check` counts every `<untyped>.update(...)`
            # as residue — the three calls in this helper moved its pinned residue
            # set off `SWEEP_RESIDUE` by +3 and reddened the `surface-guard` job
            # (non-required, so it blocks nothing and still costs every lane a red)
            # over a set in a TEST, i.e. a shape that has nothing to do with the
            # sweep this pin guards. The union is the same operation, so the pin
            # keeps meaning "a reference shape appeared" for real SDK spellings.
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                found.add(
                    node.module if node.level == 0 else f"tortoise.{node.module}"
                )
                # ⛔ `from tortoise import sdk` imports the SUBMODULE
                # `tortoise.sdk`; recording only `tortoise` left that spelling
                # invisible, which is a third way to reach a store client.
                # ⛔ THE ALIAS IS EMITTED FOR THE RELATIVE FORM TOO, and that is
                # not cosmetic: `from .__init__ import FalkorDB` resolves to
                # `tortoise.__init__`, which FOLDS onto the deliberately-unbanned
                # package root, so recording the module alone lost the export and
                # the spelling reached the client with every leg green. For every
                # other relative module the alias was redundant because the MODULE
                # itself is banned — that redundancy is exactly what hid this.
                prefix = "" if node.level == 0 else "tortoise."
                found |= {
                    f"{prefix}{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                }
                if any(alias.name == "*" for alias in node.names):
                    # ⛔ `from <pkg> import *` BINDS THE PACKAGE'S PUBLIC NAMES, and
                    # the bare `*` recorded above matches no root. For a root the
                    # guard deliberately does not ban (`tortoise` itself), that made
                    # the star form TRIPLY blind: the AST ban saw the literal `*`,
                    # the direction scan saw no store root (`_imports_of` contains
                    # none), and the probe cannot see `embedded_lifecycle` (it is
                    # probe-exempt AND already in `sys.modules`). Measured: a
                    # module-level `from tortoise import *` in the real
                    # `usage_signal.py` bound `embedded_lifecycle` — the module that
                    # owns the embedded store — with every row green. Emitting every
                    # reachable name under that prefix is a SUPERSET (a star import
                    # may bind fewer), which is this helper's stated contract and
                    # fails CLOSED.
                    #
                    # ⛔ AND THE `.__init__` SPELLING OF THAT STAR, absolute OR
                    # relative — the same "one spelling over" class #14118 and
                    # #14120 are both about. This arm used to require `level == 0`
                    # and used the RAW module, so `from tortoise.__init__ import *`
                    # derived `tortoise.__init__.` (no entry of
                    # `_REACHABLE_FROM_ROOT` starts with it) and
                    # `from .__init__ import *` never entered the arm at all. Both
                    # returned `{'tortoise.__init__'}`, which `_fold_init` folds onto
                    # the deliberately-unbanned root, so `_offending` was EMPTY and
                    # the direction scan saw no reach — while their non-star
                    # siblings are banned and pinned reachable. Measured before:
                    # both forms → offending=False. `_fold_init` is documented as
                    # "applied to every name this module derives"; folding here is
                    # what makes the package-root star the same spelling as the
                    # rest.
                    _star_module = (
                        node.module if node.level == 0 else f"tortoise.{node.module}"
                    )
                    star_prefix = f"{_fold_init(_star_module)}."
                    found |= {
                        name
                        for name in _REACHABLE_FROM_ROOT
                        if name.startswith(star_prefix)
                    }
            elif node.level:
                # `from . import x` — resolve each alias under the package.
                found |= {
                    f"tortoise.{alias.name}"
                    for alias in node.names
                    if alias.name != "*"
                }
                # ⛔ THE RELATIVE STAR IS THE SAME HOLE ONE SPELLING OVER, and it
                # cannot be reached by the absolute branch above because that one is
                # nested under `node.module is not None` — `from . import *` parses
                # as `ImportFrom(module=None, level=1)`, so it is the MISSING MODULE
                # STRING that routes it here, not the level. (Precision about which
                # gate was removed, because a comment that names the wrong one
                # protects this arm from deletion on a false premise — the class
                # #14118 item 2 is about: THIS arm never carried a level gate. The
                # `node.level == 0` gate that #14120 removed lived in the ABSOLUTE
                # branch's star sub-arm, `if node.level == 0 and any(a.name == "*" …)`
                # at `e022fc935b:876`, and #14120 folded it into the shared arm above.)
                # `from . import *` binds the
                # package's public names in a fresh interpreter (measured: 11 names,
                # `embedded_lifecycle` among them, which owns the embedded store),
                # while the literal `tortoise.*` alias matches no root, the scan sees
                # no store root, and the probe cannot see `embedded_lifecycle`. Fixed
                # the same way, and for the same reason: a superset, failing CLOSED.
                if any(alias.name == "*" for alias in node.names):
                    found |= {
                        name
                        for name in _REACHABLE_FROM_ROOT
                        if name.startswith("tortoise.")
                    }
    return found


def _package_root_attributes(source: str) -> set[str]:
    """`tortoise.FalkorDB`-style reads, resolved through the local names bound to
    the package root.

    ⛔ THE ATTRIBUTE FORM IS A SEPARATE SPELLING, NOT A VARIANT. `_imports_of`
    resolves `from tortoise import FalkorDB` to `tortoise.FalkorDB` and the set
    intersection catches it; `import tortoise` + `tortoise.FalkorDB` resolves to
    just `tortoise`, which is deliberately unbanned, so nothing caught it. Aliases
    are followed (`import tortoise as t` -> `t.FalkorDB`), because the alias is
    what a caller writes.

    Deliberately over-reports rather than under-reports: a local that shadows the
    package name can only produce a FALSE POSITIVE here, and a guard that reddens
    spuriously is recoverable while one that stays green is not.

    ⛔ THE ATTRIBUTE FORM LIVES HERE, NOT IN `_imports_of`. That helper answers a
    different question ("every name this source imports or names through an
    imported module" — a SUPERSET that deliberately includes export names, because
    `_offending`'s export intersection and the star handling need them). This one
    asks which names are BOUND to the package root. One owner per question, and
    deleting this leg reddens the export pin rather than nothing.

    ⛔ AND `from tortoise import __init__ as i` IS A SPELLING OF THE PACKAGE ROOT,
    and the leg reports it — but in the ORDINARY case that report is a FAIL-CLOSED
    OVER-REPORT, not a demonstrated reach. At runtime the unqualified binding is
    `tortoise.__init__` the MODULE's own `__init__` — `<method-wrapper '__init__' of
    module object …>` — and `i.FalkorDB` raises `AttributeError`. After an explicit
    `import tortoise.__init__` the parent's attribute IS a module and the read DOES
    return the guarded client, but that module is DISTINCT from the package
    (`sys.modules['tortoise.__init__'] is tortoise` → `False`). Both states measured
    on this tree at `e022fc935b`. The leg exists anyway because the name was the one
    package-root spelling `_imports_of` FOLDS onto the unbanned `tortoise` and no
    other loop read `ast.ImportFrom` at all, so — IN THAT ORDERING, after an
    explicit `import tortoise.__init__` — a caller writing this form would have
    reached the guarded client with every leg green. Reporting a name that is
    not the client unless the submodule was imported first is recoverable; staying
    silent on a spelling of the root is not — the same asymmetry this function's
    first paragraph sanctions.

    ⛔ AND THE RELATIVE SPELLING IS THE SAME BINDING IN THE AST, and the more
    natural one to write inside this package. `ast` records `from . import
    __init__` as `ImportFrom(module=None, level=1)` — there is no module STRING to
    compare, so an absolute-only branch can never see it, and the spelling sat in
    the hole the absolute fix had just closed one word over: this leg, `_offending`
    and `_module_reaches_a_store` were ALL silent on it. ⚠️ Same runtime caveat as
    the absolute form above — in the ordinary case the bound name is a
    `method-wrapper`, so this is
    another over-report, not a reach that was observed.
    ⚠️ OVER-REPORTS for a nested module, where `from . import __init__` names
    `tortoise.<sub>.__init__` and `from .. import __init__` names the root: the
    source string carries no package depth, so the two cannot be told apart here.
    Fail-closed, as the first paragraph sanctions, and measured to flag no file in
    the tree today.
    """
    tree = ast.parse(source)
    nodes = list(ast.walk(tree))
    bound: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "tortoise" or alias.name.startswith("tortoise."):
                    bound.add(alias.asname or "tortoise")
        elif isinstance(node, ast.ImportFrom):
            # ⛔ TWO SPELLINGS OF ONE BINDING, and the second has no module STRING
            # to match: `from tortoise import __init__` is `level=0` with
            # `module="tortoise"`, while `from . import __init__` is `level>=1`
            # with `module=None`. Keying on the module string alone left the
            # relative form — the one written INSIDE this package — invisible to
            # every leg (measured; see this function's docstring).
            absolute_root = node.level == 0 and node.module == "tortoise"
            relative_root = node.level >= 1 and node.module is None
            if absolute_root or relative_root:
                for alias in node.names:
                    if alias.name == "__init__":
                        bound.add(alias.asname or alias.name)
    # ⛔ FOLLOW THE REBINDING, not just the `import … as` hop. `t = tortoise` then
    # `t.FalkorDB` is the SAME spelling one assignment over, and a walk that stops
    # at the import statement records nothing for it — which is how closing the
    # alias hop can leave the identical hole one line further along. Iterated to a
    # FIXPOINT because the chain can be any length (`f = tortoise; g = f`).
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if isinstance(node, ast.Assign):
                targets: list[ast.expr] = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            else:
                continue
            if _dotted_base(node.value) in bound:
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in bound:
                        bound.add(target.id)
                        changed = True
    return {
        f"tortoise.{node.attr}"
        for node in nodes
        if isinstance(node, ast.Attribute)
        and _dotted_base(node.value) in bound
        and f"tortoise.{node.attr}" in _REACHABLE_FROM_ROOT
    } | _getattr_reaching_a_store(nodes, bound)


def _getattr_reaching_a_store(
    nodes: list[ast.AST], bound: set[str]
) -> set[str]:
    """`getattr(tortoise, "FalkorDB")` with BOTH arguments literal.

    ⛔ OUT-OF-SCOPE IS NOT THE SAME AS UNREACHABLE, and this one is statically
    decidable: the name is right there in a string constant. The scope note USED to
    say "a STATIC import is the only thing this guard can see", which left this
    spelling unseen while the code deliberately covered a non-import ATTRIBUTE
    read; the note is now widened to match the code. A non-literal second argument
    (`getattr(t, name)`) stays genuinely out of scope, and is named as such beside
    `importlib.import_module`/`__import__`.

    ⛔ THE BUILTIN `getattr` HAS NO EXHAUSTIVE KEYWORD FORM, BUT THE HOOK DOES.
    `getattr` accepts NO keyword arguments (`getattr(o, name="x")` raises
    `TypeError`, measured on 3.12), so for the BUILTIN the keyword spelling is not a
    reach this leg misses — it cannot execute at all. That reasoning does NOT carry
    to `tortoise.__getattr__`, which is `def __getattr__(name: str)` and therefore
    DOES accept `name=`, so the hook's keyword form is a real live reach and is
    read below alongside the positional one.

    ⚠️ **AND COVERAGE IS SYNTACTIC — stated, because it cannot be completed.** A
    `getattr`/hook reached through a local ALIAS (`f = getattr; f(tortoise, "X")`,
    `g = tortoise.__getattr__; g("X")`), through `operator.attrgetter("X")`, or via
    a direct `tortoise.__getattribute__("X")` call is NOT resolved here, and neither
    is any name assembled at runtime. Each of those is a literal string one
    indirection away and this walker does not follow indirections of that kind; the
    set of such spellings has no natural end, so the boundary is declared rather
    than chased. What IS guaranteed is the direct form, in every placement.
    """
    found: set[str] = set()
    for node in nodes:
        # ⛔ `tortoise.__getattr__("FalkorDB")` AND `tortoise.__getattr__(name=
        # "FalkorDB")` — the PEP-562 hook itself, called with a literal name. Same
        # reasoning as `getattr` above: the name is a string constant in the source,
        # and `tortoise.__getattr__` really is a module attribute that returns the
        # guarded client (`__init__.py:372`). The KEYWORD form is accepted because
        # the hook is `def __getattr__(name: str)` — unlike the builtin, it does not
        # reject it.
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "__getattr__"
            and _dotted_base(node.func.value) in bound
        ):
            argument = None
            if len(node.args) >= 1:
                argument = node.args[0]
            else:
                argument = next(
                    (
                        keyword.value
                        for keyword in node.keywords
                        if keyword.arg == "name"
                    ),
                    None,
                )
            if (
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            ):
                name = f"tortoise.{argument.value}"
                if name in _REACHABLE_FROM_ROOT:
                    found.add(name)
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
        ):
            continue
        base, attribute = node.args[0], node.args[1]
        if (
            _dotted_base(base) in bound
            and isinstance(attribute, ast.Constant)
            and isinstance(attribute.value, str)
        ):
            name = f"tortoise.{attribute.value}"
            if name in _REACHABLE_FROM_ROOT:
                found.add(name)
    return found


def _fold_init(name: str) -> str:
    """`tortoise.__init__.X` -> `tortoise.X`, `tortoise.__init__` -> `tortoise`.

    ⛔ `from tortoise.__init__ import FalkorDB` and `tortoise.__init__.FalkorDB` are
    the SAME reach as the two forms `_FORBIDDEN_CLIENT_EXPORTS` exists to catch, and
    they were missed by every leg: the resolved name is `tortoise.__init__.FalkorDB`,
    which matches no banned root and no export entry, the attribute leg only fired on
    `Attribute(value=Name)` so the chained base was invisible, and the probe is blind
    to the function-body placement (`redislite` is not loaded until the read runs).
    `tortoise.__init__` is a spelling of the package root, so the fold is applied to
    every name this module derives rather than to one call site.
    """
    return name.replace(".__init__", "")


def _dotted_base(source: ast.AST) -> str | None:
    """The dotted name an attribute read is rooted at, folded — or None.

    Follows a CHAIN (`tortoise.__init__.FalkorDB` roots at `tortoise`), which a
    reader that insists on `Attribute(value=Name)` cannot do.
    """
    parts: list[str] = []
    while isinstance(source, ast.Attribute):
        parts.append(source.attr)
        source = source.value
    if not isinstance(source, ast.Name):
        return None
    parts.append(source.id)
    return _fold_init(".".join(reversed(parts)))


def _offending(source: str) -> set[str]:
    """Every name in `source` that reaches a store client — modules AND exports."""
    imported = {_fold_init(name) for name in _imports_of(source)}
    return (
        {
            name
            for name in imported
            if any(
                name == root or name.startswith(root + ".")
                for root in _FORBIDDEN_GRAPH_ROOTS
            )
        }
        | (imported & _FORBIDDEN_CLIENT_EXPORTS)
        | _package_root_attributes(source)
    )


def test_no_graph_dependency() -> None:
    """The signal must not be able to reach a graph client, even by accident.

    A `last_searched_at` property written on every search is write amplification
    into the very store the window exists to bound (#14011), so this module is
    required to have no graph in reach.

    ⚠️ **SCOPE: a STATIC import or a literal ATTRIBUTE read is what this guard can
    see.** Every static spelling is resolved (`from . import x`, `from .mod import
    x`, `import a.b`, `from tortoise import FalkorDB`, `tortoise.FalkorDB` on the
    bound package name — aliases and rebindings followed — and any of these inside
    function bodies, since `ast.walk` descends). `importlib.import_module(...)`,
    `__import__(...)`, `getattr(t, name)` with a NON-literal name, and any name
    built at runtime (`tortoise.__dict__[...]`, `f"tortoise.{x}"`) would pass, and
    are OUT OF SCOPE rather than covered. ⛔ SO ARE THE REBINDING FORMS THE FIXPOINT
    DOES NOT WALK, which are STATIC and therefore easy to assume are covered: only
    `x = …` and `x: T = …` propagate the bound name, and the exceptions are listed
    as EXAMPLES rather than a closed set — a walrus (`if (t := tortoise): …`), a
    loop or `with … as` target, a TUPLE, LIST or STARRED target, a comprehension
    target, a `match` capture pattern, and a DEFAULT ARGUMENT
    (`def f(t=tortoise): return t.Tortoise`) all reach the client and are not seen.
    They are named because the clause above promises "aliases and rebindings
    followed", and a reader deciding sufficiency must not read that as every static
    form. (`getattr(tortoise, "FalkorDB")` with BOTH arguments literal IS covered —
    see `_getattr_reaching_a_store` — because that is statically decidable.)

    ⚠️ **ALSO OUT OF SCOPE, and easier to get wrong: the TRANSITIVE hop.** The ban
    is one-hop by design (the closure is not walked — walking it would forbid the
    legitimate `read_ledger` import too). So a module that reaches a store client
    only through a LAZY import inside a function body is not banned by name, and
    the subprocess probe cannot see it either, because the import never executes
    at module scope (`from . import analyze` is the measured shape:
    `tortoise/analyze.py` imports `tortoise.sdk` inside a function, and the probe's
    bad-list stays EMPTY). That residual is stated rather than closed, because
    closing it is a closure walk — a different design — not a spelling fix.

    Said out loud because this docstring is what a future reader consults to
    decide the guard is sufficient.
    """
    source = Path(usage_signal.__file__).read_text(encoding="utf-8")
    # ⛔ THE SUBJECT OF THIS BAN IS PINNED TOO. Every synthetic assert below feeds
    # `_offending` a LITERAL, so the one call that reads the real module could be
    # made to read nothing (`source = ""`, a wrong path, a cached constant) and
    # every row stayed green (measured: 101 passed) — the guard protecting
    # `usage_signal` itself, silently disabled, with nothing to notice it.
    assert source.strip() and usage_signal.__file__.endswith("usage_signal.py"), (
        f"the ban read {usage_signal.__file__!r} as "
        f"{len(source)} chars — this test's whole subject is that module's source, "
        "and an empty or misdirected read makes every assertion below vacuous")
    # ⛔ AND ITS PROVENANCE, not just its non-emptiness. The assert above closes the
    # EMPTY read only: `source = "# clean\n"` or a read of another tree module's
    # file left every row green (measured: 101 passed each), because MOST of the
    # tree's 305 modules `_offending` finds clean — the ban's own subject is the
    # exception, so an empty, cached or misdirected read certifies a file nobody
    # audited while the rows it feeds read green. No size figure is quoted here: the
    # one that used to sit here had already gone stale twice in this file's own
    # history — measured at the head it was removed from, the clean count was not the
    # number it stated — and a figure beside the property it is used to argue is a
    # fossil waiting to happen. So the ban compares the string it audits against a
    # fresh read of the file it claims to audit.
    assert source == Path(usage_signal.__file__).read_text(encoding="utf-8"), (
        "the ban must examine THIS module's CURRENT source: a cached constant or a "
        "misdirected read certifies a file the ban never looked at")
    offending = _offending(source)
    assert not offending, (
        f"usage_signal imports a graph client: {sorted(offending)} — a residency "
        f"signal may never write to the store it is bounding"
    )
    # Cheap transitive check: importing the module must not drag a store client in
    # through some other path the AST of one file cannot see.
    # ⚠️ What this can and cannot see: it observes what EXECUTES at import time. A
    # module reached only through a lazy import inside a function body loads
    # nothing here, so it is invisible to this leg — see the scope note in
    # `test_no_graph_dependency`, which states that residual rather than implying
    # the probe closes it.
    #
    # ⛔ IN A SUBPROCESS, not this one. `sys.modules` here is the WHOLE pytest
    # session's — the conftest and a hundred other tests import a store client long
    # before this line runs, so an in-process assertion is green or red depending on
    # test ORDER, i.e. it pins nothing. A clean interpreter is the only honest read.
    # ⚠️ The probe's own name list is DERIVED from `_FORBIDDEN_GRAPH_ROOTS`, not
    # restated: an earlier cut hardcoded two modules that are in NO tree
    # (`tortoise.manager`, `tortoise.graph_connection`) while omitting
    # `tortoise.projection` — the module that actually carries
    # `from falkordb import FalkorDB` — so the probe could not see the one path
    # that reaches the client. `test_the_no_graph_guard_names_modules_that_EXIST`
    # and `…_covers_every_module_that_reaches_a_store` hold the set in both
    # directions; this leg inherits whatever they certify.
    # ⛔ AND THE DERIVATION HAS ONE OWNER, PINNED. A derivation written inline in the
    # subprocess string can be NARROWED and every row stays green, because the
    # positive control below witnesses the `redis` root and a narrowing keeps it:
    # measured at `e022fc935b`, `forbidden={('falkordb', 'redis')!r}` (the very
    # hardcoded pair this comment says it replaced) and
    # `forbidden={tuple(_STORE_CLIENT_ROOTS)!r}` (dotless roots only) both left the
    # file at 102 passed, while a transitive pull of `tortoise.session_indexer` came
    # back empty under the dotless-only list — the module is pulled in and the leg
    # certifies nothing. So the helper is the one owner and the assert reads IT: an
    # assert against a fresh re-derivation would pin nothing, because the same
    # narrowing would move both sides.
    def _probe_forbidden() -> tuple[str, ...]:
        """The probe's name list: every banned root the probe can witness."""
        return tuple(r for r in _FORBIDDEN_GRAPH_ROOTS if r not in _PROBE_EXEMPT)

    assert set(_probe_forbidden()) == set(_FORBIDDEN_GRAPH_ROOTS) - set(_PROBE_EXEMPT), (
        f"the probe's derived list is {sorted(_probe_forbidden())} — it must be the "
        "ban set minus the probe exemptions, or the transitive leg certifies "
        "coverage it does not deliver")

    def _matching(modules: Iterable[str]) -> set[str]:
        """The ONE matcher: every module name that IS a banned root or is under one.

        ⛔ IT LIVES HERE, IN THE AUDITED FILE, and `_probe` calls it on what the
        child printed. That is what makes the predicate assertable at all (below):
        a matcher inside the child could only be graded through a RECORD of what it
        did, and every record-based cut on this PR was a round away from a green
        narrowing.
        """
        forbidden = _probe_forbidden()
        return {
            name
            for name in modules
            if any(name == root or name.startswith(root + ".") for root in forbidden)
        }

    def _probe_modules(import_stmt: str, *injected: str) -> list[str]:
        """The child's RAW report: every name in `sys.modules` after `import_stmt`.

        The child is the ONLY place this program is built, and it matches NOTHING —
        `_probe` hands what it printed to `_matching`. This helper is separate only so
        the third-root failure message can tell "the child never reported the name"
        apart from "the child reported it and the matcher dropped it", instead of
        naming one cause for all of them.
        """
        src = (
            "import sys;"
            f"{import_stmt};"
            # the ONLY way to make the name stream carry a name it could not otherwise
            # produce: supplied from outside, so the stream can be witnessed at EVERY
            # root without importing fifteen store clients
            "sys.modules.update({n: None for n in sys.argv[1:]});"
            "print(chr(31).join(sorted(sys.modules)))"
        )
        out = subprocess.run(
            [sys.executable, "-c", src, *injected],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).resolve().parent.parent),
            check=True,
        ).stdout.rstrip("\n")
        return [name for name in out.split(chr(31)) if name]

    def _probe(import_stmt: str, *injected: str) -> set[str]:
        """The banned roots present in `sys.modules` after `import_stmt`, in a CLEAN
        interpreter.

        ⛔ THE CHILD OBSERVES; THE PARENT DECIDES. The subprocess runs the import and
        prints `sys.modules`; every comparison against `_probe_forbidden()` happens
        here. Earlier cuts interpolated the list INTO the child program and had the
        child match, then emitted a RECORD of what it matched. The shapes those cuts
        left GREEN are listed ONCE, below, so this docstring does not grow two
        accounts of the same history. One hop has one place to look:
        the child's only job is to say what is in `sys.modules`, and a change to
        what it says reds (measured: printing a constant, printing only the direct
        import, and truncating `sys.modules` are each **1 failed / 101**).

        ⛔ THE DERIVATION STILL HAS ONE OWNER HERE, `_probe_forbidden()`, pinned by
        the assert above — so the DERIVATION cannot be narrowed without reddening a
        row (measured: dotless-only and the hardcoded pair are each 1 failed / 101).
        Its USE is pinned too, by a DIFFERENT row: the per-root loop below drives
        `_matching([root])` for every root in the derivation, so filtering the list
        `_matching` hands the comprehension — down to the three named controls, or by
        dropping a single root — is **1 failed / 101**, not a narrowing. The one
        filter no row sees is on `_matching`'s RETURNED set inside `_probe` (**102
        passed**), and it drops no banned ROOT: it keeps exact members of
        `_probe_forbidden()` and discards `<root>.<sub>` forms, while CPython always
        registers the parent module — so the submodule clause can never be the sole
        witness (see the prefix-clause note below). Separately, ADDING a name to
        `_PROBE_EXEMPT` leaves the derivation row above green, because it moves both
        sides of that equality together — which is why narrowing `_PROBE_EXEMPT` is
        pinned by `test_the_no_graph_guard_covers_every_module_that_reaches_a_store`
        and by the `_PROBE_EXEMPT == (…)` row, not by this one.

        ⛔ THE CHILD DOES NOT MATCH ANYTHING — the matching happens HERE, IN THE
        PARENT, against `_probe_forbidden()`. This is the structural fix for six
        rounds of findings, and the reason is worth stating exactly: every earlier
        cut put a MATCHER in the child and a RECORD of that matcher in the program's
        output, so each round closed one axis of that two-hop arrangement and left
        the next one open. Measured along the way, each **102 passed** while the
        guard certified a live miss (`import tortoise.projection` / `tortoise.sdk` /
        `tortoise.session_indexer` coming back empty):
          * a narrowed PROGRAM — the emitter building a child that matches a filtered
            list (`_probe_source()`);
          * a narrowed ITERABLE in the loop header / the interpolated list;
          * a narrowed PREDICATE — `and r in ('redis', 'tortoise.ep')`, and
            `and r != 'tortoise.projection'`, which excludes ONE non-control root
            and is therefore invisible to any control that only names the roots it
            keeps;
          * a `continue` after `walked.append(r)`, which records the visit and
            skips the match;
          * a `walked` list written outside the loop instead of observed by it.
        The last two are the tell: a record of what a matcher did is not the
        matcher, and an in-child assert can only ever check the record. So there is
        no child matcher and no record any more. The child prints `sys.modules`; the
        one matcher is in this file, in the DETECTOR — not in the module under audit.
        This test's subject is `usage_signal.py`'s source (see its provenance
        assertions above); the matcher is the EVIDENCE, so a change to it is a change
        to the evidence, which is exactly why the residual below has to exist.

        `\x1f` (unit separator) joins the module names because it cannot occur in a
        module name, and the read below is `rstrip("\n")`, NOT `strip()`: Python
        counts `\x1f` as whitespace (`'\x1f'.isspace()` is `True`, bidi class S), so
        a blanket `strip()` removes separator bytes at the ENDS of the output — the
        case that bites when a leading or trailing name is empty. ⚠️ At THIS head
        that trap is latent, not live: measured, `strip()` leaves the file at **102
        passed**, because every emitted name is non-empty and the separators are all
        interior. It was a live bug on the previous, record-based layout, where
        `strip()` made the `from tortoise import usage_signal` row report all fifteen
        roots as leaked. `rstrip("\n")` is kept because it is exactly what the
        output format needs, without claiming a failure this code no longer has.

        ⚠️ WHAT THIS STILL CANNOT WITNESS, stated instead of implied. The matcher is
        now IN THIS FILE, so it is asserted DIRECTLY: `_matching` is run over a
        one-name input for EVERY root in `_probe_forbidden()` — once as the exact
        name, once as `<root>.probe_child` — so a matcher restricted to any subset of
        the derivation (including `('redis','tortoise.ep',
        'tortoise.session_indexer')`, which measured **102 passed** at
        `9bc39f9dd4` and is **1 failed / 101** here) reds on every root it dropped.
        What remains outside any assert is a matcher that SPECIAL-CASES its input
        shape — behaving differently for the single names the assertions pass than
        for a real `sys.modules` listing — or one that predicts this test's own
        expectations. That is a rewrite of the SUBJECT, not a narrowing of it, and it
        is the structural reason this arrangement is worth preferring to a two-hop
        one where a narrowed child and a fabricated record could BOTH be green.
        ⛔ AND THE NAME STREAM IS WITNESSED AT EVERY ROOT TOO — `_probe` is run once
        with EVERY root supplied to the child, and the result must be the whole
        derivation. That closes both places the earlier version of this paragraph
        called unclosable, and each is measured RED now:
          * THE CHILD'S PRINT. A child narrowed to the roots the named controls use —
            `sorted(n for n in sys.modules if n in ('redis','tortoise.ep',
            'tortoise.session_indexer'))` — was **102 passed** before this row and is
            **1 failed / 101** after it, because the injected roots never reach the
            output.
          * THE PARENT'S PARSE. Suppressing one name in
            `[name for name in out.split(chr(31)) if name]` was **102 passed** and is
            **1 failed / 101** after it, on the suppressed root.
        The old objection — "an assert that reads the same output cannot tell a
        suppressed name from an absent one" — is true of an assert over an IMPORT, and
        false of an assert over an input the test supplies: the injected roots are
        known to be present, so their absence from the result is a defect either way.
        ⛔ AND THE WITNESS INPUTS ARE NOT THE ONLY EVIDENCE: every root is also
        IMPORTED for real, one subprocess each, with nothing injected, and the probe
        must report the module that import put in `sys.modules`. That row is what
        closes the keep-list class for good — a filter keyed on `set(injected)` (empty
        there) or on any fixed list of roots has to carry the WHOLE derivation, i.e.
        the whole ban set, which is no narrowing at all. Measured on the previous head:
        a six-literal keep-list (the three named controls plus `tortoise.cypher_guard`,
        `tortoise.projection`, `tortoise.retry`) was **102 passed** and left the probe
        blind to `tortoise.acl_graph_users` and `tortoise.hosted_backup`; it reds here.
        What remains is only PREDICTION of this test's inputs, the same residual as
        above, and a narrowed child now reds BY ITSELF instead of merely being
        undetectable.
        """
        # ⚠️ THE PREFIX CLAUSE `or n.startswith(r + '.')` IS DEFENSIVE, AND IT IS
        # NOW PINNED — this comment used to say "DELIBERATELY UNPINNED … deleting it
        # leaves the whole file at 102 passed", and the per-root submodule row added
        # below this docstring made that FALSE: measured at `904a9429` (the commit
        # that added the row), deleting the clause is **1 failed / 101 passed**, the
        # failure at exactly that row. That is the #14118 item 2 class one turn on —
        # a comment naming the wrong reason is what lets a load-bearing line be
        # deleted on a false premise — so the clause is described by what it is now:
        # pinned, for EVERY root, by `_matching([_root + '.probe_child'])`.
        #
        # Why it is still worth keeping rather than deleting, on its own merits: it
        # cannot be the SOLE witness of a reach in this probe, because CPython inserts
        # a parent module whenever a submodule is imported, so any `X.Y` the walk
        # observes implies `X` is in `sys.modules` too and the `n == r` half already
        # matched. It is fail-closed insurance for a name injected into `sys.modules`
        # behind the importer's back, which no import statement this probe accepts can
        # produce. The file's "no unpinned lever" doctrine targets levers that can be
        # NARROWED silently; this clause cannot be narrowed into a hole — it is
        # exercised in one direction by the per-root row and in the other by the
        # `_matching(['redis_probe', …]) == set()` boundary assert.
        return _matching(_probe_modules(import_stmt, *injected))

    # ⛔ AND THIS LEG NEEDS POSITIVE CONTROLS, not only the negative assertion.
    # Everything above asserts the probe finds NOTHING, so every way of breaking the
    # detector reads GREEN: an emptied derivation (`forbidden=()`), the historical
    # hardcoded pair, or the subprocess call replaced by a constant all left the file
    # at 101 passed (measured). A detector that cannot be shown to detect is not a
    # detector — the same defect as the substring scan one level up.
    #
    # ⛔ AND THEY GRADE WHAT THE PROBE FINDS, NOT THE TEXT OF ITS PROGRAM, because a
    # text pin cannot see a narrowing written AROUND the text it looks for —
    # `forbidden=[r for r in <the pinned list> if '.' not in r]` contains the pinned
    # repr verbatim and silences every dotted root. So the controls below name roots
    # that a narrowed candidate would DROP — one DOTLESS and TWO DOTTED.
    # ⛔ THE CONTROLS BELOW ARE BEHAVIOURAL ON PURPOSE, and they are the assertion
    # surface for the PRODUCTION path (`_probe`) now that the matcher lives in this
    # file. The `_probe_forbidden()` pin above holds the LIST; these hold what the
    # matcher actually FINDS on a real import, named as three distinct roots so that
    # a restricted matcher has to keep all three to stay green:
    #   `redis`                 — DOTLESS, the original store-client witness;
    #   `tortoise.ep`           — DOTTED, so a dotless-only list/matcher reds;
    #   `tortoise.session_indexer` — a THIRD root, and the reason this row exists:
    #     with only two, a predicate guard keeps exactly those two and every row
    #     stays green (measured at `ebb479dc60`: `and r in ('redis','tortoise.ep')`
    #     → **102 passed** while `import tortoise.session_indexer` came back empty).
    #     A root the narrowing would DROP is the only thing that can witness it.
    # ⚠️ WHAT THESE THREE PINS IS THOSE THREE ROOTS' REACH, not the predicate — at the
    # head they were added to they were nearly the whole assertion surface, and "keep
    # the named controls" really was green. The per-root loop over `_probe_forbidden()`
    # below is what pins the WHOLE predicate now; these rows are what pins it through
    # the production path rather than the matcher in isolation.
    # Each is matched against a SET of names, never a substring
    # (`"redis" in "redislite.falkordb_client"` is True, the class this file removes
    # everywhere else).
    _ep = _probe("import tortoise.ep")
    assert "redis" in _ep, (
        "the transitive probe cannot detect a store client at all: importing "
        "tortoise.ep must put a `redis` module in sys.modules, so an empty answer "
        "here means the probe (not the tree) is broken")
    assert "tortoise.ep" in _ep, (
        "the probe must reach the DOTTED roots, not only the dotless store-client "
        "roots: `import tortoise.ep` puts `tortoise.ep` in sys.modules, so its "
        "absence means the matcher was restricted to the dotless roots and the "
        "leg certifies coverage it does not deliver (#14118 item 1)")
    _direct = _probe("import tortoise.session_indexer")
    assert "tortoise.session_indexer" in _direct, (
        "the probe must report a banned root it was told to import: `import "
        "tortoise.session_indexer` puts it in sys.modules, yet "
        f"{sorted(_direct)!r} is empty of it. Measure before fixing — THREE causes, "
        "and the two values below tell them apart: "
        f"CHILD reported it = {'tortoise.session_indexer' in _probe_modules('import tortoise.session_indexer')}; "
        f"in _probe_forbidden() = {'tortoise.session_indexer' in _probe_forbidden()}. "
        "CHILD=False → the child's printed set is narrowed (a green mutant on its "
        "own — see the residual above); CHILD=True and in _probe_forbidden()=True → "
        "`_matching`, or the list it is handed, dropped it; "
        "in _probe_forbidden()=False → the root is not in the probe's list, which is "
        "EITHER removed from `_FORBIDDEN_GRAPH_ROOTS` OR added to `_PROBE_EXEMPT` "
        "(that set is the ban set MINUS the exemptions), and "
        "`test_the_no_graph_guard_covers_every_module_that_reaches_a_store` plus the "
        "`_PROBE_EXEMPT == (…)` row together name which one. The matcher is innocent "
        "either way (#14118 item 1)")
    # ⛔ AND EVERY ROOT IS WITNESSED AGAINST BOTH CLAUSES, so what the matcher must
    # keep is the DERIVATION, not a list of names a narrowing can satisfy. The
    # matcher is in this file now, so it can be run over a one-name input directly:
    # the exact clause per root, then the submodule clause per root. A narrowing to
    # any subset — by iterable, by predicate, by a `continue` after a recording
    # append, or by "keep the named controls" — reds on the roots it drops. Measured
    # at `9bc39f9dd4`: that last shape was **102 passed**; here it is **1 failed /
    # 101**, and so is every other subset.
    for _root in _probe_forbidden():
        assert _matching([_root]) == {_root}, (
            f"the matcher does not report {_root!r}, which IS a banned root in "
            "`_probe_forbidden()` and is present in the input — a matcher narrowed to "
            "some subset of the derivation reds here, on each root it dropped "
            "(#14118 item 1)")
        assert _matching([_root + ".probe_child"]) == {_root + ".probe_child"}, (
            f"the matcher does not report {_root + '.probe_child'!r}, a submodule of "
            f"the banned root {_root!r} — the submodule clause "
            "(`n.startswith(r + '.')`) is load-bearing for a name injected behind "
            "the importer's back, and this row pins it for EVERY root, not for the "
            "one or two a named control happens to use")
    # AND the clause's BOUNDARY, both directions: a name that merely CONTAINS a root
    # is not under it, and an empty input finds nothing. Without this, `startswith`
    # could be widened to a substring test and every row above would stay green.
    # ⛔ AND THE NAME STREAM IS WITNESSED AT EVERY ROOT, not only the matcher in
    # isolation. Pinning `_matching` alone left the production path open one hop
    # later: measured on the previous head, narrowing the child's print to the three
    # named controls was **102 passed** with `import tortoise.projection` coming back
    # empty, and suppressing one name in the parent's parse was **102 passed** too.
    # Both are red below: the injected roots are KNOWN to be present, so their absence
    # from the result is a defect rather than an ambiguity.
    _all_roots = _probe("pass", *_probe_forbidden())
    assert _all_roots == set(_probe_forbidden()), (
        f"with every root supplied to the child, the probe reported "
        f"{sorted(_all_roots)} — it must report the WHOLE derivation. A missing root "
        "means the name stream was narrowed after `sys.modules` (the child's print), or "
        "on the way into `_matching` (the parent's parse), or inside the matcher; each "
        "is a live miss for that root and none of them is visible to a row that "
        "imports a real module (#14118 item 1)")
    # ⛔ AND EVERY ROOT IS IMPORTED FOR REAL, one at a time, with NOTHING injected —
    # the row that cannot be satisfied by a keep-list. The expected name is the module
    # the import statement ITSELF put in `sys.modules`, so a filter keyed on
    # `set(injected)` (empty here) or on any fixed list of controls must include the
    # WHOLE derivation to stay green. Measured on the previous head: a keep-list of six
    # literals — the three named controls plus the three roots the old `projection` row
    # named — left the probe blind to `tortoise.acl_graph_users` and
    # `tortoise.hosted_backup` and was still **102 passed**; it reds here.
    for _root in _probe_forbidden():
        assert _root in _probe(f"import {_root}"), (
            f"`import {_root}` puts `{_root}` in `sys.modules`, so the probe must "
            "report it. Nothing is injected for this row, so no keep-list can be keyed "
            "on the witness inputs, and a filter that names only some roots drops this "
            "one (#14118 item 1)")
    assert _matching(["redis_probe", "tortoise_ep", "notfalkordb"]) == set(), (
        "the matcher must not treat a name that merely CONTAINS a banned root as "
        "being under it — `redis_probe` is not `redis.*`, and matching it would make "
        "the leg fail closed on unrelated modules")
    assert _matching([]) == set(), "the matcher must find nothing in an empty input"
    # ⛔ AND THE SUBJECT'S OWN ROW CARRIES A WITNESS, because it is the ONE row whose
    # import is `usage_signal` itself, and it was the one row with NO positive control:
    # a bare `== set()` cannot tell "nothing leaked" from "the matcher answered empty"
    # for this run. Measured before this change — a matcher that returns `set()`
    # whenever `tortoise.usage_signal` is in its input left the WHOLE FILE at **102
    # passed**, so the leg was silently blind to exactly the leak it exists to detect
    # (a store client reached indirectly, which the AST ban cannot see). Injecting a
    # KNOWN banned root into the SAME run converts that negative into a witnessed one:
    # `falkordb` is in `_FORBIDDEN_GRAPH_ROOTS` and not in `_PROBE_EXEMPT`, so it is
    # supplied from outside and must come back — an empty answer is a defect either way.
    # Measured at this head: this run yields exactly `{'falkordb'}`, and the mutant that
    # was 102 passed above is 1 failed / 101 with it in place.
    leaked = _probe("from tortoise import usage_signal", "falkordb")
    assert leaked == {"falkordb"}, (
        "importing usage_signal must pull NO banned root other than the injected "
        f"witness: got {sorted(leaked)}, expected ['falkordb']. A MISSING witness means "
        "the matcher is not observing this run at all (it answers empty for the "
        "subject's own import, which is the leak this leg exists to detect), and an "
        "EXTRA name is that leak (#14118 item 1)")


def test_the_no_graph_guard_catches_relative_imports() -> None:
    """The guard itself is pinned — it was defeated once by import spelling."""
    assert _imports_of("from . import projection\n") == {"tortoise.projection"}
    # The alias IS recorded for the relative form too (load-bearing: see the
    # `.__init__` case in the export pin), so this is a superposition.
    assert _imports_of("from .projection import FalkorProjection\n") == {
        "tortoise.projection",
        "tortoise.projection.FalkorProjection",
    }
    assert _imports_of("import redis\n") == {"redis"}
    assert _imports_of("from . import read_ledger\n") == {"tortoise.read_ledger"}
    # ⛔ AND the package-spelling of a SUBMODULE: `from tortoise import sdk`
    # reaches `tortoise.sdk` without ever naming it as a module path.
    assert _imports_of("from tortoise import sdk\n") == {
        "tortoise",
        "tortoise.sdk",
    }


@pytest.mark.parametrize("root", _FORBIDDEN_GRAPH_ROOTS)
def test_every_entry_in_the_ban_is_pinned(root: str) -> None:
    """⛔ A BAN ENTRY THAT NOTHING EXERCISES CAN BE NARROWED SILENTLY.

    The direction tests pin `_STORE_CLIENT_ROOTS` (the SCAN) and the set's tree
    modules; nothing pinned the ENTRIES, so deleting `"redislite"` left the file
    green, and so did narrowing the matcher itself to the three dotless roots
    (`_FORBIDDEN_GRAPH_ROOTS` → `_STORE_CLIENT_ROOTS` inside `_offending`), which
    is a full bypass for `from .projection import FalkorProjection` in a function
    body. Parametrized over the WHOLE set — every entry is a module path, so both
    assertions hold for all of them.

    NOTE: parametrizing over the set cannot pin a REMOVAL (the case goes with the
    entry). `test_the_ban_covers_all_three_roots_the_tree_actually_uses` is the
    floor that holds the three roots the tree is measured to import from.
    """
    assert _offending(f"import {root}\n") == {root}
    assert _offending(f"from {root}.client import Something\n") == {
        f"{root}.client",
        f"{root}.client.Something",
    }


def test_the_ban_covers_all_three_roots_the_tree_actually_uses() -> None:
    """⛔ A FLOOR, not a mirror — the parametrization above cannot pin a REMOVAL.

    Parametrizing over `_FORBIDDEN_GRAPH_ROOTS` pins each entry's BEHAVIOUR, but
    deleting an entry also deletes its own case, so THAT row cannot see the loss.
    The loss is not otherwise silent — measured at this head, dropping `redislite`
    (or `redis`, or `falkordb`) reddens TWO rows: this one, and the scan row whose
    witnesses reach the client through the dropped root. This test is therefore the
    recorded DECISION rather than the only detector: these three roots are the ones
    the tree is MEASURED to import a client from — `tortoise/__init__.py`
    (`redislite.falkordb_client`), `hosted_backup`/`acl_graph_users`/
    `tortoise_client`/`retry`/`ep`/`hosted_api` (`redis`), `projection` (`falkordb`)
    — so the floor states that a fourth root must be added deliberately and that
    none of these three may be dropped to green a scan.
    """
    assert set(_STORE_CLIENT_ROOTS) >= {"falkordb", "redis", "redislite"}


def test_no_graph_dependency_pins_the_package_root_EXPORT_form() -> None:
    """⛔ `from tortoise import FalkorDB` is a store spelling no module entry names.

    `_imports_of` resolves it to `{tortoise, tortoise.FalkorDB}`, and the package
    root is exempt from the ban (banning it bans the module itself), so before
    `_FORBIDDEN_CLIENT_EXPORTS` this passed every leg — including from inside a
    function body, where the subprocess probe cannot see it either because the
    import never runs at module scope. `tortoise/__init__.py` is a PEP-562
    `__getattr__` exporting the guarded redislite client, and names this form as
    the bypass surface in its own docstring.
    """
    assert _offending("from tortoise import FalkorDB\n") == {"tortoise.FalkorDB"}
    assert _offending("from tortoise import Tortoise\n") == {"tortoise.Tortoise"}
    assert _offending("def f():\n    from tortoise import FalkorDB\n") == {
        "tortoise.FalkorDB"
    }
    # ⛔ AND THE PLAIN ATTRIBUTE — no `import` statement at all. Inside a function
    # body nothing else sees it: the probe is blind (the access never runs at import
    # time) and no module appears for the direction scan. At module scope the probe
    # catches it incidentally (the access loads redislite), which is what made the
    # function-body spelling the surviving hole.
    assert _offending("import tortoise\nx = tortoise.FalkorDB\n") == {
        "tortoise.FalkorDB"
    }
    assert _offending("def f():\n    import tortoise\n    return tortoise.Tortoise\n") == {
        "tortoise.Tortoise"
    }
    # ⛔ AND THE ALIASED ATTRIBUTE, which is the form a caller actually writes:
    # `import tortoise as t` binds `t`, not `tortoise`, so a reader that only knows
    # the canonical name records nothing. (`_package_root_attributes` follows the
    # alias; the bare-name branch inside `_imports_of` cannot.)
    assert _offending("import tortoise as t\nc = t.FalkorDB\n") == {
        "tortoise.FalkorDB"
    }
    # ⛔ AND THE REBINDING, which is the alias hop one assignment over: a walk that
    # stops at the `import` statement sees nothing here, so closing `import … as`
    # alone leaves the identical hole. Chained once to prove the fixpoint.
    assert _offending("import tortoise\nf = tortoise\nc = f.FalkorDB\n") == {
        "tortoise.FalkorDB"
    }
    assert _offending("import tortoise as t\nf = t\ng = f\nc = g.Tortoise\n") == {
        "tortoise.Tortoise"
    }
    # ⛔ AND THE ANNOTATED REBINDING (`ast.AnnAssign`), which is a separate node kind
    # from `ast.Assign` — a fixpoint that walks only `Assign` follows `f = tortoise`
    # and NOT `f: Any = tortoise`, leaving the same hole one spelling over. Pinned
    # on its own because deleting that branch reddened nothing (measured: 98
    # passed), i.e. it was the only unpinned line of the leg.
    assert _offending(
        "from typing import Any\nimport tortoise\nf: Any = tortoise\nc = f.FalkorDB\n"
    ) == {"tortoise.FalkorDB"}
    # ⛔ AND THE FIXPOINT ITERATION ITSELF. `ast.walk` is BREADTH-FIRST, so a
    # rebinding inside a nested block is visited AFTER a module-level statement
    # that consumes it — one pass sees `b = a` before it knows `a` is the package
    # root and records nothing. Pinned because replacing `while changed:` with a
    # single pass left the suite fully green (measured) while the
    # shipped code catches the real spelling below.
    assert _offending(
        "import tortoise\nif True:\n    a = tortoise\nb = a\nc = b.FalkorDB\n"
    ) == {"tortoise.FalkorDB"}
    # …and `import tortoise.sdk` binds `tortoise` too, so it is both a banned
    # module AND the handle the attribute form is read through.
    assert _offending("import tortoise.sdk\nc = tortoise.FalkorDB\n") == {
        "tortoise.sdk",
        "tortoise.FalkorDB",
    }
    # ⛔ AND THE ATTRIBUTE FORM OF A BANNED MODULE — not just of an export. The leg
    # was first filtered to `_FORBIDDEN_CLIENT_EXPORTS`, which left `tortoise.sdk`,
    # `tortoise.projection` and `tortoise.embedded_lifecycle` reachable as
    # attributes of the package with every leg green; `_REACHABLE_FROM_ROOT` is the
    # union that closes it. `embedded_lifecycle` is the live one (`__init__.py`
    # imports it eagerly, so reading it needs no other import in the process).
    assert _offending("import tortoise as _t\nh = _t.embedded_lifecycle\n") == {
        "tortoise.embedded_lifecycle"
    }
    assert _offending("import tortoise\nx = tortoise.projection\n") == {
        "tortoise.projection"
    }
    assert _offending("def f():\n    import tortoise\n    return tortoise.sdk\n") == {
        "tortoise.sdk"
    }
    # ⛔ AND `getattr` WITH BOTH ARGUMENTS LITERAL, which is statically decidable
    # and was the one dynamic-looking spelling left unseen by a guard that
    # deliberately covers a non-import attribute read.
    assert _offending('import tortoise\nc = getattr(tortoise, "FalkorDB")\n') == {
        "tortoise.FalkorDB"
    }
    assert _offending('import tortoise\nc = getattr(tortoise, "sdk")\n') == {
        "tortoise.sdk"
    }
    # ⛔ AND THE `.__init__` SPELLING OF THE PACKAGE ROOT, which is the SAME reach as
    # the two forms above and was missed by every leg: the base of the attribute is
    # itself an Attribute, so a reader that only matches `Attribute(value=Name)` saw
    # nothing, and the import form resolved to a name matching no root and no export.
    assert _offending("from tortoise.__init__ import FalkorDB\n") == {
        "tortoise.FalkorDB"
    }
    assert _offending("import tortoise.__init__\nc = tortoise.__init__.Tortoise\n") == {
        "tortoise.Tortoise"
    }
    assert _offending("def f():\n    from tortoise.__init__ import Tortoise\n") == {
        "tortoise.Tortoise"
    }
    assert _offending(
        'import tortoise.__init__\nc = getattr(tortoise.__init__, "FalkorDB")\n'
    ) == {"tortoise.FalkorDB"}
    # ⛔ AND THE RELATIVE `.__init__` SPELLING, which is the one `usage_signal.py`'s
    # own import style produces (`from .config import …`). It resolves to
    # `tortoise.__init__`, which folds onto the UNBANNED package root, so emitting
    # only the module there lost the alias — every other relative module was saved
    # by its own ban, which is exactly what hid this one.
    assert _offending("from .__init__ import FalkorDB\n") == {"tortoise.FalkorDB"}
    assert _offending("from .__init__ import Tortoise\n") == {"tortoise.Tortoise"}
    # …and the REBINDING through that spelling, which the fixpoint missed while it
    # followed only a bare `ast.Name` value.
    assert _offending(
        "import tortoise.__init__\nf = tortoise.__init__\nc = f.FalkorDB\n"
    ) == {"tortoise.FalkorDB"}
    # A NON-literal name stays out of scope (and is named as such in the docstring).
    assert _offending("import tortoise\nc = getattr(tortoise, NAME)\n") == set()
    # ⛔ AND THE STAR IMPORT, which was TRIPLY blind: the bare `*` matched no root,
    # the scan saw no store root, and the probe cannot see `embedded_lifecycle`. In a
    # fresh interpreter `from tortoise import *` really does bind it (the package has
    # no `__all__`, and `embedded_lifecycle` is a public name), so this spelling
    # reached the module that owns the embedded store with every row green.
    assert _offending("from tortoise import *\nh = embedded_lifecycle\n") >= {
        "tortoise.embedded_lifecycle",
        "tortoise.sdk",
    }
    # ⛔ AND THE RELATIVE STAR, which the absolute branch cannot reach (it is nested
    # under `level == 0`). `from . import *` binds the package's public names in a
    # fresh interpreter, `embedded_lifecycle` among them.
    assert _offending("from . import *\nh = embedded_lifecycle\n") >= {
        "tortoise.embedded_lifecycle",
        "tortoise.sdk",
    }
    # ⛔ AND THE `.__init__` SPELLING OF THE STAR, absolute and relative (#14120
    # item 1). This arm required `level == 0` and built its prefix from the RAW
    # module, so `from tortoise.__init__ import *` derived `tortoise.__init__.`
    # (which no `_REACHABLE_FROM_ROOT` entry starts with) and
    # `from .__init__ import *` skipped the arm entirely. Both returned a set whose
    # only member folds onto the deliberately-unbanned package root, so
    # `_offending` was EMPTY — measured on this tree at `e022fc935b`: both forms →
    # `offending=False`. `__init__.py` exports `FalkorDB`/`Tortoise` through its
    # PEP-562 hook and, with no `__all__`, also `embedded_lifecycle`, so the star
    # really does bind the store. `_fold_init` is documented as "applied to every
    # name this module derives"; folding the prefix is what makes this the same
    # spelling as the rest.
    assert _offending("from tortoise.__init__ import *\nh = FalkorDB\n") >= {
        "tortoise.embedded_lifecycle",
        "tortoise.sdk",
    }, (
        "the absolute `.__init__` star must be the package-root star, not a "
        "`tortoise.__init__.` prefix that matches no reachable name")
    assert _offending("from .__init__ import *\nh = FalkorDB\n") >= {
        "tortoise.embedded_lifecycle",
        "tortoise.sdk",
    }, (
        "the relative `.__init__` star must enter the same arm; gating it on "
        "`level == 0` folded this spelling onto the unbanned root and every leg "
        "was silent")
    # ⛔ AND THE PEP-562 HOOK CALLED DIRECTLY with a literal name.
    assert _offending('import tortoise\nc = tortoise.__getattr__("FalkorDB")\n') == {
        "tortoise.FalkorDB"
    }
    assert _offending('import tortoise\nc = tortoise.__getattr__("sdk")\n') == {
        "tortoise.sdk"
    }
    # …and the KEYWORD form, which the hook accepts (`def __getattr__(name: str)`)
    # even though the builtin `getattr` rejects it.
    assert _offending(
        'import tortoise\nc = tortoise.__getattr__(name="FalkorDB")\n'
    ) == {"tortoise.FalkorDB"}
    # ⛔ AND THE RECEIVER GOES THROUGH `_dotted_base`, not a bare `ast.Name`.
    # Reverting that one call site kept every row green (measured: 101 passed)
    # while the `.__init__` spelling of the CHAIN silently stopped being seen —
    # although `_fold_init`'s docstring claims the fold is applied to every name
    # this module derives rather than to one call site.
    assert _offending(
        'import tortoise.__init__\nc = tortoise.__init__.__getattr__("FalkorDB")\n'
    ) == {"tortoise.FalkorDB"}, (
        "the `.__getattr__` receiver must be folded through `_dotted_base` like "
        "every other name this module derives")
    # ⛔ AND THE `from tortoise import __init__` SPELLING, which names the PACKAGE
    # ROOT. It was the one spelling of the root outside the bound loop's view:
    # `_imports_of` folds it to the unbanned `tortoise` and no banned root matched.
    # ⚠️ In the ORDINARY case this is a FAIL-CLOSED OVER-REPORT, and in one
    # ordering it is a real reach — the distinction matters, because "distinct
    # module" is NOT "no reach". Without a prior `import tortoise.__init__`, `__init__`
    # IS the `method-wrapper` `module.__init__`, so `i.FalkorDB` raises
    # `AttributeError`. AFTER an explicit `import tortoise.__init__` the parent's
    # attribute IS the submodule module — distinct from the package
    # (`sys.modules['tortoise.__init__'] is tortoise` → `False`), which is
    # irrelevant to reachability — and its PEP-562 `__getattr__` builds an
    # EQUIVALENT guarded client, so `i.FalkorDB` returns one. ⚠️ EQUIVALENT, not
    # identical: it is a DISTINCT class object built by the same-named builder
    # (`i.FalkorDB is tortoise.FalkorDB` → `False`; only `Tortoise` is identical),
    # and "the SAME" in a paragraph that has just discussed `is` reads as identity.
    # The reach claim is the load-bearing part and it is true; this clause must not
    # overclaim past it. Measured on this tree at
    # `e022fc935b`. The leg reports the spelling either way because a caller who
    # writes this name intends the package root, and silence on a spelling is the
    # unrecoverable direction.
    # ⛔ THE BINDING IS BY NAME, NOT BY LOAD ORDER — and this row is the one that
    # actually witnesses the `ast.ImportFrom` branch. An earlier cut asserted
    # `_offending("import tortoise.__init__\nfrom tortoise import __init__ as i\n")
    # == set()`, which is green for EVERY mutation of this branch because the
    # source contains no attribute read at all: it pinned nothing while reading as
    # if it pinned the branch. The leg matches the NAME bound from the package
    # root, so the read is reported with or without a preceding
    # `import tortoise.__init__` — the deliberate fail-closed over-report this
    # function's first paragraph sanctions.
    assert _offending(
        "from tortoise import __init__\nc = __init__.FalkorDB\n"
    ) == {"tortoise.FalkorDB"}, (
        "the binding is name-based: a read on the `__init__` name bound from the "
        "package root is reported whether or not the submodule was imported first — "
        "an over-report by design, not a claim that the name is the module")
    # ⛔ AND THE RELATIVE SPELLING, which is the one a module INSIDE this package
    # writes and which `ast` records with NO module string (module=None, level=1)
    # — so the absolute-only branch could never see it. Same fail-closed over-report
    # as the absolute form above (in the ordinary case the bound name is a
    # `method-wrapper`): the leg
    # exists because this spelling, the ban and the direction scan were ALL silent
    # on it together.
    assert _offending(
        "from . import __init__ as i\nc = i.FalkorDB\n"
    ) == {"tortoise.FalkorDB"}, (
        "`from . import __init__` binds the package root in a module inside the "
        "package; an absolute-only branch left the spelling off every leg — the "
        "ban and the direction scan were both silent on it")
    assert _offending(
        "from . import __init__\nc = __init__.FalkorDB\n"
    ) == {"tortoise.FalkorDB"}, (
        "the un-aliased relative binding is the same over-report under the plain "
        "name")
    assert _module_reaches_a_store(
        "import tortoise.__init__\nfrom tortoise import __init__ as i\nc = i.sdk\n"
    ), (
        "the direction scan must see this bound form too, or it certifies a ban "
        "it cannot see"
    )
    assert _module_reaches_a_store(
        "from . import __init__ as i\nc = i.sdk\n"
    ), (
        "the direction scan must see the RELATIVE bound form too, or it certifies "
        "a ban it cannot see"
    )
    # ⛔ AND ONE DOT FURTHER: `from .. import __init__`, written in a module ONE
    # package deep, is the root spelling one level down — so the arm must read
    # `level >= 1` rather than `level == 1`. Narrowing it to `== 1` kept every row
    # green (measured: 101 passed) while this spelling stopped being seen — the
    # same unpinned-leg class as the rest of this file. It over-reports for a
    # module at the top level, where `from .. import __init__` is past the root and
    # raises instead: fail-closed, per this function's first paragraph. Same runtime
    # caveat as the forms above — unless `tortoise.__init__` was imported first the
    # bound name is a `method-wrapper`, and after that import the read really does
    # reach the guarded client; either way the leg reports the spelling.
    assert _offending(
        "from .. import __init__ as i\nc = i.FalkorDB\n"
    ) == {"tortoise.FalkorDB"}, (
        "`from .. import __init__` in a module one package deep is the root "
        "spelling, so a `level == 1` arm lets `i.FalkorDB` through every leg")
    assert _module_reaches_a_store("from .. import __init__ as i\nc = i.sdk\n"), (
        "the direction scan runs the same leg, so the one-dot-further spelling "
        "must be visible there too")
    # ⚠️ The remaining literal-name indirections are DECLARED out of scope in the
    # scope note rather than resolved; pinned here so the boundary is visible and a
    # future reader cannot mistake silence for coverage.
    assert _offending('import tortoise\nf = getattr\nf(tortoise, "FalkorDB")\n') == set()
    assert _offending(
        'import tortoise\nc = tortoise.__getattribute__("FalkorDB")\n'
    ) == set()
    # …and the harmless spellings stay clean, so the ban is not a blanket. Bare
    # `import tortoise` and an unrelated attribute must not redden, and neither
    # must an attribute read on a name that was never bound to the package root
    # (it would `NameError` at runtime, so flagging it is a pure false positive).
    assert _offending("from tortoise import usage_signal\n") == set()
    assert _offending("import tortoise\nx = tortoise.__version__\n") == set()
    assert _offending("c = tortoise.FalkorDB\n") == set()


def test_the_no_graph_guard_names_modules_that_EXIST() -> None:
    """⛔ A FORBIDDEN NAME THAT IS IN NO TREE PROTECTS NOTHING.

    The first cut forbade `tortoise.manager` and `tortoise.graph_connection`,
    neither of which exists, while omitting `tortoise.projection` — so the guard's
    summary ("no graph in reach at all") was carried by a list half of which could
    never match and none of which was the real client's home.
    """
    package = Path(usage_signal.__file__).parent
    missing: list[str] = []
    for root in _FORBIDDEN_GRAPH_ROOTS:
        if "." not in root:
            continue  # third-party (`falkordb`, `redis`) — not this tree's to resolve
        parts = root.split(".")[1:]
        candidate = package.joinpath(*parts)
        if not (candidate.with_suffix(".py").is_file() or candidate.is_dir()):
            missing.append(root)
    assert missing == [], (
        f"the forbidden set names module(s) that do not exist: {missing} — a dead "
        f"entry makes the guard read as protection it cannot provide"
    )


@functools.cache
def _modules_reaching_a_store() -> frozenset[str]:
    """Every module in the tree that reaches a store CLIENT, by parsed imports.

    ⛔ PARSED, NOT GREPPED, AND BOTH FORMS OF REACH ARE SCANNED: the three
    store-client roots (`_STORE_CLIENT_ROOTS`) AND the two package-root exports
    that WRAP the client (`_FORBIDDEN_CLIENT_EXPORTS`), so a module whose only
    reach is `from tortoise import FalkorDB` is found here too. The export half was
    missing, and it made this row's "a module cannot slip past" claim false for the
    one spelling the ban set was EXTENDED to catch: measured, a synthetic module
    whose only reach was `def f(): from tortoise import FalkorDB` was absent from
    `found` (98 passed) while `_offending()` on the same source returned
    `['tortoise.FalkorDB']` — the scan and the ban disagreeing about one file. A
    direction scan narrower than the ban it certifies is the same defect as a scan
    narrower than the roots it declares, one layer in.

    ⛔ AND THE ATTRIBUTE SPELLING IS NOW THE THIRD LEG. It used to be left out with
    the reason "it cannot be told apart from an unrelated attribute read at this
    granularity" — measurably FALSE: `_package_root_attributes` IS that
    distinction (it filters through `_REACHABLE_FROM_ROOT`, so an unrelated
    `tortoise.__version__` read returns nothing), and it flags no file in the tree
    today, so wiring it in costs no false positive. Left out, this row was blind
    to a module whose only reach is `import tortoise; tortoise.FalkorDB` while
    `_offending` caught it — the scan/ban disagreement this docstring already
    calls out one paragraph up, one spelling over. `_module_reaches_a_store` is
    the ONE per-file predicate, and `_scan_modules` is the ONE loop, that this
    scan and its pin BOTH call — so reverting either is a red, not a silent leg
    loss.

    ⚠️ NOT the whole ban set. `_FORBIDDEN_GRAPH_ROOTS` also lists modules that are
    ORDINARY imports (`tortoise.sdk`, `tortoise.ep`, `tortoise.retry`, …) and
    matching those would demand a ban on every importer of the public SDK — a large
    share of the tree, and a direction scan that flags everything protects nothing.
    ⛔ No count is quoted, and the sentence that used to be here said "measured,
    that flags most of the package" — with a figure beside it, and then a SECOND
    figure when the first was corrected. Both moved with the tree and both were
    wrong by the time they were read, which is why no size figure belongs in a
    justification (and why the commit that removed the first one said the count was
    deleted rather than corrected). The reason this design is right is the
    MECHANISM — a ban that demanded every SDK importer be banned collapses into
    "ban most of the tree", and a direction scan that flags everything protects
    nothing — not the size. If you want the size, measure it; do not read it here.

    ⚠️ AND THIS SCAN DOES NOT SEE A TREE-RELATIVE REACH: `from .projection import
    FalkorProjection` written in ANOTHER tree module is one hop from the client, and
    the direction test's only input IS this scan — so no test reds on it. That is an
    out-of-scope residual, not a hand-off: this sentence used to say such a reach
    "belongs to the direction test", which does not scan for it either.

    (Before that, the direction test read `"from falkordb import" in source` — a
    substring test that saw only ONE of the roots the set itself declares, so the
    modules that reach the store through `redis` (`hosted_backup` constructs a real
    client over a derived pool, `acl_graph_users`, `tortoise_client`, `retry`, `ep`,
    `hosted_api`, `embedded_lifecycle`) were invisible and the coverage test reported
    protection it did not deliver. A substring is wrong in the other direction too:
    `"import redis" in source` matches PROSE — `tortoise/embedded_lifecycle.py` says
    "`import tortoise` must not import redislite" in a comment, which contains the
    substring `import redis`.)

    Cached: the tree cannot change within a run, and re-parsing every file in the
    package on each call is seconds-scale — enough to blow the shard row this file
    packs into, and paid for no benefit, since the answer cannot change mid-run.
    ⛔ No local seconds figure is quoted on purpose: a figure beside a weight
    re-stales against the run it came from (`config/ci-surfaces.yml` states the
    same rule where the `durations:` rows live).
    """
    package = Path(usage_signal.__file__).parent
    return _scan_modules(_tree_module_sources(package))


def _tree_module_sources(package: Path) -> Iterator[tuple[str, str]]:
    """`(module name, source)` for every module in the tree, `__init__` folded.

    Split from `_scan_modules` so the LOOP is feedable by the pin: the tree cannot
    discriminate the wiring (no file reaches a store ONLY through the attribute
    spelling today), so a synthetic pair is the only input that can.
    """
    for path in package.rglob("*.py"):
        rel = path.relative_to(package).with_suffix("")
        parts = [part for part in rel.parts if part != "__init__"]
        module = "tortoise" + ("." + ".".join(parts) if parts else "")
        yield module, path.read_text(encoding="utf-8")


def _scan_modules(sources: Iterator[tuple[str, str]]) -> frozenset[str]:
    """THE SCAN'S LOOP: keep the modules whose source reaches a store client.

    ⛔ ONE LOOP FOR THE TREE AND THE PIN. The pin that exercises the attribute leg
    called `_module_reaches_a_store` directly, so reverting THIS loop to the
    pre-fix two-leg expression kept every row green (measured: 101 passed) — the
    helper was left correct and exercised, and the scan was silently back on the
    narrower predicate. The wiring is pinned by feeding this function.
    """
    return frozenset(
        module for module, source in sources if _module_reaches_a_store(source)
    )


def _module_reaches_a_store(source: str) -> bool:
    """Does ONE module's source reach a store client — by any of the THREE legs?

    ⛔ EXTRACTED SO THE LEGS ARE PINNABLE INDIVIDUALLY, and so the scan cannot
    hold a predicate its own pin does not exercise. The three legs are the store
    roots, the package-root EXPORTS (`from tortoise import FalkorDB`), and the
    package-root ATTRIBUTES (`import tortoise; tortoise.FalkorDB` /
    `getattr(tortoise, "FalkorDB")`). The attribute leg was missing here for two
    rounds while `_offending` applied it — a direction scan narrower than the ban
    it certifies, which is this PR's whole subject. Synthetic input is the only
    input that can hold that leg: no tree module reaches a store ONLY that way.
    """
    return _reaches_a_store_client(_imports_of(source)) or bool(
        _package_root_attributes(source)
    )


def _reaches_a_store_client(imported: set[str]) -> bool:
    """Does this import set reach a store client, by ROOT or by EXPORT name?

    ⛔ EXTRACTED SO THE EXPORT HALF CAN BE PINNED AT ALL. Inside the scan it was
    inert against TREE DATA: the only module naming an export
    (`tortoise/projection/__init__.py` → `tortoise.FalkorDB`) also imports
    `falkordb` directly, so `found` is byte-identical with and without the export
    clause — reverting it left the whole file green (measured: 100 passed). A leg
    that no observable input exercises is a leg that can be deleted silently, which
    is this PR's whole subject; the synthetic pins below are the observable input.

    ⛔ AND THE `.__init__` FOLD BELONGS HERE, in the ONE predicate both directions
    call. It was previously applied at the scan's call site only, which made it
    unpinnable: removing it there left every row green, because the agreement pin
    re-derived the predicate inline instead of calling this function — an inert leg,
    which is this PR's whole subject.
    """
    folded = {_fold_init(name) for name in imported}
    by_root = any(
        name == root or name.startswith(root + ".")
        for name in folded
        for root in _STORE_CLIENT_ROOTS
    )
    return by_root or bool(folded & _FORBIDDEN_CLIENT_EXPORTS)


def test_the_store_reach_scan_sees_the_REDIS_spelling_too() -> None:
    """⛔ The scan must read EVERY declared root, or its verdict is vacuous.

    Pinned POSITIVELY on purpose: `uncovered == []` alone cannot see a predicate
    that narrowed, because a module that stops being scanned simply DROPS OUT of
    `found` — so the coverage row never sees it, whatever its ban membership. The
    pin is therefore SYMMETRIC in the roots it witnesses: `redis`/`redislite`
    importers (whose loss is invisible because those modules are BANNED, so
    `uncovered` cannot move) AND `tortoise.cypher_guard`, which reaches the store
    through `falkordb` ALONE (whose loss is invisible for the same reason — every
    `falkordb`-only importer is already banned, so dropping that root leaves
    `uncovered == []` untouched). Without `cypher_guard` the pin named only the
    roots it could afford to lose.
    """
    found = _modules_reaching_a_store()
    for module in (
        "tortoise.hosted_backup",
        "tortoise.acl_graph_users",
        "tortoise.tortoise_client",
        "tortoise",  # the package root, via `redislite.falkordb_client`
        "tortoise.cypher_guard",  # `falkordb` ONLY — the root the pin was missing
    ):
        assert module in found, (
            f"{module} imports a store client and the scan did not see it — the "
            f"reachability guard is narrower than the roots it declares: "
            f"found={sorted(found)!r}"
        )
    # ⛔ AND THE SUBPACKAGE WALK — the one narrowing that loses the module this
    # guard EXISTS because of. `tortoise.projection` reaches the client from
    # `tortoise/projection/__init__.py`, so it is in `found` ONLY because the walk
    # descends into subpackages. Narrowing `rglob` to `glob` keeps every row above
    # green (measured: 100 passed) while dropping it — it is BANNED, so `uncovered`
    # cannot move either, and the loss is invisible in both directions. Asserted
    # positively here, on the module whose slip the coverage row's docstring says
    # must never happen again.
    assert "tortoise.projection" in found, (
        f"tortoise.projection reaches the store from a SUBPACKAGE and is not in "
        f"the scan — the walk does not descend, so every subpackage module is "
        f"invisible: found={sorted(found)!r}")
    # ⛔ AND THE EXPORT HALF OF THE PREDICATE, synthetically. No module in the tree
    # reaches a store ONLY through an export spelling, so tree data could never hold
    # this branch — reverting it left the whole file green (measured: 100 passed).
    # These inputs are the only ones that can.
    assert _reaches_a_store_client({"tortoise.FalkorDB"})
    assert _reaches_a_store_client(
        _imports_of("def f():\n    from tortoise import FalkorDB\n")
    )
    # …and the tree-relative form stays OUT of this predicate, as documented.
    assert not _reaches_a_store_client(
        _imports_of("from .projection import FalkorProjection\n")
    )
    # ⛔ AND THE ATTRIBUTE LEG, synthetically — the third spelling, and the one the
    # scan was blind to while the ban saw it. No tree module reaches a store ONLY
    # through an attribute read (measured: `_package_root_attributes` flags 0 of
    # 305 files), so tree data could never hold this leg either; without these two
    # asserting the SCAN's own predicate, deleting `_package_root_attributes` from
    # `_module_reaches_a_store` kept every row green (measured: 101 passed).
    attr_only = "import tortoise\n\ndef f():\n    return tortoise.FalkorDB\n"
    assert _module_reaches_a_store(attr_only), (
        "the reachability scan does not see `import tortoise; tortoise.FalkorDB`, "
        "while the ban does — a scan narrower than the ban it certifies")
    assert _offending(attr_only) == {"tortoise.FalkorDB"}, (
        "the scan and the ban must agree about the attribute spelling: the scan "
        "now sees it, so `_offending` must still name it")
    assert _module_reaches_a_store(
        'import tortoise\nc = getattr(tortoise, "FalkorDB")\n'
    ), "the attribute leg must cover the `getattr` form too, not only the plain read"
    # ⛔ AND THE SCAN'S OWN LOOP IS FED, not just the predicate. No file in the tree
    # reaches a store ONLY through the attribute spelling, so reverting the LOOP to
    # the pre-fix two-leg expression left every row green (measured: 101 passed) —
    # the predicate stayed correct and exercised while the scan was back on the
    # narrower predicate. A synthetic pair is the only input that can hold the
    # wiring, so the pin goes through `_scan_modules` itself.
    # ⛔ AND THE INPUT CARRIES A NEGATIVE CONTROL. A single reaching pair cannot
    # distinguish "the loop applies the predicate" from "the loop keeps everything":
    # a loop whose body is `frozenset(module for module, _ in sources)` satisfied the
    # one-element form, and the row that reddened instead was the COVERAGE row (a
    # different gate, several hundred lines up) — measured, 1 failed, 101 passed,
    # with the loop pin among the 101. One clean pair beside the reaching one makes
    # the predicate's own contribution visible HERE: keep-everything now returns two
    # names where one is expected.
    clean = "# clean module: no store client, no package-root export or attribute\n"
    assert _scan_modules(
        iter(
            [
                ("tortoise.synthetic_attr_only", attr_only),
                ("tortoise.synthetic_clean", clean),
            ]
        )
    ) == {"tortoise.synthetic_attr_only"}, (
        "the scan's loop does not route through `_module_reaches_a_store`, so the "
        "attribute leg can be dropped from the scan with every other row green")


def test_the_scan_actually_calls_the_pinned_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """⛔ THE EDGE BETWEEN THE SCAN AND ITS LOOP, pinned directly.

    Feeding `_scan_modules` a synthetic pair pins the LOOP and nothing above it: a
    scan that computed its own inline body left `_scan_modules` correct AND
    exercised while the attribute leg was gone from the scan — measured at head
    `1f37c999` by replacing the call site with the pre-fix two-leg loop, which gave
    **101 passed**, every row green. That is the third appearance of one class in
    this file (predicate, then loop, now the call site), so the edge is pinned by
    substitution: a scan that does not route through `_scan_modules` cannot return
    this sentinel.
    """
    sentinel = frozenset({"sentinel.from.a.patched.loop"})
    monkeypatch.setattr(sys.modules[__name__], "_scan_modules", lambda sources: sentinel)
    _modules_reaching_a_store.cache_clear()
    try:
        assert _modules_reaching_a_store() == sentinel, (
            "`_modules_reaching_a_store` does not route through `_scan_modules`, "
            "so the pinned loop can be bypassed and the scan silently narrowed")
    finally:
        _modules_reaching_a_store.cache_clear()


def test_the_direction_scan_agrees_with_the_ban_on_the_FOLDED_spelling() -> None:
    """⛔ L1 AND L2 MUST AGREE THROUGH THE SAME FOLD, or the pair hides a reach.

    The scan compares against `_STORE_CLIENT_ROOTS` and `_FORBIDDEN_CLIENT_EXPORTS`;
    before the fold it saw `tortoise.__init__.FalkorDB`, which matches neither — so a
    module whose ONLY reach was `from tortoise.__init__ import FalkorDB` was red in
    `_offending` (L1) and absent from `found` (L2), leaving the coverage row green
    while the module stayed unbanned. This pins the shared predicate, so the two
    directions cannot drift apart again.
    """
    source = "def f():\n    from tortoise.__init__ import FalkorDB\n"
    # ⛔ CALLS THE SHARED PREDICATE, does not re-derive it. An inline copy here left
    # the fold at the scan's call site unpinned (reverting it kept every row green),
    # so the pin has to exercise the function the scan actually uses.
    assert _reaches_a_store_client(_imports_of(source)), (
        "the direction scan would not see this source, so L2 and L1 disagree about "
        "a module whose only reach is the `.__init__` spelling"
    )
    assert _offending(source) == {"tortoise.FalkorDB"}


def test_the_no_graph_guard_covers_every_module_that_reaches_a_store() -> None:
    """⛔ THE OTHER DIRECTION: a real graph-owning module may not be left out.

    Derived from the TREE rather than listed, so a future module that starts
    holding the client cannot slip past the guard the way `.projection` did.
    """
    uncovered = [
        module
        for module in _modules_reaching_a_store()
        if module not in _COVERAGE_EXEMPT  # cannot be banned; see the set's comment
        and not any(
            module == root or module.startswith(root + ".")
            for root in _FORBIDDEN_GRAPH_ROOTS
        )
    ]
    assert uncovered == [], (
        f"module(s) reach a graph client but are not in the forbidden set, so a "
        f"serve counter could import them unnoticed: {sorted(uncovered)}"
    )
    # ⛔ AND THE PROBE'S EXEMPTION MUST NOT BECOME A BAN EXEMPTION. This module
    # owns the embedded store and is exempt from the PROBE only (it is in
    # `sys.modules` before the probe runs); dropping it from the SET would drop the
    # AST ban too, which is the hole an earlier cut had. Asserted rather than
    # inferred from the coverage row above: that row skips only `_COVERAGE_EXEMPT`
    # (`{"tortoise"}`) and WOULD red on a dropped `embedded_lifecycle` today — but
    # it stops demanding the ban the moment the name moves into that exemption,
    # which is the lever this line pins directly. (This comment said the row "skips
    # exempt modules by construction", which is false: it skips the COVERAGE
    # exemption, not the probe one.)
    assert "tortoise.embedded_lifecycle" in _FORBIDDEN_GRAPH_ROOTS
    # ⛔ AND THE PROBE-EXEMPT LIST IS PINNED EXACTLY TOO, for the same reason the
    # coverage list is: `unbanned_probe_exempt` only enforces MEMBERSHIP, so a
    # member added here that IS in the ban set passes it — and the probe's name
    # list is built by REMOVING the exempt names, so adding a banned store root
    # here deletes that root's transitive check with every row green. Pinning the
    # tuple makes `_PROBE_EXEMPT = ("tortoise.embedded_lifecycle", "redis")` red
    # here instead of silently narrowing the probe this PR exists to keep.
    assert _PROBE_EXEMPT == ("tortoise.embedded_lifecycle",), (
        f"the probe exemption is {_PROBE_EXEMPT!r}; it may hold exactly the one "
        "module that is already in `sys.modules` before the probe runs. Adding a "
        "name here removes it from the probe's absent-list — a banned root added "
        "here drops its transitive check and reddens nothing else.")
    # ⛔ THE PROBE-EXEMPT LIST CANNOT BE A COVERAGE SKIP IN DISGUISE. Exempting from
    # the probe is only MEANINGFUL for a name the probe would otherwise assert
    # absent — one that is BANNED. A probe-exempt name that is not banned is
    # removed from the probe (a no-op) and excused from THIS row (a silent hole):
    # exactly the lever that made an earlier cut drop `embedded_lifecycle` from the
    # set only. So the relation is enforced, not documented.
    unbanned_probe_exempt = set(_PROBE_EXEMPT) - set(_FORBIDDEN_GRAPH_ROOTS)
    assert unbanned_probe_exempt == set(), (
        f"{sorted(unbanned_probe_exempt)} is exempt from the probe but is not in "
        "_FORBIDDEN_GRAPH_ROOTS, so the exemption buys nothing there and silently "
        "excuses it from THIS row — a ban exemption wearing a probe exemption's "
        "name. Ban it, or move it to _COVERAGE_EXEMPT with a stated reason.")
    # ⛔ AND THE COVERAGE EXEMPTION IS PINNED EXACTLY, because it is the remaining
    # lever: adding a genuinely store-reaching module to it would make this row
    # stop demanding the ban while the AST ban still does not name it. Pinning the
    # exact set makes that a deliberate edit that reddens here, not a quiet skip.
    assert set(_COVERAGE_EXEMPT) == {"tortoise"}, (
        f"the coverage exemption is {sorted(_COVERAGE_EXEMPT)!r}; only the package "
        "root may be excused (it is in `sys.modules` by construction, and banning "
        "the package would ban the module itself). A store-reaching module added "
        "here is invisible to the AST ban — ban it instead.")
    # ⛔ The disjointness of the two roles is NOT asserted again here: both lists
    # are pinned EXACTLY above (`_PROBE_EXEMPT` to one banned module, `_COVERAGE_EXEMPT`
    # to `{"tortoise"}`, which is not banned), so the intersection is empty before
    # any line here executes and no mutant could ever redden a third check — an
    # assertion that cannot fail is a claim of coverage it does not deliver.


def test_recording_never_raises_on_a_broken_path(tmp_path: Path) -> None:
    """A policy input may not break the read it observes."""
    bad = tmp_path / "a-directory" / "x.jsonl"
    bad.parent.mkdir()

    # A directory where the file should be: `open` fails, and the write is lost.
    (bad.parent / "x.jsonl").mkdir()

    assert usage_signal.record_serves("Point", [{"id": "p1"}], path=bad) == 0
    got = usage_signal.refresh(bad)
    assert got.served_total == 0


def test_refresh_never_raises_when_the_stream_disappears_mid_flight(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _serve(stream, "Point", ["p1"], at=_at(1))
    usage_signal.refresh(stream)

    real_stat = Path.stat

    def _boom(self: Path, *a: object, **k: object):
        if self == stream:
            raise OSError("gone")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", _boom)

    got = usage_signal.refresh(stream)

    assert got.stale is True
    assert got.error is not None


# ══════════════════════════════════════════════════════════════════════════
# The bound made OBSERVABLE — "incremental" is a measurement, not a claim
# ══════════════════════════════════════════════════════════════════════════


def test_an_idle_refresh_folds_no_bytes_and_rewrites_no_checkpoint(stream: Path) -> None:
    """A refresh with nothing appended does NO fold work.

    The reading reports the work it did, so a later "simplify by replaying the
    whole stream" refactor is visible instead of invisible — and a per-pass
    scorer does not pay O(entries) for an idle call.
    """
    _serve(stream, "Point", ["p1"], at=_at(1))
    usage_signal.refresh(stream)
    ckpt = usage_signal.checkpoint_path(stream)
    before = ckpt.stat().st_mtime_ns

    idle = usage_signal.refresh(stream)

    assert idle.bytes_folded == 0
    assert idle.lines_folded == 0
    assert idle.serves_folded == 0
    assert ckpt.stat().st_mtime_ns == before, "an idle refresh rewrote the checkpoint"


def test_incremental_folds_only_the_new_bytes(stream: Path) -> None:
    """⛔ THE BOUND, AS A NUMBER. A full replay cannot pass this.

    `replayed` is about whether a checkpoint EXISTED and `lines_read` is
    cumulative — neither can distinguish an incremental fold from a rebuild from
    byte 0. `bytes_folded`/`lines_folded` are this refresh's own work.
    """
    for hour in range(1, 11):
        _serve(stream, "Point", [f"p{hour}"], at=_at(hour))
    first = usage_signal.refresh(stream)
    assert first.bytes_folded == stream.stat().st_size, "the cold fold read the whole file"

    _serve(stream, "Point", ["p99"], at=_at(11))
    second = usage_signal.refresh(stream)

    assert second.bytes_folded == len(stream.read_bytes()) - first.offset
    assert second.lines_folded == 1
    assert second.serves_folded == 1
    assert second.lines_read == first.lines_read + 1


# ══════════════════════════════════════════════════════════════════════════
# The failures that must be IMPOSSIBLE to consume silently
# ══════════════════════════════════════════════════════════════════════════


def test_a_capped_counter_refuses_to_order(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ THE DOOR, not just the flag. A frozen counter must evict NOTHING.

    `capped` being reported is not enough: a caller that forgets to read it gets
    an ordering over a counter that stopped recording, in which every
    recently-served node has aged into "cold" — the pass then deletes the hottest
    nodes in the cache. Returning nothing evicts nothing, which is the safe
    direction; the caller loses throughput, never data.
    """
    monkeypatch.setenv(usage_signal.USAGE_MAX_BYTES_ENV, "400")
    usage_signal._warned.clear()
    _serve(stream, "Point", ["p1"], at=_at(1))
    for hour in range(2, 30):
        _serve(stream, "Point", [f"p{hour}"], at=_at(hour % 24))

    got = usage_signal.refresh(stream)

    assert got.capped is True
    assert got.orderable is False
    assert got.coldest_first() == []
    assert "STOPPED recording" in (got.refusal_reason() or "")


def test_an_empty_stream_is_not_a_confident_zero(stream: Path) -> None:
    """The `copytruncate` shape: the file exists, records nothing, looks healthy.

    Without this, after a rotation every candidate is "unserved", the caller falls
    back to creation recency — the policy the owner's criterion replaces — while
    the reading reports no error at all.
    """
    stream.write_text("", encoding="utf-8")

    got = usage_signal.refresh(stream)

    assert got.stale is True
    assert got.error == "empty stream"
    assert got.orderable is False
    assert got.coldest_first() == []
    # The door covers this cause too — without the assertion the refusal could be
    # removed here and the test would stay green, because `coldest_first` returns
    # `[]` over an empty entry map regardless.
    assert got.refusal_reason() is not None
    assert got.unserved_keys(["a", "b"]) == []


def test_a_forged_checkpoint_cannot_disable_the_prefix_check(stream: Path) -> None:
    """⛔ A checkpoint that says `head_len = 0` must be a CACHE MISS.

    `_state_matches_stream` short-circuits when there is no consumed prefix, so a
    per-field-valid checkpoint claiming `head_len = 0` alongside a real `offset`
    would silently disable the ONLY check that catches an in-place, same-size
    replacement — and the old fold would be merged onto the new file's content and
    reported as current.
    """
    _serve(stream, "Point", ["p1", "p2"], at=_at(1))
    usage_signal.refresh(stream)

    ckpt = usage_signal.checkpoint_path(stream)
    payload = json.loads(ckpt.read_text(encoding="utf-8"))
    assert payload["head_len"] > 0
    payload["head_len"] = 0
    ckpt.write_text(json.dumps(payload), encoding="utf-8")
    stream.write_text(
        stream.read_text(encoding="utf-8").replace("p1", "q1"), encoding="utf-8"
    )

    got = usage_signal.refresh(stream)

    assert got.replayed is True
    assert got.last_served("Point", "p1") is None
    assert got.serve_count("Point", "q1") == 1


def test_an_undated_entry_sorts_warmest(stream: Path) -> None:
    """⛔ "UNKNOWN TIME" IS NOT "OLDEST". One bad `at` must not evict a hot node.

    An entry with a count but no parseable stamp is KNOWN to have been served; its
    recency is unknown. Sorting it first is the module's own stated worst case —
    the eviction pass deletes the hottest node in the cache — reachable from a
    single hand-edited or foreign-written line.
    """
    _serve(stream, "Point", ["old"], at=_at(1))
    _serve(stream, "Point", ["fresh"], at=_at(9))
    with open(stream, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"at": "not-a-time", "event": "serve", "label": "Point", "key": "just_served"}
            )
            + "\n"
        )

    got = usage_signal.refresh(stream)

    assert got.undated_serves == 1
    assert [k for _lbl, k in got.coldest_first()] == ["old", "fresh", "just_served"]
    # …and it is NOT lumped in with the coordinates this counter has no opinion on.
    assert [e.key for e in got.undated_entries()] == ["just_served"]
    assert "just_served" not in got.unserved_keys(["just_served"])


def test_a_pooled_rollup_refuses_to_order(stream: Path) -> None:
    """⛔ Two tenants in one roll-up must not be ordered together.

    On one shared graph two tenants' ``(Point, id)`` pairs collide, so a pooled
    ordering scores one tenant's node on another tenant's reads — a node looks hot
    because a stranger read it, which keeps the wrong node resident.
    """
    _serve(stream, "Point", ["shared"], at=_at(1))
    usage_signal.record_serves(
        "Point", [{"id": "other"}], at=_at(9), path=stream, tenant_id="org-2"
    )

    pooled = usage_signal.refresh(stream)
    assert pooled.pooled is True
    assert pooled.tenants == 2
    assert pooled.coldest_first() == []
    assert "tenant" in (pooled.refusal_reason() or "")

    # …and one partition at a time is orderable.
    one = usage_signal.refresh(stream, tenant="org-2")
    assert one.pooled is False
    assert one.coldest_first() == [("Point", "other")]
    assert one.serve_count("Point", "shared") == 0


# ══════════════════════════════════════════════════════════════════════════
# The funnel wiring — a hook nothing drives is a hook that gets lost
# ══════════════════════════════════════════════════════════════════════════


def test_the_funnel_hook_is_off_by_default(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flag off ⇒ one env lookup and NOTHING written (the response is identical)."""
    from tortoise import sdk

    monkeypatch.delenv(usage_signal.USAGE_FLAG_ENV, raising=False)
    monkeypatch.setenv(usage_signal.USAGE_PATH_ENV, str(stream))

    sdk._usage_record("Point", [{"id": "p1"}])

    assert not stream.exists()


def test_the_funnel_hook_records_served_rows(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tortoise import sdk

    monkeypatch.setenv(usage_signal.USAGE_FLAG_ENV, "1")
    monkeypatch.setenv(usage_signal.USAGE_PATH_ENV, str(stream))

    sdk._usage_record("Point", [{"id": "p1"}, {"id": "p2"}])

    got = usage_signal.refresh(stream)
    assert sorted(e.key for e in got.entries.values()) == ["p1", "p2"]


def test_a_candidate_pool_is_never_counted_as_a_serve(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ The `_record_reads` guard, exercised rather than inspected.

    The FTS funnel fetches pools with ``_record_reads=False``. Counting a pool as
    served would mark a node the caller never saw as USED — the flattering
    direction, and the reason ``_usage_record_served`` exists as a named,
    testable step instead of a line inside a closure.
    """
    from tortoise import sdk

    monkeypatch.setenv(usage_signal.USAGE_FLAG_ENV, "1")
    monkeypatch.setenv(usage_signal.USAGE_PATH_ENV, str(stream))

    sdk._usage_record_served("Point", [{"id": "pooled_only"}], False)
    assert not stream.exists(), "a candidate POOL was recorded as a serve"

    sdk._usage_record_served("Point", [{"id": "served"}], True)
    got = usage_signal.refresh(stream)
    assert [e.key for e in got.entries.values()] == ["served"]


def test_the_shared_ledger_hook_also_feeds_the_counter(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 14 `_ledger_record` call sites are covered by ONE hook.

    If someone deletes the counter's call from `_ledger_record`, that is 14
    recorded read paths silently losing their serve signal — this is the test
    that notices.
    """
    from tortoise import sdk

    monkeypatch.setenv(usage_signal.USAGE_FLAG_ENV, "1")
    monkeypatch.setenv(usage_signal.USAGE_PATH_ENV, str(stream))
    monkeypatch.delenv("TORTOISE_READ_LEDGER", raising=False)

    sdk._ledger_record("Point", [{"id": "p1"}], kind="lookup")

    got = usage_signal.refresh(stream)
    assert [e.key for e in got.entries.values()] == ["p1"]


# ══════════════════════════════════════════════════════════════════════════
# Cycle-2 findings, pinned
# ══════════════════════════════════════════════════════════════════════════


def test_alternating_partitions_do_not_replay_the_whole_stream(stream: Path) -> None:
    """The door's own advice must not defeat the bound.

    ``refusal_reason()`` tells a pooled caller to "order one tenant at a time",
    and the default is ``ALL_TENANTS`` — so with ONE checkpoint per stream, the
    recommended usage re-folded the entire stream on every alternating call
    (bounded only by ``USAGE_MAX_BYTES``). The checkpoint is keyed by partition.
    """
    _serve(stream, "Point", ["p1"], at=_at(1))
    usage_signal.record_serves(
        "Point", [{"id": "other"}], at=_at(2), path=stream, tenant_id="org-2"
    )

    pooled = usage_signal.refresh(stream)
    one = usage_signal.refresh(stream, tenant="org-2")
    pooled_again = usage_signal.refresh(stream)
    one_again = usage_signal.refresh(stream, tenant="org-2")

    assert pooled.pooled is True
    assert one.pooled is False
    # ⛔ The BOUND, not just the result: only the FIRST call on each partition may
    # fold the stream.
    assert one_again.bytes_folded == 0
    assert pooled_again.bytes_folded == 0


def test_replay_matches_the_surviving_set_under_a_window(stream: Path) -> None:
    """What the replay invariant actually guarantees once pruning is on.

    ⚠️ NARROWER THAN THE UNBOUNDED CASE, deliberately. Pruning drops an entry
    INCLUDING its count, so a cold replay (which sees the events an incremental
    fold already pruned) legitimately disagrees on ``pruned`` and on a re-served
    key's ``serve_count``. The SURVIVING SET is what both paths must agree on, and
    that is what the ordering is built from.
    """
    _serve(stream, "Point", ["ancient"], at=_at(1))
    _serve(stream, "Point", ["kept"], at="2026-01-03T23:00:00+00:00")
    now = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)

    incremental = usage_signal.refresh(stream, now=now, window_days=2)
    for path in (usage_signal.checkpoint_path(stream),):
        path.unlink()
    # Every partition-tagged checkpoint is gone, so the next call replays.
    for extra in stream.parent.glob(f"{stream.name}.*.rollup.json"):
        extra.unlink()
    replayed = usage_signal.refresh(stream, now=now, window_days=2)

    assert replayed.replayed is True
    assert set(incremental.entries) == set(replayed.entries), (
        "the surviving coordinate SET must be identical; only the cumulative "
        "counters are history-dependent"
    )
    assert incremental.coldest_first() == replayed.coldest_first()


def test_the_checkpoint_is_not_world_readable(stream: Path) -> None:
    """The checkpoint carries the same coordinates the stream's 0600 protects.

    `Source.url` / `Object` / `Subject` keys identify a tenant's content, so a
    checkpoint written at the umask's 0644 beside a 0600 stream leaves every key
    readable by any local user on a 0755 store directory.
    """
    _serve_row(stream, "Source", "https://a.example/secret", at=_at(1))

    got = usage_signal.refresh(stream)
    ckpt = usage_signal.checkpoint_path(stream)

    assert got.served_total == 1
    assert ckpt.exists()
    assert (ckpt.stat().st_mode & 0o077) == 0, (
        f"checkpoint mode {oct(ckpt.stat().st_mode & 0o777)} exposes (label, key) "
        f"coordinates the 0600 stream exists to protect"
    )


def test_a_same_inode_shrink_forces_a_cold_replay(stream: Path) -> None:
    """`copytruncate` in place: same inode, shorter file.

    ⚠️ The RESIDUAL this cannot cover, named rather than implied: a SAME-SIZE
    in-place rewrite of bytes already folded is undetectable to any incremental
    fold — the stream's contract is append-only (``O_APPEND``), and a rotation that
    rewrites in place must remove the checkpoint. A shrink IS detectable and is.
    """
    _serve(stream, "Point", ["p1", "p2", "p3"], at=_at(1))
    usage_signal.refresh(stream)
    inode_before = stream.stat().st_ino

    with open(stream, "r+", encoding="utf-8") as fh:  # same inode, truncated
        fh.seek(0)
        fh.write('{"at":"2026-02-01T00:00:00+00:00","event":"serve","label":"Point","key":"new"}\n')
        fh.truncate()

    got = usage_signal.refresh(stream)

    assert stream.stat().st_ino == inode_before, "the test did not exercise the in-place shape"
    assert got.replayed is True
    assert [e.key for e in got.entries.values()] == ["new"]


def test_a_bad_stream_path_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`refresh` is documented NEVER to raise — and it is on the armed path.

    `checkpoint_path` calls `Path.with_suffix`, which raises `ValueError` for a
    path with an empty name; `TORTOISE_USAGE_SIGNAL_PATH=.` reaches that from
    configuration alone. The unarmed WRITER already degraded correctly, so a raise
    here landed on the armed consumer and nothing said so.
    """
    monkeypatch.setenv(usage_signal.USAGE_PATH_ENV, ".")

    got = usage_signal.refresh()

    assert got.stale is True
    assert got.orderable is False
    assert "bad path" in (got.error or "")


def test_the_accessors_return_nothing_on_a_refused_reading(stream: Path) -> None:
    """⛔ THE DOOR IS ON EVERY READ, not just `coldest_first`.

    The residency policy composes ``recency_key`` + its class booster (that is what
    the docstring tells it to do), so a frozen or pooled counter returning a
    healthy-looking tuple HERE hands the policy exactly the hazard the refusal
    exists to prevent — at the door it recommends. "No signal" is the only safe
    answer, and it is what a refused reading gives.
    """
    _serve(stream, "Point", ["hot"], at=_at(5))
    # An UNDATED serve too, so `undated_entries` has content to withhold — without
    # it the refusal below is vacuously satisfied and the guard is unpinned.
    with open(stream, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {"at": "not-a-time", "event": "serve", "label": "Point", "key": "undated"}
            )
            + "\n"
        )
    healthy = usage_signal.refresh(stream)
    assert healthy.recency_key("Point", "hot") is not None  # the control
    assert [e.key for e in healthy.undated_entries()] == ["undated"]

    for refusal in ({"capped": True}, {"stale": True}):
        refused = usage_signal.UsageReading(**{**healthy.__dict__, **refusal})
        assert refused.orderable is False
        assert refused.recency_key("Point", "hot") is None
        assert refused.serve_count("Point", "hot") == 0
        assert refused.last_served("Point", "hot") is None
        assert refused.entry("Point", "hot") is None
        assert refused.undated_entries() == []


def test_a_tenant_with_only_an_unresolvable_row_does_not_latch_pooled(
    stream: Path,
) -> None:
    """`pooled` must be derived from the ENTRIES, not from every tenant SEEN.

    A tenant that contributed only an unresolvable row gets no entry, so a bind on
    ``state.tenants`` latched a PERMANENT pooled refusal over a roll-up whose
    entries are entirely one tenant's — and it never ages off, because a replay
    re-derives it from the same events.
    """
    _serve(stream, "Point", ["mine"], at=_at(1))
    # org-2's only row is unaddressable (`Source` keys on `url`, not `sourcePath`).
    usage_signal.record_serves(
        "Source", [{"sourcePath": "/not-a-url"}], at=_at(2), path=stream, tenant_id="org-2"
    )

    got = usage_signal.refresh(stream)

    assert got.unresolved_skips == 1
    assert got.pooled is False, "one tenant's unresolvable row latched a pooled refusal"
    assert got.orderable is True
    assert got.coldest_first() == [("Point", "mine")]


def test_a_checkpoint_whose_tenants_disagree_with_its_entries_is_a_cache_miss(
    stream: Path,
) -> None:
    """The `tenants ⊇ entry tenants` invariant, in the shape `head_len` has.

    Without it, a checkpoint claiming NO partitions while its entries span two
    passes every per-field validator and opens the ordering door on exactly the
    reading the pool refusal exists for — one tenant's node scored on another's
    reads.
    """
    _serve(stream, "Point", ["mine"], at=_at(1))
    usage_signal.record_serves(
        "Point", [{"id": "theirs"}], at=_at(2), path=stream, tenant_id="org-2"
    )
    usage_signal.refresh(stream)  # pooled; a valid checkpoint now exists

    ckpt = usage_signal.checkpoint_path(stream)
    payload = json.loads(ckpt.read_text(encoding="utf-8"))
    assert payload["tenants"]  # the valid shape
    payload["tenants"] = []
    ckpt.write_text(json.dumps(payload), encoding="utf-8")

    got = usage_signal.refresh(stream)

    assert got.replayed is True, "a self-contradicting checkpoint was trusted"
    assert got.pooled is True
    assert got.coldest_first() == []


# ══════════════════════════════════════════════════════════════════════════
# The window bound: a refusal must be AUDIBLE, never byte-identical to "unset"
# ══════════════════════════════════════════════════════════════════════════


def test_a_non_positive_window_is_not_a_silent_unbounded_rollup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """⛔ #14011 review finding 3: `0` / `-1` read as "not configured".

    The `ValueError` arm already warns that a mistyped bound leaves the roll-up
    UNBOUNDED; the numeric arm returned `None` without a word, so
    `WINDOW_DAYS=0` produced a reading byte-identical to no window at all
    (`pruned=0`, `pruned_before=None`) while the roll-up kept every entry — the
    quiet direction on the one parameter whose job is bounding memory.
    """
    import logging

    for raw in ("0", "-1", "0.0"):
        monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, raw)
        usage_signal._warned.clear()
        caplog.clear()

        with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
            assert usage_signal._window_days() is None

        assert any(
            "UNBOUNDED" in record.getMessage() for record in caplog.records
        ), (
            f"{usage_signal.USAGE_WINDOW_DAYS_ENV}={raw} was silently accepted as "
            f"'no window' — the operator is never told the roll-up is unbounded"
        )


def test_a_typo_and_a_non_positive_window_are_warned_about_SEPARATELY(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """⛔ The typo arm and the non-positive arm must not share one `_warn_once` key.

    `_warn_once` is once-per-process, and both arms used the key `"window"` — so
    an operator who mistyped the value (warned once), then fixed it to `0`,
    never saw the second refusal: the suppressed message is the one carrying the
    NEWER `raw`. The non-finite arm is already a separate key for this reason;
    the non-positive arm now is too.

    A single-value test cannot see this — the collision only shows up in a
    SEQUENCE inside one process, which is why the two values are exercised
    against an uncleared `_warned` here.
    """
    import logging

    monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, "seven")
    usage_signal._warned.clear()
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        assert usage_signal._window_days() is None
        assert len(caplog.records) == 1, [r.getMessage() for r in caplog.records]

        # The operator fixes the typo to a number that is still no bound.
        monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, "0")
        assert usage_signal._window_days() is None
        messages = [record.getMessage() for record in caplog.records]

    assert len(messages) == 2, (
        "the second refusal was swallowed — both arms share a `_warn_once` key: "
        f"{messages}"
    )
    assert "not a day count" in messages[0]
    assert "not a positive day count" in messages[1]


def test_one_cause_reached_through_BOTH_doors_warns_on_each(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """⛔ A cause-keyed `_warn_once` is still one key for two DOORS.

    `_window_days` resolves the same question twice: once from the environment
    and once from the call argument. Keying by cause alone
    (`"window"`, `"window-nonpositive"`) leaves the env door able to consume
    the key, so the ARG door's refusal — the one naming the CALLER's value — is
    swallowed by `_warn_once`'s once-per-process contract. The message is not
    the same either: each door reports its own `origin` and `raw`.

    A single-door test cannot see this; the collision needs a SEQUENCE across
    both doors inside one process, which is why `_warned` is cleared ONCE here
    and never BETWEEN the three cause pairs.
    """
    import logging

    usage_signal._warned.clear()
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        # Cause 1 (a typo), env door then arg door.
        monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, "seven")
        assert usage_signal._window_days() is None
        assert usage_signal._window_days("2") is None

        # Cause 2 (non-positive), same two doors.
        monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, "0")
        assert usage_signal._window_days() is None
        assert usage_signal._window_days(0) is None

        # Cause 3 (non-finite), same two doors — the third door-qualified key.
        monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, "nan")
        assert usage_signal._window_days() is None
        assert usage_signal._window_days(float("nan")) is None

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 6, (
        "a door's refusal was swallowed — the two doors still share a key: "
        f"{messages}"
    )
    assert "not a day count" in messages[0]
    assert usage_signal.USAGE_WINDOW_DAYS_ENV in messages[0]
    assert "not a day count" in messages[1]
    assert "window_days='2'" in messages[1], (
        "the argument door's warning must name the CALLER's value: "
        f"{messages[1]}"
    )
    assert "not a positive day count" in messages[2]
    assert usage_signal.USAGE_WINDOW_DAYS_ENV in messages[2]
    assert "not a positive day count" in messages[3]
    assert "window_days" in messages[3] and "argument" in messages[3], (
        "the argument door's warning must name ITS origin, not the env's: "
        f"{messages[3]}"
    )
    assert "not a FINITE day count" in messages[4]
    assert usage_signal.USAGE_WINDOW_DAYS_ENV in messages[4]
    assert "not a FINITE day count" in messages[5]
    assert "argument" in messages[5], (
        "the argument door's non-finite warning must name ITS origin: "
        f"{messages[5]}"
    )


def test_the_float_overflow_and_the_timedelta_one_warn_on_EACH(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """⛔ Two REFUSAL CAUSES on the argument door must not share one `_warn_once` key.

    `_as_float` keyed its refusal `"window-overflow-arg"`, and `_checked_window`
    builds its timedelta-width key as `f"window-overflow-{door}"` — the SAME
    string on the argument door. `_warn_once` is once-per-process, so the first
    refusal ate the second: a caller who passed `10**400` (unrepresentable as a
    float) and then `1e10` (finite, wider than `timedelta.max`) saw only the
    first warning, about the value they had already replaced.

    A single-value test cannot see this — the collision only appears in a
    SEQUENCE inside one process, so `_warned` is never cleared between the two
    calls here. This is the same class the sibling arms above were split to fix;
    that split keyed on the DOOR, which re-created it per CAUSE.
    """
    import logging

    usage_signal._warned.clear()
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        # Cause A: no float can represent this value at all.
        assert usage_signal._window_days(10**400) is None
        assert len(caplog.records) == 1, [
            record.getMessage() for record in caplog.records
        ]

        # Cause B: finite, but wider than a `timedelta` can carry — a DIFFERENT
        # refusal with a different remedy, and it must still be said.
        assert usage_signal._window_days(1e10) is None
        messages = [record.getMessage() for record in caplog.records]

    assert len(messages) == 2, (
        "the second refusal was swallowed — the float arm and the timedelta arm "
        f"share a `_warn_once` key on the argument door: {messages}"
    )
    assert "too large to be a day count" in messages[0]
    assert "a `timedelta` cannot carry" in messages[1], (
        "the surviving message must be the NEWER cause's, not the one already "
        f"replaced by the caller: {messages[1]}"
    )


def test_a_window_a_timedelta_cannot_carry_is_refused_not_raised(
    stream: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """⛔ A FINITE bound wider than `timedelta` can hold must not escape as a raise.

    `1e10` passes every arm above — it is positive, finite and parses — and then
    `timedelta(days=…)` raises `OverflowError` out of `refresh`, which promises
    it never raises and is called from the ops door. Both doors are exercised:
    the env value and the call argument must be refused in the same voice as the
    typo / non-positive / non-finite arms, and the roll-up must stay bounded by
    "keep everything" rather than by a traceback.
    """
    import logging

    usage_signal._warned.clear()
    caplog.clear()
    _serve(stream, "Point", ["ancient"], at=_at(1))
    now = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)

    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        from_arg = usage_signal.refresh(stream, now=now, window_days=1e10)
        monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, "10000000000")
        from_env = usage_signal.refresh(stream, now=now)

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2, (
        "a window too wide for a `timedelta` was not refused on both doors: "
        f"{messages}"
    )
    assert all("cannot carry" in m and "UNBOUNDED" in m for m in messages)
    assert "argument" in messages[0]
    assert usage_signal.USAGE_WINDOW_DAYS_ENV in messages[1]
    for got in (from_arg, from_env):
        assert got.entry("Point", "ancient") is not None, (
            "an unappliable bound pruned an entry — it was read as a real bound"
        )
        assert got.pruned == 0


@pytest.mark.parametrize("band", [1e6, 739620.0])
def test_a_window_past_the_earliest_timestamp_is_refused_not_raised(
    stream: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    band: float,
) -> None:
    """⛔ The band BELOW `timedelta`'s limit, which raised out of `refresh`.

    `timedelta` can represent 999999999 days, so `_checked_window`'s overflow arm
    lets `1e6` through — and then `ref - timedelta(days=…)` raises
    `OverflowError: date value out of range` (a `datetime`, not a `timedelta`, has
    to carry the result). From a 2026 `now` the raise starts at 739620 days, so
    `1e6` — a plausible spelling of "never prune" — is inside it. Both doors are
    driven: the env door reaches the same subtraction.
    """
    import logging

    usage_signal._warned.clear()
    caplog.clear()
    _serve(stream, "Point", ["ancient"], at=_at(1))
    now = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)

    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        from_arg = usage_signal.refresh(stream, now=now, window_days=band)
        monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, repr(band))
        from_env = usage_signal.refresh(stream, now=now)

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1, (
        "the underflow guard is ONE code path (the doors are already resolved "
        f"into `window`), so its key is not per-door — got: {messages}"
    )
    assert "earliest" in messages[0] and "UNBOUNDED" in messages[0]
    for got in (from_arg, from_env):
        assert got.entry("Point", "ancient") is not None
        assert got.pruned == 0
        assert got.window_started is None


def test_a_positive_window_is_returned_and_warns_about_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The other polarity: the warning belongs to bad values, not to every call."""
    import logging

    monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, "7")
    usage_signal._warned.clear()
    caplog.clear()

    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        assert usage_signal._window_days() == 7.0

    assert not caplog.records

    # …and UNSET is the documented default: no window, and no warning either.
    monkeypatch.delenv(usage_signal.USAGE_WINDOW_DAYS_ENV)
    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        assert usage_signal._window_days() is None

    assert not caplog.records


def test_an_explicit_none_turns_an_env_window_off(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ENV_WINDOW` and `None` are DIFFERENT requests: default vs "no window".

    ⛔ Collapsing them — `refresh(window_days=None)` falling through to the
    environment, which is what the pre-`ENV_WINDOW` resolver did — makes the
    documented tuning knob one-directional: an operator who sets the env var
    cannot turn it off for one call. The same stream is refreshed twice so the
    two requests cannot both be reported by one reading.
    """
    monkeypatch.setenv(usage_signal.USAGE_WINDOW_DAYS_ENV, "2")
    _serve(stream, "Point", ["ancient"], at=_at(1))
    _serve(stream, "Point", ["recent"], at="2026-01-03T23:00:00+00:00")
    now = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)

    # The DEFAULT (`ENV_WINDOW`) reads the env: the 3-day-old serve is pruned.
    from_env = usage_signal.refresh(stream, now=now)
    assert from_env.entry("Point", "ancient") is None
    assert from_env.pruned == 1

    # …and an EXPLICIT `None` turns that same env window off, per call.
    off = usage_signal.refresh(stream, now=now, window_days=None)
    assert off.entry("Point", "ancient") is not None, (
        "`window_days=None` fell back to the env window — the knob is one-directional"
    )
    assert off.pruned == 0


@pytest.mark.parametrize("given", [0, -1, 0.0])
def test_an_explicit_non_positive_window_is_refused_loudly(
    stream: Path,
    caplog: pytest.LogCaptureFixture,
    given: float,
) -> None:
    """The EXPLICIT door refuses in the same voice as the env door.

    ⛔ Two doors, one refusal: `_window_days` resolves both the env value and the
    call argument, so a `0` given here cannot be silently unbounded while the
    same `0` in the env is refused. The message is the one `_checked_window`
    writes ("not a positive day count"), NOT a second spelling that could drift
    from it.
    """
    import logging

    usage_signal._warned.clear()
    caplog.clear()
    now = datetime(2026, 1, 4, 0, 0, tzinfo=UTC)
    _serve(stream, "Point", ["ancient"], at=_at(1))

    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        got = usage_signal.refresh(stream, now=now, window_days=given)

    messages = [record.getMessage() for record in caplog.records]
    assert any("not a positive day count" in m for m in messages), messages
    assert got.entry("Point", "ancient") is not None, (
        f"window_days={given!r} pruned — one parameter whose job is bounding "
        "memory was read as a real bound"
    )
    assert got.pruned == 0


def test_a_non_numeric_window_argument_is_refused_not_silently_dropped(
    stream: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stated bound the call cannot read warns — it does not vanish.

    The type allows `str` only for the `"env"` sentinel, so anything else is a
    caller bug. Dropping it silently is the quiet direction this module refuses
    everywhere else.
    """
    import logging

    monkeypatch.delenv(usage_signal.USAGE_WINDOW_DAYS_ENV, raising=False)
    usage_signal._warned.clear()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        assert usage_signal._window_days("7") is None
        assert usage_signal._window_days("env") is None  # the sentinel, unset env

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1, messages
    assert "not a day count" in messages[0]


def test_the_funnel_hands_the_pool_guard_its_record_reads_flag() -> None:
    """The funnel must PASS `_record_reads`, not a literal the helper is tested on.

    `_usage_record_served`'s own parameter is exercised elsewhere, but the call
    site is what decides whether a candidate pool becomes a serve. A mutant that
    replaces the argument with a literal `True` at the funnel keeps every
    behavioural test green — a node the caller never saw is then marked USED,
    which is the flattering direction for a residency score. Structural because
    reaching the funnel behaviourally needs a live graph.
    """
    import ast

    from tortoise import sdk as sdk_module

    tree = ast.parse(Path(sdk_module.__file__).read_text(encoding="utf-8"))
    parents: dict[object, object] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    sites: list[tuple[object, list[object]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_usage_record_served":
            chain: list[object] = []
            cursor: object = node
            while cursor in parents:
                cursor = parents[cursor]
                chain.append(cursor)
            sites.append((node, chain))

    assert len(sites) == 1, (
        f"expected exactly ONE `_usage_record_served` call site (the FTS funnel); "
        f"found {len(sites)}"
    )
    call, _chain = sites[0]
    assert len(call.args) == 3, (
        "the funnel's call must pass (label, rows, _record_reads); a shortened "
        "call drops the pool guard"
    )
    third = call.args[2]
    assert isinstance(third, ast.Name) and third.id == "_record_reads", (
        "the funnel passes something other than `_record_reads` as the serve "
        "guard — a candidate POOL would be counted as served"
    )


# ══════════════════════════════════════════════════════════════════════════
# Cycle-3 findings, pinned
# ══════════════════════════════════════════════════════════════════════════


def test_an_in_place_rewrite_during_a_refresh_is_detected_next_time(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The prefix is captured BEFORE the body read, so a rewrite launders nothing.

    ⛔ Taking the fingerprint AFTER folding let it certify bytes the fold never saw.
    Measured on the pre-fix code: four coordinates the stream did not contain were
    reported as current, `orderable=True`, permanently — inode, size and the
    re-read prefix all agreed with themselves, so nothing ever re-checked, while a
    cold replay of the same file showed a different set.

    Capturing first means such a rewrite leaves the OLD digest in the checkpoint,
    the next refresh mismatches, and the fold replays from cold: fail-closed and
    self-healing.
    """
    _serve(stream, "Point", ["old1", "old2"], at=_at(1))
    usage_signal.refresh(stream)

    real_consume = usage_signal._consume

    def _rewrite_then_consume(state, chunk, *, partition):
        # A same-inode `copytruncate` landing between the prefix read and the save.
        with open(stream, "w", encoding="utf-8") as fh:
            for key in ("new1", "new2", "new3"):
                fh.write(
                    json.dumps(
                        {"at": _at(9), "event": "serve", "label": "Point", "key": key}
                    )
                    + "\n"
                )
        return real_consume(state, chunk, partition=partition)

    _serve(stream, "Point", ["old3"], at=_at(2))
    monkeypatch.setattr(usage_signal, "_consume", _rewrite_then_consume)
    usage_signal.refresh(stream)
    monkeypatch.setattr(usage_signal, "_consume", real_consume)

    healed = usage_signal.refresh(stream)
    cold = usage_signal.refresh(stream, force=True)

    assert healed.replayed is True, "a rewrite during the previous refresh was laundered"
    assert sorted(e.key for e in healed.entries.values()) == sorted(
        e.key for e in cold.entries.values()
    ), "the healed reading disagrees with a cold replay of the same file"
    assert healed.serve_count("Point", "old1") == 0


def test_the_pruning_window_is_part_of_the_checkpoint_identity(stream: Path) -> None:
    """A windowed fold must not hand its pruning to the next unbounded caller.

    `tenant` is bound into the checkpoint identity because it is a fold parameter
    that changes which events are read. `window_days` is the same kind of
    parameter and it DESTROYS entries, so a windowed read written into the shared
    cache was inherited by an unbounded caller with no replay: `serve_count`
    returned 0 for a coordinate whose serve was still in the stream and
    `coldest_first` omitted it — so the node read as "no opinion" and the caller's
    creation-recency tie-break took over.
    """
    _serve(stream, "Point", ["ancient"], at=_at(1))
    _serve(stream, "Point", ["fresh"], at="2026-01-04T23:00:00+00:00")
    now = datetime(2026, 1, 5, tzinfo=UTC)

    windowed = usage_signal.refresh(stream, now=now, window_days=2)
    assert windowed.serve_count("Point", "ancient") == 0

    unbounded = usage_signal.refresh(stream, now=now, window_days=None)

    assert unbounded.replayed is True, "the unbounded caller inherited a windowed fold"
    assert unbounded.pruned == 0
    assert unbounded.serve_count("Point", "ancient") == 1, (
        "a coordinate still in the stream read as 'no opinion' because another "
        "caller had pruned it"
    )


def test_a_pre_fix_checkpoint_is_a_cache_miss_so_the_fix_is_retroactive(
    stream: Path,
) -> None:
    """⛔ AN UPGRADE MUST NOT INHERIT A PRUNED FOLD WRITTEN BY THE PREVIOUS CODE.

    The pre-PR writer pruned when a window was in effect but wrote NO `window_days`
    into the checkpoint. `_deserialise` reads a missing field as `None`, which
    `_checkpoint_matches` then MATCHED against an unbounded caller's `None` — so
    the unbounded read EXTENDED a fold another window had already destroyed,
    permanently, because the checkpoint kept matching. The format bump makes every
    pre-PR checkpoint a cache MISS, which is what `CHECKPOINT_VERSION` is for.
    """
    _serve(stream, "Point", ["ancient"], at=_at(1))
    usage_signal.refresh(stream, now=datetime(2026, 1, 20, tzinfo=UTC), window_days=2)

    ckpt = usage_signal.checkpoint_path(stream)
    payload = json.loads(ckpt.read_text(encoding="utf-8"))
    # The pre-PR on-disk shape: no `window_days` field, and the pre-PR version.
    payload.pop("window_days", None)
    payload["version"] = 1
    ckpt.write_text(json.dumps(payload), encoding="utf-8")

    unbounded = usage_signal.refresh(stream, window_days=None)

    assert unbounded.replayed is True, "a pre-PR checkpoint was extended, not replayed"
    assert unbounded.pruned == 0
    assert unbounded.serve_count("Point", "ancient") == 1, (
        "a coordinate still in the stream read as 'no opinion' because the upgrade "
        "inherited a windowed fold"
    )


def test_the_write_path_repairs_a_torn_tail(stream: Path) -> None:
    """A batch appended onto a torn final line must not concatenate into it.

    ⛔ THE WRITE-SIDE GUARD, which had no test. Without the newline repair the
    fragment and the batch's first line merge, that record is LOST — while
    `record_serves` still reports the serve it "delivered", breaking the
    what-landed-not-what-was-intended contract that number exists for. Measured on
    the unguarded code: returns 1, lands 0, `served_total` 0.
    """
    _serve(stream, "Point", ["p1"], at=_at(1))
    with open(stream, "a", encoding="utf-8") as fh:
        fh.write('{"at":"2026-01-02T00:00:00+00:00","event":"serve","label":"Po')  # no newline

    assert _serve(stream, "Point", ["p2"], at=_at(3)) == 1

    got = usage_signal.refresh(stream)
    # ⚠️ Assert on the ENTRIES, not `serve_count`: the torn fragment is a skipped
    # line, and an INCOMPLETE counter now refuses at the door — which would make
    # the accessor return 0 whether or not the serve landed. The conflation of
    # "refused" with "not there" is the bug this assertion exists to avoid.
    assert [e.key for e in got.entries.values() if e.label == "Point"] == ["p1", "p2"], (
        "the serve was reported but never landed"
    )
    assert got.served_total == 2


def test_a_counter_with_no_serves_refuses(stream: Path) -> None:
    """An armed-but-quiet counter has no opinion — and must not say the opposite.

    `orderable=True` here made `unserved_keys` declare EVERY candidate unserved:
    the creation-recency fallback this signal exists to remove, delivered from a
    reading the cache would treat as healthy.
    """
    _serve(stream, "Point", ["p1"], at=_at(1))
    usage_signal.refresh(stream, tenant="someone-else")

    got = usage_signal.refresh(stream, tenant="someone-else")

    assert got.served_total == 0
    assert got.orderable is False
    assert got.unserved_keys(["a", "b"]) == []
    assert "no serves observed" in (got.refusal_reason() or "")


def test_a_window_that_prunes_everything_refuses(stream: Path) -> None:
    """⛔ A WINDOW WIDER THAN THE STREAM'S AGE IS NOT "RECENTLY QUIET".

    `served_total` is CUMULATIVE and `prune()` never decrements it, so a bounded
    read whose surviving set is empty reported `orderable=True` with
    `served_total == 1`: `unserved_keys` then declared EVERY candidate unserved and
    the caller took its creation-recency tie-break — verbatim the outcome
    `refusal_reason` says this door exists to prevent, reached through the
    documented tuning knob instead of through never observing. `window_days=0` is
    refused for the same reason; a window this wide now reaches the same refusal.
    """
    _serve(stream, "Point", ["hot"], at=_at(1))

    got = usage_signal.refresh(
        stream, now=datetime(2026, 1, 20, tzinfo=UTC), window_days=2
    )

    assert got.entries == {}
    assert got.pruned == 1
    assert got.served_total == 1, "the guard must not be `served_total == 0`"
    assert got.orderable is False
    assert got.coldest_first() == []
    assert got.unserved_keys(["hot", "never-served"]) == [], (
        "every candidate declared unserved is the creation-recency fallback"
    )
    assert got.serve_count("Point", "hot") == 0, "a refused reading answers no number"
    assert "surviving roll-up entries" in (got.refusal_reason() or "")
    assert "creation-recency" in (got.refusal_reason() or "")


def test_a_pruned_rollup_refuses_to_certify_any_key_as_unserved(stream: Path) -> None:
    """⛔ PRUNING ERASES THE DIFFERENCE BETWEEN "pruned" AND "never served".

    `prune()` deletes the entry and records only a watermark, so a candidate absent
    from `entries` may have been served (before `pruned_before`) or may never have
    been served, and no field of the reading distinguishes them. Certifying either
    is a claim the stream does not support, so the signal certifies none of them —
    the caller keeps its own tie-break, which is the safe direction (an unfilled
    candidate is not ordered cold).
    """
    _serve(stream, "Point", ["ancient"], at=_at(1))
    _serve(stream, "Point", ["recent"], at="2026-01-03T23:00:00+00:00")

    got = usage_signal.refresh(
        stream, now=datetime(2026, 1, 4, tzinfo=UTC), window_days=2
    )

    assert got.pruned == 1
    assert got.orderable is True, "a PARTIAL pruning is still orderable"
    assert got.entry("Point", "recent") is not None
    assert got.unserved_keys(["ancient", "brand-new"], label="Point") == []

    # …and with nothing pruned the same call answers, so the guard is not vacuous.
    unbounded = usage_signal.refresh(stream, window_days=None)
    assert unbounded.pruned == 0
    assert unbounded.unserved_keys(["brand-new"], label="Point") == ["brand-new"]


def test_an_int_too_large_to_be_a_float_is_refused_not_raised(
    stream: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """⛔ `10**400` is an `int`, so every type arm accepts it — and `float()` raises.

    The conversion sat between the type check and the refusal arms, so a stated
    bound nobody can represent escaped `refresh` as `OverflowError: int too large
    to convert to float` instead of being refused in the same voice as the typo,
    non-positive, non-finite and too-wide arms. Pinned at BOTH levels: the
    resolver warns once, and `refresh` returns a reading instead of raising.
    """
    import logging

    usage_signal._warned.clear()
    caplog.clear()
    _serve(stream, "Point", ["ancient"], at=_at(1))

    with caplog.at_level(logging.WARNING, logger=usage_signal.__name__):
        assert usage_signal._window_days(10**400) is None
        got = usage_signal.refresh(
            stream, now=datetime(2026, 1, 4, tzinfo=UTC), window_days=10**400
        )

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1, messages
    assert "too large" in messages[0] and "UNBOUNDED" in messages[0]
    assert got.entry("Point", "ancient") is not None
    assert got.pruned == 0


def test_the_UNDATED_prune_arm_also_refuses_to_certify_unserved(stream: Path) -> None:
    """⛔ #14097: the second prune arm leaves `pruned == 0`, so the guard missed it.

    An entry whose serves all carried an unparseable `at` is dropped by the window
    too, but nothing places it against the watermark — so the reading reports
    `pruned == 0` / `pruned_before is None` and only `pruned_undated` moves. A guard
    that read `pruned` alone therefore stayed open while SERVED keys were deleted,
    and returned them certified as never served: the flattering direction for a
    residency score, and a claim the stream cannot support.
    """
    undated = "2026-01-02T00:00:00+00:00"
    for key in ("undated-a", "undated-b"):
        _serve_row(stream, "Point", key, at="not-a-timestamp")
    _serve(stream, "Point", ["recent"], at=undated)

    got = usage_signal.refresh(
        stream, now=datetime(2026, 1, 4, tzinfo=UTC), window_days=2
    )

    assert got.pruned == 0, "the dated watermark must not claim the undated arm"
    assert got.pruned_undated == 2
    assert got.orderable is True
    assert got.unserved_keys(["undated-a", "undated-b", "never"], label="Point") == [], (
        "the undated prune arm deleted SERVED keys and the reading certified them "
        "as never served — the caller reads that as 'no signal' (cold-ordered)"
    )


def test_an_undated_only_prune_states_what_it_removed(stream: Path) -> None:
    """⛔ The empty-roll-up caveat must name the arm that emptied it (#14011 review 2).

    When the UNDATED arm clears the window, `pruned == 0` and
    `pruned_before is None` by design — so the refusal message, which hardcoded
    both, read "0 entr(ies) last served at or before None were pruned" while
    three served entries HAD been dropped. That is the one place a caller is told
    why nothing survived, and it denied the deletion it was explaining.

    The existing pin for this branch asserted only the fixed substrings
    ("surviving roll-up entries" / "creation-recency"), so the numbers could be
    false without any test noticing.
    """
    for key in ("undated-a", "undated-b", "undated-c"):
        _serve_row(stream, "Point", key, at="not-a-timestamp")

    got = usage_signal.refresh(
        stream, now=datetime(2026, 1, 4, tzinfo=UTC), window_days=7
    )

    assert got.entries == {}
    assert got.pruned == 0, "the dated watermark must not claim the undated arm"
    assert got.pruned_before is None
    assert got.pruned_undated == 3

    reason = got.refusal_reason() or ""
    assert "no surviving roll-up entries" in reason
    assert "3 entr(ies) with no parseable `at` were pruned" in reason, reason
    assert "before None" not in reason, (
        "the dated watermark cannot describe entries that have no time: " f"{reason}"
    )
    assert "0 entr(ies)" not in reason, (
        "the caveat denies the deletion it exists to explain: " f"{reason}"
    )


def test_an_incomplete_counter_refuses(stream: Path) -> None:
    """A skipped line is the only trace of a lost `capped` marker.

    A torn marker that no later append heals re-opens the frozen-counter door with
    nothing else to notice it, so an incomplete counter refuses for the same reason
    a capped one does.
    """
    _serve(stream, "Point", ["p1"], at=_at(1))
    with open(stream, "a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    got = usage_signal.refresh(stream)

    assert got.lines_skipped == 1
    assert got.orderable is False
    assert got.coldest_first() == []
    assert "INCOMPLETE" in (got.refusal_reason() or "")
    # ⛔ THE REMEDY IS NAMED, because this refusal is PERMANENT for the stream: the
    # unparseable line is a complete line now, so it folds as a skip for ever. An
    # operator told "refused" with no way out deletes the checkpoint, which cannot
    # help — a cold replay re-reads the same line.
    assert "Rotate the stream" in (got.refusal_reason() or "")
    assert "deleting the checkpoint does not" in (got.refusal_reason() or "")


def test_capped_clears_when_recording_resumes(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remedy the warning names must actually work.

    ⛔ A LATCHED `capped` refused for ever after the bound was raised — while the
    stream demonstrably recorded again — and printed "STOPPED recording", a message
    that had become false. The writer re-decides its marker from the file's tail
    for exactly this reason; the reader now does too.
    """
    monkeypatch.setenv(usage_signal.USAGE_MAX_BYTES_ENV, "300")
    usage_signal._warned.clear()
    _serve(stream, "Point", ["p1", "p2"], at=_at(1))
    _serve(stream, "Point", ["p3", "p4"], at=_at(2))  # hits the bound
    assert usage_signal.refresh(stream).capped is True

    monkeypatch.setenv(usage_signal.USAGE_MAX_BYTES_ENV, str(1024 * 1024))
    assert _serve(stream, "Point", ["p5"], at=_at(3)) == 1

    got = usage_signal.refresh(stream)

    assert got.served_total > 0
    assert got.orderable is True, "recording resumed but the door still refused"
    assert got.capped is False


def test_the_capped_verdict_is_derived_from_the_read_descriptor(
    stream: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⛔ THE READER MUST NOT RE-OPEN THE PATH TO DECIDE `capped`.

    `_checkpoint_matches` insists the fold is described by the one descriptor the
    refresh read and nothing else. A by-path tail read can describe a file the fold
    never saw (a rotation between the read and the check) and would then certify a
    `capped` verdict about another file — the same class of defect the pre-body
    prefix capture removed for the fingerprint.
    """
    monkeypatch.setenv(usage_signal.USAGE_MAX_BYTES_ENV, "300")
    usage_signal._warned.clear()
    _serve(stream, "Point", ["p1", "p2"], at=_at(1))
    _serve(stream, "Point", ["p3", "p4"], at=_at(2))  # hits the bound
    assert usage_signal._tail_is_capped(stream) is True

    def _no_path_reads(_target: Path) -> bool:  # pragma: no cover — must not run
        raise AssertionError("refresh re-opened the stream path to decide `capped`")

    monkeypatch.setattr(usage_signal, "_tail_is_capped", _no_path_reads)

    got = usage_signal.refresh(stream)

    assert got.capped is True
    assert got.orderable is False


def test_a_record_line_over_the_bound_is_dropped(stream: Path) -> None:
    """`MAX_LINE_BYTES` keeps one record inside a page (the torn-write bound)."""
    huge = "h" * (usage_signal.MAX_LINE_BYTES + 100)

    assert usage_signal.record_serves("Source", [{"url": huge}], at=_at(1), path=stream) == 0
    assert not stream.exists() or stream.stat().st_size == 0
    assert usage_signal.refresh(stream).served_total == 0


# ══════════════════════════════════════════════════════════════════════════
# The ops door — every state, both output modes, one exit code
# ══════════════════════════════════════════════════════════════════════════


def _cli(*argv: str, capsys: pytest.CaptureFixture[str], json_mode: bool) -> int:
    args = ["--stream", *argv]
    if json_mode:
        args.append("--json")
    capsys.readouterr()
    return usage_signal._main(args)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("healthy", 0), ("no_serves", 2), ("pooled", 2), ("empty", 2), ("missing", 2)],
)
def test_the_ops_door_exits_the_same_in_both_modes(
    name: str, expected: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """⛔ The exit code must not depend on which output mode was asked for.

    A cron/check reads the exit code, and the cycle-2 fix that made a refusal exit
    2 in the human path was **unpinned**: nothing called `_main` at all, so
    reverting it left the suite green.
    """
    stream = tmp_path / "s.jsonl"
    if name == "healthy":
        _serve(stream, "Point", ["p1"], at=_at(1))
    elif name == "no_serves":
        # A stream that recorded an UNRESOLVABLE row and no serve: nothing was
        # observed, so the counter has no opinion (and is not stale).
        usage_signal.record_serves(
            "Source", [{"sourcePath": "/not-a-url"}], at=_at(1), path=stream
        )
    elif name == "pooled":
        _serve(stream, "Point", ["p1"], at=_at(1))
        usage_signal.record_serves(
            "Point", [{"id": "o"}], at=_at(2), path=stream, tenant_id="org-2"
        )
    elif name == "empty":
        stream.write_text("", encoding="utf-8")
    # "missing" leaves the file absent

    human = _cli(str(stream), capsys=capsys, json_mode=False)
    machine = _cli(str(stream), capsys=capsys, json_mode=True)

    assert human == machine == expected, f"{name}: human={human} json={machine}"


def test_the_ops_door_can_name_the_unpartitioned_namespace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--tenant ''` is the `None` partition; without it an operator had no way in."""
    stream = tmp_path / "s.jsonl"
    _serve(stream, "Point", ["mine"], at=_at(1))
    usage_signal.record_serves(
        "Point", [{"id": "theirs"}], at=_at(2), path=stream, tenant_id="org-2"
    )
    assert usage_signal.refresh(stream).pooled is True

    capsys.readouterr()
    rc = usage_signal._main(["--stream", str(stream), "--tenant", ""])

    out = capsys.readouterr().out
    assert rc == 0
    assert "mine" in out
    assert "theirs" not in out

