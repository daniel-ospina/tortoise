"""#5199 — the entity-addressed currency read for `:Object` / `:Event`.

WHAT THIS SUITE IS FOR. #5199 asked for three things of the classes the graph
reasons about (`:Object` = 7,859 at filing): the provenance path carries the
version read, **a derived staleness read exists for those classes**, and a
falsifiable test shows a revised source stales a derived `:Object` without
deleting it.

The first two were answered by a **Point**-addressed read:
``TortoiseSDK.get_provenance_chain(point_id)`` (deleted by #4282) reported each
``references`` link's note; the read it wrapped survives as a graph query, and ``search_engine.read_currency_links`` returns a Point's
``extractedFrom`` pairs, which ``search_engine.aggregate_currency`` folds per
§4.6. Neither can be asked about an entity. A
derived `:Object`/`:Event` carries **no** ``extractedFrom`` link — it reaches
its source through ``(Source)-[:references]->(entity)`` — so the only way to
ask *"is this object still about the content we hold?"* today is to guess a
Point that happens to extract from the same source. That is the gap this read
closes, and the tests below are its checklist made executable.

TWO RECORDED DECISIONS THIS READ OBEYS RATHER THAN REVERTS:

* **The derivation anchor is owner-approved on `Event`/`Document` only**
  (2026-09-25, option A) — identity/mention links to an ``Object`` stay
  PROPERTY-FREE, pinned by ``test_source_version_references_5199.py``. So an
  Object's verdict is ``unknown``: honest, never ``current``. This read reports
  that instead of inventing an anchor, and ``test_object_link_is_unknown_not_current``
  is what would fail if someone "fixed" the emptiness by stamping the link.
* **``stale`` is not ``wrong``** (§4.6): the entity is never deleted and no
  supersession is manufactured here. ``test_stale_entity_survives_the_read``
  pins the row count.

Every test names the input that makes it FAIL.
"""
from __future__ import annotations

import atexit
import contextlib
import io
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.source_currency import (
    _build_payload,
    entity_currency,
    point_currency,
    read_entity_links,
)
from tools.source_currency import (
    main as cli_main,
)
from tortoise.projection.edges import _mint_source_stub
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import read_reference_currency_links


def _tmp(name: str) -> str:
    """A per-test store path, in a directory removed when the run ends.

    `mkdtemp` per case and no cleanup left 13 embedded DB trees behind on every
    run (a dev box accumulates them; CI is ephemeral). `atexit` keeps the
    per-test isolation — each case still gets its OWN directory — while the
    removal happens after the last SDK has closed, so no case can delete a
    store another case is reading.
    """
    d = tempfile.mkdtemp(prefix="tortoise_test_")
    atexit.register(shutil.rmtree, d, ignore_errors=True)
    return os.path.join(d, name)


def _fixture(sdk: TortoiseSDK, *, content_hash: str = "h1"):
    """Source(url='src.txt', contentHash=…) + an Event and an Object."""
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


def _count(proj, label: str) -> int:
    return proj.g.query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]


def _note_and_hash(proj):
    """The two operands of the comparison, read straight off the graph."""
    row = proj.g.query(
        "MATCH (:Source {url:'src.txt'})-[r:references]->(:Event {id:'ev-1'}) "
        "RETURN r.sourceVersion"
    ).result_set[0][0]
    cur = proj.g.query(
        "MATCH (s:Source {url:'src.txt'}) RETURN s.contentHash"
    ).result_set[0][0]
    return (row, cur)


# ── the read ──────────────────────────────────────────────────────────────

def test_a_revised_source_stales_the_derived_event():
    """Input: an Event linked at 'h1', then the source re-fetched at 'h2'.

    FAILS IF the entity-addressed read cannot answer for an Event (the class
    with no ``extractedFrom`` link), or answers anything but ``stale``.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        _set_hash(proj, "h2")

        verdict = entity_currency(proj.g, "ev-1")
        assert verdict["status"] == "stale", verdict
        assert verdict["links"] == [{
            "source": "src.txt",
            "recorded": "h1",
            "current": "h2",
            "currency": "stale",
        }], verdict["links"]
    finally:
        sdk.close()


def test_unchanged_source_reads_current():
    """Input: linked at 'h1' and the source still holds 'h1'.

    FAILS IF a matching pair does not read ``current`` (a read that only ever
    answers ``stale`` or ``unknown`` is not a currency read).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")

        assert entity_currency(proj.g, "ev-1")["status"] == "current"
    finally:
        sdk.close()


def test_stale_entity_survives_the_read():
    """Input: an Event whose source moved (a ``stale`` verdict).

    FAILS IF the read has a side effect: §4.6 — *superseded, never deleted* —
    so the entity and its link are both still there after a stale verdict, and
    **neither operand moved**. Counting nodes alone is not enough: the
    destructive form here is a "helpful" auto-refresh of the note
    (``SET ref.sourceVersion = s.contentHash``), which leaves the node counts
    and the link intact while making every later read report ``current`` and
    defeating the whole feature — so the note and the source's hash are
    snapshotted too.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        _set_hash(proj, "h2")
        before_nodes = (_count(proj, "Event"), _count(proj, "Source"))
        before_note = _note_and_hash(proj)

        assert entity_currency(proj.g, "ev-1")["status"] == "stale"

        assert (_count(proj, "Event"), _count(proj, "Source")) == before_nodes, (
            "the currency read must not delete or mint anything")
        assert read_entity_links(proj.g, "ev-1"), "the link must survive too"
        assert _note_and_hash(proj) == before_note == ("h1", "h2"), (
            "the read must not refresh the note or rewrite the source's hash — "
            f"before={before_note!r} after={_note_and_hash(proj)!r}")
    finally:
        sdk.close()


def test_object_link_is_unknown_not_current():
    """Input: an Object linked to a source that then moved 'h1' → 'h2'.

    FAILS IF the Object's property-free identity link is reported as ``current``
    (the false-current #2350/§4.6 exist to prevent), or if this read is used to
    justify stamping an anchor onto an identity link (owner option A keeps it
    property-free — see ``test_source_version_references_5199.py``).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "obj-1", "Object")
        _set_hash(proj, "h2")

        verdict = entity_currency(proj.g, "obj-1")
        assert verdict["status"] == "unknown", verdict
        assert [lk["currency"] for lk in verdict["links"]] == ["unknown"], verdict
        # The read finds the LINK (so "no reference" and "an unverifiable
        # reference" stay distinguishable) and reports the empty note as such.
        assert verdict["links"][0]["recorded"] == ""
        assert verdict["links"][0]["source"] == "src.txt"
    finally:
        sdk.close()


def test_entity_with_no_reference_is_unknown():
    """Input: an Event no Source references at all.

    FAILS IF an empty link set reads ``current`` — the anti-vacuity rule
    ``aggregate_currency`` already applies to Points (§4.6: *current only when
    EVERY link is*, which is vacuously true of no links and must be refused).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")

        verdict = entity_currency(proj.g, "ev-1")
        assert verdict["status"] == "unknown", verdict
        assert verdict["links"] == [], verdict["links"]
        # The single-entity wrapper's ABSENT state is `[]` — nothing references
        # this entity — and it must stay a real list, not become the refusal's
        # `None` (the two are pinned apart in the ambiguity test below).
        assert read_entity_links(proj.g, "ev-1") == []
    finally:
        sdk.close()


def test_any_stale_link_outranks_an_unknown_sibling():
    """Input: TWO sources reference the Event; one moved, one is unannotated.

    FAILS IF a sibling's unfillable pair rehabilitates a link that is provably
    behind (§4.6: ``stale`` OUTRANKS ``unknown``), or if the verdict loses WHICH
    source moved — the disclosure "*older fact from source X*" needs the source.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        proj.g.query(
            "CREATE (s:Source {url:'other.txt', sourceKind:'document', "
            "title:'other.txt', contentHash:'z1', ingestedAt:'2024-01-01'})"
        )
        # A second, unannotated link: a raw-Cypher containment edge, exactly the
        # shape `create_edge` documents as carrying no note.
        proj.g.query(
            "MATCH (s:Source {url:'other.txt'}), (e:Event {id:'ev-1'}) "
            "CREATE (s)-[:references]->(e)"
        )
        _set_hash(proj, "h2")

        verdict = entity_currency(proj.g, "ev-1")
        assert verdict["status"] == "stale", verdict
        behind = [lk["source"] for lk in verdict["links"]
                  if lk["currency"] == "stale"]
        assert behind == ["src.txt"], verdict["links"]
    finally:
        sdk.close()


def test_the_batch_read_addresses_several_entities_at_once():
    """Input: an Event and an Object, both referenced by the moved source.

    FAILS IF the read cannot answer for a batch (the search/assembly callers
    need one query, not one per hit) or if it silently drops an entity.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        sdk.link_source_to_entity("src.txt", "obj-1", "Object")
        _set_hash(proj, "h2")

        batch = read_reference_currency_links(proj.g, ["ev-1", "obj-1", "nope"])
        assert sorted(batch) == ["ev-1", "obj-1"], sorted(batch)
        assert [lk["currency"] for lk in batch["ev-1"]] == ["stale"]
        assert [lk["currency"] for lk in batch["obj-1"]] == ["unknown"]
    finally:
        sdk.close()


def test_a_legacy_event_addressed_by_eventId_is_found():
    """Input: a raw-Cypher Event carrying only ``eventId`` (no ``id``).

    FAILS IF the read keys on ``n.id`` alone: the legacy path writes Events
    without an ``id`` (``projection/edges.py::link_source_to_event`` documents
    this shape: *legacy raw-Cypher Events carry no ``id``*), so those entities
    would read ``unknown``
    whether or not their source moved — a silent false-negative.

    The SECOND half is the identity-precedence case the id-only version of this
    test could not see: a node carrying BOTH coordinates must answer under each
    coordinate the caller used. A key derived with ``coalesce(n.id, …)``
    answers only the ``id`` one and returns ``unknown`` — with an empty link
    list — for a caller that addressed the very same node by its ``eventId``.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        proj.g.query("CREATE (e:Event {eventId:'legacy-1', name:'Old'})")
        proj.g.query(
            "MATCH (s:Source {url:'src.txt'}), (e:Event {eventId:'legacy-1'}) "
            "CREATE (s)-[r:references]->(e) SET r.sourceVersion = 'h1'"
        )
        proj.g.query("CREATE (e:Event {id:'uuid-9', eventId:'legacy-9'})")
        proj.g.query(
            "MATCH (s:Source {url:'src.txt'}), (e:Event {id:'uuid-9'}) "
            "CREATE (s)-[r:references]->(e) SET r.sourceVersion = 'h1'"
        )
        _set_hash(proj, "h2")

        assert entity_currency(proj.g, "legacy-1")["status"] == "stale"
        assert entity_currency(proj.g, "legacy-9")["status"] == "stale", (
            "an eventId-addressed node that also carries an id must answer "
            "under the eventId — a derived single key loses it")
        assert sorted(read_reference_currency_links(
            proj.g, ["uuid-9"])) == ["uuid-9"], (
            "the returned key must be a coordinate the caller used")
    finally:
        sdk.close()


def test_a_document_derivation_target_is_addressed_by_url():
    """Input: two document-provenance ``:Source`` shapes, one url-only.

    FAILS IF the read matches only ``id``/``eventId``. A ``:Source`` is
    addressed by ``url`` (``projection/edges.py``: ``key_clause = "{url:$eid}"
    if entity_label == "Source"``), and the two shapes a provenance writer
    actually mints differ in whether an ``id`` exists:

    * ``_upsert_document`` writes ``s.id=coalesce(s.id, $id)`` — id AND url;
    * ``_mint_source_stub`` writes **no** ``id`` at all.

    The url-only case is the one a ``id``-keyed read cannot answer: the note is
    on the edge, the source moved, and the verdict would be ``unknown`` with no
    links — a silent false negative rather than an honest 'no note'.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        # Both document nodes are built by the PRODUCTION WRITERS, not by this
        # test's own `CREATE`: the claim under test is a claim about what those
        # writers mint, so a hand-built fixture would pin nothing but itself
        # (it would stay green if either writer changed shape tomorrow).
        # (a) the `_upsert_document` shape: url AND id.
        proj._upsert_document({
            "id": "doc.md",
            "title": "doc.md",
            "content_hash": "d1",
            "suppress_embedding": True,
        })
        sdk.link_source_to_entity("src.txt", "doc.md", "Document")
        # (b) the stub-minted shape: `link_source_to_entity` auto-creates only
        # the SOURCE side, so the document node is minted by the writer that
        # actually owns that shape — `_mint_source_stub` (the Point provenance
        # path's create path) writes url with NO id.
        _mint_source_stub(proj.g, "stub.md")
        sdk.link_source_to_entity("src.txt", "stub.md", "Document")

        rows = proj.g.query(
            "MATCH (:Source {url:'src.txt'})-[r:references]->(d:Source) "
            "RETURN d.url, d.id IS NULL, r.sourceVersion ORDER BY d.url"
        ).result_set
        by_url = {r[0]: (r[1], r[2]) for r in rows}
        assert by_url == {"doc.md": (False, "h1"), "stub.md": (True, "h1")}, (
            f"expected the document to carry an id and the stub not to; "
            f"got {by_url!r}")

        _set_hash(proj, "h2")
        for url in ("doc.md", "stub.md"):
            verdict = entity_currency(proj.g, url)
            assert verdict["status"] == "stale", (url, verdict)
            assert verdict["links"][0]["source"] == "src.txt", (url, verdict)
    finally:
        sdk.close()


def test_an_ambiguous_address_reads_unknown_not_a_folded_verdict():
    """Input: one address string naming TWO nodes — an Event and a Source.

    FAILS IF the read unions several nodes' links under one address. The fold is
    the wrong answer ``entity_identity.py`` names (*MATCH returning several rows
    … folds two identities into one value*): here the Event's own link is
    CURRENT (recorded while the source held ``h1``) while the unrelated
    ``:Source``'s link is STALE (recorded at ``z1`` before a re-fetch to
    ``h1``), so a fold reports ``stale`` for an entity that is current, naming a
    source the addressed Event never had. An address that resolves to more than
    one node must read ``unknown`` — the honest third answer — never a verdict
    about somebody else.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        # The Event carries a coordinate of its own (`eventId`) so the control
        # below can address it ALONE — without it, "unambiguous on its own" is
        # not reachable and this test would be satisfied by a read that returns
        # nothing for every address.
        proj.g.query("CREATE (e:Event {id:'shared', eventId:'ev-unique'})")
        proj.g.query(
            "CREATE (d:Source {url:'shared', sourceKind:'document', "
            "title:'shared', contentHash:'d1', ingestedAt:'2024-01-01'})")
        # Both links come from the SAME source, recorded at different moments:
        # the Event's note matches the source's current hash, the document's
        # note predates it.
        sdk.link_source_to_entity("src.txt", "shared", "Event")
        proj.g.query(
            "MATCH (s:Source {url:'src.txt'}), (d:Source {url:'shared'}) "
            "CREATE (s)-[r:references]->(d) SET r.sourceVersion='z1'"
        )

        # CONTROL: the Event is unambiguous ON ITS OWN when addressed by the
        # coordinate the other node does not carry — its link is CURRENT. That
        # makes "the two links really do disagree" observable, and proves this
        # read is not simply returning nothing for every address.
        assert entity_currency(proj.g, "ev-unique")["status"] == "current", (
            "control failed: the Event's own link must read current")
        assert entity_currency(proj.g, "shared")["status"] == "unknown", (
            "an address naming two nodes must not answer for either")
        # The refusal keeps its key with a `None` value, so it is not confused
        # with "no inbound reference".
        batch = read_reference_currency_links(proj.g, ["shared"])
        assert batch == {"shared": None}, batch
        assert entity_currency(proj.g, "shared").get("ambiguous") is True, (
            "the refusal must be flagged, not rendered as an empty link list")
        # …and the single-entity WRAPPER must not fold the refusal into the
        # empty list: `None` vs `[]` is the only thing separating "this read
        # declined to pick a node" from "nothing references this entity", and
        # collapsing them is what makes absence read as a mintable fact.
        assert read_entity_links(proj.g, "shared") is None, (
            "the wrapper collapsed the REFUSED state into an empty link list")
    finally:
        sdk.close()


def test_the_entity_fyi_names_the_entity_it_is_about():
    """Input: a stale ENTITY verdict, rendered through the FYI disclosure.

    FAILS IF the disclosure drops the addressed id: `sourced_fyi` and the CLI
    payload read ``point``, and an entity verdict carries ``entity`` — so an
    entity FYI used to render `": stale"` with `"point": ""`. A disclosure that
    cannot say WHAT it is about is the flag-alone shape the ruling rejects
    (#5581).
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        _set_hash(proj, "h2")

        payload = _build_payload(entity_currency(proj.g, "ev-1"),
                                 fyi=True, newer=None)
        # The address keeps the kind it was given: it is NOT relabelled as a
        # Point id, so a consumer can tell an entity address from one.
        assert payload["address_kind"] == "entity", payload
        assert payload["entity"] == "ev-1", payload
        assert "point" not in payload, payload
        assert payload["fyi"]["entity"] == "ev-1", payload["fyi"]
        assert "point" not in payload["fyi"], payload["fyi"]
        assert [s["source"] for s in payload["fyi"]["sources"]] == ["src.txt"]

        # A Point verdict keeps the `point` key it always had (no regression for
        # the #5581 callers). This half goes through the `fyi=True` branch on
        # purpose: `_address` only runs there, so an `fyi=False` assertion would
        # not touch the code under test.
        pt = point_currency(proj.g, "pt-none")
        # A hand-built stale verdict: `point_currency` on an absent Point is
        # `unknown`, and `sourced_fyi` returns None unless a verdict is stale
        # (there is no drift to disclose).
        pt["status"] = "stale"
        pt["links"] = [{"source": "src.txt", "recorded": "h1",
                        "current": "h2", "currency": "stale"}]
        point_payload = _build_payload(pt, fyi=True, newer=None)
        assert point_payload["address_kind"] == "point", point_payload
        assert point_payload["point"] == "pt-none", point_payload
        assert "entity" not in point_payload, point_payload
        assert point_payload["fyi"]["point"] == "pt-none", point_payload["fyi"]
    finally:
        sdk.close()


def test_the_cli_entity_door_reports_a_verdict():
    """Input: an entity graph on disk, read through the operator CLI door.

    FAILS IF `main(["--entity", …])` does not dispatch to the entity read — the
    `--entity` flag is the only user reachable door this change has (the read is
    deliberately not an MCP tool or an SDK method; #4282), so an untested flag
    would leave the whole deliverable unexercised.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    # The path must be the SDK's OWN field, not something probed off the
    # projection: `TortoiseSDK` has no `_path` at all (its attribute is
    # `_db_path`), and `proj._path` is `None` on a URI lane BY CONSTRUCTION —
    # the #1647 test-mode redirect nulls it and falls through to the server's
    # derived graph (`tests/_embedded.py` records the same mechanism as #9209's
    # measured casualty). `sdk._db_path` is the path this SDK was handed on
    # EVERY lane, and the redirect derives its graph name from exactly that
    # path plus the session nonce, so the CLI's own in-process
    # `TortoiseSDK(path)` lands on the same derived graph the fixture wrote.
    db = sdk._db_path
    assert db, "the SDK must expose the store path for the CLI door"
    try:
        proj = _fixture(sdk, content_hash="h1")
        sdk.link_source_to_entity("src.txt", "ev-1", "Event")
        _set_hash(proj, "h2")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            assert cli_main(["--entity", "ev-1", "--db", db]) == 0
        # `main` returns 0 on EVERY path (its only failure mode is an
        # exception), so the exit code alone cannot tell the entity door from
        # the Point door: the same id through the Point read renders `unknown`.
        # The rendered verdict is the discriminating assertion.
        assert "ev-1: stale" in buf.getvalue(), buf.getvalue()

        # The refusal must be VISIBLE at the door: `unknown` with no links is
        # what "nothing references this entity" also renders, so an unlabelled
        # refusal would hide the only case where the read declined to answer.
        proj2 = sdk._get_proj()
        proj2.g.query("CREATE (e2:Event {id:'both', eventId:'both'})")
        proj2.g.query(
            "CREATE (d2:Source {url:'both', sourceKind:'document', "
            "title:'both', contentHash:'d1', ingestedAt:'2024-01-01'})")
        sdk.link_source_to_entity("src.txt", "both", "Event")
        proj2.g.query(
            "MATCH (a:Source {url:'src.txt'}), (d:Source {url:'both'}) "
            "CREATE (a)-[r:references]->(d) SET r.sourceVersion='z1'")
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            assert cli_main(["--entity", "both", "--db", db]) == 0
        assert "refused — the address names more than one node" in buf2.getvalue(), (
            buf2.getvalue())
        # …and the `--fyi` door rebuilds its payload from a fixed key set, so the
        # refusal must be carried into it explicitly or it disappears exactly on
        # the door an operator uses to ask "what drifted?".
        buf3 = io.StringIO()
        with contextlib.redirect_stdout(buf3):
            assert cli_main(["--entity", "both", "--db", db, "--fyi"]) == 0
        assert "refused — the address names more than one node" in buf3.getvalue(), (
            f"--fyi dropped the refusal: {buf3.getvalue()!r}")
    finally:
        sdk.close()


def test_duplicate_nodes_sharing_every_coordinate_are_not_folded():
    """Input: TWO `:Event {id:'dup'}` nodes — raw duplicate corruption.

    FAILS IF the ambiguity check fingerprints the nodes by their PROPERTY VALUES
    instead of the node itself: two nodes carrying identical coordinates are
    indistinguishable by value, so they fold and one address answers with the
    other node's link — the "accuse the wrong entity" shape, here turning a
    current carrier plus a stranger's stale link into ``stale``. `id:'dup'`
    twice is precisely the corruption ``entity_identity._require_unique_id``
    refuses first, so it is a shape the read must survive rather than assume
    away.
    """
    sdk = TortoiseSDK(_tmp("test.db"))
    try:
        proj = _fixture(sdk, content_hash="h1")
        # BOTH nodes carry the SAME coordinates — that is the shape a
        # value-fingerprint cannot tell apart, and the only one that pins the
        # node-identity check (a fixture with differing `eventId`s would pass
        # under a value fingerprint and prove nothing).
        proj.g.query("CREATE (e:Event {id:'dup', eventId:'dup'})")
        proj.g.query("CREATE (e:Event {id:'dup', eventId:'dup'})")
        proj.g.query(
            "CREATE (s2:Source {url:'other.txt', sourceKind:'document', "
            "title:'other.txt', contentHash:'h1', ingestedAt:'2024-01-01'})")
        proj.g.query(
            "MATCH (s:Source {url:'src.txt'}), (e:Event {eventId:'dup'}) "
            "WITH s, e LIMIT 1 "
            "CREATE (s)-[r:references]->(e) SET r.sourceVersion='h2'"
        )
        proj.g.query(
            "MATCH (s:Source {url:'other.txt'}), (e:Event {eventId:'dup'}) "
            "WITH s, e SKIP 1 LIMIT 1 "
            "CREATE (s)-[r:references]->(e) SET r.sourceVersion='zzz'"
        )
        # Both nodes are stale but against DIFFERENT sources, so a fold would
        # report one of them for the other — the read must refuse the address.
        assert entity_currency(proj.g, "dup").get("ambiguous") is True, (
            "two nodes sharing every coordinate must be refused, not folded")
        assert read_reference_currency_links(proj.g, ["dup"]) == {"dup": None}
    finally:
        sdk.close()
