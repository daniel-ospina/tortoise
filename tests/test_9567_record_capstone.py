"""#9567 capstone — the record's D-items, EXECUTED end to end, not restated.

WHAT THIS FILE IS. The capstone's DoD has twelve items and each already has a
unit somewhere else (`test_9078_…` capture, `test_9556_…` restore,
`test_record_backup.py` drill, the PGlite tenancy suite, `test_quota.py` gate).
This file does not repeat any of those bodies: it drives the **production**
path once through a real Postgres record and asserts the end-to-end property
the capstone's objective sentence names — *armed, written, complete,
restorable, tenanted and quota-correct*. Where an item cannot be executed as an
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
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tortoise import cache_projection as cp
from tortoise import monitoring, record_writer
from tortoise import record_projection as rp
from tortoise.record_writer import use_record_tenant
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


@pytest.fixture(scope="module")
def armed(tmp_path_factory):
    """Arm the record, apply the migrations, capture ONCE through the SDK.

    Module-scoped because the capture is the slow part of this file and several
    legs read the same one. Everything else
    (tenant rows, counts, the `record_write_failures` delta) is taken INSIDE
    this run, so no assertion compares against a hardcoded number (the DoD's
    rule 4).

    The schema application, the app-role DSN and the tenant seed are IMPORTED
    from `tests/test_9078_record_written_by_production_path.py` rather than
    copied: a second `_ensure_record_schema` drifts, and the copy that drifts
    passes on a stale schema while the real one reds.
    """
    dsn, psycopg2 = _driver()
    from tests.test_9078_record_written_by_production_path import (
        _app_role_dsn,
        _ensure_record_schema,
        _seed_tenant,
    )

    mp = pytest.MonkeyPatch()
    try:
        tmp = tmp_path_factory.mktemp("capstone-9567")
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        _ensure_record_schema(conn)

        tag = uuid.uuid4().hex[:8]
        tenant = (f"org-9567-capstone-{tag}", f"graph-9567-capstone-{tag}")
        _seed_tenant(conn, tenant)

        # The WRITER connects as the record's app role (NOBYPASSRLS); the
        # provisioning superuser above stays for seeding and assertions.
        mp.setenv(record_writer.RECORD_DSN_ENV, _app_role_dsn(dsn))
        # Hermetic extraction (#3695/#5477 class): the same deterministic,
        # zero-network extractor `test_9078` pins on its capture legs.
        mp.setenv("TORTOISE_SESSION_LLM_MOCK", "1")

        sdk = TortoiseSDK(str(tmp / "graph.db"), event_log_path=str(tmp / "journal.jsonl"))
        session_id = f"capstone-{tag}"
        before = _counts(conn, tenant)
        lost_before = dict(monitoring.record_write_failure_counts())
        try:
            with use_record_tenant(*tenant):
                receipt = sdk.capture_session(_TURNS, session_id=session_id)
        finally:
            after = _counts(conn, tenant)
            lost = monitoring.record_write_failure_counts().get(
                "write_failed", 0
            ) - lost_before.get("write_failed", 0)
        yield Armed(
            dsn=dsn,
            conn=conn,
            tenant=tenant,
            sdk=sdk,
            session_id=session_id,
            receipt=receipt,
            journal=tmp / "journal.jsonl",
            before=before,
            after=after,
            failures_lost=lost,
        )
        sdk.close()
        conn.close()
    finally:
        mp.undo()


# ── D1 — armed, and every record table present ─────────────────────────────


def test_D1_every_record_table_exists_on_the_armed_record(armed, monkeypatch):
    """D1: reachable, and all ten record tables exist.

    Falsifier: rename/omit one table in the migrations → the table set is
    short and this reds naming it.
    """
    # The suite's `_no_ambient_record_arming` clears the arming keys per test
    # (hermetic by design), so the seam is re-armed here with the SAME app-role
    # DSN the fixture's capture used.
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, _app_role_dsn(armed.dsn))
    assert record_writer.RecordWriter().armed is True, (
        "the record DSN is installed but the seam does not read as armed"
    )
    with armed.conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        present = {row[0] for row in cur.fetchall()}
    missing = [t for t in rp.THE_RECORD_TABLES if t not in present]
    assert missing == [], f"record tables absent from the armed record: {missing}"
    assert len(rp.THE_RECORD_TABLES) == 10, (
        "the record table set changed size — re-measure D1's baseline"
    )


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


def test_D2_one_capture_writes_the_record_with_zero_write_failures(armed):
    """D2: one `SessionRecorded` ⇒ record rows, `record_write_failures` delta 0.

    Baseline and delta are measured in the same run (`armed`), never hardcoded.
    Falsifier: reintroduce a schema↔producer disagreement (#9264's `jsonb`
    `capture_redactions`) → the write fails and both assertions red.
    """
    assert armed.receipt.get("ok") is True, armed.receipt
    grew = {t: armed.after[t] - armed.before[t] for t in armed.after}
    for table in ("session", "event", "source"):
        assert grew[table] >= 1, f"the public capture path wrote no {table} row: {grew}"
    assert grew["journal_event"] >= 1, (
        f"layer 1 (journal_event) is empty — the capture did not write the position row: {grew}"
    )
    assert armed.failures_lost == 0, (
        f"{armed.failures_lost} record write(s) were counted as failed during the capture"
    )


# ── D3 — no write path bypasses the record ─────────────────────────────────


def test_D3_no_write_path_bypasses_the_record():
    """D3's STATIC half: every capture door writes the record (the gate, run).

    The gate is the production tool; running it is the measurement.
    Falsifier: a live door that drops the journal ordinal (`seq=None`) → the
    gate exits 1 naming the call site.

    ⛔ This is a WIRING check, not the ordering the criterion also names
    ("… in that order"). It cannot observe an order, which is why the three
    `test_D3_*_order_*` / `…_ahead_…` legs below measure the same write path at
    runtime with every door instrumented (#9567 D3, #11477).
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


# ── D3's ORDERING half — the three doors, observed at RUNTIME ──────────────
#
# The gate above answers "does a production caller reach the record". It cannot
# answer the other half of the criterion — *"the capture writes journal →
# record → cache in that order, with no cache-only path"* — because a static
# scan has no order to see (#9567 comment 6713293528 §2.1 grades D3 NOT
# MEASURED for exactly this reason; #11477 carries the item). So these legs
# instrument the three real doors on one real capture and assert the order that
# was OBSERVED, not the order the source claims.
#
# What the run measured on this tree, and what is therefore NOT claimed:
#
# * `journal → record` holds for every record the capture appends (leg 1).
# * A journal-first door writes the cache after its record (leg 2).
# * Some folds DO run ahead of their own append (`_create_entity`'s Object and
#   Event registration blocks — the KNOWN DEVIATIONS block at the top of
#   `tortoise/sdk.py`), so the criterion's letter is NOT satisfied on this path.
#   What leg 3 asserts is the property the contract can actually hold those
#   arms to: **a fold that runs ahead of its append must DECLARE that it is
#   doing so** (a lane is open), and the set of such folds that declare a
#   JOURNAL-FIRST lane is the recorded deviation population — PINNED, so the
#   leg reds when it grows (a fresh contradiction) or shrinks (a deviation
#   closed, and D3 must be re-graded). A silent cache-ahead write is the §17.1
#   failure; a declared journal-first one is a recorded deviation with a
#   carrier. The census of lanes lives in the run's artifact, never as a
#   constant here (DoD rule 4); the *set* is a pin, not a count.
# * The criterion's `with no cache-only path` half is measured by the fourth
#   leg, which takes the whole mutating-cache population (folded AND raw-handle)
#   and requires a journal append and an APPLIED record write behind each one.


#: The `(event_type, lane)` pairs that fold AHEAD of their own append while
#: declaring a JOURNAL-FIRST lane — the direction D3 forbids. This is NOT a
#: licence and not a green: it is the tree's RECORDED deviation set
#: (`tortoise/sdk.py`, module-top "KNOWN DEVIATIONS" block — `_create_entity`'s
#: Object/Event registration blocks fold first and are left as-is because
#: reordering them rides the #2194/#2295/#2309 first-registration hardening).
#: Pinned as a `(event_type, lane)` SET, never a count, so the leg reds in every
#: direction: a pair that is new (a fresh contradiction — e.g. a CAPTURE door
#: re-declaring its `_live_write_scope` reason while it still folds first; NOT
#: an `_apply_and_journal` reorder, which is not on the capture path and whose
#: leg-2 probe is trimmed out of this trace), or a pair that has GONE (a
#: deviation closed or re-declared apply-first — then D3 must be re-graded,
#: #9567/#11477).
_KNOWN_AHEAD_DEVIATIONS = frozenset({
    ("EventRecorded", "create_entity"),
    ("ObjectRegistered", "create_entity"),
})

#: The steps `tortoise/sdk.py`'s KNOWN DEVIATIONS block actually NAMES as apply-first
#: (`:4836`: *"`TortoiseSDK._create_entity`'s Object/**Subject**/Event registration
#: blocks are apply-first"*), all under the `create_entity` lane (`sdk.py:43309`).
#: Kept SEPARATE from the pin above on purpose: the pin is the population THIS
#: capture exercises, and a capture that creates a Subject legitimately yields
#: `("SubjectAdded", "create_entity")` without that being a fresh violation. The
#: failure message consults this set so an observed-but-not-pinned pair is
#: reported as *a recorded deviation this capture newly exercises* (extend the
#: pin after review) and only an UNRECORDED pair is called a violation.
_RECORDED_DEVIATION_EVENT_TYPES = frozenset(
    {"ObjectRegistered", "SubjectAdded", "EventRecorded"}
)


def _ahead_population_advice(gained: set, lost: set) -> str:
    """The remediation text for a moved journal-first-ahead population.

    The tree ALREADY records these arms as apply-first (`sdk.py:4836`: the
    Object/**Subject**/Event registration blocks, all under `create_entity` at
    `sdk.py:43309`). Three shapes need three different remedies, and conflating
    them printed contradictory advice for one change (review cycle 3):

    * the pin carried this pair and still does, but the capture now drives it →
      RECORDED, newly exercised (extend the pin);
    * the pin carried the event type under a DIFFERENT lane → LANE MOVED (a
      rename, or an arm newly claiming journal-first) — never reported as both
      "a fresh violation" and "the deviation was closed";
    * nothing records it → a fresh violation.
    """
    recorded = sorted(
        p for p in gained if p[1] == "create_entity" and p[0] in _RECORDED_DEVIATION_EVENT_TYPES
    )
    # Event types the PIN carried under the recorded lane and no longer does.
    pin_recorded_types = {
        p[0] for p in lost if p[1] == "create_entity" and p[0] in _RECORDED_DEVIATION_EVENT_TYPES
    }
    moved = sorted(p for p in gained if p[0] in pin_recorded_types)
    unrecorded = sorted(p for p in gained if p not in recorded and p not in moved)
    # Subtract only the types that actually MOVED. Subtracting `pin_recorded_types`
    # unconditionally emptied `lost` for this pin, making the GONE branch dead code
    # and leaving a genuinely-closed deviation with no advice at all (review cycle 4).
    moved_types = {p[0] for p in moved}
    lost = {p for p in lost if p[0] not in moved_types}
    lines = []
    if moved:
        lines.append(
            f"  \u2022 LANE MOVED on a recorded event type: {moved}. The "
            "KNOWN DEVIATIONS block at `tortoise/sdk.py:4835` records this arm as "
            "apply-first under `create_entity`, and the pin no longer carries that "
            "lane. The deviation's lane was RENAMED: update the KNOWN DEVIATIONS "
            "block at `sdk.py:4835` and extend the pin."
        )
    if recorded:
        lines.append(
            f"  \u2022 RECORDED, newly exercised by this capture: {recorded}. The "
            "KNOWN DEVIATIONS block at the top of `tortoise/sdk.py` names these "
            "arms, so this is a deviation being OBSERVED, not a fresh violation. "
            "Extend `_KNOWN_AHEAD_DEVIATIONS` so the pin covers the population "
            "the capture actually drives."
        )
    if unrecorded:
        lines.append(
            f"  \u2022 NOT RECORDED anywhere: {unrecorded}. This is a fresh "
            "violation of D3's order \u2014 a door claiming journal-first while "
            "folding first. Fix the door; do not widen the pin."
        )
    if lost:
        lines.append(
            f"  \u2022 Pinned pair(s) GONE: {sorted(lost)}. That deviation was "
            "closed or re-declared apply-first (`_apply_first_write_scope`). "
            "Re-grade D3 (#9567 / #11477) and shrink the pin \u2014 an assertion "
            "whose population moved must not stay green."
        )
    return "\n".join(lines) + ("\n" if lines else "")


@dataclass
class Door:
    """One observed call at one of the capture's three write doors."""

    door: str  # "journal" | "record" | "fold" | "cache"
    event_type: str | None = None
    event_id: str | None = None
    lane: str | None = None  # the declared cache lane ("cache" only)
    in_fold: str | None = None  # the fold a cache statement belongs to
    statement: str | None = None
    #: The record door's own verdict ("record" only): `RecordWriteOutcome.APPLIED`
    #: when a row was materialized, `None` when the seam was UNARMED, `REFUSED`
    #: otherwise. A CALL is not a WRITE, and only this field can tell them apart
    #: (`write_live_record` returns `None` when unarmed, `record_writer.py:2340`).
    outcome: Any = None


@dataclass
class Traced:
    """One capture with every write door instrumented, plus its receipt."""

    entries: list[Door]
    receipt: dict
    session_id: str
    tenant: tuple[str, str]
    sdk: Any


def _instrument(mp, entries: list[Door]) -> dict:
    """Wrap the three write doors so each call is RECORDED, then forwarded.

    The doors are the ones the criterion names, not a new abstraction:

    * **journal** — `tortoise.log.EventLog.append`, the truth store's append;
    * **record** — `record_writer.write_live_record`, THE production call site
      (`tools/record_wiring_check.py` pins that it is the only one);
    * **cache** — `FalkorProjection.apply` (the sanctioned fold) plus every
      MUTATING statement that reaches the projection's write handle
      (`_GuardedGraph.query`), classified by the SAME shared classifier the
      §17.1 guard uses (`_statement_writes`) so this instrument and the guard
      cannot disagree about what a write is.

    A cache statement's declared lane is read from the ContextVar the guard
    itself reads (`_CACHE_WRITE_AUTHORIZED`), so "declared" here means exactly
    what it means to the door: a scope was open, and its reason is the lane.

    Returns a mutable holder carrying the fold currently in progress, so a
    statement can be attributed to the fold that issued it.
    """
    from tortoise import log as tlog
    from tortoise.projection import (
        _CACHE_WRITE_AUTHORIZED,
        FalkorProjection,
        _GuardedGraph,
        _statement_writes,
    )

    open_fold = {"type": None}

    orig_append = tlog.EventLog.append

    def append(self, event):
        seq = orig_append(self, event)
        entries.append(
            Door("journal", event_type=event.get("type"), event_id=event.get("event_id"))
        )
        return seq

    orig_write = record_writer.write_live_record

    def write_live_record(record, **kwargs):
        out = orig_write(record, **kwargs)
        entries.append(
            Door(
                "record",
                event_type=record.get("type"),
                event_id=record.get("event_id"),
                # `getattr` because the unarmed door returns `None` — and the whole
                # point of carrying the outcome is that "called" ≠ "wrote".
                outcome=getattr(out, "outcome", None),
            )
        )
        return out

    orig_apply = FalkorProjection.apply

    def apply(self, event, **kwargs):
        prev, open_fold["type"] = open_fold["type"], event.get("type")
        entries.append(
            Door("fold", event_type=event.get("type"), event_id=event.get("event_id"))
        )
        try:
            return orig_apply(self, event, **kwargs)
        finally:
            open_fold["type"] = prev

    orig_query = _GuardedGraph.query

    def query(self, cypher, params=None, timeout=None):
        mutating = False
        try:
            mutating = bool(_statement_writes(cypher))
        except Exception:
            # An unparseable statement is not a write claim: the shared
            # classifier's own contract, and this instrument must never invent
            # a write the guard would not see.
            mutating = False
        if mutating:
            entries.append(
                Door(
                    "cache",
                    lane=_CACHE_WRITE_AUTHORIZED.get(),
                    in_fold=open_fold["type"],
                    statement=" ".join(str(cypher).split())[:80],
                )
            )
        return orig_query(self, cypher, params=params, timeout=timeout)

    mp.setattr(tlog.EventLog, "append", append)
    mp.setattr(record_writer, "write_live_record", write_live_record)
    mp.setattr(FalkorProjection, "apply", apply)
    mp.setattr(_GuardedGraph, "query", query)
    return open_fold


@pytest.fixture(scope="module")
def traced(armed, tmp_path_factory):
    """One MORE capture — the same doors, instrumented, on its own tenant.

    A separate capture rather than a replay of `armed`'s: the instrumentation
    has to be installed BEFORE the writes it observes, and `armed` (the D2
    baseline) is shared by several legs. The record is the same armed one, so
    the record leg is a real write and not a decline.
    """
    from tests.test_9078_record_written_by_production_path import _seed_tenant

    tmp = tmp_path_factory.mktemp("capstone-9567-order")
    tag = uuid.uuid4().hex[:8]
    tenant = (f"org-9567-order-{tag}", f"graph-9567-order-{tag}")
    _seed_tenant(armed.conn, tenant)

    sdk = TortoiseSDK(str(tmp / "graph.db"), event_log_path=str(tmp / "journal.jsonl"))
    entries: list[Door] = []
    mp = pytest.MonkeyPatch()
    try:
        # A WARM-UP write before the instrument is installed: the graph's schema
        # DDL and its `ep-epoch` mirror are issued on FIRST use, so without this
        # they land inside the measured region and read as cache writes ahead of
        # a journal they have nothing to do with (measured: 24 schema statements
        # + `MERGE (m:EpMeta)` before the capture's first append).
        with use_record_tenant(*tenant):
            sdk.create_point("observation", "warm-up: schema + ep-epoch, not observed")
        _instrument(mp, entries)
        session_id = f"capstone-order-{tag}"
        with use_record_tenant(*tenant):
            receipt = sdk.capture_session(list(_TURNS), session_id=session_id)
        # The measured census, for the run's artifact. A COUNT of what this run
        # observed is the evidence (DoD rule 1); it is printed, never asserted
        # against a constant (DoD rule 4 — a number in a test rots).
        counts = Counter(e.door for e in entries)
        undeclared = sum(1 for e in entries if e.door == "cache" and e.lane is None)
        print(
            "\n#9567 D3 write-order census: "
            f"journal={counts['journal']} record={counts['record']} "
            f"fold={counts['fold']} cache={counts['cache']} "
            f"cache-with-no-declared-lane={undeclared}"
        )
        # ⛔ THE DOOR WRAPPERS COME OFF HERE, BEFORE THE TESTS RUN. They are only
        # needed for the CAPTURE; leaving them installed (the previous shape put
        # `mp.undo()` in the `finally` below, i.e. at MODULE teardown) meant D4–D12
        # executed with `EventLog.append` / `write_live_record` /
        # `FalkorProjection.apply` / `_GuardedGraph.query` all wrapped. Harmless
        # today only by accident. A leg that needs a door instrumented installs its
        # own — leg 2 does, through this same `_instrument` helper.
        mp.undo()
        yield Traced(entries=entries, receipt=receipt, session_id=session_id,
                     tenant=tenant, sdk=sdk)
    finally:
        sdk.close()


def test_D3_order_the_record_write_follows_the_journal_append(traced):
    """D3 (journal → record): every appended record is **written** to the record.

    The assertion is pairwise on the OBSERVED call order, so it fails on the
    two directions the criterion forbids: a record write with no journal append
    behind it (an invented write) and a journal append that never reaches the
    record ("a write path bypasses the record").

    ⛔ A CALL IS NOT A WRITE (`record_writer.write_live_record` returns `None`
    when unarmed and a `RecordWrite` whose `outcome` may be `REFUSED` otherwise),
    so the count comparison alone cannot see a seam that declined every call.
    The `APPLIED` count is asserted beside it for exactly that reason: that is
    the `derived = replay(journal)` gap D3 exists to close.

    Falsifier: make `_write_record_projection` decline every call (the record
    then has the journal and not the row) → the counts diverge and this reds.
    Falsifier 2: make the seam decline instead of write (force the unarmed
    branch, or `durable=False`) → the counts still match and the `APPLIED`
    assertion is what reds.
    """
    journal = [e for e in traced.entries if e.door == "journal"]
    records = [e for e in traced.entries if e.door == "record"]
    assert traced.receipt.get("ok") is True, traced.receipt
    assert journal, "the capture appended nothing — the trace observed no journal door"
    assert len(records) == len(journal), (
        f"the capture appended {len(journal)} journal record(s) and made {len(records)} "
        "record write(s): a write path bypassed the record (or invented one)"
    )
    applied = [
        e for e in records
        if e.outcome is record_writer.RecordWriteOutcome.APPLIED
    ]
    assert len(applied) == len(journal), (
        f"the capture appended {len(journal)} journal record(s) and only "
        f"{len(applied)} reached the record as APPLIED (outcomes: "
        f"{dict(Counter(str(e.outcome) for e in records))}) — the door was "
        "CALLED and did not WRITE, which is the `derived = replay(journal)` "
        "gap D3 measures (an unarmed/refused call is not a record write)"
    )
    for appended, written in zip(journal, applied, strict=True):
        assert written.event_id == appended.event_id, (
            f"record write for {written.event_id!r} does not answer journal append "
            f"{appended.event_id!r} ({appended.event_type}) — the record is not the "
            "journal's own position"
        )
    first_journal = next(i for i, e in enumerate(traced.entries) if e.door == "journal")
    first_record = next(i for i, e in enumerate(traced.entries) if e.door == "record")
    assert first_journal < first_record, (
        "a record write ran before the first journal append — the record was written "
        "from something other than the journal (D3's cache-only shape)"
    )


def test_D3_order_a_journal_first_door_writes_journal_record_then_cache(
    armed, traced, monkeypatch
):
    """D3 (record → cache): on the sanctioned door, the fold comes LAST.

    `_apply_and_journal` is THE live-write door for a pre-built record (the
    chokepoint #7951 landed; `tools/record_wiring_check.py` names it the
    production call site). Its contract is the criterion's own sentence, so the
    measurement drives it once and reads the door order back: **journal append,
    then record write, then the fold and every cache statement the fold
    issues.**

    ⛔ THE RECORD IS RE-ARMED HERE, and that is load-bearing, not setup. The
    suite's autouse `_no_ambient_record_arming` clears the arming keys for the
    BODY of every test (hermetic by design — `tests/conftest.py`), and this leg
    drives the door INSIDE the body. Without the re-arm the record seam is
    unarmed for this probe: `write_live_record` returns `None`, writes nothing,
    and the `"record"` door below would be a CALL that landed no row — the
    `derived = replay(journal)` gap wearing a green. `test_D1` re-arms the same
    way and for the same reason.

    Falsifier: swap the door's order (fold before the append) → the fold's own
    statements precede its record write and this reds, naming both positions.
    """
    from tests.test_9078_record_written_by_production_path import _app_role_dsn
    from tortoise.record_writer import RECORD_DSN_ENV

    monkeypatch.setenv(RECORD_DSN_ENV, _app_role_dsn(armed.dsn))

    object_id = f"order-probe-{uuid.uuid4().hex[:10]}"
    mark = len(traced.entries)
    observed: list[Door] = []
    probe_mp = pytest.MonkeyPatch()
    try:
        # The module fixture UNDOES its door wrappers before yielding (they must not
        # stay installed while D4–D12 run), so this probe installs its own for its
        # own window. Same helper, so the doors are byte-identical to the capture's.
        _instrument(probe_mp, traced.entries)
        with use_record_tenant(*traced.tenant):
            traced.sdk._apply_and_journal(
                traced.sdk._get_proj(),
                {
                    "type": "ObjectRegistered",
                    "id": object_id,
                    "name": f"ordering probe {object_id}",
                    "objectKind": "concept",
                },
            )
        observed = list(traced.entries[mark:])
    finally:
        # ⛔ The trace is MODULE-scoped and shared by every leg in this file. This
        # probe used to leak its doors into it, so leg 3's "ahead" population
        # silently depended on leg 2 having run: run alone with
        # `-k "D3 and SILENTLY"`, leg 3's declared falsifier did NOT red. Trim the
        # probe back out so each leg reads the capture and nothing else.
        del traced.entries[mark:]
        probe_mp.undo()
    doors = [e.door for e in observed]
    assert "journal" in doors and "record" in doors and "fold" in doors, (
        "`_apply_and_journal` did not reach all three doors: observed "
        f"{doors} — the trace cannot order doors it never saw"
    )
    record_doors = [e for e in observed if e.door == "record"]
    assert record_doors and all(
        e.outcome is record_writer.RecordWriteOutcome.APPLIED for e in record_doors
    ), (
        "`_apply_and_journal` reached the record door but the seam did not APPLY "
        f"it (outcomes: {[str(e.outcome) for e in record_doors]}) — an unarmed or "
        "refused call is not a record write, so this probe cannot order a write "
        "it never made"
    )
    first_j = doors.index("journal")
    first_r = doors.index("record")
    first_f = doors.index("fold")
    assert first_j < first_r < first_f, (
        "`_apply_and_journal` did not write journal → record → cache: observed "
        f"{doors} (journal at {first_j}, record at {first_r}, fold at {first_f})"
    )
    folded = [(i, e) for i, e in enumerate(observed) if e.door == "cache"]
    assert folded, (
        "the fold issued no cache statement — the door folded an event and wrote "
        f"nothing, which is not the write the criterion describes: {doors}"
    )
    # ⛔ EVERY cache statement in this probe's window, not only the ones carrying
    # `in_fold`. A raw-handle write (`in_fold is None`) issued on this door before
    # its append is exactly the shape a cache-only path takes, and filtering to
    # `e.in_fold is not None` left it unexamined. `enumerate`, never
    # `observed.index(e)`: `Door` is a dataclass, so `.index` resolves by VALUE
    # and would return an earlier equal instance.
    for i, stmt in folded:
        assert i > first_r, (
            f"a cache statement ran before the door's record write ({i} <= "
            f"{first_r}: {stmt.statement!r}, in_fold={stmt.in_fold!r}) — the cache "
            "is ahead of the record on a door that claims journal-first"
        )


def test_D3_order_no_cache_write_runs_ahead_of_its_append_SILENTLY(traced):
    """D3 (no cache-only path): cache-ahead writes must DECLARE that order.

    The tree carries recorded apply-first deviations (`_create_entity`'s Object
    and Event registration blocks — the KNOWN DEVIATIONS block at the top of
    `tortoise/sdk.py`), and this run observes them: those folds reach the cache
    before their own journal append. What is not tolerable is the SILENT form —
    a cache write ahead of the journal with no scope open at the write handle —
    which is the §17.1 failure the guard measures and does not yet refuse.
    So: a fold that runs ahead of its append must be inside a DECLARED lane, and
    the set of such folds that declare a JOURNAL-FIRST lane is the tree's
    recorded deviation population — pinned as a measurement, so the leg reds when
    a pair appears (a fresh contradiction) or when a pinned pair is gone (a
    deviation closed, and D3 must be re-graded).

    ⛔ THE PREVIOUS FORM OF THIS LEG WAS INERT. It compared `stmt.lane` against
    the literal `"live-door"`, which is only `_live_write_scope`'s DEFAULT
    argument (`tortoise/projection/__init__.py:4063`) — every production caller
    passes an explicit reason, so the assertion could never fail on production
    code, and it passed on the very contradiction it documents: the ahead folds
    run under `_live_write_scope("create_entity")`, a journal-first lane. The
    tree already exports the mechanical discriminator (`_APPLY_FIRST_LANE`,
    `projection/__init__.py:4969`) and that is what classifies a lane here.

    Falsifier: change the lane a CAPTURE door declares while it still folds first —
    e.g. `_create_entity`'s `_live_write_scope("create_entity")` to any other
    non-apply-first reason → a `(event_type, lane)` pair that is not pinned
    appears and this reds naming it. (It used to name "reorder
    `_apply_and_journal`", which stopped being true once leg 2's probe is trimmed
    back out of this module-scoped trace: `_apply_and_journal` is not on the
    capture path, so a reorder there is leg 2's falsifier, not this one's.)
    """
    from tortoise.projection import _APPLY_FIRST_LANE

    folds = [(i, e) for i, e in enumerate(traced.entries) if e.door == "fold"]
    appends_by_type: dict[str | None, list[int]] = {}
    for i, e in enumerate(traced.entries):
        if e.door == "journal":
            appends_by_type.setdefault(e.event_type, []).append(i)

    # Pair the i-th fold of a type with the i-th append of that type: a fold
    # whose paired append lands AFTER it ran ahead of its own record.
    seen: dict[str | None, int] = {}
    ahead: list[tuple[int, Door]] = []
    for i, fold in folds:
        n = seen.get(fold.event_type, 0)
        seen[fold.event_type] = n + 1
        appends = appends_by_type.get(fold.event_type) or []
        if n < len(appends) and i < appends[n]:
            ahead.append((i, fold))

    assert ahead, (
        "no fold ran ahead of its append — this leg measured nothing. If the "
        "apply-first deviations were closed, delete the leg and re-grade D3 "
        "(#9567 D3 / #11477): an assertion whose population is empty is not a "
        "green measurement (DoD rule 1)"
    )

    journal_first_ahead: set[tuple[str, str]] = set()
    for i, fold in ahead:
        # The statements of this fold, up to the fold's own append.
        stop = next(
            (
                j
                for j, e in enumerate(traced.entries)
                if j > i and e.door == "journal" and e.event_type == fold.event_type
            ),
            len(traced.entries),
        )
        # ⛔ THE WINDOW IS EVERY STATEMENT THAT IS STILL AHEAD OF ITS OWN APPEND.
        # Taking every cache entry in `[i, stop)` was too wide (review cycle 3): a
        # statement preceded by a NESTED door's append is not ahead of its journal
        # — only ahead of the OUTER fold's — and a journal-first lane there is
        # correct code. Accusing it would send a fixer to reorder a conforming
        # door. So: fold-attached statements always qualify; a raw-handle one
        # qualifies only while nothing has journalled between the fold and it.
        stmts: list[Door] = []
        for j, e in enumerate(traced.entries[i:stop], start=i):
            if e.door != "cache":
                continue
            if e.in_fold == fold.event_type or (
                e.in_fold is None
                and not any(
                    traced.entries[k].door == "journal" for k in range(i + 1, j)
                )
            ):
                stmts.append(e)
        assert stmts, (
            f"the {fold.event_type} fold ran ahead of its append and issued no cache "
            "statement — the instrument observed a fold, not a write"
        )
        for stmt in stmts:
            assert stmt.lane is not None, (
                f"a cache write ahead of its journal append ran with NO declared lane "
                f"({stmt.statement!r}, fold {fold.event_type}, in_fold={stmt.in_fold!r}) "
                "— that is the §17.1 silent cache-ahead write D3 forbids"
            )
            # Classified by the tree's OWN discriminator, never a literal: a lane
            # without the `_APPLY_FIRST_LANE` prefix claims the journal-first order.
            if not str(stmt.lane).startswith(_APPLY_FIRST_LANE):
                journal_first_ahead.add((str(fold.event_type), str(stmt.lane)))

    assert journal_first_ahead == set(_KNOWN_AHEAD_DEVIATIONS), (
        "the journal-first-ahead fold population changed: observed "
        f"{sorted(journal_first_ahead)}, pinned "
        f"{sorted(_KNOWN_AHEAD_DEVIATIONS)}.\n"
        + _ahead_population_advice(
            journal_first_ahead - set(_KNOWN_AHEAD_DEVIATIONS),
            set(_KNOWN_AHEAD_DEVIATIONS) - journal_first_ahead,
        )
    )


def test_D3_order_no_cache_write_precedes_the_captures_first_journal_append_and_record(
    traced,
):
    """D3 (no cache-only path): no cache write precedes the capture's first record.

    D3's row states BOTH halves — *"journal → record → cache in that order, **with
    no cache-only path**"* — and pins the falsifier *"inject a cache write that
    skips the record → D3 must red"*. Neither leg above could red for it:

    * leg 1 looks only at the journal/record pair, never at a cache statement;
    * the `…_SILENTLY` leg bounds itself to the window between ONE ahead fold and
      its own append, so a cache-only write anywhere else in the capture is
      outside its population.

    ⛔ WHAT THIS LEG MEASURES, stated as narrowly as it actually is. It asserts the
    FLOOR: no mutating cache statement precedes the capture's first journal append
    and first APPLIED record write. It is NOT a per-write attribution, and a
    reviewer measured what that costs: a cache write injected MID-capture — after
    some append and its record have already landed — leaves this leg (and all
    three others) green, because the trace carries no descriptor tying a raw-handle
    statement to the append it skipped. Attribution of an arbitrary raw write is
    not recoverable from call order alone; see §4 of the artifact for the residue
    and why it stays on #11477.

    The population is also exactly `_GuardedGraph.query` — the handle's other
    mutating-capable verbs (`ro_query` and `execute_command`, which the module's
    own comments call out as write-capable) are NOT wrapped, so a write through
    them is invisible to every leg here.

    Falsifier: add
    `sdk._get_proj().g.query("CREATE (:X {id:'cache-only'})")` at the TOP of the
    measured region (before the capture) → the statement has no journal append and
    no APPLIED record write behind it, and this reds naming it. The mid-capture
    placement is NOT this leg's falsifier: it does not red, and that is the
    measured residue, not an oversight.
    """
    cache = [(i, e) for i, e in enumerate(traced.entries) if e.door == "cache"]
    assert cache, (
        "the capture issued no mutating cache statement — this leg measured nothing "
        "(DoD rule 1: an empty population is not a green measurement)"
    )
    journal = [i for i, e in enumerate(traced.entries) if e.door == "journal"]
    applied = [
        i
        for i, e in enumerate(traced.entries)
        if e.door == "record" and e.outcome is record_writer.RecordWriteOutcome.APPLIED
    ]
    assert journal and applied, (
        "this trace cannot distinguish a cache-only write: "
        f"journal={len(journal)} append(s), APPLIED record write(s)={len(applied)}. "
        "With no APPLIED record there is nothing for a cache write to be 'behind', "
        "and a leg asserting it would be vacuous"
    )
    first_journal = min(journal)
    first_applied = min(applied)
    orphans = [
        (i, e) for i, e in cache if i < first_journal or i < first_applied
    ]
    assert not orphans, (
        f"{len(orphans)} mutating cache statement(s) ran with no journal append and "
        "no APPLIED record write behind them — the cache-only path D3 forbids: "
        + "; ".join(
            f"[{i}] {e.statement!r} (lane={e.lane!r}, in_fold={e.in_fold!r})"
            for i, e in orphans
        )
    )


# ── D4/D5/D6 — the rebuild from the record only ────────────────────────────

@dataclass
class Restored:
    outcome: Any
    view: Any
    project: Any
    conn: Any


@pytest.fixture(scope="module")
def restored(armed, tmp_path_factory):
    """Rebuild a FRESH graph from the record, with the journal GONE.

    D4's measurement and D5's input: the journal file is renamed away and
    `TORTOISE_JOURNAL` is unset, so a restore that reached for it could not
    silently fall back. The view comes from `read_record_view` — the
    production read door of the SAME DSN (`scope_connection` installs the
    tenant on it) — and the composition is `restore_from_record_view`, which
    is what `python -m tortoise record-restore` runs.
    """
    from tortoise import record_restore as rrestore
    from tortoise.projection import FalkorProjection

    mp = pytest.MonkeyPatch()
    try:
        # ── D4: the journal is UNAVAILABLE ──
        if armed.journal.exists():
            armed.journal.rename(armed.journal.with_suffix(".jsonl.gone"))
        mp.delenv("TORTOISE_JOURNAL", raising=False)

        from tests.test_9078_record_written_by_production_path import _app_role_dsn

        _, psycopg2 = _driver()
        # NOT autocommit: `scope_connection` sets the tenant GUC
        # transaction-locally and reads it back before the first SELECT.
        conn = psycopg2.connect(_app_role_dsn(armed.dsn))
        view = cp.read_record_view(conn, org_id=armed.tenant[0], graph_id=armed.tenant[1])
        # The view is materialized; end the read transaction so this fixture
        # does not hold a lock on the record's tables for the whole module
        # (a later DDL drill — D10's DROP POLICY — needs ACCESS EXCLUSIVE).
        conn.rollback()
        assert view.nodes, (
            "the record has no nodes for this tenant — a rebuild from an "
            "empty view would prove nothing (D4's own refusal class)"
        )

        project = FalkorProjection(str(tmp_path_factory.mktemp("cache") / "cache.db"))
        outcome = rrestore.restore_from_record_view(
            view,
            org_id=armed.tenant[0],
            graph_id=armed.tenant[1],
            cache_graph=project.g.name,
            project=project,
        )
        yield Restored(outcome=outcome, view=view, project=project, conn=conn)
        conn.close()
        project.close()
    finally:
        mp.undo()


def test_D4_the_restore_runs_with_the_journal_gone(restored):
    """D4: the rebuild reads the RECORD — nothing reaches for the journal.

    Falsifier: make `record_restore` import a journal module → its own pin
    (`test_the_restore_cannot_reach_the_journal_replay`) reds; here, a restore
    that needed the journal could not have run at all (the file is renamed
    away).
    """
    outcome = restored.outcome
    assert outcome.nodes_written > 0, outcome.render()
    assert restored.outcome.coverage == "complete", outcome.render()


def test_D5_D6_the_restore_reproduces_the_record_per_label_and_per_predicate(restored):
    """D5/D6: equality, not "close" — per label and per relation type.

    D5's measurement is count equality against the baseline captured in this
    same run (the record view). D6's falsifier is the same axis: a projection
    that drops an edge family makes that predicate's cache count fall to 0
    while the record still carries it.

    ⚠️ D6's "all four R5 families are PRESENT" is a property of what a capture
    EMITS, not of this rebuild — the artifact records it as NOT MEASURED here
    and names `tests/test_9553_class4_structural_edge_records.py` as the
    command that owns it.
    """
    outcome, view = restored.outcome, restored.view
    refused = set(outcome.refused_keys)
    assert outcome.nodes_written + len(refused) == len(view.nodes), (
        "the restore dropped record nodes it did not report as refused: "
        f"{outcome.nodes_written} written + {len(refused)} refused "
        f"!= {len(view.nodes)} record"
    )

    expected = Counter(node.label for node in view.nodes if (node.label, node.key) not in refused)
    rows = restored.project.g.query(
        "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n"
    ).result_set
    got = {label: int(n) for label, n in rows}
    for label, count in expected.items():
        assert got.get(label, 0) == count, (
            f"label {label}: the record carries {count} node(s) and the "
            f"restored cache holds {got.get(label, 0)} — got {got}"
        )

    # Relations: equality per predicate, in BOTH directions.
    rel_rows = restored.project.g.query(
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n"
    ).result_set
    got_rels = {t: int(n) for t, n in rel_rows}
    # Relations are counted over the SAME admissible endpoint set as the nodes
    # above, and "admissible" is BOTH halves of it: an endpoint the restore
    # reported as `refused` was not written, and an endpoint the record view
    # does not carry as a node is one no statement can MATCH. Counting either
    # would red this leg for a "the projection dropped an edge family" reason
    # that is not the projection's — the bounded-restore contract
    # (`Outcome.refused_keys`, `record_restore.py`) permits the first, and a
    # relation statement is a no-op for the second.
    #
    # ⛔ The node boundary is what makes the BOTH-DIRECTIONS comparison sound:
    # D4 pins `nodes_written + refused == len(view.nodes)`, so every admissible
    # endpoint was written and every edge the cache can hold has both endpoints
    # in this set — a predicate the cache holds and this set does not is an
    # edge family the projection INVENTED, which a one-way `.get()` loop cannot
    # see (#10100 review P2).
    written_nodes = {(node.label, node.key) for node in view.nodes} - refused

    def _admissible(endpoint: tuple[str, str]) -> bool:
        return endpoint in written_nodes

    expected_rels = Counter(
        rel.rel_type
        for rel in view.relations
        if _admissible((rel.src_label, rel.src_key)) and _admissible((rel.tgt_label, rel.tgt_key))
    )
    # ⛔ NON-VACUITY, stated rather than assumed. A loop over an EMPTY counter
    # asserts nothing, so a capture/record regression that stops carrying edges
    # would leave this leg green while its row claims per-predicate equality —
    # and `coverage == "complete"` (D4) is NOT that guarantee: it is derived
    # from NODE keys, so "a restore that lands every node and no edge still
    # declares `complete`. That is not a hypothetical"
    # (`record_restore.py:243-265`). Measured on this capture: 3 predicates
    # (`CONTAINS`, `extractedFrom`, `aboutObject`; the artifact's D5 row).
    assert expected_rels, (
        "the record view carries no relations at all — the per-predicate "
        "comparison would assert nothing, so this leg would be green without "
        "measuring the edge axis (D4's `coverage == complete` does not imply "
        "otherwise: it is derived from node keys alone)"
    )
    assert dict(got_rels) == dict(expected_rels), (
        "per-predicate relation counts disagree — the record carries "
        f"{dict(expected_rels)} and the restored cache holds {dict(got_rels)}"
    )
    # The outcome's OWN accounting of edges the cache does not hold must agree
    # with the measurement above rather than hide a shortfall behind
    # `coverage == "complete"`. `relations_not_written` is
    # `relations_selected - relations_written` (`record_restore.py:244`), and
    # `relations_selected` is the projection's own plan size
    # (`projection/__init__.py:17448`), so 0 means every relation the projection
    # selected was measured back off the target graph.
    assert outcome.relations_not_written == 0, outcome.render()


# ── D7 — the raw turn text comes back, from the record only ────────────────


def test_D7_the_raw_turn_text_reads_back_from_the_record_only(armed):
    """D7 (turn text): the captured turns are in `point`, and read back as text.

    This is the one leg that drives capture → record → reader in a single run;
    the narrative TEXT half is NOT MEASURED here (its carrier is emitted by the
    layer-1 commit path, not by `capture_session`) — see the module docstring.
    Falsifier: revert the record's turn write (#10000) → no turns come back.
    """
    from tortoise.record_read import session_text
    from tortoise.record_text import scoped

    _, psycopg2 = _driver()
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    conn = psycopg2.connect(_app_role_dsn(armed.dsn))
    try:
        scoped(conn, org_id=armed.tenant[0])
        text = session_text(
            conn, org_id=armed.tenant[0], graph_id=armed.tenant[1], session_id=armed.session_id
        )
    finally:
        conn.close()
    assert text is not None, (
        "the record holds neither a narrative row nor a turn for the session this run captured"
    )
    for turn in _TURNS:
        assert turn["content"] in text.transcript, (
            f"the raw turn {turn['content']!r} did not come back from the "
            f"record; transcript={text.transcript!r}"
        )


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


def _app_conn(dsn: str):
    _, psycopg2 = _driver()
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    return psycopg2.connect(_app_role_dsn(dsn))


def test_D9_a_cross_tenant_read_is_empty_and_a_cross_tenant_write_is_refused(armed):
    """D9: both directions, as the owner's G8/D9 note requires.

    A cross-tenant READ returning 0 rows alone proves nothing — a missing GUC
    reads as 0 rows too. So the WRITE is asserted as well, and it must raise
    `insufficient_privilege` (a `WITH CHECK` violation), not a FK/CHECK error.

    Falsifier: `USING (true)` → the read returns tenant B's row; a
    `current_setting(...) IS NOT NULL` predicate → same. Both were measured red
    by the SQL suite (`supabase/tests/20261022000001_record_tenancy_posture.sql`).
    """
    org_a = armed.tenant[0]
    tag = uuid.uuid4().hex[:8]
    org_b, graph_b = f"org-9567-other-{tag}", f"graph-9567-other-{tag}"
    with armed.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (org_b, org_b),
        )
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (graph_b, org_b, graph_b),
        )
        # The identity row the point's composite FK points at. Seeded so the
        # cross-tenant INSERT below can be refused by the POLICY alone — a
        # missing FK target would refuse it for the wrong reason and the two
        # are indistinguishable in a caught error.
        cur.execute(
            "INSERT INTO public.node_identity (org_id, graph_id, "
            "node_label, node_key) VALUES (%s, %s, 'Point', %s) "
            "ON CONFLICT DO NOTHING",
            (org_b, graph_b, f"p-{tag}"),
        )
        cur.execute(
            "INSERT INTO public.point (org_id, graph_id, node_key, id, "
            "content) VALUES (%s, %s, %s, %s, %s)",
            (org_b, graph_b, f"p-{tag}", f"p-{tag}", "tenant B's claim"),
        )

    conn = _app_conn(armed.dsn)
    try:
        cur = rp.scope_connection(conn, org_a)
        cur.execute("SELECT count(*) FROM public.point WHERE org_id=%s", (org_b,))
        assert int(cur.fetchone()[0]) == 0, (
            "tenant A read tenant B's point — the tenant predicate is not the boundary"
        )
        cur.execute("SELECT count(*) FROM public.point WHERE org_id=%s", (org_a,))
        assert int(cur.fetchone()[0]) >= 1, (
            "tenant A cannot see its OWN rows — a zero-row cross-tenant read "
            "would then be an empty table, not a boundary"
        )
        # The write direction: scoped to A, filing under B. Every FK target
        # for B exists, so only the policy's WITH CHECK can refuse this.
        with pytest.raises(Exception) as exc:
            cur.execute(
                "INSERT INTO public.point (org_id, graph_id, node_key, id, "
                "content) VALUES (%s, %s, %s, %s, %s)",
                (org_b, graph_b, f"p-x-{tag}", f"p-x-{tag}", "wrong tenant"),
            )
        assert getattr(exc.value, "pgcode", None) == "42501", (
            "the cross-tenant write was refused for the wrong reason: "
            f"{exc.value!r} (pgcode={getattr(exc.value, 'pgcode', None)!r})"
        )
        conn.rollback()
    finally:
        conn.close()


def test_D10_a_record_table_without_its_policy_fails_closed(armed):
    """D10: drop one `*_app_tenant` policy → 0 rows and NO exception; put it
    back → the rows return.

    This is the behavioural drill the capstone's DoD names and that the SQL
    suite's §2 policy-set assertion does not perform (the prior capstone run
    measured that gap). Falsifier: leave the reader on a BYPASSRLS connection →
    the same query returns both tenants' rows, so the 0-row assertion would be
    an empty-table artifact, not a fail-closed one.
    """
    org_a = armed.tenant[0]
    conn = _app_conn(armed.dsn)
    try:
        cur = rp.scope_connection(conn, org_a)
        cur.execute("SELECT count(*) FROM public.point")
        before = int(cur.fetchone()[0])
        assert before >= 1, "the table is empty, so 'zero rows' would prove nothing"
        conn.rollback()

        with armed.conn.cursor() as admin:
            admin.execute("DROP POLICY point_app_tenant ON public.point")

        cur = rp.scope_connection(conn, org_a)
        cur.execute("SELECT count(*) FROM public.point")
        assert int(cur.fetchone()[0]) == 0, (
            "a table whose tenant policy is GONE still returned rows — the posture is fail-open"
        )
        conn.rollback()

        # Put the production policy back by RE-APPLYING the production
        # migration (its block is DROP IF EXISTS + CREATE, so it is the
        # supported repair path, not a hand-written copy of the policy).
        with armed.conn.cursor() as admin:
            admin.execute(
                (
                    REPO_ROOT
                    / "supabase"
                    / "migrations"
                    / "20261025000001_record_tenancy_role_and_guc.sql"
                ).read_text()
            )

        cur = rp.scope_connection(conn, org_a)
        cur.execute("SELECT count(*) FROM public.point")
        assert int(cur.fetchone()[0]) == before, (
            "the rows did not return after the policy was restored — this "
            "drill measured something other than the policy"
        )
        conn.rollback()
    finally:
        conn.close()


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
