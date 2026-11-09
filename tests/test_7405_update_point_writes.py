"""#7405 — the point-mutation verbs ride the bounded write retry.

``tortoise/retry.py``'s header names the residue itself. Before this change it
listed ``update_point`` (and therefore ``annotate_operator``, which delegates to
it) among the UNWIRED surfaces, alongside ``invalidate_point``'s two statements,
which fell under "the remaining direct ``proj.g.query`` write sites". This file
pins every statement group this PR wires — one test per wrapped statement,
not one per verb — so the header can stay the single source of truth without
over-claiming. The two arms that a per-verb reading would have left unpinned
(`:Point:Object` + ``status`` promote, and the derived-embedding clear+set) have
their own tests, each failing on the pre-fix bare ``proj.g.query``.

``update_point`` is the SDK's most common mutation door, so the pre-fix
behaviour is the #7405 asymmetry in its sharpest form: while a rebuild/replace
is in flight the engine aborts every write query with *"graph was deleted or
replaced while the query is running, aborting"*, reads keep succeeding, and the
revision was REJECTED with no retry — the caller's edit is lost from the map
while ``get_point`` still answers.

Why re-issuing each statement is safe (reasoned from the statement's SHAPE, not
measured here — these tests pin that the aborted statement is re-issued and
applied exactly once):

1. the plain arms — an absolute ``SET n += $props`` whose every value
   (``_props_param``, ``now``, ``_embed_params``) was fixed before the first
   attempt, so a re-issue converges on exactly the single-issue state. The
   ``:Object`` plain arm also carries a RELATIVE ``n.version = coalesce(n.version,
   0) + 1``; it cannot double-bump because an abort means the statement did NOT
   land (the predicate's premise) — NOT because the write is absolute;
2. the promote arms — the guard lives INSIDE the statement
   (``WHERE n.status IS NULL OR n.status = 'draft'``), so a re-issue re-reads the
   graph, cannot promote twice, and a legitimately-refused promote still returns
   an empty ``result_set`` (the caller's raise path is unchanged);
3. the per-key loop — one key per statement, each an absolute ``SET``;
4. the derived-embedding clear+set — one statement, absolute;
5. ``_sync_tags`` — ``MERGE`` per tag plus DELETEs by an explicit name, so the
   same tag list converges (``create_point`` already rides this same wrapper);
6. ``invalidate_point`` — an absolute terminal stamp whose values were fixed
   before the attempt (so the window END cannot move) and a ``CORRECTS`` MERGE
   on its endpoints.

The caller-visible bound is one 7-14 s window per ``_graph_write_with_retry``
call site a branch REACHES (never one per verb), which is why the last test here
pins the call count for each shape rather than leaving it to prose: it is the
number an operator sizes a timeout against.

⛔ The retry must NOT mask a deletion. The engine reports the SAME abort for a
rebuild and for a plain deletion, so the graph-abort family is retried only
while the graph KEY is present (``_graph_exists``). That loud arm is a shared
property of ``_graph_write_with_retry`` and is pinned here as well, because the
point-mutation verbs are new callers of it.

Lane: runs on the live graph when ``TORTOISE_DB_URI`` is set (the docker lane)
and on embedded FalkorDBLite otherwise — the same dual-lane convention as
``test_7209_write_landing.py`` and ``test_7405_remaining_writes.py``.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import textwrap
import types
import uuid
from pathlib import Path

import pytest
import redis.exceptions as redis_exc

from tortoise import embeddings
from tortoise.sdk import TortoiseSDK

#: Repo root — this file's source-level pins read the tree, not the graph.
_REPO = Path(__file__).resolve().parent.parent

# The MEASURED v6 literal (see #7685): one sentence for two realities — a
# rebuild and a plain deletion. Which one it was is decided by the graph KEY's
# presence (`EXISTS`), never by this text.
_DELETED_OR_REPLACED = (
    "graph was deleted or replaced while the query was running, aborting")

# The statement each surface issues, matched on a stable fragment.
_POINT_PROP_SET_NEEDLE = "MATCH (n:Point {id:$id}) SET n += $props"
_POINT_PROMOTE_NEEDLE = (
    "n.status = 'live', n.updatedAt = $now, n += $props")
_OBJECT_PROP_SET_NEEDLE = ":Point:Object {id:$id}) SET n += $props"
# The ``:Point:Object`` promote arm carries the version bump BETWEEN ``$now,``
# and ``n +=``, which is why the plain promote needle above does NOT match it —
# the two arms are separate statements and need separate tests (#8854 review).
_OBJECT_PROMOTE_NEEDLE = (
    "n.status = 'live', n.updatedAt = $now, "
    "n.version = coalesce(n.version, 0) + 1, n += $props")
# The derived-embedding arm is the ONLY writer that in-query CLEARS the stored
# vector before setting it, so the clear-prefix fragment identifies it alone
# (the per-key ``SET n += $props`` loop never touches ``n.embedding``).
_EMBEDDING_NEEDLE = (
    "REMOVE n.embedding) SET n.embedding = CASE WHEN $embedding IS NOT NULL")
_TAGGED_NEEDLE = "MERGE (t:Tag {name:$tag}) "
_TAG_GC_NEEDLE = "WHERE NOT (t)<-[:TAGGED]-() DELETE t"
_INVALIDATE_STAMP_NEEDLE = "SET n.outdated = true"
_INVALIDATE_CORRECTS_NEEDLE = "MERGE (a)-[:CORRECTS]->(b)"
_RETRACT_CAS_NEEDLE = "SET n.status = 'retracted'"
# ``promote_point``'s CAS is gated on the literal draft status with the ``n.``
# alias and no ``IS NULL`` arm. It is not the ONLY statement gated on that
# literal (``_promote_incident_operators`` gates its operator CAS on the same
# value with the ``o`` alias), but this exact fragment — alias plus trailing
# space — is unique to this statement.
_PROMOTE_POINT_CAS_NEEDLE = "WHERE n.status = 'draft' "


class _AbortOnceGraph:
    """Delegate every statement, aborting the first *aborts* matching ones.

    Models the measured engine race (#7405 / #7685): the statement is refused
    with ``ResponseError('graph was deleted or replaced …')`` while the graph
    key is still present (a rebuild), so a correct write path RE-ISSUES it.

    ``present`` answers the SDK's own ``EXISTS <name>`` probe — the
    discriminator the retry is gated on. ``applied`` counts matching statements
    that actually reached the real graph, so "retried and applied exactly once"
    is MEASURED rather than assumed.
    """

    def __init__(self, real, needle: str, *, aborts: int = 1,
                 present: bool = True, skip: int = 0,
                 message: str = _DELETED_OR_REPLACED) -> None:
        self._real = real
        self._needle = needle
        self._aborts = aborts
        self._present = present
        self._skip = skip
        # Default is the rebuild literal; a caller can inject the OTHER
        # retryable family (the write-slot arm) to prove the witness is not
        # armed by abort classes that say nothing about node existence.
        self._message = message
        self.matching = 0
        self.applied = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            # ``skip`` lets a test abort a statement that is NOT the first
            # matching one — the discriminator for "is the whole per-key loop
            # one retry budget?" (`test_the_per_key_loop_shares_one_retry_budget`):
            # a single budget re-issues the keys that already landed, a
            # per-key budget does not.
            if self._skip < self.matching <= self._skip + self._aborts:
                raise redis_exc.ResponseError(self._message)
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
        namespace=f"test_7405_up_{tag}_{uuid.uuid4().hex[:8]}",
    )


def _pid(sdk) -> str:
    return sdk.create_point("evidence", f"claim for {uuid.uuid4().hex}")["id"]


# ── 1. the plain property arm (the per-key ``SET`` loop) ─────────────────


def test_a_rebuild_abort_on_the_property_set_is_retried_and_applies_once(
        tmp_path):
    """A rebuild abort on ``update_point``'s property ``SET`` must not lose it.

    Pre-fix the statement was a bare ``proj.g.query``: the per-query abort
    raised straight out of ``update_point``, the revision never landed, and the
    caller's edit survived only in its own transcript — while ``get_point``
    kept answering, so nothing looked broken. That is the #7405 loss.
    """
    sdk = _sdk(tmp_path, "propset")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, confidence=0.42)

        assert proxy.matching >= 2, "the aborted SET was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued absolute SET must not "
            "double-increment")
        proj.g = proxy._real
        assert sdk.get_point(pid)["confidence"] == pytest.approx(0.42), (
            "the revision must be readable after the retry")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 2. the promote arm (``status`` given, guarded WHERE) ─────────────────


def test_a_rebuild_abort_on_the_promote_set_is_retried(tmp_path):
    """The guarded ``draft → live`` ``SET`` is retried, and promotes once.

    The guard rides inside the statement, so re-issuing it re-evaluates the same
    predicate: it cannot promote twice, and a promote the graph legitimately
    refuses still yields an empty result set (the caller's raise path).
    """
    sdk = _sdk(tmp_path, "promote")
    try:
        pid = sdk.create_point(
            "evidence", f"draft claim {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROMOTE_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, status="live")

        assert proxy.matching >= 2, "the aborted promote SET was never re-issued"
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        assert sdk.get_point(pid)["status"] == "live"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 3. the ``:Point:Object`` arm ────────────────────────────────────────


def test_a_rebuild_abort_on_the_object_arm_set_is_retried(tmp_path):
    """The ``:Object``-labelled arm is a SEPARATE statement and is retried too.

    ``update_point`` branches on whether the node carries ``:Object`` (the
    entity arm, which also bumps ``version``). Wrapping only the ``:Point`` arm
    would leave every entity revision losing its write.
    """
    sdk = _sdk(tmp_path, "objarm")
    try:
        pid = _pid(sdk)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _OBJECT_PROP_SET_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, confidence=0.31)

        assert proxy.matching >= 2, "the aborted :Object SET was never re-issued"
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        assert sdk.get_point(pid)["confidence"] == pytest.approx(0.31)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 4. the TAGGED edges ``update_point`` shares with ``create_point`` ────


class _DropOneTag(_AbortOnceGraph):
    """Aborts the first TAGGED MERGE, then lets all but ONE tag land.

    The landed-but-incomplete state the FULL-SET witness exists for: 'alpha'
    reaches the graph, 'beta' does not. Redirecting the swallowed name to a
    throwaway Tag models "this MERGE matched nothing" without inventing a fake
    result object.
    """

    def __init__(self, real, needle: str, drop: str) -> None:
        super().__init__(real, needle, present=True)
        self._drop = drop

    def query(self, cypher, params=None, timeout=None):
        if (self._needle in cypher and isinstance(params, dict)
                and params.get("tag") == self._drop):
            params = {**params, "tag": self._drop + "__never_landed"}
        return super().query(cypher, params=params, timeout=timeout)


class _CountSweeps(_AbortOnceGraph):
    """Counts the orphan-``:Tag`` GC, so "this attempt paid the scan" is measured."""

    def __init__(self, real, needle: str, *, aborts: int = 1) -> None:
        super().__init__(real, needle, aborts=aborts, present=True)
        self.sweeps = 0

    def query(self, cypher, params=None, timeout=None):
        if "WHERE NOT (t)<-[:TAGGED]" in cypher:
            self.sweeps += 1
        return super().query(cypher, params=params, timeout=timeout)


def test_a_half_landed_tag_sync_is_not_a_success(tmp_path, monkeypatch):
    """The witness must require the FULL set of wanted names, not "at least one".

    A retry re-issues the whole reconciler; if it lands SOME of the requested
    tags, the point's property and its derived edges still disagree — exactly
    the state this PR exists to refuse. ``landed >= 1`` would call that good.
    Pinned here because the empty-request test cannot reach the comparison (it
    returns at the ``not _sync_wanted`` guard).
    """
    # Stub the backoff clock: this path EXHAUSTS the retry budget, so a real
    # sleep would add ~8.7 s (measured: 1.64 + 2.34 + 4.73) and make the result
    # clock-dependent. Same stub as the four landing pins above.
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "tagpartial")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _DropOneTag(proj.g, _TAGGED_NEEDLE, drop="7405partial")
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, tags=["7405alpha", "7405partial"])

        assert proxy.matching >= 3, (
            "fixture precondition: the aborted MERGE must have been re-issued")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (:Point {id:$id})-[:TAGGED]->(t:Tag) RETURN t.name "
            "ORDER BY t.name",
            params={"id": pid}).result_set
        landed = [r[0] for r in rows if not str(r[0]).endswith("__never_landed")]
        assert landed == ["7405alpha"], (
            f"fixture precondition: exactly one real tag landed, got {landed}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_the_orphan_sweep_is_a_retry_only_cost(tmp_path):
    """``sweep_orphans=(_sync_attempts > 1)`` — pinned in BOTH directions.

    The narrowing away from main's every-update scan was a deliberate #8854 P3
    decision (a first attempt that drops no edge pays no graph-wide ``:Tag``
    scan), and it was unguarded: reverting it to ``True`` left the suite green.
    A first attempt that removes nothing must issue NO GC; a RETRIED attempt
    must issue it even though its own re-read of ``stale`` finds nothing — that
    second direction is the live-vs-replay divergence the parameter exists to
    prevent.

    The third block pins the CONSEQUENCE the projection docstring states as the
    limit of that saving: an orphan ``:Tag`` from an EARLIER call survives a
    plain tagged update that removed no edge, and is collected by the next call
    that does remove one. Without it that sentence would name a behaviour no
    test measures.
    """
    sdk = _sdk(tmp_path, "sweep")
    try:
        proj = sdk._get_proj()

        pid = _pid(sdk)
        # aborts=0: a CLEAN first attempt. `_AbortOnceGraph` aborts by default,
        # so leaving the default here would measure a retry and prove nothing
        # about the first-attempt saving (caught while writing this test).
        first = _CountSweeps(proj.g, _TAGGED_NEEDLE, aborts=0)
        proj.g = first
        sdk.update_point(pid, tags=["7405sweepone"])
        proj.g = first._real
        assert first.sweeps == 0, (
            "a first attempt that removed no edge paid the graph-wide :Tag "
            "scan — the #8854 P3 saving is gone")

        pid2 = _pid(sdk)
        retried = _CountSweeps(proj.g, _TAGGED_NEEDLE)
        proj.g = retried
        sdk.update_point(pid2, tags=["7405sweeptwo"])
        proj.g = retried._real
        assert retried.matching >= 2, (
            "fixture precondition: the abort must have forced a retry")
        assert retried.sweeps >= 1, (
            "the RETRIED attempt skipped the orphan sweep — an orphan :Tag left "
            "by an earlier attempt survives, so list_tags() reports a count: 0 "
            "entry a rebuilt graph does not have (live != replay)")

        # ── the documented LIMIT of the saving, both ways ───────────────────
        pid3 = _pid(sdk)
        sdk.update_point(pid3, tags=["7405held"])
        # An orphan left by some EARLIER interrupted call: a :Tag with no
        # incoming TAGGED edge.
        proj.g.query("CREATE (:Tag {name: '7405orphan'})")

        plain = _CountSweeps(proj.g, _TAGGED_NEEDLE, aborts=0)
        proj.g = plain
        sdk.update_point(pid3, tags=["7405held"])  # already held: no edge removed
        proj.g = plain._real
        assert plain.sweeps == 0, (
            "a plain tagged update that removed no edge paid the graph-wide "
            ":Tag scan — the saving is gone")
        survived = proj.g.query(
            "MATCH (t:Tag {name: '7405orphan'}) RETURN count(t)",
        ).result_set[0][0]
        assert survived == 1, (
            "the documented limit is that an orphan from an EARLIER call "
            f"survives a plain tagged update, but it was collected: {survived}")

        # ...and a call that DOES remove an edge collects it.
        sdk.update_point(pid3, tags=[])
        collected = proj.g.query(
            "MATCH (t:Tag {name: '7405orphan'}) RETURN count(t)",
        ).result_set[0][0]
        assert collected == 0, (
            "a call that removed a TAGGED edge must sweep the orphan it left "
            f"behind, but {collected} survived")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_tag_sync_is_retried(tmp_path):
    """The tag half of an update is retried on the UPDATE door as well.

    ``create_point`` already rode the retry for this same helper
    (``"create_point TAGGED edges"``); the update door did not, so the same
    mutation behaved differently depending on which verb carried it. A lost tag
    write leaves ``n.tags`` set while ``query_points_by_tag`` cannot see the
    point — the property and its derived edges disagree.
    """
    sdk = _sdk(tmp_path, "tagsync")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _TAGGED_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, tags=["7405alpha", "7405beta"])

        assert proxy.matching >= 3, (
            "the aborted TAGGED MERGE was never re-issued")
        assert proxy.applied == 2, (
            "both tags applied exactly once — a re-issued MERGE cannot double")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (:Point {id:$id})-[:TAGGED]->(t:Tag) RETURN t.name "
            "ORDER BY t.name",
            params={"id": pid}).result_set
        assert [r[0] for r in rows] == ["7405alpha", "7405beta"]
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 5. ``annotate_operator`` — transitive, it delegates to ``update_point`` ──


def test_a_rebuild_abort_during_annotate_operator_is_retried(tmp_path):
    """``annotate_operator`` inherits the retry from ``update_point``.

    It emits ``OperatorAnnotated`` and then delegates to ``update_point``, so
    pre-fix the event landed in the journal while the annotation never reached
    the graph: the replay applies the rating, live does not, and the operator's
    EP weight differs between the live graph and a rebuilt one — a divergence
    rebuild parity is supposed to exclude.
    """
    sdk = _sdk(tmp_path, "annotate")
    try:
        src = _pid(sdk)
        tgt = _pid(sdk)
        op = sdk.create_operator("IMPL", src, [tgt])["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE, present=True)
        proj.g = proxy

        sdk.annotate_operator(op, bias=0.2, precision=0.8,
                              consistency=0.7, directness=0.9)

        assert proxy.matching >= 2, (
            "the aborted annotation SET was never re-issued")
        proj.g = proxy._real
        point = sdk.get_point(op)
        assert point["annotator_precision"] == pytest.approx(0.8)
        assert point["annotator_bias"] == pytest.approx(0.2)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 6. ``invalidate_point`` — the terminal stamp ────────────────────────


def test_a_rebuild_abort_on_the_invalidate_stamp_is_retried(tmp_path):
    """The terminal ``outdated`` stamp is retried, so the window END holds.

    Pre-fix the abort raised AFTER ``PointInvalidated`` had been appended: the
    journal recorded an invalidation the live graph never applied, and the point
    stayed live-and-readable while every replay's fold treated it as terminal.
    """
    sdk = _sdk(tmp_path, "invstamp")
    try:
        pid = _pid(sdk)
        corrector = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _INVALIDATE_STAMP_NEEDLE, present=True)
        proj.g = proxy

        sdk.invalidate_point(pid, corrector)

        assert proxy.matching >= 2, "the aborted stamp SET was never re-issued"
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        assert sdk.get_point(pid)["outdated"] is True
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 7. ``invalidate_point`` — the ``CORRECTS`` edge ─────────────────────


def test_a_rebuild_abort_on_the_corrects_edge_is_retried(tmp_path):
    """The ``CORRECTS`` ``MERGE`` is retried (and cannot double)."""
    sdk = _sdk(tmp_path, "invcorrects")
    try:
        pid = _pid(sdk)
        corrector = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(
            proj.g, _INVALIDATE_CORRECTS_NEEDLE, present=True)
        proj.g = proxy

        sdk.invalidate_point(pid, corrector)

        assert proxy.matching >= 2, "the aborted MERGE was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued MERGE cannot double")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (:Point {id:$new})-[:CORRECTS]->(:Point {id:$old}) "
            "RETURN count(*)",
            params={"new": corrector, "old": pid}).result_set
        assert rows and rows[0][0] == 1
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 8. ``retract_point`` — the terminal CAS ``SET`` ────────────────


def test_a_rebuild_abort_on_the_retract_cas_set_is_retried(tmp_path):
    """The CAS ``status → retracted`` ``SET`` is retried, and retracts once.

    The terminal-transition sibling of ``invalidate_point``: pre-fix the abort
    raised AFTER ``PointRetracted`` had been appended, so the journal recorded a
    retraction the live graph never applied — the point stayed live and voting
    while every replay's fold treated it as terminal. The
    ``_terminal_excluded`` guard rides inside the statement, so a re-issue
    cannot re-retract an already-terminal point.
    """
    sdk = _sdk(tmp_path, "retract")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _RETRACT_CAS_NEEDLE, present=True)
        proj.g = proxy

        retracted = sdk.retract_point(pid)

        assert proxy.matching >= 2, "the aborted CAS SET was never re-issued"
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        assert retracted["status"] == "retracted"
        assert sdk.get_point(pid)["status"] == "retracted"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 9. the ``:Point:Object`` PROMOTE arm (``:Object`` label AND ``status``) ──


def test_a_rebuild_abort_on_the_object_promote_set_is_retried(tmp_path):
    """The ``:Object`` promote arm is a THIRD statement, and is retried too.

    #8854 review P2: without this test the object-promote arm was claimed as
    pinned but nothing reached it — test 2 passes a bare ``:Point``, and test 3
    adds ``:Object`` without a ``status``. The two promote statements differ by
    the intervening ``n.version = coalesce(n.version, 0) + 1`` clause, so the
    plain promote needle cannot match this one (and this needle cannot match
    that one): the arms are genuinely separate and each needs its own proof.
    The risk this pins is real — the arm carries a RELATIVE increment inside a
    RE-ISSUED statement, which is only safe because an abort means the
    statement did not land.
    """
    sdk = _sdk(tmp_path, "objpromote")
    try:
        pid = sdk.create_point(
            "evidence", f"draft object {uuid.uuid4().hex}",
            status="draft")["id"]
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
        proxy = _AbortOnceGraph(proj.g, _OBJECT_PROMOTE_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, status="live")

        assert proxy.matching >= 2, (
            "the aborted :Object promote SET was never re-issued")
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        point = sdk.get_point(pid)
        assert point["status"] == "live"
        assert point["version"] == 1, (
            "one promotion, one version bump — a re-issued relative increment "
            "must not double")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 10. the derived-embedding clear+set ────────────────────────────────


def test_a_rebuild_abort_on_the_derived_embedding_set_is_retried(tmp_path):
    """``update_point(content=…)``'s clear+set vector write is retried.

    #8854 review P2: ``_derived_embedding`` is true only when the caller passes
    ``content`` with no ``embedding``, so no other test in this file reaches
    this statement at all. It is the crash-sensitive seam (an in-query CLEAR
    followed by the typed SET), and the only arm whose needle is the
    ``REMOVE n.embedding`` prefix rather than a ``SET`` fragment — the per-key
    loop's ``SET n += $props`` writes ``content`` and never touches the vector.
    """
    sdk = _sdk(tmp_path, "derivedembed")
    try:
        pid = _pid(sdk)
        revised = f"revised claim {uuid.uuid4().hex}"
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _EMBEDDING_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, content=revised)

        assert proxy.matching >= 2, (
            "the aborted embedding clear+set was never re-issued")
        assert proxy.applied == 1, (
            "exactly one application — a re-issued absolute clear+set "
            "converges")
        proj.g = proxy._real
        assert sdk.get_point(pid)["content"] == revised
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 11. the orphan-``:Tag`` sweep after a retried tag sync ─────────────


def test_a_retried_tag_sync_still_sweeps_the_orphaned_tag(tmp_path):
    """The orphan ``:Tag`` GC must survive a retry that follows a LANDED delete.

    #8854 review P3: ``_sync_tags`` keys its GC off ``removed``, a
    PER-INVOCATION counter. A first attempt can land the ``DELETE r`` and then
    be aborted on the GC statement; the retry re-reads ``stale``, finds nothing
    left to delete, and a counter-gated sweep would be SKIPPED — stranding an
    orphan ``:Tag`` that ``list_tags`` reports as ``count: 0`` while a rebuilt
    graph (which replays the reconciler from a clean state) has none. The sweep
    is therefore forced on every attempt AFTER the first
    (``sweep_orphans=(_sync_attempts > 1)``), so a retry that follows a landed
    ``DELETE r`` still sweeps, while the first attempt keeps the graph-wide
    ``:Tag`` scan off the SDK's hottest mutation door.
    """
    sdk = _sdk(tmp_path, "taggc")
    try:
        pid = _pid(sdk)
        keep = f"7405keep{uuid.uuid4().hex[:8]}"
        drop = f"7405drop{uuid.uuid4().hex[:8]}"
        sdk.update_point(pid, tags=[keep, drop])
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _TAG_GC_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, tags=[keep])

        assert proxy.matching >= 2, (
            "the aborted orphan sweep was never re-issued")
        assert proxy.applied == 1, "exactly one application"
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (t:Tag {name:$name}) RETURN count(t)",
            params={"name": drop}).result_set
        assert rows and rows[0][0] == 0, (
            "the dropped tag's :Tag node must be swept — a retried sync that "
            "skips it leaves live != replay")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 12. one retry BUDGET for the whole per-key loop ────────────────────


def test_the_per_key_loop_shares_one_retry_budget(tmp_path):
    """The per-key loop is ONE ``_graph_write_with_retry`` call, not one per key.

    #8854 review P2: the budget is per CALL (``retries=3`` → one 7–14 s
    window), so wrapping each key separately made ``annotate_operator``'s FOUR
    properties cost four windows before raising — a caller-visible latency
    regression on the SDK's most-used mutation door, buying nothing in the
    multi-minute window #7405 measured. Aborting the FOURTH key's statement and
    asserting the FIRST key was re-issued distinguishes the two shapes: a single
    budget re-issues all four (8 matching, 7 applied); a per-key budget issues
    the first key once (5 matching).
    """
    sdk = _sdk(tmp_path, "onebudget")
    try:
        src = _pid(sdk)
        tgt = _pid(sdk)
        op = sdk.create_operator("IMPL", src, [tgt])["id"]
        proj = sdk._get_proj()
        # Abort the 4th per-key statement — i.e. NOT the first one, so only a
        # whole-loop re-issue makes the 1st key's statement count as twice.
        proxy = _AbortOnceGraph(
            proj.g, _POINT_PROP_SET_NEEDLE, present=True, skip=3, aborts=1)
        proj.g = proxy

        sdk.annotate_operator(op, bias=0.2, precision=0.8,
                              consistency=0.7, directness=0.9)

        assert proxy.matching == 8, (
            "the whole per-key loop must be re-issued after one abort — one "
            "retry budget for the loop, not one per key")
        assert proxy.applied == 7, (
            "all four keys land, exactly one re-issue apiece")
        proj.g = proxy._real
        point = sdk.get_point(op)
        assert point["annotator_consistency"] == pytest.approx(0.7)
        assert point["annotator_directness"] == pytest.approx(0.9)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 13. the documented per-verb bound IS the call count ─────────────────


def test_the_documented_per_verb_retry_bound_matches_the_call_sites(
        tmp_path, monkeypatch):
    """The stated bound is the number of wrapped CALL SITES a branch reaches.

    ``_graph_write_with_retry``'s docstring states the caller-visible bound as
    one 7-14 s window per call. #8854 review P2 measured that the door's real
    counts are 3 / 4 / 5, not the stated 1 / 2 / 3: ``update_point`` also runs
    ``_mark_dirty``, which itself reaches TWO wrapped sites
    (``_advance_ep_version`` and the ``ep_dirty`` persist). The earlier test
    filtered on ``what.startswith("update_point ")``, so it could not see them
    and stayed green while the prose re-staled — the exact class of defect the
    round-3 rewrite was fixing.

    This counts EVERY wrapped call each shape below makes, on BOTH labels —
    the per-key/embedding/tag arms plus ``_mark_dirty``'s two — so a new site
    on either label's path (including one inside a helper) breaks an assertion
    instead of quietly widening the bound. NOT pinned here: the two PROMOTE
    arms (``status=``), which need a ``draft`` node and are exercised by their
    own tests without a call count, so a new site on THAT path is not caught by
    this test.

    The ``:Object`` arm is a DIFFERENT statement (``SET n += $props`` for the
    whole dict, not one key at a time) and folds the derived vector and the
    relative ``version`` bump INTO it, so its counts are one LOWER than the
    ``:Point`` shapes above and cannot be inferred from them.
    """
    sdk = _sdk(tmp_path, "bound")
    try:
        src = _pid(sdk)
        tgt = _pid(sdk)
        op = sdk.create_operator("IMPL", src, [tgt])["id"]
        seen: list[str] = []
        original = sdk._graph_write_with_retry

        def counting(fn, *, what, witness=None):
            seen.append(what)
            return original(fn, what=what, witness=witness)

        monkeypatch.setattr(sdk, "_graph_write_with_retry", counting)

        _ADVANCE = "_advance_ep_version MERGE(:EpMeta)"
        _DIRTY = "_mark_dirty ep_dirty persist SET"

        sdk.update_point(src, confidence=0.5)
        assert seen == ["update_point SET (per-key)", _ADVANCE, _DIRTY], seen

        seen.clear()
        sdk.update_point(src, tags=["a"])
        assert seen == ["update_point SET (per-key)",
                        "update_point TAGGED edges",
                        _ADVANCE, _DIRTY], seen

        seen.clear()
        sdk.update_point(src, content="revised claim", tags=["a"])
        assert seen == ["update_point SET (per-key)",
                        "update_point SET (embedding)",
                        "update_point TAGGED edges",
                        _ADVANCE, _DIRTY], seen
        assert len(seen) == 5, "a LIVE content+tags update is FIVE windows"

        seen.clear()
        sdk.annotate_operator(op, bias=0.2, precision=0.8,
                              consistency=0.7, directness=0.9)
        assert seen == ["update_point SET (per-key)", _ADVANCE, _DIRTY], seen

        # ── the SAME three shapes on the ``:Point:Object`` arm ──────────────
        # The entity arm writes the WHOLE dict in one statement and folds the
        # derived vector + the relative `version` bump into it, so there is no
        # per-key loop and no separate embedding site: one FEWER window than the
        # `:Point` shapes for content+tags. Counting only `:Point` left these
        # arms unguarded (a new site reached only on the entity path stayed
        # green), which is the drift this test exists to catch.
        obj = _pid(sdk)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n:Object", params={"id": obj})

        seen.clear()
        sdk.update_point(obj, confidence=0.5)
        assert seen == ["update_point SET (Object)", _ADVANCE, _DIRTY], seen
        assert len(seen) == 3, "an :Object plain update is THREE windows"

        seen.clear()
        sdk.update_point(obj, tags=["a"])
        assert seen == ["update_point SET (Object)",
                        "update_point TAGGED edges", _ADVANCE, _DIRTY], seen
        assert len(seen) == 4, "an :Object tagged update is FOUR windows"

        seen.clear()
        sdk.update_point(obj, content="object revision", tags=["a"])
        assert seen == ["update_point SET (Object)",
                        "update_point TAGGED edges", _ADVANCE, _DIRTY], seen
        assert len(seen) == 4, (
            "an :Object content+tags update is FOUR windows — the derived "
            "vector rides the same statement, so it adds no retry window")

        # A TERMINAL point reaches `_mark_dirty`'s extra clear SET, so the
        # bound is a RANGE, not a number (#8854 review round 3 P2).
        sdk.retract_point(src)
        seen.clear()
        sdk.update_point(src, confidence=0.3)
        assert seen == ["update_point SET (per-key)", _ADVANCE, _DIRTY,
                        "_mark_dirty terminal ep_dirty clear SET"], seen

        # The documented UPPER bound: a TERMINAL point with content AND tags.
        seen.clear()
        sdk.update_point(src, content="terminal revision", tags=["b"])
        assert seen == ["update_point SET (per-key)",
                        "update_point SET (embedding)",
                        "update_point TAGGED edges",
                        _ADVANCE, _DIRTY,
                        "_mark_dirty terminal ep_dirty clear SET"], seen
        assert len(seen) == 6, "a TERMINAL content+tags update is SIX windows"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── the loud arm: a DELETED graph is never re-issued into ───────────────

# ── the noisy arm that is NOT loud enough: a re-issue that lands NOTHING ─
# #8854 review P1. The retry predicate can only prove the graph KEY exists, and
# its own docstring admits the residual case — a rebuild that recreated the key
# and has not replayed the node yet also reads ``EXISTS 1``. A re-issued
# ``MATCH … SET`` then matches nothing and raises nothing, so pre-fix the verb
# returned SUCCESS for a write it never made.


class _AbortOnceThenEmptyGraph:
    """Abort once AND empty the graph — a rebuild whose recreate has landed.

    Models the #8854 P1 window exactly: the key is PRESENT (so the predicate
    authorises the retry) but the data has not been replayed, so the re-issued
    statement matches nothing. ``applied`` counts statements that reached the
    real graph AFTER the wipe, so "the write never landed" is measured.
    """

    def __init__(self, real, needle: str, *, present: bool = True) -> None:
        self._real = real
        self._needle = needle
        self._present = present
        self.matching = 0
        self.matched_rows = 0
        self.aborted = False

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if not self.aborted:
                self.aborted = True
                # The rebuild's recreate: key present, graph EMPTY, replay not
                # yet done. Nothing in the statement text can see this.
                self._real.query("MATCH (n:Point) DETACH DELETE n")
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            res = self._real.query(cypher, params=params, timeout=timeout)
            # ROWS this re-issue matched — not merely that it was issued.
            self.matched_rows += (res.result_set[0][0]
                                  if res.result_set else 0)
            return res
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1 if self._present else 0
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_reissue_that_matches_nothing_is_NOT_reported_as_success(
        tmp_path, monkeypatch):
    """An authorised retry that lands zero rows must stay LOUD (#8854 P1).

    Pre-fix this returned ``{}`` (or ``{"invalidated": True}``) with no
    exception: the revision survived only in the caller's transcript, and in
    journal-less mode the replay could not recover it either.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "emptied")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceThenEmptyGraph(proj.g, _POINT_PROP_SET_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, confidence=0.9)

        assert proxy.matching >= 2, "the aborted statement was never re-issued"
        assert proxy.matched_rows == 0, (
            "the re-issue matched nothing, so the verb must not report success")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_an_invalidate_that_matches_nothing_does_not_report_invalidated(
        tmp_path, monkeypatch):
    """The ``invalidate_point`` witness: no CORRECTS edge, no success claim."""
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "invempty")
    try:
        old = _pid(sdk)
        new = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceThenEmptyGraph(proj.g, _INVALIDATE_STAMP_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.invalidate_point(old, new)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_retract_does_not_claim_already_terminal_after_a_retry(
        tmp_path, monkeypatch):
    """An empty CAS result after a retry is NOT "already terminal" (#8854 P1).

    Pre-fix the re-issued ``SET`` matched nothing (rebuild still replaying) and
    the empty ``result_set`` took the terminal branch — reporting a LIVE point
    as already terminal, while the journal had already recorded the retraction.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "retractempty")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceThenEmptyGraph(proj.g, _RETRACT_CAS_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.retract_point(pid)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


_WRITE_SLOT = "another write is in progress, retry the query"


class _TargetVanishesBeforeTheWrite:
    """The target is PRESENT at the guard read and gone by the write.

    This is the ONE shape on the NON-PROMOTE arm in which a zero-row statement
    is a legitimate, NON-RAISING outcome for ``update_point`` (#8854 review
    round 10, I1): #10119 refuses an id absent AT the guard read before any
    WRITE statement is issued, so the reachable benign-zero-row case here is a
    point that existed at that read and was removed (rebuild / replay) before
    the write ran.

    Other zero-row shapes are NOT this one, and the difference is the outcome:
    on the promote arm a zero-row statement either RAISES (an absent id gets
    `No point`; a terminal one gets the #432 Illegal-status error) or returns
    the POINT as the #1088 idempotent success once the node has gone live
    between the #1088 probe and the guarded ``SET``. None of them returns the
    empty ``{}`` this arm's vanished-target case does.

    Two polarities, and the difference is the whole point:

    - ``abort_message=None`` — the first matching statement simply runs and
      matches nothing. No abort, so the retry wrapper is never consulted and
      its witness is never called: a first attempt is never witnessed.
    - ``abort_message=_WRITE_SLOT`` — the first matching statement aborts with
      the write-slot arm, which is RETRYABLE but state-INDEPENDENT: it says
      nothing about whether the target exists, so it must not ARM the witness.
      The re-issue then reports zero rows, and a mis-armed witness would turn
      that into a ``_WriteNotLanded`` surfacing the bare write-slot text
      ("retry the query") on a target that is simply gone — never the
      corruption-flavoured graph-replaced diagnosis, which belongs to the
      LATCH family only (``_GraphAbortThenSlotAbortEmptyGraph``).

    ``matched_rows`` counts what the statements ACTUALLY matched, so "nothing
    landed" is measured rather than assumed.
    """

    def __init__(self, real, needle: str, *, abort_message: str | None = None
                 ) -> None:
        self._real = real
        self._needle = needle
        self._abort_message = abort_message
        self.matching = 0
        self.matched_rows = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                # the target is gone before the statement runs
                self._real.query("MATCH (n:Point) DETACH DELETE n")
                if self._abort_message is not None:
                    raise redis_exc.ResponseError(self._abort_message)
            res = self._real.query(cypher, params=params, timeout=timeout)
            self.matched_rows += (res.result_set[0][0]
                                  if res.result_set else 0)
            return res
        return self._real.query(cypher, params=params, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_first_attempt_that_matches_nothing_keeps_the_empty_contract(
        tmp_path):
    """The witness must NOT change a legitimate zero-row FIRST attempt.

    ``update_point`` returns ``{}`` when its statement matches nothing, and a
    first attempt never consults the witness, so this stays true.

    ⛔ #10119 moved WHERE that shape is reachable: an id absent at the guard
    read now issues no WRITE statement (pinned by
    ``test_an_absent_id_issues_no_statement_for_the_write``), so this test
    reaches it the only way left — the target is PRESENT at that read and
    vanishes before the write. Do NOT "restore" a write for an id that is
    absent at entry to make this look like the old shape: that would re-open
    #10119 (an absent id journalling a revision, advancing the EP epoch and
    stranding a dirty root). The GATE itself — "only a graph-replaced abort
    arms the witness" — is pinned by
    ``test_a_write_slot_abort_on_a_vanished_target_keeps_the_empty_contract``.
    """
    sdk = _sdk(tmp_path, "vanished")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _TargetVanishesBeforeTheWrite(
            proj.g, _POINT_PROP_SET_NEEDLE)
        proj.g = proxy

        assert sdk.update_point(pid, confidence=0.5) == {}
        assert proxy.matching == 1, (
            "a target present at the guard read must still take its write "
            f"({proxy.matching} statements)")
        assert proxy.matched_rows == 0, (
            "the statement must have matched nothing — otherwise this test "
            "is not the benign zero-row shape")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_write_slot_abort_on_a_vanished_target_keeps_the_empty_contract(
        tmp_path, monkeypatch):
    """A non-rebuild abort must NOT arm the witness (#8854 review P1).

    ``retryable_aborted_write`` also admits the write-slot arm — ordinary
    multi-writer contention — which says nothing about whether the TARGET
    exists. Arming the witness on it turns a re-issue that legitimately matches
    nothing into a stale ``ResponseError`` (the bare write-slot text, "retry
    the query") where the documented answer for a write that lands nowhere is
    ``{}``. Only the graph-deleted-or-replaced family can mean "not replayed
    yet", so only that family arms the witness.

    ⛔ #10119 RE-BASED THIS TEST, and it is not the same test it was: it used to
    abort on an id absent at ENTRY, which is now refused before any WRITE
    statement is issued (so its write-slot injection could never fire and its
    assertion silently became "no write statement reached"). The invariant it names is still
    reachable, and this is the shape that reaches it — the target is PRESENT at
    the guard read, the FIRST statement aborts write-slot, the target vanishes
    so the RE-ISSUE matches zero rows. Nothing may report that as a landing,
    and nothing may report it as a graph replacement either: the caller gets
    the same empty contract, because a state-independent abort never armed the
    witness.

    ⚠️ A PREDECESSOR REVISION'S CLAIM HERE WAS MEASURED FALSE — read this
    before deleting the test. `784d0c26a` concluded that "a present target
    matches", so a PRESENT-target replacement would pass on both trees and
    nothing should be added. That reasoning misses the window this gate exists
    for: a target PRESENT at the guard read can VANISH before the write
    (rebuild / replay), and the write then legitimately matches zero rows —
    the shape below. Measured on that revision's tree, with the arming gate
    mutated to `if keep:`: THIS test fails (the aborted statement is re-issued
    three times and the bare write-slot ``ResponseError`` escapes). It is not a
    test that passes with the guard deleted.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "vanishedslot")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _TargetVanishesBeforeTheWrite(
            proj.g, _POINT_PROP_SET_NEEDLE, abort_message=_WRITE_SLOT)
        proj.g = proxy

        # No ``pytest.raises``: a witness armed by the write-slot abort would
        # surface ``_WRITE_SLOT`` here after the retry budget, and the
        # graph-replaced family would append its rebuild diagnosis — neither is
        # what a vanished target under contention may produce.
        assert sdk.update_point(pid, confidence=0.5) == {}
        assert proxy.matching >= 2, (
            "the aborted statement must be RE-ISSUED — otherwise the witness "
            "was never reachable and this test proves nothing")
        assert proxy.matched_rows == 0, (
            "the re-issue matched a row, so it was not the zero-row shape")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_an_absent_id_issues_no_statement_for_the_write(tmp_path):
    """#10119: an id absent at the guard read is refused BEFORE the write.

    This is where the #8854 witness question used to be tested, and it can no
    longer be: the door reads the target's existence from the lifecycle guard
    BEFORE the journal append and BEFORE any write, so an absent id issues NO
    WRITE statement — ``proxy.matching == 0`` — and there is no first attempt
    to abort and retry. The witness gate itself is now pinned on the shape that
    still issues a statement,
    ``test_a_write_slot_abort_on_a_vanished_target_keeps_the_empty_contract``.

    The proxy injects the graph-replaced abort by default precisely so a
    regression that re-opens the write for an absent id cannot pass quietly: it
    would be aborted and retried, and ``matching`` would leave zero.
    """
    sdk = _sdk(tmp_path, "absentwrite")
    try:
        missing = str(uuid.uuid4())
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE)
        proj.g = proxy

        assert sdk.update_point(missing, confidence=0.5) == {}
        assert proxy.matching == 0, (
            "an ABSENT id issued a write statement (#10119: the guard read "
            "must precede the append and every write)")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def _journal_carries(journal: str, pid: str) -> bool:
    """Does the JSONL journal name *pid*, in either record shape (#10515)?

    Deliberately INDEPENDENT of the SDK's own memoised scan: this is the test's
    ground truth for "the journal knows this id", read straight from the file.
    ``PointAdded`` carries the snapshot under ``point``; ``PointRevised`` carries
    ``id`` at the top level.
    """
    try:
        with open(journal, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("id") == pid:
                    return True
                point = ev.get("point")
                if isinstance(point, dict) and point.get("id") == pid:
                    return True
    except FileNotFoundError:
        return False
    return False


def test_a_journal_known_point_absent_from_the_live_graph_reports_the_loss(
        tmp_path, caplog):
    """#10515: a rebuild-window revision is no longer LOST IN SILENCE.

    The ABSENT arm (#10119) refuses an id the LIVE graph does not hold — correct
    for a TYPO, and it is deliberately kept. Its stated trade is that a target
    absent at that read gets no ``PointRevised`` row, no epoch delta and no retry,
    so during a rebuild (#7405) a LEGITIMATE revision landed nowhere and said
    nothing: the caller got the documented ``{}`` and could not tell the two
    states apart.

    This test reaches BOTH shapes deliberately, as #10515's indicator 3 asks, and
    asserts the OBSERVABILITY difference rather than the reply shape (the reply
    stays ``{}`` in both):

    * a point the JOURNAL knows, removed from the LIVE graph (the projection is
      behind — the rebuild window) → a ``WARNING`` and one
      ``metrics()["silent_revision_losses"]["journal_known"]``;
    * an id absent from BOTH → still silent: no warning, no counter, because
      turning a typo into an alarm is what the issue's indicator 2 forbids.

    ⛔ The fix REPORTS the loss; it does not prevent it. Nothing here asserts a
    journal row for the absent id — that row is exactly what #10119 removed, and
    restoring it would re-open a stranded dirty root (#2422 / #7505 / #9269).
    """
    from tortoise import monitoring

    sdk, journal = _sdk_journaled(tmp_path, "absentknown")
    try:
        pid = _pid(sdk)
        assert _journal_carries(journal, pid), (
            "the journal must carry the id — it is the discriminator")
        # The rebuild window: the projection has not (re)applied the record yet.
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) DETACH DELETE n", {"id": pid})
        assert not sdk.get_point(pid), (
            "the live graph must NOT hold the point for this shape "
            f"(get_point answered {sdk.get_point(pid)!r})")

        before = monitoring.silent_revision_loss_counts().get("journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(pid, confidence=0.9) == {}
        after = monitoring.silent_revision_loss_counts().get("journal_known", 0)

        assert after == before + 1, (
            "a journal-known id absent from the LIVE graph must be COUNTED — "
            "this is the legitimate revision the ABSENT arm drops")
        assert any("will not be replayed" in r.getMessage()
                   for r in caplog.records), (
            "the loss must also be LOUD in the log, not only in a counter")

        # ── the other shape: a typo stays silent (indicator 2) ──
        typo_before = monitoring.silent_revision_loss_counts()
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts() == typo_before, (
            "an id absent from BOTH the graph and the journal is a typo and "
            "must add to no counter")
        assert not any("will not be replayed" in r.getMessage()
                       for r in caplog.records), (
            "a typo must not produce the revision-loss warning")

        # ── the third shape: a point the lane DELETED is gone, not lost ──
        # (review B1) A hard delete journals `EntityMutated {op: delete}` with the
        # SAME id, so a "does any record carry this id" test reported a lost
        # revision on a healthy lane for every write-after-delete — and a counter
        # that fires there can never be alerted on. Presence is folded
        # last-record-wins, so a deleted point is NOT known.
        gone = _pid(sdk)
        sdk.delete_point(gone)
        deleted_before = monitoring.silent_revision_loss_counts()
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(gone, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts() == deleted_before, (
            "a deliberately DELETED point must not be counted as a lost "
            "revision — the counter has to stay alertable on a healthy lane")
        assert not any("will not be replayed" in r.getMessage()
                       for r in caplog.records), (
            "a write after a hard delete must not warn about a lost revision")

        # ── the memo is PER-ID, not per-journal-state (review round 2, find 1) ──
        # The rebuild window is exactly the state in which the journal does NOT
        # change while the arm refuses, so a memo that cached ONE id's answer
        # under a journal-wide key answered a fabricated "typo" for the next id —
        # the FIRST loss was reported and every later one went silent. Two
        # distinct journal-known ids, in one journal state, both must count.
        p1, p2 = _pid(sdk), _pid(sdk)
        sdk._get_proj().g.query(
            "MATCH (n:Point) WHERE n.id IN $ids DETACH DELETE n",
            {"ids": [p1, p2]})
        assert not sdk.get_point(p1) and not sdk.get_point(p2)
        two_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        with caplog.at_level("WARNING"):
            assert sdk.update_point(p1, confidence=0.9) == {}
            assert sdk.update_point(p2, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == two_before + 2, (
            "two journal-known points in ONE journal state must BOTH be "
            "counted — a single-slot memo answers 'typo' for the second, "
            "which is the silence #10515 removes")

        # ── a MERGED-AWAY point is gone, not lost (review round 3, finding 2) ──
        # `PointsMerged` names its ids in `keep_id`/`merge_ids`, not under `id` or
        # `point`, so a fold that only reads `_rec_point_id` skipped it and the
        # loser kept its earlier `PointAdded` presence — a routine dedup reported
        # as a lost revision on a healthy lane. The record is appended in the
        # PRODUCER's shape (`tortoise/api.py::merge_points`), and the loser is
        # removed from the live graph the way the merge fold removes it.
        keep = _pid(sdk)
        loser = _pid(sdk)
        sdk._get_event_log().append(
            {"type": "PointsMerged", "keep_id": keep, "merge_ids": [loser]})
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) DETACH DELETE n", {"id": loser})
        assert not sdk.get_point(loser)
        merged_before = monitoring.silent_revision_loss_counts()
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(loser, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts() == merged_before, (
            "a point the lane deliberately MERGED AWAY must not be counted as "
            "a lost revision")
        assert not any("will not be replayed" in r.getMessage()
                       for r in caplog.records), (
            "a write after a merge must not warn about a lost revision")

        # ── ... and the same in the NESTED record shape (review round 4, find 1) ──
        # The same record has a FLAT (API) and a NESTED (script, #325) shape, and
        # the projection normalizes both (`_norm`). A fold that read only the raw
        # record reported a nested merge as "the journal still holds the id" — the
        # false positive the earlier rounds removed for the flat shape.
        keep2 = _pid(sdk)
        loser2 = _pid(sdk)
        sdk._get_event_log().append(
            {"type": "PointsMerged",
             "point": {"keep_id": keep2, "merge_ids": [loser2]}})
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) DETACH DELETE n", {"id": loser2})
        assert not sdk.get_point(loser2)
        nested_before = monitoring.silent_revision_loss_counts()
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(loser2, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts() == nested_before, (
            "a NESTED PointsMerged record must fold exactly as the flat one — a "
            "bare top-level read reports a deliberate merge as a lost revision")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_typo_on_a_journal_less_sdk_stays_silent(tmp_path):
    """#10515 indicator 2, on the DEFAULT configuration (review round 3, finding 1).

    ``event_log_path=None`` is the documented default (``__main__``, the embedded
    lane, every test SDK). With no journal there is no replay source, so nothing
    can be judged LOST — the absent read is not a loss and must stay SILENT. The
    first cut returned ``None`` from the journal question for this case and the
    caller read ``None`` as "unreadable", which alarmed every typo with "the
    revision was NOT recorded" and grew ``journal_unreadable`` with typo traffic.
    """
    from tortoise import monitoring

    sdk = _sdk(tmp_path, "nojournal")
    try:
        before = monitoring.silent_revision_loss_counts()
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts() == before, (
            "a journal-less SDK cannot judge a loss, so a typo must add nothing "
            "to any reason")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_an_unconfirmable_graph_reports_the_loss_with_its_own_reason(tmp_path):
    """#10515 `graph_unavailable`: a graph that cannot be CONFIRMED is not a typo.

    The rebuild/replace window is the shape the issue is about, and it is reached
    here the way the engine produces it: the target is absent from the live graph
    AND the graph key probe answers 0 (`_AbortOnceGraph(present=False)`), so the
    absence cannot be judged at all. The reply stays `{}`; the signal fires with
    its own reason, because "unjudged" and "no such point" are different facts.
    """
    from tortoise import monitoring

    sdk = _sdk(tmp_path, "absentunavailable")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point {id:$id}) DETACH DELETE n", {"id": pid})
        assert not sdk.get_point(pid)
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE, aborts=0,
                                present=False)
        proj.g = proxy

        before = monitoring.silent_revision_loss_counts().get(
            "graph_unavailable", 0)
        assert sdk.update_point(pid, confidence=0.9) == {}
        after = monitoring.silent_revision_loss_counts().get(
            "graph_unavailable", 0)
        assert after == before + 1, (
            "an absent read on a graph whose key cannot be confirmed must be "
            "reported as graph_unavailable — the caller cannot tell it from a "
            "typo otherwise")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_journal_removed_mid_flight_is_unreadable_not_a_typo(tmp_path, caplog):
    """#10543 review round 7, finding 2: a GONE journal is not "early life".

    ``_journal_knows_point`` folded ``FileNotFoundError`` to *not a typo* before
    anything asked whether the journal had ever carried anything. So the two facts
    the reason vocabulary separates — a configured path nothing has written
    (ordinary early life, silent) and a journal that CARRIED records whose file
    was deleted, rotated or moved off a running lane (nothing can be judged) —
    produced the same silence, and ``journal_unreadable`` could never move for the
    shape its own docstring names ("a path removed mid-flight"). That is the
    loss-stays-silent failure #10515 indicator 1 exists to remove.

    Both facts are reached here, in that order, because the discriminator has to
    keep the FIRST silent: the control at the top is what stops the fix from
    turning every typo on a freshly-configured lane into an alarm (indicator 2).
    """
    from tortoise import monitoring

    sdk, journal = _sdk_journaled(tmp_path, "journalgone")
    try:
        # ── the shape that must STAY silent: the path has never been written ──
        assert not os.path.exists(journal), (
            "the early-life control needs a configured path nothing wrote")
        early_before = monitoring.silent_revision_loss_counts()
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts() == early_before, (
            "a configured journal path nothing has written yet is the ordinary "
            "early-life typo path — it must add to NO reason")
        assert not any("will not be replayed" in r.getMessage()
                       for r in caplog.records), (
            "early life must not warn about a lost revision")

        # ── the journal carried records, then the FILE went away ──
        pid = _pid(sdk)
        assert _journal_carries(journal, pid), (
            "the fixture must leave the journal carrying a record")
        assert os.path.exists(journal + ".position"), (
            "the discriminator reads the writer's own sidecar, so the fixture "
            "must actually have one (it is written through on append)")
        os.unlink(journal)

        known_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        before = monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        after = monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0)
        assert after == before + 1, (
            "a journal whose file is GONE after carrying records cannot be "
            "read, so the absent read is UNJUDGED — folded into the typo "
            "silence it is the loss #10515 indicator 1 forbids")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == known_before, (
            "the gone journal is UNREADABLE, not known — counting it as "
            "journal_known would claim a revision was found and dropped")
        assert any("FILE is gone" in r.getMessage()
                   for r in caplog.records), (
            "the loss must also be LOUD in the log, not only in a counter")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_non_point_record_naming_the_id_is_not_journal_presence(tmp_path, caplog):
    """#10543 review round 8, finding 1: presence is a KIND question, both ways.

    Round 4 gated the DELETE arm on ``_record_owns_point`` and left the presence
    arm kind-blind (``else: state[point_id] = True``), so ANY record carrying the
    id folded it to "the journal holds this POINT" — whatever entity it belongs
    to. The flat-``id`` writers are real: ``EventAPI._emit`` splats its payload at
    the TOP level, so ``add_object`` / ``add_subject`` / ``add_event`` /
    ``add_document`` all land a top-level ``id``. An Object id that coincides with
    a TYPO'd point id therefore made the typo answer ``journal_known``: a WARNING
    claiming a lost revision plus a ``silent_revision_losses`` increment on
    ordinary typo traffic — the counter that must stay alertable, grown by the
    path #10515 indicator 2 says must stay silent.

    The control at the end proves the gate did not simply switch presence OFF: a
    real ``PointAdded`` for the same id still confers it, last-record-wins.
    """
    from tortoise import monitoring

    sdk, _journal = _sdk_journaled(tmp_path, "nonpointkind")
    try:
        typo = str(uuid.uuid4())
        log = sdk._get_event_log()
        # the OTHER kinds' own records, in the producers' shape
        log.append({"type": "ObjectRegistered", "id": typo, "name": "collides"})
        log.append({"type": "EntityMutated", "id": typo,
                    "label": "Object", "op": "update"})
        before = monitoring.silent_revision_loss_counts()
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(typo, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts() == before, (
            "an Object/Entity record naming the id does NOT put a POINT in the "
            "journal — counting it turns every typo into a lost revision on a "
            "lane whose journal mentions other kinds")
        assert not any("will not be replayed" in r.getMessage()
                       for r in caplog.records), (
            "a typo must not warn, whatever kinds share the journal")

        # ── the control: a POINT record for the same id still confers presence ──
        log.append({"type": "PointAdded", "point": {"id": typo}})
        after_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(typo, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == after_before + 1, (
            "the kind gate must not silence a REAL point record — a journal "
            "that does hold the point is exactly the #7405 shape")
        assert any("will not be replayed" in r.getMessage()
                   for r in caplog.records)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_the_presence_memo_resumes_and_answers_a_never_seen_id(
        tmp_path, monkeypatch, caplog):
    """#10543 review round 8, finding 2: appends must not force a re-read.

    The memo was keyed on the whole ``os.stat`` identity, so ANY journal append
    invalidated it and the next absent id re-scanned from byte 0 — O(journal) per
    absent write on exactly the LIVE lane #7405 describes, growing with tenant
    age. The fix retains a presence map complete for ``[0, resume_offset)`` and
    folds only the appended range.

    Three properties are pinned, because two of them are the ways a resuming memo
    can be WRONG rather than merely slow:

    * the second absent id RESUMES (its scan starts past byte 0);
    * an id whose only record sits BEFORE the resume offset is still answered
      from the map — a shared offset with a needle-filtered scan would answer a
      fabricated ``False`` for it, which is the dangerous silence;
    * the retained map answers an id that was never asked about while it was
      being built, without reading the journal again.
    """
    from tortoise import monitoring
    from tortoise.log import EventLog

    sdk, journal = _sdk_journaled(tmp_path, "resume")
    try:
        # an OLD point: its record sits at the head of the journal, long before
        # any offset a later scan resumes from
        old = _pid(sdk)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) DETACH DELETE n", {"id": old})
        assert not sdk.get_point(old)
        log = sdk._get_event_log()
        for i in range(200):
            log.append({"type": "PointAdded", "point": {"id": f"bulk-{i:04d}"}})

        scans: list[tuple[int | None, int]] = []
        real = EventLog.mention_lines

        def spy(self, needle=None, start=0):
            scans.append((needle, start))
            return real(self, needle, start)

        monkeypatch.setattr(EventLog, "mention_lines", spy)

        # ── the first absent write builds the map: one pass from byte 0 ──
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert scans == [(None, 0)], (
            f"the first absent id pays one full pass, saw {scans}")
        first_resume = sdk._journal_presence_memo['folded_to']
        assert first_resume == os.path.getsize(journal), (
            "the memo must record how far it folded, or it cannot resume")

        # ── one append later: the next absent id resumes from that offset ──
        log.append({"type": "PointAdded", "point": {"id": "after-the-scan"}})
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert len(scans) == 2, f"one pass per absent write, saw {scans}"
        assert scans[1][1] == first_resume, (
            "the second absent id must RESUME at the offset the first pass "
            f"reached ({first_resume}), not re-read the journal: {scans[1]}")
        assert scans[1][1] < os.path.getsize(journal), (
            "the resumed pass must start before EOF — resuming at EOF would "
            "mean it read nothing at all")

        # ── a record that predates the resume offset is still found ──
        known_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(old, confidence=0.9) == {}
        assert len(scans) == 2, (
            "an id the retained map already covers must cost NO journal read — "
            f"a resuming memo that re-reads is the defect it exists to remove: {scans}")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == known_before + 1, (
            "the map must be complete for the bytes it folded, so a record that "
            "PREDATES the resume offset still answers presence — a shared offset "
            "over a needle-filtered scan answers a fabricated False here, which "
            "is the silence #10515 exists to remove")
        assert any("will not be replayed" in r.getMessage()
                   for r in caplog.records)
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_an_append_landing_during_a_fold_does_not_discard_the_presence_map(
        tmp_path, monkeypatch):
    """PR #10543 review round 12, finding 2: the memo's stat must describe its offset.

    The memo stored the journal identity read **before** the fold next to the
    offset the fold actually reached. One ordinary append landing while the fold
    was reading — the ordinary state of the append-active lane #7405 describes —
    then left ``folded_to == current size`` with a STALE ``mtime_ns``, so the next
    absent write read that pair as "a same-size rewrite in a later tick", threw
    away a map that was a true prefix of the journal, and re-folded from byte 0.
    That is an O(journal) scan per absent write: exactly the cost round 8 removed,
    restored by an accounting mistake. Here the append lands INSIDE the fold, so
    every later write to this journal is skipped — only the resume offset proves
    the map survived.

    Non-vacuity: on the pre-fix tree the second absent write scans from byte 0.
    """
    from tortoise.log import EventLog

    sdk, journal = _sdk_journaled(tmp_path, "midfold")
    try:
        log = sdk._get_event_log()
        for i in range(120):
            log.append({"type": "PointAdded", "point": {"id": f"seed-{i:04d}"}})

        scans: list[tuple[int | None, int]] = []
        real = EventLog.mention_lines
        armed = {'on': True}

        def spy(self, needle=None, start=0):
            scans.append((needle, start))
            if armed['on'] and needle is None and start == 0:
                # the append lands AFTER the pre-fold stat and BEFORE the fold
                # reaches EOF: the tightest seam this defect can be seen at
                armed['on'] = False
                log.append({"type": "PointAdded", "point": {"id": "mid-fold"}})
            return real(self, needle, start)

        monkeypatch.setattr(EventLog, "mention_lines", spy)

        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert len(scans) == 1, f"one pass for the first absent id, saw {scans}"
        folded_to = sdk._journal_presence_memo['folded_to']
        assert folded_to == os.path.getsize(journal), (
            "the fold must have consumed the record appended DURING it, or the "
            f"append did not land where this test needs it: {folded_to} vs "
            f"{os.path.getsize(journal)}")

        # nothing is written between the two calls: the ONLY difference from
        # `test_the_presence_memo_resumes_and_answers_a_never_seen_id` is that the
        # journal's last bytes arrived mid-fold, so the memo's mtime has to be the
        # POST-fold one or the pair reads as a same-size rewrite
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        # The pre-fix memo is discarded here and this write re-folds from byte 0.
        # A retained map either answers it outright (no read: it already covers
        # every byte) or resumes at `folded_to`; both are fine, byte 0 is not.
        assert len(scans) <= 2, f"at most one pass per absent write, saw {scans}"
        assert scans[1:] == [] or scans[1][1] == folded_to, (
            "an append DURING the fold must not cost the next absent write a "
            "re-fold from byte 0 — that is O(journal) per absent write on the "
            f"lane #7405 describes: {scans[1:]}, folded_to {folded_to}")

        # and the surviving map still answers an id it folded, for free
        before = len(scans)
        assert sdk._journal_knows_point("seed-0007") is True, (
            "the map retained across a mid-fold append must still answer an id "
            "folded before the seam")
        assert len(scans) == before, (
            f"a covered id must cost no journal read, saw {scans[before:]}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_same_size_rewrite_after_a_quiet_fold_still_drops_the_map(
        tmp_path, monkeypatch):
    """Inverse control for the fix above: the quiet path keeps the OLD guard.

    A journal that stood STILL through its fold is anchored by its ``mtime_ns``,
    so a same-size rewrite in a later tick must still discard the map — the fix
    for round 12's finding 2 buys the append-during-fold case with a byte
    comparison across the seam, and it must not buy it by trusting every
    same-size change. This asserts the discard, so a map that was merely
    *re-used more often* cannot pass as the fix.
    """
    from tortoise.log import EventLog

    sdk, journal = _sdk_journaled(tmp_path, "sameSize")
    try:
        log = sdk._get_event_log()
        for i in range(60):
            log.append({"type": "PointAdded", "point": {"id": f"quiet-{i:04d}"}})

        scans: list[tuple[int | None, int]] = []
        real = EventLog.mention_lines

        def spy(self, needle=None, start=0):
            scans.append((needle, start))
            return real(self, needle, start)

        monkeypatch.setattr(EventLog, "mention_lines", spy)

        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert len(scans) == 1, scans
        assert sdk._journal_presence_memo['stable'] is True, (
            "the first fold raced nothing, so its mtime anchors its offset")
        folded_to = sdk._journal_presence_memo['folded_to']

        # same size, different bytes, later tick — the shape the mtime arm owns
        journal_path = Path(journal)
        raw = journal_path.read_bytes()
        assert len(raw) > 8 and b'quiet-0007' in raw
        journal_path.write_bytes(raw.replace(b'quiet-0007', b'quiet-0009'))
        st = os.stat(journal_path)
        os.utime(journal_path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        assert os.path.getsize(journal_path) == folded_to, "a SAME-SIZE rewrite"

        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert len(scans) == 2, scans
        assert scans[1][1] == 0, (
            "a same-size rewrite in a later tick must re-fold from byte 0, not "
            f"resume over bytes that changed: {scans[1]}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_journal_replaced_during_a_fold_is_not_memoised(tmp_path, monkeypatch,
                                                          caplog):
    """A REPLACEMENT under the fold must DROP the map, never re-bind it.

    ``resume`` and ``seam`` are observations of whichever file sat at the path
    when the fold ENDED, while ``known`` was folded from the file the fold READ.
    A journal rotated, ``mv``'d or restored DURING a fold therefore left a memo
    whose identity named the NEW inode: the next call matched it, re-read the
    new file's seam (which is what the memo stored), and answered out of the OLD
    file's records — a real loss silenced, or a typo alarmed (both #10515
    indicators reversed), and the map then answered stale for good. The field
    rule for a retained prefix is to keep the identity it was folded FROM and
    treat a changed identity as DISCARD, which is the ``stat_after[:2] !=
    stat_key[:2]`` arm.

    Non-vacuity: on the pre-fix tree the memo survives the replacement and the
    next call reports an id only the REPLACED file ever held as journal-known.

    ⛔ AND THE CROSSING CALL ITSELF MUST ANSWER FROM THE NEW FILE (review round
    16, finding 1). The invalidation used to be applied to the MEMO but not to
    this call's answer: the map was dropped and then read anyway, so the very
    call that DETECTED the replacement answered ``known.get(id, False)`` out of
    the file that had just been moved away. With ``new-0100`` — present in the
    replacement, absent from the live graph — the pre-fix answer is ``False``
    (a typo, silent) where the truth is ``journal_known`` (a real loss).
    """
    from tortoise import monitoring
    from tortoise.log import EventLog

    sdk, journal = _sdk_journaled(tmp_path, "midfoldReplace")
    try:
        log = sdk._get_event_log()
        for i in range(120):
            log.append({"type": "PointAdded", "point": {"id": f"old-{i:04d}"}})
        old_stat = os.stat(journal)

        # a DIFFERENT journal, longer than the folded prefix, ready to be moved
        # over the live path: a new inode, new bytes, and a LARGER size so the
        # growth/seam arm — not the shrink guard — is what a next call takes
        replacement = journal + ".replacement"
        other = EventLog(replacement)
        for i in range(200):
            other.append({"type": "PointAdded", "point": {"id": f"new-{i:04d}"}})

        scans: list[tuple[int | None, int]] = []
        real = EventLog.mention_lines
        armed = {'on': True}

        class _ReplacingScan:
            """The real ``MentionScan``, with the ROTATE landing when it ends.

            A plain generator wrapper would lose ``resume_offset`` (the fold
            reads it off the scan object), so this delegates it — the fold must
            reach the OLD file's real EOF before the path is swapped.
            """

            def __init__(self, inner, start, needle, on_end):
                self._inner = inner
                self._on_end = on_end
                self._key = (needle, start)
                self.resume_offset = getattr(inner, 'resume_offset', start)

            def __iter__(self):
                yield from self._inner
                self.resume_offset = self._inner.resume_offset
                # every old record HAS been folded and the path has not been
                # observed since — the exact seam this guard is about
                self._on_end(self._key)

        def on_end(key):
            if armed['on'] and key == (None, 0):
                armed['on'] = False
                os.replace(replacement, journal)

        def spy(self, needle=None, start=0):
            scans.append((needle, start))
            return _ReplacingScan(real(self, needle, start), start, needle, on_end)

        monkeypatch.setattr(EventLog, "mention_lines", spy)

        # ── the CROSSING call: it discovers the replacement, so its answer
        #    must describe the file that is there NOW ──
        known_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point("new-0100", confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == known_before + 1, (
            "the call that DETECTS the replacement must answer from the new "
            "journal: `new-0100` is journal-known there and absent from the "
            "live graph, so the loss must be reported — answering out of the "
            "discarded map silences it (the #10515 indicator 1 failure)")
        assert any("the journal still holds the id" in r.getMessage()
                   for r in caplog.records), (
            "the crossing call's report must be journal_known and LOUD")
        assert scans[:] == [(None, 0), (b"new-0100", 0)], (
            "the discarded map must be re-derived from the file that is "
            "there NOW, needle-filtered (bounded memory) rather than a full "
            f"re-fold: {scans}")
        new_stat = os.stat(journal)
        assert (new_stat.st_dev, new_stat.st_ino) != (
            old_stat.st_dev, old_stat.st_ino), (
            "the fixture must actually REPLACE the journal (a new inode)")
        assert new_stat.st_size > old_stat.st_size, (
            "the replacement must be LARGER than the folded prefix, so a next "
            "call takes the growth/seam arm rather than the shrink guard")

        assert sdk._journal_presence_memo is None, (
            "a map folded from the REPLACED file must not be memoised under the "
            "new file's identity — the next call would serve another file's "
            f"records: {sdk._journal_presence_memo}")

        # and the answer must come from the file that is there NOW, from byte 0
        before = len(scans)
        assert sdk._journal_knows_point("old-0007") is False, (
            "an id only the REPLACED journal held must not read as "
            "journal-known — that is the silence #10515 indicator 1 removes")
        assert scans[before:] == [(None, 0)], (
            "the re-fold must start at byte 0 of the new journal, not resume "
            f"over a prefix of the old one: {scans[before:]}")
        assert sdk._journal_knows_point("new-0007") is True, (
            "the file that is actually there must answer for its own records")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_map_present_id_is_unjudged_when_the_fold_loses_the_file(
        tmp_path, monkeypatch, caplog):
    """#10543 review round 16, finding 1 (second site): the RETAINED MAP is evidence.

    When the resume fold raises ``FileNotFoundError`` the answer used to be
    ``narrowed.get(point_id, False)`` — and on the resume path ``narrowed`` is
    empty by construction, so it was ALWAYS ``False``: an id the retained map
    held as PRESENT answered "a typo" (silent). The map was folded from records
    that named this point, so the honest answer to a journal that went away
    under the fold is UNJUDGED (``None`` ⇒ ``journal_unreadable``) — the same
    vocabulary round 7 established for a file removed mid-flight.

    The sidecar is deleted WITH the journal here on purpose: with the sidecar in
    place ``carried_records()`` already reports the loss, so the arms would be
    indistinguishable and this test would pass on the pre-fix tree.

    Non-vacuity: on the pre-fix tree the crossing call increments nothing (it
    reports the id as a typo); ``_journal_carried_records`` answers ``False``
    because the sidecar is gone, so only the MAP arm can save it.
    """
    from tortoise import monitoring
    from tortoise.log import EventLog

    sdk, journal = _sdk_journaled(tmp_path, "midfoldGone")
    try:
        # ⛔ INITIALISE THE PROJECTION FIRST. The embedded lane AUTO-REPLAYS a
        # journal into an EMPTY db on first use, so records appended BEFORE
        # that point end up in the live graph and the ABSENT arm never runs —
        # the failure mode that makes this test vacuous rather than wrong.
        sdk._get_proj()
        log = sdk._get_event_log()
        pid = "held-0007"
        log.append({"type": "PointAdded", "point": {"id": pid}})
        assert not sdk.get_point(pid), (
            "the live graph must NOT hold the point — the journal alone does")

        # a first absent write folds the journal and RETAINS the map from it
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        memo = sdk._journal_presence_memo
        assert memo is not None, "the fixture needs a RETAINED map to test"
        assert memo['known'].get(pid) is True, (
            f"the map must hold the id as PRESENT: {memo['known'].get(pid)}")

        # an append makes the next call RESUME (a fold that starts above byte 0)
        log.append({"type": "PointAdded", "point": {"id": "grow-9999"}})

        real = EventLog.mention_lines

        def vanish(self, needle=None, start=0):
            if start and needle is None:
                # the journal AND its position sidecar go away UNDER the fold
                for path in (journal, journal + ".position"):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(path)
                raise FileNotFoundError(journal)
            return real(self, needle, start)

        monkeypatch.setattr(EventLog, "mention_lines", vanish)

        before = monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0)
        known_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(pid, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0) == before + 1, (
            "a journal whose file went away under the fold cannot be judged, "
            "and the retained map said it HELD this id — answering \"a typo\" "
            "is the silence #10515 indicator 1 forbids")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == known_before, (
            "the map proves the journal CARRIED the id but cannot prove it "
            "still does, so this is UNREADABLE, not known")
        assert any("FILE is gone" in r.getMessage()
                   for r in caplog.records), (
            "the loss must be LOUD in the log, not only in a counter")
        assert sdk._journal_presence_memo is None
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_map_present_id_outlives_an_unlink_after_the_fold(
        tmp_path, monkeypatch, caplog):
    """#10515 review round 17, finding 1: ``stat_after is None`` has its OWN evidence.

    ``unlink(2)`` keeps a file readable through an already-open descriptor —
    "the file will remain in existence until the last file descriptor referring
    to it is closed" — so a deletion that lands AFTER the fold's ``open()`` and
    BEFORE its post-fold ``os.stat`` raises nothing inside the fold: the fold
    really did read this journal's bytes, and the only trace is ``stat_after is
    None``. That makes ``known`` EVIDENCE for this call, not a stale map.

    Pre-fix the two facts shared one arm, the needle was called MAP-FREE, and
    the answer for a map-PRESENT id was ``False`` — "a typo": no WARNING and no
    counter, i.e. the silent loss #10515 indicator 1 forbids, and a regression
    against ``main``'s loud ``known.get(point_id, False)``.

    Non-vacuity: the position sidecar is deleted WITH the journal, so
    ``carried_records()`` answers ``False`` and only the retained map can
    produce ``journal_unreadable``; the pre-fix arm increments nothing.
    """
    from tortoise import monitoring

    sdk, journal = _sdk_journaled(tmp_path, "midfoldUnlink")
    try:
        # ⛔ INITIALISE THE PROJECTION FIRST: the embedded lane auto-replays a
        # journal into an EMPTY db on first use, which would put the id in the
        # live graph and retire the ABSENT arm entirely.
        sdk._get_proj()
        log = sdk._get_event_log()
        pid = "kept-0011"
        log.append({"type": "PointAdded", "point": {"id": pid}})
        assert not sdk.get_point(pid), (
            "the live graph must NOT hold the point — the journal alone does")

        # a first absent write folds the journal and RETAINS the map from it
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        memo = sdk._journal_presence_memo
        assert memo is not None, "the fixture needs a RETAINED map to test"
        assert memo['known'].get(pid) is True, (
            f"the map must hold the id as PRESENT: {memo['known'].get(pid)}")

        # an append makes the next call RESUME (a fold that starts above byte 0)
        log.append({"type": "PointAdded", "point": {"id": "grow-1111"}})

        # The unlink lands between the fold and the POST-FOLD stat. unlink(2)
        # leaves the descriptor the fold already holds readable, so the fold
        # completes normally and only `_journal_stat_key` sees the path gone —
        # the `stat_after is None` arm, not the fold's FileNotFoundError arm.
        real_stat_key = TortoiseSDK._journal_stat_key
        calls = {'n': 0}

        def stat_key_then_unlink(log):
            calls['n'] += 1
            if calls['n'] == 2:
                for path in (journal, journal + ".position"):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(path)
                return None
            return real_stat_key(log)

        monkeypatch.setattr(TortoiseSDK, '_journal_stat_key',
                            staticmethod(stat_key_then_unlink))
        calls['n'] = 0

        before = monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0)
        known_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(pid, confidence=0.9) == {}
        assert calls['n'] == 2, (
            "the fixture must reach the POST-FOLD stat (the resume path), not "
            f"the fold's own FileNotFoundError arm: {calls['n']} stat calls")
        assert not os.path.exists(journal), (
            "the fixture must actually remove the journal before the re-fold")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0) == before + 1, (
            "the fold read a file this call watched disappear, and the "
            "retained map said it HELD this id — answering \"a typo\" is the "
            "silence #10515 indicator 1 forbids")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == known_before, (
            "the map proves the journal CARRIED the id but cannot prove it "
            "still does, so this is UNREADABLE, not known")
        assert any("could not be READ to rule a typo out" in r.getMessage()
                   for r in caplog.records), (
            "the loss must be LOUD in the log, not only in a counter")
        assert not any("the journal still holds the id" in r.getMessage()
                       for r in caplog.records), (
            "the map proves the journal CARRIED the id, not that it still "
            "does — the reason must be unreadable, never known")
        assert sdk._journal_presence_memo is None, (
            "a map folded from a file whose path is gone must not be kept")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_truncation_under_the_fold_keeps_the_map_as_witness(
        tmp_path, monkeypatch, caplog):
    """#10515 review round 18, finding 1: a SHRINK is not a REPLACEMENT.

    Round 17 split ``stat_after is None`` out of the shared post-fold arm and
    handed the retained map to the needle as a WITNESS. The OTHER half of that
    predicate — ``stat_after[2] < resume`` — stayed on the MAP-FREE arm, and it
    is not the same evidence: no new file exists there, the bytes the fold read
    were merely truncated away, and ``known`` was folded over exactly those
    bytes. So a truncation landing between the fold and the post-fold ``stat``
    answered "a typo" — no counter, no WARNING. ⚠️ That silence is this
    branch's own, not `main`'s parity: `origin/main` folds truncation together
    with replacement into ONE map-free arm
    (``stat_after[2] < resume or stat_after[:2] != stat_key[:2]`` →
    ``_journal_presence_needle(log, point_id)``), so the base answers ``False``
    for this interleaving too. The witness is a deliberate TIGHTENING over it;
    the ``known.get(point_id, False)`` this docstring used to cite as "`main`
    LOUD" is the older `fcc742b8a` single-arm shape, not the base. The mirror
    image of the round-16 silence, in the direction #10515 indicator 1 forbids.
    The comparison the field makes is the same one: identity (inode) decides
    REPLACEMENT, offset vs size decides TRUNCATION.

    The position sidecar goes away WITH the bytes on purpose, so
    ``_journal_carried_records`` cannot answer and only the retained map can
    report the loss.

    Non-vacuity: on the pre-fix tree the crossing call increments nothing — the
    re-read of the shorter file simply misses the id.
    """
    from tortoise import monitoring

    sdk, journal = _sdk_journaled(tmp_path, "midfoldTruncate")
    try:
        # ⛔ INITIALISE THE PROJECTION FIRST (the embedded lane auto-replays a
        # journal into an EMPTY db on first use, which would put the id in the
        # live graph and retire the ABSENT arm).
        sdk._get_proj()
        log = sdk._get_event_log()
        pid = "trunc-0021"
        log.append({"type": "PointAdded", "point": {"id": pid}})
        assert not sdk.get_point(pid), (
            "the live graph must NOT hold the point — the journal alone does")

        # a first absent write folds the journal and RETAINS the map from it
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        memo = sdk._journal_presence_memo
        assert memo is not None, "the fixture needs a RETAINED map to test"
        assert memo['known'].get(pid) is True, (
            f"the map must hold the id as PRESENT: {memo['known'].get(pid)}")

        # an append makes the next call RESUME (a fold that starts above byte 0)
        log.append({"type": "PointAdded", "point": {"id": "grow-2222"}})
        resume = os.path.getsize(journal)
        assert resume > 0, "the fixture needs a non-empty journal to truncate"

        real_stat_key = TortoiseSDK._journal_stat_key
        calls = {'n': 0}

        def stat_key_then_truncate(log):
            calls['n'] += 1
            if calls['n'] == 2:
                # truncate IN PLACE — same inode, and the bytes the fold just
                # read are the ones cut away — and take the sidecar with them
                with open(journal, "w", encoding="utf-8"):
                    pass
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(journal + ".position")
            return real_stat_key(log)

        monkeypatch.setattr(TortoiseSDK, '_journal_stat_key',
                            staticmethod(stat_key_then_truncate))
        calls['n'] = 0

        before = monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0)
        known_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(pid, confidence=0.9) == {}
        assert calls['n'] == 2, (
            "the fixture must reach the POST-FOLD stat (the resume path), not "
            f"the fold's own FileNotFoundError arm: {calls['n']} stat calls")
        truncated = os.stat(journal)
        assert (truncated.st_dev, truncated.st_ino) == memo['ident'], (
            "the fixture must truncate the SAME inode — a new inode is the "
            "REPLACEMENT arm, which re-folds the NEW file and hands the map on "
            "as a witness too, so it would exercise different evidence")
        assert truncated.st_size < resume, (
            "the fixture must shrink the journal BELOW the folded offset")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0) == before + 1, (
            "the fold read bytes this same inode no longer holds, and the "
            "retained map said it HELD this id — answering \"a typo\" is the "
            "silence #10515 indicator 1 forbids")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == known_before, (
            "the map proves the journal CARRIED the id but cannot prove the "
            "truncated file still does, so this is UNREADABLE, not known")
        assert any("could not be READ to rule a typo out" in r.getMessage()
                   for r in caplog.records), (
            "the loss must be LOUD in the log, not only in a counter")
        assert sdk._journal_presence_memo is None, (
            "a map folded from bytes that are gone must not be kept")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_map_present_id_is_unjudged_when_the_path_is_recreated(
        tmp_path, monkeypatch, caplog):
    """#10515 review round 18, finding 1 (second sub-case): the witness must
    also cover a re-read that SUCCEEDS.

    Round 17 applied the witness only in the needle's ``except
    FileNotFoundError`` branch. The ``stat_after is None`` arm can still reach a
    SUCCESSFUL re-read — the path is recreated between the post-fold ``stat``
    and the needle's own read (a writer, a rotated-in file, a restore) — and
    then the exception never fires: the re-read simply finds no record for the
    id and the call answered ``False``, "a typo", while ``known`` proved the
    journal it folded NAMED that id. A destroyed record must be UNJUDGED.

    Non-vacuity: on the pre-fix tree the crossing call increments nothing — the
    recreated file exists and answers, so no exception path is taken.
    """
    from tortoise import monitoring

    sdk, journal = _sdk_journaled(tmp_path, "midfoldRecreate")
    try:
        sdk._get_proj()
        log = sdk._get_event_log()
        pid = "kept-0022"
        log.append({"type": "PointAdded", "point": {"id": pid}})
        assert not sdk.get_point(pid), (
            "the live graph must NOT hold the point — the journal alone does")

        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        memo = sdk._journal_presence_memo
        assert memo is not None and memo['known'].get(pid) is True, (
            f"the fixture needs the id PRESENT in a retained map: {memo}")

        log.append({"type": "PointAdded", "point": {"id": "grow-3333"}})

        real_stat_key = TortoiseSDK._journal_stat_key
        calls = {'n': 0}

        def stat_key_then_recreate(log):
            calls['n'] += 1
            if calls['n'] == 2:
                # the post-fold stat observes NO file at the path …
                for path in (journal, journal + ".position"):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(path)
                # … and a writer puts a NEW journal there before the needle
                # re-reads, so that read SUCCEEDS and carries no such record
                with open(journal, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps({"type": "PointAdded",
                                         "point": {"id": "fresh-4444"}})
                             + "\n")
                return None
            return real_stat_key(log)

        monkeypatch.setattr(TortoiseSDK, '_journal_stat_key',
                            staticmethod(stat_key_then_recreate))
        calls['n'] = 0

        before = monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0)
        known_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(pid, confidence=0.9) == {}
        assert calls['n'] == 2, (
            "the fixture must reach the POST-FOLD stat: "
            f"{calls['n']} stat calls")
        assert os.path.exists(journal) and os.path.getsize(journal) > 0, (
            "the recreated journal must ANSWER the needle's read — that is what "
            "makes the pre-fix answer a silent False instead of an exception")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0) == before + 1, (
            "the fold read a file this call watched disappear, and its retained "
            "map said that file HELD this id — a recreated path cannot confirm "
            "it, so the absence is UNJUDGED, never \"a typo\"")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == known_before, (
            "nothing proves the id still carries a record, so this is "
            "UNREADABLE, not known")
        assert any("could not be READ to rule a typo out" in r.getMessage()
                   for r in caplog.records), (
            "the loss must be LOUD in the log, not only in a counter")
        assert sdk._journal_presence_memo is None
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_delete_in_the_recreated_path_is_answered_not_escalated(
        tmp_path, monkeypatch, caplog):
    """#10515 review round 19, finding 1: only a VACUOUS ``False`` is unjudged.

    The companion of `test_a_map_present_id_is_unjudged_when_the_path_is_recreated`,
    and the case that keeps round 18's witness override from overshooting. Both
    tests put a witnessed id through a recreated path, and the re-read SUCCEEDS
    in both; what differs is what the re-read FINDS:

    * there, no line names the id, so the read has no evidence and the witness'
      ``True`` is unconfirmed ⇒ ``None`` (``journal_unreadable``);
    * here, an ``EntityMutated {op: delete}`` for that id is folded — the needle
      MATCHED the line, parsed it, and folded it to ``False``. The journal now
      says the point is GONE, which is an ANSWER, not an absence of one.

    Round 18's condition tested the dumped value (``answer is False``) rather
    than that fact, so this path answered ``None`` and filed `journal_unreadable`
    for an ordinary write-after-delete — #10515 indicator 2, "a counter that
    fires on every write-after-delete can never be alerted on", and the reading
    ``_journal_knows_point`` already applies to its retained map
    (``"The INVERSE map value (False) is NOT the same case"``).

    Non-vacuity: on the pre-fix tree this call increments `journal_unreadable`
    by one and warns "could not be READ to rule a typo out" — both asserted
    AGAINST below, so reverting only the ``point_id not in state`` term reddens
    this test.
    """
    from tortoise import monitoring

    sdk, journal = _sdk_journaled(tmp_path, "midfoldRecreateDelete")
    try:
        sdk._get_proj()
        log = sdk._get_event_log()
        pid = "deleted-0023"
        log.append({"type": "PointAdded", "point": {"id": pid}})
        assert not sdk.get_point(pid), (
            "the live graph must NOT hold the point — the journal alone does")

        # a first absent write folds the journal and RETAINS the map from it
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        memo = sdk._journal_presence_memo
        assert memo is not None and memo['known'].get(pid) is True, (
            f"the fixture needs the id PRESENT in a retained map: {memo}")

        # an append makes the next call RESUME (a fold that starts above byte 0)
        log.append({"type": "PointAdded", "point": {"id": "grow-5555"}})

        real_stat_key = TortoiseSDK._journal_stat_key
        calls = {'n': 0}

        def stat_key_then_recreate_with_delete(log):
            calls['n'] += 1
            if calls['n'] == 2:
                # the post-fold stat observes NO file at the path …
                for path in (journal, journal + ".position"):
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(path)
                # … and a writer puts a NEW journal there before the needle
                # re-reads, so that read SUCCEEDS and folds a DELETE for the id
                with open(journal, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps({"type": "EntityMutated",
                                         "id": pid, "label": "Point",
                                         "op": "delete"}) + "\n")
                return None
            return real_stat_key(log)

        monkeypatch.setattr(TortoiseSDK, '_journal_stat_key',
                            staticmethod(stat_key_then_recreate_with_delete))
        calls['n'] = 0

        before = monitoring.silent_revision_loss_counts()
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(pid, confidence=0.9) == {}
        assert calls['n'] == 2, (
            "the fixture must reach the POST-FOLD stat: "
            f"{calls['n']} stat calls")
        assert monitoring.silent_revision_loss_counts() == before, (
            "the recreated journal carries a DELETE for this id, so the re-read "
            "has positive evidence that the point is gone — an ordinary "
            "write-after-delete must never move the counter (#10515 "
            "indicator 2: a counter that fires on every write-after-delete can "
            "never be alerted on)")
        assert not any("could not be READ to rule a typo out" in r.getMessage()
                       for r in caplog.records), (
            "a folded delete is a READ answer, not an UNREADABLE journal — the "
            "round-18 witness override must not escalate it")
        assert not any("will not be replayed" in r.getMessage()
                       for r in caplog.records), (
            "a deleted point is deliberately gone; it is not a lost revision")
        assert sdk._journal_presence_memo is None, (
            "the map still came from a file the path no longer names, so it "
            "must not be kept, whatever this call answered")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_replacement_reports_a_map_present_id_as_unjudged_not_a_typo(
        tmp_path, monkeypatch, caplog):
    """A REPLACEMENT is evidence about the OLD file, not an erasure of it.

    Review round 20, finding 1. `rename(2)`: *"Open file descriptors for oldpath
    are also unaffected"* — so the fd the fold read is still the OLD journal even
    after the path resolves to a new inode. The retained map was folded from
    that fd, so an entry saying ``True`` is a RECORD of a revision that existed
    at this path and that the live graph does not hold.

    The tree used to answer this arm MAP-FREE, which made it the only post-fold
    arm that stayed QUIET: the re-read of the NEW file finds no line for the id
    and returns ``False``, `_journal_knows_point` reads ``False`` as "a typo"
    and `_report_absent_revision` returns without a line or a counter. That is
    #10515 indicator 1 — a silent legitimate-revision loss. ⚠️ It is a
    TIGHTENING over this PR's base, not a restoration of parity: `origin/main`
    folds truncation together with replacement into ONE map-free arm, so the
    base answers ``False`` (silent) here as well. The "`main` reported it as
    ``journal_known``" reading this docstring used to give is the older
    `fcc742b8a` single-arm shape, which the base no longer carries.

    The witness is applied in the round-19 shape, so it can only make the answer
    LOUDER: the re-read carries no line for the id, so its ``False`` is VACUOUS
    and the answer is UNJUDGED (``None`` ⇒ ``journal_unreadable`` + WARNING). A
    folded delete or merge loser (``point_id in state``) still answers ``False``
    and stays silent — see
    `test_a_delete_in_the_recreated_path_is_answered_not_escalated`.

    ⛔ NON-VACUITY, and the reason the docstring that used to sit here was
    deleted: it claimed "it passes on both trees", which is measurably false.
    On `main` this interleaving returns ``False`` — the shared truncation-or-
    replacement arm is MAP-FREE there, so it increments nothing — and on this
    branch's own pre-round-20 tree it also returns ``False`` (silent), so the
    assertion below on ``journal_unreadable`` fails on BOTH and only the fixed
    tree satisfies it. (The earlier "``main`` returns ``True`` (``journal_known``
    +1)" reading described `fcc742b8a`'s single arm, not this base.)
    The ``old-0055`` id is in the journal that was replaced away, which is why
    it is a LOSS and not the "absent from BOTH graph and journal" typo shape
    that #10515 indicator 2 reserves silence for.
    """
    from tortoise import monitoring
    from tortoise.log import EventLog

    sdk, journal = _sdk_journaled(tmp_path, "replaceWitness")
    try:
        sdk._get_proj()
        log = sdk._get_event_log()
        pid = "old-0055"
        log.append({"type": "PointAdded", "point": {"id": pid}})
        assert not sdk.get_point(pid)

        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        memo = sdk._journal_presence_memo
        assert memo is not None and memo['known'].get(pid) is True
        old_stat = os.stat(journal)

        # an append makes the crossing call RESUME, so it takes the map path and
        # can observe the replacement instead of answering from the memo
        log.append({"type": "PointAdded", "point": {"id": "grow-5555"}})

        # a DIFFERENT journal (new inode, no record for `pid`) replaces the path
        # when the resuming fold ends — the replacement arm, not truncation
        replacement = journal + ".replacement"
        other = EventLog(replacement)
        other.append({"type": "PointAdded", "point": {"id": "brand-new-9"}})

        real = EventLog.mention_lines
        armed = {'on': True}

        class _ReplacingScan:
            def __init__(self, inner, start, needle):
                self._inner = inner
                self.resume_offset = getattr(inner, 'resume_offset', start)
                self._key = (needle, start)

            def __iter__(self):
                yield from self._inner
                self.resume_offset = self._inner.resume_offset
                # the map-path fold (needle None) has finished, whatever offset
                # it RESUMED from, and the path has not been observed since
                if armed['on'] and self._key[0] is None:
                    armed['on'] = False
                    os.replace(replacement, journal)

        def spy(self, needle=None, start=0):
            return _ReplacingScan(real(self, needle, start), start, needle)

        monkeypatch.setattr(EventLog, "mention_lines", spy)

        before = monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0)
        known_before = monitoring.silent_revision_loss_counts().get(
            "journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(pid, confidence=0.9) == {}
        new_stat = os.stat(journal)
        assert (new_stat.st_dev, new_stat.st_ino) != (
            old_stat.st_dev, old_stat.st_ino), (
            "the fixture must actually REPLACE the journal (a new inode), or "
            "this is the truncation arm and asserts nothing about this one")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_unreadable", 0) == before + 1, (
            "the retained map — folded, through a live fd, from the file this "
            "path no longer names — says the journal CARRIED this id, and the "
            "re-read of the new file cannot rule the loss out. Answering "
            "\"a typo\" is the silence #10515 indicator 1 forbids — a "
            "tightening over `main`, whose shared truncation-or-replacement arm "
            "is map-free and stays silent here")
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == known_before, (
            "the map proves the journal CARRIED the id, not that the file at "
            "this path still does — so this is UNREADABLE, never known")
        assert any("could not be READ to rule a typo out" in r.getMessage()
                   for r in caplog.records), (
            "the loss must be LOUD in the log, not only in a counter")
        assert not any("the journal still holds the id" in r.getMessage()
                       for r in caplog.records), (
            "nothing re-read here names the id, so the reason is unreadable, "
            "never known")
        assert sdk._journal_presence_memo is None, (
            "the memo still came from a file the path no longer names")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_an_empty_id_does_not_scan_the_journal_at_all(tmp_path, monkeypatch):
    """An EMPTY id can never be a journaled point, and its needle is every line.

    ``b''`` is a substring of every record, so the scan would JSON-parse the WHOLE
    journal to answer a typo — the same cheap-request → full-journal-I/O amplifier
    review round 8, finding 2 names, in its worst form. Answering directly is both
    correct and free.
    """
    from tortoise.log import EventLog

    sdk, _journal = _sdk_journaled(tmp_path, "emptyid")
    try:
        sdk._get_event_log().append({"type": "PointAdded", "point": {"id": "x"}})
        scans: list[tuple[int | None, int]] = []
        real = EventLog.mention_lines

        def spy(self, needle=None, start=0):
            scans.append((needle, start))
            return real(self, needle, start)

        monkeypatch.setattr(EventLog, "mention_lines", spy)
        assert sdk._journal_knows_point("") is False, (
            "an empty id is not a journaled point")
        assert scans == [], (
            f"an empty id must not read the journal at all, saw {scans}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_journal_truncated_and_regrown_in_place_drops_the_stale_map(
        tmp_path, caplog):
    """#10543 round 9, finding 2: growth is NOT proof the folded prefix survived.

    The memo answered from a presence map folded from the file as it was, and the
    only guard on reusing it was "same inode and it did not shrink below
    ``folded_to``". An in-place truncate that is then REGROWN past that offset
    leaves the same inode at a LARGER size, so the map was reused over a file whose
    bytes below the seam are different content — every answer then came from
    records the file no longer holds.

    Both directions of that are harmful, and this pins the dangerous one: the id's
    last record in the FIRST journal is a hard delete (folds ABSENT), and the
    regrown journal re-adds it as a point BELOW the old watermark (the truth is
    PRESENT), so a stale map silences a genuine revision loss — #10515 indicator 1.
    The re-add deliberately sits UNDER the seam: the resuming fold re-reads
    everything appended since, so a correcting record at the tail would mask the
    stale prefix and this test would pass either way. The inverse control at the
    end (a genuine typo stays silent after the re-fold) keeps the fix from passing
    by alarming everything.
    """
    from tortoise import monitoring

    sdk, journal = _sdk_journaled(tmp_path, "regrown")
    try:
        log = sdk._get_event_log()
        point_id = str(uuid.uuid4())     # absent from the LIVE graph either way
        for i in range(40):
            log.append({"type": "PointAdded", "point": {"id": f"seed-{i:04d}"}})
        # …and this id's LAST record in the first journal is a hard delete
        log.append({"type": "EntityMutated", "id": point_id,
                    "label": "Point", "op": "delete"})

        # ── build the map: one absent read folds every record above ──
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        memo = sdk._journal_presence_memo
        assert memo is not None and memo["folded_to"] == os.path.getsize(journal), (
            "the map must be complete for the whole journal, or this is not the "
            "prefix the guard reuses")
        assert sdk._journal_knows_point(point_id) is False, (
            "the first journal's last record for this id is a delete")
        before = monitoring.silent_revision_loss_counts().get("journal_known", 0)

        # ── truncate IN PLACE (same inode) and regrow PAST the watermark ──
        with open(journal, "w", encoding="utf-8") as fh:
            # the re-add goes FIRST, i.e. below the old watermark: a record at the
            # tail would be re-read by the resumed fold whatever the map said
            fh.write(json.dumps({"type": "PointAdded",
                                 "point": {"id": point_id}}) + "\n")
            for i in range(60):
                fh.write(json.dumps({"type": "PointAdded",
                                     "point": {"id": f"rewritten-{i:04d}"}}) + "\n")
        assert os.stat(journal).st_ino == memo["ident"][1], (
            "the case only means anything if the file was rewritten in place")
        assert os.path.getsize(journal) > memo["folded_to"], (
            "the case is a regrow PAST the folded offset — a file that ends below "
            "it is already re-folded by the size comparison")

        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(point_id, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == before + 1, (
            "the regrown journal re-adds the id as a POINT, so the absent live "
            "read is a journal-known loss — answering from the pre-truncation map "
            "(whose last record for it was a delete) silences it, which is the "
            "direction #10515 indicator 1 forbids: same inode + a larger size is "
            "not proof the bytes below the seam survived")
        assert any("will not be replayed" in r.getMessage()
                   and point_id in r.getMessage() for r in caplog.records), (
            "the loss is reported with the id it lost")

        # ── inverse control: the re-fold must not turn typos into alarms ──
        after = monitoring.silent_revision_loss_counts().get("journal_known", 0)
        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == after, (
            "re-folding must leave the id-absent-from-BOTH silence intact")
        assert not caplog.records, (
            "a typo on a re-folded map is still a typo")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_map_past_its_bound_latches_the_needle_pass_instead_of_refolding(
        tmp_path, monkeypatch):
    """#10543 round 10, finding 1: the bound was checked AFTER the fold it bounds.

    ``_JOURNAL_PRESENCE_MAP_MAX`` is documented as the point past which "the map is
    DROPPED and the call takes the needle-filtered pass instead … a bounded-memory
    degradation". Neither half held:

    * the fold built the WHOLE over-bound dict and only then looked at its length,
      so the pass peaked at one entry per distinct journal id — the memory the
      constant exists to bound, unbound;
    * every memo that survives storage has ``len(known) <= MAX`` by construction,
      so ``len(known) > MAX`` on the reuse path was UNREACHABLE. The next absent
      write saw no memo, re-folded from byte 0, crossed again and released again
      — for ever. That is the O(journal)-per-absent-write cost round 8 removed,
      restored on exactly the large journal the constant is for.

    Both are pinned here, because a test that only counted reads would pass on a
    fix that latched the degradation but still built the dict once, and a test
    that only measured the peak would pass on one that kept re-folding cheaply.
    """
    from tortoise import sdk as sdk_mod
    from tortoise.log import EventLog

    monkeypatch.setattr(sdk_mod, "_JOURNAL_PRESENCE_MAP_MAX", 3)
    sdk, _journal = _sdk_journaled(tmp_path, "bound")
    try:
        log = sdk._get_event_log()
        for i in range(12):
            log.append({"type": "PointAdded", "point": {"id": f"bounded-{i:04d}"}})

        scans: list[tuple[bytes | None, int]] = []
        real_scan = EventLog.mention_lines

        def scan_spy(self, needle=None, start=0):
            scans.append((needle, start))
            return real_scan(self, needle, start)

        widths: list[int] = []
        real_state = sdk_mod._journal_presence_state

        def state_spy(log_, state, start=0, needle=None, **kwargs):
            try:
                return real_state(log_, state, start, needle, **kwargs)
            finally:
                # runs on the over-bound raise too — that is the peak
                widths.append(len(state))

        monkeypatch.setattr(EventLog, "mention_lines", scan_spy)
        monkeypatch.setattr(sdk_mod, "_journal_presence_state", state_spy)

        # ── the crossing pass: 12 ids against a bound of 3 ──
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert max(widths) <= 4, (
            "the fold must STOP at the bound (limit + 1) rather than build a dict "
            f"proportional to the journal's distinct-id count: widths={widths}")
        assert sdk._journal_presence_memo is None, (
            "an over-bound map is not retained")
        # the crossing call itself answers through the needle pass after the fold
        # stops — that retry is the fix working, not a second full read
        assert scans[0][0] is None, f"the first pass folds every record: {scans}"
        assert scans[-1][0] is not None, (
            f"the crossing call answers from the needle pass: {scans}")
        assert len([s for s in scans if s[0] is None]) == 1, (
            f"exactly ONE full fold, and it stopped at the bound: {scans}")

        # ── the NEXT absent write must take the needle pass, not re-fold ──
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        assert len(scans) == 3, f"one pass per absent write, saw {scans}"
        assert scans[2][0] is not None, (
            "past the bound the needle-filtered pass is the STEADY STATE — a "
            "second full fold from byte 0 is the O(journal) cost round 8 removed, "
            f"and it is what the unreachable post-fold guard allowed: {scans[2]}")
        assert len([s for s in scans if s[0] is None]) == 1, (
            f"no later call may re-fold the whole journal: {scans}")
        assert max(widths) <= 4, (
            f"no later pass may rebuild an over-bound map either: widths={widths}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_seam_that_cannot_be_read_drops_the_map_instead_of_trusting_it(
        tmp_path, caplog):
    """#10543 round 10, finding 2: "unanswerable" was read as "no mismatch".

    ``_read_regular_range`` opens with ``O_NOFOLLOW``, so a SYMLINKED journal path
    raises ``ELOOP`` before ``S_ISREG`` is ever reached — while everything else
    keeps working, because ``os.stat`` and ``mention_lines``' plain ``open`` both
    FOLLOW the link. So the map stays live and only the seam check dies, which is
    the worst combination: the guard that exists for exactly this lane silently
    does not run.

    Pre-fix the empty answer collapsed into ``None`` ⇒ "no seam, no claim to
    disprove" ⇒ the stale map was REUSED over an in-place truncate+regrow. The
    harmful direction is pinned here: the id's last record in the first journal is
    a hard delete (folds ABSENT), the regrown journal re-adds it as a point BELOW
    the watermark (the truth is PRESENT), so a reused map silences a genuine
    revision loss — #10515 indicator 1.
    """
    from tortoise import monitoring

    sdk, journal = _sdk_journaled(tmp_path, "seamunreadable")
    try:
        point_id = str(uuid.uuid4())     # absent from the LIVE graph either way
        target = tmp_path / "seamunreadable-target.jsonl"
        with open(target, "w", encoding="utf-8") as fh:
            for i in range(40):
                fh.write(json.dumps({"type": "PointAdded",
                                     "point": {"id": f"seed-{i:04d}"}}) + "\n")
            fh.write(json.dumps({"type": "EntityMutated", "id": point_id,
                                 "label": "Point", "op": "delete"}) + "\n")
        # the journal path is a SYMLINK: `mention_lines` follows it, `tail_bytes`
        # (O_NOFOLLOW) cannot open it at all
        os.symlink(target, journal)

        # ── build the map: the seam read on this path CANNOT answer ──
        assert sdk.update_point(str(uuid.uuid4()), confidence=0.9) == {}
        memo = sdk._journal_presence_memo
        assert memo is not None and memo["folded_to"] == os.path.getsize(journal), (
            "the map must be complete for the whole journal, or this is not the "
            "prefix the guard reuses")
        assert sdk._journal_knows_point(point_id) is False, (
            "the first journal's last record for this id is a delete")
        before = monitoring.silent_revision_loss_counts().get("journal_known", 0)

        # ── truncate IN PLACE (same inode, through the link) and regrow PAST it ──
        with open(journal, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "PointAdded",
                                 "point": {"id": point_id}}) + "\n")
            for i in range(60):
                fh.write(json.dumps({"type": "PointAdded",
                                     "point": {"id": f"rewritten-{i:04d}"}}) + "\n")
        assert os.stat(journal).st_ino == memo["ident"][1], (
            "the case only means anything if the file was rewritten in place")
        assert os.path.getsize(journal) > memo["folded_to"], (
            "the case is a regrow PAST the folded offset")

        caplog.clear()
        with caplog.at_level("WARNING"):
            assert sdk.update_point(point_id, confidence=0.9) == {}
        assert monitoring.silent_revision_loss_counts().get(
            "journal_known", 0) == before + 1, (
            "a seam read that cannot ANSWER must be read as a MISMATCH and drop "
            "the map — reading it as 'no seam to compare' reuses a map folded "
            "from bytes this file no longer holds and silences the loss")
        assert any("will not be replayed" in r.getMessage()
                   and point_id in r.getMessage() for r in caplog.records), (
            "the loss is reported with the id it lost")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class TestJournalSeamContract:
    """``EventLog.tail_bytes``' three answers, pinned where the guard reads them.

    ``None`` (this path cannot answer) and ``b''`` (a genuinely empty prefix) are
    different facts with OPPOSITE safe directions, and the caller is written to
    that contract (#10543 round 10, finding 2). It had no test of its own before
    this round, which is why the collapse went unnoticed.
    """

    def test_a_symlinked_journal_is_unanswerable_not_empty(self, tmp_path):
        from tortoise.log import EventLog

        target = tmp_path / "real.jsonl"
        target.write_bytes(b"0123456789")
        link = tmp_path / "linked.jsonl"
        os.symlink(target, link)
        assert EventLog(str(link)).tail_bytes(10, 4) is None, (
            "O_NOFOLLOW refuses the link, so this path cannot answer — and the "
            "caller must be able to tell that from an empty prefix")
        assert EventLog(str(target)).tail_bytes(10, 4) == b"6789", (
            "the same read through the real path answers")

    def test_a_directory_or_missing_path_is_unanswerable(self, tmp_path):
        from tortoise.log import EventLog

        assert EventLog(str(tmp_path)).tail_bytes(10, 4) is None, (
            "a directory is not a regular file")
        assert EventLog(str(tmp_path / "absent.jsonl")).tail_bytes(10, 4) is None, (
            "a path that is gone cannot answer either — the caller's one safe "
            "reading of every unanswerable shape is the same: re-fold")

    def test_an_empty_prefix_is_empty_bytes_not_unanswerable(self, tmp_path):
        from tortoise.log import EventLog

        target = tmp_path / "real.jsonl"
        target.write_bytes(b"0123456789")
        assert EventLog(str(target)).tail_bytes(0, 4) == b"", (
            "offset 0 is 'nothing was folded yet' — a MATCH, not a mismatch")


class TestJournalMentionScan:
    """The journal byte-scan's MECHANICS, pinned where they now live (#10543).

    ``EventLog.mention_lines`` is the bounded-memory half of the ABSENT arm's
    presence check (#10515). Its window/tail/separator handling used to sit in
    ``sdk.py`` behind a bare ``open()`` — which the capability model correctly
    reads as "this SDK operation reaches a filesystem path", so it re-classified
    ``update_point`` (and every method that shares its write path: 33 fs methods
    against ``main``'s 12) as an operator-only filesystem operation and redded
    ``tests/test_tool_registry.py``. A journal read through the LOG HANDLE is the
    shape that model already declares non-filesystem, and keeping the mechanics
    on ``EventLog`` is what makes that true rather than an exemption.

    These tests fail on the pre-fix tree by construction (the method did not
    exist), and each one pins a behaviour the fold depends on and cannot see
    from the outside: the carried partial line, the CR separator, the needle
    filter, and the missing-file contract the silent early-life path relies on.
    """

    @staticmethod
    def _journal(tmp_path, name: str, records, sep=b"\n") -> str:
        path = tmp_path / name
        path.write_bytes(sep.join(
            json.dumps(r, ensure_ascii=False).encode("utf-8") for r in records))
        return str(path)

    def test_only_lines_naming_the_needle_are_yielded(self, tmp_path):
        from tortoise.log import EventLog

        journal = self._journal(tmp_path, "needle.jsonl", [
            {"type": "PointAdded", "point": {"id": "wanted"}},
            {"type": "PointAdded", "point": {"id": "other"}},
            {"type": "PointRevised", "id": "wanted"},
        ])
        seen = list(EventLog(journal).mention_lines(b"wanted"))
        assert len(seen) == 2, f"one line per mention, saw {seen!r}"
        ids = [json.loads(line.decode("utf-8")) for line in seen]
        assert [r.get("type") for r in ids] == ["PointAdded", "PointRevised"]
        assert all(b"other" not in line for line in seen), (
            "a line that does not name the needle must never be yielded")

    def test_a_line_straddling_two_windows_is_carried_not_split(self, tmp_path):
        """The tail carry: the needle sits in the SECOND window of ONE line."""
        from tortoise.log import EventLog

        record = {"type": "PointAdded", "point": {"id": "straddler",
                                                    "text": "x" * 200}}
        journal = self._journal(tmp_path, "straddle.jsonl", [record])
        log = EventLog(journal)
        log.MENTION_SCAN_CHUNK = 64          # force the line across windows
        seen = list(log.mention_lines(b"straddler"))
        assert len(seen) == 1, f"one record, one line — saw {len(seen)}"
        assert len(seen[0]) > 64, (
            "the fixture must actually straddle two windows, or this test "
            "proves nothing about the carry")
        assert json.loads(seen[0].decode("utf-8")) == record, (
            "a carried partial line must be yielded ONCE, whole — a split "
            "yield would fold a truncated record and lose the presence "
            "answer for a point the journal does hold")

    def test_a_bare_cr_separated_journal_still_reads(self, tmp_path):
        """``append`` writes ``\\n``; a foreign journal may use bare ``\\r``."""
        from tortoise.log import EventLog

        journal = self._journal(tmp_path, "cr.jsonl", [
            {"type": "PointAdded", "point": {"id": "crsplit"}},
            {"type": "PointAdded", "point": {"id": "unrelated"}},
        ], sep=b"\r")
        seen = list(EventLog(journal).mention_lines(b"crsplit"))
        assert len(seen) == 1, f"saw {seen!r}"
        assert b"unrelated" not in seen[0]

    def test_a_later_scan_resumes_at_a_line_boundary(self, tmp_path):
        """Resuming is what makes the memo O(appended) — and it must not split a line.

        ``resume_offset`` is the end of the last COMPLETE line, so a caller that
        retains presence for ``[0, resume_offset)`` and starts again there reads
        every record exactly once. A record that was MID-WRITE at the first pass
        is the one shape that can go wrong: resuming past it would read its tail
        as a whole line, fold nothing, and lose its presence. So the offset is
        deliberately held before an unterminated tail, and the next pass re-reads
        that record whole.
        """
        from tortoise.log import EventLog

        journal = str(tmp_path / "resume.jsonl")
        log = EventLog(journal)
        # through the PRODUCER: `append` is what terminates a record, and it
        # repairs an unterminated tail before writing
        log.append({"type": "PointAdded", "point": {"id": "one"}})
        log.append({"type": "PointAdded", "point": {"id": "two"}})
        first = log.mention_lines(None)
        assert len(list(first)) == 2
        assert first.resume_offset == os.path.getsize(journal), (
            "a journal ending on a terminator resumes at EOF")

        log.append({"type": "PointAdded", "point": {"id": "three"}})
        resumed = log.mention_lines(None, start=first.resume_offset)
        lines = [json.loads(line.decode("utf-8")) for line in resumed]
        assert [r["point"]["id"] for r in lines] == ["three"], (
            f"a resumed scan must yield ONLY the appended records, saw {lines}")

        # ── the torn-tail rule ──
        torn = json.dumps({"type": "PointAdded",
                           "point": {"id": "four"}}).encode("utf-8")
        with open(journal, "ab") as fh:
            fh.write(torn)                      # no terminator yet
        partial = log.mention_lines(None, start=resumed.resume_offset)
        assert len(list(partial)) == 1, "a complete-but-unterminated record is read"
        assert partial.resume_offset < os.path.getsize(journal), (
            "an unterminated tail must NOT be counted as folded: the next pass "
            "has to re-read it whole, or a record could be lost")
        again = log.mention_lines(None, start=partial.resume_offset)
        assert [json.loads(line.decode("utf-8"))["point"]["id"]
                for line in again] == ["four"], (
            "re-reading the tail yields the same record, whole — never two "
            "unparseable fragments")

    def test_a_missing_journal_raises_rather_than_answering(self, tmp_path):
        """The silent early-life path belongs to the CALLER, not to this reader."""
        from tortoise.log import EventLog

        with pytest.raises(FileNotFoundError):
            list(EventLog(str(tmp_path / "never-written.jsonl"))
                 .mention_lines(b"anything"))

    def test_carried_records_reads_the_sidecar_and_creates_nothing(self, tmp_path):
        """The gone-file discriminator (#10543 review round 7, finding 2).

        ``carried_records`` is what separates "a configured path nothing has
        written" from "a journal that carried records and lost its file". The
        evidence is the position sidecar ``append`` writes through, and the read
        must be PURE: answering for a path with no sidecar must not create one
        (the whole point is a diagnostic on a lane that is already refusing a
        write), and the journal file must stay absent too.
        """
        from tortoise.log import EventLog

        fresh = tmp_path / "fresh.jsonl"
        assert EventLog(str(fresh)).carried_records() is False, (
            "a path nothing has written has no evidence and must answer False")
        assert not fresh.exists() and not (tmp_path / "fresh.jsonl.position").exists(), (
            "the probe must be read-only — it may not create the journal or its "
            "sidecar")

        written = EventLog(str(tmp_path / "written.jsonl"))
        written.append({"type": "PointAdded", "point": {"id": "a"}})
        assert written.carried_records() is True, (
            "one appended record must be visible as evidence even after the "
            "journal file itself is gone")
        written.path.unlink()
        assert written.carried_records() is True, (
            "the evidence has to SURVIVE the journal file — that is the whole "
            "shape it exists for")

        (tmp_path / "written.jsonl.position").write_text("{\"v\":1,\"records\":0}")
        assert written.carried_records() is False, (
            "a sidecar that proved ZERO records is not evidence the journal "
            "carried any — an empty early-life journal stays silent")


def test_an_absent_id_is_not_embedded_at_all(tmp_path, monkeypatch):
    """#10495 review P3: the derived-embedding seam runs AFTER the guard read.

    ``update_point(content=…)`` re-encodes the row through ``encode_for_row``,
    which is a real inference — a model round-trip, or a timeout on a wedged
    one. The seam used to sit ABOVE the lifecycle guard (#10119's read), so an
    id that is ABSENT — the typo case that guard exists for — paid for a full
    encode whose vector the early return then DISCARDED: work on a write that
    cannot land. Measured on the pre-fix tree with this same spy:
    ``update_point("typo-4", content=…)`` -> the embedder was called once.

    Discriminating: on the pre-fix tree the first assertion fails with
    ``['a revision that cannot land']``. The POSITIVE CONTROL keeps the spy
    honest — if the content-edit arm stopped deriving a vector at all (a
    regression of #4208), the second half fails instead of passing quietly.
    """
    sdk = _sdk(tmp_path, "absentembed")
    try:
        calls: list[str] = []
        real = embeddings.encode_for_row

        def _spy(content, expected_dim):
            calls.append(content)
            return real(content, expected_dim)

        monkeypatch.setattr(embeddings, "encode_for_row", _spy)

        missing = str(uuid.uuid4())
        assert sdk.update_point(
            missing, content="a revision that cannot land") == {}
        assert calls == [], (
            "an ABSENT id paid for an embedding the early return then threw "
            f"away (#10495 review P3): {calls}")

        # POSITIVE CONTROL: a real point's content edit must still derive.
        pid = _pid(sdk)
        calls.clear()
        sdk.update_point(pid, content="a revision that lands")
        assert calls == ["a revision that lands"], (
            "the derived-embedding arm no longer re-encodes a content edit "
            f"(#4208): {calls}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _GraphAbortThenSlotAbortEmptyGraph:
    """Wipe + graph abort, then a write-slot abort, then pass through.

    Models a rebuild — which is ITSELF a concurrent writer — followed by
    ordinary write-slot contention inside the same retry window. The
    graph-replaced abort must stay LATCHED: if the gate re-reads the LATEST
    abort, the write-slot one disarms the witness and the zero-row re-issue
    reports success again.
    """

    def __init__(self, real, needle: str) -> None:
        self._real = real
        self._needle = needle
        self.matching = 0
        self.matched_rows = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                self._real.query("MATCH (n:Point) DETACH DELETE n")
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            if self.matching == 2:
                raise redis_exc.ResponseError(_WRITE_SLOT)
            res = self._real.query(cypher, params=params, timeout=timeout)
            self.matched_rows += (res.result_set[0][0]
                                  if res.result_set else 0)
            return res
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_later_write_slot_abort_does_not_disarm_the_witness(
        tmp_path, monkeypatch):
    """The graph-abort latch survives a later, state-independent abort.

    A rebuild is a concurrent writer, so a write-slot refusal arriving after a
    graph-replaced one is not exotic. Gating on the LATEST abort let the slot
    abort disarm a witness that had been legitimately armed, and the next
    zero-row re-issue returned success — the false-success this PR closes.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "mixed")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _GraphAbortThenSlotAbortEmptyGraph(
            proj.g, _POINT_PROP_SET_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, confidence=0.9)

        assert proxy.matching >= 3, "the sequence never reached the re-issue"
        assert proxy.matched_rows == 0, "nothing landed, so nothing may report it"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _AbortOnceThenTerminalise:
    """Abort once AND mark the point terminal — a rebuild replaying the record.

    ``retract_point`` appends ``PointRetracted`` BEFORE its CAS, so a rebuild
    that replays that record makes the re-issued CAS match nothing through the
    statement's OWN guard. The zero-row result is then legitimate and must
    keep the documented ``ValueError``, not report a graph replacement.
    """

    def __init__(self, real, needle: str) -> None:
        self._real = real
        self._needle = needle
        self.matching = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                self._real.query("MATCH (n:Point) SET n.status = 'retracted'")
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _AbortOnceThenOutdated:
    """Abort once AND flag the point ``outdated`` — a rebuild replaying an
    ``invalidate_point``.

    ``invalidate_point`` sets the legacy ``outdated`` FLAG and leaves ``status``
    alone, so the point stays ``live`` while ``_terminal_excluded`` excludes it
    from the retract CAS. A re-issued CAS therefore matches nothing and the
    witness has to read terminality off the FLAG — reading only the status would
    report a graph replacement for a point the graph itself calls dead.
    """

    def __init__(self, real, needle: str, victim: str) -> None:
        self._real = real
        self._needle = needle
        self._victim = victim
        self.matching = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                self._real.query(
                    "MATCH (n:Point {id:$id}) SET n.outdated = true",
                    params={"id": self._victim})
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_retract_of_an_OUTDATED_node_still_raises_already_terminal(
        tmp_path, monkeypatch):
    """Terminality is the FLAG as well as the status, on the retried route.

    ``_terminal_excluded`` excludes ``outdated=true`` (the flag
    ``invalidate_point`` sets instead of a status), so an invalidated-but-live
    point's CAS matches nothing on the re-issue. Reading terminality off
    ``status`` alone would take the sentinel path and surface the GRAPH abort
    after the full 7-14 s budget — naming a replaced graph for a point the
    caller can see is dead, which is the false diagnosis this PR removes.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "outdatedrace")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceThenOutdated(proj.g, _RETRACT_CAS_NEEDLE, pid)
        proj.g = proxy

        with pytest.raises(ValueError, match="already terminal"):
            sdk.retract_point(pid)

        assert proxy.matching >= 2, "the aborted CAS was never re-issued"
        proj.g = proxy._real
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
            params={"id": pid}).result_set[0]
        assert row[1] is True, "fixture precondition: the flag must be set"
        assert row[0] == "live", (
            "the raise must not be papering over a write, and the FLAG must be "
            "the sole disqualifier — any other status would satisfy the "
            f"status arm instead of the `or outdated` one, saw {row[0]!r}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_retract_that_lost_the_race_still_raises_already_terminal(
        tmp_path, monkeypatch):
    """A legitimately empty re-issue keeps the documented ValueError.

    The graph was never replaced, so reporting one would name a cause that did
    not occur (and `_classify_db_failure` would bucket it as "db").
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "lostrace")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceThenTerminalise(proj.g, _RETRACT_CAS_NEEDLE)
        proj.g = proxy

        with pytest.raises(ValueError, match="already terminal"):
            sdk.retract_point(pid)
        assert proxy.matching >= 2, "the aborted CAS was never re-issued"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _AbortOnceThenSetStatus(_AbortOnceThenTerminalise):
    """Abort once AND set every point's status — the competing writer's win."""

    def __init__(self, real, needle: str, *, status: str = "live") -> None:
        super().__init__(real, needle)
        self._status = status

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                self._real.query(
                    "MATCH (n:Point) SET n.status = $s",
                    params={"s": self._status})
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
        return self._real.query(cypher, params=params, timeout=timeout)


def test_a_promote_refused_by_a_terminal_status_keeps_its_illegal_transition(
        tmp_path, monkeypatch):
    """A node that went TERMINAL in the retry window keeps the ValueError.

    (A node that went LIVE is the #1088 idempotent no-op instead — pinned by
    `test_a_status_only_promote_on_an_already_live_node_is_idempotent`.)
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "promoterace")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnceThenSetStatus(
            proj.g, _POINT_PROMOTE_NEEDLE, status="retracted")
        proj.g = proxy

        with pytest.raises(ValueError, match="Illegal status transition"):
            sdk.update_point(pid, status="live")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_promote_on_an_absent_node_still_surfaces_the_abort(
        tmp_path, monkeypatch):
    """An absent node must NOT become `ValueError('No point')` (#8854 r4 P1).

    ``ValueError`` is predicate-FALSE, so raising it after ONE re-issue would
    abandon the 7-14 s window #7405 exists to heal. Absent → keep retrying;
    exhausted → the graph abort, unchanged in type.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "promoteabsent")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnceThenEmptyGraph(proj.g, _POINT_PROMOTE_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, status="live")
        assert proxy.matching >= 3, (
            "an absent node must keep retrying, not raise after one re-issue")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _EmptyResult:
    """A result that matched nothing without raising (the transient window)."""

    def __init__(self) -> None:
        self.result_set: list = []
        self.rows: list = []


class _AbortThenEmptyThenPass(_AbortOnceThenTerminalise):
    """Abort once, then answer ZERO rows while the node is still LIVE.

    The transient state #8854(b) narrows: the node is absent when the CAS runs
    (a rebuild mid-replay) and restored — live — before the diagnostic read.
    Presence alone is not terminality.
    """

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            if self.matching == 2:
                return _EmptyResult()
        return self._real.query(cypher, params=params, timeout=timeout)


def test_retract_mid_skew_does_not_claim_already_terminal(
        tmp_path, monkeypatch):
    """A LIVE node restored between the CAS and the read is not terminal.

    Pins the terminality guard: a presence-only witness raises
    ``ValueError('already terminal')`` here, for a point that is live.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "skew")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortThenEmptyThenPass(proj.g, _RETRACT_CAS_NEEDLE)
        proj.g = proxy

        out = sdk.retract_point(pid)
        assert out["status"] == "retracted"
        assert proxy.matching >= 3, "the CAS was never re-issued after the skew"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_promote_on_a_draft_node_re_lands_after_a_transient_empty(
        tmp_path, monkeypatch):
    """PRESENCE is not PROMOTABILITY (#8854 review round 5 P1).

    The replay lands the node as ``draft`` between the abort and the re-issue;
    a presence-only witness reports an illegal transition (predicate-FALSE, so
    the window is abandoned after one re-issue). The status read keeps retrying,
    and the third attempt promotes.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "draftskew")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortThenEmptyThenPass(proj.g, _POINT_PROMOTE_NEEDLE)
        proj.g = proxy

        out = sdk.update_point(pid, status="live")

        assert out["status"] == "live"
        assert proxy.matching >= 3, "the promote was not re-issued after the skew"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_status_only_promote_on_an_already_live_node_is_idempotent(
        tmp_path, monkeypatch):
    """The #1088 outcome survives a retry (#8854 review round 5).

    With status-only props, an already-live node is an idempotent success — not
    an illegal transition, and not an abort.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "liveskew")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnceThenSetStatus(
            proj.g, _POINT_PROMOTE_NEEDLE, status="live")
        proj.g = proxy

        out = sdk.update_point(pid, status="live")

        assert out["status"] == "live"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_the_tag_witness_counts_NAMES_not_edges(tmp_path, monkeypatch):
    """The duplicate-id fan-out must not read as a non-landing tag sync.

    ``MATCH (p:Point {id:$id})`` matches every node carrying that id, so the
    edge count exceeds the requested NAME count on the documented duplicate
    shape. Counting DISTINCT tag names keeps the witness a name comparison.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "duptag")
    try:
        pid = _pid(sdk)
        other = _pid(sdk)
        proj = sdk._get_proj()
        # Make the id non-unique — the shape `_mark_dirty` and the per-key
        # witness already account for.
        proj.g.query("MATCH (n:Point {id:$other}) SET n.id = $pid",
                     params={"other": other, "pid": pid})
        proxy = _AbortOnceGraph(proj.g, _TAGGED_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, tags=["dup-tag"])

        assert proxy.applied >= 1, "the tag sync never landed"
        tags = {r[0] for r in proj.g.query(
            "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) RETURN t.name",
            params={"pid": pid}).result_set}
        assert tags == {"dup-tag"}, tags
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _AbortOnceThenEmptyRows:
    """Abort once, then answer the SAME statement with an empty result set.

    ``RETURN count(n)`` is a single-row aggregate, so an empty ``result_set``
    means the statement did not run at all — the premise ``_landed_at_least_one``
    documents. The per-key loop reads that same shape and must treat it as "this
    key did not land", never index it.
    """

    def __init__(self, real, needle: str) -> None:
        self._real = real
        self._needle = needle
        self.matching = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            return _EmptyResult()
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_per_key_attempt_with_no_result_row_is_a_non_landing(
        tmp_path, monkeypatch):
    """An empty per-key result set must not escape as ``IndexError``.

    ``_write_props`` reads ``result_set[0][0]`` for every key. An empty result
    set there means the statement did not run (the same premise
    ``_landed_at_least_one`` guards for). Indexing it blind raises
    ``IndexError``, which ``retryable_aborted_write`` does not recognise — it
    escapes as a STRUCTURAL failure and masks the abort that authorised the
    retry (#8854 review P3). Read as "this key did not land", the witness keeps
    the call loud with its original error type instead.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "emptyrows")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceThenEmptyRows(proj.g, _POINT_PROP_SET_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, confidence=0.42)
        assert proxy.matching >= 2, "the per-key SET was never re-issued"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _GraphThenSlotThenDeletedGraph:
    """Rebuild abort, then a write-slot abort, then the graph key is GONE.

    The pre-issue `EXISTS` probe must stay armed across the state-independent
    abort: otherwise the retry issues a statement into a graph FalkorDB would
    auto-create EMPTY (the #7685 harm).
    """

    def __init__(self, real, needle: str) -> None:
        self._real = real
        self._needle = needle
        self.matching = 0
        self.applied = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            if self.matching == 2:
                raise redis_exc.ResponseError(_WRITE_SLOT)
            self.applied += 1
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 0 if self.matching >= 2 else 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_deleted_graph_is_not_issued_into_after_a_mixed_abort(
        tmp_path, monkeypatch):
    """The pre-issue deletion gate is latched like the witness (#8854 r6 P2).

    A write-slot abort after a rebuild abort must not re-open issuing into a
    graph whose key is gone.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "mixeddeleted")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _GraphThenSlotThenDeletedGraph(
            proj.g, _POINT_PROP_SET_NEEDLE)
        proj.g = proxy

        # The FAMILY matters: after a write-slot abort, a guard that re-raised
        # `last_abort` would tell the caller to "retry the query" against a
        # graph that is gone (#8854 r7 P2). The latched GRAPH abort is raised.
        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, confidence=0.9)

        assert proxy.applied == 0, (
            "a deleted graph must never be issued into, even after a "
            "state-independent abort")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_deleted_graph_abort_on_the_property_set_stays_loud(tmp_path):
    """The retry must not MASK a deletion — the #7685 guarantee, extended.

    With the graph key ABSENT the engine's sentence means a plain deletion, and
    re-issuing would write into a graph FalkorDB auto-created EMPTY and report
    SUCCESS against a graph the caller never wrote to (#6666 class). The abort
    therefore surfaces unchanged, and ``update_point`` raises instead of
    returning a point dict that claims the edit.
    """
    sdk = _sdk(tmp_path, "deleted")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE, present=False)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, confidence=0.9)

        assert proxy.applied == 0, (
            "a deleted graph must not be re-issued into — nothing may be "
            "reported as applied")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 9. the tag sync's LANDING WITNESS (#8854 review round 5, P1) ────────

# The stale-edge DELETE the reconciler issues for a tag the caller dropped.
_TAG_DELETE_NEEDLE = "-[r:TAGGED]->(t:Tag {name:$tag}) DELETE r"


class _AbortThenUnreplay(_AbortOnceGraph):
    """Abort the first matching statement, then delete its anchor Point.

    Models the window the retry exists for, in the form that USED to be
    swallowed: the abort is a rebuild's, and the statement it aborts is the one
    that would write the derived half — so on the re-issue the anchor Point is
    still not there. A ``MERGE`` against a missing anchor matches nothing and
    returns normally, so without a witness the caller is told the tag write
    landed while the graph holds no edge at all.

    ``present=True`` keeps the graph KEY present, which is what authorises the
    retry: the abort is indistinguishable from a plain deletion except by that
    probe.
    """

    def __init__(self, real, needle: str, *, victim: str) -> None:
        super().__init__(real, needle, present=True)
        self._victim = victim
        self.vanished = False

    def query(self, cypher, params=None, timeout=None):
        try:
            return super().query(cypher, params=params, timeout=timeout)
        except redis_exc.ResponseError:
            # The rebuild replaced the graph mid-statement; its replay has not
            # reached this Point yet. One deletion is enough — a second would
            # race the assertions below.
            if not self.vanished:
                self.vanished = True
                self._real.query(
                    "MATCH (p:Point {id:$id}) DETACH DELETE p",
                    params={"id": self._victim})
            raise


def test_a_retried_tag_sync_that_lands_nothing_does_not_report_success(
        tmp_path, monkeypatch):
    """A re-issued tag sync against an unreplayed Point is NOT a success.

    Measured pre-fix: ``update_point(pid, tags=[a, b])`` returned normally
    with ``{}`` while the graph held ZERO ``TAGGED`` edges and no ``Point`` at
    all — the success report the whole retry exists to avoid (#6666 class).
    ``{}`` is the historical missing-id answer, not a point dict: this fixture
    DELETES the Point at the abort, so the re-issue's read-back is empty (the
    same contract `test_a_first_attempt_that_matches_nothing_keeps_the_empty_
    contract` pins). The tag arm rode ``_graph_write_with_retry`` WITHOUT a
    witness, so nothing asked the graph whether the re-issue had landed.

    The abort therefore surfaces unchanged, exactly as it does on the property
    arm — one behaviour for the whole mutation, not one per statement.
    """
    # The witness answers `False` on every re-issue, so this test runs the
    # budget to EXHAUSTION — the path that issues the three real backoff
    # sleeps (`base=2.0`, `cap=8.0` ⇒ 7-14 s of wall clock). Recording rather
    # than sleeping keeps the file cheap and simultaneously proves the
    # exhaustion path was the one exercised.
    sleeps: list[float] = []
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=sleeps.append))
    sdk = _sdk(tmp_path, "tagwitness")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortThenUnreplay(proj.g, _TAGGED_NEEDLE, victim=pid)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, tags=["7405ghost", "7405spectre"])

        assert sleeps, (
            "the retry budget was never exhausted — the witness must refuse "
            "EVERY re-issue, so this test proves nothing if it does not run "
            "out of attempts")
        proj.g = proxy._real
        assert proxy.vanished, (
            "fixture precondition: the Point must be gone — an unresolvable "
            "anchor is the window under test")
        rows = proj.g.query(
            "MATCH (p:Point {id:$id})-[:TAGGED]->(t:Tag) RETURN count(t)",
            params={"id": pid}).result_set
        assert rows[0][0] == 0, (
            "fixture precondition: the re-issue must land NO edge, or the "
            "witness was never the thing that refused the call")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_clearing_tags_is_never_reported_as_a_non_landing(tmp_path):
    """The witness must not turn an EMPTY tag request into a failure.

    ``tags=[]`` asks for no edge, so a graph holding none is exactly what was
    requested — ``landed == 0 == len(wanted)`` and the call returns.

    ⛔ This does NOT pin the FULL-SET rule: an empty request returns at the
    ``not _sync_wanted`` guard, so ``landed == len(wanted)`` is never evaluated
    here (measured: mutating that comparison to ``>= 1`` left this test green).
    ``test_a_half_landed_tag_sync_is_not_a_success`` is what pins it.
    """
    sdk = _sdk(tmp_path, "tagclear")
    try:
        pid = _pid(sdk)
        sdk.update_point(pid, tags=["7405gone"])
        proj = sdk._get_proj()
        # Abort the stale-edge DELETE, so the retry re-issues the whole
        # reconciler with the latch armed — the state in which the witness is
        # actually consulted.
        proxy = _AbortOnceGraph(proj.g, _TAG_DELETE_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, tags=[])  # returns; must NOT raise

        assert proxy.matching >= 2, (
            "the aborted stale DELETE was never re-issued — the witness was "
            "never consulted, so this test would prove nothing")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (p:Point {id:$id})-[:TAGGED]->(t:Tag) RETURN count(t)",
            params={"id": pid}).result_set
        assert rows[0][0] == 0, "the clear must have landed"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _QueryCounter:
    """Record every Cypher string issued through this handle."""

    def __init__(self, real) -> None:
        self._real = real
        self.statements: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.statements.append(cypher)
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_tagged_update_that_never_retries_pays_no_read_back(tmp_path):
    """The tag landing check must be LAZY — inside the witness, not in `fn`.

    The wrapper consults a witness only once a graph-replaced abort has latched.
    A read-back placed inside the reconciler callable is charged to EVERY tagged
    update on the SDK's hottest mutation door, which is a cost this PR would be
    introducing rather than removing (measured: one extra `count(DISTINCT
    t.name)` per update at a head where the witness never ran).
    """
    sdk = _sdk(tmp_path, "lazy")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        counter = _QueryCounter(proj.g)
        proj.g = counter

        sdk.update_point(pid, tags=["7405lazy"])

        read_backs = [s for s in counter.statements
                      if "count(DISTINCT t.name)" in s]
        assert read_backs == [], (
            "the tag read-back ran on a NON-retried update — it belongs inside "
            f"the witness; saw {read_backs}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _AbortPromoteThenEmptyPresence(_AbortOnceGraph):
    """Abort the promote ``SET`` once, then answer the STATUS read with no row.

    ``_promote_witness`` reads ``MATCH (n:Point {id:$id}) RETURN n.status`` — a
    single-row read, so an empty result set means the statement did not run.
    That is the shape this proxy produces once the retry is authorised.
    """

    _PRESENCE = "MATCH (n:Point {id:$id}) RETURN n.status"

    def __init__(self, real, promote_needle: str) -> None:
        super().__init__(real, promote_needle, present=True)
        self.presence_reads = 0

    def query(self, cypher, params=None, timeout=None):
        if self._PRESENCE in cypher:
            # The witness's own read: no row at all (the statement did not run).
            self.presence_reads += 1
            return _EmptyResult()
        if self._needle in cypher:
            # The promote `SET`: abort the first attempt, then answer every
            # re-issue with ZERO rows — the "replay has not restored it yet"
            # shape. `_promote_witness` only consults the presence read when the
            # SET returned no row, so both halves are required to reach it.
            self.matching += 1
            if self.matching == 1:
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            return _EmptyResult()
        return self._real.query(cypher, params=params, timeout=timeout)


def test_a_promote_witness_reads_an_empty_status_row_as_absent(
        tmp_path, monkeypatch):
    """An empty status read is "absent", never an ``IndexError``.

    Indexing ``result_set[0][0]`` blind raises ``IndexError``, which
    ``retryable_aborted_write`` does not recognise — so it escapes as a
    STRUCTURAL failure and MASKS the abort that authorised the retry, replacing
    a caller-actionable graph error with an internal one.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "promoteempty")
    try:
        # The promote arm engages only on a DRAFT point (`WHERE n.status = 'draft'`),
        # so a live `_pid` never issues the statement under test.
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortPromoteThenEmptyPresence(proj.g, _POINT_PROMOTE_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, status="live")

        assert proxy.presence_reads >= 1, (
            "the witness was never consulted — this test would prove nothing")
        assert proxy.matching >= 2, "the promote SET was never re-issued"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_non_string_tag_list_is_still_witnessed(tmp_path, monkeypatch):
    """A HASHABLE non-string tag list must not disable the landing check.

    ``tags=[1, 2]`` is a legal write — the engine creates both ``:Tag`` nodes
    and raises nothing — so treating "not all str" as unwitnessed re-opens the
    false success this arm closes: under an abort that wiped the Point, the
    reconciler's MERGEs match nothing and the call returned a success dict with
    no read-back at all. The witness is disabled only for entries it cannot
    dedupe (UNHASHABLE ones), where the engine owns the error.
    """
    # Same exhaustion shape as the string-tag sibling above: the witness stays
    # `False` for every re-issue, so the three backoff sleeps would be real
    # wall clock (7-14 s) unless recorded instead of slept.
    sleeps: list[float] = []
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=sleeps.append))
    sdk = _sdk(tmp_path, "nonstrtags")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortThenUnreplay(proj.g, _TAGGED_NEEDLE, victim=pid)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, tags=[1, 2])

        assert sleeps, (
            "the retry budget was never exhausted — the witness must refuse "
            "every re-issue even for a non-string tag list")
        assert proxy.vanished, "fixture precondition: the Point must be gone"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _PromoteLosesTheRace:
    """Flip the node LIVE between the door's probe and its guarded ``SET``.

    The pre-write short-circuit answers an already-live point as an idempotent
    no-op, so the ``live`` status is only reachable AT the guarded statement when
    the replay lands it in between — the window this pins.
    """

    def __init__(self, real, needle: str, victim: str) -> None:
        self._real = real
        self._needle = needle
        self._victim = victim
        self.flipped = False

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher and not self.flipped:
            self.flipped = True
            self._real.query(
                "MATCH (n:Point {id:$id}) SET n.status = 'live'",
                params={"id": self._victim})
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_live_node_with_unwritten_props_is_not_a_silent_success(tmp_path):
    """A promote that matched nothing is NOT an idempotent no-op.

    ``status_only`` is what separates the two: the replay can land the node
    ``live`` between the door's probe and the guarded ``SET`` (this proxy does),
    and returning silently then would report a landed write that never happened
    for a call that asked for more than the status. The invariant this pins:
    the promote matched NOTHING, so the ``confidence`` the caller asked for is
    not on the node, and the door must not answer as though it were.
    """
    sdk = _sdk(tmp_path, "promoterace")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _PromoteLosesTheRace(proj.g, _POINT_PROMOTE_NEEDLE, pid)
        proj.g = proxy

        with pytest.raises(ValueError, match="already live"):
            sdk.update_point(pid, status="live", confidence=0.42)

        assert proxy.flipped, "fixture precondition: the node must have gone live"
        proj.g = proxy._real
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status, n.confidence",
            params={"id": pid}).result_set[0]
        assert row[0] == "live"
        assert row[1] is None, (
            "the raise must not be papering over a partly-applied write: the "
            f"confidence was never set, saw {row[1]!r}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_status_only_promote_on_a_live_node_stays_an_idempotent_noop(
        tmp_path):
    """The other half of ``status_only``: nothing was asked for but the status.

    A live node is exactly what a status-only promote wanted, so this must NOT
    raise — otherwise the ``live`` classification would turn an idempotent retry
    into a spurious error.

    NOT pinned here: that the silent no-op also emits no ``PointRevised``. That
    silence comes from the POST-write early return — the promote statement IS
    issued, matches nothing, and ``update_point`` returns ``get_point(id)``
    before the tail emit — so ``res.result_set`` gates nothing at this head (the
    conjunct was deleted once the early return made it unreachable-false). The
    observable is pinned by
    ``test_a_live_status_only_promote_emits_no_point_revised``.
    """
    sdk = _sdk(tmp_path, "promoteidem")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _PromoteLosesTheRace(proj.g, _POINT_PROMOTE_NEEDLE, pid)
        proj.g = proxy

        out = sdk.update_point(pid, status="live")  # must NOT raise

        assert out["status"] == "live"
        assert proxy.flipped, "fixture precondition: the node must have gone live"
        proj.g = proxy._real
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status, n.confidence",
            params={"id": pid}).result_set[0]
        assert row[0] == "live"
        assert row[1] is None, (
            "a status-only promote writes nothing else, so no other prop may "
            f"appear: saw {row[1]!r}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_live_status_only_promote_writes_through_no_other_slot(
        tmp_path, monkeypatch):
    """The status-only no-op adds NO write after the promote it matched nothing.

    ``_mark_dirty`` reaches TWO wrapped write slots of its own
    (``_advance_ep_version``, a graph-wide ``:EpMeta`` MERGE, and the
    ``ep_dirty`` persist), so falling through to the tail spends both — and
    advances the EP epoch — for a promote that applied nothing a caller can
    observe. The ``#1088`` probe-path no-op reaches the same end state without
    touching either, so the post-write route may not do more work than the
    route that never wrote.

    The promote statement itself is still ISSUED on this path (it is the write
    that discovered the node is already live), which is why the expectation is
    that ONE call and not the empty list the probe path produces.
    """
    sdk = _sdk(tmp_path, "promotedirty")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _PromoteLosesTheRace(proj.g, _POINT_PROMOTE_NEEDLE, pid)
        proj.g = proxy
        seen: list[str] = []
        original = sdk._graph_write_with_retry

        def counting(fn, *, what, witness=None):
            seen.append(what)
            return original(fn, what=what, witness=witness)

        monkeypatch.setattr(sdk, "_graph_write_with_retry", counting)

        out = sdk.update_point(pid, status="live")  # must NOT raise

        assert proxy.flipped, "fixture precondition: the node must have gone live"
        assert seen == ["update_point SET (promote)"], (
            "a status-only promote that matched nothing must not reach "
            f"`_mark_dirty`'s slots: saw {seen}")
        assert out["status"] == "live", (
            "the no-op still answers with the node, like the probe path: "
            f"saw {out.get('status')!r}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _AbortThenLive(_AbortOnceGraph):
    """Abort the first promote, then make the node LIVE.

    The rebuild window with a status flip: the abort authorises a retry, and by
    the time the promote is re-issued the replay has landed the node live, so
    the guarded ``SET`` matches NOTHING and raises nothing.
    """

    def __init__(self, real, needle: str, *, victim: str) -> None:
        super().__init__(real, needle, present=True)
        self._victim = victim
        self.aborted = False

    def query(self, cypher, params=None, timeout=None):
        try:
            return super().query(cypher, params=params, timeout=timeout)
        except redis_exc.ResponseError:
            if not self.aborted:
                self.aborted = True
                self._real.query(
                    "MATCH (n:Point {id:$id}) SET n.status = 'live'",
                    params={"id": self._victim})
            raise


def test_a_retried_live_promote_with_other_props_raises_the_documented_refusal(
        tmp_path, monkeypatch):
    """A retry must not REPLACE the refusal with an inactionable abort.

    The first-attempt route raises ``ValueError`` "already live — ... re-issue
    without `status`". The re-issued route reaches the same end state and must
    answer the SAME way: answering `_WriteNotLanded` instead exhausts the
    budget and surfaces the GRAPH abort — 7-14 s of retries, then "graph was
    deleted or replaced", which sends the caller to retry a promote that can
    never match while the node is live and never names the remedy.

    The sleep count is the discriminator: one backoff means the witness raised
    on the first re-issue, three means the budget was run out.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=sleeps.append))
    sdk = _sdk(tmp_path, "promoteretrylive")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortThenLive(proj.g, _POINT_PROMOTE_NEEDLE, victim=pid)
        proj.g = proxy

        with pytest.raises(ValueError, match="already live"):
            sdk.update_point(pid, status="live", confidence=0.42)

        assert proxy.aborted, (
            "fixture precondition: the promote must have aborted once, or the "
            "witness was never armed and this test proves nothing")
        assert len(sleeps) == 1, (
            "the refusal must surface on the FIRST re-issue — one backoff, "
            f"not a run-out budget: saw {len(sleeps)} sleeps")
        proj.g = proxy._real
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status, n.confidence",
            params={"id": pid}).result_set[0]
        assert row[0] == "live"
        assert row[1] is None, (
            "the refusal must not paper over a partly-applied write: the "
            f"confidence was never set, saw {row[1]!r}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_live_status_only_promote_emits_no_point_revised(
        tmp_path, monkeypatch):
    """A no-op promote journals nothing: nothing applied ⇒ no `PointRevised`.

    The MECHANISM is the early return for a status-only promote on a live node.
    This fixture flips the node live AT the guarded ``SET``, so the statement IS
    issued, matches nothing, and the POST-write early return leaves before the
    tail emit; the PRE-write probe route (a point already live at entry) leaves
    even earlier, returning on its status read at the top of the door, before
    the ``:Object`` label probe — no PROMOTE WRITE and no label/validity read is
    issued, only ``RETURN n.status`` plus ``get_point``. An earlier
    ``res.result_set`` conjunct on that emit became unreachable-false once those
    early returns
    existed; this test pins the OBSERVABLE, so it holds whichever route produces
    the silence.

    The store half of `PointRevised` is the change-feed switch, which is read
    from the environment AT CALL TIME — so the emit IS observable in this
    fixture, and the positive control below is what makes the empty assertion
    mean something (a landed promote must emit one).

    Why it matters: the probe-path no-op for the same end state writes nothing
    and emits nothing, so emitting here would journal a revision the graph never
    made — and `poll_events` returns the payload verbatim, so a client would see
    it.
    """
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")

    def revised(sdk) -> list:
        return [e for e in sdk.poll_events()["events"]
                if e["type"] == "PointRevised"]

    # POSITIVE CONTROL: an ordinary promote that lands DOES emit.
    sdk = _sdk(tmp_path, "emitctl")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        sdk.update_point(pid, status="live")
        assert len(revised(sdk)) == 1, (
            "reader precondition: a LANDED promote must emit a PointRevised, "
            "or the assertion below proves nothing")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()

    # THE GATE: the live race applies nothing, so it must emit nothing.
    sdk = _sdk(tmp_path, "emitgate")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _PromoteLosesTheRace(proj.g, _POINT_PROMOTE_NEEDLE, pid)
        proj.g = proxy

        sdk.update_point(pid, status="live")  # must not raise

        proj.g = proxy._real
        assert proxy.flipped, "fixture precondition: the node must have gone live"
        assert revised(sdk) == [], (
            "no write landed, so no revision may be journaled: "
            f"{revised(sdk)}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _PromoteThenEmptyCount:
    """The promote matches nothing AND the diagnostic's presence read is empty.

    Two single-row statements answering with NO ROW: the guarded `SET` (so the
    door falls through to the diagnostic) and the diagnostic's own `count(n)`.
    """

    _COUNT = "MATCH (n:Point {id:$id}) RETURN count(n)"

    def __init__(self, real, promote_needle: str) -> None:
        self._real = real
        self._needle = promote_needle
        self.count_reads = 0

    def query(self, cypher, params=None, timeout=None):
        if self._COUNT in cypher:
            self.count_reads += 1
            return _EmptyResult()
        if self._needle in cypher:
            return _EmptyResult()
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_the_promote_diagnostic_reads_an_empty_count_as_absent(tmp_path):
    """An empty `count(n)` means ABSENT, never an ``IndexError``.

    `count()` is a single-row aggregate, so no row means the statement did not
    run. Indexing it blind raises `IndexError`, which is not a retryable abort:
    it escapes as a STRUCTURAL failure and MASKS the abort that authorised the
    retry — the caller sees an internal error instead of the graph one.
    """
    sdk = _sdk(tmp_path, "diagcount")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _PromoteThenEmptyCount(proj.g, _POINT_PROMOTE_NEEDLE)
        proj.g = proxy

        with pytest.raises(ValueError, match="No point"):
            sdk.update_point(pid, status="live")

        assert proxy.count_reads >= 1, (
            "the diagnostic's presence read was never consulted — this test "
            "would prove nothing")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


_STATUS_READ = "RETURN n.status"


class _VanishAfterTheStatusRead:
    """Delete the node once the promote witness has read its STATUS.

    Models the node disappearing between the witness's read and the diagnostic
    probe: the status read proves the point EXISTS and is TERMINAL, so the
    refusal is real, while a second presence probe would find nothing and could
    only lie ``No point``.
    """

    def __init__(self, real, needle: str, pid: str) -> None:
        self._real = real
        self._needle = needle
        self._pid = pid
        self._aborted = False
        self.vanished = False

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher and not self._aborted:
            self._aborted = True
            self._real.query(
                "MATCH (n:Point {id:$id}) SET n.status = 'retracted'",
                params={"id": self._pid})
            raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
        out = self._real.query(cypher, params=params, timeout=timeout)
        if self._aborted and _STATUS_READ in cypher and not self.vanished:
            self.vanished = True
            self._real.query(
                "MATCH (n:Point {id:$id}) DETACH DELETE n",
                params={"id": self._pid})
        return out

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_the_terminal_diagnostic_does_not_claim_no_point_after_its_own_read(
        tmp_path, monkeypatch):
    """Presence comes from the witness's read, not a second probe (#8854 r5 P3).

    The witness reads STATUS, proves the node exists and that it is terminal, and
    only then raises. Re-probing presence would let a node that vanishes between
    the two reads turn the documented illegal-transition error into
    ``ValueError('No point …')``; and the re-probe's ``count()`` aggregate is
    indexed blind, so a statement that does not run raises ``IndexError`` — a
    STRUCTURAL failure that masks the abort which authorised the retry.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "promotevanish")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _VanishAfterTheStatusRead(proj.g, _POINT_PROMOTE_NEEDLE, pid)
        proj.g = proxy

        with pytest.raises(ValueError, match="Illegal status transition"):
            sdk.update_point(pid, status="live")
        assert proxy.vanished, (
            "the node never vanished after the status read — this test would "
            "prove nothing")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 10. every witnessed site must REFUSE a re-issue that lands nothing ──────
#
# A witness nobody reverts in a test is a witness nothing protects: delete it
# and the suite stays green, while a landed-nothing write starts reporting
# success again. These four arms each had a retry test but no LANDING test.


def _object_point(sdk) -> str:
    """A Point that also carries the `:Object` label (the entity arm)."""
    pid = _pid(sdk)
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
    return pid


def test_the_object_arm_refuses_a_reissue_that_lands_nothing(
        tmp_path, monkeypatch):
    """`SET (Object)` returns `count(n)`; a zero count is a NON-landing."""
    # Stub the backoff clock: these paths EXHAUST the retry budget, so
    # a real sleep would add ~11 s each and make the result clock-dependent.
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "objlanding")
    try:
        pid = _object_point(sdk)
        proj = sdk._get_proj()
        proxy = _AbortThenUnreplay(proj.g, _OBJECT_PROP_SET_NEEDLE, victim=pid)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, confidence=0.31)

        assert proxy.vanished, "fixture precondition: the Point must be gone"
        assert proxy.matching >= 2, (
            "the aborted statement was never re-issued — the witness was never consulted")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_promote_point_cas_that_lands_nothing_is_not_a_not_draft_verdict(
        tmp_path, monkeypatch):
    """A non-landing re-issue must not be reported as the node's STATE.

    ``promote_point``'s CAS is retried, so its empty re-issue is ambiguous: a
    lost race (the node moved on) or a rebuild that has not replayed it (the
    node is absent). Without a witness the `raced` branch reads the absent node
    as ``{'blocked': True, 'reason': 'not_draft'}`` — a state verdict for a
    point nobody observed — and ``VerdictLayer.decide(..., VERDICT_KEEP)``
    consumes ``blocked``
    as a lost race, rolling back a review write that had already landed.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "promotepointlanding")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortThenUnreplay(proj.g, _PROMOTE_POINT_CAS_NEEDLE, victim=pid)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.promote_point(pid)

        assert proxy.vanished, "fixture precondition: the Point must be gone"
        assert proxy.matching >= 2, (
            "the aborted CAS was never re-issued — the witness was never consulted")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _PromotePointCasReturnsEmpty:
    """Abort the CAS once, then answer ZERO rows forever while the node is draft.

    The "the rebuild has not replayed it yet" window, WITHOUT the node being
    absent: a re-issue that matches nothing against a point that is present and
    still promotable must keep retrying (the witness answers False) rather than
    manufacture a state verdict. Only the CAS statements after the first are
    faked; the entry and witness reads stay real.
    """

    def __init__(self, real, needle: str) -> None:
        self._real = real
        self._needle = needle
        self.matching = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            return _EmptyResult()
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _PromotePointVanishesAtTheReRead(_PromotePointCasReturnsEmpty):
    """Abort the CAS once, go LIVE, then vanish between the two outcome reads.

    The witness's own status read answers ``live`` (a real race), but the
    ``raced`` branch's ``get_point`` finds nothing — a second replacement, or a
    concurrent ``delete_point``. That is an ABSENCE, not a state, and must not
    be reported as ``not_draft``.
    """

    def __init__(self, real, needle: str, victim: str) -> None:
        super().__init__(real, needle)
        self._victim = victim
        self.vanished = False

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher and self.matching >= 1:
            self.matching += 1
            self._real.query(
                "MATCH (n:Point {id:$id}) SET n.status = 'live'",
                params={"id": self._victim})
            return _EmptyResult()
        if (self.matching >= 1 and not self.vanished
                and "RETURN properties(n)" in cypher):
            self.vanished = True
            self._real.query(
                "MATCH (p:Point {id:$id}) DETACH DELETE p",
                params={"id": self._victim})
            return _EmptyResult()
        return super().query(cypher, params=params, timeout=timeout)


def test_a_promote_point_reissue_that_matches_nothing_keeps_retrying(
        tmp_path, monkeypatch):
    """A zero-row re-issue against a still-DRAFT point is NOT an answer.

    The CAS matches only the literal ``'draft'``, so a draft node does not
    explain an empty result — the re-issue simply has not been replayed. The
    witness must answer False and let the retry window run out, not turn the
    ambiguity into a ``not_draft`` verdict after one attempt.
    """
    sleeps: list[float] = []
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=sleeps.append))
    sdk = _sdk(tmp_path, "promotepointdraftretry")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _PromotePointCasReturnsEmpty(proj.g, _PROMOTE_POINT_CAS_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.promote_point(pid)

        assert proxy.matching >= 3, (
            "a draft node does not explain a zero row — the CAS must be "
            f"re-issued until the budget is spent, saw {proxy.matching} issue(s)")
        assert len(sleeps) >= 2, f"the retry window was not spent: {sleeps}"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_promote_point_that_races_a_concurrent_promote_reports_already_live(
        tmp_path, monkeypatch):
    """The TRUE arm of the witness: a concurrent promote is a real answer.

    The abort authorises a retry, and by the re-issue the node is ``live`` — a
    status that DOES explain the zero row. Neutralising the witness's True arm
    turns this documented ``already_live`` no-op into a graph-abort after the
    full 7-14 s budget (the false diagnosis this PR removes).
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "promotepointrace")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortThenLive(proj.g, _PROMOTE_POINT_CAS_NEEDLE, victim=pid)
        proj.g = proxy

        out = sdk.promote_point(pid)

        assert proxy.aborted, "fixture precondition: the CAS must have aborted"
        assert out["reason"] == "already_live", (
            "a concurrent promote is classified from the node, not reported as "
            f"a graph replacement: saw {out!r}")
        assert out["blocked"] is False
        assert out["promoted"] is False
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _PromotePointCasSetsTheStatus(_PromotePointCasReturnsEmpty):
    """Abort the CAS once, then move the node's status before the re-issue.

    The re-issued CAS matches nothing because the node is no longer ``draft`` —
    a state that DOES explain the zero row, so the witness answers True and the
    ``raced`` branch reports the real status. Answering False instead (a
    non-``draft`` but also non-``live`` status treated as "keep retrying") spends
    the whole window and then raises "graph was deleted or replaced" for a node
    that is present and unpromotable.

    ``new_status=None`` STRIPS the property: `null = 'draft'` is null, not true,
    so a statusless node cannot match the CAS either. This is the ONLY status
    class where the pre-fix `status is not None and status != 'draft'` and the
    shipped `status != 'draft'` disagree, which is exactly why it needs a test.
    """

    def __init__(self, real, needle: str, victim: str, new_status) -> None:
        super().__init__(real, needle)
        self._victim = victim
        self._new_status = new_status
        self.moved = False

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher and self.matching >= 1:
            self.matching += 1
            if not self.moved:
                self.moved = True
                if self._new_status is None:
                    self._real.query(
                        "MATCH (n:Point {id:$id}) REMOVE n.status",
                        params={"id": self._victim})
                else:
                    self._real.query(
                        "MATCH (n:Point {id:$id}) SET n.status = $s",
                        params={"id": self._victim, "s": self._new_status})
            return _EmptyResult()
        return super().query(cypher, params=params, timeout=timeout)


@pytest.mark.parametrize("new_status", [None, "retracted"])
def test_a_promote_point_that_refuses_the_cas_is_not_reported_as_a_graph_abort(
        tmp_path, monkeypatch, new_status):
    """Every status that EXPLAINS the zero row must NOT spend the retry budget.

    The CAS matches only the literal ``'draft'``, so the classes that explain a
    zero row are exactly `not draft` — a concurrent promote, a concurrent
    terminal transition, or a statusless legacy node (`null = 'draft'` is null,
    not true). Read as "keep retrying" they exhaust the 7-14 s window and then
    raise "graph was deleted or replaced" for a node that is present and
    simply unpromotable; read correctly they are answered at once — the
    concurrent promote as `already_live`, the other two as `not_draft` (this
    test's two parametrizations; the `already_live` arm is pinned by
    ``test_a_promote_point_that_races_a_concurrent_promote_reports_already_live``).
    """
    sleeps: list[float] = []
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=sleeps.append))
    sdk = _sdk(tmp_path, f"promotepointrefuse{new_status}")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _PromotePointCasSetsTheStatus(
            proj.g, _PROMOTE_POINT_CAS_NEEDLE, pid, new_status)
        proj.g = proxy

        out = sdk.promote_point(pid)  # must NOT raise

        assert proxy.moved, "fixture precondition: the status must have moved"
        assert out["blocked"] is True
        assert out["reason"] == "not_draft", out
        assert out["status"] == new_status, out
        assert proxy.matching == 2, (
            "the zero row is EXPLAINED by the node's state — it must not be "
            f"re-issued, saw {proxy.matching} issue(s)")
        assert len(sleeps) == 1, (
            f"only the retry the abort authorised may be paid, saw {sleeps}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_promote_point_whose_node_vanishes_at_the_re_read_is_not_not_draft(
        tmp_path, monkeypatch):
    """The outcome read's ABSENCE is not a state verdict either.

    The witness can answer True from its own status read and the ``raced``
    branch's ``get_point`` can still find nothing — a second replacement or a
    concurrent ``delete_point``. ``get_point`` returns ``{}``, whose missing
    status used to be reported as ``not_draft``: a state for a point that no
    longer exists, and a ``blocked`` refusal that consumers treat as a lost CAS
    race. Absence must answer like absence does at the door's entry.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "promotepointvanish")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _PromotePointVanishesAtTheReRead(
            proj.g, _PROMOTE_POINT_CAS_NEEDLE, pid)
        proj.g = proxy

        with pytest.raises(ValueError, match="No point"):
            sdk.promote_point(pid)

        assert proxy.vanished, "fixture precondition: the node must have vanished"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_the_object_promote_arm_refuses_a_reissue_that_lands_nothing(
        tmp_path, monkeypatch):
    """A promote whose re-issue matched nothing is classified by STATUS.

    An empty row set is NOT automatically a non-landing on this arm: the witness
    reads the node's status, and a status-only promote on a node the replay landed
    LIVE is the #1088 idempotent no-op — a success, pinned on THIS arm by
    `test_a_live_status_only_OBJECT_promote_writes_through_no_other_slot`. What
    must stay loud is the node that is ABSENT, which is what this fixture builds:
    the rebuild has not replayed it, so the re-issue cannot apply and the graph
    abort surfaces.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "objpromotelanding")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
        proj = sdk._get_proj()
        proxy = _AbortThenUnreplay(proj.g, _OBJECT_PROMOTE_NEEDLE, victim=pid)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, status="live")

        assert proxy.vanished, "fixture precondition: the Point must be gone"
        assert proxy.matching >= 2, (
            "the aborted statement was never re-issued — the witness was never consulted")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_live_status_only_OBJECT_promote_writes_through_no_other_slot(
        tmp_path, monkeypatch):
    """The ``:Object`` arm's status-only no-op reaches no write after the CAS.

    The ``:Object`` promote is a SEPARATE statement from the ``:Point`` one
    (it carries the version bump), so its post-write early return is a separate
    branch and the ``:Point`` twin does not cover it. Without that return the
    no-op falls through to the tail, where ``_mark_dirty`` spends BOTH of its
    wrapped slots and advances the graph-wide EP epoch — persisting ``ep_dirty``
    and journaling revisions — for a promote that applied nothing.
    """
    sdk = _sdk(tmp_path, "objpromotedirty")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
        proxy = _PromoteLosesTheRace(proj.g, _OBJECT_PROMOTE_NEEDLE, pid)
        proj.g = proxy
        seen: list[str] = []
        original = sdk._graph_write_with_retry

        def counting(fn, *, what, witness=None):
            seen.append(what)
            return original(fn, what=what, witness=witness)

        monkeypatch.setattr(sdk, "_graph_write_with_retry", counting)

        out = sdk.update_point(pid, status="live")  # must NOT raise

        assert proxy.flipped, "fixture precondition: the node must have gone live"
        assert seen == ["update_point SET (Object promote)"], (
            "a status-only :Object promote that matched nothing must not reach "
            f"`_mark_dirty`'s slots: saw {seen}")
        assert out["status"] == "live", (
            "the no-op still answers with the node, like the probe path: "
            f"saw {out.get('status')!r}")

        # The POSITIVE control for the SAME branch boundary: a LANDED
        # status-only :Object promote must NOT take the early return, or the
        # tail — and with it the #1240 / DE2E-4 dirty-root re-entry that keeps
        # a promoted claim in dreaming — is skipped for every real promote.
        proj.g = proxy._real
        pid2 = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj.g.query("MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid2})
        seen.clear()
        sdk._dirty_roots.clear()

        landed = sdk.update_point(pid2, status="live")

        assert landed["status"] == "live"
        assert seen == [
            "update_point SET (Object promote)",
            "_advance_ep_version MERGE(:EpMeta)",
            "_mark_dirty ep_dirty persist SET",
        ], (
            "a LANDED status-only :Object promote must run `_mark_dirty` — "
            f"otherwise the branch boundary is measured from one side only: {seen}")
        assert pid2 in sdk._dirty_roots, (
            "the wrapper labels are not the observable: the point itself must "
            "be a dirty root, or the promoted claim never re-enters dreaming "
            f"(#1240/DE2E-4): saw {sorted(sdk._dirty_roots)}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_the_derived_embedding_arm_refuses_a_reissue_that_lands_nothing(
        tmp_path, monkeypatch):
    """The clear+set arm returns `count(n)`; a ZERO count is a NON-landing.

    Not "an empty row set": `count()` always yields its single row, so only the
    VALUE can testify here — reading presence-of-row would call every re-issue a
    landing and re-open the defect this test closes.
    """
    # Stub the backoff clock: these paths EXHAUST the retry budget, so
    # a real sleep would add ~11 s each and make the result clock-dependent.
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "embedlanding")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortThenUnreplay(proj.g, _EMBEDDING_NEEDLE, victim=pid)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, content=f"revised {uuid.uuid4().hex}")

        assert proxy.vanished, "fixture precondition: the Point must be gone"
        assert proxy.matching >= 2, (
            "the aborted statement was never re-issued — the witness was never consulted")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_the_corrects_edge_refuses_a_reissue_that_lands_nothing(
        tmp_path, monkeypatch):
    """`MERGE (a)-[:CORRECTS]->(b) RETURN count(*)` needs BOTH anchors.

    A lost `a` makes the match empty, so `count(*)` is ZERO — the non-landing,
    in an always-present row.
    """
    # Stub the backoff clock: these paths EXHAUST the retry budget, so
    # a real sleep would add ~11 s each and make the result clock-dependent.
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "correctslanding")
    try:
        old = _pid(sdk)
        new = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortThenUnreplay(proj.g, _INVALIDATE_CORRECTS_NEEDLE, victim=old)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.invalidate_point(old, new)

        assert proxy.vanished, "fixture precondition: the old Point must be gone"
        assert proxy.matching >= 2, (
            "the aborted statement was never re-issued — the witness was never consulted")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_duplicate_tag_names_do_not_read_as_a_half_landed_sync(
        tmp_path, monkeypatch):
    """`tags=["a", "a"]` must not be witnessed against the RAW list length.

    The engine stores ONE `:Tag` per name, so the landed DISTINCT-name count is
    1 while an undeduped request asks for 2 — the FULL-SET rule then refuses a
    sync that landed, the sentinel retries it, and the caller gets the stale
    graph abort for a write that succeeded. Measured on a mutant without the
    dedupe: `update_point(pid, tags=["dup", "dup"])` under one landing
    graph-replaced abort raised `ResponseError('graph was deleted or replaced
    …')` with the TAGGED edge present.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "tagdupe")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _TAGGED_NEEDLE, present=True)
        proj.g = proxy

        sdk.update_point(pid, tags=["7405dup", "7405dup"])  # must NOT raise

        assert proxy.applied >= 1, "the retried tag sync never landed"
        proj.g = proxy._real
        names = [r[0] for r in proj.g.query(
            "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) RETURN t.name",
            params={"pid": pid}).result_set]
        assert names == ["7405dup"], names
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_an_unhashable_tag_value_keeps_the_engine_error_family(tmp_path):
    """A non-hashable tag must fail with the ENGINE's error, not a `TypeError`.

    `tags=[{"name": "a"}]` is outside the public signature, so the engine is the
    one that refuses it (`Property values can only be of primitive types …`,
    the "db" family). Deduping the request for the witness must not raise
    `TypeError: unhashable type` first: `_classify_db_failure` buckets that as
    "structural", so the guard changes which error family the caller sees.
    """
    sdk = _sdk(tmp_path, "tagunhashable")
    try:
        pid = _pid(sdk)
        with pytest.raises(redis_exc.ResponseError):
            sdk.update_point(pid, tags=[{"name": "a"}])
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _AbortThenRefuseTagMerge:
    """Abort the tag MERGE once, then answer every tag MERGE with ZERO rows.

    Models the rebuild window from the PROMOTE side: the graph key is present
    (so the retry is authorised) and the promote statement has already applied,
    while the tag arm can never land because its MERGE matches an anchor the
    replay has not provided. The order matters — this is the only window in
    which a landed promote used to be left unjournaled.
    """

    def __init__(self, real, needle: str) -> None:
        self._real = real
        self._needle = needle
        self.matching = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            if self.matching == 1:
                raise redis_exc.ResponseError(_DELETED_OR_REPLACED)
            return _EmptyResult()
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.mark.parametrize("arm", ["point", "object"])
def test_a_landed_promote_is_journaled_before_a_later_arm_can_abort(
        tmp_path, monkeypatch, arm):
    """A promote that LANDED must be recorded even when the tag arm aborts.

    `update_point` carries the promote TWICE — a `:Point:Object` arm and a plain
    `:Point` arm — so both are covered here: a pin on one arm does not red the
    other, and deleting the `:Point:Object` arm's `_record_promote()` call left
    the suite green before this parametrization.

    `#8830` exempts the promote arm from the outbox ordering — its guarded `SET`
    can legitimately match nothing, so the record may not precede the write. It
    must not follow the TAG sync either: that arm is witnessed, so an
    unlandable tag merge aborts the whole call, and a record appended at the tail
    was never reached. Measured on `main`: the node ended `status='live'`, the
    caller was told the call failed, and ZERO `PointRevised` was appended — a
    promotion that APPLIED with no record of it at all.

    No other test combines `status` with `tags`; this is the union that exposed
    the window, and the assertion is on ONE emit so a double record also reds.

    ⚠️ WHAT THIS PIN PROVES, AND WHAT IT DOES NOT. It proves the APPEND: exactly
    one `PointRevised`, and (asserted below) a payload that CARRIES the
    promotion (`status='live'` a payload without it would close nothing — the
    record lane applies `status` from this payload, `PointRevised` being a flat
    `NodePatch` onto `point.status`). It does NOT prove replay parity: the GRAPH
    fold (`projection._revise_point`) has no `status` clause, so a `rebuild_all`
    over this journal still materialises the point as `draft` — the pre-existing
    gap declared in `docs/plans/2026-10-17-7951-update-point-outbox.md` §6.5 and
    filed as #9255. Do not restate this test as "the promotion survives a
    rebuild"; when that fold lands, extend THIS test with
    `rebuild_all(..., confirm_destructive=True)` instead of reading the count as
    parity.
    """
    monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))

    def revised(sdk) -> list:
        return [e for e in sdk.poll_events()["events"]
                if e["type"] == "PointRevised"]

    sdk = _sdk(tmp_path, "promotejournal")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        if arm == "object":
            # The `:Point:Object` arm of the promote is reached only by a node
            # carrying both labels; no production path mints one, so the shape
            # is built by hand (same approach as test_3311).
            proj.g.query("MATCH (n:Point {id:$id}) SET n:Object",
                         params={"id": pid})
            labels = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN labels(n)",
                params={"id": pid}).result_set
            assert labels and set(labels[0][0]) == {"Point", "Object"}, (
                "fixture precondition: the object arm needs BOTH labels — "
                f"node labels {labels!r}")
        before = len(revised(sdk))
        proxy = _AbortThenRefuseTagMerge(proj.g, _TAGGED_NEEDLE)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, status="live", tags=["7405late"])

        assert proxy.matching >= 2, (
            "the tag arm was never re-issued — the witness was never consulted, "
            "so this test would prove nothing")
        proj.g = proxy._real
        state = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status",
            params={"id": pid}).result_set
        assert state and state[0][0] == "live", (
            "fixture precondition: the PROMOTE must have landed before the tag "
            f"arm aborted — node state {state!r}")
        emitted = len(revised(sdk)) - before
        assert emitted == 1, (
            "a landed promote whose later arm aborted must be journaled exactly "
            f"once — appends observed: {emitted}")
        record = revised(sdk)[-1]
        assert (record.get("payload") or {}).get("status") == "live", (
            "the record of a LANDED promote must CARRY the promotion: the "
            "record lane applies `status` from this payload (`PointRevised` is "
            "a flat NodePatch onto `point.status`), so a record without it "
            f"would close nothing — record: {record!r}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── #10473 — the target VANISHES between the guard read and the write ──────
#
# The #10119 arm (an id ABSENT at the guard read) is a different defect, and it
# is NOT on this tree: the fix for it lives in PR #10470, where the door answers
# at ENTRY from the guard's own read — a re-read is the right tool there because
# the caller's id is a typo. This arm cannot be answered that way.
# a second read would only move the window — and the statement that would mutate
# is the only thing that can answer "did I mutate", because in it the presence
# check and the mutation are ONE operation.
#
# The interleaving below is FORCED, not timed: the proxy removes the node on the
# first matching WRITE statement and then issues that statement for real, so the
# guard read has already returned "present" when the vanish happens. Nothing here
# depends on scheduling, and the assertions are on the OBSERVABLE EFFECTS (the
# `:EpMeta` epoch, `_dirty_roots`, orphan `:Tag` nodes) — never on a message.

_VANISH_ARMS = (
    (_POINT_PROP_SET_NEEDLE, False),    # the :Point per-key SET
    (_OBJECT_PROP_SET_NEEDLE, True),    # the :Point:Object single SET
)


class _VanishOnFirstWrite:
    """Delete the target on the first matching write, then issue it for real.

    ``rows_matched`` is the sum of the ``RETURN count(n)`` values the real
    statements answered with, so "the conditional write proved the target gone"
    is MEASURED rather than assumed — it is the premise every assertion in this
    section rests on (a proxy that let the write land would prove nothing).
    """

    def __init__(self, real, needle: str) -> None:
        self._real = real
        self._needle = needle
        self.matching = 0
        self.rows_matched = 0
        self.tag_statements = 0
        self.vanished: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        if "MERGE (t:Tag" in cypher:
            self.tag_statements += 1
        if self._needle in cypher:
            self.matching += 1
            pid = (params or {}).get("id")
            if pid is not None and not self.vanished:
                # THE RACE: present at the guard read, deleted before the write.
                self._real.query(
                    "MATCH (n:Point {id:$id}) DETACH DELETE n",
                    params={"id": pid})
                self.vanished.append(pid)
            res = self._real.query(cypher, params=params, timeout=timeout)
            rows = res.result_set[0][0] if res.result_set else 0
            with contextlib.suppress(TypeError, ValueError):
                self.rows_matched += int(rows or 0)
            return res
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            return 1
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _ep_version(sdk) -> int:
    rows = sdk._get_proj().g.query(
        "MATCH (m:EpMeta) RETURN m.ep_version").result_set
    return int(rows[0][0]) if rows else 0


def _sdk_journaled(tmp_path, tag: str):
    """A real SDK on the configured lane, with the JSONL journal wired."""
    uri = os.environ.get("TORTOISE_DB_URI")
    journal = str(tmp_path / f"{tag}.jsonl")
    sdk = TortoiseSDK(
        None if uri else str(tmp_path / f"{tag}.db"),
        namespace=f"test_7405_up_{tag}_{uuid.uuid4().hex[:8]}",
        event_log_path=journal,
    )
    return sdk, journal


def _journalled(journal: str, *, type_: str, pid: str) -> list[dict]:
    """The records of ``type_`` for ``pid`` in the JSONL journal."""
    out: list[dict] = []
    try:
        with open(journal, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == type_ and ev.get("id") == pid:
                    out.append(ev)
    except FileNotFoundError:
        return []
    return out


@pytest.mark.parametrize("needle,as_object", _VANISH_ARMS)
def test_a_target_that_vanishes_at_the_write_strands_no_dirty_root(
        tmp_path, needle, as_object):
    """A zero-row CONDITIONAL write must take no EP side effects (#10473).

    Measured on the pre-fix tree with this exact interleaving: the reply was
    ``{}`` (correct), the journal row was appended (correct — see the sibling
    test), and then the door fell through to ``_mark_dirty``, which advanced
    ``:EpMeta.ep_version`` and classified the vanished id as a delete zombie,
    KEEPING it in ``_dirty_roots``. No *sweep* can converge it (the persist SET
    matches no node, and ``_prune_nonexistent_dirty_roots`` runs only inside a
    dream), so ``_auto_dream_mode`` stays 'local' until a dream pass runs —
    while the point it names does not exist — the #2422/#7505/#9269 pinning harm.

    The write statements are conditional by construction
    (``MATCH … SET … RETURN count(n)``), so the fix consumes their answer
    instead of discarding it on the non-abort path.
    """
    sdk = _sdk(tmp_path, f"vanishroot{as_object}")
    try:
        pid = sdk.create_point("evidence", f"claim {uuid.uuid4().hex}",
                               dedup=False, status="live")["id"]
        proj = sdk._get_proj()
        if as_object:
            proj.g.query("MATCH (n:Point {id:$id}) SET n:Object",
                         params={"id": pid})
        # Model a converged sweep: the create's own root is not the subject.
        sdk._dirty_roots.clear()
        before_ep = _ep_version(sdk)
        before_roots = set(sdk._dirty_roots)

        proxy = _VanishOnFirstWrite(proj.g, needle)
        proj.g = proxy
        reply = sdk.update_point(pid, confidence=0.5)
        proj.g = proxy._real

        assert proxy.vanished == [pid], (
            "fixture precondition: the target must have been deleted by the "
            f"proxy — vanished {proxy.vanished!r}")
        assert proxy.rows_matched == 0, (
            "fixture precondition: the conditional write must have matched "
            f"NOTHING — rows matched {proxy.rows_matched}")
        assert not proj.g.query("MATCH (n:Point {id:$id}) RETURN count(n)",
                                params={"id": pid}).result_set[0][0], (
            "fixture precondition: the node must be gone")

        # The reply is the door's documented absent-id contract (#10079: with
        # no end receipt — the shaped-but-unstored window cannot report an end).
        assert reply == {}, f"a vanished target answers the absent contract, saw {reply!r}"

        assert _ep_version(sdk) == before_ep, (
            "the vanished shape advanced the graph-wide EP epoch: "
            f"{before_ep} -> {_ep_version(sdk)} — the epoch's ordering stamp is "
            "spent on a target that cannot carry the dirty flag it stamps")
        assert pid not in sdk._dirty_roots, (
            "the vanished id was retained as a dirty root, where no sweep can "
            f"converge it — roots {sorted(sdk._dirty_roots)} (before "
            f"{sorted(before_roots)})")
        # The PIN itself, not a restatement of the root set: this fixture holds
        # one point and no operators, so `_auto_dream_mode` answers 'full' —
        # unless a write-context root (the stranded vanished id) holds it in the
        # local window. Measured pre-fix: 'local'.
        assert sdk._auto_dream_mode() == "full", (
            "a vanished target alone must not hold the instance in the local "
            f"dream window — mode {sdk._auto_dream_mode()!r}, roots "
            f"{sorted(sdk._dirty_roots)}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_the_vanish_shape_keeps_the_JOURNAL_record_that_carries_the_revision(
        tmp_path):
    """The fix must NOT suppress the append: it is the rebuild-window record.

    The SAME zero-row shape covers a target DELETED concurrently and a target
    NOT YET REPLAYED mid-rebuild (#7405). In the second, this ``PointRevised``
    is the only copy of the caller's revision — the replay applies it as soon as
    the node is back. A "conditional append" would trade a stranded EP root for
    a LOST revision, so the append is journal-first by decision (§17.1) and is
    untouched here. This is the over-reject control for the fix above.
    """
    sdk, journal = _sdk_journaled(tmp_path, "vanishjournal")
    try:
        pid = sdk.create_point("evidence", f"claim {uuid.uuid4().hex}",
                               dedup=False, status="live")["id"]
        proj = sdk._get_proj()
        # Model a converged sweep: the CREATE's own root is not the subject, so
        # what remains in `_dirty_roots` below is this call's doing alone.
        sdk._dirty_roots.clear()
        before_ep = _ep_version(sdk)
        proxy = _VanishOnFirstWrite(proj.g, _POINT_PROP_SET_NEEDLE)
        proj.g = proxy

        reply = sdk.update_point(pid, confidence=0.5)  # must NOT raise
        proj.g = proxy._real

        assert reply == {}
        assert proxy.rows_matched == 0, "fixture precondition"
        records = _journalled(journal, type_="PointRevised", pid=pid)
        assert len(records) == 1, (
            "the vanished target's revision must STILL be journaled — it is the "
            "record a rebuild replays onto the node when it comes back "
            f"(#7405): saw {len(records)} record(s)")
        assert records[0].get("confidence") == 0.5, (
            "the record must carry the caller's revision — a rebuild applies "
            f"what the payload says: saw {records[0]!r}")
        assert pid not in sdk._dirty_roots, (
            "the journal record must not come at the price of a stranded root: "
            f"{sorted(sdk._dirty_roots)}")
        assert _ep_version(sdk) == before_ep, (
            "the same call advanced the EP epoch for a target that cannot carry "
            f"the stamp: {before_ep} -> {_ep_version(sdk)}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_vanished_tagged_target_issues_no_tag_sync(tmp_path):
    """The tag sync is the door's OTHER post-write statement group (#10473).

    MEASURED, and the reason this asserts on STATEMENTS rather than on orphan
    ``:Tag`` nodes: ``_sync_tags`` issues ``MATCH (p:Point {id:$pid}) MERGE
    (t:Tag {name:$tag}) MERGE (p)-[:TAGGED]->(t)`` — the MERGE is ANCHORED on
    the Point match, so a vanished target creates no ``:Tag`` (verified on the
    pre-fix tree: the orphan count is 0 either way). What the vanish shape does
    produce, pre-fix, is one futile statement per tag against a Point that is not
    there; after the fix the door returns at the conditional write's answer and
    issues none.
    """
    sdk = _sdk(tmp_path, "vanishtags")
    try:
        pid = sdk.create_point("evidence", f"claim {uuid.uuid4().hex}",
                               dedup=False, status="live")["id"]
        tag = f"10473-{uuid.uuid4().hex[:8]}"
        proj = sdk._get_proj()
        proxy = _VanishOnFirstWrite(proj.g, _POINT_PROP_SET_NEEDLE)
        proj.g = proxy
        sdk.update_point(pid, tags=[tag])
        proj.g = proxy._real

        assert proxy.rows_matched == 0, "fixture precondition"
        assert proxy.tag_statements == 0, (
            "a tag sync ran against a Point that does not exist — the door "
            f"issued {proxy.tag_statements} `MERGE (t:Tag …)` statement(s) for "
            "a target the conditional write proved gone")
        orphans = proj.g.query(
            "MATCH (t:Tag {name:$tag}) RETURN count(t)",
            params={"tag": tag}).result_set[0][0]
        assert not orphans, (
            "PREMISE, not the fix — the MERGE is anchored on the Point match, "
            "so it mints nothing on this shape either way; the discriminating "
            f"assertion in this test is the statement count above ({tag!r})")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_LANDED_update_still_runs_the_ep_side_effects(tmp_path):
    """The negative control: the gate must not disarm a real revision.

    Without this, "no epoch advance, no dirty root" would also pass on a fix
    that simply deleted the tail — which would strand EVERY revision's
    confidence recompute.
    """
    sdk = _sdk(tmp_path, "vanishcontrol")
    try:
        pid = sdk.create_point("evidence", f"claim {uuid.uuid4().hex}",
                               dedup=False, status="live")["id"]
        sdk._dirty_roots.clear()
        before_ep = _ep_version(sdk)

        out = sdk.update_point(pid, confidence=0.42)

        assert out.get("confidence") == 0.42, (
            f"the write must land on a present target, saw {out.get('confidence')!r}")
        assert _ep_version(sdk) == before_ep + 1, (
            "a LANDED revision must advance the EP epoch — otherwise the fix "
            "disarmed the write path instead of the vanish shape: "
            f"{before_ep} -> {_ep_version(sdk)}")
        assert pid in sdk._dirty_roots, (
            "a LANDED revision must leave its point a dirty root, or its "
            "confidence is never re-derived (#1240/DE2E-4): roots "
            f"{sorted(sdk._dirty_roots)}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── the presence fold's projection references are SYMBOLS, not line cites ──
#
# `_apply_journal_presence` reads the same journal records the projection does and
# says so in comments; so do the promote notes beside it and the `sourceVersion`
# anchor in `_write_relation`. Every one of those references used to be a
# `file:line` cite — and NOTHING resolves a `projection/` or `record_projection.py`
# line cite written in CODE: the bundle's `_BUNDLE_CITE` sweep matches
# `sdk|edges|extractor_v2` only and reads DOCUMENTS (not `sdk.py`); its sibling
# pattern resolves `projection/__init__.py`/`entities.py` cites in those same
# DOCUMENTS, not a cite in this file; and `_PREREG_STEP_CITE_DOCS` reads the
# extractor's own cites. So those numbers re-staled in silence, and all five had
# drifted by PR #10543's head (measured, each target read back):
#
#   `projection/__init__.py:8060`   — the `PointsMerged` presence scan. The line
#                                     sat mid-docstring in
#                                     `plan_point_restamp_folds`; the scan it
#                                     means had moved on.
#   `projection/__init__.py:1074`   — the `PointPromoted` replay arm. The line
#                                     was delete-masking prose (#8256).
#   `projection/__init__.py:1082`   — the `OperatorPromoted` replay arm. Same.
#   `projection/edges.py:504`       — `_anchor_on_create`. The line was
#                                     `_mint_subject_stub`'s Subject arm.
#   `record_projection.py:2061-2068`— the "re-link must not ADVANCE a recorded
#                                     version" rule. The line was a cache-tier
#                                     UPDATE.
#
# The durable fix is the one the #9675 index row already records — "cite the
# symbol, not the line". This leg refuses the numeric form for the two modules
# nothing sweeps, and resolves each symbol the comments now name against the file
# they attribute it to, so a rename reds here instead of leaving a comment
# pointing at a stranger.

#: The modules `sdk.py` cites but no sweep resolves. `projection/` is the fast-
#: moving one; `record_projection.py` carries the same `sourceVersion` vocabulary.
_UNCWEPT_CITE = re.compile(
    r"(?<![\w.])(?:tortoise/)?(?:projection/[\w]+|record_projection)\.py:\d+")


def _decommented(path: Path) -> str:
    """The source with comment markers collapsed.

    A cite can be WRAPPED across two ``#`` lines — the ``PointPromoted`` one was
    written as ``(projection/`` newline ``# __init__.py:1074)`` — and a
    line-scoped scan does not match that at all, which is how it survived every
    earlier re-derivation. The marker is removed with NO separator so the halves
    of a wrapped cite rejoin exactly; that is the only way the ``:1074`` form is
    visible to a scanner at all.
    """
    text = path.read_text(encoding="utf-8")
    return re.sub(r"\s+", " ", re.sub(r"\s*#\s*", "", text))


def test_the_presence_fold_cites_the_projection_by_symbol_not_by_line():
    """Nothing sweeps these numbers, so they must not be numbers.

    Non-vacuity: at the pre-fix head `tortoise/sdk.py` carried five live cites of
    exactly this shape (listed above), so the refusal reds; after them, the
    symbol-resolution arms are the ones that can fail.
    """
    sdk_path = _REPO / "tortoise" / "sdk.py"
    found = _UNCWEPT_CITE.findall(_decommented(sdk_path))
    assert not found, (
        f"`tortoise/sdk.py` cites an unswept module by LINE again: {sorted(found)} "
        "— no sweep resolves a `projection/` or `record_projection.py` line cite "
        "(the bundle's `_BUNDLE_CITE` matches `sdk|edges|extractor_v2` in the "
        "bundle DOCUMENTS only), so the number rots silently on the next insertion "
        "above it; cite the symbol")

    # The scanner itself is not vacuous: it DOES see both spellings, including the
    # wrapped one, on text where they are live. (The range's END is not matched —
    # a ban does not need it, and refusing `:2061` is what stops the number.)
    probe = "  # see (projection/\n  # __init__.py:1074) and record_projection.py:2061-2068\n"
    assert sorted(_UNCWEPT_CITE.findall(
        re.sub(r"\s+", " ", re.sub(r"\s*#\s*", "", probe)))) == [
            "projection/__init__.py:1074", "record_projection.py:2061"]

    sdk = sdk_path.read_text(encoding="utf-8")
    proj = (_REPO / "tortoise" / "projection" / "__init__.py").read_text(
        encoding="utf-8")
    edges = (_REPO / "tortoise" / "projection" / "edges.py").read_text(
        encoding="utf-8")
    record = (_REPO / "tortoise" / "record_projection.py").read_text(
        encoding="utf-8")
    for symbol, owner, source in (
        ("plan_point_restamp_folds", "projection/__init__.py", proj),
        ("FalkorProjection", "projection/__init__.py", proj),
        ("_anchor_on_create", "projection/edges.py", edges),
        ("_relation_upsert_statement", "record_projection.py", record),
    ):
        assert symbol in sdk, (
            f"`{symbol}` is no longer named in `tortoise/sdk.py` — the comment "
            "that used to cite it by line now cites nothing, so its claim is "
            "unattributable")
        assert symbol in source, (
            f"`tortoise/sdk.py` attributes a claim to `{symbol}` in `{owner}`, but "
            f"`{owner}` no longer defines it — re-derive the symbol the mechanic "
            "moved to")

    # The CLAIM the presence-fold comment makes still holds: the projection folds
    # a merged-away id to NOT-present (a pending delete), the same verdict this
    # module's `PointsMerged` arm reaches.
    assert 'pending_deleted.add(("Point", mid))' in proj, (
        "the comment on `_apply_journal_presence`'s `PointsMerged` arm claims "
        "`plan_point_restamp_folds` folds every merged id to NOT-present; the "
        "projection's pending-delete fold is gone — re-derive the claim, do not "
        "delete the assertion")


def test_a_fifo_at_the_journal_path_cannot_park_the_presence_scan(tmp_path):
    """#10702 review (non-blocking 1): the ABSENT-write scan must never WAIT.

    ``MentionScan.__iter__`` opened the journal with a plain ``open``, while its
    sibling :func:`tortoise.log._read_regular_range` is hardened with
    ``O_NONBLOCK | S_ISREG`` because it too runs on ``update_point``'s
    ABSENT-write path. A FIFO left at the configured journal path therefore
    PARKED ``open`` until a writer appeared — synchronously, inside an
    HTTP-exposed call — where the seam read makes the same file a MISS.

    ⛔ RUN IN A SUBPROCESS, deliberately. Pre-fix the failure mode is a permanent
    WAIT, so an in-process assertion would HANG the suite rather than fail it; a
    subprocess with a timeout turns "parked" into the failure this case reports.
    """
    fifo = tmp_path / "journal.fifo"
    os.mkfifo(fifo)
    code = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from tortoise.log import MentionScan
        try:
            lines = list(MentionScan(Path(sys.argv[1])))
        except OSError as exc:
            print(f"refused: {type(exc).__name__}")
            raise SystemExit(0)
        print(f"scanned: {len(lines)}")
        raise SystemExit(3)
        """)
    try:
        done = subprocess.run([sys.executable, "-c", code, str(fifo)],
                             capture_output=True, text=True, timeout=120,
                             cwd=str(_REPO))
    except subprocess.TimeoutExpired:
        pytest.fail(
            "the presence scan PARKED on a FIFO at the journal path: the scan's "
            "open waited for a writer, so an absent write on this lane never "
            "returns — the failure `_read_regular_range`'s O_NONBLOCK exists to "
            "prevent")
    assert done.returncode == 0, (
        "a non-regular journal must be REFUSED promptly, never scanned as "
        f"garbage and never waited on: rc={done.returncode} "
        f"out={done.stdout!r} err={done.stderr[-800:]!r}")
    assert "refused" in done.stdout, done.stdout


def test_the_presence_scan_still_follows_a_symlinked_journal(tmp_path):
    """Why the hardening above must NOT reach for ``O_NOFOLLOW``.

    ``test_a_seam_that_cannot_be_read_drops_the_map_instead_of_trusting_it``
    depends on this asymmetry: the SEAM read refuses a symlinked journal
    (``O_NOFOLLOW`` ⇒ ``ELOOP`` ⇒ unreadable ⇒ re-fold) while this scan FOLLOWS
    it, so the memo stays live and only the guard dies — the combination that
    makes the unreadable-seam branch reachable. A scan that refused symlinks
    would make that state impossible and silently retire the guard.
    """
    from tortoise.log import MentionScan

    real = tmp_path / "real-journal.jsonl"
    real.write_bytes(b'{"id": "a"}\n{"id": "b"}\n')
    link = tmp_path / "journal.jsonl"
    os.symlink(real, link)
    assert list(MentionScan(link)) == [b'{"id": "a"}', b'{"id": "b"}']


def test_a_fifo_at_the_journal_path_cannot_park_the_absent_write(tmp_path):
    """#10702 review (P2-1): the SCAN refusing is only half the claim.

    ``MentionScan._open``'s reason for existing is that this scan runs on
    ``update_point``'s ABSENT-write path, synchronously inside an HTTP-exposed
    call — and the scan-level test above never exercises that half. What a
    caller needs is that the whole door still answers: ``{}`` and no raise, the
    loss reported as UNJUDGED (``journal_unreadable`` — the tri-state's THIRD
    value, never ``False``/"a typo"), and ``journal_known`` untouched.

    ⛔ RUN IN A SUBPROCESS for the same reason as the scan-level case: pre-fix
    the failure mode is a permanent WAIT inside the open, which would hang the
    suite rather than fail it. ``cwd`` is the repo root so the child resolves
    ``import tortoise`` from the checkout rather than inheriting whatever
    directory pytest was launched from.

    Non-vacuity: with ``_open`` back to a plain ``open`` the child never prints
    and ``subprocess.run`` times out — and a fix that dropped the ``S_ISREG``
    refusal (or let the ``OSError`` escape ``update_point``) reddens the reply
    or the counter assertions instead.
    """
    code = textwrap.dedent(
        """
        import json, os, sys, uuid
        from tortoise import monitoring
        from tortoise.sdk import TortoiseSDK

        db_path, journal = sys.argv[1], sys.argv[2]
        sdk = TortoiseSDK(
            None if os.environ.get("TORTOISE_DB_URI") else db_path,
            namespace=f"test_7405_fifo_{uuid.uuid4().hex[:8]}",
            event_log_path=journal)
        try:
            # materialise the lane BEFORE the FIFO exists: an embedded lane
            # AUTO-REPLAYS a journal into an empty db, and a replay would park
            # on the FIFO in the fixture rather than in the code under test
            sdk._get_proj()
            os.mkfifo(journal)
            pid = str(uuid.uuid4())
            before = monitoring.silent_revision_loss_counts()
            reply = sdk.update_point(pid, confidence=0.9)
            after = monitoring.silent_revision_loss_counts()
            print(json.dumps({
                "reply": reply,
                "tri": sdk._journal_knows_point(pid),
                "unreadable": (after.get("journal_unreadable", 0)
                               - before.get("journal_unreadable", 0)),
                "known": (after.get("journal_known", 0)
                          - before.get("journal_known", 0)),
            }))
        finally:
            sdk.close()
        """)
    try:
        done = subprocess.run(
            [sys.executable, "-c", code,
             str(tmp_path / "fifo.db"), str(tmp_path / "journal.fifo")],
            capture_output=True, text=True, timeout=180, cwd=str(_REPO))
    except subprocess.TimeoutExpired:
        pytest.fail(
            "`update_point` PARKED on a FIFO at the journal path: the "
            "ABSENT-write scan's open waited for a writer, so every absent "
            "write on this lane never returns — the failure "
            "`_read_regular_range`'s O_NONBLOCK exists to prevent")
    assert done.returncode == 0, (
        "a FIFO at the journal path must be REPORTED, never waited on and "
        f"never raised: rc={done.returncode} out={done.stdout!r} "
        f"err={done.stderr[-1200:]!r}")
    seen = json.loads(done.stdout.strip().splitlines()[-1])
    assert seen["reply"] == {}, (
        "the documented ABSENT-arm reply is `{}`: an unreadable journal is a "
        f"REPORT, not a 500: {seen}")
    assert seen["tri"] is None, (
        "an unreadable journal is UNJUDGED — the tri-state's third value. "
        f"`False` is \"a typo\" and silences the loss: {seen}")
    assert seen["unreadable"] == 1 and seen["known"] == 0, (
        f"the loss must be counted as journal_unreadable alone: {seen}")
