"""Pack Registry — catalogs expansion packs from packs/*/manifest.yaml.

Loads all manifests at startup (no code execution). Validates against schema.
Provides queryable registry: "what packs are installed?" without importing connectors.

Design:
  - Manifest-first: parse YAML only. No connector code executed on load.
  - Idempotent: safe to re-run register(). Duplicates are skipped.
  - Independent layers: ontology, connector status, and tools are queryable separately.
  - Namespace enforcement: all kinds are prefixed with pack namespace on registration.
"""
from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import yaml

# ── Canonical kind vocabularies (ONTOLOGY_v2.5 §1.1) ──────────────────────

CANONICAL_OBJECT_KINDS = frozenset({
    "document", "product", "customer", "competitor", "user", "skill",
    "workflow", "tool", "agent", "indicator", "database", "api", "code",
    "software", "infrastructure", "agreement", "standard", "epic",
    "project", "task", "other",
})

CANONICAL_EVENT_KINDS = frozenset({
    "meeting", "decision", "experiment", "deployment", "review",
    "friction", "extraction", "documentCreated", "roleCreated", "pointAdded",
})

CANONICAL_POINT_KINDS = frozenset({
    "statement", "decision", "vision", "strategy", "plan", "goal",
    "target", "observation", "hypothesis",
})

CANONICAL_DOCUMENT_KINDS = frozenset({
    "research", "reflectPostmortem", "strategyDoc", "visionDoc",
    "planDoc", "decisionDoc", "meetingNotes", "experimentResults",
    "evidenceLog", "handoff", "transcript", "roadmap", "brief",
})

CANONICAL_ACTION_KINDS = frozenset({
    "research", "scope", "plan", "implement", "verify", "reflect",
    "decompose", "delegate", "loop", "brainstorm", "decide", "agree",
    "meet", "experiment", "deploy", "review", "other",
})

VALID_ENTITY_TYPES = frozenset({
    "Subject", "Object", "Action", "Point", "Event", "Document", "Source",
})

VALID_CARDINALITIES = frozenset({
    "one_to_one", "one_to_many", "many_to_one", "many_to_many",
})

CANONICAL_KINDS = {
    "objectKinds": CANONICAL_OBJECT_KINDS,
    "eventKinds": CANONICAL_EVENT_KINDS,
    "pointKinds": CANONICAL_POINT_KINDS,
    "documentKinds": CANONICAL_DOCUMENT_KINDS,
    "actionKinds": CANONICAL_ACTION_KINDS,
}


# ── Pack data model ───────────────────────────────────────────────────────

@dataclasses.dataclass
class PackManifest:
    """Parsed manifest for one expansion pack."""
    namespace: str
    name: str
    version: str
    tier: str
    description: str
    path: Path

    # Ontology
    object_kinds: list[str] = dataclasses.field(default_factory=list)
    event_kinds: list[str] = dataclasses.field(default_factory=list)
    point_kinds: list[str] = dataclasses.field(default_factory=list)
    document_kinds: list[str] = dataclasses.field(default_factory=list)
    action_kinds: list[str] = dataclasses.field(default_factory=list)
    relations: list[dict] = dataclasses.field(default_factory=list)

    # Connectors
    connectors: list[dict] = dataclasses.field(default_factory=list)

    # Tools
    tools: list[dict] = dataclasses.field(default_factory=list)

    # Dependencies
    depends_on: list[str] = dataclasses.field(default_factory=list)


# ── Registry ──────────────────────────────────────────────────────────────

class PackRegistry:
    """Catalog of all installed expansion packs.

    Usage:
        registry = PackRegistry(packs_dir)
        registry.load_all()
        print(registry.list_packs())
        print(registry.list_all_kinds())
    """

    def __init__(self, packs_dir: Path | str):
        self.packs_dir = Path(packs_dir)
        self.packs: dict[str, PackManifest] = {}
        self.errors: dict[str, list[str]] = {}  # namespace → error messages

    # ── Load ──────────────────────────────────────────────────────────

    def load_all(self) -> int:
        """Load all packs/*/manifest.yaml. Returns count of successfully loaded packs."""
        if not self.packs_dir.exists():
            return 0
        count = 0
        for manifest_path in sorted(self.packs_dir.glob("*/manifest.yaml")):
            ns = manifest_path.parent.name
            if ns.startswith("_") or ns.startswith("."):
                continue  # skip _template, hidden dirs
            try:
                manifest = self._load_one(manifest_path)
                if manifest.namespace in self.packs:
                    self.errors[ns] = [f"duplicate namespace: {manifest.namespace}"]
                    continue
                self.packs[manifest.namespace] = manifest
                count += 1
            except Exception as e:
                self.errors[ns] = [str(e)]
        return count

    def _load_one(self, path: Path) -> PackManifest:
        raw = yaml.safe_load(path.read_text()) or {}
        errors = self._validate(raw)
        if errors:
            raise ValueError(f"Invalid manifest: {'; '.join(errors)}")

        ont = raw.get("ontology", {})
        return PackManifest(
            namespace=raw["namespace"],
            name=raw["name"],
            version=raw.get("version", "0.1.0"),
            tier=raw.get("tier", "free"),
            description=raw.get("description", ""),
            path=path.parent,
            object_kinds=ont.get("objectKinds", []),
            event_kinds=ont.get("eventKinds", []),
            point_kinds=ont.get("pointKinds", []),
            document_kinds=ont.get("documentKinds", []),
            action_kinds=ont.get("actionKinds", []),
            relations=ont.get("relations", []),
            connectors=raw.get("connectors", []),
            tools=raw.get("tools", []),
            depends_on=raw.get("depends_on", []),
        )

    # ── Validate ───────────────────────────────────────────────────────

    def _validate(self, raw: dict) -> list[str]:
        errors: list[str] = []

        # Required top-level fields
        if not raw.get("namespace"):
            errors.append("missing required field: namespace")
        if not raw.get("name"):
            errors.append("missing required field: name")

        ns = raw.get("namespace", "")

        # Namespace format: no colons (would corrupt kind prefixing)
        if ":" in ns:
            errors.append("namespace must not contain ':'")

        ont = raw.get("ontology", {})

        # Validate ontology section
        if ont:
            if ont.get("extends") != "core":
                errors.append("ontology.extends must be 'core'")

            for kind_field in ["objectKinds", "eventKinds", "pointKinds",
                               "documentKinds", "actionKinds"]:
                kinds = ont.get(kind_field, [])
                if not isinstance(kinds, list):
                    errors.append(f"ontology.{kind_field} must be a list")
                    continue
                for k in kinds:
                    if not isinstance(k, str) or not k:
                        errors.append(f"ontology.{kind_field}: invalid kind value: {k}")
                        continue
                    if k[0].isupper():
                        errors.append(
                            f"ontology.{kind_field}: '{k}' should be camelCase "
                            f"(lowercase first letter)"
                        )
                    elif k in CANONICAL_KINDS.get(kind_field, set()):
                        errors.append(
                            f"ontology.{kind_field}: '{k}' is already in canonical "
                            f"vocabulary — no need to register"
                        )

            # Validate relations
            for rel in ont.get("relations", []):
                pred = rel.get("predicate", "")
                if not pred or not pred[0].islower():
                    errors.append(f"relation predicate '{pred}' must be camelCase")
                if rel.get("from") not in VALID_ENTITY_TYPES:
                    errors.append(f"relation '{pred}': invalid 'from' type: {rel.get('from')}")
                if rel.get("to") not in VALID_ENTITY_TYPES:
                    errors.append(f"relation '{pred}': invalid 'to' type: {rel.get('to')}")
                if rel.get("cardinality") not in VALID_CARDINALITIES:
                    errors.append(f"relation '{pred}': invalid cardinality: {rel.get('cardinality')}")

        # Validate connectors
        for conn in raw.get("connectors", []):
            if not conn.get("source"):
                errors.append("connector missing 'source' field")
            if not conn.get("entrypoint"):
                errors.append(f"connector '{conn.get('source', '?')}' missing 'entrypoint'")

        # Validate tools
        for tool in raw.get("tools", []):
            if not tool.get("name"):
                errors.append("tool missing 'name' field")
            if not tool.get("entrypoint"):
                errors.append(f"tool '{tool.get('name', '?')}' missing 'entrypoint'")

        # Validate tier
        tier = raw.get("tier", "free")
        if tier not in ("free", "premium"):
            errors.append(f"tier must be 'free' or 'premium', got: {tier}")

        return errors

    # ── Query ──────────────────────────────────────────────────────────

    def list_packs(self) -> list[dict]:
        """Return all installed packs with metadata. No connector code executed."""
        return [
            {
                "namespace": p.namespace,
                "name": p.name,
                "version": p.version,
                "tier": p.tier,
                "description": p.description,
                "kind_counts": {
                    "objectKinds": len(p.object_kinds),
                    "eventKinds": len(p.event_kinds),
                    "pointKinds": len(p.point_kinds),
                    "relations": len(p.relations),
                },
                "connector_count": len(p.connectors),
                "tool_count": len(p.tools),
            }
            for p in self.packs.values()
        ]

    def list_all_kinds(self) -> dict[str, list[str]]:
        """Return all registered kinds across all packs, keyed by kind field."""
        result: dict[str, list[str]] = {
            "objectKinds": [], "eventKinds": [], "pointKinds": [],
            "documentKinds": [], "actionKinds": [],
        }
        for p in self.packs.values():
            ns = p.namespace
            result["objectKinds"].extend(f"{ns}:{k}" for k in p.object_kinds)
            result["eventKinds"].extend(f"{ns}:{k}" for k in p.event_kinds)
            result["pointKinds"].extend(f"{ns}:{k}" for k in p.point_kinds)
            result["documentKinds"].extend(f"{ns}:{k}" for k in p.document_kinds)
            result["actionKinds"].extend(f"{ns}:{k}" for k in p.action_kinds)
        return result

    def get_pack(self, namespace: str) -> PackManifest | None:
        """Get a pack by namespace."""
        return self.packs.get(namespace)

    def list_tools(self) -> list[dict]:
        """Return all tools across all packs with their schemas."""
        tools: list[dict] = []
        for p in self.packs.values():
            for t in p.tools:
                tools.append({
                    "pack": p.namespace,
                    "name": t.get("name"),
                    "description": t.get("description"),
                    "entrypoint": t.get("entrypoint"),
                    "params": t.get("params", {}),
                })
        return tools

    def list_connectors(self) -> list[dict]:
        """Return all connectors with their config requirements."""
        connectors: list[dict] = []
        for p in self.packs.values():
            for c in p.connectors:
                connectors.append({
                    "pack": p.namespace,
                    "source": c.get("source"),
                    "sourceKind": c.get("sourceKind"),
                    "entrypoint": c.get("entrypoint"),
                    "required_config": c.get("config", {}).get("required", []),
                })
        return connectors

    # ── Register (idempotent) ──────────────────────────────────────────

    def register_kinds(self) -> dict[str, int]:
        """Register all pack kinds into the global ontology. Idempotent.

        Returns counts of kinds registered. Safe to call multiple times —
        duplicate registrations are skipped.
        """
        counts = {"registered": 0, "skipped": 0}
        for p in self.packs.values():
            ns = p.namespace
            # Registration is currently in-memory (the registry IS the catalog).
            # When FalkorDB-backed registration is needed, this method will
            # create nodes/edges for each kind.
            # For now, list_all_kinds() is the registration artifact.
            counts["registered"] += (
                len(p.object_kinds) + len(p.event_kinds) + len(p.point_kinds)
                + len(p.document_kinds) + len(p.action_kinds)
            )
        return counts


# ── Self-check ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    packs_dir = Path(__file__).resolve().parent.parent / "packs"
    registry = PackRegistry(packs_dir)
    loaded = registry.load_all()

    print(f"Loaded {loaded} pack(s)")
    if registry.errors:
        print(f"Errors: {registry.errors}")
        sys.exit(1)

    print("\nPacks:")
    for p in registry.list_packs():
        print(f"  {p['namespace']}: {p['name']} v{p['version']} ({p['tier']})")

    print("\nAll kinds:")
    for field, kinds in registry.list_all_kinds().items():
        if kinds:
            print(f"  {field}: {', '.join(kinds)}")

    print("\nTools:")
    for t in registry.list_tools():
        print(f"  {t['pack']}/{t['name']}: {t['description']}")

    print("\nOK")
