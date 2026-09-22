"""A4 A/B arm unit tests (epic #2080 E2E-1 When-3, issue #2100 Indicator 6).

Hermetic: measure() runs with an INJECTED pair set + fake rank seam +
monkeypatched variance/relevance reads — no graph, no network.  Pins the
arm's semantics: variance-tier calibration, conflict-relevant queries firing
on every pair, the at-threshold CONTROL excluded from the contested-tier
rates, regime-dependent ordering ⇒ measured (with the delta recorded), and
the honest "not measured" recording when the ordering is regime-independent
(the calibrated When-3 pair set is open work — recorded as a precise gap,
never faked).
"""

from __future__ import annotations

from eval.why_suite import a4_ab

from tortoise.ranking import W4_CONTESTED_BOOST

# A4 tiers (see seeding.A4_TIER_POSTERIORS): at_threshold variance == 0.04,
# just_over == 0.05, high == 0.0625.
TIER_VARIANCES = {"at_threshold": 0.04, "just_over": 0.05, "high": 0.0625}


class _FakeSDK:
    """Minimal stand-in: measure() only reaches the SDK through the
    monkeypatched variance/relevance reads + the injected rank seam."""

    def __init__(self) -> None:
        self._proj = object()

    def _get_proj(self):
        return self._proj


def _pair_set() -> dict:
    """Two pairs per tier (the committed spec) with synthetic ids."""
    pairs = []
    index = 0
    for tier in ("at_threshold", "just_over", "high"):
        for _ in range(2):
            pairs.append(
                {
                    "pair_id": f"pair-{index}",
                    "tier": tier,
                    "contested_twin": f"ct{index}",
                    "clean_twin": f"cl{index}",
                    "counter": f"co{index}",
                    "query": f"ct{index} counterargument gamma",
                }
            )
            index += 1
    return {"pairs": pairs}


class _FakeRank:
    """Deterministic rank seam: orders per a callable configured by the
    test (the ordering may depend on the env flag, mirroring the real
    ranker's flag-on/off seam)."""

    def __init__(self, on_order=None, off_order=None) -> None:
        import os

        self.on_order = on_order or {}
        self.off_order = off_order or {}
        self.calls = []
        self._os = os

    def __call__(self, sdk, query: str):
        import os

        on = os.environ.get("TORTOISE_W4_ENRICHMENT", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self.calls.append((query, on))
        order = self.on_order if on else self.off_order
        hits = order.get(query)
        return [{"id": pid} for pid in (hits or [])]


def _patch(monkeypatch, *, variances=None, relevance=None):
    if variances is None:

        def _default_var(proj, pid):
            index = int(pid[2:]) if pid.startswith("ct") else 0
            tier = ("at_threshold", "at_threshold", "just_over", "just_over", "high", "high")[index]
            return TIER_VARIANCES[tier]

        variances = _default_var

    def _var(proj, pid):
        return (
            variances(proj, pid)
            if callable(variances)
            else variances.get(pid, TIER_VARIANCES["just_over"])
        )

    monkeypatch.setattr(a4_ab, "_persisted_variance", _var)
    if relevance is None:

        def _rel(proj, ids, query):
            return {pid: W4_CONTESTED_BOOST for pid in ids}

        relevance = _rel
    monkeypatch.setattr(a4_ab, "resolve_contested_relevance", relevance)


def _default_order(seed) -> dict:
    """Flag-on and flag-off orderings that put the contested twin FIRST on
    contested tiers (the E2E-1 expectation) and the clean twin first on the
    control (boost never fires there)."""
    on, off = {}, {}
    for pair in seed["pairs"]:
        q = pair["query"]
        if pair["tier"] == "at_threshold":
            on[q] = [pair["clean_twin"], pair["contested_twin"]]
            off[q] = [pair["clean_twin"], pair["contested_twin"]]
        else:
            on[q] = [pair["contested_twin"], pair["clean_twin"]]
            off[q] = [pair["clean_twin"], pair["contested_twin"]]
    return on, off


def test_measured_when_boost_flips_contested_tiers(monkeypatch):
    seed = _pair_set()
    on, off = _default_order(seed)
    _patch(monkeypatch)
    result = a4_ab.measure(_FakeSDK(), seed, rank_fn=_FakeRank(on, off))
    assert result["measured"] is True
    # Contested tiers (4 of 6 pairs): flag-on puts the contested twin first
    # everywhere; flag-off (confidence-only) never does.
    assert result["contested_first_rate_on"] == 1.0
    assert result["contested_first_rate_off"] == 0.0
    assert result["delta"] == 1.0
    assert result["pre_assertions"]["variance_calibrated"] is True
    assert result["pre_assertions"]["conflict_relevant"] is True
    assert result["notes"] == []


def test_boost_noop_records_not_measured_with_gap(monkeypatch):
    """Regime-independent ordering (the ranker's confidence weighting
    dominates on naive twins — the measured reality on this corpus): the
    arm records NOT measured + the precise calibration gap, never a faked
    delta."""
    seed = _pair_set()
    same = {}
    for pair in seed["pairs"]:
        same[pair["query"]] = [pair["clean_twin"], pair["contested_twin"]]
    _patch(monkeypatch)
    result = a4_ab.measure(_FakeSDK(), seed, rank_fn=_FakeRank(same, same))
    assert result["measured"] is False
    assert result["delta"] == 0.0
    assert any("regime-INDEPENDENT" in n for n in result["notes"])
    assert any("calibrated When-3 pair set is open" in n for n in result["notes"])


def test_at_threshold_control_flip_is_boundary_violation(monkeypatch):
    """The at-threshold control twin is NOT contested (variance ==
    threshold — the W4-b strict boundary): a regime flip on the control is
    a BOUNDARY VIOLATION (the boost fired on a not-contested state) —
    recorded, NEVER measured; the contested-tier rates stay unaffected."""
    seed = _pair_set()
    on, off = {}, {}
    for pair in seed["pairs"]:
        q = pair["query"]
        if pair["tier"] == "at_threshold":
            on[q] = [pair["contested_twin"], pair["clean_twin"]]  # flip
        else:
            on[q] = [pair["clean_twin"], pair["contested_twin"]]
        off[q] = [pair["clean_twin"], pair["contested_twin"]]
    _patch(monkeypatch)
    result = a4_ab.measure(_FakeSDK(), seed, rank_fn=_FakeRank(on, off))
    assert result["measured"] is False
    assert result["contested_first_rate_on"] == 0.0
    assert any("BOUNDARY VIOLATION" in n for n in result["notes"])


def test_pre_assertion_failure_never_records_measured(monkeypatch):
    """An invalid pair set (failed pre-assertions) never records a measured
    delta — even when contested-tier pairs flip between regimes."""
    seed = _pair_set()
    on, off = _default_order(seed)
    _patch(monkeypatch, variances=lambda proj, pid: 0.02)
    result = a4_ab.measure(_FakeSDK(), seed, rank_fn=_FakeRank(on, off))
    assert result["measured"] is False
    assert result["pre_assertions"]["variance_calibrated"] is False
    assert any("never records a measured delta" in n for n in result["notes"])


def test_variance_miscalibration_fails_pre_assertion(monkeypatch):
    seed = _pair_set()
    on, off = _default_order(seed)
    # Every planted twin reads 0.02 — no tier's calibration can pass.
    _patch(monkeypatch, variances=lambda proj, pid: 0.02)
    result = a4_ab.measure(_FakeSDK(), seed, rank_fn=_FakeRank(on, off))
    assert result["pre_assertions"]["variance_calibrated"] is False
    assert any("variance not calibrated" in n for n in result["notes"])


def test_irrelevant_query_fails_pre_assertion(monkeypatch):
    seed = _pair_set()
    on, off = _default_order(seed)
    _patch(monkeypatch, relevance=lambda proj, ids, query: {})
    result = a4_ab.measure(_FakeSDK(), seed, rank_fn=_FakeRank(on, off))
    assert result["pre_assertions"]["conflict_relevant"] is False
    assert any("did not fire" in n for n in result["notes"])
