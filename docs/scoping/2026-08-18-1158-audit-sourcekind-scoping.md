---
title: Scoping — #1158 audit.py missing_sourceKind covers legacy points
type: engineering
domain: platform
doc_status: draft
subjects.team: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise/audit.py, tortoise/source_credibility.py, tests/test_audit.py
created: 2026-08-18
---

# Scoping — #1158: audit.py missing_sourceKind covers legacy points

Date: 2026-08-18
Issue: #1158 ("tech(audit): audit.py missing_sourceKind checks legacy point-level
sourceKind — needs Source-level variant")
Complexity: micro

## Problem

`tortoise/audit.py` `audit_graph()` check 1a (`missing_sourceKind`) flags every
evidence point with `ev.sourceKind IS NULL`. Per ontology v3.2 / #398, point-level
`sourceKind` is LEGACY — the canonical field lives on Source nodes and credibility
resolves via `resolve_tier` (explicit `credibilityTier` > sourceKind tier-form >
registry default > None) in `source_credibility.py`. Consequences today:

1. **False positives on correct graphs:** a correctly-wired current-ontology graph
   (evidence `extractedFrom` → tiered Source, no point-level sourceKind) still trips
   check 1 — and the suggested fix (`tortoise_set_source_tier`) never moves the
   metric. `test_real_write_paths_not_flagged` cannot assert this check is clean.
2. **Legacy points are the real target:** pre-#398 points have NO Source provenance
   chain at all (no `extractedFrom` edge) — check 7 (`missing_sourceKind_source`,
   Source-level variant, added with the #348 audit tool) structurally cannot see
   them. Check 1 is the only surface that can cover them, but today it treats every
   modern point identically to a true legacy gap.
3. **Check 7 keys on raw fields, not `resolve_tier` outcome:** a Source with
   `sourceKind:'news'` (unregistered kind → registry default None → neutral) or a
   malformed `credibilityTier` (e.g. 'T9' → resolve_tier → None) is treated as
   tiered, even though its effective tier is neutral. The issue's suggested fix
   offers "and/or key the check on resolve_tier outcome (non-neutral effective
   tier) rather than the raw field".

## Solution (chosen)

- **Check 1 (`missing_sourceKind`, point-level, stays legacy/low):** re-key to a
  left-anti-join — flag ONLY evidence points with `sourceKind IS NULL` AND no
  `extractedFrom` edge to ANY Source (a true legacy provenance gap). Points backed
  by a Source are no longer flagged; Source tiering gaps are owned by check 7
  (single-report division, no double-flagging of the same root cause).
- **Check 7 (`missing_sourceKind_source`, medium):** key on `resolve_tier` outcome
  — flag a Source when its effective tier is neutral: `credibilityTier` NOT a T0-T4
  form AND `sourceKind` NOT a T0-T4 form AND `sourceKind` NOT in the registry's
  tiered kinds (computed dynamically from `SOURCE_KIND_DEFAULTS` so runtime
  `register_source_kind_default` mappings are honored). Explicitly-neutral legacy
  kinds (registry → None) and unregistered kinds are exactly #334's remediation
  population — flagging them is the intended measurement.

Both changes are pure Cypher predicate changes; no schema/write-path impact.

## Deliberately NOT in scope

- Graph-scripts Source-level audit variant for #334 Phase 2 (homed in
  `graph-scripts/` per #334 scoping; this issue is the audit.py product surface).
- Runtime registry persistence (registry policy persistence is #334-owned).

## Acceptance

- New tests: point backed by tiered Source → check 1 clean; legacy point (no
  Source) → check 1 flagged; point with untiered Source → check 7 flagged only;
  Source with neutral-resolving kind → check 7 flagged; explicit tier beats
  neutral kind → check 7 clean. Existing test suite green.
- `python3 tools/ci_selection.py --integrity` passes.
