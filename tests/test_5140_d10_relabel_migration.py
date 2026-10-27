"""#5140 finding 8 — the ``:Document`` → ``:Source`` relabel migration.

D10 (ONTOLOGY v3.15 §4.4) retired the ``:Document`` label, but it shipped **no
relabel**: every graph written before it still holds ``(:Document {id, …})``
while every D10 reader and migration addresses ``(:Source {url, …})``. Two
consequences, both closed here and both reproduced below:

1. **``migrate_kinds`` could not see the population it migrates.** Its
   ``document`` rows derive ``MATCH (n:Source {documentKind: $old})`` from
   ``ENTITY_LABELS``, so on an un-relabelled graph they count 0 on exactly the
   nodes they exist to re-type.
2. **The pre-D10 extraction backlog was undiscoverable** (#5140 finding 3's
   discovery half). ``ingest._do_upgrade`` MATCHes ``(:Source {url})`` and
   ``ingest._discover_pending_documents`` MATCHes a bare ``:Source`` with
   ``documentKind IS NOT NULL``, so the ``doc_status='captured'`` arm they carry
   *for this population* can never fire while the node is a ``:Document``.

Each test names the failure it prevents; the refusal tests exist because the
tempting fix (relabel everything, date everything) writes a guess where the
graph does not have a fact.
"""
from __future__ import annotations

import contextlib
import os
import sys

import pytest

from tests import _live_utils

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.migrate_kinds import (  # noqa: E402, RUF100
    _do_migrate,
    relabel_legacy_documents,
)
from tortoise.projection import FalkorProjection  # noqa: E402, RUF100
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100

FALKORDB_AVAILABLE = False
try:
    _probe_uri = os.environ.get("TORTOISE_DB_URI") or _live_utils.legacy_uri(
        "tortoise_test_5140_relabel")
    # `graph_name=` is passed explicitly (#10164, the new-test-file registration
    # rule): the probe only constructs and closes, so it can never DETACH, and
    # naming its graph keeps the census (`test_derived_names`
    # `test_from_uri_sites_resolve_test_prefixed`) honest without a declaration
    # that would have to be re-cut when this probe changes shape.
    _probe = FalkorProjection.from_uri(
        _probe_uri, graph_name="tortoise_test_5140_relabel_probe")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False

_live_db = pytest.mark.skipif(not FALKORDB_AVAILABLE,
                             reason="Live FalkorDB (Docker) not available")

#: The pre-D10 spelling in the fields these cases DEPEND on (read off
#: `294d5847e^:tortoise/projection/entities.py`): keyed `id`, no `url`, no
#: `sourceKind`/`version`/`ingestedAt`, the retired `doc_status` marker the
#: deleted `backfill_doc_status.py` stamped, and the node's own `updatedAt` (the
#: clock `ingestedAt` is derived from). It is NOT the whole node: the real writer
#: also set `format`/`topics`/`summary`/`sessionId`/`eventId`, which the relabel
#: must preserve and this seed simply does not exercise.
_LEGACY_PROPS = (
    "id:'doc/pre-d10.md', title:'Pre D10', documentKind:'competitiveAnalysis', "
    "doc_status:'captured', needs_extraction:false, "
    "sourcePath:'/corpus/pre-d10.md', updatedAt:'2026-02-03T04:05:06Z'"
)


def _live_uri(test_graph: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    env = os.environ.get("TORTOISE_DB_URI")
    if env:
        parts = urlsplit(env)
        return urlunsplit((parts.scheme, parts.netloc, f"/{test_graph}", "", ""))
    return _live_utils.legacy_uri(test_graph)


@pytest.fixture()
def graph(monkeypatch):
    """A private test graph + the SDK whose migration a caller would run."""
    if not FALKORDB_AVAILABLE:
        pytest.skip("live FalkorDB not reachable")
    name = f"test_5140_relabel_{os.urandom(4).hex()}"
    uri = _live_uri(name)
    # ⛔ TWO HANDLES, ONE DATABASE (#10164). `proj` seeds the node and reads it
    # back; `sdk` is the object `relabel_legacy_documents` runs the migration
    # through, and it does NOT read `_live_uri()` — it resolves its target from
    # `TORTOISE_DB_URI` (`tortoise/sdk.py:7481`) and falls back to the EMBEDDED
    # path when that is unset (`:7502` → `resolve_db_path()`). On a URI-less
    # lane — every tier-2 PR leg — those were two DIFFERENT databases, so the
    # seed landed in the provisioned service, the migration ran against an
    # empty embedded graph, and 19 cases here reported "the relabel did
    # nothing" on a migration that is green.
    #
    # The fix is the shape #10164 names as acceptable — make the SDK FOLLOW the
    # URI rather than inventing a parallel one (same as `tests/test_ingest.py`).
    # This keeps the coverage on BOTH lanes; a skip would not.
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    proj = FalkorProjection.from_uri(uri, graph_name=name)
    proj.g.query("MATCH (n) DETACH DELETE n")
    sdk = TortoiseSDK(graph_name=name)
    try:
        yield proj, sdk
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.close()


def _row(proj, cypher, **params):
    rows = proj.g.query(cypher, params=params or None).result_set
    return rows


@_live_db
def test_relabel_writes_the_d10_shape_of_a_document_source(graph):
    """#5140 f8 — the fields a relocated node must carry, and where each comes from.

    FAIL-ON: the node is relabelled but stays short of §4.6 — no
    ``sourceKind``/``version``/``ingestedAt``, no ``_searchText`` (invisible to
    the FTS leg), or no ``canonicalUrl`` (invisible to the S0a/S0b resolver, so
    the same identity in another spelling mints a SECOND ``:Source`` — #5140
    f12 all over again). Mutation: drop any one item from the ``SET`` list.
    """
    proj, sdk = graph
    proj.g.query(f"CREATE (d:Document {{{_LEGACY_PROPS}}})")

    relabel_legacy_documents(sdk)

    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 0, (
        "the retired :Document label must be gone — a relabel that leaves it "
        "behind leaves the node outside every D10 reader AND every index")
    rows = _row(proj, "MATCH (s:Source {url: 'doc/pre-d10.md'}) "
                      "RETURN s.id, s.documentKind, s.sourceKind, s.version, "
                      "s.ingestedAt, s._searchText, s.canonicalUrl, "
                      "s.urlAliases, s.doc_status")
    assert rows, "no :Source was minted at the document's own id"
    (sid, dk, sk, version, ingested, st, cu, aliases, doc_status) = rows[0]
    assert sid == "doc/pre-d10.md", "the pre-D10 `id` is kept (§4.6 keeps it)"
    assert dk == "competitiveAnalysis", "the genre is preserved, never invented"
    assert sk == "document", "§4.6 REQUIRED sourceKind (the #5082 TYPE axis)"
    assert version == 1, "§4.6: `version` is 1 at creation"
    assert ingested == "2026-02-03T04:05:06Z", (
        "ingestedAt is DERIVED from the node's own clock, not from the "
        "migration's `now` (which would be a later fact than the ingest)")
    assert st == "Pre D10", "the FTS field falls back to title, as the fold does"
    assert cu == "doc/pre-d10.md" and aliases == ["doc/pre-d10.md"], (
        "S0a/S0b parity — without canonicalUrl the resolver cannot see the "
        "node, so a variant spelling would mint a SECOND :Source (#5140 f12)")
    assert doc_status == "captured", (
        "retirement forbids WRITING a retired field; it does not license "
        "DELETING stored history")


@_live_db
def test_the_relabel_reports_how_much_of_the_instant_it_had_to_derive(graph):
    """``ingested_at_derived`` is part of the return contract and a print line.

    An operator reading "ingestedAt derived from the node clock on N" is being
    told how much of the migrated population has a §4.6 ``ingestedAt`` that came
    from a CREATION timestamp rather than a true ingest instant — a fact to
    audit, and the only way to notice the ``undated`` arm is about to fire on the
    rest of a corpus.

    FAIL-ON: the counter stays 0 while a derived instant was written, so the
    report and the graph disagree about which nodes need auditing. Mutation:
    drop the ``report["ingested_at_derived"] += 1``.
    """
    proj, sdk = graph
    # One node with a real ingest instant, one that has to derive from `updatedAt`.
    proj.g.query(
        "CREATE (a:Document {id:'doc/exact.md', title:'Exact', "
        "documentKind:'report', ingestedAt:'2026-01-01T00:00:00Z'})")
    proj.g.query(
        "CREATE (b:Document {id:'doc/derived.md', title:'Derived', "
        "documentKind:'report', updatedAt:'2026-02-03T04:05:06Z'})")

    report = relabel_legacy_documents(sdk)

    assert len(report["relabelled"]) == 2
    assert report["ingested_at_derived"] == 1, (
        "exactly the one node whose instant had to be derived — the report is "
        "the audit list, so an undercount hides work a reader must review")


def test_the_migration_runs_the_relabel_first(graph):
    """The WIRING: ``_do_migrate`` relabels before its kind rows, by itself.

    A caller that runs the migration (the operator entry point, ``migrate``)
    never calls ``relabel_legacy_documents`` directly, so a working function
    that the migration does not invoke is the defect intact. FAIL-ON: the
    ``document`` kind row counts 0 on a graph whose only document node is a
    pre-D10 ``:Document``. Mutation: delete the ``relabel_legacy_documents``
    call from ``_do_migrate``.
    """
    proj, sdk = graph
    proj.g.query(f"CREATE (d:Document {{{_LEGACY_PROPS}}})")

    results = _do_migrate(sdk, dry_run=False)

    assert results["competitiveAnalysis"]["count"] == 1, (
        "the `document` kind row must count the relocated node — before the "
        "relabel it counted 0 on exactly the population it migrates")
    assert _row(proj, "MATCH (s:Source {url: 'doc/pre-d10.md'}) "
                      "RETURN s.documentKind")[0][0] == \
        "product-strategy:competitiveAnalysis", (
            "the relocated node must also be RE-TYPED, which needs both halves"
            " in one run")


@_live_db
def test_two_documents_that_normalise_onto_one_identity_are_not_both_taken(graph):
    """The incumbent set must include the identities THIS RUN installs.

    A pre-D10 graph can hold two `:Document` nodes whose ids are two spellings
    of one identity. The map of occupied identities is built once, before the
    loop, so without registering each write the second candidate sees nothing
    occupying its canonical key — and the migration produces, silently, exactly
    the duplicate its collision arm exists to refuse (`collision: []`, two
    `:Source` nodes, one canonical identity, one node's provenance unreachable).

    FAIL-ON: both are relabelled and `collision` is empty. Mutation: remove the
    `occupied.setdefault(canonical, …)` after the write.
    """
    proj, sdk = graph
    proj.g.query(
        "CREATE (d:Document {id:'https://example.com/a/b/', "
        "title:'First', documentKind:'report', "
        "updatedAt:'2026-02-03T04:05:06Z'})")
    proj.g.query(
        "CREATE (d:Document {id:'https://Example.COM/a/b?utm_source=x#frag', "
        "title:'Second', documentKind:'report', "
        "updatedAt:'2026-02-04T04:05:06Z'})")

    report = relabel_legacy_documents(sdk)

    # The graph does NOT say which of two pre-D10 duplicates is the real
    # document, so the load-bearing assertions are the SPLIT (exactly one taken,
    # exactly one refused, the refusal naming the winner). The specific winner is
    # then pinned too, because `candidates` orders by `ID(n)`: that is a
    # deliberate determinism guarantee (a re-run picks the same node), not an
    # engine scan order.
    assert len(report["relabelled"]) == 1, (
        "exactly ONE of two spellings of one identity may be taken")
    assert len(report["collision"]) == 1
    assert report["relabelled"][0] not in [
        c["url"] for c in report["collision"]]
    assert report["collision"][0]["incumbent"] == report["relabelled"][0]
    assert report["relabelled"] == ["https://example.com/a/b/"]
    assert _row(proj, "MATCH (s:Source) RETURN count(s)")[0][0] == 1
    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 1, (
        "the refused node is left in place — recoverable by the operator")


@_live_db
def test_a_dry_run_previews_the_kind_work_on_the_legacy_population(graph):
    """The preview must not under-report what the real run will do.

    A dry run relabels nothing (that is what `dry_run` means), so the kind rows'
    `MATCH (n:Source {documentKind: $old})` still sees 0 while the population sits
    under `:Document` — and the operator, whose whole reason for a dry run is to
    decide whether to run the write, is told there is no kind work to do on
    exactly the nodes the two-pass order exists for.

    FAIL-ON: the `document` row reports 0 in the preview and 1 on the real run.
    Mutation: drop the `entity_type == "document"` dry-run branch in
    `_do_migrate`, so the preview counts only the `:Source` label.
    """
    proj, sdk = graph
    proj.g.query(f"CREATE (d:Document {{{_LEGACY_PROPS}}})")

    preview = _do_migrate(sdk, dry_run=True)

    assert preview["competitiveAnalysis"]["count"] == 1, (
        "the dry run must count what the real run will re-type")
    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 1, (
        "…and must still have written nothing")
    assert _do_migrate(sdk, dry_run=False)["competitiveAnalysis"]["count"] == 1, (
        "preview and real run must AGREE — that agreement is the assertion")


@_live_db
def test_the_dry_run_preview_counts_a_half_moved_node_exactly_once(graph):
    """The two preview arms overlap; a half-moved node is in BOTH.

    The preview is `count(:Source {documentKind: $old}) + relabel["kinds"][$old]`
    — nodes already under the label PLUS nodes this pass would relocate. Those
    sets are not naturally disjoint: a candidate is ANY `:Document`, including
    one that already carries `:Source` from an interrupted older attempt. A
    url-less one is counted by the `:Source` arm (it has the label) AND in
    `kinds` (it is relabelled), so the operator is told "2 nodes would update"
    about 1 node, and the real run — which touches that node once — prints 1.
    The preview and the write disagree, in the direction that makes an operator
    expect work the run does not do.

    FAIL-ON: preview 2 (or 3 with a plain `:Document` beside it) where the real
    run is 2. Mutation: drop the `NOT (n:Document AND n.url IS NULL)` guard
    from the dry-run `:Source` count.
    """
    proj, sdk = graph
    # 1. Both labels, url-LESS → in `kinds` (a candidate) AND in the `:Source`
    #    arm (it has the label). The shape that used to count twice.
    proj.g.query(
        "CREATE (n:Document:Source {id:'doc/half.md', title:'Half', "
        "documentKind:'competitiveAnalysis', doc_status:'captured', "
        "updatedAt:'2026-02-03T04:05:06Z'})")
    # 2. Both labels, url SET → same overlap, the OTHER mutating shape.
    proj.g.query(
        "CREATE (o:Document:Source {id:'doc/half2.md', url:'doc/half2.md', "
        "title:'Half2', documentKind:'competitiveAnalysis', "
        "updatedAt:'2026-02-03T04:05:06Z'})")
    # 3. Both labels, url-less, and REFUSED (no clock) → in neither `kinds` nor
    #    a `NOT (:Document AND url IS NULL)`-guarded `:Source` arm, yet the real
    #    run DOES re-type it (it carries `:Source`, so the kind row matches). A
    #    guard cannot see this one, which is why the preview must not use one.
    proj.g.query(
        "CREATE (p:Document:Source {id:'doc/half3.md', title:'Half3', "
        "documentKind:'competitiveAnalysis'})")
    # 4. Plain legacy node → a candidate, no `:Source` label.
    proj.g.query(f"CREATE (d:Document {{{_LEGACY_PROPS}}})")

    preview = _do_migrate(sdk, dry_run=True)
    real = _do_migrate(sdk, dry_run=False)

    assert preview["competitiveAnalysis"]["count"] == 4, (
        "4 distinct :Source nodes carry the kind after the run, so the preview "
        "says 4 — not 5 (url-set double count) and not 3 (url-less refusal "
        "under count)")
    assert real["competitiveAnalysis"]["count"] == 4, (
        "preview and real run must AGREE — that agreement is the assertion")


@_live_db
def test_a_dry_run_does_not_preview_kind_work_a_refused_node_blocks(graph):
    """The preview must not promise kind work the real run will not do.

    Counting every `:Document` node over-reports: a node the relabel REFUSES
    (`undated` here) stays `:Document` and is never re-typed, so the preview
    would say "1 nodes would update" while the same preview's own relabel line
    says "1 REFUSED" — two lines of one report disagreeing, and the operator's
    decision input wrong in the opposite direction from the under-report above.

    FAIL-ON: the preview counts the refused node and the real run updates 0.
    Mutation: count `(n:Source OR n:Document)` instead of `:Source` plus the
    relabel's own `kinds` tally.
    """
    proj, sdk = graph
    # The `_event_plain_merge` shape: a document with NO clock at all.
    proj.g.query("CREATE (d:Document {id:'doc/undated.md', "
                 "documentKind:'competitiveAnalysis'})")

    preview = _do_migrate(sdk, dry_run=True)
    real = _do_migrate(sdk, dry_run=False)

    assert "competitiveAnalysis" in preview, (
        "a dry run REPORTS every row, including a zero — omitting it would "
        "make 'no work here' indistinguishable from 'row never computed'")
    assert preview["competitiveAnalysis"]["count"] == 0, (
        "a refused node is never re-typed — the preview must not count it")
    assert "competitiveAnalysis" not in real


@_live_db
def test_relabel_makes_the_pre_d10_backlog_discoverable(graph):
    """#5140 f3's discovery half — what the relabel is FOR downstream.

    FAIL-ON: the sweep still returns ``[]`` after the migration, i.e. the
    ``doc_status='captured'`` compatibility arm is reachable in principle and
    dead in practice. Mutation: have the relabel DROP the retired
    ``doc_status`` (the "tidy up retired fields" instinct) — the arm then has
    nothing to read and the backlog stays invisible.
    """
    from tortoise.ingest import _discover_pending_documents

    proj, sdk = graph
    proj.g.query(f"CREATE (d:Document {{{_LEGACY_PROPS}}})")
    assert _discover_pending_documents(proj) == [], (
        "precondition: a :Document-labelled capture is invisible to the sweep")
    assert _discover_pending_documents(proj, include_unstamped=True) == [], (
        "and --include-unstamped widens only the no-signal ARM — it must stay "
        "bounded by the label, or the widening would reach the pre-D10 "
        "population before the relabel it needs. Pinned here because the "
        "boundary test that used to hold this assertion was retired with the "
        "relabel.")

    relabel_legacy_documents(sdk)

    found = _discover_pending_documents(proj)
    assert [r[0] for r in found] == ["doc/pre-d10.md"], (
        "the relabelled pre-D10 capture must now be selected by the sweep — "
        "this is the whole point of finding 8 for finding 3; if it is not, the "
        "marker the arm reads was lost in the move")


@_live_db
def test_relabel_is_idempotent_and_never_moves_the_derived_instant(graph):
    """Re-running must be a no-op, not a re-derivation.

    FAIL-ON: a second run reports work, or the second run's write moves
    ``ingestedAt`` (a derived value that must stay at the node's own first
    clock). Mutation: drop ``REMOVE n:Document`` — the node keeps both labels and
    a re-run relabels it AGAIN, which is exactly what the second assertion sees.
    """
    proj, sdk = graph
    proj.g.query(f"CREATE (d:Document {{{_LEGACY_PROPS}}})")

    first = relabel_legacy_documents(sdk)
    second = relabel_legacy_documents(sdk)

    assert first["relabelled"] == ["doc/pre-d10.md"]
    assert second["relabelled"] == [], "a re-run must find nothing to do"
    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 0, (
        "the label was REMOVEd, not merely shadowed by :Source")
    assert _row(proj, "MATCH (s:Source {url: 'doc/pre-d10.md'}) "
                      "RETURN count(s)")[0][0] == 1, "no duplicate node"
    assert _row(proj, "MATCH (s:Source {url: 'doc/pre-d10.md'}) "
                      "RETURN s.ingestedAt")[0][0] == "2026-02-03T04:05:06Z"


@_live_db
def test_a_url_a_source_already_holds_is_refused_not_merged(graph):
    """A collision is an operator decision, not a migration's.

    FAIL-ON: two ``:Source`` nodes end up sharing one ``url`` (the identity key
    every reader MATCHes on) — either the graph is corrupted or one node's
    edges are silently abandoned. Which node keeps them is not derivable from
    the graph, so the relabel must REFUSE. Mutation: drop the collision check.
    """
    proj, sdk = graph
    proj.g.query(f"CREATE (d:Document {{{_LEGACY_PROPS}}})")
    proj.g.query("CREATE (s:Source {url:'doc/pre-d10.md', sourceKind:'web', "
                 "title:'Already here'})")

    report = relabel_legacy_documents(sdk)

    assert [c["url"] for c in report["collision"]] == ["doc/pre-d10.md"]
    assert report["relabelled"] == []
    assert _row(proj, "MATCH (s:Source {url: 'doc/pre-d10.md'}) "
                      "RETURN count(s)")[0][0] == 1, "no second node on one key"
    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 1, (
        "a refused node is LEFT IN PLACE — invisible is recoverable, deleted "
        "is not")
    assert _row(proj, "MATCH (s:Source {url: 'doc/pre-d10.md'}) "
                      "RETURN s.sourceKind")[0][0] == "web", (
        "the incumbent source is untouched — the relabel does not overwrite "
        "another writer's node")


@_live_db
@pytest.mark.parametrize("incumbent_url", ["https://example.com/a/b",
                                            "https://example.com/a/b/"],
                         ids=["canonical-url-registered",
                              "pre-5012-stored-under-a-spelling-of-its-own"])
def test_a_variant_spelling_of_one_identity_is_also_a_collision(graph,
                                                              incumbent_url):
    """Identity is the RESOLVED key, so an exact-url check is not enough.

    Two incumbents. The second is the population BOTH hand-rolled reads miss: a
    `:Source` written before #5012 is stored under a spelling of its own (here a
    trailing slash) with `canonicalUrl IS NULL`, so neither a `{url: …}` match
    nor a `canonicalUrl`/`urlAliases` arm fires — yet `normalize_source_url`
    puts it on the SAME identity as the document's id. Relabelling onto it
    leaves TWO `:Source` nodes for one identity (the duplicate S0a/S0b #5012/
    #5053 and #5140 finding 12 exist to prevent), and the resolver's own fast
    path then prefers the migrated node, stranding the incumbent's
    `extractedFrom`/provenance edges. Collision detection therefore goes through
    the ONE normaliser, over every stored spelling.

    FAIL-ON: `relabelled` is non-empty and the graph holds two `:Source` nodes on
    one canonical identity. Mutation: read incumbents with the pre-fix
    `url = $u OR canonicalUrl = $cu OR $cu IN urlAliases` arms.
    """
    proj, sdk = graph
    proj.g.query(
        "CREATE (d:Document {id:'https://Example.COM/a/b/?utm_source=x#frag', "
        "title:'Variant', documentKind:'report', "
        "updatedAt:'2026-02-03T04:05:06Z'})")
    proj.g.query(
        "CREATE (s:Source {url:$u, sourceKind:'document'})",
        params={"u": incumbent_url})

    report = relabel_legacy_documents(sdk)

    assert report["relabelled"] == [], (
        "the variant must be REFUSED — two nodes for one canonical identity is "
        "the duplicate the resolver exists to prevent, not a migration win")
    assert [c["url"] for c in report["collision"]] == [
        "https://Example.COM/a/b/?utm_source=x#frag"]
    assert _row(proj, "MATCH (s:Source) RETURN count(s)")[0][0] == 1


@_live_db
def test_a_node_with_neither_id_nor_url_is_refused_not_relabelled_keyless(graph):
    """``unidentified`` is the refusal that stops an unkeyed ``:Source``.

    A node with no ``id`` and no ``url`` has nothing to key §4.6's identity on.
    Relabelling it anyway would mint a ``:Source`` whose ``url`` is NULL — a node
    that is inside the label every reader addresses and reachable by none of
    them (``MATCH (s:Source {url: …})`` cannot match a NULL), i.e. strictly worse
    than leaving it visibly stranded as a ``:Document``.

    FAIL-ON: the node is relabelled and the graph holds a url-less ``:Source``.
    Mutation: drop the ``unidentified`` guard.
    """
    proj, sdk = graph
    proj.g.query("CREATE (d:Document {documentKind:'report', "
                 "updatedAt:'2026-02-03T04:05:06Z'})")

    report = relabel_legacy_documents(sdk)

    assert len(report["unidentified"]) == 1
    assert report["unidentified"][0]["id"] is None
    assert report["relabelled"] == []
    assert _row(proj, "MATCH (s:Source) RETURN count(s)")[0][0] == 0, (
        "an identity-less node must not become a keyless :Source")
    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 1, (
        "…and stays where the operator can see it, as a :Document")


@_live_db
@pytest.mark.parametrize("incumbent_props", [
    "{url:'https://example.com/other', canonicalUrl:'https://example.com/a/b'}",
    "{url:'https://example.com/other', "
    " urlAliases:['https://example.com/a/b']}",
], ids=["canonicalUrl-arm", "urlAliases-arm"])
def test_an_incumbent_holding_the_identity_as_an_alias_is_also_a_collision(
        graph, incumbent_props):
    """The incumbent read is over EVERY stored spelling, not just ``url``.

    A ``:Source`` written by ``resolve_source_key`` can hold the identity on its
    ``canonicalUrl`` or inside ``urlAliases`` while its ``url`` is a DIFFERENT
    spelling (``resolve_source_key`` writes exactly this: ``canonicalUrl=$cu``
    plus the spelling into ``urlAliases``). A ``{url: …}``-only read misses it,
    relabels onto the same identity, and the graph holds two ``:Source`` nodes a
    resolver will disagree about — the duplicate this arm exists to refuse.

    FAIL-ON: the document is relabelled beside the incumbent. Mutation: read
    incumbents with ``MATCH (s:Source) RETURN ID(s), s.url`` only.
    """
    proj, sdk = graph
    proj.g.query(
        "CREATE (d:Document {id:'https://example.com/a/b', title:'Doc', "
        "documentKind:'report', updatedAt:'2026-02-03T04:05:06Z'})")
    proj.g.query(f"CREATE (s:Source {incumbent_props})")

    report = relabel_legacy_documents(sdk)

    assert report["relabelled"] == [], (
        "an incumbent holding the identity as canonicalUrl/alias already owns "
        "it — two owners is the duplicate, not a migration win")
    assert len(report["collision"]) == 1
    assert _row(proj, "MATCH (s:Source) RETURN count(s)")[0][0] == 1


@_live_db
def test_a_stray_url_prop_is_keyed_on_the_id_and_preserved_as_an_alias(graph):
    """A legacy ``:Document``'s identity is its ``id``, never a stray ``url``.

    ``url`` was absent from the pre-D10 ``_DOCUMENT_HANDLED`` set, so
    ``_persist_extra_props`` stored a caller-supplied ``url`` from the
    ``DocumentCreated`` payload as a NODE PROPERTY — a spelling that is not the
    document's identity. Keying the ``:Source`` on it moves the node OFF the key
    a later ``DocumentCreated`` folds onto (``_upsert_document`` MERGEs
    ``{url: $id}``), so one document becomes two ``:Source`` nodes. Keying on the
    ``id`` and keeping the stray spelling as a ``urlAliases`` entry preserves
    what the caller wrote AND the identity the resolver looks up.

    FAIL-ON: the ``:Source`` is minted at the stray url, and a later
    ``DocumentCreated`` for ``doc/stray.md`` then folds a SECOND ``:Source`` —
    the duplicate class this function's collision arm exists to prevent.
    Mutation: ``url = stored_url or raw_id``.
    """
    proj, sdk = graph
    proj.g.query(
        "CREATE (d:Document {id:'doc/stray.md', "
        "url:'https://example.com/article', title:'Stray', "
        "documentKind:'report', updatedAt:'2026-02-03T04:05:06Z'})")

    report = relabel_legacy_documents(sdk)

    assert report["relabelled"] == ["doc/stray.md"], (
        "the identity is the pre-D10 key, not the stray prop")
    assert [a.get("kept_as") for a in report["ambiguous"]] == ["urlAlias"], (
        "the stray spelling is reported, not silently dropped")
    row = _row(proj, "MATCH (s:Source {url: 'doc/stray.md'}) "
                     "RETURN s.id, s.urlAliases")[0]
    assert row[0] == "doc/stray.md"
    assert "https://example.com/article" in row[1], (
        "the caller-written spelling survives as an alias, so the resolver can "
        "still find this node by it")
    assert _row(proj, "MATCH (s:Source) RETURN count(s)")[0][0] == 1


@_live_db
def test_a_stray_url_the_document_does_not_own_is_refused_not_aliased(graph):
    """An alias must not give one spelling two owners.

    The same stray ``url``, but that spelling already belongs to a different
    ``:Source``. Folding it in as an alias would make two nodes answer a lookup
    for it — the duplicate a step later instead of immediately. Refusing and
    naming the incumbent leaves the operator the decision the graph cannot make.

    FAIL-ON: the node is relabelled and two ``:Source`` nodes claim
    ``https://example.com/article``. Mutation: alias the stray url without
    checking ``occupied``.
    """
    proj, sdk = graph
    proj.g.query(
        "CREATE (d:Document {id:'doc/stray.md', "
        "url:'https://example.com/article', title:'Stray', "
        "documentKind:'report', updatedAt:'2026-02-03T04:05:06Z'})")
    proj.g.query("CREATE (s:Source {url:'https://example.com/article', "
                 "sourceKind:'document'})")

    report = relabel_legacy_documents(sdk)

    assert report["relabelled"] == []
    assert len(report["ambiguous"]) == 1
    assert report["ambiguous"][0]["incumbent"] == "https://example.com/article"
    assert _row(proj, "MATCH (s:Source) RETURN count(s)")[0][0] == 1, (
        "one spelling, one owner")


@_live_db
def test_a_node_with_no_clock_is_refused_rather_than_dated_by_the_migration(graph):
    """§4.6's ``ingestedAt`` is REQUIRED — refusing beats inventing it.

    FAIL-ON: the node is relabelled with the migration run's own ``now`` as its
    ingest instant, i.e. the graph now asserts a fact that never happened, and
    every recency/decay read that keys on ``ingestedAt`` inherits it. Mutation:
    ``at = ingested or created or updated or _now_iso()``.
    """
    proj, sdk = graph
    proj.g.query("CREATE (d:Document {id:'doc/undated.md', title:'No clock', "
                 "documentKind:'research'})")

    report = relabel_legacy_documents(sdk)

    assert [u["url"] for u in report["undated"]] == ["doc/undated.md"]
    assert report["relabelled"] == []
    assert _row(proj, "MATCH (s:Source) RETURN count(s)")[0][0] == 0
    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 1


@_live_db
def test_dry_run_reports_the_same_shape_and_writes_nothing(graph):
    """An operator must be able to measure before moving a graph.

    FAIL-ON: the dry run writes (so "measure first" is not available on the one
    operation that rewrites a whole label). Mutation: drop the ``if dry_run:
    continue`` guard.
    """
    proj, sdk = graph
    proj.g.query(f"CREATE (d:Document {{{_LEGACY_PROPS}}})")

    report = relabel_legacy_documents(sdk, dry_run=True)

    assert report["relabelled"] == ["doc/pre-d10.md"], (
        "a dry run reports what it WOULD do, not nothing")
    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 1
    assert _row(proj, "MATCH (s:Source) RETURN count(s)")[0][0] == 0


@_live_db
def test_a_half_moved_node_is_completed_not_skipped_as_already_source(graph):
    """A node carrying BOTH labels is COMPLETED, in either half-moved shape.

    The move is one statement precisely so this state is not produced (a raise
    between a label-add and a label-remove used to be able to). But the state is
    what an interrupted older attempt — or a hand edit — leaves, in TWO shapes,
    and both must be finished rather than skipped as "already migrated":

    * url-LESS — invisible to every `MATCH (s:Source {url: …})` reader, so
      `ingest --upgrade-all` selects a row whose `url` is NULL and neither
      `_do_upgrade` nor the resolver can ever reach it;
    * url-SET — readable, but it still carries the retired `:Document` label and
      whatever §4.6 fields the interruption left NULL, and a re-run would skip it
      forever, so the pass that exists to write those fields never does.

    FAIL-ON: the node is skipped and keeps `:Document` (and, in the url-less
    shape, stays keyless). Mutation: filter candidates with `WHERE NOT n:Source`
    (skips both shapes) or skip anything already `:Source` (skips the second).
    """
    proj, sdk = graph
    # Shape 1: both labels, NO url.
    proj.g.query(
        "CREATE (n:Document:Source {id:'doc/half.md', title:'Half', "
        "documentKind:'report', doc_status:'captured', "
        "updatedAt:'2026-02-03T04:05:06Z'})")
    # Shape 2: both labels, url already set, §4.6 fields still NULL.
    proj.g.query(
        "CREATE (m:Document:Source {id:'doc/half2.md', url:'doc/half2.md', "
        "title:'Half2', documentKind:'report', "
        "updatedAt:'2026-02-04T04:05:06Z'})")

    report = relabel_legacy_documents(sdk)

    assert sorted(report["relabelled"]) == ["doc/half.md", "doc/half2.md"], (
        "BOTH half-moved shapes need the move completed, not a skip")
    assert _row(proj, "MATCH (s:Source {url: 'doc/half.md'}) "
                      "RETURN s.sourceKind, s.ingestedAt")[0] == [
        "document", "2026-02-03T04:05:06Z"]
    assert _row(proj, "MATCH (s:Source {url: 'doc/half2.md'}) "
                      "RETURN s.sourceKind, s.version, s.canonicalUrl")[0] == [
        "document", 1, "doc/half2.md"], (
            "the url-SET shape gets the §4.6 fields it was missing")
    assert _row(proj, "MATCH (n:Document) RETURN count(n)")[0][0] == 0, (
        "and the retired label is gone from both")
