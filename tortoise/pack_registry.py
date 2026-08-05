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
from pathlib import Path
from typing import Any

import yaml

# ── Canonical kind vocabularies (ONTOLOGY_v2.5 §1.1) ──────────────────────

CANONICAL_OBJECT_KINDS = frozenset({
    # Core work concepts
    "Project", "WorkItem",
    # Universal
    "document", "user", "skill", "tool", "agent",
    "workflow", "agreement", "standard", "other",
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

# DEPRECATED (#122): CANONICAL_ACTION_KINDS is no longer canonical.
# Action kinds are still valid in pack manifests but the centralized 17-value
# frozenset is unnecessary — packs declare their own actionKinds.
# Kept for backward compatibility; will be removed in a future version.
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

VALID_PARAM_TYPES = frozenset({
    "string", "integer", "number", "boolean", "array", "object",
})

CANONICAL_KINDS = {
    "objectKinds": CANONICAL_OBJECT_KINDS,
    "eventKinds": CANONICAL_EVENT_KINDS,
    "pointKinds": CANONICAL_POINT_KINDS,
    "documentKinds": CANONICAL_DOCUMENT_KINDS,
    # "actionKinds": CANONICAL_ACTION_KINDS,  # DEPRECATED (#122)
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

    # Subclass declarations: {kind: parent_kind} — e.g., {"epic": "Project"}
    # Parent must exist in core CANONICAL_OBJECT_KINDS or another pack's kinds.
    kind_subclasses: dict[str, str] = dataclasses.field(default_factory=dict)

    # Equivalence declarations: {kind: [other_ns:kind, ...]} — e.g., {"issue": ["pm:task"]}
    # Means "this pack's 'issue' is the same concept as 'pm:task'."
    # Bidirectional: the registry makes it symmetric at query time.
    kind_equivalences: dict[str, list[str]] = dataclasses.field(default_factory=dict)

    # Relations: edge schema declarations
    # New format (preferred): {predicate, fromKind, toKind, mechanism, cardinality?}
    #   - predicate: domain verb — "addresses", "competesWith", "implements"
    #   - fromKind/toKind: specific pack kinds — "product-strategy:feature"
    #   - mechanism: IMPL or NAND — epistemic mechanism
    #   - cardinality: optional (one_to_one, one_to_many, many_to_one, many_to_many)
    # Old format (compat): {predicate, from, to, cardinality?}
    #   - from/to: entity types — "Object", "Subject"

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
        self._build_kind_expansions()
        self._validate_cross_pack_refs()
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
            kind_subclasses=ont.get("subclassOf", {}),
            kind_equivalences=ont.get("equivalentTo", {}),
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
        if ns and ":" in ns:
            errors.append("namespace must not contain ':'")

        ont = raw.get("ontology", {})

        # Validate ontology section
        if ont:
            # extends: core is a marker — no actual 'core' manifest file exists.
            # Validation is against CANONICAL_OBJECT_KINDS (see subclassOf checks).
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

            # Collect all pack-declared kinds (used by relation + subclassOf validation)
            all_pack_kinds: set[str] = set()
            for kf in ["objectKinds", "eventKinds", "pointKinds",
                        "documentKinds", "actionKinds"]:
                all_pack_kinds.update(ont.get(kf, []))

            # Pre-built core kind set for relation validation
            ALL_CORE_KINDS = (CANONICAL_OBJECT_KINDS | CANONICAL_EVENT_KINDS |
                              CANONICAL_POINT_KINDS | CANONICAL_DOCUMENT_KINDS |
                              CANONICAL_ACTION_KINDS |
                              {"Subject", "Object", "Action", "Event",
                               "Point", "Document", "Source",
                               "Project", "WorkItem"})

            # Validate relations (new format: fromKind/toKind/mechanism)
            for rel in ont.get("relations", []):
                pred = rel.get("predicate", "")
                if not pred or not pred[0].islower():
                    errors.append(f"relation predicate '{pred}' must be camelCase")

                # New format: fromKind/toKind (specific pack kinds)
                if "fromKind" in rel or "toKind" in rel:
                    if "fromKind" not in rel or "toKind" not in rel:
                        errors.append(
                            f"relation '{pred}': both fromKind and toKind required"
                        )
                    mechanism = rel.get("mechanism", "")
                    if mechanism not in ("IMPL", "NAND"):
                        errors.append(
                            f"relation '{pred}': mechanism must be IMPL or NAND, "
                            f"got {mechanism!r}"
                        )
                    # Referential integrity: fromKind/toKind must reference
                    # existing kinds (core CANONICAL or this pack's own kinds).
                    # Cross-pack refs validated in load_all() after all packs loaded.
                    for direction, kind_val in [("fromKind", rel.get("fromKind", "")),
                                                 ("toKind", rel.get("toKind", ""))]:
                        if not kind_val:
                            continue  # already caught above
                        if kind_val[0].isupper():
                            # Core PascalCase kind — must be in canonical vocab
                            if kind_val not in ALL_CORE_KINDS:
                                errors.append(
                                    f"relation '{pred}': {direction} '{kind_val}' is not "
                                    f"a known core kind"
                                )
                        elif ":" in kind_val:
                            # Prefixed kind: namespace:localKind
                            ref_ns, ref_kind = kind_val.split(":", 1)
                            if not ref_ns or not ref_kind:
                                errors.append(
                                    f"relation '{pred}': {direction} '{kind_val}' "
                                    f"has empty namespace or kind"
                                )
                            elif ref_ns == ns:
                                # Self-reference — must be in this pack's kinds
                                if ref_kind not in all_pack_kinds:
                                    errors.append(
                                        f"relation '{pred}': {direction} '{kind_val}' "
                                        f"references '{ref_kind}' which is not "
                                        f"declared in this pack"
                                    )
                            # Cross-pack refs (ref_ns != ns): validate in load_all()
                        else:
                            # Unprefixed camelCase — not valid in new format
                            errors.append(
                                f"relation '{pred}': {direction} '{kind_val}' "
                                f"must be a core PascalCase kind or 'namespace:kind'"
                            )
                else:
                    # Old format: from/to (entity types) — backward compat
                    if rel.get("from") not in VALID_ENTITY_TYPES:
                        errors.append(
                            f"relation '{pred}': invalid 'from' type: {rel.get('from')}"
                        )
                    if rel.get("to") not in VALID_ENTITY_TYPES:
                        errors.append(
                            f"relation '{pred}': invalid 'to' type: {rel.get('to')}"
                        )

                if rel.get("cardinality") and rel["cardinality"] not in VALID_CARDINALITIES:
                    errors.append(
                        f"relation '{pred}': invalid cardinality: {rel.get('cardinality')}"
                    )

            # Validate hierarchies
            hierarchies = ont.get("hierarchies", [])
            if not isinstance(hierarchies, list):
                errors.append("ontology.hierarchies must be a list")
            else:
                for h in hierarchies:
                    if not isinstance(h, dict):
                        errors.append(
                            f"hierarchies: expected dict, got {type(h).__name__}"
                        )
                        continue
                    if not h.get("path"):
                        errors.append("hierarchies: entry missing 'path' field")
                    if not h.get("type"):
                        errors.append("hierarchies: entry missing 'type' field")

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
            for pname, pspec in tool.get("params", {}).items():
                ptype = pspec.get("type", "") if isinstance(pspec, dict) else ""
                if ptype and ptype not in VALID_PARAM_TYPES:
                    errors.append(
                        f"tool '{tool.get('name', '?')}' param '{pname}': "
                        f"invalid type '{ptype}' (must be one of: "
                        f"{', '.join(sorted(VALID_PARAM_TYPES))})"
                    )

        # Validate tier
        tier = raw.get("tier", "free")
        if tier not in ("free", "premium"):
            errors.append(f"tier must be 'free' or 'premium', got: {tier}")

        # Validate subclassOf declarations
        subclass_of = ont.get("subclassOf", {})
        if not isinstance(subclass_of, dict):
            errors.append("ontology.subclassOf must be a dict")
        else:
            for child, parent in subclass_of.items():
                if child not in all_pack_kinds:
                    errors.append(
                        f"subclassOf: '{child}' is not declared in this pack's kinds"
                    )
                if not parent or not parent[0].isupper():
                    errors.append(
                        f"subclassOf: parent kind '{parent}' must be PascalCase "
                        f"(core kinds use PascalCase)"
                    )
                # Check parent exists in core object kinds or core concepts
                core_parents = CANONICAL_OBJECT_KINDS | {"Project", "WorkItem",
                    "Document", "Subject", "Object", "Action", "Event", "Point", "Source"}
                if parent not in core_parents:
                    errors.append(
                        f"subclassOf: parent kind '{parent}' not found in core "
                        f"ontology. Must be a core entity type or core objectKind."
                    )

        # Validate equivalentTo declarations
        equiv_to = ont.get("equivalentTo", {})
        if not isinstance(equiv_to, dict):
            errors.append("ontology.equivalentTo must be a dict")
        else:
            for kind, targets in equiv_to.items():
                if kind not in all_pack_kinds:
                    errors.append(
                        f"equivalentTo: '{kind}' is not declared in this pack's kinds"
                    )
                if not isinstance(targets, list):
                    errors.append(
                        f"equivalentTo: value for '{kind}' must be a list of "
                        f"'namespace:kind' strings"
                    )
                    continue
                for target in targets:
                    if ":" not in target:
                        errors.append(
                            f"equivalentTo: '{target}' must use 'namespace:kind' format"
                        )

        return errors

    # ── Cross-pack validation ──────────────────────────────────────────

    def _validate_cross_pack_refs(self) -> None:
        """Validate that all kind references resolve across packs.

        Called after load_all() populates all packs. Checks:
          1. Every relation's fromKind/toKind values reference known kinds.
          2. Every equivalentTo target references a kind in another loaded pack.
        Adds errors to self.errors keyed by pack namespace.
        """
        # Build the set of all known kinds (core + all packs)
        all_known: set[str] = set()

        # Core entity types and core kinds
        all_known |= {"Subject", "Object", "Action", "Event",
                       "Point", "Document", "Source",
                       "Project", "WorkItem"}
        all_known |= CANONICAL_OBJECT_KINDS
        all_known |= CANONICAL_EVENT_KINDS
        all_known |= CANONICAL_POINT_KINDS
        all_known |= CANONICAL_DOCUMENT_KINDS
        all_known |= CANONICAL_ACTION_KINDS

        # All pack-prefixed kinds
        for ns, pack in self.packs.items():
            for kind in pack.object_kinds:
                all_known.add(f"{ns}:{kind}")
            for kind in pack.event_kinds:
                all_known.add(f"{ns}:{kind}")
            for kind in pack.point_kinds:
                all_known.add(f"{ns}:{kind}")
            for kind in pack.document_kinds:
                all_known.add(f"{ns}:{kind}")
            for kind in pack.action_kinds:
                all_known.add(f"{ns}:{kind}")

        # Check every relation's fromKind/toKind
        for ns, pack in self.packs.items():
            for rel in pack.relations:
                pred = rel.get("predicate", "?")
                for direction in ("fromKind", "toKind"):
                    kind_val = rel.get(direction, "")
                    if not kind_val:
                        continue  # old-format, already validated
                    if kind_val not in all_known:
                        msg = (
                            f"relation '{pred}': {direction} '{kind_val}' "
                            f"does not resolve to any known kind "
                            f"(core or loaded pack)"
                        )
                        self.errors.setdefault(ns, []).append(msg)

            # Check every equivalentTo target resolves
            for kind, targets in pack.kind_equivalences.items():
                for target in targets:
                    if target not in all_known:
                        msg = (
                            f"equivalentTo: target '{target}' (for '{kind}') "
                            f"does not resolve to any known kind "
                            f"(core or loaded pack)"
                        )
                        self.errors.setdefault(ns, []).append(msg)

    # Keep backward-compatible alias
    _validate_cross_pack_relations = _validate_cross_pack_refs

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

    def get_subclasses(self, parent_kind: str) -> list[str]:
        """Return all pack kinds that are subclasses of parent_kind.

        Example: get_subclasses("Project") → ["dev:epic"]
        """
        result = []
        for p in self.packs.values():
            ns = p.namespace
            for child, parent in p.kind_subclasses.items():
                if parent == parent_kind:
                    result.append(f"{ns}:{child}")
        return result

    def expand_kind(self, kind: str) -> list[str]:
        """Expand a kind to include subclasses and equivalents.

        Returns list of kind strings for Cypher IN clause.
        Example: expand_kind("WorkItem") → ["WorkItem", "dev:issue", "pm:task"]

        Pre-computed at load_all() time, O(1) dict lookup at query time.
        """
        if not hasattr(self, '_kind_expansions'):
            self._build_kind_expansions()
        return self._kind_expansions.get(kind, [kind])

    def _build_kind_expansions(self) -> None:
        """Build the pre-computed kind expansion table.

        Called once after load_all(). Maps each kind to [self] + subclasses +
        bidirectional equivalents.
        """
        expansions: dict[str, list[str]] = {}

        # Core kinds: expand to [self] + all pack subclasses (all 5 categories)
        all_canonical: list[str] = []
        for kind_set in (CANONICAL_OBJECT_KINDS, CANONICAL_POINT_KINDS,
                          CANONICAL_EVENT_KINDS, CANONICAL_DOCUMENT_KINDS,
                          CANONICAL_ACTION_KINDS):
            all_canonical.extend(kind_set)
        for parent in all_canonical:
            subs = self.get_subclasses(parent)
            expansions[parent] = [parent] + subs

        # Pack kinds: each maps to [self] initially (all 5 categories)
        for p in self.packs.values():
            ns = p.namespace
            for kind_list in (p.object_kinds, p.point_kinds, p.event_kinds,
                               p.document_kinds, p.action_kinds):
                for kind in kind_list:
                    full = f"{ns}:{kind}"
                    expansions[full] = [full]

        # Apply subclassOf: children also expand to parent
        for p in self.packs.values():
            ns = p.namespace
            for child, parent in p.kind_subclasses.items():
                full_child = f"{ns}:{child}"
                if full_child not in expansions:
                    expansions[full_child] = [full_child]

        # Apply equivalentTo: bidirectional
        for p in self.packs.values():
            ns = p.namespace
            for kind, targets in p.kind_equivalences.items():
                full_kind = f"{ns}:{kind}"
                for target in targets:
                    # Add target to this kind's expansion
                    if full_kind in expansions:
                        if target not in expansions[full_kind]:
                            expansions[full_kind].append(target)
                    # Add this kind to target's expansion (bidirectional)
                    if target not in expansions:
                        expansions[target] = [target]
                    if full_kind not in expansions[target]:
                        expansions[target].append(full_kind)

        # Bare-kind resolution: a bare kind name (e.g., "useCase") maps to all
        # pack-prefixed forms (e.g., "product-strategy:useCase"). The bare form
        # itself is omitted unless it's a canonical core kind — migrate_kinds
        # converts bare- → prefixed before queries.
        bare_to_full: dict[str, list[str]] = {}
        for full in expansions:
            if ":" in full:
                _ns, _kind = full.split(":", 1)
                bare_to_full.setdefault(_kind, []).append(full)
        for bare, fulls in bare_to_full.items():
            if bare in expansions:
                for f in fulls:
                    if f not in expansions[bare]:
                        expansions[bare].append(f)
            else:
                expansions[bare] = fulls

        self._kind_expansions = expansions

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

    def list_relations(self) -> list[dict]:
        """Return all relation declarations across packs with mechanisms.

        Example: [{"pack": "product-strategy", "predicate": "addresses",
                   "fromKind": "product-strategy:feature",
                   "toKind": "product-strategy:customerNeed",
                   "mechanism": "IMPL"}]
        """
        relations: list[dict] = []
        for p in self.packs.values():
            ns = p.namespace
            for r in p.relations:
                entry = {"pack": ns, "predicate": r.get("predicate")}
                # New format
                if "fromKind" in r:
                    entry["fromKind"] = r["fromKind"]
                    entry["toKind"] = r["toKind"]
                    entry["mechanism"] = r.get("mechanism", "IMPL")
                # Old format (backward compat)
                else:
                    entry["from"] = r.get("from")
                    entry["to"] = r.get("to")
                if r.get("cardinality"):
                    entry["cardinality"] = r["cardinality"]
                relations.append(entry)
        return relations

    # ── Register (idempotent) ──────────────────────────────────────────

    def register_kinds(self) -> dict[str, int]:
        """Register all pack kinds into the global ontology. Idempotent.

        Returns counts of kinds registered. Safe to call multiple times —
        duplicate registrations are skipped.
        """
        counts = {"registered": 0, "skipped": 0}
        seen: set[str] = set()
        for p in self.packs.values():
            ns = p.namespace
            for kind_list in [p.object_kinds, p.event_kinds, p.point_kinds,
                               p.document_kinds, p.action_kinds]:
                for k in kind_list:
                    key = f"{ns}:{k}"
                    if key in seen:
                        counts["skipped"] += 1
                    else:
                        seen.add(key)
                        counts["registered"] += 1
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

    # ── Error path tests ──
    errors = registry._validate({"name": "MissingNamespace"})
    assert errors, f"Expected errors for missing namespace, got none"
    print("  ✓ missing namespace detected")

    errors = registry._validate({"namespace": "bad:ns", "name": "X",
                                 "ontology": {"extends": "core"}})
    assert errors, f"Expected errors for colon in namespace"
    print("  ✓ namespace colon rejected")

    errors = registry._validate({"namespace": "x", "name": "X",
                                 "ontology": {"extends": "core",
                                              "eventKinds": ["BadCase"]}})
    assert any("camelCase" in e for e in errors), f"Expected camelCase error: {errors}"
    print("  ✓ camelCase enforced")

    errors = registry._validate({"namespace": "x", "name": "X",
                                 "ontology": {"extends": "core",
                                              "objectKinds": ["document"]}})
    assert any("canonical" in e for e in errors), f"Expected canonical error: {errors}"
    print("  ✓ canonical conflict detected")

    errors = registry._validate({"namespace": "x", "name": "X",
                                 "ontology": {"extends": "core"},
                                 "tools": [{"name": "t", "entrypoint": "f",
                                             "params": {"p": {"type": "badtype"}}}]})
    assert any("invalid type" in e for e in errors), f"Expected param type error: {errors}"
    print("  ✓ invalid tool param type detected")

    errors = registry._validate({"namespace": "x", "name": "X",
                                 "ontology": {"extends": "core"},
                                 "connectors": [{"source": "gh"}]})
    assert any("entrypoint" in e for e in errors), f"Expected entrypoint error: {errors}"
    print("  ✓ missing connector entrypoint detected")

    errors = registry._validate({"namespace": "x", "name": "X", "tier": "enterprise"})
    assert any("tier" in e for e in errors), f"Expected tier error: {errors}"
    print("  ✓ invalid tier rejected")

    print("\nAll validation checks passed")
