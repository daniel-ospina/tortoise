---
title: "Core hypothesis — the graph is the memory, not the summaries (epic #909 + product)"
type: strategy
domain: product
doc_status: draft — owner-approved framing, research-backed
created: 2026-08-12
ownedBy: epistemic-team
governingAgreement: "#909"
inputs: owner design review 2026-08-12, extraction criteria v1 §1.5, graph-as-memory research (14 sources), 2026-08-09-state-epistemic-separation.md
---

# Core Hypothesis: the graph is the memory

> **Statement:** the epistemic graph — its content AND its metadata (lifecycle
> events, supersession chains, confidence trajectories, provenance) — is the
> PRIMARY record of context for the agent. The narrative lives in the graph;
> agents are the computational layer that reads and maintains it. Semantic
> summaries are derived projections, never the record.
>
> This is the hypothesis the product exists to test. It is filed as a critical
> consideration across all live epics (2026-08-12).

## 1. The model (what "the graph is the memory" means operationally)

1. **STATE, not decisions (the competitor difference).** Competitors store
   decision objects ("Decision X was made because of Reasons"). We do NOT.
   The record is: **state** (objects/options with their lifecycle events —
   promoted/deprecated/superseded, queryable for context reconstruction — and
   their confidence) + **points** (the logic: claims connected to the state
   objects, the arguments that move confidence) + **events** (what happened,
   for context). The graph says "this state is based on these reasons", never
   "this decision was made because of these reasons".
2. **Lifecycle events are queryable context, not bookkeeping.** Every state
   change on objects/points/edges is queryable: "what was believed at t1",
   "why is X deprecated", "which decision deprecated it". The graph's content
   including metadata IS the narrative — no semantic summary stored anywhere.
3. **Claims are the confidence engine.** Arguments for/against an option
   (IMPL/NAND/MITIGATES on entities and edges) move the option's confidence
   up and down. The graph explains state AND the evolution of state.
4. **A decision resolves the confidence dynamics — recorded as an EVENT.**
   "We decided X on 2026-08-12" lands on the timeline as an Event node
   (eventKind `decision`, aboutObject → the object(s) it resolved); the
   operationalisation (chosen promoted, alternatives deprecated) is expressed
   as lifecycle writes on the objects. Deriving state is harder than querying
   a decision object (you read state lifecycle + state confidence + the
   points) — this is the deliberate tradeoff: the product optimizes for the
   LOGIC BEHIND STATE over the TRACK RECORD of decisions, while keeping the
   decision dimension queryable as timeline events.
5. **The evidence stays authoritative.** Every Point keeps its quoted source
   span; Sources are append-only. The graph is an auditable INDEX over
   unrewritten evidence — extraction is a revisable projection, never a
   rewrite of the record.

## 2. The research basis (2026-08-12, 14 sources)

**FOR (direct evidence):**
- **Temporal KG beats flat/summary memory on exactly our axes.** Zep/Graphiti
  (arXiv:2501.13956): beats MemGPT on DMR (94.8 vs 93.4) and LongMemEval
  (+18.5% accuracy, −90% latency), strongest on multi-session + temporal
  reasoning; its mechanism (bi-temporal facts, invalidation-not-deletion,
  episode provenance) is our supersede/deprecate + Source/Event design.
- **Independent head-to-head** (n26modi multi-agent-memory-eval, identical
  agents): staleness error 87%→20%, historical-belief retrieval 60%→100%
  (Graphiti+Neo4j vs ChromaDB vector summaries) — lifecycle-structured facts
  beat similarity over undated content.
- **Event sourcing precedent** (Fowler; Azure): append-only log as system of
  record, current state = materialized projection; auditability, replay,
  retroactive correction. "The log IS the record" is proven architecture.
- **Derive-cost argument** (Yonk, "The Process Is the Memory"): memory value =
  turns saved; decisions/rationales/dead-ends/corrections have high derive
  cost and don't stale; volatile re-derivable facts are net-negative to store.
- **Relational reasoning** (HippoRAG arXiv:2405.14831, +20% multi-hop QA;
  GraphRAG surveys): graphs win where reasoning is multi-hop/relational.

**AGAINST / tradeoffs (design constraints, not refutations):**
- **Write-before-query omission** (TierMem arXiv:2602.17913): write-time
  extraction causes unverifiable omissions — a discarded detail is
  unrecoverable. Extraction noise poisons the record (our measured 88% noise
  is the canonical failure; the value gate is the answer).
- **Summaries win on simple-factual cost** (TierMem sufficiency router;
  GraphRAG surveys): route cheap factual queries to a summary lane, escalate
  to the graph on miss. Graphs are not uniformly better.
- **The graph is an index, not the record** (Eywa arXiv:2605.30771): the
  immutable evidence store is authoritative; extracted facts are revisable
  projections linked to it (§1.5 above — our design must honor this).
- **Encoding-time commitment** (Rashomon arXiv:2604.03588): write-time
  interpretation commits one framing before the query is known. Decisions are
  commitments (correct for us); claims must stay revisable.

## 3. Design implications (what the graph must be, for this to hold)

| # | Implication | Where it lands |
|---|---|---|
| D1 | **Bi-temporal metadata queryable**: validFrom/validTo + observedAt/recordedAt on points AND edges; point-in-time + is-current queries are the differentiator | ontology fields + query surface (v1.1 ledger/belief-history), supersede chains |
| D2 | **Evidence append-only + quoted spans**: every Point keeps its ≤200-char quoted evidence; Sources are never rewritten — the graph is an auditable index over unrewritten evidence | capture architecture, W-7, Source semantics |
| D3 | **Read path = deterministic queries + agent compute**: zero-LLM retrieval for exact/temporal/lifecycle queries; LLM-guided traversal for relational synthesis (agents = the computational means of keeping memory alive) | search_engine, query surface, agents |
| D4 | **Extraction-nothing is valid; don't store re-derivable facts** | value gate (S1), keep-ratio fail-closed |
| D5 | **Decisions carry rejected options + criteria** — alternatives are the explanation | extraction criteria v1 (chosen/options/criteria), issue #1013 |
| D6 | **Lifecycle events (superseded/deprecated/promoted) queryable for context reconstruction** | supersede_point, status vocab, event log |

## 4. The falsification experiments (what tests the hypothesis)

- **E1 — State-retrieval head-to-head** (mirror n26modi): identical sessions
  → Tortoise graph vs vector-of-summaries; ask "what is the state of X, why
  (the points), and how did it get here (lifecycle + events)?" — plus the
  decision-dimension query via events ("when did we decide about X?"); measure
  precision, point-in-time accuracy, staleness error, latency.
- **E2 — Lifecycle stress**: seed supersede chains + contradictions; compare
  current-status accuracy vs summary baseline (does the graph stay correct
  where summaries go stale?).
- **E3 — Noise + cost ledger**: inject known-bad extractions, measure
  downstream answer corruption; log write-time extraction tokens vs read-time
  synthesis tokens per session (the TierMem sufficiency analysis).

## 5. Status

- Filed as a critical comment on all live epics (2026-08-12).
- README: core-hypothesis section added (2026-08-12, PR pending).
- Epic #909 deltas it implies: bi-temporal fields (D1), evidence append-only
  (D2), decision schema chosen/options/criteria (#1013), lifecycle query
  surface (D6) — the rest land with the roadmap epics (#900-906 series).
