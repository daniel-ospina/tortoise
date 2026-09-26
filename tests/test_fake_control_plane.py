"""#1719 Task 3 — FakeControlPlane UUID filter fidelity (the regression lock).

The fake previously string-compared filter values, so a non-UUID literal in
a ``user_id eq`` filter silently no-matched in CI while PostgREST 22P02'd in
prod (the exact "CI green while prod 500s" gap #1719 fixes). Default-on
fidelity mirrors the real seam: a non-UUID value on a registered uuid
column raises the same RuntimeError("... HTTP 400") the production query
raises, so a future unsanitized call site fails the suite.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fake_control_plane import FakeControlPlane  # noqa: E402


def _fake() -> FakeControlPlane:
    return FakeControlPlane(tables={"org_memberships": []})


def test_non_uuid_user_id_eq_filter_raises() -> None:
    """A non-UUID literal on org_memberships.user_id raises the same
    RuntimeError surface PostgREST produces (22P02 → HTTP 400)."""
    f = _fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("org_memberships", filters=[("user_id", "eq", "api")])


def test_non_uuid_user_id_eq_filter_raises_on_patch_and_delete() -> None:
    """Fidelity covers PATCH/DELETE too — those flow through _matches, and
    22P02 in prod is method-agnostic."""
    f = _fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("org_memberships", method="PATCH",
                filters=[("user_id", "eq", "anon-abc")], json_body={"role": "x"})
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("org_memberships", method="DELETE",
                filters=[("user_id", "eq", "reg-xyz")])


def test_uuid_user_id_eq_filter_ok() -> None:
    """A real UUID filter behaves normally (returns rows / [])."""
    import uuid
    uid = str(uuid.uuid4())
    f = FakeControlPlane(tables={"org_memberships": [
        {"org_id": "t1", "user_id": uid, "role": "owner", "status": "active"},
    ]})
    rows = f.query("org_memberships", select=["role"],
                   filters=[("user_id", "eq", uid)])
    assert rows == [{"role": "owner"}]


def test_user_id_is_null_filter_ok() -> None:
    """is.null has no cast — unaffected by fidelity."""
    f = FakeControlPlane(tables={"org_memberships": [
        {"org_id": "t1", "user_id": None, "role": "member", "status": "active"},
    ]})
    rows = f.query("org_memberships", select=["role"],
                   filters=[("user_id", "is", None)])
    assert rows == [{"role": "member"}]


def test_non_uuid_on_unregistered_column_ok() -> None:
    """Only registered uuid columns are checked — other columns (text) are
    untouched by fidelity."""
    f = FakeControlPlane(tables={"api_keys": [
        {"org_id": "t1", "created_by": "anon-abc", "enabled": True},
    ]})
    rows = f.query("api_keys", select=["org_id"],
                   filters=[("created_by", "eq", "anon-abc")])
    assert rows == [{"org_id": "t1"}]


def test_uuid_fidelity_escape_hatch() -> None:
    """uuid_fidelity=False opts out (suites deliberately testing pre-#1511
    non-UUID data)."""
    f = FakeControlPlane(tables={"org_memberships": []}, uuid_fidelity=False)
    assert f.query("org_memberships",
                   filters=[("user_id", "eq", "api")]) == []


def test_unsupported_filter_op_raises_on_patch_and_delete() -> None:
    """#3665 review: ``_matches`` (PATCH/DELETE) must RAISE on an op it does
    not implement instead of silently treating it as "matches".

    The GET path already raises for an unsupported op; letting the
    PATCH/DELETE path ignore one makes the fake mutate MORE rows than the real
    client would (the ignored predicate drops out), so a test can pass against
    behaviour production does not have — the same "CI green while prod
    differs" class this file exists to lock down.

    REDs on: reverting ``_matches`` to the if-chain with no final op check.
    GREEN legitimate form: an op every branch covers (``eq``)."""
    f = FakeControlPlane(tables={"org_memberships": [
        {"org_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001",
         "role": "member"},
    ]}, uuid_fidelity=False)
    with pytest.raises(ValueError, match="unsupported filter op"):
        f.query("org_memberships", method="DELETE",
                filters=[("org_id", "in", ["t1"])])

    # the supported-op control: the same PATCH/DELETE path still works
    assert f.query("org_memberships", method="DELETE",
                   filters=[("org_id", "eq", "t1")]) == []
