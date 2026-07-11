"""
E017 Fidelity Convergence — Graph ↔ Documents fixed-point iteration.

Phase 1: Enrich reference graph with document-generated detail
Phase 2: Verify documents contain all graph claims (binary checklist)
Phase 3: Verify graph contains all document claims (reverse audit)
Phase 4: Multi-model reviewer loop until zero discrepancies

Also tracks: which reviewer/model catches which type of gap
→ becomes anecdotally useful extraction strategy reference.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

DOCS_DIR = Path(__file__).parent / "documents"
GRAPH_FILE = Path(__file__).parent / "reference_graph.py"
CHECKLIST_FILE = Path(__file__).parent / "fidelity_checklist.json"
GAP_LOG = Path(__file__).parent / "convergence_log.jsonl"

# ── Phase 1: Enriched graph (seed + detail from document generation) ──
# The enriched graph is the SINGLE SOURCE OF TRUTH.
# Documents must faithfully encode ALL claims below.

ENRICHED_CLAIMS: dict[str, dict] = {
    # ═══ Domain A: Product Analytics (8 base + 4 detail) ═══
    "A1": {
        "domain": "product", "thesis": "PERSEVERE", "tier": "core",
        "label": "MRR Growth",
        "text": "MRR is $142K, growing at 4% month-over-month. At Series A (14 months ago) it was growing at 12% MoM. Growth is decelerating but still positive.",
        "target_docs": ["D1", "D3", "D9"],
    },
    "A2": {
        "domain": "product", "thesis": "PIVOT", "tier": "core",
        "label": "NPS Decline",
        "text": "NPS dropped from 62 to 41 over the past 6 months. Customer satisfaction is deteriorating rapidly. Support ticket volume is up 40% in the same period.",
        "target_docs": ["D1", "D7"],
    },
    "A3": {
        "domain": "product", "thesis": "PIVOT", "tier": "core",
        "label": "AI Feature Failure",
        "text": "The AI-powered insights module, launched 4 months ago at a cost of $400K in engineering time, has only 23% feature adoption. The feature was the flagship initiative of the current product roadmap.",
        "target_docs": ["D1", "D5"],
    },
    "A4": {
        "domain": "product", "thesis": "PIVOT", "tier": "core",
        "label": "Low Engagement",
        "text": "DAU/MAU ratio is 18%. The industry benchmark for analytics tools is 25-35%. Users log in infrequently, suggesting the product is not a daily workflow tool.",
        "target_docs": ["D1"],
    },
    "A5": {
        "domain": "product", "thesis": "PIVOT", "tier": "core",
        "label": "Enterprise Feature Requests",
        "text": "The 3 largest customers, representing 34% of total revenue, have all requested enterprise-grade features: SSO, audit logs, and custom SLAs. These features are not on the current roadmap.",
        "target_docs": ["D4", "D7"],
    },
    "A6": {
        "domain": "product", "thesis": "PIVOT", "tier": "core",
        "label": "Time-to-Value Doubling",
        "text": "Average time-to-value for new users is now 14 days, up from 7 days just 8 months ago. New users take twice as long to reach their first meaningful insight from the product.",
        "target_docs": ["D1", "D7", "D9"],
    },
    "A7": {
        "domain": "product", "thesis": "PIVOT", "tier": "core",
        "label": "Mobile Decline",
        "text": "Mobile app usage is down to 8% of total sessions, from 22% at launch. Churned mobile users cite 'desktop-only workflow' as their primary reason for leaving.",
        "target_docs": ["D1"],
    },
    "A8": {
        "domain": "product", "thesis": "PIVOT", "tier": "core",
        "label": "Uptime Below SLA",
        "text": "Platform uptime is 99.2% over the past quarter, below the 99.9% SLA that 3 enterprise pipeline deals require. Two deals have specifically asked about reliability track record.",
        "target_docs": ["D8"],
    },
    # Detail claims (scaffolding, not scored)
    "A_detail_1": {
        "domain": "product", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Q2 MRR",
        "text": "MRR at the end of Q2 was $128K, reflecting consistent growth trajectory.",
        "target_docs": ["D1"],
    },
    "A_detail_2": {
        "domain": "product", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Power User AI Adoption",
        "text": "Power users (top 20% by session count) adopt the AI module at roughly 45%, while casual users show less than 10% adoption.",
        "target_docs": ["D1"],
    },
    "A_detail_3": {
        "domain": "product", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Time-to-Value Quarterly Breakdown",
        "text": "Time-to-value by quarter: Q4 2025=7 days, Q1 2026=9 days, Q2 2026=11 days, Q3 2026=14 days. Accounts with longer TTV are 3x more likely to churn within the first year.",
        "target_docs": ["D7"],
    },
    "A_detail_4": {
        "domain": "product", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Support Ticket Breakdown",
        "text": "Support ticket breakdown: 45% product usage questions, 25% bug reports, 20% feature requests (including enterprise features), 10% billing/account. CS team at 3:180 ratio.",
        "target_docs": ["D7"],
    },

    # ═══ Domain B: Market & Competition (8 base + 4 detail) ═══
    "B1": {
        "domain": "market", "thesis": "PIVOT", "tier": "core",
        "label": "Enterprise Market Growth",
        "text": "The enterprise analytics market is growing at 28% year-over-year, currently valued at $14B. Mid-market analytics is growing at 9% YoY, valued at $3.2B.",
        "target_docs": ["D2"],
    },
    "B2": {
        "domain": "market", "thesis": "PERSEVERE", "tier": "core",
        "label": "Mid-Market Still Growing",
        "text": "At 9% YoY growth, the mid-market analytics space is not dying — it's maturing. $3.2B is a meaningful TAM for a company at Nexus's scale. Many successful companies have built $100M+ businesses in maturing mid-markets.",
        "target_docs": ["D2"],
    },
    "B3": {
        "domain": "market", "thesis": "PIVOT", "tier": "core",
        "label": "New Competitors Below",
        "text": "Three new competitors have launched in the mid-market analytics space in the last 6 months. Two of them offer free tiers, directly attacking Nexus's self-serve entry point.",
        "target_docs": ["D2"],
    },
    "B4": {
        "domain": "market", "thesis": "PIVOT", "tier": "core",
        "label": "Well-Funded Rival",
        "text": "A competitor raised a $20M Series B and is targeting the same ICP as Nexus. Their marketing spend is estimated at $150K/month — 3x Nexus's total marketing budget.",
        "target_docs": ["D2"],
    },
    "B5": {
        "domain": "market", "thesis": "PIVOT", "tier": "core",
        "label": "Tableau SMB Threat",
        "text": "A major incumbent recently announced SMB-friendly pricing at $15/user/month, undercutting Nexus's $25/user/month starter plan. The incumbent's brand recognition and feature set far exceed Nexus's.",
        "target_docs": ["D2"],
    },
    "B6": {
        "domain": "market", "thesis": "NEUTRAL", "tier": "core",
        "label": "Gartner Exclusion",
        "text": "Nexus is not listed in the Gartner Magic Quadrant for Analytics. Inclusion requires a minimum of $5M in annual revenue, which Nexus does not yet meet.",
        "target_docs": ["D2"],
    },
    "B7": {
        "domain": "market", "thesis": "PIVOT", "tier": "core",
        "label": "Enterprise RFP Losses",
        "text": "Nexus lost 2 enterprise RFPs in Q3. Both prospects cited 'lack of enterprise readiness' as the reason — specifically, missing SSO, no audit log capabilities, and no dedicated account management.",
        "target_docs": ["D2", "D4"],
    },
    "B8": {
        "domain": "market", "thesis": "PIVOT", "tier": "core",
        "label": "AI Commoditization Risk",
        "text": "A major AI company recently announced an analytics API that could commoditize mid-market dashboard products within 12-18 months. The API generates insights from raw data without requiring a dedicated analytics platform.",
        "target_docs": ["D2"],
    },
    # Detail claims
    "B_detail_1": {
        "domain": "market", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Competitor Names",
        "text": "Three new entrants are named: one lightweight analytics tool with a free tier, one focused on e-commerce analytics with pre-built integrations, and one open-core analytics platform with a self-hosted free option and cloud-hosted paid tier.",
        "target_docs": ["D2"],
    },
    "B_detail_2": {
        "domain": "market", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Enterprise Market Definition",
        "text": "Enterprise segment defined as platforms serving organizations with 500+ employees and contracts above $50K annual value. Mid-market defined as organizations with 50-500 employees, contracts $10K-$50K annually.",
        "target_docs": ["D2"],
    },
    "B_detail_3": {
        "domain": "market", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Enterprise RFP Lost Details",
        "text": "The specific capabilities cited as missing in lost RFPs: single sign-on (SAML/OIDC) integration, audit log capabilities for compliance, and dedicated account management with defined SLAs.",
        "target_docs": ["D2", "D4"],
    },
    "B_detail_4": {
        "domain": "market", "thesis": "NEUTRAL", "tier": "detail",
        "label": "AI API Limitations",
        "text": "The AI analytics API is still in beta and has significant limitations: it cannot handle complex multi-source data modeling or governed metrics. But the trajectory suggests commoditization of basic-to-intermediate dashboard functionality within 12-18 months.",
        "target_docs": ["D2"],
    },

    # ═══ Domain C: Financials (6 base + 4 detail) ═══
    "C1": {
        "domain": "financial", "thesis": "PERSEVERE", "tier": "core",
        "label": "Healthy Runway",
        "text": "Nexus has $4.2M in cash remaining. With a current burn rate of $300K/month, this provides approximately 14 months of runway at current spending levels.",
        "target_docs": ["D3", "D9", "D10"],
    },
    "C2": {
        "domain": "financial", "thesis": "PIVOT", "tier": "core",
        "label": "Burn Rate Increase",
        "text": "Monthly burn is now $300K, up from $220K at Series A. Headcount has grown 40% to 32 people. The burn increase has not been matched by proportional revenue growth.",
        "target_docs": ["D3"],
    },
    "C3": {
        "domain": "financial", "thesis": "PIVOT", "tier": "core",
        "label": "CAC Explosion",
        "text": "Customer acquisition cost has risen to $8,400 per customer, up from $3,200 at the time of Series A. Paid marketing channels are showing clear signs of saturation with diminishing returns.",
        "target_docs": ["D3", "D9"],
    },
    "C4": {
        "domain": "financial", "thesis": "PIVOT", "tier": "core",
        "label": "Unit Economics Below Benchmark",
        "text": "The LTV:CAC ratio is currently 2.8:1, down from 5.1:1 at Series A. The industry benchmark for healthy SaaS unit economics is 3:1 or above. Nexus is now below the sustainability threshold.",
        "target_docs": ["D3", "D9"],
    },
    "C5": {
        "domain": "financial", "thesis": "PIVOT", "tier": "core",
        "label": "Enterprise Pipeline Value",
        "text": "The enterprise sales pipeline currently has 8 active deals with an average ACV of $480K. The average enterprise sales cycle is 3.8 months. Total pipeline value is $3.84M in potential ARR.",
        "target_docs": ["D4", "D8"],
    },
    "C6": {
        "domain": "financial", "thesis": "PERSEVERE", "tier": "core",
        "label": "Mid-Market Pipeline Velocity",
        "text": "The mid-market pipeline has 34 active deals with an average ACV of $18K and an average sales cycle of just 3 weeks. Mid-market deals close 5x faster than enterprise deals.",
        "target_docs": ["D4"],
    },
    # Detail claims
    "C_detail_1": {
        "domain": "financial", "thesis": "NEUTRAL", "tier": "detail",
        "label": "LTV Dollar Value",
        "text": "Lifetime Value is $23,500 per customer, up from $16,300 at Series A. LTV growth (44%) has not kept pace with CAC growth (163%).",
        "target_docs": ["D3", "D9"],
    },
    "C_detail_2": {
        "domain": "financial", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Team Headcount Breakdown",
        "text": "Team breakdown: Engineering 14, Sales 4 AEs + 2 SDRs, Customer Success 3, Marketing 3, G&A (including executive) 6. Total: 32. No enterprise AEs, security engineers, or compliance specialists on staff.",
        "target_docs": ["D3", "D4"],
    },
    "C_detail_3": {
        "domain": "financial", "thesis": "NEUTRAL", "tier": "detail",
        "label": "MRR Projections",
        "text": "At 4% MoM growth, MRR reaches $180K in 6 months, $225K in 12 months, $340K in 24 months. Operating costs are $275K-$300K/month. Cash depletion rate is approximately $160K/month. Breakeven at ~$300K MRR would occur around month 20-22 if nothing changes.",
        "target_docs": ["D3", "D9"],
    },
    "C_detail_4": {
        "domain": "financial", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Sales Pipeline Granularity",
        "text": "Mid-market pipeline: 34 active deals, $18K ACV, 3 weeks sales cycle, 22% demo-to-close conversion, total pipeline value ~$612K. Enterprise: 8 deals, $480K ACV, 3.8 months cycle. Quota attainment declined from 71% in Q2 to 62% in Q3. Average deal size $18,400 in Q3.",
        "target_docs": ["D4"],
    },

    # ═══ Domain D: Team & Organization (5 base + 2 detail) ═══
    "D1": {
        "domain": "team", "thesis": "PIVOT", "tier": "core",
        "label": "CTO Departure Risk",
        "text": "The CTO is considering leaving. They are frustrated with the product direction and want to build a platform with APIs and infrastructure, not incremental user-facing features. They have not formally resigned but have expressed this to the CEO confidentially.",
        "target_docs": ["D5", "D10"],
    },
    "D2": {
        "domain": "team", "thesis": "PIVOT", "tier": "core",
        "label": "No Enterprise Sales Capacity",
        "text": "The sales team consists of 4 mid-market account executives. None have enterprise sales experience. Quota attainment across the team is 62%. There are zero enterprise AEs on staff.",
        "target_docs": ["D4", "D8"],
    },
    "D3": {
        "domain": "team", "thesis": "PIVOT", "tier": "core",
        "label": "No Security Engineering",
        "text": "The 14-person engineering team has no one with enterprise security or compliance experience. Building SSO, audit logs, and SOC 2 compliance would require new hires or extensive retraining.",
        "target_docs": ["D5", "D8"],
    },
    "D4": {
        "domain": "team", "thesis": "PIVOT", "tier": "core",
        "label": "CEO PLG Background",
        "text": "The CEO's background is in product-led growth and self-serve SaaS. They have never led an enterprise sales organization. Two board members have privately noted this gap as a concern for an enterprise pivot.",
        "target_docs": ["D10"],
    },
    "D5": {
        "domain": "team", "thesis": "PIVOT", "tier": "core",
        "label": "Customer Success Overloaded",
        "text": "The customer success team has 3 people managing 180 accounts — a 60:1 ratio. Industry best practice is 40:1 or lower. Response times have slipped to an average of 18 hours, up from 6 hours a year ago.",
        "target_docs": ["D7"],
    },
    # Detail claims
    "D_detail_1": {
        "domain": "team", "thesis": "NEUTRAL", "tier": "detail",
        "label": "CTO Technical Grievances",
        "text": "The CTO's specific frustrations: undocumented APIs, no proper multi-tenancy, data pipeline breaks at volume, roadmap prioritizes visible features over invisible reliability. The CTO joined as first engineer three years ago with a vision of building an analytics infrastructure layer.",
        "target_docs": ["D5"],
    },
    "D_detail_2": {
        "domain": "team", "thesis": "NEUTRAL", "tier": "detail",
        "label": "CS Churn Risk Details",
        "text": "12 accounts flagged as high churn risk ($38K MRR, 27% of total revenue). Three are among the largest customers. Two CSMs have flagged burnout. QBRs only for top-tier accounts; mid-tier gets automated check-ins.",
        "target_docs": ["D7"],
    },

    # ═══ Domain E: Investors & Board (4 base + 2 detail) ═══
    "E1": {
        "domain": "investors", "thesis": "PIVOT", "tier": "core",
        "label": "Lead Investor Pushing Pivot",
        "text": "The lead Series A investor is strongly advocating for an enterprise pivot. Their partner told the CEO: 'Mid-market analytics is a feature, not a company. You need to go upmarket or you'll get eaten.'",
        "target_docs": ["D6"],
    },
    "E2": {
        "domain": "investors", "thesis": "PIVOT", "tier": "core",
        "label": "Board CEO Confidence",
        "text": "Two of the five board members have indicated that if MRR does not reach $200K within 6 months, they will push for a CEO change. Current MRR is $142K, which at 4% MoM growth would reach approximately $180K in 6 months.",
        "target_docs": ["D6"],
    },
    "E3": {
        "domain": "investors", "thesis": "PIVOT", "tier": "core",
        "label": "Conditional Term Sheet",
        "text": "A growth-stage fund has offered a $5M Series A extension at flat valuation, contingent on two conditions: the company must pivot to enterprise, and the CTO must commit to staying for at least 18 months.",
        "target_docs": ["D6", "D10"],
    },
    "E4": {
        "domain": "investors", "thesis": "PERSEVERE", "tier": "core",
        "label": "Bridge Round Available",
        "text": "A secondary investor has indicated willingness to provide a $2M bridge round regardless of strategic direction. This would extend runway without requiring a pivot commitment.",
        "target_docs": ["D6", "D10"],
    },
    # Detail claims
    "E_detail_1": {
        "domain": "investors", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Investor Incentive Analysis",
        "text": "The lead investor's incentive is growth at any cost — they need a big outcome to justify fund returns. An enterprise pivot is the higher-variance bet. The CEO recognizes this incentive structure may not align with company's best interest.",
        "target_docs": ["D6", "D10"],
    },
    "E_detail_2": {
        "domain": "investors", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Bridge Round Signal",
        "text": "Taking a bridge round signals weakness to future investors — it signals the company wasn't ready for a proper round. In the current fundraising environment, a bridge can make the next priced round harder.",
        "target_docs": ["D6"],
    },

    # ═══ Domain F: Narrative & Context (5 detail) ═══
    "F_detail_1": {
        "domain": "narrative", "thesis": "PIVOT", "tier": "detail",
        "label": "CEO Leaning",
        "text": "As of October 9, six days before the board meeting, the CEO is personally leaning toward the enterprise pivot \u2014 not because investors want it but because the numbers support it.",
        "target_docs": ["D10"],
    },
    "F_detail_2": {
        "domain": "narrative", "thesis": "NEUTRAL", "tier": "detail",
        "label": "NPS Seasonality",
        "text": "Q3 is historically Nexus\u2019s lowest-scoring quarter for NPS due to summer seasonality in user engagement patterns, which partially explains but does not fully account for the 62-to-41 decline.",
        "target_docs": ["D1"],
    },
    "F_detail_3": {
        "domain": "narrative", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Board Meeting Date",
        "text": "The next board meeting is scheduled for October 15, 2026. The CEO is preparing her position before facing the board.",
        "target_docs": ["D6"],
    },
    "F_detail_4": {
        "domain": "narrative", "thesis": "NEUTRAL", "tier": "detail",
        "label": "Term Sheet Not Disclosed",
        "text": "The CEO has not shared the StrategicCo term sheet with the full board yet. She wants to clarify her own thinking before presenting it.",
        "target_docs": ["D6"],
    },
    "F_detail_5": {
        "domain": "narrative", "thesis": "NEUTRAL", "tier": "detail",
        "label": "VentureCo Unscheduled Call",
        "text": "VentureCo\u2019s partner contacted the CEO directly via an unscheduled call to push for the enterprise pivot, citing competitive dynamics as evidence the mid-market window is closing.",
        "target_docs": ["D6"],
    },

    # ═══ Domain G: Methodology (4 detail) ═══
    "G_detail_1": {
        "domain": "methodology", "thesis": "NEUTRAL", "tier": "detail",
        "label": "NPS Survey Response Rate",
        "text": "NPS is measured via an in-app popup survey with a response rate of approximately 12% of active users.",
        "target_docs": ["D7"],
    },
    "G_detail_2": {
        "domain": "methodology", "thesis": "NEUTRAL", "tier": "detail",
        "label": "NPS Detractor Verbatim Counts",
        "text": "NPS detractor verbatims cluster around: product has become too complex (18 respondents), hard to get started / steep learning curve (14), support takes too long to respond (11), mobile app is basically useless now (9), features I don\u2019t need keep getting added but features I need don\u2019t exist (7).",
        "target_docs": ["D7"],
    },
    "G_detail_3": {
        "domain": "methodology", "thesis": "NEUTRAL", "tier": "detail",
        "label": "NPS Promoter Verbatim Counts",
        "text": "NPS promoter verbatims cluster around: core analytics are solid and reliable (22 respondents), great value for the price (15), CSM has been incredibly helpful (12).",
        "target_docs": ["D7"],
    },
    "G_detail_4": {
        "domain": "methodology", "thesis": "NEUTRAL", "tier": "detail",
        "label": "LTV Calculation Methodology",
        "text": "LTV is calculated using a 36-month customer lifespan assumption with a 12% annual discount rate. CAC includes fully-loaded marketing and sales costs.",
        "target_docs": ["D9"],
    },

    # ═══ Domain H: Projections & Derivations (6 detail) ═══
    "H_detail_1": {
        "domain": "projections", "thesis": "PIVOT", "tier": "detail",
        "label": "Post-Hire Burn Rate",
        "text": "After hiring 2 enterprise AEs and 1 security engineer, the monthly burn rate would increase from $300K to approximately $358K.",
        "target_docs": ["D10"],
    },
    "H_detail_2": {
        "domain": "projections", "thesis": "PIVOT", "tier": "detail",
        "label": "First Enterprise Deal Timeline",
        "text": "The first enterprise deal may not close until month 5-6 after the pivot begins, even though the average enterprise sales cycle is 3.8 months.",
        "target_docs": ["D10"],
    },
    "H_detail_3": {
        "domain": "projections", "thesis": "PIVOT", "tier": "detail",
        "label": "Enterprise Hire Annual Cost",
        "text": "The enterprise hires (2 AEs + 1 security engineer) would add approximately $700,000 per year in burn.",
        "target_docs": ["D10"],
    },
    "H_detail_4": {
        "domain": "projections", "thesis": "PIVOT", "tier": "detail",
        "label": "Combined Runway with Extension",
        "text": "With the $5M StrategicCo extension, total cash would be $4.2M + $5M = $9.2M. At the post-hire burn rate of approximately $358K/month, this provides approximately 25 months of runway.",
        "target_docs": ["D10"],
    },
    "H_detail_5": {
        "domain": "projections", "thesis": "PIVOT", "tier": "detail",
        "label": "Cash-Exhaustion Breakeven Gap",
        "text": "At current burn and growth rates, cash of $4.2M would be exhausted around month 14, while breakeven at approximately $300K MRR would occur around month 20-22. There is a 6-8 month gap between cash exhaustion and breakeven.",
        "target_docs": ["D9"],
    },
    "H_detail_6": {
        "domain": "projections", "thesis": "PIVOT", "tier": "detail",
        "label": "Enterprise Pivot Hiring Plan",
        "text": "The enterprise pivot plan requires hiring 2 enterprise account executives and 1 security engineer immediately.",
        "target_docs": ["D10"],
    },

    # ═══ Domain I: Causal Claims (1 detail) ═══
    "I_detail_1": {
        "domain": "causal", "thesis": "PIVOT", "tier": "detail",
        "label": "AI Module Causing Ticket Surge",
        "text": "The support team has attributed roughly half of the 40% quarter-over-quarter ticket volume increase to the rollout of the new AI insights module.",
        "target_docs": ["D1"],
    },
}

# Operators unchanged from original — they're relationships, not claims
OPERATORS = {
    "O1": {"type": "NAND", "inputs": ["A1", "A2"], "label": "Growth vs. Satisfaction", "tier": "core"},
    "O2": {"type": "NAND", "inputs": ["A3", "A4"], "label": "AI Investment vs. Engagement", "tier": "core"},
    "O3": {"type": "COMPARE", "inputs": ["B1", "B2"], "label": "Enterprise vs. Mid-Market Growth", "tier": "core"},
    "O4": {"type": "COMPARE", "inputs": ["C5", "C6"], "label": "Enterprise vs. Mid-Market ACV", "tier": "core"},
    "O5": {"type": "NAND", "inputs": ["A5", "C5", "D2"], "label": "Enterprise Demand vs. Sales Capacity", "tier": "core"},
    "O6": {"type": "NAND", "inputs": ["C1", "C2", "D4"], "label": "Runway vs. CEO Experience", "tier": "core"},
    "O7": {"type": "NAND", "inputs": ["B3", "B4", "B5"], "label": "Three-Front Competitive Squeeze", "tier": "core"},
    "O8": {"type": "NAND", "inputs": ["D1", "E3"], "label": "CTO Wants Platform, Term Sheet Requires Them", "tier": "core"},
    "O9": {"type": "CORRELATE", "inputs": ["A6", "C4"], "label": "Time-to-Value × Unit Economics", "tier": "core"},
    "O10": {"type": "NAND", "inputs": ["C1", "C3", "C5"], "label": "Cash vs. Pivot Economics", "tier": "core"},
    "O11": {"type": "NAND", "inputs": ["B8", "A7"], "label": "AI Threat × Mobile Abandonment", "tier": "core"},
    "O12": {"type": "NAND", "inputs": ["E1", "E2", "E3", "D4"], "label": "Conflicting Stakeholder Vectors", "tier": "core"},
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

# ── Phase 2: Fidelity Checklist Generation ──

def generate_checklist():
    """Build a binary checklist: every claim-document pair."""
    docs = sorted([f.stem for f in DOCS_DIR.glob("D*.md")])
    # Map short codes to full stems: "D1" → "D1-product-analytics-review"
    short_to_full = {}
    for stem in docs:
        short = stem.split("-")[0]
        short_to_full[short] = stem
    checklist = {"generated": datetime.now().isoformat(), "checks": []}
    for cid, claim in ENRICHED_CLAIMS.items():
        for short_code in short_to_full:
            doc = short_to_full[short_code]
            present = short_code in claim["target_docs"]
            checklist["checks"].append({
                "claim_id": cid,
                "doc": doc,
                "expected": present,
                "verified": None,  # filled by reviewer
                "reviewer": None,
                "confidence": None,
                "notes": None,
            })
    return checklist


def log_gap(direction: str, claim_id: str, doc: str, description: str, reviewer: str, model: str):
    """direction: 'graph→doc' (claim in graph, missing from doc) or 'doc→graph' (claim in doc, missing from graph)"""
    with open(GAP_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "direction": direction,
            "claim_id": claim_id,
            "doc": doc,
            "description": description,
            "reviewer": reviewer,
            "model": model,
        }) + "\n")


def convergence_status():
    """Return current convergence state."""
    if not CHECKLIST_FILE.exists():
        return {"status": "no_checklist", "message": "Run generate_checklist() first"}
    with open(CHECKLIST_FILE) as f:
        checklist = json.load(f)
    verified = [c for c in checklist["checks"] if c["verified"] is not None]
    passed = [c for c in verified if c["verified"] == c["expected"]]
    failed = [c for c in verified if c["verified"] != c["expected"]]
    unverified = [c for c in checklist["checks"] if c["verified"] is None]
    return {
        "status": "converged" if not failed and not unverified else ("partial" if verified else "not_started"),
        "total": len(checklist["checks"]),
        "verified": len(verified),
        "passed": len(passed),
        "failed": len(failed),
        "unverified": len(unverified),
        "failures": [{"claim_id": f["claim_id"], "doc": f["doc"], "expected": f["expected"]} for f in failed],
    }


if __name__ == "__main__":
    cl = generate_checklist()
    CHECKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKLIST_FILE, "w") as f:
        json.dump(cl, f, indent=2)
    print(f"Checklist written: {CHECKLIST_FILE}")
    print(f"  {len(cl['checks'])} claim-document pairs")
    print(f"  {len(ENRICHED_CLAIMS)} claims ({sum(1 for c in ENRICHED_CLAIMS.values() if c['tier']=='core')} core, {sum(1 for c in ENRICHED_CLAIMS.values() if c['tier']=='detail')} detail)")
    print(f"  {len(list(DOCS_DIR.glob('D*.md')))} documents")
