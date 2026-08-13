# Plan: #880 — CI `test` job green (semantic-dedup product fix + cli_serve 401 + fast-job rebalance)

Date: 2026-08-10 · Issue: #880 · Complexity: standard · Team: epistemic-team
Status: **converged — approach B (Reuse the Degrade Chain), 2 PRs, product first**

All runtime figures below were **measured on 2026-08-10** on this worktree (M-series Mac,
`.venv`, `HF_HUB_OFFLINE=1`, embeddings extra installed, HF model cached) unless noted.

---

## 1. Decision

**PICK: Approach B — "Reuse the Degrade Chain"** — with two validated corrections:

1. **Correction to B's premise:** B claimed singleton reuse "removes 12x 90MB instantiations →
   test_sdk_group3 may drop below 60s SLOW_FILES bar". **Falsified by measurement.**
   test_sdk_group3 = **120s (32 passed, both model states)** and is **redislite-teardown
   dominated** (~4.3s teardown × 32 tests; model instantiation is ~2s warm-cache and only
   ~3-4 tests hit the existing-items path). _encode reuse saves ~2-4s, not minutes.
   **Consequence: test_sdk_group3 moves to SLOW_FILES** (it clears the 60s bar at 120s).
2. **Correction to B's observability claim:** the `EmbeddingModel` load thread logs load
   failure at **INFO without exc_info** (embeddings.py `_load`). Restoring #330 observability
   for the offline-model case requires a 1-line upgrade to `WARNING + exc_info` in
   embeddings.py (part of PR1).

### Why B beats A and C

| Criterion | A (minimal patch) | B (reuse degrade chain) | C (root-cause + capacity) |
|---|---|---|---|
| Product-bug outcome | Passes test; leaves **two divergent embedding paths** that already drifted once | **Converges codebase**: `SentenceTransformer(` instantiation exists ONLY in embeddings.py after the fix; `_encode` shared by cross_lens.py + sdk.py | Same as B |
| Failure-mode coverage | Model-present-but-encode-fails → checkpoint's generic warning → **hash-only fallback files duplicates** | Encode failure → **TF-IDF** → near-dupes still caught | Same as B |
| Edge cases | ImportError-only | Full degrade chain: model missing → TF-IDF; sklearn missing → zeros; empty texts; 60s negative cache; 30s thread-load timeout | Same as B |
| Observability (#330) | Silent-ish fallback with generic warning | WARNING + exc_info at the failure point (after correction 2) | Same as B |
| Runtime fix | move test_ep_sources (valid) | move test_ep_sources + test_sdk_group3 (validated) | watchdog 30→35m: **converts problem to permanent wall-time**; anti-pattern that produced this issue |
| Diff/proportionality | smallest | small (6-line sdk change + 1-line log upgrade + regression test) | largest (fixture rewrite) |
| CLI test fidelity | subprocess kept | subprocess kept | **loses real-process coverage** — the #493 bug (CLI subprocess import shadowing → 401) was exactly a subprocess-path defect |

### Rejected alternatives

- **A (Minimal Patch)** — rejected: fixes the one observed exception type but leaves
  `_semantic_dedup`'s duplicated model-instantiation path divergent from `embeddings._encode`.
  That duplication is the *root cause of this bug class* (the degrade chain was built for
  #399/#160 and `_semantic_dedup` re-implemented a weaker copy of it). A's failure mode for
  "model present, encode fails" is strictly worse than B's (hash-only fallback files
  duplicates). A **would have been better** only if the embeddings extra were not installed
  in CI and no embedding-based tests existed — neither is true.
- **C (In-process `_cmd_key_create` + capacity bump)** — rejected as a whole; its cli_serve
  piece is the escalation path, its capacity piece is the anti-pattern. C's in-process
  rewrite **would have been better** if the poll+settle proves flaky in practice (2+ CI
  failures after PR2): then in-process key-create is the only deterministic option. C's
  watchdog bump 30→35m **would have been better** only if the suite were at its final size —
  it is not (it has grown every week; #647, #798, #880).

---

## 2. Problem statement

Three independent defects keep the pre-merge `test` job red (#880):

1. **PRODUCT BUG — `tortoise/sdk.py::_semantic_dedup` (2859-2892).** `except ImportError`
   only. With sentence-transformers installed and the model missing under `HF_HUB_OFFLINE`,
   `SentenceTransformer("all-MiniLM-L6-v2")` raises `LocalEntryNotFoundError` — MRO
   FileNotFoundError → **OSError**, NOT ImportError (verified) — which escapes to
   `checkpoint()`'s `except Exception` → silent hash-only fallback → `duplicates == 0` →
   `test_sdk_group3::test_semantic_dedup_catches_near_duplicates` fails. Reproduced locally
   with an empty HF cache (exact CI failure: `Semantic dedup failed — falling back to
   hash-only dedup: We couldn't connect to 'https://huggingface.co'...`).
2. **TEST FLAKE — `tests/test_cli_serve.py::test_local_http_roundtrip_lands_in_team_graph`
   (620-677), line 634.** `local_db` fixture spawns `python -m tortoise key create` as a
   subprocess; the test then opens a **fresh** `TortoiseSDK(namespace="registry")` on the same
   DB. ce4605a fixed only the test's *second* in-process handle. The residual race is the
   cross-process handoff: the subprocess's redislite server shutdown/RDB flush can lag under
   CI load → registry opens with a stale/mid-write RDB → key not found → spurious 401.
   Local handoff is deterministic (verified: key visible on immediate open) — the race is
   load-dependent, i.e. CI-only.
3. **RUNTIME FLOOR — fast job killed at 29:59 (1921 passed at kill), exit 137.** The fast
   suite is ~30m+ on a GH runner. Measured fast-job residents over the 60s bar:
   **test_ep_sources = 140s** (real EP propagation compute: 18s/14s/14s tests),
   **test_sdk_group3 = 120s** (teardown-dominated). Controls: test_cli_serve = 50s;
   test_ep_selector + test_ep_projections + test_directional_impl_fix = 40s combined
   (NOT slow — do not move).

**Ride-along:** `python-ci.yml`'s watchdog rc check tests only `rc -eq 124`; the observed
kills exit **137** (timeout's `-k 10` SIGKILL after pytest ignored SIGINT mid-test) or **2**
(pytest's own SIGINT summary), so the WATCHDOG count banner never prints.

---

## 3. Proposed solution — 2 PRs, product first

### PR 1 (product, independently shippable): `fix/880-semantic-dedup-degrade-chain`

| File | Change |
|---|---|
| `tortoise/sdk.py` | `_semantic_dedup` vector step routes through `embeddings._encode` (6 lines, replaces the try/except ImportError block) |
| `tortoise/embeddings.py` | `EmbeddingModel._load` failure log: INFO → **WARNING + exc_info** (#330 observability). P4 refinement (verifier 2026-08-10): keep `ImportError` at INFO (designed zero-dep path) and use WARNING only for other exceptions — the regression test asserts a WARNING with "unavailable", which the LocalEntryNotFoundError path (non-ImportError) still satisfies |
| `tests/test_sdk_group3.py` | New regression test pinning the exact CI failure mode (offline + model missing → TF-IDF degrade → duplicates==1 + WARNING) |

### PR 2 (CI green): `fix/880-ci-green-rebalance`

| File | Change |
|---|---|
| `tests/test_cli_serve.py` | `local_db` fixture: RDB mtime settle after subprocess exit; roundtrip test: bounded poll for the bootstrap key before asserting auth |
| `.github/workflows/python-ci.yml` | SLOW_FILES += `tests/test_ep_sources.py`, `tests/test_sdk_group3.py` (+ comment with measurement basis); watchdog rc check widened to 124\|137\|2 in BOTH jobs |

**No watchdog/timeout budget changes** (rejected: C's capacity bump). `--timeout=300`,
`--maxfail=20`, timeout-minutes 36/90 unchanged.

### Sequencing rationale (2 PRs, not 1)

- The sdk.py fix is a **product bug** with its own regression test; it must ship and be
  reviewed on its own merits (precedent: ce4605a shipped CI de-flakes separately from product
  fixes).
- PR2's file-move decision depends on **post-PR1** measurements (the re-measure gate) — the
  singleton changes checkpoint's model-load behavior, so PR2 must measure against fixed code.
- PR2 moves test_sdk_group3 — the file containing the product-fixed test — so it must land
  **after** PR1 (test-slow would otherwise run the unfixed failure, red on PR2).
- PR1's own CI run will show the *known pre-existing* runtime red (documented in the PR
  body; the issue already documents main merging red for weeks). PR2 is what turns the gate
  green per the issue's indicators.
- 1 PR **would have been better** only if the product fix were not independently shippable —
  it is (no test file depends on it being in the fast job; the fast job runs it until PR2).

---

## 4. Implementation steps

### PR 1 — product fix

**Step 1.1 — `tortoise/sdk.py::_semantic_dedup`** (validated, currently applied in this
worktree): replace the duplicated instantiation block with the shared degrade chain:

```python
        new_texts = [item["content"] for item, _ch in candidates]
        # Single degrade chain (embeddings._encode): real model → TF-IDF → zeros.
        # #880: this used to instantiate SentenceTransformer directly. A missing
        # model under HF_HUB_OFFLINE raises LocalEntryNotFoundError (an OSError,
        # NOT ImportError) → checkpoint()'s except Exception silently dropped
        # semantic dedup to hash-only and near-duplicates were filed. Reuse the
        # EmbeddingModel singleton + degrade chain so any load/encode failure
        # degrades to deterministic TF-IDF instead of hash-only.
        from .embeddings import _encode
        all_vecs, _degraded = _encode(existing + new_texts)

        e_vecs, n_vecs = all_vecs[:len(existing)], all_vecs[len(existing):]
```

(`_norm`, slicing, and the `max_sims` filter below stay untouched. Zero-vector degrade → all
candidates filed — same as hash-only, by design. `checkpoint()`'s `except ImportError` branch
becomes vestigial; kept deliberately as defense-in-depth.)

**Step 1.2 — `tortoise/embeddings.py::EmbeddingModel._load`** (validated, applied):
upgrade the load-failure log so the #880 failure mode is observable:

```python
            except Exception as e:  # noqa: BLE001
                # #880: a load failure (e.g. LocalEntryNotFoundError when the
                # model is missing under HF_HUB_OFFLINE) is a real degrade —
                # warn with traceback so it stays observable (#330 contract).
                logger.warning(
                    "sentence-transformers unavailable — embeddings degrade: %s",
                    e, exc_info=True,
                )
                result["model"] = None
```

**Step 1.3 — regression test** in `tests/test_sdk_group3.py::TestCheckpoint` (prototype
validated: fails on old code with the exact CI warning, passes on fixed code, 6.75s):

```python
    def test_semantic_dedup_model_load_failure_offline_degrades_to_tfidf(
            self, sdk, monkeypatch, caplog):
        """#880 regression: sentence_transformers installed + model missing under
        HF_HUB_OFFLINE raises LocalEntryNotFoundError (an OSError, NOT ImportError).
        The old _semantic_dedup let it escape to checkpoint()'s except Exception →
        silent hash-only fallback → near-duplicates filed. The EmbeddingModel
        degrade chain must land on TF-IDF instead: near-duplicate still caught,
        degrade observable as a WARNING.
        """
        import logging
        import tempfile
        import pytest
        import huggingface_hub.constants as hf_const
        from tortoise.embeddings import EmbeddingModel

        # Embedded-only dev environments (neither sklearn nor sentence_transformers)
        # can't exercise this path — same guard as the sibling dedup test.
        pytest.importorskip("sklearn")
        pytest.importorskip("sentence_transformers")

        EmbeddingModel._reset()          # clear any cached model + negative cache
        try:
            with tempfile.TemporaryDirectory() as empty_hf:
                # huggingface_hub bakes HF_HOME/HF_HUB_CACHE/HF_HUB_OFFLINE at
                # import time — setenv AFTER import is INERT (is_offline_mode()
                # reads the module attribute at call time). Patch the runtime
                # constants so the pin is deterministic on every machine.
                monkeypatch.setattr(hf_const, "HF_HUB_CACHE", empty_hf)
                monkeypatch.setattr(hf_const, "HF_HUB_OFFLINE", True)
                sdk.checkpoint([{"content":
                                 "deploy the new feature to production servers tonight"}])
                with caplog.at_level(logging.WARNING, logger="tortoise.embeddings"):
                    result = sdk.checkpoint(
                        [{"content":
                          "deploy the new feature to production servers today"}],
                        threshold=0.7,
                    )
        finally:
            EmbeddingModel._reset()      # never poison the 60s negative cache

        assert result["duplicates"] == 1, "near-duplicate must be caught via TF-IDF degrade"
        assert result["filed"] == 0
        assert any("unavailable" in r.message for r in caplog.records), \
            "load failure must be observable (WARNING, #330)"
```

Design notes: pins the **real mechanism** (actual `LocalEntryNotFoundError` from a real
offline load against an empty cache — not a mocked exception); the `HF_HUB_CACHE` constant
patch gives import-order immunity (the suite imports huggingface_hub long before this test);
`_reset()` in `finally` prevents the 60s negative-cache from degrading subsequent embedding
tests.

**Step 1.4 — run PR1 validation** (below).

### PR 2 — CI green

**Step 2.1 — re-measure gate** (post-PR1 worktree):

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m pytest \
  tests/test_ep_sources.py tests/test_sdk_group3.py -q --durations=8
```

Expected: **~140s / ~120s** (measured 2026-08-10; the singleton saves only seconds —
teardown/EP-compute dominated). Both > 60s bar → **move both**. If (contrary to
measurement) test_sdk_group3 comes in < 60s, keep it in the fast job and move only
test_ep_sources.

**Step 2.2 — `tests/test_cli_serve.py`.** In `local_db` fixture, after `subprocess.run`
returns, settle the RDB handoff; in the roundtrip test, poll for the key:

```python
def _wait_rdb_settled(db, settle_s=1.0, max_wait_s=10.0):
    """#880: the key-create subprocess's redislite server is gone (atexit
    SHUTDOWN SAVE), but the RDB flush can lag under CI load — a handle opened
    mid-write sees a stale registry → 401. Wait until the file stops changing."""
    deadline = time.monotonic() + max_wait_s
    last_mtime = None
    stable_since = None
    while time.monotonic() < deadline:
        mtime = os.path.getmtime(db) if os.path.exists(db) else None
        now = time.monotonic()
        if mtime == last_mtime:
            if stable_since is None:
                stable_since = now
            elif now - stable_since >= settle_s:
                return
        else:
            stable_since = None
        last_mtime = mtime
        time.sleep(0.1)
```

Fixture: `...; key = match[0].split(":", 1)[1].strip(); _wait_rdb_settled(db); yield db, key, env`.

Roundtrip test (replaces the bare `result_set[0][0]` at line ~634):

```python
    # #880: bounded poll — the subprocess's registry write must be visible
    # before auth assertions (CI-load cross-process handoff race). Each
    # iteration opens a FRESH SDK handle: a cached projection would reload
    # the RDB only at server start, so re-querying the same handle can never
    # see a key written after boot (inert poll — verifier finding, 2026-08-10).
    team_id = None
    for _ in range(10):
        probe = TortoiseSDK(namespace="registry")
        rows = probe._get_registry().query(
            "MATCH (k:APIKey) RETURN k.team_id").result_set
        probe.close()
        if rows:
            team_id = rows[0][0]
            break
        time.sleep(1.0)
    assert team_id is not None, "registry key not visible after 10s (cross-process handoff)"
    sdk = TortoiseSDK(namespace="registry")
```

(Note: `test_cli_serve.py` needs `import time` — currently imports json/os/shutil/subprocess/sys/tempfile.)

**Step 2.3 — `.github/workflows/python-ci.yml` SLOW_FILES**: add `tests/test_ep_sources.py`
and `tests/test_sdk_group3.py`; update the env comment with the 2026-08-10 measurement basis
(local M-series, post-#880-fix: ep_sources 140s EP compute; sdk_group3 ~120-220s redislite
teardown 4.3-5.0s × 32-33 — both > 60s bar; verifier re-measured 218s/33 on 2026-08-10,
use the Step 2.1 re-measure gate value in the final comment). Do NOT move:
test_ep_selector / test_ep_projections / test_directional_impl_fix (measured 40s combined).

**Step 2.4 — watchdog rc widening** (both `test` and `test-slow` steps):

```bash
          if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ] || [ "$rc" -eq 2 ]; then
```

with a comment: `# #880: 124 = timeout SIGINT kill; 137 = -k 10 SIGKILL after pytest
ignored INT mid-test; 2 = pytest's own SIGINT summary — the count banner must print for all
three.` (`exit $rc` unchanged — 137/2 still fail the job, which is correct.)

**Step 2.5 — PR2 validation** (below).

---

## 5. Testing strategy

- **Product PR:** the regression test pins the CI failure mode (validated red→green). The
  full `TestCheckpoint` class + the offline-missing whole-file run cover the degrade
  contract. Cross-lens/search embedding paths are untouched (already `_encode`-based) —
  run `tests/test_cross_lens.py` + `tests/test_embeddings.py` to confirm no singleton-state
  interference (the regression test's `_reset()` is scoped to its own try/finally).
- **CI PR:** the 401 fix is defensive (race is CI-load-only, not locally reproducible);
  correctness is verified by code inspection + the existing 4 `local_db` tests passing, and
  flake-freedom by CI repetition (see verification).
- **No tests skipped/deleted** — issue indicator (3): `--collect-only` count unchanged.

## 6. Verification plan (how we prove `test` green in CI)

Note: main-push runs get cancelled by the concurrency group's queueing; **PR runs complete**
— verification is done on PRs.

1. **PR1 (product):** CI run on the PR. Check: `test_sdk_group3` passes **regardless of HF
   cache state** — the cache-miss state is pinned by the regression test; the cache-hit
   state by the existing tests. The fast job's known runtime red is documented in the PR
   body (pre-existing, tracked by this issue). `pytest --collect-only` count unchanged.
2. **PR2 (CI):** CI run on the PR. Read the job logs:
   - `test` job: `--durations=15` tail + summary line — expect **success**, pytest time
     ≈ 21.5–24.5 min (29:59 − ~140s − ~120s, runner-multiplied), i.e. 5.5–8.5 min margin
     under the 30m watchdog.
   - `test-slow` job: expect success ≈ 39–42 min of the 75m watchdog (was 33:39; +2 files).
   - WATCHDOG banner present-and-correct on any non-zero rc (124/137/2).
   - Both jobs' `pytest exit code: 0`.
3. **Flake re-check:** if PR2 is green, merge, then confirm the next 2 PRs' runs stay green
   (the 401 flake was load-dependent; 2 clean runs is the bar the issue's history implies).
4. **Escalation:** if the fast job still approaches the 30m watchdog on a PR run, move the
   next-largest fast-job file by CI `--durations` (candidates by size proxy only, must
   measure first: test_supabase_control / test_backup_sweep / test_writer_inventory). Do NOT
   bump the watchdog (C's anti-pattern).

## 7. Acceptance criteria (issue #880 O/I/T)

1. `test` job completes `success` on a PR touching Python code.
2. `test-slow` job completes `success` on the same PR.
3. `pytest --collect-only` count unchanged vs today (no tests skipped/deleted).
4. Regression test exists and fails on pre-fix `_semantic_dedup` (proven locally).
5. WATCHDOG count banner prints for rc 124/137/2 (ride-along).

## 8. Runtime prerequisites

- Local: `pip install -e '.[test,embeddings]'` (or `uv sync --group dev` + embeddings
  extra) — the bug only manifests with sentence-transformers installed.
- CI: unchanged — embedded FalkorDBLite (no Docker), HF model cache steps stay
  best-effort, `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` on both jobs, Python 3.12,
  `TORTOISE_SECRET_PEPPER: test-static-pepper`.
- The worktree currently carries the validated PR1 fix (sdk.py + embeddings.py) — commit it
  as PR1; revert only if the approach is overturned.

### PR1 branch hygiene (P2, verifier 2026-08-10)

Create PR1 on a **fresh branch off origin/main** (e.g. `fix/880-semantic-dedup-degrade`),
NOT the current `fix/881-hero-cta-pe-on` worktree branch (which also carries #881's
`website/product.html` change). Stage **only**:

```bash
git add tortoise/sdk.py tortoise/embeddings.py tests/test_sdk_group3.py
```

The untracked plan doc (`docs/plans/2026-08-10-880-ci-green-semantic-dedup.md`) rides
along in PR1 (or its own docs commit). `website/product.html` ships separately in #881.

### P1 resolution — singleton negative-cache blast radius (coherence check, 2026-08-10)

Concern: routing `_semantic_dedup` through `_encode` → `EmbeddingModel.get()` means a
checkpoint load failure sets the 60s `_FAIL_COOLDOWN_S`, degrading cross_lens/search for
60s. **Controller verdict: IGNORE as P2 with documented rationale.**

1. The 60s negative cache is **designed, pre-existing behavior** (#399, documented in
   `get()`'s docstring): "return None immediately instead of blocking up to 30s per
   request in a degraded environment". `search_points` and `cross_lens` already route
   through it — checkpoint is one more caller of the same designed path, not a new
   class of behavior.
2. In any environment where checkpoint's load fails, cross_lens/search would fail
   identically (same process, same singleton, same cache state) — the cooldown is set
   only slightly earlier and makes their degrade fast + consistent (each consumer
   otherwise blocks 30s trying the same doomed load).
3. TF-IDF degrade is functional (near-duplicates still caught; search still returns
   results) — the 60s window is fail-safe, not data-loss. Old code's alternative was
   hash-only fallback which **filed duplicates** (the actual bug).
4. Verified: cross_lens (29) + embeddings (12) + sdk_group3 (33) all pass with the fix;
   regression test resets the singleton in finally, so no cross-test contamination.

Escalation: if a future issue shows real consumers harmed by the cooldown, revisit with
option (a) from the coherence review (bypass cooldown in checkpoint path) — filed as a
note, not a blocker.

Create PR1 on a **fresh branch off origin/main** (e.g. `fix/880-semantic-dedup-degrade`),
NOT the current `fix/881-hero-cta-pe-on` worktree branch (which also carries #881's
`website/product.html` change). Stage **only**:

```bash
git add tortoise/sdk.py tortoise/embeddings.py tests/test_sdk_group3.py
```

The untracked plan doc (`docs/plans/2026-08-10-880-ci-green-semantic-dedup.md`) rides
along in PR1 (or its own docs commit). `website/product.html` ships separately in #881.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Fast job still near 30m after moves (runner variance) | Re-measure gate + escalation list (step 6.4) |
| `hf_const.HF_HUB_CACHE` patch couples test to huggingface_hub internals | It fails loudly on rename (good); fallback = patch `sentence_transformers.SentenceTransformer.__init__` to raise `LocalEntryNotFoundError` (mechanism-agnostic pin) |
| 401 flake persists after poll+settle (2+ CI failures) | **Escalate to C's cli_serve piece**: in-process `_cmd_key_create` (capsys) — the deterministic option; revisit only then |
| Regression test's `_reset()` poisons later embedding tests | `finally: _reset()` clears the 60s negative cache; verified locally with TestCheckpoint + cross_lens runs |
| test-slow grows toward 75m | Current headroom ≈ 33m after moves; revisit sharding (out of scope today — proportionality) |
