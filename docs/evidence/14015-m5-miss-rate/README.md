---
title: "#14015 M5 — the miss rate per tenant, and the curve that sizes the working-set allowance"
type: evidence
domain: platform
doc_status: live
created: 2026-11-21
subjects.team: epistemic-team
aboutSubjects: cache window, residency, working-set allowance, miss rate, fault-in
aboutObjects: "#14015, #13989, #13103, #13082, SEARCH-ARCHITECTURE.md §7, SEARCH-ARCHITECTURE.md §8, SEARCH-ARCHITECTURE.md §9, STORAGE-ARCHITECTURE.md §17.4, #12186, #11758"
---

# M5 — the miss rate per tenant, and the curve that sizes the allowance (2026-11-21)

**Verdict: the curve exists, it is measured, and it does NOT have a knee — so the allowance has to be
sized from an absolute miss bar, and at the bar this record declares (10 %) the shipped windows are
already at the flattening point for the tenant §8 describes.** At §8's own reference tenant (~150 MB of
graph) the shipped windows leave **10.1 %** miss on the entity dictionary and **5.2 %** on claims
(solo, 77 MB), falling to **5.7 % / 1.4 %** at pro and **0 % / 0 %** at team — where a 615 MB window
over a 150 MB tenant evicts nothing at all.

**The number `#13989` asked for, stated in tenant MB.** The largest tenant a window still holds at a
**10 %** miss bar:

| | solo (77 MB) | team (615 MB) |
|---|---|---|
| `:Object` | **149 MB** | **1193 MB** |
| `:Point` | **184 MB** | **1471 MB** |

⇒ **solo ≈150 MB of tenant graph, team ≈1.2 GB.** Past that the miss rate climbs steeply on both
labels (`:Object` 10.1 % → 20.9 % at 300 MB; `:Point` 5.2 % → 25.9 %), so that is where a customer
starts being nudged to pay more.

⛔ **There is no knee to find, and that is the finding rather than a gap in it.** Neither label's curve
flattens under "a doubling removes < 2 pp": `:Object` gives a near-constant **10.4 – 12.0 pp per
doubling at every size** (log-linear), and `:Point` decays faster — from its steepest doubling
(**24.6 pp** at `0.15→0.30`) down to **5.6 pp** at `0.50→1.00` — without ever reaching the bar. **So
"good enough" must be a declared bar, not a discovered knee** — which is why §5 publishes the answer
at five bars instead of one.

**And the larger lever is not a megabyte: it is the policy.** At the coverage the shipped windows
actually use (`F ≈ 0.5`), ordering residency by usage instead of at random removes **34.8 pp**
(`:Object`) and **36.0 pp** (`:Point`) — against **10.4 pp** from doubling the window. `#13989`
criterion 1 is worth three times the tariff.

- **Artifact:** [`measurement.json`](./measurement.json)
- **Instrument:** `tools/miss_rate_m5.py` · **git head:** `75970a958753` — the head these
  figures are ANCHORED to: the artifact's `git_head`, pinned against this line and against
  the instrument's bytes at that commit. ⛔ **It is not the head the run ran at.** The run
  was performed at `5cc6bad05cf2` (the artifact's `provenance.run_git_head`, with the bytes
  that ran in `provenance.run_tool_sha256`); the only change to the instrument since then is
  INVARIANT 3's refusal band, which decides whether a run is refused and cannot move a
  computed number.
  A pin checks the pair: the run head's commit time is not after `generated_at`, so an
  anchor can never again be substituted for a run head unnoticed.
- **Corpus:** `~/.tortoise/session-events/*.jsonl` — 27 files, 738 lines
- **Labels measured:** `:Point` (711 nodes, 250 queries per weighting) and `:Object` (1000 nodes,
  250 per weighting). **No aggregate across labels is published anywhere** (`#13989`'s rule).
- **Retrieval mode:** `keyword_only` — a figure about the **lexical** leg, which is precisely the leg
  §6.2 keeps in the graph and therefore the leg a window bounds. It is **not** a figure about the
  fused production read, and no such claim is made.
- **This is M5 from `SEARCH-ARCHITECTURE.md` §9.** Not `STORAGE-ARCHITECTURE.md` §15's M5, which asked
  where the vector index lives and was answered 2026-11-07.

---

## 1. What was asked, and what a "miss" is

`SEARCH-ARCHITECTURE.md` §9 M5 asks for *"the miss rate per tenant"*, and says what it decides:
*"the compute/IOPS/egress tier"*. The owner's acceptance criterion for the window size (`#13989`,
2026-11-20, verbatim) is what made that a question about a **curve** rather than a number:

> *"it's basically an amount that's good enough the system works and they don't have to increase what
> they pay all the time (because postgres is cheap so we can offer a lot without breaking our margins
> and because falkorDB excess is evicted), so ideally we're not nudging them to pay more and more and
> more after only a week or two."*

`#13103` arms the window **with fault-in**: a demoted region is evicted and restored on demand
(measured **0.03 s** index-dropped; 1.585 s with the fulltext index kept). So a query whose canonical
node is not resident is **not an error** — it is a **Postgres read**. That is the cost quantity, and it
is exactly what `STORAGE-ARCHITECTURE.md` §17.4.3 says is *"PRICED but conditional on a miss RATE no
tenant exists to provide"*.

**Two quantities, and they are never added into one number:**

| quantity | definition | what it decides |
|---|---|---|
| **`fault_in_rate`** | share of queries whose canonical is **not resident** | **THE M5 NUMBER.** With fault-in armed this is a record read: network + IOPS + egress. It is what a bigger window buys down. |
| **`miss_resident`** | share of **resident** queries the lexical leg still cannot return at depth 50 | **A FLOOR NO WINDOW CAN BUY UNDER** — the leg's own unreachability (§6.2: no coordination factor, no length normalisation) |

`miss = fault_in_rate + miss_resident × resident_query_share`, and the instrument **refuses to
publish** if that identity does not hold (invariant 3).

---

## 2. The curve

The window keeps `F` of the tenant's graph resident. Residency is scored by **usage** (`#13989`
criterion 1), so the resident set is the highest-usage nodes and the fault-in rate is the complement
of that population's **Lorenz curve**:

```
fault_in_rate(F) = 1 − (the share of QUERY MASS carried by the hottest F of nodes)
```

| coverage `F` | 0.02 | 0.05 | 0.10 | 0.20 | 0.30 | 0.40 | 0.50 | 0.60 | 0.70 | 0.80 | 0.90 | 1.00 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **`:Object`** fault-in | 70.8 % | 52.2 % | 36.7 % | 24.7 % | 18.4 % | 13.9 % | **10.4 %** | 7.6 % | 5.3 % | 3.3 % | 1.5 % | 0 % |
| **`:Point`** fault-in | 85.5 % | 72.7 % | 56.9 % | 35.2 % | 20.2 % | 11.0 % | **5.6 %** | 2.6 % | 1.1 % | 0.4 % | 0.1 % | 0 % |

Concentration, measured on the real corpus: the hottest **1 %** of `:Object` names carry **16.7 %** of
all recurrences and the hottest **10 %** carry **63.3 %**; for `:Point` the same figures are **8.7 %**
and **43.1 %**. Fitted Zipf exponents: **0.95** (`:Object`), **0.70** (`:Point`).

⚠️ **The two labels behave differently, and the difference is not noise — it is the leg.** `:Object` is
near-log-linear (a roughly constant pp per doubling, §3); `:Point` decays faster and steeply. Any
sentence that says "the window costs X %" without naming the label is averaging two different laws,
which the epic's own rule forbids.

---

## 3. The marginal value — pp removed per **doubling** of the window

pp/MB is **tenant-size dependent by construction** (`W = F × S`: a bigger tenant spends more MB per
unit coverage), so it is not a property of the curve. A **doubling** is dimensionless — the same number
for a 150 MB tenant and a 1200 MB one — and it is what *"pay more and more and more"* is actually a
question about.

| doubling | 0.05→0.10 | 0.10→0.20 | 0.15→0.30 | 0.20→0.40 | 0.30→0.60 | 0.40→0.80 | 0.50→1.00 |
|---|---|---|---|---|---|---|---|
| **`:Object`** pp removed | 15.5 | **12.0** | **10.9** | **10.8** | **10.7** | **10.6** | **10.4** |
| **`:Point`** pp removed | 15.7 | 21.8 | **24.6** | **24.2** | 17.6 | 10.6 | **5.6** |

⚠️ **Seven columns, not six — the grid is the artifact's own `doubling_grid`** (`0.05 / 0.10 / 0.15 /
0.20 / 0.30 / 0.40 / 0.50`), so every value sits under the interval it was computed over. A six-column
table tempted a reader (and this record, once) to carry the **24.6 pp** peak — computed over
`0.15→0.30` — into the `0.20→0.40` column, where the artifact holds **24.2 pp**.

⛔ **`:Object` has no knee, and reporting one would invent it.** After the first doubling its curve
removes a near-constant **10.4 – 12.0 pp per doubling** all the way to full residency. A curve with a
constant pp-per-doubling never flattens: **for the entity dictionary "good enough" must be an absolute
miss bar, because there is no flattening point to find.**

`:Point` decays faster — **24.6 pp** at its peak doubling (`0.15→0.30`), **24.2 pp** at `0.20→0.40`,
down to **5.6 pp** at `0.50→1.00` — **but it does not reach the declared 2 pp-per-doubling bar
either**, so under that criterion it has no knee. Its
flattening is real in the sense that matters for sizing (a doubling past `F = 0.5` buys little) and
absent in the strict sense. Both statements are published rather than one of them.

---

## 4. The shipped windows — what a tier actually buys

Windows are **read from `product/pricing.json`** (`max_graph_storage_mb`: solo **77**, pro **102**,
team **615**) and a test asserts the artifact carries exactly those values. ⛔ This measurement changes
no window and no price; `#11758` is the owner's.

Fault-in rate, per label, per tenant size (`S` is the tenant's graph in MB; `F = min(1, W/S)`):

### `:Object` — the entity dictionary

| tenant graph `S` | **solo** (77 MB) | **pro** (102 MB) | **team** (615 MB) |
|---|---|---|---|
| 75 MB | 0 % (fully resident) | 0 % (fully resident) | 0 % (fully resident) |
| **150 MB** (§8's reference) | **10.1 %** (F = 0.51) | **5.7 %** (F = 0.68) | **0 %** (fully resident) |
| 300 MB | 20.9 % (F = 0.26) | 16.6 % (F = 0.34) | 0 % (fully resident) |
| 600 MB | 32.5 % (F = 0.13) | 27.4 % (F = 0.17) | 0 % (fully resident) |
| 1200 MB | 47.8 % (F = 0.06) | 41.4 % (F = 0.09) | 10.1 % (F = 0.51) |

### `:Point` — the claim population

| tenant graph `S` | **solo** (77 MB) | **pro** (102 MB) | **team** (615 MB) |
|---|---|---|---|
| 75 MB | 0 % (fully resident) | 0 % (fully resident) | 0 % (fully resident) |
| **150 MB** (§8's reference) | **5.2 %** (F = 0.51) | **1.4 %** (F = 0.68) | **0 %** (fully resident) |
| 300 MB | 25.9 % (F = 0.26) | 16.5 % (F = 0.34) | 0 % (fully resident) |
| 600 MB | 50.0 % (F = 0.13) | 40.9 % (F = 0.17) | 0 % (fully resident) |
| 1200 MB | 68.2 % (F = 0.06) | 61.6 % (F = 0.09) | 5.2 % (F = 0.51) |

⚠️ **Read the cells, not the row shapes.** Every `0 %` above is a **fully-resident** graph, which is
the absence of a window rather than a good miss rate — a team tenant below 615 MB evicts nothing, so
its cell says nothing about the window's quality. The informative cells are the ones where `W < S`.

⚠️ **`S` is not measured here and cannot be.** §8's "complete graph ≈150 MB" is an **estimate** from a
sampling instrument with known omissions that bias it **low** (blind to the HNSW `vectorset`, excludes
per-graph overhead), so the 150 MB row is the reference and the rest bracket a real spread.

---

## 5. Where it flattens — the number `#13989` needs

Inverting §4: **the largest tenant a tier's window still holds at or below a given miss bar.** This is
the "not nudged in week two" number, because it is a tenant **size**, not a rate.

| miss bar | `:Object` — solo | `:Object` — team | `:Point` — solo | `:Point` — team |
|---|---|---|---|---|
| ≤ 20 % | 284 MB | 2265 MB | 255 MB | 2037 MB |
| **≤ 10 %** | **149 MB** | **1193 MB** | **184 MB** | **1471 MB** |
| ≤ 5 % | 108 MB | 862 MB | 149 MB | 1186 MB |
| ≤ 2 % | 88 MB | 705 MB | 121 MB | 963 MB |
| ≤ 1 % | 83 MB | 659 MB | 108 MB | 860 MB |

**⇒ The flattening point, stated plainly:**

- **solo (77 MB): ≈150 MB of tenant graph** — `:Object` 149 MB, `:Point` 184 MB at the 10 % bar, i.e.
  within 24 % of each other. Above it the miss rate climbs steeply on both labels
  (`:Object` 10.1 % → 20.9 % at 300 MB; `:Point` 5.2 % → 25.9 %), so a solo tenant past ~150 MB of
  graph is the shape that gets nudged.
- **team (615 MB): ≈1.2 GB of tenant graph** — `:Object` 1193 MB, `:Point` 1471 MB. Team's window is
  **8.0×** solo's and the covered tenant size scales with it (615/77 = 7.99; 1193/149 = 7.99).
- **The bar is the assumption, not the curve.** Under a stricter bar the same two labels give
  ≈83 MB / ≈108 MB (1 %) and at 20 % ≈284 MB / ≈255 MB. Because no label has a knee (§3), the bar
  **is** the decision, which is why every bar is published rather than one.

⚠️ **Every figure in this section is a fault-in rate read off a curve measured on a recurrence-weighted
query set.** It is not an observed tenant reading (§6), and it is not a promise about a specific
customer's traffic.

---

## 6. What the measurement assumes, and how far that moves the answer

The curve's input is **query mass**, and no read ledger exists on this box (`§17.4.6` forbids the
tenant-wide `read_ledger` as a product input in any case). So:

**THE ASSUMPTION: a tenant's query mass is proportional to how often a node's identity RECURS across
that tenant's real captured work.** The recurrence unit is the session RECORD for `:Object` (the
`#11901` harvest's own `records` count — one owner of that definition) and the turn for `:Point` (a
majority-of-terms match over the same turns the population is built from). The mass is a **relative**
weight within a label and is never compared across labels.

**How far it moves the answer is measured, not asserted.** Re-running the curve over a Zipf family —
the shape family the observed distributions sit in, and the generator is label-independent, so one
table covers both:

| exponent | mass at top 10 % — `:Point` | mass at top 10 % — `:Object` | flattening @150 MB (0.1 pp/MB criterion) |
|---|---|---|---|
| 0.6 (flatter than `:Point`'s fit) | 36.3 % | 36.8 % | never |
| 0.8 | 51.6 % | 52.6 % | never |
| 1.0 | 67.8 % | 69.3 % | 142.5 MB |
| 1.2 (steeper than either) | 81.5 % | 83.1 % | 90.0 MB |
| **observed** | **43.1 %** (fit 0.70) | **63.3 %** (fit 0.95) | see §5 |

⚠️ **One table, TWO mass columns.** The generator is label-independent, but `zipf_masses` normalises by
population size, so the same exponent over 711 `:Point` nodes and 1000 `:Object` nodes is **not the same
curve** — quoting one label's column for both is how this record once published `:Point`'s masses under
a sentence claiming they covered `:Object`. They happen to give the **same flattening column** here
(both labels: never / never / 142.5 MB / 90.0 MB), which is a coincidence of the 0.1 pp/MB criterion at
this grid, not an identity — the pin asserts both labels' rows agree before the shared column is printed
once.

**Direction:** a flatter usage distribution makes the window **less** valuable (the curve moves toward
the uniform line and flattens *later or never*); a steeper one makes it **more** valuable and moves the
answer down. Both observations sit on the flat side of this table, which is why the declared bar does
most of the work in §5.

⚠️ **The control that keeps this honest.** Every arm is also measured under **uniform** query
weighting, where `fault_in_rate == 1 − F` **by arithmetic** and nothing ever flattens. That is not a
defect — it is exactly what M2's sampler could see, because M2 drew its queries uniformly at random.
**M2's finding that no eviction order beats any other is therefore a property of its SAMPLER and
cannot be evidence about a usage-ordered window.** The two readings side by side, at the same graph
state:

| coverage | `:Object` mass reads | `:Object` uniform reads | `:Point` mass reads | `:Point` uniform reads |
|---|---|---|---|---|
| 0.75 | 8.4 % | 28.0 % | 2.4 % | 27.6 % |
| 0.50 | 20.4 % | 49.6 % | 10.4 % | 51.2 % |
| 0.25 | 41.6 % | 76.4 % | 43.2 % | 73.6 % |

---

## 7. The policy is worth more than the megabyte

Same query set, same coverage, two residency orders — mass-ordered (usage, `#13989` criterion 1) vs a
seeded random order (the shape a creation-recency window approximates when age is unrelated to use):

| coverage | `:Object` usage | `:Object` random | Δ | `:Point` usage | `:Point` random | Δ |
|---|---|---|---|---|---|---|
| 0.75 | 8.4 % | 28.8 % | **20.4 pp** | 2.4 % | 21.6 % | **19.2 pp** |
| 0.50 | 20.4 % | 55.2 % | **34.8 pp** | 10.4 % | 46.4 % | **36.0 pp** |
| 0.25 | 41.6 % | 76.4 % | **34.8 pp** | 43.2 % | 74.4 % | **31.2 pp** |

**At `F ≈ 0.5` — the coverage the shipped windows use on a 150 MB tenant — the ordering is worth
34.8 – 36.0 pp against 10.4 – 10.6 pp from a doubling of the window.** The policy is the lever; the
allowance is the dial. This is the measurement that says `#13989` criterion 1 is load-bearing rather
than a refinement — and it is also the reason the shipped windows look adequate in §4: they are
adequate *because* residency is usage-ordered, not regardless of it.

---

## 8. The floor, and the caveat that makes it a side finding

`miss_resident` — the share of **resident** queries the lexical leg still cannot return at depth 50:

| label | complete graph, mass reads | complete graph, uniform reads | M2's reading (uniform, complete) |
|---|---|---|---|
| `:Object` | **0.0 %** | 0.0 % | ceiling 1.0 |
| `:Point` | **24.0 %** | 24.4 % | **28.7 %** |

They agree in magnitude, which is a useful cross-check between two instruments on two corpora.

⛔ **But the floor is NOT a production number, and this is the one figure in the bundle that must not
be carried out of its context.** `found@deep` is a rate at a fixed depth 50, so it depends on **how
many documents compete for those 50 ranks**. At production scale (tens of thousands of nodes) *far
more* documents compete for the same 50, so **24.0 % is a LOWER bound on the production ceiling, not
an estimate of it.** Read its direction and order of magnitude, never its decimal.

The `fault_in_rate` — the M5 number, and the whole of §2–§5 — is a **residency** question and is not
affected this way. That asymmetry is why the curve is the deliverable and the floor is a side finding.

⚠️ **A second-order effect worth naming, because it is visible in the arms and would otherwise look
like an error.** On `:Point` the TOTAL miss rate is **non-monotone** in window size: complete graph
24.0 %, `F = 0.75` 16.4 %, `F = 0.50` 18.0 %, `F = 0.25` 43.6 %. It dips and then rises, because (a)
the unreachable claims are concentrated in the cold tail a usage window evicts first and (b) fewer
documents compete for the depth-50 ranks. **This is not an argument for a smaller window** — the
fault-in rate, which is the cost, rises monotonically as the window shrinks — it is the reason the two
quantities are published separately.

---

## 9. What this does **NOT** say

- ⛔ **No price.** `product/pricing.json` is the owner's (`#11758`). This bundle sizes an allowance; it
  does not set a rate.
- ⛔ **No window change.** The shipped values are read from the price artifact and asserted equal to it
  in `tests/test_miss_rate_m5.py`.
- ⛔ **No aggregate across labels** (`#13989`: *"per label, never aggregate"*). There is no row anywhere
  in this bundle that pools `:Point` and `:Object`.
- ⛔ **No capture caps, no retention change** (`#4010`).
- ⛔ **No claim about the fused read.** `retrieval.mode` is `keyword_only`; the vector leg did not run
  and is not bounded by a graph window once it moves to Postgres (§3).
- ⛔ **No claim about the `:Subject` / `:Source` / `:Event` legs.** `:Subject` shares `:Object`'s
  harvest class and leg and is not separately populated; `:Source`/`:Event` are anchored/key reads a
  window does not move the same way.
- ⚠️ **Not a read ledger.** The mass is a recurrence proxy (§6). The ledger
  (`tortoise/read_ledger.py`) is the instrument that would replace it, and `#14011` carries that wiring.

---

## 10. Reproducing

```bash
uri="$(uv run python tools/test_lane.py uri)" || exit 1
eval "$uri"
# ⛔ --out points somewhere OTHER than this bundle: re-running over it would
#    overwrite the artifact you are comparing against.
uv run python tools/miss_rate_m5.py --points 900 --objects 1000 --sample 250 \
    --out /tmp/m5-miss-rate.json
```

The run builds **18 graphs** (9 per label: 1 complete + 2 policies × 4 windowed coverages) and
**drops none**; `test_lane.py down` disposes of them, which is the intended path.

**What a re-run reproduces, measured across runs of this instrument.** The **curve** is arithmetic on
a mass distribution derived from the corpus with no sampling in it, so on a fixed corpus it is exact;
the **arms** contain seed-fixed sampling and the lexical leg breaks near-ties on engine-internal
document ids. Measured between two runs: **`:Point` arms identical**, **`:Object` arms within 0.4 pp**.

⚠️ **And this corpus is LIVE.** `~/.tortoise/session-events/2026-11-21.jsonl` is appended to by the
sessions working on this box, so the harvest's absolute counts move between runs (`definite` names
156,106 → 156,148 observed) and the `:Object` curve with them — by **≤0.02 pp at every coverage**.
That is a real reading of a live corpus rather than a reproducibility claim, and it is why the
conclusions in §3–§7 rest on gaps of tens of points and never on a decimal.

`tests/test_miss_rate_m5.py` pins the arithmetic, the five invariants **on the committed artifact**, the
tool's sha256 against `tools/miss_rate_m5.py`, and **every cell of every table above, recomputed from
`measurement.json` at the row and column it is printed under**. A figure that is merely true somewhere
in the artifact — the `:Point` peak quoted under the wrong doubling interval, or one label's mass
column printed for both — fails the pin; so does a figure the artifact does not hold. It cannot
re-derive a run — only a re-run can — and nothing here claims otherwise.

---

## Provenance

| | |
|---|---|
| instrument | `tools/miss_rate_m5.py` (sha256 in the artifact's `provenance`) |
| corpus | `~/.tortoise/session-events/*.jsonl`, 27 files, 738 lines read by the turn harvester, 3 parse errors |
| populations | `:Point` 711 built (funnel recorded: 900 requested, 481 too short, 74 no span, 1 sanitised out, 114 duplicate span), `:Object` 1000 from the `#11901` harvest |
| harvest | `definite` 156,510 · `ident` 14,494 · `path` 6,031 names |
| windows | `product/pricing.json` → solo 77 · pro 102 · team 615 MB |
| generated | see the artifact's `generated_at`; the commit the run ran at is `provenance.run_git_head`, and `git_head` is the ANCHOR the digest is pinned to |
