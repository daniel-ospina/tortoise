<!-- research-path: docs/plans/2026-08-08-ep-source-validation-research.md -->

# #341 Implementation Plan: Mathematical EP validation (source priors monotonic + directionally correct)

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Prove mathematically (and lock in via tests) that source credibility priors are monotonic and directionally correct across topologies, replacing the Docker-only/fictional-model suite with an embedded real-path validation suite.

**Team:** epistemic-team
**Role:** worker agent (autonomous)

**Architecture:** Two-tier theorem. T1 = exact prior-level monotonicity of `aggregate_prior` (source_credibility.py:169) under uniform weight + decay=1.0 — provable in closed form (log2(N+1) strictly increasing, all terms nonnegative, per-source marginal decreases by log2 concavity). T2a = real-path prior-ordering tests read via `_apply_source_inheritance(recency_decay=1.0, recompute_interval=0)` + `get_point()` (never `compute_confidence()` — `_flush_cache` overwrites graph `ep_alpha` with posteriors). T2b = EP directional audit via `compute_confidence()["confidences"][id]["mean"]`, loose margins, seeded. NAND/mitigation situations 8-10 are a **documented audit** (bug issue to EP owner), never silently encoded as expected. No production-code changes expected (log-scale aggregation already live from #398); single edit point if a mechanism change emerges: `aggregate_prior`.

### Pattern Research

**Library docs (preflight)** — no third-party deps in plan (Python stdlib + in-repo tortoise + pytest/falkordblite already installed). Skipped.

**Library version & API surface** — skipped (zero third-party deps).

**Idiomatic usage patterns** — skipped (plan follows in-repo pattern with 2+ examples: `test_source_inheritance_own.py`, `test_ep_nary_falsification.py`).

**Library/framework pitfalls** — skipped (all deps used identically elsewhere in repo; no documented post-mortems). Epistemic memory checkpoint: research doc documents all prior claims.

### Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | FalkorDB graph (Source/Point/extractedFrom/IMPL/NAND) | DB | Write (create) + Read (get_point) | Integration (embedded falkordblite) | Source `{url}` MERGE key; `credibilityTier` T0-T4; Point `{id, ep_alpha, ep_beta, baseline_source}`; extractedFrom edge | MERGE-on-url collapse (distinct URLs required); list-url trap (extractedFrom takes single string); tier not set → neutral |
| 2 | `_apply_source_inheritance(recency_decay, recompute_interval)` | State | Write (baselines) | Integration | recompute_interval=0 → always recompute; revert path for deleted edges | 3600s gate blocks re-run; in-memory `_evidence` staleness after revert |
| 3 | `compute_confidence()` → EP | State | Read (confidences) | Integration | `{"confidences": {id: {"mean",...}}}`; `_flush_cache` overwrites graph ep_alpha | random.shuffle nondeterminism (seed); posterior ≠ prior |
| 4 | `test_ep_nary_falsification.py` `_RecordingEP` | State | Read | Unit (hermetic) | `_clear_caches` deletes `_node_cache` post-run (#330) | post-run cache read → AttributeError (pre-existing fail) |

### Bug Pattern Flags
- **Silent function skips / stale state:** `compute_confidence()` does NOT expose `recompute_interval` — tests must call `_apply_source_inheritance(recompute_interval=0)` directly or use fresh SDK per state (existing convention).
- **Conditional guards:** inheritance gate (3600s) + eligibility where-clause (baseline_source IS NULL OR 'inherited') — tests must pin both paths.
- **N+1 queries:** 1000-source real-path test = 1000 `_link_source` calls — acceptable (2 queries each, embedded); 1M sources NOT feasible real-path → formula-level only.

### Verification Plan (test-routing)
- Domain: code. Complexity: complex. UX_RATING: low (no UI → ux-verification skipped).
- Layers: unit (T1 pure-math theorem tests) + integration (T2a/T2b embedded real-path). No e2e, no pgTAP (no SQL business logic), no external services.
- Verification: `python -m pytest tests/test_ep_sources.py tests/test_ep_nary_falsification.py tests/test_source_inheritance_own.py -q` (3-file scope, embedded) + full-suite spot check for no-new-failures.

### Task 1: Carry-forward regression fix — `test_run_converges_with_gentle_factor`

**Intent:** The #330 cache lifecycle change broke a falsification-suite positive control (test reads `ep._node_cache` after `run()` clears it). Fix is test-side and additive — the EP engine itself is untouched (parallel agent's domain).
**Acceptance:** `python -m pytest tests/test_ep_nary_falsification.py -q` → all pass. No production code changed.
**Files:**
- Modify: `tests/test_ep_nary_falsification.py:234-260` (`_RecordingEP`), `:333`

**Step 1: Add snapshot override to `_RecordingEP`**

In `_RecordingEP.__init__` (l.237-243), initialize the snapshots so early-exit
paths (run returns without `_clear_caches`) read a deterministic empty dict
instead of AttributeError:

```python
        self._final_node_cache: dict = {}
        self._final_msg_cache: dict = {}
```

In `_RecordingEP` (after `_flush_cache`), add:

```python
def _clear_caches(self) -> None:
    """Snapshot final posterior state before run() clears caches (#330)."""
    self._final_node_cache = dict(getattr(self, "_node_cache", {}))
    self._final_msg_cache = dict(getattr(self, "_msg_cache", {}))
    super()._clear_caches()
```

**Step 2: Update the assertion**

In `test_run_converges_with_gentle_factor` (l.333), change:

```python
    a, b = ep._node_cache["a"]
```
to:
```python
    a, b = ep._final_node_cache["a"]
```

**Step 3: Verify**

Run: `python -m pytest tests/test_ep_nary_falsification.py -q`
Expected: PASS (all tests in file, including the formerly failing one).

**Step 4: Commit**

```bash
git add tests/test_ep_nary_falsification.py
git commit -m "fix(tests): snapshot EP node cache before _clear_caches in _RecordingEP (#341)"
```

### Task 2: Rewrite `tests/test_ep_sources.py` — harness + pure-math helpers

**Intent:** Establish the embedded real-path harness and preserve the correct formula helpers while removing the Docker-only + fictional-model machinery.
**Acceptance:** File imports cleanly; `TIER_MAP`, `TIER_PC`, `log_aggregate_pc`, `beta_mean` preserved; no `set_point_baseline`, no `log_aggregate_prior_mixed`, no Docker `fresh_sdk`. This rewrite SUPERSEDES the "regression: #341 prior suite | must stay green unmodified" row in docs/plans/2026-08-07-source-credibility.md l.59 — prior-level invariants are re-asserted at T1 (test_aggregate_prior_matches_formula) + T2a (Situation 2 exact TIER_MAP alphas, Situation 3 exact cumulative), and the file moves from Docker-only to embedded, matching post-merge-validation.yml's embedded runner (no workflow pins the old Docker fresh_sdk — grep-verified). calibrate_summary (sdk.py:2021) is audit-only guidance, NOT the inheritance path — monotonicity is asserted on ep_alpha directly, so no calibrate_summary call is needed (issue body's "run calibrate_summary" is a mental-model correction, documented in the proof writeup).
**Files:**
- Rewrite: `tests/test_ep_sources.py`

**Step 1: Write new harness (replaces old fresh_sdk/set_source_evidence)**

```python
"""#341 — Mathematical validation: source priors are monotonic + directionally correct.

Embedded real-path suite (no Docker). T1: exact prior-level monotonicity theorem
for aggregate_prior. T2a: real-path prior ordering via _apply_source_inheritance +
get_point (NOT compute_confidence — its _flush_cache overwrites ep_alpha with
posteriors). T2b: EP directional audit via compute_confidence() confidences.

Deliberate overlap with test_source_inheritance_own.py (corroboration/anti-Sybil):
those assertions are re-derived here under the issue's situation numbering.
"""
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.source_credibility import TIER_PRIORS, aggregate_prior, resolve_tier

FRESH = "2024-01-01T00:00:00+00:00"
DELTA = 1e-6          # exact prior math
EPSILON = 0.02        # EP convergence tolerance (directional)
TIER_MAP = dict(TIER_PRIORS)
TIER_PC = {tier: alpha - 1.0 for tier, (alpha, beta) in TIER_MAP.items()}


def log_aggregate_pc(base_pc: float, n_sources: int) -> float:
    """pc = base_pc * log2(N+1) — the issue's log-scale law."""
    return base_pc * math.log2(n_sources + 1)


def beta_mean(alpha: float, beta: float) -> float:
    """Mean of Beta(alpha, beta). Note: the issue's alpha_eff = mean*(pc+2)
    form equals the implemented (1+pc, 1) ONLY at N=1 (see TestT1Theorem)."""
    return alpha / (alpha + beta)


@contextmanager
def fresh_sdk():
    with tempfile.TemporaryDirectory(prefix="tt_341_") as td:
        db_path = os.path.join(td, "test.db")
        sdk = TortoiseSDK(db_path)
        try:
            yield sdk
        finally:
            try:
                sdk.close()
            except Exception:
                pass


def tier_source(sdk, url: str, tier: str, source_date: str = FRESH) -> str:
    """Create a Point extracted from a tiered Source. Returns point id."""
    p = sdk.create_point("statement", f"claim from {url}", extractedFrom=url)
    sdk._get_proj().g.query(
        "MATCH (s:Source {url:$url}) SET s.credibilityTier = $t, s.sourceDate = $sd, "
        "s.ingestedAt = $sd",
        params={"url": url, "t": tier, "sd": source_date},
    )
    return p["id"]


def link_tiered_source(sdk, pid: str, url: str, tier: str) -> None:
    """Link an additional tiered source to an existing point."""
    sdk._get_proj()._link_source(pid, url)
    sdk._get_proj().g.query(
        "MATCH (s:Source {url:$url}) SET s.credibilityTier = $t, s.ingestedAt = $ts",
        params={"url": url, "t": tier, "ts": FRESH},
    )


def inherited_alpha(sdk, pid: str) -> float | None:
    return sdk.get_point(pid).get("ep_alpha")


def make_point(sdk: TortoiseSDK, content: str, kind: str = "statement") -> dict:
    return sdk.create_point(kind, content)


def make_operator(sdk: TortoiseSDK, source_id: str, target_id: str,
                  op_type: str = "IMPL", direction: str = "bidirectional") -> dict:
    return sdk.create_operator(op_type, source_id, [target_id], direction=direction)


def build_scenario_a(sdk) -> tuple[str, str]:
    """Linear chain: Point_A ->[IMPL]-> Claim_B."""
    a = make_point(sdk, "Point A: evidence aggregation point")
    b = make_point(sdk, "Claim B: conclusion")
    make_operator(sdk, a["id"], b["id"], "IMPL")
    return a["id"], b["id"]


def build_scenario_b(sdk) -> tuple[str, str, str]:
    """Loopy cluster: A->B->C->A (unidirectional IMPL cycle)."""
    a = make_point(sdk, "Point A: loopy cluster")
    b = make_point(sdk, "Point B: loopy cluster")
    c = make_point(sdk, "Point C: loopy cluster")
    make_operator(sdk, a["id"], b["id"], "IMPL", direction="unidirectional")
    make_operator(sdk, b["id"], c["id"], "IMPL", direction="unidirectional")
    make_operator(sdk, c["id"], a["id"], "IMPL", direction="unidirectional")
    return a["id"], b["id"], c["id"]


def run_ep(sdk: TortoiseSDK, seed: int = 42, anchors: list[str] | None = None) -> dict:
    """Deterministic EP run.

    - recency_decay=1.0 passed EXPLICITLY through the whole chain — otherwise
      compute_confidence internally re-applies env default 0.95 (only masked by
      the 3600s inheritance gate today).
    - anchors=[...] skips the uncontrolled self.dream(dirty_only=True) branch
      (compute_confidence with no factors/anchors dreams first) and scopes the
      BFS subgraph — deterministic and side-effect-free.
    """
    random.seed(seed)
    kwargs = {"recency_decay": 1.0}
    if anchors is not None:
        kwargs["anchors"] = anchors
        kwargs["max_hops"] = 2
    return sdk.compute_confidence(**kwargs)


def get_conf(result: dict, node_id: str) -> float:
    return result["confidences"][node_id]["mean"]
```

**Step 2: Verify**

Run: `python -m pytest tests/test_ep_sources.py -q --collect-only`
Expected: collects 0 tests (helpers only so far) — no import errors.

**Step 3: Commit**

```bash
git add tests/test_ep_sources.py
git commit -m "test(341): rewrite harness — embedded real-path + preserved formula helpers"
```

### Task 3: T1 theorem tests (pure function)

**Intent:** Prove the exact prior-level monotonicity law in closed form — the issue's "mathematical proof" at the level where it's provable.
**Acceptance:** All T1 tests pass with DELTA=1e-6 (no EP, no flake).
**Files:**
- Modify: `tests/test_ep_sources.py` (append `TestLogAggregationMath` → `TestT1Theorem`)

**Step 1: Write T1 tests**

```python
class TestT1Theorem:
    """T1: exact prior-level monotonicity of the log-scale law."""

    def test_log2_strictly_increasing(self):
        for n in range(1, 100):
            assert math.log2(n + 2) > math.log2(n + 1)

    def test_per_source_marginal_decreases(self):
        """log2 concavity: 1->2 adds more than 10->11, per tier."""
        for tier in TIER_PC:
            base = TIER_PC[tier]
            m12 = base * (math.log2(3) - math.log2(2))
            m1011 = base * (math.log2(12) - math.log2(11))
            assert m12 > m1011

    def test_issue_decade_claim_corrected(self):
        """Issue's '10->100 adds less than 1->10' is FALSE for totals;
        true only per-source. Assert the correct statement."""
        g_1_10 = math.log2(11) - math.log2(2)     # 2.459
        g_10_100 = math.log2(101) - math.log2(11)  # 3.199
        assert g_10_100 > g_1_10  # decade totals grow (documented correction)
        m_9_10 = math.log2(11) - math.log2(10)
        m_99_100 = math.log2(101) - math.log2(100)
        assert m_9_10 > m_99_100  # per-source marginal shrinks

    def test_anti_sybil_1m_t4_lt_2_t0(self):
        pc_1m_t4 = 0.1 * math.log2(1_000_001)
        pc_2_t0 = 9.0 * math.log2(3)
        assert pc_1m_t4 < pc_2_t0

    def test_10_t4_gt_1_t4(self):
        assert log_aggregate_pc(0.1, 10) > log_aggregate_pc(0.1, 1)

    def test_1000_t4_lt_1_t2(self):
        assert log_aggregate_pc(0.1, 1000) < log_aggregate_pc(2.0, 1)

    def test_monotone_in_n_all_tiers(self):
        for tier in TIER_PC:
            prev = log_aggregate_pc(TIER_PC[tier], 0)
            for n in range(1, 21):
                cur = log_aggregate_pc(TIER_PC[tier], n)
                assert cur > prev
                prev = cur

    def test_reparameterization_identity_holds_at_n1_only(self):
        """Two readings of the issue's alpha_eff = mean*(pc_eff+2) form:
        (a) constant-mean reading (mean = the tier's FIXED base mean): identical
            to implemented (1+pc, 1) only at N=1; diverges at N>1 — the
            implementation (1+pc, 1) pushes mean toward 1 (confidence rises),
            the constant-mean form holds mean fixed (variance shrinks only).
        (b) dynamic-mean reading (mean = (1+pc)/(2+pc)): identical for all N
            (research doc's symbolic identity — verified).
        This test pins reading (a), which is the issue formula's literal
        interpretation, so the divergence cannot silently change."""
        for tier in TIER_PC:
            base_alpha, _b = TIER_MAP[tier]
            mean = base_alpha / (base_alpha + 1.0)
            # N=1: exact identity
            a_eff = mean * (TIER_PC[tier] + 2)
            assert a_eff == pytest.approx(1.0 + TIER_PC[tier], rel=1e-9)
            # N>1: issue formula mean stays FIXED; implemented mean rises
            pc_10 = log_aggregate_pc(TIER_PC[tier], 10)
            a_issue = mean * (pc_10 + 2)
            a_impl = 1.0 + pc_10
            assert a_issue < a_impl  # divergence pinned (implementation stronger)
        # concrete: 10 T4 → issue 1.229 vs implemented 1.346
        m_t4 = TIER_MAP["T4"][0] / (TIER_MAP["T4"][0] + 1.0)
        pc_10_t4 = log_aggregate_pc(TIER_PC["T4"], 10)
        assert m_t4 * (pc_10_t4 + 2) == pytest.approx(1.2287, rel=1e-3)
        assert 1.0 + pc_10_t4 == pytest.approx(1.3459, rel=1e-3)

    def test_aggregate_prior_matches_formula(self):
        """Real aggregate_prior (uniform factor, decay 1.0) == formula."""
        # 2 x T0 same-tier: pc = 9 * log2(3)
        a, b = aggregate_prior(
            [("T0", FRESH, FRESH, 1.0), ("T0", FRESH, FRESH, 1.0)],
            recency_decay=1.0,
        )
        assert a == pytest.approx(1.0 + 9.0 * math.log2(3), rel=1e-9)
        assert b == pytest.approx(1.0, rel=1e-9)
```

> Note: `aggregate_prior(groups, *, recency_decay=0.95, now=None)` verified at source_credibility.py:169 — group tuple is `(tier, source_date, ingested_at, factor)`. T0 exempt from decay; factor default 1.0.

**Step 2: Verify**

Run: `python -m pytest tests/test_ep_sources.py::TestT1Theorem -q`
Expected: PASS (9 tests).

**Step 3: Commit**

```bash
git add tests/test_ep_sources.py
git commit -m "test(341): T1 theorem — exact prior-level monotonicity + anti-Sybil + formula identity"
```

### Task 4: T2a real-path prior-ordering tests (situations 1-7)

**Intent:** Prove monotonicity through the REAL graph path (Source → extractedFrom → inheritance), deterministic and EP-free.
**Acceptance:** All T2a tests pass; `ep_alpha` asserted to rel=1e-9; no `compute_confidence()` calls in T2a tests EXCEPT the documented strict-xfail `test_revert_is_idempotent_through_ep_path` (deliberate exception that exercises the EP path to expose the stale-`_evidence` bug).
**Files:**
- Modify: `tests/test_ep_sources.py` (append situation test classes)

**Step 1: Write T2a tests**

```python
class TestSituation1_NoSourceToT4:
    def test_t4_above_no_source(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s1.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha = inherited_alpha(sdk, pid)
        # Beta(1.1, 1) vs Beta(1,1) baseline — mean 0.5238 > 0.5
        assert alpha == pytest.approx(1.1, rel=1e-9)
        assert beta_mean(alpha, 1.0) > 0.5


class TestSituation2_TierProportional:
    def test_tier_ordering_exact(self):
        alphas = {}
        for tier in ["T4", "T3", "T2", "T1", "T0"]:
            with fresh_sdk() as sdk:
                pid = tier_source(sdk, f"https://{tier}.example", tier)
                sdk._apply_source_inheritance(recency_decay=1.0)
                alphas[tier] = inherited_alpha(sdk, pid)
                assert alphas[tier] == pytest.approx(TIER_MAP[tier][0], rel=1e-9)
        assert alphas["T4"] < alphas["T3"] < alphas["T2"] < alphas["T1"] < alphas["T0"]


class TestSituation3_CumulativeWeakSources:
    def test_each_addition_increases(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            prev = inherited_alpha(sdk, pid)
            for i in range(1, 10):
                link_tiered_source(sdk, pid, f"https://s{i}.example", "T4")
                sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
                cur = inherited_alpha(sdk, pid)
                assert cur > prev
                assert cur == pytest.approx(1.0 + 0.1 * math.log2(i + 2), rel=1e-9)
                prev = cur

    def test_10_t4_exact(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            for i in range(1, 10):
                link_tiered_source(sdk, pid, f"https://s{i}.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, pid) == pytest.approx(
                1.0 + 0.1 * math.log2(11), rel=1e-9)


class TestSituation4_AntiSybil:
    def test_10_t4_lt_1_t2(self):
        """Ordering, not equality — issue's '10 T4 ~ 1 T2' is a 5.8x gap."""
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            for i in range(1, 10):
                link_tiered_source(sdk, pid, f"https://s{i}.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            pc_10t4 = inherited_alpha(sdk, pid) - 1.0
        with fresh_sdk() as sdk:
            pid2 = tier_source(sdk, "https://t2.example", "T2")
            sdk._apply_source_inheritance(recency_decay=1.0)
            pc_1t2 = inherited_alpha(sdk, pid2) - 1.0
        assert pc_10t4 < pc_1t2
        assert pc_10t4 > 0.1  # and beats 1 T4


class TestSituation5_CeilingEffect:
    def test_2_gold_plus_t4_increases_slightly(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://g0.example", "T0")
            link_tiered_source(sdk, pid, "https://g1.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_2gold = inherited_alpha(sdk, pid)
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://g0.example", "T0")
            link_tiered_source(sdk, pid, "https://g1.example", "T0")
            link_tiered_source(sdk, pid, "https://t4.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_2gold_t4 = inherited_alpha(sdk, pid)
        gain = alpha_2gold_t4 - alpha_2gold
        assert gain > 0
        assert gain < 9.0 * (math.log2(3) - math.log2(2))  # smaller than adding a T0


class TestSituation6_GoldPlusT4NoPullDown:
    def test_5_gold_plus_t4_not_below_5_gold(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://g0.example", "T0")
            for i in range(1, 5):
                link_tiered_source(sdk, pid, f"https://g{i}.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_5gold = inherited_alpha(sdk, pid)
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://g0.example", "T0")
            for i in range(1, 5):
                link_tiered_source(sdk, pid, f"https://g{i}.example", "T0")
            link_tiered_source(sdk, pid, "https://t4.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_5gold_t4 = inherited_alpha(sdk, pid)
        assert alpha_5gold_t4 >= alpha_5gold  # regression guard: never pulls down
        assert alpha_5gold_t4 > alpha_5gold   # strictly up (pc_t4 > 0)


class TestT2aEdgeCases:
    def test_untiered_source_deletes_inherited_baseline(self):
        """Surprising-but-real semantic: mutating a sourced point's source to
        untiered drops it from eligibility -> revert path REMOVES the baseline."""
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, pid) == pytest.approx(1.1, rel=1e-9)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) REMOVE s.credibilityTier",
                params={"url": "https://s0.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            assert inherited_alpha(sdk, pid) is None

    def test_no_source_no_baseline(self):
        """Point with no extractedFrom never gets an inherited baseline."""
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "orphan point")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, p["id"]) is None


class TestSituation7_AddRemoveIdempotent:
    def test_remove_returns_to_baseline(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, pid) == pytest.approx(1.1, rel=1e-9)
            # remove the source edge (raw Cypher) + force recompute
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:$url}) DELETE r",
                params={"pid": pid, "url": "https://s0.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            # no sources -> neutral (no ep_alpha on graph)
            assert inherited_alpha(sdk, pid) is None

    def test_remove_one_of_two_returns_to_single(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            link_tiered_source(sdk, pid, "https://s1.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, pid) == pytest.approx(
                1.0 + 0.1 * math.log2(3), rel=1e-9)
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:$url}) DELETE r",
                params={"pid": pid, "url": "https://s1.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            assert inherited_alpha(sdk, pid) == pytest.approx(1.1, rel=1e-9)

    @pytest.mark.xfail(
        strict=True,
        reason="KNOWN SDK FINDING (#341 audit): _apply_source_inheritance revert "
        "REMOVEs graph markers but leaves the stale (alpha, beta) in "
        "sdk._evidence (set_point_baseline writes it; _hydrate_evidence is "
        "additive-only). compute_confidence() re-applies the deleted prior "
        "through ep.run(evidence=self._evidence). Fix belongs in sdk.py "
        "(clear _evidence on revert) — filed as bug issue, not fixed here.",
    )
    def test_revert_is_idempotent_through_ep_path(self):
        """Full-path idempotency: after revert, compute_confidence returns to
        neutral. Requires an operator so EP actually runs (no operators ->
        empty confidences -> this test would be vacuous)."""
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, a_id) == pytest.approx(1.1, rel=1e-9)
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:$url}) DELETE r",
                params={"pid": a_id, "url": "https://s0.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            assert inherited_alpha(sdk, a_id) is None  # graph read is clean
            # EP path must also see neutral — currently resurrects stale prior:
            res = sdk.compute_confidence(recency_decay=1.0, anchors=[a_id], max_hops=2)
            conf = get_conf(res, a_id)
            assert abs(conf - 0.5) < EPSILON
```

**Step 2: Verify**

Run: `python -m pytest tests/test_ep_sources.py -q -k "Situation" -p no:randomly`
Expected: PASS. (Revert path verified: sdk.py:1953 REMOVEs ep_alpha/ep_beta/baseline markers -> `alpha is None` is the unconditional outcome; recompute_interval=0 bypasses the gate.)

**Step 3: Commit**

```bash
git add tests/test_ep_sources.py
git commit -m "test(341): T2a real-path prior ordering — situations 1-7 monotonic + idempotent"
```

### Task 5: T2b EP directional tests (topologies A/B/C + S10) + edge invariants

**Intent:** Prove directional correctness through the real EP path (loopy belief propagation, bidirectional messages) — the issue's scenarios A/B/C + situation 10.
**Acceptance:** Directional assertions only (loose margins); `random.seed()` pinned; all pass deterministically.
**Files:**
- Modify: `tests/test_ep_sources.py` (append topology test classes)

**Step 1: Write T2b tests**

```python
@pytest.fixture(autouse=True)
def _seed_random():
    random.seed(42)


@pytest.fixture(autouse=True)
def _pin_ep_env(monkeypatch):
    """Pin decay + recompute env so tests pass BECAUSE of the pin, not the
    3600s gate. Prevents silent env-dependent drift (CI speed-ups etc.)."""
    monkeypatch.setenv("TORTOISE_EP_RECENCY_DECAY", "1.0")
    monkeypatch.setenv("TORTOISE_EP_REINHERIT_INTERVAL", "0")


class TestScenarioA_LinearChain:
    def test_b_rises_through_impl(self):
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            # no sources: both near baseline
            res0 = run_ep(sdk, anchors=[a_id])
            b0 = get_conf(res0, b_id)
            # attach T0 source DIRECTLY to A (extractedFrom on A — required for
            # EP: only operator factors are auto-extracted, orphan points inert)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res1 = run_ep(sdk, anchors=[a_id])
            b1 = get_conf(res1, b_id)
        assert b1 > b0  # B responds through IMPL
        assert b1 > 0.5 + EPSILON  # above no-source baseline

    def test_more_sources_rises_b(self):
        """1 vs 3 T0 sources on A (wide prior gap: mean 0.909 vs 0.95) so B's
        posterior response is comfortably above noise."""
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            b1 = get_conf(run_ep(sdk, anchors=[a_id]), b_id)
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            for i in range(3):
                link_tiered_source(sdk, a_id, f"https://s{i}.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            b3 = get_conf(run_ep(sdk, anchors=[a_id]), b_id)
        assert b3 > b1  # more evidence on A -> B rises
        # pre-declared relaxation (if flaky): b3 >= b1 - EPSILON, documented


class TestScenarioB_LoopySingleEntry:
    def test_cluster_rises_from_single_entry(self):
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            res0 = run_ep(sdk, anchors=[a_id])
            assert res0["converged"] is True  # precondition: loopy BP converged
            base = {k: get_conf(res0, k) for k in (a_id, b_id, c_id)}
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res1 = run_ep(sdk, anchors=[a_id])
            assert res1["converged"] is True
            after = {k: get_conf(res1, k) for k in (a_id, b_id, c_id)}
        for k in (a_id, b_id, c_id):
            assert after[k] >= base[k] - EPSILON  # whole cluster not-lower (loose)
        # strict rise asserted on the entry node (max) — pre-declared relaxation:
        assert max(after[k] - base[k] for k in after) > 0


class TestScenarioC_LoopyMultiEntry:
    def test_multi_entry_rises_more_than_single(self):
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res_s = run_ep(sdk, anchors=[a_id])
            assert res_s["converged"] is True
            single = {k: get_conf(res_s, k) for k in (a_id, b_id, c_id)}
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            link_tiered_source(sdk, b_id, "https://s1.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res_m = run_ep(sdk, anchors=[a_id])
            assert res_m["converged"] is True
            multi = {k: get_conf(res_m, k) for k in (a_id, b_id, c_id)}
        for k in (a_id, b_id, c_id):
            assert multi[k] >= single[k] - EPSILON  # >= single-entry (loose)
        # at least one node clearly higher (pre-declared relaxation)
        assert max(multi[k] - single[k] for k in multi) > 0


class TestSituation10_ChainResponse:
    def test_source_on_a_moves_b(self):
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            # T2 source (mean 0.75) — robust margin for B's response; T4 would
            # be marginal at w=1.0 (prior mean 0.5238)
            link_tiered_source(sdk, a_id, "https://s0.example", "T2")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res = run_ep(sdk, anchors=[a_id])
            a_conf = get_conf(res, a_id)
            b_conf = get_conf(res, b_id)
        assert a_conf > 0.5 + EPSILON
        assert b_conf > 0.5  # B responds through IMPL + bidirectional EP
        assert b_conf <= a_conf + EPSILON  # attenuation: B not above A


class TestEdgeCaseInvariants:
    CONFIGS = [{"T4": 1}, {"T0": 1}, {"T4": 10}, {"T0": 1, "T4": 1}]

    def test_convergence_under_50(self):
        for cfg in self.CONFIGS:
            with fresh_sdk() as sdk:
                a_id, b_id = build_scenario_a(sdk)
                for i, (tier, n) in enumerate(cfg.items()):
                    for j in range(n):
                        link_tiered_source(sdk, a_id, f"https://cfg{i}-{j}.example", tier)
                sdk._apply_source_inheritance(recency_decay=1.0)
                res = run_ep(sdk, anchors=[a_id])
                assert res["converged"] is True
                # hard cap is 50 (max_iter) — assert real convergence speed
                assert res["iterations"] <= 20

    def test_confidence_bounds(self):
        for cfg in self.CONFIGS:
            with fresh_sdk() as sdk:
                a_id, b_id = build_scenario_a(sdk)
                for i, (tier, n) in enumerate(cfg.items()):
                    for j in range(n):
                        link_tiered_source(sdk, a_id, f"https://cfg{i}-{j}.example", tier)
                sdk._apply_source_inheritance(recency_decay=1.0)
                res = run_ep(sdk, anchors=[a_id])
                for cid in (a_id, b_id):
                    conf = get_conf(res, cid)
                    assert 0.0 <= conf <= 1.0

    def test_determinism_seeded(self):
        """Two fresh SDKs built identically → same confidence (anchored subgraph + EP
        path, 3-factor loopy topology — real random.shuffle draws, seed-pinned)."""
        confs = []
        for _ in range(2):
            with fresh_sdk() as sdk:
                a_id, b_id = build_scenario_b(sdk)  # 3 factors — real shuffle draws
                link_tiered_source(sdk, a_id, "https://s0.example", "T0")
                sdk._apply_source_inheritance(recency_decay=1.0)
                res = run_ep(sdk, anchors=[a_id], seed=42)
                confs.append(get_conf(res, a_id))
        assert abs(confs[0] - confs[1]) < 1e-9
```

**Step 2: Verify**

Run: `python -m pytest tests/test_ep_sources.py -q -k "Scenario or Situation10 or EdgeCase" -p no:randomly`
Expected: PASS. If a loopy-cluster assertion is marginal (multi-entry not strictly > single on all nodes), relax to `>= - EPSILON` with the strict increase asserted only on the max (validation finding documented in the audit — do not silently weaken without recording).

**Step 3: Commit**

```bash
git add tests/test_ep_sources.py
git commit -m "test(341): T2b EP directional audit — topologies A/B/C + S10 + edge invariants"
```

### Task 6: NAND/mitigation audit (situations 8-9) + proof writeup + bug issue

**Intent:** Situations 8-9 involve the EP NAND factor whose real behavior (phi_nand = agreement potential) contradicts the issue's mental model. Document, don't encode. Deliver the mathematical proof + audit artifact.
**Acceptance:** Audit doc written; bug issue filed with repro; no test asserts NAND direction; gold-anchor assertion (no-source vs gold) covered.
**Files:**
- Modify: `tests/test_ep_sources.py` (audit section)
- Create: `docs/plans/2026-08-08-ep-source-validation-proof.md`

**Step 1: Write audit tests (document, don't assert NAND direction)**

```python
class TestSituation8_GoldPlusNand_Audit:
    def test_gold_alone_anchors_high(self):
        """Gold source alone anchors the claim high (the part that IS asserted)."""
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            link_tiered_source(sdk, a_id, "https://g0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            conf = get_conf(run_ep(sdk, anchors=[a_id]), a_id)
        assert conf >= 0.8  # T0 single source -> Beta(10,1) mean 0.909


class TestSituation9_Mitigation_Audit:
    def test_mitigation_weight_mechanics(self):
        """AUDIT (documented, not directional): real mitigation is
        compute_operator_weight (weights.py:9) — w *= 2.0 when an operator
        targets another operator. The issue's pc*0.5 mitigation model is
        fictional (old suite). This pins the MECHANIC (not NAND direction)."""
        import tortoise.weights as weights_mod
        from tortoise.sdk import TortoiseSDK
        import tempfile, os
        with tempfile.TemporaryDirectory(prefix="tt_341_") as td:
            sdk = TortoiseSDK(os.path.join(td, "test.db"))
            try:
                a = sdk.create_point("statement", "A")
                b = sdk.create_point("statement", "B")
                op = sdk.create_operator("IMPL", a["id"], [b["id"]])
                # plain operator -> w == 1.0 (no input operator)
                w_plain = weights_mod.compute_operator_weight(sdk._get_proj(), op["id"])
                assert w_plain == pytest.approx(1.0, rel=1e-9)
                # operator targeting an operator -> w == 2.0
                op2 = sdk.create_operator("NAND", a["id"], [op["id"]])
                w_mit = weights_mod.compute_operator_weight(sdk._get_proj(), op2["id"])
                assert w_mit == pytest.approx(2.0, rel=1e-9)
            finally:
                sdk.close()
```

**Step 2: Write the proof writeup**

Create `docs/plans/2026-08-08-ep-source-validation-proof.md` with:
- T1 theorem statement + derivation (log2 increasing, nonneg terms, per-source concavity)
- Scoping statement (uniform weight, decay=1.0; assessment-factor mean can decrease pc — intended, doc S5/S6)
- Spec corrections (log-flatten, 10 T4 ≈ 1 T2, NAND→beta fictional, mitigation ≠ pc×0.5)
- Audit findings table: phi_nand docstring (0.637/0.064 claimed vs 0.519/0.135 actual), agreement-potential shape, comment sites test_directional_impl.py:302,359 + test_directional_impl_fix.py:335, live gold+NAND behavior (both claims rise)
- Bug issue content

**Step 3: File the bug issue (routed to EP-propagation owner)**

```bash
gh issue create --title "NAND factor phi_nand is an agreement potential, not contradiction — docstring + tests cite wrong numbers" \
  --body "$(cat <<'EOF'
**Found by:** #341 validation audit
**Owner:** EP-propagation (feat/326-ep-propagation)

## Expected (docstring intent, quadrature.py:68)
"NAND: equal-quality contradiction returns to ~50%"; "When both T0(0.91): phi ~ 0.637"; "When both baseline(0.5): phi ~ 0.064"

## Actual
- phi_nand(0.91, 0.91) = 0.5193 (not 0.637)
- phi_nand(0.5, 0.5) = 0.1353 (not 0.064)
- phi_nand is MAXIMIZED at (1,1)/(0,0) = 1.0, MINIMIZED at (1,0)/(0,1) = 0.0183 — an agreement/XNOR potential, opposite of contradiction
- Live EP: gold+NAND on two claims raises BOTH (0.909 -> 0.912-0.924 at w=1-5)

## Repro
phi_nand(ca, cb) = exp(-w*(ca(1-cb)+cb(1-ca))/2); evaluate at (0.91,0.91), (0.5,0.5), (1,1), (1,0).

## Stale docstring/comment sites
- tortoise/quadrature.py:68-80 (docstring numbers)
- tests/test_directional_impl.py:302,359 (0.637 comments)
- tests/test_directional_impl_fix.py:335 (0.637 comment)
EOF
)" --label "bug"
```

**Step 4: Verify audit tests**

Run: `python -m pytest tests/test_ep_sources.py -q -k "Audit or Situation8" -p no:randomly`
Expected: PASS.

**Step 5: Commit**

```bash
git add tests/test_ep_sources.py docs/plans/2026-08-08-ep-source-validation-proof.md
git commit -m "test(341): NAND/mitigation audit + proof writeup + spec corrections"
```

### Task 7: Full verification + plan-review signature

**Intent:** Confirm the whole suite is green, no regressions, and the plan gate passes.
**Acceptance:** 3-file scope + full-suite spot check green; baseline pre-existing failure now fixed.
**Files:** none (verification only)

**Step 1: Run the 3-file scope**

Run: `python -m pytest tests/test_ep_sources.py tests/test_ep_nary_falsification.py tests/test_source_inheritance_own.py -q -p no:randomly`
Expected: all pass. (`-p no:randomly` pins order — redislite-spawned embedded DBs are order-sensitive under parallel contention, per research doc.)

**Step 2: Run full suite spot check**

Run: `python -m pytest tests/ -q -p no:randomly 2>&1 | tail -20`
Expected: no NEW failures vs baseline (baseline had 1 fail: test_run_converges_with_gentle_factor — now fixed by Task 1; the flaky identity under parallel contention documented).

**Step 2.5: Temp-dir hygiene**

`fresh_sdk()` registers its tempdir for cleanup (shutil.rmtree after close) — ~40 SDK instances per run otherwise leak MBs each. Use `tempfile.TemporaryDirectory()` context in the helper.

**Step 3: Append plan-review signature**

```bash
echo "<!-- plan-review: cycles=N, status=clean, version=2.2.0 -->" >> docs/plans/2026-08-08-ep-source-validation-impl.md
```

## Acceptance Criteria (from issue O/I/T)
1. Test suite passes for all scenarios (A/B/C + situations 1-10, corrected where the issue's literal claims were numerically wrong).
2. Confidence is monotonic: more sources (uniform weight) = higher confidence — proven at prior level (exact) and verified directionally at EP level. 0 edge cases where adding a source reduces the inherited prior under uniform weight.
3. Log-scale targets validated: 1M T4 < 2 T0; 10 T4 > 1 T4; per-source marginal decreases.
4. NAND/mitigation engine behavior documented as audit + bug issue (never silently encoded).
5. Pre-existing `test_run_converges_with_gentle_factor` regression fixed (test-side).

## Runtime Prerequisites
- Python 3.11+ (worktree venv has 3.12), embedded falkordblite, pytest. `uv pip install -e .` done.

## Risks & Mitigations
| Risk | Mitigation |
|---|---|
| EP nondeterminism | random.seed(42) fixture; directional-only assertions; loose margins |
| recompute_interval gate | fresh SDK per state OR recompute_interval=0; never rely on repeated compute_confidence; env pinned (TORTOISE_EP_REINHERIT_INTERVAL=0) |
| _flush_cache overwrites ep_alpha | T2a reads get_point() after _apply_source_inheritance only (no EP) |
| env-decay drift in compute_confidence | run_ep passes recency_decay=1.0 explicitly; env pinned (TORTOISE_EP_RECENCY_DECAY=1.0) |
| uncontrolled dream() branch | run_ep passes anchors=[...] (skips dream); determinism test compares 2 fresh SDKs |
| loopy B/C non-convergence | assert res["converged"] is True in B/C before reading confidences |
| stale _evidence after revert (sdk finding) | strict xfail test documents it; bug issue filed; NOT fixed in #341 |
| Revert marker semantics | sdk.py:1953 REMOVEs markers -> alpha is None (verified); contingency removed |
| Topology B/C not strictly monotone | pre-declared relaxation (all-nodes >= -EPSILON, strict on max entry node); recorded as finding |
| aggregate_prior signature drift | verified (source_credibility.py:169) — tuple (tier, source_date, ingested_at, factor) |
| Sub-agent infra timeouts | controller-run verification; fresh dispatches for plan-review + code-review gates |
<!-- plan-review: cycles=3, status=clean, version=2.2.0 -->
