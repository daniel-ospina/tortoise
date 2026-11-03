"""#9567 capstone — the record's D-items, EXECUTED end to end, not restated.

WHAT THIS FILE IS. The capstone's DoD has twelve items and each already has a
unit somewhere else (`test_9078_…` capture, `test_9556_…` restore,
`test_record_backup.py` drill, the PGlite tenancy suite, `test_quota.py` gate).
This file does not repeat any of those bodies: it drives the **production**
path through a real Postgres record and asserts the end-to-end property the
capstone's objective sentence names. ⚠️ #4282 deleted `capture_session`, so the
**armed** and **restorable** legs (D1's armed half, D2's one-capture, D4/D5's
rebuild-from-record-only) no longer run here — what survives is the
NOT-ARMED report, the no-bypass scan, the tenancy legs and the quota gate, each
still executed rather than restated. Where an item cannot be executed as an
end-to-end case from a lane, the artifact under `docs/verify/` says so with the
reason; no leg here fakes a green for one.

The falsifier of each leg is named on the leg, so the run that reads as
evidence can be broken on purpose and observed red (the artifact's table
records the mutation used for each).

HOW TO RUN IT (the same arming the CI job uses, #9551):

    TEST_RECORD_DB_URI=postgresql://…  uv run --extra postgres \\
        pytest tests/test_9567_record_capstone.py -q

Unset ⇒ every record leg SKIPS by name and the DB-free legs still run. It must
never pass by asserting nothing: the leg gate
(`.github/scripts/record-postgres-leg-gate.py`) fails a job whose record legs
skip.

⛔ WHAT IS DELIBERATELY NOT HERE.

* **D8 (restore from its own backup)** — the production drill shells out to
  `pg_dump`/`pg_restore`, which this lane does not carry. The command that owns
  it is `pytest tests/test_record_backup.py`; the artifact records D8 as NOT
  MEASURED here with that reason rather than a green it did not run.
* **D6's "all four R5 edge families are present"** — a property of what a
  CAPTURE emits, not of the rebuild. This file proves the seam the projection
  owns (every relation the record carries lands in the cache, per predicate);
  the family-completeness command is
  `pytest tests/test_9553_class4_structural_edge_records.py`.
* **D7's narrative TEXT** — the §9.1 narrative carrier is emitted by the
  layer-1 commit path (`hosted_api._execute_commit_writes`), not by
  `capture_session`; the raw turn text IS driven end to end here. The carrier's
  own write pin is DB-free in `tests/test_9555_narrative_carrier.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tortoise import record_writer
from tortoise.sdk import TortoiseSDK

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The record's database — the arming surface `test_9078` and the CI job use.
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: The tables this run counts per tenant: the ones the capture writes and the
#: ones D9's cross-tenant pair / D10's dropped policy read.
_TOUCHED = ("session", "event", "source", "point", "journal_event")

#: The turn text the capture writes; D7 reads it back from the record only.
_TURNS = [
    {"role": "user", "content": "We chose FalkorDB for the hot cache."},
    {"role": "assistant", "content": "Noted: FalkorDB holds the hot cache."},
]


# ── the record, armed the way production arms it ───────────────────────────


def _driver():
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres, so no record to prove")
    psycopg2 = pytest.importorskip(
        "psycopg2", reason="psycopg2-binary is the record's driver ([postgres] extra)"
    )
    return dsn, psycopg2


def _counts(conn, tenant: tuple[str, str]) -> dict[str, int]:
    org, graph = tenant
    out: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in _TOUCHED:
            cur.execute(
                f"SELECT count(*) FROM public.{table} WHERE org_id=%s AND graph_id=%s", (org, graph)
            )
            out[table] = int(cur.fetchone()[0])
    return out


@dataclass
class Armed:
    """One armed record, one capture through the PUBLIC path."""

    dsn: str
    conn: Any  # superuser: seeding + assertions
    tenant: tuple[str, str]
    sdk: Any
    session_id: str
    receipt: dict
    journal: Path
    before: dict[str, int]
    after: dict[str, int]
    failures_lost: int




# ── D1 — armed, and every record table present ─────────────────────────────




def test_D1_an_unset_dsn_reports_NOT_ARMED_and_writes_nothing(monkeypatch):
    """D1's second half: unset DSN ⇒ `NOT_ARMED`, never a silent pass.

    Falsifier: report `armed` unconditionally → this reds.
    """
    monkeypatch.delenv(record_writer.RECORD_DSN_ENV, raising=False)
    writer = record_writer.RecordWriter()
    assert writer.armed is False
    result = writer.write([{"type": "SessionRecorded", "id": "s"}], org_id="o", graph_id="g")
    assert result.outcome is record_writer.RecordWriteOutcome.NOT_ARMED
    assert result.last_seq == 0, "an unarmed write must not advance the watermark"


# ── D2 — one capture, rows, zero write failures ────────────────────────────




# ── D3 — no write path bypasses the record ─────────────────────────────────


def test_D3_no_write_path_bypasses_the_record():
    """D3: every capture door writes the record (the wiring gate, executed).

    The gate is the production tool; running it is the measurement.
    Falsifier: a live door that drops the journal ordinal (`seq=None`) → the
    gate exits 1 naming the call site.
    """
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "record_wiring_check.py")],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, (
        f"record_wiring_check failed ({proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
    )
    assert "WIRED" in proc.stdout, proc.stdout


# ── D4/D5/D6 — the rebuild from the record only ────────────────────────────










# ── D7 — the raw turn text comes back, from the record only ────────────────




# ── D9/D10 — tenancy ───────────────────────────────────────────────────────








# ── D11/D12 — the quota gate ───────────────────────────────────────────────


@pytest.fixture()
def org(tmp_path, monkeypatch):
    """A real SDK with a provisioned org, on an embedded registry."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "quota.db"))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    sdk = TortoiseSDK(str(tmp_path / "quota.db"), namespace="registry")
    sdk.org_create(name="capstone")
    rows = sdk._get_registry().query("MATCH (t:Team) RETURN t.id LIMIT 1").result_set
    assert rows, "no org provisioned"
    yield sdk, rows[0][0]
    sdk.close()


def test_D11_the_gate_refuses_at_the_org_limit_and_the_refusal_is_visible(org):
    """D11: at the cap the GATE refuses (machine-readable), under the cap the
    gate admits and the write lands.

    The claim is scoped to the gate on purpose: this leg calls
    `enforce_org_limit` directly and does NOT attempt a write through a
    production door while over the cap, so the name must not read as "a write
    was refused". The write doors that call this gate
    (`tortoise/mcp_server.py`, `tortoise/hosted_api.py`) are the end-to-end
    form and belong to #9559's storage meter, not here.

    The allowance is a TEST value set at the MEASURED usage, never a hardcoded
    number (`resolve_org_limits` supplies the rest of the dict).
    Falsifier: put the cap above the usage → the refusal assertion reds, so the
    refusal is the meter's, not the fixture's.
    """
    from tortoise.quota import (
        QUOTA_REFUSAL_CODE,
        QuotaExceededError,
        count_org_usage,
        enforce_org_limit,
        quota_refusal_payload,
        resolve_org_limits,
    )

    sdk, org_id = org
    sdk.create_point("statement", "one point the org already owns")
    used = count_org_usage(org_id, "points", sdk=sdk)
    limits = resolve_org_limits(org_id)

    over = dict(limits, max_points=used)  # at the cap ⇒ refuse
    with pytest.raises(QuotaExceededError) as exc:
        enforce_org_limit(over, "points", sdk=sdk)
    payload = quota_refusal_payload(exc.value)
    assert payload["code"] == QUOTA_REFUSAL_CODE, payload
    assert payload["resource"] == "points" and payload["used"] == used, payload
    assert "limit" in payload and payload["limit"] == used, payload

    under = dict(limits, max_points=used + 1)  # below the cap ⇒ the write goes
    enforce_org_limit(under, "points", sdk=sdk)
    sdk.create_point("statement", "the write the gate admitted")
    assert count_org_usage(org_id, "points", sdk=sdk) > used


def test_D12_a_gate_with_no_usage_reading_fails_closed(org, monkeypatch):
    """D12: with an allowance configured and NO reading, the gate REFUSES.

    Falsifier: make the counting seam return 0 instead of raising → this reds,
    which is the fail-open direction the branch exists to prevent.
    """
    from tortoise.quota import QuotaCheckError, enforce_org_limit

    sdk, org_id = org
    limits = {"org_id": org_id, "max_points": 10_000}

    def no_reading(*_a, **_kw):
        raise RuntimeError("the meter has no reading")

    # The meter is made UNREADABLE (the count itself fails), which is the
    # shape D12 names — not a missing limit key, which `test_quota.py` pins.
    with monkeypatch.context() as m:
        m.setattr(sdk._get_proj().g._g, "query", no_reading)
        with pytest.raises(QuotaCheckError) as exc:
            enforce_org_limit(limits, "points", sdk=sdk)
        assert "quota count failed" in str(exc.value), exc.value

    # The SAME write with a reading succeeds — so the refusal above came from
    # the absent reading, not from the gate refusing everything.
    enforce_org_limit(limits, "points", sdk=sdk)


# ── the pin that keeps this file ARMED, not merely present ─────────────────


def test_ci_runs_this_file_in_the_armed_record_job():
    """A capstone no job runs armed is prose with extra steps.

    Every record leg here SKIPS by name without `TEST_RECORD_DB_URI`, and the
    only job that arms it is `test-record-postgres` (#9551). So the job's
    `run:` must name this file: a lane that deleted that one line would leave
    the record job green (its leg gate is satisfied by the sibling files) and
    the fast pool green (7 legs skip silently) while the capstone executed
    only its DB-free legs — the #9551 defect one level up.

    Pinned structurally, the same way `test_9664` pins its own job and
    `test_9078` pins its: a `run:` string cannot be executed, so a substring
    assertion is the closest thing to running it.
    """
    import yaml

    workflow = REPO_ROOT / ".github" / "workflows" / "python-ci.yml"
    jobs = yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]
    job = jobs.get("test-record-postgres")
    assert job is not None, (
        "`test-record-postgres` is gone — that job is the only place the record "
        "is armed, so this file would never run armed anywhere (#9551)"
    )
    runs = "\n".join(str(step.get("run", "")) for step in job.get("steps") or [])
    assert Path(__file__).name in runs, (
        f"`test-record-postgres` does not run {Path(__file__).name}: the capstone "
        "legs would skip in every other job and the job would stay green"
    )
