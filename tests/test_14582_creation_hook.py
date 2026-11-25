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
#: yields an id from THREE arms (active-row short-circuit, mint, convergent adopt)
#: and hooks each; a refactor that collapses them to one silently drops two. ⛔ The
#: short-circuit arm is reached on a MISS/race or a RE-PROVISIONING, not on the
#: credential resolution of an org whose row exists (#14610 review F3).
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


def _hook_calls_named_like(node: ast.AST, stem: str) -> int:
    """Calls in `node`'s body whose callee identifier CONTAINS `stem`.

    ⛔ A REAL SECOND SPELLING, not an inert disjunct (#14610 review F5). The
    "must stay unhooked" pin used to read

        _hook_calls(node, "provision_graph_record") or _hook_calls(
            node, "provision_graph_record_")

    but `_hook_calls` matches the callee by EXACT equality and no function in
    the tree is named `provision_graph_record_`, so the second term was `0`
    under every possible mutation: the guard was the first term alone while
    reading as though a second spelling were covered. This matches the way a
    later edit actually reaches the hook — a WRAPPER carrying the hook's name
    inside its own (`_provision_graph_record_quietly`) or any
    `module.provision_graph_record`. Structural, so a comment or a docstring
    cannot satisfy it.
    """
    count = 0
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name is not None and stem in name:
            count += 1
    return count


def _calls_with_enclosing_tests(
    node: ast.AST, callee: str,
) -> list[tuple[ast.Call, list[ast.AST]]]:
    """Every call to `callee` in `node`'s body, with the `if` tests above it.

    `ast.walk` yields no parents, so the branch a call sits inside is invisible
    to it — and a call's BRANCH is exactly what the creation-hook pin has to
    judge (#14610 review F2).
    """
    found: list[tuple[ast.Call, list[ast.AST]]] = []

    def walk(parent: ast.AST, guards: list[ast.AST]) -> None:
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, ast.Call):
                func = child.func
                name = func.id if isinstance(func, ast.Name) else (
                    func.attr if isinstance(func, ast.Attribute) else None)
                if name == callee:
                    found.append((child, list(guards)))
            walk(child, [*guards, child.test] if isinstance(child, ast.If) else guards)

    walk(node, [])
    return found


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
        if _hook_calls_named_like(node, "provision_graph_record"):
            hooked.append(f"{rel}::{function}")
    assert not hooked, (
        f"{hooked} now calls the creation hook, and it must not: its Supabase "
        "arm persists no graph row (so the cursor row would be seeded for an id "
        "that never exists), and its registry arm creates a graph "
        "`public.graphs` never holds — `record_cursor`'s FK refuses every one, "
        "so each create logs a warning and the deployment reads "
        "`record.armed_but_uncut` forever. If this door is to be hooked, the "
        "graph-row mint has to land first")


def test_the_unhooked_door_pin_sees_a_wrapper_spelling():
    """⛔ F5 (#14610 review): the second term of the unhooked-door guard is LIVE.

    The guard used to be `_hook_calls(node, "provision_graph_record") or
    _hook_calls(node, "provision_graph_record_")` — and because `_hook_calls`
    matches the callee identifier by EXACT equality, and no function in the tree
    is named `provision_graph_record_`, the second term could not vary under any
    mutation. The guard was the first term alone while reading as two.

    Exercised directly, because a spelling the detector cannot see is a spelling
    a later edit can use to put the hook back into an unhooked door. A WRAPPER is
    that edit's natural shape (it is how `hosted_api._provision_graph_record`
    already reaches the writer's hook), so both a direct and a wrapped callee
    must be seen, and an unrelated `provision_graph` call must not be.
    """
    direct = ast.parse(
        "def f(org, graph):\n    rw.provision_graph_record(org, graph)\n")
    wrapped = ast.parse(
        "def f(org, graph):\n    _provision_graph_record_quietly(org, graph)\n")
    unrelated = ast.parse("def f(org, graph):\n    provision_graph(org, graph)\n")
    assert _hook_calls_named_like(direct, "provision_graph_record") == 1
    assert _hook_calls_named_like(wrapped, "provision_graph_record") == 1, (
        "a wrapper spelling is exactly how the hook returns to an unhooked door — "
        "the detector must see it, or this term is the inert disjunct again")
    assert _hook_calls_named_like(unrelated, "provision_graph_record") == 0, (
        "a detector that fires on `provision_graph` would red a legitimate edit")


def test_the_hook_is_taken_on_the_control_plane_arm_only():
    """⛔ F2 (#14610 review): the REGISTRY arm of `_provision_graph` must not be hooked.

    `_provision_graph` mints the graph on one of two arms. The hook seeds a
    `record_cursor` row whose key is `(org_id, graph_id) REFERENCES
    public.graphs`, and the REGISTRY arm's graph (`sdk._graph_create`) is never
    inserted there — the invariant `record_writer.current_record_tenant` states.
    So calling the hook on THAT arm fails on EVERY create (FK 23503), the writer
    records `provision_failed`, and `/health` folds it into `ok: false` for the
    life of the process on a deployment with no repairable gap: the exact false
    red `sdk._graph_create` is deliberately left unhooked to avoid (#14582 F3),
    re-opened through the other door.

    The gate is read from the door's OWN body: the name bound to
    `is_supabase_enabled()` there IS the arm decision, and every hook call must
    sit inside a branch on that name. Pre-fix the call is at the function's top
    level — no enclosing `if` at all — so this reds.
    """
    node = _functions("tortoise/hosted_api.py").get("_provision_graph")
    assert node is not None, "hosted_api._provision_graph is gone — re-run the census"
    arm_names: set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Assign) or not isinstance(sub.value, ast.Call):
            continue
        func = sub.value.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name == "is_supabase_enabled":
            arm_names |= {t.id for t in sub.targets if isinstance(t, ast.Name)}
    assert arm_names, (
        "`_provision_graph` no longer binds its arm decision to a name, so this pin "
        "cannot tell which branch mints a `public.graphs` row. Re-cut it against the "
        "new spelling; do NOT delete it")
    calls = _calls_with_enclosing_tests(node, "_provision_graph_record")
    assert calls, (
        "the hook call is gone from the door — `test_every_creation_door_calls_the_hook` "
        "reds on that too")
    ungated = [
        call for call, guards in calls
        if not any(arm in ast.unparse(test) for test in guards for arm in arm_names)
    ]
    assert not ungated, (
        f"{len(ungated)} creation-hook call(s) in `hosted_api._provision_graph` sit "
        f"outside a branch on {sorted(arm_names)}. On the REGISTRY arm the cursor "
        "row's FK (`REFERENCES public.graphs`) refuses EVERY create, so each one "
        "publishes a permanent `armed_but_uncut` + `ok: false` for a deployment "
        "that has no gap to close (#14610 review F2)")


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


def test_health_stays_ok_when_an_unarmed_deployment_carries_an_uncut_graph(monkeypatch):
    """⛔ THE FOLD IS GATED ON `armed`, AND NOTHING ELSE PINNED THAT.

    MEASURED (#14610 review, mutation attribution): deleting the
    ``block["armed"] and`` conjunct from `hosted_api._record_health`'s fold
    leaves the WHOLE of this file green (41 passed) and
    `tests/test_9548_record_arming_health.py` green (36 passed). The sibling test
    above cannot catch it, because it clears the registry first — the count is 0,
    so the fold is never reached. Nothing in the tree exercised "unarmed AND
    count > 0" at all, so the fold's own comment claiming `armed` gates it was
    decoration: a mutant that drops the conjunct SURVIVED the suite.
    """
    rw.reset_uncut_graphs()
    try:
        rw.note_uncut_graph("org-14582", "graph-14582", reason="no_cursor_row")
        block = _health(monkeypatch, dsn=None)
        assert block["armed"] is False
        assert block["armed_but_uncut"] == 1, (
            "the control: the registry really does carry the row, so a green "
            "below cannot be a green over an empty population")
        assert block["ok"] is True, (
            "an UNARMED deployment writes no record at all, so a graph missing "
            "its cursor row there costs nothing and must not red the "
            "deployment: without this leg the fold's `armed` conjunct is "
            "unpinned and a mutant that drops it survives the whole suite")
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


def test_the_health_payload_takes_the_count_and_its_reasons_from_one_snapshot(
        monkeypatch):
    """⛔ F6 (#14610 review): the count and its reasons are ONE read, not two.

    Two lock acquisitions let a concurrent `note_uncut_graph` land between them
    and publish `armed_but_uncut: 0` beside `{"no_cursor_row": 1}` — a payload
    that contradicts itself, with no repair path because it is already out. The
    invariant was only ever asserted at the FUNCTION level, never on `/health`,
    so nothing redded.

    Proved by making the two readers DISAGREE: the payload must publish the
    reasons that travelled with the count it publishes. Pre-fix the second reader
    is consulted, so the `mode_halted` value below leaks into the block.
    """
    snapshot = {
        "armed_but_uncut": 2,
        "armed_but_uncut_untracked_observations": 0,
        "armed_but_uncut_observations": 2,
        "armed_but_halted": 0,
        "armed_but_halted_untracked_observations": 0,
        "armed_but_uncut_reasons": {"no_cursor_row": 2},
    }
    monkeypatch.setattr(rw, "armed_but_uncut", lambda: dict(snapshot))
    monkeypatch.setattr(
        rw, "armed_but_uncut_reasons", lambda: {"mode_halted": 9})
    block = _health(monkeypatch, dsn="postgresql://u:p@127.0.0.1:1/record")
    assert block["armed_but_uncut"] == 2
    assert block["armed_but_uncut_reasons"] == {"no_cursor_row": 2}, (
        "the payload's reasons did not come from the snapshot that produced its "
        f"count — got {block['armed_but_uncut_reasons']!r}. A second reader is a "
        "second moment: a concurrent observation then publishes a count and a "
        "breakdown that contradict each other (#14610 review F6)")
    assert sum(block["armed_but_uncut_reasons"].values()) == block["armed_but_uncut"]


def test_the_payload_states_how_the_uncut_field_clears(monkeypatch):
    """⛔ F3 (#14610 review): the payload must not name a remedy that leaves it red.

    The register is PROCESS memory. `record_allocator_cli.py backfill` heals the
    RECORD in ANOTHER process and cannot retract this process's entry, and on a
    quiescent graph — or with the allocator flag off, where `record_owns_position`
    returns before its read — no later observation happens at all, so the entry
    and the `ok` it drives clear only on a restart. Publishing the count alone
    left an operator who ran the documented remedy staring at a red `ok` with no
    way to know why.
    """
    rw.reset_uncut_graphs()
    try:
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        rw.note_uncut_graph("org-14582", "graph-a", reason="no_cursor_row")
        block = _health(monkeypatch, dsn=dsn)
        assert block["ok"] is False, "the control: this graph is uncut"
        note = block["armed_but_uncut_clears"]
        assert isinstance(note, str) and note, (
            "`/health` publishes `armed_but_uncut` and its reasons but not HOW they "
            "clear, so the documented remedy appears to clear a field it cannot")
        assert "restart" in note, (
            "the note must name the RESTART, because that is the only clear on a "
            "quiescent graph (and the only one at all with the allocator flag off)")
        assert "backfill" in note and "another process" in note, (
            "the note must say that `backfill` repairs the RECORD in another "
            "process — otherwise it reads as the clear route for this field")
        assert "--arm" in note and "--acknowledge-cutover" in note, (
            "the note must name the WHOLE chain, not a bare `backfill`: seeding "
            "alone leaves the row `seeded`, and a `seeded` row allocates nothing "
            "(#14610 review F1)")
        # ...and it is published on the UNARMED arm too, because it is a constant
        # rather than a measurement (a `None` there would read as "not evaluated").
        assert _health(monkeypatch, dsn=None)["armed_but_uncut_clears"] == note
    finally:
        rw.reset_uncut_graphs()


def test_a_halted_graph_is_reported_but_does_not_red_the_deployment(monkeypatch):
    """⛔ `halted` IS NOT `uncut` (#14582 review P1-3) — and NOT because it is free.

    `halt` is the DOCUMENTED remedy an operator runs when the record holds a
    duplicate position, so the state is SANCTIONED: folding it into
    `armed_but_uncut` would report an operator's decision as a deployment defect,
    and a halt does not clear on its own, so the red would be permanent and
    indistinguishable from a real one. It is still PUBLISHED, on its own key.

    ⛔ WHAT AN EARLIER CUT OF THIS DOCSTRING GOT WRONG (#14610 review F2): it said
    that in `halted` mode `record_owns_position` returns False, so
    `live_write.write` KEEPS the JSONL append — "that graph loses nothing". The
    first clause is true and the conclusion is FALSE: keeping the append makes the
    journal LINE durable, while the record accumulates NOTHING (`ALLOCATION_SQL`
    is scoped `active`, and with the writer flag off the enumerated arm is refused
    because the row exists). The payload therefore publishes what a halted graph
    is actually losing, in `armed_but_halted_clears`.

    ⛔ AND THE POPULATIONS ARE MUTUALLY EXCLUSIVE PER GRAPH (#14610 review F2).
    Each observation RETRACTS the state it supersedes, so a graph moved between
    modes is counted in one key and not both — see the `armed_but_halted == 0`
    transition below, which is red on the pre-fix code.
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
            "a halted graph is a SANCTIONED STOP, not a gap — the halted population "
            "stays out of the `ok` fold for that reason. It is not free: a halted "
            "graph's journal LINE still appends while its record accumulates "
            "NOTHING, so `ok: false` would red the deployment for the state the "
            "documented remedy leaves it in"
            "(`armed_but_halted_clears` states the cost and what clears it)")

        # ── the transition the pre-fix code double-counted ──────────────────
        # `mode_halted -> mode_seeded` is the `reopen -> seed` half-remedy this
        # module documents as the common operator path. One graph, so exactly one
        # population may carry it: pre-fix `armed_but_halted` stayed 1 for the
        # life of the process over a graph the operator had already reopened.
        rw.note_uncut_graph("org-14582", "graph-halted", reason="mode_seeded")
        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_halted"] == 0, (
            "the graph was re-observed as `seeded`, so it is NOT halted any more — "
            "`armed_but_halted` is published with the clear route 'until an "
            "operator reopens it', and the operator did. Pre-fix this stayed 1 "
            "forever, and a graph left `seeded` never clears it at all")
        assert block["armed_but_uncut"] == 1, (
            "the `seeded` half-remedy IS the gap: the row exists but allocates "
            "nothing, so the graph is uncut from this observation on")
        assert block["armed_but_uncut_reasons"] == {"mode_seeded": 1}, block
        assert sum(block["armed_but_uncut_reasons"].values()) == block[
            "armed_but_uncut"], "one snapshot, one population"
        assert block["ok"] is False, (
            "an ARMED deployment now carries an uncut graph, so `ok` must follow "
            "the retractable per-graph map — this is the heal-in-reverse direction "
            "of the same retraction")

        # ── and the direction back: the newest observation owns the graph ────
        rw.note_uncut_graph("org-14582", "graph-halted", reason="mode_halted")
        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_halted"] == 1, block
        assert block["armed_but_uncut"] == 0, (
            "a halted graph must leave `armed_but_uncut` — otherwise one graph is "
            "published in BOTH populations, which is the self-contradicting "
            "payload the split exists to prevent")
        assert block["armed_but_uncut_reasons"] == {}, block
        assert block["ok"] is True, block
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

    ⛔ What must NOT be the mechanism: retracting the OVERFLOW counter on the
    heal that restores room. That fires on the first tracked graph healed, before
    anything is known about the graphs it counts, and erases the only evidence
    that `armed_but_uncut` is a lower bound — see
    `test_the_overflow_evidence_survives_the_heal_of_every_tracked_graph`.
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
        assert block["armed_but_uncut_untracked_observations"] == 1, (
            "the overflow count is LIFETIME and is deliberately NOT retracted: "
            "it counts observations the full map could not track, so a heal of a "
            "TRACKED graph knows nothing about them. What `ok` reads is the "
            "tracked map alone")
        assert block["armed_but_uncut_reasons"] == {}, (
            "a payload that says `armed_but_uncut: 0` beside "
            "`{'no_cursor_row': 33}` contradicts itself and has no repair path")
        assert block["ok"] is True, (
            "NOTHING tracked is uncut any more: the deployment is healthy, and "
            "the only way out of `ok: false` must not be a restart")
        # The lifetime diagnostic is retained — it is simply not part of `ok`.
        assert block["armed_but_uncut_observations"] == over
    finally:
        rw.reset_uncut_graphs()


def test_the_overflow_evidence_survives_the_heal_of_every_tracked_graph(
        monkeypatch):
    """⛔ #14587 review P1 — RETRACTING THE OVERFLOW IS THE LATCH INVERTED.

    The first fix for P1 zeroed `armed_but_uncut_untracked_observations` inside
    `clear_uncut_graph` as soon as a pop restored room. That fires on the FIRST
    tracked graph healed — with `UNCUT_GRAPH_CAP + 1` uncut graphs, healing the 32
    TRACKED ones zeroes the count while graph 33 is still uncut and untouched.
    The count of uncut graphs is then honestly 0, `ok` is true, and the payload
    carries no evidence at all that the cap was ever exceeded, so nothing sends
    the operator to `sweep`. Measured on that code:

        33 uncut        : uncut=32 untracked=1
        heal the 32     : uncut=0  untracked=0   <- evidence erased, graph 33
                                                  still uncut
    """
    rw.reset_uncut_graphs()
    try:
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        cap = rw.UNCUT_GRAPH_CAP
        for i in range(cap + 1):
            rw.note_uncut_graph("org-14582", f"graph-{i}", reason="no_cursor_row")
        # Heal ONLY the tracked ones. graph-<cap> is the untracked one and stays
        # uncut — exactly the state in which the erased count lied.
        for i in range(cap):
            rw.clear_uncut_graph("org-14582", f"graph-{i}")
        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_uncut"] == 0
        assert block["armed_but_uncut_untracked_observations"] == 1, (
            "this is the ONLY signal that `armed_but_uncut: 0` is a LOWER BOUND "
            "— erasing it turns the payload into a false pass over a graph that "
            "is still uncut")
        assert block["armed_but_uncut_untracked_observations"] > 0, (
            "an operator must be able to tell a capped count from a complete one")
        # …and the untracked graph is not lost forever: the room the heal made
        # means its next observation is TRACKED again, so it reds again.
        rw.note_uncut_graph("org-14582", f"graph-{cap}", reason="no_cursor_row")
        again = _health(monkeypatch, dsn=dsn)
        assert again["armed_but_uncut"] == 1
        assert again["ok"] is False, (
            "a graph still uncut must red the deployment again as soon as it is "
            "observable — the heal only cleared the graphs it could not name")
    finally:
        rw.reset_uncut_graphs()


def test_a_partial_heal_of_an_overflowing_registry_stays_red(monkeypatch):
    """The other direction: retracting what was healed must not clear a real gap."""
    rw.reset_uncut_graphs()
    try:
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        for i in range(rw.UNCUT_GRAPH_CAP + 1):
            rw.note_uncut_graph("org-14582", f"graph-{i}", reason="no_cursor_row")
        rw.clear_uncut_graph("org-14582", "graph-0")
        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_uncut"] == rw.UNCUT_GRAPH_CAP - 1
        assert block["armed_but_uncut_untracked_observations"] == 1
        assert block["ok"] is False, (
            "32 graphs are still uncut — retracting the healed one must never be "
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
            "a halted population is a sanctioned stop — counting its overflow "
            "must not smuggle it into the `ok` fold. The reason is SANCTION, not "
            "safety: the journal line keeps appending while the record gains "
            "nothing (`_HALTED_CLEARS_NOTE`), which is the operator's stated "
            "position and not a repair the deployment may refuse")
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
    """A door can be RE-ENTERED (a retry after a partial commit, a concurrent
    create), so the second call must be a no-op rather than a refusal — while
    never moving a counter that is already allocating."""
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

    A re-entrant arm (a re-provisioning, a create that raced another, a retry
    after a partial commit) reaches a graph that ALREADY EXISTS and may carry a
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


# ── DB-free: the #14610 review's findings, one pin each ────────────────────
#
# Every case below reds on the PRE-FIX tree. They exist because the suite pinned
# the hook's wiring, the registry arithmetic, the `/health` fold and `backfill`'s
# counts — and NOT the sentence an operator is told to run (F1), the premise the
# `halted` split stands on (F2), what a non-cutover provision reports and clears
# (F4), the operator's `seeded` state (F5), or two prose claims the tree
# contradicts (F3, F6).

def test_the_uncut_remedy_names_the_whole_arming_chain():
    """⛔ F1 (#14610 review): `backfill` is HALF a remedy, and six sites named it.

    `backfill` seeds; `ALLOCATION_SQL` is scoped `AND mode = 'active'`, so a
    `seeded` row allocates nothing — the graph's writes stay refused
    `cursor_not_active`, `/health` keeps folding `ok: false`, and on a deployment
    whose `TORTOISE_RECORD_ALLOCATE` is not yet rolled the new row also REFUSES
    the enumerated replay that graph was still landing records through
    (`_refuse_an_enumerated_replay`: the ROW, not the mode, is the fact). So the
    remedy must name the ARM, and every operator-facing site must publish that
    one text rather than a second copy that can drift.
    """
    remedy = rw.uncut_graph_remedy("org-14610")
    assert "backfill" in remedy, remedy
    assert "--arm" in remedy, (
        "the remedy must arm the graph: a bare `backfill` leaves the row "
        f"`seeded`, which allocates nothing — got {remedy!r}")
    assert "--acknowledge-cutover" in remedy, (
        "arming is the per-graph cutover the CLI refuses without an "
        f"acknowledgement (#13043 review P1-2) — got {remedy!r}")
    assert "org-14610" in remedy, "the org id must be interpolated, not left as <org>"

    # The operator-facing homes, and how many times each must reach the ONE
    # builder. `provision_graph_record` has two (the failed provision and the
    # failed commit).
    #
    # ⛔ #14610 review F1 moved ONE site behind a helper, and the move is the fix:
    # the default-row HANDLER cannot name the builder directly, because the
    # `from … import` that would bind it is the very import that fails on the arm
    # the handler exists for (a function-local import makes the name LOCAL, so the
    # unbound name raises `UnboundLocalError` while the message is being built).
    # So the handler is pinned to its helper (the assertion after this loop) and
    # the helper is pinned to the ONE builder (the row below) — two hops, both
    # graded, rather than a site that quietly names no remedy at all.
    sites = (
        ("tortoise/record_writer.py", "_warn_uncut_graph", 1),
        ("tortoise/record_writer.py", "provision_graph_record", 2),
        ("tortoise/hosted_api.py", "_provision_graph_record", 1),
        ("tortoise/supabase_control.py", "_arming_remedy_for_log", 1),
    )
    missing = []
    for rel, function, want in sites:
        node = _functions(rel).get(function)
        assert node is not None, f"{rel}::{function} is gone — re-run the census"
        got = _hook_calls(node, "uncut_graph_remedy")
        if got < want:
            missing.append(f"{rel}::{function} calls uncut_graph_remedy× "
                           f"(want {want}, got {got})")
    assert not missing, (
        f"these operator-facing remedies do not name the arming chain: {missing}. "
        "A log line naming only `backfill` sends an operator to a command that "
        "seeds the row, cuts nothing over, and leaves `/health` red — the defect "
        "the PR fixed elsewhere, applied to the sentence that names the command")

    # The second hop (#14610 review F1): the HANDLER must reach its helper, or the
    # helper above is a site nothing calls — the same "no call site" shape as a
    # builder referenced only in comments.
    handler = _functions("tortoise/supabase_control.py").get(
        "_provision_default_graph_record")
    assert handler is not None, (
        "`supabase_control._provision_default_graph_record` is gone — re-run the "
        "census")
    assert _hook_calls(handler, "_arming_remedy_for_log") >= 1, (
        "the default-row handler no longer names the arming remedy at all, so its "
        "ERROR tells an operator the row is uncut and not how to cut it over")


def test_the_uncut_clear_note_names_the_arm_too():
    """F1, the `/health` half: the note must not say `backfill` heals the RECORD."""
    from tortoise import hosted_api

    note = hosted_api._UNCUT_CLEARS_NOTE
    assert "--arm" in note and "--acknowledge-cutover" in note, (
        "`record.armed_but_uncut_clears` must name the arming chain; a bare "
        f"`backfill` heals the ROW, not the record — got {note!r}")
    assert "seeds the ROW" in note, (
        "the note must say what a bare `backfill` actually does (seeds the row), "
        "because the old text said it 'heals the RECORD' and it does not")


def test_the_health_payload_states_what_a_halted_graph_loses(monkeypatch):
    """⛔ F2 (#14610 review): the `halted` split stands on SANCTION, not on "free".

    The pre-fix justification — "in that mode `record_owns_position` returns False
    so the JSONL append is KEPT, nothing is lost" — proves the LINE is durable and
    concludes something false about the RECORD: a halted row refuses the
    allocator's arm (`ALLOCATION_SQL` is scoped `active`) and, with the writer flag
    off, refuses the enumerated replay too. So `ok: true` over a halted graph whose
    record leg is completely off was the exact silence this field exists to end.
    Keeping it out of the fold may still be right (a sanctioned stop must not pin
    a deployment red for ever) — the payload now says what it costs.
    """
    rw.reset_uncut_graphs()
    try:
        rw.note_uncut_graph("org-14610-f2", "graph-14610-f2", reason="mode_halted")
        block = _health(monkeypatch, dsn="postgresql://u:p@127.0.0.1:1/record")
        assert block["armed_but_halted"] == 1, "the control: one halted graph"
        note = block.get("armed_but_halted_clears")
        assert isinstance(note, str) and note, (
            "the payload publishes `armed_but_halted` with no word on what the "
            "population is losing, so a reader concludes the halt is free")
        for token in ("REFUSED", "reopen", "seed", "arm"):
            assert token in note, (
                f"`armed_but_halted_clears` must name {token!r}: a halted graph's "
                f"record writes are refused until reopen -> seed -> arm — got {note!r}")
        assert "nothing is lost" not in note.lower(), (
            "the note must not repeat the claim F2 measured as false")
        # a constant, so it survives the failed-snapshot arm too
        assert _health(monkeypatch, dsn=None)["armed_but_halted_clears"] == note
    finally:
        rw.reset_uncut_graphs()


def test_a_halted_provision_is_not_reported_as_cut_over(monkeypatch):
    """⛔ F4 (#14610 review): a non-cutover outcome must not look like a cutover.

    `record_allocator.provision_graph_record` returns `mode='halted'` WITHOUT
    raising (it respects the halt), and the pre-fix wrapper fell into its success
    arm — reporting `provisioned: True` for a graph nothing was done to, and
    calling `clear_uncut_graph`, which pops `_UNCUT_HALTED` as well as
    `_UNCUT_GRAPHS`. So the entry `armed_but_halted` promises to keep publishing
    was evicted by a provision that changed nothing, and `/health` read
    `armed_but_halted: 0` for a graph that is still halted.
    """
    from tortoise import record_allocator as ra

    rw.reset_uncut_graphs()
    try:
        org, graph = "org-14610-f4", "graph-14610-f4"
        rw.note_uncut_graph(org, graph, reason="mode_halted")
        assert rw.armed_but_uncut()["armed_but_halted"] == 1

        class _Conn:
            committed = False

            def commit(self):
                self.committed = True

            def rollback(self):
                pass

            def close(self):
                pass

        conn = _Conn()
        monkeypatch.setattr(
            rw, "record_dsn",
            lambda: "postgresql://u:p@127.0.0.1:1/record")
        monkeypatch.setattr(rw, "_default_connect", lambda *a, **k: conn)
        monkeypatch.setattr(
            ra, "provision_graph_record",
            lambda connection, **kw: ra.ProvisionedRecord(
                org_id=kw["org_id"], graph_id=kw["graph_id"], seq=4,
                mode=ra.MODE_HALTED, already_cut_over=False, seeded=True))

        result = rw.provision_graph_record(org_id=org, graph_id=graph)
        assert conn.committed, (
            "the monotone SEED was written and must still be committed — this "
            "case is about what is REPORTED and CLEARED, not about skipping work")
        assert result["provisioned"] is False, (
            f"a halted provision cut NOTHING over, so `provisioned` must not be "
            f"True — got {result!r}")
        assert result["reason"] == ra.MODE_HALTED, result
        assert result["mode"] == ra.MODE_HALTED, result
        assert rw.armed_but_uncut()["armed_but_halted"] == 1, (
            "a provision that resumed nothing evicted the `armed_but_halted` "
            "entry, so the halted population now flickers with traffic")
    finally:
        rw.reset_uncut_graphs()


def test_the_hook_does_not_arm_a_row_an_operator_seeded(monkeypatch):
    """⛔ F5 (#14610 review): `seeded` is an operator's statement, like `halted`.

    The CLI refuses to move a graph `seeded -> active` without
    `--acknowledge-cutover`, spelling out four preconditions (#13043 review P1-2).
    The hook took that same transition silently for any row that seeded, so a
    re-provisioning armed a row an operator had deliberately left un-armed —
    bypassing a gate whose whole point is that arming before the writer flag is
    rolled turns the record path OFF. The rule is the ROW: a graph with NO counter
    row has no statement to respect, and it IS born cut over.
    """
    from tortoise import record_allocator as ra

    armed: list[str] = []
    monkeypatch.setattr(ra, "scope_tenant", lambda conn, org: None)
    monkeypatch.setattr(
        ra, "seed",
        lambda conn, **kw: ra.SeedResult(
            org_id=kw["org_id"], graph_id=kw["graph_id"], seq=3,
            mode=ra.MODE_SEEDED, row_max=3, enumerated_floor=3,
            healed_tail=False, burned=False))
    monkeypatch.setattr(ra, "arm",
                        lambda conn, **kw: armed.append(kw["graph_id"]) or 3)

    # (1) a PRE-EXISTING `seeded` row: seed it (monotone), do NOT arm it.
    monkeypatch.setattr(ra, "read_cursor", lambda conn, **kw: (3, ra.MODE_SEEDED))
    result = ra.provision_graph_record(None, org_id="org-14610-f5",
                                      graph_id="graph-seeded",
                                      enumerated_floor=3)
    assert armed == [], (
        "the hook ARMED a row an operator had deliberately left `seeded` — that "
        "is the per-graph cutover the CLI gates behind `--acknowledge-cutover`")
    assert result.mode == ra.MODE_SEEDED, result
    assert result.seeded is True, "the monotone seed must still be reported"

    # (2) CONTROL: no row at all is not an operator's statement, so the hook still
    # arms a graph it created — this is what stops the pin above from being
    # satisfiable by simply never arming anything (which would restore the very
    # defect #14582 closes).
    monkeypatch.setattr(ra, "read_cursor", lambda conn, **kw: None)
    fresh = ra.provision_graph_record(None, org_id="org-14610-f5",
                                      graph_id="graph-fresh",
                                      enumerated_floor=0)
    assert armed == ["graph-fresh"], (
        "a graph with NO counter row must still be born cut over (the plan's "
        "§A7.8 item 8 / M16 falsifier)")
    assert fresh.mode == ra.MODE_ACTIVE and fresh.seeded is True, fresh


def test_the_hook_docstring_does_not_name_the_door_it_must_not_hook():
    """⛔ F6.1 (#14610 review): the canonical place a reader lands said the WRONG thing.

    `record_writer.provision_graph_record`'s docstring listed
    `sdk._graph_create`'s registry arm as a caller; `sdk.py:51515-51540` says the
    opposite in capitals ("NOT HOOKED … on purpose") and the pin above reds if it
    is hooked. An unpinned absence is how the next reader "fixes" the absence back
    into a permanently-red `/health` field — so the claim is pinned where the
    absence is pinned.
    """
    doc = ast.get_docstring(
        _functions("tortoise/record_writer.py")["provision_graph_record"])
    assert doc, "`provision_graph_record` lost its docstring"
    assert "Called from" in doc, (
        "the pin reads the caller list out of the `Called from` paragraph; if the "
        "prose moved, move the pin with it")
    callers = doc.split("Called from", 1)[1].split("\n\n", 1)[0]
    assert "_graph_create" not in callers, (
        "the hook's own docstring names `sdk._graph_create` as a caller — the "
        "opposite of what `sdk.py` says and of what this file reds on. A reader "
        f"landing here is one trusting edit from a false red; got {callers!r}")
    assert "hosted_api._provision_graph" in callers, callers
    assert "ensure_default_graph_row" in callers, callers


def test_the_default_row_hook_is_not_reachable_from_a_credential_hit(monkeypatch):
    """⛔ F3 (#14610 review): the "credential self-heal" cannot reach what it names.

    `_provision_default_graph_record`'s docstring promised that "an EXISTING
    default row whose record cursor is missing gets one on this org's next
    credential resolution". It cannot: `default_graph_for_credential` RETURNS on a
    row hit without calling `ensure_default_graph_row`, and
    `_mint_default_graph_row_best_effort` is that function's only caller — reached
    on a MISS (or the race window), never for a row that already exists. So the
    false claim is both a claim and the compensation the PR offered for a
    best-effort hook: a reader who believes it stops looking for the population it
    does not cover.
    """
    from tortoise import supabase_control as sc

    # (a) BEHAVIOUR — a credential HIT must not re-enter the mint/provision path.
    def _must_not_run(*args, **kwargs):
        raise AssertionError(
            "a credential HIT re-entered the default-row mint path — if this ever "
            "becomes reachable, F3's premise changes and this pin must be "
            "re-derived rather than deleted")

    monkeypatch.setattr(sc, "ensure_default_graph_row", _must_not_run)
    monkeypatch.setattr(sc, "_mint_default_graph_row_best_effort", _must_not_run)
    monkeypatch.setattr(sc, "default_graph_row_for",
                        lambda cp, org_id: {"id": "g_1", "namespace": "ns"})
    assert sc.default_graph_for_credential(
        object(), "org-14610-f3", "fallback-ns", context="pin") == ("ns", "g_1")

    # (b) THE CLAIM — the REACHABILITY paragraph must not name the credential
    # path. Structural, not a substring of the whole docstring: the docstring
    # keeps the retracted claim as HISTORY (quoted, then refuted), and a pin that
    # searched the whole text would red on its own correction.
    doc = sc._provision_default_graph_record.__doc__ or ""
    reach = doc.split("Called at every arm", 1)[-1].split("\n\n", 1)[0]
    assert reach and reach != doc, (
        "the pin reads the reachability paragraph (`Called at every arm …`); if "
        "that prose moved, move the pin with it")
    assert "credential" not in reach.lower(), (
        "the reachability paragraph still names the credential path, which (a) "
        f"measures as unreachable for an existing row (#14610 review F3): {reach!r}")
    assert "only caller" in doc, (
        "the docstring must name the REAL mechanism — one caller, reached on a "
        "MISS or a re-provisioning — rather than leaving the reach unstated")


# ---------------------------------------------------------------- argv-level pins
# ⛔ #14610 REVIEW FINDING 2 (blocking, category A — a bypass): every one of these
# rows was UNPINNED. The suite drove `cli._cmd_backfill(...)` directly, so the
# argv-level gate in `main()` — the branch that decides whether an operator's
# `backfill --arm` has to acknowledge a cutover — could be deleted with NOTHING
# going red. Deleting `(args.command == "backfill" and args.arm_graphs)` from
# `wants_arm` is a one-line edit that arms an entire org's graphs with no
# acknowledgement, and the sibling gate is pinned exactly this way in the house
# style (`test_13043_record_allocator_cli.py::
# test_arm_refuses_without_the_acknowledgement`), which is what made the omission
# invisible. These rows call `cli.main([...])` — the real argv path — not the
# command body.

def test_backfill_arm_is_refused_without_the_cutover_acknowledgement(capsys):
    """`backfill --arm` IS a cutover — it must pass the same gate as `arm`.

    ⛔ THIS ROW REDS IF THE `backfill` DISJUNCT GOES. `arm` before the writer flag
    is rolled turns the record path OFF for every graph it touches (`CUTOVER_ACK_TEXT`
    item 1), and `backfill --arm` rolls that switch for a whole population, so the
    acknowledgement is not less necessary there — it is more.
    """
    from tools import record_allocator_cli as cli

    rc = cli.main(["backfill", "--org-id", "o", "--arm"])
    assert rc == cli.EXIT_REFUSED, (
        "`backfill --arm` reached the population path without an acknowledgement — "
        "the gate in `main()` no longer covers it (got "
        f"{rc}, want EXIT_REFUSED={cli.EXIT_REFUSED})")
    err = capsys.readouterr().err
    assert "record_allocator_cli backfill" in err, (
        "the refusal must name the command the operator ran:\n" + err)


def test_backfill_seed_only_is_not_gated(capsys):
    """Non-vacuity for the row above: `backfill` WITHOUT `--arm` seeds only.

    A gate that refused every `backfill` would pass the refusal pin and break the
    verb that the gate's own text recommends (`backfill --dry-run`, then for real).
    A `seeded` row allocates nothing, so there is no cutover to acknowledge; the
    run must get PAST the gate and fail later on what it actually lacks (no DSN).
    """
    from tools import record_allocator_cli as cli

    rc = cli.main(["backfill", "--org-id", "o", "--dry-run"])
    assert rc != cli.EXIT_REFUSED, (
        "a seed-only `backfill` was refused by the CUTOVER gate — the gate now "
        "covers the verb whose own remedy text recommends it")
    err = capsys.readouterr().err
    assert "acknowledge-cutover" not in err, (
        "a seed-only `backfill` was told to acknowledge a cutover it does not "
        "perform:\n" + err)


def test_the_acknowledgement_states_each_verbs_own_blast_radius(capsys):
    """⛔ ONE SHARED SENTENCE CANNOT DESCRIBE BOTH VERBS (#14610 review F2).

    Item 3 of the text read "This command arms ONE graph; every other graph in the
    org stays uncut" — true of `arm`, the OPPOSITE of `backfill --arm`, which arms
    the whole sweep population in one run. An acknowledgement whose blast-radius
    sentence is false for the verb the operator ran is not an acknowledgement, and
    the scope is what the operator is being asked to accept.
    """
    from tools import record_allocator_cli as cli

    assert cli.main(["arm", "--org-id", "o", "--graph-id", "g"]) == cli.EXIT_REFUSED
    single = capsys.readouterr().err
    assert cli.main(["backfill", "--org-id", "o", "--arm"]) == cli.EXIT_REFUSED
    population = capsys.readouterr().err

    assert "ONE GRAPH" in single.upper(), (
        "`arm`'s acknowledgement no longer states its single-graph scope:\n" + single)
    assert "EVERY UNCUT GRAPH IN ITS POPULATION" in population.upper(), (
        "`backfill --arm`'s acknowledgement does not state the population scope — "
        "it is telling a population operator about one graph:\n" + population)
    # ⛔ The failure this pin exists for: the single-graph sentence surviving into
    # the population verb's text. `arm`'s phrasing, verbatim, must NOT be there.
    assert "arms ONE graph; every other graph in the org" not in population, (
        "`backfill --arm` printed `arm`'s single-graph blast radius — the text is "
        "shared verbatim and the scope is not interpolated:\n" + population)
    assert "`arm` changes" not in population, (
        "`backfill --arm`'s header names the WRONG verb, so an operator reading a "
        "population refusal believes they ran the single-graph command:\n" + population)
    assert "`backfill --arm` changes" in population, (
        "`backfill --arm`'s header must name the verb the operator ran:\n" + population)


def test_the_backfill_only_argv_rules_are_enforced(monkeypatch):
    """The three `ap.error` branches `backfill` added are pinned by argv, not by prose.

    `--journal`/`--enumerated-floor` are PER-GRAPH readings (a single floor handed
    to a population would seed every graph to one number — the defect `sweep`
    refuses by name), `--dry-run` promises a rollback only `backfill` can perform,
    and `--arm` is `backfill`'s spelling. Each must raise `SystemExit` from
    `ap.error` rather than be silently ignored by a verb that cannot honour it.
    """
    import contextlib
    import io

    from tools import record_allocator_cli as cli

    cases = {
        "backfill --journal": ["backfill", "--org-id", "o", "--journal", "/tmp/j"],
        "backfill --enumerated-floor": [
            "backfill", "--org-id", "o", "--enumerated-floor", "7"],
        "dry-run on arm": ["arm", "--org-id", "o", "--graph-id", "g", "--dry-run"],
        "arm on sweep": ["sweep", "--org-id", "o", "--arm"],
    }
    for label, argv in cases.items():
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                rc = cli.main(argv)
            except SystemExit as exc:          # argparse's own exit
                assert exc.code not in (0, None), (
                    f"{label} exited {exc.code}, which argparse uses for `--help` "
                    "— a rule that exits 0 is not a refusal")
                continue
        raise AssertionError(
            f"{label} was ACCEPTED (rc={rc}) — the argv rule that forbids it is "
            f"gone, so the verb silently ignored {' '.join(argv)}")


# ── the OBSERVER the registry is fed by (DB-free) ───────────────────────────
#
# ⛔ #14610 REVIEW FINDING 3 (non-vacuity, agent-infra#3442 rule 3). The whole
# detection/retraction mechanism below shipped with NO test: every row in this
# file drove `note_uncut_graph` / `clear_uncut_graph` DIRECTLY (the map), and the
# three suites that name `record_owns_position` MOCK it. Deleting the six lines
# that observe and retract left all of them green, so `armed_but_uncut` would
# never populate from traffic — and `clear_uncut_graph`'s own warning ("WITHOUT
# THIS THE REGISTRY IS A LATCH") would be back in the code with nothing red.
# These two rows are the missing pins.


class _OwnsPositionConn:
    """The smallest thing `record_owns_position` accepts as a connection."""

    def __init__(self):
        self.committed = 0
        self.closed = 0

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed += 1


def test_the_write_path_observes_and_retracts_an_uncut_graph(monkeypatch):
    """`record_owns_position` IS the observer — and the repair — not just a gate.

    The read it already makes on every armed write is the only per-write sight of
    the graph's cursor row, so an absent/`seeded` row must be NOTED and an
    `active` row must CLEAR the note. A one-directional version is a latch:
    `/health` stays non-`ok` for the life of the process over a graph an operator
    already healed.
    """
    from tortoise import record_allocator as ra

    conn = _OwnsPositionConn()
    state: dict = {"row": None}
    monkeypatch.setattr(rw, "_default_connect", lambda _dsn: conn)
    monkeypatch.setattr(ra, "read_cursor",
                        lambda _conn, *, org_id, graph_id: state["row"])
    monkeypatch.setattr(rw, "_breaker_open_until", 0.0)
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    rw.reset_uncut_graphs()

    # (1) NO ROW — the population /health could not see: a graph whose record leg
    # is off while every counter reads healthy. The write still goes to the
    # journal (fail open to the append), and the graph is now VISIBLE.
    assert rw.record_owns_position(tenant=("org-observe", "graph-observe")) is False
    published = rw.armed_but_uncut()
    assert (published["armed_but_uncut"], published["armed_but_uncut_reasons"]) == (
        1, {"no_cursor_row": 1}), (
        "an armed deployment read a graph with NO cursor row and published "
        f"nothing — the observer is gone: {published!r}")

    # (2) A NON-ACTIVE ROW — `seeded`/`halted` allocate nothing, so the row must
    # be reported under ITS OWN reason (the remedy differs), not folded into (1).
    state["row"] = (0, "seeded")
    assert rw.record_owns_position(tenant=("org-observe", "graph-observe")) is False
    assert rw.armed_but_uncut()["armed_but_uncut_reasons"] == {"mode_seeded": 1}

    # (3) THE SAME READ THAT OBSERVES THE GAP OBSERVES ITS REPAIR — an `active`
    # row is the record taking the ordinal, and the entry must be RETRACTED.
    state["row"] = (0, "active")
    assert rw.record_owns_position(tenant=("org-observe", "graph-observe")) is True
    healed = rw.armed_but_uncut()
    assert (healed["armed_but_uncut"], healed["armed_but_uncut_reasons"]) == (0, {}), (
        "a HEALED graph is still published as uncut — the registry is a LATCH, "
        f"and /health cannot go green without a restart: {healed!r}")
    assert conn.committed and conn.closed, (
        "the observation's read left its transaction/handle open (#9558's "
        "lock-leak rule) — the commit/close belong to this path, not the caller")
    rw.reset_uncut_graphs()


def test_the_default_row_handler_survives_an_unimportable_record_seam(
        monkeypatch, caplog):
    """The handler cannot name a binding the failing `try` was supposed to make.

    ⛔ #14610 REVIEW FINDING 1 (P1). The remedy formatter lives on the record seam;
    a `from tortoise.record_writer import …` INSIDE the function makes both names
    LOCAL, so on the very arm this handler exists for — "the record seam is
    unimportable" — `uncut_graph_remedy` is unbound and the handler raises
    `UnboundLocalError` while BUILDING its message.

    The consequence is not cosmetic: the ERROR is never emitted, the exception
    escapes into `_mint_default_graph_row_best_effort`, which reports "row NOT
    minted" over a row that exists (all three arms run after the id is durable)
    and starts that org's `_DEFAULT_ROW_MINT_RETRY_S` suppression window.

    The fallback is asserted to be the seam's OWN text, so the literal cannot
    drift from `record_writer.uncut_graph_remedy` by an edit to one of them.
    """
    import logging
    import sys
    from types import ModuleType

    from tortoise import supabase_control

    org, graph = "org-14610-f1", "graph-14610-f1"
    expected = rw.uncut_graph_remedy(org)

    # The healthy arm first: the helper reads the seam when the seam is readable.
    assert supabase_control._arming_remedy_for_log(org) == expected, (
        "the log helper no longer names the seam's own remedy, so the two "
        "formatters of one command have already drifted")

    # Then the arm this handler exists for: the module is present but a shell (as
    # it is when ITS import failed — nothing it would have exported is bound).
    stub = ModuleType("tortoise.record_writer")
    monkeypatch.setitem(sys.modules, "tortoise.record_writer", stub)

    with caplog.at_level(logging.ERROR, logger="tortoise.supabase_control"):
        try:
            supabase_control._provision_default_graph_record(org, graph)
        except BaseException as exc:  # noqa: BLE001, RUF100
            raise AssertionError(
                "the handler raised while BUILDING its message, so the one line "
                "that makes an uncut row visible was never emitted and the "
                f"caller sees the opposite fact: {type(exc).__name__}: {exc}"
            ) from exc

    emitted = [r for r in caplog.records
               if "record cursor could" in r.getMessage()]
    assert emitted, (
        "the unimportable-seam arm logged NOTHING at ERROR — the best-effort "
        "wrapper's stated compensation is that a record fault is logged and made "
        f"visible; records: {[r.getMessage()[:80] for r in caplog.records]!r}")
    message = emitted[0].getMessage()
    assert graph in message and org in message, (
        f"the ERROR names neither the graph nor the org: {message!r}")
    assert expected in message, (
        "the ERROR does not carry the arming remedy — an operator reading it "
        f"learns the row is uncut and not how to cut it over: {message!r}")
