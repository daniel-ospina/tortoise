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
import json
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
#: yields an id from THREE arms (existing-row short-circuit, mint, convergent
#: adopt) and hooks each; a refactor that collapses them to one drops two.
#: ⛔ NOT "self-heal" for the short-circuit arm: a resolver that finds an existing
#: default row returns it without re-entering this door (`#14624` review 4,
#: ISSUE 2), so the arm fires on a re-provision or a race — see
#: `_provision_default_graph_record`'s docstring.
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


def test_the_hooks_own_door_list_matches_the_doors_that_call_it():
    """⛔ THE DOCSTRING NAMED A CALLER THAT DOES NOT EXIST (#14624 review 2).

    `provision_graph_record`'s docstring said it was *"Called from every
    graph-creation door the census found (`hosted_api._provision_graph`,
    `supabase_control.ensure_default_graph_row`, `sdk._graph_create`'s registry
    arm)"* — while the same PR deliberately does NOT hook `sdk._graph_create`
    (`_UNHOOKED`, above), because hooking it publishes a permanent
    `armed_but_uncut`.

    The door list is the first thing a reader consults when deciding whether a
    create path is covered, so an ASSERTED PRESENCE is worse than a gap: it is the
    inverse of the unpinned-absence hazard `test_the_doors_that_cannot_be_hooked_
    stay_unhooked` exists for, and neither test could see it — that one reads the
    AST, this one reads the prose the reader trusts. Both arms are graded here, so
    the sentence cannot drift from the call sites in either direction.
    """
    import inspect

    doc = inspect.getdoc(rw.provision_graph_record) or ""
    # The module path a door is named by in the prose, minus the package.
    named = [rel.removeprefix("tortoise/").removesuffix(".py")
             + "." + function for rel, function, _callee in _DOORS]
    missing = [door for door in named if door not in doc]
    assert not missing, (
        f"the hook's docstring no longer names the doors that DO call it: {missing} "
        "— a reader checking coverage would conclude they are not wired")
    claimed_but_unhooked = [rel.removeprefix("tortoise/").removesuffix(".py")
                            + "." + function for rel, function in _UNHOOKED]
    for door in claimed_but_unhooked:
        assert f"{door}`'s" not in doc and f"`{door}`," not in doc, (
            f"the hook's docstring claims a caller that does not exist: {door} is "
            "NOT hooked on purpose (F1/F3, pinned above); an asserted presence is "
            "how the next reader concludes a door is covered when it is not")
    assert "NOT hooked" in doc, (
        "the docstring omits the deliberate NON-call, so the absence reads as an "
        "omission rather than a decision")


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
    # ⛔ SUBSTRING, OVER THE WHOLE SERIALIZED PAYLOAD (#14624 review F1). This pin
    # used to assert SET MEMBERSHIP of the leaf values, which is exact equality —
    # and the registry's key is COMPOSITE (`f"{org_id}\u0000{graph_id}"`), so a
    # payload leaking the keys verbatim stayed GREEN over the leak, on the one
    # claim that must be airtight on an UNAUTHENTICATED endpoint (measured: the
    # reviewer published `org-SECRET\u0000graph-SECRET` and all three assertions
    # held). Every nested string is covered here, keys included.
    blob = json.dumps({"report": report, "reasons": reasons},
                      sort_keys=True, default=str)
    assert "SECRET" not in blob, (
        f"a tenant identifier reached the unauthenticated payload: {blob}")
    assert all(isinstance(v, (int, str)) for v in published)


#: The names THIS change declares in `record_writer.__all__`: the creation hook,
#: the registry it feeds, and the reader/clearer of that registry. The surface
#: pytest grades below is THESE, because they are the names this diff can make a
#: declared-but-absent export by editing one list.
_DECLARED_BY_THIS_CHANGE = (
    "armed_but_uncut",
    "armed_but_uncut_reasons",
    "clear_uncut_graph",
    "note_uncut_graph",
    "provision_graph_record",
)


def test_the_declared_export_surface_keeps_what_this_change_added():
    """⛔ A WIDENED SURFACE MUST STAY WIDE, AND STAY RESOLVABLE (#14582 P2-8).

    This change adds the creation hook and the `armed_but_uncut` registry to
    `record_writer.__all__`. A by-name import
    (``from tortoise.record_writer import provision_graph_record``) bypasses
    `__all__` entirely, so a name dropped from the list is invisible to the
    suites that use it and visible only to `import *` and to tooling that reads
    the declared surface — in production, after the release. Re-adding a name
    here is free; shrinking the surface is a red that has to be argued for.

    ⛔ WHAT THIS ROW NO LONGER CLAIMS (#14624 review 2, P3). An earlier cut also
    asserted `"current_record_tenant" in rw.__all__` while its rationale said
    this change had DROPPED that name "in the same edit" — false of the final
    diff, where it is unchanged context that `origin/main` already declares. An
    assertion on unchanged context cannot vary under ANY mutation of this diff,
    so it was dead weight under a rationale that asserted the opposite. What
    replaces it is the direction this diff CAN break: the five names above.

    MUTANT: delete any of `_DECLARED_BY_THIS_CHANGE` from `__all__` (→ the first
    arm), or declare a name the module does not define (→ the second).
    """
    missing = [name for name in _DECLARED_BY_THIS_CHANGE if name not in rw.__all__]
    assert not missing, (
        f"{missing} are declared public by this change but no longer in "
        "`record_writer.__all__` — that is a silent SHRINK of the surface a by-name "
        "import does not notice, so nothing else would red")
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
        # ...and it is published on the UNARMED arm too, because it is a constant
        # rather than a measurement (a `None` there would read as "not evaluated").
        assert _health(monkeypatch, dsn=None)["armed_but_uncut_clears"] == note
        # ⛔ THE `armed` CONJUNCT IS GRADED HERE (#14624 review F3). This is the
        # one arm where the registry holds an uncut graph AND the deployment is
        # UNARMED, and the fold is `block["armed"] and uncut_count > 0`: an
        # unarmed deployment has no record for a graph to be missing a row in, so
        # the count must NOT degrade `ok`. Delete the conjunct and this reds.
        unarmed = _health(monkeypatch, dsn=None)
        assert unarmed["armed"] is False and unarmed["armed_but_uncut"] == 1, (
            f"the control: the registry holds the graph, the deployment is "
            f"unarmed — {unarmed}")
        assert unarmed["ok"] is True, (
            "an unarmed deployment cannot have an uncut graph: with no record "
            "there is no row for one to be missing, so a process-memory entry "
            "must not red `ok`")
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


def test_a_graph_TRANSITIONS_between_the_two_populations(monkeypatch):
    """⛔ THE SPLIT IS A TRANSITION, NOT A CLASSIFICATION (#14624 review N1).

    The registry records OBSERVATIONS of ONE graph, and a graph MOVES: `halt` is
    the documented remedy for a duplicate position (`active` -> `halted`), and
    `reopen` -> `seed` takes a halted graph back to an incomplete cutover. A
    dispatch that only ever ADDED an entry to one map published the same graph
    on BOTH keys after a `reopen` — and, the worse direction, kept an uncut entry
    (with a reason that had stopped being true) over a graph an operator had
    healed and then deliberately halted. That second construct is a `ok: false`
    with a false reason that NO in-process write can clear: a halted graph never
    reaches the `active` arm that calls `clear_uncut_graph`.
    """
    rw.reset_uncut_graphs()
    try:
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        tenant = ("org-14582", "graph-transitions")

        # 1. Observed uncut: the hook never reached it. A GAP, and red.
        rw.note_uncut_graph(*tenant, reason="provision_failed")
        block = _health(monkeypatch, dsn=dsn)
        assert (block["armed_but_uncut"], block["armed_but_halted"]) == (1, 0)
        assert block["armed_but_uncut_reasons"] == {"provision_failed": 1}
        assert block["ok"] is False

        # 2. The operator heals it out of band and then HALTS it. The graph now
        #    has a row and is no longer a gap; the old reason is a dead fact.
        rw.note_uncut_graph(*tenant, reason="mode_halted")
        block = _health(monkeypatch, dsn=dsn)
        assert (block["armed_but_uncut"], block["armed_but_halted"]) == (0, 1), (
            "a halt MOVES the graph between the populations: the uncut map holds "
            "the deployment's GAPS, and this graph is not one of them")
        assert block["armed_but_uncut_reasons"] == {}
        assert block["ok"] is True, (
            "the false red this transition prevents: a healed-then-halted graph "
            "keeps its journal append and loses nothing, and `ok: false` here "
            "could never clear while this process lives")

        # 3. `reopen` -> `seed`: an incomplete cutover again, and NOT still filed
        #    as a deliberate halt (whose remedy would be "nothing to do").
        rw.note_uncut_graph(*tenant, reason="mode_seeded")
        block = _health(monkeypatch, dsn=dsn)
        assert (block["armed_but_uncut"], block["armed_but_halted"]) == (1, 0), (
            "one graph is in ONE population: `seeded` allocates nothing, so it "
            "is a gap again, and `armed_but_halted` beside it would tell an "
            "operator the halt is deliberate when the remedy is `arm`")
        assert block["armed_but_uncut_reasons"] == {"mode_seeded": 1}
        assert block["ok"] is False
        assert sum(rw.armed_but_uncut_reasons().values()) \
            == rw.armed_but_uncut()["armed_but_uncut"] == 1
    finally:
        rw.reset_uncut_graphs()


class _OwnsPositionConn:
    """The smallest thing `record_owns_position` accepts as a connection."""

    def __init__(self):
        self.committed = 0
        self.closed = 0

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed += 1


def test_the_WRITE_PATH_feeds_and_retracts_the_registry(monkeypatch):
    """⛔ THE OBSERVER ITSELF, NOT JUST THE REGISTRY API (#14624 review F2).

    Every other row in this file calls `note_uncut_graph` / `clear_uncut_graph`
    DIRECTLY, so the observation block inside :func:`record_owns_position` — the
    one the whole field exists for, and the only per-write read of the cursor row
    on an armed deployment — was entered by NO test: deleting `if usable:` and
    its `else:` branch together reddened nothing. It is driven here the way
    `tests/test_14002_record_only_journal.py` drives the same function: a
    connection and a cursor row are INJECTED, so the question is the DECISION and
    no database is needed.
    """
    from tortoise import record_allocator as ra

    conn = _OwnsPositionConn()
    state: dict[str, object] = {"row": None}

    def read_cursor(_conn, *, org_id, graph_id):
        return state["row"]

    monkeypatch.setattr(rw, "_default_connect", lambda _dsn: conn)
    monkeypatch.setattr(ra, "read_cursor", read_cursor)
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setenv(rw.RECORD_SINGLE_TENANT_ENV, "1")
    monkeypatch.setenv(rw.RECORD_ORG_ENV, "org-14582")
    monkeypatch.setenv(rw.RECORD_GRAPH_ENV, "graph-write-path")
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    rw.reset_uncut_graphs()
    try:
        tenant = ("org-14582", "graph-write-path")

        # 1. no row: the WRITE PATH is what records the gap.
        state["row"] = None
        assert rw.record_owns_position(tenant=tenant) is False
        block = rw.armed_but_uncut()
        assert block["armed_but_uncut"] == 1, (
            f"the write path observed an uncut graph and the registry did not "
            f"hear it: {block}")
        assert rw.armed_but_uncut_reasons() == {"no_cursor_row": 1}

        # 2. `active`: the SAME read is the repair.
        state["row"] = (5, "active")
        assert rw.record_owns_position(tenant=tenant) is True
        assert rw.armed_but_uncut()["armed_but_uncut"] == 0, (
            "the observation that repairs the gap must RETRACT the entry")

        # 3. `halted`: the write path must MOVE it — report it on the halt's own
        #    key, not as a gap. (The `halted` arm's retraction is graded by
        #    `test_a_graph_TRANSITIONS_between_the_two_populations`: the `active`
        #    observation in step 2 already emptied the uncut map here, so this row
        #    cannot also show the entry being retracted.)
        state["row"] = (5, "halted")
        assert rw.record_owns_position(tenant=tenant) is False
        block = rw.armed_but_uncut()
        assert (block["armed_but_uncut"], block["armed_but_halted"]) == (0, 1), (
            f"a halt observed on the write path must MOVE the graph between the "
            f"populations, not add a second filing: {block}")

        # 4. `seeded`: an incomplete cutover again, and not still a halt.
        state["row"] = (5, "seeded")
        assert rw.record_owns_position(tenant=tenant) is False
        block = rw.armed_but_uncut()
        assert (block["armed_but_uncut"], block["armed_but_halted"]) == (1, 0), block
        assert rw.armed_but_uncut_reasons() == {"mode_seeded": 1}
    finally:
        rw.reset_uncut_graphs()


class _RowOnlyControlPlane:
    """The control plane reduced to the ONE read a credential resolution makes."""

    def __init__(self, rows):
        self.rows = rows
        self.queries: list[tuple[str, tuple[str, ...]]] = []

    def query(self, table, select=None, filters=None):
        self.queries.append((table, tuple(select or ())))
        return self.rows


def test_a_credential_resolution_over_an_existing_row_never_enters_the_mint(
        monkeypatch):
    """⛔ THE "SELF-HEAL ON THE NEXT AUTHENTICATED REQUEST" CLAIM IS FALSE, AND
    THIS PINS THE REACHABILITY THAT SAYS SO (#14624 review ISSUE 2).

    `_provision_default_graph_record`'s docstring claimed that an org whose
    default graph was missed — *"the hook failed, or the graph predates it"* —
    *"is provisioned on its next authenticated request instead of waiting for an
    operator"*. Both of those populations HAVE the `public.graphs` row and LACK
    the cursor row, and `default_graph_for_credential` returns a PRESENT row's id
    at its own short-circuit **without** calling `ensure_default_graph_row`
    (whose only production caller is `_mint_default_graph_row_best_effort`). So
    that population is reached by no credential resolution at all; its remedy is
    `record_allocator_cli.py backfill`, which the corrected docstring now says.

    ⛔ THIS ROW PINS THE REACHABILITY, NOT THE PROSE — prose cannot be asserted.
    It reds if someone wires the present-row arm into the mint or the hook, which
    is exactly the change that would make the old sentence true: at that point
    the docstring and this test must change DELIBERATELY rather than the claim
    quietly becoming true while the plan still sends the operator to `backfill`.
    """
    from tortoise import supabase_control

    calls: list[tuple[str, str]] = []

    def fake_mint(cp, org_id, graph_name=None, *, context):
        calls.append((org_id, f"mint:{context}"))
        raise AssertionError(
            "a credential resolution over an EXISTING default row must not enter "
            "the mint: the self-heal claim belongs to the MISS path only")

    def fake_ensure(cp, org_id, graph_name=None):
        calls.append((org_id, "ensure_default_graph_row"))
        raise AssertionError(
            "a credential resolution over an EXISTING default row must not re-run "
            "the creation door — `backfill` is that population's remedy")

    def fake_hook(org_id, graph_id):
        calls.append((org_id, graph_id))
        raise AssertionError(
            "a credential resolution over an EXISTING default row must not reach "
            "the creation hook — that is the claim this row exists to falsify")

    monkeypatch.setattr(
        supabase_control, "_mint_default_graph_row_best_effort", fake_mint)
    monkeypatch.setattr(
        supabase_control, "ensure_default_graph_row", fake_ensure)
    monkeypatch.setattr(
        supabase_control, "_provision_default_graph_record", fake_hook)

    cp = _RowOnlyControlPlane([{"id": "g_default", "namespace": "org_ns_row"}])
    namespace, graph_id = supabase_control.default_graph_for_credential(
        cp, "org-14582", "org_ns", context="test")

    # ⛔ THE FIXTURE'S NAMESPACE DIFFERS FROM THE `graph_name` ARGUMENT (#14624
    # review P3-2). They used to be the SAME string, which made the pair
    # assertion below unfalsifiable: `return (graph_name, row["id"])` — the exact
    # regression the pair exists to prevent (#10452 F2) — produced the same
    # tuple, so mutating the return left the row GREEN. With them distinct, a
    # value taken from the argument instead of the one row read is visible.
    assert (namespace, graph_id) == ("org_ns_row", "g_default"), (
        "the pair must come off the one row read, unchanged")
    assert cp.queries == [("graphs", ("id", "namespace"))], (
        f"the credential path is ONE read and nothing else: {cp.queries}")
    assert calls == [], (
        f"the credential path re-entered the creation door/hook: {calls}")


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


# ── the backfill's ARGUMENT GATE (#14624 review 2) ─────────────────────────

#: Every guard below is a REFUSAL in `main()`, and none of them was exercised
#: anywhere before these rows (#14624 review 2). The `SystemExit` vs `return`
#: distinction is the non-vacuity: `ap.error` RAISES (exit 2), while a guard that
#: is not there lets the same argv continue to the DSN check, which only RETURNS
#: `EXIT_USAGE` — same code, no exception. So a reverted guard fails these rows.


def test_backfill_arm_requires_the_cutover_acknowledgement(monkeypatch) -> None:
    """⛔ `backfill --arm` ARMS A WHOLE ORG, so it needs the SAME
    `--acknowledge-cutover` that `arm` does (#14624 review 2a).

    THIS IS THE SAFETY GUARD. Revert `wants_arm` to `args.command == "arm"` and
    nothing in the tree reds while a population is armed with no operator having
    read the four preconditions (`CUTOVER_ACK_TEXT` exists to make them read).
    """
    from tools import record_allocator_cli as cli

    monkeypatch.delenv(cli._record_env_contract()[0], raising=False)
    assert cli.main(["backfill", "--org-id", "o", "--arm"]) == cli.EXIT_REFUSED
    # …and the ack is a GATE, not a blanket refusal: unset the DSN and the
    # acknowledged form gets PAST the gate and fails on what it lacks.
    assert cli.main(["backfill", "--org-id", "o", "--arm",
                     "--acknowledge-cutover"]) == cli.EXIT_USAGE, (
        "an acknowledged `backfill --arm` with no DSN should fail on the DSN, "
        "not on the ack gate")


def test_backfill_takes_no_graph_id_and_no_stated_floor() -> None:
    """`backfill` walks the WHOLE org and reads EACH graph's own journal
    enumeration (#14624 review 2b/2c).

    A `--graph-id` would let a population verb act on one graph while reporting
    a population run; a single stated `--floor`/`--journal` would seed every
    graph of an org to one number — the defect `sweep` already refuses by name.
    """
    from tools import record_allocator_cli as cli

    for argv in (["backfill", "--org-id", "o", "--graph-id", "g"],
                 ["backfill", "--org-id", "o", "--enumerated-floor", "3"],
                 ["backfill", "--org-id", "o", "--journal", "j.jsonl"]):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == cli.EXIT_USAGE, argv


def test_dry_run_and_arm_belong_to_backfill_only() -> None:
    """A flag a writing verb SILENTLY IGNORED is a lie about the one thing it
    promises (#14624 review 2d/2e): `--dry-run` on `seed` would write, and
    `--arm` on `seed` would allocate nothing while reading as if it had."""
    from tools import record_allocator_cli as cli

    for argv in (["seed", "--org-id", "o", "--graph-id", "g",
                  "--enumerated-floor", "0", "--dry-run"],
                 ["seed", "--org-id", "o", "--graph-id", "g",
                  "--enumerated-floor", "0", "--arm"]):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == cli.EXIT_USAGE, argv


def test_the_population_is_everything_not_allocating_not_just_missing_rows():
    """⛔ THE FALSE CLEAN (#14624 review 1, the P1) — pinned where it was made.

    `backfill` seeds without arming, and a `seeded` row allocates nothing
    (`ALLOCATION_SQL` is scoped to `active`). With `WHERE c.graph_id IS NULL` the
    graph the run had just touched LEFT the population, so the operator checklist
    (sweep → backfill → re-sweep to confirm → roll the flag) read **clean** over
    graphs that refuse every record write — silently, because the live door is
    fail-soft.

    The population a pre-flight enumerates is therefore everything NOT in the
    TERMINAL state. Both members matter and each carries its own remedy, which is
    why the mode is SELECTed beside the id rather than inferred from membership.

    NON-VACUITY: this is a static pin, and it is paired with live legs that drive
    the real database (`test_a_non_arm_backfill_does_not_read_as_a_finished_cutover`,
    `test_a_halted_graph_is_seeded_but_never_armed_by_backfill`). Reverting the
    predicate to `c.graph_id IS NULL` reds this row in the fast matrix and those
    two in `test-record-postgres`.
    """
    from tortoise import record_allocator as ra

    sql = " ".join(ra.UNCUT_GRAPHS_SQL.split())
    assert f"c.mode <> '{ra.MODE_ACTIVE}'" in sql, (
        "the population is narrowed back to 'no counter row', so a `seeded`/"
        f"`halted` graph vanishes from the pre-flight: {sql!r}")
    assert "c.mode" in sql.split("FROM")[0], (
        "the population must SELECT the counter's mode — membership alone cannot "
        f"tell `seeded` (heal with `arm`) from `halted` (heal with `reopen`): {sql!r}")
    assert ra.MODE_SEEDED not in sql and ra.MODE_HALTED not in sql, (
        "the predicate is spelled `<> active` on PURPOSE: an enumeration of the "
        "non-terminal modes would silently omit a future mode, whereas 'not the "
        "terminal one' cannot")


def test_backfill_never_arms_a_halted_counter(monkeypatch) -> None:
    """⛔ `arm` moves `seeded` → `active` ONLY, so an org-wide `backfill --arm`
    that met a `halted` graph raised out of the middle of the run — after
    committing every graph before it (#14624 review 1).

    No database: the seed's ANSWER is what the decision reads (`result.mode`), so
    it is monkeypatched to the halted state and the arm leg is a recorder that
    FAILS if it is called. The live leg is
    `test_a_halted_graph_is_seeded_but_never_armed_by_backfill`.
    """
    from tools import record_allocator_cli as cli
    from tortoise import record_allocator as ra

    answers = {
        _sql_key(ra.UNCUT_GRAPHS_SQL): [("g-halted", ra.MODE_HALTED)],
        _sql_key(ra.JOURNAL_MAX_SQL): [(2,)],
        _sql_key(ra.JOURNAL_COUNT_SQL): [(2,)],
    }
    monkeypatch.setattr(ra, "scope_tenant", lambda *a, **k: None)
    monkeypatch.setattr(
        ra, "seed",
        lambda *a, **k: SimpleNamespace(seq=2, mode=ra.MODE_HALTED,
                                       healed_tail=False, burned=False))

    def _arm_must_not_be_called(*a, **k):
        raise AssertionError(
            "`arm` was called on a halted counter: it refuses (`seeded` → "
            "`active` only) and would abort an org-wide run mid-way")

    monkeypatch.setattr(ra, "arm", _arm_must_not_be_called)

    payload = cli._cmd_backfill(_FakeRecordConn(answers),
                               _backfill_args("o", arm_graphs=True))
    assert (payload["considered"], payload["seeded"], payload["armed"]) == (1, 1, 0), payload
    entry = payload["graphs"][0]
    assert entry["counter_mode"] == ra.MODE_HALTED, entry
    assert entry["armed"] is False, entry
    assert "reopen" in entry["arm_refused"], (
        f"the entry must name the halt's own remedy: {entry}")


# ── the backfill ───────────────────────────────────────────────────────────

@pytestmark_live
def test_a_non_arm_backfill_does_not_read_as_a_finished_cutover(
        armed, record_schema):
    """⛔ `N considered / M seeded / K refused` — AND `seeded` IS NOT CUT OVER
    (#14624 review 1, the P1).

    The defect this pins, measured on the reviewed head: `backfill` seeds and does
    NOT arm by default, `seeded` allocates nothing (`ALLOCATION_SQL` is scoped to
    `active`) — and `UNCUT_GRAPHS_SQL` was `WHERE c.graph_id IS NULL`, so the graph
    the run had just "healed" DISAPPEARED from the population. The operator
    checklist in `CUTOVER_ACK_TEXT` (sweep → backfill → re-run sweep to confirm →
    roll the flag) therefore read **0 rows, clean** over graphs that refuse every
    record write, and the fail-soft door made the loss silent.

    This row asserts the two halves at once: the graph STAYS in `sweep`'s
    population with `counter_mode: seeded`, and only `--arm` moves it to the
    terminal state that legitimately leaves. Revert the SQL predicate to
    `c.graph_id IS NULL` and the `sweep`/re-run assertions below red.
    """
    from tools import record_allocator_cli as cli

    org, graph = _uncut_graph(record_schema, "backfill", positions=(1, 2, 3))
    args = _backfill_args(org)

    dry = cli._cmd_backfill(_conn(), _backfill_args(org, dry_run=True))
    # ⛔ A DRY RUN WROTE NOTHING, SO IT REPORTS NOTHING AS WRITTEN (#14624
    # review 3). This row used to assert `dry["seeded"] == 1` while the same
    # payload's per-graph `outcome` said `would_seed` — the summary and the detail
    # contradicted each other in the field an operator reads BEFORE arming
    # production. The projection is `would_seed`; `seeded`/`armed` stay 0.
    assert (dry["considered"], dry["seeded"], dry["refused"]) == (1, 0, 0), dry
    assert (dry["would_seed"], dry["would_arm"]) == (1, None), dry
    assert dry["graphs"][0]["outcome"] == "would_seed"
    assert dry["graphs"][0]["counter_mode"] is None, (
        "the dry run must say WHICH not-cut-over state the graph is in: `None` "
        "is born uncut, and it is healed by seed — not by reopen")
    assert _counter(record_schema, org, graph) is None, (
        "--dry-run wrote a row: the one thing it promises is that it does not")

    real = cli._cmd_backfill(_conn(), args)
    assert (real["considered"], real["seeded"], real["refused"]) == (1, 1, 0)
    assert real["armed"] is None, "no --arm was passed, so nothing was armed"
    assert _counter(record_schema, org, graph) == (3, "seeded"), (
        "a graph with 3 position rows must seed to 3 — the seed records the "
        "FLOOR; a SEEDED counter allocates nothing (`ALLOCATION_SQL` is "
        "`mode = 'active'`), so its next write is refused `cursor_not_active` "
        "until `--arm`")
    assert "finish_the_cutover" in real, (
        "a run that leaves graphs SEEDED must say so in words: `seeded` is not "
        "cut over, and the operator's next step is `--arm`")

    # ⛔ THE FALSIFIER. A graph left `seeded` is STILL not allocating, so it must
    # still be in the population a pre-flight enumerates. Before this fix this
    # read `count: 0` — the false CLEAN.
    swept = cli._cmd_sweep(_conn(), _sweep_args(org))
    assert swept["count"] == 1, (
        "`sweep` read CLEAN over a graph `backfill` left `seeded`: the population "
        f"must be everything NOT ALLOCATING, not everything missing a row: {swept}")
    assert swept["uncut_graphs"][0]["counter_mode"] == "seeded", swept

    # …and a re-run still SEES it (nothing to re-discover by hand), while the
    # counter does not move: idempotence is about the WRITE, not about hiding the
    # graph from the pre-flight.
    again = cli._cmd_backfill(_conn(), args)
    assert (again["considered"], again["seeded"], again["refused"]) == (1, 1, 0), (
        "a second run must still consider the graph it left seeded — reporting 0 "
        "is the blind population this row exists to catch", again)
    assert _counter(record_schema, org, graph) == (3, "seeded"), (
        "the backfill must be IDEMPOTENT: a second run may not move the counter")

    # --arm is what finishes it, and THEN the graph leaves the population for the
    # reason the terminal state exists.
    armed_run = cli._cmd_backfill(_conn(), _backfill_args(org, arm_graphs=True))
    assert (armed_run["considered"], armed_run["seeded"],
            armed_run["armed"]) == (1, 1, 1), armed_run
    assert _counter(record_schema, org, graph) == (3, "active")
    assert cli._cmd_sweep(_conn(), _sweep_args(org))["count"] == 0, (
        "an `active` graph allocates, so it is OUT of the population — this is "
        "the one zero that is a clean one")


@pytestmark_live
def test_a_halted_graph_is_seeded_but_never_armed_by_backfill(
        armed, record_schema):
    """⛔ `arm` MOVES `seeded` → `active` ONLY, so a halted graph in the (now
    wider) population must be SEEDED and reported, not armed (#14624 review 1).

    A `halted` counter allocates nothing either, so the population carries it. It
    is NOT healed by `arm`: a halt is a deliberate stop after a defect, with the
    opposite remedy (`reopen`), so an org-wide `backfill --arm` that called `arm`
    blindly would raise out of the middle of the run — after committing the graphs
    before it.
    """
    from tools import record_allocator_cli as cli
    from tortoise import record_allocator as ra

    org, graph = _uncut_graph(record_schema, "halted", positions=(1, 2))
    conn = _conn()
    # `halt` moves `active` → `halted` ONLY, so the graph must be cut over first —
    # which is what the arm run below does (and it also proves the happy path the
    # next assertion contrasts with).
    assert cli._cmd_backfill(conn, _backfill_args(org, arm_graphs=True))["armed"] == 1
    ra.halt(conn, org_id=org, graph_id=graph)
    conn.commit()
    assert _counter(record_schema, org, graph) == (2, "halted")

    payload = cli._cmd_backfill(_conn(), _backfill_args(org, arm_graphs=True))
    assert (payload["considered"], payload["seeded"], payload["armed"]) == (1, 1, 0), (
        "a halted graph must NOT be counted as armed", payload)
    assert payload["graphs"][0]["counter_mode"] == "halted", payload
    assert payload["graphs"][0]["armed"] is False, payload
    assert "reopen" in payload["graphs"][0]["arm_refused"], (
        "the entry must name the remedy the halt actually has: reopen", payload)
    assert _counter(record_schema, org, graph) == (2, "halted"), (
        "backfill may not lift a halt — that is the `reopen` verb's decision")
    assert cli._cmd_sweep(_conn(), _sweep_args(org))["count"] == 1, (
        "a halted graph is not cut over, so the pre-flight may not read clean "
        "over it")


def test_a_dry_run_reports_would_counts_and_a_real_run_reports_the_writes(
        monkeypatch):
    """⛔ THE DRY-RUN SUMMARY MAY NOT CLAIM A WRITE (#14624 review 3).

    `seeded`/`armed` are the fields an operator acts on, so a dry run that
    filled them in said "N graphs are healed" about a run that wrote nothing
    while the per-graph `outcome` in the SAME payload said `would_seed`.

    No database: the reporting is what is under test, so the seed's decision is
    monkeypatched out (its own legs are the live tests above, and re-deciding it
    here with a hand-written predicate is the second implementation this module
    exists to avoid). The connection is a recorder, so "wrote nothing" is
    MEASURED — zero `commit()`s and one run-level `rollback()`.
    """
    from tools import record_allocator_cli as cli
    from tortoise import record_allocator as ra

    graphs = ["g-1", "g-2"]
    answers = {
        # ⛔ TWO COLUMNS: `(graph_id, counter_mode)`. The second one is the state
        # the population now carries (`None` = born uncut, `seeded`/`halted` = a
        # row that allocates nothing) — a fake answering the OLD one-column shape
        # reds here rather than silently reporting `counter_mode: None` (#14624
        # review 1).
        _sql_key(ra.UNCUT_GRAPHS_SQL): [(graph, None) for graph in graphs],
        _sql_key(ra.JOURNAL_MAX_SQL): [(3,)],
        _sql_key(ra.JOURNAL_COUNT_SQL): [(3,)],
    }
    monkeypatch.setattr(ra, "scope_tenant", lambda *a, **k: None)
    monkeypatch.setattr(
        ra, "seed",
        lambda *a, **k: SimpleNamespace(seq=3, mode="seeded",
                                       healed_tail=None, burned=0))
    monkeypatch.setattr(ra, "arm", lambda *a, **k: 3)

    dry_conn = _FakeRecordConn(answers)
    dry = cli._cmd_backfill(dry_conn, _backfill_args("o", dry_run=True,
                                                     arm_graphs=True))
    assert dry["dry_run"] is True
    assert (dry["considered"], dry["seeded"], dry["refused"]) == (2, 0, 0), dry
    assert (dry["armed"], dry["would_seed"], dry["would_arm"]) == (0, 2, 2), dry
    assert [e["outcome"] for e in dry["graphs"]] == ["would_seed"] * 2
    assert [e.get("would_arm") for e in dry["graphs"]] == [True, True]
    assert dry_conn.commits == 0, "--dry-run committed: it wrote something"
    assert dry_conn.rollbacks == 1, (
        "the run-level rollback is the dry run's own promise; without it the "
        "per-graph savepoints were rolled back but the transaction was left open")

    # ⛔ NON-VACUITY: the SAME payload shape reports the writes on a real run, so
    # a `seeded` that were hard-coded to 0 would fail here.
    real_conn = _FakeRecordConn(answers)
    real = cli._cmd_backfill(real_conn, _backfill_args("o", arm_graphs=True))
    assert (real["seeded"], real["armed"]) == (2, 2), real
    # ⛔ THE ENTRY'S MODE IS THE COUNTER's mode AFTER `--arm` (#14624 review F4).
    # It used to be the SEED's answer, so a graph this very run cut over read
    # `mode: "seeded"` beside `armed: true` — one entry contradicting itself.
    # Grade the field: every armed entry must report `active`, which is `seed`'s
    # answer only if `arm` did nothing.
    assert {entry["mode"] for entry in real["graphs"]} == {"active"}, real
    assert (real["would_seed"], real["would_arm"]) == (None, None), real
    assert real_conn.commits == 2, "one transaction per graph"


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

    The default row's SHORT-CIRCUIT arm runs for a graph that ALREADY EXISTS and
    may carry a journal this hook never accounted for. ⛔ It is reached on a
    re-provision or a race, NOT on every credential resolution — a resolver that
    finds an existing row returns it without entering
    `ensure_default_graph_row` (`#14624` review 4, ISSUE 2;
    `_provision_default_graph_record`'s docstring and
    `test_a_credential_resolution_over_an_existing_row_never_enters_the_mint`
    state and pin the same). A hard-coded `enumerated_floor=0` makes
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


def _backfill_args(org: str, *, dry_run: bool = False,
                   arm_graphs: bool = False) -> SimpleNamespace:
    """`_cmd_backfill` takes the CONNECTION, so it never reads `args.dsn` — the
    field is here only so the namespace has the shape `main()` builds. Keeping it
    `None` also lets the REPORTING legs below run without a record database,
    which is all they are about (the seed's own decision has live legs)."""
    return SimpleNamespace(org_id=org, command="backfill", graph_id=None,
                           dsn=None, floor=None, journal=None,
                           dry_run=dry_run, arm_graphs=arm_graphs,
                           acknowledge_cutover=False, as_json=False)


def _sweep_args(org: str) -> SimpleNamespace:
    """The same shape for `sweep` — which reads no floor here, so every entry's
    `enumerated_floor` is `None` and the payload says UNKNOWN rather than a
    comparison it never made."""
    return SimpleNamespace(org_id=org, command="sweep", graph_id=None, dsn=None,
                           floor=None, journal=None, dry_run=False,
                           arm_graphs=False, acknowledge_cutover=False,
                           as_json=False)


def _sql_key(sql: str) -> str:
    """Whitespace-insensitive key, so a re-indented constant still matches."""
    return " ".join(str(sql).split())


class _FakeRecordConn:
    """A RECORDER, not a second record: it answers the backfill's reads and
    counts what the code asked it to do.

    `fetchone`/`fetchall` are keyed by the SQL the code executed, so a verb that
    read a different statement than this test set up gets an empty answer instead
    of a silent pass.
    """

    def __init__(self, answers: dict[str, list]) -> None:
        self._answers = {_sql_key(k): v for k, v in answers.items()}
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return _FakeRecordCursor(self._answers)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _FakeRecordCursor:
    def __init__(self, answers: dict[str, list]) -> None:
        self._answers = answers
        self._rows: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def execute(self, sql, params=None) -> None:
        self._rows = self._answers.get(_sql_key(sql), [])

    def fetchall(self) -> list:
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


def _conn():
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(_app_dsn())
    conn.autocommit = False
    return conn
