"""#13337 — one record tenant, TWO cache graphs, measured from the reconciler.

THE MEASUREMENT THIS FILE EXISTS FOR. `#12943` was reopened because a tenant's
record held 8 objects / 39 events / 5 operators while the cache graph it was
counted in held none. The count was taken on the **registry spelling**
(`org_<org>_<gid>`). An ORG-WIDE credential opens the org's DEFAULT graph, whose
namespace is the legacy `org_<org>` — a different graph — so the reconciler graded
an empty spelling and reported the whole tenant missing. The write was never lost.

This harness reproduces that end to end and writes the capture the README quotes:
one run-unique throwaway tenant, ONE write through the shipped org-wide door
(`hosted_api._data_sdk` with no `graph_id` on the credential), the record counted
through the OWNER role, the cache counted on BOTH candidate graphs, and the
product's own reconciler run on each.

Run (both stores real; the cache URI comes from
``uv run python tools/test_lane.py uri``):

    TEST_RECORD_DB_URI=postgresql://tortoise_record_app:PW@127.0.0.1:33943/record \\
    OWNER_RECORD_DSN=postgresql://record:record@127.0.0.1:33943/record \\
    TORTOISE_DB_URI=docker://:@127.0.0.1:16391/tortoise_test_matrix \\
    EVENTS_BASE=/tmp/13337-orgwide \\
    uv run python docs/evidence/12943-cache-leg/orgwide_two_graphs.py

⛔ Never point any of these at a shared deployment: every run mints a fresh
``org_<hex>`` / ``g_<hex>`` pair, and nothing is deleted (``public.journal_event``
is append-only by trigger, so a pair could not be reused anyway).

⛔ Control-plane stand-in, named so it is not mistaken for production: this box has
no Supabase, so ``hosted_api._resolve_record_tenant`` is patched to the seeded pair
(every door would otherwise be measured as the ``no_tenant`` refusal). That stands
in for the CONTROL PLANE only — the SDK open, the graph fold, the journal append
and the record merge all run as shipped, and the capture records it.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

RECORD_DB_ENV = "TEST_RECORD_DB_URI"
OWNER_ENV = "OWNER_RECORD_DSN"


def _owner_dsn() -> str:
    dsn = os.environ.get(OWNER_ENV) or os.environ.get(RECORD_DB_ENV)
    if not dsn:
        raise SystemExit(
            f"{RECORD_DB_ENV} must name a Postgres carrying the record's "
            "migrations (and OWNER_RECORD_DSN its BYPASSRLS owner) — this "
            "harness reads across tenants for seeding and counting only")
    return dsn


def _seed(org: str, graph_id: str, namespace: str) -> None:
    import psycopg2

    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                (org, org))
            # The FACT the declaration reads. `namespace` is the legacy spelling
            # for an org's default graph (#10180) — the one graph the registry
            # derivation is wrong about.
            cur.execute(
                "INSERT INTO public.graphs (id, org_id, name, namespace) "
                "VALUES (%s, %s, %s, %s)",
                (graph_id, org, "default", namespace))
    finally:
        conn.close()


def _seed_registry(org: str, graph_id: str, namespace: str) -> None:
    """The ``:Graph`` node the registry-lane ownership check reads."""
    from tortoise import hosted_api

    sdk = hosted_api._make_sdk(namespace="registry")
    try:
        sdk._get_registry().query(
            "MERGE (g:Graph {id:$id, org_id:$org}) "
            "SET g.namespace = $ns, g.status = 'active'",
            params={"id": graph_id, "org": org, "ns": namespace})
        sdk._get_registry().query(
            "MERGE (t:Team {id:$id}) SET t.onboarding_state = '{}'",
            params={"id": org})
    finally:
        sdk.close()


def _record_counts(org: str, graph_id: str) -> dict[str, int]:
    import psycopg2

    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    out: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for table in ("object", "event", "point"):
                cur.execute(
                    f"SELECT count(*) FROM public.{table} "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph_id))
                out[table] = cur.fetchone()[0]
    finally:
        conn.close()
    return out


def _cache_counts(graph_name: str) -> dict[str, int] | None:
    from urllib.parse import unquote, urlparse

    from falkordb import FalkorDB

    uri = (os.environ.get("TORTOISE_DB_URI") or "").strip()
    parsed = urlparse(uri)
    db = FalkorDB(host=parsed.hostname, port=parsed.port or 6379,
                  password=unquote(parsed.password) if parsed.password else None)
    if graph_name not in set(db.list_graphs() or []):
        return None
    graph = db.select_graph(graph_name)
    out: dict[str, int] = {}
    for label in ("Object", "Event", "Point"):
        res = graph.query(f"MATCH (n:{label}) RETURN count(n)")
        out[label] = int(res.result_set[0][0]) if res.result_set else 0
    return out


def _reconcile(org: str, graph_id: str, cache_graph: str) -> dict:
    """The product's own instrument, on one name — the verdict this whole
    measurement is about."""
    import psycopg2

    from tortoise import record_reconcile as rr
    from tortoise.projection import FalkorProjection

    conn = psycopg2.connect(os.environ[RECORD_DB_ENV])
    proj = None
    try:
        from tortoise.cache_projection import read_record_view

        view = read_record_view(conn, org_id=org, graph_id=graph_id)
        proj = FalkorProjection.from_uri(os.environ["TORTOISE_DB_URI"],
                                         graph_name=cache_graph)
        scan = rr.scan_cache_view(proj.g)
        result = rr.reconcile_record_and_cache(
            rr.record_key_map(view), scan.view, placed=scan.placed)
        return result.as_dict()
    finally:
        if proj is not None:
            proj.close()
        conn.close()


def _write_org_wide(org: str, graph_id: str) -> str:
    """ONE write through the shipped ORG-WIDE door. Returns the graph it opened."""
    from tortoise import hosted_api

    original = hosted_api._resolve_record_tenant
    hosted_api._resolve_record_tenant = lambda _org: (org, graph_id)
    try:
        org_dict = {"org_id": org, "graph_id": None, "graph_namespace": None,
                    "key_id": None, "legacy_full_access": True, "scopes": [],
                    "tier": "free", "delegation_depth": None,
                    "created_by_key_id": None, "max_users": 1, "max_graphs": 1,
                    "max_points": 10000, "max_api_keys": 2, "max_sessions": None}
        sdk = hosted_api._data_sdk(org_dict)
        try:
            target = sdk._get_proj().graph_name
            sdk._create_object("alpha", objectKind="goal")
            sdk.create_entity("event", "launch", eventKind="meeting")
        finally:
            sdk.close()
    finally:
        hosted_api._resolve_record_tenant = original
    return target


def main() -> int:
    from tortoise.cache_graph import (
        canonical_cache_graph_name,
        declared_cache_graph,
        namespace_from_control_plane,
    )

    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        raise SystemExit(f"{RECORD_DB_ENV} is required (a throwaway record)")
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        raise SystemExit("TORTOISE_DB_URI is required (a shared cache)")
    os.environ["TORTOISE_RECORD_DSN"] = dsn
    os.environ["TORTOISE_EVENT_LOG_BASE_DIR"] = os.environ.get(
        "EVENTS_BASE", "/tmp/13337-orgwide")
    os.environ.pop("TORTOISE_RECORD_ALLOCATE", None)

    run = uuid.uuid4().hex[:8]
    org = f"org13337cap{run}"
    graph_id = f"g_13337cap{run}"
    namespace = f"org_{org}"
    derived = canonical_cache_graph_name(org, graph_id)

    _seed(org, graph_id, namespace)
    _seed_registry(org, graph_id, namespace)

    cache_target = _write_org_wide(org, graph_id)

    declared = declared_cache_graph(org_id=org, graph_id=graph_id,
                                    read_namespace=lambda o, g: namespace)
    # What the SHIPPED reader says on a box with no control plane: `None` — which
    # is exactly why the derived arm is still reachable in production and still
    # labelled. Recorded rather than assumed.
    shipped_fact = namespace_from_control_plane(org, graph_id)

    capture = {
        "issue": ["#13337", "#12943"],
        "org": org,
        "graph_id": graph_id,
        "run": run,
        "credential": "org-wide (graph_id None)",
        "control_plane_stand_in": "_resolve_record_tenant patched; no Supabase on "
                                  "this box",
        "cache_target_the_sdk_opened": cache_target,
        "graphs_namespace_fact": namespace,
        "derived_spelling": derived,
        "declared": {"name": declared.name, "source": declared.source,
                     "detail": declared.detail},
        "shipped_reader_on_this_box": shipped_fact,
        "record": _record_counts(org, graph_id),
        "cache_on_fact_name": _cache_counts(namespace),
        "cache_on_derived_spelling": _cache_counts(derived),
        "reconcile_on_derived_spelling": _reconcile(org, graph_id, derived),
        "reconcile_on_fact_name": _reconcile(org, graph_id, namespace),
    }

    out = os.environ.get("SUMMARY_OUT",
                         "docs/evidence/12943-cache-leg/orgwide-two-graphs.json")
    Path(out).write_text(json.dumps(capture, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: capture[k] for k in (
        "credential", "cache_target_the_sdk_opened", "graphs_namespace_fact",
        "record", "cache_on_fact_name", "cache_on_derived_spelling")}, indent=2))
    for key in ("reconcile_on_derived_spelling", "reconcile_on_fact_name"):
        r = capture[key]
        print(f"{key}: verdict={r['verdict']} checked={r['checked']} "
              f"missing_in_cache={r['missing_in_cache']} "
              f"field_mismatches={r['field_mismatches']}")
    print(f"capture written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
