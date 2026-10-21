"""#9157 — the eager-init CREATE is DECLARED, at the handle, on every lane.

The defect this file pins: `onboarding.state.eager_init_query()` only BUILDS a
statement, and every one of its five call sites issued that statement on
`proj.db.select_graph(<graph>)` — §17.1's escape (3), one hop below the guarded
handle. So the `:OnboardingState` CREATION writes were unjournalled **and**
undeclared, and the `onboarding.metadata` lane (#9121) did not cover its own
class: a door inventory done against that lane read the class as complete when
it was not.

What "declared" means here, stated so the tests cannot certify something
narrower: the statement is issued on the projection's §17.1 write handle
(`_GuardedGraph`) — the door that consults the classifier — WHILE the
`onboarding.metadata` declaration is in flight. Either half alone is not the
fix: the guarded handle without the declaration is the violation the guard
exists to catch, and the declaration without the guarded handle is a ContextVar
nothing reads (the escape is below the door that reads it).

⛔ WHY THE RUNTIME TEST DRIVES `sdk.org_create` AND NOT A SYNTHETIC HANDLE. The
earlier draft of the #9121 residual pins asserted with a probe that built its
own handle, opened its own scope and issued the statement itself — a tautology
that passes on the pre-fix tree. This one calls the PRODUCT entry point and
observes which door the statement actually took, so it fails on the pre-fix tree
for the right reason (the statement never reaches the handle) rather than
because a test forgot to declare itself.
"""
from __future__ import annotations

import pytest

import tortoise.projection as projection
from tortoise.onboarding import state as _os
from tortoise.projection import DirectCacheWriteRefused, _GuardedGraph
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk(tmp_path):
    """A plain embedded SDK — no journal, so nothing here depends on the guard's
    armed state unless a test arms it deliberately."""
    sdk = TortoiseSDK(db_path=str(tmp_path / "eager-init.db"))
    try:
        yield sdk
    finally:
        sdk.close()


@pytest.fixture
def armed_sdk(tmp_path):
    """A JOURNAL-BACKED SDK: `event_log_path` is what arms §17.1 at the handle."""
    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "eager-init-armed.db"),
                      event_log_path=str(journal))
    try:
        yield sdk
    finally:
        sdk.close()


def _spy_on_handle_writes(monkeypatch):
    """Record `(statement, lane, projection)` for every guarded-handle write."""
    seen: list[tuple[str, str | None, object]] = []
    real = _GuardedGraph.query

    def query(self, cypher, params=None, timeout=None):
        seen.append((cypher, projection._CACHE_WRITE_AUTHORIZED.get(), self._proj))
        return real(self, cypher, params, timeout)

    monkeypatch.setattr(_GuardedGraph, "query", query)
    return seen


def _is_eager_init(cypher: str) -> bool:
    return "MERGE (n:OnboardingState" in cypher


# ── the defect: the statement never reaches the door that can see it ──────

def test_the_eager_init_create_reaches_the_projection_write_handle(sdk, monkeypatch):
    """RED on the pre-fix tree: issued on `proj.db.select_graph(...)`, escape (3).

    MUTATION: revert `sdk.org_create` to
    `proj.db.select_graph(graph_name).query(_init_q, params=_init_p)` and this
    fails with "never reached the handle" — which is exactly the pre-fix defect,
    not a style preference: a write the door cannot see is a write §17.1 cannot
    authorize, refuse, or count.
    """
    seen = _spy_on_handle_writes(monkeypatch)

    sdk.org_create("declared-lane")

    init = [(cypher, lane) for cypher, lane, _proj in seen if _is_eager_init(cypher)]
    assert init, (
        "the :OnboardingState creation never reached the projection's write "
        "handle — it is issued on `proj.db.select_graph(...)` (§17.1 escape 3), "
        "so the §17.1 door never sees it and the class is unjournalled AND "
        "undeclared (#9157)"
    )
    lanes = {lane for _cypher, lane in init}
    assert lanes == {_os.ONBOARDING_WRITE_LANE}, (
        "the eager-init write reached the handle WITHOUT its declaration in "
        f"flight (lanes seen: {lanes}) — 'declared' means the scope is open AT "
        f"the write, i.e. {_os.ONBOARDING_WRITE_LANE!r}, not that a scope "
        "exists somewhere in the call tree (#9157)"
    )


def test_the_declared_write_lands_on_the_org_graph_it_names(sdk, monkeypatch):
    """The handle is the ONLY change: same statement, same target graph.

    A fix that reaches the guarded handle by writing to the SDK's own graph
    instead of `org_<id>` would satisfy the test above and mint the onboarding
    node in the wrong place — so the target is asserted, not assumed.
    """
    seen = _spy_on_handle_writes(monkeypatch)

    result = sdk.org_create("right-graph")
    graph_name = sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.graph_name", params={"id": result["id"]}
    ).result_set[0][0]

    init = [(cypher, proj) for cypher, lane, proj in seen if _is_eager_init(cypher)]
    assert init, "precondition: the declared route is in use (see the test above)"
    for cypher, proj in init:
        assert "org_id: $org_id" in cypher, (
            "the declared statement is not the eager-init shape this lane "
            f"authorizes:\n{cypher}"
        )
        assert getattr(proj, "_graph_name", None) != graph_name, (
            "the write went to the SDK's own projection graph — the eager-init "
            f"CREATE must land on {graph_name!r}, the graph it names (#9157)"
        )
    assert sdk._get_proj().db.select_graph(graph_name).query(
        "MATCH (n:OnboardingState {org_id:$o}) RETURN count(n)",
        params={"o": result["id"]},
    ).result_set[0][0] == 1, "the onboarding node is not in the org graph it names"


# ── the other half: declared, not bypassed ────────────────────────────────

def test_org_create_still_mints_with_the_write_handle_guard_ARMED(armed_sdk,
                                                                  monkeypatch):
    """`_HANDLE_GUARD_ENFORCES = True` must not break the org mint.

    This is the half a "route it through `proj.g` and hope" fix fails: with the
    guard armed, an UNDECLARED statement at the handle raises
    `DirectCacheWriteRefused`. The declaration is what makes the routed write
    legal, so this test fails both when the route is undeclared AND when the
    route is quietly abandoned.
    """
    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    assert armed_sdk._get_proj()._journal_backed, (
        "the fixture must ARM §17.1 (`event_log_path` configured), or this test "
        "cannot tell a declaration from an unguarded write"
    )

    result = armed_sdk.org_create("armed-lane")

    assert result["id"], result
    graph_name = armed_sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) RETURN t.graph_name", params={"id": result["id"]}
    ).result_set[0][0]
    assert armed_sdk._get_proj().db.select_graph(graph_name).query(
        "MATCH (n:OnboardingState {org_id:$o}) RETURN count(n)",
        params={"o": result["id"]},
    ).result_set[0][0] == 1, (
        "the org minted without its onboarding state — a declared write must "
        "still WRITE (#9157: the lane keeps the product working)"
    )


def test_an_undeclared_write_at_the_same_handle_is_still_refused(armed_sdk,
                                                                 monkeypatch):
    """The control for the test above: the guard is armed, and it still refuses.

    Without this, a fixture that silently failed to arm would make the previous
    test vacuous — the classic "green because nothing was measured".
    """
    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:OnboardingState {org_id:'undeclared-9157'})")


# ── the lane's width: it authorizes THIS class, and says so ───────────────

def test_the_declared_runner_refuses_a_statement_outside_the_onboarding_class():
    """`run_eager_init` is a lane, not a general write door.

    The lane's declaration is class-scoped (its reasons are about onboarding
    FLOW metadata). If the helper would run any statement, the next writer that
    wants an undeclared write has a one-call way to get it — so the class check
    is part of the fix, not decoration.
    """
    class _Handle:
        def __init__(self):
            self.ran: list[str] = []

        def query(self, cypher, params=None, **kwargs):
            self.ran.append(cypher)
            return None

    handle = _Handle()
    with pytest.raises(ValueError) as exc:
        _os.run_eager_init(handle, "CREATE (:Point {id:'not-onboarding'})", {"id": "x"})
    assert "OnboardingState" in str(exc.value), (
        "the refusal does not name the class the lane authorizes, so a caller "
        "cannot tell what it violated"
    )
    assert handle.ran == [], "the refused statement reached the graph anyway"

    cypher, params = _os.eager_init_query(
        "CREATE (:TeamMeta {name:$name})", {"name": "lane"}, org_id="org-9157")
    _os.run_eager_init(handle, cypher, params)
    assert handle.ran == [cypher], "the eager-init statement did not reach the handle"


# ── the prose cannot go stale behind the code ─────────────────────────────

def test_the_module_docstring_names_the_route_and_not_the_old_escape():
    """The MODULE docstring is what a reader meets first — pin it too.

    Measured on the first draft of this fix: the declaration block was
    corrected while the module docstring kept saying the eager-init statement
    "is issued by its CALLERS on `proj.db.select_graph(<g>)`", i.e. it described
    the pre-fix defect as current fact. No test read it, so the suite stayed
    green over a docstring that contradicted the code 500 lines below it.
    """
    import pathlib

    source = pathlib.Path(_os.__file__).read_text(encoding="utf-8")
    docstring = source.split('"""', 2)[1]
    flat = " ".join(docstring.split())

    assert "run_eager_init" in flat and "#9157" in flat, (
        "the module docstring no longer states that the eager-init statement "
        "rides the declared lane (#9157) — a reader would re-derive the old "
        f"residual. Docstring:\n{docstring[:600]}"
    )
    assert "issued by its CALLERS on" not in flat, (
        "the module docstring still says the eager-init statement is issued by "
        "its callers on `proj.db.select_graph(...)` — that is the #9157 defect "
        "described as current fact, and it contradicts the declaration block"
    )
    assert "not closed" in flat, (
        "the module docstring must keep saying escape (3) itself is NOT closed: "
        "this fix removes one class from that hole, it does not close it"
    )
