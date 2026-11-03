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
* **F1** — a configured journal whose append fails is not silent:
  ``test_unwritable_journal_is_disclosed_and_counted`` pins the ERROR/counter
  signal and the additive capture-receipt warning. The append stays fail-soft
  by design (the graph mutation already succeeded), so ``ok`` remains True —
  flipping it would trip the #2335 TRUE-retry gate over a persisted write.
* **F2 (cycle 2)** — the same disclosure must reach the HOSTED receipt, not just
  the SDK lane: ``test_hosted_receipt_discloses_a_failed_journal_append`` drives
  ``_capture_session_impl`` itself (it builds its own receipt and never calls
  ``sdk.capture_session``), asserts the warning is present, and asserts the
  outcome is unchanged (the envelope's ``status`` is ``"ok"`` — the hosted
  receipt's success signal — independent of the ``_capture_ok_record`` written
  earlier).
* **#5632** — the count disclosed on the receipt is THIS capture's DELTA, never
  the SDK's LIFETIME counter: ``test_capture_after_a_failed_non_capture_write_
  reports_no_journal_warning`` (SDK lane) and its hosted twin fail a
  NON-capture append, then capture successfully, and assert NO journal warning.
  A lifetime read brands every later capture live-only forever.

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
* ``test_capture_aboutobject_edges_survive_rebuild_all`` — FAILS while the
  capture's edges are absent from the journal (the rebuild's edge set
  collapses to empty); the fixture mints a resolvable WorkItem Object and
  captures a conversation that references it, so the live edge set is
  non-empty (reachable: pinned by the pre-rebuild assertion). **Honest RED:** at
  the pre-#4240 base this test fails with ``AttributeError`` (no
  ``_resolve_event_log_path``) — a wiring pin, not the drift. The drift itself
  is exercised by ``test_live_only_capture_loses_aboutobject_edges_without_the_journal``.
* ``test_relink_aboutobject_edge_survives_rebuild_all`` — FAILS while the
  re-link's ``EntityLinked`` record is not written; the fixture captures BEFORE
  the Object exists (honest no-match) then materializes it, so the re-link
  really creates an edge (reachable: pinned on the live count). Same honest-RED
  note as above.
* ``test_live_only_capture_loses_aboutobject_edges_without_the_journal`` — the
  ONE test whose RED is the drift itself: it mutates only the wiring (the
  pre-#4240 journal-less construction) and shows the live-only edges do NOT
  survive ``rebuild_all``.
* ``test_unwritable_journal_is_disclosed_and_counted`` — FAILS while a failed
  append is silent (no counter, no receipt warning); reachable: the fixture
  chmods the journal base dir to 0500.
* ``test_hosted_receipt_discloses_a_failed_journal_append`` — FAILS while the
  HOSTED receipt omits the disclosure (the pre-cycle-2 state: only the SDK lane
  carried it); reachable: the hosted impl is driven directly against a mode-0500
  journal base dir, and the envelope's ``status`` is asserted unchanged
  (``"ok"`` — the hosted receipt's success signal) alongside it.
* ``test_capture_after_a_failed_non_capture_write_reports_no_journal_warning``
  — FAILS while the receipt reads the LIFETIME counter (the pre-#5632 state): a
  failed ``create_object`` append precedes a fully successful capture, so the
  warning must be absent; reachable: the journal base dir is 0500 for that
  non-capture write and 0700 for the capture, and the counter is asserted to
  move before the capture and NOT move during it.
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

from tortoise import hosted_api as ha

# A conversation whose turn text carries a resolvable GitHub issue URL — the
# `session_link` trigger the capture's entity-linking pass reads.
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



# ── the honest RED for the drift: mutate ONLY the wiring ───────────────────



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
