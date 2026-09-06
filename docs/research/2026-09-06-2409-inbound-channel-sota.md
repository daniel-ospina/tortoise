---
title: "SOTA: customer→us intake — AI-first company loop (customer request → agent → shipped feature)"
type: operations
domain: operations
doc_status: live
created: 2026-09-06
subjects.team: organisation-design-team
aboutSubjects: tortoise
aboutObjects: tortoise-beta
relatedIssues: "#2409"
---

# Research Summary: AI-first inbound loop — customer requests → agent-driven processing → shipped features

> **Findings date:** 2026-09-06
> **Scope:** Issue #2409 (customer→us intake channel design) researched against the founder's stated end-vision: an AI-first company where customer requests made through the product (a customer "tells their agent" / "files the thing") are processed largely automatically and new features ship — with the explicit constraint that full automation on messages from strangers is NOT the v1 target.

## What We Have Internally

- **Current state (2026-08-14, #1199):** beta cohort (10–50 technical agent devs) files bugs via structured `bug_report.yml` → GitHub Issues auto-labeled `bug`+`beta-feedback`; questions/ideas → GitHub Discussions. **The founder is the triage layer today** (watches the label, 2-business-day triage, ack-every-report). `docs/beta-feedback.md`.
- **Existing plumbing to reuse (from #2409 research basis):** GitHub REST issue reads via `GH_TOKEN` (agents already read issues); Supabase swarm board + always-on agents polling ~every 2 min; `commit-workflow` skill = mandatory human review gate before code ships (PR review = the founder's fulfillment gate); `epic-executor`, `bug-scanner`, `issue-workflow` = the in-repo equivalent of SOTA agent triage routines. **Do NOT import a second agent stack** — Hermes-agent's triage routine is what agent-infra already implements.
- **#2335 error-contract work** will ship a customer-facing "file a report" hook on hosted capture errors → this is the in-product intake surface seed.
- **Slack-bridge outbound was found unreliable** by the founder (channel swap required — issue's Indicator 1).
- **Epistemic graph:** no substantive prior claims found (graph contains test noise only) — this research is the first capture on this topic.

## External Findings (state of the art, 2025–2026)

### 1. The full "stranger message → shipped feature" loop does NOT exist as a proven pattern — but every component does, and the components compose into the founder's vision with one deliberate gap: the fulfillment human-gate.

| Component | SOTA | Evidence |
|---|---|---|
| Capture | Mature (support desks, in-product hooks, issue forms) | in-repo `bug_report.yml` is already this |
| **Agentic triage/classify** | Proven in production, though accuracy figures are vendor-reported. **Vendor case studies claim 85–96%** triage accuracy at mature deployments (no independent benchmark found) — and all emphasize: **structured categories, enough labeled data, shadow mode first, confidence thresholds + human fallback** before auto-routing. Auto-**resolution** (not classification) is the tracked metric and stays mostly at tier-1 support. | IrisAgent, BestlaTech case study, Flamingo MSP (self-reported 96% / 173M tickets / 10–25% closed pre-human) |
| **Issue → agent → draft PR → human review** | **Mainstream platform mechanics** — this is the deepest-proven "automation with a gate" pattern. Assign an issue to the agent like a junior dev; agent opens a draft PR; human reviews via PR comments. | GitHub Copilot coding agent (2025 public preview), Sentry Autofix (stack trace → RCA → fix PR, human review) |
| **Spec-first / plan-gate SDLC** | SOTA gate design for agentic code: agent produces spec+plan → **human approves the plan** → code → CI gates ship. | Microsoft AI-led SDLC ("Spec First, Code Second"), agentic-design.ai Spec→Code→Ship, spec-kit movement |
| **Agent workforces at company level** | Early experimentation, hype-heavy. Cognition (2026 disclosure) claims ~89% of its production code written by Devin; one-person-agent-company experiments exist but publicly surface **coordination failures**. Consulting claims (DevHawk, Altan) unproven. | Cognition disclosure via AI Accelerator Institute roundup; dev.to first-person experiment |
| Org-design vision | AI-first companies engage customers via AI agents, innovation cycles shrink years→months, humans handle exceptions. Vision, not operating reality. | BCG 2025, McKinsey agentic product lifecycle |

**Synthesis:** the "AI-first company" loop is real but is being built as a **layered autonomy ladder with human gates at each rung** — never as a single skip-the-human pipe from untrusted input to shipped code. The rungs: capture → classify/dedupe (autonomous, shadow-validated) → investigate (autonomous, **read-only**) → propose fix/feature (autonomous, spec+plan) → **human approve** → implement (agent, gated by CI + review) → ship → feedback loop into classifier.

### 2. Safety — why full automation on strangers is risky (the constraint in the founder's framing is correct)

- **Prompt injection is the #1 gen-AI vulnerability.** Indirect injection hides instructions in content agents read (reports, code, attachments). Architectural controls that work: treat untrusted content as **data, never instructions** (instruction hierarchy), **tool-permission gating / least privilege**, provenance tracking, output verification. (Galileo; arXiv 2026 agentic-injection papers; Microsoft Zero Trust catalog.)
- **Klarna's reversal is the canonical over-automation precedent (2025):** chatbot optimized the average case and broke the long tail (fraud, disputes, escalations) → service-quality collapse → rehiring humans. Lesson: **confidence-based escalation, verify-before-answer, human backup on high-stakes classes**. Average-case metrics lie.
- **HITL approval pattern consensus** (AWS, Cloudflare, StackAI, AgentNative): durable approval queue; **payload-locked** proposed actions; reviewer surface with execution context; an execution worker that consumes **only approved payloads**; immutable audit trail; timeouts on approval waits. High-risk action classes that must be gated: production deploys, schema changes, side-effecting external calls, credential changes, destructive ops (agentic-engineering book).
- **Autonomy boundary that companies converged on:** read-only investigation can be autonomous; **write/fulfillment gates at the point where consequences become irreversible or costly** — or a reproduce-first, gated-fix path.

### 3. Notification reliability (issue's open question: ntfy vs Telegram vs email)

- **ntfy.sh** is webhook-native (HTTP POST → push), **agent-readable** (JSON backchannel), no daemon/token to babysit; public server is best-effort, self-host supported; **iOS push is the weak spot** on self-hosted.
- **Telegram** anecdotally very reliable delivery (seconds), but it is a second messaging channel to keep alive (bot, phone number, session) — the issue's basis already flags "second channel to keep alive".
- **Email — narrowed out of the notification relay comparison** for urgency-bearing founder signals: push (ntfy/Telegram) beats email on delivery latency and attention; email remains relevant as a *later* intake surface (forward-to-issue) and as the customer-facing ack channel, not as the founder alert. Decision on this narrowing belongs in scope, not research.
- Net: ntfy as primary relay candidate (founder reliability target) with the agent polling the machine-readable queue as the source of truth; notification = signal-to-humanness, never the work queue itself.

### 4. Feedback-loop mechanics

- Classifier improvement needs **founder corrections to feed the classifier** (flagged disagreements → labeled set) — matches issue's scope item 6.
- Every report must carry enough context (receipt/session reference) for the fixing agent to pull ground truth — the issue's #2335 hook design already points this way (session id → replay → reproduce).

## Recommendation (synthesis)

Design v1 of #2409 as the **first three rungs of the autonomy ladder, ending at the human gate — with the ladder's shape pre-agreed so the end-vision is reachable without shipping the risky top rung:**

1. **One canonical machine-readable queue** (GitHub issues — already the structured intake; agents poll via REST). All intake surfaces (issue form, in-product "report" hook from #2335, future email) feed it. Queue = source of truth; notifications are signals.
2. **Autonomous triage** (classify bug/feature/question/other + dedupe + route), shadow-validated against a labeled set before it acts, confidence thresholds → human fallback. Founder corrections feed the classifier.
3. **Autonomous investigation is READ-ONLY** (reproduce against logs/receipts; research; draft an answer or a fix). Never act on the reporter's narrative as instruction.
4. **Fulfillment requires the existing human gate** (commit-workflow PR review) — i.e., agent drafts; founder approves; ship. This is the GitHub/Sentry-proven pattern already wired in this org's agent-infra.
5. **Untrusted-content discipline:** fixed report schema; report content is data; reproduce-first; least-privilege tool access for the handling agent.
6. **Founder interface:** reliable notification (ntfy spike first), digest cadence, approve/respond buttons; founder = scope/priority/brand decisions, never the routing layer.

**Explicit non-goal for v1 (aligned with founder's risk constraint):** no path where a stranger's message alone reaches shipped code without (a) a reproduced, gated fix (bug class) or (b) founder plan-approval (feature class). The end-vision's "features ship automatically" rung is a **later, measured** escalation gated on triage/repro reliability data — designed for, not shipped.

**Intake abuse posture (scoping input, not yet decided):** a strangers-open intake needs a reporter-identity model (known-beta reporter w/ account vs anonymous) and queue backpressure/rate limits to resist spam and duplicate floods. Whether this lands in v1 or is deferred as a documented non-goal (beta cohort is invite-only and small) is a scope decision.

## Raw Notes (append-only)

- 2026-09-06 [canonical/competitor]: GitHub Copilot coding agent — "assign an issue like a developer; agent opens draft PR; you review via comments" (github.blog + docs.github.com). Direct precedent for issue-as-work-unit + PR-as-gate.
- 2026-09-06 [canonical/competitor]: Sentry Autofix/Seer — RCA → fix → PR with human review; "cookbook: self-healing workflow" explicitly composes Seer + coding agent. Closest public product to bug-report→auto-fix.
- 2026-09-06 [canonical]: Microsoft AI-led SDLC blog — "Spec First, Code Second" agentic lifecycle; spec/plan human-approval gate before implementation.
- 2026-09-06 [competitor]: Cognition 2026 disclosure (via aiacceleratorinstitute.com roundup) — ~89% of production code written by Devin. Top-of-SOTA autonomy datapoint, still human-supervised company.
- 2026-09-06 [pitfalls]: Klarna 2025 reversal (Forbes + failureindex.ai + thecraftofai.com) — average-case optimization broke long-tail service; rehired humans. Canonical cautionary tale for "automate customer channel" ambitions.
- 2026-09-06 [pitfalls]: Prompt injection as #1 gen-AI vuln (Galileo; arXiv 2606.10525, 2601.17548); mitigation = data-vs-instruction separation, tool gating, provenance, output verification.
- 2026-09-06 [canonical]: HITL approval patterns (AWS blog, Cloudflare agents docs, StackAI, AgentNative) — durable approval queue, payload-locked actions, approved-payload-only execution, audit trail.
- 2026-09-06 [canonical]: Agentic triage deployments 85–96% accuracy with structured categories + shadow mode + confidence thresholds (IrisAgent, BestlaTech, Flamingo).
- 2026-09-06 [canonical]: ntfy.sh docs/FAQ — webhook-native, agent-readable JSON, self-hostable, iOS weak spot; Telegram reliable-but-second-channel (self-hosted community reports).
- 2026-09-06 [adversarial]: dev.to "company run entirely by AI agents" first-person — coordination failures when agents own end-to-end without a human planner; supports human-gate-at-plan design.
- 2026-09-06 [reviewer P3 fixes]: (1) triage accuracy figures reframed as vendor-reported (no independent benchmark found); (2) email explicitly narrowed out of the notification-relay comparison (push beats email on urgency latency; email = future intake + customer ack surface); (3) intake abuse posture (identity model, rate limits) added as an open scoping input.

## Round 2 — technical questions (multi-source intake, Sentry, external PRs, close-and-queue bot)

> **Findings date:** 2026-09-06 (round 2)

**Internal facts verified:** `daniel-ospina/tortoise` is **PUBLIC** (anyone can open issues/PRs); issues enabled; **no CONTRIBUTING.md, no PR template, no CLA/DCO**. License = **BSL 1.1** (Licensor: Premise Labs), Change Date +4y → **MPL 2.0**; production-use grant ≤ $5M revenue orgs, no hosted-service resale. Product has **no Sentry instrumentation today** (zero hits in `tortoise/`, `apps/`, `services/`). Hosted API (`tortoise/hosted_api.py`) has session capture/commit/session-detail endpoints + per-team session auth — natural home for the #2335 "file a report" hook; org-ops surfaces (Supabase board, always-on ~2min agents) are separate.

**R2 raw notes (timestamped, source-tagged):**
- 2026-09-06 [canonical] **Sentry → intake**: two paths — *issue-alert webhooks* (alert fires → JSON POST to your URL; configured via an Internal Integration + "send a notification via <integration>" alert action) and *issue webhooks* (issue created/state-changed). Verify origin via `sentry-hook-resource` header + payload `action` field; dedupe/state via Sentry issue id + events. **Sentry webhooks cannot send custom headers** → for an authed endpoint, deploy a thin webhook forwarder that adds auth (Datadog-documented pattern). (docs.sentry.io integration-platform + blog sample; datadoghq.com/sentry docs)
- 2026-09-06 [competitor/legal] **BSL + external PRs**: MariaDB (BSL originators) accepts contributions under a **Contributor Agreement OR New BSD (BSD-3) license with a note in the PR** (or public domain) — New BSD is BSL-compatible and later MPL-2-compatible. Tortoise needs a CONTRIBUTING.md + contribution-license note before welcoming external PRs; without it, third-party code rights under BSL are ambiguous. (mariadb.com/bsl-faq-adopting, bsl11, MCA)
- 2026-09-06 [canonical/pitfalls] **GitHub close-and-queue bot**: triggers on `issues:opened` / `pull_request:opened` (Actions) → comment + label + close + relay record to intake. **Author notification is automatic** (GitHub notifies on comments/close). **Fork-PR caveat**: `pull_request` runs fork PRs with a read-only token + no secrets; commenting on fork PRs needs `pull_request_target`, which is a documented RCE/supply-chain vector **if the workflow checks out/executes PR code** (actions/checkout v7 now refuses insecure patterns). Safe pattern: `pull_request_target` that NEVER checks out the PR — only REST-calls label/comment/close and POSTs PR metadata (number, head SHA, ref) to intake. (docs.github.com secure-use, orca.security, sysdig, wiz)
- 2026-09-06 [canonical] **ntfy action buttons**: tap → HTTP GET/POST/PUT (+ confirm dialogs); Android strong (buttons + intents); **iOS supports action buttons incl. HTTP requests** (newer releases). Feasible for approve/dismiss from the push itself; bearer-action security + audit needed (approval = payload-locked decision per HITL pattern). (docs.ntfy.sh/publish, subscribe/phone, releases.md)
- 2026-09-06 [canonical] **Unified intake schema**: multi-source raw store needs canonical record {source, source_item_id, reporter, content, context_ref (session id / receipt / stacktrace), severity, received_at} + **at-least-once + idempotent consumer** dedupe keyed on (source, source_item_id) — matches the repo's own event-log delivery-semantics research (docs/research/2026-08-08-event-log-subscriptions.md).

**Round-2 synthesis:**
- **External PRs to Tortoise are possible (public repo) but not yet enabled**: need CONTRIBUTION policy (CONTRIBUTING.md: BSD-3 donation note or contributor agreement), and external code must always pass the full review/CI gate (commit-workflow) — never auto-merge untrusted code.
- **Sentry + future tools**: intake needs one generic **authed webhook ingestion endpoint** with per-source normalization into the raw store; Sentry integration is alert-action webhook + (optionally) an instrumenting of the hosted product with Sentry SDK — separate decisions.
- **GitHub relay**: event-driven close-and-queue is standard; the fork-PR security rule (never check out PR code in `pull_request_target`) is the load-bearing constraint.
- **Raw store home is the remaining architecture fork**: org-ops Supabase (edge function webhook + table + existing always-on agents) vs hosted_api surface (product-side). Coupling decision for scope.

## Round 3 — notification + email mechanics (post-alignment, scope grounding)

> **Findings date:** 2026-09-06 (round 3). Grounds two founder-decided scope choices: Telegram over ntfy, email as a v1 intake source.

**R3 raw notes:**
- 2026-09-06 [canonical] **Telegram Bot API is daemon-free**: bots are created in minutes via @BotFather (token issued), and the standard HTTPS Bot API (getUpdates long-poll or webhook) needs no self-hosted daemon — Telegram hosts the bot. Delivery is widely reported reliable (seconds) on iOS + Android; bots cannot message a user until the user has started the bot once (privacy rule). Inline-keyboard buttons (callback queries) support tap-to-act flows. Tradeoff vs ntfy: ntfy is a purpose-built notification relay (webhook-native, no account/chat surface) but needs the app + topic subscription and its self-hosted iOS push is the documented weak spot. (docs.ntfy.sh FAQ/publish — round-1 sources; Telegram Bot API docs/community reports)
- 2026-09-06 [canonical] **Email → webhook services are commodity**: CloudMailin (inbound email → JSON webhook; ~10k/mo free tier), InboxBridge, JsonHook, Mailhooks, WebhookRelay, inbound.new all POST parsed email (sender/subject/body/attachments/headers) to a URL; Cloudflare Email Routing is free but requires self-built parsing in a worker; polling a personal Gmail inbox (Gmail API + OAuth) is fragile (auth scope, personal-inbox noise). (cloudmailin.com; inboxbridge.io; jsonhook.com; mailhooks.dev; webhookrelay.com; inbound.new)

**R3 synthesis:** Telegram chosen by founder for the notification channel (not ntfy) — decision recorded in the scoping package R6 + "Notification channel — decided" paragraph (ntfy remains the fallback); delivery spike (founder starts bot, test ping) remains the pre-commit gate inside build item 4. Email intake = dedicated inbound address via an email→webhook service (build item 5).
- 2026-09-06 [adversarial]: BCG/McKinsey AI-first framing is vision; no public case of stranger-message→shipped-code at a real product company found (9 queries). Absence of precedent = the risk the founder named.
