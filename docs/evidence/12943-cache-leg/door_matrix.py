"""#12943 (reopened) — WRITE, PER DOOR, and count the cache.

The reopening's table said the asymmetry is now **door-specific**: REST reaches
the cache, the in-process writer does not. #12950's harness measured ONE door
(``hosted_api._make_sdk`` + ``sdk._bind_record_tenant``, the expression
``_data_sdk`` itself uses). This harness measures the DOORS — one node written
through each, then the record and the cache counted for that tenant:

  ``sdk``                ``_make_sdk(graph_name=…)`` + ``_bind_record_tenant``
                         (the expression `_data_sdk` uses — #12950's door)
  ``data_sdk``           ``hosted_api._data_sdk(org)``, GRAPH-BOUND credential
  ``data_sdk_orgwide``   ``hosted_api._data_sdk(org)``, ORG-WIDE credential
                         (the branch that opens the org's DEFAULT graph)
  ``rest``               ``POST /v1/objects`` through the real FastAPI app
  ``mcp``                ``mcp_server.tortoise_create_entity`` (the tool body)

and it counts the cache **in every graph the write could plausibly have landed
in** — the tenant's canonical ``org_<org>_<gid>``, the org's default
``org_<org>``, and the graph the SDK says it opened. That last part is the
point: a "0 in ``org_<org>_<gid>``" is only evidence of a missing write if the
write was not in some OTHER graph of the same server, and a hand count of one
graph cannot tell the two apart. That is exactly how the first reading of
#12943 went wrong twice.

⛔ Control-plane stand-in, named so it is not mistaken for production: this box
has no Supabase, so ``record_tenant_for`` returns ``None`` (it must — a registry
``g_<hex>`` id cannot satisfy the record's FK) and every door would be measured
as the ``no_tenant`` REFUSAL instead of the write. The harness therefore patches
``hosted_api._resolve_record_tenant`` to the pair it seeded. That is a stand-in
for the CONTROL PLANE, not for the write path: the SDK open, the graph fold, the
journal append and the record merge all run as shipped, and the record rows are
then counted through the OWNER role (RLS bypass) so a tenant-scoped read cannot
answer 0 for the wrong reason.

Run (both stores real; the cache lane's URI comes from
``uv run python tools/test_lane.py uri``):

    TEST_RECORD_DB_URI=postgresql://tortoise_record_app:PW@127.0.0.1:33943/record \\
    OWNER_RECORD_DSN=postgresql://record:record@127.0.0.1:33943/record \\
    TORTOISE_DB_URI=docker://:@127.0.0.1:16403/tortoise_test_matrix \\
    EVENTS_BASE=/tmp/12943-doors \\
    uv run python docs/evidence/12943-cache-leg/door_matrix.py --doors sdk,data_sdk,data_sdk_orgwide,rest,mcp

⛔ Never point any of these at a shared deployment: every run mints a fresh
``org_<hex>`` / ``g_<hex>`` pair, and nothing is deleted.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

ALL_DOORS = ("sdk", "data_sdk", "data_sdk_orgwide", "rest", "mcp",
             "script_orglevel", "misroute")
LABELS = ("Object", "Subject", "Point", "Event", "Source")


# ── env ──────────────────────────────────────────────────────────────────────


def _fingerprint(dsn: str) -> str:
    import hashlib

    parsed = urlparse(dsn)
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme or '?'}://{host}{port}{parsed.path}"\
           f"#{hashlib.sha256(dsn.encode()).hexdigest()[:8]}"


def _setup_env() -> None:
    """Arm the record and the journal, refusing to override an ambient value.

    Same rule #12950's harness already learned twice: ``setdefault`` lets an
    operator's exported variable WIN, and the run then measures the ambient
    database while it prints the throwaway one.
    """
    dsn = (os.environ.get("TEST_RECORD_DB_URI")
           or os.environ.get("TORTOISE_RECORD_DSN") or "").strip()
    if not dsn:
        raise SystemExit("no TEST_RECORD_DB_URI in env")
    ambient = (os.environ.get("TORTOISE_RECORD_DSN") or "").strip()
    if ambient and ambient != dsn:
        raise SystemExit(
            "TORTOISE_RECORD_DSN is set to a DIFFERENT value than this run "
            f"writes to ({_fingerprint(ambient)} vs {_fingerprint(dsn)})")
    os.environ["TORTOISE_RECORD_DSN"] = dsn
    base = Path(os.environ.get("EVENTS_BASE", "/tmp/12943-doors"))
    base.mkdir(parents=True, exist_ok=True)
    ambient_base = (os.environ.get("TORTOISE_EVENT_LOG_BASE_DIR") or "").strip()
    if ambient_base and Path(ambient_base).resolve() != base.resolve():
        raise SystemExit("TORTOISE_EVENT_LOG_BASE_DIR disagrees with EVENTS_BASE")
    os.environ["TORTOISE_EVENT_LOG_BASE_DIR"] = str(base)
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        raise SystemExit(
            "TORTOISE_DB_URI is empty — this harness measures the DOORS on a "
            "shared cache; export the lane's URI")


def _owner_dsn() -> str:
    return os.environ.get("OWNER_RECORD_DSN") or os.environ["TEST_RECORD_DB_URI"]


def _psycopg2():
    import psycopg2

    return psycopg2


def _falkor():
    from falkordb import FalkorDB

    parsed = urlparse((os.environ.get("TORTOISE_DB_URI") or "").strip())
    if not parsed.hostname:
        raise SystemExit("TORTOISE_DB_URI names no host")
    return FalkorDB(host=parsed.hostname, port=parsed.port or 6379,
                    password=unquote(parsed.password) if parsed.password else None)


# ── provisioning ─────────────────────────────────────────────────────────────


def _seed_record(org: str, graph_id: str, *, default: bool) -> None:
    conn = _psycopg2().connect(_owner_dsn())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.organizations (id, name, graph_name) "
                "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (org, org, f"org_{org}"))
            cur.execute(
                "INSERT INTO public.graphs (id, org_id, name) "
                "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (graph_id, org, "default" if default else graph_id))
    finally:
        conn.close()


def _seed_registry(org: str, graph_id: str, namespace: str) -> None:
    """The ``:Graph`` node ``_assert_graph_owned`` reads on the registry lane."""
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


# ── reading both sides ───────────────────────────────────────────────────────


def _record_counts(org: str, graph_id: str) -> dict[str, int]:
    conn = _psycopg2().connect(_owner_dsn())
    conn.autocommit = True
    out: dict[str, int] = {}
    try:
        with conn.cursor() as cur:
            for table in ("object", "subject", "point", "event", "relation",
                          "journal_event"):
                cur.execute(
                    f"SELECT count(*) FROM public.{table} "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph_id))
                out[table] = cur.fetchone()[0]
    finally:
        conn.close()
    return out


def _cache_counts(graph_name: str) -> dict[str, int]:
    db = _falkor()
    try:
        if graph_name not in set(db.list_graphs() or []):
            return {"__absent__": 1}
        g = db.select_graph(graph_name)
        out: dict[str, int] = {}
        for label in LABELS:
            res = g.query(f"MATCH (n:{label}) RETURN count(n) AS c")
            out[label] = int(res.result_set[0][0]) if res.result_set else 0
        return out
    finally:
        with contextlib.suppress(Exception):
            db.connection.close()


def _total(counts: dict[str, int]) -> int:
    """Nodes in a cache graph. `__absent__` is "the graph does not exist", i.e. 0
    nodes — counting it as 1 made every absent graph report as "reached"."""
    if counts.get("__absent__"):
        return 0
    return sum(counts.values())


def _reconcile(org: str, graph_id: str, canonical: str,
               actual: str | None) -> dict:
    """The product's OWN instrument, on both candidate cache graphs.

    Run once against the tenant's canonical name (what an operator derives from
    the record) and once against the graph the write actually landed in. Both
    verdicts are needed: the first is the confusion this issue is about, the
    second is the convergence proof. `--cache-graph` has no derived default by
    design (`tortoise/__main__.py`), so naming both is the operator's job — and
    the point is that the SECOND one is the name nothing told them.
    """
    import subprocess

    def _run(cache_graph: str) -> dict:
        proc = subprocess.run(
            [sys.executable, "-m", "tortoise", "record-reconcile",
             "--db", os.environ["TORTOISE_DB_URI"],
             "--record-dsn", os.environ["TORTOISE_RECORD_DSN"],
             "--org", org, "--graph", graph_id,
             "--cache-graph", cache_graph, "--json"],
            capture_output=True, text=True, env=dict(os.environ), check=False)
        try:
            payload = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            payload = {"unparseable_stdout": (proc.stdout or "")[-400:]}
        payload["exit"] = proc.returncode
        payload["stderr_tail"] = (proc.stderr or "")[-300:]
        return payload

    return {"canonical_graph": canonical,
            "actual_graph": actual,
            "canonical": _run(canonical),
            "actual": _run(actual) if actual else None}


def _verdict(payload: dict | None) -> dict:
    if not payload:
        return {"verdict": "not-run"}
    return {k: payload.get(k) for k in
            ("verdict", "checked", "divergent", "missing_in_cache",
             "extra_in_cache", "field_mismatches", "exit")
            if k in payload}


# ── the doors ────────────────────────────────────────────────────────────────


def _org_dict(org: str, graph_id: str | None, namespace: str | None) -> dict:
    return {"org_id": org, "graph_id": graph_id,
            "graph_namespace": namespace, "key_id": None,
            "legacy_full_access": True, "scopes": [],
            "tier": "free", "delegation_depth": None,
            "created_by_key_id": None,
            # The quota stub resolves these; a stub missing them makes the gate
            # 500 instead of passing (#310) — the pattern `tests/test_hosted_api`
            # uses for `TEST_TEAM`.
            "max_users": 1, "max_graphs": 1, "max_points": 10000,
            "max_api_keys": 2, "max_sessions": None}


def _write_sdk(org: str, graph_id: str, ns: str, name: str) -> dict:
    from tortoise import hosted_api

    sdk = hosted_api._make_sdk(graph_name=ns)
    try:
        # What graph will this SDK actually fold into? Ask the object, not the
        # argument — `_get_proj().graph_name` is the projection's own answer.
        target = sdk._get_proj().graph_name
        sdk._bind_record_tenant(org, graph_id)
        sdk.create_object(name, objectKind="goal")
    finally:
        sdk.close()
    return {"cache_target": target}


def _patched_tenant(org: str, graph_id: str):
    """Stand in for the control plane (see the module docstring).

    ``_resolve_record_tenant`` is the ONE seam ``_data_sdk`` binds through, so
    patching it changes only WHICH TENANT is filed — every SDK open, graph fold,
    journal append and record merge below is shipped code.
    """
    from tortoise import hosted_api

    original = hosted_api._resolve_record_tenant
    hosted_api._resolve_record_tenant = lambda _org: (org, graph_id)
    return original


def _write_data_sdk(org: str, graph_id: str, ns: str, name: str) -> dict:
    from tortoise import hosted_api

    original = _patched_tenant(org, graph_id)
    try:
        sdk = hosted_api._data_sdk(_org_dict(org, graph_id, ns))
        try:
            target = sdk._get_proj().graph_name
            sdk.create_object(name, objectKind="goal")
        finally:
            sdk.close()
    finally:
        hosted_api._resolve_record_tenant = original
    return {"cache_target": target}


def _write_data_sdk_orgwide(org: str, graph_id: str, ns: str, name: str) -> dict:
    """The ORG-WIDE branch: no ``graph_id`` on the credential.

    This is the branch that opens the org's DEFAULT graph, and the one whose
    record tenant is the default graph's id (#10025) — two values that come
    from two different places. ``_resolve_record_tenant`` is replaced with the
    answer the control plane would give (this box has none).
    """
    from tortoise import hosted_api

    original = _patched_tenant(org, graph_id)
    try:
        sdk = hosted_api._data_sdk(_org_dict(org, None, None))
        try:
            target = sdk._get_proj().graph_name
            sdk.create_object(name, objectKind="goal")
        finally:
            sdk.close()
    finally:
        hosted_api._resolve_record_tenant = original
    return {"cache_target": target}


def _write_rest(org: str, graph_id: str, ns: str, name: str) -> dict:
    """``POST /v1/objects`` through the real app.

    The auth dependency is overridden (no control plane here) and
    ``_resolve_record_tenant`` answers the seeded pair; everything else — the
    route body, ``_data_sdk_offloaded``, the quota gate, the SDK write — is
    shipped code.
    """
    os.environ.setdefault("TORTOISE_SECRET_PEPPER", "door-matrix-pepper")
    os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
    from fastapi.testclient import TestClient

    from tortoise import hosted_api

    original = _patched_tenant(org, graph_id)
    try:
        from tortoise.hosted_api import app, get_current_org

        app.dependency_overrides[get_current_org] = lambda: _org_dict(
            org, graph_id, ns)
        try:
            with TestClient(app) as client:
                resp = client.post("/v1/objects",
                                   json={"name": name,
                                         "objectKind": "goal"})
        finally:
            app.dependency_overrides.clear()
    finally:
        hosted_api._resolve_record_tenant = original
    return {"status": resp.status_code, "body": resp.text[:200]}


def _write_mcp(org: str, graph_id: str, ns: str, name: str) -> dict:
    from tortoise import mcp_auth, mcp_server
    from tortoise.record_writer import use_record_tenant

    previous = (mcp_server._sdk, getattr(mcp_server, "sdk", None))
    mcp_server._sdk = None
    mcp_server.sdk = None
    tokens = [mcp_auth._transport_mode.set("stdio"),
              mcp_auth._current_org_id.set(org),
              mcp_auth._current_graph_id.set(graph_id),
              mcp_auth._current_graph_namespace.set(ns)]
    try:
        with use_record_tenant(org, graph_id):
            out = mcp_server.tortoise_create_entity(
                type="object", name=name, props={"objectKind": "goal"})
    finally:
        for var, token in reversed(list(zip(
                (mcp_auth._transport_mode, mcp_auth._current_org_id,
                 mcp_auth._current_graph_id,
                 mcp_auth._current_graph_namespace), tokens, strict=True))):
            var.reset(token)
        mcp_server._sdk, mcp_server.sdk = previous
    return {"tool_returned": str(out)[:160]}


def _write_script_orglevel(org: str, graph_id: str, ns: str, name: str) -> dict:
    """The SHAPE a maintenance script writes in: an org-LEVEL SDK + a tenant.

    ``_make_sdk(namespace=org_id)`` opens ``org_<org>`` — the org's DEFAULT graph
    namespace — and then the caller binds a tenant whose ``graph_id`` is a CUSTOM
    graph. That is the second documented way into the split (#12943's reopening
    looked like exactly this from the record's side): the record files under the
    custom graph's id, the cache node lands in the org-level graph, both calls
    succeed, and `record-reconcile --cache-graph org_<org>_<gid>` reports the
    whole tenant missing because the graph it is told to read holds nothing.
    """
    from tortoise import hosted_api

    sdk = hosted_api._make_sdk(namespace=org)
    try:
        target = sdk._get_proj().graph_name
        sdk._bind_record_tenant(org, graph_id)
        sdk.create_object(name, objectKind="goal")
    finally:
        sdk.close()
    return {"cache_target": target}


def _write_misroute(org: str, graph_id: str, ns: str, name: str) -> dict:
    """A cache graph for graph A + a record tenant for graph B — COUNTED.

    The one shape of the split a process can decide without reading either store:
    the SDK's own graph name carries ``g_A`` and the tenant says ``g_B``.
    """
    from tortoise import hosted_api

    other = f"g_12943other{uuid.uuid4().hex[:8]}"
    sdk = hosted_api._make_sdk(graph_name=f"org_{org}_{other}")
    try:
        target = sdk._get_proj().graph_name
        sdk._bind_record_tenant(org, graph_id)
        sdk.create_object(name, objectKind="goal")
    finally:
        sdk.close()
    return {"cache_target": target, "tenant_graph_id": graph_id}


DOORS = {
    "sdk": _write_sdk,
    "script_orglevel": _write_script_orglevel,
    "misroute": _write_misroute,
    "data_sdk": _write_data_sdk,
    "data_sdk_orgwide": _write_data_sdk_orgwide,
    "rest": _write_rest,
    "mcp": _write_mcp,
}


def main(argv: list[str]) -> int:
    doors = ALL_DOORS
    for i, arg in enumerate(argv):
        if arg == "--doors" and i + 1 < len(argv):
            doors = tuple(d.strip() for d in argv[i + 1].split(",") if d.strip())
    _setup_env()
    from tortoise import monitoring

    run = uuid.uuid4().hex[:10]
    rows: list[dict] = []
    for door in doors:
        # One tenant PER DOOR, and the slug must be unique per door: a shared
        # slug made two doors count each other's objects, which is exactly the
        # "a graph name is not a row identity" trap this harness exists for.
        slug = {"sdk": "a", "data_sdk": "b", "data_sdk_orgwide": "c",
                "rest": "d", "mcp": "e", "script_orglevel": "f",
                "misroute": "h"}[door]
        org = f"{slug}12943{run}"[:40]
        graph_id = f"g_12943{slug}{run}"[:40]
        ns = f"org_{org}_{graph_id}"
        # The tenant is a DEFAULT graph only for the org-wide door (that is what
        # an org-wide credential names); every other door is graph-bound to a
        # custom graph, which is the shape REST's 2 objects arrived in.
        _seed_record(org, graph_id, default=(door == "data_sdk_orgwide"))
        _seed_registry(org, graph_id, ns)
        from tortoise.cache_leg import cache_write_targets as _targets

        targets_before = _targets()
        failures_before = monitoring.cache_leg_failure_counts()
        before_metrics = dict(monitoring.metrics())
        outcome: dict = {}
        error = None
        try:
            outcome = DOORS[door](org, graph_id, ns,
                                  f"door-{door}-{run}")
        except Exception:
            error = traceback.format_exc()[-1200:]
        after_metrics = dict(monitoring.metrics())
        targets_after = _targets()
        target_delta = {k: targets_after[k] - targets_before.get(k, 0)
                        for k in targets_after
                        if targets_after[k] != targets_before.get(k, 0)}
        record = _record_counts(org, graph_id)
        canonical = _cache_counts(ns)
        org_default = _cache_counts(f"org_{org}")
        row = {
            "door": door, "org": org, "graph_id": graph_id,
            "cache_graph_canonical": ns,
            "record": record,
            "cache_canonical": canonical,
            "cache_org_default": org_default,
            "outcome": outcome,
            "error": error,
            "cache_leg_failures": monitoring.cache_leg_failure_counts(),
            "cache_leg_failures_delta": {
                k: v - failures_before.get(k, 0)
                for k, v in monitoring.cache_leg_failure_counts().items()
                if v != failures_before.get(k, 0)},
            "cache_write_target_delta": target_delta,
            "metrics_keys_moved": sorted(
                k for k in after_metrics
                if after_metrics.get(k) != before_metrics.get(k)),
        }
        rows.append(row)
        reached_canonical = _total(canonical) > 0
        reached_default = _total(org_default) > 0
        row["reached_canonical"] = reached_canonical
        row["reached_org_default"] = reached_default
        print(f"\n=== {door} ===")
        print(f"  tenant            org={org} graph={graph_id}")
        print(f"  record            {json.dumps(record)}")
        print(f"  cache {ns:<32} {json.dumps(canonical)}")
        print(f"  cache org_{org:<26} {json.dumps(org_default)}")
        print(f"  cache target      {outcome.get('cache_target')!r}")
        print(f"  write TARGETS     {json.dumps(target_delta)}")
        print(f"  cache_leg_failures {json.dumps(row['cache_leg_failures_delta'])}")
        print(f"  reached: canonical={reached_canonical} "
              f"org_default={reached_default}"
              f"  writes_in_record={sum(record.values())}")
        if error:
            print(f"  WRITE RAISED      {error.splitlines()[-1]}")
        if "--reconcile" in argv:
            # The ACTUAL graph is read from the target registry, not from the
            # door's own report: the registry is what the fold itself recorded,
            # so it is the door-independent answer (REST holds no SDK handle at
            # all, and the MCP door's pre-call handle is a different SDK).
            actual = None
            if len(target_delta) == 1:
                actual = next(iter(target_delta)).split("|", 1)[1]
            row["reconcile"] = _reconcile(org, graph_id, ns, actual)
            print("  reconcile (tenant's canonical graph): "
                  f"{json.dumps(_verdict(row['reconcile']['canonical']))}")
            print("  reconcile (the graph the write used): "
                  f"{json.dumps(_verdict(row['reconcile']['actual']))}")
    out = Path(os.environ.get(
        "SUMMARY_OUT",
        str(Path(__file__).with_name("last-run-door-matrix.json"))))
    out.write_text(json.dumps({
        "cache_uri": _fingerprint(os.environ["TORTOISE_DB_URI"]),
        "record_dsn": _fingerprint(os.environ["TORTOISE_RECORD_DSN"]),
        "rows": rows,
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    import os as _os

    raise SystemExit(main(_os.sys.argv))
