---
title: "Epic #2080 scoping — adopt gbrain's measurable memory practices"
type: decisions
issue: "#2080"
date: 2026-09-01
created: 2026-09-01
status: scoping
domain: product
doc_status: draft
subjects.team: epistemic-team
---

# Scoping — #2080: adopt gbrain's measurable memory practices (write-path evals, EP-scored volunteering-memory reflex, opt-in auto-ingestion)

> Epic-tier scope (epic-scope skill). Inputs: epic #2080 body (O/I/T +
> learnings map + W1–W7 workstreams + open questions), research brief
> `docs/research/2026-08-31-gbrain-learnings/research-brief.md` (2026-09-01,
> deep, external+internal), raw notes `raw-notes.md` (692-line append-only
> codebase walkthrough), ONTOLOGY.md v3.6 (§3.1 operators, §5 vocabulary).
> Complexity: **complex** (epic label). Method: double-diamond boundary cut
> with high-level E2E written BEFORE user journeys, per the skill.
>
> **Findings date:** 2026-09-01

## Step 0 — Axis Research Notes (issue #231 D11)

The research brief is deep (adversarial check, adopt/adapt/skip per
workstream, assumptions register with validation plans), so most axes are
**deduped against the brief — justified skips**:

| Axis | Boundary question | Basis (brief section) | Verdict |
|------|-------------------|-----------------------|---------|
| Ontology / why-layer recall semantics | How do NAND/IMPL/CORRECTS/contestation map to recall content? | Brief "W4 — Why-aware recall" fit-audit table (gaps are in-place-fillable: contentiousness not scored, no assembled support chain, no trade-offs, no dig-deeper pointers) + ONTOLOGY §3.1 (NAND/IMPL/CORRECTS + supersession) + §5 (P9/contestation) | Justified skip — fully bounded by brief + ontology |
| Write-path eval unit of analysis | Point-level vs page-level salient-unit survival | Brief A1 (medium) + W2 ADAPT ("salient-unit survival = does the point (or REPHRASE-linked point) survive with provenance + EP update?") | Justified skip — bounded (point-level, REPHRASE-linked dedup) |
| Onboarding-toggle integration with #1976 | Where does the toggle live, against what surface? | Epic dependencies + brief W6 SKIP ("#1976 governs W6 toggle placement; gbrain contributes nothing new") — #1976 is OPEN (epic: onboarding, shrink wizard, graph-held state) | Justified skip — bounded (build against #1976 output, never current wizard); internal repo fact, no external query |
| Reflex delivery surface | MCP tool vs client hook vs SDK callback? | Epic open question + mission constraint | Justified skip as a *boundary*: delivery-surface build deferred to a later issue; the decision logic + why-content are in scope and graded via harness seams |

**Queries fired (2, both on the W4 contention-driven-recall thesis — the
only axis the brief left genuinely unbounded: A4 is low-confidence
"no external precedent found" and the epic's headline claim):**

1. [competitor framing] AI agent memory systems surfacing conflicting
   beliefs / contradictions at retrieval time — shipping product or
   benchmark scan.
2. [canonical/precedent framing] Controversy-aware retrieval and ranking
   boosted by contention vs confidence-only — IR literature.

**Findings (provenance: Perplexity web search, 2026-09-01, 2 queries; also
appended to the brief's `## Raw Notes` via `_research_append.sh`):**

- **No shipping product** surfaces belief conflicts / why-context at recall
  time (consistent with the brief's A4 low-confidence hypothesis — the W4
  headline remains novel as a *product feature*).
- **BUT academic precedent exists** for the underlying mechanism:
  - Conflict-Aware Memory Primitive (arXiv 2608.08236): write-time conflict
    resolution marking incompatible claims `SUPERSEDED`/`CONTESTED` with
    provenance so readers see a resolved state — directly analogous to
    Tortoise's NAND/CORRECTS + status vocabulary.
  - Graph-Native Cognitive Memory with formal belief revision (arXiv
    2603.17244): surfaces both current AND superseded beliefs at retrieval
    with provenance — analogous to the bi-temporal supersession surface.
  - Belief Memory under partial observability (arXiv 2605.05583): keeps
    multiple candidate conclusions with probabilities, surfacing
    alternatives together at retrieval — analogous to the trade-offs +
    EP-weight surface.
  - Controversy-aware IR/argument retrieval: documents ranked normally then
    boosted by discussion-cluster controversy, stance-labeled pro/con
    collections (CEUR-WS Vol-2936), axiomatic re-ranking for argument
    retrieval (Webis), indexical-bias evaluation on controversial topics
    (ACL 2024 findings) — precedent for **contentiousness as a ranking
    signal**, which is W4's core thesis.
- **Implication for the boundary:** the conflict-surfacing E2E is
  implementable (mechanism precedent exists), the why-layer product claim is
  still the differentiator, and W7's comparison-systems.md gains citation
  material for the mechanism rows. The Research axis rating (high) is
  driven by *Tortoise-specific* integration (EP-scored contentiousness in
  ranking + why-context assembly), not by novelty of the idea itself.

## Scope Boundaries

### In Scope

1. **W1 — Learnings map doc** (`docs/research/2026-08-31-gbrain-learnings/`):
   formalize the adopt/adapt/skip verdicts per gbrain practice + licensing
   gate (MIT/MIT, attribution obligations named, no vendoring).
2. **W2 — Write-path eval (Cat 35 port):** planted-gold benchmark over the
   session→graph ingestion pipeline (`session_import`/`dream.py`) —
   salient-unit survival at the point level (incl. REPHRASE-linked dedup),
   distractor leakage, provenance accuracy, quote fidelity. Deterministic
   mechanical checks authoritative over judge output; judge-blind salience;
   committed, CI-gated baseline that **can fail**; fix-wave protocol
   (publish bad number → name failure classes → fix → re-run same frozen
   corpus + pinned judge). Cost-bounded runner (BPRE default, `--full`
   opt-in, HARD_STOP_USD).
3. **W3 — Volunteering-memory harness (Cat 34 port) + why-layer suite:**
   fixture schema + sealed gold + `fixtures_hash` + holdout split;
   per-turn replay scoring know-to-ask failure, false-fire anti-gaming,
   push precision/recall under a pointer budget, write-back fidelity +
   provenance, continuity pairs; **source-isolation gates at zero**;
   harness seams = Tortoise's REAL integration points (MCP
   search/ask/recall, `claude-hooks/session-{start,end}.sh`,
   `session_import`) with env-key stripping for hermetic runs. **ADD the
   Tortoise-original why-layer suite:** given ONLY the surfaced context for
   a state with planted conflicts (NANDs, superseded predecessors, contested
   alternatives), grade "what contradicts this?" / "why is this believed?" /
   "where do I dig deeper?" — conflict-surfacing rate + dig-deeper
   navigation accuracy.
4. **W4 — Why-aware recall (HEADLINE, read-side, INTEGRATED — no new
   tool):** enrich the EXISTING recall surfaces (ask / analyze / search /
   MCP) so any recalled state returns: (a) **why** — support chain + ledger
   evidence + EP weight; (b) **conflict** — active NANDs, contested claims
   (P9), supersession history (bi-temporal); (c) **trade-offs** — decision
   alternatives with EP weights + mitigations; (d) **dig-deeper** — labeled
   navigation pointers ("read supports", "read the counterargument (NAND)",
   "see what changed (superseded)"). **Contentiousness becomes a scored
   recall signal** (variance/contested weighting — currently "surfaced,
   never scored" at `mcp_server.py:1018`), used BOTH as a relevance boost
   and as a why-context trigger. **Delivery (D1/D2, 2026-09-01):** phase-1
   synchronous event-in → context-out IS in scope — SDK `volunteer_context()`
   + `POST /v1/context`, **one shared implementation running in BOTH hosted
   and self-host modes** (not hosted-only). Tenancy resolves against the
   **per-graph API key** (D3, aligned with #2083) — contract must stay
   reversible to per-user slices without breaking. Pushed channel: the
   EP-scored reflex DECISION LOGIC + why-context assembly are in scope and
   graded by W3 harness seams. **Phase-2 async streaming (webhook/SSE) is
   OUT of scope — filed as #2081** (TogetherCrew/Temporal precedent check
   first). Gate-before-build: existing-tool fit audit already completed in
   research — result: **no new tool needed**, all four context types
   fillable in-place (documented gaps: contentiousness not scored, no
   assembled support-chain narrative, no trade-offs, no dig-deeper
   pointers).
5. **W5 — Automatic memory ingestion — MOSTLY BUILT; re-scoped to
   write-path QUALITY (Daniel, 2026-09-01):** the feature already exists —
   onboarding Q3 toggle + `session_recording` flag (same key as dashboard
   Memory sources off-switch), `POST /v1/sessions` capture with 2xx
   receipts, codex/pi/claude-desktop parsers with content-hash idempotency
   (#1727). **The gap is quality, not the feature:** salient-unit survival
   on planted gold (measured by W2), provenance stamping on every ingested
   point, EP updates on ingestion, dedup incl. REPHRASE-linked keys,
   triage-with-verified-segment rescue (anti-hallucination gate), frozen
   version-stamped write verb contract. **Opt-in, disclosure-visible** —
   default ON covered by ToS per current onboarding; toggle contract
   (feature flag + disclosure semantics) exposed for W6/#1976's UI; UI out
   of scope.
6. **W7 — Public benchmark discipline + published runs:** sealed answer
   keys at the boundary; official `recall_all@5` semantics (NOT any-hit);
   pinned judge versions; validated receipts naming the exact commit + corpus
   hash + judge pin; committed baselines with `justification` required for
   blessing regressions; errata policy (annotate, never silently edit);
   `comparison-systems.md` vs MemPalace/gbrain/Supermemory/Mem0 with
   mechanism-named rows + neutral cited tables + no win claims on benchmarks
   not run; **one full LongMemEval 500-question sealed run published** with
   official keys AND Tortoise's 4 documented semantics divergences
   (`dataset_audit.py`) named as variants; publish bad numbers on purpose.

### Out of Scope

- **W6 UI work (onboarding + Settings toggles)** — defer to **epic #1976**
  (agent-driven onboarding, OPEN): toggle placement, disclosure checkpoint,
  Settings → Memory sources surface, agent just-in-time proposals. This epic
  ships only the ingestion engine + toggle contract; **do not start W6 UI
  until #1976 ships or is clearly blocked**, and build it against #1976's
  output, never the current wizard.
- **Reflex delivery-surface build (phase-2 async streaming)** — webhook/SSE
  push, async precompute on the dream cycle, event ingestion contract —
  defer to **#2081** (filed; TogetherCrew/Temporal precedent check first).
  Phase-1 synchronous delivery (SDK `volunteer_context()` + `POST
  /v1/context`, hosted + self-host) IS in scope per D1/D2. The EP-scored
  reflex decision logic + why-content are graded via W3 harness seams.
- **Any new standalone retrieval tool / recall surface** — prohibited by the
  epic's no-duplicate-tool constraint. The fit audit says gaps are fillable
  in-place; if a build-phase finding contradicts that, a genuinely new
  surface requires Daniel's sign-off and is out of scope here.
- **Vendoring gbrain/gbrain-evals code or corpus files** — reimplement ideas
  only (schemas, metric formulas, receipts discipline); any copied corpus
  file would carry the MIT notice (none planned). No gbrain runtime
  dependency (not on npm).
- **gbrain's codex-fragment seam** — skipped (no Tortoise codex
  integration); the anti-gaming lesson (preamble slugs don't count) is kept.
- **gbrain's onboarding/UX patterns** — skipped entirely; #1976 governs.
- **Ask-surface exposure decision (#2013)** — W4 routes through
  search/analyze meanwhile; the ask gating decision belongs to #2013, not
  this epic (ask-surface work is conditional on its outcome).
- **Adaptive return-sizing / source-boost prefix map** (gbrain retrieval
  knobs noted in research as "cheap, useful additions") — not in the epic's
  O/I/T; defer to a later retrieval-levers issue (cf. #1657 precedent).

### Boundary Rationale

The cut principle: **this epic ships (1) everything that makes memory
measurable — the eval/harness/benchmark infrastructure and the published
numbers — and (2) the read-side why-layer, write-side ingestion QUALITY
(W5 already exists as a feature), and the phase-1 synchronous delivery
path (SDK + /v1/context, hosted AND self-host per D1), built only inside
existing surfaces.** Everything that is a *new user-facing surface*
(onboarding/Settings UI, phase-2 async push wiring, a new retrieval tool)
is deferred — UI to #1976, async streaming to #2081, and new tools are
prohibited outright. Tenancy resolves per-graph API key (D3 → #2083),
keeping the /v1/context contract reversible to per-user slices.
The why-layer is the moat (gbrain structurally can't build it), the eval
discipline is the durable asset (gbrain's 61.5%→88.1% pattern), and both
are testable end-to-end without touching the surfaces that are owned by
other epics. Targets inside scope follow the research's recommendations
where the epic's literal numbers overreach (e.g. distractor leakage
tolerance — see E2E-2).

## Customer Value Map

| Scoped Capability | User-Visible Value |
|-------------------|--------------------|
| W1 Learnings map doc | Anyone can audit exactly which gbrain practices we adopted/skipped and the MIT terms — no legal ambiguity and a settled baseline for future memory work. |
| W2 Write-path eval | The session→graph pipeline can no longer silently lose or invent memory — a failing CI-gated benchmark catches regressions before they ship (the 61.5%→88.1% fix-wave pattern, provable). |
| W3 Volunteering-memory harness | Proof that the agent knows WHEN to volunteer memory and when to stay silent — know-to-ask, false-fire, push, write-back, and continuity all scored on Tortoise's real seams, with zero cross-source leakage. |
| W3 Why-layer suite | Proof that a recalled state's surfaced context alone lets the agent answer "what contradicts this?", "why is this believed?", "where do I dig deeper?" — the why-layer is measurable, not vibes. |
| W4 Why-aware recall | Recalling anything now surfaces the belief AND its support chain, active conflicts, superseded history, trade-offs, and dig-deeper pointers — the user (and agent) see WHY it's believed, exactly when it's contested. |
| W4 EP-scored reflex logic (graded) | The volunteering decision becomes EP-scored (support + contentiousness) and benchmarked — "my agent should have known that" is engineered, not hoped for, before any delivery wiring ships. |
| W5 Opt-in auto-ingestion | Sessions flow into memory automatically with provenance and EP updates, only when the user opts in — memory builds itself with zero transcription effort and zero silent capture. |
| W7 Public benchmark discipline | Published, sealed-key numbers with receipts and errata (incl. a real 500-Q LongMemEval run) — nobody can claim memory quality without auditable proof, and bad numbers get published on purpose. |

## Step 3 — Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | medium | Why-context rendering (bounded "explore" block vs inline), pointer budget, and the ingestion toggle contract are real decisions — but all surface BUILD defers (#1976 UI, later delivery-surface issue), keeping UX below high. |
| Architecture | high | Enriching four existing recall surfaces in-place, contentiousness as a new scored signal touching ranking, the write-side ingestion engine (dedup + provenance + versioned verbs), CI-gated planted-gold infra, and a real-seams hermetic harness are substantial integration work across MCP/ranking/ingestion. |
| Ontology | medium | No new entity types (NAND/IMPL/CORRECTS/P9/bi-temporal already exist), but contentiousness moves from "surfaced, never scored" to a scored signal, the why-context assembly contract is new, and §5 controlled vocabulary gains the dig-deeper/why-block labels. |
| Research | high | The W4 contention-driven-recall thesis is novel as a shipped product feature (academic precedent exists — see Axis Research Notes — but no product precedent); the why-layer suite design is Tortoise-original (fixture generator + planted-conflict gold conventions); judge-calibration and fix-wave loops are research-shaped. |
| Org Infra | medium | CI-gated baselines, sealed keys, receipts, comparison-systems.md publication discipline, and a 500-Q sealed LongMemEval run (embeddings cache, cost-bounded) — new standing infrastructure but well-precedented by gbrain's machinery. |
| **Overall** | **complex** | Matches the epic label; the headline W4 claim is unverified at product level (A4), the unit mismatch in the write-path port is real (A1), and the ask surface is gated (#2013, A12) — all managed by making the evals the first ship-able increments. |

## Step 4 — High-Level E2E Test Cases

> Written BEFORE user journeys. Behavioral, not presentational — verifiable
> without UI detail. The detailed E2E tests in `epic-plan` flesh these out.

### E2E-1: Why-layer conflict-surfacing (the headline)
**Given:** a state (Point) in the graph with active NANDs and a contested
claim (P9) against it; a user/agent recalls it through an EXISTING recall
surface (ask / analyze / search / MCP).
**When:** the state is recalled and appears in the surfaced context.
**Then:** the surfaced context includes the current belief AND the conflict
structure — the active NAND/counterargument, the contestation, and the
supersession history (if any) — plus at least one dig-deeper pointer.
**And:** conflict-surfacing rate ≥ 0.95 on the planted-conflict corpus;
a point with NO conflicts surfaces without conflict noise (no false
conflict surfacing).
**And:** for a decision point, the surfaced context includes the
trade-offs — alternatives with EP weights + mitigations (W4 capability c).
**And:** when the query touches the conflict, a contested-but-relevant
state is surfaced/ranked with the contentiousness signal participating
(contentiousness is a scored ranking signal, not just a why-trigger; the
full A/B vs confidence-only validation is an eval-phase artifact of the
W3 why-layer suite / W7, not an E2E gate here).

### E2E-2: Write-path planted-gold survival (the W2 benchmark)
**Given:** a planted-gold corpus (fictional sessions, salient units with
verbatim anchors, true-but-routine distractors, attribution hazards) and a
frozen, sealed answer key.
**When:** the session→graph pipeline ingests the sessions and the benchmark
runs against the committed baseline.
**Then:** salient-unit survival ≥ 80% macro and ≥ 75% strict
(point-level, REPHRASE-linked dedup accepted), 100% of sessions emitting,
distractor leakage ≤ 1 per run (the research-recommended tolerance,
pending sign-off item 3 — supersedes the epic's literal "zero" wording), and
provenance accuracy + quote fidelity ≥ 80% on the graded lanes.
**And:** a regression (e.g. a stripped provenance field) FAILS the CI gate —
the benchmark can fail, and the failure is publishable.

### E2E-3: Reflex know-to-ask / false-fire (the W3 harness)
**Given:** a scripted-conversation fixture with sealed gold (per-turn
should_retrieve labels), replayed through Tortoise's real seams (MCP
tools / claude-hooks / session_import).
**When:** each turn is scored against the gold.
**Then:** know-to-ask failure rate 0.00 (inject exactly when gold says
should_retrieve, silent otherwise) and false-fire ≤ 0.03 on the gate corpus.
**And:** push precision/recall under the pointer budget score on the same
replay (precision ≥ 1.000, recall per the committed baseline — the
pointer-budget mechanics are graded, not just the binary inject/silent
decision); write-back fidelity + provenance and continuity pairs pass.
**And:** re-mention suppression and below-notability openers never fire;
superseded facts don't surface as current.

### E2E-4: Source-isolation gates at zero (the W3 guard)
**Given:** the W3 harness running all suites (know-to-ask, push, write-back,
continuity, why-layer) across a multi-source / multi-team graph fixture.
**When:** any suite's replay writes or injects memory.
**Then:** source-isolation violations = 0 across all suites — no content
from source A ever leaks into source B's context, writes, or continuity
pairs.
**And:** env-key stripping is verified (no silent LLM path / ambient
reranker during hermetic runs).

### E2E-5: Ingestion toggle — engine contract, aligned with #1976 (W5 QUALITY work; UI deferred)
**Given:** the W5 write-path QUALITY work ships with the toggle contract
surfaced (feature flag + disclosure semantics); #1976 owns the onboarding
and Settings → Memory sources UI surfaces (out of scope here). Current
shipped state (2026-09-01): ingestion EXISTS with default ON (ToS-covered
per onboarding Q3) + the `session_recording` opt-out flag.
**When:** ingestion is enabled and disabled through the flag/contract, and
a session is captured under each state.
**Then:** the toggle contract is honored — default per current onboarding
(default ON, ToS-covered; the default-ON-vs-OFF question is a product
decision flagged to Daniel in the approval gate); toggling off blocks new
capture (server rejects with 409) and remains disclosure-visible;
toggling on runs session→graph write-back with provenance + EP belief
updates speaking the frozen version-stamped write verb
(protocol_version in every response).
**And:** the epic's O/I/T target "both toggles reachable in ≤ 2 clicks from
onboarding and Settings" transfers to **#1976's** E2E surface verification
(UI reachability is not testable in this epic).

### E2E-6: Supersession-aware recall (bi-temporal why-context)
**Given:** a Point that was superseded by a newer one (CORRECTS edge +
edge transfer) and an outdated predecessor still in the graph.
**When:** the superseded predecessor is recalled through an existing surface.
**Then:** the surfaced context shows it as superseded with the supersession
history and a dig-deeper pointer ("see what changed") to the successor —
it never surfaces as the current belief.
**And:** current-view search defaults return the successor as current
(no stale-belief-as-current).

### E2E-7: Dig-deeper navigation accuracy (the why-layer suite)
**Given:** a state with planted conflicts (NANDs, superseded predecessors,
contested alternatives) and ONLY the surfaced context available.
**When:** an agent is asked "what contradicts this?", "why is this
believed?", and "where do I dig deeper?".
**Then:** the agent answers all three correctly from the surfaced context
alone (conflict-surfacing rate ≥ 0.95; dig-deeper navigation accuracy —
following "read supports" / "read the counterargument" / "see what changed"
to the correct points — ≥ 0.95).
**And:** the answer requires no graph access beyond the surfaced context
(validates A11; if it fails, W4's context assembly changes first).

### E2E-8: Published LongMemEval 500-Q sealed run (W7)
**Given:** the official LongMemEval 500-question set, sealed answer keys,
pinned judge versions, and the Tortoise runner with its documented
semantics divergences.
**When:** the full run executes and the report is committed.
**Then:** the report names the exact commit, corpus hash, and judge pins;
reports BOTH official `recall_all@5` AND Tortoise's legacy/divergent
variants (dataset_audit.py's 4 divergences) as explicitly-labeled variants;
receipts validate (run_status, verdict, failure_origin).
**And:** a `comparison-systems.md` table sits alongside with
mechanism-named rows vs MemPalace/gbrain/Supermemory/Mem0, neutral cited
tables, and no win claims on benchmarks not run; errata policy documented.

## Epic Scope Ready for Review

**Scope:** 6 workstreams in (W1 doc, W2 write-path eval, W3 harness +
why-layer suite, W4 why-aware recall in existing surfaces + EP-scored reflex
logic, W5 opt-in ingestion engine, W7 public benchmark discipline + published
500-Q run); 3 explicitly deferred (W6 UI → #1976, reflex delivery-surface
build → later issue, any new retrieval tool → prohibited without Daniel's
sign-off).
**Customer value map:** 8 capabilities mapped (one line each, user-visible).
**E2E test cases:** 8 drafted (conflict-surfacing, write-path gold, reflex
know-to-ask/false-fire, source-isolation, ingestion toggle, supersession
recall, dig-deeper navigation, LongMemEval 500-Q).
**Complexity:** UX medium / Architecture high / Ontology medium /
Research high / Org Infra medium / overall **complex**.

### Items requiring explicit sign-off

1. **In/Out cut** — especially: W6 UI deferred to #1976 (never the current
   wizard), reflex delivery-surface build deferred to a later issue (the
   logic + why-content stay in scope, graded via harness seams), and no new
   retrieval tool.
2. **W4 scope = existing surfaces only** — ask work is conditional on
   #2013; search/analyze are the unblocked path. Acceptable?
3. **W2 target interpretation** — epic says "zero distractor leakage";
   research recommends ≤ 1 distractor per run tolerance (gbrain's own
   borderline-mention variance was 1/86) with macro ≥ 80%, strict ≥ 75%,
   100% sessions emitting, quote fidelity ≥ 80%. Adopt the tolerance?
4. **Ingestion default** — default OFF (opt-in) with the toggle contract
   exposed to #1976; disclosure checkpoint mirrors #1976 precedent.
5. **Complexity ratings** — Research high is driven by the why-layer suite
   design + no-product-precedent headline, not by IR novelty (academic
   precedent found 2026-09-01 — see Axis Research Notes).
6. **The 8 E2E cases** as the boundary anchors for detailed planning.

Review the scope boundaries, customer value map, complexity ratings, and
E2E test cases. Reply **"proceed"** to continue to detailed planning, or
give feedback.
