# Scoping — #1656: revalidate E2E-8 300ms latency band for bge-small on a deployment-class box (T15, #1349 follow-up)

> issue-scoping v5.1 double diamond · complexity: standard · team: epistemic-team
> Session: 2026-08-24 · Worktree: /private/tmp/tw-1349 (read-only; matches main caf98bf2)
> Deliverables: this scoping.md + research.md (Phase 1.5 artifact) → /tmp/followup-docs/1656/

---

## Confirmed Problem (Phase 2, converged through 5 verification cycles)

**Discharge the pre-registered T15 obligation of ADR-009/#1349:** re-validate the E2E-8 p95 ≤ 300ms
latency band for the **shipped bge-small-en-v1.5 embedder** (+ the MiniLM control as the relative
anchor) on a **deployment-class benchmark box** (shared-CPU 2 vCPU / 4GB — fly.toml's class, the
T15/GATE (d) operationalization of "deployment VM class"; NOT the 16–32GB burn box), using the
CI-tested protocol (`benchmarks/run_report.py --model bge-small` / `--model minilm`,
`--load-timeout 300`, default query_mix, censored warm p95, `--samples 100`, ≥3 repeats
median-aggregated), **keeping 300ms as the verdict criterion** (triple-anchored: #316
pre-registration, ADR-009 tenant-visible commitment, user's 2026-08-21 option-1), and **record in
ADR-009**: (1) the verdict per the #316 taxonomy (achieved/cap-dominated/tail/inconclusive) with
censored + elevated columns + `headroom_ms_317`; (2) the bge-vs-MiniLM delta with the pre-committed
sanity overlay; (3) a surface-caveat annex; (4) the pre-committed decision table (below) applied as
a recorded, atomic decision. **Restored pre-registered T15 leg**: per-encode microbench (1/32
texts) to measure the encode share on the deployment box (feeds the delta sanity check + future
rotation decisions).

**Why this framing** (evidence): the surface and the criterion are pre-registered (plan T15: "E2E-8
≤300ms p95 warm, censored arm, default query_mix, **on the deployment-VM class (comparability with
the pre-swap GATE (d) measurement — cycle-6 P2 fix)**"; GATE (d) pins "the deployment VM class").
The issue's phrase "production-class box" is ambiguous against the plan's disambiguation
(benchmark box = 16–32GB/4–8 cores for the burn; deployment VM = the class target) — resolved to
the **deployment class**. The pre-registered protocol **cannot run on the actual deployed surface**
(no Docker daemon on Fly; Dockerfile.hosted ships only `tortoise/`; `entrypoint.sh` FATAL-rejects
`TORTOISE_EMBEDDER_OVERRIDE`; `HF_HUB_OFFLINE=1` with only bge baked → MiniLM control unloadable) —
so a **spec'd box of the class, repo-direct**, is the only surface the protocol runs on at all,
and it is the class T15 pinned. The band is **not re-derived pre-measurement**: it is a fixed,
tenant-visible, user-confirmed criterion; re-derivation is a **decision output** (triggered if the
control also exceeds 300ms on a clean deployment-class box) requiring a #316 scoping revision +
product sign-off, with the swap retained meanwhile (revert would forfeit the measured +15.7%
turn_recall@10 for latency parity with a *slower* baseline).

**Falsification check**: this definition is wrong if (a) the user's 2026-08-21 decision or ADR-009
actually re-scoped the criterion to relative-only or a re-derived band (checked: the override +
GATE (d) + T15 all say the absolute 300ms on the deployment-VM class); (b) the deployment-class
box cannot be provisioned or run the protocol (then "what production-class means" re-opens toward
the recorded fallbacks: laptop + cgroup pins + documented non-class caveat, or a downgraded
directional read); (c) a staging pilot appeared and showed the deployed surface ≤300ms (the
spec'd-box exercise then answers a stale question — recorded as a caveat).

**Confidence: 82/100** (residual: box availability/class fidelity; the 2GB-vs-4GB class language
drift; whether the user reads "production-class box" as the burn box — the scoping records the
resolution explicitly).

---

## Phase 1 — problem-diverge (2 agents)

### Agent A — alternative framings
- **F1 — band re-derivation**: the 300ms band is a pre-real-model artifact (#316, synthetic
  stand-in vectors; competitor-informed Neo4j ~340ms / Supermemory <300ms); every real-model
  measurement exceeds it INCLUDING the MiniLM control (2× faster per encode) → re-derive the
  absolute budget like T14 re-derived cosine thresholds. *Weakness*: rejects the user-confirmed
  pre-registration + the tenant-visible ADR-009 commitment; needs a #316 scoping revision; decides
  the contract on contended-hardware noise.
- **F2 — relative-to-control as the operative criterion**: certify bge E2E-8 ≤ MiniLM control on
  the same surface; absolute as context. *Weakness*: collapses the tenant contract to "not worse
  than the thing replaced"; #317's headroom (300 − elevated p95) still needs an absolute.
- **F3 — deployment-surface validation first**: the real topology (Fly microVM → FalkorDB Cloud
  TLS) has never been measured; a bench-box pass doesn't transfer. *Weakness*: requires staging
  infra that doesn't exist; impossible for the paired control (MiniLM not baked, probe seam FATAL
  in prod, no Docker on Fly); project-workflow scope.
- **F4 — original (absolute band on a production-class box)**: valid IF pinned to the deployment
  class + band history acknowledged in interpretation.

Assumption map (A1–A12, selected): A2 "deployment class can run the harness" = **likely false as
specified** (5 verified constraints above); A4 "bge encode is the dominant latency term" =
**falsified** (encode ≈ +3–5ms vs a 300ms budget; #316: budget "dominated by non-strategy
overhead"; the 2×-faster control also fails the band → DB/CPU/network-bound); A6 "measurement
reproducible" = partially validated (provenance exists; pre/e2e8 artifacts never committed); A7
"T15 fulfills GATE (d)" = **false** — it is a post-sunk rescue measurement (verdict-final.json
`blocked: true`); A11 "2GB VM context current" = **falsified** (fly.toml 4GB, #545).

### Agent B — devil's advocate challenge
1. Plan T15/GATE (d) pin the **deployment-VM class**, not a "production-class benchmark box"; the
   plan explicitly separates the 16–32GB burn box from the deployment VM.
2. The protocol **cannot run on the deployment surface** (5 verified constraints) — a "staging
   deployment" answer to the issue's research question is a trap.
3. The blocking 344.61ms is **misattributed** (identical across candidates in verdict-final.json;
   `blocking_reasons` names arctic-xs; `pre/e2e8-*.json` artifacts never committed; burn at
   ae2c388c → code_sha-drift block on re-run).
4. The 300ms band is a #316-fixed competitor-informed artifact promoted to a **tenant contract**
   in ADR-009; re-deriving it silently revises that contract.
5. T15 has **4 acceptance items** (E2E-8, per-encode microbench, 2GB-envelope re-run — script
   `envelope_2gb.sh` never created + threshold stale vs 4GB prod, HNSW winner-vs-control spot-check
   — satisfied pre-swap by a committed artifact); the issue covers only E2E-8.
6. The swap already shipped → re-validation is **post-hoc** with motivated bias toward PASS.

---

## Phase 2 — problem-converge (2 agents, independent)

Both agents (~80/82 confidence) converged on the hybrid: **keep 300ms as the criterion, measure
both models on a deployment-class box, record verdict + delta + surface caveats + decision, and
treat band-realism (control also >300ms on a clean box) as the pre-committed trigger for a #316
scoping revision + product sign-off** — never a silent pre-measurement re-derivation. F1 rejected
(confound-based: never-met is contended-hardware confounded; measure first, re-derive as a
documented output); F2 rejected (collapses the tenant contract + #317's absolute formula); F3's
fact accepted, prescription rejected (unactionable + impossible for the paired control; the
spec'd deployment-class box is the ONLY surface the pre-registered protocol runs on, and it is
the class T15 pinned — a 16–32GB box would overstate the class).

---

## Phase 1.5 — external research

Full artifact: **research.md** (same dir). Summary: deployment class = Fly shared-cpu-2x
(2 shared vCPU / 4GB; quota 5ms/80ms per vCPU = 10ms/80ms combined ≈ 12.5% of one core sustained,
burst-balance throttling, AMD EPYC/KVM — the E2E-8 p95 is quota-shaped by design on this class);
industry practice = pre-deploy validation on production-class hardware + post-deploy monitoring
(repo precedent: live-endpoint runbooks; no product-side E2E-8 monitor — gap); the band should
NOT be re-derived pre-measurement (triple-anchored; never achieved with a real embedder on any
surface incl. the control; re-derivation = decision output via the decision table); FalkorDB Cloud
adds a TLS/network hop excluded from the local-Docker measurement.

---

## Phase 2.5 — problem-verify (5 cycles)

| Cycle | Verifier A | Verifier B | Controller action |
|---|---|---|---|
| 1 | 2 P1 (inconclusive branch missing; research artifact not in-repo) | 3 P1 (decision table not pre-committed; 4GB memory envelope; T15 legs disposition) | Fixed all 5 into v2 confirmed problem |
| 2 | 1 P1 (deliverables not yet written — timing artifact, resolved by final write) | 2 P1 (row precedence/overlap; delta boundary + confirmation inversion) | Fixed into v3 decision table (precedence, mechanical clean-box, ×1.25 pin) |
| 3 | 4 P1 (dirty-probe hole; unreachable tail row; spread flag unconsumed; ×1.25 derivation inconsistent) | 5 P1 (most-likely-outcome unmapped; row 2 forced #316 revision vs issue menu; ×1.25 inside noise + knife-edge; sanity precedence; spread routing) | Fixed into v4 (dedicated-box redefinition, noise-aware boundary, sanity overlay, spread→row 1, tail reorder) |
| 4 | 4 P1 (row-4 claim revision missing; throttle X% unpinned + no probe; dedicated-vs-shared box; warmup unevaluable) | 4 P1 (same set + corpus un-pinned) | Fixed into v5/v6 (row-4 atomic record + claim revision; throttle probe as T1 task with pinned 5% threshold; box = fly.toml class with contention recorded; warmup proxy = `warmup_iters == max_iters`; corpus pinned synthetic-1000 labeled synthetic-scoped) |
| 5 | **NO P0/P1** (state space complete; 4 mechanical P1s fixed; P2/P3 residuals accepted) | — | Gate PASSED |

**Cycle-5 verdict**: the (control, bge, spread, throttle, overlay) state space maps every cell to
exactly one row; no improvised boundaries; residuals are accepted P2/P3 pins (all incorporated
below).

---

## Decision Table v6 (pre-committed, precedence-ordered — the verdict procedure)

**Box**: dedicated deployment-class box = **fly.toml class** (`cpu_kind=shared`, 2 vCPU, 4GB),
contention environment recorded in the report. **Clean** = warmup converged (proxy:
`warmup_iters < warmup_max_iters`; non-convergence → row 1) + no co-tenant load (we control the
box); **quota-imposed throttling on the shared-CPU class is class evidence, not interference** —
recorded via the throttle probe, never silently disqualifying (a dirty probe with control ≤ 300 is
conservative-direction: throttling inflates, so MET stands, flagged). **Repeats**: per-run
censored p95, median of ≥3; `max_spread = max(control_spread, bge_spread)`; low-confidence if
max_spread > ±50ms or >20% relative → one extra repeat round → still dirty → row 1. **Auto-accept
zone**: `bge ≤ control + 2×max_spread` (thesis-preserving; a bge slower than control beyond noise
is NEVER auto-accepted). **Elevated column + cap-share recorded in every row** (cap-masked vs
true-slow: censored ~460 but elevated ~320 → cap/infra note, not band re-derivation). **Corpus
pinned**: synthetic 1000 (`--corpus-size 1000 --seed 42`), verdict labeled **synthetic-scoped per
#316 AC7** (production-scoped verdict withheld until a real-corpus measurement exists).

| # | Condition (censored p95, deployment-class box) | Action |
|---|---|---|
| 0 | INVALIDATED (DB pre-flight fail, invalidating sample, OOM/swap — `memory.events/oom_kill`, `memory.peak`, swap evidence) | Record + diagnose + re-run once; persistent → **NOT-CERTIFIED** + human (atomic 24h record — artifact: `evidence/t15-verdict.json` `"certified": false` + ADR-009 Status update + issue comment; 24h no-response → recorded default per product-call convention) |
| 1 | inconclusive (>30% capped/degraded; warmup non-convergence via the proxy; `--load-timeout` expiry; low-confidence spread after the extra round; **throttle-share > 5%** of the measured window — `throttled_time/(throttled+running)` from the cgroup `cpu.stat` snapshot-diff) | Diagnose + re-run once with recorded cause; persistent → NOT-CERTIFIED with degradation fraction + probe data + human (atomic 24h record) |
| 2a | control > 300 AND bge ≤ 300 | **MET** — the shipped embedder holds the band on the deployment-VM class; control-only band-realism recorded (control can't meet it; bge does); tenant claim stands; #317 headroom recorded; if control > 500 also flag system-cap finding |
| 2b | control > 300 AND bge > 300 — **band-realism** | Class cannot meet the absolute band regardless of embedder → **human-gated menu** per the issue's pre-named options, recorded atomically: **accept-delta DEFAULT** (pre-selected, NOT auto-applied; swap retained — revert cannot meet the band either and forfeits +15.7% recall; #316 scoping revision ONLY if the user explicitly chooses it), **throttle** (concurrency/request shaping — a product change, own scope if selected), **revert** (only if bge > control + 2×max_spread — investigate first, revert on human confirmation); 24h no-response → accept-delta recorded WITH the mandatory tenant-claim revision (ADR-009 launch-checklist ≤300ms claim updated to the measured envelope in the same commit, or explicit human authorization for the claim to stand); control > 500 → also escalate system-cap finding |
| 3 | control ≤ 300 AND bge ≤ 300 | **MET** — band holds; swap retained; #317 headroom recorded; overlay relative-plausibility flag (bge slower than control within band → flag) |
| 4 | control ≤ 300 AND bge ∈ (300, 500] AND bge ≤ control + 2×max_spread | **accept-delta** — recorded auto-decision (bge not slower than control beyond noise — thesis preserved; user pre-committed option-1) + **mandatory tenant-claim revision** in the same commit or explicit human authorization (the ≤300ms claim is factually false here) |
| 5 | control ≤ 300 AND bge > 500 (tail) | **revert to MiniLM** (pre-registered rollback: re-bake `EMBEDDING_MODEL` + force-re-embed) + escalation; #317 headroom recorded; tenant-claim revision (claim can stand if control holds the band) |
| 6 | control ≤ 300 AND bge ∈ (300, 500] AND bge > control + 2×max_spread (thesis-reversal beyond noise) | **investigate + escalate** (injection hygiene, control-arm failure fractions, breaker state, microbench encode ratio, cache isolation); revert only on human confirmation; NEVER auto-accept a thesis-reversal; tenant-claim revision on keep-bge outcome |
| overlay | sanity/hygiene guard ABOVE rows 2a–6: injection effectiveness (probe state hf_id + discriminating-embedding check — harness `EmbedderProbeError` abort IS the invalidation evidence) and model-keyed cache isolation → row 0/1 semantics; control-arm hygiene + relative-plausibility flags (bge-faster-by->10% relative, delta ≤ 1% relative, bge slower-than-prior) → flag + record; absolute-band certification proceeds; relative claim re-scoped to the deployment box | per item |

**Number-empirical acknowledgement**: `max_spread` (and any boundary derived from it) is
pre-committed as a formula but data-empirical on the repeat spread — the same class of double-dip
as #1349's n-adaptive bar; acknowledged in the ADR record.

---

## Phase 3 — Codebase Explorer findings (key)

- **Harness (complete, CI-tested, unchanged)**: `benchmarks/run_report.py` — `--model`
  (L799–803), `--load-timeout` (L810–815), `--samples` (default 50 → pin 100), `--seed`,
  `--corpus-size` (default 1000), `--out`; `--model` injection before E2E-8 (L525–530, HARD FAIL);
  DB pre-flight `RETURN 1` (L596–604, fail → INVALIDATED); E2E arm via `sdk.tortoise_fts_query`
  (L692–717; in-path encode sdk.py L9573–9588); provenance auto-captures host specs, probe-truthful
  `embedding_model` hf_id, git sha, `db_mode`, warmup state, corpus fingerprint (L133–170).
  `benchmarks/bench_core.py`: `E2E_TARGET_MS=300`, `CAP_MS=500`, `ELEVATED=5000`,
  `e2e_verdict` bands, `headroom_ms_317`, warmup CV<10%/max-20-iters, `E2E_CAP_DOMINANCE_FRACTION=0.30`.
  `tools/embedder_probe.py`: `PROBE_MODELS` revision-pinned (bge `5c38ec7c…`, minilm `1110a24…`),
  `inject_model` HARD FAIL + warm-singleton discriminating-embedding check.
- **ADR-009 append anchor**: Evidence Summary "Post-swap E2E-8 re-run" bullet (L283–291); Status
  block wording fix needed ("production-class benchmark box" → "deployment-VM class (2 shared vCPU /
  4GB shared-CPU)"); `verdict-final.json` misattribution (344.61ms → arctic-xs) should be annotated.
- **Evidence discipline**: `docs/research/2026-08-17-1349-embedder-selection/evidence/` README +
  `manifest.json` (report_sha/code_sha/resolved_revision); `benchmarks/reports/` is gitignored →
  reports must be copied to the committed evidence dir.
- **Runtime traps**: `TORTOISE_DB_URI` env-only (no `.env` load in run_report → silent embedded
  fallback if unset); Docker FalkorDB pinned `falkordb/falkordb-server:v4.16.7` (docker-compose,
  cpus 2 / mem 2g); HF cache needed for BOTH pinned models under `HF_HUB_OFFLINE=1`.
- **Absent artifacts (net-new)**: no microbench tool, no throttle probe, no repeats/aggregation
  runner, no envelope script, no CI gate for T15 evidence.

---

## Phase 4/5 — solution-diverge/converge

### Approaches
- **A — Dedicated deployment-class benchmark box (pre-registered surface)**: throwaway
  2 shared vCPU / 4GB box (burstable cloud VM = closest class proxy, e.g. Hetzner CX22 / AWS
  t3.small / GCP e2-medium; cgroup-pinned Docker `--cpus=2 --memory=4g` on a larger host = most
  reproducible, no throttle simulation; local VM = noisiest — all recorded in the annex), repo-
  direct bench, Docker FalkorDB v4.16.7, HF cache both pins, campaign 2 models × ≥3 repeats ×
  `--samples 100` (alternating), median aggregation, evidence copy + sha manifest, ADR-009 update.
- **B — Fly staging machine + benchmark-capable image + falkordb sidecar**: real Fly
  throttle/region/DB-hop fidelity; **rejected** — T11 design-intent tension (probe seam + control
  model in a deployable image = backdoor risk), staging app + bake validation + billing for one
  annex sentence, staging has no tenant load either. *B would have been better if* the 300ms
  criterion were anchored to bit-exact Fly scheduler behavior (it isn't — the pre-registered
  criterion is the deployment-class box).
- **C — Box run + directional live-hosted client-side wall-clock leg**: replay `query_mix.json`
  over the live hosted endpoint (stdlib `urllib/http.client`, the #316 MCP-conformance arm
  precedent), bge-only (no control on the shipped path), measures the true shipped path (Cloud TLS
  hop + tenant load + real Fly quota by construction). **Kept as OPTIONAL, never-gating annex leg**
  — trigger: run iff row 2b fires or box budget remains; otherwise record `annex_leg_c: not-run` in
  `t15-verdict.json` and keep the caveats as exclusions. Tenant-safety pins: off-peak window,
  capped samples (directional, well below the #316 ≥50 conformance norm), no concurrent box
  campaign, abort-on-error/on-slow-endpoint, non-comparability note (includes TLS hop + real quota
  + tenant load).

### Chosen: **A + optional C**. Rationale: A discharges the pre-registered obligation as written
(the decision table is written for A); A is the only surface with a real control arm (B needs the
probe seam in a deployable image; C physically cannot run a control); A maximizes reproducibility
(CI-tested protocol + pinned FalkorDB + pins + sha manifest); the DB-host-split fallback is
pre-built into A (third surface — absolute numbers marked non-comparable, paired delta stays
comparable). C costs ~nothing and converts two annex caveats into directional evidence.

### Rejected alternatives
- B (above) — rejected on T11 risk + cost; better only for bit-exact Fly scheduler anchoring.
- C as the verdict surface — rejected: no control arm, real corpus ≠ synthetic bench corpus
  (p95 non-comparable), production-load risk, violates the box contract; better only if the
  question were "is the shipped path within budget today" (a monitoring question) or no box were
  acquirable (recorded downgrade).
- D — re-run on the contended laptop with cgroup pins (implicit null option) — rejected by the
  issue itself: the ADR "Post-swap E2E-8 re-run" bullet (345–460ms 'inconclusive') is the proof
  the contended laptop cannot resolve the band. **Recorded fallback if no box is acquirable**:
  laptop + cgroup cpu/mem pins to 2vCPU/4GB + documented non-class caveat (provenance auto-captures
  host specs, so the deviation is visible), with the verdict-validity note updated.

---

## Plan Draft (standard-proportional; NO hosted-image/entrypoint changes)

### Task 1 — Small tools (the only new code)
- `benchmarks/encode_microbench.py`: 32 fixed texts (first 32 `query_mix.json` queries,
  deterministically ordered), batch_size=1, ≥3 discarded warmup encodes, ≥5 passes; per-model
  ms/encode mean/p50/p95 + bge/minilm ratio; `--model` via `embedder_probe` (HARD FAIL on load),
  `--load-timeout` override, fresh process per model, `HF_HUB_OFFLINE=1`; records probe state
  (hf_id + resolved revision).
- `benchmarks/throttle_probe.py`: cgroup `cpu.stat` snapshot-diff (`throttled_time`,
  `nr_throttled`/`nr_periods`) + `/proc/stat` steal over each measured window; emits
  throttle-share for the annex + the row-1 trigger (share > 5% → row 1).
- `benchmarks/t15_verdict.py`: pure function applying decision table v6 (rows 0–6 + overlay +
  boundary) from the campaign manifest; **validates the manifest's per-report sha + code_sha
  before applying** (gate_1349.py discipline; sha-mismatch → refuse).
- **Tests** (registered in `config/ci-surfaces.yml` + halves, `bench` marker, skip-if-not-cached
  convention): microbench determinism/stats/CLI/HARD-FAIL/output schema; verdict applier — all 7
  rows + overlay flags + boundary + precedence + sha-refusal + row 2a/2b split + control>500
  system-cap flag.
- **AC**: both tools run on the box; unit tests pass; `tortoise/`, `entrypoint.sh`,
  `Dockerfile.hosted` untouched.

### Task 2 — Box provisioning + pre-flight (manual ops)
- Acquire the deployment-class box per human decision (burstable cloud VM preferred; cgroup-pinned
  Docker fallback; laptop+cgroup-pins last resort, non-class caveat). Python 3.12 + uv, Docker +
  compose, repo at a recorded sha, `uv sync --extra embeddings`, `docker compose up`
  (falkordb v4.16.7, image digest recorded).
- HF cache both pinned revisions (bge ~127MB, MiniLM ~90MB) + `HF_HUB_OFFLINE=1` verify offline
  load; `TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise` exported (no `.env` load).
- **Peak-RSS pre-flight** ≤ 4GB (bench process both models + FalkorDB container; joint/co-running
  check; verify the compose mem cap is enforced via `docker inspect`); DB-host-split pre-decided
  fallback (second local VM, v4.16.7, no TLS — third surface, absolute non-comparable, paired
  delta comparable).
- Throttle-probe baseline; microbench dry-run both models.
- **AC**: pre-flight log (host specs, sha, image digests, HF revisions, peak RSS, throttle
  baseline); both models load offline under `--load-timeout 300`.

### Task 3 — Run campaign (manual ops)
- Per model, ≥3 fresh-process repeats: `TORTOISE_DB_URI=… HF_HUB_OFFLINE=1 python -m
  benchmarks.run_report --model <m> --load-timeout 300 --samples 100 --corpus-size 1000 --seed 42
  --out benchmarks/reports/t15-<m>-rep<N>.json` — order pre-registered alternating (bge r1, minilm
  r1, …) to expose drift.
- Censored warm p95 = verdict column (validity: failure fraction ≤ 0.30, else row 1); elevated p95
  + cap-share + `headroom_ms_317` recorded; throttle probe snapshot-diffed per window; low-
  confidence spread → one extra repeat round per model; microbench full pass AFTER the campaign;
  OOM/swap → INVALIDATED (never classified); optional throttled verification repeat per model if
  the box can be cgroup-pinned to the class quota (10ms/80ms) — transfer check, else recorded caveat.
- **AC**: ≥6 report JSONs (≥3/model) each with `provenance.db_mode == "docker-falkordb"`,
  `provenance.embedding_model == <candidate hf_id>`, warmup proxy, failure fraction, spread,
  throttle diff, peak RSS.

### Task 4 — Evidence + ADR-009 record (docs)
- Copy reports + microbench + probe outputs to
  `docs/research/2026-08-17-1349-embedder-selection/evidence/` (`t15-*` prefix); write
  `t15-campaign.json` (box class + acquisition, pins, per-run stats, medians, spread, probe states,
  overlay results), `t15-microbench.json`, `t15-verdict.json` (row + evidence + recorded decisions,
  `annex_leg_c` disposition); sha256 manifest mirroring `manifest.json`'s
  report_sha/code_sha/resolved_revision schema; update `evidence/README.md`.
- ADR-009: (1) Status wording fix → "deployment-VM class (2 shared vCPU / 4GB shared-CPU)"; (2)
  Evidence bullet superseding the "Post-swap E2E-8 re-run" bullet with the deployment-class result
  (verdict, censored/elevated p95, headroom, median+spread, git sha, db_mode, FalkorDB tag, report
  SHAs, microbench ratio, sanity overlay results); (3) surface-caveat annex (six items: Fly quota
  = class evidence; Cloud TLS hop excluded; tenant load excluded; burn non-comparability incl. the
  "no committed deployment-class pre-swap anchor"; "2GB VM class" stale vs 4GB; repo-direct
  probe-injected surface mirrors GATE (d)'s own pre-swap surface, not the baked image); (4)
  decision-table application (row, boundary, spread, recorded auto-decisions, max_spread
  double-dip acknowledgement); (5) **annotate/supersede `verdict-final.json`** with the corrected
  disposition (statistical core PASSED, latency precondition unmet-on-class-surface, user override,
  T15 = the re-validation — so `blocked: true` can't be read as a stale veto).
- **AC**: ADR-009 self-contained; manifest sha-checks every artifact; secrets scan clean; evidence
  PR through commit-workflow (docs-only + 2 small tools + tests — code-review gate applies).

### Task 5 — Decision application + follow-up filings
- Apply decision table v6 row + overlay flags; execute the row action (rows 0/1 → atomic-24h
  NOT-CERTIFIED record; 2a/3 → MET + close; 2b → human-gated menu with accept-delta default +
  atomic record + tenant-claim revision; 4 → recorded auto-decision + claim revision; 5 → revert +
  escalation; 6 → investigate + escalate + revert-on-confirmation). File follow-up issues:
  1. **4GB envelope script** (`envelope_4gb.sh` — cold-start/pre-warm thresholds matching fly.toml;
     the plan's `envelope_2gb.sh` was never created; 2GB→4GB threshold change documented);
  2. **post-swap HNSW recall spot-check under recalibrated thresholds** (verify the committed
     artifact's threshold version first — recalibration touched eval-critical files);
  3. **production-latency monitor** (product-side E2E-8 p95 telemetry — the post-deploy gap).
- **AC**: #1656 closed with the recorded row + disposition; follow-ups filed with evidence links.

### Verification plan (what proves the run valid before any verdict)
`db_mode == docker-falkordb`; `embedding_model` == injected candidate hf_id (probe-truthful);
corpus fingerprint + indexes fts/vector true; warmup proxy clean; failure fraction ≤ 0.30; spread
within bounds; throttle diff recorded; peak RSS ≤ 4GB; fresh process per model, identical
seed/corpus/samples; `HF_HUB_OFFLINE=1` (cache isolation). Then `t15_verdict.py` applies the table
mechanically; row + flags reviewed; sha-manifest matches committed artifacts.

### Acceptance criteria (issue O/I/T mapping)
- **Objective**: T15 discharged — deployment-class E2E-8 re-validation recorded in ADR-009 with a
  #316 verdict + decision-table disposition; no hosted production code changed.
- **Indicators**: (1) E2E-8 p95 for bge-small AND MiniLM control on the deployment-class box
  (≥3 repeats, median, provenance-captured host); (2) verdict recorded in ADR-009 (band met/not
  met + delta vs control + decision-table row).
- **Targets**: bge p95 ≤ 300ms verdict criterion held fixed; if not met → the pre-committed
  decision (accept-delta default / throttle / revert) recorded atomically with the tenant-claim
  revision.
- **Bonus (pre-registered T15 leg)**: microbench ratio recorded.

### Runtime prerequisites (enumerated)
Python 3.12 + uv; `uv sync --extra embeddings` (sentence-transformers >=3,<6 + sklearn); Docker +
compose with `falkordb/falkordb-server:v4.16.7`; HF cache: bge-small `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`
+ all-MiniLM-L6-v2 `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` (~220MB total); ~5–8GB disk; no API
keys (offline, local); `TORTOISE_DB_URI` exported; peak RSS ≤ 4GB pre-flight. **Wall-clock**: box
~2–4h total (provisioning 10–30min, deps+cache 15–30min, pre-flight+microbench dry-run 15min,
campaign 6 runs × 8–12min ≈ 1–1.5h, microbench 5min, evidence+ADR 1–2h).

---

## Phase 6 — Wiring check

| Touch Point | Type | Covered By | Status |
|---|---|---|---|
| benchmarks/run_report.py, bench_core.py | harness (existing, read-only) | T3 | ✅ |
| tools/embedder_probe.py | probe seam (existing) | T1 (microbench) + T3 (campaign injection) | ✅ |
| tortoise/embeddings.py, sdk.py, search_engine.py | prod code (measured, unchanged) | T3 (in-path E2E-8 arm) | ✅ |
| Docker FalkorDB v4.16.7 (docker-compose.yml) | external service | T2/T3 (pinned, prod-parity) | ✅ |
| HF cache (both revision pins, HF_HUB_OFFLINE) | external dep | T2 | ✅ |
| TORTOISE_DB_URI env (no .env load) | config | T3 (explicit export; silent-embedded trap) | ✅ |
| benchmarks/reports/ (gitignored) | artifact staging | T4 copy → committed evidence dir | ✅ |
| docs/research/…/evidence/ (t15-* + sha manifest) | evidence | T4 (report_sha/code_sha discipline) | ✅ |
| docs/adr/ADR-009 (Status, Evidence, annex) | docs | T4 (wording fix + verdict + annex) | ✅ |
| evidence/verdict-final.json (misattribution) | evidence | T4 (annotation/supersede) | ✅ |
| config/ci-surfaces.yml + python-ci.yml halves | CI | T1 (register new tests) | ✅ |
| commit-workflow (review gate) | git | T5 (docs-only evidence PR) | ✅ |
| New tools (microbench, throttle probe, verdict applier) | new code | T1 + unit tests | ✅ |
| Follow-ups (4GB envelope, HNSW re-check, latency monitor) | issues | T5 filings | ✅ |
| #317 reranker (downstream consumer) | cross-cutting | `headroom_ms_317` recorded in every row | ✅ |
| Hosted image / entrypoint (T11 intent) | deploy | UNCHANGED by design | ✅ (no touch) |

---

## Complexity

| Domain | Rating | Rationale |
|---|---|---|
| Architecture | standard | 3 small stdlib tools + manual ops + docs; no prod-code, image, or entrypoint changes |
| UX | low | no UI surface |
| Ontology | low | no data-model or schema change |

## Open Questions for the Human

1. **Box acquisition** — method/owner/budget: burstable shared-CPU cloud VM (recommended — closest
   class proxy), cgroup-pinned Docker `--cpus=2 --memory=4g` on a larger host (most reproducible,
   no throttle simulation), or local VM; last-resort fallback = laptop + cgroup pins + documented
   non-class caveat.
2. **Row 2b menu pre-selection** — confirm **accept-delta as the DEFAULT (human-gated, not
   auto-applied)** with the atomic 24h-record convention + mandatory tenant-claim revision, so the
   plan can pre-wire the record artifact.
3. **Optional live-leg (C) opt-in** — runs a capped, off-peak query_mix replay against the LIVE
   hosted endpoint (production-load risk, directional-only, bge-only). Default: record
   `annex_leg_c: not-run` and keep the caveats as exclusions.
4. **Follow-up filings** — approve filing: (a) 4GB envelope script + cold-start/pre-warm,
   (b) post-swap HNSW spot-check under recalibrated thresholds (threshold-version verification
   first), (c) production-latency monitor.
5. **Corpus pin** — confirm synthetic-1000 with the verdict labeled "synthetic-scoped per #316
   AC7" (production-scoped verdict withheld until a real-corpus measurement), vs providing an
   EventLog-replay corpus.
6. **Close vehicle** — confirm a docs-only evidence PR through commit-workflow (with the two small
   tools + tests code-reviewed) is the acceptable close for #1656.

## Rejected Alternatives (summary)
- **Band re-derivation (F1)** — premature; decides the tenant contract on contended-hardware
  noise; needs #316 scoping revision; re-derivation remains a triggered decision OUTPUT.
- **Relative-to-control criterion (F2)** — collapses the tenant contract + #317's absolute formula.
- **Staging/Fly deployment surface (F3/B)** — protocol cannot run there (5 verified constraints);
  T11 backdoor risk; project scope; better only for bit-exact Fly scheduler anchoring.
- **Live-hosted leg as verdict (C alone)** — no control, non-comparable corpus, prod-load risk.
- **Contended laptop re-run (D)** — already proven incapable (ADR post-swap bullet); retained only
  as the recorded no-box fallback.

## Review Cycle Log
problem-verify: 5 cycles → clean (all P1s fixed into table v6; residuals P2/P3 incorporated).
solution-verify: 1 cycle → clean (no P0/P1; 4 P2s + P3s incorporated).
Second-model coherence (deepseek second-model gate): 1 cycle → 1 P1 (corpus pin) fixed; 6 P2s
incorporated.
