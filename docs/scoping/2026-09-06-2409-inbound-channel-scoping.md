---
title: "Scoping: customer→us inbound intake channel — rung 1 of the AI-first inbound loop"
type: operations
domain: operations
doc_status: live
created: 2026-09-06
subjects.team: organisation-design-team
aboutSubjects: tortoise
aboutObjects: tortoise-beta
relatedIssues: "#2409"
---

# Scoping package — #2409 inbound intake channel

> **Status:** approved by founder 2026-09-06. Research basis: `docs/research/2026-09-06-2409-inbound-channel-sota.md` (rounds 1–3, fresh-context reviewed).

## Confirmed problem

**The founder is the routing + triage layer for all customer input** — email he receives and doesn't know what to do with, beta issues on the label he watches, error reports — manually. That does not scale and burns attention on low-leverage work. Deeper: the company lacks the first structural rung of its AI-first operating model — a canonical machine-readable inbound store with agent-driven processing, where the founder sits only at high-leverage gates (scope, priority, brand, approval).

**Root cause being fixed (not the symptom):** inbound is a *personal workflow* (founder's inbox + label watch) instead of a *function of the business* (one store + processing pipeline + decision rules).

**North star (designed for, NOT shipped in v1):** the founder's end-vision — a customer says "file that / ship that" through the product, and it flows through processing into shipped work. **Risk boundary (founder's constraint, made structural):** no path where a stranger's message alone reaches shipped code. Processing cadence scales immediate → periodic so aggregation/prioritization becomes possible as volume grows.

## Requirements (from alignment, all turns)

- **R1 — One raw inbound store, pre-issue.** Every item lands first as a record: `{source, source_item_id, reporter, content, context_ref, severity, received_at}`. GitHub issues are **decision outputs**, never the intake record. Idempotent dedupe on `(source, source_item_id)` (at-least-once ingestion).
- **R2 — Multi-source intake.** v1 sources, all normalizing into R1: (a) GitHub issues + PRs (beta users + external public), (b) in-product "report this" hook from #2335 (carries session/receipt ref), (c) Sentry — **product-side: instrument the hosted product with the Sentry SDK on the capture/commit error paths (the same paths #2335's error contract touches) → issue-alert webhooks → auth forwarder (Sentry can't send custom headers) → intake endpoint** — coordination touch point: product code is epistemic-team owned; (d) **email** — dedicated intake address via an email→webhook service (founder's live pain: "I get emails and don't know what to do with them").
- **R3 — Processing cadence is config, not a rewrite.** v1 immediate (~2-min poll, as the always-on agents do today). Later: periodic batches (24h/weekly) driven by a volume trigger — batching enables **aggregation + prioritization** (clusters with counts: "9 customers hit this error this week"). **Urgent class always immediate** even at volume.
- **R4 — Decision layer before fulfillment.** Classify (bug / feature request / question / other) + dedupe + route. Shadow-mode until ≥90% on a labeled set; confidence thresholds → founder digest.
  - *Bug* → agent investigates **read-only, reproduce-first** (verify against logs/receipts/session — never trust the reporter's narrative) → reproduced → draft fix → **founder PR approval** → ship.
  - *Feature* → agent drafts spec → **founder plan approval** → issue → build lane.
  - *Question* → agent answers from docs/graph (drafted, audit-visible; founder sets voice, reviews sample).
  - *Other / low-confidence* → founder digest.
- **R5 — GitHub relay (close-and-queue).** New external issues/PRs on the public repo → bot comments ("thanks — queued for triage, you'll be notified"), labels `queued-for-triage`, closes, relays the record to the store. Author notification is automatic (GitHub notifies on comments). **Fork-PR safety rule:** the relay runs on `pull_request_target` and NEVER checks out PR code (only REST: label/comment/close + relay metadata). PRs reopen on approval. Contribution policy is a separate issue **#2437** (BSL / BSD-3 donation note, MariaDB pattern) — prerequisite before welcoming external PRs.
- **R6 — Founder interface.** Reliable push notification via a **Telegram bot (standard Bot API — no daemon, bot created in ~2 min with BotFather, hosted by Telegram; founder preference; ntfy is the fallback if the delivery spike disappoints)** on: new items needing a gate, digest-ready, urgent class. Digest cadence. Founder acts **in GitHub** (PR review = approve; comments = correct/respond; close = reject) — no custom UI in v1. Telegram inline-button approvals (approve/dismiss from the phone) are a later rung (bearer-action security + audit needed). Operational rules: founder starts the bot once (bots can't message first), token lives server-side. **Founder = scope/priority/brand decisions, never the routing layer.**
- **R7 — Safety by design.** Untrusted inbound content is DATA, never instructions (instruction boundary, not prompt armor); fixed report schema; reproduce-first; handling agent is read-only + draft-PR, **no merge**; human approval gate before any code/behavior change (`commit-workflow`); zero unauthored action path; external code never auto-merged.
- **R8 — Feedback loop.** Founder corrections feed the triage classifier's labeled set; every report carries a context ref so the fixing agent pulls ground truth.
- **R9 — Targets (from the issue).** report → queue + notify < 5 min (immediate mode); triage classification ≥90% on labeled test set; zero path where inbound mutates code/logs without founder approval or a reproduced, gated fix; founder weekly touch ≤ one digest review + approvals.
- **R10 — Abuse posture (v1-lite).** beta cohort is invite-only + small → identity known; ingestion rate limits + (source, source_item_id) dedupe resist floods. Full strangers-posture deferred with rationale.

## Architecture (proposed)

```
 SOURCES ──▶ INTAKE ──▶ RAW STORE ──▶ PROCESSING ──▶ DECISION ──▶ FULFILLMENT
 (a) GH issue/PR  ─┐                (inbound_items,   (cadence:     classify+dedupe   question → agent answer (audited)
 (b) in-product    ├─▶ webhook  ──▶  org-ops Supabase,  immediate    then:
     "report" hook │   endpoint      pre-issue,        → periodic)   bug → reproduce (read-only) → draft → PR gate
 (c) Sentry alerts ┘   (edge fn)     status: received→              feature → spec → founder plan gate
 (d) email address  ──▶ email→webhook service          classified→  other/low-conf → founder digest
                                                      decided)
                                        │                              │
                                        └── idempotent dedupe ──────────┘
 FULFILLMENT: GitHub issues created ONLY after a decision (R4); PR = founder approval gate (commit-workflow);
              public-surface items closed by the relay (R5), reopened on approval.
 FOUNDER: Telegram bot push (new-gate / digest / urgent) + acts in GitHub. Corrections → classifier labeled set (R8).
```

**Raw store home — decided: org-ops Supabase** (recommended over the hosted product API). Why: keeps company operations separate from the customer-facing product API; always-on agents + boards already live there; external tools (Sentry, GitHub relay, email) never touch the product surface; the only product coupling is the #2335 hook *posting out* to the intake endpoint, which also lets it carry the team/session identity for R1's reporter field.

**Intake sources v1 — decided:** GitHub relay, in-product hook (#2335-dependent), Sentry (product-side instrumentation in scope — build item 6: Sentry SDK on hosted capture/commit error paths → issue-alert webhooks → auth forwarder → intake), email (dedicated address via CloudMailin-class service — free tier ~10k/mo is ample; alternatives: InboxBridge/JsonHook/Mailhooks paid, Cloudflare Email Routing + self-built parser cheaper-but-more-work, Gmail-API polling of the personal inbox fragile). **Email v1 = dedicated address the founder forwards unknowns to, PLUS known sources (Sentry/GitHub) re-routed native so they stop arriving as email at all.**

**Relay behavior — decided:** close-with-message for public issues (clean surface, single record in the store); PRs close + reopen-on-approval (code reviewed only in the sandboxed lane, never in the relay).

**Founder approval surface — decided:** GitHub-native (PR review + comments) with Telegram push notification; no custom UI v1.

**Notification channel — decided:** Telegram Bot API over ntfy (founder preference; daemon-free standard API; inline buttons fit the later approve flow). ntfy remains the fallback if the Telegram delivery spike disappoints.

## Rejected alternatives

- **GitHub issues as the canonical queue (issue's original lean)** — overturned per founder: issues are decision outputs, not intake; batching/aggregation needs a pre-issue store. Re-derivation above (R1).
- **Supabase board as canonical record** — projection/execution surface only; the store is the single record.
- **Hosted product API as intake home** — couples org ops to product auth/ownership (epistemic team) and exposes the product surface to external relays.
- **Keep public issues open with a queued label** — surface clutter + two records; close-with-message chosen.
- **Purpose-built intake UI / digest app in v1** — unnecessary; GitHub + Telegram covers approve/correct/respond until volume justifies a UI (later rung).
- **Full email→everything (poll personal inbox, auto-classify every inbox email)** — fragile (OAuth, privacy, personal-noise); dedicated address + source re-routing is the root fix.

## Wiring check

| Touch point | Covered by |
|---|---|
| GitHub events (issues/PRs) | relay workflow (R5) → intake endpoint |
| #2335 "report this" hook (session/receipt ref) | **#2335** (dependency — feeds R2b) |
| Sentry alerts | forwarder (auth) → intake endpoint (R2c) |
| Email | email→webhook service → intake endpoint (R2d) |
| Raw store | org-ops Supabase table `inbound_items` + unique (source, source_item_id) |
| Processing agent | existing always-on agent infra, cadence config (R3) |
| Classifier + labeled eval set | triage ≥90% target (R4/R9); founder corrections feed set (R8) |
| Approval gate for code | `commit-workflow` (exists) — founder PR review |
| Notification | Telegram bot (spike before commit: founder starts bot + delivery test) + digest; ntfy fallback |
| External-PR contribution policy | **#2437** (filed separately) |
| Abuse/flood resistance | ingestion rate limits + dedupe (R10) |

## Build split (for Plan/Decompose — output of this scoping run)

1. Raw store + intake endpoint + canonical record + idempotent ingestion.
2. Processing agent: poll → classify/dedupe/decide + shadow-mode eval set (≥90% gate).
3. GitHub relay: label/comment/close + relay, fork-safe (R5).
4. Founder notification: Telegram spike (founder starts bot, delivery test) → bot push + digest; ntfy fallback.
5. Email intake: dedicated address → webhook service wiring.
6. **Product-side Sentry instrumentation** (Sentry SDK on hosted capture/commit error paths; coordinate with #2335's shared surfaces) → issue-alert webhooks → forwarder → intake. Requires a Sentry project/DSN.
7. (Separate) #2437 contribution policy.

**Dependencies:** #2 depends on #1; #3 depends on #1; **#3's PR reopen-on-approval branch is gated on #2437 (contribution policy) — v1 relay = close-only for PRs until the policy lands**; #4 depends on the Telegram delivery spike; #6 depends on #2335's error-path work (shared surfaces) + a Sentry project/DSN (external); #1's in-product source depends on #2335's hook. Cadence flip to batch (R3) is post-v1, driven by volume data.

## Open items / deferred (explicit)

- Batch-cadence trigger threshold — set from volume data post-v1.
- Telegram inline-button approvals — later rung (security + audit design).
- Pre-sales/interest intake — out of scope (landing page covers); strictly post-sale + ideas.
- Full strangers abuse posture — later, when non-beta reporters appear.
- Approval-UI/digest app — later, when GitHub-native + Telegram shows founder load above target (R9).

## Review gates

- Research: fresh-context verifier clean (round 1: 3 P3s fixed; round 2 appended after founder's multi-source/PR/email requirements; round 3 grounded Telegram + email mechanics).
- Scope: **founder-approved 2026-09-06** (human gate) → scope summary posted on #2409 → decompose into child issues (build split 1–7; #2437 already filed). Implementation sessions run per-issue workflows (issue-workflow routing) against each child.
