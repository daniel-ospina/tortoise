---
title: "#11739 — the `quote` population rate on real captured points (the issue's \"Do this FIRST\")"
type: evidence
domain: platform
doc_status: live
created: 2026-11-11
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph, composed embedding text, verbatim quote
aboutObjects: "#11739, #11682, #11706, #10360, #8488, #11805, sdk.create_point"
---

# #11739 — the `quote` population rate

**The measurement #11739 asked for before anything else.** #11682 put it plainly: *"How often is
`quote` non-empty? If it is mostly empty, filling it is the entry point and #10360 becomes the first
task, not a side issue."* The question decides whether the recompose seam is worth building at all,
because a composed text over empty quotes is `content` wearing a different name.

**Answer: `quote` is populated.** 94.86% of live Points carry a non-empty `quote`, and **98.12% of the
Points that carry a vector do**. #10360 does **not** become the entry point on this graph.

```text
source    = hosted graph (api.premiselabs.co), ONE complete pass, offset walk over the SDK's
            total order (createdAt DESC, id(n) DESC — #5346), asked WITH terminal rows so both
            populations come from the same rows. The pass starts at 500 rows/page and MAY
            shrink (see the wait-budget ladder below), so its cost is not a constant — the
            artifact records it: `pages_walked`, `page_size_start`, `page_size_final`.
read      = tortoise_query(limit, offset, include_retracted) → {results, total, hasMore}
scope     = every NON-OPERATOR :Point the walk saw (15,868) — the read path applies the
            canonical operator-scope clause
            ((n.is_operator IS NULL OR n.is_operator = false) AND n.op_type IS NULL,
            `sdk._operator_scope_clauses`), so operator Points are NOT in this population.
            Within it the headline divides by rows that are NOT terminal by STATUS **and** do
            not carry the legacy `outdated` flag — the two conjuncts of the default read's own
            predicate (`search_engine._exclude_status_clause` →
            `live._terminal_excluded(…, include_outdated_flag=True)`); the flag half is reported
            as `excluded_by_outdated_flag` and is **empty on this graph**, so the two conjuncts
            coincide here as a MEASUREMENT rather than an assumption. 15,842 rows are live and
            the 26 excluded ones are reported separately.
artifact  = census.json, carrying its own `provenance` (measured_at, probe_sha256,
            endpoint, head_sha) — `tests/test_11739_quote_population_probe.py` recomputes
            the digest from the committed probe and reds if the artifact outlives its code.
            `head_sha` is **informational**: it names the commit the run executed against, and
            the bundle is measured in that order on purpose — probe committed first, census
            produced on a CLEAN tree, artifact committed after — so `git show <head_sha>:<probe>`
            digests to `probe_sha256` while that commit is reachable. It is not always
            reachable, and that is normal rather than a defect: `.mergify.yml` **squashes**, so
            a branch commit never becomes an ancestor of `main` while `census.json` does land,
            and CI checks out `fetch-depth: 1`. **The ENFORCED binding is therefore
            `probe_sha256`** — a test recomputes it from the committed probe in EVERY checkout,
            so an artifact that outlives its code reds. The relational check (the named commit's
            probe is the probe that made the artifact) runs where the history allows it and
            skips where it does not, which is why the README states no positional claim like
            `HEAD~1`.
```

## The number (`census.json`)

| cell | value |
|---|---|
| `rows_walked` | **15,868** (every row the walk read; the headline's denominator is 15,842 of them) |
| `pages_walked` / `page_size_*` | **from the artifact, never from this row** — the walk's cost varies with server load, and a `page_size_final` below `page_size_start` means the wait-budget ladder fired and the run served more pages. The probe counts pages, NOT requests: a shrunk page costs extra requests the artifact does not count |
| `live_points` | **15,842** (15,868 walked − **26** terminal) |
| `quote_non_empty` | **15,027** → **94.86%** of live Points |
| `points_with_vector` | **15,295** (96.5% of the live graph is embedded) |
| `points_with_vector_and_quote` | **15,008** → **98.12%** of embedded Points |
| `quote_chars_all` | min **8** · median **137** · max **200** · n 15,027 (composed length) |
| `at_cap` | **2,260** — stored length ≥ 200: **15.04% of quoted Points**, **14.27% of all live Points** |
| `quote_shapes` | `str` **15,051** (of which **24** are empty/whitespace-only) · `absent_or_null` **791** · `list` **0** · `other` **0** |
| `excluded_by_outdated_flag` | **`{}`** — no NON-TERMINAL row carries the legacy `outdated` flag, so the live subset is the same under either conjunct of the read path's predicate (a terminal row is bucketed by its status before the flag is read, so this field says nothing about those 26) |

By `pointKind`:

| kind | points | quote non-empty | % | embedded | embedded + quote | % |
|---|---|---|---|---|---|---|
| `statement` | 15,708 | 15,027 | **95.66%** | 15,171 | 15,008 | **98.93%** |
| `evidence` | 120 | 0 | 0.0% | 113 | 0 | 0.0% |
| `decision` | 7 | 0 | 0.0% | 5 | 0 | 0.0% |
| `option` / `diary` / `finding` / `useCase` / `jobToBeDone` | 7 | 0 | 0.0% | 6 | 0 | 0.0% |

**The split is by PROVENANCE.** Every quoted **live** Point is a `statement` (`by_point_kind`
covers the 15,842 live rows; the 26 terminal rows are bucketed by status instead and are NOT
kind-split, so this census carries no kind datum for them); the 134 hand-authored Points
(`evidence` 120 + `decision` 7 + `option`/`diary`/`finding`/`useCase`/`jobToBeDone` 7 = 134) carry none.
That 0% is a property of how those Points are created — capture writes `quote`, hand authoring never
did — and it is why a single headline ratio over the whole graph understates the coverage of the
population the composed text targets: **98.93% of embedded statements**.

> ⚠️ Whether any of those 134 *ever* had a quote is **not measurable here**: this is one snapshot, and
> the only rows that could speak to a prior quote state at all are the 26 terminal ones
> (`by_status_terminal.superseded`: 26 Points, **25 with a quote, 96.15%**). So the provenance reading
> above is an inference from what the rows carry today plus how the two writers work — **not** a
> measurement that quote never existed for them.

## What this changes for #11739

1. **#10360 is not the entry point.** The premise the issue was hedging against — "if `quote` is
   mostly empty, the composed text is mostly `content`" — is measured false. The recompose seam
   (#11739's remaining step 1) composes over real source words for **98.93% of embedded statements**.
2. **The truncation budget is a safety net, not a live constraint.** The bound comes from the two
   contract constants, not from a central tendency: `NAME_WORD_BUDGET = 48` caps the names line
   (`embed_compose.py`), so the quote keeps at least `EMBED_WORD_BUDGET − 48 = 464` words of room, while
   the longest possible quote — **200 chars at the clamp** — is at most ~100 words. The quote side
   therefore cannot be cut, whatever the names side turns out to be (and that side is the unmeasured
   one). The median of **137 chars** (~25 words) is distribution colour, not the argument. The reserve
   shipped in #11805 stays correct — it is a property of the **contract**, not of today's
   distribution.
3. **A datum for the gate step (step 4), decided nothing today.** The quote is clamped at 200 chars
   (`_write_span`, `extractor_v2._MAX_QUOTE`) and **2,260 quoted Points (15.04% of them, 14.27% of all
   live Points) sit at or above that cap**. The stored value goes through `_write_span(…, 200)`, whose
   rule is narrower than "cut at 200": if the cap lands inside a token, the span moves back to the
   previous whitespace **only when the split token's kept prefix carries a decimal digit AND the
   discarded remainder carries an alphanumeric character** (a value straddles the cut); otherwise the
   hard 200-char cut stands, and a quote whose 200th character is whitespace ends at a word boundary.
   The helper's own docstring records that arm firing on `_MAX_QUOTE` cuts, so it is reachable here, not
   excluded — this graph's quotes do carry digits (`p95`, `4.0`, `1200`). **Which of those two happened
   at the cut is not measured by this census** (it carries min/median/max/n/at_cap, not a boundary
   classification; a spot check during
   authoring showed one cut point ending `…— a d`, and that spot check is not a committed artifact). The
   flag is OFF; this is recorded so the 0.89 duplicate-similarity gate knows it will be reading UP TO 200 chars (the
   STORED basis, which the artifact's `quote_chars_all.at_cap_basis` field names — a composed text
   can be shorter, so it is not the length this 2,260 counts) for about one Point in seven.
4. **Zero list-valued quotes among the 15,842 live Points.** `quote` is a declared flatten-list
   property, so a census that treated a list as empty would under-report the population — here
   `quote_shapes` publishes every class over the live population, including the zeros: `str` 15,051 (24
   of them empty or whitespace-only) · `absent_or_null` 791 · `list` **0** · `other` **0**. The rows
   reconcile exactly: 15,027 quoted + (791 + 24) not quoted = 15,842. So the 94.86% is a complete
   count, not a lower bound with an invisible gap — and "0 list-valued" is a **count of zero**, not an
   absent key. ⚠️ The 26 terminal rows are shape-classified only by their status bucket
   (`by_status_terminal`), not by `quote_shapes`; a list-valued quote among them would show up there as
   a quoted value, not as a `list` shape.
5. **The names half has no property input at all.** `points_with_any_about_property` = **0** across all
   15,842 live Points: not one `about*` string property exists on a **live** Point (the scan runs over
   the live rows; the 26 terminal rows are status-bucketed and are not scanned for `about*`, so this
   is not a statement about them). That is consistent with
   #11805's review finding (the create door stores `about*` values as properties; capture writes none)
   and it strengthens the reason the names half ships unwired — but it is **not** a measurement of
   resolved `about*` EDGES, which is what the composer needs.

## Limits — what this does and does not establish

- **`by_month` has exactly one bucket (`2026-11`, all 15,868 rows walked, terminal rows included)**, so
  this census **cannot** separate pre-`#8488` captures from post-`#8488` ones and **no trend is
  claimed**. A single month across a graph this size is *consistent with* the graph having been
  rebuilt/restamped, but that cause is **not** established by this artifact — the observation is the
  one bucket.
- **The population is non-operator.** The read path excludes operator Points by design (the canonical
  operator-scope clause), so 15,842 is `:Point` **minus** operators. A raw-Cypher re-measure that
  counted operators would land on a lower ratio; this census deliberately measures the population the
  default read serves, which is the one the composed text would be compared against.
- **The flag conjunct is measured empty, not assumed away.** The default read also excludes rows with
  `outdated = true` (`invalidate_point` sets the flag and leaves `status` alone). This census applies
  that conjunct and publishes `excluded_by_outdated_flag`; it is **`{}`** here, which is what lets the
  headline be called "exactly the population the default read serves" rather than "the population the
  default read serves, minus a conjunct nobody checked".
- **One graph, one team's captures.** This is the dogfood fleet graph, not a customer corpus; a
  different capture mix may populate `quote` at a different rate.
- **Properties, not edges.** The `about*` cell counts string properties only. Resolved-EDGE population
  — the number the recompose seam's value actually depends on — is **unmeasured**: the MCP surface
  exposes no relationship census (`graph_overview` has no `relationships` section), so it needs either
  a Cypher-capable read or a per-entity walk. Named here rather than guessed.
- **Quote presence ≠ quote quality.** Whether a 137-char verbatim span is a *good* embedding input is
  #10360's question (value fidelity 49.4% vs a 54.4% baseline) and is untouched by this census.
- **The predicate counted is "non-empty AFTER whitespace collapse"** — `" ".join(value.split())`,
  which is `tortoise/embed_compose.py::quote_text` (`_collapse`) line for line, i.e. **the
  composer's own arm predicate**: `compose_embedding_text` takes `base = quote_text(quote)` and
  falls back to `content` only `if not base` (`_collapse(content)` when a names line is present; the
  raw string on its byte-identity arm). So 94.86% is exactly "the share of live Points where the
  composer takes the quote arm rather than the `content` fallback". A quote that is *present but
  equal to the title or `content`* still counts, and that **stricter** figure ("carries a *useful*
  quote") is **not recomputable from this artifact**: `census.json` carries no `content`/title
  field, so measuring it needs a probe change, which reds the digest pin and forces a re-run. Read
  94.86% as "carries a quote", never as "carries a useful quote".

## Reproduce

```bash
export TORTOISE_MCP_API_KEY=…            # read at CALL time; never from a file in the repo
python docs/evidence/11739-quote-population/probes/quote_population.py \
    --out docs/evidence/11739-quote-population/census.json
```

Cost: the artifact records it — `duration_s` and `pages_walked` are written by the run that produced
`census.json`, so quote those rather than a range. Both vary with server load: a run that has to
shrink the page size serves more pages (a shrunk page costs extra requests the artifact does not
count), and the per-page time varies with the graph's load. Read the artifact's own `duration_s` and
`pages_walked`; this README quotes no runtime figure of its own, because every one it quoted went stale
on the next re-run.

Exit codes are the measurement's own guard rails, because a census that silently reports zero is
worse than no census: `0` measured · `2` **usage, or the `--out` destination was not writable**
(`PROBE-WRITE`) — the two share a code because both are the operator's own argument, but they are not
the same event: a `PROBE-WRITE` means the graph WAS read and the walk succeeded, and the measurement
was discarded by the destination, so nothing was published and re-running with the same arguments to
the same path fails the same way. Fix the PATH, not the arguments · `3` graph unreachable / no key /
a scheme-less endpoint / the query could not be read at any page size / a reply that parses but is
not a JSON-RPC object (**never** read as 0%) · `4` the page walk could not be proven complete (a short
page with `hasMore=true`, a repeated id, a `results` list holding something that is not a row, or a
walked count that disagrees with the reported `total`). The walk refuses to publish a count it cannot
show is a partition of the order — the #5346 loss class.

Two bounded recoveries exist, both for the server's **wait budget** (a 2xx reply whose content is the
*"wait budget … exceeded"* notice, i.e. the query did not run — it carries no rows, so neither recovery
can turn a refusal into a wrong answer):

1. the **same page** is retried **once**;
2. if it misses again, the page **size** is halved and the same rows asked for again — at the default
   `PAGE = 500` the rungs are **500 → 250 → 125 → 100**, i.e. **four rungs, no more**, and the last
   one IS `MIN_PAGE` (so "a page that misses at the floor" describes a size the ladder actually asks
   for). A page that misses on the last rung is the graph not serving the query and exits `3`; at a
   `PAGE` already below the floor there is nothing to halve to and the ladder is one rung. This is a
   size ladder rather than more retries because a page can be heavy enough to lose the wait budget
   on a loaded graph, so a persistent miss is a page-weight problem; re-asking for the same heavy page
   is the retry-loop that never converges. **Nothing else is ever retried.**

The run records `page_size_start` / `page_size_final`, so a walk that had to shrink says so in the
artifact instead of silently costing more requests.
