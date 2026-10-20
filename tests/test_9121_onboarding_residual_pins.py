"""#9121 — the seam the §17.1 declaration does **NOT** cover, pinned in code.

`tests/test_9079_onboarding_declared_lane.py` owns the declaration itself: it
arms the `#9124` predicate on the guarded handle and proves the onboarding lane
keeps working with the guard closed. This file owns the *residual*, and exists
because that suite cannot see it: the five `eager_init_query()` call sites
CREATE `:OnboardingState` on `proj.db.select_graph(<graph>)` — §17.1's escape
(3) — so they meet neither `state._run` nor `_onboarding_write_scope`, and they
keep working after `#9079` precisely *because* they sit below the handle.

⛔ WHY THIS FILE IS NOT A SECOND COPY OF test_9079. A previous draft of this
work asserted the residual with a runtime probe that built its OWN handle,
monkeypatched it and issued the statement ITSELF — so it recorded "no
declaration in flight" because the *test* did not open one, not because
production does not. That is a tautology: it passes on the pre-fix tree and it
would keep passing after the residual were fixed. Everything here reads the
PRODUCTION source with `ast` and with the same reader the note is written for,
so each assertion has a way to fail:

* a SIXTH `eager_init_query()` site appearing anywhere in the SDK / hosted API
  fails `test_the_note_and_the_ast_inventory_agree` (the note would be stale);
* a site being DELETED (or migrated off `select_graph`) fails
  `test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle`,
  which now ASSERTS THE LINKAGE — the statement must be issued by a `query(...)`
  call on the handle the same function bound from `select_graph(...)`. The two
  wrong closures red it: `proj.g.query(...)` (the §17.1 violation this file
  exists to see) and `state._run(...)` (the honest fix, which would leave the
  note describing a gap that no longer exists);
* the day one of those sites is DECLARED — the honest fix, `#9157` —
  `test_no_eager_init_site_declares_itself_yet` turns RED *when the declaration
  is a `with` in the same function*, so the note, the guard's opener inventory
  and this file have to move together instead of drifting back to a universal
  ("every onboarding write is declared") that the escape disproves. The
  `_run` route opens its scope in another file and is caught by the linkage
  pin above;
* the fact that the detector is *capable* of firing is not argued, it is
  executed: `test_the_declaration_detector_can_fail` runs it over a synthetic
  declaration **for every recorded opener** and requires a hit, and
  `test_the_issuance_detector_reds_on_the_guarded_handle_and_on_run` does the
  same for the linkage pin over the two closure shapes.

The claim these pins protect, stated in the terms of the code: every statement
this package issues **through `state._run`** runs inside the declaration; the
`eager_init_query()` callers do not, and are unjournalled AND undeclared.

⛔ AND IT IS NOT THE ONLY INVENTORY OF THIS SEAM — the closer neighbour is
`tests/test_write_ahead_mint.py::_MINT_SITES`, which already walks
`tortoise/sdk.py` / `tortoise/hosted_api.py` / `tortoise/__main__.py` for
`:TeamMeta` mint sites and recognises `eager_init_query` bindings. The two share
**the site set** (a site that appears or disappears moves both), and each owns
what the other cannot see:

* unique to THIS file: the per-function **count** (`register_user` issues two —
a third arm moves nothing in `_MINT_SITES`), the note↔AST agreement, and the
  `#9157` no-declaration coupling;
* unique to `_MINT_SITES`: the write-ahead journaling ORDER, and the
  `tortoise/__main__.py::_cmd_key_create` site, which mints `:TeamMeta` through a
  different shape and therefore is not an `eager_init_query()` site at all.

Rather than keeping two free-handed copies that can drift apart silently,
`test_the_escape3_sites_are_the_mint_sites_that_use_eager_init` asserts the
RELATIONSHIP against `_MINT_SITES` itself.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from tortoise.onboarding import state as _os
from tortoise.projection import _statement_writes

_REPO = Path(__file__).resolve().parents[1]

#: The `:TeamMeta` mint sites that are NOT `eager_init_query()` sites — they mint
#: through another shape. Named, so the difference between this file's inventory
#: and `test_write_ahead_mint.py::_MINT_SITES` is an assertion rather than a
#: remainder nobody reads.
_NON_EAGER_MINT_SITES = {("tortoise/__main__.py", "_cmd_key_create")}


def _mint_sites_from_its_owner() -> set[tuple[str, str]]:
    """`_MINT_SITES`, read from the suite that owns it.

    Imported by path rather than copied: a second hand-written copy is the drift
    seam this file exists to close (a site set is not a thing to spell twice).
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "test_write_ahead_mint.py"
    spec = importlib.util.spec_from_file_location(
        "_write_ahead_mint_owner", path
    )
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module._MINT_SITES)

#: The primitives that OPEN a §17.1 declaration. A `with` over any of these is
#: what "declared" means; a site that acquires one has left the residual.
#: Every scope opener `tortoise/projection/__init__.py` defines is listed ON
#: PURPOSE — an earlier revision named only the three onboarding-ish ones, so
#: closing the residual through `_graph_metadata_write_scope(...)` (the
#: graph-metadata lane, and the shape `onboarding/state.py`'s SEAM block names)
#: left every pin green. `test_the_declaration_detector_can_fail` runs the
#: detector over EACH member, so a new opener cannot silently reopen the hole
#: without also failing that instrument check's recorded list below.
#:
#: ⚠️ Scope of what this detects: a `with` in the SAME function. The likeliest
#: shape of the honest fix (#9157) is routing the statement through
#: `onboarding/state.py::_run`, which opens its scope in ANOTHER file — that
#: route is caught by
#: `test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle`,
#: which reds when the statement stops being issued on the `select_graph`
#: handle (including when it is handed to `_run`).

#: (file, enclosing function) -> how many `eager_init_query()` statements that
#: function issues. The COUNT is part of the pin: `register_user` issues two
#: (the self-service arm and the Supabase-mode arm), and a slice of that pair
#: disappearing without the note being re-read is exactly the drift here.
_ESCAPE3_CALL_SITES = {
    ("tortoise/sdk.py", "org_create"): 1,
    ("tortoise/hosted_api.py", "provision_tenant"): 1,
    ("tortoise/hosted_api.py", "register_user"): 2,
    ("tortoise/hosted_api.py", "_eager_provision_org_graph"): 1,
}


def _parse(rel: str) -> tuple[ast.Module, dict[ast.AST, ast.AST]]:
    tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
    parents = {
        child: node
        for node in ast.walk(tree)
        for child in ast.iter_child_nodes(node)
    }
    return tree, parents


def _enclosing_function(node: ast.AST, parents: dict[ast.AST, ast.AST]):
    while node is not None and not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node = parents.get(node)
    return node


def _bound_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for element in target.elts:
            out |= _bound_names(element)
        return out
    return set()


def _named(nodes, names: tuple[str, ...]) -> bool:
    """Does any node in `nodes` NAME one of `names`?"""
    for root in nodes:
        for node in ast.walk(root):
            if isinstance(node, ast.Name) and node.id in names:
                return True
            if isinstance(node, ast.Attribute) and node.attr in names:
                return True
    return False


def _escape_handle_names(enclosing: ast.AST) -> set[str]:
    """Names bound from a `select_graph(...)` call inside `enclosing`."""
    handles: set[str] = set()
    for sub in ast.walk(enclosing):
        if (
            isinstance(sub, ast.Assign)
            and isinstance(sub.value, ast.Call)
            and getattr(sub.value.func, "attr", None) == "select_graph"
        ):
            for target in sub.targets:
                handles |= _bound_names(target)
    return handles


def _statement_sinks(
    enclosing: ast.AST, issued: set[str]
) -> list[tuple[int, str, str | None, tuple[str, ...]]]:
    """Every call the statement text FLOWS INTO, as `enclosing` reads it.

    `(lineno, callee, receiver, statement names)` — `callee` is the source form
    of the call's `func` (`handle.query`, `proj.g.query`, `_os._run`), and
    `receiver` is the plain name it is called on, or `None` when the receiver
    is an expression (that is itself the signal: `proj.g.query(...)` leaves the
    escape handle). This is what makes the claim "issued on the
    `select_graph` handle" checkable: an earlier revision only asserted that
    SOME `select_graph(...)` existed in the function and that the statement was
    bound to SOME name, so it stayed green under both mutations it claimed to
    catch (`proj.g.query(...)`, and `_run(proj, _init_q, ...)`).
    """
    sinks: list[tuple[int, str, str | None, tuple[str, ...]]] = []
    for call in ast.walk(enclosing):
        if not isinstance(call, ast.Call):
            continue
        used: set[str] = set()
        for arg in call.args:
            used |= {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)}
        for kw in call.keywords:
            if kw.value is not None:
                used |= {n.id for n in ast.walk(kw.value) if isinstance(n, ast.Name)}
        passed = used & issued
        if not passed:
            continue
        receiver: str | None = None
        if isinstance(call.func, ast.Attribute):
            callee = ast.unparse(call.func)
            if isinstance(call.func.value, ast.Name):
                receiver = call.func.value.id
        elif isinstance(call.func, ast.Name):
            callee = call.func.id
        else:
            callee = ast.unparse(call.func)
        sinks.append((call.lineno, callee, receiver, tuple(sorted(passed))))
    return sinks


def _not_on_the_escape_handle(
    enclosing: ast.AST, issued: set[str], handles: set[str]
) -> list[str]:
    """Sinks where the statement leaves the `select_graph` handle, rendered."""
    offenders: list[str] = []
    for lineno, callee, receiver, passed in _statement_sinks(enclosing, issued):
        if callee.rsplit(".", 1)[-1] == "query" and receiver in handles:
            continue
        offenders.append(
            f"line {lineno}: {callee}({', '.join(passed)}) — "
            f"escape-(3) handle(s): {sorted(handles)}"
        )
    return offenders


#: DERIVED, not hand-listed (#9150 review P2): the recorded set above was a
#: literal, so a NEW opener added to `tortoise/projection/__init__.py` — the
#: shape both #9123 and #9195 used — was invisible to the instrument check,
#: which iterated the literal and therefore could not notice it. This reads the
#: production source and returns every function whose body declares through
#: `_cache_write_authorized`, so a new lane joins the population by existing.
def _declaration_primitives() -> set[str]:
    """Every §17.1 opener the tree defines, by AST — ALL of `tortoise/`.

    Two populations, because they answer two questions. This one is the DETECTOR
    population: any function that can put a declaration in flight, wherever it
    lives. Measured before it was widened (#9150 review, P2): a site wrapped in
    `tortoise/projection/edges.py::_structural_edge_write_scope` left 13/13
    green, because the population was projection-only — so "fails the day one of
    those sites enters a declaration" was false for a real, in-use opener.
    `_projection_openers()` below is the INVENTORY population: only
    `projection/__init__.py`, because that is the bullet list a reader is told
    is exhaustive.
    """
    return _openers_under(_REPO / "tortoise")


def _openers_under(root: Path) -> set[str]:
    """Functions under `root` whose body declares via `_cache_write_authorized`.

    A FILE path is allowed too (the inventory population is one module:
    `projection/__init__.py`, the bullet list a reader is told is exhaustive —
    `projection/edges.py`'s `_structural_edge_write_scope` is a real opener that
    is NOT in that list).
    """
    found: set[str] = set()
    paths = [root] if root.is_file() else sorted(root.rglob("*.py"))
    for path in paths:
        rel = str(path.relative_to(_REPO))
        tree, _parents = _parse(rel)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and (getattr(inner.func, "id", None) == "_cache_write_authorized"
                         or getattr(inner.func, "attr", None) == "_cache_write_authorized")
                ):
                    found.add(node.name)
    return found


def _projection_openers() -> set[str]:
    """The openers `tortoise/projection/__init__.py` defines (the inventory)."""
    return _openers_under(_REPO / "tortoise" / "projection" / "__init__.py")


#: The primitives that can authorize a cache write, from two places: every opener
#: `projection/__init__.py` derives, plus the two names `onboarding/state.py`
#: uses (its opener lives in another file and declares through the raw ContextVar
#: helper rather than a projection opener).
_DECLARATION_PRIMITIVES = (
    _declaration_primitives()
    | {"_cache_write_authorized", "_onboarding_write_scope"}
)


def _eager_init_calls(tree: ast.Module):
    """(node, call, targets) for every `eager_init_query` CALL in a module.

    Shared by the inventory and the linkage pin (#9150 review, P2): they used to
    hardcode `ast.Assign` + `func.attr` independently, so a bare-name / `Expr` /
    `AnnAssign` / `Return` site was COUNTED by one and never INSPECTED by the
    other — measured: such a site issued on the guarded handle (`proj.g.query`)
    left every pin green while the file reported it as below the handle.
    """
    out = []
    # PARENT-AGNOSTIC (#9150 review, P2): walk every CALL, not "the call inside an
    # Assign/Expr/...". Measured: a sixth site in an ARGUMENT position
    # (`graph.query(*eager_init_query(...))`) was invisible, because the filter
    # looked at the parent node's type first — so the docstring's promise ("for
    # every `eager_init_query` CALL") was false and the "a SIXTH site fails the
    # inventory pin" claim did not hold for that shape.
    for call in ast.walk(tree):
        if not (
            isinstance(call, ast.Call)
            and (getattr(call.func, "attr", None) == "eager_init_query"
                 or getattr(call.func, "id", None) == "eager_init_query")
        ):
            continue
        # the BINDING node, when there is one, so the linkage pin can read the
        # target it was assigned to; the call itself otherwise.
        node = _binding_parent(tree, call) or call
        targets = getattr(node, "targets", None) or (
            [node.target] if isinstance(node, ast.AnnAssign) else [])
        out.append((node, call, targets))
    return out


def _binding_parent(tree: ast.Module, call: ast.AST) -> ast.AST | None:
    """The Assign/AnnAssign whose value IS `call` (None when it is an expression,
    an argument, or a return)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is call:
            return node
    return None


def _eager_init_sites() -> dict[tuple[str, str], int]:
    """(file, enclosing function) -> count, read off the production source."""
    sites: dict[tuple[str, str], int] = {}
    # EVERY module, not the two that hold today's sites (#9150 review P2): the
    # sentence this pins is unqualified ("five call sites"), so a sixth caller in
    # any other module must be visible. The defining module builds the STRING and
    # issues nothing, so it is skipped by the enclosing-function shape, not by a
    # file allow-list.
    for rel in sorted(
        str(p.relative_to(_REPO)) for p in (_REPO / "tortoise").rglob("*.py")
    ):
        tree, parents = _parse(rel)
        for node, _call, _targets in _eager_init_calls(tree):
            enclosing = _enclosing_function(node, parents)
            assert enclosing is not None, (
                f"{rel}:{node.lineno} builds an eager-init statement outside a "
                "function — this inventory would silently skip it"
            )
            key = (rel, enclosing.name)
            sites[key] = sites.get(key, 0) + 1
    return sites


def _declaring_functions(tree: ast.Module,
                         parents: dict[ast.AST, ast.AST]) -> set[str]:
    """Enclosing functions of a `with` that opens a §17.1 declaration."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        if not _named([item.context_expr for item in node.items],
                      _DECLARATION_PRIMITIVES):
            continue
        enclosing = _enclosing_function(node, parents)
        if enclosing is not None:
            out.add(enclosing.name)
    return out


# ── a premise, not a measurement ──────────────────────────────────────────
def test_the_eager_init_statement_is_a_mutating_one():
    """The residual matters only if the escape issues a WRITE.

    No graph, no fixture: this is the shared classifier the guard itself uses
    (`FalkorProjection.query` → `_statement_writes`), so the claim "escape (3)
    carries an undeclared mutating statement" is the guard's own reading, not
    this file's opinion. Deleting the classifier's `MERGE` recognition (or
    narrowing it) turns this RED.
    """
    cypher, _params = _os.eager_init_query(
        "CREATE (:TeamMeta {name:$name})",
        {"name": "escape3"},
        org_id="org9121-residual",
        fork=_os.FORK_SELF,
    )
    assert _statement_writes(cypher) is True, (
        "`eager_init_query()` no longer builds a statement the §17.1 classifier "
        "calls mutating — the residual this file pins would no longer exist as "
        f"described:\n{cypher}"
    )


# ── the note in the declaration says what it does NOT cover ───────────────
def test_the_declaration_note_names_the_seam_it_does_not_cover():
    """`state.py`'s declaration must state the escape, where a reader looks.

    The universal that five call sites disprove ("every onboarding write is
    declared") is the thing this pins against: the note has to (a) narrow the
    claim to the `_run` seam, (b) name the mechanism (`eager_init_query` on
    `select_graph`), and (c) name the residual issue (`#9157`) so the gap is
    filed rather than implied closed. Deleting any of the three turns this RED.
    """
    source = (_REPO / "tortoise/onboarding/state.py").read_text(encoding="utf-8")
    flat = " ".join(source.split())
    for phrase in ("eager_init_query", "select_graph", "#9157",
                   "do not close escape (3) believing"):
        assert phrase in flat, (
            f"`tortoise/onboarding/state.py` no longer contains {phrase!r} — the "
            "declaration's note about the seam it does NOT cover has been "
            "removed or reworded, which restores the over-claim that five "
            "`eager_init_query()` call sites disprove"
        )


def test_the_note_and_the_ast_inventory_agree():
    """The note's site list must BE the code's site list — both directions.

    Derived from `ast`, not from a copy of the answer: a sixth
    `eager_init_query()` site appearing in `sdk.py` / `hosted_api.py` makes the
    note stale (RED here forces the same change to re-read the residual), and a
    site's name leaving the note is caught by the per-site presence check.
    """
    measured = _eager_init_sites()
    assert measured == _ESCAPE3_CALL_SITES, (
        "the escape-(3) inventory moved: measured "
        f"{sorted(measured.items())} vs recorded "
        f"{sorted(_ESCAPE3_CALL_SITES.items())}. A new site has to be reasoned "
        "about (is it declared? is it journalled?), and the note in "
        "`onboarding/state.py` + the guard's opener inventory in "
        "`projection/__init__.py` have to be updated in the same change (#9157)"
    )
    source = " ".join(
        (_REPO / "tortoise/onboarding/state.py").read_text(encoding="utf-8").split()
    )
    for rel, func in measured:
        assert f"{rel}::{func}" in source, (
            f"the declaration's note no longer names the escape-(3) site "
            f"{rel}::{func} — a lane doing the §17.1 door inventory would read "
            "the onboarding class as fully declared"
        )


# ── what the sites DO, and the coupling to the honest fix (#9157) ─────────
def test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle():
    """Each site issues the statement ON a `select_graph(...)`-bound handle.

    This is the assertion that gives the note its meaning: `eager_init_query`
    only PRODUCES text, so the seam is "issued below the guarded handle" only
    while every caller does that — and only while the statement is issued on
    the handle it resolves in the SAME function. The linkage is asserted, not
    its neighbourhood: an earlier revision checked only that the enclosing
    function mentioned `select_graph(...)` somewhere, which stayed green when
    the statement moved to `proj.g.query(...)` (the §17.1 violation this file
    exists to see) or to `state._run(...)` (the honest #9157 fix) while an
    unrelated handle binding remained. Both of those now RED here, so the
    residual cannot be closed in code while the docstrings still describe it.
    """
    # Driven by the SAME finder and population as the inventory (#9150 review,
    # P2): a site this pin cannot see is a site it silently certifies.
    for rel in sorted(
        str(p.relative_to(_REPO)) for p in (_REPO / "tortoise").rglob("*.py")
    ):
        tree, parents = _parse(rel)
        for node, _call, targets in _eager_init_calls(tree):
            enclosing = _enclosing_function(node, parents)
            assert enclosing is not None
            handles = _escape_handle_names(enclosing)
            assert handles, (
                f"{rel}:{node.lineno} ({enclosing.name}) builds an eager-init "
                "statement but nothing in the function binds a `select_graph(...)` "
                "handle by name — the escape-(3) description in "
                "`onboarding/state.py` and in the guard's inventory is now wrong "
                "(#9157)"
            )
            issued = _bound_names(targets[0]) if len(targets) == 1 else set()
            assert issued, (
                f"{rel}:{node.lineno} binds the statement to something this walk "
                "cannot read — the inventory would silently skip it"
            )
            sinks = _statement_sinks(enclosing, issued)
            assert sinks, (
                f"{rel}:{node.lineno} ({enclosing.name}) binds the eager-init "
                f"statement to {sorted(issued)} but nothing in the function "
                "consumes it — re-derive the residual rather than assuming it"
            )
            offenders = _not_on_the_escape_handle(enclosing, issued, handles)
            assert not offenders, (
                f"{rel}:{node.lineno} ({enclosing.name}) no longer issues the "
                f"eager-init statement on the escape-(3) handle: {offenders}. "
                "The residual is moving (#9157): update, in the same change, the "
                "note in `tortoise/onboarding/state.py`, the opener inventory in "
                "`tortoise/projection/__init__.py`, and this pin"
            )


def test_the_issuance_detector_reds_on_the_guarded_handle_and_on_run():
    """The instrument check for the pin above — it must RED on the two closures.

    Written because the pin it guards is worthless if it cannot fire: the
    previous revision passed on both of these mutations (proved in the review),
    because it never linked the statement to the handle. Both snippets below are
    the STATEMENT used correctly, changed only in where it is issued.
    """
    cases = {
        # The escape-(3) shape, as production issues it today: NOT an offender.
        "on_the_escape_handle": (
            """
            def site(proj, _init_q, _init_p):
                handle = proj.db.select_graph("g")
                handle.query(_init_q, params=_init_p)
            """,
            [],
        ),
        # Closed the WRONG way: the guarded handle, i.e. the §17.1 violation.
        "on_the_guarded_handle": (
            """
            def site(proj, _init_q, _init_p):
                handle = proj.db.select_graph("g")
                proj.g.query(_init_q, params=_init_p)
            """,
            ["proj.g.query(_init_p, _init_q)"],
        ),
        # Closed the honest way (#9157) — but then the note is stale.
        "through_the_run_funnel": (
            """
            def site(proj, _init_q, _init_p):
                handle = proj.db.select_graph("g")
                _os._run(handle, _init_q, _init_p)
            """,
            ["_os._run(_init_p, _init_q)"],
        ),
    }
    for name, (source, expected) in cases.items():
        tree = ast.parse(textwrap.dedent(source))
        func = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "site"
        )
        issued = {"_init_q", "_init_p"}
        handles = _escape_handle_names(func)
        offenders = _not_on_the_escape_handle(func, issued, handles)
        assert len(offenders) == len(expected), (
            f"the issuance detector is {'blind' if expected else 'crying wolf'} on "
            f"the {name!r} shape: expected {expected}, got {offenders}"
        )
        for fragment in expected:
            assert any(fragment in offender for offender in offenders), (
                f"the {name!r} shape did not produce an offender naming "
                f"{fragment!r}: {offenders}"
            )


def test_the_escape3_sites_are_the_mint_sites_that_use_eager_init():
    """The two inventories of this seam must be two VIEWS, not two copies.

    `test_write_ahead_mint.py::_MINT_SITES` is the existing owner of "every
    product function that mints a `:TeamMeta` graph". This file owns a slice of
    it — the sites that mint through `eager_init_query()`. Asserting the
    relationship (rather than eyeballing two hand-written sets) means a site
    added to one inventory only cannot pass unnoticed here, and the ONE
    legitimate difference (`_cmd_key_create`, which mints through another shape)
    is named instead of being an unexplained remainder.
    """
    mint_sites = _mint_sites_from_its_owner()
    assert set(_ESCAPE3_CALL_SITES) <= mint_sites, (
        "this file lists an escape-(3) site that `tests/test_write_ahead_mint.py` "
        f"does not know mints a graph: {sorted(set(_ESCAPE3_CALL_SITES) - mint_sites)} "
        "— one inventory moved without the other"
    )
    assert mint_sites - set(_ESCAPE3_CALL_SITES) == _NON_EAGER_MINT_SITES, (
        "the mint sites this file does not cover changed: "
        f"{sorted(mint_sites - set(_ESCAPE3_CALL_SITES))} vs the recorded "
        f"{sorted(_NON_EAGER_MINT_SITES)}. A mint site that migrated ONTO "
        "`eager_init_query` (or a new one that mints through it) belongs in "
        "_ESCAPE3_CALL_SITES and in the note in `onboarding/state.py` (#9157); "
        "one that migrated OFF it leaves the residual"
    )


def test_no_eager_init_site_declares_itself_yet():
    """THE COUPLING: today no escape-(3) function opens a §17.1 declaration.

    This is the assertion the deleted runtime probe only *claimed* to make. It
    turns RED the day `#9157` lands by putting a `with` over ANY scope opener
    (`_DECLARATION_PRIMITIVES`) into `sdk.py` / `hosted_api.py` around one of
    the five sites — which is precisely when the note in `onboarding/state.py`,
    the opener inventory in `projection/__init__.py` and this file must change
    together.

    ⚠️ What this does NOT see: the honest fix may instead route the statement
    through `onboarding/state.py::_run`, which opens the scope in ANOTHER file.
    No `with` appears here in that shape, so this pin stays green — and
    `test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle`
    is the pin that reds for it (the statement stops being issued on the
    `select_graph` handle). Both are needed; neither alone is the coupling.
    """
    declared: dict[str, set[str]] = {}
    for rel in ("tortoise/sdk.py", "tortoise/hosted_api.py"):
        tree, parents = _parse(rel)
        found = _declaring_functions(tree, parents)
        if found:
            declared[rel] = found
    escaped = {
        rel: sorted(names & {func for r, func in _ESCAPE3_CALL_SITES if r == rel})
        for rel, names in declared.items()
    }
    escaped = {rel: names for rel, names in escaped.items() if names}
    assert not escaped, (
        "an escape-(3) site now DECLARES itself — the residual (#9157) is being "
        f"closed: {escaped}. Update, in the same change: the note in "
        "`tortoise/onboarding/state.py`, the opener inventory in "
        "`tortoise/projection/__init__.py`, and this pin — otherwise the "
        "docstrings keep describing a gap that no longer exists"
    )


def test_the_opener_inventory_is_pinned_so_a_new_lane_moves_this_file():
    """A NEW opener in `projection/__init__.py` must red this suite.

    The instrument check below iterates the DERIVED set, so on its own it accepts
    any number of openers — measured: adding `_future_lane_write_scope()` to
    `projection/__init__.py` left 12/12 green (#9150 review, P2). The honest fix
    for the escape-(3) residual (#9157) is exactly that shape — a new named
    opener, the shape #9123 and #9195 both used — so the day it lands, this
    file's note, the opener inventory in `projection/__init__.py` and this list
    have to move TOGETHER instead of drifting back to a universal claim that the
    escape disproves.

    Derived, then compared: `_declaration_primitives()` reads the production
    source, so the expected set below is the only hand-written part.
    """
    expected = {
        "_live_write_scope",
        "_replay_write_scope",
        "_graph_metadata_write_scope",
    }
    derived = _projection_openers()
    assert derived == expected, (
        "the §17.1 opener inventory in `tortoise/projection/__init__.py` changed: "
        f"derived {sorted(derived)} vs recorded {sorted(expected)}. A new opener "
        "can authorize a cache write, so re-read the bullet list in that file and "
        "the escape-(3) note in `tortoise/onboarding/state.py` (and this list) "
        "before letting the claim stand (#9157)"
    )


def test_the_detector_population_is_pinned_by_content():
    """The DETECTOR population needs a content anchor of its own.

    The instrument check iterates `_DECLARATION_PRIMITIVES`, which is DERIVED, so
    it shrinks with the derivation — circular (#9150 review, P2). Measured:
    narrowing `_declaration_primitives()` back to
    `projection/__init__.py` (a plausible cleanup — "only projection defines
    openers") and wrapping a site in
    `tortoise/projection/edges.py::_structural_edge_write_scope` left 13/13
    green, restoring exactly the hole round 3 closed. The members defined OUTSIDE
    `projection/__init__.py` are the point of the wide population, so they are
    named here.
    """
    expected = {
        "_graph_metadata_write_scope",
        "_live_write_scope",
        "_onboarding_write_scope",
        "_replay_write_scope",
        # the two that live outside `projection/__init__.py`:
        # `_run` is `tortoise/webhook_delivery.py`'s opener (a name, not the
        # onboarding funnel — `onboarding/state.py::_run` delegates through
        # `_onboarding_write_scope`, one hop away, and is covered by that name
        # above). Recorded because the population is what the detector iterates.
        "_run",
        "_structural_edge_write_scope",  # projection/edges.py
    }
    derived = _declaration_primitives()
    assert derived == expected, (
        "the detector population changed: derived "
        f"{sorted(derived)} vs recorded {sorted(expected)}. This population is "
        "what makes `test_no_eager_init_site_declares_itself_yet` able to see a "
        "site declared through ANY opener — narrowing it re-opens the hole "
        "(#9157)"
    )


def test_the_declaration_detector_can_fail():
    """The instrument check: `_declaring_functions` must SEE every declaration.

    Without this, `test_no_eager_init_site_declares_itself_yet` would also pass
    if the detector were broken (a renamed helper, a `with` form it stopped
    matching) — an instrument that cannot fail is not evidence. It is run over
    EACH recorded opener: an earlier revision exercised only
    `_onboarding_write_scope`, so closing the residual through
    `_graph_metadata_write_scope(...)` left every pin green.
    """
    for primitive in _DECLARATION_PRIMITIVES:
        snippet = textwrap.dedent(
            f"""
            def org_create(proj, cypher):
                with {primitive}(cypher):
                    return proj.db.select_graph("g").query(cypher)
            """
        )
        tree = ast.parse(snippet)
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        assert _declaring_functions(tree, parents) == {"org_create"}, (
            f"the declaration detector no longer recognises a `with` over "
            f"{primitive}() — a site closed through that opener would leave "
            "every residual pin in this file green while the note went stale"
        )


# ── the guard's own opener inventory ─────────────────────────────────────
def test_the_guards_inventory_names_this_lane_and_its_residual():
    """`_live_write_scope`'s inventory is what ENFORCES the rule; it must agree.

    It was written as an exhaustive set of journal-first openers. `#9121` adds
    an opener that appends nothing, so the block has to say so *in its own
    terms* — otherwise the next reader trusts "only journal-first doors open
    this" and reintroduces exactly the P0 this work blocks, or reads the
    onboarding scope as a §17.1 violation. It also has to name the residual
    (`#9157`), i.e. that the escape-(3) creation writes are NOT this lane.
    """
    source = (_REPO / "tortoise/projection/__init__.py").read_text(encoding="utf-8")
    flat = " ".join(source.split())
    for phrase in (
        "onboarding.metadata",
        # the opener is NOT a `_live_write_scope` opener: the bullet says the
        # LANE appends nothing, and why that is not a contradiction.
        "THIS LANE APPENDS NOTHING",
        "not every opener",
        "#9157",
        "_onboarding_write_scope",
    ):
        assert phrase in flat, (
            f"the §17.1 opener inventory in `tortoise/projection/__init__.py` no "
            f"longer contains {phrase!r} — the declaration block at "
            "`_live_write_scope` is the file that ENFORCES the rule, so a "
            "reader who trusts it over `onboarding/state.py` would conclude the "
            "onboarding lane is journal-first (it is not) or fully delegated "
            "(it is not: #9157)"
        )


def test_the_live_scope_docstring_names_the_opener_the_lane_actually_calls():
    """The docstring must name the call `state.py` makes — and not this alias.

    `_live_write_scope`'s own docstring said the onboarding lane opens *it*, and
    one grep disproves that: `tortoise/onboarding/state.py` never imports this
    alias — `_onboarding_write_scope` returns
    `_cache_write_authorized(ONBOARDING_WRITE_LANE)`, the same ContextVar under a
    different spelling. The sentence lives in the file that ENFORCES §17.1, so a
    reader who trusts it over `state.py` learns the wrong mechanism, and the
    phrase-presence pin above cannot see it (it asserts that words exist, never
    that they are true).

    Derived, not eyeballed: the mechanism names are read out of `state.py`'s own
    opener, so re-wording the docstring around a mechanism the lane does not use
    cannot satisfy this.
    """
    state_source = (_REPO / "tortoise/onboarding/state.py").read_text(
        encoding="utf-8")
    assert "_live_write_scope" not in state_source, (
        "`tortoise/onboarding/state.py` now opens `_live_write_scope` itself — "
        "re-read the opener inventory's onboarding bullet in "
        "`tortoise/projection/__init__.py` (and this pin) (#9121)"
    )
    tree, _parents = _parse("tortoise/onboarding/state.py")
    opener = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.FunctionDef)
         and node.name == "_onboarding_write_scope"),
        None,
    )
    assert opener is not None, (
        "`tortoise/onboarding/state.py` no longer defines "
        "`_onboarding_write_scope` — the lane's §17.1 declaration moved, so "
        "every claim about how it declares must be re-derived"
    )
    names: set[str] = set()
    for node in ast.walk(opener):
        if isinstance(node, ast.Call):
            names.add(getattr(node.func, "id", None)
                      or getattr(node.func, "attr", None) or "")
        elif isinstance(node, ast.Name):
            names.add(node.id)
    mechanism = {
        name for name in names
        if name == "ONBOARDING_WRITE_LANE"
        or name.startswith("_cache_write_authorized")
        or name.startswith("_onboarding_write_scope")
    }
    assert mechanism == {"_cache_write_authorized", "ONBOARDING_WRITE_LANE"}, (
        "the onboarding lane's declaration mechanism moved: the opener now "
        f"names {sorted(mechanism)} — re-derive the claim in "
        "`tortoise/projection/__init__.py` rather than assuming it (#9121)"
    )
    projection_tree, _pp = _parse("tortoise/projection/__init__.py")
    live = next(
        (node for node in ast.walk(projection_tree)
         if isinstance(node, ast.FunctionDef) and node.name == "_live_write_scope"),
        None,
    )
    assert live is not None, (
        "`tortoise/projection/__init__.py` no longer defines "
        "`_live_write_scope` — the §17.1 opener inventory moved"
    )
    doc = ast.get_docstring(live) or ""
    missed = sorted(name for name in mechanism if name not in doc)
    assert not missed, (
        "`_live_write_scope`'s docstring describes the onboarding lane without "
        f"naming the mechanism it actually uses ({missed} absent). It claimed "
        "the lane opens THIS alias, which `tortoise/onboarding/state.py` never "
        "imports; a reader trusts the docstring over the code, and this is the "
        "file that ENFORCES §17.1 (#9121)"
    )


def test_a_diff_to_the_note_itself_selects_this_file():
    """The pin must RUN on the change it guards — a named surface REPLACES `core`.

    `test_the_declaration_note_names_the_seam_it_does_not_cover` and
    `test_the_note_and_the_ast_inventory_agree` guard the note in
    `tortoise/onboarding/state.py`, and `tools/ci_selection.py` resolves
    `tortoise/onboarding/` to the `onboarding` surface — a NAMED
    `SOURCE_PATTERNS` match, which REPLACES the `core` fallback. While this file
    was registered on `api` + `ep` and NOT on `onboarding`, an
    `onboarding`-only diff — the exact drift these two pins exist to catch —
    selected every suite EXCEPT this one, so the note could go stale green (the
    #1349/#2938 silent-drop class). The population is the set of files this
    suite actually reads, derived from the pins above rather than from a copy of
    the registration.

    `config/ci-surfaces.yml` is IN the population: this suite reads it (through
    `load_manifest()`), and it is the one file whose edit silently disables the
    pin — a registration change resolves to `core` and nothing else, so the
    change class that breaks this file is the one that must run it.
    """
    from tools.ci_selection import load_manifest, select

    manifest = load_manifest()
    this_file = Path(__file__).name
    missing: dict[str, list[str]] = {}
    for subject in (
        "tortoise/onboarding/state.py",
        "tortoise/projection/__init__.py",
        "tortoise/sdk.py",
        "tortoise/hosted_api.py",
        # read by the sibling-population pin below (a suite that pins the same
        # mint path), so a diff there must run this file too (#9150 review P2).
        "tests/test_write_ahead_mint.py",
        "config/ci-surfaces.yml",
    ):
        sel = select([subject], "pull_request", manifest)
        selected = (sel["full"] or sel["test_files"] == "ALL"
                    or this_file in set(sel["test_files"]))
        if not selected:
            missing[subject] = list(sel["surfaces"])
    assert not missing, (
        "a diff to a file this suite reads does not SELECT this file, so the "
        f"pins above cannot fail there: {missing}. Register this file on the "
        "surfaces those files resolve to in `config/ci-surfaces.yml` — a named "
        "match REPLACES the `core` fallback (#9121 review P1)"
    )


def test_the_inventory_cites_the_issue_that_owns_the_journalling_increment():
    """A pointer a reader follows must land on the tracked work, not a stub.

    The inventory names the larger increment (journal the onboarding class) as
    filed separately. That citation is load-bearing — it is how a reader weighing
    the declaration against journalling finds the work — and it was wrong (#9144,
    an auto-filed `NEVER_BACKED_UP` incident stub with `{}` detail, files no
    scope). The increment is **#9129**. No pin covered this, so nothing reddened;
    this is that pin.
    """
    source = (_REPO / "tortoise/projection/__init__.py").read_text(encoding="utf-8")
    flat = " ".join(source.split())
    assert "(`#9129`), not assumed by this lane" in flat, (
        "`tortoise/projection/__init__.py` no longer cites `#9129` for the "
        "onboarding-journalling increment — either the citation was dropped or "
        "it points somewhere else (it once pointed at `#9144`, an auto-filed "
        "NEVER_BACKED_UP incident stub that files no scope)"
    )
    assert "#9144" not in flat, (
        "`tortoise/projection/__init__.py` cites `#9144`, which is a "
        "NEVER_BACKED_UP incident stub — the onboarding-journalling increment is "
        "`#9129` (#9121 residual)"
    )
