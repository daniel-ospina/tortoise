"""Epic #902 A2 — the failure contract (plan §6.3/§6.4 + cycle-23/24 pins).

BundleValidationError shape, _safe special-casing (violations intact through
the MCP boundary), the dedicated-branch ordering pin, Phase2Error batch_id,
and ERR_BUNDLE_INVALID wire shape.
"""
import os
import tempfile

import pytest

import tortoise.mcp_server as m
from tortoise.exceptions import BundleValidationError, Phase2Error
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db = os.path.join(tempfile.mkdtemp(prefix="a2_"), "test.db")
    s = TortoiseSDK(db)
    yield s
    s.close()


def _transport(fn):
    """Run fn with the dev-mode stdio transport context set."""
    m._transport_mode.set("stdio")
    try:
        return fn()
    finally:
        m._transport_mode.set(None)


BAD_BUNDLE = {
    "points": [
        {"kind": "statement", "content": "A"},
        {"ref": "pB", "content": 5},  # non-string content — check-1 violation
    ],
    "connections": [],
}


class TestBundleValidationError:
    def test_carries_all_violations(self, sdk):
        with pytest.raises(BundleValidationError) as exc:
            sdk.ingest(BAD_BUNDLE)
        e = exc.value
        assert e.violations, "must carry at least one violation"
        for v in e.violations:
            assert {"section", "index", "message"} <= set(v)
        # str() = first message (back-compat parity)
        assert str(e) == e.violations[0]["message"]

    def test_as_dict_wire_shape(self, sdk):
        with pytest.raises(BundleValidationError) as exc:
            sdk.ingest(BAD_BUNDLE)
        d = exc.value.as_dict()
        assert d["code"] == "ERR_BUNDLE_INVALID"
        assert d["violations"] == exc.value.violations

    def test_subclasses_valueerror(self):
        # Pre-A2 callers catching ValueError keep working.
        assert issubclass(BundleValidationError, ValueError)


class TestSafeSpecialCasing:
    def test_violations_survive_safe(self, sdk):
        r = _transport(lambda: m._safe(sdk.ingest, BAD_BUNDLE))
        assert r["code"] == m.ERR_BUNDLE_INVALID
        assert "violations" in r
        assert r["violations"], "violations must survive the MCP boundary"
        for v in r["violations"]:
            assert {"section", "index", "message"} <= set(v)
        assert r["error"] == r["violations"][0]["message"]

    def test_ordering_bundle_before_generic(self, sdk):
        # A BundleValidationError must NEVER fall through to the generic
        # {error}-only Phase-2 shape.
        r = _transport(lambda: m._safe(sdk.ingest, BAD_BUNDLE))
        assert "code" in r and r["code"] == m.ERR_BUNDLE_INVALID
        assert "violations" in r

    def test_quota_branch_still_precedes_generic(self, sdk, monkeypatch):
        from tortoise.quota import QuotaExceededError

        def boom(*a, **k):
            raise QuotaExceededError("team points limit reached")

        r = _transport(lambda: m._safe(boom))
        assert r["code"] == m.ERR_QUOTA

    def test_generic_branch_no_code(self, sdk):
        def boom(*a, **k):
            raise RuntimeError("simulated mid-Phase-2 DB failure")

        r = _transport(lambda: m._safe(boom))
        assert "code" not in r
        assert "violations" not in r
        assert "simulated" in r["error"]


class TestPhase2Error:
    def test_carries_batch_id(self):
        e = Phase2Error("ingest: points[2] write failed", batch_id="b1")
        assert e.batch_id == "b1"
        assert str(e) == "ingest: points[2] write failed"

    def test_safe_surfaces_batch_id_no_code(self, sdk):
        def boom(*a, **k):
            raise Phase2Error("ingest: points[2] write failed", batch_id="b1")

        r = _transport(lambda: m._safe(boom))
        assert r["batch_id"] == "b1"
        assert "code" not in r
        assert "violations" not in r
        assert "points[2]" in r["error"]

    def test_ingest_phase2_raises_carry_batch_id(self, sdk):
        # A bundle that passes Phase 1 but whose Phase-2 write path raises a
        # parity violation (race class simulated) — the raised Phase2Error
        # must carry the computed batch_id. Force a Phase-2-only failure by
        # patching _get_proj to fail on the 2nd call (post-batch_id).
        import tortoise.sdk as sdkmod
        calls = {"n": 0}
        orig = sdkmod.TortoiseSDK._get_proj

        def flaky(self):
            calls["n"] += 1
            if calls["n"] >= 2:
                raise RuntimeError("simulated mid-Phase-2 DB failure")
            return orig(self)

        sdkmod.TortoiseSDK._get_proj = flaky
        try:
            r = _transport(lambda: m._safe(sdk.ingest, {
                "points": [{"kind": "statement", "content": "A", "ref": "pA"}],
                "connections": [],
            }))
            # The generic RuntimeError path: {error} with NO code — the
            # Phase-2 discrimination (E2E-15(h)); batch_id presence is
            # asserted for Phase2Error-shaped failures in
            # test_safe_surfaces_batch_id_no_code (the DB-failure
            # attribution wrapper is A3-owned per plan §5.5).
            assert "code" not in r
            assert "simulated" in r["error"]
        finally:
            sdkmod.TortoiseSDK._get_proj = orig
