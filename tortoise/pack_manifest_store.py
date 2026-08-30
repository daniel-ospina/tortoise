"""Per-tenant custom pack manifests (#1935, epic #1891 slice 4).

Extends the #318 pack-isolation model with TENANT-AUTHORED manifests:
the shared catalog stays read-only for tenants; a tenant's custom packs
are stored graph-natively as ``(:PackManifest {namespace, name, version,
yaml, sha256, status, installed_at})`` nodes in the tenant's OWN graph
(``team_{team_id}`` — the LANDED isolation boundary, so cross-tenant
access is structurally impossible, same as #318).

Design (plan §4/§5, test-design #1898 surfaces 7/8):

- **Ontology-only v1**: tenant uploads reject connector/tool entrypoints —
  the only code-execution surface stays allowlisted (starter packs).
- **Reserved-namespace rejection**: a tenant manifest whose namespace is in
  the starter set → 422 (one guard mirrors the CLI scaffold guard).
- **Shared validator**: uploads run the SAME validation as the filesystem
  catalog (schema + cross-pack vs core+starter) via a temp-dir
  ``PackRegistry`` — no second validator to drift.
- **#1154 resolution (process-global singleton leak)**: the shared catalog
  stays cached-global read-only; the TENANT VIEW (catalog + tenant's
  manifests) is memoized per ``(tenant_identity, pack_config_version)``
  where the version is a hash of the tenant's ``(namespace, version,
  sha256)`` manifest tuples — invalidated on any ``:PackManifest`` write.
  NEVER cached globally; the cache key includes tenant identity, so
  isolation is preserved. This also avoids the #1350 perf class (per-call
  manifest re-parse on the hosted extraction hot path).
- **Idempotent additive activation**: ``PackInstall`` MERGE per
  (graph, namespace) with source='custom', serialized by the existing
  per-(graph, namespace) lock (#1307).
"""
from __future__ import annotations

import hashlib
import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

import yaml

from tortoise.pack_state import _pack_install_lock, _resolved_graph_name, _target_graph

log = logging.getLogger(__name__)

PACK_MANIFEST_LABEL = "PackManifest"
MAX_MANIFEST_BYTES = 64 * 1024  # 64KB cap (plan §6)

# #2029: wire cap for POST /v1/packs/manifests — 6x the yaml cap + slack.
# 6x covers the WORST legal JSON escape inflation (control chars U+0000..1F
# → \uXXXX = 6 bytes per 1-byte char; non-ASCII ≤3x; `"`/`\`/newline 2x), so
# every legal JSON encoding of any <=64KB manifest clears the wire layers
# (the exact len(manifest_yaml.encode()) check remains the authoritative
# gate). Memory bound ~397KB/request — trivial vs the 64MiB import cap.
MANIFEST_WIRE_CAP_BYTES = MAX_MANIFEST_BYTES * 6 + 4096

# Ontology-only v1: tenant uploads must not carry code entrypoints.
_ONTOLOGY_ONLY_KEYS = ("connectors", "tools")

# ── Tenant-view memoization (#1154 + #1350) ────────────────────────────────
# Key: (tenant_identity, pack_config_version) → compiled view. The shared
# catalog is NOT in the key (it is cached-global read-only, safe to share);
# the tenant's manifest set IS (isolation + invalidation). tenant_identity
# = the SDK's resolved graph name (team_{team_id}) — the #2031 consumer
# path derives it from the tenant-scoped SDK, never from a caller-supplied
# id (a mismatched id/identity pairing is structurally impossible).
_TENANT_VIEWS: dict[tuple[str, str], dict] = {}
_TENANT_VIEWS_GUARD = Lock()
# Monotonic bump: any :PackManifest write invalidates the tenant's entry.
_TENANT_VIEW_DIRTY: set[str] = set()
# Fleet bound (#2031 review): the memo keeps one entry per (gid, version)
# and is never evicted beyond the per-gid prune — a long-lived multi-tenant
# process would otherwise accumulate one compiled brief + manifest YAML set
# per tenant ever seen. Cap the distinct-gid count; a dropped tenant simply
# recompiles on its next capture (the dirty-set + sha256 key stay intact).
_MAX_TENANT_VIEWS = 1024


@dataclass(frozen=True)
class ManifestValidation:
    """Result of validating one uploaded manifest."""
    ok: bool
    namespace: str | None = None
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.errors is None:
            object.__setattr__(self, "errors", [])


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


# ── Validation (shared validator — one source of truth) ────────────────────

def validate_manifest(manifest_yaml: str) -> ManifestValidation:
    """Validate a single uploaded manifest against the registry schema.

    Runs the SAME validation as the filesystem catalog: writes the manifest
    to a temp packs dir and loads via ``PackRegistry`` (schema + cross-pack
    refs + per-pack isolation), then applies the tenant-specific policy:
    reserved starter namespace → error; connector/tool entrypoints
    (ontology-only v1) → error; size cap enforced by the caller (413).
    """
    if len(manifest_yaml.encode()) > MAX_MANIFEST_BYTES:
        return ManifestValidation(False, errors=["manifest exceeds 64KB"])
    try:
        raw = yaml.safe_load(manifest_yaml) or {}
    except yaml.YAMLError as e:
        return ManifestValidation(False, errors=[f"invalid YAML: {e}"])
    except RecursionError:
        # #2040 Task 4: a deeply-nested payload-controlled yaml raises
        # RecursionError (NOT a YAMLError subclass) through safe_load — catch
        # it as a clean validation failure (422-class), never a 500 post-swap.
        return ManifestValidation(False, errors=["invalid YAML: nesting too deep"])
    if not isinstance(raw, dict):
        return ManifestValidation(False, errors=["manifest must be a YAML mapping"])
    ns = str(raw.get("namespace", "")).strip()
    if not ns:
        return ManifestValidation(False, errors=["missing required field: namespace"])
    if ":" in ns:
        return ManifestValidation(False, errors=["namespace must not contain ':'"])
    # #2029 review gate (security P1): ns becomes a DIRECTORY NAME in the
    # temp registry below — a traversal namespace ("../x", "a/b", "..")
    # would mkdir/write OUTSIDE the tempdir (arbitrary file write for an
    # authenticated tenant). Charset guard is the first line; the
    # resolve()+is_relative_to containment check backstops it regardless —
    # a single regex is one bug away from admitting a `..` component (or a
    # future relaxation), so containment is defense in depth, not ceremony.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", ns):
        return ManifestValidation(
            False, namespace=ns,
            errors=["namespace must be a safe identifier "
                    "(letters, digits, '_', '-', '.')"])


    # Tenant policy (plan §4): reserved namespaces. The starter set is the
    # canonical collision surface (a tenant pack named `dev` would collide
    # with the starter `dev` PackInstall); ``core`` and
    # ``memory_granularity`` are reserved because the #2031 tenant-view
    # compile overlays tenant kinds onto ``compile_value_brief``'s dicts,
    # where a tenant ``core`` namespace would shadow the canonical core
    # vocabulary and a ``memory_granularity`` namespace would shadow the
    # brief's reserved granularity key.
    from tortoise.pack_state import DEFAULT_STARTER_PACKS
    if ns in DEFAULT_STARTER_PACKS:
        return ManifestValidation(
            False, namespace=ns,
            errors=[f"namespace '{ns}' is a reserved starter pack — pick a different name"])
    if ns in ("core", "memory_granularity"):
        return ManifestValidation(
            False, namespace=ns,
            errors=[f"namespace '{ns}' is a reserved namespace — pick a different name"])

    # Ontology-only v1: reject connector/tool entrypoints (code surfaces).
    for key in _ONTOLOGY_ONLY_KEYS:
        if raw.get(key):
            return ManifestValidation(
                False, namespace=ns,
                errors=[f"connector/tool entrypoints are not allowed on tenant "
                        f"packs (ontology-only v1) — remove '{key}'"])

    # Shared validator via temp-dir registry (schema + cross-pack).
    with tempfile.TemporaryDirectory(prefix="tortoise-manifest-") as td:
        td_root = Path(td).resolve()
        pack_dir = (td_root / ns).resolve()
        if not pack_dir.is_relative_to(td_root):
            return ManifestValidation(False, namespace=ns,
                                      errors=["invalid namespace path"])
        pack_dir.mkdir(parents=True)
        (pack_dir / "manifest.yaml").write_text(manifest_yaml)
        from tortoise.pack_registry import PackRegistry
        reg = PackRegistry(Path(td))
        reg.load_all()
        if ns in reg.errors:
            return ManifestValidation(False, namespace=ns, errors=reg.errors[ns])
        if ns not in reg.packs:
            return ManifestValidation(False, namespace=ns,
                                      errors=["manifest failed to register"])
    return ManifestValidation(True, namespace=ns)


# ── Storage (graph-native :PackManifest in the tenant graph) ───────────────

def upsert_tenant_manifest(sdk, manifest_yaml: str) -> dict:
    """Validate + store + activate a tenant manifest. Returns the record.

    Raises ValueError on validation failure (caller maps to 422) — the
    graph is never touched on a failed validation.
    """
    result = validate_manifest(manifest_yaml)
    if not result.ok:
        raise ValueError("; ".join(result.errors or ["invalid manifest"]))
    ns = result.namespace
    assert ns is not None
    raw = yaml.safe_load(manifest_yaml)
    g = _target_graph(sdk, None)
    lock_graph = _resolved_graph_name(sdk, None)
    sha = _sha256(manifest_yaml)
    now = _now()
    with _pack_install_lock(lock_graph, ns):
        g.query(
            f"MERGE (m:{PACK_MANIFEST_LABEL} {{namespace: $ns}}) "
            "SET m.name = $name, m.version = $version, m.yaml = $yaml, "
            "    m.sha256 = $sha, m.status = 'active', m.installed_at = $now",
            params={
                "ns": ns,
                "name": raw.get("name", ns),
                "version": str(raw.get("version", "0.1.0")),
                "yaml": manifest_yaml,
                "sha": sha,
                "now": now,
            },
        )
        # Activate (PackInstall source='custom') — idempotent additive MERGE.
        g.query(
            "MERGE (p:PackInstall {namespace: $ns}) "
            "SET p.version = $version, p.status = 'active', "
            "    p.source = 'custom', p.installed_at = coalesce(p.installed_at, $now)",
            params={"ns": ns, "version": str(raw.get("version", "0.1.0")), "now": now},
        )
    # Invalidate the tenant-view memo (#1154/#1350).
    with _TENANT_VIEWS_GUARD:
        _TENANT_VIEW_DIRTY.add(_graph_identity(sdk))
    return {"namespace": ns, "version": str(raw.get("version", "0.1.0")),
            "status": "active", "source": "custom"}


def _graph_identity(sdk) -> str:
    """Physical graph identity for the tenant-view cache key.

    #2031 review fix: ``_resolved_graph_name`` takes ``(sdk, graph_name)`` —
    passing ``None`` resolves the SDK's namespace-scoped graph name (e.g.
    ``team_team-xxx``). The pre-#2031 one-arg call TypeError'd into the
    catch-all, collapsing EVERY tenant's key to "default" (one shared memo
    entry + one global dirty flag) — activated once tenant_view gained its
    first real consumer on the hosted capture hot path. The fallback is
    logged (never silent): if graph resolution ever fails again, the
    collapse is visible in the logs rather than quietly reinstated."""
    try:
        return _resolved_graph_name(sdk, None)
    except Exception as e:  # noqa: BLE001, RUF100
        log.warning("tenant-view graph identity resolution failed — "
                    "falling back to 'default' (cross-tenant memo key "
                    "collapse risk): %s", e)
        return "default"


def get_tenant_manifests(sdk) -> list[dict]:
    """Read the tenant's :PackManifest nodes (namespace/name/version/status/
    sha256). sha256 is the manifest content fingerprint — the #1935
    version-hash omits it and #2031 adds it to the tenant-view cache key
    (the cross-process staleness signal; the dirty-set is process-local)."""
    g = _target_graph(sdk, None)
    rows = g.query(
        f"MATCH (m:{PACK_MANIFEST_LABEL}) "
        "RETURN m.namespace, m.name, m.version, m.status, m.sha256 "
        "ORDER BY m.namespace",
    ).result_set
    return [{"namespace": r[0], "name": r[1], "version": r[2], "status": r[3],
             "sha256": r[4]}
            for r in rows]


def _get_tenant_manifest_yamls(sdk) -> dict[str, str]:
    """namespace → full manifest YAML text for every :PackManifest node
    (the #2031 consumer-path vocab source — fed through
    ``compile_value_brief(tenant_manifests=...)``, never a parallel compile)."""
    g = _target_graph(sdk, None)
    rows = g.query(
        f"MATCH (m:{PACK_MANIFEST_LABEL}) RETURN m.namespace, m.yaml "
        "ORDER BY m.namespace",
    ).result_set
    return {r[0]: r[1] for r in rows if r[0] and r[1]}


def delete_tenant_manifest(sdk, namespace: str) -> bool:
    """Remove a tenant manifest + its activation (additive-reverse, explicit)."""
    g = _target_graph(sdk, None)
    lock_graph = _resolved_graph_name(sdk, None)
    with _pack_install_lock(lock_graph, namespace):
        g.query(f"MATCH (m:{PACK_MANIFEST_LABEL} {{namespace: $ns}}) DELETE m",
                params={"ns": namespace})
        g.query("MATCH (p:PackInstall {namespace: $ns, source: 'custom'}) "
                "SET p.status = 'removed'", params={"ns": namespace})
    with _TENANT_VIEWS_GUARD:
        _TENANT_VIEW_DIRTY.add(_graph_identity(sdk))
    return True


# ── Tenant-view compile (#1154/#1350) ──────────────────────────────────────

def tenant_view(sdk) -> dict:
    """The tenant's pack view: shared catalog + tenant manifests, memoized.

    Cache key: (graph_identity, pack_config_version) where the version is a
    hash of the tenant's (namespace, version, sha256) manifest tuples
    (sha256 in the key since #2031 — the view is the hosted extraction
    vocabulary source, and the hash is the only cross-process staleness
    signal; the dirty-set is process-local). Invalidated by any
    :PackManifest write (dirty-set). The shared catalog is read from the
    global registry (read-only, safe to share — #1154).

    The tenant identity is the SDK's resolved graph name — callers must
    pass the tenant-scoped SDK (``_make_sdk(namespace=team_id)``); there is
    no separate identity argument to mismatch (#2031 review: the pre-#2031
    ``team_id`` parameter was dead — every caller could pair any id with
    any sdk).

    #2031 consumer wiring: the memoized view now also carries the manifest
    YAMLs (``yaml``) and the COMPILED value brief (``brief`` — shared
    catalog + this tenant's manifests, via ``compile_value_brief`` per the
    epic plan §4 "no parallel compile path"). ``build_master_list`` reads
    the brief on the hosted path, so the compile happens once per
    (graph_identity, pack_config_version) — the #1350 perf guard rides the
    memo.
    """
    gid = _graph_identity(sdk)
    manifests = get_tenant_manifests(sdk)
    version = hashlib.sha1(
        repr(sorted((m["namespace"], m["version"], m["sha256"])
                    for m in manifests)).encode()
    ).hexdigest()[:12]
    key = (gid, version)
    with _TENANT_VIEWS_GUARD:
        if key in _TENANT_VIEWS and gid not in _TENANT_VIEW_DIRTY:
            return _TENANT_VIEWS[key]
    # Compile: shared catalog summaries + tenant manifests + the value brief.
    from tortoise.domain_loader import _get_registry
    reg = _get_registry()
    catalog = reg.pack_summaries() if reg is not None else {}
    yamls = _get_tenant_manifest_yamls(sdk)
    from tortoise.value_extractor import compile_value_brief
    brief = compile_value_brief(tenant_manifests=yamls)
    view = {"catalog": catalog, "tenant": manifests, "yaml": yamls,
            "brief": brief}
    with _TENANT_VIEWS_GUARD:
        _TENANT_VIEW_DIRTY.discard(gid)
        # #2031 review fix: evict this tenant's prior (gid, version) entries
        # on insert — the sha256-in-key change makes every content revision a
        # NEW key, so without eviction a churn-heavy tenant accumulates one
        # compiled brief + YAML set per revision forever. The versioned key
        # only needs the CURRENT entry for the dirty-set to work.
        for old_key in [k for k in _TENANT_VIEWS if k[0] == gid]:
            del _TENANT_VIEWS[old_key]
        _TENANT_VIEWS[key] = view
        # Fleet bound (#2031 review): evict oldest gids past the cap (dict
        # insertion order = recency) and drop their dirty flags (a stale
        # dirty flag only forces a recompile, but the set should stay
        # bounded with the memo). pop() returns the VALUE — extract the gid
        # from the KEY tuple before deleting.
        while len(_TENANT_VIEWS) > _MAX_TENANT_VIEWS:
            evicted_key = next(iter(_TENANT_VIEWS))
            _TENANT_VIEW_DIRTY.discard(evicted_key[0])
            del _TENANT_VIEWS[evicted_key]
    return view
