---
title: "334 Scoping — Epic: Work Graph Wiring Remediation (sourceKind, orphan cleanup, confidence propagation)"
type: decisions
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# #334 Scoping — Epic: Work Graph Wiring Remediation (sourceKind, orphan cleanup, confidence propagation)

> **Scoping run:** 2026-08-13 · issue-scoping v5.1 double diamond + verify (streamlined) · **Tier:** complex (epic)
> **Status:** scoped — plan comment posted to #334 · docs/scoping/2026-08-13-334-wiring-remediation-scoping.md

---

## Confirmed Problem

The Tortoise work graph (FalkorDB port 6379) violates its own wiring contract in ways that break graph-as-memory guarantees (CRITICAL comments 2026-08-12; state-centric model — state confidence derives from points; orphans break the derivation):

- **(a) Orphan stub corruption** — `_create_edges` (tortoise/projection/edges.py ~L40-70, #329/#6713) auto-creates stub Points `{content:'[missing]', is_operator:false, no status}` when an operator references a missing short-ID Point. Stubs carry dead IMPL/NAND edges from real operators → EP propagates confidence through garbage (`_live_only` treats NULL status as LIVE), Point-FTS can surface stubs, context reconstruction ("what was believed at t1") hits dead ends. TWO additional classes from the same mechanism: **cap-skip** (at the per-instance 500-stub cap the edge is silently skipped → partial under-propagation) and **Subject stubs** (`_create_about_edges`, subjectKind='other').
- **(b) Provenance gaps, sourceKind confidence-neutrality** — cycle-written evidence stores provenance as a `provenanceSource` STRING (tortoise/projection/entities.py:143-147) with no Source node and no extractedFrom edge. Where Sources exist, **sourceKind presence alone is confidence-neutral**: legacy/descriptive kinds resolve neutral by absence (SOURCE_KIND_DEFAULTS explicit entries only for document/github_issue/slack_message/linear_card → None; unknown kinds → `.get()` default None); `resolve_tier` precedence is explicit credibilityTier > sourceKind tier-form > registry > None; `_apply_source_inheritance` skips None-tier sources (sdk.py:3538); sourceDate falls back to ingestedAt. Real levers: registry registration (`register_source_kind_default` — 0 production callers, module-global, NOT persisted), explicit `credibilityTier`, extractedFrom backing.
- **(c) No verified EP pass** — legacy deliberate runs exist (graph-scripts/decide.py, file_pricing_decision.py, decide_licensing.py) but no verified, reproducible pass under the current ep.py engine with baseline snapshot + stability record; work-graph draft/live distribution unexamined (EP default excludes 'draft').

**#334 = one-shot data-correctness remediation of the live graph** (non-destructive migration + paired verification + 8 explicit exit criteria). **#348 = tooling + annotation product** (audit CLI, skill-enforcer, migration tooling). Boundary decision: **SPLIT BY CONCERN — do NOT merge** (see Plan → Boundary).

## Verification Gates

### problem-verify: 2 cycles, clean — 6×P1 fixed by controller
- Cycle 1: Verifier A — no P0/P1 (2×P2, 2×P3, 1×P4). Verifier B (Devil's Advocate) — **4×P1**: (1) claim (b) sourceKind-neutrality re-adopted original framing without code validation (registry/credibilityTier/extractedFrom are the real levers; cycle evidence has no Source node); (2) #348 boundary mischaracterized (its title includes operator annotation/mitigation migration) + sibling EP epics #395/#901/#903 missed; (3) falsification condition 2 measured the wrong property (audit.py legacy point-level sourceKind — a Source-level backfill never moves it); (4) cleanup mechanics unresolved (delete→dangling edges; 'archived' is LIVE for EP; cap-skip class; Subject-stub class; no quiescence).
- Controller: all 4 P1s REAL (verified #395/#901/#903 exist; entities.py provenanceSource string; live.py excludes only 'draft') → fixed (problem (b) rewritten; boundary re-drawn incl. sibling epics; exit criteria re-based to Source-level metrics; cleanup contract pinned). P2s/P3s incorporated (graph-as-memory assumption re-validated via merged PR #1014; search claim qualified; EP test count corrected; findings-date added; counter-evidence query added).
- Cycle 2 (re-dispatch): Verifier A — 2×P1 (criterion-6 recurrence guardrail tested out-of-scope write path; who-measures-it contradiction "measured by #348's audit" vs "verification queries #334-owned") + 4×P2. Verifier B — 4×P1 (registry policy unpersisted → criterion 3 unmeasurable; cap-skip "declared inputs" not computable (event-log or content-string only); exit 3/4 "agreed with human / or explicitly none" = no-op escape; option B 'archived' path not implementable — terminal status, no SDK write path). No P0 from either.
- Controller: all 6 P1s REAL and code-grounded → fixed (exit criteria hardened: registry persistence pre-commit, cap-skip non-escape floor, committed cleanup path A + degenerate-operator pass, criterion-6 re-scoped to 6a/6b/6c, measurement ownership made #348-independent, +2 P2 mitigation/Subject-stub criteria added, criterion 1/5 de-vacuoused). **Max 1 re-dispatch honored — gate passed via controller adjudication of cycle-2 fixes.**
- Outcome: clean (0 P0/P1 remaining after controller fixes).

### solution-verify: 1 cycle, clean — 8×P1 fixed by controller
- Cycle 1: Verifier A — 1×P1 (EP determinism protocol unverifiable — `random.shuffle(factors)` unseeded at ep.py:1049) + P2s (restore scope, quiescence for phases 3-4, #395/#901 non-conflict, FTS index refresh, 6b landing point). Verifier B (Devil's Advocate) — **7×P1**: (1) EP restore not implementable — no snapshot/restore tooling exists; restore must reset posteriors + edge msg caches; (2) backup path broken for docker:// URI (tortoise backup --db takes file path; JSONL replay resurrects stubs + drops SDK-created points; real restore = docker-aware RDB); (3) exit-5 seed set + exit-3 denominator undefined; (4) cap-skip "best-effort" no-op escape; (5) recurrence risk externalized to #348 with no contingency; (6) no quiescence for destructive phases 3-4; (7) backfill can manufacture stub Sources (provenanceSource strings not URLs → `_link_source` MERGE auto-creates Source on any string); + event-log normalization decision ownerless.
- Controller: all 8 P1s REAL (verified backup.py docstring, ep.py `_flush_cache`, entities.py provenanceSource values) → fixed in plan (docker-aware RDB snapshot + verified restore; EP snapshot/restore script as Phase-5 prerequisite with full-state contract + seeded RNG; seed set/denominator defined; cap-skip UNVERIFIABLE floor; risk-acceptance with owner/date; quiescence extended; identity-validation + stub-Source detector; event-log decision owner+default). P2s incorporated (degenerate-operator review artifact, A/C distinctness framing, rebuild durability, EP scale bound, Koza-archive reconciliation carried in, FTS index refresh, audit variant home = graph-scripts/).
- Outcome: clean (0 P0/P1 after controller fixes).

### coherence review (Phase 5.6): 1 cycle — 2×P1 fixed
- `[QWEN-GATE] substitute reviewer used` — qwen3.8-max unavailable (401); ONE fresh-context substitute dispatched.
- Verdict: no P0 (problem-solution alignment PASS, edge-case coverage PASS, research cross-check consistent). 2×P1: (1) F4 consumer-impact lens dropped — no context-reconstruction/queryability verification in plan (the diamond's own falsification co-equal signal); (2) weakest assumption — live-graph connectivity mode (docker:// vs bolt://) unverified; RDB snapshot requires local Docker, no bolt:// fallback. Both FIXED (context-reconstruction test added to Phase 7; connectivity-mode Phase-0 gate + bolt:// fallback). 4×P2 incorporated (deleted-stub inventory artifact; EP cache surface + PYTHONHASHSEED; EP machinery shared with #903; mitigation/EP-carve-out stale-confidence interplay).

## Plan

**Chosen approach: A+C hybrid — "audit-gated one-shot remediation migration"** (execution vehicle = one-shot migration scripts; gating = Source-level audit variant built first as verification instrument). Rejected: SDK-level enforcement (B) = #348 Phase 3 + engine-semantics risk, boundary-assigned; pure incremental (C) — bulk classes need the one-shot migration.

### Boundary decision (#334 vs #348 — the dedup outcome)
- **SPLIT BY CONCERN — do NOT merge.** #334 = one-shot data-correctness remediation of the LIVE graph (orphans/cap-skip/backfill/verified-EP/mitigations) with 8 exit criteria verified by **#334-owned queries**. #348 = audit CLI/MCP product, skill-enforcer enforcement, migration tooling; its "operator annotation" = GRADE dimensions on decision-critical operators (distinct target from #334's Source-level sourceKind/credibilityTier); its "mitigation migration" hands off to #334 criterion 7. #348's audit = ongoing/maintenance measurement post-close; #334's exit gate is **#348-independent**.
- Sibling epics: **#903** (Dreaming, ongoing whole-graph EP) — #334's EP pass is a one-shot verified baseline with quiescence/hand-off protocol (snapshot/restore machinery built as shared #903-beneficiary tooling); **#395/#901** (EP subgraph semantics for new data) — non-conflict check via recorded params; **#388** (connectors emit proper Source nodes) — complementary forward-path fix for component (b); **#52** (ID normalization, closed) — short-ID stub problem is a facet, NOT absorbed.
- **Overlap resolution:** who backfills = #334 (live-graph current state); #348 = ongoing tooling-driven cycles. Recurrence: #334 owns idempotency (6a), FTS filter (6b), documented residual + risk-acceptance (6c); hard write-path enforcement = #348 Phase 3 with re-open contingency.

### Implementation phases
1. **Pre-migration** (no graph writes): Phase-0 connectivity gate (docker:// vs bolt:// → RDB snapshot vs fallback); docker-aware RDB snapshot + verified restore path; baseline scan (stub/cap-skip/Subject-stub/Source-tier/extractedFrom/draft-live counts); registry policy pre-commit + persistence (config YAML or graph node); event-log normalization decision (owner + default: graph-only durability unless log is source of truth); cap-skip source-of-truth determination (event-log > content-string; neither → class UNVERIFIABLE, blocks close or explicit unremediated acceptance).
2. **Source-level audit variant** (graph-scripts/; #334-owned verification instrument; hand-off note for #348 CLI): Source-level sourceKind/resolved-tier/extractedFrom checks + stub/cap-skip/degenerate-operator detection + stub-Source detector.
3. **Orphan remediation** (write-quiescence for all non-#334 writers during destructive phases): idempotent script — enumerate stubs → DETACH DELETE stub + incident edges → degenerate-operator pass (reviewable artifact + human approval; supersede is terminal) → Subject-stub decision → verify-0 queries + Point-FTS index refresh. Deleted-stub inventory artifact committed (feeds #348 enforcement + per-apply migration log). Koza-archive reconciliation: deletion safe (stubs carry no information — ISSI 2011/Choi 2006) vs 'archived' path unimplementable.
4. **Provenance backfill**: extractedFrom backfill with identity validation (existing-Source url match or URL/scheme check — no stub-Source manufacturing via `_link_source` MERGE); credibilityTier/registry per pre-committed policy; sourceDate best-effort (de-scoped — decay falls back to ingestedAt); Source-level audit re-run gate. Exit-3 denominator defined (all evidence Points with any Source backing; string-backed population declared included/excluded with consequence).
5. **EP verified pass** (quiescence + #903 coordination; seed set = re-derived context-cluster carve-out with documented coverage or max-affected-claims cap): draft/live distribution check; **build EP snapshot/restore script** (full state: ep_alpha/beta, posterior_alpha/beta, edge msg/back_msg, itemized caches; pin random.seed + PYTHONHASHSEED if cross-process); snapshot → run (record damping/max_hops/include_draft/max_iter/tol/converged) → restore → re-run → compare; commit baseline; params recorded for #395/#901 non-conflict.
6. **Mitigation backfill**: audit-driven mitigation adds for low-confidence ops (confidence ≤ 0.35, no mitigates edge), restricted to EP-covered set or with post-EP confidence re-read; count vs agreed target.
7. **Verification + hand-off**: 8 exit criteria via #334-owned queries; context-reconstruction test (before/after Phase 3); recurrence risk-acceptance (owner + date; re-open with fail-closed `_create_edges` guardrail if #348 Phase 3 stalled); rebuild-durability statement (migration writes bypass event log; expected loss on rebuild; pre-rebuild baseline); regression tests (EP excludes stubs, FTS excludes [missing] + index refresh, idempotent re-run).

### Acceptance criteria (exit criteria 1-8)
1. 0 stub Points (content='[missing]'); 0 IMPL/NAND/INPUT edges with stub endpoints.
2. Cap-skip baseline counted (documented source of truth; UNVERIFIABLE → blocks close) + degenerate-operator pass with reviewed artifact.
3. ≥50% (default) of evidence-backed Sources resolved non-neutral (denominator defined); registry policy artifact committed + persisted; verification loads same registry state.
4. extractedFrom backing % measured; backfilled only for identity-validated Sources; remainder documented.
5. EP pass: seed recorded; snapshot→run→restore(full state)→re-run determinism documented; max_iter/tol/converged recorded; non-convergence documented if occurs.
6. 6a idempotent re-run (zero new stubs); 6b Point-FTS excludes [missing] (landed in Phase 3 + index refresh); 6c residual hand-off + rebuild-durability + risk-acceptance documented.
7. mitigation_recommended count reported vs agreed target (0 or documented exceptions).
8. Subject-stub class decided (0 aboutSubject-to-stub edges or documented acceptance).

### Runtime prerequisites
- Phase-0 connectivity gate (docker:// vs bolt://) with restore-path fallback; docker-aware RDB snapshot + verified restore before ANY destructive op (JSONL replay is NOT a restore path — resurrects stubs, drops SDK-created points); human approval for destructive migration incl. degenerate-operator supersede list; registry policy artifact signed; event-log normalization decision with owner + default; write-quiescence for non-#334 writers during destructive phases; EP machinery verified against full state surface.

## Clarifications
No clarifying-questions skill dispatch (streamlined mode; questions surfaced inline). **Human decisions required before planning:**
1. **Registry/tier policy** — which sourceKind values map to which tiers (or a written "explicitly none" rationale with owner signature). Default: document/github_issue/slack_message/linear_card stay neutral; T0-T4-form values inherit.
2. **#334-vs-#348 dedup** — recommended: keep separate (split by concern), #334 first. Human sign-off requested on the boundary.
3. **Live-graph verification first?** — port 6379 not reachable from repo; issue stats (2,057 ops / 94 stubs / 230 issues) unverifiable here. Recommend a live baseline scan (Phase 1) as the first execution step before committing to targets.
4. **Draft/live population** — EP default excludes 'draft'; if the work graph is predominantly draft, Phase 5 scope changes materially.

## External Research (Phase 1.5 artifact)

### Axis Research
> **Findings-date:** 2026-08-13. Queries: 5 fresh (exa MCP) post-dedup. Graphiti prior-research deduped (CRITICAL comment + prior-art docs); figures attributed to hypothesis doc via PR #1014 — re-verify before citing in plan.

- **Ontology/Provenance (high)** — canonical: W3C PROV-DM (w3.org/TR/prov-dm): provenance is the record for trust judgments; attribution critical; provenance-of-provenance. Tortoise's extractedFrom + sourceKind TYPE vocabulary + credibilityTier inheritance aligns; remediation = completing the attribution record. Pitfalls: DataAIHub KG best-practices (dataaihub.co/learn/knowledge-graph-best-practices): no provenance = cannot audit; quality decays without ingest validation; weekly orphan-rate/validation-failure metrics are the sustained practice.
- **Architecture / orphan cleanup (high)** — canonical: Koza clean-graph (koza.monarchinitiative.org/graph-operations/how-to/clean-graph/): dangling edges from ID mismatches; prune moves to archive NOT delete; verify-0 after prune. Pitfalls: SchemaApp orphan analysis (support.schemaapp.com): CONNECT/FIX/REMOVE per orphan + "prevent re-creation" — the prevention half one-shot migrations skip.
- **Architecture / BP correctness (high)** — canonical/pitfalls: Weiss & Freeman 1999 (NeurIPS): loopy BP means may be correct, variances/confidence may be wrong (overconfident); Ihler et al. 2005 (JMLR): loopy BP convergence not guaranteed, non-unique fixed points, message errors accumulate. Counter-evidence: ISSI 2011 (citation networks): dangling-node removal had only local impact (Spearman 0.987-0.99) — but those nodes carried information; Tortoise stubs carry none; Choi et al. 2006 (AAAI edge-deletion semantics): low-MI edge deletion safe for BP → cleanup's value is queryability + stopping garbage propagation; confidence shift is ONE signal, not the sole signal.
- **Migration execution (high, adversarial)** — pitfalls: Dataconomy (dataconomy.com/2026/05/25): dry-run default, idempotent markers, paired verification script, migration log; GitLab 2017 quiet-corruption lesson. DataSemantics: clean-first fails without governance enforcement; post-migration KPIs + 30/60/90-day checks. Datachecks: 80% of migrations fail; scripts lack lineage/rollback. Stackable: pre-migration baseline, continuous validation, "migration endpoint is the start of ongoing operational responsibility."

### Integration Docs
- **No new third-party deps.** Existing stack only: falkordb client + redisgraph (FalkorDB), docker CLI (RDB snapshot via pre_migration_snapshot.py pattern), in-repo EP engine (tortoise/ep.py), in-repo audit (tortoise/audit.py). Registry policy persistence introduces no dep (config YAML or graph node). Docker-aware snapshot/restore scripts are repo-local (graph-scripts/). Nothing to verify externally beyond the existing stack; `#398` source-credibility model is the in-repo precedent for tier resolution.

## Rejected Alternatives

**Problem diamond:**
- F2 (tooling-first — missing measurement): would have been better IF the graph were already clean and the only issue were observability — but stub corruption is real data corruption independent of tooling. Assigned to #348.
- F3 (root-cause systemic — write-side invariants): the deepest root cause, but full write-path enforcement + ID normalization are separate epics (#348 Phase 3, #52); absorbed the minimal recurrence guardrail as verification/risk-acceptance rather than a rewrite.
- F4 (consumer-impact) alone: the prioritization lens, not a standalone scope — cleanup needed regardless of which consumer is hit first; embedded as exit criterion (context-reconstruction test).

**Solution diamond:**
- B (SDK-level enforcement + continuous guardrails): would have been better IF #334+#348 were merged (they're not — boundary decided and twice verified) or if corruption were actively flowing (no evidence — legacy from cross-file wiring scripts). Engine-semantics risk (write-path change mid-migration races the cleanup; 'archived' status has no SDK write path; `_live_only` change is graph-wide). Contingency documented (re-open with fail-closed guardrail if #348 Phase 3 stalls).
- Pure A (unscripted one-shot): faster but repeats the unmeasured-migration failure mode (Dataconomy/Datachecks).
- Pure C (incremental batches only): fine execution rhythm but the bulk classes (94 stubs) are a single batch anyway; surviving distinction is instrument-first gating.

## Wiring Check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| FalkorDB work graph (port 6379) | Data store | #334 phases 1-7; RDB snapshot/restore (Phase-0 connectivity gate) | ✅ |
| SDK write paths (create_point/create_operator, `_create_edges` stub mechanism) | API | #334 6c residual + risk-acceptance; #348 Phase 3 enforcement (boundary) | ⚠️ documented residual |
| EP engine (tortoise/ep.py) | Engine | #334 Phase 5 verified pass; #903 ongoing; #395/#901 subgraph semantics | ✅ |
| Source credibility (source_credibility.py, registry) | Engine | #334 Phase 4 registry policy + persistence; #398 (closed, model) | ✅ |
| Audit tool (tortoise/audit.py) | Tool | #334 Source-level variant (graph-scripts/); #348 CLI product | ✅ |
| MCP server (create_source/set_source_tier exist; no audit tool) | API | #334 uses existing tools; #348 ships audit MCP | ✅ |
| Connectors (github/linear/slack) | External | #334 quiescence during destructive phases; #388 forward-path Source nodes (related) | ✅ |
| Search engine (Point-FTS [missing] exposure) | Engine | #334 Phase 3 (6b filter + index refresh) | ✅ |
| JSONL event log | Data store | #334 Phase 1 normalization decision (owner + default) | ✅ |
| graph-scripts (cross-file wiring scripts = stub source) | Tooling | #334 cleanup + deleted-stub inventory; #348 enforcement | ✅ |
| Hosted API (hosted_api.py) | API | #334 quiescence during destructive phases | ✅ |
| Backup/restore (backup.py, pre_migration_snapshot.py) | Infra | #334 Phase 1 docker-aware RDB (bolt:// fallback at Phase-0 gate) | ✅ |
| #388 / #903 / #395 / #901 / #52 coordination | Sibling epics | Boundary section; EP machinery shared with #903 | ✅ |

**HARD-GATE:** PASS — every touch point covered; the single ⚠️ is a documented, risk-accepted residual with owner/date contingency, not an uncovered surface.

## Review Cycle Log

### problem-verify — Cycle 1
- Verifier A: P0=0, P1=0, P2=2, P3=2, P4=1 (recurrence test missing; Koza mechanics not folded; cap-skip class; findings-date; test count; Graphiti numbers)
- Verifier B: P0=0, P1=4, P2=2, P3=1 (sourceKind-neutrality claim (b); #348 boundary + sibling epics; falsification measures wrong property; cleanup mechanics; (c) restatement; Graphiti attribution; search claim; exit criteria absent; sibling owners)
- Controller: Fixed P1-1/2/3/4 (all code-verified real); incorporated P2s; re-dispatched.

### problem-verify — Cycle 2
- Verifier A: P0=0, P1=2, P2=4, P3=3 (criterion-6 out-of-scope; measurement-ownership contradiction; cap-skip measurement basis; Subject-stub unmeasured; mitigation unmeasured; convergence over-claim; test count; wording)
- Verifier B: P0=0, P1=4, P2=3, P3=1 (registry unpersisted; cap-skip uncomputable; no-op escape; option B unimplementable; criterion-1 vacuous; criterion-5 vacuous; Subject-stub gap; #903 no mechanism; research one-sided)
- Controller: Fixed all 6 P1s; incorporated P2s (criterion 7 mitigation, criterion 8 Subject-stub, criterion 1/5 de-vacuoused, #903 protocol, counter-evidence query). **Max 1 re-dispatch honored; gate closed by controller adjudication.**

### solution-verify — Cycle 1
- Verifier A: P0=0, P1=1, P2=3, P3=2, P4=1 (EP unseeded shuffle; restore scope; quiescence; #395/#901; test count; 6b landing)
- Verifier B: P0=0, P1=7, P2=6, P3=2 (EP restore tooling; backup path; seed set/denominator; cap-skip floor; recurrence contingency; quiescence; stub-Source manufacturing; event-log owner; degenerate-operator review; A/C framing; rebuild durability; EP scale; Koza reconciliation; FTS index; audit home)
- Controller: Fixed all 8 P1s; incorporated P2s; gate closed by controller adjudication (no P0 from either).

### coherence review (Phase 5.6) — Cycle 1
- `[QWEN-GATE] substitute reviewer used` (qwen3.8-max 401).
- Substitute: P0=0, P1=2, P2=4 (context-reconstruction dropped; connectivity-mode assumption; deleted-stub inventory; EP cache surface + hash seed; EP machinery #903-sharing; mitigation stale-confidence interplay)
- Controller: Fixed both P1s; incorporated P2s. No re-dispatch (fixes mechanical, verifiable).

## Complexity

| Domain | Rating | Rationale |
|---|---|---|
| Problem | complex | Epic; live-graph data migration + multi-class corruption + 3 problem components |
| UX | low | No user-facing UI; MCP/CLI consumers only |
| Ontology | high | sourceKind/credibilityTier/status vocabulary, state-centric model, registry policy decision |
| Architecture | high | EP engine, projection write paths, backup/restore, quiescence, multi-epic coordination (#348/#903/#395/#901/#388) |
| Library-deps | low | No new deps; existing falkordb/docker stack |
| Test/verification | high | 8 exit criteria, paired verification queries, EP determinism protocol, live-graph tests, context-reconstruction test |
| Risk/data-integrity | high | Destructive migration on live graph; RDB-restore dependency; recurrence residual (6c) |

---
*Scoped via issue-scoping v5.1 double diamond + verify (streamlined mode). Working notes: docs/scoping/.334-diamond-working.md, docs/scoping/.334-solution-working.md.*
