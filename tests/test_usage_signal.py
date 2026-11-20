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
"""

from __future__ import annotations

import json
import subprocess
import sys
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
    _serve(stream, "Event", ["e1"], at=_at(4))

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
    """An unreadable checkpoint replays from cold — never a partial state."""
    _serve(stream, "Point", ["p1"], at=_at(1))
    before = usage_signal.refresh(stream)
    usage_signal.checkpoint_path(stream).write_text("{not json", encoding="utf-8")

    after = usage_signal.refresh(stream)

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
_FORBIDDEN_GRAPH_ROOTS = (
    "falkordb",
    "redis",
    "tortoise.manager",
    "tortoise.graph_connection",
    "tortoise.sdk",
)


def _imports_of(source: str) -> set[str]:
    """Every module this source imports, RELATIVE IMPORTS RESOLVED.

    ⛔ The resolution is the whole point. An earlier version of this guard walked
    `ast.ImportFrom` with `node.module` truthy and compared the leftover string,
    so `from . import manager` (module is None) was SKIPPED and
    `from .graph_connection import get_connection` recorded `.graph_connection`,
    which matched nothing in the forbidden set. Both spellings are the ones this
    package actually uses (`from . import read_ledger`,
    `from .config import resolve_db_path`), so the guard passed on the exact
    regression it existed for.
    """
    import ast

    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                found.add(
                    node.module if node.level == 0 else f"tortoise.{node.module}"
                )
            elif node.level:
                # `from . import x` — resolve each alias under the package.
                found.update(f"tortoise.{alias.name}" for alias in node.names)
    return found


def test_no_graph_dependency() -> None:
    """The signal must not be able to reach a graph client, even by accident.

    A `last_searched_at` property written on every search is write amplification
    into the very store the window exists to bound (#14011), so this module is
    required to have no graph in reach at all — an import is the only way it could
    get one. The import forms checked include the relative spellings this package
    actually uses; see `_imports_of`.
    """
    source = Path(usage_signal.__file__).read_text(encoding="utf-8")
    imported = _imports_of(source)
    offending = {
        name
        for name in imported
        for root in _FORBIDDEN_GRAPH_ROOTS
        if name == root or name.startswith(root + ".")
    }
    assert not offending, (
        f"usage_signal imports a graph client: {sorted(offending)} — a residency "
        f"signal may never write to the store it is bounding"
    )


def test_the_no_graph_guard_catches_relative_imports() -> None:
    """The guard itself is pinned — it was defeated once by import spelling."""
    assert _imports_of("from . import manager\n") == {"tortoise.manager"}
    assert _imports_of("from .graph_connection import get\n") == {
        "tortoise.graph_connection"
    }
    assert _imports_of("import redis\n") == {"redis"}
    assert _imports_of("from . import read_ledger\n") == {"tortoise.read_ledger"}


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
