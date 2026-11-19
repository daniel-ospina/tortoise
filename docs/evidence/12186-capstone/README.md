---
title: "#12186 capstone — the merged search levers, measured where a USER meets them, on one fixed query set"
type: evidence
domain: platform
doc_status: live
created: 2026-11-18
subjects.team: epistemic-team
aboutSubjects: candidate recall, search surface, duplicate-noun rate, MCP tortoise_search, capture-time prior
aboutObjects: "#12186, #11901, #12368, #12304, #12343, #12297, #12348, #12349, SEARCH-ARCHITECTURE.md"
---

# #12186 capstone — are the merged levers visible to a user?

**Instrument:** `tools/capstone_12186_recall.py` · **Fixed query set:**
[`queries.json`](./queries.json) (sha256 `2486a359…`, pinned in the artifact) · **Artifact:**
[`measurement.json`](./measurement.json) · **Code head:** `31bce52ddb` · **Measured:** 2026-11-19.

**Plain language:** one of the merged levers is visible to a user, one is not, and one is only
visible on the write path. The `_`-preserving OR terms (`#12368`) move the user's own search from
**83.4 % → 98.9 %** of queries reaching the right record in the top 3 — **+15.5 points**, 41 queries
gained, none lost, p < 1e-12, on the same corpus. The cross-encoder rerank (`#12297`/`#12343`) is
**not visible to a user, because it is off by default**; armed, it is +1.13 points on clean windows
(3 gained, 0 lost) — too small to call a win at p = 0.25. `#12304`'s comparison-side fold is visible
on the **write path**: the duplicate-mint rate on real surface variants falls from **100 % → 53.3 %**.

⛔ **This is not a comparison to the 0.7133 baseline in the epic body, and the artifact refuses that
comparison by name** (`comparisons.different_corpus_from`). 0.7133 was measured on the
`#12298`/`#12360` pin; this run's population is a different freeze of the same harvest rules. The
before/after below is same-corpus — same pin, same graph, same process, one env flag apart — which
is the only shape in which "a lever works" can be shown.

---

## 1. The fixed query set, and why a pin was needed

`tools/candidate_recall_11901.py` harvests its population from the **live** capture corpus at run
time, so two runs on two days measure two corpora. That is right for a mechanism measurement and
fatal for a capstone: a number that cannot be re-derived from the commit is a claim, not evidence.

So `build-pin` froze the harvest into [`queries.json`](./queries.json):

| field | value |
|---|---|
| rules | `candidate_recall_11901.harvest` / `build_population` / `_allocate_shares` / `_interleave` — **imported, not re-implemented** |
| corpus read | `~/.tortoise/session-events`, **104 files**, 122 912 records, 0 parse errors, 0 records without text, 729 s |
| population | 1 000 names — 600 `definite` / 300 `ident` / 100 `path` (the harness's declared OVERSAMPLE of minority shapes) |
| queries | the 300-name stratified sample, 180 / 90 / 30 — carried as **ids into the same population**, so the two lists cannot disagree about a name |
| digest | population `90122e99a38caf45`, queries `5604d0ad3769bd52`, file sha256 `2486a359…` |

⚠️ **What is real, and what is not.** The names are real (harvested from real agent sessions by
declared rules) and the graph is a real FalkorDB written through the real projection
(`_upsert_object`), queried by the real `sdk.tortoise_fts_query`. The population is a **sample of
the strings those captures contain**, not the `:Object` rows a production graph holds — no lane can
census that. Every figure here measures the retrieval and matching *mechanism* on a real name
distribution; none is a census of production.

⚠️ **Corpus identity.** Not `#11901`'s published population (61.3 % / 65.0 % / 69.0 % / 73.0 %) —
that artifact predates any pin and its own brief §2.2 records that its population is not
re-provable. Not `#12298`'s pin (0.7133). Read the `comparisons` block in the artifact before
putting any cell beside either.

---

## 2. Recall@k — the table the epic asks for

The door a **user** hits is the MCP tool `tortoise_search` and REST `GET /v1/search`; both resolve
to `sdk.tortoise_fts_query`, and the artifact re-reads that binding at run time
(`user_door.matches_measured_call: true`, `rest: GET /v1/search`).

| arm (one pin, one graph, one process) | recall@3 | recall@5 | recall@10 | recall@15 |
|---|---|---|---|---|
| capture prior — `TORTOISE_UNDERSCORE_TERMS=0` (pre-`#12368`) | 0.8033 | 0.8600 | 0.8933 | 0.9300 |
| capture prior — **shipped** | **0.9800** | **0.9933** | **0.9933** | **0.9933** |
| user door — `TORTOISE_UNDERSCORE_TERMS=0` (pre-`#12368`) | 0.7933 | 0.8500 | 0.8833 | 0.9200 |
| user door — **SHIPPED (what a user gets today)** | **0.9700** | **0.9800** | **0.9800** | **0.9800** |
| user door — fused rerank **armed** (NOT the shipped default) | 0.9733 | 0.9733 | 0.9733 | 0.9733 |

Where the `#12368` gain actually lands, by class (user door, recall@3 / recall@15):

| class | pre-`#12368` @3 | shipped @3 | pre-`#12368` @15 | shipped @15 |
|---|---|---|---|---|
| `definite` (180) | 0.9611 | 0.9778 | 0.9778 | 0.9889 |
| **`ident`** (90) | **0.4111** | **0.9444** | **0.7778** | **0.9556** |
| `path` (30) | 0.9333 | 1.0000 | 1.0000 | 1.0000 |

That is the lever's mechanism, confirmed at the surface: it repairs **identifier** queries (a
`snake_case` name that RediSearch splits into fragments) by **+53.3 points at k=3**, and moves the
other two classes by +1.7 and +6.7 points.

### Paired, on windows clean in BOTH arms

A leg occasionally degrades with `timeout` on a box at load ≈90 — measured, not imagined
(`leg_degrade_reasons`). A raw arm-to-arm difference would mix the lever with that, so each pair is
also reported paired, excluding any query whose window was degraded on **either** side.

| pair (what the difference IS) | clean queries | before → after (recall@3) | Δ | gains / losses | McNemar p |
|---|---|---|---|---|---|
| `#12368` on the **capture prior** (write path) | 274 (26 excluded) | 0.8321 → 0.9891 | **+15.69 pts** | +43 / −0 | < 1e-12 |
| `#12368` on the **user door** | 265 (35 excluded) | 0.8340 → 0.9887 | **+15.47 pts** | +41 / −0 | < 1e-12 |
| `#12297` rerank on the user door (armed) | 266 (34 excluded) | 0.9887 → 1.0000 | +1.13 pts | +3 / −0 | 0.25 |
| **the door itself** — internal path vs the user's surface | 272 (28 excluded) | 0.9890 → 0.9890 | 0.00 pts | +0 / −0 | 1.0 |

The last row is a finding, not a null: **on this corpus the user surface and the internal candidate
path reach the canonical identically** (0 gains, 0 losses across 272 clean queries), so a
capture-prior number is also a user-door number — the epic does not need a separate user-only fix for
reachability.

### Leg degradation, per arm — declared, because it is the raw column's noise

A `timeout` on the dense leg is not a lever difference: the window falls back to keyword-only, or
returns no rows at all. Both counts are per arm, and no raw arm figure above may be read without
them.

| arm | keyword-only windows (of 300) | queries with no rows at all | `vector:degraded:timeout` |
|---|---|---|---|
| capture prior — `TORTOISE_UNDERSCORE_TERMS=0` (pre-`#12368`) | 16 | 8 | 16 |
| capture prior — **shipped** | 9 | 2 | 9 |
| user door — `TORTOISE_UNDERSCORE_TERMS=0` (pre-`#12368`) | 15 | 8 | 15 |
| user door — **SHIPPED (what a user gets today)** | 18 | 6 | 18 |
| user door — fused rerank **armed** (NOT the shipped default) | 14 | 8 | 14 |

⚠️ **Read the paired column, not the raw one, for the rerank.** Its raw arm figure (0.9733 against
0.9700) looks like a +0.33 pt gain and is not the lever: 14 of its 300 windows were keyword-only and
8 queries returned nothing at all — its OWN row in the table above, not the shipped arm's 18/6. On
windows clean in both arms it is +1.13 pts, 3 gained / 0 lost.

---

## 3. Duplicate rate — two readings, both reported

### 3a. In window (`neardup-v1`) — how many of the k rows a caller is handed are redundant

Rule (`neardup-v1`, frozen in the tool): fold case + whitespace + the separator class
(`-` `_` `.` `/`) in one pass, drop a **leading article**, and call two rows near-duplicates when
their keys are equal or their token Jaccard ≥ **0.8**. Declared, versioned and pinned by
`tests/test_capstone_12186_recall.py`, because a duplicate rate without its rule is a number nobody
can re-derive.

| k | rows handed out | near-duplicate rows | **rate** | duplicate pairs | queries affected |
|---|---|---|---|---|---|
| 3 | 875 | 20 | **0.0229** | 11 | 9 / 300 |
| 5 | 1 452 | 33 | 0.0227 | 19 | 14 / 300 |
| 10 | 2 887 | 41 | 0.0142 | 24 | 17 / 300 |
| 15 | 4 320 | 55 | 0.0127 | 31 | 24 / 300 |

So ~2.3 % of what a user is handed at the production-style width is a near-duplicate of something
else in the same window, and the share does not grow with k — a wider window adds rows of which
proportionally fewer are near-duplicates.

### 3b. Mints a duplicate (`#11901`'s own definition) — the write path

A real surface **variant** of a stored name is issued as the mention; if phase 1 does not bind it
onto its own canonical, that capture mints a second node.

| configuration | pairs | canonical in window | attached | **mint rate** |
|---|---|---|---|---|
| frozen pre-`#12295` matcher (`_norm` equality only) | 411 | 397 | 0 | **1.0000** |
| **shipped matcher (post-`#12304` folded rung)** | 411 | 397 | 192 | **0.5328** |

The fold converts **192 of 411** guaranteed duplicates into an attach. Per variant class — and this
is where the remaining gap is named:

| variant class | pairs | in window | shipped attaches | mint rate |
|---|---|---|---|---|
| `article-drop` (`the write path` → `write path`) | 180 | 171 | 171 | **0.0500** |
| `hash-dropped` (`#1234` → `1234`) | 6 | 6 | 6 | **0.0000** |
| `hyphen-to-space` (`the merge-base` → `the merge base`) | 15 | 15 | 15 | **0.0000** |
| **`space-to-hyphen`** (`the write path` → `the-write-path`) | 180 | 175 | 0 | **1.0000** |
| **`basename-only`** (`scripts/x.py` → `x.py`) | 30 | 30 | 0 | **1.0000** |

⛔ **`space-to-hyphen` is not a bug to "fix harder" — it is a recorded decision's boundary.**
`entity_resolver.normalize_entity_name` anchors the article strip on a real SPACE (or `_`) on
purpose: `#12047` round-4 finding 5 records that the previous token-based strip made
`normalize("a-b") == "b"`, which merged an unrelated `AB` node. So `the-write-path` folds to
`the write path` (article kept) while `the write path` folds to `write path` — the two never compare
equal. Widening the strip would reverse `#12047`'s KEEP; the shape that would close it is a **second
comparison key**, not a wider one. `basename-only` is the same kind of deliberate refusal:
`attach_matches` will not let a shared basename decide inside a 3-row window, because a window is not
the population.

---

## 4. What the merged levers do to a user, lever by lever

| lever | shipped state | visible to a user? | evidence |
|---|---|---|---|
| `#12368` `_`-preserving OR terms | **ON** | **YES — +15.47 pts at the user door**, 41 gains / 0 losses, p < 1e-12 | `paired["user_search_pre_lever -> user_search"]` |
| `#12304` fold + multi-key blocking | ON (write path) | **YES on the write path** — mint rate 1.0000 → 0.5328 | `arms.mint_rate` |
| `#12343`/`#12297` cross-encoder rerank | **OFF by default** | **NO** — a user does not get it; armed it is +1.13 pts on clean windows, p = 0.25 | `arms.user_search_rerank`, `merged_lever_visibility` |
| `#12348` verdict step | ON (capture, LLM) | not measurable here — it needs a model call per capture, which this instrument does not make | declared, §5 |
| `#12349` M3 / SPLADE | measured and closed | n/a — no code shipped | `#12349` |

---

## 5. What this measurement does NOT establish — declared, never skipped

1. **Not a production census.** The population is a harvest sample (§1). A production duplicate rate
   needs a tenant's real `:Object` population, which no lane can reach.
2. **Phase 2 (the LLM verdict, `#12348`) is not measured.** It fires only with a model, on a real
   capture; this instrument measures phase 1 and the window. The 53.3 % mint rate is therefore an
   **upper bound on duplicates that survive**, not the production rate — phase 2 can still merge
   them.
3. **The rerank arm is confounded by box load, and is reported as such.** 34 of 300 windows were
   degraded on one side (`vector`/`fts` `timeout`); the raw arm figure is *not* the rerank's effect,
   the paired clean-window figure is. It is still not a win at p = 0.25.
4. **18 of 300 user-door windows were keyword-only** (`vector:degraded:timeout`), so the mode label is
   `MIXED` rather than `hybrid` — 282/300 were true hybrid reads, with dense coverage 100 %
   (`graph.vector_coverage: 1.0`). The dense leg is present and was warm **before** the writes
   (`embedder_at_write: SentenceTransformer`); the first attempt on this instrument lost every vector
   to a cold model (`#13224`), which is why the warm-up is now in the code path.
5. **`ident`/`path` are oversampled** (30 % / 10 % against a measured production share of ≈2 %
   identifiers). The pooled headline is **stratified**, never class-share weighted; quote the
   per-class table for any one class.
6. **n = 300 queries.** One query is ±0.33 pts, which is why every delta above carries its paired
   counts and p rather than standing alone.
7. **The box was loaded** (load average ≈90, 24 live agent sessions) for the whole run: 35 of 300
   user-door queries were excluded from the `#12368` pair for a leg timeout on one side. The paired
   numbers are the defensible ones; a quieter box would compare more queries, not different ones.

---

## 6. Reproduce

```bash
# one-time (only if the pin is ever deliberately re-frozen)
uv run python tools/capstone_12186_recall.py build-pin

uri="$(uv run python tools/test_lane.py uri)" || exit 1
eval "$uri"
HF_HUB_OFFLINE=1 uv run python tools/capstone_12186_recall.py run \
    --graph "cap12186-$(date +%s)" \
    --out docs/evidence/12186-capstone/measurement.json

# the guards for this tool and its numbers (no graph needed, ~40 s)
uv run python tools/test_lane.py run -- tests/test_capstone_12186_recall.py -q
```

`run` took **21 m 04 s** on this box at load ≈90: 366 s of writes (1 000 objects embedded), 676 s
over 5 arms × 300 queries, 145 s for the 411 variant reads.

⛔ **The committed [`measurement.json`](./measurement.json) is not byte-identical to a run at its
`code_head`** — one block was inserted by hand afterwards, and the record declares it in
`post_run_edits`: the `cleanup` block was added by hand, because the tool at `code_head` has no
`cleanup` builder at all (`git show 31bce52ddb:tools/capstone_12186_recall.py | grep -c cleanup`
→ `0`). **Every figure in the record is run output**; a reproduction at `code_head` yields the same
numbers without that one declaration block, and any later run writes the block itself and records
`post_run_edits: []`.

Refs `#11901` (harness) · `#12368` (composite terms) · `#12304` (fold + multi-key) · `#12343`/`#12297` (rerank) · `#12348` (verdict) · `#12349` (M3, closed) · `docs/architecture/SEARCH-ARCHITECTURE.md`
