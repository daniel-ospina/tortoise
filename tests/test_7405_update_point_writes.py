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
                 present: bool = True, skip: int = 0) -> None:
        self._real = real
        self._needle = needle
        self._aborts = aborts
        self._present = present
        self._skip = skip
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
    """The stated bound is one window per wrapped CALL SITE a branch reaches.

    ``_graph_write_with_retry``'s docstring states the caller-visible bound as
    one 7-14 s window per call: one for a plain property update, one for
    ``annotate_operator``, two for ``update_point(tags=...)`` and THREE for
    ``update_point(content=..., tags=...)`` (property loop, derived-embedding
    clear+set, tag sync). #8854 review round 4 found that count stated as "at
    most two", which understates the door's worst case by a whole window — the
    number a caller sizes a timeout against, so it is pinned rather than left to
    prose.

    Discriminating power: pre-fix the wrapper was never called by the door at
    all (empty list), and folding the embedding write into the per-key loop's
    ``fn`` would drop the third case to two. Only the ``update_point``-labelled
    sites are compared: the door also forwards ``_mark_dirty`` / ``_advance_ep_
    version`` (pre-existing callers, not part of the enumerated bound).
    """
    sdk = _sdk(tmp_path, "bound")
    try:
        src = _pid(sdk)
        tgt = _pid(sdk)
        op = sdk.create_operator("IMPL", src, [tgt])["id"]
        seen: list[str] = []
        original = sdk._graph_write_with_retry

        def counting(fn, *, what):
            if what.startswith("update_point "):
                seen.append(what)
            return original(fn, what=what)

        monkeypatch.setattr(sdk, "_graph_write_with_retry", counting)

        sdk.update_point(src, confidence=0.5)
        assert seen == ["update_point SET (per-key)"], seen

        seen.clear()
        sdk.update_point(src, tags=["a"])
        assert seen == ["update_point SET (per-key)",
                        "update_point TAGGED edges"], seen

        seen.clear()
        sdk.update_point(src, content="revised claim", tags=["a"])
        assert seen == ["update_point SET (per-key)",
                        "update_point SET (embedding)",
                        "update_point TAGGED edges"], seen

        seen.clear()
        sdk.annotate_operator(op, bias=0.2, precision=0.8,
                              consistency=0.7, directness=0.9)
        assert seen == ["update_point SET (per-key)"], seen
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── the loud arm: a DELETED graph is never re-issued into ───────────────

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
