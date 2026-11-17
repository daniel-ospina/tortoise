"""#13043 review P1-1 / P1-2 / P2-2 / P2-4 — the operator door's preconditions.

WHY THIS FILE EXISTS. `record_allocator_cli.py` is the ONLY way to reach the
allocator, and the review found that it let an operator `seed` + `arm` while
`TORTOISE_RECORD_ALLOCATE` was unset — a state that EATS ROWS (the door is on the
enumerated arm while the graph is cut over, so `_refuse_an_enumerated_replay`
refuses every record while the journal keeps advancing; `write_live_record` is
fail-soft by contract, so this is a WARN per record and never an exception). It
also found that the flag is GLOBAL while the cutover is PER GRAPH, and that
nothing in this tree creates the counter row for a NEW graph
(`provision_graph_record` has zero hits), so every graph created after the
cutover is born uncut.

The fixes asserted here:

* `arm` REFUSES without `--acknowledge-cutover`, and the refusal is the
  documentation: it names the env flag, its HOME (the writer, not this shell),
  the order of operations, the per-graph scope, the absent creation hook, and
  the backfill lane.
* `sweep` finds born-uncut graphs the record has seen (read-only), so the hazard
  is discoverable rather than a sentence.
* `status` reports the RECORD's state and the flag SEPARATELY (P2-4), and the
  DSN name is IMPORTED from the seam rather than copied (P2-2).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
_spec = importlib.util.spec_from_file_location(
    "record_allocator_cli", _TOOLS / "record_allocator_cli.py")
assert _spec and _spec.loader
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


# ── P1-2: `arm` needs the acknowledgement, and the refusal IS the doc ──────

def test_arm_refuses_without_the_acknowledgement(capsys) -> None:
    rc = cli.main(["arm", "--org-id", "o", "--graph-id", "g"])
    assert rc == cli.EXIT_REFUSED, (
        "`arm` proceeded without an acknowledgement — seed+arm followed by a "
        "flag nobody rolled is the state that eats rows")
    captured = capsys.readouterr()
    err, out = captured.err, captured.out
    for required in ("TORTOISE_RECORD_ALLOCATE",   # the flag, by name
                     "WRITER",                     # …and whose environment it lives in
                     "halt", "seed", "reopen",     # the way back, in order
                     "provision_graph_record",     # the hook that is absent
                     "sweep",                      # how the hazard is found
                     "backfill_copy_turns"):       # the fourth position-row writer
        assert required in err, (
            f"the refusal does not mention {required!r}, so an operator learns "
            f"it only from the incident:\n{err}")
    # ⛔ THE ACK IS NOT A KEYSTROKE THE TOOL PERFORMS: nothing was armed.
    assert "arm" in out


def test_the_acknowledgement_is_only_a_gate_not_a_DSN_bypass(monkeypatch) -> None:
    """With the ack, `arm` gets PAST the gate and then fails on what it lacks.

    Non-vacuity for the refusal above: a `main` that refused EVERYTHING would
    pass that test. Unset the DSN and the acknowledged form must reach the DSN
    check (EXIT_USAGE) instead of the gate (EXIT_REFUSED).
    """
    monkeypatch.delenv(cli._record_env_contract()[0], raising=False)
    rc = cli.main(["arm", "--org-id", "o", "--graph-id", "g",
                   "--acknowledge-cutover"])
    assert rc == cli.EXIT_USAGE, (
        "an acknowledged `arm` with no DSN should fail on the DSN, not on the gate")


def test_every_graph_scoped_verb_still_requires_a_graph_id() -> None:
    """`sweep` is org-scoped; nothing else may run with `--graph-id` omitted."""
    for verb in ("status", "seed", "arm", "halt", "reopen"):
        with pytest.raises(SystemExit) as excinfo:
            cli.main([verb, "--org-id", "o", "--enumerated-floor", "0"])
        assert excinfo.value.code == cli.EXIT_USAGE
    # …and `sweep` does not require one: it reached the DSN check and returned
    # its own exit code rather than argparse's usage error.
    assert cli.main(["sweep", "--org-id", "o"]) == cli.EXIT_USAGE


# ── P2-2 / P2-4: one name for the DSN, two readings for "allocates" ────────

def test_the_DSN_name_is_IMPORTED_from_the_seam_not_copied() -> None:
    """A second spelling of one name is how an operator seeds one record and
    arms another — the comment already claimed "one name", the code copied it."""
    from tortoise import record_writer as rw

    dsn_env, allocate_env = cli._record_env_contract()
    assert dsn_env == rw.RECORD_DSN_ENV
    assert allocate_env == rw.RECORD_ALLOCATE_ENV
    source = (_TOOLS / "record_allocator_cli.py").read_text()
    assert 'RECORD_DSN_ENV = "TORTOISE' not in source, (
        "the CLI re-declares the DSN name instead of importing the seam's")


def test_the_sweep_query_is_org_scoped_and_reads_the_ROW_TABLE_not_graphs() -> None:
    """`tortoise_record_app` has NO grant on `public.graphs`, so a sweep over it
    would fail on exactly the role the CLI must connect as."""
    from tortoise import record_allocator as ra

    sql = ra.UNCUT_GRAPHS_SQL
    assert "public.journal_event" in sql
    assert "public.graphs" not in sql, (
        "UNCUT_GRAPHS_SQL reads public.graphs, which the record role cannot see")
    assert "public.record_cursor" in sql and "IS NULL" in sql
    assert sql.count("%s") == 1, "the sweep must be scoped to ONE org"


def test_status_reports_the_record_state_and_the_box_separately() -> None:
    """P2-4: `allocates` printed `true` on a graph whose live door is unarmed."""
    from tortoise import record_allocator as ra

    class _Cur:
        def __init__(self): self._rows = [(0,)]
        def execute(self, *a): pass
        def fetchone(self): return self._rows[0]
        def fetchall(self): return self._rows
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Conn:
        closed = False
        def cursor(self): return _Cur()
        def rollback(self): pass

    from tortoise import record_allocator as ra_mod
    original = ra_mod.read_cursor
    ra_mod.read_cursor = lambda *a, **k: (7, ra_mod.MODE_ACTIVE)
    try:
        payload = cli._cmd_status(
            _Conn(), type("A", (), {"org_id": "o", "graph_id": "g",
                                    "floor": None})())
    finally:
        ra_mod.read_cursor = original
    assert payload["allocates"] is True
    assert payload["allocates_scope"] == "record", (
        "`allocates` is not labelled: a reader takes it for the DOOR's state")
    assert payload["record_allocate_env"] == "TORTOISE_RECORD_ALLOCATE"
    assert "record_allocate_seen_here" in payload
    assert ra is ra_mod


# ── the sweep, against a real record: it must FIND the graph ───────────────

_RECORD_DSN = os.environ.get("TEST_RECORD_DB_URI")
pytestmark_db = pytest.mark.skipif(
    not _RECORD_DSN,
    reason="TEST_RECORD_DB_URI not set — no Postgres carrying the record migrations")


@pytestmark_db
def test_sweep_finds_a_born_uncut_graph_and_not_the_cut_over_one() -> None:
    """P1-1's mitigation, MEASURED: the hazard has to be findable.

    Two graphs of one org: A has journal rows and a counter row (cut over), B has
    journal rows and NO counter row (born uncut — the state a graph created after
    the cutover is in). A read-only sweep over the org must name B and only B, and
    must state that the floor is the JOURNAL's count rather than `row_max`.
    """
    import contextlib
    import uuid

    import psycopg2
    from tests.test_9078_record_written_by_production_path import (
        _app_role_dsn,
        _ensure_metering_schema,
        _ensure_record_schema,
    )

    admin = psycopg2.connect(_RECORD_DSN)
    admin.autocommit = True
    try:
        _ensure_record_schema(admin)
        _ensure_metering_schema(admin)
        _ensure_record_schema(admin)
        token = uuid.uuid4().hex[:10]
        org = f"org-13043-{token}"
        cut, uncut = f"graph-cut-{token}", f"graph-uncut-{token}"
        with admin.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                        (org, org))
            for graph in (cut, uncut):
                cur.execute(
                    "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s)",
                    (graph, org, graph))
                cur.execute(
                    "INSERT INTO public.journal_event "
                    "(org_id, graph_id, seq, event_type, payload) "
                    "VALUES (%s, %s, 1, 'PointAdded', %s)",
                    (org, graph, '{"id": "p"}'))
            cur.execute(
                "INSERT INTO public.record_cursor (org_id, graph_id, seq, mode) "
                "VALUES (%s, %s, 1, 'active')", (org, cut))

        app = psycopg2.connect(_app_role_dsn(_RECORD_DSN))
        app.autocommit = False
        try:
            payload = cli._cmd_sweep(
                app, type("A", (), {"org_id": org, "floor": None})())
        finally:
            with contextlib.suppress(Exception):
                app.rollback()
            app.close()
    finally:
        with contextlib.suppress(Exception):
            admin.close()

    assert [entry["graph_id"] for entry in payload["uncut_graphs"]] == [uncut], (
        f"the sweep did not isolate the born-uncut graph: {payload}")
    assert payload["floor_is_the_journals_count"] is True
    assert "cannot_see" in payload, (
        "the sweep does not state what it cannot see — a graph with no journal "
        "row is invisible, and silence reads as coverage")
