"""#4240 — the hosted lane must JOURNAL its ``aboutObject`` edges.

The defect (pre-fix): ``hosted_api._make_sdk`` / ``_data_sdk`` built every
hosted SDK with **no** ``event_log_path``, so the JSONL-only reassembly records
(``EntityLinked``, ``SessionRecorded``, ``ObjectRegistered``, ``PointAdded``)
were silent no-ops. The hosted capture's entity attachment was therefore
**live-only**: ``rebuild_all`` / ``rebuild`` / ``recover_from_log`` had nothing
to replay and the ``(Session|Point)-[:aboutObject]->(Object)`` edges vanished
while the rebuild reported success (the #2296 hazard class).

These tests pin the two named hosted call sites:
  * the SDK-construction seam both sites share (``_make_sdk``), and
  * ``_relink_sessions_after_index`` (the index-completion re-link).

They also pin the two review findings on the wiring:

* **F2** — the journal key must be the graph the SDK ACTUALLY opens. The
  derivation lives in ``sdk._derive_graph_name`` (ONE source of truth); a local
  re-derivation in the resolver diverged on the hyphenated ``test-*`` branch
  (``test-foo`` opens ``test_foo_tortoise``, not ``org_test-foo``), so
  ``test_journal_key_matches_the_graph_the_sdk_opens`` asserts the two agree,
  and ``test_no_journal_key_can_escape_the_base_dir`` pins the traversal guard.
* **F1** — a configured journal whose append fails is not silent. ⛔ The
  **SDK-lane** leg that pinned it (``test_unwritable_journal_is_disclosed_and_counted``)
  was **DELETED WITH ITS DRIVER** (`#4282`, 2026-10-27: it read the deleted
  ``capture_session``'s receipt — ``status == "partial"``, ``points``,
  ``warnings`` — wrapper-level semantics a test cannot restate without
  re-implementing the method). The mechanism is still pinned, on the HOSTED
  door, by ``test_hosted_receipt_discloses_a_failed_journal_append`` below — but
  only the RECEIPT half: the operator counter
  (``monitoring.journal_write_failure_count``) has no test reference left, so the
  counter half is **unwatched**. The append stays fail-soft by design (the graph
  mutation already succeeded), so ``ok`` remains True — flipping it would trip
  the #2335 TRUE-retry gate over a persisted write.
* **F2 (cycle 2)** — the same disclosure must reach the HOSTED receipt, not just
  the SDK lane: ``test_hosted_receipt_discloses_a_failed_journal_append`` drives
  ``_capture_session_impl`` itself (it builds its own receipt and never calls
  ``sdk.capture_session``), asserts the warning is present, and asserts the
  outcome is unchanged (the envelope's ``status`` is ``"ok"`` — the hosted
  receipt's success signal — independent of the ``_capture_ok_record`` written
  earlier).
* **#5632** — the count disclosed on the receipt is THIS capture's DELTA, never
  the SDK's LIFETIME counter. ⛔ The **SDK-lane** half
  (``test_capture_after_a_failed_non_capture_write_reports_no_journal_warning``)
  was **DELETED WITH ITS DRIVER** (`#4282`) for the same receipt reason as F1's,
  and only its **hosted twin**
  (``test_hosted_capture_after_a_failed_non_capture_write_reports_no_warning``,
  below) survives. A lifetime read brands every later capture live-only
  forever.

Class-B doctrine — every test states (1) the value/state that makes it fail and
(2) that the state is reachable in its fixture:

* ``test_make_sdk_wires_the_journal_when_the_base_dir_is_set`` — FAILS while
  ``_event_log_path is None``; the fixture sets ``TORTOISE_EVENT_LOG_BASE_DIR``
  (reachable: it is the documented switch).
* ``test_journal_key_matches_the_graph_the_sdk_opens`` — FAILS while the
  resolver derives its own key (the pre-F2 state: ``org_test-…`` vs
  ``test_…_tortoise``); reachable: the fixture namespace is hyphenated.
* ``test_no_journal_key_can_escape_the_base_dir`` — FAILS while a ``..`` key is
  accepted; reachable: the pre-F2 guard kept ``.``.
* ⛔ ``test_capture_aboutobject_edges_survive_rebuild_all`` — **DELETED WITH ITS
  DRIVER** (`#4282`). Its subject was the **Session** pass's
  ``(Session)-[:aboutObject]->(Object)`` edge surviving ``rebuild_all``, and that
  edge is produced by the deleted wrapper's session-link step; the surviving
  primitives emit the **Point** edge only (measured: replaying
  ``_write_session_and_turns`` → mint → ``_materialize_session_source`` →
  ``_extract_session_v2`` yields ``(('Point',), <pt>, <object>)`` and no Session
  source) — the wrapper was the only SDK call site that invoked the linker
  (``session_link.link_session_entities``, which SURVIVES and is still called by
  the hosted lane at ``hosted_api.py``), so with the SDK door gone no SDK-lane
  capture can produce that edge. The rebuild-survival of Session edges is still
  pinned, by ``test_relink_aboutobject_edge_survives_rebuild_all`` below (which
  asserts ``("Session",)`` in the live edge set and then
  ``_about_edges(proj) == live`` after ``rebuild_all``).
* ``test_relink_aboutobject_edge_survives_rebuild_all`` — FAILS while the
  re-link's ``EntityLinked`` record is not written; the fixture captures BEFORE
  the Object exists (honest no-match) then materializes it, so the re-link
  really creates an edge (reachable: pinned on the live count). Same honest-RED
  note as above.
* ``test_live_only_capture_loses_aboutobject_edges_without_the_journal`` — the
  ONE test whose RED is the drift itself: it mutates only the wiring (the
  pre-#4240 journal-less construction) and shows the live-only edges do NOT
  survive ``rebuild_all``.
* ⛔ ``test_unwritable_journal_is_disclosed_and_counted`` — **DELETED WITH ITS
  DRIVER** (`#4282`): it read the deleted wrapper's receipt
  (``status == "partial"``, ``points``, ``warnings``). Its hosted twin below
  keeps the mechanism pinned; the SDK-lane receipt shape is unwatched.
* ``test_hosted_receipt_discloses_a_failed_journal_append`` — FAILS while the
  HOSTED receipt omits the disclosure (the pre-cycle-2 state: only the SDK lane
  carried it); reachable: the hosted impl is driven directly against a mode-0500
  journal base dir, and the envelope's ``status`` is asserted unchanged
  (``"ok"`` — the hosted receipt's success signal) alongside it.
* ⛔ ``test_capture_after_a_failed_non_capture_write_reports_no_journal_warning``
  — **DELETED WITH ITS DRIVER** (`#4282`), same receipt reason. Its hosted twin
  below fails a non-capture append at the SDK-open seam and asserts NO journal
  warning, so the DELTA-vs-LIFETIME distinction is still exercised — on the
  hosted receipt, not the SDK one.
* ``test_hosted_capture_after_a_failed_non_capture_write_reports_no_warning``
  — the hosted byte-parity twin (same mutation, same reachable fixture) — the
  capture's fresh SDK is seeded with one real failed non-capture append at the
  SDK-open seam, which is the state a polluted lifetime counter produces.

The journal is a DOMAIN EVENT LOG, never the durability mechanism
(``docs/durability-posture.md``): these tests assert the derived graph is
REBUILDABLE, not that anything keeps it alive.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import tortoise.sdk as sdk_mod
from tortoise import hosted_api as ha
from tortoise.sdk import TortoiseSDK

# A conversation whose turn text carries a resolvable GitHub issue URL — the
# `session_link` trigger the capture's entity-linking pass reads.
#: Fixed mint clock so a replayed capture and a re-run of the same test compare.
_CAPTURE_NOW = "2026-10-25T00:00:00+00:00"


def _drive_capture(sdk, turns, session_id: str) -> dict:
    """A capture WITHOUT `TortoiseSDK.capture_session` (`#4282` deleted it).

    The wrapper's work survives in four calls, made here in the order the HOSTED
    production lane makes them (`hosted_api._capture_session_impl`):

      1. ``sdk_mod._write_session_and_turns`` — journals ``SessionRecorded`` and
         the turn records, which the ``session``/``point`` rows project from;
      2. ``sdk.create_event(..., "sessionCaptured")`` — the mint the event stream
         projects from;
      3. ``sdk._materialize_session_source`` — the typed session ``:Source`` and
         its ``(Source)-[:references]->(Event)`` wiring;
      4. ``sdk._extract_session_v2`` — the v2 assembly, which is what emits the
         ``aboutObject`` edges THIS FILE's subject is about.

    Returns a receipt-shaped dict, because the legs that survive here read only
    ``ok`` — the wrapper's richer receipt (``status``/``points``/``warnings``) is
    NOT reconstructed: the two legs that read those are deleted and disclosed in
    the module docstring rather than imitated here.
    """
    now = _CAPTURE_NOW
    sdk_mod._write_session_and_turns(sdk._get_proj(), sdk, session_id, turns, now=now)
    minted = sdk.create_event(
        f"session_{session_id}", "sessionCaptured",
        _server_id=f"sdk_{session_id}",
        startedAt=now, endedAt=now, sessionId=session_id, is_episodic=True,
    )
    event_id = minted.get("id") or minted.get("eventId")
    assert event_id, f"the sessionCaptured mint returned no id: {minted!r}"
    sdk._materialize_session_source(session_id, event_id, now, turns)
    sdk._extract_session_v2(turns, session_id, now)
    return {"ok": True, "session_id": session_id, "event_id": event_id,
            "driver": "surviving-primitives"}

CONV = [
    {"role": "user",
     "content": "we must ship github.com/test/repo/issues/42 today; "
                "the auth dead-end is the top issue."},
    {"role": "assistant", "content": "agreed"},
]


@pytest.fixture(autouse=True)
def _mock_extractor(monkeypatch):
    # The offline v2 seam — no provider key / network.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture
def hosted_lane(tmp_path, monkeypatch):
    """The hosted lane's own SDK constructor, with a per-run journal base dir.

    Uses whatever ``TORTOISE_DB_URI`` the lane provides (the docker test graph)
    so the REAL ``_make_sdk`` is under test — no monkeypatched constructor.
    """
    events = tmp_path / "events"
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(events))
    org = f"test-4240-{uuid.uuid4().hex[:10]}"
    yield SimpleNamespace(org=org, events=events, tmp_path=tmp_path)
    # Best-effort teardown: drop the test graph so a shared container does not
    # accumulate leaked graphs (#2979). The conftest hygiene sweep owns
    # `test_`-prefixed graphs too; this is the belt to that braces.
    with contextlib.suppress(Exception):
        _drop = ha._make_sdk(namespace=org)
        _drop._get_proj().g.query("MATCH (n) DETACH DELETE n")
        _drop.close()


def _about_edges(proj) -> set:
    """(source_label, source_id, target_key) for every live aboutObject edge.

    Not a count: a count cannot tell a Session edge from a turn edge.
    """
    rows = proj.g.query(
        "MATCH (s)-[:aboutObject]->(o:Object) "
        "RETURN labels(s), coalesce(s.id, s.eventId, s.name), "
        "coalesce(o.id, o.name)").result_set
    return {(tuple(r[0]), r[1], r[2]) for r in rows}


def _journal_types(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            with contextlib.suppress(ValueError):
                out.append(json.loads(line).get("type"))
    return out


# ── the resolver (§ the lane's per-graph journal path) ─────────────────────

def test_resolve_event_log_path_is_per_graph_and_none_when_unset(monkeypatch):
    """Unset base dir ⇒ None (byte-identical to pre-#4240); set ⇒ one file per
    graph, keyed by the SDK's OWN derivation, and the control-plane registry /
    default graph are excluded.

    MUTATION: drop the ``if not base: return None`` guard → an unconfigured
    lane starts writing a journal at ``None/…`` (RED). Drop the registry
    exclusion → the control plane gets an org data journal (RED).
    """
    monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
    assert ha._resolve_event_log_path(namespace="acme", graph_name=None) is None
    assert ha._resolve_event_log_path(namespace=None, graph_name=None) is None

    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", "/data/events")
    p = ha._resolve_event_log_path(namespace="acme", graph_name=None)
    assert p == "/data/events/org_acme/events.jsonl", p
    # An explicit full graph name is used verbatim (the custom-org-graph seam).
    p = ha._resolve_event_log_path(namespace=None, graph_name="org_abc_def")
    assert p == "/data/events/org_abc_def/events.jsonl", p
    # F2: the hyphenated test-* namespace maps to `<ns>_tortoise`, NOT
    # `org_<ns>` — the branch the pre-fix local derivation got wrong.
    p = ha._resolve_event_log_path(namespace="test-foo", graph_name=None)
    assert p == "/data/events/test_foo_tortoise/events.jsonl", p
    # Two projects in one namespace never share a journal file.
    assert (ha._resolve_event_log_path(namespace="acme", graph_name=None)
            != ha._resolve_event_log_path(namespace="acme2", graph_name=None))
    # The control plane is not an org data graph — never journaled here.
    assert ha._resolve_event_log_path(namespace="registry", graph_name=None) is None


def test_no_journal_key_can_escape_the_base_dir(monkeypatch):
    """F2(b): a journal key that is not a valid graph name must be REFUSED, not
    sanitized — sanitizing silently keyed the journal to a directory no graph
    uses, and the pre-fix guard kept ``.`` so ``..`` survived to the SDK's own
    constructor (i.e. the stated anti-traversal guard was dead).

    MUTATION: restore the sanitize-instead-of-validate guard → ``..`` is
    accepted and this test REDs (path would be ``/data/events/../events.jsonl``).
    Reachable: the SDK's graph-name rule rejects ``..``/``.``/``/``; the
    resolver runs BEFORE that constructor, so it is the only guard at build time.
    """
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", "/data/events")
    for bad in ("..", ".", "a/b", "a.b", "org x", ""):
        with pytest.raises(ValueError):
            ha._resolve_event_log_path(namespace=None, graph_name=bad)
    # And a bad namespace cannot smuggle one in through the derivation either.
    with pytest.raises(ValueError):
        ha._resolve_event_log_path(namespace="a/b", graph_name=None)


# ── the seam: the hosted SDK constructor carries the journal ───────────────

def test_make_sdk_wires_the_journal_when_the_base_dir_is_set(hosted_lane):
    """``_make_sdk(namespace=…)`` must build a journaled SDK when
    ``TORTOISE_EVENT_LOG_BASE_DIR`` is set.

    MUTATION: drop the ``event_log_path=`` argument from ``_make_sdk``'s
    ``TortoiseSDK(...)`` construction → ``_get_event_log()`` returns None and
    every JSONL-only record is a silent no-op (the pre-#4240 state) → RED.
    """
    sdk = ha._make_sdk(namespace=hosted_lane.org)
    try:
        log = sdk._get_event_log()
        assert log is not None, (
            "hosted _make_sdk built a journal-less SDK — every JSONL-only "
            "record (EntityLinked / SessionRecorded / ObjectRegistered) is a "
            "silent no-op, so a rebuild cannot reconstruct the capture (#4240)"
        )
        expected = ha._resolve_event_log_path(
            namespace=hosted_lane.org, graph_name=None)
        assert str(log.path) == expected, (str(log.path), expected)
    finally:
        sdk.close()


def test_journal_key_matches_the_graph_the_sdk_opens(hosted_lane):
    """F2(a): the journal key must be the graph ``_get_proj`` actually opens.

    A second, local derivation in the resolver keyed the journal to a graph the
    SDK never opened (the hyphenated ``test-*`` branch: ``org_test-…`` vs
    ``test_…_tortoise``), so a rebuild would replay the wrong file. This asserts
    the single-source-of-truth property directly.

    MUTATION: reintroduce a local key derivation in ``_resolve_event_log_path``
    → the hyphenated fixture namespace diverges → RED. Reachable: the fixture
    org is ``test-4240-<hex>``, a name that exercises the divergent branch.
    """
    sdk = ha._make_sdk(namespace=hosted_lane.org)
    try:
        proj = sdk._get_proj()
        log = sdk._get_event_log()
        assert log is not None
        assert Path(log.path).parent.name == proj.graph_name, (
            "journal key and the graph the SDK opened disagree — a rebuild "
            f"would replay the wrong file: key={Path(log.path).parent.name!r} "
            f"graph={proj.graph_name!r}")
        # The explicit graph-name seam keeps the same property.
        custom = ha._make_sdk(graph_name="test_4240_custom_tortoise")
        try:
            assert (Path(custom._get_event_log().path).parent.name
                    == custom._get_proj().graph_name
                    == "test_4240_custom_tortoise")
        finally:
            custom.close()
    finally:
        sdk.close()


# ── the capture path: aboutObject edges survive rebuild_all ────────────────

# ── the index-completion re-link path ──────────────────────────────────────

def test_relink_aboutobject_edge_survives_rebuild_all(hosted_lane):
    """The index-completion re-link (``_relink_sessions_after_index``) writes
    ``aboutObject`` edges through ``_make_sdk(namespace=org_id)``; they must be
    journaled and survive a rebuild.

    What this pins: the composite re-link seam. Its sibling — the capture-path
    leg ``test_capture_aboutobject_edges_survive_rebuild_all`` — was DELETED WITH
    ITS DRIVER by ``#4282`` (see the module docstring), so this is now the only
    rebuild-survival pin in the file.

    Reachable: the session is captured BEFORE its Object exists (an honest
    no-match), then the Object materializes and the re-link creates the edge —
    asserted non-empty live before the rebuild.

    MUTATION: drop the journal wiring in ``_make_sdk`` → the re-link's
    ``EntityLinked`` record is a no-op and the rebuilt edge set is empty → RED.
    """
    org = hosted_lane.org
    sdk = ha._make_sdk(namespace=org)
    try:
        proj = sdk._get_proj()
        # Capture first — no Object yet, so the conversation-reference pass is
        # an honest no-match (the extractor's own claim edges are a different
        # source and do not count).
        r = _drive_capture(sdk, CONV, "s-4240-relink")
        assert r.get("ok") is True, r
        assert not any(src == ("Session",)
                       for src, _, _ in _about_edges(proj)), \
            "no referenced entity yet — the Session pass must be an honest no-match"

        # Index completes → the WorkItem Object materializes.
        sdk.create_object("test/repo#42", objectKind="pm:issue")
        ha._relink_sessions_after_index(org)

        live = _about_edges(proj)
        assert any(src == ("Session",) for src, _, _ in live), \
            "the re-link must resolve the now-present Object"

        log_path = Path(ha._resolve_event_log_path(namespace=org, graph_name=None))
        assert "EntityLinked" in _journal_types(log_path), (
            "the re-link's EntityLinked record was not journaled")

        proj.rebuild_all(str(log_path.parent), confirm_destructive=True)
        assert _about_edges(proj) == live, (
            "re-linked aboutObject edge drift across rebuild\n"
            f" live={sorted(live)}\n post={sorted(_about_edges(proj))}")
    finally:
        sdk.close()


# ── the honest RED for the drift: mutate ONLY the wiring ───────────────────

def test_live_only_capture_loses_aboutobject_edges_without_the_journal(
        hosted_lane, monkeypatch):
    """The #4240 drift itself, reproduced with the wiring mutated (F4).

    The two ``live == rebuild`` tests above cannot show the drift at the pre-fix
    base (they fail on ``AttributeError`` first). This test removes ONLY the
    journal wiring — exactly the pre-#4240 ``_make_sdk`` — while every other
    piece is present, so the failure it produces IS the live-only edge loss the
    issue describes.

    Reachable: the live edge set is asserted non-empty BEFORE the rebuild, and
    the journal file is asserted absent (nothing landed to replay).

    MUTATION (inverse): wire the journal back in → the journal-less GUARD REDs
    FIRST — ``assert not (.../events.jsonl).exists()`` fails with "the
    journal-less SDK must not have written a journal" (the wired SDK DOES write
    it), before ``rebuild_all`` runs. The ``not post`` assertion is not the RED
    site (a wired journal would preserve the edges, but the guard stops the
    test before reaching it).
    """

    def _journal_less_make_sdk(*, namespace=None, graph_name=None):
        # The pre-#4240 construction: identical graph seam, no event_log_path.
        return TortoiseSDK(namespace=namespace, graph_name=graph_name)

    monkeypatch.setattr(ha, "_make_sdk", _journal_less_make_sdk)
    hosted_lane.events.mkdir(parents=True, exist_ok=True)

    sdk = ha._make_sdk(namespace=hosted_lane.org)
    try:
        proj = sdk._get_proj()
        sdk.create_object("test/repo#42", objectKind="pm:issue")
        r = _drive_capture(sdk, CONV, "s-4240-liveonly")
        assert r.get("ok") is True, r

        live = _about_edges(proj)
        assert live, "fixture must produce live aboutObject edges to lose"
        assert not (hosted_lane.events / proj.graph_name / "events.jsonl").exists(), \
            "the journal-less SDK must not have written a journal"

        proj.rebuild_all(str(hosted_lane.events), confirm_destructive=True)
        post = _about_edges(proj)
        assert not post, (
            "the live-only edges SURVIVED a rebuild_all — the mutation is inert "
            "and the drift is not being exercised\n"
            f" live={sorted(live)}\n post={sorted(post)}")
    finally:
        sdk.close()


# ── F1: a failed append on a CONFIGURED journal is not silent ──────────────

@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root bypasses directory mode bits")
def test_hosted_receipt_discloses_a_failed_journal_append(tmp_path, monkeypatch):
    """F2 (cycle 2): the disclosure must reach the HOSTED receipt itself.

    The hosted lane builds its own receipt in ``_capture_session_impl`` and never
    calls ``sdk.capture_session`` (``grep capture_session hosted_api.py``
    → none), so the SDK-lane warning was invisible to a hosted client — only the
    operator counter/ERROR log showed a live-only capture. This drives the REAL
    hosted impl with its own org dict + reserved slot and asserts the warning is
    on ITS receipt.

    Reachable: ``TORTOISE_EVENT_LOG_BASE_DIR`` points at an existing mode-0500
    dir, so the hosted SDK's every journal append raises and
    ``sdk._journal_write_failures`` is non-zero by the time the receipt is built.

    ⚠️ #7951 SUPERSEDES the outcome half, exactly as in the F1 test above: the
    append is now the commit point, so nothing persists and the envelope is
    ``partial``, not ``ok``. The DISCLOSURE (this test's deliverable) is kept and
    must still ride a partial capture.

    MUTATION: drop the hosted receipt disclosure (the pre-F2 state) → the
    warning is absent on this receipt → RED.
    """
    events = tmp_path / "events"
    events.mkdir()
    events.chmod(0o500)
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(events))
    org_id = f"test-4240-{uuid.uuid4().hex[:10]}"
    org = {"org_id": org_id, "graph_id": None, "key_id": None, "tier": "free",
           "scopes": [], "legacy_full_access": True, "max_points": 100000,
           "max_sessions": None}
    body = ha.SessionRequest(conversation=CONV,
                             session_id="s-4240-hosted-receipt",
                             harness="claude")
    slot = ha._reserve_capture_slot(ha._capture_session_key(org, body.session_id))
    try:
        r = asyncio.run(ha._capture_session_impl(body, None, org, slot=slot))
    finally:
        slot.release()
        events.chmod(0o700)  # restore before teardown
    assert r.get("status") == "partial", r
    assert any("journal append failed" in w for w in r["warnings"]), (
        "the hosted receipt did not disclose the failed journal append — a "
        f"hosted client cannot see its capture is live-only: {r['warnings']}")
    with contextlib.suppress(Exception):
        _drop = ha._make_sdk(namespace=org_id)
        _drop._get_proj().g.query("MATCH (n) DETACH DELETE n")
        _drop.close()


# ── #5632: the disclosed count is THIS capture's delta, not the lifetime one ──

@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root bypasses directory mode bits")
def test_hosted_capture_after_a_failed_non_capture_write_reports_no_warning(
        tmp_path, monkeypatch):
    """#5632 (hosted lane): byte-parity with the SDK-lane delta test above.

    The hosted impl reads the same LIFETIME counter. Its capture SDK is built
    fresh at the SDK-open seam (``_capture_session_probe_off_loop``), so the
    false positive needs a failed append on THAT instance before the capture
    begins — the state a polluted lifetime counter produces. ``_make_sdk`` is
    wrapped to perform one REAL failed non-capture append (``create_object``
    against a mode-0500 journal dir), then disarms, so the capture's own
    appends succeed and its delta is 0.

    MUTATION: read the lifetime counter (the pre-#5632 state) → the hosted
    receipt carries the warning → RED.
    """
    events = tmp_path / "events"
    events.mkdir()
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(events))
    org_id = f"test-5632-{uuid.uuid4().hex[:10]}"
    org = {"org_id": org_id, "graph_id": None, "key_id": None, "tier": "free",
           "scopes": [], "legacy_full_access": True, "max_points": 100000,
           "max_sessions": None}
    real_make_sdk = ha._make_sdk
    opened: list = []
    armed = {"on": True}

    def _opened_sdk_with_a_failed_non_capture_write(*a, **kw):
        sdk = real_make_sdk(*a, **kw)
        if armed["on"] and kw.get("namespace") == org_id:
            armed["on"] = False
            events.chmod(0o500)
            try:
                sdk.create_object("test/repo#5632", objectKind="pm:issue")
            finally:
                events.chmod(0o700)
            # Bind the baseline HERE — AFTER the non-capture failure and
            # BEFORE the capture runs. This is the counter the capture's own
            # delta must be measured against; reading it after the capture
            # would compare the attribute to itself (a tautology).
            opened.append((sdk, sdk._journal_write_failures))
        return sdk

    monkeypatch.setattr(ha, "_make_sdk",
                        _opened_sdk_with_a_failed_non_capture_write)
    body = ha.SessionRequest(conversation=CONV,
                             session_id="s-5632-delta-hosted",
                             harness="claude")
    slot = ha._reserve_capture_slot(ha._capture_session_key(org, body.session_id))
    try:
        r = asyncio.run(ha._capture_session_impl(body, None, org, slot=slot))
    finally:
        slot.release()
        events.chmod(0o700)  # restore before teardown
    assert r.get("status") == "ok", r
    assert opened, "the wrapper never saw the capture's SDK open"
    sdk, before = opened[0]
    assert before > 0, (
        "fixture precondition: the non-capture append must have failed")
    assert sdk._journal_write_failures == before, (
        "the capture's own appends must have SUCCEEDED — otherwise this test "
        "would not exercise the false positive")
    assert not any("journal append failed" in w for w in r["warnings"]), (
        "the hosted receipt reported an EARLIER non-capture failure as this "
        f"capture's residue: {r['warnings']}")
    with contextlib.suppress(Exception):
        _drop = ha._make_sdk(namespace=org_id)
        _drop._get_proj().g.query("MATCH (n) DETACH DELETE n")
        _drop.close()
