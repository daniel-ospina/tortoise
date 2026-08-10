# Competitor User Journeys — How They Get Users to First Value

**Date:** 2026-08-08
**Source:** live site + docs + quickstart + pricing pages (fetched 2026-08-08)
**Purpose:** the journey from landing → signup → first memory, per competitor — and what it implies for Tortoise.

---

## Zep — enterprise-led, dashboard-demo as proof

```
Landing: live demo graph (agent_voyager: entities/facts/episodes ticking up)
         + "Trusted By" logos (Samsung, Zscaler, HoneyBook)
  → CTA: "Start Building" (register) OR "Book a Demo"  [dual-path: self-serve + sales]
  → register → free 10,000 credits/mo → API key
  → Quickstart: clone example repo → pip install zep-cloud → 3-line API:
      init client → create user → ingest → retrieve Context Block
  → First value: sub-200ms retrieval of a real ingested conversation
```

- **Positioning:** enterprise agent memory (SOC2/HIPAA, governance, sub-200ms)
- **Pricing:** credits ($125/mo Flex = 50k credits, $25/10k overage, auto-top-up); free = 10k credits/mo
- **Key pattern:** a LIVE DASHBOARD DEMO on the landing as social proof — shows real ingestion happening

## Mem0 — proof-not-promises, shortest path to first memory

```
Landing: hero has a copy-to-clipboard SDK snippet + "62,590 GitHub stars"
  → CTA: "Get Started" (app.mem0.ai)
  → signup → API key from dashboard
  → Quickstart: pip install mem0ai → add memory → search — "<5 minutes"
  → ⭐ "Sign up as an agent: mint a working API key in four commands,
       no email or dashboard required"
```

- **Positioning:** production memory layer, "for developers who want proof, not promises"
- **Pricing:** Hobby free (10k add req/mo + 1k retrievals, unlimited end-users) · Starter $19 · Growth $79 · Pro $249 · Enterprise custom
- **Key pattern:** THE ZERO-EMAIL AGENT SIGNUP — four CLI commands mint a key, no account
- OSS gravity: 62k stars as the trust anchor on the landing

## Honcho — integration-first, $100 free credits

```
Landing: benchmarks first (LoCoMo 89.9%, LongMem S 90.4%, BEAM), then INSTALL CTAs:
         Agent Skill (npx skills add) · Claude Code plugin · Codex plugin · OpenClaw
  → CTA: "Start Building" OR "Vibecoding Setup"
  → signup → API key → "$100 free credits on sign up" (~2,500× the quickstart cost)
  → Quickstart: uv add honcho-ai → create peers → ingest 14-message conversation
      → query reasoning (context())
  → OR 2-min MCP config / npx skills add
```

- **Positioning:** "memory that reasons" — continual learning, token savings (60-90%), SOTA benchmarks
- **Pricing:** pure usage — $2/M tokens ingested, retrieval free, $100 free credits
- **Key pattern:** LANDING LEADS WITH INSTALL COMMANDS IN THE TERMINAL, not product narrative; generous credits so the first write never fails

## Hindsight (Vectorize) — install-itself, benchmark-led

```
Landing: benchmark (94.6% LongMemEval vs Zep 71.2%) + "learns from mistakes"
  → CTA: "View on GitHub" (MIT OSS) / "Book a demo"
  → ⭐ "Memory that installs itself": npx add-skill vectorize-io/hindsight
      → agent reads docs, writes config, registers MCP tools — zero boilerplate
  → Self-host free (Docker, MIT, no limits, no telemetry) OR Cloud pay-as-you-go
```

- **Positioning:** "agent memory that learns" — judgment over retrieval, 4 memory networks
- **Pricing:** self-host = truly free (MIT, no usage limits); Cloud = pay-as-you-go tokens, "start for free"; Enterprise = contact
- **Key pattern:** THE INSTALL-ITSELF ONBOARDING — one command and the agent sets up its own memory

---

## The shared pattern (what all four converge on)

| Journey step | How they do it |
|---|---|
| **Entry CTA** | "Start Building / Get Started" primary; sales secondary (Zep/Hindsight) |
| **Signup** | Email or OAuth, minimal friction |
| **First artifact** | **API key is THE onboarding artifact** — dashboard is de-emphasized |
| **Free headroom** | Honcho $100 credits · Zep 10k credits/mo · Mem0 10k adds — generous enough that the FIRST SESSION never dead-ends |
| **Aha moment** | Engineered as **add memory → retrieve it**, in the terminal, <5 min |
| **Agent self-onboarding** | Mem0: 4-command key mint, no email. Hindsight: npx self-install |
| **Dashboard role** | Management surface, NOT the hero |
| **Trust entry** | Benchmarks (LongMemEval/LoCoMo) as the opening argument (Hindsight, Honcho) or live demo / OSS stars (Zep, Mem0) |

## Free-tier baseline (feeds pricing)

| Product | Free tier |
|---|---|
| Mem0 Hobby | 10,000 add requests/mo + 1,000 retrieval requests/mo, unlimited end-users, 1 project |
| Zep Free | 10,000 credits/mo |
| Honcho | $100 free credits (~2,500× quickstart cost) |
| Hindsight | Self-host truly free (MIT, no limits, no telemetry); Cloud pay-as-you-go "start for free" |
| **Tortoise (updated)** | **10,000 write ops/mo** (raised 2026-08-08 to match baseline) |

## Implications for Tortoise

1. **Terminal-first aha, dashboard-as-management.** Every competitor's quickstart is: install SDK → write memory → search it, in code, <5 min. Our dashboard is the hub today; it should be the *management surface* (keys, sessions, graphs, billing) while the aha happens in the terminal/SDK. The welcome page already starts this (key + quickstart + curl snippet) — the dashboard should not be the required path to first value.

2. **Zero-email agent signup is the missing move.** Mem0 mints a key with four commands; Hindsight self-installs. Our provisioning pipeline (edge function → team_memberships → key) already exists — a `tortoise signup` CLI that calls it and returns the key is a small surface that captures the vibecoder + autonomous-agent segments. Filed as #666 (see issue).

3. **Benchmarks as the landing's opening argument.** Hindsight and Honcho lead with LongMemEval/LoCoMo numbers. We have no published benchmark on the landing. Candidates for our claim: epistemic features competitors lack (NAND/confidence propagation), or a LoCoMo/LongMemEval run when ready.
