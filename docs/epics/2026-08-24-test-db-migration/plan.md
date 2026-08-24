<!-- research-path: docs/epics/2026-08-24-test-db-migration/research-brief.md -->

# Epic #1647 Implementation Plan — Migrate test suite from embedded FalkorDBLite to real FalkorDB (docker)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make real FalkorDB (docker, matching the selfhost product) the default test DB — one URI-aware seam flips all test DB construction; embedded FalkorDBLite remains only for the 342-test behavioral carve-out — eliminating the test debt caused by the parallel embedded stack (reaper/orphans/EmbeddedStoreBusyError/concurrency divergence).

**Team:** epistemic-team

**Architecture:** A four-phase strangler rollout (P1 seam+hermeticity with zero behavior change → P2 one fast-matrix half on docker → P3 both halves → P4 allowlist/reaper shrink). The core mechanism is a **class-level URI-aware redirect** in `FalkorProjection.__init__` (the `path is not None` branch): when `TORTOISE_DB_URI` (supported scheme) is set, raw `path=` constructions construct server-mode from the URI with a guard-passing derived graph name `test_<stem>_<hash8(path)>`; when unset, the embedded path is byte-for-byte today's code. Hermeticity is name-first: guard-passing graph names + a server-mode `wipe_server()` filtered to `test_`/`tortoise_test_`-prefixed graphs, refusing non-loopback hosts. Fail-closed semantics are enforced by extending `tools/skip-guard.py` with a coverage manifest (any expected nodeid missing from both PASSED and SKIPPED-reasoned → red) plus a conftest backend-identity tripwire.

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
| — | **Seam redirect** (new unit surface) | unit (tests/test_redirect_seam.py) | redirect fires when it must not (prod path) | URI unset → `_is_embedded is True` byte-identical; URI set → server mode + derived `test_*` name; explicit graph_name honored; `TORTOISE_TEST_NO_REDIRECT` exempts stems |
| — | **wipe_server** (new unit surface) | unit (tests/test_wipe_server.py) | wiping a non-test graph (data loss) | wipes only `test_`/`tortoise_test_`-prefixed graphs; refuses non-loopback hosts; embedded `wipe()` unchanged |
| — | **Coverage manifest** (new unit surface) | unit (tests/test_skip_guard.py) | vacuous early-return (nodeid vanishes) | expected nodeid missing from PASSED + SKIPPED-reasoned → red; missing log + manifest → red (flip of `test_missing_log_is_not_a_failure`) |

**Complexity ratings (from issue):** Architecture = complex, Config = standard, Ontology = low. Verification routing: integration + e2e depth for the migration surfaces; unit for the 3 new mechanisms; no UX domain (no UI); no content domain.

---

### E2E test catalog (runnable gates)

Each E2E ships as a concrete artifact (new test file, CI job config, or existing-test adaptation). Setup/assertion/gates per E2E; phase gates below reference these.

- **E2E-1** — `tests/test_round_trip_parity.py` (new): parametrized `create_point → search` round-trip over `TORTOISE_DB_URI` set/unset. Assertion: point id, content_hash, search hit list, vector results identical on the D1/D5-identical paths; D6/D8 sides assert their documented expectation. **Gates:** P1 mechanism → P2 docker half proves it.
- **E2E-2** — shared-graph tier fixture + `wipe_server()`; exercised by existing wipe-heavy files (test_projection, test_search_engine_gaps, test_a9_direct_edge_traversal, test_index_surfacing, test_recall_gaps_subgraph, test_about_event_untangle) under URI set + a control test (`test_bare_test_graph_wipe_still_raises`) in `tests/test_wipe_server.py`. **Gates:** P1 unit → P2 wipe-heavy half-b files on docker.
- **E2E-3** — `tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes` + live-writer portion (:130) under job URI; the 3 busy-error tests stay embedded-marked (skip visibly on docker, pass on embedded). **Gates:** P2 (half b, no busy errors) → P3 (both halves).
- **E2E-4** — carve-out job (URI unset) running exactly the 342-test set; skip-guard exempts them (redislite-availability class ≠ FalkorDB-availability). **Gates:** P1 (untouched) → P4 (post-shrink registry consistency).
- **E2E-5** — fast-matrix CI jobs; half-b wall is the P2 data point, both-halves the P3 gate. **Gates:** P2 (half b) → P3 (both halves).
- **E2E-6** — skip-guard coverage manifest + backend-identity tripwire; outage simulation = run migrated set without URI → guard must go red. **Gates:** P2 (manifest on half b) → P3 (both halves).
- **E2E-7** — orphan-assert CI step re-targeted per surface. **Gates:** P3 (docker ~0) → P4 (reaper demotion; dev-machine <20 without reaper).
- **E2E-8** — `tests/test_divergence_conformance.py` (new): asserts D2/D3 recovery raises on server, D6 composite only on server, D8 HNSW only on server, D11 busy-error embedded-only, D12 multi-tenant on server. **Gates:** P1 (expectation split) → P2 (side-by-side confirmation) → P3 (both modes enforced).

---

## Phase 1 — Seam + hermeticity (zero behavior change)

> Embedded remains the default; `TORTOISE_DB_URI` is not set anywhere in P1, so every new mechanism is **dormant**. Acceptance: full 6,837-test embedded suite green (identical to baseline), redirect/wipe/manifest unit-tested, no orphan delta.

### Task 1: Class-level URI-aware redirect + fixture-seam URI-awareness

**Intent:** Create the single seam (decision D-1=A) — one place flips ALL test DB construction to real FalkorDB when `TORTOISE_DB_URI` is set. Raw `FalkorProjection(path=...)` constructions (114 no-graph-name sites in 27 files) redirect automatically; seam fixtures (`shared_proj`/`sdk_factory`/`shared_embedded_db`) get URI-aware branches. Inert in P1 (URI unset) — embedded path byte-for-byte today.

**Acceptance:**
- With `TORTOISE_DB_URI` unset, `FalkorProjection(path=...)` constructs identically to today (`_is_embedded is True`, embedded subclass, AOF/relative-path semantics intact) — verified by the pre-existing embedded suite plus the new inertness test.
- With `TORTOISE_DB_URI` set to a supported scheme (`docker`/`redis`/`rediss`), `FalkorProjection(path=...)` constructs server-mode (`_is_embedded is False`, host/port/user/pass from the URI — `from_uri` semantics), with graph name = explicit `graph_name` if given, else derived `test_<stem>_<hash8(path)>` (guard-passing, deterministic, collision-safe).
- No-arg `FalkorProjection()` does NOT redirect (D-1 scope: explicit `path=` only) — still resolves the canonical embedded path. Captured by recording `explicit_path = path is not None` BEFORE the `resolve_db_path()` no-arg fallback (verified: `__init__` resolves no-arg → path at L311-313, so a naive path-branch check would also capture no-arg).
- `TORTOISE_TEST_NO_REDIRECT=<comma-separated file stems>` exempts matching stems from the redirect (mechanism only — list wired in Task 5).
- `shared_proj`/`sdk_factory`/`shared_embedded_db` URI-aware: URI set → server construction with `test_suite_<uuid>`-style graph names; unset → today's construction.
- Unsupported URI scheme → embedded construction (fail-safe, no crash).

**Files:**
- Modify: `tortoise/projection/__init__.py:303-392` (`__init__` path branch — the redirect insertion; `from_uri` at L547 reused for URI parsing)
- Modify: `tests/_embedded.py` (`shared_proj` URI branch; `TEST_NO_REDIRECT_STEMS` constant)
- Modify: `tests/conftest.py` (`sdk_factory` ~L83 URI branch; `shared_embedded_db` URI branch; export `TORTOISE_TEST_NO_REDIRECT` via `os.environ.setdefault` from the `tests/_embedded.py` constant so the product-side redirect reads the in-repo list without importing `tests/`)
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


def test_no_redirect_env_exempts_stem(uri_env, monkeypatch):
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "seam-test-d")
    proj = FalkorProjection("/tmp/seam-test-d.db")
    try:
        assert proj._is_embedded is True  # exempted stem stays embedded
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

At the top of the `if path is not None:` branch, insert:

```python
# Epic #1647 (D-1=A): class-level URI-aware redirect. When TORTOISE_DB_URI
# names a supported scheme, raw path= constructions construct server-mode
# from the URI (from_uri semantics) instead of spawning an embedded
# redislite server — one seam flips the whole test suite. Explicitly
# explicit path= only (D-1 option a): no-arg keeps the embedded canonical
# path (captured via explicit_path above). TORTOISE_TEST_NO_REDIRECT
# (comma-separated file stems) exempts carve-out files (Task 5).
_uri = os.environ.get("TORTOISE_DB_URI")
if explicit_path and _uri and _is_supported_uri_scheme(_uri):
    _stem = os.path.basename(path)
    _no_redirect = {
        s.strip() for s in
        os.environ.get("TORTOISE_TEST_NO_REDIRECT", "").split(",") if s.strip()
    }
    if _stem not in _no_redirect:
        # from_uri semantics: parse the URI, construct host-mode, derive a
        # guard-passing graph name when the caller gave none.
        _parsed = urlparse(_uri)
        _validate_uri_scheme(_parsed.scheme)
        _graph = graph_name if graph_name != "tortoise" else \
            f"test_{_stem}_{hashlib.sha1(path.encode()).hexdigest()[:8]}"
        return cls(host=_parsed.hostname or "localhost",
                   port=_parsed.port or 16379,
                   username=_parsed.username or None,
                   password=_parsed.password or None,
                   graph_name=_graph,
                   ssl=(_parsed.scheme == "rediss"))
```

> The redirect must NOT be `return cls(...)` recursion-unsafe — construct via `FalkorProjection.__new__`-style delegation or refactor the server branch into a shared `_from_uri_parts` helper; the plan-review must sanity-check this. The key invariant: the server branch (host-mode) code is reused, NOT duplicated, and the embedded branch (AOF config, relative-path reject, `FalkorDB(path, serverconfig=...)` subclass) is entirely skipped on the redirect path.

Then make the seam fixtures URI-aware (`tests/_embedded.py` `shared_proj`, `tests/conftest.py` `sdk_factory` + `shared_embedded_db`): when `TORTOISE_DB_URI` (supported scheme) is set → construct via `FalkorProjection.from_uri(uri, graph_name=f"test_suite_{os.urandom(4).hex()}")` (shared tier) or per-test uuid names (exact-set tier); unset → today's construction unchanged.

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_redirect_seam.py -v`
Expected: all PASS (with a live docker on 6379 for the redirect cases).

**Step 5: Prove P1 inertness (zero behavior change)**

Run: `uv run pytest tests/test_projection.py tests/test_search_engine_gaps.py tests/test_a9_direct_edge_traversal.py -v` (URI unset)
Expected: green, identical to pre-P1 — the redirect never fires.

Run: `uv run pytest tests/ --collect-only -q | tail -1` → `6837 tests collected` (no test-count drift).

**Step 6: Commit**

```bash
git add tortoise/projection/__init__.py tests/_embedded.py tests/conftest.py tests/test_redirect_seam.py
git commit -m "feat(testdb): class-level URI-aware redirect + URI-aware seam fixtures (epic #1647 P1)"
```

---

### Task 2: `wipe_server()` — server-mode hermeticity wipe (graph-granularity, test-prefix-filtered, non-loopback refusal)

**Intent:** Deliver the P0 hermeticity mechanism (decision D-4=A): the existing `wipe()` refuses server mode (verified, `tests/_embedded.py:56-65`), and the graph guard rejects bare `test`/`tortoise` (verified, `_assert_test_graph` L980-1003 + `'test'.startswith(('test_','tortoise_test'))` is False). Docker hermeticity = per-test wipe of **test-prefixed graphs only**, fail-closed, on loopback hosts only.

**Acceptance:**
- `wipe_server(proj)` enumerates `list_graphs()`, wipes (DETACH DELETE) ONLY graphs starting `test_`/`tortoise_test_`; every other graph is skipped (never wiped).
- `wipe_server(proj)` raises `RuntimeError` when the projection's host is not loopback (`localhost`/`127.0.0.1`/`::1`) — protecting a remote dev/shared server (research Q6, decision D-4).
- Existing `wipe()` unchanged: all-graphs wipe on embedded; server refusal retained.
- Unit surface green; the shared-graph tier (E2E-2) uses it.

**Files:**
- Modify: `tests/_embedded.py` (add `wipe_server`; keep `wipe` untouched)
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
    proj = FalkorProjection(host="db.internal.example.com", port=6379,
                            graph_name="test_remote")
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
```

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_wipe_server.py -v` → all PASS (docker 6379 up).

**Step 5: Prove no behavior change on embedded**

Run: `uv run pytest tests/test_projection.py -v -k "wipe"` → embedded wipe path green.

**Step 6: Commit**

```bash
git add tests/_embedded.py tests/test_wipe_server.py
git commit -m "feat(testdb): wipe_server() server-mode hermeticity wipe (epic #1647 P1, D-4)"
```

---

### Task 3: Skip-guard inversion — coverage manifest + `test_workflow_keeps_rs` / `test_missing_log_is_not_a_failure` flips

**Intent:** Kill the vacuous-pass class (#942) at 6,837-test scale: on migrated halves, any expected nodeid missing from both PASSED and SKIPPED-reasoned must go red. Builds the mechanism in P1 (dormant — no manifest passed); wires it to half b in P2 (Task 6).

**Acceptance:**
- `tools/skip-guard.py` gains a manifest mode: `--manifest <expected-nodeids.txt>` — every expected nodeid must appear in the log's PASSED lines (`-v` progress, already emitted by CI) or reasoned SKIPPED lines (`-rs` summary, already emitted via the pinned `-r fEs`); a missing nodeid → exit 1.
- PASSED-nodeid source decision (scope-review M2): **`-v` progress lines** (`tests/file.py::Test::test_name PASSED`) — the CI fast job already runs `-v`; nodeids appear before the ` PASSED` marker so the 80-col truncation (which only affects trailing skip reasons) cannot drop them. junitxml (`--junitxml`) is the fallback if parametrized-id edge cases surface; no pytest invocation change in P1.
- `test_missing_log_is_not_a_failure` (tests/test_skip_guard.py:92) flips: with `--manifest` passed, a missing log = every expected nodeid absent → **red** (was exit 0). The no-manifest path keeps exit 0 (back-compat for the current invocation).
- `test_workflow_keeps_rs` (L108) stays green and gains a manifest-parsing assertion (the `-r fEs` contract is what feeds the reasoned-SKIPPED set).
- Existing `FalkorDB`-reason matcher + `_live_utils.py` exclusion unchanged; carve-out files' redislite-reasoned skips never match the `FalkorDB` substring.

**Files:**
- Modify: `tools/skip-guard.py`
- Modify: `tests/test_skip_guard.py` (`test_missing_log_is_not_a_failure` flip; `test_workflow_keeps_rs` extension; new manifest cases: missing-nodeid red, PASSED satisfies, reasoned-skip satisfies, carve-out exemption, vacuous early-return)
- Test: `tests/test_skip_guard.py` (extended)

**Step 1: Write the failing tests** (append to `tests/test_skip_guard.py`)

```python
def test_manifest_missing_nodeid_is_red(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("tests/test_ep_directional.py::TestX::test_y PASSED\n")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("tests/test_ep_directional.py::TestX::test_y\n"
                        "tests/test_projection.py::test_something\n")
    # expected nodeid for test_projection absent from PASSED and SKIPPED
    rc = run_guard(str(log), str(manifest))
    assert rc == 1  # fail-closed — vacuous early-return detected


def test_manifest_passed_nodeid_satisfies(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("tests/test_ep_directional.py::TestX::test_y PASSED\n")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("tests/test_ep_directional.py::TestX::test_y\n")
    rc = run_guard(str(log), str(manifest))
    assert rc == 0


def test_manifest_reasoned_skip_satisfies(tmp_path):
    log = tmp_path / "pytest.log"
    log.write_text("SKIPPED [1] tests/test_config.py:44: redislite unavailable\n")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("tests/test_config.py:44\n")
    rc = run_guard(str(log), str(manifest))
    assert rc == 0  # reasoned skip ≠ vanished nodeid (no FalkorDB substring)


def test_missing_log_with_manifest_is_red(tmp_path):
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("tests/test_ep_directional.py::TestX::test_y\n")
    rc = run_guard(str(tmp_path / "no-such.log"), str(manifest))
    assert rc == 1  # FLIPPED from the historical exit 0


def test_missing_log_without_manifest_stays_green(tmp_path):
    rc = run_guard(str(tmp_path / "no-such.log"), None)
    assert rc == 0  # back-compat: no manifest, no evidence
```

**Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_skip_guard.py -v`
Expected: 4 of the new tests FAIL (no manifest mode yet); the existing `test_missing_log_is_not_a_failure` still passes (not yet flipped).

**Step 3: Implement**

- Add `--manifest <path>` arg to `tools/skip-guard.py`.
- Parse expected nodeids (one per line, `#`-comments allowed).
- Collect observed: PASSED nodeids from `^\s*tests/[^\s]+ PASSED` lines (strip the trailing ` PASSED`); reasoned-SKIPPED nodeids from `SKIPPED [N] tests/file.py:line:` summary lines.
- An expected nodeid absent from both sets → violation (print + exit 1).
- When `--manifest` is absent: current behavior (missing log → exit 0).
- When `--manifest` present and log missing/unreadable → every expected nodeid absent → exit 1 (the flip).

**Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_skip_guard.py -v` → all PASS (including the flipped `test_missing_log_is_not_a_failure`, updated to pass `--manifest`).

**Step 5: Commit**

```bash
git add tools/skip-guard.py tests/test_skip_guard.py
git commit -m "feat(testdb): skip-guard coverage manifest — vanished nodeids go red (epic #1647 P1)"
```

---

### Task 7: Graph-name sweep — explicit non-test graph names + no-namespace SDK bulk-wipers + derived-name verification

**Intent:** Make every migrated construction docker-safe BEFORE the flip. The class-level redirect (Task 1) auto-derives guard-passing names for the 114 no-graph-name `FalkorProjection` sites (measured this plan-pass; scope-brief's "93" counted a narrower site class). The sweep's real scope is the **explicit non-test graph names** + the no-namespace `TortoiseSDK` constructions whose computed graph is bare `tortoise` (which fails the guard on bulk-wipe) + a red-herring check that no bulk-wipe test still targets `test`/`tortoise`.

**Acceptance:**
- All explicit non-test graph names in migrated files renamed to `test_*`/`tortoise_test_*` (measured: 9× `graph_name="test"` in test_embedded_lifecycle/test_embedded_lifecycle_fast_close [carve-out — untouched]/test_extractor_priors [migrate → rename], 2× `graph_name="tortoise"` in test_projection/test_remove_context_migration, 1× `graph_name="crash_live"` in test_import_endpoint).
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
```
Expected: the explicit-name sites above; the bulk-wipe users list (D4 table: test_projection, test_search_engine_gaps, test_a9_direct_edge_traversal, test_index_surfacing, test_recall_gaps_subgraph, test_about_event_untangle, test_pre_migration_safety, test_embedded_concurrency live-reset).

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

For each migrated-file site: `graph_name="tortoise"` → `graph_name="test_projection_suite"` (etc.). Carve-out files (`test_embedded_lifecycle*`) untouched. Run the touched files embedded → green (name-only change).

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

**Step 3: Implement** — conftest session-autouse fixture:

```python
@pytest.fixture(scope="session", autouse=True)
def _assert_backend_identity():
    """Epic #1647 E2E-6 tripwire: on docker-URI sessions, the FIRST
    constructed projection must be server mode — a dormant redirect would
    silently run the migrated suite on embedded and pass green."""
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if not uri or not uri.split(":", 1)[0] in ("docker", "redis", "rediss"):
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

**Intent:** Implement decision D-2=A (per-test embedded-only marker for the 3 `EmbeddedStoreBusyError` tests) + the scope-review H1 requirement (`TORTOISE_TEST_NO_REDIRECT` file-stem exemption for the 6 half-b carve-out files, so the P2 job-level URI cannot flip them to docker and break their embedded-specific assertions).

**Acceptance:**
- New `embedded_only` marker registered in `pyproject.toml` (alongside `track_b`) + a conftest autouse skip: URI set + marker present → `pytest.skip("embedded-only: ...")` (visible skip, the D-2 skip mechanism — distinct from the per-file redirect exemption); URI unset → test runs normally.
- Applied to exactly 3 tests: `tests/test_audit.py` (d) case (~L478/L504), `tests/test_pack_state.py::TestBackfillScript::test_dry_run_default_makes_no_writes` (L668), `tests/test_index_directory.py::test_e2e9_cross_process_embedded_overlap` (L1852).
- `TEST_NO_REDIRECT_STEMS` in `tests/_embedded.py` = the 6 half-b carve-out files at P2 (`test_embedded_lifecycle_fast_close`, `test_redis_guard`, `test_guard`, `test_config`, `test_ops_safety`, `test_pre_migration_safety`); conftest exports it via `os.environ.setdefault("TORTOISE_TEST_NO_REDIRECT", ...)` so the product-side redirect honors it. CI may override.
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

**Step 3: Run to verify** — URI unset: the 3 tests pass. URI set: they skip with `embedded-only` reason; skip-guard (FalkorDB-substring) does not trip; manifest sees the reasoned skip.

**Step 4: Commit**

```bash
git add pyproject.toml tests/conftest.py tests/_embedded.py tests/test_audit.py tests/test_pack_state.py tests/test_index_directory.py tests/test_markers.py
git commit -m "feat(testdb): embedded_only per-test markers + carve-out redirect exemption (epic #1647 P2, D-2/H1)"
```

---

### Task 6: CI phase-2 flip — half b → docker + coverage-manifest wiring + dual-lane

**Intent:** The P2 flip (decision D-3=A): half b runs on the provisioned falkordb service with job-level `TORTOISE_DB_URI`; half a stays embedded as the canary; skip-guard manifest goes fail-closed on half b. This is the first divergence-discovery run.

**Acceptance:**
- `test` job, `half: b` include gains job env `TORTOISE_DB_URI: docker://:falkordb@localhost:6379` (passworded service already provisioned; services block unchanged) + `TORTOISE_TEST_NO_REDIRECT` (Task 5's 6 stems, exported via conftest — CI override optional).
- Manifest generation step on half b: expected nodeids = `tools/ci_selection.py --emit-push-matrix` half_b list × `--collect-only` (a small `--emit-manifest <files>` mode on skip-guard.py or a generator script).
- Skip-guard step on half b passes `--manifest`; fail-closed (missing nodeid or FalkorDB-reasoned skip → red). Half a unchanged (embedded canary, current guard).
- No test-slow / e2e / track_b changes (P2 out of scope — they follow in P3).
- P2 divergence confirmation (Task 8 Step 4) runs against the observed half-b results.

**Files:**
- Modify: `.github/workflows/python-ci.yml` (`test` job: half-b include env, manifest generation + guard wiring)
- Modify: `tools/skip-guard.py` (manifest generation mode, if not already covered by Task 3)
- Test: `tests/test_ci_selection.py` (if the manifest generator lives in `tools/ci_selection.py`)

**Step 1: Add the manifest generator** — `tools/skip-guard.py --emit-manifest` reads a file list + a collect-only output and emits expected nodeids (or a `tools/ci_selection.py --emit-manifest` sibling). Unit-test it.

**Step 2: Edit the workflow** — half-b include:

```yaml
include:
  - half: a
    files: ${{ needs.changes.outputs.matrix_a }}
  - half: b
    files: ${{ needs.changes.outputs.matrix_b }}
    uri: "docker://:falkordb@localhost:6379"
```

and in the run step: `env: TORTOISE_DB_URI: ${{ matrix.uri }}` (absent on half a); manifest generation before pytest on half b; skip-guard step passes `--manifest`.

**Step 3: Local pre-flight (the divergence-discovery run)** — run the half-b file list against the local docker containers:

```bash
TORTOISE_DB_URI="docker://:falkordb@localhost:6379" \
TORTOISE_TEST_NO_REDIRECT="test_embedded_lifecycle_fast_close,test_redis_guard,test_guard,test_config,test_ops_safety,test_pre_migration_safety" \
uv run pytest tests/test_search_engine.py tests/test_ranking.py tests/test_pack_state.py ... -v --timeout=300 -m 'not track_b' --maxfail=20 -r fEs
```

Expected: half-b DB-agnostic tests run against docker; the 3 busy-error tests skip (marker); the 6 carve-out files run embedded (exemption); any embedded-calibrated assertion break = a divergence-confirmation item (Task 8 Step 4), not a silent fix.

**Step 4: Commit + push + observe CI** — record half-b wall; confirm half a (embedded) still green; confirm the skip-guard manifest passes.

```bash
git add .github/workflows/python-ci.yml tools/skip-guard.py tools/ci_selection.py tests/test_ci_selection.py
git commit -m "ci(testdb): phase-2 flip — fast half b on docker + coverage manifest (epic #1647 P2, D-3)"
```

---

## Phase 2 Gate (P2 acceptance)

1. Half b green on docker with job URI; zero FalkorDB-reasoned skips (guard red otherwise); manifest passes (zero vanished nodeids).
2. Half a (embedded control) green — same as P1.
3. Divergence confirmation (Task 8 Step 4): observed divergences match the D1–D16 table exactly; zero unexpected (each unexpected = P2 blocker).
4. Half-b wall measured (expect 57–58m → ≤ ~40m) — the P3 merge-decision input.
5. The 3 busy-error tests skip visibly on the docker half, pass on embedded (E2E-3/5 AC5).
6. E2E-1 (docker half round-trip), E2E-2 (wipe-heavy half-b files), E2E-3 (no busy errors), E2E-6 (tripwire + manifest) all green.

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
**Step 5:** Wire test-slow / track_b / e2e/ to docker; extend the manifest; add the dedicated carve-out job (URI unset, the 342-test set) as E2E-4's CI home.
**Step 6:** After 5 consecutive green CI runs, remove the embedded canary lane (half-a embedded config retired — the dual-lane was bounded per D-3).
**Step 7:** Commit each step (`git commit -m "ci(testdb): phase-3 ..."`).

---

## Phase 3 Gate (P3 acceptance)

1. Full fast matrix green on docker services; half a ≤ ~50m, half b clears the 55m watchdog (target ≤ ~45m) — no >20% regression vs baseline (E2E-5).
2. Manifest passes with zero FalkorDB-reasoned skips and zero missing nodeids on both halves (E2E-6).
3. 0 flaky failures attributable to docker-vs-embedded divergence in 5+ consecutive CI runs (epic indicator #2).
4. Orphan assert on docker halves ≈ 0 (E2E-7).
5. Carve-out 342 still green on embedded (dedicated URI-unset job, E2E-4).
6. E2E-8 conformance passes in both modes.

---

## Phase 4 — Allowlist/reaper shrink

### Task 10: Allowlist shrink (34 → ~21) + reaper demotion + end-state verification

**Intent:** Delete the debt (epic indicators #3/#4): the drift registry shrinks to the true carve-out; the reaper loses its CI-correctness role; the divergence change list is filed canonically; end-state O/I/T verified.

**Acceptance:**
- `RAW_EMBEDDED_ALLOWLIST` (tests/test_embedded_lifecycle.py:42) shrinks 34 → ~21: the 7 drift-registered files (test_export_cli, test_import_endpoint, test_projection, test_indexes, test_ingest, test_supplementary, test_semantic_extractor) + 6 non-carve-out entries (test_de2e1_entity_extraction, test_extractor_doc, test_extractor_priors, test_index_github_cli, test_m1, test_remove_context_migration) migrate out; `e2e/hosted/test_12_selfhost_migration` reviewed (selfhost path IS docker — likely drift fix; if it hardcodes embedded paths, fix rather than carve out); `repro/reproduce_redislite_leak.py` + fixtures + 16 carve-out files stay.
- `test_no_new_raw_embedded_constructions` (test_embedded_lifecycle.py:186) passes against the shrunk list (it reads the list from source).
- Default `pytest` requires `TORTOISE_DB_URI`; the carve-out is the sole embedded surface.
- Reaper demoted: scheduled reaper narrows to local-dev hygiene; CI loses its correctness dependency (docker halves produce no orphans); conftest `_redislite_hygiene` still sweeps embedded sessions on dev boxes.
- End-state measurements: orphans < 20 on a dev machine WITHOUT the scheduled reaper (epic indicator #2, re-measure of the 4-orphan baseline); ≥90% of tests on docker (measured ≈95% — 342/6,837 carve-out); fast-matrix wall ≤ 20% regression.
- Optional: matrix merge (D-3) — single fast job if both measured halves < ~40m (decision recorded with measured walls).

**Files:**
- Modify: `tests/test_embedded_lifecycle.py` (allowlist shrink)
- Modify: reaper scheduling/ops config (demotion to hygiene-only)
- Create: `docs/divergence-change-list.md` (canonical D1–D16 filing; or link into the epic docs)
- Modify: `.github/workflows/python-ci.yml` (optional matrix merge)
- Test: `tests/test_embedded_lifecycle.py::test_no_new_raw_embedded_constructions`, `tests/test_skip_guard.py`

**Steps:**

**Step 1:** Remove the 13 migrated files from the allowlist; run the enforcement test → green (they've been running docker since P2/P3 — the move is a registry update, not a first run).
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
| Redirect fires in prod code when `TORTOISE_DB_URI` is set (path construction in prod) | D-1 scope limits it to `explicit path=`; the graph guard still refuses non-test bulk-wipes server-side — fail-closed | `tests/test_redirect_seam.py::test_no_arg_does_not_redirect` + guard |
| `wipe_server()` misclassifies a real graph as test-prefixed | Exact prefix filter; fail-closed skip; non-loopback refusal | `tests/test_wipe_server.py` |
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
| **O1** — default run uses `TORTOISE_DB_URI` for DB-agnostic tests; embedded only for the carve-out | P3/P4: CI fast matrix env has job-level URI; dedicated URI-unset job runs exactly the 342-test carve-out; measured docker share ≈95% (6,495/6,837) vs target ≥90% | P3 Gate 1, P4 Gate 2 + `tests/test_embedded_lifecycle.py::test_no_new_raw_embedded_constructions` |
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
- **PASSED-nodeid source:** `-v` progress lines (M2) — junitxml fallback documented in Task 3.
