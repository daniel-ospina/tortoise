"""#9121 §17.1 — the onboarding lane **declares** itself, so closing the write
handle (#9079) does not silently disable onboarding.

`tortoise/onboarding/state.py::_run` reaches for the projection's raw write
handle (`proj.g.query`) *on purpose*, and its docstring says why: the onboarding
writers are the unjournaled class-(a) writes `#7951` deferred, and the public
`FalkorProjection.query` lane refused every one of them. `#9079` closes that
handle, so the reason expires — a writer that goes around a guard because the
guard is open has nothing left to stand on once the guard closes.

⛔ WHAT THIS SUITE PINS, AND WHY IT IS NOT VACUOUS. `#9124`'s guard is not on
`main` yet (it lands last, after its three declaration children — this issue
among them). So `_instrument` reproduces the third door's *predicate* verbatim —
journal-backed + no declaration in flight + a mutating statement ⇒
`DirectCacheWriteRefused` — and installs it on `_GuardedGraph.query`. That is
the same predicate `#9124` pins, so the two cannot disagree about what a
declaration is, and this issue's contract is executable now instead of blocked
on a sibling.

The chosen shape is **(a) DECLARED**, not (b) journalled. The argument is stated
where the declaration lives (the block above `ONBOARDING_WRITE_LANE`); its
decisive leg is that the replay engines able to rebuild this graph already
CAPTURE-AND-RESTORE this class rather than replay it (`#4641`), so declaring does
not move recovery onto a path that was carrying it.

⛔ WHAT THIS SUITE DOES **NOT** CLAIM:
* it does not claim the lane covers only onboarding metadata. It doesn't:
  `write_onboards_edge` MERGEs the anchor `:Subject`, a JOURNALLED ontology
  class, so an unjournalled `:Subject` can be minted on this lane (the
  #2194/#2295 class, already pinned as unrebuildable by
  `tests/test_rebuild_config_preservation.py`). That is disclosed in the
  declaration and pinned here — `test_every_declared_statement_writes_only_
  declared_labels` enumerates it — and the fix is tracked on `#9079`'s family,
  not silently blessed.
* it does not claim every onboarding-adjacent writer declares. The selfhost
  SEED path reaches `proj.create_edge` (`tortoise/projection/edges.py`), which
  writes the handle with no scope and no journal record at all, and its REST
  caller is fail-open (`selfhost_api.py`, `except Exception: _logger.exception`).
  That writer belongs to the SDK's graph-edge lane, was outside `#9079`'s
  measured three classes, and is recorded there rather than widened into here.
  `ensure_selfhost_onboarding` — the onboarding lane this issue owns — IS
  covered (`test_the_selfhost_onboarding_lane_reaches_the_graph_with_the_guard_
  armed`).

⚠️ What is NOT acceptable, and what these tests exist to prevent: fixing this by
making `_run` tolerate a refusal. Every caller is fail-open
(`hosted_api._maybe_apply_completion` → `except Exception: return False`,
`mcp_server._maybe_onboarding_auto_complete` → `return`,
`selfhost.ensure_selfhost_onboarding_once` best-effort), so a swallowed
`DirectCacheWriteRefused` is indistinguishable from success at every call site.
That is the silent org-never-completes regression #7951 already hit once.
"""
from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _GuardedGraph,
    _statement_writes,
)

ORG = "org9121"

#: Verbs that write NOTHING on either surface, so the scan may ignore them.
#: `query`/`ro_query`/`profile`/`explain` are deliberately NOT here (`profile`
#: executes the statement, and both query verbs carry Cypher writes), and the
#: `create_*` / `drop_*` index-constraint families are writes by definition.
_READ_ONLY_HANDLE_VERBS = frozenset(
    {"name", "list_constraints", "list_indices", "slowlog"})


def _scanned_write_verbs() -> frozenset[str]:
    """Every write-capable verb reachable on `proj.g`.

    DERIVED, not hand-maintained: the wrapper's own attributes (including the
    `_query` private hop) UNION the vendor `Graph` surface that `__getattr__`
    forwards, minus the read-only allowlist above. A verb added to either side
    is therefore scanned automatically, and `test_the_verb_scan_is_not_vacuous`
    proves the derivation is not degenerate.
    """
    from falkordb import Graph

    wrapper = {n for n in dir(_GuardedGraph) if not n.startswith("__")}
    vendor = {n for n in dir(Graph) if not n.startswith("_")}
    return frozenset((wrapper | vendor) - _READ_ONLY_HANDLE_VERBS)

#: The classes this lane declares. `:Subject` is in the set BY DISCLOSURE, not
#: by blessing: see the module docstring's "does not claim" section.
_DECLARED_LABELS = frozenset(
    {"OnboardingState", "OnboardingStep", "COMPLETED_STEP", "onboards",
     "Subject"})


@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK with a configured JSONL journal, so §17.1's guard is ARMED.

    The same shape `tests/test_7951_enforce_atomic_write.py` uses: the explicit
    `db_path` makes the projection embedded when no URI is configured, and
    `event_log_path` is what arms the guard.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "onboarding9121.db"),
                      event_log_path=str(journal))
    try:
        yield sdk
    finally:
        sdk.close()


def _instrument(monkeypatch, *, refuse: bool):
    """Install the #9124 predicate on `_GuardedGraph.query`; record every
    statement the onboarding writers issue as `(statement, reason, writes)`.

    `reason` is `_CACHE_WRITE_AUTHORIZED` AT THE WRITE — read inside the handle
    dispatch, which is where #9124's guard will read it — so "the write ran
    inside a declaration" is observed rather than inferred from source.

    ⛔ The statement is recorded IN FULL (normalized whitespace only). An earlier
    version truncated it to 160 chars for the failure message — and the class
    check then read the TRUNCATED text, so 8 of 17 statements lost labels to the
    cut and an appended `MERGE (p:Point …)` passed silently: fail-open in the
    one direction that check exists for. Truncation belongs in the MESSAGE.
    """
    original = _GuardedGraph.query
    seen: list[tuple[str, str | None, bool]] = []

    def instrumented(self, cypher, params=None, timeout=None):
        writes = _statement_writes(cypher)
        reason = _CACHE_WRITE_AUTHORIZED.get()
        seen.append((" ".join((cypher or "").split()), reason, writes))
        if (refuse and writes and reason is None
                and getattr(self._proj, "_journal_backed", False)):
            raise DirectCacheWriteRefused(
                "§17.1 third door (#9079/#9124): a mutating statement reached "
                "`proj.g` with no declaration in flight — statement="
                + seen[-1][0][:160]
            )
        return original(self, cypher, params=params, timeout=timeout)

    monkeypatch.setattr(_GuardedGraph, "query", instrumented)
    return seen


def test_the_instrument_records_statements_in_full(sdk_with_journal,
                                                   monkeypatch):
    """A guard on the guard: the class check and the `:Subject` pin read the
    recorded statement, so a re-introduced truncation must fail the suite here
    rather than quietly shrink what they can see.
    """
    proj = sdk_with_journal._get_proj()
    seen = _instrument(monkeypatch, refuse=False)
    _walk_the_hosted_write_set(proj)

    longest = max(len(c) for c, _r, _w in seen)
    assert longest > 160, (
        "every recorded statement is <= 160 chars, so the class check may be "
        f"reading a PREFIX again (longest={longest}) — truncate only in the "
        "failure message, never in the recorded statement"
    )
    truncated_looking = [c for c, _r, _w in seen if c.endswith(" …")]
    assert not truncated_looking, (
        "a statement was recorded with a truncation marker: "
        f"{truncated_looking[:1]}"
    )


#: The `_run`-calling functions this suite EXERCISES.
#: `_walk_the_hosted_write_set` runs every writer and `_walk_the_read_set` every
#: reader. `test_the_class_check_covers_every_run_call_site` compares both sets
#: against an AST enumeration of the module, so a NEW `_run`-calling function
#: fails THERE instead of being silently uncovered — which is what makes the
#: declaration's "the class check is the counterweight" claim true rather than
#: aspirational.
_EXERCISED_WRITERS = frozenset({
    "ensure_onboarding_state_node", "write_fork", "write_completed_step",
    "write_member_progress", "write_onboards_edge", "write_status",
    "write_compact", "write_fork_unsure_at", "clear_fork_unsure_at",
    "write_last_decide_attempt", "write_operational_state",
    "remove_decide_completed_edge", "regress_status",
})
_EXERCISED_READERS = frozenset({
    "read_onboarding_node", "completed_steps", "decide_completed_edge_exists",
    "decision_evidence", "count_stamped_repairs",
})
#: `_run`-calling functions NOT exercised directly, each with the reason it does
#: not need to be. A function added here must say why in this mapping.
_STATED_OTHERS = {
    "_prune_orphan_decide_step":
        "private; reached from remove_decide_completed_edge, which IS exercised",
}


def _walk_the_hosted_write_set(proj, org_id: str = ORG) -> None:
    """Every MUTATING writer of the lane, in a plausible endpoint order.

    Includes the `onboards`/`:Subject` anchor link, which is the statement that
    makes this lane wider than "onboarding metadata" (see
    `test_every_declared_statement_writes_only_declared_labels`), and the two
    AUDITED EXCEPTIONS (`remove_decide_completed_edge`, `regress_status`) on a
    SECOND org — they move server-owned state backwards, so running them on
    `org_id` would invalidate the completion assertions that read it.
    """
    from tortoise.onboarding import state as _os

    _os.ensure_onboarding_state_node(proj, org_id)
    _os.write_fork(proj, org_id, _os.FORK_SELF)
    _os.write_completed_step(proj, org_id, "team-named")
    _os.write_member_progress(proj, org_id, "user-1", ["team-named"])
    _os.write_onboards_edge(proj, org_id, f"anchor-{org_id}")
    _os.write_status(proj, org_id, _os.STATUS_COMPLETE)
    _os.write_compact(proj, org_id, True)
    _os.write_fork_unsure_at(proj, org_id, "2026-10-19T00:00:00Z")
    _os.clear_fork_unsure_at(proj, org_id)
    _os.write_last_decide_attempt(proj, org_id, "failed")
    _os.write_operational_state(proj, org_id, {"capture_disclosed": True})

    audited = f"{org_id}-audited"
    _os.ensure_onboarding_state_node(proj, audited)
    _os.write_completed_step(proj, audited, "decide-completed")
    _os.remove_decide_completed_edge(proj, audited, reason="9121 test: unearned")
    _os.regress_status(proj, audited, reason="9121 test: regression")


def _walk_the_read_set(proj, org_id: str = ORG) -> None:
    """Every READER of the lane — the other half of the AST enumeration."""
    from tortoise.onboarding import state as _os

    _os.read_onboarding_node(proj, org_id)
    _os.completed_steps(proj, org_id)
    _os.decide_completed_edge_exists(proj, org_id)
    _os.decision_evidence(proj, org_id)
    _os.count_stamped_repairs(proj, org_id)


def _labels_written(statement: str) -> set[str]:
    """Every LABEL-POSITION token in a statement — node labels and relationship
    types, in the `(v:Label {…})`, `-[:REL]->` and backticked-variable spellings.

    Deliberately anchored on the label position (after `(` or `[`) so a property
    VALUE (`{step_id: 'decide-completed'}`) is not mistaken for a label. A
    backticked variable or label name is accepted because an engine accepts it,
    and MISSING one is the fail-open direction for a class check — it would read
    as "writes nothing undeclared".
    """
    ident = r"(?:[A-Za-z_]\w*|`[^`]+`)"
    return {m.strip("`") for m in re.findall(
        rf"[(\[]\s*(?:{ident})?\s*:\s*[`\"']?({ident})", statement)}


def test_the_label_reader_reads_labels_and_not_property_values():
    """A class check is only as good as its reader, and both directions of a
    misread matter: a missed label is fail-OPEN (the lane looks narrower than it
    is), a property value read as a label is a false alarm that gets the check
    weakened.

    The backticked-VARIABLE spelling is pinned because an engine accepts it and
    the reader's whole job is to not go blind on an accepted spelling. (The
    reader does not claim backticked label NAMES are used in this repo today —
    they appear in identity-property KEYS, e.g. ``{`id`: $id}`` — it just must
    not depend on that staying true.)
    """
    cypher = (
        "MERGE (s:Subject {id: $sid}) WITH s "
        "MERGE (n:OnboardingState {org_id: $org_id, step_id: 'decide-completed'}) "
        "MERGE (n)-[:COMPLETED_STEP]->(s2:`OnboardingStep`) "
        "SET n.org_subject_id = $sid"
    )
    assert _labels_written(cypher) == {
        "Subject", "OnboardingState", "COMPLETED_STEP", "OnboardingStep"
    }, _labels_written(cypher)
    # the same label with a BACKTICKED variable
    assert _labels_written("MATCH (`n`:Point {id: $i}) RETURN 1") == {"Point"}
    # and a bare `(Name {…})` is a VARIABLE, not a label — no colon, no label
    assert _labels_written("MATCH (Point {id: $i}) RETURN 1") == set()


# ── the declaration: every write of this lane runs inside it ──────────────

def test_every_onboarding_write_declares_its_lane(sdk_with_journal, monkeypatch):
    """Was RED before this change: `_CACHE_WRITE_AUTHORIZED` was `None` at every
    one of these writes, so #9124's handle guard refuses the product's own
    onboarding. MUTATION: drop the declaration in `state._run` and this fails
    with a list of undeclared statements.
    """
    from tortoise.onboarding import state as _os

    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed is True, (
        "the fixture did not arm §17.1 — an unarmed projection is outside the "
        "rule and every assertion below would be vacuous"
    )

    seen = _instrument(monkeypatch, refuse=False)
    _walk_the_hosted_write_set(proj)

    writes = [(c, r) for c, r, w in seen if w]
    assert len(writes) >= 6, (
        f"the writers issued {len(writes)} mutating statement(s); the walk no "
        "longer covers the hosted write set, so this pin has gone stale"
    )
    undeclared = [c for c, r in writes if r is None]
    assert not undeclared, (
        "these onboarding writes reached the cache handle OUTSIDE any §17.1 "
        "declaration; #9079's handle guard will refuse them and every caller "
        f"is fail-open, so orgs would silently never complete: {undeclared}"
    )
    assert {r for _c, r in writes} == {_os.ONBOARDING_WRITE_LANE}, (
        "the lane must declare ONE name, so the guard block and the refusal "
        f"message can point at it; saw {sorted({r for _c, r in writes})}"
    )


def test_every_declared_statement_writes_only_declared_labels(
        sdk_with_journal, monkeypatch):
    """The class check, not the classifier, is what keeps this lane narrow.

    `_statement_writes` is deliberately conservative (anything not provably
    read-only is a write), so on THIS side of the gate an unreadable statement
    takes the declaration. This test is the counterweight: every label the
    declared statements actually write must be one the declaration named. A new
    writer routed through `_run` that touches, say, `:Point` or `:Event` fails
    here instead of minting a JOURNALLED class under a metadata lane.

    `:Subject` is present because `write_onboards_edge` MERGEs the anchor — the
    module docstring discloses it; this pin makes that disclosure load-bearing
    rather than decorative, and `test_the_subject_anchor_mint_is_not_widened`
    keeps the disclosure from becoming a licence.
    """
    from tortoise.onboarding import state as _os

    declared = _DECLARED_LABELS | {
        _os.ONBOARDING_NODE_LABEL, _os.ONBOARDING_STEP_LABEL,
        _os.COMPLETED_STEP_EDGE, _os.ONBOARDS_EDGE,
    }

    proj = sdk_with_journal._get_proj()
    seen = _instrument(monkeypatch, refuse=False)
    _walk_the_hosted_write_set(proj)

    written: set[str] = set()
    for cypher, _reason, writes in seen:
        if writes:
            written |= _labels_written(cypher)
    assert written, "the walk issued no mutating statement — vacuous"
    assert {"OnboardingState", "OnboardingStep", "Subject"} <= written, (
        "the walk did not even reach the declared classes — the enumeration "
        f"below is not measuring the lane: {sorted(written)}"
    )
    undeclared = sorted(written - declared)
    assert not undeclared, (
        "a statement declared as `onboarding.metadata` writes a class the "
        f"declaration does not name: {undeclared}. Either add it to the "
        "declared set WITH its argument, or stop routing it through this "
        "funnel."
    )


def test_the_subject_anchor_mint_is_not_widened(sdk_with_journal, monkeypatch):
    """`:Subject` is on the declared list BY DISCLOSURE (the anchor link), and
    the disclosure must stay as narrow as the thing it describes.

    Without this, putting `:Subject` in `_DECLARED_LABELS` would authorize any
    future `:Subject` write routed through `_run`, while the declaration's
    argument (an id-only anchor written by the seed's own journalled door)
    quietly stops being true.

    The assertion is the EXACT statement, not a blacklist of `SET` spellings.
    A blacklist was measured to miss `SET s = $props`, `SET s = {name: $n}`
    (whole-node SET) and `REMOVE s.x` — each of which widens the mint while
    passing a `SET s.`-shaped check. Exact equality cannot be widened without a
    visible diff in this test.
    """
    from tortoise.onboarding import state as _os

    proj = sdk_with_journal._get_proj()
    seen = _instrument(monkeypatch, refuse=False)
    _walk_the_hosted_write_set(proj)

    expected = (
        "MERGE (s:Subject {id: $sid}) WITH s "
        f"MERGE (n:{_os.ONBOARDING_NODE_LABEL} {{org_id: $org_id}}) "
        "SET n.org_subject_id = $sid WITH n, s "
        f"MERGE (n)-[:{_os.ONBOARDS_EDGE}]->(s)"
    )
    subject_statements = [c for c, _r, w in seen if w and "Subject" in c]
    assert subject_statements, (
        "no statement touching `:Subject` was issued — this pin would be vacuous"
    )
    for cypher in subject_statements:
        assert cypher == expected, (
            "the `:Subject` anchor statement changed. It is the ONE write on "
            "this lane to a journalled ontology class, authorized only as the "
            "id-only anchor link (see the ANCHOR MINT bullet above "
            "`ONBOARDING_WRITE_LANE`). If this widening is intended, argue it "
            "on #9079 and update both the bullet and this expectation:\n"
            f"  expected: {expected}\n  actual:   {cypher}"
        )


def test_the_class_check_covers_every_run_call_site():
    """The class check only sees what the walk runs, so the walk must reach
    every `_run` call site in the module — or say why not.

    Without this, the declaration's "the class check is the counterweight"
    would be true only of the six-ish writers a test happened to call, and a new
    writer routed through `_run` would mint an undeclared class in the gap.
    Three explicit buckets: exercised writers, exercised readers, and the ones
    that state their reason.
    """
    from tortoise.onboarding import state as _os

    tree = ast.parse(Path(_os.__file__).read_text(encoding="utf-8"))
    call_sites = {
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        and any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "_run"
                for c in ast.walk(fn))
    }
    assert call_sites, "no `_run` call site found — the module shape changed"

    uncovered = call_sites - _EXERCISED_WRITERS - _EXERCISED_READERS \
        - set(_STATED_OTHERS)
    assert not uncovered, (
        "these `_run`-calling functions are not exercised by this suite and "
        f"state no reason: {sorted(uncovered)}. Add them to the walk (or to "
        "`_STATED_OTHERS` with the reason their class cannot escape)."
    )

    stale = set(_EXERCISED_WRITERS) | set(_EXERCISED_READERS) | set(_STATED_OTHERS)
    assert not (stale - call_sites), (
        "this suite names functions that no longer call `_run` — the buckets "
        f"have drifted from the module: {sorted(stale - call_sites)}"
    )

    # ⛔ Bucket MEMBERSHIP is a claim about the walk, so it must be checked
    # against the walk's own BODY: otherwise the cheapest edit that turns the
    # check above green is adding a name to `_EXERCISED_WRITERS` that no walk
    # ever calls — the writer stays unexercised and its statements stay unseen,
    # re-opening the exact gap this test exists to close.
    called = _functions_called_by(_walk_the_hosted_write_set) \
        | _functions_called_by(_walk_the_read_set)
    claimed = set(_EXERCISED_WRITERS) | set(_EXERCISED_READERS)
    not_called = sorted(claimed - called)
    assert not not_called, (
        "these names are claimed as EXERCISED but no walk calls them, so their "
        f"statements are never seen by the class check: {not_called}"
    )


def _functions_called_by(fn) -> set[str]:
    """The `state.<name>` / `_os.<name>` functions a walk function actually
    calls, read from its own source (so the pin cannot drift from the body)."""
    import inspect

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "_os"
    }


def test_a_read_through_the_onboarding_lane_is_not_declared(
        sdk_with_journal, monkeypatch):
    """Was GREEN before the change and MUST STAY: a READ that the classifier can
    prove read-only is not declared, so the declaration covers this module's
    WRITES rather than the whole helper.

    ⚠️ The invariant is not "no read is ever declared". `_statement_writes` is
    conservative, so a read it cannot PROVE read-only IS declared (stated in the
    declaration block and in `_run`'s docstring). Asserting the stronger thing
    would make the first classifier gap read as a declaration defect and send
    the next author to the wrong file.
    """
    proj = sdk_with_journal._get_proj()
    seen = _instrument(monkeypatch, refuse=False)

    _walk_the_read_set(proj)

    reads = [(c, r) for c, r, w in seen if not w]
    assert len(reads) >= 5, (
        f"the readers issued {len(reads)} statement(s) — `_walk_the_read_set` "
        "no longer covers the lane's readers, so this pin has gone stale"
    )
    declared_reads = [(c, r) for c, r in reads if r is not None]
    assert not declared_reads, (
        "a statement the CLASSIFIER called read-only ran inside a write "
        "declaration, i.e. the declaration is now wrapped around `_run` "
        "instead of around the write. If this is a classifier PRECISION change "
        "(a new read shape it cannot prove), teach `_statement_writes` rather "
        f"than accepting the wider lane: {declared_reads}"
    )


# ── the product still works with the third door armed ─────────────────────

def _walk_or_fail(proj) -> None:
    """Run the write set, turning a handle refusal into a failure that NAMES the
    refused statement (the raw `DirectCacheWriteRefused` from inside the walk is
    loud but does not say which writer's declaration is missing).

    This does not make the node assertions below reachable on a refusal —
    `pytest.fail` stops the test there. They are the signal for a write that
    returned WITHOUT landing, which a refusal does not cover.
    """
    try:
        _walk_the_hosted_write_set(proj)
    except DirectCacheWriteRefused as exc:
        pytest.fail(
            "an onboarding write was refused by the armed handle guard: "
            f"{exc}"
        )


def test_the_org_still_reaches_complete_when_the_handle_guard_is_armed(
        sdk_with_journal, monkeypatch):
    """Was RED before this change — this is the P0 the issue names.

    The writers are called DIRECTLY here, so a refusal is loud — `_walk_or_fail`
    names the refused statement. The node assertions are therefore the
    SECONDARY signal, for a write that returned without landing; they are not
    the thing that catches a refusal. (The fail-open CALLER is
    `test_the_fail_open_caller_reports_the_completion_instead_of_swallowing_it`;
    that is where a swallowed refusal is the failure mode.)
    """
    from tortoise.onboarding import state as _os

    proj = sdk_with_journal._get_proj()
    _instrument(monkeypatch, refuse=True)

    _walk_or_fail(proj)

    node = _os.read_onboarding_node(proj, ORG)
    assert node is not None, (
        "no OnboardingState node was written at all — the create-on-write "
        "MERGE was refused, which is the silent org-never-completes P0"
    )
    assert node["status"] == _os.STATUS_COMPLETE, (
        "the org never reached complete under the armed handle guard; "
        f"node={node}"
    )
    assert node.get("fork") == _os.FORK_SELF, (
        "the set-once fork write was lost — it is never re-asked, so losing it "
        "is a permanent regression for the org"
    )
    assert "team-named" in _os.completed_steps(proj, ORG), (
        "the completed-step edge was lost; the gate reads edges, not the node "
        "alone"
    )


def test_the_selfhost_onboarding_lane_reaches_the_graph_with_the_guard_armed(
        sdk_with_journal, monkeypatch):
    """The selfhost lane owns one write point (`ensure_selfhost_onboarding` →
    `state.ensure_onboarding_state_node` → `_run`), and it is best-effort +
    never-raise, so a refusal there is as silent as the hosted one.

    The pre-existing `#7951` pin for this lane
    (`test_the_selfhost_onboarding_lane_writes_on_a_journal_backed_proj`) reaches
    the graph through the SAME raw handle (`proj.g`, the third door), but it never
    arms that door, so it cannot tell a declared write from an undeclared one.
    What it would see once `#9124` arms the handle is a walk that still completes —
    because `_run` declares the statement, which is the property THIS pin actually
    tests: the same call with the `#9124` predicate installed must still reach the
    graph, and must be refused if the declaration is removed.
    """
    from tortoise.onboarding import selfhost
    from tortoise.onboarding import state as _os

    proj = sdk_with_journal._get_proj()
    _instrument(monkeypatch, refuse=True)

    selfhost.ensure_selfhost_onboarding(sdk_with_journal)

    node = _os.read_onboarding_node(proj, selfhost.selfhost_org_id())
    assert node is not None, (
        "the selfhost onboarding node was not written — its one owned write "
        "point was refused by the armed handle guard, and the caller exits "
        "silently"
    )
    assert node.get("fork") == _os.FORK_SELF


def test_the_fail_open_caller_reports_the_completion_instead_of_swallowing_it(
        sdk_with_journal, monkeypatch):
    """Was RED before this change: `_maybe_apply_completion` returned False
    because its `write_status` was refused and `except Exception: return False`
    ate it.

    This is the fail-open path that makes the regression SILENT in production:
    the caller's truthy return is what invalidates the MCP onboarding cache, so
    a swallowed refusal leaves every worker serving the stale pre-complete
    state. The MCP twin (`mcp_server._maybe_onboarding_auto_complete`) is
    exercised separately — `test_the_mcp_caller_caches_completion_only_when_the_
    write_lands` — because it owns the cache WRITE rather than the invalidation.
    """
    from tortoise import hosted_api
    from tortoise.onboarding import state as _os

    proj = sdk_with_journal._get_proj()
    monkeypatch.setattr(hosted_api, "_org_proj", lambda _org: proj)

    # A satisfiable-but-not-yet-complete node: fork=self and the four gate
    # steps (#3913). Written BEFORE the guard is armed, so the only statement
    # under test is the CALLER's own gate write.
    _os.ensure_onboarding_state_node(proj, ORG)
    _os.write_fork(proj, ORG, _os.FORK_SELF)
    for step in ("team-named", "harness-connected", "first-points-filed",
                 "decide-completed"):
        _os.write_completed_step(proj, ORG, step)

    _instrument(monkeypatch, refuse=True)

    assert hosted_api._maybe_apply_completion(ORG) is True, (
        "the completion write was refused and swallowed — the caller reports "
        "'no transition' for an org that just satisfied its gate"
    )
    assert _os.read_onboarding_node(proj, ORG)["status"] == _os.STATUS_COMPLETE


def test_the_mcp_caller_caches_completion_only_when_the_write_lands(
        sdk_with_journal, monkeypatch):
    """The MCP twin of the fail-open caller, and the one that owns the
    tools/list CACHE.

    `_maybe_onboarding_auto_complete` sets `_onboarding_state_cache[org] =
    (now, True)` only when `_maybe_apply_completion` reports a real transition,
    and it swallows every exception. With the handle guard armed, the step
    writes are declared (this PR) so the transition lands — but if that
    declaration were missing, the refusal would be swallowed here, the cache
    would stay EMPTY, and every worker would keep serving the pre-complete
    state with no error anywhere. That is the assertion below.
    """
    from tortoise import hosted_api, mcp_server
    from tortoise.mcp_auth import _current_org_id
    from tortoise.onboarding import state as _os

    proj = sdk_with_journal._get_proj()
    monkeypatch.setattr(hosted_api, "_org_proj", lambda _org: proj)
    monkeypatch.setattr(hosted_api, "_get_onboarding_projection",
                        lambda _org: {"onboarding_complete": False})
    monkeypatch.setattr(hosted_api, "_get_onboarding_state",
                        lambda _org: {"onboarding_complete": False})
    monkeypatch.setattr(hosted_api, "_emit_onboarding_step_events",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(hosted_api, "_onboarding_distinct_id",
                        lambda _org: "distinct-9121")
    mcp_server._onboarding_state_cache.pop(ORG, None)

    _os.ensure_onboarding_state_node(proj, ORG)
    _os.write_fork(proj, ORG, _os.FORK_SELF)

    _instrument(monkeypatch, refuse=True)

    token = _current_org_id.set(ORG)
    try:
        mcp_server._maybe_onboarding_auto_complete(decision_observed=True)
    finally:
        _current_org_id.reset(token)

    try:
        assert mcp_server._onboarding_state_cache.get(ORG) is not None, (
            "the MCP caller's completion was refused and swallowed: the "
            "tools/list cache was never set, so the org's onboarding tools "
            "stay advertised and no counter fires"
        )
        assert mcp_server._onboarding_state_cache[ORG][1] is True
        assert _os.read_onboarding_node(proj, ORG)["status"] == \
            _os.STATUS_COMPLETE
    finally:
        mcp_server._onboarding_state_cache.pop(ORG, None)


# ── the enumeration: no write path around the declaration ─────────────────

def test_the_verb_scan_is_not_vacuous():
    """The scan below derives its verb set from the handle AND from the vendor
    `Graph` the handle forwards to, so it cannot fall behind a verb added to
    either. This test is what keeps that derivation honest: the set must cover
    every write-capable verb both surfaces expose, and the read-only allowlist
    must stay small enough that the derivation is doing work.
    """

    scanned = _scanned_write_verbs()
    required = {
        # the wrapper's own verbs (including the private hop #9124 enumerates)
        "query", "ro_query", "_query", "profile", "explain",
        "execute_command",
        # forwarded through `__getattr__` to the vendor Graph
        "delete", "copy", "call_procedure",
    }
    missing = sorted(required - scanned)
    assert not missing, (
        "the scan does not cover these write-capable verbs, so the test below "
        f"is not measuring the door it claims to: {missing}"
    )
    # The WRAPPER leg must be falsifiable on its own: `scanned` also unions the
    # vendor surface, so asserting only against `scanned` would let a wrapper
    # override disappear unnoticed (the vendor would supply the name).
    wrapper_verbs = {n for n in dir(_GuardedGraph) if not n.startswith("__")}
    assert {"query", "ro_query", "_query", "profile", "explain",
            "execute_command"} <= wrapper_verbs, (
        "`_GuardedGraph` no longer overrides one of the verbs the guard "
        "depends on — it would fall through to the vendor handle and lose the "
        "projection's own refusals"
    )
    assert not ({"query", "delete"} & _READ_ONLY_HANDLE_VERBS), (
        "the read-only allowlist contains a write verb — that would silence "
        "the scan on the exact calls it exists to catch"
    )


def test_the_module_has_no_write_path_around_the_declaration():
    """Every handle-write call this module issues is issued from `_run`, so
    declaring `_run` covers the module.

    A future writer calling `proj.g.query(...)` directly would bypass the
    declaration entirely and no behavioural test would see it. Bounded
    honestly: this matches the direct `.attr(...)` call spelling only — a
    `getattr(proj.g, "query")(...)` or a `q = proj.g.query; q(...)` alias is not
    matched (which is why §17.1's remaining escapes are STATED in the projection
    module rather than claimed closed).
    """
    from tortoise.onboarding import state as _os

    scanned = _scanned_write_verbs()

    source = Path(_os.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    run_def = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_run"), None)
    assert run_def is not None, (
        "`_run` is gone or renamed — the write funnel this whole declaration "
        "hangs on no longer exists, and this pin must not pass silently"
    )
    run_span = (run_def.lineno, run_def.end_lineno or run_def.lineno)

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in scanned):
            continue
        if run_span[0] <= node.lineno <= run_span[1]:
            continue
        offenders.append(
            f"{Path(_os.__file__).name}:{node.lineno} `.{node.func.attr}(...)`"
        )

    assert not offenders, (
        "these handle-write calls sit OUTSIDE `_run`, so they are outside the "
        "§17.1 declaration this issue adds (and #9124's guard would refuse "
        f"them silently, because every caller is fail-open): {offenders}"
    )


def test_the_declaration_and_its_argument_are_recorded():
    """The issue's own checklist row: the chosen shape must be STATED in
    `state._run`'s docstring, replacing the rationale that expired with #9079.

    A structural pin, the way `tests/test_onboarding_selfhost_w12.py` pins its
    module contract: the decision this PR takes is the artifact, and nothing
    else in this suite would notice it being deleted from the docstring.
    """
    from tortoise.onboarding import state as _os

    run_doc = _os._run.__doc__ or ""
    # Load-bearing phrases, not words that survive the paragraph's deletion:
    # `"declared"` alone is satisfied by "Reads are not declared", and
    # `ONBOARDING_WRITE_LANE`/`#9121` each appear in more than one sentence. These
    # three only co-exist if the DECLARATION sentence is present.
    for phrase in ("a MUTATING", "statement run through it is DECLARED",
                   "`_onboarding_write_scope`"):
        assert phrase in run_doc, (
            f"`_run`'s docstring no longer contains {phrase!r} — the shape "
            "decision #9121 requires recorded has been deleted or reworded "
            "out from under this pin"
        )
    assert "#9121" in run_doc, (
        "`_run`'s docstring must cite the issue that made the decision, so the "
        "choice is reviewable where the writer reads it"
    )
    assert _os.ONBOARDING_WRITE_LANE == "onboarding.metadata", (
        "the lane name is what a refusal message and the projection module's "
        "declaration ledger cite; changing it is a deliberate act"
    )

    block = Path(_os.__file__).read_text(encoding="utf-8")
    assert "ONBOARDING_WRITE_LANE = \"onboarding.metadata\"" in block, (
        "the lane constant is gone — the declaration no longer exists"
    )
    assert "the classifier only ever WIDENS this lane" in block, (
        "the block no longer states the lane's real width (the classifier's "
        "conservative bias makes it wider, not narrower) — the honest property "
        "this PR added has been removed in favour of a flattering one"
    )
    assert "ANCHOR MINT IS NOT THIS LANE'S CLAIM" in block, (
        "the `:Subject` anchor-mint disclosure is gone; that disclosure is what "
        "keeps `test_every_declared_statement_writes_only_declared_labels` "
        "from reading as a blessing"
    )
