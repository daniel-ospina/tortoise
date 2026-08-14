---
title: "SOC 2 Roadmap Communication — Research Notes (for issue #309 /security page)"
type: synthesis
domain: capability
doc_status: live
created: 2026-08-13
ownedBy: epistemic-team
---

# SOC 2 Roadmap Communication — Research Notes (for issue #309 /security page)

**Findings-date:** 2026-08-13
**Trigger:** demonstrated gap in Phase 1.5 (Micro proportional) — SOC 2 roadmap phrasing is the highest-risk claim on the /security page; claim-accuracy is the issue's binding constraint.
**Sources (independent domains, confidence: Medium — 2 practitioner categories):**
- promise.legal — "SOC 2 Compliance Roadmap for Startups (2025)" (guide: scoping → readiness assessment → evidence collection → remediation; common controls: vulnerability scanning, patch management, penetration testing, dependency scanning)
- soc2auditors.org — "SOC 2 Compliance for Startups: Close Bigger Deals (2026)" (minimum viable SOC 2 scope: access control, change management, vendor management, training, incident response)
- lorikeetsecurity.com — "SOC 2 for Startups: The 6-Month Timeline" (separate "in progress / roadmap" language from "certified / compliant" claims; final SOC 2 report shared with customers/prospects)
- zipsec.com — "SOC 2 for Startups: What to Deploy Before Your First Audit" (core policies enterprise buyers expect referenced: information security, access control, acceptable use, incident response, change management)

## Findings

1. **Separate roadmap language from certification language.** Public pages must distinguish "in progress / roadmap" from "certified / compliant". An uncertified vendor claiming certification is a credibility killer; conversely a roadmap that reads like a certification is an overclaim.
2. **Roadmap credibility without a hard date.** A roadmap with no target date is the weakest credible version; a dated roadmap that slips is worse (silent credibility loss on every missed milestone). Best practice for a pre-audit vendor: milestone-based phrasing (scoping → readiness → audit) with control-area milestones, no fabricated auditor, no fabricated date.
3. **Minimum viable SOC 2 scope for startups:** access control, change management, vendor management, training, incident response (SOC2 Auditors). These map to control areas the roadmap can name without implying certification.
4. **Core policies enterprise buyers expect referenced:** information security, access control, acceptable use, incident response, change management (Zipsec). The page can point at these as roadmap control areas rather than claiming policies exist.

## Application to the /security page (issue #309)

- SOC 2 section copy: **explicitly "not SOC 2 certified"** + roadmap status (readiness milestones, control areas above, no target date, no auditor name).
- Negation-safe spec must use positive overclaim phrases ("we are SOC 2 certified", "SOC 2 Type I/II", "attestation", "audited"). The bare substring "certified" is satisfiable ONLY via **strip-then-scan**: assert "not soc 2 certified" present → strip that exact phrase → assert "certified" absent in the remainder. Do NOT use lookbehind regexes (e.g. `(?<!not )certified`) — they only block direct adjacency and match "soc 2 certified" (verified against the e2e normalization pipeline, 2026-08-13).
- No future-dated claims in the SOC 2 section (audit-date regex scoped to that section only — it should find nothing).

> Note: this note is the committed Raw Notes artifact for the Phase 1.5 research fired on this issue (research-skill ## Raw Notes convention). Referenced from docs/scoping/2026-08-13-309-security-page-scoping.md.
