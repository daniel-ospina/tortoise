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
