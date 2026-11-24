"""#14582 — the creation hook that gives a new graph its cursor row, and the gap.

THE STATE THIS EXISTS IN. Production is ARMED (`TORTOISE_RECORD_ALLOCATE=1`), so
on a graph with an `active` `record_cursor` row the record is the ONLY allocator
and `tortoise.live_write.write` skips the JSONL append (#14002). A graph created
after that cutover had **no** cursor row, and `write_live_record` is fail-soft by
contract, so nothing raised: the user's write succeeded, the record accumulated
nothing for that graph, and `/health` read `ok: true` throughout.

WHAT THIS FILE HOLDS DOWN, and how each leg could go quietly wrong:

* the HOOK **ARMS**, it does not only seed. A `seeded` row allocates nothing
  (`ALLOCATION_SQL` is scoped to `active`), so a hook that stopped at `seeded`
  would leave the graph's first record write refused `cursor_not_active` — the
  exact defect — while looking like a fix. This is the plan's `§A7.8` item 8 /
  M16 falsifier, and it is why the row is born `active` rather than `seeded`;
* every CONTROL-PLANE door calls it — pinned from the AST, on the DOOR'S OWN BODY
  rather than on a helper's, because the call is one line and its absence is
  invisible at runtime until a graph is uncut: `hosted_api._provision_graph`
  (both HTTP create endpoints route through it) and
  `supabase_control.ensure_default_graph_row`'s THREE id-yielding arms;
* the doors that CANNOT be hooked are pinned as NOT hooked, on purpose, because
  both are findings rather than omissions (`sdk._graph_create`: the Supabase arm
  persists nothing — F1 — and the registry arm creates a graph `public.graphs`
  never holds, so the FK refuses it — F3). An unpinned absence is how the next
  reader "fixes" it back into a permanently-red health field;
* a graph the record ALREADY knows is REFUSED, not seeded (`#12894`'s divergence
  rule) — `SEED_SQL`'s own guard decides it, there is no second predicate here;
* `/health` CANNOT report `record.ok: true` on an armed deployment carrying an
  uncut graph, and it publishes COUNTS ONLY (the endpoint is unauthenticated);
* the BACKFILL reports `considered / seeded / refused` and is idempotent, and its
  `--dry-run` runs the real decision and writes nothing.

The DB legs (`TEST_RECORD_DB_URI`) are the point: a cursor row and a first
APPLIED write need a real record DSN. They skip without it and are armed by the
`test-record-postgres` job, whose leg gate reds on any skip.
"""
from __future__ import annotations

import ast
import contextlib
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tortoise import record_writer as rw

_HAS_RECORD_DB = os.environ.get("TEST_RECORD_DB_URI")
_ROOT = Path(__file__).resolve().parent.parent

#: One record that expands to node/identity mutations AND a position row — the
#: same shape `tests/test_12894_record_cursor_live.py` and
#: `tests/test_14002_record_only_journal.py` use, so the suites cannot disagree
#: about what a record looks like.
RECORD = {
    "type": "PointAdded",
    "ts": "2026-11-24T00:00:00Z",
    "id": "p-14582",
    "point": {"id": "p-14582", "content": "a new graph is born cut over",
              "pointKind": "claim"},
}

#: The DOORS, as `(module, door function, callee that must appear in its body)`.
#: ⛔ THE DOOR'S OWN BODY, not a helper's: pinning `hosted_api.py` for the STRING
#: `_provision_graph_record` is satisfied by the helper's definition, so deleting
#: the one call site in `_provision_graph` would keep the pin green — the exact
#: regression the pin exists to catch (#14582 review P1-4).
_DOORS = (
    ("tortoise/hosted_api.py", "_provision_graph", "_provision_graph_record"),
    ("tortoise/supabase_control.py", "ensure_default_graph_row",
     "_provision_default_graph_record"),
)

#: How many hook calls each door's body must carry. `ensure_default_graph_row`
#: yields an id from THREE arms (active-row self-heal, mint, convergent adopt) and
#: hooks each; a refactor that collapses them to one silently drops two.
_DOOR_HOOK_CALLS = {
    ("tortoise/hosted_api.py", "_provision_graph"): 1,
    ("tortoise/supabase_control.py",
     "ensure_default_graph_row"): 3,
}

#: The helpers those doors call must themselves reach the writer's hook.
_HELPERS = (
    ("tortoise/hosted_api.py", "_provision_graph_record"),
    ("tortoise/supabase_control.py", "_provision_default_graph_record"),
)

#: Doors this lane REFUSES to hook, pinned so the refusal cannot silently rot
#: into an omission: `sdk._graph_create` (F1: its Supabase arm persists no row;
#: F3: its registry arm creates a graph the record cannot file — `public.graphs`
#: never holds it, and `record_cursor`'s FK refuses it).
_UNHOOKED = (("tortoise/sdk.py", "_graph_create"),)


# ── DB-free: the hook is wired, and it ARMS ────────────────────────────────
#
# These run in the fast matrix (registered on `core`), so a lane that deletes the
# hook, or downgrades it to a seed, reds without a database.

def _source(rel: str) -> str:
    return (_ROOT / rel).read_text()


def _call_names(node: ast.AST) -> set[str]:
    """Every name this node CALLS, by the callee's own identifier.

    Covers `f(...)`, `mod.f(...)`, and `mod.f` referenced as a value — the three
    spellings a door's one line can take. Deliberately structural: a substring
    search would be satisfied by a comment, which is exactly how
    `provision_graph_record` "existed" in two comments and zero call sites
    before this change.
    """
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        elif isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


def _functions(rel: str) -> dict[str, ast.AST]:
    tree = ast.parse(_source(rel))
    return {node.name: node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _hook_calls(node: ast.AST, callee: str) -> int:
    """How many times `node`'s body calls `callee`, however it is spelled.

    Written against :func:`_call_names`'s structural approach rather than a
    substring search, so a mention in a document/comment cannot satisfy it.
    """
    count = 0
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name == callee:
            count += 1
    return count


def test_every_creation_door_calls_the_hook():
    """⛔ THE WIRING PIN. A door that drops the call is the defect, not a style.

    The absence is invisible at runtime: the graph is still created, writes still
    succeed for the user, and only the record leg silently stops. So the census
    is pinned structurally rather than trusted to a review — and pinned on the
    DOOR, because a helper that still calls the hook proves only that the helper
    is intact.
    """
    missing = []
    for rel, function, callee in _DOORS:
        node = _functions(rel).get(function)
        assert node is not None, (
            f"{rel}: `{function}` is gone — the door the census found was "
            f"renamed or removed; re-run the census (`rg -n 'create_graph|"
            f"insert_graph|ensure_default_graph_row|_graph_create' tortoise/`) "
            f"and pin the new name rather than deleting this check")
        want = _DOOR_HOOK_CALLS[(rel, function)]
        got = _hook_calls(node, callee)
        if got < want:
            missing.append(f"{rel}::{function} calls {callee}× (want {want})")
    assert not missing, (
        "these creation doors no longer call the record-cursor hook: "
        f"{missing}. A graph they create is born UNCUT — every record it writes "
        "is refused `cursor_not_active` while `write_live_record` (fail-soft by "
        "contract) reports success, so nothing reds and the loss only shows at a "
        "rebuild")


def test_the_door_helpers_reach_the_writers_hook():
    """The door's callee must be a call to the REAL hook, not a stub."""
    missing = []
    for rel, helper in _HELPERS:
        node = _functions(rel).get(helper)
        assert node is not None, f"{rel}::{helper} is gone but a door calls it"
        if _hook_calls(node, "provision_graph_record") < 1:
            missing.append(f"{rel}::{helper}")
    assert not missing, (
        f"these helpers no longer reach `record_writer.provision_graph_record`: "
        f"{missing} — the doors would call a function that provisions nothing")


def test_the_doors_that_cannot_be_hooked_stay_unhooked():
    """⛔ AN UNPINNED ABSENCE IS A FUTURE "FIX". Both of these are FINDINGS:
    hooking them manufactures a permanent false red on `/health` for a deployment
    that has no gap (see the module docstring's F1/F3).
    """
    hooked = []
    for rel, function in _UNHOOKED:
        node = _functions(rel).get(function)
        assert node is not None, f"{rel}::{function} is gone — re-run the census"
        if _hook_calls(node, "provision_graph_record") or _hook_calls(
                node, "provision_graph_record_"):
            hooked.append(f"{rel}::{function}")
    assert not hooked, (
        f"{hooked} now calls the creation hook, and it must not: its Supabase "
        "arm persists no graph row (so the cursor row would be seeded for an id "
        "that never exists), and its registry arm creates a graph "
        "`public.graphs` never holds — `record_cursor`'s FK refuses every one, "
        "so each create logs a warning and the deployment reads "
        "`record.armed_but_uncut` forever. If this door is to be hooked, the "
        "graph-row mint has to land first")


def test_the_hook_arms_and_does_not_only_seed():
    """⛔ THE PLAN'S M16 FALSIFIER, AS A PIN (`§A7.8` item 8): *"a hook that only
    seeds writes `DEFAULT 'seeded'`, which allocates nothing, and the test must
    go red on it"*.

    The live leg below proves the behaviour; this proves the MECHANISM, because a
    `seed`-only hook still writes a row and still looks like progress.
    """
    node = _functions("tortoise/record_allocator.py").get(
        "provision_graph_record")
    assert node is not None, "record_allocator.provision_graph_record is gone"
    called = _call_names(node)
    assert "seed" in called, "the hook must reuse the CLI's own seed (SEED_SQL)"
    assert "arm" in called, (
        "the hook never ARMS: a `seeded` row allocates nothing (`ALLOCATION_SQL` "
        "is scoped to `active`), so the graph's first record write still refuses "
        "`cursor_not_active` — the defect this hook closes")


def test_the_uncut_registry_is_bounded_and_counts_past_the_cap(monkeypatch):
    rw.reset_uncut_graphs()
    try:
        for i in range(rw.UNCUT_GRAPH_CAP + 8):
            rw.note_uncut_graph("org-14582", f"graph-{i}", reason="no_cursor_row")
        report = rw.armed_but_uncut()
        assert report["armed_but_uncut"] == rw.UNCUT_GRAPH_CAP
        assert report["armed_but_uncut_untracked_observations"] == 8, (
            "past the cap the observation must still be COUNTED — a bounded map "
            "that silently stops counting is a bound that hides the overflow")
        assert rw.armed_but_uncut_reasons() == {"no_cursor_row": rw.UNCUT_GRAPH_CAP}, (
            "the reasons key is DISTINCT GRAPHS behind `armed_but_uncut` — it must "
            "sum to that count, so a graph healed out of it retracts its reason "
            "too. The lifetime observation total is "
            "`armed_but_uncut_observations`.")
    finally:
        rw.reset_uncut_graphs()


def test_the_registry_publishes_no_tenant_identifiers():
    """`/health` is UNAUTHENTICATED: a graph id is the customer's own name."""
    rw.reset_uncut_graphs()
    try:
        rw.note_uncut_graph("org-SECRET", "graph-SECRET", reason="no_cursor_row")
        report = rw.armed_but_uncut()
        reasons = rw.armed_but_uncut_reasons()
    finally:
        rw.reset_uncut_graphs()

    def leaves(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from leaves(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from leaves(item)
        else:
            yield value

    published = set(leaves(report)) | set(leaves(reasons))
    assert "org-SECRET" not in published
    assert "graph-SECRET" not in published
    assert all(isinstance(v, (int, str)) for v in published)


def test_the_declared_export_surface_is_not_silently_shrunk():
    """⛔ ADDING TO `__all__` MUST NOT DROP FROM IT (#14582 review P2-8).

    This change added `provision_graph_record` / `note_uncut_graph` /
    `armed_but_uncut` to `__all__` and, in the same edit, dropped
    `current_record_tenant` — the SCOPE reader three suites and every
    scope-aware caller import by name (``tortoise/record_writer.py`` still
    defines it; only its declaration was cut).

    ⛔ NOTHING REDDED, and that is why this pin exists: a by-name import
    (``from tortoise.record_writer import current_record_tenant``) bypasses
    `__all__` entirely, so the loss is visible only to `import *` and to the
    operator tooling that reads the module's declared surface BY NAME — in
    production, after the release. Re-adding a name here is free; shrinking the
    surface is a red that has to be argued for.
    """
    assert "current_record_tenant" in rw.__all__, (
        "`current_record_tenant` is no longer in `record_writer.__all__` — it is "
        "defined in the module and imported by name by the record suites, so this "
        "is a silent SHRINK of the declared public surface, not a rename")
    unresolved = [name for name in rw.__all__ if not hasattr(rw, name)]
    assert not unresolved, (
        f"`__all__` declares names the module does not define: {unresolved} — an "
        f"`import *` would raise AttributeError, and a declared-but-absent name "
        f"reads as an export in any tool that trusts the list")


# ── DB-free: /health cannot hide an uncut armed graph ──────────────────────

def _health(monkeypatch, *, dsn: str | None):
    from tortoise import hosted_api

    if dsn is None:
        monkeypatch.delenv(rw.RECORD_DSN_ENV, raising=False)
    else:
        monkeypatch.setenv(rw.RECORD_DSN_ENV, dsn)
    return hosted_api._record_health()


def test_health_is_ok_on_an_unarmed_deployment_that_has_seen_nothing(monkeypatch):
    rw.reset_uncut_graphs()
    try:
        block = _health(monkeypatch, dsn=None)
        assert block["armed"] is False
        assert block["armed_but_uncut"] == 0
        assert block["ok"] is True, (
            "an unarmed deployment CANNOT have an uncut graph — with no DSN "
            "there is no record for a graph to be missing a row in, so this "
            "must not red a deployment that runs without a record at all")
    finally:
        rw.reset_uncut_graphs()


def test_health_cannot_report_ok_with_an_uncut_armed_graph(monkeypatch):
    """FALSIFIER (c). Red before this change: `armed_but_uncut` did not exist and
    `ok` stayed `true` for exactly this state — production's measured shape."""
    rw.reset_uncut_graphs()
    try:
        block = _health(monkeypatch, dsn="postgresql://u:p@127.0.0.1:1/record")
        assert block["armed"] is True
        assert block["ok"] is True, "the control: nothing is uncut yet"
        rw.note_uncut_graph("org-14582", "graph-14582",
                            reason="no_cursor_row")
        block = _health(monkeypatch, dsn="postgresql://u:p@127.0.0.1:1/record")
        assert block["armed"] is True
        assert block["armed_but_uncut"] == 1
        assert block["armed_but_uncut_reasons"] == {"no_cursor_row": 1}
        assert block["ok"] is False, (
            "an armed deployment carrying an uncut graph is a DEPLOYMENT "
            "problem: every record that graph writes is refused while "
            "`write_live_record` reports success, so an `ok: true` here is the "
            "silence this field exists to end")
        # `degraded` keeps its documented window meaning (#10185) — it is not
        # re-used as a second, weaker statement of the same state.
        assert block["degraded"] is False
    finally:
        rw.reset_uncut_graphs()


def test_health_clears_the_gap_when_the_graph_is_healed(monkeypatch):
    """⛔ THE FIELD MUST BE ABLE TO SAY "FIXED" (#14582 review P1-2).

    Every entry is fed from an observation, so the observation that shows the row
    is now `active` must REMOVE it. A registry that could only be added to would
    pin `/health` non-`ok` for the life of the process over a graph an operator
    healed an hour ago — and then the field that this PR relies on as the
    compensation for best-effort provisioning would itself be an unfalsifiable
    red, indistinguishable from a real one.
    """
    rw.reset_uncut_graphs()
    try:
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        rw.note_uncut_graph("org-14582", "graph-a", reason="no_cursor_row")
        assert _health(monkeypatch, dsn=dsn)["ok"] is False
        rw.clear_uncut_graph("org-14582", "graph-a")
        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_uncut"] == 0
        assert block["ok"] is True, (
            "the registry is a LATCH — a healed graph must stop reddening the "
            "deployment, or the only way to clear it is a restart")
    finally:
        rw.reset_uncut_graphs()


def test_a_halted_graph_is_reported_but_does_not_red_the_deployment(monkeypatch):
    """⛔ `halted` IS NOT `uncut` (#14582 review P1-3).

    `halt` is the DOCUMENTED remedy an operator runs when the record holds a
    duplicate position, and in that mode `record_owns_position` returns False, so
    `live_write.write` KEEPS the JSONL append — that graph loses nothing. Folding
    it into `armed_but_uncut` would report a sanctioned action as a deployment
    defect, and a halt does not clear on its own, so the red would be permanent
    and indistinguishable from a real one. It is still PUBLISHED, on its own key.
    """
    rw.reset_uncut_graphs()
    try:
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        rw.note_uncut_graph("org-14582", "graph-halted", reason="mode_halted")
        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_halted"] == 1, (
            "a halted graph must still be VISIBLE — the point is that it is not "
            "filed as a gap, not that it is hidden")
        assert block["armed_but_uncut"] == 0
        assert "mode_halted" not in block["armed_but_uncut_reasons"]
        assert block["ok"] is True, (
            "a halted graph keeps its journal append, so an `ok: false` here "
            "would punish the operator for following the documented remedy")
    finally:
        rw.reset_uncut_graphs()


def test_health_returns_to_ok_after_every_overflowing_graph_is_healed(
        monkeypatch):
    """⛔ #14587 review P1 — THE OVERFLOW COUNTER WAS A LATCH IN THE `ok` FOLD.

    `armed_but_uncut_untracked_observations` was monotone: past
    `UNCUT_GRAPH_CAP` it only ever grew, `clear_uncut_graph` never touched it, and
    `reset_uncut_graphs` has no runtime caller. `_record_health` sums it into
    `ok`, so a deployment whose operator healed EVERY uncut graph — exactly what
    `record_allocator_cli.py backfill` does — still read `ok: false` until a
    process restart, and the restart then reported `ok: true` regardless of the
    real state. The signal was inverted at the one moment an operator would
    trust it.

    Red before this change: `ok` stayed False and `reasons` still named 33
    graphs while `armed_but_uncut` said 0.
    """
    rw.reset_uncut_graphs()
    try:
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        over = rw.UNCUT_GRAPH_CAP + 1
        for i in range(over):
            rw.note_uncut_graph("org-14582", f"graph-{i}", reason="no_cursor_row")
        before = _health(monkeypatch, dsn=dsn)
        assert before["armed_but_uncut"] == rw.UNCUT_GRAPH_CAP
        assert before["armed_but_uncut_untracked_observations"] == 1
        assert before["ok"] is False, "the control: the gap is real right now"

        # The operator runs the remedy: every graph is healed.
        for i in range(over):
            rw.clear_uncut_graph("org-14582", f"graph-{i}")

        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_uncut"] == 0
        assert block["armed_but_uncut_untracked_observations"] == 0, (
            "the overflow backlog must be RETRACTED by the heal that restores "
            "room — from then on any graph still uncut is tracked again, so a "
            "kept count is a latch that pins `ok: false` forever")
        assert block["armed_but_uncut_reasons"] == {}, (
            "a payload that says `armed_but_uncut: 0` beside "
            "`{'no_cursor_row': 33}` contradicts itself and has no repair path")
        assert block["ok"] is True, (
            "NOTHING is uncut any more: the deployment is healthy, and the only "
            "way out of `ok: false` must not be a restart")
        # The lifetime diagnostic is retained — it is simply not part of `ok`.
        assert block["armed_but_uncut_observations"] == over
    finally:
        rw.reset_uncut_graphs()


def test_a_partial_heal_of_an_overflowing_registry_stays_red(monkeypatch):
    """The other direction: retracting the backlog must not clear a real gap."""
    rw.reset_uncut_graphs()
    try:
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        for i in range(rw.UNCUT_GRAPH_CAP + 1):
            rw.note_uncut_graph("org-14582", f"graph-{i}", reason="no_cursor_row")
        rw.clear_uncut_graph("org-14582", "graph-0")
        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_uncut"] == rw.UNCUT_GRAPH_CAP - 1
        assert block["armed_but_uncut_untracked_observations"] == 0
        assert block["ok"] is False, (
            "32 graphs are still uncut — the overflow retraction must never be "
            "read as `the gap is gone`")
    finally:
        rw.reset_uncut_graphs()


def test_the_uncut_reasons_always_sum_to_the_uncut_count():
    """The invariant an operator can check on the payload (#14587 review P2)."""
    rw.reset_uncut_graphs()
    try:
        rw.note_uncut_graph("org-14582", "graph-a", reason="no_cursor_row")
        rw.note_uncut_graph("org-14582", "graph-b", reason="provision_failed")
        # Re-observing an already-tracked graph REPLACES its reason; it must not
        # inflate the breakdown, which is per GRAPH and not per observation.
        rw.note_uncut_graph("org-14582", "graph-b", reason="commit_failed")
        report = rw.armed_but_uncut()
        reasons = rw.armed_but_uncut_reasons()
        assert reasons == {"no_cursor_row": 1, "commit_failed": 1}
        assert sum(reasons.values()) == report["armed_but_uncut"] == 2
        rw.clear_uncut_graph("org-14582", "graph-a")
        assert rw.armed_but_uncut_reasons() == {"commit_failed": 1}
        assert sum(rw.armed_but_uncut_reasons().values()) \
            == rw.armed_but_uncut()["armed_but_uncut"]
    finally:
        rw.reset_uncut_graphs()


def test_a_halted_overflow_is_counted_not_silently_dropped(monkeypatch):
    """⛔ #14587 review P2 — the halted arm evicted with no counter.

    `armed_but_halted`'s contract (and the registry comment above
    `UNCUT_GRAPH_CAP`) is that past the cap the observation is still COUNTED —
    "nothing is lost, only the distinctness". The uncut arm honoured that; the
    halted arm popped the oldest with no record, so past 32 halted graphs the
    population silently shrank to the cap on `/health`.
    """
    rw.reset_uncut_graphs()
    try:
        over = rw.UNCUT_GRAPH_CAP + 5
        for i in range(over):
            rw.note_uncut_graph("org-14582", f"halted-{i}", reason="mode_halted")
        report = rw.armed_but_uncut()
        assert report["armed_but_halted"] == rw.UNCUT_GRAPH_CAP
        assert report["armed_but_halted_untracked_observations"] == 5, (
            "each evicted halted graph must be COUNTED — a bound that drops "
            "observations silently hides exactly the population it claims to "
            "publish")
        block = _health(monkeypatch, dsn="postgresql://u:p@127.0.0.1:1/record")
        assert block["armed_but_halted_untracked_observations"] == 5, (
            "the overflow must reach `/health`, or an operator reading the "
            "payload sees a capped number and no hint it was capped")
        assert block["ok"] is True, (
            "a halted population still keeps its journal append — counting it "
            "must not smuggle it into the `ok` fold")
        # Re-observing an already-tracked halted graph is not an eviction.
        rw.note_uncut_graph("org-14582", f"halted-{over - 1}", reason="mode_halted")
        assert rw.armed_but_uncut()[
            "armed_but_halted_untracked_observations"] == 5
    finally:
        rw.reset_uncut_graphs()


# ── the live legs: a real record database ──────────────────────────────────

pytestmark_live = pytest.mark.skipif(
    not _HAS_RECORD_DB,
    reason="TEST_RECORD_DB_URI not set — no Postgres carrying the record "
           "migrations (the `test-record-postgres` job arms these legs)")


@pytest.fixture(scope="module")
def record_schema():
    """The record schema + the byte meter, applied ONCE for this module.

    The order is load-bearing (the byte meter's EXECUTE grant lives in a step
    that must run AFTER the metering migration creates the function), and
    re-applying it per test takes an ACCESS EXCLUSIVE lock on
    `public.record_cursor` — the #12399 deadlock when a pooled handle from the
    previous test still holds a share lock.
    """
    pytest.importorskip("psycopg2")
    from tests.test_9078_record_written_by_production_path import (
        _ensure_metering_schema,
        _ensure_record_schema,
        _record_db,
    )

    conn = _record_db()
    _ensure_record_schema(conn)      # the base shape the metering files depend on
    _ensure_metering_schema(conn)    # …14… — the meter's function comes into being
    _ensure_record_schema(conn)      # …16…+`…02…` — the GRANT step, re-applied
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


@pytest.fixture(autouse=True)
def _release_pooled_handles():
    """Hand back every idle record connection a test's writes opened (#9558)."""
    yield
    rw._release_pooled_connections()


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is PROCESS state; a leg that leaves it dirty biases the next."""
    rw.reset_uncut_graphs()
    yield
    rw.reset_uncut_graphs()


def _app_dsn() -> str:
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    return _app_role_dsn(os.environ["TEST_RECORD_DB_URI"])


@pytest.fixture()
def armed(monkeypatch):
    """The deployment ARMED, as production is: a record DSN (app role) + the flag."""
    monkeypatch.setenv(rw.RECORD_DSN_ENV, _app_dsn())
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    yield


@pytest.fixture()
def fresh_graph(record_schema):
    """A tenant pair the record has NEVER seen — no `record_cursor` row.

    This is the state every graph created after the cutover was in: a `graphs`
    row exists, the record knows nothing, and the armed door refuses.
    """
    token = f"{os.getpid()}-{uuid.uuid4().hex[:10]}"
    org, graph = f"org-14582-{token}", f"graph-14582-{token}"
    with record_schema.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (org, org))
        cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                    "VALUES (%s, %s, %s)", (graph, org, graph))
    record_schema.commit()
    return SimpleNamespace(org=org, graph=graph, admin=record_schema)


def _counter(conn, org, graph):
    with conn.cursor() as cur:
        cur.execute("SELECT seq, mode FROM public.record_cursor "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph))
        return cur.fetchone()


def _positions(conn, org, graph):
    with conn.cursor() as cur:
        cur.execute("SELECT seq FROM public.journal_event WHERE org_id = %s "
                    "AND graph_id = %s ORDER BY seq", (org, graph))
        return [row[0] for row in cur.fetchall()]


@pytestmark_live
def test_the_hook_gives_a_new_graph_a_row_and_its_first_write_applies(
        armed, fresh_graph):
    """FALSIFIER (a) — the whole point, end to end.

    RED before this change: the graph had no cursor row, so the armed door's
    allocation matched zero rows and `write_live_record` came back
    `outcome=REFUSED refusal="write_failed"` (the allocator's
    `cursor_not_active`) — counted and WARNed, never raised, so the write was
    lost from the record and nothing reddened.
    """
    org, graph = fresh_graph.org, fresh_graph.graph
    assert _counter(fresh_graph.admin, org, graph) is None, (
        "the premise of this leg is a graph the record has never seen")

    result = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert result["provisioned"] is True, result
    assert result["mode"] == "active", (
        f"the row must be born `active` (the plan's M16 falsifier), got "
        f"{result['mode']!r} — a `seeded` row allocates nothing, so the first "
        f"write below would still refuse `cursor_not_active`")
    assert result["seq"] == 0, "an empty journal seeds to 0, not 1"
    assert _counter(fresh_graph.admin, org, graph) == (0, "active")

    write = rw.write_live_record(RECORD, seq=None, journal_skipped=True,
                                 tenant=(org, graph))
    assert write.outcome.value == "applied", (
        f"the graph's FIRST record write must apply on an armed deployment once "
        f"the hook has run; got outcome={write.outcome!r} "
        f"refusal={getattr(write, 'refusal', None)!r}")
    assert (write.first_seq, write.last_seq) == (1, 1), (
        f"the first allocated position must be 1, got "
        f"{write.first_seq}..{write.last_seq}")
    assert _positions(fresh_graph.admin, org, graph) == [1], (
        "the position row must carry the number the counter issued")
    assert _counter(fresh_graph.admin, org, graph) == (1, "active")


@pytestmark_live
def test_the_hook_is_idempotent_and_never_re_bases_an_armed_graph(
        armed, fresh_graph):
    """A door can be RE-ENTERED (a retry, the default graph's self-heal), so the
    second call must be a no-op rather than a refusal — while never moving a
    counter that is already allocating."""
    org, graph = fresh_graph.org, fresh_graph.graph
    first = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert first["provisioned"] is True and first["seeded"] is True
    rw.write_live_record(RECORD, seq=None, journal_skipped=True,
                         tenant=(org, graph))
    assert _counter(fresh_graph.admin, org, graph) == (1, "active")

    again = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert again["provisioned"] is True, again
    assert again["already_cut_over"] is True
    assert _counter(fresh_graph.admin, org, graph) == (1, "active"), (
        "a re-entered hook must not re-base a live allocator")


@pytestmark_live
def test_a_graph_the_record_already_knows_is_refused_not_seeded(
        armed, fresh_graph):
    """FALSIFIER (b) — `#12894`'s divergence rule, decided by `SEED_SQL` itself.

    DIVERGENCE IS A GAP, not merely an existing journal: a graph whose position
    axis is `1, 2, 4` enumerates THREE positions while the record holds FOUR, so
    the record claims a position a replay of this journal cannot reproduce. The
    hook must REFUSE and leave no row — the graph keeps whatever allocator it had
    instead of being silently re-based onto a number nobody can derive.

    ⛔ A DENSE PRE-EXISTING JOURNAL IS **NOT** THIS CASE, and conflating them was
    the review's P2-6: `1, 2, 3` is a graph the self-heal must HEAL (seed to 3),
    because the count equals the max. The two are separated by `SEED_SQL`'s own
    `MAX(seq) <= enumerated_floor` guard, not by a second predicate here.
    """
    org, graph = fresh_graph.org, fresh_graph.graph
    with fresh_graph.admin.cursor() as cur:
        for seq in (1, 2, 4):
            cur.execute(
                "INSERT INTO public.journal_event "
                "(org_id, graph_id, seq, event_type, payload) "
                "VALUES (%s, %s, %s, 'PointAdded', %s)",
                (org, graph, seq, '{"restored": true}'))
    fresh_graph.admin.commit()

    from tortoise import record_allocator as ra

    # The ALLOCATOR refuses (this is the divergence rule itself) — both with the
    # DERIVED floor (3, the journal's count) and with an explicit 0, which is what
    # a door claiming "this graph is new" states. Neither number seeds over 4.
    for floor in (None, 0):
        with pytest.raises(ra.SeedRefused) as refused:
            ra.provision_graph_record(_conn(), org_id=org, graph_id=graph,
                                      enumerated_floor=floor)
        assert refused.value.reason == "divergence", refused.value
    # ... and the DOOR reports it without raising, because a graph creation must
    # not fail on a record fault (the fail-soft contract this hook lives under).
    reported = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert reported["provisioned"] is False
    assert reported["reason"] == "refused:divergence"
    assert _counter(fresh_graph.admin, org, graph) is None, (
        "a REFUSED graph must have NO counter row — a row here would arm an "
        "allocator over positions the journal cannot reproduce")
    assert rw.armed_but_uncut()["armed_but_uncut"] == 1, (
        "a refused graph is UNCUT, so it must be published")


@pytestmark_live
def test_the_door_helpers_provision_the_graph_they_just_created(
        armed, fresh_graph):
    """The DOORS' own call, executed for real.

    `hosted_api._provision_graph_record` and
    `supabase_control._provision_default_graph_record` are the one line each door
    adds; driving them (rather than the seam directly) is what proves the door's
    call — not just the function behind it — carries the right ids.
    """
    from tortoise import hosted_api, supabase_control

    # `record_cursor` carries a FOREIGN KEY to `public.graphs` (measured), so the
    # door's call can only ever provision a graph whose row exists — which is the
    # correct direction: a cursor row for a graph that does not exist would be a
    # phantom (see the census finding F1 on `_graph_create`'s Supabase arm).
    hosted_api._provision_graph_record(fresh_graph.org, fresh_graph.graph)
    assert _counter(fresh_graph.admin, fresh_graph.org,
                    fresh_graph.graph) == (0, "active"), (
        "the custom-graph door's call left no cursor row")

    # The default-graph mint: `ensure_default_graph_row` with the control-plane
    # transport faked (the function itself is real, including its guards).
    default_org = fresh_graph.org + "-default"
    # The mint's own precondition: `graphs.org_id REFERENCES public.organizations`
    # (the column was created as `team_id`), so the org row must exist first —
    # exactly as `provision_org` orders it in production (its RPC commits, THEN
    # the default row is minted; #10025 F1's reason for being best-effort).
    with fresh_graph.admin.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (default_org, default_org))
    fresh_graph.admin.commit()
    row = supabase_control.ensure_default_graph_row(
        _FakeControlPlane(fresh_graph.admin), default_org,
        "team_" + default_org)
    assert isinstance(row, str) and row
    assert _counter(fresh_graph.admin, default_org, row) == (0, "active"), (
        "the default-graph door's call left no cursor row — this is the org "
        "whose EVERY record write is refused `no_tenant`/`cursor_not_active` "
        "until it is seeded (#10025's forward half)")


class _FakeControlPlane:
    """Enough of `supabase_control`'s control plane to mint a default graph row.

    ⛔ ONLY THE TRANSPORT IS FAKE, AND THE INSERT IS REAL. `ensure_default_graph_row`
    runs its real guards, its real `insert_graph`, and its real hook — and the
    POST is forwarded to the real `public.graphs` table, because
    `record_cursor` carries a FOREIGN KEY to it (`record_cursor_org_id_graph_id_
    fkey`, measured): a purely in-memory mint would have the hook refused by the
    database and the leg would prove the FK rather than the hook.
    """

    def __init__(self, admin):
        self.admin = admin
        self.graphs: list[dict] = []
        self.orgs = {"graph_name": None}

    def query(self, table, *, select=None, filters=None, method=None,
              json_body=None):
        filters = filters or []
        if table == "graphs" and method == "POST":
            row = dict(json_body or {})
            # ⛔ THE IN-MEMORY ROW IS APPENDED **AFTER** THE REAL INSERT SUCCEEDS.
            # The other order lets a `query` that RAISED still be adopted by the
            # mint's convergent-read arm — i.e. a fake that reports a row the
            # database refused, which would let this leg pass on a mint that
            # never happened.
            with self.admin.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.graphs (id, org_id, name) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (row["id"], row["org_id"], row.get("name")))
            self.admin.commit()
            self.graphs.append(row)
            return None
        if table == "graphs":
            rows = list(self.graphs)
            for column, op, value in filters:
                if op == "eq":
                    rows = [r for r in rows if r.get(column) == value]
                elif op == "neq":
                    rows = [r for r in rows if r.get(column) != value]
            return [{key: r.get(key) for key in (select or r)} for r in rows]
        if table == "organizations":
            return [{"graph_name": self.orgs["graph_name"]}]
        raise AssertionError(f"unexpected control-plane read: {table}")


# ── the backfill ───────────────────────────────────────────────────────────

@pytestmark_live
def test_the_backfill_reports_its_three_counts_and_is_idempotent(
        armed, record_schema):
    """`N considered / M seeded / K refused`, and a re-run is a no-op."""
    from tools import record_allocator_cli as cli

    org, graph = _uncut_graph(record_schema, "backfill", positions=(1, 2, 3))
    args = _backfill_args(org)

    dry = cli._cmd_backfill(_conn(), _backfill_args(org, dry_run=True))
    assert (dry["considered"], dry["seeded"], dry["refused"]) == (1, 1, 0), dry
    assert dry["graphs"][0]["outcome"] == "would_seed"
    assert _counter(record_schema, org, graph) is None, (
        "--dry-run wrote a row: the one thing it promises is that it does not")

    real = cli._cmd_backfill(_conn(), args)
    assert (real["considered"], real["seeded"], real["refused"]) == (1, 1, 0)
    assert _counter(record_schema, org, graph) == (3, "seeded"), (
        "a graph with 3 position rows must seed to 3 — its next write is 4")

    # ⛔ IDEMPOTENCE IS MEASURED AS "NOTHING LEFT TO DO", not as "the same three
    # counts again": the population is UNCUT graphs, so a graph the first run
    # seeded has left it. A second run that reported it again would be a verb
    # that cannot tell a healed graph from a broken one.
    again = cli._cmd_backfill(_conn(), args)
    assert (again["considered"], again["seeded"], again["refused"]) == (0, 0, 0), again
    assert _counter(record_schema, org, graph) == (3, "seeded"), (
        "the backfill must be IDEMPOTENT: a second run may not move the counter")


@pytestmark_live
def test_the_backfill_heals_every_graph_of_an_org_not_just_the_first(
        armed, record_schema):
    """⛔ ONE ORG, THREE UNCUT GRAPHS — ONE RUN HEALS ALL THREE (#14582 review P1-1).

    `scope_tenant` sets a TRANSACTION-LOCAL GUC (`set_config(..., true)`), and
    `journal_event` is RLS-protected on it, so the per-graph `commit()` ends the
    transaction the scope lived in. A loop that scopes ONCE reads every graph
    after the first under NO tenant — RLS answers ZERO rows — reports
    `journal_count: 0`, and is then REFUSED `divergence` by `seed` (which
    re-scopes and sees the truth), with a printed remedy (`--enumerated-floor 0`)
    that the same guard refuses. Measured shape before the fix: only the FIRST
    graph healed per run, and the rest were falsely accused.

    Two graphs of the SAME org is the whole reproduction; three makes the ordering
    explicit (`UNCUT_GRAPHS_SQL` is `ORDER BY graph_id`).
    """
    from tools import record_allocator_cli as cli

    org = f"org-14582-multi-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    graphs = []
    with record_schema.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (org, org))
        for index in range(3):
            graph = f"graph-14582-multi-{index}-{uuid.uuid4().hex[:8]}"
            cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                        "VALUES (%s, %s, %s)", (graph, org, graph))
            for seq in (1, 2):       # the same dense journal for each
                cur.execute(
                    "INSERT INTO public.journal_event "
                    "(org_id, graph_id, seq, event_type, payload) "
                    "VALUES (%s, %s, %s, 'PointAdded', %s)",
                    (org, graph, seq, '{"multi": true}'))
            graphs.append(graph)
    record_schema.commit()

    payload = cli._cmd_backfill(_conn(), _backfill_args(org))
    assert (payload["considered"], payload["seeded"], payload["refused"]) == (3, 3, 0), (
        "every uncut graph of the org must be seeded in ONE run; a refusal here "
        f"means the loop lost the tenant scope: {payload['refusals']}")
    for graph in graphs:
        assert _counter(record_schema, org, graph) == (2, "seeded"), (
            f"graph {graph} was considered but left uncut")
    assert not payload["refusals"]


@pytestmark_live
def test_the_self_heal_arm_reconciles_a_graph_that_predates_the_hook(
        armed, fresh_graph):
    """⛔ THE FLOOR IS DERIVED, SO A PRE-EXISTING GRAPH IS HEALED, NOT ACCUSED
    (#14582 review P2-6).

    The default-row self-heal runs for a graph that ALREADY EXISTS and may carry a
    journal this hook never accounted for. A hard-coded `enumerated_floor=0` makes
    `SEED_SQL`'s guard (`MAX(seq) <= floor`) refuse it as `divergence`, with text
    claiming the record holds positions the journal cannot reproduce — FALSE for a
    healthy pre-existing graph whose journal holds exactly those positions. So the
    hook reads the journal's COUNT itself, and a dense `1..N` seeds to `N`.
    """
    org, graph = fresh_graph.org, fresh_graph.graph
    with fresh_graph.admin.cursor() as cur:
        for seq in (1, 2, 3):
            cur.execute(
                "INSERT INTO public.journal_event "
                "(org_id, graph_id, seq, event_type, payload) "
                "VALUES (%s, %s, %s, 'PointAdded', %s)",
                (org, graph, seq, '{"predates": true}'))
    fresh_graph.admin.commit()

    result = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert result["provisioned"] is True, result
    assert result["seq"] == 3, (
        f"a graph with journal positions 1..3 must seed to 3 (its next write is "
        f"4), got {result['seq']} — a floor of 0 refuses it instead")
    assert _counter(fresh_graph.admin, org, graph) == (3, "active")


@pytestmark_live
def test_the_backfill_refuses_a_gapped_graph_with_its_reason(armed, record_schema):
    """A gap in the position axis (`COUNT(*) < MAX(seq)`) is a REFUSAL with both
    numbers and a working remedy — never a silent pick between them."""
    from tools import record_allocator_cli as cli

    org, graph = _uncut_graph(record_schema, "gap", positions=(1, 2, 4))
    payload = cli._cmd_backfill(_conn(), _backfill_args(org))
    assert (payload["considered"], payload["seeded"], payload["refused"]) == (1, 0, 1)
    refusal = payload["refusals"][0]
    assert refusal["reason"] == "divergence", refusal
    assert refusal["journal_count"] == 3 and refusal["journal_max_seq"] == 4
    assert refusal["remedy"] == (
        f"seed --org-id {org} --graph-id {graph} --enumerated-floor 4")
    assert _counter(record_schema, org, graph) is None


def _uncut_graph(conn, label: str, *, positions=()):
    """A tenant with position ROWS and no counter row — `sweep`'s population."""
    token = f"{os.getpid()}-{uuid.uuid4().hex[:10]}"
    org, graph = f"org-14582-{label}-{token}", f"graph-14582-{label}-{token}"
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (org, org))
        cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                    "VALUES (%s, %s, %s)", (graph, org, graph))
        for seq in positions:
            cur.execute(
                "INSERT INTO public.journal_event "
                "(org_id, graph_id, seq, event_type, payload) "
                "VALUES (%s, %s, %s, 'PointAdded', %s)",
                (org, graph, seq, '{"backfill": true}'))
    conn.commit()
    return org, graph


def _backfill_args(org: str, *, dry_run: bool = False):
    return SimpleNamespace(org_id=org, command="backfill", graph_id=None,
                           dsn=_app_dsn(), floor=None, journal=None,
                           dry_run=dry_run, arm_graphs=False,
                           acknowledge_cutover=False, as_json=False)


def _conn():
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(_app_dsn())
    conn.autocommit = False
    return conn
