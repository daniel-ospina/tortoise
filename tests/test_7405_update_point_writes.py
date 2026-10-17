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
import os
import types
import uuid

import pytest
import redis.exceptions as redis_exc

from tortoise.sdk import TortoiseSDK

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
    is therefore forced on the retried update path, so it is a function of the
    caller's tag list and not of the attempt.
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

    This counts EVERY wrapped call the door makes, so a new site on any label
    (including one inside a helper) breaks the assertion instead of quietly
    widening the bound.
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


def test_a_first_attempt_that_matches_nothing_keeps_the_empty_contract(
        tmp_path):
    """The witness must NOT change a legitimate zero-row FIRST attempt.

    ``update_point`` on a missing id historically returns ``{}`` without
    raising. A first attempt never consults the witness, so this stays true.
    (The GATE itself — "only a graph-replaced abort arms it" — is pinned by
    ``test_a_write_slot_abort_on_a_missing_target_keeps_the_empty_contract``;
    this test alone cannot observe it, since ``origin/main`` passes it too.)
    """
    sdk = _sdk(tmp_path, "missing")
    try:
        missing = str(uuid.uuid4())
        assert sdk.update_point(missing, confidence=0.5) == {}
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


_WRITE_SLOT = "another write is in progress, retry the query"


def test_a_write_slot_abort_on_a_missing_target_keeps_the_empty_contract(
        tmp_path, monkeypatch):
    """A non-rebuild abort must NOT arm the witness (#8854 review P1).

    ``retryable_aborted_write`` also admits the write-slot arm — ordinary
    multi-writer contention — which says nothing about whether the TARGET
    exists. Arming the witness on it turned ``update_point(<missing id>)`` into
    a stale ``ResponseError`` after four attempts, where the documented answer
    is ``{}``. Only the graph-deleted-or-replaced family can mean "not
    replayed yet", so only that family arms the witness.
    """
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "lockslot")
    try:
        missing = str(uuid.uuid4())
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _POINT_PROP_SET_NEEDLE,
                               message=_WRITE_SLOT)
        proj.g = proxy

        assert sdk.update_point(missing, confidence=0.5) == {}
        assert proxy.matching >= 2, "the aborted statement was never re-issued"
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


def test_a_promote_refused_after_a_retry_keeps_its_illegal_transition(
        tmp_path, monkeypatch):
    """A present-but-not-draft node keeps the documented ValueError."""
    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "promoterace")
    try:
        pid = sdk.create_point(
            "evidence", f"draft {uuid.uuid4().hex}", status="draft")["id"]
        proj = sdk._get_proj()
        proxy = _AbortOnceThenSetStatus(proj.g, _POINT_PROMOTE_NEEDLE)
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
        tmp_path):
    """A re-issued tag sync against an unreplayed Point is NOT a success.

    Measured pre-fix: ``update_point(pid, tags=[a, b])`` returned a point dict
    while the graph held ZERO ``TAGGED`` edges and no ``Point`` at all — the
    success report the whole retry exists to avoid (#6666 class). The tag arm
    rode ``_graph_write_with_retry`` WITHOUT a witness, so nothing asked the
    graph whether the re-issue had landed.

    The abort therefore surfaces unchanged, exactly as it does on the property
    arm — one behaviour for the whole mutation, not one per statement.
    """
    sdk = _sdk(tmp_path, "tagwitness")
    try:
        pid = _pid(sdk)
        proj = sdk._get_proj()
        proxy = _AbortThenUnreplay(proj.g, _TAGGED_NEEDLE, victim=pid)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            sdk.update_point(pid, tags=["7405ghost", "7405spectre"])

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
    requested — ``landed == 0 == len(wanted)`` and the call returns. This pins
    the FULL-SET rule against a future ``>= 1`` reading of it, which would red
    an honest clear.
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
