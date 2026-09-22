"""#3926 — a user point prop named ``error`` must not suppress the success path.

``_safe`` reports failures with an ``_SafeError`` (a ``dict`` subclass); a
successful write returns the created node's own property dict, which may
legitimately carry a user-supplied key named ``error``. Gating the onboarding
observation on ``"error" not in result`` conflated the two and silently dropped
the observation — a false negative, so never a truth violation, but a real
suppression of the #3784 observation path.

RED before the fix: ``test_create_point_with_user_error_prop_records_observation``
fails (no observation recorded). GREEN after.
"""
from __future__ import annotations

import contextlib
import json
import os

from tortoise import mcp_server


@contextlib.contextmanager
def _hosted_ctx(tmp_path, name: str):
    """A minimal hosted team context on a temp embedded graph."""
    os.environ.setdefault("TORTOISE_SESSION_LLM_MOCK", "1")
    from tests._http_fixtures import patched_tortoise_sdk
    from tortoise.mcp_auth import _current_org_id, _current_org_limits, _transport_mode

    with patched_tortoise_sdk(str(tmp_path / f"{name}.db")):
        import tortoise.hosted_api as _ha
        _ha._make_sdk(namespace="registry")._get_registry().query(
            "CREATE (t:Team {id:$id})", params={"id": f"team-{name}"})
        tok_t = _current_org_id.set(f"team-{name}")
        tok_l = _current_org_limits.set(
            {"org_id": f"team-{name}", "tier": "free",
             "max_points": 100000, "max_sessions": None})
        tok_m = _transport_mode.set("http")
        try:
            yield
        finally:
            _current_org_id.reset(tok_t)
            _current_org_limits.reset(tok_l)
            _transport_mode.reset(tok_m)


def test_create_point_with_user_error_prop_records_observation(tmp_path, monkeypatch):
    """The defect direction: a *successful* write whose props carry a user key
    named ``error`` must still record the onboarding observation."""
    calls: list[bool] = []
    # The merged signature carries the #3784 `decision_observed` keyword, so the
    # stub must tolerate it; the assertion below is about the CALL, not the kwarg.
    monkeypatch.setattr(mcp_server, "_maybe_onboarding_auto_complete",
                        lambda **_kw: calls.append(True))
    with _hosted_ctx(tmp_path, "err-prop"):
        res = mcp_server.tortoise_create_point(
            kind="statement",
            content="a point whose props carry a user error key",
            props={"error": "user-supplied, not a transport failure"})
    assert res.get("error") == "user-supplied, not a transport failure", res
    assert calls == [True], (
        "a successful write must record the onboarding observation even when a "
        "user prop is named 'error' (#3926)")


def test_failed_write_does_not_record_observation(tmp_path, monkeypatch):
    """The other direction: a real transport failure must NOT record."""
    calls: list[bool] = []
    monkeypatch.setattr(mcp_server, "_maybe_onboarding_auto_complete",
                        lambda **_kw: calls.append(True))
    from tortoise.mcp_auth import _transport_mode
    with _hosted_ctx(tmp_path, "err-real"):
        tok = _transport_mode.set(None)  # fail-closed: _safe returns auth error
        try:
            res = mcp_server.tortoise_create_point(
                kind="statement", content="a write that must fail")
        finally:
            _transport_mode.reset(tok)
    assert isinstance(res, mcp_server._SafeError), res
    assert res.get("error"), res
    assert calls == [], "a failed write must never record an observation"


def test_safe_error_preserves_dict_wire_shape_and_is_distinguishable():
    """The structural contract the gates rely on: failures are an ``_SafeError``
    (a dict subclass) — behaviour-identical on the wire, but never confusable
    with a plain success prop dict."""
    err = mcp_server._SafeError({"error": "boom", "code": "X"})
    assert isinstance(err, dict)
    assert err == {"error": "boom", "code": "X"}
    assert json.dumps(err) == '{"error": "boom", "code": "X"}'
    assert isinstance(err, mcp_server._SafeError)
    assert not isinstance({"error": "a user prop"}, mcp_server._SafeError)
