---
title: "#1714 confirmed problem definition"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

# Problem-Converge — Confirmed Problem Definition (issue #1714)

**Two independent evaluators converged (85/100 and 80/100 confidence).** Both verified the baseline claims in code.

## Confirmed Problem Definition

Tortoise's onboarding memory-capture promise sits on an ingestion baseline that violates its own ontology and idempotency contract — the GitHub indexer mints unkeyed duplicates of a removed point kind (`observation`) with no lifecycle or entity links and no quota gate (while sessions are 402-gated, so ungated auto-index can poison the very capture the wizard promises), session capture is a flag-only false promise, and no remote GitHub-docs extraction exists at all — so #1714 must (1) rebuild ingestion as keyed, Events-as-truth, entity-linked, quota-fair (statement Points about issue content + WorkItem Objects + lifecycle Events + Sources, on a REST fetch layer reusing the connector/projection semantics), (2) build remote GitHub-docs extraction (Contents-API walk into the corpus pipeline; honest stdio-only difference for self-hosted), (3) wire the user-mandated wizard/prompt ask (off-by-default, transparent, gated on mechanism delivery, later opt-in; T3-universal promise with T1/T2 as staged acceleration), and (4) remediate the already-misled state (existing `session_recording=True` users re-surfaced honestly) — keeping epic #909's extraction pipeline strictly out of scope.

## Amendments absorbed from problem-verify (controller fixes, cycle 1)

1. **GitHub docs restored to the definition (P1 fix):** remote `docs/` extraction via GitHub Contents-API walk into the existing corpus/file-indexer pipeline (hosted-eligible), or local-clone + `tortoise_ingest_corpus` for self-hosted — with the honest stdio-only difference per the parity pattern. Dedup via existing `compute_file_hash`; re-ingest idempotency is a falsification condition.
2. **T3 web-harness gate (P2):** for Claude Web the hosted wizard cannot install/verify the workflows prompt — a "yes" must NOT present capture as active. Web harnesses get an honest disclosure ("you'll be asked at conversation end to file the session; here's the prompt") and capture is only presented as active after a server-observable delivery signal (harness field on Session + first capture receipt).
3. **gh-CLI → REST reuse boundary (P2):** `connectors/github.py` fetches via `gh` CLI subprocess — not viable in the hosted control plane. Reuse its event/entity/projection semantics (`_issue_to_event`, `_issue_to_entities`, `_upsert_event`, `sourceObjectId`) with a REST fetch layer (the existing indexer's httpx client is the fetch candidate); do NOT couple hosted ingestion to a `gh` binary.
4. **Net-new component named (P2):** neither existing path produces the deliverable's core artifact — externalId-keyed `statement` Points extracted from issue title/body with `aboutObject` → WorkItem Object and `extractedFrom` → Source. This is in-scope net-new work, not covered by either path alone.
5. **Misled-user remediation (P2):** existing `session_recording=True` onboarding state must be re-surfaced honestly (re-ask or disclosure) as part of wiring the ask — the Q3 false promise's existing victims are part of the problem, not only the new ask.
6. **Re-poll trigger (P3):** lifecycle behavior is inert without a re-run trigger — deliver diff-on-poll re-run + a dashboard re-index affordance (or explicitly defer with the honest note).
7. **Historical-duplicate remediation (P3):** prior live runs minted unkeyed `observation` duplicates — decide leave-as-is vs one-time dedup/merge pass explicitly (stale `github_state` props on existing points also noted).
8. **PR lifecycle (P3):** PRs flow through the same `/issues` endpoint — map `github_pr` sourceKind + closed/reopened via poll-diff into the Events-as-truth model. `deleted`/`transferred` are webhook-delivery-only (GitHub removes deleted issues from the API entirely) — documented as a webhook-only gap in the parity/limitations section, not built here (no-webhook boundary).
9. **Confidence delta (P3):** evaluator A scored 85, evaluator B scored 80; B's reservations: connector production-readiness, quota-bomb severity, unproven hosted capture leg, moving #909 contract. No disagreement on the definition's substance — B's definition wording was adopted with the docs amendment.
10. **Citation verifiability (P2):** behavioral/memory-source URLs appended to Raw Notes in the research brief.
11. **Initial auto-index trigger (P3, cycle-2):** auto-index-after-connect is an EXPLICIT deliverable (quota-gated), not just a problem condition — the wizard's connect action starts the first index run.
12. **Docs falsification condition (P3, cycle-2):** falsification list gains (f) docs re-ingest produces 0 new nodes on unchanged docs.
13. **Session entity-linking target (P3, cycle-2):** part (3) explicitly restates the issue body's target — captured sessions produce Session/Event + episodic Points LINKED to subject/project entities (net-new, matching the issue's acceptance criterion).
14. **Docs hosted staging (P4, cycle-2):** hosted docs path = Contents-API fetch → server-side staging → internal (non-HTTP-exposed) `ingest_corpus`; user-supplied-path exclusion (#236) does not apply to server-staged paths.
15. **Later-opt-in dashboard surface (P4, cycle-2):** "later opt-in from dashboard" is net-new dashboard work (currently a dead end — no dashboard index/session surfaces exist).
16. **Historical-duplicate decision owner (P4, cycle-2):** amendment 7's leave-as-is vs dedup decision gets a named owner step in scope/plan (deliver-or-defer structure like amendment 6).

## Verified baseline defects (code-confirmed)

1. **Unkeyed duplication** — `sdk.py:1539` `dedup = props.pop("dedup", False)` (default False → ULID + unconditional CREATE); `github_indexer.py:115-118` never passes `dedup`. Docstring "Idempotent: SDK create_point dedups by content hash" is FALSE. Test masks it (`tests/test_github_indexer.py:38-52` FakeSDK with its own URL-set).
2. **Removed legacy kind** — `ONTOLOGY.md §5 (v3.8)`: extraction point kind = `statement` ONLY; `observation` removed. Indexer writes `observation`.
3. **Quota asymmetry** — `/v1/index/github` → `_run_indexing` → `index_issues`: zero `count_team_usage` calls (ungated); `/v1/sessions` 402-gates (hosted_api.py:4029-4038). Auto-index can exhaust points → sessions die.
4. **Flag-only session promise** — `set_session_recording` (hosted_api.py:7700) writes state only; #235 plan Step 3b capture contract never implemented.
5. **Orphaned ontology-correct path** — `tortoise/connectors/github.py` already emits Object (pm:issue) + Event (`eventKind: github.issue.<state>`) + Sources + aboutSubject edges via `_upsert_event`/sourceObjectId (`projection/entities.py`), with `start_webhook`; reachable only via `pipeline_cli.py`.

## Key corrections absorbed (from challenge report)

- **Lifecycle = Events-as-truth, NOT supersede-on-everything.** ONTOLOGY §2 "events are the truth, status is a read-only projection"; §3.1 CORRECTS is belief-correction only (bi-temporal validFrom/validTo, §4.1); §5 tombstone contract — naive supersede on a closed issue marks content "outdated" that was never wrong (amnesia). Closed/reopened → Event + projected status on WorkItem Object; content edit → bi-temporal statement update; never CORRECTS a claim that was true while open.
- **T3 (prompt-instructed) is the universal promise; T1/T2 are acceleration.** Per-harness is a mechanism taxonomy, not the product promise. Session node has NO harness field — add one so per-harness capture is auditable.
- **The wizard ask stays (user mandate) but refined:** off-by-default, transparency-first (what's recorded, where it goes), gated on mechanism DELIVERY (not just existence — the hosted wizard cannot install Pi extensions/Claude hooks), later opt-in from dashboard (currently a dead end — one-shot wizard, no dashboard surfaces).
- **Consent-theater / ask-nothing reversal REJECTED as position** (user mandate), retained as copy/UX refinement evidence (Windows Recall, onboarding-friction data, observability capture-all defaults).

## Rejected alternatives

1. Onboarding-as-root (the issue's own framing) — rejected as root: fixes the surface over a broken baseline; ask-yield unproven. Absorbed as the delivery vehicle.
2. Ingestion-correctness narrow ("just pass dedup=True") — rejected: leaves removed kind, quota bomb, no entity links. Absorbed as root core, expanded + re-pointed at the connector path.
3. Promise-economics-as-root — rejected: economics is #909's mandate (quota/metering out of scope); absorbed as gating/transparency guardrails.
4. Supersede-on-everything lifecycle — rejected (ontology violation).
5. Ask-nothing-at-onboarding — rejected (user mandate), form retained.

## Falsification check

Falsified if: (a) a real-SDK re-run of the indexer on unchanged issues produces 0 new nodes; (b) ONTOLOGY §5 still admits `observation`; (c) ontology intended closed/reopened to CORRECTS content points; (d) quota caps ≥ worst-case auto-index volume (unresolved — highest-risk unverified assumption); (e) #909 changes the /v1/sessions contract mid-flight.

## Residual uncertainties (do not change the definition)

- Connector path production-readiness (asserted, not stress-tested)
- Quota-bomb severity depends on team plan defaults (check at scope/wiring)
- Hosted capture leg unproven (no observed 2xx on dev machine; key not configured)
- #909 is OPEN — black-box contract is moving
