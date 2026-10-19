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
  `test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle`;
* the day one of those sites is DECLARED — the honest fix, `#9157` —
  `test_no_eager_init_site_declares_itself_yet` turns RED, so the note, the
  guard's opener inventory and this file have to move together instead of
  drifting back to a universal ("every onboarding write is declared") that the
  escape disproves;
* the fact that the detector is *capable* of firing is not argued, it is
  executed: `test_the_declaration_detector_can_fail` runs it over a synthetic
  declaration and requires a hit.

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
_DECLARATION_PRIMITIVES = (
    "_onboarding_write_scope",
    "_cache_write_authorized",
    "_live_write_scope",
)

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


def _eager_init_sites() -> dict[tuple[str, str], int]:
    """(file, enclosing function) -> count, read off the production source."""
    sites: dict[tuple[str, str], int] = {}
    for rel in ("tortoise/sdk.py", "tortoise/hosted_api.py"):
        tree, parents = _parse(rel)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "attr", None) == "eager_init_query"
            ):
                continue
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
    """Each site binds a `select_graph(...)` handle and issues the statement on it.

    This is the assertion that gives the note its meaning: `eager_init_query`
    only PRODUCES text, so the seam is "issued below the guarded handle" only
    while every caller does that. A site that migrates to the guarded handle
    (or to `_run`) fails here, so the residual cannot be closed in code while
    the docstrings still describe it.
    """
    for rel in ("tortoise/sdk.py", "tortoise/hosted_api.py"):
        tree, parents = _parse(rel)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "attr", None) == "eager_init_query"
            ):
                continue
            enclosing = _enclosing_function(node, parents)
            assert enclosing is not None
            select_graphs = [
                call for call in ast.walk(enclosing)
                if isinstance(call, ast.Call)
                and getattr(call.func, "attr", None) == "select_graph"
            ]
            assert select_graphs, (
                f"{rel}:{node.lineno} ({enclosing.name}) builds an eager-init "
                "statement but no longer resolves a `select_graph(...)` handle — "
                "the escape-(3) description in `onboarding/state.py` and in the "
                "guard's inventory is now wrong (#9157)"
            )
            issued = _bound_names(node.targets[0]) if len(node.targets) == 1 else set()
            assert issued, (
                f"{rel}:{node.lineno} binds the statement to something this walk "
                "cannot read — the inventory would silently skip it"
            )
            bound_by_select_graph = set()
            for sub in ast.walk(enclosing):
                if (
                    isinstance(sub, ast.Assign)
                    and isinstance(sub.value, ast.Call)
                    and getattr(sub.value.func, "attr", None) == "select_graph"
                ):
                    for target in sub.targets:
                        bound_by_select_graph |= _bound_names(target)
            assert bound_by_select_graph, (
                f"{rel}:{node.lineno} ({enclosing.name}) issues the eager-init "
                "statement but nothing in the function binds a `select_graph` "
                "handle by name — re-derive the residual, do not assume it"
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
    turns RED the day `#9157` lands — wrapping any of the five sites in
    `_onboarding_write_scope(...)` / `_cache_write_authorized(...)` makes the
    enclosing function appear here — which is precisely when the note in
    `onboarding/state.py`, the opener inventory in `projection/__init__.py` and
    this file must change together.
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


def test_the_declaration_detector_can_fail():
    """The instrument check: `_declaring_functions` must SEE a declaration.

    Without this, `test_no_eager_init_site_declares_itself_yet` would also pass
    if the detector were broken (a renamed helper, a `with` form it stopped
    matching) — an instrument that cannot fail is not evidence.
    """
    snippet = textwrap.dedent(
        """
        def org_create(proj, cypher):
            with _onboarding_write_scope(cypher):
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
        "the declaration detector no longer recognises a `with` over a §17.1 "
        "primitive — every residual pin in this file would pass vacuously"
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
        "THIS OPENER APPENDS NOTHING",
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
