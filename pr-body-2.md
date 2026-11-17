# #12943 (reopened) — the write leg, graded per door, and where each live write lands

Follow-up to **#13332** (merged `376cbd613d`). That PR landed the diagnosis and the
instrument; this one closes the four review findings the merge did not carry, and re-measures
every door at the new head **with the record's own reconciler in the table**.

Closes nothing on its own — #12943 stays open until the split rows below stop being reportable
(see *Residual*), and the reopened defect class it names is now **measured**, not assumed.

---

## 1. What the reopened issue actually is

The reopening said: *the in-process writer reaches the record and never the cache — 8 objects /
39 events / 5 operators in Postgres, 0 in FalkorDB.*

**That is not a missing cache leg on `_data_sdk`.** Measured below: `_data_sdk` writes both
stores whenever both are configured and its credential is graph-bound. The defect one level up
is:

> **The record's TENANT and the cache's GRAPH are two independent answers, and nothing compares
> them.** The cache graph derives from the **credential** (the graph namespace the SDK was opened
> with, or an org-wide open), while the record files the same write under the **tenant's**
> `graph_id`. When those differ, the write lands — in a graph the reconciler will never look at.

That is why the counters read healthy while the graph reads empty: nothing failed, so nothing was
counted.

## 2. Per-door measurement (all seven doors, current head)

Harness: `docs/evidence/12943-cache-leg/door_matrix.py` — one **run-unique throwaway tenant per
door**, a real Postgres record (`postgresql://127.0.0.1:33943/record#0367bb5d`, APPLICATION role, owner DSN
only for seeding) and a real shared FalkorDB (`docker://127.0.0.1:16403/tortoise_test_matrix#87627ce7`).
Every cell below is derived from the capture by script, not transcribed.
The cache side is counted in **both** plausible graphs — the tenant's canonical
`org_<org>_<gid>` **and** the org-level `org_<org>` — so "reached" is never inferred from one name.
Capture: `docs/evidence/12943-cache-leg/door-matrix-shared-cache.json`.

`write TARGETS` is `monitoring.metrics()["cache_write_targets"]` delta across the write, `counter`
is the `cache_leg_failures` delta, and `reached actual` is `reached_actual` from the capture — the
registry's own answer to "did the cache receive this write", which the two name-derived counts
cannot give (see below).

| door | tenant (org / gid) | record obj/journal | cache `org_<org>_<gid>` | cache `org_<org>` | write TARGETS | counter | reconcile (tenant's graph) | reconcile (graph the write used) | reached actual |
|---|---|---|---|---|---|---|---|---|---|
| `sdk` | `a12943eaf4807daa` / `g_12943aeaf4807daa` | 1/1 | 1 | 0 | `org_a12943eaf4807daa_g_12943aeaf4807daa` ×1 | `{}` | **clean** | **clean** | `True` |
| `data_sdk` | `b12943eaf4807daa` / `g_12943beaf4807daa` | 1/1 | 1 | 0 | `org_b12943eaf4807daa_g_12943beaf4807daa` ×1 | `{}` | **clean** | **clean** | `True` |
| `data_sdk_orgwide` | `c12943eaf4807daa` / `g_12943ceaf4807daa` | 1/1 | 0 | 1 | `org_c12943eaf4807daa` ×1 | `{}` | **divergent** (miss 1, exit 3) | **clean** | `True` |
| `rest` | `d12943eaf4807daa` / `g_12943deaf4807daa` | 1/1 | 1 | 0 | `org_d12943eaf4807daa_g_12943deaf4807daa` ×1 | `{}` | **clean** | **clean** | `True` |
| `mcp` | `e12943eaf4807daa` / `g_12943eeaf4807daa` | 1/1 | 1 | 0 | `org_e12943eaf4807daa_g_12943eeaf4807daa` ×1 | `{}` | **clean** | **clean** | `True` |
| `script_orglevel` | `f12943eaf4807daa` / `g_12943feaf4807daa` | 1/1 | 0 | 1 | `org_f12943eaf4807daa` ×1 | `{}` | **divergent** (miss 1, exit 3) | **clean** | `True` |
| `misroute` | `h12943eaf4807daa` / `g_12943heaf4807daa` | 1/1 | 0 | 0 | `org_h12943eaf4807daa_g_12943otherb7a495a1` ×1 | `{"tenant_graph_mismatch": 1}` | **divergent** (miss 1, exit 3) | **clean** | `True` |

Read the last two columns together: **every row reconciles CLEAN against the graph the write
actually used**, and only the coherent rows reconcile clean against the graph the tenant's name
predicts. The write leg works in all seven rows; what fails is the *agreement* between the two
names, and that is now visible three ways (the counter, the target registry, and reconcile).

The capture also carries `reached_actual` per row — **`true` for all seven**, derived from the
target registry rather than from a graph the harness guessed. The `misroute` row is why it exists:
`reached_canonical=false` **and** `reached_org_default=false` over a write that *did* land (in a
third graph), so a summary built from the two name-derived counts alone reads "the cache received
nothing" — review cycle 3, P3.

### `record-reconcile` — clean, quoted verbatim

The `sdk` door's write, reconciled by the record's own CLI with `--cache-graph` naming the
tenant's canonical graph (the `record-reconcile` arm the door matrix runs; values verbatim from
`door-matrix-shared-cache.json`, keys re-sorted alphabetically for reading — the capture emits
`exit` after `verdict`, so the ORDER here is not the capture's):

```json
{
  "cache_side": "cache",
  "checked": 1,
  "declaration_coverage": "complete",
  "declaration_source": null,
  "divergent": 0,
  "divergent_keys": [],
  "exit": 0,
  "extra_in_cache": 0,
  "field_mismatches": 0,
  "form_normalized": 0,
  "graph_only": {},
  "graph_only_total": 0,
  "missing_in_cache": 0,
  "not_hot": 0,
  "truth_side": "record",
  "verdict": "clean"
}
```

Both blocks above come from the capture (`reconcile.canonical` for the `sdk` row), minus the
`stderr_tail` line — an unindexed-label warning, quoted separately below because it is the only key
omitted:

```
    ... is unindexed for label(s) ['Event', 'Object', 'Source', 'Subject'] (indexed: ['Point']) — those reads silently full-scan.
```

And the split, as the record sees it, quoted from the capture's **`misroute`** row
(`reconcile.canonical` — the tenant's own graph, which the write never reached):

```json
{
  "cache_side": "cache",
  "checked": 1,
  "declaration_coverage": "complete",
  "declaration_source": null,
  "divergent": 1,
  "divergent_keys": [
    "Object:1a1ebb5103c-a516e2f1f276"
  ],
  "exit": 3,
  "extra_in_cache": 0,
  "field_mismatches": 0,
  "form_normalized": 0,
  "graph_only": {},
  "graph_only_total": 0,
  "missing_in_cache": 1,
  "not_hot": 0,
  "truth_side": "record",
  "verdict": "divergent"
}
```

…while the SAME row reconciled against the graph the write used is `clean`
(`graph_only={"embedding": 1}`, `divergent=0`): the write is real, it is simply filed where the
tenant's name does not point.

The divergent run is `exit 3` by design (a divergence is a real verdict, not an error), so it is
recorded as evidence rather than "fixed" — see *Residual*.

## 3. The issue's four hypotheses, each with this lane's measurement

| # | Hypothesis | Verdict | Measurement |
|---|---|---|---|
| H1 | The cache leg is not on the SDK path at all | **REJECTED** | `sdk`, `rest` and `mcp` rows: 1 record object + 1 journal event → **1 Object** in the tenant graph, reconcile **clean**. The leg exists on every door. |
| H2 | The write was attempted and fails silently | **REJECTED as stated, ACCEPTED in substance** | Nothing fails: `cache_leg_failures {}` on the coherent rows and **1 write landed** in the graph the write used (reconcile clean). What was silent was the **misroute** (previous row 7 shape): counted `0` before this PR, `{tenant_graph_mismatch: 1}` after. |
| H3 | It writes to a different namespace | **REJECTED for namespace, ACCEPTED one level up: a different STORE/GRAPH** | The record tenant is `org_<org>_<gid>` in every row; the cache graph is `org_<org>` in `data_sdk_orgwide` / `script_orglevel` and `…_g_other` in `misroute`. The namespace is right; the **graph name** is derived from the credential. |
| H4 | The projection is deferred/async so the cache is eventually written | **REJECTED** | Every door's cache count and reconcile verdict are read **after** the write call returns, with no polling: the fold is synchronous and precedes the record's commit point (`_apply_and_journal`). |

## 4. What this PR changes

Carried from #13332 (now on `main`): `cache_leg.tenant_graph_reason` / `note_tenant_graph` (the
pure decision + counter, fired from `_bind_record_tenant`), `cache_leg.note_cache_write_target`
(the cause-agnostic per-process target registry), `metrics()["cache_write_targets"]` and its
`/health` publication.

New here — the four review-cycle-2 findings, each with a test that fires it:

1. **The registry counted ATTEMPTS, not landed writes** (P2). The hook sat above the
   malformed-envelope guard and above every raising statement: `apply({"type": 123})` logged
   "skipping malformed event" and still left `{'embedded:…|tortoise': 1}`. `apply` is now a thin
   wrapper around `_apply_event`, and every fold arm that can decide it wrote NOTHING marks itself
   through `_skip()`: a malformed envelope, a non-dict point, an unwritable or absent id on the
   point/operator/promote arms, an empty or all-non-str `merge_ids`, a re-stamp that folded no
   Point, an `ObjectSuperseded` / retract / annotator / belief / edge-message fold that matched no
   row, an `EntityLinked` / `SessionRecorded` / `EntityMutated` fold whose statement matched
   nothing, and the three recognized-not-folded arms at the tail.

   ⛔ **The bound is stated in `_skip()`'s own docstring, not implied:** an arm whose fold returns
   NO count cannot be marked without inventing a predicate (`_upsert_subject`, `_upsert_object`,
   `_upsert_document` return `None`), and a statement counted against a target that matched 0 rows
   still REACHED that graph. So the error direction is over-report — never under-report, which is
   the direction that would recreate the illusion this registry exists to end. Tests:
   `test_a_SKIPPED_fold_is_not_recorded_as_a_landed_write` (fails against the pre-cycle-2 hook) and
   `test_the_OTHER_writing_nothing_exits_are_not_counted` (parametrized).
2. **`apply` is not the only live write lane** (P2, twice — cycle 2 and cycle 3). The first
   version counted only folds. Then `create_edge` / the about-lane mint (raw `MERGE` under
   `_structural_edge_write_scope`, never through `apply`) and the three declared live **gateways**
   that issue their own statements — `onboarding.state._run`, `webhook_delivery._run`,
   `why_snapshot._run` — reached the graph uncounted, each reporting `{}`, "byte-identical to *this
   process took no live cache write*". All four funnels now record through ONE total helper,
   `projection.note_live_write_target(proj)` (endpoint from the projection's own
   `_stable_endpoint`, memoised; replay excluded inside the helper; whole body guarded). Tests:
   `test_the_structural_edge_lane_records_its_target_too`, and
   `test_a_declared_gateway_lane_records_its_target` — **parametrized over all three gateways**,
   because the single-gateway version left two of the three unpinned (measured: deleting the hook
   from `webhook_delivery` and from onboarding left the suite green; review cycle 4, P2). The same
   test pins that a READ through a funnel is **not** a write.

   ⛔ **Residual, named rather than implied:** a raw statement at `proj.g` inside a live scope from
   anywhere *else* — the capture provenance stamps (`_CAPTURE_PROVENANCE_LANE`) are the standing
   example — is still not counted. That is accepted because such a writer does not *choose* its
   graph (it stamps nodes by id in the graph the SDK is already bound to), so it cannot hide this
   class; closing it belongs at the write handle, where §17.1's per-statement census already
   lives.
3. **The `_uri_graph_name` docstring named the wrong call sites** (P3): it credited
   `_graph_census`, which never had a copy. The two real sites are `_get_proj` and
   `check_connection`'s census read.
4. **`_bind_record_tenant` evaluated `_cache_graph_name()` OUTSIDE the guard** (P3). #5148
   protects the call, not its argument, and `urlparse("docker://[::1/db")` raises
   `ValueError: Invalid IPv6 URL` — which would have turned a tenant bind on the hosted
   per-request path into a 500 for a write that could be served. Test:
   `test_a_malformed_uri_does_not_turn_binding_a_tenant_into_a_failure`.

Two instruments, deliberately not one:

- `tenant_graph_reason` is **decidable in-process** and fires only when the cache graph name
  carries a graph id that is not the tenant's (`tenant_graph_mismatch`). It cannot accuse a name
  with no graph id.
- `note_cache_write_target` is **cause-agnostic**: `{store|graph: landed writes}` per process,
  bounded at 16 with an overflow bucket (a per-graph Prometheus label would be unbounded
  cardinality), store identity taken from the **projection's own** `_stable_endpoint`, never from
  the environment.

⚠️ **Deliberately ungraded, and pinned by a test:** names with no graph id — `org_<org>`,
`tortoise`, `registry_tortoise` — do not fire the counter
(`test_an_org_level_graph_name_is_SILENT_and_that_is_DELIBERATE`). A constructor cannot tell a
coherent org-wide request from a misroute without the control plane, so the registry makes it
*visible* instead of guessing. That is why the two org-level rows are `{}`-counter and divergent
in reconcile, rather than accused.

## 5. Interaction with #13093 (the READ side of the same seam)

`#13093` (`13093-entitydoor`) works the **read** side: a read that resolves a tenant must find the
graph the write landed in. This PR adds **no** read path, changes no resolution order, and does not
duplicate its change — it only makes the write side's disagreement *countable*, and its two new
signal names (`tenant_graph_mismatch`, `cache_write_targets`) are available to that lane as
evidence. Additive by construction: the only shared files are `cache_leg.py`, `sdk.py`'s
`_bind_record_tenant` (one guarded hook) and `projection/__init__.py` (the fold wrapper, which no
read touches).

## 6. Residual — reported, not "fixed" by moving data

The split itself is **not** repaired here, on purpose: making `_data_sdk` on an org-wide
credential open the tenant's own graph reverses the **#10180 byte-identical open** decision, so it
is filed as its own P1-severe defect with the measurement and an acceptance criterion —
**#13337, "one record tenant, two cache graphs — the cache graph is derived from the CREDENTIAL,
not from the tenant"**. This PR is what makes that issue's failure observable; that issue is where
the routing decision belongs.

## 7. Constraints honoured

- **No MCP/SDK surface change**: `tools/surface_manifest.py check` → OK (48 tools),
  `tools/surface-guard.py` → OK (94 methods). No public SDK method or MCP tool added or removed.
- **`live-v5` untouched** — every graph named above is a throwaway tenant created by the harness
  on this run; nothing reads or writes `g_311d79c684de4e8953d189ed`.
- §2.0 of `STORAGE-ARCHITECTURE.md` unchanged: journal → record → cache, the fold still runs
  inside the live write scope, and the new instrumentation is **after** the record's commit point
  and never able to fail a write (whole block wrapped; the argument is evaluated inside the guard
  too — finding 4).
- `#4010` capture caps and the 45-minute allowance: untouched.

## 7.5 Review cycles — what each one caught, including two regressions this PR introduced and fixed

The review gate is not a formality; on this PR it caught two defects in *my own* instrumentation, one
of which would have been silent data loss:

**Cycle 2 (4 findings, all applied).** The registry counted fold *attempts* (a malformed event wrote
nothing and was still counted); `apply` was treated as the only live write lane; a docstring named
call sites that never existed; and `_bind_record_tenant` evaluated its argument *outside* the guard.

**Cycle 3 (6 findings: P0=1, P1=1, P2=3, P3=1, all applied).**

- **P0 — the wrapper swallowed `apply`'s return value.** `create_source` reads it to decide whether a
  `SourceVersioned` record is owed (#5024 T6), and the journal is append-only, so the loss was
  permanent. Measured by the reviewer: `test_source_rematerialisation_5024` **12 failed**,
  `test_source_version_window_3644` **5 failed**. Fixed by returning the fold's result, and pinned by
  `test_apply_still_RETURNS_the_fold_result`. My own §8 table had not run those files — the
  reviewer's, and now this PR's, verification covers them (**169 passed**).
- **P1 — `**kwargs` erased the keyword names the replay engines introspect.** `consistency.recover_from_log`
  and `backup.restore` read `inspect.signature(proj.apply).parameters` to decide whether to hand this
  engine the journal's context; with `**kwargs` they stopped threading it —
  `test_rebuild_parity_fail_loud_3585` **8 failed**. Fixed by restoring the explicit keyword-only
  signature and forwarding each name, pinned by
  `test_apply_keeps_the_signature_the_replay_engines_introspect`.
- **P2 — `_skip()` did not cover every writing-nothing exit**, and the `ObjectSuperseded` one was
  nested inside the *journal-context* gate, i.e. absent on exactly the live one-record path this
  instrument serves. Fixed at all four sites and pinned by
  `test_the_OTHER_writing_nothing_exits_are_not_counted` (parametrized).
- **P2 — the "two lanes" bound was false** (see §4.2): three declared gateways were uncounted. Fixed.
- **P2 — an evidence block in this body was not in the capture** (it came from a separate manual CLI
  run on a tenant whose cache had been recreated, and cited a key that exists nowhere in the tree).
  Removed; both quoted blocks now come from `door-matrix-shared-cache.json`, with the one omitted key
  named.
- **P3 — the harness's `reached_*` summaries could read "the cache received nothing" over a landed
  write.** Added `reached_actual` from the target registry; the capture now carries it for every row.

## 8. Verification

| suite | result |
|---|---|
| `tests/test_12943_cache_leg.py` | **41 passed** (the two DB legs need the `postgres` extra: `uv sync --extra embeddings --extra parity --extra postgres`) |
| `test_source_rematerialisation_5024`, `test_source_version_window_3644`, `test_rebuild_parity_fail_loud_3585` (the suites review cycle 3 proved broken by this PR's first draft) | **169 passed** |
| `test_7951_*`, `test_9079_record_replay_declared`, `test_9124_declared_live_doors`, `test_9206_apply_first_lane`, `test_9391_replay_window_raw_query_width`, `test_cache_projection*`, `test_12943_cache_leg` | **276 passed, 1 failed** — the failure is `test_9206_apply_first_lane.py::test_capture_session_writes_and_records_both_halves_under_the_armed_guard`, which refuses for a missing `DEEPSEEK_API_KEY` / `TORTOISE_SESSION_LLM_MOCK` (**environment**, reproduced independently of this diff; recorded in `WORKTREE-REPORT.md`) |
| `tests/test_hosted_api.py` | 12 failures, **environment-owned and reproduced at the base commit**: the test Postgres is passwordless, so per-graph ACL provisioning returns 503 "default ACL user is open (nopass)" |

Counts above come from `tools/worktree_report.py run` (plain pytest commands, verbatim, logged
under `.worktree-report/logs/`), never asserted by hand.
