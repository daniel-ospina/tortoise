"""#8093 — the `:Graph.namespace` cell vs the namespace the code derives (doc↔code pin).

The defect: ``docs/registry-graph-schema.md`` §Graph stated the custom-graph
namespace as ``team_<org_id>_<gid>`` and the default graph's namespace as
``team_<name>``. The code derives ``org_{org_id}_{gid}`` for customs
(``acl_graph_users._graph_namespace``, ``sdk._graph_create``,
``hosted_api._provision_graph``) and the org namespace for the default graph —
``org_{org_id}`` / ``org_{name}`` — because #3543 renamed the prefix from
``team_`` to ``org_``. ``team_*`` survives only as the pre-rename shape the
purge guard still recognises and no data migration rewrites.

That cell is the input another lane uses to derive an ACL key pattern or a
namespace lookup (``docs/authorization-architecture.md`` §2.1 cites the ACL
recipe), so a stale shape there is not cosmetic.

Class-B test doctrine:
  (1) *What value makes this test fail?*  Restating ``team_<org_id>_<gid>`` (or
      ``team_<name>`` for the default) as the derived shape; dropping a derived
      shape from the cell; un-marking the legacy prefix; or changing the code
      derivation the cell is pinned to.
  (2) *Does the test reach it?*  Yes — the shapes are read from the committed
      doc cell AND from the code that derives them, so the assertion is
      doc-against-code, never the doc against itself. That code side is a
      derivation call where one exists (``acl_graph_users._graph_namespace`` for
      the ACL key pattern, ``hosted_api._org_graph_name`` for the hosted
      default) and the deriver's own source line where the shape is written by a
      function no test can call without a DB (``sdk._graph_create``,
      ``hosted_api._provision_graph``, ``sdk.org_create``).

Hermetic: reads committed files only — the two docs, ``sdk.py``,
``hosted_api.py``, ``backup_sweep.py``, ``mcp_server.py`` and the #3543 tenancy
migration — and calls pure derivations. No fixture, no DB. The read set is named
rather than counted: a count here re-stales the moment a test is added, which is
the failure class this file exists to catch. ``tier1`` is the load-bearing
registration, not ``core``: a ``docs/``-only diff takes ``select()``'s early
return and runs ``tier1`` alone, so a docs-only edit is exactly the change class
this pin exists for.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCHEMA_DOC = REPO / "docs" / "registry-graph-schema.md"
AUTHZ_DOC = REPO / "docs" / "authorization-architecture.md"

# Any `file.py:NNN` citation — a line number rots on the next rename or merge
# (the #7818 precedent: two citations in one section went stale within weeks).
_LINE_CITATION = re.compile(r"[\w/]+\.py:\d+")

CUSTOM_SHAPE = "org_{org_id}_{gid}"   # a custom graph: _graph_namespace / _graph_create
DEFAULT_SHAPES = ("org_{org_id}", "org_{name}")  # the org namespace (the default graph)

# The default-graph CLAUSE of the cell: the stored-name lead-in (what the ns IS)
# up to the customs clause. A whole-cell substring test cannot discriminate here
# — `org_{org_id}` is a substring of `org_{org_id}_{gid}`, stated in the SAME
# cell for customs, so it stays satisfied by the customs clause alone even after
# the hosted default-graph clause is deleted. Pinning the clause is what bites.
_DEFAULT_CLAUSE_START = "the stored `Team.graph_name`:"
_DEFAULT_CLAUSE_END = "Customs ="


def _flat(text: str) -> str:
    """Collapse the cell's line comments and wrapping to one space-separated line.

    The committed cell is a code-block comment: every line is prefixed with
    `//` and hard-wrapped, so a phrase assertion against the RAW slice is
    defeated by a line break (verified: "not by the deployment lane" straddles a
    wrap and was invisible to a raw `in` test). Every phrase-level assertion
    therefore reads the FLATTENED text (the cell, its default-graph clause, and
    the Default-graph-semantics bullet); shape-level ones can keep the raw
    slice.
    """
    return " ".join(text.replace("//", " ").split())


def _namespace_cell() -> str:
    """The committed `:Graph` namespace cell — from its key to the next key."""
    doc = SCHEMA_DOC.read_text(encoding="utf-8")
    start = doc.index("  namespace: string,")
    end = doc.index("  status: string,", start)
    return doc[start:end]


def _default_graph_clause() -> str:
    """The default-graph clause of the cell — from the stored-name lead-in to `Customs`.

    Both ends are asserted, not `index()`-ed: a bare `str.index` failure reads
    ``ValueError: substring not found`` and names nothing, so a cell that loses
    the clause (or renames it) would red without saying what it must state —
    which is exactly the diagnosability this pin exists for.
    """
    cell = _namespace_cell()
    assert _DEFAULT_CLAUSE_START in cell, (
        "§Graph must state the default graph's namespace in a clause led by "
        f"{_DEFAULT_CLAUSE_START!r} — the stored `graph_name` is what the "
        "default graph's namespace IS, so the cell cannot drop it and state a "
        "bare team/org namespace instead")
    start = cell.index(_DEFAULT_CLAUSE_START) + len(_DEFAULT_CLAUSE_START)
    assert _DEFAULT_CLAUSE_END in cell[start:], (
        "§Graph's default-graph clause must still be followed by the "
        f"{_DEFAULT_CLAUSE_END!r} clause — the slice pins the default-graph "
        "statement alone, so its end boundary must stay discoverable")
    end = cell.index(_DEFAULT_CLAUSE_END, start)
    return cell[start:end]


def _default_graph_bullet() -> str:
    """The §"Default-graph semantics" bullet that states the derived namespace."""
    doc = SCHEMA_DOC.read_text(encoding="utf-8")
    start = doc.index("Default-graph semantics")
    end = doc.index("\n- **No per-graph keys", start)
    return doc[start:end]


# ── 1. the custom shape is the one the code derives ─────────────────────────

def test_custom_namespace_cell_matches_the_acl_and_provisioning_derivation():
    """§Graph states `org_{org_id}_{gid}` — the exact shape both lanes derive.

    (1) *What value makes this test fail?*  The pre-fix ``team_<org_id>_<gid>``
        restated as the custom shape, or the cell losing the derived shape.
    (2) *Does the test reach it?*  Yes — the committed cell is compared to a
        FIXED literal, and the same literal is asserted at each deriver the cell
        cites: `acl_graph_users._graph_namespace` (called on a sample org/gid —
        the mirror the ACL pattern is built from), `sdk._graph_create` and
        `hosted_api._provision_graph` (read by symbol from their own source, since
        each needs a DB to call and `_provision_graph`'s branch writes the
        `graphs.namespace` row the ACL pattern must match). A change to any of
        the three reds the doc pin; the literal is the pin's target, not its
        source.
    """
    from tortoise.acl_graph_users import _graph_namespace

    derived = _graph_namespace("team_a", "g_0123abcd")
    assert derived == "org_team_a_g_0123abcd", (
        "the ACL key pattern is the graph's exact namespace "
        f"(got {derived!r}); the doc cell is pinned to this derivation")

    # `_graph_namespace` is a MIRROR of the derivers, not the only one: the cell
    # names `_graph_create` / `_provision_graph` as the sites that mint a custom
    # graph, and neither is reachable from a hermetic test (both need a DB;
    # `_provision_graph`'s Supabase branch is the one that writes the namespace
    # the ACL pattern has to match). Pin them by symbol from their own source, or
    # a change to what those two mint re-stales the cell with this pin green.
    sdk_src = (REPO / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    # Two branches, two sites: the supabase one and the registry one (the only
    # live custom-graph path when no `namespace=` is passed). A bare substring
    # test is satisfied by EITHER site, so renaming one branch alone stayed green
    # — count the template, and bind the registry branch by its neighbour gid
    # line, so neither branch can be changed on its own.
    template = 'ns = namespace or f"org_{org_id}_{gid}"'
    assert sdk_src.count(template) >= 2, (
        "`sdk._graph_create` must keep minting `org_{org_id}_{gid}` in BOTH of "
        "its branches (supabase and registry) — one renamed branch means the "
        "ACL key pattern no longer names the graph that branch created, while "
        "the cell keeps stating the shape")
    assert ('gid = f"g_{_uuid.uuid4().hex[:16]}"\n        ' + template) in sdk_src, (
        "`sdk._graph_create`'s REGISTRY branch (the random gid, unlike the "
        "supabase branch's deterministic one) must keep minting "
        "`org_{org_id}_{gid}` — it is the live custom-graph path")
    hosted_src = (REPO / "tortoise" / "hosted_api.py").read_text(encoding="utf-8")
    assert '''ns = f"org_{org['id']}_{gid}"''' in hosted_src, (
        "`hosted_api._provision_graph`'s Supabase branch must keep minting "
        "`org_{org_id}_{gid}` — it writes the `graphs.namespace` row the ACL "
        "key pattern is built from, and no other test reaches that branch")

    cell = _namespace_cell()
    assert CUSTOM_SHAPE in cell, (
        f"§Graph must state the derived custom shape {CUSTOM_SHAPE!r} — "
        "it is the input another lane derives a key pattern from")
    assert "team_<org_id>_<gid>" not in cell, (
        "the legacy shape is stated as the current one; after #3543 the "
        "derivation is org_{org_id}_{gid}")


def test_default_graph_namespace_cell_names_the_org_namespace_not_legacy_team():
    """The default graph's ns is the org namespace — `org_…`, not `team_<name>`.

    (1) *What value makes this test fail?*  Re-stating ``team_<name>`` as the
        default graph's namespace (the pre-fix cell), or dropping either
        hosted/registry variant from the DEFAULT-GRAPH CLAUSE.
    (2) *Does the test reach it?*  Yes — the two shapes are asserted against
        `_default_graph_clause()`, the slice that actually states the default
        graph's namespace, so deleting the hosted `org_{org_id}` clause reds
        even though `org_{org_id}` survives as a substring of the custom shape
        stated later in the same cell. And each half is anchored to its own
        code side: `hosted_api._org_graph_name` is the shared seam the
        export/open paths use, and its no-stored-name fallback is asserted to
        be `org_{org_id}`; the registry-lane `org_{name}` half is anchored to
        `sdk.org_create`'s derivation, which no exported function returns —
        without that anchor the assertion would be the doc against itself. The
        ATTRIBUTION is pinned too, not just the presence of both shapes, and it is
        pinned on the right AXIS: the shape follows the MINTING FUNCTION, not the
        deployment lane (`register_user` and `agent_signup` compute
        `org_{org_id}` before any lane branch; `provision_tenant` is selfhost-only
        and also mints it). `_org_graph_name` is the EXPORT seam — the open half
        is `_open_org_graph_sdk` / `_graph_has_org_namespace`, pinned by the
        legacy test. The `org_{org_id}` deriver list must name the registry
        `kind='default'` node by its MINTER, not as a bare deriver: that node
        KIND is written with both shapes (`agent_signup`'s registry branch with
        `org_{org_id}`, `sdk.org_create` with `org_{name}`), so an unqualified
        listing sends a reader resolving a registry default node to a graph
        that may not exist.
    """
    from tortoise.hosted_api import _org_graph_name

    assert _org_graph_name({}, "team_x") == "org_team_x", (
        "the org namespace fallback is org_{org_id} (#3543 rename)")
    assert _org_graph_name({"graph_name": "org_team_y"}, "team_x") == "org_team_y", (
        "a stored graph_name wins verbatim — the default graph's namespace "
        "IS that stored name")

    clause = _default_graph_clause()
    for shape in DEFAULT_SHAPES:
        assert shape in clause, (
            f"§Graph must state {shape!r} in the DEFAULT-graph clause — a bare "
            "whole-cell substring test cannot see this: `org_{org_id}` is also "
            f"a substring of the custom shape `{CUSTOM_SHAPE}` stated later in "
            "the same cell, so the hosted clause can vanish green")

    # The ATTRIBUTION, not just the presence of both shapes: the clause states
    # `org_{org_id}` for one set of derivers and `org_{name}` for the other, and
    # the whole point of this correction is WHICH. Swapping the two leaves both
    # shapes in the clause and both code anchors intact, so presence alone cannot
    # see it.
    supabase_half, registry_half = clause.split("`org_{name}`", 1)
    # The BACKTICKED standalone shape, not a bare substring: `org_{org_id}` is
    # also a prefix of the custom shape `org_{org_id}_{gid}`, so a bare test is
    # satisfied by the customs clause — the exact vacuity this pin exists to fix.
    assert "`org_{org_id}`" in supabase_half, (
        "the DEFAULT-graph clause must attribute `org_{org_id}` to the derivers "
        "listed before the `org_{name}` marker — `hosted_api._org_graph_name`'s "
        "fallback is that shape")
    assert "sdk.org_create" in registry_half, (
        "the clause's `org_{name}` half must name `sdk.org_create` as its ONLY "
        "deriver — no other path stores that shape")
    # WHICH axis the split runs on, pinned explicitly: `org_{org_id}` is minted
    # by `register_user` / `agent_signup` BEFORE any lane branch (they compute the
    # name unconditionally), so a hosted-vs-registry framing is FALSE and was in
    # this cell for two rounds. The derivers are functions; the deployment mode is
    # not the discriminator.
    # Each deriver must sit on ITS OWN side. A whole-clause membership test lets
    # a deriver be moved into the `org_{name}` half — attributing a mint of
    # `org_{org_id}` to the other shape — while every name is still present.
    for deriver in ("provision_tenant", "register_user", "agent_signup"):
        assert deriver in supabase_half, (
            f"the clause must name `{deriver}` in the `org_{{org_id}}` half — "
            "`hosted_api._org_graph_name`'s docstring enumerates it there, and "
            "moving it across the `org_{name}` marker attributes the wrong "
            "shape to the wrong deriver")
        assert deriver not in registry_half, (
            f"`{deriver}` must NOT appear in the `org_{{org_name}}` half — it "
            "mints `org_{org_id}`, so naming it there inverts the attribution")
    assert "the lane does" not in _flat(clause), (
        "the clause must not claim the deployment lane decides the shape: "
        "`register_user` and `agent_signup` mint `org_{org_id}` before any lane "
        "branch, and `provision_tenant` is selfhost-ONLY yet also mints it")
    assert "not by the deployment lane" in _flat(clause), (
        "the clause must state the AXIS explicitly — that the default graph's "
        "shape follows the minting path and not the deployment lane. Dropping "
        "the negation leaves 'decided by the deployment lane:' followed by a "
        "list of functions, a self-contradiction the shapes alone cannot detect")
    # Ban the whole FAMILY, not one historical spelling. The merged wording was
    # "on the hosted Supabase-lane provision paths"; a literal-phrase ban let that
    # — and "on the hosted provision paths" — walk straight back in beside the
    # "not by the deployment lane" negation, producing the self-contradiction
    # this correction exists to remove.
    # The family is "hosted … paths/lanes", NOT "hosted … provision path": a
    # guard that requires the word `provision` lets the third spelling of the
    # refuted grouping — "on the hosted Supabase-lane paths" — back in green,
    # beside the negation asserting the opposite (measured). The only legitimate
    # `hosted` mention in this clause ("reached through the hosted API") is more
    # than 60 characters from the next `path`/`lane`, so it stays clear.
    assert not re.search(r"hosted[^.]{0,60}\b(?:paths?|lanes?)\b",
                         _flat(_namespace_cell())), (
        "the cell must not group the default graph's shape by deployment "
        "(\"the hosted ... paths/lanes\"): `register_user` / `agent_signup` mint "
        "`org_{org_id}` in BOTH lanes, `provision_tenant` is selfhost-ONLY, and "
        "the registry create_org lane is hosted too — no hosted grouping "
        "decides the shape")
    # The Supabase-lane `create_org` is a deriver of `org_{org_id}`, so it must
    # sit on that side — naming it on the `org_{name}` side attributes a mint of
    # the wrong shape to it (`agent_signup` is guarded the same way above; this
    # one is a phrase because `create_org` also legitimately appears in the
    # registry half as `sdk.org_create`'s lane name).
    assert "Supabase-lane `create_org`" in _flat(supabase_half), (
        "the Supabase-lane `create_org` must be named among the `org_{org_id}` "
        "derivers (`hosted_api` mints `org_{org_id}` there); moving it across "
        "the `org_{name}` marker inverts the attribution")
    # `stored` is load-bearing and must survive: `hosted_api`'s duplicate-name
    # replay (create_org) re-derives `f"org_{name}".replace(' ', '_')` outside
    # `sdk.org_create`, so an unqualified "only on sdk.org_create" is false. What
    # is unique to `sdk.org_create` is the STORED `Team.graph_name`.
    assert re.search(r"stored only by\s+`?sdk\.org_create", _flat(clause), re.I), (
        "the clause must say `org_{name}` is STORED only by `sdk.org_create`, "
        "not derived only there: the create_org duplicate-name replay derives "
        "the same shape, so an unqualified 'only' is a false claim")
    # The registry `kind='default'` node is NOT a deriver on its own: the same
    # node KIND is written with BOTH shapes — `agent_signup`'s registry branch
    # with `org_{org_id}` (`hosted_api.py`, `namespace=graph_name`), `sdk.org_create`
    # with `org_{name}` — so listing it beside the functions hands a reader
    # resolving a registry default node two contradictory rules and no way to
    # choose, which is the silent-empty-read class this cell exists to prevent.
    flat_clause = _flat(clause)
    assert ("`agent_signup` (including the registry `kind='default'` node it "
            "writes)") in flat_clause, (
        "the clause must attribute the registry `kind='default'` node to the "
        "MINTER that writes it — `agent_signup`'s registry branch stores "
        "`org_{org_id}` there, so the node is named through the function, never "
        "as a bare deriver")
    # A literal ban on ONE bare spelling is the defect, not the guard: measured
    # on this cell, re-listing the node beside the functions with a comma
    # instead of "and" — "`agent_signup` (including the registry `kind='default'`
    # node it writes), the registry `kind='default'` node, the Supabase-lane
    # `create_org` …" — left every cell assertion GREEN, because the minter form
    # survives and the banned phrase is the "and" spelling. So bound the number
    # of MENTIONS instead, exactly as the row does below: one, the minter one.
    assert flat_clause.count("the registry `kind='default'` node") == 1, (
        "the clause must mention the registry `kind='default'` node exactly "
        "ONCE — inside the minter parenthetical, which is the only attribution "
        "that is true. A second mention lists the node as one more "
        "`org_{org_id}` deriver, and `sdk.org_create` writes that same node "
        "KIND with `org_{name}`, so the node kind is not the discriminator")
    assert "so the path, never the node kind, decides" in flat_clause, (
        "the clause must say the NODE KIND is not the discriminator — without "
        "it a reader resolving a registry default node has `org_{org_id}` from "
        "this clause and `org_{name}` from the bullet below, and nothing says "
        "which applies")
    # The `org_{name}` half's code side: the registry lane's stored org
    # `graph_name` — `sdk.org_create` derives it from the org name (spaces →
    # `_`) and the registry `kind='default'` node takes it as its namespace. The
    # cell attributes the shape to that method, so the pin must read that method
    # or it is only the doc agreeing with itself.
    sdk_src = (REPO / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    assert "graph_name = f\"org_{name}\".replace(' ', '_')" in sdk_src, (
        "`sdk.org_create` must keep deriving the registry-lane default "
        "namespace as org_{name} (spaces → `_`) — the §Graph cell states that "
        "shape for that lane, so a change here re-stales the cell")
    # The node-kind claim's CODE side — two SITES, one node KIND, two shapes. The
    # cell says the `kind='default'` node is written from each path's own
    # `graph_name`, so the node kind never decides; without these two pins that
    # claim is the doc agreeing with itself.
    assert ('self._graph_create(tid, "default", kind="default", namespace=graph_name)'
            in sdk_src), (
        "`sdk.org_create` must keep writing the registry `kind='default'` node "
        "from ITS `graph_name` (`org_{name}`) — the cell's claim that the node "
        "KIND takes both shapes rests on this site")
    hosted_src = (REPO / "tortoise" / "hosted_api.py").read_text(encoding="utf-8")
    assert ('sdk._graph_create(org_id, "default", kind="default", namespace=graph_name)'
            in hosted_src), (
        "`agent_signup`'s registry branch must keep writing that SAME "
        "`kind='default'` node kind from its own `graph_name` "
        "(`org_{org_id}`) — the other half of the cell's node-kind claim")

    assert "team_<name>" not in clause, (
        "the pre-#3543 `team_<name>` is not the default graph's namespace")
    assert "team_<name>" not in _namespace_cell(), (
        "the pre-#3543 `team_<name>` must not appear anywhere in the cell")

    # Flattened, like every other phrase assertion: this slice is hard-wrapped
    # too, so a future re-wrap that splits the clause would false-RED a raw
    # `in` test — the failure `_flat` exists to prevent.
    bullet = _flat(_default_graph_bullet())
    assert "namespace IS that stored `graph_name`" in bullet, (
        "the no-migration bullet must say the default graph's namespace IS the "
        "stored `graph_name` — a bare 'graph_name' substring is satisfied by the "
        "'supabase mode reads `teams.graph_name`' clause that opens the same "
        "bullet, so the sentence can be replaced by 'the team namespace' while "
        "this test stays green")


# ── 2. the legacy prefix is stated as legacy, and is really still recognised ─

def test_legacy_prefix_is_only_stated_as_legacy():
    """Every `team_` mention in the namespace cell is marked LEGACY.

    A reader who finds `team_` next to the derived shapes cannot tell a
    back-compat shape from a current one — which is exactly how the cell went
    stale in the first place.

    (1) *What value makes this test fail?*  A `team_` mention in the cell
        without the LEGACY marker (e.g. re-introducing `team_<org_id>_<gid>`
        as a "supported" shape).
    (2) *Does the test reach it?*  Yes — every line of the committed cell is
        scanned, not just the ones this test knows about.
    """
    offenders = [
        line.strip() for line in _namespace_cell().splitlines()
        if "team_" in line and "LEGACY" not in line
    ]
    assert offenders == [], (
        f"these namespace-cell lines mention `team_` without marking it "
        f"LEGACY: {offenders}")


def test_legacy_prefix_is_still_recognised_where_the_doc_says_it_is():
    """The doc's "still recognised" claim is anchored to EVERY live acceptance.

    The cell promises the pre-rename ``team_*`` name is "still recognised by the
    tombstone purge and the legacy graph-name resolution". That sentence covers
    two subsystems, and every acceptance inside them is pinned here — the purge's
    namespace drop AND its artifact-ownership guard (separate acceptances in the
    same purge path: one decides whether to drop the graph, the other whether the
    archive it is about to delete is provably this org's), the MCP list filter,
    and the two hosted-path acceptances (the existence PROBE and the legacy
    OPEN). The set is enumerated by SITE, not counted: a number here re-stales the
    moment a sixth acceptance is added, and the pin cannot see an addition — only
    a removal. The hosted pair is the one with the operator-visible consequence:
    probing only ``org_`` made every pre-rename org read "node absent" and sent
    the onboarding projection down the no-write path (#3543 on the probe), and the
    MCP comment records that the two list filters "must agree or a legacy org's
    graphs become invisible here alone".

    (1) *What value makes this test fail?*  Any listed acceptance dropping the
        pre-rename shape — the purge's `team_{org_id}_{gid}` namespace drop, the
        purge's `team_{org_id}_{graph_id}` artifact-ownership guard, the MCP
        resolution's `team_{org_id}`, the hosted probe's `team_{org_id}`
        alternative, or the hosted legacy open — while the doc keeps claiming
        back-compat: the reverse direction of the same drift.
    (2) *Does the test reach it?*  Yes — each acceptance is read from the
        committed source that has to contain it, so removing one reds the doc's
        claim. Every DECISION line is pinned with the literal that feeds it,
        because a literal on its own is inert: the guard can be re-prefixed, an
        `any()` inverted to `all()`, or a filter expression neutered while the
        string it reads survives elsewhere in the file. So the pins are
        - the purge's artifact guard as `ns_trusted = bool(namespace) and
          namespace in gid_namespaces` (a set can be built and never consulted),
        - the MCP filter as its `return [g for g in graphs if g in own]` line,
        - the hosted legacy open as the `if`/`return` pair,
        - the hosted probe as its `any(... for name in (…))` expression.
        The pin still cannot see an ADDITION of a further acceptance — only a
        removal or a weakened decision — which is why the docstring names the
        sites and does not claim a closed count.
    """
    source = (REPO / "tortoise" / "backup_sweep.py").read_text(encoding="utf-8")
    assert 'f"team_{org_id}_{gid}"' in source, (
        "the tombstone purge guard must keep accepting the pre-#3543 "
        "`team_{org_id}_{gid}` namespace (no migration rewrites it)")
    assert 'f"org_{org_id}_{gid}"' in source, (
        "and the current derivation must stay alongside it")
    # The ARTIFACT half of the same purge: `_purge_graph_storage` decides whether
    # the archive it is about to delete is provably this org's by testing the
    # namespace against both spellings. Dropping the legacy arm alone would let a
    # pre-rename graph's archive be deleted on a name that proves nothing, while
    # the cell keeps claiming the purge recognises `team_*`.
    assert 'f"team_{org_id}_{graph_id}"' in source, (
        "the purge's artifact-ownership guard must keep trusting the pre-#3543 "
        "`team_{org_id}_{graph_id}` namespace — it is the artifact half of the "
        "same back-compat the cell claims")
    assert 'f"org_{org_id}_{graph_id}"' in source, (
        "and the current namespace alongside it, or every post-#3543 graph's "
        "archive becomes ambiguous")
    assert "ns_trusted = bool(namespace) and namespace in gid_namespaces" in source, (
        "the artifact guard's DECISION must keep consulting `gid_namespaces` — "
        "the set existing proves nothing if the trust test stops reading it")

    mcp = (REPO / "tortoise" / "mcp_server.py").read_text(encoding="utf-8")
    assert 'f"team_{org_id}"' in mcp, (
        "the legacy graph-name resolution must keep accepting the pre-#3543 "
        "`team_{org_id}` org namespace")
    assert 'f"org_{org_id}"' in mcp, (
        "and the current org namespace alongside it")
    assert "return [g for g in graphs if g in own]" in mcp, (
        "the MCP listing must keep FILTERING by the resolved set — an "
        "unfiltered return would list every org's graphs, which is the "
        "cross-tenant exposure the surrounding comment warns about")

    hosted = (REPO / "tortoise" / "hosted_api.py").read_text(encoding="utf-8")
    assert '(f"org_{org_id}", f"team_{org_id}")' in hosted, (
        "`_graph_has_org_namespace` must keep probing BOTH prefixes: probing "
        "only `org_` reported 'node absent' for every pre-rename org, which "
        "sent the onboarding projection down the no-write FLOW-defaults path "
        "(#3543)")
    assert ("return any(name in listed\n"
            "               for name in (f\"org_{org_id}\", f\"team_{org_id}\"))") in hosted, (
        "the probe's DECISION must stay an `any` over both names — `all` would "
        "require both spellings to exist, so a healthy org would read as "
        "absent (and an org with both would read as the only present one)")
    assert 'if f"team_{org_id}" in listed:\n        return _make_sdk(graph_name=f"team_{org_id}")' in hosted, (
        "`_open_org_graph_sdk` must keep OPENING a legacy org by its pre-#3543 "
        "name: the GUARD and the argument together — a re-prefixed guard leaves "
        "the argument present but the branch dead, which reads as back-compat "
        "while opening nothing")


# ── 3. the pin itself cannot rot the way the cell did ──────────────────────

def _coarse_isolation_row() -> str:
    """The §2.1 coarse-isolation row — the second place this fact is stated."""
    authz = AUTHZ_DOC.read_text(encoding="utf-8")
    return next(
        line for line in authz.splitlines()
        if line.startswith("| **Coarse isolation**")
    )


def test_namespace_citations_are_by_symbol_never_by_line():
    """Both docs cite this fact by symbol — a `file.py:NNN` here rots silently.

    #8093 was filed off citations that had already rotted: the original evidence
    (``sdk.py:20694-20723``, ``hosted_api.py:16815``) no longer contains
    `_graph_create` / `_provision_graph` at all, so a reader auditing tenancy
    landed on unrelated code.

    (1) *What value makes this test fail?*  Re-adding a line-number citation for
        this fact to the `:Graph` cell or to the §2.1 coarse-isolation row.
    (2) *Does the test reach it?*  Yes — the pattern is applied to the committed
        slices, so a citation added anywhere in them reds.
    """
    assert _LINE_CITATION.findall(_namespace_cell()) == [], (
        "the namespace cell cites code by line number; cite the symbol instead")

    row = _coarse_isolation_row()
    assert _LINE_CITATION.findall(row) == [], (
        "the coarse-isolation row cites code by line number; cite the symbol "
        "(`_graph_create`, `_provision_graph`, `_graph_namespace`) instead")


def test_coarse_isolation_row_cites_live_migration_anchors():
    """The row's migration citations resolve, and the legacy origin is stated.

    The coarse-isolation row cites migration files as evidence, and the `graphs`
    DDL comment in ``20260901000001_graphs_and_key_scopes.sql`` still spells the
    namespace ``team_{team_id}_{gid}`` — the pre-#3543 shape. Those migration
    comments cannot be corrected: ``.github/scripts/check-migration-append-only``
    rejects a CONTENT edit of a migration present in the PR base tree (only a
    provably-unapplied pure-prefix rename is exempt), which is exactly why
    #3543 shipped as one new timestamp file that leaves the historical spelling
    untouched. So the row must also cite the file that says the spelling is
    HISTORICAL — otherwise a reader who follows the row's own citation lands on
    the stale shape with nothing marking it.

    (1) *What value makes this test fail?*  Dropping the tenancy-migration anchor
        from the row (leaving the DDL comment to imply the legacy shape is
        current), or citing a migration path that does not exist.
    (2) *Does the test reach it?*  Yes — every cited `supabase/migrations/*.sql`
        path is resolved on disk, the tenancy migration's own text is asserted to
        state that earlier migrations keep the historical spelling, and the row's
        SHAPE claim is pinned to the same two namespace shapes the §Graph cell
        states — the two docs describe one fact, so a row reverting to a single
        lane's shape while the cell states both reds here instead of passing as
        two independent prose statements. Its ATTRIBUTION and its AXIS are
        pinned as the cell's are: the pre-#9052 row ("Which lane mints the graph
        decides the shape") satisfied every presence assertion — it carried
        `org_{org_id}` before its `org_{name}` marker and named `sdk.org_create`
        after it — so presence alone left the correction revertible VERBATIM
        with this test green.
    """
    row = _coarse_isolation_row()
    for shape in DEFAULT_SHAPES:
        assert shape in row, (
            f"the §2.1 coarse-isolation row must state {shape!r} too — it is the "
            "second description of the same fact, and a row that keeps only one "
            "lane's shape contradicts the §Graph cell this pin also reads")
    # The row's ATTRIBUTION too, on the same axis as the cell: which derivers
    # produce which shape. Presence alone let a swapped attribution pass — and
    # let the WHOLE pre-#9052 row pass, so the axis is asserted next.
    row_org_id_half, row_name_half = row.split("`org_{name}`", 1)
    # Backticked standalone shape — see the cell's note: the custom shape
    # `org_{org_id}_{gid}` is also stated in this row and would satisfy a bare
    # substring test after the attribution is swapped.
    assert "`org_{org_id}`" in row_org_id_half, (
        "the row must attribute `org_{org_id}` to the derivers listed before its "
        "`org_{name}` marker")
    assert "sdk.org_create" in row_name_half, (
        "the row's `org_{name}` half must name `sdk.org_create` as that shape's "
        "only source")
    # …and `stored` is as load-bearing in the ROW as it is in the cell, for the
    # same reason: `hosted_api`'s create_org duplicate-name replay re-derives
    # `f"org_{name}".replace(' ', '_')` outside `sdk.org_create`, so an
    # unqualified "only on sdk.org_create" is false. The word was pinned on the
    # cell clause only (measured: the row rewritten to "`org_{name}` is derived
    # only by `sdk.org_create`" — the exact false claim this PR corrects — kept
    # the whole row test green). The row is a single line, so no flattening.
    assert re.search(r"stored only by\s+`?sdk\.org_create", row, re.I), (
        "the row must say `org_{name}` is STORED only by `sdk.org_create`, not "
        "derived only there: the create_org duplicate-name replay derives the "
        "same shape, so an unqualified 'only' is a false claim")
    # (`the lane does` is asserted as a negative on the CELL only. The row never
    # carried that phrase — its pre-#9052 wording is "Which lane mints the
    # graph decides the shape" — so a row-level `"the lane does" not in row`
    # fires on no version of this file: it reads as a guard and guards nothing.
    # The row's axis is carried below by assertions that actually bite.)
    # The AXIS, positively and negatively — the correction this PR makes, not
    # just the shapes it lists. `origin/main`'s row states "Which lane mints the
    # graph decides the shape, not whether the deployment is hosted" and passes
    # everything above, so the row could be reverted verbatim, green.
    for deriver in ("provision_tenant", "register_user", "agent_signup"):
        assert deriver in row_org_id_half, (
            f"the row must name `{deriver}` among its `org_{{org_id}}` derivers "
            "— the same set the §Graph cell enumerates, and the set the row "
            "itself now names; a lane-based split drops these")
    assert "minting path" in row.lower(), (
        "the row must state the axis POSITIVELY — the default graph's shape "
        "follows the minting path — so a revert to the lane framing cannot pass "
        "on the shape list alone")
    assert "not the deployment lane" in row, (
        "and the negation, as the cell states it: the deployment lane is not the "
        "discriminator (`register_user` / `agent_signup` mint `org_{org_id}` "
        "before any lane branch, `provision_tenant` is selfhost-ONLY yet mints "
        "it too)")
    assert "Which lane mints" not in row, (
        "the row must not carry the literal pre-#9052 claim that the lane that "
        "mints decides the shape — that is the framing this PR corrects")
    # The node-kind attribution, POSITIVELY first: the row must name the node
    # through its MINTER, exactly as the cell does. The negative alone was
    # inert — the identifier both docs use is "the registry `kind='default'`
    # node", which does not contain the literal "the registry default node", so
    # a bare-deriver regression (`... `agent_signup`, the registry `kind='default'`
    # node; `org_{name}` only from `sdk.org_create` ...`) passed it green while
    # dropping the parenthetical. So require the minter form, then bound the
    # number of node mentions: one, the minter one. (A literal ban on a
    # particular bare spelling is what failed before — a reworded bare listing
    # slips past it, the same defect the authz-note pin's docstring names.)
    minter_ref = ("`agent_signup` (including the registry `kind='default'` node "
                  "it writes)")
    assert minter_ref in row_org_id_half, (
        "the row must attribute the registry `kind='default'` node to the "
        "MINTER that writes it — `agent_signup`'s registry branch stores "
        "`org_{org_id}` there — not list it as one more `org_{org_id}` deriver: "
        "that node KIND is written with BOTH shapes (`agent_signup` with "
        "`org_{org_id}`, `sdk.org_create` with `org_{name}`), so the artifact "
        "alone does not decide the shape")
    assert row.count("the registry `kind='default'` node") == 1, (
        "and it must be the row's ONLY mention of the registry "
        "`kind='default'` node — a second, bare mention lists it as one more "
        "`org_{org_id}` deriver, and `sdk.org_create` writes that same node "
        "KIND with `org_{name}`, so the node kind is not the discriminator")
    cited = re.findall(r"supabase/migrations/[\w.\-]+\.sql", row)
    assert cited, (
        "the coarse-isolation row must keep citing the `graphs` table's own "
        "migration as evidence — do not drop it without a live replacement")
    for path in cited:
        assert (REPO / path).is_file(), (
            f"the coarse-isolation row cites {path!r}, which does not exist — "
            "a dead citation is the #8093 rot class")

    tenancy = "supabase/migrations/20260915000001_tenancy_team_to_org.sql"
    assert tenancy in cited, (
        "the row must cite the #3543 tenancy migration: it is the live anchor "
        "that says the `team_` spelling in earlier migrations is historical "
        "(their DDL comments still read `team_{team_id}_{gid}` and cannot be "
        "edited — check-migration-append-only rejects a content edit)")
    text = (REPO / tenancy).read_text(encoding="utf-8")
    assert "historical `team` spelling" in text, (
        "the cited migration must itself state that earlier migrations keep "
        "their historical `team` spelling — otherwise the citation does not "
        "disambiguate the stale DDL comments it is cited for")


def test_authz_doc_records_the_finding_as_fixed():
    """The #2082 audit note must not keep claiming the schema doc is stale.

    (1) *What value makes this test fail?*  The note reverting to a filed/stale
        framing ("still states `team_<org_id>_<gid>`", "Filed as #8093") now
        that the cell is fixed — a doc asserting a defect that no longer exists.
    (2) *Does the test reach it?*  Yes — the note is located by its #8093
        mention, required to carry the `(fixed)` marker, and required to point
        at the pin. Banning one literal phrase is not enough: a reworded stale
        note ("still documents … as `team_<org_id>_<gid>`") would slip past it.
    """
    authz = AUTHZ_DOC.read_text(encoding="utf-8")
    note = next(
        line for line in authz.splitlines()
        if "#8093" in line and line.startswith(">")
    )
    assert "Audit finding (fixed)" in note, (
        "the note must be headed as a FIXED finding — anything else is the "
        "filed/stale framing this test exists to reject")
    assert "Filed as" not in note, (
        "the note claims the defect was filed rather than fixed")
    assert "still states" not in note, (
        "the note claims the §Graph cell is still stale — it is corrected here")
    assert "test_8093_registry_namespace_doc.py" in note, (
        "the note must point at the pin that keeps the cell honest")
