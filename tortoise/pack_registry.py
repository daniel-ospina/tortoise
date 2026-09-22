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
import logging
import os
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

# Warn-once sentinel (epic #1891 WF-2, #1930): fallback/isolation warnings on
# the resolution hot path must fire once per key, not per call — keyed by
# (category, value...). Cleared per test via tests/conftest.py so caplog
# assertions are deterministic.
_WARN_ONCE: set[tuple] = set()


def _warn_packs_once(key: tuple, msg: str, *args) -> None:
    """Log a warning at most once per ``key`` (module-level sentinel)."""
    if key in _WARN_ONCE:
        return
    _WARN_ONCE.add(key)
    log.warning(msg, *args)

# ── Canonical kind vocabularies (ONTOLOGY_v2.5 §1.1) ──────────────────────

CANONICAL_OBJECT_KINDS = frozenset({
    # Core work concepts
    "Project", "WorkItem", "Problem",  # Problem: deviation from desired state — problem-family parent
    # Universal
    "document", "user", "skill", "tool", "agent",
    "workflow", "agreement", "standard", "other",
})

CANONICAL_EVENT_KINDS = frozenset({
    "meeting", "decision", "experiment", "deployment", "review",
    "friction", "extraction", "documentCreated", "roleCreated", "pointAdded",
    "sessionCaptured",  # #125 metadata-only session capture
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

# ── Manifest v3 vocabulary (research-r6 §3, epic #909) ────────────────────

# Core entity types (ONTOLOGY §1) — the five conceptual nodes plus Object/
# Action. Valid subclassOf parents AND expansion roots (R6 §6.2a: named
# Document/Source/Subject/Point/Event; Object/Action included so every parent
# the validator accepts also expands).
# Alias of VALID_ENTITY_TYPES (review P2, PR #978) — one source of truth.
CORE_ENTITY_TYPES = VALID_ENTITY_TYPES

# Everything a bare kind reference may resolve to without a namespace.
CORE_KINDS = (
    CANONICAL_OBJECT_KINDS | CANONICAL_EVENT_KINDS | CANONICAL_POINT_KINDS
    | CANONICAL_DOCUMENT_KINDS | CANONICAL_ACTION_KINDS | CORE_ENTITY_TYPES
)

# kindDefs value keys (R6 §3.1/§3.5)
VALID_KINDDEF_KEYS = frozenset({
    "description", "synonyms", "examples", "nearMisses",
    "extractable", "storeAs", "enforcement",
})

# storeAs values — what stream bucket a kind belongs to (R6 §3.1)
VALID_STORE_AS = frozenset({"claim", "decision", "entity", "tag", "event"})

# Enforcement ladder (R6 §5.3): warn | retry | block
VALID_ENFORCEMENT_LEVELS = frozenset({"warn", "retry", "block"})

# pointKinds whose inferred storeAs is `decision` (R6 §3.1)
DECISION_POINT_KINDS = frozenset({
    "decision", "vision", "strategy", "plan", "goal", "target", "humanApproval",
})

# sourceKind vocabulary (ONTOLOGY §4.6) — extraction.sourceTypes must be ⊂ this
# (R6 §6.1#3: v1 restrict to conversation/document + allowlist of the
# connector-registered source kinds).
KNOWN_SOURCE_TYPES = frozenset({
    "conversation", "document",
    "github_issue", "slack_message", "linear_card",
})

# Escape hatch for future connectors (R6 §3.5): packs may reference these
# without a registry release. Anything outside KNOWN_SOURCE_TYPES ∪
# SOURCE_TYPE_ESCAPE_HATCH is a validation error (typo protection).
SOURCE_TYPE_ESCAPE_HATCH = frozenset({
    "email", "webpage", "discord_message", "notion_page",
})

# Core mechanism predicates (S3 pipeline emits IMPL/NAND; MITIGATES for
# mitigations) — valid chain-edge / enforcement targets without a pack relation.
CORE_PREDICATES = frozenset({"IMPL", "NAND", "MITIGATES"})


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

    # Manifest v3 (R6 §3.1): per-kind extractor metadata, keyed by pack-local
    # kind name. Allowed value keys: description, synonyms, examples,
    # nearMisses, extractable, storeAs, enforcement.
    kind_defs: dict[str, dict] = dataclasses.field(default_factory=dict)

    # Manifest v3 (R6 §3.2): first-class business-logic chains.
    # [{id, name?, description?, steps[], enforcement?, edges?}]
    chains: list[dict] = dataclasses.field(default_factory=list)

    # Manifest v3 (R6 §3.4): extraction activation + enforcement config,
    # normalized — always present with defaults (backward compat: absent in
    # v2 manifests → active: true, sourceTypes: [], enforcement default warn).
    extraction: dict = dataclasses.field(
        default_factory=lambda: {
            "active": True,
            "sourceTypes": [],
            "enforcement": {
                "default": "warn",
                "kinds": {},
                "relations": {},
                "chains": {},
            },
        }
    )

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

    # ── Manifest v3 read-side helpers (consumed by the value-brief compiler) ──

    def is_extractable(self, kind: str) -> bool:
        """kindDefs[].extractable — default True (R6 §3.1)."""
        kd = self.kind_defs.get(kind, {})
        if isinstance(kd, dict) and "extractable" in kd:
            return bool(kd["extractable"])
        return True

    def relation_is_extractable(self, rel: dict) -> bool:
        """relations[].extractable — default False (R6 §3.3)."""
        return bool(rel.get("extractable", False))

    def store_as(self, kind: str) -> str:
        """Resolved storeAs for a pack-local kind (R6 §3.1).

        Explicit kindDefs[].storeAs wins; otherwise inferred from list
        placement: pointKinds → claim (decision/vision/strategy/plan/goal/
        target/humanApproval → decision), objectKinds/documentKinds → entity,
        eventKinds → event.
        """
        kd = self.kind_defs.get(kind, {})
        if isinstance(kd, dict) and kd.get("storeAs"):
            return kd["storeAs"]
        if kind in self.point_kinds:
            return "decision" if kind in DECISION_POINT_KINDS else "claim"
        if kind in self.event_kinds:
            return "event"
        if kind in self.document_kinds or kind in self.object_kinds:
            return "entity"
        return "claim"

    def enforcement_for(self, kind: str) -> str:
        """Kind-level enforcement resolution order (R6 §3.4):
        kindDefs[].enforcement → extraction.enforcement.kinds → default → warn."""
        kd = self.kind_defs.get(kind, {})
        if isinstance(kd, dict) and kd.get("enforcement"):
            return kd["enforcement"]
        kinds_cfg = self.extraction["enforcement"].get("kinds", {})
        if kind in kinds_cfg:
            return kinds_cfg[kind]
        return self.extraction["enforcement"].get("default", "warn")

    def enforcement_for_relation(self, predicate: str) -> str:
        """Relation-level enforcement: extraction.enforcement.relations → default → warn."""
        rel_cfg = self.extraction["enforcement"].get("relations", {})
        return rel_cfg.get(predicate, self.extraction["enforcement"].get("default", "warn"))

    def enforcement_for_chain(self, chain_id: str) -> str:
        """Chain-level enforcement: chain.enforcement → extraction.enforcement.chains
        → default → warn (R6 §3.2/§3.4)."""
        for chain in self.chains:
            if chain.get("id") == chain_id and chain.get("enforcement"):
                return chain["enforcement"]
        chains_cfg = self.extraction["enforcement"].get("chains", {})
        return chains_cfg.get(chain_id, self.extraction["enforcement"].get("default", "warn"))

    def is_active_for(self, source_type: str) -> bool:
        """§3.4 activation: active AND (sourceTypes empty or contains source_type)."""
        if not self.extraction.get("active", True):
            return False
        source_types = self.extraction.get("sourceTypes") or []
        return not source_types or source_type in source_types


# ── Registry ──────────────────────────────────────────────────────────────


def env_packs_dir_value() -> str:
    """Raw stripped ``TORTOISE_PACKS_DIR`` value ('' when unset/blank).

    Blank/whitespace is treated as unset (config.py precedent for
    ``TORTOISE_DB_PATH``) — a blank value must never resolve to the process
    CWD (``Path('') == Path('.')``).
    """
    return os.environ.get("TORTOISE_PACKS_DIR", "").strip()


def _has_loadable_manifests(d: Path) -> bool:
    """True when the dir holds at least one loadable pack manifest.

    Applies the SAME namespace skip rules as ``PackRegistry.load_all``
    (``_``/``.``-prefixed dirs are scaffolding — e.g. ``packs/_template/``)
    so the resolver's emptiness definition can never diverge from the
    loader's: a dir with only ``_template/manifest.yaml`` is EMPTY and must
    fall through, never resolve (the silent-empty G1 class).
    """
    return any(
        m.is_file()
        for child in d.glob("*")
        if not child.name.startswith(("_", "."))
        for m in child.glob("manifest.yaml")
    )


def env_packs_dir() -> Path | None:
    """Resolve the ``TORTOISE_PACKS_DIR`` env leg (#1930, epic #1891 WF-2).

    Returns the dir when set+valid (exists, is a directory, holds ≥1 loadable
    manifest). Returns ``None`` when unset/blank (no warning) or when set but
    missing / not-a-dir / empty (logged warning, warn-once per value — the
    caller falls back to ``default_packs_dir()``). A set-but-broken value
    must never silently resolve to an empty registry (G1 class): the warning
    is the operator's diagnostic; ``registry.errors`` (and the all-broken
    fallback in ``domain_loader._get_registry``) cover manifest-level
    failures.
    """
    val = env_packs_dir_value()
    if not val:
        return None
    env_dir = Path(val).expanduser()
    if not env_dir.exists():
        _warn_packs_once(
            ("env-dir", val, "missing"),
            "TORTOISE_PACKS_DIR=%r does not exist — falling back to the "
            "default pack catalog", val)
        return None
    if not env_dir.is_dir():
        _warn_packs_once(
            ("env-dir", val, "not-a-dir"),
            "TORTOISE_PACKS_DIR=%r is not a directory — falling back to the "
            "default pack catalog", val)
        return None
    if not _has_loadable_manifests(env_dir):
        _warn_packs_once(
            ("env-dir", val, "empty"),
            "TORTOISE_PACKS_DIR=%r contains no pack manifests — falling back "
            "to the default pack catalog", val)
        return None
    return env_dir


def default_packs_dir(*, _skip_env: bool = False) -> Path:
    """Resolve the default packs directory (env → packaged → repo root).

    The resolution order (epic #1891 plan §5, WF-2; #1930):

    0. ``TORTOISE_PACKS_DIR`` (env) — self-host custom pack directory, when
       set+valid (exists + holds ≥1 loadable manifest). Set-but-missing /
       not-a-dir / empty → warn-once + fall through (never silent empty).
    1. Packaged default — ``<package>/packs`` (``site-packages/tortoise/packs``
       on a wheel install, where package-data ships the catalog). Chosen ONLY
       when it actually contains loadable manifests (same skip rules as
       ``load_all`` — ``_template`` alone is not a catalog): the source tree's
       ``tortoise/packs/`` holds the discovery stub (no yamls), so in dev and
       in the Docker build context this leg falls through.
    2. Repo root — ``<package>/../packs`` (dev/editable installs, and the
       Docker image's ``COPY packs/ packs/`` at ``/app``).

    All registry consumers resolve the catalog through this single primitive
    (previously each hardcoded ``Path(__file__).parent.parent / "packs"`` —
    which on a wheel install resolves ``site-packages/packs`` → zero packs,
    the silent empty-registry defect G1, #1929).

    ``_skip_env`` is the internal escape hatch for the registry-level
    all-broken fallback (``domain_loader._get_registry``): it re-resolves the
    default chain WITHOUT the env leg after the env dir proved unusable. Pure
    kwarg — no env/global mutation, safe on concurrent callers.
    """
    if not _skip_env:
        env_dir = env_packs_dir()
        if env_dir is not None:
            return env_dir
    packaged = Path(__file__).resolve().parent / "packs"
    if _has_loadable_manifests(packaged):
        return packaged
    repo_root = Path(__file__).resolve().parent.parent / "packs"
    if packaged.is_dir() and not repo_root.exists():
        # A packaged layout that ships ZERO manifests (package-data /
        # MANIFEST.in regression) falls back to a nonexistent repo-root leg —
        # the silent empty-registry defect class (G1). Keep the fallback
        # (dev/Docker must not break) but say it out loud; the publish
        # smokes are the hard gate.
        _warn_packs_once(
            ("packaged-stub", str(packaged), str(repo_root)),
            "packaged packs dir %s contains no manifests and repo-root "
            "fallback %s does not exist — registry will load 0 packs",
            packaged, repo_root,
        )
    return repo_root


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
        # #1930: set by domain_loader._get_registry when the TORTOISE_PACKS_DIR
        # leg was unusable (0 valid packs / load failure) and the registry
        # fell back to the default catalog — a queryable diagnostic for ops.
        self.fallback_note: str | None = None

    # ── Load ──────────────────────────────────────────────────────────

    def load_all(self) -> int:
        """Load all packs/*/manifest.yaml. Returns count of successfully loaded packs.

        Per-pack load isolation (R-16, epic #909): a manifest that fails
        validation fails THAT pack only — the registry keeps loading every
        other pack, and any pack with a recorded error (load-time or
        cross-pack) is excluded from self.packs so the compiled vocabulary
        (expansions, brief, register_kinds) only ever sees healthy packs.
        Errors remain queryable via self.errors.
        """
        if not self.packs_dir.exists():
            return 0
        # Reset state up front (review P1, PR #978): a re-entry previously
        # accumulated stale errors and the isolation drop loop wiped every
        # pack that had ANY recorded error — including errors from an earlier
        # call. Reload must be idempotent.
        self.packs = {}
        self.errors = {}
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
        self._validate_cross_pack_refs()
        # R-16 isolation: drop packs that failed cross-pack validation so the
        # compiled registry only contains healthy packs.
        for ns in [ns for ns, pack in self.packs.items() if ns in self.errors]:
            del self.packs[ns]
            count -= 1
        # Fixpoint (review P2, PR #978): a pack whose cross-pack references
        # pointed at a now-dropped pack must also be excluded — re-validate
        # against the surviving set so the compiled vocabulary never carries
        # dangling references.
        if self.errors:
            self._validate_cross_pack_refs()
            for ns in [ns for ns, pack in self.packs.items() if ns in self.errors]:
                del self.packs[ns]
                count -= 1
        self._build_kind_expansions()
        # #1930 (epic #1891 WF-2 / E2E-2): a pack isolated by R-16 must never
        # be silent — every consumer surfaces the startup warning (registry.errors
        # stays the supplementary diagnostic). Warn-once per (dir, error-set)
        # signature so repeated loads of the same broken dir do not spam.
        if self.errors:
            _warn_packs_once(
                ("load-errors", str(self.packs_dir), tuple(sorted(self.errors))),
                "pack registry %s: %d pack(s) failed validation and were "
                "isolated (see registry.errors): %s",
                self.packs_dir, len(self.errors), ", ".join(sorted(self.errors)),
            )
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
            kind_defs=ont.get("kindDefs", {}),
            chains=ont.get("chains", []),
            extraction=self._normalize_extraction(raw.get("extraction")),
            kind_subclasses=ont.get("subclassOf", {}),
            kind_equivalences=ont.get("equivalentTo", {}),
            connectors=raw.get("connectors", []),
            tools=raw.get("tools", []),
            depends_on=raw.get("depends_on", []),
        )

    @staticmethod
    def _normalize_extraction(ext: Any) -> dict:
        """Normalize the extraction section to its default shape (R6 §3.4).

        Absent in v2 manifests → active: true, no sourceTypes, warn default.
        """
        ext = ext or {}
        enforcement = ext.get("enforcement") or {}
        return {
            "active": ext.get("active", True),
            "sourceTypes": list(ext.get("sourceTypes") or []),
            "enforcement": {
                "default": enforcement.get("default", "warn"),
                "kinds": dict(enforcement.get("kinds") or {}),
                "relations": dict(enforcement.get("relations") or {}),
                "chains": dict(enforcement.get("chains") or {}),
            },
        }

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

        # Collect all pack-declared kinds (used by relation + subclassOf +
        # kindDefs + extraction validation). Empty when ontology is absent.
        all_pack_kinds: set[str] = set()
        seen_chain_ids: set[str] = set()

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

            # Collect all pack-declared kinds (used by relation, subclassOf,
            # kindDefs, and extraction validation)
            for kf in ["objectKinds", "eventKinds", "pointKinds",
                        "documentKinds", "actionKinds"]:
                all_pack_kinds.update(ont.get(kf, []))

            # Pre-built core kind set for relation validation
            ALL_CORE_KINDS = CORE_KINDS

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
                            elif ref_ns == ns:  # noqa: SIM102
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

                # Manifest v3 (R6 §3.3): extractable must be a boolean when present
                if "extractable" in rel and not isinstance(rel["extractable"], bool):
                    errors.append(
                        f"relation '{pred}': 'extractable' must be a boolean"
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

            # ── Manifest v3 (R6 §3.1/§3.5): kindDefs validation ──────────
            kind_defs = ont.get("kindDefs", {})
            if not isinstance(kind_defs, dict):
                errors.append("ontology.kindDefs must be a map keyed by kind name")
            else:
                for kind, spec in kind_defs.items():
                    if kind not in all_pack_kinds and kind not in CORE_KINDS:
                        errors.append(
                            f"kindDefs: '{kind}' is not declared in this pack's "
                            f"*Kinds lists (nor a core kind)"
                        )
                    if not isinstance(spec, dict):
                        errors.append(f"kindDefs: value for '{kind}' must be a map")
                        continue
                    for key in spec:
                        if key not in VALID_KINDDEF_KEYS:
                            errors.append(
                                f"kindDefs '{kind}': unknown key '{key}' (allowed: "
                                f"{', '.join(sorted(VALID_KINDDEF_KEYS))})"
                            )
                    if "description" in spec and not isinstance(spec["description"], str):
                        errors.append(
                            f"kindDefs '{kind}': 'description' must be a string "
                            f"(it is prompt material for the extractor, R6 §3.1)"
                        )
                    for list_field in ("synonyms", "examples", "nearMisses"):
                        if list_field in spec:
                            val = spec[list_field]
                            if (not isinstance(val, list)
                                    or not all(isinstance(x, str) and x for x in val)):
                                errors.append(
                                    f"kindDefs '{kind}': '{list_field}' must be a "
                                    f"list of non-empty strings"
                                )
                    if "extractable" in spec and not isinstance(spec["extractable"], bool):
                        errors.append(
                            f"kindDefs '{kind}': 'extractable' must be a boolean"
                        )
                    if ("storeAs" in spec
                            and spec["storeAs"] not in VALID_STORE_AS):
                        errors.append(
                            f"kindDefs '{kind}': 'storeAs' must be one of "
                            f"{', '.join(sorted(VALID_STORE_AS))}, "
                            f"got {spec['storeAs']!r}"
                        )
                    if ("enforcement" in spec
                            and spec["enforcement"] not in VALID_ENFORCEMENT_LEVELS):
                        errors.append(
                            f"kindDefs '{kind}': 'enforcement' must be one of "
                            f"{', '.join(sorted(VALID_ENFORCEMENT_LEVELS))}, "
                            f"got {spec['enforcement']!r}"
                        )
                    # nearMisses targets resolve post-load (cross-pack pass)

            # ── Manifest v3 (R6 §3.2/§3.5): chains validation ────────────
            chains = ont.get("chains", [])
            if not isinstance(chains, list):
                errors.append("ontology.chains must be a list")
            else:
                for i, chain in enumerate(chains):
                    if not isinstance(chain, dict):
                        errors.append(f"chains[{i}]: expected a map, got {type(chain).__name__}")
                        continue
                    cid = chain.get("id", "")
                    if not isinstance(cid, str) or not cid:
                        errors.append(f"chains[{i}]: missing required 'id'")
                    elif cid in seen_chain_ids:
                        errors.append(f"chains: duplicate id '{cid}'")
                    else:
                        seen_chain_ids.add(cid)
                    steps = chain.get("steps", [])
                    if (not isinstance(steps, list) or not steps
                            or not all(isinstance(s, str) and s for s in steps)):
                        errors.append(
                            f"chains: '{cid or i}': 'steps' must be a non-empty "
                            f"list of kind names"
                        )
                    if ("enforcement" in chain
                            and chain["enforcement"] not in VALID_ENFORCEMENT_LEVELS):
                        errors.append(
                            f"chains: '{cid or i}': 'enforcement' must be one of "
                            f"{', '.join(sorted(VALID_ENFORCEMENT_LEVELS))}"
                        )
                    if "edges" in chain:
                        edges = chain["edges"]
                        if not isinstance(edges, list) or not all(
                                isinstance(e, str) and e for e in edges):
                            errors.append(
                                f"chains: '{cid or i}': 'edges' must be a list of "
                                f"predicate names"
                            )
                        else:
                            declared_preds = {
                                r.get("predicate") for r in ont.get("relations", [])
                            }
                            for edge in edges:
                                if edge not in declared_preds and edge not in CORE_PREDICATES:
                                    errors.append(
                                        f"chains: '{cid or i}': edge '{edge}' is not a "
                                        f"declared relation predicate nor a core "
                                        f"predicate ({', '.join(sorted(CORE_PREDICATES))})"
                                    )
                    # steps resolve post-load (cross-pack pass)

        # ── Manifest v3 (R6 §3.4/§3.5): extraction validation (top-level) ──
        extraction = raw.get("extraction")
        if extraction is not None and not isinstance(extraction, dict):
            errors.append("extraction must be a map")
        else:
            extraction = extraction or {}
            if "active" in extraction and not isinstance(extraction["active"], bool):
                errors.append("extraction.active must be a boolean")
            source_types = extraction.get("sourceTypes", [])
            if not isinstance(source_types, list) or not all(
                    isinstance(s, str) and s for s in source_types):
                errors.append("extraction.sourceTypes must be a list of strings")
            else:
                for st in source_types:
                    if st not in KNOWN_SOURCE_TYPES and st not in SOURCE_TYPE_ESCAPE_HATCH:
                        errors.append(
                            f"extraction.sourceTypes: '{st}' is not a known source "
                            f"type (known: {', '.join(sorted(KNOWN_SOURCE_TYPES))}; "
                            f"escape hatch: {', '.join(sorted(SOURCE_TYPE_ESCAPE_HATCH))})"
                        )
            enforcement = extraction.get("enforcement", {})
            if not isinstance(enforcement, dict):
                errors.append("extraction.enforcement must be a map")
            else:
                for key in enforcement:
                    if key not in ("default", "kinds", "relations", "chains"):
                        errors.append(
                            f"extraction.enforcement: unknown key '{key}' "
                            f"(allowed: default, kinds, relations, chains)"
                        )
                if ("default" in enforcement
                        and enforcement["default"] not in VALID_ENFORCEMENT_LEVELS):
                    errors.append(
                        f"extraction.enforcement.default must be one of "
                        f"{', '.join(sorted(VALID_ENFORCEMENT_LEVELS))}, "
                        f"got {enforcement['default']!r}"
                    )
                # Per-key overrides: level validated; keys typo-protected.
                declared_preds = {
                    r.get("predicate") for r in ont.get("relations", [])
                } if ont else set()
                for section, valid_keys, label in (
                        ("kinds", all_pack_kinds | CORE_KINDS, "kind"),
                        ("relations", declared_preds | CORE_PREDICATES, "predicate"),
                        ("chains", seen_chain_ids, "chain id"),
                ):
                    if section not in enforcement:
                        continue
                    mapping = enforcement[section]
                    if not isinstance(mapping, dict):
                        errors.append(
                            f"extraction.enforcement.{section} must be a map"
                        )
                        continue
                    for key, level in mapping.items():
                        if level not in VALID_ENFORCEMENT_LEVELS:
                            errors.append(
                                f"extraction.enforcement.{section}: level for "
                                f"'{key}' must be one of "
                                f"{', '.join(sorted(VALID_ENFORCEMENT_LEVELS))}, "
                                f"got {level!r}"
                            )
                        if key not in valid_keys:
                            errors.append(
                                f"extraction.enforcement.{section}: '{key}' is not "
                                f"a declared {label}"
                            )

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
                core_parents = CANONICAL_OBJECT_KINDS | CORE_ENTITY_TYPES
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
          3. Every kindDefs nearMisses target resolves (R6 §3.5).
          4. Every chain step resolves — bare steps may be this pack's own
             kinds, core kinds, or kinds declared by exactly ONE other pack
             (ambiguous bare names are errors, R6 §3.2 / plan W-5 failure b).
        Adds errors to self.errors keyed by pack namespace.
        """
        # Build the set of all known kinds (core + all packs)
        all_known = self._all_known_kinds()

        # Bare-name index for single-namespace resolution (chain steps,
        # nearMisses): bare kind name → full 'ns:kind' forms.
        bare_index: dict[str, list[str]] = {}
        for full in all_known:
            if ":" in full:
                _ns, kind = full.split(":", 1)
                bare_index.setdefault(kind, []).append(full)

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

            # Manifest v3 (R6 §3.1/§3.5): every nearMisses target resolves
            pack_kinds = self._pack_kind_set(pack)
            for kind, kd in pack.kind_defs.items():
                for target in kd.get("nearMisses", []):
                    status = self._resolve_kind_ref(
                        target, pack_kinds, all_known, bare_index
                    )
                    if status == "unknown":
                        msg = (
                            f"kindDefs '{kind}': nearMisses target '{target}' "
                            f"does not resolve to any known kind "
                            f"(core or loaded pack)"
                        )
                        self.errors.setdefault(ns, []).append(msg)
                    elif status == "ambiguous":
                        msg = (
                            f"kindDefs '{kind}': nearMisses target '{target}' is "
                            f"ambiguous — declared by multiple packs; "
                            f"use 'namespace:kind'"
                        )
                        self.errors.setdefault(ns, []).append(msg)

            # Manifest v3 (R6 §3.2/§3.5): every chain step resolves
            for chain in pack.chains:
                cid = chain.get("id", "?")
                for step in chain.get("steps", []):
                    status = self._resolve_kind_ref(
                        step, pack_kinds, all_known, bare_index
                    )
                    if status == "unknown":
                        msg = (
                            f"chain '{cid}': step '{step}' does not resolve to "
                            f"any known kind (core or loaded pack)"
                        )
                        self.errors.setdefault(ns, []).append(msg)
                    elif status == "ambiguous":
                        msg = (
                            f"chain '{cid}': step '{step}' is ambiguous — "
                            f"declared by multiple packs; use 'namespace:kind'"
                        )
                        self.errors.setdefault(ns, []).append(msg)

    @staticmethod
    def _pack_kind_set(pack: PackManifest) -> set[str]:
        """All kinds a pack declares (all five categories)."""
        kinds: set[str] = set()
        for kind_list in (pack.object_kinds, pack.event_kinds, pack.point_kinds,
                          pack.document_kinds, pack.action_kinds):
            kinds.update(kind_list)
        return kinds

    def _all_known_kinds(self) -> set[str]:
        """Core kinds + every loaded pack's prefixed kinds."""
        all_known: set[str] = set(CORE_KINDS)
        for pack in self.packs.values():
            ns = pack.namespace
            for kind in self._pack_kind_set(pack):
                all_known.add(f"{ns}:{kind}")
        return all_known

    def _resolve_kind_ref(self, ref: str, pack_kinds: set[str],
                          all_known: set[str],
                          bare_index: dict[str, list[str]]) -> str:
        """Resolve a kind reference to "ok" | "unknown" | "ambiguous".

        Namespaced refs must be in all_known. Bare refs resolve against: the
        pack's own kinds, core kinds, then kinds declared by exactly one
        other pack (single-namespace); more than one match → ambiguous.
        """
        if ":" in ref:
            return "ok" if ref in all_known else "unknown"
        if ref in pack_kinds or ref in CORE_KINDS:
            return "ok"
        fulls = bare_index.get(ref, [])
        if len(fulls) == 1:
            return "ok"
        if not fulls:
            return "unknown"
        return "ambiguous"

    # Keep backward-compatible alias
    _validate_cross_pack_relations = _validate_cross_pack_refs

    # ── Query ──────────────────────────────────────────────────────────

    def pack_summaries(self) -> dict[str, dict]:
        """Stable shared-catalog read: namespace → {name, version, tier, description}.

        #318 (multi-tenant pack isolation): per-tenant activation records
        (pack_state.PackInstall) join against this catalog — the shared
        catalog stays READ-ONLY for tenants; only install-state is
        per-tenant. Keys are manifest namespaces (the catalog's canonical
        id — note the project-management pack dir declares namespace `pm`).
        Read-at-call-time contract: no caching here; callers resolve the
        catalog when they need it.
        """
        return {
            p.namespace: {
                "name": p.name,
                "version": p.version,
                "tier": p.tier,
                "description": p.description,
            }
            for p in self.packs.values()
        }

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
        # R6 §6.2a (#949): core ENTITY types are subclass parents too —
        # `architecture: Document` validated but did NOT expand because
        # Document/Source/Subject/Point/Event were not expansion roots.
        all_canonical.extend(CORE_ENTITY_TYPES)
        for parent in all_canonical:
            subs = self.get_subclasses(parent)
            expansions[parent] = [parent] + subs  # noqa: RUF005

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
            for child, parent in p.kind_subclasses.items():  # noqa: B007
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
                    if full_kind in expansions:  # noqa: SIM102
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
                    # Manifest v3 (R6 §3.3): extractable flag — default false
                    entry["extractable"] = r.get("extractable", False)
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

    packs_dir = default_packs_dir()
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
    assert errors, f"Expected errors for missing namespace, got none"  # noqa: F541
    print("  ✓ missing namespace detected")

    errors = registry._validate({"namespace": "bad:ns", "name": "X",
                                 "ontology": {"extends": "core"}})
    assert errors, f"Expected errors for colon in namespace"  # noqa: F541
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
