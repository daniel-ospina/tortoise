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

Run (the canonical, copy-pasteable form is in ``README.md`` — it names all four
targets and the two arm shapes; this is the short version):

    TEST_RECORD_DB_URI=postgresql://tortoise_record_app:PW@127.0.0.1:33943/record \\
    OWNER_RECORD_DSN=postgresql://record:record@127.0.0.1:33943/record \\
    CACHE_COUNT_URI=docker://:@127.0.0.1:16419/tortoise_test_matrix \\
    TORTOISE_DB_URI=docker://:@127.0.0.1:16419/tortoise_test_matrix \\
    uv run python docs/evidence/12943-cache-leg/measure.py

⚠️ Do NOT set ``TORTOISE_RECORD_DSN`` or ``TORTOISE_EVENT_LOG_BASE_DIR`` in the
ambient environment: this harness ASSIGNS both (from ``TEST_RECORD_DSN``/
``TEST_RECORD_DB_URI`` and ``EVENTS_BASE``) and REFUSES to run if an ambient
value disagrees, because a silent override here measures the wrong database.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))


def _record_dsn() -> str:
    """The APPLICATION role's DSN — what the live writer connects as.

    ``SUMMARY_OUT`` selects where the JSON capture is written (default
    ``last-run.json`` beside this script; the committed captures are
    ``last-run-arm-a-shared-cache.json`` and ``last-run-arm-b-local-cache.json``).
    ``EVENTS_BASE`` selects the journal base dir (default ``.evidence-events/``);
    each run APPENDS a run-unique ``org_<hex>/events.jsonl`` there, which is
    scratch — it is gitignored, and it is not part of the evidence.

    ``record_projection`` REFUSES a role that BYPASSES RLS, so this must be
    ``tortoise_record_app`` (see ``provision_record.py``).
    """
    dsn = (os.environ.get("TEST_RECORD_DB_URI")
           or os.environ.get("TORTOISE_RECORD_DSN"))
    if not dsn:
        raise SystemExit("no TEST_RECORD_DB_URI / TORTOISE_RECORD_DSN in env")
    return dsn


def _fingerprint(dsn: str) -> str:
    """`scheme://host:port/db#8hex` — enough to tell two targets apart in a
    capture, and never the credential (the same rule `/health`'s `dsn` field
    follows)."""
    h = hashlib.sha256(dsn.encode()).hexdigest()[:8]
    parsed = urlparse(dsn)
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme or '?'}://{host}{port}{parsed.path}#{h}"


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
    import psycopg2
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
        except Exception as exc:
            out[label] = f"ERR {type(exc).__name__}: {exc}"
    return out


def _falkor_client():
    """A client for the cache server.

    ``urlparse``, not a hand split: ``str.rsplit("@", 1)`` yields ONE element
    when the URI carries no userinfo, so a credential-less
    ``redis://host:6379/0`` (or ``rediss://``, or a bare path) raised
    ``ValueError`` here instead of being reported as the unusable URI it is.
    """

    from falkordb import FalkorDB
    # ⛔ THE COUNTING CLIENT TAKES ITS URI FROM ITS OWN VARIABLE. To reproduce the
    # DEFECT shape the write path must run with `TORTOISE_DB_URI` EMPTY
    # (`_make_sdk` branches on its truthiness) — but the cache then has to be
    # counted on a server that URI would have named, or the "0" is an artefact of
    # looking nowhere. `CACHE_COUNT_URI` (default `TORTOISE_DB_URI`) separates
    # the two questions: where the SDK writes, and where we look. Without it this
    # harness could not produce the second row of its own table (review cycle 2).
    uri = (os.environ.get("CACHE_COUNT_URI")
           or os.environ.get("TORTOISE_DB_URI") or "").strip()
    parsed = urlparse(uri)
    if not parsed.hostname:
        raise SystemExit(
            "neither CACHE_COUNT_URI nor TORTOISE_DB_URI names a host — export "
            "one, e.g. `uv run python tools/test_lane.py uri`")
    if parsed.scheme.endswith("s") or "ssl" in parsed.scheme:
        # `rediss://` / `redis+ssl://` are TLS endpoints; `FalkorDB` here has no
        # ssl flag, so dialling them would silently fall back to cleartext and
        # surface as an auth/timeout error that blames the wrong thing.
        raise SystemExit(
            f"{parsed.scheme}:// is a TLS scheme this harness cannot dial "
            "(it passes no ssl=True); point it at the lane's plaintext "
            "docker:// URI instead")
    return FalkorDB(host=parsed.hostname, port=parsed.port or 6379,
                    password=unquote(parsed.password) if parsed.password else None)


def main() -> int:
    run = uuid.uuid4().hex[:10]
    org = f"org12943{run}"
    graph_id = f"g_12943{run}"
    ns = f"org_{org}_{graph_id}"

    dsn = _record_dsn()
    # ⛔ ASSIGN, NEVER `setdefault`. This whole harness exists because two
    # variables pick two stores and nothing compares them — and `setdefault`
    # reproduced that defect one level down: an ambient `TORTOISE_RECORD_DSN`
    # (the variable an operator of this deployment is most likely to have
    # exported) would WIN, so the SDK would write to the AMBIENT record while the
    # counts read the THROWAWAY one, and the capture would look normal. Refusing
    # on a disagreement is the only safe shape: a silent override here is a
    # measurement of the wrong database (review cycle 4).
    ambient = (os.environ.get("TORTOISE_RECORD_DSN") or "").strip()
    if ambient and ambient != dsn:
        raise SystemExit(
            "TORTOISE_RECORD_DSN is set to a DIFFERENT value than the one this "
            "run would write to.\n  ambient (would win): "
            f"{_fingerprint(ambient)}\n  this run's target : {_fingerprint(dsn)}\n"
            "Unset TORTOISE_RECORD_DSN (the harness assigns it from "
            "TEST_RECORD_DB_URI) or point both at the same throwaway database.")
    os.environ["TORTOISE_RECORD_DSN"] = dsn
    # The record's only durable source is the JSONL journal (`write_live_record`
    # refuses `no_journal` without it), and production arms it
    # (`TORTOISE_EVENT_LOG_BASE_DIR=/data/events`). One dir per run — and, for
    # the same reason as the DSN above, ASSIGNED rather than defaulted, so an
    # ambient production `/data/events` cannot receive this harness's journals.
    base = Path(os.environ.get("EVENTS_BASE", f"/tmp/12943-events-{run}"))
    base.mkdir(parents=True, exist_ok=True)
    ambient_base = (os.environ.get("TORTOISE_EVENT_LOG_BASE_DIR") or "").strip()
    if ambient_base and Path(ambient_base).resolve() != base.resolve():
        raise SystemExit(
            "TORTOISE_EVENT_LOG_BASE_DIR is set to a DIFFERENT directory than "
            f"this run would append to.\n  ambient (would win): {ambient_base}\n"
            f"  this run's target : {base}\n"
            "Unset it, or pass EVENTS_BASE with the same value.")
    os.environ["TORTOISE_EVENT_LOG_BASE_DIR"] = str(base)
    psycopg2 = _psycopg2()
    # OWNER connection: seeding + counting only (see `_owner_dsn`).
    conn = psycopg2.connect(_owner_dsn())
    conn.autocommit = True
    _seed_tenant(conn, org, graph_id)

    from tortoise import hosted_api, monitoring

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
    print(f"TORTOISE_DB_URI  {os.environ.get('TORTOISE_DB_URI', '<unset>')!r}"
          f"  (write path)")
    print(f"CACHE_COUNT_URI  "
          f"{os.environ.get('CACHE_COUNT_URI', '<defaults to TORTOISE_DB_URI>')}")
    print(f"record DSN       {_fingerprint(os.environ['TORTOISE_RECORD_DSN'])}"
          f"  (write path)")
    print(f"events base      {base}")
    print(f"cache count URI  {os.environ.get('CACHE_COUNT_URI')
                             or os.environ.get('TORTOISE_DB_URI') or '<none>'}")

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
        # `--skip-operator` exists for the RECONCILE arm: the operator Point's
        # record row carries an EMPTY ``point_kind`` and a structured
        # ``props.operator`` echo (``{inputs, op_type}``) which the graph
        # represents NATIVELY as ``op_type`` + typed edges — so a reconciler that
        # compares the record's echo to the graph's properties reports it
        # divergent every time, on a store that lost nothing. That comparison is
        # its own root, filed as #12949; this harness can therefore measure the
        # reconcile on a graph written through the ordinary doors, and name the
        # operator residual rather than hide it.
        if "--skip-operator" in sys.argv:
            print("operator         SKIPPED (--skip-operator: see #12949)")
        else:
            results["operator"] = sdk.create_operator(
                "IMPL", o["id"], [p["id"]], "supports")
    except Exception as exc:
        print(f"WRITE RAISED: {type(exc).__name__}: {exc}")

    sdk.close()
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
    print("\n── the cache leg's own counter ──")
    print("  " + json.dumps(monitoring.cache_leg_failure_counts(), sort_keys=True))
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
        # Which shape this run measured — the README's table keys on it.
        "write_path_uri": os.environ.get("TORTOISE_DB_URI") or None,
        # The VALUE, not only the variable name (review cycle 3): arm A's whole
        # claim is that the cache count came from THE SERVER THE SDK WROTE TO,
        # and a source-name alone cannot show that. A `CACHE_COUNT_URI` naming a
        # different server makes arm A read 0/0 while looking identical to arm B.
        "cache_count_uri": (os.environ.get("CACHE_COUNT_URI")
                            or os.environ.get("TORTOISE_DB_URI") or None),
        "cache_count_uri_source": ("CACHE_COUNT_URI"
                                   if os.environ.get("CACHE_COUNT_URI")
                                   else "TORTOISE_DB_URI"),
        # Same server or arm A proves nothing — computed, so it cannot be
        # transcribed wrongly.
        "cache_count_uri_matches_write_target":
            (os.environ.get("CACHE_COUNT_URI")
             or os.environ.get("TORTOISE_DB_URI") or None)
            == (os.environ.get("TORTOISE_DB_URI") or None),
        # WHICH record and WHICH journal dir, fingerprinted: a capture that
        # records only "a DSN was set" cannot show that it measured the
        # throwaway rather than the ambient database (review cycle 4).
        "record_dsn_fingerprint": _fingerprint(
            os.environ["TORTOISE_RECORD_DSN"]),
        "events_base": str(base),
        # The counter columns, so `both-arms.json` can carry EVERY column of the
        # README table rather than a subset of it.
        "record_writes_applied_delta": deltas.get("record_writes_applied"),
        "record_write_failures_published": after_metrics.get(
            "record_write_failures"),
        "record": record, "cache": cache,
        "cache_leg_failures": monitoring.cache_leg_failure_counts(),
        "metric_deltas": deltas,
    }
    out = Path(os.environ.get("SUMMARY_OUT",
                              str(Path(__file__).with_name("last-run.json"))))
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
