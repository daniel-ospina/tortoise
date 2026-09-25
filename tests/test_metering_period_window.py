"""#3825 (lane B7) — the metering ledger expresses a PERIOD BOUNDARY.

WHAT THIS FILE IS
-----------------
D10 (adopted) fixes the cost meter's window as **the subscription's own billing
period**, anchored to ``organizations.subscription_id`` — not a calendar month,
not a rolling 30 days. D13 (adopted) closes the gap D10 left open: an org with
no subscription meters on the **calendar month in UTC**. This file pins both.

THE FLEET BAR FOR THIS FILE
---------------------------
Every test NAMES THE MUTATION that must make it RED, and every test drives the
REAL writer (``metering.record_write_ops`` / ``record_ask_usage`` /
``record_capture_usage``) and the REAL reader (``metering.get_cohort_spend_usd``
/ the ``metering_cohort_spend`` seam) — never a resolver's return value in
isolation. `(c.3)` of the #3825 scope rejects a test that asserts a resolver
without a real increment+read: such a test stays GREEN under the mutations
T2/T4/T7/T9 name, which is evidence about a spelling rather than a behaviour.

SCOPE MAPPING — the 13 named mutations in ``(c.1)``:
  T1..T12   implemented below, one test per named mutation.
  T13       **NOT APPLICABLE** (option (b), the analytics stream was not
            chosen). T13 is explicitly conditional — "*(option (b) only)*" —
            and this change keeps the DURABLE ledger as the window authority
            precisely because the analytics sink's durability is why (b) was
            rejected: #3677 ("every production analytics event was written to
            an ephemeral VM and lost") and #3749 are both still OPEN. Making
            the cap trust a stream whose loss would be a cap that never trips
            is the fail-open D10/D13 exist to prevent. See the report.

WHY SOME WINDOWS BELOW ARE 10 DAYS
----------------------------------
Several tests use two windows that BOTH fall inside one calendar month. That
is deliberate and is the sharpest possible form of the mutation: it is exactly
what makes a month-keyed PK collide. A "billing period" is an arbitrary
interval (Stripe supports daily/weekly/monthly/annual); its LENGTH is
irrelevant to the ledger contract, while the fact that a month label cannot
identify it is the whole issue.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import UTC, datetime

import pytest

#: A subscription billing period used by the anchor tests (a mid-month start —
#: the 3rd, NOT the 1st — so a calendar-month fallback is visibly wrong).
SUB_START = "2026-09-03T00:00:00+00:00"
SUB_END = "2026-10-03T00:00:00+00:00"


class _FrozenDatetime(datetime):
    """``datetime`` with ``now`` pinned.

    Subclasses the REAL class rather than standing in for it, because
    ``metering._anchor_instant`` calls ``fromisoformat``/``fromtimestamp`` and
    does ``isinstance`` checks against the module-global ``datetime`` — a bare
    stub would break anchor parsing while the clock is patched, and the test
    would fail for a reason unrelated to its mutation.
    """

    _frozen: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        return cls._frozen


@pytest.fixture
def reg_org(monkeypatch, tmp_path):
    """A registry (embedded) SDK with one org — the lane the cap's writer and
    reader both run on when Supabase mode is off."""
    from tortoise.sdk import TortoiseSDK

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    db = str(tmp_path / "window.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", db)
    sdk = TortoiseSDK(db, namespace="registry")
    team = sdk.org_create(name="window-test")
    yield sdk, team["id"]
    sdk.close()


@pytest.fixture
def supabase_mode(monkeypatch):
    """Force the Supabase control-plane lane with a swappable fake."""
    import tortoise.supabase_control as sc

    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    return sc


def _anchor(reg, tid: str, start_iso: str | None, end_iso: str | None,
            sub_id: str | None = "sub-b7") -> None:
    """Set the org's billing anchor (the D10 window source)."""
    reg.query(
        "MATCH (t:Team {id: $tid}) SET t.subscription_id = $sub, "
        "    t.current_period_start = $ps, t.current_period_end = $pe",
        params={"tid": tid, "sub": sub_id, "ps": start_iso, "pe": end_iso},
    )


def _rows(reg, tid: str, *columns: str) -> list:
    """Every ``:MeteringRecord`` for *tid*, ordered by window start."""
    sel = ", ".join(f"m.{c}" for c in columns)
    return reg.query(
        f"MATCH (m:MeteringRecord {{org_id: $tid}}) "
        f"RETURN {sel} ORDER BY m.period_start",
        params={"tid": tid},
    ).result_set


def _period(start_iso: str, end_iso: str):
    from tortoise.metering import MeteringPeriod
    return MeteringPeriod(start=datetime.fromisoformat(start_iso),
                          end=datetime.fromisoformat(end_iso))


# ── T1 ───────────────────────────────────────────────────────────────────────


def test_period_key_is_subscription_boundary_not_calendar_month(reg_org,
                                                                monkeypatch):
    """T1 → mutation: revert the resolver to ``f"{now.year}-{now.month:02d}"``
    and IGNORE the subscription anchor.

    The org's billing period is 2026-09-03 → 2026-10-03 and the clock is frozen
    at 2026-09-30. A calendar-month mutation yields the label "2026-09" with a
    2026-09-01 start; the correct answer is the Sep-03 boundary PAIR.

    RED: the persisted row's ``period_start`` would be 2026-09-01, not
    2026-09-03.
    """
    from tortoise.metering import record_write_ops

    sdk, tid = reg_org
    reg = sdk._get_registry()
    _anchor(reg, tid, SUB_START, SUB_END)
    _FrozenDatetime._frozen = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr("tortoise.metering.datetime", _FrozenDatetime)

    result = record_write_ops(tid)
    assert result is not None

    assert result["period_start"] == SUB_START
    assert result["period_end"] == SUB_END
    # The month SURVIVES, but only as the DERIVED aggregation label of the
    # window start (D10 permits a month as a sub-period, never as the invoice
    # window). It is a pure function of the key, so it cannot drift.
    assert result["period"] == "2026-09"

    rows = _rows(reg, tid, "period_start", "period_end", "period")
    assert len(rows) == 1
    assert rows[0] == [SUB_START, SUB_END, "2026-09"], rows


# ── T2 ───────────────────────────────────────────────────────────────────────


def test_increment_row_carries_period_start_and_end(supabase_mode, monkeypatch):
    """T2 → mutation: drop the ``p_period_start`` / ``p_period_end`` binds from
    ``metering_increment`` (write only the month label).

    The RPC body AND the persisted row must carry the org's subscription
    window. RED: the body / row loses the window (the SQL would then have to
    invent one, or fail the NOT NULL).

    The month label is checked too, so a mutation that keeps the window but
    stops deriving the label is also caught.
    """
    from tests.fake_control_plane import FakeControlPlane
    from tortoise.metering import record_write_ops

    fake = FakeControlPlane({
        "organizations": [{"id": "org-t2", "subscription_id": "sub-t2",
                           "current_period_start": SUB_START,
                           "current_period_end": SUB_END}],
        "metering_records": [],
    })
    monkeypatch.setattr(supabase_mode, "get_control_plane", lambda: fake)

    result = record_write_ops("org-t2")
    assert result is not None

    fn, body = fake.rpc_calls[-1]
    assert fn == "metering_increment"
    assert body["p_period_start"] == SUB_START
    assert body["p_period_end"] == SUB_END

    rows = fake.tables["metering_records"]
    assert len(rows) == 1
    assert rows[0]["period_start"] == SUB_START
    assert rows[0]["period_end"] == SUB_END
    assert rows[0]["period"] == "2026-09"
    assert rows[0]["write_ops"] == 1


# ── T3 ───────────────────────────────────────────────────────────────────────


def test_one_day_window_differs_from_monthly_window(reg_org):
    """T3 (the issue's own mutation) → make the cohort reader aggregate a FIXED
    bucket instead of the SUPPLIED window.

    Two captures land in two different windows through the real writer. A
    1-day window must resolve only the newer capture; the wider window must
    resolve both — so the two reads MUST differ. Today (a month-keyed ledger)
    both reads return the same row, which is why the defect cannot even be
    expressed before the boundary exists.

    RED: any reader that ignores the supplied window returns the same total
    twice.
    """
    from tortoise.metering import get_cohort_spend_usd, record_capture_usage

    sdk, tid = reg_org
    reg = sdk._get_registry()

    older = ("2026-07-15T00:00:00+00:00", "2026-07-25T00:00:00+00:00")
    newer = ("2026-08-28T00:00:00+00:00", "2026-08-29T00:00:00+00:00")

    _anchor(reg, tid, *older)
    record_capture_usage(tid, cost_usd=3.0)
    _anchor(reg, tid, *newer)
    record_capture_usage(tid, cost_usd=5.0)

    one_day = _period(*newer)
    wide = _period(older[0], newer[1])  # covers BOTH rows

    assert get_cohort_spend_usd([tid], one_day) == pytest.approx(5.0)
    assert get_cohort_spend_usd([tid], wide) == pytest.approx(8.0)


# ── T4 ───────────────────────────────────────────────────────────────────────


def test_capture_both_sides_of_a_boundary_land_in_two_rows(reg_org):
    """T4 → mutation: keep the PK as ``(org_id, period)`` (the month bucket) so
    both sides of a mid-month boundary COLLIDE into one row.

    Both windows below are 10-day periods inside September 2026, so they share
    the SAME month label ("2026-09"). Under a month key the two captures are
    ONE row; under ``(org_id, period_start)`` they are two.

    RED: ``len(rows) == 1`` (collision), and the two windowed reads below would
    both return 12.0 instead of 5.0 / 7.0.
    """
    from tortoise.metering import get_cohort_spend_usd, record_capture_usage

    sdk, tid = reg_org
    reg = sdk._get_registry()
    first = ("2026-09-05T00:00:00+00:00", "2026-09-15T00:00:00+00:00")
    second = ("2026-09-15T00:00:00+00:00", "2026-09-25T00:00:00+00:00")

    _anchor(reg, tid, *first)
    record_capture_usage(tid, cost_usd=5.0)
    _anchor(reg, tid, *second)
    record_capture_usage(tid, cost_usd=7.0)

    rows = _rows(reg, tid, "period_start", "period", "capture_cost_usd")
    assert len(rows) == 2, f"a mid-month boundary must split the ledger: {rows}"
    assert [r[0] for r in rows] == [first[0], second[0]]
    assert [r[1] for r in rows] == ["2026-09", "2026-09"]  # same month label

    assert get_cohort_spend_usd([tid], _period(*first)) == pytest.approx(5.0)
    assert get_cohort_spend_usd([tid], _period(*second)) == pytest.approx(7.0)


# ── T5 ───────────────────────────────────────────────────────────────────────


def test_period_boundary_is_half_open_start_inclusive_end_exclusive(reg_org):
    """T5 → mutation: change the window comparison from
    ``period_start < end AND period_end > start`` to include the ADJACENT row
    (``<=`` / ``>=``).

    W1 = [S, E) and W2 = [E, E2) are adjacent: W1's END is exactly W2's START.
    Half-open means W1 does not count in W2 and W2 does not count in W1.

    RED: a non-half-open comparison folds the adjacent row in, so each read
    returns the SUM (12.0) instead of its own figure.
    """
    from tortoise.metering import get_cohort_spend_usd, record_capture_usage

    sdk, tid = reg_org
    reg = sdk._get_registry()
    w1 = ("2026-09-05T00:00:00+00:00", "2026-09-15T00:00:00+00:00")
    w2 = ("2026-09-15T00:00:00+00:00", "2026-09-25T00:00:00+00:00")

    _anchor(reg, tid, *w1)
    record_capture_usage(tid, cost_usd=5.0)
    _anchor(reg, tid, *w2)
    record_capture_usage(tid, cost_usd=7.0)

    assert get_cohort_spend_usd([tid], _period(*w1)) == pytest.approx(5.0)
    assert get_cohort_spend_usd([tid], _period(*w2)) == pytest.approx(7.0)


# ── T6 ───────────────────────────────────────────────────────────────────────


def test_capture_and_ask_share_one_period_row(reg_org):
    """T6 → mutation: make ``record_capture_usage`` mint its OWN key from a
    fresh ``datetime.now()`` month instead of the shared resolver — the
    ``[PR#3780]`` shape, where ``cohort_cost.py:218`` duplicated the month
    producer (D14: #3825 must carry that duplicate, not leave it).

    Ask + capture for one org in one window must land on ONE ledger row.

    RED: two rows (the duplicate producer disagrees with the shared one), and
    the cohort read no longer sees the sum.
    """
    from tortoise.metering import get_cohort_spend_usd, record_ask_usage, record_capture_usage

    sdk, tid = reg_org
    reg = sdk._get_registry()
    _anchor(reg, tid, SUB_START, SUB_END)

    record_ask_usage(tid, cost_usd=1.0, tokens_in=10, tokens_out=2)
    record_capture_usage(tid, cost_usd=2.0)

    rows = _rows(reg, tid, "period_start", "ask_cost_usd", "capture_cost_usd")
    assert len(rows) == 1, f"ask + capture must share ONE window row: {rows}"
    assert rows[0] == [SUB_START, 1.0, 2.0], rows
    assert get_cohort_spend_usd([tid], _period(SUB_START, SUB_END)) == \
        pytest.approx(3.0)


# ── T7 ───────────────────────────────────────────────────────────────────────


def test_period_start_does_not_drift_within_a_period(reg_org, monkeypatch):
    """T7 → mutation: derive ``period_start`` from ``now`` on every call
    (rolling / self-anchored) instead of from the subscription.

    Three captures at three different instants INSIDE one billing period must
    produce exactly ONE row. The mutation splits one billing period across
    rows, making the cap's sum depend on call timing — and, since the cohort
    read is windowed, on which of those rows the window happens to cover.

    RED: three rows instead of one.
    """
    from tortoise.metering import record_capture_usage

    sdk, tid = reg_org
    reg = sdk._get_registry()
    _anchor(reg, tid, SUB_START, SUB_END)

    monkeypatch.setattr("tortoise.metering.datetime", _FrozenDatetime)
    for ts in (datetime(2026, 9, 4, 1, 0, 0, tzinfo=UTC),
               datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC),
               datetime(2026, 9, 29, 23, 59, 59, tzinfo=UTC)):
        _FrozenDatetime._frozen = ts
        assert record_capture_usage(tid, cost_usd=1.0) is not None

    rows = _rows(reg, tid, "period_start", "capture_cost_usd")
    assert rows == [[SUB_START, 3.0]], rows


# ── T8 ───────────────────────────────────────────────────────────────────────


def test_renewal_opens_a_new_row_and_freezes_the_prior_one(reg_org):
    """T8 → mutation: make the increment UPSERT the prior row after the anchor
    advances (e.g. keep keying on the month while the boundary moved).

    Both windows share the month label "2026-09" on purpose: under a month key
    the post-renewal write bumps the SAME row. The prior row's totals must stay
    byte-identical — the metering docstring's "previous-period records are
    frozen" promise, now anchored to the subscription rather than a month.

    RED: ``len(rows) == 1``, or the prior row's columns change.
    """
    from tortoise.metering import record_write_ops

    sdk, tid = reg_org
    reg = sdk._get_registry()
    w1 = ("2026-09-05T00:00:00+00:00", "2026-09-15T00:00:00+00:00")
    w2 = ("2026-09-15T00:00:00+00:00", "2026-09-25T00:00:00+00:00")  # renewal

    _anchor(reg, tid, *w1)
    record_write_ops(tid, n=3, nodes_written=7)
    frozen = _rows(reg, tid, "period_start", "period_end", "period",
                   "write_ops", "nodes_written")
    assert len(frozen) == 1

    _anchor(reg, tid, *w2)
    record_write_ops(tid, n=5, nodes_written=11)

    rows = _rows(reg, tid, "period_start", "period_end", "period",
                 "write_ops", "nodes_written")
    assert len(rows) == 2, f"a renewal must open a NEW row: {rows}"
    assert rows[0] == frozen[0], "the prior window's row must stay frozen"
    assert rows[1] == [w2[0], w2[1], "2026-09", 5, 11], rows[1]


# ── T9 ───────────────────────────────────────────────────────────────────────


def test_cohort_spend_reads_a_boundary_range(supabase_mode, monkeypatch):
    """T9 → mutation: revert ``metering_cohort_spend`` to ``period = p_period``
    equality (the exact read #3780 shipped at 20260917000001:87).

    ONE org has TWO rows that share the month label "2026-09". A month-equality
    read matches BOTH and over-states the cohort; the windowed read sums only
    the row that overlaps the supplied window.

    RED: every read returns 12.0.
    """
    from tests.fake_control_plane import FakeControlPlane
    from tortoise.metering import get_cohort_spend_usd

    w1 = _period("2026-09-05T00:00:00+00:00", "2026-09-15T00:00:00+00:00")
    w2 = _period("2026-09-15T00:00:00+00:00", "2026-09-25T00:00:00+00:00")
    fake = FakeControlPlane({"metering_records": [
        {"org_id": "org-t9", "period_start": w1.start_iso,
         "period_end": w1.end_iso, "period": "2026-09",
         "capture_cost_usd": 5.0},
        {"org_id": "org-t9", "period_start": w2.start_iso,
         "period_end": w2.end_iso, "period": "2026-09",
         "capture_cost_usd": 7.0},
    ]})
    monkeypatch.setattr(supabase_mode, "get_control_plane", lambda: fake)

    assert get_cohort_spend_usd(["org-t9"], w1) == pytest.approx(5.0)
    assert get_cohort_spend_usd(["org-t9"], w2) == pytest.approx(7.0)
    # ...and it is the SCALAR RPC over the window, so no row cap can truncate
    # it into an understated (fail-open) spend.
    assert len([c for c in fake.rpc_calls
                if c[0] == "metering_cohort_spend"]) == 2


# ── T10 ──────────────────────────────────────────────────────────────────────


def test_unresolvable_anchor_raises_a_signal_and_the_gate_absorbs_it(
        reg_org, monkeypatch, caplog):
    """T10 → mutations: (i) CATCH the anchor-read exception and return the
    calendar-month key (the silent-fallback shape UF3 warns about); (ii) return
    None and DROP the increment — the write-path fail-open (#3825). Assert a
    RAISE (`QuotaCheckError`) on BOTH the reader and the writer, never a month
    key and never a silent drop.

    Three ways the anchor is unusable:
      (a) a subscription whose period columns are NULL (a half-known anchor);
      (b) the anchor READ fails (a control-plane/registry blip);
      (c) the ADMISSION GATE must NOT propagate the window raise — that would
          be a NEW unconditional user-facing 500 on the capture path, before
          any spend, which the owner's #3981 ruling forbids. The gate ABSORBS
          it, alerts the operator, and SERVES (the cap is simply not evaluated
          for the window-unresolvable org — a calendar-month substitute would
          read the cohort as free, the false PASS this lane exists to prevent).

    (a) and (b) each assert BOTH ends: `_current_period` (the cap's read) and
    `record_write_ops` (the ledger's write). They must raise together — for an
    org whose window is unresolvable, a reader that refuses while a writer
    drops is exactly the undercount that makes the cap fire late.
    """
    import tortoise.cohort_cost as cc
    import tortoise.metering as m
    from tortoise.quota import QuotaCheckError

    sdk, tid = reg_org
    reg = sdk._get_registry()

    # (a) subscription present, period unknown
    _anchor(reg, tid, None, None)
    with pytest.raises(QuotaCheckError, match="not a usable"):
        m._current_period(tid)
    # ...and the WRITER must REFUSE too. This assertion used to be
    # `is None` — the fail-open half of a test NAMED fails_closed: the reader
    # refused while the writer forgot, so the ledger ran short and the cohort
    # cap fired LATE (real money past the cap). Mutation caught here: return
    # None (drop) or a calendar-month key instead of refusing (#3825).
    with pytest.raises(QuotaCheckError):
        m.record_write_ops(tid)

    # (b) the read itself fails — "I could not find out" must never be spelled
    # the same way as "this org has no subscription"
    original = m._reg_sdk

    def _boom(*a, **k):
        raise RuntimeError("registry down (simulated)")

    monkeypatch.setattr(m, "_reg_sdk", _boom)
    with pytest.raises(QuotaCheckError, match="anchor read failed"):
        m._current_period(tid)
    with pytest.raises(QuotaCheckError):
        m.record_write_ops(tid)

    # (c) the ADMISSION GATE absorbs the window raise (#3981 ruling) — it must
    # NOT propagate to the user. This assertion used to be
    # `pytest.raises(QuotaCheckError)`: that IS the new pre-spend 500 on a
    # paying org's capture, and the ruling forbids a new unconditional
    # user-facing refusal. The gate reports the operator and SERVES.
    # Mutations caught: reverting to a bare `period = _current_period(org_id)`
    # (the 500), or dropping the alert (an unenforceable cap, silently).
    monkeypatch.setattr(m, "_reg_sdk", original)
    monkeypatch.setattr(cc, "cohort_org_ids", lambda since: [tid])
    with caplog.at_level(logging.ERROR, logger="tortoise.cohort_cost"):
        assert cc.enforce_cohort_cost_cap(
            {"org_id": tid},
            cap=cc.CohortCostCap(cap_usd=0.01,
                                 since="2026-01-01T00:00:00+00:00")) is None
    assert any("UNENFORCEABLE COHORT COST CAP" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


# ── T11 ──────────────────────────────────────────────────────────────────────


def test_org_without_subscription_uses_the_declared_fallback_window(reg_org):
    """T11 → mutation: leave the ``subscription_id IS NULL`` path returning a
    subscription key (or raising).

    D13 (adopted): an org with no subscription uses the **calendar month in
    UTC**. This is the beta-cohort path — the very orgs the cap targets — so it
    is not an edge case.

    RED: the resolver raises (or returns a subscription-shaped key) for a
    NULL-subscription org, and no write lands.
    """
    from tortoise.metering import _calendar_month_period, _current_period, record_write_ops

    sdk, tid = reg_org
    reg = sdk._get_registry()
    _anchor(reg, tid, None, None, sub_id=None)

    window = _current_period(tid)
    expected = _calendar_month_period()
    assert window.start_iso == expected.start_iso
    assert window.end_iso == expected.end_iso
    now = datetime.now(UTC)
    assert window.label == f"{now.year}-{now.month:02d}"
    assert window.start.day == 1 and window.start.hour == 0

    result = record_write_ops(tid)
    assert result is not None
    assert result["period_start"] == expected.start_iso
    assert result["period_end"] == expected.end_iso


# ── T12 ──────────────────────────────────────────────────────────────────────


def test_billing_webhook_persists_period_start(supabase_mode, monkeypatch):
    """T12 → mutation: remove ``current_period_start`` from
    ``update_org_billing``'s ``allowed`` set.

    ``update_org_billing`` filters with ``if k in allowed`` and then PATCHes —
    so a dropped key is SILENT: the call succeeds, the webhook still 200s, and
    the column stays NULL. The meter resolver then fails closed on a
    half-known anchor, so every increment for a paying org is dropped. Nothing
    in the failure says so.

    Driven through the REAL signature-verified ``/webhooks/stripe`` endpoint
    with a real ``customer.subscription.updated`` payload, so it also catches a
    webhook handler that never writes the field.

    RED: ``row["current_period_start"]`` stays NULL while the response is 200.
    """
    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha
    from tests.fake_control_plane import FakeControlPlane

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    fake = FakeControlPlane({"organizations": [
        {"id": "team-t12", "stripe_customer_id": "cus_t12"}]})
    monkeypatch.setattr(supabase_mode, "get_control_plane", lambda: fake)

    start = int(datetime.fromisoformat(SUB_START).timestamp())
    end = int(datetime.fromisoformat(SUB_END).timestamp())
    payload = {
        "id": "evt_t12",
        "type": "customer.subscription.updated",
        "data": {"object": {
            "id": "sub_t12", "customer": "cus_t12", "status": "active",
            "current_period_start": start, "current_period_end": end,
        }},
    }
    raw = json.dumps(payload).encode()
    issued = str(int(time.time()))
    signed = hmac.new(b"whsec_test", f"{issued}.{raw.decode()}".encode(),
                      hashlib.sha256).hexdigest()

    with TestClient(ha.app) as tc:
        resp = tc.post("/webhooks/stripe", content=raw,
                       headers={"stripe-signature": f"t={issued},v1={signed}"})
    assert resp.status_code == 200, resp.text

    row = fake.tables["organizations"][0]
    assert row.get("subscription_id") == "sub_t12"
    # The control plane binds an ISO-8601 instant (`update_org_billing`
    # normalises Stripe's epoch int — #4216), so the stored value is SUB_START.
    assert row.get("current_period_start") == SUB_START, (
        "the meter window anchor was silently dropped by update_org_billing's "
        "allowed-set filter")
    assert row.get("current_period_end") == SUB_END


# ── T13 — not applicable (documented, not implemented) ───────────────────────
#
# `(c.1)` T13 is explicitly conditional: "*(option (b) only)*". Option (b) —
# reading the window from the append-only `analytics_events` stream — was NOT
# chosen. It needs zero DDL (`created_at` is a real instant and the
# `(org_id, created_at DESC)` index already supports a range), and it is
# rejected for exactly the reason T13's own note gives: the analytics sink's
# durability is unproven and BOTH issues that own that question are OPEN
# (#3677 "every production analytics event was written to an ephemeral VM and
# lost", #3749). A cap that reads a stream which can silently stop recording is
# a cap that never trips — the "configured but not enforced" false PASS.
#
# There is therefore no "ledger vs stream agreement" test to write: the ledger
# IS the window authority, and asserting agreement with a stream this change
# deliberately does not trust would pin a constraint we do not hold.


# ── The MIGRATION's shape — assert the ARTIFACT, not a claim about it ────────


def test_migration_rekeys_the_ledger_and_the_cohort_read_to_a_window():
    """Mutation: re-issue the ledger month-keyed — keep the PK as
    ``(org_id, period)``, keep ``ON CONFLICT (org_id, period)``, or revert
    ``metering_cohort_spend`` to ``AND period = p_period`` (the exact read
    20260917000001:87 shipped).

    The heavy SQL lane is HELD, so this asserts the ARTIFACT: the newest
    migration that defines each function must carry the window, and the OLD
    month-keyed signatures must be DROPPED. A ``CREATE OR REPLACE`` with a new
    argument list is an OVERLOAD — it would leave the month-keyed function
    callable, which is the silent second path this issue removes.

    RED: any of the assertions below, each of which is a distinct regression
    (month PK / month upsert target / month-equality aggregate / a surviving
    month-keyed overload / an unpersisted anchor column).
    """
    from pathlib import Path

    mig = (Path(__file__).resolve().parent.parent / "supabase" / "migrations"
           / "20260918000001_metering_period_window.sql").read_text()
    # A SQL statement may wrap across lines; compare on a whitespace-collapsed
    # copy so the assertions are about the STATEMENT, not its line breaks.
    flat = " ".join(mig.split())

    # (a) the row's IDENTITY is the window start — the PK swap, not an add
    assert "DROP CONSTRAINT IF EXISTS metering_records_pkey" in mig
    assert "ADD PRIMARY KEY (org_id, period_start)" in mig
    assert "ADD PRIMARY KEY (org_id, period)" not in mig

    # (b) every increment upserts on the WINDOW START (3 RPCs, all of them)
    assert mig.count("ON CONFLICT (org_id, period_start)") == 3
    assert "ON CONFLICT (org_id, period)" not in mig

    # (c) the old month-keyed signatures are DROPPED — never merely shadowed
    assert ("DROP FUNCTION IF EXISTS public.metering_increment"
            "(text, text, integer, integer)") in flat
    assert ("DROP FUNCTION IF EXISTS public.metering_increment_ask"
            "(text, text, integer, integer, integer, double precision)") in flat
    assert ("DROP FUNCTION IF EXISTS public.metering_cohort_spend"
            "(text[], text)") in flat

    # (d) the cohort read is a HALF-OPEN OVERLAP of the supplied window
    spend = mig.split("CREATE FUNCTION public.metering_cohort_spend")[1]
    flat_spend = " ".join(spend.split("$$;")[0].split())
    assert "period_start < p_period_end" in flat_spend
    assert "period_end > p_period_start" in flat_spend
    assert "period = p_period" not in flat_spend, (
        "the cohort aggregate must not filter on the DERIVED month label")

    # (e) the anchor column the window resolves from, and its one-time backfill
    assert "ADD COLUMN IF NOT EXISTS current_period_start timestamptz" in mig
    # Month arithmetic on a ``timestamptz`` runs in the SESSION TimeZone (a
    # non-UTC default would land a historical window off the UTC boundary, per
    # connection), so both backfills normalise through UTC. The assertion pins
    # the UTC-normalised form, not the old session-dependent one.
    assert ("current_period_start = (current_period_end AT TIME ZONE 'UTC' "
            "- interval '1 month') AT TIME ZONE 'UTC'") in flat
    assert ("(((period || '-01T00:00:00+00:00')::timestamptz "
            "AT TIME ZONE 'UTC') + interval '1 month') AT TIME ZONE 'UTC'") in flat


# ── #4216 — a subscription AUTHORING path must write a COMPLETE window ───────


def test_checkout_webhook_writes_both_period_bounds(supabase_mode, monkeypatch):
    """#4216 → mutation: revert ``checkout.session.completed`` to writing only
    ``subscription_id`` (no period).

    Checkout is an AUTHORING path for the subscription: a just-checked-out
    PAYING org used to persist the id and NO period, so
    ``metering._current_period`` raised for it, its increments were dropped and
    the cohort cap could never be enforced for it (#3981 absorbed + alerted the
    symptom; this is the DATA defect). Driven through the REAL
    signature-verified endpoint with an existing org, then resolved through the
    REAL meter.

    RED: both ``current_period_start`` and ``current_period_end`` stay NULL and
    ``_current_period`` raises.
    """
    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha
    import tortoise.metering as m
    from tests.fake_control_plane import FakeControlPlane
    from tortoise import billing as bl

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    fake = FakeControlPlane({"organizations": [
        {"id": "org-4216-checkout", "stripe_customer_id": "cus_4216"}]})
    monkeypatch.setattr(supabase_mode, "get_control_plane", lambda: fake)

    start = int(datetime.fromisoformat(SUB_START).timestamp())
    end = int(datetime.fromisoformat(SUB_END).timestamp())
    # items empty → the tier cannot resolve, so this exercises ONLY the window
    # write (no apply_limits / notify side path).
    monkeypatch.setattr(bl.StripeClient, "get_subscription",
                        lambda self, sid: {"id": "sub_4216", "status": "active",
                                           "current_period_start": start,
                                           "current_period_end": end,
                                           "items": {"data": []}})
    payload = {
        "id": "evt_4216_checkout",
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "org-4216-checkout",
            "customer": "cus_4216",
            "customer_details": {"email": "o@e.com"},
            "subscription": "sub_4216",
        }},
    }
    raw = json.dumps(payload).encode()
    issued = str(int(time.time()))
    signed = hmac.new(b"whsec_test", f"{issued}.{raw.decode()}".encode(),
                      hashlib.sha256).hexdigest()

    with TestClient(ha.app) as tc:
        resp = tc.post("/webhooks/stripe", content=raw,
                       headers={"stripe-signature": f"t={issued},v1={signed}"})
    assert resp.status_code == 200, resp.text

    row = fake.tables["organizations"][0]
    assert row.get("subscription_id") == "sub_4216"
    # #4216: the CONTROL PLANE can only bind an ISO-8601 instant — Stripe sends
    # epoch ints, and `update_org_billing` normalises them at the one seam every
    # Supabase-lane billing write passes through (PostgREST rejects a bare JSON
    # number for a `timestamptz`). Mutation caught: dropping that normalisation
    # (the int is stored / the real PATCH would 400).
    from datetime import UTC as _UTC
    assert row.get("current_period_start") == datetime.fromtimestamp(
        start, tz=_UTC).isoformat()
    assert row.get("current_period_end") == datetime.fromtimestamp(
        end, tz=_UTC).isoformat()

    # The whole point: the org's window now RESOLVES (no raise), so its ledger
    # rows are addressable and the cap can measure it.
    window = m._current_period("org-4216-checkout")
    assert window.start_iso == SUB_START
    assert window.end_iso == SUB_END


def test_checkout_with_malformed_items_is_acked_not_500(supabase_mode, monkeypatch):
    """#4216 → mutation: deref ``.get`` on a scalar ``items`` in
    ``_price_id_from`` (or drop the shared ``_subscription_items`` guard).

    The checkout call site is OUTSIDE any try, so a malformed payload raised
    ``AttributeError`` → HTTP 500 before the metadata-tier fallback could run,
    and Stripe would retry the same malformed event forever.

    RED against THIS branch's structure: the narrow ``try`` now covers only
    ``get_subscription``, so the deref escapes to the route's ``except
    Exception`` → 500. On ``origin/main`` the whole block sat inside one outer
    ``except Exception`` that masked the same AttributeError into a 200, so this
    test pins the narrow-try decision + the ``_subscription_items`` guard, not a
    branch-point regression.
    """
    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha
    from tests.fake_control_plane import FakeControlPlane
    from tortoise import billing as bl

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    fake = FakeControlPlane({"organizations": [
        {"id": "org-4216-malformed", "stripe_customer_id": "cus_bad"}]})
    monkeypatch.setattr(supabase_mode, "get_control_plane", lambda: fake)
    monkeypatch.setattr(bl.StripeClient, "get_subscription",
                        lambda self, sid: {"id": "sub_bad", "status": "active",
                                           "items": "x"})
    payload = {
        "id": "evt_4216_malformed",
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "org-4216-malformed",
            "customer": "cus_bad",
            "subscription": "sub_bad",
        }},
    }
    raw = json.dumps(payload).encode()
    issued = str(int(time.time()))
    signed = hmac.new(b"whsec_test", f"{issued}.{raw.decode()}".encode(),
                      hashlib.sha256).hexdigest()

    with TestClient(ha.app) as tc:
        resp = tc.post("/webhooks/stripe", content=raw,
                       headers={"stripe-signature": f"t={issued},v1={signed}"})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("bad_sub", [
    {"items": "x"},                             # scalar items
    {"items": 5},                               # int items
    {"items": {"data": [{"price": "x"}]}},       # non-dict price
    {"items": {"data": [{"price": 7}]}},         # int price
    {"items": [{"price": ["not", "a", "dict"]}]},  # list price
])
def test_checkout_with_malformed_subscription_shape_is_acked_not_500(
        supabase_mode, monkeypatch, bad_sub):
    """#4216 review → mutation: leave ANY unknown-shape ``.get`` in
    ``_price_id_from`` unguarded.

    ``_subscription_items`` hardened the ``items`` shape, but the sibling
    ``(rows[0].get("price", {}) or {}).get("id")`` still raised on a truthy
    non-dict ``price`` (``billing.subscription_plan`` guards the identical
    access, so the shape is an expected payload class). The checkout call site
    is OUTSIDE any try, so the ``AttributeError`` escapes to the route's
    ``except Exception`` → HTTP 500 → Stripe redelivers the same malformed
    event forever.
    """
    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha
    from tests.fake_control_plane import FakeControlPlane
    from tortoise import billing as bl

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    fake = FakeControlPlane({"organizations": [
        {"id": "org-4216-shape", "stripe_customer_id": "cus_bad"}]})
    monkeypatch.setattr(supabase_mode, "get_control_plane", lambda: fake)
    sub = {"id": "sub_bad", "status": "active", **bad_sub}
    monkeypatch.setattr(bl.StripeClient, "get_subscription",
                        lambda self, sid: sub)
    payload = {
        "id": "evt_4216_shape",
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "org-4216-shape",
            "customer": "cus_bad",
            "subscription": "sub_bad",
        }},
    }
    raw = json.dumps(payload).encode()
    issued = str(int(time.time()))
    signed = hmac.new(b"whsec_test", f"{issued}.{raw.decode()}".encode(),
                      hashlib.sha256).hexdigest()

    with TestClient(ha.app) as tc:
        resp = tc.post("/webhooks/stripe", content=raw,
                       headers={"stripe-signature": f"t={issued},v1={signed}"})
    assert resp.status_code == 200, resp.text


def test_checkout_window_write_failure_is_retried_not_swallowed(
        supabase_mode, monkeypatch):
    """#4216 review → mutation: revert the checkout window write to the
    log-and-CONTINUE ``try/except`` (i.e. drop the re-raise).

    A swallowed window-write failure lets the route return 200, so Stripe never
    redelivers and the org stays window-unresolvable (increments dropped, cap
    unenforceable) until the next renewal — which a checkout-only org may never
    receive. The fix APPLIES THE TIER FIRST (a taken payment must not sit on
    free limits, #2789) and then re-raises so the event is retried; both writes
    are idempotent, so the retry completes the window.

    RED: response 200 (the failure is masked) instead of 500.
    """
    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha
    from tests.fake_control_plane import FakeControlPlane
    from tortoise import billing as bl

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    fake = FakeControlPlane({"organizations": [
        {"id": "org-4216-wfail", "stripe_customer_id": "cus_4216"}]})
    monkeypatch.setattr(supabase_mode, "get_control_plane", lambda: fake)

    real_update = supabase_mode.update_org_billing

    def _fail_only_on_window(cp, org, updates):
        if "current_period_start" in updates or "current_period_end" in updates:
            raise RuntimeError("period column write exploded")
        return real_update(cp, org, updates)

    monkeypatch.setattr(supabase_mode, "update_org_billing", _fail_only_on_window)

    class _StubCatalog:
        def tier_for_price(self, price_id):
            return "pro"

    monkeypatch.setattr(bl, "PriceCatalog", _StubCatalog)

    start = int(datetime.fromisoformat(SUB_START).timestamp())
    end = int(datetime.fromisoformat(SUB_END).timestamp())
    monkeypatch.setattr(bl.StripeClient, "get_subscription",
                        lambda self, sid: {"id": "sub_4216", "status": "active",
                                           "current_period_start": start,
                                           "current_period_end": end,
                                           "items": {"data": [
                                               {"price": {"id": "price_x"}}]}})
    payload = {
        "id": "evt_4216_wfail",
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "org-4216-wfail",
            "customer": "cus_4216",
            "subscription": "sub_4216",
        }},
    }
    raw = json.dumps(payload).encode()
    issued = str(int(time.time()))
    signed = hmac.new(b"whsec_test", f"{issued}.{raw.decode()}".encode(),
                      hashlib.sha256).hexdigest()

    with TestClient(ha.app) as tc:
        resp = tc.post("/webhooks/stripe", content=raw,
                       headers={"stripe-signature": f"t={issued},v1={signed}"})
    # The window failure is SURFACED (retried), not masked as a 200.
    assert resp.status_code == 500, resp.text

    row = fake.tables["organizations"][0]
    # ...but the tier was applied FIRST, so the taken payment does not sit on
    # free limits while Stripe redelivers (#2789).
    assert row.get("tier") == "pro", row
    # The window itself was NOT partially written.
    assert row.get("current_period_start") is None, row
    assert row.get("current_period_end") is None, row


def test_new_org_window_failure_still_applies_the_metadata_tier(
        supabase_mode, monkeypatch):
    """#4216 review → mutation: re-raise ``window_error`` BEFORE the
    ``is_new_org and resolved_tier is None`` metadata-tier fallback.

    When the window write fails AND the subscription price does not resolve
    (``tier is None``), the metadata fallback is the only path that lifts a new
    PAID org off free limits in the registry/selfhost lane (``sdk.org_create``
    hardcodes free; #2789). Re-raising early skips it. The fix runs the
    fallback first and re-raises LAST, so the tier lands AND the event is
    retried.

    RED: the org is left on the default (free) tier while the route 500s.
    """
    from fastapi.testclient import TestClient

    import tortoise.hosted_api as ha
    from tests.fake_control_plane import FakeControlPlane
    from tortoise import billing as bl

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_123")
    fake = FakeControlPlane({"organizations": [
        {"id": "org-4216-neworg", "stripe_customer_id": "cus_4216"}]})
    monkeypatch.setattr(supabase_mode, "get_control_plane", lambda: fake)
    # Stub provisioning: this test is about the ORDER of the two failure
    # paths, not about provisioning (covered elsewhere).
    monkeypatch.setattr(ha, "_provision_new_org_from_checkout",
                        lambda sdk, org_id, meta: org_id)

    real_update = supabase_mode.update_org_billing

    def _fail_only_on_window(cp, org, updates):
        if "current_period_start" in updates or "current_period_end" in updates:
            raise RuntimeError("period column write exploded")
        return real_update(cp, org, updates)

    monkeypatch.setattr(supabase_mode, "update_org_billing", _fail_only_on_window)

    class _EmptyCatalog:
        def tier_for_price(self, price_id):
            return None

    monkeypatch.setattr(bl, "PriceCatalog", _EmptyCatalog)

    start = int(datetime.fromisoformat(SUB_START).timestamp())
    end = int(datetime.fromisoformat(SUB_END).timestamp())
    monkeypatch.setattr(bl.StripeClient, "get_subscription",
                        lambda self, sid: {"id": "sub_4216", "status": "active",
                                           "current_period_start": start,
                                           "current_period_end": end,
                                           "items": {"data": [
                                               {"price": {"id": "price_x"}}]}})
    payload = {
        "id": "evt_4216_neworg_wfail",
        "type": "checkout.session.completed",
        "data": {"object": {
            "client_reference_id": "org-4216-neworg",
            "customer": "cus_4216",
            "subscription": "sub_4216",
            "metadata": {"new_org": "1", "tier": "pro",
                          "user_id": "u_4216", "org_name": "New Org"},
        }},
    }
    raw = json.dumps(payload).encode()
    issued = str(int(time.time()))
    signed = hmac.new(b"whsec_test", f"{issued}.{raw.decode()}".encode(),
                      hashlib.sha256).hexdigest()

    with TestClient(ha.app) as tc:
        resp = tc.post("/webhooks/stripe", content=raw,
                       headers={"stripe-signature": f"t={issued},v1={signed}"})
    assert resp.status_code == 500, resp.text

    # The metadata fallback ran BEFORE the re-raise: the new paid org is NOT
    # left on free limits while Stripe redelivers.
    row = fake.tables["organizations"][0]
    assert row.get("tier") == "pro", row

