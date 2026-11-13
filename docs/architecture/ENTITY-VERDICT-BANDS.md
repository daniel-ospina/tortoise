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
| the merge writes no `status` | `entity_resolver.apply_entity_merge` — the candidate's only props are `resolved_from`, `dedup_reviewed='merge'`, `reviewed` (~`:909-913`) | the retired carrier stays a live node |
| the carrier keeps its `name` | `entity_resolver.apply_entity_merge` — the SURVIVOR gains `aliases[]` (~`:890-901`); nothing rewrites the carrier's `name` | both rows carry the same spelling |
| the FTS index is `name`-only | `_FULLTEXT_INDEX_LABEL_FIELDS`'s `("Subject", ["name"])` / `("Object", ["name"])` | a name query matches both |
| the retrieval filter is `status`-only | `search_engine.py:2973-2997` (`_STATUS_FILTERED_LABELS`, `:148`) — `resolved_from` count in `search_engine.py`, `ranking.py`, `session_link.py`, `assembly.py`: **0** | the carrier is search-visible and ranked |
| the capture path never consulted the resolver | `extractor_v2` S3 → `_fts_rows` → `sdk.tortoise_fts_query(..., include_terminal=True)` | and it opts *back into* the wider status view |
| the tie rule | rows land in **id-keyed dicts**, order DISCARDED — `search_graph`'s id-keyed accumulator (`results[bucket[…]][rid] = row`; the comment in `_fts_rows` says the same) | carrier and survivor **tie** on the name-only index |

⇒ A mention could bind to **the node the merge retired**, and `resolve_entities`
would report it as a resolution.

**The guard, and what it tests** (`extractor_v2._current_entity_priors`): after
the S3 union, every entity prior row is tested for `resolved_from` and **replaced
by its fixpoint** — the survivor's id, name and kind, **plus the spellings it
answers to** (`aliases[]`, so the spelling that matched the carrier still resolves
here; a row without them turns a re-mention of a merged variant into a measured
absence, `found: False · no match — created`) — so the verdict table holds one
candidate per ENTITY.

Four properties are load-bearing and each is pinned by a test:

* **Replacement, not deletion.** Dropping the row would turn a merged mention into
  a measured absence, and §4.4 I1 makes measured absence the one state that
  licenses a CREATE — the guard would trade a wrong binding for a duplicate.
* **Replacement carries the spelling, not only the id.** `_find_existing_entity`
  matches `name` first and now the row's `aliases[]` too (mode `alias`, compared
  with the tree's own `normalize_entity_name` fold) — otherwise a mention spelled
  the way the merged variant was spelled finds nothing in phase 1 and is recorded
  as a measured absence, which is the same defect one field over. Pinned by
  `test_a_re_mention_of_the_merged_variant_resolves_through_the_alias`. Every
  row that folds onto one id UNIONs its spellings into that id's row, because
  which row reaches the fold first is FTS order and the spelling is what resolves:
  with a first-wins dedupe, the same graph answered the same mention as a merge in
  one row order and as a measured absence (a duplicate) in the other. Pinned by
  `test_the_folded_row_s_aliases_do_not_depend_on_row_order`, and a bare-string
  `aliases` is normalised, not dropped, like its two sibling readers
  (`test_a_bare_string_stored_alias_is_normalised_not_dropped`).
* **The survivor is not status-filtered.** This leg deliberately includes terminal
  entities (`#3301`); a merge-retired carrier is a different question from a
  terminal incarnation, and a guard that conflated them would silently drop the
  supersession prior.
* **Cannot-answer is not an answer — and not an abort, and not `degraded`.**
  A read that fails after a projection resolved never returns the pre-hop rows
  (that is the binding the guard exists to prevent) and never escapes
  `search_graph` either: its one caller (`extract_session_v2` — `search_graph`
  has no other call site) holds
  no `try`, so a raise would abort a capture. It is graded **PARTIAL** (§4.4 G3 /
  `#5095`), not `degraded` — `degraded` means the search never got through its
  first query and `_render_search_results` acts on it by withholding every prior
  and telling the gap reviewer the lookup did not run, whereas here the queries
  ran and their point/event priors are real. So the fold failure keeps every row
  it did resolve, impairs the entity legs in the counters (one `ran` down and one
  `failed` up, so `ran + failed <= derived` still holds and the S4 note NAMES the
  impaired leg instead of blaming the query budget), sets a reason naming the
  fold, and drops the entity legs from `query_results.ran` so
  an entity candidate whose fixpoint was not established is recorded
  `found: None`, never `found: false`. Pinned by
  `test_a_fold_failure_is_PARTIAL_and_keeps_the_rows_it_did_resolve`. ⚠️ **The
  other arm of the same `except` is narrower, and is stated rather than implied:
  a read that fails BEFORE any hop resolves** (a query error, not a per-row
  unresolvable) **has no `folded` rows to carry, so `entities` comes back EMPTY**
  — `search_graph` withholds every entity prior, `extract_session_v2` therefore
  skips D3 for that run entirely (the pre-`#12296` behaviour: no resolution, plus
  this reason and the impaired-leg counters), and no pre-hop row is emitted by
  either arm. The only
  client skipped is one with no projection at all (a test double: no
  `resolved_from` state exists to withhold).
* **A chain the bound cannot resolve is cannot-answer, not a carrier.**
  `_PRIOR_HOP_BOUND` (8) exists because the walk is batched;
  `apply_entity_merge` cannot create a chain ≥ 2, so a longer one is a
  corrupt/legacy record. Hitting the bound on a still-retired node raises
  `_PriorFoldUnresolvable` — carrying the rows already folded — instead of
  emitting the retired row, which would be the guard's own failure with no
  marker. The failure is per-ROW: one unresolvable row must not void the prior
  set the fold could answer.

⚠️ **Residual, stated:** the guard runs on every entity prior row and costs one
read per label (plus one per hop round, normally zero), and the verdict's facts
reader adds a REVERSE read per label ("which nodes were folded into these ids?").
That reverse read is now index-backed — `resolved_from` was added to
`_RANGE_INDEX_LABEL_PROPS` and to `_REQUIRED_RANGE_INDEXES` for both identity
labels. ⚠️ **What carries that decision is a STRUCTURAL fact, not a figure:** an
un-indexed reverse-edge lookup over an identity label is a full label scan by
construction, on every capture, twice per run; the local spot measurement that
first surfaced it was never recorded as an artifact, so **no milliseconds are
claimed here** (the earlier `6.17 ms / 0.95 ms` figures were unpinned and are
withdrawn — a number with no probe behind it cannot justify a REQUIRED index that
sends every pre-existing graph through the one-time bootstrap sweep). It does not change which rows S3's
`LIMIT` selected — a carrier that consumed a window slot still consumed it.
`#12244` owns the write-side attachment sites; this is the read/prior side and its
acceptance does not reach them.

---

## 2. The vocabulary: three bands, and `link` is deliberately not one of them

| band | meaning | disposition |
|---|---|---|
| `merge` | the mention IS this existing entity | binds (phase 1 exact, or a phase-2 proposal that survives every veto) |
| `review` | undecidable at this layer | **HELD** (`HOLD_VETOED`), never asserted new (§4.4 I1) |
| `new` | the run measured ABSENCE | the one state that licenses a create (`#11653`). ⛔ `judge` never returns it — it takes a (mention, candidate) PAIR and cannot answer absence; the create side is `resolve_entities`'s `new` population |

⛔ **`link` is not a band.** Round 3 (`#12186` comment `6839898289`, Half-A.1)
found exactly one non-retiring pointer — `aliases[]` on the SURVIVOR
(`entity_resolver.apply_entity_merge`'s alias write, read back by
`resolve_entity_id`'s `$k IN n.aliases` and by `entity_identity`) — which leaves
the other node live, search-visible and
still a dedup candidate. Two reasons it is not a verdict value here:

1. **A pointer is not a verdict.** `aliases[]` records *addressability* ("this
   spelling names that entity"). It carries no evidence, no score and no inverse:
   from an alias you cannot recover the variant that produced it.
2. ⛔ **Writing `dedup_reviewed='link'` is worse than inert — it silently
   DEQUEUES.** The write is one call (`sdk.update_entity` → `_update_entity`'s `SET n
   += $props`), but the content review queue filters `dedup_reviewed IS NULL`
   (`sdk.py:24507`), so any non-NULL value removes the row from review with
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
| `identifier_conflict` | both sides carry identifier-shaped tokens, the sets are disjoint, **and the two names are otherwise identical** (`billing v2 ledger` / `billing v3 ledger`) — "identical" after the tree's own fold, so a leading article (`the billing v3 ledger`) does not defeat it | a **path is scanned by component** (`entity_verdict._scan`): the directory parts are literals and the basename is tokenised like any name, so two spellings of ONE file stay silent (`tortoise/extractor_v2.py` → `extractor_v2.py`) while two different files still veto (`tortoise/extractor_v2.py` vs `.../extractor_v3.py`). Dropping a path token wholesale satisfied the first and lost the second — which a silence-only corpus test cannot tell apart. Separator/case folding means the `#8933` class (`ERR-4032` ≡ `err4032`) never fires. A `#`-prefixed reference (`#11901`) is an identifier: it is the corpus's dominant spelling and the hash form is not a bare number, so `#11901 plan` / `#11905 plan` veto |
| `date_conflict` | both sides carry date tokens, values are disjoint **for a shared kind**, names otherwise identical (`the Q3 plan` / `the Q4 plan`) | per-kind comparison: `Q3 plan` vs `2026 plan` is not a contradiction and those two spellings can name one entity. Temporal compounds (`YYYY-MM`, `YYYY-MM-DD`, `YYYY-Qn`, `Qn-YYYY`) are their own kinds and are excluded from the identifier class, so a date disagreement is reported as a DATE conflict — the rule that fired — rather than under `identifier_conflict` |
| `kind_conflict` | both sides declare a SPECIFIC kind and they differ | `other`/empty is the write-time coalesce default (`projection/entities.py:5086`) and is treated as *unknown*, never as a value; so is `unclassified`, the #1695 classify-later SENTINEL the S2 prompt instructs the model to emit and `execute_embed` repairs later — treated as a declared kind it conflicts with every real kind and vetoes every merge in classify-later mode. The comparison is on the BARE kind (`extractor_v2._norm_kind`), so `core:person` vs `ops:person` is not a conflict — the namespace is an emission-time spelling |
| `target_suppressed` | the candidate carries a `reject` that named no survivor | `_pair_reviewed`'s documented case (`entity_resolver.py:236-250`): the reviewer answered, against an ambiguous set, and re-asking against one survivor is not a different question |
| `target_retired` | the candidate carries `resolved_from` | D-1 at the candidate-table layer — the belt to `_current_entity_priors`' braces, for any caller that built the table another way |
| `group_join` | the candidate is itself a merge survivor (`dependents >= 1`) | **M-4.** It stands for a group; binding a name-only mention to it asserts membership from a pairwise comparison never made against the group's other members |

⛔ **`group_join` stands down when the alias already proves the spelling**
(`facts["mention_is_alias"]`). `apply_entity_merge` writes the candidate's folded
spelling into the survivor's `aliases[]`, and the by-name resolver reads
`$k IN n.aliases` **as a resolution** (`entity_resolver.py:611`) — so that
spelling's membership is *recorded*, not inferred, and no group-level comparison
is being skipped. Without the escape, every later re-mention of an already-folded
variant is refused **on every run for the lifetime of the merge**, and an entity
hold is **not carried forward** (`extractor_v2.execute_embed`, the `"ENTITY hold(s) are NOT carried forward"` branch of `execute_embed`), so the mention is
dropped rather than queued. Pinned by
`test_a_repeat_mention_of_a_folded_alias_merges_instead_of_being_lost`.

The fuller M-4 reading — compare the mention against the group's **members**, not
only the survivor — remains available and is the next widening; the alias case is
the provable subset and the one that recurs.

⛔ **On the issue's "an explicit `DIFFERENT_FROM`" veto: that predicate does not
exist in this tree** (`grep -rn 'DIFFERENT_FROM' tortoise/ tests/` → **0 hits**;
`_VALID_EDGE_PREDICATES` has no such edge). The recorded analogue is the persisted
no-merge verdict — `dedup_reviewed='reject'` + `dedup_target_id` /
`dedup_rejected_targets` — and it is **pair-scoped between two NODES**. A
name-only mention has no node, so only the target-less form (`target_suppressed`)
is evaluable here; the targeted form is honoured by the merge verb
(`sdk.py:24861-24879`). Stated rather than approximated.

**Phase 1 applies `target_retired` — and, on the alias path, the name/kind vetoes
too.** It binds on an exactly-equal folded name+kind *or* on a spelling the
survivor's `aliases[]` already records (`mode: "alias"`), and on that second path
the two spellings differ by more than separators, so `identifier_conflict` /
`date_conflict` / `kind_conflict` are reachable there (the warning names the codes
that actually fired, never a retirement that did not happen). `group_join` is
deliberately NOT applied in phase 1: an exactly-equal spelling, or one the merge
recorded as an alias, is the strongest evidence the tree has, and the merge
queue's own rule lets an exact re-mention bind. `target_suppressed` is NOT applied
there either, and the asymmetry is stated rather than left to inference: a
`reject` that named no survivor was recorded against an ambiguous SET, not against
this spelling, so an exact/alias re-mention is not re-asking it — and because an
entity hold is not carried forward, refusing there would drop every re-mention of
that candidate for the life of the rejection. The persisted rejection remains
honoured where it was made: the merge queue (`entity_resolver._pair_reviewed`)
never re-offers it, and a phase-2 PROPOSAL to bind it is refused
(`target_suppressed`).

Name hits outrank alias hits, and an alias tie is `ambiguous` — the reverse (one
undifferentiated "matched" answer plus `[0]`) made the answer depend on candidate
ORDER: a row that matched by NAME could lose to one that merely carried the
spelling as an alias, and two rows claiming the same alias resolved silently to
whichever the ranking put first, with the claim rewritten onto it and recorded
`band: merge`.

`facts` reaches both phases through `_verdict_facts`, which adds
`mention_is_alias` from the candidate's `aliases[]` and the mention's own
`normalize_entity_name` fold — and folds the STORED alias too, because `aliases`
is writable by any caller and a stored `"The Team"` must not be a spelling
nothing answers.

---

## 4. The closure guard (M-4) — both halves

*"One false-positive link can silently merge unrelated entities — assuming 'A
matches B' and 'B matches C' implies 'A matches C' lets a single bad link chain
hundreds of records together, so every cross-group merge must be actively
re-verified"* (arXiv:2607.26298 lesson 3; `#12186` round 1's M-4).

In this tree the chain is a `resolved_from` path, and `follow_resolved_from`
follows to a **fixpoint** (`entity_resolver.py:556-577`) — so a chain is silently
collapsed rather than reported, and nothing bounds it.

* **Negative half, at the verdict** (`entity_verdict.hard_vetoes` over the facts
  `extractor_v2._entity_verdict_facts` reads — the ONE implementation on the
  path): a candidate that carries `resolved_from` (`target_retired`) or that is
  itself a merge survivor (`group_join`) is refused → `review`. A second,
  mention-blind `closure_vetoes` helper was deleted rather than left as a
  divergent twin: it could not see the mention, so it could not honour the alias
  escape, and wiring it in would have refused exactly the case that escape fixes.
* **Positive half, at the merge verb** (`entity_resolver.apply_entity_merge`), and it
  takes BOTH legs. The survivor is hopped to its fixpoint before writing **and**
  the candidate side is closed: a candidate that is already retired is refused
  (its entity is the fixpoint), the candidate's DEPENDENTS are re-pointed at the
  final survivor in the same merge (path-shortening), and the candidate's
  `aliases[]` are unioned into the survivor's. Hopping only the survivor is not
  enough — a group survivor is a perfectly eligible CANDIDATE (`scan_entities`
  keeps live nodes), so `N0 → N1` then `N1 → N2` wrote `N0 → N1 → N2` (measured),
  which both broke the invariant below and stranded `N0`'s spellings on the
  retired `N1`. The hop is reported (`survivor_hopped`) and so is the re-point
  (`dependents_repointed`), not silent; `approve_merge` therefore returns the
  survivor it actually landed on. Pinned by
  `test_a_second_group_merge_repoints_the_members_and_keeps_their_spellings` and
  `test_a_merge_of_an_already_retired_candidate_is_refused`.

⚠️ **Residual, stated:** a `resolved_from` whose target no longer exists
(`delete_entity` leaves the pointer behind) makes the guard's walk hop onto a node
it cannot read. That row is graded cannot-answer (PARTIAL, per `#1` above) rather
than re-emitted as the pre-hop carrier; the row is then absent from the candidate
set for that run, which is a duplicate risk the record names rather than hides.
Pinned by `test_a_dangling_resolved_from_is_cannot_answer_not_a_pre_hop_row`.

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
admission-control signal), and `#11901`'s conclusion. ⚠️ **What the change DOES
do to the index path, stated rather than certified away:** it adds ONE range index
per identity label — `resolved_from` on `Object` and on `Subject` — inside the
graph projection, and declares both REQUIRED
(`projection/__init__.py::_RANGE_INDEX_LABEL_PROPS`, `::_REQUIRED_RANGE_INDEXES`),
so a pre-`#12296` graph runs the one-time bootstrap sweep instead of reading
current forever (the `#8400` trap). It MOVES no index and adds no new index
LOCATION — this is an index on an existing label in the existing projection, so
V1's ruling is not engaged — and it adds no vector, alters no window, and reads
no ledger.

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
— eleven names across `article-drop`, `space-to-hyphen`, `hyphen-to-space`,
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
