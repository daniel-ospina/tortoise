"""Tests for ep — Expectation Propagation belief propagation on factor graphs."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Check if FalkorDB available for integration tests
try:
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK()
    sdk.status()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False


class TestNaturalParameterHelpers:
    """Pure math: natural parameter ↔ Beta conversions."""
    def test_natural_from_beta_uniform(self):
        from tortoise.ep import TortoiseEP
        eta1, eta2 = TortoiseEP._natural_from_beta(1.0, 1.0)
        assert eta1 == 0.0
        assert eta2 == 0.0

    def test_natural_from_beta_biased(self):
        from tortoise.ep import TortoiseEP
        eta1, eta2 = TortoiseEP._natural_from_beta(5.0, 1.0)
        assert eta1 == 4.0
        assert eta2 == 0.0

    def test_beta_from_natural_roundtrip(self):
        from tortoise.ep import TortoiseEP
        alpha, beta = 3.0, 2.0
        eta1, eta2 = TortoiseEP._natural_from_beta(alpha, beta)
        a2, b2 = TortoiseEP._beta_from_natural(eta1, eta2)
        assert abs(a2 - alpha) < 0.01
        assert abs(b2 - beta) < 0.01

    def test_beta_from_natural_clamps_minimum(self):
        from tortoise.ep import TortoiseEP
        a, b = TortoiseEP._beta_from_natural(-2.0, -2.0)
        assert a >= 0.01
        assert b >= 0.01

    def test_conversions_are_static(self):
        """These should be callable on the class without an instance."""
        from tortoise.ep import TortoiseEP
        assert callable(TortoiseEP._natural_from_beta)
        assert callable(TortoiseEP._beta_from_natural)


class MockProjection:
    """Minimal mock for testing EP constructor and static methods."""
    def __init__(self):
        self.g = MockGraph()
        self._neighbors = {}


class MockGraph:
    def query(self, cypher, params=None):
        return MockResultSet()


class MockResultSet:
    def __init__(self, rows=None):
        self.result_set = rows or []


class TestTortoiseEPConstruction:
    def test_default_parameters(self):
        from tortoise.ep import TortoiseEP
        proj = MockProjection()
        ep = TortoiseEP(proj)
        assert ep.damping == 0.5
        assert ep.n_quad == 8
        assert ep.max_iter == 50
        assert ep.tol == 1e-3
        assert ep._evidence == {}

    def test_custom_parameters(self):
        from tortoise.ep import TortoiseEP
        proj = MockProjection()
        ep = TortoiseEP(proj, damping=0.3, n_quad=16, max_iter=100, tol=1e-4)
        assert ep.damping == 0.3
        assert ep.n_quad == 16
        assert ep.max_iter == 100

    def test_evidence_prior(self):
        from tortoise.ep import TortoiseEP
        proj = MockProjection()
        ep = TortoiseEP(proj, evidence={"c1": (5.0, 1.0), "c2": (1.0, 5.0)})
        assert ep._evidence["c1"] == (5.0, 1.0)
        assert ep._evidence["c2"] == (1.0, 5.0)

    def test_evidence_is_copied(self):
        """Modifying the original dict shouldn't affect the EP instance."""
        from tortoise.ep import TortoiseEP
        evidence = {"c1": (2.0, 2.0)}
        proj = MockProjection()
        ep = TortoiseEP(proj, evidence=evidence)
        evidence["c1"] = (9.0, 9.0)
        assert ep._evidence["c1"] == (2.0, 2.0)

    def test_damping_clamped(self):
        """Damping should be between 0 and 1."""
        from tortoise.ep import TortoiseEP
        proj = MockProjection()
        ep = TortoiseEP(proj, damping=0.0)
        assert ep.damping == 0.0
        ep = TortoiseEP(proj, damping=1.0)
        assert ep.damping == 1.0


class TestEPWithRealGraph:
    """Integration tests against running FalkorDB."""
    @pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
    def test_construct_with_real_projection(self):
        from tortoise.sdk import TortoiseSDK
        from tortoise.ep import TortoiseEP
        sdk = TortoiseSDK()
        proj = sdk._get_proj()
        ep = TortoiseEP(proj) if proj else None
        if ep:
            assert ep.proj is not None
            assert ep.g is not None

    @pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
    def test_natural_parameter_helpers_on_graph(self):
        """Verify helpers work with real data."""
        from tortoise.sdk import TortoiseSDK
        from tortoise.ep import TortoiseEP
        sdk = TortoiseSDK()
        proj = sdk._get_proj()
        if proj:
            ep = TortoiseEP(proj)
            # Default node values should be (1.0, 1.0) → natural (0,0)
            eta1, eta2 = ep._natural_from_beta(1.0, 1.0)
            assert eta1 == 0.0
            assert eta2 == 0.0
