"""#9121 — the onboarding lane is a DECLARED §17.1 lane, not an open handle.

`tortoise/onboarding/state.py::_run` used to justify itself by pointing at a
*residual*: the write reached the cache through the raw guarded handle
(`proj.g.query`) because `#7951` had closed only the public
`FalkorProjection.query` lane, and the handle was still open. That rationale
EXPIRES with `#9079` — the child that closes the handle to an *undeclared*
mutating statement. When it lands, every onboarding statement issued THROUGH
`state._run` / `seed._run` would be refused were it undeclared, and the callers
are all fail-open (`hosted_api._maybe_apply_completion` -> `except Exception:
return False`, `mcp_server._maybe_onboarding_auto_complete` -> `return`,
`selfhost.ensure_selfhost_onboarding_once` best-effort), so a refusal there is
SILENT: an org simply never reaches `complete`.

⚠️ That is NOT the whole class, and this suite does not claim it is: the five
`eager_init_query()` call sites that CREATE `:OnboardingState` execute their
statement on `proj.db.select_graph(...)` — escape (3) of the guard's own
inventory — so they meet neither `_run` nor the declaration. They are
unjournalled AND undeclared (residual named on `onboarding_write_scope`'s
docstring, filed as #9157), and `test_the_eager_init_seam_is_undeclared...` /
`test_the_declaration_note_names_the_seam_it_does_not_cover` pin both the
reality and the note, so the narrowing is measured rather than prose.

So this suite pins the chosen shape — **(a) the declared lane**: the write runs
inside a named `_live_write_scope` (`onboarding.state.onboarding_write_scope`,
shape (a) vs (b) argued in its docstring) — and pins it against the guard, not
against a stub of it. `_ArmedWriteHandle` below is `#9079`'s rule applied one
layer ahead of the child:

    a mutating statement through the write handle is REFUSED unless a
    sanctioned scope is in flight (`_CACHE_WRITE_AUTHORIZED.get() is None`)

— the same predicate `FalkorProjection.apply` and `FalkorProjection.query`
already enforce, and the same conservative `_statement_writes` classifier. A
lane that still reached around the guard would fail here today, before the
child lands, instead of silently disabling onboarding after it.

⛔ What this suite does NOT do: it does not make `_run` tolerate a refusal. A
swallowed `DirectCacheWriteRefused` is indistinguishable from success at every
fail-open call site, and `test_without_the_declaration_the_lane_is_refused...`
is the witness for exactly that — the declaration is load-bearing, not
cosmetic.
"""
from __future__ import annotations

import ast
import contextlib
import inspect
from pathlib import Path

import pytest

from tortoise.onboarding import seed as _seed
from tortoise.onboarding import state as _os
from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _statement_writes,
)


@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK with a configured JSONL journal, so §17.1's guard is ARMED.

    Lane-agnostic, like `test_7951_enforce_atomic_write.py`'s fixture of the
    same name: the explicit `db_path` makes the projection embedded when no
    URI is configured, and the SDK's own construction is redirected to the
    docker server in a URI-set session. `event_log_path` is what arms §17.1.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "declared-lane.db"),
                      event_log_path=str(journal))
    try:
        yield sdk
    finally:
        sdk.close()


class _ArmedWriteHandle:
    """`#9079`'s rule, armed AHEAD of `#9079`.

    Wraps the projection's write handle and refuses a mutating statement
    issued with no sanctioned scope in flight — the exact predicate the child
    will add to `_GuardedGraph.query`, using the same `_statement_writes`
    classifier the existing gate uses (so an unreadable statement counts as a
    write). Every statement is RECORDED with the scope reason in flight at the
    moment it ran, which is what makes "the write ran INSIDE its declaration"
    assertable rather than inferable.

    ⚠️ This probe is the instrument, and an instrument that cannot fail is not
    evidence: `test_the_armed_handle_refuses_an_undeclared_statement` asserts
    it refuses, so `test_an_org_onboards_to_complete...` passing means the
    declaration carried it.
    """

    def __init__(self, inner):
        self._inner = inner
        #: (scope reason in flight, is-mutating, statement head)
        self.seen: list[tuple[str | None, bool, str]] = []

    def query(self, cypher, params=None, **kwargs):
        reason = _CACHE_WRITE_AUTHORIZED.get()
        writes = _statement_writes(cypher)
        self.seen.append((reason, writes, " ".join((cypher or "").split())[:90]))
        if writes and reason is None:
            raise DirectCacheWriteRefused(
                "armed write handle (#9079 shape): a mutating statement was "
                "issued with no sanctioned scope in flight. statement="
                + " ".join((cypher or "").split())[:160]
            )
        return self._inner.query(cypher, params=params, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture
def armed(sdk_with_journal, monkeypatch):
    """The journal-backed projection with its write handle ARMED."""
    sdk = sdk_with_journal
    proj = sdk._get_proj()
    assert proj._journal_backed is True, (
        "the fixture did not arm §17.1 — the guard would be inert and this "
        "suite vacuous"
    )
    handle = _ArmedWriteHandle(proj.g)
    monkeypatch.setattr(proj, "g", handle)
    return sdk, proj, handle


def _hosted_sdk(monkeypatch, sdk):
    """Point the hosted lane's per-org handle at the journal-backed SDK."""
    from tortoise import hosted_api
    monkeypatch.setattr(hosted_api, "_make_sdk", lambda **_kw: sdk)
    return hosted_api


def test_an_org_onboards_to_complete_with_the_write_handle_armed(
        armed, monkeypatch):
    """The P0 this issue exists to prevent: with the handle guard ARMED, a
    hosted org still onboards all the way to `STATUS_COMPLETE`.

    Asserted on the NODE (`read_onboarding_node`), not on a truthy return:
    every caller in this lane is fail-open, so a return value is exactly the
    signal that cannot distinguish "completed" from "refused and swallowed".

    RED without the declaration: the first `MERGE` raises
    `DirectCacheWriteRefused` (see the mutation witness below), so the node is
    never written and `_maybe_apply_completion` returns False.
    """
    sdk, proj, _handle = armed
    hosted_api = _hosted_sdk(monkeypatch, sdk)
    org = "org9121"

    _os.ensure_onboarding_state_node(proj, org, fork=_os.FORK_SELF)
    _os.write_fork(proj, org, _os.FORK_SELF)
    for step in ("harness-connected", "first-points-filed", "decide-completed"):
        _os.write_completed_step(proj, org, step)

    assert hosted_api._maybe_apply_completion(org) is True, (
        "the completion gate did not transition the org — the status write was "
        "refused or lost"
    )

    node = _os.read_onboarding_node(proj, org)
    assert node is not None, "the org's OnboardingState node was never written"
    assert node["status"] == _os.STATUS_COMPLETE, (
        f"the org did not reach complete: status={node.get('status')!r}"
    )
    assert set(_os.completed_steps(proj, org)) >= {
        "team-named", "harness-connected", "first-points-filed",
        "decide-completed"}


def test_every_mutating_onboarding_statement_ran_inside_the_declaration(
        armed):
    """Indicator 2/3: the writes run INSIDE a named declaration.

    This is the scope READ the definitional rule asks for — not "a scope
    exists somewhere" but "at the moment each mutating statement was issued,
    the declaration was in flight and it was THIS lane's". A future swallow of
    `DirectCacheWriteRefused`, or a write routed around the seam, fails here
    instead of passing quietly.
    """
    _sdk, proj, handle = armed
    org = "org9121-declared"

    _os.ensure_onboarding_state_node(proj, org, fork=_os.FORK_SELF)
    _os.write_completed_step(proj, org, "harness-connected")
    # The seed module's duck-typed primitive is the OTHER raw-handle seam in
    # this package; it declares itself with its own name.
    assert _seed.find_subject_by_name(proj, "nobody-seeded-yet") is None

    mutating = [entry for entry in handle.seen if entry[1]]
    assert mutating, (
        "no mutating statement was observed — the probe or the seam moved, so "
        "this assertion would be vacuous"
    )
    undeclared = [entry for entry in mutating if entry[0] is None]
    assert not undeclared, (
        "an onboarding write ran with NO sanctioned scope in flight: "
        f"{undeclared!r}"
    )
    wrong_lane = [
        entry for entry in mutating
        if entry[0] not in (_os.ONBOARDING_WRITE_SCOPE, "onboarding.seed")]
    assert not wrong_lane, (
        "an onboarding write ran inside a scope that does not name this lane, "
        f"so the declaration does not say what authorized it: {wrong_lane!r}"
    )
    # The seed primitive's statements carry the seed lane's name, not the
    # state lane's — the two seams stay distinguishable in a refusal message.
    assert handle.seen[-1][0] == "onboarding.seed"


def test_the_declaration_does_not_outlive_the_statement(armed):
    """The scope is a per-call `with` block, never an armed-by-default flag.

    If the declaration leaked, the NEXT writer on this thread — in another
    package, on the same projection — would inherit an authorization it never
    declared, which is the second-writer hole §17.1 exists to close.
    """
    _sdk, proj, _handle = armed
    assert _CACHE_WRITE_AUTHORIZED.get() is None

    _os.ensure_onboarding_state_node(proj, "org9121-reset")

    assert _CACHE_WRITE_AUTHORIZED.get() is None, (
        "the onboarding write left the cache-write authorization armed after "
        "it returned — the declaration outlived its statement"
    )


def test_the_armed_handle_refuses_an_undeclared_statement(armed):
    """The probe can fail — so the passing tests above mean something.

    This is `#9079`'s behaviour, one layer early: the SAME statement the lane
    issues is refused when it is issued with no declaration.
    """
    _sdk, proj, _handle = armed
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query(
            "MERGE (n:OnboardingState {org_id: $org_id}) SET n.status = 'x'",
            params={"org_id": "org9121-undeclared"})


def test_without_the_declaration_the_lane_is_refused_and_the_org_never_completes(
        armed, monkeypatch):
    """The mutation witness: remove the declaration and the P0 returns.

    With `_run`'s scope neutralised, the guard refuses the write; the refusal
    reaches the caller as an exception that the fail-open lanes above turn
    into `False`/`return`, and the org simply never completes. That is the
    regression #9121 blocks #9079 with — it is demonstrated here rather than
    asserted in prose.
    """
    sdk, proj, _handle = armed
    hosted_api = _hosted_sdk(monkeypatch, sdk)

    @contextlib.contextmanager
    def _no_declaration(reason: str = _os.ONBOARDING_WRITE_SCOPE):
        yield

    monkeypatch.setattr(_os, "onboarding_write_scope", _no_declaration)

    with pytest.raises(DirectCacheWriteRefused):
        _os.ensure_onboarding_state_node(proj, "org9121-red", fork=_os.FORK_SELF)

    # The fail-open shape every real caller has: the refusal is invisible.
    assert hosted_api._maybe_apply_completion("org9121-red") is False
    node = _os.read_onboarding_node(proj, "org9121-red")
    assert node is None or node.get("status") != _os.STATUS_COMPLETE, (
        "the org reached complete without its write being declared"
    )


# ── the residual the declaration does NOT cover (#9157) ──────────────────
#
# The review of this PR found the docstrings making a universal that five call
# sites disprove: the `:OnboardingState` CREATION writes are built by
# `eager_init_query()` and executed on `proj.db.select_graph(<graph>)` —
# escape (3) — so they meet neither `state._run` nor the declaration. The two
# tests below keep the note honest: one measures the seam, one reads both the
# note and the guard's own inventory.

_REPO = Path(__file__).resolve().parents[1]

#: (file, enclosing function) -> how many `eager_init_query()` statements that
#: function issues. The COUNT is part of the pin: a sixth lane appearing
#: without the residual being re-read is exactly the drift this records.
_ESCAPE3_CALL_SITES = {
    ("tortoise/sdk.py", "org_create"): 1,
    ("tortoise/hosted_api.py", "provision_tenant"): 1,
    ("tortoise/hosted_api.py", "register_user"): 2,
    ("tortoise/hosted_api.py", "_eager_provision_org_graph"): 1,
}


def _bound_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for element in target.elts:
            out |= _bound_names(element)
        return out
    return set()


def _eager_init_sites():
    """(file, enclosing function, names bound, enclosing node) per call site."""
    sites = []
    for rel in ("tortoise/sdk.py", "tortoise/hosted_api.py"):
        tree = ast.parse((_REPO / rel).read_text())
        parents = {
            child: node
            for node in ast.walk(tree)
            for child in ast.iter_child_nodes(node)
        }
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "attr", None) == "eager_init_query"
            ):
                continue
            bound: set[str] = set()
            for target in node.targets:
                bound |= _bound_names(target)
            enclosing = node
            while enclosing is not None and not isinstance(
                    enclosing, (ast.FunctionDef, ast.AsyncFunctionDef)):
                enclosing = parents.get(enclosing)
            assert enclosing is not None, (
                f"{rel}:{node.lineno} runs eager_init_query outside a function "
                "— this inventory would silently skip it"
            )
            sites.append((rel, enclosing.name, bound, enclosing))
    return sites


def test_the_eager_init_seam_is_undeclared_on_the_select_graph_escape(
        sdk_with_journal, monkeypatch):
    """The residual, MEASURED — the note claims something; this reads reality.

    The statement `eager_init_query()` builds is a MUTATING one, and the five
    call sites issue it on `proj.db.select_graph(<graph>)`. The probe records
    the authorization in flight at the moment it ran: `None` — i.e. the write
    is unjournalled AND undeclared, so "every onboarding write is declared"
    would be false and the declaration's note must not say it (pinned by
    `test_the_declaration_note_names_the_seam_it_does_not_cover`).

    Coupling, deliberately: if a later increment declares this seam (the honest
    fix, #9157), THIS test turns RED and the note + the guard's inventory have
    to be updated in the same change instead of drifting back.
    """
    sdk = sdk_with_journal
    proj = sdk._get_proj()
    assert proj._journal_backed is True, (
        "the fixture did not arm §17.1 — the probe would be vacuous"
    )
    org_graph = proj.db.select_graph(proj._graph_name)
    cypher, params = _os.eager_init_query(
        "CREATE (:TeamMeta {name:$name, created:$now})",
        {"name": "escape3", "now": "2026-10-19"},
        org_id="org9121-escape3", fork=_os.FORK_SELF)
    assert _statement_writes(cypher), (
        "the eager-init statement is not classified as a write, so the probe "
        "below would prove nothing"
    )

    seen: list[tuple[str | None, bool]] = []
    inner = org_graph.query

    def probe(statement, params=None, **kwargs):
        seen.append((_CACHE_WRITE_AUTHORIZED.get(), _statement_writes(statement)))
        return inner(statement, params=params, **kwargs)

    monkeypatch.setattr(org_graph, "query", probe)
    org_graph.query(cypher, params=params)

    assert seen == [(None, True)], (
        "the eager-init CREATE did not run undeclared: "
        f"{seen!r}. If the seam is now declared, remove the residual from "
        "`onboarding_write_scope`'s docstring and from the guard's inventory "
        "(#9157) in the same change."
    )


def test_every_eager_init_site_issues_its_statement_on_the_escape_three_handle():
    """The five sites the declaration's note names, located from the AST.

    Pins the LIST, not just the prose: each function that binds a statement
    from `eager_init_query(...)` must issue it on a name bound from
    `.select_graph(...)` (escape (3)) rather than through `state._run`/`seed
    ._run`. A site that migrates to the declared lane fails here, so the
    residual cannot be closed in code while the docstrings still name it.
    """
    located: dict[tuple[str, str], int] = {}
    for rel, fn_name, bound, enclosing in _eager_init_sites():
        key = (rel, fn_name)
        located[key] = located.get(key, 0) + 1

        select_graph = [
            call for call in ast.walk(enclosing)
            if isinstance(call, ast.Call)
            and getattr(call.func, "attr", None) == "select_graph"
        ]
        issued = [
            call for call in ast.walk(enclosing)
            if isinstance(call, ast.Call)
            and getattr(call.func, "attr", None) == "query"
            and call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id in bound
        ]
        assert select_graph, (
            f"{rel}::{fn_name} issues an eager_init_query statement but builds "
            "no `select_graph(...)` handle — the residual moved, so the "
            "declaration's note is stale"
        )
        assert issued, (
            f"{rel}::{fn_name} binds the eager_init_query statement to {bound} "
            "but never issues it through that name — the inventory drifted"
        )

    assert located == _ESCAPE3_CALL_SITES, (
        "the escape-(3) eager-init inventory changed. Update the list, then "
        "`onboarding_write_scope`'s note and the guard's inventory "
        f"(tortoise/projection/__init__.py) together. located={located!r}"
    )


def test_the_declaration_note_names_the_seam_it_does_not_cover():
    """The note's own wording: no universal it cannot keep, and the uncovered
    seam named where a §17.1 door inventory is read.

    RED on the pre-fix docstring, which stated *"Every onboarding write funnels
    through `_run`, so this scope is the single point that declares the lane"* —
    the five `eager_init_query()` sites above disprove it.
    """
    note = " ".join((inspect.getdoc(_os.onboarding_write_scope) or "").split())

    assert "single point that declares the lane" not in note, (
        "the declaration still claims to be the single point that declares the "
        "onboarding lane — five `eager_init_query()` call sites issue the "
        "`:OnboardingState` CREATE on `proj.db.select_graph(...)` instead"
    )
    assert "Every onboarding write funnels through" not in note, (
        "the declaration still states the disproved universal"
    )
    for expected in ("eager_init_query", "select_graph", "#9157"):
        assert expected in note, (
            f"the declaration's note does not name the uncovered seam "
            f"({expected!r}) — a lane reading only this docstring would "
            "conclude the onboarding class is fully declared"
        )
    # And the narrowing is scoped to the seams that DO declare themselves: the
    # note must still make the load-bearing claim that motivated the change.
    assert "through" in note and "_run" in note, (
        "the note no longer says which seams ARE declared, which is the half "
        "the declaration exists for"
    )
    assert "NOT built-from-zero-safe" in note or "NOT rebuilt-from-zero-safe" in note, (
        "the durability argument (the #4641 rescue, not replay) was dropped "
        "while narrowing the universal — it is load-bearing for shape (a)"
    )


def test_the_guards_inventory_lists_this_lane_among_the_live_scope_openers():
    """`tortoise/projection/__init__.py` is where the invariant lives.

    It enumerates who may open `_live_write_scope`, and it is what a §17.1
    door inventory is read against. It must therefore list the declared
    metadata lane (which appends nothing) — and, in the same place, the seam
    the declaration does NOT cover. Otherwise the next reader concludes the
    onboarding scope is a §17.1 violation, or that "only journal-first doors
    open this" and re-opens the silent refusal #9121 exists to prevent.
    """
    flat = " ".join((_REPO / "tortoise/projection/__init__.py").read_text().split())

    assert "onboarding/state.py::_run" in flat, (
        "the guard's opener inventory does not name the onboarding declaration"
    )
    assert "onboarding/seed.py::_run" in flat, (
        "the guard's opener inventory does not name the seed seam"
    )
    assert "appends NOTHING" in flat or "append nothing" in flat, (
        "the guard's inventory still reads as 'every opener appends first' — "
        "which this lane does not do"
    )
    assert "#9157" in flat, (
        "the guard's inventory does not name the escape-(3) eager-init seam the "
        "declaration does not cover"
    )
