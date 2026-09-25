---
title: "Embedding encode-work measurement (#4488) — Implementation Plan"
type: engineering
domain: capability
doc_status: draft
created: 2026-09-25
subjects.team: organisation-design-team
aboutSubjects: Tortoise
aboutObjects: "#4488, #5045"
---

<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/4488 -->

# Embedding encode-work measurement (#4488) Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Record a per-org embedding-encode workload figure — texts, characters, wall time, model identity — on the existing metering ledger, readable from the existing `/v1/team` usage response.

**Team:** epistemic-team (from the issue's `lane:c7-instrumentation` / `domain:data`)
**Role:** implementation lane `obj7-4488-embed`

**Architecture:** A leaf module `tortoise/embed_metering.py` owns a mutable `EmbedTally` in a `ContextVar`. `embeddings.compute_embeddings` (the store-vector encode funnel) mutates it in place (O(1), no I/O). Work-owning boundaries take-and-reset it into the existing ledger via `metering.record_embedding_usage`: a pure-ASGI `EmbedMeteringMiddleware` (arms non-GET requests; resolves the org from `scope["state"]["org_id"]`, which every org-resolving auth dependency — key auth AND session auth — populates during the request; flushes in `finally`), MCP `_quota_gated` (org from `_current_org_id`), and — for work born in a detached task — a `meted(org_id)` CM that installs a FRESH tally and flushes on exit (background index jobs, the dream worker, the onboarding/starter seed runners). **The hosted capture body is NOT a `meted` site** — a capture is a REQUEST, so the middleware owns it (the pool thread's copied context shares the tally object). There is deliberately **no** flush from `_record_write_op` (see the deviation note below). Supabase gets additive `embed_*` columns + one atomic RPC; the registry lane gets the matching Cypher MERGE. `/v1/team` renders the reader.

**Scope guard:** no cap, price, quota, entitlement, or recorded decision changes. `get_cohort_spend_usd` is untouched (the new columns are excluded from the spend ceiling). The pre-existing `show_progress_bar` signature bug found during scoping is filed as **#5321** and is NOT fixed here.

**Measured vs excluded (state this, so generic field names are not over-read):**
- **Counted:** `EmbeddingModel.encode` reached through `compute_embeddings` — the store-vector funnel used by `sdk.create_point`, the capture turn batch, `api.py`, and `projection/entities.py`.
- **Excluded, with reason:** `_encode` / `search_points` / `kind_index._DefaultEncoder.encode` pass `show_progress_bar=False`, which `EmbeddingModel.encode` does not accept → `TypeError` → TF-IDF (**#5321**), so they run **no model work** today; `sdk.py:14358` and `fallback_snapshot` are read/query-side. If #5321 is fixed, extending the measurement to those paths is a follow-up (and the same hook design applies).

### Pattern Research

In-repo precedent only; no new third-party dependency (stdlib `contextvars`/`time.perf_counter`).
- **Writer/reader shape:** `metering.record_ask_usage` / `get_ask_usage` (+ `metering_increment_ask` / `metering_get_usage`). Canonical.
- **Ledger + atomic increment + migration:** `supabase/migrations/20260829000001_metering_ask_columns.sql`, **`20260917000001_metering_capture_cost.sql` (the NOT NULL DEFAULT 0 precedent)**, `20260918000001_metering_period_window.sql` (window keying + `SECURITY DEFINER`/ACLs).
- **Mutable tally across off-loop/worker context copies:** `hosted_api._submit_off_loop` (`contextvars.copy_context()`, cpython#78195). Sibling pattern reference (not a dependency): the unmerged `tortoise/graph_ops.py` (PR #5292).
- **Person-facing read:** `/v1/team` → `OrgInfoResponse` additive fields (#1987 `ask_*`, #4331 `nodes_*`).
- **Pitfall (axis research, MEDIUM):** cold load and batching must be separated from steady-state encode work; a per-point figure is an allocation. → time only `model.encode(...)`; ship no division.

### Integration Surface Map

| Surface | Type | Test layer | Notes |
|---|---|---|---|
| `embeddings.compute_embeddings` hook | pure function | unit | return values + frozen signature unchanged |
| `embed_metering` tally/ContextVar/middleware | pure in-process + ASGI | unit + integration | lock-guarded; total; take-and-reset; empty-flush no-op |
| hosted `_record_write_op` / `meted()` | integration (embedded) | integration | two-flush no-op; empty no-write; detached ownership |
| `metering.record_embedding_usage` / `get_embedding_usage` (registry) | integration (embedded FalkorDB) | integration | real Cypher MERGE + read back |
| Supabase migration + RPC | DB migration | static (mirroring `test_metering_period_window.py::test_migration_*`) | no live Supabase in CI |
| `/v1/team` `embed_*` render | REST response | integration (embedded + FakeControlPlane) | zeros for a fresh org (P2-14) |
| the swallow-site fence | static | `test_metering_window_admission.py` | **the new `embed` lane must be added to `SITE_LANES`** |

### Journey Test Map

### Journey: an operator reads an org's embedding workload
1. **Step:** an org writes Points (store encode) → **Acceptance:** the ledger row carries texts/chars/wall_ms/model/revision → **Test:** `test_compute_embeddings_notes_the_encode` (hook) + `test_record_then_read_back` (row)
2. **Step:** `GET /v1/team` → **Acceptance:** the `embed_*` fields render the figure (zeros for a fresh org) → **Test:** `test_renders_zeros_then_the_recorded_figure`

### Failure Modes
- Metering window unresolvable → **Expected:** dropped + alerted via `metering.report_unmetered_increment(lane="embed", …)`; the write is served → **Test:** `test_unresolvable_window_alerts_and_does_not_raise`
- Non-empty tally with no resolvable org → **Expected:** same alert, never a silent drop → **Test:** `test_unbound_nonempty_flush_alerts`
- Nothing encoded on a non-GET request → **Expected:** no RPC, no zero row → **Test:** `test_empty_flush_records_nothing_and_calls_no_rpc`
- Model unavailable → **Expected:** no texts/chars/time; `embed_skipped` increments; the stored identity is NOT erased and the mixed flag is NOT flipped → **Test:** `test_skipped_only_flush_preserves_identity_and_flag`
- TF-IDF fallback → **Expected:** not counted (the hook is inside the model branch) → **Test:** `test_tfidf_fallback_is_not_counted_as_an_encode`
- Two flush boundaries in one request → **Expected:** exactly one increment → **Test:** `test_second_flush_is_a_no_op`
- Off-loop worker encode → **Expected:** lands in the tally the owning boundary flushes, and the blocking write never runs under the loop → **Test:** `test_async_meted_flushes_off_loop`, `test_async_flush_is_offloaded_off_the_event_loop` (thread identity), `test_sync_arm_offloads_when_called_from_the_loop`
- Mid-period model OR revision change → **Expected:** `embed_identity_mixed` latches TRUE and STAYS TRUE → **Test:** `test_identity_mixed_is_sticky_across_windows`
- **Cancellation residual (declared, not claimed):** a capture request cancelled while its pool worker still runs flushes at the request boundary; the worker's later `note_encode` lands in a retired tally and is **not** attributed. Same class as the #4451 on-loop residual; bounded, documented in the PR body → **Test:** `test_cancelled_capture_residual_is_a_declared_loss` (asserts the awaited path records once; the cancelled path is a declared loss, not a silent claim).

**Tech Stack:** Python 3.12, FalkorDB (embedded registry lane), Supabase/Postgres, Starlette/FastAPI middleware, pytest.

---

### Task 1: The tally module

**Intent:** Own the per-request primitive with zero I/O on the encode path; exactly one boundary takes it.
**Acceptance:**
- `EmbedTally` accumulates `calls/texts/chars/wall_ms/skipped` + the observed `(model, revision)` identity.
- `note_encode(...)`, `bind_org(...)`, `flush(...)` are **total** (never raise); `bind_org` is a no-op when unarmed.
- One `threading.Lock` guards the tally's consumed-transition **and** its counter snapshot (one critical section, so a concurrent boundary can neither double-write nor lose an increment that landed mid-flush); `record_embedding_usage` is called **after** the lock is released (a blocking ledger RPC must not serialize concurrent encodes).
- `arm()` is idempotent. `meted(org_id)` installs a **FRESH** tally (never inherits) and **flushes on exit** (`__exit__` for sync callers — MCP `_quota_gated`, the seed runners; `__aexit__` offloading the blocking flush for async callers — `_run_indexing`, `_run_docs_indexing`, `_dream_worker`; the SYNC arm also offloads when it is entered from a running loop, so a sync runner called by an `async def` handler never blocks it). The critical section covers the consumed transition **and** the counter snapshot together.
- `flush(org_id)` takes-and-resets, **returns immediately on an empty tally**; on a dropped window it calls `metering.report_unmetered_increment(lane="embed", org_id=org_id, error=e)` — **the KEYWORD form, because the fence census is `re.findall(r'report_unmetered_increment\(\s*lane="([a-z_]+)"', src)` (`test_metering_window_admission.py:581`)**; a positional lane emits no pair and reds the fence. Importing the host `_alert_unmetered` helper would cycle; a non-empty tally with no org alerts the same lane.
- `EmbedMeteringMiddleware` (pure ASGI): non-GET → `arm()`; `finally` → resolve org as `tally.org_id or scope.get("state", {}).get("org_id")` and `flush(org)`, then restore `_ACTIVE`.

**Files:**
- Create: `tortoise/embed_metering.py`
- Test: `tests/test_embed_metering.py`

**Step 1: Write the failing tests** — accumulation; idempotent `arm`; `meted()` fresh-tally/restore/flush-on-exit (sync + async); `bind_org` total when unarmed; empty take; empty flush issues no RPC; unbound non-empty flush alerts; concurrent note/flush yields exactly one tally's worth.
**Step 2: Run — expect failure.** `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embed_metering.py -v`
**Step 3: Implement.**
**Step 4: Re-run — expect pass.**

### Task 2: The encode hook

**Intent:** Measure only real model encode work on the store funnel.
**Acceptance:** `compute_embeddings` notes `(texts, chars, wall_ms)` around `model.encode(...)` using the **truncated** strings actually passed; `model is None` notes `skipped` and still returns `[None]*n`; return values and the frozen `(texts, max_tokens)` signature unchanged; `_encode`'s TF-IDF fallback untouched/uncounted; the `embed_metering` import is lazy (no cycle).

**Files:**
- Modify: `tortoise/embeddings.py` (`compute_embeddings`) · Test: `tests/test_embed_metering.py`

**Step 1: Write failing tests** (armed+stub → tally; unarmed → no effect; model None → `skipped`; return value unchanged). **Step 2: Run.** **Step 3: Implement the `perf_counter` hook.** **Step 4: Re-run.**

### Task 3: The metering writer/reader (registry lane)

**Intent:** Persist on the existing per-(org, period) ledger and read back.
**Acceptance:** `record_embedding_usage(org_id, *, calls, texts, chars, wall_ms, skipped, model, revision, _selfhost_transport: bool = False)` mirrors `record_ask_usage` (exemption `not org_id or _selfhost_transport or _selfhost_transport_active()`, `_require_period` raises, per-org `_ask_meter_lock`, registry Cypher MERGE, increment-RPC failure logged non-fatally). The MERGE's mixed rule is **sticky, paired, and skip-safe**: assign the flag BEFORE the identity, `embed_identity_mixed = coalesce(m.embed_identity_mixed,false) OR $mixed OR (m.embed_model IS NOT NULL AND $model IS NOT NULL AND (coalesce(m.embed_model,'') <> coalesce($model,'') OR coalesce(m.embed_revision,'') <> coalesce($revision,''))` — the `$mixed` term carries an identity change observed WITHIN one tally (two models encoded in the same window), which no comparison against the stored row can see; then `m.embed_model = coalesce($model, m.embed_model), m.embed_revision = coalesce($revision, m.embed_revision)`. `get_embedding_usage(org_id)` mirrors `get_ask_usage` (`_current_period` + zero view, never raises). `get_cohort_spend_usd` untouched.

**Files:**
- Modify: `tortoise/metering.py` · Test: `tests/test_embed_metering.py`

**Step 1: Write failing tests** — record→read back; zeros for no row; identity == `embedding_identity()`; model-only change flips; **revision-only** change flips; **A→B→B stays TRUE**; **real-A → skipped-only → real-A leaves the flag FALSE and identity A**; exemption records nothing; writer raises on an unresolvable window, reader degrades to zeros.
**Step 2: Run.** **Step 3: Implement.** **Step 4: Re-run.**

### Task 4: Supabase lane — columns, RPC, reader, fence

**Intent:** Make the dimension durable on the production lane and keep the metering fences green.
**Acceptance:**
- New file `supabase/migrations/20260925000003_metering_embedding_columns.sql` (prefix verified free) adds — via `ADD COLUMN IF NOT EXISTS` — `embed_calls int NOT NULL DEFAULT 0`, `embed_texts bigint NOT NULL DEFAULT 0`, `embed_chars bigint NOT NULL DEFAULT 0`, `embed_wall_ms double precision NOT NULL DEFAULT 0`, `embed_skipped int NOT NULL DEFAULT 0`, `embed_model text`, `embed_revision text`, `embed_identity_mixed boolean NOT NULL DEFAULT false`. **Every counter is NOT NULL DEFAULT 0** (the 20260917000001 precedent) so a row first created by `metering_increment`/`metering_increment_ask` cannot make `embed_x + n` evaluate to NULL forever.
- `metering_increment_embedding` is `DROP FUNCTION IF EXISTS <the same signature>` + `CREATE` with `SECURITY DEFINER`, `SET search_path = ''`, parameters `p_org_id text, p_period_start timestamptz, p_period_end timestamptz, p_calls int, p_texts bigint, p_chars bigint, p_wall_ms double precision, p_skipped int, p_model text, p_revision text`; then `REVOKE ALL ON FUNCTION … FROM public, anon, authenticated` + `GRANT EXECUTE … TO service_role`.
- **The INSERT column list is explicit** (the 20260918000001 precedent, `:178-180`), because `period text NOT NULL` (`supabase/migrations/0014_metering_records.sql:15`, no default) and `period_start`/`period_end` are NOT NULL with no default (`20260918000001:133-134`):
  `INSERT INTO public.metering_records (org_id, period, period_start, period_end, embed_calls, embed_texts, embed_chars, embed_wall_ms, embed_skipped, embed_model, embed_revision, embed_identity_mixed) VALUES (p_org_id, to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'), p_period_start, p_period_end, p_calls, p_texts, p_chars, p_wall_ms, p_skipped, p_model, p_revision, false) ON CONFLICT (org_id, period_start) DO UPDATE SET …` with additive counters written as `coalesce(metering_records.embed_x, 0) + p_x` (belt-and-braces with the NOT NULL), and the same sticky/paired/skip-safe mixed rule and `coalesce`-guarded identity write. Include the degenerate-window guard (`p_period_end <= p_period_start` → raise) mirroring the precedent.
- **Deploy order:** migration FIRST, code second — a missing RPC logs non-fatal and the reader degrades to zeros (a silent zero window, not an error). State it in the migration header and the PR body (the 20260918000001 precedent carries this block).
- `supabase_control.metering_increment_embedding` calls the RPC; `metering_get_embed_usage` is a PostgREST **table read** (mirroring `metering_get_usage` — no second SQL function).
- **`tests/fake_control_plane.py` gains a `metering_increment_embedding` branch** mirroring `metering_increment_ask` (`:230`) — otherwise the unhandled `fn` falls through to `return None` (`:599-600`) and the supabase-lane read-back silently reads zeros; the branch writes the `embed_*` columns into `tables["metering_records"]` with the same sticky/paired/skip-safe mixed rule, and the tests also assert via `fake.rpc_calls` (the `tests/test_metering.py:946` precedent).
- **`tests/test_metering_window_admission.py::SITE_LANES` gains `"embed": "tortoise/embed_metering.py"`**, its "six swallow sites" wording and `metering.report_unmetered_increment`'s lane-list docstring are updated to seven, or the fence reds.

**Files:**
- Create: `supabase/migrations/20260925000003_metering_embedding_columns.sql`
- Modify: `tortoise/supabase_control.py`, `tests/test_metering_window_admission.py`, `tortoise/metering.py` (docstring)
- Test: `tests/test_embed_metering.py`, `tests/fake_control_plane.py`

**Step 1: Write failing tests** — static SQL: every column present AND its `NOT NULL DEFAULT 0`; the RPC is window-keyed (`p_period_start timestamptz`, `p_period_end timestamptz`) and upserts `ON CONFLICT (org_id, period_start)`; **the INSERT lists `org_id, period, period_start, period_end` (the NOT NULL no-default trap)**; `SECURITY DEFINER`/`SET search_path = ''`/REVOKE+GRANT present; the Python seam passes the exact parameter names; the fake route exists (assert via `fake.rpc_calls` AND a read-back through `metering_get_embed_usage`); the fence lists the `embed` lane (its own test in `test_metering_window_admission.py`).
**Step 2: Run.** **Step 3: Implement.** **Step 4: Re-run.**

### Task 5: Wiring — middleware, flush boundaries, detached work, read path

**Intent:** Arm on every per-org write request, attribute the org, flush once, own detached work, expose the figure.
**Acceptance:**
- `EmbedMeteringMiddleware` registered **before `app.add_middleware(InFlightMiddleware)` (`hosted_api.py:2222`)** — NOT before `WaitBoundMiddleware` (`:2681`). Starlette inserts at index 0, so this yields `[WaitBound, InFlight, EmbedMetering, …]`: still inner to the WaitBound-owned task, while the pinned order `classes[0] is WaitBoundMiddleware, classes[1] is InFlightMiddleware` (`tests/test_hosted_api.py:563-578`) holds. Registering it between the two would push `InFlightMiddleware` to index 2 and red that pin.
- Org sources: the middleware reads `scope["state"]["org_id"]`, which the org-resolving dependencies set — the **key-auth** lanes (`hosted_api.py:3809`, `:3927`) and the **session lane** (`_session_user_org`, which stamps `request.state.org_id` before returning). The internal lane (`_check_internal`, `:2748`) sets NOTHING, so `/internal/starter-seed` (`:22077`, org from the request body) is **NOT** covered by scope state and must be bound explicitly — done by decorating the RUNNERS (`_run_onboarding_seed`, `_run_starter_seed`) with `_embed_metered`, which also covers the MCP caller that reaches them directly. Without that, every tenant provisioning fires a spurious UNMETERED-INCREMENT incident on a resolvable org. MCP `_quota_gated` resolves `_current_org_id` (and arms only when it is truthy: the stdio transport has no org).
- **NO flush from `_record_write_op` (deviation from the plan's first draft, deliberate).** That site is SYNCHRONOUS and runs ON the event loop (the documented #4451 residual), and `metering.record_write_ops` is already the one blocking ledger write there. A second one per write op lengthened responses enough to trip the transport wait bound in `tests/test_hosted_api.py` (4 POST /v1/points timeouts under load; verified load-sensitive, and two of the four reproduce on a CLEAN base tree). The middleware flush is offloaded and the runner flushes are offloaded, so the measurement never sits between a write and its response, and coverage is unchanged: HTTP → middleware, MCP → `_quota_gated`, detached runners → `_embed_metered`. `test_record_write_op_does_not_add_a_second_ledger_write` pins the absence.
- Detached/unattributed work is wrapped in `meted(org_id)` where it is born, via the `_embed_metered` decorator on the runners: `_run_indexing`, `_run_docs_indexing`, `_dream_worker(org_id, key)`, `_run_onboarding_seed`, `_run_starter_seed(org_id, …)`. A decorator (not a per-call-site `with`) because these runners are reached from several boundaries and only the runner knows the org. The hosted capture body is covered by the request-boundary middleware for the **awaited** path (the pool thread's copied context shares the tally object); the **cancellation** residual above is declared, not claimed.
- `docs/ops/registry-backup-dr.md:514` — the `UNMETERED_INCREMENT` detail-`lane` vocabulary gains `embed` (no test reds, but an undocumented lane is untriageable).
- `/v1/team` gains **additive, defaulted** optional `embed_calls/embed_texts/embed_chars/embed_wall_ms/embed_skipped/embed_model/embed_revision/embed_identity_mixed` rendered inline from `get_embedding_usage` (mirroring `hosted_api.py:6540-6583`).

**Files:**
- Modify: `tortoise/hosted_api.py` · Test: `tests/test_embed_metering.py`

**Step 1: Write failing tests** — middleware records once on a real ASGI POST and does not arm GET; the middleware order pin (`tests/test_hosted_api.py`) still holds; second flush is a no-op; empty flush writes nothing; `/v1/team` zeros then values; `/v1/points`, `/v1/objects`, `/v1/subjects`, `/v1/sessions/commit`, `/v1/onboarding/seed`, `/internal/starter-seed`, the capture lane, the background index job, and the dream lane each record once — and **never alert** (assert no `UNMETERED_INCREMENT` for the session/internal lanes, i.e. the `meted` bindings work); off-loop encode recorded.
**Step 2: Run.** **Step 3: Implement (minimal, additive; call out the shared-file edits in the PR body).** **Step 4: Re-run.**

### Task 6: MCP wiring

**Intent:** Attribute MCP write-tool encodes without double-counting the hosted capture lane.
**Acceptance:** `_quota_gated` uses `with meted(org_id):` (sync arm) around `fn` when `_current_org_id` is set — a fresh tally + flush on exit, so an exception cannot leave a stale tally and it cannot double-count the middleware.

**Files:**
- Modify: `tortoise/mcp_server.py` · Test: `tests/test_embed_metering.py`

**Step 1: Write a failing test** (a `_quota_gated`-wrapped stub that notes an encode records once; an exception leaves no stale tally). **Step 2: Run.** **Step 3: Implement.** **Step 4: Re-run.**

### Task 7: Test-file registration

**Intent:** The new test file runs on every PR that can break it.
**Acceptance:** `tests/test_embed_metering.py` registered under `api`, `core`, **and `eval`** (`tortoise/embeddings.py` selects `eval`), with a comment naming each guarded file; added to `DELIBERATE_URI_MUTATIONS` in `tests/test_uri_env_mutations_declared.py` (the `test_metering_period_window.py` fixture-param precedent, `DELIBERATE_EMBEDDED_LANE` comment).

**Files:**
- Modify: `config/ci-surfaces.yml`, `tests/test_uri_env_mutations_declared.py`
- Test: `tests/test_ci_selection.py`, `tests/test_uri_env_mutations_declared.py`

**Step 1: Add the registrations. Step 2: Run both guards — expect pass.**

### Task 8: Full verification

**Intent:** Prove the change on the real suite.
**Step 1:**
```
TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_embed_metering.py tests/test_metering.py tests/test_metering_period_window.py tests/test_metering_window_admission.py tests/test_migration_append_only.py tests/test_migration_drift_gate.py tests/test_uri_env_mutations_declared.py tests/test_graph_write_loop_responsiveness.py tests/test_hosted_api.py tests/test_ci_selection.py -v
bash .github/scripts/check-migration-append-only prefix
```
**Step 2:** Record the verbatim output as the PR's test evidence.
