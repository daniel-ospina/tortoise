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
