"""#318 — per-tenant pack activation state (``PackInstall`` install records).

Multi-tenant pack isolation: a shared pack catalog (``pack_registry``) plus
per-tenant activation records stored graph-natively in each tenant's graph
(``graph_name=org_{org_id}``, the LANDED isolation boundary). The tenant
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
- D5  Existing-tenant backfill handles ``org_{name}`` vs ``org_{id}``
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
# #1933 (epic #1891): agent-ops joins the starter set (defaults become 5 —
# dev, marketing, product-strategy, pm, agent-ops); the CI smoke bound
# derives from len(DEFAULT_STARTER_PACKS) so it self-adjusts. Existing
# tenants converge additively via ensure_tenant_packs (idempotent MERGE — R8).
DEFAULT_STARTER_PACKS: tuple[str, ...] = ("dev", "marketing", "product-strategy", "pm", "agent-ops")

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
    isolation boundary ``org_{org_id}`` for org SDKs). An explicit
    ``graph_name`` targets a specific graph — used by the D5 backfill to
    handle legacy ``org_{name}``-named tenant graphs (recorded graph_name).
    """
    proj = sdk._get_proj()
    if graph_name is None:
        return proj.g
    # Epic #1647 (PR #1684 CI-fix): on the DOCKER lane the redirect derives
    # per-path test_<stem>_<hash> names — an explicit legacy name
    # (org_org-k, the D5 backfill's recorded graph_name) does NOT exist
    # as a physical server graph. Routing it raw splits the lock AND the
    # target from the SDK's namespace graph (the #1307 race survives on
    # docker: provision locks derived-X, backfill locks raw-Y, both write
    # the same derived graph). Resolve through the projection's derivation:
    # the redirect's own rule (test-prefixed verbatim, else derived from
    # path+name) applied to the explicit name.
    # Epic #1647 (PR #1684 CI-fix): on the DOCKER TEST lane the redirect
    # derives per-path test_<stem>_<hash> names — an explicit legacy name
    # (org_org-k, the D5 backfill's recorded graph_name) does NOT exist as
    # a physical server graph. Routing it raw splits the lock AND the target
    # from the SDK's namespace graph (the #1307 race survives on docker:
    # provision locks derived-X, backfill locks raw-Y, both write the same
    # derived graph). Resolve through the projection's derivation — BUT ONLY
    # when the redirect actually fired (TEST_MODE=1 + explicit path): in
    # PROD (URI set, no TEST_MODE) the graph IS the real org_<name> and
    # deriving would corrupt the backfill into phantom test_ graphs.
    _test_session = os.environ.get("TORTOISE_TEST_MODE") == "1"
    _explicit = getattr(proj, "_explicit_path", None)
    if (not getattr(proj, "_is_embedded", True) and _test_session
            and _explicit is not None):
        _gn = graph_name
        if not _gn.startswith(("test_", "tortoise_test")):
            import hashlib as _h
            import os as _os
            import re as _re
            _sess = _os.environ.get("TORTOISE_TEST_SESSION", "")
            _path = str(_explicit)
            _stem = _re.sub(r"[^a-zA-Z0-9_]", "_",
                            _os.path.splitext(_os.path.basename(_path))[0])
            _gn = (f"test_{_stem}_"
                   + _h.sha1((_sess + _path + _gn).encode()).hexdigest()[:12])
        return proj.db.select_graph(_gn)
    return proj.db.select_graph(graph_name)


def _resolved_graph_name(sdk: TortoiseSDK, graph_name: str | None) -> str:
    """Resolve the PHYSICAL graph identity a call reads/writes.

    ``graph_name=None`` → the SDK's namespace-scoped graph; the projection
    exposes the LANDED name (``org_{org_id}`` for org SDKs — the #7886
    isolation boundary). An explicit ``graph_name`` (D5 backfill read
    target, ``org_{org_id}``) is already the physical name.

    Locking keys on this RESOLVED name (conf 75, PR #1312): a hosted
    provision (``graph_name=None``) and the backfill (explicit
    ``org_{org_id}``) write the SAME physical graph, so they must
    serialize on the SAME lock. Keying on the passed string instead would
    split one graph across two locks (race survives in the mixed path) and
    collapse every None caller onto one shared ``default`` lock
    (over-serializing distinct tenants).
    """
    proj = sdk._get_proj()
    if graph_name is None:
        return proj.graph_name
    # Epic #1647 (PR #1684 CI-fix): same derivation as _target_graph — an
    # explicit legacy name on the docker lane must resolve to the derived
    # physical graph so the lock matches the target (one lock per physical
    # graph, conf 75).
    _test_session = os.environ.get("TORTOISE_TEST_MODE") == "1"
    _explicit = getattr(proj, "_explicit_path", None)
    if (not getattr(proj, "_is_embedded", True) and _test_session
            and _explicit is not None):
        import hashlib as _h
        import os as _os
        import re as _re
        if not graph_name.startswith(("test_", "tortoise_test")):
            _sess = _os.environ.get("TORTOISE_TEST_SESSION", "")
            _path = str(_explicit)
            _stem = _re.sub(r"[^a-zA-Z0-9_]", "_",
                            _os.path.splitext(_os.path.basename(_path))[0])
            return (f"test_{_stem}_"
                    + _h.sha1((_sess + _path + graph_name).encode()).hexdigest()[:12])
    return graph_name


# #1307: the embedded FalkorDBLite engine does not guarantee server-side
# atomicity of MERGE under thread contention (observed: 8-thread races
# producing duplicate PackInstall nodes). Serialize activation per
# (graph, namespace) in-process — embedded mode is single-writer, so the
# lock is sufficient there; server-side (FalkorDB server/docker) engines
# keep their atomic MERGE. The lock key is the RESOLVED graph name (conf
# 75, PR #1312), so mixed paths (hosted provision ``graph_name=None`` vs
# the backfill's explicit ``org_{org_id}``) hitting the same physical
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
                    f"MERGE (p:{PACK_INSTALL_LABEL} {{namespace: $ns}}) "
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
        f"MATCH (p:{PACK_INSTALL_LABEL}) "
        "RETURN p.namespace, p.version, p.status, p.source, p.installed_at "
        "ORDER BY p.namespace",
    ).result_set
    return [tuple(r) for r in rows]


# ── #2714 layer 1 (APPROVAL): the graph's active-namespace set ────────────
#
# The compile seams (``compile_value_brief``, the ``build_master_list`` prompt
# it feeds, and ``compile_vocab``) used to iterate the CATALOG UNION — every
# pack in ``packs/``, in every graph. #2714 gates them on the GRAPH's
# installed set instead. The gating itself is a pure filter; the only thing
# that needs the graph is this reader.

#: The node properties that carry a ``ns:kind`` value. Mirrors the ontology's
#: kind fields (``hosted_api._KIND_PROP_KEYS`` is the import-time twin — kept
#: as a local copy so this reader never depends on the hosted import path).
_KIND_PROP_KEYS: tuple[str, ...] = (
    "objectKind", "pointKind", "eventKind", "sourceKind",
    "documentKind", "subjectKind", "actionKind", "kind",
)


def graph_kind_namespaces(g) -> frozenset[str]:
    """Namespaces already PRESENT IN THE GRAPH'S DATA (the back-compat union).

    A pack that was installed when data was written can stop being installed
    (``delete_tenant_manifest`` flips its ``PackInstall`` to
    ``status='removed'``) while its kinds remain all over the graph. Those
    kinds must stay READABLE and WRITABLE — a gate that rejects vocabulary
    the graph already contains would make old data un-updatable (#2714
    indicator 2).

    Un-namespaced kind values (legacy/core forms) contribute nothing: only a
    ``ns:kind`` string names a pack to keep alive.

    ⚠️ COST: one property scan per key (8) — a whole-graph read. The
    caller's memo does NOT shelter it: ``tenant_view`` folds the approval set
    into its cache key, so the scan runs on every call (measured ~6 ms at
    300 nodes, and O(nodes) in the graph's size). The gate is a compile-time
    decision, not a render-time one — the cost is per capture, not per prompt
    render. An index on the kind properties, or a cheap data-version signal
    feeding the key, is the optimization.

    ⚠️ FAILURE DIRECTION — NARROWING, not widening. A property scan that
    raises omits that key's namespaces (logged at WARNING) and the loop
    continues, so the returned union can only be SMALLER than a complete
    scan — never larger. The consequence is deliberate and bounded: a
    namespace discoverable ONLY through the failed key drops out of the
    back-compat union, so historical data using it could be refused at the
    write gate until a later successful scan. The record leg (the inline
    ``recorded`` set in ``graph_installed_namespaces``) and the core
    vocabulary are unaffected. The alternative — failing open to the catalog
    union — would silently DISABLE the gate on a graph error and re-create
    the exact "every graph mints every pack's kinds" defect #2714 exists to
    fix. Pinned by ``test_kind_scan_failure_narrows_never_widens``.
    """
    out: set[str] = set()
    for key in _KIND_PROP_KEYS:
        try:
            rows = g.query(
                f"MATCH (n) WHERE n.{key} IS NOT NULL "
                f"RETURN DISTINCT n.{key}",
            ).result_set
        except Exception as e:  # noqa: BLE001, RUF100
            # Narrow the union, never widen it — see FAILURE DIRECTION above.
            log.warning("pack_state: kind-namespace scan failed for %s — "
                        "that key's namespaces are omitted from the "
                        "back-compat union: %s", key, e)
            continue
        try:
            for row in rows:
                val = row[0] if row else None
                if isinstance(val, str) and ":" in val:
                    ns = val.split(":", 1)[0].strip()
                    if ns:
                        out.add(ns)
        except Exception as e:  # noqa: BLE001, RUF100
            # The DECODE is inside the same NARROWING guard as the scan: a
            # row we cannot decode (a non-subscriptable row, a None
            # result_set) contributes nothing, and everything decoded so
            # far stays. Letting it escape would break the contract the
            # FAILURE DIRECTION note above states — and, because
            # graph_installed_namespaces does not catch it, would abort the
            # approval-set read rather than narrow it.
            log.warning("pack_state: kind-namespace row decode failed for "
                        "%s — the remaining rows for that key are omitted "
                        "from the back-compat union: %s", key, e)
            continue
    return frozenset(out)


def graph_installed_namespaces(sdk: TortoiseSDK, *,
                               graph_name: str | None = None
                               ) -> frozenset[str] | None:
    """The graph's APPROVAL set (#2714 layer 1) — which packs this graph allows.

    ``None`` is a MEANINGFUL return, not an empty one:

    * ``None`` — the graph has **no ``:PackInstall`` records at all**. The
      caller MUST fall back to the catalog union (today's behaviour). Every
      pre-#318 graph, every self-hosted graph, and every graph restored
      without pack state has no records — gating those to ``core``-only would
      break them while the new tests still passed (#2714 indicator 3).
    * a frozenset — the graph HAS records. The set is
      ``recorded ∪ already-in-data``: the recorded namespaces (any status —
      see the removal note below) plus every namespace already present in
      the graph's data (``graph_kind_namespaces``).

    ``ensure_tenant_packs``/``get_tenant_packs`` are deliberately NOT used
    here: both SELF-HEAL an empty graph by re-activating the starter set
    (#318 convergence), which would make "no activation records"
    indistinguishable from "starter set installed" and destroy the union
    fallback above. This reads the raw records instead.

    ⚠️ This is a whole-graph read (it runs the per-property kind scans).
    ``tenant_view`` — its only caller — folds the returned set into its memo
    KEY, so the scan runs per call and the memo caches the compiled brief,
    not the scan. That is deliberate: a memo whose key omitted the data leg
    served a stale approval set (an imported graph, or a commit through a
    write door that does not yet consult the gate — see #5163).

    **Removal semantics (#2714 open decision (a) — uninstall vs tombstone):**
    the non-destructive default is taken — a namespace with a non-active
    record (``status='removed'``) STAYS in the set, because its kinds are
    still in the data and the compile seams must keep accepting them. This
    is the "keep writable union" arm of the recorded open decision; the
    "tombstone" arm would filter ``status != 'active'`` out here. Nothing
    is decided silently about the USER surface — per-graph selection/removal
    UI is #2728, gated on the #318-D1 decision.

    Graph-unreachable RAISES (same posture as ``get_tenant_packs``: an
    outage must never read as "this graph has no packs" and silently widen
    or narrow the gate).
    """
    g = _target_graph(sdk, graph_name)
    rows = _read_installs(g)
    if not rows:
        return None
    recorded = {ns for ns, _v, _status, _src, _at in rows if ns}
    return frozenset(recorded) | graph_kind_namespaces(g)
