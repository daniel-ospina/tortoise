# Clarity (heyclarity.dev)

> AI implementation consultancy + Self-Model API. "We build and fix AI products that actually work."

---

## 1. Overview

| Field | Value |
|---|---|
| Founded | ⚠️ Unknown |
| HQ | Birmingham, AL, USA |
| Funding raised | $0 — bootstrapped |
| Team size | 2–10 (LinkedIn) |
| Markets | US (i18n into 10 languages — global ambition) |
| Legal entity | Epistemic Me |

*Last checked: 2026-07-06*

---

## 2. Product Type

Two-product model:

**A) AI implementation consultancy** — Sprint Zero ($15K/4 weeks) and Full Build ($50K+/6–12 weeks). Rebuilds broken AI products. Primary revenue engine.

**B) Self-Model API** — Early access. 130 endpoints (OpenAPI Spec v1.0.0). Models users as "self-models" — structured digital twins tracking beliefs, confidence scores, alignment, and behavioral patterns. `api.heyclarity.dev`. Not yet GA.

*Last checked: 2026-07-06*

---

## 3. Positioning & Messaging

**Tagline:** "We build and fix AI products that actually work."

**Sub-tagline:** "Other firms quote $1M+ and 6 months. We ship in 6 weeks for a fraction."

**Brand voice:** Aggressively anti-consulting, transparent, data-driven. Uses code-snippet metaphors. Names competitors' failure modes explicitly. "We're not another prompt engineering shop." "Fixed pricing. No hourly billing surprises."

[Source](https://heyclarity.dev/) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 4. Target Audience

Mid-market to enterprise AI product teams with broken AI products — those burned by big consultancies or stalled internal builds.

Explicitly: "For Growth Teams," "For Enterprise AI," "For AI Product Teams."

*Last checked: 2026-07-06*

---

## 5. Business Model & Pricing

| Tier | Price | Duration | What |
|------|-------|----------|------|
| **Sprint Zero** | $15K flat | 4 weeks | Audit + roadmap + P0 fixes. Satisfaction guarantee. |
| **Full Build** | From $50K | 6–12 weeks | Full roadmap implementation, production deploy. |
| **Maintenance** | Month-to-month, reduced rate | Ongoing | Monitoring, incident response, model updates. |
| **Self-Model API** | ⚠️ Not public | Early access | Gated behind "Get Early Access" CTAs |

[Source](https://heyclarity.dev/pricing) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 6. Product & Features

### Self-Model API (early access)
- 130 endpoints, OpenAPI Spec v1.0.0
- Auth: X-API-Key header
- Core: self-models CRUD, beliefs CRUD (with confidence 0–1 + observation counts), context assembly (`POST /external/context` → `ai_ready` format), observation contexts, analytics, recommendations, journey stages, agent chat
- Integration: MCP, Skills, Direct API
- SDKs: ⚠️ None — cURL examples only in API playground

### Service offering
- Codebase + data + AI audit
- AI quality evaluation
- P0 fixes to production
- Eval infrastructure + quality gates
- Production deploy, monitoring, alerting
- 30-day post-launch support

[Source](https://heyclarity.dev/api-playground), [Source](https://heyclarity.dev/platform) — retrieved 2026-07-06

### Confidence Scoring Mechanism (deep-dive)

Clarity's confidence model is built on the **Free Energy Principle / Active Inference** framework from neuroscience (Friston, Levin, Seth, Clark). Three intertwined mechanisms:

**Precision-Weighted Bayesian Updating:**
- Every belief has `confidence` (0.0–1.0) representing posterior precision — how strongly evidence supports the belief relative to uncertainty
- Learning rate is precision-weighted: `Δbelief = precision × prediction_error`
- High confidence = small updates from new evidence; low confidence = large updates
- Confidence = inverse of posterior variance of the belief's generative distribution

**Observation Count as Evidence Mass:**
- Every belief tracks `observations` — discrete count of evidence pieces
- Confidence climbs with consistent reinforcement: "Prefers concise" starts at 0.4 → climbs to 0.85 after 14 consistent observations
- Functionally a Beta prior update — each observation is a "success" reinforcing the belief

**Time-Decay on Staleness:**
- Beliefs not reinforced within a window lose confidence: "No confirmation in 90 days → confidence drops from 0.8 → 0.6"
- Discount factor on effective evidence count — stale observations count less

**Contradiction Handling:**
- New conflicting evidence → confidence drops (belief doesn't flip immediately): "Prefers concise (conf: 0.87) + asks for detail → conf drops to 0.5"
- If contradictory pattern persists, belief content updates
- System doesn't hold contradictory beliefs at high confidence

**Key design patterns (relevant for epistemic graph):**
- Confidence is **traceable** — every belief links to observations that formed it (provenance)
- Beliefs are **user-inspectable and correctable** — manual correction recalibrates
- **No raw PII** — tracks beliefs about preferences/constraints, not identity
- ~130 API endpoints including `POST /beliefs`, `GET /beliefs/{belief_id}`, `GET /journey/confidence`

⚠️ All confidence scoring details from API playground + platform page + blog. No formal paper published.

*Last checked: 2026-07-06*

*Last checked: 2026-07-06*

---

## 7. Go-to-Market & Acquisition

| Channel | Activity |
|---------|----------|
| **Content** | Blog: 285 articles on self-models, AI evaluation, agent architectures. AI-authored under "Robert Ta's Self-Model." |
| **Research page** | 22 curated foundational papers (Friston, Levin, Seth, Clark, Metzinger) |
| **Newsletter** | "Weekly insights on AI personalization, self-models" |
| **Podcast** | "Self Aligned" — hosted by founders |
| **Sales** | Inbound via "Book a Sprint Zero Call" CTAs → 30-min scoping → fixed quote |
| **Social** | Robert Ta: 4,300+ LinkedIn followers. No Twitter/X presence. |

[Source](https://heyclarity.dev/) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 8. Traction & Scale

| Signal | Value |
|---|---|
| GitHub stars | 1 (clarity-api-samples) |
| Funding | $0 (bootstrapped) |
| Public customers | 1 (Mystica: +60% revenue in 90 days) |
| Revenue | ⚠️ No public data |
| Press | ⚠️ None found |
| ⚠️ All traction data single-sourced from own marketing |

[Source](https://heyclarity.dev/) — retrieved 2026-07-06

*Last checked: 2026-07-06*

---

## 9. Online Presence & Content

| Metric | Value |
|---|---|
| Website | ✅ Live, polished, multi-language (10 languages) |
| Blog | 285 articles |
| API docs | Interactive OpenAPI playground |
| Research | 22 curated papers, 7 themes |
| ⚠️ Traffic | No data — too small for SimilarWeb |

*Last checked: 2026-07-06*

---

## 10. Community & Ecosystem

| Channel | Signal |
|---------|--------|
| GitHub | 1 star |
| LinkedIn | Robert Ta: 4,300+ followers |
| Twitter/X | ⚠️ No account |
| Discord | ⚠️ None |
| Newsletter | Exists but subscriber count unknown |

**Verdict: No detectable community.** Two-founder consulting shop, not community-driven.

*Last checked: 2026-07-06*

---

## 11. Customer Sentiment

| Type | Data |
|------|------|
| Testimonial | 1 (Mystica CEO: "re-energized our entire tech stack. +60% revenue.") |
| Third-party reviews | ⚠️ None — no G2, Capterra, Reddit mentions |
| **Overall:** Near-zero independent sentiment. Single testimonial from only named customer. |

*Last updated: 2026-07-06*

---

## Notes & Sources

- **Primary:** [heyclarity.dev](https://heyclarity.dev/) — fetched live 2026-07-06
- **Pricing:** [heyclarity.dev/pricing](https://heyclarity.dev/pricing) — transparent comparison table
- **API:** [heyclarity.dev/api-playground](https://heyclarity.dev/api-playground) — 130 endpoints
- **⚠️ All traction data** single-sourced from their marketing. No independent verification.
- **⚠️ Self-Model API** pre-GA — no public users, no pricing, no adoption data.
- **⚠️ Case study** unverifiable — Mystica is not a publicly known company.

*Last updated: 2026-07-06*
