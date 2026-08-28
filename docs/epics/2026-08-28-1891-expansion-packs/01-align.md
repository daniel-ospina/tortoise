---
title: "Epic #1891 — Align Decision: Expansion packs as a configurable product"
type: decisions
domain: product
doc_status: draft
created: 2026-08-28
ownedBy: epistemic-team
---

# Align Decision — Expansion packs as a configurable product (hosted + self-hosted)

> Issue: #1891 · Pipeline stage: Align · Skill: epic-align

## Strategy Alignment Decision

**Feature:** Expansion packs as a configurable product — ship in all installs, authoring surface, enforcement, hosted + self-hosted parity
**Decision:** PROCEED (phased — value-ordered slices; see Recommendation)

**Alternatives considered:**
1. **Packaging-only fix (G1+G2) + demo pack, nothing else** — close the clone-only gap so the current prospect can self-host with packs, defer everything else to #1154/#557. Why rejected as the *whole* answer: it fixes the demo but leaves "customers create their own expansion packs" — the promise made in the sales call — impossible without editing the repo, and the hosted side stays frozen at the fixed starter set. It is retained as **Slice 1** (the urgent revenue-protecting part).
2. **Skip pack productization; serve the prospect with docs + a hand-built rules-with-why model** — treat packs as internal config, give the prospect a written recipe. Why rejected: the prospect explicitly asked to *create his own pack* ("I might need to build an expansion pack for that… we can do it together"); a docs-only answer fails the demonstrated ask and forfeits the pattern that makes the product extensible for every future vertical.
3. **Build hosted custom packs first, defer self-host packaging** — Why rejected: hosted has zero paying tenants and a security-sensitive surface (arbitrary YAML ingestion, #1154 registry singletons); self-host is where the current deal and the BSL free-tier funnel live. Sequencing hosted-first inverts value order.

**Profit impact:** Causal chain: packaging fix → every documented install path loads packs (today: empty registry on docker/wheel) → the 2026-08-28 prospect's self-host trial succeeds → he becomes a channel partner (his pipeline includes a 5,000-seat hospital security team; large-deploy rate card needed) → hosted migration + channel revenue. Authoring surface (CLI + `TORTOISE_PACKS_DIR`) is the difference between "packs work for the founder" and "packs work for customers" — it is the product's extensibility story and the reason verticals (agent-ops, legal, healthcare) don't need us to build their ontology. Rough magnitude: $100s/mo now (prospect + beta cohort), $1,000s–$10,000s/mo when a channel deal lands (12-month horizon). **Estimate caveat:** pre-revenue — no cohort-size or conversion data exists yet (the rate card itself is unbuilt); treat the band as direction, not forecast. **Falsification point for Slice 1's revenue story:** if the prospect's trial succeeds with packs on a packaged install AND either (a) he closes a channel deal or (b) another beta user pays, within 60 days of Slice 1 shipping, the causal chain holds; if a successful trial produces no revenue signal in 90 days, the channel-magnitude assumption is void and later slices should be re-sequenced toward hosted direct-sales levers. The packaging slice alone is ~days of work protecting the entire funnel — cheapest insurance in the pipeline.

**Eisenhower placement:** **Important + Not-Urgent (Schedule)** *for the full epic*; **Important + Urgent (Do Now)** *for Slice 1* (packaging + packs-dir + mini agent-ops demo pack). Rationale: the prospect conversation created urgency, but the full parity epic (hosted custom packs, enforcement ladder) is weeks of work that does not gate revenue this month. Slice 1 is Do-Now for the **silent-broken-default** reason, not the prospect: every documented install path (docker compose, pip wheel) runs with an empty pack registry, so the product's core extensibility claim silently fails for every new user on every supported install — a defect, not just a demo risk. (The prospect is a hacker-type who can work around it from a clone; the default itself is the liability.) The rest is Schedule. Classification must not hide the Slice-1 urgency behind the epic's Schedule placement.

**Key assumptions:**
- The prospect follows through on self-hosting and building a pack — confidence: medium (strong intent signal, but pre-revenue channel talk).
- Custom-pack authoring is a real customer need beyond this prospect (hacker-type early users) — confidence: medium (the product's own thesis: early users build their own connectors).
- Enforcement-ladder operationalization improves extraction without regressing the now-integrated deterministic chain pass (classify-later #1695 completed 2026-08-27) — confidence: medium (adversarial research warns over-constraint causes extraction refusal; warn-not-block default mitigates; the wiring still carries regression risk over a recently-shipped extraction path).
- Hosted per-tenant custom packs are demanded before sub-tenancy (#557) — confidence: low (no paying hosted tenants; #557 is the channel path).
- Packaging fix has no hidden cost (wheel data-file layout, Docker layers) — confidence: high (bounded, well-understood packaging work).

**Recommendation:** PROCEED, value-ordered. Land Slice 1 (G1 packaging + G2 `TORTOISE_PACKS_DIR` + G5 authoring doc + the agent-ops rules-with-why starter pack) first — it unblocks the live deal and every self-host install. Then authoring tooling (G3 self-host CLI), then enforcement (G4), then hosted custom packs (G3 hosted) as the final slice gated on #1154 and sequenced before/with #557. Do not build dashboard pack UI or a marketplace in this epic (non-goals).

---

## Step 1 — Adversarial Strategy Test

**Alternatives (see above):** packaging-only fix; docs-only; hosted-first.

**Anti-post-rationalization — strongest reasons NOT to build this:**
1. **Zero paying customers.** The founder needs revenue "soon-ish"; this epic is weeks of engineering that doesn't itself sell anything. The packaging slice is the only part with a direct revenue-protection story.
2. **The prospect is a hacker-type who would edit the repo.** "Custom-pack authoring for customers" may be solving a problem the early adopter doesn't have — he said he'd "self-host and poke around and help contribute."
3. **Enforcement (G4) is speculative.** The classify-later experiment (#1695) completed 2026-08-27 and its deterministic chain pass is now integrated into every extraction arm; wiring kind-level `retry|block` on top of that recently-shipped path still carries real regression risk, and the 2026-08-05 research itself warns that rigid constraints cause extraction refusal. Building it now risks churn on a freshly-shipped extraction surface.
4. **Hosted custom packs are a security surface with no demand.** Arbitrary YAML ingestion on a multi-tenant host, with #1154's process-global singletons unresolved, is a landmine for zero paying hosted tenants.
5. **The "expansion pack marketplace" vision (20 packs per graph) is aspirational** and could pull the epic into a distribution platform nobody asked for.

**Opportunity cost:** not building this → the alternative spend is: hosted onboarding polish, pricing/rate card (explicitly requested by the prospect for the 5,000-seat deal), and the Letta connector for the demo. Those are real. The mitigation is sequencing: Slice 1 is small and does not crowd out the rate card; the epic's later slices are deferrable without killing the deal.

## Step 2 — Eisenhower Matrix

| | Urgent | Not Urgent |
|---|---|---|
| **Important** | **Slice 1**: packaging (packs ship in docker/wheel), `TORTOISE_PACKS_DIR`, authoring doc, agent-ops demo pack | **Slices 2–4**: authoring CLI, enforcement wiring, hosted custom packs · **rate card** (out-of-epic; tracked as its own issue — deal-critical, explicitly requested by the prospect for the 5,000-seat deal, but not this epic's scope) |
| **Not Important** | — | Dashboard pack UI, marketplace (non-goals) |

Justification: the broken install path (empty registry on every packaged install) is a silent default defect affecting every new user — Do Now. The parity/enforcement/hosted work is strategically important but not time-critical: Schedule it, in value order. The rate card is Important-Not-Urgent but **out of this epic's scope** (it is not convenience-classified as unimportant — it is tracked separately because it is blocked on cost data, not on this epic).

## Step 3 — Profit Growth Alignment

- **Feature → user behavior → revenue:** (1) packaging fix → prospect can self-host with packs on the recommended path → trial succeeds → channel partner onboarding → hosted migration + rate-carded deals; (2) authoring surface → customers build vertical packs (agent-ops, legal, healthcare) without us → product becomes the memory layer for N verticals → usage-based hosted revenue; (3) hosted custom packs → enterprise/channel readiness (5,000-seat hospital class).
- **Faster path to same profit?** The rate card + channel deal is arguably the faster direct-revenue path — but it is blocked on cost data and the product *working for the partner*, which the packaging slice provides. The epic and the rate card are complements, not competitors.
- **Magnitude:** $100s/mo near-term (beta cohort + prospect); $1,000s–$10,000s/mo at 12 months conditional on channel deals; Slice 1 cost is ~days.

## Step 4 — Decision Rationale

See "Strategy Alignment Decision" above.

## Step 5 — Routing

**PROCEED** → hand off to epic-research (Stage 2). Slice ordering enforced at Decompose: 1) packaging + packs-dir + demo pack + authoring doc, 2) authoring CLI, 3) enforcement, 4) hosted custom packs (gated on #1154).
