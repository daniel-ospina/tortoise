---
title: "Documentation Index"
type: index
domain: operations
doc_status: live
created: 2026-08-08
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Documentation Index

| Area | Path |
| --- | --- |
| Ask lane — **EVAL-ONLY** (`tortoise/ask_lane.py`, #1987/#3849: no REST route, no SDK method, no MCP tool) | `docs/product/answer-surface.md` |
| **MCP tool surface + public SDK methods** — the curated list: every tool and public method, what it does, what uses it, a recommendation and its rationale; the approved baseline (`config/surface-manifest.yml`) and the drift gate (#3863), which catches an *unrecorded* change — **not** an unapproved one: the surface cannot be changed without **Daniel's approval first** (#4282), because it materially affects customer outcomes | `docs/product/mcp-sdk-surface.md` |
| **Canonical MCP tool list** — the owner-approved target surface (23 proposed tools; the beta target is **26**), read/write-separated, with the removals, the design decisions and the settled placements gating the cutover (#3863) | `docs/product/canonical-mcp-tools.md` |
| **Canonical SDK method inventory** — all 150 public methods in 32 groups, with the true duplicates, the do-not-merge blockers, the competitor evidence, and the read-named methods that write (#1521). **An inventory plus an earlier target sketch, NOT the target** — where it disagrees with `beta-sdk-surface.md`, that doc governs | `docs/product/canonical-sdk-methods.md` |
| **Beta SDK surface** — the recommended set: the memory API (mirrors the MCP list) plus the tenancy API a builder needs to run one graph per end-customer, with the discarded methods and their rationale (#1521) | `docs/product/beta-sdk-surface.md` |
| **Vision — the MCP and SDK surface** — the target surface, the implementation plan from it, which artifact governs which, and the lanes blocked on this (#1521). `graph_set_recording` was settled by owner ruling on 2026-09-21 (kept — the MCP target is **26**); the **four placement items** that were owner-open in `canonical-mcp-tools.md` are **settled on 2026-09-22, recorded on #4282** (`manage_deployment` off the MCP, `run_onboarding` dropped for `check_connection`, packs SDK/REST post-beta) and are listed in the vision doc's cross-artifact tensions section | `docs/product/vision-mcp-sdk-surface.md` |
| **Bridge table (Phase 0.1 of #4282)** — every current tool and its single destination, the merged-target discriminator map, and the target methods with no SDK method behind them. **GENERATED** by `tools/bridge_table.py`; never edit by hand | `docs/product/bridge-table.md` |
| **MCP rename table (Phase 0.3 of #4282)** — the caller's migration row for every served MCP tool: the name to call **today** (the registry's live `RETIRED_USE_INSTEAD`, or an explicit `no replacement`), the **target-surface** destination, and whether the old name retires with a warning shim or is simply absent — plus the name-by-name agreement check against #4031 (16 of 16 agree, 0 findings). **GENERATED** by `tools/mcp_rename_table.py`; never edit by hand | `docs/product/mcp-rename-table.md` |
| **SDK rename table (Phase 0.3b of #4282)** — every public `TortoiseSDK` method and what replaces it (a target method, `unchanged`, or `discarded` with the doc's rationale and quote), the unbacked rows, the cross-doc tensions, and the doc→code name mismatches. **GENERATED ON DEMAND — not committed** (#5373): it is a function of `sdk.py` line numbers, so committing it conflicted between any two concurrent `sdk.py` PRs. Generate it with `uv run python tools/sdk_rename_table.py` (verify a local copy with `--check`); `tests/test_sdk_rename_table.py` renders and asserts it on every run | `docs/product/sdk-rename-table.md` (generated) |
| **Declared SDK public surface (Phase 0.4 + 1.1 of #4282)** — the authoritative `TortoiseSDK` public method set, derived from the AST (not an `__all__`: the methods are class members), reconciled in both directions against runtime reflection and the approved baseline, and the procedure for adding a method. **GENERATED** by `tools/sdk_surface.py`; the machine-readable form is `config/sdk-surface.json`. Adding or removing a method needs **Daniel's approval first** (#4282) — `--check` is a consistency check, not consent | `docs/product/sdk-surface-declaration.md` |
| Ask pre-ship gate runbook + #2069 routing/cost record (#1987/#2069) | `docs/runbook/1987-ask-abstention-check.md` |
| **#4107 — advice-shaped preference abstention, characterised** — the answer-bearing turn is present (fused rank 4, rendered block, 16-word gold span, L2/`ctx_recall` green) yet L1 fails; root cause is the universal clause's synthesis-license vs asked-subject-scoping-guard tension; the `_PREFERENCE_FRAGMENT` route is refuted by before/after replay **on the same frozen context**. No prompt change landed (one sample; battery filed as **#4837**) | `docs/runbook/4107-preference-abstention.md` · receipts `docs/runbook/4107-preference-abstention.json` + `docs/runbook/ask-shape-rate-2026-09-23.json` · diagnostic `docs/runbook/4107_preference_abstention_diagnostic.py` |
| **Blog E2E residue cleanup runbook** — remove the `meta-contract-*` / `lifecycle-e2e-*` rows the pre-#4220 deploy E2E deposited in prod; guards, dry-run, verification | `docs/runbook/4220-blog-e2e-residue-cleanup.md` |
| **Ship-test instrument** — the per-deploy clean-browser onboarding walk (signup → wizard → connected only when the server observed it) + the observation artifact (#3806) | `docs/runbook/3806-ship-test-instrument.md` |
| Hosted platform **infra runbook** — provisioning, secrets rotation, rollback, health checks, machine topology + autostop policy (§6), the out-of-band availability watchdog with self-healing + alert dedupe (§7), and the deploy-gate `SKIP_*` bypass convention + Fly secret-provenance gate (§8, #4126) (#2850) | `docs/infra-runbook.md` |
| **Temp-dir hygiene + sweep** — the per-test `mkdtemp` teardown that stops the leak and the operator-invoked, age-gated, prefix-matched backlog sweep (#4069) | `docs/infra/tmpdir-sweep.md` |
| Temporal measurement runbook + gate output (2×2 attribution + widening ablation, #2578) | `docs/runbook/2578-temporal-measurement.md` |
| Temporal retrieval diagnosis + oracle ceiling (42/52 = 81%, #2976/#2978) — evidence | `docs/runbook/2578-oracle-ceiling.jsonl` · `docs/research/2026-09-11-subgraph-retrieval-research.md` |
| **PR #3770 `test (b)` red — failure classification** (pre-existing embedded-lane `rebuild_all` embedding drift, not introduced by the PR; tracked as #4457) — both lanes × four revisions, raw logs | `docs/evidence/3770-embedded-lane-preexisting-failures/README.md` |
| **A/B/C context-assembly pre-registration** — verbatim vs epistemic subgraph vs union (#2976/#2978/#2683) | `docs/experiments/2026-09-11-abc-context-assembly-experiment.md` |
| Competitor analysis — 14 profiles incl. the four-epistemic-primitives matrix (Kumiho/Cognee/Mem0/HippoRAG/GraphRAG/Emergence/Letta) | `product/competition/_analysis.md` · `product/competition/_index.md` |
| Decision evidence — #2952 (degraded retrieval) + #2976 (temporal retrieval) from competitors (Hindsight TEMPR temporal leg, Zep bi-temporal, supermemory "dreaming") | `docs/research/2026-09-11-decisions-2952-2976-competitor-evidence.md` |
| Auth architecture — **current: server-side BFF + HttpOnly `__Host-session` on `app.premiselabs.co` (#4054)**; historical client-side head-gate design (#1498/#1506); key-permission model + #2082 boundary (§6, epic #2083) | `docs/auth-architecture.md` |
| Backup/DR runbook | `docs/ops/registry-backup-dr.md` |
| CI timing measurement artifact (#1477) | `docs/ci-timing.md` |
| CI audit — measured runner-cost/lane analysis, read-only (2026-09-02) | `docs/research/2026-09-02-ci-audit.md` |
| **Finding provenance gate** — every reported finding names the tree it was measured against; `tools/finding_provenance.py` fails on a stale measurement (ancestry, never SHA equality) (#4290) | `tools/finding_provenance.py` · `CONTRIBUTING.md` (§ A finding must carry the tree it was measured against) · `.github/workflows/finding-provenance.yml` |
| Post-flip verification runbook (#669) | `docs/ops/669-post-flip-verification.md` |
| Activation scorecard runbook — beta-readiness lane B7: which sessions activate vs merely sign up, the measurable-vs-blocked legs, cohort roll-up, measured production baseline, deploy requirement | `docs/runbook/b7-activation-scorecard.md` |
| Ontology | `docs/ONTOLOGY.md` |
| Registry graph schema (incl. Graph entity + scoped APIKey + quota — epic #2083) | `docs/registry-graph-schema.md` |
| Definitions — account layer vs in-graph Subjects ("organization account" vocab note, #2311) | `docs/registry-graph-schema.md` (§ Definitions) · `docs/ONTOLOGY.md` §5/§6 |
| Multi-graph migration runbook — no-forced-migration path + rollback drill (epic #2083, C8 #2117) | `docs/ops/multi-graph-migration-runbook.md` |
| Retrieval latency benchmark runbook (#316) | `benchmarks/README.md` |
| Memory-system comparison table + publication/errata discipline (epic #2080 W7) | `docs/benchmarks/comparison-systems.md` |
| Agent-harness landscape — volunteering-memory end-state distribution targets (epic #2080) | `docs/research/2026-09-01-gbrain-learnings/platform-landscape.md` |
| Embedder selection decision record (ADR-009, #1349) | `docs/adr/ADR-009-embedder-selection.md` |
| Auth planes — sessions vs machine credentials (ADR-010, #2246) | `docs/adr/ADR-010-auth-planes-session-agent-key.md` |
| Resolution authority for DR alerts is evidence-gated (ADR-011, #2844/#3127) | `docs/adr/ADR-011-resolution-authority-for-dr-alerts.md` |
| OAuth 2.1 for remote MCP — discovery, PKCE flow, DCR capacity policy + trusted-CIDR exemption (#524/#2866) | `docs/oauth-mcp.md` |
| #2246 dashboard session-only scope + verified plan (ADR-010) | `docs/scoping/2026-09-04-2246-dashboard-session-only.md` |
| #2514 planted-operator (layer-2) corpus + grading scoping | `docs/scoping/2026-09-07-2514-operator-corpus.md` |
| #4620 Pi capture seam executable verification — scope + plan (installed-artifact fired check; `session_verify` disclosure; manual-only residual for objective 1) | `docs/scoping/2026-09-22-4620-pi-capture-verification.md` · `docs/plans/2026-09-22-4620-pi-capture-verification.md` |
| #1370 write-time subject binding — confidence-gated, fail-closed direct `aboutSubject` edges (scope + plan; root cause = empty Subject layer, also #4934) | `docs/scoping/2026-09-24-1370-write-time-subject-binding.md` · `docs/plans/2026-09-24-1370-write-time-subject-binding.md` |
| #2535 invited-user onboarding — what "joining an org" means; the membership model as it is, the member connect gap after the (shipped) skip, multi-org, and the recorded decisions (POLICY A · #2789 · #2534) | `docs/scoping/2026-09-25-2535-invited-user-onboarding-design.md` |
| #3027 OAuth authorization-code redemption state — durable `redemption_state`/`redemption_id` + `code_id` provenance, the claim-identity CAS fence, why the reconciler never re-arms, and the directional `used_at`-agreement CHECK | `docs/scoping/2026-09-25-3027-oauth-redemption-state.md` |
| #4911 capture-path credential redaction — the anchored scrubber at the ONE stored-text definition, the three persistence consumers it reaches, the local-spool exclusion ruling, and the measured residuals | `docs/scoping/2026-09-25-4911-capture-secret-redaction.md` |
| Hosted-vs-local embedding UX research (#1349) | `docs/research/2026-08-17-1349-embedder-selection/ux-research.md` |
| E2E-8 latency re-validation scoping/research (#1656) | `docs/scoping/2026-08-24-1656-e2e8-latency-revalidation-scoping.md` |
| Retrieval levers research (#1657) | `docs/research/2026-08-24-1657-retrieval-levers/research.md` |
| Read-path latency profile — phase-by-phase measurement of the ask lane, store round-trips, and the captured-turn embedding census (WAVE-R / M1, #4194) | `docs/research/2026-09-19-m1-read-path-latency-profile.md` |
| Temporal reasoning in competitor agent-memory systems (2026-09-02) | `docs/research/2026-09-02-temporal-reasoning-competitors.md` |
| Temporal retrieval admission trace — where #2578's temporal evidence is lost (#2976, 2026-09-11) | `docs/research/2026-09-11-2976-temporal-retrieval-trace.md` |
| Capture consent — separating the MCP Bearer credential from session-capture authorization (#3615, 2026-09-16) | `docs/research/2026-09-16-3615-capture-consent.md` |
| Kinds classification-later deep research (#1695) | `docs/research/2026-08-26-classification-later.md` |
| Reaper race scoping/research (#1658) | `docs/scoping/2026-08-24-1658-reaper-race-scoping.md` |
| Reaper destruction-path threat model on a shared `$TMPDIR` — demonstrated CWE-377 symlink-write + attacker-chosen kill/rmtree, and the escalated provenance-guard decision (#4098) | `docs/scoping/2026-09-18-4098-tmpdir-hardening-scoping.md` |
| Local-branch reap path — double diamond, why an existing reviewed worktree engine is reused, and the unsafe name-only classification (#4408) | `docs/scoping/2026-09-20-4408-branch-reaper.md` |
| Worktree/branch reaper dry-run report — measured classification of the current branch + worktree population, with excluded reasons (#4408) | `docs/runbook/4408-branch-reaper.md` |
| Test (b) lane 403/export-delete scoping (#2090) | `docs/scoping/2026-09-01-2090-test-b-lane-scoping.md` |
| Test B-wave shared-fixture scoping (#2127) | `docs/scoping/2026-09-02-2127-b-waves-scoping.md` |
| Ingest contract (`tortoise_ingest` / `sdk.ingest` bundle API) | `docs/INGEST_CONTRACT.md` |
| Data safety — encryption in transit + at rest | `docs/data-safety.md` |
| Quickstart — hosted | `docs/quickstart-cloud.md` |
| Quickstart — self-hosted | `docs/quickstart-selfhosted.md` |
| Client/server package split (`tortoise-client` thin driver, #526) | `docs/client-server-split.md` |
| Beta feedback & bug reporting (#1199) | `docs/beta-feedback.md` |
| Blog keyword research — topic taxonomy + keyword map (#1862) | `docs/research/2026-08-28-tortoise-blog-keywords/research.md` |
| Tortoise agent daemon epic research — OAuth/attribution second-pass verdict + industry code audit (#2554, 2026-09-08) | `docs/research/2026-09-08-tortoise-agent-daemon.md` |
| Actor attribution in agent-memory graphs — industry code audit (Zep/Mem0/Letta/LangMem/basic-memory, #2554) | `docs/research/memory-attribution-industry-findings.md` |
| Standard-harness reuse audit — do we need to invent the battery's key decisions (#1416, 2026-09-10) | `docs/research/2026-09-10-standard-harness-reuse-audit.md` |
| Epic #2554 scoping (REVISED) — keyless agent connect via per-agent OAuth + human attribution on the graph | `docs/scoping/tortoise-agent-daemon.md` |
| #2600 Attribution Phase 1 scoping — resolver human actor; sessions + write-events stamped (epic #2554 v1 gate) | `docs/scoping-2600-attribution-phase1.md` |
| #2789 one free organization per person — scoping (double diamond, Design 2 = webhook-provisioned org) | `docs/scoping/2026-09-10-2789-one-free-org.md` |
| #2789 one free organization per person — implementation plan | `docs/plans/2026-09-10-2789-one-free-org.md` |
| #3525 CSP for the app origin + `no-store` regression guard — scope + plan, with the `Set-Cookie`-stripping / `_headers`-bypass `### Integration Docs` record | `docs/plans/2026-09-22-3525-csp-no-store.md` |
| Retention and deletion — the one promise (canonical) | `docs/retention-and-deletion.md` |
| Durability posture — the one authority: mechanism, loss window, and the strongest verification actually performed, per deployment (canonical, #2881) | `docs/durability-posture.md` |
