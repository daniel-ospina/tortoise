"""#9121 / #9157 — the §17.1 seam, from residual to declared ROUTE, pinned in code.

`tests/test_9079_onboarding_declared_lane.py` owns the declaration itself: it
arms the `#9124` predicate on the guarded handle and proves the onboarding lane
keeps working with the guard closed. THIS file owned the *residual* — the five
`eager_init_query()` call sites that CREATED `:OnboardingState` on
`proj.db.select_graph(<graph>)` (§17.1's escape (3), below the handle, meeting
neither `state._run` nor `_onboarding_write_scope`) — and since `#9157` it owns
the fix: the ROUTE that takes those five statements through the declaration. The
pins flipped with the fix rather than being deleted, which is the point of
writing them as "the claim current at this head" instead of "the defect".

⛔ WHY THIS FILE IS NOT A SECOND COPY OF test_9079. A previous draft of this
work asserted the residual with a runtime probe that built its OWN handle,
monkeypatched it and issued the statement ITSELF — so it recorded "no
declaration in flight" because the *test* did not open one, not because
production does not. That is a tautology: it passes on the pre-fix tree and it
would keep passing after the residual were fixed. Everything here reads the
PRODUCTION source with `ast` and with the same reader the note is written for,
so each assertion has a way to fail:

* a SIXTH `eager_init_query()` site appearing anywhere in the package fails
  `test_the_note_and_the_ast_inventory_agree` (the note would be stale);
* a site going back to the escape handle — or being handed to the declared
  funnel ON that handle, the plausible-looking minimal edit that opens the
  declaration below the door — fails
  `test_every_eager_init_site_issues_its_statement_through_the_declared_funnel`.
  That pin reads the SINKS and the handle argument: the funnel must receive a
  `guarded_write_handle(...)` binding, not this function's own
  `select_graph(...)` binding and not `proj.g` (the projection's graph, not the
  org graph);
* a declaration WITHDRAWN from a site is caught twice over: by the funnel pin
  (the statement would be issued on the escape handle again) and by
  `tests/test_9157_eager_init_declared.py`, which drives the product entry point
  and requires this lane's reason to be in flight AT the write;
* the day one of those functions opens a declaration `with` in its OWN body,
  `test_no_escape3_function_opens_a_declaration_anywhere_yet` turns RED, so the
  note, the guard's opener inventory and this file move together instead of
  drifting back to a universal ("every onboarding write is declared");
* the fact that the detectors are *capable* of firing is not argued, it is
  executed: `test_the_declaration_detector_can_fail` runs the declaration
  detector over a synthetic declaration **for every recorded opener**, and
  `test_the_issuance_detector_reds_on_every_route_but_the_declared_one` does the
  same for the route pin over five shapes (escape handle, `proj.g`, an unknown
  sink, the funnel on a guarded handle, the funnel on the escape handle).

The claim these pins protect, stated in the terms of the code: every statement
this package issues **through `state._run`** runs inside the declaration, and
since `#9157` that INCLUDES the `eager_init_query()` callers, which reach it via
`run_eager_init` on a `guarded_write_handle`. Escape (3) itself is not closed —
it is structural — and the records this file pins are required to say so.

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
from functools import cache
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
#: ⚠️ Scope of what this detects: a `with` in the SAME function. The fix that
#: LANDED (#9157) routes the statement through
#: `onboarding/state.py::run_eager_init`, which opens its scope in ANOTHER file —
#: that route is caught by
#: `test_every_eager_init_site_issues_its_statement_through_the_declared_funnel`,
#: which reds when the statement stops being issued through the funnel (or is
#: handed to it on the escape handle).

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


@cache
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
) -> list[tuple[int, str, str | None, tuple[str, ...], ast.Call]]:
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
    sinks: list[tuple[int, str, str | None, tuple[str, ...], ast.Call]] = []
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
        sinks.append((call.lineno, callee, receiver, tuple(sorted(passed)), call))
    return sinks


#: The §17.1 sinks an eager-init statement may legitimately be issued through
#: AFTER #9157. The escape-(3) handle (`<handle>.query(...)` where `<handle>` is
#: this function's own `select_graph(...)` binding) is no longer one of them, and
#: neither is `proj.g` — that handle is bound to the projection's graph, not the
#: org graph the statement names.
_DECLARED_EAGER_INIT_SINKS = ("run_eager_init", "_run")


def _not_through_the_declared_funnel(
    enclosing: ast.AST, issued: set[str], handles: set[str]
) -> list[str]:
    """Sinks where the statement leaves the declared route, rendered.

    Two failure modes, named separately because they fail differently:
    re-issuing on the escape-(3) handle restores the #9157 defect (an
    unjournalled, undeclared write no door can see), while issuing on `proj.g`
    writes the onboarding row into the projection's OWN graph — measured, not
    hypothetical: the org mint binds `proj` to the SDK's graph and opens
    `org_<id>` separately, so `proj.g` is the wrong target.
    """
    # Names bound from `guarded_write_handle(...)` inside this function are the
    # ONLY handles the funnel may be handed. Without this set the check would
    # pass `run_eager_init(<this function's select_graph binding>, ...)` — the
    # declaration opened, the guard never reached — which is the whole #9157
    # defect with a different spelling (measured: the first version of this pin
    # certified exactly that).
    guarded_names: set[str] = set()
    for sub in ast.walk(enclosing):
        if (
            isinstance(sub, ast.Assign)
            and isinstance(sub.value, ast.Call)
            and "guarded_write_handle"
            in (getattr(sub.value.func, "attr", None)
                or getattr(sub.value.func, "id", "") or "")
        ):
            for target in sub.targets:
                guarded_names |= _bound_names(target)
    offenders: list[str] = []
    for lineno, callee, receiver, passed, call in _statement_sinks(enclosing, issued):
        leaf = callee.rsplit(".", 1)[-1]
        if leaf in _DECLARED_EAGER_INIT_SINKS:
            handle = ast.unparse(call.args[0]) if call.args else "<none>"
            if isinstance(call.args[0], ast.Name) if call.args else False:
                name = call.args[0].id
                if name in handles:
                    offenders.append(
                        f"line {lineno}: {callee}({handle}, {', '.join(passed)}) — "
                        "the DECLARATION is opened around this function's own "
                        f"escape-(3) handle {name!r}: the guard never sees the "
                        "write, which is the #9157 defect with the funnel in "
                        "front of it"
                    )
                    continue
                if name not in guarded_names:
                    offenders.append(
                        f"line {lineno}: {callee}({handle}, {', '.join(passed)}) — "
                        f"handle {name!r} is not provably a "
                        "`guarded_write_handle(...)` binding; a funnel is only "
                        "declared if the write reaches a door that reads it"
                    )
                    continue
                continue
            if "guarded_write_handle" in handle:
                continue
            if "select_graph" in handle:
                offenders.append(
                    f"line {lineno}: {callee}({handle}, {', '.join(passed)}) — an "
                    "inline `select_graph(...)` handle handed to the funnel: the "
                    "declaration is opened below the door (#9157)"
                )
                continue
            if handle.endswith(".g"):
                offenders.append(
                    f"line {lineno}: {callee}({handle}, {', '.join(passed)}) — "
                    "`proj.g` is bound to the projection's graph, not the org "
                    "graph this statement names"
                )
                continue
            offenders.append(
                f"line {lineno}: {callee}({handle}, {', '.join(passed)}) — handle "
                "is not provably a `guarded_write_handle(...)` binding"
            )
            continue
        if leaf == "query" and receiver in handles:
            offenders.append(
                f"line {lineno}: {callee}({', '.join(passed)}) — issued on this "
                f"function's escape-(3) handle(s) {sorted(handles)}: the #9157 "
                "escape is back"
            )
            continue
        if leaf == "query" and (receiver in {"g", "proj"}
                                or callee.endswith(".g.query")):
            offenders.append(
                f"line {lineno}: {callee}({', '.join(passed)}) — `proj.g` is bound "
                "to the projection's graph, not the org graph this statement names"
            )
            continue
        offenders.append(
            f"line {lineno}: {callee}({', '.join(passed)}) — not one of the "
            f"declared sinks {_DECLARED_EAGER_INIT_SINKS}"
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


#: Openers that DELEGATE the permit instead of calling `_cache_write_authorized`
#: themselves (`#9391`): `{opener: the opener it hands the permit to}`.
#:
#: Declared on purpose, and DECLARED rather than derived transitively, because the
#: population is a detector: the honest derivation is "this function can put a
#: declaration in flight", and a one-level delegation is that, while a blanket
#: transitive closure would also enrol every CALLER of an opener
#: (`rebuild_from_record` calls `_replay_write_scope()`) and change what the
#: escape-(3) instrument sees. `#9391` routed `_replay_write_scope()` through the
#: new `_handle_only_write_scope()` (so the raw-query closure travels with the
#: scope stack); without this map the inventory would have silently SHRUNK —
#: `_replay_write_scope` would drop out and any site wrapped in
#: `with _replay_write_scope():` would become invisible to the detector, which is
#: the #9157 hole. Both names are therefore recorded in the two lists below.
_DELEGATED_OPENERS: dict[str, str] = {
    "_replay_write_scope": "_handle_only_write_scope",
}


def _openers_under(root: Path) -> set[str]:
    """Functions under `root` that can put a §17.1 declaration in flight.

    A function joins the population when its body declares through
    `_cache_write_authorized` DIRECTLY, or through a DECLARED delegation hop
    (`_DELEGATED_OPENERS`).

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
            delegated_to = _DELEGATED_OPENERS.get(node.name)
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                callee = (getattr(inner.func, "id", None)
                          or getattr(inner.func, "attr", None))
                if callee == "_cache_write_authorized" or (
                        delegated_to is not None and callee == delegated_to):
                    found.add(node.name)
                    break
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
    """Enclosing functions of a `with` that opens a §17.1 declaration.

    ⚠️ Deliberately FUNCTION-granular, not statement-granular: a `with` anywhere
    inside an escape-(3) function is worth a human look, because that function
    is the site. The cost is a FALSE RED when an unrelated `with` is added
    nearby — accepted, and the failure message names both causes so a lane is
    never told it closed #9157 when it did not. Statement-level resolution
    (walk the `With` body for the issued statement name, as `_statement_sinks`
    does) is the fix if that turns out to hurt more than it helps.
    """
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
def test_the_declaration_note_states_the_route_that_closed_the_seam():
    """`state.py`'s declaration must say HOW the class is covered, where a reader looks.

    Flipped with `#9157`. While the residual was open this asserted the note
    carried its NEGATIVE ("do not close escape (3) believing onboarding is
    covered"). Now the note has to carry the POSITIVE, and each part is a way to
    regress into the old over-claim or a new one:

    * (a) the mechanism — `eager_init_query` statements now ride
      `run_eager_init` (`_run` + the class check), not `select_graph`;
    * (b) BOTH halves named, because either alone is a broken fix: the guarded
      handle without the declaration is refused once the guard arms, and the
      declaration without the guarded handle is a ContextVar nothing reads;
    * (c) the residual that is STILL open stated as still open, so a reader does
      not conclude that escape (3) itself was closed by this change.
    """
    source = (_REPO / "tortoise/onboarding/state.py").read_text(encoding="utf-8")
    flat = " ".join(source.split())
    for phrase in ("eager_init_query", "run_eager_init", "guarded_write_handle",
                   "#9157", "Escape (3) itself is NOT closed"):
        assert phrase in flat, (
            f"`tortoise/onboarding/state.py` no longer contains {phrase!r} — the "
            "note that tells a door inventory HOW this class is declared has "
            "been removed or reworded (#9157)"
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
def test_every_eager_init_site_issues_its_statement_through_the_declared_funnel():
    """Each site issues the statement through the DECLARED funnel when available.

    This pin has now flipped with the fix it was built to see (#9157). Written
    against the residual, it asserted "issued ON the `select_graph(...)` handle"
    — so the honest fix (routing the statement through `onboarding/state.py`)
    turned it RED and forced this change, which is the coupling working:

    * the statement must be issued through `run_eager_init`/`_run` — the route
      that opens `_onboarding_write_scope` in another module — on a handle that
      is NOT one of this function's own `select_graph(...)` bindings;
    * it must NOT be issued on the escape-(3) handle (the residual, restored),
      and not on `proj.g` either (the wrong-graph violation: the org graph is
      not the projection's own).

    The escape-(3) handle BINDING may remain in these functions — it is the read
    handle for the idempotency/`_seen` probe in three of them — so the pin reads
    the statement's SINKS, not the function's bindings.
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
            # #9157: an empty binding set is now the CLEAN shape for a site
            # whose only graph access is the guarded handle — `sdk.org_create`
            # opened `select_graph(...)` solely to issue this statement, and the
            # fix removed it. Three other sites keep the binding as their READ
            # handle (the `_seen`/idempotency probe), so both shapes are legal;
            # what the offenders list below decides is where the STATEMENT goes.
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
            offenders = _not_through_the_declared_funnel(enclosing, issued, handles)
            assert not offenders, (
                f"{rel}:{node.lineno} ({enclosing.name}) issues the eager-init "
                f"statement outside the declared funnel: {offenders}. The "
                "statement must go through `_os.run_eager_init(...)` (which opens "
                "`_onboarding_write_scope`) on a `guarded_write_handle` bound to "
                "the ORG graph — issuing it on `select_graph(...)` restores the "
                "#9157 escape, and issuing it on `proj.g.query(...)` writes the "
                "onboarding row into the WRONG graph"
            )


def test_the_issuance_detector_reds_on_every_route_but_the_declared_one():
    """The instrument check for the pin above — it must fire on every wrong route.

    Flipped with `#9157`, and the flip is the point: the old instrument certified
    "issued on the escape handle" as CORRECT, so once the fix landed it would have
    certified the restored defect. The three wrong routes are now distinct
    offenders (the escape handle, `proj.g`, and a sink that is neither) and the
    two right ones are clean — so the pin cannot pass by being blind, and cannot
    pass by crying wolf either.
    """
    cases = {
        # The #9157 defect, restored: issued on this function's escape-(3) handle.
        "on_the_escape_handle": (
            """
            def site(proj, _init_q, _init_p):
                handle = proj.db.select_graph("g")
                handle.query(_init_q, params=_init_p)
            """,
            ["the #9157 escape is back"],
        ),
        # The WRONG-graph route: the projection is bound to another graph.
        "on_the_projection_handle": (
            """
            def site(proj, _init_q, _init_p):
                proj.g.query(_init_q, params=_init_p)
            """,
            ["not the org graph"],
        ),
        # Any other sink: not declared, and invisible to this pin's rule.
        "on_an_unknown_sink": (
            """
            def site(proj, _init_q, _init_p):
                proj.db.some_new_door(_init_q, _init_p)
            """,
            ["not one of the declared sinks"],
        ),
        # The landed route: `run_eager_init` on a `guarded_write_handle`.
        "through_run_eager_init": (
            """
            def site(proj, _init_q, _init_p):
                handle = guarded_write_handle(proj, "org_x")
                _os.run_eager_init(handle, _init_q, _init_p)
            """,
            [],
        ),
        # The same route spelled through the funnel directly.
        "through_the_run_funnel": (
            """
            def site(proj, _init_q, _init_p):
                handle = guarded_write_handle(proj, "org_x")
                _os._run(handle, _init_q, _init_p)
            """,
            [],
        ),
        # ⛔ THE PLAUSIBLE-LOOKING MINIMAL EDIT, and the defect with the funnel
        # in front of it: the declaration is opened, the guard never sees the
        # write. This is the shape the FIRST version of this pin certified clean.
        "funnel_over_the_escape_handle": (
            """
            def site(proj, _init_q, _init_p):
                handle = proj.db.select_graph("g")
                _os.run_eager_init(handle, _init_q, _init_p)
            """,
            ["escape-(3) handle"],
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
        offenders = _not_through_the_declared_funnel(func, issued, handles)
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


def test_no_escape3_function_opens_a_declaration_anywhere_yet():
    """THE COUPLING: today no escape-(3) function opens a §17.1 declaration.

    WHAT THIS MEASURES IS COARSER THAN ITS NAME USED TO SUGGEST, on purpose: it
    reds on a declaration-scope `with` ANYWHERE inside an escape-(3) function
    (`sdk.py` / `hosted_api.py`), not only around one of the five
    `eager_init_query()` sites. Those functions ARE the sites, so a scope
    appearing in one is worth a human look either way — but the two possible
    causes are different, and the failure message names both rather than
    assuming the interesting one.

    It still turns RED the day `#9157` lands: putting a `with` over any scope
    opener (`_DECLARATION_PRIMITIVES`) into one of those functions around one of
    the five sites is precisely when the note in `onboarding/state.py`, the
    opener inventory in `projection/__init__.py` and this file must change
    together.

    ⚠️ What this does NOT see: the LANDED fix (#9157) routes the statement
    through `onboarding/state.py::run_eager_init`, which opens the scope in
    ANOTHER file. No `with` appears here in that shape, so this pin stays green
    — and
    `test_every_eager_init_site_issues_its_statement_through_the_declared_funnel`
    is the pin that reds for it (the statement stops being issued through the
    declared funnel, or is handed to it on the escape handle). Both are needed;
    neither alone is the coupling.
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
        "a §17.1 declaration scope now appears INSIDE an escape-(3) function: "
        f"{escaped}. TWO possible causes, check which before acting. "
        "(a) #9157 is being closed — then update, in the same change: the note "
        "in `tortoise/onboarding/state.py`, the opener inventory in "
        "`tortoise/projection/__init__.py`, and this pin, otherwise the "
        "docstrings keep describing a gap that no longer exists. "
        "(b) an UNRELATED `with` was added in that function — this pin is "
        "function-granular on purpose and cannot tell the two apart; move the "
        "scope out of the escape-(3) function, or tighten this pin to "
        "statement level."
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
        # #9391: `_replay_write_scope()` now DELEGATES to this opener so the
        # raw-query closure travels with the scope stack, so both names are in
        # the bullet list and both are recorded here — the delegation is declared
        # in `_DELEGATED_OPENERS`, which is what keeps the replay opener in the
        # population instead of silently dropping it.
        "_handle_only_write_scope",
        "_graph_metadata_write_scope",
        # #9210 added the PRE-WIPE RESTORE lane to the same bullet list (the
        # restore path writes records that did not come from the journal, so it
        # is neither a live door nor a replay); #9218 landed it and moved this
        # inventory, which is the pin doing its job.
        "_prewipe_restore_write_scope",
        # #9210's second lane, landed with #9347: the SYNTHETIC fold writes from
        # the same pre-wipe capture through a fold rather than a restore
        # statement, which is why it is a distinct opener and a distinct row in
        # the same bullet list — the pin firing again on the same rule.
        "_prewipe_synthetic_write_scope",
        # #9206 landed its three SDK write lanes with their openers (#9349): the
        # apply-first lane (the write is issued BEFORE the record, so the door
        # may not authorize it after the fact), the derived-recompute lane, and
        # the test-fixture lane that shadows rather than defers. They are new
        # openers in this same inventory, so the list moves with them — which is
        # what this pin exists to force, and it fired on main before this file
        # was carried forward here.
        "_apply_first_write_scope",
        "_derived_recompute_write_scope",
        "_test_fixture_write_scope",
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
        # #9391: both halves of the replay declaration are recorded — the opener
        # that holds the permit and the one it delegates to (`_DELEGATED_OPENERS`).
        "_handle_only_write_scope",
        "_live_write_scope",
        "_onboarding_write_scope",
        "_replay_write_scope",
        "_prewipe_restore_write_scope",
        # #9347 landed the second pre-wipe lane (the synthetic fold), so it joins
        # the content anchor for the same reason its restore sibling is here.
        "_prewipe_synthetic_write_scope",
        # the three #9206 write lanes (#9349) are openers in the same projection
        # inventory, so they belong to the population the detector iterates.
        "_apply_first_write_scope",
        "_derived_recompute_write_scope",
        "_test_fixture_write_scope",
        # those defined OUTSIDE `projection/__init__.py`:
        # `_run` is `tortoise/webhook_delivery.py`'s opener (a name, not the
        # onboarding funnel — `onboarding/state.py::_run` delegates through
        # `_onboarding_write_scope`, one hop away, and is covered by that name
        # above). Recorded because the population is what the detector iterates.
        "_run",
        "_structural_edge_write_scope",  # projection/edges.py
        # #9210/#9218 added the watermark re-establish path, which declares its
        # own cache write from `tortoise/event_store.py` — outside projection
        # entirely, and therefore exactly the class this wide population exists
        # to see.
        "reestablish_watermark",
    }
    derived = _declaration_primitives()
    assert derived == expected, (
        "the detector population changed: derived "
        f"{sorted(derived)} vs recorded {sorted(expected)}. This population is "
        "what makes `test_no_escape3_function_opens_a_declaration_anywhere_yet` "
        "able to see a "
        "site declared through ANY opener — narrowing it re-opens the hole "
        "(#9157)"
    )


def test_the_declaration_detector_can_fail():
    """The instrument check: `_declaring_functions` must SEE every declaration.

    Without this, `test_no_escape3_function_opens_a_declaration_anywhere_yet`
    would also pass
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
    `SOURCE_PATTERNS` match, which REPLACES the `core` fallback. Before the
    `onboarding` arm existed, an `onboarding`-only diff — the exact drift these
    two pins exist to catch — selected every suite EXCEPT this one, so the note
    could go stale green (the #1349/#2938 silent-drop class). The population is
    the set of files this suite actually reads — the subjects below, derived
    from the pins above rather than copied from the registration. It is
    deliberately NOT every file the suite PARSES: `_declaration_primitives()`
    walks all 242 modules under `tortoise/`, and covering that whole population
    is what the six arms in `config/ci-surfaces.yml` do (#9150 review: with
    `api` + `core` + `onboarding` only, the 12 modules resolving solely to
    `ep`/`sdk`/`eval` — `dream.py` first — could take a new §17.1 opener on a PR
    that never ran this file).

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
        # One ARM-ONLY subject per CI arm this suite is registered on. Without
        # these, withdrawing the `api` / `sdk` / `ep` / `eval` rows leaves every
        # gate green while 24 modules lose the reachability those rows exist
        # for (measured: `--integrity` exit 0, this file 15 passed, the whole
        # `test_ci_selection.py` 274 passed) — the arm-withdrawal class the
        # `api` comment in `config/ci-surfaces.yml` names. Each of these files
        # resolves SOLELY to its arm, so it is the only subject that can red the
        # pin when that row goes.
        "tortoise/selfhost.py",      # api
        "tortoise/reader.py",        # sdk
        "tortoise/dream.py",         # ep
        "tortoise/cross_lens.py",    # eval
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


def test_the_plan_doc_and_the_storage_ledger_carry_the_CLOSED_residual():
    """The TWO record docs must say the residual is CLOSED — and stay truthful.

    Flipped with `#9157`. These two records had no pin at all (#9150 review),
    and then they carried the residual's NEGATIVE — §17.1's `onboarding
    metadata` row said "but NOT the five `eager_init_query()` sites … do not
    read this row as 'the class has no second writer'", and the increment-4
    tracker said "STILL OPEN: … `#9157`". After the fix that is the same
    falsehood in the mirror image: an auditor doing exactly the door inventory
    the fix exists to unblock would read "not covered".

    So the assertion is shaped to the CLOSED state and to the honesty that has
    to survive it:

    * the issue is named, and named as CLOSED, in both records;
    * the mechanism is named (`run_eager_init`, `guarded_write_handle`) so a
      reader can check the claim rather than trust it;
    * ⚠️ **escape (3) itself is still stated as OPEN** — the fix removes one
      class from it, and a record that reads as "escape (3) is closed" would be
      a worse over-claim than the one this pin replaced.
    """
    for rel, what in (
        ("docs/plans/2026-10-14-7951-atomic-write-outbox.md", "the increment-4 tracker"),
        ("docs/architecture/STORAGE-ARCHITECTURE.md", "§17.1's door ledger"),
    ):
        flat = " ".join((_REPO / rel).read_text(encoding="utf-8").split())
        for phrase in ("#9157", "run_eager_init", "guarded_write_handle"):
            assert phrase in flat, (
                f"{what} (`{rel}`) no longer contains {phrase!r}. The record a "
                "door inventory reads must state HOW the onboarding class is now "
                "covered (#9157), not merely carry the issue number"
            )
        assert "STILL OPEN: the five" not in flat, (
            f"{what} (`{rel}`) still says the five eager-init sites are STILL "
            "OPEN — that is the #9157 defect described as current fact, and it "
            "contradicts the route the code now takes"
        )
        assert "NOT closed" in flat or "not closed" in flat, (
            f"{what} (`{rel}`) must keep saying that §17.1 escape (3) itself is "
            "NOT closed: this fix removes one class from that hole, and a record "
            "that reads as 'escape (3) closed' is the worse over-claim"
        )
        # ⛔ AND THE STILL-OPEN CLAIM MUST NAME AN INSTANCE A READER CAN CHECK
        # (#9409 review, P3). "Escape (3) is structural" is unfalsifiable on its
        # own, so the record reads as a formality; the measured instance is
        # `__main__.py::_cmd_key_create`, which still mints its `:TeamMeta` on a
        # raw `select_graph(...)` handle. Naming it is what makes the sentence
        # auditable — and it is the site a later lane will close, so the record
        # is also the pointer to that work.
        for phrase in ("_cmd_key_create", "select_graph"):
            assert phrase in flat, (
                f"{what} (`{rel}`) says escape (3) is not closed but no longer "
                f"names {phrase!r} — the claim is then unfalsifiable and reads "
                "as a formality. Name the measured instance "
                "(`tortoise/__main__.py::_cmd_key_create` mints its `:TeamMeta` "
                "on a raw `select_graph(...)` handle) so a reader can check it "
                "and a later lane can close it (#9409 review)"
            )
        for stale in ("BUT NOT the five", "meet neither `state._run` nor"):
            assert stale not in flat, (
                f"{what} (`{rel}`) still carries {stale!r} — the present-tense "
                "residual this fix closed, in the mirror image; the pin used to "
                "assert phrase PRESENCE, so stale text satisfied it (#9409 review)"
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
