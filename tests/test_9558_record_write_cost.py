"""#9558 (R10) — the record write path stops paying per record.

WHAT THIS PINS, and what each pin would RED on.

The issue names three indicators. Two are mechanisms and one is a measurement;
the measurement lives in `docs/evidence/9558-record-write-cost/` (one probe run
against two trees) because a wall-clock number asserted in a test rots into a
pass. These tests pin the MECHANISMS that number came from, plus the properties
the issue's own verification checklist makes non-negotiable:

* **S6 — a cached column map cannot diverge from the live schema silently.** The
  cache is keyed on each migration file's `(name, mtime_ns, size, inode, ctime_ns)`,
  so an edit,
  an addition and a removal are all cache MISSES. Reverting `table_columns` to a
  single unconditional module-level memo reds
  `test_an_edited_migration_is_seen_immediately` and
  `test_a_removed_migration_is_seen_and_restores_the_error`.
* **S2 — pooling does not change the write order and introduces no bypass.** The
  live door still runs `record_projection.replay` — the same prologue, the same
  plan, in the same order — on a connection it merely REUSES. The pin drives two
  writes through the live door on ONE connection and asserts the second write's
  statement sequence equals a fresh write's.
* **S1 — replay equality and idempotency still hold on the pooled path.** The
  reuse pin replays the SAME journal position twice on the same pooled handle and
  asserts the record absorbs it into one `journal_event` row, exactly as the
  unpooled path does.

Plus the two things a pool can silently get wrong, which no other test covers: a
handle whose server went away is DISCARDED rather than handed to the next
record, and an unfinished or aborted transaction is reset before the handle is
handed to the next borrower (`_reset_parked_transaction`) — never on the release
path, because a release rollback is a server round trip taken after the rows are
fetched and therefore before the answer is returned (#9558 review cycle 3, P1).

NO DATABASE. Every connection here is a fake DB-API object (the same shape
`tests/test_record_writer_seam.py` uses, imported rather than re-invented), and
the transaction-status constants are patched so this file runs on a lane with no
`[postgres]` extra (⚠️ THIRTEEN of its twenty-eight tests then SKIP — the pool pins need the
driver — which is why it is also armed into `test-record-postgres`) — `test_record_writer_seam.py`'s own
`test_importing_the_seam_pulls_in_no_database_driver` is what keeps the seam
importable there, and this file must not undo it by importing the driver itself.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from tests.test_record_writer_seam import (
    GRAPH,
    ORG,
    FakeConnection,
    FakeCursor,
)
from tortoise import record_projection as rp
from tortoise import record_writer as rw

REPO_MIGRATIONS = (Path(__file__).resolve().parent.parent
                   / "supabase" / "migrations")

#: The transaction statuses the seam compares against, as plain ints
#: (`psycopg2.extensions.TRANSACTION_STATUS_IDLE` / `_UNKNOWN`). Patched over
#: `record_writer._transaction_statuses` so neither the seam's release path nor
#: this file needs the driver installed.
IDLE, UNKNOWN = 0, 4

#: One armed DSN for the pool pins, and its bucket key.
_DSN = "postgresql://record@127.0.0.1/db"
_DSN_KEY = rw._pool_key(_DSN)


# ════════════════════════════════════════════════════════════════════════════
# A migration tree on disk, and a fake connection with a transaction status
# ════════════════════════════════════════════════════════════════════════════


def _tree(base: Path) -> Path:
    """A copy of the REAL record migrations, so a parse of it is a real parse."""
    base.mkdir(parents=True, exist_ok=True)
    target = base / "migrations"
    target.mkdir(exist_ok=True)
    for name in rp.RECORD_MIGRATIONS:
        shutil.copy(REPO_MIGRATIONS / name, target / name)
    return target


class _Info:
    """The one attribute of `conn.info` this seam reads."""

    def __init__(self, status: int) -> None:
        self.transaction_status = status


class FakeStatusConnection(FakeConnection):
    """`FakeConnection` plus the transaction status a pool must be able to read."""

    def __init__(self, status: int = IDLE) -> None:
        super().__init__()
        self.info = _Info(status)


class ScriptedCursor(FakeCursor):
    """A cursor that honours the DRIVER's post-`close()` contract.

    psycopg2: *"The cursor will be unusable from this point forward; an
    InterfaceError will be raised if any operation is attempted with the
    cursor"* (<https://www.psycopg.org/docs/cursor.html>). Before #9558 it was
    `connection.close()` that did this to every cursor the connection had
    issued; parking a handle no longer closes it, so the wrapper has to close
    them itself or a pre-release cursor stays live on a reused connection.

    `close()` is idempotent, exactly as the driver's is, so the ordinary
    `with conn.cursor() as cur:` shape costs nothing extra on release.
    """

    def __init__(self, conn: FakeConnection) -> None:
        super().__init__(conn)
        self.closed = False
        self.close_calls = 0

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1

    def execute(self, sql: str, params: Any = None) -> None:
        if self.closed:
            try:
                from psycopg2 import InterfaceError  # type: ignore[import-not-found]
            except Exception:  # pragma: no cover - driver absent
                raise RuntimeError("cursor already closed") from None
            raise InterfaceError("cursor already closed")
        super().execute(sql, params)


class ScriptedConnection(FakeStatusConnection):
    """A fake whose cursor also accepts DB-API arguments and can be closed.

    The shared `FakeConnection.cursor()` takes no parameters and its cursor has no
    `close()`, which is fine for `replay` and wrong for the two surfaces the
    POOLED handle is handed to: `open_record` closes its cursor, and
    `cache_projection._rows` asks for a `RealDictCursor`. Narrowing the wrapper to
    the fake's shape would let a real `TypeError` ship, so the pins drive this.
    """

    def __init__(self, status: int = IDLE) -> None:
        super().__init__(status)
        self.cursor_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.cursors: list[ScriptedCursor] = []

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        self.cursor_calls.append((args, kwargs))
        cur = ScriptedCursor(self)
        self.cursors.append(cur)
        return cur

    def set_session(self, **kwargs: Any) -> None:
        """A psycopg2 SESSION MUTATOR — a METHOD, so it reaches the raw handle
        through the wrapper's `__getattr__`, not through `__setattr__`."""
        for name, value in kwargs.items():
            setattr(self, name, value)


@pytest.fixture(autouse=True)
def _isolated_caches(monkeypatch):
    """No test inherits another's parsed schema, and no test leaks a handle.

    ⚠️ AUTOUSED AND MANDATORY. `_SCHEMA_CACHE` and the live pool are
    process-global; without this a temp tree from one test could be served to the
    next, and an idle handle would outlive the test that opened it.
    """
    monkeypatch.setattr(rw, "_transaction_statuses", lambda: (IDLE, UNKNOWN))
    rp._release_schema_cache()
    rw._release_pooled_connections()
    yield
    rp._release_schema_cache()
    rw._release_pooled_connections()


# ════════════════════════════════════════════════════════════════════════════
# S6 — the column map is cached AND cannot go stale in silence
# ════════════════════════════════════════════════════════════════════════════


def test_the_column_map_is_parsed_once_for_an_unchanged_schema(
        monkeypatch, tmp_path):
    """Indicator (b): the parse happens once, not once per call.

    Counts the PARSE, not the call: #9558's target is "called once per
    process/refresh", and the property that makes that true is that the work
    behind the call is not repeated. A call that returns a cached parse costs one
    stat pass, which is what the measurement prices.

    FALSIFIER: delete the `entry["parsed"]` short-circuit in `table_columns` and
    this reds with a parse count of 3 instead of 1.
    """
    tree = _tree(tmp_path)
    parses = {"n": 0}
    real = rp._parse_record_tables

    def counting(sql: str) -> Any:
        parses["n"] += 1
        return real(sql)

    monkeypatch.setattr(rp, "_parse_record_tables", counting)
    first = rp.table_columns(tree)
    second = rp.table_columns(tree)
    third = rp.table_columns(tree)
    assert parses["n"] == 1, (
        f"the migration parse ran {parses['n']} time(s) for three reads of an "
        f"UNCHANGED tree — the cache is not taking effect (#9558)")
    assert first == second == third


class _CountingPattern:
    """A compiled regex whose `findall` passes are counted.

    The second reader parses INLINE over module-level patterns, so the pattern is
    the only seam its parse crosses — there is no `_parse_…` function to wrap the
    way `table_columns` has `_parse_record_tables`.
    """

    def __init__(self, pattern: re.Pattern[str], counter: dict[str, int]) -> None:
        self._pattern = pattern
        self._counter = counter

    def findall(self, *args: Any, **kwargs: Any) -> Any:
        self._counter["n"] += 1
        return self._pattern.findall(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pattern, name)


def test_the_second_reader_parses_once_for_an_unchanged_schema(
        monkeypatch, tmp_path):
    """The other half of the per-record migration cost is cached too.

    `replay_plan` calls `table_columns()` AND `refused_props_names()` for every
    record; a cache on one and not the other would leave half the per-record
    migration work in place.

    ⚠️ This pin counts the PARSE, and the distinction is the whole point: BOTH
    readers take the SQL from `_entry_sql`, which memoises it on the schema entry,
    so counting `_read_migrations` stays at 1 even with this reader's short-circuit
    DELETED — that is a pin which can never fail on the mutation it names (review
    at `eee6bc92bc5`). What each reader repeats for ITSELF is the parse over the
    shared SQL, so the parse is what is counted here; the read count is asserted
    too, because it is a real invariant, just not a discriminating one.

    FALSIFIER: drop the `entry["parsed"]` short-circuit in `refused_props_names`
    and `_TABLE_RE` runs 2 times for two calls on an unchanged tree.
    """
    tree = _tree(tmp_path)
    parses = {"n": 0}
    reads = {"n": 0}
    real_read = rp._read_migrations

    def counting_read(base: Path | None = None) -> str:
        reads["n"] += 1
        return real_read(base)

    monkeypatch.setattr(rp, "_TABLE_RE", _CountingPattern(rp._TABLE_RE, parses))
    monkeypatch.setattr(rp, "_read_migrations", counting_read)
    first = rp.refused_props_names(tree)
    second = rp.refused_props_names(tree)
    assert parses["n"] == 1, (
        f"the second reader's parse ran {parses['n']} time(s) for two calls on "
        f"an UNCHANGED tree — its cache is not taking effect (#9558)")
    assert reads["n"] == 1, (
        f"the migrations were read {reads['n']} time(s) for two calls on the "
        f"same unchanged tree (#9558)")
    assert first == second


def test_an_edited_migration_is_seen_immediately(tmp_path):
    """S6: a schema change cannot be served from the cache, even once.

    The invalidation surface is the CONTENT of a registered migration file (the
    reader parses `RECORD_MIGRATIONS`, so a file outside that tuple is invisible
    with or without a cache — stated at `_migrations_fingerprint`). THREE edits,
    because each one separates a different part of the key:

    1. an append (a FORWARD `ALTER … ADD COLUMN`, #2782's shape) — separates "no
       cache at all" from "a cache";
    2. a SECOND append to the same file — the in-place rewrite a cache keyed on
       the directory's mtime alone would miss;
    3. a SAME-SIZE rewrite (one added column renamed for another of equal
       length) — the only step that separates `mtime_ns` from `size` in the
       fingerprint. Without it a `(name, size, inode)` key passes this file, so
       the shipped key would not be pinned (#9558 review P2, measured: dropping
       `mtime_ns` left the whole file green until this step existed).

    The mtime is forced forward rather than left to the filesystem: two writes
    inside one timestamp tick would make a `size`-only key look correct by
    accident, and a coarse-timestamp filesystem would flake. `os.utime` makes the
    step deterministic and tests the property instead of the clock.

    FALSIFIER: replace the fingerprint with a module-level memo on the directory
    string — step 1 reds. Or key on `(name, size, inode)` — step 3 reds.
    """
    tree = _tree(tmp_path)
    target = tree / rp.RECORD_MIGRATIONS[-1]
    original = target.read_text(encoding="utf-8")
    assert "pool_probe_column" not in rp.table_columns(tree)["point"]
    target.write_text(original + "ALTER TABLE public.point "
                                "ADD COLUMN pool_probe_column text;\n",
                      encoding="utf-8")
    assert "pool_probe_column" in rp.table_columns(tree)["point"], (
        "an edit to a registered migration was NOT seen by the next schema read "
        "— the cache is serving a map built from files that are no longer the "
        "schema (#9558, surface S6)")
    # …the SAME file edited again (unchanged name, unchanged directory).
    assert "edited_probe_column" not in rp.table_columns(tree)["point"]
    second = (original + "ALTER TABLE public.point "
                         "ADD COLUMN pool_probe_column text;\n"
                         "ALTER TABLE public.point "
                         "ADD COLUMN edited_probe_column text;\n")
    target.write_text(second, encoding="utf-8")
    assert "edited_probe_column" in rp.table_columns(tree)["point"], (
        "a SECOND in-place edit of the same migration was not seen — a cache "
        "keyed on the directory's mtime would pass the first edit and fail "
        "exactly here")
    # …and a SAME-SIZE rewrite: `eeee_probe` -> `ffff_probe` changes the parsed
    # column and NOT the file's length, so only `mtime_ns` (or a content digest)
    # can see it.
    assert "eeee_probe_column" not in rp.table_columns(tree)["point"]
    third = second + "ALTER TABLE public.point ADD COLUMN eeee_probe_column text;\n"
    target.write_text(third, encoding="utf-8")
    assert "eeee_probe_column" in rp.table_columns(tree)["point"]
    rewritten = third.replace("eeee_probe_column", "ffff_probe_column")
    assert len(rewritten) == len(third), "the rewrite must not change the size"
    target.write_text(rewritten, encoding="utf-8")
    # Force the mtime forward by a second: a same-size rewrite within one
    # timestamp tick would let a size-only key pass this step by accident.
    stat = target.stat()
    forward = stat.st_mtime_ns + 1_000_000_000
    os.utime(target, ns=(forward, forward))
    columns = rp.table_columns(tree)["point"]
    assert "ffff_probe_column" in columns, (
        "a SAME-SIZE in-place edit was not seen — the fingerprint cannot be "
        "distinguishing `mtime_ns`, so a `(name, size, inode)` key would pass "
        "this file while serving a stale column map (#9558, surface S6)")
    assert "eeee_probe_column" not in columns
    # …and a same-size rewrite that ALSO restores the mtime: what `cp -p`,
    # `rsync -a`/`-t`, `install -p`, `tar -xp` and Docker's COPY do as a matter
    # of course. mtime and size are both unchanged, so only `ctime_ns` (which
    # none of those tools can restore) can see it — without it the cache serves
    # the pre-edit map indefinitely (#9558 review cycle 1, P2, reproduced: five
    # migrations rewritten content->contenu, stat unchanged, stale map served).
    before = target.stat()
    # ⛔ BURN ONE INODE-CLOCK TICK FIRST, so the ctime arm ALWAYS runs.
    # Inode timestamps are coarse on ordinary Linux filesystems (measured: ext4,
    # delta 0 ns for a write + `utime` microseconds apart), so a step that simply
    # hopes a tick boundary falls between two calls runs only ~60% of the time —
    # and this file is armed into `test-record-postgres`, whose leg gate FAILS on
    # any skip (#9551). A nondeterministic pin there is a false RED that blames
    # the database (#9558 review cycle 2, P1). So: nudge the target's ctime until
    # it moves past the pre-edit value, bounded, then do the write. `os.utime`
    # with the ORIGINAL mtime keeps the property this step is about.
    deadline = time.monotonic() + 3.0
    while (target.stat().st_ctime_ns == before.st_ctime_ns
           and time.monotonic() < deadline):
        os.utime(target, ns=(before.st_mtime_ns, before.st_mtime_ns))
        time.sleep(0.005)
    ticked = target.stat()
    assert ticked.st_ctime_ns != before.st_ctime_ns, (
        "the inode clock did not advance within 3 s — this box cannot exercise "
        "the ctime arm at all, which is an environment failure rather than a "
        "property this test can assert")
    target.write_text(rewritten.replace("ffff_probe_column",
                                        "gggg_probe_column"), encoding="utf-8")
    os.utime(target, ns=(before.st_mtime_ns, before.st_mtime_ns))
    after = target.stat()
    assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns,
                                                  before.st_size), (
        "the step must preserve size AND mtime, or it does not test ctime")
    assert after.st_ctime_ns != before.st_ctime_ns, (
        "the ctime moved back to the pre-edit value — the key would see nothing")
    columns = rp.table_columns(tree)["point"]
    assert "gggg_probe_column" in columns, (
        "a same-size rewrite with the mtime RESTORED was not seen — the "
        "fingerprint is keyed on stat fields an archive extraction can "
        "preserve, so a `cp -p` burn would serve a stale column map forever")
    assert "ffff_probe_column" not in columns


def test_the_refused_props_map_also_reacts_to_an_edit(tmp_path):
    """The second cached reader is invalidated by the same fingerprint.

    A cache that invalidated only ONE of the two readers would be the worst
    shape: the column map fresh, the refusal map stale, and a property written
    that the schema has since refused.
    """
    tree = _tree(tmp_path)
    assert "pool_probe_refused" not in rp.refused_props_names(tree)["point"]
    target = tree / rp.RECORD_MIGRATIONS[-1]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "ALTER TABLE public.point ADD CONSTRAINT chk_point_props_no_derived "
          "CHECK (\n    NOT (props ?| ARRAY['pool_probe_refused']::text[])\n);\n",
        encoding="utf-8",
    )
    assert "pool_probe_refused" in rp.refused_props_names(tree)["point"], (
        "a forward refusal migration was not seen by the refusal map (#8263's "
        "rule, applied to an invalidated cache)")


def test_a_removed_migration_is_seen_and_restores_the_error(tmp_path):
    """S6's other direction: an ABSENT file is part of the fingerprint.

    If removal were not part of the key, a process could keep answering a
    complete map after the schema it parsed was taken away — the "the record is
    fine" reading this module's own error message exists to refuse.
    """
    tree = _tree(tmp_path)
    rp.table_columns(tree)                # warm the cache on a complete tree
    (tree / rp.RECORD_MIGRATIONS[0]).unlink()
    with pytest.raises(rp.RecordProjectionError, match="not on disk"):
        rp.table_columns(tree)


def test_two_different_trees_never_share_a_parse(tmp_path):
    """The cache key includes the BASE, not only the files' fingerprint."""
    first = _tree(tmp_path / "a")
    second = _tree(tmp_path / "b")
    target = second / rp.RECORD_MIGRATIONS[-1]
    target.write_text(
        target.read_text(encoding="utf-8")
        + "ALTER TABLE public.point ADD COLUMN only_in_b text;\n",
        encoding="utf-8")
    assert "only_in_b" not in rp.table_columns(first)["point"]
    assert "only_in_b" in rp.table_columns(second)["point"]


def test_the_returned_map_is_the_callers_own(tmp_path):
    """The cached dict is never handed out: a caller cannot poison the process.

    Every call before #9558 returned a fresh dict, and a cache that changed that
    would make one caller's edit the next caller's column map — silently.
    """
    tree = _tree(tmp_path)
    mine = rp.table_columns(tree)
    mine["point"] = frozenset()
    mine["invented"] = frozenset({"x"})
    again = rp.table_columns(tree)
    assert again["point"], "a caller's edit emptied the cached column map"
    assert "invented" not in again


# ════════════════════════════════════════════════════════════════════════════
# (a) — the record path reuses one connection across records
# ════════════════════════════════════════════════════════════════════════════


def _point(index: int) -> dict[str, Any]:
    return {"type": "PointAdded", "ts": "2026-11-07T00:00:00Z",
            "point": {"id": f"pt9558-{index}", "pointKind": "event",
                      "content": f"measured write {index}", "status": "live"}}


def _fake_driver(monkeypatch) -> dict[str, list[Any]]:
    """Replace `psycopg2.connect` with a factory, and count every CALL to it.

    The real `_default_connect` is exercised, not a patched hook: the whole point
    of #9558's placement is that production goes through this function, so a test
    that patched it would assert its own stub.

    Returns `{"handles": [...], "dsns": [...]}` — one entry per connect.
    """
    psycopg2 = pytest.importorskip("psycopg2", reason="the pool needs its driver")
    made = {"handles": [], "dsns": [], "kwargs": []}

    def connect(dsn: str, **kwargs: Any) -> Any:
        made["dsns"].append(dsn)
        made["kwargs"].append(kwargs)
        handle = FakeStatusConnection()
        made["handles"].append(handle)
        return handle

    monkeypatch.setattr(psycopg2, "connect", connect)
    return made


def _arm(monkeypatch, dsn: str = "postgresql://record@127.0.0.1/db") -> None:
    monkeypatch.setenv(rw.RECORD_DSN_ENV, dsn)


def test_the_record_path_opens_one_connection_for_many_records(monkeypatch):
    """Indicator (a), the headline: `connect()` no longer appears per record.

    Measured through the production door (`write_live_record`), because that is
    the caller the measurement covers and the one that pays the per-record cost.

    FALSIFIER: revert `_default_connect` to always call `psycopg2.connect` (drop
    `_take_idle_connection`) and this reds with `len(dsns) == 3`.
    """
    made = _fake_driver(monkeypatch)
    _arm(monkeypatch)
    tenant = (ORG, GRAPH)
    for index in range(3):
        write = rw.write_live_record(_point(index), seq=index + 1, tenant=tenant)
        assert write is not None and write.applied, write
    assert len(made["dsns"]) == 1, (
        f"the record path opened {len(made['dsns'])} connections for 3 records "
        f"— the per-record connect is still there (#9558)")
    conn = made["handles"][0]
    assert conn.commits == 3, (
        f"the door committed {conn.commits} time(s) — reuse must not collapse "
        "the transaction boundary: one record, one commit (§3)")
    assert conn.closed == 0, (
        "the pooled handle was closed between records — it is RELEASED, not "
        "disposed of, while the record stays reachable")


def test_the_second_write_on_a_reused_handle_is_the_same_plan(monkeypatch):
    """S2/S1: reuse changes the connection, never the write.

    The second write must execute exactly what a fresh one did (prologue, plan,
    same order) and must be APPLIED — reuse that "worked" by skipping the
    prologue would be a tenancy bypass, and one that skipped the plan a dropped
    row. The same journal position replayed twice is absorbed into one
    `journal_event` row, which is the idempotency half.

    FALSIFIER: hold a connection-level flag that skips the prologue on reuse —
    the second sequence loses its first statement and this reds.
    """
    made = _fake_driver(monkeypatch)
    _arm(monkeypatch)
    tenant = (ORG, GRAPH)
    first = rw.write_live_record(_point(0), seq=1, tenant=tenant)
    assert first is not None and first.applied
    conn = made["handles"][0]
    after_first = len(conn.executed)
    second = rw.write_live_record(_point(0), seq=1, tenant=tenant)
    assert second is not None and second.applied
    second_run = conn.executed[after_first:]
    expected = rp.replay_plan([_point(0)], org_id=ORG, graph_id=GRAPH,
                              start_seq=1, position=True)
    assert [sql for sql, _params in second_run] == [
        rp.TENANT_GUC_SQL, rp.TENANT_SCOPE_SQL, *[s.sql for s in expected]
    ], (
        "the reused handle did not execute the same prologue + plan as a fresh "
        f"one — the write order changed (#9558, surface S2): {second_run}")
    assert conn.journal_positions == {(ORG, GRAPH, 1)}, (
        "replaying the same position on the pooled path did not stay one row — "
        f"idempotency broke (surface S1): {conn.journal_positions}")


def test_a_borrowed_handle_reports_the_same_outcome_to_its_caller(monkeypatch):
    """The seam's own contract is untouched by the pool (the reason it lives in
    the hook).

    `RecordWriter.write` is given a handle it must not commit; the pool must not
    change that — on a FRESH handle and on a REUSED one alike, which is why the
    second write is here: a hook that commits only what it reuses would slip past
    a single-write version of this test.

    FALSIFIER: commit inside `_default_connect` on the reuse branch and this reds
    on `handle.commits == 0`.
    """
    made = _fake_driver(monkeypatch)
    _arm(monkeypatch)
    for seq in (1, 2):
        write = rw.write_records([_point(seq)], org_id=ORG, graph_id=GRAPH,
                                 first_seq=seq)
        assert write.applied, write
        assert write.connection is not None, (
            "the seam did not report the handle it opened — a caller could no "
            "longer commit its own transaction")
        write.connection.close()      # the caller disposes of it: a RELEASE
    handle = made["handles"][0]
    assert len(made["handles"]) == 1, (
        f"the second write opened its own connection ({len(made['handles'])}) — "
        "the reuse this test is about never happened")
    assert handle.commits == 0, (
        f"the SEAM committed {handle.commits} transaction(s) it does not own "
        "(§3) — the pool must not change who owns the boundary")
    assert handle.closed == 0, "the caller's close closed the pooled handle"
    assert rw._take_idle_connection("postgresql://record@127.0.0.1/db") is handle, (
        "the handle the seam opened was not returned to the pool when its caller "
        "closed it — the pool would slowly lose every connection it opened")


def test_a_broken_handle_is_discarded_not_reused(monkeypatch):
    """A handle whose server went away must not be handed to the next record.

    The residual the hooks document is that ONE record per dead handle may be
    refused; what must never happen is that EVERY following record inherits the
    same dead handle.

    FALSIFIER: drop the `TRANSACTION_STATUS_UNKNOWN` test in
    `_connection_is_reusable` — the dead handle is parked and re-handed, and this
    reds on the second acquire.
    """
    dead = FakeStatusConnection(status=UNKNOWN)
    live = FakeStatusConnection()
    handles = [dead, live]
    calls = {"n": 0}

    def connect(_dsn: str, **_kwargs: Any) -> Any:
        calls["n"] += 1
        return handles.pop(0)

    psycopg2 = pytest.importorskip("psycopg2", reason="the pool needs its driver")
    monkeypatch.setattr(psycopg2, "connect", connect)
    first = rw._default_connect("postgresql://record/db")
    assert first.cursor is not None
    first.close()                     # the server went away: must be discarded
    assert dead.closed == 1, (
        "a handle whose connection is UNKNOWN was parked in the pool instead of "
        "being closed — the next record would inherit a dead connection")
    second = rw._default_connect("postgresql://record/db")
    assert isinstance(second, rw._PooledConnection) and calls["n"] == 2, (
        "the pool re-handed the dead handle instead of opening a fresh one")


def test_a_parked_transaction_is_reset_at_reuse_not_at_release():
    """A failed write must not fail the NEXT record for the previous one's reason.

    `replay` leaves the transaction aborted when a statement fails; without a
    reset, psycopg2 refuses every subsequent statement on that handle ("current
    transaction is aborted") and the pool would turn one bad record into a stream
    of `write_failed`s.

    ⛔ THE RESET MUST HAPPEN AT REUSE (#9558 review cycle 3, P1). At RELEASE it is
    a server round trip on every read borrow — `open_record` always parks INTRANS
    — taken AFTER the rows are fetched and therefore BEFORE the answer is
    returned, so a backend that wedges in that window discards a read that
    already succeeded. libpq has no per-statement timeout to bound it with.

    FALSIFIER, both halves: move the `rollback()` back into `_park_connection`
    and the first assertion reds (`rollbacks` becomes 1 at park); delete it
    from `_reset_parked_transaction` and the second reds.
    """
    conn = FakeStatusConnection(status=2)          # INTRANS / INERROR, not IDLE
    rw._park_connection(rw._pool_key("postgresql://record/db"), conn)
    assert conn.rollbacks == 0, (
        "a RELEASE took a server round trip — that rollback runs after the "
        "caller's rows are fetched, so a wedged backend discards a completed "
        "read; it belongs on the borrow path")
    assert conn.closed == 0
    assert rw._take_idle_connection("postgresql://record/db") is conn, (
        "the handle was not returned to the pool")
    assert conn.rollbacks == 1, (
        "a parked unfinished transaction was handed to the next borrower "
        "without being reset — it would fail for the previous record's reason")


def test_a_handle_that_dies_while_idle_is_discarded_at_borrow():
    """A dead idle handle must not surface to the READ door as an outage.

    Both fields `_connection_is_reusable` reads are LOCAL and therefore stale:
    a handle parked while its server was alive reports `closed == 0` and
    `transaction_status == IDLE` after the peer goes away, until something
    touches the socket. The read door parks INTRANS, so the borrow-time reset is
    that first touch — it raises, the handle is discarded, and the caller gets a
    FRESH connection instead of a `RecordUnavailable` a reconnect would have
    served (#9558 review cycle 3, P1).

    FALSIFIER: hand the candidate back without running the reset (return it as
    soon as `_connection_is_reusable` says yes) and the dead handle reaches the
    door; drop the discard-on-failure and the second borrow returns the corpse.
    """
    dead = FakeStatusConnection(status=2)          # INTRANS: the read door's shape
    dead.rollback = lambda: (_ for _ in ()).throw(          # type: ignore[assignment]
        RuntimeError("server closed the connection unexpectedly"))
    rw._park_connection(_DSN_KEY, dead)
    assert rw._take_idle_connection(_DSN) is None, (
        "a handle whose server died while idle was handed out — the door would "
        "raise RecordUnavailable for a request a fresh connect serves")
    assert dead.closed == 1, (
        "the dead handle was neither handed out nor CLOSED — its socket and "
        "backend slot leak")


def test_a_dead_candidate_is_skipped_and_a_live_one_in_the_bucket_is_used():
    """The borrow must RETRY past a dead candidate, not give up on the bucket.

    `_take_idle_connection` pops candidates in a `while True` loop because the
    first one it pops may be a corpse. Without the loop a single dead handle in
    front of a live one turns every borrow into a fresh connect — the pool would
    silently stop pooling after one server restart, which is exactly when it is
    worth having.

    ⛔ THIS IS THE PIN THE #9558 CYCLE-4 REVIEW FOUND MISSING: collapsing the
    loop to a single `pop()` left the whole suite green (measured), so nothing
    would have caught that regression.

    FALSIFIER: make the loop body take ONE candidate and return it without
    retrying, and this reds.
    """
    dead = FakeStatusConnection(status=2)      # INTRANS: the read door's shape
    dead.rollback = lambda: (_ for _ in ()).throw(      # type: ignore[assignment]
        RuntimeError("server closed the connection unexpectedly"))
    live = FakeStatusConnection(status=2)      # also INTRANS, but alive
    # Park the LIVE one first: `_take_idle_connection` pops LIFO, so the corpse
    # must be the LAST parked to be the FIRST candidate the borrow pops.
    rw._park_connection(_DSN_KEY, live)
    rw._park_connection(_DSN_KEY, dead)
    assert rw._take_idle_connection(_DSN) is live, (
        "the borrow gave up on the bucket instead of retrying past the dead "
        "candidate — one corpse in front of a live handle permanently disables "
        "pooling for that DSN")
    assert live.rollbacks == 1, (
        "the live candidate was handed out without its parked transaction "
        "being reset")
    assert dead.closed == 1, "the dead candidate was not discarded"


def test_a_discarded_candidate_is_replaced_by_a_fresh_connect(monkeypatch):
    """A dead idle handle must cost a RECONNECT, never a surfaced error.

    The read door parks INTRANS, so `_reset_parked_transaction` is that handle's
    first wire touch; when it raises the caller must still get a working
    connection — `_default_connect` falls through to `psycopg2.connect`. This
    pins the half the cycle-4 review found untested: the previous pin stopped at
    `_take_idle_connection(...) is None` and never asserted a connect followed.

    FALSIFIER: make `_default_connect` return None when `_take_idle_connection`
    yields nothing after a discard, and this reds.
    """
    made = _fake_driver(monkeypatch)
    dead = FakeStatusConnection(status=2)
    dead.rollback = lambda: (_ for _ in ()).throw(      # type: ignore[assignment]
        RuntimeError("server closed the connection unexpectedly"))
    rw._park_connection(_DSN_KEY, dead)
    fresh = rw._default_connect(_DSN)
    assert len(made["dsns"]) == 1, (
        f"{len(made['dsns'])} connects — a discarded candidate must be "
        "replaced by exactly one fresh connect")
    assert fresh is not dead, "the discarded handle was handed back to the caller"
    assert dead.closed == 1, "the discarded handle's socket leaked"


def test_every_connection_carries_the_parked_handle_bound(monkeypatch):
    """A parked INTRANS handle must not hold its locks indefinitely (P1).

    The read door parks a handle INTRANS on purpose, and an INTRANS handle holds
    an `AccessShareLock` on the tables it read until its transaction ends —
    measured on PostgreSQL 16, such a session blocks `ALTER TABLE`, `DROP`,
    `VACUUM FULL` and `CLUSTER` on those tables. Record migrations ARE actively
    edited, so an otherwise-quiet worker could hang the next migration.

    Nothing else bounds it: `idle_in_transaction_session_timeout` is set nowhere
    in the tree and the server default is 0 (disabled), and `open_record`'s
    `statement_timeout` is transaction-local and bounds STATEMENTS, not idling.
    The bound therefore ships in the STARTUP `options` packet, which costs no
    round trip — the fix must not undo the change it escorts.

    FALSIFIER: drop the `options` kwarg from `_default_connect` and this reds.
    """
    made = _fake_driver(monkeypatch)
    rw._default_connect(_DSN)
    options = made["kwargs"][0].get("options", "")
    assert "idle_in_transaction_session_timeout" in options, (
        f"the connection carries no idle-in-transaction bound (options={options!r}) "
        "— a parked INTRANS handle would hold an AccessShareLock until the same "
        "DSN is borrowed again, which may be never")
    assert f"={rw.IDLE_IN_TRANSACTION_TIMEOUT_SECONDS * 1000}" in options, (
        "the bound on the wire does not come from "
        "IDLE_IN_TRANSACTION_TIMEOUT_SECONDS")


def test_an_operator_idle_timeout_is_not_replaced(monkeypatch):
    """A DSN that names its own bound WINS — same rule as `connect_timeout`.

    Silently replacing an operator's explicit number is the defect the
    `connect_timeout` branch already guards against (#9231 review P2); the new
    bound must not reintroduce it.

    FALSIFIER: drop the `already_bounded` guard and this reds.
    """
    made = _fake_driver(monkeypatch)
    rw._default_connect(
        "postgresql://record@127.0.0.1/db"
        "?idle_in_transaction_session_timeout=900000")
    assert "options" not in made["kwargs"][0], (
        "the pool overrode an idle-in-transaction bound the operator named "
        f"(kwargs={made['kwargs'][0]!r})")


def test_an_operator_idle_timeout_in_their_options_is_not_replaced(monkeypatch):
    """The same rule when the operator expressed it as a STARTUP `options` `-c`.

    `-c idle_in_transaction_session_timeout=…` inside `options` is the only form
    libpq actually accepts for this setting, so this is the operator-noted-it
    case that can really happen. Their value must reach the server untouched.

    FALSIFIER: drop the `operator_options` half of `already_bounded` and this
    reds — the pool would append a second `-c` for the same setting.
    """
    made = _fake_driver(monkeypatch)
    rw._default_connect(
        "postgresql://record@127.0.0.1/db"
        "?options=-c%20idle_in_transaction_session_timeout%3D900000")
    assert "options" not in made["kwargs"][0], (
        "the pool replaced a bound the operator put in their STARTUP options "
        f"(kwargs={made['kwargs'][0]!r})")


def test_a_password_that_mentions_the_bound_does_not_disable_it(monkeypatch):
    """The bound is decided by a QUERY KEY, never by a substring of the DSN.

    `#9231`'s `connect_timeout` fix closed exactly this trap two lines above the
    new bound (a password needs no percent-encoding and sits in the netloc, so
    `"…" not in dsn` matched it) — and the parked-handle bound reintroduced it:
    a password ending in `idle_in_transaction_session_timeout` silently removed
    the bound, restoring the unbounded `AccessShareLock` the bound exists to
    cap. ⛔ Removing a safety bound because of a CREDENTIAL is the failure mode,
    not a false positive to live with.

    FALSIFIER: restore `"idle_in_transaction_session_timeout" not in dsn` (the
    whole-DSN substring test) and this reds with no `options` at all.
    """
    made = _fake_driver(monkeypatch)
    rw._default_connect(
        "postgresql://svc:idle_in_transaction_session_timeout_2024@127.0.0.1/db")
    options = made["kwargs"][0].get("options", "")
    assert "idle_in_transaction_session_timeout" in options, (
        "a password that merely CONTAINS the setting's name removed the "
        f"parked-handle bound (kwargs={made['kwargs'][0]!r})")


def test_an_operators_other_startup_options_survive_the_bound(monkeypatch):
    """Adding the bound must not DELETE the operator's own startup options.

    `options` is one STARTUP field, and psycopg2's keyword REPLACES a value the
    DSN already carries (`psycopg2.extensions.make_dsn`: asked for
    `?options=-c statement_timeout=5000` with `options=…` added, it returns ONLY
    the keyword's value — verified, not assumed). So passing the bound alone
    dropped `statement_timeout`, `search_path` and `application_name` on every
    connect: a silent session-configuration regression, and the same class of
    silent override the `connect_timeout` branch above exists to prevent.

    FALSIFIER: pass the bound without prefixing `operator_options` and this reds
    on the missing `statement_timeout`.
    """
    made = _fake_driver(monkeypatch)
    rw._default_connect(
        "postgresql://record@127.0.0.1/db"
        "?options=-c%20statement_timeout%3D5000")
    options = made["kwargs"][0].get("options", "")
    assert "statement_timeout=5000" in options, (
        "the pool DROPPED a startup option the operator set "
        f"(options={options!r})")
    assert (
        f"-c idle_in_transaction_session_timeout="
        f"{rw.IDLE_IN_TRANSACTION_TIMEOUT_SECONDS * 1000}" in options
    ), f"the bound did not ride along (options={options!r})"


def test_the_idle_set_is_bounded_and_overflow_is_closed():
    """The pool cannot grow without a bound: an idle handle over the cap is closed.

    FALSIFIER: park unconditionally and `RECORD_POOL_MAX_CONNECTIONS` stops
    meaning anything — the process would keep every connection it ever opened.
    """
    key = rw._pool_key("postgresql://record/db")
    conns = [FakeStatusConnection()
             for _ in range(rw.RECORD_POOL_MAX_CONNECTIONS + 1)]
    for conn in conns:
        rw._park_connection(key, conn)
    kept = [c for c in conns if c.closed == 0]
    closed = [c for c in conns if c.closed == 1]
    assert len(kept) == rw.RECORD_POOL_MAX_CONNECTIONS
    assert len(closed) == 1, (
        f"{len(closed)} handles were closed; the cap must close exactly the "
        f"overflow (cap={rw.RECORD_POOL_MAX_CONNECTIONS})")


def test_a_double_close_returns_one_handle_once(monkeypatch):
    """Two closes must not park the same handle twice.

    The door's `finally` and a caller's own `close()` can both run; psycopg2
    tolerates a double close, so the pooled handle must too — a second park would
    put one connection in the idle list twice and hand it to two records at once.

    FALSIFIER: drop the `_returned` guard in `_PooledConnection.close` — the
    second park appends the handle again and this reds.
    """
    made = _fake_driver(monkeypatch)
    _arm(monkeypatch)
    write = rw.write_records([_point(0)], org_id=ORG, graph_id=GRAPH, first_seq=1)
    assert write.applied
    write.connection.close()
    write.connection.close()
    handle = made["handles"][0]
    assert rw._take_idle_connection("postgresql://record@127.0.0.1/db") is handle
    assert rw._take_idle_connection("postgresql://record@127.0.0.1/db") is None, (
        "the same handle was parked twice — two records would share one "
        "connection and one transaction")


def test_an_unarmed_door_acquires_nothing(monkeypatch):
    """§2.0: an unarmed deployment stays byte-identical — no connection, no pool.

    FALSIFIER: acquire before the arming check and this reds on `len(dsns) == 1`
    for a DSN nobody configured.
    """
    monkeypatch.delenv(rw.RECORD_DSN_ENV, raising=False)
    made = _fake_driver(monkeypatch)
    assert rw.write_live_record(_point(0), seq=1, tenant=(ORG, GRAPH)) is None
    assert made["dsns"] == [], "an UNARMED live write opened a connection"


# ════════════════════════════════════════════════════════════════════════════
# The release contract: what a returned handle must NOT still be able to do
# ════════════════════════════════════════════════════════════════════════════


def test_a_returned_handle_refuses_further_use():
    """`close()` must be a FENCE, not only a release.

    Once the wrapper is returned, its raw handle is idle in the pool and can be
    borrowed by ANOTHER thread. A late `commit()` would then commit that thread's
    transaction, and a late `cursor()` would run statements on a connection this
    caller no longer owns — silent cross-talk where a closed psycopg2 connection
    used to raise `InterfaceError`. The pool must not turn a loud error into a
    silent one.

    FALSIFIER: forward `cursor`/`commit`/`rollback` straight to `self._raw` (drop
    `_live`) and this reds on all three — the calls succeed instead of raising.
    A FOURTH arm covers `__setattr__`, which is how a session setting like
    `autocommit` reaches the server: without its own fence a caller could
    re-poison the handle AFTER `_park_connection` normalised it, so the two fixes
    would cancel out (#9558 review cycle 2, P2, reproduced).
    A FIFTH arm covers the SAME mutation reached as a METHOD
    (`set_session(autocommit=True)`) — a method goes through `__getattr__`, so
    fencing `__setattr__` alone left that door open (#9558 review at
    `d51c7897d6`, P2, reproduced).
    A SIXTH arm covers the FENCE'S THIRD DOOR: a cursor obtained BEFORE the
    release. It holds no reference to `_returned`, so fencing `cursor()` alone
    leaves it fully usable on a handle the pool may have lent to another thread
    (#9558 review at `b8371c2350`, P2).
    """
    driver = pytest.importorskip("psycopg2", reason="the fence raises the "
                                                 "driver's own InterfaceError")
    raw = ScriptedConnection()
    handle = rw._pooled_connection(raw, _DSN)
    early = handle.cursor()          # obtained BEFORE the release (SIXTH arm)
    handle.close()
    assert early.closed is True, (
        "a cursor issued before `close()` survived the release OPEN — it can "
        "still issue statements on a handle the pool may have lent to another "
        "thread, which is the silent cross-talk this fence exists to prevent")
    with pytest.raises(driver.InterfaceError):
        early.execute("SELECT 1")
    for call in (lambda: handle.commit(),
                 lambda: handle.rollback(),
                 lambda: handle.cursor()):
        with pytest.raises(driver.InterfaceError):
            call()
    with pytest.raises(driver.InterfaceError):
        handle.autocommit = True
    with pytest.raises(driver.InterfaceError):
        handle.set_session(autocommit=True)
    assert getattr(raw, "autocommit", False) is False, (
        "a post-close ATTRIBUTE WRITE reached the parked handle — it can "
        "re-poison the same connection's session state after "
        "`_park_connection` already normalised it, which is the exact state the "
        "normalisation exists to prevent")
    assert raw.commits == 0 and raw.rollbacks == 0, (
        f"a post-close call reached the raw handle (commits={raw.commits}, "
        f"rollbacks={raw.rollbacks})")
    assert raw.closed == 0 and rw._take_idle_connection(_DSN) is raw, (
        "the handle was not still parked after the refused calls")


def test_a_returned_handle_forwards_cursor_arguments():
    """The wrapper must not NARROW the DB-API surface the doors were given.

    `record_serve`/`sdk` receive this object, and `cache_projection._rows` is
    written for a `RealDictCursor`. A `cursor(self)` with no parameter forwarding
    turns `conn.cursor(cursor_factory=...)` into a `TypeError` at a call site that
    was correct before the pool existed.

    FALSIFIER: `def cursor(self)` with no `*args`/`**kwargs` — this reds with
    TypeError instead of recording the call.
    """
    raw = ScriptedConnection()
    handle = rw._pooled_connection(raw, _DSN)
    marker = object()
    handle.cursor(cursor_factory=marker, name="a_named_cursor")
    args, kwargs = raw.cursor_calls[-1]
    assert kwargs == {"cursor_factory": marker, "name": "a_named_cursor"}, (
        f"the wrapper dropped the cursor arguments: {kwargs!r}")
    assert args == ()


def test_an_autocommit_handle_is_normalised_before_it_is_parked():
    """A parked handle must never be handed on in autocommit mode.

    The wrapper forwards session state on purpose, so a caller CAN set it. The
    next borrower would then get a connection where `_apply_tenant_scope`'s
    transaction-local `set_config` sets nothing (every record failing with a
    tenant-GUC error that blames the DSN) and `open_record`'s transaction-local
    `statement_timeout` stops applying (reads lose their bound in silence).

    FALSIFIER: drop the `autocommit` normalisation in `_park_connection` — the
    parked handle keeps `True` and this reds.
    """
    conn = FakeStatusConnection()
    conn.autocommit = True
    rw._park_connection(_DSN_KEY, conn)
    assert conn.autocommit is False, (
        "an autocommit handle was parked as-is — the next borrower inherits a "
        "session in which every transaction-local setting is a no-op")
    assert rw._take_idle_connection(_DSN) is conn


def test_the_pool_is_bounded_across_dsns():
    """A re-armed DSN must not leave sockets behind for every value it ever saw.

    The per-bucket cap alone is not a bound: `record_dsn()` re-reads the
    environment on every call, so a rotating credential or a per-tenant DSN makes
    a new bucket each time. Past `RECORD_POOL_MAX_IDLE_KEYS`, the oldest buckets
    are drained and closed.

    FALSIFIER: bound only per key — `len(_IDLE_CONNECTIONS)` grows past the cap
    and this reds.
    """
    conns = []
    for index in range(rw.RECORD_POOL_MAX_IDLE_KEYS + 2):
        conn = FakeStatusConnection()
        conns.append(conn)
        rw._park_connection(rw._pool_key(f"postgresql://record/db{index}"), conn)
    assert len(rw._IDLE_CONNECTIONS) <= rw.RECORD_POOL_MAX_IDLE_KEYS, (
        f"{len(rw._IDLE_CONNECTIONS)} buckets are idle; the cross-DSN cap is "
        f"{rw.RECORD_POOL_MAX_IDLE_KEYS}")
    assert any(c.closed for c in conns), (
        "nothing was evicted or closed — every bucket ever seen is still parked")


def test_the_read_door_bounds_its_read_transaction_locally(monkeypatch):
    """`open_record` must set its bound TRANSACTION-locally, not per session.

    `_default_connect` is pooled, so a session-level `SET statement_timeout` set
    by a read would outlive the read and bound the next BORROWER — a write door's
    record, or another tenant's read. `set_config(..., true)` scopes it to the
    read's own transaction, and the pool's reset-at-reuse clears the transaction
    — and the bound with it — before the next borrower sees the handle.

    FALSIFIER: restore `SET statement_timeout = %s` and this reds on the
    statement text — nothing else in the tree asserts it, which is exactly why
    the line needed a pin (#9558 review cycle 1, P2).
    """
    from tortoise import record_serve as rs

    monkeypatch.setenv(rw.RECORD_DSN_ENV, _DSN)
    conn = ScriptedConnection()
    monkeypatch.setattr(rw, "_default_connect", lambda dsn: conn)
    assert rs.open_record() is conn
    executed = [sql for sql, _params in conn.executed]
    assert executed, "the read door opened a connection and set nothing"
    assert "set_config" in executed[0], (
        f"the read bound is not transaction-local: {executed[0]!r} — a "
        f"session-level SET would leak onto the next borrower of a POOLED "
        f"connection")
    assert executed[0].rstrip().endswith("true)"), (
        f"`set_config` was called without the transaction-local flag: "
        f"{executed[0]!r}")
    assert conn.commits == 0, "the read door committed; a read must not"
