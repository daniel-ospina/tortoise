# Capstone verification report — epic #2080 (gbrain learnings)

**Status: NOT PASS — the epic cannot be marked complete** · **Commit verified on:** `d1f2d345cd73df2d402af775885a94b455870ee5` · **Date:** 2026-10-11 · **Capstone:** #2107 · **Epic:** #2080

**Mode:** embedded / URI-less carve-out lane (`TORTOISE_TEST_CARVE_OUT=1`, Python 3.12, `uv sync --extra embeddings --extra parity`) — a lane CI runs (`python-ci.yml`, carve-out job) — plus one docker-lane leg on a throwaway private FalkorDB (`tools/test_lane.py`, #5084). No mocks for the graph logic under test; the harness/why/write-path suites are hermetic by construction (per-cell graphs, sealed gold, `fixtures_hash`).

## Scope

The epic's 9 detailed E2E scenarios (plan §7, `docs/planning/2026-09-01-2080-gbrain-plan.md`), walked as real journeys through their assigned test layers, against the integration-surface map S1–S15 (#2093), plus the published-artifact checks and the W4 user-exposure gate. This epic's deploy surface is backend/API + MCP tools (no GUI pages — W6 UI belongs to #1976), so "clickthrough" here means executing each E2E's own suite and reading its evidence (request/response, metric, receipt, DB state), not browser screenshots.

## Verdict summary

| E2E | Surfaces | Suite(s) executed at `d1f2d345c` | Result |
|---|---|---|---|
| E2E-1 conflict-surfacing | S6/S7/S8/S15 | `tests/test_w4_why_enrichment.py` (29), `tests/test_ranking_w4b.py` (23) | ✅ PASS (shipped assertions) — the ranking-participation ordering holds on a **single-leg read** only (#7704, addendum 2026-10-15) |
| E2E-2 write-path planted gold | S1/S2/S4/S5/S15 | `tests/eval/write_path/` corpus 85 + grading 47 + prescreen 48 + semantic_judge 41 + operator_lane 7 (docker) | ⚠️ PARTIAL (2 heavy gate files see gap note) |
| E2E-3 reflex know-to-ask / false-fire | S3/S4/S5/S10 | `tests/eval/harness/` (62) + `tests/test_volunteer_reflex_harness_seam.py` (3) | ❌ NOT PASS |
| E2E-4 source-isolation = 0 | S3/S4 | `tests/eval/harness/` isolation suite + committed baselines | ✅ PASS |
| E2E-5 ingestion toggle engine contract | S1/S2/S11/S12/S5 | `test_capture_session` + `test_capture_consent` + `test_write_verb` (276), `test_capture_phase_d_dedup` (21), `test_onboarding_w6_capture_disclosure` (12) | ✅ PASS (engine contract) |
| E2E-6 supersession-aware recall | S6/S7/S8/S15 | `test_e2e6_supersession_aware_recall` (in the 29) | ✅ PASS |
| E2E-7 dig-deeper / why-suite | S3/S6/S4 | `tests/eval/why_suite/` (68) | ✅ PASS (in-run floors) |
| E2E-8 sealed LongMemEval 500-Q run | S13/S14/S5 | `tests/test_w7_publish.py` (9) | ❌ NOT PASS (run never executed) |
| E2E-9 `/v1/context` delivery contract | S9/S10 | `test_volunteer_contract` + `test_volunteer_pipeline` + `test_hosted_volunteer_context` + `test_selfhost_volunteer_context` (61) | ⚠️ PARTIAL |

## Per-E2E evidence

### E2E-1 — why-layer conflict-surfacing (the headline)

`tests/test_w4_why_enrichment.py` **29 passed** (168 s) and `tests/test_ranking_w4b.py` **23 passed** (136 s), both on the embedded lane. The E2E's own assertions are present and green: `test_e2e1_conflict_surfacing_rate`, `test_flag_drift_all_four_surfaces` (search / analyze / eval-only ask / MCP `recall_state`), `test_flag_off_emission_byte_identical`, `test_flag_off_mcp_surfaces_byte_identical` (the R11 golden-ordering guard), `test_clean_points_carry_no_conflict_noise`, `test_assembly_bounded_batch_reads` (the N+1 guard), `test_assembly_fail_open_never_breaks_turn`, plus the ranking-participation arm in `test_ranking_w4b.py` (strict threshold boundary, uncalibrated-never-contested, delta-causality, `[0,1]` clamp).

**Addendum (2026-10-15, refs #7704).** The ranking-participation arm cited above — the E2E-1 ordering claim "the contested twin outranks its uncontested twin" — was green on a **pinned single-leg read** (`force_sparse_tfidf`), which the SDK's single-list shortcut answers without calling `rrf_fusion` at all (#7629, fixed by PR #8572, which also added a fused-path arm that pins the path-independent contract and deliberately asserts **no** ordering). Re-measured at `cb38dee1b` with the dense leg live: the claim does **not** hold on the fused read — the content-identical twins tie on every leg, each leg's `ORDER BY score DESC, node.<id> ASC` tie-break (#3019) hands the lower-ULID twin rank 0, and the post-fusion min-max similarity gap (0.0377 ⇒ 0.0188 of final score on this 4-row fixture) exceeds the boost's contribution (0.0175), so the calm twin ranks first. The gap is normalized over the **page**, so the outcome is a function of the caller's `limit` (measured: settles on the contested twin winning only at limit ≥ 40 on a 404-point graph). This is a live product question — the remedies collide with recorded decisions (#4210 open; #3019's tie-break; the #2102 E2E-1 indicator) and are **not** enacted by this report. E2E-1's conflict-surfacing assertions are unaffected; only the ranking-participation ordering is path-limited.

Latency: the ≤ 300 ms p95 hard wall-clock assertion is assigned by the plan to a dedicated perf lane that **does not exist** (#3992). The blocking mechanism assertions (zero-LLM read path, bounded traversal, batch EP reads, fail-open `degraded_reason`) are green; the wall-clock SLO is unverifiable in this repo. See Residuals.

### E2E-2 — write-path planted-gold survival (W2 benchmark)

Committed baseline `tests/eval/write_path/baselines/main.json` (product lane, `judge_pin: w2-write-path-mechanical-v2`, 6 justified history entries):

| metric | value |
|---|---|
| `salient_unit_survival_macro` / `_strict` | 0.5444 / 0.5444 |
| `provenance_accuracy` | 1.0 |
| `sessions_emitting` | 1.0 |
| `quote_fidelity` | 1.0 |
| `distractor_leakage_per_run` | 1 |

The deterministic lane (`baselines/m2.json`) records `salient_unit_survival_macro` 1.0 with `distractor_leakage_per_run` 15 — the echo extractor retains everything, including routine distractors; that is the documented shape of the deterministic lane, not a regression.

Executed in this run, on the embedded carve-out lane: `test_write_path_corpus.py` **85 passed** (5.58 s), `test_write_path_grading.py` **47 passed** (5.21 s), `test_write_path_prescreen.py` **48 passed** (5.31 s), `test_write_path_semantic_judge.py` **41 passed** (8.90 s). `test_write_path_operator_lane.py` is a **docker-lane** file and is reported separately below.

**Embedded-lane flake, diagnosed (not an E2E-2 defect):** running `test_write_path_operator_lane.py` on the URI-less carve-out lane gave `1 failed, 6 passed`, then `2 failed, 5 passed` on a second full-file run, while the failing test **passes in isolation** (169.33 s). The failure is the documented redislite dead-socket class — `redis.connection.UnixDomainSocketConnection(path=/private/tmp/tt_*/redis.socket)` → `FileNotFoundError: [Errno 2] No such file or directory` (MEMORY.md; #4879 → #4892). The file is CI-registered on the docker lane (`config/ci-surfaces.yml:617`), so it was re-run there: `TORTOISE_DB_URI='docker://:@127.0.0.1:16390/tortoise_test_matrix'` on the pinned private FalkorDB → **7 passed** (167.67 s), container still `Up`. The embedded-lane result was a lane mismatch, not a product regression.

**Gap note (honest):** the two heavy gate files did **not** complete in this lane within a bounded window. `test_write_path_benchmark.py` exceeded a 2409 s window and `test_write_path_runner.py` a 1505 s window (their committed CI weights are 101.8 s and 70.0 s at `config/ci-surfaces.yml:3058`/`:3094`). Both bounded-window kills left a `faulthandler_timeout` dump (280 s, `pyproject.toml:261`) whose live frames sit in `jax/_src/dispatch.py::apply_primitive` — the lazy JAX path in `tortoise/svbp.py:18-20`, imported by the ingest EP path. `test_write_path_runner.py` was measured at 27 tests in 93 s before the slow path was reached, so this is a specific test's cost, not a uniformly slow file.

This is recorded as an **unverified lane observation, not a defect claim**: the measurement is one machine under load average 12-18 from concurrent fleet lanes, and the embedded carve-out lane is not the lane the CI weights were taken on. It is surfaced because a file whose committed weight is ~100 s taking > 40 min is the shape that silently blows a CI shard budget; triage is the reviewer's call.

The published failure classes are unchanged from #2104's own analysis: surviving-unit misses are dominated by `content_missing` (extractor recall — #2453), not by leakage or provenance. The fix-wave re-run that the W4 user-exposure gate depends on is gated on the provider-spend decision (#2552) and has not happened.

### E2E-3 — reflex know-to-ask / false-fire

`tests/eval/harness/` **62 passed** (550 s) — the Cat-34 replay harness itself is green, and it honestly records the null-reflex baseline. **The E2E's target is not demonstrated:**

- Both committed harness baselines carry `config.reflex: "null"` (`tests/eval/harness/baselines/{main,m2}.json`), so the standing kta / false-fire / push bars are **inactive by design** (`tests/eval/harness/schema.py:486` — bars activate only when the committed baseline's `config.reflex == "graded"`). The recorded numbers are `know_to_ask_failure_rate` 1.0 / `push_precision` 0.0 / `push_recall` 0.0.
- #2099's close note assigned the re-bless to W4: *"the standing kta 0.00 / false-fire ≤ 0.03 / push-precision ≥ 1.000 bars activate when W4 (#2102) re-blesses reflex null→graded via `--bless-protocol`"*. #2102 and #2103 are both closed; neither re-blessed. #2103's close note records *"honest first numbers, no blessing — orchestrator owns"*.
- The graded seam exists and is exercised: `tests/test_volunteer_reflex_harness_seam.py` **3 passed** (137 s) and `tests/test_volunteer_reflex_harness_seam.py::test_reflex_replays_kta_corpus_with_honest_numbers` prints

  ```text
  HONEST KTA FIRST-NUMBERS: {"kta01_reminder_turns": {"missed": 2, "should": 2, "false_fires": 0, "silent_required": 4}, "kta02_aurora_status": {"missed": 2, "should": 2, "false_fires": 0, "silent_required": 3}}
  ```

  i.e. the reflex misses **4 of 4** know-to-ask turns with 0 false fires. The file names the cause: the corpus write path leaves cells unmeasured (no posterior α/β), so the 0.7 EP gate under-fires — the fix is an EP warm-start on the graded cells, owned by "the W3 orchestrator", which has no issue. Filed as **#8125**. Against the epic target (`know-to-ask failure 0.00, false-fire ≤ 0.03`) this E2E is **not met**.

### E2E-4 — source-isolation gates at zero

`source_isolation_violations` = **0** in both committed baselines, and the isolation gate is live from day one (`test_isolation_gate_live_under_null_reflex`; the gate probes the union of the other team's anchors across all suites, and a misrouted write-back regresses CI). This is the epic's anti-P0 guard and it holds.

### E2E-5 — ingestion toggle, engine contract (W5 quality)

`tests/test_capture_session.py tests/test_capture_consent.py tests/test_write_verb.py` — **276 passed, 1 skipped** (267 s). `tests/test_capture_phase_d_dedup.py` — **21 passed** (155 s; content-hash idempotency + REPHRASE). `tests/test_onboarding_w6_capture_disclosure.py` — **12 passed** (194 s) on the repo-pinned FalkorDB image.

Evidence inside that set: the verified gate order (boundary 422 → 409 → 503 → 400 → 422 → 402, shared REST/MCP `_capture_session_impl`), recording-off ⇒ 409 with no Session write and no receipt, `memory_write_v1` `protocol_version` on every write response with `PROVENANCE_MISSING` rejection, provenance `source_session`/`source_harness`/`ingested_at` stamping, EP-on-ingest at the user-visible level, concurrency idempotency.

**The E2E ran on two FalkorDB images and the result depends on the image — that is an infrastructure finding, not a test finding:**

- `falkordb/falkordb:latest` (the default of `tools/test_lane.py:124`, and the image of the long-lived shared `falkordb` container): the very first test panics the **server** — `FalkorDB panic: panicked at src/reply.rs:655:58: called Option::unwrap() on a None value` in `reply_compact` ← `graph_core::execute_query` — and the container exits (1). Reproduced twice, deterministically; the surviving-client symptom is `redis.exceptions.ConnectionError: Connection closed by server`.
- `falkordb/falkordb-server:v4.20.4` (the image `docker-compose.yml:49` pins for the durable self-host topology): the same file **12 passed**, container stays up.

The disclosure-marker half of E2E-5 therefore only has docker-lane evidence on the pinned image. Filed as **#8124** — a floating `latest` tag that kills the server under a shipped test is the #5084 restart class one level down. CI is not exposed: `.github/actions/falkordb-provision/action.yml:19` defaults to the pinned `falkordb/falkordb-server:v4.20.6`.

### E2E-6 — supersession-aware recall (bi-temporal)

`test_e2e6_supersession_aware_recall`, `test_superseded_kind_pointer_only_when_supersession_data_exists`, `test_supersession_fetch_failure_degrades_honestly` — all in the green E2E-1 run (29 passed). The anti-ghost half is graded by the why-suite's resolved arm (`#2490`): a superseded (terminal) claim's why-block serves `supersession.status: superseded` and reads neither `ep.contested` nor `conflicts.contested`.

### E2E-7 — dig-deeper navigation accuracy (why-layer suite)

`tests/eval/why_suite/` — **68 passed** (195 s). The suite grades the canonical §3.1.4 why-block built by the REAL `tortoise.why.assemble_why_blocks` over the shared 40-point planted-conflict corpus and asserts the E2E-7 floors in-run: `conflict_surfacing_rate` ≥ 0.95, `dig_deeper_navigation_accuracy` ≥ 0.95, `false_positive_rate` ≤ 0, `support_chain_sufficiency` 1.0, `tradeoff_sufficiency` 1.0, plus the can-fail probe (a degraded assembly trips `VERDICT_REGRESSION` with `failure_origin: gate_regression`).

Residual: the committed `tests/eval/why_suite/baselines/{main,m2}.json` are still the **empty template** (`metrics: {}`, `judge_pin: null`), so the *standing* baseline comparison never activates — only the in-run floor assertions gate. Publication is owned by **#2522** (open). The A4 ranking A/B arm is recorded honestly as not-measured.

### E2E-8 — published LongMemEval 500-Q sealed run (W7)

`tests/test_w7_publish.py` — **9 passed** (3.77 s): the publication machinery (official `recall_all@5` vs any-hit separation, sealed keys, receipt validation) is correct and pinned.

**The run itself has never been executed.** `docs/benchmarks/longmemeval-500q/run-report.md` states `RUN STATUS (this commit): PENDING — full-run NOT executed`; `gate-evidence.json` records `evidence_scope: gates 1-4 ... steps 5+ pending execution`. #2105 is open and its last comment lists the remaining prerequisites (embedder extra, a stable reader window, cost). The epic's indicator (6) and E2E-8 are therefore **not met** — the machinery shipped, the published number did not.

### E2E-9 — `/v1/context` delivery contract

`tests/test_volunteer_contract.py tests/test_volunteer_pipeline.py tests/test_hosted_volunteer_context.py tests/test_selfhost_volunteer_context.py` — **61 passed** (154 s). Covers the §3.2 contract, auth fail-closed (two teams / two keys, 0 cross-team context), 422 SDK-first bounds, clean-empty ≠ degradation, re-mention suppression, superseded-never-current, fail-open `degraded_reason`, statelessness, and the W4-content delivery arm (contested + superseded through the endpoint).

Two documented deferrals keep this at PARTIAL, both carried in #2103's close note:

- The **self-host container-boot leg** — plan E2E-9 requires booting the real container and hitting `POST /v1/context` with a client ("never a shared-impl shortcut"). What ships is the selfhost ASGI app + router via `TestClient`; `tests/test_selfhost_volunteer_context.py`'s own docstring says *"The full Docker-container boot leg ... is the E2E-9 container-boundary assertion; this round ships the mechanism ... and documents the container leg in the PR body."*
- The **≤ 300 ms p95 latency SLO** is assigned to a dedicated perf lane that does not exist (#3992).

## Published artifacts (indicator checks)

| Artifact | Expected | Measured |
|---|---|---|
| W1 learnings map + licensing gate | `docs/research/2026-08-31-gbrain-learnings/`, `docs/research/2026-09-01-gbrain-learnings/` | present (learnings-map, research-brief, raw-notes, delivery-shape, ux-research, platform-landscape, platform-seams) ✅ |
| W2 baseline + can-fail CI gate | committed baseline w/ justification + failure classes | `baselines/main.json` (6 justified history entries) ✅; survival target not reached ⚠️ |
| W3 harness published numbers | committed baselines | null-reflex + why-suite template ⚠️ |
| W7 run receipt naming commit/corpus/judge | validated receipt | machinery only — run PENDING ❌ |
| `comparison-systems.md` | mechanism-named rows, no un-run win claims | `docs/benchmarks/comparison-systems.md` + `w7a-comparison-rows-for-2106.md`; LongMemEval rows honestly PENDING ✅ |

## W4 user-exposure gate (indicator 4)

The flag is `TORTOISE_W4_ENRICHMENT`, resolved by `tortoise/why.py:94` (`w4_enrichment_enabled`) and consumed by `tortoise/ranking.py:44`. Its default is **OFF**, and that is the correct posture today:

- **Condition (a)** — W2 first baseline published AND first fix-wave failure classes named — **met**: `baselines/main.json` is published and the baseline names its own misses (`content_missing`, `ep_update_missing`).
- **Condition (b)** — the committed survival target reached on the frozen corpus after ≤ 2 fix-waves — **not met**: survival is 0.5444 (llm lane), the remaining gap is the extractor's `content_missing` recall (#2453), and the fix-wave re-run cannot be executed unilaterally (`main.json`: *"The llm product lane was NOT re-run on the new operator gold (provider spend is an open owner decision on #2552)"*).

So production exposure remains (correctly) gated, and the epic's branch *"if the pipeline can't be fixed, cut the why-layer to an internal research finding and re-scope"* is **live but undecided** — it needs the #2552 ruling and a fix-wave outcome. The capstone cannot resolve it; it records that the gate is open and the flag is off, which is the state #2107's indicator 4 asks to be checked.

## Residuals and blockers

| Item | Type | Status | What it blocks |
|---|---|---|---|
| #2104 W5 write-path quality | open child | OPEN (`P0-blocker`) — blocked on #2552 (provider spend) + #2453 (extractor recall) | E2E-5/W4 gate condition (b); the fix-wave loop |
| #2105 W7 sealed 500-Q run | open child | OPEN — run never executed | E2E-8; epic indicator (6) |
| #2522 why-layer baseline publication | open | baseline is the empty template | E2E-7 standing baseline comparison; `comparison-systems.md` why-layer row |
| #2093 test-design surface map | open child | artifact delivered in the issue body; amendments recorded in the plan; bookkeeping only | nothing |
| #3992 dedicated perf lane does not exist | open | referenced by the plan but unimplemented | E2E-1 / E2E-9 wall-clock SLOs unverifiable |
| #7704 W4-b contested boost vs RRF fusion | open | the E2E-1 ranking-participation **ordering** holds only on a pinned single-leg read; on the fused (shipped) read the id tie-break decides, and the outcome is a function of `limit`. Re-measured 2026-10-15; not a lane-actionable fix (every remedy is an owner decision) | E2E-1 ranking participation — the "contested outranks its twin" claim is path-limited, not corpus-wide |
| Graded-reflex harness re-bless (reflex null→graded) + EP warm-start on graded cells | filed: **#8125** | no owner after #2102/#2103 closed — E2E-3 target unmet | E2E-3; epic indicator (3) |
| `falkordb/falkordb:latest` panics the server under a shipped test | filed: **#8124** | `reply.rs:655` `reply_compact` unwrap; container exits | any docker-lane run on the floating tag; the shared container's restart class (#5084) |

**Dependency-graph note:** #2107's `Depends on:` field names #2106, #2103, #2100 and #2083 (all closed), but its own verification checklist walks E2E-5 (owned by the still-open #2104) and E2E-8 (owned by the still-open #2105). Those two children were never in the capstone's dependency list, so the capstone was dispatched as unblocked while two of its nine E2Es had open owners.

## Lane conditions (measurement caveats)

- E2E-1 to E2E-7 suites ran URI-less on the embedded carve-out lane (`TORTOISE_TEST_CARVE_OUT=1` — the lane `python-ci.yml`'s carve-out job runs). The epic's plan prefers the docker lane for graph logic; the carve-out is a CI-run lane, not a skip, but it is a **different backend** from the one CI's `durations` weights were measured on, which is the most likely explanation for the E2E-2 gap note above.
- E2E-5's docker-lane-only file ran on a throwaway private FalkorDB (`tools/test_lane.py`, #5084) with `TORTOISE_TEST_LANE_IMAGE=falkordb/falkordb-server:v4.20.4`.
- The fleet was concurrently running other lanes throughout (load average 12-18 on 8 cores), so every wall time in this report is an upper bound.

## Verdict

**NOT PASS — epic #2080 is not complete.** Five of the nine E2Es are green at their shipped assertions (E2E-1, E2E-4, E2E-5, E2E-6, E2E-7); E2E-2 and E2E-9 are green at the assertions that ran, with the bounded gaps named above. Two are not:

- **E2E-3 (reflex know-to-ask / false-fire)** — the graded reflex measures 4/4 missed know-to-ask turns with 0 false fires, and the standing bars that would gate it have never been activated (harness baselines remain `reflex: "null"`). Target `know-to-ask failure 0.00` is unmet.
- **E2E-8 (sealed LongMemEval 500-Q run)** — the machinery is correct and tested; the run has never been executed, so no published number exists.

Plus the W4 user-exposure gate's condition (b) is unmet and its branch decision is live. The epic must not be marked complete; re-run this capstone after #2104 (post-#2552) and #2105 close, and after the two filed residuals are owned.
