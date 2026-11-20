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
    org_<org>      (the FACT)   missing_in_cache=0   field_mismatches=6
                                (all six on the Event — #12949's root, not this one)

WHAT THIS FILE PINS, in order:

1. **The declaration's SOURCE ORDER** — the operator's stated name, then the
   control-plane fact (``public.graphs.namespace``, the same read
   ``hosted_api._assert_graph_owned`` makes), then the registry spelling — and
   the third one is LABELLED, so a verdict computed on an assumed coordinate can
   never read like one computed on a read one.
2. **The verbs no longer REFUSE for a missing flag WHEN A FACT ANSWERS.**
   ``record-reconcile`` and ``record-restore`` required ``--cache-graph`` because
   guessing opened an empty graph and graded a coherent cache divergent. A fact
   exists now, so on that arm the flag is optional; the refusal that remains is
   *"nothing could answer"* — exit 2, nothing compared, nothing wiped. ⛔ That
   second arm is the recorded decision in ``#13337``'s body and it is pinned here
   (``test_record_reconcile_REFUSES_when_the_name_would_be_DERIVED``,
   ``test_the_DERIVED_arm_never_reaches_the_WIPE``), because a derived coordinate
   is a wrong VERDICT and, for the restore, a wipe of a graph nobody named.
3. **The end-to-end acceptance #13337 states**: a tenant written through an
   ORG-WIDE credential reconciles ``clean`` on the name the control plane holds
   for it, with no ``--cache-graph`` — the derived spelling is still
   ``divergent`` — AND the same tenant written through BOTH doors (org-wide and
   graph-bound) reconciles ``clean`` on ONE declared name (#13337 acceptance 2).

⛔ THESE END-TO-END LEGS MUST ACTUALLY RUN (#13337 review, finding 3). They skip
without a record DSN and a cache URI, and a collection that skips everywhere is a
headline claim with no coverage. ``_both_stores`` therefore refuses to certify by
absence: the two variables are provisioned by the SAME step, so exactly one of
them set is a real misconfiguration and is reported as a FAILURE, not a skip.
There is deliberately no ``REQUIRE_*`` switch — a switch nothing sets is an inert
guard, and the environment is the signal that cannot go stale.

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
import inspect
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


def test_the_control_plane_read_uses_the_SAME_filters_the_ownership_check_does(
        monkeypatch):
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

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(supabase_control, "get_control_plane", _CP)

    assert cg.namespace_from_control_plane("orgA", "g_1") == "org_orgA"

    assert seen["table"] == "graphs"
    assert seen["select"] == ["id", "namespace"], (
        f"the declaration must read the namespace, not the whole row: {seen['select']}")
    assert seen["filters"] == [("id", "eq", "g_1"), ("org_id", "eq", "orgA")]

    # ⛔ AND THE PIN MUST NOT BE VACUOUS (review P3): the assertions above read
    # what THIS caller sent. If the shipped ownership check stopped filtering on
    # the same pair, both functions could disagree about a namespace while this
    # test stayed green — so the ownership read is pinned in its OWN source.
    from tortoise import hosted_api

    owned = inspect.getsource(hosted_api._assert_graph_owned)
    assert 'select=["id", "namespace"]' in owned, (
        "the ownership check no longer selects (id, namespace) — the declaration "
        f"now asks a different question than the gate that authorises it:\n{owned}")
    assert '"id", "eq", graph_id' in owned, owned
    assert '"org_id", "eq"' in owned, owned


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


def _stub_psycopg2(monkeypatch):
    """A psycopg2 whose ``connect`` refuses — the record is unreachable here.

    Every leg below is about WHICH refusal fires (and whether the run got as far
    as the record at all), never about Postgres, so the driver is stubbed to the
    one behaviour that proves the verb got PAST the coordinate and then failed on
    the store.
    """
    import types

    stub = types.ModuleType("psycopg2")

    def _connect(*a, **k):
        raise OSError("no database here")

    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg2", stub)


def test_record_reconcile_REFUSES_when_the_name_would_be_DERIVED(
        monkeypatch, capsys):
    """⛔ AN ASSUMED COORDINATE IS NO VERDICT — the recorded decision, kept.

    ``#13337``'s body states it verbatim: the shipped refusal "is correct and
    must stay: guessing would make an empty graph read as ``divergent``, i.e. a
    wrong verdict instead of no verdict". The shape below is EXACTLY the one that
    sentence was written about — a self-host with no control plane, where
    ``is_supabase_enabled()`` is False, so an org-wide tenant's cache is
    ``org_<org>`` and the derived ``org_<org>_<gid>`` is a different, EMPTY graph.

    RED on the pre-fix tree for the opposite reason this row exists: the verb
    GRADED the derived spelling, so a coherent cache printed ``divergent`` and
    exited 3. Both conventions are named because an operator told only one
    spelling cannot tell a coherent cache from an empty one.
    """
    _stub_psycopg2(monkeypatch)

    code = cli._cmd_record_reconcile(_reconcile_args(cache_graph=None))
    captured = capsys.readouterr()

    assert code == 2, (
        f"an assumed coordinate produces NO verdict, not a verdict: {captured.err}")
    assert "org_<org_id>" in captured.err and "org_<org_id>_<graph_id>" in captured.err, (
        f"both conventions must be named: {captured.err}")
    assert "DERIVED" in captured.err and "org_orgrepro_grepro" in captured.err, (
        f"the run must name the spelling it would have assumed: {captured.err}")
    assert "UNKNOWN, not zero" in captured.err, captured.err


def _fact_control_plane(monkeypatch, namespace: str):
    """The declaration's READ, against a control plane this box does not have."""
    from tortoise import supabase_control

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(
        supabase_control, "get_control_plane",
        lambda: type("CP", (), {"query": lambda self, *_a, **_k: [
            {"id": "grepro", "namespace": namespace}]})())


def test_record_reconcile_ACCEPTS_the_name_once_a_FACT_answers(monkeypatch, capsys):
    """The half that CHANGES: with a fact answering, a missing flag is no longer
    a refusal — the run proceeds to the record and fails THERE (this box has no
    Postgres). That is the proof the flag stopped being the blocker."""
    _stub_psycopg2(monkeypatch)
    _fact_control_plane(monkeypatch, "org_orgrepro")

    code = cli._cmd_record_reconcile(_reconcile_args(cache_graph=None))
    captured = capsys.readouterr()

    assert code == 2, "still no verdict — but for the RECORD, not the flag"
    assert "--cache-graph is required" not in captured.err, captured.err
    assert "DERIVED" not in captured.err, captured.err
    assert "org_orgrepro" in captured.err or "unreadable" in captured.err, (
        f"the failure must be the record's: {captured.err}")


def test_record_restore_STILL_refuses_when_the_name_would_be_DERIVED(
        monkeypatch, capsys):
    """⛔ A DERIVED NAME IS NOT ENOUGH FOR A DESTRUCTIVE VERB.

    ``#9556`` pinned this refusal for the right reason — a wrong guess WIPES a
    graph and refills it, so an assumption here destroys a graph the operator
    never named. What changes is that the refusal is no longer BLIND: it names
    the spelling it would have used, so the flag is one read away instead of a
    guess the operator has to make blind.
    """
    _stub_psycopg2(monkeypatch)
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)

    args = argparse.Namespace(
        db="docker://localhost:6379",
        record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
        org="orgrepro", graph="grepro", cache_graph=None,
        limit=None, allow_empty=False, json=True)
    code = cli._cmd_record_restore(args)
    captured = capsys.readouterr()

    assert code == 2, "a destructive verb must refuse on an assumed coordinate"
    assert "--cache-graph is required" in captured.err, captured.err
    assert "org_orgrepro_grepro" in captured.err, (
        f"the refusal must name the spelling it would have used: {captured.err}")
    assert "Nothing was restored" in captured.err, captured.err


def test_a_WHITESPACE_ONLY_cache_graph_is_NOT_a_stated_name(monkeypatch, capsys):
    """⛔ `--cache-graph "   "` is the `$UNSET_VAR` shape, and it derived SILENTLY.

    ``declared_cache_graph`` strips the stated value, so a whitespace-only flag
    falls to the derived arm — but both verbs gated their message on
    ``and not args.cache_graph``, and ``"   "`` is TRUTHY. Neither the
    disagreement WARNING nor the derived note printed, so the run graded (and
    restored) a derived coordinate with no message at all. MEASURED on the
    pre-fix tree: ``record-restore --cache-graph "   "`` reached the wipe.

    The repo designs for this exact shape elsewhere (``--journal "$UNSET_VAR"``
    must survive as SUPPLIED so the verb can refuse it), so this pins that a
    blank-but-present value is treated as NOT STATED rather than as a fact.
    """
    _stub_psycopg2(monkeypatch)
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)

    code = cli._cmd_record_reconcile(_reconcile_args(cache_graph="   "))
    err = capsys.readouterr().err
    assert code == 2, err
    assert "DERIVED" in err, (
        f"a whitespace-only flag must not become a silent derived coordinate: {err}")

    restore = argparse.Namespace(
        db="docker://localhost:6379",
        record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
        org="orgrepro", graph="grepro", cache_graph="   ",
        limit=None, allow_empty=False, json=True)
    code = cli._cmd_record_restore(restore)
    err = capsys.readouterr().err
    assert code == 2, err
    assert "Nothing was restored" in err, err
    assert "org_orgrepro_grepro" in err, err


def test_a_PADDED_namespace_fact_does_not_fake_a_disagreement(monkeypatch, capsys):
    """Two readers of one value must agree on its NORMALISATION.

    ``graphs.namespace`` is operator-supplied TEXT, so it can carry padding;
    ``declared_cache_graph`` trims the STATED name. Comparing the raw column with
    the trimmed one printed ``WARNING: --cache-graph 'org_x' disagrees with the
    control plane's namespace (' org_x ')`` for an operator who stated exactly the
    fact — a warning that is FALSE and that trains its reader to ignore the one
    that matters.
    """
    _stub_psycopg2(monkeypatch)
    _fact_control_plane(monkeypatch, "  org_orgrepro  ")

    code = cli._cmd_record_reconcile(_reconcile_args(cache_graph="org_orgrepro"))
    out = capsys.readouterr()

    assert "WARNING" not in out.err, (
        f"the stated name IS the fact — no disagreement to report: {out.err}")
    assert code == 2, "the record is still unreachable on this box"


def test_the_control_plane_read_TRIMS_the_namespace(monkeypatch):
    """The ONE place a namespace enters the process is where it is normalised.

    ``declared_cache_graph`` trims what an operator TYPED; without trimming the
    read fact too, a caller comparing the two (both verbs' disagreement WARNING)
    reports a disagreement that does not exist. Pinned at the read, because that
    is what makes every consumer agree rather than each one re-stripping.
    """
    import tortoise.cache_graph as cg
    from tortoise import supabase_control

    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(
        supabase_control, "get_control_plane",
        lambda: type("CP", (), {"query": lambda self, *_a, **_k: [
            {"id": "g_1", "namespace": "  org_orgA  "}]})())

    assert cg.namespace_from_control_plane("orgA", "g_1") == "org_orgA"


def test_the_DERIVED_arm_never_reaches_the_WIPE(monkeypatch, capsys):
    """⛔ A REFUSAL THAT FIRES AFTER THE ``DETACH DELETE`` PROTECTS NOTHING.

    The removal of the old usage refusal was the dangerous half of this change: a
    derived coordinate handed to ``record-restore`` is a coordinate handed to a
    bulk wipe (``record_restore.rebuild_from_record``). The refusal must fire
    BEFORE the composition is entered, so this pins the ORDER and not just the
    exit code: the wipe callable is replaced by an ``AssertionError``.
    """
    import tortoise.record_restore as rr

    _stub_psycopg2(monkeypatch)
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)

    def _boom(*a, **k):
        raise AssertionError(
            "the wipe ran on an ASSUMED coordinate — the refusal must precede it")

    # The verb imports the seam INSIDE the function, so patching the module
    # attribute is what it will resolve — and `restore_from_record_view` is where
    # `confirm_destructive=True` is passed (record_restore's own note says a
    # future caller must not be able to wipe a graph by forgetting it).
    monkeypatch.setattr(rr, "restore_from_record_view", _boom)

    args = argparse.Namespace(
        db="docker://localhost:6379",
        record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
        org="orgrepro", graph="grepro", cache_graph=None,
        limit=None, allow_empty=False, json=True)
    code = cli._cmd_record_restore(args)

    assert code == 2, capsys.readouterr().err


def test_record_restore_ACCEPTS_the_name_once_a_FACT_answers(monkeypatch, capsys):
    """The half that changes: with the control plane answering, the SAME run does
    not refuse for the missing flag — it proceeds to the record and fails THERE
    (this box has no Postgres), which is the proof that the flag is no longer the
    blocker."""
    import types

    stub = types.ModuleType("psycopg2")

    def _connect(*a, **k):
        raise OSError("no database here")

    stub.connect = _connect
    monkeypatch.setitem(sys.modules, "psycopg2", stub)
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)

    table: dict = {}
    _fake_control_plane("org_orgrepro", table)
    try:
        args = argparse.Namespace(
            db="docker://localhost:6379",
            record_dsn="postgresql://tortoise_record_app:repro@localhost:5432/record",
            org="orgrepro", graph="grepro", cache_graph=None,
            limit=None, allow_empty=False, json=True)
        code = cli._cmd_record_restore(args)
    finally:
        _restore_control_plane(table)
    captured = capsys.readouterr()

    assert code == 2, "still no verdict — but for the RECORD, not the flag"
    assert "--cache-graph is required" not in captured.err, captured.err
    assert "record is unreadable" in captured.err, captured.err


# ── 3. the end-to-end acceptance (both stores; SKIPS without them) ───────────


def _both_stores():
    """The two stores these legs need — and a HALF-PROVISIONED job is a FAILURE.

    ⛔ The shape refused here is *certifying by absence* (#9551): a leg that needs
    both stores and SKIPS when one is missing cannot tell a job that provisioned
    both from a job that provisioned neither, and the second reads exactly like a
    pass.

    The signal is the ENVIRONMENT, never a switch: ``TEST_RECORD_DB_URI`` and
    ``TORTOISE_DB_URI`` are exported by the same CI step, so *both set* means the
    leg must run and *neither set* means there is nothing here to test (a laptop,
    the fast matrix). Exactly one set is a real misconfiguration, and it is
    reported as a FAILURE — a ``REQUIRE_*`` switch nobody exports would be an
    inert guard that only ever skips.
    """
    dsn = (os.environ.get(RECORD_DB_ENV) or "").strip()
    cache = (os.environ.get("TORTOISE_DB_URI") or "").strip()
    if not dsn and not cache:
        pytest.skip(f"neither {RECORD_DB_ENV} nor TORTOISE_DB_URI is set — "
                    "there is no record and no cache on this lane")
    if not dsn:
        pytest.fail(
            f"TORTOISE_DB_URI is set but {RECORD_DB_ENV} is not — a job that "
            "provisions the cache must provision the record too; this leg cannot "
            "certify by absence (#9551/#13337).")
    if not cache:
        pytest.fail(
            f"{RECORD_DB_ENV} is set but TORTOISE_DB_URI is not — the record was "
            "provisioned without the cache it is compared against; this leg "
            "cannot certify by absence (#9551/#13337).")
    try:
        import psycopg2  # noqa: F401 — the record's driver, nothing else
    except ImportError:
        pytest.fail(
            "both stores are provisioned but psycopg2 is not installed — the "
            "`postgres` extra is missing (`uv sync --extra postgres`), so this "
            "leg cannot run and must not skip (#13337).")
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


def _graph_bound_write(monkeypatch, org: str, graph_id: str, namespace: str,
                       *, event: bool = False) -> None:
    """One write through the GRAPH-BOUND ``_data_sdk`` door (#13337 acceptance 2).

    The credential carries the graph, so ``_data_sdk`` takes its graph-bound
    branch: ``_data_graph_name`` → ``graph_namespace``, the ownership pre-check,
    then ``_make_sdk(graph_name=<namespace>)``. When that namespace is the row's
    OWN namespace (which the seeded row is), both doors open the SAME graph —
    which is the acceptance: two credentials, one declared cache graph.
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
        org_dict = {"org_id": org, "graph_id": graph_id,
                    "graph_namespace": namespace,
                    "key_id": None, "legacy_full_access": True, "scopes": [],
                    "tier": "free", "delegation_depth": None,
                    "created_by_key_id": None, "max_users": 1, "max_graphs": 1,
                    "max_points": 10000, "max_api_keys": 2, "max_sessions": None}
        sdk = hosted_api._data_sdk(org_dict)
        try:
            assert sdk._get_proj().graph_name == namespace, (
                "the graph-bound credential must open ITS graph's namespace")
            sdk._create_object("beta", objectKind="goal")
            if event:
                sdk.create_entity("event", "launch2", eventKind="meeting")
        finally:
            sdk.close()
    finally:
        hosted_api._resolve_record_tenant = original


def test_BOTH_DOORS_write_ONE_cache_graph_and_reconcile_CLEAN_on_it(
        monkeypatch, capsys):
    """#13337 ACCEPTANCE ITEM 2 — the row that had no coverage at all.

    *"A tenant written through an org-wide credential AND through a graph-bound
    credential reconciles ``clean`` on the same ``--cache-graph`` name."*

    Both doors write; the record files both under ONE tenant; the cache holds both
    in ``org_<org>`` (the fact) and NOTHING in the derived spelling — and the
    reconciler is clean on that one name, whether it READS it or is given it. The
    second half matters: a stated name equal to the fact must not WARN, which is
    the normalisation pin one layer up.
    """
    _both_stores()

    run = uuid.uuid4().hex[:8]
    org = f"org13337b{run}"
    graph_id = f"g_13337b{run}"
    namespace = f"org_{org}"
    derived = canonical_cache_graph_name(org, graph_id)

    table: dict = {}
    _fake_control_plane(namespace, table)
    try:
        _org_wide_write(monkeypatch, org, graph_id, namespace, event=False)
        _graph_bound_write(monkeypatch, org, graph_id, namespace)
    finally:
        _restore_control_plane(table)

    assert _count_record(org, graph_id) == {"object": 2, "event": 0}, (
        "both doors must have filed under the SAME record tenant")
    assert _cache_counts(namespace) == {"Object": 2, "Event": 0}
    assert _cache_counts(derived) == {"Object": 0, "Event": 0}, (
        "the derived spelling must still be EMPTY — that is the false divergence")

    # (a) no flag — the fact is READ
    table = {}
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
    assert code == 0, f"clean on the READ name: {out.out}\n{out.err}"
    assert '"checked": 2' in out.out, out.out
    assert '"missing_in_cache": 0' in out.out, out.out
    assert '"extra_in_cache": 0' in out.out, out.out
    assert '"cache_graph_source": "control_plane"' in out.out, out.out

    # (b) the SAME name, STATED — the two credentials agree about it
    table = {}
    _fake_control_plane(namespace, table)
    try:
        args = _reconcile_args(cache_graph=namespace)
        args.db = os.environ["TORTOISE_DB_URI"]
        args.record_dsn = os.environ[RECORD_DB_ENV]
        args.org, args.graph = org, graph_id
        code = cli._cmd_record_reconcile(args)
    finally:
        _restore_control_plane(table)
    out = capsys.readouterr()
    assert code == 0, out.out
    assert '"cache_graph_source": "flag"' in out.out, out.out
    assert "WARNING" not in out.err, out.err


def test_a_HALF_PROVISIONED_job_FAILS_rather_than_skips(monkeypatch):
    """``_both_stores`` must not be able to certify by ABSENCE (#9551/#13337).

    Neither variable set is a laptop / the fast matrix → a skip. ONE of them set
    is a job that provisioned half its target (or a shell that exported half of
    it) → a FAILURE for each direction, because a leg that needs both and skips
    on the missing one reads exactly like a pass.
    """
    monkeypatch.delenv(RECORD_DB_ENV, raising=False)
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://localhost:6379")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        _both_stores()
    assert RECORD_DB_ENV in str(excinfo.value)

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv(RECORD_DB_ENV,
                       "postgresql://u:p@localhost:5432/record")
    with pytest.raises(pytest.fail.Exception) as excinfo:
        _both_stores()
    assert "TORTOISE_DB_URI" in str(excinfo.value)

    monkeypatch.delenv(RECORD_DB_ENV, raising=False)
    with pytest.raises(pytest.skip.Exception):
        _both_stores()


def test_the_armed_CI_job_supplies_BOTH_stores():
    """The ARMING is pinned, not only the helper (#13337 review finding 3).

    ``_both_stores()`` inferring the shape from the environment is worth nothing
    if no job supplies both variables: deleting the step, the FalkorDB provision
    or its teardown silently restores the permanently-unexecuted gate this change
    exists to close — a green job over a headline acceptance that never ran, which
    nothing can see from the outside. So the wiring is read out of the workflow
    itself and asserted here.
    """
    import yaml

    wf = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "python-ci.yml")
                        .read_text(encoding="utf-8"))
    steps = wf["jobs"]["test-record-postgres"]["steps"]
    uses = [str(s.get("uses", "")) for s in steps]

    runners = [s for s in steps
               if "tests/test_13337_cache_graph_declaration.py" in str(s.get("run", ""))]
    assert runners, (
        "no step of test-record-postgres RUNS this file — its end-to-end legs are "
        "back to skipping everywhere with nothing to notice it "
        "(TORTOISE_DB_URI is provisioned below and TEST_RECORD_DB_URI job-wide, so "
        "_both_stores() would fail the leg the moment it ran)")

    # The RECORD half comes from the job's own Postgres provisioners; the CACHE
    # half from the FalkorDB pair added for this file. Both stores must be
    # supplied BY THIS JOB, or `_both_stores()` skips (neither set) and the gate
    # is gone without a red anywhere.
    assert any(u.endswith("postgres-provision") for u in uses), (
        "this job provisions no record database, so the record half of the "
        "end-to-end legs cannot arm")
    provisions = [i for i, u in enumerate(uses) if u.endswith("falkordb-provision")]
    teardowns = [i for i, u in enumerate(uses) if u.endswith("falkordb-teardown")]
    assert len(provisions) == 1 and len(teardowns) == 1, (
        "expected exactly one FalkorDB provision/teardown pair, got "
        f"{len(provisions)}/{len(teardowns)} — the cache URI these legs read comes "
        "from that provision, and an unpaired one leaks a container")
    assert (steps[provisions[0]].get("with") or {}).get("uri_graph") == \
        "tortoise_test_matrix", (
        "the provision must export TORTOISE_DB_URI (`uri_graph`) or the cache "
        "half of `_both_stores()` is missing")
    assert teardowns[0] > provisions[0] and steps[teardowns[0]].get("if") == "always()", (
        "the teardown must follow the provision and run on failure/cancel too")
    # Ordering is the reason the provisioning is BELOW the twelve record legs and
    # not above them: `uri_graph` writes TORTOISE_DB_URI into $GITHUB_ENV, so every
    # LATER step of the job sees it — and those files are written to build their
    # own graphs with NO URI (this job's `TORTOISE_TEST_CARVE_OUT: 1`).
    record_legs = next(i for i, s in enumerate(steps)
                       if "test_9567_record_capstone.py" in str(s.get("run", "")))
    assert provisions[0] > record_legs, (
        "the FalkorDB provision runs BEFORE the record legs: `uri_graph` exports "
        "TORTOISE_DB_URI for every later step, so those twelve records-only files "
        "would silently be handed a server they were never written for")


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
