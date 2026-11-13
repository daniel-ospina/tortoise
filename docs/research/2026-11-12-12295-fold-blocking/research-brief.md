# #12295 — the comparison-side key, multi-key blocking, and pair completeness on `#11901`'s corpus

**Lane:** `fix/12295-fold-blocking` · **Artifact:** `measurement.json` in this directory ·
**Harness:** `tools/candidate_recall_11901.py` (**reused, not rebuilt** — the tool and its pinned
test landed with `#12159`) · **Run:** `--population 1000 --sample 300 --captures 20`, elapsed
390.6 s, graph `recall11901_1794532634_746264`, population digest `03785295abd20d9a`,
measured at code head `dcb43345a3b6755d58725469aa96b9d339427d6d` — quoted in full because
`test_the_artifact_declares_one_uniform_retrieval_mode` asserts the brief names the artifact's
`code_head` verbatim.
⛔ **AND THAT SHA IS *NOT* A REPRODUCTION ANCHOR — the correction (#12304 review round 21).** This
paragraph used to call it "the anchor a reader reproduces from" and to imply it survived the rebases
because the LATEST one left it. Measured: `git merge-base --is-ancestor dcb43345a3b6` answers **1**
against BOTH this branch's HEAD and `origin/main`, and no ref contains it at all — the `#12304` force-pushes carried the local `refs/remotes/` entry past the generation that held it — so it is dangling in exactly the way the paragraph below
calls disqualifying, and no reader can `git show` it from a fresh clone. The FIELD is kept because
it is what the artifact's own contract requires and because a squash merge destroys every branch
sha anyway (the pin's own comment records that, and is why it asserts the note's SHAPE and a
content-equivalence rather than an ancestry). What replaces it as the anchor is CONTENT, which is
checkable without any sha being reachable: on a docstring-stripped AST compare,
`tortoise/entity_resolver.py` and `tortoise/extractor_v2.py` are **identical** between
`dcb43345a3b6` and this head, and `tortoise/entity_blocking.py` differs **only** by the two label
changes named below. ⛔ This paragraph also named the `437d…`-generation shas (a first rebase
generation, now dangling) until #12304 review round 10, for the same reason they are not named here
now: a sha no ref serves is not provenance, and quoting one in full makes it look like it is.
⛔ And a reader should NOT expect the obvious guard: an "every quoted sha resolves" arm was
WRITTEN and MEASURED and is deliberately NOT shipped, because a rebased-out commit leaves every
ref while its object stays in the local object DB — `git cat-file -e` answers 0 for
`437d5d17a5f5` today — so the arm stayed GREEN on exactly the mutation it exists to catch. What
IS shipped is the arm that can fail: `test_the_artifact_declares_one_uniform_retrieval_mode`
asserts `report["code_head"] in brief`, and it reds on a first-generation sha substituted for the
artifact's declared one (measured, both directions) — which is a CONSISTENCY pin, not a
reachability one. The artifact is the
source of truth for this field and the brief must agree with it.
The DELTA between the code that produced these numbers and this head touches those files in two
ways, and the provenance claim is stated as the diff rather than as a summary of it: (a)
DOCSTRINGS/COMMENTS — the `emitted:` note moves, and a
`complete=True` note is added; (b) LABELS — `e7f9d77ed5b7` (pre-rebase `d52a989eafd2`) renames the
`lsh_bands` docstring label and the `stats()` counter key `keys` -> `key_memberships`, and
`fe9cbee4e447` (pre-rebase `22ec4d1a6048`) adds a `candidates(label=None)` LABEL FALLBACK in
`entity_blocking.py`.
⛔ The enumeration, not this summary, is what proves it — and ⛔ **THE RANGE NO LONGER ISOLATES
POST-RUN CHANGES**: the `#12304` rebases rewrote every branch sha, so
`git log --format=%h dcb43345a3b6..HEAD -- tortoise/entity_resolver.py tortoise/entity_blocking.py
tortoise/extractor_v2.py` now prints the whole rewritten branch's commits, and SPANS it. No count
is stated here — deliberately: an integer quoting a range this same PR keeps extending is a
number a reader cannot re-derive, and this paragraph quoted one such figure before it was removed. It is not a list of docstring-only
commits and this paragraph does not claim it is — it holds the branch's own feature and fix
commits too, which is what a base-moved range holds. What the enumeration DOES establish is the
IDENTITY of the two statement changes, and it establishes it by CONTENT, not by position: the
`stats()` key rename (a diagnostic LABEL, which is why the artifact reads `key_memberships` while
the run emitted `keys`) is `git log -S'key_memberships'` -> `e7f9d77ed5b7`, and the fallback is
`git log -S'every label this index actually holds'` -> `fe9cbee4e447`; both are members of that
list, and both pre-rebase shas are dangling objects no reader can resolve. This run never
reaches the fallback: the harness calls
`index.candidates(surface, label="Object")` (cited BY SYMBOL, never by line — the number has rotted
three times already, so `grep -n` for the symbol in `tools/candidate_recall_11901.py` is the
check), so the `label is None` branch is not taken. Nothing further is claimed about the
range's contents: the two `git log -S` invocations above are the proof offered, and they
answer only WHICH commits the two label changes are.

⛔ **THE SCOPE ABOVE IS THE PRODUCT CODE — NOT THE HARNESS, WHICH ALSO MOVED.** The code that
PRODUCES this artifact is `tools/candidate_recall_11901.py`, and `git diff --stat e9a572d4b4
<code_head> -- tools/candidate_recall_11901.py` is `494 insertions(+), 7 deletions(-)`: the `#12294`
name-entropy arms arrived with the base move. So a re-run at the declared head emits **six arms
this artifact does not contain** — `gate`, `gate_by_class`, `gate_gated_names`, `gate_variant`,
`pooled_coverage`, `pooled_gate`, every one an unconditional `arms[...] = ...` assignment in
today's harness (16 assigned vs the 10 held here: `blocking`, `duplicate_rate`, `exact*`,
`pooled`, `variant*`). ⛔ Do not verify that set with a grep for two of the names — that is how
this paragraph first undercounted it by four; enumerate instead:
`git show <code_head>:tools/candidate_recall_11901.py | grep -o 'arms\["[a-z_]*"\]' | sort -u`
against the artifact's own `arms` keys. Read it as the reproduction contract: these
numbers are the RUN's (the harness as it stood at `e9a572d4b4`); a re-run at the declared head
reproduces every arm here **plus** those six, whose absence is the base having moved
and not a lost measurement. The `#12294` gate arms have their own run and their own artifact
(`measurement-entropy-gate-12294.json`), and `code_head_note` carries this same disclosure.

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
| `normalize_entity_name` | `entity_resolver.py` → `def normalize_entity_name` | NFKC → casefold → drop a leading **article** → non-word run → space → whitespace collapse |
| write doors that persist it as `canonical_name` | `sdk.py:45886` (the `_emit` funnel), `api.py:2628` (`add_subject`), `api.py:2671` (`add_object`), `sdk.py:47229` (a real rename) | filled at every door, `setdefault`, so a caller-supplied fold wins |
| the projection | `projection/entities.py:5094` (ON CREATE, objects) / `:4958` (subjects), coalesce fill at `:5101` / `:4975` | `canonical_name` is written ON CREATE only, so a re-mention cannot rewrite the key that resolved it |
| identity | `projection/entities.py:4907` (`merge_key = "id"`) | `name` is a mutable natural key; the id is the identity (`#3584`/`#3590`) |

So item 1 of the issue — *"a comparison-side normalised key … never rewrite `name`"* — is **built**,
as of `#12047`. What did not exist, and is what this lane adds:

| gap | site | measured consequence |
|---|---|---|
| the capture prior compared `_norm` only — whitespace + lowercase, byte-identical to Zep's `_normalize_string_exact` | `extractor_v2._norm` at `:4232`, the historical arm in `_find_existing_entity`; `extractor_v2._find_by_folded_key` cited BY SYMBOL (a `file.py:<line>` cite rots on any insertion above it, and this one did — it read `:5823` for two review rounds after the def moved to `:5831`) (`_find_existing_entity`, `:5769` at the head this table was written), which now delegates to `extractor_v2._find_by_folded_key` (cited by SYMBOL above — its `def` line moves and is deliberately NOT restated here) | **0 of 422** variant pairs attach at any `k` in the **hybrid** mode measured here (`#11901` measured 0 of **419** in each of its own two modes) — the before-half of every rate in §3 |
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
from the old artifact: a before/after against a moving before is not a measurement. ⚠️ **The same
limit applies one field down, inside ARM 2:** this run's `attached_folded` = **386** is not
comparable with `#11901`'s `attached_folded` = **114** either — and the movement is **both** terms,
not either one alone: **386 = 114 + 186 CODE + 86 INPUT**. The **186** is the classes the arm could
not bind AT ALL before the shipped rung existed (`article-drop` 0 → 175, `hash-dropped` 0 → 11 —
both `attached_folded` 0 in `#11901`); the **86** is the separator classes that were already **100 %
of in-window in BOTH runs** (`space-to-hyphen` 101/101 → 179/179, `hyphen-to-space` 13/13 → 21/21),
so their growth is on the INPUT side — the two runs differ in both corpus size and retrieval
configuration (`legs_seen.structural` is `false` there, `true` here), so the mechanism stays
undiagnosed, exactly as §6 item 2 says. ⛔ **The split is not optional bookkeeping, and
the reason is arithmetic:** the arm gates on `cand2.get("id") == row["id"]` and `cand2` is chosen only
from `rows`, which IS the window — so `attached_folded <= in_window` always. `#11901`'s own
`in_window` at `k=3` is **260**, so a code-only re-run of THAT corpus could reach at most 260 and
never 386; a "code move" reading of the total is arithmetically impossible, not merely imprecise.
⚠️ **And the arm did not start running the shipped matcher here:** `origin/main`'s arm already
folds each row's NAME and the surface separately, then calls the matcher —
`folded = [dict(r, name=_fold_punct(r["name"])) for r in rows]` and
`_find_existing_entity(folded, folded_surface, "core:other")`, unchanged at this head. What moved is
the MATCHER's body, which gained the folded rung (`#12295`). The arm only ever called it.

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
| **multi-key blocking** (`entity_blocking.BlockIndex`: folded key ∪ folded prefix ∪ per-token prefixes ∪ LSH shingle bands) | **35.9** (max 152) | **1.0000** (422/422), reduction ratio 0.0255 |

The reduction ratio is the blocker's actual cost on the whole graph: pairs proposed by **all 1000**
names over `C(1000, 2)` (`entity_blocking.reduction_ratio`, computed by that one helper). It is NOT
the pairs proposed by the 300-row variant sample — mixing the two published a ratio ~3× cheaper
than the quantity its own definition names (`#12295` round-2 B3, fixed before this run).

⚠️ **Three recall bounds sit under every blocking figure in this section, and none is a
property of the blocker alone.** The first is `MAX_BLOCK_SIZE = 200` in `BlockIndex.candidates()`
(`tortoise/entity_blocking.py`): a block larger than that is **skipped, not scored**, and the skip
is recorded under `oversized_skipped` — and this table's pair-completeness column is computed
through that same call — `tools/candidate_recall_11901.py`'s `_in_block` (~:741), which is
`member_id in set(index.candidates(surface, label=label))` — while the candidates-per-mention
column is a SEPARATE inline count,
`blocked_ids = set(index.candidates(surface, label="Object"))` (~:1187) reported as
`mk_len = len(blocked_ids)` (~:1200). `_blocked_pair_set` (~:684) is the third caller of that
method, and it serves only the **reduction ratio** numerator, not either column in this table.
All three go through `BlockIndex.candidates()`, so all three sit under the 200 cap. The second
is the `low_entropy(key)` gate in `block_keys`, which drops
every `S:` (shingle) band for a short or repetitive name, so typo/separator reach is unavailable for
exactly those names. **Neither bound shrinks these figures:** the measured run's
`arms.blocking.index_stats` reports `max_block_size` **93** against the 200 cap, with
`oversized_skipped` **empty** — so **these counts are complete for THIS corpus**. On a larger
corpus the cap would deflate pair completeness **silently**: nothing refuses, warns, or fails on a
non-empty `oversized_skipped`, so a reader must check that field to know whether the number still
means what this column says it means. The third is the `T:` key's own floor — `block_keys` emits a
word-order/transposition key only for tokens matched by `_WORD = re.compile(r"[a-z0-9']+")` **and** at
least `MIN_BLOCK_CHARS` (3) characters long, so a transposition of shorter tokens (`'ab cd'` /
`'cd ab'`) or of non-ASCII tokens (`'привет мир'` / `'мир привет'`) shares **no** key and is never
compared. It does not move these figures — every token in this population's reachable classes
clears the floor — but it is a real reach limit on the shipped comparison path, and it is stated
here rather than left for a reader to infer from “transpositions are reached”.

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
harness's `attached_folded` arm binds **386 of 422 (91.5 %)** — and ⛔ **read what that arm IS before
quoting it as a ceiling on the shipped fold.** It is **not** a second, article-preserving fold. It is
the **shipped matcher itself** (`_find_existing_entity`, which since this PR includes
`_find_by_folded_key`) run on a **separator-normalised input**: `_fold_punct` is applied to the
MENTION and to the rows first, and the live matcher then compares those. The article-dropping rung
therefore still fires inside it — the arm moves the mention across the **separator**, not the fold
across the **article**. Its 386 decomposes per class as 175 (`article-drop`, bound by the shipped
folded rung) + 179 (`space-to-hyphen`, bound because the mention was separator-normalised to
`the plan doc` and the stored `the plan doc` folds onto it) + 21 (`hyphen-to-space`) + 11
(`hash-dropped`). So 207 is what the *stored* key binds, and 386 is what it binds **once the MENTION
is separator-normalised before the comparison** — a lever on the matcher's input. ⛔ It leaves the
STORED anchor alone, but it is **not free** and not compatible with `#12047` round-4 finding 5 as
written: `_fold_punct("a-b")` → `"a b"`, the fold then drops the leading `a`, so
`key_of("a b") == "b"` and an `a-b` mention binds a stored `b` — the binding that finding forbids.
§4 and §7(a) therefore treat it as its own decision (or a reopen), not as a way to reach the 179
without touching that finding. Neither number is "the fold works" on its own, which is why both are
in the artifact.

## 4. The residual is exactly three declared classes — two of them decisions, one a retrieval miss

215 pairs remain unattached. **Every one of them is attributed** (the pinned test
`test_the_residual_is_the_DECLARED_classes_and_nothing_unexplained` fails on an undeclared class).
The tolerated set is `space-to-hyphen`, `basename-only` and `article-drop`; only the first two are
*decisions* this lane explains — `article-drop` is the pre-existing retrieval miss `#11901` owns,
tolerated so the test reds on a NEW unexplained class rather than on that one. A reader who takes
the shorter title literally believes a newly unattached `article-drop` pair fails the test; it
does not.

**`space-to-hyphen` — 180 pairs (100 % of the class; 42.7 % of the 422 pairs), 180/180 reachable by the blocker, 0 attach.**
Mechanism, in one line: `normalize_entity_name` anchors the article strip on a real whitespace
boundary **before** it folds punctuation (`#12047` round-4 finding 5 — the fix that stopped `a-b`
folding to `b`), so `the-plan-doc` folds to `the plan doc` while the stored `the plan doc` folds to
`plan doc`. The two never meet. **That decision is NOT reversed here** — this lane pins it from the
outside (`test_the_article_anchor_decision_is_not_reversed`) and reports its measured cost instead.
The artifact's own per-`k` rows say something sharper than "a ceiling". At the **k=3 production
window** 179 of 180 are IN the window, so exactly one is out of reach at that setting; at
**k=5, k=10 and k=15 all 180 are in the window** (`arms.variant_by_class["space-to-hyphen"][k]["in_window"]`
= 180 at each of those `k`) and **still 0 attach**. Width therefore cannot recover them — which makes the diagnosis
*stronger*, not weaker: the article anchor alone is the cause of the SHIPPED matcher's
non-attachment, and no `k` budget fixes it. Recovering
them inside the window would take the lane from **207/422 = 49.1 %** to **386/422 = 91.5 %**, and the
artifact's `attached_folded` = 386 (91.5 %) is the number that says so — ⛔ **but the lever that
number names is not the article anchor.** The arm separator-normalises the MENTION and then runs the
shipped matcher, so those pairs bind because the separator is gone from the mention — ⛔ but that is
**not free**: `_fold_punct("a-b")` is `"a b"` and the fold then drops the leading `a` as an
article, so `key_of("a b") == "b"` and an `a-b` mention binds a stored `b`, the binding `#12047`
round-4 finding 5 forbids. The artifact measures ATTACH COUNTS only and carries no false-merge arm,
so nothing here bounds what those binds cost; adopting the lever therefore needs its own precision
decision (a guard for the article-plus-separator class, plus a false-merge measurement this run does
not have) or it has to be argued as a reopen. Reversing the anchor is still not a candidate for quiet
adoption — it is filed as evidence (see §7), argued from the per-class reach numbers rather than from
the 386.

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
| **`#12047` round-4 finding 5** — `a-b` must not fold to `b` | **upheld, and pinned on the SHIPPED comparison path this PR itself modifies** — `test_the_article_anchor_decision_is_not_reversed` drives the shipped `_find_existing_entity` (which gains `_find_by_folded_key` at `extractor_v2.py:5828`) and returns `(None, 'none')` for `a-b` against a stored `b`; its measured cost is the 180-pair residual above; §7(a)'s separation lever would reintroduce that binding on the MENTION side, which is why it is not free |
| `§7` + `§17.4.6` — the read ledger is not an admission-control signal | **not read** — the instrument passes `_record_reads=False` throughout |

---

## 6. What this lane could NOT establish

1. **Not a production census, and not a production duplicate *rate*.** Same declared limit as
   `#11901`: a lane cannot reach a live `:Object` graph, so this is the mechanism on a real name
   distribution, never a frequency estimate. The 422 variant pairs are **GENERATED** from real
   names, not observed.
2. **The in-window difference against `#11901` (96.9 % vs 62.1 % at `k=3`) is measured but not
   diagnosed** (§2). The likely driver is the changed harvest input; nothing here proves it.
3. **Phase 2 — the live LLM resolver — is still unmeasured.** It is handed `in_window_not_attached`
   pairs, and with the fold those are now 202 at `k=3` instead of 409; whether the *production*
   duplicate rate falls by as much is **not** established by this artifact.
4. **A blocker is not a production wiring.** `BlockIndex` is wired into `entity_candidates` (the
   dedupe scan, a live MCP surface) and **nothing else** — it is not on the comparison path, whose
   consumer is `extractor_v2._find_by_folded_key` and the KEY-only `attach_matches` (`E`/`B` keys,
   no index), and it is **not** wired into the capture
   path's candidate generation, because that would widen the set handed to the adjudicator, which
   `#11901` measured as the X7 regression. That wiring is `#12186`'s B1, gated on the verdict step.
5. **No LongMemEval number.** `docs/benchmarks/longmemeval-500q/run-report.md` stays `PENDING`.

---

## 7. What moves next, with the number attached

1. **Two distinct levers — neither is free, and one is a reopen in the stored key.** The argument
   rests on the per-class reach numbers, not on one total: `space-to-hyphen` is **180/180 reachable by
   the blocker, 179 in the `k=3` window, 0 attach** (and `0 attach` at every `k`, including the widths
   where all 180 are in the window) — so the pairs are present and unattached, and no `k` budget
   reaches them.
   **(a) Separator-normalise the MENTION before the comparison** — that recovers **179** of them, it
   is exactly what the artifact's `attached_folded = 386` measures (386 = 175 + 179 + 21 + 11). It
   leaves the STORED key alone, but it is not free and not priced by this artifact —
   `_fold_punct("a-b")` → `"a b"` → the fold drops the leading `a` → `key_of("a b") == "b"`, so an
   `a-b` mention binds a stored `b`, the binding `#12047` round-4 finding 5 forbids, and the
   measurement counts no false merges. Adopting it needs a decision of its own (a guard on the
   article-plus-separator class, and a false-merge arm this run does not have) rather than being a
   way to reach the 179 without touching that finding.
   **(b) Reverse the article anchor** — that would fold `the-plan-doc` onto `plan doc` in the stored
   key itself. That IS a reopen of a recorded finding, and it belongs where that finding lives, with
   this brief's §4 as the evidence — **not** a quiet change to `normalize_entity_name`, and not a
   second comparison key smuggled in beside it. `+179` pairs (42.4 % of the 422 pairs) are in the
   window and reachable in either case.
2. **The basename class (30 pairs)** needs the adjudicator (B1) or a complete-population read on the
   capture path; both are larger than this lever.
3. **Phase 2 measurement** stays first in `#11901`'s own ordering and is unaffected by this lane.
