---
title: "Research Brief — Classification Cues for Decision / Event / Claim (Epic #909, R1+R2)"
type: research
domain: operations
doc_status: draft
created: 2026-08-11
ownedBy: epistemic-team
aboutObjects: tortoise, value-extractor, ontology
epic: "#909 value-first mining system"
covers: R1 (decisions ≠ events), R2 (atomic decisions)
---

> Research stage output for epic #909, focus area: classification cues. Grounded in
> speech-act theory, argument mining, dialogue-act/meeting research, and a measured
> scan of 8 real pi sessions. Honest labeling: what is **reliable** (replicated,
> corpus-backed, or directly measured) vs **research-grade** (single-study or
> inferred).

---

## 1. The core model: three-way classification via illocutionary force

The decision/event/claim trichotomy is not an arbitrary tag set — it maps cleanly
onto Searle's speech-act taxonomy, and the mapping gives us the cue structure for
free (Searle 1976; Stanford Encyclopedia of Philosophy "Speech Acts").

| Requirement class | Searle class | Direction of fit | Sincerity condition | What it commits the speaker to |
|---|---|---|---|---|
| **DECISION** (R1) | **Commissive** (promise, offer, agree, decide, choose) | world → word | intention | a future course of action |
| **EVENT** (R1) | **Assertive** (representative) — perfective/past | word → world | belief | that a state change occurred at T |
| **CLAIM** (R4) | **Assertive** — stative/gnomic/embedded | word → world | belief | that a state of the world holds |

**Key structural insight:** events and claims are BOTH assertives (word→world,
belief-condition). They differ on the *aspect/predicate axis*, not the
illocutionary axis: an event is an assertive whose proposition is a past state
change (accomplishment/achievement verb, perfective aspect: *repaired, shipped,
fixed, completed*); a claim is an assertive whose proposition is a general or
embedded state (*GLM costs $0.60/M, I believe X is better*). A decision is the
only commissive — *or the report of a past commissive act* ("we decided to X",
"we chose Postgres" — past-tense performative verbs still report a commitment
and should classify as decision, per the owner's own rule: "chose" → decision).

**Consequence for the classifier:** the owner's cue lists ("did/repaired/fixed/
shipped" → event; "decided/will/should/chose" → decision) are a *lexical
shorthand* for a deeper structural distinction that a single regex or cue-list
cannot express reliably. The reliable classifier is two-axis:

1. **Axis A — illocutionary type:** performative verb semantics (commissive verbs:
   *decide, choose, agree, commit, promise, plan-to, resolve, pick, go-with,
   settle-on* vs assertive verbs: *believe, think, claim, state, conclude,
   observe, show*) + commitment operators.
2. **Axis B — within assertives, episodic vs epistemic:** tense + aspect +
   predicate class (accomplishment/achievement + past → EVENT; stative/gnomic/
   embedded-proposition → CLAIM).

**Reliability note (honest):** pure cue-list classification is measurably weak at
decision detection — the AMI DecisionDetector (the most direct prior art: decision
detection in real multi-party dialogue) reached **92% precision on train but only
68% on test** using dialogue-act + lexical features (Hsueh & Moore 2007). LLM
zero-shot speech-act classification lands ~70–85% accuracy on MRDA (Act2P 2025:
70.3→84.5% with prompt engineering; fine-tuned GPT-4o: 87% acc / 67 macro-F1 —
"Empirical Evaluation of Automatic Speech Act Classification" 2024). Argument-mining
claim detection with GPT-4 varies wildly by dataset: 85.3 F1 (premise) / 51.9 F1
(conclusion) on a legal corpus (PMC10691378), 48.9 macro-F1 on mental-health
narratives (2025.argmining-1.18), ~59 F1 claim identification (IJCNLP 2025). **The
takeaway: no single method reaches the R8 ≥0.90 layer-correct target alone; the
deterministic cue layer + LLM adjudication + thresholded eval is the right shape,
and the eval must be a rate, not a single-run claim (R8 already says this).**

### 1.1 The cue table (what to put in the prompt / deterministic layer)

Decision (commissive) cues, strongest first:

| Cue class | Examples | Strength |
|---|---|---|
| Performative verb, any tense | decided, chose, agreed to, committed to, settled on, resolved to, picked | **hard** (esp. "decided to X", "chose X") |
| First-person plural + deontic + action | "we will ship", "we're going to move to", "we'll go with", "let's adopt" | strong (agentive subject + action verb) |
| Decision idioms | "go with option B", "land on", "we're going with", "I'm leaning toward" (weak but real — owner's own review flagged "I'm leaning toward option B" as a missed decision) | medium |
| Plan/commitment frames | "action item:", "next step:", "we committed to", "our plan is to" | strong when followed by first-person action |

Event cues:

| Cue class | Examples | Strength |
|---|---|---|
| Past-tense accomplishment/achievement verb + narration context | repaired, fixed, shipped, completed, deployed, migrated, closed (PR), merged | **hard** |
| Perfect aspect | "we have shipped", "I updated", "we moved the table to" | strong |
| Tool-result echo | "the test passed", "build succeeded", output quoted from tool calls | strong (but should mostly be filtered at S0, not classified) |

Claim cues:

| Cue class | Examples | Strength |
|---|---|---|
| Epistemic predicate | believe, think, my understanding is, we found that, data shows, evidence suggests | hard |
| Gnomic/stative present | "X costs $0.60/M", "Y is the bottleneck", "Z doesn't scale" | strong |
| Embedded proposition under commitment-verb | "we decided that [claim]" | strong (split: decision + claim, linked IMPL) |

### 1.2 Where the owner's rules are right, and where they need refinement

**Confirmed by research and data:**
- "did/repaired/fixed/shipped/completed" → event. Reliable: these are
  accomplishment verbs in perfective aspect — the canonical episodic assertive.
- "decided/chose" → decision even in past tense. Reliable: past-tense
  performatives report the commissive act; for a knowledge graph the commitment
  is durable and should be a decision Point (with the time as metadata).

**Needs refinement (evidence-backed):**
- **"will" is ambiguous.** *Prediction* ("the build will take 5 min", "that will
  break X") is an assertive, not a commissive. The discriminator is subject
  agentivity: first-person (we/I) + action verb → commitment; third-person/It +
  state/event verb → prediction. The current regex (`hosted_api.py` decision
  patterns: `we will|I will|I'm going to|let's`) is agentive-constrained, which
  is right — but "we will" still fires on plan-narration ("we will need to check
  the logs" = anticipation of work, not a decision).
- **"should" is genuinely weak.** Linguistically "we should X" is a
  recommendation (assertive about desirability), not a commitment — commitment
  requires the speaker to have decided. Measured in real sessions: "should" is
  the agent's dominant self-deliberation form ("we should add X", "we should not
  kill it") — harvesting it as decision is precisely the noise the owner
  complained about. Recommendation: "should" → decision ONLY with corroborating
  context (followed by "let's do that", "I'll do X", "plan:"); otherwise →
  claim-of-recommendation or drop. **This is a deviation from the owner's
  shorthand cue list — flag it at the review gate.**
- **Negation and conditionality cancel commitment** (CommitmentBank: modal,
  conditional, negation, question operators systematically reduce speaker
  commitment — de Marneffe et al. 2019): "we should NOT kill it", "if X, we
  might Y", "we could Z" are not decisions.
- **Implicit decisions have no trigger word at all** ("I'm leaning toward option
  B", "let's go with X", "we'll just not specify model"). Cue-only recall is
  bounded; the LLM layer must catch these, and the eval must measure recall on
  them (the gold set should include trigger-less decisions explicitly).

---

## 2. Atomicity (R2): one commitment per point

### 2.1 The unit is the elementary discourse unit (EDU)

Discourse-segmentation research defines the EDU as the **minimal speech act** —
"an EDU is a minimal speech act or communicative unit, roughly clause-level
content denoting a single fact or event" (NoDaLiDa 2023; RST literature: EDUs
are clause-like, each must contain a verb, coordinated clauses and adjunct
clauses are separate units — IJCAI 2019; Tofiloski et al. 2009). Since a
decision is a commissive *act*, the atomic unit of a decision is exactly one
EDU/illocutionary act. **"Tiers are feature baselines AND usage is metered AND
no caps" is three EDUs = three commitments.** This gives the splitter its
theoretical ground: split at clause boundaries.

### 2.2 Concrete compound structures to split

| Structure | Example | Split |
|---|---|---|
| Coordination (and/but/or/as well as/both...and) | "tiers are baselines AND usage is metered AND no caps" | 3 decisions |
| Serial verb / comma lists | "we will build the API, add auth, and write docs" | 3 commitments |
| Rationale subordination (because/so that/to/for) | "we chose X because it's cheaper" | decision + supporting claim, linked IMPL (R2: "the what and the why-it-pays-for-itself are different") |
| Decision + embedded belief | "we decided that GLM is fine at $0.60/M" | decision (adopt GLM) + claim (cost), linked |
| Coordination of one decision + one event | "we shipped the fix and decided to open-source" | event + decision |

The pipeline doc's S3 already emits IMPL edges between claims; the natural
design is: **split at classification time (S1/S2), re-link at relation time
(S3)** — the rationale clause becomes an IMPL-supporting claim, exactly what R4's
IMPL chain wants.

### 2.3 Forcing atomic output (best practices)

1. **Split-then-classify order.** Propositionize (clause-segment) BEFORE
   classification so the classifier never sees a compound unit. This kills the
   compound-classification error too: a compound "X AND we decided Y" tends to
   be classified as decision because one conjunct is a decision.
2. **Contract + deterministic validator (R8 Layer-1).** Schema contract: one
   commitment per item. Deterministic validation on emitted content: count
   coordination cues (and/but/or/, + finite verb) and commissive predicates;
   >1 → retry once with split instruction → fail with reason. This is exactly
   R8's "atomic single commitment" contract gate — it is enforceable
   deterministically and cheaply, no LLM needed for the check itself.
3. **Few-shot compound→split exemplars** in the S2 prompt (3-4 real examples
   from the gold set).
4. **Atomic-propositions intermediate representation.** The 2026 KaLLM paper
   (MPropositionneur-V2) shows decomposing text into atomic propositions
   (minimal, semantically autonomous units) improves triplet extraction,
   especially for weaker models — supporting the cheap-model S2 design; the
   decomposition stage is where the frontier/cheap split can be tuned.
5. **Do NOT rely on sentence splitting.** Agent prose sentences routinely carry
   2-4 commitments (measured: dense assistant turns); sentence-level splitting
   (current `_utterances`) is insufficient as the atomicity mechanism.

---

## 3. Real-agent-conversation specifics (measured, not guessed)

I scanned 8 real pi sessions (4 tortoise + 4 agent-infra, the same pool the gold
set draws from). Findings that directly affect R1/R2 classification:

1. **Tool traffic dominates.** 5 agent-infra sessions: 63 user text messages,
   1,176 assistant messages, 1,419 tool-result messages. Tool results ≈ 2/3 of
   message traffic and the bulk of tokens. **If tool output reaches the
   classifier, "fixed/repaired/shipped" cue words from command output will be
   harvested as events at massive volume.** S0's boilerplate filter is therefore
   not a nice-to-have — it is the single biggest precision lever for R1.
2. **Meta-discussion pollution (measured).** In sessions about the mining system
   itself, "decided/decision" appeared 78× in 5 agent-infra sessions — and the
   overwhelming majority are the agent *discussing the extraction system* ("the
   decisions, claims, and action items are extracted as epistemic Points"),
   not making decisions. **A cue-based classifier on such a session explodes:
   the regex would harvest the system's own design discussion as decisions.**
   Mitigation: speaker-anchored commitment (a decision requires a first-person
   committing speaker: "we/I decided", not "the system extracts decisions"),
   plus the S1 value gate filtering by entity/value relevance.
3. **"we will"/"I will" are nearly ABSENT from assistant prose** (18 total
   "will" across 8 sessions of prose, zero "we will"/"I will" sentence hits in
   the sampled text). Agents narrate in past tense ("I updated X", "I created
   Y") and self-deliberate with "should" (44×) and "we need" (1×). The current
   regex decision patterns (`we will|I will|I'm going to`) are tuned to
   human-meeting language and mostly misfire on real agent prose — the
   dominant decision-adjacent forms are past-tense performatives ("decided",
   rare: 10×) and deliberation ("should": 44×). This is why the owner's
   examples (repaired/shipped = event) dominate: **agent sessions are mostly
   event narration with occasional micro-decisions.**
4. **Dense compound turns.** Assistant turns mix prose + code + tool quotes;
   a single turn routinely contains 2-5 extractable items of mixed classes.
   Classification on the window (S1) rather than per-utterance is the right
   granularity, but atomicity must be enforced per-item regardless.
5. **Real decisions are rare and trigger-less.** The single clearest "decided"
   hit sampled was a micro-decision ("I read the tool description and decided
   'I'll just not specify model'") — arguably process (R3) or low-value. The
   gold set's expected keep-ratio (5-25% of segments; 3-12 points/session) is
   consistent with what the data shows: few decisions, many events, most prose
   is narration.

---

## 4. Failure modes (ranked by expected frequency on real sessions)

| # | Failure mode | Evidence | Layer it breaks |
|---|---|---|---|
| F1 | Event narration harvested as decision ("we will check the logs", "we need to fix X") | Current regex patterns; 44× "should" deliberation measured | R1 |
| F2 | Meta-discussion harvested as decisions (session about the mining system itself) | Measured 78× "decided/decision", mostly self-referential | R1 |
| F3 | Tool-output echo classified as events/claims ("fixed" from `git log`, test output) | Tool results = 2/3 of messages | R1 |
| F4 | Compound decision emitted as one Point ("A AND B AND C") | Pipeline doc R2; current `_POINTS_DOC_SYS` has no split mechanism | R2 |
| F5 | Prediction "will" classified as commitment (it-will vs we-will) | Speech-act literature: modality cancels commitment (CommitmentBank) | R1 |
| F6 | Negation/conditional harvested ("we should NOT kill it") | Documented in-session critique of the regex; CommitmentBank | R1 |
| F7 | Trigger-less decisions missed (recall gap) ("I'm leaning toward option B") | Owner review itself; regex misses it | R1 (recall) |
| F8 | Rationale merged into decision ("we chose X because Y") | R2 review input D6/D7 | R2 |
| F9 | Past-tense performative misclassified as event ("we chose Postgres", "we decided to ship X first") | The exact D1/D12 owner failure in reverse; note the owner's rule already says "chose" → decision | R1 |
| F10 | Overlapping cue patterns double-count one sentence (I think + we should → 2 points) | Documented in-session critique of the regex | R1 |

The in-session critique quoted above (F6/F10 and the "leaning toward" example)
is the mining-system design session's own audit of the regex amplifier — it
independently corroborates the same failure list.

---

## 5. Recommendations for the R1/R2 design

**R1. Two-axis classification with a hard-cue deterministic layer + LLM
adjudication band.** (a) Deterministic pre-pass: hard decision cues
(past-tense performatives: decided/chose/agreed-to/committed-to; decision
idioms), hard event cues (past accomplishment verbs in narration context:
repaired/fixed/shipped/completed/deployed/merged), claim cues (epistemic
predicates, gnomic present). Hard cues decide directly (they are the reliable
80%). (b) Everything in the ambiguous band — "should"/"will" sentences,
"leaning toward" implicatures, negated/hypothetical forms, embedded
propositions — goes to the S1/S2 LLM with the cue table above as the rubric,
plus speaker-anchoring ("we/I committed" vs "the system said"). Expectation
setting: decision detection prior art caps at ~68-85% precision (AMI); the
≥0.90 layer-correct rate is achievable on *agent-written prose* (cleaner than
spoken meetings) but only with the deterministic layer doing the easy cases
and the eval measuring a rate per R8.

**R2. Propositionize before classify; validate after.** S1/S2 input units are
EDU-level clauses (split on coordination, serial lists, rationale
subordination), not sentences. Emitted items get a deterministic atomicity
validator (coordination cues + >1 commissive predicate → retry once → fail
with reason), which doubles as the R8 Layer-1 contract gate. Rationale clauses
("because X") become separate claims auto-linked IMPL to the decision at S3 —
this is the mechanism that makes R2's "the what and the why are different
decisions" real.

**R3. Reclassify "should" and "will" per data, and flag the deviation to the
owner.** Bare "we should X" = recommendation (drop or claim), not decision —
measured as the dominant false-positive form. "Will" requires first-person +
action-verb to be a commitment; third-person + state-verb is a prediction.
The owner's shorthand ("will/should → decision") is a *precision-losing*
shorthand; the design should implement the refined rule and present the
refinement as a review-gate decision.

**R4. Speaker-anchored commitment + S0 tool filtering as R1's first line of
defense.** A decision must be attributable to a first-person committing
speaker in the conversation (we/I decided, we will ship), never to quoted
material, tool output, or the agent's meta-discussion of the system. Tool
blocks must be excluded from classification input at S0 (already planned) —
measurement says this is the single largest R1 precision lever.

**R5. Gold set must include the hard cases, explicitly.** From the owner's
review + this research: (a) trigger-less decisions ("I'm leaning toward...");
(b) past-tense performatives ("we chose X", "we decided to ship serve --http
first" — already in the in-session critique); (c) compound decisions of 2-3
commitments; (d) negated/conditional forms ("we should NOT..."); (e) prediction-
will vs commitment-will; (f) meta-discussion windows (a session about the
mining system) with expected `keep: []` for decision harvesting. Layer-correct
rate ≥0.90 must be computed per class (decision-precision, event-precision,
atomicity), not as one blended number — the classes have very different base
rates (events ≫ decisions in real sessions) and a blended rate can hide
decision-recall collapse.

---

## 6. Sources

**Speech-act theory**
- Searle, J. (1976). *A Classification of Illocutionary Acts.* Language in Society 5(1). (Commissive vs assertive; direction of fit; sincerity conditions.)
- *Speech Acts* — Stanford Encyclopedia of Philosophy (assertives word→world; commissives world→word).

**Argument mining / claims**
- *Performance analysis of LLMs in argument component classification* (PMC10691378): GPT-4 85.3 F1 premise / 51.9 F1 conclusion (legal corpus); baselines sometimes better.
- *Mining Claims and Evidence in Mental Health Narratives* (ArgMining 2025): claim identification 32.0 F1, macro 48.9.
- *Can AI Validate Science? Benchmarking LLMs on Claim-Evidence* (IJCNLP 2025): best ≈0.59 F1 claim identification.
- de Marneffe, Simons, Tonhauser (2019). *The CommitmentBank* (P19-1412): speaker commitment to embedded clauses, 7-point scale; negation/modal/conditional/question cancel projection.

**Dialogue acts / meetings (decision detection prior art)**
- Hsueh & Moore (2007). *Automatic Decision Detection in Meeting Speech* (AMI DecisionDetector): 92% train / 68% test precision; 15 DA classes → 5 major classes gains +16% recall.
- *Modelling and Detecting Decisions in Multi-party Dialogue* (W08-0125, AMI): hierarchical decision-subdialogue detection ≈0.55-0.58 F.
- *Act2P: LLM-Driven Online Dialogue Act Classification* (Findings ACL 2025): MRDA zero-shot 70.3→84.5% accuracy with prompt engineering.
- *Empirical Evaluation of Automatic Speech Act Classification* (2024): fine-tuned GPT-4o 87% acc / 67 macro-F1; fine-tuned RoBERTa competitive.
- MRDA baselines: RNN 86.8% accuracy (COLING 2016).

**Discourse units / atomicity**
- *Comparing Methods for Segmenting Elementary Discourse Units* (NoDaLiDa 2023): EDU = minimal speech act, clause-level, single fact/event.
- *Neural Discourse Segmentation* (IJCAI 2019): EDUs as clause-like RST building blocks.
- Tofiloski, Brooke, Taboada (2009): segmentation candidates — clauses, coordinated clauses, adjunct clauses; every unit contains a verb.
- Pommeret et al. (2026). *LLM-based Atomic Propositions Help Weak Extractors* (KaLLM): atomic-proposition decomposition improves triplet extraction, esp. for weak models.

**KG-from-agent-conversation**
- Zep/Graphiti (github.com/getzep/graphiti; arXiv 2501.13956): episode ingestion, entity/edge/fact extraction, bi-temporal invalidation — the resolution-pass pattern the pipeline doc already cites.

**Measured (this research)**
- Cue-word scan of 8 real pi sessions (~537K chars assistant prose): counts of decision/fixed/should/shipped/will/decided per class; message-role distribution (63 user / 1,176 assistant / 1,419 toolResult in 5 agent-infra sessions); meta-discussion hit analysis; "should" deliberation dominance; near-absence of "we will"/"I will" in agent prose.
- Repo audit: `hosted_api.py:1745-1835` (regex amplifier — decision/claim patterns, no event class), `extractor.py:334-360` (`_POINTS_DOC_SYS`; `decision` = "a binding choice was made"; weak one-claim rule), `tests/extraction_eval` framework (gold windows, layer-correct rate target), `docs/drafts/2026-08-09-state-epistemic-separation.md` (extraction-active kinds).

---

## 7. Reliability ledger (honest summary)

| Claim | Reliability |
|---|---|
| Decision↔commissive, event/claim↔assertive mapping | **Reliable** (settled speech-act theory, 50 yrs) |
| Past-tense performative → decision | **Reliable** (theory + owner rule alignment) |
| "should" = weak commitment in real agent prose | **Reliable** (linguistic analysis + measured 44× deliberation) |
| Event/decision detection caps ~68-85% precision with cues-only | **Reliable** (AMI + DA benchmarks, replicated) |
| EDU/clause splitting is the right atomicity unit | **Reliable** (RST consensus) |
| Deterministic atomicity validator works (R8 Layer 1) | **Reliable by construction** (regex check, no LLM) |
| LLM zero-shot claim detection ≥0.90 F1 | **Research-grade — FALSE for most datasets** (32-85 F1 range) |
| Atomic-propositions decomposition improves cheap-model extraction | **Research-grade** (single 2026 study, but directionally sensible) |
| ≥0.90 layer-correct rate achievable on agent prose | **Research-grade estimate** — depends on the deterministic layer + eval design; must be measured, which R8 already mandates |
