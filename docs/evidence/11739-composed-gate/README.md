# #11739 — the composed-text GATE, measured on the live graph (2026-11-13)

**Verdict: all three gates FAIL. `TORTOISE_COMPOSED_EMBED_TEXT` must stay OFF.**
This bundle is the measurement, not a proposal: no threshold was changed and no gate was tuned.

## What was measured

The composition that #11805 ships is `tortoise.embed_compose.compose_embedding_text`: the
verbatim `quote` (whitespace-collapsed) as the Point's embedding text, with the names half
**unwired**. On the live graph the names half has **no material at all** — `points_with_any_about_property
= 0` over all 15,842 live Points (the #11838 census) and **no `about*` key appears on any row this
run walked**, and the MCP read surface exposes no edge enumeration. Names were therefore fed as an
empty list, which is the text that would actually ship today. The names arm is **unmeasurable on
this graph**, not passing.

* Population: the hosted graph's default read — 15,842 live Points, of which **15,008 carry both a
  non-empty `quote` and a stored 384-d vector** (byte-identical to the #11838 census).
* Raw arm: the vectors the graph already stores (`embedding`).
* Composed arm: `compose_embedding_text(quote, ())`, encoded with the pinned
  `BAAI/bge-small-en-v1.5`. **Encoder provenance: mean cosine 1.000000** between this encoder's
  encoding of `content` and the graph's stored vector (n = 50) — the two arms are the same space.
* Subject proxy (the graph carries no subject field): the capture `session_id` (997 groups of ≥3,
  14,941 Points) **and** the whole live population.

## The numbers

`gate.json` (three gate verdicts) and `quote_identity.json` (root-cause counts) each carry the probe
they came from in `provenance`; every figure below is a field in one of those two artifacts.

```text
GATE: raw-vs-composed similarity   worst case = 1.000 (global and worst session) vs 0.89  → FAIL
                                   raw worst case = 1.000 (12 pre-existing exact-duplicate pairs)
                                   composition-CAUSED: 16,114 pairs rise from <0.89 (raw) to 1.000 composed
GATE: rank distribution            items displaced from top-10 = 3,706 over 500 queries (7.41/query)
                                   crowding in [0.70,0.85) = 2,316,407 raw → 3,661,251 composed (+58.1%)
                                   new pairs entering from below 0.70 = 2,795,990; pairs crossing 0.89 = 27,356
                                   raw-relevant (≥0.72) items in raw top-10 lost from composed top-10 = 3,033 of 4,186 (72.5%)  → FAIL
GATE: false-proposal (cross_lens)  13,691 new proposals out of 17,305 composed proposals (sample n=1,500;
                                   10,232 at raw); 20 of them cross 0.89; max new similarity 1.000  → FAIL
VERDICT: the composed text may NOT ship ON
```

Field map (artifact → field):

| figure | artifact field |
|---|---|
| 1.000 global / 1.000 worst session / 0.89 / raw 1.000 | `gate.json` → `gate1_similarity` |
| 3,706 · 7.41 · 500 · 3,033 of 4,186 · 27,356 · 2,795,990 · 2,316,407 → 3,661,251 | `gate.json` → `gate2_rank` |
| 13,691 / 17,305 / 10,232 / 20 / 1.000 | `gate.json` → `gate3_false_proposal` |
| 15,842 · 15,008 · 997 · 14,941 · 384 · encoder 1.0 · 949.2 s | `gate.json` → `population`, `subject_groups`, `duration_s` |
| 13,341 distinct texts · 2,059 Points (13.72%) · 392 groups · 28,994 pairs · 0 same-content · 16,114 below 0.89 · 117 largest group · 12 raw exact duplicates | `quote_identity.json` |

## Root cause (measured, not inferred)

**A verbatim quote is not unique to a fact.** 13,341 distinct composed texts cover 15,008 Points:
**2,059 Points (13.72%) carry a quote another Point also carries**, in 392 groups, and all 28,994
pairs inside those groups compose to **byte-identical text** (cosine 1.000 exactly) while every one
of those pairs has a **different `content`** (0 of 28,994 share content). 16,114 of those pairs were
**below** the 0.89 near-duplicate band in raw space. (`quote_identity.json`.)

The worst group is boilerplate: **117 distinct facts share one instruction quote** ("A `find` over the
home directory BLOCKS …"), so 117 unrelated facts get one identical vector
(`quote_identity.json.largest_group_points = 117`). Examples from
`quote_identity.json.examples` (also quoted on the issue):

| raw | composed | quote | two different contents |
|---|---|---|---|
| 0.744 | 1.000 | `ISSUES 2` | "The review of the extractor prompt-injection fix PR…" / "The review outcome was reported as ISSUES 2" |
| 0.794 | 1.000 | `ISSUES 2` | "The review of the extractor prompt-injection fix PR…" / "The review of the cell-recovery PR…" |
| 0.730 | 1.000 | `ISSUES 2` | "…extractor prompt-injection fix PR…" / "The review outcome was ISSUES 2, posted as a PR comment on the shard refresh PR" |
| 0.685 | 1.000 | `ISSUES 2` | "…extractor prompt-injection fix PR…" / "The review of PR #10675 on branch p0-10000b…" |

The second mechanism (independent of shared quotes) is context loss: a 200-char quote drops the
subject a 100-word `content` carried, so distinct facts in the same session crowd into the
0.70–0.85 band (+58.1%) and 72.5% of raw-relevant top-10 neighbours fall out of the composed top-10.

## Declared limitations of `gate.json` (found by the verification pass)

* **GATE 3 is narrower than its one-line criterion:** `new_proposals_crossing_0_89` counts only pairs
  that are NEW proposals. An already-proposed pair that rises across 0.89 is not counted there — but
  the same transition over the WHOLE population is GATE 2's `crossed_089` (27,356), so the aggregate
  gate is not fail-open. The `[0.70, 0.85)` count is emitted as
  `new_proposals_entering_0_70_0_85` (a pair whose raw similarity is below 0.70, not a pair entering
  from 0.72).
* **GATE 1's session leg compares the rounded value** (`round(worst, 4)`) while the global leg
  compares unrounded; the rounding is conservative (it cannot move a value below 0.89).
* The 0.89 / 0.72 bands are written as literals in the probe; they EQUAL
  `NEAR_DUPLICATE_THRESHOLD` / `DEFAULT_THRESHOLD` in `tortoise/embeddings.py` (the imported
  constants the product uses) — the gate changed no threshold.
* The false-proposal check calls `cross_lens.find_cross_lens_matches`, the lens-keyed sibling of the
  `find_cross_source_matches` wrapper the issue names — both are thin callers of the same encode +
  cosine-similarity pipeline (the wrapper keys on `speaker`, this one on `lens`). The live rows
  carry neither `lens` nor `speaker`, so the capture session is the lens.
* **The two drop counters do NOT partition the population, and one of them is a structural zero.**
  `measure()` skips a row with no `embedding` BEFORE the quote test (`if not isinstance(emb, list) or
  not emb: continue`) and then increments `quoted` and appends to `kept` unconditionally — so
  `quoted == len(kept)` identically and `live_with_quote_no_vector_dropped` can only ever read `0`.
  It therefore cannot detect the regression its name suggests (quoted Points losing their vectors),
  and the **547** vector-less live rows (`15,842 − 15,295`) appear in neither drop field. From the
  cited census the split is **19 quoted** (`quote_non_empty` 15,027 − `points_with_vector_and_quote`
  15,008) and **528 unquoted**; `live_without_quote_dropped` (287) is exactly the *unquoted AND
  embedded* bucket (`15,295 − 15,008`) and its name does not say so. No gate verdict moves (all three
  fail on the kept population regardless) and the population the gates were computed over is stated
  exactly, so this is recorded, not repaired: repairing it changes the probe's bytes, which changes
  both `probe_sha256` bindings and requires a fresh run + re-attest of BOTH artifacts.
* **`gate.json` does not record the endpoint it measured, and its `rows_source` is a cache that is
  not committed** (`provenance.rows_source` = `cache:/tmp/11739-rows.jsonl`). On a box without that
  cache, step 1 below re-walks the LIVE graph, which has captured more Points since 2026-11-13 — so
  what a re-run reproduces is the **protocol and the gate logic**; the **numbers** reproduce only
  against the same cached rows. The sibling #11838 census records `endpoint`; this bundle does not,
  so its figures cannot be attributed to a tenant from the artifact alone.

## Reproduce

Numbers in `gate.json` (the three gates, ~15 min: it encodes 15,008 composed texts) and in
`quote_identity.json` (root cause, ~30 s, no encoding — it reads the same row cache):

```bash
cd <checkout>
set -a; . ~/keys.env; set +a      # TORTOISE_MCP_API_KEY
uv sync --extra embeddings
# 1. the three gates (a fresh run WRITES provenance itself; ~16 min of encoding)
uv run python docs/evidence/11739-composed-gate/probes/composed_gate.py \
    --out docs/evidence/11739-composed-gate/gate.json --cache /tmp/11739-rows.jsonl
# 2. the root cause (no encoding, ~30 s)
uv run python docs/evidence/11739-composed-gate/probes/quote_identity.py \
    --rows /tmp/11739-rows.jsonl \
    --out docs/evidence/11739-composed-gate/quote_identity.json
# 3. only if step 1 was run without emitting provenance: rebind the SAME artifact
uv run python docs/evidence/11739-composed-gate/probes/composed_gate.py \
    --attest docs/evidence/11739-composed-gate/gate.json
```

The probe walks the whole live population through the #11838 census probe's own client/walk
(one owner for the paging contract), caches the rows, encodes the composed text with the pinned
embedder, and applies the pre-declared pass criteria in its docstring. Measured duration of the
committed run: **949.2 s** (mostly CPU encoding of 15,008 texts on a loaded 16-core cell).

Each artifact binds itself to its code by the probe's own sha256 (gate.json:
`provenance.probe_sha256` = `294535695b7dd2fdefc696203f9e0766cb5a37df5ab0a37ca338bf23d609bb6b`;
quote_identity.json: `0b784446e43d0faa9448d232fd51978a9f19485741428be5fe5108120fa682c0`), not by a
head sha — the repo squash-merges, so a head sha is unverifiable when someone re-runs the probe to
check it (the same binding the #11838 census uses). `_provenance()` in `composed_gate.py` is the one
producer of that field: a fresh run calls it, and `--attest` calls it on an existing artifact (it
refuses an artifact with no gate verdicts). `--attest` exists because a fresh run is ~16 minutes of
encoding while the binding is a pure function of the probe's bytes; it changes ONLY `provenance`, so
re-running steps 1–2 reproduces every number in this bundle.
