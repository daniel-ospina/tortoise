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
    order still diverged — both orders are pinned here.

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
  * **finding 8** — no label-relabel migration shipped for pre-D10 `:Document`
    nodes — and **finding 3's backlog-discovery half**, which depends on it.
  * the **`transcript` genre-vocabulary** decision (issue comment
    2026-10-22T00:49; `owner-decision` label).

Hermetic: an embedded FalkorDB per test (`sdk_factory`), no live Docker.
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
    shapes the live producers emit (`doc_<rel-path>` from `derive_document_id`,
    `corpus://…` from the CLI), so their node keys must not move."""
    sdk = sdk_factory()
    proj = sdk._get_proj()
    dids = ("doc/notes/5140.md", "corpus://5140/cli-doc.md")
    for did in dids:
        proj.apply({"type": "DocumentCreated", "id": did, "title": did, "document_kind": "brief"})
    rows = proj.g.query(
        "MATCH (s:Source) WHERE s.url IN ['doc/notes/5140.md', "
        "'corpus://5140/cli-doc.md'] RETURN s.url, s.documentKind"
    ).result_set
    assert sorted(rows) == [
        ["corpus://5140/cli-doc.md", "brief"],
        ["doc/notes/5140.md", "brief"],
    ], rows


def test_document_created_replays_to_the_same_node(sdk_factory):
    """Durability: the resolved key and the identity props are written by the
    FOLD, so a rebuild of the same record is a no-op — the node count must not
    grow on replay, which is what a resolver applied only on the live path
    would have produced."""
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
        "RETURN count(s), collect(s.documentKind)"
    ).result_set
    assert rows[0] == [1, ["report"]], rows
