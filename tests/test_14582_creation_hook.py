"""#14582 — the creation hook that gives a new graph its cursor row, and the gap.

THE STATE THIS EXISTS IN. Production is ARMED (`TORTOISE_RECORD_ALLOCATE=1`), so
on a graph with an `active` `record_cursor` row the record is the ONLY allocator
and `tortoise.live_write.write` skips the JSONL append (#14002). A graph created
after that cutover had **no** cursor row, and `write_live_record` is fail-soft by
contract, so nothing raised: the user's write succeeded, the record accumulated
nothing for that graph, and `/health` read `ok: true` throughout.

WHAT THIS FILE HOLDS DOWN, and how each leg could go quietly wrong:

* the HOOK **ARMS**, it does not only seed. A `seeded` row allocates nothing
  (`ALLOCATION_SQL` is scoped to `active`), so a hook that stopped at `seeded`
  would leave the graph's first record write refused `cursor_not_active` — the
  exact defect — while looking like a fix. This is the plan's `§A7.8` item 8 /
  M16 falsifier, and it is why the row is born `active` rather than `seeded`;
* every DOOR calls it (`hosted_api._provision_graph`, its own helper,
  `supabase_control.ensure_default_graph_row`'s three id-yielding arms, and
  `sdk._graph_create`'s registry arm) — pinned from the AST, because the call is
  one line and its absence is invisible at runtime until a graph is uncut;
* a graph the record ALREADY knows is REFUSED, not seeded (`#12894`'s divergence
  rule) — `SEED_SQL`'s own guard decides it, there is no second predicate here;
* `/health` CANNOT report `record.ok: true` on an armed deployment carrying an
  uncut graph, and it publishes COUNTS ONLY (the endpoint is unauthenticated);
* the BACKFILL reports `considered / seeded / refused` and is idempotent, and its
  `--dry-run` runs the real decision and writes nothing.

The DB legs (`TEST_RECORD_DB_URI`) are the point: a cursor row and a first
APPLIED write need a real record DSN. They skip without it and are armed by the
`test-record-postgres` job, whose leg gate reds on any skip.
"""
from __future__ import annotations

import ast
import contextlib
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tortoise import record_writer as rw

_HAS_RECORD_DB = os.environ.get("TEST_RECORD_DB_URI")
_ROOT = Path(__file__).resolve().parent.parent

#: One record that expands to node/identity mutations AND a position row — the
#: same shape `tests/test_12894_record_cursor_live.py` and
#: `tests/test_14002_record_only_journal.py` use, so the suites cannot disagree
#: about what a record looks like.
RECORD = {
    "type": "PointAdded",
    "ts": "2026-11-24T00:00:00Z",
    "id": "p-14582",
    "point": {"id": "p-14582", "content": "a new graph is born cut over",
              "pointKind": "claim"},
}

#: Modules whose doors this suite pins, and the function each hook must be in.
_DOORS = (
    ("tortoise/hosted_api.py", "_provision_graph_record"),
    ("tortoise/supabase_control.py", "_provision_default_graph_record"),
    ("tortoise/sdk.py", "_graph_create"),
)


# ── DB-free: the hook is wired, and it ARMS ────────────────────────────────
#
# These run in the fast matrix (registered on `core`), so a lane that deletes the
# hook, or downgrades it to a seed, reds without a database.

def _source(rel: str) -> str:
    return (_ROOT / rel).read_text()


def _call_names(node: ast.AST) -> set[str]:
    """Every name this node CALLS, by the callee's own identifier.

    Covers `f(...)`, `mod.f(...)`, and `mod.f` referenced as a value — the three
    spellings a door's one line can take. Deliberately structural: a substring
    search would be satisfied by a comment, which is exactly how
    `provision_graph_record` "existed" in two comments and zero call sites
    before this change.
    """
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        elif isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


def _functions(rel: str) -> dict[str, ast.AST]:
    tree = ast.parse(_source(rel))
    return {node.name: node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_every_creation_door_calls_the_hook():
    """⛔ THE WIRING PIN. A door that drops the call is the defect, not a style.

    The absence is invisible at runtime: the graph is still created, writes still
    succeed for the user, and only the record leg silently stops. So the census
    is pinned structurally rather than trusted to a review.
    """
    missing = []
    for rel, function in _DOORS:
        node = _functions(rel).get(function)
        assert node is not None, (
            f"{rel}: `{function}` is gone — the door the census found was "
            f"renamed or removed; re-run the census (`rg -n 'create_graph|"
            f"insert_graph|ensure_default_graph_row|_graph_create' tortoise/`) "
            f"and pin the new name rather than deleting this check")
        called = _call_names(node)
        if "provision_graph_record" not in called:
            missing.append(f"{rel}::{function}")
    assert not missing, (
        "these creation doors no longer call the record-cursor hook: "
        f"{missing}. A graph they create is born UNCUT — every record it writes "
        "is refused `cursor_not_active` while `write_live_record` (fail-soft by "
        "contract) reports success, so nothing reds and the loss only shows at a "
        "rebuild")


def test_the_hook_arms_and_does_not_only_seed():
    """⛔ THE PLAN'S M16 FALSIFIER, AS A PIN (`§A7.8` item 8): *"a hook that only
    seeds writes `DEFAULT 'seeded'`, which allocates nothing, and the test must
    go red on it"*.

    The live leg below proves the behaviour; this proves the MECHANISM, because a
    `seed`-only hook still writes a row and still looks like progress.
    """
    node = _functions("tortoise/record_allocator.py").get(
        "provision_graph_record")
    assert node is not None, "record_allocator.provision_graph_record is gone"
    called = _call_names(node)
    assert "seed" in called, "the hook must reuse the CLI's own seed (SEED_SQL)"
    assert "arm" in called, (
        "the hook never ARMS: a `seeded` row allocates nothing (`ALLOCATION_SQL` "
        "is scoped to `active`), so the graph's first record write still refuses "
        "`cursor_not_active` — the defect this hook closes")


def test_the_uncut_registry_is_bounded_and_counts_past_the_cap(monkeypatch):
    rw.reset_uncut_graphs()
    try:
        for i in range(rw.UNCUT_GRAPH_CAP + 8):
            rw.note_uncut_graph("org-14582", f"graph-{i}", reason="no_cursor_row")
        report = rw.armed_but_uncut()
        assert report["armed_but_uncut"] == rw.UNCUT_GRAPH_CAP
        assert report["armed_but_uncut_untracked"] == 8, (
            "past the cap the observation must still be COUNTED — a bounded map "
            "that silently stops counting is a bound that hides the overflow")
        assert rw.armed_but_uncut_reasons() == {"no_cursor_row": 32 + 8}
    finally:
        rw.reset_uncut_graphs()


def test_the_registry_publishes_no_tenant_identifiers():
    """`/health` is UNAUTHENTICATED: a graph id is the customer's own name."""
    rw.reset_uncut_graphs()
    try:
        rw.note_uncut_graph("org-SECRET", "graph-SECRET", reason="no_cursor_row")
        report = rw.armed_but_uncut()
        reasons = rw.armed_but_uncut_reasons()
    finally:
        rw.reset_uncut_graphs()

    def leaves(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield key
                yield from leaves(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from leaves(item)
        else:
            yield value

    published = set(leaves(report)) | set(leaves(reasons))
    assert "org-SECRET" not in published
    assert "graph-SECRET" not in published
    assert all(isinstance(v, (int, str)) for v in published)


# ── DB-free: /health cannot hide an uncut armed graph ──────────────────────

def _health(monkeypatch, *, dsn: str | None):
    from tortoise import hosted_api

    if dsn is None:
        monkeypatch.delenv(rw.RECORD_DSN_ENV, raising=False)
    else:
        monkeypatch.setenv(rw.RECORD_DSN_ENV, dsn)
    return hosted_api._record_health()


def test_health_is_ok_on_an_unarmed_deployment_that_has_seen_nothing(monkeypatch):
    rw.reset_uncut_graphs()
    try:
        block = _health(monkeypatch, dsn=None)
        assert block["armed"] is False
        assert block["armed_but_uncut"] == 0
        assert block["ok"] is True, (
            "an unarmed deployment CANNOT have an uncut graph — with no DSN "
            "there is no record for a graph to be missing a row in, so this "
            "must not red a deployment that runs without a record at all")
    finally:
        rw.reset_uncut_graphs()


def test_health_cannot_report_ok_with_an_uncut_armed_graph(monkeypatch):
    """FALSIFIER (c). Red before this change: `armed_but_uncut` did not exist and
    `ok` stayed `true` for exactly this state — production's measured shape."""
    rw.reset_uncut_graphs()
    try:
        block = _health(monkeypatch, dsn="postgresql://u:p@127.0.0.1:1/record")
        assert block["armed"] is True
        assert block["ok"] is True, "the control: nothing is uncut yet"
        rw.note_uncut_graph("org-14582", "graph-14582",
                            reason="no_cursor_row")
        block = _health(monkeypatch, dsn="postgresql://u:p@127.0.0.1:1/record")
        assert block["armed"] is True
        assert block["armed_but_uncut"] == 1
        assert block["armed_but_uncut_reasons"] == {"no_cursor_row": 1}
        assert block["ok"] is False, (
            "an armed deployment carrying an uncut graph is a DEPLOYMENT "
            "problem: every record that graph writes is refused while "
            "`write_live_record` reports success, so an `ok: true` here is the "
            "silence this field exists to end")
        # `degraded` keeps its documented window meaning (#10185) — it is not
        # re-used as a second, weaker statement of the same state.
        assert block["degraded"] is False
    finally:
        rw.reset_uncut_graphs()


# ── the live legs: a real record database ──────────────────────────────────

pytestmark_live = pytest.mark.skipif(
    not _HAS_RECORD_DB,
    reason="TEST_RECORD_DB_URI not set — no Postgres carrying the record "
           "migrations (the `test-record-postgres` job arms these legs)")


@pytest.fixture(scope="module")
def record_schema():
    """The record schema + the byte meter, applied ONCE for this module.

    The order is load-bearing (the byte meter's EXECUTE grant lives in a step
    that must run AFTER the metering migration creates the function), and
    re-applying it per test takes an ACCESS EXCLUSIVE lock on
    `public.record_cursor` — the #12399 deadlock when a pooled handle from the
    previous test still holds a share lock.
    """
    pytest.importorskip("psycopg2")
    from tests.test_9078_record_written_by_production_path import (
        _ensure_metering_schema,
        _ensure_record_schema,
        _record_db,
    )

    conn = _record_db()
    _ensure_record_schema(conn)      # the base shape the metering files depend on
    _ensure_metering_schema(conn)    # …14… — the meter's function comes into being
    _ensure_record_schema(conn)      # …16…+`…02…` — the GRANT step, re-applied
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


@pytest.fixture(autouse=True)
def _release_pooled_handles():
    """Hand back every idle record connection a test's writes opened (#9558)."""
    yield
    rw._release_pooled_connections()


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is PROCESS state; a leg that leaves it dirty biases the next."""
    rw.reset_uncut_graphs()
    yield
    rw.reset_uncut_graphs()


def _app_dsn() -> str:
    from tests.test_9078_record_written_by_production_path import _app_role_dsn

    return _app_role_dsn(os.environ["TEST_RECORD_DB_URI"])


@pytest.fixture()
def armed(monkeypatch):
    """The deployment ARMED, as production is: a record DSN (app role) + the flag."""
    monkeypatch.setenv(rw.RECORD_DSN_ENV, _app_dsn())
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    yield


@pytest.fixture()
def fresh_graph(record_schema):
    """A tenant pair the record has NEVER seen — no `record_cursor` row.

    This is the state every graph created after the cutover was in: a `graphs`
    row exists, the record knows nothing, and the armed door refuses.
    """
    token = f"{os.getpid()}-{uuid.uuid4().hex[:10]}"
    org, graph = f"org-14582-{token}", f"graph-14582-{token}"
    with record_schema.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (org, org))
        cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                    "VALUES (%s, %s, %s)", (graph, org, graph))
    record_schema.commit()
    return SimpleNamespace(org=org, graph=graph, admin=record_schema)


def _counter(conn, org, graph):
    with conn.cursor() as cur:
        cur.execute("SELECT seq, mode FROM public.record_cursor "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph))
        return cur.fetchone()


def _positions(conn, org, graph):
    with conn.cursor() as cur:
        cur.execute("SELECT seq FROM public.journal_event WHERE org_id = %s "
                    "AND graph_id = %s ORDER BY seq", (org, graph))
        return [row[0] for row in cur.fetchall()]


@pytestmark_live
def test_the_hook_gives_a_new_graph_a_row_and_its_first_write_applies(
        armed, fresh_graph):
    """FALSIFIER (a) — the whole point, end to end.

    RED before this change: the graph had no cursor row, so the armed door's
    allocation matched zero rows and `write_live_record` came back
    `outcome=REFUSED refusal="write_failed"` (the allocator's
    `cursor_not_active`) — counted and WARNed, never raised, so the write was
    lost from the record and nothing reddened.
    """
    org, graph = fresh_graph.org, fresh_graph.graph
    assert _counter(fresh_graph.admin, org, graph) is None, (
        "the premise of this leg is a graph the record has never seen")

    result = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert result["provisioned"] is True, result
    assert result["mode"] == "active", (
        f"the row must be born `active` (the plan's M16 falsifier), got "
        f"{result['mode']!r} — a `seeded` row allocates nothing, so the first "
        f"write below would still refuse `cursor_not_active`")
    assert result["seq"] == 0, "an empty journal seeds to 0, not 1"
    assert _counter(fresh_graph.admin, org, graph) == (0, "active")

    write = rw.write_live_record(RECORD, seq=None, journal_skipped=True,
                                 tenant=(org, graph))
    assert write.outcome.value == "applied", (
        f"the graph's FIRST record write must apply on an armed deployment once "
        f"the hook has run; got outcome={write.outcome!r} "
        f"refusal={getattr(write, 'refusal', None)!r}")
    assert (write.first_seq, write.last_seq) == (1, 1), (
        f"the first allocated position must be 1, got "
        f"{write.first_seq}..{write.last_seq}")
    assert _positions(fresh_graph.admin, org, graph) == [1], (
        "the position row must carry the number the counter issued")
    assert _counter(fresh_graph.admin, org, graph) == (1, "active")


@pytestmark_live
def test_the_hook_is_idempotent_and_never_re_bases_an_armed_graph(
        armed, fresh_graph):
    """A door can be RE-ENTERED (a retry, the default graph's self-heal), so the
    second call must be a no-op rather than a refusal — while never moving a
    counter that is already allocating."""
    org, graph = fresh_graph.org, fresh_graph.graph
    first = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert first["provisioned"] is True and first["seeded"] is True
    rw.write_live_record(RECORD, seq=None, journal_skipped=True,
                         tenant=(org, graph))
    assert _counter(fresh_graph.admin, org, graph) == (1, "active")

    again = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert again["provisioned"] is True, again
    assert again["already_cut_over"] is True
    assert _counter(fresh_graph.admin, org, graph) == (1, "active"), (
        "a re-entered hook must not re-base a live allocator")


@pytestmark_live
def test_a_graph_the_record_already_knows_is_refused_not_seeded(
        armed, fresh_graph):
    """FALSIFIER (b) — `#12894`'s divergence rule, decided by `SEED_SQL` itself.

    A graph whose record already holds positions and whose journal (as the door
    can enumerate it) is EMPTY is exactly the disagreement the rule forbids
    seeding over: the floor the creation door can state is 0, and the record
    holds 3. The hook must REFUSE and leave no row — the graph then keeps
    whatever allocator it had instead of being silently re-based.
    """
    org, graph = fresh_graph.org, fresh_graph.graph
    with fresh_graph.admin.cursor() as cur:
        for seq in (1, 2, 3):
            cur.execute(
                "INSERT INTO public.journal_event "
                "(org_id, graph_id, seq, event_type, payload) "
                "VALUES (%s, %s, %s, 'PointAdded', %s)",
                (org, graph, seq, '{"restored": true}'))
    fresh_graph.admin.commit()

    from tortoise import record_allocator as ra

    # The ALLOCATOR refuses (this is the divergence rule itself) ...
    with pytest.raises(ra.SeedRefused) as refused:
        ra.provision_graph_record(_conn(), org_id=org, graph_id=graph,
                                  enumerated_floor=0)
    assert refused.value.reason == "divergence", refused.value
    # ... and the DOOR reports it without raising, because a graph creation must
    # not fail on a record fault (the fail-soft contract this hook lives under).
    reported = rw.provision_graph_record(org_id=org, graph_id=graph)
    assert reported["provisioned"] is False
    assert reported["reason"] == "refused:divergence"
    assert _counter(fresh_graph.admin, org, graph) is None, (
        "a REFUSED graph must have NO counter row — a row here would arm an "
        "allocator over positions the journal cannot reproduce")
    assert rw.armed_but_uncut()["armed_but_uncut"] == 1, (
        "a refused graph is UNCUT, so it must be published")


@pytestmark_live
def test_the_door_helpers_provision_the_graph_they_just_created(
        armed, fresh_graph):
    """The DOORS' own call, executed for real.

    `hosted_api._provision_graph_record` and
    `supabase_control._provision_default_graph_record` are the one line each door
    adds; driving them (rather than the seam directly) is what proves the door's
    call — not just the function behind it — carries the right ids.
    """
    from tortoise import hosted_api, supabase_control

    # `record_cursor` carries a FOREIGN KEY to `public.graphs` (measured), so the
    # door's call can only ever provision a graph whose row exists — which is the
    # correct direction: a cursor row for a graph that does not exist would be a
    # phantom (see the census finding F1 on `_graph_create`'s Supabase arm).
    hosted_api._provision_graph_record(fresh_graph.org, fresh_graph.graph)
    assert _counter(fresh_graph.admin, fresh_graph.org,
                    fresh_graph.graph) == (0, "active"), (
        "the custom-graph door's call left no cursor row")

    # The default-graph mint: `ensure_default_graph_row` with the control-plane
    # transport faked (the function itself is real, including its guards).
    default_org = fresh_graph.org + "-default"
    # The mint's own precondition: `graphs.org_id REFERENCES public.organizations`
    # (the column was created as `team_id`), so the org row must exist first —
    # exactly as `provision_org` orders it in production (its RPC commits, THEN
    # the default row is minted; #10025 F1's reason for being best-effort).
    with fresh_graph.admin.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (default_org, default_org))
    fresh_graph.admin.commit()
    row = supabase_control.ensure_default_graph_row(
        _FakeControlPlane(fresh_graph.admin), default_org,
        "team_" + default_org)
    assert isinstance(row, str) and row
    assert _counter(fresh_graph.admin, default_org, row) == (0, "active"), (
        "the default-graph door's call left no cursor row — this is the org "
        "whose EVERY record write is refused `no_tenant`/`cursor_not_active` "
        "until it is seeded (#10025's forward half)")


class _FakeControlPlane:
    """Enough of `supabase_control`'s control plane to mint a default graph row.

    ⛔ ONLY THE TRANSPORT IS FAKE, AND THE INSERT IS REAL. `ensure_default_graph_row`
    runs its real guards, its real `insert_graph`, and its real hook — and the
    POST is forwarded to the real `public.graphs` table, because
    `record_cursor` carries a FOREIGN KEY to it (`record_cursor_org_id_graph_id_
    fkey`, measured): a purely in-memory mint would have the hook refused by the
    database and the leg would prove the FK rather than the hook.
    """

    def __init__(self, admin):
        self.admin = admin
        self.graphs: list[dict] = []
        self.orgs = {"graph_name": None}

    def query(self, table, *, select=None, filters=None, method=None,
              json_body=None):
        filters = filters or []
        if table == "graphs" and method == "POST":
            row = dict(json_body or {})
            # ⛔ THE IN-MEMORY ROW IS APPENDED **AFTER** THE REAL INSERT SUCCEEDS.
            # The other order lets a `query` that RAISED still be adopted by the
            # mint's convergent-read arm — i.e. a fake that reports a row the
            # database refused, which would let this leg pass on a mint that
            # never happened.
            with self.admin.cursor() as cur:
                cur.execute(
                    "INSERT INTO public.graphs (id, org_id, name) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (row["id"], row["org_id"], row.get("name")))
            self.admin.commit()
            self.graphs.append(row)
            return None
        if table == "graphs":
            rows = list(self.graphs)
            for column, op, value in filters:
                if op == "eq":
                    rows = [r for r in rows if r.get(column) == value]
                elif op == "neq":
                    rows = [r for r in rows if r.get(column) != value]
            return [{key: r.get(key) for key in (select or r)} for r in rows]
        if table == "organizations":
            return [{"graph_name": self.orgs["graph_name"]}]
        raise AssertionError(f"unexpected control-plane read: {table}")


# ── the backfill ───────────────────────────────────────────────────────────

@pytestmark_live
def test_the_backfill_reports_its_three_counts_and_is_idempotent(
        armed, record_schema):
    """`N considered / M seeded / K refused`, and a re-run is a no-op."""
    from tools import record_allocator_cli as cli

    org, graph = _uncut_graph(record_schema, "backfill", positions=(1, 2, 3))
    args = _backfill_args(org)

    dry = cli._cmd_backfill(_conn(), _backfill_args(org, dry_run=True))
    assert (dry["considered"], dry["seeded"], dry["refused"]) == (1, 1, 0), dry
    assert dry["graphs"][0]["outcome"] == "would_seed"
    assert _counter(record_schema, org, graph) is None, (
        "--dry-run wrote a row: the one thing it promises is that it does not")

    real = cli._cmd_backfill(_conn(), args)
    assert (real["considered"], real["seeded"], real["refused"]) == (1, 1, 0)
    assert _counter(record_schema, org, graph) == (3, "seeded"), (
        "a graph with 3 position rows must seed to 3 — its next write is 4")

    # ⛔ IDEMPOTENCE IS MEASURED AS "NOTHING LEFT TO DO", not as "the same three
    # counts again": the population is UNCUT graphs, so a graph the first run
    # seeded has left it. A second run that reported it again would be a verb
    # that cannot tell a healed graph from a broken one.
    again = cli._cmd_backfill(_conn(), args)
    assert (again["considered"], again["seeded"], again["refused"]) == (0, 0, 0), again
    assert _counter(record_schema, org, graph) == (3, "seeded"), (
        "the backfill must be IDEMPOTENT: a second run may not move the counter")


@pytestmark_live
def test_the_backfill_refuses_a_gapped_graph_with_its_reason(armed, record_schema):
    """A gap in the position axis (`COUNT(*) < MAX(seq)`) is a REFUSAL with both
    numbers and a working remedy — never a silent pick between them."""
    from tools import record_allocator_cli as cli

    org, graph = _uncut_graph(record_schema, "gap", positions=(1, 2, 4))
    payload = cli._cmd_backfill(_conn(), _backfill_args(org))
    assert (payload["considered"], payload["seeded"], payload["refused"]) == (1, 0, 1)
    refusal = payload["refusals"][0]
    assert refusal["reason"] == "divergence", refusal
    assert refusal["journal_count"] == 3 and refusal["journal_max_seq"] == 4
    assert refusal["remedy"] == (
        f"seed --org-id {org} --graph-id {graph} --enumerated-floor 4")
    assert _counter(record_schema, org, graph) is None


def _uncut_graph(conn, label: str, *, positions=()):
    """A tenant with position ROWS and no counter row — `sweep`'s population."""
    token = f"{os.getpid()}-{uuid.uuid4().hex[:10]}"
    org, graph = f"org-14582-{label}-{token}", f"graph-14582-{label}-{token}"
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (org, org))
        cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                    "VALUES (%s, %s, %s)", (graph, org, graph))
        for seq in positions:
            cur.execute(
                "INSERT INTO public.journal_event "
                "(org_id, graph_id, seq, event_type, payload) "
                "VALUES (%s, %s, %s, 'PointAdded', %s)",
                (org, graph, seq, '{"backfill": true}'))
    conn.commit()
    return org, graph


def _backfill_args(org: str, *, dry_run: bool = False):
    return SimpleNamespace(org_id=org, command="backfill", graph_id=None,
                           dsn=_app_dsn(), floor=None, journal=None,
                           dry_run=dry_run, arm_graphs=False,
                           acknowledge_cutover=False, as_json=False)


def _conn():
    psycopg2 = pytest.importorskip("psycopg2")
    conn = psycopg2.connect(_app_dsn())
    conn.autocommit = False
    return conn
