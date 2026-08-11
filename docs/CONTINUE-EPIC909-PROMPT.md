# CONTINUATION PROMPT — Epic #909 (paste to a fresh agent)

> Copy everything below into a new pi session. It tells the agent who it is, where to
> look, what's done, and what to do next.

---

You are continuing **epic #909** — the value-first mining system — in the tortoise repo
(/Users/danielospina/Documents/GitHub/tortoise). The epic is mid-pipeline: Align ✅,
Research ✅, Scope ✅ (gate closed), **Plan is next**. Do NOT re-derive anything — the
decisions are made and documented.

## READ FIRST (in this order — these are the source of truth)

1. `docs/HANDOFF-2026-08-11.md` — the full session state, all decisions, housekeeping gotchas, next steps
2. `docs/epics/2026-08-11-epic909-value-first-mining/DESIGN.md` — the CANONICAL design (the "design to go"): the four-node model, the pipeline S0-S6, the endpoint contract, the evaluation contract, the build order
3. `docs/epics/2026-08-11-epic909-value-first-mining/scope.md` — scoped boundaries, customer value map, 10 high-level E2Es, the decomposition sketch
4. `docs/drafts/2026-08-09-mining-system-requirements.md` — the behavior contract R1-R9 (decisions≠events≠claims, atomicity, provenance chain, pack-typed entities, sources indexed, two-layer testability, MITIGATES relations)
5. `docs/epics/2026-08-11-epic909-value-first-mining/spec-classification-model.md` — the buildable classifier spec (two-axis model, cue tables, the R1∧R3 conjunction, atomicity validator, 10 observed failure modes)

Where the docs live: the `docs/design-drafts-2026-08-09` branch (PR #854). Implementation work branches from **origin/main**, never the local stale checkout.

## WHAT'S DONE (do not revisit)

- **Validation loop converged**: window #1 (design session — 3 iterations, mitigation coverage 0→29→100%, canonical test case green) + window #2 (operational session — generalizes correctly, surfaced 7 eval-harness requirements). Artifacts: `docs/drafts/2026-08-09-probe-extraction-window1{-v2,-v3}.md`, `...-window2.md`, `...-mitigation-audit-window1.md`.
- **All scope decisions resolved**: four-node model (Event→Document←Source←Points; Source is the provenance bridge, NOT content), local-intelligence/remote-graph capture, **BYOK is THE default** (managed-key not in v1), NAND bidirectional default + directed opt-in (extraction-emitted NANDs ALWAYS unidirectional), no capture caps, warrants deferred, no per-turn nodes, enforcement WARN/RETRY/BLOCK.
- **The known P0**: `_count_resource("sessions")` counts ALL nodes (quota.py) — must ship with the session-commit endpoint.
- **Pending ontology amendments**: `agentSession` in §4.5, Document summary/arc fields, `capturedAt`, NAND policy doc.

## WHAT TO DO NEXT

Run the **Plan stage** (epic-plan skill — 8 substeps with full review gates):
1. User Journeys (from the scope's customer value map + 10 E2Es)
2. Workflows → Prototype → Data Model → Architecture → Interfaces → Detailed E2E → Coherence Review

The scope's decomposition sketch (9 slices, gate-first) feeds it:
1. 2-window validation tooling (already validated — formalize) · 2. Quota fix · 3. Ontology amendments · 4. Pack manifest v3 · 5. Session-commit endpoint · 6. Local extractor · 7. SDK producer · 8. Eval harness · 9. Privacy.

Follow the issue-workflow/epic pipeline discipline. Update epic #909 and PR #854 as you go. The build order and endpoint contract are in DESIGN.md §5/§3 — do not redesign.

## GOTCHAS

- The local tortoise checkout is stale (on a stray branch, ~130 commits behind) with another session's uncommitted files. **Use a fresh worktree from origin/main.**
- GitHub API rate limits may block gh — retry after the hour reset; work locally meanwhile.
- Other sessions' work (e.g., the pricing refactor) is stashed — leave it.
- The documents are the contract. If a doc contradicts the code, the doc is the intent; flag the conflict.
