"""#9124 — §17.1's THIRD door: the projection's write handle detects and refuses.

`#7951` made §17.1 load-bearing at two doors — the fold
(`FalkorProjection.apply`) and the public raw-query lane
(`FalkorProjection.query`) — and stated the third as an explicit residual: the
handle `proj.g` (`_GuardedGraph`), which the module docstring calls *"the single
handle every projection write passes through"*. The capstone measured the hole
(`docs/evidence/7950-capstone/README.md`, S1 **NOT-GREEN**):

    ARMED RAW HANDLE (proj.g.query): SUCCEEDED (direct cache write)
    Point:p1 gained an unauthorized property (Point.second_writer_marker)

This suite is the acceptance gate for closing it. It is written against the
BEHAVIOUR (a second writer is refused, a sanctioned lane still writes), not
against the shape of the fix, so it survives a refactor that keeps §17.1.

⛔ MEASURE FIRST, REFUSE WHEN DECLARED (#9124, owner decision option B). The
refusal is armed by `_HANDLE_GUARD_ENFORCES`, and the LANDED state has it
`False`: the handle records an undeclared write (a counter plus one WARNING)
without raising, because the SDK's write surface is not yet fully declared and
arming it now refuses the product's own doors — measured as 16 failed / 4 errors
across `test_7951_update_point_outbox.py`, `test_7951_delete_point_outbox.py`,
`test_5048_retract_instant_recorded.py` and
`test_6239_promote_after_supersede.py`. So every test below that asserts a
REFUSAL takes the `enforced` fixture, which flips the switch for that test — the
guard's correctness is still proven, and the default stays non-breaking. The
two `measure` tests pin the other half, so neither mode can rot silently.

WHY EVERY MUTATING VERB, AND WHY AN ENUMERATION TEST. A guard on `query` alone
is one private hop (`proj.g._query`) or one verb (`proj.g.profile`, which
EXECUTES via `GRAPH.PROFILE`) away from being no guard at all — the #3595
shared-decision precedent. The parametrized case reads the SHARED verb tuple
(`_HANDLE_MUTATING_VERBS`, also read by `tests/test_9124_declared_live_doors.py`
and by the handle's own enumeration test), so "what is a mutating verb" cannot
drift between the three readers.

WHAT IS *NOT* CLAIMED. This closes escape (1) of the three named in
`tortoise/projection/__init__.py`'s SCOPE block. Escapes (2)
`proj.g._g.query(...)` and (3) `proj.db.select_graph(<g>).query(...)` stay
OPEN — each is one hop below anything a runtime signal is placed on, and a
lexical inventory cannot reach them. They are §17.1's atomic-write work and
this suite does not assert they are closed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tortoise.projection import (
    _HANDLE_MUTATING_VERBS,
    DirectCacheWriteRefused,
    _GuardedGraph,
    _live_write_scope,
    _replay_write_scope,
    _reset_undeclared_handle_writes,
    undeclared_handle_writes,
)

#: The verbs on `_GuardedGraph` that can put a MUTATING statement on the wire —
#: the SHARED tuple (`_HANDLE_MUTATING_VERBS`), not a copy: the declaration
#: census in `tests/test_9124_declared_live_doors.py` and the handle's own
#: enumeration test read the same list, so a new verb cannot be covered in one
#: reader and forgotten in another.
MUTATING_CAPABLE_VERBS = _HANDLE_MUTATING_VERBS

#: The five CYPHER verbs, i.e. the shared tuple minus `execute_command` — which
#: is guarded too, but takes its Cypher at ``args[2]`` inside a Redis command
#: and therefore gets its own test below rather than the uniform per-verb call.
MUTATING_QUERY_VERBS = tuple(
    verb for verb in _HANDLE_MUTATING_VERBS if verb != "execute_command")

#: Every OTHER callable defined on `_GuardedGraph`, with the reason it can put no
#: mutating statement on the wire. The enumeration test compares the class's own
#: members (dunders excluded) against this set, the shared verb tuple and the
#: non-statement-guarded verbs below, so a new method is a failure rather than a
#: silent gap.
_NON_MUTATING_HANDLE_METHODS = {
    "_refuse_unauthorized_cache_write",  # the check itself
    "_bar",  # #7162: rewrites the statement text, dispatches nothing
    # #9209: the two non-Cypher refusal helpers. They RAISE and dispatch nothing,
    # so they are non-mutating in the sense this bucket means; the verbs that
    # CONSULT them are in the bucket below, so a helper cannot be smuggled in
    # here on behalf of an unguarded verb.
    "_refuse_unauthorized_graph_wipe",
    "_refuse_key_space_handle_command",
}

def _wire_reaching_calls(func) -> set[str]:
    """Calls in `func`'s BODY (never its prose) that reach the client.

    AST-based on purpose: the refusal helpers' DOCSTRINGS quote
    ``proj.g.execute_command("DEL", …)`` as the evidence for why they exist, so
    a substring test over `inspect.getsource` reads prose as a dispatch and reds
    on a method that is correctly filed. Walking the parsed tree sees only real
    calls.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    roots = {"self._g", "self._proj.g", "self.g"}
    # ⛔ ALIASES COUNT (#9223 review, measured): `inner = self._g; return
    # inner.execute_command(...)` reached the client while this helper returned
    # an empty set, so a one-line refactor defeated the claim it enforces. Any
    # local assigned FROM a root is a root.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name | ast.Attribute)
            and ast.unparse(node.value) in roots
        ):
            roots |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not isinstance(target, ast.Attribute):
            continue
        base = ast.unparse(target.value)
        if base in roots:
            found.add(f"{base}.{target.attr}")
    return found


#: #9209 — the third bucket: MUTATING verbs on the handle whose refusal is NOT
#: the §17.1 statement check, mapped to the guard(s) that must appear in each
#: verb's own body.
#:
#: ⛔ WHY A THIRD BUCKET RATHER THAN ONE OF THE OTHER TWO (#9223 review). The
#: shared tuple `_HANDLE_MUTATING_VERBS` is not a list of "verbs that write"; it
#: is the list of verbs that carry a CYPHER STATEMENT, and two other gates
#: depend on that meaning — `MUTATING_QUERY_VERBS` (below) calls each verb with
#: a statement positionally, and `tests/test_9124_declared_live_doors.py`
#: censuses declaration call sites at that same argument index. `delete()`
#: takes a graph NAME, not a statement, so listing it there would break both
#: readers and demand a check its body structurally cannot perform. Filing the
#: two verbs under `_NON_MUTATING_HANDLE_METHODS` would be worse: it would state
#: in a SECURITY inventory that a mutating verb cannot write. So they get their
#: own bucket, which is STRICTER than the non-mutating one — membership requires
#: naming the guard, and the guard is asserted to be in the verb's source.
_NON_STATEMENT_GUARDED_HANDLE_VERBS = {
    # `GRAPH.DELETE` carries no Cypher at all: the guard is #9209's non-Cypher
    # decision, shared with the command channel so the two spellings cannot
    # drift.
    "delete": ("_refuse_unauthorized_graph_wipe",),
    # `CALL <procedure>(…)` IS Cypher text, so this verb is protected by the L2
    # bulk-wipe classifier applied to the statement the vendor will build — AND
    # it consults §17.1's statement door on that same reconstructed statement
    # (measure mode until `_HANDLE_GUARD_ENFORCES`), which is asserted below.
    # It still cannot sit in `_HANDLE_MUTATING_VERBS`: that tuple's other two
    # readers take ``args[0]`` as a CYPHER STATEMENT (this verb's ``args[0]`` is
    # a procedure NAME) and index it positionally.
    "call_procedure": (
        "_is_bulk_wipe",
        "_assert_test_graph",
        "_refuse_unauthorized_cache_write",
    ),
}


@pytest.fixture
def enforced(monkeypatch):
    """Arm the third door for one test (part 2's state, exercised deliberately).

    `_HANDLE_GUARD_ENFORCES` is a module constant and a code change to flip, not
    an env var or constructor argument — so a test flips it on the imported
    module, which is exactly the reference the handle reads at call time.
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", True)
    return projection


@pytest.fixture
def measure_only(monkeypatch):
    """Pin the LANDED mode: detect and count, never raise.

    Explicit rather than inherited from the module default so a test that needs
    to observe the census cannot be silently changed by part 2's flip.
    """
    import tortoise.projection as projection

    monkeypatch.setattr(projection, "_HANDLE_GUARD_ENFORCES", False)
    _reset_undeclared_handle_writes()
    return projection


@pytest.fixture
def armed_sdk(tmp_path):
    """A real SDK with a configured JSONL journal, so the guard is ARMED.

    Lane-agnostic, exactly like `tests/test_7951_enforce_atomic_write.py`'s
    fixture: an explicit `db_path` makes the projection embedded when no URI is
    configured, and the SDK's own construction is redirected to the docker
    server in a URI-set session. `event_log_path` is what arms §17.1 at the
    handle (`_get_proj` -> `_bind_journal`).
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "handle-authority.db"),
                      event_log_path=str(journal))
    sdk._journal_path_for_test = journal
    try:
        yield sdk
    finally:
        sdk.close()


def _count(proj, label: str, prop: str, value: str) -> int:
    return int(proj.g.query(
        f"MATCH (n:{label} {{{prop}:$v}}) RETURN count(n)",
        params={"v": value},
    ).result_set[0][0])


def _journal_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


# ── the load-bearing pin: a direct write through the handle FAILS ─────────

def test_a_mutating_statement_through_the_write_handle_is_refused(armed_sdk, enforced):
    """RED before this lane: `proj.g.query("CREATE …")` wrote the cache direct.

    This is the exact statement shape the capstone probe issued at
    `proj.g.query` and recorded as `SUCCEEDED (direct cache write)`.

    MUTATION: delete the `_refuse_unauthorized_cache_write` call from
    `_GuardedGraph.query` and this test fails with `DID NOT RAISE` — the write
    silently lands again.
    """
    proj = armed_sdk._get_proj()
    assert proj._journal_backed, (
        "the fixture must ARM the guard (`event_log_path` configured); an "
        "unarmed projection is outside §17.1 and this test would be vacuous"
    )

    with pytest.raises(DirectCacheWriteRefused) as exc:
        proj.g.query("CREATE (n:Point {id:'handle-9124'})")

    assert "17.1" in str(exc.value), (
        "the refusal must cite the rule it enforces so the next writer can "
        f"read why; message={str(exc.value)!r}"
    )


def test_a_refused_handle_write_lands_nothing(armed_sdk, enforced):
    """The refusal is PRE-WRITE.

    A guard that raised AFTER dispatch would leave exactly the corruption it
    claims to prevent, with a stack trace on top — the second writer would have
    written and the caller would only learn about it.
    """
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:Point {id:'handle-nothing'})")
    assert _count(proj, "Point", "id", "handle-nothing") == 0


def test_a_property_rewrite_through_the_write_handle_is_refused(armed_sdk, enforced):
    """The capstone's `SET` shape, not just `CREATE`.

    The probe's finding was a property GAINED on an existing node
    (`Point.second_writer_marker`) — a `MATCH … SET`, i.e. a classifier that
    only recognised node creation would have left the observed hole open.
    """
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query(
            "MATCH (n:Point {id:'handle-rewrite'}) "
            "SET n.second_writer_marker = 'x'"
        )


@pytest.mark.parametrize("verb", MUTATING_QUERY_VERBS)
def test_every_mutating_capable_verb_on_the_handle_is_guarded(armed_sdk, enforced, verb):
    """Parametrized because a guard on `query` alone is one hop from no guard.

    `ro_query` is mutating-CAPABLE despite its name (the vendor's
    "read-only" verb dispatches whatever statement it is handed), `_query` is
    the private hop both public verbs delegate to, and `profile`/`explain` put
    the statement on the wire WITHOUT routing through `_query` (a raw
    `GRAPH.PROFILE` never calls a query verb).
    """
    proj = armed_sdk._get_proj()
    statement = "CREATE (n:Point {id:'handle-verb-" + verb.strip('_') + "'})"
    kwargs = {"read_only": False} if verb == "_query" else {}
    with pytest.raises(DirectCacheWriteRefused):
        getattr(proj.g, verb)(statement, **kwargs)


def test_the_handle_exposes_no_unguarded_mutating_query_verb(armed_sdk):
    """Enumeration: every query verb on `_GuardedGraph` consults ONE check.

    Structural, not behavioural: a SEVENTH verb added to the handle later fails
    here instead of silently re-opening §17.1 (the `#3595` shared-decision
    precedent). The CLASS is enumerated, not the shared tuple — iterating the
    tuple and comparing it back to itself cannot fail, so that form proved
    nothing; every public method defined on `_GuardedGraph` must now be either a
    shared mutating verb or one of the explicitly NON-mutating names below, and
    an unlisted verb fails by construction. The source is read rather than the
    bound methods, because the property asserted is "the check is called inside
    the verb", which is not observable from outside without issuing a statement
    per verb — and that is what the parametrized test above does for the verbs
    we know about.
    """
    import inspect

    source = inspect.getsource(_GuardedGraph)
    guarded = []
    for verb in MUTATING_QUERY_VERBS:
        body = inspect.getsource(getattr(_GuardedGraph, verb))
        assert "_refuse_unauthorized_cache_write" in body, (
            f"`_GuardedGraph.{verb}` does not consult §17.1's authorization; a "
            "mutating statement through it would write the derived cache "
            "directly, which is the door #9124 closes"
        )
        guarded.append(verb)

    assert set(guarded) == set(MUTATING_QUERY_VERBS)

    # THE ENUMERATION. `vars()` on the class, so a verb added tomorrow is neither
    # "in the shared tuple" nor "in the allow-list" and reddens here.
    public_members = {
        name for name, value in vars(_GuardedGraph).items()
        if not name.startswith("__") and callable(value)
    }
    accounted = (set(_HANDLE_MUTATING_VERBS) | set(_NON_MUTATING_HANDLE_METHODS)
                 | set(_NON_STATEMENT_GUARDED_HANDLE_VERBS))
    assert public_members == accounted, (
        "`_GuardedGraph` gained or lost a method without this gate noticing. "
        "A new verb that can put a mutating STATEMENT on the wire belongs in "
        "`_HANDLE_MUTATING_VERBS` (shared with the declaration census); a "
        "MUTATING verb whose refusal is not the statement check belongs in "
        "`_NON_STATEMENT_GUARDED_HANDLE_VERBS` with the guard named; a "
        "method that cannot write belongs in `_NON_MUTATING_HANDLE_METHODS` "
        f"with the reason. Unaccounted: {sorted(public_members - accounted)}; "
        f"listed but absent: {sorted(accounted - public_members)}"
    )

    # THE THIRD BUCKET IS NOT A WAY OUT OF THE GATE (#9223 review). Each entry
    # must name a guard that really appears in the verb's own body, and the
    # buckets must stay disjoint — otherwise `delete`/`call_procedure` could be
    # moved here to dodge the statement assertion while carrying no guard at
    # all, which is the silent bypass the review declined to author.
    assert set(_NON_STATEMENT_GUARDED_HANDLE_VERBS).isdisjoint(
        _NON_MUTATING_HANDLE_METHODS), (
        "a verb cannot be both \"cannot write\" and \"writes, guarded by a "
        "non-statement check\""
    )
    assert set(_NON_STATEMENT_GUARDED_HANDLE_VERBS).isdisjoint(
        MUTATING_QUERY_VERBS), (
        "a verb in the shared statement tuple must carry "
        "`_refuse_unauthorized_cache_write`; listing it here too would let it "
        "skip that assertion"
    )
    for verb, guards in _NON_STATEMENT_GUARDED_HANDLE_VERBS.items():
        body = inspect.getsource(getattr(_GuardedGraph, verb))
        for guard in guards:
            assert guard in body, (
                f"`_GuardedGraph.{verb}` is declared as guarded by `{guard}`, "
                "but its body does not consult it — an unguarded mutating "
                "verb declared guarded is worse than an unlisted one"
            )
        # ⛔ THE GUARD MUST BE CONSULTED IN CODE, not merely named (#9223
        # review, measured): the substring assertion above is satisfied by a
        # DOCSTRING or a COMMENT that names the guard, so a verb whose body was
        # `"""... `_refuse_unauthorized_graph_wipe` ..."""` followed by
        # `return self._g.execute_command("FLUSHALL")` passed it. Docstrings and
        # trailing comments are stripped here before the search.
        #
        # WHAT THIS STILL DOES NOT PROVE, stated rather than implied: that the
        # guard DOMINATES every wire-reaching call on every path. A first attempt
        # asserted that too and was withdrawn — `call_procedure` has an early
        # `return self._g.call_procedure(...)` inside an unclassifiable-procedure
        # branch that RAISES first (so it is safe), while `delete` dispatches
        # after its guard; a line-order rule cannot tell those apart, and one
        # that claimed to would be the same "guard map describes a check the code
        # does not perform" defect this PR exists to remove. A verb that CALLED
        # its guard with an argument that cannot refuse would also pass. What
        # actually pins these verbs is BEHAVIOUR: no-op'ing the guards fails 53
        # of the 70 cases in `tests/test_9209_handle_non_cypher_wipe.py`
        # (measured in review).
        code_lines = [
            line.split("#")[0] for line in body.splitlines()
        ]
        # blank the docstring's own lines: parse, then clear the constant's range
        import ast
        import textwrap

        tree = ast.parse(textwrap.dedent(body))
        node = tree.body[0]
        if (node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            for i in range(node.body[0].lineno - 1, node.body[0].end_lineno):
                code_lines[i] = ""
        code = "\n".join(code_lines)
        for guard in guards:
            assert guard in code, (
                f"`_GuardedGraph.{verb}` names `{guard}` only in PROSE — its "
                "docstring or a comment — and never consults it in code, so the "
                "declaration reads as guarded while nothing refuses this verb"
            )

    # ⛔ THE BUCKET'S CLAIM IS CHECKED, not just its membership (#9223 review,
    # measured). `_NON_MUTATING_HANDLE_METHODS` says "this method puts no
    # mutating statement on the wire", and membership used to be the whole
    # assertion: adding a `flushall` method that dispatches `FLUSHALL` and filing
    # it under the bucket with a plausible reason left this gate GREEN. A member
    # must therefore make no call that reaches the client, which also catches a
    # future verb that merely delegates.
    for name in _NON_MUTATING_HANDLE_METHODS:
        reaching = _wire_reaching_calls(getattr(_GuardedGraph, name))
        assert not reaching, (
            f"`_GuardedGraph.{name}` is filed in "
            "`_NON_MUTATING_HANDLE_METHODS`, but its body calls "
            f"{sorted(reaching)}: a method that can reach the wire is not "
            "non-mutating. If it mutates without carrying a statement, it "
            "belongs in `_NON_STATEMENT_GUARDED_HANDLE_VERBS` with the guard "
            "that refuses it named"
        )
    # `execute_command` carries a statement only in its args, so it is checked
    # separately — and this is the assertion that keeps it from being forgotten.
    assert "_refuse_unauthorized_cache_write" in inspect.getsource(
        _GuardedGraph.execute_command), (
        "`_GuardedGraph.execute_command` must recover the statement from its "
        "args and consult §17.1: a raw `GRAPH.QUERY` never reaches `query`, so "
        "an unguarded command channel is this door re-opened under another name"
    )
    assert source.count("def _refuse_unauthorized_cache_write") == 1, (
        "the handle must have exactly ONE authorization check — a second copy "
        "is a second policy that can drift from the shared classifier"
    )


def test_a_mutating_statement_through_the_command_channel_is_refused(armed_sdk, enforced):
    """`execute_command` is a mutating verb too.

    A `GRAPH.QUERY` payload is dispatched by the vendor's client directly; it
    never calls `query`/`_query`, so a guard that only covered the query verbs
    would leave the raw channel as the one unguarded write path on this handle.
    """
    proj = armed_sdk._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.execute_command(
            "GRAPH.QUERY", proj._graph_name,
            "CREATE (n:Point {id:'handle-cmd'})",
        )
    assert _count(proj, "Point", "id", "handle-cmd") == 0


# ── reads are not writes ─────────────────────────────────────────────────

def test_reads_through_the_write_handle_still_pass(armed_sdk, measure_only):
    """GREEN before this lane, and it must stay so.

    The reconciler and every diagnostic read through this handle. A classifier
    that refused reads would break the whole product, so this is the pin that
    keeps the guard narrow — and the CENSUS is what makes it a pin at all: in
    the landed measure mode nothing is refused, so "did not raise" alone passes
    on a classifier that calls every statement a write (#9124 review, round 5).
    `measure_only` resets the census, so a non-empty one here means
    `_statement_writes` misread a read — the condition part 2 would turn into a
    refusal of every diagnostic read in the product.
    """
    proj = armed_sdk._get_proj()
    assert proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0
    assert proj.g.ro_query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0
    assert undeclared_handle_writes() == {}, (
        "a read through the write handle was counted as an UNDECLARED WRITE — "
        "`_statement_writes` over-classified it, and under `enforced` that is "
        f"a refusal of every diagnostic read: {undeclared_handle_writes()!r}")


def test_a_read_whose_text_contains_a_write_keyword_still_passes(
        armed_sdk, enforced):
    """The classifier strips literals, so a keyword inside DATA is not a write.

    `_statement_writes` is conservative by design, but "conservative" must not
    mean "any statement mentioning SET is a write": a diagnostic that filters on
    a property VALUE would be refused, and the natural workaround is to bypass
    the handle — which is how a guard creates the hole it was closing.

    ARMED (`enforced`), which is the mode where the mistake is visible at all:
    in the landed measure mode this statement executes whether or not the
    classifier misreads it, so the test passed for a classifier that called the
    literal a write (#9124 review, round 5).
    """
    proj = armed_sdk._get_proj()
    proj.g.query("MATCH (n:Point) WHERE n.note = 'SET n.x = 1' RETURN count(n)")
    assert undeclared_handle_writes() == {}, (
        "the keyword inside the STRING LITERAL was read as a write clause — a "
        f"diagnostic filtering on data would be refused: {undeclared_handle_writes()!r}")


# ── the sanctioned lanes still write ─────────────────────────────────────

def test_the_sdk_write_lane_still_writes_on_a_journal_backed_projection(
        armed_sdk, force_sparse_tfidf):
    """GREEN before this lane — and this is the DEPENDENCY, executable.

    `force_sparse_tfidf` pins the embedder OFF: this test is about the WRITE
    LANE's authority, not about retrieval, and the ambient HF cache is
    present-or-absent per lane (a cold, cache-less lane spent 141 s in
    blocked-egress retries here before this pin). The assertions are unchanged
    either way — the write must land and must be journalled.

    The handle is *also* the SDK's live write lane (312 direct `proj.g.query(`
    sites in `tortoise/sdk.py` alone). Arming the guard without the declaration
    children does not "fail safe": it refuses the PRODUCT'S OWN writes, in
    exactly three statement classes — `:EpMeta`'s epoch (#9123), the two direct
    `:Event` writers (#9122), and the onboarding lane (#9121). This test is
    what makes that ordering constraint CATCHABLE rather than remembered: it
    goes RED the moment the guard lands ahead of any one of them.
    """
    sdk = armed_sdk
    # `create_point` returns the created node's DICT, not a bare id — the id is
    # `["id"]`, the same idiom `tests/test_7951_enforce_atomic_write.py` uses
    # (`.get("id")`). Passing the dict straight to `_count` would bind it as a
    # map value, match nothing, and read 0 whether or not the write landed.
    point_id = sdk.create_point("concept", "handle authority lane")["id"]
    assert point_id, "the SDK's own public write API must still write"
    proj = sdk._get_proj()
    assert _count(proj, "Point", "id", point_id) == 1
    assert any(rec.get("type") for rec in _journal_lines(
        sdk._journal_path_for_test)), (
        "the write must still be journalled — the guard does not replace the "
        "journal, it only refuses a write that bypassed it"
    )


def test_the_replay_scope_authorizes_the_write_handle(armed_sdk):
    """A replay engine legitimately writes this cache: it IS the journal.

    `_replay_write_scope` is the declaration those engines use, so the third
    door must honour it — otherwise `rebuild` (which replays through the same
    handle) would be refused by the guard that exists to protect its output.
    """
    proj = armed_sdk._get_proj()
    with _replay_write_scope():
        proj.g.query("CREATE (n:Point {id:'handle-replay'})")
    assert _count(proj, "Point", "id", "handle-replay") == 1


def test_the_live_scope_authorizes_the_write_handle(armed_sdk):
    """The live doors' declaration reaches the third door too.

    #7951 R3 recorded that `create_point`'s scope is a RESERVED marker whose
    `CREATE` still runs on this handle; that is precisely why the scope has to
    be honoured HERE, or the marker would be a promise the handle does not keep
    and the transition to the fold would be blocked by the guard.
    """
    proj = armed_sdk._get_proj()
    with _live_write_scope("test-door"):
        proj.g.query("CREATE (n:Point {id:'handle-live'})")
    assert _count(proj, "Point", "id", "handle-live") == 1


def test_the_authorization_is_context_local_and_expires(armed_sdk, enforced):
    """A scope is a per-call declaration, not a mode.

    It must not leak out of the `with` (a raising body included): an escaping
    authorization would turn every later write in the process into a sanctioned
    one, which is strictly worse than no guard because it reads as one.
    """
    proj = armed_sdk._get_proj()
    with pytest.raises(RuntimeError), _live_write_scope("test-leak"):
        proj.g.query("CREATE (n:Point {id:'handle-in-scope'})")
        raise RuntimeError("boom")
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:Point {id:'handle-after-scope'})")


def test_an_unarmed_projection_is_outside_section_17_1(armed_sdk):
    """No journal, nothing to be the single writer of.

    The guard's arming rule is identical to the fold's: a projection with no
    journal is not a DERIVED cache, so refusing its writes would break every
    embedded, journal-less caller (`FalkorProjection(db_path)` on its own) for
    no gain.
    """
    proj = armed_sdk._get_proj()
    proj._bind_journal(None)
    proj.g.query("CREATE (n:Point {id:'handle-unarmed'})")
    assert _count(proj, "Point", "id", "handle-unarmed") == 1


# ── MEASURE MODE — the state part 1 ships ────────────────────────────────

def test_the_landed_mode_measures_and_does_not_refuse(armed_sdk, measure_only):
    """The LANDED state: an undeclared write is COUNTED, not raised (#9124).

    Arming the handle ahead of the declaration programme refuses the product's
    own write doors (measured: 16 failed / 4 errors across the four §17.1
    suites named in this module's docstring), so part 1 ships the detection and
    defers the refusal. This pin is what makes that sequencing explicit rather
    than an oversight — but it does NOT enforce the sequencing itself: the
    `measure_only` fixture monkeypatches the constant to `False` before
    asserting it is `False`, so this test cannot fail on a flip (the docstring
    used to claim it did). `tests/test_9124_declared_live_doors.py`
    (`test_arming_the_guard_requires_a_fully_declared_surface`) is the gate that
    carries that duty and fails with the list of what is still undeclared.
    """
    proj = armed_sdk._get_proj()
    assert proj._journal_backed
    assert measure_only._HANDLE_GUARD_ENFORCES is False
    _reset_undeclared_handle_writes()

    proj.g.query("CREATE (n:Point {id:'handle-measured'})")

    assert _count(proj, "Point", "id", "handle-measured") == 1, (
        "measure mode must let the write through — it is a DETECTOR, not a gate")
    census = undeclared_handle_writes()
    assert any("handle-measured" in key for key in census), (
        f"the measure-mode census missed the write it exists to see: {census!r}")


def test_a_declared_lane_is_not_measured(armed_sdk, measure_only):
    """The census counts UNDECLARED writes only — a lane is not a violation.

    Without this the census would count every sanctioned write and the number
    would be unreadable; with it, an empty census means "the declared surface
    held", which is the signal part 2 arms on.
    """
    proj = armed_sdk._get_proj()
    _reset_undeclared_handle_writes()
    with _live_write_scope("declared-lane-9124"):
        proj.g.query("CREATE (n:Point {id:'handle-declared'})")
    with _replay_write_scope():
        proj.g.query("CREATE (n:Point {id:'handle-replay-9124'})")
    assert _count(proj, "Point", "id", "handle-declared") == 1
    assert _count(proj, "Point", "id", "handle-replay-9124") == 1
    assert undeclared_handle_writes() == {}, (
        "a declared lane was counted as an undeclared write: "
        f"{undeclared_handle_writes()!r}")


def test_a_declared_door_writes_through_the_handle_with_no_declaration_of_its_own(
        armed_sdk, enforced, force_sparse_tfidf):
    """The DOORS part 1 declared still write once the handle is armed.

    This is the dependency the four declarations exist for, executable: with the
    handle REFUSING, `delete_point` / `retract_point` / `invalidate_point` /
    `supersede_point` must complete — each appends its record and then writes a
    cache the guard would otherwise refuse. A door declared too early (i.e.
    apply-first) would fail here rather than silently pass in measure mode.

    `force_sparse_tfidf` pins the embedder OFF for the same reason the
    sanctioned-lane test above does: seven `create_point`s against a cold HF
    cache spend minutes in blocked-egress retries, and this test is about the
    write lane's authority, never about retrieval.
    """
    sdk = armed_sdk

    # retract_point
    retracted = sdk.create_point("statement", "declared-door retracted")["id"]
    assert sdk.retract_point(retracted)["id"] == retracted

    # invalidate_point — a LIVE corrector, so the CORRECTS arm really writes
    superseded = sdk.create_point("statement", "declared-door superseded")["id"]
    corrector = sdk.create_point("statement", "declared-door corrector")["id"]
    assert sdk.invalidate_point(superseded, corrector)["invalidated"] is True

    # supersede_point
    old = sdk.create_point("statement", "declared-door old")["id"]
    new = sdk.create_point("statement", "declared-door new")["id"]
    assert sdk.supersede_point(old, new)["invalidated"] is True

    # delete_point
    doomed = sdk.create_point("statement", "declared-door deleted")["id"]
    assert sdk.delete_point(doomed) is True
    proj = sdk._get_proj()
    assert _count(proj, "Point", "id", doomed) == 0


# ── the schema-DDL decision (#9124's own, per the issue thread) ───────────

def test_schema_ddl_survives_the_armed_guard(armed_sdk, enforced):
    """Schema DDL is INSIDE the guard's surface, and it declares itself.

    The issue thread measured the trap: `_ensure_indexes` runs the DDL sweep
    outside every scope, the creation sites are `try/except`-LOGGED rather than
    raised, so arming the handle guard without a declaration for the sweep does
    not fail loudly — it leaves the projection with no fulltext indexes, and a
    fulltext query then returns an EMPTY result set recorded as
    `empty_results`, indistinguishable from a genuine zero-match.

    So this asserts the OUTCOME (the indexes exist), not the absence of a log
    line: a guard that turned the §17.1 suites green while degrading retrieval
    must fail here.

    ⛔ THE `enforced` FIXTURE IS THE TEST (#9124 review, round 2). Without it the
    sweep ran in MEASURE mode, where `_refuse_unauthorized_cache_write` records
    and returns — so deleting the `with _graph_metadata_write_scope("schema-ddl")`
    wrapper changed nothing observable and this test passed on the pre-fix tree.

    ⛔ AND SO IS THE DROP. The fixture's SDK CONSTRUCTS the projection, which runs
    the sweep once in the (unarmed) default mode and leaves every index in the
    catalogue; asserting that they exist afterwards therefore passes whether or
    not the armed sweep created anything — measured: with `enforced` added and
    the declaration deleted, this test still passed, because the indexes were
    inherited from construction. So the two indexes the sweep owes are DROPPED
    first (through the inner handle, escape (2) of the three the module's SCOPE
    block names) and the test requires the ARMED sweep to put them back. Armed +
    undeclared, the creation sites' `except Exception` swallow the refusal and
    both assertions below red.
    """
    sdk = armed_sdk
    proj = sdk._get_proj()
    assert proj._journal_backed

    # Remove the two indexes this test is about, so their presence afterwards is
    # the armed sweep's work and nothing else.
    proj.g._g.query("DROP INDEX ON :Point(pointKind)")
    proj.g._g.query("CALL db.idx.fulltext.drop('Point')")
    before = {str(cell) for row in proj.g.ro_query("CALL db.indexes()").result_set
              for cell in row}
    assert not any("pointKind" in c for c in before), (
        "the fixture could not drop the Point range index, so the assertion "
        f"below would pass vacuously; catalogue={sorted(before)}")
    assert not any("content" in c and "search_keys" in c for c in before), (
        "the fixture could not drop the Point fulltext index, so the assertion "
        f"below would pass vacuously; catalogue={sorted(before)}")

    # Force the full sweep on the ARMED projection (the fast path would skip
    # it), which is the state in which the guard meets the DDL.
    proj._schema_is_current = lambda: False
    proj._ensure_indexes()

    rows = proj.g.ro_query("CALL db.indexes()").result_set
    names = {str(cell) for row in rows for cell in row}
    assert any("content" in c and "search_keys" in c for c in names), (
        "the Point fulltext index must exist after the sweep — without it "
        f"fulltext retrieval silently returns nothing; catalogue={sorted(names)}"
    )
    assert any("pointKind" in c for c in names), (
        "the Point range indexes must exist after the sweep; "
        f"catalogue={sorted(names)}"
    )


def test_the_schema_declaration_does_not_authorize_a_data_write(armed_sdk, enforced):
    """The declaration covers the SCHEMA, not data — OUTSIDE the sweep.

    `_ensure_indexes` opens the graph-metadata lane around the sweep. That must
    not become a general permission: a node write issued from ordinary code
    immediately after the sweep is still refused.

    ⛔ WHAT THIS TEST DOES *NOT* SAY (#9124 review, round 2, restated because the
    old source docstring claimed it did): it pins the boundary at the END of the
    lane, not the sweep's contents. A `CREATE` added INSIDE
    `_ensure_indexes_declared` sits inside the scope and IS authorized — the one
    such write the sweep really makes (the `search_keys` flatten) is stated as
    the lane's second obligation and pinned by
    `test_the_schema_lane_also_authorizes_the_documented_corpus_fixup`.
    """
    proj = armed_sdk._get_proj()
    proj._schema_is_current = lambda: False
    proj._ensure_indexes()
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:Point {id:'handle-after-sweep'})")


def test_the_schema_lane_also_authorizes_the_documented_corpus_fixup(
        armed_sdk, enforced):
    """The lane's SECOND obligation, executable (#9124 review, round 2).

    `_ensure_indexes`' docstring says the graph-metadata lane also authorizes
    exactly ONE corpus write: the `search_keys` array→string flatten inside
    `_fix_point_search_keys`, which two `except` branches of the sweep reach.
    That claim is only worth the test behind it, so this seeds the owed state
    (a Point whose `search_keys` is still the pre-R2 ARRAY), runs the sweep with
    the guard ARMED, and requires BOTH halves:

      * the flatten LANDED — so the lane really does cover the corpus write. Drop
        the `with _graph_metadata_write_scope("schema-ddl")` wrapper and the
        `SET` is refused, `_fix_point_search_keys`' own `except Exception`
        swallows it, `search_keys` stays an array and this assertion reddens;
      * the lane is NOT a general permission — a `CREATE` issued after the sweep
        returns is still refused (`DirectCacheWriteRefused`), which is the
        boundary the companion test above pins from the other side.

    The seed goes through the INNER handle (`proj.g._g`, escape (2) of the three
    the module's SCOPE block names) on purpose: it is a test-only fixture write,
    and the guarded handle would rightly refuse it under `enforced`.
    """
    sdk = armed_sdk
    proj = sdk._get_proj()
    assert proj._journal_backed

    proj.g._g.query(
        "CREATE (n:Point {id:'search-keys-owed', content:'owed search keys', "
        "search_keys:['alpha','beta']})"
    )
    assert proj._array_valued_search_keys_exist(), (
        "the fixture did not create the owed fixup state — without an array "
        "`search_keys` the sweep never reaches `_fix_point_search_keys` and "
        "this test would pass vacuously"
    )

    proj._schema_is_current = lambda: False
    proj._ensure_indexes()  # must NOT raise: the lane authorizes the flatten

    flat = proj.g.ro_query(
        "MATCH (n:Point {id:'search-keys-owed'}) RETURN n.search_keys"
    ).result_set[0][0]
    assert flat == "alpha beta", (
        "the schema lane's documented corpus fixup did not land — with the "
        "guard ARMED an undeclared `SET n.search_keys` is refused and this "
        "helper's own `except Exception` swallows it; "
        f"search_keys={flat!r}"
    )

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:Point {id:'handle-after-fixup'})")
