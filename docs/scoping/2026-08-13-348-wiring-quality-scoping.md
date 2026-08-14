---
title: "348 Scoping — Epic: Graph Wiring Quality Remediation (audit tool, operator annotation, mitigation migration)"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# #348 Scoping — Epic: Graph Wiring Quality Remediation (audit tool, operator annotation, mitigation migration)

> **Scoping run:** 2026-08-13 · issue-scoping v5.1 double diamond + verify (streamlined, 3rd attempt) · **Tier:** complex (epic)
> **Status:** scoped — plan comment posted to #348 · parent epic #6945
> **Gate note:** `[QWEN-GATE] substitute reviewer used` — qwen3.8-max blocked (time-critical run); Phase 5.6 coherence dispatch skipped, cross-diamond coherence covered by the 4 fresh-context verifiers across the two verify gates.

---

## Confirmed Problem

The Tortoise graph's wiring quality has **no maintained measurement surface wired to the live graph and no enforcement gate** — on top of an actively-corrupting write path owned by #334. Specifics, all code-verified:

- **`tortoise/audit.py` is unshipped AND internally broken.** Exists (`audit_graph()`, `AuditResult`, 6 checks) but is dead code — no `audit` subcommand in `__main__.py`, no MCP audit tool in `tool_registry.py`, no handler in `mcp_server.py`, zero production callers, zero tests. Worse, its checks are stale or defective:
  - **Check 6 (`mitigation_recommended`) is blind**: queries `(tgt)<-[mit:mitigates]-(:Point)` but the ONLY write path `sdk.mitigate_operator` creates `(op)-[:mitigated_by]->(m)` OUTBOUND from the operator (sdk.py:1656); ONTOLOGY §3.9 registers only `mitigated_by`. `mitigates` exists only in legacy scripts + the security allowlist. The flagship check can never see an SDK-created mitigation → guaranteed false positives. **Shared instrument defect: #334 exit criterion 7 uses this same check shape.**
  - **Every check is LIMIT-capped (50 / 50 / 50 / 50 / 20-per-keyword / 50)** → `AuditResult.issues` is a sample, not totals; "80%+ annotated" and "0 naive IMPL" are uncomputable from capped samples.
  - **Check 1 flags legacy artifacts**: point-level `sourceKind` is marked "legacy — tier the SOURCE node" per #398 (audit.py comment); the predicate would flag essentially the whole graph at MEDIUM.
  - **Fix-string is copy-paste broken** (audit.py:198): `tortoise_mitigate_operator(..., confidence=0.7)` — the SDK/MCP accept `strength=` (sdk.py:1615, mcp_server.py:1021), and 0.7 is outside the skill's documented relevance-attack range 0.10–0.50.
  - **Check 5 (`impl_instead_of_nand`) is a substring classifier** at HIGH severity: flags any IMPL edge whose target contains "not ", "fail", "never", etc. — including v3.5-legal operator-less edges; a false NAND fix changes belief propagation (NAND_BASE_WEIGHT=8.0).
- **ONTOLOGY v3.5 §8 reification rule** ("an edge carries an operator iff it needs mitigation, or is Point↔Point support/contradict") **makes the issue's "annotate every operator" premise obsolete.** Annotation dims (bias/precision/consistency/directness) are **ARCHIVED in weights.py:58-61** (`annotation_factor = 1.0` — zero engine effect, only tests read `annotator_*`). The body stats (2,056 ops / 6 annotated / 0 mitigations) are stale AND measure the wrong quantity under v3.5.
- **No enforcement surface** warns writers per the reification rule. `how-to-use-tortoise` has annotation rules but no gate; the agent-infra skill-enforcer is a static tool-call interceptor with no graph read path.
- **Remediation cycles ran blind/ad-hoc** (bp_approach_cycle1/3/4, cost_control_cycle1-4, bp_pros_cons_cycle5 against embedded tortoise.db — "Do not run against production Docker"); no verifiable live counts; `audit_graph*.py` in graph-scripts query the removed `context` field (#49) — dead.

**Root cause = unshipped + stale instrument, no enforcement gate, mis-scoped annotation target — NOT a missing annotation batch.**

**Falsification check:** This definition is wrong if shipping a corrected audit tool + warn-only enforcer leaves write behavior and annotation counts unchanged (→ problem is enforcement-only, not measurement), or if the 2,056-operator "annotation gap" turns out to be entirely v3.5-legal operator-less edges (→ annotation phase collapses; mitigation-focused re-scope). Both conditions are testable at Phase 0 baseline.

**Confidence: 76** (code-verified: audit.py unwired + 4 defect classes, weights.py archived factor, ONTOLOGY §8, sdk edge types; uncertainty: live-graph counts unreachable from repo, v3.5 target redefinition unvalidated on live data, #334 execution status unknown).

### Assumptions

| Assumption | Status | Evidence / Falsification |
|---|---|---|
| Live graph reachable (docker:// port 6379) | [unverified] | #334 clarification #3: NOT reachable from repo; .env has no TORTOISE_DB_URI; embedded ~/.tortoise/tortoise.db is the only queryable baseline (not production) |
| Body stats 2,056 ops / 6 annotated / 0 mitigations | [unverified/stale] | Predate remediation cycles; embedded graph differs materially (609 ops / 132 annotated / 264 mitigated_by / 0 low-conf) |
| "6 critical contexts" | [partially validated] | 3 families confirmed in graph-scripts (bp_approach, cost_control, bp_pros_cons — 8 scripts ≠ 6 contexts; bp_pros_cons has only cycle5; HANDOFF mitigation audits have no repo artifacts) |
| Skill-enforcer = warning-on-write in agent layer | [partially validated] | how-to-use-tortoise hard-gates writes (annotation rules, no enforcement); agent-infra skill-enforcer is a static tool-call interceptor (no graph read path) |
| MCP server exposes tool_registry ToolDefinitions | [validated] | ToolDefinition + http_policy + sdk_method pattern; annotate/mitigate/get_operator tools exist |
| #334 executes before/concurrently | [unverified] | #334 scoped same-day, OPEN, plan-comment only, no branch — no live-graph remediation landed yet |
| Operator-less IMPL/NAND legal post-v3.5 | [validated] | ONTOLOGY.md §8 + #920; makes "6 of 2,056 annotated" misleading |
| Annotation dims have an engine consumer | [DISPROVEN] | weights.py:58-61 ARCHIVED, annotation_factor=1.0; only tests read annotator_* |

### Boundary & Stakeholders
- **Out of scope:** #334's one-shot live-graph remediation (orphans, cap-skip, provenance backfill, EP verified pass, mitigation backfill); ID normalization (#52, closed); fail-closed engine write-gates (deferred — #334 criterion 7 re-open contingency); connector Source-node emission (#388); EP engine changes (weights.py annotation_factor reactivation).
- **In scope:** audit instrument refresh + CLI/MCP ship; decision-critical operator annotation (re-based); warn-only enforcement; migration tooling; baseline re-measurement.
- **Affected but unmentioned:** #334 (shared broken instrument — criterion 7); #903 (Dreaming EP — baseline/migration audit runs must sit outside its quiescence windows); #395/#901 (EP subgraph semantics — non-conflict via recorded params); hosted customers (no /v1/audit REST — see Clarifications).

---

## Verification Gates

### problem-verify: 1 cycle — 1×P0 + 5×P1 fixed by controller (streamlined: NO re-dispatch)
- Verifier A: P0=0, P1=0, P2=6, P3=2.
- Verifier B (Devil's Advocate): **P0=1** (indicator-to-instrument mismatch — audit.py has no annotation-coverage check, so the shipped tool cannot verify the epic's own KPI), **P1=5**: (1) `mitigates` vs `mitigated_by` schema drift → flagship check blind (shared defect with #334 criterion 7); (2) LIMIT caps → samples not totals; (3) annotation dims have ZERO engine effect (weights.py ARCHIVED) — Phase 2 activity is metadata theater without a consumer; (4) enforcement on a noisy keyword classifier degrades the graph (false NAND at NAND_BASE_WEIGHT=8.0; "0 naive IMPL" incentivizes IMPL→NAND conversion); (5) sequencing vs #334 quiescence unstated. Plus P2: customer is the AGENT not a human CLI user (no /v1/audit; hosted customers have no local FalkorDB), root-cause rewording, #49 removed `context` field → "6 contexts" undefined, fix-string `confidence=` kwarg bug, #903/#395/#901 omitted from boundary, baseline re-run unscheduled.
- Controller: all P0/P1 REAL (verified sdk.py:1656 edge type, weights.py:58-61, audit.py fix-string, tool_registry surfaces) → fixed inline (annotation-coverage check added to instrument scope; mitigated_by fix pre-ship; COUNT aggregates; annotation re-targeted as auditability signal with named consumer; enforcement warn-only + check-5 demotion; sequencing contract; MCP-primary surface). P2s incorporated (falsification+confidence added, KPI re-based, consumer-impact lens, pointKind scoping units, boundary siblings).

### solution-verify: 1 cycle — 6×P1 fixed by controller (streamlined: NO re-dispatch)
- Verifier A: P0=0, P1=0, P2=7, P3=2 (check-1 Source-level re-scope; enforcer trigger predicate; connectivity gate; SDK `audit()` wrapper; exit-code pins; 6-context inventory; #903 line; criterion-7 citation; advisory-only fix-strings).
- Verifier B (Devil's Advocate): P0=0, **P1=6**: (1) Phase 0 "post-#334" not schedulable — #334 OPEN, no live graph reachable, embedded DB is the only baseline (bp_approach_cycle3 fallback pattern exists but uncommitted); (2) check-6 fix insufficient — traversal wrong in BOTH edge type AND direction (`(op)-[:mitigated_by]->(m)` outbound from operator); threshold ≤0.35 vacuous on reachable graph; (3) Phase 3 not implementable as scoped — agent-infra skill-enforcer is a static tool-call interceptor with no graph read path; "keyed to reification rule" requires NEW cross-repo capability; (4) Phase 2 annotation consumer circular — "auditability signal" is self-referential with the issue's own Indicator; (5) "REST out of scope" contradicts the issue's own "customer-facing product feature" Objective; (6) no warning throttle — annotation-coverage floods 477/609 (78%) on embedded; fix-strings out of range + nonexistent kwarg; batch-mitigation EP cascade risk. Plus P2s (COUNT(DISTINCT) per check; per-check fixture regression tests; check-5 demotion implemented in code with precision baseline; #334 criterion-7 coordination; adoption path owner; shared JSON payload contract; UPG precedent unverifiable in-repo → replace with `tortoise check-consistency` exit-code precedent) and P3 (three-edit MCP wiring + tools/list test).
- Controller: all 6 P1s REAL (verified skill-enforcer.ts capabilities, embedded-graph stats via live query, sdk.py:1656 direction) → fixed inline in plan (Phase 0 baseline named + #334 stall contingency; corrected check-6 Cypher pinned; Phase 3 re-scoped 3a static nudges / 3b cross-repo extension with contingency; annotation consumer = operator display + decision-relevant Indicator; REST surfaced as human decision with recommendation; throttle + safe fix-strings + human-gated review queue). P2s/P3s incorporated.

### coherence (Phase 5.6)
- `[QWEN-GATE] substitute reviewer used` — qwen3.8-max blocked (time-critical third attempt); dispatch skipped. Cross-diamond coherence checked implicitly by the 4 fresh-context verifiers (problem-solution alignment: solution phases 1-4 each trace to a confirmed-problem component; no diamond-1 finding dropped — every P0/P1 fix carried into the plan).

---

## Plan

**Chosen approach: B — MCP-tool-first + instrument refresh** (fix the instrument, expose it to the agent consumer, warn-only enforcement per v3.5, audit-gated migration tooling). Sequencing contract with #334: **audit-only phases (1–3) proceed against embedded/bolt baselines; only Phase 4 (migration) hard-gates on #334 quiescence** — #348 does not block on #334, but Phase 4 writes never land inside #334's destructive window.

### Boundary (#334 vs #348 — from 2026-08-13-334-wiring-remediation-scoping.md, not re-litigated)
- **SPLIT BY CONCERN.** #334 = one-shot data-correctness remediation of the LIVE graph (orphans/cap-skip/provenance/EP/mitigation backfill) with #334-owned exit queries. #348 = tooling + annotation product: audit CLI/MCP, skill-enforcer, migration tooling; audit = ongoing/maintenance measurement post-close. #334's fail-closed re-open contingency lives in criterion 7; criterion 6c = migration hand-off checkpoint for #348 Phase 4. Sibling coordination: #903 (baseline/migration audit runs outside its EP quiescence windows; no EP engine changes in #348), #395/#901 (non-conflict via recorded params), #388 (forward-path Source nodes).

### Phases (refreshed vs ontology v3.5–v3.8)

**Phase 0 — Baseline precondition (no graph writes).** Connectivity gate (docker:// vs bolt:// vs embedded via `_resolve_db_target` / TORTOISE_DB_URI; record mode + graph name + failure exit code). Baseline run against the **named baseline graph** (embedded `~/.tortoise/tortoise.db` via bp_approach_cycle3 pattern with "not production" qualifier; live graph when reachable) using the CORRECTED instrument; re-baseline ALL stats (issue body counts stale). Decision-critical pointKind inventory verified populated. If live graph unreachable → audit-only phases proceed on embedded baseline; Phase 4 re-baselines before migration.

**Phase 1 — Instrument refresh + ship (the product feature).**
1. Fix check 6: `OPTIONAL MATCH (op)-[mit:mitigated_by]->(:Point) WITH op WHERE mit IS NULL` (correct edge + direction); re-baseline the ≤0.35 threshold (confidence band + pointKind scope) to avoid count explosion; fixture test with real `mitigated_by` edges.
2. Fix check 1: re-scope predicate to Source-level tier coverage (flag points whose Source is untiered/untierable — not points missing the legacy point-level property).
3. COUNT aggregates: explicit `COUNT(DISTINCT ...)` per check (check 1 evidence nodes, check 4 superseded nodes, check 5 (src,tgt) pairs, check 6 operators) with a test asserting totals vs seeded fixture; LIMIT samples only for drill-down listings.
4. Add **annotation-coverage check**: operator Points missing `annotator_bias/precision/consistency/directness`, pointKind-scoped — **informational**, not a gate.
5. Check 5 demoted in CODE (severity → advisory) with precision baseline recorded; remediation = human-gated review queue (semantic verification required before any NAND fix).
6. Fix fix-strings: `strength=` within 0.10–0.50, one-at-a-time + verify guidance; SDK-call equivalents where they exist; advisory-only (no `--apply` in scope).
7. Output contract: shared JSON payload `{summary:{high/medium/low}, checks:[...], node_count, edge_count, exit_code}` used by CLI, MCP handler, and (if approved) REST; exit codes 0 clean / 2 violations / 3 connectivity error (in-repo precedent: `tortoise check-consistency`; UPG `upg check` as competitor-precedent); empty graph = 0 with explicit "0 nodes".
8. Tests: one regression test per check (mitigated_by present/absent, keyword false-positive "not " in supportive statement, LIMIT-removal totals, pointKind-scoped vs unscoped, superseded with/without edge); wiring test asserting `tortoise_audit` in MCP tools/list + HTTP_ALLOWED (three-edit wiring: TOOL_REGISTRY entry + mcp_server handler + GROUP_BY_NAME).
9. Ship: `tortoise audit` CLI subcommand (wraps corrected audit via a thin SDK `audit(point_kinds, ...)` method — single entry for CLI/MCP/REST) + `tortoise_audit` MCP tool (read-only, `_ro()` + `http_policy=True`).

**Phase 2 — Decision-critical operator annotation (re-based on v3.5 §8).** Annotation is an **auditability signal, not an engine lever** (annotation_factor stays archived — reactivation is a human decision, EP change, #903 coordination). Consumer: `tortoise_get_operator` display + context reconstruction surfaces `annotator_*` dims (sdk.py:5042 mitigation-display precedent). Scope = decision-critical pointKinds (initial inventory: the 6-context families bp_approach / cost_control / bp_pros_cons, re-derived as pointKinds — verify populated at Phase 0; add/remove process defined). Re-baseline annotation coverage % as count-based totals.

**Phase 3 — Enforcement (warn-only, reification-rule-keyed).** No fail-closed gate.
- **3a (in-repo, static):** how-to-use-tortoise + AGENTS.md annotation-specific guidance; audit-tool adoption path (skill references `tortoise_audit`, named run owner — the pull mechanism that "unshipped instrument" failure mode requires).
- **3b (cross-repo, contingency-flagged):** agent-infra skill-enforcer extension work — give the interceptor a graph read path (call `tortoise_audit` per session, parse JSON contract, apply warn-only rules). Documented stall contingency: 3b deferred → 3a-only interim.
- Trigger predicates (precise): (a) low-confidence operators without mitigations (corrected check 6, pointKind-scoped); (b) low-confidence support/contradict edges needing mitigation but lacking an operator anchor (lazy-promotion trigger). **Never** operator-less edges per se (v3.5-legal).
- **Throttle:** max warnings/day/context, dedupe window, top-K actionable items, severity demotion after N consecutive runs — prevents warning fatigue + batch-mitigation EP cascade (how-to-use-tortoise: "EP weights nuked by batch-connected mitigations").

**Phase 4 — Migration tooling (unstarted today).** graph-scripts pattern, idempotent, audit-gated (remediation driven by corrected audit output), event-log-safe (SDK-call equivalents; documented rebuild-durability statement per #334 convention). Hard-gates on #334 quiescence; hand-off to #334 criterion 7 = **"re-run with the corrected instrument"** (not "receive #334's count" — criterion 7's measurement shape inherits the fixed check 6; coordination note: who owns criterion-7 queries post-fix). #334 criterion 6c = migration hand-off checkpoint.

### Acceptance criteria
1. `tortoise audit` CLI + `tortoise_audit` MCP tool shipped; audit.py v3.5-refreshed (mitigated_by fix verified against real edges, COUNT totals, annotation-coverage check informational, strength= fix-strings, check-5 advisory); per-check regression tests + wiring test pass.
2. Live baseline re-run (named graph, mode recorded): all stats refreshed; annotation coverage % in decision-critical pointKinds reported as count-based totals; "0 naive IMPL" re-based to "advisory review-queue cleared/justified".
3. Skill-enforcer warns (warn-only, throttled) on reification-rule defects in decision-critical contexts; no fail-closed gate; 3b contingency documented.
4. Migration tooling exists, idempotent, audit-gated; #334 criterion-6c checkpoint named; hand-off = corrected-instrument re-run.
5. No new third-party deps; no EP engine changes; baseline/migration runs outside #903 quiescence.

### Runtime prerequisites
- DB connectivity resolution (docker:// / bolt:// / embedded) via existing `_resolve_db_target`; baseline graph named explicitly; Phase 4 requires #334 quiescence window; agent-infra access for 3b; human decisions in Clarifications.

---

## Clarifications

**Human decisions required (streamlined mode — questions surfaced inline):**
1. **REST surface** — the issue's Objective says "tortoise audit is a customer-facing product feature" but no `/v1/audit` route exists (hosted_api.py audit refs are auth logging only); hosted customers have no local FalkorDB for a CLI. Recommendation: add a read-only `GET /v1/audit` RestSpec day-one (~one registry entry, reuses the shared payload) OR documented deferral with owner/date (mirroring #334 risk-acceptance convention). Default if no answer: deferral with owner/date.
2. **Annotation-factor reactivation** (weights.py ARCHIVED) — reactivate (engine change, makes annotation behaviorally real, #903 coordination) vs keep archived with annotation as auditability signal (plan default). Engine-semantics change → human sign-off required for reactivation.
3. **#334 sequencing** — #348 audit-only phases proceed on embedded/bolt baselines regardless; only Phase 4 hard-gates on #334. Confirm the embedded-baseline qualifier is acceptable as the interim measurement authority.
4. **Decision-critical pointKind inventory** — initial scope = bp_approach / cost_control / bp_pros_cons families re-derived as pointKinds; human confirmation of the set + add/remove process.

---

## External Research (Phase 1.5 artifact)

### Axis Research
> **Findings-date:** 2026-08-13. Queries: 3 fresh (exa MCP) post-dedup (cap 4; 1 unused). #334's research artifact (Koza clean-graph, DataAIHub KG best-practices, migration-execution pitfalls, BP-correctness literature) deduplicated as PRIOR_RESEARCH — not re-searched.

- **Audit-CLI product pattern (Architecture, high)** — competitor-precedent: Grafeo `grafeo validate` (integrity check, exit code 2 on failure) (grafeo.dev/cli); **UPG `upg check`** — one ranked verdict (structure + health + gaps + anti-patterns), exit 2 on violations, `--json`, `upg health --min-score` CI gating, `upg dedupe` dry-run/`--apply`, `upg migrate` (unifiedproductgraph.org/cli/reference); GraQle `graq audit` — graded health (CRITICAL/WARNING/MODERATE/HEALTHY) + exit codes + `--json` for CI/MCP + `--fix` (github.com/quantamixsol/graqle). Pitfall (GraQle origin story): hand-built KGs can pass a structural `validate()` while hollow — **measure the RIGHT invariants**. Applied: shared JSON payload + exit-code contract; check-5 advisory demotion (wrong-invariant risk); annotation-coverage informational (not a gate).
- **Write-gate / enforcement (Architecture, high)** — precedent: graphlint `check_query` — transaction-scoped SHACL validation, commit-if-conforms / rollback-if-violates (github.com/manbradcalf/graphlint); kkrlstrm/knowledge-graph-governance — deterministic write gate (validate proposals, refuse implicit creation, version beliefs, provenance stamp, hash-chained audit) (github.com/kkrlstrm/knowledge-graph-governance); Partenit — pre-commit query linting, shadow-mode ingestion, ontology-as-code; "governance as guardrails not roadblocks" (partenit.io). Pitfalls: enforcement keyed to a noisy classifier creates false-fix pressure (check-5 demotion); warn-only without throttle = fatigue (throttle added). Applied: Phase 3 warn-only + throttle; fail-closed (graphlint/kgg style) explicitly deferred — engine-semantics risk, #334 criterion-7 contingency.
- **Epistemic annotation selectivity (Ontology, high)** — canonical: Confidence Information Ontology — confidence ≠ quality; basic rating system; **selective annotation is correct practice; annotate-everything is added burden masking signal** (PMC4425939); SciClaim (EMNLP 2021) — selective epistemic labels on claims (aclanthology.org/2021.emnlp-main.381); uncertainty survey — five KG quality dimensions (completeness/accuracy/timeliness/availability/redundancy), confidence as triple metadata with provenance (arxiv 2405.16929). Pitfalls: annotation with no behavioral consumer = theater (weights.py archived factor). Applied: v3.5 §8-aligned selective annotation; annotation-coverage informational; decision-relevant Indicator replaces "80%+ annotated".

### Integration Docs
- **No new third-party deps.** Existing stack only: falkordb client, in-repo `tool_registry.py` / `mcp_server.py` / `sdk.py`, in-repo audit (`tortoise/audit.py`), in-repo EP (`weights.py` — no changes), graph-scripts pattern, `_resolve_db_target` connectivity resolution. Cross-repo surface: agent-infra skill-enforcer extension (3b, contingency-flagged, no code in this repo). Shared audit payload defined once (CLI/MCP/future-REST) — no external verification required beyond in-repo precedents.

---

## Rejected Alternatives

**Problem diamond:**
- F1 (original framing — annotate all operators + remediate 6 contexts): would have been better IF v3.5 §8 and #920 (operator-less propagation) didn't exist and remediation hadn't already run outside the epic — but the annotation premise is obsolete, the dims are archived in the engine, and #334 owns live-graph remediation. Refreshed rather than adopted.
- F3 (enforcement-first as the single root cause): the deepest cause of decay, but enforcement on an unshipped/broken instrument is gating on garbage; measurement-first (F2) is the prerequisite. Enforcement retained as Phase 3, warn-only.
- Consumer-impact-only lens: prioritization frame, not a standalone scope — embedded as the decision-relevant Indicator + context-reconstruction display consumer.

**Solution diamond:**
- A (CLI-first parity, ship audit.py as-is): would have been better IF audit.py were already correct and customers were humans — but it ships 4 defect classes and misreads the consumer (agents via MCP; hosted customers have no local FalkorDB). Rejected.
- C (engine-gated enforcement — graphlint/kgg style transaction-scoped validation): would have been better IF corruption were actively flowing and fail-closed enforcement could be verified safe against the EP engine while #903 runs — engine-semantics risk, #334 already flags fail-closed as its criterion-7 re-open contingency. Deferred as documented contingency, not absorbed.
- Pure-incremental migration (no Phase 4 tooling): fine rhythm but unmeasured remediation repeats the blind-cycle failure mode — audit-gated migration retained.

---

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| FalkorDB / embedded tortoise.db (baseline graph) | Data store | Phase 0 connectivity gate + named baseline (bp_approach_cycle3 pattern, "not production" qualifier); live graph when reachable | ✅ |
| `tortoise/audit.py` (instrument) | Tool | Phase 1 refresh (mitigated_by, COUNT totals, annotation-coverage, Source-level check 1, check-5 advisory, fix-strings) + per-check tests | ✅ |
| CLI (`__main__.py` argparse) | API | Phase 1 `tortoise audit` subcommand via SDK `audit()` wrapper | ✅ |
| MCP (`tool_registry.py` + `mcp_server.py`) | API | Phase 1 `tortoise_audit` tool (3-edit wiring: registry + handler + GROUP_BY_NAME) + tools/list + HTTP_ALLOWED test | ✅ |
| Hosted API (`hosted_api.py` — no /v1/audit) | API | Clarification #1 (REST day-one or documented deferral with owner/date) | ⚠️ human decision |
| SDK write paths (`annotate_operator`/`mitigate_operator` idempotency) | API | Already exist (MCP tools live); fix-strings reference `strength=` in-range | ✅ |
| EP engine (`weights.py` archived annotation factor) | Engine | NO changes in #348; reactivation = human decision (Clarification #2); baseline/migration runs outside #903 quiescence | ✅ |
| Skill-enforcer (agent-infra extension, static interceptor) | External | Phase 3a in-repo nudges + adoption path; 3b cross-repo graph-read capability (contingency-flagged) | ⚠️ cross-repo dep |
| `how-to-use-tortoise` / AGENTS.md | Tooling | Phase 3a annotation-specific guidance + `tortoise_audit` adoption reference | ✅ |
| JSONL event log | Data store | Phase 4 SDK-call equivalents + rebuild-durability statement (per #334 convention) | ✅ |
| graph-scripts (remediation cycles) | Tooling | Phase 4 migration tooling home; hand-off = corrected-instrument re-run | ✅ |
| #334 sequencing (quiescence window, criterion 6c/7) | Sibling epic | Phase 4 hard-gate; criterion-7 measurement coordination (fixed check 6 shared) | ✅ |
| #903 / #395 / #901 / #388 coordination | Sibling epics | Boundary section; no EP changes; recorded params; #388 forward-path | ✅ |
| Tests (`tests/` — no test_audit.py today) | Test infra | Phase 1 per-check fixture tests + wiring test (InMemoryProjection exposes `.g` — no Docker needed) | ✅ |

**HARD-GATE:** PASS — every touch point covered; the two ⚠️ are named human decisions with documented defaults + owner/date deferral convention, not uncovered surfaces.

---

## Review Cycle Log

### problem-verify — Cycle 1 (streamlined: no re-dispatch)
- Verifier A: P0=0, P1=0, P2=6, P3=2.
- Verifier B: P0=1, P1=5, P2=4, P3=2.
- Controller: Fixed P0 (annotation-coverage check added to instrument scope — otherwise shipped tool can't verify the epic's KPI) + all 5 P1s (mitigated_by drift; LIMIT sampling; archived annotation factor → consumer decision; keyword-classifier enforcement hazard; #334 sequencing) + P2s (falsification/confidence, MCP-primary surface, #49 context removal → pointKind units, fix-string bug, sibling epics). All fixes code-verified. No re-dispatch per streamlined override.

### solution-verify — Cycle 1 (streamlined: no re-dispatch)
- Verifier A: P0=0, P1=0, P2=7, P3=2.
- Verifier B: P0=0, P1=6, P2=5, P3=1.
- Controller: Fixed all 6 P1s (baseline schedulability + #334 stall contingency; check-6 direction+edge Cypher pinned; Phase 3 implementability re-scoped 3a/3b; annotation consumer named + decision-relevant Indicator; REST surfaced as human decision; throttle + safe fix-strings + human-gated review queue) + P2s (COUNT(DISTINCT) spec, per-check fixtures incl. keyword false-positive, check-5 code demotion + precision baseline, criterion-7 coordination, adoption path, shared JSON payload, in-repo exit-code precedent) + P3 (three-edit wiring test). All fixes code-verified (embedded-graph live query: 264 `mitigated_by` / 0 `mitigates` edges; skill-enforcer.ts static interceptor confirmed). No re-dispatch per streamlined override.

### coherence (Phase 5.6)
- `[QWEN-GATE] substitute reviewer used` — qwen3.8-max blocked; dispatch skipped (time-critical 3rd attempt); cross-diamond coherence verified implicitly by 4 fresh-context verifiers (no diamond-1 finding dropped; every P0/P1 fix present in plan phases 0-4).

---

## Complexity

| Domain | Rating | Rationale |
|---|---|---|
| Problem | complex | Epic; multi-component (instrument, annotation, enforcement, migration) + stale premises + shared broken instrument with #334 |
| UX | medium | CLI/MCP/REST consumer surface; no UI; output contract + exit codes for agent/CI consumption (per issue UX_RATING medium) |
| Ontology | high | v3.5 §8 reification rule, #920 operator-less propagation, #49 context-field removal, annotation target redefinition, Source-level tier model (#398) |
| Architecture | medium | Instrument refresh + exposure (3-edit MCP wiring), connectivity gate, cross-repo skill-enforcer 3b, shared payload contract; NO EP engine changes |
| Library-deps | low | No new third-party deps; existing falkordb/tool_registry/sdk stack; agent-infra extension = cross-repo surface, not a dep |
| Test/verification | high | Per-check regression tests (zero coverage today), COUNT-total fixtures, wiring test, baseline re-measurement protocol, review-queue gate for NAND fixes |
| Risk/data-integrity | medium | Check-6 false positives / count explosion managed by re-baselined threshold; warning throttle prevents batch-mitigation EP cascade; Phase 4 gated on #334 quiescence; no destructive ops in #348 |

---
*Scoped via issue-scoping v5.1 double diamond + verify (streamlined mode, 3rd attempt). Working notes inline; no separate diamond working files.*
