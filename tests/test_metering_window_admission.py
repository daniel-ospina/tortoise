"""#3981 — the metering-window raise is a SIGNAL; the seven absorbing call sites each report an unmetered increment.

THE COVERAGE PROOF IS COMPLETENESS — SEVEN SITES, NOT FOUR
--------------------------------------------------------
#3825 made ``metering._require_period`` raise ``QuotaCheckError`` when an org's
metering window is unresolvable, and its docstring claimed that refusal "REFUSES
the user write". It does not: **every** production caller wraps the ``record_*``
call in a broad ``except`` and absorbs the raise, so the request is served and
the increment is dropped — the pre-#3825 behaviour with a louder module log.
#3981's fix (owner ruling: proceed-and-alert) leaves the pre-spend admission gate
untouched; the seven absorbing call sites below each emit a lane-naming ERROR record
and report an unmetered increment:

  1. ``hosted_api._record_write_op``        → lane=write_op
  2. ``hosted_api._emit_capture_ledger``    → lane=capture_ledger
  3. ``hosted_api.create_object``           → lane=object_write_op
  4. ``hosted_api.create_subject``          → lane=subject_write_op
  5. ``mcp_server._quota_gated`` (inner)    → lane=mcp_write_op
  6. ``ask_lane.run_ask_lane`` step 7        → lane=ask_ledger
  7. ``embed_metering._report``             → lane=embed  (#4488)

Site 7 is #4488's embedding-encode measurement: its ``flush_tally`` absorbs a
failed increment (and an unattributable non-empty tally) and reports it on the
``embed`` lane. Its census depends on the call FORM — ``_report`` passes
``lane=`` as a KEYWORD, because the fence below matches only that form. A
positional call would make the lane invisible here, which is the opposite of
the intent, so the form is load-bearing. (``ask_ledger`` is censused by the
same regex and shares this property — the embed lane is not unique in it.)

Sites 3 and 4 are the two the issue body missed. They are a *second-line*
handler around the already-absorbing ``_record_write_op``: normal traffic
signals as lane=write_op and reaches them only if the inner helper itself
raises. The owner's SEVEN-site requirement counts the HANDLERS, and the
correctness proof is that no handler is a silent ``pass`` — so an uncovered
one would be a silent drop exactly like the original defect.

Site 2's call site (the ``_emit_capture_ledger`` invocation in the capture
handler) is pinned by ``tests/test_cohort_cost_cap.py``; this file pins the
helper's handler. The coverage is the two files together.

WHAT EACH TEST BINDS
--------------------
Every test asserts the SIGNAL (an ERROR record naming its lane), never the
raise: a raise a caller swallows enforces nothing. Each is RED on the unfixed
tree because the handler is a bare ``except Exception: pass`` and no
lane-naming record exists there.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from fastapi.testclient import TestClient

import tortoise.ask_lane as ask_lane_mod
import tortoise.hosted_api as ha
import tortoise.metering as metering
import tortoise.supabase_control as sc
from tortoise import mcp_auth
from tortoise import mcp_server as mcp
from tortoise import operator_alert as oa
from tortoise.alert_store import AlertStore
from tortoise.ask_lane import (
    _reset_ask_reader_cache_for_tests,
    run_ask_lane,
)
from tortoise.hosted_api import app
from tortoise.hosted_backup import MemoryStorage
from tortoise.sdk import TortoiseSDK

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tests.test_metering_period_window import _anchor, reg_org  # noqa: F401

#: The seven swallow sites → the lane token each must report. The inventory is
#: asserted against source in ``test_the_seven_swallow_sites_are_the_seven_lanes``.
SITE_LANES: dict[str, str] = {
    "write_op": "tortoise/hosted_api.py",
    "capture_ledger": "tortoise/hosted_api.py",
    "object_write_op": "tortoise/hosted_api.py",
    "subject_write_op": "tortoise/hosted_api.py",
    "mcp_write_op": "tortoise/mcp_server.py",
    "ask_ledger": "tortoise/ask_lane.py",
    # #4488: the embed lane's own swallow site. ``flush_tally`` reports a
    # WINDOW-UNRESOLVABLE drop and a tally with no resolvable org here. Two
    # drops do NOT reach this site and are NOT silent-by-this-lane: a failure
    # of the increment RPC itself is absorbed at WARNING inside
    # ``metering.record_embedding_usage`` (the shared #3824 residual), and a
    # note landing after the tally was consumed is the declared
    # capture-cancellation residual. Same ruling, seventh lane.
    "embed": "tortoise/embed_metering.py",
}

_SUPABASE_URL = "https://n3981.test.supabase.co"


def _forced_window_error():
    """The window-resolution raise, forced at the metering seam.

    This is the exact exception ``_require_period`` produces for a half-known
    anchor; forcing it (rather than provisioning one) keeps each test about the
    CALLER's handler.
    """
    from tortoise.quota import QuotaCheckError

    def _raise(*_args, **_kwargs):
        raise QuotaCheckError(
            "metering window unresolvable for org 'org-3981': it carries "
            "subscription 'sub-invert' but its billing period is not a usable "
            "half-open interval (test-forced)")

    return _raise


def _unmetered_lanes(caplog) -> list[str]:
    """The lane tokens of every operator alert captured at ERROR.

    Records are matched by MESSAGE, not by logger NAME: a name filter would
    blind this to ``ask_lane``'s direct log line. A record containing the
    marker but no ``lane=`` token is skipped rather than sliced — the pre-#3981
    shape would have raised ``IndexError`` on it.
    """
    lanes: list[str] = []
    for rec in caplog.records:
        msg = rec.getMessage()
        if "UNMETERED INCREMENT" not in msg or "lane=" not in msg:
            continue
        lanes.append(msg.split("lane=", 1)[1].split(" ", 1)[0])
    return lanes


@pytest.fixture(autouse=True)
def _clean_ask_state(monkeypatch):
    """Reset the shared ask-reader cache + budget between tests, and force the
    SDK's LOCAL ask lane (a dev shell may export ``TORTOISE_API_URL``, which
    would route ``sdk.ask`` at the hosted ``/v1/ask`` instead)."""
    from tortoise.quota import _reset_ask_budget_for_tests
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    _reset_ask_reader_cache_for_tests()
    _reset_ask_budget_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


@pytest.fixture
def hosted(tmp_path, monkeypatch):
    """The REAL hosted app over the shared FakeControlPlane + temp embedded DB.

    Mirrors ``tests/test_action_endpoints_dual_auth.env`` (the established
    harness) — a signed-up org resolved through the real auth dependency, so
    ``/v1/points``, ``/v1/objects`` and ``/v1/subjects`` run their real
    handlers including the metering call sites.
    """
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-3981-test")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    ha._INDEX_JOBS.clear()
    with patched_tortoise_sdk(str(tmp_path / "n3981.db")):
        try:
            with TestClient(app) as client:
                r = client.post("/v1/agent/signup", json={})
                assert r.status_code == 200, r.text
                data = r.json()
                yield SimpleNamespace(
                    client=client, org_id=data["org_id"], key=data["key"],
                    headers={"Authorization": f"Bearer {data['key']}"})
        finally:
            ha._INDEX_JOBS.clear()


# ── Site 1: hosted_api._record_write_op (the primary RED test) ──────────────


def test_write_op_drop_is_signalled_at_the_http_caller(hosted, monkeypatch,
                                                       caplog):
    """PRIMARY RED TEST (#3981). A forced window failure on the write-op meter
    must be reported at the caller, and the request must still be SERVED.

    #3981 defect: ``_record_write_op`` ended in ``except Exception: pass`` — no
    trace anywhere at the caller — so the ledger ran short silently. Owner
    ruling: a bookkeeping fault of ours never hands the user a 500, so the
    signal (not a refusal) is the fix.

    RED on the unfixed tree: ``_unmetered_lanes`` is empty (`_require_period`'s
    own module-level ERROR is not a caller signal and carries no lane).
    Mutations caught:
      M1  revert the handler to bare ``except Exception: pass`` → no lane → RED.
      M2  delete the caller's metering call entirely → RED (no alert at all).
      M4  turn the absorbed raise into a user-facing refusal (500) → the
          status assertion fails — the ruling explicitly forbids this.
    (M3 — ``_require_period`` degrading to the calendar month — is caught by
    ``test_write_op_lane_reports_a_real_unresolvable_window`` below, which drives
    the REAL writer; this test stubs that writer, so M3 is out of its reach.)
    """
    monkeypatch.setattr(metering, "record_write_ops", _forced_window_error())
    with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
        r = hosted.client.post(
            "/v1/points", headers=hosted.headers,
            json={"content": "n3981 write-op lane", "kind": "statement"})

    assert r.status_code == 200, r.text
    assert _unmetered_lanes(caplog) == ["write_op"], (
        [rec.getMessage() for rec in caplog.records])


def test_write_op_lane_reports_a_real_unresolvable_window(request, caplog):
    """No stub at the metering seam: a REAL inverted anchor makes the real
    writer raise a real ``QuotaCheckError``, and the caller must convert it into
    the operator alert in ``_require_period``'s TRUTHFUL contract.

    REDs if the caller ever re-raises (a swallowed raise is not enforcement and
    a served request must not 500 over bookkeeping) or swallows it silently.
    """
    sdk, tid = request.getfixturevalue("reg_org")
    _anchor(sdk._get_registry(), tid,
            "2026-10-03T00:00:00+00:00", "2026-09-03T00:00:00+00:00")
    with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
        ha._record_write_op({"org_id": tid, "tier": "pro"})  # must NOT raise
    assert _unmetered_lanes(caplog) == ["write_op"], (
        [rec.getMessage() for rec in caplog.records])


def test_alert_import_failure_never_becomes_a_user_facing_error(monkeypatch,
                                                                caplog):
    """The alert must never depend on successfully importing the metering
    module: if THAT module is the thing that failed, an unguarded alert import
    inside the handler would escape and hand the user the 500 the ruling
    forbids. The fallback logs the same lane directly.

    Mutation caught: reverting ``_alert_unmetered`` to a bare
    ``from tortoise.metering import report_unmetered_increment`` inside the
    handler — this call then raises instead of returning.
    """
    monkeypatch.setitem(sys.modules, "tortoise.metering", None)
    with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
        ha._record_write_op({"org_id": "org-3981", "tier": "pro"},
                            nodes_written=1)  # must NOT raise
    assert _unmetered_lanes(caplog) == ["write_op"], (
        [rec.getMessage() for rec in caplog.records])


def _op_channels():
    """A REAL ``AlertStore`` over fake GitHub + Telegram transport."""
    class _Ch:
        def __init__(self):
            self.issues: dict[int, str] = {}
            self.telegram: list[str] = []
            self._next = 1

        def file_issue(self, title, body):
            n = self._next
            self._next += 1
            self.issues[n] = title
            return n

        def close_issue(self, number, comment=None):
            pass

        def search_open(self, kind, org_id=""):
            return [n for n, t in self.issues.items() if f"[DR] {kind}" in t]

        def push_telegram(self, text):
            self.telegram.append(text)

    ch = _Ch()
    store = AlertStore(
        MemoryStorage(), file_issue=ch.file_issue, close_issue=ch.close_issue,
        search_open=ch.search_open, push_telegram=ch.push_telegram,
        repo="daniel-ospina/tortoise", assignee="daniel-ospina")
    return store, ch


@pytest.mark.parametrize("call,lane", [
    (ha._alert_unmetered, "write_op"),
    (mcp._alert_unmetered, "mcp_write_op"),
])
def test_the_import_guard_fallbacks_still_alert(monkeypatch, caplog, call, lane):
    """The fallback that runs when ``tortoise.metering`` itself is unavailable
    must ALERT, not merely log.

    That branch is the one case where the ledger AND the reporter are both down,
    so a log line on an ephemeral rootfs is the #3677 silent-loss class. REDs if
    the fallback is reverted to log-and-return (the incident never files).
    """
    from tortoise.quota import QuotaCheckError

    store, ch = _op_channels()
    monkeypatch.setattr(oa, "alert_store", lambda: store)
    monkeypatch.setitem(sys.modules, "tortoise.metering", None)
    with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
        call(lane, "org-fallback", QuotaCheckError("x"))  # must NOT raise
    assert _unmetered_lanes(caplog) == [lane], (
        [rec.getMessage() for rec in caplog.records])
    assert oa.join_operator_alerts() == 0
    assert list(ch.issues.values()) == [
        f"[DR] {oa.UNMETERED_INCREMENT_KIND} — org-fallback"], ch.issues
    assert ch.telegram, "the fallback incident must also push"


def test_the_ask_lane_fallback_still_alerts(tmp_path, monkeypatch, caplog):
    """The THIRD import-guard fallback (inline in ``run_ask_lane`` step 7).

    A half-broken ``tortoise.metering`` (the cost-rate helpers importable, the
    writer not) is installed, so the writer import fails exactly as a partial
    deploy does — without breaking the rest of the ask lane. REDs if that
    fallback is reverted to log-only. (Embedded-DB lane; see the sibling ask
    test below for the known redislite interaction.)
    """
    import types

    store, ch = _op_channels()
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    stub = types.ModuleType("tortoise.metering")
    stub.estimate_ask_cost_usd = metering.estimate_ask_cost_usd
    stub.select_ask_meter_rates = metering.select_ask_meter_rates
    monkeypatch.setitem(sys.modules, "tortoise.metering", stub)

    class _Reader:
        model = "deepseek-v4-flash"
        route = "deepseek-direct"

        def complete(self, *, system, user):
            return "served answer"

        def close(self):
            pass

    monkeypatch.setattr(ask_lane_mod, "_default_ask_reader_factory",
                        lambda: _Reader())
    sdk = TortoiseSDK(str(tmp_path / "askfallback3981.db"))
    try:
        with caplog.at_level(logging.ERROR, logger="tortoise.ask_lane"):
            result = run_ask_lane(sdk, "what is the plan?", org_id="org-fb")
    finally:
        sdk.close()
    assert result["answer"] == "served answer"
    assert _unmetered_lanes(caplog) == ["ask_ledger"], (
        [rec.getMessage() for rec in caplog.records])
    assert oa.join_operator_alerts() == 0
    assert list(ch.issues.values()) == [
        f"[DR] {oa.UNMETERED_INCREMENT_KIND} — org-fb"], ch.issues


def test_dropped_increment_files_an_operator_incident(request, monkeypatch):
    """The mandated end-to-end deliverable: a REAL inverted anchor → the real
    writer → a real ``AlertStore`` → a filed GitHub issue + Telegram push.

    Asserts the INCIDENT, never merely a log record: the log line was already
    present before #3981, so a log-only assertion would be satisfied by the
    defect. REDs if the reporter stops dispatching (OAM1) or the dispatcher
    becomes a no-op (OAM2).
    """
    sdk, tid = request.getfixturevalue("reg_org")
    _anchor(sdk._get_registry(), tid,
            "2026-10-03T00:00:00+00:00", "2026-09-03T00:00:00+00:00")
    store, ch = _op_channels()
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    ha._record_write_op({"org_id": tid, "tier": "pro"})  # must NOT raise
    assert oa.join_operator_alerts() == 0

    assert list(ch.issues.values()) == [
        f"[DR] {oa.UNMETERED_INCREMENT_KIND} — {tid}"], ch.issues
    assert ch.telegram, "the incident must push, not only file"
    body = next(iter(ch.issues))
    assert body  # keep the number for a readable failure


def test_ambiguous_org_ids_share_one_incident_and_throttle(monkeypatch):
    """``None`` and ``""`` collapse onto ONE subject and one throttle key.

    The stdio lanes carry no org context, so every such drop would otherwise
    look like a distinct incident and a distinct throttle bucket — an alert
    storm from one broken deployment. The rendered subject is EMPTY (AlertStore
    never renders a ``_`` placeholder), so the title carries no org suffix.
    """
    store, ch = _op_channels()
    monkeypatch.setattr(oa, "alert_store", lambda: store)

    for org in (None, "", None, ""):
        oa.alert_operator(oa.UNMETERED_INCREMENT_KIND, org,
                          {"lane": "mcp_write_op", "error_type": "X"})
    assert oa.join_operator_alerts() == 0

    assert list(ch.issues.values()) == [f"[DR] {oa.UNMETERED_INCREMENT_KIND}"], (
        ch.issues)
    assert oa._ATTEMPT and len(oa._ATTEMPT) == 1
    assert next(iter(oa._ATTEMPT)) == (oa.UNMETERED_INCREMENT_KIND, "")


# ── Site 2: hosted_api capture ledger emit ────────────────────────────────


def test_capture_ledger_drop_is_signalled(caplog, monkeypatch):
    """The capture ledger write is best-effort (a committed capture never
    fails over bookkeeping) but the drop must be attributable to the LEDGER —
    not buried in the analytics emit's log line (the pre-#3981 comment claimed
    ``record_capture_usage`` "swallows its own failures", which stopped being
    true at #3825).

    REDs on: reverting ``_emit_capture_ledger``'s handler to a bare pass, or
    collapsing the ledger failure into the analytics handler (no lane token).

    SCOPE NOTE: this calls the emit helper directly, so it pins the HANDLER, not
    the call site. Deleting the ``_emit_capture_ledger`` call in the capture
    handler leaves this test GREEN; the call site is pinned by
    ``tests/test_cohort_cost_cap.py::test_capture_ledger_write_is_what_the_cohort_spend_reader_reads``.
    """
    monkeypatch.setattr(ha, "_capture_cost_props",
                        lambda *_a, **_k: {"cost_usd": 0.25})
    monkeypatch.setattr(metering, "record_capture_usage",
                        _forced_window_error())
    with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
        asyncio.run(ha._emit_capture_ledger("org-3981", "sess-3981", {}))
    assert _unmetered_lanes(caplog) == ["capture_ledger"], (
        [rec.getMessage() for rec in caplog.records])


# ── Sites 3 and 4: create_object / create_subject outer guards ─────────────


def test_create_object_drop_is_signalled(hosted, monkeypatch, caplog):
    """``create_object`` wraps ``_record_write_op`` in its own guard — one of
    the two handlers the issue body missed, and one of the owner's declared
    seven. It is a SECOND-LINE guard: ``_record_write_op`` already absorbs and
    signals its own failures, so normal production traffic is reported as
    lane=write_op and reaches this handler only if the inner helper itself
    raises (an import fault, a future refactor). The owner's seven-site proof
    counts the HANDLERS, so this one must not be a silent ``pass`` either.

    REDs on: reverting this guard to ``except Exception: pass`` (no
    object_write_op lane).
    """
    monkeypatch.setattr(ha, "_record_write_op", _forced_window_error())
    with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
        r = hosted.client.post("/v1/objects", headers=hosted.headers,
                               json={"name": "N3981Object",
                                     "objectKind": "project"})
    assert r.status_code == 200, r.text
    assert _unmetered_lanes(caplog) == ["object_write_op"], (
        [rec.getMessage() for rec in caplog.records])


def test_create_subject_drop_is_signalled(hosted, monkeypatch, caplog):
    """The second of the two missed sites; same contract as ``create_object``.
    REDs on reverting its guard to a bare pass."""
    monkeypatch.setattr(ha, "_record_write_op", _forced_window_error())
    with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
        r = hosted.client.post("/v1/subjects", headers=hosted.headers,
                               json={"name": "N3981Subject",
                                     "subjectKind": "person"})
    assert r.status_code == 200, r.text
    assert _unmetered_lanes(caplog) == ["subject_write_op"], (
        [rec.getMessage() for rec in caplog.records])


# ── Site 5: mcp_server._quota_gated ────────────────────────────────────────


def test_mcp_write_op_drop_is_signalled(caplog, monkeypatch):
    """The MCP write-tool meter swallows on a broad ``except`` and must report
    lane=mcp_write_op. The pre-write ``_enforce_quota`` refusal is untouched —
    it runs BEFORE the write and is the only refusal on this lane.

    REDs on: reverting the handler to ``except Exception: pass``.
    """
    monkeypatch.setattr(mcp, "_enforce_quota", lambda *_a, **_k: None)
    monkeypatch.setattr(metering, "record_write_ops", _forced_window_error())
    token = mcp_auth._current_org_id.set("org-3981")
    try:
        gated = mcp._quota_gated(lambda: {"ok": True})
        with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
            result = gated()
    finally:
        mcp_auth._current_org_id.reset(token)
    assert result == {"ok": True}
    assert _unmetered_lanes(caplog) == ["mcp_write_op"], (
        [rec.getMessage() for rec in caplog.records])


def test_mcp_abuse_failure_is_not_reported_as_a_dropped_increment(caplog,
                                                                  monkeypatch):
    """The abuse-weight recording lives in its OWN best-effort block after the
    metering increment (#3981). An abuse failure is not a dropped increment and
    must NOT raise an UNMETERED INCREMENT alert — a folded-together handler
    (the pre-#3981 single ``try``) would emit a false alert here.

    REDs on: merging the abuse block back under the metering ``except`` so that
    its failure is reported as lane=mcp_write_op.
    """
    import tortoise.abuse as abuse_mod

    monkeypatch.setattr(mcp, "_enforce_quota", lambda *_a, **_k: None)
    monkeypatch.setattr(mcp, "_abuse_off", lambda: False)
    # the increment SUCCEEDS — this test isolates the abuse failure
    monkeypatch.setattr(metering, "record_write_ops",
                        lambda *_a, **_k: {"write_ops": 1})

    class _Engine:
        def record_point_create(self, *_a, **_k):
            raise RuntimeError("abuse store down")

    monkeypatch.setattr(abuse_mod, "get_engine", lambda: _Engine())
    token = mcp_auth._current_org_id.set("org-3981")
    try:
        gated = mcp._quota_gated(lambda: {"ok": True}, abuse_weight=3)
        with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
            result = gated()
    finally:
        mcp_auth._current_org_id.reset(token)
    assert result == {"ok": True}
    assert _unmetered_lanes(caplog) == [], (
        [rec.getMessage() for rec in caplog.records])


# ── Site 6: ask_lane.run_ask_lane step 7 ───────────────────────────────────


def test_ask_ledger_drop_is_signalled(caplog, monkeypatch, tmp_path):
    """``run_ask_lane`` meters AFTER the answer exists. A window failure there
    can refuse nothing, so it is absorbed and reported as lane=ask_ledger. The
    answer must still be returned.

    REDs on: reverting the handler to ``except Exception: pass``; or letting
    the raise escape (the answer would be lost to a bookkeeping fault).

    KNOWN LOCAL-ONLY INTERACTION (filed as a residual, not a production
    behaviour): in the EMBEDDED carve-out lane, running this file between
    ``test_metering_period_window.py`` and ``test_cohort_cost_cap.py`` in ONE
    pytest process can leave redislite daemon state that poisons a later
    direct-ledger test in that third file. CI never runs that combination (this
    file is selected into a different half) and the docker lane has no
    redislite. Reproduced with and without the explicit ``close()`` below; the
    trigger is the embedded-SDK combination, not this test's asserts.
    """
    class _Reader:
        model = "deepseek-v4-flash"
        route = "deepseek-direct"

        def complete(self, *, system, user):
            return "served answer"

        def close(self):
            pass

    monkeypatch.setattr(metering, "record_ask_usage", _forced_window_error())
    monkeypatch.setattr(ask_lane_mod, "_default_ask_reader_factory",
                        lambda: _Reader())
    sdk = TortoiseSDK(str(tmp_path / "ask3981.db"))
    try:
        with caplog.at_level(logging.ERROR, logger="tortoise.metering"):
            result = run_ask_lane(sdk, "what is the plan?", org_id="org-3981")
    finally:
        sdk.close()
    assert result["answer"] == "served answer"
    assert _unmetered_lanes(caplog) == ["ask_ledger"], (
        [rec.getMessage() for rec in caplog.records])


# ── The completeness fence ─────────────────────────────────────────────────


def test_the_seven_swallow_sites_are_the_seven_lanes():
    """The correctness proof for #3981 IS coverage completeness, so pin the
    inventory against the SOURCE — not against this module's own dict (a bare
    ``len(SITE_LANES) == 7`` is tautological: it counts a literal three lines
    above it and cannot fail). The scan covers every lane token passed through
    the two lane-carrying helpers — ``_alert_unmetered("<lane>", ...)`` and
    ``report_unmetered_increment(lane="<lane>", ...)``; the (file, lane) pairs
    actually emitted must equal the declared seven. A site routed through
    either helper, a renamed token, or a deleted alert turns this RED.

    (#4488 added the seventh lane, ``embed``, when embedding-encode
    measurement became its own swallow site. The census matches only the
    ``lane=`` KEYWORD form, which is why ``embed_metering._report`` calls it
    that way: a positional call would make the new lane invisible here —
    the opposite of the intent.)

    (A silent swallow reusing an EXISTING lane token is not detectable by this
    fence; it is caught by review, and the seven lanes above are the declared
    surface.)
    """
    root = Path(__file__).resolve().parents[1]
    emitted: set[tuple[str, str]] = set()
    for path in sorted((root / "tortoise").glob("*.py")):
        src = path.read_text(encoding="utf-8")
        rel = f"tortoise/{path.name}"
        lanes = set(re.findall(r'_alert_unmetered\(\s*"([a-z_]+)"', src))
        lanes |= set(re.findall(
            r'report_unmetered_increment\(\s*lane="([a-z_]+)"', src))
        emitted |= {(lane, rel) for lane in lanes}
    assert emitted == set(SITE_LANES.items()), emitted
