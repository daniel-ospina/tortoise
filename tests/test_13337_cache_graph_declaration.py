"""A record tenant has ONE cache graph, DECLARED — read, never derived (#13337).

THE DEFECT THIS PINS, and it is the residual half of #12943. A record tenant
``(org_id, graph_id)`` does not name one cache graph when the name is *derived*
from the credential that wrote:

===========================  ==================================  =====================
credential class             cache graph the write lands in       derived spelling
===========================  ==================================  =====================
graph-bound                  ``org_<org>_<gid>``                  ``org_<org>_<gid>`` ✅
org-wide (default graph)     ``org_<org>``                        ``org_<org>_<gid>`` ⛔
===========================  ==================================  =====================

The record files BOTH writes under the same tenant, so the second row made
``record-reconcile`` report ``missing_in_cache = N`` for a tenant whose cache was
complete — the "the cache never sees the writes" shape #12943 was reopened for.
Measured end to end below, on a real Postgres record and a real FalkorDB
(``docs/evidence/12943-cache-leg/orgwide-two-graphs.json``): one org-wide
``_data_sdk`` write of an Object and an Event, then the reconciler on both names.

    org_<org>_<gid> (derived)   missing_in_cache=2   divergent   ← FALSE
    org_<org>      (the FACT)   missing_in_cache=0   ← and one field mismatch
                                on the Event, which is #12949's root, not this one

WHAT THIS FILE PINS, in order:

1. **The declaration's SOURCE ORDER** — the operator's stated name, then the
   control-plane fact (``public.graphs.namespace``, the same read
   ``hosted_api._assert_graph_owned`` makes), then the registry spelling — and
   the third one is LABELLED, so a verdict computed on an assumed coordinate can
   never read like one computed on a read one.
2. **The verbs no longer REFUSE for a missing flag.** ``record-reconcile`` and
   ``record-restore`` required ``--cache-graph`` because guessing opened an empty
   graph and graded a coherent cache divergent. A fact exists now, so the flag is
   optional; the refusal that remains is *"nothing could answer"*.
3. **The end-to-end acceptance #13337 states**: a tenant written through an
   ORG-WIDE credential reconciles ``clean`` on the name the control plane holds
   for it, with no ``--cache-graph`` — and the derived spelling is still
   ``divergent``, which is why the two are reported with different sources.

⛔ What is NOT here, named so the absence is visible: the write path does not
gain a control-plane read (a per-write lookup would put a network call on the hot
path, and ``cache_leg.note_tenant_graph``'s documented silence for the
graph-id-less shape is correct until one exists); relocating an org's default
graph off ``org_<org>`` is #10180's guarantee and an OWNER decision, not a lane's;
and the Event ``field_mismatches`` on the fact-named graph is #12949's root
(projection fidelity — the fold writes defaults the record leaves NULL), pinned
there rather than folded in here.
"""
from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tortoise import __main__ as cli  # noqa: E402
from tortoise.cache_graph import (  # noqa: E402
    SOURCE_CONTROL_PLANE,
    SOURCE_DERIVED,
    SOURCE_FLAG,
    CacheGraph,
    canonical_cache_graph_name,
    declared_cache_graph,
    namespace_from_control_plane,
)

RECORD_DB_ENV = "TEST_RECORD_DB_URI"


# ── 1. the declaration ───────────────────────────────────────────────────────


def test_a_stated_name_wins_and_says_so():
    """The operator's word is a fact and is never re-derived.

    A ``--cache-graph`` that a deployment has used for a year may be a name no
    control plane knows (a hand-built graph); silently preferring the namespace
    would grade a graph nobody reads.
    """
    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              cache_graph="hand_built_graph",
                              read_namespace=lambda o, g: "org_orgA_g_1")
    assert got == CacheGraph(name="hand_built_graph", source=SOURCE_FLAG,
                             detail="stated by the caller")
    assert got.is_fact


def test_the_control_plane_fact_wins_over_the_registry_spelling():
    """⛔ THE FIX. For the org's DEFAULT graph the two disagree, and the fact is
    right: its namespace is the legacy ``org_<org>``, which no derivation of
    ``org_<org>_<gid>`` can produce."""
    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              read_namespace=lambda o, g: "org_orgA")
    assert got.name == "org_orgA"
    assert got.source == SOURCE_CONTROL_PLANE
    assert got.is_fact
    assert got.name != canonical_cache_graph_name("orgA", "g_1")


def test_no_fact_falls_back_to_the_registry_spelling_and_LABELS_it():
    """The fallback is the old behaviour; what changes is that it can no longer
    be mistaken for a read coordinate."""
    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              read_namespace=lambda o, g: None)
    assert got.name == "org_orgA_g_1"
    assert got.source == SOURCE_DERIVED
    assert not got.is_fact, "a derived coordinate must never claim to be a fact"


@pytest.mark.parametrize("answer", [None, "", "   ", 7, ["org_orgA"]])
def test_a_non_fact_answer_is_not_a_fact(answer):
    """A blank or non-string namespace is ABSENCE, not a name.

    ``public.graphs.namespace`` is operator-supplied; a blank one would otherwise
    become a graph name of ``""`` — an empty graph graded as the tenant's cache,
    i.e. the wrong-verdict shape this declaration exists to end.
    """
    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              read_namespace=lambda o, g: answer)
    assert got.source == SOURCE_DERIVED
    assert got.name == canonical_cache_graph_name("orgA", "g_1")


def test_a_reader_that_RAISES_is_not_a_fact():
    """A control plane that cannot answer must not turn a sweep into a crash —
    the same posture ``record-reconcile``'s other bounded reads take."""
    def _boom(_org, _gid):
        raise RuntimeError("control plane unreachable")

    got = declared_cache_graph(org_id="orgA", graph_id="g_1",
                              read_namespace=_boom)
    assert got.source == SOURCE_DERIVED


def test_a_stated_name_is_whitespace_trimmed_and_a_blank_one_is_not_stated():
    assert declared_cache_graph(org_id="o", graph_id="g",
                               cache_graph="  org_o  ").name == "org_o"
    assert declared_cache_graph(org_id="o", graph_id="g",
                               cache_graph="   ").source == SOURCE_DERIVED


def test_the_control_plane_read_answers_NONE_when_the_control_plane_is_off(
        monkeypatch):
    """The self-host shape: no control plane ⇒ no fact ⇒ the labelled fallback.

    ⛔ It must not RAISE and must not guess: this function is called by two CLI
    verbs whose contract is "no verdict" (exit 2), not "exception" (exit 1).
    """
    from tortoise import supabase_control

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: False)
    assert namespace_from_control_plane("orgA", "g_1") is None


def test_the_control_plane_read_uses_the_SAME_filters_the_ownership_check_does():
    """Two readers of one fact must not disagree about it.

    ``hosted_api._assert_graph_owned`` is the shipped ownership read; this pins
    that the declaration asks the same table with the same two filters, so a
    namespace can never be resolved for a ``(id, org)`` pair the ownership check
    would refuse.
    """
    from tortoise import supabase_control

    seen = {}

    class _CP:
        def query(self, table, select=None, filters=None):
            seen.update(table=table, select=select, filters=filters)
            return [{"id": "g_1", "namespace": "org_orgA"}]

    import tortoise.cache_graph as cg

    original = supabase_control.is_supabase_enabled
    original_cp = supabase_control.get_control_plane
    try:
        supabase_control.is_supabase_enabled = lambda: True
        supabase_control.get_control_plane = _CP
        assert cg.namespace_from_control_plane("orgA", "g_1") == "org_orgA"
    finally:
        supabase_control.is_supabase_enabled = original
        supabase_control.get_control_plane = original_cp

    assert seen["table"] == "graphs"
    assert seen["filters"] == [("id", "eq", "g_1"), ("org_id", "eq", "orgA")]


# ── 2. the verbs stop refusing for the missing flag ──────────────────────────


def _reconcile_args(cache_graph=None):
    return argparse.Namespace(
        db="docker://localhost:6379",
        record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
        org="orgrepro",
        graph="grepro",
        cache_graph=cache_graph,
        timeout_ms=2000,
        json=True,
        journal=None,
    )


def test_record_reconcile_no_longer_refuses_for_a_missing_cache_graph(
        monkeypatch, capsys):
    """RED before this change: the verb exited 2 with ``--cache-graph is
    required`` and compared NOTHING.

    The shape under test is a self-host with no control plane, so the coordinate
    is the labelled derivation — the run must still happen, and the operator must
    be told the name was assumed. The record read is stubbed to fail because this
    leg is about WHICH REFUSAL fires, not about Postgres.
    """
    import types

    stub = types.ModuleType("psycopg2")

    class _Conn:
        def close(self):
            return None

    def _connect(*a, **k):
        raise OSError("no database here")

    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg2", stub)

    code = cli._cmd_record_reconcile(_reconcile_args(cache_graph=None))
    captured = capsys.readouterr()

    assert "--cache-graph is required" not in captured.err, (
        "the flag is optional now — a fact exists, so a missing flag is no "
        f"longer a refusal: {captured.err}")
    assert "DERIVED" in captured.err, (
        f"a derived coordinate must be LABELLED, not silent: {captured.err}")
    assert code == 2, "the record being unreachable is still no-verdict (2)"


def test_record_restore_no_longer_refuses_for_a_missing_cache_graph(
        monkeypatch, capsys):
    """The same hole, with a worse consequence (a wipe into a graph nobody
    reads), so it must be closed by the same declaration rather than by a second
    spelling of the rule."""
    import types

    stub = types.ModuleType("psycopg2")

    def _connect(*a, **k):
        raise OSError("no database here")

    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg2", stub)

    args = argparse.Namespace(
        db="docker://localhost:6379",
        record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
        org="orgrepro", graph="grepro", cache_graph=None,
        limit=None, allow_empty=False, json=True)
    code = cli._cmd_record_restore(args)
    captured = capsys.readouterr()

    assert "--cache-graph is required" not in captured.err, captured.err
    assert "DERIVED" in captured.err, captured.err
    assert code == 2


# ── 3. the end-to-end acceptance (both stores; SKIPS without them) ───────────


def _both_stores():
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the record")
    pytest.importorskip("psycopg2", reason="psycopg2-binary is the record's driver")
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        pytest.skip("TORTOISE_DB_URI not set — there is no cache to compare")
    return dsn


def _owner_dsn() -> str:
    return os.environ.get("OWNER_RECORD_DSN") or os.environ[RECORD_DB_ENV]


def _seed_default_graph(org: str, graph_id: str, namespace: str) -> None:
    """A tenant whose DEFAULT graph lives at the legacy namespace (#10180).

    ``public.graphs.namespace`` is the fact the declaration reads; this seeds it
    to the legacy spelling, which is the one graph the registry derivation is
    wrong about.
    """
    import psycopg2

    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING", (org, org))
            cur.execute(
                "INSERT INTO public.graphs (id, org_id, name, namespace) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (graph_id, org, "default", namespace))
    finally:
        conn.close()


def _count_record(org: str, graph_id: str) -> dict[str, int]:
    import psycopg2

    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    out: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for table in ("object", "event"):
                cur.execute(
                    f"SELECT count(*) FROM public.{table} "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph_id))
                out[table] = cur.fetchone()[0]
    finally:
        conn.close()
    return out


def _cache_counts(graph_name: str) -> dict[str, int]:
    from urllib.parse import unquote, urlparse

    from falkordb import FalkorDB

    uri = os.environ["TORTOISE_DB_URI"]
    parsed = urlparse(uri)
    db = FalkorDB(host=parsed.hostname, port=parsed.port or 6379,
                  password=unquote(parsed.password) if parsed.password else None)
    graph = db.select_graph(graph_name)
    out: dict[str, int] = {}
    for label in ("Object", "Event"):
        res = graph.query(f"MATCH (n:{label}) RETURN count(n)")
        out[label] = int(res.result_set[0][0]) if res.result_set else 0
    return out


def _fake_control_plane(namespace: str, table: dict):
    """The shipped read, against a control plane this box does not have.

    ⛔ It stands in for the CONTROL PLANE only — the declaration's query, its
    filters, its blank/absent handling and the fallback all run as shipped.
    """
    from tortoise import supabase_control

    table["is_supabase_enabled"] = supabase_control.is_supabase_enabled
    table["get_control_plane"] = supabase_control.get_control_plane

    class _CP:
        def query(self, _table, select=None, filters=None):
            return [{"id": "g", "namespace": namespace}]

    supabase_control.is_supabase_enabled = lambda: True
    supabase_control.get_control_plane = _CP


def _restore_control_plane(table: dict) -> None:
    from tortoise import supabase_control

    supabase_control.is_supabase_enabled = table["is_supabase_enabled"]
    supabase_control.get_control_plane = table["get_control_plane"]

def _org_wide_write(monkeypatch, org: str, graph_id: str, namespace: str,
                    *, event: bool) -> None:
    """One write through the ORG-WIDE ``_data_sdk`` door (no graph_id on the
    credential) — the class that opens the org's DEFAULT graph.

    The control plane is the only stand-in (``_resolve_record_tenant``, which is
    what it would answer); the SDK open, the graph fold, the journal append and
    the record merge all run as shipped.
    """
    from tortoise import hosted_api

    _seed_default_graph(org, graph_id, namespace)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", os.environ[RECORD_DB_ENV])
    monkeypatch.setenv(
        "TORTOISE_EVENT_LOG_BASE_DIR",
        os.environ.get("TORTOISE_EVENT_LOG_BASE_DIR")
        or str(Path(os.environ.get("TMPDIR", "/tmp")) / f"13337-{org}"))
    monkeypatch.delenv("TORTOISE_RECORD_ALLOCATE", raising=False)

    original = hosted_api._resolve_record_tenant
    hosted_api._resolve_record_tenant = lambda _o: (org, graph_id)
    try:
        org_dict = {"org_id": org, "graph_id": None, "graph_namespace": None,
                    "key_id": None, "legacy_full_access": True, "scopes": [],
                    "tier": "free", "delegation_depth": None,
                    "created_by_key_id": None, "max_users": 1, "max_graphs": 1,
                    "max_points": 10000, "max_api_keys": 2, "max_sessions": None}
        sdk = hosted_api._data_sdk(org_dict)
        try:
            assert sdk._get_proj().graph_name == namespace, (
                "the org-wide credential must open the org's default graph")
            sdk._create_object("alpha", objectKind="goal")
            if event:
                sdk.create_entity("event", "launch", eventKind="meeting")
        finally:
            sdk.close()
    finally:
        hosted_api._resolve_record_tenant = original


def test_an_ORG_WIDE_object_write_reconciles_CLEAN_on_the_DECLARED_name(
        monkeypatch, capsys):
    """THE ACCEPTANCE #13337 STATES, measured on the OBJECT shape.

    One org-wide write, then the product's own reconciler with NO
    ``--cache-graph``: the name is READ from ``public.graphs.namespace`` (the
    legacy ``org_<org>`` — the one graph the registry derivation is wrong about),
    and the verdict is ``clean``/exit 0.

    ⛔ RED on the tree before this change twice over: the verb refused for the
    missing flag (exit 2, nothing compared), and had the operator stated the
    DERIVED spelling the run would have read ``missing_in_cache=1`` on a cache
    that is complete.
    """
    _both_stores()

    run = uuid.uuid4().hex[:8]
    org = f"org13337a{run}"
    graph_id = f"g_13337a{run}"
    namespace = f"org_{org}"                 # the legacy default-graph spelling
    derived = canonical_cache_graph_name(org, graph_id)

    _org_wide_write(monkeypatch, org, graph_id, namespace, event=False)

    assert _count_record(org, graph_id) == {"object": 1, "event": 0}
    assert _cache_counts(namespace) == {"Object": 1, "Event": 0}
    assert _cache_counts(derived) == {"Object": 0, "Event": 0}, (
        "the derived spelling must be EMPTY — that is why it graded divergent")

    table: dict = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=None)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        code = cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    out = capsys.readouterr()

    assert code == 0, f"verdict must be clean once the name is READ: {out.err}"
    assert '"cache_graph_source": "control_plane"' in out.out, out.out
    assert f'"cache_graph": "{namespace}"' in out.out, out.out
    assert '"missing_in_cache": 0' in out.out, out.out
    assert "--cache-graph" not in out.err, (
        f"nothing was missing to state — no note/warning should fire: {out.err}")


def test_an_ORG_WIDE_event_is_PRESENT_once_the_name_is_READ(monkeypatch, capsys):
    """The EVENT shape — key presence, and the residual named.

    ``missing_in_cache`` must be 0: the record row and the projected cache node
    exist for the same write. The run is still ``divergent`` because of
    ``field_mismatches`` on the Event — the fold writes ``classificationLevel``/
    ``format``/``participants``/``startedAt`` defaults where the record holds
    NULL. ⛔ That is #12949's root (projection fidelity), NOT a lost write, and it
    is asserted here so a reader cannot mistake "divergent" for "absent"; it is
    deliberately not folded into this change.
    """
    _both_stores()

    run = uuid.uuid4().hex[:8]
    org = f"org13337e{run}"
    graph_id = f"g_13337e{run}"
    namespace = f"org_{org}"
    derived = canonical_cache_graph_name(org, graph_id)

    _org_wide_write(monkeypatch, org, graph_id, namespace, event=True)

    assert _count_record(org, graph_id) == {"object": 1, "event": 1}
    assert _cache_counts(namespace) == {"Object": 1, "Event": 1}
    assert _cache_counts(derived) == {"Object": 0, "Event": 0}

    table: dict = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=None)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    out = capsys.readouterr()

    assert '"missing_in_cache": 0' in out.out, out.out
    assert '"Object' not in out.out.split('"divergent_keys"')[1].split("]")[0], (
        f"the OBJECT must not be listed as divergent at all: {out.out}")
    # the residual: an Event field mismatch (#12949), never a missing key
    assert '"field_mismatches": 0' not in out.out, (
        "if the fold's record-NULL defaults are ever fixed, this leg must be "
        f"re-measured rather than silently re-read: {out.out}")

    # ── and the DERIVED spelling is the false divergence ────────────────────
    table = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=derived)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    derived_out = capsys.readouterr()

    assert '"missing_in_cache": 2' in derived_out.out, derived_out.out
    assert "WARNING: --cache-graph" in derived_out.err, (
        "a stated name that disagrees with a readable fact must WARN — a wrong "
        f"coordinate is a wrong verdict: {derived_out.err}")
