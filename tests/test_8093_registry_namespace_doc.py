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
        ATTRIBUTION is pinned too, not just the presence of both shapes: the
        clause is split at its `org_{name}` marker, and each half must name the
        lane it belongs to. Both shapes present is not enough — swapping which
        lane derives which leaves the shapes intact, and the lane split is the
        substance of this correction.
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
    # `org_{org_id}` for one lane and `org_{name}` for the other, and the whole
    # point of this correction is WHICH. Swapping the two leaves both shapes in
    # the clause and both code anchors intact, so presence alone cannot see it.
    supabase_half, registry_half = clause.split("`org_{name}`", 1)
    assert "org_{org_id}" in supabase_half, (
        "the DEFAULT-graph clause must attribute `org_{org_id}` to the "
        "Supabase-lane provision paths — it is stated BEFORE the `org_{name}` "
        "marker, and `hosted_api._org_graph_name`'s fallback is that shape")
    assert "sdk.org_create" in registry_half, (
        "the clause's `org_{name}` half must name the registry lane's "
        "`sdk.org_create` as the deriver — 'hosted' does not decide the shape "
        "(that method IS reached through the hosted API), the lane does")
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

    assert "team_<name>" not in clause, (
        "the pre-#3543 `team_<name>` is not the default graph's namespace")
    assert "team_<name>" not in _namespace_cell(), (
        "the pre-#3543 `team_<name>` must not appear anywhere in the cell")

    bullet = _default_graph_bullet()
    assert "graph_name" in bullet and "org_" in bullet, (
        "the no-migration bullet must name the stored graph_name it derives — "
        "a bare 'the team namespace' cannot be resolved to a DB graph name")


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
        two independent prose statements.
    """
    row = _coarse_isolation_row()
    for shape in DEFAULT_SHAPES:
        assert shape in row, (
            f"the §2.1 coarse-isolation row must state {shape!r} too — it is the "
            "second description of the same fact, and a row that keeps only one "
            "lane's shape contradicts the §Graph cell this pin also reads")
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

    (1) *What value makes this test fail?*  The note reverting to "still states
        `team_<org_id>_<gid>`" / "Filed as #8093" now that the cell is fixed —
        a doc asserting a defect that no longer exists.
    (2) *Does the test reach it?*  Yes — the note is located by its #8093
        mention and asserted to point at the pin.
    """
    authz = AUTHZ_DOC.read_text(encoding="utf-8")
    note = next(
        line for line in authz.splitlines()
        if "#8093" in line and line.startswith(">")
    )
    assert "still states" not in note, (
        "the note claims the §Graph cell is still stale — it is corrected here")
    assert "test_8093_registry_namespace_doc.py" in note, (
        "the note must point at the pin that keeps the cell honest")
