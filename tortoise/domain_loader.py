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


def _get_registry() -> PackRegistry | None:
    """Lazily load the pack registry once; None if packs are unavailable.

    The adapter must never break the legacy vocabulary — any load failure
    degrades to the base/registered kinds only (ponytail).
    """
    global _registry
    packs_dir = _PACKS_DIR or (Path(__file__).resolve().parent.parent / "packs")
    if _registry is None or _registry.packs_dir != packs_dir:
        with _registry_lock:
            if _registry is None or _registry.packs_dir != packs_dir:
                try:
                    reg = PackRegistry(packs_dir)
                    reg.load_all()
                    _registry = reg
                except Exception:
                    _registry = None  # ponytail: degrade to legacy vocabulary
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
