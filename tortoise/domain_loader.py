"""Domain loader — thin adapter over PackRegistry + routing for domain ontologies.

Kind sources collapse to ONE (epic #909 slice 4c, plan §5.2 boundary 4):
``pack_registry`` is canonical; this module compiles the pack vocabulary into
bucket-aware views consumed by the extractor, SDK and hosted validators:

- ``known_kinds()``              — flat legacy view (base + registered + pack kinds)
- ``known_kinds(bucket)``        — kinds for one bucket (pointKind/objectKind/
                                   eventKind/documentKind/subjectKind)
- ``kind_is_known(kind)``        — 1-arg legacy flat check
- ``kind_is_known(kind, bucket)``— 2-arg bucket-scoped check
- ``domain_kinds(domain, bucket)``      — pack kinds for the domain's namespace
                                          + core kinds (R6 §5.4 read side)
- ``domain_kind_semantics(domain, bucket)`` — {kind: description} from pack kindDefs

The legacy base vocabulary (``_BASE_KINDS``, mirrors ONTOLOGY §12) and
``register_kind()`` stay for backward compatibility; pack kinds are layered on
top so existing callers see a strict superset (document-domain routing is
unchanged). The system still accepts any string — the registry is descriptive
(used for warnings), not restrictive.

Domain manifest (config/domain_manifest.yaml) provides routing configuration:
query patterns, event types, Cypher templates, timeout, priority.
``kind_values`` in the manifest are LEGACY (#951) — still registered into the
flat registry for backward compat, but the packs are the canonical source.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from .pack_registry import (
    CANONICAL_DOCUMENT_KINDS,
    CANONICAL_EVENT_KINDS,
    CANONICAL_OBJECT_KINDS,
    CANONICAL_POINT_KINDS,
    PackRegistry,
)

log = logging.getLogger(__name__)

# Base known kinds (ONTOLOGY §12).
_BASE_KINDS: set[str] = {
    "statement", "decision", "vision", "strategy",
    "plan", "goal", "target", "observation", "hypothesis",
    "jobToBeDone", "useCase", "userJourney", "workflow", "requirement", "issue",
    # Event kinds
    "session", "meeting", "milestone", "incident",
    # #531: human approval — event kind + decision point kind (approval pattern)
    "humanApproval",
}


# ── Pack vocabulary adapter (epic #909 slice 4c) ───────────────────────

# Buckets the adapter understands. ``subjectKind`` has no pack category (the
# manifest v3 schema has no subjectKinds list), so it always resolves empty —
# the extractor's _SemanticStage falls back to its own defaults there.
_BUCKET_ATTRS: dict[str, str | None] = {
    "pointKind": "point_kinds",
    "objectKind": "object_kinds",
    "eventKind": "event_kinds",
    "documentKind": "document_kinds",
    "subjectKind": None,
}

# Canonical core vocabulary per bucket (pack_registry is the source).
_CORE_KINDS_BY_BUCKET: dict[str, frozenset[str]] = {
    "pointKind": CANONICAL_POINT_KINDS,
    "objectKind": CANONICAL_OBJECT_KINDS,
    "eventKind": CANONICAL_EVENT_KINDS,
    "documentKind": CANONICAL_DOCUMENT_KINDS,
    "subjectKind": frozenset(),
}

_registry: PackRegistry | None = None
_registry_lock = Lock()
# Overridable packs dir (tests inject temp packs; None = repo default).
_PACKS_DIR: Path | None = None
# #1930: env value whose TORTOISE_PACKS_DIR leg fell back (sticky) — while
# the env value is unchanged, _get_registry resolves the skip-env default
# directly instead of re-attempting the broken env dir per call (no reload
# storm). Reset per test via tests/conftest.py.
_env_fallback_key: str | None = None


def _get_registry() -> PackRegistry | None:
    """Lazily load the pack registry once; None if packs are unavailable.

    The adapter must never break the legacy vocabulary — any load failure
    degrades to the base/registered kinds only (ponytail), and is LOGGED
    (never silent). ``TORTOISE_PACKS_DIR`` (env leg) is resolved through
    ``default_packs_dir()``; when the env dir yields 0 valid packs (S1a) or
    its load raises (S1b), warn + sticky-fall back to the packaged/repo
    default so the daemon registry is never silently empty (G1 class, epic
    #1891 WF-2). ``_PACKS_DIR`` (test injection) always wins over env.
    """
    global _registry, _env_fallback_key
    from tortoise.pack_registry import (
        _warn_packs_once,
        default_packs_dir,
        env_packs_dir,
        env_packs_dir_value,
    )
    env_val = env_packs_dir_value()
    if _PACKS_DIR is not None:
        packs_dir = _PACKS_DIR
    elif (_env_fallback_key is not None and _PACKS_DIR is None and env_val
            and env_val == _env_fallback_key):
        # Sticky branch (S1a/S1b landed): skip the broken env dir. The
        # _PACKS_DIR guard mirrors the set-side guards — test injection must
        # always win over env, even after a fallback.
        packs_dir = default_packs_dir(_skip_env=True)
    else:
        packs_dir = default_packs_dir()
    if _registry is None or _registry.packs_dir != packs_dir:
        with _registry_lock:
            if _registry is None or _registry.packs_dir != packs_dir:
                try:
                    reg = PackRegistry(packs_dir)
                    reg.load_all()
                    if (not reg.packs and reg.errors and _PACKS_DIR is None
                            and env_val and env_packs_dir() == packs_dir):
                        # S1a: env leg active but every manifest failed
                        # validation — the empty-registry defect class (G1).
                        # (The _PACKS_DIR guard is intentional: the test-only
                        # injection knob with an all-malformed dir yields an
                        # empty registry + Task-2 warn, no fallback.)
                        _env_fallback_key = env_val
                        packs_dir = default_packs_dir(_skip_env=True)
                        log.warning(
                            "TORTOISE_PACKS_DIR=%r: 0 valid packs (%d isolated) "
                            "— falling back to the default catalog %s",
                            env_val, len(reg.errors), packs_dir)
                        reg = PackRegistry(packs_dir)
                        reg.load_all()
                        reg.fallback_note = (
                            f"TORTOISE_PACKS_DIR={env_val}: 0 valid packs; "
                            f"loaded default catalog {packs_dir}")
                    _registry = reg
                except Exception as e:  # noqa: BLE001, RUF100 — ponytail
                    if (_PACKS_DIR is None and env_val
                            and env_packs_dir() == packs_dir):
                        # S1b: the env-dir load itself raised — fall back once
                        # (never silent empty), same sticky semantics.
                        _env_fallback_key = env_val
                        packs_dir = default_packs_dir(_skip_env=True)
                        log.warning(
                            "TORTOISE_PACKS_DIR=%r: pack load failed (%s) — "
                            "falling back to the default catalog %s",
                            env_val, e, packs_dir)
                        try:
                            reg = PackRegistry(packs_dir)
                            reg.load_all()
                            reg.fallback_note = (
                                f"TORTOISE_PACKS_DIR={env_val}: load failed; "
                                f"loaded default catalog {packs_dir}")
                            _registry = reg
                        except Exception as e2:  # noqa: BLE001, RUF100
                            _warn_packs_once(("ponytail", "default-load"),
                                "pack registry load failed, degrading to "
                                "legacy vocabulary: %s", e2)
                            _registry = None
                    else:
                        _warn_packs_once(("ponytail", str(packs_dir)),
                            "pack registry load failed, degrading to legacy "
                            "vocabulary: %s", e)
                        _registry = None
    return _registry


def _pack_kinds_by_bucket() -> dict[str, set[str]]:
    """Compile every loaded pack's kinds into {bucket: set of bare kind names}."""
    result: dict[str, set[str]] = {b: set() for b in _BUCKET_ATTRS}
    reg = _get_registry()
    if reg is None:
        return result
    for pack in reg.packs.values():
        for bucket, attr in _BUCKET_ATTRS.items():
            if attr is not None:
                result[bucket].update(getattr(pack, attr))
    return result


def _check_bucket(bucket: str) -> None:
    if bucket not in _BUCKET_ATTRS:
        raise ValueError(
            f"unknown kind bucket {bucket!r} "
            f"(expected one of {sorted(_BUCKET_ATTRS)})"
        )


def known_kinds(bucket: str | None = None) -> set[str]:
    """Return the current set of known kind values.

    No arg (legacy): base + registered + every loaded pack's kinds (bare
    names) — the compiled pack vocabulary, flat.
    With a bucket: kinds for that bucket only (canonical core + pack kinds;
    pointKind also includes the legacy base registry so existing document-
    domain warnings don't change).
    """
    if bucket is None:
        pack_kinds: set[str] = set()
        for kinds in _pack_kinds_by_bucket().values():
            pack_kinds.update(kinds)
        return frozenset(_BASE_KINDS) | frozenset(pack_kinds)  # frozen: no mutation
    _check_bucket(bucket)
    kinds: set[str] = set(_CORE_KINDS_BY_BUCKET[bucket])
    if bucket == "pointKind":
        kinds.update(_BASE_KINDS)  # legacy flat registry ≈ point/event kinds
    kinds.update(_pack_kinds_by_bucket()[bucket])
    return frozenset(kinds)


def register_kind(kind: str) -> None:
    """Register an additional kind value from a domain ontology (legacy flat)."""
    _BASE_KINDS.add(kind)


def kind_is_known(kind: str, bucket: str | None = None) -> bool:
    """Check if a kind value is in the known registry.

    1-arg (legacy): flat check against the compiled vocabulary.
    2-arg: bucket-scoped check; namespaced refs (``ns:kind``) resolve to the
    bare name.
    """
    if bucket is None:
        return kind in known_kinds()
    if ":" in kind:
        kind = kind.split(":", 1)[-1]
    return kind in known_kinds(bucket)


def domain_kinds(domain: str, bucket: str) -> list[str]:
    """Kind names for a domain: the pack's kinds for the bucket (when a pack
    with that namespace is loaded) + canonical core kinds, bucket-scoped.

    Previously nonexistent — extractor.py:745-746 called it and silently
    fell back to defaults (research-r6 §1.2, fixed by #951).
    """
    _check_bucket(bucket)
    result: list[str] = []
    seen: set[str] = set()
    reg = _get_registry()
    pack = reg.get_pack(domain) if reg is not None else None
    if pack is not None:
        attr = _BUCKET_ATTRS[bucket]
        if attr is not None:
            for k in getattr(pack, attr):
                result.append(k)
                seen.add(k)
    for k in sorted(_CORE_KINDS_BY_BUCKET[bucket]):
        if k not in seen:
            result.append(k)
            seen.add(k)
    if bucket == "pointKind":
        # Legacy flat registry (workflow/requirement/issue/...) — kept so the
        # old document-domain vocabulary stays visible to the prompt.
        for k in sorted(_BASE_KINDS):
            if k not in seen:
                result.append(k)
    return result


def domain_kind_semantics(domain: str, bucket: str) -> dict[str, str]:
    """{kind: description} for the domain's pack kindDefs, bucket-scoped.

    Pack kinds carry their kindDefs[].description when declared, "" otherwise;
    canonical core kinds are included with "" so the extractor fills its core
    defaults (kind defs come from the packs now, defaults are the fallback).
    Empty dict when no pack matches the domain.
    """
    _check_bucket(bucket)
    semantics: dict[str, str] = {}
    reg = _get_registry()
    pack = reg.get_pack(domain) if reg is not None else None
    if pack is not None:
        attr = _BUCKET_ATTRS[bucket]
        if attr is not None:
            for kind in getattr(pack, attr):
                kd = pack.kind_defs.get(kind, {})
                desc = kd.get("description", "") if isinstance(kd, dict) else ""
                semantics[kind] = desc
    for kind in sorted(_CORE_KINDS_BY_BUCKET[bucket]):
        semantics.setdefault(kind, "")
    return semantics


# ── Domain Routing Config ─────────────────────────────

@dataclass
class DomainRoutingConfig:
    """Routing configuration for one domain ontology."""
    key: str
    name: str
    active: bool = True
    version: str = "1.0"
    event_types: list[str] = field(default_factory=list)
    query_patterns: list[str] = field(default_factory=list)
    cypher_template: str = ""
    timeout: float = 5.0
    priority: int = 10


def load_manifest(manifest_path: str | Path | None = None) -> dict[str, DomainRoutingConfig]:
    """Parse domain manifest YAML → {domain_key: DomainRoutingConfig}.

    Returns empty dict if manifest not found or YAML not installed.
    """
    if manifest_path is None:
        manifest_path = Path(__file__).parent.parent / "config" / "domain_manifest.yaml"
    else:
        manifest_path = Path(manifest_path)

    if not manifest_path.exists():
        return {}

    import yaml
    with open(manifest_path) as f:
        data = yaml.safe_load(f) or {}

    domains: dict[str, DomainRoutingConfig] = {}
    for key, raw in (data.get("domains") or {}).items():
        if not raw.get("active", True):
            continue
        # Register kind values from manifest (LEGACY #951: pack manifests are
        # the canonical kind source; kept so existing document-domain routing
        # and warnings don't change).
        kind_values = raw.get("kind_values") or {}
        for kind_list in kind_values.values():
            for k in kind_list:
                register_kind(k)
        domains[key] = DomainRoutingConfig(
            key=key,
            name=raw.get("name", key),
            active=True,
            version=raw.get("version", "1.0"),
            event_types=raw.get("event_types") or [],
            query_patterns=raw.get("query_patterns") or [],
            cypher_template=raw.get("cypher_template") or "",
            timeout=float(raw.get("timeout", 5.0)),
            priority=int(raw.get("priority", 10)),
        )
    return domains


def domain_event_types(domains: dict[str, DomainRoutingConfig]) -> dict[str, list[str]]:
    """Extract {domain_key: [event_type, ...]} for write-side routing."""
    return {k: v.event_types for k, v in domains.items()}


def domain_query_patterns(domains: dict[str, DomainRoutingConfig]) -> dict[str, list[str]]:
    """Extract {pattern: [domain_key, ...]} for read-side routing."""
    patterns: dict[str, list[str]] = {}
    for key, cfg in domains.items():
        for p in cfg.query_patterns:
            patterns.setdefault(p, []).append(key)
    return patterns


def domain_cypher_templates(domains: dict[str, DomainRoutingConfig]) -> dict[str, str]:
    """Extract {domain_key: cypher} for dispatch."""
    return {k: v.cypher_template for k, v in domains.items() if v.cypher_template}


def resolve_domain_from_path(file_path: str, manifest_path: str | Path | None = None) -> str:
    """Resolve domain from file path using manifest's directory_map.

    Longest-prefix match wins. Falls back to "capability" for unmatched paths.
    """
    if manifest_path is None:
        manifest_path = Path(__file__).parent.parent / "config" / "domain_manifest.yaml"
    manifest_path = Path(manifest_path)

    if not manifest_path.exists():
        return "capability"

    import yaml
    with open(manifest_path) as f:
        data = yaml.safe_load(f) or {}

    directory_map: dict[str, str] = data.get("directory_map") or {}
    # Sort by prefix length descending for longest-prefix match
    sorted_prefixes = sorted(directory_map.items(), key=lambda kv: len(kv[0]), reverse=True)
    for prefix, domain in sorted_prefixes:
        if file_path.startswith(prefix):
            return domain

    return "capability"


# ── Domain integrity validator registry (#405) ────────────────────────────
# Constraint-registration API (issue #405, scoping bullet 1): domains register
# validation functions per surface, discovered by the CLI, MCP tool and the
# commit-path hook. Definitions (registered fns) are separated from
# enforcement strategy (warn-first at write time; advisory on-demand runs) —
# SHACL-style, per the research brief.
#
# Surfaces:
#   "graph"          — graph-global rules (orphan useCase, dangling refs, …),
#                      run on-demand via `tortoise validate --domain` / MCP.
#                      Signature: fn(graph) -> list[dict]. Never run at commit
#                      (the commit path has no graph state by construction).
#   "payload_local"  — intra-payload rules (leapfrog, …), run from the commit
#                      path via validate_domain_rules(). Signature:
#                      fn(payload) -> list[dict]. MUST NOT touch the graph.
#
# Production validators register at IMPORT TIME in tortoise/domain_validators.py
# (imported by the CLI, MCP server and SDK paths) so every process sees the
# same registry — a missing import would make `validate` report a false-clean.
# The registry is append-only after import; tests may register under synthetic
# domains (no production path looks those up).

SURFACE_GRAPH = "graph"
SURFACE_PAYLOAD_LOCAL = "payload_local"
_VALID_SURFACES: frozenset[str] = frozenset({SURFACE_GRAPH, SURFACE_PAYLOAD_LOCAL})

# (domain, surface) -> [registered spec dicts]. Ordered: earlier registrations
# run first. Spec: {domain, surface, chain_id, fn}.
_DOMAIN_VALIDATORS: dict[tuple[str, str], list[dict]] = {}
_VALIDATORS_LOCK = Lock()


def _check_surface(surface: str) -> None:
    if surface not in _VALID_SURFACES:
        raise ValueError(
            f"unknown validator surface {surface!r} "
            f"(expected one of {sorted(_VALID_SURFACES)})"
        )


def register_domain_validator(
    domain: str,
    *,
    chain_id: str | None = None,
    surface: str = SURFACE_GRAPH,
    fn: Callable[[Any], list[dict]] | None = None,  # noqa: F821
) -> Callable[[Any], list[dict]] | None:  # noqa: F821
    """Register a per-domain validation function (issue #405).

    ``surface`` selects when the function runs (graph-global on-demand vs
    payload-local at commit). ``chain_id`` ties the rule to a manifest chain
    (used for enforcement resolution + drift detection). Returns ``fn`` when
    given (decorator-compatible); callers may also use ``@domain_validator``.
    Duplicate (domain, surface, chain_id, fn) registrations are ignored;
    re-registering the same fn under a fresh chain_id appends a second entry.
    """
    _check_surface(surface)
    if fn is None:
        raise ValueError("register_domain_validator requires fn (or use @domain_validator)")
    key = (domain, surface)
    spec = {"domain": domain, "surface": surface, "chain_id": chain_id, "fn": fn}
    with _VALIDATORS_LOCK:
        entries = _DOMAIN_VALIDATORS.setdefault(key, [])
        for existing in entries:
            if existing["chain_id"] == chain_id and existing["fn"] == fn:
                return fn  # idempotent re-registration (imports may run twice)
        entries.append(spec)
    return fn


def domain_validator(
    domain: str,
    *,
    chain_id: str | None = None,
    surface: str = SURFACE_GRAPH,
) -> Callable:  # noqa: F821
    """Decorator form of register_domain_validator.

    Usage:
        @domain_validator("product-strategy", chain_id="productDelivery",
                          surface=SURFACE_GRAPH)
        def validate_chain_integrity(graph) -> list[dict]: ...
    """

    def deco(fn: Callable[[Any], list[dict]]) -> Callable[[Any], list[dict]]:  # noqa: F821
        register_domain_validator(domain, chain_id=chain_id, surface=surface, fn=fn)
        return fn

    return deco


def domain_validators(
    domain: str, surface: str | None = None
) -> list[dict]:
    """Discover registered validators for a domain.

    Returns a COPY of the registered specs ({domain, surface, chain_id, fn})
    so callers can never mutate the live registry. ``surface=None`` returns
    validators for every surface. Unknown domains → empty list.
    """
    if surface is not None:
        _check_surface(surface)
        with _VALIDATORS_LOCK:
            return list(_DOMAIN_VALIDATORS.get((domain, surface), []))
    out: list[dict] = []
    with _VALIDATORS_LOCK:
        for s in sorted(_VALID_SURFACES):  # deterministic cross-surface order
            out.extend(list(_DOMAIN_VALIDATORS.get((domain, s), [])))
    return out


def domain_chain_spec(domain: str) -> dict[str, dict]:
    """Manifest chain declaration for a domain (issue #405, the *declaration*
    reference): {chain_id: {id, name, steps, enforcement}} from the pack
    manifest v3 ``chains[]``. Empty dict when no pack matches the domain.
    """
    reg = _get_registry()
    pack = reg.get_pack(domain) if reg is not None else None
    if pack is None:
        return {}
    spec: dict[str, dict] = {}
    for chain in getattr(pack, "chains", []) or []:
        chain_id = chain.get("id")
        if not chain_id:
            continue
        spec[chain_id] = {
            "id": chain_id,
            "name": chain.get("name", chain_id),
            "steps": list(chain.get("steps", [])),
            "enforcement": chain.get("enforcement", "warn"),
        }
    return spec


def known_domains() -> list[str]:
    """Sorted namespaces of every loaded pack (the queryable domain set).
    Empty when packs are unavailable."""
    reg = _get_registry()
    if reg is None:
        return []
    return sorted(reg.packs.keys())


def pack_kind_overlap(domain: str, bucket: str, kinds: list[str]) -> int:
    """Count of the given kind names (bare, namespace stripped) present in a
    pack's OWN kind list for ``bucket``. Only the pack's declared kinds are
    counted (canonical core kinds are not pre-added to the score, so the
    score measures pack attribution, not shared vocabulary). Used by the
    commit path's fail-safe domain inference (#405)."""
    _check_bucket(bucket)
    reg = _get_registry()
    pack = reg.get_pack(domain) if reg is not None else None
    if pack is None:
        return 0
    attr = _BUCKET_ATTRS[bucket]
    own = getattr(pack, attr) if attr is not None else []
    bare = {k.split(":", 1)[-1] for k in kinds}
    return sum(1 for k in own if k in bare)
