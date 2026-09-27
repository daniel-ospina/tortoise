"""#4779 — leg 2 of the #3981 ruling: a dropped increment is DISTINGUISHABLE
from a zero increment.

WHAT THIS FILE IS
-----------------
#3981's ruling ("PROCEED AND ALERT") has three legs. Leg 1 (the request
proceeds) and leg 3 (an operator alert fires) shipped. Leg 2 — "the increment
is recorded as explicitly unmeterable" — had no artifact, so a dropped
increment was simply ABSENT from every durable surface: indistinguishable from
an org that genuinely spent zero.

THE CENTRAL ACCEPTANCE TEST
---------------------------
``test_a_dropped_increment_is_distinguishable_from_a_zero_increment`` is the
one that matters, and it is written so that it CANNOT PASS on the old tree. It
drives a REAL unmeterable org (an inverted anchor → ``_require_period`` raises →
the caller's handler → ``report_unmetered_increment``) and a REAL control org
that never dropped, then asserts the PAIR of observables *differ*:
``get_cohort_spend_usd`` is 0.0 for BOTH (the count is not spend — the ceiling
must keep reading only ``ask_cost_usd + capture_cost_usd``, #4779 constraint 1)
while ``get_unmetered_increment_total`` is N for one and 0 for the other. An
assertion that could not tell them apart would be the defect restated.

BOTH DROP PATHS, TWO DECLARED CLASSES
-------------------------------------
A decrement is dropped in two distinct places, and they get distinct declared
classes so an operator can tell them apart:

  * ``window_unresolvable`` — ``_require_period`` raised; written from
    ``report_unmetered_increment`` (so it rides all six leg-3 swallow lanes);
  * ``increment_write_unconfirmed`` — the writer's own increment call RAISED with
    the window KNOWN; written from that writer's handler. Named UNCONFIRMED, not
    failed: ``metering_increment``'s lost-response case (#925) means a raise does
    not prove the increment was not written, so the class carries an upper bound
    rather than a false claim made durable.

Most tests NAME the mutation that must make them RED. A few pin an OUTCOME or a
layered refusal and SAY SO in place of a mutation clause — where that is the
case, the layer that owns the mutation is named too.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

import tortoise.metering as metering_mod
from tests.test_metering import _break_increment_only
from tests.test_metering_period_window import (
    SUB_END,
    SUB_START,
    _anchor,
    _period,
    reg_org,  # noqa: F401 — a pytest fixture, requested by name in ``reg``
    supabase_mode,  # noqa: F401 — a pytest fixture, requested by name in ``sb``
)
from tortoise.metering import (
    DROP_CLASS_INCREMENT_WRITE_UNCONFIRMED,
    DROP_CLASS_WINDOW_UNRESOLVABLE,
    _current_period,
    get_cohort_spend_usd,
    get_unmetered_increment_total,
    get_unmetered_increments,
    record_ask_usage,
    record_capture_usage,
    record_unmetered_increment,
    record_write_ops,
    report_unmetered_increment,
)
from tortoise.quota import QuotaCheckError

#: The six swallow-site lane tokens (leg 3's vocabulary — #3981). Leg 2 reuses
#: it verbatim so the record and the alert are joinable on ONE vocabulary,
#: rather than a second, parallel mapping that could drift.
SIX_LANES = ("write_op", "object_write_op", "subject_write_op",
             "capture_ledger", "mcp_write_op", "ask_ledger")

_MIGRATION = (Path(__file__).resolve().parents[1] / "supabase" / "migrations"
              / "20260927000001_metering_unmetered_increments.sql")


@pytest.fixture
def reg(request):
    """The imported ``reg_org`` fixture, resolved BY NAME.

    Not a parameter: importing a fixture from another test module puts its name
    in this module's namespace, and using it as a parameter is an F811
    redefinition at every call site. The repo's
    ``test_metering_window_admission.py`` resolves it the same way.
    """
    return request.getfixturevalue("reg_org")


@pytest.fixture
def sb(request):
    """The imported ``supabase_mode`` fixture, resolved by name (see ``reg``)."""
    return request.getfixturevalue("supabase_mode")


@pytest.fixture
def registry_lane(monkeypatch):
    """Force the embedded/registry lane deterministically (no Supabase)."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")


# ── THE ACCEPTANCE TEST ─────────────────────────────────────────────────────


def test_a_dropped_increment_is_distinguishable_from_a_zero_increment(
        reg, registry_lane):
    """THE #4779 acceptance test: the drop and the zero must NOT read the same.

    Mutation caught: removing ``record_unmetered_increment`` from
    ``report_unmetered_increment`` (the "leg 2 missing" tree). Then the dropped
    org's ``unmetered`` is 0 — identical to the control — and the comparison
    below REDs, naming the pair.

    Note what is asserted and why: the spend figures are EQUAL and ZERO for
    both orgs (a count is not spend), while the unmetered counts DIFFER. That
    is the whole change — before it, both orgs produced the same observation,
    so neither the cap nor an operator could see the drop.
    """
    sdk, tid = reg
    control = sdk.org_create(name="4779-control")["id"]

    # An inverted billing interval → ``_current_period`` RAISES (no
    # calendar-month fallback, #3825/D10). This is the REAL failure, not a stub.
    _anchor(sdk._get_registry(), tid, SUB_END, SUB_START, "sub-4779-inverted")

    window = _period(SUB_START, SUB_END)
    for _ in range(3):
        with pytest.raises(QuotaCheckError):
            record_write_ops(tid, tier="pro")
        # ...the caller's handler, verbatim in shape: absorb, then report.
        report_unmetered_increment("write_op", tid,
                                   QuotaCheckError("unresolvable window"))

    dropped_spend = get_cohort_spend_usd([tid], window)
    control_spend = get_cohort_spend_usd([control], window)
    dropped_unmetered = get_unmetered_increment_total([tid])
    control_unmetered = get_unmetered_increment_total([control])

    # The ledger the cap reads is legitimately zero for BOTH — the count must
    # never become a cap input (#4779 constraint 1).
    assert dropped_spend == 0.0
    assert control_spend == 0.0
    # ...and the representation is what tells them apart.
    assert dropped_unmetered == 3, "the drop is not counted — leg 2 is missing"
    assert control_unmetered == 0
    assert (dropped_spend, dropped_unmetered) != (control_spend, control_unmetered)

    rows = get_unmetered_increments(tid)
    assert len(rows) == 1
    (row,) = rows
    assert row["lane"] == "write_op"
    assert row["drop_class"] == DROP_CLASS_WINDOW_UNRESOLVABLE
    assert row["increments"] == 3
    # The diagnostic payload: a record that cannot name what broke is a filing
    # defect (#5047's direction), so the error CLASS rides the row.
    assert row["last_error_type"] == "QuotaCheckError"
    assert row["first_observed_at"]
    assert row["last_observed_at"]
    assert get_unmetered_increments(control) == []


def test_every_swallow_lane_carries_the_representation(reg, registry_lane):
    """Leg 2 rides EVERY leg-3 lane — six sites, six rows (one per lane+class).

    Mutation caught: making the representation LANE-CONDITIONAL inside
    ``report_unmetered_increment`` (e.g. writing rows for five of the six
    tokens), which would leave a dropped increment on that lane still
    indistinguishable from zero. The complement — wiring the CALL does not
    happen at all for one swallow helper (``mcp_server``'s fallback, say) — is
    pinned by ``tests/test_metering_window_admission.py``, which drives the real
    helpers; this test feeds the lane tokens to the shared reporter, so it cannot
    see a missing call site.
    """
    _sdk, tid = reg
    for lane in SIX_LANES:
        report_unmetered_increment(lane, tid, QuotaCheckError("x"))
    rows = get_unmetered_increments(tid)
    assert {r["lane"] for r in rows} == set(SIX_LANES)
    assert all(r["drop_class"] == DROP_CLASS_WINDOW_UNRESOLVABLE
               for r in rows)
    assert all(r["increments"] == 1 for r in rows)
    assert get_unmetered_increment_total([tid]) == len(SIX_LANES)


# ── The second drop class ───────────────────────────────────────────────────


@pytest.mark.parametrize("writer,lane,extra", [
    (lambda tid: record_write_ops(tid, tier="pro"), "write_op", {}),
    (lambda tid: record_ask_usage(tid, cost_usd=0.5), "ask_ledger", {}),
    (lambda tid: record_capture_usage(tid, cost_usd=0.5), "capture_ledger", {}),
])
def test_the_increment_rpc_failure_is_represented_with_its_own_class(
        reg, registry_lane, monkeypatch, writer, lane, extra):
    """A KNOWN-WINDOW drop (the increment write failed) is represented too.

    This is the residual the four stale docstrings claimed #3824 represented.
    It is a different ``drop_class``, because the window IS known here — the
    operator's fix is different (retry/repair the RPC, not repair the anchor).

    Mutation caught: leaving the writer's ``except`` as a bare
    ``_logger.warning`` + ``return None`` (the pre-#4779 shape), which is
    exactly the state the issue calls indistinguishable from zero.
    """
    sdk, tid = reg
    _break_increment_only(monkeypatch, sdk)
    assert writer(tid) is None
    rows = get_unmetered_increments(tid)
    assert len(rows) == 1, rows
    assert rows[0]["lane"] == lane
    assert rows[0]["drop_class"] == DROP_CLASS_INCREMENT_WRITE_UNCONFIRMED
    assert rows[0]["increments"] == 1
    assert rows[0]["last_error_type"] == "RuntimeError"


def test_the_two_drop_classes_are_separate_rows(reg, registry_lane,
                                                monkeypatch):
    """The PK includes ``drop_class``: one lane can carry BOTH episodes.

    Mutation caught: dropping ``drop_class`` from the key (the registry MERGE's
    identity, mirrored by the SQL PK's third column) — the two causes would
    merge into one count and the operator could not tell which fix applies.
    """
    sdk, tid = reg
    _break_increment_only(monkeypatch, sdk)
    assert record_write_ops(tid, tier="pro") is None
    report_unmetered_increment("write_op", tid, QuotaCheckError("x"))
    classes = {r["drop_class"]: r["increments"]
               for r in get_unmetered_increments(tid)}
    assert classes == {DROP_CLASS_INCREMENT_WRITE_UNCONFIRMED: 1,
                       DROP_CLASS_WINDOW_UNRESOLVABLE: 1}


# ── Observation bounds, vocabulary guards, no-op paths ──────────────────────


def test_first_observed_at_is_preserved_and_last_advances(reg, registry_lane):
    """The since-when survives every later drop; the last-seen advances.

    Mutation caught: collapsing both onto ``now()`` (the since-when then reports
    the LATEST drop, so "how long has this been happening" is unanswerable), or
    dropping ``last_observed_at`` from the update (the reader cannot then tell a
    repaired org from an ongoing one).
    """
    sdk, tid = reg
    reg = sdk._get_registry()
    assert record_unmetered_increment(
        "write_op", tid, DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) == 1
    # Pin a synthetic since-when, then drop again.
    reg.query(
        "MATCH (u:MeteringUnmeteredIncrement {org_id: $tid, lane: $lane, "
        "       drop_class: $cls}) "
        "SET u.first_observed_at = '2026-01-01T00:00:00+00:00', "
        "    u.last_observed_at = '2026-01-01T00:00:00+00:00'",
        params={"tid": tid, "lane": "write_op",
                "cls": DROP_CLASS_WINDOW_UNRESOLVABLE},
    )
    assert record_unmetered_increment(
        "write_op", tid, DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("y")) == 2
    (row,) = get_unmetered_increments(tid)
    assert row["first_observed_at"] == "2026-01-01T00:00:00+00:00"
    assert row["last_observed_at"] != "2026-01-01T00:00:00+00:00"


def test_an_undeclared_drop_class_writes_nothing(reg, registry_lane):
    """The drop vocabulary is CLOSED on both lanes.

    The SQL lane enforces it with a CHECK; the registry lane must refuse it too,
    or the two modes disagree on what a valid record is (and the Python lane
    would write rows the SQL lane would reject on the next deploy).

    Mutation caught: dropping the ``drop_class not in _DROP_CLASSES`` guard.
    """
    _sdk, tid = reg
    assert record_unmetered_increment(
        "write_op", tid, "some_invented_class", QuotaCheckError("x")) is None
    assert get_unmetered_increments(tid) == []


@pytest.mark.parametrize("org", [None, ""])
def test_no_org_context_is_a_noop(reg, registry_lane, org):
    """The stdio/selfhost shape: no org, no record (and no raise).

    Mutation caught: writing an org-less row — it could never be read alongside
    anything, and it would make ``_``-shaped keys that collapse unrelated lanes.
    """
    assert record_unmetered_increment(
        "write_op", org, DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) is None


def test_the_representation_never_raises_and_logs_its_own_failure(
        reg, registry_lane, monkeypatch, caplog):
    """A failed representation is logged, not raised — and the alert still fires.

    It is called from handlers whose whole point is that metering cannot block a
    request (``report_unmetered_increment`` must not raise either), so this
    branch is a stated limit rather than an exception: with the control plane
    down, the leg-3 alert (a different channel) is the backstop.

    Mutation caught: letting the representation's failure propagate — every
    swallowed drop would become the user-facing 500 the #3981 ruling forbids.
    """
    def _broken():
        raise RuntimeError("registry down")

    monkeypatch.setattr(metering_mod, "_reg_sdk", _broken)
    with caplog.at_level(logging.WARNING, logger="tortoise.metering"):
        assert record_unmetered_increment(
            "write_op", "org-4779", DROP_CLASS_WINDOW_UNRESOLVABLE,
            QuotaCheckError("x")) is None
        # ...and the reporter (which calls it) must not raise either.
        report_unmetered_increment("write_op", "org-4779", QuotaCheckError("x"))
    assert any("representation failed" in r.message for r in caplog.records), (
        [r.message for r in caplog.records]
    )


# ── The readers ─────────────────────────────────────────────────────────────


def test_readers_fail_closed_rather_than_reading_as_zero(reg, registry_lane, monkeypatch):
    """An unreadable cohort must NOT read as "zero drops".

    ``[]``/``0`` on a failure would manufacture the exact false zero this whole
    surface exists to remove — the reader could not tell "no drops" from "could
    not read". Both readers follow ``get_cohort_spend_usd``'s fail-closed
    posture, NOT ``get_ask_usage``'s degrade-to-zero one.

    Mutation caught: wrapping each reader's query in
    ``except Exception: return []/0`` (the "never 500" reflex applied to a
    surface where silence IS the defect).
    """
    def _broken():
        raise RuntimeError("registry down")

    monkeypatch.setattr(metering_mod, "_reg_sdk", _broken)
    with pytest.raises(RuntimeError):
        get_unmetered_increments("org-4779")
    with pytest.raises(RuntimeError):
        get_unmetered_increment_total(["org-4779"])


def test_the_supabase_lane_readers_fail_closed_too(sb, monkeypatch):
    """The FAIL-CLOSED readers are fail-closed on BOTH deployments.

    The embedded half is pinned by
    ``test_readers_fail_closed_rather_than_reading_as_zero``. The Supabase half
    is a DIFFERENT code path (``get_control_plane()`` + a
    ``metering_unmetered_for_org`` / ``metering_unmetered_total`` RPC), and
    returning ``[]``/``0`` there would manufacture the false zero just as
    effectively — an unreadable cohort would read as "no drops".

    Mutation caught: wrapping the Supabase branch in
    ``except Exception: return []`` / ``return 0`` (the never-500 reflex, which
    on this surface is worse than a 500).
    """
    class _BrokenCP:
        def rpc_value(self, fn, params=None):
            raise RuntimeError(f"control plane down: {fn}")

    monkeypatch.setattr(sb, "get_control_plane", lambda: _BrokenCP())

    with pytest.raises(RuntimeError, match="control plane down"):
        get_unmetered_increments("org-4779")
    with pytest.raises(RuntimeError, match="control plane down"):
        get_unmetered_increment_total(["org-4779"])


def test_an_empty_cohort_reads_zero(reg, registry_lane):
    """No orgs is a legitimate 0 — distinct from an unreadable cohort."""
    assert get_unmetered_increment_total([]) == 0
    assert get_unmetered_increment_total(None) == 0
    assert get_unmetered_increments("") == []


# ── Constraint 1: the count never becomes a cap input ──────────────────────


def test_the_count_never_leaks_into_the_spend_read(reg, registry_lane):
    """``get_cohort_spend_usd`` reads ONLY ``ask_cost_usd + capture_cost_usd``.

    The org here has REAL measured spend AND a non-zero unmetered count, so
    this is a live divergence rather than an all-zero comparison. An
    unattributable count folded into a spend ceiling is the behaviour change
    #4779 puts explicitly out of scope.

    Mutation caught: joining the representation into the cohort SUM, or
    reading ``increments`` as if it were spend.
    """
    _sdk, tid = reg
    period = _current_period(tid)
    assert record_ask_usage(tid, calls=1, cost_usd=2.5) is not None
    assert record_capture_usage(tid, calls=1, cost_usd=1.25) is not None
    for _ in range(7):
        report_unmetered_increment("write_op", tid, QuotaCheckError("x"))

    assert get_cohort_spend_usd([tid], period) == pytest.approx(3.75)
    assert get_unmetered_increment_total([tid]) == 7


# ── The derived-bookkeeping boundary ────────────────────────────────────────


def test_derived_threshold_bookkeeping_is_not_a_dropped_increment(
        reg, registry_lane, monkeypatch, caplog):
    """A pricing-drift failure AFTER a landed increment is NOT a drop.

    ``record_write_ops`` computes the allowance and threshold events from
    ``pricing.json``, which RAISES on a missing required key (``pricing.py:63``).
    That work used to sit inside the increment's ``try``, so a config drift made
    the writer report ``None`` — claiming a drop for an increment that had
    committed. The representation would have made that false claim DURABLE, so
    the derived work now has its own guard.

    Mutation caught: moving the allowance/threshold block back inside the
    increment ``try`` (or letting its failure reach the representation) — the
    increment then reads as dropped, and a representation row appears.
    """
    _sdk, tid = reg

    def _broken_allowance(_tier):
        raise KeyError("pricing.json tier 'pro' missing required limit keys")

    monkeypatch.setattr(metering_mod, "_ops_allowance", _broken_allowance)
    with caplog.at_level(logging.WARNING, logger="tortoise.metering"):
        result = record_write_ops(tid, tier="pro")
    assert result is not None, "a landed increment was reported as a drop"
    assert result["write_ops"] == 1
    assert get_unmetered_increments(tid) == [], (
        "an increment that LANDED was represented as unmeterable"
    )
    assert any("threshold bookkeeping failed" in r.message
               for r in caplog.records)


# ── Supabase lane ───────────────────────────────────────────────────────────


def _fake_cp(*, org_id="org-4779", spend_rows=()):
    from tests.fake_control_plane import FakeControlPlane

    return FakeControlPlane({
        "organizations": [{"id": org_id, "subscription_id": "sub-4779",
                           "current_period_start": SUB_START,
                           "current_period_end": SUB_END}],
        "metering_records": list(spend_rows),
        "metering_unmetered_increments": [],
    })


def test_supabase_lane_writes_and_reads_the_representation(sb, monkeypatch):
    """The Supabase lane has the same behaviour, through the RPC seams.

    Mutation caught: implementing only the registry lane (or vice versa) — the
    two modes are the production and the embedded deployment, so one of them
    silently losing the representation is the defect this table removes.
    """
    fake = _fake_cp()
    monkeypatch.setattr(sb, "get_control_plane", lambda: fake)

    assert record_unmetered_increment(
        "write_op", "org-4779", DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) == 1
    assert record_unmetered_increment(
        "write_op", "org-4779", DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("y")) == 2
    rows = get_unmetered_increments("org-4779")
    assert [(r["lane"], r["drop_class"], r["increments"]) for r in rows] == [
        ("write_op", DROP_CLASS_WINDOW_UNRESOLVABLE, 2)]
    assert rows[0]["last_error_type"] == "QuotaCheckError"
    assert get_unmetered_increment_total(["org-4779"]) == 2
    # ...and it went through the declared RPC, not a raw table write.
    assert fake.rpc_calls[-1][0] == "metering_unmetered_total"


def test_supabase_lane_refuses_an_undeclared_class_and_an_unknown_org(
        sb, monkeypatch):
    """The OUTCOME through the seam: an undeclared class and an unknown org both
    leave NO row behind (``fake.tables[...] == []``).

    Stated carefully, because the outcome is layered: the Python vocabulary
    guard and the fake's own check also refuse these, and
    ``record_unmetered_increment`` never raises, so this test pins the OUTCOME
    rather than any single layer. What it would catch is a change that lets
    either call STORE a row.

    The layers themselves are pinned elsewhere, and by mutation: the SQL CHECK by
    the pglite suite (``an UNDECLARED drop_class was accepted``) and the
    Python<->SQL vocabulary contract test; the org FK by the same suite's
    ``the org FK is missing — a row was written for an unknown org`` probe.
    """
    fake = _fake_cp()
    monkeypatch.setattr(sb, "get_control_plane", lambda: fake)

    assert record_unmetered_increment(
        "write_op", "org-4779", "some_invented_class",
        QuotaCheckError("x")) is None
    assert record_unmetered_increment(
        "write_op", "org-absent", DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) is None
    assert fake.tables["metering_unmetered_increments"] == []


def test_supabase_lane_count_never_leaks_into_the_spend_read(sb, monkeypatch):
    """Constraint 1 again, in the lane where the cap actually runs.

    The org carries measured spend (12.5) AND an unmetered count (7). The
    spend read must return 12.5 exactly — not 19.5.

    Mutation caught: adding the count to ``metering_cohort_spend``'s SUM.
    """
    fake = _fake_cp(spend_rows=[{
        "org_id": "org-4779", "period_start": SUB_START,
        "period_end": SUB_END, "period": "2026-09", "ask_cost_usd": 12.5}])
    monkeypatch.setattr(sb, "get_control_plane", lambda: fake)

    for _ in range(7):
        assert record_unmetered_increment(
            "write_op", "org-4779", DROP_CLASS_WINDOW_UNRESOLVABLE,
            QuotaCheckError("x")) is not None
    assert get_cohort_spend_usd(["org-4779"], _period(SUB_START, SUB_END)) \
        == pytest.approx(12.5)
    assert get_unmetered_increment_total(["org-4779"]) == 7


# ── The vocabulary contract ─────────────────────────────────────────────────


def test_the_declared_classes_match_the_migration_check():
    """Python's declared vocabulary ⇄ the migration's CHECK — one set.

    The two lanes must agree: a class Python accepts but the CHECK refuses
    fails only in production (the Supabase deployment), where the write is
    swallowed as best-effort and the row silently never appears. Pinning the two
    together is what makes "declared vocabulary" true rather than aspirational.

    Mutation caught: adding/renaming a class on one side only.
    """
    sql = _MIGRATION.read_text(encoding="utf-8")
    check = re.search(r"CHECK \(drop_class IN \(([^)]*)\)\)", sql)
    assert check, "the declared-vocabulary CHECK is gone from the migration"
    declared = set(re.findall(r"'([a-z_]+)'", check.group(1)))
    assert declared == set(metering_mod._DROP_CLASSES), (
        f"migration declares {declared}, metering.py declares "
        f"{set(metering_mod._DROP_CLASSES)}"
    )


# ── The batch, the lane parity, and the two ordering rules (review cycle 1) ──


def test_a_failed_batch_is_counted_at_its_batch_size(
        reg, registry_lane, monkeypatch):
    """A lost BATCH is N lost increments, not one.

    The writers take a batch size (``record_write_ops(n=…)``,
    ``record_ask_usage(calls=…)``, ``record_capture_usage(calls=…)``) and the
    SQL RPC plus the fake already carry ``p_n``. The surface's ONE job is the
    count, so recording a hardcoded 1 per failed call would understate the loss
    by the batch factor on a lane whose whole purpose is "how much did we lose".

    Mutation caught: not threading the caller's batch into
    ``record_unmetered_increment`` (the pre-review shape).
    """
    sdk, tid = reg
    _break_increment_only(monkeypatch, sdk)

    assert record_write_ops(tid, tier="pro", n=3) is None
    assert get_unmetered_increment_total([tid]) == 3, (
        "the write-op batch was counted as one increment"
    )

    assert record_ask_usage(tid, calls=4, cost_usd=0.5) is None
    assert record_capture_usage(tid, calls=5, cost_usd=0.5) is None
    counted = {r["lane"]: r["increments"] for r in get_unmetered_increments(tid)}
    assert counted == {"write_op": 3, "ask_ledger": 4, "capture_ledger": 5}, (
        counted
    )


def test_the_supabase_lane_threads_the_batch_size(sb, monkeypatch):
    """The same batch is carried over the RPC seam (``p_n``), not collapsed.

    Mutation caught: passing the batch on the registry lane only — the two
    deployment modes would then disagree on the count.
    """
    monkeypatch.setattr(sb, "get_control_plane", lambda: _fake_cp())
    assert record_unmetered_increment(
        "write_op", "org-4779", DROP_CLASS_INCREMENT_WRITE_UNCONFIRMED,
        QuotaCheckError("x"), 4) == 4
    assert record_unmetered_increment(
        "ask_ledger", "org-4779", DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) == 1  # the default is ONE, not zero


@pytest.mark.parametrize("lane", ["", "   ", "\t", "\n", " \t\r\n"])
@pytest.mark.parametrize("org", ["   ", "\t", "\xa0"])
def test_a_blank_lane_or_org_is_refused_on_the_embedded_lane(
        reg, registry_lane, lane, org):
    """A blank ``lane``/``org_id`` is refused HERE, with the same test the SQL
    lane applies (``btrim(key, blank_chars)``, mirrored exactly — see
    ``test_the_blank_set_is_pythons_exact_whitespace_set``).

    Both keys use the one comparison: ``org_id`` additionally has to name a real
    org, but that is the FK's job on the Supabase lane, not a second blank test
    here — a whitespace-only org would otherwise create a durable node no reader
    could join to ``organizations``.

    Mutation caught: guarding only ``not org_id``/``not lane`` (falsy), not the
    blank string; and narrowing either check to ASCII spaces (the TAB/NBSP cases
    below then write a row the Supabase lane refuses).
    """
    _sdk, tid = reg
    assert record_unmetered_increment(
        lane, tid, DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) is None
    assert record_unmetered_increment(
        "write_op", org, DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) is None
    assert get_unmetered_increments(tid) == []


def test_the_blank_set_is_pythons_exact_whitespace_set():
    r"""CROSS-LANGUAGE CONTRACT: the SQL blank set IS Python's ``str.isspace()``.

    The embedded lane compares keys with a bare ``str.strip()``; the Supabase
    lane compares them with ``btrim(key, blank_chars)``. If those two sets differ
    by even one character, the two deployment modes disagree about whether a
    record exists — the defect this guard closes (a bare ``btrim(x)``, which
    removes ASCII spaces only, refused a TAB-only lane on the embedded lane and
    WROTE it on the Supabase lane).

    The migration's literal is decoded with POSTGRES ESCAPE SEMANTICS for the
    declared parser version and checked to equal EXACTLY Python's whitespace set,
    in both directions, so a hand-edited copy cannot drift.

    The literal is ALSO required to spell U+000B as ``\u000B``, asserted
    separately below. That second assertion is a POLICY, not a parser
    disagreement (it corrects an earlier claim in this docstring): PostgreSQL 17,
    the version ``supabase/config.toml`` declares, implements
    ``case 'v': return '\v';`` in
    ``src/backend/parser/scan.l::unescape_single_char`` (commit ae6d06f096), so
    ``E'\v'`` and ``E'\u000B'`` compose the same character on this project's
    parser — no SQL probe on a PG17-or-later engine can object to ``\v``. It is
    nonetheless held to ``\u000B`` because ``\v`` does not exist in PostgreSQL 16
    and earlier, and the manual's escape table omits it while stating that any
    other character after a backslash "is taken literally" — a trap for a reader
    who consults the documentation.

    Mutation caught: dropping or adding any character in the migration's literal
    (e.g. removing ``\u00A0`` — an NBSP-only key is then written through the RPC
    while the embedded lane refuses it), any escape this decoder reads
    differently from the parser, and the ``\v`` spelling — which the equality
    check CANNOT see, because on this parser it decodes to the intended U+000B,
    so the explicit assertion below is what catches it.
    """
    sql = _MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"blank_chars constant text := E'([^']*)'", sql)
    assert m, "the migration no longer declares a blank_chars constant"
    # The declared parser's escape set (PostgreSQL 17): \uXXXX / \UXXXXXXXX,
    # \xhh, \ooo, and the single-character escapes b f n r t v; any other
    # character after a backslash is itself.
    decoded = re.sub(
        r"\\u([0-9A-Fa-f]{4})|\\U([0-9A-Fa-f]{8})|\\x([0-9A-Fa-f]{1,2})"
        r"|\\([0-7]{1,3})|\\(.)",
        lambda g: (
            chr(int(g.group(1), 16)) if g.group(1)
            else chr(int(g.group(2), 16)) if g.group(2)
            else chr(int(g.group(3), 16)) if g.group(3)
            else chr(int(g.group(4), 8)) if g.group(4)
            else {"t": "\t", "n": "\n", "f": "\x0c", "r": "\r",
                  "b": "\x08", "v": "\x0b", "\\": "\\", "'": "'"}
            .get(g.group(5), g.group(5))
        ),
        m.group(1))
    python_set = {c for c in map(chr, range(0x10000)) if c.isspace()}
    assert set(decoded) == python_set, (
        "the SQL blank set and Python's str.isspace() disagree: "
        f"sql-only={sorted(set(decoded) - python_set)!r} "
        f"python-only={sorted(python_set - set(decoded))!r}"
    )
    # POLICY (see the docstring): PostgreSQL 17 decodes `\v` to U+000B, so the
    # equality check above accepts it — the spelling is held to `\u000B` because
    # it is undocumented and absent before PostgreSQL 17.
    assert "\\v" not in m.group(1), (
        "the vertical tab must be spelled \\u000B, not \\v: PostgreSQL only "
        "implements the \\v escape from version 17, and the manual omits it "
        "(any other escape 'is taken literally')"
    )


@pytest.mark.parametrize("lane", ["\t", "\n", "\x0b", "\xa0", "\u2000", "\u001c"])
def test_a_unicode_whitespace_lane_is_refused_like_the_sql_lane(
        reg, registry_lane, lane):
    """A whitespace-only LANE is refused on the embedded lane — every character
    the SQL ``blank_chars`` set covers, not just ASCII spaces.

    Mutation caught: reverting this guard to a bare ``btrim(x)`` /
    ``.strip(" ")`` in either lane (the SQL half is pinned by the SQL suite's
    TAB/NBSP lane probes, this half by these cases).
    """
    _sdk, tid = reg
    assert record_unmetered_increment(
        lane, tid, DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) is None, lane
    assert get_unmetered_increments(tid) == []


def test_a_batch_that_cannot_be_counted_never_makes_the_writer_raise(
        reg, registry_lane):
    """``n`` is floored from ANY value, and the floor never raises.

    ``int(float('inf'))`` raises ``OverflowError``, and the three writers pass
    caller-supplied ``n``/``calls`` (``record_capture_usage(calls=…)``) into
    this function. The floor sits OUTSIDE the write ``try``, so an uncaught
    OverflowError would escape a function whose whole contract is that it never
    raises — from a best-effort metering writer, i.e. into a request path.

    Mutation caught: catching only ``(TypeError, ValueError)`` at the floor.
    """
    _sdk, tid = reg
    counts = [
        record_unmetered_increment(
            "write_op", tid, DROP_CLASS_WINDOW_UNRESOLVABLE,
            QuotaCheckError("x"), bad)
        for bad in (float("inf"), float("-inf"), float("nan"), object())
    ]
    # Four calls, each floored to ONE (cumulative 1,2,3,4) — i.e. not one of
    # them raised, and none recorded a nonsense count.
    assert counts == [1, 2, 3, 4], counts

    # ...and a writer whose caller supplied a non-finite batch still records the
    # drop instead of raising out of the handler.
    assert record_unmetered_increment(
        "capture_ledger", tid, DROP_CLASS_INCREMENT_WRITE_UNCONFIRMED,
        QuotaCheckError("x"), float("inf")) == 1
    assert get_unmetered_increment_total([tid]) == 5


def test_a_failed_read_back_is_not_a_dropped_increment(
        reg, registry_lane, monkeypatch):
    """#925 parity on the embedded lane: only the READ-BACK failed.

    The ``MERGE`` committed, so the increment LANDED; a read-back blip must not
    be represented as an unmeterable increment. This is the same guard #925
    established on the Supabase lane
    (``supabase_control.metering_increment`` returns the known delta ``n``
    rather than raising), and #4779 is what would have made the false claim
    DURABLE.

    Mutation caught: keeping the read-back inside the increment's ``try`` — the
    represented count then includes increments that are on the ledger.
    """
    sdk, tid = reg
    real_reg = sdk._get_registry()

    class _ReadBackBrokenRegistry:
        def query(self, cypher, *args, **kwargs):
            if "RETURN m.write_ops, m.nodes_written" in cypher:
                raise RuntimeError("read-back blip")
            return real_reg.query(cypher, *args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_reg, name)

    class _StubSDK:
        def _get_registry(self):
            return _ReadBackBrokenRegistry()

    monkeypatch.setattr(metering_mod, "_reg_sdk", lambda: _StubSDK())
    result = record_write_ops(tid, tier="pro", n=2)
    assert result is not None, "a landed increment was reported as a drop"
    assert result["write_ops"] == 2, (
        "the known delta is the fallback when the read-back fails"
    )
    assert get_unmetered_increments(tid) == [], (
        "an increment that LANDED was represented as unmeterable"
    )
    # ...and the increment really is on the ledger (the MERGE committed).
    on_ledger = real_reg.query(
        "MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) "
        "RETURN m.write_ops",
        params={"tid": tid, "pstart": _current_period(tid).start_iso},
    ).result_set
    assert on_ledger and int(on_ledger[0][0]) == 2


def test_the_alert_is_dispatched_even_when_the_representation_raises(
        reg, registry_lane, monkeypatch):
    """The leg-3 alert must not be gated on the leg-2 write.

    Their channels are independent by design (the alert is R2 + GitHub +
    Telegram, not the control plane), and the control plane is the usual cause
    of a window-unresolvable drop — so a failure of the representation must not
    be able to swallow the alert.

    Mutation caught: calling ``record_unmetered_increment`` INSIDE the
    ``contextlib.suppress`` ahead of the alert — an exception there then skips
    the ERROR log and the alert entirely.

    The pair of assertions pins the ORDER two ways: a representation moved
    inside the ``suppress`` is swallowed (no raise → ``pytest.raises`` fails),
    and one moved bare ahead of the alert skips it (``sent`` stays empty).
    """
    import tortoise.operator_alert as operator_alert

    sent: list[tuple] = []
    monkeypatch.setattr(
        operator_alert, "alert_unmetered_increment",
        lambda lane, org_id, error: sent.append((lane, org_id, error)))
    monkeypatch.setattr(
        metering_mod, "record_unmetered_increment",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("cp down")))

    # ``record_unmetered_increment`` never raises by contract; this monkeypatch
    # breaks that contract to prove the alert does not depend on it.
    with pytest.raises(RuntimeError, match="cp down"):
        report_unmetered_increment("write_op", "org-4779", QuotaCheckError("x"))
    assert len(sent) == 1, "the representation's failure swallowed the alert"


def test_the_drop_is_represented_after_the_per_org_lock_is_released(
        reg, registry_lane, monkeypatch):
    """The representation write happens OUTSIDE the per-org increment lock.

    The lock serializes the ask/capture lanes for one org. Doing a blocking
    control-plane round trip inside it would hold every other increment for that
    org behind a control plane that is already failing — and that is exactly the
    state that produced the failure being represented.

    Mutation caught: recording the drop inside ``_record_*_usage_locked`` (the
    pre-review shape).
    """
    sdk, tid = reg
    _break_increment_only(monkeypatch, sdk)
    seen: list[bool] = []

    def _probe(lane, org_id, drop_class, error, n=1):
        seen.append(metering_mod._ask_meter_lock(org_id).locked())
        return None

    monkeypatch.setattr(metering_mod, "record_unmetered_increment", _probe)
    assert record_ask_usage(tid, calls=2, cost_usd=0.5) is None
    assert record_capture_usage(tid, calls=2, cost_usd=0.5) is None
    assert seen == [False, False], (
        "the representation was written while the per-org lock was held"
    )


def test_the_fake_refuses_an_explicit_null_p_n_like_the_migration(
        sb, monkeypatch):
    """Parity contract with the RPC: an OMITTED ``p_n`` defaults to 1; an explicit
    NULL is refused (``IF p_n IS NULL OR p_n < 1 THEN RAISE``); a key that is
    blank under Python's whitespace set is refused for BOTH keys (the RPC mirrors
    that set exactly via ``blank_chars``); and an unknown org is refused by the FK.

    The fake is the only schema the Python lane runs against, so a fake that
    collapsed NULL onto the default would encode a write the real RPC rejects,
    and any test of that case would pin the wrong behaviour. The same applies in
    the other direction for the blank set: a bare ASCII-space ``.strip(" ")``
    here would accept a key ``btrim(key, blank_chars)`` refuses.
    """
    fake = _fake_cp()
    monkeypatch.setattr(sb, "get_control_plane", lambda: fake)

    # Written through the seam, the default is ONE and no NULL is ever sent.
    assert record_unmetered_increment(
        "write_op", "org-4779", DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x")) == 1

    with pytest.raises(RuntimeError, match="p_n must be >= 1"):
        fake.rpc("metering_record_unmetered", {
            "p_org_id": "org-4779", "p_lane": "write_op",
            "p_drop_class": "window_unresolvable",
            "p_error_type": "QuotaCheckError", "p_n": None,
        })

    for blank in ("\t", "\xa0", "\u2000"):
        with pytest.raises(RuntimeError, match="p_lane is required"):
            fake.rpc("metering_record_unmetered", {
                "p_org_id": "org-4779", "p_lane": blank,
                "p_drop_class": "window_unresolvable",
                "p_error_type": "QuotaCheckError", "p_n": 1,
            })
        with pytest.raises(RuntimeError, match="p_org_id is required"):
            fake.rpc("metering_record_unmetered", {
                "p_org_id": blank, "p_lane": "write_op",
                "p_drop_class": "window_unresolvable",
                "p_error_type": "QuotaCheckError", "p_n": 1,
            })

    # An org id that EXISTS but is unknown to the FK is refused by the FK (the
    # RPC's blank guard is a pre-flight, not the integrity check).
    with pytest.raises(RuntimeError, match="org FK violation"):
        fake.rpc("metering_record_unmetered", {
            "p_org_id": "org-does-not-exist", "p_lane": "write_op",
            "p_drop_class": "window_unresolvable",
            "p_error_type": "QuotaCheckError", "p_n": 1,
        })


def test_a_negative_or_none_batch_is_floored_at_one(reg, registry_lane):
    """``n`` is floored at 1: a zero-count record is the state the surface
    exists to distinguish, and the SQL lane refuses it.

    Mutation caught: forwarding ``n`` verbatim, so a caller passing 0/None
    writes a row the SQL lane rejects (or, on the registry lane, one that reads
    as "nothing was lost").
    """
    _sdk, tid = reg
    assert record_unmetered_increment(
        "write_op", tid, DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x"), 0) == 1
    assert record_unmetered_increment(
        "write_op", tid, DROP_CLASS_WINDOW_UNRESOLVABLE,
        QuotaCheckError("x"), None) == 2
