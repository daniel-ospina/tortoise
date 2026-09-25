"""#5199 — the version anchor on DERIVATION ``references`` links.

Owner-approved 2026-09-25 (option A): an **optional** ``sourceVersion`` on the
**derivation** ``references`` link only — present iff the target was built from
that source's content. Identity/mention links (``Object``) and referential
containment (``Source → Source``) stay **property-free**. The anchor is set at
LINK TIME from the source's current ``contentHash``, so the public SDK signature
does not change.

``sourceVersion`` stays a **read** (``r.sourceVersion`` compared to
``s.contentHash``), never a stored status, and ``stale != wrong`` — the derived
node is never deleted.

Every test below names the input that makes it FAIL.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK  # noqa: E402


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_test_"), name)


def _fixture(sdk: TortoiseSDK, *, content_hash: str = "h1"):
    """Source(url='src.txt', contentHash=content_hash) + an Event and an Object."""
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (s:Source {url:'src.txt', sourceKind:'document', title:'src.txt', "
        "contentHash:$h, ingestedAt:'2024-01-01'})",
        params={"h": content_hash},
    )
    proj.g.query(
        "CREATE (ev:Event {id:'ev-1', eventId:'ev-1', name:'Meeting', "
        "eventKind:'meeting'})"
    )
    proj.g.query("CREATE (o:Object {id:'obj-1', name:'Widget', objectKind:'product'})")
    return proj


def _set_hash(proj, content_hash: str) -> None:
    proj.g.query(
        "MATCH (s:Source {url:'src.txt'}) SET s.contentHash=$h",
        params={"h": content_hash},
    )


# ── the stamp ──────────────────────────────────────────────────────────────

def test_derivation_link_records_the_version_read():
    """Input: a source with contentHash 'h1'; Event linked.

    FAILS IF the derivation link carries no ``sourceVersion`` (the anchor is not
    written) or it is not the hash that was read.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        rows = proj.g.query(
            "MATCH (:Source {url:'src.txt'})-[r:references]->(:Event {id:'ev-1'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [["h1"]], f"expected the version read ('h1'), got {rows!r}"
    finally:
        sdk.close()


def test_derivation_label_set_excludes_identity_targets():
    """Input: the discriminator itself.

    FAILS IF ``Object`` (identity/mention) is added to the derivation set — that
    is the regression that would stamp a non-answer onto a connector artifact,
    or if the derivation classes are dropped entirely.
    """
    from tortoise.projection.edges import _DERIVATION_REFERENCES_LABELS

    assert "Event" in _DERIVATION_REFERENCES_LABELS
    assert "Object" not in _DERIVATION_REFERENCES_LABELS


def test_identity_link_stays_property_free():
    """Input: a source with contentHash 'h1'; Object linked.

    FAILS IF the identity/mention link carries ``sourceVersion`` — the approved
    scope keeps it property-free (the Source's url IS the artifact).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "obj-1", "Object")

        rows = proj.g.query(
            "MATCH (:Source {url:'src.txt'})-[r:references]->(:Object {id:'obj-1'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [[None]], f"Object link must stay property-free, got {rows!r}"
    finally:
        sdk.close()


# ── the read ───────────────────────────────────────────────────────────────

def test_unchanged_hash_reads_current():
    """Input: linked at 'h1' and the source still holds 'h1'.

    FAILS IF the derived comparison does not read CURRENT.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        rows = proj.g.query(
            "MATCH (s:Source {url:'src.txt'})-[r:references]->(:Event {id:'ev-1'}) "
            "RETURN r.sourceVersion = s.contentHash"
        ).result_set
        assert rows == [[True]], f"expected current, got {rows!r}"
    finally:
        sdk.close()


def test_relink_does_not_advance_the_recorded_version():
    """Input: link at 'h1', move the source to 'h2', link AGAIN.

    FAILS IF the re-link advances ``sourceVersion`` to 'h2' (``SET`` instead of
    ``ON CREATE SET``) — the recorded version would then equal the current hash
    and the staleness the anchor exists to expose would read as CURRENT.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        _set_hash(proj, "h2")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        rows = proj.g.query(
            "MATCH (s:Source {url:'src.txt'})-[r:references]->(:Event {id:'ev-1'}) "
            "RETURN r.sourceVersion, s.contentHash"
        ).result_set
        assert rows == [["h1", "h2"]], (
            "a re-link must not erase staleness — the recorded version stays the "
            f"one that was read; got {rows!r}"
        )
    finally:
        sdk.close()


def test_revised_source_stales_the_derived_node_without_deleting_it():
    """Input: link at 'h1', then the source content changes to 'h2'.

    FAILS IF the derived Event is deleted (``stale != wrong``; supersession is
    additive and deferred), or if it does not read STALE.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        _set_hash(proj, "h2")

        alive = proj.g.query(
            "MATCH (e:Event {id:'ev-1'}) RETURN count(e)"
        ).result_set
        assert alive == [[1]], f"the derived node must survive, got {alive!r}"

        rows = proj.g.query(
            "MATCH (s:Source {url:'src.txt'})-[r:references]->(:Event {id:'ev-1'}) "
            "RETURN r.sourceVersion = s.contentHash"
        ).result_set
        assert rows == [[False]], f"expected a STALE read, got {rows!r}"
    finally:
        sdk.close()


# ── the absent state ───────────────────────────────────────────────────────

def test_auto_created_source_is_not_stamped():
    """Input: an Event linked to a source that does not exist yet (auto-created
    with ``contentHash=''``).

    FAILS IF ``sourceVersion`` is written as the empty string — that compares
    EQUAL to the auto-created source's ``''`` and would read as a false CURRENT.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query("CREATE (ev:Event {id:'ev-2', eventId:'ev-2', name:'Loose'})")

        sdk.link_source_to_entity("never-seen.txt", "ev-2", "Event")

        rows = proj.g.query(
            "MATCH (:Source {url:'never-seen.txt'})-[r:references]->(:Event {id:'ev-2'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [[None]], (
            "a source with no content has no version to anchor — an empty-string "
            f"stamp would read as falsely current; got {rows!r}"
        )
    finally:
        sdk.close()


def test_eventid_keyed_writer_anchors_too():
    """Input: an ``Event`` with ``eventId`` but NO ``id``, linked through the
    ``eventId``-keyed writer (the shape legacy raw-Cypher Events have).

    FAILS IF the second derivation writer omits the anchor — a derivation edge
    must not be anchored on one path and unanchored on another (the connector
    choke point, the capture path and the backfill path all write this shape).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'src2.txt', sourceKind:'agentSession', "
            "title:'src2.txt', contentHash:'h1', ingestedAt:'2024-01-01'})"
        )
        # No `id` — the legacy shape the id-keyed MATCH would silently no-op on.
        proj.g.query("CREATE (e:Event {eventId:'ev-9', name:'Captured'})")

        sdk._get_proj().link_source_to_event("src2.txt", "ev-9")

        rows = proj.g.query(
            "MATCH (s:Source {url:'src2.txt'})-[r:references]->(e:Event {eventId:'ev-9'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [["h1"]], f"eventId-keyed writer must anchor too, got {rows!r}"
    finally:
        sdk.close()


def test_document_link_records_the_version_read():
    """Input: a source with contentHash 'h1'; ``Document`` linked (the second
    member of the derivation set, still live until D10 retires the label).

    FAILS IF ``Document`` is dropped from the derivation set — deleting it used
    to leave every test green, so the set member had no input that could fail.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'src3.txt', sourceKind:'document', "
            "title:'src3.txt', contentHash:'h1', ingestedAt:'2024-01-01'})"
        )
        proj.g.query("CREATE (d:Document {id:'doc-9', name:'Spec'})")

        sdk.link_source_to_entity("src3.txt", "doc-9", "Document")

        rows = proj.g.query(
            "MATCH (:Source {url:'src3.txt'})-[r:references]->(:Document {id:'doc-9'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [["h1"]], f"Document is a derivation class, got {rows!r}"
    finally:
        sdk.close()


def test_pre_model_edge_is_not_retro_stamped():
    """Input: a derivation edge that already exists WITHOUT the anchor (what
    every pre-`#5199` edge looks like), then re-linked.

    FAILS IF a later link retro-stamps it with the CURRENT hash. The version a
    pre-model edge was read from is **unknown**, and writing today's hash would
    fabricate a `current` read. The honest state is ABSENT — never a guess.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'src4.txt', sourceKind:'document', "
            "title:'src4.txt', contentHash:'h2', ingestedAt:'2024-01-01'})"
        )
        proj.g.query("CREATE (e:Event {eventId:'ev-8', name:'Legacy'})")
        # The pre-model edge: property-free.
        proj.g.query(
            "MATCH (s:Source {url:'src4.txt'}), (e:Event {eventId:'ev-8'}) "
            "MERGE (s)-[:references]->(e)"
        )

        sdk._get_proj().link_source_to_event("src4.txt", "ev-8")

        rows = proj.g.query(
            "MATCH (:Source {url:'src4.txt'})-[r:references]->(:Event {eventId:'ev-8'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [[None]], (
            "a pre-model edge has no recorded version and must NOT be retro-stamped "
            f"with the current hash (that would fabricate `current`); got {rows!r}"
        )
    finally:
        sdk.close()
