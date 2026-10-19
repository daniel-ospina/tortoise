"""#9195 §17.1 — the SDK's structural-edge write lane **declares** itself, so
closing the write handle (#9079 / #9124) does not refuse the product's own edge
mints.

`tortoise/projection/edges.py::_EdgeHandlers.create_edge` reaches the
projection's guarded write handle (`self.g`) with **no authorization scope and
no journal record**, and its three typed wrappers
(`create_owned_by`/`create_managed_by`/`create_authored_by`) are one funnel onto
the same statement:

    MATCH (s:L {k:$sv}) MATCH (t:M {k:$tv}) MERGE (s)-[:<predicate>]->(t)

`#9124`'s guard classifies that `MERGE` a write (`_statement_writes`), so with
the guard armed every `sdk.create_edge*` call site on `origin/main` would be
refused — including the self-host onboarding seed, whose REST callers answer
500. That is a product regression wearing a guard, so this declaration lands in
the **before-the-guard batch** with `#9123`/`#9121`/`#9122`.

⛔ WHAT THIS SUITE PINS, AND WHY IT IS NOT VACUOUS. `#9124`'s guard is not on
`main` yet, so `_instrument` reproduces the third door's *predicate* verbatim —
journal-backed + no declaration in flight + a mutating statement ⇒
`DirectCacheWriteRefused` — and installs it on `_GuardedGraph.query`, reading
`_CACHE_WRITE_AUTHORIZED` **at the write**, inside the handle dispatch, where
`#9124`'s guard will read it. "The write ran inside a declaration" is therefore
observed, not inferred from source.

The chosen shape is **(a) DECLARED**, and the lane states its claim **per
relation** rather than globally (`structural_edge_carrier`, derived from
`DERIVABLE_STRUCTURAL_RELS`). Read the block above `STRUCTURAL_EDGE_WRITE_LANE`
for the argument. Its two measured legs:

* `DERIVABLE_STRUCTURAL_RELS` are snapshot-carried — replay resurrects them from
  the endpoint Point's immutable record (`entities._upsert_point_edges`);
* every other allowlisted predicate has **no replay carrier**: measured at this
  head, `rebuild_all` over a journal holding such edges reports `edges: 0` and
  the edges are gone. **This lane discloses that gap; it does not bless it** —
  the gap is on `origin/main` today, undeclared, and the carrier belongs to the
  record-store work (`#7879`), not to a declaration child.

⛔ WHAT THIS SUITE DOES **NOT** CLAIM:
* it does not claim `_EdgeHandlers` holds only this lane's writers. The
  about/provenance/tag/fold verbs mint NODES or stamp `sourceVersion` or are
  already scoped by their journalling door, so they are NOT in this lane, and
  `test_every_edge_minting_verb_is_declared_or_stated` enumerates them from this
  module's own AST so a new one cannot appear undeclared. `#9124` must census
  them before it arms; this suite's guard assertion is deliberately scoped to
  the four declared verbs.
* it does not claim the bare `references` edge is anchored. The generic verb
  writes it property-free and a later `link_source_to_*` `MERGE` cannot repair
  it (`#5213` option 3, §9.6, `tests/test_references_boundary_5213.py`);
  `test_the_references_edge_is_still_written_bare` asserts THAT shape.
* it does not claim durability for the carrier-less relations (above).

⚠️ What is NOT acceptable, and what these tests exist to prevent: "fixing" this
by making `create_edge` tolerate a refusal, or by treating the lane as a bypass
(a blanket scope around the handle). A declaration that also authorizes a raw
`MERGE` it does not own is not a declaration.
"""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _GuardedGraph,
    _statement_writes,
)
from tortoise.projection.edges import (
    _VALID_EDGE_PREDICATES,
    DERIVABLE_STRUCTURAL_RELS,
    STRUCTURAL_EDGE_WRITE_LANE,
    structural_edge_carrier,
)

#: The lane's declared verbs — one funnel plus its three typed wrappers.
DECLARED_VERBS = (
    "create_edge", "create_owned_by", "create_managed_by", "create_authored_by",
)

#: The lane's declared verbs — the two edge mints plus `create_edge`'s three
#: typed wrappers.
DECLARED_VERBS = (
    "create_edge", "create_about_edge",
    "create_owned_by", "create_managed_by", "create_authored_by",
)

#: The predicates this lane can write. Pinned as a LITERAL partition of
#: `_VALID_EDGE_PREDICATES` so a predicate added to (or moved between) the two
#: halves is a visible diff in this file rather than a silent widening.
SNAPSHOT_CARRIED = frozenset({
    "aboutSubject", "aboutObject", "aboutEvent", "aboutDocument",
})
#: No carrier is EMITTED from this lane, so a rebuild drops these edges. The
#: name says "emitted", not "exists": the replay engine has a carrier
#: (`DirectEdgeCreated` + its pass-2b fold) and emitting that record restores
#: them — what is missing is the emission from this projection verb.
NO_CARRIER_EMITTED = frozenset({
    "performs", "produces", "uses", "authoredBy", "ownedBy", "managedBy",
    "hasMember", "holdsRole", "memberOf", "reportsTo",
    "participatesIn", "hasPart", "related", "dependsOn", "references",
    "wasDerivedFrom", "aboutSource", "aboutAction",
})

DOC_URL = "https://docs9195.test/page"


@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK with a configured JSONL journal, so §17.1's guard is ARMED.

    The same shape `tests/test_9079_onboarding_declared_lane.py` uses: the
    explicit `db_path` makes the projection embedded when no URI is configured,
    and `event_log_path` is what arms the guard.
    """
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(str(tmp_path / "edges9195.db"),
                      event_log_path=str(tmp_path / "journal.jsonl"))
    try:
        yield sdk
    finally:
        sdk.close()


def _instrument(monkeypatch, *, refuse: bool):
    """Install the #9124 predicate on `_GuardedGraph.query`; record every
    statement the edge writers issue as `(statement, reason, writes)`.

    `reason` is `_CACHE_WRITE_AUTHORIZED` AT THE WRITE — read inside the handle
    dispatch, which is where #9124's guard will read it.

    ⛔ The statement is recorded IN FULL (normalized whitespace only):
    truncating it would let the class checks read a PREFIX of the statement they
    are checking (the fail-open direction #9121 measured on its own suite).
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


def _endpoints(sdk) -> dict:
    """The walk's fixtures: two Points for the generic verb, two MORE Points
    for the typed wrappers (the generic loop mints `(p1)-[:ownedBy]->(p2)`, so
    reusing those ids for `create_owned_by` would close a real ownership cycle
    and raise), and a document-bearing `:Source` (`aboutDocument`'s legal
    target, ONTOLOGY §4.4)."""
    first = sdk.create_point("statement", "edge-lane source 9195")
    second = sdk.create_point("statement", "edge-lane target 9195")
    third = sdk.create_point("statement", "edge-lane wrapper entity 9195")
    fourth = sdk.create_point("statement", "edge-lane wrapper subject 9195")
    sdk.create_source(DOC_URL, "web", documentKind="page")

    def _id(p):
        return p["id"] if isinstance(p, dict) else p

    return {"source": _id(first), "target": _id(second),
            "entity": _id(third), "subject": _id(fourth),
            "doc": DOC_URL}


def _walk_the_edge_lane(proj, endpoints) -> None:
    """Every DECLARED verb, in a plausible order.

    The generic verb runs over the whole allowlist, because the lane's claim is
    per relation and a predicate that stops reaching the funnel must fail here
    rather than quietly leaving the walk's coverage.
    """
    for predicate in sorted(_VALID_EDGE_PREDICATES):
        target = endpoints["doc"] if predicate == "aboutDocument" \
            else endpoints["target"]
        proj.create_edge(endpoints["source"], target, predicate)
    for about_type in ("aboutSubject", "aboutObject", "aboutEvent",
                       "aboutPoint", "aboutDocument"):
        target = endpoints["doc"] if about_type == "aboutDocument" \
            else endpoints["target"]
        proj.create_about_edge(endpoints["source"], target, about_type)
    proj.create_owned_by(endpoints["entity"], endpoints["subject"])
    proj.create_managed_by(endpoints["entity"], endpoints["subject"])
    proj.create_authored_by(endpoints["entity"], endpoints["subject"])


# ── the declaration: the lane's write runs inside it ──────────────────────

def test_every_edge_mint_declares_its_lane(sdk_with_journal, monkeypatch):
    """Was RED before this change: `_CACHE_WRITE_AUTHORIZED` was `None` at the
    lane's `MERGE`, so #9124's handle guard refuses the product's own edge
    mints. MUTATION: drop the `_structural_edge_write_scope()` wrapper in
    `create_edge` and this fails with the undeclared statements listed.
    """
    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed is True, (
        "the fixture did not arm §17.1 — an unarmed projection is outside the "
        "rule and every assertion below would be vacuous"
    )
    endpoints = _endpoints(sdk_with_journal)

    seen = _instrument(monkeypatch, refuse=False)
    _walk_the_edge_lane(proj, endpoints)

    writes = [(c, r) for c, r, w in seen if w]
    assert len(writes) >= len(_VALID_EDGE_PREDICATES), (
        f"the walk issued {len(writes)} mutating statement(s) for "
        f"{len(_VALID_EDGE_PREDICATES)} predicates; the lane no longer funnels "
        "every mint through the declared statement, so this pin has gone stale"
    )
    undeclared = [c for c, r in writes if r is None]
    assert not undeclared, (
        "these structural-edge mints reached the cache handle OUTSIDE any "
        "§17.1 declaration: #9079's handle guard will refuse them, and the "
        "self-host/hosted onboarding seed answers 500 when it does "
        f"({len(undeclared)} statement(s)): {undeclared[:3]}"
    )
    assert {r for _c, r in writes} == {STRUCTURAL_EDGE_WRITE_LANE}, (
        "the lane must declare ONE name, so the guard block and this pin cannot "
        f"drift about which lane authorized a write; saw "
        f"{sorted({r for _c, r in writes})}"
    )


def test_a_read_on_this_path_is_not_declared(sdk_with_journal, monkeypatch):
    """The scope wraps the MUTATING statement, not the verb.

    `create_edge` resolves both endpoints, and (for `ownedBy`) walks a bounded
    cycle probe, BEFORE it writes. Those are reads, and declaring them would
    widen the lane over statements it does not own — the direction that turns a
    declaration into a blanket scope.
    """
    proj = sdk_with_journal._get_proj()
    endpoints = _endpoints(sdk_with_journal)

    seen = _instrument(monkeypatch, refuse=False)
    proj.create_edge(endpoints["source"], endpoints["target"], "ownedBy")

    reads = [(c, r) for c, r, w in seen if not w]
    assert reads, "the walk issued no read — this pin would be vacuous"
    declared_reads = [(c, r) for c, r in reads if r is not None]
    assert not declared_reads, (
        "a statement the CLASSIFIER called read-only ran inside the lane's "
        "write declaration, i.e. the scope is wrapped around the verb (or the "
        f"method) instead of around its write: {declared_reads}"
    )


def test_the_edges_still_land_when_the_handle_guard_is_armed(
        sdk_with_journal, monkeypatch):
    """Was RED before this change — this is the P0 the issue names: with the
    guard armed, `create_edge` raised `DirectCacheWriteRefused` and the edge
    never landed, while every caller treats a raise as a failed operation (the
    seed turns it into a 500).
    """
    proj = sdk_with_journal._get_proj()
    endpoints = _endpoints(sdk_with_journal)

    _instrument(monkeypatch, refuse=True)
    _walk_the_edge_lane(proj, endpoints)

    def count(rel: str) -> int:
        return proj.g.query(
            f"MATCH ()-[r:{rel}]->() RETURN count(r)").result_set[0][0]

    for rel in ("related", "dependsOn", "ownedBy", "managedBy", "authoredBy",
                "wasDerivedFrom", "aboutDocument"):
        assert count(rel) >= 1, (
            f"the armed handle guard refused the product's own `{rel}` mint, "
            "so the edge never landed"
        )
    assert count("references") >= 1, (
        "the bare `references` mint was refused — it is the relation #5213 "
        "documents as written WITHOUT an anchor, and the lane must not start "
        "requiring one"
    )


def test_the_declaration_is_not_a_bypass(sdk_with_journal, monkeypatch):
    """A declaration must not become a bypass, in either direction:

    * an UNDECLARED mutating statement on the handle is still refused while the
      guard is armed (the lane is not a blanket scope around `proj.g`);
    * the scope does not LEAK — after a mint returns, no reason is in flight, so
      the caller's next write is refused again (a `ContextVar` reset in a
      `finally`, #7951's thread-isolation property).
    """
    proj = sdk_with_journal._get_proj()
    endpoints = _endpoints(sdk_with_journal)

    _instrument(monkeypatch, refuse=True)
    proj.create_edge(endpoints["source"], endpoints["target"], "related")

    assert _CACHE_WRITE_AUTHORIZED.get() is None, (
        "the lane's write scope leaked past the statement — a caller's next "
        "write would be silently authorized"
    )
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.hijacked9195 = true",
            params={"id": endpoints["source"]},
        )


# ── the enumeration: no edge-minting verb appears undeclared ──────────────

#: `_EdgeHandlers` verbs that CAN issue a mutating statement but are NOT this
#: lane, each with the reason — the same bucket discipline
#: `tests/test_9079_onboarding_declared_lane.py` applies to `_run` call sites.
_STATED_OTHER_WRITERS = {
    "_create_edges":
        "the FOLD: runs inside `_live_write_scope`/`_replay_write_scope`",
    "replay_about_links":
        "replay: the links came FROM the journal's `EventRecorded` record",
    "_create_about_edges":
        "delegates to `wire_about_link`; the name-resolved about lane, whose "
        "links are journaled with the record (#8398)",
    "wire_about_link":
        "the single about-link construction; journals via its caller's record "
        "and MINTS `:Subject` stubs",
    "_link_source":
        "PROVENANCE: MERGEs a `:Source` NODE and stamps `r.sourceVersion`",
    "link_source_to_entity":
        "PROVENANCE: MERGEs a `:Source` node and stamps `r.sourceVersion`",
    "link_source_to_event":
        "PROVENANCE: MERGEs a `:Source` node and stamps `r.sourceVersion`",
    "link_source_to_legacy_event":
        "PROVENANCE: MERGEs a `:Source` node and stamps `r.sourceVersion`",
    "_sync_tags":
        "`TAGGED` edges + a `:Tag` MERGE, written from `create_point`'s own "
        "declared scope",
}

_WRITE_KEYWORDS = ("MERGE", "CREATE", "SET ", "DELETE", "REMOVE ")


def _mutating_verbs() -> set[str]:
    """Every `_EdgeHandlers` verb that can reach a mutating statement.

    DERIVED from the module's own AST, not hand-maintained: a verb counts when
    it issues a handle call that can write, and the set is then CLOSED over
    both intra-class delegation (`create_owned_by` → `create_edge`) and the
    module-level helpers a verb calls (`_mint_source_stub` and friends MERGE, so
    a verb that only delegates to one still writes).

    ⛔ IT FAILS CLOSED ON A SHAPE IT CANNOT READ. A handle call whose statement
    text is not a literal we can test (`self.g.query(some_variable)`), any
    verb other than the statement verbs on `self.g`, and any module-level
    helper that itself writes all count as writers. The fail-open direction —
    a verb that writes being classified read-only and so passing as
    "undeclared" — is exactly what this enumeration exists to prevent, so an
    unknown shape must make noise, not silence.
    """
    from tortoise.projection import edges as _edges

    source = Path(_edges.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next((n for n in ast.walk(tree)
                if isinstance(n, ast.ClassDef) and n.name == "_EdgeHandlers"),
               None)
    assert cls is not None, "`_EdgeHandlers` is gone or renamed"

    methods = {fn.name: fn for fn in cls.body if isinstance(fn, ast.FunctionDef)}
    module_fns = {fn.name: fn for fn in tree.body
                  if isinstance(fn, ast.FunctionDef)}

    #: The handle's statement verbs. `execute_command` is included because
    #: `_GuardedGraph` overrides it and a `GRAPH.QUERY` through it writes;
    #: `profile`/`explain` execute the statement.
    statement_verbs = {"query", "_query", "execute_command", "profile",
                       "explain"}

    def _receiver(node: ast.Call) -> str | None:
        """`g` for a call on `self.g` / `self.g.<attr>`, else `None`."""
        func = node.func
        if not isinstance(func, ast.Attribute):
            return None
        if isinstance(func.value, ast.Name) and func.value.id == "g":
            return "g"
        if (isinstance(func.value, ast.Attribute)
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "self"
                and func.value.attr == "g"):
            return "g"
        return None

    def _issues_a_write(fn: ast.FunctionDef,
                        writers: set[str] | None = None) -> bool:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            # Module-level helper or same-class sibling that writes?
            target = None
            if isinstance(node.func, ast.Name):
                target = node.func.id
            elif (isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in {"self", "_edges"}):
                target = node.func.attr
            if target is not None and writers and target in writers:
                return True

            on_handle = _receiver(node)
            if on_handle is None:
                continue
            verb = node.func.attr
            if verb not in statement_verbs or not node.args:
                # An unknown verb on the handle (`self.g.anything(...)`) cannot
                # be proven read-only — fail closed.
                return True
            # The statement text is assembled in several spellings on this
            # module (a plain literal, an f-string, and `literal + helper(...)`
            # — `_link_source` uses the last), so every literal piece of the
            # first argument's expression tree is read. NO literal at all means
            # the statement is unreadable: fail closed.
            literal = "".join(
                part.value for part in ast.walk(node.args[0])
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            if not literal.strip():
                return True
            if any(kw in literal.upper() for kw in _WRITE_KEYWORDS):
                return True
        return False

    module_writers = {name for name, fn in module_fns.items()
                      if _issues_a_write(fn)}
    writers = {name for name, fn in methods.items()
               if _issues_a_write(fn, module_writers)}
    # Delegation closure: a verb that calls a writing sibling can write too.
    changed = True
    while changed:
        changed = False
        for name, fn in methods.items():
            if name in writers:
                continue
            if _issues_a_write(fn, writers):
                writers.add(name)
                changed = True
    return writers


def _verbs_called_by_walk() -> set[str]:
    """The `proj.<name>(...)` calls the walk actually issues, read from its own
    body — so a DECLARED bucket cannot be satisfied by a name no walk calls."""
    import inspect

    tree = ast.parse(textwrap.dedent(inspect.getsource(_walk_the_edge_lane)))
    return {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "proj"
    }


def test_the_verb_scan_is_not_vacuous():
    """The derivation above must find the lane's own funnel and at least one
    verb per bucket — otherwise the enumeration proves nothing."""
    writers = _mutating_verbs()
    for required in ("create_edge", "create_owned_by", "create_managed_by",
                     "create_authored_by", "_link_source", "create_about_edge",
                     "_create_edges", "_sync_tags"):
        assert required in writers, (
            f"the AST scan did not find {required!r} as a mutating verb — the "
            f"derivation is not measuring the class it claims to: "
            f"{sorted(writers)}"
        )
    # And it must NOT sweep in a verb that writes nothing (the read-only
    # resolvers on this path), or the buckets below become noise.
    for read_only in ("resolve_about_link", "_resolve_about_edge_target",
                      "_resolve_entity", "resolve_structural_target",
                      "edge_stats"):
        assert read_only not in writers, (
            f"{read_only!r} is read-only but the scan called it a writer — the "
            "keyword test is too loose to be a check"
        )


def test_every_edge_minting_verb_is_declared_or_stated():
    """The issue's "no verb left undeclared" row.

    A new edge-minting verb on `_EdgeHandlers` fails HERE — it is neither in
    `DECLARED_VERBS` nor accounted for in `_STATED_OTHER_WRITERS` — instead of
    arriving undeclared and being refused by #9124's guard in production.
    """
    writers = _mutating_verbs()
    uncovered = writers - set(DECLARED_VERBS) - set(_STATED_OTHER_WRITERS)
    assert not uncovered, (
        "these `_EdgeHandlers` verbs can issue a mutating statement and are "
        f"neither declared by this lane nor stated with a reason: "
        f"{sorted(uncovered)}. Add them to the lane's declaration (and this "
        "suite's walk), or to `_STATED_OTHER_WRITERS` with the reason their "
        "write is scoped/recorded elsewhere."
    )

    stale = (set(DECLARED_VERBS) | set(_STATED_OTHER_WRITERS)) - writers
    assert not stale, (
        "this suite names verbs that no longer issue a mutating statement — "
        f"the buckets have drifted from the module: {sorted(stale)}"
    )

    # ⛔ Bucket MEMBERSHIP is a claim about the walk, so it is checked against
    # the walk's own BODY: otherwise the cheapest way to green the check above
    # is to add a name to `DECLARED_VERBS` that no walk ever calls, leaving the
    # verb unexercised and its statements unseen.
    called = _verbs_called_by_walk()
    not_called = sorted(set(DECLARED_VERBS) - called)
    assert not not_called, (
        "these verbs are claimed as DECLARED but the walk never calls them, so "
        f"their statements are never observed: {not_called}"
    )


# ── the lane's per-relation claim ─────────────────────────────────────────

def test_the_lane_states_its_carrier_per_relation():
    """The issue's "classify per relation, not per lane" row.

    A blanket answer is false in one direction or the other: `references` has no
    anchor and no carrier emitted from here (`#5213` option 3, §9.6), while the
    `about*` family is snapshot-derivable (`DERIVABLE_STRUCTURAL_RELS` ←
    `entities._upsert_point_edges`). `structural_edge_carrier` must answer from
    that ONE set, and the two halves must partition the predicates this lane can
    write — so a new predicate, or a change to what replay resurrects, is a
    visible diff here.

    ⚠️ A `None` here means "no carrier is EMITTED from this verb", NOT "no
    replay carrier can exist": the engine's `DirectEdgeCreated` fold restores
    these very edges when a record describes them (measured), so the gap is the
    emission.
    """
    assert set(_VALID_EDGE_PREDICATES) == SNAPSHOT_CARRIED | NO_CARRIER_EMITTED, (
        "the pinned partition no longer covers the predicates this lane can "
        f"write: uncovered={sorted(set(_VALID_EDGE_PREDICATES) - SNAPSHOT_CARRIED - NO_CARRIER_EMITTED)} "
        f"stale={sorted((SNAPSHOT_CARRIED | NO_CARRIER_EMITTED) - set(_VALID_EDGE_PREDICATES))}"
    )
    assert not (SNAPSHOT_CARRIED & NO_CARRIER_EMITTED)

    assert set(_VALID_EDGE_PREDICATES) & DERIVABLE_STRUCTURAL_RELS == SNAPSHOT_CARRIED, (
        "the snapshot-carried half must be exactly the intersection with the "
        "set the replay engines read — a hand-written second table would drift "
        "from what replay actually resurrects"
    )

    for rel in SNAPSHOT_CARRIED:
        assert structural_edge_carrier(rel) == "endpoint-snapshot", rel
    for rel in NO_CARRIER_EMITTED:
        assert structural_edge_carrier(rel) is None, (
            f"`{rel}` is classified as emitting no carrier, but the lane claims "
            "a replay carrier for it"
        )
    assert structural_edge_carrier("references") is None, (
        "`references` is the relation the issue names as the counter-example to "
        "a global answer; it must keep its no-carrier-from-here classification"
    )


def test_the_references_edge_is_still_written_bare(sdk_with_journal):
    """The issue's "the bare `references` edge" row: whatever this lane
    declares, it does not route `references` through the derivation anchor.

    `create_edge` takes no property parameter, so an anchor is unsuppliable
    from the caller; the assertion is the WRITE's shape, not a doc claim
    (`#5213` option 3; `tests/test_references_boundary_5213.py`).
    """
    proj = sdk_with_journal._get_proj()
    endpoints = _endpoints(sdk_with_journal)

    assert proj.create_edge(
        endpoints["source"], endpoints["target"], "references") is True
    rows = proj.g.query(
        "MATCH (a:Point {id:$a})-[r:references]->(b:Point {id:$b}) "
        "RETURN r.sourceVersion",
        params={"a": endpoints["source"], "b": endpoints["target"]},
    ).result_set
    assert rows, "the `references` edge did not land"
    assert rows[0][0] is None, (
        "the generic verb started stamping `sourceVersion` — that is a "
        "behaviour change (#5213 option 1), not a §17.1 declaration"
    )


# ── the decision is recorded where the writer reads it ───────────────────

def test_the_lane_and_its_argument_are_recorded():
    """The issue's requirement that the lane's obligation be stated AT THE
    VERB, not as one global claim. Nothing else in this suite would notice the
    argument being deleted from the source."""
    from tortoise.projection import edges as _edges

    assert STRUCTURAL_EDGE_WRITE_LANE == "structural-edge.derived", (
        "the lane name is what a refusal message and the projection module's "
        "declaration ledger cite; changing it is a deliberate act"
    )

    block = Path(_edges.__file__).read_text(encoding="utf-8")
    # Load-bearing phrases, not words that survive the paragraph's deletion:
    # `"declared"` alone is satisfied by the module docstring, and `#9124` /
    # `#9079` each appear more than once. These co-exist only if the
    # DECLARATION paragraph is present.
    for phrase in (
        "WHAT THIS LANE CLAIMS, PER RELATION",
        "This lane does NOT claim durability for them",
        "WHAT THIS LANE DOES NOT COVER",
        "`references` KEEPS ITS OWN SHAPE",
    ):
        assert phrase in block, (
            f"the declaration block no longer contains {phrase!r} — the shape "
            "decision #9195 requires recorded has been deleted or reworded out "
            "from under this pin"
        )
    assert "MEASURED at" in block, (
        "the carrier gap is asserted, not measured — the lane must record the "
        "measurement that produced it (a rebuild drops these edges)"
    )
    assert "DirectEdgeCreated" in block, (
        "the declaration must name the replay machinery that DOES exist for "
        "these edges, so the gap is stated as the EMISSION from this door and "
        "not as a missing replay engine"
    )
    assert "#5213" in block and "#7879" in block, (
        "the declaration must cite the `references` boundary (#5213) and where "
        "the missing replay carrier belongs (#7879)"
    )

    # The ledger in the projection's own module must list the lane, so a reader
    # who asks "which writes are sanctioned?" finds it there too.
    from tortoise.projection import __file__ as _proj_file

    init_block = Path(_proj_file).read_text(encoding="utf-8")
    assert f"`{STRUCTURAL_EDGE_WRITE_LANE}`" in init_block, (
        "the projection's declaration ledger no longer names this lane"
    )
