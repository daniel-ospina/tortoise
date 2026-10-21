"""#8693 — the end-customer credential design's measured claims, pinned to the code.

Canonical document:
``docs/scoping/2026-10-19-8693-end-customer-credential-design.md``.

That document is a *design deliverable*, so its measured claims ARE the artifact.
Three of them were wrong in the first revision (PR #9178 review) because nothing
tied the prose to the code it cites:

1. per-end-user Replace/Revoke was described as shipped, while ``rotate`` and
   ``DELETE /v1/team/keys/{key_id}`` are pinned to the caller's OWN org
   (``_ensure_key_in_pinned_org(..., required=True)``) — a target key in another
   team is a ``403 "Not your API key"``, and a minted (``deleg=0``) credential
   cannot rotate anything at all;
2. §1 claimed there is no key-auth graph-creation path, while
   ``POST /v1/organizations/{org_id}/graphs`` ships one — it needs an owner-class
   ``graphs:create`` key (``_OWNER_SCOPE_ALLOWLIST``, never ``_MINTABLE_SCOPES``)
   and rejects ``deleg=0`` keys;
3. §8 cited two ``api_key_mint`` lanes as evidence for ``api_key_create`` /
   ``api_key_rotate`` — a third action name, conflated.

The tests are of two kinds, both deliberate:

* **code pins** — the property the document asserts must stay true of
  ``tortoise/hosted_api.py`` (these fail if the pin, the scope class, or the
  audit action drifts, i.e. exactly when the document becomes stale); and
* **prose pins** — the corrected sentences must still be present, and the
  refuted ones absent (these fail on the PRE-FIX document, which is the
  non-vacuity proof for a docs-only fix).

The document cites ``tortoise/hosted_api.py`` by **symbol address**
(``hosted_api.py::<symbol>``) or **route address** (``@app.<verb>("<route>")``),
never by absolute line number. That is the repo's own recorded rule — *the symbol
name is authoritative, the line number is a convenience* — already enforced for the
extractor-v4 design by ``tests/test_extractor_v4_citation_rot.py``
(``test_every_quoted_symbol_citation_resolves_at_head``, the ``#8506`` guard). This
file does not get a second, opposite-polarity instrument: pinning the number reds
every PR that edits this 4,700-line module on a doc its author has no reason to
touch, which is exactly what happened twice under #9178. The symbols and routes are
resolved with ``ast`` here, so a rename/removal still reds.

Every assertion imports nothing that needs a database: the module under test is
parsed with ``ast``, and only ``_ensure_key_in_pinned_org``, ``_MINTABLE_SCOPES``
and ``_OWNER_SCOPE_ALLOWLIST`` are imported from it, so this file belongs to the
fast pool.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"
DESIGN_DOC = (
    REPO / "docs" / "scoping" / "2026-10-19-8693-end-customer-credential-design.md"
)


# ── helpers ────────────────────────────────────────────────────────────────

def _module() -> ast.Module:
    return ast.parse(HOSTED_API.read_text(encoding="utf-8"))


def _function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(_module()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"tortoise/hosted_api.py no longer defines {name}()")


def _calls(node: ast.AST, func_name: str) -> list[ast.Call]:
    """Every ``func_name(...)`` call inside ``node``."""
    out: list[ast.Call] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        fn = sub.func
        called = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if called == func_name:
            out.append(sub)
    return out


def _kwarg_true(call: ast.Call, name: str) -> bool:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


def _string_constants(node: ast.AST) -> list[str]:
    return [
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    ]


def _membership_checks(node: ast.AST, needle: str) -> list[tuple[ast.Compare, ast.AST]]:
    """``(Compare, container)`` for membership tests of ``needle`` (``x in y``/``x not in y``).

    Structural on purpose: a membership TEST cannot be satisfied by the function's
    docstring or by the detail string of a raise, which is what made the old
    ``_string_constants`` assertions pass with the check deleted. Both operand
    orders are covered (``needle in container`` and ``needle not in container``).
    """
    out: list[tuple[ast.Compare, ast.AST]] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Compare):
            continue
        # ``strict=True`` states the invariant the loop relies on: an ``ast.Compare`` carries
        # exactly as many comparators as ops — ``comparator[i]`` is the right operand of
        # ``op[i]``, so ``a in b`` is ops=[In]/comparators=[b] and ``a in b in c`` is
        # ops=[In, In]/comparators=[b, c]. Measured on five shapes (1/1, 1/1, 2/2, 2/2, 3/3)
        # and over all 1,221 ``Compare`` nodes in ``tortoise/hosted_api.py`` (0 unequal), so it
        # raises on nothing reachable from source — the equality is a property of the grammar.
        # What it buys is therefore twofold, and neither is a truncation that happens today:
        # it is what conforms to B905, so this file cannot redden the repo-wide lint leg again
        # (#9316), and it turns a *malformed* node (only constructible by hand) into a loud
        # failure instead of a partial walk. ``strict=False`` — what #9249 left behind — buys
        # neither, so it is not a resting place.
        for op, comparator in zip(sub.ops, sub.comparators, strict=True):
            if not isinstance(op, (ast.In, ast.NotIn)):
                continue
            left = sub.left
            if isinstance(left, ast.Constant) and left.value == needle:
                out.append((sub, comparator))
            elif isinstance(comparator, ast.Constant) and comparator.value == needle:
                out.append((sub, left))
    return out


def _audit_actions(node: ast.AST) -> list[str]:
    """The exact action literals passed to ``_async_audit(request, id, "<action>", …)

    Exact, not a substring: ``api_key_created`` (an analytics event imported at
    module scope) contains ``api_key_create``, so a substring test would keep
    passing while §8's row became false.
    """
    actions: list[str] = []
    for call in _calls(node, "_async_audit"):
        if len(call.args) < 3:
            continue
        action = call.args[2]
        if isinstance(action, ast.Constant) and isinstance(action.value, str):
            actions.append(action.value)
    return actions


_CITED_LINE_NUMBER = re.compile(r"hosted_api\.py:(\d{4,5})")
#: A **symbol address** in the design doc: ``hosted_api.py::<symbol>`` — the only
#: form this document is allowed to use for the module (see its §0 note).
_SYMBOL_ADDRESS = re.compile(r"hosted_api\.py::([A-Za-z_][A-Za-z0-9_.]*)")
#: A **route address**: ``@app.<verb>("<route>")``, verbatim as registered.
_ROUTE_ADDRESS = re.compile(r'@app\.(get|post|put|delete|patch)\("([^"]+)"\)')


def _module_symbols() -> set[str]:
    """Every name the module binds at any level: functions, classes, assignments."""
    names: set[str] = set()
    for node in ast.walk(_module()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _registered_routes() -> dict[str, set[str]]:
    """``{route: {verb, …}}`` read from the ``@app.<verb>("<route>")`` decorators."""
    routes: dict[str, set[str]] = {}
    for node in ast.walk(_module()):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "app"
                and dec.args
                and isinstance(dec.args[0], ast.Constant)
                and isinstance(dec.args[0].value, str)
            ):
                continue
            routes.setdefault(dec.args[0].value, set()).add(dec.func.attr)
    return routes


def _audit_call_handlers(action: str) -> list[str]:
    """The enclosing handler of every ``_async_audit(…, "<action>", …)`` call."""
    owners: list[str] = []
    for node in ast.walk(_module()):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if action in _audit_actions(node):
            owners.append(node.name)
    return owners


def _doc_lines_matching(*needles: str) -> list[str]:
    """Doc lines that contain every needle."""
    return [
        line
        for line in _doc().splitlines()
        if all(needle in line for needle in needles)
    ]


def _doc() -> str:
    return DESIGN_DOC.read_text(encoding="utf-8")


# ── 0. the AST helper pairs ops with comparators strictly (#9316) ──────────

def test_membership_pairing_is_strict_not_truncating():
    """``_membership_checks`` zips ops with comparators *strictly*.

    An ``ast.Compare`` carries exactly as many comparators as ops — ``comparator[i]`` is
    the right operand of ``op[i]`` — measured on five shapes: ``a in b`` 1/1, ``a not in b``
    1/1, ``a in b in c`` 2/2, ``a in b == c`` 2/2, ``a < b < c < d`` 3/3. Equal counts are a
    property of the comparison grammar, not of our code, so ``strict=True`` raises on nothing
    reachable from source: `strict=False` truncated nothing in production either.

    What is pinned here is the failure *mode*, and it is the only reachable difference: a
    hand-built node with unequal lists must fail loudly rather than be walked as far as the
    shorter list. Accepting either exception type on purpose — an ``enumerate`` rewrite
    (``IndexError``) is *louder* than ``strict=True`` and equally correct, so the test must
    not force one spelling. Reverting the site to ``strict=False`` makes nothing raise, which
    is what this test exists to catch (#9316).
    """
    needle = "graphs:create"
    container = ast.Constant(value=[needle])
    # Two ops, one comparator: a chain with a link missing. No well-formed comparison can
    # look like this, so failing loudly on it costs nothing — and walking it as far as the
    # shorter list would be the silent weakening B905 exists to catch.
    malformed = ast.Compare(
        left=ast.Constant(value=needle),
        ops=[ast.In(), ast.In()],
        comparators=[container],
    )
    with pytest.raises((ValueError, IndexError)):
        _membership_checks(malformed, needle)

    # Evidence, not a guard: the invariant holds over the whole real module, so the strict
    # zip adds no failure to the corpus these pins walk. (It could only red on a CPython
    # grammar bug; that is why it is phrased as the claim the comment makes.)
    unequal = [
        (len(sub.ops), len(sub.comparators))
        for sub in ast.walk(_module())
        if isinstance(sub, ast.Compare) and len(sub.ops) != len(sub.comparators)
    ]
    assert not unequal, (
        f"hosted_api.py carries Compare nodes pairing unequal op/comparator counts ({unequal[:3]}) "
        "— the strict zip in _membership_checks would now raise, and the op/comparator "
        "pairing assumption this file's pins rest on is broken"
    )

    # The reachable positive property: pairing reaches the LATER link of a chain, so a walk
    # limited to the first link (what a shorter-list zip degrades to) would be caught.
    chained = _membership_checks(ast.parse("scopes in other in 'graphs:create'").body[0], needle)
    assert [ast.unparse(c) for _, c in chained] == ["scopes"], (
        "a chained `scopes in other in \"graphs:create\"` no longer resolves through its second "
        f"link ({[ast.unparse(c) for _, c in chained]}) — the zip stopped pairing ops with "
        "comparators past the first one"
    )


# ── 1. the lifecycle verbs are pinned to the caller's own org ──────────────

def test_rotate_carries_the_required_org_pin():
    """``rotate`` must keep the pin §5.2 measures (a cross-team target is 403)."""
    rotate = _function("rotate_api_key")
    pins = _calls(rotate, "_ensure_key_in_pinned_org")
    assert pins, (
        "rotate_api_key lost its _ensure_key_in_pinned_org call — the design doc (§5.2, §8) "
        "states a builder-org credential cannot rotate a key in another team because of it. "
        "If cross-team rotate was deliberately shipped, update §5.2/§5.3/§7 row 4/§9.1/§10 first."
    )
    assert any(_kwarg_true(call, "required") for call in pins), (
        "rotate_api_key's org pin is no longer required=True — the helper then no-ops on a "
        "None/absent org, which is the fail-OPEN edge §5.2's 403 claim rests against"
    )


def test_rotate_rejects_minted_callers():
    """A ``deleg=0`` credential cannot rotate — it cannot even rotate itself."""
    rotate = _function("rotate_api_key")
    rejects = _calls(rotate, "_reject_minted_delegated_key")
    assert rejects, (
        "rotate_api_key must keep _reject_minted_delegated_key — §3.1/§5.2 state that the "
        "end-user credential cannot self-rotate (hosted_api.py:12004 in the doc)"
    )
    assert any(
        "rotate" in arg
        for call in rejects
        for arg in _string_constants(call)  # the action label, e.g. "rotate API keys"
    ), "the minted-key rejection must still name the rotate action it refuses"


def test_delete_is_pinned_on_both_lanes():
    """``DELETE`` pins in the supabase lane AND the registry lane (§8 evidence row)."""
    revoke = _function("revoke_api_key")
    pins = _calls(revoke, "_ensure_key_in_pinned_org")
    assert len(pins) == 2, (
        f"revoke_api_key has {len(pins)} _ensure_key_in_pinned_org call(s), expected 2 "
        "(supabase lane + registry lane). The design doc's §8 row says the pin covers BOTH "
        "lanes; a lane that loses it becomes the cross-team revoke §5.3 says does not exist."
    )
    assert all(_kwarg_true(call, "required") for call in pins), (
        "both DELETE lanes must keep required=True (the resolved org owns the key "
        "unconditionally)"
    )


def test_the_pin_actually_refuses_a_cross_team_target():
    """The measured fact behind §5.2: mismatched orgs → 403, never a silent pass."""
    from fastapi import HTTPException

    from tortoise.hosted_api import _ensure_key_in_pinned_org

    with pytest.raises(HTTPException) as excinfo:
        _ensure_key_in_pinned_org("org_builder", "org_end_user", required=True)
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Not your API key"
    # and the no-pin edge really no-ops, so the pin's absence is what widens reach
    _ensure_key_in_pinned_org(None, "org_end_user", required=False)


# ── 2. key-auth graph creation exists, owner-class only ────────────────────

def test_key_auth_graph_creation_exists_and_is_owner_class():
    """§1's corrected second reason: the route ships, ``graphs:create`` gates it."""
    from tortoise.hosted_api import _MINTABLE_SCOPES, _OWNER_SCOPE_ALLOWLIST

    create = _function("create_org_graph")
    routes = [ast.unparse(d) for d in create.decorator_list]
    assert any("/v1/organizations/{org_id}/graphs" in r for r in routes), (
        "create_org_graph is no longer registered at POST /v1/organizations/{org_id}/graphs — "
        "§1/§8 would then be describing a route that does not exist"
    )
    scope_checks = _membership_checks(create, "graphs:create")
    assert scope_checks, (
        "create_org_graph lost its `\"graphs:create\" not in scopes` membership test — §1's "
        "corrected reason rests on the scope being checked (and on it being owner-class). "
        "A string constant elsewhere in the function (its docstring says graphs:create twice) "
        "cannot satisfy this assertion."
    )
    assert any("scopes" in ast.unparse(container) for _, container in scope_checks), (
        "the graphs:create test no longer consults the resolved `scopes` local"
    )
    assert any(
        isinstance(sub, ast.Compare)
        and len(sub.ops) == 1
        and isinstance(sub.ops[0], ast.Eq)
        and isinstance(sub.left, ast.Call)
        and isinstance(sub.left.func, ast.Attribute)
        and sub.left.func.attr == "get"
        and sub.left.args
        and isinstance(sub.left.args[0], ast.Constant)
        and sub.left.args[0].value == "delegation_depth"
        and isinstance(sub.comparators[0], ast.Constant)
        and sub.comparators[0].value == 0
        for sub in ast.walk(create)
    ), (
        "create_org_graph must keep its minted-key rejection — the `delegation_depth == 0` "
        "comparison, not merely a docstring or detail string naming it"
    )

    assert "graphs:create" in _OWNER_SCOPE_ALLOWLIST, (
        "graphs:create left the owner-class allowlist — §1's corrected reason says an "
        "OWNER-class key can create graphs programmatically today"
    )
    assert "graphs:create" not in _MINTABLE_SCOPES, (
        "graphs:create became MINTABLE — a per-end-user child could then provision graphs, "
        "which reverses the child policy §3.1 and §1's second reason both rely on"
    )


# ── 3. the three audit action names are not conflated ─────────────────────

def test_audit_action_names_are_distinct_and_as_documented():
    """``api_key_create``/``api_key_rotate`` are the lifecycle actions; ``api_key_mint``
    is the #2446 member self-service / recovery lane's THIRD name (§8 rows)."""
    assert "api_key_create" in _audit_actions(_function("create_api_key")), (
        "the mint handler must still audit as exactly api_key_create (§8) — asserted on the "
        "action literal handed to _async_audit, so the analytics event api_key_created "
        "cannot stand in for it"
    )
    assert "api_key_rotate" in _audit_actions(_function("rotate_api_key")), (
        "the rotate handler must still audit as exactly api_key_rotate (§8)"
    )
    assert "api_key_mint" in _audit_actions(_module()), (
        "api_key_mint (the member self-service / recovery lanes) is gone — §8's third-action "
        "row and #9177's premise (create+rotate audited, mint lanes a different action) need it"
    )


def test_every_code_address_in_the_design_resolves_at_head():
    """The design's citations are SYMBOL addresses, and those symbols must exist.

    This is the rot instrument, and it is deliberately the *same polarity* the repo
    already owns in ``tests/test_extractor_v4_citation_rot.py``
    (``test_every_quoted_symbol_citation_resolves_at_head``, the ``#8506`` guard): the
    rule is *"the symbol name is authoritative, the line number is a convenience"*.
    ``tortoise/hosted_api.py`` moves by hundreds of lines on unrelated merges — it did
    under this very PR, which shipped ``:27258``/``:27475`` and a ``:38600-38967`` span
    that no longer held ``/oauth/token`` — so pinning the absolute number reds every
    future PR that touches the module, on a doc its author has no reason to open. A
    renamed or deleted symbol still reds here, which is the guarantee that matters.

    Non-vacuity: the pre-fix document carried ZERO ``hosted_api.py::`` addresses (it
    cited numbers only), so the inventory assertion below is red on it.
    """
    doc = _doc()
    symbols = _SYMBOL_ADDRESS.findall(doc)
    routes = _ROUTE_ADDRESS.findall(doc)
    assert len(symbols) >= 15 and routes, (
        f"the design doc carries {len(symbols)} symbol address(es) and {len(routes)} route "
        "address(es) — it has gone back to absolute line numbers, or the extractor broke and "
        "this guard would otherwise pass vacuously"
    )

    stale = _CITED_LINE_NUMBER.findall(doc)
    assert not stale, (
        f"the design doc cites hosted_api.py by absolute line again ({stale[:5]}) — that is "
        "the rot class this pin exists to stop (see the doc's §0 note): use a symbol address "
        "(`hosted_api.py::<symbol>`) or a route address (`@app.<verb>(\"<route>\")`)"
    )

    known = _module_symbols()
    unresolved = sorted({name for name in symbols if name not in known})
    assert not unresolved, (
        "the design doc addresses symbols that no longer exist in tortoise/hosted_api.py: "
        f"{unresolved} — a rename or removal moved the evidence out from under the claim"
    )

    registered = _registered_routes()
    unresolved_routes = sorted(
        f'@app.{verb}("{route}")'
        for verb, route in routes
        if verb not in registered.get(route, set())
    )
    assert not unresolved_routes, (
        f"the design doc cites routes that are not registered with that verb: "
        f"{unresolved_routes} — re-resolve §0/§8's #524 claim"
    )


def test_the_doc_cites_the_two_api_key_mint_lanes_by_their_handlers():
    """§8's ``api_key_mint`` row must name the lanes, and the lanes must be the mint ones.

    ``api_key_mint`` is the ``#2446`` member self-service / recovery action, a THIRD name
    from ``api_key_create``/``api_key_rotate``. The code half resolves it structurally:
    exactly two ``_async_audit(…, "api_key_mint", …)`` call sites, in two different
    handlers, neither of which is the create/rotate handler.

    Non-vacuity: the pre-fix row named two absolute line numbers (a merge had already
    invalidated both) and named no handler at all.
    """
    handlers = _audit_call_handlers("api_key_mint")
    assert sorted(handlers) == ["_session_key_supabase", "session_key"], (
        f"the api_key_mint lanes moved: {handlers} — §8's third-action row names "
        "`session_key` and `_session_key_supabase`; update that row in the same commit"
    )
    assert set(handlers).isdisjoint({"create_api_key", "rotate_api_key"}), (
        "api_key_mint is being audited from the create/rotate handler — the three action "
        "names are exactly what §8's row exists to keep apart"
    )

    rows = _doc_lines_matching("api_key_mint", "hosted_api.py")
    assert rows, (
        "§8 must keep the api_key_mint row — it exists so the three audit action names are "
        "not conflated"
    )
    for row in rows:
        for handler in ("session_key", "_session_key_supabase"):
            assert handler in row, (
                f"§8's api_key_mint row must name the handler `{handler}` it cites — a bare "
                f"line number rots on any merge that touches the module "
                f"({row.strip()[:90]}…)"
            )


def test_the_doc_cites_the_oauth_routes_as_the_decorators_they_are():
    """Every row making the ``#524`` claim must cite the ROUTES, not a line span.

    Non-vacuity: the pre-fix §0/§8 rows cited one ``hosted_api.py:A-B`` span, and that
    span had come to exclude BOTH routes the rows are evidence for — a citation that
    measures nothing about its own claim.
    """
    registered = _registered_routes()
    for route in ("/oauth/token", "/oauth/revoke"):
        assert "post" in registered.get(route, set()), (
            f"{route} is no longer registered with POST — §0/§8's #524 evidence is gone"
        )
    assert "/.well-known/oauth-protected-resource" in registered, (
        "the RFC 9728 discovery route is gone — §0/§8 cite it as part of the #524 evidence"
    )

    rows = _doc_lines_matching("/oauth/token", "@app.")
    assert len(rows) >= 2, (
        f"§0 and §8 both make the #524 claim; only {len(rows)} row(s) cite the module's "
        "routes for it"
    )
    for row in rows:
        for route in ("/oauth/token", "/oauth/revoke"):
            assert f'@app.post("{route}")' in row, (
                f"this #524 row names `{route}` but does not cite it as the decorator "
                f'`@app.post("{route}")` — cite the route, not a line span '
                f"({row.strip()[:90]}…)"
            )
        assert not _CITED_LINE_NUMBER.search(row), (
            "this #524 row cites an absolute line span again — it rots on the next merge "
            "that touches tortoise/hosted_api.py"
        )


def test_revoke_writes_no_audit_row_until_the_doc_is_updated():
    """Pins #9177's measured premise: no ``_async_audit`` in the DELETE handler.

    This is a doc pin, not a requirement: when somebody fixes #9177 the §8 row
    ("Key revoke writes NO audit row") and §7 row 7 become false, and this
    assertion is the reminder to update them in the same PR.
    """
    revoke = _function("revoke_api_key")
    assert not _calls(revoke, "_async_audit"), (
        "revoke_api_key now writes an audit row — #9177 appears fixed. Update the design doc's "
        "§8 row ('Key revoke writes NO audit row'), §7 row 7, and §10 precondition 4, then "
        "delete this assertion (it exists so the doc cannot go stale silently)."
    )


# ── 4. the corrected prose is present and the refuted prose is gone ───────

def test_design_doc_keeps_the_corrected_claims():
    """Non-vacuity: the fragments this PR's correction ADDED are absent pre-fix.

    Per-fragment, not blanket. ``create_org_graph`` predates the correction (§3.2
    already cited it), so its presence is not evidence; its load-bearing neighbour
    ``key-auth graph creation DOES exist`` is absent pre-fix and is. The rest
    (``_ensure_key_in_pinned_org``, ``not one shipped call``, ``cross-team
    lifecycle reach``, the ``session-authed only`` REMOVAL) discriminate too —
    measured against ``1e0f0f0b1``.
    """
    doc = _doc()

    # Finding 1 — Replace/Revoke reach, measured as absent.
    assert "_ensure_key_in_pinned_org" in doc, (
        "§5.2/§8 must cite the pin that makes cross-team rotate/revoke a 403 — the pre-fix "
        "document described the per-end-user Replace/Revoke story as shipped"
    )
    assert "not one shipped call" in doc, (
        "§5.2 must state that the Replace round trip is NOT one shipped call"
    )
    assert "cross-team lifecycle reach" in doc, (
        "§5.3 must name the missing cross-team lifecycle reach as a precondition"
    )

    # Finding 2 — key-auth graph creation exists, owner-class only.
    assert "key-auth graph creation DOES exist" in doc, (
        "§1 must state the corrected reason; the pre-fix text claimed '/v1/graphs is "
        "session-authed only … no key-auth path today either'"
    )
    assert "create_org_graph" in doc, (
        "§1/§8 must cite the key-driven provisioning route (create_org_graph)"
    )
    assert "session-authed only" not in doc, (
        "the refuted claim ('POST /v1/graphs is session-authed only') must not come back as a "
        "standalone fact — the key lane exists at POST /v1/organizations/{org_id}/graphs"
    )

    # Finding 3 — the third action name is labelled, not cited as create/rotate evidence.
    assert "api_key_mint" in doc, (
        "§8 must name api_key_mint as a THIRD action, not evidence for create/rotate"
    )
