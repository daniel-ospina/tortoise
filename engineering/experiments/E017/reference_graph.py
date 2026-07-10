"""
E017 Reference Graph — Nexus Analytics: Pivot or Persevere?

31 claims across 5 domains, 12 operators forming trees, loops, and chains.
Conclusion: PIVOT to enterprise.
"""
from __future__ import annotations

CLAIMS: dict[str, dict] = {
    # ── Domain A: Product Analytics ──
    "A1": {
        "domain": "product",
        "thesis": "PERSEVERE",
        "label": "MRR Growth",
        "text": "MRR is $142K, growing at 4% month-over-month. At Series A (14 months ago) it was growing at 12% MoM. Growth is decelerating but still positive.",
    },
    "A2": {
        "domain": "product",
        "thesis": "PIVOT",
        "label": "NPS Decline",
        "text": "NPS dropped from 62 to 41 over the past 6 months. Customer satisfaction is deteriorating rapidly. Support ticket volume is up 40% in the same period.",
    },
    "A3": {
        "domain": "product",
        "thesis": "PIVOT",
        "label": "AI Feature Failure",
        "text": "The AI-powered insights module, launched 4 months ago at a cost of $400K in engineering time, has only 23% feature adoption. The feature was the flagship initiative of the current product roadmap.",
    },
    "A4": {
        "domain": "product",
        "thesis": "PIVOT",
        "label": "Low Engagement",
        "text": "DAU/MAU ratio is 18%. The industry benchmark for analytics tools is 25-35%. Users log in infrequently, suggesting the product is not a daily workflow tool.",
    },
    "A5": {
        "domain": "product",
        "thesis": "PIVOT",
        "label": "Enterprise Feature Requests",
        "text": "The 3 largest customers, representing 34% of total revenue, have all requested enterprise-grade features: SSO, audit logs, and custom SLAs. These features are not on the current roadmap.",
    },
    "A6": {
        "domain": "product",
        "thesis": "PIVOT",
        "label": "Time-to-Value Doubling",
        "text": "Average time-to-value for new users is now 14 days, up from 7 days just 8 months ago. New users take twice as long to reach their first meaningful insight from the product.",
    },
    "A7": {
        "domain": "product",
        "thesis": "PIVOT",
        "label": "Mobile Decline",
        "text": "Mobile app usage is down to 8% of total sessions, from 22% at launch. Churned mobile users cite 'desktop-only workflow' as their primary reason for leaving.",
    },
    "A8": {
        "domain": "product",
        "thesis": "PIVOT",
        "label": "Uptime Below SLA",
        "text": "Platform uptime is 99.2% over the past quarter, below the 99.9% SLA that 3 enterprise pipeline deals require. Two deals have specifically asked about reliability track record.",
    },

    # ── Domain B: Market & Competition ──
    "B1": {
        "domain": "market",
        "thesis": "PIVOT",
        "label": "Enterprise Market Growth",
        "text": "The enterprise analytics market is growing at 28% year-over-year, currently valued at $14B. Mid-market analytics is growing at 9% YoY, valued at $3.2B.",
    },
    "B2": {
        "domain": "market",
        "thesis": "PERSEVERE",
        "label": "Mid-Market Still Growing",
        "text": "At 9% YoY growth, the mid-market analytics space is not dying — it's maturing. $3.2B is a meaningful TAM for a company at Nexus's scale. Many successful companies have built $100M+ businesses in maturing mid-markets.",
    },
    "B3": {
        "domain": "market",
        "thesis": "PIVOT",
        "label": "New Competitors Below",
        "text": "Three new competitors have launched in the mid-market analytics space in the last 6 months. Two of them offer free tiers, directly attacking Nexus's self-serve entry point.",
    },
    "B4": {
        "domain": "market",
        "thesis": "PIVOT",
        "label": "Well-Funded Rival",
        "text": "Competitor DataSight raised a $20M Series B and is targeting the same ICP as Nexus. Their marketing spend in Google Ads alone is estimated at $150K/month — 3x Nexus's total marketing budget.",
    },
    "B5": {
        "domain": "market",
        "thesis": "PIVOT",
        "label": "Tableau SMB Threat",
        "text": "Tableau recently announced SMB-friendly pricing at $15/user/month, undercutting Nexus's $25/user/month starter plan. Tableau's brand recognition and feature set far exceed Nexus's.",
    },
    "B6": {
        "domain": "market",
        "thesis": "NEUTRAL",
        "label": "Gartner Exclusion",
        "text": "Nexus is not listed in the Gartner Magic Quadrant for Analytics. Inclusion requires a minimum of $5M in annual revenue, which Nexus does not yet meet.",
    },
    "B7": {
        "domain": "market",
        "thesis": "PIVOT",
        "label": "Enterprise RFP Losses",
        "text": "Nexus lost 2 enterprise RFPs in Q3. Both prospects cited 'lack of enterprise readiness' as the reason — specifically, missing SSO, no audit log capabilities, and no dedicated account management.",
    },
    "B8": {
        "domain": "market",
        "thesis": "PIVOT",
        "label": "AI Commoditization Risk",
        "text": "OpenAI's recently announced analytics API could commoditize mid-market dashboard products within 12-18 months. The API generates insights from raw data without requiring a dedicated analytics platform.",
    },

    # ── Domain C: Financials ──
    "C1": {
        "domain": "financial",
        "thesis": "PERSEVERE",
        "label": "Healthy Runway",
        "text": "Nexus has $4.2M in cash remaining. With a current burn rate of $300K/month, this provides approximately 14 months of runway at current spending levels.",
    },
    "C2": {
        "domain": "financial",
        "thesis": "PIVOT",
        "label": "Burn Rate Increase",
        "text": "Monthly burn is now $300K, up from $220K at Series A. Headcount has grown 40% to 32 people. The burn increase has not been matched by proportional revenue growth.",
    },
    "C3": {
        "domain": "financial",
        "thesis": "PIVOT",
        "label": "CAC Explosion",
        "text": "Customer acquisition cost has risen to $8,400 per customer, up from $3,200 at the time of Series A. Paid marketing channels are showing clear signs of saturation with diminishing returns.",
    },
    "C4": {
        "domain": "financial",
        "thesis": "PIVOT",
        "label": "Unit Economics Below Benchmark",
        "text": "The LTV:CAC ratio is currently 2.8:1, down from 5.1:1 at Series A. The industry benchmark for healthy SaaS unit economics is 3:1 or above. Nexus is now below the sustainability threshold.",
    },
    "C5": {
        "domain": "financial",
        "thesis": "PIVOT",
        "label": "Enterprise Pipeline Value",
        "text": "The enterprise sales pipeline currently has 8 active deals with an average ACV of $480K. The average enterprise sales cycle is 3.8 months. Total pipeline value is $3.84M in potential ARR.",
    },
    "C6": {
        "domain": "financial",
        "thesis": "PERSEVERE",
        "label": "Mid-Market Pipeline Velocity",
        "text": "The mid-market pipeline has 34 active deals with an average ACV of $18K and an average sales cycle of just 3 weeks. Mid-market deals close 5x faster than enterprise deals.",
    },

    # ── Domain D: Team & Organization ──
    "D1": {
        "domain": "team",
        "thesis": "PIVOT",
        "label": "CTO Departure Risk",
        "text": "The CTO is considering leaving. He is frustrated with the product direction and wants to build a platform with APIs and infrastructure, not incremental user-facing features. He has not formally resigned but has expressed this to the CEO confidentially.",
    },
    "D2": {
        "domain": "team",
        "thesis": "PIVOT",
        "label": "No Enterprise Sales Capacity",
        "text": "The sales team consists of 4 mid-market account executives. None have enterprise sales experience. Quota attainment across the team is 62%. There are zero enterprise AEs on staff.",
    },
    "D3": {
        "domain": "team",
        "thesis": "PIVOT",
        "label": "No Security Engineering",
        "text": "The 14-person engineering team has no one with enterprise security or compliance experience. Building SSO, audit logs, and SOC 2 compliance would require new hires or extensive retraining.",
    },
    "D4": {
        "domain": "team",
        "thesis": "PIVOT",
        "label": "CEO PLG Background",
        "text": "The CEO's background is in product-led growth and self-serve SaaS. She has never led an enterprise sales organization. Two board members have privately noted this gap as a concern for an enterprise pivot.",
    },
    "D5": {
        "domain": "team",
        "thesis": "PIVOT",
        "label": "Customer Success Overloaded",
        "text": "The customer success team has 3 people managing 180 accounts — a 60:1 ratio. Industry best practice is 40:1 or lower. Response times have slipped to an average of 18 hours, up from 6 hours a year ago.",
    },

    # ── Domain E: Investors & Board ──
    "E1": {
        "domain": "investors",
        "thesis": "PIVOT",
        "label": "Lead Investor Pushing Pivot",
        "text": "VentureCo, the lead Series A investor, is strongly advocating for an enterprise pivot. Their partner told the CEO: 'Mid-market analytics is a feature, not a company. You need to go upmarket or you'll get eaten.'",
    },
    "E2": {
        "domain": "investors",
        "thesis": "PIVOT",
        "label": "Board CEO Confidence",
        "text": "Two of the five board members have indicated that if MRR does not reach $200K within 6 months, they will push for a CEO change. Current MRR is $142K, which at 4% MoM growth would reach approximately $180K in 6 months.",
    },
    "E3": {
        "domain": "investors",
        "thesis": "PIVOT",
        "label": "Conditional Term Sheet",
        "text": "StrategicCo has offered a $5M Series A extension at flat valuation, contingent on two conditions: the company must pivot to enterprise, and the CTO must commit to staying for at least 18 months.",
    },
    "E4": {
        "domain": "investors",
        "thesis": "PERSEVERE",
        "label": "Bridge Round Available",
        "text": "A secondary investor has indicated willingness to provide a $2M bridge round regardless of strategic direction. This would extend runway without requiring a pivot commitment.",
    },
}


OPERATORS: dict[str, dict] = {
    # ── Within-domain tensions ──
    "O1": {
        "type": "NAND",
        "inputs": ["A1", "A2"],
        "label": "Growth vs. Satisfaction",
        "text": "MRR is growing but NPS is dropping. The growth is coming from expansion of existing accounts, not from acquiring happy new users. A growing unhappy customer base is a ticking time bomb — expansion revenue from unhappy customers eventually churns.",
    },
    "O2": {
        "type": "NAND",
        "inputs": ["A3", "A4"],
        "label": "AI Investment vs. Engagement",
        "text": "The $400K AI insights module was the flagship initiative, yet only 23% of users adopt it and DAU/MAU is 18%. The most expensive product investment is reaching less than a quarter of users, and even those users don't engage daily.",
    },
    "O3": {
        "type": "COMPARE",
        "inputs": ["B1", "B2"],
        "label": "Enterprise vs. Mid-Market Growth",
        "text": "Enterprise analytics is growing at 28% vs mid-market at 9%. Even though mid-market at $3.2B isn't dead, the growth delta is 3:1. Revenue follows market growth — Nexus's 4% MoM in a 9% market means it's actually losing share, not gaining.",
    },
    "O4": {
        "type": "COMPARE",
        "inputs": ["C5", "C6"],
        "label": "Enterprise vs. Mid-Market ACV",
        "text": "Enterprise deals average $480K ACV vs $18K for mid-market. You need to close 27 mid-market deals to generate the same ARR as one enterprise deal. At 34 mid-market deals in pipeline with 3-week cycles, the math still doesn't scale — the top of funnel isn't big enough.",
    },

    # ── Cross-domain tensions ──
    "O5": {
        "type": "NAND",
        "inputs": ["A5", "C5", "D2"],
        "label": "Enterprise Demand vs. Sales Capacity",
        "text": "Three large customers explicitly want enterprise features AND eight enterprise deals are in pipeline, but the company has zero enterprise AEs and the current sales team is at 62% quota. Demand exists but the go-to-market machinery to capture it does not.",
    },
    "O6": {
        "type": "NAND",
        "inputs": ["C1", "C2", "D4"],
        "label": "Runway vs. CEO Experience",
        "text": "14 months of runway sounds safe, but an enterprise pivot requires hiring expensive enterprise AEs and security engineers (increasing burn) while extending sales cycles (delaying revenue). A CEO without enterprise experience leading this transition increases execution risk. The runway may not be as comfortable as it appears.",
    },
    "O7": {
        "type": "NAND",
        "inputs": ["B3", "B4", "B5"],
        "label": "Three-Front Competitive Squeeze",
        "text": "Nexus is being squeezed from three directions simultaneously: free-tier competitors attacking from below (B3), a $20M-funded rival at the same level (B4), and Tableau's SMB pricing from above (B5). The mid-market position is becoming indefensible — no single response addresses all three threats.",
    },
    "O8": {
        "type": "NAND",
        "inputs": ["D1", "E3"],
        "label": "CTO Wants Platform, Term Sheet Requires Him",
        "text": "The CTO wants to build a platform (APIs, infrastructure) but is frustrated by the current feature-focused roadmap. The term sheet from StrategicCo requires the CTO to stay. However, the enterprise pivot (SSO, audit logs, APIs) IS platform work — it aligns with what the CTO wants to build. The tension is resolvable if the pivot is framed as platform engineering, not feature engineering.",
    },
    "O9": {
        "type": "CORRELATE",
        "inputs": ["A6", "C4"],
        "label": "Time-to-Value × Unit Economics Degradation",
        "text": "Time-to-value doubled (7→14 days) at the same time CAC tripled ($3.2K→$8.4K). Both metrics point to the same underlying problem: the product is becoming harder to sell and harder to adopt. This is a product-market fit signal degrading in two independent dimensions simultaneously.",
    },
    "O10": {
        "type": "NAND",
        "inputs": ["C1", "C3", "C5"],
        "label": "Cash vs. Pivot Economics",
        "text": "$4.2M cash at $300K burn gives 14 months. But an enterprise pivot means: hiring 2 enterprise AEs ($250K/year each) + 1 security engineer ($200K/year) = $700K/year new burn. New burn would be ~$358K/month, reducing runway to ~11.5 months. Enterprise sales cycles are 3.8 months — the first enterprise deal may not close until month 5-6. Can the company survive the transition period?",
    },
    "O11": {
        "type": "NAND",
        "inputs": ["B8", "A7"],
        "label": "AI Threat × Mobile Abandonment",
        "text": "OpenAI's analytics API threatens to commoditize dashboard products within 12-18 months — the exact same timeline as the company's remaining cash runway. Meanwhile, mobile usage (a key differentiator at launch) has collapsed to 8%. Two strategic bets (mobile, self-serve analytics) are being undermined simultaneously.",
    },
    "O12": {
        "type": "NAND",
        "inputs": ["E1", "E2", "E3", "D4"],
        "label": "Conflicting Stakeholder Vectors",
        "text": "The lead investor (E1) wants an enterprise pivot. Two board members (E2) may push to replace the CEO. StrategicCo's term sheet (E3) is available but conditional. The CEO (D4) lacks enterprise experience. The stakeholder field is pulling in multiple directions — no single path satisfies everyone. The CEO must choose a direction and manage the board, not try to please all factions.",
    },
}


REFERENCE_CONCLUSION = {
    "answer": "PIVOT",
    "rationale": (
        "Pivot to enterprise, but with a structured path: (1) Use $5M extension to hire "
        "2 enterprise AEs + 1 security engineer. (2) Keep mid-market running to cover burn "
        "while enterprise pipeline matures. (3) Retain CTO by framing enterprise work as "
        "platform engineering (SSO, audit logs, APIs). (4) Enterprise ACV of $480K with "
        "6-8 deals/year creates a path to $3M+ ARR within 18 months. (5) Mid-market unit "
        "economics at 2.8:1 LTV:CAC are below sustainable threshold — persevering means "
        "slow decline into cash exhaustion in 18-24 months with no path to $200K MRR."
    ),
    "confidence": 80,
}
