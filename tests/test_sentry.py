"""Unit tests for the optional Sentry integration (#2444, tortoise/sentry.py).

Sentry activation is gated on SENTRY_DSN; all capture helpers no-op when
disabled. Tests inject a FAKE sentry_sdk module into sys.modules so they never
require the real package (or a DSN). The fake models the real SDK surface the
module touches (init kwargs, push_scope context manager with scope.set_tag,
capture_exception, capture_message) and records per-call scope-tag snapshots so
the tag-propagation path is observable.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

import tortoise.sentry as tsentry


class _Scope:
    """Context-manager scope fake: records set_tag calls into .tags."""

    def __init__(self, active: dict) -> None:
        self.tags: dict[str, str] = {}
        self._active = active
        self.methods: list[str] = []

    def set_tag(self, k: str, v: str) -> None:
        self.methods.append("set_tag")
        self.tags[k] = v

    def __enter__(self) -> _Scope:
        self._active["scope"] = self
        return self

    def __exit__(self, *a: object) -> bool:
        return False


@pytest.fixture()
def fake_sentry(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Inject a fake sentry_sdk module and reload tortoise.sentry fresh."""
    calls: dict[str, list] = {"init": [], "exc": [], "msg": []}
    active: dict = {"scope": None}

    def _snapshot(scope: object) -> dict:
        if scope is None:
            return {"tags": {}, "scope_methods": []}
        return {"tags": dict(scope.tags), "scope_methods": list(scope.methods)}

    mod = SimpleNamespace(
        init=lambda **kw: calls["init"].append(kw),
        push_scope=lambda: _Scope(active),
        capture_exception=lambda e: calls["exc"].append(
            {"exception": e, **_snapshot(active["scope"])}
        ),
        capture_message=lambda m, level="info": calls["msg"].append(
            {"message": m, "level": level, **_snapshot(active["scope"])}
        ),
    )
    monkeypatch.setitem(sys.modules, "sentry_sdk", mod)
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_ENV", raising=False)
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    importlib.reload(tsentry)
    return calls


def test_disabled_without_dsn(fake_sentry: dict[str, list]) -> None:
    assert tsentry.init() is False
    assert tsentry.enabled() is False
    tsentry.capture_exception(RuntimeError("x"), tags={"a": "b"})
    tsentry.capture_message("m", level="warning")
    assert fake_sentry["exc"] == []
    assert fake_sentry["msg"] == []
    assert fake_sentry["init"] == []  # sentry_sdk.init never called


def test_enabled_with_dsn(monkeypatch: pytest.MonkeyPatch, fake_sentry: dict[str, list]) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://dsn@example.invalid/1")
    importlib.reload(tsentry)
    assert tsentry.init() is True
    assert tsentry.enabled() is True
    init_kwargs = fake_sentry["init"][0]
    assert init_kwargs["dsn"] == "https://dsn@example.invalid/1"
    assert init_kwargs["environment"] == "production"  # SENTRY_ENV unset default
    assert init_kwargs["traces_sample_rate"] == 0.0  # default: tracing off
    assert init_kwargs["send_default_pii"] is False

    # exception capture forwards the exception AND tags onto the scope
    err = RuntimeError("boom")
    tsentry.capture_exception(err, tags={"team_id": "t1"})
    assert fake_sentry["exc"] == [
        {"exception": err, "tags": {"team_id": "t1"}, "scope_methods": ["set_tag"]}
    ]

    # exception capture with NO tags skips the scope.set_tag loop entirely
    tsentry.capture_exception(RuntimeError("no-tags"))
    assert fake_sentry["exc"][-1]["tags"] == {}
    assert fake_sentry["exc"][-1]["scope_methods"] == []  # `if tags:` guard skipped

    # message capture forwards message, explicit level, and tags
    tsentry.capture_message("warn", level="warning", tags={"a": "b"})
    assert fake_sentry["msg"] == [
        {"message": "warn", "level": "warning", "tags": {"a": "b"}, "scope_methods": ["set_tag"]}
    ]

    # default level is "warning" when omitted
    tsentry.capture_message("implicit-level")
    assert fake_sentry["msg"][-1]["message"] == "implicit-level"
    assert fake_sentry["msg"][-1]["level"] == "warning"
    assert fake_sentry["msg"][-1]["tags"] == {}


def test_env_override_flows_into_init(
    monkeypatch: pytest.MonkeyPatch, fake_sentry: dict[str, list]
) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://dsn@example.invalid/1")
    monkeypatch.setenv("SENTRY_ENV", "staging")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    importlib.reload(tsentry)
    assert tsentry.init() is True
    kw = fake_sentry["init"][0]
    assert kw["environment"] == "staging"
    assert kw["traces_sample_rate"] == 0.25


def test_init_failure_does_not_break(
    monkeypatch: pytest.MonkeyPatch, fake_sentry: dict[str, list]
) -> None:
    """A Sentry init failure must never take the API down — init returns False and
    capture helpers stay inert (zero SDK calls), not just non-raising."""
    import sentry_sdk as _fake  # resolves to the injected fake

    def _boom(**kw: object) -> None:
        raise RuntimeError("sentry unavailable")

    monkeypatch.setattr(_fake, "init", _boom)
    monkeypatch.setenv("SENTRY_DSN", "https://dsn@example.invalid/1")
    importlib.reload(tsentry)
    assert tsentry.init() is False
    assert tsentry.enabled() is False
    # capture helpers after a failed init are no-ops: no exception AND no SDK call
    tsentry.capture_exception(RuntimeError("x"), tags={"a": "b"})
    tsentry.capture_message("m")
    assert fake_sentry["exc"] == []
    assert fake_sentry["msg"] == []


def test_capture_failures_are_swallowed(
    monkeypatch: pytest.MonkeyPatch, fake_sentry: dict[str, list]
) -> None:
    """Sentry SDK failures inside capture helpers must never propagate to callers."""
    import sentry_sdk as _fake  # resolves to the injected fake

    monkeypatch.setenv("SENTRY_DSN", "https://dsn@example.invalid/1")
    importlib.reload(tsentry)
    assert tsentry.init() is True

    boom_hits: list[str] = []

    def _boom_capture(_e: object) -> None:
        boom_hits.append("capture_exception")
        raise RuntimeError("sentry capture failed")

    def _boom_scope() -> object:
        boom_hits.append("push_scope")
        raise RuntimeError("sentry scope failed")

    monkeypatch.setattr(_fake, "capture_exception", _boom_capture)
    tsentry.capture_exception(RuntimeError("x"), tags={"a": "b"})  # must not raise

    monkeypatch.setattr(_fake, "push_scope", _boom_scope)
    tsentry.capture_exception(RuntimeError("x"))  # push_scope failure also swallowed
    tsentry.capture_message("m")  # message path shares the same guard

    # the booms actually fired — the swallows exercised the real failure paths
    assert boom_hits == ["capture_exception", "push_scope", "push_scope"]


def test_init_is_idempotent(monkeypatch: pytest.MonkeyPatch, fake_sentry: dict[str, list]) -> None:
    """init() twice with a DSN initializes the SDK exactly once."""
    monkeypatch.setenv("SENTRY_DSN", "https://dsn@example.invalid/1")
    importlib.reload(tsentry)
    assert tsentry.init() is True
    assert tsentry.init() is True
    assert len(fake_sentry["init"]) == 1


def test_import_failure_when_dsn_set(
    monkeypatch: pytest.MonkeyPatch, fake_sentry: dict[str, list]
) -> None:
    """A missing sentry_sdk package must degrade to disabled, not crash."""
    import builtins

    real_import = builtins.__import__

    def _no_sentry(name: str, *a: object, **kw: object) -> object:
        if name == "sentry_sdk":
            raise ModuleNotFoundError("No module named 'sentry_sdk'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_sentry)
    monkeypatch.setenv("SENTRY_DSN", "https://dsn@example.invalid/1")
    importlib.reload(tsentry)
    assert tsentry.init() is False
    assert tsentry.enabled() is False
    tsentry.capture_exception(RuntimeError("x"))  # no-raise with no SDK present
    tsentry.capture_message("m")
