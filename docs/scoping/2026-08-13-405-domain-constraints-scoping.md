---
title: "Scoping — #405: Domain Integrity Constraint System"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# Scoping — #405: Domain Integrity Constraint System

> **Issue:** daniel-ospina/tortoise#405 (re-scoped 2026-08-13, tier standard)
> **Epic:** docs/epics/2026-07-14-memory-system/04-plan.md
> **Skill:** issue-scoping v5.1.0 (double diamond + verify gates) — STREAMLINED mode (3rd attempt, time-critical)
> **Gate log:** problem-verify 2 verifiers (PASS, P1s fixed inline) · solution-verify 2 verifiers (FAIL → controller tiebreaker re-converged, P0/P1s fixed inline) · Coherence: `[QWEN-GATE] substitute reviewer used` — qwen3.8-max coherence review BLOCKED (time-critical mode; solution-verify verifiers covered coherence)

---

## Confirmed Problem

**Root cause (not symptom):** Tortoise has **three live, divergent chain-definition surfaces** for domain ontology integrity, and **zero unified enforcement**:

1. **Pack manifest v3 `chains[]`** (packs/product-strategy/manifest.yaml) — `productDelivery` steps `[useCase, feature, userJourney, workflow, requirement, architecture]`, **JTBD omitted**; chain-level `enforcement: warn`, kind-level `useCase: retry, userJourney: retry`. Declared but **never enforced in-repo**.
2. **`sdk.check_structure()`** (tortoise/sdk.py:1813) — hardcoded, in-repo chain checker anchored on **jobToBeDone**: `orphan_use_case` (JTBD parent via `composedOf`/`hasPart` operator + pack-aware `_expand_kind`), `dangling_use_case_ref` / `dangling_jtbd_ref` / `dangling_workflow_ref` (property-ref resolution on `covered_use_cases`/`enables_jtbd`/`enabled_workflow` string fields), `orphaned_draft` (status check). Exposed via mcp_server alias + `tool_registry` (`tortoise_check_structure`).
3. **`tortoise-verify-chain` skill** — "JTBD → useCase → userJourney → workflow → requirement" + hosted-MCP tool names (`tortoise_verify_chain`/`tortoise_get_chain_status`) that **do not exist in this repo** (skill is already drifting from the surface).

**The commit path cannot enforce graph-global rules.** `commit_schema.validate_layer1(payload, vocab)` is pure payload-local schema conformance (caps, closed vocab, referential integrity, atomicity, MITIGATES shape) with **no graph state** — a useCase's JTBD parent may have been committed in an earlier payload. Per the research brief (three-gates principle: *"cheapest check at earliest gate with enough information"*; SHACL: definition separated from enforcement, warnings don't block unless mandatory, persist-and-flag is a valid state; mammoth-transaction caution → warn-only in commit path): **write-time enforcement splits** — payload-local rules at commit (Phase A warn; Phase B block only for a small deterministic set), graph-global rules where graph state exists (CLI + MCP).

**What remains (the 3 bullets, confirmed against code):**
1. Constraint-registration API on `domain_loader` — does not exist (kind adapter only: `known_kinds`/`register_kind`/`domain_kinds`).
2. Write-time enforcement in commit path — `validate_payload_dict` (commit_schema.py:585) is the chokepoint but has **zero production call sites** (tests only; commit endpoint unmerged, slice-5 worktree) → greenfield wiring.
3. `tortoise validate --domain <slug>` CLI — no `validate` subcommand in `__main__.py` (name free; `verify`/`check-consistency` are unrelated).

**Anti-goal:** full SHACL-style shapes engine / rule DSL — over-engineering for standard tier (Mem0 graph-machinery cost caution).

**Confidence: HIGH** (brief 3-gates + SHACL severity + direct code evidence: `enforcement_for_chain` resolver exists at pack_registry.py:249, `VALID_ENFORCEMENT_LEVELS={"warn","retry","block"}` at :102, `check_structure` in-repo, no validator registry, no validate subcommand, no domain field on CommitPayload).

---

## Verification Gates

### problem-verify (2 parallel verifiers, fresh context)
- **Cycle 1:** First dispatch FAILED at infrastructure (sub-agent startup: API-key blocked, no verdict). Re-dispatched once with default model.
- **Verdict: PASS (conditional)** — P1s: (a) `check_structure` is **in-repo** (sdk.py:1813), not external-hosted — root cause re-stated as definition-divergence (fixed); (b) enforcement vocabulary is **warn/retry/block** with existing resolver, not warn/error (fixed); (c) O/I write-time split must be an explicit user decision, not silent (→ Clarifications); (d) payload-local commit hook is thin but worth keeping (fixed scope). P2+: commit hook not deferrable (greenfield, slice-5 unmerged — kept); `warnings[]` channel needed (added); multi-domain attribution precondition (added); single-chain vs all-chains scope (added); check_structure covers more than chain steps (added).
- **Controller tiebreaker:** verified both P1 factual claims directly in repo (sdk.py:1813; pack_registry.py:102,249) — confirmed.

### solution-verify (2 parallel verifiers, fresh context)
- **Verdict: FAIL → controller tiebreaker re-converged.** P0/P1s (all fixed inline):
  - Generic manifest-driven chain-runner **cannot express the actual rules** — check_structure has 5 heterogeneous rule types (operator-anchored orphan, property-ref dangling ×3, status check); JTBD is outside the manifest chain; 3 of 5 consecutive chain pairs have **no declared edge mechanism** in the manifest (feature→userJourney, userJourney→workflow, workflow→requirement); leapfrog-as-defined false-positives on legitimate back-refs (`userJourney.covered_use_cases → useCase`). → **Generic runner shelved**; registration-only, issue-conformant shape selected; leapfrog lives inside the validator as a forward-only rule.
  - Phase B deterministic payload-local set near-empty; no production chain is `block` → Phase B wired + tested with synthetic block rule, documented inactive in prod; graph-wide orphan stays CLI/MCP.
  - CommitPayload `extra="forbid"` — adding a domain field breaks `client_commit_id` canonicalization → **no schema change**; domain passed from orchestration layer, kind-inference fallback fail-safe.
  - REST is **not** auto-derived (FastAPIRouterAdapter.register_all needs explicit `rest_spec`; `tortoise_check_structure` is MCP-only) → v1 = local CLI + MCP-only tool; hosted REST = explicit follow-up.
  - Drift warning must be per-domain scoped (dev/marketing chains are out of scope — must not fail `validate`); registration must be import-time in a shared module (same-process visibility; else CLI returns **false-clean**).
  - Idempotency ordering (validate after dedup/merge); cold-start noise (commit hook intra-payload only → safe by construction); N+1 in check_structure (batched queries in migration); exit-code contract disambiguated from `verify`.
- **P2/P3 incorporated:** wrapper-equivalence test, false-positive budget vs check_structure, perf budget, back-edge exclusion, idempotent re-commit stability, `--json` contract, hosted-mode out of scope, per-domain drift scoping.

### Coherence gate
- `[QWEN-GATE] substitute reviewer used` — qwen3.8-max coherence review BLOCKED (time-critical streamlined mode, 3rd attempt). Substituted by the two solution-verify verifiers, which exercised cross-cutting coherence (idempotency, hosted mode, drift, performance, contract collisions). Residual coherence risk documented in Open Questions.

---

## Plan (3 bullets — the remaining work)

### 1. Constraint-registration API on `domain_loader`

- **`register_domain_validator(domain, *, chain_id=None, surface="graph"|"payload_local", fn)`** + `@domain_validator(...)` decorator — module-level registry (Lock pattern already in domain_loader.py); `domain_validators(domain, surface=None)` discovery; `domain_chain_spec(domain)` reads pack manifest chains (steps + enforcement) as the *declaration* reference.
- **`tortoise/domain_validators.py`** — import-time declarative registrations, imported by CLI, MCP, and SDK paths (same-process registration visibility guaranteed → no false-clean).
- **`product_strategy.validate_chain_integrity(graph)`** — mechanism-complete migration of `check_structure` (all 5 rule types: orphan_use_case w/ JTBD anchor via composedOf/hasPart + `_expand_kind`; dangling_use_case_ref / dangling_jtbd_ref / dangling_workflow_ref property-ref resolution; orphaned_draft). Leapfrog = forward-only non-adjacent step edge (back-refs exempt). **Batched Cypher** (single OPTIONAL MATCH per rule — fixes current N+1 of one query per useCase).
- **`sdk.check_structure` becomes a thin delegation wrapper** over the registry — output contract `list[dict]` (`type/id/message`) preserved for 4 consumers (mcp_server.py:666, tool_registry.py:89, test_sdk.py, test_integration_search.py).
- **AC:** registration+discovery unit tests; **wrapper-equivalence test** (identical output pre/post migration on fixture graph); no registry mutation after import (documented); **performance budget** (batched queries, e.g. <5s on product graph, measured).

### 2. Write-time enforcement in the commit path (Phase A warn + wired-but-inactive Phase B)

- **`validate_domain_rules(payload, domain=None)`** called from `validate_payload_dict` (single chokepoint). Domain passed by orchestration layer when known (memory_orchestrator DomainRouter); kind-inference fallback via `domain_kinds()` with **fail-safe no-match/multi-match → skip + log** (no CommitPayload schema change — avoids `client_commit_id` break).
- Runs **`surface="payload_local"` validators only**. Enumerated deterministic set: (a) intra-payload **forward** non-adjacent chain edges (both endpoints in payload); (b) intra-payload dangling refs under self-contained policy. Graph-global rules (orphan useCase) **never run at commit** — cold-start safe by construction (useCase-before-JTBD capture is the documented normal pattern).
- Results → new **`warnings[]`** field on `Layer1Result` + commit 200 contract (additive; existing tests assert only ok/errors/code; slice-5 endpoint unmerged → greenfield, no breaking change).
- **Severity** via `enforcement_for_chain`: `warn` → Phase A warning; `block` → Phase B reject (4xx with detail); `retry` → Phase A warning + non-zero at CLI. Phase B machinery built + tested with a **synthetic block-level rule**; no production chain is `block` today → documented inactive-but-wired.
- **Idempotency:** validate **after** dedup/merge resolution (validate what actually writes) → idempotent re-commits return stable 200, no repeated warnings.
- **AC:** clean payload → empty warnings[]; intra-payload leapfrog → warning (warn) / 4xx (synthetic block rule); cold-start single-point useCase commit → **no warning**; idempotent re-commit → stable; **no graph I/O in commit path**.

### 3. `tortoise validate --domain <slug>` CLI + tool surface

- **`__main__.py`**: new `validate` subcommand — `tortoise validate --domain <slug> [--json]`, **local mode only** (FalkorDB direct, existing DB pattern); runs graph-surface validators for the domain; per-rule report.
- **Exit codes** (documented deviation from brief's generic 1=error — CI-actionable): `0` clean · `1` violations found · `2` usage error · `3` runtime/DB failure. Disambiguated from `verify` (health) and `check-consistency` (event-log replay).
- **`tool_registry.py`**: `tortoise_validate_domain` (readOnly, MCP-only like `tortoise_check_structure`). Hosted REST surface = **explicit follow-up** (requires rest_spec + hosted handler — not auto-derived).
- **Drift check** (per-domain, default-on for the queried domain only): manifest chains for the domain with no registered validator → warning; `--all-domains` opt-in (out-of-scope dev/marketing chains never fail a clean run).
- **AC:** exit-code tests (0/1/2/3); `--json` output contract; unknown domain → exit 2; drift warning on missing validator; MCP tool registered; runs against live graph.

### Cross-cutting
- JTBD canonical-chain decision gates rule behavior (see Clarifications).
- Out of scope (documented): dev (`epicToCode`) / marketing (`campaignToChannel`) chain registrations (mechanism ready, registration deferred); hosted REST surface; generic declarative chain-runner (follow-up when manifest gains per-pair edge specs); rules DSL.

---

## Clarifications (user decisions — NOT design decisions)

1. **O/I re-scope sign-off:** "enforced at write time" = payload-local warn (Phase A) + deterministic intra-payload block (Phase B) at commit; graph-global rules (no orphan useCases, cross-commit leapfrogging) enforced via `validate --domain` CLI + MCP. Is this amended O/I acceptable? (Recommended: yes — matches the three-gates evidence; the alternative — graph-state access in the commit path — contradicts the brief and the codebase's design.)
2. **JTBD canonical chain:** add `jobToBeDone` to the manifest `productDelivery` steps (matches check_structure + skill + target "no orphan useCases") **vs** drop the orphan-useCase rule. (Recommended: add to manifest — closes the divergence at its source.)
3. **`retry` enforcement level mapping:** treat as Phase A warning at commit + non-zero exit at CLI. (Recommended; `retry` semantics for extractor retries can be layered later.)
4. **Phase B in prod:** no chain is `block` today — confirm Phase B ships wired-but-inactive (synthetic-test only) until a chain flips to `block`.

---

### Axis Research

PRIOR_RESEARCH: **docs/research/2026-08-13-405-domain-constraints-brief.md** (persisted per Phase 1.5; cited throughout). Key findings used:

- **Ontology (HIGH):** SHACL separates constraint *definition* from *enforcement strategy*; severity model = warnings never block unless individually mandatory; persist-and-flag is a normal state (valid data-with-warnings). Competitor precedent: Cognee stamps `ontology_valid` (validate-as-flag). Cautionary: Mem0 removed graph memory (cost). Pitfall: validation artifacts drift from governed data → drift detection needed.
- **Architecture (HIGH):** three validation gates (pre-load / in-transaction / post-load reconciliation); *"cheapest check that can catch a class of defect should run at the earliest gate that has enough information to run it"* → this repo's commit path lacks graph state → chain checks belong at the post-load/read side (CLI/reconciliation). Mammoth-transaction concurrency caution → warn-only in commit path; heavy checks on-demand. Incremental update model → useCase-before-JTBD capture is the normal pattern (cold start).
- **UX/CLI (LOW, fired on demonstrated gap):** CLI exit codes 0=success / 1=error / 2=usage (universal convention; optional 3/4 for finer granularity) — adapted here to 0/1/2/3 with 1=violations (documented deviation, CI-actionable).

---

## Rejected Alternatives

| Alternative | Why rejected | When it would be better |
|---|---|---|
| **Generic manifest-driven chain-runner** (orphan/leapfrog engine over manifest chains) | Cannot express actual rules: 5 heterogeneous rule types (operator-anchored orphan, property-ref dangling ×3, status check); JTBD outside manifest chain (step-0 anchor undefined); 3 of 5 chain pairs lack declared edge mechanisms (false coverage or false positives); leapfrog-as-defined false-positives valid back-refs; recreates 3-surface drift (manifest + runner + registered fns) | When the manifest gains per-pair edge specs + JTBD anchor — then a declarative runner is honest and should be revisited as follow-up |
| **Pure declarative rules DSL (SHACL-style)** | DSL design = scope creep for standard tier; check_structure's non-adjacency rules don't fit a pure adjacency language | When domains multiply beyond ~3 and all rules are structural |
| **Registration API without commit hook** | Payload-local intra-payload checks (leapfrog both-endpoints, dangling refs) are real; hook is greenfield-cheap (zero call sites, slice-5 unmerged) | If payload-local checks were proven useless |
| **External governance layer (CMGL-style, brief)** | Overkill for a single engine; external gate between runtime and backend | Multi-backend fail-closed admission control |
| **Add `domain` field to CommitPayload** | `extra="forbid"` → breaks `client_commit_id` canonicalization → contract break for every extractor client | Only if multi-domain attribution becomes unsolvable via orchestration-passed domain |

---

## Wiring Check

| Touch-point | Type | What changes | Gate |
|---|---|---|---|
| tortoise/domain_loader.py | Core | Registration API + registry + domain_chain_spec | HARD (bullet 1) |
| tortoise/domain_validators.py | New | Import-time registrations (product-strategy validator) | HARD |
| tortoise/sdk.py | Migrate | check_structure → delegation wrapper (contract-preserving) | HARD (compat) |
| tortoise/commit_schema.py | Core | validate_domain_rules + warnings[] on Layer1Result | HARD (bullet 2) |
| Commit endpoint (slice-5 worktree, unmerged) | Co-req | 200 contract gains warnings[]; validate-after-dedup ordering | HARD — sequence with slice-5 merge |
| tortoise/__main__.py | Core | `validate` subcommand + exit codes | HARD (bullet 3) |
| tortoise/tool_registry.py | Core | tortoise_validate_domain (MCP-only) | HARD |
| tortoise/mcp_server.py | Alias | validate tool surface | HARD |
| tests/ | Verify | test_domain_loader_adapter, test_commit_schema, test_cli_* + wrapper equivalence + exit codes + perf budget | HARD |
| config/packs/product-strategy/manifest.yaml | Config | JTBD canonical-chain decision (user) | GATED on decision |
| tortoise-verify-chain skill (external) | Adjacent | Sync/deprecate phantom tool refs | Extra issue #XXX |

---

## Complexity (7-domain table)

| Domain | Rating | Rationale |
|---|---|---|
| Strategy | LOW | Mechanics only; product-strategy rules already exist (check_structure) |
| Architecture | MEDIUM | New registry + chokepoint hook + CLI; constraint: no graph state in commit path |
| Ontology | HIGH | 3 divergent chain definitions; JTBD canonical decision gates rule behavior |
| UX | LOW | One CLI subcommand; no UI |
| Data | MEDIUM | No schema change (extra=forbid avoided); warnings[] additive; check_structure output-contract migration |
| Security | LOW | Read-only CLI + MCP tool; no new privileges |
| Testing/Integration | MEDIUM | Wrapper equivalence, exit codes, idempotency, cold-start, perf budget, false-positive budget |

**Tier: standard** (confirmed; complexity:standard label).

---

## Review Cycle Log

| Gate | Dispatched | Result | Resolution |
|---|---|---|---|
| problem-verify | 2 verifiers (1st dispatch infra-failed → re-dispatched) | PASS (conditional, P1s) | Root cause re-stated (definition-divergence); enforcement vocab corrected (warn/retry/block); O/I split → user decision; commit hook kept (greenfield) |
| solution-verify | 2 verifiers | FAIL → controller tiebreaker re-converged | Generic runner shelved → registration-only; Phase B set enumerated; no CommitPayload change; REST not auto-derived (hosted = follow-up); drift per-domain; idempotency order; cold-start; batched queries |
| Coherence | qwen3.8-max | `[QWEN-GATE] substitute reviewer used` — BLOCKED (time-critical) | Substituted by solution-verify cross-cutting checks; residual risk → Open Questions |

---

## Open Questions (for human)

1. Approval of amended O/I (Clarification 1) — **blocks implementation start**.
2. JTBD canonical-chain decision (Clarification 2) — **blocks bullet-1 rule definition**.
3. `retry` mapping + Phase B inactive-in-prod sign-off (Clarifications 3–4).
4. Timing: bullet 2 lands with slice-5 (commit endpoint unmerged) — confirm sequencing with that worktree.
5. Residual coherence risk from `[QWEN-GATE]`: recommend a cheap plan-review pass (plan-review skill) after user approval, before writing-plans.
