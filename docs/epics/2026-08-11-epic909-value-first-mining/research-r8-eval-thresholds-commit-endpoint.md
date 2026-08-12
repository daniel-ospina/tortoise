---
title: "Research — R8 eval thresholds + session-commit endpoint (epic #909)"
type: research
domain: engineering
doc_status: research
created: 2026-08-11
ownedBy: epistemic-team
governingAgreement: "#909, #753"
---

# R8: Evaluation Thresholds + Session-Commit Endpoint Contract

## Threshold table (target / block / N — band gates, not point gates)

| Req | Metric (per window, macro-averaged) | Target (pass) | Block (fail) | Min N | Notes |
| --- | --- | --- | --- | --- | --- |
| R8 L1 | Typed-stream schema + required fields + kind∈vocab + referential integrity | **100%** (deterministic) | any violation → retry once → fail | 1 | CI blocker, not an eval |
| R4/R7 | `source_ref` present on every point/decision/entity | **100%** (deterministic field check) | any missing → retry once → reject | 1 | "source-citation 100%" = schema-level |
| R1 | Layer-correct rate | ≥0.90 | <0.80 | 30 accept / 12 bump | headline semantic gate |
| R2 | Atomicity rate | ≥0.85 | <0.70 | 30 / 12 | gold matching, not self-report |
| R4 | Citation-correctness (semantic) | ≥0.90 | <0.80 | 30 | quote/span backs the claim |
| R6 | Kind-correctness (pack vocab) | ≥0.90 | <0.80 | 30 | out-of-vocab kind = auto FP |
| R6 | Entity P / R | ≥0.80 / ≥0.65 | <0.65 / <0.50 | 30 | |
| R3 | Process-decision routing | ≥0.95 | <0.80 | monitor-only | rare class, warn-only until n≥20 |
| R1 | Empty-window / high-value-empty / FN_hv | 20–40% / <15% / ≤10% | eval A7–A9 | 30 | |
| R8 L2 | Live floor compliance (calibrated) | ≥95% | <90% | rolling 20 | |

**N math (honest operating characteristics — verifier-corrected):** these bands are
COARSE WATCH-GATES, not powered statistical tests. Exact binomial:

- N=6: ±17pp — useless as a standalone gate.
- N=12: SE≈0.087 at p=0.9; a healthy system (true 0.90) trips the <0.80 block band
  11% of the time (1-in-9) — the bump must be non-destructive (warn, re-measure),
  not a hard fail. A true 0.85 passes the 0.90 target ~70% of the time.
- N=30: SE≈0.055; the block band (<0.80) catches only catastrophic collapse —
  P(reject true-0.80) ≈ 41% at α=0.05 (NOT 90% — 90% power needs N≈109); a 95% CI on
  observed 0.90 is [0.79, 1.0], which does not even exclude the block threshold.
- Separating 0.85 from 0.90 at 80% power requires N≈260 — effectively out of scope
  for the v1 gold set.
**Conclusion:** the bands are honest watch-gates (warn at <target on N≥30, alert at
<block on N≥12, re-measure); the 30-window gold set validates the premise directionally
and feeds the live-judge calibration loop (rolling N=20), which is the real statistical
power source post-launch. Do NOT claim powered separation from the gold set alone.

**Reconciliation with thresholds.yaml (A1-A21):** the eval spec's existing gates
(point_precision_raw 0.65/0.55, point_precision_live 0.80/0.70, per_kind_f1 0.60,
nand_precision 0.70, empty 20-40%, live floor 95/90, nodes 15/25/50, failclose 0.40)
remain the acceptance source of truth for those rows; the R8 table adds the layer-level
semantic gates (layer-correct ≥0.90, atomicity ≥0.85, kind-correctness ≥0.90,
citation-correctness ≥0.90) as NEW rows with the same band semantics. The scope stage
must produce ONE reconciled thresholds.yaml — no two authoritative sets.

**Coupling warning:** layer-correct ≥0.90 is achievable ONLY because the pipeline is gate-first (S1 drops 75–95%) and the vocab closed. If keep-ratio drifts >40% (fail-closed), classification difficulty rises toward the 59–73% research range — the keep-ratio alarm is the leading indicator.

## Session-commit endpoint

```text
POST /v1/sessions/commit     # derived commit (BYOK default path)
Auth: tt_ key (get_current_team) — same as capture_session
200 {session_id, commit_id, nodes_created, nodes_merged, held[], duplicate}
422 schema (retry once allowed) · 402 quota · 500 fail-closed count
```

**Payload (no raw conversation):** schema_version, session_id (client-stable), client_commit_id (content-addressed), captured_at, extractor {version: value@semver+prompt_hash+model_cfg_hash, mode, calibration_version}, summary + story_arc (Document metadata — the conversation content node is a Document per the four-node model; Source carries provenance only), provenance_refs (local file + window spans), entities [{name, kind, passes_frequency_gate}], points [{id: pt_<sha>, content, pointKind ∈ closed vocab, reason: NEW|REVISES|CONNECTS|RESOLVES, confidence, c_cal, about_entities ⊆ entities, source_ref REQUIRED, quote ≤200, status: live|draft}] — **event-class items (past-perfective) serialize as points[] entries with their event pointKind; the four-node chain's Event = the agentSession container, not per-item events** — operators [{src, dst, op_type: IMPL|NAND|MITIGATES, ...}], telemetry. (Plan resolutions PL1/PL2: MITIGATES is a first-class op_type, edge-targeted — {op_type: MITIGATES, target: {src, dst, op_type: IMPL}, strength 0.10-0.50} — operator MERGE key (src,dst,op_type); no op_<sha> ids → server-side maps to mitigate_operator semantics; REPHRASE is a dedup label only, NOT a written operator.)

**Layer-1 validation (deterministic):** Pydantic mirrors valid_kind; required fields; referential integrity (operator src/dst ∈ emitted ids); point count ≤ MAX_VALUE_POINTS_PER_SESSION; content ≤1000, quote ≤200. Non-conforming → retry once → 422 with field reasons.

**Idempotency (two levels):**

- L1 exact replay: client_commit_id = SHA-256 of canonical (session_id + points + entities + operators + summary), excluding timestamps. Replay → 200 {duplicate: true}, zero writes, no write-op.
- L2 re-capture (same session_id, new commit_id after extractor bump): MERGE-based reconciliation — points upsert by deterministic pt_<sha> (same id → MERGE bump updatedAt/version, keep createdAt); changed content → new id → **supersede_point (the existing mechanism: CORRECTS edge + outdated flag + edge transfer — the payload `reason: REVISES` is the semantic label; there is no new REVISES edge in v1, plan resolution PL2)**; entities MERGE by (name, kind); operators MERGE by (src, dst, op_type). Never hard-delete.

**Cumulative per-session budget:** net-new non-episodic nodes per session (post-dedup): soft 15, hard 25, ceiling 50. Counters on Session node (value_nodes_created/held, draft_count, commit_count). >25 → budget_overflow hold queue (never dropped); >50 → fail-closed 402. MERGE hits + dedup burn zero budget.

**Quota fix (P0, must ship with endpoint):** `_count_resource` (quota.py:140-157) has no `sessions` branch → falls through to MATCH(n) counting ALL nodes → ~40 commits × 25 nodes = 402. Fix: add sessions branch counting `MATCH (s:Session) RETURN count(s)`; recommended extra: `is_episodic: true` on Session/Event/Source + points branch counts non-episodic only.

**Metering:** keep `write_ops` (+1 per NON-duplicate commit call — the published billed unit, unchanged; L1 replays bill zero; overflow-to-hold commits bill zero and their re-submission bills the single +1 — one logical payload billed exactly once, plan resolution PL4); add `nodes_written` (+net-new non-episodic, post-dedup — the cost-driver unit) on the existing MeteringRecord. Do NOT switch the billed unit to node-delta in v1. Prevents the 25× per-node arbitrage vs create_point.

**Telemetry (no raw text):** extractor.version/mode/calibration_version, model {provider, id, cfg_hash}, kept_count, candidate_count, segment_count, window_count, keep_ratio, empty_windows, dedup_hits, merge_count, supersede_count, held_count, draft_count, live_count, frontier_calls, llm_cost_usd (optional), extraction_ms, retry_count, last_error_code, confidence_histogram (0.1 buckets), judge_summary (BYOK: locally-run 1-in-N judge aggregates only).

## Build order

1. quota.py sessions fix + is_episodic exemption (C9 blocker)
2. MAX_VALUE_POINTS_PER_SESSION constants (soft 15 / hard 25 / ceiling 50) in quota.py
3. POST /v1/sessions/commit (schema, idempotency, cumulative budget, dual-counter metering)
4. Local commit_session producer in SDK (reuses capture_session local extraction + derived-commit serializer)
5. thresholds.yaml bands + production drift alerts wired to telemetry
