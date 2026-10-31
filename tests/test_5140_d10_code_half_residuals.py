"""#5140 — D10 (#5026) code-half residuals that are STILL LIVE on current main.

`#5140` is a laundry list: ten findings outside PR #5127's declared threat
surface, plus more added in its comments. Most of it has LANDED since, and this
module pins only what has not — in both directions, so a fix cannot silently
over-reach:

  * **finding 12** — `_upsert_document` MERGEs the raw event `id` instead of
    routing through the S0a/S0b canonical-identity resolver (`resolve_source_key`,
    #5012/#5053) that every other `:Source` writer uses, so a caller-supplied
    URL variant minted a SECOND `:Source` for one identity. The node also
    carried none of the S0a identity props, so the SAME identity in the OTHER
    order still diverged — both orders are pinned here, and the collapse a
    NETWORK document id now inherits from S0a (two ids differing only in
    `#fragment` are one `:Source`) is pinned as INTENDED, so it cannot be
    "tidied" back into per-fragment nodes.

LANDED, and therefore deliberately NOT re-pinned here (the `file:line` evidence
is on the issue): finding 1 (`search_engine.py`'s `documentKind` discriminator
on all three legs), finding 2 (the `references` self-loop + the re-pointed
reader, `tests/test_references_edge.py::test_provenance_chain_returns_document
_without_source_url`), finding 3's reproduced defect (#9874,
`tests/test_5140_pending_extraction.py`), finding 5 (#9396,
`tests/test_document_source_ingested_at_5140.py`), finding 6 (#5422's `version`
anchor), finding 9 (#5236), finding 11 (the ruling-B `:Source` guard).

⛔ NOT closed by this module, stated rather than implied:

  * **finding 7's FORGE direction** — adding a genre to an existing
    non-document `:Source` — is PINNED AS INTENDED by #9536
    (`tests/test_quota.py::test_document_kind_cannot_be_demoted_out_of_the_meter`,
    the NON-OVER-REACH arm: *"SETTING a genre where none is stored is how a
    document `:Source` is minted through these surfaces at all"*). Closing it is
    a decision about that contract, not a bugfix, so it is left to the owner.
  * **finding 8** — CLOSED by `tortoise/migrate_kinds.py::relabel_legacy_documents`
    (runs first in `_do_migrate`), and with it **finding 3's backlog-discovery
    half for the foldable part of a LIVE graph**: once the pre-D10 `:Document`
    population is folded into `:Source {url}`, the `doc_status='captured'` arm
    the sweep already carries reaches it. Pinned by
    `tests/test_5140_d10_relabel_migration.py`.
    ⚠️ Two populations are NOT reached, and neither is a relabel's to fix:
    (a) a document minted by `_event_plain_merge`'s `objectType='Document'`
    branch carries NO clock, so the relabel refuses it (`undated`) rather than
    invent §4.6's `ingestedAt` — an operator asserts the instant and re-runs;
    (b) the same backlog on a REBUILT graph (the replay scrubs `doc_status` and
    stamps the old `needs_extraction=false`, which reads as done) — a product
    decision recorded on #5140.
  * the **`transcript` genre-vocabulary** decision (issue comment
    2026-10-22T00:49; `owner-decision` label).

Hermetic: an embedded FalkorDB per test (`sdk_factory`), no live Docker. The
fixture is URI-AWARE, so a leg that sets `TORTOISE_DB_URI` redirects the graph
to that server; the tier-2 PR legs are URI-empty by design, so it runs embedded
there.
"""

from __future__ import annotations

# ── finding 12: the document MERGE key adopts the shared resolver ───────────


def test_document_created_url_variant_does_not_mint_a_second_source(sdk_factory):
    """S0a/S0b parity (#5012/#5053): NO write path may mint a second `:Source`
    for one identity. `_upsert_document` merged the raw event id, so a URL
    variant of an already-registered source produced TWO nodes.
    """
    sdk = sdk_factory()
    sdk.create_source("https://example.com/a/b", "document")
    sdk._get_proj().apply(
        {
            "type": "DocumentCreated",
            "id": "https://Example.COM/a/b/?utm_source=x#frag",
            "title": "X",
            "document_kind": "report",
        }
    )
    rows = (
        sdk._get_proj()
        .g.query("MATCH (s:Source) WHERE toLower(s.url) CONTAINS 'example.com/a/b' RETURN s.url")
        .result_set
    )
    urls = sorted(r[0] for r in rows)
    assert urls == ["https://example.com/a/b"], (
        f"the resolver was bypassed — {len(urls)} nodes: {urls}"
    )


def test_document_created_then_source_still_one_node(sdk_factory):
    """The OTHER order (the issue reproduced both). The document node carried
    none of the S0a identity props, so `resolve_source_key` had no
    `canonicalUrl` to match and `create_source` minted a sibling."""
    sdk = sdk_factory()
    sdk._get_proj().apply(
        {
            "type": "DocumentCreated",
            "id": "https://Example.COM/a/b/?utm_source=x#frag",
            "title": "X",
            "document_kind": "report",
        }
    )
    sdk.create_source("https://example.com/a/b", "document")
    rows = (
        sdk._get_proj()
        .g.query(
            "MATCH (s:Source) WHERE toLower(s.url) CONTAINS 'example.com/a/b' "
            "RETURN s.url, s.documentKind"
        )
        .result_set
    )
    assert len(rows) == 1, f"one identity, {len(rows)} nodes: {rows}"
    assert rows[0][1] == "report", (
        "the document node lost its genre when the source resolved onto it"
    )


def test_document_created_non_url_id_keeps_its_identity(sdk_factory):
    """Non-over-reach: the resolver is IDENTITY for the two non-URL document-id
    shapes the INDEX and CLI paths emit — `doc_<rel-path>` from
    `derive_document_id` (the underscore form; the literal below is the
    MIXED-CASE `doc_Notes/5140.md`, not `doc/notes/…`) and a BARE FILENAME,
    which is what the CLI passes (`ingest.py` uses `args.transcript.name`;
    `corpus://…` is the index path's SOURCE URL, not a document id). Their node
    keys must not move.

    ⚠️ It asserts the identity PROPERTY as well as the key, and one literal is
    MIXED CASE, because the key alone cannot see the defect this guards: on a
    first-time miss `resolve_source_key` returns the raw id (its step 4), so a
    resolver that canonicalised these shapes would leave `url` untouched while
    moving only `canonicalUrl` — and a lower-casing resolver is invisible to
    all-lower-case literals. Measured: with `normalize_source_url` mapping
    `doc_Notes/…` to `doc/Notes/…`, or lower-casing it, a key-only version of
    this test passed 7/7 and this version fails.

    Scoped to those two PRODUCER paths on purpose: they are not the only
    non-URL document ids (`create_document` mints a ULID key), but the claim
    being pinned is that the resolver leaves these two alone."""
    sdk = sdk_factory()
    proj = sdk._get_proj()
    dids = ("doc_Notes/5140.md", "README.md")
    for did in dids:
        proj.apply({"type": "DocumentCreated", "id": did, "title": did, "document_kind": "brief"})
    rows = proj.g.query(
        "MATCH (s:Source) WHERE s.url IN ['doc_Notes/5140.md', 'README.md'] "
        "RETURN s.url, s.canonicalUrl, s.documentKind"
    ).result_set
    assert sorted(rows) == [[d, d, "brief"] for d in sorted(dids)], rows


def test_document_created_replays_to_the_same_node(sdk_factory):
    """Durability: the resolved key and the identity props are written by the
    FOLD, so re-applying the SAME record must be a no-op — the node count must
    not grow and the alias set must not accumulate the raw spelling again.

    The alias half is the assertion that DISCRIMINATES; the node count does not
    on its own, and this docstring no longer claims it does: a `MERGE` on a
    constant key is one node however many times it runs, so a resolver applied
    only on the live path would leave this count at 1 as well (#5140 review of
    PR #9952 — the earlier "which is what a resolver applied only on the live
    path would have produced" claimed a discrimination a constant `MERGE`
    cannot have, and the case was green pre-fix by that PR's own base
    measurement). What IS fold-owned is the `urlAliases` clause's dedup:
    measured, dropping its `CASE WHEN $raw_url IN …` guard (leaving a bare
    `coalesce(s.urlAliases, []) + [$raw_url]`) fails this test with the raw
    spelling repeated three times."""
    sdk = sdk_factory()
    proj = sdk._get_proj()
    rec = {
        "type": "DocumentCreated",
        "id": "https://Example.COM/a/b/?utm_source=x#frag",
        "title": "X",
        "document_kind": "report",
    }
    proj.apply(rec)
    proj.apply(rec)
    proj.apply(rec)
    rows = proj.g.query(
        "MATCH (s:Source) WHERE toLower(s.url) CONTAINS 'example.com/a/b' "
        "RETURN count(s), collect(s.documentKind), collect(s.urlAliases)"
    ).result_set
    assert rows[0] == [1, ["report"], [[rec["id"]]]], rows


def test_document_created_fragment_variants_collapse_to_one_node(sdk_factory):
    """PINNED AS INTENDED (S0a, #5012/#5053): a FRAGMENT does not select a
    different document, so two document ids differing only in `#fragment` are
    ONE `:Source` — and the `documents` meter therefore counts one.

    This is the consequence of adopting the shared resolver rather than a
    side effect to be "fixed": a fragment- or query-only variant is the same
    resource, and minting a second node per anchor is the defect S0a removed
    for every other `:Source` writer. Pinned so a later tidy-up cannot
    re-introduce per-fragment document nodes without deleting a named decision.
    """
    sdk = sdk_factory()
    proj = sdk._get_proj()
    for frag in ("one", "two"):
        proj.apply(
            {
                "type": "DocumentCreated",
                "id": f"https://example.com/doc#section-{frag}",
                "title": f"t-{frag}",
                "document_kind": "report",
            }
        )
    rows = proj.g.query(
        "MATCH (s:Source) WHERE toLower(s.url) CONTAINS 'example.com/doc' "
        "RETURN count(s), collect(s.url), collect(s.canonicalUrl)"
    ).result_set
    assert rows[0][0] == 1, f"fragment variants minted separate nodes: {rows}"
    # The stored `url` is NOT rewritten: the first write stamped `canonicalUrl`,
    # so the second variant is found by `resolve_source_key`'s step 1
    # (`MATCH (s:Source {canonicalUrl:$cu})`) and MERGEs onto the first writer's
    # stored url. Asserted so this is a stated contract, not a reading: the
    # node's KEY is the first writer's, the identity is canonical.
    assert rows[0][1] == ["https://example.com/doc#section-one"], rows
    assert rows[0][2] == ["https://example.com/doc"], rows


def test_document_created_appends_the_raw_spelling_to_url_aliases(sdk_factory):
    """The alias set `_upsert_source` keeps, kept here too: the fold records the
    RAW spelling this record named, so the resolver can re-find the node from a
    spelling it has not been handed before.

    DISCRIMINATING, and it is the ONLY case here that pins the ALIAS clause: the
    pre-canonical node below is stored under the CANONICAL spelling, so
    `resolve_source_key` adopts it in step 2 — which writes
    `urlAliases=[canonical]` ITSELF — and the raw variant reaches `urlAliases`
    only through `_upsert_document`'s own clause. Delete that clause and this test
    fails while the other six still pass.
    """
    sdk = sdk_factory()
    proj = sdk._get_proj()
    canonical = "https://example.com/a/b"
    raw = "https://Example.COM/a/b/?utm_source=x#frag"
    # A node registered BEFORE S0a: the canonical spelling as its `url`, no
    # `canonicalUrl` yet — exactly the shape the resolver's step 2 adopts.
    proj.g.query(
        "MERGE (s:Source {url:$url}) ON CREATE SET s.id=$url, "
        "s.sourceKind='document', s.version=1",
        params={"url": canonical},
    )
    proj.apply(
        {
            "type": "DocumentCreated",
            "id": raw,
            "title": "X",
            "document_kind": "report",
        }
    )
    rows = proj.g.query(
        "MATCH (s:Source) WHERE toLower(s.url) CONTAINS 'example.com/a/b' "
        "RETURN s.url, s.canonicalUrl, s.urlAliases"
    ).result_set
    assert len(rows) == 1, f"the adopting write minted a sibling: {rows}"
    url, stored_canonical, aliases = rows[0]
    assert url == canonical, rows
    assert stored_canonical == canonical, rows
    assert sorted(aliases or []) == sorted([canonical, raw]), (
        f"the raw spelling was not recorded — a later variant of it would mint a "
        f"second node: {rows}"
    )


def test_document_id_that_canonicalises_onto_a_web_source(sdk_factory):
    """The reach of the identity rule beyond documents: a document id whose
    canonical form equals an EXISTING non-document `:Source` folds onto that
    source. It is one node — that half is the rule, and it is what is pinned.

    ⚠️ The REST of the measurement is CURRENT BEHAVIOUR, pinned so a change is
    deliberate, NOT endorsed as the destination: the fold overwrites the
    source's `title` and gives it a `documentKind`, so the node leaves
    `list_sources()` (`documentKind IS NULL`) and enters the `documents` meter,
    while `sourceKind` stays `'web'`. Whether promotion should also move the
    TYPE axis (or whether a document should fold onto a provenance source at
    all) is #9536's contract, not this fold's — raised there, not decided here.
    `EventAPI.add_document` takes a caller-supplied `doc_id`, so this is
    reachable from a public door.
    """
    sdk = sdk_factory()
    sdk.create_source("https://example.com/a", "web", title="A page")
    sdk._get_proj().apply(
        {
            "type": "DocumentCreated",
            "id": "https://example.com/a#frag",
            "title": "Extracted doc",
            "document_kind": "report",
        }
    )
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source) WHERE toLower(s.url) CONTAINS 'example.com/a' "
        "RETURN count(s), collect(s.url), collect(s.sourceKind), "
        "collect(s.documentKind), collect(s.title)"
    ).result_set
    assert rows[0][0] == 1, f"one identity must be one node: {rows}"
    # current behaviour (see docstring): the source is PROMOTED to a document
    assert rows[0][2] == ["web"] and rows[0][3] == ["report"], rows
    assert [r.get("url") for r in sdk.list_sources()] == [], (
        "a promoted node is metered as a document and leaves list_sources()"
    )
