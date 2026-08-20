"""#318 — per-tenant pack activation state (``PackInstall`` install records).

Multi-tenant pack isolation: a shared pack catalog (``pack_registry``) plus
per-tenant activation records stored graph-natively in each tenant's graph
(``graph_name=team_{team_id}``, the LANDED isolation boundary). The tenant
graph itself is the isolation boundary, so cross-tenant access is
structurally impossible — a query with tenant B's identity reads tenant B's
graph (auth-only scoping; no tenant selector exists on any surface).

Design decisions (locked 2026-08-15, issue #318 — decision comment
5287515660, plan docs/plans/2026-08-15-318-pack-isolation-plan.md):

- D1  Fixed default starter set for ALL tenants; selection UI later.
- D2  Activation records, not file copies — the shared catalog is read-only
      for tenants (industry pattern: shared catalog + per-tenant install-state).
- D3  Enterprise governance (kind lifecycle, schema versioning) DEFERRED —
      ``tier: enterprise`` is a manifest validation error today, so
      governance hooks would be phantom work. Re-open when an enterprise
      tier exists (pricing/GTM decision).
- D4  Read-only introspection surface (REST ``GET /v1/packs`` + MCP
      ``packs_list``) — this module is the shared ensure-then-read core.
- D5  Existing-tenant backfill handles ``team_{name}`` vs ``team_{id}``
      naming via the RECORDED graph_name (graph-scripts/backfill_pack_installs.py).
- D6  Existence masking — empty result when nothing to see; errors only for
      auth failures.

Semantics:

- **Idempotent additive MERGE** per namespace — re-running activation
  (provision retry, self-heal, backfill) is a no-op and never duplicates.
  MERGE is atomic per statement on server-side engines; the embedded
  FalkorDBLite engine serializes activation in-process per (graph,
  namespace) instead (#1307) — concurrent ensures converge to exactly ONE
  ``PackInstall`` per namespace either way.
- **Additive-only removal** — removing a pack from ``TORTOISE_STARTER_PACKS``
  does NOT uninstall existing installs (non-destructive; Backlex/decree
  reseed-no-op precedent). Explicit uninstall/deactivation belongs to the
  deferred governance slice.
- **Best-effort** — activation failure never blocks provisioning (Backlex
  precedent) and never raises into the provisioning path; the introspection
  surface self-heals on first read.
- **Env validation** — starter names are validated against the catalog at
  call time (not cached at import); unknown names are skipped with a logged
  warning, never fail provisioning. Unset/empty ``TORTOISE_STARTER_PACKS``
  → built-in default set.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

from tortoise.sdk import TortoiseSDK

log = logging.getLogger(__name__)

PACK_INSTALL_LABEL = "PackInstall"

# D1: fixed default starter set for ALL tenants. Namespaces are the pack
# catalog's canonical ids (note: the project-management pack dir declares
# namespace `pm`, not `project-management`). Unknown names (env typo or a
# renamed pack) are skipped with a logged warning — never a failure.
DEFAULT_STARTER_PACKS: tuple[str, ...] = ("dev", "marketing", "product-strategy", "pm")

# Test-only escape hatch (#318 coherence P1): disable the read-path
# self-heal so tests can exercise the PURE eager activation path
# (direct graph assertion post-provision, pre-GET).
_SELF_HEAL_DISABLE_ENV = "PACK_STATE_DISABLE_SELF_HEAL"


@dataclass(frozen=True)
class PackInstallRecord:
    """Activation record model (#318 task 1): one per (tenant, namespace).

    Persisted as a ``(:PackInstall {namespace, version, status, source,
    installed_at})`` node in the tenant graph. ``source`` is the provenance
    mark ('starter' for the default set; future slices: 'custom'/'manual').
    """

    namespace: str
    version: str
    status: str
    source: str
    installed_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "namespace": self.namespace,
            "version": self.version,
            "status": self.status,
            "source": self.source,
            "installed_at": self.installed_at,
        }


def _self_heal_disabled() -> bool:
    return os.environ.get(_SELF_HEAL_DISABLE_ENV) == "1"


def _resolve_catalog() -> dict[str, dict]:
    """Shared-catalog read (read-at-call-time contract, never cached).

    Returns {} (degrade gracefully) when the pack registry is unavailable —
    activation then skips every namespace with a warning and the
    introspection surface returns empty (D6 masking), never an error.
    """
    try:
        from tortoise.domain_loader import _get_registry
        reg = _get_registry()
    except Exception:
        reg = None
    if reg is None:
        return {}
    try:
        return reg.pack_summaries()
    except Exception:
        return {}


def _starter_namespaces(starter: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Resolve the starter set at call time; dedup, preserve order."""
    if starter is not None:
        names = list(starter)
    else:
        raw = os.environ.get("TORTOISE_STARTER_PACKS", "")
        if not raw.strip():
            names = list(DEFAULT_STARTER_PACKS)
        else:
            names = [token.strip() for token in raw.split(",") if token.strip()]
    out: list[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return out


def _target_graph(sdk: TortoiseSDK, graph_name: str | None):
    """The graph to read/write pack state in.

    ``graph_name=None`` → the SDK's namespace-scoped graph (the LANDED
    isolation boundary ``team_{team_id}`` for team SDKs). An explicit
    ``graph_name`` targets a specific graph — used by the D5 backfill to
    handle legacy ``team_{name}``-named tenant graphs (recorded graph_name).
    """
    proj = sdk._get_proj()
    if graph_name is None:
        return proj.g
    return proj.db.select_graph(graph_name)


def _resolved_graph_name(sdk: TortoiseSDK, graph_name: str | None) -> str:
    """Resolve the PHYSICAL graph identity a call reads/writes.

    ``graph_name=None`` → the SDK's namespace-scoped graph; the projection
    exposes the LANDED name (``team_{team_id}`` for team SDKs — the #7886
    isolation boundary). An explicit ``graph_name`` (D5 backfill read
    target, ``team_{team_id}``) is already the physical name.

    Locking keys on this RESOLVED name (conf 75, PR #1312): a hosted
    provision (``graph_name=None``) and the backfill (explicit
    ``team_{team_id}``) write the SAME physical graph, so they must
    serialize on the SAME lock. Keying on the passed string instead would
    split one graph across two locks (race survives in the mixed path) and
    collapse every None caller onto one shared ``default`` lock
    (over-serializing distinct tenants).
    """
    proj = sdk._get_proj()
    if graph_name is None:
        return proj.graph_name
    return graph_name


# #1307: the embedded FalkorDBLite engine does not guarantee server-side
# atomicity of MERGE under thread contention (observed: 8-thread races
# producing duplicate PackInstall nodes). Serialize activation per
# (graph, namespace) in-process — embedded mode is single-writer, so the
# lock is sufficient there; server-side (FalkorDB server/docker) engines
# keep their atomic MERGE. The lock key is the RESOLVED graph name (conf
# 75, PR #1312), so mixed paths (hosted provision ``graph_name=None`` vs
# the backfill's explicit ``team_{team_id}``) hitting the same physical
# graph share one lock, while distinct tenants never serialize against
# each other.
#
# Bounded in practice: one entry per (graph, namespace) — a fixed starter
# set (D1) × active tenants. # TODO (conf 60, PR #1312): evict idle
# entries if tenant churn grows (e.g. drop locks for graphs with no recent
# writers) — under sustained churn this dict would otherwise grow without
# bound.
_PACK_INSTALL_LOCKS: dict[str, Lock] = {}
_PACK_INSTALL_LOCKS_GUARD = Lock()


def _pack_install_lock(graph_name: str, ns: str) -> Lock:
    key = f"{graph_name}\x1f{ns}"
    with _PACK_INSTALL_LOCKS_GUARD:
        lock = _PACK_INSTALL_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _PACK_INSTALL_LOCKS[key] = lock
        return lock


def ensure_tenant_packs(sdk: TortoiseSDK, *, starter: list[str] | tuple[str, ...] | None = None,
                        graph_name: str | None = None) -> list[dict]:
    """Idempotent activation of the starter set into the tenant graph.

    One ``MERGE (:PackInstall {namespace})`` per starter namespace —
    atomic per statement on server-side engines; the embedded engine's
    activation is serialized in-process per (graph, namespace) (#1307), so
    concurrent ensures converge to exactly ONE node per namespace
    regardless of interleaving. Best-effort: a namespace that
    fails (or is unknown in the catalog) is skipped with a logged warning
    and never raises into the provisioning path. Returns the activation
    records written (or already present) this call.
    """
    catalog = _resolve_catalog()
    g = _target_graph(sdk, graph_name)
    lock_graph = _resolved_graph_name(sdk, graph_name)
    now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    activated: list[dict] = []
    for ns in _starter_namespaces(starter):
        meta = catalog.get(ns)
        if meta is None:
            log.warning(
                "pack_state: starter pack %r unknown in shared catalog — skipped "
                "(check TORTOISE_STARTER_PACKS)", ns)
            continue
        try:
            with _pack_install_lock(lock_graph, ns):
                g.query(
                    "MERGE (p:PackInstall {namespace: $ns}) "
                "SET p.version = $version, p.status = 'active', "
                "    p.source = 'starter', "
                "    p.installed_at = coalesce(p.installed_at, $now)",
                params={"ns": ns, "version": meta["version"], "now": now},
            )
            activated.append(PackInstallRecord(
                namespace=ns, version=meta["version"], status="active",
                source="starter", installed_at=now,
            ).to_dict())
        except Exception as e:  # noqa: BLE001, RUF100
            log.warning(
                "pack_state: activation of %r failed — skipped (best-effort, "
                "self-heals on next introspection): %s", ns, e)
    return activated


def get_tenant_packs(sdk: TortoiseSDK, *, graph_name: str | None = None) -> list[dict]:
    """Ensure-then-read introspection: the tenant's active packs.

    Reads ``PackInstall`` install-state from the tenant graph and joins the
    shared catalog metadata (name/tier/description). Self-heal (convergence
    safety net): when the tenant graph has NO installs on first read,
    re-ensure the starter set then read again — pre-existing tenants converge
    automatically. Disable under ``PACK_STATE_DISABLE_SELF_HEAL=1`` (test-only).

    D6 masking: returns [] when there is genuinely nothing to see (starter
    set empty/unset, catalog unavailable, or ensure failed) — never an error.
    Graph-unreachable RAISES — callers map that to 503 (never empty-on-outage).
    """
    g = _target_graph(sdk, graph_name)
    rows = _read_installs(g)
    if not rows and not _self_heal_disabled():
        ensure_tenant_packs(sdk, graph_name=graph_name)
        rows = _read_installs(g)
    catalog = _resolve_catalog()
    out: list[dict] = []
    for ns, version, status, source, installed_at in rows:
        meta = catalog.get(ns, {})
        out.append({
            "namespace": ns,
            "name": meta.get("name", ns),
            "version": version,
            "tier": meta.get("tier", "free"),
            "description": meta.get("description", ""),
            "status": status or "active",
            "source": source or "starter",
            "installed_at": installed_at,
        })
    return sorted(out, key=lambda p: p["namespace"])


def _read_installs(g) -> list[tuple[str, str, str, str, str | None]]:
    """Raw install-state read: (namespace, version, status, source, installed_at)."""
    rows = g.query(
        "MATCH (p:PackInstall) "
        "RETURN p.namespace, p.version, p.status, p.source, p.installed_at "
        "ORDER BY p.namespace",
    ).result_set
    return [tuple(r) for r in rows]
