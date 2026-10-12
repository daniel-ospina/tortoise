"""#7209 — a write that did not land must never read as a SUCCESS.

The defect (as reported): a ``TortoiseSDK.create_point`` call returned an id and
the Point then was not in the graph, with no error at the point of writing —
against a graph that was concurrently being replaced/rebuilt. That is a silent
loss on the fleet's durable memory: the writer holds a value that looks like
success and only a LATER read reveals the loss.

Two halves of the remedy are pinned here, in the order the issue asks for them.

1. **Retry internally on replacement.** The ``CREATE`` already rides the bounded
   write-retry primitive (#7405 / #7685) — but the FOLLOW-UP writes of the same
   create did not: ``_sync_tags`` and ``_link_source`` were bare
   ``proj.g.query`` calls, so a rebuild's per-query abort landing there aborted
   the write AFTER the Point had landed and left it half-materialized (no
   ``TAGGED`` / no ``extractedFrom`` edge). A Point no provenance query can
   reach is the same "the map loses a finding" outcome by a narrower route.
   They now ride the same funnel. Every statement in those two helpers is a
   MERGE (plus ``_mint_source_stub``'s guarded ``ON MATCH`` adopt, which is a
   documented no-op on re-run), so re-issuing one after an engine ABORT cannot
   duplicate anything — the abort means the statement did not land, which is the
   contract ``retryable_aborted_write`` is built on.

2. **Fail loudly with no id returned.** ``create_point`` already read the Point
   back, but it RETURNED whatever that read returned — including an EMPTY dict,
   raising nothing. ``{}`` is indistinguishable, to a caller, from a successful
   create. It now verifies its own write and raises ``WriteNotLandedError``,
   naming the id and the graph, and it does so BEFORE the ``PointAdded`` event
   is journaled — so no journal record exists for a Point the graph never held,
   and a rebuild cannot resurrect one.

Why the guard is not made redundant by the retry: the retry is state-gated on
the graph KEY being present (#7685), so a rebuild whose recreate has already
landed is retried INTO the rebuilt graph, and if the rebuild's own replay then
replaces the graph again the statement is gone while the writer was told it
succeeded. Closing that window completely needs an engine-level identity token
(#7685's escalation line); refusing to REPORT the success is what the SDK can do.

Lane: runs on the live graph when ``TORTOISE_DB_URI`` is set (the docker lane)
and on embedded FalkorDBLite otherwise — the same dual-lane convention as
``test_retrieval.py::test_graph_exists_works_through_a_real_projection``.
"""
from __future__ import annotations

import contextlib
import os
import uuid

import pytest
import redis.exceptions as redis_exc

from tortoise.exceptions import WriteNotLandedError
from tortoise.sdk import TortoiseSDK

# The MEASURED v6 literal (see #7685): one sentence for two realities — a
# rebuild and a plain deletion. Which one it was is decided by the graph KEY's
# presence (`EXISTS`), never by this text.
_DELETED_OR_REPLACED = (
    "graph was deleted or replaced while the query was running, aborting")

# The statement each follow-up write issues, matched on a stable fragment.
_TAG_WRITE_NEEDLE = "MERGE (t:Tag"
_EDGE_WRITE_NEEDLE = "MERGE (n)-[r:extractedFrom]->(s)"


class _AbortOnceGraph:
    """Delegate every statement, aborting the first *aborts* matching ones.

    Models the measured engine race (#7405 / #7685): the statement is refused
    with ``ResponseError('graph was deleted or replaced …')`` while the graph
    key is still present (a rebuild), so a correct write path RE-ISSUES it.

    ``present`` answers the SDK's own ``EXISTS <name>`` probe — the discriminator
    the retry is gated on. ``applied`` counts matching statements that actually
    reached the real graph, so "retried and applied exactly once" is MEASURED
    rather than assumed.
    """

    def __init__(self, real, needle: str, *, aborts: int = 1,
                 present: bool = True) -> None:
        self._real = real
        self._needle = needle
        self._aborts = aborts
        self._present = present
        self.matching = 0
        self.applied = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching <= self._aborts:
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            self.applied += 1
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1 if self._present else 0
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _sdk(tmp_path, tag: str):
    """A real SDK on the configured lane (a live URI, else embedded)."""
    uri = os.environ.get("TORTOISE_DB_URI")
    return TortoiseSDK(
        None if uri else str(tmp_path / f"{tag}.db"),
        namespace=f"test_7209_{tag}_{uuid.uuid4().hex[:8]}",
    )


def _count(proj, cypher: str, **params) -> int:
    rows = proj.g.query(cypher, params=params or None).result_set
    return rows[0][0] if rows else 0


# ── (2) FAIL LOUDLY: never return a success value for a write that is not there ──


def test_create_point_raises_when_its_own_write_is_not_readable(tmp_path):
    """#7209, as BEHAVIOR: an unreadable read-back is an ERROR, not a result.

    The read-back is the SDK's own proof that the write landed. Returning the
    empty answer is what made a discarded write read as success; the write is
    now verified and the error names the Point and the graph.
    """
    sdk = _sdk(tmp_path, "notlanded")
    try:
        proj = sdk._get_proj()
        # The reported shape, injected at the one place it is observable: the
        # CREATE succeeded and the Point is NOT readable on the read-back that
        # immediately follows (a replacement between the write and the read).
        sdk.get_point = lambda _id: {}

        with pytest.raises(WriteNotLandedError) as ei:
            sdk.create_point("observation", "a finding that never landed")

        msg = str(ei.value)
        assert ei.value.graph == proj.graph_name
        assert ei.value.point_id and ei.value.point_id in msg
        assert proj.graph_name in msg
        assert "did not land" in msg
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_create_point_journals_no_point_that_is_not_there(tmp_path):
    """The guard fires BEFORE the journal write — no ``PointAdded`` for a ghost.

    A ``PointAdded`` recorded for a Point the graph does not hold is a journal
    record with no node behind it: a rebuild would "restore" a Point that never
    existed, and `derived == replay(journal)` would be false in the other
    direction. The guard sits before ``_emit_event`` so this cannot happen.
    """
    sdk = _sdk(tmp_path, "nojournal")
    try:
        emitted: list[str] = []
        real_emit = sdk._emit_event

        def _spy(type_, *a, **k):
            emitted.append(type_)
            return real_emit(type_, *a, **k)

        sdk._emit_event = _spy
        sdk.get_point = lambda _id: {}

        with pytest.raises(WriteNotLandedError):
            sdk.create_point("observation", "another finding that never landed")

        assert "PointAdded" not in emitted, (
            "a PointAdded event was journaled for a Point the graph does not "
            "hold — the guard must sit before the journal write (#7209)")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_create_point_reports_the_point_when_the_write_lands(tmp_path):
    """The guard is not a false positive: a landed write returns the Point.

    Pinned because the fail-loud arm is worthless if it also fires on success —
    every healthy create would raise.
    """
    sdk = _sdk(tmp_path, "landed")
    try:
        result = sdk.create_point("observation", "a finding that lands")
        assert result and result.get("id"), (
            "a landed write must return the Point, not an error and not `{}`")
        assert sdk.get_point(result["id"])["content"] == "a finding that lands"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── (1) RETRY INTERNALLY: the create's follow-up writes ride the same funnel ──


def test_a_rebuild_abort_on_the_tag_write_is_retried_and_applies_once(tmp_path):
    """#7209: the TAGGED-edge write must not abort the create mid-way.

    Pre-fix this statement was a bare ``proj.g.query``: a rebuild abort raised
    out of ``create_point`` AFTER the Point had landed, leaving a Point with no
    tag edges — unreachable by every tag read, i.e. a finding the map lost
    (silently, from the reader's side: the Point is there, the query surface
    that should find it is not).
    """
    sdk = _sdk(tmp_path, "tagretry")
    try:
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _TAG_WRITE_NEEDLE, present=True)
        proj.g = proxy

        result = sdk.create_point("observation", "tagged finding",
                                  tags=["alpha"])

        assert result.get("id"), "the create must succeed once the abort heals"
        assert proxy.matching >= 2, "the aborted statement was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued MERGE must not duplicate")
        proj.g = proxy._real
        assert _count(proj,
                      "MATCH (p:Point {id:$id})-[:TAGGED]->(t:Tag) RETURN count(t)",
                      id=result["id"]) == 1, "the tag edge must be present"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_provenance_write_is_retried_and_applies_once(
        tmp_path):
    """#7209: the ``extractedFrom`` edge write is retried too.

    Same shape as the tag case, and the consequence is worse: a Point whose
    provenance edge is missing is invisible to every source-scoped read and to
    the source-tier calibration that depends on the edge existing.
    """
    sdk = _sdk(tmp_path, "edgeretry")
    try:
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _EDGE_WRITE_NEEDLE, present=True)
        proj.g = proxy

        result = sdk.create_point("observation", "sourced finding",
                                  extractedFrom="https://example.test/7209")

        assert result.get("id"), "the create must succeed once the abort heals"
        assert proxy.matching >= 2, "the aborted edge write was never re-issued"
        assert proxy.applied == 1, "exactly one application — no duplicate edge"
        proj.g = proxy._real
        assert _count(proj,
                      "MATCH (p:Point {id:$id})-[:extractedFrom]->"
                      "(s:Source) RETURN count(s)",
                      id=result["id"]) == 1
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_deleted_graph_abort_on_a_followup_write_stays_loud(tmp_path):
    """The retry must not MASK a deletion — the #7685 guarantee, extended.

    With the graph key ABSENT the engine's sentence means a plain deletion, and
    re-issuing would write into a graph FalkorDB auto-created EMPTY and report
    SUCCESS against a graph the caller never wrote to (#6666 class). The abort
    therefore surfaces unchanged.
    """
    sdk = _sdk(tmp_path, "deleted")
    try:
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _TAG_WRITE_NEEDLE, present=False)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.create_point("observation", "finding on a deleted graph",
                             tags=["alpha"])

        assert proxy.applied == 0, (
            "a deleted graph must not be re-issued into — nothing may be "
            "reported as applied")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
