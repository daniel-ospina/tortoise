---
name: how-to-use-tortoise
description: Create points, operators, mitigations, NANDs, supersede, delete, and annotate on the Tortoise graph. Use when asked to write to the Tortoise graph — creating points, operators, mitigations, NAND edges, superseding points, deleting, or annotating.
domain: capability
type: Workflow
status: live
tags: [tortoise, graph, epistemology, knowledge-graph, operations, search, entity-type, pack, decision-comparison, discovery]
summary: "Safe Tortoise graph write operations — teaches edge semantics (IMPL vs NAND), mitigation ranges, supersession cleanup, sourceKind taxonomy, annotation rules, decision comparison workflow, structural discovery, DB URI reality, and ID fragmentation."
created: 2026-07-14
updated: 2026-08-05
allowed-tools: read write edit bash grep find
---

> ⛔ **This skill MUST be read in full before any Tortoise graph write.** Skipping corrupts the graph.

# How to Use Tortoise

Safe graph write operations for the Tortoise probabilistic inference graph.

## Hard Gate

**Any graph write** (create_point, create_operator, mitigate_operator, supersede_point, delete_point, annotate_point, invalidate_point) **MUST** go through this skill. Bypassing it risks:
- EP weights nuked by batch-connected mitigations
- Orphaned NAND edges with no cleanup
- Superseded operators with active edges still propagating
- Label-based content instead of structural claims
- Criteria with no provenance — untethered from the customer needs they serve

## Edge Types

| Type | Semantics | Use When |
|------|-----------|----------|
| **IMPL** | A implies/supports B | Evidence supports a claim |
| **NAND** | A contradicts B | Evidence conflicts with a claim |

## Mitigation Ranges

Mitigations reduce claim confidence. Range: **0.10–0.50**.

- 0.10: Minor caveat (claim is mostly true)
- 0.30: Significant limitation
- 0.50: Major counter-evidence (claim is substantially weakened)

Never use <0.10 (negligible) or >0.50 (would invert the claim — use NAND instead).

## The Critical Semantic: Truth vs Weight

When a claim faces challenge, you have two tools. They address different things:

| | NAND | mitigatedBy |
|---|------|-------------|
| **What it says** | "This claim is FALSE" | "This claim is TRUE but matters LESS than it seems" |
| **Dimension** | Correctness | Relevance |
| **Effect on EP** | Contradiction propagates through graph | Confidence reduction on the edge |
| **Applies to** | The argument Point directly | The operator (IMPL connection) between argument and what it supports |

**The golden rule:** Relevance lives on the OPERATOR, truth lives on the POINT.

- An option IS an option (it's true that it's a candidate); a criterion IS a criterion — **never NAND an option or criterion for being a bad fit.** A bad fit is a relevance problem, not a truth problem.
- If a FINDING is factually untrue → NAND the finding Point directly (truth attack).
- If a finding is TRUE but IRRELEVANT to the option/criterion → NAND/IMPL the OPERATOR between them, or mitigate the operator (relevance attack, range 0.10–0.50).
- If an OPTION doesn't satisfy a CRITERION well → NAND the IMPL OPERATOR between them, or mitigate it — never NAND the option or criterion points themselves.

### Worked Example: Option ↔ Criterion

**Setup:** "Option:AGPLv3-dual-license" should score well on "Criterion:adoption" (OSI-approved status). Finding says "AGPLv3 is OSI-approved."

| Scenario | What you do | Why |
|----------|-------------|-----|
| "Finding:agpl-osi" says AGPLv3 is OSI-approved (TRUE) | Create IMPL from finding → option:agplv3-dual | Truth + relevance both correct |
| "Finding:agpl-enterprise-risk" says enterprises ban AGPL (TRUE) | Create NAND from finding → option:agplv3-dual | True fact that COUNTERS the option — NAND on the OPERATOR (finding opposes option), not on the option point itself |
| "Finding:bsl-osi-gap" says BSL is OSI-approved (FALSE — it's not) | NAND the finding Point directly | The finding is factually wrong — truth attack on the Point |
| Option:agplv3-dual doesn't fit Criterion:stack-compat well (Apache 2.0 compat question) | NAND the operator between criterion and option, or mitigate it at 0.30 | The option and criterion are both true; the RELATIONSHIP is weak — relevance attack on the operator |

### Quick Decision Tree

```
Challenge received:
├─ Claim is FACTUALLY WRONG? → NAND the Point directly (truth attack)
├─ Claim is TRUE but OVERSTATED? → mitigate_operator on the edge (relevance attack, 0.10–0.50)
├─ Claim is TRUE but IRRELEVANT to what it's connected to? → NAND the operator (relevance attack)
└─ Option is a BAD FIT for a criterion? → NAND/mitigate the operator between them, never the option or criterion points
```

**Original example (argument↔option):** "A1: Provider cannot read content" supports Option A via IMPL.

- **NAND:** "Metadata reveals topics, so the provider CAN infer some content." → This says A1 is categorically wrong. NAND the A1 point.
- **mitigatedBy (0.20):** "Metadata is lossy, like email subjects — visible but not the full content." → This says A1 is true, but the privacy claim is weaker than it sounds. Mitigate the IMPL operator.

**Rule of thumb:** If you're saying the argument is wrong → NAND. If you're saying it's overstated or irrelevant → mitigatedBy or NAND the operator.

## SourceKind Taxonomy

| Tier | Label | Description |
|------|-------|-------------|
| T0 | Direct Observation | First-hand empirical evidence |
| T1 | Primary Source | Original document, raw data |
| T2 | Secondary Source | Analysis, interpretation of primary sources |
| T3 | Tertiary Source | Synthesis, summaries, encyclopedias |
| T4 | Speculative | Hypothetical, unverified claims |

## Decision Provenance: Tracing Criteria to Customer Needs

A decision's criteria shouldn't float. They should trace back to who needs them and why. The chain flows from customer-facing needs down to architecture choices:

```
[domain concepts from expansion packs] → Criterion → Argument → Option
```

Each link is an IMPL connection. The specific pointKinds in the chain depend on the expansion packs loaded. Load the packs, check their registered kinds and relations, and wire the chain accordingly.

The chain is auditable in both directions:
- **Downward:** "which customer need does this criterion serve?" → traverse IMPL up
- **Upward:** "which decisions does this requirement drive?" → traverse IMPL down

An agent auditing an ADR can walk the full chain: understand not just what was decided, but why the criteria exist, who they serve, and whether the decision holds up if those customer needs change.

## Supersession

When superseding a point:
1. Create the new point with updated content
2. Call `supersede_point(old_id, new_id)` — this cleans up edges
3. Verify old point's edges are properly transferred

## Annotation Rules

- Annotations describe WHY an edge exists (rationale, not label)
- Use sentence-case, be specific
- Never annotate with just a label (e.g., "evidence" — say what evidence)

## Common Mistakes

| Mistake | Consequence | Correct |
|---------|-------------|---------|
| Batch-connecting mitigations | EP weights cascade-nuked | Connect mitigations one at a time, verify each |
| NAND without checking existing IMPL | Orphaned contradiction | Check for existing IMPL edges before adding NAND |
| Superseding without edge cleanup | Stale edges from old point still propagate | Always call supersede_point, never manually move edges |
| Label-based annotation | Unreadable graph | Write rationale sentences, not keywords |

## Pre-Write Checklist

- [ ] Have I read the target point's current edges?
- [ ] Am I using the correct edge type (IMPL vs NAND)?
- [ ] If mitigation: is the value in 0.10–0.50 range?
- [ ] If NAND: am I attacking truth (NAND the Point) or relevance (NAND/mitigate the Operator)?
- [ ] Am I about to NAND an option or criterion? STOP — options and criteria are true by definition. Attack the operator instead.
- [ ] If superseding: will I call supersede_point (not manually move edges)?
- [ ] Are my annotations sentence-case rationales, not labels?

---
> After writing, verify with tortoise-verify-chain to ensure graph integrity.

# Decision Comparison Workflow

The most common high-value agent operation: comparing options against weighted criteria using evidence, then ranking them by EP belief propagation.

## Pattern

```
1. Define criteria (evaluation dimensions)
2. Define options (candidates)
3. Gather findings (evidence points — research, observations, data)
4. Wire findings/criteria → options via IMPL (supports) or NAND (opposes)
5. Run compute_confidence
6. Read per-option confidence, ranked highest → lowest
```

**Key insight:** EP propagates belief from evidence through operators to options. More IMPL → higher confidence. More NAND → lower confidence. Mitigated operators → dampened influence.

## Anchors Convention

Decision comparisons use explicit anchor lists to scope EP propagation. Collect all option IDs into a list and pass them to `compute_confidence(anchors=...)`.

## Code Pattern

```python
from tortoise.sdk import TortoiseSDK

sdk = TortoiseSDK()

# 1. Criteria — evaluation dimensions (pointKind: "criterion")
criteria = {
    "crit:adoption": "OSI-approved / recognizable open source status",
    "crit:enterprise": "Enterprise procurement acceptability",
}
# 2. Options — candidates (pointKind: "option")
options = {
    "opt:agpl": "AGPLv3 license",
    "opt:mit": "MIT license",
}
# 3. Findings — evidence (pointKind: "evidence")
findings = {
    "finding:1": "AGPL is OSI-approved",
    "finding:2": "Enterprises ban AGPL internally",
}

# 4. Create all points — no context param needed (namespace is pointKind-based)
all_points = {**criteria, **options, **findings}
point_ids = {}
for pid, content in all_points.items():
    kind = "criterion" if pid.startswith("crit:") else (
        "option" if pid.startswith("opt:") else "evidence"
    )
    p = sdk.create_point(kind, content)
    point_ids[pid] = p["id"]

# 5. Wire edges (findings/criteria → options)
edges = [
    # Criterion supports option
    ("crit:adoption", "IMPL", "opt:agpl"),
    # Finding supports option
    ("finding:1", "IMPL", "opt:agpl"),
    # Finding opposes option
    ("finding:2", "NAND", "opt:agpl"),
]
for src, op_type, tgt in edges:
    sdk.create_operator(op_type, point_ids[src], [point_ids[tgt]])

# 6. Compute confidence via anchors (options are the anchor set)
option_ids = [point_ids[pid] for pid in options]
result = sdk.compute_confidence(anchors=option_ids, max_hops=2, direction="incoming")
confs = result.get("confidences", {})

# 7. Rank options
ranked = sorted(
    [(pid, confs.get(point_ids[pid], {}).get("mean", 0))
     for pid in options],
    key=lambda x: x[1], reverse=True
)
for pid, confidence in ranked:
    print(f"  {pid}: {confidence:.4f}")
```

## Link-Before-Create Rule

Before creating a new evidence point, search whether it already exists:
- Use `tortoise_search` with the evidence claim as query
- Use `create_point` with `dedup=True` (idempotent — returns existing if content matches)
- This prevents duplicate evidence points that fragment EP propagation

## Worked Examples

### `graph-scripts/file_pricing_decision.py`
Compares Pro/Team pricing options ($29/$49/$79) using criteria (competitor positioning, conversion rate, ARPU) and findings (devtool sweetspot, OSS conversion rates). Wires IMPL to chosen options, NAND to rejected ones. EP computes per-option confidence.

### `graph-scripts/decide_licensing.py`
Compares 3 license options (AGPLv3-dual, BSL+AGPL, SSPL) using 7 criteria and 20+ findings. Full pattern: criteria → options → findings → edges → compute_confidence → ranked output. Run as:

```bash
TORTOISE_DB_URI=docker://:@localhost:16379/tortoise python3 graph-scripts/decide_licensing.py
```

### `graph-scripts/decide.py` (future — Issue #43)
Generic decision comparison tool. Will accept criteria/options/findings as structured input and automate the create→wire→compute→rank pipeline. Until it lands, follow the pattern above manually.

## Decision Comparison Checklist

- [ ] Option IDs collected in an anchors list for `compute_confidence(anchors=...)`
- [ ] Criteria points created with kind=`"criterion"`
- [ ] Option points created with kind=`"option"`
- [ ] Evidence/finding points created with kind=`"evidence"`
- [ ] Each edge wired: finding/criterion → option via IMPL (support) or NAND (oppose)
- [ ] `compute_confidence(anchors=[option_ids], max_hops=2, direction="incoming")` called after all edges
- [ ] Options ranked by `confidence_mean` descending
- [ ] ✅ CORRECT: NAND on operator when option opposes criterion
- [ ] ❌ WRONG: NAND on option/criterion points themselves — they ARE options/criteria

---

# Searching the Graph

Tortoise supports two search modes for different use cases.

## Two Search Modes

| Mode | Tool | What It Does | When to Use |
|------|------|-------------|-------------|
| **Full-scan** | `tortoise_query` (kind only, no query) | Returns ALL Points of a given pointKind | Graph review, finding weak spots, integrity checks, duplicate detection |
| **Best-match** | `tortoise_search` (with query string) | Returns top-N Points ranked by RRF fusion | Agent context retrieval, entity resolution, "what does the graph believe about X?" |

**Key rule:** Full-scan mode **never filters by confidence** — low-confidence points are exactly what reviewers need to see. Best-match mode annotates confidence but defaults to no filter.

## Which Tool to Use

| Tool | Use When |
|------|----------|
| `tortoise_search` | You have a text query and want ranked, relevant results. Returns RRF-fused results from FTS + vector + structural indexes with full EP breakdown. |
| `tortoise_query` | You want to filter by kind without text search. Use `text` param for hybrid search. Supports `order_by` (relevance/confidence) and `min_confidence`. |
| `tortoise_suggest_entry_points` | You need to resolve an entity name from natural language (e.g., "what entities relate to pricing?"). Uses hybrid search for semantic matching. |

## EP Breakdown Fields

Every search result includes an `ep` object with:

| Field | Meaning | Range |
|-------|---------|-------|
| `confidence_mean` | EP Beta posterior mean — how confident the graph is | 0.0–1.0 |
| `evidence.impl_count` | Number of IMPL (supporting) edges | 0+ |
| `evidence.nand_count` | Number of NAND (contradicting) edges | 0+ |
| `evidence.total` | Total edge count (impl + nand) | 0+ |
| `contention` | Ratio of NAND to total — how disputed the claim is | 0.0–1.0 |

**Interpreting confidence:**
- 0.50 mean + low total evidence (total < 5) = **uncertainty** — not enough data yet
- 0.50 mean + high total evidence (total > 10) + high contention (> 0.3) = **disagreement** — strong opposing views
- 0.85 mean + high total evidence = **settled** — strong supporting evidence

## Ordering and Filtering

- `order_by="relevance"` (default): Results ranked by RRF fusion score — best semantic + keyword match
- `order_by="confidence"`: Results sorted by EP confidence_mean descending — highest-confidence claims first
- `min_confidence=0.5`: Only return Points with confidence_mean ≥ 0.5 (default: 0.0 = no filter)

**When to filter by confidence:** Use when you need settled claims for decision-making. Do NOT use when reviewing the graph for weak spots — you need to see low-confidence points to identify what needs verification.

## Entity Types

`tortoise_search` and `tortoise_query` support cross-entity search via `entity_type`:

| entity_type | Searches | ID Field | Kind Field | FTS Index |
|------------|----------|----------|------------|-----------|
| `point` (default) | Point nodes | `id` | `pointKind` | `Point` |
| `event` | Event nodes | `eventId` | `eventKind` | `Event` |
| `subject` | Subject nodes | `id` | `subjectKind` | `Subject` |

Non-Point entities skip EP annotation (no confidence breakdown in results).

## Pack-Aware Search

Kind values are expanded through pack registries before queries execute. When you search for a kind like `WorkItem`, the system automatically includes:

- **subclassOf**: Subclasses registered in packs (e.g., `dev:issue`, `pm:task`)
- **equivalentTo**: Bidirectional equivalents (e.g., `dev:issue` ↔ `pm:task`)

This means `tortoise_search(query="auth bug", kind="WorkItem")` matches both dev and PM work items automatically. No manual kind expansion needed.

Pack relations are queryable via `list_relations()`, which returns declared edge schemas with fromKind/toKind/mechanism triplets across all installed packs.

## Degradation Behavior

Hybrid search degrades gracefully when indexes are unavailable:

| Strategy | When Available | Degradation |
|----------|---------------|-------------|
| **FTS** | FalkorDB fulltext index exists | Skipped silently if index missing |
| **Vector** | Embedding model + vector index | Falls back to brute-force Euclidean distance; skips if no embeddings |
| **Structural** | Always available | N/A — uses native property filters |
| **TF-IDF** | Last resort (in-memory) | Only triggered if all FalkorDB strategies fail |

A 500ms timeout caps all strategies. If all FalkorDB strategies fail, the system falls back to in-memory TF-IDF for Point queries. For Event/Subject queries without FTS indexes, only structural (kind) filtering is available.

## Creating Relationships with Semantic Labels

When connecting Points with operators, use the `label` parameter to add domain context. **Direction is an explicit flag, not derived from the label** (ONTOLOGY v3.1 §3.1, §8 — #189):

| Semantic Type | Mechanism | Typical Direction | When to Use |
|--------------|-----------|-------------------|-------------|
| **hasPart** | IMPL | bidirectional (parts↔whole) | Composition — "Epic hasPart Issue", "Product hasPart Feature" |
| **addresses** | IMPL | unidirectional (A→B) | "Feature addresses Need", "Task implements Feature" |
| **opposes** | NAND | unidirectional (A→B) | "Feature competesWith Competitor", "Issue blocks Issue" |

**Direction is explicit, not label-derived.** `create_operator(..., direction="bidirectional"|"unidirectional")` controls EP back-propagation: `bidirectional` (default) propagates both ways; `unidirectional` is source→target only. The `label` carries domain semantics only — EP reads `direction`, never the label. NAND defaults to bidirectional but also supports unidirectional.

**Backward compatibility:** existing operators without a `direction` property default to bidirectional in EP (with a warning log). Run `graph-scripts/migrate_direction.py` to backfill `direction` on existing operators per their old semantics (NAND / hasPart / part-of → bidirectional; other IMPL → unidirectional).

**Strength:** Set via `set_point_baseline(operator_id, alpha, beta)`. High alpha = strong support. High beta = strong contradiction.

```python
# Create a relationship with semantic context + explicit direction
op = sdk.create_operator("IMPL", feature_id, [need_id], label="addresses", direction="unidirectional")
sdk.set_point_baseline(op["id"], alpha=10, beta=1)  # strong support
```

---

# Discovery

Points are organized by `pointKind` (structural type) and source provenance (`extractedFrom` edges). Use these APIs to discover what's in the graph before searching or filing decisions.

## Listing PointKinds

```python
sdk.list_pointkinds()
# or via MCP: tortoise_list_pointkinds()
```

Returns `[{pointKind, count}, ...]` ordered by count DESC — what structural types actually EXIST in the graph.

## Listing Sources

```python
sdk.list_sources()
# or via MCP: tortoise_list_sources()
```

Returns sources grouped by `sourceKind` with point counts — where data came FROM.

## Pack Registry

Packs declare what KINDS and RELATIONS can exist:

```python
# What edge types are registered across all installed packs?
sdk.list_relations()  # returns fromKind/toKind/mechanism triplets

# What kinds does a pack declare (including subclass/equivalence expansion)?
sdk.expand_kind("WorkItem")  # returns ["dev:issue", "pm:task", ...]
```

## Discovery Checklist

- [ ] Ran `list_pointkinds()` before creating new points — know what structural types exist
- [ ] Ran `list_sources()` if the data has provenance — know where existing data came from
- [ ] Used pack-registered kinds for new points (check `list_relations()` / `expand_kind()`) 
- [ ] For decision comparisons: collected option IDs in an anchors list for `compute_confidence(anchors=...)`

---

# DB URI Reality

**The DB target is explicit, never accidental, and local stays local.** A self-hosted/local instance is intentionally local — local tooling (MCP server, SDK, graph-scripts) targets the local FalkorDB, never a remote cloud DB. The **hosted version** is a separate product: the Fly API resolves `FALKORDB_CLOUD_URI` → `TORTOISE_DB_URI` via entrypoint.sh and refuses to start without it; clients reach it over HTTP with API keys.

| Source | URI resolution | Behavior when unset |
|--------|----------------|---------------------|
| **MCP server (local)** | `TORTOISE_DB_URI` in `.mcp.json` — defaults to local `docker://:@localhost:16379/tortoise`; a repo-root `.env` only fills keys `.mcp.json` does **not** set (useful for SDK scripts / direct launches — `.mcp.json` env always wins for the MCP server) | Fails loud on startup (exit 1) — never silently connects to an empty embedded graph (`TORTOISE_ALLOW_EMBEDDED=1` is the test-only escape hatch) |
| **SDK / graph-scripts** | `os.environ["TORTOISE_DB_URI"]` (or `FalkorProjection.from_uri`) | Embedded redislite (dev/test only) |
| **Hosted API (Fly)** | `FALKORDB_CLOUD_URI` secret → `TORTOISE_DB_URI` via entrypoint.sh | Refuses to start on Fly |

Supported URI schemes: `docker://` (local), `redis://` / `rediss://` (accepted so the hosted API can consume cloud connection strings).

## Always Verify First

Before any graph operation, confirm you're on the right database:

```python
# Via SDK
sdk.taxonomy()  # or tortoise_status() — returns point counts, pointKinds, graph name
```

Or via MCP:
```
tortoise_summarize_structure  # returns {gateN_*, total}; zero total on an empty/wrong graph
```

## Common Failure Mode

```
Agent: "Let me search for licensing evidence..."
       (TORTOISE_DB_URI unset → MCP server refused to start, or a script fell back
        to embedded/empty redislite)
Agent: "The graph has no licensing data. I'll create evidence from scratch."
       (files 20+ duplicate points on the wrong graph)
```

**Fix:** point local tooling at the local FalkorDB. The default in `.mcp.json` is `docker://:@localhost:16379/tortoise` (the designated local container); override in the repo-root `.env` (gitignored — never commit credentials) if your local target differs.

```bash
# .env (repo root, gitignored) — LOCAL target only
TORTOISE_DB_URI=docker://:@localhost:16379/tortoise
```

Restart the MCP server after changing the URI — the connection is resolved once at startup. Do **not** point local tooling at the hosted (cloud) instance.

## SDK Props Convention

`create_point(kind, content, **props)` takes **flattened** kwargs. Direct SDK callers may pass a nested `props={"k": v}` dict (mirroring the MCP tool signature) — the SDK now flattens it automatically (#218). A dict-valued `props` property is illegal in FalkorDB ("Property values can only be of primitive types"), so the flatten is the single place handling both conventions.

---

# ID Fragmentation

**Point IDs are NOT uniformly ULIDs. Don't assume the ID format.**

| Format | Example | Origin |
|--------|---------|--------|
| **ULID** (canonical) | `01JR8KZ5V7X2M3N4P6Q8R9T0W1` | `create_point` with ULID generation |
| **19fc-hash** | `19fc8a3b4c5d6e7f8a9b0c1d2e3f4a5b` | Early creation path |
| **Numeric** | `42`, `1337` | Legacy/bulk-imported points |
| **Prefixed** | `letta-abc123`, `op-xyz789`, `ARCH_001`, `SVBP_002` | Legacy systems, specific graph-scripts |

## What This Means

- **Always use the returned `id` property** — never construct or guess an ID
- **Store IDs, not derive them** — if you need to reference a point later, save its `id` from the response
- **Search by content, not ID** — `dedup=True` and `tortoise_search` handle resolution; you don't need to know the format

## Related Issues

- **#44 (FIXED):** `traverse` used to leak internal FalkorDB node IDs instead of public Point IDs. Fixed — traverse now returns the canonical `id` property.
- **#52 (DONE):** Audit script validates ID format consistency across the graph and ULID validation in `create_point`.

---

> Continue following the workflow as mandated by this skill. Do not skip steps.

# Ingest Workflow (epic #902 — the bundle surface)

## The Bundle Shape

`tortoise_ingest` / `sdk.ingest` takes ONE heterogeneous bundle with four
sections: `points` (statements — `kind` defaults to `statement`; `event` is
NOT a write kind — use an ENTITY item `type:"event"` for episodic records),
`entities` (subject/object/event/document), `sources` (url + sourceKind),
and `connections` (operator/relation edges between them).

```json
{
  "points": [{"ref": "p1", "kind": "statement", "content": "A implies B."}],
  "entities": [{"ref": "s1", "type": "subject", "name": "Acme"}],
  "sources": [{"ref": "src1", "url": "https://example.com/r", "sourceKind": "report"}],
  "connections": [{"ref": "c1", "from": "p1", "to": "s1", "relation": "authoredBy"}]
}
```

`granularity`: `"bulk"` (default — aggregated counts) or `"granular"`
(adds a per-item `results` array). The response is the SAME key set either
way: `{granularity, batch_id, created, deduped, ids, nudges, warnings}`
(+ `results` for granular); `created`/`deduped` count
`{points, entities, sources, connections}`; `ids` carries
`{points, entities, sources, connections, refs}`.

## Promotion policy: gated (default) vs auto

`promotion_policy` is a kwarg-only param, orthogonal to granularity.
**`"gated"` is the DEFAULT**: bundle points stay `draft` — promotion is
explicit, NEVER automatic. An explicit `status:"live"` on a point item
under gated is a VIOLATION (row 9 — case variants, nested `props:{...}`,
and terminal statuses too). The sanctioned promotion routes:

- **Interim route (pre-#785):** `tortoise_update_point(id, props={"status":"live"})`
  — the guarded draft→live promote (single point). Caveat: it does NOT
  resolve ZOMBIE draft operators (an operator whose endpoints are all live
  stays inert until IT is explicitly promoted; auto-resolution ships with
  the #785 promote tools).
- `promotion_policy="auto"` opts into #131 parity (source auto-promotion on
  first edge write) — the migration path from the old default.

The response carries `warnings` (never fatal) — see the ELEVEN-key table.

## Idempotent resubmission

The identical bundle re-submission is SAFE: it derives the SAME
content-derived `batch_id` and returns everything in `deduped` (exactly-once
convergence — no duplicate points/operators/edges). Use it after a transport
death (no response): re-send the identical bundle; the response confirms
presence. Crash-retry semantics: an item that crashed mid-write is absorbed
by the retry (content+kind fallback scan), never duplicated.

## Retry table (error → action)

| Error | Meaning | Action |
|---|---|---|
| `ERR_BUNDLE_INVALID` `{error, code, violations[]}` | Phase-1 validation failed; zero mutations | Fix the bundle; NEVER re-send unchanged |
| `ERR_INVALID` `{error, code}` | Bad param (granularity/promotion_policy) or the row-9 gated guard | Fix the param; the message names valid values |
| `ERR_QUOTA` `{error, code}` | Team cap reached (pre-write count-then-act) | Stop; escalate. Resubmit ONCE after headroom |
| Transport death — no response | The call may have committed anything | Re-send the identical bundle (exactly-once) |

## Route decision table (connections)

| Connection | Route | Result |
|---|---|---|
| Plain `operator: IMPL/NAND` | Direct edge (operator-less) | `{"direct_edge", "from", "to", "deduped"}` |
| `mitigation` / `reify: true` | Operator point | `{"operator_id", "deduped"}` |
| `relation` | Structural edge | `{"relation", "from", "to", "deduped"}` |

Multi-item `to` on a plain IMPL/NAND connection: use `reify:true` /
mitigation (the operator route) or split into singular connections.

## §6.6 behavior changes (migration lines)

- **Default policy flipped auto→gated** — `promotion_policy="auto"` is the
  opt-in parity mode (migration).
- **Multi-item `to` on plain IMPL/NAND** becomes a Phase-1 rejection —
  migrate via `reify:true`/mitigation or split.
- **Same-pair plain connections differing only in `label`** become Phase-1
  rejections — migrate via `reify:true` ×2 (the operator route preserves
  both labels).

## Warnings — the ELEVEN keys (closed set)

`warnings` entries are key-prefixed strings. A key outside this table is a
divergence — report it:

`append_only_items` · `modified_item_residue` · `mitigation_orphan_residue`
· `mitigation_drift_duplicate` · `nfc_straddle_duplicate` ·
`mitigation_strength_change` · `partial_operator_residue` ·
`operator_absorb_completed` · `label_dropped_resubmit` ·
`direction_dropped_resubmit` · `direction_changed_resubmit`

## Query-back verification (J8 exit state)

After ingest, VERIFY what you wrote: `recall_subgraph(seed)` /
`tortoise_query` reach the ingested knowledge (promoted/live content), and
`list_batch(batch_id)` audits the exact stamped artifact set (points +
direct edges; entities/sources are out of stamp scope). Keep bundles
reasonably sized (large bundles are slower to validate — split when a
bundle exceeds a few hundred items).
