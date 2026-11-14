---
title: "#11739 gate 2 — the rank distribution: top-k displacement, composed vs raw"
type: evidence
domain: platform
doc_status: draft
created: 2026-11-14
subjects.team: epistemic-team
aboutSubjects: Tortoise memory graph, composed embedding text, verbatim quote, top-k displacement
aboutObjects: "#11739, #11805, #12436, #12439, #11682, #11706, #8488"
---

# #11739 gate 2 (of 3) — the rank distribution

> ⚠️ **`doc_status: draft` — this is a PROBE artifact, not the landed bundle.** It is NOT registered
> in `docs/00_index.md`, has NO pin test, and carries NO `provenance.payload_sha256` self-binding —
> the three things `docs/evidence/11739-composed-gate/` has and this does not. It is committed to a
> **branch** so the numbers and the probe that produced them are durable and a reader can follow the
> citation the issue comment makes. Landing it as an indexed bundle (README + self-binding artifact +
> `tests/test_11739_rank_gate_probe.py` + `docs/00_index.md` row + two `config/ci-surfaces.yml`
> registrations) is a follow-up lane's work, exactly as gate 1's bundle landed via #12439.

**The number this gate exists to produce: facts the composed arm pushes out of top-k while their
similarity sits inside the `0.70–0.85` band that the `0.89` near-duplicate bar cannot see.**

| query formulation | arm | in `0.70–0.85` **and** out of top-k | out of top-k (any similarity) |
|---|---|---|---|
| **A** content-query, full population (15,008) | **composed** | **803** | **1,377** |
| | **raw** | **0** *(structural — see below)* | **0** |
| **B** prefix-query, 1,500-query seeded sample | **composed** | **174** | **554** |
| | **raw** | **115** | **186** |

B's counts are counts on a seeded 1,500-query sample — **not estimates and not scaled**. Do not multiply
by ~10 to compare them with A's full-population rows: the two rows differ in n as well as in arm.

```text
rank metric used:                top-k displacement + facts crowding 0.70-0.85
                                 FROM #11739's OWN WORDS: "a rank-distribution check — the
                                 0.89 band is a duplicate detector and cannot see facts
                                 crowding the top-k at 0.70-0.85. Add: count of items
                                 displaced from top-k"  (k = 10 = the product's top-k)
queries / subjects:              A: 15,008 (every live quoted+embedded Point is its own query)
                                 B: 1,500 (seed 11739) over the full 15,008-doc pool
top-k used:                      k = 10
rank histogram       composed:   1:11,939  2:707  3:271  4-10:714  out-of-topk:1,377   (A)
                     raw:        1:15,006  2:2    3:0    4-10:0    out-of-topk:0       (A)
rank histogram       composed:   1:652     2:111  3:43   4-10:140  out-of-topk:554     (B)
                     raw:        1:1,005   2:146  3:52   4-10:111  out-of-topk:186     (B)
live vs pinned artifact:         pinned population / RE-DERIVED ranks (see below)
exit / bounded out?:             0 (not bounded out)
posted:                          #11739 comment 6869726971
```

**Items displaced from top-k** (the issue's literal wording — items, not queries; reference ranking =
the **raw** arm's own top-k):

| | items considered | displaced by the composed arm | queries affected |
|---|---|---|---|
| all raw top-k items | 150,080 | **96,133** (64.1 %, mean **6.41** of 10) | 15,001 of 15,008 |
| raw top-k items **in `0.70–0.85`** | 105,442 | **67,259** (63.8 %, mean **4.48** of 10) | 11,798 of 15,008 |

## No verdict is rendered here

Gate 2 only. Gate 1 is recorded (#11739 comment `6868851537`: worst-case composed similarity **1.000**
vs the **0.89** bar, raw also 1.000 — so gate 1 cannot decide the switch) and gate 3
(`find_cross_source_matches` → `cross_lens`) belongs to a parallel probe. Nothing was armed, tuned or
opened: `TORTOISE_COMPOSED_EMBED_TEXT` stays **OFF**, no threshold, band, criterion or k was changed.

## Why A's raw row is 0 BY CONSTRUCTION, not by merit

Query text = the Point's own `content`, i.e. the byte-identical text the **raw** arm indexes, so the
effective fact has similarity **1.000** to its own query and the `0.70–0.85` band is *unreachable* in
the raw arm. Read A as "when the fact's own paraphrase is the query, the composed index puts the fact
in the paraphrase band and then loses it" — never as "raw is flawless". **B** is the
less-rigged comparison (query = the first `max(6, 25 %)` of the content words — i.e. the **raw** arm's own
index text, truncated — so the composer sees only a fragment and the raw arm's full-text advantage is
partly **withheld, not absent**), and it is the one where the raw arm has a real, nonzero displacement.

## Why the composed arm loses facts — measured, not inferred

Of the 1,377 effective facts the composed arm pushes out of top-k:

| class (disjoint unless marked) | count | of which in `0.70–0.85` |
|---|---|---|
| **≥11 byte-identical composed-text twins exist** — the group holds ONE rank, decided by non-twin documents; when that rank is >10 every member is displaced together | **581** (42 %) | 360 |
| … of which ≥51 twins (largest group: **117 facts sharing one instruction quote**) | 240 *(subset of the row above)* | — |
| **2–10 byte-identical twins** | **171** (12 %) | 110 |
| **unique composed text — no twins at all** | **625** (45 %) | 333 |

⚠️ **Twin membership does not itself displace anything.** Rank is `1 + count of STRICTLY greater
similarities` (`probes/rank_gate.py:88`), so byte-identical twins **tie** with the fact and are excluded
from its rank: a twin group collapses its members onto one rank rather than pushing them down. That is why
592 of the 1,173 Points in ≥11-groups rank **inside** top-10 (the 117-member group sits at **rank 2**,
similarity 0.9724). So the proximate cause of all 1,377 displacements is the same — non-twin documents
outranking the fact — and twin-group membership is a property 42 % of them share, not the mechanism that
lost them their rank. Twins *do* matter elsewhere: they are why 15,008 Points map onto only 13,341
distinct texts, which is gate 1's root cause.

1,173 of the 15,008 Points sit in groups of ≥11 byte-identical composed texts (15,008 Points map onto
only **13,341** distinct composed texts), and the median rank of a twin-group Point is **4** against
**1** for a Point with unique composed text.

## Population and provenance

* Population source: the committed artifact's own row cache, `/tmp/11739-rows.jsonl`, sha256
  `be2ee6353aa22e4103b4ab2f1db9576021152cceec7e82e99a82167788558439` — the exact cache
  `docs/evidence/11739-composed-gate/gate.json` records. Gate 1's denominators reproduce
  byte-for-byte from it: **15,842 live rows · 15,008 with quote + vector · 13,341 distinct composed
  texts**.
* **Re-derived this pass** (01:44–01:56 UTC 2026-11-14): every composed vector (the real shipped
  composer `tortoise.embed_compose.compose_embedding_text`, empty neighbour-name list — the names
  half is unmeasurable on this graph, as gate 1 reported), every rank, histogram and displacement
  count, and the encoder provenance (`mean cosine vs the STORED vectors = 1.000000` on a 256-row
  sample, 384-d, pinned `BAAI/bge-small-en-v1.5`).
* **NOT re-derived: the live walk.** A full live re-walk + encode is ~16 min on this box. The data
  plane was **up** at 01:27 UTC (`/health` 200 in 0.93 s, `/mcp/` 200 in 2.15 s), so this is a
  **time-bound decision, not a data-plane refusal**: the numbers are ranks over a pinned population,
  not a claim about the graph's live state at 02:00 UTC.
* Hermetic re-run: `probes/rank_gate.py` (sha256 `6cd1220ab762dfcd3f8dfebdfdff516dca529dc162a21d2519c9dcbf654bda9d`)
  against the cache above produced the population, both arms' ranks and histograms, and the item-displacement
  counts. ⚠️ **It did NOT produce the twin-group classification** in "Why the composed arm loses facts" —
  those counts came from a one-off pass over the same cache whose script is not in this bundle, so they have
  **no `gate.json` field** and are not machine-followable yet. Treat them as one-off and re-derivable, not
  pinned; folding them into the probe is follow-up work named in the header.

## Files

| file | what |
|---|---|
| `probes/rank_gate.py` | the probe; produces the numbers above from the row cache |
| `gate.json` | the machine-readable result (both arms, both query formulations, histograms, item displacement, digests) **plus a hand-written `provenance` block** — the probe emits no `provenance` key, so that block is not probe output |

Refs #11739 · #11805 · #12439 · #12436 · #11682 · #11706 · #8488
