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
* the sites that create a graph BY DELEGATION (the HTTP create aliases, the
  default-graph mint, the credential resolver, the two SDK verbs and the signup
  route — seven in all) are pinned as
  REACHING their graph-creating door, transitively, so a refactor that drifts an
  alias off the route reds instead of shipping a graph with no cursor row — ⛔ and
  for the three SDK/signup sites that door is the UNHOOKED one, so their route is
  pinned as the census's record of the gap, not as coverage (#14587 P3-3: the PR
  body claimed this pin and nothing pinned it);
* a graph the record ALREADY knows is REFUSED, not seeded (`#12894`'s divergence
  rule) — `SEED_SQL`'s own guard decides it, there is no second predicate here;
* `/health` CANNOT report `record.ok: true` for a graph THIS PROCESS has
  OBSERVED uncut, and it publishes COUNTS ONLY (the endpoint is unauthenticated).
  ⛔ That is the whole claim, and it is a WINDOW rather than a census (#14587
  review P3-1): the registry has no runtime population source, so a graph that
  was already uncut when the process started has not been observed and is not
  counted until it writes. The measured form is pinned here
  (`test_health_cannot_report_ok_for_a_graph_it_has_observed_uncut`) and so is
  the bound it does NOT cross (`test_health_is_a_window_not_a_census`), so the
  caveat cannot be deleted as tidying;
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
from typing import Any

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
    # ⛔ #14587 review P2: these two MINT the same `kind='default'` row when the
    # org has none — `set_graph_name` from `PATCH /v1/graphs/{id}` with the
    # `default` sentinel, `set_graph_recording` from the recording override — and
    # neither called the hook. The minted row is a read HIT on every later
    # credential path, so `ensure_default_graph_row`'s hook is never reached for
    # that org: the graph's record writes are refused `cursor_not_active` for
    # ever while `write_live_record` (fail-soft by contract) reports success.
    ("tortoise/supabase_control.py", "set_graph_name",
     "_provision_default_graph_record"),
    ("tortoise/supabase_control.py", "set_graph_recording",
     "_provision_default_graph_record"),
)

#: How many hook calls each door's body must carry. `ensure_default_graph_row`
#: yields an id from THREE arms (active-row self-heal, mint, convergent adopt) and
#: hooks each; a refactor that collapses them to one silently drops two. The two
#: default-row minters hook their MINT and their CONVERGENT-ADOPT arm — the PATCH
#: arm writes to a row that already exists and mirrors `ensure_default_graph_row`'s
#: read-hit reach (see `_provision_default_graph_record`'s docstring).
_DOOR_HOOK_CALLS = {
    ("tortoise/hosted_api.py", "_provision_graph"): 1,
    ("tortoise/supabase_control.py",
     "ensure_default_graph_row"): 3,
    ("tortoise/supabase_control.py", "set_graph_name"): 2,
    ("tortoise/supabase_control.py", "set_graph_recording"): 2,
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

#: The creation hook's own name. An absence pin matches callees by this PREFIX
#: rather than by one exact spelling — the exact spelling the pin used to carry
#: alongside it (`provision_graph_record_`) matches nothing in the tree, so that
#: half was inert (`#14587` review, residual); see :func:`_hook_call_names`.
_HOOK_PREFIX = "provision_graph_record"

#: The DOOR-SIDE hook helpers — the two functions a door calls instead of the
#: writer's `record_writer.provision_graph_record` directly. They are NOT prefix
#: matches of :data:`_HOOK_PREFIX` (`_provision_default_graph_record` carries
#: `default` in the middle), so a mint-site pin built on that prefix reports every
#: hooked door as unhooked — measured while adding
#: :func:`test_every_graph_minting_site_is_declared`. Listed exactly, and their own
#: wiring is pinned separately by :data:`_HELPERS`.
_HOOK_HELPERS = (
    "_provision_graph_record",
    "_provision_default_graph_record",
)

#: The DELEGATED sites: an entry point that creates a graph WITHOUT minting the id
#: itself, so it is covered by the door it routes through. `(module, entry point,
#: door)` — and the route is RESOLVED through that module's own AST rather than
#: asserted here, because a name in a table is exactly the prose promise this pin
#: replaces. ⛔ THE TABLE ABOVE (`_DOORS`) IS NOT THIS PIN: it reads the DOOR's
#: body, and a delegating site that stops delegating leaves every door intact.
#: Measured on the #14587 review (P3-3): the PR body's census claimed A1/A2, B1,
#: C1/C2/C3 were "verified by AST pin, not by reading", and NOTHING pinned them —
#: the aliases were verified by reading a reviewer had done, which is not a pin and
#: does not survive the next refactor. So a `create_graph` alias that started minting
#: its own id (bypassing A/B/C) would have left the suite green while the graph it
#: created was born UNCUT — the defect this whole file exists to name.
_DELEGATED = (
    # A1 / A2 — the two HTTP create doors; both delegate to `_provision_graph` (A).
    ("tortoise/hosted_api.py", "create_org_graph", "_provision_graph"),
    ("tortoise/hosted_api.py", "create_graph", "_provision_graph"),
    # B1 / B2 — the default-graph mint, and the credential resolver that reaches it
    # on a read MISS only (two hops through `default_graph_for_credential`), which
    # is what makes this arm TRANSITIVE rather than a direct-call check.
    ("tortoise/supabase_control.py", "_mint_default_graph_row_best_effort",
     "ensure_default_graph_row"),
    ("tortoise/supabase_control.py", "default_graph_id_for_credential",
     "ensure_default_graph_row"),
    # C1 / C2 — the two SDK doors the census names: the org/team create that mints
    # the default graph, and the memory-graph verb. Both delegate to `_graph_create`
    # — the door this lane REFUSES to hook, and `_UNHOOKED` pins the WHOLE FUNCTION,
    # not one arm: NEITHER arm calls the hook (F1: the Supabase arm persists no row;
    # F3: the registry arm mints one the record's FK refuses). The route is pinned
    # because an alias that drifted off it would create a graph without reaching
    # that door at all.
    ("tortoise/sdk.py", "_org_create", "_graph_create"),
    ("tortoise/sdk.py", "create_memory_graph", "_graph_create"),
    # C3 — the signup route, the census's third C arm and `#14587` review (i), open
    # at every head until now: it is a graph-creating site the census NAMED and this
    # table did not pin, so an alias drifting off the route would have been invisible
    # here. Pinned under the SAME door as C1/C2, because "the census named seven
    # sites" and "the table held six" is a disagreement the next reader cannot
    # resolve. ⛔ C3, not "C4": the census's C range is C1/C2/C3 (the two SDK verbs
    # and the signup path), so a `C4` label would invent a fourth arm.
    ("tortoise/hosted_api.py", "agent_signup", "_graph_create"),
)


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


def _hook_call_names(node: ast.AST) -> list[str]:
    """Callee names in `node` that ARE the creation hook, matched by PREFIX.

    ⛔ The absence pin below cannot use an exact spelling: it previously also
    matched ``provision_graph_record_`` (a trailing underscore), a name that
    exists nowhere in the tree, so that clause was INERT — it could never fire
    (`#14587` review, residual). A name-based AST walk cannot see a hook that is
    renamed or aliased, so the honest pin is the PREFIX: any callee starting with
    the hook's own name counts as a call to it, and a differently-spelled hook is
    caught instead of sailed past.
    """
    found: list[str] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        # ⛔ THE DOOR-SIDE HELPERS COUNT TOO (#14587 review P3): a door that calls
        # `_provision_default_graph_record` IS calling the creation hook, and the
        # prefix alone does not match it (`default` sits in the middle). Without
        # this, wiring `sdk._graph_create` to the helper left this absence pin
        # GREEN over the exact arm it exists to keep unhooked — and that arm
        # persists no `graphs` row, so every create would fail the FK and pin
        # `/health` red over a healthy deployment.
        if name and (name.startswith(_HOOK_PREFIX) or name in _HOOK_HELPERS):
            found.append(name)
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


def test_the_hook_matcher_is_not_vacuous():
    """Non-vacuity for the matcher the absence pin below rests on (`#14587` N1).

    ⛔ An ABSENCE pin is green precisely when its matcher finds nothing, so a
    matcher that always answers `[]` silences it without reddening a thing —
    measured at the reviewed head: `return []` at the top of
    :func:`_hook_call_names` left the whole file `23 passed, 8 skipped`. The pin
    itself is not a no-op (a real hook call reds it); what was missing is any
    assertion that the matcher SEES a hook, which is what this is.
    """
    node = ast.parse(
        "def door():\n"
        "    provision_graph_record(org_id=o, graph_id=g)\n"
        "    _rw.provision_graph_record_v2(g)\n"
        "    other_call(g)\n").body[0]
    found = set(_hook_call_names(node))
    assert {"provision_graph_record", "provision_graph_record_v2"} <= found, (
        f"the hook matcher answered {sorted(found)} — it no longer sees the hook in "
        "either spelling a door's line takes. Every absence pin it backs is then "
        "green for ANY door, so a door that starts calling the hook sails past it")
    assert "other_call" not in found, (
        "an unrelated callee was matched as the hook — the matcher is no longer "
        "prefix-scoped to the hook's own name")
    # ⛔ DECLARED over-match, asserted so it cannot drift silently in either
    # direction: any callee whose name STARTS with the hook's name counts as a call
    # to it (fail-closed — a renamed hook is caught, at the price of an unrelated
    # prefix counting).
    assert _hook_call_names(ast.parse(
        "def door():\n    provision_graph_recorder_notes(g)\n").body[0]) == [
            "provision_graph_recorder_notes"]


def test_the_doors_that_cannot_be_hooked_stay_unhooked():
    """⛔ AN UNPINNED ABSENCE IS A FUTURE "FIX". Both of these are FINDINGS:
    hooking them manufactures a permanent false red on `/health` for a deployment
    that has no gap (see the module docstring's F1/F3).
    """
    hooked = []
    for rel, function in _UNHOOKED:
        node = _functions(rel).get(function)
        assert node is not None, f"{rel}::{function} is gone — re-run the census"
        names = _hook_call_names(node)
        if names:
            hooked.append(
                f"{rel}::{function} calls {sorted(set(names))}")
    assert not hooked, (
        f"{hooked} now calls the creation hook, and it must not: its Supabase "
        "arm persists no graph row (so the cursor row would be seeded for an id "
        "that never exists), and its registry arm creates a graph "
        "`public.graphs` never holds — `record_cursor`'s FK refuses every one, "
        "so each create logs a warning and the deployment reads "
        "`record.armed_but_uncut` forever. If this door is to be hooked, the "
        "graph-row mint has to land first")


# ── DB-free: the MINTING sites, enumerated from the INSERT and not from names ──
#
# ⛔ WHY THIS EXISTS. `_DOORS` above can only pin a door SOMEONE LISTED, and the
# census that produced it searched for NAMES (`create_graph`, `_graph_create`,
# `insert_graph`, `ensure_default_graph_row`, `create_memory_graph`, …).
# `supabase_control.set_graph_name` and `set_graph_recording` mint a
# `kind='default'` row through an inline `cp.query("graphs", method="POST", …)`,
# so no name search could see them — and at the reviewed head they created
# graphs with no cursor row, the exact defect this PR exists to close
# (#14587 review P2). A pin that reds on a NEW minting arm is the only form that
# cannot be re-opened by the next arm nobody lists.

#: Every site in `tortoise/**` that INSERTS a `public.graphs` row, keyed by the
#: site's own identity, with what covers it:
#:   * `hooked`    — the site's body calls the creation hook itself;
#:   * `delegated` — a mint HELPER: it writes the row for a caller, so every
#:                   caller of it must be a hooked door (asserted below).
_GRAPH_MINT_SITES = {
    # The one mint helper. Its two production callers are `_DOORS` entries
    # (`hosted_api._provision_graph`, `supabase_control.ensure_default_graph_row`),
    # which is what makes this entry a DECLARATION and not an exemption.
    #
    # ⛔ KEYED BY SITE, NOT BY FUNCTION (#14587 review P2): `(module, function,
    # ORDINAL)` — the ordinal is the site's position among the INSERTs of that
    # function, in source order. Keyed by function alone, a SECOND mint added
    # inside an already-`hooked` function was INVISIBLE, because the hook the
    # existing arm calls satisfied the whole function body: measured by adding a
    # second unhooked `cp.query("graphs", method="POST", …)` beside the hooked one
    # in `set_graph_name` — this pin and its non-vacuity test both stayed green.
    # The ordinal makes a new site a new KEY, so it cannot ride a neighbour's
    # declaration, and the pin's promise ("the next arm added anywhere reds
    # HERE") is what the code now does.
    ("tortoise/supabase_control.py", "insert_graph", 0): "delegated",
    ("tortoise/supabase_control.py", "set_graph_name", 0): "hooked",
    ("tortoise/supabase_control.py", "set_graph_recording", 0): "hooked",
}


def _calls_a_door_hook(node: ast.AST) -> bool:
    """Does `node` call either door-side hook helper (:data:`_HOOK_HELPERS`)?"""
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name in _HOOK_HELPERS:
            return True
    return False


def _is_graphs_insert(node: ast.AST) -> bool:
    """Does this node INSERT a `public.graphs` row through the control plane?

    The shape is `cp.query("graphs", method="POST", json_body=…)` — matched
    structurally (table + method as constants), so a differently-named
    transport is still seen and a POST to any other table is not.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr == "query"):
        return False
    table = node.args[0] if node.args else next(
        (kw.value for kw in node.keywords if kw.arg == "table"), None)
    if not (isinstance(table, ast.Constant) and table.value == "graphs"):
        return False
    method = next(
        (kw.value for kw in node.keywords if kw.arg == "method"), None)
    return isinstance(method, ast.Constant) and method.value == "POST"


def _enclosing_sites(predicate) -> dict[tuple[str, str], list[ast.AST]]:
    """`{(module, INNERMOST enclosing function): [matching nodes, source order]}`
    for every call in `tortoise/**` that `predicate` accepts.

    Innermost on purpose: a mint inside a nested helper is reported as that
    helper, which is the thing a reader has to classify, not its outer shell.
    The LIST is what lets :func:`_mint_sites` key site by site (see
    :data:`_GRAPH_MINT_SITES`): one function can carry several INSERTs, and
    collapsing them to one key is how a new arm hid behind a neighbour's hook.
    """
    per: dict[tuple[str, str], list[ast.AST]] = {}

    def walk(rel: str, node: ast.AST, current: ast.AST | None) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            current = node
        if predicate(node):
            assert current is not None, (
                "a `public.graphs` INSERT outside any function — this pin cannot "
                "attribute it to a door")
            per.setdefault((rel, current.name), []).append(node)
        for child in ast.iter_child_nodes(node):
            walk(rel, child, current)

    for path in sorted((_ROOT / "tortoise").rglob("*.py")):
        rel = str(path.relative_to(_ROOT))
        walk(rel, ast.parse(path.read_text()), None)
    return per


def _mint_sites() -> dict[tuple[str, str, int], ast.AST]:
    """Every `public.graphs` INSERT, keyed BY SITE — `(module, function, ordinal)`.

    The ordinal is source order within the function, so adding a second mint to a
    function whose first mint is already declared produces a NEW key that the
    declaration table does not hold, and the pin reds. That is the whole point of
    the third component: function-level keys let a new arm inherit its
    neighbour's hook (measured, #14587 review P2).
    """
    return {(rel, fn, idx): node
            for (rel, fn), nodes in _enclosing_sites(_is_graphs_insert).items()
            for idx, node in enumerate(nodes)}


def _call_sites(callee: str) -> set[tuple[str, str]]:
    """`{(module, innermost enclosing function)}` for every `<callee>(…)` call in
    `tortoise/**`."""

    def is_call(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        return name == callee

    return set(_enclosing_sites(is_call))


def test_every_graph_minting_site_is_declared():
    """⛔ THE COMPLETENESS PIN: no `public.graphs` INSERT may exist unclassified.

    A mint that is not hooked (and not a helper whose every caller is) creates a
    graph with no cursor row — the user's writes succeed, the record accumulates
    nothing for that graph, and `/health` reads green until the graph's first
    refused write. This enumerates the INSERTs themselves, so the next arm added
    anywhere under `tortoise/` reds HERE instead of shipping as a silent gap.

    Two closes for a red: HOOK it, or declare it a named residual with a tracked
    issue and its reason (which is what `_UNHOOKED` does for the doors that must
    NOT be hooked). What is not a close is leaving it out of the table.
    """
    measured = _mint_sites()
    undeclared = sorted(set(measured) - set(_GRAPH_MINT_SITES))
    assert not undeclared, (
        f"these functions INSERT a `public.graphs` row and are NOT in "
        f"_GRAPH_MINT_SITES: {undeclared}. If it MINTS a graph, every record "
        "that graph writes is refused `cursor_not_active` until something seeds "
        "it, and nothing raises (write_live_record is fail-soft by contract) — "
        "so either call the creation hook on the arm that minted the row, or "
        "declare it a residual with a tracked issue and say why hooking it is "
        "wrong")
    stale = sorted(set(_GRAPH_MINT_SITES) - set(measured))
    assert not stale, (
        f"_GRAPH_MINT_SITES declares sites that no longer INSERT a `public.graphs` "
        f"row: {stale}. A stale declaration is a pin on nothing — update the "
        "table to the measured set rather than leaving a name that outlived its "
        "mint")

    unhooked = [f"{rel}::{fn}#{idx}" for (rel, fn, idx), _node in measured.items()
                if _GRAPH_MINT_SITES[(rel, fn, idx)] == "hooked"
                and not _calls_a_door_hook(_functions(rel)[fn])]
    assert not unhooked, (
        f"these `public.graphs` minters are declared `hooked` and call no "
        f"creation hook: {unhooked} — the declaration is now the lie the defect "
        "used to be")

    doors = {(rel, fn) for rel, fn, _callee in _DOORS}
    unprovisioned = sorted(
        site for (rel, fn, _idx), _node in measured.items()
        if _GRAPH_MINT_SITES[(rel, fn, _idx)] == "delegated"
        for site in _call_sites(fn) - doors)
    assert not unprovisioned, (
        f"these functions call a `public.graphs` mint HELPER without being a "
        f"hooked door themselves: {unprovisioned}. The helper writes the row; the "
        "caller owes the cursor, so a new caller of it is a new unhooked mint")


def _first_call(source: str) -> ast.AST:
    """The first `Call` in `source` — the node :func:`_is_graphs_insert` takes."""
    return next(node for node in ast.walk(ast.parse(source))
                if isinstance(node, ast.Call))


def test_the_mint_census_is_not_vacuous():
    """Non-vacuity for :func:`test_every_graph_minting_site_is_declared`.

    That pin is green when it finds NOTHING, so a matcher that answers `{}` (a
    refactor that stops recognising the INSERT shape) silences it without
    reddening a thing — the failure mode the hook matcher's own non-vacuity pin
    was added for. These are the four shapes that matter: the tree's own two, and
    the two that must NOT count.
    """
    assert _is_graphs_insert(_first_call(
        "def f(cp):\n    cp.query('graphs', method='POST', json_body=row)\n"
    )), "the inline one-positional shape is no longer recognised"
    assert _is_graphs_insert(_first_call(
        "def f(cp):\n    cp.query(\n        'graphs', method='POST',\n"
        "        json_body={})\n"
    )), "the multi-line shape is no longer recognised"
    assert not _is_graphs_insert(_first_call(
        "def f(cp):\n    cp.query('graphs', method='PATCH', json_body={})\n"
    )), "a PATCH is not a mint and must not be counted as one"
    assert not _is_graphs_insert(_first_call(
        "def f(cp):\n    cp.query('api_keys', method='POST', json_body={})\n"
    )), "a POST to another table must not be counted as a graph mint"
    # And the census really does see the two arms the #14587 review found.
    measured = set(_enclosing_sites(_is_graphs_insert))
    assert {("tortoise/supabase_control.py", "set_graph_name"),
            ("tortoise/supabase_control.py", "set_graph_recording"),
            ("tortoise/supabase_control.py", "insert_graph")} <= measured, (
        f"the census answered {sorted(measured)} — it no longer sees the default-row "
        "mints, which is exactly how those two arms went unhooked and unnoticed")


class _MintControlPlane:
    """The three control-plane calls `set_graph_name` / `set_graph_recording` make.

    DB-free on purpose: the pins below are about WHICH ID the hook is called for,
    and a real record DSN would make them skip in the fast matrix.
    """

    def __init__(self, *, graphs=None, org_graph_name="team_mint",
                 post_error=None, adopt_row=None):
        self.graphs = [dict(g) for g in (graphs or [])]
        self.org_graph_name = org_graph_name
        self.post_error = post_error
        self.adopt_row = adopt_row
        self.posts = 0

    def query(self, table, *, select=None, filters=None, method=None,
              json_body=None):
        if table == "organizations":
            return [{"graph_name": self.org_graph_name}]
        assert table == "graphs", f"unexpected control-plane read: {table}"
        if method == "POST":
            self.posts += 1
            if self.post_error is not None:
                # A concurrent writer won: its row appears between this call's
                # read and this POST, which is the convergent-adopt arm.
                if self.adopt_row is not None:
                    self.graphs.append(dict(self.adopt_row))
                raise self.post_error
            self.graphs.append(dict(json_body or {}))
            return None

        def matches(row: dict) -> bool:
            for column, op, value in (filters or []):
                got = row.get(column)
                if op == "eq" and got != value:
                    return False
                if op == "neq" and got == value:
                    return False
            return True

        rows = [r for r in self.graphs if matches(r)]
        if method == "PATCH":
            for row in rows:
                row.update(json_body or {})
            return None
        return [{key: r.get(key) for key in (select or ["id"])} for r in rows]


def _record_hook(monkeypatch) -> list[tuple[str, str]]:
    from tortoise import supabase_control as sc

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(sc, "_provision_default_graph_record",
                        lambda org, gid: calls.append((org, gid)))
    return calls


def test_a_rename_that_mints_the_default_row_provisions_it(monkeypatch):
    """#14587 review P2, the live endpoint: `SET name` on an org with no row."""
    from tortoise import supabase_control as sc

    calls = _record_hook(monkeypatch)
    cp = _MintControlPlane()
    assert sc.set_graph_name(cp, "org-1", "default", "Team HQ") is True
    assert cp.posts == 1, "the rename no longer mints a row — this leg is stale"
    minted = cp.graphs[0]["id"]
    assert calls == [("org-1", minted)], (
        f"the rename minted graph {minted} and the hook was called with {calls} — "
        "a default graph minted by a rename has no cursor row, is a read HIT for "
        "every later credential path, and its record writes are refused "
        "`cursor_not_active` for ever")


def test_a_recording_override_that_mints_the_default_row_provisions_it(
        monkeypatch):
    """#14587 review P2, the other live endpoint."""
    from tortoise import supabase_control as sc

    calls = _record_hook(monkeypatch)
    cp = _MintControlPlane()
    assert sc.set_graph_recording(cp, "org-1", "default", True) is True
    assert cp.posts == 1, "the override no longer mints a row — leg stale"
    minted = cp.graphs[0]["id"]
    assert calls == [("org-1", minted)]


def test_a_convergent_mint_provisions_the_row_that_became_durable(monkeypatch):
    """The adopt arm provisions the WINNER's id, not the one this call invented."""
    from tortoise import supabase_control as sc

    winner = {"id": "g_winner", "org_id": "org-1", "kind": "default",
              "status": "active", "namespace": "team_mint", "recording": None}
    calls = _record_hook(monkeypatch)
    cp = _MintControlPlane(post_error=RuntimeError("duplicate key"),
                          adopt_row=winner)
    assert sc.set_graph_name(cp, "org-1", "default", "Team HQ") is True
    assert calls == [("org-1", "g_winner")], (
        f"the adopt arm provisioned {calls} instead of the durable winner")

    calls.clear()
    cp = _MintControlPlane(post_error=RuntimeError("duplicate key"),
                          adopt_row=winner)
    assert sc.set_graph_recording(cp, "org-1", "default", False) is True
    assert calls == [("org-1", "g_winner")]


def test_a_default_row_that_already_exists_is_not_re_provisioned(monkeypatch):
    """The PATCH arm is bounded: an existing row is the read-hit population.

    ⛔ THE NEGATIVE HALF of the two pins above, and the one that keeps this claim
    from overreaching a THIRD time: the hook runs on the arms that MINT (or adopt
    a mint), never on a row that already exists. That population is the one
    `_provision_default_graph_record`'s docstring names — healed by the operator
    verb or observed on the graph's next write, not healed here.
    """
    from tortoise import supabase_control as sc

    row = {"id": "g_existing", "org_id": "org-1", "kind": "default",
           "status": "active", "namespace": "team_mint", "recording": None}
    calls = _record_hook(monkeypatch)
    cp = _MintControlPlane(graphs=[row])
    assert sc.set_graph_name(cp, "org-1", "default", "Team HQ") is True
    assert sc.set_graph_recording(cp, "org-1", "default", False) is True
    assert cp.posts == 0 and calls == [], (
        "an existing default row is a read HIT — the mint arms are the only ones "
        f"this change hooks, and {calls} says otherwise")
    assert cp.graphs[0]["name"] == "Team HQ"
    assert cp.graphs[0]["recording"] is False


def _same_module_call_graph(rel: str) -> dict[str, set[str]]:
    """Every function in `rel` mapped to the names its body calls.

    Structural (`ast`, via :func:`_call_names`), so a mention in a docstring or a
    comment cannot satisfy a route — the same reason :func:`_call_names` is
    structural. Names rather than resolved objects because the only thing a route
    needs is "a call to a function defined in this module", which is what
    :func:`_reaches` follows.

    ⛔ THE LIMITS, stated so a later reader does not read more into it than it does:
    same-module resolution, by CALL NAME, with no dataflow and no control flow. A
    full interprocedural reachability query is CodeQL's or Semgrep's job (CodeQL's
    control-flow reachability is the documented answer to "can X be reached without
    passing through Y"); this file deliberately keeps the stdlib-only AST form that
    `_call_names`/`_hook_calls` already use, because a new analyser dependency for one
    route pin is a bigger blast radius than the defect, and the pin only has to red
    when a route is CUT, which a name walk sees.
    """
    tree = ast.parse(_source(rel))
    return {node.name: _call_names(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _reaches(graph: dict[str, set[str]], start: str, target: str) -> bool:
    """Can `start` reach a call to `target` through functions in its own module?"""
    seen: set[str] = set()
    frontier = [start]
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        for callee in graph.get(name, ()):
            if callee == target:
                return True
            if callee in graph:
                frontier.append(callee)
    return False


def test_every_delegating_creation_site_still_reaches_its_creation_door():
    """⛔ THE CENSUS'S OTHER HALF — the seven sites that create a graph BY DELEGATION.

    ⛔ The name says CREATION door, not "hooked door", and that is the fix to the
    `#14587` review's P3-2: three of these rows (the census's C1/C2/C3 — the two SDK
    verbs and the signup route) reach `sdk._graph_create`, which the sibling pin
    `test_the_doors_that_cannot_be_hooked_stay_unhooked` contradicts — a name
    promising a HOOKED door against a test asserting the opposite.

    `_DOORS` pins the two sites that MINT an id AND hook it; these are the sites
    that reach one of them — or, for the three SDK/signup rows, reach the third
    minting door, `sdk._graph_create`, which `_UNHOOKED` pins the opposite way.
    They are the quiet half: a door keeps calling the hook perfectly while an
    alias drifts off the route, and the graph the alias creates is born uncut with no
    test red (the failure mode the module docstring measures). The PR's census claimed
    them as "verified by AST pin" when the review found nothing pinned them (#14587
    P3-3), so the pin is here rather than in the claim.

    ⛔ TRANSITIVE ON PURPOSE: `default_graph_id_for_credential` does not call the mint,
    it calls `default_graph_for_credential` which does. A direct-call check would red
    on the correct code or be quietly deleted; the reachability reader follows the
    route however it is spelled and however many hops it takes.
    """
    graphs: dict[str, dict[str, set[str]]] = {}
    missing: list[str] = []
    for rel, entry, door in _DELEGATED:
        if rel not in graphs:
            graphs[rel] = _same_module_call_graph(rel)
        graph = graphs[rel]
        assert entry in graph, (
            f"{rel}::{entry} is gone — the census found it as a delegating creation "
            f"site; re-run the census (`rg -n 'create_graph|insert_graph|"
            f"ensure_default_graph_row|_graph_create' tortoise/`) and pin the new name "
            f"rather than deleting this check")
        if not _reaches(graph, entry, door):
            missing.append(f"{rel}::{entry} no longer reaches {door}")
    assert not missing, (
        f"these graph-creating sites stopped reaching their creation door: {missing}. "
        "A graph they create is born UNCUT — the record refuses every write for it "
        "(`cursor_not_active`) while `write_live_record`, fail-soft by contract, reports "
        "success, so nothing reds and the loss only shows at a rebuild. Route it back "
        "through the door the census named — `_provision_graph` (A), "
        "`ensure_default_graph_row` (B) or `_graph_create` (C) — or, if it now mints "
        "an id of its own, hook it and add it to `_DOORS`. ⛔ A route to (C) is NOT a "
        "hooked route: `_graph_create` is pinned UNHOOKED (`_UNHOOKED`, F1/F3), so "
        "reaching it records the gap rather than curing it")


def test_the_delegation_pin_reads_the_route_and_not_the_names():
    """Non-vacuity for the arm above, and the two ways it could be decoration.

    A pin that reads a TABLE of names passes whatever the code does; a reachability
    reader that ignores hops reds on the correct multi-hop site. Both are asserted
    here, against hand-built graphs, and the REAL module is checked for the hop count
    the transitive reader exists for.
    """
    assert not _reaches({"create_org_graph": {"_mint_graph_key"}},
                        "create_org_graph", "_provision_graph"), (
        "a site that calls some OTHER helper was read as reaching the door — the "
        "arm would be satisfied by the name appearing anywhere")
    assert _reaches({"a": {"b"}, "b": {"c"}, "c": {"_provision_graph"}},
                    "a", "_provision_graph"), (
        "the reader lost a route three hops deep, so the transitive sites it exists "
        "for would red on correct code")
    # ⛔ THE REAL MULTI-HOP SITE, measured rather than assumed: if this ever becomes a
    # direct call the transitive arm stops being exercised by the tree it grades.
    graph = _same_module_call_graph("tortoise/supabase_control.py")
    assert "ensure_default_graph_row" not in graph[
        "default_graph_id_for_credential"], (
        "`default_graph_id_for_credential` now calls the mint directly — that is fine, "
        "but the transitive arm in the sibling test is then no longer measured on real "
        "code: point it at another multi-hop site or state that the reader is no longer "
        "exercised here")
    assert _reaches(graph, "default_graph_id_for_credential",
                    "ensure_default_graph_row")


#: The census `_DELEGATED` records, as `(module, entry point, door)` IDENTITIES.
#:
#: ⛔ A COUNT IS NOT A FLOOR (`#14587` review P3-3, measured): the guard this
#: replaces asserted `len(_DELEGATED) >= 7`, which a SUBSTITUTION closes — replacing
#: one row with a duplicate of another keeps the length at 7 while a site drops out
#: of the route pin with nothing red.
#: ⛔ AND THE DOOR IS PART OF THE IDENTITY (measured in this delta's own review):
#: the route test grades whatever door the row NAMES, so a table whose third column
#: was edited to a callee the entry happens to call DIRECTLY (`_graph_quota_gate`)
#: stayed GREEN on both halves while `create_org_graph` had stopped reaching
#: `_provision_graph` — the cut route the pin exists to catch. Pinning the door cell
#: here means such an edit is a census edit, which is the review it should be.
_DELEGATION_CENSUS = frozenset({
    ("tortoise/hosted_api.py", "create_org_graph", "_provision_graph"),
    ("tortoise/hosted_api.py", "create_graph", "_provision_graph"),
    ("tortoise/supabase_control.py", "_mint_default_graph_row_best_effort",
     "ensure_default_graph_row"),
    ("tortoise/supabase_control.py", "default_graph_id_for_credential",
     "ensure_default_graph_row"),
    ("tortoise/sdk.py", "_org_create", "_graph_create"),
    ("tortoise/sdk.py", "create_memory_graph", "_graph_create"),
    ("tortoise/hosted_api.py", "agent_signup", "_graph_create"),
})


def test_the_delegation_table_matches_the_census_by_identity():
    """Non-vacuity for the table the route pin ITERATES (`#14587` review (ii), (P3-3)).

    ⛔ The arm above is green when `_DELEGATED` is empty, because a pin that loops
    over nothing asserts nothing — measured at the reviewed head: `_DELEGATED = ()`
    left both delegation tests green (`2 passed, 29 deselected`) while the census's
    other half silently stopped being checked. So the table is asserted against the
    census.

    ⛔ By IDENTITY, not by length: a `len(...) >= 7` floor is green for a table that
    swapped one site for a DUPLICATE of another (`#14587` review P3-3), which is the
    same silent drop the floor exists to catch, one edit away. And the DOOR cell is
    part of the identity, because the route test grades the door the row NAMES
    (measured: a third-column edit to a callee the entry calls directly left both
    halves green while the real route was cut). Adding a site to the table therefore
    means adding it to `_DELEGATION_CENSUS` above — the census is what the entry is
    graded against, and it is the only place a site enters.
    """
    pinned = set(_DELEGATED)
    assert pinned == _DELEGATION_CENSUS, (
        f"`_DELEGATED` is {sorted(pinned)} but the census is "
        f"{sorted(_DELEGATION_CENSUS)} — the route pin only checks the entries it is "
        "given, so a table that lost an entry, substituted one for a DUPLICATE of "
        "another, was emptied, or repointed an entry at a door it calls directly "
        "reports PASS for a site nobody looks at any more. Re-census "
        "(`rg -n 'create_graph|insert_graph|ensure_default_graph_row|_graph_create' "
        "tortoise/`) and fix the table — do not edit the census to match a typo")


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


def test_health_cannot_report_ok_for_a_graph_it_has_observed_uncut(monkeypatch):
    """FALSIFIER (c). Red before this change: `armed_but_uncut` did not exist and
    `ok` stayed `true` for exactly this state — production's measured shape.

    ⛔ THE NAME STATES THE MEASURED FORM, NOT THE DEPLOYMENT-GLOBAL ONE (#14587
    review P3-1). This test calls `note_uncut_graph(...)` and asserts the fold, so
    what it establishes is "a graph this process has OBSERVED uncut cannot sit
    under `ok: true`". The global form — "no armed deployment carrying an uncut
    graph reads ok" — is NOT established here and is NOT true: the registry is process
    state with no population source, so an uncut graph that has not written since
    the process started is invisible until it does. The honest bound is pinned by
    `test_health_is_a_window_not_a_census`; the name said the stronger thing and
    is how the next lane deletes the caveats.
    """
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


def test_health_is_a_window_not_a_census(monkeypatch):
    """⛔ THE HONEST HALF OF FALSIFIER (c), PINNED SO IT CANNOT BE DELETED.

    #14587 review P3-1: the field, the test name and one module-header bullet all
    stated the deployment-global form ("no armed deployment carrying an uncut
    graph reads `ok: true`") while the code and the other tests measure the
    OBSERVED form. `armed_but_uncut` has NO runtime population source — it is
    process state fed from the write path and the hook — so a process that starts
    with a graph that is already uncut and has not written since reports `0` and
    `ok: true` over a real gap. That is a WINDOW, not a census, and the field's
    own docstring says so.

    This test asserts the BOUND rather than the gap: a fresh registry must read 0
    and `ok: true`. If a later change gave the field a census (an enumeration at
    startup, a periodic probe), this test fails — which is the point, because
    then the docstring's caveat would be the stale half and the wording would have
    to move to the other one deliberately instead of by tidying.
    """
    rw.reset_uncut_graphs()
    try:
        block = _health(monkeypatch, dsn="postgresql://u:p@127.0.0.1:1/record")
        assert block["armed"] is True
        assert block["armed_but_uncut"] == 0, (
            "`armed_but_uncut` claims a population it does not measure — it is a "
            "WINDOW fed by observations, so an unobserved graph cannot appear in "
            "it. A non-zero count here means the field became a census, and the "
            "docstring/tests that say otherwise are then wrong")
        assert block["ok"] is True
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


# ── DB-free: the hook's REACH, and the two feeders that keep it honest ─────
#
# #14587 review: P1-1 (the hook fired on an arm whose graph `public.graphs`
# never holds) and P1-3 (the write-path feeder and the allocator's `active`
# short-circuit could both be deleted with the suite green). Each pin below
# names the mutant it reds on.


def _parent_map(node: ast.AST) -> dict[ast.AST, ast.AST]:
    """`{child: parent}` for one parsed function."""
    parents: dict[ast.AST, ast.AST] = {}
    for sub in ast.walk(node):
        for child in ast.iter_child_nodes(sub):
            parents[child] = sub
    return parents


def _enclosing_if_tests(node: ast.AST,
                        parents: dict[ast.AST, ast.AST]) -> list[ast.AST]:
    """Every `if` TEST above `node`, nearest first — the guards around a line."""
    tests: list[ast.AST] = []
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.If):
            tests.append(cur.test)
    return tests


def _names(node: ast.AST) -> set[str]:
    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}


def _calls_to(node: ast.AST, callee: str) -> list[ast.Call]:
    out: list[ast.Call] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name == callee:
            out.append(sub)
    return out


def test_the_hook_is_scoped_to_the_arm_that_persisted_a_graphs_row():
    """⛔ #14587 review P1-1 — THE HOOK MUST NOT FIRE ON THE REGISTRY ARM.

    `record_cursor` carries `FOREIGN KEY (org_id, graph_id) REFERENCES
    public.graphs (org_id, id)`. `insert_graph` persists such a row on the
    Supabase arm; `sdk._graph_create` does NOT (it returns a Falkor `:Graph` id,
    `record_writer.record_tenant_for` states registry graphs are never in
    `public.graphs`). `_provision_graph` took the registry branch and still
    called the hook, so on any selfhost/registry install that sets
    `TORTOISE_RECORD_DSN` EVERY graph create failed the FK (`23503`, or `42P01`
    with no graphs table), counted `provision_failed`, and — because a registry
    tenant never reaches `record_owns_position` to retract it — pinned `/health`
    at `ok: false` for the life of the process over a deployment with NO gap.

    Mutants this reds: (a) drop the `if control_plane_owns_graph:` guard → the
    call has no enclosing `if` naming the flag; (b) set the flag True in the
    `else` (registry) arm → its assignment is not guarded by
    `is_supabase_enabled()`; (c) never set it True → the `True` assignment is
    gone. The `_UNHOOKED` pin cannot see any of them: it grades
    `sdk._graph_create`'s own body, and this is a caller one frame up that
    composes the same id into the same hook.
    """
    rel, door = "tortoise/hosted_api.py", "_provision_graph"
    func = _functions(rel).get(door)
    assert func is not None, f"{rel}::{door} is gone — re-run the door census"
    parents = _parent_map(func)
    calls = _calls_to(func, "_provision_graph_record")
    assert len(calls) == 1, (
        f"`{door}` calls `_provision_graph_record` {len(calls)}× — expected "
        f"exactly one gated call site")
    guards: set[str] = set()
    for test in _enclosing_if_tests(calls[0], parents):
        guards |= _names(test)
    assert "control_plane_owns_graph" in guards, (
        "the creation hook is not gated on `control_plane_owns_graph`: it runs "
        "for the REGISTRY arm too, where the graph id is a Falkor `:Graph` node "
        "that `public.graphs` never holds — `record_cursor`'s FK refuses every "
        "such create, and a registry tenant never reaches the write path that "
        "would retract the entry, so `/health` reads `ok: false` forever")
    trues = [n for n in ast.walk(func)
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name)
                     and t.id == "control_plane_owns_graph" for t in n.targets)
             and isinstance(n.value, ast.Constant) and n.value.value is True]
    assert len(trues) == 1, (
        f"`control_plane_owns_graph` is set True {len(trues)}× in `{door}` — "
        f"it must be set True exactly once, and only where `insert_graph` "
        f"persisted the row")
    assert any(_calls_to(test, "is_supabase_enabled")
               for test in _enclosing_if_tests(trues[0], parents)), (
        "`control_plane_owns_graph = True` is not inside the "
        "`if is_supabase_enabled():` arm — the flag that gates the hook is now "
        "set by the arm whose graph has no `public.graphs` row")


class _RecordingCursor:
    """The cursor half of :class:`_FakeConn`: records what was EXECUTED.

    ⛔ IT EXISTS BECAUSE THE HOOK NOW ISSUES A STATEMENT OF ITS OWN (#14587
    review P3-2): `_bound_provision_statements` scopes a transaction-local
    `statement_timeout` on the connection before the allocator runs, so a fake
    connection the hook opens must be able to answer `cursor()`. Without this the
    only way to keep these tests green would be to skip the bound in tests, which
    is how the claim and the guard drift apart again.
    """

    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    def execute(self, sql: str, params: Any = None) -> None:
        self._log.append((sql, params))

    def fetchone(self) -> None:
        return None

    def close(self) -> None:
        pass


class _FakeConn:
    """What `record_writer` accepts as a connection: commit/rollback/close."""

    def __init__(self) -> None:
        self.committed = 0
        self.closed = 0
        self.statements: list[tuple[str, Any]] = []

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self.statements)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed += 1


def test_the_write_path_observation_feeds_the_registry(monkeypatch):
    """⛔ #14587 review P1-3 (first instance) — THE FEEDER HAD NO PIN.

    `record_owns_position` is the ONLY observation that runs in the normal course
    (`record_owns_position` reads the graph's cursor row on every armed write),
    so it is what keeps `armed_but_uncut` honest for the graphs that PREDATE the
    hook, the graphs a failed hook left behind, and every graph an operator later
    breaks. Reverting its `note_uncut_graph`/`clear_uncut_graph` pair to the bare
    `return row is not None and row[1] == active` reddens NOTHING before this
    test: every other assertion in this file drove the registry BY HAND. With the
    feeder gone, `/health` only moves at creation time and reads green forever
    over exactly the population this PR exists to surface.

    Driven the way `tests/test_12894_record_cursor.py` drives `replay()` — an
    injected connection and a cursor row — so the three arms are exercised
    without a database.
    """
    from tortoise import record_allocator as ra

    state: dict = {"row": None}
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setattr(rw, "record_live_allocate", lambda: True)
    monkeypatch.setattr(rw, "_default_connect", lambda _dsn, **_kw: _FakeConn())
    monkeypatch.setattr(ra, "read_cursor",
                        lambda _conn, *, org_id, graph_id: state["row"])
    monkeypatch.setattr(rw, "_uncut_warned_at", {})

    # (1) NO ROW — the born-uncut graph. The observation must be TRACKED.
    assert rw.record_owns_position(tenant=("org-14582", "graph-14582")) is False
    assert rw.armed_but_uncut()["armed_but_uncut"] == 1, (
        "a write to a graph with no cursor row did not reach the registry — the "
        "write-path feeder is gone and `/health` can no longer see a graph the "
        "hook missed")
    assert rw.armed_but_uncut_reasons() == {"no_cursor_row": 1}

    # (2) A ROW THAT IS NOT `active` — the mode is named, not collapsed.
    state["row"] = (0, "seeded")
    assert rw.record_owns_position(tenant=("org-14582", "graph-14582")) is False
    assert rw.armed_but_uncut_reasons() == {"mode_seeded": 1}, (
        "a `seeded` graph must be tracked with its OWN reason — the row exists "
        "but allocates nothing (`ALLOCATION_SQL` is scoped to `active`)")

    # (3) THE SAME READ THAT OBSERVES THE GAP OBSERVES ITS REPAIR.
    state["row"] = (0, "active")
    assert rw.record_owns_position(tenant=("org-14582", "graph-14582")) is True
    assert rw.armed_but_uncut()["armed_but_uncut"] == 0, (
        "a healed graph is still tracked — the clearer is unreachable from the "
        "write path, so `/health` is a LATCH and only a restart clears it")
    assert rw.armed_but_uncut_reasons() == {}


class _CountingCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def execute(self, _sql, _params=None):
        pass

    def fetchone(self):
        return (2,)          # the journal's COUNT — a gapped `1, 2, 4` axis

    def close(self):
        pass


class _CountingConn:
    def cursor(self):
        return _CountingCursor()


def test_the_active_short_circuit_removes_a_false_divergence(monkeypatch):
    """⛔ #14587 review P1-3 (second instance) — the short-circuit had no pin.

    `record_allocator.provision_graph_record` reads the row BEFORE seeding, and
    its own docstring says that read is *"not an optimisation — it removes a
    FALSE DIVERGENCE"*: on an ALREADY `active` graph with a gapped journal,
    `floor = COUNT(*)` is 2 while `MAX(seq)` is 3, so `SEED_SQL` would refuse
    `divergence` and the hook would publish a healthy cut-over graph as uncut.
    Deleting the short-circuit left the suite GREEN (on a dense journal the
    `armed` refusal arm returns the same answer), so the only input that
    separates them — active + GAPPED journal — is built here.

    Mutant: delete the `existing is not None and existing[1] == MODE_ACTIVE`
    return → `seed` is reached, refuses, and this test fails on `seen == []`.
    """
    from tortoise import record_allocator as ra

    seen: list[str] = []
    monkeypatch.setattr(ra, "read_cursor",
                        lambda _conn, *, org_id, graph_id: (3, ra.MODE_ACTIVE))
    monkeypatch.setattr(ra, "scope_tenant", lambda _conn, _org: None)

    def seed_that_would_refuse(*_a, **_kw):
        seen.append("seed")
        raise ra.SeedRefused(
            "the record holds positions this journal cannot reproduce",
            reason="divergence", row_max=3, enumerated_floor=2)

    monkeypatch.setattr(ra, "seed", seed_that_would_refuse)
    result = ra.provision_graph_record(_CountingConn(), org_id="org-14582",
                                       graph_id="graph-14582")
    assert seen == [], (
        "an ALREADY `active` graph was sent through `seed` — with a gapped "
        "journal that refuses `divergence`, so a healthy cut-over graph is "
        "published as uncut and `/health` reds for nothing")
    assert result.already_cut_over is True
    assert (result.seq, result.mode) == (3, ra.MODE_ACTIVE)
    assert result.seeded is False


def test_the_provision_connect_is_bounded_by_its_own_timeout(monkeypatch):
    """⛔ THE ONE BOUND THIS CHANGE ADVERTISES WAS UNTESTED (#14587).

    The hook runs synchronously from `async` endpoints, so its connect is capped
    at `PROVISION_CONNECT_TIMEOUT_SECONDS` (3 s) rather than the live door's 10 —
    a record outage must not cost a create endpoint the full live budget.
    Mutating the kwarg back to the module default reddened nothing.
    """
    from tortoise import record_allocator as ra

    seen: dict = {}

    def connect(_dsn, *, connect_timeout=None):
        seen["connect_timeout"] = connect_timeout
        return _FakeConn()

    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setattr(rw, "_default_connect", connect)
    monkeypatch.setattr(ra, "provision_graph_record", lambda *_a, **_kw: (
        SimpleNamespace(seq=0, mode="active", already_cut_over=False,
                        seeded=True, healed_tail=False, burned=False)))

    assert rw.provision_graph_record(org_id="o", graph_id="g")[
        "provisioned"] is True
    assert seen["connect_timeout"] == rw.PROVISION_CONNECT_TIMEOUT_SECONDS, (
        f"the provision connect is not bounded by its own timeout (got "
        f"{seen.get('connect_timeout')!r}) — a record outage then blocks a "
        f"create endpoint for the live door's "
        f"{rw.CONNECT_TIMEOUT_SECONDS}s budget")
    assert seen["connect_timeout"] < rw.CONNECT_TIMEOUT_SECONDS


def test_the_provision_statements_are_bounded_transaction_locally(monkeypatch):
    """⛔ THE CONNECT BOUND WAS NOT THE STATEMENT BOUND (#14587 review P3-2).

    `PROVISION_CONNECT_TIMEOUT_SECONDS` bounded the CONNECT only, and the hook's
    advertised claim was "a record outage at creation must cost the user a bounded
    pause". A record that ACCEPTS the TCP connection and then hangs on a statement
    is the shape that refutes it: `_default_connect` sets `connect_timeout` and
    `idle_in_transaction_session_timeout` and NO `statement_timeout`, and this sync
    hook runs inside an `async` endpoint, so the hang blocked the worker's event
    loop (including a co-located `/health`) forever.

    Two assertions, two mutants: delete the `_bound_provision_statements(connection)`
    call → no `statement_timeout` statement is executed at all; flip the scope flag
    `true` → `false` (a SESSION setting on a POOLED handle, #9558) → the scope
    assertion reds.
    """
    from tortoise import record_allocator as ra

    conn = _FakeConn()
    order: list[str] = []
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setattr(rw, "_default_connect", lambda _dsn, **_kw: conn)

    def provision(*_a, **_kw):
        order.append(f"allocator_after_{len(conn.statements)}_statements")
        return SimpleNamespace(seq=0, mode="active", already_cut_over=False,
                               seeded=True, healed_tail=False, burned=False)

    monkeypatch.setattr(ra, "provision_graph_record", provision)

    assert rw.provision_graph_record(
        org_id="org-14582", graph_id="graph-14582")["provisioned"] is True

    bounds = [(sql, params) for sql, params in conn.statements
              if "statement_timeout" in sql]
    assert len(bounds) == 1, (
        "the hook issued no statement bound — `connect_timeout` does not bound a "
        f"statement, so a hung SELECT blocks the create unbounded: {conn.statements}")
    sql, params = bounds[0]
    assert params == (str(rw.PROVISION_STATEMENT_TIMEOUT_MS),), (
        f"the bound applied is not PROVISION_STATEMENT_TIMEOUT_MS: {params!r}")
    assert rw.PROVISION_STATEMENT_TIMEOUT_MS > 0, (
        "Postgres reads statement_timeout = 0 as NO TIMEOUT AT ALL, so a zero "
        "here is the unbounded hang spelled as a bound")
    assert sql.rstrip().endswith("true)"), (
        "the bound must be TRANSACTION-LOCAL (`set_config(..., true)`): the "
        "handle is POOLED (#9558), so a session-level SET follows it into the next "
        f"borrower's transaction — got {sql!r}")
    assert order and order[0].startswith("allocator_after_1"), (
        "the bound must be scoped BEFORE the allocator's statements run, or those "
        f"statements are the unbounded ones: {order}")


def test_the_halted_remedy_names_reopen(caplog):
    """⛔ `seed` THEN `arm` CANNOT ARM A HALTED GRAPH (#14587 review P3).

    `_warn_uncut_graph(reason="mode_halted")` printed the un-halted sequence —
    `seed --enumerated-floor 0`, then `arm`. `ARM_SQL` is scoped
    `WHERE mode='seeded'` and `_transition` refuses a halted row with "its mode is
    'halted', not 'seeded'", and `SEED_SQL`'s `ON CONFLICT` is scoped to
    `seeded`/`halted` and sets only `seq` — so re-seeding a halted row leaves it
    halted and the operator's second command fails. `reopen`
    (`halted -> seeded`) is the missing verb, in the order `record_allocator`'s
    own halt message prints: `seed` -> `reopen` -> `arm`.

    Mutant: drop the `mode_halted` branch in `_remedy_for_reason` → `reopen` is
    absent from the emitted line and the ordering assertion reds.
    """
    rw.reset_uncut_graphs()
    try:
        remedy = rw._remedy_for_reason("org-14582", "graph-halted", "mode_halted")
        assert "reopen" in remedy, (
            f"the halted remedy omits `reopen`, the only verb that leaves `halted`: {remedy}")
        assert remedy.index("seed") < remedy.index("reopen") < remedy.index("arm"), (
            f"the sequence must be seed -> reopen -> arm: {remedy}")
        assert "--acknowledge-cutover" in remedy
        # The un-halted population must NOT be told to reopen: a `no_cursor_row`
        # graph is not halted, and `reopen` would refuse it (`wanted=halted`).
        assert "reopen" not in rw._remedy_for_reason(
            "org-14582", "graph-new", "no_cursor_row")

        with caplog.at_level("WARNING", logger="tortoise.record_writer"):
            rw._warn_uncut_graph("org-14582", "graph-halted", reason="mode_halted")
        lines = [rec.getMessage() for rec in caplog.records]
        assert any("reopen" in line for line in lines), (
            "the emitted warning does not carry the sequence that reaches the "
            f"state it names: {lines}")
    finally:
        rw.reset_uncut_graphs()
        rw._uncut_warned_at.clear()


def test_the_divergence_remedy_does_not_print_a_floor_the_seed_refuses():
    """⛔ `--enumerated-floor 0` CANNOT REACH A DIVERGENT GRAPH (#14587 review P3).

    `_warn_uncut_graph(reason="provision_refused:divergence")` printed the
    un-halted sequence with floor `0`. `SEED_SQL` writes a row only
    `WHERE observed.row_max <= observed.enumerated_floor`, and `divergence` IS
    `row_max > floor`, so the first printed command is refused by the same guard
    and the sequence cannot reach the state the message names — the same class as
    the halted arm above, on a third population.

    The floor that works is the record's own max: with `floor = MAX(seq)` the
    guard's `row_max <= floor` holds, and it is the number
    `tools/record_allocator_cli.py backfill` prints in the `remedy` field of a
    graph it refused `divergence`.

    Mutant: drop the `provision_refused:divergence` branch in
    `_remedy_for_reason` → the emitted line carries a floor-`0` seed command
    again and the first assertion reds.
    """
    remedy = rw._remedy_for_reason(
        "org-14582", "graph-divergent", "provision_refused:divergence")
    # The assertion is about the COMMAND the operator would run, not about the
    # word `0` anywhere in the paragraph: the message names the refused form on
    # purpose, and matching the bare literal would red on its own explanation.
    assert ("seed --org-id org-14582 --graph-id graph-divergent "
            "--enumerated-floor 0") not in remedy, (
        "the divergence remedy prints a seed command the guard refuses: "
        f"{remedy}")
    assert "--enumerated-floor <the record's own MAX(seq)>" in remedy, (
        f"the divergence remedy does not name the floor that works: {remedy}")
    assert remedy.index("seed") < remedy.index("arm"), remedy
    # A halted row can ALSO be divergent, and a re-seed does not move the mode,
    # so the sequence must name `reopen` or its last verb is refused for that
    # intersection — the same defect this arm exists to fix, one population over.
    assert "reopen" in remedy and "halted" in remedy, (
        "the divergence remedy assumes a `seeded` row, so `arm` is refused for a "
        f"graph that is divergent AND halted: {remedy}")
    assert rw._remedy_for_reason("o", "g", "provision_refused:divergence").count(
        "reopen") == 1, "the conditional `reopen` must be named exactly once"
    # The un-halted population keeps the `0` sequence: its record holds no
    # position beyond the enumeration, so `0` is the correct floor there and the
    # new arm must not swallow it. It also names the fallback, so an operator
    # whose `0` seed comes back `divergence` is not sent round the loop twice.
    unarmed_gap = rw._remedy_for_reason("org-14582", "graph-new", "no_cursor_row")
    assert ("seed --org-id org-14582 --graph-id graph-new --enumerated-floor 0"
            in unarmed_gap), unarmed_gap
    assert "divergence" in unarmed_gap and "MAX(seq)" in unarmed_gap, unarmed_gap


def test_a_re_provisioned_halted_graph_keeps_its_published_entry(monkeypatch):
    """⛔ `provisioned: True` IS NOT "CUT OVER" (#14587 review P3).

    `record_allocator.provision_graph_record`'s `halted` arm seeds MONOTONICALLY
    and deliberately does NOT re-arm — a hook that silently resumed a halt would
    erase the only record that somebody stopped the graph. The caller committed
    and then unconditionally called `clear_uncut_graph` under the comment "the
    graph IS cut over now", which popped the graph's `armed_but_halted` entry:
    `/health` forgot a graph whose writes are still refused, until its next write
    re-observed it. A retraction must be earned by an `active` outcome.

    Mutant: restore the unconditional `clear_uncut_graph` → the halted count goes
    to 0 and the first assertion reds.
    """
    from tortoise import record_allocator as ra

    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setattr(rw, "_default_connect", lambda _dsn, **_kw: _FakeConn())

    def provision(mode, seq):
        return lambda _conn, **_kw: ra.ProvisionedRecord(
            org_id="org-14582", graph_id="graph-14582", seq=seq, mode=mode,
            already_cut_over=False, seeded=True)

    rw.reset_uncut_graphs()
    try:
        # The graph was already published as halted by an earlier observation.
        rw.note_uncut_graph("org-14582", "graph-14582", reason="mode_halted")
        assert rw.armed_but_uncut()["armed_but_halted"] == 1

        monkeypatch.setattr(ra, "provision_graph_record",
                            provision(ra.MODE_HALTED, 7))
        out = rw.provision_graph_record(
            org_id="org-14582", graph_id="graph-14582")
        assert out["provisioned"] is True and out["mode"] == ra.MODE_HALTED, out
        assert rw.armed_but_uncut()["armed_but_halted"] == 1, (
            "the hook retracted a halted graph's published entry on an outcome "
            "that deliberately did NOT re-arm it: `/health` now forgets a graph "
            "whose writes are still refused")

        # …and an ACTIVE outcome still retracts, so the clearer is not dead.
        monkeypatch.setattr(ra, "provision_graph_record",
                            provision(ra.MODE_ACTIVE, 8))
        assert rw.provision_graph_record(
            org_id="org-14582", graph_id="graph-14582")["mode"] == ra.MODE_ACTIVE
        assert rw.armed_but_uncut()["armed_but_halted"] == 0, (
            "an ACTIVE outcome must retract the entry — a clearer that never "
            "fires turns `/health` into a latch")
    finally:
        rw.reset_uncut_graphs()
        rw._uncut_warned_at.clear()


def test_a_halt_retracts_an_earlier_uncut_observation(monkeypatch):
    """⛔ A SANCTIONED HALT MUST NOT PIN `ok: false` (#14587 review P3).

    A graph is published on the UNCUT key when it was observed unusable
    (`provision_failed`, `no_cursor_row`, a refused cutover), and an operator can
    HALT it afterwards — on the write path that is the very next observation, and
    the hook's halted arm reaches the same function. `mode_halted` is published on
    its OWN key and used to leave the earlier entry untouched, so the graph was
    counted in both populations, `armed_but_uncut_reasons()` kept the falsified
    `provision_failed`, and — `ok` folding only `armed_but_uncut` — the
    deployment read degraded for as long as the halt stood. The health contract
    splits the key precisely so an operator's decision is not a degradation.

    Mutant: drop the `_UNCUT_GRAPHS.pop(key, None)` in the halted arm → the uncut
    count stays 1 and both assertions red.
    """
    rw.reset_uncut_graphs()
    try:
        rw.note_uncut_graph("org-14582", "graph-1", reason="provision_failed")
        assert rw.armed_but_uncut()["armed_but_uncut"] == 1
        rw.note_uncut_graph("org-14582", "graph-1", reason="mode_halted")
        block = rw.armed_but_uncut()
        assert block["armed_but_halted"] == 1, block
        assert block["armed_but_uncut"] == 0, (
            "the halt left the graph in the UNCUT population, which `ok` folds: "
            f"a sanctioned halt pinned the deployment red — {block}")
        assert rw.armed_but_uncut_reasons() == {}, (
            "the halt kept a reason it had itself falsified, and that reason is "
            f"what `/health` publishes: {rw.armed_but_uncut_reasons()}")

        # THE INVARIANT IS SYMMETRIC: at most one map per graph, newest wins. A
        # graph that is halted and is THEN observed refusing is published as
        # uncut and stops being labelled halted — the newest fact is the one
        # `/health` owes the operator, and the same graph must never count twice.
        rw.note_uncut_graph("org-14582", "graph-1",
                            reason="provision_refused:divergence")
        block = rw.armed_but_uncut()
        assert block["armed_but_uncut"] == 1, block
        assert block["armed_but_halted"] == 0, (
            "the graph kept the superseded HALTED label and inflated "
            f"`armed_but_halted` while counting as uncut too: {block}")
        assert rw.armed_but_uncut_reasons() == {
            "provision_refused:divergence": 1}, rw.armed_but_uncut_reasons()

        # …AND THE CONSEQUENCE ON THE CHANNEL THE CLAIM IS ABOUT, through
        # `_record_health` rather than the maps: the halt alone stays GREEN, and
        # the refusal that superseded it REDS — a claim about `/health` that never
        # reads `/health` cannot catch a fold that stops folding.
        dsn = "postgresql://u:p@127.0.0.1:1/record"
        assert _health(monkeypatch, dsn=dsn)["ok"] is False, (
            "a graph observed REFUSING after a halt is a real gap, so `ok` must "
            "be false — the halt rationale covers only the graph whose last "
            "observation IS the halt")
        rw.note_uncut_graph("org-14582", "graph-1", reason="mode_halted")
        block = _health(monkeypatch, dsn=dsn)
        assert block["armed_but_halted"] == 1 and block["armed_but_uncut"] == 0, block
        assert block["ok"] is True, (
            "a halt that nothing later contradicted must not red the deployment "
            "— that is the permanent false red the key split exists to prevent")
    finally:
        rw.reset_uncut_graphs()


def test_the_failure_paths_print_the_per_graph_remedy(caplog, monkeypatch):
    """⛔ #14587 review P1-2 — the printed remedy could not reach the population.

    Every failure path printed `backfill` as THE remedy, and `backfill` seeds
    `sweep`'s population: `public.journal_event` minus `record_cursor`. On an
    armed deployment a graph whose hook failed writes NO `journal_event` row —
    `ALLOCATION_SQL` is scoped to `mode='active'`, so the door refuses and the
    line stays in the JSONL file — so the operator followed the printed remedy,
    got `considered: 0 / seeded: 0 / refused: 0` with exit 0, read a CLEAN
    pre-flight, and was left with a hand-typed command whose id `/health` does
    not publish. The per-graph `seed` + `arm` is the command that works, so it is
    what the text must print — first, and with the ids filled in.

    Mutant: revert either message to `backfill --org-id <org>` → the assertion
    reds on the missing `--enumerated-floor 0`.
    """
    from tortoise import record_allocator as ra

    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setattr(rw, "_default_connect", lambda _dsn, **_kw: _FakeConn())

    def boom(_conn, **_kw):
        raise RuntimeError("the record is unreachable")

    monkeypatch.setattr(ra, "provision_graph_record", boom)
    with caplog.at_level("WARNING", logger="tortoise.record_writer"):
        assert rw.provision_graph_record(
            org_id="org-14582", graph_id="graph-14582")["reason"] == (
                "provision_failed")
    lines = [rec.getMessage() for rec in caplog.records]
    assert any("seed --org-id org-14582 --graph-id graph-14582 "
               "--enumerated-floor 0" in line for line in lines), lines
    assert any("arm --org-id org-14582" in line for line in lines), lines

    caplog.clear()
    monkeypatch.setattr(rw, "_uncut_warned_at", {})
    with caplog.at_level("WARNING", logger="tortoise.record_writer"):
        rw._warn_uncut_graph("org-14582", "graph-14582",
                             reason="no_cursor_row")
    lines = [rec.getMessage() for rec in caplog.records]
    assert any("seed --org-id org-14582 --graph-id graph-14582" in line
               for line in lines), lines
    assert any("NOT in their population" in line for line in lines), (
        "the warning still lets an operator wait for a graph to appear in "
        "`sweep`/`backfill`'s population, where an armed deployment guarantees "
        f"it never will: {lines}")


def test_an_unimportable_allocator_seam_names_the_graph_and_never_escapes(
        caplog, monkeypatch):
    """⛔ #14587 review P3-1 — the ONE arm that escaped the never-raise contract.

    `from . import record_allocator as ra` sat BEFORE the hook's first `try`, so an
    unimportable allocator raised out of a function whose docstring promises "it
    never raises" AND whose whole compensation is a health field. It landed in the
    door wrappers, which can only LOG — so `/health` kept `ok: true` over a graph
    whose cursor row was never provisioned: the PR's headline failure, with the
    remedy moved off the surface the docstring advertises.

    RED before the fix: the call below raises `ModuleNotFoundError` instead of
    returning, so there is no result to assert and nothing in the registry.

    Two levers, and BOTH are needed to make the import fail for real: the
    `sys.modules` entry is what the import machinery refuses, and the package
    ATTRIBUTE has to go too or `from . import record_allocator` resolves it
    without consulting `sys.modules` at all (measured: with the `sys.modules` entry
    alone the hook returns `unarmed` and never touches the seam).
    """
    import sys

    import tortoise

    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setitem(sys.modules, "tortoise.record_allocator", None)
    monkeypatch.delattr(tortoise, "record_allocator", raising=False)

    with caplog.at_level("WARNING", logger="tortoise.record_writer"):
        result = rw.provision_graph_record(org_id="org-14582",
                                          graph_id="graph-14582")

    assert result == {"provisioned": False, "reason": "provision_failed"}, (
        "an unimportable seam must be a NAMED provisioning outcome, not an "
        f"escaping exception: {result!r}")
    assert rw.armed_but_uncut_reasons() == {"provision_failed": 1}, (
        "the graph is uncut and `/health` folds `armed_but_uncut`; a failure it "
        "does not count is the silent gap this hook exists to close")
    lines = [rec.getMessage() for rec in caplog.records]
    assert any("seed --org-id org-14582 --graph-id graph-14582 "
               "--enumerated-floor 0" in line for line in lines), lines
    assert any("not importable" in line for line in lines), lines

    # ⛔ AND THE GUARD SITS AFTER THE `unarmed` RETURN ON PURPOSE (property 1): with
    # no record DSN there is no record, so an unimportable seam is not a fact about
    # anything and must not be counted. Mutant: hoist the import guard above the
    # `unarmed` return → this counts a graph on an unarmed deployment (the
    # permanent false red F3 forbids).
    rw.reset_uncut_graphs()
    monkeypatch.delenv(rw.RECORD_DSN_ENV)
    assert rw.provision_graph_record(org_id="org-14582", graph_id="graph-14582") \
        == {"provisioned": False, "reason": "unarmed"}
    assert rw.armed_but_uncut_reasons() == {}, (
        "an unarmed deployment has no record, so nothing is uncut — an "
        "unimportable seam must not be counted there")


@pytest.mark.parametrize(("module", "wrapper"), (
    ("tortoise.hosted_api", "_provision_graph_record"),
    ("tortoise.supabase_control", "_provision_default_graph_record"),
))
def test_a_wrapper_that_logs_an_escaping_hook_also_counts_the_graph(
        module, wrapper, monkeypatch):
    """⛔ #14587 review P3-1 — "logged at ERROR" is not a health field.

    Both door wrappers caught the hook's escaping exception with a bare
    `_logger.error`, so the graph a log line NAMED was absent from the registry
    `/health` folds — `ok: true` over a known gap, on the one arm the wrapper's
    own docstring claimed was covered.

    The hook now guards its own seam import, so what reaches a wrapper is either
    the residual (the hook's module itself did not import, where the registry it
    would report into is gone too — stated on both docstrings, not pretended
    away) or a hook that RAISED despite its never-raise contract. In that second
    case the module DID import, so a log-only body leaves the surface green over a
    bug — which is what this pins. Mutant: delete either wrapper's
    `note_uncut_graph` call → the registry stays empty and this reds.
    """
    import importlib

    from tortoise import record_writer

    door = getattr(importlib.import_module(module), wrapper)

    def boom(**_kw):
        raise RuntimeError("the hook raised despite its never-raise contract")

    with monkeypatch.context() as patch:
        patch.setattr(record_writer, "provision_graph_record", boom)
        door(org_id="org-14582", graph_id="graph-14582")

    assert rw.armed_but_uncut_reasons() == {"provision_failed": 1}, (
        f"`{module}.{wrapper}` logged the failure and left `/health` green — the "
        "graph is uncut and the registry that would say so was importable there")


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
