---
title: "Merge Throughput Measurements"
type: engineering
domain: capability
doc_status: live
created: 2026-09-27
subjects.team: epistemic-team
ownedBy: epistemic-team
aboutSubjects: epistemic-team
aboutObjects: tortoise
---

# Merge Throughput Measurements (#5215)

> Record home for the **measurement plan (§5)** of `docs/plans/2026-09-26-5215-merge-throughput.md`.
> **One writer per file per wave.** Task 2 wrote **M2** (below). Task 3 appends **M3–M6** (M8 is
> Task 4b's; Task 6 records the full M1–M6/M8 set). Task 6 writes the `#ceiling` anchor. A row's
> numbers are owned by that row's task — do not restate them here.
>
> Every record carries a **capture timestamp** and the **resolved `origin/main` SHA**; the validity
> window is **per row** (§5). The instrument's `check baseline-fresh` is the implementation of
> `--require-fresh`; a row states its own window.

---

## M2 — Cycle-time attribution (first-class; ⟨C2⟩)

**Intent.** `test (a)` runs materially longer than `test (b)` on a full push-to-main matrix, and the
cause is unrecorded (⟨C2⟩). The claim attached to it — that reweighting the shards buys ~4 minutes of
cycle — is only valid if the imbalance is **weight-driven** rather than **overhead-driven**. M2
partitions the heavy job's wall time into **collection / execution / teardown / watchdog-recovery**,
summing to wall time (±30 s), and names the driver.

### Record

| Field | Value |
| --- | --- |
| `capture` (`verified_at`) | `2026-09-27T18:49:49Z` |
| validity window | **7 days** (§5: M2) |
| resolved `origin/main` at capture | `db70babd507abd33c7a1b6749b98b7ee8a5c4bf8` |
| measured run id | `36333730533` (event `push`, branch `main`) |
| measured run `head_sha` | `fad98a5fd67e3a5dd15fdf4103df19b4ea3f0798` |
| run `created_at` | `2026-09-27T16:34:17Z` |
| heavy jobs | `test (a)` = `108668615242`, `test (b)` = `108668615136` |

### Sources read — and why the primary read is UNKNOWN, not zero

M2's primary source is the existing collector (`tools/ci_timing.py` / `docs/ci-timing.json`). It was
read first. **It is empty**, so that path is recorded as **UNKNOWN** — never as a zero value:

| Source | Bytes | sha256 | Usable? |
| --- | --- | --- | --- |
| `docs/ci-timing.json` (primary) | **43** | `486bf262b8573fb6aa2b42cb152d2e9d28d8fd3320042a59f4f9ef07c3531ce5` | **No** — `{"schema_version":1,"history":[]}`, no `sampled_run`/`steps`; the collector has never sampled (⟨C2⟩) |
| collector JSON for run `36333730533` (fallback) | **25 621** | — | **Yes** — Jobs-API step durations, produced by the collector itself |
| raw job log `test (a)` (fallback) | **218 822** | — | **Yes** — non-empty (`0 bytes ≠ empty`) |
| raw job log `test (b)` (fallback) | **205 980** | — | **Yes** — non-empty |

The fallback is the one M2 sanctions: *"fall back to the job log only if unusable — then
`gh api --allow-escape-sequences` (0 bytes ≠ empty)"*. It was taken because the primary read was
empty. The collector was not forked: it was **run** (`--run-id … --out-dir …`) and its own
`steps_by_job` step table is the step-level source. The raw logs supplied the **pytest session-banner
wall time** (and the pass/fail summary counts) — the one phase boundary the collector does not record.
That is a **documented gap in the collector**, owned by #5393 / plan Task 4b, **not** a second parser:
`tools/ci_timing.py::parse_log` parses files/counts/outcomes/**killed** (the collector *does* flag a
watchdog kill) and `steps_by_job` records each step's `conclusion`, so the watchdog signal and step
conclusions are already the collector's; only the session wall time is not.

### Phase attribution — the four phases, summed to wall time

`collection` = the pre-execution job steps **plus pytest's in-run collection**; `execution` = the
pytest session minus that in-run collection; `teardown` = the post-session tail (atexit + the step's
summary print) plus the post-execution job steps and the inter-step gaps; `watchdog-recovery` = the
SIGINT/grace time and re-runs a watchdog kill costs.

| Phase | `test (a)` (s) | `test (a)` (min) | `test (b)` (s) | `test (b)` (min) | Composition |
| --- | ---: | ---: | ---: | ---: | --- |
| collection | 314.00 | 5.23 | 308.00 | 5.13 | pre-run steps (232 / 240, incl. the standalone collect-only pass 82 / 68) + in-run collection proxy (82 / 68) |
| execution | 1860.67 | 31.01 | 1280.22 | 21.34 | pytest session (1942.67 / 1348.22) − in-run collection proxy (82 / 68) |
| teardown | 15.33 | 0.26 | 18.78 | 0.31 | post-session tail (9.33 / 9.78, atexit + summary) + post-run steps (3 / 5) + inter-step gap (3 / 4) |
| watchdog-recovery | 0.00 | 0.00 | 0.00 | 0.00 | no kill: `rc=0` / `rc=1`, no `WATCHDOG: pytest killed` banner |
| **sum** | **2190.00** | **36.50** | **1607.00** | **26.78** | **= job wall exactly (Δ = 0.00 s; bound ±30 s)** |

**Backing measurements** (Jobs API step `started_at`/`completed_at` via the collector; pytest session
banner read from the raw log):

| Quantity | `test (a)` | `test (b)` |
| --- | ---: | ---: |
| job wall | 2190 s (36.50 min) | 1607 s (26.78 min) |
| Σ step durations | 2187 s | 1603 s |
| pre-run steps | 232 s | 240 s |
| `Run fast test suite` step | 1952 s | 1358 s |
| pytest session (banner) | 1942.67 s | 1348.22 s |
| post-session tail (step − banner) | 9.33 s | 9.78 s |
| post-run steps | 3 s | 5 s |
| tests | 8238 passed, 0 failed | 8722 passed, 2 failed |

The phase sum is **exact and independent of the collection proxy**: if in-run collection is `X` instead
of 82 / 68, `collection` gains `X` and `execution` loses `X`, and the total is unchanged at 2190.00 /
1607.00 s. The proxy is the standalone collect-only step (`.github/workflows/python-ci.yml`, "Generate
coverage manifest"), which collects the identical file set under the identical marker filter.

### Verdict — the 38/28 split is **weight-driven**, not overhead-driven

| Phase delta (`test (a)` − `test (b)`) | Seconds | Minutes | Share of the gap |
| --- | ---: | ---: | ---: |
| collection | +6.00 | +0.100 | 1.0 % |
| execution | **+580.45** | **+9.674** | **99.6 %** |
| teardown | −3.45 | −0.058 | −0.6 % |
| watchdog-recovery | +0.00 | +0.000 | 0.0 % |
| **total** | **+583.00** | **+9.717** | 100 % |

`m2_verdict: weight_driven`

The entire ~9.7-minute gap sits in **execution**. The overhead phases do not explain it — collection
differs by **6 s** and teardown is **lower** for `test (a)`. Watchdog recovery is 0 for both halves.

The imbalance is a **stale-weight** defect, not a pack that cannot be improved. The durations map
(`config/ci-surfaces.yml:durations`, 688 entries) claims the halves are **exactly balanced**:

| Half | Files | Map weight | Observed pytest session |
| --- | ---: | ---: | ---: |
| `half_a` | 329 | 29.22 min | 32.38 min |
| `half_b` | 326 | 29.22 min | 22.47 min |
| ratio | — | **1.00×** (tolerance 1.25×) | **1.44×** |

The LPT pack balanced the **map's** weights perfectly (29.22 = 29.22 min), yet the observed execution
diverges by 44 %. `test (a)` executes **fewer** tests (8238 vs 8724) in **more** time — its per-test
cost is 1.53× `test (b)`'s. So the map's per-file durations are wrong, not the packing: a fresh
measurement feeding the map (the collector → map **bridge**, plan Task 4b) is the lever, and it is the
one T-B's "~4-minute cycle win" depends on. Balancing execution to the observed mean would move the
slowest half from 32.4 min toward ~27.4 min — the same order as the plan's claimed ~4 min.

### Sample caveat — no both-green run exists, and the 38/28 signal is confounded with main's red

All **14 of 14** most recent completed push-to-main `python-ci.yml` runs have **at least one heavy half
red** (checked `2026-09-27T18:5xZ` over the 20-run window). There is therefore **no both-green clean
sample** to confirm the split.

The measured run is still usable for the *work* comparison: `test (b)` ended `rc=1` with **2 failures
out of 8724 and did NOT hit `--maxfail=20`** (no "stopping after 20 failures"), so its 1348.22 s
session measured its **full** file set — it is not a truncated red. `test (a)` was green (`rc=0`). The
comparison is therefore valid for execution cost, but the *presence* of main's red on one half in every
available run is itself a finding: **the 38/28 imbalance cannot be cleanly separated from the live
main-red defect (⟨C4⟩) until a both-green run exists.** That is a precondition for T-B, not a detail.

### Not measurable (recorded, not silently zeroed)

- **pytest's in-run collection time** is not printed by the suite; the standalone collect-only step
  (82 s / 68 s) is the proxy. The phase **sum** does not depend on it.
- **watchdog-recovery** is 0 **because neither half was killed in this sample**. The partition is
  stated so that a killed run yields a non-zero value; a 0 here is a measured 0, not an assumption.
- **The collector path itself** — `docs/ci-timing.json` is empty, so M2's own reading of the collector
  is **UNKNOWN**. The collector was refreshed into a temp dir only; the committed artifact was **not**
  modified (Task 4b owns `tools/ci_timing.py` + `.github/workflows/ci-timing.yml`; one writer per
  file per wave).

### Reproduction

```bash
# 1. Primary read — the collector's committed artifact (empty ⇒ UNKNOWN, never 0)
wc -c docs/ci-timing.json            # 43
shasum -a 256 docs/ci-timing.json

# 2. Fallback — the collector's own eligible-run selection, then the step table
python3 tools/ci_timing.py --repo daniel-ospina/tortoise --pick-run          # 36333730533
python3 tools/ci_timing.py --repo daniel-ospina/tortoise \
    --run-id 36333730533 --out-dir /tmp/m2-collector --logs-dir /tmp/m2-nologs

# 3. Raw job logs (the sanctioned fallback read; 0 bytes ≠ empty)
gh api --allow-escape-sequences repos/daniel-ospina/tortoise/actions/jobs/108668615242/logs > a.raw
gh api --allow-escape-sequences repos/daniel-ospina/tortoise/actions/jobs/108668615136/logs > b.raw
grep -aoE "[0-9]+ passed[^=]* in [0-9.]+s" a.raw   # pytest session banner
grep -aoE "pytest exit code: [0-9]+"        b.raw
grep -ac  "stopping after"                  b.raw   # 0 ⇒ --maxfail not hit

# 4. Duration-map weights per half (consumes ci_selection.py, never a second packer)
python3 -c "import sys; sys.path.insert(0,'tools'); import ci_selection as c; \
m=c.load_manifest(); l=c.push_legs(m); d=c._durations_map(m); \
print({h: round(sum(c._duration_weight(d.get(f if f.endswith('.py') else f+'.py')) for f in l[h])/60, 2) for h in ('half_a','half_b')})"
```
