"""Integration tests for S0a/S0b source dedup at registration (issue #5012).

These exercise the REAL registration path — ``TortoiseSDK.create_source`` →
``Projection._upsert_source`` — and assert the node count, because the whole
point of the change is that **two spellings of one document are one
``:Source``**.

``test_two_url_variants_register_one_source`` is the acceptance test: it fails
on the pre-change code (2 nodes) and passes after (1 node).

Backend-agnostic: under a supported ``TORTOISE_DB_URI`` the SDK redirects to the
server (per-test graph), otherwise it runs embedded.
"""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from tortoise.sdk import TortoiseSDK


def _uri_set() -> bool:
    from tortoise.config import is_db_uri

    return is_db_uri(os.environ.get("TORTOISE_DB_URI"))


@contextmanager
def fresh_sdk():
    """A fresh, isolated graph (embedded, or a redirect-namespaced server graph)."""
    base = tempfile.mkdtemp(prefix="tt_5012_")
    db_path = os.path.join(base, "test.db")
    # Epic #1647 seam: under a supported URI, pass a guard-passing per-test
    # namespace so the redirect targets test_suite_<uuid>_tortoise.
    ns = f"test_suite_{os.urandom(4).hex()}" if _uri_set() else None
    sdk = TortoiseSDK(db_path, namespace=ns)
    try:
        yield sdk
    finally:
        try:  # noqa: SIM105
            sdk.close()
        except Exception:
            pass


def _source_rows(sdk) -> list[list]:
    return sdk._get_proj().g.query(
        "MATCH (s:Source) RETURN s.url, s.canonicalUrl, s.urlAliases, s.version"
    ).result_set


def test_two_url_variants_register_one_source():
    """ACCEPTANCE: trailing slash + tracking param + fragment ⇒ ONE :Source."""
    with fresh_sdk() as sdk:
        sdk.create_source("https://docs.example.com/a/b/", "document")
        sdk.create_source(
            "https://docs.example.com/a/b?utm_source=news#section", "document"
        )
        rows = _source_rows(sdk)
        assert len(rows) == 1, rows
        url, canonical, aliases, _version = rows[0]
        assert url == "https://docs.example.com/a/b/"  # first-seen raw preserved
        assert canonical == "https://docs.example.com/a/b"
        assert set(aliases) == {
            "https://docs.example.com/a/b/",
            "https://docs.example.com/a/b?utm_source=news#section",
        }


def test_variant_after_canonical_form_merges():
    with fresh_sdk() as sdk:
        sdk.create_source("https://e.com/a/b", "document")  # already canonical
        sdk.create_source("https://e.com/a/b/", "document")  # variant
        sdk.create_source("HTTPS://E.COM/a/b", "document")  # another variant
        assert len(_source_rows(sdk)) == 1


def test_host_case_and_default_port_collapse():
    with fresh_sdk() as sdk:
        sdk.create_source("https://Example.com:443/p", "document")
        sdk.create_source("https://example.com/p", "document")
        assert len(_source_rows(sdk)) == 1


def test_non_network_identity_is_not_aliased():
    with fresh_sdk() as sdk:
        sdk.create_source("session:abc-123", "agentSession")
        rows = _source_rows(sdk)
        assert len(rows) == 1
        assert rows[0][0] == "session:abc-123"
        assert rows[0][1] == "session:abc-123"


def test_distinct_documents_are_not_merged():
    """The false-merge guard: different paths must stay different Sources."""
    with fresh_sdk() as sdk:
        sdk.create_source("https://e.com/a", "document")
        sdk.create_source("https://e.com/b", "document")
        sdk.create_source("http://e.com/a", "document")  # scheme preserved
        assert len(_source_rows(sdk)) == 3


def test_point_stub_path_does_not_mint_a_second_source():
    """create_point(extractedFrom=<variant>) resolves to the registered node."""
    with fresh_sdk() as sdk:
        sdk.create_source("https://e.com/c/d/", "document")
        sdk.create_point("statement", "a claim", extractedFrom="https://e.com/c/d")
        rows = _source_rows(sdk)
        assert len(rows) == 1, rows
        edges = sdk._get_proj().g.query(
            "MATCH (p:Point)-[:extractedFrom]->(s:Source) RETURN s.url"
        ).result_set
        assert {r[0] for r in edges} == {"https://e.com/c/d/"}


def test_reader_resolves_a_variant():
    """A by-url mutator addresses the node the write registered (S0b)."""
    with fresh_sdk() as sdk:
        sdk.create_source("https://e.com/j/k/", "document")
        sdk.set_source_tier("https://e.com/j/k?utm_source=x", "high")
        assert len(_source_rows(sdk)) == 1
        tiers = sdk._get_proj().g.query(
            "MATCH (s:Source) RETURN s.credibilityTier"
        ).result_set
        assert tiers[0][0] == "T1"  # canonical_tier("high")


def test_legacy_node_is_adopted_on_touch_not_duplicated():
    """A Source minted before canonical identity existed is adopted, not cloned."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        raw = "https://legacy.example.com/x/?utm_source=old"
        # Simulate a pre-fix node: url keyed, NO canonicalUrl.
        proj.g.query(
            "MERGE (s:Source {url:$url}) ON CREATE SET s.id=$url, "
            "s.sourceKind='document', s.version=1",
            params={"url": raw},
        )
        assert _source_rows(sdk)[0][1] is None  # no canonicalUrl yet
        sdk.create_source(raw, "document")
        rows = _source_rows(sdk)
        assert len(rows) == 1, rows  # adopted, not duplicated
        assert rows[0][1] == "https://legacy.example.com/x"


def test_connector_source_path_does_not_mint_a_second_source():
    """The connector choke point (#388) resolves too — 100% of connector
    events flow through ``_materialize_connector_source``, so an unresolved
    path there would defeat dedup for the whole connector surface."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        # A real :Event, so the (Source)-[:references]->(Event) edge AND the
        # supersede sweep are exercised — the region N3 lives in.
        proj.g.query(
            "MERGE (e:Event {eventId: 'evt-1'}) ON CREATE SET e.id='evt-1'"
        )
        proj._materialize_connector_source(
            {"sourceKind": "github_issue",
             "sourceUrl": "https://github.com/acme/repo/issues/5"},
            "evt-1",
        )
        sdk.create_source(
            "https://github.com/acme/repo/issues/5?utm_source=slack", "document"
        )
        rows = _source_rows(sdk)
        assert len(rows) == 1, rows
        assert rows[0][1] == "https://github.com/acme/repo/issues/5"
        refs = proj.g.query(
            "MATCH (s:Source)-[:references]->(e:Event {eventId:'evt-1'}) "
            "RETURN s.url"
        ).result_set
        assert {r[0] for r in refs} == {"https://github.com/acme/repo/issues/5"}


def test_connector_sweep_preserves_superseded_provenance():
    """N3: when the sweep deletes a superseded duplicate Source, the survivor
    inherits its accumulated tier/hash — the canonical-identity survivor is not
    necessarily the node that carried the metadata."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.g.query("MERGE (e:Event {eventId: 'evt-2'}) ON CREATE SET e.id='evt-2'")
        proj.g.query(
            "MERGE (s:Source {url:'https://dup.example.com/a/'}) "
            "ON CREATE SET s.id='dup', s.sourceKind='document', "
            "    s.credibilityTier='T0', s.contentHash='hash1' "
            "WITH s MATCH (e:Event {eventId:'evt-2'}) MERGE (s)-[:references]->(e)"
        )
        proj._materialize_connector_source(
            {"sourceKind": "github_issue",
             "sourceUrl": "https://dup.example.com/a"},
            "evt-2",
        )
        rows = proj.g.query(
            "MATCH (s:Source) RETURN s.url, s.credibilityTier, s.contentHash"
        ).result_set
        assert len(rows) == 1, rows
        url, tier, content_hash = rows[0]
        assert url == "https://dup.example.com/a"
        assert tier == "T0"  # inherited from the superseded node
        assert content_hash == "hash1"


def test_connector_sweep_never_overwrites_the_survivor():
    """#398: the survivor's own tier wins — inheritance only fills a gap."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.g.query("MERGE (e:Event {eventId: 'evt-3'}) ON CREATE SET e.id='evt-3'")
        proj.g.query(
            "MERGE (s:Source {url:'https://keep.example.com/b/'}) "
            "ON CREATE SET s.id='keep', s.sourceKind='document', "
            "    s.credibilityTier='T2', s.contentHash='keepme' "
            "WITH s MATCH (e:Event {eventId:'evt-3'}) MERGE (s)-[:references]->(e)"
        )
        proj.g.query(
            "MERGE (s:Source {url:'https://keep.example.com/b'}) "
            "ON CREATE SET s.id='surv', s.sourceKind='document', "
            "    s.credibilityTier='T1', s.contentHash='survh' "
            "WITH s MATCH (e:Event {eventId:'evt-3'}) MERGE (s)-[:references]->(e)"
        )
        # Sweep via a third, equivalent materialization.
        proj._materialize_connector_source(
            {"sourceKind": "github_issue",
             "sourceUrl": "https://keep.example.com/b"},
            "evt-3",
        )
        rows = proj.g.query(
            "MATCH (s:Source {url:'https://keep.example.com/b'}) "
            "RETURN s.credibilityTier, s.contentHash"
        ).result_set
        assert rows[0][0] == "T1"
        assert rows[0][1] == "survh"


def test_connector_sweep_does_not_graft_from_a_live_duplicate():
    """The P2-1 scope guard: a superseded node that is still SHARED (deg > 1)
    is not deleted, so its metadata must not be copied onto the survivor."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        proj.g.query("MERGE (e:Event {eventId: 'evt-4'}) ON CREATE SET e.id='evt-4'")
        proj.g.query(
            "MERGE (e2:Event {eventId: 'evt-9'}) ON CREATE SET e2.id='evt-9'"
        )
        # `old` references TWO events → deg = 2 → it survives the sweep.
        proj.g.query(
            "MERGE (s:Source {url:'https://live.example.com/c/'}) "
            "ON CREATE SET s.id='live', s.sourceKind='document', "
            "    s.credibilityTier='T3' "
            "WITH s MATCH (e:Event {eventId:'evt-4'}) MERGE (s)-[:references]->(e) "
            "WITH s MATCH (e2:Event {eventId:'evt-9'}) MERGE (s)-[:references]->(e2)"
        )
        proj._materialize_connector_source(
            {"sourceKind": "github_issue",
             "sourceUrl": "https://live.example.com/c"},
            "evt-4",
        )
        par = proj.g.query(
            "MATCH (s:Source {url:'https://live.example.com/c'}) "
            "RETURN s.credibilityTier"
        ).result_set
        assert par[0][0] is None  # no graft from the still-live duplicate
        live = proj.g.query(
            "MATCH (s:Source {url:'https://live.example.com/c/'}) RETURN s.credibilityTier"
        ).result_set
        assert live[0][0] == "T3"  # the shared node keeps its own tier


def test_legacy_canonically_spelled_node_is_adopted_on_variant_touch():
    """F2: the pre-existing corpus stores the *canonical* spelling with
    ``canonicalUrl IS NULL``.  An inbound tracking-param variant matches it by
    neither exact url nor canonicalUrl unless adoption also probes the
    canonical spelling — so it must not mint a duplicate."""
    with fresh_sdk() as sdk:
        proj = sdk._get_proj()
        legacy = "https://conn.example.com/pull/5"  # already canonical spelling
        proj.g.query(
            "MERGE (s:Source {url:$url}) ON CREATE SET s.id=$url, "
            "s.sourceKind='document', s.version=1",
            params={"url": legacy},
        )
        assert _source_rows(sdk)[0][1] is None  # no canonicalUrl yet
        sdk.create_source("https://conn.example.com/pull/5?utm_source=x", "document")
        rows = _source_rows(sdk)
        assert len(rows) == 1, rows
        assert rows[0][1] == legacy


def test_content_hash_change_is_a_new_version_not_a_new_source():
    with fresh_sdk() as sdk:
        url = "https://versioned.example.com/doc"
        sdk.create_source(url, "document", contentHash="aaa")
        sdk.create_source(url, "document", contentHash="bbb")
        rows = _source_rows(sdk)
        assert len(rows) == 1
        assert rows[0][3] == 2  # version bumped on the same node


def test_anchored_then_anchorless_does_not_wipe_hash():
    """#3998 contract preserved: an absent anchor must not wipe a stored one."""
    with fresh_sdk() as sdk:
        url = "https://anchor.example.com/doc"
        sdk.create_source(url, "document", contentHash="keepme")
        sdk.create_source(url, "document")  # no hash
        rows = sdk._get_proj().g.query(
            "MATCH (s:Source {url:$url}) RETURN s.contentHash, s.version",
            params={"url": url},
        ).result_set
        assert rows[0][0] == "keepme"
        assert rows[0][1] == 1
