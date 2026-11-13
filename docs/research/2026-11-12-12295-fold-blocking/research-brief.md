# #12295 — the comparison-side key, multi-key blocking, and pair completeness on `#11901`'s corpus

**Lane:** `fix/12295-fold-blocking` · **Artifact:** `measurement.json` in this directory ·
**Harness:** `tools/candidate_recall_11901.py` (**reused, not rebuilt** — the tool and its pinned
test landed with `#12159`) · **Run:** `--population 1000 --sample 300 --captures 20`, elapsed
390.6 s, graph `recall11901_1794532634_746264`, population digest `03785295abd20d9a`,
measured at code head `437d5d17a5f5` — the commit whose code these numbers describe. ⚠️ The run
itself happened at `e9a572d4b4`, which the `#12304` rebase rewrote out of every ref (a dangling
local object, absent from a fresh clone); `437d5d17a5f5` is that same commit rebased, and
`git diff e9a572d4b4 437d5d17a5f5 -- tortoise/entity_resolver.py tortoise/entity_blocking.py
tortoise/extractor_v2.py` is EMPTY. Commits after it touch those files in two ways, and the
provenance claim is stated as the diff rather than as a summary of it: (a) DOCSTRINGS/COMMENTS —
`07033dd926cc` moves the `emitted:` note and `3e75c65c71a8` adds the `complete=True` note;
(b) LABELS — `ebf7b13fc3cd` renames the `lsh_bands` docstring label and the `stats()` counter key
`keys` -> `key_memberships`, and `f7c0e819f4` adds a `candidates(label=None)` LABEL FALLBACK in
`entity_blocking.py`. Apart from that counter-key RENAME, the fallback is the only statement change in those
files after `437d5d17a5f5`, and this run never reaches it: the harness calls
`index.candidates(surface, label="Object")` (`tools/candidate_recall_11901.py:717`), so the
`label is None` branch is not taken. No statement in `entity_resolver.py` or `extractor_v2.py`
moved after `437d5d17a5f5`.

**Search rung, stated because it was taken twice and failed twice.**
`mcp__seo-intelligence__perplexity_search` → `Unknown MCP server 'seo-intelligence'` (rung 1
unavailable on this box). Fell to rung 2, `web_search` with `model="sonar"` named explicitly →
`PERPLEXITY_API_KEY not set. Set it via PERPLEXITY_API_KEY env var, AGENT_MCP_ENV_PATH … or
$AGENT_INFRA_PATH/../.env`. External work therefore ran on **rung 3, `web_fetch` against primary
sources**: `getzep/graphiti` `dedup_helpers.py` at `main`, and `arXiv:1905.06167` for the blocking /
pair-completeness framing. **2 queries attempted, 0 returned.** Rounds 1 and 2 of `#12186` hit the
same two failures, so this is a box condition, not a lane mistake.

---

## 1. What was inspected before anything was built

The stored folded key **already exists**. This is the finding that decided the shape of the change,
and it is the reason no new field, no new fold and no second source of truth appear below:

| mechanism | where | what it does |
|---|---|---|
| `normalize_entity_name` | `entity_resolver.py:156` | NFKC → casefold → drop a leading **article** → non-word run → space → whitespace collapse |
| write doors that persist it as `canonical_name` | `sdk.py:45886` (the `_emit` funnel), `api.py:2628` (`add_subject`), `api.py:2671` (`add_object`), `sdk.py:47229` (a real rename) | filled at every door, `setdefault`, so a caller-supplied fold wins |
| the projection | `projection/entities.py:5094` (ON CREATE, objects) / `:4958` (subjects), coalesce fill at `:5101` / `:4975` | `canonical_name` is written ON CREATE only, so a re-mention cannot rewrite the key that resolved it |
| identity | `projection/entities.py:4907` (`merge_key = "id"`) | `name` is a mutable natural key; the id is the identity (`#3584`/`#3590`) |

So item 1 of the issue — *"a comparison-side normalised key … never rewrite `name`"* — is **built**,
as of `#12047`. What did not exist, and is what this lane adds:

| gap | site | measured consequence |
|---|---|---|
| the capture prior compared `_norm` only — whitespace + lowercase, byte-identical to Zep's `_normalize_string_exact` | `extractor_v2._norm` at `:4232`, the historical arm in `_find_existing_entity` (`:5769`), which now delegates to `_find_by_folded_key` (`:5823`, the folded rung this lane added) | **0 of 422** variant pairs attach at any `k`, both modes — the before-half of every rate in §3 |
| the dedupe scan blocks on ONE key — `folded[:3]` | `entity_resolver.py:394` **on `origin/main`** (this change moves it) | a transposition (`the project alpha` / `alpha project`) is never compared, at any similarity |

External confirmation at source (rung 3): Zep's `_normalize_string_exact` is *"Lowercase text and
collapse whitespace so equal names map to the same key"* — i.e. **we were already at Zep's exact
rung, and the missing piece is the fuzzy/blocking one**, which Zep builds with
`_normalize_name_for_fuzzy`, 3-gram `_shingles`, 32 MinHash permutations, `_MINHASH_BAND_SIZE = 4`,
and a candidate set that is the **union of LSH band buckets**. Its entropy gate
(`_NAME_ENTROPY_THRESHOLD = 1.5`, `_MIN_NAME_LENGTH = 6`, `_MIN_TOKEN_COUNT = 2`) guards the fuzzy
path only — verbatim from the module: *"Exact normalized-name matching runs first for* all *names
regardless of length or entropy. The entropy gate only guards the fuzzy (MinHash/LSH) path"*. Its
`_hash_shingle` uses `blake2b`, never Python's salted `hash()`; ours does the same.

---

## 2. The corpus, and one comparability limit stated up front

18 harvested session files (`~/.tortoise/session-events`; `#11901` ran on **9**), population 1000,
stratified sample 300 (definite 180 / ident 90 / path 30), **hybrid and uniform** — 4388 windows,
`keyword_only: 0`, derived through `search_engine.require_hybrid_read`, not asserted.

⚠️ **The two artifacts are not comparable on absolute in-window rates**, and this brief does not
compare them. On this corpus the FTS window contains the canonical for **409/422 = 96.9 %** of pairs
at `k=3`; `#11901`'s hybrid artifact holds **260/419 = 62.1 %**. The `exact` arm moves the same way
(definite class 179/180 here vs 113/180 there). The harvest input differs (18 vs 9 files; the top-1000
by frequency is a different population), and **the mechanism of the difference is not diagnosed
here** — it is declared, not explained. It is also why the before/after below is measured **in one
run, on one corpus**, with the pre-`#12295` matcher frozen inside the harness rather than read back
from the old artifact: a before/after against a moving before is not a measurement.

---

## 3. The three numbers the issue asks for

### 3.1 Pair completeness — before (the shipped window) and after (multi-key blocking)

Pair completeness = the fraction of true duplicate pairs landing in at least one block
(`arXiv:1905.06167`; the ER field's own floor for "scoring is not capped before it starts" is 0.95).
**Cost is quoted per MENTION**, because that is the only column in which the two generators are the
same quantity: `k` rows per mention for the window, one candidate list per mention for the blocker.

| generator | candidates per mention | **pair completeness** |
|---|---|---|
| **FTS window, k=3** (production) | 3 | **0.9692** (409/422) |
| FTS window, k=5 | 5 | 0.9953 |
| FTS window, k=10 | 10 | 0.9976 |
| FTS window, k=15 | 15 | **1.0000** |
| FTS candidate set a capture actually sees (`search_graph` pooled union) | 2.84 (42.65 per capture of 15 mentions — **not** a per-mention figure, and not comparable to the row below) | not measured per mention |
| **multi-key blocking** (`entity_blocking.BlockIndex`: folded key ∪ folded prefix ∪ per-token prefixes ∪ LSH shingle bands) | **34.9** (max 152) | **1.0000** (422/422), reduction ratio 0.0255 |

The reduction ratio is the blocker's actual cost on the whole graph: pairs proposed by **all 1000**
names over `C(1000, 2)` (`entity_blocking.reduction_ratio`, computed by that one helper). It is NOT
the pairs proposed by the 300-row variant sample — mixing the two published a ratio ~3× cheaper
than the quantity its own definition names (`#12295` round-2 B3, fixed before this run).

### 3.2 The falsifier, answered — and it fires against the BLOCKER

> *If pair completeness is already ≥ 0.99 on the measured corpus, the loss is not here — stop and
> report the number.*

**At the production window pair completeness is 0.9692 — below 0.99, so the falsifier does not fire
on its own terms, and the work proceeded.** But the number says something sharper, and reporting it
is the point of having a falsifier:

- the shipped window **already clears the 0.95 floor at every `k`**, and reaches **1.0000 at `k=15`
  with 15 candidates per mention** — **less than half the multi-key blocker's 34.9**;
- the multi-key blocker reaches the same completeness at roughly **2.3× the cost**.

⇒ **Multi-key blocking is NOT earned for the capture prior on this corpus — on COST, not on
completeness.** ⚠️ The plan's falsifier **F1** is worded *"pair completeness does not rise above the
FTS-only blocker"*, and **read literally F1 does NOT fire**: completeness does rise at `k=3`, `k=5`
and `k=10` (0.9692 → 1.0). It ties only at `k=15`, and it gets there for **2.3× the cost**. So the
honest statement of what fired is the falsifier's *purpose* — **the blocker's spend is not
justified by a gain in reach** — and it is stated as that, not as a quote of a criterion that a
literal reading does not satisfy. And the width is not the reason to widen either: see 3.3 —
attaching is flat in `k` (207 at `k=3`, 212 at `k=15`), so a wider window buys completeness that the
matcher cannot spend.

**Where the blocker IS load-bearing is a different surface: the dedupe scan** (`entity_candidates`),
which has no FTS window at all and blocked on ONE 3-character prefix. There the union is the only
candidate mechanism, and the pairs it adds (transpositions, typos, token-disjoint spellings) cannot
be reached at any `k` — pinned by
`test_the_scan_finds_a_transposition_the_single_prefix_key_could_not`, which reds when the union is
reverted. ⚠️ **That corpus contains no token-disjoint pair by construction**, so the scan's gain is
pinned structurally and is **not** quantified here — declared, not claimed.

### 3.3 The duplicate rate, and how many of the pairs move

At `k=3`, on the same 422 pairs, one run:

| configuration | attached | minted a duplicate | rate |
|---|---|---|---|
| prior **off** (no candidate set at all — a definition, not a measurement) | 0 | 422 | **1.0000** |
| prior **on**, pre-`#12295` matcher (`_norm` equality — the frozen control) | **0** | 422 | **1.0000** |
| prior **on**, shipped matcher (with the folded rung) | **207** | 215 | **0.5095** |

**207 of 422 pairs move (49.1 %)**; the duplicate rate halves. Per class at `k=3`:

| class | pairs | in window | reachable by the multi-key blocker | **attached** |
|---|---|---|---|---|
| article-drop | 180 | 175 | **180** | **175** |
| hyphen-to-space | 21 | 21 | 21 | **21** |
| hash-dropped | 11 | 11 | 11 | **11** |
| space-to-hyphen | 180 | 179 | **180** | **0** |
| basename-only | 30 | 23 | **30** | **0** |

⚠️ **The same run holds a higher number and it must be read beside 207, not instead of it:** the
harness's `attached_folded` arm — a fold that scrubs `[-_./]` only and does **NOT** drop a leading
article — binds **386 of 422 (91.5 %)**. The 179-pair gap between 386 and 207 is exactly the
`space-to-hyphen` class, i.e. the article anchor (§4). 207 is what the *stored* key achieves without
reversing a decision; 386 is the ceiling if that decision is reopened. Neither is "the fold works" on
its own, which is why both are in the artifact.

## 4. The residual is exactly two declared classes, and one of them is a decision

215 pairs remain unattached. **Every one of them is attributed** (the pinned test
`test_the_residual_is_the_two_named_classes_and_nothing_unexplained` fails on an undeclared class):

**`space-to-hyphen` — 180 pairs, 42.7 % of the class, 180/180 reachable by the blocker, 0 attach.**
Mechanism, in one line: `normalize_entity_name` anchors the article strip on a real whitespace
boundary **before** it folds punctuation (`#12047` round-4 finding 5 — the fix that stopped `a-b`
folding to `b`), so `the-plan-doc` folds to `the plan doc` while the stored `the plan doc` folds to
`plan doc`. The two never meet. **That decision is NOT reversed here** — this lane pins it from the
outside (`test_the_article_anchor_decision_is_not_reversed`) and reports its measured cost instead.
The artifact's own per-`k` rows say something sharper than "a ceiling". At the **k=3 production
window** 179 of 180 are IN the window, so exactly one is out of reach at that setting; at
**k=5, k=10 and k=15 all 180 are in the window** (`arms.variant_by_class["space-to-hyphen"][k]["in_window"]`
= 180 at each of those `k`) and **still 0 attach**. Width therefore cannot recover them — which makes the diagnosis
*stronger*, not weaker: the article anchor alone is the cause, and no `k` budget fixes it. Recovering
them would take the
lane from **207/422 = 49.1 %** to **386/422 = 91.5 %**, which is exactly the artifact's
`attached_folded` = 386 (91.5 %). That is a **reopen of a recorded finding**, not a candidate for
quiet adoption, so it is filed as evidence (see §7).

**`basename-only` — 30 pairs, 30/30 blocked, 0 attach.** Deliberate: a basename is non-injective
(`lib/config.py` and `server/config.py` share one), so `entity_blocking.attach_matches` attaches on it
only when the caller's candidate list **is the complete live population** and exactly one carrier
holds the key. A capture window is not the population — one hit in three rows proves nothing — so at
capture time a basename is a **candidate for the adjudicator**, never an auto-attach. Attaching it
would be the false merge `#2730` §6.7 records as invisible and hard to undo.

**`article-drop` — 5 pairs**: not in the `k=3` window at all. This is the pre-existing
retrieval-miss class `#11901` owns; the folded rung cannot reach what the window did not return.

---

## 5. Contradiction test — the recorded decisions, one line each

**No recorded decision is reversed.** Checked before adopting anything, per the epic's rule:

| recorded decision | status here |
|---|---|
| **KEEP ruling** (`#11395` `6781921075`) — the `:Object`/`:Subject` embeddings stay | **untouched** — nothing is embedded, added or removed |
| `§12.2` / `§12.2c` (name+context behind the extractor) | **not reached** — no field added, no vector changed |
| **V1**, `SEARCH-ARCHITECTURE.md` 1–4 — the text index stays in the graph | **obeyed** — the blocker is in-process and **not persisted, not an index**; no migration, no store change |
| **`#11901`**: *do not widen the noun-matching window* | **obeyed** — `limit` is unchanged; the fold is not a width, and the blocker's completeness is `k`-independent (`k_independent: true`) |
| **`#11901`**: no similarity score, no second constant (`#2730` §6.7) | **obeyed** — only key EQUALITY attaches; shingles/prefixes generate candidates only. No new threshold anywhere |
| `§2.0` (Postgres record / FalkorDB cache) | **not reached** |
| hosted FalkorDB **no-eviction** finding | **not reached** — nothing is tiered or evicted |
| `#3584`/`#3590` — identity is the minted id, `name` is a natural key | **upheld** — `name` is never rewritten; every key here is derived and comparison-side |
| **`#12047` round-4 finding 5** — `a-b` must not fold to `b` | **upheld and pinned**, and its measured cost is the 180-pair residual above |
| `§7` + `§17.4.6` — the read ledger is not an admission-control signal | **not read** — the instrument passes `_record_reads=False` throughout |

---

## 6. What this lane could NOT establish

1. **Not a production census, and not a production duplicate *rate*.** Same declared limit as
   `#11901`: a lane cannot reach a live `:Object` graph, so this is the mechanism on a real name
   distribution, never a frequency estimate. The 419/422 variant pairs are **GENERATED** from real
   names, not observed.
2. **The in-window difference against `#11901` (96.9 % vs 62.1 % at `k=3`) is measured but not
   diagnosed** (§2). The likely driver is the changed harvest input; nothing here proves it.
3. **Phase 2 — the live LLM resolver — is still unmeasured.** It is handed `in_window_not_attached`
   pairs, and with the fold those are now 202 at `k=3` instead of 409; whether the *production*
   duplicate rate falls by as much is **not** established by this artifact.
4. **A blocker is not a production wiring.** `BlockIndex` is wired into `entity_candidates` (the
   dedupe scan, a live MCP surface) and into the comparison; it is **not** wired into the capture
   path's candidate generation, because that would widen the set handed to the adjudicator, which
   `#11901` measured as the X7 regression. That wiring is `#12186`'s B1, gated on the verdict step.
5. **No LongMemEval number.** `docs/benchmarks/longmemeval-500q/run-report.md` stays `PENDING`.

---

## 7. What moves next, with the number attached

1. **The article-anchor reopen (evidence filed, not adopted).** `+179` pairs (42.4 % of the 422 pairs) are in the window and reachable; only the anchor prevents the bind. The artifact's
   `attached_folded = 386` is the number that reopen is argued with, not an estimate of it. Route: argue it where
   `#12047` round-4 finding 5 lives, with this brief's §4 as the evidence — **not** a quiet change to
   `normalize_entity_name`, and not a second comparison key smuggled in beside it.
2. **The basename class (30 pairs)** needs the adjudicator (B1) or a complete-population read on the
   capture path; both are larger than this lever.
3. **Phase 2 measurement** stays first in `#11901`'s own ordering and is unaffected by this lane.
