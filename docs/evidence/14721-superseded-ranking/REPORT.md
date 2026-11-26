# #14721 — the stale answer stops coming first, and the superseded one says so

**P0.** After a document contradicts an older one, retrieval returned the *stale* side as its best
hit and nothing said anything had changed. This is the retrieval/ranking half of that defect; the
representation half — a contradiction **closing** the older claim's interval — is `#14198` and is
still open.

| | |
|---|---|
| Lane | `fix/14721-superseded-ranking` |
| Graph | `xv14592_operating_model` on this lane's own private FalkorDB (`fdb-lane-f37f7e7da5`, `127.0.0.1:16414`) |
| Corpus | `docs/evidence/14592-operating-model-validation/corpus/corpus.json` — the committed corpus, unchanged, not re-authored |
| Harness | the `#14606` harness, reused: `run_ingest.py` for the write, `probe.py`/`record.py` here for the reads and the one write |
| Doors | every write through `TortoiseSDK`; every read through the real MCP server over stdio (`python -m tortoise.mcp_server`) |

---

## 0. What the live-work check found before a line was written

```bash
$ git ls-remote origin 'refs/claims/*'   → no 14721 entry, no 14198 entry
$ git ls-remote origin 'refs/wip/*'      → no issue-14721 entry, no issue-14198 entry
$ gh pr list --state open                → 12 open; none touches search_engine|belief_timeline|
                                           ranking|supersede (#14723 is the #14691 extractor T6 test)
$ ps -eo args | grep search_engine       → only this lane's own dispatch
```

Siblings: **`#14198`** (representation — *this change consumes it and does not duplicate it*),
**`#14304`/`#14285`** (the entity/relation-pair write legs — untouched), **`#11656`** (declared
relation kind guards — untouched), **`#14691`** (the noise capstone — untouched). Nothing live.

## 1. Research

**Search tool: rung 2 `web_search` unavailable in the tool process** (`PERPLEXITY_API_KEY not set`
— the key exists at `/home/cmux/keys.env` but the tool does not inherit this shell's environment),
and **rung 1 unavailable** (`Unknown MCP server 'seo-intelligence'`). Taken: **rung 3, `web_fetch`**,
on primary sources — *2 attempted / 2 returned*.

* Zep / Graphiti (arXiv:2501.13956, and the Graphiti README): **every fact carries a validity
  window — when it became true and when, if ever, it was superseded** — and the engine supports
  both "what is true now" and "precise historical queries … without complete graph recomputation".
* That is the convergent shape, and it is **already the product's own shape**: `supersede_point`
  writes a `CORRECTS` edge, marks the old point `outdated`, and closes `validTo`; `ended.py` serves
  the ended population with its successor; `live._terminal_excluded` hides ended rows from default
  reads (design §6). Nothing new was needed at the representation layer, so nothing new was added.

**The internal finding that decided the design** — `extractor_v2.OPERATOR_SEMANTICS_RULE` already
specifies the mechanism, in the product's own words:

> **POINT-LEVEL SUPERSEDE (cross-session reversal — F2):** when a new point REPLACES a claim or
> decision from an EARLIER session, set its `"supersedes"` … the write path folds a point-level
> CORRECTS supersession (the old point is marked outdated).

`doc-01 → doc-05` is exactly that shape. So the mechanism is **the recorded one**; this lane's job
was to make the read path honour it.

## 2. Reproduction — the defect, on the committed corpus

Fresh private graph, corpus ingested through `mine_knowledge_from_session` (5 documents, all `ok`).
Re-extraction spend, as the brief requires: **18 LLM calls, 129,602 prompt + 16,744 completion =
146,346 tokens** (deepseek-v4-flash; the provider reported no per-call cost). `out/before.json`.

`tortoise_search("Did ColdPath fail its cold-chain audit, and what did we do about it?")`, `limit=8`:

```text
1. [live] ColdPath Logistics holds a validated cold-chain certificate            <- doc-01, REFUTED
2. [live] Cold-chain rules forbid adding an account unless …
3. [live] The wholesale licence forbids subcontracting …
4. [live] Marta Reyes, Operations Lead, owns the cold-chain temperature log
5. [live] The chilled-goods operation was believed to be runnable …
6. [live] The cold chain must stay below 4 degrees Celsius at all times …
7. [live] Two of ColdPath's vehicles could not show a validated cold-chain certificate …
8. [live] We believed ColdPath Logistics was a viable delivery partner …
```

Rank 1 is the claim `doc-05` refutes. **Reproduced, and `#14606`'s G1 reproduced with it**: every
document reports `supersessions: 0`, and both rival pairs sit at `status: live` with no marker
between them. The extractor's `supersedes` channel did not fire. *That is `#14198`'s gap and is
reported there — this lane does not fix it.*

### 2.1 The retrieval defect is not the missing marker — it is that a score cannot express "replaced"

The marker was then applied by hand, through the product's own write door
(`supersede_knowledge`, the door the extractor's `supersedes` ref folds onto), on both rival pairs.
With the marker present, `recall(mode="state", include_superseded=True)`, `limit=12` — the
product's own historical read — returned (`out/prefix-limit12.json`):

```text
BEFORE THE FIX (docs/evidence/14721-superseded-ranking/out/prefix-limit12.json)
 1. [superseded] ColdPath Logistics holds a validated cold-chain certificate   <- REFUTED, led with
 2. [live]       The wholesale licence forbids subcontracting …
 3. [live]       The cold chain must stay below 4 degrees Celsius …
 4. [live]       We revert to NordicFreight as our delivery partner …
 5. [live]       We believed ColdPath Logistics was a viable delivery partner …
 6. [live]       The ColdPath choice is not viable …
 7. [live]       Our wholesale licence forbids subcontracting …
 8. [superseded] We are switching our delivery partner to ColdPath Logistics
 9. [live]       validated cold-chain certificate
10. [live]       Two of ColdPath's vehicles could not show a validated cold-chain certificate …
11. [live]       The subcontracting restriction is a licence constraint …
12. [live]       cold chain
```

The replaced claim led with, on FTS similarity alone (5.0 against every current row's ≤ 3.0), and
the claim that replaced it sat at **rank 10 — not in the top 8**, which is the page the `#14606`
probe read. A relevance score is a statement about *likelihood of matching*, and a claim's relevance
does not fall when a later document refutes it — so the order was decided by an accident of lexical
overlap.

## 3. Mechanism chosen, and the alternatives rejected

**Chosen — a pairwise order constraint: a row whose `superseded_by` names another row in the same
result set is placed immediately after it.** Nothing else moves.

| alternative | verdict |
|---|---|
| **recency** | ⛔ **Rejected.** `doc-05` is newer than `doc-01`, so a recency boost "fixes" this corpus — and demotes every older claim that is still true. It is a heuristic about *documents*, not a rule about *claims*, and it cannot answer "what did we originally decide" at all. |
| **a global "ended rows sort last" tier** | ⛔ **Built, measured, rejected.** It fixes the order and pushes the replaced claim past the page edge — on this corpus it disappeared from a `limit=20` page. One silent omission traded for another, and it breaks the historical read the caller asked for. |
| **interval closure from `#14198`** | ✅ **Consumed, not duplicated.** The `CORRECTS` edge + `validTo` + `outdated` is that closure; this change is the read-side half that honours it. |
| **an explicit superseded flag** | ✅ Already exists (`status=superseded`, `outdated`, `superseded_by`) — no new flag was added. |

Why it is a rule rather than a heuristic: **`superseded_by` is the record's own field**, built from
the incoming `CORRECTS` edge (`search_engine.fetch_point_epistemic_state`), the same edge
`supersede_point` writes. The ordering reads a fact the product already stores, not a guess about
which direction time points.

**What it does when the user genuinely asks about history**: nothing is dropped and nothing is
hidden — the replaced claim is still served, still labelled, ranked immediately below the claim that
replaced it (`test_a_history_read_keeps_the_replaced_claim`).

## 4. The change

| file | change |
|---|---|
| `tortoise/ranking.py` | `_superseded_below_successor()` — the pairwise constraint, applied after `StateRanker`'s score sort. The move budget is `n·(n-1)` MOVES (not passes): every move strictly increases a row's index, so that is a proof of termination, and it is what a `k`-link chain actually needs (`k(k-1)/2` moves). Rows whose id is empty or shared are not indexable and are left alone. A cycle in the data has no valid order; the budget runs out and a deterministic order is returned rather than looping. |
| `tortoise/ranking.py` (signature) | `rerank(..., page_limit=None)` — a pair whose successor falls at or beyond the page edge is left alone, because moving the replaced row there would push it out of the page too and hide **both**. |
| `tortoise/sdk.py` | `_recall_state` passes `page_limit=limit`. |
| `tortoise/search_engine.py` | `superseded_by` gains `superseded_at` (the `validTo` bound the supersede closes) and `source` (`coalesce(new.sessionId, new.session_id)` — both spellings, the rule `ended._SUCCESSOR_PROPS` states). Present-only, so legacy decorations stay byte-identical. |
| `config/surface-manifest.yml` | the two additive label keys recorded as `response_fields` rows (the "must still be recorded" rule in `CONTRIBUTING.md`). |

⛔ **One deliberate inversion, stated rather than hidden**: applying this constraint after the score
sort can place a belief-carrying row below an absent-belief successor, which inverts the `#11702`
tier for that one pair. That is intended — a replaced claim must not lead with its belief while
leading — and it is documented at the function and in `StateRanker.rerank`'s contract. Refusing to
cross the belief tier would leave the defect open for exactly the claims carrying the most evidence.

## 5. Before → after, verbatim

```text
BEFORE (pre-fix ranker, marker present)          AFTER (same query, same graph, limit=12)
 1 [superseded] ColdPath … validated cert    →     1 [live]       The wholesale licence forbids …
 2 [live]       The wholesale licence …           …
 3 [live]       The cold chain must …             7 [superseded] We are switching our delivery
 4 [live]       We revert to NordicFreight …                    partner to ColdPath Logistics
 …                                                   superseded_by: {…, source: "doc-05"}
 8 [superseded] We are switching our …           8 [live]       validated cold-chain certificate
 9 [live]       validated cold-chain …           9 [live]       Two of ColdPath's vehicles could not
10 [live]       Two of ColdPath's vehicles …                     show a validated cold-chain certificate …
                                                 10 [superseded] ColdPath Logistics holds a validated
                                                                  cold-chain certificate
                                                  superseded_by: {
                                                    id: pt_b44d1713…,
                                                    content_snippet: "Two of ColdPath's vehicles
                                                      could not show a validated cold-chain certificate…",
                                                    created_at:     2026-11-26T15:06:41.006129+00:00,
                                                    superseded_at:  2026-11-26T15:06:41.006129+00:00,
                                                    source:         "doc-05" }
```

Every replaced claim sits **below** the claim that replaced it — *below*, not adjacent: the replaced decision is at rank 7 while its successor is at rank 3, with three rows between them. The constraint guarantees ORDER, never adjacency, and a future change that forced pairs together would be re-introducing the global-tier shape §3 rejects. Both rival pairs behave identically.

**On the currency read the refuted claim is no longer served at all** (`out/after.json`):
`tortoise_search` rank 1 is a current claim and the stale certificate claim does not appear in the
top 8. Design §6 — default reads exclude ended records — is a RECORDED decision and is left exactly
as it is; what this lane adds is that the historical read no longer lies about the order and that
the label says what/when/from-which-source. **Both halves are needed, and this lane owns the
second.**

## 5.1 Suites run

At this head (the second review round). The first three commands are the embedded carve-out lane —
`TORTOISE_TEST_CARVE_OUT=1`, this worktree's own `.venv` — and they are stated as such because that
lane SKIPS the cases that need a live URI rather than running them, so its counts are not
interchangeable with the private-docker-lane counts quoted by the first revision of this report.
The two clusters below it were then re-run on the private lane (`tools/test_lane.py run`, docker
available on this box) so the graph-backed cases are INCLUDED, which is the comparable reading.

```text
TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_14721_superseded_never_outranks.py \
    -q -p no:cacheprovider
    → 12 passed

TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_relationships_bounded.py \
    -q -p no:cacheprovider
    → 46 passed

TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_13098_cypher_inventory.py \
    -q -p no:cacheprovider
    → 77 passed, 14 skipped        (the census pins, after Appendix A was regenerated)

TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest tests/test_surface_manifest.py \
    -q -p no:cacheprovider
    → 293 passed, 1 skipped        (after `tools/surface_manifest.py render`)

.venv/bin/python tools/test_lane.py run -- tests/test_recall_state.py \
    tests/test_belief_order_10137.py tests/test_11702_absent_belief_state.py \
    tests/test_ranking.py tests/test_2952_write_path_determinism.py \
    tests/test_retrieval_determinism_2952.py -q -p no:cacheprovider
    → 208 passed

.venv/bin/python tools/test_lane.py run -- tests/test_search_engine.py \
    tests/test_pointsuperseded_rebuild.py tests/test_13557_13558_reachable_reads.py \
    tests/test_14033_state_cue_gap.py -q -p no:cacheprovider
    → 407 passed

uv run python tools/surface_manifest.py check
    → OK — 39 tool rows, 73 retired, thirteen properties hold
uv run python tools/surface_manifest.py render  → wrote docs/product/mcp-sdk-surface.md
uv run python tools/ci_selection.py --integrity → ✅ all test files classified
python3 tools/sdk_privatise.py --check         → 11911 now vs 11912 baseline (1 FEWER — passes)
```

## 5.2 Independent review — what it found, and what was fixed

A fresh-context reviewer (no memory of writing the change) found five issues. Every one was verified
against the primary source before being applied; all five were real.

| # | finding | verification | disposition |
|---|---|---|---|
| P1 | the chain bound counted PASSES, not moves, so a 4-link chain still left a claim above its successor (`k` links need `k(k-1)/2` moves) | reproduced: `A←B←C←D` returned `B, A, D, C` | **fixed** — move budget `n·(n-1)`; `chain4` → `D,C,B,A`, `chain5` → `E,D,C,B,A`; regression test added. ⛔ The move BUDGET was itself replaced in the second round (§5.3 C1): it was `O(n⁴)` and spent 146 s of a 10 s wait bound on a cycle, so the chain is now resolved by DEPTH PLACEMENT |
| P2 | a non-injective id map (`""` for id-less rows, or a shared id) made `pop(index[id])` remove a different row — one duplicated, one dropped | reproduced: three rows in, one id-less row lost and the other doubled | **fixed** — only unique non-empty ids are indexable; regression test added |
| P2 | moving the replaced row to a successor beyond the page edge pushes both out of the page | reproduced: successor at index 15, replaced at 1, page 8 ⇒ neither served | **fixed** — `page_limit`; a pair whose successor is not served is left alone; regression test added |
| P2 | the constraint runs after the documented tiers, so it can invert the `#11702` belief tier while the docstring still claimed the old order | reproduced: a belief-carrying superseded row landed below an absent-belief successor | **accepted and documented** at the function and in the `rerank` contract — the inversion is the intended trade, not an accident |
| P2 | this report said replaced claims sit "immediately below" their successor, contradicted by its own artifact (rank 7 vs rank 3) | confirmed against `out/after.json` | **fixed** — the invariant is *below*, never adjacency |

One further defect was found by the suites rather than the reviewer: passing `page_limit` broke a
test double whose `rerank` dropped caller kwargs. The double was wrong (a decorating wrapper must
forward), and it now forwards `**kwargs`.

## 5.3 Second review round — what it found, and what was fixed

A second fresh-context reviewer, on the same head, raised four findings plus four non-vacuity gaps.
Every one was re-verified against the code before being applied; the dispositions are below.

| # | finding (severity) | verification at this head | disposition |
|---|---|---|---|
| F1/B1 | the new test file was declared in NO CI surface, so the required `manifest-integrity` check redded AND the file ran in no leg (P1, merge-blocking) | `grep -c 14721 config/ci-surfaces.yml` → `0`; `tools/ci_selection.py --integrity` → `❌ manifest drift: ['test_14721_superseded_never_outranks.py']` | **fixed** — declared under `ep` (the surface `tortoise/ranking.py` selects) with a `durations` row at the flat default, `# unmeasured`; `--integrity` is green |
| F2 | the SDK's `page_limit=limit` had ZERO coverage — deleting that one keyword reverted the page-edge guard in production with no row red | reasoned from `sdk.py` and confirmed by mutant: with the keyword removed, the new test fails | **fixed** — `test_state_read_tells_the_ranker_the_page_size` drives `_recall_state` with a replaced claim in the page and its successor past it |
| F3/C3 | `superseded_by.superseded_at` / `.source` were emitted with NO assertion anywhere, and two doc comments still described the old three-key shape | `git grep` found no assertion on either key | **fixed** — `test_superseded_label_carries_when_and_from_which_source` (both session spellings) + a present-only control; both doc comments updated |
| F4 | the PR body's proof-of-work block contradicted this report and the file it describes | compared the body's counts with §5.1/§6 | **fixed** — the body now quotes these counts |
| B2 | the manifest's two new `response_fields` rows were added without re-rendering the generated surface doc | `tests/test_surface_manifest.py` red pre-fix | **fixed** — `tools/surface_manifest.py render` |
| B3 | `ruff` red: three `RUF100`/`F401` in the new evidence scripts and the new test | CI `docs` job, quoted verbatim in the review | **fixed** — `ruff check` clean on every changed file (plus one `B905` this round's own edit introduced) |
| B4 | `docs` red: six `MD040` fenced blocks with no language in this report | `markdownlint-cli2` → 6 MD040 | **fixed** — six fences given a language; `markdownlint-cli2` → 0 issues |
| B5 | the census doc-vs-tree pins were not regenerated, so all 126 downstream pins drifted by the diff's own `+116` lines | `test_13098_cypher_inventory.py` red: `126 row(s) differ` | **fixed** — Appendix A regenerated from `tools/cypher_inventory.py --markdown` |
| B6 | `surface-guard` red: the untyped-receiver ratchet rose by one on `row.update(extra)` | `tools/sdk_privatise.py --check` → `ROSE by 1` | **fixed** — the helper spreads `**extra` into the dict literal instead; the two test files' raw-write helpers were consolidated so the count is now 1 FEWER than the baseline |
| C1 | the ordering pass was `O(n⁴)`: up to `n·(n-1)` passes, each re-indexing with `ids.count(rid)`; 10.2 s at 160 rows, 146 s at 320, against #3834's 10 s wait bound — and in the cycle shape it paid that and returned the input order | measured on the previous shape: 160-link chain 4.2 s (review: 67 s at 320) | **fixed** — chain-DEPTH placement: one `O(n)` walk computes each row's distance to a terminal successor, then one placement per row (`O(n²)` overall). 300-row chain: 0.018 s. A cycle is now STATED and left alone instead of burning a budget |
| C2 | the id-injectivity test exercised only EMPTY ids, so it could not fail for the SHARED-id class its docstring names | ran the ungated map (`if rid`) against the test rows: `A, pt_s, A` | **fixed** — a shared non-empty id case added (reds on the ungated map) |
| C3 | (same as F3) | — | **fixed** |
| C4 | the self-supersede guard was unasserted and load-bearing: without it the move pops the row and then looks for the id it just removed | reproduced: `StopIteration` on the old shape, `ValueError` on the depth placement | **fixed** — a self-naming row case added |

All four new behavioural assertions were then proven NON-VACUOUS by re-running them against the
mutant that reverts the behaviour: the label emission deleted (test reds), `page_limit=limit`
deleted (test reds), the shared-id gate removed (contents become `A, pt_s, A`), the self-supersede
guard removed (`ValueError`). The cost test's non-vacuity is the previous shape's own measurement.

## 6. Proof the tests fail on the pre-fix code

`tortoise/ranking.py` restored to `origin/main`, same suite, same invocation:

```text
FAILED test_14721_superseded_never_outranks.py::test_the_superseded_claim_never_outranks_its_successor
FAILED test_14721_superseded_never_outranks.py::test_a_history_read_keeps_the_replaced_claim
FAILED test_14721_superseded_never_outranks.py::test_a_supersession_chain_resolves_to_the_newest_claim
FAILED test_14721_superseded_never_outranks.py::test_a_four_link_chain_resolves_too
FAILED test_14721_superseded_never_outranks.py::test_a_successor_beyond_the_page_does_not_drag_the_pair_out_of_it
FAILED test_14721_superseded_never_outranks.py::test_the_order_is_deterministic_for_rows_that_are_not_in_violation
FAILED test_14721_superseded_never_outranks.py::test_a_large_pool_resolves_within_the_wait_bound
7 failed, 5 passed
```

The five that pass pre-fix are deliberate **negative controls and regression pins**, and every one
is supposed to:

* `test_a_superseded_claim_whose_successor_is_absent_keeps_its_rank` — with no successor in the
  result set there is nothing to outrank, so no reordering may happen before *or* after.
* `test_two_unidentifiable_rows_are_never_reordered_by_each_other` and
  `test_rows_sharing_one_non_empty_id_are_never_dropped_or_duplicated` — a pre-fix ranker imposes
  no order at all, so it trivially cannot lose a row. They exist to pin the *fix* from regressing
  into the non-injective-id defect, not to demonstrate the fix. The shared-id case was verified
  against the UNGATED map instead (`if rid`): contents come out `A, pt_s, A` — one row lost, one
  duplicated.
* `test_a_row_that_names_itself_as_its_successor_keeps_its_rank` and
  `test_a_cycle_keeps_its_rows_in_place` — the pre-fix ranker leaves both shapes alone because it
  has no constraint at all. Their mutants are the ones that matter: dropping the self-supersede
  guard raises (`StopIteration` on the old shape, `ValueError` here), which propagates out of
  `rerank` and fails the whole state read.

A suite where all twelve failed pre-fix would be evidence of a blanket penalty rather than the
pairwise rule.

## 7. Findings this lane did NOT fix (recorded, not widened)

1. **`#14198` is the blocker for the end-to-end behaviour.** On a fresh ingest of this corpus the
   extractor emits `supersessions: 0`; both rival pairs stay `live` with no `CORRECTS` edge, so
   *nothing* in any read path can know they conflict. Measured here twice (this run and `#14606`).
   Reported on `#14198`.
2. **`fetch_point_epistemic_state` fails open and silently.** While this change was being
   developed, a Cypher error in that query logged `Epistemic-state fetch failed` and every row
   came back with `status: None`, `superseded_by: None` — i.e. the whole epistemic decoration
   vanished and the response looked like a graph with no statuses. The reads still returned rows,
   so the failure is invisible to a caller. Not fixed here (it is a separate defect with its own
   blast radius); noted for the reviewer.
3. **`state_intent.detect_state_question` does not route the natural history phrasings.**
   `"Which claims have been superseded?"` → `ended` ✅, but
   `"What did we originally decide about our delivery partner, and what superseded it?"` and
   `"Which of our decisions were superseded, and by what?"` → `None`. So the ended population is
   still not reached from the question a customer would actually ask. That is `#13557`/`#13558`'s
   surface, not this one — recorded here rather than patched, because its cue table carries 13
   disclosed residuals and widening it is its own measured piece of work.
4. **`belief_timeline.py`, named as an INTERNAL source in the brief, does not exist** at the repo
   root; the only copy is `archive/sdk-methods/12901-sdk-final/belief_timeline.py`. The brief's
   reference is stale.
5. **`tools/collision_preflight.py` was not run** — the mandate-preflight block in the brief
   declares `#3377` unsatisfiable on this box (two required state files absent, and no provisioning
   path). The live-work check in §0 was run instead, by hand, with the commands shown.

## 8. Reproduce

```bash
# this lane's private container; the tool exports all four seam variables
uv run python tools/test_lane.py uri && eval "$(uv run python tools/test_lane.py uri | grep ^export)"

export DEEPSEEK_API_KEY='…'     # extraction credential — never read from a file
./.venv/bin/python docs/evidence/14592-operating-model-validation/run_ingest.py   # 1. write
./.venv/bin/python docs/evidence/14721-superseded-ranking/record.py --old <stale-id> --new <successor-id>
./.venv/bin/python docs/evidence/14721-superseded-ranking/probe.py --label after    # 2. read
```
