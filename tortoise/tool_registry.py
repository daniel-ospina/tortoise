"""Canonical tool registry — single source of truth for all Tortoise operations.

One ToolDefinition per SDK operation. Both MCP and REST surfaces derive their
registrations from this registry. HTTP_ALLOWED is derived — zero manual sync.

⛔ SURFACE APPROVAL MANDATE (#4282, owner ruling)
    This registry IS the MCP tool surface. You may NOT add, remove, or rename an
    entry without human approval. The surface is the contract every agent and
    customer integration is built on, so changing it materially affects customer
    outcomes. GET APPROVAL FROM DANIEL FIRST — repo `AGENTS.md` → "USER QUESTIONS"
    / "DECISION RELAY" — and record it in `docs/product/canonical-mcp-tools.md`
    before the PR; the next `tools/surface_manifest.py cut` copies it into
    `config/surface-manifest.yml` (the manifest is a committed snapshot, never a live
    reader). The full procedure is in
    `CONTRIBUTING.md` ("The MCP tool surface and public SDK methods cannot grow by
    accident").

    `tools/surface-guard.py` is a DRIFT control, NOT an approval gate: an expansion
    that updates this registry and `config/surface-manifest.yml` consistently
    PASSES it. It cannot tell an approved addition from an unapproved one —
    Daniel's review is what carries the approval.
"""
from __future__ import annotations  # noqa: I001

from dataclasses import dataclass, replace, field  # noqa: F401
from typing import Any, Callable, Optional  # noqa: UP035

from mcp.types import ToolAnnotations


@dataclass(frozen=True)
class RestSpec:
    """REST route metadata for SDK-backed operations."""
    method: str          # "GET" | "POST" | "DELETE"
    path: str            # "/v1/points" etc.
    request_model: type | None = None  # Pydantic request model
    response_model: type | None = None  # Pydantic response model
    status_code: int = 200


@dataclass(frozen=True)
class ToolDefinition:
    """One entry per SDK-exposed operation."""
    name: str                    # e.g. "tortoise_create_point"
    id: str                      # immutable stable identity — `name` may change; a
                                 # retired id is reserved once a ledger exists (#3883)
    description: str             # Docstring for MCP + OpenAPI
    # ⛔ THE ONE RISK DECLARATION (#4122, owner ruling 2026-10-21). Everything the
    #    caller is allowed to do and every MCP hint is DERIVED from this — see
    #    `RISK_TIERS` below — so a permission and a hint can never drift apart
    #    (they were two independent fields, which is the whole defect #4122 named).
    #
    #    OVERRIDES: NIST SP 800-53 AC-6's read-low / write-high default — a tool is
    #    classified by USER-PERCEIVED RISK, not by whether its handler writes, so a
    #    confined, idempotent, derived-value write is permissioned as read
    #    (`maintenance`).
    risk: str                    # read | maintenance | write | destructive | admin
    http_policy: bool            # True = exposed on HTTP surfaces
    sdk_method: str              # Attribute name on TortoiseSDK, e.g. "create_point"
    handler_override: Optional[Callable] = None  # For raw-Cypher REST ops  # noqa: UP045
    rest_spec: Optional[RestSpec] = None         # For SDK-backed REST ops  # noqa: UP045
    group: str = "memory"        # Curation group (#523): memory|reasoning|graph|sessions|sources|journal|admin|onboarding
    hosted_only: bool = False    # #1935: register ONLY on the hosted surface
                                 # (deployment-gated — e.g. tortoise_pack_install;
                                 # self-host uses filesystem packs dir + CLI)
    #: Retry safety of the OPERATION — deliberately NOT a risk statement, and so
    #: deliberately not fully derivable from `risk`: `tortoise_create_point` is a
    #: `write` yet idempotent (content-hash dedup), and `maintenance` is idempotent
    #: by default but NOT always (`tortoise_compute_confidence` re-derives from the
    #: value the previous call stored — measured 0.693315 -> 0.693320 on #4122).
    #: `None` → derived from the tier (`_RISK_HINTS`).
    idempotent: Optional[bool] = None  # noqa: UP045
    #: ONLY for a probe/test that must construct a DISAGREEING entry so the drift
    #: guards can be proven to fire. Never set on a registry entry (#4122).
    annotations_override: Optional[ToolAnnotations] = None  # noqa: UP045
    retired_use_instead: Optional[str] = None  # noqa: UP045  # #3883: non-None ONLY
                                 # on a retired entry — the call that replaces this name.

    def __post_init__(self) -> None:
        if self.risk not in RISK_TIERS:
            # A typo'd tier must not silently fall back to "read" — that is the
            # #4170 failure mode (an under-classified writer served as a read).
            raise ValueError(
                f"{self.name}: unknown risk {self.risk!r} — must be one of {RISK_TIERS}")

    @property
    def annotations(self) -> ToolAnnotations:
        """DERIVED MCP hints (#4122) — recomputed on every read, never stored.

        A property rather than a field on purpose: the only way to derive a
        variant of a frozen entry is `dataclasses.replace`, which copies stored
        fields verbatim — so a stored `annotations` would have stayed stale
        after `replace(risk=...)`, exactly the drift class this change closes.
        """
        if self.annotations_override is not None:
            return self.annotations_override
        return annotations_for_risk(self.risk, self.idempotent)

    @property
    def writes(self) -> bool:
        """DERIVED write permission (#4122) — never declared on an entry.

        Read by `mcp_server._enforce_mcp_tool_scope` (a `writes` entry needs
        `graphs:write`) and by `get_write_tool_names()` / `WRITE_TOOL_NAMES`
        (the read-velocity classification set), so it stays a plain attribute
        from their point of view while having exactly ONE source: `risk`."""
        return self.risk in WRITE_RISK_TIERS


# ── Risk tiers (#4122) ────────────────────────────────────────────
#
#   risk: read | maintenance | write | destructive | admin
#      ├─ writes (needs graphs:write) ← write | destructive | admin
#      ├─ readOnlyHint               ← read
#      └─ idempotentHint             ← maintenance (or an explicit `idempotent`)
#
# `maintenance` is a class of its own, following the PostgreSQL MAINTAIN privilege
# (added in **PostgreSQL 17**, not 16: `GRANT MAINTAIN ON <table>` alongside
# SELECT/INSERT/UPDATE/DELETE, plus the `pg_maintain` role). PostgreSQL's own
# answer is to mint a SEPARATE grant; ours cannot — a new scope would be a
# surface change — so we keep the read key and refuse to CLAIM read-only instead.
# A confined refresh that recomputes and stores a DERIVED value is therefore
# neither a plain read nor a caller-authored write — and calling it a
# write (`writes=True`) would break integrations AND stop metering the expensive
# recompute as a read (`maybe_record_mcp_read` skips writers), which is the cost
# lever OWASP API4 asks us to watch. Hence the third tier, and hence the
# `OVERRIDES:` line on the `risk` field above.
#
# The three tools that motivated it (#4122): `tortoise_get_confidence`,
# `tortoise_compute_confidence`, `tortoise_packs_list`. They keep `graphs:read`
# and stop claiming `readOnlyHint: true` — the hint is what clients auto-approve
# on. The hint was NOT the only wrong channel: the `tortoise_packs_list`
# `description` kept telling the agent it was "Read-only;" after the hint was
# fixed, and that second string was corrected under #9575 — the two are separate
# artifacts an agent reads before calling, and #4122 only closed the first.
#
# THE TIER AXIS, in one sentence each, so the next lane does not re-litigate a
# row (the split is what `destructiveHint` means to a client):
#   read        — reads only, plus system-owned RETENTION housekeeping on the one
#                 recorded carve-out (`tortoise_events_poll` → `purge_expired`,
#                 declared in `tests/tool_surface_capabilities.py` →
#                 RETENTION_CARVEOUTS so the guard sees it). It stays `read`
#                 because `writes=True` would take a poll away from read keys and
#                 `maintenance` would drop the read hint on a tool agents poll in a
#                 loop (a confirmation prompt per poll). Reopen #4122 rather than
#                 re-tiering it silently.
#   maintenance — a DERIVED value it owns is refreshed (the trio above, plus a
#                 confined housekeeping pass). Idempotent by default; an entry
#                 overrides `idempotent` where the behaviour differs (both
#                 confidence tools DO re-store on a repeat call).
#   write       — ADDITIVE, and it may also apply THE CALLER'S OWN property to the
#                 row THIS CALL identified (`create_source` sets `credibilityTier`).
#                 It never removes a row, never rewrites a row the caller did not
#                 name, and never stores a value the caller did not supply — a call
#                 that re-applies OUR OWN DEFAULT is `destructive` below. It is a
#                 WRITER either way, so "additive" must not be read as "changes
#                 nothing". `tortoise_onboarding_github_index` is here deliberately:
#                 it starts a background index of the org's GitHub issues/PRs — new
#                 rows from external content, no user-authored row rewritten.
#   destructive — removes, or overwrites in place, stored state that a user
#                 authored (delete family, in-place update family, terminal status
#                 writes, whole-graph rewrites, re-ingest that rewrites an existing
#                 corpus) — `destructiveHint: true`. A tool belongs here whenever a
#                 REPEAT or a WRAPPER call reaches one of these (`mitigate_operator`
#                 updates its mitigation point; `index_sessions` and
#                 `mine_conversations` wrap the corpus re-index), and whenever a bare
#                 repeat call applies values over a node that already exists:
#                 `create_subject` / `create_object` / `create_entity` write the
#                 caller's props ON MATCH (`_persist_extra_props`), so a re-create
#                 re-applies them — the same overwrite basis `tortoise_update` is in
#                 this tier for. The FABRICATED-DEFAULT half of that finding is
#                 FIXED (#9572: a caller who names no `subjectKind`/`objectKind` now
#                 journals none, and the fold supplies `"other"` ON CREATE only), so
#                 the reason these three stay here is the props lane, not the
#                 discriminator. Re-deriving the tier from #4122's axis now that the
#                 clobber is gone is a classification question of its own, deliberately
#                 NOT settled by a bug fix.
#   admin       — changes what the product DOES for the org/graph/install:
#                 provisioning, pack install, credentials, recording/monitoring and
#                 source switches. `destructiveHint: true`.
# Both writing tiers keep `graphs:write`; the split exists only so a client (and
# the per-tool consent UI the ruling defers) can tell "adds" from "replaces".

#: Every legal tier, least to most risk.
RISK_TIERS: tuple[str, ...] = ("read", "maintenance", "write", "destructive", "admin")

#: Tiers that require `graphs:write` — i.e. the meaning of `writes`.
WRITE_RISK_TIERS: frozenset[str] = frozenset({"write", "destructive", "admin"})

#: Tier for a confined, idempotent refresh of a derived value (PostgreSQL MAINTAIN).
MAINTENANCE_TIER = "maintenance"

#: risk → (readOnlyHint, destructiveHint, idempotentHint), the idempotency
#: default being `risk == maintenance` (an entry may override it, see
#: `ToolDefinition.idempotent`). `destructiveHint` is the MCP spec's "may this
#: perform destructive updates, or only additive ones": true for the tiers that
#: remove/overwrite stored data and for org-level admin, false for additive
#: writes — the `write` tier is additive BY DEFINITION, which is what makes it
#: a separate tier from `destructive` at all.
_RISK_HINTS: dict[str, tuple[bool, bool, bool]] = {
    "read":        (True,  False, False),
    "maintenance": (False, False, True),
    "write":       (False, False, False),
    "destructive": (False, True,  False),
    "admin":       (False, True,  False),
}


def annotations_for_risk(risk: str, idempotent: Optional[bool] = None) -> ToolAnnotations:  # noqa: UP045
    """The MCP hints for a tier — the ONLY place they are decided.

    Every hint a client sees comes from here, so `risk` cannot disagree with the
    hints (pinned by `tests/test_tool_risk_tiers.py`). A tier's row is the single
    statement: `idempotent` overrides ONLY the retry-safety element, for the tools
    whose operation is not idempotent despite its tier."""
    read_only, destructive, idem = _RISK_HINTS[risk]
    if idempotent is not None:
        idem = bool(idempotent)
    return ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                           idempotentHint=idem)


# ── Shorthand annotations (probe/test only — the registry DERIVES its own) ──
# Kept because the capability guards construct deliberately-inconsistent probe
# entries with them (`tests/test_tool_registry.py::_probe`); NO registry entry
# declares annotations any more (#4122), which is why there is no `_idem()` — an
# idempotent write is expressed as `idempotent=True` on its entry.

def _ro() -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=False)


def _rw() -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


# ── Registry Entries ────────────────────────────────────────────
# Maintained in the same order as mcp_server.py for diffability.
# New tools: add one entry here → both surfaces pick it up.
#
# ⛔ AN ENTRY HERE EXPANDS THE AGENT-FACING SURFACE, AND THAT NEEDS EXPLICIT
#    HUMAN APPROVAL (#3863). `tools/surface-guard.py` — CI job `surface-guard`,
#    part of `python-ci-gate` — compares this registry against the frozen
#    baseline in `config/surface-manifest.yml` and fails closed on ANY added
#    tool, removed tool, changed SDK binding, or an exemption that has become
#    reachable.
#
#    To propose an addition: change the registry, run
#    `uv run python tools/surface_manifest.py cut` to fold it into the baseline,
#    run `... render` to regenerate `docs/product/mcp-sdk-surface.md`, and record
#    the owner's approval in `docs/product/canonical-mcp-tools.md` — the next `cut`
#    copies it into the manifest, and the per-row `approval:` is only an audit
#    record, never the gate. A baseline that no longer matches this registry is a
#    red build by design — that red IS the gate. Do not "fix" it by editing the
#    baseline to match; see docs/product/mcp-sdk-surface.md for the curated list.

# The FULL declaration, live and retired together. `TOOL_REGISTRY` (the live,
# advertised surface) and `RETIRED_TOOL_REGISTRY` (the #3883 warning shims) are
# both derived from this at the module bottom — which entries land where is
# decided by `RETIRED_USE_INSTEAD` there, so an entry's retirement is legible in
# one place rather than implied by which list literal it sits in.
_ENTRY_DECLARATIONS: list[ToolDefinition] = [
    # ── Core CRUD ─────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_create_point",
        id="surface.create_point",
        description="Create a Point node (statement, decision, vision, hypothesis, etc.). "
                    "dedup=True (default): idempotent — returns existing Point if content matches. "
                    "Declaring extractedFrom materializes that :Source and links it (#8171); "
                    "a fresh write with no source and no provenance is advised (advisories: "
                    "[{code: no_source, node_id, message}] — advisory only, never a block). "
                    "validTo accepts an ISO-8601 instant (representational variance such as "
                    "'2024-3-1' or '2024/03/01' is normalised silently), or a DECLARED no-date "
                    "value — 'unknown', TBD, TBA, TBC, N/A, NA, -, --, an em dash, or '?' — which "
                    "is stored as the canonical un-datable end (no end plus "
                    "validToPrecision='unknown', reading end_state='unknown') and echoed back as "
                    "valid_to_shaped; None or omitted means the end is OPEN. An impossible "
                    "calendar day, a month outside 01-12, an epoch passed as a string, or prose "
                    "with no recoverable date is REFUSED, naming the fault and the accepted "
                    "forms (#9202).",
        risk="write",
        idempotent=True,
        http_policy=True,
        sdk_method="create_point",
        rest_spec=RestSpec(method="POST", path="/v1/points",
                           request_model="CreatePointRequest",
                           response_model="PointResponse"),
    ),
    ToolDefinition(
        name="tortoise_query",
        id="surface.query",
        description="Query points by pointKind and/or property filters — structural exact-match "
                    "retrieval for known shapes (Epic #888: paginated_query + query_points_by_tag "
                    "merged in). Pagination via offset=/limit= (or 1-based page=); tag= filters "
                    "Points by TAGGED edge; include_retracted=True surfaces tombstones. For "
                    "semantic relevance use tortoise_search; for a single known ID use "
                    "tortoise_get_point.",
        risk="read",
        http_policy=True,
        sdk_method="query",
    ),
    ToolDefinition(
        name="tortoise_paginated_query",
        id="surface.paginated_query",
        description="DEPRECATED (Epic #888) — thin alias for tortoise_query(offset=, limit=). "
                    "Kept for one release; will be removed in the next release — migrate now.",
        risk="read",
        http_policy=True,
        sdk_method="paginated_query",
    ),
    ToolDefinition(
        name="tortoise_check_structure",
        id="surface.check_structure",
        description="Check Gate 0→4 chain integrity (orphans, dangling refs).",
        risk="read",
        http_policy=True,
        sdk_method="check_structure",
    ),
    ToolDefinition(
        name="tortoise_validate_domain",
        id="surface.validate_domain",
        description="Validate a domain's ontology integrity (issue #405) — advisory, read-only. "
                    "Runs the domain's graph-surface rules (orphan useCase, dangling refs, "
                    "draft hygiene) and returns enriched, actionable violations "
                    "({rule, kind, ref, message, fix}) + drift warnings. Never modifies "
                    "the graph; violations are warnings, not blocks.",
        risk="read",
        http_policy=True,
        sdk_method="validate_domain",
    ),
    ToolDefinition(
        name="tortoise_summarize_structure",
        id="surface.summarize_structure",
        description="Structure summary — points on the graph across ALL point "
                    "kinds (#2205). Returns {total, operators, gate0_jtbds.."
                    "gate4_requirements, gate_total}: total counts non-operator "
                    "Points of every kind (not just the product-strategy gates), "
                    "operators is reported separately, gate keys stay the "
                    "gate-kind breakdown, gate_total is the gate subtotal.",
        risk="read",
        http_policy=True,
        sdk_method="summarize_structure",
    ),
    ToolDefinition(
        name="tortoise_audit",
        id="surface.audit",
        description="Audit graph wiring quality — 8 checks: missing sourceKind "
                    "(point-level legacy + Source-level canonical), missing sourceDate, "
                    "superseded points without a CORRECTS edge, live IMPL/NAND edges "
                    "into superseded points, naive-IMPL contradiction heuristic, "
                    "low-confidence operators without mitigation, and legacy 'mitigates' "
                    "edges. Returns structured JSON: per-check counts (uncapped) + "
                    "capped samples + summary + exit_code (0 clean, 1 issues). "
                    "point_kinds: optional pointKind list to scope the audit.",
        risk="read",
        http_policy=True,
        sdk_method="audit",
    ),
    ToolDefinition(
        name="tortoise_list_pointkinds",
        id="surface.list_pointkinds",
        description="List all pointKinds present in the graph with counts. What EXISTS.",
        risk="read",
        http_policy=True,
        sdk_method="list_pointkinds",
    ),
    ToolDefinition(
        name="tortoise_list_sources",
        id="surface.list_sources",
        description="List all Sources with point counts. Where data came FROM.",
        risk="read",
        http_policy=True,
        sdk_method="list_sources",
    ),
    ToolDefinition(
        name="tortoise_list_namespaces",
        id="surface.list_namespaces",
        description="List installed pack namespaces.",
        risk="read",
        http_policy=True,
        sdk_method="list_namespaces",
    ),
    # epic #902 A13 (#1051) — batch audit surface
    ToolDefinition(
        name="tortoise_list_batch",
        id="surface.list_batch",
        description="Audit one ingest bundle: the stamped artifacts (Points "
                    "created or adopted via dedup + direct edges) carrying the "
                    "given batch_id. Entities/sources are out of stamp scope; "
                    "editorial supersede artifacts are outside audit. "
                    "Completeness holds across rebuild_all.",
        risk="read",
        http_policy=True,
        sdk_method="list_batch",
    ),
    ToolDefinition(
        name="tortoise_list_batches",
        id="surface.list_batches",
        description="Batch discovery — the most recent distinct ingest batch_ids "
                    "with their point/direct-edge counts (ordered by newest "
                    "stamp, capped at limit, default 20).",
        risk="read",
        http_policy=True,
        sdk_method="list_batches",
    ),
    ToolDefinition(
        name="tortoise_packs_list",
        id="surface.packs_list",
        description="List this team's active packs (#318): the shared pack catalog "
                    "joined with the tenant graph's PackInstall activation records. "
                    "NOT a pure read: when the graph carries no PackInstall record "
                    "at all, the handler re-seeds the starter set before it answers "
                    "(one MERGE per starter namespace), so a graphs:read caller can "
                    "cause a write. The re-seed is skipped when the graph's pack set "
                    "was deliberately configured (#8648). Empty result when nothing "
                    "is installed (existence masking — another tenant's packs are "
                    "never observable).",
        risk="maintenance",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (calls pack_state.get_tenant_packs)
    ),
    ToolDefinition(
        name="tortoise_pack_install",
        id="surface.pack_install",
        description="Install a custom expansion pack on the HOSTED surface (#1935): "
                    "validates against the shared registry + tenant policy "
                    "(reserved starter namespace, ontology-only v1), stores the "
                    "manifest in the tenant graph and activates it. Deployment- "
                    "gated: on self-host this is an actionable stub (use the "
                    "filesystem packs dir + tortoise pack CLI).",  # C5 #2114 (re-review P2): MERGEs manifests/installs — a write
        risk="admin",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (calls pack_manifest_store.upsert_tenant_manifest)
        group="admin",
        hosted_only=True,
    ),
    # ── Tags (#215) ───────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_list_tags",
        id="surface.list_tags",
        description="List all Tag names with count of tagged Points. Where tags are USED.",
        risk="read",
        http_policy=True,
        sdk_method="list_tags",
    ),
    ToolDefinition(
        name="tortoise_query_points_by_tag",
        id="surface.query_points_by_tag",
        description="DEPRECATED (Epic #888) — thin alias for tortoise_query(tag=). "
                    "Kept for one release; will be removed in the next release — migrate now.",
        risk="read",
        http_policy=True,
        sdk_method="query_points_by_tag",
    ),
    # ── Point accessors ───────────────────────────────────────────
    ToolDefinition(
        name="tortoise_get_point",
        id="surface.get_point",
        description="Get a single Point by ID. Returns all properties, or empty dict.",
        risk="read",
        http_policy=True,
        sdk_method="get_point",
    ),
    ToolDefinition(
        name="tortoise_suggest_entry_points",
        id="surface.suggest_entry_points",
        description="Entity resolution — NL query → matching entities from the graph.",
        risk="read",
        http_policy=True,
        sdk_method="suggest_entry_points",
    ),
    # ── Semantic Search (#6990) ───────────────────────────────────
    ToolDefinition(
        name="tortoise_search",
        id="surface.search",
        description="Hybrid semantic search — FTS + vector + structural RRF fusion with EP "
                    "confidence annotation. Use when matching by MEANING (natural-language "
                    "query); for exact structural/filter queries use tortoise_query. "
                    "Full-scan mode: omit query, set kind → all Points of kind.",
        risk="read",
        http_policy=True,
        sdk_method="tortoise_fts_query",
        rest_spec=RestSpec(method="GET", path="/v1/search"),
    ),
    ToolDefinition(
        name="tortoise_expand_relationships",
        id="surface.expand_relationships",
        description="Full relationship payload for ONE Point, incl. each related point's "
                    "content (the expand side of the #1353 list/expand split — search "
                    "returns bounded state entries; use this to read a neighbor's full "
                    "text on demand).",
        risk="read",
        http_policy=False,
        sdk_method="expand_relationships",
    ),
    ToolDefinition(
        name="tortoise_recall",
        id="surface.recall",
        description="Epistemic recall — four intents via mode (preset + override). "
                    "mode='state' (UC1): current high-confidence state — "
                    "multiplicative confidence gate "
                    "(score = relevance^a × confidence^b × (1 + w_c·centrality)), "
                    "excludes superseded/deprecated/retracted by default "
                    "(include_superseded=True brings them back), object-centric "
                    "(Objects + their Points ranked together), surfaces the most "
                    "important arguments (operators), high-contention NANDs and "
                    "mitigations, and flags contested claims with counter-evidence "
                    "attached (never rank-penalized). mode='gaps' (UC2): "
                    "load-bearing but under-supported claims — graph-structure "
                    "query (score = load/(1+support); load = outgoing IMPL+NAND, "
                    "support = incoming IMPL + Source edges; reads IMPL/NAND "
                    "operator-mediated or direct per the reification rule); needs "
                    "query (topic scope) or kind (population scan). mode='subgraph' "
                    "(UC3): complete connected subgraph for a seed/topic — "
                    "completeness-optimized (high recall, precision secondary), "
                    "returns {nodes, edges, stats}; needs seed (node id, Source url, "
                    "or topic text); depth 1-5, completeness core|full. "
                    "mode='custom': raw params, full control.",
        risk="read",
        http_policy=True,
        sdk_method="recall_state",
    ),
    # ── Phase-4 mining/promotion/dedup/timeline (#787) ────────────
    ToolDefinition(
        name="tortoise_mine_conversations",
        id="surface.mine_conversations",
        description="Mine agent conversations (single transcript or corpus "
                    "batch) into the graph — extraction, entity reification, "
                    "content dedup, temporal wiring, W-3 batch gate. Batch "
                    "per-file failures reported non-fatally in 'errors'. "
                    "WRITES to the graph; corpus mode walks the server "
                    "filesystem — stdio/CLI only (excluded from tenant HTTP).",
        risk="destructive",  # wraps ingest_corpus — the same corpus re-ingest
        http_policy=False,
        sdk_method="mine_corpus",
    ),
    ToolDefinition(
        name="tortoise_list_dedup_candidates",
        id="surface.list_dedup_candidates",
        description="Review queue for dedup/temporal candidates "
                    "(candidate_type=content|temporal|entity).",
        risk="read",
        http_policy=True,
        sdk_method="list_dedup_candidates",
    ),
    ToolDefinition(
        name="tortoise_approve_merge",
        id="surface.approve_merge",
        description="Review a dedup/temporal candidate — action=merge|reject. "
                    "Wiring deferred to promotion for live priors. WRITES "
                    "review flags + wires edges (idempotent for repeats).",
        risk="write",
        idempotent=True,
        http_policy=True,
        sdk_method="approve_merge",
    ),
    ToolDefinition(
        name="tortoise_promote_point",
        id="surface.promote_point",
        description="Reviewer-gated draft→live promotion — the only path a "
                    "draft extraction Point may go live (quarantine lock, "
                    "R16 operator promotion, deferred dedup/temporal wiring). "
                    "WRITES status + wires edges.",
        risk="destructive",
        http_policy=True,
        sdk_method="promote_point",
    ),
    ToolDefinition(
        name="tortoise_belief_timeline",
        id="surface.belief_timeline",
        description="Dated, ordered belief chain for a topic — decision "
                    "Points with validFrom, NAND/CORRECTS links, superseded "
                    "priors visible.",
        risk="read",
        http_policy=True,
        sdk_method="belief_timeline",
    ),
    # ── EP Belief Propagation (#6908) ─────────────────────────────
    ToolDefinition(
        name="tortoise_compute_confidence",
        id="surface.compute_confidence",
        description="Compute confidence via EP belief propagation. "
                    "Returns {iterations, converged, confidences}. "
                    "#395: no-arg (no factors/anchors) runs LOCAL EP over "
                    "the dirty subgraph on stdio/embedded; over HTTP, the "
                    "no-arg path serves the graph-persisted dirty subgraph "
                    "(#1163) and only returns diagnostic "
                    "'no_dirty_state_http' when the graph is truly clean. "
                    "anchors+max_hops=None is "
                    "clamped to a bounded default over HTTP. "
                    "Calibration is required by default (#344): raises "
                    "CalibrationError on uncalibrated graphs. Pass "
                    "require_calibration=False to run on topology alone "
                    "(explicit opt-out).",
        risk="maintenance",
        # measured on #4122: call 2 starts from the value call 1 stored
        # (0.693315 -> 0.693320), so repeated calls DO have an additional effect
        idempotent=False,
        http_policy=True,
        sdk_method="compute_confidence",
    ),
    ToolDefinition(
        name="tortoise_set_point_baseline",
        id="surface.set_point_baseline",
        description="Set Beta prior evidence for a claim.",
        risk="destructive",
        http_policy=True,
        sdk_method="set_point_baseline",
    ),
    ToolDefinition(
        name="tortoise_get_confidence",
        id="surface.get_confidence",
        description="Get EP confidence for a claim: {mean, variance, alpha, beta}.",
        risk="maintenance",
        # same lazy refresh path as compute_confidence: a repeat call can store a
        # re-derived score, so it is NOT a no-op (#4122 review)
        idempotent=False,
        http_policy=True,
        sdk_method="get_confidence",
    ),
    ToolDefinition(
        name="tortoise_calibrate_summary",
        id="surface.calibrate_summary",
        description="Audit graph calibration state. Returns per-point guidance.",
        risk="read",
        http_policy=True,
        sdk_method="calibrate_summary",
    ),
    ToolDefinition(
        name="tortoise_dream",
        id="surface.dream",
        description="Run EP stabilization (dreaming, #85). "
                    "Default: incremental dirty subgraph. Set full=True for whole-graph. "
                    "mode (epic 903): explicit strategy override "
                    "{\"local\", \"stale-first\", \"full\"} — wins over full; "
                    "budget: per-pass operator cap.",
        # #329: whole-graph EP is CPU-heavy — tenant MCP surface excluded
        # (stdio/operator only). REST /v1/dream remains tenant-reachable but is
        # separately budgeted at MAX_DREAM_FULL_PER_HOUR (hosted_api).
        risk="destructive",
        http_policy=False,
        sdk_method="dream",
        rest_spec=RestSpec(method="POST", path="/v1/dream"),
    ),
    ToolDefinition(
        name="tortoise_dream_health",
        id="surface.dream_health",
        description="Dream observability (epic 903-C7): zero-output "
                    "silent-death alarm verdict + health record (last pass, "
                    "coverage, failure rate, region_attempts, warm-start "
                    "savings). Embedded call-triggered evaluator.",
        risk="read",
        http_policy=False,
        sdk_method="dream_health_check",
        rest_spec=RestSpec(method="GET", path="/v1/dream/health"),
    ),
    # ── Updates ───────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_update_point",
        id="surface.update_point",
        description="Update properties on a Point. Safe — modifies one Point only. "
                    "validTo takes the same forms as tortoise_create_point: an ISO-8601 "
                    "instant, a declared no-date value ('unknown', TBD, TBA, TBC, N/A, NA, -, "
                    "--, — or ? — stored as no end plus validToPrecision='unknown' and echoed "
                    "back as valid_to_shaped), or None for an OPEN end; an impossible date, a "
                    "month outside 01-12, an epoch as a string or unreadable prose is refused "
                    "naming the repair. Existing rows are never re-validated or migrated "
                    "(#9202).",
        risk="destructive",
        http_policy=True,
        sdk_method="update_point",
    ),
    # ── Operators ─────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_create_operator",
        id="surface.create_operator",
        description="Create an operator connecting Points. "
                    "op_type: IMPL, NAND, composedOf, decomposesInto, contains, wraps. "
                    "semantics: the Layer-2 meaning (addresses, supports, refutes, "
                    "mutuallyExclusive, hasPart, related, …) — it resolves the direction.",
        risk="write",
        idempotent=True,
        http_policy=True,
        sdk_method="create_operator",
    ),
    ToolDefinition(
        name="tortoise_annotate_operator",
        id="surface.annotate_operator",
        description="Annotate an operator Point with structured epistemic dimensions "
                    "(bias, precision, consistency, directness).",
        risk="destructive",
        http_policy=True,
        sdk_method="annotate_operator",
    ),
    ToolDefinition(
        name="tortoise_get_operator",
        id="surface.get_operator",
        description="Get an operator Point by ID. Returns all properties including "
                    "annotation dimensions.",
        risk="read",
        http_policy=True,
        sdk_method="get_point",  # get_operator uses get_point + is_operator check
    ),
    ToolDefinition(
        name="tortoise_mitigate_operator",
        id="surface.mitigate_operator",
        description="Create a mitigation Point that modulates an operator's edge strength. "
                    "Idempotent for a CURRENT mitigation — a second call updates it in place; "
                    "once it has been withdrawn (retracted/superseded/outdated/archived) a "
                    "re-assertion mints a NEW mitigation Point and returns its id.",
        risk="destructive",
        idempotent=True,
        http_policy=True,
        sdk_method="mitigate_operator",
    ),
    # ── Decisions ─────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_file_decision",
        id="surface.file_decision",
        description="File a simple decision directly to the graph. "
                    "Creates decision + options + evidence + IMPL edges atomically.",
        risk="write",
        http_policy=True,
        sdk_method="file_decision",
    ),
    ToolDefinition(
        name="tortoise_file_human_approval",
        id="surface.file_human_approval",
        description="File a human approval of a planning artifact to the graph. "
                    "Creates Event (eventKind: humanApproval) + decision Point "
                    "(pointKind: humanApproval) + unidirectional IMPL fan-out "
                    "(label approvedBy) so dependent claims strengthen.",
        risk="write",
        http_policy=True,
        sdk_method="file_human_approval",
    ),
    # ── Deletion / Invalidation ───────────────────────────────────
    ToolDefinition(
        name="tortoise_delete_point",
        id="surface.delete_point",
        description="Delete a Point. DESTRUCTIVE — requires human confirmation. Cannot be undone. "
                    "dry_run=True previews the blast radius (the point and every edge that "
                    "would be removed) and changes nothing.",
        risk="destructive",
        http_policy=True,
        sdk_method="delete_point_wrapped",
    ),
    ToolDefinition(
        name="tortoise_invalidate",
        id="surface.invalidate",
        description="Mark a Point outdated with a CORRECTS edge from the correcting Point. "
                    "dry_run=True previews the one-point transition + one edge and changes nothing.",
        risk="destructive",
        http_policy=True,
        sdk_method="invalidate_point",
    ),
    ToolDefinition(
        name="tortoise_supersede",
        id="surface.supersede",
        description="Atomically replace old Point with new — CORRECTS edge + outdated flag. "
                    "transfer_edges=True (default): full supersede — all edges move from "
                    "old to new. transfer_edges=False: invalidate behavior — outdated flag "
                    "+ CORRECTS edge only, no edge transfer. "
                    "dry_run=True previews exactly which edges would transfer and changes nothing.",
        risk="destructive",
        http_policy=True,
        sdk_method="supersede",
    ),
    # ── Subscriptions / claim lifecycle (#432) ─────────────────────
    ToolDefinition(
        name="tortoise_events_poll",
        id="surface.events_poll",
        description="Poll graph/claim events after an opaque cursor (at-least-once). "
                    "Returns {events, next_cursor}. Event types: 12 registered claim "
                    "types (PointAdded, OperatorAdded, PointRetracted, PointSuperseded, "
                    "OperatorAnnotated, PointPromoted, OperatorPromoted, DedupeRecorded, "
                    "DedupeRejected, ObjectSuperseded, PointInvalidated, PointRevised). "
                    "PointRevised reaches the store, and the response adds `changes` "
                    "(per-claim before→after deltas), only while TORTOISE_CHANGE_FEED "
                    "is enabled.",
        # ⚠️ `read` IS DELIBERATE, AND IT IS A RECORDED CARVE-OUT (#9576, the
        # #4122 defect class). The handler path also runs interval-gated retention
        # housekeeping on SYSTEM-owned rows (`poll_events` -> `_maybe_purge_events`
        # -> `event_store.purge_expired`/`purge_overflow`, a `DETACH DELETE` of
        # expired-or-over-cap `:GraphEvent` rows, then `_refresh_first_seq` SETs the
        # `(:GraphEventMeta).first_seq` watermark), so a read-only key CAN cause a
        # DELETE and a watermark write. It stays `risk="read"` because both
        # alternatives are worse for the caller: `risk="write"` would take a poll
        # away from `graphs:read` keys entirely, and any non-read tier
        # (`maintenance` included) derives `readOnlyHint: false`, i.e. a
        # confirmation prompt in front of a tool agents poll in a loop. The rows are
        # the store's own bookkeeping, deleted by policy and never replayed by a
        # rebuild —
        # `docs/retention-and-deletion.md` records the window as an OPERATIONAL
        # property, not user content.
        #
        #   OVERRIDES: `risk="read"` ⇒ `readOnlyHint: true` ⇒ the call changes
        #   nothing the caller owns — this poll purges `:GraphEvent` rows the store
        #   owns, on a timer, idempotently. (#4122's tier axis was written for a
        #   DERIVED-value write; this is system-owned RETENTION, and #9576 is the
        #   ruling that it stays `read` rather than `maintenance`.)
        #
        # The decision is checkable, not just written down: the carve-out is
        # declared in `tests/tool_surface_capabilities.py` -> RETENTION_CARVEOUTS
        # (with the out-of-boundary mutation it covers), and a guard fails if this
        # entry moves to a WRITING tier (the carve-out then no longer covers a read),
        # if it stops reaching the purge, or if ANY other reading-tier
        # (`read | maintenance`) tool reaches one of those DECLARED mutations
        # without a carve-out. A new cross-module mutation is not auto-detectable
        # (that is the #9576 gap), so it must be added to `OUT_OF_BOUNDARY_MUTATORS`
        # alongside the code that introduces it.
        # Re-tier it deliberately or not at all: a move to a WRITING tier must
        # delete the carve-out with it, while a move to `maintenance` KEEPS it
        # (that tier must still declare the deletion, and the ledger has a green
        # state for both reading tiers). Either re-tier reds the ruling pin,
        # `test_the_read_classification_this_issue_asked_about`, which names
        # #9576/#4122 — see the `TestReadTierCannotHideAnOutOfBoundaryWrite`
        # family in `tests/test_tool_registry.py`.
        risk="read",
        http_policy=True,
        sdk_method="poll_events",
    ),
    ToolDefinition(
        name="tortoise_retract_point",
        id="surface.retract_point",
        description="Tombstone-retract a Point — status='retracted' (point stays "
                    "in graph, excluded from default surfaces). Terminal; cannot "
                    "retract operators or already-terminal points. "
                    "dry_run=True previews the status transition and changes nothing.",
        risk="destructive",
        http_policy=True,
        sdk_method="retract_point",
    ),
    # ── Navigation (#6962, #6963, #6964) ──────────────────────────
    ToolDefinition(
        name="tortoise_entity_profile",
        id="surface.entity_profile",
        description="Multi-hop BFS from an entity with optional filters (pointKind, "
                    "confidenceMin) — full neighborhood categorized by node type. Use for deep "
                    "entity analysis; for a fast neighbor list use tortoise_list_topics.",
        risk="read",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (calls navigation.entityProfile)
    ),
    ToolDefinition(
        name="tortoise_traverse",
        id="surface.traverse",
        description="Multi-hop graph traversal from entity following ALL relationship types. "
                    "Returns {entity, nodes: [{node, relationship, depth}]}.",
        risk="read",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (tortoise.navigation.tortoise_traverse on sdk._get_proj(); #3904)
    ),
    # ── P0 Group 3: Checkpoint, Diary, Status, Ingest ─────────────
    ToolDefinition(
        name="tortoise_checkpoint",
        id="surface.checkpoint",
        description="Session batch save — two-tier dedup (content hash + embedding similarity). "
                    "Returns {filed: N, duplicates: M}.",
        risk="write",
        idempotent=True,
        http_policy=True,
        sdk_method="checkpoint",
    ),
    ToolDefinition(
        name="tortoise_diary_write",
        id="surface.diary_write",
        description="Write an agent diary entry (AAAK format suggested). "
                    "Creates a Point with pointKind=diary, authoredBy=agent.",
        risk="write",
        http_policy=True,
        sdk_method="diary_write",
    ),
    ToolDefinition(
        name="tortoise_diary_read",
        id="surface.diary_read",
        description="Read recent diary entries for an agent, newest first.",
        risk="read",
        http_policy=True,
        sdk_method="diary_read",
    ),
    ToolDefinition(
        name="tortoise_list_graphs",
        id="surface.list_graphs",
        description="List all graph names in the database. Useful for namespace discovery.",
        risk="read",
        http_policy=True,
        sdk_method="list_graphs",
    ),
    ToolDefinition(
        name="tortoise_status",
        id="surface.status",
        description="Graph health + entity counts + FalkorDB connectivity. "
                    "Returns {connected, counts, total_entities}.",
        risk="read",
        http_policy=True,
        sdk_method="status",
    ),
    ToolDefinition(
        name="tortoise_health",
        id="surface.health",
        description="Health check + basic metrics: graph_size, last_ingest, error_count, uptime.",
        risk="read",
        http_policy=True,
        sdk_method="health",  # monitoring.metrics — not a direct SDK method
    ),
    ToolDefinition(
        name="tortoise_session_context",
        id="surface.session_context",
        description="Return 'what happened last session' — diary entries, recent Points, "
                    "Events, confidence changes.",
        risk="read",
        http_policy=True,
        sdk_method="session_context",
        rest_spec=RestSpec(method="GET", path="/v1/context"),
    ),
    ToolDefinition(
        name="tortoise_session_capture",
        id="surface.session_capture",
        description="File an agent session into the graph (epic #909 capture path): "
                    "turns become episodic Points, the conversation is LLM-extracted into "
                    "epistemic Points, and the Session links to subject/project entities. "
                    "#1927: session_recording is default-ON (ToS-covered) with an "
                    "optional off-switch — returns 409 when the team disabled capture; "
                    "402 at quota; a missing LLM provider key is NOT a refusal — the "
                    "capture is STORED and only extraction is skipped "
                    '(extraction_mode "no-provider"); '
                    "422 for an invalid harness. session_id is the idempotency key (re-filing "
                    "the same id mints zero new nodes). Only call this when the team has "
                    "session recording enabled (the dashboard 'Memory sources > Agent sessions' "
                    "toggle); if the call fails, tell the user it wasn't filed and do NOT "
                    "retry. Consent: this tool runs server-side and cannot read the caller's "
                    "TORTOISE_CAPTURE, so calling it IS the agreement to record the session "
                    "(#3662, Option 1 — connecting the agent is the agreement); tell the user "
                    "you are filing the session before you call, so they can decline. Requires "
                    "hosted mode — stdio/self-hosted returns an honest error "
                    "(no local fallback that bypasses the capture pipeline).",
        risk="write",
        http_policy=True,
        # Custom handler in mcp_server.py — the REST /v1/sessions route stays
        # hand-written in hosted_api.py (raw-Cypher op, Gate 3 pre-step
        # decision); no rest_spec here so the router adapter never double-
        # registers the hand-written route.
        sdk_method="",
    ),
    ToolDefinition(
        name="tortoise_graph_set_recording",
        id="surface.graph_set_recording",
        description="Set or clear a graph's session-recording override (#2302) — the MCP "
                    "twin of PATCH /v1/graphs/{graph_id} (recording), sharing the same "
                    "hosted_api core so the surfaces never drift. recording: true/false "
                    "sets the per-graph override, null removes it (inherit the team "
                    "default — never flips a team ON). Requires hosted mode + a "
                    "team:manage-scoped key (or legacy full-access) — the capture 409 "
                    "('Session recording is disabled for this graph') routes agents here; "
                    "call this tool to turn recording back on, then retry the capture.",
        risk="admin",
        http_policy=True,
        # Custom handler in mcp_server.py (ContextVar auth gate); the REST twin
        # is the hand-written PATCH /v1/graphs/{graph_id} in hosted_api.py — no
        # rest_spec so the router adapter never double-registers it.
        sdk_method="",
    ),
    ToolDefinition(
        name="tortoise_issue_insight",
        id="surface.issue_insight",
        description="Return a compact 'there's more in the graph' insight for a would-be "
                    "issue — call BEFORE filing. Surfaces cross-session decisions / EP-tagged "
                    "claims matching the title (semantic stage) plus prior indexed issues for "
                    "the repo (repo= given). Fail-closed: empty graph -> no_prior_knowledge; "
                    "populated graph + unindexed repo -> repo_not_indexed.",
        risk="read",
        http_policy=True,
        sdk_method="issue_insight",
        rest_spec=RestSpec(method="GET", path="/v1/issue-insight"),
    ),
    # ── Excluded from HTTP ────────────────────────────────────────
    ToolDefinition(
        name="tortoise_ingest_corpus",
        id="surface.ingest_corpus",
        description="DEPRECATED — use tortoise_index_files. Batch document ingestion — walk directory, parse YAML frontmatter "
                    "from .md files, create/update document Sources "
                    "(D10: a document is a :Source). "
                    "EXCLUDED from tenant HTTP — walks server filesystem with user-supplied path.",
        risk="destructive",
        http_policy=False,
        sdk_method="ingest_corpus",
    ),
    ToolDefinition(
        name="tortoise_ingest",
        id="surface.ingest",
        description="Heterogeneous bulk write (epic #888 W4) — one call writes points + "
                    "entities + sources + connections coherently (nodes first, then "
                    "connections). Connections carrying 'operator' (IMPL/NAND) create "
                    "operator Points per the reification rule (v3.5 §8); connections "
                    "carrying 'relation' stay plain structural edges. Local refs address "
                    "bundle items. Endpoint typing (#7925): connections on BOTH routes "
                    "— operator-routed (reify:true / mitigation / part-whole) and "
                    "direct-edge (plain IMPL/NAND) — accept the four epistemic endpoints "
                    "Point/Subject/Object/Event (by node id; Sources, operator-shaped "
                    "Points or entity items, and eventId-only Events stay violations). "
                    "granularity='bulk' (default) returns aggregated counts; "
                    "granularity='granular' returns per-item results. promotion_policy='gated' "
                    "(default) keeps points draft and never promotes connections (operator "
                    "path: promote_source=False via #780); under gated ANY effective "
                    "status other than 'draft' on a point item is REJECTED (INGEST_CONTRACT "
                    "row 9 — case variants, nested props={...}, and terminal statuses "
                    "included) — use promotion_policy='auto' or promote after ingest via "
                    "update_point(status='live'). promotion_policy='auto' preserves the #131 "
                    "promote-on-wire lifecycle (null-status sources only — an explicit draft "
                    "is never retro-promoted, #1088; terminal sources are never resurrected; "
                    "deduped connections never retro-promote). Idempotent-ish: "
                    "points dedup by content hash + kind, sources by url, operators by "
                    "input set.",
        risk="write",
        idempotent=True,
        http_policy=True,
        sdk_method="ingest",
    ),
    # ── Taxonomy ──────────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_taxonomy",
        id="surface.taxonomy",
        description="Count entities by node label. "
                    "Returns {Point: N, Event: N, Subject: N, Object: N, Source: N} "
                    "(D10: a document is a Source, not a :Document node).",
        risk="read",
        http_policy=True,
        sdk_method="taxonomy",
    ),
    ToolDefinition(
        name="tortoise_list_topics",
        id="surface.list_topics",
        description="Fast one-hop neighbor enumeration for an entity — quick discovery and "
                    "navigation. Use for shallow context; for multi-hop filtered BFS use "
                    "tortoise_entity_profile.",
        risk="read",
        http_policy=True,
        sdk_method="list_topics",
    ),
    # ── Graph Analysis ────────────────────────────────────────────
    ToolDefinition(
        name="tortoise_analyze",
        id="surface.analyze",
        description="Answer natural language questions about the Tortoise epistemic graph. "
                    "Ask things like: 'where is the disagreement?' 'what supports claim X?'",
        risk="read",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (calls analyze.analyze)
    ),
    # ── P1-3: Staleness Detection ─────────────────────────────────
    ToolDefinition(
        name="tortoise_stale",
        id="surface.stale",
        description="Find Points not updated in N days. Returns {stale, count, cutoff, limit}.",
        risk="read",
        http_policy=True,
        sdk_method="stale_points",
    ),
    ToolDefinition(
        name="tortoise_review_connections",
        id="surface.review_connections",
        description="Review graph connections (READ-ONLY — never mutates the graph). "
                    "Hygiene counterpart to connect: mode=add surfaces related-but-missing "
                    "connections as suggestions {from, to, suggested_relation, reason, "
                    "similarity} (nudge, don't enforce); mode=prune flags illogical/stale "
                    "IMPL/NAND connections {from, to, relation, issue, suggested_action, "
                    "detail} with issue in (contradictory, stale, contested) and action in "
                    "(review, prune, re-point); mode=both runs both and returns "
                    "{add: [...], prune: [...]}. Optional scope (topic text or Point id) "
                    "narrows the candidate pool to the retrieval-NEAREST points — a focus "
                    "filter, not an exact-match or relevance gate (mode=add also applies "
                    "similarity_threshold; mode=prune has no similarity bar).",
        risk="read",
        http_policy=True,
        sdk_method="review_connections",
    ),
    ToolDefinition(
        name="tortoise_find_cross_lens_candidates",
        id="surface.find_cross_lens_candidates",
        description="Cross-lens candidate discovery (READ-ONLY, #438 bring-your-own-agent): "
                    "surface unverified candidate pairs between Points from DIFFERENT "
                    "sources (cross-stream discovery over the vector index) with lens "
                    "pair, cosine similarity, point context, and dedup vs existing "
                    "operators. Payload carries a single #901 routing field "
                    "(truth|relevance) but stays NEUTRAL — no op_type hint; the "
                    "customer agent decides semantics and writes operators via the "
                    "normal API. Gated on registered sourceKind (any tier, D3); hard "
                    "cap 200 candidates/cycle (D4); top_k is hard-clamped to 100 so an "
                    "agent cannot inflate the per-cycle recall budget. Empty results "
                    "(not errors) when there is nothing to see (D8).",
        risk="read",
        http_policy=True,
        sdk_method="get_cross_lens_candidates",
    ),
    ToolDefinition(
        name="tortoise_provenance",
        id="surface.provenance",
        description="Provenance chain — 'Who decided this?' "
                    "Follows authoredBy → Subject → delegation.",
        risk="read",
        http_policy=True,
        sdk_method="provenance",
    ),
    # ── Multi-tenancy (#7001) ─────────────────────────────────────
    ToolDefinition(
        name="tortoise_org_create",
        id="surface.org_create",
        description="Create isolated team graph via FalkorDB select_graph. "
                    "EXCLUDED from tenant HTTP — provisioning belongs to "
                    "/internal/provision behind FASTAPI_INTERNAL_KEY.",
        risk="admin",
        http_policy=False,
        sdk_method="org_create",
    ),
    # ── Entity CRUD (ONTOLOGY v2.5) ───────────────────────────────
    ToolDefinition(
        name="tortoise_create_subject",
        id="surface.create_subject",
        description=(
            "Create a Subject node (team, role, organization, person), unless the "
            "kind names the other entity layer: the KIND decides the LABEL "
            "(#9964), so a kind the classifier partitions as an Object (e.g. "
            "subjectKind='core:skill') writes an :Object and is reported as "
            "advisories.kind_layer_routed."),
        risk="destructive",  # a repeat call re-applies caller props ON MATCH (#9572)
        http_policy=True,
        sdk_method="create_subject",
    ),
    ToolDefinition(
        name="tortoise_create_object",
        id="surface.create_object",
        description=(
            "Create an Object node (product, customer, skill, etc.), unless the "
            "kind is a §5 Subject kind: the KIND decides the LABEL (#9964), so "
            "objectKind='core:organization' writes a :Subject and is reported "
            "as advisories.kind_layer_routed."),
        risk="destructive",  # a repeat call re-applies caller props ON MATCH (#9572)
        http_policy=True,
        sdk_method="create_object",
    ),
    ToolDefinition(
        name="tortoise_create_event",
        id="surface.create_event",
        description="Create an Event node (meeting, decision, deployment, etc.).",
        risk="write",
        http_policy=True,
        sdk_method="create_event",
    ),
    ToolDefinition(
        name="tortoise_get_events",
        id="surface.get_events",
        description="Get recent Events, optionally filtered by eventKind (e.g. 'AgentSession').",
        risk="read",
        http_policy=True,
        sdk_method="get_events",
    ),
    ToolDefinition(
        name="tortoise_get_session",
        id="surface.get_session",
        description="Get a single agent session Event by session_id.",
        risk="read",
        http_policy=True,
        sdk_method="get_session",
    ),
    ToolDefinition(
        name="tortoise_index_sessions",
        id="surface.index_sessions",
        description="DEPRECATED — use tortoise_index_files. Index session .md files "
                    "as AgentSession Events. "
                    "EXCLUDED from tenant HTTP — walks server filesystem with user-supplied path.",
        risk="destructive",
        http_policy=False,
        sdk_method="index_sessions",
    ),
    ToolDefinition(
        name="tortoise_index_files",
        id="surface.index_files",
        description="Index a corpus directory of .md files as Sources + Events "
                    "(D10: a document is a Source) (the unified index path — replaces "
                    "tortoise_index_sessions + tortoise_ingest_corpus file semantics "
                    "for local corpora). Returns the honest summary (file_count, "
                    "indexed, updated, skipped, failed, aborted, ignored, errors[], "
                    "by_kind). Idempotent — re-runs converge to skipped. On a shared "
                    "graph, give each corpus a unique corpus_name (default = directory "
                    "basename) to avoid cross-corpus url collisions. "
                    "EXCLUDED from tenant HTTP — walks server filesystem with user-supplied path.",
        risk="destructive",
        http_policy=False,
        sdk_method="index_directory",
    ),
    ToolDefinition(
        name="tortoise_search_sessions",
        id="surface.search_sessions",
        description="Search indexed agent sessions. Returns Events with narrative_arc snippets.",
        risk="read",
        http_policy=True,
        sdk_method="search_sessions",
    ),
    ToolDefinition(
        name="tortoise_create_document",
        id="surface.create_document",
        description="Create a document — a :Source keyed by url (research, planDoc, "
                    "meetingNotes, etc.).",
        risk="write",
        idempotent=True,
        http_policy=True,
        sdk_method="create_document",
    ),
    ToolDefinition(
        name="tortoise_create_source",
        id="surface.create_source",
        description="Create a Source node for provenance (document, web, db, etc.). "
                    "Sources track content origin — url is the permalink key. "
                    "tier (T0-T4 / legacy alias) stores the credibility tier; "
                    "sourceDate is the evidence-age clock.",
        risk="write",
        idempotent=True,
        http_policy=True,
        sdk_method="create_source",
    ),
    ToolDefinition(
        name="tortoise_get_source_reliability",
        id="surface.get_source_reliability",
        description="Derive a Source's reliability (0-1) — query-time, "
                    "cache-consistency-checked. NOTE: refreshes the reliability "
                    "cache on the Source node (write-through projection), so not read-only.",
        risk="write",
        http_policy=True,
        sdk_method="get_source_reliability",
    ),
    ToolDefinition(
        name="tortoise_assess_source",
        id="surface.assess_source",
        description="Record an agent's assessment of a Source (0-1 score + rationale). "
                    "Creates a pointKind='assessment' Statement Point; latest per "
                    "(url, assessor) wins; weighted by assessor reputation.",
        risk="destructive",
        http_policy=True,
        sdk_method="assess_source",
    ),
    ToolDefinition(
        name="tortoise_set_source_tier",
        id="surface.set_source_tier",
        description="Set (or change) a Source's credibility tier (T0-T4). "
                    "Non-destructive — never overwrites sourceKind type strings.",
        risk="destructive",
        http_policy=True,
        sdk_method="set_source_tier",
    ),
    ToolDefinition(
        name="tortoise_get_entity",
        id="surface.get_entity",
        description="Get any entity by ID, eventId, or url. `type` selects the node kind "
                    "(point | entity | operator | events | governance), dispatching exactly as "
                    "the retired per-type getters did; omit it to look the entity up by id. "
                    "The canonical fetch-by-id tool.",
        risk="read",
        http_policy=True,
        sdk_method="get_entity",
    ),
    ToolDefinition(
        name="tortoise_update_entity",
        id="surface.update_entity",
        description="Update any entity's properties.",
        risk="destructive",
        http_policy=True,
        sdk_method="update_entity",
    ),
    ToolDefinition(
        name="tortoise_delete_entity",
        id="surface.delete_entity",
        description="Delete any entity by ID. DESTRUCTIVE — cannot be undone. "
                    "dry_run=True previews the node(s) and every edge that would be "
                    "removed and changes nothing.",
        risk="destructive",
        http_policy=True,
        sdk_method="delete_entity",
    ),
    ToolDefinition(
        name="tortoise_create_entity",
        id="surface.create_entity",
        description="Create an entity — type: subject|object|event|document. "
                    "Event entities wire about* edges from aboutSubject/aboutObject/"
                    "aboutPoint/aboutDocument props. Returns {node, nudges, "
                    "advisories?} — nudges suggest IMPL/NAND/mitigate connections "
                    "to related Points; advisories notes a write that declared no "
                    "source and no provenance (advisory only, never a block). "
                    "For type='document', declaring extractedFrom materializes "
                    "the :Source (#8171); on subject/object/event it is inert "
                    "and an extracted_from_inert advisory is returned.",
        risk="destructive",  # a repeat call re-applies caller props ON MATCH (#9572)
        http_policy=True,
        sdk_method="create_entity",
    ),
    ToolDefinition(
        name="tortoise_update",
        id="surface.update",
        description="Update a Point OR entity by id. Points get point-lifecycle semantics "
                    "(draft→live promote via status — an already-live point is an idempotent "
                    "no-op, version increment for Point:Object, status validation); entities "
                    "get a plain property update.",
        risk="destructive",
        http_policy=True,
        sdk_method="update",
    ),
    ToolDefinition(
        name="tortoise_delete",
        id="surface.delete",
        # #9660 (owner ruling 2026-10-25): ONE delete for every kind, behind a
        # preview-then-confirm protocol — not a family of per-kind delete tools,
        # because every agent that connects re-reads the tool list every session,
        # so each extra tool is a cost every customer pays forever.
        description="Delete one item by id — a Point, a relation (operators are "
                    "Points), a Source, an Object, a Subject, or an Event. "
                    "DESTRUCTIVE and PERMANENT: cannot be undone. The FIRST call "
                    "changes nothing: it previews which node resolves and every "
                    "edge that would go with it, and returns a confirmation token. "
                    "Call again with confirmation=<token> to delete. When the item "
                    "is wrong rather than unwanted, supersede it or retract it "
                    "instead.",
        risk="destructive",
        http_policy=True,
        sdk_method="delete",
    ),
    ToolDefinition(
        name="tortoise_operator_action",
        id="surface.operator_action",
        description="Consolidated operator write action — action=mitigate|annotate. "
                    "mitigate: reason + strength (0-1) — creates/updates the mitigation "
                    "Point (idempotent). annotate: bias/precision/consistency/directness "
                    "(0-1).",
        risk="destructive",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (dispatches to sdk.mitigate_operator / sdk.annotate_operator; #3904)
    ),
    ToolDefinition(
        name="tortoise_create_edge",
        id="surface.create_edge",
        description="Create a typed structural edge between two entities. "
                    "Relation: performs, produces, uses, memberOf, ownedBy, managedBy, "
                    "about*, related, dependsOn, etc. Operator-less per the reification "
                    "rule (v3.5 §8) — lazy promotion via operator_action when mitigation "
                    "is needed. Returns {edge, created, nudges}.",
        risk="write",
        http_policy=True,
        sdk_method="create_edge",
    ),
    ToolDefinition(
        name="tortoise_get_governance",
        id="surface.get_governance",
        description="Get all entities owned by a Subject.",
        risk="read",
        http_policy=True,
        sdk_method="get_owned_entities",
    ),
    # ── Orient / Direct consolidation (epic #888 W3) ────────────────
    ToolDefinition(
        name="tortoise_overview",
        id="surface.overview",
        description="Graph orientation in one call — consolidates the list_*/status/health/"
                    "taxonomy/structure zoo. section: taxonomy|structure|structure_check|"
                    "pointkinds|tags|sources|namespaces|graphs|topics|health|status|stale. "
                    "Omit section → compact combined summary. topics requires entity_id.",
        risk="read",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (dispatches to legacy tools)
    ),
    ToolDefinition(
        name="tortoise_get",
        id="surface.get",
        description="Fetch a node by id — consolidates get_point/get_entity/get_operator/"
                    "get_events/get_session/get_governance. type: point|operator|entity|"
                    "event|session|events|governance. Omitted type → auto-detect by id "
                    "lookup (id|eventId|url, then session_id/sessionId).",
        risk="read",
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py (dispatches to legacy tools)
    ),
    ToolDefinition(
        name="tortoise_backfill_v25",
        id="surface.backfill_v25",
        description="Backfill database to ONTOLOGY v2.5 schema. "
                    "EXCLUDED from tenant HTTP — schema-level migration (operator-only).",
        risk="destructive",
        http_policy=False,
        sdk_method="backfill_v25",
    ),
    # ── Onboarding tools (#498/#499/#500) ─────────────────────────
    ToolDefinition(
        name="tortoise_onboarding_demo_create",
        id="surface.onboarding_demo_create",
        description="Create the demo epistemic graph (4 layers) for this team. Idempotent (Q4).",
        risk="write",
        idempotent=True,
        http_policy=True,
        sdk_method="",  # custom handler in mcp_server.py
        rest_spec=RestSpec(method="POST", path="/v1/demo"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_state",
        id="surface.onboarding_state",
        description="Return this team's onboarding progress (Q6 verification).",
        risk="read",
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="GET", path="/v1/onboarding/state"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_seed",
        id="surface.onboarding_seed",
        description=("File the two onboarding anchor Subjects (Organization/"
                     "organization + User/naturalPerson linked memberOf) — "
                     "interactive, ontology-precise (#1999 W3): call without "
                     "names to discover gaps/collisions, with user-confirmed "
                     "names to file."),
        risk="write",
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="POST", path="/v1/onboarding/seed"),
    ),
    ToolDefinition(
        name="tortoise_manage_sources",
        id="surface.manage_sources",
        description=(
            "Configure the memory sources this team uses (the ones Tortoise "
            "reads FROM). action: list|enable|disable; source: github_issues|"
            "github_documents|agent_sessions. list returns EVERY source with "
            "its current state and its default, in one call. enable/disable "
            "act only within the caller's own permission for this team — a key "
            "without graphs:write is REFUSED, never self-granted — and no "
            "action accepts, returns or echoes a credential: authorising "
            "GitHub stays a host/user-side step (use "
            "tortoise_onboarding_github_connect for that). source is required "
            "for enable/disable and ignored by list. The two GitHub sources "
            "share ONE connection but have SEPARATE enable intent (#1924), so "
            "disable switches off only the named source and leaves the "
            "connection and the sibling source intact; that flag is intent-only "
            "today and the payload says so. "
            "agent_sessions is ON by default (an optional off-switch, #1927): "
            "disable stops recording; enable there reports already-on rather "
            "than changing a recorded default."
        ),
        risk="admin",
        http_policy=True,
        # Custom handler in mcp_server.py, following `tortoise_overview`'s
        # consolidation precedent (one entry point, dispatching). NO `rest_spec`
        # — per the ruling the tool sits ON TOP OF the existing endpoints
        # (POST /v1/index/github, /v1/index/docs, /v1/onboarding/session-recording)
        # and adds no live route of its own.
        sdk_method="",
    ),
    ToolDefinition(
        name="tortoise_onboarding_session_recording",
        id="surface.onboarding_session_recording",
        description="Toggle automatic session recording for this team (Q3).",
        risk="admin",
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="POST", path="/v1/onboarding/session-recording"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_github_connect",
        id="surface.onboarding_github_connect",
        description="Initiate GitHub OAuth — returns authorize URL + CSRF state (Q1).",
        risk="admin",
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="POST", path="/v1/onboarding/github/connect"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_github_index",
        id="surface.onboarding_github_index",
        description="Start background GitHub indexing of an org's issues/PRs (Q2).",
        risk="write",
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="POST", path="/v1/index/github"),
    ),
    ToolDefinition(
        name="tortoise_onboarding_github_status",
        id="surface.onboarding_github_status",
        description="Return GitHub connection status for this team (Q1 verify).",
        risk="read",
        http_policy=True,
        sdk_method="",
        rest_spec=RestSpec(method="GET", path="/v1/onboarding/github/status"),
    ),

]


# ── Derived sets ─────────────────────────────────────────────────

def get_http_allowed() -> frozenset[str]:
    """Derive HTTP_ALLOWED from registry — zero manual sync."""
    return frozenset(t.name for t in TOOL_REGISTRY if t.http_policy)


def get_write_tool_names() -> frozenset[str]:
    """Derive the write-tool name set from each entry's `risk` tier.

    `writes` is a DERIVED permission (#4122): a tool needs `graphs:write` iff its
    tier is `write` / `destructive` / `admin`. This is the set `WRITE_TOOL_NAMES`
    and the read-velocity meter both consume, so a rename or a merge cannot leave
    the permission behind (#4170).

    Covers the SERVED set (#3883): a retired name still answers through the
    warning shim, so a write served under a retired name must not be recorded as
    a read. FOUR retired shims ARE writers (`tortoise_ingest_corpus`,
    `tortoise_index_sessions`, and the two #3540 onboarding retirements
    `tortoise_onboarding_github_index` / `tortoise_onboarding_session_recording`),
    so the served census (48) exceeds the live census (44) — the derivation over
    the served set is what keeps them counted, and it stops the census from
    silently shrinking when a live writer is retired."""
    return frozenset(
        t.name for t in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY) if t.writes
    )


_TOOL_BY_NAME: dict[str, ToolDefinition] | None = None


def get_tool_by_name() -> dict[str, ToolDefinition]:
    """Registry indexed by tool name — the scope gate's single lookup.

    Retired entries are INCLUDED (#3883): a retired name still resolves and is
    still served (through the warning shim), so it must carry a permission the
    gate can read. Omitting them made the gate answer a scoped caller with
    "Unknown tool — denied" instead of the retirement warning.

    Cached: the registry is immutable after import, and this is on the
    per-tool-call hot path."""
    global _TOOL_BY_NAME
    if _TOOL_BY_NAME is None:
        _TOOL_BY_NAME = {t.name: t for t in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY)}
    return _TOOL_BY_NAME


# ── Adapters ────────────────────────────────────────────────────

# ── #9340: the optional `graph` tool argument ─────────────────
#
# The owner's 2026-10-22 decision on #9340 makes the graph a TOOL ARGUMENT,
# not a token claim: a graph is working context inside one workspace, so a
# connector selects it per call and the token stays (user, workspace, scope).
# The resolver + the reachability check live in `tortoise.mcp_auth`
# (`apply_graph_target`); these two declarations are the SURFACE half — which
# tools advertise the argument, and what the schema says it means — and they
# live here because this module owns the tool surface.
#
# **OVERRIDES:** the tenant-level consent scope used by most MCP connectors (a
# `graph_id` claim minted at consent) — see the decision comment on #9340 and
# `mcp_auth.apply_graph_target`'s note; binding the graph at consent would make
# a four-graph account addressable only by four connections and force a
# re-consent on every graph switch.

#: Tools that must NOT advertise (nor honour) the `graph` argument: they
#: already name a graph explicitly (`graph_id`), address the WORKSPACE rather
#: than a graph, or are the discovery surface itself. ONE constant, read by
#: both the schema extension below and the dispatch seam
#: (`mcp_server._take_graph_argument` for the strip + exclusion refusal,
#: `mcp_server._resolve_graph_argument` for the resolve/scope half), so the
#: advertised set and the honoured set cannot drift apart.
#:
#: ⛔ The WORKSPACE-level entries are not a judgement call: each one calls
#: `mcp_server._reject_graph_bound_mcp_org_surface`, which refuses whenever a
#: graph is selected. Advertising `graph` there would mean a connector that
#: selects a graph uniformly (the whole point of the argument) silently loses
#: pack install, sources management and every onboarding tool — and would be
#: told its credential is graph-bound when it is not. `tests/
#: test_9340_graph_tool_argument.py` derives exactly this set from the source
#: and reds if a new `_reject_graph_bound_mcp_org_surface` caller is added
#: without an entry here.
GRAPH_ARGUMENT_EXCLUDED: frozenset[str] = frozenset({
    "tortoise_list_graphs",          # the discovery surface — lists them all
    "tortoise_graph_set_recording",  # names its target with `graph_id`
    "tortoise_org_create",           # provisions the WORKSPACE
    # workspace-level state (packs / sources / onboarding) — see the note above
    "tortoise_pack_install",
    "tortoise_packs_list",
    "tortoise_manage_sources",
    "tortoise_onboarding_state",
    "tortoise_onboarding_seed",
    "tortoise_onboarding_demo_create",
    "tortoise_onboarding_session_recording",
    "tortoise_onboarding_github_connect",
    "tortoise_onboarding_github_status",
    "tortoise_onboarding_github_index",
})

GRAPH_ARGUMENT_DESCRIPTION = (
    "Optional graph to act on. Omitted = the workspace's default graph, and an "
    "explicit `graph=\"default\"` is the same no-op. "
    "Accepts, in order of preference: the graph's id, the namespace string "
    "`tortoise_list_graphs` returns for it (e.g. `org_<workspace>_<graph_id>` "
    "— that tool lists namespaces, not ids), "
    "or its name when that name is unique in this workspace. An unknown, "
    "ambiguous, deleted or other-workspace graph is refused. Any graph OTHER "
    "than `default` needs a HOSTED workspace connection: a stdio or self-host "
    "server has no workspace graph set to resolve a name or id against, so it "
    "refuses one rather than guessing a target."
)


def _with_graph_argument(tool: Any, name: str) -> Any:
    """Advertise the optional `graph` parameter on a registered tool (#9340).

    Applied to the registered Tool's JSON schema, never to the handler's
    signature: `served_attributes` / the code fingerprint in
    `config/surface-manifest.yml` are derived from the HANDLER, and a per-tool
    signature edit would re-stamp every row of an owner-reviewed baseline for
    one uniform argument. The parameter is honoured centrally, at the single
    dispatch point (`mcp_server._take_graph_argument` strips it and refuses it
    on an excluded tool; `mcp_server._resolve_graph_argument` resolves and
    scopes the request), so this declaration and the behaviour cannot disagree.

    `GRAPH_ARGUMENT_EXCLUDED` tools keep their schema untouched; a caller that
    sends the argument to one of them is refused centrally, so an excluded tool
    never silently ignores it.
    """
    if name in GRAPH_ARGUMENT_EXCLUDED:
        return tool
    parameters = dict(getattr(tool, "parameters", None) or {})
    properties = dict(parameters.get("properties") or {})
    if "graph" in properties:
        return tool
    properties["graph"] = {
        "type": "string",
        "description": GRAPH_ARGUMENT_DESCRIPTION,
    }
    parameters["properties"] = properties
    return tool.model_copy(update={"parameters": parameters})


class FastMCPAdapter:
    """Register all TOOL_REGISTRY entries on a FastMCP instance via add_tool().

    Replaces the 58 @mcp.tool() decorators with a single adapter call.
    Tool function bodies remain in mcp_server.py as module-level callables;
    this adapter wraps each via FunctionTool.from_function() and registers
    them through mcp.add_tool().

    Registration is additive-only: entries whose name is ALREADY on the mcp
    instance (stale @mcp.tool() decorators, other registration paths) are
    skipped, never replaced — see register_all (#2204 dedupe).

    Usage (in mcp_server.py):
        adapter = FastMCPAdapter(mcp)
        adapter.register_all(TOOL_REGISTRY, _handler_map)
    """

    def __init__(self, mcp: Any) -> None:
        self._mcp = mcp

    def register_all(self, registry: list[ToolDefinition], handlers: dict[str, Callable]) -> None:
        """Register every registry entry as an MCP tool, skipping names that
        are already registered on the mcp instance.

        The skip (#2204 dedupe) covers stale @mcp.tool() decorator
        registrations and any other registration path that ran first —
        fastmcp's default on_duplicate="warn" would otherwise log
        "Component already exists" and REPLACE the existing tool at import
        time. Skipped entries are still served; they are simply served by
        whichever registration happened first.

        Args:
            registry: TOOL_REGISTRY list of ToolDefinition entries.
            handlers: dict mapping tool_name → callable function body.
        """
        from fastmcp.tools import FunctionTool

        missing = [e.name for e in registry if e.name not in handlers]
        if missing:
            import logging
            logging.getLogger(__name__).warning(
                "FastMCPAdapter: %d registry entries have no handler — skipped: %s",
                len(missing), ", ".join(sorted(missing)),
            )
        for entry in registry:
            handler = handlers.get(entry.name)
            if handler is None:
                continue
            # #2204: skip names already registered on the mcp instance (stale
            # @mcp.tool() decorators / other registration paths). fastmcp's
            # default on_duplicate="warn" logs "Component already exists" and
            # REPLACES — double registration at import is pure noise, and
            # replacing a live tool is never intended when the first
            # registration came from an identical decorator. Sync existence
            # check via the local provider's component store (get_tool is
            # async; _components is keyed "tool:<name>@..." — scanning by
            # component name avoids the key format). getattr-guarded so an
            # upstream layout change degrades to a duplicate warning, never
            # a crash.
            _provider = getattr(self._mcp, "_local_provider", None)
            _components = getattr(_provider, "_components", None)
            if _components is not None:
                _dup = any(
                    getattr(c, "name", None) == entry.name
                    for c in _components.values()
                )
                if _dup:
                    continue
            tool = FunctionTool.from_function(
                handler,
                name=entry.name,
                description=entry.description,
                annotations=entry.annotations,
            )
            tool = _with_graph_argument(tool, entry.name)
            self._mcp.add_tool(tool)


# ── REST endpoint classification (Gate 3 pre-step, #454) ─────────
# 9 REST tool-ops classified for the FastAPIRouterAdapter:
#
# | REST endpoint            | SDK-backed? | Registry entry      | RestSpec            |
# |--------------------------|-------------|---------------------|---------------------|
# | POST /v1/points          | YES         | tortoise_create_point | populated above    |
# | GET  /v1/points          | RAW CYPHER  | (no SDK method)     | — extract list_points |
# | GET  /v1/points/{id}     | RAW CYPHER  | (differs from sdk.get_point) | — extract |
# | POST /v1/dream           | YES         | tortoise_dream      | populated above    |
# | GET  /v1/search          | YES         | tortoise_search     | populated above    |
# | POST /v1/sessions        | RAW CYPHER  | (content_hash dedup bug — filed as tortoise#490) | — extract capture_session |
# | GET  /v1/sessions        | RAW CYPHER  | (no SDK method)     | — extract list_sessions |
# | GET  /v1/context         | YES         | tortoise_session_context | populated above |
# | GET  /v1/issue-insight   | YES         | tortoise_issue_insight | populated above |
#
# Raw-Cypher ops are NOT added to the registry (no SDK method to register).
# Gate 3 pre-step: extract SDK methods for list_points/get_point_by_id/
# capture_session/list_sessions OR keep them hand-written in hosted_api.py
# and document the drift (control-plane-style). capture_session dedup bug
# tracked as tortoise#490.
#
# Control-plane endpoints (/internal/*, /v1/team*, /v1/team/keys) stay
# hand-written — out of registry scope per the epic scope guard.


class FastAPIRouterAdapter:
    """Register REST tool-ops from the registry onto a FastAPI APIRouter.

    Reads entries with rest_spec populated and registers routes. Surface
    policies (audit logging, org limits, dream enqueue) stay in the route
    handlers — the adapter only wires registration.

    Raw-Cypher ops (list_points, get_point, capture_session, list_sessions)
    have NO sdk_method — they are registered via handler_override with their
    existing hosted_api handler functions, OR kept hand-written in
    hosted_api.py with drift documented (Gate 3 pre-step decision).
    """

    def __init__(self, router: Any) -> None:
        self._router = router

    def register_all(self, registry: list[ToolDefinition],
                     handlers: dict[str, Callable]) -> None:
        """Register every rest_spec entry as a route.

        Args:
            registry: TOOL_REGISTRY entries.
            handlers: dict mapping tool_name → FastAPI route handler callable.
        """
        for entry in registry:
            if entry.rest_spec is None:
                continue
            handler = handlers.get(entry.name)
            if handler is None:
                continue
            self._router.add_api_route(
                entry.rest_spec.path,
                handler,
                methods=[entry.rest_spec.method],
                response_model=entry.rest_spec.response_model,
                name=entry.name,
            )


# ── Tool curation groups (#523) ──────────────────────────────────
# Role-scoped grouping so agents can be served a curated subset (tool-selection
# accuracy degrades past ~20 tools). Groups:
#   memory    — point CRUD, queries, confidence, search
#   reasoning — structure checks, EP, analysis, taxonomy, provenance
#   graph     — operators, entities, events, edges
#   sessions  — session context, indexing, search
#   sources   — sources, documents, corpus ingestion
#   journal   — checkpoints, diary, decisions, approvals
#   admin     — status, health, orgs, governance, migrations
#   onboarding — hosted onboarding flows

GROUP_BY_NAME: dict[str, str] = {
    # memory
    "tortoise_create_point": "memory", "tortoise_update_point": "memory",
    "tortoise_update": "memory",
    "tortoise_get_point": "memory", "tortoise_query": "memory",
    "tortoise_paginated_query": "memory", "tortoise_query_points_by_tag": "memory",
    "tortoise_delete_point": "memory", "tortoise_delete": "memory",
    "tortoise_supersede": "memory",
    "tortoise_invalidate": "memory", "tortoise_retract_point": "memory",
    "tortoise_list_tags": "memory",
    "tortoise_list_pointkinds": "memory", "tortoise_search": "memory",
    "tortoise_expand_relationships": "memory",
    "tortoise_recall": "memory",
    "tortoise_issue_insight": "memory",
    "tortoise_mine_conversations": "mining",
    "tortoise_list_dedup_candidates": "review",
    "tortoise_approve_merge": "review",
    "tortoise_promote_point": "review",
    "tortoise_belief_timeline": "memory",
    "tortoise_compute_confidence": "memory", "tortoise_get_confidence": "memory",
    "tortoise_set_point_baseline": "memory", "tortoise_calibrate_summary": "memory",
    "tortoise_suggest_entry_points": "memory",
    # reasoning
    "tortoise_check_structure": "reasoning", "tortoise_validate_domain": "reasoning",
    "tortoise_summarize_structure": "reasoning",
    "tortoise_overview": "reasoning",
    "tortoise_traverse": "reasoning", "tortoise_entity_profile": "reasoning",
    "tortoise_analyze": "reasoning", "tortoise_taxonomy": "reasoning",
    "tortoise_list_topics": "reasoning", "tortoise_provenance": "reasoning",
    "tortoise_stale": "reasoning", "tortoise_dream": "reasoning",
    "tortoise_review_connections": "reasoning",
    "tortoise_find_cross_lens_candidates": "reasoning",
    # graph
    "tortoise_create_operator": "graph", "tortoise_annotate_operator": "graph",
    "tortoise_get_operator": "graph", "tortoise_mitigate_operator": "graph",
    "tortoise_operator_action": "graph",
    "tortoise_get": "graph",
    "tortoise_create_subject": "graph", "tortoise_create_object": "graph",
    "tortoise_create_event": "graph", "tortoise_create_entity": "graph",
    "tortoise_get_events": "graph",
    "tortoise_create_edge": "graph", "tortoise_get_entity": "graph",
    "tortoise_update_entity": "graph", "tortoise_delete_entity": "graph",
    "tortoise_list_sources": "sources", "tortoise_create_source": "sources",
    "tortoise_create_document": "sources", "tortoise_ingest_corpus": "sources",
    "tortoise_ingest": "graph",
    # sessions
    "tortoise_session_context": "sessions", "tortoise_get_session": "sessions",
    "tortoise_index_sessions": "sessions", "tortoise_search_sessions": "sessions",
    "tortoise_list_graphs": "sessions", "tortoise_list_namespaces": "sessions",
    "tortoise_packs_list": "admin", "tortoise_pack_install": "admin",  # #1935
    "tortoise_events_poll": "sessions",  # #432 CDC/subscription — not a memory tool
    # #1727 (Task 13): the session-capture filing tool groups under
    # "sessions" (else it falls to the "memory" default and is filtered out
    # of sessions-group surfaces — pinned by test_session_tool_grouped_sessions).
    "tortoise_session_capture": "sessions",
    # #2302: the per-graph recording override write rides the sessions
    # curation group with capture + graph listing (graph recording settings
    # are session-capture management state).
    "tortoise_graph_set_recording": "sessions",
    # journal
    "tortoise_checkpoint": "journal", "tortoise_diary_write": "journal",
    "tortoise_diary_read": "journal", "tortoise_file_decision": "journal",
    "tortoise_file_human_approval": "journal",
    # admin
    "tortoise_status": "admin", "tortoise_health": "admin",
    "tortoise_org_create": "admin", "tortoise_get_governance": "admin",
    "tortoise_backfill_v25": "admin",
    # onboarding
    "tortoise_onboarding_demo_create": "onboarding", "tortoise_onboarding_state": "onboarding",
    "tortoise_onboarding_seed": "onboarding",  # #1999 (W3)
    # #3540 ruling B: the consolidated source-management tool. The two
    # per-source switches it replaces keep their entries (they are RETIRED via
    # RETIRED_USE_INSTEAD, so they resolve and warn) and therefore keep their
    # group mapping too.
    "tortoise_manage_sources": "onboarding",
    "tortoise_onboarding_session_recording": "onboarding",
    "tortoise_onboarding_github_connect": "onboarding",
    "tortoise_onboarding_github_index": "onboarding",
    "tortoise_onboarding_github_status": "onboarding",
}


# ── Retired names (#3883 / #3863) ─────────────────────────────────────────
# A name in this mapping is RETIRED: it is not in TOOL_REGISTRY, so it is not
# registered as an MCP tool and never appears in `tools/list`. It is NOT gone —
# `_RetiredToolTransform` in mcp_server.py resolves it on `get_tool` and serves
# a shim that answers exactly as the live tool did AND warns the caller, naming
# the replacement. That is the #3836 (b) decision: a retired name keeps working
# and tells us who still calls it.
#
# The handler FUNCTION (mcp_server.py) and the SDK METHOD (sdk.py) both stay: the
# consolidating tool calls the function internally, so retiring the NAME loses no
# capability.
#
# Retiring a name SHRINKS the agent-facing surface, so it is a surface change:
# `tools/surface-guard.py` requires every name here to be recorded in the
# frozen baseline's `retired:` block, and re-cut by a human, exactly as an
# addition requires approval.
RETIRED_USE_INSTEAD: dict[str, str] = {
    # Tier 1 — `tortoise_get_entity` is the canonical fetch-by-id tool; the
    # per-type getters and `tortoise_get` are redundant names for it.
    #
    # ⚠ This direction is an OWNER DECISION, not an implementation preference:
    # `docs/product/canonical-mcp-tools.md` (approved, approval_pr 4120) rules
    # that `tortoise_get_entity` must NOT be retired and that the map must
    # retire `tortoise_get` in its place. Retiring the pair the other way sends
    # every caller of `get_entity` to a name that is itself retired — a churn
    # loop — which is why the pointers below name `tortoise_get_entity`.
    "tortoise_get_point": 'tortoise_get_entity(id, type="point")',
    "tortoise_get": 'tortoise_get_entity(id, type=...)',
    "tortoise_get_events": 'tortoise_get_entity(None, type="events")',
    "tortoise_get_operator": 'tortoise_get_entity(id, type="operator")',
    "tortoise_get_governance": 'tortoise_get_entity(id, type="governance")',
    # Tier 1 — tortoise_overview() already calls the handler; the name is redundant.
    "tortoise_list_pointkinds": 'tortoise_overview(section="pointkinds")',
    "tortoise_list_tags": 'tortoise_overview(section="tags")',
    "tortoise_list_sources": 'tortoise_overview(section="sources")',
    "tortoise_taxonomy": 'tortoise_overview(section="taxonomy")',
    "tortoise_health": 'tortoise_overview(section="health")',
    "tortoise_status": 'tortoise_overview(section="status")',
    "tortoise_stale": 'tortoise_overview(section="stale")',
    # Tier 2 — the entry's own description already named its replacement.
    "tortoise_paginated_query": "tortoise_query(offset=..., limit=...)",
    "tortoise_query_points_by_tag": "tortoise_query(tag=...)",
    "tortoise_index_sessions": "tortoise_index_files(directory)",
    "tortoise_ingest_corpus": "tortoise_index_files(directory)",
    # #3540 ruling B (owner, 2026-10-12): ONE consolidated source-management
    # operation replaces the two per-source switches, so the surface ends one
    # tool FEWER while the number of agent-settable sources grows (the
    # github-documents gap closes through the same tool, without an addition).
    #
    # These two are RETIRED, not deleted: #3883 requires a retired name to keep
    # resolving and to name its replacement, and a deletion would answer "tool
    # not found" instead. `tortoise_onboarding_github_connect` is deliberately
    # NOT here — it is the OAuth handshake, and the ruling's own "credentials
    # never pass through the model" condition is what keeps it out of an
    # agent-callable list/enable/disable operation.
    # NB: no `|` in these values. `RETIRED_USE_INSTEAD` is rendered into
    # markdown table cells (docs/product/mcp-rename-table.md, Part A and Part B),
    # where an unescaped pipe ends the cell — it produced a row with one cell too
    # many and TRUNCATED the value when the table rendered (MD056). The word "or"
    # says the same thing and cannot break a table.
    "tortoise_onboarding_github_index":
        'tortoise_manage_sources(action="enable", source="github_issues")'
        ' (no per-repo scope: it indexes every connected repo)',
    "tortoise_onboarding_session_recording":
        'tortoise_manage_sources(action="enable" or "disable", '
        'source="agent_sessions")',
}


def _apply_groups(entries: list[ToolDefinition]) -> list[ToolDefinition]:
    """Assign curation groups + the retirement pointer to a list of entries."""
    return [
        replace(
            t,
            group=GROUP_BY_NAME.get(t.name, "memory"),
            retired_use_instead=RETIRED_USE_INSTEAD.get(t.name),
        )
        for t in entries
    ]


# The LIVE, advertised surface. Every entry here is registered on both surfaces.
TOOL_REGISTRY = _apply_groups(
    [t for t in _ENTRY_DECLARATIONS if t.name not in RETIRED_USE_INSTEAD]
)
# The RETIRED names: served ONLY through the warning shim, never advertised.
RETIRED_TOOL_REGISTRY = _apply_groups(
    [t for t in _ENTRY_DECLARATIONS if t.name in RETIRED_USE_INSTEAD]
)


def tools_by_group(group: str) -> list[ToolDefinition]:
    """Tools in a curation group (e.g. "memory" — role-scoped server surface)."""
    return [t for t in TOOL_REGISTRY if t.group == group]


def tool_groups() -> dict[str, list[str]]:
    """Group name → tool names (for docs / surface introspection)."""
    out: dict[str, list[str]] = {}
    for t in TOOL_REGISTRY:
        out.setdefault(t.group, []).append(t.name)
    return out


# ── Builder capability catalog (#2004 W8 — epic #1976 WF-6/DM-5/I-7) ──────
# The pullable indexers+extractors catalog a BUILD-fork org sees once on the
# build path. R2-9: lives HERE (no new infra). Registry rows are the canonical
# builder-facing module names (the presented copy — the dashboard fallback
# mirrors them byte-for-byte); each row names the REAL code module(s) that
# implement the capability, and every named module carries the catalog note
# in its module docstring (W8b sweep — tests/test_capability_catalog.py
# TestModuleNoteInventory reds on any missed note).
#
# Mapping evidence (2026-09-02, repo-wide class search): session recording =
# the hosted POST /v1/sessions capture (the SDK's `capture_session` was deleted
# by #4282); session
# extraction = extractor.py (Extractor/LLMExtractor) + extractor_v2.py (5-stage
# pipeline) + session_indexer.py (session-file metadata); document indexing =
# ingest.py (corpus) + file_indexer.py (file identity) + session_indexer.py
# (session .md files).
#
# Deliberately OUTSIDE this sweep (legacy/internal seams, not builder-facing
# capabilities — no note added, keep the list honest if they resurface):
# mining.py (internal batch conversation-mining pipeline over recorded
# sessions), value_extractor.py (extractor-v1 production seam behind
# TORTOISE_EXTRACTOR=v1 in sdk.py), session_import/parsers.py (recorder-side
# importers feeding POST /v1/sessions). A future "extractor" catalog row must
# add each implementing module to its modules tuple + the note to its file.
#
# DELIBERATELY EXCLUDED: the GitHub source indexers (tortoise/indexer/
# github_indexer.py + github_docs.py). They are the SELF-use Settings
# memory-source surface (github_* toggles, epic #1976 scope boundary: webhook
# GitHub ingestion is out of epic scope) — build-fork orgs see THIS catalog,
# not GitHub toggles. If GitHub ingestion becomes a buildable module, add a
# row here + the note to those files.

CATALOG_NOTE = (
    "This module is referenced in the builder capability catalog (onboarding) — "
    "tortoise/tool_registry.py CAPABILITY_CATALOG. If you add or rename an "
    "extractor/indexer, update the catalog reference."
)


@dataclass(frozen=True)
class CatalogModule:
    """One builder-facing data-input module in the capability catalog.

    ``modules`` lists the real code module(s) implementing the capability —
    each file's module docstring carries :data:`CATALOG_NOTE` (W8b sweep;
    inventory test iterates this tuple). ``available=False`` marks a planned
    module (the registry's honest future entry).
    """
    name: str                       # canonical builder-facing name
    kind: str                       # "indexer" | "extractor"
    description: str                # builder-facing one-liner (presented copy)
    modules: tuple[str, ...]        # real module paths (W8b note homes)
    available: bool = True          # False = planned/future


CAPABILITY_CATALOG: tuple[CatalogModule, ...] = (
    CatalogModule(
        name="Session recorder",
        kind="indexer",
        description="Files agent conversations to the graph.",
        modules=("tortoise/sdk.py", "tortoise/hosted_api.py"),
    ),
    CatalogModule(
        name="Session extractor",
        kind="extractor",
        description=("Pulls decisions and findings out of recorded "
                     "sessions."),
        modules=("tortoise/extractor.py", "tortoise/extractor_v2.py",
                 "tortoise/session_indexer.py"),
    ),
    CatalogModule(
        name="Document indexer",
        kind="indexer",
        description="Indexes documents you point your agent at.",
        modules=("tortoise/ingest.py", "tortoise/file_indexer.py",
                 "tortoise/session_indexer.py"),
    ),
    CatalogModule(
        name="Document extractor",
        kind="extractor",
        description=("Extracts claims and decisions from indexed documents "
                     "(planned)."),
        modules=(),
        available=False,
    ),
)


def capability_catalog() -> list[dict]:
    """Pullable catalog rows — the GET /v1/capabilities payload (#2004 W8).

    Returns the canonical module list (presented once on the build path):
    ``[{name, kind, description, available}, ...]``. Names/kinds/descriptions
    are the source of truth the dashboard fallback mirrors — rename here AND
    in the module docstrings (W8b), never one without the other.
    """
    return [
        {"name": m.name, "kind": m.kind, "description": m.description,
         "available": m.available}
        for m in CAPABILITY_CATALOG
    ]
