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
(`good > easy`). Bare numeric tokens are still dropped; nothing else about the search lane's
bytes changes.

## 3. Complexity + domains

`complexity:standard`, **code**. Two existing modules, one new test module, one plan doc. No
schema change, no new indexed property, no MCP/SDK surface change (the tokenizer and the
projection writer are internal; `TortoiseSDK`'s public method set is untouched).

## 4. Integration Surface Map

| Surface | Layer | Test |
|---|---|---|
| `sparse.tokenize_sparse_query` (one tokenizer, imported by `bm25`, `search_engine`, `sdk` PRF) | unit | `tests/test_identifier_tokens_8933.py::TestPolicy` |
| `sparse.build_or_query` (the literal RediSearch query string) | unit | `TestPolicy`, `TestRawRetrievalUnchanged` |
| `projection/entities._upsert_point_props` (shared Point writer: SDK doors + `EventAPI` seam + replay) | integration | `TestWritePath` (live FTS lane) |
| `search_engine.run_fts_query` over the real index | integration | `TestDiscrimination` (live FTS lane) |
| the #2070 number-lane control (`keep_numeric`) | unit | `TestRawRetrievalUnchanged` |

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
| `tortoise/projection/entities.py::_upsert_point_props` | flattens `search_keys` on a copy when a list is present | also appends the derived aliases to `search_keys` (same copy, same door) |
| `tortoise/projection/entities.py::flatten_search_keys_prop` | THE one home of the search_keys graph form | unchanged (the alias is appended *after* flattening) |
| rebuild / replay (`rebuild_all` pass 1a → `_upsert_point_props`) | writes the journal payload | re-derives the same aliases from `content` — the backfill for existing records is a **replay**, not new code, and is idempotent by construction |
| `search_engine.run_fts_query` | passes `build_or_query` output to `queryNodes` | unchanged — the alias/phrase arrive through the query string and the indexed text |

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

## 8. Rounds

**Rounds:** 1 (verifiers: 1, class: code)

### Review Scope Contract
**Class:** code

**Risk surface** — what this change can break, each with the check that would catch it:
- R1 **A query that matched before stops matching** (the alias/phrase could displace or de-rank an
  original token) → `TestRawRetrievalUnchanged` (non-identifier queries are byte-identical
  `build_or_query` output; a mixed-alnum query keeps every original token) and the A4 reserved-slot
  assertion `tests/test_ask_retrieval_levers.py::test_expansion_never_displaces_original_tokens`.
- R2 **Write-side and read-side normalisers drift** (the silent-nothing failure) →
  `TestRoundTrip::test_write_and_read_agree_on_every_spelling` calls ONE function from both sides
  over a spelling table; deleting the shared call on either side reds it.
- R3 **Bare-numeric noise re-enters the search lane** (the #2070 control silently reversed beyond
  identifiers) → `TestRawRetrievalUnchanged::test_no_bare_numeric_token_on_the_search_lane`
  (`27:12`, `$40`, `2024-01-15` contribute no all-digit token; `build_or_query` differs from
  `keep_numeric=True`).
- R4 **The write path indexes text that was not scrubbed** (ruling risk 2) →
  `TestWritePath::test_redacted_span_contributes_no_secret_alias` — a `[REDACTED:…]` span yields no
  alias containing the secret, because redaction replaces the whole value (`tortoise/security.py:488`).
- R5 **A point with no identifier is written differently** (raw-retrieval regression) →
  `TestWritePath::test_no_identifier_leaves_search_keys_absent` — a plain point still writes no
  `search_keys` at all.
- R6 **Unbounded index growth** (aliases appended per write, forever) → `append_identifier_aliases`
  is idempotent (alias set difference against the existing tokens) and capped at
  `MAX_ALIASES_PER_POINT`; `TestWritePath::test_append_is_idempotent` re-applies three times and
  asserts the value is unchanged.

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
