---
title: "#1714 problem-diverge — alternative framings"
type: engineering
domain: platform
doc_status: draft
created: 2026-08-25
subjects.team: epistemic-team
aboutObjects: tortoise-memory-capture, tortoise-onboarding
---

# Problem-Diverge — Agent A Report (issue #1714)

**Source:** worktree `feat/1714-memory-capture` @ edb77e18. All claims verified against that checkout.

## Alternative Problem Framings

### Framing 1 — Onboarding-as-root (the issue's own framing)
"The wizard needs to ask + the mechanisms need wiring."
- Strength: user-visible truth, verified: Q3 flag-only false promise is live (`hosted_api.py:7699` `set_session_recording` writes state only; #235 plan Step 3b capture contract never implemented); wizard step 1 connect-only (`main.jsx:2434-2460`); copy says "issues → Events" while indexer writes Points (`kind="observation"`, `github_indexer.py:117`).
- Weakness: centers the ask when the existing surface is more broken than the issue describes; assumes ask has yield — no evidence opt-in rates justify wizard expansion; automatic recording is consent-sensitive (external: recording must be opt-in with per-session override — ReedSmith/UC Davis privacy guidance; 4/4 adversarial sources on consent).

### Framing 2 — Ingestion-correctness-as-root (the strongest finding)
"Before any wizard ask or promise, the GitHub ingestion layer violates its own idempotency contract — every re-run duplicates every issue."
- `create_point` dedups ONLY when `dedup=True` is passed (`sdk.py:1557` `dedup = props.pop("dedup", False)`); without it: `pid = ulid()` + unconditional CREATE (`sdk.py:1597-1610`).
- `github_indexer.py:115-118` calls `create_point` with NO `dedup` argument. Docstring claims "Idempotent: SDK create_point dedups by content hash" — FALSE.
- Test masks it: `tests/test_github_indexer.py:38-52` uses a FakeSDK with its own URL-set dedup; never touches the real SDK path. Test green, production broken.
- Consequence: state-only changes (closed/reopened) mint duplicates; content edits mint duplicates; a clean re-run mints 100% duplicates. Auto-index-after-connect (decided) doesn't exist today (no `index/github` call in main.jsx; connect only PATCHes `github_connected`, main.jsx:525).
- Lifecycle machinery exists to fix: `supersede()`/`supersede_point()` with edge transfer + bi-temporal windows (`sdk.py:2741-2830`, E5 #1537/E6 #1538), `externalId`/never-overwrite ontology precedent (#398).
- Fix must start with "pass dedup=True or key by externalId", not "make lifecycle-aware".
- Weakness: underweights the live trust defect (deployed prompt promising capture that doesn't happen).

### Framing 3 — Promise-economics-as-root
"Capture everything per harness automatically" is the wrong promise: (a) uneconomic — every captured session costs a second full-conversation LLM extraction pass; capture surface fail-closes on that (503 without provider key `hosted_api.py:3970`; points-quota 402 `hosted_api.py:3995`; turn cap 500 `quota.py:94`); (b) raw session → points is the exact noise source epic #909 exists to remove (historical regex amplifier ~160 nodes/turn, #329 flood gate; 909 mandates keep-ratio fail-closed >40%).
- External: typical Claude Code session ~$0.34 tokens (morphllm 2026); always-on extraction is the cost lever (premai/honeycomb); memory systems that pull toward user misconceptions degrade performance (TechCrunch 2026-06-10); memory fails when treated as "expensive search tools" (Lektik); memory fails when disconnected from where work happens (DevRev).
- Live trust: the false promise is SHIPPED — prompt deployed (`deploy-pages.yml:9,36`; `welcome.html:1636`), beta cohort ~10-50 technical users (`docs/beta-feedback.md:14`). The issue's indicators are all new behavior; none remediate existing users who already said "yes" to Q3.
- Weakness: undersells machinery work (T3 still needs per-harness instruction engineering; GitHub lifecycle ingestion is independent and still needed).

### Cross-cutting (feeds all framings)
The wizard ask is ONE-SHOT — returning users suppressed via `onboarding_complete` (#1643, main.jsx:476); no later opt-in surface exists for sessions/docs (no dashboard index/session toggles anywhere). "Later opt-in from dashboard" is currently a dead end for everyone who skips.

## Assumptions (validated/falsified)

| Assumption | Status | Evidence |
|---|---|---|
| Q3 flag-only false promise | **Validated** | hosted_api.py:7699; #235 plan E2E-6 step 3 never implemented |
| Wizard never asks sessions/docs; copy "Events" | **Validated** | main.jsx:2350,2437,2439 |
| Indexer idempotent on re-run | **FALSIFIED** | dedup=True never passed; sdk.py:1557 default False → ulid + CREATE; FakeSDK test masks it |
| "Each state a distinct memory" | **Partly falsified** | No dedup at all — even unchanged re-runs duplicate 100%; state changes never update github_state prop (stale status) |
| Capture surface exists and works | **Partially validated** | POST /v1/sessions exists (Session MERGE + turn Points + v2/M2 LLM extraction; 503 without key; 402 quota; 500 turn cap). **Unverified:** Pi hosted leg (no observed 2xx; no key on dev machine). Entity linking to subject/project NOT done today |
| GitHub auto-index after connect | **Falsified as implemented — unbuilt** | No index/github call in dashboard; connect PATCHes github_connected only |
| LME supersession machinery exists | **Validated** | sdk.py:2741-2830; tests/test_lme_ingest_v2_supersession.py |
| All three data sources desired by users | **Unverified** | Owner-decided; external evidence suggests always-on raw capture can degrade perceived value |
| Beta testers hitting false promise | **Plausible, unverified** | Prompt deployed; beta cohort exists (docs/beta-feedback.md:14); question_answered analytics exist (hosted_api.py:7706) |
| Supersede-never-duplicate for edited issues | **Validated as target** | Ontology never-overwrite precedent (#398); LME machinery; issue's own indicator |
| Session capture produces entity-linked Points | **Unverified** | Capture endpoint creates Session + turn Points but no aboutSubject/aboutObject links — new behavior |

## Boundary & Stakeholders

### Out of scope (do NOT absorb)
- The extraction pipeline itself — epic #909 owns S0-S6 value-first extraction, derived-commit serializer, POST /v1/sessions/commit, pack manifest v3, dual-counter metering. #1714 consumes POST /v1/sessions as a black box.
- Webhook infrastructure — poll-with-diff on updated_at covers lifecycle in-scope; do not build a webhook consumer.
- LLM cost accounting/metering changes — 909 owns; #1714 respects existing quota gates, does not modify them.
- Cursor storage determination — scoped as a research task inside the issue, not a delivery item.
- The value/noise gate (keep-ratio >40%) — 909's S1 contract.

### Affected but unmentioned
- **Beta testers (~10-50)** — live victims of the Q3 false promise + hosted dead-end; their question_answered distribution is readable evidence.
- **Returning users** — wizard is one-shot; "connect later from dashboard" has no dashboard surface.
- **Self-hosted users** — stdio parity: GitHub hosted-only; corpus/transcript stdio-only; Q3 stdio fallback is diary_write (not capture).
- **Ops / LLM-cost owner** — capture "enabled" is server config (provider key) + points quota; wizard can promise capture the server can't deliver.
- **Pi extension maintainer** — T1 Pi means files in ~/.pi/agent/extensions/ OUTSIDE this repo; needs a deployment story; hosted 2xx leg unproven.
- **GitHub OAuth token owner** — org-wide indexing walks every repo; _MAX_ITEMS_PER_RUN=500; rate limits; encrypted token at rest. Auto-index multiplies this surface.
