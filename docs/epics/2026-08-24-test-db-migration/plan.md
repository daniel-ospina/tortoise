<!-- research-path: docs/epics/2026-08-24-test-db-migration/research-brief.md -->

# Epic #1647 Implementation Plan — Migrate test suite from embedded FalkorDBLite to real FalkorDB (docker)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make real FalkorDB (docker, matching the selfhost product) the default test DB — one URI-aware seam flips all test DB construction; embedded FalkorDBLite remains only for the 342-test behavioral carve-out — eliminating the test debt caused by the parallel embedded stack (reaper/orphans/EmbeddedStoreBusyError/concurrency divergence).

**Team:** epistemic-team

**Architecture:** A four-phase strangler rollout (P1 seam+hermeticity with zero behavior change → P2 one fast-matrix half on docker → P3 both halves → P4 allowlist/reaper shrink). The core mechanism is a **class-level URI-aware redirect** in `FalkorProjection.__init__` (the `path is not None` branch): when `TORTOISE_TEST_MODE=1` (exported by conftest — the test-session signal, plan-review P0-4), `TORTOISE_DB_URI` (supported scheme), **and a calling test frame** (`_caller_test_stem() is not None` — cycle-2 P1-1b: subprocess CLI children inherit the URI+TEST_MODE env but have no test frame, so they must never redirect) are all present, raw `path=` constructions from a **non-exempt test module** construct server-mode from the URI with a guard-passing derived graph name `test_<stem_sanitized>_<hash12(session+path)>` (per-path **and** per-session unique — cycle-2 P0-1b/P2-3; stems sanitized to `[a-zA-Z0-9_]` and 12 hex = 48+ bits — cycle-3 P2-5/P2-17); the redirect **refuses non-loopback hosts before the first write** (cycle-2 P0-2, escape `TORTOISE_TEST_ALLOW_REMOTE=1` — **write-only**: wipes still refuse, cycle-3 P2-6) and inherits the existing host branch's raw-client lifecycle — no worse than today's `from_uri` sites, `FalkorProjection.close()` disconnects the pool (cycle-3 P1-4); the redirect additionally records `self._host = host` so `wipe_server`/sweep/tripwire can extract the host without touching the raw client (cycle-3 P0-1); otherwise the embedded path is byte-for-byte today's code. Prod tools (backup/rebuild/migrate) never redirect: they never run under `TORTOISE_TEST_MODE` (and prod-role entry points pop it in their `if __name__ == "__main__":` blocks / subprocess launchers ONLY — never at module import or in a bare `main()` a test can call in-process — cycle-3 P1-3). `TORTOISE_TEST_NO_REDIRECT` exempts **caller test modules** (frame-identified, plan-review P0-1), not DB-file stems. Hermeticity is name-first: guard-passing graph names + a server-mode `wipe_server()` filtered to `test_`/`tortoise_test_`-prefixed graphs, refusing non-loopback hosts, with a `_wipe_or()` dispatcher so every existing `wipe()` caller migrates (plan-review P0-2). Fail-closed semantics are enforced by extending `tools/skip-guard.py` with a coverage manifest reconciled against **junitxml** (the lossless per-testcase PASSED/SKIPPED+reason source, plan-review P0-3) plus a conftest backend-identity tripwire.

**Pattern Research:** (embedded from `external-research.md`, verifier-gated 2026-08-24)

Three evidence buckets drove the 4 approved decisions:

1. **Canonical test-isolation patterns (Q1)** — Jimmy Bogard's canonical catalog (transaction rollback / delete-all / per-test DB), EF Core ("single shared DB, seeded once"), Rails/Django/pytest-django (transaction rollback per test), and for Redis-family: dedicated test DB + `FLUSHDB`, or unique key prefixes. For FalkorDB specifically, the vendor security guide sanctions **per-graph isolation** ("separate graph names for teams or tenants") and the graph key is the natural isolation unit ("every query targets a single graph key"). Pitfall: per-test DB at 1,000+ test scale is documented as too slow; the scalable pattern is per-worker/per-session DB + flush between tests.
2. **Embedded-vs-server divergence (Q2)** — the standard consensus (Testcontainers, EF Core, Fowler, Neon's "Dangers of Testing in SQLite") is: test on the same DB as production; a substitute is a test double that can pass while production fails. Caveat unique to this project: FalkorDBLite is the **vendor's own embedded build with a parity claim** ("simply swap the connection line") — so the residual divergence is topology (single-process, no network, no multi-client concurrency), not Cypher semantics. The vendor's own migration story ("swap the connection line") is the direct evidence for **D-1=A**: one seam flips everything.
3. **Fail-closed + containerized CI (Q3/Q5)** — Testcontainers maintainer issue #343: "letting the test fail is the correct behavior" (fail-closed, **D-4=A**); CI should use GitHub Actions service containers (already provisioned for this repo, #1436); community consensus is ONE production-like DB in CI — a dual lane is only a bounded transition artifact (strangler-fig applied to acceptance, not code scope, **D-3=A**). Per-test marking is the documented norm for localized exceptions (pytest-django `@pytest.mark.django_db`, Rails `use_transactional_tests = false` per case — **D-2=A**); whole-file marking is only for module-wide properties.

**Tech Stack:** Python 3.12 (uv), pytest (single-process, no xdist), falkordb/falkordb-server docker image (6379 passworded + 16379 passwordless services, already in `python-ci.yml`), FalkorDBLite (redislite) for the carve-out, GitHub Actions service containers, `tools/skip-guard.py` / `tools/ci_selection.py`.

---

### Integration Surface Map

From the Test-Design gate: the epic's 5 verification surfaces mapped to the 8 E2E tests + the 3 new unit-test surfaces the plan introduces. Each E2E is a runnable gate with a concrete test layer.

| # | Surface (E2E) | Test Layer | Bug-pattern flags | Verification contract |
|---|---|---|---|---|
| E2E-1 | DB-agnostic round-trip, docker vs embedded | integration (parametrized over URI set/unset) | false-confidence (substitute passes, prod fails) | identical results where D1/D5-identical; documented divergence (D6 composite, D8 ordering) asserts its own side |
| E2E-2 | Bulk-wipe on docker without tripping the graph guard | integration (shared-graph tier + `wipe_server`) | cross-test pollution masquerading as flake | no `RuntimeError` from `_assert_test_graph`; control test proves bare-`test` still raises (guard intact) |
| E2E-3 | Concurrency: multi-tenant, no `EmbeddedStoreBusyError` | integration (live-writer tests under job URI) | single-writer semantics leaking into prod | 0 busy errors; concurrent writes all present (D11/D12 documented divergence) |
| E2E-4 | Carve-out (342 tests) still passes on embedded | unit + integration (URI-unset job) | carve-out accidentally migrated | all 342 pass; recovery auto-rebuild (D2/D3) + busy-error (D11) embedded-only; no carve-out file's embedded path depends on `TORTOISE_DB_URI` being set |
| E2E-5 | Fast matrix green on docker; wall ≤ 20% of baseline | e2e (CI matrix) | wall-time regression; watchdog ride | both halves pass; half a ≤ ~50m, half b clears 55m watchdog (target ≤ ~45m); wall recorded for P3/P4 merge decision |
| E2E-6 | Missing docker → fail-closed / visible skip, never green-skip | e2e (outage simulation + skip-guard) | vacuous pass (#942 class) | every migrated test fails loudly OR skips with FalkorDB-reason → guard red; backend-identity tripwire catches "redirect inert + embedded succeeds" |
| E2E-7 | Zero redislite orphans on docker halves; bounded on carve-out; **server GRAPH.LIST bounded (cycle-4 P1-9)** | e2e (orphan assert step + GRAPH.LIST check) | leak accumulation (redislite processes **and** server graph-count) | docker halves: 0 orphans post-run; carve-out jobs keep <20 bounded assert; **docker sessions end with `GRAPH.LIST` count < journal size + constant — the session-end/stale sweeps GRAPH.DELETE journaled graphs after DETACH (Task 2 Step 7, drop=True) so a persistent dev docker cannot accumulate every derived graph forever (the old spec measured redislite processes only — invisible to DETACH-only wipe accumulation)** |
| E2E-8 | Divergence change-list conformance (D1–D16) | integration (conformance file) | silent divergence beyond the documented list | each D1–D16 branch asserts its documented behavior in both modes; the file is the executable change list |
| — | **Seam redirect** (new unit surface) | unit (tests/test_redirect_seam.py) | redirect fires when it must not (prod path) | URI unset → `_is_embedded is True` byte-identical; URI + `TORTOISE_TEST_MODE` set → server mode + derived `test_*` name; explicit graph_name honored; `TORTOISE_TEST_NO_REDIRECT` exempts the **caller test module** (frame-identified, never the DB-file stem); `:memory:` derives a per-construction unique `test_memory_<nonce>` |
| — | **wipe_server** (new unit surface) | unit (tests/test_wipe_server.py) | wiping a non-test graph (data loss) | wipes only `test_`/`tortoise_test_`-prefixed graphs; refuses non-loopback hosts; embedded `wipe()` unchanged |
| — | **Coverage manifest** (new unit surface) | unit (tests/test_skip_guard.py) | vacuous early-return (nodeid vanishes) | junitxml is the authoritative observed set (lossless per-testcase nodeid + skip reason); expected nodeid (from `--collect-only`) missing from the junitxml testcases → red; missing junitxml + manifest → red (flip of `test_missing_log_is_not_a_failure`) |

**Complexity ratings (from issue):** Architecture = complex, Config = standard, Ontology = low. Verification routing: integration + e2e depth for the migration surfaces; unit for the 3 new mechanisms; no UX domain (no UI); no content domain.

---

### E2E test catalog (runnable gates)

Each E2E ships as a concrete artifact (new test file, CI job config, or existing-test adaptation). Setup/assertion/gates per E2E; phase gates below reference these.

- **E2E-1** — `tests/test_round_trip_parity.py` (new): parametrized `create_point → search` round-trip over `TORTOISE_DB_URI` set/unset. Assertion: point id, content_hash, search hit list, vector results identical on the D1/D5-identical paths; D6/D8 sides assert their documented expectation. **Owner: Task 1** (cycle-2 P1-5 — folded in as Task 1 Step 5b; it directly verifies the Task 1 seam in both modes). **Gates:** P1 mechanism → P2 docker half proves it.
- **E2E-2** — shared-graph tier fixture + `wipe_server()`; exercised by existing wipe-heavy files (test_projection, test_search_engine_gaps, test_a9_direct_edge_traversal, test_index_surfacing, test_recall_gaps_subgraph, test_about_event_untangle) under URI set + a control test (`test_bare_test_graph_wipe_still_raises`) in `tests/test_wipe_server.py`. **Gates:** P1 unit → P2 docker smoke of the wipe-heavy + `_wipe_or` surfaces (Task 6 Step 3 local pre-flight — only test_a9_direct_edge_traversal + test_recall_gaps_subgraph are in fast half b; test_projection/test_search_engine_gaps/test_index_surfacing/test_about_event_untangle are half-a/slow and follow at P3 — plan-review P1-6).
- **E2E-3** — `tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes` + live-writer portion (:130) under job URI; the 3 busy-error tests stay embedded-marked (skip visibly on docker, pass on embedded). **Gates:** P2 docker smoke (Task 6 Step 3 pre-flight includes the live-writer tests; test_embedded_concurrency is a test-slow file, not in the P2 half-b flip — plan-review P1-6) → P3 (both halves, CI-wide).
- **E2E-4** — carve-out job (URI unset) running exactly the 342-test set; skip-guard exempts them (redislite-availability class ≠ FalkorDB-availability). **Gates:** P1 (untouched) → P4 (post-shrink registry consistency).
- **E2E-5** — fast-matrix CI jobs; half-b wall is the P2 data point, both-halves the P3 gate. **Wall measured on the JOB wall** (`timeout-minutes: 60`), not the pytest-step wall (cycle-2 P2-12: manifest collect-only runs in its own step so it cannot consume the pytest step's 55m budget) — **AND the pytest-step wall separately (cycle-3 P2-16):** the run step captures `step_wall` via the in-step `SECONDS` accounting the watchdog already uses; gate `step_wall < 55m` with margin (target ≤ ~45m) so a step that silently rides the watchdog is caught by the gate, not just reported. **Gates:** P2 (half b) → P3 (both halves).
- **E2E-6** — skip-guard coverage manifest + backend-identity tripwire; outage simulation = **docker service down WITH URI set** → FalkorDB-reasoned skips → guard red (cycle-2 P2-6: a URI-less run is the carve-out shape and can never go red; the vacuous-pass side is closed by `TORTOISE_TEST_EXPECT_URI=1`, the session signal that fails a docker-half session whose URI is missing). **Gates:** P2 (manifest on half b) → P3 (both halves).
- **E2E-7** — orphan-assert CI step re-targeted per surface; **cycle-4 P1-9 adds the server-side GRAPH.LIST growth bound** (docker sessions end with `GRAPH.LIST` count < journal size + constant — DETACH-only wipes empty but never remove graphs, invisible to the redislite-process count). **Gates:** P3 (docker ~0) → P4 (reaper demotion; dev-machine <20 without reaper).
- **E2E-8** — `tests/test_divergence_conformance.py` (new): asserts D2/D3 recovery raises on server, D6 composite only on server, D8 HNSW only on server, D11 busy-error embedded-only, D12 multi-tenant on server. **Gates:** P1 (expectation split) → P2 (side-by-side confirmation) → P3 (both modes enforced).

---

## Phase 1 — Seam + hermeticity (zero behavior change)

> Embedded remains the default; `TORTOISE_DB_URI` is not set anywhere in P1, so every new mechanism is **dormant**. Acceptance: full 6,837-test embedded suite green (identical to baseline), redirect/wipe/manifest unit-tested, no orphan delta.

### Task 1: Class-level URI-aware redirect + fixture-seam URI-awareness

**Intent:** Create the single seam (decision D-1=A) — one place flips ALL test DB construction to real FalkorDB when `TORTOISE_DB_URI` is set **and the caller is a test session** (`TORTOISE_TEST_MODE=1`, exported by conftest — plan-review P0-4: prod tools that construct with explicit paths must NEVER redirect). Raw `FalkorProjection(path=...)` constructions (114 no-graph-name sites in 27 files) redirect automatically; seam fixtures (`shared_proj`/`sdk_factory`/`shared_embedded_db`) get URI-aware branches. Inert in P1 (URI unset) — embedded path byte-for-byte today.

**Acceptance:**
- With `TORTOISE_DB_URI` unset, `FalkorProjection(path=...)` constructs identically to today (`_is_embedded is True`, embedded subclass, AOF/relative-path semantics intact) — verified by the pre-existing embedded suite plus the new inertness test.
- With `TORTOISE_DB_URI` set to a supported scheme (`docker`/`redis`/`rediss`), `TORTOISE_TEST_MODE=1`, **and a calling test frame present** (`_caller_test_stem() is not None` — cycle-2 P1-1b), `FalkorProjection(path=...)` from a non-exempt test module constructs server-mode (`_is_embedded is False`, host/port/user/pass from the URI — `from_uri` semantics), with graph name = test-prefixed explicit `graph_name` if given (the **shared opt-in**: honored verbatim, e.g. `test_explicit`/`test_suite_<uuid>`), else derived `test_<stem_sanitized>_<hash12(session+path)>` (guard-passing, **per-path AND per-session unique** — cycle-2 P0-1b: distinct paths with the same explicit non-test name (the parity/g_consistency pairs) must land on distinct server graphs; cycle-2 P2-3: the session nonce keeps concurrent sessions' same-path derivations distinct; cycle-3 P2-5: the path-derived stem is sanitized to `[a-zA-Z0-9_]` before embedding — tmp paths carry `-`/`/`; cycle-3 P2-17: 12 hex = 48+ bits, the 32-bit `[:8]` is a collision risk at multi-thousand-graph scale). With URI set but `TORTOISE_TEST_MODE` absent (prod tools: backup.py:134/144, ingest.py:471, **`__main__.py` 6 path-construction sites** — L13 rebuild, L521 doctor-fallback, L1724 `_resolve_db_target`, L1915 dump, L2165 backfill, L2562 export; **cycle-4 P2-8 corrected census: the plan's "`__main__.py:13`" undercounted — VERIFIED 6 sites this pass; backup.py has NO `main()`/`__main__` block — its backup/restore entry is the `tortoise` CLI at `__main__.py:4121-4127` (pyproject scripts: tortoise/tortoise-ingest/tortoise-serve only, verified)**; migrate_db.py:75/184, hosted_api.py:6254/8263/8357, pipeline_cli.py:139 — verified), **no redirect** — the embedded path construction is preserved (plan-review P0-4); prod-role entry points additionally `os.environ.pop("TORTOISE_TEST_MODE", None)` **only in their `if __name__ == "__main__":` blocks / subprocess-launcher paths** so a TEST_MODE leaked into their process cannot redirect them (cycle-2 P2-10; cycle-3 P1-3: never at module import or in a `main()` a test can call in-process — a mid-session pop would silently kill the redirect and green-pass on the wrong backend). **hosted_api is dropped from the pop list entirely** (verified: its 3 `path=` constructions at 6254/8263/8357 are inside `if getattr(proj, "_path", None):` embedded-only guards — no pop needed; it has no `main()`/`__main__` block to pin to).
- **Lane-safe seam/parity tests (cycle-3 P1-6):** every seam/parity test is self-contained w.r.t. `TORTOISE_DB_URI` — unset-assuming tests `monkeypatch.delenv("TORTOISE_DB_URI", raising=False)`, URI-assuming tests set it explicitly; E2E-1 is parametrized with real env control (embedded leg `delenv`, docker leg `setenv`) so a URI-set dev lane can never run docker-vs-docker (which can never detect divergence). Item added to the Task 1 acceptance checklist (below) and verified in Step 1's tests.
- **Non-loopback refusal (cycle-2 P0-2, fail-fast):** the redirect raises `RuntimeError` with a locality message when the URI's host is not loopback — BEFORE `path = None` falls through to any construction, so a typo'd/shared `TORTOISE_DB_URI` can never mint `test_*` graphs or write on a remote server (D-4's protection currently fires only at `wipe_server`, which is AFTER pollution). Escape: `TORTOISE_TEST_ALLOW_REMOTE=1` (explicit opt-in; wipe_server/sweep still refuse — D-4 unchanged; **cycle-3 P2-6: the escape is WRITE-ONLY by design** — a `TORTOISE_TEST_ALLOW_REMOTE=1` session can redirect + write on a remote server but cannot wipe it, so it is only usable for assert-only/read-only sessions; recorded in Failure Modes. If a future workflow needs remote wipes, that is a separate explicit opt-in, never folded into the write escape). The session-start tripwire (Task 4) re-checks the same predicate so the failure happens before ANY test writes, not just before the first redirect-eligible construction.
- **Host recording + connection lifecycle (cycle-3 P0-1/P1-4, supersedes cycle-2 P2-11):** the host branch records `self._host = host` at construction (both `from_uri` → `cls(host=...)` and the redirect's fall-through land there), so `wipe_server`/session sweep/tripwire read the projection, never the raw client. VERIFIED: the host branch constructs `from falkordb import FalkorDB` (raw — projection/__init__.py:380-381); the guarded subclass is embedded-only (redislite can't take `host=`/`port=`), so the redirect inherits the existing host branch's raw-client lifecycle — **no better, no worse than today's `from_uri` sites**, and `FalkorProjection.close()` disconnects the pool (L1547-1568, verified). The boundedness unit test asserts POOL RELEASE (below), not `conn.closed`/`conn.connection` — those do not exist on a redis.Redis (redis-py 8.1.0 verified: host lives in `connection_pool.connection_kwargs['host']`, and `_falkordb_version_cache_key` at projection L999 reads the same dead `.connection.host` pattern — fixed by the same `self._host` recording).
- `TORTOISE_TEST_NO_REDIRECT=<comma-separated TEST-MODULE stems>` (e.g. `test_ops_safety`, `test_config`) exempts constructions whose **caller test module** is listed — resolved by frame inspection (`_caller_test_stem()`, see Step 3), NEVER by the DB-file basename (plan-review P0-1: carve-out files use arbitrary DB names — `c.db`, `user.db`, `fresh.db`, `solo.db`, `lost.db`… — that never match their file stems, so the old stem-keyed check never fired). A listed module keeps `path=` embedded construction even when URI is set. List wired in Task 5.
- `path == ":memory:"` is special-cased: derives a per-construction unique `test_memory_<8-hex nonce>` graph (plan-review P2-13 — `:memory:` is a constant string, so a path-hash would collide every `:memory:` construction in a run onto one shared server graph, breaking `test_open_kinds`' per-construction isolation).
- `skip_health_check` (and `allow_nonstandard_path`) are preserved on the redirect path — the redirect falls through to the existing host-mode branch in the SAME `__init__` frame instead of recursing `cls(...)` (plan-review P0-4).
- No-arg `FalkorProjection()` does NOT redirect (D-1 scope: explicit `path=` only) — still resolves the canonical embedded path. Captured by recording `explicit_path = path is not None` BEFORE the `resolve_db_path()` no-arg fallback (verified: `__init__` resolves no-arg → path at L311-313, so a naive path-branch check would also capture no-arg).
- `shared_proj`/`sdk_factory`/`shared_embedded_db` URI-aware: URI set → server construction with `test_suite_<uuid>`-style graph names; unset → today's construction. The URI branch is evaluated BEFORE `has_falkor()`/`skip_if_no_falkor()` (plan-review P2-16): under URI, `has_falkor()` returns True immediately (no embedded probe — the probe would redirect and mint a `test` graph on the server) so migrated files never early-return.
- Unsupported URI scheme → embedded construction (fail-safe, no crash).

**Files:**
- Modify: `tortoise/projection/__init__.py:303-392` (`__init__` path branch — the redirect insertion; `from_uri` at L547 reused for URI parsing)
- Modify: `tests/_embedded.py` (`shared_proj` URI branch; `TEST_NO_REDIRECT_STEMS` constant)
- Modify: `tests/conftest.py` (`sdk_factory` ~L83 URI branch; `shared_embedded_db` URI branch; export `TORTOISE_TEST_NO_REDIRECT` via `os.environ.setdefault` from the `tests/_embedded.py` constant so the product-side redirect reads the in-repo list without importing `tests/`; export `os.environ.setdefault("TORTOISE_TEST_MODE", "1")` — the test-session signal that gates the redirect, plan-review P0-4; export `os.environ.setdefault("TORTOISE_TEST_SESSION", os.urandom(4).hex())` — the session nonce folded into derived graph names, cycle-2 P2-3; `has_falkor()` URI short-circuit per P2-16)
- Test: `tests/test_redirect_seam.py` (new)

**Step 1: Write the failing tests**

```python
# tests/test_redirect_seam.py
"""Unit surface: the class-level URI-aware redirect (epic #1647 Task 1)."""
import os
import subprocess
import sys

import pytest

from tortoise.projection import FalkorProjection


@pytest.fixture
def uri_env(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    yield


def test_unset_uri_constructs_embedded(monkeypatch):
    # Cycle-3 P1-6: self-contained w.r.t. TORTOISE_DB_URI — a dev/CI lane
    # with a URI set must not flip this to the server lane (the P2 half-b
    # docker job would red it).
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    proj = FalkorProjection("/tmp/seam-test-a.db")
    try:
        assert proj._is_embedded is True
    finally:
        proj.close()


def test_uri_set_redirects_to_server(uri_env):
    proj = FalkorProjection("/tmp/seam-test-b.db")
    try:
        assert proj._is_embedded is False
        # graph name derived: test_<stem_sanitized>_<hash12(session+path)>
        # — per-path + per-session unique (cycle-2 P0-1b/P2-3); stem
        # sanitized (cycle-3 P2-5) + 12 hex = 48+ bits (cycle-3 P2-17)
        assert proj.graph_name.startswith("test_seam_test_b_")
        assert len(proj.graph_name) == len("test_seam_test_b_") + 12
        assert proj._host == "localhost"  # cycle-3 P0-1: recorded on the projection
    finally:
        proj.close()


def test_explicit_graph_name_honored(uri_env):
    # Cycle-2 P0-1b rule: a TEST-PREFIXED explicit name is the shared opt-in
    # — honored verbatim (seam fixtures use test_suite_<uuid> this way).
    proj = FalkorProjection("/tmp/seam-test-c.db", graph_name="test_explicit")
    try:
        assert proj.graph_name == "test_explicit"
        assert proj._is_embedded is False
    finally:
        proj.close()


def test_explicit_nondefault_name_derives_per_path(uri_env):
    # Cycle-2 P0-1b: test_projection's parity pair constructs DISTINCT paths
    # with the SAME explicit non-test name ("test"). Deriving per-path names
    # is mandatory — a shared rename would collapse parity_a/parity_b onto
    # one server graph and the apply-vs-rebuild comparison would be a graph
    # compared to itself (vacuous pass, #942 class). Same path + same
    # explicit name must still share (the embedded same-file analog).
    a = FalkorProjection("/tmp/seam-parity-a.db", graph_name="test")
    b = FalkorProjection("/tmp/seam-parity-b.db", graph_name="test")
    a2 = FalkorProjection("/tmp/seam-parity-a.db", graph_name="test")
    try:
        assert a.graph_name != b.graph_name, "distinct paths must yield distinct server graphs"
        # cycle-3 P2-5: path-derived stems are sanitized (hyphens → "_")
        assert a.graph_name.startswith("test_seam_parity_a_")
        assert b.graph_name.startswith("test_seam_parity_b_")
        assert a2.graph_name == a.graph_name, "same path + same explicit name shares"
        assert a._is_embedded is False
    finally:
        a.close()
        b.close()
        a2.close()


def test_no_arg_does_not_redirect(uri_env):
    # D-1 scope: explicit path= only. No-arg stays embedded canonical.
    proj = FalkorProjection()
    try:
        assert proj._is_embedded is True
    finally:
        proj.close()


def test_no_redirect_env_exempts_caller_test_module(uri_env, monkeypatch):
    # P0-1: the exemption keys on the CALLER TEST MODULE (frame-identified),
    # never on the DB-file basename. List this test file's own stem.
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "test_redirect_seam")
    proj = FalkorProjection("/tmp/seam-test-d.db")
    try:
        assert proj._is_embedded is True  # exempted caller module stays embedded
    finally:
        proj.close()


def test_db_file_stem_never_exempts(uri_env, monkeypatch):
    # P0-1 regression: the OLD bug compared os.path.basename(path) against
    # the exemption list — carve-out files use arbitrary DB names (c.db,
    # fresh.db, solo.db...) that never match their file stems, so the
    # exemption never fired. Listing a DB-file stem must NOT exempt.
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "seam-test-d")
    proj = FalkorProjection("/tmp/seam-test-d.db")
    try:
        assert proj._is_embedded is False  # DB basename ≠ test module → redirects
    finally:
        proj.close()


def test_no_test_frame_in_stack_no_redirect(monkeypatch):
    # Cycle-2 P1-1b: subprocess CLI children (test_export_cli's `python -m
    # tortoise export`, redis-guard fixture scripts, ...) inherit
    # TORTOISE_DB_URI + TORTOISE_TEST_MODE via os.environ.copy() but their
    # process has NO test module in the stack (_caller_test_stem() → None).
    # The redirect must NOT fire — the child exercises the embedded/CLI lane.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    out = subprocess.run(
        [sys.executable, "-c",
         "from tortoise.projection import FalkorProjection; "
         "p = FalkorProjection('/tmp/seam-child.db', skip_health_check=True); "
         "print(p._is_embedded); p.close()"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True", \
        "no test frame in the stack → embedded lane must be preserved"


def test_caller_test_stem_nearest_frame_semantics(uri_env, monkeypatch):
    # Cycle-3 P2-18: _caller_test_stem() keys on the NEAREST test_ frame, not
    # the outermost. A construction made through a CROSS-TEST-MODULE helper
    # (a test_-prefixed module, e.g. tests/test_helpers.py, imported by other
    # test files) resolves to the HELPER's stem — so listing a shared helper
    # in TEST_NO_REDIRECT_STEMS exempts ALL its callers (documented caveat;
    # the exemption list is the caller-module list, and shared helpers must
    # never be listed). Non-test_-prefixed helpers (tests/_embedded.py) are
    # skipped by the prefix sniff and resolve UP to the calling test module.
    from tortoise.projection import _caller_test_stem
    # Cycle-4 P1-4: tests/test_helpers.py is CREATED in Step 3's edit list
    # below (this file would otherwise ImportError at collection — the
    # cycle-3 plan referenced it without an owner task). It is the ONLY
    # test_-prefixed shared helper; the P2-9 stem-registry guard (Task 5
    # Step 1) asserts no carve-out file imports it — a carve-out
    # constructing through it would resolve to stem "test_helpers" (not its
    # own exempted stem) and silently lose its redirect exemption.
    import tests.test_helpers as _th  # test_-prefixed helper module (tiny, new)
    assert _caller_test_stem() == "test_redirect_seam"  # this file is nearest
    assert _th.construct_via_helper() == "test_helpers", \
        "nearest-frame semantics: the helper's own stem wins over the caller"


def test_memory_path_derives_unique_test_graph(uri_env):
    # P2-13: :memory: is a constant string — a path-hash would collide every
    # :memory: construction onto one shared graph. Must derive a per-
    # construction unique test_memory_* graph instead.
    proj = FalkorProjection(":memory:")
    proj2 = FalkorProjection(":memory:")
    try:
        assert proj._is_embedded is False
        assert proj.graph_name.startswith("test_memory_")
        assert proj.graph_name != proj2.graph_name
    finally:
        proj.close()
        proj2.close()


def test_no_redirect_without_test_mode(monkeypatch):
    # P0-4: TORTOISE_TEST_MODE is the test-session signal. URI set but
    # TEST_MODE absent (prod tool: backup/rebuild/migrate) → NO redirect —
    # prod path constructions are preserved byte-for-byte.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    monkeypatch.delenv("TORTOISE_TEST_MODE", raising=False)
    proj = FalkorProjection("/tmp/seam-test-f.db", skip_health_check=True)
    try:
        assert proj._is_embedded is True  # prod-style construction unaffected
    finally:
        proj.close()


def test_uri_set_refuses_non_loopback_host(monkeypatch):
    # Cycle-2 P0-2 (fail-fast): a typo'd/shared TORTOISE_DB_URI must refuse
    # BEFORE the first construction — not at the first wipe_server call,
    # which is after every migrated test has already written to the remote.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@db.internal.example.com:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    with pytest.raises(RuntimeError, match="loopback"):
        FalkorProjection("/tmp/seam-remote.db", skip_health_check=True)


def test_allow_remote_escape_lets_non_loopback_through(monkeypatch):
    # P0-2 escape: TORTOISE_TEST_ALLOW_REMOTE=1 is the explicit opt-in. The
    # redirect proceeds (wipe_server still refuses non-loopback — D-4;
    # cycle-3 P2-6: the escape is write-only — usable for assert-only/read-only
    # sessions, never for wipes).
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@db.internal.example.com:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    monkeypatch.setenv("TORTOISE_TEST_ALLOW_REMOTE", "1")
    # Cycle-4 P1-1: the redirect falls through to the host branch, which
    # constructs `from falkordb import FalkorDB` — and the REAL FalkorDB
    # constructor performs a LIVE round-trip at __init__ (falkordb.py:132
    # Is_Sentinel(conn) → conn.info(), verified in the vendored 3.x source)
    # BEFORE returning, so a remote host raises redis.exceptions.ConnectionError
    # (not RuntimeError) and the test could never reach its assertions. FIX:
    # monkeypatch falkordb.FalkorDB with a stub — the host branch only needs
    # select_graph() + close() (projection L380-390 + L1547-1568, verified);
    # the redirect's own predicate + _host recording are what this test pins.
    import types

    class _GraphStub:
        def query(self, *a, **k):
            return types.SimpleNamespace(result_set=[])

    class _DbStub:
        def select_graph(self, name):
            return _GraphStub()
        def close(self):
            pass

    class _FakeFalkorDB:
        def __init__(self, **kw):
            self.db = _DbStub()

    monkeypatch.setattr("falkordb.FalkorDB", _FakeFalkorDB)
    proj = FalkorProjection("/tmp/seam-remote.db", skip_health_check=True)
    try:
        assert proj._is_embedded is False
        assert proj.graph_name.startswith("test_seam_remote_")  # sanitized stem (P2-5)
        assert proj._host == "db.internal.example.com"  # cycle-3 P0-1: recorded
    finally:
        proj.close()


def test_redirect_connection_boundedness(uri_env):
    # Cycle-4 P1-2 (re-scoped from cycle-3 P1-4 — the pool-release assert was
    # STILL INVERTED/VACUOUS): on redis-py 8.1.0, `p.db.connection` CHECKS OUT
    # a connection (pool._available_connections == 0 before), then close() →
    # pool.release() → disconnect() (which does NOT remove connection objects)
    # → reset() (which empties) → after == 0 → "0 >= 0" passes; AND a NO-OP
    # close (a real leak) also passes (after == before). The assert cannot
    # distinguish release from leak. Re-scoped to a SERVER-VISIBLE metric +
    # a weakref live-count; redis-py introspection is secondary only.
    #   (a) WEAKREF live ConnectionPool count: after close(), the pool must be
    #       unreferenced (collected by gc) — a leaked client keeps it alive.
    #   (b) SERVER-VISIBLE: INFO clients' connected_clients before vs after 20
    #       construct/close cycles must grow by ≤ 1 (the probe's own client),
    #       never by ~20 — this is what the docker job can actually observe.
    import gc
    import weakref
    import redis
    pools = []
    for i in range(20):
        p = FalkorProjection(f"/tmp/seam-conn-{i}.db")
        conn = p.db.connection  # redis.Redis client
        pools.append(weakref.ref(conn.connection_pool))
        assert isinstance(conn, redis.Redis), "host branch is the raw redis client"
        p.close()
    gc.collect()
    live = [r for r in pools if r() is not None]
    assert len(live) <= 1, \
        f"close() leaked {len(live)} of 20 live ConnectionPool instances (weakref)"
    probe = redis.Redis(host="localhost", port=6379, password="falkordb",
                        socket_connect_timeout=5)
    try:
        before = int(probe.info("clients")["connected_clients"])
    finally:
        probe.close()
    for i in range(20):
        p = FalkorProjection(f"/tmp/seam-conn-{i}.db")
        p.close()
    probe = redis.Redis(host="localhost", port=6379, password="falkordb",
                        socket_connect_timeout=5)
    try:
        after = int(probe.info("clients")["connected_clients"])
    finally:
        probe.close()
    assert after - before <= 1, \
        f"close() left {after - before} extra server-side clients (INFO clients)"


def test_unsupported_uri_scheme_stays_embedded(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", "postgres://x@localhost/y")
    proj = FalkorProjection("/tmp/seam-test-e.db")
    try:
        assert proj._is_embedded is True
    finally:
        proj.close()
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_redirect_seam.py -v`
Expected: `test_uri_set_redirects_to_server` fails (`_is_embedded` is True — no redirect exists yet); others may pass (the "no uri branch exists yet" claim verified — genuine insertion).

**Step 3: Implement the redirect**

In `FalkorProjection.__init__` (L303), before the no-arg resolution:

```python
explicit_path = path is not None
```

Between the no-arg resolution and the existing `if path is not None:` branch, insert the redirect. It resolves the server params, sets `path = None`, and falls through to the EXISTING `elif host is not None:` branch — the host-mode construction is reused, not duplicated, and the embedded body (relative-path reject, AOF config, `FalkorDB(path, serverconfig=...)` subclass) is skipped because `path` is now None. `skip_health_check`/`allow_nonstandard_path` keep their caller values (plan-review P0-4) — no recursive `cls(...)` call. **The host branch additionally records `self._host = host` after constructing `self.db` (cycle-3 P0-1)** — the redirect's reassigned `host` local flows into that branch, and `from_uri` → `cls(host=...)` lands there too, so every server projection carries its host on the projection (wipe_server/sweep/tripwire read it instead of the raw client, which has no `.host` — redis-py 8.1.0 verified; `_falkordb_version_cache_key` at L999 reads the same dead pattern and is fixed by the same recording: `host = getattr(self, "_host", None)` first).

**Edit list for this step (cycle-3 P2-4 + cycle-4 P1-4):** add `import hashlib` to the module imports of `tortoise/projection/__init__.py` (verified NOT currently imported — the snippet below calls `hashlib.sha1`). **Create `tests/test_helpers.py` (cycle-4 P1-4 — the cycle-3 plan's `test_caller_test_stem_nearest_frame_semantics` imports it but no task created it → ImportError at collection):** a tiny module whose ONLY content is the nearest-frame pin:

```python
# tests/test_helpers.py — epic #1647 cycle-4 P1-4
"""Tiny test_-prefixed shared helper pinning _caller_test_stem()'s NEAREST-frame
semantics (cycle-3 P2-18): a construction made through THIS module resolves to
"test_helpers" — the helper's own stem — never the calling file's. A shared
test_-prefixed helper must therefore NEVER be listed in TEST_NO_REDIRECT_STEMS
(its exemption would exempt every caller), and no carve-out file may import it
(a carve-out constructing through it would lose its own stem's exemption). The
P2-9 stem-registry guard (Task 5 Step 1) enforces both."""
from tortoise.projection import _caller_test_stem


def construct_via_helper() -> str:
    return _caller_test_stem()
```

```python
# Epic #1647 (D-1=A): class-level URI-aware redirect. Fires ONLY in a test
# session (TORTOISE_TEST_MODE=1, exported by conftest) with a supported
# TORTOISE_DB_URI AND a calling test frame (cycle-2 P1-1b: subprocess CLI
# children inherit the env but have no test_ module in their stack, so
# _caller_test_stem() returns None and they never redirect). Prod tools
# (backup.py, __main__.py rebuild, ingest.py, migrate_db.py, hosted_api.py,
# pipeline_cli.py) construct with explicit paths but never run under
# TEST_MODE, so they never redirect (P0-4) — and their entry points pop
# TEST_MODE at startup anyway (cycle-2 P2-10). Explicit path= only (D-1
# option a): no-arg keeps the embedded canonical path (captured via
# explicit_path above). TORTOISE_TEST_NO_REDIRECT (comma-separated
# TEST-MODULE stems) exempts carve-out files via caller frame inspection
# (P0-1) — the DB-file basename is NEVER the key.
_uri = os.environ.get("TORTOISE_DB_URI")
if (explicit_path and _uri
        and os.environ.get("TORTOISE_TEST_MODE") == "1"
        and _is_supported_uri_scheme(_uri)):
    _no_redirect = {
        s.strip() for s in
        os.environ.get("TORTOISE_TEST_NO_REDIRECT", "").split(",") if s.strip()
    }
    _stem = _caller_test_stem()  # None = no test frame (subprocess child)
    if _stem is not None and _stem not in _no_redirect:
        from urllib.parse import urlparse
        _parsed = urlparse(_uri)
        _validate_uri_scheme(_parsed.scheme)
        host = _parsed.hostname or "localhost"
        port = _parsed.port or 16379
        username = _parsed.username or None
        password = _parsed.password or None
        ssl = (_parsed.scheme == "rediss")
        # Cycle-2 P0-2 (fail-fast): refuse non-loopback hosts BEFORE the
        # first write — a typo'd/shared TORTOISE_DB_URI must never mint
        # test_* graphs on a remote server (wipe_server's refusal is too
        # late: it fires after every migrated construction already wrote).
        if not _is_loopback_host(host) \
                and os.environ.get("TORTOISE_TEST_ALLOW_REMOTE") != "1":
            raise RuntimeError(
                f"test redirect refuses non-loopback host {host!r} — "
                f"TORTOISE_DB_URI must point at a local docker (D-4); "
                f"set TORTOISE_TEST_ALLOW_REMOTE=1 to override")
        _sess = os.environ.get("TORTOISE_TEST_SESSION", "")
        if path == ":memory:":
            # P2-13: :memory: is a constant string — a path-hash would
            # collide every :memory: construction onto one shared graph;
            # embedded :memory: is fresh per construction, so derive a
            # per-construction unique test_memory_* graph.
            _graph = f"test_memory_{os.urandom(4).hex()}"
        elif graph_name.startswith(("test_", "tortoise_test")):
            # Cycle-2 P0-1b: a TEST-PREFIXED explicit name is the shared
            # opt-in — honored verbatim (test_suite_<uuid> seam, explicit
            # test_* names). Same name across sites = same server graph.
            _graph = graph_name
        else:
            # Cycle-2 P0-1b: explicit non-guard-passing names ("test", "t",
            # "team_...") derive PER-PATH names — the parity/g_consistency
            # pairs construct distinct paths with one shared explicit name
            # and must land on DISTINCT server graphs (a shared rename makes
            # the apply-vs-rebuild parity comparison a graph-vs-itself
            # vacuous pass, #942 class). Same path + same explicit name
            # shares (the embedded same-file analog). The session nonce
            # (conftest-exported TORTOISE_TEST_SESSION) keeps concurrent
            # sessions' same-path derivations distinct (cycle-2 P2-3).
            # Cycle-3 P2-5: the path-derived stem is SANITIZED to
            # [a-zA-Z0-9_] — /tmp/seam-test-b.db → seam_test_b (hyphens/
            # slashes in tmp paths must never ride into the graph name).
            # Cycle-3 P2-17: 12 hex = 48+ bits (was 8 hex = 32 bits) —
            # collision-safe at multi-thousand-graph scale.
            _stem = os.path.splitext(os.path.basename(path))[0]
            _stem = re.sub(r"[^a-zA-Z0-9_]", "_", _stem)
            _graph = (f"test_{_stem}_"
                      + hashlib.sha1((_sess + path).encode()).hexdigest()[:12])
        path = None  # fall through to the host-mode branch below
        graph_name = _graph
        # Cycle-3 P1-4 (supersedes cycle-2 P2-11): the host branch constructs
        # the RAW falkordb.FalkorDB (projection/__init__.py:380-381, verified)
        # — the guarded subclass is embedded-only (redislite cannot take
        # host=/port=). The redirect inherits that branch's lifecycle: no
        # better, no worse than today's from_uri sites, and
        # FalkorProjection.close() disconnects the pool (L1547-1568). The
        # host branch records self._host = host so host extraction never
        # touches the raw client (cycle-3 P0-1).
```

Add two module-level helpers (plan-review P0-1/P2-11):

```python
def _is_supported_uri_scheme(uri: str) -> bool:
    """True when the URI's scheme is in the shared SUPPORTED_URI_SCHEMES
    (docker/redis/rediss). Single shared predicate — the redirect and any
    URI-routing check use it (plan-review P2-11: the old plan referenced a
    non-existent _is_supported_uri_scheme; _validate_uri_scheme RAISES and
    cannot be used as a predicate)."""
    return uri.split(":", 1)[0].lower() in _SUPPORTED_URI_SCHEMES


def _caller_test_stem() -> str | None:
    """Nearest calling TEST module's file stem (plan-review P0-1).

    Walks the caller stack for the FIRST (nearest) frame whose __file__
    basename starts with "test_" — the exemption key for
    TORTOISE_TEST_NO_REDIRECT. Cycle-3 P2-18: the key is the NEAREST test_
    frame, NOT the outermost — a cross-test-module helper (e.g. a shared
    fixture in test_helpers.py called from test_config.py) resolves to the
    helper's stem, so a shared helper's exemption exempts ALL its callers
    (documented semantics; the exemption list is the caller-module list, so
    a helper used by both a carve-out and a migrated file must not be
    listed). Helpers (tests/_embedded.py, tests/_live_utils.py, conftest.py)
    are skipped by the prefix sniff, so a carve-out file constructing
    through a helper still resolves to its own stem. Returns None when no
    test module is in the stack — subprocess CLI children (test_export_cli's
    `python -m tortoise export`, redis-guard fixture scripts) and prod
    callers. None SUPPRESSES the redirect entirely (cycle-2 P1-1b): a child
    that inherits URI+TEST_MODE must keep the embedded lane, never silently
    test the server. Pinned by a cross-module unit test (a helper frame
    between the test and the construction resolves to the nearest stem)."""
    import inspect
    frame = inspect.currentframe()
    try:
        while frame is not None:
            mod = frame.f_globals.get("__file__")
            if mod:
                name = os.path.basename(mod)
                if name.startswith("test_") and name.endswith(".py"):
                    return name[:-3]
            frame = frame.f_back
        return None
    finally:
        del frame


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_loopback_host(host: str) -> bool:
    """Shared loopback predicate (cycle-2 P0-2).

    True for localhost/127.0.0.1/::1. Used by the redirect (fail-fast
    before the first write), the Task 4 session-start tripwire (fail before
    ANY test writes), and wipe_server (D-4) — one predicate so a typo'd or
    shared TORTOISE_DB_URI is refused at the earliest possible point.
    Exposed on tortoise.config as `is_loopback_uri(uri)` for conftest/CI."""
    return host in _LOOPBACK_HOSTS
```

Then make the seam fixtures URI-aware (`tests/_embedded.py` `shared_proj`, `tests/conftest.py` `sdk_factory` + `shared_embedded_db`): when `TORTOISE_DB_URI` (supported scheme) is set → construct via `FalkorProjection.from_uri(uri, graph_name=f"test_suite_{os.urandom(4).hex()}")` (shared tier) or per-test uuid names (exact-set tier); unset → today's construction unchanged. In `tests/_embedded.py`, the URI branch runs BEFORE `has_falkor()` and `has_falkor()` short-circuits to True under URI (plan-review P2-16 — the embedded probe would otherwise redirect, mint a `test` graph on the server, and misreport backend availability); `skip_if_no_falkor()` therefore returns False in URI sessions and migrated files never vacuous-return. (Note: the frame gate from cycle-2 P1-1b also protects the probe — `has_falkor()` constructs inside `tests/_embedded.py`, which has no `test_` frame, so it would not redirect even without the short-circuit; the short-circuit stays for cost.) `provision_test_user` (conftest) gets the same treatment (plan-review P1-5): its `namespace="e2e-tests"` computes the non-test graph `team_e2e-tests` shared by the whole suite — swept to a guard-passing per-test `test_e2e_<uuid>` namespace (`test_e2e_<uuid>_tortoise` via the SDK mapping, sdk.py L1115-1123). The same per-test-<uuid> pattern extends to the backup fixtures' registry/team graphs (cycle-2 P0-1a, Task 2 Step 6b). Conftest additionally exports `os.environ.setdefault("TORTOISE_TEST_SESSION", os.urandom(4).hex())` at session start — the session nonce the redirect folds into derived graph names (cycle-2 P2-3: concurrent URI sessions on one docker must never collide on a same-path derived name, and the session-end sweep keys its created-graph set off it). Prod-role entry points (`tortoise/__main__.py`, `tortoise/mcp_server.py`, backup/migrate/pipeline CLIs) `os.environ.pop("TORTOISE_TEST_MODE", None)` **inside their `if __name__ == "__main__":` blocks / subprocess-launcher paths ONLY** (cycle-3 P1-3: never at module import — conftest imports hosted_api at L337, and tests call `main()`s in-process — a module-import or bare-`main()` pop would remove TEST_MODE mid-session and silently kill the redirect → wrong-backend green, the #942 vacuity class). **hosted_api is dropped from the pop list** (verified: it has NO `main()`/`__main__` block, and its 3 `path=` constructions at 6254/8263/8357 sit inside `if getattr(proj, "_path", None):` embedded-only guards — safe without a pop). `tests/test_mcp_server.py`'s subprocess env-pop loop (L946-948) gains `TORTOISE_TEST_MODE` in the popped set (its subprocess launcher path IS a valid pop site).

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_redirect_seam.py -v`
Expected: all PASS (with a live docker on 6379 for the redirect cases).

**Step 5: Prove P1 inertness (zero behavior change)**

Run: `uv run pytest tests/test_projection.py tests/test_search_engine_gaps.py tests/test_a9_direct_edge_traversal.py -v` (URI unset AND `TORTOISE_TEST_MODE` irrelevant — the redirect needs both conditions)
Expected: green, identical to pre-P1 — the redirect never fires.

Note: on a dev machine whose `.env` sets `TORTOISE_DB_URI`, the P1 embedded-baseline runs must explicitly unset it (`TORTOISE_DB_URI= uv run pytest …`); this is the documented local-dev path until P3 inverts the default (plan-review P1-9).

Run: `uv run pytest tests/ --collect-only -q | tail -1` → `6837 tests collected` (no test-count drift).

**Step 5b: Ship E2E-1 — `tests/test_round_trip_parity.py` (cycle-2 P1-5)**

This file had no owning task in cycle 1 (E2E-1 listed it as a gate with no task creating it). It owns E2E-1 here — it directly verifies the Task 1 seam in both modes (P1-5 fix; the P1 Gate's "unit surfaces" list gains it). Create:

```python
# tests/test_round_trip_parity.py
"""E2E-1: DB-agnostic round-trip parity, docker vs embedded (epic #1647).
Parametrized over TORTOISE_DB_URI set/unset: identical create_point → search
results on the D1/D5-identical paths; D6/D8 sides assert their own lane."""
import os
import pytest

from tortoise.log import EventLog
from tortoise.projection import FalkorProjection


def _round_trip(tmp_path, point_id: str, content: str):
    log = EventLog(str(tmp_path / "events.jsonl"))
    proj = FalkorProjection(str(tmp_path / "roundtrip.db"))
    try:
        proj.apply({"event_id": "e1", "ts": "2026-01-01T00:00:00Z",
                    "type": "PointCreated", "id": point_id,
                    "content": content, "pointKind": "claim"})
        hits = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.id, n.content, n.content_hash",
            params={"id": point_id}).result_set
        return hits
    finally:
        proj.close()


# Cycle-3 P1-6: the claimed "parametrized URI set/unset" was prose, not code —
# test_round_trip_same_shape ran the SAME lane in both legs, and on a URI-set
# dev/CI lane BOTH legs ran docker (docker-vs-docker can never detect
# divergence). Real env control: the embedded leg delenv's the URI, the docker
# leg setenv's it — each leg provably runs its intended backend.
_LEGS = ["embedded", "docker"]


@pytest.mark.parametrize("leg", _LEGS)
def test_round_trip_same_shape(leg, tmp_path, monkeypatch):
    if leg == "embedded":
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_TEST_MODE", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
        monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    hits = _round_trip(tmp_path, "rt-1", "parity claim")
    assert hits and hits[0][0] == "rt-1" and hits[0][1] == "parity claim"
    assert hits[0][2]  # content_hash present in BOTH modes (D1/D5-identical)
    from tortoise.projection import FalkorProjection
    assert FalkorProjection._is_embedded is not None  # smoke: module importable
```

The URI-set leg runs against docker via the seam; the URI-unset leg is the embedded baseline. Run both locally → green (each leg provably on its own backend — cycle-3 P1-6).

**Step 6: Commit**

```bash
python3 tools/ci_selection.py --register --surface core  # cycle-2 P1-3: new test files must be manifest-listed
uv run pytest tests/test_ci_selection.py -k integrity -q  # drift gate green
uv run pytest tests/test_redirect_seam.py tests/test_round_trip_parity.py -v
uv run pytest tests/ --collect-only -q | tail -1  # 6837 + 2 new (test_redirect_seam + test_round_trip_parity)
git add tortoise/projection/__init__.py tests/_embedded.py tests/conftest.py \
    tests/test_redirect_seam.py tests/test_round_trip_parity.py config/ci-surfaces.yml
git commit -m "feat(testdb): class-level URI-aware redirect + URI-aware seam fixtures + E2E-1 round-trip parity (epic #1647 P1, P0-1b/P0-2/P1-1b/P1-5/P2-3/P2-10/P2-11)"
```

---

### Task 2: `wipe_server()` — server-mode hermeticity wipe (graph-granularity, test-prefix-filtered, non-loopback refusal)

**Intent:** Deliver the P0 hermeticity mechanism (decision D-4=A): the existing `wipe()` refuses server mode (verified, `tests/_embedded.py:56-65`), and the graph guard rejects bare `test`/`tortoise` (verified, `_assert_test_graph` L980-1003 + `'test'.startswith(('test_','tortoise_test'))` is False). Docker hermeticity = per-test wipe of **test-prefixed graphs only**, fail-closed, on loopback hosts only. Also delivers the **caller migration** (plan-review P0-2): a `_wipe_or()` dispatcher converts every existing `wipe()` caller so no migrated file raises `RuntimeError` on docker, and a session-end server sweep (plan-review P2-14).

**Acceptance:**
- `wipe_server(proj)` enumerates `list_graphs()`, wipes (DETACH DELETE) ONLY graphs starting `test_`/`tortoise_test_`; every other graph is skipped (never wiped). **Per-graph failures are collected, not swallowed** (cycle-2 P2-7): any failed DETACH re-raises as `RuntimeError` naming the graph, and a wipe-completeness unit test asserts no `test_`-prefixed graph retains nodes after the sweep.
- `wipe_server(proj)` raises `RuntimeError` when the projection's host is not loopback (`localhost`/`127.0.0.1`/`::1`, via the shared `_is_loopback_host` predicate from Task 1) — protecting a remote dev/shared server (research Q6, decision D-4).
- Existing `wipe()` unchanged: all-graphs wipe on embedded; server refusal retained.
- **Backup fixtures use guard-passing per-test graphs (cycle-2 P0-1a):** `test_backup_sweep._make_env()` seeds `registry_control_plane` + `team_team_x` — non-test-prefixed graphs on the SHARED projection that only `wipe()` (all-graphs) cleared. `_wipe_or` → `wipe_server` skips them by design (fail-closed), so leftover Team nodes survive into later tests (`test_enumerate_teams_returns_registry_ids`, `test_sweep_no_teams_is_signal_not_incident` break order-dependently). The fixtures route the registry/team graph names through a per-test config seam (Step 6): `test_registry_<uuid>` + `test_team_<uuid>_tortoise` — extending the P1-5 per-test-unique-name pattern. A docker-lane unit test proves team/registry isolation across two sequential tests.
- `_wipe_or(proj)` dispatches on `_is_embedded`: embedded → `wipe(proj)` (all-graphs, today's semantics); server → `wipe_server(proj)` (filtered, loopback-only). This is the plan-review P0-2 fix — Task 2 of the ORIGINAL plan only added `wipe_server` and no task rewired the callers, so `test_projection._shared_proj()` (per-test `_wipe()`), `test_backup_sweep` (22 calls), `test_projection_version_gate` (6), `test_analyze` (3), `test_1162_add_operator_local_svbp`, `test_github_connector` would all raise on docker. Every migrated `wipe()` caller converts to `_wipe_or()`; E2E-2's gate depends on this.
- **Session-end server sweep (plan-review P2-14 + cycle-2 P0-3/P2-1/3 + cycle-4 P2-9):** a conftest session-end autouse fixture (URI set only) drops the `test_`-prefixed graphs CREATED BY THIS SESSION — mirroring `_redislite_hygiene`'s active-suite registry + defer-to-last-suite-standing: (1) the fixture records every graph it created in a **per-session created-graph JOURNAL** (cycle-3 P1-8 — **NOT** a token-embedded name claim; cycle-4 P2-9: the old bullet said "`test_suite_<uuid>`/`test_memory_<nonce>`/derived names all embed the session token from `TORTOISE_TEST_SESSION`" — FALSE, and contradicted by P1-8's own "not invertible"/"os.urandom nonce" finding: derived names are `hash12(session+path)` (not recoverable) and nonce names use `os.urandom` (not the session token), so NO graph name carries a recoverable session token — the journal is the only source of truth); (2) it sweeps ONLY the journal's names — never another concurrent suite's live graphs; (3) the FULL leftover sweep (all `test_`-prefixed) is deferred to the LAST suite standing (an active-suite marker file, same `{pid}-{uuid8}` format + `pid=`/`start=` lines + `active_suite_markers`/`_process_start_time` helpers as `_redislite_hygiene` — cycle-4 P2-1), so two concurrent URI sessions on one docker cannot drop each other's live graphs; (4) a **session-start stale sweep** drops DEAD sessions' journaled graphs (journal present, marker absent), and (5) an **atexit fallback** (mirroring `_redislite_hygiene`) runs the sweep when the session dies abnormally before the session-end fixture.
- Unit surface green; the shared-graph tier (E2E-2) uses it.

**Files:**
- Modify: `tests/_embedded.py` (add `wipe_server` + `_wipe_or`; keep `wipe` untouched)
- Modify: `tests/conftest.py` (session-end server sweep fixture)
- Modify: `tests/test_projection.py`, `tests/test_backup_sweep.py`, `tests/test_projection_version_gate.py`, `tests/test_analyze.py`, `tests/test_1162_add_operator_local_svbp.py`, `tests/test_github_connector.py` (wipe() → `_wipe_or()` — the P0-2 caller conversion)
- Test: `tests/test_wipe_server.py` (new)

**Step 1: Write the failing tests**

```python
# tests/test_wipe_server.py
"""Unit surface: server-mode wipe_server() (epic #1647 Task 2, D-4)."""
import pytest

from tests._embedded import wipe, wipe_server


@pytest.fixture
def server_proj():
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_wipe_target")
    proj.g.query("CREATE (:Point {id:'x'})")
    yield proj
    proj.close()


def test_wipe_server_clears_only_test_prefixed(server_proj):
    proj = server_proj
    # seed a non-test graph on the same server
    proj.db.select_graph("team_control_plane").query("CREATE (:Point {id:'keep'})")
    wipe_server(proj)
    # test-prefixed graph emptied
    assert proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
    # non-test graph untouched
    assert proj.db.select_graph("team_control_plane").query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 1


def test_wipe_server_localhost_acceptance():
    # Cycle-3 P0-1 (RED-FIRST, before the refusal tests): from_uri with a
    # LOOPBACK host must WIPE, not raise. The old host extraction read
    # getattr(proj.db.connection, "host", None) — the raw falkordb client's
    # .connection is a redis.Redis with NO .host (redis-py 8.1.0: host lives
    # in connection_pool.connection_kwargs['host']), so host=None →
    # _is_loopback_host(None) → False → wipe_server refused EVERY server
    # projection including localhost, and every _wipe_or/sweep raised. With
    # the Task 1 self._host recording, this test wipes clean.
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_local_accept")
    try:
        assert proj._host == "localhost"  # recorded on the projection (P0-1)
        proj.g.query("CREATE (:Point {id:'x'})")
        wipe_server(proj)  # must NOT raise RuntimeError
        assert proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
    finally:
        proj.close()


def test_wipe_server_skips_non_test_graphs(server_proj):
    wipe_server(server_proj)
    assert server_proj.db.select_graph("registry_tortoise").query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 0  # not wiped — never created

def test_wipe_server_refuses_non_loopback(monkeypatch):
    import types
    # Cycle-4 P1-1: constructing FalkorProjection(host="db.internal...") here
    # can NEVER reach wipe_server — the host branch builds the REAL
    # falkordb.FalkorDB, whose __init__ does a LIVE round-trip at
    # construction (falkordb.py:132 Is_Sentinel(conn) → conn.info(),
    # verified in the vendored 3.x source) and raises
    # redis.exceptions.ConnectionError (not RuntimeError) BEFORE returning —
    # so pytest.raises(RuntimeError, match="loopback") never fires (and the
    # cycle-1 P2-10 skip_health_check fix only bypasses the projection's own
    # health probe, not the falkordb client's). FIX: stub the projection —
    # wipe_server reads ONLY proj._host + (past the host check) db
    # list_graphs()/select_graph(); the host check raises first here.
    proj = types.SimpleNamespace(_host="db.internal.example.com")
    with pytest.raises(RuntimeError, match="loopback"):
        wipe_server(proj)


def test_embedded_wipe_still_refuses_server_mode(server_proj):
    with pytest.raises(RuntimeError, match="EMBEDDED"):
        wipe(server_proj)  # the pre-existing refusal must survive


def test_wipe_server_completeness(server_proj, monkeypatch):
    # Cycle-2 P2-7 + cycle-3 P2-7: after wipe_server, the fixture's OWN
    # test_-prefixed graphs must retain no nodes — a silently-skipped graph
    # would break the P2 hermeticity claim. SCOPED to the fixture's graphs:
    # on a dev docker with pre-existing leftovers, a server-global assert
    # reds spuriously. list_graphs is faked to the fixture's two graphs so
    # the completeness property is tested without touching unrelated
    # graphs; the session-end stale sweep (Step 7) owns the global cleanup
    # and must run BEFORE this assert in CI order.
    created = []
    for g in ["test_ws_wipe_target", "test_ws_second_target"]:
        server_proj.db.select_graph(g).query("CREATE (:Point {id:'x'})")
        created.append(g)
    monkeypatch.setattr(server_proj.db, "list_graphs", lambda: created)
    wipe_server(server_proj)
    for g in created:
        n = server_proj.db.select_graph(g).query(
            "MATCH (n) RETURN count(n)").result_set[0][0]
        assert n == 0, f"graph {g} still has {n} nodes after wipe_server"


def test_team_registry_isolation_across_sequential_tests(server_proj, monkeypatch):
    # Cycle-2 P0-1a (docker-lane): test_backup_sweep's fixtures must be
    # isolated per test. Two sequential tests seed test_registry_<uuid> +
    # test_team_<uuid>_tortoise, wipe via _wipe_or, and must never see each
    # other's Team/Point nodes (a shared non-test graph would leak them).
    # Cycle-3 P1-1 (CONSUMPTION side): the sweep's per-team graph name comes
    # from team_graph_name() (backup_sweep.py:190, call at :514) — on docker
    # it returns deterministic team_team_x while the seam-seeded data lives
    # on test_team_<uuid>_tortoise, so _backup_team (select_graph :264)
    # would dump the EMPTY derived graph. The seam must ALSO route
    # consumption: monkeypatch tortoise.backup_sweep.team_graph_name to the
    # seam names and assert the P0 guard in _backup_team still holds.
    from tests._embedded import _wipe_or
    import tortoise.backup_sweep as bs
    fake_team_names = iter(["test_team_0_tortoise", "test_team_1_tortoise"])
    monkeypatch.setattr(
        bs, "team_graph_name", lambda registry, team_id: next(fake_team_names))
    for i in range(2):
        reg = server_proj.db.select_graph(f"test_registry_{i}")
        team = server_proj.db.select_graph(f"test_team_{i}_tortoise")
        reg.query("CREATE (:Team {id:'team_x', tier:'pro'})")
        team.query("CREATE (:Point {id:'pt-0', content:'c', pointKind:'claim'})")
        # the sweep consumes the SEAM name, never the derived team_team_x
        assert bs.team_graph_name(None, "team_x") == f"test_team_{i}_tortoise"
        assert f"test_team_{i}_tortoise".startswith(("test_", "tortoise_test")), \
            "P0 guard: _backup_team's graph must stay guard-passing"
        _wipe_or(server_proj)
        # second iteration: fresh graphs — previous iteration's teams gone
        if i == 1:
            stale = server_proj.db.select_graph("test_registry_0").query(
                "MATCH (t:Team) RETURN count(t)").result_set[0][0]
            assert stale == 0, "test 0's Team survived into test 1 (pollution)"


def test_per_test_wipe_or_touches_only_session_set(server_proj, monkeypatch):
    # Cycle-4 P0-1 (WIRING): Task 2 Step 6 converts every caller to
    # `_wipe_or(proj)` with NO scope arg. Under the cycle-3 spec that meant
    # scope=None → server-global blind wipe (re-opening the P1-7 concurrency
    # hazard + quadratic wall). This test runs the wipe EXACTLY as converted
    # and proves the default scope is the session's created-since-last-wipe
    # registry — a foreign session's graph + unrelated test_* graphs survive.
    import uuid
    from tests._embedded import _wipe_or, _JOURNAL, _WIPED_UP_TO
    foreign = f"test_foreign_{uuid.uuid4().hex[:8]}"
    foreign_g = server_proj.db.select_graph(foreign)
    foreign_g.query("CREATE (:Point {id:'foreign'})")
    unrelated = []
    for i in range(100):
        g = f"test_unrelated_{i}"
        server_proj.db.select_graph(g).query("CREATE (:Point {id:'u'})")
        unrelated.append(g)
    # session's own since-last-wipe set (the journal appender records it)
    mine = f"test_ws_{uuid.uuid4().hex[:8]}"
    server_proj.db.select_graph(mine).query("CREATE (:Point {id:'mine'})")
    _JOURNAL.append(mine)
    _wipe_or(server_proj)  # converted-style call: NO explicit scope
    assert server_proj.db.select_graph(mine).query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 0, "session's own graph wiped"
    assert foreign_g.query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 1, \
        "foreign-session graph's nodes must SURVIVE a per-test wipe"
    for g in unrelated:
        assert server_proj.db.select_graph(g).query(
            "MATCH (n) RETURN count(n)").result_set[0][0] == 1, \
            f"unrelated test_* graph {g} must survive (scope ≠ server-global)"
    _JOURNAL.clear(); _WIPED_UP_TO = 0  # reset the shared registry for later tests


def test_wipe_server_failure_is_collected(server_proj, monkeypatch):
    # Cycle-2 P2-7: a failing DETACH must re-raise, not pass silently.
    def _boom(*a, **k):
        raise RuntimeError("injected")
    monkeypatch.setattr(server_proj.db, "select_graph", _boom)
    with pytest.raises(RuntimeError, match="test_ws_wipe_target"):
        wipe_server(server_proj)
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_wipe_server.py -v`
Expected: FAIL — `wipe_server` does not exist (ImportError); all server-mode tests fail on the missing symbol, including the cycle-3 P0-1 localhost acceptance test (`test_wipe_server_localhost_acceptance` — the RED-FIRST test proving from_uri localhost + wipe_server must wipe, not raise, once implemented).

**Step 3: Implement**

```python
def wipe_server(proj, scope: set[str] | None = None, drop: bool = False) -> None:
    """Server-mode hermeticity wipe (epic #1647, D-4).

    Enumerates list_graphs() and DETACH-DELETEs ONLY graphs named
    test_/tortoise_test_* — the guard-passing test-graph family. Every other
    graph is skipped, never wiped (fail-closed: a misconfigured non-test
    graph survives untouched). Refuses non-loopback hosts: a test suite must
    never wipe a remote dev/shared server even if test-prefixed. Per-graph
    failures are collected and re-raised (cycle-2 P2-7) — a silently-passed
    failed DETACH would break the hermeticity claim. Cycle-3 P1-7: when
    scope (the session's created-set) is given, ONLY names in the scope are
    considered — a per-test wipe must never blind-wipe another concurrent
    session's live graphs; scope=None is the server-global sweep, used only
    at session-end/last-suite-standing.

    Cycle-4 P0-1 (scope DEFAULT — never None for per-test callers): the
    caller-side registry below (tests/_embedded.py `_CREATED_SINCE_LAST_WIPE`,
    populated by the journal appender) is the default scope, so a converted
    `_wipe_or(proj)` with NO explicit scope argument is STILL scoped to the
    session's created set — the cycle-3 signature added `scope=` but the
    Task 2 Step 6 conversion never wired it, so every converted caller
    passed scope=None → server-global blind wipe (re-opening the
    concurrent-session hazard + quadratic wall). `_wipe_or` resolves the
    default below; wipe_server keeps scope=None meaning "caller decided
    global" for the session-end/last-suite-standing sweep ONLY.

    Cycle-4 P1-9 (GRAPH.DELETE after DETACH): DETACH DELETE empties a graph
    but never removes it — every wiped graph accumulates in GRAPH.LIST
    forever (invisible to the orphan assert, which counts redislite
    processes). The session-end/stale sweeps (Step 7) therefore call
    `wipe_server(..., drop=True)` — DETACH first, then GRAPH.DELETE the
    journaled names so server graph-count stays bounded (E2E-7's new
    bound: server graph count < journal size + constant). Per-test wipes
    keep drop=False (graphs are reused across tests in a session).
    """
    from tortoise.projection import _is_loopback_host  # shared predicate (P0-2)
    # Cycle-3 P0-1: host extraction reads the projection, never the raw
    # client. VERIFIED BROKEN before this fix: proj.db.connection is a
    # redis.Redis (falkordb's .connection attr, falkordb.py:150) with NO
    # .host — redis-py 8.1.0 keeps host in
    # connection_pool.connection_kwargs['host'] — so the old getattr
    # returned None → _is_loopback_host(None) → False → wipe_server refused
    # EVERY server projection. Task 1 records self._host = host on the host
    # branch; the connection_kwargs fallback covers pre-seam constructions.
    host = getattr(proj, "_host", None)
    if host is None:
        _conn = getattr(proj.db, "connection", None)
        _pool = getattr(_conn, "connection_pool", None) if _conn else None
        host = (_pool.connection_kwargs.get("host") if _pool else None) \
            or getattr(_conn, "_host", None)
    if not _is_loopback_host(host):
        raise RuntimeError(
            f"wipe_server() refuses non-loopback host {host!r} — test wipes "
            f"are local-only (decision D-4)")
    failures = []
    dropped = []
    for g in proj.db.list_graphs() or []:
        if scope is not None and g not in scope:
            continue  # cycle-3 P1-7: per-test wipes touch only the session's own graphs
        if not g.startswith(("test_", "tortoise_test")):
            continue  # fail-closed: never wipe a non-test graph
        try:  # noqa: SIM105
            proj.db.select_graph(g).query("MATCH (n) DETACH DELETE n")
        except Exception as e:  # P2-7: collect + re-raise, never pass silently
            failures.append((g, e))
        else:
            if drop:
                dropped.append(g)
    if failures:
        raise RuntimeError(
            "wipe_server() failed on graph(s): " +
            "; ".join(f"{g}: {e!r}" for g, e in failures))
    for g in dropped:  # cycle-4 P1-9: DETACH-then-DELETE keeps GRAPH.LIST bounded
        try:  # noqa: SIM105
            proj.db.select_graph(g).query("GRAPH.DELETE")
        except Exception as e:
            failures.append((g, e))
    if failures:
        raise RuntimeError(
            "wipe_server() GRAPH.DELETE failed on graph(s): " +
            "; ".join(f"{g}: {e!r}" for g, e in failures))


def _created_since_last_wipe() -> set[str]:
    """The session's created-since-last-wipe set (cycle-4 P0-1/P2-10).

    Module-level in-memory registry in tests/_embedded.py: the journal
    appender (Task 2 Step 7) records every graph name the redirect/seam/
    from_uri minted, and the per-test autouse fixture advances the
    `_WIPED_UP_TO` cursor to len(_JOURNAL) after each wipe. `_wipe_or`
    defaults its scope to the slice of the journal AFTER that cursor, so a
    per-test wipe is O(created-since-last-wipe) — NOT O(whole-session)
    (cycle-4 P2-10: scoping to the session's full accumulated created-set
    is still quadratic across a long session — the honest bound is the
    since-last-wipe delta). The full `_JOURNAL` stays intact for the
    session-end sweep's source of truth.
    """
    global _WIPED_UP_TO
    return set(_JOURNAL[_WIPED_UP_TO:])


def _wipe_or(proj, scope: set[str] | None = None) -> None:
    """Mode-dispatching hermeticity wipe (plan-review P0-2).

    Embedded projection → wipe(proj) (all-graphs, today's semantics).
    Server projection → wipe_server(proj, scope=scope) (test-prefix-filtered,
    loopback-only). Every migrated file's per-test wipe converts to this so
    the P2 half-b flip never raises RuntimeError from the server-mode
    refusal. Cycle-3 P1-7: per-test callers pass the session's created-set
    as scope (wipe_server then touches ONLY that set — never another
    concurrent session's live graphs); scope=None is the server-global full
    sweep, reserved for session-end/last-suite-standing only.

    Cycle-4 P0-1 (WIRING — the cycle-3 fix was signature-only): when the
    caller passes NO scope (every Task 2 Step 6 conversion does today),
    scope DEFAULTS to `_created_since_last_wipe()` — the session's
    created-since-last-wipe registry — NEVER None. scope=None (true
    server-global) is reachable only via the explicit sentinel used by the
    session-end/last-suite-standing sweep. The per-test autouse fixture
    (Step 7 item 0a) advances the since-last-wipe cursor after each wipe so
    the next per-test wipe is O(delta), not O(session) (cycle-4 P2-10)."""
    if getattr(proj, "_is_embedded", False):
        wipe(proj)
    else:
        if scope is None:
            scope = _created_since_last_wipe()  # cycle-4 P0-1: never None by default
        wipe_server(proj, scope=scope)


# Cycle-4 P0-1/P2-3: the created-set registry + per-session journal live in
# tests/_embedded.py next to wipe/_wipe_or. The journal is append-only, one
# graph name per line, written with per-append atomicity (open/write/close
# per append — a torn write can never interleave); the tolerant reader
# parses line-by-line and truncates at the first unparseable line (a
# truncated final line from a killed writer is dropped, the rest honored).
_JOURNAL: list[str] = []
_WIPED_UP_TO = 0
```

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_wipe_server.py -v` → all PASS (docker 6379 up).

**Step 5: Prove no behavior change on embedded**

Run: `uv run pytest tests/test_projection.py -v -k "wipe"` → embedded wipe path green.

**Step 6: Convert the `wipe()` callers to `_wipe_or()` (plan-review P0-2)**

For each migrated wipe caller, swap the import/alias and the call site — `from tests._embedded import wipe` → `from tests._embedded import _wipe_or` (or alias `_wipe = _wipe_or` where the file keeps a local alias):

**Cycle-4 P0-1 note:** every converted call site stays `_wipe_or(proj)` with NO explicit scope — the scope default is wired in Step 3/7 (`_wipe_or` resolves `_created_since_last_wipe()` when the caller passes nothing, never None). The wiring-level test in Step 1 (`test_per_test_wipe_or_touches_only_session_set`) pins this: it runs a converted-style `_wipe_or(proj)` against pre-created foreign-session graphs and asserts they survive.
- `tests/test_projection.py` — `_shared_proj()` L85 (`from tests._embedded import wipe as _wipe` → `import _wipe_or as _wipe`)
- `tests/test_backup_sweep.py` — 22 call sites
- `tests/test_projection_version_gate.py` — 6 call sites
- `tests/test_analyze.py` — 3 call sites
- `tests/test_1162_add_operator_local_svbp.py`, `tests/test_github_connector.py` — 1+ call sites each

Verify embedded behavior is unchanged (URI unset → `_wipe_or` dispatches to `wipe`): `uv run pytest tests/test_backup_sweep.py tests/test_projection_version_gate.py tests/test_analyze.py tests/test_projection.py -v -k wipe` → green.

**Step 6b: Route the backup fixtures through a per-test config seam (cycle-2 P0-1a)**

`test_backup_sweep._make_env()` seeds `registry_control_plane` (L47) and `team_team_x` (L49) on the SHARED projection, relying on per-test `wipe()` (all-graphs) to clear them. On docker, `_wipe_or` → `wipe_server` skips non-`test_` graphs BY DESIGN (fail-closed) — the seeded Team/Point nodes survive into later tests and `test_enumerate_teams_returns_registry_ids` / `test_sweep_no_teams_is_signal_not_incident` break order-dependently. Fix = extend the P1-5 per-test-unique-name pattern to the backup fixtures (the chosen Good over the two alternatives — an explicit test-owned-graphs argument to `wipe_server`, or carving out test_backup_sweep — both cost more and one loses the sweep's coverage):

- Introduce a config seam in `tests/test_backup_sweep.py`: `_REGISTRY_GRAPH = f"test_registry_{os.urandom(4).hex()}"` and `_TEAM_GRAPH = f"test_team_{os.urandom(4).hex()}_tortoise"` (uuid per test, module-level is fine — per-test unique names via the uuid + `_wipe_or` clearing them each test; per-TEST uuid for exact isolation where a file has multiple independent tests).
- `_make_env()`/`_seed_team_graph()` and ALL registry/team graph sites route through the seam. **Cycle-3 P1-2: route by regex sweep, not an enumerated line list** — the cycle-2 census (L47/49/71/84/100/117/139/163/177/189/207/215/231/248/284/304/318/335/379/419) MISSED 9 sites (verified this pass): `select_graph("registry_control_plane")` at L452/475/510/533/603/606, `select_graph("team_team_x")` at L538, `select_graph("registry_control_plane")` at L744, and the `team_graph_name(reg, "team_x") == "team_team_x"` assertion at L745 — 20 of 29 sites total. Sweep commands: `grep -n 'select_graph("registry_control_plane")\|select_graph("team_team_x")' tests/test_backup_sweep.py` + `grep -n 'team_graph_name' tests/test_backup_sweep.py` (plus the dynamic `select_graph(f"team_{team_id}")` at L57 and `team_team_e` L233 / `team_myapp` L770/860 / `team_beta` L816 sites — every non-test graph name in the file routes through the seam). The `manifest["graph_name"] == "team_team_x"` assertion (L745) follows the seam.
- **Consumption side (cycle-3 P1-1 + cycle-4 P1-5 — completes cycle-2 P0-1a):** the seam routes only the SEED; the sweep's per-team graph name still comes from `team_graph_name()` (backup_sweep.py:190, called at :514) → deterministic `team_team_x`, so on docker `_backup_team` (select_graph at :264) dumps the EMPTY derived graph while the seeded data sits on `test_team_<uuid>_tortoise` → content assertions fail (test_backup_sweep rides half b at P2 — this is a P2 blocker, not a P3 afterthought). **Cycle-4 P1-5: the cycle-3 fix covered only 2 of ~17 registry-mode sweep tests** — `test_team_registry_isolation_across_sequential_tests` + `test_sweep_consumes_seam_team_graph` monkeypatch `tortoise.backup_sweep.team_graph_name` individually, but the OTHER ~15 registry-mode tests (verified this pass: 22 of 27 `run_backup_sweep(` call sites ride registry mode — test_sweep_enum_delta*, test_sweep_backs_up_team_and_writes_state, test_sweep_size_guard*, test_sweep_data_loss*, test_sweep_steady_zero, test_sweep_p0_guard, test_team_sweep_*, test_sweep_uses/missing_registry_stream_key, test_sweep_per_label_drift*) consume `team_team_x` via the REAL function and would dump an EMPTY graph on docker → red. FIX — **route consumption FILE-WIDE**: an autouse module-scoped fixture in `tests/test_backup_sweep.py` monkeypatches `tortoise.backup_sweep.team_graph_name` to the seam constants (`test_team_<uuid>_tortoise`) for EVERY sweep test, matching the seeded seam names. The patch CANNOT live in `_make_env` (its monkeypatch arg is passed `None` at L162 today — verified — and only some tests call `_make_env`); the autouse fixture patches at the MODULE level so seed AND consumption agree file-wide. **L745 contradiction resolved explicitly:** `test_team_graph_name_reads_from_teams` (L732-746) tests the REAL function's registry branch (`team_graph_name(reg, "team_x") == "team_team_x"`) — under the file-wide monkeypatch it would assert against the patched stub and become vacuous. The autouse fixture therefore SKIPS patching for that one test (`if request.node.name == "test_team_graph_name_reads_from_teams": return`), so the real-function semantics stay covered — the fixture documents this exemption inline. Concretely: the docker-lane proof adds a `test_sweep_consumes_seam_team_graph` that runs `backup_sweep.backup_teams(...)` against a fake registry + the autouse seam and asserts the dump reads the seeded seam graph (content present), never `team_team_x`; `test_team_registry_isolation_across_sequential_tests` (Step 1) keeps its per-test monkeypatch (it needs per-iteration seam names) and asserts the P0 guard in `_backup_team` still holds (the consumed name is `test_`-prefixed — `_assert_test_graph` passes).
- `_wipe_or(proj)` (converted in Step 6) clears them per test; the docker-lane isolation proof is `test_team_registry_isolation_across_sequential_tests` (Step 1).
- Embedded lane: the seam names only matter on the server; embedded behavior (all-graphs `wipe`) is unchanged.

Verify: `uv run pytest tests/test_backup_sweep.py -v` (embedded) → green; URI-set docker run of the file → green with zero cross-test pollution.

**Step 7: Add the session-scoped server sweep + created-graph journal + stale-sweep + atexit fallback (plan-review P2-14 + cycle-2 P0-3/P2-1/3 + cycle-3 P1-7/P1-8/P2-13)**

Conftest session fixtures (URI set only), mirroring `_redislite_hygiene`'s active-suite registry + defer-to-last-suite-standing (conftest L151-307):

0. **Per-test wipes are session-scoped (cycle-3 P1-7 — the #1 concurrency hazard):** as written, `wipe_server` is a server-global blind wipe — it enumerates `list_graphs()` and DETACHes EVERY `test_`/`tortoise_test_` graph. Two concurrent pytest processes on one docker: session A's per-test `_wipe_or` deletes session B's LIVE `test_suite_<uuid>` graphs mid-suite; and mid-session each per-test wipe re-enumerates + DETACHes the entire session's accumulated graphs (thousands) — a quadratic wall driver. FIX: per-test wipes touch ONLY the session's created set (`_wipe_or` passes the scope; the server-global full sweep is reserved for session-end/last-suite-standing ONLY). **Cycle-4 P2-10 — the quadratic claim is STATED HONESTLY:** scoping to the session's FULL accumulated created-set is still O(session) per wipe → O(session²) across the session. The honest bound is the **created-since-last-wipe delta** (`_created_since_last_wipe()`, Step 3 — the `_WIPED_UP_TO` cursor slices the journal after the previous wipe), so each per-test wipe is O(delta) and the session total is O(created) amortized. Cost-bound test: pre-create ~2000 `test_*` graphs (fake `list_graphs`), time a scoped `_wipe_or`, assert it is bounded (touches only the since-last-wipe slice — never O(server-graphs), never O(whole-session)).
0a. **The WIRING (cycle-4 P0-1 — the cycle-3 signature was never wired):** Task 2 Step 6 converts 30+ call sites to `_wipe_or(proj)` with NO scope arg — under the cycle-3 spec that means scope=None → server-global blind wipe → the exact hazard P1-7 was meant to close, and the cost-bound test exercised the primitive but NOT the wiring. FIX — three pieces, all in `tests/_embedded.py` + `tests/conftest.py`:
    - a **module-level in-memory created-set registry** (`_JOURNAL` + `_WIPED_UP_TO`, Step 3) populated by the journal appender — every graph the redirect/seam/from_uri mints is appended at the construction seam;
    - an **autouse per-test fixture** (conftest, URI set only) that snapshots the "created since last wipe" slice into the registry cursor and advances `_WIPED_UP_TO = len(_JOURNAL)` after each per-test wipe;
    - **`_wipe_or` defaults its scope to the registry — NEVER None** (`scope = _created_since_last_wipe()` when the caller passes nothing; the true server-global sweep is reachable only through the session-end/last-suite-standing sentinel).
    Wiring-level test (in `tests/test_wipe_server.py`): pre-create a FOREIGN-session graph `test_foreign_<uuid>` + 100 unrelated `test_*` graphs on the server, then run a converted per-test `_wipe_or(proj)` (as Task 2 Step 6 converts it — no explicit scope); assert the foreign graph's nodes SURVIVE and only the session's since-last-wipe set was touched. This is the test that would have red every converted caller under the cycle-3 spec.
1. **Session-start (autouse, before any test):** (a) a stale sweep reads DEAD sessions' **journals** (below) and drops exactly the graphs they recorded — never a live suite's graphs (live = marker present in ACTIVE_SUITES_DIR); (b) registers THIS session's token (from `TORTOISE_TEST_SESSION`) as an active suite. **Cycle-4 P2-1 — the docker marker REUSES the embedded format:** `token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"` (verified conftest L178), marker file written with `pid=`/`start=` lines (conftest L188-192), and the same `tortoise.embedded_reaper` helpers (`active_suite_markers`, `_process_start_time`, `ACTIVE_SUITES_DIR`) — so `active_suite_markers()`' liveness verification (recycled-pid guard, #1642 FIX 5) applies to docker sessions too, and the last-suite-standing coordination is literally shared code, not a parallel implementation. Pin with a parser test: a docker-marker file written by the conftest fixture parses through `active_suite_markers()` with the same (pid, start) identity checks as an embedded marker.
2. **Created-graph journal (cycle-3 P1-8 + cycle-4 P2-3/P2-2 — replaces the token-matching claim):** no graph name embeds a recoverable session token (derived = `hash12(session+path)`, not invertible; `test_memory_<nonce>`/`test_suite_<uuid>` use `os.urandom`, not `TORTOISE_TEST_SESSION`), so token-matching `list_graphs()` is IMPOSSIBLE and the created-set has no recording mechanism. FIX: the redirect + seam fixtures APPEND each derived/verbatim graph name to a per-session journal — a token-named file in `ACTIVE_SUITES_DIR` (e.g. `{ACTIVE_SUITES_DIR}/{TORTOISE_TEST_SESSION}.graphs.jsonl`, same directory as the embedded markers so the last-suite-standing coordination is shared). The session-end sweep drops exactly the journal's names; the stale sweep reads dead sessions' journals (marker file absent → dead → drop its journaled graphs, then remove the journal). Drop the "recoverable by token-matching" claim entirely — the journal is the source of truth. **Cycle-4 P2-3 — torn-writes + threading are SPECIFIED:** each append is `open(path, "a"); fh.write(name + "\n"); fh.close()` — open/write/close per append (a killed writer can never interleave a half-line into another append; no cross-append lock needed in the single-process session, and the per-append open is the atomicity boundary against concurrent sessions appending to DIFFERENT token-named files). The READER is tolerant: parse line-by-line, stop at the first unparseable line (a truncated final line from a killed writer is dropped, all prior lines honored), and if even the first line is unparseable, treat the journal as empty and delete it (poison-file guard, mirroring the marker hygiene). **Cycle-4 P2-2 — `from_uri` bypasses the journal, and the URI-default graph is shared across sessions:** `from_uri(uri)` without an explicit `graph_name` resolves the URI path (`/tortoise_test_matrix` under the P2 job URI) — a SHARED graph every session uses; a per-session DETACH of it (via a per-test scope that includes it) races other sessions' live writes. FIX: (a) in test mode (`TORTOISE_TEST_MODE=1`, conftest-only), `FalkorProjection.from_uri()` appends its resolved graph name (URI-path default OR explicit) to the session journal — the single seam point, so every from_uri-minted graph is owned by its session's journal; (b) the per-test wipe scope EXCLUDES the shared URI-default graph (it is swept only at session-end by the last suite standing — the same coordination as everything else); (c) the from_uri census is enumerated for the P2 divergence pass: test_embedded_concurrency `test_live_mw_tortoise` (×3), the Task 4 tripwire probe (`test_tripwire_probe`), test_namespace_uri_mode (`tortoise_test_221_namespace`), and the URI-default `from_uri(os.environ["TORTOISE_DB_URI"])` sites (test_search_engine L303/304+L413/414+L497 — already fixed by the path-ed job URI, P1-2). Test: a session whose journal contains `tortoise_test_matrix` never DETACHes it in a per-test wipe, and the session-end/last-suite-standing sweep owns it.
3. **Session-end (autouse):** sweep the session's OWN journaled graphs. **Never** drop another live suite's graphs: if other active-suite markers exist, this session sweeps only its own journal; the FULL leftover sweep (all `test_`-prefixed via `list_graphs()`) is deferred to the last suite standing (same marker/countdown as `_redislite_hygiene`). **Cycle-4 P1-9 — DETACH-only wipes grow GRAPH.LIST unboundedly:** every wipe is DETACH DELETE (empties a graph, never removes it); a persistent dev docker accumulates every derived graph forever — invisible to E2E-7's orphan assert, which counts redislite PROCESSES, not server graphs. FIX: session-end + stale sweeps call `wipe_server(..., drop=True)` (DETACH first, then GRAPH.DELETE the journaled names — Step 3); per-test wipes keep drop=False (graphs are reused across tests in a session). E2E-7 gains a server-side bound: `GRAPH.LIST` count < journal size + constant (P1-9, catalog row below). **Cycle-4 P1-8 — sweeps SKIP on non-loopback, they do NOT raise:** a `TORTOISE_TEST_ALLOW_REMOTE=1` session redirects+writes to a remote server (write-only escape, P2-6) — if the session-end/stale/atexit sweeps "share the loopback refusal with wipe_server" (raise), every ALLOW_REMOTE session fails at TEARDOWN, after its tests passed. FIX: the sweep helpers take `skip_on_non_loopback=True` — log-and-continue (no graphs touched, journal preserved for the next session); D-4's `RuntimeError` refusal remains ONLY for explicit `wipe_server()` calls. New test: a 1-test ALLOW_REMOTE session (stub projection, fake journal) ends GREEN — teardown logs the skip instead of raising.
4. **atexit fallback:** register `_atexit_cleanup` (mirroring conftest L254-276) so an abnormal process exit (watchdog kill, crash) still records the session as dead — the NEXT session's stale sweep reads its journal and drops its graphs. Same non-loopback skip semantics as item 3 (P1-8).
5. Loopback + `test_`-prefix filter shared with `wipe_server` (common helper); non-test graphs never touched. **Split semantics (cycle-4 P1-8):** the shared helper raises on non-loopback for EXPLICIT `wipe_server()` calls (D-4) and logs-and-skips for the sweep paths — the same predicate, different failure policy, both pinned by tests.

Verify (cycle-3 P2-13 + cycle-4 P2-3/P1-8 — unit, not just manual two-process): **`test_concurrent_suite_end_sweep_leaves_other_suite_graphs`** with a FAKE `list_graphs`/marker dir: suite A's end-sweep (journal A) must leave suite B's live graphs (marker B present) untouched; with B's marker removed (B crashed), A's stale sweep drops B's journaled graphs. **`test_journal_tolerant_reader_truncated_line` (P2-3):** a journal whose last line is a truncated half-name (simulated torn write) parses to the complete prefix lines only, and the stale sweep drops exactly those; a journal whose FIRST line is unparseable is treated as empty and deleted (poison guard). **`test_allow_remote_session_teardown_green` (P1-8):** the ALLOW_REMOTE session above. The manual two-process check stays as the integration confirmation.

**Step 8: Commit**

```bash
python3 tools/ci_selection.py --register --surface core  # cycle-2 P1-3: test_wipe_server.py
uv run pytest tests/test_ci_selection.py -k integrity -q
git add tests/_embedded.py tests/conftest.py tests/test_wipe_server.py \
    tests/test_projection.py tests/test_backup_sweep.py \
    tests/test_projection_version_gate.py tests/test_analyze.py \
    tests/test_1162_add_operator_local_svbp.py tests/test_github_connector.py config/ci-surfaces.yml
git commit -m "feat(testdb): wipe_server() + _wipe_or() caller migration + session-scoped sweep + backup-fixture seam (epic #1647 P1, D-4/P0-1a/P0-2/P0-3/P2-1/P2-3/P2-7/P2-14)"
```

---

### Task 3: Skip-guard inversion — coverage manifest + `test_workflow_keeps_rs` / `test_missing_log_is_not_a_failure` flips

**Intent:** Kill the vacuous-pass class (#942) at 6,837-test scale: on migrated halves, any expected nodeid missing from the run's observed set must go red. **P0-3 reconciliation:** the ORIGINAL plan generated the manifest (expected nodeids) from `--collect-only` full nodeids but observed skips from `-r fEs` file:line summaries — the two formats NEVER match, so the 3 `embedded_only` marker skips guaranteed P2 red every run, and the plan's own unit test masked the mismatch by writing file:line into the manifest. Fix: **junitxml is the authoritative observed PASSED/SKIPPED+reason source** — lossless per-testcase nodeid (via `file`+`classname`+`name` reconstruction, verified on the real repo format with `junit_family=xunit1`) and lossless skip reason (`<skipped message>`). Builds the mechanism in P1 (dormant — no manifest passed); wires it to half b in P2 (Task 6).

**Acceptance:**
- `tools/skip-guard.py` gains a manifest mode: `--manifest <expected-nodeids.txt>` + `--junitxml <path>` — every expected nodeid must appear as a junitxml testcase (passed or skipped); a missing nodeid → exit 1.
- Observed-set source (scope-review M2, plan-review P0-3): **junitxml** (`--junitxml=/tmp/junit.xml`, `-o junit_family=xunit1` for the `file`/`line` attributes). Nodeid reconstruction: `file` + `::` + (classname minus its module-dotted prefix) + `::` + `name` — verified against the real repo format: `classname="tests.test_skip_guard.TestGuardAcceptsCleanLog"` + `file="tests/test_skip_guard.py"` + `name="test_no_skips_at_all"` → `tests/test_skip_guard.py::TestGuardAcceptsCleanLog::test_no_skips_at_all`; module-level tests have classname == module-dotted prefix → `file::name`. Parametrized ids ride in `name` (`test_y[1]`).
- The `-v` progress lines are NOT a source for the manifest (their 80-col truncation behavior is terminal-width-dependent; junitxml is deterministic). The `-r fEs` summary stays for human-readable logs only. The existing `FalkorDB`-reason matcher reads `<skipped message="...">` from junitxml (lossless, never truncated) instead of log lines; the junitxml reader uses **`xml.etree.ElementTree`** — never regex — so entity-escaped ids (`&quot;`/`&lt;`) in `name`/`classname` are decoded correctly (cycle-2 P2-2/4, pinned by an escaped-id unit fixture). The `_live_utils.py` exclusion is **re-keyed to the reason-family prefix** (`"requires TORTOISE_DB_URI"`) instead of the source-file location (cycle-2 P2-13: under junitxml the skip's `file` attribute is the CALLING test file, so the location-based exclusion cannot survive); carve-out files' redislite-reasoned skips never match the `FalkorDB` substring.
- `test_missing_log_is_not_a_failure` (tests/test_skip_guard.py) flips: with `--manifest` passed, a missing/unreadable junitxml = every expected nodeid absent → **red** (was exit 0). **Cycle-3 P2-14: an ABSENT/UNREADABLE manifest file itself is also red when `--manifest` is passed** — a vanished manifest must never vacuously green (the vacuous-early-return class); the no-manifest path keeps exit 0 (back-compat for the current invocation).
- `test_workflow_keeps_rs` stays green and gains junitxml assertions (the CI fast-suite invocation must pass `--junitxml` + `-o junit_family=xunit1`; `-r fEs` remains for the human summary).

**Files:**
- Modify: `tools/skip-guard.py`
- Modify: `tests/test_skip_guard.py` (`test_missing_log_is_not_a_failure` flip; `test_workflow_keeps_rs` extension; new manifest cases: missing-nodeid red, PASSED satisfies, reasoned-skip satisfies, carve-out exemption, vacuous early-return)
- Test: `tests/test_skip_guard.py` (extended)

**Step 1: Write the failing tests** (append to `tests/test_skip_guard.py`; fixtures are REAL pytest junitxml output, plan-review P0-3 — not the old file:line fake. The existing `run_guard(log_text)` helper stays byte-for-byte — cycle-3 P2-10: **10** existing call sites (verified this pass: `def run_guard(` at L55 + 10 callers; the plan's "12" counted the def) depend on its single-arg signature; add a NEW `run_guard_with_manifest(log_path, manifest=None, junit=None)` helper that passes the new `--manifest`/`--junitxml` args)

```python
# REAL junitxml format (pytest 9.1.1, -o junit_family=xunit1 — verified):
JUNIT_PASSED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="2">
<testcase classname="tests.test_ep_directional.TestX" name="test_y" file="tests/test_ep_directional.py" line="35" time="0.001" />
<testcase classname="tests.test_projection" name="test_something" file="tests/test_projection.py" line="88" time="0.001" />
</testsuite></testsuites>'''
JUNIT_SKIPPED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="1">
<testcase classname="tests.test_embedded_lifecycle_fast_close" name="test_ephemeral_nosave" file="tests/test_embedded_lifecycle_fast_close.py" line="30" time="0.001"><skipped type="pytest.skip" message="redislite unavailable">/tests/test_embedded_lifecycle_fast_close.py:30: redislite unavailable</skipped></testcase>
</testsuite></testsuites>'''
# Cycle-2 P2-2/4: junitxml entity-escapes ids (&quot; / &lt;). The reader must
# use xml.etree.ElementTree — a regex reader mangles these nodeids and the
# manifest reconciliation silently misses them.
JUNIT_ESCAPED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="1">
<testcase classname="tests.test_api" name="test_arg_&quot;weird&quot;_&lt;x&gt;" file="tests/test_api.py" line="41" time="0.001" />
</testsuite></testsuites>'''


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


def test_manifest_missing_nodeid_is_red(tmp_path):
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n"
                      "tests/test_projection.py::test_something\n"
                      "tests/test_vanished.py::test_never_ran\n")
    # test_vanished absent from the junitxml testcases (deselected / file
    # dropped from $FILES / early-return with no skip) → red
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 1  # fail-closed — vacuous early-return detected


def test_manifest_escaped_ids_parse(tmp_path):
    # Cycle-2 P2-2/4: an escaped nodeid (&quot; / &lt;) in the junitxml must
    # round-trip through ElementTree and satisfy its manifest entry.
    junit = _write(tmp_path, "junit.xml", JUNIT_ESCAPED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_api.py::test_arg_\"weird\"_<x>\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0


def test_manifest_passed_nodeid_satisfies(tmp_path):
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n"
                      "tests/test_projection.py::test_something\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0


def test_manifest_reasoned_skip_satisfies(tmp_path):
    # A reasoned skip (junxml <skipped>) is an OBSERVED testcase — it
    # satisfies the manifest (the marker-skips in Task 5 must never go red).
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_embedded_lifecycle_fast_close.py::test_ephemeral_nosave\n")
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0  # reasoned skip ≠ vanished nodeid (and no FalkorDB substring)


def test_missing_junitxml_with_manifest_is_red(tmp_path):
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n")
    rc = run_guard_with_manifest(str(tmp_path / "no-such.log"), manifest,
                                 junit=str(tmp_path / "no-such.xml"))
    assert rc == 1  # FLIPPED from the historical exit 0


def test_missing_junitxml_without_manifest_stays_green(tmp_path):
    rc = run_guard(str(tmp_path / "no-such.log"))
    assert rc == 0  # back-compat: no manifest, no evidence


def test_missing_manifest_file_is_red(tmp_path):
    # Cycle-3 P2-14: --manifest passed but the manifest FILE is absent/
    # unreadable → red with an actionable message (a vanished manifest must
    # never vacuous-green — the expected-set is then unknowable, which is
    # itself the failure).
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"),
                                 manifest=str(tmp_path / "no-such-manifest.txt"),
                                 junit=str(tmp_path / "junit.xml"))
    assert rc == 1


def test_falkordb_reason_skip_from_junitxml_is_red(tmp_path):
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "Live FalkorDB (Docker) not available"))
    rc = run_guard_with_manifest(str(tmp_path / "pytest.log"), junit=junit)
    assert rc == 1  # the historical matcher, now reading junitxml reasons
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_skip_guard.py -v`
Expected: the new junitxml-manifest tests FAIL (no `--manifest`/`--junitxml` mode yet); the existing `test_missing_log_is_not_a_failure` still passes (not yet flipped).

**Step 3: Implement**

- Add `--manifest <path>` and `--junitxml <path>` args to `tools/skip-guard.py` (the existing single positional `<pytest.log>` stays for back-compat).
- Parse expected nodeids from the manifest (one per line, `#`-comments allowed).
- Parse the junitxml with **`xml.etree.ElementTree`** (cycle-2 P2-2/4 — entity-escaped ids `&quot;`/`&lt;` are decoded automatically; a regex reader mangles them and silently misses nodeids): per `<testcase>`, reconstruct the nodeid as `file + "::" + (classname minus its module-dotted prefix) + "::" + name` (module-dotted prefix = `file` with `/`→`.` and `.py` stripped; module-level tests have classname == prefix → `file::name`). A `<skipped>` child marks the testcase skipped and carries the lossless reason in `message`.
- Observed set = all junitxml testcase nodeids (passed AND skipped-with-reason). An expected nodeid absent from the observed set → violation (print + exit 1).
- FalkorDB-reason check: any `<skipped message>` containing `FalkorDB`, EXCLUDING reasons whose family prefix is `"requires TORTOISE_DB_URI"` (cycle-2 P2-13 re-key: `_live_utils._skip_unless_live_uri` is the INTENTIONAL visible URI-gate; its junitxml `file` attribute is the CALLING test file — `tests/test_foo.py` — so the old location-based `_live_utils.py` exclusion cannot survive junitxml; the reason-family prefix does) → violation (exit 1).
- When `--manifest` is absent: current behavior (missing junitxml/log → exit 0).
- When `--manifest` present and the junitxml is missing/unreadable → every expected nodeid absent → exit 1 (the flip).
- **Cycle-3 P2-14:** when `--manifest` is passed but the MANIFEST FILE is absent/unreadable → exit 1 with an actionable message (a vanished manifest must not vacuous-green; the expected-set is then unknowable, which is itself the failure). New unit case: `test_manifest_file_missing_is_red` (`run_guard_with_manifest(log, manifest="/no-such-manifest", junit=junit)` → rc 1).
- **Format contract (plan-review P0-3/P1-7):** the manifest generator and the pytest run must use the SAME rootdir-relative `$FILES` (CI invokes `tests/$f.py`) so `--collect-only` nodeids and junitxml `file` attributes agree; the pytest invocation passes `--junitxml=/tmp/junit.xml -o junit_family=xunit1`.

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_skip_guard.py -v` → all PASS (including the flipped `test_missing_log_is_not_a_failure`, updated to call `run_guard_with_manifest` with `--manifest`/`--junitxml` — the single-arg `run_guard` and its 10 existing call sites untouched, cycle-2 P2-5/cycle-3 P2-10). Then confirm the real-format contract end-to-end: `uv run pytest tests/test_skip_guard.py --junitxml=/tmp/junit.xml -o junit_family=xunit1 -q && python3 tools/skip-guard.py /tmp/pytest.log --junitxml=/tmp/junit.xml --manifest <collect-only-nodeids>` → exit 0.

**Step 5: Commit**

```bash
git add tools/skip-guard.py tests/test_skip_guard.py
git commit -m "feat(testdb): skip-guard coverage manifest reconciled against junitxml — vanished nodeids go red (epic #1647 P1, P0-3)"
```

---

### Task 7: Graph-name sweep — explicit non-test graph names + no-namespace SDK bulk-wipers + derived-name verification

**Intent:** Make every migrated construction docker-safe BEFORE the flip. The class-level redirect (Task 1) auto-derives guard-passing names for the 114 no-graph-name `FalkorProjection` sites (measured this plan-pass; scope-brief's "93" counted a narrower site class). The sweep's real scope is the **explicit non-test graph names** + the no-namespace `TortoiseSDK` constructions whose computed graph is bare `tortoise` (which fails the guard on bulk-wipe) + a red-herring check that no bulk-wipe test still targets `test`/`tortoise`.

**Acceptance:**
- All explicit non-test graph names in migrated files are accounted for (plan-review P1-4 corrected census — verified by grep this pass) and become guard-passing on the server via the **cycle-2 P0-1b per-path derivation** — NOT a blanket rename to one shared `test_<file>_suite` graph (that would collapse test_projection's 21 per-call-unique-path constructions onto one graph and make the apply-vs-rebuild parity test compare a graph to itself — vacuous, #942 class):
  - `graph_name="test"` construction sites: **35 across 6 files** (the old plan's "9" was wrong): test_projection.py **21** (L82/888/910/937/965/1195/1400/1417/1866/1867/1981/2076/2243/2245/2299/2370/2387/2420/2466/2532/2786), test_supplementary.py 2, test_extractor_priors.py 2, test_embedded_lifecycle.py 4 + test_embedded_lifecycle_fast_close.py 4 (carve-out — untouched), tests/_embedded.py 2 (seam-internal — handled by Task 1's URI branch, not renamed). **Fate:** on the server the redirect derives `test_<file-stem_sanitized>_<hash12(session+path)>` per site (distinct paths → distinct graphs — the parity/g_consistency pairs stay isolated; same path → shared, the embedded same-file analog; cycle-3 P2-5/P2-17). Embedded lane unchanged (`graph_name="test"` stays). Explicit rename to a shared name is the per-site opt-in ONLY where a test genuinely needs one deterministic server graph across DISTINCT paths — then it uses an already-guard-passing name (`test_<file>_suite`), honored verbatim by the redirect.
  - `graph_name="tortoise"` construction sites: **2** — test_projection.py:2162, test_remove_context_migration.py:333. (The many `dump_graph(..., graph_name="tortoise")`/`create_backup(..., graph_name="tortoise")` call sites in test_hosted_backup.py/test_export_cli.py are FUNCTION arguments, not projections; test_hosted_backup is carve-out.) Default-name → redirect-derived (same rule).
  - `graph_name="crash_live"`: test_import_endpoint.py:724 (**1**) → non-guard-passing explicit → redirect-derived.
  - **Cycle-2 P1-4 additions to the census** (explicit non-test names/namespaces the cycle-1 sweep missed): test_pack_state.py L306 `graph_name="team_team-k"` (lock-keying arg) + `namespace="team-a"` (L62/73, half-b P2 — SDK maps it to the non-test graph `team_team-a_tortoise`); test_invites_http.py `namespace="registry"` (L103, half-b P2 — `registry_tortoise` shared by 36 tests); test_m1.py `graph_name="t"` (L99/118, half-a P3). **Cycle-3 P2-9 census completion (VERIFIED this pass):** test_pack_state's namespace sites are `namespace="team-c"` at **L88** and `namespace="team-a"` at **L118/126** (in addition to L62/73 — team-a total: L62/73/118/126/133/143/159/167). All route to per-test `test_*` namespaces (Step 5); per-path derivation isolates the `team_team-k` lock-keying arg anyway, but the census list must be complete.
- **Net explicit renames: ZERO for the derived-name sites** (the redirect handles them — no rename needed, no embedded assertion churn); explicit renames only for the P1-4 namespaces + any site choosing the shared opt-in.
- **g_rebuild*/g_consistency/parity classification (plan-review P1-4):** the test_projection `g_rebuild*` (L888/910/937/965/1981/2786), `g_consistency*` (L1400/1417) and apply-vs-rebuild parity (L1866/1867) tests MIGRATE with a name-only sweep — verified by inspection: they call `rebuild()`/`rebuild_all()`/`check_consistency()`, DB-agnostic apply-path operations (JSONL → graph) that run identically on the server; they do NOT exercise the D2/D3 `_auto_health_recover`/`recover_from_log` branches (those live in test_ops_safety — carve-out). No mode-split needed.
- The sweep extends to `graph_name="test"`/`"tortoise"`/`"crash_live"` in ALL migrated files (not just the wipe/assert files) — every migrated construction must be guard-passing before the flip; with the cycle-2 P0-1b derivation this holds automatically on the server lane.
- Seam default graph name is `test_suite_<uuid>` in docker mode (Task 1) — never bare `test`.
- No-namespace `TortoiseSDK(db_path)` constructions in migrated files that bulk-wipe or assert exact sets gain a `namespace="test_<file>_..."` (the SDK maps `test_<ns>` → `test_<ns>_tortoise`, sdk.py L1115-1123 — verified). **Cycle-3 P1-5 + cycle-4 P1-6 — the no-namespace share is REAL and is fixed AT THE SDK LAYER (the OR is RESOLVED — SDK-layer, not per-file enumeration):** `TortoiseSDK._get_proj()` (sdk.py L1115-1123, VERIFIED) with URI set IGNORES `db_path` and resolves `urlparse(uri).path or "tortoise"` for no-namespace constructions — under the P2 job URI (`/tortoise_test_matrix`) EVERY no-namespace `TortoiseSDK(db_path=...)` in the half shares ONE graph. test_projection has 5 such sites (L2043/2135/2321/2346/2507 — VERIFIED, distinct `_tmp` paths) whose exact-set assertions get cross-test data. FIX (chosen Good): give no-namespace SDK constructions a **per-session default namespace** gated on the SAME test-session signal as the redirect: `db_path is not None and TORTOISE_TEST_MODE=1 and no namespace` → derive `test_sdk_<hash12(session + db_path)>`; otherwise today's semantics unchanged (URI-path graph / `tortoise` default). Gating on `db_path is not None` is mandatory — `TortoiseSDK()` with no db_path is exactly the URI-graph case (`test_namespace_uri_mode`'s two no-namespace tests, below) and must keep resolving the URI's own graph. **Cycle-4 P0-2 — the hash input is the VALUE, never `id()`:** the cycle-3 plan wrote `hash12(session + id(db_path))` — `id()` is the CPython MEMORY ADDRESS: (a) test 1's SDK is GC'd, test 2's construction reuses the freed address → SAME hash → SAME graph → cross-test pollution (the exact hazard the derivation exists to prevent); (b) two same-value/different-instance db_paths (distinct str objects, equal value) → DIFFERENT hashes → DIFFERENT graphs → write-then-read-stale within one test (the same-path share breaks). FIX: hash the string VALUE — `test_sdk_<hash12(session + db_path)>` — distinct values → distinct graphs, same value → shared (the correct embedded analog). Add BOTH unit tests: (a) two SEQUENTIAL no-namespace SDKs with distinct db_paths + `gc.collect()` between → DISTINCT graphs (proves the GC/reuse scenario cannot collapse them); (b) two constructions of the SAME path VALUE (fresh `str` each, e.g. `str(Path(x))` built twice) → SAME graph (proves same-value sharing). **Expectation splits for `test_namespace_uri_mode` (P1-6):** its two no-namespace tests construct `TortoiseSDK()` with NO db_path — `test_no_namespace_uses_uri_graph` asserts the URI-path graph (`tortoise_test_221_namespace`), `test_uri_without_path_defaults_to_tortoise` asserts the `tortoise` default — an UNCONDITIONAL SDK-layer default would red both (the cycle-3 prose was unconditional). With the db_path gate they stay green as-is; add ONE docker-session expectation to the file: a no-namespace SDK WITH a db_path under URI derives `test_sdk_<hash12>` (assert the `test_sdk_` prefix + 12 hex + `_is_embedded is False`), documenting the two semantics side by side. **Census completed (P1-6 + P2-7):** `grep -rn "TortoiseSDK(" tests/ --include="*.py"` — the no-namespace sites are ~25 across ~20 files (crude same-line grep count this pass — the reviewer's per-file figures (test_suggest_entry_points ×11, test_ep_operatorless ×4, test_mcp_server ×2) and this pass's same-line counts differ slightly because multi-line calls land differently under each counting method; the aggregate ~25/~20 magnitude agrees, and Task 7 Step 1's census grep is the authoritative enumeration to implement against; the cycle-3 plan named only test_projection's 5): test_suggest_entry_points (10), test_ep_operatorless (5), test_mcp_server (5), test_references_edge (10), test_integration_search (13), test_de2e1_entity_extraction (8), test_battery_setup (7), test_search_engine (7), test_pack_state (6), test_projection (5), test_ep_draft_filter (5), test_index_cli (5), test_embedded_lifecycle (5, carve-out — untouched), test_sdk_props_coercion/test_ingest_bundle/test_analyze_scoped/test_sdk/test_search_engine_recency/test_calibration (4 each) + the long tail (~90 single-site files). The SDK-layer default fixes the whole class at the seam — no per-file edits; files whose sites must NOT derive (carve-out + test_namespace_uri_mode) are protected by the db_path/TEST_MODE gate and their own TEST_NO_REDIRECT_STEMS exemption. Add a unit test: two no-namespace SDKs with distinct db_paths under the same URI → `_get_proj()` yields DISTINCT graphs (assert `projA.graph_name != projB.graph_name`, both `test_`-prefixed). **All explicit non-test namespaces in migrated files route to per-test `test_*` namespaces (cycle-2 P1-4):** `namespace="registry"` → `test_registry_<uuid>` (test_invites_http — per-test uuid so the 36 tests share within a test but never across tests); `namespace="team-a"`/`"team-c"` → `test_<file>_<uuid>` (test_pack_state — **cycle-4 P2-7 census completed: 13 team-* namespace sites, not 8**: team-a ×8 L62/73/118/126/133/143/159/167, team-c L88, **team-p L188, team-k L296, team-red L324, team-green L345 — all VERIFIED this pass**; plus the other non-test namespaces in the file: tenant-a/tenant-b L175/176/539/540, t-reg-2 L456, mcp-team L578, registry L616, t-bf-1 L719); **`namespace="e2e-900"` → `test_e2e900_<uuid>` (cycle-4 P2-7 — MISSED by every prior census): test_index_surfacing L39 + test_backfill_sources L37/902/916/959/989 map to the SHARED non-test graph `team_e2e-900` (the SDK's team_* mapping) used by the e2e-900 suites — route to per-test test_* namespaces like the rest**; `graph_name="team_team-k"`/`"t"` → redirect-derived per-path (P0-1b rule) or explicit test-prefixed. wipe_server's skip behavior on one of these (e.g. a seeded `registry_tortoise` survives `wipe_server` untouched) is asserted in `tests/test_wipe_server.py` — `test_wipe_server_clears_only_test_prefixed` already seeds/asserts a non-test graph survives; add the registry/team variants. Non-wiping SDK users may share the URI default graph (no guard trip) but are listed for the P2 divergence pass.
- Red-herring gate: a test asserts that with URI set, constructing on bare `test`/`tortoise` and bulk-wiping still raises (guard intact — this is the E2E-2 control).
- Embedded mode (URI unset): zero behavior change — embedded guard is disabled and graph-name is only observable where a test asserts it; those assertions are updated consciously (P1 diff-review item).

**Files:**
- Modify: `tests/test_extractor_priors.py`, `tests/test_projection.py`, `tests/test_remove_context_migration.py`, `tests/test_import_endpoint.py` (explicit graph-name renames) + no-namespace SDK bulk-wiper files (identified by the census grep in Step 1)
- Test: `tests/test_wipe_server.py` (add the bare-`test` control test)

**Step 1: Census (no code change)**

Run:
```bash
grep -rn 'graph_name="test"\|graph_name="tortoise"\|graph_name="crash_live"\|graph_name="t"\|graph_name="team_' tests/ --include="*.py"
grep -rn 'namespace="' tests/ --include="*.py" | grep -v 'namespace="test_'  # cycle-2 P1-4: non-test namespaces (team-a, team-c, registry, e2e-tests...)
grep -rn "TortoiseSDK(" tests/ --include="*.py"  # cycle-3 P1-5: no-namespace SDK constructions (the URI-default-graph share)
grep -rn "DETACH DELETE" tests/ --include="*.py" | grep -v __pycache__  # bulk-wipe users
grep -rc 'graph_name="test"' tests/ --include="*.py" | grep -v ":0"   # per-file counts (P1-4 census)
```
Expected: the corrected census above (35× `graph_name="test"` / 2× `tortoise` / 1× `crash_live` / 1× `t` / 1× `team_team-k` constructions + the `namespace="registry"`/`"team-a"` sites — cycle-2 P1-4; **cycle-3 P2-9: test_pack_state's namespace census is incomplete — `namespace="team-c"` at L88 and `namespace="team-a"` at L118/126 (VERIFIED this pass: team-a also at L62/73/133/143/159/167) — all route to per-test `test_*` namespaces (Step 5); per-path derivation isolates the `team_team-k` lock-keying arg anyway, but the census list must be complete**; **cycle-4 P2-7: the census is STILL incomplete — team-p L188, team-k L296, team-red L324, team-green L345 (all VERIFIED this pass) + `namespace="e2e-900"` (test_index_surfacing L39, test_backfill_sources L37/902/916/959/989) were missed by every prior cycle — all route to per-test `test_*` namespaces (Step 5); the e2e-900 namespace is especially load-bearing: it maps to the SHARED non-test graph `team_e2e-900`**); the bulk-wipe users list (D4 table: test_projection, test_search_engine_gaps, test_a9_direct_edge_traversal, test_index_surfacing, test_recall_gaps_subgraph, test_about_event_untangle, test_pre_migration_safety, test_embedded_concurrency live-reset).

**Step 2: Write the failing control test**

```python
def test_bare_test_graph_wipe_still_raises_on_server(uri_env):
    """E2E-2 control: the guard must survive the migration."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test")
    try:
        with pytest.raises(RuntimeError, match="test graph"):
            proj.g.query("MATCH (n) DETACH DELETE n")
    finally:
        proj.close()
```

**Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_wipe_server.py::test_bare_test_graph_wipe_still_raises_on_server -v`
Expected: PASS immediately (the guard already raises — the test pins existing behavior; the "failing-first" here is the rename sweep, verified by Step 4's red run).

**Step 4: Verify the per-path derivation covers the explicit-name sites (cycle-2 P0-1b)**

For each migrated-file construction site above, NO rename is needed on the server lane — the redirect derives `test_<file-stem_sanitized>_<hash12(session+path)>` per construction (distinct paths → distinct graphs; cycle-3 P2-5/P2-17). Verify by running the touched files under URI set: the parity pair (`_tmp("parity_a.db")`/`_tmp("parity_b.db")`, L1866/1867) and the `g_consistency_ok/bad` pair (L1400/1417) must land on DISTINCT graphs (assert `projA.graph_name != projB.graph_name` in a URI-set smoke run — never a graph-vs-itself comparison). Sites that genuinely need ONE deterministic server graph across distinct paths opt in with an already-guard-passing shared name (`test_<file>_suite`) — honored verbatim by the redirect (Task 1 rule). Embedded lane: untouched (graph names stay byte-for-byte — the derivation is server-only). Run the touched files embedded → green (zero embedded-assertion churn).

**Step 5: Route non-test namespaces to per-test `test_*` namespaces (cycle-2 P1-4)**

Add `namespace="test_<file>_<uuid>"` to no-namespace `TortoiseSDK` bulk-wipe/exact-set constructions in migrated files (or route through the URI-aware `sdk_factory`); `namespace="registry"` → `test_registry_<uuid>` (test_invites_http — per-test uuid, so the file's 36 tests share within a test but never across tests); `namespace="team-a"` → `test_<file>_<uuid>` (test_pack_state); `graph_name="team_team-k"`/`"t"` fall under the Step 4 derivation rule. Verify the SDK's `_get_proj` emits `test_*_tortoise` graphs, and add a wipe_server skip assertion for the swept names (a seeded `registry_tortoise` survives `wipe_server` untouched — extend `test_wipe_server_clears_only_test_prefixed`).

**Step 6: Verify zero embedded-behavior change + commit**

Run: `uv run pytest tests/test_extractor_priors.py tests/test_projection.py tests/test_remove_context_migration.py tests/test_import_endpoint.py tests/test_pack_state.py tests/test_invites_http.py -v` (URI unset) → green; then a URI-set smoke of the same files → green with the derived-name isolation asserts (Step 4).
```bash
git add tests/...
git commit -m "chore(testdb): graph-name sweep — per-path derived names + per-test namespaces for migrated constructions (epic #1647 P1, P0-1b/P1-4)"
```

---

### Task 8: Divergence expectation split + E2E-8 conformance file (P1 half) + confirmation pass (P2 half)

**Intent:** Make the D1–D16 divergence table executable (epic indicator #3 — "explicit documented change list"). P1: split test expectations that were already wrong on docker (D5/D6 index shapes, D8 vector, D9 calibration) and ship the E2E-8 conformance file. P2: apply the table to the actual half-b docker run; unexpected divergences are blockers.

**Acceptance (P1 portion):**
- `tests/test_indexes.py` `EXPECTED_RANGE_EMBEDDED` (L21) gets a docker sibling expectation; the D6 composite `(is_operator, lastDreamedAt)` assertion becomes docker-only; `is_operator` is NOT added to the D5 range set (the #522 regression guard — verified in `_ensure_indexes` L1214-1224).
- `tests/test_divergence_conformance.py` (new, E2E-8) asserts: D2/D3 recovery raises on server / auto-rebuilds on embedded; D6 composite present on server only; D8 HNSW index created on server, brute-force ordering on embedded (bench smoke pins it); D11 busy-error embedded-only; D12 multi-tenant on server.
- Existing docker-only assertions unchanged; all additions are additive (embedded expectations untouched).

**Files:**
- Modify: `tests/test_indexes.py`, cross-lens/EP calibration files (additive docker-calibrated expectations)
- Create: `tests/test_divergence_conformance.py`
- Test: the conformance file itself

**Step 1: Write the E2E-8 conformance file** (mode-parametrized over URI set/unset; each assert tagged to its D-branch). Run both modes locally → both green (embedded asserts on embedded, server asserts on server).

**Step 2: Split test_indexes expectations** — add the docker sibling set; run `tests/test_indexes.py` under URI set → the composite assertion now executes on docker (previously docker-skipped); under URI unset → embedded expectations unchanged.

**Step 3: Commit (P1 portion)**

```bash
python3 tools/ci_selection.py --register --surface core  # cycle-2 P1-3: test_divergence_conformance.py
uv run pytest tests/test_ci_selection.py -k integrity -q
git add tests/test_indexes.py tests/test_divergence_conformance.py tests/test_cross_lens.py config/ci-surfaces.yml
git commit -m "test(testdb): divergence expectation split + E2E-8 conformance file (epic #1647 P1)"
```

**Step 4 (P2): Divergence confirmation pass** — after Task 6 flips half b, run the half-b docker suite and check every D-branch against the table (Task 6 Step 4's run). Record findings in `docs/epics/2026-08-24-test-db-migration/divergence-confirmation.md`. Unexpected divergences = P2 blockers (fixed before P3, same class as D9 calibration work).

---

## Phase 1 Gate (P1 acceptance — all of the above must hold before P2)

1. Full embedded suite green: `uv run pytest tests/ -q` → 6,837 passed (baseline).
2. P1 diff review: no embedded-mode assertion changed except conscious graph-name updates; no carve-out file touched.
3. Unit surfaces green: `tests/test_redirect_seam.py`, `tests/test_wipe_server.py`, `tests/test_skip_guard.py`, `tests/test_divergence_conformance.py`, `tests/test_round_trip_parity.py` (cycle-2 P1-5 — E2E-1 now owns a task: Task 1 Step 5b, so it counts at the P1 gate).
4. Orphan count unchanged: `pgrep -f "redislite/bin/redis-server" | wc -l` ≈ 4 (baseline).
5. E2E-1 (mechanism works, embedded unchanged), E2E-2 (wipe_server unit + control), E2E-4 (carve-out untouched), E2E-8 (conformance both modes) all green.
6. CI: both halves green, wall within 20% of baseline (41–42m / 57–58m) — no CI change in P1, so this is a regression check.

---

## Phase 2 — One-half flip (half b → docker, side-by-side divergence discovery)

> Half b is the redislite-heavy half (test_search_engine 121, test_ranking, bench via push_extra — cycle-3 P2-3 label fix: test_reaper and test_embedded_concurrency are **slow_files** entries (VERIFIED via `--emit-push-matrix`: they are in NO fast half; the old "test_reaper 52" claimed a half-b file), so the redislite-heavy-half characterization is test_search_engine + test_ranking + the bench smoke) whose wall (57–58m) already rides the 55m watchdog. Half a stays embedded as the control arm. The embedded lane becomes a **non-gating canary** (decision D-3=A) until the docker lane is proven green for N runs.

### Task 4: Backend-identity tripwire (conftest)

**Intent:** Close scope-review M3 — the coverage manifest only catches nodeids that vanish; it cannot catch "redirect inert + embedded succeeds → job green". A conftest-level backend-identity check makes the docker-half guarantee complete (E2E-6).

**Acceptance:** With `TORTOISE_DB_URI` set and no `TORTOISE_TEST_NO_REDIRECT` covering the sample, a session-autouse check constructs a probe projection and asserts `_is_embedded is False`; if the redirect is inert, the suite fails at session start (never a green pass on the wrong backend). The probe constructs via `FalkorProjection.from_uri(uri, graph_name="test_tripwire_probe")` — NOT a `path=` construction (cycle-2 P1-1b re-key: the fixture's own frame is conftest.py, which has no `test_` stem, so `_caller_test_stem()` returns None and a `path=` probe would now correctly stay embedded → the tripwire would false-alarm; `from_uri` bypasses the redirect and asserts the server lane directly). The same session-start check refuses non-loopback URIs via the shared `is_loopback_uri` predicate — a remote/shared `TORTOISE_DB_URI` fails BEFORE any test writes (cycle-2 P0-2; the redirect's own refusal is the second line of defense). The check also enforces the `TORTOISE_TEST_EXPECT_URI=1` session signal (cycle-2 P2-6): when CI sets it, a missing URI fails the session at start — closing the "outage simulation runs URI-less and passes on embedded" vacuous-pass hole. Skip-exempted when the entire session is carve-out (URI-unset job — check is a no-op there).

**Files:**
- Modify: `tests/conftest.py` (session-autouse fixture)
- Test: `tests/test_backend_identity_tripwire.py` (new)

**Step 1: Write the failing test** — with URI set, `FalkorProjection.from_uri(uri, graph_name="test_tripwire_probe")` must be server-mode; a monkeypatched-broken redirect (simulated inert) must make the assertion fail; a fake non-loopback URI must raise the locality error at session start with **zero graphs created** (point a 1-test session at `docker://:pw@db.internal.example.com:6379` and assert the session fails before any test body runs — cycle-2 P0-2).

**Step 2: Run to verify it fails** (against today's code, URI set, no redirect → `_is_embedded is True` → red).

**Step 3: Implement** — conftest session-autouse fixture (conftest already exports `TORTOISE_TEST_MODE=1` (Task 1), so the redirect is armed; the probe uses `from_uri` per the cycle-2 P1-1b re-key):

```python
@pytest.fixture(scope="session", autouse=True)
def _assert_backend_identity():
    """Epic #1647 E2E-6 tripwire: on docker-URI sessions, the session must
    be server mode — a dormant redirect would silently run the migrated
    suite on embedded and pass green. Cycle-2 P0-2: a non-loopback URI fails
    here, before ANY test writes. Cycle-2 P2-6: TORTOISE_TEST_EXPECT_URI=1
    (CI docker halves) fails a URI-less session instead of green-passing on
    the carve-out shape."""
    from tortoise.config import is_db_uri, is_loopback_uri  # shared predicates
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if os.environ.get("TORTOISE_TEST_EXPECT_URI") == "1" and not uri:
        pytest.fail("TORTOISE_TEST_EXPECT_URI=1 but TORTOISE_DB_URI unset — "
                    "docker-half session must run against the server (epic #1647 E2E-6)")
    if not uri or not is_db_uri(uri):
        return  # embedded session (carve-out) — no tripwire
    if not is_loopback_uri(uri) and os.environ.get("TORTOISE_TEST_ALLOW_REMOTE") != "1":
        pytest.fail(f"TORTOISE_DB_URI {uri!r} is not loopback — refusing before "
                    f"any test writes (epic #1647 D-4/P0-2); set "
                    f"TORTOISE_TEST_ALLOW_REMOTE=1 to override")
    from tortoise.projection import FalkorProjection
    probe = FalkorProjection.from_uri(uri, graph_name="test_tripwire_probe")
    try:
        assert probe._is_embedded is False, (
            "backend-identity tripwire: server session but the probe is "
            "embedded — migrated suite would green-pass on the wrong "
            "backend (epic #1647 E2E-6)")
    finally:
        probe.close()
```

**Step 4: Run to verify it passes** (Task 1's redirect active) → green.

**Step 5: Commit**

```bash
python3 tools/ci_selection.py --register --surface core  # cycle-2 P1-3: test_backend_identity_tripwire.py
uv run pytest tests/test_ci_selection.py -k integrity -q
git add tests/conftest.py tests/test_backend_identity_tripwire.py config/ci-surfaces.yml
git commit -m "feat(testdb): backend-identity tripwire on docker sessions (epic #1647 P2, E2E-6/P0-2/P1-1b/P2-6)"
```

---

### Task 5: The 3 busy-error per-test markers + the carve-out redirect-exemption wiring

**Intent:** Implement decision D-2=A (per-test embedded-only marker for the 3 `EmbeddedStoreBusyError` tests) + the scope-review H1 requirement (`TORTOISE_TEST_NO_REDIRECT` **TEST-MODULE** exemption for the 7 half-b carve-out files — cycle-2 P1-1a adds `test_smoke_embedded`, so the P2 job-level URI cannot flip them to docker and break their embedded-specific assertions). Per plan-review P0-1, the exemption key is the **caller test module** (frame-identified by the redirect), not the DB-file basename — the carve-out files' arbitrary DB names (`c.db`, `fresh.db`, `solo.db`, `tortoise.db`…) can never be the key.

**Acceptance:**
- New `embedded_only` marker registered in `pyproject.toml` (alongside `track_b`) + a conftest autouse skip: URI set + marker present → `pytest.skip("embedded-only: ...")` (visible skip, the D-2 skip mechanism — distinct from the per-file redirect exemption); URI unset → test runs normally.
- Applied to exactly 3 tests: `tests/test_audit.py` (d) case (~L478/L504), `tests/test_pack_state.py::TestBackfillScript::test_dry_run_default_makes_no_writes` (L668), `tests/test_index_directory.py::test_e2e9_cross_process_embedded_overlap` (L1852).
- `TEST_NO_REDIRECT_STEMS` in `tests/_embedded.py` = the **7** half-b carve-out TEST-MODULE stems at P2 (`test_embedded_lifecycle_fast_close`, `test_redis_guard`, `test_guard`, `test_config`, `test_ops_safety`, `test_pre_migration_safety`, **`test_smoke_embedded`** — cycle-2 P1-1a with a cycle-3 P2-1 rationale fix: `tests/bench/test_smoke_embedded.py` rides fast half b via the **push_extra distribution** (`--emit-push-matrix` at python-ci.yml:193 → `push_legs` at ci_selection.py:262 distributes `push_extra` evenly across halves — index 3 lands in half b, VERIFIED by running the derivation this pass: half_b contains `bench/test_smoke_embedded`), NOT as a classified top-level fast file (bench files are not top-level surfaces; push_extra is the mechanism, and it IS referenced in CI — the cycle-3 reviewer's "push_extra unreferenced" premise is factually wrong, but the exemption itself stays). The file asserts `db_mode == "embedded-falkordblite"` (L47) — the redirect would flip it to the server lane and red. The exemption additionally covers the **post-merge-validation full `tests/` collection at P4** (pmv runs `pytest tests/`, which collects `tests/bench/*` — the same exemption list must ride the pmv URI job, Task 10 Step 1a). `_caller_test_stem()` resolves `test_smoke_embedded`, but it is not exempted; conftest exports it via `os.environ.setdefault("TORTOISE_TEST_NO_REDIRECT", ...)` so the product-side redirect honors it. CI may override. **Stem-registry test (cycle-2 P2-9):** a test mirrors `test_no_new_raw_embedded_constructions` — every stem in `TEST_NO_REDIRECT_STEMS` must resolve to an existing `tests/` (or `tests/bench/`) module, and (P3+, once the carve-out list expands) every carve-out file's stem must be present — a stale/typo'd stem reds instead of silently not exempting.
- **fixtures/redis-guard note (P0-1 interaction, cycle-3 P2-8 correction):** the redis-guard fixtures (`bad_relative_path.py` etc.) are subprocess scripts, not `test_`-prefixed modules — the caller-frame exemption cannot key them. **`tools/redis-guard.py` is a STATIC SCANNER, not a fixture executor (VERIFIED: its docstring is "scan repo, exit 1 on violations"; it has no fixture-running code), so the earlier "already pops both (cycle-1)" claim is false — there is no pop to rely on, and no code change is needed there.** The fixtures inherit `os.environ.copy()` from the pytest process; the redirect's frame gate (cycle-2 P1-1b) already suppresses redirection in a subprocess with no test frame. Add ONE verification step: run `tests/fixtures/redis-guard/` scripts with URI set and assert the relative-path reject still fires (embedded-clean semantics preserved) — the frame gate, not a pop, is the mechanism (same pattern as `test_hard_reject.py:131`).
- **Marker timing (plan-review P2-17):** of the 3 marked tests, only `test_pack_state` rides fast half b — its marker activates at the P2 flip. `test_audit` (d) and `test_index_directory` E2E-9 ride half a, so their markers first activate in CI at P3 (half-a flip). Task 5 Step 3 therefore verifies all 3 with URI set explicitly (monkeypatch), independent of CI surface timing.
- On the embedded lane all 3 marked tests still pass; on the docker lane they skip with the embedded-only reason (never green-skip — the skip reason contains neither "FalkorDB" nor vanishes from the manifest).

**Files:**
- Modify: `pyproject.toml` (marker registration, ~L149)
- Modify: `tests/conftest.py` (autouse marker-skip fixture; env export)
- Modify: `tests/_embedded.py` (`TEST_NO_REDIRECT_STEMS`)
- Modify: `tests/test_audit.py`, `tests/test_pack_state.py`, `tests/test_index_directory.py` (marker application)
- Test: the 3 marked tests + a marker-semantics test

**Step 1: Register the marker + write the marker-semantics test**

```toml
# pyproject.toml markers list
"embedded_only: epic #1647 (D-2) — embedded-only behavior (EmbeddedStoreBusyError); skips visibly when TORTOISE_DB_URI is set",
```

```python
# tests/test_markers.py (new)
from pathlib import Path


def test_embedded_only_marker_skips_when_uri_set(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    # a marker-semantics probe test is marked embedded_only — the autouse
    # skip fixture must skip it
    ...


def test_no_redirect_stems_exist_as_modules():
    # Cycle-2 P2-9: every TEST_NO_REDIRECT_STEMS entry must resolve to a
    # real test module — a stale/typo'd stem silently fails to exempt.
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    root = Path(__file__).resolve().parents[1]
    for stem in TEST_NO_REDIRECT_STEMS:
        hit = list((root / "tests").glob(f"{stem}.py")) or \
              list((root / "tests/bench").glob(f"{stem}.py"))
        assert hit, f"TEST_NO_REDIRECT_STEMS entry {stem!r} is not a test module"


def test_no_carve_out_imports_test_helpers():
    # Cycle-4 P1-4 guard: _caller_test_stem() keys on the NEAREST test_
    # frame — a carve-out file constructing through tests/test_helpers.py
    # would resolve to stem "test_helpers" (not its own exempted stem), so
    # its redirect exemption silently never fires. Assert no carve-out
    # module (a TEST_NO_REDIRECT_STEMS entry) imports it.
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    root = Path(__file__).resolve().parents[1]
    for stem in TEST_NO_REDIRECT_STEMS:
        for p in (root / "tests").glob(f"{stem}.py"):
            src = p.read_text()
            assert "test_helpers" not in src, \
                f"carve-out {stem} imports test_helpers — loses its stem exemption (P1-4)"


def test_session_token_present_and_hex8_during_docker_session(monkeypatch):
    # Cycle-4 P2-14: mid-session TEST_SESSION mutation would strand this
    # session's graphs (journal filename + derived names key off the ORIGINAL
    # value; Task 1's no-mutation probe covers drift, this covers presence/
    # shape on docker lanes). Only asserted when the URI is actually set
    # (the docker-half session shape) — embedded sessions need no token.
    import os, re
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("no docker session — token not required")
    assert re.fullmatch(r"[0-9a-f]{8}", os.environ.get("TORTOISE_TEST_SESSION", "")), \
        "docker session must carry TORTOISE_TEST_SESSION = 8 hex (conftest export)"
```

**Step 2: Implement the autouse skip + apply markers to the 3 tests**; add the 7 stems to `TEST_NO_REDIRECT_STEMS` (the 6 from cycle 1 + `test_smoke_embedded`, cycle-2 P1-1a).

**Step 3: Run to verify** — URI unset: the 3 tests pass. URI set (explicitly, via monkeypatch or env — P2-17: test_audit/test_index_directory won't see the URI in CI until P3, so this step is the P2-era verification for them): they skip with `embedded-only` reason; skip-guard (FalkorDB-substring, read from junitxml per Task 3) does not trip; the manifest sees the reasoned skip (junitxml `<skipped>`), so P2 stays green. Also run `tests/fixtures/redis-guard/` against the redis-guard tool with URI set to confirm the pop keeps the reject fixtures green.

**Step 4: Commit**

```bash
python3 tools/ci_selection.py --register --surface core  # cycle-2 P1-3: test_markers.py
uv run pytest tests/test_ci_selection.py -k integrity -q
git add pyproject.toml tests/conftest.py tests/_embedded.py tests/test_audit.py tests/test_pack_state.py tests/test_index_directory.py tests/test_markers.py tools/redis-guard.py config/ci-surfaces.yml
git commit -m "feat(testdb): embedded_only per-test markers + carve-out redirect exemption (epic #1647 P2, D-2/H1/P0-1/P1-1a/P2-9)"
```

---

### Task 6: CI phase-2 flip — half b → docker + coverage-manifest wiring + dual-lane

**Intent:** The P2 flip (decision D-3=A): half b runs on the provisioned falkordb service with job-level `TORTOISE_DB_URI`; half a stays embedded as the canary; skip-guard manifest goes fail-closed on half b. This is the first divergence-discovery run.

**Acceptance:**
- `test` job, `half: b` gains job-level `TORTOISE_DB_URI: docker://:falkordb@localhost:6379/tortoise_test_matrix` (passworded service already provisioned; services block unchanged) **scoped to `full == true` (plan-review P1-7)** — the tier-2 PR selection (`a_files`/`b_files`, surface-based) is not verified against the carve-out exemption and must not ride the redirect; half a unchanged (embedded canary). **The URI carries a test-prefixed path (cycle-2 P1-2):** `from_uri(uri)` with no explicit graph resolves the URI path (projection L566-567: `parsed.path.lstrip('/') or "tortoise"`) — a path-less job URI resolves to the non-test graph `tortoise`, and any migrated test doing `from_uri(os.environ["TORTOISE_DB_URI"])` then `DETACH DELETE` (test_search_engine L303/304 + L413/414 + L497 — the half-b file that matters at P2) would trip `_assert_test_graph`. `/tortoise_test_matrix` is guard-passing, so every URI-default DETACH site is fixed by the single URI change. **Cycle-3 P2-2 (over-cataloguing fix):** test_hnsw_vector_index and test_ingest are REMOVED from this list — `test_hnsw_vector_index`'s DETACHes are STARTS-WITH/`{id:$id}`-scoped, not bulk (VERIFIED: L126/129 `WHERE n.id STARTS WITH 'hnsw247_'`, L239 `{id: $id}` — no guard trip; it also rides half **a**), and `test_ingest` is a **slow_files** entry (VERIFIED: ci-surfaces.yml) so it follows at P3, not in the P2 half-b flip. The Task 6 Step 3 grep/normalization check keeps only the half-b-bulk-DETACH sites (test_search_engine) + any straggler found by the grep.
- Manifest generation step on half b: expected nodeids = **the SAME `$FILES` the pytest run builds** (plan-review P1-7 — the old plan keyed off `--emit-push-matrix` lists, which can diverge from `matrix.files`/`a_files`/`b_files` in the tier-2 path) × `--collect-only` with the same `-m 'not track_b'` filter. **Off the critical path (cycle-2 P2-12):** the collect-only runs in its own step (or a cached manifest keyed on a file-list hash), so the collect-only wall does not consume the pytest step's 55m budget. **Cycle-3 P2-16: E2E-5 measures BOTH walls — the job wall (`timeout-minutes: 60`, the current spec) AND the pytest-step wall** (captured in the run step: `echo "step_wall=$SECONDS" >> $GITHUB_OUTPUT` from the existing in-step `SECONDS` accounting the watchdog uses) — the job wall alone can hide a step that silently rides the 55m watchdog; the step-wall is the real divergence/regression signal. Gate: `step_wall < 55m` with margin (target ≤ ~45m), recorded for P3/P4.
- Skip-guard step on half b passes `--manifest` + `--junitxml` (Task 3); fail-closed — **gated on pytest rc==0 AND non-empty `$FILES`** (plan-review P1-7: the "no selected files" path writes rc=0 with no junitxml; with a manifest that would false-red, so the guard must skip when `$FILES` is empty). Half a unchanged (embedded canary, current guard).
- No test-slow / e2e / track_b changes (P2 out of scope — they follow in P3).
- P2 divergence confirmation (Task 8 Step 4) runs against the observed half-b results.
- **Cycle-4 P2-6 — the live-required job is added to this task's audit:** `test-concurrency-falkor` (python-ci.yml L695-760, VERIFIED) sets job-level `TORTOISE_DB_URI: "docker://:falkordb@localhost:6379/tortoise"` — a NON-test-prefixed path (bare `tortoise`). This is currently INERT and stays that way through P2/P3: both live tests construct via `FalkorProjection.from_uri(uri, graph_name="test_live_mw_tortoise")` with EXPLICIT test-prefixed names (test_embedded_concurrency L111-153, verified — no `path=` construction, no URI-default DETACH), so nothing resolves a bulk-wipe to `/tortoise`. The audit adds a grep in Task 6 Step 3: no construction in the live-required job's two test files may (a) construct with `path=` under URI (would redirect to a derived name — harmless but must be declared) or (b) bulk-`DETACH DELETE` the URI-default graph (guard trip / cross-run pollution). If a future test adds either, the job URI must gain the test-prefixed path like the fast matrix (P1-2).

**Files:**
- Modify: `.github/workflows/python-ci.yml` (`test` job: half-b include env, manifest generation + guard wiring)
- Modify: `tools/skip-guard.py` (manifest generation mode, if not already covered by Task 3)
- Test: `tests/test_ci_selection.py` (if the manifest generator lives in `tools/ci_selection.py`)

**Step 1: Add the manifest generator** — `tools/skip-guard.py --emit-manifest` (or a `tools/ci_selection.py --emit-manifest` sibling) reads the **same space-joined `$FILES` string the run step builds** + a `--collect-only` output (run with the same `-m 'not track_b'` filter) and emits expected nodeids (one per line, `#`-comments allowed). Unit-test it (plan-review P1-7: the generator must consume the run's file list verbatim, not a re-derived matrix list).

**Step 2: Edit the workflow** (plan-review P1-7):

1. In the run step, after the existing `$FILES` construction, emit it for reuse: `echo "$FILES" > ${RUNNER_TEMP:-/tmp}/pytest-files`.
2. Compute the URI run-step-locally — the matrix include can't condition on `full`:

```yaml
URI=""
if [ "${{ needs.changes.outputs.full }}" = "true" ] && [ "${{ matrix.half }}" = "b" ]; then
  URI="docker://:falkordb@localhost:6379/tortoise_test_matrix"  # P1-2: test-prefixed path — the URI-default graph must pass the guard
fi
```

and export it on the pytest step: `TORTOISE_DB_URI: $URI` (plus `TORTOISE_TEST_MODE: "1"` is already exported by conftest; `TORTOISE_TEST_NO_REDIRECT` comes from conftest too — CI override optional). **Cycle-4 P1-7 — wire the E2E-6 session signal into CI:** the half-b docker job also exports `TORTOISE_TEST_EXPECT_URI: "1"` (Task 4's tripwire consumes it — a docker-half session whose URI is missing/dropped fails at session start instead of green-passing on the carve-out shape; the cycle-3 plan defined the signal but Task 6's workflow edits never set it, so CI never actually armed it). The env line lands in the same half-b include block as the URI.
3. Manifest generation BEFORE pytest on half b, in a SEPARATE step (same `$FILES` + `-m 'not track_b'` collect-only) so the collect-only wall never rides the pytest step's 55m budget (cycle-2 P2-12; cache the emitted manifest keyed on a hash of `$FILES` so tier-1/tier-2 reruns reuse it).
4. The pytest invocation gains `--junitxml=/tmp/junit.xml -o junit_family=xunit1` (Task 3's authoritative observed-set source; `-r fEs` stays for the human summary).
5. Skip-guard step: `RC=$(cat ${RUNNER_TEMP:-/tmp}/pytest-rc)`; skip when `$RC != 0` or the files file is empty; else `python3 tools/skip-guard.py /tmp/pytest.log --junitxml=/tmp/junit.xml --manifest <expected-nodeids>`.
6. If `$FILES` is empty, the run step keeps its current early-exit and the guard skips (no manifest).

**Step 3: Local pre-flight (the divergence-discovery run + the E2E-2/E2E-3 docker smoke, plan-review P1-6)** — run the half-b file list against the local docker containers, PLUS the wipe-heavy and concurrency surfaces that CI won't see until P3 (test_projection, test_search_engine_gaps, test_index_surfacing, test_about_event_untangle are half-a/slow; test_embedded_concurrency is test-slow — their docker behavior is P2-gated via this run):

```bash
TORTOISE_DB_URI="docker://:falkordb@localhost:6379/tortoise_test_matrix" \  # P1-2: test-prefixed URI path
TORTOISE_TEST_NO_REDIRECT="test_embedded_lifecycle_fast_close,test_redis_guard,test_guard,test_config,test_ops_safety,test_pre_migration_safety,test_smoke_embedded" \  # P1-1a: 7 stems (incl. tests/bench/test_smoke_embedded)
uv run pytest tests/test_search_engine.py tests/test_ranking.py tests/test_pack_state.py ... \
  tests/test_projection.py tests/test_search_engine_gaps.py tests/test_index_surfacing.py \
  tests/test_about_event_untangle.py tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes \
  -v --timeout=300 -m 'not track_b' --maxfail=20 -r fEs --junitxml=/tmp/junit.xml -o junit_family=xunit1
```

Expected: half-b DB-agnostic tests run against docker; the 3 busy-error tests skip (marker); the 7 carve-out files run embedded (exemption, cycle-2 P1-1a); the wipe-heavy surfaces run green via `_wipe_or` (E2E-2); the live-writer concurrency test runs with 0 busy errors (E2E-3); any embedded-calibrated assertion break = a divergence-confirmation item (Task 8 Step 4), not a silent fix. **Cycle-4 P2-12 — in-process prod-command call sites are added to the pre-flight (the cycle-3 audit never covered them):** `tests/test_domain_validators.py` calls `tortoise.__main__._cmd_validate` in-process (8 sites, L543-579 verified), `tests/test_session_index_health.py:207` calls `main(["doctor"])`, `tests/test_cli_context.py:45` calls `main(["context"])` — under a docker session the redirect fires inside these prod commands (their `FalkorProjection(args.db)` constructions have the TEST file in the caller stack, so `_caller_test_stem()` resolves to the test module and the path redirects to a derived server graph). Add the three files to the Step 3 pre-flight run and verify each stays green on docker (DB-agnostic — the redirect makes the CLI test the server lane with a derived name, which is the DESIRED outcome) or is explicitly exempted; document the result in the divergence-confirmation log. Also add the L521 `doctor`-fallback path (`FalkorProjection(db_path)` — the embedded fallback after a failed URI probe) to the audit: it must stay reachable when URI is set but the server is down (embedded fallback, no redirect — the redirect never fires because `_caller_test_stem()` is None in the subprocess; in-process `main(["doctor"])` DOES redirect, and its embedded-fallback branch is then unreachable — pin which leg `test_session_index_health` exercises on each lane). **URI-default DETACH check (cycle-2 P1-2, scope per cycle-3 P2-2):** grep the half-b migrated files for `from_uri(os.environ["TORTOISE_DB_URI"])`/`_LIVE_URI` sites that bulk-`DETACH DELETE` — with the path-ed job URI every such site resolves to `tortoise_test_matrix` (guard-passing). Only the half-b bulk-DETACH sites matter at P2 (test_search_engine L303/304 + L413/414 + L497 — VERIFIED in half b); test_hnsw_vector_index (half a, STARTS-WITH-scoped wipes) and test_ingest (slow_files, P3) are out of this check's scope; any half-b site still resolving to a non-test graph is normalized to an explicit test-prefixed graph in the same step (the `tortoise_test_r2_migrate`-style literals already carry one). **Cycle-4 P2-6 — the live-required job joins the same grep:** `grep -rn "from_uri\|path=\|DETACH DELETE" tests/test_embedded_concurrency.py` — the `test-concurrency-falkor` job URI is `docker://.../tortoise` (non-test-prefixed, python-ci.yml L695-760 verified); both live tests pass explicit `graph_name="test_live_mw_tortoise"` and never bulk-wipe the URI default (verified L111-153), so it stays inert — the grep pins that no new `path=` construction or URI-default DETACH sneaks in (if one does, the job URI must gain the P1-2 test-prefixed path). Then verify the manifest closes: generate the expected nodeids from the same file list × collect-only and run `tools/skip-guard.py /tmp/pytest.log --junitxml=/tmp/junit.xml --manifest <expected-nodeids>` → exit 0.

**Step 4: Commit + push + observe CI** — record half-b wall; confirm half a (embedded) still green; confirm the skip-guard manifest passes (junitxml-reconciled, Task 3).

```bash
git add .github/workflows/python-ci.yml tools/skip-guard.py tools/ci_selection.py tests/test_ci_selection.py
git commit -m "ci(testdb): phase-2 flip — fast half b on docker + junitxml-reconciled coverage manifest (epic #1647 P2, D-3/P1-7)"
```

---

## Phase 2 Gate (P2 acceptance)

1. Half b green on docker with job URI; zero FalkorDB-reasoned skips (guard red otherwise); manifest passes (zero vanished nodeids).
2. Half a (embedded control) green — same as P1.
3. Divergence confirmation (Task 8 Step 4): observed divergences match the D1–D16 table exactly; zero unexpected (each unexpected = P2 blocker).
4. Half-b wall measured (expect 57–58m → ≤ ~40m) — the P3 merge-decision input.
5. The 3 busy-error tests skip visibly with URI set / pass embedded (E2E-3/5 AC5). **P2-17 timing:** in CI only test_pack_state's marker activates at P2 (test_audit + test_index_directory ride half a — verified URI-set locally in Task 5 Step 3; their CI-wide marker activation lands at P3).
6. E2E-1 (docker half round-trip) green; E2E-2 + E2E-3 green via the Task 6 Step 3 docker smoke (their CI-wide gate is P3 — the wipe-heavy files are half-a/slow and test_embedded_concurrency is test-slow; plan-review P1-6); E2E-6 (tripwire + manifest + `TORTOISE_TEST_EXPECT_URI=1` signal — cycle-2 P2-6) green.

---

## Phase 3 — Both halves → docker + default inversion

### Task 9: Phase-3 flip — half a, canary drop, `skip_if_no_falkor` retirement, hygiene gating

**Intent:** Complete the default inversion (P3): the whole fast matrix runs on docker; embedded runs only the carve-out. Drop the embedded canary after N consecutive green docker runs (N=5 per epic target #2); retire the vacuous `skip_if_no_falkor` early-return from migrated files; gate `TORTOISE_FAST_ATEXIT`/`_redislite_hygiene` to embedded sessions; flip the orphan assert; follow the non-fast surfaces (test-slow, track_b, e2e/) to docker.

**Acceptance:**
- Both fast halves set job-level `TORTOISE_DB_URI`; manifest covers both halves; `TORTOISE_TEST_NO_REDIRECT` expands to the full **17-file** carve-out set (cycle-3 P2-12: the old "16" undercounts — the set is the 7 Task-5 stems + 10 Task-9 additions = 17 TEST-MODULE stems; `fixtures/redis-guard/*` are subprocess scripts, not test modules, and `test_smoke_embedded` is already one of the 7. Count verified against research-brief §4.1's "17 files / 342 tests"): the 7 (test_embedded_lifecycle_fast_close, test_redis_guard, test_guard, test_config, test_ops_safety, test_pre_migration_safety, test_smoke_embedded) + test_embedded_lifecycle, test_reaper, test_reaper_orphan, test_embedded_concurrency, test_flip_gate, test_hard_reject, test_migrate_db, test_backup_e2e, test_hosted_backup, test_projection_lifecycle (path corrected from the cycle-1 "bench/...", the research-brief §4.1 verified list).
- `skip_if_no_falkor` retired from the 10 migrated files (test_audit, test_battery_setup, test_domain_validators, test_event_provenance, test_ingest, test_list_contexts, test_projection, test_projection_version_gate, test_supplementary + `_live_utils` stays — its `_skip_unless_live_uri` is the INTENTIONAL visible URI-gate, guard-excluded). Replacement: visible `pytest.skip(reason=...)` or fail-fast.
- `TORTOISE_FAST_ATEXIT` + `_redislite_hygiene` session sweeps become no-ops on docker halves (gated on whether any embedded server was actually created).
- Orphan assert: docker halves expect ~0 redislite orphans; carve-out/embedded jobs keep the bounded (<20) assert.
- Non-fast surfaces flip to docker default (same URI mechanics + manifest); carve-out 342 stay embedded-only.
- Matrix-merge decision (D-3): keep the 2-half split through P3; the measured P2/P3 walls inform the P4 merge decision.

**Files:**
- Modify: `.github/workflows/python-ci.yml` (half-a URI; manifest both halves; orphan-assert flip; hygiene env gating; non-fast surface wiring; carve-out job)
- Modify: 10 migrated files (`skip_if_no_falkor` → visible skip)
- Modify: `tests/_embedded.py` (deprecate `skip_if_no_falkor`; hygiene gating hook)
- Modify: `tests/conftest.py` (env gating)
- Test: `tests/test_skip_guard.py` (carve-out exemption + live-utils exclusion cases), the 10 migrated files' skips

**Steps:** (mirror Task 6 mechanics per surface)

**Step 1:** Flip half a env + extend manifest to both halves; run the full fast matrix locally against docker (E2E-5 dry run).
**Step 2:** Retire `skip_if_no_falkor` from the 10 files (visible skip or fail-fast); verify each with URI set and unset.
**Step 3:** Gate `TORTOISE_FAST_ATEXIT`/`_redislite_hygiene` to embedded sessions; verify docker halves log no hygiene action (E2E-7).
**Step 4:** Flip the orphan assert for docker halves; carve-out job keeps <20. **Cycle-4 P1-9:** add the E2E-7 server-side GRAPH.LIST bound to the same step — after the session-end/stale sweeps (drop=True), `GRAPH.LIST` count must be < this session's journal size + constant (the sweep's DELETE leaves only pre-existing/foreign graphs).
**Step 5:** Wire test-slow / track_b / e2e/ to docker; extend the manifest; add the dedicated carve-out job (URI unset, `TORTOISE_TEST_CARVE_OUT=1`, the 342-test set) as E2E-4's CI home — this job is what keeps the P4 URI-required enforcement (Task 10 Step 1a) from failing the carve-out.
**Step 6:** After 5 consecutive green CI runs, remove the embedded canary lane (half-a embedded config retired — the dual-lane was bounded per D-3). **The streak is instrumented (cycle-2 P2-8 + cycle-3 P2-15 — producer/population/classifier now specified):** a `config/testdb-canary-streak.json` CI artifact records `{runs: [run_id...], consecutive_green: N}`. **Producer:** a dedicated post-merge step in the fast-matrix job (runs AFTER both halves + the skip-guard manifest step; `post-merge` trigger only — PR runs never mutate the artifact); it writes via atomic rename (write to `testdb-canary-streak.json.tmp` → `mv`) so a concurrent writer can never half-read. **Population:** ONLY post-merge full-matrix runs count (the canary lane is the push/schedule lane; a tier-2 PR run is a different shape and is excluded). **Classifier:** a scripted triage step (`tools/testdb_canary_classify.py`) reads the run's junitxml + manifest output + divergence-confirmation log **+ the recorded `step_wall` (cycle-4 P2-4 — a MANDATORY input, not optional: E2E-5 gates `step_wall < 55m`, so a run that silently rides the watchdog and passes green is a masked wall regression — it must break the streak like any other failure)** and buckets each failing run: D1–D16 divergence (expected table entry → does NOT reset; logged), unexpected divergence / guard red / manifest red / **step-wall-gate failure (`step_wall ≥ 55m` → resets to 0)** / infra flake (→ resets to 0). The classification is deterministic (scripted, no human-in-the-loop): the same inputs always classify the same run. A run with the manifest + tripwire green increments, any docker-divergence flake or guard red **resets it to 0**; the canary drop is gated on `consecutive_green >= 5` (the artifact, not a hand-wave). What breaks a streak is defined: a FalkorDB-reasoned skip (guard red), a vanished nodeid (manifest red), a divergence-attributable failure (P3 Gate 3 triage), **a step-wall-gate failure (cycle-4 P2-4)**, or an infra flake on the docker service — infra flakes reset too (the lane must prove the CODE is green on docker, N times in a row).
**Step 7:** Commit each step (`git commit -m "ci(testdb): phase-3 ..."`).

---

## Phase 3 Gate (P3 acceptance)

1. Full fast matrix green on docker services; half a ≤ ~50m, half b clears the 55m watchdog (target ≤ ~45m) — no >20% regression vs baseline (E2E-5).
2. Manifest passes with zero FalkorDB-reasoned skips and zero missing nodeids on both halves (E2E-6).
3. 0 flaky failures attributable to docker-vs-embedded divergence in 5+ consecutive CI runs (epic indicator #2).
4. Orphan assert on docker halves ≈ 0 (E2E-7); **server GRAPH.LIST count < journal size + constant after session-end/stale sweeps (cycle-4 P1-9 — the DETACH-only accumulation bound)**.
5. Carve-out 342 still green on embedded (dedicated URI-unset `TORTOISE_TEST_CARVE_OUT=1` job, E2E-4).
6. E2E-8 conformance passes in both modes.

---

## Phase 4 — Allowlist/reaper shrink

### Task 10: Allowlist shrink (34 → ~21) + reaper demotion + end-state verification

**Intent:** Delete the debt (epic indicators #3/#4): the drift registry shrinks to the true carve-out; the reaper loses its CI-correctness role; the divergence change list is filed canonically; end-state O/I/T verified.

**Acceptance:**
- `RAW_EMBEDDED_ALLOWLIST` (tests/test_embedded_lifecycle.py:42) shrinks 34 → ~21: the 7 drift-registered files (test_export_cli, test_import_endpoint, test_projection, test_indexes, test_ingest, test_supplementary, test_semantic_extractor) + 6 non-carve-out entries (test_de2e1_entity_extraction, test_extractor_doc, test_extractor_priors, test_index_github_cli, test_m1, test_remove_context_migration) migrate out; `e2e/hosted/test_12_selfhost_migration` reviewed (selfhost path IS docker — likely drift fix; if it hardcodes embedded paths, fix rather than carve out); `repro/reproduce_redislite_leak.py` + fixtures + 17 carve-out files stay (cycle-3 P2-12 count).
- `test_no_new_raw_embedded_constructions` (test_embedded_lifecycle.py:186) passes against the shrunk list (it reads the list from source).
- Default `pytest` requires `TORTOISE_DB_URI`; the carve-out is the sole embedded surface. **Enforcement mechanism (plan-review P1-9 — the old plan stated the requirement with no enforcement):** a conftest session-start check fails the run when `TORTOISE_DB_URI` is unset UNLESS `TORTOISE_TEST_CARVE_OUT=1` is set: `if not os.environ.get("TORTOISE_DB_URI") and not os.environ.get("TORTOISE_TEST_CARVE_OUT"): pytest.fail("default pytest requires TORTOISE_DB_URI (epic #1647 P4); run the carve-out with TORTOISE_TEST_CARVE_OUT=1")`. The dedicated carve-out job sets `TORTOISE_TEST_CARVE_OUT=1`.
- **post-merge-validation + local-dev wiring (plan-review P1-9):** `.github/workflows/post-merge-validation.yml` (runs `pytest tests/` on every merge with the embedded default today) gains job-level `TORTOISE_DB_URI` + the same manifest/junitxml guard as the fast matrix (it is a full-suite run, so the carve-out exemption list must be the full Task 9 set); local dev: `uv run pytest tests/` requires the `.env` URI (documented in `docs/epics/2026-08-24-test-db-migration/README.md`), carve-out via `TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/<carve-out files>`.
- Reaper demoted: scheduled reaper narrows to local-dev hygiene; CI loses its correctness dependency (docker halves produce no orphans); conftest `_redislite_hygiene` still sweeps embedded sessions on dev boxes.
- End-state measurements: orphans < 20 on a dev machine WITHOUT the scheduled reaper (epic indicator #2, re-measure of the 4-orphan baseline); ≥90% of tests on docker (measured ≈95% — 342/6,837 carve-out); fast-matrix wall ≤ 20% regression.
- Optional: matrix merge (D-3) — single fast job if both measured halves < ~40m (decision recorded with measured walls).

**Files:**
- Modify: `tests/test_embedded_lifecycle.py` (allowlist shrink)
- Modify: reaper scheduling/ops config (demotion to hygiene-only)
- Create: `docs/divergence-change-list.md` (canonical D1–D16 filing; or link into the epic docs)
- Modify: `.github/workflows/python-ci.yml` (optional matrix merge)
- Modify: `.github/workflows/post-merge-validation.yml` (job-level URI + junitxml-reconciled manifest — plan-review P1-9)
- Modify: `tests/conftest.py` (the P4 URI-required enforcement check)
- Test: `tests/test_embedded_lifecycle.py::test_no_new_raw_embedded_constructions`, `tests/test_skip_guard.py`

**Steps:**

**Step 1:** Remove the 13 migrated files from the allowlist; run the enforcement test → green (they've been running docker since P2/P3 — the move is a registry update, not a first run).
**Step 1a:** Implement the P4 enforcement (plan-review P1-9): conftest session-start URI-required check + `TORTOISE_TEST_CARVE_OUT` gate (above); wire `.github/workflows/post-merge-validation.yml` to job-level URI + junitxml-reconciled manifest; verify the carve-out job passes with `TORTOISE_TEST_CARVE_OUT=1` and that a URI-less migrated run fails with the actionable message. **The post-merge-validation manifest must replicate the run's OWN ignore set (cycle-2 P2-14 + cycle-4 P2-11):** the pmv run excludes `--ignore=tests/e2e` + `$SLOW_IGNORES` (the **29** slow files from `config/ci-surfaces.yml` — cycle-3 P2-11: verified count, the plan's "21" undercounted); its collect-only manifest generation uses the SAME excludes — otherwise the manifest expects slow/e2e nodeids that the pmv run never produces and every merge reds on vanished nodeids. **Cycle-4 P2-11 — the marker filter rides too:** the pmv run applies `-m 'not track_b'` (post-merge-validation.yml:318, VERIFIED: `pytest tests/ -v --timeout=300 -p no:cacheprovider -m 'not track_b' --ignore=tests/e2e $SLOW_IGNORES ...`), so the manifest generation MUST pass the same `-m 'not track_b'` — a manifest built without it expects every track_b nodeid the pmv run deliberately deselects, and every merge reds on vanished nodeids.
**Step 2:** Review `e2e/hosted/test_12_selfhost_migration.py` (open question 4): selfhost path is docker FalkorDB — verify it runs under URI; fix drift if it hardcodes embedded paths; migrate it or document the carve-out decision.
**Step 3:** File the D1–D16 change list as the canonical divergence doc (epic indicator #3).
**Step 4:** Demote the reaper: CI drops its correctness dependency; keep local-dev hygiene (conftest sweep + cron for dev boxes).
**Step 5:** End-state verification (the O/I/T pass): orphan count < 20 without reaper (dev machine); docker-share count; 5+ consecutive green runs; wall measurement → matrix-merge decision (D-3).
**Step 6:** Commit each step; final: `git commit -m "chore(testdb): phase-4 allowlist shrink + reaper demotion (epic #1647 P4)"`.

---

## Phase 4 Gate (P4 acceptance — the epic's end state)

1. Allowlist enforcement passes with ~21 entries; 13 migrated files on docker with no embedded markers.
2. Default `pytest` requires `TORTOISE_DB_URI`; carve-out is the sole embedded surface.
3. Orphans < 20 on a dev machine without the scheduled reaper (end-state re-measure).
4. All 4 epic indicators green (see Verification below).

---

## Failure Modes

| Failure scenario | Expected behavior | Test / guard |
|---|---|---|
| Redirect fires in prod code when `TORTOISE_DB_URI` is set (path construction in prod: backup.py:134/144, ingest.py:471, `__main__.py:13` rebuild, migrate_db.py:75/184, hosted_api.py:6254/8263/8357, pipeline_cli.py:139) | **plan-review P0-4/P1-8:** the redirect is gated on `TORTOISE_TEST_MODE=1` (conftest-only) — prod never redirects, writes can never land invisibly on a server-mode `test_` graph, and `skip_health_check`/`allow_nonstandard_path` are preserved on the redirect path. The old row claimed the graph guard sufficed — it only blocks wipes, not writes. **Cycle-3 P1-3:** the TEST_MODE pop lives ONLY in `if __name__ == "__main__":` blocks / subprocess launchers (never module import, never a bare `main()` a test can call in-process — a mid-session pop kills the redirect → wrong-backend green); hosted_api is exempt from the pop list (no `main()`/`__main__`; its path= sites are embedded-only-guarded) | `tests/test_redirect_seam.py::test_no_redirect_without_test_mode` + `test_no_arg_does_not_redirect` |
| `wipe_server()` misclassifies a real graph as test-prefixed | Exact prefix filter; fail-closed skip; non-loopback refusal. The non-loopback unit test constructs with `skip_health_check=True` (plan-review P2-10 — the constructor's health probe raises before `wipe_server` otherwise) | `tests/test_wipe_server.py` |
| A half-b file's assertions are embedded-calibrated and break on docker | Divergence-confirmation pass is the gate; docker-calibrated expectations added (same class as D9); the documented change list absorbs them, not silent fixes | Task 8 Step 4 + E2E-8 |
| Missing docker service → whole half fails/skips | Skip-guard coverage manifest → red (fail-closed); never green-skip | `tests/test_skip_guard.py` manifest cases |
| Redirect inert + embedded succeeds → job green on the wrong backend | Backend-identity tripwire fails the session at start | `tests/test_backend_identity_tripwire.py` |
| Redirect fires in a subprocess CLI child (URI+TEST_MODE inherited via `os.environ.copy()`, e.g. test_export_cli's `python -m tortoise export`, redis-guard fixture scripts) | Redirect gated on a test frame being present — `_caller_test_stem() is None` → no redirect; the child keeps the embedded/CLI lane (cycle-2 P1-1b); prod entry points pop TEST_MODE at startup (P2-10) | `tests/test_redirect_seam.py::test_no_test_frame_in_stack_no_redirect` |
| Typo'd / shared (non-loopback) `TORTOISE_DB_URI` → every migrated construction writes `test_*` graphs on a remote server before any wipe refuses | Redirect + session-start tripwire refuse BEFORE the first write (loopback predicate; `TORTOISE_TEST_ALLOW_REMOTE=1` escape); `wipe_server`'s D-4 refusal is the third line (cycle-2 P0-2). **Cycle-3 P2-6: the escape is WRITE-ONLY** — it unlocks redirect+write but explicit wipes still refuse non-loopback, so a `TORTOISE_TEST_ALLOW_REMOTE=1` session is assert-only/read-only by construction (any `_wipe_or` raises); remote wipes would need a separate explicit opt-in, never folded into the write escape. **Cycle-4 P1-8: the SESSION-END/STALE/ATEXIT SWEEPS SKIP (log-and-continue) on non-loopback — they do NOT raise** — so a read-only ALLOW_REMOTE session passes its tests AND teardowns green (D-4's `RuntimeError` remains ONLY for explicit `wipe_server()`/`_wipe_or` calls; the old "wipes/sweeps still refuse" wording made every ALLOW_REMOTE session red at teardown) | `tests/test_redirect_seam.py::test_uri_set_refuses_non_loopback_host` + Task 4 tripwire (zero graphs created) + `test_allow_remote_session_teardown_green` (Task 2 Step 7) |
| Path-less job URI → `from_uri` resolves default graph `tortoise` → `DETACH DELETE` trips the guard | Job URI carries a test-prefixed path (`/tortoise_test_matrix`); URI-default DETACH sites verified/normalized in Task 6 Step 3 (cycle-2 P1-2) | P2 half-b run (Task 6) |
| Two concurrent URI sessions on one docker → session A's end-sweep drops session B's live graphs | **Per-session created-graph JOURNAL** (token-named file in `ACTIVE_SUITES_DIR` — the redirect/seam append each derived/verbatim name; cycle-3 P1-8: token-matching `list_graphs()` is impossible because derived names are hash12(session+path), not invertible, and nonce names use `os.urandom` — the journal is the source of truth) + per-test wipes scoped to the session's created-set (cycle-3 P1-7: `_wipe_or` passes the scope; the server-global sweep runs ONLY at session-end/last-suite-standing) + defer-full-sweep-to-last-suite-standing + session-start stale sweep reading DEAD sessions' journals + atexit fallback (mirror `_redislite_hygiene`; cycle-2 P0-3/P2-1/3) | Task 2 Step 7 + `test_concurrent_suite_end_sweep_leaves_other_suite_graphs` (unit, cycle-3 P2-13) + two-process integration |
| Hidden cross-file state on the shared docker graph | Per-test graph names (exact-set tier) + filtered `wipe_server()` (shared tier); guard makes bare-`test` wipes impossible | E2E-2 |
| Half-b wall does not improve (graph creation cost offsets spawn savings) | Measured at P2 gate; wall ≥ baseline +20% blocks P3 pending wipe-cost tuning | E2E-5 |
| CI service instability (falkordb container flake) | Services already health-checked (`redis-cli -a falkordb ping`); guard flips red on skip; visible infra failure, not silent green | workflow health checks |
| A drift-registered file is less DB-agnostic than the audit believed | P2/P3 already ran these files on docker (they're in the matrix); P4 move is a registry update, not a first run | Task 10 Step 1 |
| Reaper demotion strands dev-machine orphans | conftest `_redislite_hygiene` still sweeps embedded sessions; reaper cron stays for dev boxes — only CI's correctness dependency is removed | E2E-7 |
| Allowlist enforcement test breaks on the smaller list | Test reads the list from source; the shrink is a list edit — passes once the list matches reality | `test_no_new_raw_embedded_constructions` |

---

## Verification — mapping to the epic O/I/T

| Indicator | Verification | Where |
|---|---|---|
| **O1** — default run uses `TORTOISE_DB_URI` for DB-agnostic tests; embedded only for the carve-out | P3/P4: CI fast matrix env has job-level URI; dedicated URI-unset `TORTOISE_TEST_CARVE_OUT=1` job runs exactly the 342-test carve-out; conftest fails URI-less non-carve-out sessions (P1-9 enforcement); measured docker share ≈95% (6,495/6,837) vs target ≥90% | P3 Gate 1, P4 Gate 2 + `tests/test_embedded_lifecycle.py::test_no_new_raw_embedded_constructions` + conftest enforcement check |
| **O2** — zero orphan accumulation without the scheduled reaper | Baseline 4 orphans (post-#1645, precondition met, research-brief §5.1) → P4 end-state re-measure < 20 without reaper; E2E-7 (~0 on docker halves) | P4 Gate 3, E2E-7 |
| **O3** — no UNEXPECTED divergence; explicit documented change list | E2E-8 conformance file asserts D1–D16 in both modes; P2 divergence-confirmation log records observed-vs-predicted; zero unexpected = P2 gate; `docs/divergence-change-list.md` filed at P4 | Task 8, E2E-8, P2 Gate 3 |
| **O4** — reaper scope shrinks to hygiene; allowlist shrinks; no CI/dev dependency on reaper | P4: allowlist 34 → ~21; reaper demoted (CI drops correctness dependency); docker halves produce no orphans | Task 10, E2E-7 |
| **T1** — ≥90% of 6,837 tests on docker by default | Measured share = 1 − 342/6,837 = **95.0%** (carve-out verified, research-brief §4) — count at P4 Gate | P4 Gate 2 |
| **T2** — 0 flaky failures attributable to divergence in 5+ consecutive CI runs | P3 Gate 3: 5 consecutive green runs with the manifest + tripwire active; flake triage against the D1–D16 table | P3 Gate 3, E2E-6 |
| **T3** — orphans < 20 without reaper | Baseline 4 ✓ (measured at migration start, precondition); P4 re-measure | research-brief §5.1, P4 Gate 3 |
| **T4** — fast-matrix wall ≤ 20% regression; split only if a half exceeds ~40m | Baseline a=41–42m / b=57–58m; P2 half-b wall (expect drop) + P3 full-wall recorded; matrix-merge decision (D-3) uses measured values | E2E-5, P3 Gate 1, Task 10 Step 5 |

**Phase gate chain:** every phase gate above is hard — a failed gate blocks the next phase. P2 half-b wall and P3 full-matrix wall feed the D-3 merge decision; the divergence-confirmation log feeds indicator O3.

---

## Deferred sub-decisions (research-brief §9, plan-time hooks)

- **test_flip_gate partial migration:** delete-registry whitelist logic is DB-agnostic (graph-name-based) but seeded on embedded DBs — P3 decision, default keep whole-file in the carve-out (research §9.2).
- **test_pre_migration_safety partial:** has a `docker://` branch at L62-63 — P3 decision whether the parity_sample/snapshot dry-run can go docker; safe default = carve-out (research §9.3).
- **e2e/hosted/test_12_selfhost_migration:** P4 drift-fix vs carve-out (Task 10 Step 2).
- **TORTOISE_FAST_ATEXIT env:** keep set unconditionally (harmless no-op on docker) — decided at Task 9 Step 3 (gated to embedded sessions).
- **Numeric calibration candidate pre-list:** EP family (test_ep_directional, test_directional_impl, test_directional_impl_fix, test_ep_nary_falsification, test_ep_quadrature, test_ep_calibration), cross-lens, recall-gap family — docker-calibrated expectations at P2 divergence pass (research §2.3).
- **PASSED/SKIPPED observed-set source (plan-review P0-3):** junitxml is authoritative (lossless per-testcase nodeid + reason, `-o junit_family=xunit1`) — the old `-v`-lines decision is superseded; the `-r fEs` summary remains for human logs.

---

## Plan-Review Changelog (cycle 1 → fixed plan)

All P0/P1 issues from plan-review cycle 1 (3 reviewers) plus the actionable P2s. Each row: issue → fix → where in this plan.

| ID | Severity | Issue (reviewer finding) | Fix applied | Where |
|---|---|---|---|---|
| P0-1 | P0 | Carve-out exemption keyed on `os.path.basename(path)` (DB-file basename) vs Task 5's TEST-FILE stems — never matches (carve-out files use arbitrary DB names: `c.db`, `fresh.db`, `solo.db`…); the plan's own unit test masked it; relative-path interaction undecided | Exemption re-keyed on the **caller test module** via frame inspection (`_caller_test_stem()`); DB-file stems never exempt (regression test `test_db_file_stem_never_exempts`); exempt stems keep the relative-path reject (reject is skipped only on the redirect path, which is server-safe); redis-guard fixture scripts covered via a `tools/redis-guard.py` URI pop (Task 5 note); unit test rewritten to caller-module semantics | Task 1 (acceptance, Step 1 tests, Step 3 impl + helpers), Task 5
| P0-2 | P0 | No task migrates `wipe()` callers → `test_projection._shared_proj()` per-test `_wipe()`, test_backup_sweep (22), test_projection_version_gate (6), test_analyze (3), test_1162_add_operator_local_svbp, test_github_connector raise RuntimeError on docker; E2E-2's gate depends on it | New `_wipe_or(proj)` dispatcher in `tests/_embedded.py` (embedded → `wipe`, server → `wipe_server`) + explicit caller-conversion step (Task 2 Step 6) covering all 6 files | Task 2 (acceptance, Step 3 impl, Step 6)
| P0-3 | P0 | Manifest nodeids (collect-only, full) vs `-r fEs` reasoned-skips (file:line) NEVER match → the 3 `embedded_only` marker skips guarantee P2 red; the plan's unit test wrote file:line into the manifest (masked) | junitxml is the AUTHORITATIVE observed PASSED/SKIPPED+reason source (`--junitxml` + `-o junit_family=xunit1`; nodeid reconstructed from `file`/`classname`/`name` — verified against the real repo format); `-r fEs` demoted to human logs; unit tests rewritten to REAL junitxml fixtures; `test_workflow_keeps_rs` gains junitxml assertions; missing-junitxml+manifest → red flip kept | Task 3 (intent, acceptance, Step 1/3/4)
| P0-4 | P0 | Prod `path=` constructions (backup.py:134/144, ingest.py:471, `__main__.py:13`, migrate_db.py:75/184, hosted_api.py:6254/8263/8357, pipeline_cli.py:139) silently redirect when `TORTOISE_DB_URI` set (canonical in `.env.example`) — writes land invisibly; guard only blocks wipes, not writes; `skip_health_check` not preserved | Redirect gated on `TORTOISE_TEST_MODE=1` (conftest-exported test-session signal) — prod never redirects; fall-through design preserves `skip_health_check`/`allow_nonstandard_path` and reuses the host branch (no `cls(...)` recursion); unit test `test_no_redirect_without_test_mode` | Task 1 (intent, acceptance, Step 1/3), Failure-Modes table
| P1-4 | P1 | Task 7 census wrong: test_projection has ~10 `graph_name="test"` sites (35 total across 6 files, not 9); g_rebuild*/g_consistency/parity tests assert embedded recovery from a DB-agnostic file | Corrected census (verified by grep): 35× `graph_name="test"` (test_projection 21), 2× `tortoise` (projection:2162, remove_context_migration:333), 1× `crash_live` (import_endpoint:724); 27 migrated rename sites across 5 files; g_rebuild*/g_consistency/parity classified MIGRATE (name-only) — verified they exercise `rebuild()`/`rebuild_all()`/`check_consistency()`, NOT the D2/D3 `_auto_health_recover` branches (those live in test_ops_safety, carve-out); sweep extended to all migrated files | Task 7 (acceptance, Step 1 census, Step 4)
| P1-5 | P1 | `provision_test_user` namespace `e2e-tests` → `team_e2e-tests` shared non-test graph | Namespace swept to guard-passing per-test `test_e2e_<uuid>` (P1 diff-review checks no embedded assertion depends on the old name) | Task 1 (Step 3 seam fixtures paragraph) — see also P0-2 file list
| P1-6 | P1 | E2E-2/E2E-3 P2 gates reference surfaces not in the P2 flip (test_projection/test_search_engine_gaps/test_index_surfacing/test_about_event_untangle are half-a/slow; test_embedded_concurrency is slow — verified via `ci_selection` this pass) | Gates relabeled honestly (P2 docker smoke via Task 6 Step 3 pre-flight → P3 CI-wide); the pre-flight now explicitly runs the wipe-heavy + live-writer surfaces; P2 Gate rows 5–6 updated | E2E catalog, Task 6 Step 3, P2 Gate
| P1-7 | P1 | CI wiring is push-matrix-only; tier-1/tier-2 PR runs get the URI but a mismatched manifest | Manifest generated from the SAME `$FILES` the run uses (echoed to `${RUNNER_TEMP}/pytest-files`); guard gated on rc==0 AND non-empty `$FILES`; half-b URI scoped to `full==true` (run-step-local `URI` var, since the matrix include can't condition on `full`) | Task 6 (acceptance, Step 1/2)
| P1-8 | P1 | Failure-mode prod-safety claim false (redirect mints guard-passing names, bypasses relative-path reject) | Covered by P0-4's TEST_MODE gating — row rewritten with the verified prod construction sites | Failure-Modes table
| P1-9 | P1 | P4 "default pytest requires TORTOISE_DB_URI" has no enforcement; post-merge-validation unaddressed | Enforcement spec: conftest session-start fail when URI unset unless `TORTOISE_TEST_CARVE_OUT=1`; post-merge-validation.yml wired (job URI + junitxml manifest); local-dev path documented (Task 1 Step 5 note, README) | Task 10 (acceptance, Step 1a), Task 1 Step 5
| P2-10 | P2 | `test_wipe_server_refuses_non_loopback` can't pass — constructor raises before `wipe_server` | `skip_health_check=True` added to the construction | Task 2 Step 1
| P2-11 | P2 | `_is_supported_uri_scheme` doesn't exist (only `_validate_uri_scheme`, which raises) | Single shared predicate using `_SUPPORTED_URI_SCHEMES` (`_is_supported_uri_scheme` in projection; `is_db_uri` from config reused by conftest/tripwire) | Task 1 Step 3 helpers, Task 4
| P2-13 | P2 | `:memory:` paths redirect → derived `test_:memory:_<hash8>` identical every time | Special-cased: per-construction unique `test_memory_<nonce>` graph + unit test | Task 1 (acceptance, Step 1/3)
| P2-14 | P2 | Server graph accumulation on long-lived dev docker | Session-end server sweep fixture in conftest (URI set only; same `test_`-prefix filter as `wipe_server` via a shared helper) | Task 2 (acceptance, Step 7)
| P2-16 | P2 | `has_falkor()` probe corrupted under URI set (probe redirects, mints a `test` graph) | URI branch evaluated BEFORE `has_falkor`; `has_falkor()` short-circuits True under URI → migrated files never vacuous-return | Task 1 (acceptance, Step 3 fixtures paragraph)
| P2-17 | P2 | `test_audit` (half a) marker only activates at P3 | Timing noted; Task 5 Step 3 verifies all 3 markers with URI set explicitly regardless of CI surface; P2 Gate row 5 caveat | Task 5, P2 Gate

---

## Plan-Review Changelog (cycle 2 → fixed plan)

Second review pass (failure-drive + structural + integration). All P0s/P1s fixed; actionable P2s fixed in-line. Verified against the worktree code before editing (`registry_control_plane`/`team_team_x` at test_backup_sweep L47/49; `_tmp()` mkdtemp per-call paths; parity pair L1866/1867; `from_uri` default graph L566-567; 21× `graph_name="test"` in test_projection; `os.environ.copy()` children in test_export_cli/test_remove_context_migration/test_flip_gate; `run_guard(log_text)` 10 call sites (cycle-3 P2-10); `_redislite_hygiene` active-suite registry conftest L151-307; `--register` in tools/ci_selection.py L456).

| ID | Severity | Issue (reviewer finding) | Fix applied | Where |
|---|---|---|---|---|
| P0-1a | P0 | `wipe_server`'s test-prefix filter breaks `test_backup_sweep`'s non-test graphs: `_make_env` seeds `registry_control_plane` (L47) + `team_team_x` (L49) on the shared projection, relying on per-test all-graphs `wipe()`; `_wipe_or`→`wipe_server` skips them by design → leftover Team nodes survive → order-dependent pollution of `test_enumerate_teams_returns_registry_ids`/`test_sweep_no_teams_is_signal_not_incident`. The P0-2 conversion silently narrowed wipe semantics | Backup fixtures route registry/team graph names through a per-test config seam (`test_registry_<uuid>`/`test_team_<uuid>_tortoise`) — the P1-5 per-test-unique-name pattern; docker-lane unit test proves team/registry isolation across two sequential tests | Task 2 (acceptance, Step 1 tests, Step 6b)
| P0-1b | P0 | Task 7's shared rename (`test_<file>_suite`) destroys per-path isolation: test_projection's 21 sites construct per-call UNIQUE paths (`_tmp()` = mkdtemp); one shared server graph collapses the apply-vs-rebuild PARITY test (L1864-1900) into a graph-vs-itself vacuous pass (#942 class); `g_consistency_ok/bad` becomes order-dependent | Redirect derives PER-PATH names for explicit non-guard-passing names (`test_<stem>_<hash8(session+path)>`); test-prefixed explicit names are the shared opt-in (honored verbatim); Task 7 renames become unnecessary for derived sites (embedded lane untouched); redirect unit test: distinct paths + same explicit non-default name → DISTINCT graphs; same path → shared | Task 1 (acceptance, Step 1/3), Task 7 (acceptance, Step 1/4)
| P0-2 | P0 | Non-loopback URI redirect writes to a remote server BEFORE `wipe_server` refuses: a typo'd `TORTOISE_DB_URI` → every migrated construction mints+writes `test_*` graphs on the remote; D-4's protection fires only at the first `_wipe_or`, after pollution | Loopback check added to the redirect itself (fail before the first write) AND to the session-start tripwire (fail before ANY write) via one shared `is_loopback_uri`/`_is_loopback_host` predicate; `TORTOISE_TEST_ALLOW_REMOTE=1` escape; unit test points a 1-test session at a fake remote host → locality error + zero graphs created | Task 1 (acceptance, Step 1/3 helpers), Task 2 (wipe_server reuses predicate), Task 4 (tripwire), Failure-Modes table
| P0-3 | P0 | Session-end server sweep has no concurrent-suite coordination: session A's end-sweep drops session B's live `test_*` graphs (the embedded `_redislite_hygiene` has an active-suite registry + defer-to-last-suite-standing; the sweep mirrored only the filter) | Sweep rewritten: session-scoped created-graph set (session tokens in `test_suite_<uuid>`/`test_memory_<nonce>`/derived names), full sweep deferred to last suite standing, session-start stale sweep + atexit fallback (mirror `_redislite_hygiene`); two-process verification | Task 2 (acceptance, Step 7), Failure-Modes table
| P1-1a | P1 | `bench/test_smoke_embedded` rides fast half b but is missing from the P2 exemption (asserts `db_mode == "embedded-falkordblite"`, L47) — not exempted → redirects → assertion fails → half-b red | `test_smoke_embedded` added to the P2 `TEST_NO_REDIRECT_STEMS` (6→7 stems; path corrected to `tests/bench/`); pre-flight env updated | Task 5 (acceptance, Step 2), Task 6 Step 3, Task 9 carve-out list
| P1-1b | P1 | Subprocess CLI children redirect: `test_export_cli`/`test_remove_context_migration`/`test_flip_gate` run `python -m tortoise ...` with `os.environ.copy()`; the child inherits TEST_MODE+URI, has no test frame → `_caller_test_stem()` None → redirect fires → CLI silently tests the server lane | Redirect gated on a test frame being PRESENT (`stem := _caller_test_stem(); if stem is not None and stem not in _no_redirect`); unit test for URI+TEST_MODE with no test module in the stack (subprocess `-c` probe stays embedded); Task 4 tripwire probe re-keyed to `from_uri(uri, graph_name="test_tripwire_probe")` (its conftest frame → None would now correctly stay embedded) | Task 1 (acceptance, Step 1/3 + `_caller_test_stem` docstring), Task 4 (Step 1/3), Failure-Modes table
| P1-2 | P1 | Path-less job URI trips `_assert_test_graph` on the URI-default graph: `from_uri` resolves no-path → `tortoise` (projection L566-567); `from_uri(os.environ["TORTOISE_DB_URI"])` + `DETACH DELETE` sites (test_search_engine L303/304+L413/414+L497, test_ingest L531/587/625/660/695, test_hnsw_vector_index) trip the guard on the half-b flip | Job URI gains a test-prefixed path (`docker://:falkordb@localhost:6379/tortoise_test_matrix`) — one change fixes every URI-default DETACH site; Task 6 Step 3 adds a grep/normalization check for stragglers; failure-mode row | Task 6 (acceptance, Step 2/3), Failure-Modes table
| P1-3 | P1 | New test files (test_redirect_seam, test_wipe_server, test_markers, test_backend_identity_tripwire, test_divergence_conformance, test_round_trip_parity) never registered in `config/ci-surfaces.yml` → the `--integrity` drift gate reds | `python3 tools/ci_selection.py --register --surface core` added to every creating task's commit step (verified: `--register` exists at tools/ci_selection.py L456) | Task 1 Step 6, Task 2 Step 8, Task 4 Step 5, Task 5 Step 4, Task 8 Step 3
| P1-4 | P1 | Explicit non-test graph names/namespaces missed by Task 7: test_pack_state L306 `graph_name="team_team-k"` + `namespace="team-a"` (half-b), test_invites_http `namespace="registry"` → `registry_tortoise` shared by 36 tests (half-b), test_m1 `graph_name="t"` (half-a) — `wipe_server` skips them → cross-test pollution | Census extended (Step 1 greps); namespaces routed to per-test `test_*` namespaces (`registry` → `test_registry_<uuid>`; `team-a` → `test_<file>_<uuid>`); `t`/`team_team-k` fall under the P0-1b per-path derivation; wipe_server skip-behavior asserted on the swept names | Task 7 (acceptance, Step 1/4/5)
| P1-5 | P1 | E2E-1 (`tests/test_round_trip_parity.py`) listed as a gate with no owning task | Folded into Task 1 as Step 5b (it verifies the Task 1 seam in both modes); P1 Gate unit-surface list renumbered to include it | Task 1 Step 5b, P1 Gate item 3, E2E catalog
| P2-1/3 | P2 | Session-end sweep needs the full `_redislite_hygiene` hygiene shape: session-start stale sweep + atexit fallback; derived names must be session-distinct (same fixed path in two sessions → same graph name → collision) | Step 7 rewritten (session-start stale sweep, atexit fallback, last-suite-standing); derivation folds the conftest-exported `TORTOISE_TEST_SESSION` nonce into the hash input (still 8 hex — hash is session+path) | Task 2 Step 7, Task 1 Step 3 (derivation), Task 1 seam-fixtures paragraph
| P2-2/4 | P2 | junitxml reader must use `xml.etree.ElementTree`: entity-escaped ids (`&quot;`/`&lt;`) in `name`/`classname` are mangled by a regex reader → manifest reconciliation silently misses nodeids | Reader specified on ElementTree; unit fixture `JUNIT_ESCAPED` + `test_manifest_escaped_ids_parse` | Task 3 (acceptance, Step 1/3)
| P2-5 | P2 | Changing `run_guard(log_text)`'s signature breaks existing test_skip_guard tests | `run_guard(log_text)` kept byte-for-byte; NEW `run_guard_with_manifest(log_path, manifest=None, junit=None)` added; new tests use it (cycle-3 P2-10: call-site count corrected 12 → **10**, verified) | Task 3 (acceptance, Step 1/4)
| P2-6 | P2 | E2E-6's outage simulation can't go red as written (a URI-less run is the carve-out shape and passes on embedded) | Reworded to "docker service DOWN with URI set" → FalkorDB-reasoned skips → guard red; added `TORTOISE_TEST_EXPECT_URI=1` session signal (CI docker halves fail a URI-less session at start) | E2E-6 catalog row, Task 4 (acceptance, Step 3), P2 Gate item 6
| P2-7 | P2 | `wipe_server` swallows per-graph failures (`except Exception: pass`) — a failed DETACH silently breaks the hermeticity claim | Failures collected + re-raised as `RuntimeError` naming the graph; wipe-completeness unit test (no `test_`-prefixed graph retains nodes post-sweep); injected-failure unit test | Task 2 (acceptance, Step 1/3)
| P2-8 | P2 | Canary-drop N=5 is uninstrumented (nobody can tell which runs counted) | Streak artifact `config/testdb-canary-streak.json` (`consecutive_green`); defined breakers: guard red, manifest red, divergence-attributable failure, docker infra flake — any resets to 0; drop gated on `>= 5` | Task 9 Step 6
| P2-9 | P2 | `TEST_NO_REDIRECT_STEMS` has no registry test — a stale/typo'd stem silently fails to exempt | `test_no_redirect_stems_exist_as_modules` mirrors `test_no_new_raw_embedded_constructions` (every stem resolves to a module; P3+ every carve-out file's stem present) | Task 5 (acceptance, Step 1)
| P2-10 | P2 | TEST_MODE leaks into prod-role subprocesses (test_hosted_api server; test_mcp_server pops URI but not TEST_MODE, L946-948) | Prod-role entry points pop `TORTOISE_TEST_MODE` **in their `if __name__ == "__main__":` blocks / subprocess-launcher paths ONLY** (cycle-3 P1-3: never at module import or in a test-callable `main()`); test_mcp_server's env-pop loop gains TEST_MODE. **Cycle-3 P2-8: the "tools/redis-guard.py already pops both (cycle-1)" claim is DROPPED — redis-guard.py is a STATIC SCANNER with no fixture-executor code (verified); the frame gate covers the fixtures, no pop needed** | Task 1 (acceptance, seam-fixtures paragraph), Task 5 (redis-guard note)
| P2-11 | P2 | Connection exhaustion: the redirect must not route through RAW `falkordb.FalkorDB` (no GC lifecycle) | **Cycle-3 P1-4 re-scope (the cycle-2 fix was based on a false premise):** the host branch IS the raw `falkordb.FalkorDB` (projection/__init__.py:380-381, verified); the guarded subclass is embedded-only. The redirect inherits the host branch's lifecycle — no worse than today's `from_uri` sites; `FalkorProjection.close()` disconnects the pool (L1547-1568); boundedness test asserts POOL RELEASE (the old `conn.closed`/`conn.connection` asserts were vacuous — neither exists on redis.Redis) | Task 1 (acceptance, Step 1/3)
| P2-12 | P2 | Job-wall vs pytest-step-wall: collect-only manifest generation inside the pytest step consumes the 55m watchdog budget; E2E-5 measured the wrong wall | Manifest generation moved to its own step (cached collect-only keyed on a `$FILES` hash); E2E-5's wall = job wall (`timeout-minutes: 60`) | Task 6 (acceptance, Step 2), E2E-5 catalog row
| P2-13 | P2 | `_live_utils` exclusion can't survive junitxml: the skip's `file` attribute becomes the CALLING test file, so the location-based exclusion stops matching | Exclusion re-keyed to the reason-family prefix `"requires TORTOISE_DB_URI"` (the `_skip_unless_live_uri` reason string, verified in tests/_live_utils.py L25-26) | Task 3 (acceptance, Step 3)
| P2-14 | P2 | post-merge-validation manifest must replicate its own ignore set — pmv runs `--ignore=tests/e2e` + `$SLOW_IGNORES` (29 slow files — cycle-3 P2-11: verified count, the "21" undercounted); a manifest without those excludes reds on vanished nodeids every merge | pmv manifest generation uses the SAME ignores as its pytest run | Task 10 Step 1a

### Good-vs-Easy deferrals (cycle 2 — rule 4/5 records)

| Issue | Chosen (Good) | Deferred alternative | Cost of deferral | Rationale |
|---|---|---|---|---|
| P0-1a backup fixtures | Per-test config seam (registry/team names routed through `test_registry_<uuid>`/`test_team_<uuid>_tortoise`) | (a) `wipe_server` takes an explicit test-owned-graphs argument; (b) carve out test_backup_sweep | (a) threads a new parameter through every wipe caller for ONE file's fixtures; (b) loses the sweep's docker coverage | The seam extends the already-shipped P1-5 pattern (provision_test_user) — one file's fixture, zero new wipe-server surface, sweep stays migrated |
| P0-1b explicit-name derivation | Per-path derivation in the redirect (non-guard-passing explicit names derive; test-prefixed = shared opt-in) | Rename all 21 sites to a shared `test_<file>_suite` graph by hand | Shared rename collapses per-call-unique-path constructions onto one graph → parity test compares a graph to itself (vacuous) | Derivation is one branch in the redirect and fixes every site uniformly, including future ones; embedded lane unchanged |
| P1-2 path-less URI | Test-prefixed job URI path (`/tortoise_test_matrix`) + verification grep | Per-site normalization of every `from_uri(os.environ[...])` DETACH site | Per-site edits are drift-prone (new sites appear); the path-ed URI fixes the whole class with one CI change | The URI path IS the default graph name — naming it guard-passing is the single point of control |
| P0-3 session sweep | Session-scoped created-graph set + defer-full-sweep-to-last-suite-standing (mirror `_redislite_hygiene`) | Blind full sweep with a small sleep/retry | Concurrent sessions race; a fixed delay is a flake masquerading as coordination | The embedded hygiene already solved this exact problem — reuse its registry, don't invent a weaker one |

### Good-vs-Easy deferrals (rule 5 — explicit records)

| Issue | Chosen (Good) | Deferred alternative | Cost of deferral | Rationale |
|---|---|---|---|---|
| P0-1 exemption key | Caller-test-module via frame inspection | Conftest-injected per-call module allowlist plumbing | Requires threading an allowlist through every construction/fixture call site; frame inspection is self-contained at the seam | Frame inspection is one helper + one lookup; an injected allowlist would touch the seam fixtures AND every raw construction path for no robustness gain |
| P0-3 reconciliation | junitxml authoritative (lossless nodeid + reason) | Resolve `file:line` → nodeid against collect-only with count handling | collect-only emits no line numbers; `[N]` count disambiguation across parametrized cases is fragile; still leaves the reason source truncated | junitxml is deterministic, terminal-width-independent, and verified against the real repo format; the file:line route adds machinery for a worse guarantee |
| P0-4 prod gate | `TORTOISE_TEST_MODE` test-session gate | Prod-side allowlist of never-redirect paths | Prod allowlist must track every future `path=` construction (drift-prone); cannot catch new prod tools | The test-session signal is one env line in conftest with ZERO prod surface; prod tools are correct by construction |
| P1-6 P2 gates | Honest relabel + bounded docker pre-flight smoke | Flip a bounded test-slow leg to docker in P2 | Extra CI minutes + changes the P2 wall measurement baseline | The pre-flight smoke covers the gate's intent (wipe + concurrency on docker) without altering the matrix shape or the P2 half-b data point |

---

## Plan-Review Changelog (cycle 3 → fixed plan)

Third review pass (3 fresh reviewers). Every P0/P1 fixed; every actionable P2 fixed in-line; the rest are explicit Good-vs-Easy deferrals. **All code claims verified against the worktree before editing** (redis-py 8.1.0 `.connection` attr shape; `from falkordb import FalkorDB` at projection/__init__.py:380-381; `_falkordb_version_cache_key` at L999; hosted_api no-`main`/`__main__` + conftest L337 import; backup_sweep `team_graph_name` :190/:514 + `_backup_team` select_graph :264; sdk.py `_get_proj` no-namespace branch L1123; test_backup_sweep select_graph census incl. L452/475/510/533/538/603/606/744 + assert :745; test_pack_state team-c L88 + team-a L118/126; `run_guard(` 10 call sites; slow_files = 29; push_extra distribution → half b; test_hnsw_vector_index scoped DETACHes L126/129/239; test_ingest slow; FalkorProjection.close() pool disconnect L1547-1568).

| ID | Severity | Issue (reviewer finding) | Fix applied | Where |
|---|---|---|---|---|
| P0-1 | P0 | `wipe_server` host extraction broken on the real client: `getattr(proj.db.connection, "host", None)` reads a redis.Redis (falkordb's `.connection` attr, falkordb.py:150) that has NO `.host` (redis-py 8.1.0 verified; host lives in `connection_pool.connection_kwargs['host']`) → host=None → `_is_loopback_host(None)` False → wipe_server refuses EVERY server projection including localhost → every `_wipe_or`/sweep raises; `_falkordb_version_cache_key` (L999) reads the same dead pattern | Host branch records `self._host = host` (Task 1 Step 3 edit list + host-branch note; from_uri → cls(host=...) lands there); wipe_server/sweep/tripwire read `proj._host` with fallback `connection_pool.connection_kwargs.get("host")`; **localhost-ACCEPTANCE test FIRST** (`test_wipe_server_localhost_acceptance` — from_uri localhost + wipe_server wipes, not raises) before the refusal tests; `_falkordb_version_cache_key` fixed by the same recording (`getattr(self, "_host", None)` first) | Task 1 (acceptance, Step 1/3), Task 2 (Step 1 tests, Step 3 impl)
| P1-1 | P1 | P0-1a seam routes only the SEED; consumption (the sweep's per-team graph name from `team_graph_name()` backup_sweep.py:190/:514 → `team_team_x`, then `_backup_team` select_graph :264) still dumps the EMPTY derived graph on docker while data sits on `test_team_<uuid>_tortoise` → content assertions fail at P2 (test_backup_sweep rides half b) | Seam must ALSO route consumption: docker-lane tests monkeypatch `tortoise.backup_sweep.team_graph_name` (or `enumerate_teams`) to return the seam names per test (`test_team_registry_isolation_across_sequential_tests` + new `test_sweep_consumes_seam_team_graph`); P0 guard in `_backup_team` asserted (consumed name stays `test_`-prefixed) | Task 2 Step 6b, Step 1 tests
| P1-2 | P1 | backup_sweep census incomplete (20 of 29 sites): missed L452/475/510/533/538/603/606 (select_graph), L744 (select_graph), L745 (assert team_graph_name) | Route by regex sweep (`select_graph("registry_control_plane")` / `select_graph("team_team_x")` / `team_graph_name` grep + dynamic `f"team_{team_id}"` L57 / `team_team_e` L233 / `team_myapp` L770/860 / `team_beta` L816), not an enumerated list | Task 2 Step 6b
| P1-3 | P1 | TEST_MODE pop placement dangerous: hosted_api has no `main()`/`__main__` guard; module-import placement fires in-session (conftest imports hosted_api at L337), main()-placement fires in-process (tests call main()s) → mid-session pop kills the redirect → wrong-backend green (#942 class) | Pops pinned to `if __name__ == "__main__":` blocks / subprocess-launcher paths ONLY; hosted_api DROPPED from the pop list (its 3 path= constructions at 6254/8263/8357 are inside embedded-only `_path` guards — verified safe); mcp_server's existing `__main__` block (L2699) is the pop site; test_mcp_server env-pop loop gains TEST_MODE | Task 1 (acceptance, seam-fixtures paragraph), Failure-Modes prod row
| P1-4 | P1 | Connection-boundedness claim FALSE: host branch is `from falkordb import FalkorDB` (raw, L380-381); guarded subclass is redislite-only; test asserts `conn.closed`/`conn.connection` on a redis.Redis — neither exists → passes vacuously | Re-scoped to "redirect inherits the existing host branch's raw-client lifecycle — no worse than today's from_uri sites"; `FalkorProjection.close()` already disconnects the pool (L1547-1568); test rewritten to assert POOL RELEASE (`connection_pool._available_connections` back to baseline) | Task 1 (acceptance, Step 1/3), cycle-2 P2-11 row amended
| P1-5 | P1 | No-namespace TortoiseSDK sites share the URI-default graph: `_get_proj()` (sdk.py L1115-1123) with URI set IGNORES db_path, resolves `urlparse(uri).path or "tortoise"` → under `/tortoise_test_matrix` EVERY no-namespace SDK shares one graph (test_projection L2043/2135/2321/2346/2507 — 5 sites, distinct _tmp paths) → exact-set assertions get cross-test data | Task 7 census gains `grep -rn "TortoiseSDK("`; no-namespace SDK constructions get a per-session default namespace (`test_sdk_<hash12(session + id(db_path))>`) OR enumerated per-test namespaces; new test: two no-namespace SDKs with distinct db_paths under URI → distinct `test_`-prefixed graphs. **Cycle-4 P0-2/P1-6 amendment (the cycle-3 fix was based on a false premise):** `id(db_path)` is a memory address (GC/reuse collapses distinct SDKs; same-value/different-instance splits them) — the hash input is the VALUE (`session + db_path`); the SDK-layer default is gated on `db_path is not None and TORTOISE_TEST_MODE=1` (an unconditional default reds `test_namespace_uri_mode`'s two no-db_path tests); the OR is resolved to the SDK-layer option | Task 7 (acceptance, Step 1/5)
| P1-6 | P1 | Seam/parity tests not lane-safe: `test_unset_uri_constructs_embedded` has NO `monkeypatch.delenv("TORTOISE_DB_URI")` (red on URI-set lanes); E2E-1's `test_round_trip_same_shape` has no env control — the claimed "parametrized URI set/unset" is prose (docker-vs-docker on a URI-set lane, never detects divergence) | Every seam/parity test self-contained: unset-assuming tests `delenv`; E2E-1 parametrized with REAL env control (embedded leg delenv, docker leg setenv); acceptance checklist item added | Task 1 (acceptance, Step 1, Step 5b)
| P1-7 | P1 | Per-test `_wipe_or` is a server-global blind wipe: session A's per-test wipe deletes session B's LIVE graphs; mid-session each wipe enumerates + DETACHes the whole session's accumulated graphs (quadratic wall) | Per-test wipes scoped to the session's created-set (`wipe_server(proj, scope=...)` — shared predicate with the session sweep); server-global full sweep ONLY at session-end/last-suite-standing; cost-bound test (~2000 pre-created test_* graphs, scoped wipe bounded) | Task 2 (acceptance, Step 3 `_wipe_or`/`wipe_server`, Step 7)
| P1-8 | P1 | Stale-sweep token matching impossible (derived = hash12(session+path) not invertible; nonce names use os.urandom, not TORTOISE_TEST_SESSION); created-set has no recording mechanism | Per-session created-graph JOURNAL (token-named file in ACTIVE_SUITES_DIR; redirect + seam append each derived/verbatim name); stale sweep reads DEAD sessions' journals; "recoverable by token-matching" claim dropped | Task 2 Step 7, Failure-Modes concurrent-session row
| P2-1 | P2 | bench/test_smoke_embedded rationale wrong (reviewer premise "push_extra unreferenced in CI" is itself wrong — `--emit-push-matrix` at python-ci.yml:193 consumes it; VERIFIED: index 3 lands in half b) but the RATIONALE was imprecise and the pmv full-`tests/` collection at P4 is a real second exposure | Rationale corrected: rides half b via the push_extra distribution (not as a classified fast file); exemption kept and explicitly covers the pmv `tests/` collection at P4 (Task 10 Step 1a) | Task 5 (acceptance), Task 10 Step 1a
| P2-2 | P2 | test_hnsw_vector_index DETACHes are STARTS-WITH/`{id:$id}`-scoped (L126/129/239, verified) — not bulk, no guard trip; it rides half **a**; test_ingest is slow (P3) — both over-catalogued as P2 URI-default DETACH risks | Removed from the P1-2 catalogue; Task 6 Step 3 check scoped to half-b bulk-DETACH sites (test_search_engine) + grep stragglers | Task 6 (acceptance, Step 3)
| P2-3 | P2 | "test_reaper 52" in half b wrong — test_reaper and test_embedded_concurrency are slow_files (verified via `--emit-push-matrix`: in NO fast half); half b = test_search_engine + test_ranking + bench | Phase-2 intro labels fixed | Phase 2 intro
| P2-4 | P2 | hashlib NOT imported in projection/__init__.py (verified L13-22) — the derivation snippet calls hashlib.sha1 | `import hashlib` added to Task 1 Step 3's edit list (re is already imported, L15) | Task 1 Step 3
| P2-5 | P2 | Derived stems can carry hyphens (`/tmp/seam-test-b.db` → `seam-test-b`); graph names are Redis keys (no documented char restriction, FalkorDB docs verified) but tooling (prefix filters, journaling, logs) is cleaner without them | Stems sanitized to `[a-zA-Z0-9_]` in the derivation; unit-test expectations updated to `test_seam_test_b_`/`test_seam_parity_a_` | Task 1 (acceptance, Step 1/3), Task 7
| P2-6 | P2 | `TORTOISE_TEST_ALLOW_REMOTE=1` lets writes through but wipes still refuse non-loopback → escape is write-only/unusable as documented | Stated as WRITE-ONLY by design: usable for assert-only/read-only sessions; remote wipes require a separate explicit opt-in (never folded into the write escape); Failure-Modes row updated | Task 1 (acceptance), Failure-Modes row
| P2-7 | P2 | `test_wipe_server_completeness` asserts NO test_ graph retains nodes server-globally — on a dev docker with leftovers this reds spuriously | Scoped to the fixture's own graphs (faked list_graphs); the session-end stale sweep owns the global cleanup and must run BEFORE this assert in CI order | Task 2 Step 1
| P2-8 | P2 | redis-guard.py is a STATIC SCANNER not a fixture executor (docstring verified: "scan repo, exit 1 on violations") — the "already pops" claim is false | Note dropped; frame gate (P1-1b) covers the fixture subprocesses; added a URI-set verification step that the relative-path reject still fires | Task 5 (redis-guard note), cycle-2 P2-10 row amended
| P2-9 | P2 | test_pack_state namespace census misses L88 (team-c) and L118/126 (team-a) (verified; team-a total L62/73/118/126/133/143/159/167) | Census completed; all 8 namespace sites route to per-test `test_*` namespaces; per-path derivation isolates the `team_team-k` arg anyway | Task 7 (Step 1/5)
| P2-10 | P2 | `run_guard(` call sites = 10, not 12 (verified: def at L55 + 10 callers) | Count corrected in Task 3 Step 1/4 + cycle-2 P2-5 row | Task 3, changelog
| P2-11 | P2 | slow_files = 29, not 21 (verified count) | Corrected in Task 10 Step 1a + cycle-2 P2-14 row | Task 10, changelog
| P2-12 | P2 | Task 9 carve-out "16" is 17 (7 Task-5 stems + 10 additions; fixtures/redis-guard/* are scripts, test_smoke_embedded already in the 7; matches research-brief §4.1 "17 files") | Corrected in Task 9 acceptance + Task 10 allowlist | Task 9, Task 10
| P2-13 | P2 | Concurrent-suite coordination has only MANUAL two-process verification | Unit test added (`test_concurrent_suite_end_sweep_leaves_other_suite_graphs` — fake list_graphs/marker dir: A's end-sweep leaves B's live graphs; B-crashed → A's stale sweep drops B's journaled graphs) | Task 2 Step 7
| P2-14 | P2 | Manifest-missing → vacuous green: `--manifest` passed but the manifest FILE absent/unreadable was unspecified | Treat absent/unreadable manifest as red when `--manifest` is passed (+ `test_manifest_file_missing_is_red`); junitxml-missing flip unchanged | Task 3 (acceptance, Step 3, Step 1 test)
| P2-15 | P2 | Canary-streak artifact write mechanism unspecified (who writes, which runs count, who classifies) | Producer (post-merge step, atomic rename), population (post-merge full-matrix only), classifier (`tools/testdb_canary_classify.py` — scripted, deterministic, buckets D1–D16 + infra) all specified | Task 9 Step 6
| P2-16 | P2 | E2E-5 measures job wall not step wall — a step silently riding the 55m watchdog is hidden | Step-wall captured in the run step (`step_wall` via in-step SECONDS) + gate `step_wall < 55m` with margin (target ≤ ~45m) | E2E-5 catalog row, Task 6 acceptance
| P2-17 | P2 | hash8 (32-bit) collision risk at multi-thousand-graph scale | 12 hex (48+ bits) in the derivation; test asserts +12 length | Task 1 (acceptance, Step 1/3)
| P2-18 | P2 | `_caller_test_stem` keys on NEAREST test_ frame — cross-test-module helpers mis-key; semantics undocumented | Nearest-frame semantics documented; cross-module pin (`test_caller_test_stem_nearest_frame_semantics` — a test_-prefixed helper resolves to ITS OWN stem; shared helpers must never be listed) | Task 1 (Step 3 docstring, Step 1 test)

### Good-vs-Easy deferrals (cycle 3 — rule 4/5 records)

| Issue | Chosen (Good) | Deferred alternative | Cost of deferral | Rationale |
|---|---|---|---|---|
| P1-3 hosted_api pop | Dropped hosted_api from the pop list entirely (its path= sites are `_path`-guarded embedded-only) | Add a serve entry + pop inside it | New prod entry surface for zero gain; the guard already contains the writes | Verified the three path= constructions cannot redirect (they only run when `_path` is set) — no pop needed |
| P1-7 cost bound | Scoped per-test wipes (session created-set) + cost-bound test | Keep the global wipe and add a graph-name prefix for session | Global wipe is the concurrency hazard by construction — no amount of naming fixes it | Scoping is one `scope` parameter shared with the journal; the global sweep stays where it belongs (session-end) |
| P1-8 journal | Token-named journal file in ACTIVE_SUITES_DIR (append-only) | Embed the session token in every graph name so token-matching works | Changing derived/nonce name formats ripples through every assertion + the redirect; still can't recover os.urandom nonces | The journal is append-only at the one construction seam — cheaper than re-plumbing every name format |
| P2-6 write-only escape | `TORTOISE_TEST_ALLOW_REMOTE=1` documented as write-only (assert/read-only sessions) | Add a separate wipe-remote opt-in now | New escape surface before any user exists; the write-escape already covers the redirect | Record the semantic; add the wipe-remote opt-in only when a real workflow needs it |
| P2-16 step-wall | Step-wall measured + gated in the run step | Restructure CI into per-step timings | Over-engineering for the single number the merge decision needs | One `$SECONDS` capture at the watchdog site already exists — expose it |

---

## Cycle-3 SUMMARY

All 8 P0/P1 issues fixed (P0-1, P1-1 … P1-8) and all 18 P2s fixed in-line; three reviewer premises were empirically corrected rather than rubber-stamped:

1. **P2-1** — "push_extra unreferenced in CI" is FALSE (verified: `--emit-push-matrix` consumes it at python-ci.yml:193; `bench/test_smoke_embedded` lands in half b at index 3). The exemption was kept with a corrected rationale + the real second exposure (pmv full-`tests/` collection at P4).
2. **P1-4** — the cycle-2 P2-11 "guarded subclass" claim was based on a false premise (host branch is the raw falkordb client, verified at L380-381); re-scoped honestly to raw-client lifecycle + pool-release test.
3. **P2-2** — test_hnsw_vector_index/test_ingest were over-catalogued as P2 URI-default DETACH risks (scoped wipes / slow_files).

Every other claim was verified exactly as the reviewers stated (redis.Redis `.host` absence, the 9 missed select_graph sites, 10 run_guard call sites, 29 slow files, hosted_api's missing `__main__` + conftest import, the no-namespace SDK graph share, hyphenated stems, 32-bit hash, the write-only escape, the vacuous boundedness test). The plan's structure (Tasks 1-10, Phase 1-4 gates, E2E catalog, Failure Modes, changelogs) is preserved; all changes are surgical in place.

---

## Plan-Review Changelog (cycle 4 → fixed plan)

Fourth review pass (3 fresh reviewers). All P0s/P1s fixed; all actionable P2s fixed in-line; the rest are explicit Good-vs-Easy deferrals. **All code claims verified against the worktree before editing** (`falkordb.FalkorDB.__init__` live round-trip at falkordb.py:132 `Is_Sentinel(conn)` → `conn.info()`; `_is_embedded` instance-attr at projection L390 (no class attr); test_helpers.py absent; team_graph_name registry branch `team_{id}` at backup_sweep.py:246 + consumption :514 + `_backup_team` select_graph :264; 27 `run_backup_sweep(` call sites / 22 registry-mode tests in test_backup_sweep; L745 real-function assert in `test_team_graph_name_reads_from_teams`; SDK no-namespace → URI-path graph sdk.py L1106-1128 + TortoiseSDK() default db_path=None L962; test_namespace_uri_mode's 2 no-namespace tests; __main__.py 6 path-construction sites L13/521/1724/1915/2165/2562 + `if __name__ == "__main__":` at the bottom; backup.py has NO main()/`__main__` (entry = tortoise CLI L4121-4127; pyproject scripts: tortoise/tortoise-ingest/tortoise-serve only); test-concurrency-falkor job URI `docker://.../tortoise` python-ci.yml L695-760 + live tests use explicit `test_live_mw_tortoise` (test_embedded_concurrency L111-153); pmv run applies `-m 'not track_b'` (post-merge-validation.yml:318); embedded marker format `{pid}-{uuid8}` + `pid=`/`start=` lines (conftest L178-192) via `tortoise.embedded_reaper` helpers; test_pack_state team-p L188 / team-k L296 / team-red L324 / team-green L345 + e2e-900 namespaces (test_index_surfacing L39, test_backfill_sources L37/902/916/959/989); `_cmd_validate` ×7-8 in test_domain_validators L543-579 + `main(["doctor"])` L207 + `main(["context"])` L45; pool-release vacuity on redis-py 8.1.0 `_available_connections`).

| ID | Severity | Issue (reviewer finding) | Fix applied | Where |
|---|---|---|---|---|
| P0-1 | P0 | `_wipe_or` scope WIRING never happened: cycle-3 added `scope=` to wipe_server/_wipe_or but Task 2 Step 6 converts 30+ call sites (test_projection._shared_proj, test_backup_sweep ×22, test_projection_version_gate ×6, test_analyze ×3) to `_wipe_or(proj)` with NO scope → scope=None → server-global blind wipe → re-opens the concurrent-session deletion hazard + quadratic wall; the cost-bound test exercises the primitive, not the wiring | Mechanism defined: module-level in-memory created-set registry in tests/_embedded.py (`_JOURNAL` + `_WIPED_UP_TO` cursor) populated by the journal appender; an autouse per-test fixture advances the since-last-wipe cursor; **`_wipe_or` defaults scope to the registry — NEVER None** (true server-global reachable only via the session-end sentinel); wiring-level test `test_per_test_wipe_or_touches_only_session_set` (foreign `test_foreign_<uuid>` + 100 unrelated `test_*` graphs survive a converted-style `_wipe_or(proj)`) | Task 2 (Step 3 impl, Step 6 note, Step 7 item 0a, Step 1 test)
| P0-2 | P0 | `id(db_path)` as the SDK namespace hash input is a memory-address collision: test 1's SDK GC'd → test 2 reuses the freed address → SAME graph → cross-test pollution; same-value/different-instance str objects → DIFFERENT graphs → write-then-read-stale within one test | Hash the VALUE: `test_sdk_<hash12(session + db_path)>` (distinct values → distinct graphs, same value → shared — the correct embedded analog); two new unit tests: (a) sequential SDKs + `gc.collect()` between → distinct graphs; (b) two constructions of the same path value (fresh str each) → same graph | Task 7 (acceptance, Step 5)
| P1-1 | P1 | falkordb constructor does a LIVE round-trip: `FalkorDB.__init__` at falkordb.py:132 `Is_Sentinel(conn)` → `conn.info()` — an eager server command BEFORE returning. `test_wipe_server_refuses_non_loopback` (host="db.internal...") and `test_allow_remote_escape_lets_non_loopback_through` raise redis.exceptions.ConnectionError (NOT RuntimeError) during construction → `pytest.raises(RuntimeError, match="loopback")` never fires on both | Stub/monkeypatch: `test_wipe_server_refuses_non_loopback` constructs `types.SimpleNamespace(_host="db.internal.example.com")` (wipe_server reads only `proj._host` before the host check raises); the escape test `monkeypatch.setattr("falkordb.FalkorDB", _FakeFalkorDB)` (stub needs select_graph + close only — host branch L380-390 + close L1547-1568 verified) | Task 1 Step 1 (escape test), Task 2 Step 1 (refusal test)
| P1-2 | P1 | Pool-release assert still inverted/vacuous: `p.db.connection` CHECKS OUT a connection (`_available_connections` == 0 before); close() → release → disconnect (does not remove connection objects) → reset (empties) → after == 0 → "0 >= 0" passes; a NO-OP close (leak) also passes (after == before) | Re-scoped to (a) weakref live-ConnectionPool count after gc.collect() (≤ 1 of 20) — the primary; (b) server-visible INFO clients `connected_clients` before/after the loop (growth ≤ 1 — what the docker job can actually observe); redis-py introspection kept secondary | Task 1 Step 1 (`test_redirect_connection_boundedness`)
| P1-3 | P1 | E2E-1 smoke assert is an AttributeError: `FalkorProjection._is_embedded` is an INSTANCE attr (set in `__init__` L390, verified) — no class attr → the assert raises on BOTH legs, making E2E-1 red in both modes | Line dropped; replaced with an instance-level probe (`probe._is_embedded in (True, False)` on a constructed instance) | Task 1 Step 5b
| P1-4 | P1 | `tests/test_helpers.py` referenced (imported by `test_caller_test_stem_nearest_frame_semantics`) but never created → ImportError at collection; and it would be the only test_-prefixed shared helper — carve-out files constructing through it resolve to stem `test_helpers` (not exempt) → redirect fires despite the exemption | Module added to Task 1 Step 3's edit list with its full spec (`construct_via_helper()` returns `_caller_test_stem()`); guard added to the P2-9 stem-registry test (`test_no_carve_out_imports_test_helpers` — no TEST_NO_REDIRECT_STEMS module's source may contain "test_helpers") | Task 1 (Step 1 test, Step 3 edit list), Task 5 Step 1 (guard)
| P1-5 | P1 | backup_sweep consumption seam covers only 2 of ~22 registry-mode sweep tests: `team_graph_name` (backup_sweep.py:190) registry branch returns deterministic `team_{id}` (L246) and `run_backup_sweep` consumes it at :514 — the two cycle-3 docker-lane tests monkeypatch it individually; the other ~20 ride half b and dump the EMPTY derived graph (seam-seeded data sits on `test_team_<uuid>_tortoise`) → red | Consumption routed FILE-WIDE: a module-scoped autouse fixture in test_backup_sweep monkeypatches `tortoise.backup_sweep.team_graph_name` to the seam constants for every sweep test (the patch can't live in `_make_env` — its monkeypatch arg is None at L162, verified); the L745 contradiction resolved explicitly: `test_team_graph_name_reads_from_teams` (tests the REAL function) is exempted from the autouse patch by name, so the real-function semantics stay covered and the patch isn't vacuous | Task 2 Step 6b
| P1-6 | P1 | No-namespace SDK default namespace: (1) the cycle-3 OR (SDK-layer default vs per-file enumeration) is unresolved — the plan's unit test only passes under the SDK-layer option; (2) an unconditional SDK-layer default reds `test_namespace_uri_mode`'s two no-namespace tests (`test_no_namespace_uses_uri_graph` → URI-path graph, `test_uri_without_path_defaults_to_tortoise` → `tortoise` — both construct `TortoiseSDK()` with NO db_path); (3) census names only test_projection's 5 (~25 sites across ~20 files) | OR RESOLVED: SDK-layer default gated on `db_path is not None and TORTOISE_TEST_MODE=1 and no namespace` (the db_path gate is what protects the no-db_path URI-graph semantics — TEST_MODE alone is set in every pytest session); expectation split: the two no-namespace tests stay as-is (their `TortoiseSDK()` constructions carry no db_path, so the gate leaves them green) + ONE new docker-session test asserts a no-namespace SDK WITH db_path derives `test_sdk_<hash12>`; census completed via `grep -rn "TortoiseSDK("` (test_suggest_entry_points 10, test_ep_operatorless 5, test_mcp_server 5, test_references_edge 10, test_integration_search 13, test_de2e1_entity_extraction 8, test_battery_setup 7, test_search_engine 7, test_pack_state 6, test_projection 5 + long tail) | Task 7 (acceptance, Step 1/5)
| P1-7 | P1 | `TORTOISE_TEST_EXPECT_URI` never wired into CI: the E2E-6 session signal exists as a Task 4 mechanism but Task 6 Step 2's workflow edits never set it on the docker-half job — CI's docker half could silently lose its URI and green-pass | `TORTOISE_TEST_EXPECT_URI: "1"` added to Task 6 Step 2's half-b include env block (same place as the URI) | Task 6 Step 2
| P1-8 | P1 | ALLOW_REMOTE sessions red at teardown: the session-end/stale/atexit sweeps "share the loopback refusal with wipe_server" → they RAISE on non-loopback → every write-escape session fails after its tests passed (sweep-vs-raise was unspecified) | Specified + tested: session-end/stale/atexit sweeps SKIP (log-and-continue, `skip_on_non_loopback=True`, journal preserved) — D-4's `RuntimeError` remains ONLY for explicit `wipe_server()`/`_wipe_or` calls; new 1-test `test_allow_remote_session_teardown_green` (stub projection, fake journal); Failure-Modes row rewritten | Task 2 Step 7 items 3-5 + Verify, Failure-Modes table
| P1-9 | P1 | DETACH-only wipes → unbounded GRAPH.LIST growth: every wipe is DETACH DELETE (empties but never removes the graph); a persistent dev docker accumulates every derived graph forever — invisible to E2E-7 (counts redislite processes, not server graphs) | Session-end + stale sweeps call `wipe_server(..., drop=True)` (DETACH then GRAPH.DELETE the journaled names; per-test wipes keep drop=False); E2E-7 gains a server-side bound: `GRAPH.LIST` count < journal size + constant (catalog row + Task 9 Step 4 + P3 Gate 4) | Task 2 Step 3/7, E2E-7 catalog, Task 9, P3 Gate 4
| P2-1 | P2 | Docker marker format unspecified: the plan said "an active-suite marker file, same pattern as `_redislite_hygiene`" without pinning the FORMAT — a divergent format breaks `active_suite_markers()` liveness (recycled-pid, start-time identity) | Docker markers REUSE the embedded format exactly: `{pid}-{uuid8}` token, `pid=`/`start=` lines, same `tortoise.embedded_reaper` helpers (`active_suite_markers`, `_process_start_time`, `ACTIVE_SUITES_DIR`) — shared code, not a parallel implementation; pinned by a parser test (a conftest-written docker marker parses through `active_suite_markers()` with the same identity checks) | Task 2 Step 7 item 1
| P2-2 | P2 | `from_uri` bypasses the journal: the URI-default graph (`tortoise_test_matrix`) is shared across sessions — a per-session DETACH of it races other sessions' live writes | In test mode (`TORTOISE_TEST_MODE=1`), `from_uri()` appends its resolved graph name to the session journal (single seam point); the per-test wipe scope EXCLUDES the shared URI-default graph (owned by session-end/last-suite-standing); from_uri census enumerated (test_live_mw_tortoise ×3, tripwire probe, tortoise_test_221_namespace, URI-default sites) | Task 2 Step 7 item 2
| P2-3 | P2 | Journal torn-writes + threaded appends unspecified: a killed writer can truncate the journal mid-line; concurrent appenders unspecified | Per-append atomicity (open/write/close per append — the atomicity boundary; no cross-append lock needed with token-named per-session files); tolerant reader (parse line-by-line, truncate at the first unparseable line; an unparseable FIRST line → treat as empty + delete — poison-file guard); stale-sweep test `test_journal_tolerant_reader_truncated_line` | Task 2 Step 7 item 2 + Verify
| P2-4 | P2 | Canary classifier omits step-wall/job outcome: `tools/testdb_canary_classify.py` reads junitxml + manifest + divergence log but never the E2E-5 step-wall gate — a run that rides the watchdog and passes green is classified as green | `step_wall` added as a MANDATORY classifier input; a step-wall-gate failure (`step_wall ≥ 55m`) resets the streak to 0 like any other failure | Task 9 Step 6
| P2-5 | P2 | `TORTOISE_TEST_SESSION` mutation has no test: the nonce folds into derived names AND the journal filename — a mid-session mutation strands this session's graphs with no probe | `test_session_nonce_is_stable_and_hex8` added (module-import snapshot `_SESSION_NONCE_AT_IMPORT` vs runtime value + 8-hex shape) | Task 1 Step 1
| P2-6 | P2 | The live-required CI job (`test-concurrency-falkor`, python-ci.yml L695-760) sets `TORTOISE_DB_URI: docker://.../tortoise` — a NON-test-prefixed path — and was never in any audit | Added to the Task 6 audit (acceptance + Step 3 grep): currently INERT (both live tests use `from_uri(uri, graph_name="test_live_mw_tortoise")` with explicit test-prefixed names, no `path=`, no URI-default DETACH — verified L111-153); the grep pins that no new construction flips it; if one does, the job URI gains the P1-2 test-prefixed path | Task 6 (acceptance, Step 3)
| P2-7 | P2 | Census misses test_pack_state team-p/team-k/team-red/team-green (L188/296/324/345, verified) + e2e-900 (test_index_surfacing L39, test_backfill_sources L37/902/916/959/989 → shared non-test `team_e2e-900`) | Census completed: 13 team-* namespace sites (not 8) + the tenant-*/t-reg-2/mcp-team/registry/t-bf-1 sites + e2e-900 routed to per-test `test_*` namespaces | Task 7 (acceptance, Step 1)
| P2-8 | P2 | `__main__.py` has 6 FalkorProjection path-construction sites (L13/521/1724/1915/2165/2562, verified) not 1; backup.py has no `main()`/`__main__` block (its backup/restore entry is the `tortoise` CLI at L4121-4127; pyproject scripts = tortoise/tortoise-ingest/tortoise-serve only, verified) — the prod-census + pop-site claims were built on a false single-site assumption | Task 1 acceptance + Failure-Modes prod row corrected to the 6-site census + backup.py entry-point reality (the pop for backup.py lives at its `tortoise` CLI subprocess-launcher path in `__main__.py`, not in backup.py) | Task 1 (acceptance), Failure-Modes row
| P2-9 | P2 | Task 2 Step 7 acceptance bullet still claims "all embed the session token" — directly contradicting the cycle-3 P1-8 journal finding (derived = hash12, nonce = os.urandom; no graph name carries a recoverable token) | Bullet rewritten: the journal is the ONLY source of truth; the token-embedding claim is dropped with the contradiction called out | Task 2 (acceptance bullet)
| P2-10 | P2 | P1-7's quadratic-wall fix overstated: scoping per-test wipes to the FULL session created-set is still O(session) per wipe → O(session²) across the session; "O(created)" was only true of the primitive | Honest bound: per-test scope = the created-since-last-wipe DELTA (`_WIPED_UP_TO` cursor slicing `_JOURNAL`), so per-test wipes are O(delta) and the session total is O(created) amortized; cost-bound test re-specified on the delta | Task 2 Step 3/7 item 0
| P2-11 | P2 | pmv manifest must include `-m 'not track_b'`: post-merge-validation.yml:318 runs `pytest tests/ ... -m 'not track_b' --ignore=tests/e2e $SLOW_IGNORES` (verified) — a manifest generated without the marker filter expects track_b nodeids the pmv run deselects → every merge reds | Task 10 Step 1a's manifest-generation spec gains the same `-m 'not track_b'` filter | Task 10 Step 1a
| P2-12 | P2 | In-process prod-command calls unaudited: test_domain_validators `_cmd_validate` ×7-8 (L543-579), test_session_index_health `main(["doctor"])` (L207), test_cli_context `main(["context"])` (L45) — under a docker session the redirect fires inside these prod commands (test frame in the caller stack) | Task 6 Step 3's pre-flight gains the three files + the L521 doctor-fallback leg is pinned per lane (subprocess → embedded fallback; in-process → redirect); results recorded in the divergence-confirmation log | Task 6 Step 3
| P2-13 | P2 | `test_wipe_server_completeness` was server-global (reds spuriously on a dev docker with leftovers) | **ALREADY FIXED — verified:** the cycle-3 P2-7 scoping is in place (fixture's own graphs via faked `list_graphs`; the session-end stale sweep owns the global cleanup and must run BEFORE this assert in CI order). No change needed this pass; recorded for the record | Task 2 Step 1 (verified)
| P2-14 | P2 | Mid-session TEST_SESSION mutation unguarded at the carve-out/stem-registry level | `test_session_token_present_and_hex8_during_docker_session` added to test_markers.py (conditional on `TORTOISE_DB_URI` set — docker-half shape; asserts presence + 8-hex) | Task 5 Step 1

### Good-vs-Easy deferrals (cycle 4 — rule 4/5 records)

| Issue | Chosen (Good) | Deferred alternative | Cost of deferral | Rationale |
|---|---|---|---|---|
| P0-1 scope wiring | Registry + since-last-wipe cursor in `_wipe_or`'s default scope | Thread an explicit scope arg through all 30+ converted call sites | Touches every migrated file again; the default at the seam fixes the whole class and future callers | One default in the dispatcher beats 30 call-site edits that future callers would forget anyway |
| P1-6 SDK default | SDK-layer per-session default namespace gated on `db_path is not None` + TEST_MODE | Per-file enumeration + explicit per-test namespaces at ~25 sites across ~20 files | ~25 edits, drift-prone (new no-namespace sites appear), and the plan's own unit test only works under the SDK-layer option | The seam fixes the class; the gate preserves `TortoiseSDK()`'s URI-graph semantics with zero churn to test_namespace_uri_mode |
| P1-9 GRAPH.DELETE | Session-end/stale sweeps drop journaled graphs after DETACH (drop=True) | A scheduled server-side GRAPH.LIST reaper | A new ops surface for a problem the sweep can solve at its own boundary | The sweep already knows the session's exact graph set — deleting at the same seam is one flag |
| P2-2 from_uri | from_uri journals its resolved graph in test mode + per-test scope excludes the shared default | Per-session URI path (`/tortoise_test_matrix_<session>`) | Changes the CI URI format (ripples through every job/env/assert) and still leaves explicit-name from_uri sites unjournaled | One seam point covers URI-default AND explicit from_uri constructions; the last-suite-standing coordination already bounds the shared graph |
| P2-6 live-required job | Audit + grep pin (job URI stays `/tortoise`) | Change the job URI to the test-prefixed path now | The job's live tests use explicit `test_live_*` names today — a URI change is churn with no current risk | Inertness is verified; the audit + grep turn a future regression into a visible CI failure instead of silent pollution |

## Cycle-4 SUMMARY

All 10 P0/P1 issues fixed (P0-1, P0-2, P1-1 … P1-9) and all 14 actionable P2s fixed in-line; one issue (P2-13) verified as already-fixed. The dominant theme this pass was **wiring vs primitive**: cycle 3 specified mechanisms whose CALL SITES never delivered them (P0-1 scope, P1-7 EXPECT_URI, P1-5 file-wide consumption, P1-2's vacuous assert) and two mechanisms that could never run at all (P1-1's constructor round-trip, P1-3's class-attr assert). Three reviewer premises were confirmed with fresh line-level evidence rather than rubber-stamped:

1. **P1-5** — the consumption seam's coverage is 2 of ~22 registry-mode tests (verified: 27 `run_backup_sweep(` call sites; `team_graph_name` registry branch is deterministic `team_{id}`), so half b reds at P2 without a file-wide fixture.
2. **P2-8** — `__main__.py` has 6 path-construction sites (L13/521/1724/1915/2165/2562) and backup.py has NO main/`__main__` block — the plan's single-site census + pop-site assumption were factually wrong.
3. **P2-6/P2-11** — the live-required job's non-test-prefixed URI (`/tortoise`) is inert today (explicit `test_live_mw_tortoise` names) but was never audited; the pmv run DOES apply `-m 'not track_b'` (post-merge-validation.yml:318) so the pmv manifest must too.

Every other claim was verified exactly as stated (falkordb `Is_Sentinel` live round-trip at :132, `_is_embedded` instance-only, test_helpers.py absent, L745 real-function assert, `TortoiseSDK()` default db_path=None + URI-graph resolution, embedded marker `{pid}-{uuid8}` + `pid=`/`start=` lines, team-p/team-k/team-red/team-green + e2e-900 census gaps, `_cmd_validate`/`main([...])` in-process calls, `_available_connections` vacuity). The plan's structure (Tasks 1-10, Phase 1-4 gates, E2E catalog, Failure Modes, changelogs) is preserved; all changes are surgical in place.
