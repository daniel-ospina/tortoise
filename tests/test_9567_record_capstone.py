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

* **D8's HOSTED leg (the drill against the account's record)** — the drill ITSELF
  runs here, end to end, against a second real Postgres that the CI job
  provisions, with its falsifier fired (see `test_D8_…`). What is NOT here is the
  same drill against the **hosted** record: that needs
  `TORTOISE_RECORD_BACKUP_DSN` + the R2 key, which a lane cannot supply
  (tracked on #11193). It is recorded as `hosted leg NEVER-RUN`, never as a green
  this file did not run (#9567 comment 6690574287 §4).
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
import shutil
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

#: The SECOND Postgres the D8 drill restores INTO (`postgres-provision` with
#: `dsn_var: TEST_RECORD_SCRATCH_URI`). It cannot be the record itself: the drill
#: refuses a scratch target that resolves to the source (`refuse_same_database`).
SCRATCH_DB_ENV = "TEST_RECORD_SCRATCH_URI"

#: The image whose client binaries carry the drill when the runner has none.
#: Measured 2026-11-06: `pg_dump (PostgreSQL) 16.15` inside `postgres:16` — so
#: "this box has no `pg_dump`" is not a reason to skip D8 (#9567 §1 of comment
#: 6690574287), it is a reason to borrow the record's own image.
#: The CANDIDATES are the provision action's own (`image_candidates()` in
#: `.github/actions/postgres-provision/provision.sh`): mirror first, upstream
#: second, each tried against the local daemon before any pull, because a
#: rate-limited or unreachable mirror must not fail a job that needs no pull
#: (#7867). This list is resolved through the SAME two steps (inspect on every
#: candidate first, then pull each in order) — a single hard-coded reference here
#: would reintroduce the failure the action's resolution exists to avoid.
CLIENT_IMAGES = ("mirror.gcr.io/postgres:16", "postgres:16")

#: The tables the drill's verdict must cover, declared HERE rather than read from
#: `record_tables()`. A set compared against `record_tables()` on both sides is
#: the same call twice and can never fail (`compare_row_counts` builds its `names`
#: from that same function), so the mutation it was supposed to catch — a table
#: dropped from `THE_RECORD_TABLES`, which removes it from the dump AND from the
#: comparison in one edit, leaving a green verdict — would have been invisible
#: (#11429 review cycle 2). This tuple is the record's ten tables as the capstone
#: states them; a table that disappears from the drill reds here by name.
RECORD_TABLES_DECLARED = (
    "event",
    "journal_event",
    "node_identity",
    "object",
    "point",
    "relation",
    "session",
    "session_narrative",
    "source",
    "subject",
)

#: One drill's wall clock (dump + encrypt + upload + download + decrypt + restore
#: + two counts). `record_backup.RECORD_SUBPROCESS_TIMEOUT_S` bounds the drill's
#: own legs; this bounds the client-binary wrapper, which the drill cannot see.
_CLIENT_TIMEOUT_S = 300

#: 32 bytes = AES-256. A FIXED test key: the drill's job is to prove the artifact
#: restores, and a random key would only prove a random key restores.
_DRILL_KEY = b"0123456789abcdef0123456789abcdef"

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




def test_the_record_table_set_is_the_one_this_capstone_declares():
    """The SET half of D1's pin — and the home of D8's own falsifier.

    `RECORD_TABLES_DECLARED` is a literal in this file; `record_tables()` is the
    tuple the drill dumps and compares. Because they are independent, this pin can
    only pass while the record's table set is the one this capstone states, and it
    NAMES the divergence.

    ⛔ Why it exists beside D8's in-drill comparison (#11429 review P1): that
    comparison cannot be the only detector. A table dropped from
    `THE_RECORD_TABLES` leaves the dump's `--table=` list AND the comparison
    together, so the drill's own verdict has nothing left to disagree with — and
    where the dropped table references one that is still truncated,
    `reset_record_tables` refuses first (measured 2026-11-07 on `subject`:
    ``cannot truncate a table referenced in a foreign key constraint … Table
    "subject" references "node_identity"``), so the in-drill assertion is never
    even reached. This pin needs no database, so nothing can pre-empt it.

    Falsifier: drop, rename or add a table in `THE_RECORD_TABLES`
    (`tortoise/record_projection.py`) → this reds naming it in well under a
    second.
    """
    from tortoise import record_backup as rb

    runtime = set(rb.record_tables())
    declared = set(RECORD_TABLES_DECLARED)
    assert runtime == declared, (
        f"the record measures {sorted(runtime)} but this capstone declares "
        f"{sorted(declared)} — the drill no longer covers "
        f"{sorted(declared - runtime)}, and this file declares tables the record "
        f"does not have: {sorted(runtime - declared)}"
    )


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




# ── D8 — restore from the record's own backup, against a SECOND real Postgres ─


def _pg_client_runner(workdir: Path):
    """The drill's `run` seam: REAL `pg_dump`/`pg_restore`, on PATH or in the image.

    Returns `None` when both client binaries are on PATH — the drill's own default
    runner then does the work and nothing here is involved.

    Otherwise the SAME argv is executed by the client binaries that ship inside the
    Postgres image. That is not a workaround: `#9567`'s comment 6690574287 §1
    measured the binaries were never the gap (`docker run … postgres:16 pg_dump
    --version` ⇒ 16.15), and D8's remaining half is a CREDENTIAL, not a client.

    `--network host` because the provision action binds 127.0.0.1 (a container's
    own loopback is not the host's), and the workdir is mounted because the dump
    is written to a host path that `pg_restore` then reads back. Only `PG*`
    variables are forwarded, by NAME (`--env KEY`, with the value taken from the
    docker CLIENT's own environment): passing `--env KEY=VALUE` would put the
    record's password in this process's argv, which is the exposure
    `record_backup`'s env-only design exists to prevent — argv is public, and it
    lands in `ps`, shell history and crash reports.
    """
    if shutil.which("pg_dump") and shutil.which("pg_restore"):
        return None

    image = _client_image()

    def run(argv, *, env=None, capture_output=True, text=True, timeout=None, **_ignored):
        forwarded = {key: value for key, value in (env or {}).items() if key.startswith("PG")}
        cmd = ["docker", "run", "--rm", "--network", "host", "-v", f"{workdir}:{workdir}"]
        for key in sorted(forwarded):
                                    cmd += ["--env", key]
        cmd += [image, *argv]
        return subprocess.run(
            cmd,
            env={**forwarded, **({"PATH": os.environ["PATH"]} if "PATH" in os.environ else {})},
            capture_output=True,
            text=True,
            timeout=timeout or _CLIENT_TIMEOUT_S,
        )

    return run


def test_the_docker_client_runner_keeps_the_password_out_of_argv(monkeypatch, tmp_path):
    """The wrapper's env-only property, measured — it is DEAD on CI as written.

    `_pg_client_runner` returns `None` whenever `pg_dump`/`pg_restore` are on PATH,
    and on `ubuntu-latest` they are, so the `docker run --env KEY` construction
    never executes on the leg D8 runs in. The production exposure IS pinned
    (`tests/test_record_backup.py::test_the_password_never_reaches_argv_and_the_dsn_is_not_positional`),
    but that pin covers `libpq_env`, not this wrapper — so this is the one place
    the drill could reintroduce `--env PGPASSWORD=<secret>` with no falsifier, and
    the docstring above asserts the property as though it were measured (#11429
    review cycle 3).

    Mutation: replace `["--env", key]` with `["--env", f"{key}={value}"]` in
    `_pg_client_runner` -> RED (the secret appears in the captured argv).
    """
    module = sys.modules[__name__]
    # Force the docker branch: no client binaries on this "box".
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(module, "_client_image", lambda: "mirror.gcr.io/postgres:16")

    captured: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    runner = _pg_client_runner(tmp_path)
    assert runner is not None, (
        "the wrapper returned `None` with no client binaries on PATH — the docker "
        "branch this test exists to measure was not reached")

    secret = "s3cret-do-not-leak"
    runner(
        ["pg_dump", "--data-only", "--format=custom"],
        env={"PGPASSWORD": secret, "PGHOST": "127.0.0.1", "PATH": os.environ.get("PATH", "")},
    )

    cmd = captured["cmd"]
    assert not [part for part in cmd if secret in str(part)], (
        f"the record's password reached argv: {cmd!r}. `--env KEY=VALUE` puts the "
        "secret in `ps`, shell history and crash reports; only the NAME may be "
        "forwarded, with the value taken from the docker client's environment")

    env_flags = [cmd[i + 1] for i, part in enumerate(cmd) if part == "--env"]
    assert env_flags == ["PGHOST", "PGPASSWORD"], (
        f"the forwarded `--env` names are {env_flags!r} — the wrapper must forward "
        "the PG* NAMES it was given, and nothing else")
    assert "=" not in "".join(env_flags), (
        "an `--env` argument carries `=`, which means a VALUE was put on the "
        "command line — the exposure this seam exists to avoid")

    forwarded = captured["kwargs"]["env"]
    assert forwarded["PGPASSWORD"] == secret, (
        "the secret must still reach the child through its ENVIRONMENT — moving "
        "it off argv must not also stop the client from authenticating")
    assert "PGHOST" in forwarded, forwarded


def _client_image() -> str:
    """The client-binary image: local presence first, then a pull per candidate.

    The action's `provision.sh:86-101` does exactly this in two passes, and the
    two-pass shape is the point: a warm daemon serves every candidate without a
    registry call, and only when none is present does a pull happen — mirror
    first, upstream second (#7867). Falling straight back to `postgres:16` would
    make this leg bypass the mirror whenever neither image was local, which is the
    rate-limit shape the resolution exists to avoid.
    """
    for candidate in CLIENT_IMAGES:
        if subprocess.run(
            ["docker", "image", "inspect", candidate], capture_output=True
        ).returncode == 0:
            return candidate
    for candidate in CLIENT_IMAGES:
        if subprocess.run(["docker", "pull", candidate], capture_output=True).returncode == 0:
            return candidate
    return CLIENT_IMAGES[-1]


@dataclass
class Drilled:
    """One completed restore drill, and the second server it landed in."""

    verdict: dict[str, Any]
    scratch: str
    psycopg2: Any


@pytest.fixture(scope="module")
def drilled(armed, tmp_path_factory):
    """Run the production drill once: dump → encrypt → upload → download →
    decrypt → restore → per-table count, into a SECOND real Postgres.

    The scratch target is armed the way the record is (the repo's own
    `_ensure_record_schema`) and carries the tenancy rows its foreign keys
    reference — read from the SOURCE rather than assumed, because this job's
    record also holds whatever the sibling legs captured, and a missing tenant
    row is a restore failure that has nothing to do with the artifact.
    """
    from tortoise import record_backup as rb
    from tortoise.hosted_backup import MemoryStorage

    _, psycopg2 = _driver()
    scratch = os.environ.get(SCRATCH_DB_ENV)
    if not scratch:
        pytest.skip(
            f"{SCRATCH_DB_ENV} not set — the drill restores into a target that is NOT "
            "the record, and `refuse_same_database` correctly refuses a drill pointed "
            "at the record itself. One database cannot arm this leg; the CI job "
            "provisions the second (postgres-provision, dsn_var=TEST_RECORD_SCRATCH_URI)."
        )
    from tests.test_9078_record_written_by_production_path import (
        _ensure_record_schema,
        _seed_tenant,
    )

    scratch_conn = psycopg2.connect(scratch)
    scratch_conn.autocommit = True
    try:
        _ensure_record_schema(scratch_conn)
        with armed.conn.cursor() as cur:
            cur.execute("SELECT id, org_id FROM public.graphs ORDER BY id")
            tenancies = [(row[1], row[0]) for row in cur.fetchall()]
        for pair in tenancies:
            _seed_tenant(scratch_conn, pair)
    finally:
        scratch_conn.close()

    workdir = tmp_path_factory.mktemp("record-drill-9567")
    verdict = rb.run_restore_drill(
        armed.dsn,
        MemoryStorage(),
        scratch,
        run=_pg_client_runner(workdir),
        key=_DRILL_KEY,
        workdir=workdir,
        reset_scratch=True,
    )
    yield Drilled(verdict=verdict, scratch=scratch, psycopg2=psycopg2)


def test_D8_the_artifact_restores_into_a_scratch_record_and_the_falsifier_fires(
    armed, drilled
):
    """D8: *restore from its own backup* — measured, and breakable on purpose.

    Two halves in one run, because the second is the first's falsifier: a green
    that cannot be broken on purpose is not evidence.

      1. `verdict == "RESTORED"` over the DECLARED table set — the drill's own
         verdict, and the set compared against the ten tables this file names
         rather than against the function the drill used to build it.
      2. delete one table's rows in the RESTORED copy and require the module's own
         `verify_restore` to RED, naming the table and its number.

    ⛔ What is deliberately NOT restated: the drill's per-table bound. Acceptance
    there is the window `[min(source, source_after), max(source, source_after,
    cut)]` (`compare_row_counts`, #9601 review cycle 4), `cut` is not exposed per
    table, and a leg that re-derived it would red a healthy artifact on a live
    record — the false red that window exists to remove. `RESTORED` IS that
    assertion; repeating it here in a weaker form would only look like coverage.

    Falsifier: change the record's table set — drop, rename or add a table in
    `THE_RECORD_TABLES` (`tortoise/record_projection.py`) — and
    `test_the_record_table_set_is_the_one_this_capstone_declares` reds naming the
    divergence with no database at all. That static pin is where this half's
    falsifier actually fires, and the reason it must exist beside this one is
    measured, not stylistic: a table dropped from `THE_RECORD_TABLES` leaves the
    dump's `--table=` list AND the comparison together, so the drill's own verdict
    has nothing left to disagree with, and where the dropped table references one
    that is still truncated, `reset_record_tables` refuses FIRST (measured
    2026-11-07 on `subject`: `cannot truncate a table referenced in a foreign key
    constraint … Table "subject" references "node_identity"`) — so the set
    comparison below is never reached. Read it as the set check at the verdict,
    not as the leg's only detector; the static pin is what NAMES a coverage loss,
    and an earlier red is still a red. Pointing the drill at the record itself is
    the other falsifier, and it is a refusal by design (`refuse_same_database`) —
    which is why this leg needs a second server rather than a second database on
    one.
    """
    from tortoise import record_backup as rb

    verdict = drilled.verdict
    assert verdict["verdict"] == "RESTORED", verdict
    assert verdict["scratch_reset"] is True, verdict
    assert set(verdict["tables"]) == set(RECORD_TABLES_DECLARED), (
        f"the drill measured {sorted(verdict['tables'])}, which is not the record's "
        f"table set {sorted(RECORD_TABLES_DECLARED)}"
    )

    # ── the falsifier: the same counting step, on a copy that lost a table ──
    source_counts = rb.record_row_counts(armed.dsn)
    # `journal_event` is APPEND-ONLY by trigger (`journal_event_is_append_only`
    # refuses DELETE unless `app.record_journal_purge=on`), so the falsifier picks
    # the largest table it can actually shorten. That is a property of the schema,
    # not a carve-out: the deletion is the measurement, and it has to be allowed.
    candidates = {
        name: count
        for name, count in source_counts.items()
        if name != "journal_event" and count > 0
    }
    assert candidates, (
        f"no record table outside the append-only journal carries a row ({source_counts}) "
        "— the falsifier needs rows it can delete"
    )
    victim = max(candidates, key=candidates.get)
    conn = drilled.psycopg2.connect(drilled.scratch)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM public.{victim}")
    finally:
        conn.close()
    restored = rb.record_row_counts(drilled.scratch, role="scratch target")
    with pytest.raises(rb.RecordRestoreVerifyError) as fired:
        rb.verify_restore(source_counts, restored)
    assert victim in str(fired.value), (
        f"the row-count step red but did not NAME the table that lost its rows "
        f"({victim}): {fired.value}"
    )
    assert str(source_counts[victim]) in str(fired.value), (
        f"the red does not carry the source's own number for {victim} "
        f"({source_counts[victim]}): {fired.value}"
    )


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
