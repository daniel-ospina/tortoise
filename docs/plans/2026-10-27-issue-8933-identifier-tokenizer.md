<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/8933#issuecomment-6451906842 -->
<!-- plan-review: cycles=1, status=clean, version=2.3.0 -->
# #8933 — preserve the identifier: the search lane's identifier tokenizer

**Path:** decided-ruling (owner comment on #8933, 2026-10-27T00:42:49Z — "RULING (owner, 2026-10-26): **reopen** — the identifier is preserved, in two stages")
**Issue:** #8933 · **Branch:** `fix/8933-search-tokenizer` · **Complexity:** standard · **Team:** epistemic-team

## 1. Confirmed problem

`tortoise/sparse.py` splits a query on non-alphanumerics *before* dropping all-digit
tokens (`_SPLIT_RE`, `tortoise/sparse.py:72`; the drop is in `tokenize_sparse_query`,
`tortoise/sparse.py:117`). A punctuated identifier is therefore shredded and its numeric
segments — the discriminating half of the code — are discarded as "numeric noise":
`ERR-4032` → `err`, `INV-2024-0091` → `inv`, `capstone-9664` → `capstone`. Two records whose
codes differ by one digit become the same query, receive the same score, and the tie-break
drops the newest. Mixed alphanumeric identifiers (`cus_9fA2bL`) survive; every identifier whose
specificity sits in digits does not.

This was a **recorded control** (`keep_numeric=False`, "the search lane stays byte-identical",
`tortoise/sparse.py:114`, pinned by `tests/test_ask_retrieval_levers.py:415`, decided in
`docs/planning/2026-08-31-2070-scoping-package.md:244`). The owner **reopened** it on
2026-10-26: *"The control is reopened. The search lane may no longer stay byte-identical for
identifier-shaped queries."* The body's option-table "A" was **withdrawn on measurement** (an
escaped composite query term matches zero rows because the graph index stores `capstone` and
`9664` separately); the live shapes are the owner's two stages.

**Confidence:** high — the defect and both stages are measured in the ruling comment.

**Falsifier (from the research block):** if the write-side and read-side normalised forms can
disagree for any spelling a writer produces, the alias silently matches nothing and Stage 2
must not ship. Pinned by the round-trip test, which calls ONE function from both sides.

## 2. Alternatives considered

| # | Option | Outcome |
|---|---|---|
| A | Escaped composite query term (`capstone\-9664`) | **Rejected — withdrawn on measurement.** The index stores the segments separately, so a composite term matches 0 rows; with a second OR half it restores the query but not the discrimination (fresh point still 16th of 16). |
| B | Flip `keep_numeric=True` on the search lane | **Rejected.** Wholesale reversal of #2070; admits years/counts/times into every query, and measures *worse* than the phrase shape on a corpus where look-alikes also carry digits. |
| C | Leave the tokenizer alone, serve identifiers elsewhere | **Rejected by the ruling** — dense retrieval is the leg structurally worst at exact identifiers. |
| **1+2** | **Stage 1** adjacency phrase (query side) + **Stage 2** derived normalised alias in the indexed text (write + query side) | **Chosen.** Stage 1 needs nothing indexed and works on existing records (measured: fresh exact match 16th → 1st of 16). Stage 2 makes it a class fix and matches how identifiers are treated at the field level elsewhere (a single normalised whole-value token, one definition serving both indexing and querying). |

**OVERRIDES:** the #2070 `keep_numeric=False` / "search lane stays byte-identical" control, for
identifier-shaped queries only — owner ruling on #8933, 2026-10-26: the identifier is preserved
(`good > easy`). Bare numeric tokens are still dropped.

⚠️ **What that reopen actually widens, stated so nobody has to measure it from the code.**
Byte-identity holds only for queries with no identifier SHAPE. The space form the ruling requires
("matches however the person typed it — dashes, spaces, or none") is syntactically
indistinguishable from prose, so **any** `<word> <2+ digits>` query now gains an alias term and an
adjacency phrase: `top 10` → `top|top10|"top 10"` (was `top\ 10`), and a document whose prose
happens to contain that pair can outrank one matching all of the query's words — measured on a
live FalkorDB at #10203 review, and pinned by
`TestRawRetrieval::test_the_space_typed_pair_is_a_MEASURED_cost_not_a_silent_one`.
`tests/test_bm25_lexical_8488.py` also records a measured ranking harm from the phrase half. The
ruling authorises this cost; it is not a side effect discovered later.

## 3. Complexity + domains

`complexity:standard`, **code**. Two existing modules, one new test module, one plan doc. No
schema change, no new indexed property, no MCP/SDK surface change (the tokenizer and the
projection writer are internal; `TortoiseSDK`'s public method set is untouched).

## 4. Integration Surface Map

| Surface | Layer | Test |
|---|---|---|
| `sparse.tokenize_sparse_query` (one tokenizer, imported by `bm25`, `search_engine`, `sdk` PRF) | unit | `tests/test_identifier_tokens_8933.py::TestPolicy` |
| `sparse.build_or_query` (the literal RediSearch query string) | unit | `TestPolicy`, `TestRawRetrieval` |
| `projection/entities._upsert_point_props` (shared Point writer: SDK doors + `EventAPI` seam + replay) | integration | `TestWritePath` (live FTS lane) |
| `search_engine.run_fts_query` over the real index | integration | `TestDiscrimination` (live FTS lane) |
| the #2070 number-lane control (`keep_numeric`) | unit | `TestRawRetrieval` |

## 5. Research

The `<!-- research: v1 -->` comment ([issuecomment-6451906842](https://github.com/daniel-ospina/tortoise/issues/8933#issuecomment-6451906842)) — decided-ruling form, with `**Basis:**` pointing at the owner ruling, the internal locators re-measured at this head, and the two external claims re-checked at the Elastic primary docs. It supports: the one-normaliser-both-paths invariant (§Stage 2), the "alias is additive, never a replacement" rule (criterion 4), the conservative grammar (risk 1), and the schema-free insertion point (`Point` fulltext is already `(content, search_keys)` — `tortoise/projection/__init__.py:18528`).

## 6. Wiring

| Site | What it does today | What it does after |
|---|---|---|
| `tortoise/sparse.py::tokenize_sparse_query` | splits + drops all-digit + stopwords | unchanged, then **appends the normalised identifier aliases** (flag-gated) |
| `tortoise/sparse.py::build_or_query` | `len(tokens)<=1` → escaped RAW; else OR-union + expansion tail | unchanged, then **appends the adjacency phrase terms** (flag-gated) |
| `tortoise/sparse.py::expansion_tokens` | tokenizes PRF aliases with `keep_numeric=True` | unchanged; aliases now also appear in `search_keys` so they can be harvested — additive |
| `tortoise/bm25.py::bm25_tokens` | `tokenize_sparse_query` | inherits the aliases — no second tokenizer (the dual-stack contract) |
| `tortoise/embeddings.py::search_points` | sklearn TF-IDF over `index_text(content, search_keys)` | unchanged; the alias reaches it through `search_keys` (already in the indexed text) |
| `tortoise/projection/entities.py::_upsert_point_props` | flattens `search_keys` on a copy when a list is present | also appends the derived aliases to `search_keys` (same copy, same door) — and, when the payload does **not** carry its own non-blank `search_keys`, **appends** the alias to the node's stored value instead of assigning it (`identifier_alias_append_clauses`, a guarded `SET` in the same query). Assigning it turned a stored `"release deploy rollout"` into `"err4032"` for the E3 default spelling `search_keys: []` — measured, #10203 review round 6 P1 |
| `tortoise/sdk.py::update_point` + `projection/_revise_point` (the two CONTENT-EDIT doors) | write `n.content` and left `search_keys` alone | **recompute the derived aliases** (`revise_identifier_aliases`): the node's previous `content`/`search_keys` is read in the same round trip, its own aliases are dropped, the new content's are added, and an empty remainder REMOVES the property. Same rule the door already applies to `content_hash` (#1904) and the vector (#4208). Without it a revision never indexed a newly added identifier, kept a stale one after it was removed, and reported an unclearable `content` divergence on `search_keys` (measured P0) |
| `tortoise/projection/entities.py::flatten_search_keys_prop` | THE one home of the search_keys graph form | unchanged (the alias is appended *after* flattening) |
| rebuild / replay (`rebuild_all` pass 1a → `_upsert_point_props`) | writes the journal payload | re-derives the same aliases from `content` — the backfill for existing records is a **replay**, not new code, and is idempotent by construction |
| `tortoise/consistency.py::_canonical_point_fields` | applies the writer's `flatten_search_keys_prop` to the canonical view of BOTH sides | **strips the derived alias from BOTH sides** (`strip_identifier_aliases`). The first shape of this change derived it on the JOURNAL side only (`derive_identifier_aliases=False` for the graph view) to keep the graph side "the node's own content"; that is a false red, measured: the revise door never derived, so any content edit introducing an identifier made the reference view mint an alias the node could never hold — `ok=False`, `fields=['search_keys']`, action "replay the journal", which reproduces the same red. Deriving on the GRAPH side instead (the other direction) is a false PASS. The alias is a PURE function of `content`+`search_keys`, both of which are compared on their own, so it is not independent evidence: removing it is exact on both sides, and it makes `db_hash` alias-invariant, so a backfill that only ADDS aliases cannot move a recorded baseline. (The REMOVAL direction is not free and is why `_PROJECTION_STATE_FORMAT` is now 4: a token the CALLER authored and the strip canonicalises away — at the current content, or at the content the point previously held, which is the #10203 review P1 — is present on a graph written by RELEASED code, so that baseline moves once on upgrade and, because `record_projection_state` is gated on `ok`, would otherwise repeat `unrecorded-mutation` for ever. Same answer as v1 → v2 (`tags`) and v2 → v3: append the format, let the predecessor migrate) |
| `search_engine.run_fts_query` | passes `build_or_query` output to `queryNodes` | unchanged — the alias/phrase arrive through the query string and the indexed text |
| `tortoise/sdk.py::_warn_point_name_not_lexically_indexed` | tokenised the INDEXED text with the tokenizer's DEFAULT — the READ half | **the WRITE half** (`space_separated_identifiers=False`). Anything that asks "does the index hold this?" has to use the half that WROTE it, or the read-side composite (``sprint2024``, minted from the prose "sprint 2024 planning") counts as coverage no search can produce, and the #10122 advisory falls silent about a stored-but-unfindable name (measured: 0 advisories → 1, #10203 review round 12 P2). `name_tokens` stays on the READ side: the name is caller-typed and reaches the engine as a QUERY |
| `tortoise/sdk.py::update_point`, explicit `search_keys=None` | on a content edit, `props.get(...)` could not tell an explicit `None` from an absent key, so the recomputed derived value REPLACED the caller's clear and the vocabulary it asked to drop survived on the node and in the FTS index | an explicit `search_keys=None` is **caller-owned CLEAR intent**: the gate is on the KEY, the null rides `SET n += $props` (removes the property) and rides `PointRevised`, exactly as pre-#8933. The replay fold has no clear clause, so live (cleared) and replay (re-derived) disagree here — the SAME #5046 caller-owned gap a caller-supplied LIST already has, neither widened nor narrowed, and identical to what `main` produced for this call |

## 7. Implementation steps

1. `sparse.py`: `IDENTIFIER_ENV` + `identifier_tokens_enabled()` (default ON, `env_flag`, so a
   typo never flips it and `=0/false/no/off` bisects — the ruling's fail-safe switch).
2. `sparse.py`: `_IDENTIFIER_SPAN_RE` (conservative: `letters` + `[-_]` + segments, ≥1 all-digit
   segment of ≥2 digits, ≤48 chars, ≤6 segments) and `identifier_alias(span)` — **THE one
   normaliser**, used by the write path and the read path.
3. `sparse.py`: `identifier_aliases(text)`, `identifier_phrase_terms(text)`.
4. `sparse.py`: `tokenize_sparse_query` appends the aliases; `build_or_query` appends the phrases
   to the OR-union tail (original tokens keep their reserved slots — the A4 guard is untouched).
5. `projection/entities.py`: `point_identifier_aliases(props)` + `append_identifier_aliases(...)`,
   applied on the same copy that `flatten_search_keys_prop` already takes.
6. `tests/test_identifier_tokens_8933.py` (registered in `config/ci-surfaces.yml` → `surfaces.core`):
   policy pin (reds when the mechanism is removed), round-trip agreement, #10121-shaped
   discrimination on a live FTS corpus, raw-retrieval byte-identity for non-identifier input.
7. `docs/plans/` this doc; PR body with the contract close-out.

Each step's test is named in §4/§8. Step 4's test is the policy pin; step 5's is the write-path
integration test; step 6 is the whole file.

### Decision update — review round 6 (2026-10-28), why §6's checker row reversed

The plan's checker decision ("derive the alias on the journal side only") was **withdrawn on
measurement**, not on preference. Reproduced end-to-end against a live lane: create a point, then
`PointRevised(new_content="error ERR-4032 on deploy")` → the node keeps the creation's
`search_keys`, the reference view derives the new content's alias, and `check_consistency` returns
`ok=False`, `divergence="content"`, `fields=['search_keys']` with the action "reconcile by
replaying the journal" — which reproduces the red. Setting `TORTOISE_IDENTIFIER_TOKENS=0` on the
identical journal+graph returns `ok=True`, i.e. the false red was this change's alone. Two
alternatives were considered and rejected: (a) make the REVISE door derive while the checker keeps
the journal-side derivation — still needs the old content to retract a stale alias, so it needs the
same read, and it cannot cover an `PointAdded` re-emit that changes content at all; (b) move the
alias to a NEW indexed property — correct, but it changes the `Point` fulltext field set, and on an
existing graph the engine refuses to re-create an index with a different field set ("already
indexed"), so live upgrades would write a field nothing indexes until a rebuild. The chosen shape
fixes the write doors (which is where the value must be correct) and makes the checker exact.

**Known residual, filed rather than hidden:** a `PointAdded` re-emit over an **existing** id whose
content changed and which carries no `search_keys` list keeps the previous content's alias
(the append cannot retract what it cannot see). `check_consistency` reports it as a REAL
`search_keys` divergence — a replay of that journal does not produce the stale token — so it is
detectable and its action clears it. Everything the capture and revise doors write is retracted
correctly. Also documented, not fixed here: dropping separators means `foo-12-34` and `foo-1234`
share the alias `foo1234` (the ruling's own concatenation rule; the adjacency PHRASE distinguishes
them, the alias term does not, and widening the grammar is a separate measured step).

## 8. Rounds

### Decision update — review round 12 (2026-10-29), the two open findings and what they cost

Round 12 reported ISSUES 2, both in this change's own operator/edge surface rather than in the
mechanism (which it verified clean). Both are fixed and pinned; nothing above was reversed.

**F1 (P2, fail-open)** — the #10122 advisory tokenised the indexed text with the tokenizer's
DEFAULT (the READ half). The index is written by the WRITE half, which refuses to mint a
composite from a prose pair, so `content="sprint 2024 planning"` indexes `sprint`/`2024`/
`planning` and never `sprint2024`, while the read side handed `sprint2024` to `indexed_tokens`. A
Point named `sprint2024` over that content therefore intersected and the advisory fell SILENT
about a name the engine cannot find — the exact case it exists to report. Fixed at the consumer
(§6 row), measured 0 → 1 advisory. The pin carries an FTS POSITIVE CONTROL (the composite really
is absent from the index), so the advisory is owed rather than assumed.

**F2 (P3)** — `update_point(id, content=…, search_keys=None)` stopped clearing: the gate was
`props.get("search_keys")`, which cannot distinguish an explicit `None` from an ABSENT key, so the
derived value replaced the caller's clear, and `_derived_search_keys` came out `True`, stripping
the clear from `PointRevised` as if it were derived. Measured pre-fix `'release deploy'` (the
derivation rebuilt the vocabulary the caller dropped). The gate is now on the KEY.

**The consequence, measured and recorded rather than discovered later:** honouring the clear makes
the live node `None` and a replay re-derive `'release deploy'` (the revise fold has no clear
clause). That is the #5046 CALLER-OWNED parity gap a caller-supplied list already has, and it is
identical to what `main` produced for this call — pre-#8933 neither door touched `search_keys`, so
live cleared and replay kept the creation's value. This therefore restores the pre-change shape
rather than introducing an asymmetry, and the A4 vocabulary a caller clears stays cleared on the
node, which is what the call asked for.

**Rounds:** 1 (verifiers: 1, class: code)

### Review Scope Contract

**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:

- R1 **A query that matched before stops matching** (the alias/phrase could displace or de-rank an
  original token) → `TestRawRetrieval` (a query with no identifier SHAPE is byte-identical
  `build_or_query` output; a mixed-alnum query keeps every original token) and the A4 reserved-slot
  assertion `tests/test_ask_retrieval_levers.py::test_expansion_never_displaces_original_tokens`.
  The space form's measured cost on `<word> <2+ digits>` prose is §2's warning, pinned by
  `test_the_space_typed_pair_is_a_MEASURED_cost_not_a_silent_one` — R1 is about not LOSING what
  matched, not about promising the query string never changes.
- R2 **Write-side and read-side normalisers drift** (the silent-nothing failure) →
  `TestRoundTrip::test_write_and_read_agree_on_every_separated_spelling` calls ONE function from both sides
  over a spelling table; deleting the shared call on either side reds it.
- R3 **Bare-numeric noise re-enters the search lane** (the #2070 control silently reversed beyond
  identifiers) → `TestRawRetrieval::test_no_bare_numeric_token_reaches_the_search_lane`
  (`27:12`, `$40`, `2024-01-15` contribute no all-digit token; the same identifier query is
  compared against `keep_numeric=True`, so the test cannot pass with the mechanism removed).
- R4 **The write path indexes text that was not scrubbed** (ruling risk 2) →
  `TestWritePath::test_redacted_span_contributes_no_secret_alias` — a `[REDACTED:…]` span yields no
  alias containing the secret, because redaction replaces the whole value (`tortoise/security.py:488`).
- R5 **A point with no identifier is written differently** (raw-retrieval regression) →
  `TestWritePath::test_no_identifier_takes_the_pre_8933_path` — a plain point still writes no
  `search_keys` at all.
- R6 **Unbounded index growth** (aliases appended per write, forever) → `append_identifier_aliases`
  is idempotent (alias set difference against the existing tokens) and capped at
  `MAX_ALIASES_PER_POINT`; `TestWritePath::test_append_is_idempotent` re-applies three times and
  asserts the value is unchanged.
- R7 **A revision's derived alias diverges between live and replay** (the false-red / stale-alias
  class) → `TestWritePathLive::test_a_content_edit_rederives_the_alias_and_retracts_stale_ones`
  (projection door) and `test_the_sdk_update_door_rederives_too` (SDK door),
  `tests/test_consistency_divergence_5011.py::test_an_identifier_alias_is_not_a_search_keys_divergence`
  (create + two revisions compare clean) and `::test_the_graph_fingerprint_is_alias_invariant`
  (a backfill cannot move `db_hash`, while a payload-owned key still moves it).
- R8 **The derived alias replaces a stored retrieval vocabulary** →
  `TestWritePathLive::test_an_empty_search_keys_list_does_not_replace_the_stored_vocabulary`
  (the E3 `search_keys: []` default keeps the node's own keys and gains the alias),
  `::test_an_ARRAY_valued_node_does_not_abort_the_alias_write` (the ADD door keeps an
  array-valued node's tokens *and* flattens it to the indexable string) and
  `::test_a_content_edit_on_an_ARRAY_valued_node_keeps_its_vocabulary` (the REVISE door
  does the same, both directions — an edit that introduces an identifier keeps the tokens
  and gains the alias, an edit that removes one keeps the tokens and drops only the alias).
  The risk is a property of the *invariant*, not of one door: round 9 found it live on the
  revise door because only the ADD door was pinned. Both pins are non-vacuous, measured at
  `b591e0ec9` on a live lane (`docker://:@127.0.0.1:16397/…`, 2 passed) by reverting each fix
  in place: restoring the unguarded `_NODE_SEARCH_KEYS_VIEW = "n.search_keys"` reds the ADD
  pin, and restoring the non-list-aware `current = old_search_keys if isinstance(…, str) else ""`
  reds the REVISE pin (`assert sorted(stored.split()) == ["alpha", "beta", "err4032"]`).

**Threat classes:** (not adversarial) — no gate/enforcement code in this diff.

**Out of scope — must NOT be raised** (note, file, do not chase):

- the #10121 tie-break/recency ordering (a separate issue with its own decision; this change
  raises the exact match's *score*, it does not change tie ordering);
- widening the identifier grammar to `.`/`/`/`#` (ruling risk 1: a separate measured step);
- the LongMemEval-500q baseline (#8486 is postponed by the owner; this lands on mechanism tests);
- a standalone backfill tool (the rebuild path already re-derives the alias — §6);
- pre-existing defects outside the diff · style/prose preference · architectures the plan did not
  propose.

**Acceptance:** every `R<n>` has its named evidence, and no open item remains on the declared
surface.
