---
title: "#11739 — the composed-text gate, measured on the live graph (all three gates FAIL)"
type: evidence
domain: platform
doc_status: live
created: 2026-11-13
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph, composed embedding text, verbatim quote, near-duplicate band
aboutObjects: "#11739, #12436, #11838, #11682, #11706, #8488, #1349, #4194"
---

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
they came from **and a digest of the numbers they publish** in `provenance` (so a hand-edit of a
verdict or a count reds in the pin); every figure below is a field in one of those two artifacts.

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
                                   RE-DERIVED 2026-11-14 (a NEW-PROPOSAL count is not a FALSE count):
                                   4,202 of 4,220 composed proposals are FALSE (99.6%), 2,599 of 2,661 raw
                                   (97.7%), and 3,319/3,319 composed-ONLY proposals are FALSE (100%)  → still FAIL
VERDICT: the composed text may NOT ship ON
```

Field map (artifact → field):

| figure | artifact field |
|---|---|
| 1.000 global / 0.89 / raw 1.000 | `gate.json` → `gate1_similarity` |
| 1.000 worst session | `gate.json` → `subject_groups.worst_session.worst_composed` |
| 3,706 · 7.41 · 500 · 3,033 of 4,186 · 27,356 · 2,795,990 · 2,316,407 → 3,661,251 | `gate.json` → `gate2_rank` |
| 13,691 / 17,305 / 10,232 / 20 / 1.000 | `gate.json` → `gate3_false_proposal` |
| 15,842 · 15,008 · 997 · 14,941 · 384 · encoder 1.0 · 949.2 s | `gate.json` → `population`, `subject_groups`, `duration_s` |
| 13,341 distinct texts · 2,059 Points (13.72%) · 392 groups · 28,994 pairs · 0 same-content · 16,114 below 0.89 · 117 largest group · 12 raw exact duplicates | `quote_identity.json` |

## Gate 3 re-derived: the FALSE-PROPOSAL count (2026-11-14)

`gate.json`'s `gate3_false_proposal` publishes **proposal** counts and no classification: its fields are
`sample`, `threshold`, `lens_key`, `proposals_raw`, `proposals_composed`, `new_proposals`,
`new_proposals_crossing_0_89`, `new_proposals_entering_0_70_0_85`, `max_similarity_among_new_proposals`,
`pass` — there is no false-count field, and no pair in that run was ever labelled true/false. The
2026-11-13 **verdict comment** on #11739 rendered `13,691` as *"13,691 false proposals out of 17,305"*;
13,691 is `new_proposals`. **A new-proposal count is not a false-proposal count**, and the field name
`gate3_false_proposal` is what pre-seeded the substitution. The figures below are the missing measurement.

`probes/gate3_false_proposals.py` (this bundle, landed 2026-11-14) runs BOTH arms over the SAME rows —
stored vector (`content`) vs `compose_embedding_text(quote, ())` encoded with the pinned embedder —
through the real `find_cross_lens_matches` at **0.72**, `lens_key="session_id"`, and classifies every
proposal under the criterion declared in its docstring before the run (#399's candidate surface and its
calibrated 0.72 paraphrase / below-0.72-noise split; #438's candidate precision):

| tier | rule | verdict |
|---|---|---|
| T1 | the two `content`s are identical (whitespace-collapsed) | TRUE — same claim |
| T2 | composed texts byte-identical **and** contents differ | FALSE — one quote span, two different facts |
| T3 | raw (content-space) similarity **< 0.72** | FALSE — never reaches the product's own same-concept floor |
| T4 | raw ≥ 0.72, texts differ | AUDITED on a seeded sample (the only tier needing judgement) |

T1–T3 are mechanical and exhaustive; T4 is dumped by the probe (`--pairs-out`, `set` = `shared_T4` /
`raw_only_T4`) and **labelled by the lane**, which is what the artifact honestly says: the sample frame
and its size are the probe's, the labels are not.

`gate3_false_proposals.json` (823 live quoted+embedded Points — a page PREFIX of the default read order
at limit 100, declared as a prefix, not a random sample; 4,220 composed / 2,661 raw candidate pairs):

| | composed | raw |
|---|---|---|
| proposals | **4,220** | **2,661** |
| T2 identical composed text, different content | 2 | 2 |
| T3 below the calibrated 0.72 floor | 3,319 | 0 (cannot occur) |
| T4 audited (raw ≥ 0.72) | 899 → **98/100 false** | 899 shared (98/100) + 1,760 raw-only (**39/40 false**) |
| **false total** | **4,202 (99.6% of 4,220)** | **2,599 (97.7% of 2,661)** |

**Composed-only delta — the pairs the switch actually causes: 3,319, all 3,319 FALSE (100%), exact, no
sampling** (every one sits below 0.72 in content space) and **0** of them crosses 0.89 on this prefix
(the pinned 1,500-Point sample recorded 20 — same mechanism, different sample). Wilson 95% intervals on
the audited strata: shared T4 n=100 k=98 → 92.9–99.5%; raw-only n=40 k=39 → 87.1–99.6%; all-Point
composed ≈ 4,157–4,216 (98.5–99.9%), raw ≈ 2,371–2,649 (89.1–99.6%) — bounds rounded **outward**
(floor on the low end, ceil on the high end, 1 dp), and
the true/proposed totals derived from the audited rates (≈18 composed, ≈62 raw) are **estimates, not
counts**.

Two counter-movements are recorded so the delta is not read as purely additive: composition **loses**
1,760 raw proposals (66% of raw's set) whose content is ≥ 0.72 but whose quote diverges below it — false
under the same criterion and no longer proposed (a recall loss, which is gate 2's subject) — and the 2 T2
pairs were already proposed by raw, so they change neither arm. ⚠️ **T3 is definitional-by-calibration,
not a semantic audit**: the lane did not read 3,319 contents, so a purely semantic argument would have to
be had exactly where #399's own floor lives.

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
* **`rows_walked` is a cache-sourced count, and the walk's completeness proof is absent from the
  artifact.** `gate.json` was written from the row cache (`source: cache:/tmp/11739-rows.jsonl`), and
  cache mode synthesises `rows_walked = live_rows = len(rows)`, so the field reads **15,842** — the
  LIVE row count — while the #11838 census walked **15,868** (26 terminal rows). With the walk
  skipped, `pages`, `distinct_ids`, `total_reported` and `final_page_size` — the #5346
  partition proof — are not in the artifact at all, so `gate.json` alone cannot show that the cache
  was a complete partition of the population rather than a truncated walk. What corroborates it is
  external: the population is byte-identical to the census (15,842 live, 15,295 embedded, 15,008
  quoted+embedded). Recorded, not repaired, for the same reason as the drop counters — the
  artifacts are dated and their digests bind.
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

⚠️ **Needs ~2 GB RSS peak in EACH step — the second one is not the cheap one.** `_pair_bands` is
chunked (~30 MB), but both probes build the full `n x n` similarity matrix for the whole population
(`15,008² x 4 B ≈ 900 MB`) and neither doubles it: `_worst_pair` is called twice (composed, then raw)
but strictly sequentially, one matrix at a time, measured `ru_maxrss` 104 MB → 976 MB. Step 2 —
described below as "~30 s, no encoding" — allocates the SAME matrix (`raw @ raw.T`, then
`np.triu(rr >= 0.99999, 1)`), and measured `341 MB → 1,214 MB` after the product and
**`1,858 MB` after the comparison**: more than step 1, in the probe that encodes nothing. Budget
the `free -g` check for both. A 15 GB cell already at load 15 has OOM-killed multi-suite
runs; check `free -g` / `uptime` before starting, because a run killed at 80 % produces no verdict.

Numbers in `gate.json` (the three gates, ~15 min: it encodes 15,008 composed texts) and in
`quote_identity.json` (root cause, ~30 s, no encoding — it reads the same row cache):

⚠️ **The probes' own `Usage` docstrings still show the HISTORICAL in-place invocation**
(`--out docs/evidence/11739-composed-gate/gate.json`, and the same for `quote_identity.py`). The
recipe below supersedes them; they are left alone because rewriting a frozen baseline's prose is
churn, **not** because it is unrepairable — since this round both probes carry `--attest`, so a
docstring correction is a provenance rebind rather than a re-run that re-stamps `measured_at`.

```bash
cd <checkout>
set -a; . ~/keys.env; set +a      # TORTOISE_MCP_API_KEY
uv sync --extra embeddings
# 1. the three gates (a fresh run WRITES provenance itself; ~16 min of encoding)
#    ⛔ WRITE TO A SCRATCH PATH, then diff — do NOT overwrite the committed artifact:
#    this bundle is the 2026-11-13 baseline #12436 is scoped against, and a fresh run with no
#    cache on the box is a LIVE walk, so its numbers are EXPECTED to differ. The provenance
#    bindings cannot stop that (an overwrite re-attests itself: probe bytes, verdict and bands
#    all stay valid), though the pinned headline figures do red on a moved population — measured:
#    rows_walked 15842 -> 15868 re-attested with --attest reds
#    `test_the_headline_figures_are_the_ones_the_index_quotes`. Write to scratch anyway: a red you
#    did not intend is still a red.
uv run python docs/evidence/11739-composed-gate/probes/composed_gate.py \
    --out /tmp/gate.json --cache /tmp/11739-rows.jsonl
# 2. the root cause (no encoding, ~30 s)
uv run python docs/evidence/11739-composed-gate/probes/quote_identity.py \
    --rows /tmp/11739-rows.jsonl --out /tmp/quote_identity.json
# 3. gate 3's false-proposal count (both arms, ~70 s; no network beyond the row cache)
#    ⛔ it DUMPS the T4 samples for labelling, it does not label them — see the gate-3 section
#    ⛔ ITS POPULATION IS NOT STEP 1's. It reads `G3_ROWS_CACHE`, default
#       /tmp/11739-g3-rows.jsonl — the 900-row page PREFIX the count was measured on (823 live
#       quoted+embedded Points). NEITHER cache is committed. If that file is absent the probe
#       does its OWN bounded live walk (PAGE=100, ROWS_TARGET=900) and caches it, which
#       re-creates the prefix's SHAPE, not necessarily its exact rows.
#       Pointing G3_ROWS_CACHE at step 1's cache instead is a DIFFERENT frame: ~15,008 live
#       Points, and the counts move ~18x. Reproduce the committed FRAME this way or say the
#       numbers are a new measurement — do not quote them against step 1.
uv run python docs/evidence/11739-composed-gate/probes/gate3_false_proposals.py \
    --out /tmp/11739-g3.json --pairs-out /tmp/11739-g3-pairs.jsonl
# 4. only after a probe EDIT — rebinds provenance (code digest + payload digest) to the edited
#    probe WITHOUT re-running the measurement; every fresh run already writes its own provenance
uv run python docs/evidence/11739-composed-gate/probes/composed_gate.py \
    --attest docs/evidence/11739-composed-gate/gate.json
# …and the same for the root-cause artifact (no row cache needed for a rebind)
uv run python docs/evidence/11739-composed-gate/probes/quote_identity.py \
    --attest docs/evidence/11739-composed-gate/quote_identity.json
# …and the gate-3 artifact
uv run python docs/evidence/11739-composed-gate/probes/gate3_false_proposals.py \
    --attest docs/evidence/11739-composed-gate/gate3_false_proposals.json
```

Then compare: `diff <(uv run python -m json.tool /tmp/gate.json) <(uv run python -m json.tool
docs/evidence/11739-composed-gate/gate.json)`. A pass where the committed artifact records a FAIL is
a NEW artifact (its own bundle directory and index row, cited by the PR that flips
`TORTOISE_COMPOSED_EMBED_TEXT`), never an edit to this one.

The probe walks the whole live population through the #11838 census probe's own client/walk
(one owner for the paging contract), caches the rows, encodes the composed text with the pinned
embedder, and applies the pre-declared pass criteria in its docstring. Measured duration of the
committed run: **949.2 s** (mostly CPU encoding of 15,008 texts on a loaded 16-core cell).

Each artifact binds itself twice, and the two bindings answer different questions — the same split
**SLSA v1.0 provenance** makes between the attested artifact's **`subject` digest** and its
**`resolvedDependencies`** (the inputs; a probe digest is a dependency digest, not a build
definition — in-toto's Statement itself has no build-definition field):

* `provenance.probe_sha256` binds it to the **code** that produced it (gate.json:
  `4f066693fbc1c742e36d29875f4b970af7ee12df264217cf95911f480b47a411`; quote_identity.json:
  `b66f8a174708f855501ac02ae3e5da57dd9e7ae62fac5721cb77c2036f3dfc65`;
  gate3_false_proposals.json: `d98e31aa450f5c3f2ea1d7b706fa6aba446a7b7dcae8d42eaa134c14f63ddc8b`), and
* `provenance.payload_sha256` binds it to the **numbers** it produced **and to the row cache they
  are attributed to** — sha256 over `{"artifact": <every field except provenance>,
  "rows_source": …}`, canonicalised `indent=2, sort_keys=True` (gate.json:
  `cfbe7ad4eaee3868ddc52e5432b79fc7baf92a541e581dbcbff6398cf00693be`; quote_identity.json: `31ba4cf0deb6d0bee1f95e0ccf8e66211fe2f0fbb0ba290971f1d735d40dd296`; gate3_false_proposals.json: `c5aaa639ccf7260463dff03b4e8dae0b4d0724411a48859ff22531b8ec514c99`). Without the second, a hand-edit of a NUMBER
  — `gate2_rank.top_k.items_displaced_from_top_k` 3706 → 3707, or `global_worst_composed`
  1.0 → 0.10 — left the first untouched and passed the pin, which is what the payload binding
  closes; and without `rows_source` INSIDE it, re-pointing the attribution at another cache also
  left the pin green (measured). (The VERDICT was already pinned before either half: at
  `bf7b848531`, flipping `verdict.all_pass` to `true` reded the pin — `1 failed, 4 passed`,
  measured.)

Neither is a head sha — the repo squash-merges, so a head sha is unverifiable when someone re-runs
the probe to check it (the same binding the #11838 census uses). `_provenance()` in each probe is
the ONE producer of its block: a fresh run calls it, and `--attest` calls it on an existing artifact.
`--attest` fails closed, exit `2`, under four markers — and the ARM SURFACE differs per probe,
because the three artifacts answer different questions. `composed_gate.py` refuses an unreadable or
unparseable artifact (`ATTEST-UNREADABLE`); an artifact whose ROOT is not an object, no gate
verdicts, a `verdict` that is not an object, a `verdict.gates` block that does not carry **exactly**
the three gate names (so neither a deleted block nor a partially deleted flag can be blessed — the
same guard subsumes both, `#12476`) or no `source` to record as `rows_source` (`ATTEST-REFUSED`);
`all_pass` disagreeing with the three gate flags (`ATTEST-INCONSISTENT`); and an unwritable target
(`PROBE-WRITE`). `quote_identity.py` refuses an unreadable artifact (`ATTEST-UNREADABLE`); an
artifact whose root or `provenance` is not an object, no root-cause measurement, or no
`provenance.rows_source` (`ATTEST-REFUSED`); and an unwritable target (`PROBE-WRITE`) — it carries
no verdict block, so it has no inconsistent-verdict arm. `gate3_false_proposals.py` refuses an
unreadable or unparseable artifact, or one whose ROOT is not an object (`ATTEST-UNREADABLE` — a JSON
ARRAY parses fine, so that shape needs its own guard before any `.get`); an artifact with no
`source` to record as `rows_source` (`ATTEST-REFUSED`); and an unwritable target (`PROBE-WRITE`) —
on `--attest`, and equally on the MEASUREMENT writes (`--out`, `--pairs-out`, and the row cache),
so a full read plus a full encode never ends in an `OSError` traceback; an unreadable or TRUNCATED
row cache is its own refusal (`PROBE-READ`, exit `2`) rather than a fall-through to a live walk,
because that would swap the frame without saying so. It carries no verdict block,
so it has no inconsistent-verdict arm. ⛔ **Every one of these exits `2` and none
raises**: the shape checks come before any `.get`/`.values()` call, because a lane script cannot
tell an `AttributeError` out of `main()` (exit `1`) from a refusal. It exists
because a fresh run is ~16 minutes of
encoding while the binding is a pure function of the probe's bytes and the artifact's own payload;
it rewrites ONLY `provenance`, so it cannot move a number. What a re-run reproduces is the
**protocol and the gate logic**; the numbers reproduce from the row cache, not from a fresh walk
(see the `rows_source` limitation above).

⚠️ **What none of it proves — and what is bound.** `--attest` blesses the bytes it is handed,
exactly as `probe_sha256` blesses whatever the probe is — so the guarantee is *"nothing edited this
artifact after the attest"*, not *"these numbers came from a run"*. The attest is a lane action, and
the commit diff is where it is reviewable.

Within the artifact, `payload_sha256` covers **every field except `provenance`, plus the
`rows_source` those fields are attributed to**, and the pin separately checks the remaining fields
`provenance` carries that the artifact itself can corroborate: the probe's bytes, the probe's
basename, a non-empty row source (which must equal the artifact's own `source` where it records
one) and a non-empty encoder. It ALSO asserts the figures the README and `docs/00_index.md` quote,
and DRIVES `--attest`'s refusal arms on temp copies (exit 2, bytes left untouched), so an edit to a
NUMBER, a count, the VERDICT, the attested probe, or the cache the numbers are attributed to each
reds (`tests/test_11739_composed_gate_probe.py`). What none of it can see is an edit made *and
re-attested* in the same commit — that is what the commit diff is for.

What that means for a re-run: the committed numbers are reproducible from the row cache named in
`provenance.rows_source` (`/tmp/11739-rows.jsonl`, which is **not** committed — see the limitation
above), and a fresh run with no cache on the box is a **live** walk over a graph that has captured
more Points since 2026-11-13 — so it reproduces the **protocol and the gate logic**, which is
what #12436's acceptance test means by "the same gate re-run", not these literals.
