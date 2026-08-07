"""Domain loader — extensible kind registry and routing for domain ontologies.

The base vocabulary lives here (mirrors ONTOLOGY §12). Domain ontologies
(Product Strategy, custom user ontologies) register additional kind values
via ``register_kind()``. The system accepts any string — the registry is
descriptive (used for warnings), not restrictive.

Domain manifest (config/domain_manifest.yaml) provides routing configuration:
query patterns, event types, Cypher templates, timeout, priority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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


def known_kinds() -> set[str]:
    """Return the current set of known kind values (base + registered)."""
    return frozenset(_BASE_KINDS)  # ponytail: frozen to avoid accidental mutation


def register_kind(kind: str) -> None:
    """Register an additional kind value from a domain ontology."""
    _BASE_KINDS.add(kind)


def kind_is_known(kind: str) -> bool:
    """Check if a kind value is in the known registry."""
    return kind in _BASE_KINDS


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
        # Register kind values from manifest
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
