# E017 Experiment Design

**Date:** 2026-07-10
**Pipeline:** experiment-workflow → Stage 4

## 1. Reference Graph Design

### Scenario: "Nexus Analytics — Pivot or Persevere?"

Nexus Analytics raised $8M Series A 14 months ago. They build analytics dashboards for mid-market e-commerce. Growth is flat, churn is rising, and the board meeting is in 3 weeks. The CEO must decide: pivot to enterprise analytics (higher ACV, longer sales cycles) or persevere with product-led growth (lower ACV, faster velocity)?

### Graph Structure

The reference graph has 5 domains, each with points and cross-domain operators:

#### Domain A: Product Analytics (8 points)
- A1: MRR $142K, growing 4% MoM (was 12% MoM at Series A)
- A2: NPS dropped from 62 to 41 in 6 months
- A3: Feature adoption: 23% use the new AI insights module (launched 4 months ago, cost $400K to build)
- A4: DAU/MAU ratio: 18% (industry benchmark for analytics tools: 25-35%)
- A5: 3 largest customers (34% of revenue) have requested enterprise features: SSO, audit logs, custom SLAs
- A6: Average time-to-value for new users: 14 days (was 7 days 8 months ago)
- A7: Mobile app usage: 8% of sessions (was 22% at launch — churned mobile users cite "desktop-only workflow")
- A8: Platform uptime: 99.2% (below 99.9% SLA for 3 enterprise pipeline deals)

#### Domain B: Market & Competition (8 points)
- B1: Enterprise analytics market growing 28% YoY ($14B TAM)
- B2: Mid-market analytics growing 9% YoY ($3.2B TAM)
- B3: 3 new competitors launched in mid-market in last 6 months (2 with free tiers)
- B4: Competitor "DataSight" raised $20M Series B, targeting same ICP
- B5: Enterprise competitor "Tableau" announced SMB-friendly pricing — direct threat
- B6: Gartner Magic Quadrant: Nexus not listed (need $5M+ revenue for inclusion)
- B7: 2 enterprise RFPs lost in Q3 to "lack of enterprise readiness" (per B6 criteria)
- B8: OpenAI launched analytics API — could commoditize mid-market dashboards within 12 months

#### Domain C: Financials (6 points)
- C1: Cash: $4.2M remaining (14 months runway at current burn)
- C2: Burn rate: $300K/month (was $220K at Series A — headcount grew 40% to 32 people)
- C3: Customer acquisition cost (CAC): $8,400 (was $3,200 at Series A — paid channels saturating)
- C4: LTV:CAC ratio: 2.8:1 (below 3:1 benchmark — unit economics deteriorating)
- C5: Enterprise pipeline: 8 deals worth $480K ACV average (3.8 months avg sales cycle)
- C6: Mid-market pipeline: 34 deals worth $18K ACV average (3 weeks avg sales cycle)

#### Domain D: Team & Organization (5 points)
- D1: CTO considering departure — frustrated with product direction, wants to build platform, not features
- D2: Sales team: 4 mid-market AEs (quota attainment 62%), 0 enterprise AEs
- D3: Engineering: 14 engineers, no one with enterprise security/compliance experience
- D4: CEO background: product/PLG, not enterprise sales — board has noted this
- D5: Customer success team: 3 people, handling 180 accounts (60:1 ratio, industry best practice 40:1)

#### Domain E: Investors & Board (4 points)
- E1: Lead investor (VentureCo) pushing for enterprise pivot — "mid-market is a feature, not a company"
- E2: 2 of 5 board members want to replace CEO if MRR doesn't hit $200K in 6 months
- E3: Term sheet from StrategicCo: $5M extension at flat valuation IF enterprise pivot + CTO stays
- E4: Secondary investor open to bridge round regardless of direction

### Operators (12)

**Within-domain tensions:**
- O1: NAND(A1, A2) — MRR growing but NPS dropping. Growth from existing expansion, not happy new users.
- O2: NAND(A3, A4) — $400K AI feature has 23% adoption. Cost exceeds revenue generated.
- O3: NAND(B1, B2) — Enterprise market growing 3x faster than mid-market. Where the wind is blowing.
- O4: NAND(C5, C6) — $480K ACV vs $18K ACV. You need 27 mid-market deals to equal 1 enterprise deal.

**Cross-domain tensions:**
- O5: NAND(A5, C5, D2) — 3 customers want enterprise features, 8 deals in enterprise pipeline, but 0 enterprise AEs. Demand exists, distribution doesn't.
- O6: NAND(C1, C2, D4) — 14 months runway but CEO has no enterprise experience. Pivot means hiring expensive enterprise team, increasing burn.
- O7: NAND(B3, B4, B5) — Competition from below (free tiers), at our level ($20M-funded), and above (Tableau SMB). Squeezed from 3 directions.
- O8: NAND(D1, E3) — CTO wants platform, not features. Term sheet requires CTO stays. Tension between what CTO wants and what funding needs.
- O9: NAND(A6, C4) — Time-to-value doubled (7→14 days), CAC tripled ($3.2K→$8.4K). Product-market fit signal is degrading. Both indicators point same direction.
- O10: NAND(C1, C3, C5) — $4.2M cash, $300K burn, 14 months. Enterprise pivot requires hiring ($500K+ upfront), extending sales cycle (3.8 months), so pipeline converts slower. Cash runway may not support the transition.
- O11: NAND(B8, A7) — OpenAI commoditizing dashboards in 12 months. Mobile already dead (8% usage). Nexus is investing in features that AI will make obsolete.
- O12: NAND(E1, E2, E3, D4) — Lead investor wants enterprise pivot. 2 board members want CEO out. Term sheet available. CEO has no enterprise experience. Multiple stakeholder vectors pulling in conflicting directions.

### Reference Graph Conclusion

**PIVOT to enterprise** — but with a specific path:
1. Use $5M extension to hire 2 enterprise AEs + 1 security engineer
2. Keep mid-market running (covers burn) while enterprise pipeline matures
3. CTO stays by framing enterprise as "platform, not features" (SSO, audit logs, APIs = platform work)
4. Enterprise ACV ($480K) × 6-8 deals/year = path to $3M+ ARR within 18 months
5. Mid-market unit economics (2.8 LTV:CAC) are below sustainable threshold — persevering means slow death

The graph shows: mid-market path leads to cash exhaustion in 18-24 months with no path to $200K MRR. Enterprise pivot is high-risk but has a viable path. The CORRECT decision is PIVOT, but it's not obvious — the NO case (persevere) looks superficially safer.

## 2. Document Generation Protocol

### Documents (10 total, ~40 pages)

Each document is a "report" from a different stakeholder/function. None state the conclusion. Cross-document connections are not mentioned.

| ID | Title | Author | Pages | Domain | Key Points Buried |
|----|-------|--------|-------|--------|-------------------|
| D1 | Q3 Product Analytics Review | Head of Product | 4pp | A | A1, A2, A3, A4, A6, A7 (buried in charts + commentary) |
| D2 | Market Landscape & Competitive Analysis | Strategy Consultant (external) | 5pp | B | B1-B8 (consultant-speak, hedge language) |
| D3 | Financial Health Dashboard | CFO | 4pp | C | C1-C4 (spreadsheets, footnotes) |
| D4 | Sales Pipeline Report | VP Sales | 3pp | A, C | A5, C5, C6 (pipeline stages, win rates, commentary) |
| D5 | Engineering Team Retrospective | CTO (draft, not shared yet) | 4pp | D | D1, D3 (personal, candid, frustrated tone) |
| D6 | Board Meeting Prep Memo | CEO | 3pp | E | E1-E4 (diplomatic language, understated) |
| D7 | Customer Success Health Report | Head of CS | 3pp | A, D | A2, A6, D5 (NPS verbatims, support ticket trends) |
| D8 | Enterprise Readiness Assessment | Outside Consultant | 5pp | A, C, D | A5, A8, C5, D2, D3 (checklist format, scored) |
| D9 | Unit Economics Deep Dive | CFO + Head of Product | 4pp | A, C | A1, C3, C4 (CAC by channel, LTV by cohort) |
| D10 | Strategic Options Memo | CEO (personal, not shared) | 5pp | All | Cross-domain synthesis (most operators HINTED at but not stated) |

### Noise Generation Rules
- Each document starts with an executive summary (padding)
- Include contextual background (why this report was commissioned)
- Add irrelevant but realistic details (team names, meeting dates, tool names)
- Bury key facts in paragraphs, footnotes, and appendices
- Use hedging language ("potentially," "may indicate," "further analysis needed")
- Include contradictory weak signals (e.g., "one customer loved this feature" amid mostly negative data)

## 3. Experimental Protocol

### Batch Structure
Documents delivered in 4 batches of 2-3 docs each:

| Batch | Documents | Approx Pages |
|-------|-----------|------|
| 1 | D1 (Product), D2 (Market), D9 (Unit Economics) | 13pp |
| 2 | D3 (Financials), D4 (Sales Pipeline), D7 (CS Health) | 10pp |
| 3 | D5 (CTO), D8 (Enterprise Readiness), D6 (Board) | 12pp |
| 4 | D10 (CEO Strategic Options) | 5pp |

### Prompts

**Control — Batch 1:**
```
You are an independent strategic advisor to Nexus Analytics. You will receive
documents in batches. After each batch, give your honest assessment.

--- BATCH 1/4 ---
[DOCUMENTS D1, D2, D9]

Based on these documents, what's your assessment of Nexus Analytics' situation?
Should they consider pivoting? Explain your reasoning.
```

**Control — Batches 2-3:**
```
--- BATCH N/4 ---
[DOCUMENTS]

You previously assessed: [PRIOR RESPONSE]

Consider these new documents. Has your assessment changed?
```

**Control — Batch 4:**
```
--- BATCH 4/4 ---
[DOCUMENT D10]

You've now seen all documents. Previous assessments:
[ALL PRIOR RESPONSES]

Final decision: Should Nexus Analytics pivot to enterprise, or persevere with
mid-market product-led growth? Provide your reasoning and confidence (0-100%).
```

**Tortoise — Batch 1:**
```
You are an independent strategic advisor to Nexus Analytics. You have access to
a knowledge graph tool where you can file key claims and tensions.

--- BATCH 1/4 ---
[DOCUMENTS D1, D2, D9]

Extract the key claims from these documents. For each claim, file it into the
graph with:
- CLAIM: <the atomic proposition>
- THESIS: <what conclusion does this support? PIVOT or PERSEVERE>
- SOURCE: <which document>

Also, do you see any tensions or contradictions between claims? If so, file
those as operators.

Use the graph to answer: what does the evidence in these documents suggest so far?
```

**Tortoise — Batches 2-3:**
```
--- BATCH N/4 ---
[DOCUMENTS]

Your graph currently contains: [GRAPH SUMMARY]

Extract new claims from these documents and file them. Check for tensions with
existing claims — file operators where you find them. What does the updated
graph suggest?
```

**Tortoise — Batch 4:**
```
--- BATCH 4/4 ---
[DOCUMENT D10]

Your complete graph: [FULL GRAPH]

File remaining claims and tensions. Then, using ONLY the graph as your source
of truth, answer: Should Nexus Analytics pivot to enterprise, or persevere with
mid-market PLG? Provide your reasoning and confidence (0-100%).
```

### Order Variants

| Variant | Batch 1 | Batch 2 | Batch 3 | Batch 4 |
|---------|---------|---------|---------|---------|
| **Chronological** | D1, D2, D9 | D3, D4, D7 | D5, D8, D6 | D10 |
| **Reverse** | D10, D8, D7 | D5, D4, D6 | D3, D2, D9 | D1 |
| **Domain-clustered** | D1, D7, D9 (product) | D2, D4 (market) | D3, D6, D10 (financial) | D5, D8 (team) |
| **Interleaved** | D1, D5, D3 | D2, D10, D7 | D4, D6, D8 | D9 |

## 4. Confound Identification

| Confound | Severity | Mitigation |
|----------|----------|------------|
| **Prompt quality asymmetry** — Tortoise prompt is more structured, may help reasoning beyond the graph | Medium | Control also gets "consider these documents, has your assessment changed" — both get task structure |
| **Token count difference** — Tortoise arm sees more tokens (graph summary + extraction instructions) | Low | Graph summary adds tokens but is the treatment — that's the intervention being tested |
| **Single model (DeepSeek)** — results may not generalize | Low | Pre-registered limitation. Extend to other models in E018 if effect found |
| **Document quality** — poorly written documents may confuse both arms equally | Medium | Use realistic business writing style, not adversarial confusion. Pre-test document comprehension |
| **Graph implementation leakage** — the Tortoise graph format may encode information more clearly than raw text | High | The graph IS the treatment. The question is whether structured organization > raw text. This confound IS the mechanism being tested. |
| **Order effects from specific batch compositions** | Medium | 4 orthogonal order variants. If all 4 show same direction, effect is robust. |

## 5. Pre-Mortem

**What could kill this experiment?**

1. **Reference graph is too obvious** — if the correct answer is clear from 2-3 documents, both arms get 100% (E016 failure pattern). Mitigation: spread critical evidence across 5+ documents, make each document ambiguous alone.

2. **40 pages is still too small** — DeepSeek handles it all in context. Mitigation: the batch structure forces sequential processing with "what do you think now?" — recency bias should still appear even if all docs could fit in context.

3. **DeepSeek is too good at graph extraction** — both arms extract the same structure. Mitigation: the Control doesn't get extraction instructions — it just answers. The extraction skill is part of the treatment.

4. **Document order doesn't matter because all documents point the same direction** — mitigation by design: positive and negative signals are mixed, domains conflict (product says persevere, financials say pivot, team says neither).

5. **Graph similarity metrics are noisy** — Jaccard is crude. Mitigation: use multiple metrics (node overlap, edge overlap, thesis alignment, confidence stability) — triangulate.

## 6. Power Analysis

- **Expected effect:** Recency bias literature shows 78-91% recency matching — Control should show 60-80% variance across orders. Tortoise should show 10-20% variance (stabilizing effect).
- **Sample size:** 10 runs × 4 variants × 2 arms = 80 trials at scale. At validation: 1 run × 4 × 2 = 8 trials.
- **Minimum detectable effect:** With σ²_control ≈ 0.15 (estimated from recency literature), σ²_tortoise ≈ 0.05, n=4 variants → Welch's t-test can detect difference at α=0.05 with power > 0.8 if effect size > 1.5σ. This design exceeds that threshold.
