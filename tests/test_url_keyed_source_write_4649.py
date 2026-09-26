"""#4649 — a url-keyed ``:Source`` is writable, not just readable.

CLASS DEFINITION (this is what the suite is for): the READ path
(``_get_entity`` → ``FalkorProjection._resolve_entity``, whose own comment names
the case — *"Source matches by id and/or url (url-only ingestion stubs from
``_link_source`` have no id)"*) addresses a ``:Source`` by ``url``, while the
WRITE paths matched ``Source {id:$id}`` only. A url-only Source therefore
matched no write branch, and ``_update_entity``'s final
``return self._get_entity(id_val)`` resolved the node BY URL and handed the
caller the UNCHANGED node — success and no-op were indistinguishable. ``delete``
did not even find the node (``False``).

CLASS B (mechanical conformance, per the repo test doctrine). The decision —
a ``:Source``'s identity is ``id`` OR ``url``, because the read path has always
promised it — is MADE by #4649. It is not a hypothesis under test here. So each
test below states:
  (1) the value/state that makes it fail, and
  (2) that this state is reachable IN THE FIXTURE.

Reachability of the failing state (a ``:Source`` node with NO ``id`` and a
``url``): it is minted by the provenance path itself, never by hand —
``create_point(..., extractedFrom=<url>)`` → ``_link_source`` →
``_mint_source_stub`` (``tortoise/projection/edges.py``) MERGEs
``(:Source {url:$url})`` and sets no ``id``; ``link_source_to_entity``
(the ``DocumentCreated`` auto-wire) minted the corpus Source the same way. A
``create_source`` Source is NOT this shape: ``_upsert_source`` sets
``s.id = coalesce($id, $url)`` ON CREATE. Both shapes are exercised below.

The residual this suite does NOT paper over is pinned as a ``strict=True``
xfail and cited to its root: a url-only Source whose only creation carrier is a
Point's ``extractedFrom`` is minted during ``rebuild_all``'s PASS 2, while
``EntityMutated`` folds run in pass 1b — the pass-ordering class consolidated at
**#5048** (see ``TestPassTwoMintOrderingResidual``).

Runnable with the docker lane:
  TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_test_matrix' \\
    uv run pytest tests/test_url_keyed_source_write_4649.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

import pytest

from tortoise.api import EventAPI
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

# The one journaled write-contract record type (#3299).
_REC = "EntityMutated"
# The fold's "I could not replay this mutation" marker (#3299 non-folded set).
_MISS = "fold matched no entity"


@pytest.fixture
def env(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired (mirrors
    ``tests/test_entity_delete_rebuild.py``)."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "urlkeyed.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _rows(proj, cypher: str, **params):
    return proj.g.query(cypher, params=params or None).result_set


def _journal(events) -> list[dict]:
    return EventLog(str(events / "events.jsonl")).read_all()


def _mutations(events) -> list[dict]:
    return [e for e in _journal(events) if e.get("type") == _REC]


def _fold_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if _MISS in r.getMessage()]


def _stub(proj, url: str):
    """The url-only stub's (id, status) — ``id`` must be None for this class."""
    rows = _rows(proj, "MATCH (s:Source {url:$u}) RETURN s.id, s.status", u=url)
    assert rows, "fixture did not mint the Source stub"
    return rows[0]


def _graph_shape(sdk) -> tuple[int, int]:
    """(node count, edge count) — a dry run must leave this untouched."""
    proj = sdk._get_proj()
    return (_rows(proj, "MATCH (n) RETURN count(n)")[0][0],
            _rows(proj, "MATCH ()-[r]->() RETURN count(r)")[0][0])


def _corpus_source(sdk, tmp_path, source_url: str, doc_id: str) -> None:
    """Create the corpus ``:Source`` the way the ingest route does.

    ``EventAPI.add_document`` emits the JOURNALED ``DocumentCreated`` (so a
    rebuild re-creates the document), and its projection apply reaches
    ``_upsert_document`` → ``link_source_to_entity(source_url or id, …,
    "Source")``, which MERGEs a ``:Source {url:$source_url}`` with NO ``id``.
    That makes the corpus Source exist during rebuild pass 1 — i.e. BEFORE the
    pass-1b ``EntityMutated`` folds — which is the precondition the id-carrying
    ``create_source`` shape cannot provide.
    """
    api = EventAPI(EventLog(str(tmp_path / "events" / "events.jsonl")),
                   initiated_by="extractor", agent_id="test",
                   projection=sdk._get_proj())
    api.add_document(doc_id, "Corpus Paper", document_kind="brief",
                     source_url=source_url, suppress_embedding=True)


# ══════════════════════════════════════════════════════════════════════════
# The premise — the READ path addresses a url-only Source (GREEN pre-fix too)
# ══════════════════════════════════════════════════════════════════════════

class TestReadPathPremise:
    def test_get_entity_resolves_a_url_only_source(self, env):
        """(1) The state is a ``:Source`` node with ``id`` ABSENT and the
        address in ``url``; (2) reachable because ``create_point``'s
        ``extractedFrom`` mints exactly that node via ``_link_source``."""
        sdk, _ = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)

        node_id, _status = _stub(sdk._get_proj(), url)
        assert node_id is None, (
            "the fixture minted an id-carrying Source — it does not exercise "
            "the url-only identity this suite is about")

        got = sdk.get_entity(url)
        assert got.get("url") == url, (
            "get_entity must resolve a url-only Source (the read path)")

    def test_an_id_keyed_source_carries_id_equal_to_url(self, env):
        """The OTHER shape, for contrast: ``create_source`` sets
        ``s.id = coalesce($id, $url)`` ON CREATE, so it is id-addressable."""
        sdk, _ = env
        url = "https://example.com/keyed"
        sdk.create_source(url, "document")
        node_id, _ = _stub(sdk._get_proj(), url)
        assert node_id == url


# ══════════════════════════════════════════════════════════════════════════
# The defect — the write paths ignored the url identity (RED pre-fix)
# ══════════════════════════════════════════════════════════════════════════

class TestUrlKeyedSourceIsWritable:
    def test_update_entity_writes_a_url_only_source_and_journals(self, env):
        """(1) The failing value is ``status``: pre-fix the stored node keeps
        ``None`` and NO ``EntityMutated`` record is emitted while the call
        returns the unchanged node as success; (2) the fixture state is the
        ``extractedFrom`` stub minted by ``create_point`` — a node with
        ``id`` absent."""
        sdk, events = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)
        assert _stub(sdk._get_proj(), url)[1] is None

        returned = sdk.update_entity(url, status="retired")

        assert _stub(sdk._get_proj(), url)[1] == "retired", (
            "update_entity on a url-keyed Source wrote nothing — the stored "
            "node is unchanged while the call reported success (#4649)")
        assert returned.get("status") == "retired", (
            "the returned node must be the updated one — returning the "
            "pre-write node is the silent-success signature")
        recs = [r for r in _mutations(events) if r.get("id") == url]
        assert recs and recs[0]["label"] == "Source", (
            f"the write must journal an {_REC} label='Source' record; got "
            f"{recs!r}")
        assert recs[0]["op"] == "restatus" and recs[0]["state"] == {
            "status": "retired"}, recs[0]

    def test_the_consolidated_update_entry_point_routes_by_url(self, env):
        """``SDK.update()`` is the canonical entry (epic #888 W2). Pre-fix it
        resolved WITHOUT ``by_url`` and returned ``{}``; the failing value is
        the empty return / unchanged ``status``; reachable through the same
        ``extractedFrom`` stub."""
        sdk, _ = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)

        returned = sdk.update(url, status="retired")

        assert returned, "update() must resolve a url-keyed Source, not return {}"
        assert _stub(sdk._get_proj(), url)[1] == "retired"

    def test_delete_finds_and_removes_a_url_only_source(self, env):
        """(1) The failing value is the ``False`` return + the node's
        continued existence + the absent delete record; (2) reachable through
        the ``extractedFrom`` stub."""
        sdk, events = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)
        proj = sdk._get_proj()
        assert _rows(proj, "MATCH (s:Source {url:$u}) RETURN s.url", u=url)

        assert sdk.delete(url) is True, (
            "delete() must find a url-keyed Source — pre-fix it resolved "
            "without by_url and returned False (#4649)")
        assert not _rows(proj, "MATCH (s:Source {url:$u}) RETURN s.url", u=url)

        recs = [r for r in _mutations(events)
                if r.get("id") == url and r.get("op") == "delete"]
        assert recs and recs[0]["label"] == "Source", (
            f"the delete must journal an {_REC} op=delete label='Source' "
            f"record; got {recs!r}")

    def test_delete_entity_also_finds_a_url_only_source(self, env):
        """The entity-scoped door (``delete_entity`` → ``_delete_entity``) is
        the implementation both entry points reach; it must honour the same
        OR-set. Failing value: ``False`` + the surviving node."""
        sdk, _ = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)

        assert sdk.delete_entity(url) is True
        assert not _rows(sdk._get_proj(),
                         "MATCH (s:Source {url:$u}) RETURN s.url", u=url)


# ══════════════════════════════════════════════════════════════════════════
# Public-surface sharing (mcp_server delete preview) — no under-reporting
# ══════════════════════════════════════════════════════════════════════════

class TestDeletePreviewAgreesWithTheWriter:
    def test_preview_counts_a_url_only_source(self, env):
        """(1) Failing value is ``nodes_removed`` (0 pre-fix) while the writer
        WOULD delete 1 node; (2) reachable through the ``extractedFrom`` stub.
        The preview must not under-report the blast radius — that is the
        dangerous direction (see ``mcp_server._preview_delete_entity``)."""
        from tortoise.mcp_server import _preview_delete_entity

        sdk, _ = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)

        preview = _preview_delete_entity(sdk, url)
        assert preview["nodes_removed"] == 1, preview
        assert preview["found"] is True, preview

    def test_the_delete_dispatcher_previews_the_url_only_source(self, env):
        """The DISPATCHER behind the canonical destructive tool
        (``tortoise_delete(dry_run=True)`` → ``_preview_delete``), not just the
        leaf preview. It resolves the label itself, so it needs the same
        ``by_url`` the writer got — otherwise it reports ``found=False`` for a
        node ``sdk.delete`` deletes (a dry run that lies on an irreversible
        op), while the (already OR-set-aware) leaf preview disagrees.

        (1) Failing values are ``found is False`` / ``nodes_removed == 0`` and
        a dry run that mutated the graph; (2) reachable through the
        ``extractedFrom`` stub."""
        from tortoise.mcp_server import _preview_delete, _preview_delete_entity

        sdk, _ = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)
        before = _graph_shape(sdk)

        preview = _preview_delete(sdk, url)

        assert preview["found"] is True, (
            "tortoise_delete(dry_run=True) reported the url-keyed Source as "
            "absent while sdk.delete(url) deletes it — the dispatcher resolves "
            "without by_url (#4649)")
        assert preview["nodes_removed"] == 1, preview
        assert preview["nodes_removed"] == _preview_delete_entity(
            sdk, url)["nodes_removed"], (
            "the dispatcher and the leaf preview must agree on the blast radius")
        assert _graph_shape(sdk) == before, "a dry run must never write"


# ══════════════════════════════════════════════════════════════════════════
# The fold honours the same OR-set (replay parity)
# ══════════════════════════════════════════════════════════════════════════

class TestFoldHonoursTheOrSet:
    def test_state_fold_matches_a_url_only_source(self, env):
        """(1) Failing value is the fold's return count (0 pre-fix) and the
        unmet ``status``; (2) the fixture creates the url-only node directly,
        which is the shape ``_mint_source_stub`` writes."""
        sdk, _ = env
        proj = sdk._get_proj()
        proj.g.query("CREATE (:Source {url:'u-only'})")

        matched = proj._fold_entity_mutation({
            "type": _REC, "op": "restatus", "label": "Source",
            "id": "u-only", "state": {"status": "retired"},
        })

        assert matched == 1, "the state fold must match by url, not only by id"
        assert _stub(proj, "u-only")[1] == "retired"

    def test_scoped_delete_fold_matches_a_url_only_source(self, env):
        """(1) Failing value is the deleted-row count (0 pre-fix); (2) same
        directly-created url-only node."""
        sdk, _ = env
        proj = sdk._get_proj()
        proj.g.query("CREATE (:Source {url:'u-only'})")

        assert proj._delete_entity_by_id("u-only", "Source") == 1
        assert not _rows(proj, "MATCH (s:Source {url:'u-only'}) RETURN s.url")

    def test_a_url_keyed_source_that_exists_at_fold_time_survives_rebuild(
            self, env, tmp_path, caplog):
        """(1) Failing values are the reverted ``status`` and a fold-miss
        warning; (2) the corpus Source is reachable via the JOURNALED
        ``DocumentCreated`` route, which mints it in rebuild pass 1 — before
        the pass-1b folds — so this shape is durable end to end."""
        sdk, events = env
        proj = sdk._get_proj()
        url = "https://corpus.example.com/paper"
        _corpus_source(sdk, tmp_path, url, "doc_1")
        assert _stub(proj, url)[0] is None, "corpus Source must be url-only"

        sdk.update_entity(url, status="retired")
        assert _stub(proj, url)[1] == "retired"

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            proj.rebuild_all(str(events))

        assert _fold_warnings(caplog) == [], (
            f"a valid journal produced a fold-miss: {_fold_warnings(caplog)}")
        assert _stub(proj, url)[1] == "retired", (
            "rebuild_all reverted a journaled mutation of a url-keyed Source")

    def test_delete_of_a_url_keyed_corpus_source_survives_rebuild(
            self, env, tmp_path, caplog):
        """The delete half of the same shape: (1) failing value is the
        resurrected node after rebuild; (2) reachable via the journaled
        ``DocumentCreated`` corpus Source."""
        sdk, events = env
        proj = sdk._get_proj()
        url = "https://corpus.example.com/paper2"
        _corpus_source(sdk, tmp_path, url, "doc_2")

        assert sdk.delete(url) is True
        assert not _rows(proj, "MATCH (s:Source {url:$u}) RETURN s.url", u=url)

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            proj.rebuild_all(str(events))

        assert _fold_warnings(caplog) == [], _fold_warnings(caplog)
        assert not _rows(proj, "MATCH (s:Source {url:$u}) RETURN s.url", u=url), (
            "the deleted url-keyed corpus Source resurrected on rebuild_all")


# ══════════════════════════════════════════════════════════════════════════
# No regression on the shapes the OR-set widening touches
# ══════════════════════════════════════════════════════════════════════════

class TestNoRegressionOnExistingShapes:
    def test_id_keyed_source_writes_once_and_journals_once(self, env):
        """``id == url`` on a ``create_source`` node: the primary key matches,
        so the secondary branch must NOT also fire — a double write would
        journal TWO records for one mutation (and the fold would apply twice).

        (1) Failing value is ``len(records) != 1``; (2) the fixture's
        ``create_source`` sets ``id = url``, the ambiguous shape."""
        sdk, events = env
        url = "https://example.com/keyed"
        sdk.create_source(url, "document")

        sdk.update_entity(url, status="retired")

        recs = [r for r in _mutations(events) if r.get("id") == url]
        assert len(recs) == 1, (
            f"one mutation must journal ONE record, got {recs!r}")
        assert _stub(sdk._get_proj(), url)[1] == "retired"

    def test_id_keyed_source_delete_journals_once(self, env):
        """Same shape on the delete path — a double ``DETACH DELETE`` would
        journal two delete records."""
        sdk, events = env
        url = "https://example.com/keyed"
        sdk.create_source(url, "document")

        assert sdk.delete(url) is True
        recs = [r for r in _mutations(events)
                if r.get("id") == url and r.get("op") == "delete"]
        assert len(recs) == 1, recs

    def test_an_absent_id_is_still_a_silent_no_op(self, env):
        """The pre-existing contract the fix must not break: an id that
        resolves nowhere performs no write, journals nothing, and does not
        raise (the OR-set widening must not make a miss an error).

        (1) Failing value is a raised exception / a phantom record; (2) the id
        is simply absent from an otherwise populated graph."""
        sdk, events = env
        sdk.create_point("statement", "an unrelated claim")

        assert sdk.update_entity("no-such-id", status="retired") == {}
        assert sdk.update("no-such-id", status="retired") == {}
        assert sdk.delete("no-such-id") is False
        assert _mutations(events) == []

    def test_point_update_still_journals_only_the_annotator_dim(self, env):
        """The Point branch (status/annotator) is untouched by the OR-set
        widening — a Point has one identity key. (1) Failing value is a
        missing ``PointRevised`` or a stray Source record; (2) a Point with no
        Source linkage."""
        sdk, events = env
        pt = sdk.create_point("statement", "claim", annotator_bias=0.5)
        pid = pt["id"]

        sdk.update_entity(pid, annotator_bias=0.77)

        assert _rows(sdk._get_proj(),
                     "MATCH (p:Point {id:$i}) RETURN p.annotator_bias",
                     i=pid)[0][0] == 0.77
        assert [r for r in _mutations(events) if r.get("label") == "Source"] == []


# ══════════════════════════════════════════════════════════════════════════
# The write OR-set and the READ router cannot drift
# ══════════════════════════════════════════════════════════════════════════

class TestIdentityTablesCannotDrift:
    def test_the_read_router_covers_every_write_identity_key(self, env):
        """Every key in the write OR-set must have an ENABLED read branch, or
        the silent no-op this issue fixes is rebuilt for the new key: the
        writers (and both folds) would match it while `sdk.update`/`delete`
        resolution could not route to the node.

        (1) The failing state is a declared write-identity key that
        `_resolve_entity(..., by_url=True)` cannot return; (2) reachability is
        by construction — the loop iterates the DECLARED tables
        (`_CANONICAL_ENTITY_ID_PROPS` + `_CANONICAL_ENTITY_SECONDARY_ID_PROPS`)
        and the fixture creates one minimal node per declared key.
        """
        from tortoise.projection import (
            _CANONICAL_ENTITY_ID_PROPS,
            _CANONICAL_ENTITY_SECONDARY_ID_PROPS,
        )

        sdk, _ = env
        proj = sdk._get_proj()
        declared = (*_CANONICAL_ENTITY_ID_PROPS,
                    *_CANONICAL_ENTITY_SECONDARY_ID_PROPS)
        assert (
            "Source", "url") in declared, (
            "the Source/url identity this suite fixes is no longer declared")
        for i, (label, prop) in enumerate(declared):
            value = f"route-probe-{i}"
            proj.g.query(f"CREATE (n:{label} {{{prop}:$v}})",
                         params={"v": value})
            resolved = proj._resolve_entity(value, by_id=True, by_eventId=True,
                                            by_url=True)
            assert any(r["label"] == label and r["key"] == prop
                       for r in resolved), (
                f"the write/fold identity ({label}, {prop}) has no enabled "
                f"read branch — a write to it would silently no-op (#4649)")


# ══════════════════════════════════════════════════════════════════════════
# The residual this fix does NOT close — pinned, cited, and LOUD
# ══════════════════════════════════════════════════════════════════════════

class TestPassTwoMintOrderingResidual:
    """A url-only ``:Source`` whose ONLY creation carrier is a Point's
    ``extractedFrom`` is re-minted during ``rebuild_all``'s PASS 2
    (``_upsert_point_edges`` → ``_link_source``), while ``EntityMutated``
    records fold in pass 1b — so the fold runs before the node exists and the
    mutation/delete is lost on replay. Same root as the ``EntityLinked``
    deferral (#3664) and the pass-ordering class consolidated at **#5048**
    ("Ordered. the folds consume a monotonic sequence in order;
    deferred, order-insensitive sweeps are removed").

    The LIVE write is fixed by #4649 (asserted above); the gap is LOUD — the
    fold emits its ``fold matched no entity`` warning — so it is disclosed, not
    silent. These pins are ``strict=True``: closing #5048 XPASSes them, which
    FAILS the suite on purpose so the xfail cannot be forgotten.
    """

    @pytest.mark.xfail(strict=True, reason=(
        "#5048 — rebuild_all pass ordering: a pass-2-minted url-only Source "
        "does not exist when the pass-1b EntityMutated fold runs"))
    def test_update_of_a_pass2_minted_url_only_source_survives_rebuild(
            self, env):
        """(1) Failing value is the reverted ``status`` after ``rebuild_all``;
        (2) the fixture is the ``extractedFrom`` stub — the shape whose only
        creation carrier is pass 2."""
        sdk, events = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)
        sdk.update_entity(url, status="retired")

        sdk._get_proj().rebuild_all(str(events))

        assert _stub(sdk._get_proj(), url)[1] == "retired"

    @pytest.mark.xfail(strict=True, reason=(
        "#5048 — rebuild_all pass ordering: the delete fold runs in pass 1b, "
        "before pass 2 re-mints the extractedFrom stub, so the delete is lost"))
    def test_delete_of_a_pass2_minted_url_only_source_survives_rebuild(
            self, env):
        """(1) Failing value is the resurrected stub after ``rebuild_all``;
        (2) same ``extractedFrom``-stub fixture."""
        sdk, events = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)
        assert sdk.delete(url) is True

        sdk._get_proj().rebuild_all(str(events))

        assert not _rows(sdk._get_proj(),
                         "MATCH (s:Source {url:$u}) RETURN s.url", u=url)

    def test_the_pass2_gap_is_loud_not_silent(self, env, caplog):
        """The residual above must not be a second silent loss: the fold's
        #3299 non-folded-set contract requires a WARNING when a journaled
        mutation cannot be replayed. (1) Failing value is an empty warning
        list; (2) the same stub fixture, rebuilt without the xfail's
        assertion on the outcome."""
        sdk, events = env
        url = "https://example.com/report"
        sdk.create_point("statement", "the claim", extractedFrom=url)
        sdk.update_entity(url, status="retired")

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            sdk._get_proj().rebuild_all(str(events))

        assert _fold_warnings(caplog), (
            "the pass-2 ordering gap silently dropped a journaled mutation — "
            "the #3299 non-folded-set contract requires a fold-miss warning")
