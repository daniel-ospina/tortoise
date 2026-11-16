"""#12943 — does a write that the RECORD accepted reach the CACHE?

Measurement harness for the write-side projection gap. One throwaway tenant per
run (``public.journal_event`` is append-only by trigger, so a fixed pair cannot
be reused), written through the SAME seam the hosted data plane uses
(``hosted_api._make_sdk(graph_name=…)`` + ``_bind_record_tenant``), then counted
in BOTH stores.

The four hypotheses from #12943, each answered with a number rather than an
argument:

  H1  the cache leg is not on the ``_data_sdk``/SDK path at all
  H2  the cache write is attempted and fails invisibly (and is counted nowhere)
  H3  the graph handle resolved to a different namespace than the record tenant
  H4  a deferred/async projection that had not run yet

Run:

    TEST_RECORD_DSN=postgresql://record:record@127.0.0.1:33500/record \\
    TORTOISE_DB_URI=docker://:@127.0.0.1:16419/tortoise_test_matrix \\
    TORTOISE_RECORD_DSN=postgresql://record:record@127.0.0.1:33500/record \\
    uv run python docs/evidence/12943-cache-leg/measure.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def _record_dsn() -> str:
    """The APPLICATION role's DSN — what the live writer connects as.

    ``record_projection`` REFUSES a role that BYPASSES RLS, so this must be
    ``tortoise_record_app`` (see ``provision_record.py``).
    """
    dsn = (os.environ.get("TEST_RECORD_DB_URI")
           or os.environ.get("TORTOISE_RECORD_DSN"))
    if not dsn:
        raise SystemExit("no TEST_RECORD_DB_URI / TORTOISE_RECORD_DSN in env")
    return dsn


def _owner_dsn() -> str:
    """The OWNER DSN — seeding and counting only, never the write path.

    The owner BYPASSes RLS, which is exactly why the writer refuses it: the
    counts below are the MEASUREMENT, not a read path, and a tenant-scoped read
    of rows the write path just filed would answer 0 for the wrong reason.
    """
    dsn = os.environ.get("OWNER_RECORD_DSN")
    if not dsn:
        raise SystemExit("no OWNER_RECORD_DSN in env")
    return dsn


def _psycopg2():
    import psycopg2  # noqa: PLC0415
    return psycopg2


def _seed_tenant(conn, org: str, graph: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING", (org, org))
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING", (graph, org, graph))


def _record_counts(conn, org: str, graph: str) -> dict[str, int]:
    out = {}
    with conn.cursor() as cur:
        for table in ("object", "subject", "point", "event", "relation",
                      "journal_event"):
            cur.execute(
                f"SELECT count(*) FROM public.{table} "
                "WHERE org_id = %s AND graph_id = %s", (org, graph))
            out[table] = cur.fetchone()[0]
    return out


def _cache_counts(db, graph_name: str) -> dict[str, int]:
    out = {}
    g = db.select_graph(graph_name)
    for label in ("Object", "Subject", "Point", "Event", "Source"):
        try:
            res = g.query(f"MATCH (n:{label}) RETURN count(n) AS c")
            out[label] = int(res.result_set[0][0]) if res.result_set else 0
        except Exception as exc:                       # noqa: BLE001
            out[label] = f"ERR {type(exc).__name__}: {exc}"
    return out


def _falkor_client():
    from falkordb import FalkorDB  # noqa: PLC0415
    uri = os.environ["TORTOISE_DB_URI"]
    # docker://:<pw>@host:port/dbname
    body = uri.split("://", 1)[1]
    creds, rest = body.rsplit("@", 1)
    pw = creds.split(":", 1)[1] if ":" in creds else ""
    hostport = rest.split("/", 1)[0]
    host, _, port = hostport.partition(":")
    return FalkorDB(host=host, port=int(port or 6379), password=pw or None)


def main() -> int:
    run = uuid.uuid4().hex[:10]
    org = f"org12943{run}"
    graph_id = f"g_12943{run}"
    ns = f"org_{org}_{graph_id}"

    dsn = _record_dsn()
    os.environ.setdefault("TORTOISE_RECORD_DSN", dsn)
    # The record's only durable source is the JSONL journal (`write_live_record`
    # refuses `no_journal` without it), and production arms it
    # (`TORTOISE_EVENT_LOG_BASE_DIR=/data/events`). One dir per run.
    base = Path(os.environ.get("EVENTS_BASE", f"/tmp/12943-events-{run}"))
    base.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TORTOISE_EVENT_LOG_BASE_DIR", str(base))
    psycopg2 = _psycopg2()
    # OWNER connection: seeding + counting only (see `_owner_dsn`).
    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    _seed_tenant(conn, org, graph_id)

    from tortoise import hosted_api, monitoring  # noqa: PLC0415

    before_metrics = dict(monitoring.metrics())

    # ── the door under test: exactly `_data_sdk`'s graph-bound branch ────────
    sdk = hosted_api._make_sdk(graph_name=ns)
    # The by-value carrier `write_live_record(tenant=…)` reads. The REST/MCP
    # lanes resolve this same pair through `record_writer.record_tenant_for`;
    # that resolver refuses on a box with no control plane (`control_plane_ids`
    # False), which is this harness, so the pair is bound directly — the SDK's
    # own documented seam, not a second resolution.
    sdk._bind_record_tenant(org, graph_id)
    print(f"tenant           org={org} graph={graph_id}")
    print(f"cache graph      {ns}")
    print(f"TORTOISE_DB_URI  {os.environ['TORTOISE_DB_URI']}")
    print(f"record DSN set   {'yes' if os.environ.get('TORTOISE_RECORD_DSN') else 'no'}")

    writes: list[dict] = []
    results = {}
    try:
        results["object:alpha"] = sdk.create_object("alpha", objectKind="goal")
        results["object:beta"] = sdk.create_object("beta", objectKind="goal")
        results["point:gamma"] = sdk.create_point("goal", "gamma content",
                                                  is_episodic=False)
        results["point:delta"] = sdk.create_point("goal", "delta content",
                                                  is_episodic=False)
        o = results["object:alpha"]
        p = results["point:gamma"]
        results["operator"] = sdk.create_operator(
            "IMPL", o["id"], [p["id"]], "supports")
    except Exception as exc:                            # noqa: BLE001
        print(f"WRITE RAISED: {type(exc).__name__}: {exc}")

    after_metrics = dict(monitoring.metrics())
    deltas = {}
    for k, after in after_metrics.items():
        before = before_metrics.get(k)
        if isinstance(after, dict) and isinstance(before, dict):
            sub = {ik: after[ik] - before.get(ik, 0) for ik in after
                   if after[ik] != before.get(ik, 0)}
            if sub:
                deltas[k] = sub
        elif after != before:
            deltas[k] = f"{before!r} -> {after!r}"

    record = _record_counts(conn, org, graph_id)
    db = _falkor_client()
    cache = _cache_counts(db, ns)

    print("\n── record (Postgres) ──")
    for k, v in record.items():
        print(f"  {k:14s} {v}")
    print("\n── cache (FalkorDB) ──")
    for k, v in cache.items():
        print(f"  {k:14s} {v}")
    print("\n── monitoring.metrics() deltas ──")
    print("  " + (json.dumps(deltas, sort_keys=True) if deltas else "(none)"))
    print("\n── writes returned ──")
    for k, v in results.items():
        print(f"  {k:14s} {json.dumps(v, default=str)[:100]}")

    # ── H3: which graphs actually exist on this server? ─────────────────────
    print("\n── graphs present on the cache server ──")
    for name in sorted(db.list_graphs() if hasattr(db, "list_graphs") else []):
        print(f"  {name}")

    result = {
        "org": org, "graph_id": graph_id, "cache_graph": ns,
        "record": record, "cache": cache, "metric_deltas": deltas,
    }
    out = Path(os.environ.get("SUMMARY_OUT",
                              str(Path(__file__).with_name("last-run.json"))))
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
