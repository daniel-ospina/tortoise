---
title: "Embedding-Based Cross-Lens Candidate Generation (#399) — Implementation Plan"
type: data
domain: data
status: live
created: 2026-08-08
updated: 2026-08-08
ownedBy: epistemic-team
subjects:
  team: epistemic-team
doc_status: live
aboutSubjects: epistemic-team
aboutObjects: Point, Source
---

<!-- research-path: https://github.com/daniel-ospina/tortoise/issues/399 (controller-verified scoping; empirical threshold calibration measured 2026-08-07 in .venv, sentence-transformers 5.7.0, all-MiniLM-L6-v2) -->

# #399 Embedding-Based Cross-Lens Candidate Generation — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Ship the embedding-based cross-lens candidate-generation CORE as a testable isolated module (`tortoise/cross_lens.py`), wire it into the existing MockExtractor `multi_source` branch, and fix the structural root cause of "0 cross-lens connections" — without activating production mining (#6306's remainder).

**Team:** epistemic-team
**Role:** (not available — omitted)

**Architecture:** Two modules split by responsibility. `tortoise/embeddings.py` gains a shared `_encode` (routes through the existing `EmbeddingModel` singleton — fixes a real 90MB-per-call reload bug — with deterministic TF-IDF fallback) and a pure-numpy `cosine_similarity_matrix`, keeping `find_cross_source_matches`/`search_points` backward-compatible. NEW `tortoise/cross_lens.py` provides `find_cross_lens_matches(points, *, threshold=0.40, lens_key=None, encode=None)` — recall-only candidate generation keyed by LENS (not speaker), zero graph writes, zero EventAPI. The MockExtractor `multi_source` branch stops dropping `provenance.source_id`, calls the new function with `lens_key="source"`, removes the ≥3-shared-content-words gate, and lets the existing cue-word regexes decide IMPL/NAND direction — candidates without cue words are recorded on `self._last_candidates` (the documented #6306 integration point) but never become operators.

**TIER:** complex — new module, cross-file refactor, extractor behavior change, TDD required. Worktree already provisioned (`399-embedding-matching`).

### Pattern Research

**Skipped — plan touches zero NEW third-party dependencies.** Both libraries used (`sentence-transformers`, `scikit-learn`) are existing, pinned extras in `pyproject.toml` (`embeddings = ["sentence-transformers>=3,<6", "scikit-learn>=1.0"]`), already imported by `tortoise/embeddings.py`, and already exercised in CI (`pip install -e '.[embeddings]'` in `.github/workflows/post-merge-validation.yml:40`). The only library API touched is `model.encode(texts, show_progress_bar=False)` and `TfidfVectorizer().fit_transform(...)` — both already in production code paths. Verified in `.venv`: sentence-transformers 5.7.0, scikit-learn 1.9.0, numpy 2.5.1; model weights cached at `~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2`. **Library preflight:** `EmbeddingModel` singleton (lazy worker-thread load, 30s timeout, `_reset()` test hook) already exists and is used by `compute_embedding` — no new loading machinery needed.

### Integration Surface Map

| # | Surface | Boundary | Test layer | Failure mode |
|---|---------|----------|-----------|--------------|
| 1 | `embeddings._encode` shared encoder | singleton model ↔ TF-IDF fallback ↔ callers (`find_cross_source_matches`, `search_points`, `cross_lens`) | unit (existing suites as regression) | singleton bypassed (reload bug persists), fallback not deterministic |
| 2 | `find_cross_source_matches` wrapper | speaker-keyed core ↔ 12 legacy tests | unit (byte-unchanged tests) | semantic drift (threshold/speaker ordering), fresh model per call |
| 3 | `cross_lens.find_cross_lens_matches` | lens derivation ↔ same-lens exclusion ↔ threshold ↔ candidate shape | unit (new, deterministic fake encode) + 1 real-embedder e2e | wrong lens key, same-lens pairs leak in, wrong shape |
| 4 | extractor `multi_source` branch | candidates ↔ cue-gate ↔ `add_operator` / `_last_candidates` | integration (MockExtractor + fake log) | similarity-only IMPL, shared-words gate still present, provenance lost |
| 5 | extractor degraded/fallback | embeddings unavailable → all-pairs cue-gate | integration (existing `test_mock_extractor_multi_source_fallback`) | fallback broken by module-cache/lazy-import subtlety (P0 — see D9) |
| 6 | `mining.py` activation | `multi_source=True` wiring (NOT in scope) | none (#6306) | n/a — documented contract only |

## Problem

Term-index matching finds **0 cross-lens connections** because different lenses use different vocabularies for the same concepts. The issue's motivating pair — *"Cost inversion from fixed to variable"* (contemporary lens) and *"MVP now costs ~$100"* (practitioner lens) — shares zero words yet describes the same phenomenon.

### Verified structural root cause (5 independent defects, confirmed by code reading)

1. **No lens dimension exists.** `find_cross_source_matches` (`tortoise/embeddings.py:130`) keys on `speaker`. Document-mode points are stamped `speaker="document"` (`extractor.py:737-738`), so **all cross-document pairs share one speaker value and are excluded before similarity is ever consulted** — a structural guarantee of zero matches.
2. **Caller drops the lens identity.** `extractor.py:199-202` rebuilds `all_points` as `{content, speaker}` only — `provenance.source_id` (the actual lens) is discarded.
3. **`multi_source` is never invoked.** `mining.py:75` calls `extractor.run(transcript, source_id, api)` without `multi_source=True`, so the entire branch is dead in production.
4. **Cross-vocabulary candidates killed by a token-overlap gate.** `extractor.py:228-243` requires ≥3 shared content words (after stopword removal) before a similarity match becomes an IMPL — the exact killer for zero-word-overlap pairs like the motivating one.
5. **Explicit-assertion-only relation prompt.** `extractor.py:298` (`_RELATIONS_SYS`) forbids the LLM from inferring unstated relations — the verifier that #6306 needs doesn't exist yet in document mode.

### Empirical calibration (all-MiniLM-L6-v2, measured in `.venv` 2026-08-07)

| Pair | Cosine | Band |
|---|---|---|
| "Deployments must be automated for reliability" ↔ "Automating deployments is required for reliability" | **0.958** | near-duplicate |
| "Growth depends on distribution channels and partnerships" ↔ "Winning requires strong go to market and channel partners" | **0.448** | cross-vocab paraphrase (in-band) |
| "Cost inversion from fixed to variable" ↔ "MVP now costs ~$100" (issue's motivating pair) | **0.291** | boundary — topically similar, NOT logically implied |
| "Deployments must be automated for reliability" ↔ "Growth depends on distribution channels and partnerships" | 0.172 | weak/unrelated |
| "quantum physics research papers" ↔ "chocolate chip cookie recipes" | **0.121** | noise floor |

These measured values are the basis for the default threshold (0.40), the documented bands, and the test assertions with margins.

## Solution

### Component 1 — `tortoise/embeddings.py` refactor (shared core, backward compat)

- **`_encode(texts) -> tuple[np.ndarray, bool]`** — (vectors, degraded). Routes through `EmbeddingModel.get()` (singleton, all-MiniLM-L6-v2); on `None` (model unavailable) falls back to deterministic sklearn TF-IDF (`TfidfVectorizer().fit_transform(...)`). **Fixes the real bug:** `find_cross_source_matches` and `search_points` currently call `SentenceTransformer("all-MiniLM-L6-v2")` **fresh per call** (~90MB reload each — measured baseline: `tests/test_tortoise_search.py` alone takes 116s across 16 tests) instead of reusing the singleton `compute_embedding` already uses.
- **`cosine_similarity_matrix(vectors) -> np.ndarray`** — pure-numpy normalized dot product (the existing inline math extracted).
- **`find_cross_source_matches(points, threshold=0.75)`** becomes a thin speaker-keyed wrapper over `_encode` + helper with **exact** existing semantics (same signature, same speaker-exclusion, same `{src, dst, similarity, speakers}` shape, same result ordering). All 12 existing `tests/test_embeddings.py` tests stay green (11 byte-identical; test #12 needs a documented 1-line insertion — see D9b).
- **`search_points`** switches to the shared `_encode` (behavior preserved; TF-IDF now fits on `[query] + texts` — query enters vocab; relative ranking unchanged, verified against existing assertions).
- **Module docstring** gains the calibration table (near-dup 0.9+, cross-vocab band 0.35–0.51, noise floor ≤0.15, motivating 0.29 boundary).

### Component 2 — `tortoise/cross_lens.py` (NEW — the core deliverable)

```python
NEAR_DUPLICATE_THRESHOLD = 0.75   # issue-spec value; documented near-dup-only
DEFAULT_THRESHOLD = 0.40          # calibrated (see bands above)

def find_cross_lens_matches(points, *, threshold=DEFAULT_THRESHOLD,
                            lens_key=None, encode=None) -> list[dict]:
    """Recall-only cross-lens candidate generation (#399).

    NEVER writes to the graph and NEVER decides operator semantics —
    produces candidates for a verifier (extractor cue-gate today; LLM
    relation verifier in #6306). See module docstring for the #6306 contract.
    """
```

- **points:** `{point_id: {"content": str, ...}}` — content is the only required key.
- **Lens derivation chain** (when `lens_key is None`): `point["lens"]` → `point["source"]` → `point["provenance"]["source_id"]` → `point["speaker"]` → `"unknown"`. `lens_key` names an explicit field (e.g. `"source"`).
- **Excludes same-lens pairs.** Returns candidates sorted by similarity descending:
  `[{"src": id, "dst": id, "similarity": float, "lenses": [l1, l2], "speakers": [sp1, sp2], "degraded": bool}]`.
- **`encode` param:** injected deterministic encoder (`Callable[[list[str]], np.ndarray]`) for tests; default `None` → **lazy** `from tortoise.embeddings import _encode` (see D9 for why lazy). When the encoder degraded to TF-IDF, every candidate carries `degraded=True` and a log line is emitted.
- **No EventAPI, no graph writes, no `api`/`db` imports** — structural boundary enforced by review.

### Component 3 — `tortoise/extractor.py` MockExtractor `multi_source` wiring

1. Build `all_points` **with** `source` — stop dropping `provenance.source_id`:
   `all_points[pid] = {"content": ti, "speaker": sp, "source": source_id}`.
2. Replace `find_cross_source_matches(all_points, threshold=0.40)` with
   `find_cross_lens_matches(all_points, lens_key="source")` (threshold default 0.40).
3. **REMOVE the ≥3 shared-content-words gate** (`extractor.py:228-243`).
4. Iterate candidates directly (no O(N²) `matched_pairs` scan): existing `_SUPPORT`/`_REFUTE` regexes decide direction — support cue in either text → `IMPL`; else refute cue → `NAND`; **no cue → NO operator** (similarity alone never creates operators) but the candidate **is recorded** in `self._last_candidates`.
5. **Degraded mode** (encoder degraded to TF-IDF) **or** embeddings import failure → existing all-pairs cue-gate loop (`extractor.py:247-262`) preserved **verbatim** as fallback (byte-compatible with pre-#399 behavior; `test_mock_extractor_multi_source_fallback` must still pass).

## Design Decisions

### D1 — New module `cross_lens.py` instead of growing `embeddings.py` or the extractor
`embeddings.py` is shared search infra (`compute_embedding`/`search_points` consumed by `search_engine.py:871`, `session_indexer.py:411`, `projection/entities.py`, `hosted_api.py:100`) — it must not acquire cross-lens semantics. `cross_lens.py` is the #399 deliverable: isolated, recall-only, unit-testable with zero graph/API surface, and the natural home for #6306's verifier evolution. The extractor stays thin (cue-gate orchestration only).

### D2 — `embeddings.py` keeps backward compat (wrapper, not rewrite)
`find_cross_source_matches` and `search_points` are public API consumed by tests (`tests/test_embeddings.py`, `tests/test_embeddings_filters.py`, `tests/test_tortoise_search.py`, `tests/test_search_engine_gaps.py`, `tests/test_session_semantic_search.py`) and `search_engine.py`. The refactor is a **bug fix** (singleton reuse) plus internal sharing — semantics stay identical. This is what makes "legacy tests unchanged" a meaningful backward-compat proof. Side benefit: model loads drop from per-call to once-per-process (measured: affected-file suite ~7.5 min → ~1 min).

### D3 — Threshold policy: default 0.40, `NEAR_DUPLICATE_THRESHOLD = 0.75`
The issue spec proposed 0.75 — but the measured cross-vocab paraphrase band is **0.35–0.51** (e.g. the 0.448 pair), so 0.75 would find only near-duplicates and reproduce today's zero-cross-vocab result. 0.40 sits inside the target band with margin: above the noise floor (≤0.15) by 2.6×, and below the motivating pair (0.291) so **the boundary case stays a candidate-absent non-event** — topical similarity ≠ logical implication, and verification (not similarity) decides that. 0.75 is preserved as a named constant documenting the issue's original near-dup-only semantics. Thresholds are model-specific — documented in both module docstrings so a future model swap recalibrates.

### D4 — Lens derivation chain (lens → source → provenance.source_id → speaker → unknown)
The root cause is that speaker is a **person** dimension, not a **lens** dimension (document points all say `speaker="document"`). The chain picks the most lens-y field present, in stable priority order: explicit `lens` (future), `source` (extractor conversation mode), `provenance.source_id` (document mode — provenance is built by `tortoise/api.py:17` and always carries `source_id`), then `speaker` as a last-resort identity (makes `lens_key=None` behave like the old speaker-keyed function when no lens metadata exists), then `"unknown"`. Unknown-lens pairs are cross-lens by construction (excluded only when both sides resolve identically).

### D5 — Cue-gate verifier, NOT similarity→IMPL
Direct similarity→IMPL would fabricate epistemic structure: the 0.291 motivating pair is topically similar but neither implies nor refutes the other. IMPL/NAND edges feed EP belief propagation — an unverified IMPL would inflate confidence in both points. The existing cue-word regexes (`_SUPPORT`/`_REFUTE`, `extractor.py:19-22`) are the M0 semantic for *speaker-asserted* relations, so reusing them as the verifier is deterministic, zero-cost, and consistent with sequential-mode semantics. #6306 upgrades the verifier to the LLM without changing candidate generation.

### D6 — Candidate recording (`self._last_candidates`) = the #6306 integration point
Similarity computation is the expensive part; re-running it inside the #6306 verifier would double the cost. Recording the raw candidates (post-similarity, pre-cue) on the extractor instance makes the LLM verifier a pure consumer of an existing list. No graph writes, no new API — just an attribute (`MockExtractor` currently has no `__init__`; the attribute is set at the top of `run()`).

### D7 — Degraded mode (embeddings optional, extraction never fails)
Embeddings are explicitly optional infra (documented in `EmbeddingModel`'s docstring: "point creation and search must never depend on them"). Three degradation levels, all backward compatible:
- ST missing, sklearn present → `_encode` TF-IDF, candidates flagged `degraded=True` → extractor runs the existing all-pairs cue-gate (pre-#399 behavior).
- ST **and** sklearn missing → `_encode`'s lazy sklearn import raises ImportError → propagates to the extractor's existing `except` → all-pairs cue-gate.
- Any runtime failure → same `except` path.
`test_mock_extractor_multi_source_fallback` (which forces ImportError via `builtins.__import__` patch) is the regression proof.

### D8 — Alternative C (DI verifier protocol) REJECTED
A dependency-injection verifier protocol (abstract provider + registry) adds interface surface for a single in-repo consumer. YAGNI: the cue-gate is the deterministic verifier today; #6306 adds the LLM verifier via the documented `_last_candidates` contract. No DI needed to keep those two pluggable.

### D9 — Lazy `from tortoise.embeddings import _encode` inside `find_cross_lens_matches` (P0 subtlety)
`test_mock_extractor_multi_source_fallback` patches `builtins.__import__` to raise on any module name containing `"embeddings"`. pytest imports test files alphabetically → `tests/test_cross_lens.py` runs before `tests/test_extractor.py` → `tortoise.cross_lens` is already cached in `sys.modules`. A **module-level** `from tortoise.embeddings import _encode` in cross_lens would therefore never re-execute under the patch: the extractor's `from tortoise.cross_lens import find_cross_lens_matches` becomes a cache hit (patched `__import__` sees `"tortoise.cross_lens"` — no `"embeddings"` substring — and does not raise), the real embedding path runs, the fallback test's text finds no matches, and the test fails. **The function-level lazy import is what trips the patch inside `find_cross_lens_matches` → ImportError → extractor fallback.** Verified by reading the current test's mechanics.

### D9b — Legacy test #12 (`test_sentence_transformers_path`) requires exactly ONE inserted line
The test seeds `sys.modules["sentence_transformers"]` with a mock and asserts `mock_st.SentenceTransformer.assert_called_once_with("all-MiniLM-L6-v2")` + `mock_model.encode.assert_called_once()`. Under the singleton refactor, tests #1–11 (same file, same process) warm the singleton with the **real** model first, so test #12's mock is bypassed and both assertions fail. The fix is a 1-line insertion of `EmbeddingModel._reset()` before the mock seeding (the public test hook that already exists at `embeddings.py:69`) — after reset, the singleton is cold, the worker thread's import picks up the seeded mock, and **both existing assertions pass unchanged**. This is the ONLY legacy-test edit in the whole plan; the other 11 tests are byte-identical. (Note: the controller brief said "10 tests"; `tests/test_embeddings.py` actually contains 12 — this plan targets all 12.)

## Tasks

### Task 1: `embeddings.py` — shared encoder + cosine helper (backward-compat refactor)

**Intent:** Fix the fresh-model-per-call bug and expose one shared deterministic encode path for all embedding consumers.
**Acceptance:** `find_cross_source_matches` and `search_points` produce identical results to today (12 legacy `test_embeddings.py` tests green; 23 `test_embeddings_filters.py`; 16 `test_tortoise_search.py`; 128+4 `test_search_engine_gaps.py`+`test_session_semantic_search.py`); the SentenceTransformer is instantiated **at most once per process** (assert via test #12's mock after `_reset()`); `test_tortoise_search.py` runtime drops from ~116s toward <30s.
**Files:**
- Modify: `tortoise/embeddings.py` (add `_encode`, `cosine_similarity_matrix`; refactor `find_cross_source_matches`, `search_points`; extend module docstring with calibration table)
- Modify: `tests/test_embeddings.py:234-273` (test #12: insert `EmbeddingModel._reset()` — the single documented legacy edit)

**Step 1: Write the failing test (proves the bug) — a singleton-reuse regression test**

Add to `tests/test_embeddings_filters.py` (or `test_cross_lens.py`):
```python
def test_singleton_reused_across_calls():
    """#399: SentenceTransformer must be instantiated ONCE per process."""
    from unittest.mock import patch
    from tortoise.embeddings import EmbeddingModel, _encode
    EmbeddingModel._reset()
    with patch("tortoise.embeddings.SentenceTransformer") as mock_st:
        # _encode's singleton path imports ST lazily inside the worker thread;
        # seeding sys.modules is the deterministic route:
        import sys
        fake_mod = __import__("unittest.mock").mock.MagicMock()
        fake_st = fake_mod
        ...
```
> Simpler deterministic variant (no sys.modules seeding): assert `EmbeddingModel._model is EmbeddingModel._instance._model` identity and that two consecutive `_encode` calls invoke the model's `encode` twice on the **same** object:
```python
def test_singleton_reused_across_calls():
    from unittest.mock import MagicMock
    from tortoise.embeddings import EmbeddingModel, _encode
    EmbeddingModel._reset()
    fake = MagicMock()
    fake.encode.return_value = __import__("numpy").array([[1.0, 0.0]])
    with __import__("unittest.mock").patch.object(EmbeddingModel, "get", return_value=fake):
        _encode(["a"]); _encode(["b"])
    assert fake.encode.call_count == 2  # two encodes…
    # …but the singleton model object is the same instance across the process:
    assert EmbeddingModel.get() is fake
```

**Step 2:** Run `pytest tests/test_embeddings_filters.py -q` → the new test fails today (two calls = two `SentenceTransformer(...)` instantiations, no shared instance; also `_encode` doesn't exist yet → ImportError).

**Step 3: Implement `_encode` and `cosine_similarity_matrix` in `tortoise/embeddings.py`**

```python
def _encode(texts: list[str]) -> tuple[np.ndarray, bool]:
    """Encode texts → (vectors, degraded). degraded=True ⇒ TF-IDF fallback.

    Routes through the EmbeddingModel singleton (all-MiniLM-L6-v2) — never
    re-instantiates the model per call (#399). Falls back to deterministic
    sklearn TF-IDF when the model is unavailable. Embeddings stay optional:
    callers must tolerate degraded output.
    """
    if not texts:
        return np.zeros((0, 0)), False
    model = EmbeddingModel.get()
    if model is not None:
        try:
            vecs = model.encode(texts, show_progress_bar=False)
            if vecs is not None and len(vecs) > 0:
                return np.asarray(vecs, dtype=np.float64), False
        except Exception:  # noqa: BLE001 — model failures degrade, never raise
            logger.warning("embedding encode failed — TF-IDF fallback", exc_info=True)
    from sklearn.feature_extraction.text import TfidfVectorizer  # lazy: [embeddings] extra
    return TfidfVectorizer().fit_transform(texts).toarray(), True


def cosine_similarity_matrix(vectors: np.ndarray) -> np.ndarray:
    """Normalized dot product = cosine similarity. Pure numpy."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    v = vectors / norms
    return v @ v.T
```

**Step 4:** Refactor `find_cross_source_matches` to use them (identical semantics — same signature, speaker keying, `{src, dst, similarity, speakers}` shape, same loop order) and `search_points` (encode `[query] + texts` via `_encode`, slice, reuse `cosine_similarity_matrix`; snippet/limit logic untouched). Add the calibration table to the module docstring.

**Step 5:** Run `pytest tests/test_embeddings.py -q` → test #12 FAILS (warm singleton bypasses mock — expected, see D9b). Insert the one line (after `import tortoise.embeddings as mod`, which already exists):

```python
    import tortoise.embeddings as mod
    mod.EmbeddingModel._reset()      # ← the single legacy edit (#399, D9b)
    original = sys.modules.get("sentence_transformers")
    sys.modules["sentence_transformers"] = mock_st
```

**Step 6:** Run the full legacy regression battery:
```bash
.venv/bin/python -m pytest tests/test_embeddings.py tests/test_embeddings_filters.py \
  tests/test_tortoise_search.py tests/test_search_engine_gaps.py tests/test_session_semantic_search.py -q
```
Expected: all green (was 222 passed / 4 skipped). Sanity-check the runtime win on `test_tortoise_search.py` (~116s before → <30s after).

**Step 7: Commit** — `feat(399): shared embedding encoder via singleton (fixes per-call 90MB reload)`.

### Task 2: `tortoise/cross_lens.py` — the core deliverable

**Intent:** Provide recall-only cross-lens candidate generation — the missing lens dimension — as an isolated, graph-free, deterministically testable module.
**Acceptance:** `find_cross_lens_matches` derives lens per the chain, excludes same-lens pairs, sorts by similarity desc, returns the exact candidate shape with `degraded` flags, honors injected `encode`, never imports EventAPI/graph modules; new `tests/test_cross_lens.py` green (deterministic fake-encode + one real-embedder e2e).
**Files:**
- Create: `tortoise/cross_lens.py`
- Test: `tests/test_cross_lens.py`

**Step 1: Write the failing tests** (`tests/test_cross_lens.py`) — deterministic fake encoder:

```python
"""Tests for tortoise.cross_lens — embedding-based cross-lens candidate generation (#399).

Deterministic via injected encode (fixed vectors); one real-embedder e2e test
guarded by sentence_transformers availability (model cached locally).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tortoise.cross_lens import find_cross_lens_matches, NEAR_DUPLICATE_THRESHOLD

# Fixed vectors: a≈b (cross-vocab in-band), c≈p3 mid, x/p4 orthogonal noise.
# Content doubles as the point id in these tests; the map is the deterministic encoder.
_V = {
    "p1": np.array([1.0, 0.0, 0.0]), "p2": np.array([0.9, 0.1, 0.0]),
    "p3": np.array([0.0, 1.0, 0.0]), "p4": np.array([0.0, 0.0, 1.0]),
    "a": np.array([1.0, 0.0, 0.0]), "b": np.array([0.9, 0.1, 0.0]),
    "c": np.array([0.0, 1.0, 0.0]), "x": np.array([0.0, 0.0, 1.0]),
}
def _fake_encode(texts: list[str]) -> np.ndarray:
    return np.stack([_V[t] for t in texts])

def _pts(**kw):
    d = {"p1": {"content": "p1", "lens": "l1"}, "p2": {"content": "p2", "lens": "l2"},
         "p3": {"content": "p3", "lens": "l2"}, "p4": {"content": "p4", "lens": "l1"}}
    d.update(kw); return d

def test_same_lens_excluded():
    pts = _pts(p2={"content": "p2", "lens": "l1"})          # p1,p2 same lens, similar vecs
    cands = find_cross_lens_matches(pts, encode=_fake_encode)
    assert all({c["src"], c["dst"]} != {"p1", "p2"} for c in cands)

def test_cross_lens_pairing_and_shape():
    cands = find_cross_lens_matches(_pts(), encode=_fake_encode)
    by = {(c["src"], c["dst"]): c for c in cands}
    assert ("p1", "p2") in by and by[("p1", "p2")]["similarity"] >= 0.9
    c = by[("p1", "p2")]
    assert set(c) == {"src", "dst", "similarity", "lenses", "speakers", "degraded"}
    assert c["lenses"] == ["l1", "l2"] and c["degraded"] is False

def test_threshold_monotonicity():
    low = find_cross_lens_matches(_pts(), threshold=0.1, encode=_fake_encode)
    high = find_cross_lens_matches(_pts(), threshold=0.95, encode=_fake_encode)
    assert len(low) >= len(high)

def test_lens_derivation_chain():
    # no lens → falls to source → provenance.source_id → speaker → unknown
    pts = {"a": {"content": "a", "source": "s1"}, "b": {"content": "b", "provenance": {"source_id": "s2"}}}
    cands = find_cross_lens_matches(pts, encode=_fake_encode)
    assert cands and {tuple(c["lenses"]) for c in cands} == {("s1", "s2")}
    pts2 = {"a": {"content": "a", "speaker": "alice"}, "b": {"content": "b", "speaker": "bob"}}
    assert find_cross_lens_matches(pts2, encode=_fake_encode)          # speaker fallback
    pts3 = {"a": {"content": "a"}, "b": {"content": "b"}}              # both unknown → same lens
    assert find_cross_lens_matches(pts3, encode=_fake_encode) == []

def test_lens_key_explicit():
    pts = {"a": {"content": "a", "sector": "x"}, "b": {"content": "b", "sector": "x"}}
    assert find_cross_lens_matches(pts, lens_key="sector", encode=_fake_encode) == []
    pts2 = {"a": {"content": "a", "sector": "x"}, "b": {"content": "b", "sector": "y"}}
    assert find_cross_lens_matches(pts2, lens_key="sector", encode=_fake_encode)

def test_sorted_by_similarity_desc():
    pts = {"a": {"content": "a", "lens": "l1"}, "b": {"content": "b", "lens": "l2"},
           "c": {"content": "c", "lens": "l1"}}  # _V: a-b 0.90+, a-c 0.0, b-c 0.09
    sims = [c["similarity"] for c in find_cross_lens_matches(pts, encode=_fake_encode)]
    assert sims == sorted(sims, reverse=True)

def test_degraded_flag_on_tfidf_fallback():
    pytest.importorskip("sklearn")
    from unittest.mock import patch
    from tortoise.embeddings import EmbeddingModel
    pts = {"a": {"content": "deployments must be automated", "lens": "l1"},
           "b": {"content": "automating deployments is required", "lens": "l2"}}
    with patch.object(EmbeddingModel, "get", return_value=None):
        cands = find_cross_lens_matches(pts)
    assert cands and all(c["degraded"] for c in cands)

def test_encode_param_used():
    from unittest.mock import MagicMock
    enc = MagicMock(return_value=_fake_encode(["p1", "p2"]))
    find_cross_lens_matches(_pts(), encode=enc)
    enc.assert_called_once()
```

Real-embedder e2e (guarded; measured values with margins):
```python
def test_real_embedder_cross_vocab_in_band_and_noise():
    pytest.importorskip("sentence_transformers")
    pts = {
        "p1": {"content": "Growth depends on distribution channels and partnerships", "lens": "contemporary"},
        "p2": {"content": "Winning requires strong go to market and channel partners", "lens": "practitioner"},
        "p3": {"content": "quantum physics research papers", "lens": "lens-a"},
        "p4": {"content": "chocolate chip cookie recipes", "lens": "lens-b"},
    }
    cands = find_cross_lens_matches(pts)  # default threshold 0.40
    pair12 = next(c for c in cands if {c["src"], c["dst"]} == {"p1", "p2"})
    assert pair12["similarity"] >= 0.35          # measured 0.448
    assert pair12["degraded"] is False
    assert not any({c["src"], c["dst"]} == {"p3", "p4"} for c in cands)  # 0.121 noise

def test_real_embedder_motivating_pair_below_default():
    pytest.importorskip("sentence_transformers")
    pts = {"a": {"content": "Cost inversion from fixed to variable", "lens": "contemporary"},
           "b": {"content": "MVP now costs ~$100", "lens": "practitioner"}}
    assert find_cross_lens_matches(pts) == []    # 0.291 < 0.40 — recall-only boundary
```

**Step 2:** Run `pytest tests/test_cross_lens.py -q` → FAIL (module doesn't exist).

**Step 3: Implement `tortoise/cross_lens.py`**

```python
"""Embedding-based cross-lens candidate generation for Tortoise (#399).

Recall-only: finds point pairs from DIFFERENT lenses that may describe the
same concept. This module NEVER writes to the graph and never decides
operator semantics — it produces candidates for a verifier (the extractor's
cue-word gate today; the LLM relation verifier in #6306).

Lens derivation (when lens_key is None): point["lens"] → point["source"] →
point["provenance"]["source_id"] → point["speaker"] → "unknown".

Threshold calibration (all-MiniLM-L6-v2, measured 2026-08-07):
  near-duplicate paraphrases ....... 0.90+   (NEAR_DUPLICATE_THRESHOLD = 0.75)
  cross-vocabulary paraphrase band . 0.35–0.51  ← DEFAULT_THRESHOLD = 0.40
  motivating pair (#399) ............ 0.29 (boundary: topically similar, NOT
                                         logically implied — verification's job)
  unrelated / noise floor ........... ≤ 0.15

#6306 contract: find_cross_lens_matches(points) over folded document points
({pid: {"content", "lens"}} or provenance.source_id); candidates are INPUT to
the LLM relation verifier — never operators by themselves.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

NEAR_DUPLICATE_THRESHOLD = 0.75
DEFAULT_THRESHOLD = 0.40


def _lens_of(point: dict, lens_key: str | None) -> str:
    if lens_key is not None:
        v = point.get(lens_key)
        return str(v) if v is not None else "unknown"
    for key in ("lens", "source"):
        v = point.get(key)
        if v is not None:
            return str(v)
    prov = point.get("provenance")
    if isinstance(prov, dict) and prov.get("source_id") is not None:
        return str(prov["source_id"])
    sp = point.get("speaker")
    return str(sp) if sp is not None else "unknown"


def find_cross_lens_matches(points, *, threshold: float = DEFAULT_THRESHOLD,
                            lens_key: str | None = None,
                            encode: Callable[[list[str]], np.ndarray] | None = None,
                            ) -> list[dict]:
    """Recall-only cross-lens candidate generation (#399).

    Args:
        points: point_id → {"content": str, ...}; content is the only
            required key. Lens identity resolved via _lens_of.
        threshold: cosine similarity cutoff (default 0.40, calibrated).
        lens_key: explicit field to use as the lens; None → derivation chain.
        encode: injected encoder (tests); None → shared tortoise.embeddings
            _encode (real model; deterministic TF-IDF degraded fallback).

    Returns:
        Candidates sorted by similarity descending:
        [{"src", "dst", "similarity", "lenses": [l1,l2], "speakers": [...],
          "degraded": bool}] — same-lens pairs excluded. Never writes to the
        graph and never returns operator semantics.
    """
    ids = list(points)
    texts = [points[i]["content"] for i in ids]
    lenses = [_lens_of(points[i], lens_key) for i in ids]
    speakers = [points[i].get("speaker", "unknown") for i in ids]

    degraded = False
    if encode is not None:
        vectors = np.asarray(encode(texts), dtype=np.float64)
    else:
        # Lazy import — see plan D9: keeps test_mock_extractor_multi_source_fallback
        # (patched builtins.__import__ raising on "embeddings") able to trip the
        # extractor's fallback even when tortoise.cross_lens is already cached.
        from tortoise.embeddings import _encode  # noqa: PLC0415
        vectors, degraded = _encode(texts)

    from tortoise.embeddings import cosine_similarity_matrix  # noqa: PLC0415
    sim = cosine_similarity_matrix(vectors)
    if degraded:
        logger.info("cross-lens matching degraded to TF-IDF (%d points)", len(ids))

    candidates = []
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            if lenses[i] == lenses[j]:
                continue
            if sim[i, j] >= threshold:
                candidates.append({
                    "src": ids[i], "dst": ids[j],
                    "similarity": float(sim[i, j]),
                    "lenses": [lenses[i], lenses[j]],
                    "speakers": [speakers[i], speakers[j]],
                    "degraded": degraded,
                })
    candidates.sort(key=lambda c: c["similarity"], reverse=True)
    return candidates
```

> Note: `cosine_similarity_matrix` handles the empty-vectors case (`norms[norms==0]=1`) so single-point and empty inputs return `[]` without special-casing.

**Step 4:** Run `pytest tests/test_cross_lens.py -q` → all green (e2e tests exercise the real model; cached locally, ~3s load).

**Step 5:** Structural boundary check — `grep -n "api\|EventAPI\|add_point\|add_operator\|cypher\|graph" tortoise/cross_lens.py` → only `Callable`/numpy/logging imports. Add to Task 6 review gate.

**Step 6: Commit** — `feat(399): cross-lens candidate generation module (recall-only, lens-keyed)`.

### Task 3: MockExtractor `multi_source` wiring + cue-gate direction

**Intent:** Make the extractor's existing multi-source branch actually find cross-vocabulary connections (root-cause fixes #1/#2/#4) without activating production mining.
**Acceptance:** cross-vocabulary zero-shared-words matched pairs create `IMPL` (support cue) / `NAND` (refute cue); matched pairs without cue words create **no** operator but appear in `extractor._last_candidates`; degraded/import-failure falls back to the existing all-pairs cue-gate; `test_mock_extractor_multi_source_fallback` and `test_mock_extractor_multi_source_embedding` stay green.
**Files:**
- Modify: `tortoise/extractor.py:179-262` (MockExtractor.run multi_source branch)
- Modify: `tests/test_extractor.py:413-446` (rewrite `test_mock_extractor_multi_source_semantic_agreement`)
- Test: `tests/test_extractor.py` (new cue-direction tests)

**Step 1: Write the failing tests** — rewrite the semantic-agreement test and add direction tests:

```python
def test_mock_extractor_multi_source_semantic_agreement():
    """Cross-vocabulary pair (zero shared words) + SUPPORT cue → IMPL; recorded."""
    import tortoise.cross_lens as cl
    api, log = _api()
    text = (
        "Alice: Winning requires strong go to market and channel partners because growth depends on distribution.\n"
        "Bob: Growth depends on distribution channels and partnerships."
    )
    _orig = cl.find_cross_lens_matches
    def _fake(points, *, threshold=0.40, lens_key=None, encode=None):
        ids = list(points)
        return [{"src": ids[0], "dst": ids[1], "similarity": 0.45,
                 "lenses": ["test.txt", "test.txt"],
                 "speakers": ["alice", "bob"], "degraded": False}] if len(ids) >= 2 else []
    cl.find_cross_lens_matches = _fake
    try:
        ex = MockExtractor()
        ex.run(text, "test.txt", api, multi_source=True)
    finally:
        cl.find_cross_lens_matches = _orig
    events = log.read_all()
    ops = [e for e in events if e["type"] == "OperatorAdded"]
    assert len(ops) == 1 and ops[0]["opType"] == "IMPL"          # cue direction, zero shared words
    assert ex._last_candidates and ex._last_candidates[0]["similarity"] == 0.45

def test_mock_extractor_multi_source_refute_cue_nand():
    """Cross-vocabulary pair + REFUTE cue → NAND (no support cue present)."""
    import tortoise.cross_lens as cl
    api, log = _api()
    text = ("Alice: Growth depends on distribution channels and partnerships.\n"
            "Bob: Winning requires strong go to market and channel partners but returns diminish.")
    _orig = cl.find_cross_lens_matches
    def _fake(points, *, threshold=0.40, lens_key=None, encode=None):
        ids = list(points)
        return [{"src": ids[0], "dst": ids[1], "similarity": 0.45,
                 "lenses": ["s", "s"], "speakers": ["alice", "bob"], "degraded": False}] if len(ids) >= 2 else []
    cl.find_cross_lens_matches = _fake
    try:
        ex = MockExtractor(); ex.run(text, "test.txt", api, multi_source=True)
    finally:
        cl.find_cross_lens_matches = _orig
    ops = [e for e in log.read_all() if e["type"] == "OperatorAdded"]
    assert len(ops) == 1 and ops[0]["opType"] == "NAND"

def test_mock_extractor_multi_source_no_cue_no_operator_but_recorded():
    """Matched cross-vocab pair WITHOUT cue words → no operator, recorded in _last_candidates."""
    import tortoise.cross_lens as cl
    api, log = _api()
    text = ("Alice: Growth depends on distribution channels and partnerships.\n"
            "Bob: Winning requires strong go to market and channel partners.")
    _orig = cl.find_cross_lens_matches
    def _fake(points, *, threshold=0.40, lens_key=None, encode=None):
        ids = list(points)
        return [{"src": ids[0], "dst": ids[1], "similarity": 0.45,
                 "lenses": ["s", "s"], "speakers": ["alice", "bob"], "degraded": False}] if len(ids) >= 2 else []
    cl.find_cross_lens_matches = _fake
    try:
        ex = MockExtractor(); ex.run(text, "test.txt", api, multi_source=True)
    finally:
        cl.find_cross_lens_matches = _orig
    ops = [e for e in log.read_all() if e["type"] == "OperatorAdded"]
    assert ops == [] and len(ex._last_candidates) == 1     # similarity alone never creates operators
```

**Step 2:** Run the three tests → FAIL against current code (extractor still calls `find_cross_source_matches`, shared-words gate still present, `_last_candidates` missing, mocked symbol not consulted).

**Step 3: Implement** — replace the multi-source block (`extractor.py:180-263`):

```python
if multi_source:
    # Multi-source mode: claim extraction → embedding pre-filter → cue-word gate typing
    self._last_candidates = []
    pids = []
    for speaker, text, span in _utterances(transcript):
        if not _is_claim(text):
            continue
        prov = provenance(source_id, span, quote=text, speaker=speaker,
                          extracted_by=self.version)
        pid = api.add_point(content=text, provenance=prov, extractedFrom=source_id)
        pids.append((pid, text.lower(), prov))
    try:
        from tortoise.cross_lens import find_cross_lens_matches
        all_points = {}
        for pid_i, ti, pvi in pids:
            sp = pvi.get('speaker', 'unknown') if isinstance(pvi, dict) else 'unknown'
            # #399: keep the lens identity — source_id was dropped before (root cause #2)
            all_points[pid_i] = {"content": ti, "speaker": sp, "source": source_id}
        candidates = find_cross_lens_matches(all_points, lens_key="source")
        self._last_candidates = list(candidates)
        degraded = any(c.get("degraded") for c in candidates)
        if candidates and not degraded:
            # Matched-pairs cue-gate: candidates never become operators from
            # similarity alone; cue words decide IMPL vs NAND direction.
            by_pid = {pid: (ti, pvi) for pid, ti, pvi in pids}
            for m in candidates:
                ti = by_pid[m["src"]][0]
                tj = by_pid[m["dst"]][0]
                ti_clean = _PUNC.sub('', f" {ti} ")
                tj_clean = _PUNC.sub('', f" {tj} ")
                gate = None
                if _has_cue(ti_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES) or \
                   _has_cue(tj_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
                    gate = "IMPL"
                elif _has_cue(ti_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES) or \
                     _has_cue(tj_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
                    gate = "NAND"
                if gate:
                    api.add_operator(gate, inputs=[m["src"], m["dst"]],
                                     provenance=by_pid[m["src"]][1])
        else:
            # Degraded (TF-IDF) or empty candidates: pre-#399 all-pairs cue-gate
            for i in range(len(pids)):
                for j in range(i + 1, len(pids)):
                    pi, ti, pvi = pids[i]
                    pj, tj, pvj = pids[j]
                    ti_clean = _PUNC.sub('', f" {ti} ")
                    tj_clean = _PUNC.sub('', f" {tj} ")
                    gate = None
                    if _has_cue(ti_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES) or \
                       _has_cue(tj_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
                        gate = "IMPL"
                    elif _has_cue(ti_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES) or \
                         _has_cue(tj_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
                        gate = "NAND"
                    if gate:
                        api.add_operator(gate, inputs=[pi, pj], provenance=pvi)
    except Exception:
        # Fallback: cue-word only all-pairs (noisy but works)
        # (catches ImportError for missing dependencies AND runtime errors
        #  like sklearn ValueError on empty vocabulary)
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                pi, ti, pvi = pids[i]
                pj, tj, pvj = pids[j]
                gate = None
                ti_clean = _PUNC.sub('', f" {ti} ")
                tj_clean = _PUNC.sub('', f" {tj} ")
                if _has_cue(ti_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES) or \
                   _has_cue(tj_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
                    gate = "IMPL"
                elif _has_cue(ti_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES) or \
                     _has_cue(tj_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
                    gate = "NAND"
                if gate:
                    api.add_operator(gate, inputs=[pi, pj], provenance=pvi)
```

**Step 4:** Run the extractor suite:
```bash
.venv/bin/python -m pytest tests/test_extractor.py -q
```
Expected: all green — the three new tests + rewritten semantic-agreement + `test_mock_extractor_multi_source_fallback` + `test_mock_extractor_multi_source_embedding` (real-model near-dup pair with "because" cue → IMPL).

**Step 5: Commit** — `feat(399): wire cross-lens candidates into MockExtractor multi_source (cue-gate direction, _last_candidates)`.

### Task 4: Full regression + acceptance verification

**Intent:** Prove the falsifiable acceptance criteria end-to-end.
**Acceptance:** entire affected battery green; no graph writes from cross_lens; mining.py untouched.
**Files:** none (verification only)

**Step 1:** Run the full affected battery:
```bash
.venv/bin/python -m pytest tests/test_cross_lens.py tests/test_embeddings.py \
  tests/test_embeddings_filters.py tests/test_extractor.py tests/test_tortoise_search.py \
  tests/test_search_engine_gaps.py tests/test_session_semantic_search.py -q
```
Expected: green; note runtime delta vs the pre-change baseline (~7.5 min).

**Step 2:** Run `git diff --stat tortoise/mining.py` → empty (out of scope). Run the structural boundary grep from Task 2 Step 5.

**Step 3:** Hand off to `plan-review` → then `executing-plans`.

## Testing Strategy

1. **Deterministic unit tests (bulk):** `tests/test_cross_lens.py` uses an injected fixed-vector `encode` — same-lens exclusion, cross-lens pairing, threshold monotonicity, lens derivation chain (all 5 rungs), explicit `lens_key`, sort order, candidate shape, `encode` param honored. Zero model, zero network, zero randomness.
2. **Degraded-mode determinism:** TF-IDF fallback is deterministic for fixed inputs; the degraded test patches `EmbeddingModel.get → None` (`importorskip("sklearn")`), asserting `degraded=True` on every candidate. No seeding needed — real MiniLM has no dropout; TF-IDF is exact.
3. **Real-embedder e2e (one, guarded):** `pytest.importorskip("sentence_transformers")` + locally cached model (`~/.cache/huggingface/hub/...`); asserts against **measured** values with margins — in-band 0.448 pair ≥ 0.35 yields a candidate; noise 0.121 pair yields none; motivating 0.291 pair below default yields none (recall-only boundary). CI installs `.[embeddings]` so it runs there; offline dev envs skip.
4. **Backward-compat regression battery:** 12 `test_embeddings.py` (11 byte-identical; test #12 +1 line `EmbeddingModel._reset()`), 23 `test_embeddings_filters.py`, 16 `test_tortoise_search.py`, 128+4 search/session tests, 43 `test_extractor.py` (with the one rewritten test + 3 new cue-direction tests). These prove the wrapper semantics and the extractor fallback.
5. **Red-Green-Refactor discipline:** every task starts with the failing test (Task 1's singleton-reuse test fails against today's fresh-per-call code; Task 2's module tests fail on ImportError; Task 3's cue tests fail against the old shared-words gate).

## Acceptance Criteria (falsifiable)

1. **In-band cross-vocab → candidate:** `find_cross_lens_matches` (default 0.40) returns a candidate for "Growth depends on distribution channels and partnerships" ↔ "Winning requires strong go to market and channel partners" with `similarity ≥ 0.35` and `degraded is False` (measured 0.448).
2. **Noise floor → none:** no candidate for "quantum physics research papers" ↔ "chocolate chip cookie recipes" (measured 0.121 ≤ 0.2).
3. **Boundary respected:** no candidate for the motivating pair "Cost inversion from fixed to variable" ↔ "MVP now costs ~$100" (measured 0.291 < 0.40) — similarity alone never becomes a connection.
4. **Same-lens excluded:** identical/similar points with the same resolved lens never produce a candidate.
5. **Extractor directionality:** cross-vocabulary zero-shared-words matched pair + support cue → exactly one `IMPL` operator; + refute cue (no support cue) → exactly one `NAND`; no cue → zero operators, candidate present in `extractor._last_candidates`.
6. **Legacy unchanged:** all 12 `tests/test_embeddings.py` green (11 byte-identical; test #12 +1 documented line); 23 `test_embeddings_filters.py`; 16 `test_tortoise_search.py`; 128 passed/4 skipped search+session; 43 `test_extractor.py` (one test rewritten per D9b/controller scope, 3 new).
7. **Structural boundaries:** `tortoise/cross_lens.py` imports no EventAPI/graph modules; `tortoise/mining.py` has zero diff; no graph writes from the new code path.
8. **Runtime fix observable:** `test_tortoise_search.py` drops from ~116s (16 fresh model loads) toward <30s (one singleton load).

## Runtime Prerequisites

- `sentence-transformers` + `scikit-learn` via the **existing** `[embeddings]` pyproject extra — **no dependency changes**. CI already installs it (`post-merge-validation.yml:40`); local `.venv` has 5.7.0/1.9.0.
- Model `all-MiniLM-L6-v2` cached locally (`~/.cache/huggingface/hub/`) and pre-downloaded/pre-warmed in the hosted image (`Dockerfile.hosted`, `entrypoint.sh`) — no runtime download path introduced.
- Embeddings remain **optional**: `_encode` degrades to TF-IDF (sklearn) and the extractor falls back to the all-pairs cue-gate; point creation/search never depend on model availability.
- Test runs must not require network: real-embedder tests use the cached model; all other tests are deterministic (fake vectors or TF-IDF).

## #6306 Integration Point (documented contract — NOT implemented here)

**Producer:** `tortoise/cross_lens.find_cross_lens_matches(points, threshold=0.40)` — recall-only; `points` = folded document points `{pid: {"content", "lens"}}` (or any rung of the derivation chain); returns `[{src, dst, similarity, lenses, speakers, degraded}]`. Already exercised in production shape by `MockExtractor._last_candidates`.

**Consumer (#6306):** the LLM relation verifier (M2 `_RelationStage` / `_RELATIONS_SYS`'s successor) ingests `extractor._last_candidates` (or re-runs `find_cross_lens_matches` over a multi-document fold) and emits IMPL/NAND **only for candidates it verifies** — never similarity-only. This is the fix for root cause #5 (explicit-assertion-only prompt): the prompt gains the verified-candidates context.

**Activation (#6306):**
1. `mining.py:75` → `extractor.run(transcript, source_id, api, multi_source=True)` — flips on the branch this plan wires.
2. Document-mode cross-source machinery: fold document points (currently `speaker="document"`, `provenance.source_id` — extractor.py:737) into `all_points` with the lens dimension and call `find_cross_lens_matches` (derivation chain already resolves `provenance.source_id`).
3. Multi-document gather: a fold step aggregates points across documents into the `points` dict (today the branch only sees one transcript's utterances).

## Out of Scope / Rejected Alternatives

- **Alternative C — DI verifier protocol: REJECTED** (D8). Over-engineered for a single in-repo consumer; `_last_candidates` + cue-gate covers today; LLM verifier slots in via the documented contract.
- **`NearDuplicateVerifier` / similarity→IMPL: REJECTED** (D5). Reintroduces unverified epistemic structure; the 0.291 motivating pair is topically similar but not implied — EP belief weights would be corrupted.
- **Threshold-only fix (0.75→0.40 on `find_cross_source_matches`): REJECTED.** Speaker keying remains — document points all share `speaker="document"` (root cause #1) so cross-document pairs stay excluded; per-call model reload also persists.
- **Direct similarity→IMPL in the extractor: REJECTED** — same fabrication problem; the cue-gate is the deterministic verifier.
- **#6306 remainder: `mining.py` `multi_source` activation, document-mode LLM cross-source machinery, multi-document gather** — documented contract only.
- **Model swap / different embedder:** out of scope; thresholds are calibration-specific and documented as such.
- **Graph writes of any kind from the new code:** structural boundary (no EventAPI imports in `cross_lens.py`).

## Files to Change

| File | Change |
|---|---|
| `tortoise/embeddings.py` | Add `_encode`, `cosine_similarity_matrix`; refactor `find_cross_source_matches` → thin wrapper; `search_points` → shared `_encode`; calibration docstring |
| `tortoise/cross_lens.py` | **NEW** — `find_cross_lens_matches`, `_lens_of`, `NEAR_DUPLICATE_THRESHOLD`, `DEFAULT_THRESHOLD`, #6306 contract docstring |
| `tortoise/extractor.py` | MockExtractor multi-source branch: keep `source_id` in `all_points`, call `find_cross_lens_matches(lens_key="source")`, remove shared-words gate, cue-gate direction, `_last_candidates`, degraded/fallback |
| `tests/test_cross_lens.py` | **NEW** — deterministic fake-encode unit tests + 2 guarded real-embedder e2e tests |
| `tests/test_embeddings.py` | Test #12 only: insert `EmbeddingModel._reset()` (1 line, D9b) |
| `tests/test_extractor.py` | Rewrite `test_mock_extractor_multi_source_semantic_agreement`; add refute-cue NAND + no-cue-recorded tests |
| `docs/plans/2026-08-08-399-embedding-matching.md` | This plan |

**Explicitly NOT modified:** `tortoise/mining.py`, `tests/test_embeddings_filters.py`, `tests/test_tortoise_search.py`, `tests/test_search_engine_gaps.py`, `tests/test_session_semantic_search.py` (regression-only), `tests/test_embeddings.py` tests #1–11.
