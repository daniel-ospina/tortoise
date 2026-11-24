"""#4625 — a record write performs ZERO filesystem operations on the migrations.

WHAT THIS PINS, and what each pin would RED on.

The production defect (`Measured at: e74cf1d13da76ba7176c73ebacd96cef843378f5`,
hosted `tortoise-y4mjjq`): a record write was caught inside
`replay_plan → _jsonb_or_scalar → jsonb_columns → _jsonb_columns_for_dir →
_declared_types_for_dir → pathlib.read_text`, on the single serving thread, inside
a **52.406 s** loop block (`loop_blocks.max_age_s`), with `capture.in_flight
16/16` and `capacity_refused_total 152` in the same window.

The mechanism measured for this lane, in-process, is the SCHEMA CACHE SHAPE and
not a volatile fingerprint (`_migrations_fingerprint` over the unchanged
directory returns an equal tuple across calls, and the production stack does not
reach it at all — that frame chain is guarded by a 5 s TTL, not a fingerprint).
So the property the fix establishes is stated as a count, not a timing:

    a WARM process serving a record write touches no migration file — no
    `read_text`, no `open`, no `stat`, no `exists`.

That is what these pins assert. A wall-clock number beside an assertion re-stales
against the run it came from, and the census bar elsewhere in this repo says the
same thing for the same reason.

⛔ WHY THIS IS NOT "CACHE FOREVER AND HOPE". The freshness the old TTL and the
per-call fingerprint bought is now a VERB: `_release_schema_cache()`.
⛔ `_jsonb_cache_clear()` is NOT a second one — every resident reader parses the
entry's memoised SQL, so popping that view re-parses the same bytes and an edit
stays invisible (measured; the module states it at the freshness block).
`test_the_refresh_verb_re_resolves_the_resident_schema`
pins both halves — that the resident schema is NOT re-checked per call (the
fix), and that an explicit refresh DOES see an edit (what is still guaranteed) —
so a future edit cannot keep the fix and silently drop the refresh.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from tortoise import record_projection as rp


def _real_migrations() -> Path:
    """The migrations the PROCESS would read — `_migrations_dir()`'s own answer.

    ⛔ NOT a second derivation of the repo path (#4625 review): a constant built
    from `__file__` agrees with `_migrations_dir()` only while that resolver
    picks the checkout, so an installed wheel would have this file copying
    `supabase/migrations` while the process reads `PACKAGED_MIGRATIONS` — the
    tests green over a tree nothing reads.
    """
    return rp._migrations_dir()


#: A record shape the projection plans end-to-end. `props` carries a string, so
#: the plan runs the `jsonb` encoding leg (`_jsonb_or_scalar`) — the frame the
#: production stack caught — and not only the column/refusal readers.
RECORD: dict[str, Any] = {
    "type": "PointAdded",
    "id": "p-4625",
    "seq": 1,
    "point": {
        "id": "p-4625",
        "title": "the schema read",
        "kind": "concept",
        "status": "active",
        "labels": ["a"],
        "properties": {"note": "a string parameter"},
    },
}


def _plan() -> None:
    """One record write's plan — the whole projection side of a record.

    `replay_plan` and not `replay`: the DB half of a write is a different
    surface (#14022's allocator, the tenant GUC) and nothing here is about it.
    """
    rp.replay_plan([RECORD], org_id="org-4625", graph_id="graph-4625",
                   position=False)


class _Clock:
    """A settable `time.monotonic`, so a BOUNDED-WINDOW mutant cannot hide.

    ⛔ WHY THIS EXISTS AT ALL (#4625 review). The pre-fix shape was not "no
    cache" — it was a 5 s TTL on `jsonb_columns()`, and a pin that makes three
    calls inside one sub-second burst is answered by the TTL itself, so it stays
    GREEN with the defect restored. Freezing the clock and then advancing it past
    any plausible window makes the assertion able to fail against the shape that
    actually shipped the 52.406 s block — "resolve once, but re-check every N
    seconds" — as well as against an uncached reader. ONE hour, so no bounded
    window a future lane might pick (5 s, 60 s, 600 s) can hide behind it.
    """

    STEP_SECONDS = 3600.0

    def __init__(self) -> None:
        # ⛔ START FROM THE REAL CLOCK, not from an arbitrary constant: a TTL
        # mutant compares `now - at` and a fake reading BELOW the real one it
        # was set against makes that difference NEGATIVE, which a `< window`
        # test reads as fresh — the pin would then pass over the mutant it
        # exists to catch (measured on a first cut of this very test).
        self.now = time.monotonic()

    def __call__(self) -> float:
        return self.now

    def advance(self) -> None:
        self.now += self.STEP_SECONDS


def _tree(base: Path) -> Path:
    """A copy of the record migrations the process reads, so a parse is a real one."""
    source = _real_migrations()
    target = base / "migrations"
    target.mkdir(parents=True, exist_ok=True)
    for name in rp.RECORD_MIGRATIONS:
        shutil.copy(source / name, target / name)
    return target


@pytest.fixture()
def fs_ops(monkeypatch) -> list[str]:
    """Every filesystem operation made ON THE MIGRATION FILES, by name.

    Four entry points, because "read" is not the only way a write path can touch
    the schema: `read_text`/`open` are the read, `stat`/`exists` are the
    fingerprint pass. `Path.exists` and `Path.stat` both call through
    `os.stat`, so a probe that counted only `read_text` would report a warm path
    as clean while it stat-ed twenty files per record — which is exactly the
    cheaper half of this defect.
    """
    base = rp._migrations_dir()
    roots = {str(base), str(base.resolve())}

    def _inside(path: Any) -> bool:
        text = str(path)
        return any(text == root or text.startswith(root + os.sep) for root in roots)

    ops: list[str] = []
    for attr in ("read_text", "open", "stat", "exists"):
        real = getattr(Path, attr)

        def probe(self, *args: Any, _real=real, _attr=attr, **kwargs: Any):
            if _inside(self):
                ops.append(f"{_attr}:{Path(str(self)).name}")
            return _real(self, *args, **kwargs)

        probe.__name__ = attr
        monkeypatch.setattr(Path, attr, probe)
    return ops


def test_a_warm_record_write_performs_no_migration_filesystem_operation(
        fs_ops, monkeypatch):
    """THE PIN: the second and every later record write touches no file.

    WHY THE COLD BUILD IS ASSERTED FIRST: a probe that never records anything is
    indistinguishable from a fix, and this suite would then pass over a process
    whose schema resolution had moved somewhere the probe cannot see. So the
    cold plan must be SEEN to read the FILES — a bare `fs_ops` is not enough,
    because the first resolution always probes the directory (`_migrations_dir`
    → `_complete` is one `exists` per registered migration), so `assert fs_ops`
    alone is satisfied by the directory probe and cannot tell a parse from a
    stat pass. Every plan after it must be silent.

    FALSIFIER (executed — see the PR body): restoring
    `key = (str(migrations_dir or _migrations_dir()), _migrations_fingerprint(…))`
    in `_schema_entry` reds this with a non-empty `fs_ops` naming
    `stat:`/`exists:` per migration. Restoring `_JSONB_CACHE_TTL_SECONDS = 5.0`
    and its `time.monotonic` memo reds it too, because the warm plans below run
    past the window on a clock this test controls — the calls are NOT a
    sub-second burst that the TTL itself would answer (#4625 review).
    """
    rp._release_schema_cache()
    rp._jsonb_cache_clear()
    _plan()
    assert fs_ops, (
        "the FIRST plan build of a fresh process touched no migration file — "
        "the probe is not observing the schema resolution at all, so the "
        "assertion below would pass over any implementation")
    assert any(op.startswith("read_text:") for op in fs_ops), (
        "the FIRST plan build probed the migration directory without READING a "
        f"single file ({sorted(set(fs_ops))}) — then `fs_ops` cannot tell a "
        "parse from the resolver's own `exists` pass, and the silence below "
        "would be asserted over a probe that never sees a read")
    del fs_ops[:]
    clock = _Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    for _ in range(3):
        clock.advance()
        _plan()
    assert fs_ops == [], (
        "a WARM process re-touched the migration files while writing a record: "
        f"{sorted(set(fs_ops))} ({len(fs_ops)} operation(s) for 3 records). The "
        "schema is baked into the deployed image (`migrations_source: "
        "\"checkout\"`), so this buys nothing a redeploy would not deliver — and "
        "on the single serving thread it is what held the loop for 52.406 s "
        "(#4625). Resolve the schema once; refresh through "
        "`_release_schema_cache()` (`_jsonb_cache_clear()` re-parses the same "
        "bytes and does NOT make an edit visible).")


def test_the_write_path_never_fingerprints_the_migrations(monkeypatch):
    """The stat half of the same property, pinned structurally.

    A count of `stat` calls can be satisfied by a fingerprint that happens to be
    memoised elsewhere; a fingerprint that RAISES cannot. So the resident path is
    warmed first and the fingerprint is then poisoned: a record write must
    complete without ever asking the filesystem which files it has.
    """
    rp._release_schema_cache()
    _plan()  # the one resolution this process pays

    def _poison(base: Path) -> tuple:
        raise AssertionError(
            "a record write consulted _migrations_fingerprint — the schema was "
            "revalidated against the FILESYSTEM on the write path (#4625)")

    monkeypatch.setattr(rp, "_migrations_fingerprint", _poison)
    # ⛔ POSITIVE CONTROL FIRST: the poison must be REACHABLE, or the silence
    # below is about a patch that never applied rather than about the resident
    # path. An explicit-directory read DOES take the fingerprint, so it must
    # raise the poison's own message.
    with pytest.raises(AssertionError, match="revalidated against the FILESYSTEM"):
        rp.table_columns(_real_migrations())
    _plan()
    _plan()


def test_the_refresh_verb_re_resolves_the_resident_schema(monkeypatch, tmp_path):
    """BOTH HALVES OF THE TRADE, so neither can be dropped on its own.

    1. An edit to the files of a RUNNING process is NOT seen by its next record.
       That is the deliberate cost of the fix (and it is why the deployed
       immutability matters), stated here rather than left to be discovered.
    2. An explicit refresh IS seen. `_release_schema_cache()` is the verb the
       S6 surface (`#9558`) and every dev/test editing session depend on, and a
       future change that keeps (1) and loses (2) would red here rather than
       quietly turn "resolve once" into "never".
    """
    tree = _tree(tmp_path)
    monkeypatch.setattr(rp, "_migrations_dir", lambda: tree)
    rp._release_schema_cache()
    try:
        assert "refresh_probe_column" not in rp.table_columns()["point"]
        target = tree / rp.RECORD_MIGRATIONS[-1]
        target.write_text(
            target.read_text(encoding="utf-8")
            + "ALTER TABLE public.point ADD COLUMN refresh_probe_column text;\n",
            encoding="utf-8")
        assert "refresh_probe_column" not in rp.table_columns()["point"], (
            "an edit to the migrations was seen by the RESIDENT read without a "
            "refresh — then the read is not resolved once, and #4625's cost is "
            "still on the write path")
        rp._release_schema_cache()
        assert "refresh_probe_column" in rp.table_columns()["point"], (
            "`_release_schema_cache()` did not re-resolve the resident schema — "
            "the refresh verb is the ONLY way a running process may see an "
            "edited migration now, so losing it makes the cache unfalsifiable")
    finally:
        rp._release_schema_cache()


def test_the_jsonb_reset_cannot_make_an_edit_visible(monkeypatch, tmp_path):
    """`_jsonb_cache_clear()` is a RESET, not a refresh — pinned as BEHAVIOUR.

    WHY THIS PIN EXISTS (#14459 review, P2). `_jsonb_cache_clear`'s docstring used
    to close by saying it exists so a process that must PROVE a fresh read has a
    verb — five lines below the same docstring's headline denying exactly that,
    and contradicted by this PR's own freshness story (every resident reader
    parses the entry's memoised SQL, so a `pop` re-parses the SAME bytes). A lane
    that read that sentence would reach for the reset after a red pin, get a
    green, and have proven nothing — the false-PASS class this PR is written
    against. The prose is corrected; this test makes the correction EXECUTABLE,
    so the claim cannot drift back without a red.

    FALSIFIER (executed — see the commit message): make the reset actually a
    refresh (`_jsonb_cache_clear` additionally detaching the resident entry, the
    shape the old sentence promised) and the first `not in` assertion below reds,
    because the edited column then appears without `_release_schema_cache()`.
    """
    tree = _tree(tmp_path)
    monkeypatch.setattr(rp, "_migrations_dir", lambda: tree)
    rp._release_schema_cache()
    try:
        assert "reset_probe_jsonb" not in rp.jsonb_columns()
        target = tree / rp.RECORD_MIGRATIONS[-1]
        target.write_text(
            target.read_text(encoding="utf-8")
            + "ALTER TABLE public.point ADD COLUMN reset_probe_jsonb jsonb;\n",
            encoding="utf-8")
        rp._jsonb_cache_clear()
        assert "reset_probe_jsonb" not in rp.jsonb_columns(), (
            "`_jsonb_cache_clear()` made an edit to the migrations visible — it is "
            "documented as a RESET over the same certified bytes, and the only "
            "refresh verb is `_release_schema_cache()`. Either the behaviour or "
            "the docstring must move, and the docstring is what this PR fixed.")
        assert "reset_probe_jsonb" not in rp.table_columns()["point"], (
            "the jsonb reset reached the column map — it drops one derived view, "
            "not the memoised SQL both views are parsed from")
        rp._release_schema_cache()
        assert "reset_probe_jsonb" in rp.jsonb_columns(), (
            "`_release_schema_cache()` did not re-resolve the resident schema — "
            "the control half of this pin is dead, so the assertions above prove "
            "only that NOTHING can see an edit")
        assert "reset_probe_jsonb" in rp.table_columns()["point"]
    finally:
        rp._release_schema_cache()


def test_a_named_directory_is_still_revalidated_per_call(tmp_path):
    """The OTHER mode of `_schema_entry`, pinned so the split is not tidied away.

    Every S6 pin (`tests/test_9558_record_write_cost.py`) drives an EXPLICIT
    directory and depends on file-level invalidation with no refresh verb. That
    mode is what a tool asking "what does THIS tree say right now" needs, and it
    is the mode a later simplification is most likely to delete in the name of
    "resolve once". It is pinned here at the seam rather than only through its
    consequences.
    """
    tree = _tree(tmp_path)
    rp._release_schema_cache()
    try:
        assert "direct_probe_column" not in rp.table_columns(tree)["point"]
        target = tree / rp.RECORD_MIGRATIONS[-1]
        target.write_text(
            target.read_text(encoding="utf-8")
            + "ALTER TABLE public.point ADD COLUMN direct_probe_column text;\n",
            encoding="utf-8")
        assert "direct_probe_column" in rp.table_columns(tree)["point"], (
            "an EXPLICIT migrations directory was served from a stale parse — "
            "the fingerprint mode that `#9558`'s edit-detection pins depend on "
            "is gone")
    finally:
        # …and no entry keyed to a `tmp_path` outlives this test: the cache is
        # process-global, so leaving one behind hands the next test a tree that
        # no longer exists.
        rp._release_schema_cache()


def test_the_jsonb_reader_reads_nothing_on_a_warm_process(fs_ops, monkeypatch):
    """The exact frame the production stack caught, pinned on its own.

    `jsonb_columns()` is called once per STRING PARAMETER of every statement, so
    it was guarded by a 5 s TTL — and a TTL that expires re-reads twenty files.
    The production stack is that expiry, seen from inside `replay_plan`
    (`record_projection.py:2608 → :2524 → :2481 → pathlib.py:1027 read_text` at
    `e74cf1d13d`). It must now be a dict lookup.

    ⛔ AND THE PIN MUST BE ABLE TO FAIL AGAINST *THAT* SHAPE (#4625 review). Three
    warm calls microseconds apart are answered by a restored 5 s TTL itself, so
    a burst-only pin stays green over the defect it documents. Two independent
    probes close that, and either one alone would be enough:

    1. the warm calls run on a CLOCK THIS TEST OWNS, advanced an hour before
       each one — a TTL, a fingerprint, or any "re-check every N seconds"
       window therefore expires and its re-read lands in `fs_ops`;
    2. the producer is POISONED after the cold read, in the shape
       `test_the_write_path_never_fingerprints_the_migrations` already uses, so
       ANY re-parse reds regardless of what clock it consults. The poison's own
       reachability is proven with a positive control (an explicit-directory
       read DOES call the producer, so it must raise the poison's message).

    FALSIFIER (executed): restore `_JSONB_CACHE_TTL_SECONDS = 5.0` and its
    `time.monotonic` memo — the warm loop reds with `read_text:`/`open:` per
    migration. Remove the memoisation entirely and the poison reds it.
    """
    rp._release_schema_cache()
    rp._jsonb_cache_clear()
    rp.jsonb_columns()
    assert fs_ops, "the cold read touched nothing — the probe proves nothing"
    del fs_ops[:]

    clock = _Clock()
    monkeypatch.setattr(time, "monotonic", clock)
    for _ in range(3):
        clock.advance()
        rp.jsonb_columns()
    assert fs_ops == [], (
        "`jsonb_columns()` re-read the migration files on a warm process: "
        f"{sorted(set(fs_ops))} — this is the #4625 stack frame, and it runs "
        "once per bound string parameter of every statement. The warm calls "
        "above run on an ADVANCING clock, so a bounded-window cache (the 5 s "
        "TTL that shipped the 52.406 s block) reds here too, not only an "
        "uncached reader.")

    def _poison(base: Path) -> frozenset[str]:
        raise AssertionError(
            "`jsonb_columns()` re-parsed the migrations on a WARM process — the "
            "resident map was not reused, so every bound string parameter pays "
            "a file read again (#4625)")

    monkeypatch.setattr(rp, "_jsonb_columns_for_dir", _poison)
    # ⛔ POSITIVE CONTROL FIRST: an explicit-directory read parses directly, so
    # it must reach the poison. Without this, the silence below is about a patch
    # that never applied rather than about the resident read.
    with pytest.raises(AssertionError, match="re-parsed the migrations on a WARM"):
        rp.jsonb_columns(_real_migrations())
    clock.advance()
    rp.jsonb_columns()


def test_an_absent_schema_heals_without_the_refresh_verb(monkeypatch, tmp_path):
    """Absence→appear on the RESIDENT path heals by itself, through ALL THREE readers.

    The fix made the resident entry permanent, so a process that served a write
    BEFORE its migrations were readable would otherwise raise
    `RecordProjectionError` on every later write for the life of the process —
    cured only by `_release_schema_cache()`, which has no production caller.
    Pre-#4625 the next consult healed, and it still must: the fix buys zero
    filesystem operations on the HEALTHY path, never a permanent hard failure
    while the schema is merely not there yet.

    ⛔ EVERY READER THAT ANSWERS FROM THE RESIDENT ENTRY IS DRIVEN HERE (#4625
    review), because they do NOT share one failure shape: `table_columns()` and
    `jsonb_columns()` fail closed while the schema is absent, so neither can
    memoise it — but `refused_props_names()` answers an EMPTY map, which is the
    SILENT shape, and a `{}`-is-not-None cache probe freezes it for the
    process's life. A pin that drives only `table_columns()` leaves that half
    uncovered.

    FALSIFIER (executed): memoise the empty read again (`entry["sql"] = sql`
    unconditionally in `_entry_sql_state`) and the second `table_columns()` below keeps
    raising; memoise the refusal parse unconditionally
    (`entry["parsed"]["refused_props_names"] = out`) and `healed` stays `{}`.
    """
    source = _real_migrations()          # the real tree, captured BEFORE the patch
    tree = tmp_path / "migrations"
    tree.mkdir()
    monkeypatch.setattr(rp, "_migrations_dir", lambda: tree)
    rp._release_schema_cache()
    try:
        with pytest.raises(rp.RecordProjectionError, match="not on disk"):
            rp.table_columns()
        with pytest.raises(rp.RecordProjectionError, match="not on disk"):
            rp.jsonb_columns()
        assert rp.refused_props_names() == {}, (
            "the refusal map is expected to answer EMPTY (not raise) while the "
            "schema is absent — if that changed, this pin's premise changed "
            "with it")
        # …the schema becomes readable under the SAME process, no refresh verb.
        for name in rp.RECORD_MIGRATIONS:
            shutil.copy(source / name, tree / name)
        assert "point" in rp.table_columns(), (
            "an absence→appear transition on the RESIDENT schema did not heal: "
            "the empty read is memoised, so every record write raises for the "
            "life of the process and the only cure (`_release_schema_cache()`) "
            "has no production caller. Do not memoise an ABSENT read.")
        healed = rp.refused_props_names()
        assert healed.get("point"), (
            "the refusal map did not heal: the `{}` parsed from the ABSENT read "
            "was memoised, so this reader answers 'no name is refused' for the "
            "life of the process. The projection then stops dropping the "
            "derived/EP-scalar names BY NAME and `chk_*_props_no_derived` "
            f"rejects the row instead (healed={healed}). An empty read must not "
            "satisfy the cache probe — mirror `_entry_sql_state`.")
        assert rp.jsonb_columns(), (
            "the jsonb name map did not heal. It fails closed on an absent "
            "schema today, so this can only red on a 'make it cheap' tidy-up "
            "that memoises the empty answer instead — which would silently "
            "stop the #9548 encoding and drop the row with no signal.")
        # …and the healthy path still resolves ONCE: the memo is taken now.
        assert "point" in rp.table_columns()
        assert rp.refused_props_names()
    finally:
        rp._release_schema_cache()


#: The migration that declares `point`'s money columns by a forward
#: `ALTER TABLE … ADD COLUMN` (#2782) — omitting it leaves a NON-EMPTY, silently
#: SHORT column map, which is the case a "read is empty?" guard alone misses.
_PARTIAL_OMITTED = "20261018000001_record_point_money_value_2782.sql"

#: The PARTIAL case for the third reader, whose own reader
#: (`_jsonb_columns_for_dir`) fails closed on a tree with NO record table and
#: cannot see one that is merely missing a file (#4625 review, F2). Measured on
#: this head: omitting this migration removes SEVEN `jsonb` names the full set
#: carries (`about_entities`, `keywords`, `participants`, `payload`,
#: `provenance_spans`, `topics`, `url_aliases`) and raises nothing — the SHORT,
#: plausible answer the memo must not freeze.
_PARTIAL_JSONB_OMITTED = "20261012000001_record_event_stream.sql"


def test_a_partially_present_schema_heals_without_the_refresh_verb(monkeypatch, tmp_path):
    """incomplete→complete on the RESIDENT path heals by itself (#4625 review).

    The absent-schema pin above covers the read that returns `""`. This one covers
    the read that returns SOMETHING: `_read_migrations` skips every absent file
    (`if (base / name).exists()`), so a tree missing one of the twenty yields a
    non-empty join of the other nineteen. A resident entry that memoised THAT
    freezes a silently short column map for the life of the process — the record
    would then write a row without the missing migration's columns, which is the
    row-drop class `#9548` exists to end — and no raise ever surfaces it, so
    `table_columns()`'s own guard is not the protection here.

    ⛔ THE GATE IS PER READER, so the discriminating arm here is `table_columns`'
    own: measured, dropping THAT gate reds this test, while dropping
    `refused_props_names`' gate reds
    `test_an_absent_schema_heals_without_the_refresh_verb` instead (this tree
    omits a migration that declares no refusal, so the gate is unobservable
    here — the `refused_props_names()` call below runs only after the heal), and
    `jsonb_columns` has its own pin
    (`test_a_partial_jsonb_read_does_not_freeze_the_name_set`). An earlier
    revision of this docstring claimed all three red HERE, which was not true.

    FALSIFIER (executed): restore `if sql: entry["sql"] = sql` (memoise any
    non-empty read) and `amount_minor` never appears below.
    """
    source = _real_migrations()          # the real tree, captured BEFORE the patch
    tree = tmp_path / "migrations"
    tree.mkdir()
    for name in rp.RECORD_MIGRATIONS:
        if name != _PARTIAL_OMITTED:
            shutil.copy(source / name, tree / name)
    monkeypatch.setattr(rp, "_migrations_dir", lambda: tree)
    rp._release_schema_cache()
    try:
        short = rp.table_columns()
        assert "point" in short, (
            "the partial tree did not even parse the base `CREATE TABLE` — this "
            "pin is not exercising the partial-read path it claims")
        assert "amount_minor" not in short["point"], (
            "the omitted migration's column is present without its file, so the "
            "fixture does not create a partial read")
        # …the missing migration appears under the SAME process, no refresh verb.
        shutil.copy(source / _PARTIAL_OMITTED, tree / _PARTIAL_OMITTED)
        healed = rp.table_columns()
        assert "amount_minor" in healed["point"], (
            "a partially-present schema was memoised from a non-empty read: the "
            "column map stays short for the life of the process, with no error "
            "and no production caller for the refresh verb. Do not memoise an "
            "INCOMPLETE read — gate the memo on the completeness of THAT read.")
        assert rp.refused_props_names(), (
            "the refusal map memoised an answer parsed from a SHORT read: the "
            "§17.5 carve-out is silently disabled from then on")
    finally:
        rp._release_schema_cache()


def test_a_partial_jsonb_read_does_not_freeze_the_name_set(monkeypatch, tmp_path):
    """The third reader's complete-read gate, which had no pin (#4625 review, F2).

    `jsonb_columns()` fails closed when NO record table is parseable, so the
    ABSENT-tree pin above cannot reach its gate: a tree that is merely MISSING one
    migration yields a SHORTER, plausible name set instead of raising, and a
    memoised short set silently changes how a bound parameter is encoded (a
    `jsonb` column read as a scalar is JSON-quoted; a narrowed one read as a
    container is not), with no error anywhere.

    FALSIFIER (executed): drop ONLY the `if complete:` gate in `jsonb_columns` and
    the assertion below reds on `payload` — one of the names the short read DROPS.
    (`embedding` would NOT discriminate here: it survives the omission, so an
    earlier revision of this docstring named the wrong witness.)
    """
    source = _real_migrations()
    tree = tmp_path / "migrations"
    tree.mkdir()
    for name in rp.RECORD_MIGRATIONS:
        if name != _PARTIAL_JSONB_OMITTED:
            shutil.copy(source / name, tree / name)
    monkeypatch.setattr(rp, "_migrations_dir", lambda: tree)
    rp._release_schema_cache()
    try:
        short = rp.jsonb_columns()
        assert "payload" not in short, (
            "the omitted migration's `jsonb` names are present without its file, "
            "so the fixture does not create a partial read")
        shutil.copy(source / _PARTIAL_JSONB_OMITTED, tree / _PARTIAL_JSONB_OMITTED)
        healed = rp.jsonb_columns()
        assert "payload" in healed, (
            "a partially-present schema froze the `jsonb` NAME SET: the names "
            "the absent migration declares is missing for the life of the "
            "process, so every parameter bound to it is encoded on the wrong "
            "side of the rule — silently. Memoise only a COMPLETE read.")
    finally:
        rp._release_schema_cache()


def test_an_edit_under_a_resolved_schema_cannot_split_the_three_readers(
        monkeypatch, tmp_path):
    """The residents parse the SAME certified bytes, so they cannot disagree (#4625).

    This is the shape that made an earlier revision of this fix write a value
    UNENCODED: `table_columns()` / `refused_props_names()` answer from the schema
    entry's memoised SQL, so an EDIT or a REMOVAL under an already-resolved entry
    leaves them on the old parse — while a `jsonb_columns()` that RE-READ the
    directory saw the new tree. The column stayed in the column map, the `jsonb`
    name left the name set, and `_jsonb_or_scalar` then bound that parameter on the
    wrong side of the encoding rule (measured: `payload` in the map, absent from
    the set, `_jsonb_or_scalar("payload", "raw string")` returning the raw string,
    no error anywhere) — the `22P02` row-drop class this module documents.

    ⛔ `jsonb_columns()` is NOT called before the mutations: an earlier cut called
    it in a "before" assertion, which memoised the name set and made every arm
    below pass for the wrong reason (the memo, not the parse). The only thing
    resolved first is the SQL, which is exactly the state a parameter-free batch
    leaves behind.

    FALSIFIER (executed): restore `names = _jsonb_columns_for_dir(entry["base"])`
    on the resident path and the REMOVAL arm below reds.
    """
    source = _real_migrations()
    tree = tmp_path / "migrations"
    tree.mkdir()
    for name in rp.RECORD_MIGRATIONS:
        shutil.copy(source / name, tree / name)
    monkeypatch.setattr(rp, "_migrations_dir", lambda: tree)
    rp._release_schema_cache()
    try:
        # …the SQL memo is taken here; the jsonb name set is NOT.
        before_map = rp.table_columns()
        assert "payload" in before_map.get("journal_event", ()), (
            "the fixture does not carry the `jsonb` column this pin is about")

        # (1) REMOVAL under the resolved entry.
        (tree / _PARTIAL_JSONB_OMITTED).unlink()
        assert "payload" in rp.jsonb_columns(), (
            "a REMOVAL under an already-resolved schema changed the `jsonb` name "
            "set while the column map kept the column: the resident readers are "
            "reading different things, so a value bound to that column is written "
            "on the wrong side of the encoding rule — silently, the 22P02 class. "
            "Parse the certified SQL, like the other two readers.")
        shutil.copy(source / _PARTIAL_JSONB_OMITTED, tree / _PARTIAL_JSONB_OMITTED)

        # (2) …and the refresh VERB is what sees a change: re-resolve, then the
        # same removal IS visible (the documented trade, pinned from the other
        # side — a verb that does not actually re-resolve is the failure).
        (tree / _PARTIAL_JSONB_OMITTED).unlink()
        rp._release_schema_cache()
        with pytest.raises(rp.RecordProjectionError, match="not on disk"):
            rp.table_columns()
        assert "payload" not in rp.jsonb_columns(), (
            "after `_release_schema_cache()` the removal should be VISIBLE — if "
            "it is not, the verb does not re-resolve the jsonb reader either")
    finally:
        rp._release_schema_cache()


def test_the_two_parse_paths_agree_on_the_real_tree():
    """The directory parse and the concatenated-SQL parse must not diverge.

    The resident readers parse the certified SQL as ONE chunk; `column_types()` and
    the explicit-directory mode parse ONE CHUNK PER FILE (comments stripped per
    file). They must answer the same thing, or a caller that moves between the two
    modes silently changes what the schema is believed to say — the #11075
    contract, one layer down. Pinned here so a future divergence REDS instead of
    shipping.

    FALSIFIER (executed): strip comments AFTER joining rather than per file, in a
    `/tmp` copy, and this test still passes (the two are equivalent today) — what
    it reds on is a real semantic change to either path, e.g. dropping the
    `ALTER … ALTER COLUMN … TYPE` shape from `_declared_types_from_chunks`.
    """
    tree = _real_migrations()
    per_file = rp._declared_types_for_dir(tree)
    joined = rp._declared_types_from_chunks(
        [rp._strip_sql_comments(
            "\n".join((tree / name).read_text(encoding="utf-8")
                      for name in rp.RECORD_MIGRATIONS))])
    assert per_file == joined, (
        "the per-file parse and the concatenated-SQL parse disagree about the "
        "record schema — the resident `jsonb` reader and `column_types()` would "
        "then answer differently for the same tree")


def test_an_incomplete_read_never_reaches_the_sql_memo(monkeypatch, tmp_path):
    """The WRITE rule that makes the cache-hit flag safe (#4625 review, round 2).

    The prose on `_entry_sql_state` and `refused_props_names` used to say "the
    returned flag is THIS CALL'S, not the entry's". That is false on a cache HIT —
    there the flag literally IS `entry["sql"] is not None` and this call reads
    nothing — and what actually carries the safety is WHERE THE WRITE IS:
    `entry["sql"]` is assigned only on the complete branch, so no ABSENT and no
    SHORT join can reach the memo, and therefore no reader can ever be certified
    `True` over one.

    That mechanism had no pin of its own: the three self-heal pins above observe
    its CONSEQUENCE (a short map is never frozen), which passes even if the memo
    stores a short SQL the readers happen to re-derive past. This one holds the
    rule directly, at the seam the readers trust, in three steps.

    FALSIFIER (executed): `if complete:` -> `if sql:` reds step 2
    (`entry["sql"] is None`), and an unconditional `entry["sql"] = sql` reds step
    1 — the two shapes that freeze a short map for the life of the process.
    """
    source = _real_migrations()
    tree = tmp_path / "migrations"
    tree.mkdir()
    monkeypatch.setattr(rp, "_migrations_dir", lambda: tree)
    rp._release_schema_cache()
    try:
        entry = rp._resident_entry()
        # 1. ABSENT — the read yields `""`, which must NOT be stored: storing it
        #    would make every later consult answer `(sql, True)` without reading,
        #    so the process would never see its migrations appear.
        sql, complete = rp._entry_sql_state(entry)
        assert (sql, complete) == ("", False), (
            "an absent tree did not read as (\"\", False) — the fixture is not "
            f"exercising the absent path: {sql[:40]!r}, {complete!r}")
        assert entry["sql"] is None, (
            "an ABSENT read was stored as the entry's SQL: every later consult "
            "now answers `(sql, True)` without reading, so the process never "
            "sees its migrations appear. Only a COMPLETE read may be memoised.")

        # 2. PARTIAL — a non-empty join of only the files that happen to exist.
        #    This is the shape `if sql:` would store.
        for name in rp.RECORD_MIGRATIONS:
            if name != _PARTIAL_OMITTED:
                shutil.copy(source / name, tree / name)
        sql, complete = rp._entry_sql_state(entry)
        assert sql and not complete, (
            "the partial tree did not yield a NON-EMPTY incomplete read, so this "
            f"step cannot discriminate the `if sql:` shape: {len(sql)} chars, "
            f"{complete!r}")
        assert entry["sql"] is None, (
            "a PARTIALLY-present tree was stored as the entry's SQL. The memo is "
            "then a silently SHORT join served to every later consult — and on the "
            "cache-hit path that map is returned WITH `True`, which is exactly the "
            "certification the prose says cannot happen. Gate the write on the "
            "completeness of THIS read, never on the read being non-empty.")

        # 3. COMPLETE — the write is allowed, and the memo is then what a later
        #    call is handed (the HIT branch). `(sql, True)` there is legitimate
        #    ONLY because steps 1 and 2 kept every incomplete join out of it.
        shutil.copy(source / _PARTIAL_OMITTED, tree / _PARTIAL_OMITTED)
        sql, complete = rp._entry_sql_state(entry)
        assert complete, "the completed tree did not read as complete"
        assert entry["sql"] == sql, (
            "a COMPLETE read was not memoised, so the warm path re-reads twenty "
            "files per consult — the #4625 cost the whole change exists to remove")
        assert rp._entry_sql_state(entry) == (sql, True), (
            "the memo was not served back to a later call")
    finally:
        rp._release_schema_cache()


def test_a_commented_out_ALTER_cannot_reach_exactly_one_reader(monkeypatch, tmp_path):
    """All three residents parse the SAME text, including its comment stripping (#4625).

    A record migration carries its DOWN direction in comments, and
    `_ALTER_ADD_COLUMN_RE` is `re.S`, so a commented
    `-- ALTER TABLE public.point ADD COLUMN … jsonb;` is a live match for a parser
    that reads RAW text and no match at all for one that strips. While the `jsonb`
    reader stripped and the other two did not, that single comment made them
    disagree about pre-processing rather than about the tree — measured: the column
    map kept the column, the `jsonb` name set had no such name, and
    `_jsonb_or_scalar` bound the value as a RAW string (the `22P02` row-drop
    class). The stripping now happens ONCE, when the SQL is stored.

    FALSIFIER (executed): drop `_strip_sql_comments` from the store and this pin
    reds on the column-map assertion.
    """
    source = _real_migrations()
    tree = tmp_path / "migrations"
    tree.mkdir()
    for name in rp.RECORD_MIGRATIONS:
        shutil.copy(source / name, tree / name)
    down = (
        "\n-- Down (run by hand).\n"
        "-- ALTER TABLE public.point ADD COLUMN payload_note jsonb;\n")
    target = tree / rp.RECORD_MIGRATIONS[0]
    target.write_text(target.read_text(encoding="utf-8") + down, encoding="utf-8")
    monkeypatch.setattr(rp, "_migrations_dir", lambda: tree)
    rp._release_schema_cache()
    try:
        columns = rp.table_columns()
        names = rp.jsonb_columns()
        assert "payload_note" not in columns.get("point", ()), (
            "a COMMENTED-OUT `ADD COLUMN` reached the column map — a migration's "
            "DOWN direction is not its UP. The resident readers must parse the "
            "same comment-stripped text.")
        assert "payload_note" not in names, (
            "a COMMENTED-OUT `ADD COLUMN … jsonb` reached the `jsonb` name set")
        # …and the two readers were looking at the same text all along: an
        # unstripped store is exactly how one of them sees this comment and the
        # other does not.
        assert rp._resident_entry()["sql"] is not None
        assert "-- Down (run by hand)" not in rp._resident_entry()["sql"], (
            "the stored SQL still carries comments, so each reader's own "
            "pre-processing decides what it sees — the divergence this pins")
    finally:
        rp._release_schema_cache()
