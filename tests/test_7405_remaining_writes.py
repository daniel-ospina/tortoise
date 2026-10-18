"""#7405 — the REMAINING write surfaces ride the bounded write retry.

``tortoise/retry.py``'s header used to say the primitive was only wired into
``create_point``'s two direct writes, and named the residue itself: *"Wiring the
remaining write surfaces — ``_post_commit`` (tortoise/sdk.py) and the
capture/commit graph writes — is separate work (audit G8)"*. This file pins the
half this change wires, one test per SURFACE, so the header can be updated to
the truth of what is covered without over-claiming (three separate over-claims
were already caught on this workstream; the issue carries two commits whose
whole content is that correction).

The surfaces, and why re-issuing each is safe (idempotency is REASONED from each
statement's shape — a MERGE on an explicit key, an absolute ``SET``, an
``eventId IS NULL`` guard, or a DELETE by an explicit id list — and the retry
tests below pin that each aborted statement is re-issued; they do not measure
idempotency):

1. ``_write_session_and_turns`` — the ``:Session`` MERGE (``MERGE (s:Session
   {id:$sid}) SET s.…=coalesce(s.…, …)``). Re-running it re-MERGEs the same node
   and the coalesce SETs are no-ops for every field already present: it cannot
   mint a second Session and cannot overwrite a first writer's value.
2. ``_write_capture_turns`` — the batched ``UNWIND $turns`` turn store. Every
   row MERGEs ``(t:Point {id: turn.id})`` (``turn.id`` is derived from the
   session id, so a re-issue targets the same node) and the ``CONTAINS`` edge is
   a MERGE too; ``t.status``/``t.createdAt`` use ``coalesce`` so a re-issue
   cannot demote or re-stamp a stored turn.
3. ``_write_capture_turns`` — the stale-turn sweep (``… WHERE t.id IN $ids
   DETACH DELETE t``). A delete by explicit id is a no-op for any id already
   gone; the id list is read BEFORE the delete, so a re-issued statement carries
   the same list.

4. ``_post_commit`` — the ``POST /v1/sessions/commit`` transmission primitive.
   It is NOT a graph write, so it rides ``retryable_transient`` (the
   transport-class predicate) rather than ``retryable_aborted_write`` (the
   write-abort predicate, which needs a graph-state probe that is meaningless
   for an HTTP POST). Re-POSTing is safe by construction: the body carries a
   deterministic ``client_commit_id`` and the server serializes on it
   atomically (``CommitRecordStore.acquire`` — an atomic MERGE on
   ``client_commit_id``), so a duplicate POST sees the winner's record instead
   of double-applying. This is the ``client_commit_id`` replay + server-side
   dedup the retry header already named as this path's robustness.

⛔ The retry must NOT mask a deletion. The engine reports the SAME abort for a
rebuild and for a plain deletion, so the graph-abort family is retried only
while the graph KEY is present (``_graph_exists``). That loud arm is a shared
property of ``_graph_write_with_retry`` and is pinned by the deleted-graph
tests, not by every retry test.

Lane: runs on the live graph when ``TORTOISE_DB_URI`` is set (the docker lane)
and on embedded FalkorDBLite otherwise — the same dual-lane convention as
``test_7209_write_landing.py``.
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
_SESSION_MERGE_NEEDLE = "MERGE (s:Session {id:$sid}) SET"
_TURN_WRITE_NEEDLE = "UNWIND $turns AS turn"
_STALE_SWEEP_NEEDLE = "WHERE t.id IN $ids DETACH DELETE t"


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
        namespace=f"test_7405_{tag}_{uuid.uuid4().hex[:8]}",
    )


def _turns(n: int) -> list[dict]:
    return [{"role": "user", "content": f"turn {i}"} for i in range(n)]


# ── 1. the :Session MERGE ────────────────────────────────────────────────


def test_a_rebuild_abort_on_the_session_merge_is_retried_and_applies_once(
        tmp_path):
    """A rebuild abort on the ``:Session`` MERGE must not lose the capture.

    Before this wiring the MERGE was a bare ``proj.g.query``: a per-query abort
    raised out of ``_write_session_and_turns`` BEFORE the turn store, so the
    capture lost both its session row and its turns — the finding the session
    carried never reaches the map, and the caller only sees a raise.
    """
    from tortoise.sdk import _write_session_and_turns

    sdk = _sdk(tmp_path, "sessmerge")
    try:
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _SESSION_MERGE_NEEDLE, present=True)
        proj.g = proxy

        _write_session_and_turns(proj, sdk, "7405-merge", _turns(1),
                                 now="2026-10-13T00:00:00Z")

        assert proxy.matching >= 2, "the aborted MERGE was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued MERGE must not duplicate")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (s:Session {id:$sid}) RETURN count(s)",
            params={"sid": "7405-merge"}).result_set
        assert rows and rows[0][0] == 1, "the Session must be present exactly once"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 2. the batched turn store ────────────────────────────────────────────


def test_a_rebuild_abort_on_the_turn_store_is_retried_and_applies_once(tmp_path):
    """The batched ``UNWIND $turns`` write is retried, and cannot duplicate.

    The turn store is the highest-volume Point producer on the capture path. A
    bare abort here left the Session committed with NO turns: the episodic leg
    the whole capture exists for is missing, and the next read finds an empty
    session.
    """
    from tortoise.sdk import _write_session_and_turns

    sdk = _sdk(tmp_path, "turnwrite")
    try:
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _TURN_WRITE_NEEDLE, present=True)
        proj.g = proxy

        _write_session_and_turns(proj, sdk, "7405-turns", _turns(2),
                                 now="2026-10-13T00:00:00Z")

        assert proxy.matching >= 2, "the aborted turn write was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued MERGE must not duplicate")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (:Session {id:$sid})-[:CONTAINS]->(t:Point) "
            "WHERE t.is_episodic = true RETURN count(t)",
            params={"sid": "7405-turns"}).result_set
        assert rows and rows[0][0] == 2, (
            "both turns must be present exactly once after the retry")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 3. the stale-turn sweep ──────────────────────────────────────────────


def test_a_rebuild_abort_on_the_stale_turn_sweep_is_retried(tmp_path):
    """The #1920 stale-turn ``DETACH DELETE`` is retried.

    A shorter re-capture deletes the turns beyond the new window. If the abort
    lands on that delete, the residue stays ``CONTAINS``-wired to the Session
    while ``s.turn_count`` reports the new, shorter length — the stored count
    and the CONTAINS walk disagree, and turns the session no longer owns remain
    reachable from it.
    """
    from tortoise.sdk import _write_session_and_turns

    sdk = _sdk(tmp_path, "sweep")
    try:
        proj = sdk._get_proj()
        _write_session_and_turns(proj, sdk, "7405-sweep", _turns(2),
                                 now="2026-10-13T00:00:00Z")
        proxy = _AbortOnceGraph(proj.g, _STALE_SWEEP_NEEDLE, present=True)
        proj.g = proxy

        # Shorter re-capture: floor 2 -> 1, so id ``…_t1`` is stale.
        _write_session_and_turns(proj, sdk, "7405-sweep", _turns(1),
                                 now="2026-10-13T00:00:01Z",
                                 session_existed=True)

        assert proxy.matching >= 2, "the aborted sweep was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued DELETE must not double-delete")
        proj.g = proxy._real
        rows = proj.g.query(
            "MATCH (:Session {id:$sid})-[:CONTAINS]->(t:Point) "
            "WHERE t.is_episodic = true RETURN count(t)",
            params={"sid": "7405-sweep"}).result_set
        assert rows and rows[0][0] == 1, (
            "the stale turn must be gone and the kept turn present")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── the loud arm: a DELETED graph is never re-issued into ────────────────


def test_a_deleted_graph_abort_on_the_session_merge_stays_loud(tmp_path):
    """The retry must not MASK a deletion — the #7685 guarantee, extended.

    With the graph key ABSENT the engine's sentence means a plain deletion, and
    re-issuing would write into a graph FalkorDB auto-created EMPTY and report
    SUCCESS against a graph the caller never wrote to (#6666 class). The abort
    therefore surfaces unchanged.
    """
    from tortoise.sdk import _write_session_and_turns

    sdk = _sdk(tmp_path, "sessdeleted")
    try:
        proj = sdk._get_proj()
        proxy = _AbortOnceGraph(proj.g, _SESSION_MERGE_NEEDLE, present=False)
        proj.g = proxy

        with pytest.raises(redis_exc.ResponseError, match="deleted or replaced"):
            _write_session_and_turns(proj, sdk, "7405-deleted", _turns(1),
                                     now="2026-10-13T00:00:00Z")

        assert proxy.applied == 0, (
            "a deleted graph must not be re-issued into — nothing may be "
            "reported as applied")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 4. _post_commit — the transport-class retry on the commit POST ───────


class _FakeResponse:
    def __init__(self, body: dict, *, status: int = 200) -> None:
        self._body = body
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> dict:
        return self._body


def _commit_payload(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "points": [], "entities": [], "operators": [],
        "summary": "", "story_arc": "",
    }


def test_post_commit_retries_a_transport_connection_error(monkeypatch):
    """The commit POST is re-issued on a connection error (replay-safe).

    ``_post_commit`` is the client transmission primitive for session-derived
    content. A ``ConnectionError`` means the reply was lost — the server may or
    may not have applied the commit — which is exactly why the body carries a
    deterministic ``client_commit_id``: the server serializes on it atomically,
    so the re-POST is deduplicated rather than double-applied. Before this
    wiring the error surfaced immediately and the caller reported ``ok=False``
    for a commit that a single re-issue would have landed.
    """
    import requests

    from tortoise.sdk import _post_commit

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    calls: list[dict] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        # Snapshot the payload INDEPENDENTLY: appending the same dict object
        # twice would make the comparison below reduce to ``x == x`` and miss
        # a payload mutated between attempts.
        calls.append(dict(json))
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("connection reset")
        return _FakeResponse({"ok": True, "committed": True})

    monkeypatch.setattr(requests, "post", fake_post)

    result = _post_commit(_commit_payload("7405-post-ok"), base_url="http://x")

    assert result == {"ok": True, "committed": True}
    assert len(calls) == 2, "the aborted POST was never re-issued"
    # Replay safety is the constraint that makes the retry legitimate: BOTH
    # attempts carry the SAME deterministic client_commit_id, which is the
    # server's atomic dedup key.
    assert calls[0]["client_commit_id"] == calls[1]["client_commit_id"]
    assert calls[0]["client_commit_id"]


def test_post_commit_does_not_retry_a_deterministic_http_error(monkeypatch):
    """A deterministic HTTP status is NOT a transport transient.

    ``retryable_transient`` excludes ``HTTPError`` FIRST (it is an OSError on
    3.12), so a 4xx/5xx status response is surfaced immediately. Pinned here
    because the predicate arm this surface uses must not be widened: re-issuing
    a refused commit only burns the budget and delays the caller's failure.
    """
    import requests

    from tortoise.sdk import _post_commit

    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    calls: list[int] = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return _FakeResponse({}, status=500)

    monkeypatch.setattr(requests, "post", fake_post)

    with pytest.raises(requests.HTTPError):
        _post_commit(_commit_payload("7405-post-500"), base_url="http://x")

    assert len(calls) == 1, "a deterministic HTTP error must not be retried"


# ── 5. the capture lane's follow-up provenance / state stamps ────────────────
#
# These run AFTER the store writes. Each is non-fatal — `capture_session` wraps
# it in its own try/except and downgrades a failure to an additive warning —
# but the write is still LOST on an abort: provenance the eventId-keyed memory
# layer cannot reach, the link counters, or the #2335 retry bit (`capture_ok`)
# the NEXT same-session capture reads to decide replay vs TRUE-retry. Each is
# idempotent (see the wiring comments: absolute-value SETs, or a statement
# carrying its own `eventId IS NULL` guard), and each is proved below by
# driving the REAL `capture_session` with the extractor seam monkeypatched.

_CONV = [
    {"role": "user",
     "content": "we must ship github.com/test/repo/issues/42 today"},
    {"role": "assistant", "content": "noted"},
]

# Needles unique to each stamp (a short fragment can also match a READ, or a
# like-shaped statement issued by the EP pass that runs after the capture).
_MINTED_STAMP_NEEDLE = "MATCH (n:Point) WHERE n.id IN $ids SET n.eventId"
_LABEL_STAMP_NEEDLE = (
    "WHERE c.id IN $ids AND (o.status IS NULL OR o.status = 'draft')")
_OPERATOR_ID_STAMP_NEEDLE = (
    "MATCH (o:Point {is_operator:true}) WHERE o.id IN $ids")
_RETRY_HEAL_NEEDLE = (
    "MATCH (s:Session {id:$sid})-[c:CONTAINS]->(n:Point) WHERE n.eventId IS NULL")
_ENTITY_LINK_NEEDLE = "SET s.entity_links_attempted=$a"
_CAPTURE_OK_NEEDLE = "SET s.capture_ok=$ok"


def _pt(pid: str, content: str, kind: str = "statement") -> dict:
    return {"id": pid, "content": content, "pointKind": kind, "reason": "NEW",
            "confidence": 0.5, "c_cal": 0.5, "about_entities": [],
            "source_ref": "session.md", "quote": "", "status": "draft",
            "search_keys": []}


def _v2_payload(points, operators) -> dict:
    return {"session_id": "x", "client_commit_id": "",
            "captured_at": "2026-10-13T00:00:00Z",
            "extractor": {"version": "t", "mode": "byok",
                          "calibration_version": "v2"},
            "summary": "", "story_arc": "", "provenance_refs": [],
            "sources": [], "entities": [], "points": points,
            "events": [], "operators": operators, "supersessions": [],
            "telemetry": {"counts": {"kept": len(points)}}}


def _mock_v2(monkeypatch, payload):
    """Point the v2 extraction seam at a fixed payload — no LLM, no network."""
    import tortoise.extractor_v2 as ev2

    def _run(_model, _conversation=None, *, session_id=None, **_kw):
        return {"payload": payload, "errors": [], "warnings": [], "stats": {},
                "noops": [], "chain_notes": [], "link_before_create": [],
                "supersessions": [], "minted_kinds": [], "story_arc": "",
                "search": {}, "error_census": {}}

    monkeypatch.setattr(ev2, "extract_session_v2", _run)


def _default_capture_payload():
    """Two minted points + one operator — reaches the minted/label/id stamps."""
    import tortoise.extractor_v2 as ev2
    prior = "the ingest queue has no backpressure control"
    other = "backpressure must be added before the next release"
    a, b = ev2._content_id("pt", prior), ev2._content_id("pt", other)
    return _v2_payload(
        [_pt(a, prior), _pt(b, other)],
        [{"src": a, "dst": b, "op_type": "IMPL",
          "direction": "unidirectional"}])


def _label_stamp_state(sdk, session_id: str) -> tuple:
    """The stamp state a capture left on its operators — the control
    projection ``(stamped, eventIds, harnesses, statuses)``."""
    rows = sdk._get_proj().g.query(
        "MATCH (o:Point {is_operator:true}) WHERE o.source_session=$sid "
        "RETURN count(o), count(distinct o.eventId), "
        "collect(distinct o.source_harness), collect(distinct o.status)",
        params={"sid": session_id}).result_set
    return tuple(rows[0]) if rows else (0, 0, [], [])


def _capture_with_abort(sdk, needle: str, *, session_id: str, present=True):
    """Run the REAL capture with one matching statement aborting once."""
    proj = sdk._get_proj()
    proxy = _AbortOnceGraph(proj.g, needle, present=present)
    proj.g = proxy
    try:
        result = sdk.capture_session(_CONV, session_id=session_id)
    finally:
        proj.g = proxy._real
    return proxy, result


def test_a_rebuild_abort_on_the_minted_point_provenance_stamp_is_retried(
        tmp_path, monkeypatch):
    """The minted points' ``eventId`` provenance stamp is retried.

    An abort here loses the stamp with only an additive warning: the points
    land, but they are invisible to every eventId-keyed memory query — the
    ``#2552`` ``operator_counts == {}`` signature, by the point route.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _mock_v2(monkeypatch, _default_capture_payload())
    sdk = _sdk(tmp_path, "mintedstamp")
    try:
        proxy, result = _capture_with_abort(
            sdk, _MINTED_STAMP_NEEDLE, session_id="7405-minted")
        assert result.get("ok") is True, result
        assert proxy.matching >= 2, "the aborted stamp was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — a re-issued absolute SET must not "
            "double-apply")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_operator_label_provenance_stamp_is_retried(
        tmp_path, monkeypatch):
    """The epistemic-label operator stamp loop is retried (#2552/#7853)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _mock_v2(monkeypatch, _default_capture_payload())
    sdk = _sdk(tmp_path, "labelstamp")
    try:
        proxy, result = _capture_with_abort(
            sdk, _LABEL_STAMP_NEEDLE, session_id="7405-label")
        assert result.get("ok") is True, result
        assert proxy.matching >= 2, "the aborted label stamp was never re-issued"
        # The loop issues one statement per epistemic label, so the proxy's own
        # ``applied == matching - 1`` arithmetic is a tautology of its default
        # ``aborts=1`` — it holds even when nothing is re-issued. Prove the
        # retry against the GRAPH instead: the aborted run must converge to the
        # SAME stamp state as a no-abort control run.
        control = _sdk(tmp_path, "labelstamp-control")
        try:
            control_result = control.capture_session(
                _CONV, session_id="7405-label-ctl")
            assert control_result.get("ok") is True, control_result
            aborted_state = _label_stamp_state(sdk, "7405-label")
            control_state = _label_stamp_state(control, "7405-label-ctl")
            assert aborted_state == control_state, (
                "the aborted label stamp did not converge to the no-abort "
                f"state: {aborted_state} != {control_state}")
            assert aborted_state[0] >= 1, (
                "the stamp must land on at least one operator")
        finally:
            with contextlib.suppress(Exception):
                control.close()
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_operator_id_provenance_stamp_is_retried(
        tmp_path, monkeypatch):
    """The ``operator_ids`` provenance stamp is retried (#4936)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _mock_v2(monkeypatch, _default_capture_payload())
    sdk = _sdk(tmp_path, "opidstamp")
    try:
        proxy, result = _capture_with_abort(
            sdk, _OPERATOR_ID_STAMP_NEEDLE, session_id="7405-opid")
        assert result.get("ok") is True, result
        assert proxy.matching >= 2, "the aborted operator stamp was never re-issued"
        assert proxy.applied == 1, "exactly one application"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_retry_heal_provenance_stamp_is_retried(
        tmp_path, monkeypatch):
    """The ``retry_failed_capture`` provenance heal is retried (#2335 WI-2b)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _mock_v2(monkeypatch, _default_capture_payload())
    sdk = _sdk(tmp_path, "healstamp")
    try:
        proj = sdk._get_proj()
        # The state a FAILED prior attempt leaves (the #2335 gate reads it):
        # the session exists, capture_ok False, lane 'v2' — so this capture is
        # a TRUE retry and the heal stamp runs.
        proj.g.query(
            "MERGE (s:Session {id:$sid}) "
            "SET s.capture_ok=false, s.capture_extractor='v2'",
            params={"sid": "7405-heal"})
        proxy, result = _capture_with_abort(
            sdk, _RETRY_HEAL_NEEDLE, session_id="7405-heal")
        assert result.get("ok") is True, result
        assert proxy.matching >= 2, "the aborted heal stamp was never re-issued"
        assert proxy.applied == 1, "exactly one application"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_entity_link_counter_stamp_is_retried(
        tmp_path, monkeypatch):
    """The ``entity_links_*`` counter SET is retried (#3664).

    Requires a resolvable WorkItem Object so the link pass actually attempts a
    link — the counter write only runs under ``if attempted``.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    import tortoise.extractor_v2 as ev2
    sdk = _sdk(tmp_path, "linkstamp")
    try:
        sdk.create_object("test/repo#42", objectKind="pm:issue")
        _mock_v2(monkeypatch, _v2_payload(
            [_pt(ev2._content_id("pt", "a claim about the release"),
                 "a claim about the release")], []))
        proxy, result = _capture_with_abort(
            sdk, _ENTITY_LINK_NEEDLE, session_id="7405-link")
        assert result.get("ok") is True, result
        assert proxy.matching >= 2, (
            "the aborted counter stamp was never re-issued — the link pass "
            "did not attempt a link, so this surface was not exercised")
        assert proxy.applied == 1, "exactly one application"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_capture_outcome_stamp_is_retried(
        tmp_path, monkeypatch):
    """The ``capture_ok`` / ``capture_extractor`` SET is retried (#2335)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _mock_v2(monkeypatch, _default_capture_payload())
    sdk = _sdk(tmp_path, "okstamp")
    try:
        proxy, result = _capture_with_abort(
            sdk, _CAPTURE_OK_NEEDLE, session_id="7405-ok")
        assert result.get("ok") is True, result
        assert proxy.matching >= 2, "the aborted outcome stamp was never re-issued"
        assert proxy.applied == 1, "exactly one application"
        proj = sdk._get_proj()
        row = proj.g.query(
            "MATCH (s:Session {id:$sid}) RETURN s.capture_ok",
            params={"sid": "7405-ok"}).result_set[0]
        assert row[0] is True, "the outcome stamp must land as True"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_deleted_graph_abort_on_the_capture_outcome_stamp_stays_loud(
        tmp_path, monkeypatch):
    """The capture stamps keep the #7685 loud arm too.

    With the graph key ABSENT the abort is a deletion, so the retry refuses.
    The capture's own try/except downgrades it to an additive warning — the
    call must NOT report success for a state write that did not land, and it
    must not have re-issued into a graph FalkorDB auto-created empty.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    _mock_v2(monkeypatch, _default_capture_payload())
    sdk = _sdk(tmp_path, "okdeleted")
    try:
        proxy, result = _capture_with_abort(
            sdk, _CAPTURE_OK_NEEDLE, session_id="7405-okdel", present=False)
        assert proxy.applied == 0, (
            "a deleted graph must not be re-issued into")
        assert any("capture_ok state write failed" in w
                   for w in result.get("warnings", [])), (
            "the lost state write must be surfaced on the receipt, never "
            "silently swallowed")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 6. the residual SDK write surfaces (the increment after #8698) ───────────
#
# Each surface below was named in ``tortoise/retry.py``'s ``⛔ NOT covered``
# list and is now wrapped by ``TortoiseSDK._graph_write_with_retry``. One test
# per STATEMENT, not per method: a method may issue several writes, and the unit
# of retry (and of the idempotency argument) is the statement.
#
# Re-issuing is safe for every surface here, and the reason is named per test:
#
#   * ``_mark_dirty``'s persist SET and its terminal clear SET are ABSOLUTE SETs
#     guarded by the statement's own WHERE (a fixed literal value under a
#     predicate the statement re-reads at write time);
#   * ``delete_point``'s DETACH DELETE and orphan-tag GC are deletes by an
#     explicit id / a re-derived "orphan" predicate — a re-issue of an already
#     applied delete is a no-op;
#   * ``promote_point``'s CAS SET re-evaluates the same ``status = 'draft'``
#     predicate;
#   * ``create_operator`` and ``mitigate_operator`` issue bare ``CREATE``s whose
#     id is minted ONCE above the statement, so a re-issue names the same node —
#     admissible ONLY because ``retryable_aborted_write`` proves the abort means
#     the statement did NOT land;
#   * the two ``_link_source`` legs are MERGE-idempotent at every step.
#
# Each positive test asserts the aborted statement was re-issued (a statement
# count, not a verdict about the graph).

_MARK_DIRTY_PERSIST_NEEDLE = "SET n.ep_dirty = true, n.ep_dirty_at = $ep"
_MARK_DIRTY_CLEAR_NEEDLE = "SET n.ep_dirty = null, n.ep_dirty_at = null"
_DELETE_POINT_NEEDLE = "MATCH (n:Point {id:$id}) DETACH DELETE n"
_TAG_GC_NEEDLE = "WHERE NOT (t)<-[:TAGGED]-() DELETE t"
_PROMOTE_CAS_NEEDLE = "SET n.status = 'live', n.reviewed = true"
_OPERATOR_CREATE_NEEDLE = "CREATE (o:Point {id:$id, is_operator:true"
_OPERATOR_EDGE_NEEDLE = "CREATE (s)-[:INPUT {idx:$i}]->(o)"
_OPERATOR_PROMOTE_NEEDLE = "MATCH (s:Point {id:$sid}) WHERE s.status IS NULL"
_MITIGATION_CREATE_NEEDLE = "CREATE (m:Point {id:$id, content:$c"
_MITIGATION_EDGE_NEEDLE = "CREATE (m)-[:IMPL]->(op), (op)-[:mitigated_by]->(m)"
_LINK_SOURCE_NEEDLE = "MERGE (n)-[r:extractedFrom]->(s)"


class _DeterministicGraph(_AbortOnceGraph):
    """Refuse every matching statement with a NON-transient ``ResponseError``.

    The negative control: the write retry's predicate must be an allow-list, so
    a deterministic error (``WRONGTYPE``) is re-raised on the FIRST attempt —
    never re-issued, never wrapped.
    """

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            raise redis_exc.ResponseError(
                "WRONGTYPE Operation against a key holding the wrong kind of value")
        return self._real.query(cypher, params=params, timeout=timeout)


def _abort_once(sdk, needle: str, *, present: bool = True):
    """Install the abort-once proxy on the SDK's live projection."""
    proj = sdk._get_proj()
    proxy = _AbortOnceGraph(proj.g, needle, present=present)
    proj.g = proxy
    return proj, proxy


# ── 6a. _mark_dirty — the EP dirty-persist SET ───────────────────────────────


def test_a_rebuild_abort_on_the_dirty_persist_set_is_retried(tmp_path):
    """``_mark_dirty``'s ``ep_dirty`` persist SET is retried.

    Before this wiring the SET was a bare ``proj.g.query``: a per-query abort
    raised out of ``_mark_dirty``, so the mutation the caller had just made was
    left with NO dirty marking — the next EP pass would not recompute the claim
    or its reverse-BFS neighbourhood, and the stored confidence would stay
    stale with nothing recording that the marking was dropped.
    """
    sdk = _sdk(tmp_path, "dirtypersist")
    try:
        pid = sdk.create_point(
            "statement", "a claim that must be marked dirty")["id"]
        proj, proxy = _abort_once(sdk, _MARK_DIRTY_PERSIST_NEEDLE)
        try:
            sdk._mark_dirty([pid])
        finally:
            proj.g = proxy._real
        assert proxy.matching >= 2, (
            "the aborted dirty-persist SET was never re-issued")
        assert proxy.applied == 1, (
            "exactly one application — an absolute SET must not double-apply")
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.ep_dirty, n.ep_dirty_at",
            params={"id": pid}).result_set
        assert rows and rows[0][0] is True, "the ep_dirty marking must land"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_dirty_clear_set_is_retried(tmp_path):
    """``_mark_dirty``'s terminal ``ep_dirty`` clear SET is retried.

    A point that went terminal before the persist is excluded by the SET's own
    WHERE, and the follow-up clear removes its live-era flag so it cannot strand
    in the dirty-root mirror forever. A dropped clear leaves the flag set and
    the terminal point permanently eligible as a dirty root.
    """
    sdk = _sdk(tmp_path, "dirtyclear")
    try:
        pid = sdk.create_point(
            "statement", "a claim that will go terminal")["id"]
        proj = sdk._get_proj()
        # Terminal AND flagged, so the persist WHERE excludes it and the CLEAR
        # path is the one that runs.
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.status = 'retracted', "
            "n.ep_dirty = true", params={"id": pid})
        proxy = _AbortOnceGraph(proj.g, _MARK_DIRTY_CLEAR_NEEDLE, present=True)
        proj.g = proxy
        try:
            sdk._mark_dirty([pid])
        finally:
            proj.g = proxy._real
        assert proxy.matching >= 2, (
            "the aborted dirty-clear SET was never re-issued")
        assert proxy.applied == 1, "exactly one application"
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.ep_dirty",
            params={"id": pid}).result_set
        assert rows and rows[0][0] is None, (
            "the terminal point's flag must be cleared after the retry")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_deleted_graph_abort_on_the_dirty_persist_set_stays_loud(tmp_path):
    """The #7685 loud arm, at the dirty-persist SET.

    With the graph key ABSENT the same abort means a plain deletion. Re-issuing
    would write into a graph FalkorDB auto-created EMPTY and report success
    against a graph the caller never wrote to (#6666 class), so the abort must
    surface unchanged and nothing may be reported as applied.
    """
    sdk = _sdk(tmp_path, "dirtydeleted")
    try:
        pid = sdk.create_point("statement", "a claim never marked")["id"]
        proj, proxy = _abort_once(
            sdk, _MARK_DIRTY_PERSIST_NEEDLE, present=False)
        try:
            with pytest.raises(redis_exc.ResponseError,
                               match="deleted or replaced"):
                sdk._mark_dirty([pid])
        finally:
            proj.g = proxy._real
        assert proxy.applied == 0, (
            "a deleted graph must not be re-issued into")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 6b. delete_point — the hard delete and the orphan-tag GC ─────────────────


def test_a_rebuild_abort_on_delete_point_is_retried(tmp_path):
    """``delete_point``'s DETACH DELETE is retried.

    A dropped hard delete leaves the claim LIVE on the map while the caller was
    told the delete failed — but the event and journal halves below it have
    already recorded a ``PointRetracted`` / ``EntityMutated(delete)``, so a
    later replay hard-deletes a Point the live graph still answers for. The two
    stores disagree, and the live one is the stale half.
    """
    sdk = _sdk(tmp_path, "deletept")
    try:
        pid = sdk.create_point("statement", "a claim to be deleted")["id"]
        proj, proxy = _abort_once(sdk, _DELETE_POINT_NEEDLE)
        try:
            found = sdk.delete_point(pid)
        finally:
            proj.g = proxy._real
        assert found is True, "the delete must report the point as found"
        assert proxy.matching >= 2, "the aborted DELETE was never re-issued"
        assert proxy.applied == 1, (
            "exactly one application — re-deleting an absent node is a no-op")
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN count(n)",
            params={"id": pid}).result_set
        assert rows and rows[0][0] == 0, "the point must be gone after the retry"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_orphan_tag_gc_is_retried(tmp_path):
    """``delete_point``'s orphan-tag GC is retried.

    The sweep removes the ``:Tag`` nodes a delete left with no incoming
    ``TAGGED`` edge. A dropped sweep leaves orphan tags accumulating in
    ``list_tags`` — the exact residue #485 added the GC to prevent.
    """
    sdk = _sdk(tmp_path, "taggc")
    try:
        pid = sdk.create_point(
            "statement", "a tagged claim", tags=["alpha"])["id"]
        proj = sdk._get_proj()
        before = proj.g.query(
            "MATCH (t:Tag {name:'alpha'}) RETURN count(t)").result_set
        assert before and before[0][0] == 1, (
            "the fixture must mint the :Tag so the GC is reachable")
        proxy = _AbortOnceGraph(proj.g, _TAG_GC_NEEDLE, present=True)
        proj.g = proxy
        try:
            found = sdk.delete_point(pid)
        finally:
            proj.g = proxy._real
        assert found is True
        assert proxy.matching >= 2, (
            "the aborted tag GC was never re-issued — the fixture point's "
            "TAGGED edge did not reach the sweep")
        assert proxy.applied == 1, "exactly one application"
        after = proj.g.query(
            "MATCH (t:Tag {name:'alpha'}) RETURN count(t)").result_set
        assert after and after[0][0] == 0, (
            "the orphan tag must be swept after the retry")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_deterministic_error_on_delete_point_is_not_retried(tmp_path):
    """The negative control: a non-abort error is never re-issued.

    ``WRONGTYPE`` is not an engine abort and does not prove the statement failed
    to land, so the allow-list predicate refuses it and the error surfaces on
    the FIRST attempt. This is what keeps the retry from becoming a
    retry-everything loop.
    """
    sdk = _sdk(tmp_path, "deletewrong")
    try:
        pid = sdk.create_point("statement", "a claim with a wrongtype")["id"]
        proj = sdk._get_proj()
        proxy = _DeterministicGraph(proj.g, _DELETE_POINT_NEEDLE)
        proj.g = proxy
        try:
            with pytest.raises(redis_exc.ResponseError, match="WRONGTYPE"):
                sdk.delete_point(pid)
        finally:
            proj.g = proxy._real
        assert proxy.matching == 1, (
            "a deterministic error must be attempted exactly once, never "
            "re-issued")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 6c. promote_point — the CAS promotion SET ────────────────────────────────


def test_a_rebuild_abort_on_the_promote_cas_is_retried(tmp_path):
    """``promote_point``'s CAS ``status draft → live`` SET is retried.

    A dropped CAS leaves the claim draft while the caller is told the promotion
    failed — but EP's draft filter excludes it, so the claim the reviewer
    approved conveys nothing to the graph. Re-issuing is safe because an engine
    ABORT means the CAS did not land, so the ``status = 'draft'`` predicate is
    re-evaluated against unchanged state and matches exactly once.
    """
    sdk = _sdk(tmp_path, "promote")
    try:
        pid = sdk.create_point(
            "statement", "a draft claim awaiting review", status="draft")["id"]
        proj, proxy = _abort_once(sdk, _PROMOTE_CAS_NEEDLE)
        try:
            result = sdk.promote_point(pid)
        finally:
            proj.g = proxy._real
        assert result.get("promoted") is True, result
        assert proxy.matching >= 2, "the aborted CAS was never re-issued"
        assert proxy.applied == 1, "exactly one application — no double CAS"
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status",
            params={"id": pid}).result_set
        assert rows and rows[0][0] == "live", "the promotion must land"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 6d. create_operator — the node, its edges, and the source promote ────────


def _two_points(sdk, tag: str) -> tuple[str, str]:
    a = sdk.create_point("statement", f"{tag} source claim")["id"]
    b = sdk.create_point("statement", f"{tag} target claim")["id"]
    return a, b


def test_a_rebuild_abort_on_the_operator_create_is_retried(tmp_path):
    """``create_operator``'s bare ``CREATE (o:Point)`` is retried.

    The operator is the bridge every EP message travels over: a dropped create
    leaves the two claims unconnected while the caller's response carries no
    operator at all — and the edge writes below it never run. The id is minted
    once, so a re-issue names the same node; admissible ONLY on
    ``retryable_aborted_write``, which proves the statement did not land.
    """
    sdk = _sdk(tmp_path, "opcreate")
    try:
        a, b = _two_points(sdk, "opcreate")
        proj, proxy = _abort_once(sdk, _OPERATOR_CREATE_NEEDLE)
        try:
            result = sdk.create_operator("IMPL", a, [b])
        finally:
            proj.g = proxy._real
        op_id = result.get("id")
        assert op_id, result
        assert proxy.matching >= 2, (
            "the aborted operator CREATE was never re-issued")
        assert proxy.applied == 1, (
            "exactly one application — a re-issued bare CREATE must not mint "
            "a second operator")
        rows = proj.g.query(
            "MATCH (o:Point {id:$id}) RETURN count(o)",
            params={"id": op_id}).result_set
        assert rows and rows[0][0] == 1, (
            "exactly one operator node must exist after the retry")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_operator_edges_is_retried(tmp_path):
    """``create_operator``'s typed-edge + ``INPUT`` mirror statement is retried.

    One statement writes BOTH halves the factor graph needs (the typed edge and
    its reverse ``INPUT`` mirror). A dropped pair leaves an operator with no
    inputs — EP cannot derive a factor from it, and the operator's own
    creation-time ``ep_dirty`` flag masks the loss live while a rebuilt graph
    has no mask at all.
    """
    sdk = _sdk(tmp_path, "opedge")
    try:
        a, b = _two_points(sdk, "opedge")
        proj, proxy = _abort_once(sdk, _OPERATOR_EDGE_NEEDLE)
        try:
            result = sdk.create_operator("IMPL", a, [b])
        finally:
            proj.g = proxy._real
        op_id = result.get("id")
        # Two endpoints ⇒ the statement runs once per input (source + target),
        # so the abort on the FIRST is re-issued and the SECOND applies: 3
        # matches (2 statements + 1 re-issue) and 2 applications. The per-
        # endpoint count assertions below are what prove the re-issue did not
        # DOUBLE either edge.
        assert proxy.matching >= 3, (
            "the aborted edge statement was never re-issued")
        assert proxy.applied == 2, (
            "exactly one application per endpoint — no doubled edge")
        rows = proj.g.query(
            "MATCH (o:Point {id:$o})-[:IMPL]->(a:Point {id:$a}) "
            "RETURN count(*)",
            params={"a": a, "o": op_id}).result_set
        assert rows and rows[0][0] == 1, "the typed edge must exist exactly once"
        mirror = proj.g.query(
            "MATCH (b:Point {id:$b})-[:INPUT]->(o:Point {id:$o}) "
            "RETURN count(*)",
            params={"b": b, "o": op_id}).result_set
        assert mirror and mirror[0][0] == 1, (
            "the INPUT mirror must exist exactly once")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_operator_source_promote_is_retried(tmp_path):
    """``create_operator``'s never-set-status source promote SET is retried.

    Legacy graphs carry status-less Points; the first operator edge fills that
    status in with the live default. A dropped SET leaves the source status-less,
    where the strict grounding selector excludes it while a rebuilt one (whose
    fold coalesces to 'live') includes it — the rebuild alone changes the
    operator's participation (#5089).
    """
    sdk = _sdk(tmp_path, "oppromote")
    try:
        a, b = _two_points(sdk, "oppromote")
        proj = sdk._get_proj()
        # The legacy shape this clause exists for: a source with NO status.
        proj.g.query("MATCH (n:Point {id:$id}) REMOVE n.status",
                     params={"id": a})
        proxy = _AbortOnceGraph(proj.g, _OPERATOR_PROMOTE_NEEDLE, present=True)
        proj.g = proxy
        try:
            sdk.create_operator("IMPL", a, [b])
        finally:
            proj.g = proxy._real
        assert proxy.matching >= 2, (
            "the aborted source-promote SET was never re-issued")
        assert proxy.applied == 1, "exactly one application"
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status",
            params={"id": a}).result_set
        assert rows and rows[0][0] == "live", (
            "the status-less source must be promoted to live")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 6e. mitigate_operator — the mitigation Point and its edge pair ───────────


def _operator(sdk, tag: str) -> str:
    a, b = _two_points(sdk, tag)
    return sdk.create_operator("IMPL", a, [b])["id"]


def test_a_rebuild_abort_on_the_mitigation_create_is_retried(tmp_path):
    """``mitigate_operator``'s bare ``CREATE (m:Point)`` is retried.

    A dropped create loses the dampener entirely while the caller is told the
    mitigation failed — and because the id is minted once, a re-issue names the
    same node (bare CREATE ⇒ ``retryable_aborted_write`` only). A duplicate here
    would DOUBLE the dampening, which is the failure the idempotency argument
    exists to exclude.
    """
    sdk = _sdk(tmp_path, "mitcreate")
    try:
        op_id = _operator(sdk, "mitcreate")
        proj, proxy = _abort_once(sdk, _MITIGATION_CREATE_NEEDLE)
        try:
            result = sdk.mitigate_operator(op_id, "weak evidence", strength=0.3)
        finally:
            proj.g = proxy._real
        mid = result.get("id")
        assert mid, result
        assert proxy.matching >= 2, (
            "the aborted mitigation CREATE was never re-issued")
        assert proxy.applied == 1, (
            "exactly one application — a re-issued bare CREATE must not mint "
            "a second mitigation Point")
        rows = proj.g.query(
            "MATCH (m:Point {id:$id}) RETURN count(m)",
            params={"id": mid}).result_set
        assert rows and rows[0][0] == 1, (
            "exactly one mitigation Point must exist after the retry")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_mitigation_edges_is_retried(tmp_path):
    """``mitigate_operator``'s IMPL / ``mitigated_by`` edge pair is retried.

    The pair is what makes the mitigation reachable from the operator it damps
    (``compute_operator_weight`` walks ``(op)-[:mitigated_by]->(m)``). A dropped
    pair leaves a mitigation Point connected to nothing — recorded, but inert.
    """
    sdk = _sdk(tmp_path, "mitedge")
    try:
        op_id = _operator(sdk, "mitedge")
        proj, proxy = _abort_once(sdk, _MITIGATION_EDGE_NEEDLE)
        try:
            result = sdk.mitigate_operator(op_id, "weak evidence", strength=0.3)
        finally:
            proj.g = proxy._real
        mid = result.get("id")
        assert proxy.matching >= 2, (
            "the aborted mitigation edge pair was never re-issued")
        assert proxy.applied == 1, "exactly one application — no doubled edge"
        rows = proj.g.query(
            "MATCH (op:Point {id:$op})-[:mitigated_by]->(m:Point {id:$mid}) "
            "RETURN count(*)",
            params={"op": op_id, "mid": mid}).result_set
        assert rows and rows[0][0] == 1, (
            "the mitigated_by edge must exist exactly once")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 6f. the extractedFrom legs of the entity and document doors ──────────────


def test_a_rebuild_abort_on_the_create_entity_link_source_is_retried(tmp_path):
    """``create_entity``'s document ``_link_source`` leg is retried (#8171).

    The entity itself lands, so a dropped provenance edge leaves a document the
    auditor reads as a provenance GAP: the ``extractedFrom`` edge is the only
    thing connecting it to its source. ``_link_source`` MERGEs the Source stub
    on its url and the edge on the (node, Source) pair, so a re-issue
    converges — the same funnel ``create_point`` already rides.
    """
    sdk = _sdk(tmp_path, "entlink")
    try:
        proj, proxy = _abort_once(sdk, _LINK_SOURCE_NEEDLE)
        try:
            result = sdk.create_entity(
                "document", "A Document", documentKind="report",
                extractedFrom="doc://entity")
        finally:
            proj.g = proxy._real
        node = result.get("node") or {}
        did = node.get("id") or node.get("eventId")
        assert did, result
        assert proxy.matching >= 2, (
            "the aborted extractedFrom MERGE was never re-issued")
        assert proxy.applied == 1, "exactly one application — MERGE must not double"
        rows = proj.g.query(
            "MATCH (n:Source {url:$url})<-[r:extractedFrom]-(d:Source {id:$id}) "
            "RETURN count(r)",
            params={"url": "doc://entity", "id": did}).result_set
        assert rows and rows[0][0] == 1, (
            "exactly one extractedFrom edge to exactly one Source must exist")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_rebuild_abort_on_the_create_document_link_source_is_retried(tmp_path):
    """``create_document``'s ``_link_source`` leg is retried (#394).

    Parity with ``create_entity``'s document leg: same MERGE-idempotent writer,
    same bounded retry, so the two doors cannot disagree about whether a
    dropped abort costs the provenance edge.
    """
    sdk = _sdk(tmp_path, "doclink")
    try:
        proj, proxy = _abort_once(sdk, _LINK_SOURCE_NEEDLE)
        try:
            result = sdk.create_document(
                "A Document", "report", extractedFrom="doc://document")
        finally:
            proj.g = proxy._real
        assert result, result
        assert proxy.matching >= 2, (
            "the aborted extractedFrom MERGE was never re-issued")
        assert proxy.applied == 1, "exactly one application"
        # Address the edge by the SOURCE URL (the document's own id spelling is
        # the door's, not this test's) and prove exactly one edge from exactly
        # one document.
        rows = proj.g.query(
            "MATCH (n:Source {url:$url})<-[r:extractedFrom]-(d) "
            "RETURN count(r), count(distinct d)",
            params={"url": "doc://document"}).result_set
        assert rows and rows[0][0] == 1 and rows[0][1] == 1, (
            "exactly one extractedFrom edge to exactly one Source must exist")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


# ── 7. #7357 — an exhausted graph swap must not READ as data loss ────────────
#
# The engine's sentence for the graph-abort family is one string for several
# realities ("graph was deleted or replaced while the query was running"), and
# on the measured outages (#7357) the common reality was a SWAP the retry could
# not outlast — a restart/reload, a rebuild, or the engine evicting the graph
# from its 25-entry cache — while the graph key was still present and the data
# intact. Read raw, that sentence sends a caller hunting a rebuild on a healthy
# graph (measured: a lane asked the owner to authorise a destructive rebuild on
# the strength of it). These tests pin the appended diagnosis, its allow-list,
# and — the safety half — that a DELETION is never told a retry is safe.
#
# What the diagnosis may CLAIM is bounded by its evidence, which is one
# `EXISTS <name>`: PRESENCE, not identity and not integrity. So the note says
# the key was not left deleted and that a re-issue cannot duplicate — it never
# reassures the caller about its data (a rebuild REPLACES the contents, and it
# is one of the causes the same sentence names), and it never settles WHY an
# absent key is absent (a deletion and a rebuild inside its delete window both
# read 0). The pre-fix wording broke all three; the assertions below fail on it.


def _explain(exc, present):
    from tortoise.retry import explain_graph_abort
    return explain_graph_abort(exc, graph_present=present, attempts=4)


def test_the_swap_diagnosis_keeps_the_engine_sentence_first_and_classifiable():
    """The suffix is APPENDED, so the ``\\A``-anchored classifier still fires.

    The retry predicate, ``graph_abort_family`` and the SDK's own re-issue gate
    all read the message through ``_abort_arm``, whose patterns are anchored at
    position 0. A prefix (or a rewrite) would silently un-classify the error —
    and a caller that re-raises an explained error through another retry layer
    would then re-issue a bare ``CREATE`` it may not re-issue.
    """
    from tortoise.retry import graph_abort_family, retryable_aborted_write

    explained = _explain(redis_exc.ResponseError(_DELETED_OR_REPLACED), True)
    assert explained is not None, "the graph-swap family must be explained"
    assert explained.startswith(_DELETED_OR_REPLACED), (
        "the engine's own sentence must stay at position 0")
    assert "graph key is still present" in explained, (
        "the note must say the graph was not deleted")
    assert "retrying is safe" in explained

    # PRESENCE is not IDENTITY and not INTEGRITY: `EXISTS 1` is exactly what a
    # completed deletion + recreate (a rebuild) produces, and a rebuild is one
    # of the mechanisms this very sentence lists — so the note must not infer
    # "nothing was deleted" (the #7357 overclaim, sign flipped), and must warn
    # that the contents may have been replaced instead of reassuring the caller
    # about its data.
    assert "nothing was deleted" not in explained, (
        "EXISTS proves the key, not the contents — do not claim nothing was "
        "deleted when a rebuild REPLACES the graph")
    assert "REPLACES" in explained and "verify, do not assume" in explained, (
        "a rebuild is one of the named causes, so the data may be gone")
    assert "presence is not writability" in explained.lower(), (
        "a retry can still be refused — presence does not make it succeed")

    again = redis_exc.ResponseError(explained)
    assert graph_abort_family(again) is True, (
        "an explained abort must still classify into its own family")
    assert retryable_aborted_write(again, graph_exists=lambda: True) is True


def test_the_swap_diagnosis_is_an_allow_list_and_is_applied_once():
    """Only the graph-swap family is rewritten, and only once.

    ``WRONGTYPE`` and a transport timeout are DIFFERENT realities (a
    deterministic bug; a lost reply) whose own text is already accurate, and two
    nested retry layers must not grow two suffixes.
    """
    assert _explain(
        redis_exc.ResponseError(
            "WRONGTYPE Operation against a key holding the wrong kind of value"),
        True) is None
    assert _explain(
        redis_exc.TimeoutError("Timeout reading from socket"), True) is None
    assert _explain(OSError("boom"), True) is None

    once = _explain(redis_exc.ResponseError(_DELETED_OR_REPLACED), True)
    assert once is not None
    assert _explain(redis_exc.ResponseError(once), True) is None, (
        "an already-explained abort must never be explained again")


def test_the_swap_diagnosis_never_promises_a_safe_retry_on_a_gone_or_unknown_graph():
    """The two branches where "your data is intact — retry" would be a LIE.

    An ABSENT key is a deletion OR a rebuild still inside its delete window,
    and either way a re-issued bare ``CREATE`` lands in a graph FalkorDB
    auto-creates EMPTY, which is the #6666 silent-success class — so the note
    must refuse the retry. An UNANSWERABLE probe is an unresponsive instance, a
    missing projection, or an unresolved graph name: claiming either outcome
    would be a guess.
    """
    absent = _explain(redis_exc.ResponseError(_DELETED_OR_REPLACED), False)
    assert absent is not None
    assert "ABSENT" in absent and "deletion" in absent
    assert "retrying is safe" not in absent, (
        "a deleted graph must never be advertised as safe to retry")
    assert "Restore the graph" in absent
    # An absence is a deletion OR a slower rebuild still inside its delete
    # window (retryable_aborted_write says so itself), and the probe cannot
    # tell them apart — so the note must not settle which, and its action must
    # fit both.
    assert "rather than a replacement" not in absent, (
        "absence does not prove a deletion; a rebuild inside its delete "
        "window reads 0 too")
    assert "delete window" in absent and "replaying" in absent, (
        "name the rebuild case and an action that fits it")

    unknown = _explain(redis_exc.ResponseError(_DELETED_OR_REPLACED), None)
    assert unknown is not None
    assert "UNKNOWN" in unknown, "an unanswerable probe must not become a guess"
    # `None` covers an unresponsive instance, an unresolved graph name and a
    # missing projection alike, and no amount of server-checking fixes the last
    # one — so the note must name ALL THREE as the reasons the state is unknown.
    # Naming only the instance, or negating that one phrasing, is not enough:
    # the previous wording said "Check that the instance is answering and that
    # the graph name resolved", which drops the missing-projection cause and
    # sends the reader to check a healthy server (#8954).
    for cause in ("connection", "graph name", "projection"):
        assert cause in unknown, (
            f"the UNKNOWN note must name {cause!r} as one of the causes it "
            "cannot distinguish; naming a single one points the reader at "
            "the wrong thing")
    for single_cause_directive in (
            "Check that the instance is answering",
            "the instance is not answering",
            "the instance is unresponsive"):
        assert single_cause_directive not in unknown, (
            "the instance must not be named as THE cause: `_graph_presence` "
            "answers None for a wiring gap too")
    assert "could not be probed" in unknown
    assert "retrying is safe" not in unknown
    assert "restore" not in unknown.lower()


def test_an_exhausted_swap_surfaces_the_legible_diagnosis_with_the_same_type(
        tmp_path, monkeypatch):
    """End to end through the SDK write funnel, with the graph still PRESENT.

    This is the measured shape: the graph exists, its data is intact, and the
    write refuses repeatedly. The caller must receive the engine's own error
    TYPE (``_classify_db_failure`` buckets by type) carrying a message that
    names the swap and says a retry is safe.
    """
    import types

    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "legible")
    try:
        pid = sdk.create_point("evidence", "a claim to delete")["id"]
        proj, proxy = _abort_once(sdk, _DELETE_POINT_NEEDLE)
        proxy._aborts = 99  # never heals — the exhaustion arm
        try:
            with pytest.raises(redis_exc.ResponseError) as caught:
                sdk.delete_point(pid)
        finally:
            proj.g = proxy._real
        text = str(caught.value)
        from tortoise.retry import _LEGIBLE_OPENER
        assert text.startswith(_DELETED_OR_REPLACED), (
            "the surfaced error must still BE the engine's abort, not a wrapper")
        assert _LEGIBLE_OPENER in text, (
            "an exhausted graph swap must not surface the bare engine sentence")
        assert "graph key is still present" in text
        assert "retrying is safe" in text
        assert proxy.matching >= 3, (
            "the diagnosis belongs to the EXHAUSTED arm, after re-issues")
        # The suffix must not move the error's CLASS: `_classify_db_failure`
        # buckets by TYPE, so the surfaced error must still be EXACTLY the
        # engine's `ResponseError` — `pytest.raises` above would also accept a
        # subclass wrapping the diagnosis, which is the shape this guards.
        assert type(caught.value) is redis_exc.ResponseError, (
            "the diagnosis must ride the SAME exception object, never a "
            "wrapper type")
        from tortoise.sdk import _classify_db_failure
        assert _classify_db_failure(caught.value) == "db"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


class _PresentThenGone(_AbortOnceGraph):
    """Present while the retry LOOP runs, ABSENT once it has given up.

    Models a deletion landing inside the retry backoff: every predicate probe
    inside ``call_with_predicate`` sees ``EXISTS 1`` (so the re-issues are
    authorised), and the probe the EXHAUSTION arm pays immediately afterwards
    sees ``0``. The two are told apart by ``retrying()`` — a flag the test
    wraps around ``call_with_predicate`` — not by counting probes, because the
    write path pays ``EXISTS`` for its own reasons too.
    """

    def __init__(self, real, needle: str, *, retrying) -> None:
        super().__init__(real, needle, aborts=99)  # never heals
        self._retrying = retrying
        self.absent_answers = 0

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            if self._retrying():
                return 1
            self.absent_answers += 1
            return 0
        return super().execute_command(*a, **k)


def test_an_absent_key_at_exhaustion_still_gets_the_diagnosis(
        tmp_path, monkeypatch):
    """The suffix belongs to the FAMILY, not to the "still present" answer.

    ``_graph_presence`` has three answers and the exhaustion arm explains all
    three. The measured shape here is a deletion landing INSIDE the backoff: the
    key is present for every predicate probe (so the re-issues are authorised)
    and gone by the time the budget is spent. That path must still surface the
    diagnosis — an absent key is never told "retrying is safe", but it must not
    silently fall back to the BARE engine sentence either, because that sentence
    is what sent the measured lanes hunting a destructive rebuild (#7357).

    This is the behaviour ``_WriteNotLanded``'s docstring used to misdescribe,
    claiming the suffix is appended only "when the probe finds the graph key
    still present".
    """
    import types

    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    import tortoise.retry as retry_mod

    retrying = {"on": False}
    real_retry = retry_mod.call_with_predicate

    def _watched_retry(fn, **kwargs):
        retrying["on"] = True
        try:
            return real_retry(fn, **kwargs)
        finally:
            retrying["on"] = False

    # ``_graph_write_with_retry`` imports the helper at CALL time, so patching
    # the module attribute is what the retry it runs will pick up.
    monkeypatch.setattr(retry_mod, "call_with_predicate", _watched_retry)
    sdk = _sdk(tmp_path, "legiblegone")
    try:
        pid = sdk.create_point("evidence", "a claim to delete")["id"]
        proj = sdk._get_proj()
        proxy = _PresentThenGone(
            proj.g, _DELETE_POINT_NEEDLE, retrying=lambda: retrying["on"])
        proj.g = proxy
        try:
            with pytest.raises(redis_exc.ResponseError) as caught:
                sdk.delete_point(pid)
        finally:
            proj.g = proxy._real
        text = str(caught.value)
        from tortoise.retry import _LEGIBLE_OPENER
        assert text.startswith(_DELETED_OR_REPLACED), (
            "the surfaced error must still BE the engine's abort")
        assert _LEGIBLE_OPENER in text, (
            "an absent key at EXHAUSTION must still be explained; only a "
            "deletion the predicate sees at classification time stays bare")
        assert "ABSENT" in text and "retrying is safe" not in text, (
            "the absent arm must describe itself and refuse the safe-retry "
            "claim")
        assert proxy.absent_answers >= 1, (
            "the flip must have engaged: the probe paid AFTER the retry loop "
            "answers ABSENT, which is the answer this test needs")
        assert proxy.matching > 1, "the re-issues must actually have run"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_deletion_abort_is_never_given_the_retry_safety_note(tmp_path):
    """The loud arm's message is untouched: it is ALREADY accurate.

    An absent key at classification time is a deletion — the predicate refuses
    before this arm — and here the engine's sentence is literally true. The
    predicate refuses on the FIRST abort, so no diagnosis may be appended and
    nothing may claim a retry is safe.
    """
    sdk = _sdk(tmp_path, "legibledeleted")
    try:
        pid = sdk.create_point("evidence", "a claim to delete")["id"]
        proj, proxy = _abort_once(sdk, _DELETE_POINT_NEEDLE, present=False)
        try:
            with pytest.raises(redis_exc.ResponseError) as caught:
                sdk.delete_point(pid)
        finally:
            proj.g = proxy._real
        assert str(caught.value) == _DELETED_OR_REPLACED, (
            "a deletion must surface the engine sentence untouched")
        assert proxy.applied == 0, "a deleted graph must not be re-issued into"
    finally:
        with contextlib.suppress(Exception):
            sdk.close()


def test_the_not_landed_sentinel_documents_the_message_may_change():
    """The sentinel's OWN docstring must not promise an untouched message.

    ``_WriteNotLanded``'s docstring is the first place a lane looks when
    reasoning about what an exhausted re-raise carries, and it said the
    ORIGINAL abort is "re-raised unchanged" — which the suffixed graph-swap
    message above contradicts. A test written from that promise
    (``assert str(exc) == _DELETED_OR_REPLACED``) is exactly what the SDK-path
    test asserts against, so the two records must agree: TYPE is what
    exhaustion preserves, the MESSAGE may gain the #7357 diagnosis.
    """
    from tortoise.sdk import _WriteNotLanded

    doc = _WriteNotLanded.__doc__ or ""
    assert "re-raised unchanged" not in doc, (
        "the sentinel must not promise an unchanged message; the graph-swap "
        "family's re-raise gains the #7357 suffix")
    assert "TYPE unchanged" in doc, (
        "the TYPE guarantee is the one exhaustion actually keeps — state it")
    assert "MESSAGE is NOT guaranteed untouched" in doc
    assert "explain_graph_abort" in doc, (
        "name the builder that appends the suffix, so a reader can follow it")
    assert "still present" not in doc, (
        "the suffix is appended for EVERY probe answer (present / absent / "
        "unprobeable), so the docstring must not narrow it to the present "
        "branch — that narrowing is what made the misdescription invisible "
        "to the substring assertions above (#8954)")


_WRITE_SLOT_ABORT = "another write is in progress, retry the query"


class _SlotAbortGraph:
    """Refuse every matching statement with the STATE-INDEPENDENT write-slot abort.

    Ordinary multi-writer contention says nothing about whether the graph key
    exists, so nothing on this path has a use for ``EXISTS``. ``exists_calls``
    counts the probes the SDK actually pays, and the probe RAISES so a caller
    that pays it anyway is caught twice: by the count, and by the
    ``graph-existence probe failed`` warning it logs.
    """

    def __init__(self, real, needle: str) -> None:
        self._real = real
        self._needle = needle
        self.matching = 0
        self.exists_calls = 0

    def query(self, cypher, params=None, timeout=None):
        if self._needle in cypher:
            self.matching += 1
            raise redis_exc.ResponseError(_WRITE_SLOT_ABORT)
        return self._real.query(cypher, params=params, timeout=timeout)

    def execute_command(self, *a, **k):
        if a and a[0] == "EXISTS":
            self.exists_calls += 1
            raise RuntimeError("EXISTS probe paid on a state-independent path")
        return self._real.execute_command(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_write_slot_exhaustion_pays_no_graph_presence_probe(
        tmp_path, monkeypatch, caplog):
    """A state-independent exhaustion must not spend an ``EXISTS`` round trip.

    The presence probe answers a question only the graph-swap family asks
    ("deleted, or swapped?"). Evaluating it EAGERLY as an argument made every
    exhausted write pay one round trip on the error path and discard the
    answer — and if the instance happens to be unresponsive at that moment, it
    logged a ``graph-existence probe failed`` warning for a datum nothing on
    this path consumes. The probe is now gated on the family, so contention
    exhaustion stays exactly as cheap as it was before #7357.
    """
    import logging
    import types

    monkeypatch.setattr("tortoise.retry.time",
                        types.SimpleNamespace(sleep=lambda _s: None))
    sdk = _sdk(tmp_path, "slotexhaust")
    try:
        pid = sdk.create_point("evidence", "a claim to delete")["id"]
        proj = sdk._get_proj()
        proxy = _SlotAbortGraph(proj.g, _DELETE_POINT_NEEDLE)
        proj.g = proxy
        try:
            with (caplog.at_level(logging.WARNING),
                  pytest.raises(redis_exc.ResponseError) as caught):
                sdk.delete_point(pid)
        finally:
            proj.g = proxy._real
        assert proxy.matching >= 3, (
            "the write-slot abort is state-independent and must still be "
            "re-issued until the budget is spent")
        assert str(caught.value) == _WRITE_SLOT_ABORT, (
            "a state-independent exhaustion surfaces the bare engine refusal — "
            "no diagnosis is written for an arm that has nothing to diagnose")
        assert proxy.exists_calls == 0, (
            "the presence probe must not be paid for by a write-slot "
            "exhaustion: its answer is discarded")
        assert "graph-existence probe failed" not in caplog.text, (
            "a probe nothing consumes must not emit a failure warning")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
