---
title: "Value-First Extraction Pipeline — Architecture Spec"
type: design
domain: engineering
doc_status: draft
created: 2026-08-09
ownedBy: epistemic-team
aboutObjects: tortoise, extractor, ontology, expansion-packs
---

# Value-First Extraction Pipeline — Architecture Spec

> **UPDATE (2026-08-09, product owner): extraction runs LOCALLY on the user's
> machine with their key/model; the hosted product receives derived content only
> (see docs/drafts/2026-08-09-capture-architecture-local-intelligence.md).
> S4 warrant generation is DEFERRED from the default path (research-grade,
> invented reasoning) — at most a user-opted toggle, always tagged low-confidence.**

The intelligence flow that turns a conversation into few, high-value,
provenance-backed epistemic/state deltas. Replaces the regex amplifier
(`hosted_api.py:1451-1660`) as the `value` extraction mode.

## The problem being fixed

Regex extraction measures 88% noise at ~160 nodes/turn (`hosted_api.py` #329
flood-gate comments; 30 dense turns → 4,832 nodes). Median session ≈ 98 turns
→ ~16k nodes *per session* vs the solo cap of 25k. The node amplifier — not
LLM cost — is the cost problem. LLM cost at DeepSeek V4 Flash
($0.14/M in, $0.28/M out) is already ~$0.01/session; even a 3-5x richer
pipeline is negligible. Value-first fixes the amplifier *by construction*:
extraction is gated by the ontology's definition of value, and "extract
nothing" is a first-class success.

## Design principles

1. **Ontology = value.** The pack manifest (objectKinds, pointKinds,
   relations, mechanisms) is compiled into a *value brief* that the LLM
   gates against. No ontology entry → no extraction.
2. **Gate before generate.** Value assessment runs on raw segments *before*
   any content generation. You can't generate noise you decided not to write.
3. **Empty is success.** The gate's `keep: []` is a normal return. No
   retry, no quota, no minimum-count pressure anywhere in the prompt.
4. **No LLM-minted identifiers.** The LLM emits entity *names* and claim
   *content*; a deterministic resolver assigns IDs (SPIRES: LLM ID
   emission causes severe hallucination). Grounding/resolution is a
   separate pass (Zep/Graphiti, iText2KG, DIAL-KG).
5. **Provenance to the span.** Every Point carries `extractedFrom → Source`
   - char-offset span + verbatim quote + speaker, via the existing
   `provenance()` helper (`extractor.py`) and the episodic `sessionCaptured`
   Event (`hosted_api.py:1636-1660`).
6. **Cheap-first, frontier-last.** Segmentation/gating/extraction/relations
   run on a cheap model; a frontier model is reserved for ≤3 warrants per
   session (the only genuinely reasoning stage).
7. **Deterministic fallback always works.** Regex mode stays as the
   always-works baseline (#312 semantics). Value mode fails closed to
   empty, never to regex.

## Pipeline overview

```text
conversation (100-800k tok)
   │ S0 deterministic preprocessing (0 LLM)
   ▼
turns → utterances (provenance spans, cheap boilerplate filter)
   │ S1 value gate per 16k-token window (CHEAP) ← graph-state conditioning
   ▼
kept segment indices (+kind, +why)     // keep: [] is success
   │ S2 claim+entity extraction on kept segments only (CHEAP)
   ▼
candidate points (content, pointKind-from-vocab, entity NAMES, confidence, span)
   │ S3 windowed relation extraction (CHEAP)
   ▼
IMPL/NAND/REPHRASE candidates (within window, quote-backed)
   │ S4 warrant generation on contested/novel claims only (FRONTIER, ≤3)
   ▼
derived warrant points (derived:true, confidence 0.4-0.6)
   │ S5 resolution/grounding (DETERMINISTIC + batched cheap verdicts)
   ▼
grounded deltas (real IDs, dedup outcomes: merge|rephrase|new|supersede)
   │ S6 delta write (existing SDK: create_point/add_operator/
   │                add_subject/add_object/supersede_point, dedup=True)
   ▼
few high-value Points + entities + operators, provenance-backed
```

## Stage-by-stage spec

### S0 — Deterministic preprocessing (0 LLM, ~0 ms)

- Reuse `SessionRequest.conversation` turns; split each turn into
  sentence-level utterances with char-offset spans (the `_utterances`
  pattern in `extractor.py`).
- Cheap boilerplate filter (no LLM): drop role=user acks ("ok", "sounds
  good", < 6 tokens), tool output blocks, repeated/near-identical turns
  (content-hash), code dumps over a token ceiling. Deterministic rules,
  ~50-70% volume reduction on real sessions.
- **Output:** `N` utterances `{role, text, span, turn_idx}` + session Event
  node (existing `sessionCaptured`).
- **Cost:** 0.

### S1 — Value gate (CHEAP model, per 16k-token window)

The novel stage. A cheap model (DeepSeek V4 Flash tier) receives: the value
brief (compiled from pack manifests), a ≤4k-token graph-state summary
(§Graph-state conditioning), and the conversation window. It returns *only*
which segments qualify and why — **no content generation**. This is a
classification/ranking task, which cheap models do well, and it makes
extract-nothing a natural output.

- **Output:** `{"keep": [{"start": span, "end": span, "kind": <pointKind>,
  "why": "..."}], "reason": "..."}` or `{"keep": []}`.
- **Cost per window:** ~16k in + 0.2k out ≈ $0.0025. ×5 windows ≈ $0.012.
- **Quality guards:** keep-ratio must stay 5-25%. If the model returns
  "keep everything" (>40% of segments) for ≥3 consecutive windows, treat as
  classifier degradation → fail closed to extract-nothing for the session +
  alert (same fail-closed spirit as quota counting, `quota.py`).

### S2 — Claim + entity extraction (CHEAP, kept segments only)

Runs only on segments the gate kept (≈3-8 per session). Essentially the
existing `_POINTS_DOC_SYS` (`extractor.py:520-548`) with two upgrades:
(a) pointKind chosen from the pack vocabulary (via `domain_loader`), not
open; (b) entities returned as **names only**, kinds chosen from
subjectKind/objectKind vocab. Confidence per the rubric (§Value brief).

- **Output:** `[{"content": ..., "pointKind": <vocab>, "aboutEntities":
  [names], "confidence": 0-1, "span": ...}]` — `[]` allowed per segment.
- **Cost:** ~3k in + 0.3k out ≈ $0.0005/segment. ×8 ≈ $0.004.

### S3 — Windowed relation extraction (CHEAP)

Over the kept claims of one window (≤20 claims): IMPL (supports), NAND
(attacks), REPHRASE (restates an existing graph claim — feeds dedup, never
a graph edge). Same discipline as `_RELATIONS_DOC_SYS` — only *logically
necessary* relations, quote-backed, no topical-similarity or co-occurrence
edges. Cross-window relations are NOT searched here; they surface at query
time via shared-entity neighborhoods (`find_cross_lens_matches` pattern in
`extractor.py:240`), which is cheaper and less hallucination-prone.

- **Output:** `[{"op_type": "IMPL|NAND|REPHRASE", "src": idx, "dst": idx,
  "quote": "..."}]`.
- **Cost:** ~3k in + 0.3k out ≈ $0.0025/window. ×5 ≈ $0.0025.

### S4 — Warrant / implicit-premise generation (FRONTIER, ≤3 per session)

The only frontier-model stage, and the epistemic value-add. Runs only on
claims that are (a) contested (a NAND partner exists in-window), (b) novel
high-confidence decisions, or (c) explicitly flagged by the gate. Generates
the warrant: *why this claim is justified / what implicit premise it
relies on*. Tagged `derived: true`, `derivedFrom: [claim ids]`, default
confidence 0.4-0.6 (never above). These are argument-structure nodes —
excluded from fact retrieval by default, included in reasoning queries.
Argument mining (claim graphs + support/attack) is production-proven; this
stage adds the *implicit premise* level research rates as hard (67 F1 for
full trees from dialogue) — hence the low confidence + review calibration.

- **Output:** ≤3 `Point`s with `epistemicRole: warrant`, `derived: true`.
- **Cost:** ~4k in + 0.5k out at ~$3/M in, ~$15/M out ≈ $0.02 max.

### S5 — Resolution / grounding (DETERMINISTIC + batched cheap verdicts)

Two parallel resolvers. **Never accept an LLM-minted ID** — the LLM output
contains names/content only.

**5a. Entity grounding** (names → Subject/Object ids):

1. Exact name match (normalized: lowercase, strip punctuation);
2. Alias match (canonical name index + aliases harvested from `references`
   and `about*` edge neighborhoods);
3. Embedding match (existing sentence-transformers stack, `embeddings.py`),
   cosine ≥ 0.85;
4. **NEW** only if the kind is in the pack/core vocab **and** the name
   appears in ≥2 distinct segments (frequency gate — kills one-off proper
   nouns). Otherwise drop the mention or attach as a `Tag`.
Kind is validated against `known_kinds()`; out-of-vocab kinds are rejected.

**5b. Claim resolution** (candidate claims → existing Points):

1. content-hash dedup (existing `create_point(dedup=True)`);
2. embedding similarity vs. the neighborhood claims of the grounded
   entities: ≥ 0.92 → REPHRASE (attach provenance to the existing Point,
   update confidence as credibility-weighted average — the Graphiti
   pattern); 0.80-0.92 → batched cheap-model verdict (≤1 call per session,
   "same claim rephrased? yes/no"); < 0.80 → NEW.
3. **Supersede:** a NEW claim that directly contradicts an existing
   high-confidence claim emits a NAND edge, not a `supersede_point`
   (supersession is reserved for credibility-tier-higher or
   human-approved sources — v1 default keeps both sides with EP
   confidence, which is exactly what belief propagation needs).

- **Cost:** deterministic graph reads + ≤1k tokens of batched verdicts
  ≈ $0.0005.

### S6 — Delta write with provenance (0 LLM)

Through the existing SDK so nothing downstream changes:

- `create_point(kind, content, dedup=True, confidence=...)` → Points with
  `provenance(source_id, span, quote, speaker, extracted_by="value@0.1")`
  and `extractedFrom` edge to the session Source; `about*` edges via
  `create_about_edge` to the `sessionCaptured` Event.
- `add_subject` / `add_object` (grounded ids from S5a).
- `add_operator(IMPL|NAND, [src, dst], provenance)` — operator confidence
  flows into `ep.py confidence_to_prior` (already wired: extractor
  confidence → Beta(α,β) prior).
- **NO per-turn nodes (product-owner ruling, 2026-08-09).** Three-layer
  model (validated against KG best practice — GAF/SEM, MeetGraph, context
  graphs): (1) **OCCURRENCE = Event node** (eventKind **`agentSession`** — an
  agent-human/agent-agent session, distinct from human-to-human
  `conversation`/`meeting`; ALREADY code-wired: sdk.py AgentSession
  indexing + hybrid search, mcp_server AgentSession events. Happened at T,
  participants with roles, bi-temporal startedAt/endedAt + capturedAt,
  content-addressed ID for idempotent re-capture; §4.5 Event + §3.5
  Subject→Event→Object performs/produces/uses); (2) **CONTENT =
  Source** (sourceKind `conversation`, summary + story arc `narrative_arc`,
  §4.6 — mutable artifact, rewritable); (3) **EPISTEMIC = Points** derived.
  Linking: `Subject -performs-> Event -produces-> Source`; `Points
  -extractedFrom-> Source` (canonical provenance path, §3.3); `Points
  -aboutEvent-> Event` (denormalized shortcut for occurrence-scoped
  queries); Source `references` Entities (§3.4). The Event is the
  immutable occurrence anchor (Source may be rewritten/merged — occurrence
  identity must not move with it); it also carries occurrence-level
  trust metadata and enables cross-event-kind timelines (the context-graph
  "decision events" layer). Guard: skip the Event when a capture yields no
  derived Points and no temporal/participant queries (conditional
  enrichment, not mandatory).

## Value-assessment prompt sketch

```text
SYSTEM: TASK: assess_value
You are the value gate for a knowledge graph. The graph stores only what is
durably valuable. Most conversation is NOT worth storing — saying "nothing
here" is a successful result, never a failure. There is no minimum and no
quota on nothing.

VALUE DEFINITION (compiled from the expansion pack ontology):
- objectKinds we track: product, feature, customer, competitor, customerSegment, market
- pointKinds we store: jobToBeDone, useCase, userJourney, valueProposition, decision, statement, ...
- relations we care about (predicate / mechanism):
    contains        IMPL   product → feature
    addresses       IMPL   feature → customerSegment
    competesWith    NAND   feature → competitor
    targets         IMPL   product → customerSegment

GATE RULES — keep a segment ONLY if it does at least one of:
1. NEW       asserts a fact or decision about a tracked objectKind the
             graph does not already know;
2. REVISES   contradicts or refines an existing claim (NAND/supersede
             candidate);
3. CONNECTS  asserts support/attack between two tracked concepts not yet
             linked;
4. RESOLVES  settles an open question the graph tracks.

DO NOT keep: opinions without commitment, transient status, tool output,
boilerplate, restatements of existing claims (mark REPHRASE, not NEW).

CONFIDENCE RUBRIC (per kept item, self-assess):
- 0.9+  explicit unambiguous assertion; authoritative speaker
- 0.7-0.9  clear assertion, some hedging or second-hand
- 0.5-0.7  implied, inferred, or contested
- <0.5  speculative — keep only as a tagged low-confidence hypothesis

USER:
Current graph state for entities in this window (<2k tokens of
entity summaries + their top claims — see conditioning below):

<graph-state summary>

Conversation window (turns 12-40, span-annotated):
<window text>

Return JSON: {"keep": [{"start": "t14:120", "end": "t14:210",
"kind": "decision", "confidence": 0.8, "why": "new decision on Tortoise
pricing, not in graph"}]} — or {"keep": []} if nothing qualifies.
```

## Extract-nothing policy

**Enforcement:**

1. The gate prompt states empty output is success (above).
2. The pipeline accepts `keep: []` end-to-end: S2/S3/S4 short-circuit, S6
   writes nothing beyond the episodic baseline, and the audit log records
   `extraction: value, kept: 0` as a normal row. No quota penalty, no
   retry, no minimum-count prompt anywhere.
3. The gate runs before generation, so nothing is generated when nothing
   is kept.
4. Degradation guard: keep-ratio >40% for ≥3 consecutive windows → fail
   closed to extract-nothing for the session + alert (never regex).

**Measurement (the under-extraction risk):**

- **Keep ratio** per session/pack/model-version — distribution, not mean;
  alert on drift (a pack update that accidentally zeroes extraction).
- **False-negative rate:** sample 1-in-N sessions through a frontier-model
  judge ("what high-value items did the gate miss?") — target ≤10-15% of
  high-value items. This is the calibration-via-review loop, aligned with
  the existing human-approval research
  (`docs/research/2026-08-07-human-approval-tortoise-artifact.md`).
- **Empty-session rate:** target 20-40% (most sessions *are* routine).
  If it's 0%, the gate is not actually gating.
- **Graph utility:** retrieved-use proxy — do kept Points get hit by
  `search_engine.py` / `memory_orchestrator.py` queries? Zero-hit kept
  points for a month ⇒ gate is over- or mis-gating.
- **Amplification ratio:** new non-episodic nodes / turn (target ≤ 0.15 vs
  regex's 1.6) — the headline KPI.

## Graph-state conditioning

Never feed the whole graph. Follow the Zep/Graphiti resolution-pass
pattern: retrieve *before* assessment, summarize *at write time*.

1. **Per-window retrieval:** entity-like strings in the window (cheap NER
   — existing `_SemanticStage` at extraction time or a deterministic
   proper-noun pass at gate time) → embedding kNN against existing
   Subject/Object nodes → top-8-12 entities.
2. **Materialized summaries:** each entity node carries a 1-line summary +
   top-3 related claims, written once at creation (Honcho-style
   "representation"). Fetching is a graph read, O(1), **no LLM at gate
   time**.
3. **Claim-level conditioning:** top-k claims (embedding-similar to the
   window) mentioning those entities — these are the REPHRASE/supersede
   candidates the gate compares against. Cap ≤12 claims.
4. **Budget:** graph state ≤4k tokens of the ~20k-token gate context; the
   rest is conversation.
5. **Cold start:** empty graph → empty conditioning → everything is novel,
   and the ≥2-segment frequency gate is the only brake. Fine for v1.
6. **Cache:** entity summaries are stored on-node, so conditioning never
   re-invokes an LLM — the gate's context stays deterministic and cheap.

## Cost / latency controls

- **We never feed the whole session.** 100-800k tokens → S0 deterministic
  filter → ~60-80k candidate tokens → 4-5 windows of 16k.
- **Cheap/frontier split:** S1-S3, S5 verdicts on Flash tier; S4 (warrants)
  on frontier, hard-capped at 3/session.
- **Batching:** windows run in parallel; near-threshold dedup verdicts
  batched into ≤1 call; entity embeddings precomputed.
- **Latency:** capture returns immediately (Source + session Event,
  deterministic, idempotent); S1-S6 run as an async extraction job via the
  existing event-log infrastructure (`shared_state/event_log.py`) and write
  deltas as events. Synchronous-vs-async is a config flag for v1
  (background is the default — don't block capture on intelligence).

Per-session budget at DeepSeek V4 Flash ($0.14/M in, $0.28/M out) + one
frontier warrant call (~$3/M in, ~$15/M out):

| Stage | Model | In (k) | Out (k) | Cost |
| ------- | ------- | -------- | --------- | ------ |
| S1 value gate ×5 windows | flash | 80 | 1 | $0.012 |
| S2 extraction ×8 segments | flash | 24 | 2.4 | $0.004 |
| S3 relations ×5 windows | flash | 15 | 1.5 | $0.003 |
| S4 warrants ×1 | frontier | 4 | 0.5 | $0.020 |
| S5 resolution (deterministic + verdict) | flash | 1 | 0.2 | $0.000 |
| S6 write | — | 0 | 0 | $0 |
| **Total** | | | | **≈ $0.04** (typical $0.02-0.05) |

At 100 sessions/month that's **$2-5/month in LLM** — versus regex, which
"pays" nothing in LLM but burns the entire solo node cap in ~1.5 sessions
and ~1.6M write ops/month. The design trades a rounding-error LLM bill for
a 30-100x cut in permanent graph cost.

## Target output volumes (per median session: ~98 turns, ~200k tokens)

| Artifact | Regex today | Value-first | Notes |
| ---------- | ------------- | ------------- | ------- |
| Conversation Source (summary+arc) | 0 | **1** | the raw record, indexed as a Source (§4.6) |
| State entities (Subject/Object) | (regex has none) | 0-8, median 2-4 | frequency-gated, vocab-constrained |
| Epistemic Points (kept) | ~15,700 (88% noise) | 3-12, median 5-7 | value-gated |
| Operators (IMPL/NAND) | ~0 (no relations) | 0-8, median 2-4 | within-window only |
| Warrant Points (derived) | 0 | 0-3 | frontier, low confidence |
| **New nodes / session** | **~16,000** | **~105-125 total, ~8-25 non-episodic** | |

**Caps math:** a solo user (25k cap) survives ~200-3,000 value-first
sessions vs ~1.5 regex sessions. Monthly at 10k sessions: ~80k-250k nodes
vs ~10M+ with regex. And because extract-nothing is first-class, growth is
*sublinear in conversation volume* — doubling sessions does not double
nodes; new knowledge is rare after the first few sessions on a topic. That
is the "fixes by construction" property: the graph grows with value, not
with volume.

## Repo integration

- **New module:** `tortoise/value_extractor.py` implementing the same
  `Extractor` protocol (`extractor.py:50`) — segment in, events out — so
  nothing downstream changes.
- **Model config:** reuse `OpenAICompatModel` (`models.py`) — one adapter
  for DeepSeek/Gemini/OpenAI/Ollama; two instances: `value_cheap_model`,
  `value_frontier_model` (config-only swap).
- **Mode wiring:** new `TORTOISE_SESSION_EXTRACTION=value` mode in
  `hosted_api.capture_session` (#312 semantics preserved; `auto` → value
  when provider key present; `regex` stays the always-works baseline).
  Replace the regex decision/claim loops
  (`hosted_api.py:1590-1635`) with the async job; keep the Source +
  `sessionCaptured` Event + quota estimate (now computed from the
  *guaranteed* episodic baseline + a hard value-mode ceiling, not regex
  matches).
- **Value brief:** compiled from pack manifests via `domain_loader`
  (`known_kinds()`, kind validation) — the ontology is the source of the
  value definition, which is what makes it "value-first".
- **Quota:** `MAX_EXTRACTIONS_PER_TURN` becomes a *ceiling*, not a target;
  add `MAX_VALUE_POINTS_PER_SESSION` (default 50 — 4x the median, hard
  backstop only).

## Risks & mitigations

| Risk | Mitigation |
| ------ | ----------- |
| Gate under-extracts (silent loss) | FN-rate judge on sampled sessions, keep-ratio drift alerts, graph-utility KPI |
| Gate over-extracts (degradation) | keep-ratio >40% fail-closed, hard per-session ceiling |
| Cheap model invents kinds | vocab-restricted pointKind/subjectKind/objectKind + `kind_is_known` validation |
| Entity pollution | frequency gate (≥2 segments), kind validation, embedding threshold |
| Confident-but-wrong claims | confidence gate (≥0.6 to write; 0.5-0.6 → draft), review-calibrated per-kind offsets feeding `confidence_to_prior` |
| REPHRASE merges distinct claims | 0.80-0.92 band goes to a batched cheap verdict, never auto-merge |
| Frontier warrant noise | ≤3/session, derived:true, confidence ≤0.6, excluded from fact retrieval |
| Async extraction races with queries | deltas write as events; eventual consistency is acceptable — extraction is a few seconds behind capture |
