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

from tortoise.sdk import TortoiseSDK


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

    FAILS IF ``Object`` (identity/mention) or ``Source``
    (referential containment) is added to the derivation set — either is the
    regression that would stamp a non-answer onto a link with no version to
    compare — or if a derivation class is dropped.

    ``Document`` IS a member: in-repo writers do mint a document-derivation link
    (``projection/entities.py::_upsert_document``, the session→document link in
    ``hosted_api.py``, and the doc classifier of the ingest path), so excluding it
    would leave that derivation half unanchored. ⚠️ POST-D10 the ``:Document`` label
    is retired and those writers keep passing ``"Document"`` as the RELATION's
    spelling — the writer reads it for this decision and then remaps the IDENTITY
    onto ``:Source``. ⚠️ ONTOLOGY §3.4 declares the target list as
    ``Event|Object|Source`` and notes a document is itself a ``:Source`` — that list
    does not name ``Document``, so this set is pinned to the code's actual writers,
    not to a §3.4 enumeration (the ontology wording is in owner review on #5199).
    """
    from tortoise.projection.edges import _DERIVATION_REFERENCES_LABELS

    assert frozenset({"Event", "Document"}) == _DERIVATION_REFERENCES_LABELS, (
        "the derivation set must be exactly the labels whose in-repo writers build "
        f"the target FROM the source's content; got {_DERIVATION_REFERENCES_LABELS!r}"
    )
    assert "Object" not in _DERIVATION_REFERENCES_LABELS
    assert "Source" not in _DERIVATION_REFERENCES_LABELS


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

    FAILS IF the derived Event is deleted or REWRITTEN (``stale != wrong``;
    supersession is additive and deferred, and the anchor is a property on the
    EDGE — the target's own recorded version must survive untouched), or if it
    does not read STALE. The survival clause asserts the target's properties, not
    a bare ``count(e) == 1`` — the node is created by this test's own fixture, so
    a count could never be falsified by any change to the anchor.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        _set_hash(proj, "h2")

        alive = proj.g.query(
            "MATCH (e:Event {id:'ev-1'}) RETURN count(e), e.name, e.eventKind"
        ).result_set
        assert alive == [[1, "Meeting", "meeting"]], (
            "the derived node must survive the source revision UNCHANGED — the "
            f"anchor lives on the edge, never as a rewrite of the target; got {alive!r}"
        )

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

    FAILS IF the `eventId`-keyed derivation writer omits the anchor — a derivation
    edge must not be anchored on one provenance path and unanchored on another (the
    connector choke point and the capture path write this shape; the crash-repair
    backfill has its OWN writer, ``link_source_to_legacy_event``, because its Source
    is deliberately not the version the Event was read from).
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


def test_eventid_keyed_writer_does_not_stamp_an_empty_hash():
    """Input: a Source holding ``contentHash=''`` (the auto-created placeholder
    the connector choke point creates) linked through the SAME ``eventId``-keyed
    writer as the test above.

    FAILS IF the ``''`` guard is absent on THIS writer — the guard was only ever
    exercised on the id-keyed path, so removing it here left the whole file green.
    The value is reachable: ``projection/entities.py:2222-2240`` creates the
    Source with ``contentHash=''`` and immediately calls this writer, and a
    stamped ``''`` compares EQUAL to the source's ``''`` — a FALSE current.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'src7.txt', sourceKind:'github_issue', "
            "title:'src7.txt', contentHash:'', ingestedAt:'2024-01-01'})"
        )
        proj.g.query("CREATE (e:Event {eventId:'ev-10', name:'Connector'})")

        sdk._get_proj().link_source_to_event("src7.txt", "ev-10")

        rows = proj.g.query(
            "MATCH (s:Source {url:'src7.txt'})-[r:references]->(:Event {eventId:'ev-10'}) "
            "RETURN r.sourceVersion, r.sourceVersion IS NULL"
        ).result_set
        assert rows == [[None, True]], (
            "an empty source hash anchors NOTHING — stamping '' would compare equal "
            f"to the source's '' and read as falsely current; got {rows!r}"
        )
    finally:
        sdk.close()


def test_backfill_seam_anchors_the_events_recorded_version(tmp_path):
    """Drives the REAL repair seam — ``backfill_sources`` → ``_backfill_link`` —
    rather than the writer directly, because the ROUTING is what production runs.

    FAILS IF ``_backfill_link`` routes to ``link_source_to_event`` (which anchors
    the Source's CURRENT hash): a legacy Event whose ``file_hash`` predates the
    file's current content would then read **current** — the false-current the
    anchor exists to prevent. Mutation-verified: reverting that one call in
    ``sdk.py`` leaves the writer-level tests green and turns THIS one red.
    """
    from tortoise.file_indexer import compute_file_hash

    corpus = tmp_path / "corpus5199"
    corpus.mkdir()
    body = '---\nsessionId: legacy5199\ntitle: "S5199"\n---\nBody.\n'
    path = corpus / "s1.md"
    path.write_text(body)
    stored = compute_file_hash(str(path))

    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (e:Event) SET e.eventId='session_legacy5199', "
            "e.eventKind='AgentSession', e.source_file='s1.md', "
            "e.file_hash=$h, e.title='S5199'",
            params={"h": stored},
        )
        # edited since capture: the Source takes the CURRENT hash, the legacy
        # Event keeps the one it was captured with (additive-only, W2).
        path.write_text(body + "EDITED\n")
        current = compute_file_hash(str(path))
        assert current != stored

        report = sdk.backfill_sources(str(corpus))
        assert report["linked"] == 1, f"the missing edge must be repaired: {report!r}"

        rows = proj.g.query(
            "MATCH (s:Source)-[r:references]->(e:Event {eventId:'session_legacy5199'}) "
            "RETURN r.sourceVersion, e.file_hash, s.contentHash"
        ).result_set
        anchor, event_hash, source_hash = rows[0]
        assert anchor == stored, (
            "the repair path must anchor the version the EVENT records it was read "
            f"from ({stored!r}), not the Source's current hash ({source_hash!r}); "
            f"got {anchor!r}"
        )
        assert event_hash == stored and source_hash == current, (
            f"fixture sanity: the W2 shape must hold; got {rows!r}"
        )
        assert anchor != source_hash, (
            "anchoring the Source's current hash here would report a STALE Event as "
            "current — the exact failure sourceVersion exists to expose"
        )
    finally:
        sdk.close()


def test_document_link_records_the_version_read():
    """Input: a source with contentHash 'h1'; ``Document`` linked (the second
    member of the derivation set — the label D10 retires).

    FAILS IF ``Document`` is dropped from the derivation set — deleting it used
    to leave every test green, so the set member had no input that could fail.
    The member tracks the CODE's writers (``projection/entities.py::_upsert_document``,
    the session→document link in ``hosted_api.py``), which is where the relation's
    label is live; ONTOLOGY §3.4's declared target list does not name it (see the
    label-set test above). NOTE this test calls the writer directly, so it pins the
    MECHANISM only — production-call-site reachability is pinned by
    ``test_document_derivation_through_the_production_path_anchors`` below.

    **Updated at the #5199 x main merge (D10).** This test used to hang the
    target off a ``:Document`` node. D10 retires that label and collapses the
    caller onto ``:Source`` ("existing callers/journal replay converge on the
    same node"), so post-D10 the caller's ``"Document"`` must resolve to a
    ``:Source`` target by ``url``. The GUARD is unchanged and still load-bearing:
    drop ``Document`` from ``_DERIVATION_REFERENCES_LABELS`` and this reddens
    again, because the caller's original label is what selects the anchor
    (``_is_derivation``) — a bare ``Source`` caller stays property-free, which is
    what keeps referential-containment links unanchored.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'src3.txt', sourceKind:'document', "
            "title:'src3.txt', contentHash:'h1', ingestedAt:'2024-01-01'})"
        )
        # D10: the document IS a :Source — the caller still says "Document".
        proj.g.query("CREATE (d:Source {url:'doc-9', name:'Spec'})")

        sdk.link_source_to_entity("src3.txt", "doc-9", "Document")

        rows = proj.g.query(
            "MATCH (:Source {url:'src3.txt'})-[r:references]->(:Source {url:'doc-9'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [["h1"]], f"Document is a derivation class, got {rows!r}"
    finally:
        sdk.close()


def test_document_derivation_through_the_production_path_anchors():
    """Input: the production writer ``entities.py::_upsert_document`` handed a corpus
    ``source_url``, called directly (so the surrounding ``_doc_write``→``apply`` routing
    is not exercised) rather than a link this test builds by hand.

    FAILS IF the production call site stops spelling the relation ``"Document"``.
    That is exactly what D10 did: it rewrote these call sites to ``"Source"`` because
    the *node* a document resolves to is now a ``:Source``. The writer remaps identity
    either way, so the edge is still created and every other test here stayed green —
    while the anchor silently disappeared from every document link in the product,
    because ``"Source"`` means referential containment and containment links are
    deliberately property-free. Test the CALL SITE, not just the mechanism.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'corpus.txt', sourceKind:'corpus', "
            "title:'corpus.txt', contentHash:'h1', ingestedAt:'2024-01-01'})"
        )
        proj._upsert_document({
            "id": "doc-prod-1",
            "title": "Spec",
            "source_url": "corpus.txt",
            "suppress_embedding": True,
        })

        rows = proj.g.query(
            "MATCH (:Source {url:'corpus.txt'})-[r:references]->(:Source {url:'doc-prod-1'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [["h1"]], (
            "the document-derivation link minted by the production write path must carry "
            f"the version it was read at, got {rows!r}"
        )
    finally:
        sdk.close()


def test_index_path_document_link_records_the_version_read(tmp_path):
    """Input: the REAL index path (``sdk.index_directory``) over one document — the path
    through which a corpus→document link is actually minted in production.

    Pins the ANCHOR end-to-end on that path. It does NOT pin the ``sdk.py`` doc-classifier
    call site specifically, and says so rather than implying otherwise: mutating that
    site's label leaves this test GREEN, because on this path the edge is minted earlier
    by ``_upsert_document`` and ``ON CREATE`` does not fire twice. The call sites
    themselves are pinned by
    ``test_document_call_sites_express_derivation_not_containment``.
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "spec.md").write_text("---\ntitle: Spec\n---\nBody text about the spec.")
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        sdk.index_directory(str(corpus), extract_metadata=False)
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (a:Source)-[r:references]->(b:Source) "
            "WHERE b.url CONTAINS 'spec.md' AND a.url CONTAINS 'spec.md' "
            "RETURN a.contentHash, r.sourceVersion"
        ).result_set
        assert rows, "the index path must mint the corpus->document references edge"
        for source_hash, anchor in rows:
            assert anchor == source_hash and anchor, (
                "the index path's corpus->document link must carry the version it was "
                f"read at, got contentHash={source_hash!r} sourceVersion={anchor!r}"
            )
    finally:
        sdk.close()


def test_document_call_sites_express_derivation_not_containment():
    """A STRUCTURAL contract test, deliberately, and the reason is the regression class
    itself: D10 rewrote three production call sites from ``"Document"`` to
    ``"Source"`` with no conflict marker and no test failure, because the writer remaps
    identity either way and only the ANCHOR changes. A behavioural pin can only cover a
    path its fixture happens to drive — and two of the three paths (the hosted commit and
    the index repair) need heavy harnesses. This reads the call sites directly.

    The invariant, scoped exactly as enforced: **within the three files this change
    touched**, every literal label passed at a ``link_source_to_entity`` document call
    site is a member of ``_DERIVATION_REFERENCES_LABELS``. A genuinely caller-supplied
    label rides in a VARIABLE (the public passthrough does exactly that and must stay
    untouched), so it is not a literal here and is not constrained by this test.

    KNOWN COVERAGE GAP (recorded, not implied away): this is a regression pin for the
    three call sites D10 broke, not a repo-wide derivation-writer audit.
    ``tortoise/mining.py`` passes a literal ``"Event"`` at a real derivation call site
    and is **outside** this pin's file list; ``tortoise/connectors/github.py`` passes
    ``"Object"`` and is correctly irrelevant (an identity/mention link is not a
    derivation writer). Widening the file list is the way to close the gap — the
    assertion below counts literals, so a new file must be added here deliberately.
    """
    import ast
    from pathlib import Path

    from tortoise.projection.edges import _DERIVATION_REFERENCES_LABELS

    root = Path(__file__).resolve().parent.parent
    files = (
        "tortoise/projection/entities.py",
        "tortoise/hosted_api.py",
        "tortoise/sdk.py",
    )
    literal_labels: list[tuple[str, int, str]] = []
    for rel in files:
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "link_source_to_entity"
                and len(node.args) >= 3
                and isinstance(node.args[2], ast.Constant)
                and isinstance(node.args[2].value, str)
            ):
                literal_labels.append((rel, node.lineno, node.args[2].value))
            # the doc classifier of the ingest path picks its label conditionally
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.IfExp)
                and isinstance(node.value.orelse, ast.Constant)
                and isinstance(node.value.orelse.value, str)
                and any(isinstance(t, ast.Name) and t.id == "label" for t in node.targets)
            ):
                literal_labels.append((rel, node.lineno, node.value.orelse.value))

    non_derivation = [t for t in literal_labels if t[2] not in _DERIVATION_REFERENCES_LABELS]
    assert not non_derivation, (
        "a production document call site passes a NON-derivation label, so its link "
        f"silently loses the sourceVersion anchor: {non_derivation!r}"
    )
    assert len(literal_labels) == 3, (
        "expected exactly the three production document-derivation call sites "
        f"(entities/_upsert_document, hosted_api session->document, sdk doc classifier); "
        f"found {len(literal_labels)}: {literal_labels!r} — if one moved or was removed, "
        "re-point this pin rather than deleting it"
    )


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


def test_repair_path_anchors_the_events_recorded_version_not_the_current_one():
    """Input: the W2 shape `backfill_sources` produces when a file was edited since
    capture — the Source holds the CURRENT hash, the legacy Event keeps the
    ``file_hash`` it was captured with, and the edge between them is missing.

    FAILS IF the repair path anchors ``s.contentHash``: that would stamp today's
    hash on an Event built from older content, so the graph would report a STALE
    Event as **current** — the precise failure ``sourceVersion`` exists to expose.
    The honest anchor is the version the target records it was read from.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'src5.txt', sourceKind:'document', "
            "title:'src5.txt', contentHash:'h-current', ingestedAt:'2024-01-01'})"
        )
        # Legacy Event: no `id` (the shape this writer exists for), and its own
        # recorded file_hash — older than the Source's current content.
        proj.g.query(
            "CREATE (e:Event {eventId:'ev-7', name:'Captured', "
            "file_hash:'h-captured'})"
        )

        sdk._get_proj().link_source_to_legacy_event("src5.txt", "ev-7")

        rows = proj.g.query(
            "MATCH (s:Source {url:'src5.txt'})-[r:references]->(e:Event {eventId:'ev-7'}) "
            "RETURN r.sourceVersion, s.contentHash, e.file_hash"
        ).result_set
        assert rows == [["h-captured", "h-current", "h-captured"]], (
            "the repair path must anchor the version the EVENT was read from, so a "
            "stale Event reads stale (not `current`); got " + repr(rows)
        )
        # ...and the read that matters: NOT current.
        assert rows[0][0] != rows[0][1], (
            "anchoring the Source's current hash would make this stale Event read "
            "as current"
        )
    finally:
        sdk.close()


def test_repair_path_anchors_nothing_when_the_event_records_an_empty_hash():
    """Input: a legacy Event whose ``file_hash`` is the EMPTY STRING — the value
    ``edges.py`` names as "no recorded hash" on an Event.

    FAILS IF the repair path's ``''`` guard is dropped: with only the NULL case
    covered, replacing the guarded expression with a bare ``e.file_hash`` left
    every test green (an unguarded anchor renders NULL and ``''`` identically
    only for the null input). A stamped ``''`` FABRICATES a recorded version
    where the contract says ABSENT.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'src8.txt', sourceKind:'document', "
            "title:'src8.txt', contentHash:'h-current', ingestedAt:'2024-01-01'})"
        )
        proj.g.query(
            "CREATE (e:Event {eventId:'ev-11', name:'EmptyHash', file_hash:''})"
        )

        sdk._get_proj().link_source_to_legacy_event("src8.txt", "ev-11")

        rows = proj.g.query(
            "MATCH (:Source {url:'src8.txt'})-[r:references]->(:Event {eventId:'ev-11'}) "
            "RETURN r.sourceVersion, r.sourceVersion IS NULL"
        ).result_set
        assert rows == [[None, True]], (
            "an Event recording no hash ('' — not merely NULL) has no read version "
            f"to anchor; got {rows!r}"
        )
    finally:
        sdk.close()


def test_repair_path_anchors_nothing_when_the_event_records_no_hash():
    """Input: a legacy Event with no ``file_hash`` at all (the pre-#320 shape).

    FAILS IF the repair path falls back to the Source's hash — an unknown read
    version must be ABSENT, never guessed from the current content.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (s:Source {url:'src6.txt', sourceKind:'document', "
            "title:'src6.txt', contentHash:'h-current', ingestedAt:'2024-01-01'})"
        )
        proj.g.query("CREATE (e:Event {eventId:'ev-6', name:'Legacy'})")

        sdk._get_proj().link_source_to_legacy_event("src6.txt", "ev-6")

        rows = proj.g.query(
            "MATCH (:Source {url:'src6.txt'})-[r:references]->(:Event {eventId:'ev-6'}) "
            "RETURN r.sourceVersion"
        ).result_set
        assert rows == [[None]], (
            "an Event with no recorded hash has no read version to anchor — "
            f"guessing the current hash would fabricate `current`; got {rows!r}"
        )
    finally:
        sdk.close()
