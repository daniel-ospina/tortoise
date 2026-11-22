## Measurement

rows: 122 (failed: 0)

### arm `cold`

- documents measured: 50 · calls/doc: 3.98 · median wall-clock/doc: 56.25s
- prompt cache: 1,642,367 hit / 1,562,112 miss → hit ratio 51.2%

| stage | input/item | output/item | calls/item | input/call | output/call | cache hit |
|---|---|---|---|---|---|---|
| s1 | 19,611 | 3,698.20 | 1.68 | 11,673 | 2,201 | 524,415 |
| s2 | 14,966 | 12,556.46 | 1.16 | 12,902 | 10,825 | 590,336 |
| s4 | 29,512 | 8,095.82 | 1.14 | 25,888 | 7,102 | 527,616 |

- input fit: `tokens_in = 45,503 + 2.159 × T`  (R²=0.715, median |resid| 11,942)
- output fit: `tokens_out = 22,802 + 0.180 × T`  (R²=0.020, median |resid| 12,810)
  - s1: `1,909 + 2.056 × T` (R²=0.995)
  - s2: `14,916 + 0.006 × T` (R²=0.000)
  - s4: `28,678 + 0.097 × T` (R²=0.009)

- sessions: n=10, median item 1,534.0 tokens → in 30,647.0 / out 5,768.0 (median), calls/item 3.4
  - session cache hit ratio 55.8%

- the longest document (127,914 tokens) delivered as different numbers of turns:

| turns | calls | in | out | stage in | stage out |
|---|---|---|---|---|---|
| 40 | 6 | 365,692 | 87,746 | {'s1': 263780, 's2': 25754, 's4': 76158} | {'s1': 4690, 's2': 35056, 's4': 48000} |

### arm `warm`

- documents measured: 50 · calls/doc: 3.92 · median wall-clock/doc: 58.55s
- prompt cache: 1,962,361 hit / 1,075,462 miss → hit ratio 64.6%

| stage | input/item | output/item | calls/item | input/call | output/call | cache hit |
|---|---|---|---|---|---|---|
| s1 | 19,934 | 3,425.62 | 1.68 | 11,866 | 2,039 | 969,342 |
| s2 | 14,494 | 12,173.84 | 1.16 | 12,495 | 10,495 | 590,715 |
| s4 | 26,328 | 4,935.22 | 1.08 | 24,378 | 4,570 | 402,304 |

- input fit: `tokens_in = 42,439 + 2.128 × T`  (R²=0.791, median |resid| 9,852)
- output fit: `tokens_out = 19,736 + 0.093 × T`  (R²=0.012, median |resid| 8,473)
  - s1: `2,162 + 2.064 × T` (R²=0.986)
  - s2: `14,471 + 0.003 × T` (R²=0.000)
  - s4: `25,806 + 0.061 × T` (R²=0.008)

- sessions: n=10, median item 1,534.0 tokens → in 31,793.5 / out 5,329.5 (median), calls/item 3.4
  - session cache hit ratio 65.4%

- the longest document (127,914 tokens) delivered as different numbers of turns:

| turns | calls | in | out | stage in | stage out |
|---|---|---|---|---|---|
| 40 | 4 | 303,065 | 21,158 | {'s1': 263780, 's2': 11540, 's4': 27745} | {'s1': 3363, 's2': 14282, 's4': 3513} |

### the first measured document — NOT a cold prefix

`docs/retrospectives/2026-08-25-11-06-14.md` (439 tokens). **83.3% of this item's prompt tokens were served from the provider cache** (29,568 hit of 35,512), so this row is not a cold reading — the prefix was already warm when it was measured, whether from a sibling call or from an earlier run (limit 3) — a truly cold prefix was NOT observed.

| stage | prompt | completion | cache hit |
|---|---|---|---|
| s1 | 4,409 | 622 | 3,968 |
| s2 | 21,124 | 87 | 19,968 |
| s4 | 9,979 | 40 | 5,632 |

## Cost: modelled vs measured

Measured cache-hit share of prompt tokens: **51.2%** (cold arm) · 64.6% (warm arm). `measured-cold` prices every prompt token at the full input rate — the conservative end `#14275` says it is quoting; `measured-cached` prices at the measured share.

Tokens per median document: **64,799** measured vs **15,859** modelled (#14275's `1.5T + 7k` input, `0.2T` output).

| item | tokens | modelled in/out | measured in/out | modelled \$ | measured-cold \$ | measured-cached \$ |
|---|---|---|---|---|---|---|
| median document | 5,211 | 14,816 / 1,042 | 48,461 / 16,338 | \$0.0057 | \$0.0341 | \$0.0268 |
| mean document | 8,610 | 19,914 / 1,722 | 64,090 / 24,350 | \$0.0080 | \$0.0484 | \$0.0388 |
| p75 document | 9,131 | 20,696 / 1,826 | 77,582 / 24,137 | \$0.0084 | \$0.0522 | \$0.0405 |
| long document | 127,914 | 198,871 / 25,583 | 299,362 / 23,832 | \$0.0904 | \$0.1184 | \$0.0733 |
| median session | 1,534 | 9,301 / 307 | 30,647 / 5,768 | \$0.0032 | \$0.0161 | \$0.0115 |

Peak rates (`#14275`'s own: \$0.30/M in, \$1.20/M out, \$0.006/M cache hit). Off-peak in the JSON.

**How big the cache lever actually is.** A PERFECT replay (every prompt token a cache hit, which no real workload achieves) prices the median document at \$0.0199 — 42% below the fully-cold \$0.0341, and the warm arm's measured 64.6% hit share reaches 0.0249. The lever is real but SECOND-ORDER, because it only discounts the input side and the output side is now the larger half of the bill.

| item | modelled \$ (off-peak) | measured-cold \$ | measured-cached \$ |
|---|---|---|---|
| median document | \$0.0028 | \$0.0171 | \$0.0134 |
| mean document | \$0.0040 | \$0.0242 | \$0.0194 |
| p75 document | \$0.0042 | \$0.0261 | \$0.0203 |
| long document | \$0.0452 | \$0.0592 | \$0.0367 |
| median session | \$0.0016 | \$0.0081 | \$0.0057 |

## What this does to #14275's numbers

### Confirmed / corrected

- **Cost per median document — CORRECTED.** `$0.0057` modelled → **\$0.0268** measured at peak (4.7×), \$0.0134 off-peak. 1,000 median documents ≈ **\$26.84** at peak, not \$5.6–7.7.
- **The two constants — CORRECTED, and one of them was never a constant.** Input: `tokens_in = 45,503 + 2.16 × T` per document, so the per-item overhead is **45,503 tokens**, not ~7k, and the pass multiplier is **2.16T**, not 1.5T. Output: `tokens_out = 22,802 + 0.180 × T` — **output is dominated by a constant too, which the model assumed away** (`0.2T` ≈ 1.0k on a median document; measured ≈ 16,338).
- **Per-stage:** the fit is per stage in the JSON. The shape is the finding: S1 scales with the document (~2T, R²≈0.99 — it is the only stage that reads the text), while **S2 and S4 are almost pure constants** (~15k and ~26-29k) that a document's length barely moves, so small items are not relatively expensive — they are essentially ALL overhead.

### The allowances, re-stated in the measured unit

| tier | subscription | allowance (tokens) | cost to us peak | % of sub | cost off-peak | % of sub | ≈ documents | ≈ sessions |
|---|---|---|---|---|---|---|---|---|
| free | \$0 | 0 | \$0.00 | 0.0% | \$0.00 | 0.0% | 0 | 0 |
| solo | \$19 | 3,000,000 | \$1.24 | 6.5% | \$0.62 | 3.3% | 46 | 82 |
| pro | \$25 | 4,000,000 | \$1.66 | 6.6% | \$0.83 | 3.3% | 62 | 110 |
| team | \$149 | 24,000,000 | \$9.94 | 6.7% | \$4.97 | 3.3% | 370 | 659 |

- **The overage rate — CONFIRMED (as a rate above cost).** Our measured cost is **\$0.41 per 1M blended tokens at peak** and **\$0.21 off-peak**, so `#14275`'s \$1.00/1M sits 2.4× above cost at peak and 4.8× off-peak. It is not below cost at either end, which is the property the issue needs from it.
- **The DOCUMENT COUNT attached to each allowance — CORRECTED.** The token figures already land inside the 5–10% band at peak (6.5–6.6%), so the allowance numbers themselves stand; what the measurement changes is what they BUY. `#14275` promises ≈200/260/1,600 median documents; at the measured 64,799 tokens per median document it is the counts in the table above — ~4× fewer.

### A rate the issue and the repo disagree on (flagged, not resolved)

Every figure above is at `#14275`'s own quoted peak rates. The repo's reprice map (`tools/longmem_eval/costing.py`, `deepseek-direct/deepseek-v4-flash`) carries **\$0.14/M input, \$0.28/M output, \$0.003/M cache hit** — 2.1× and 4.3× lower. At the map's rates each `$` figure here scales down by the same per-axis factor, so the TOKEN measurement is unaffected and the DOLLAR conclusion moves by up to 4.3×. Which rate set is authoritative is a pricing decision (`#14275`, owner) — this lane states both and picks neither.

### The measurement's own cost

122 items · 473 provider calls · 7,582,601 input / 2,479,926 output tokens · cache hit 59.2% → **\$3.93 at peak, \$1.97 off-peak** (\$5.25 had nothing been cached).
