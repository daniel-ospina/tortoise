# Solution-Converge — Agent A (PRCA) — #2165 Connected Assembly

**Date:** 2026-09-08 · Independent converge (Agent B's convergent decision is in `2026-09-08-2165-connected-assembly-solution-converge.md`; Agent A's decision recorded here for auditability — see solution-spec-v2 C2).

## Chosen approach: PRCA — Pre-Routed Connected Assembly
Private (pre-retrieval) shape-router with a pool-REPLACING branch whose assembled block is emitted as first-class decorated evidence lines flowing through the existing `assemble_context`/`render_context` budget machinery, over a single pure `tortoise/assembly.py` core (injected projection) that both `ask()` and the eval/unit seam call.

Composition (each piece earned on evidence):
- **B1 posture** (router surface + block entry): internal classifier (ordering/interval/date-lookup/current-state) runs pre-retrieval; fired branch replaces the point pool. `TORTOISE_ASK_CONNECTED_ASSEMBLY` OFF (default) → branch never starts → legacy byte-identical BY CONSTRUCTION.
- **A2 line-form** (block implementation): block = decorated evidence-line dicts with honest `session_date`/`speaker`/`superseded_by`/`valid_*` keys → inherits 8k/32-KiB whole-hit caps, W4 guard (synthesized lines lack `point_id` → skipped), byte accounting, `_render_block`/`_validity_marker` markers, `context_tokens == estimate_tokens_ask(...)` alignment invariant. State header + ordering/diff = ONE compact synthesized hit (explicit ISO + markers). Replace posture defuses A2's dedup/synthetic-session-key subtlety (no legacy pool to dedup against).
- **A3 module shape** (module + entrypoint): pure module `assemble(port, question, question_date) -> AssemblyResult` (subjects, fired, slices, post-cap lines, admission counts) — single source for ask()'s branch AND eval consumption (drift impossible: same object). Agent A deferred a NEW public sdk method in v1 (#2013-gated surface; module seam suffices for eval) — RECONCILED in solution-spec-v2 R-seam: public `sdk.ask_assembled()` adopted (recall_* precedent), single-source `_assemble_connected()` shared with ask()'s branch.

**Rejected (when each wins):** A1 qtype-native — wins post-#2013 un-gate when contract-visible per-shape question_type is wanted (promote internal shape set into the enum then). A2 additive-alone — wins if pool-interleave is acceptable (its budget-reuse kept; weaker both-halves not). B2 fold-time snapshot — wins when hard deterministic p95 + free as-of restore dominate AND fold machinery lands stable (revisit with #2164/#2349 record axis); biggest departure from the read-path framing + drift invariants across three lanes + the v2-eval dated spine (Events w/o aboutObject that never fold) breaks its core premise today. B3 AEP-append — wins when blast radius dominates AND the RRF pool already recalls both subjects; its pool-presence gate is pool-recall-limited (disqualified by the issue's own diagnosis: gold at ranks 48-68 outside any capped pool).

## Constraint compliance + plan draft
See the converge decision body (full constraint 1-9 table + phased TDD plan) in this session's record; solution-spec-v2 (R1-R12) supersedes ambiguities with verify-gate findings incorporated.
