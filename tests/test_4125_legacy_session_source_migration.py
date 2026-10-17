"""#4125 — pre-#4005 basename-url session ``:Source`` nodes are migrated.

The defect this file pins, measured at the base commit (see
``TestTheDefectThisRepairs`` — it needs NO import of the module under test, so
its assertions are the fail-before evidence):

* the pre-#4005 hosted commit path minted its session Source at the raw's
  BASENAME (``'session.md'``) — ``os.path.basename(ref.path)``;
* #4005 moved that identity to the canonical ``session:<session_id>`` but did
  NOT rewrite the nodes already minted, so a re-commit of the same session
  leaves TWO ``agentSession`` Sources for one raw, and the legacy one is not
  addressable by ``delete_session`` (it leaks);
* the legacy node is JOURNALED, so it is re-materialised by every whole-journal
  replay — a graph-only migration is silently undone by the next rebuild.

The contract under test (issue #4125):
  (1) after the migration one session with a legacy Source has exactly ONE
      ``agentSession`` Source (``session:<id>``);
  (2) the legacy node's ``extractedFrom`` and ``references`` edges are
      re-pointed — no dangling provenance;
  (3) the migration is idempotent, dry-runnable, and rebuild-durable.

For every mechanical test the two doctrine questions are answered in its own
docstring: (1) what value makes this test fail? (2) is that value reachable from
the fixture? A test whose failing value cannot occur is decorative.

DB-lane (live FalkorDB under ``TORTOISE_DB_URI``) and embedded-safe.
"""
from __future__ import annotations

import importlib.util as _ilu
import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tortoise.session_source_migration as ssm
from tortoise.consistency import recover_from_log
from tortoise.file_indexer import derive_session_source_url
from tortoise.ids import content_hash
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK
from tortoise.session_source_migration import (
    find_legacy_session_sources,
    is_pre_fix_url_hash,
    reconcile_after_replay,
    reconcile_legacy_session_sources,
)

LEGACY_URL = "session.md"
RAW = "the raw session conversation"
SPANS = ["0-10"]

#: Sentinel for "this carrier attributes to the fixture's session" — a bare
#: ``= sid`` default is not expressible (the parameter is inside its own
#: signature).
_USE_SID = "__use_sid__"


# ── fixtures ──────────────────────────────────────────────────────────────


def _ns() -> str | None:
    """Per-test server graph name under a URI (the #5012 fixture seam)."""
    return (f"test_suite_{os.urandom(4).hex()}"
            if os.environ.get("TORTOISE_DB_URI") else None)


@pytest.fixture
def sdk(tmp_path):
    """(sdk, events_dir) — journal wired, so rebuild is exercisable."""
    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(str(tmp_path / "g4125.db"), namespace=_ns(),
                    event_log_path=str(events / "events.jsonl"))
    yield s, events
    s.close()


def g_of(sdk):
    return sdk._get_proj().g


# ── fixture builders (the two write path shapes) ──────────────────────────


def pre_4005_commit(sdk, sid: str, *, doc_id: str = "doc_x1",
                    basename: str = LEGACY_URL, raw: str = RAW,
                    point_content: str = "legacy-era claim",
                    point_session: object = _USE_SID,
                    doc_session: object = _USE_SID) -> str:
    """Reproduce the PRE-#4005 hosted commit for one session.

    ``url = os.path.basename(ref.path)``, ``contentHash = content_hash(url)``
    (the hash-of-the-filename placeholder #4005 removed), the Document
    (:Source + ``sessionId``, written by raw Cypher in the commit path), the
    ``(session Source)-[:references]->(Document)`` link and the point whose
    ``extractedFrom`` names the basename. ``point_session``/``doc_session``
    override the attribution carrier (``None`` = the carrier is absent).
    """
    psid = sid if point_session is _USE_SID else point_session
    dsid = sid if doc_session is _USE_SID else doc_session
    sdk.create_source(basename, "agentSession",
                      contentHash=content_hash(basename),
                      provenance_spans=list(SPANS), is_episodic=True,
                      sourceDate="2026-01-01T00:00:00+00:00")
    g = g_of(sdk)
    if dsid is not None:
        g.query("MERGE (s:Source {url:$did}) "
                "SET s.documentKind='transcript', s.sessionId=$sid",
                params={"did": doc_id, "sid": dsid})
    sdk.link_source_to_entity(basename, doc_id, "Document")
    props = {"extractedFrom": basename, "source_ref": basename}
    if psid is not None:
        props["session_id"] = psid
    sdk.create_point("statement", point_content, **props)
    return basename


def post_4005_commit(sdk, sid: str, *, doc_id: str = "doc_x1",
                     raw: str = RAW) -> str:
    """The post-#4005 write for the SAME session (the canonical identity)."""
    canonical = derive_session_source_url(sid)
    sdk.create_source(canonical, "agentSession", contentHash=content_hash(raw),
                      provenance_spans=list(SPANS), is_episodic=True,
                      source_path=LEGACY_URL,
                      sourceDate="2026-01-01T00:00:00+00:00")
    sdk.link_source_to_entity(canonical, doc_id, "Document")
    return canonical


def corpus_indexed_session_source(sdk, sid: str = "s1",
                                  url: str = "corpus://notes/session.md",
                                  event_id: str = "ev_1") -> str:
    """The OTHER current producer of an ``agentSession`` Source.

    ``sdk._index_process_unit`` writes ``(corpus://… Source)-[:references]->
    (Event)`` with ``sourceKind='agentSession'`` and ``session_id`` on the
    indexed Event — a keyed, LIVE identity that is fully attributable and must
    never be treated as a pre-#4005 candidate (#8229 review, P1).
    """
    sdk.create_source(url, "agentSession", provenance_spans=list(SPANS),
                      is_episodic=True)
    g_of(sdk).query("MERGE (e:Event {id:$eid}) SET e.session_id=$sid",
                    params={"eid": event_id, "sid": sid})
    sdk.link_source_to_entity(url, event_id, "Event")
    return url


# ── read helpers ──────────────────────────────────────────────────────────


def agent_session_urls(sdk) -> list[str]:
    return [r[0] for r in g_of(sdk).query(
        "MATCH (s:Source) WHERE s.sourceKind='agentSession' "
        "RETURN s.url ORDER BY s.url").result_set]


def props(sdk, url: str) -> dict:
    rows = g_of(sdk).query("MATCH (s:Source {url:$u}) RETURN properties(s)",
                           params={"u": url}).result_set
    return dict(rows[0][0]) if rows else {}


def extracted_from(sdk) -> list[tuple[str, str]]:
    return sorted(tuple(r) for r in g_of(sdk).query(
        "MATCH (p:Point)-[:extractedFrom]->(s:Source) "
        "RETURN p.id, s.url").result_set)


def references(sdk) -> list[tuple[str, str]]:
    return sorted(tuple(r) for r in g_of(sdk).query(
        "MATCH (a:Source)-[:references]->(b:Source) "
        "RETURN a.url, b.url").result_set)


def census(sdk) -> dict:
    """A whole-graph signature — the dry-run/idempotency witness."""
    g = g_of(sdk)
    nodes = sorted(
        f"{sorted(labels)}|{props_}" for labels, props_ in
        ((r[0], json.dumps(r[1], sort_keys=True, default=str))
         for r in g.query("MATCH (n) RETURN labels(n), properties(n)").result_set))
    edges = sorted(tuple(r) for r in g.query(
        "MATCH (a)-[r]->(b) RETURN coalesce(a.url,a.id), type(r), "
        "coalesce(b.url,b.id)").result_set)
    return {"nodes": nodes, "edges": edges}


def delete_session_source(sdk, sid: str) -> None:
    """``delete_session`` step 5 — the ONLY Source it deletes (#2002 W6)."""
    g_of(sdk).query("MATCH (src:Source {url:$url}) DETACH DELETE src",
                    params={"url": derive_session_source_url(sid)})


# ══════════════════════════════════════════════════════════════════════════
# 1. The defect, measured — no import of the module under test
# ══════════════════════════════════════════════════════════════════════════


class TestTheDefectThisRepairs:
    def test_pre_fix_then_re_commit_leaves_two_agent_session_sources(self, sdk):
        """The duplicate, and the leaked legacy node, reproduced from the graph.

        (1) FAILS if the pre-#4005 and post-#4005 write shapes converge — the
            two-write sequence below is the only way both identities appear.
        (2) REACHABLE: both writes are the real SDK calls each path makes; the
            legacy one is what the pre-fix hosted commit executed verbatim.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        post_4005_commit(s, "s1")

        assert agent_session_urls(s) == sorted([LEGACY_URL, "session:s1"]), (
            "the fixture must reproduce the #4125 duplicate — exactly one "
            "agentSession Source per raw is the contract under test")

        # The legacy node, not the canonical one, is what holds the provenance.
        assert references(s) == sorted([(LEGACY_URL, "doc_x1"),
                                        ("session:s1", "doc_x1")])
        assert [u for _, u in extracted_from(s)] == [LEGACY_URL]

    def test_delete_session_leaks_the_legacy_node(self, sdk):
        """The second half of #4125: the legacy node outlives a session delete.

        (1) FAILS if the legacy node is reachable by the canonical url — then
            ``delete_session`` (which deletes only ``Source {url:'session:<id>'}``)
            would have removed it too.
        (2) REACHABLE: ``delete_session_source`` is the endpoint's own delete
            statement (hosted_api.py step 5, #2002 W6), not a re-description.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        delete_session_source(s, "s1")

        assert agent_session_urls(s) == [LEGACY_URL], (
            "a delete of session:s1 left the legacy basename Source (and its "
            "references edge) behind — the leak #4125 reports")
        assert references(s) == [(LEGACY_URL, "doc_x1")]


# ══════════════════════════════════════════════════════════════════════════
# 2. Migration — the contract (indicator 1 + 2)
# ══════════════════════════════════════════════════════════════════════════


class TestMigration:
    def test_reconcile_unifies_the_identity_and_repoints_every_edge(self, sdk):
        """Indicators (1) and (2) on the live duplicate.

        (1) FAILS if the legacy node survives, if a second agentSession Source
            remains, or if any edge still points at the basename url.
        (2) REACHABLE: the fixture holds one legacy node with BOTH edge kinds
            (a Document reference and a Point's extractedFrom).
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        canonical = post_4005_commit(s, "s1")

        report = reconcile_legacy_session_sources(g_of(s))

        assert report["candidates"] == [LEGACY_URL]
        assert report["migrated"] == [LEGACY_URL]
        assert report["unattributed"] == [] and report["ambiguous"] == []
        assert report["edges_repointed"] == 2
        assert agent_session_urls(s) == [canonical], "two Sources survived"
        assert props(s, LEGACY_URL) == {}, "the legacy node was not deleted"
        # No dangling provenance: every edge now lands on the canonical node,
        # and the duplicate Document reference collapsed onto the ONE edge the
        # re-commit already held.
        assert extracted_from(s) and all(u == canonical for _, u in extracted_from(s))
        assert references(s) == [(canonical, "doc_x1")]

    def test_reconcile_mints_the_canonical_node_when_the_session_never_recommitted(
            self, sdk):
        """A legacy node with NO canonical twin still converges — and carries.

        (1) FAILS if the migration refuses to mint the canonical identity (the
            node would stay a basename, unreachable by ``delete_session``) or if
            it invents an anchor from the pre-fix ``hash(url)`` placeholder.
        (2) REACHABLE: this fixture is the pre-fix state alone, with a
            ``hash(url)`` contentHash — the placeholder #4005 removed.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")

        report = reconcile_legacy_session_sources(g_of(s))

        assert report["canonical_created"] == ["session:s1"]
        assert agent_session_urls(s) == ["session:s1"]
        p = props(s, "session:s1")
        assert p["sourceKind"] == "agentSession"
        assert p["is_episodic"] is True
        # The W-7 basename rides as a PROPERTY (never the identity), and the
        # legacy spelling is recorded as an alias — the "alias rule" the #4005
        # write site names.
        assert p["sourcePath"] == LEGACY_URL
        assert LEGACY_URL in p["urlAliases"]
        assert p["provenance_spans"] == SPANS
        assert p["sourceDate"] == "2026-01-01T00:00:00+00:00"
        # ⛔ The pre-fix anchor is a hash of the FILENAME, not of the raw: it is
        # REFUSED, so the canonical node keeps an ABSENT anchor (honest) rather
        # than a version stamp that cannot detect a changed/missing raw.
        assert is_pre_fix_url_hash(LEGACY_URL, content_hash(LEGACY_URL))
        assert "contentHash" not in p, (
            "the pre-fix hash(url) placeholder was carried as a raw anchor")

    def test_a_real_legacy_anchor_is_carried_when_the_canonical_has_none(self, sdk):
        """The other arm of the anchor rule: a non-placeholder hash IS carried.

        (1) FAILS if the migration only ever refuses anchors — then a real
            recorded version would be dropped on the floor.
        (2) REACHABLE: the legacy node is stamped with a hash that is NOT
            ``hash(url)`` (the #4005 anchor shape), and the canonical node does
            not exist yet.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        real = content_hash(RAW)
        g_of(s).query("MATCH (s:Source {url:$u}) SET s.contentHash=$h",
                      params={"u": LEGACY_URL, "h": real})

        reconcile_legacy_session_sources(g_of(s))

        assert props(s, "session:s1")["contentHash"] == real

    def test_a_repointed_edge_keeps_its_recorded_source_version(self, sdk):
        """#5199's derivation anchor rides the moved edge verbatim.

        (1) FAILS if the move re-creates the edge without its attrs (the
            ``sourceVersion`` the #5199 anchor gate uses would be lost on every
            migrated provenance edge — a silent provenance downgrade).
        (2) REACHABLE: ``create_point(extractedFrom=...)`` stamps the anchor on
            the edge it writes, and the fixture's point is such an edge.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        anchor = g_of(s).query(
            "MATCH (p:Point)-[r:extractedFrom]->(s:Source {url:$u}) "
            "RETURN r.sourceVersion", params={"u": LEGACY_URL}).result_set[0][0]
        assert anchor, "the fixture must carry a recorded source version"

        reconcile_legacy_session_sources(g_of(s))

        after = g_of(s).query(
            "MATCH (p:Point)-[r:extractedFrom]->(s:Source {url:'session:s1'}) "
            "RETURN r.sourceVersion").result_set
        assert [r[0] for r in after] == [anchor], (
            "the re-pointed edge lost the version it was read from (#5199)")

    def test_every_movable_incoming_relation_is_moved_not_just_extractedfrom(
            self, sdk):
        """The movable set is exercised beyond the one edge type the writers emit.

        (1) FAILS if the incoming sweep only handles ``extractedFrom`` — an
            ``aboutSource`` edge would then be left behind, and either block the
            delete (wrongly reported as unfixable) or dangle on deletion.
        (2) REACHABLE: ``aboutSource`` has no in-tree producer for a session
            Source (the ontology comment in edges.py says so), so the test
            creates it — the realistic out-of-tree/legacy shape.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        g_of(s).query(
            "MERGE (sub:Subject {name:'an-about-source-carrier'}) "
            "WITH sub MATCH (old:Source {url:$u}) "
            "MERGE (sub)-[:aboutSource]->(old)", params={"u": LEGACY_URL})

        report = reconcile_legacy_session_sources(g_of(s))

        assert report["migrated"] == [LEGACY_URL]
        assert [tuple(r) for r in g_of(s).query(
            "MATCH (sub:Subject)-[:aboutSource]->(s:Source) RETURN s.url"
        ).result_set] == [("session:s1",)]

    def test_the_canonical_nodes_own_anchor_and_identity_are_never_overwritten(
            self, sdk):
        """A merge must not rewrite what the canonical node already owns.

        (1) FAILS if the migration lets the legacy metadata win (the canonical
            node's re-commit anchor would move, and ``version`` would churn).
        (2) REACHABLE: the canonical node's hash differs from the legacy one and
            its ``title`` is its own url.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        canonical = post_4005_commit(s, "s1", raw="a DIFFERENT raw body")
        before = props(s, canonical)

        reconcile_legacy_session_sources(g_of(s))

        after = props(s, canonical)
        assert after["contentHash"] == before["contentHash"] != content_hash(LEGACY_URL)
        assert after["version"] == before["version"]
        assert after["title"] == before["title"] == canonical


class TestIdentityDerivation:
    """The canonical url comes from the shared derivator, on the RAW id."""

    def test_a_padded_session_id_derives_the_identity_the_writers_address(
            self, sdk):
        """``session_id=' s1 '`` must derive ``session: s1 ``, never ``session:s1``.

        Layer-1 accepts a padded id VERBATIM (``CommitPayload`` rejects only a
        blank one) and every producer — ``_materialize_session_source``,
        ``derive_session_source_url`` in the commit path, and ``delete_session``
        — addresses the RAW spelling. Stripping the id before concatenating
        ``session:`` minted ``session:s1``, a THIRD identity no writer, reader or
        deleter reaches, and then DELETED the legacy node after moving its
        provenance onto it: the leak #4125 removes, re-created (#8229 review,
        P2).

        (1) FAILS if the canonical url is hand-built from the STRIPPED id:
            ``canonical_created`` reads ``['session:s1']`` and a duplicate
            identity appears in the graph.
        (2) REACHABLE: a padded id passes Layer-1 and is written as given.
        """
        s, _ = sdk
        padded = " s1 "
        pre_4005_commit(s, padded)

        report = reconcile_legacy_session_sources(g_of(s))

        expected = derive_session_source_url(padded)
        assert expected == "session: s1 "
        assert report["canonical_created"] == [expected], (
            "the canonical identity must come from the shared derivator, on "
            "the unmodified session id")
        assert "session:s1" not in agent_session_urls(s), (
            "a third session-Source identity was minted from the stripped id")
        # The padded identity is the WRITER's spelling, so nothing is deleted on
        # a guess: the legacy node keeps its edges and the run reports the
        # reason instead of claiming a successful migration.
        assert report["migrated"] == []
        assert report["ambiguous"] == [LEGACY_URL]
        assert "surrounding whitespace" in report["notes"][LEGACY_URL]
        assert props(s, LEGACY_URL) != {}, "the legacy node was deleted"
        assert [u for _, u in extracted_from(s)] == [LEGACY_URL]

    def test_the_pre_fix_hash_refusal_uses_the_canonical_content_hash(
            self, monkeypatch):
        """The refusal is pinned to ``tortoise.ids.content_hash``, not a copy.

        Re-deriving ``sha256(str(url))`` inline keeps the refusal true only
        until ``content_hash`` changes (algorithm, prefix, normalisation), at
        which point every real pre-fix placeholder would be carried onto the
        canonical node as a current-version anchor (§4.6 / #3998).

        (1) FAILS on a local ``hashlib`` copy: the spy below would never be
            called, so ``calls`` stays empty.
        (2) REACHABLE: the real helper is what the module must call, and the
            spy delegates to it so the assertion stays meaningful.
        """
        calls: list[str] = []

        def spy(text: str) -> str:
            calls.append(text)
            return content_hash(text)

        monkeypatch.setattr(ssm, "content_hash", spy)

        assert is_pre_fix_url_hash(LEGACY_URL, content_hash(LEGACY_URL))
        assert calls == [LEGACY_URL], (
            "is_pre_fix_url_hash does not go through content_hash")


class TestAttribution:
    def test_find_reports_the_candidate_and_the_session_it_is_attributed_to(
            self, sdk):
        """The dry-run surface: candidates + the attribution used.

        (1) FAILS if ``find_legacy_session_sources`` writes, or if it reports a
            candidate without the Point/Document attribution that justifies the
            migration.
        (2) REACHABLE: one Point (``session_id='s1'``) and one Document
            (``sessionId='s1'``) both attach to the legacy node.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")

        found = find_legacy_session_sources(g_of(s))

        assert [c["url"] for c in found] == [LEGACY_URL]
        assert found[0]["sessions"] == ["s1"]
        assert set(found[0]["points"].values()) == {"s1"}
        assert found[0]["references"] == {"doc_x1": "s1"}
        assert found[0]["degree"] == 2

    def test_an_unattributable_legacy_source_is_reported_and_left_in_place(
            self, sdk):
        """No derivable session ⇒ no invented identity, no delete.

        (1) FAILS if the migration deletes (or rewrites) a node whose
            attribution is empty — the value this test exists for is
            ``report['unattributed'] == []`` with the node gone.
        (2) REACHABLE: the Point carries NO ``session_id`` and there is no
            Document, so both attribution carriers are absent.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1", doc_session=None, point_session=None)
        before = census(s)

        report = reconcile_legacy_session_sources(g_of(s))

        assert report["unattributed"] == [LEGACY_URL]
        assert report["migrated"] == [] and report["blocked"] == {}
        assert census(s) == before, "an unattributable node was written to"
        assert agent_session_urls(s) == [LEGACY_URL]

    def test_the_pre_fix_collision_is_split_per_session(self, sdk):
        """ONE legacy node, SEVERAL sessions (the collision #4005 removed).

        (1) FAILS if the reconciler forces a collided node onto a single
            canonical identity — then one session's provenance would be
            re-pointed onto the other's Source.
        (2) REACHABLE: the fixture is exactly the pre-fix failure mode — one
            ``'session.md'`` node two sessions' Documents/Points attached to.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1", doc_id="doc_a", doc_session=None,
                        point_content="claim from s1", point_session="s1")
        g_of(s).query("MERGE (s:Source {url:'doc_b'}) SET s.sessionId='s2'")
        s.create_point("statement", "claim from s2", extractedFrom=LEGACY_URL,
                       source_ref=LEGACY_URL, session_id="s2")

        report = reconcile_legacy_session_sources(g_of(s))

        assert report["candidates"] == [LEGACY_URL]
        assert report["migrated"] == [LEGACY_URL]
        assert sorted(report["canonical_created"]) == ["session:s1", "session:s2"]
        # Each Point's provenance lands on the canonical node of ITS session —
        # asserted against the Point's own content, so a swap cannot pass.
        contents = {r[0]: r[1] for r in
                    g_of(s).query("MATCH (p:Point) RETURN p.id, p.content").result_set}
        expected = {"claim from s1": "session:s1", "claim from s2": "session:s2"}
        assert {contents[pid]: url for pid, url in extracted_from(s)} == expected
        assert agent_session_urls(s) == ["session:s1", "session:s2"]

    def test_a_corpus_indexed_agent_session_source_is_never_a_candidate(self, sdk):
        """The OTHER current producer is excluded, reported, and left intact.

        ``_index_process_unit`` mints an ``agentSession`` ``:Source`` at
        ``corpus://<corpus>/<rel-path>`` and wires ``references`` to an indexed
        Event carrying ``session_id`` — so a ``sourceKind``-only predicate would
        find it FULLY ATTRIBUTABLE, move its ``references`` edge, and
        ``DETACH DELETE`` the corpus node (losing its ``corpus://`` identity,
        ``_searchText`` and ``format``), manufacturing the very duplicate
        #4125 removes (#8229 review, P1).

        (1) FAILS if the candidate predicate is ``sourceKind``-alone: the
            report would list the corpus url as a candidate, the census would
            move, and the source would be gone.
        (2) REACHABLE: the fixture is the indexer's real write shape —
            ``create_source``, an Event with ``session_id``, and
            ``link_source_to_entity(..., 'Event')`` — not a hand-invented one.
        """
        s, _ = sdk
        corpus_url = corpus_indexed_session_source(s, "s1")
        before = census(s)

        report = reconcile_legacy_session_sources(g_of(s))

        assert report["candidates"] == [], (
            "a keyed corpus:// agentSession Source was selected as a legacy "
            "basename candidate")
        assert report["migrated"] == [] and report["canonical_created"] == []
        assert report["other_agent_session"] == [corpus_url], (
            "the excluded producer must be REPORTED, not silently skipped")
        assert census(s) == before, "a non-candidate was written to"
        # The corpus node and its references edge survive byte-for-byte.
        assert agent_session_urls(s) == [corpus_url]
        assert [tuple(r) for r in g_of(s).query(
            "MATCH (a:Source {url:$u})-[:references]->(e:Event) "
            "RETURN e.id", params={"u": corpus_url}).result_set] == [("ev_1",)]

    def test_a_blocked_node_stays_blocked_on_the_next_run(self, sdk):
        """A re-run must not re-diagnose a BLOCKED node as identity-less.

        Run 1 moves every attributable edge off a node with an unmovable
        relation (partial progress is reported, not rolled back), so run 2 holds
        no attribution carrier at all. Computing ``blocking`` after the
        attribution early-exit made run 2 report ``unattributed`` — erasing the
        offending relation type (the only actionable diagnosis) and telling the
        operator "no session derivable", which is false (#8229 review, P2).

        (1) FAILS if ``_rel_types``/``_blocking_rel_types`` run only on the
            attribution path: run 2's ``blocked`` is empty and ``unattributed``
            names the node.
        (2) REACHABLE: ``mysteryRel`` is an out-of-tree relation, and run 1
            provably moves both attributable edges away.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        g_of(s).query("MERGE (o:Object {name:'outsider'}) "
                      "WITH o MATCH (old:Source {url:$u}) "
                      "CREATE (old)-[:mysteryRel]->(o)", params={"u": LEGACY_URL})

        first = reconcile_legacy_session_sources(g_of(s))
        assert first["blocked"] == {LEGACY_URL: ["mysteryRel"]}
        assert first["ambiguous"] == [LEGACY_URL]
        after_first = census(s)

        second = reconcile_legacy_session_sources(g_of(s))

        assert second["candidates"] == [LEGACY_URL]
        assert second["blocked"] == {LEGACY_URL: ["mysteryRel"]}, (
            "the unmovable relation is still the obstacle — it must be named")
        assert second["ambiguous"] == [LEGACY_URL]
        assert second["unattributed"] == [], (
            "the node's identity WAS derivable; only an unmovable relation "
            "blocks it")
        assert "no attributable edges remain" in second["notes"][LEGACY_URL]
        assert census(s) == after_first, "the re-run rewrote a blocked node"

    def test_an_unmovable_relation_blocks_the_delete_and_is_reported(self, sdk):
        """A relation this reconciler does not know is never traded for a guess.

        (1) FAILS if the legacy node is deleted while an unrecognised edge is
            still attached (that edge would be silently dropped), or if the
            blocked node is not surfaced for an operator.
        (2) REACHABLE: ``mysteryRel`` is created by the test — an out-of-tree
            producer's relation, the realistic unknown-edge case.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        g_of(s).query("MERGE (o:Object {name:'outsider'}) "
                      "WITH o MATCH (old:Source {url:$u}) "
                      "CREATE (old)-[:mysteryRel]->(o)", params={"u": LEGACY_URL})

        report = reconcile_legacy_session_sources(g_of(s))

        assert report["blocked"] == {LEGACY_URL: ["mysteryRel"]}
        assert report["ambiguous"] == [LEGACY_URL]
        assert agent_session_urls(s) == sorted(["session:s1", LEGACY_URL])
        # The movable edges were still corrected (partial progress is reported,
        # not rolled back) — no dangling provenance on the surviving node.
        assert all(u == "session:s1" for _, u in extracted_from(s))
        assert [r for r in g_of(s).query(
            "MATCH (old:Source {url:$u})-[:mysteryRel]->(o) "
            "RETURN o.name", params={"u": LEGACY_URL}).result_set] == [["outsider"]]


# ══════════════════════════════════════════════════════════════════════════
# 3. Dry-run + idempotency (issue: "A dry-run report ... before any delete")
# ══════════════════════════════════════════════════════════════════════════


class TestDryRunAndIdempotency:
    def test_dry_run_writes_nothing(self, sdk):
        """``dry_run=True`` is a read: the whole-graph census is unchanged.

        (1) FAILS if any write path ignores the flag (the census would move),
            or if the report does not say the node WOULD be migrated.
        (2) REACHABLE: the fixture state is migratable, so a non-dry run WOULD
            write — the flag is the only difference.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        post_4005_commit(s, "s1")
        before = census(s)

        report = reconcile_legacy_session_sources(g_of(s), dry_run=True)

        assert report["dry_run"] is True
        assert report["candidates"] == [LEGACY_URL]
        assert report["migrated"] == [LEGACY_URL]
        assert report["edges_repointed"] == 2
        assert census(s) == before, "dry_run wrote to the graph"

    def test_reconcile_is_idempotent(self, sdk):
        """A re-run is a no-op — the issue's "re-running must not double-migrate".

        (1) FAILS if the second run finds the node again, moves an edge, or
            changes any node/edge (including a churned version).
        (2) REACHABLE: the first run deletes the candidate, so a re-run's
            observable is exactly the no-op report.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        post_4005_commit(s, "s1")
        reconcile_legacy_session_sources(g_of(s))
        after_first = census(s)

        report = reconcile_legacy_session_sources(g_of(s))

        assert report["candidates"] == [] and report["migrated"] == []
        assert report["edges_repointed"] == 0
        assert census(s) == after_first, "the second run wrote"


# ══════════════════════════════════════════════════════════════════════════
# 4. Rebuild durability — the whole point of the engine hooks
# ══════════════════════════════════════════════════════════════════════════


class TestRebuildDurability:
    def test_rebuild_all_converges_without_a_prior_migration(self, sdk):
        """A wipe+replay of a legacy graph ALONE must leave the canonical node.

        This test imports NOTHING from the module under test, so at the base
        commit it is RED for the durability reason the issue names: replay
        re-materialises the legacy `SourceCreated` line (and `_link_source`
        re-mints the stub from the Point's basename `extractedFrom`), so the
        duplicate comes back.

        (1) FAILS if the reconciliation does not run at the end of the rebuild
            (the value `agent_session_urls(s) == ['session:s1']` is reachable —
            measured as `['session.md', 'session:s1']` before the hook).
        (2) REACHABLE: the fixture's journal holds the pre-fix SourceCreated and
            the PointAdded naming the basename.
        """
        s, events = sdk
        pre_4005_commit(s, "s1")

        s._get_proj().rebuild_all(str(events), confirm_destructive=True)

        assert agent_session_urls(s) == ["session:s1"]
        assert [u for _, u in extracted_from(s)] == ["session:s1"]

    def test_rebuild_all_leaves_no_legacy_candidate_after_migrating(self, sdk):
        """Rebuild after a live migration is still clean (the hook is idempotent).

        (1) FAILS if the hook re-mints a legacy node, or re-runs the migration
            into an error (the report is dropped on failure, so this would
            surface as a legacy url in the census below).
        (2) REACHABLE: the graph was migrated first, so the only legacy-shaped
            record left is the journal's pre-fix line.
        """
        s, events = sdk
        pre_4005_commit(s, "s1")
        post_4005_commit(s, "s1")
        reconcile_legacy_session_sources(g_of(s))

        s._get_proj().rebuild_all(str(events), confirm_destructive=True)

        assert agent_session_urls(s) == ["session:s1"]
        assert find_legacy_session_sources(g_of(s)) == []

    def test_the_rebuild_engine_converges_too(self, sdk):
        """``rebuild`` (the apply()-driven whole-journal engine) agrees.

        (1) FAILS if the sweep was wired into ``rebuild_all`` only — then this
            engine's replay leaves ``session.md`` beside ``session:s1`` and the
            engines disagree on the same journal.
        (2) REACHABLE: same pre-fix journal, replayed through ``rebuild``.
        """
        s, events = sdk
        pre_4005_commit(s, "s1")

        s._get_proj().rebuild(EventLog(str(events / "events.jsonl")),
                              confirm_destructive=True)

        assert agent_session_urls(s) == ["session:s1"]
        assert [u for _, u in extracted_from(s)] == ["session:s1"]

    def test_the_rebuild_engine_labels_its_reconciliation_as_rebuild(
            self, sdk, caplog):
        """The diagnostic names the engine that ran it (#8229 review, P2).

        ``reconcile_after_replay`` prefixes every message with ``engine``; the
        ``rebuild()`` call site passed ``engine="rebuild_all"``, so a rebuild-
        failure investigation was misdirected to the wrong engine. The
        observable is the log record's text, which is the only place the label
        surfaces.

        (1) FAILS if ``rebuild()`` passes the ``rebuild_all`` label: the
            migration record reads ``rebuild_all: migrated …`` instead of
            ``rebuild: migrated …``.
        (2) REACHABLE: the pre-#4005 journal below leaves a legacy candidate
            that this engine's end-of-replay sweep migrates, which emits the
            ``migrated`` record under test.
        """
        s, events = sdk
        pre_4005_commit(s, "s1")

        with caplog.at_level(logging.INFO,
                             logger="tortoise.session_source_migration"):
            s._get_proj().rebuild(EventLog(str(events / "events.jsonl")),
                                  confirm_destructive=True)

        msgs = [r.getMessage() for r in caplog.records
                if r.name == "tortoise.session_source_migration"]
        assert any(m.startswith("rebuild: migrated") for m in msgs), msgs
        assert not any(m.startswith("rebuild_all:") for m in msgs), msgs

    def test_the_recover_from_log_engine_converges_too(self, sdk):
        """``recover_from_log`` (the module-level recovery path) agrees.

        (1) FAILS if the sweep is missing on this engine: the transparent
            recovery would hand back a graph with the legacy duplicate.
        (2) REACHABLE: the graph is wiped to 0 nodes first, which is the only
            state this function rebuilds in (#428's discriminator).
        """
        s, events = sdk
        pre_4005_commit(s, "s1")
        proj = s._get_proj()
        proj._wipe_all_nodes(confirm_destructive=True,
                             operation="test_4125_recover")

        result = recover_from_log(str(events), proj)

        assert result["recovered"] is True, result
        assert agent_session_urls(s) == ["session:s1"]


# ══════════════════════════════════════════════════════════════════════════
# 4b. The unmigrated-source diagnostic count (no double-counting)
# ══════════════════════════════════════════════════════════════════════════


class TestUnmigratedWarning:
    def test_a_blocked_node_is_counted_once_in_the_unmigrated_warning(
            self, sdk, caplog):
        """One unmigrated node, one unit in the warning total (#8229, P2).

        A node blocked by an unmovable relation stays on the graph, so
        ``reconcile_legacy_session_sources`` appends it to ``ambiguous`` —
        ``blocked`` is therefore a SUBSET of ``ambiguous``. The warning total
        summed ``unattributed + ambiguous + blocked``, so one blocked node was
        announced as two. The observable is the formatted log message.

        (1) FAILS if the total re-adds ``blocked``: one node prints ``2 legacy
            session Source(s) could NOT be migrated`` instead of ``1``.
        (2) REACHABLE: the ``mysteryRel`` below genuinely blocks the delete,
            so the node is reported in both ``blocked`` and ``ambiguous``.
        """
        s, _ = sdk
        pre_4005_commit(s, "s1")
        g_of(s).query("MERGE (o:Object {name:'outsider'}) "
                      "WITH o MATCH (old:Source {url:$u}) "
                      "CREATE (old)-[:mysteryRel]->(o)",
                      params={"u": LEGACY_URL})

        with caplog.at_level(logging.WARNING,
                             logger="tortoise.session_source_migration"):
            report = reconcile_after_replay(s._get_proj(), engine="probe")

        # The double-report this test pins: blocked ⊆ ambiguous.
        assert report["blocked"] == {LEGACY_URL: ["mysteryRel"]}
        assert report["ambiguous"] == [LEGACY_URL]
        warnings = [r.getMessage() for r in caplog.records
                    if r.name == "tortoise.session_source_migration"
                    and r.levelno >= logging.WARNING]
        assert len(warnings) == 1, warnings
        assert "1 legacy session Source(s) could NOT be migrated" in warnings[0], \
            warnings[0]
        assert "1 ambiguous" in warnings[0], warnings[0]
        assert "1 blocked by an unmovable relation" in warnings[0], warnings[0]


# ══════════════════════════════════════════════════════════════════════════
# 5. The operator CLI (the issue's migration surface)
# ══════════════════════════════════════════════════════════════════════════

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_PATH = _REPO_ROOT / "graph-scripts" / "4125_migrate_legacy_session_sources.py"
_spec = _ilu.spec_from_file_location("migrate_legacy_session_sources_4125",
                                     str(_CLI_PATH))
_cli = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cli)


class TestOperatorCli:
    def test_dry_run_reports_and_writes_nothing_then_live_migrates(
            self, tmp_path, monkeypatch, capsys):
        """The CLI's two modes against an embedded DB path.

        (1) FAILS if ``--dry-run`` writes (the dry run would migrate), if the
            dry run hides the candidate, or if live mode does not migrate.
        (2) REACHABLE: the embedded path is a supported target (the deployment
            where a pre-#4005 node also lives); ``--dry-run`` is the only
            difference between the two invocations.

        Deliberately NOT the ``sdk`` fixture: that fixture's namespace is minted
        per call, and ``--db <path>`` (scheme-less) addresses the one embedded
        store regardless of graph name, so a locally-built embedded SDK keeps
        the assertion independent of the lane's namespace seam.
        """
        db = str(tmp_path / "cli4125.db")
        # The env change comes FIRST (#8229 review, P1): with a URI still
        # ambient, the SDK construction below hands an EXPLICIT db_path to
        # `FalkorProjection`, which arms the Epic #1647 test redirect — the
        # setup write then lands on the lane's server graph while the
        # scheme-less CLI reads the never-written embedded file (measured:
        # `0 legacy node(s)`). A scheme-less value fails
        # `_is_supported_uri_scheme`, so the redirect is inert for BOTH halves.
        monkeypatch.setenv("TORTOISE_DB_URI", db)
        s = TortoiseSDK(db)
        try:
            pre_4005_commit(s, "s1")
        finally:
            s.close()

        monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, "--dry-run"])
        assert _cli.main() == 0
        out = capsys.readouterr().out
        assert "1 legacy node(s)" in out
        assert "session.md: sessions=['s1']" in out

        s2 = TortoiseSDK(db)
        try:
            assert agent_session_urls(s2) == [LEGACY_URL], "dry run migrated"
            monkeypatch.setattr(sys, "argv", [_CLI_PATH.name])
            assert _cli.main() == 0
            assert agent_session_urls(s2) == ["session:s1"]
        finally:
            s2.close()

    def test_dry_run_reports_the_plan_the_live_run_would_execute(
            self, tmp_path, monkeypatch, capsys):
        """``--dry-run`` must print the real plan, including blocked nodes.

        The dry-run arm used to build its own report with ``migrated`` /
        ``ambiguous`` / ``blocked`` / ``edges_repointed`` hardcoded empty, so the
        self-contradicting summary ``N legacy node(s) → 0 migrated … 0 blocked``
        printed for EVERY graph — including one whose live run migrates a node —
        and the ``⚠️ … unmovable relation(s) … LEFT IN PLACE`` line was
        unreachable in the mode built to be read before the destructive run
        (#8229 review, P2).

        (1) FAILS if the dry-run report is hand-built: the migratable graph
            prints ``0 migrated`` instead of ``1 migrated``, and the blocked
            graph prints ``0 blocked`` with no relation named.
        (2) REACHABLE: two real graphs — one migratable, one blocked by an
            out-of-tree ``mysteryRel``.
        """
        migratable = str(tmp_path / "cli4125_ok.db")
        monkeypatch.setenv("TORTOISE_DB_URI", migratable)
        s = TortoiseSDK(migratable)
        try:
            pre_4005_commit(s, "s1")
        finally:
            s.close()

        monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, "--dry-run"])
        assert _cli.main() == 0
        out = capsys.readouterr().out
        assert "1 legacy node(s) → 1 migrated" in out, out
        assert "0 blocked" in out, out

        blocked_db = str(tmp_path / "cli4125_blocked.db")
        monkeypatch.setenv("TORTOISE_DB_URI", blocked_db)
        s2 = TortoiseSDK(blocked_db)
        try:
            pre_4005_commit(s2, "s1")
            g_of(s2).query(
                "MERGE (o:Object {name:'outsider'}) "
                "WITH o MATCH (old:Source {url:$u}) "
                "CREATE (old)-[:mysteryRel]->(o)", params={"u": LEGACY_URL})
        finally:
            s2.close()

        assert _cli.main() == 0
        out = capsys.readouterr().out
        assert "1 legacy node(s) → 0 migrated" in out, out
        assert "1 ambiguous, 1 blocked" in out, out
        assert "unmovable relation(s) ['mysteryRel'] — LEFT IN PLACE" in out, out

    def test_embedded_target_selection_is_one_graph(self):
        """A scheme-less --db is an embedded store: one graph, named ``''``.

        (1) FAILS if an embedded path is routed through the URI-only opener
            (``from_uri`` refuses a scheme-less value — measured ValueError).
        (2) REACHABLE: ``/tmp/x.db`` has no scheme.
        """
        assert _cli.derive_uri_graph("/tmp/x.db") == ""
        assert _cli.target_graphs("/tmp/x.db", None) == ([""], [])
