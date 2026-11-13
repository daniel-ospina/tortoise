---
title: "The entity verdict — three bands, hard vetoes, and the D-1 guard"
type: engineering
domain: platform
doc_status: live
created: 2026-11-13
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph
aboutObjects: EXTRACTOR-V4-ARCHITECTURE.md, entity resolution (S3/D3), #12186, #12244, #11901, #2730
---

# The entity verdict — three bands, hard vetoes, and the D-1 guard (issue #12296)

> **What this is.** The record of the step that decides *what a noun mention means*
> against the candidates S3 supplied. It ships the vocabulary (`merge` / `review`
> / `new`), the deterministic vetoes that make a false merge expensive to reach,
> and — first, because nothing else is safe without it — the guard that keeps a
> merge-retired node out of the candidate set at all.

**Code:** `tortoise/entity_verdict.py` (the bands, stdlib only) ·
`extractor_v2._current_entity_priors` (the D-1 guard) ·
`extractor_v2._entity_verdict_facts` (the graph facts the vetoes read) ·
`extractor_v2.resolve_entities` (the wiring) ·
`entity_resolver._survivor_fixpoint` + `apply_entity_merge` (the closure guard).
**Tests:** `tests/test_12296_entity_verdict.py`.
**Rung taken for the external research:** 1 — `mcp__seo-intelligence__perplexity_search`
— **unavailable** (`mcp_catalog` does not list the server). Rung 2 — `web_search`
with `model="sonar"` named explicitly — **failed**: `PERPLEXITY_API_KEY not set`.
External work therefore ran on `web_fetch` against the primary **source trees**,
the same fallback rounds 1 and 3 of `#12186` used; every external claim below
names a file, not a blog.

---

## 1. ⛔ First, the defect (D-1), because a verdict layer on top of it makes it worse

**A completed merge is invisible to every retrieval reader.**

| hop | code | what it means |
|---|---|---|
| the merge writes no `status` | `entity_resolver.py:797-805` — the candidate's only props are `resolved_from`, `dedup_reviewed='merge'`, `reviewed` | the retired carrier stays a live node |
| the carrier keeps its `name` | `entity_resolver.py:778-796` — the SURVIVOR gains `aliases[]`; nothing rewrites the carrier's `name` | both rows carry the same spelling |
| the FTS index is `name`-only | `projection/__init__.py:6819` (`:Subject`), `:6823` (`:Object`) | a name query matches both |
| the retrieval filter is `status`-only | `search_engine.py:2973-2997` (`_STATUS_FILTERED_LABELS`, `:148`) — `resolved_from` count in `search_engine.py`, `ranking.py`, `session_link.py`, `assembly.py`: **0** | the carrier is search-visible and ranked |
| the capture path never consulted the resolver | `extractor_v2` S3 → `_fts_rows` → `sdk.tortoise_fts_query(..., include_terminal=True)` | and it opts *back into* the wider status view |
| the tie rule | rows land in **id-keyed dicts**, order DISCARDED (`extractor_v2.py:2913-2914`) | carrier and survivor **tie** on the name-only index |

⇒ A mention could bind to **the node the merge retired**, and `resolve_entities`
would report it as a resolution.

**The guard, and what it tests** (`extractor_v2._current_entity_priors`): after
the S3 union, every entity prior row is tested for `resolved_from` and **replaced
by its fixpoint** — the survivor's id, name and kind — so the verdict table holds
one candidate per ENTITY.

Three properties are load-bearing and each is pinned by a test:

* **Replacement, not deletion.** Dropping the row would turn a merged mention into
  a measured absence, and §4.4 I1 makes measured absence the one state that
  licenses a CREATE — the guard would trade a wrong binding for a duplicate.
* **The survivor is not status-filtered.** This leg deliberately includes terminal
  entities (`#3301`); a merge-retired carrier is a different question from a
  terminal incarnation, and a guard that conflated them would silently drop the
  supersession prior.
* **Cannot-answer is not an answer.** A read that fails after a projection
  resolved **raises** (`search_graph` counts it as a failed leg) rather than
  returning the pre-hop rows — the rule `follow_resolved_from` already states at
  `entity_resolver.py:562-572`. The only client skipped is one with no projection
  at all (a test double: no `resolved_from` state exists to withhold).

⚠️ **Residual, stated:** the guard runs on every entity prior row and costs one
read per label (plus one per hop round, normally zero). It does not change which
rows S3's `LIMIT` selected — a carrier that consumed a window slot still consumed
it. `#12244` owns the write-side attachment sites; this is the read/prior side and
its acceptance does not reach them.

---

## 2. The vocabulary: three bands, and `link` is deliberately not one of them

| band | meaning | disposition |
|---|---|---|
| `merge` | the mention IS this existing entity | binds (phase 1 exact, or a phase-2 proposal that survives every veto) |
| `review` | undecidable at this layer | **HELD** (`HOLD_VETOED`), never asserted new (§4.4 I1) |
| `new` | the run measured ABSENCE | the one state that licenses a create (`#11653`) |

⛔ **`link` is not a band.** Round 3 (`#12186` comment `6839898289`, Half-A.1)
found exactly one non-retiring pointer — `aliases[]` on the SURVIVOR
(`entity_resolver.py:784`, `:792`), read back at `:611` and
`entity_identity.py:843` — which leaves the other node live, search-visible and
still a dedup candidate. Two reasons it is not a verdict value here:

1. **A pointer is not a verdict.** `aliases[]` records *addressability* ("this
   spelling names that entity"). It carries no evidence, no score and no inverse:
   from an alias you cannot recover the variant that produced it.
2. ⛔ **Writing `dedup_reviewed='link'` is worse than inert — it silently
   DEQUEUES.** The write is one call (`sdk.py:51584` → `SET n += $props`), but the
   content review queue filters `dedup_reviewed IS NULL`
   (`sdk.py:24256-24257`), so any non-NULL value removes the row from review with
   **no verdict applied**. A `link` band that shipped only its writer would delete
   a review nobody counts.

A proposal that is neither provable identity nor measured absence therefore leaves
as `review` — the same disposition, minus the lost review.

> **OVERRIDES:** the ER/record-linkage default of persisting a `SAME_AS`-style
> link verdict as a first-class outcome — this tree has no non-retiring link
> record that any reader means (`_VALID_EDGE_PREDICATES` has no `sameAs`,
> `projection/edges.py:717-730`; the only pointer is `aliases[]`, which is
> addressability and not a verdict), so the band routes to review instead of
> inventing a writer whose value nothing reads.

---

## 3. The hard vetoes — precision is the expensive direction

A false merge retires a node other paths keep reading; a missed merge costs one
duplicate mention. Every veto can therefore only move `merge` → `review`; **none
can license `new`**, because a contradiction between two spellings says nothing
about whether the entity exists elsewhere. This is the field's own lesson, quoted
from `#12186` round 1's stage-B row: *"precision and recall need separate fixes,
not a shared threshold — precision needs hard rule-based vetoes"*
(arXiv:2607.26298 lesson 2).

| veto | tests | why it is safe |
|---|---|---|
| `identifier_conflict` | both sides carry identifier-shaped tokens, the sets are disjoint, **and the two names are otherwise identical** (`billing v2 ledger` / `billing v3 ledger`) | the skeleton equality (`entity_verdict.skeleton`) is what keeps it from firing on `basename-only` variants of a path — measured, `tortoise/sdk.py` → `sdk.py` fired without it. Separator/case folding means the `#8933` class (`ERR-4032` ≡ `err4032`) never fires |
| `date_conflict` | both sides carry date tokens, values are disjoint **for a shared kind**, names otherwise identical (`the Q3 plan` / `the Q4 plan`) | per-kind comparison: `Q3 plan` vs `2026 plan` is not a contradiction and those two spellings can name one entity |
| `kind_conflict` | both sides declare a SPECIFIC kind and they differ | `other`/empty is the write-time coalesce default (`projection/entities.py:5086`) and is treated as *unknown*, never as a value; the comparison is on the BARE kind (`extractor_v2._norm_kind`), so `core:person` vs `ops:person` is not a conflict — the namespace is an emission-time spelling |
| `target_suppressed` | the candidate carries a `reject` that named no survivor | `_pair_reviewed`'s documented case (`entity_resolver.py:236-250`): the reviewer answered, against an ambiguous set, and re-asking against one survivor is not a different question |
| `target_retired` | the candidate carries `resolved_from` | D-1 at the candidate-table layer — the belt to `_current_entity_priors`' braces, for any caller that built the table another way |
| `group_join` | the candidate is itself a merge survivor (`dependents >= 1`) | **M-4.** It stands for a group; binding a name-only mention to it asserts membership from a pairwise comparison never made against the group's other members |

⛔ **On the issue's "an explicit `DIFFERENT_FROM`" veto: that predicate does not
exist in this tree** (`grep -rn 'DIFFERENT_FROM' tortoise/ tests/` → **0 hits**;
`_VALID_EDGE_PREDICATES` has no such edge). The recorded analogue is the persisted
no-merge verdict — `dedup_reviewed='reject'` + `dedup_target_id` /
`dedup_rejected_targets` — and it is **pair-scoped between two NODES**. A
name-only mention has no node, so only the target-less form (`target_suppressed`)
is evaluable here; the targeted form is honoured by the merge verb
(`sdk.py:24861-24879`). Stated rather than approximated.

**Phase 1 keeps exactly one veto** (`target_retired`). Phase 1 binds only on an
exactly-equal folded name+kind, so no name/kind veto can fire by construction, and
`group_join` must NOT: an exactly-equal spelling is the strongest evidence the
tree has, and the merge queue's own rule lets an exact re-mention bind.

---

## 4. The closure guard (M-4) — both halves

*"One false-positive link can silently merge unrelated entities — assuming 'A
matches B' and 'B matches C' implies 'A matches C' lets a single bad link chain
hundreds of records together, so every cross-group merge must be actively
re-verified"* (arXiv:2607.26298 lesson 3; `#12186` round 1's M-4).

In this tree the chain is a `resolved_from` path, and `follow_resolved_from`
follows to a **fixpoint** (`entity_resolver.py:556-577`) — so a chain is silently
collapsed rather than reported, and nothing bounds it.

* **Negative half, at the verdict** (`entity_verdict.closure_vetoes`): a target
  that is itself retired, or a candidate that carries dependents, is refused →
  `review`.
* **Positive half, at the merge verb** (`entity_resolver._survivor_fixpoint`):
  `apply_entity_merge` hops the survivor to its fixpoint **before writing**, so no
  merge can create a chain of length ≥ 2. That makes "no chain longer than 1" an
  invariant instead of a hope — which is exactly what makes the verdict's
  `dependents >= 1` a reliable reading of "this target is a group". The hop is
  reported (`survivor_hopped`), not silent; `approve_merge` therefore returns the
  survivor it actually landed on.

---

## 5. External research — the source trees, not blogs

**Zep / Graphiti** ([`graphiti_core/utils/maintenance/dedup_helpers.py`](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/dedup_helpers.py),
[`node_operations.py`](https://github.com/getzep/graphiti/blob/main/graphiti_core/utils/maintenance/node_operations.py),
[`prompts/dedupe_nodes.py`](https://github.com/getzep/graphiti/blob/main/graphiti_core/prompts/dedupe_nodes.py)):

* **Four rungs, first-wins** — identity properties (`node_operations.py:850`) →
  exact-normalised (`:861`) → fuzzy/MinHash (Jaccard **0.9**, 32 permutations, band
  4, entropy gate `>= 1.5` and `len < 6 AND tokens < 2` refused) → **LLM**
  (`:872`). An ambiguity at the exact rung escalates to the LLM, **not** to fuzzy:
  a cheap rung that answers ends the question.
* **The adjudicator payload carries no topology** (`:703-712`):
  `{**attributes, candidate_id, name, entity_types, summary[:120]}`.
  `summary` is a **prompt input and is never embedded** (`nodes.py:506-509` embeds
  `name` alone) — that is where "context" belongs, and it is B1's work, not A3's.
* **The prompt shape** (`dedupe_nodes.py`): a SELECT over a supplied list, a
  `NodeDuplicate{id, name, duplicate_candidate_id}` with **`-1` = new**
  (`:26-40`), *"NEVER fabricate entity names or mark distinct entities as
  duplicates"* (`:59`), and *"EXACTLY N resolutions"* (`:154`).
  `NODE_DEDUP_CANDIDATE_LIMIT = 15`, `NODE_DEDUP_COSINE_MIN_SCORE = 0.6`
  (`node_operations.py:67-68`) — a window downstream of a **similarity floor**,
  never a raw top-k.

**Mem0** ([`mem0/memory/main.py`](https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py),
[`utils/scoring.py`](https://github.com/mem0ai/mem0/blob/main/mem0/utils/scoring.py)):
the entity boost is `similarity × ENTITY_BOOST_WEIGHT (0.5) ×
memory_count_weight`, the damping being `1/(1 + 0.001·(n−1)²)`
(`main.py:1815-1817`), combined **additively normalised** with a
`max_possible` denominator (`scoring.py:72`, `:97-101`) and with the semantic
threshold gating **before** combining (`scoring.py:111-112`). Its candidate pool is
**semantic-only** (`main.py:1680`), which is a recall regression for our measured
class — the signal is applicable, the pool is not.

**What this settles for us:** the band layer is a *verdict over supplied
candidates*, and no surveyed system puts topology in the adjudicator. Adding
`title`/`object_kind`/neighbour names there is an **extension beyond the surveyed
field** (defensible from ER practice, not copied from Zep) — deliberately **not**
done here, because we have no measurement that would justify the prompt bytes.

---

## 6. Contradiction test — one row per finding

| finding | decision it would reverse | verdict |
|---|---|---|
| a merge-retired carrier is still a retrieval candidate (D-1) | **no recorded decision reaches this work** — a completeness defect in the merge's read side | **defect, guarded** |
| `aliases[]` is the only non-retiring pointer; pointer ≠ verdict | none; it informs the `#2730` owner question without answering it | decision recorded above (`OVERRIDES` line) |
| `dedup_reviewed='link'` dequeues | none | code statement |
| hard vetoes are precision-only and never license `new` | obeys §4.4 I1 | **obeyed** |
| closure guard hops the survivor to its fixpoint | none — `follow_resolved_from` already walks to a fixpoint; this only stops the verb CREATING a chain | code statement |
| `group_join` refuses a merged-target binding | none — it routes to `review`, which is what the band vocabulary is for | band |
| Zep's adjudicator is topology-blind; `summary` is never embedded | none — it narrows A3 to B1 and adds no prompt bytes | third-party statement |

**Checked explicitly and NOT reversed:** V1 (`SEARCH-ARCHITECTURE.md` 1–4),
§12.2/§12.2c, §14.3 V3 + the `#11395` KEEP ruling (`6781921075`), §2.0, the
no-eviction/projection-window rule, §7 + §17.4.6 (the read ledger is not an
admission-control signal), and `#11901`'s conclusion (the window is untouched —
this change alters no window, no index, no vector, and reads no ledger).

---

## 7. The falsifier — NOT RUN, and what exactly is missing

The issue's falsifier: *"Label a small gold set from the `#11901` corpus. If merge
precision is not materially better than 'always merge the top candidate', the bands
add cost without signal — report and stop."*

**It is not run, and no number is claimed.** Labelling requires a human (or an
independent judge) on sampled real names, and this box serves no tenant: the
`#11901` harvest needs a sessions corpus, and the harness is the same one that made
rounds 1–3 declare `title` fill, chain lengths, cluster sizes and D-1's magnitude
**unmeasured**. A self-labelled set produced by the model that also proposes would
measure nothing.

**The half that IS runnable, and is run** — the direction that would do harm first:
**no veto fires on the variant classes `#11901` measured** (the merges we *want*),
using the instrument's own generator (`tools/candidate_recall_11901.py::variants`)
rather than a fixture written to pass. Pinned by
`tests/test_12296_entity_verdict.py::TestVetoesAreSilentOnTheGenuineVariantCorpus`
— ten names across `article-drop`, `space-to-hyphen`, `hyphen-to-space`,
`basename-only` and `hash-dropped`, **zero vetoes**. That measurement is why the
identifier/date vetoes require the token to be the ONLY difference: the first cut
fired on `basename-only` and was corrected against this corpus.

**What would close it:** a labelled `{mention, candidate, label}` set over the
`#11901` harvest, then `entity_verdict.judge` scored against the
"always-merge-top-candidate" baseline (precision + recall, per band). Until that
exists the honest statement is that the vetoes bound the damage a wrong proposal
can do, and the band vocabulary makes the outcome readable — not that precision is
better than the baseline.

---

## 8. What this deliberately does not do

* **No candidate generation.** The window, the blocking keys, the union of
  retrieval legs — untouched (`#11901` measured and left the window alone).
* **No prompt enrichment.** `title`/`object_kind`/neighbours are not added to the
  adjudicator payload (§5): an extension with no measurement behind it.
* **No new verdict value on the graph.** Nothing writes `dedup_reviewed` here; a
  vetoed name is HELD, and `HOLD_VETOED` joins the hold vocabulary
  (`extractor_v2.HOLD_REASONS`) so a run's census can tell "the resolver did not
  answer" from "the answers contradicted".
* **No write-side attachment change.** `#12244` owns those three sites.
