<!-- research-path: docs/epics/2026-08-24-test-db-migration/research-brief.md -->

# Epic #1647 Implementation Plan — Migrate test suite from embedded FalkorDBLite to real FalkorDB (docker)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make real FalkorDB (docker, matching the selfhost product) the default test DB — one URI-aware seam flips all test DB construction; embedded FalkorDBLite remains only for the 342-test behavioral carve-out — eliminating the test debt caused by the parallel embedded stack (reaper/orphans/EmbeddedStoreBusyError/concurrency divergence).

**Team:** epistemic-team

**Architecture:** A four-phase strangler rollout (P1 seam+hermeticity with zero behavior change → P2 one fast-matrix half on docker → P3 both halves → P4 allowlist/reaper shrink). The core mechanism is a **class-level URI-aware redirect** in `FalkorProjection.__init__` (the `path is not None` branch): when **both** `TORTOISE_TEST_MODE=1` (exported by conftest — the test-session signal, plan-review P0-4) **and** `TORTOISE_DB_URI` (supported scheme) are set, raw `path=` constructions from a **non-exempt test module** construct server-mode from the URI with a guard-passing derived graph name `test_<stem>_<hash8(path)>`; otherwise the embedded path is byte-for-byte today's code. Prod tools (backup/rebuild/migrate) never redirect: they never run under `TORTOISE_TEST_MODE`. `TORTOISE_TEST_NO_REDIRECT` exempts **caller test modules** (frame-identified, plan-review P0-1), not DB-file stems. Hermeticity is name-first: guard-passing graph names + a server-mode `wipe_server()` filtered to `test_`/`tortoise_test_`-prefixed graphs, refusing non-loopback hosts, with a `_wipe_or()` dispatcher so every existing `wipe()` caller migrates (plan-review P0-2). Fail-closed semantics are enforced by extending `tools/skip-guard.py` with a coverage manifest reconciled against **junitxml** (the lossless per-testcase PASSED/SKIPPED+reason source, plan-review P0-3) plus a conftest backend-identity tripwire.

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
| E2E-7 | Zero redislite orphans on docker halves; bounded on carve-out | e2e (orphan assert step) | leak accumulation | docker halves: 0 orphans post-run; carve-out jobs keep <20 bounded assert |
| E2E-8 | Divergence change-list conformance (D1–D16) | integration (conformance file) | silent divergence beyond the documented list | each D1–D16 branch asserts its documented behavior in both modes; the file is the executable change list |
| — | **Seam redirect** (new unit surface) | unit (tests/test_redirect_seam.py) | redirect fires when it must not (prod path) | URI unset → `_is_embedded is True` byte-identical; URI + `TORTOISE_TEST_MODE` set → server mode + derived `test_*` name; explicit graph_name honored; `TORTOISE_TEST_NO_REDIRECT` exempts the **caller test module** (frame-identified, never the DB-file stem); `:memory:` derives a per-construction unique `test_memory_<nonce>` |
| — | **wipe_server** (new unit surface) | unit (tests/test_wipe_server.py) | wiping a non-test graph (data loss) | wipes only `test_`/`tortoise_test_`-prefixed graphs; refuses non-loopback hosts; embedded `wipe()` unchanged |
| — | **Coverage manifest** (new unit surface) | unit (tests/test_skip_guard.py) | vacuous early-return (nodeid vanishes) | junitxml is the authoritative observed set (lossless per-testcase nodeid + skip reason); expected nodeid (from `--collect-only`) missing from the junitxml testcases → red; missing junitxml + manifest → red (flip of `test_missing_log_is_not_a_failure`) |

**Complexity ratings (from issue):** Architecture = complex, Config = standard, Ontology = low. Verification routing: integration + e2e depth for the migration surfaces; unit for the 3 new mechanisms; no UX domain (no UI); no content domain.

---

### E2E test catalog (runnable gates)

Each E2E ships as a concrete artifact (new test file, CI job config, or existing-test adaptation). Setup/assertion/gates per E2E; phase gates below reference these.

- **E2E-1** — `tests/test_round_trip_parity.py` (new): parametrized `create_point → search` round-trip over `TORTOISE_DB_URI` set/unset. Assertion: point id, content_hash, search hit list, vector results identical on the D1/D5-identical paths; D6/D8 sides assert their documented expectation. **Gates:** P1 mechanism → P2 docker half proves it.
- **E2E-2** — shared-graph tier fixture + `wipe_server()`; exercised by existing wipe-heavy files (test_projection, test_search_engine_gaps, test_a9_direct_edge_traversal, test_index_surfacing, test_recall_gaps_subgraph, test_about_event_untangle) under URI set + a control test (`test_bare_test_graph_wipe_still_raises`) in `tests/test_wipe_server.py`. **Gates:** P1 unit → P2 docker smoke of the wipe-heavy + `_wipe_or` surfaces (Task 6 Step 3 local pre-flight — only test_a9_direct_edge_traversal + test_recall_gaps_subgraph are in fast half b; test_projection/test_search_engine_gaps/test_index_surfacing/test_about_event_untangle are half-a/slow and follow at P3 — plan-review P1-6).
- **E2E-3** — `tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes` + live-writer portion (:130) under job URI; the 3 busy-error tests stay embedded-marked (skip visibly on docker, pass on embedded). **Gates:** P2 docker smoke (Task 6 Step 3 pre-flight includes the live-writer tests; test_embedded_concurrency is a test-slow file, not in the P2 half-b flip — plan-review P1-6) → P3 (both halves, CI-wide).
- **E2E-4** — carve-out job (URI unset) running exactly the 342-test set; skip-guard exempts them (redislite-availability class ≠ FalkorDB-availability). **Gates:** P1 (untouched) → P4 (post-shrink registry consistency).
- **E2E-5** — fast-matrix CI jobs; half-b wall is the P2 data point, both-halves the P3 gate. **Gates:** P2 (half b) → P3 (both halves).
- **E2E-6** — skip-guard coverage manifest + backend-identity tripwire; outage simulation = run migrated set without URI → guard must go red. **Gates:** P2 (manifest on half b) → P3 (both halves).
- **E2E-7** — orphan-assert CI step re-targeted per surface. **Gates:** P3 (docker ~0) → P4 (reaper demotion; dev-machine <20 without reaper).
- **E2E-8** — `tests/test_divergence_conformance.py` (new): asserts D2/D3 recovery raises on server, D6 composite only on server, D8 HNSW only on server, D11 busy-error embedded-only, D12 multi-tenant on server. **Gates:** P1 (expectation split) → P2 (side-by-side confirmation) → P3 (both modes enforced).

---

## Phase 1 — Seam + hermeticity (zero behavior change)

> Embedded remains the default; `TORTOISE_DB_URI` is not set anywhere in P1, so every new mechanism is **dormant**. Acceptance: full 6,837-test embedded suite green (identical to baseline), redirect/wipe/manifest unit-tested, no orphan delta.

### Task 1: Class-level URI-aware redirect + fixture-seam URI-awareness

**Intent:** Create the single seam (decision D-1=A) — one place flips ALL test DB construction to real FalkorDB when `TORTOISE_DB_URI` is set **and the caller is a test session** (`TORTOISE_TEST_MODE=1`, exported by conftest — plan-review P0-4: prod tools that construct with explicit paths must NEVER redirect). Raw `FalkorProjection(path=...)` constructions (114 no-graph-name sites in 27 files) redirect automatically; seam fixtures (`shared_proj`/`sdk_factory`/`shared_embedded_db`) get URI-aware branches. Inert in P1 (URI unset) — embedded path byte-for-byte today.

**Acceptance:**
- With `TORTOISE_DB_URI` unset, `FalkorProjection(path=...)` constructs identically to today (`_is_embedded is True`, embedded subclass, AOF/relative-path semantics intact) — verified by the pre-existing embedded suite plus the new inertness test.
- With `TORTOISE_DB_URI` set to a supported scheme (`docker`/`redis`/`rediss`) **and `TORTOISE_TEST_MODE=1`**, `FalkorProjection(path=...)` from a non-exempt test module constructs server-mode (`_is_embedded is False`, host/port/user/pass from the URI — `from_uri` semantics), with graph name = explicit `graph_name` if given, else derived `test_<stem>_<hash8(path)>` (guard-passing, deterministic, collision-safe). With URI set but `TORTOISE_TEST_MODE` absent (prod tools: backup.py:134/144, ingest.py:471, `__main__.py:13` rebuild, migrate_db.py:75/184, hosted_api.py:6254/8263/8357, pipeline_cli.py:139 — verified), **no redirect** — the embedded path construction is preserved (plan-review P0-4).
- `TORTOISE_TEST_NO_REDIRECT=<comma-separated TEST-MODULE stems>` (e.g. `test_ops_safety`, `test_config`) exempts constructions whose **caller test module** is listed — resolved by frame inspection (`_caller_test_stem()`, see Step 3), NEVER by the DB-file basename (plan-review P0-1: carve-out files use arbitrary DB names — `c.db`, `user.db`, `fresh.db`, `solo.db`, `lost.db`… — that never match their file stems, so the old stem-keyed check never fired). A listed module keeps `path=` embedded construction even when URI is set. List wired in Task 5.
- `path == ":memory:"` is special-cased: derives a per-construction unique `test_memory_<8-hex nonce>` graph (plan-review P2-13 — `:memory:` is a constant string, so a path-hash would collide every `:memory:` construction in a run onto one shared server graph, breaking `test_open_kinds`' per-construction isolation).
- `skip_health_check` (and `allow_nonstandard_path`) are preserved on the redirect path — the redirect falls through to the existing host-mode branch in the SAME `__init__` frame instead of recursing `cls(...)` (plan-review P0-4).
- No-arg `FalkorProjection()` does NOT redirect (D-1 scope: explicit `path=` only) — still resolves the canonical embedded path. Captured by recording `explicit_path = path is not None` BEFORE the `resolve_db_path()` no-arg fallback (verified: `__init__` resolves no-arg → path at L311-313, so a naive path-branch check would also capture no-arg).
- `shared_proj`/`sdk_factory`/`shared_embedded_db` URI-aware: URI set → server construction with `test_suite_<uuid>`-style graph names; unset → today's construction. The URI branch is evaluated BEFORE `has_falkor()`/`skip_if_no_falkor()` (plan-review P2-16): under URI, `has_falkor()` returns True immediately (no embedded probe — the probe would redirect and mint a `test` graph on the server) so migrated files never early-return.
- Unsupported URI scheme → embedded construction (fail-safe, no crash).

**Files:**
- Modify: `tortoise/projection/__init__.py:303-392` (`__init__` path branch — the redirect insertion; `from_uri` at L547 reused for URI parsing)
- Modify: `tests/_embedded.py` (`shared_proj` URI branch; `TEST_NO_REDIRECT_STEMS` constant)
- Modify: `tests/conftest.py` (`sdk_factory` ~L83 URI branch; `shared_embedded_db` URI branch; export `TORTOISE_TEST_NO_REDIRECT` via `os.environ.setdefault` from the `tests/_embedded.py` constant so the product-side redirect reads the in-repo list without importing `tests/`; export `os.environ.setdefault("TORTOISE_TEST_MODE", "1")` — the test-session signal that gates the redirect, plan-review P0-4; `has_falkor()` URI short-circuit per P2-16)
- Test: `tests/test_redirect_seam.py` (new)

**Step 1: Write the failing tests**

```python
# tests/test_redirect_seam.py
"""Unit surface: the class-level URI-aware redirect (epic #1647 Task 1)."""
import os

import pytest

from tortoise.projection import FalkorProjection


@pytest.fixture
def uri_env(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    yield


def test_unset_uri_constructs_embedded():
    proj = FalkorProjection("/tmp/seam-test-a.db")
    try:
        assert proj._is_embedded is True
    finally:
        proj.close()


def test_uri_set_redirects_to_server(uri_env):
    proj = FalkorProjection("/tmp/seam-test-b.db")
    try:
        assert proj._is_embedded is False
        # graph name derived: test_<stem>_<hash8(path)>
        assert proj.graph_name.startswith("test_seam-test-b_")
        assert len(proj.graph_name) == len("test_seam-test-b_") + 8
    finally:
        proj.close()


def test_explicit_graph_name_honored(uri_env):
    proj = FalkorProjection("/tmp/seam-test-c.db", graph_name="test_explicit")
    try:
        assert proj.graph_name == "test_explicit"
        assert proj._is_embedded is False
    finally:
        proj.close()


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

Between the no-arg resolution and the existing `if path is not None:` branch, insert the redirect. It resolves the server params, sets `path = None`, and falls through to the EXISTING `elif host is not None:` branch — the host-mode construction is reused, not duplicated, and the embedded body (relative-path reject, AOF config, `FalkorDB(path, serverconfig=...)` subclass) is skipped because `path` is now None. `skip_health_check`/`allow_nonstandard_path` keep their caller values (plan-review P0-4) — no recursive `cls(...)` call.

```python
# Epic #1647 (D-1=A): class-level URI-aware redirect. Fires ONLY in a test
# session (TORTOISE_TEST_MODE=1, exported by conftest) with a supported
# TORTOISE_DB_URI — prod tools (backup.py, __main__.py rebuild, ingest.py,
# migrate_db.py, hosted_api.py, pipeline_cli.py) construct with explicit
# paths but never run under TEST_MODE, so they never redirect (P0-4).
# Explicit path= only (D-1 option a): no-arg keeps the embedded canonical
# path (captured via explicit_path above). TORTOISE_TEST_NO_REDIRECT
# (comma-separated TEST-MODULE stems) exempts carve-out files via caller
# frame inspection (P0-1) — the DB-file basename is NEVER the key.
_uri = os.environ.get("TORTOISE_DB_URI")
if (explicit_path and _uri
        and os.environ.get("TORTOISE_TEST_MODE") == "1"
        and _is_supported_uri_scheme(_uri)):
    _no_redirect = {
        s.strip() for s in
        os.environ.get("TORTOISE_TEST_NO_REDIRECT", "").split(",") if s.strip()
    }
    if _caller_test_stem() not in _no_redirect:
        from urllib.parse import urlparse
        _parsed = urlparse(_uri)
        _validate_uri_scheme(_parsed.scheme)
        if path == ":memory:":
            # P2-13: :memory: is a constant string — a path-hash would
            # collide every :memory: construction onto one shared graph;
            # embedded :memory: is fresh per construction, so derive a
            # per-construction unique test_memory_* graph.
            _graph = f"test_memory_{os.urandom(4).hex()}"
        elif graph_name != "tortoise":
            _graph = graph_name
        else:
            _stem = os.path.basename(path)
            _graph = f"test_{_stem}_{hashlib.sha1(path.encode()).hexdigest()[:8]}"
        path = None  # fall through to the host-mode branch below
        host = _parsed.hostname or "localhost"
        port = _parsed.port or 16379
        username = _parsed.username or None
        password = _parsed.password or None
        ssl = (_parsed.scheme == "rediss")
        graph_name = _graph
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

    Walks the caller stack for the first frame whose __file__ basename starts
    with "test_" — the exemption key for TORTOISE_TEST_NO_REDIRECT. Helpers
    (tests/_embedded.py, tests/_live_utils.py, conftest.py) are skipped by
    the prefix sniff, so a carve-out file constructing through a helper still
    resolves to its own stem. Returns None when no test module is in the
    stack (prod callers — irrelevant: the redirect is TEST_MODE-gated)."""
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
```

Then make the seam fixtures URI-aware (`tests/_embedded.py` `shared_proj`, `tests/conftest.py` `sdk_factory` + `shared_embedded_db`): when `TORTOISE_DB_URI` (supported scheme) is set → construct via `FalkorProjection.from_uri(uri, graph_name=f"test_suite_{os.urandom(4).hex()}")` (shared tier) or per-test uuid names (exact-set tier); unset → today's construction unchanged. In `tests/_embedded.py`, the URI branch runs BEFORE `has_falkor()` and `has_falkor()` short-circuits to True under URI (plan-review P2-16 — the embedded probe would otherwise redirect, mint a `test` graph on the server, and misreport backend availability); `skip_if_no_falkor()` therefore returns False in URI sessions and migrated files never vacuous-return. `provision_test_user` (conftest) gets the same treatment (plan-review P1-5): its `namespace="e2e-tests"` computes the non-test graph `team_e2e-tests` shared by the whole suite — swept to a guard-passing per-test `test_e2e_<uuid>` namespace (`test_e2e_<uuid>_tortoise` via the SDK mapping, sdk.py L1115-1123).

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_redirect_seam.py -v`
Expected: all PASS (with a live docker on 6379 for the redirect cases).

**Step 5: Prove P1 inertness (zero behavior change)**

Run: `uv run pytest tests/test_projection.py tests/test_search_engine_gaps.py tests/test_a9_direct_edge_traversal.py -v` (URI unset AND `TORTOISE_TEST_MODE` irrelevant — the redirect needs both conditions)
Expected: green, identical to pre-P1 — the redirect never fires.

Note: on a dev machine whose `.env` sets `TORTOISE_DB_URI`, the P1 embedded-baseline runs must explicitly unset it (`TORTOISE_DB_URI= uv run pytest …`); this is the documented local-dev path until P3 inverts the default (plan-review P1-9).

Run: `uv run pytest tests/ --collect-only -q | tail -1` → `6837 tests collected` (no test-count drift).

**Step 6: Commit**

```bash
git add tortoise/projection/__init__.py tests/_embedded.py tests/conftest.py tests/test_redirect_seam.py
git commit -m "feat(testdb): class-level URI-aware redirect + URI-aware seam fixtures (epic #1647 P1)"
```

---

### Task 2: `wipe_server()` — server-mode hermeticity wipe (graph-granularity, test-prefix-filtered, non-loopback refusal)

**Intent:** Deliver the P0 hermeticity mechanism (decision D-4=A): the existing `wipe()` refuses server mode (verified, `tests/_embedded.py:56-65`), and the graph guard rejects bare `test`/`tortoise` (verified, `_assert_test_graph` L980-1003 + `'test'.startswith(('test_','tortoise_test'))` is False). Docker hermeticity = per-test wipe of **test-prefixed graphs only**, fail-closed, on loopback hosts only. Also delivers the **caller migration** (plan-review P0-2): a `_wipe_or()` dispatcher converts every existing `wipe()` caller so no migrated file raises `RuntimeError` on docker, and a session-end server sweep (plan-review P2-14).

**Acceptance:**
- `wipe_server(proj)` enumerates `list_graphs()`, wipes (DETACH DELETE) ONLY graphs starting `test_`/`tortoise_test_`; every other graph is skipped (never wiped).
- `wipe_server(proj)` raises `RuntimeError` when the projection's host is not loopback (`localhost`/`127.0.0.1`/`::1`) — protecting a remote dev/shared server (research Q6, decision D-4).
- Existing `wipe()` unchanged: all-graphs wipe on embedded; server refusal retained.
- `_wipe_or(proj)` dispatches on `_is_embedded`: embedded → `wipe(proj)` (all-graphs, today's semantics); server → `wipe_server(proj)` (filtered, loopback-only). This is the plan-review P0-2 fix — Task 2 of the ORIGINAL plan only added `wipe_server` and no task rewired the callers, so `test_projection._shared_proj()` (per-test `_wipe()`), `test_backup_sweep` (22 calls), `test_projection_version_gate` (6), `test_analyze` (3), `test_1162_add_operator_local_svbp`, `test_github_connector` would all raise on docker. Every migrated `wipe()` caller converts to `_wipe_or()`; E2E-2's gate depends on this.
- Session-end server sweep (plan-review P2-14): a conftest session-end autouse fixture (URI set only) enumerates `list_graphs()` and drops every `test_`-prefixed graph left over — long-running dev docker servers do not accumulate graphs across sessions (CI per-job containers die anyway; this is a dev-machine hygiene fix mirroring `_redislite_hygiene`).
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


def test_wipe_server_skips_non_test_graphs(server_proj):
    wipe_server(server_proj)
    assert server_proj.db.select_graph("registry_tortoise").query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 0  # not wiped — never created

def test_wipe_server_refuses_non_loopback(monkeypatch):
    from tortoise.projection import FalkorProjection
    # P2-10: skip_health_check=True — the constructor's auto health check
    # probes the unreachable host and raises RuntimeError BEFORE wipe_server
    # can run its own refusal, so the test could never pass without it.
    proj = FalkorProjection(host="db.internal.example.com", port=6379,
                            graph_name="test_remote", skip_health_check=True)
    try:
        with pytest.raises(RuntimeError, match="loopback"):
            wipe_server(proj)
    finally:
        proj.close()


def test_embedded_wipe_still_refuses_server_mode(server_proj):
    with pytest.raises(RuntimeError, match="EMBEDDED"):
        wipe(server_proj)  # the pre-existing refusal must survive
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_wipe_server.py -v`
Expected: FAIL — `wipe_server` does not exist (ImportError); the refusal test also fails (`wipe` refuses, which is the point of the assert — actually it should pass; the two wipe tests fail on missing symbol).

**Step 3: Implement**

```python
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

def wipe_server(proj) -> None:
    """Server-mode hermeticity wipe (epic #1647, D-4).

    Enumerates list_graphs() and DETACH-DELETEs ONLY graphs named
    test_/tortoise_test_* — the guard-passing test-graph family. Every other
    graph is skipped, never wiped (fail-closed: a misconfigured non-test
    graph survives untouched). Refuses non-loopback hosts: a test suite must
    never wipe a remote dev/shared server even if test-prefixed.
    """
    host = getattr(getattr(proj.db, "connection", None), "host", None) \
        or getattr(proj.db, "connection", None).__dict__.get("_host", None)
    if host not in _LOOPBACK_HOSTS:
        raise RuntimeError(
            f"wipe_server() refuses non-loopback host {host!r} — test wipes "
            f"are local-only (decision D-4)")
    for g in proj.db.list_graphs() or []:
        if not g.startswith(("test_", "tortoise_test")):
            continue  # fail-closed: never wipe a non-test graph
        try:  # noqa: SIM105
            proj.db.select_graph(g).query("MATCH (n) DETACH DELETE n")
        except Exception:
            pass


def _wipe_or(proj) -> None:
    """Mode-dispatching hermeticity wipe (plan-review P0-2).

    Embedded projection → wipe(proj) (all-graphs, today's semantics).
    Server projection → wipe_server(proj) (test-prefix-filtered,
    loopback-only). Every migrated file's per-test wipe converts to this so
    the P2 half-b flip never raises RuntimeError from the server-mode
    refusal."""
    if getattr(proj, "_is_embedded", False):
        wipe(proj)
    else:
        wipe_server(proj)
```

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_wipe_server.py -v` → all PASS (docker 6379 up).

**Step 5: Prove no behavior change on embedded**

Run: `uv run pytest tests/test_projection.py -v -k "wipe"` → embedded wipe path green.

**Step 6: Convert the `wipe()` callers to `_wipe_or()` (plan-review P0-2)**

For each migrated wipe caller, swap the import/alias and the call site — `from tests._embedded import wipe` → `from tests._embedded import _wipe_or` (or alias `_wipe = _wipe_or` where the file keeps a local alias):
- `tests/test_projection.py` — `_shared_proj()` L85 (`from tests._embedded import wipe as _wipe` → `import _wipe_or as _wipe`)
- `tests/test_backup_sweep.py` — 22 call sites
- `tests/test_projection_version_gate.py` — 6 call sites
- `tests/test_analyze.py` — 3 call sites
- `tests/test_1162_add_operator_local_svbp.py`, `tests/test_github_connector.py` — 1+ call sites each

Verify embedded behavior is unchanged (URI unset → `_wipe_or` dispatches to `wipe`): `uv run pytest tests/test_backup_sweep.py tests/test_projection_version_gate.py tests/test_analyze.py tests/test_projection.py -v -k wipe` → green.

**Step 7: Add the session-end server sweep (plan-review P2-14)**

Conftest session-end autouse fixture (mirroring `_redislite_hygiene`): when `TORTOISE_DB_URI` is set, iterate `list_graphs()` on a `from_uri` probe and drop every `test_`/`tortoise_test_`-prefixed graph left at session end (skip/never-wipe non-test graphs; loopback-only — the same filter as `wipe_server`, shared via a common helper). Verify with a URI-set session that ends with a seeded `test_*` graph and asserts the sweep emptied it.

**Step 8: Commit**

```bash
git add tests/_embedded.py tests/conftest.py tests/test_wipe_server.py \
    tests/test_projection.py tests/test_backup_sweep.py \
    tests/test_projection_version_gate.py tests/test_analyze.py \
    tests/test_1162_add_operator_local_svbp.py tests/test_github_connector.py
git commit -m "feat(testdb): wipe_server() + _wipe_or() caller migration + session-end sweep (epic #1647 P1, D-4/P0-2/P2-14)"
```

---

### Task 3: Skip-guard inversion — coverage manifest + `test_workflow_keeps_rs` / `test_missing_log_is_not_a_failure` flips

**Intent:** Kill the vacuous-pass class (#942) at 6,837-test scale: on migrated halves, any expected nodeid missing from the run's observed set must go red. **P0-3 reconciliation:** the ORIGINAL plan generated the manifest (expected nodeids) from `--collect-only` full nodeids but observed skips from `-r fEs` file:line summaries — the two formats NEVER match, so the 3 `embedded_only` marker skips guaranteed P2 red every run, and the plan's own unit test masked the mismatch by writing file:line into the manifest. Fix: **junitxml is the authoritative observed PASSED/SKIPPED+reason source** — lossless per-testcase nodeid (via `file`+`classname`+`name` reconstruction, verified on the real repo format with `junit_family=xunit1`) and lossless skip reason (`<skipped message>`). Builds the mechanism in P1 (dormant — no manifest passed); wires it to half b in P2 (Task 6).

**Acceptance:**
- `tools/skip-guard.py` gains a manifest mode: `--manifest <expected-nodeids.txt>` + `--junitxml <path>` — every expected nodeid must appear as a junitxml testcase (passed or skipped); a missing nodeid → exit 1.
- Observed-set source (scope-review M2, plan-review P0-3): **junitxml** (`--junitxml=/tmp/junit.xml`, `-o junit_family=xunit1` for the `file`/`line` attributes). Nodeid reconstruction: `file` + `::` + (classname minus its module-dotted prefix) + `::` + `name` — verified against the real repo format: `classname="tests.test_skip_guard.TestGuardAcceptsCleanLog"` + `file="tests/test_skip_guard.py"` + `name="test_no_skips_at_all"` → `tests/test_skip_guard.py::TestGuardAcceptsCleanLog::test_no_skips_at_all`; module-level tests have classname == module-dotted prefix → `file::name`. Parametrized ids ride in `name` (`test_y[1]`).
- The `-v` progress lines are NOT a source for the manifest (their 80-col truncation behavior is terminal-width-dependent; junitxml is deterministic). The `-r fEs` summary stays for human-readable logs only. The existing `FalkorDB`-reason matcher reads `<skipped message="...">` from junitxml (lossless, never truncated) instead of log lines; `_live_utils.py` exclusion unchanged; carve-out files' redislite-reasoned skips never match the `FalkorDB` substring.
- `test_missing_log_is_not_a_failure` (tests/test_skip_guard.py) flips: with `--manifest` passed, a missing/unreadable junitxml = every expected nodeid absent → **red** (was exit 0). The no-manifest path keeps exit 0 (back-compat for the current invocation).
- `test_workflow_keeps_rs` stays green and gains junitxml assertions (the CI fast-suite invocation must pass `--junitxml` + `-o junit_family=xunit1`; `-r fEs` remains for the human summary).

**Files:**
- Modify: `tools/skip-guard.py`
- Modify: `tests/test_skip_guard.py` (`test_missing_log_is_not_a_failure` flip; `test_workflow_keeps_rs` extension; new manifest cases: missing-nodeid red, PASSED satisfies, reasoned-skip satisfies, carve-out exemption, vacuous early-return)
- Test: `tests/test_skip_guard.py` (extended)

**Step 1: Write the failing tests** (append to `tests/test_skip_guard.py`; fixtures are REAL pytest junitxml output, plan-review P0-3 — not the old file:line fake; extend the existing `run_guard(log_text)` helper to `run_guard(log_path, manifest=None, junit=None)` passing the new `--manifest`/`--junitxml` args)

```python
# REAL junitxml format (pytest 9.1.1, -o junit_family=xunit1 — verified):
JUNIT_PASSED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="2">
<testcase classname="tests.test_ep_directional.TestX" name="test_y" file="tests/test_ep_directional.py" line="35" time="0.001" />
<testcase classname="tests.test_projection" name="test_something" file="tests/test_projection.py" line="88" time="0.001" />
</testsuite></testsuites>'''
JUNIT_SKIPPED = '''<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite tests="1">
<testcase classname="tests.test_embedded_lifecycle_fast_close" name="test_ephemeral_nosave" file="tests/test_embedded_lifecycle_fast_close.py" line="30" time="0.001"><skipped type="pytest.skip" message="redislite unavailable">/tests/test_embedded_lifecycle_fast_close.py:30: redislite unavailable</skipped></testcase>
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
    rc = run_guard(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 1  # fail-closed — vacuous early-return detected


def test_manifest_passed_nodeid_satisfies(tmp_path):
    junit = _write(tmp_path, "junit.xml", JUNIT_PASSED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n"
                      "tests/test_projection.py::test_something\n")
    rc = run_guard(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0


def test_manifest_reasoned_skip_satisfies(tmp_path):
    # A reasoned skip (junxml <skipped>) is an OBSERVED testcase — it
    # satisfies the manifest (the marker-skips in Task 5 must never go red).
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED)
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_embedded_lifecycle_fast_close.py::test_ephemeral_nosave\n")
    rc = run_guard(str(tmp_path / "pytest.log"), manifest, junit=junit)
    assert rc == 0  # reasoned skip ≠ vanished nodeid (and no FalkorDB substring)


def test_missing_junitxml_with_manifest_is_red(tmp_path):
    manifest = _write(tmp_path, "manifest.txt",
                      "tests/test_ep_directional.py::TestX::test_y\n")
    rc = run_guard(str(tmp_path / "no-such.log"), manifest,
                   junit=str(tmp_path / "no-such.xml"))
    assert rc == 1  # FLIPPED from the historical exit 0


def test_missing_junitxml_without_manifest_stays_green(tmp_path):
    rc = run_guard(str(tmp_path / "no-such.log"), None)
    assert rc == 0  # back-compat: no manifest, no evidence


def test_falkordb_reason_skip_from_junitxml_is_red(tmp_path):
    junit = _write(tmp_path, "junit.xml", JUNIT_SKIPPED.replace(
        "redislite unavailable", "Live FalkorDB (Docker) not available"))
    rc = run_guard(str(tmp_path / "pytest.log"), None, junit=junit)
    assert rc == 1  # the historical matcher, now reading junitxml reasons
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_skip_guard.py -v`
Expected: the new junitxml-manifest tests FAIL (no `--manifest`/`--junitxml` mode yet); the existing `test_missing_log_is_not_a_failure` still passes (not yet flipped).

**Step 3: Implement**

- Add `--manifest <path>` and `--junitxml <path>` args to `tools/skip-guard.py` (the existing single positional `<pytest.log>` stays for back-compat).
- Parse expected nodeids from the manifest (one per line, `#`-comments allowed).
- Parse the junitxml: per `<testcase>`, reconstruct the nodeid as `file + "::" + (classname minus its module-dotted prefix) + "::" + name` (module-dotted prefix = `file` with `/`→`.` and `.py` stripped; module-level tests have classname == prefix → `file::name`). A `<skipped>` child marks the testcase skipped and carries the lossless reason in `message`.
- Observed set = all junitxml testcase nodeids (passed AND skipped-with-reason). An expected nodeid absent from the observed set → violation (print + exit 1).
- FalkorDB-reason check: any `<skipped message>` containing `FalkorDB` (excluding `_live_utils.py`-sourced lines, preserved from the current matcher) → violation (exit 1).
- When `--manifest` is absent: current behavior (missing junitxml/log → exit 0).
- When `--manifest` present and the junitxml is missing/unreadable → every expected nodeid absent → exit 1 (the flip).
- **Format contract (plan-review P0-3/P1-7):** the manifest generator and the pytest run must use the SAME rootdir-relative `$FILES` (CI invokes `tests/$f.py`) so `--collect-only` nodeids and junitxml `file` attributes agree; the pytest invocation passes `--junitxml=/tmp/junit.xml -o junit_family=xunit1`.

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_skip_guard.py -v` → all PASS (including the flipped `test_missing_log_is_not_a_failure`, updated to pass `--manifest`/`--junitxml`). Then confirm the real-format contract end-to-end: `uv run pytest tests/test_skip_guard.py --junitxml=/tmp/junit.xml -o junit_family=xunit1 -q && python3 tools/skip-guard.py /tmp/pytest.log --junitxml=/tmp/junit.xml --manifest <collect-only-nodeids>` → exit 0.

**Step 5: Commit**

```bash
git add tools/skip-guard.py tests/test_skip_guard.py
git commit -m "feat(testdb): skip-guard coverage manifest reconciled against junitxml — vanished nodeids go red (epic #1647 P1, P0-3)"
```

---

### Task 7: Graph-name sweep — explicit non-test graph names + no-namespace SDK bulk-wipers + derived-name verification

**Intent:** Make every migrated construction docker-safe BEFORE the flip. The class-level redirect (Task 1) auto-derives guard-passing names for the 114 no-graph-name `FalkorProjection` sites (measured this plan-pass; scope-brief's "93" counted a narrower site class). The sweep's real scope is the **explicit non-test graph names** + the no-namespace `TortoiseSDK` constructions whose computed graph is bare `tortoise` (which fails the guard on bulk-wipe) + a red-herring check that no bulk-wipe test still targets `test`/`tortoise`.

**Acceptance:**
- All explicit non-test graph names in migrated files renamed to `test_*`/`tortoise_test_*` (plan-review P1-4 corrected census — verified by grep this pass):
  - `graph_name="test"` construction sites: **35 across 6 files** (the old plan's "9" was wrong): test_projection.py **21** (L82/888/910/937/965/1195/1400/1417/1866/1867/1981/2076/2243/2245/2299/2370/2387/2420/2466/2532/2786), test_supplementary.py 2, test_extractor_priors.py 2, test_embedded_lifecycle.py 4 + test_embedded_lifecycle_fast_close.py 4 (carve-out — untouched), tests/_embedded.py 2 (seam-internal — handled by Task 1's URI branch, not renamed).
  - `graph_name="tortoise"` construction sites: **2** — test_projection.py:2162, test_remove_context_migration.py:333. (The many `dump_graph(..., graph_name="tortoise")`/`create_backup(..., graph_name="tortoise")` call sites in test_hosted_backup.py/test_export_cli.py are FUNCTION arguments, not projections; test_hosted_backup is carve-out.)
  - `graph_name="crash_live"`: test_import_endpoint.py:724 (**1**).
  - Net migrated renames: **27 sites across 5 files** (test_projection 21×`test` + 1×`tortoise`, test_supplementary 2, test_extractor_priors 2, test_remove_context_migration 1, test_import_endpoint 1).
- **g_rebuild*/g_consistency/parity classification (plan-review P1-4):** the test_projection `g_rebuild*` (L888/910/937/965/1981/2786), `g_consistency*` (L1400/1417) and apply-vs-rebuild parity (L1866/1867) tests MIGRATE with a name-only sweep — verified by inspection: they call `rebuild()`/`rebuild_all()`/`check_consistency()`, DB-agnostic apply-path operations (JSONL → graph) that run identically on the server; they do NOT exercise the D2/D3 `_auto_health_recover`/`recover_from_log` branches (those live in test_ops_safety — carve-out). No mode-split needed.
- The sweep extends to `graph_name="test"`/`"tortoise"`/`"crash_live"` in ALL migrated files (not just the wipe/assert files) — every migrated construction must be guard-passing before the flip.
- Seam default graph name is `test_suite_<uuid>` in docker mode (Task 1) — never bare `test`.
- No-namespace `TortoiseSDK(db_path)` constructions in migrated files that bulk-wipe or assert exact sets gain a `namespace="test_<file>_..."` (the SDK maps `test_<ns>` → `test_<ns>_tortoise`, sdk.py L1115-1123 — verified). Non-wiping SDK users may share the URI default graph (no guard trip) but are listed for the P2 divergence pass.
- Red-herring gate: a test asserts that with URI set, constructing on bare `test`/`tortoise` and bulk-wiping still raises (guard intact — this is the E2E-2 control).
- Embedded mode (URI unset): zero behavior change — embedded guard is disabled and graph-name is only observable where a test asserts it; those assertions are updated consciously (P1 diff-review item).

**Files:**
- Modify: `tests/test_extractor_priors.py`, `tests/test_projection.py`, `tests/test_remove_context_migration.py`, `tests/test_import_endpoint.py` (explicit graph-name renames) + no-namespace SDK bulk-wiper files (identified by the census grep in Step 1)
- Test: `tests/test_wipe_server.py` (add the bare-`test` control test)

**Step 1: Census (no code change)**

Run:
```bash
grep -rn 'graph_name="test"\|graph_name="tortoise"\|graph_name="crash_live"' tests/ --include="*.py"
grep -rn "DETACH DELETE" tests/ --include="*.py" | grep -v __pycache__  # bulk-wipe users
grep -rc 'graph_name="test"' tests/ --include="*.py" | grep -v ":0"   # per-file counts (P1-4 census)
```
Expected: the corrected census above (35× `graph_name="test"` / 2× `tortoise` / 1× `crash_live` constructions); the bulk-wipe users list (D4 table: test_projection, test_search_engine_gaps, test_a9_direct_edge_traversal, test_index_surfacing, test_recall_gaps_subgraph, test_about_event_untangle, test_pre_migration_safety, test_embedded_concurrency live-reset).

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

**Step 4: Rename explicit non-test graph names in migrated files**

For each migrated-file construction site: `graph_name="test"` → `graph_name="test_<file>_suite"` (or per-test names where exact-set assertions demand it), `graph_name="tortoise"` → `graph_name="test_projection_suite"` (etc.), `graph_name="crash_live"` → `graph_name="test_crash_live"`. Carve-out files (`test_embedded_lifecycle*`) untouched. Run the touched files embedded → green (name-only change).

**Step 5: Route no-namespace SDK bulk-wipers**

Add `namespace="test_<file>_<uuid>"` to no-namespace `TortoiseSDK` bulk-wipe/exact-set constructions in migrated files (or route through the URI-aware `sdk_factory`). Verify the SDK's `_get_proj` emits `test_*_tortoise` graphs.

**Step 6: Verify zero embedded-behavior change + commit**

Run: `uv run pytest tests/test_extractor_priors.py tests/test_projection.py tests/test_remove_context_migration.py tests/test_import_endpoint.py -v` (URI unset) → green.
```bash
git add tests/...
git commit -m "chore(testdb): graph-name sweep — guard-passing names for migrated constructions (epic #1647 P1)"
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
git add tests/test_indexes.py tests/test_divergence_conformance.py tests/test_cross_lens.py
git commit -m "test(testdb): divergence expectation split + E2E-8 conformance file (epic #1647 P1)"
```

**Step 4 (P2): Divergence confirmation pass** — after Task 6 flips half b, run the half-b docker suite and check every D-branch against the table (Task 6 Step 4's run). Record findings in `docs/epics/2026-08-24-test-db-migration/divergence-confirmation.md`. Unexpected divergences = P2 blockers (fixed before P3, same class as D9 calibration work).

---

## Phase 1 Gate (P1 acceptance — all of the above must hold before P2)

1. Full embedded suite green: `uv run pytest tests/ -q` → 6,837 passed (baseline).
2. P1 diff review: no embedded-mode assertion changed except conscious graph-name updates; no carve-out file touched.
3. Unit surfaces green: `tests/test_redirect_seam.py`, `tests/test_wipe_server.py`, `tests/test_skip_guard.py`, `tests/test_divergence_conformance.py`.
4. Orphan count unchanged: `pgrep -f "redislite/bin/redis-server" | wc -l` ≈ 4 (baseline).
5. E2E-1 (mechanism works, embedded unchanged), E2E-2 (wipe_server unit + control), E2E-4 (carve-out untouched), E2E-8 (conformance both modes) all green.
6. CI: both halves green, wall within 20% of baseline (41–42m / 57–58m) — no CI change in P1, so this is a regression check.

---

## Phase 2 — One-half flip (half b → docker, side-by-side divergence discovery)

> Half b is the redislite-heavy half (test_search_engine 121, test_reaper 52, test_ranking, test_embedded_concurrency) whose wall (57–58m) already rides the 55m watchdog. Half a stays embedded as the control arm. The embedded lane becomes a **non-gating canary** (decision D-3=A) until the docker lane is proven green for N runs.

### Task 4: Backend-identity tripwire (conftest)

**Intent:** Close scope-review M3 — the coverage manifest only catches nodeids that vanish; it cannot catch "redirect inert + embedded succeeds → job green". A conftest-level backend-identity check makes the docker-half guarantee complete (E2E-6).

**Acceptance:** With `TORTOISE_DB_URI` set and no `TORTOISE_TEST_NO_REDIRECT` covering the sample, a session-autouse check constructs a probe projection and asserts `_is_embedded is False`; if the redirect is inert, the suite fails at session start (never a green pass on the wrong backend). Skip-exempted when the entire session is carve-out (URI-unset job — check is a no-op there).

**Files:**
- Modify: `tests/conftest.py` (session-autouse fixture)
- Test: `tests/test_backend_identity_tripwire.py` (new)

**Step 1: Write the failing test** — with URI set, `FalkorProjection("/tmp/tripwire.db")` must be server-mode; a monkeypatched-broken redirect (simulated inert) must make the assertion fail.

**Step 2: Run to verify it fails** (against today's code, URI set, no redirect → `_is_embedded is True` → red).

**Step 3: Implement** — conftest session-autouse fixture (conftest already exports `TORTOISE_TEST_MODE=1` (Task 1), so the probe construction redirects; the scheme predicate uses `is_db_uri` from `tortoise.config` — the single shared scheme check, plan-review P2-11):

```python
@pytest.fixture(scope="session", autouse=True)
def _assert_backend_identity():
    """Epic #1647 E2E-6 tripwire: on docker-URI sessions, the FIRST
    constructed projection must be server mode — a dormant redirect would
    silently run the migrated suite on embedded and pass green."""
    from tortoise.config import is_db_uri  # shared scheme predicate (P2-11)
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if not uri or not is_db_uri(uri):
        return  # embedded session (carve-out) — no tripwire
    from tortoise.projection import FalkorProjection
    probe = FalkorProjection(os.path.join(tempfile.mkdtemp(), "tripwire.db"))
    try:
        assert probe._is_embedded is False, (
            "backend-identity tripwire: TORTOISE_DB_URI set but the "
            "class-level redirect is inert — migrated suite would green-pass "
            "on the wrong backend (epic #1647 E2E-6)")
    finally:
        probe.close()
```

**Step 4: Run to verify it passes** (Task 1's redirect active) → green.

**Step 5: Commit**

```bash
git add tests/conftest.py tests/test_backend_identity_tripwire.py
git commit -m "feat(testdb): backend-identity tripwire on docker sessions (epic #1647 P2, E2E-6)"
```

---

### Task 5: The 3 busy-error per-test markers + the carve-out redirect-exemption wiring

**Intent:** Implement decision D-2=A (per-test embedded-only marker for the 3 `EmbeddedStoreBusyError` tests) + the scope-review H1 requirement (`TORTOISE_TEST_NO_REDIRECT` **TEST-MODULE** exemption for the 6 half-b carve-out files, so the P2 job-level URI cannot flip them to docker and break their embedded-specific assertions). Per plan-review P0-1, the exemption key is the **caller test module** (frame-identified by the redirect), not the DB-file basename — the carve-out files' arbitrary DB names (`c.db`, `fresh.db`, `solo.db`, `tortoise.db`…) can never be the key.

**Acceptance:**
- New `embedded_only` marker registered in `pyproject.toml` (alongside `track_b`) + a conftest autouse skip: URI set + marker present → `pytest.skip("embedded-only: ...")` (visible skip, the D-2 skip mechanism — distinct from the per-file redirect exemption); URI unset → test runs normally.
- Applied to exactly 3 tests: `tests/test_audit.py` (d) case (~L478/L504), `tests/test_pack_state.py::TestBackfillScript::test_dry_run_default_makes_no_writes` (L668), `tests/test_index_directory.py::test_e2e9_cross_process_embedded_overlap` (L1852).
- `TEST_NO_REDIRECT_STEMS` in `tests/_embedded.py` = the 6 half-b carve-out TEST-MODULE stems at P2 (`test_embedded_lifecycle_fast_close`, `test_redis_guard`, `test_guard`, `test_config`, `test_ops_safety`, `test_pre_migration_safety`); conftest exports it via `os.environ.setdefault("TORTOISE_TEST_NO_REDIRECT", ...)` so the product-side redirect honors it. CI may override.
- **fixtures/redis-guard note (P0-1 interaction):** the redis-guard fixtures (`bad_relative_path.py` etc.) are subprocess scripts, not `test_`-prefixed modules — the caller-frame exemption cannot key them. `tools/redis-guard.py` must pop `TORTOISE_DB_URI` (and `TORTOISE_TEST_MODE`) when executing fixture scripts so the relative-path-reject fixtures run in an embedded-clean env (same pattern as `test_hard_reject.py:131`).
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
def test_embedded_only_marker_skips_when_uri_set(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    # a marker-semantics probe test is marked embedded_only — the autouse
    # skip fixture must skip it
    ...
```

**Step 2: Implement the autouse skip + apply markers to the 3 tests**; add the 6 stems to `TEST_NO_REDIRECT_STEMS`.

**Step 3: Run to verify** — URI unset: the 3 tests pass. URI set (explicitly, via monkeypatch or env — P2-17: test_audit/test_index_directory won't see the URI in CI until P3, so this step is the P2-era verification for them): they skip with `embedded-only` reason; skip-guard (FalkorDB-substring, read from junitxml per Task 3) does not trip; the manifest sees the reasoned skip (junitxml `<skipped>`), so P2 stays green. Also run `tests/fixtures/redis-guard/` against the redis-guard tool with URI set to confirm the pop keeps the reject fixtures green.

**Step 4: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/_embedded.py tests/test_audit.py tests/test_pack_state.py tests/test_index_directory.py tests/test_markers.py tools/redis-guard.py
git commit -m "feat(testdb): embedded_only per-test markers + carve-out redirect exemption (epic #1647 P2, D-2/H1/P0-1)"
```

---

### Task 6: CI phase-2 flip — half b → docker + coverage-manifest wiring + dual-lane

**Intent:** The P2 flip (decision D-3=A): half b runs on the provisioned falkordb service with job-level `TORTOISE_DB_URI`; half a stays embedded as the canary; skip-guard manifest goes fail-closed on half b. This is the first divergence-discovery run.

**Acceptance:**
- `test` job, `half: b` gains job-level `TORTOISE_DB_URI: docker://:falkordb@localhost:6379` (passworded service already provisioned; services block unchanged) **scoped to `full == true` (plan-review P1-7)** — the tier-2 PR selection (`a_files`/`b_files`, surface-based) is not verified against the carve-out exemption and must not ride the redirect; half a unchanged (embedded canary).
- Manifest generation step on half b: expected nodeids = **the SAME `$FILES` the pytest run builds** (plan-review P1-7 — the old plan keyed off `--emit-push-matrix` lists, which can diverge from `matrix.files`/`a_files`/`b_files` in the tier-2 path) × `--collect-only` with the same `-m 'not track_b'` filter.
- Skip-guard step on half b passes `--manifest` + `--junitxml` (Task 3); fail-closed — **gated on pytest rc==0 AND non-empty `$FILES`** (plan-review P1-7: the "no selected files" path writes rc=0 with no junitxml; with a manifest that would false-red, so the guard must skip when `$FILES` is empty). Half a unchanged (embedded canary, current guard).
- No test-slow / e2e / track_b changes (P2 out of scope — they follow in P3).
- P2 divergence confirmation (Task 8 Step 4) runs against the observed half-b results.

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
  URI="docker://:falkordb@localhost:6379"
fi
```

and export it on the pytest step: `TORTOISE_DB_URI: $URI` (plus `TORTOISE_TEST_MODE: "1"` is already exported by conftest; `TORTOISE_TEST_NO_REDIRECT` comes from conftest too — CI override optional).
3. Manifest generation before pytest on half b (same `$FILES` + `-m 'not track_b'` collect-only).
4. The pytest invocation gains `--junitxml=/tmp/junit.xml -o junit_family=xunit1` (Task 3's authoritative observed-set source; `-r fEs` stays for the human summary).
5. Skip-guard step: `RC=$(cat ${RUNNER_TEMP:-/tmp}/pytest-rc)`; skip when `$RC != 0` or the files file is empty; else `python3 tools/skip-guard.py /tmp/pytest.log --junitxml=/tmp/junit.xml --manifest <expected-nodeids>`.
6. If `$FILES` is empty, the run step keeps its current early-exit and the guard skips (no manifest).

**Step 3: Local pre-flight (the divergence-discovery run + the E2E-2/E2E-3 docker smoke, plan-review P1-6)** — run the half-b file list against the local docker containers, PLUS the wipe-heavy and concurrency surfaces that CI won't see until P3 (test_projection, test_search_engine_gaps, test_index_surfacing, test_about_event_untangle are half-a/slow; test_embedded_concurrency is test-slow — their docker behavior is P2-gated via this run):

```bash
TORTOISE_DB_URI="docker://:falkordb@localhost:6379" \
TORTOISE_TEST_NO_REDIRECT="test_embedded_lifecycle_fast_close,test_redis_guard,test_guard,test_config,test_ops_safety,test_pre_migration_safety" \
uv run pytest tests/test_search_engine.py tests/test_ranking.py tests/test_pack_state.py ... \
  tests/test_projection.py tests/test_search_engine_gaps.py tests/test_index_surfacing.py \
  tests/test_about_event_untangle.py tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes \
  -v --timeout=300 -m 'not track_b' --maxfail=20 -r fEs --junitxml=/tmp/junit.xml -o junit_family=xunit1
```

Expected: half-b DB-agnostic tests run against docker; the 3 busy-error tests skip (marker); the 6 carve-out files run embedded (exemption); the wipe-heavy surfaces run green via `_wipe_or` (E2E-2); the live-writer concurrency test runs with 0 busy errors (E2E-3); any embedded-calibrated assertion break = a divergence-confirmation item (Task 8 Step 4), not a silent fix. Then verify the manifest closes: generate the expected nodeids from the same file list × collect-only and run `tools/skip-guard.py /tmp/pytest.log --junitxml=/tmp/junit.xml --manifest <expected-nodeids>` → exit 0.

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
6. E2E-1 (docker half round-trip) green; E2E-2 + E2E-3 green via the Task 6 Step 3 docker smoke (their CI-wide gate is P3 — the wipe-heavy files are half-a/slow and test_embedded_concurrency is test-slow; plan-review P1-6); E2E-6 (tripwire + manifest) green.

---

## Phase 3 — Both halves → docker + default inversion

### Task 9: Phase-3 flip — half a, canary drop, `skip_if_no_falkor` retirement, hygiene gating

**Intent:** Complete the default inversion (P3): the whole fast matrix runs on docker; embedded runs only the carve-out. Drop the embedded canary after N consecutive green docker runs (N=5 per epic target #2); retire the vacuous `skip_if_no_falkor` early-return from migrated files; gate `TORTOISE_FAST_ATEXIT`/`_redislite_hygiene` to embedded sessions; flip the orphan assert; follow the non-fast surfaces (test-slow, track_b, e2e/) to docker.

**Acceptance:**
- Both fast halves set job-level `TORTOISE_DB_URI`; manifest covers both halves; `TORTOISE_TEST_NO_REDIRECT` expands to the full 16-file carve-out set (Task 5 list + test_embedded_lifecycle, test_reaper, test_reaper_orphan, test_embedded_concurrency, test_redis_guard already there, test_flip_gate, test_hard_reject, test_migrate_db, test_backup_e2e, test_hosted_backup, test_projection_lifecycle, test_ops_safety already there, test_pre_migration_safety already there, fixtures/redis-guard/*, bench/test_smoke_embedded — the research-brief §4.1 verified list).
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
**Step 4:** Flip the orphan assert for docker halves; carve-out job keeps <20.
**Step 5:** Wire test-slow / track_b / e2e/ to docker; extend the manifest; add the dedicated carve-out job (URI unset, `TORTOISE_TEST_CARVE_OUT=1`, the 342-test set) as E2E-4's CI home — this job is what keeps the P4 URI-required enforcement (Task 10 Step 1a) from failing the carve-out.
**Step 6:** After 5 consecutive green CI runs, remove the embedded canary lane (half-a embedded config retired — the dual-lane was bounded per D-3).
**Step 7:** Commit each step (`git commit -m "ci(testdb): phase-3 ..."`).

---

## Phase 3 Gate (P3 acceptance)

1. Full fast matrix green on docker services; half a ≤ ~50m, half b clears the 55m watchdog (target ≤ ~45m) — no >20% regression vs baseline (E2E-5).
2. Manifest passes with zero FalkorDB-reasoned skips and zero missing nodeids on both halves (E2E-6).
3. 0 flaky failures attributable to docker-vs-embedded divergence in 5+ consecutive CI runs (epic indicator #2).
4. Orphan assert on docker halves ≈ 0 (E2E-7).
5. Carve-out 342 still green on embedded (dedicated URI-unset `TORTOISE_TEST_CARVE_OUT=1` job, E2E-4).
6. E2E-8 conformance passes in both modes.

---

## Phase 4 — Allowlist/reaper shrink

### Task 10: Allowlist shrink (34 → ~21) + reaper demotion + end-state verification

**Intent:** Delete the debt (epic indicators #3/#4): the drift registry shrinks to the true carve-out; the reaper loses its CI-correctness role; the divergence change list is filed canonically; end-state O/I/T verified.

**Acceptance:**
- `RAW_EMBEDDED_ALLOWLIST` (tests/test_embedded_lifecycle.py:42) shrinks 34 → ~21: the 7 drift-registered files (test_export_cli, test_import_endpoint, test_projection, test_indexes, test_ingest, test_supplementary, test_semantic_extractor) + 6 non-carve-out entries (test_de2e1_entity_extraction, test_extractor_doc, test_extractor_priors, test_index_github_cli, test_m1, test_remove_context_migration) migrate out; `e2e/hosted/test_12_selfhost_migration` reviewed (selfhost path IS docker — likely drift fix; if it hardcodes embedded paths, fix rather than carve out); `repro/reproduce_redislite_leak.py` + fixtures + 16 carve-out files stay.
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
**Step 1a:** Implement the P4 enforcement (plan-review P1-9): conftest session-start URI-required check + `TORTOISE_TEST_CARVE_OUT` gate (above); wire `.github/workflows/post-merge-validation.yml` to job-level URI + junitxml-reconciled manifest; verify the carve-out job passes with `TORTOISE_TEST_CARVE_OUT=1` and that a URI-less migrated run fails with the actionable message.
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
| Redirect fires in prod code when `TORTOISE_DB_URI` is set (path construction in prod: backup.py:134/144, ingest.py:471, `__main__.py:13` rebuild, migrate_db.py:75/184, hosted_api.py:6254/8263/8357, pipeline_cli.py:139) | **plan-review P0-4/P1-8:** the redirect is gated on `TORTOISE_TEST_MODE=1` (conftest-only) — prod never redirects, writes can never land invisibly on a server-mode `test_` graph, and `skip_health_check`/`allow_nonstandard_path` are preserved on the redirect path. The old row claimed the graph guard sufficed — it only blocks wipes, not writes | `tests/test_redirect_seam.py::test_no_redirect_without_test_mode` + `test_no_arg_does_not_redirect` |
| `wipe_server()` misclassifies a real graph as test-prefixed | Exact prefix filter; fail-closed skip; non-loopback refusal. The non-loopback unit test constructs with `skip_health_check=True` (plan-review P2-10 — the constructor's health probe raises before `wipe_server` otherwise) | `tests/test_wipe_server.py` |
| A half-b file's assertions are embedded-calibrated and break on docker | Divergence-confirmation pass is the gate; docker-calibrated expectations added (same class as D9); the documented change list absorbs them, not silent fixes | Task 8 Step 4 + E2E-8 |
| Missing docker service → whole half fails/skips | Skip-guard coverage manifest → red (fail-closed); never green-skip | `tests/test_skip_guard.py` manifest cases |
| Redirect inert + embedded succeeds → job green on the wrong backend | Backend-identity tripwire fails the session at start | `tests/test_backend_identity_tripwire.py` |
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

### Good-vs-Easy deferrals (rule 5 — explicit records)

| Issue | Chosen (Good) | Deferred alternative | Cost of deferral | Rationale |
|---|---|---|---|---|
| P0-1 exemption key | Caller-test-module via frame inspection | Conftest-injected per-call module allowlist plumbing | Requires threading an allowlist through every construction/fixture call site; frame inspection is self-contained at the seam | Frame inspection is one helper + one lookup; an injected allowlist would touch the seam fixtures AND every raw construction path for no robustness gain |
| P0-3 reconciliation | junitxml authoritative (lossless nodeid + reason) | Resolve `file:line` → nodeid against collect-only with count handling | collect-only emits no line numbers; `[N]` count disambiguation across parametrized cases is fragile; still leaves the reason source truncated | junitxml is deterministic, terminal-width-independent, and verified against the real repo format; the file:line route adds machinery for a worse guarantee |
| P0-4 prod gate | `TORTOISE_TEST_MODE` test-session gate | Prod-side allowlist of never-redirect paths | Prod allowlist must track every future `path=` construction (drift-prone); cannot catch new prod tools | The test-session signal is one env line in conftest with ZERO prod surface; prod tools are correct by construction |
| P1-6 P2 gates | Honest relabel + bounded docker pre-flight smoke | Flip a bounded test-slow leg to docker in P2 | Extra CI minutes + changes the P2 wall measurement baseline | The pre-flight smoke covers the gate's intent (wipe + concurrency on docker) without altering the matrix shape or the P2 half-b data point |
