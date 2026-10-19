"""#9121 — the onboarding lane is a DECLARED §17.1 lane, not an open handle.

`tortoise/onboarding/state.py::_run` used to justify itself by pointing at a
*residual*: the write reached the cache through the raw guarded handle
(`proj.g.query`) because `#7951` had closed only the public
`FalkorProjection.query` lane, and the handle was still open. That rationale
EXPIRES with `#9079` — the child that closes the handle to an *undeclared*
mutating statement. When it lands, every onboarding write in this repo runs
through `state._run`, and the callers are all fail-open
(`hosted_api._maybe_apply_completion` -> `except Exception: return False`,
`mcp_server._maybe_onboarding_auto_complete` -> `return`,
`selfhost.ensure_selfhost_onboarding_once` best-effort), so a refusal there is
SILENT: an org simply never reaches `complete`.

So this suite pins the chosen shape — **(a) the declared lane**: the write runs
inside a named `_live_write_scope` (`onboarding.state.onboarding_write_scope`,
shape (a) vs (b) argued in its docstring) — and pins it against the guard, not
against a stub of it. `_ArmedWriteHandle` below is `#9079`'s rule applied one
layer ahead of the child:

    a mutating statement through the write handle is REFUSED unless a
    sanctioned scope is in flight (`_CACHE_WRITE_AUTHORIZED.get() is None`)

— the same predicate `FalkorProjection.apply` and `FalkorProjection.query`
already enforce, and the same conservative `_statement_writes` classifier. A
lanes that still reached around the guard would fail here today, before the
child lands, instead of silently disabling onboarding after it.

⛔ What this suite does NOT do: it does not make `_run` tolerate a refusal. A
swallowed `DirectCacheWriteRefused` is indistinguishable from success at every
fail-open call site, and `test_without_the_declaration_the_lane_is_refused...`
is the witness for exactly that — the declaration is load-bearing, not
cosmetic.
"""
from __future__ import annotations

import contextlib

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
