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

    The PATCH and DELETE branches pre-validate the whole filter list before
    evaluating it, so the cases below rest on ``_validate_filter_ops`` and not
    on ``_matches``' own final op check; removing either call makes the
    corresponding case return ``[]`` instead of raising.
    GREEN legitimate form: an op every branch covers (``eq``)."""
    f = FakeControlPlane(tables={"org_memberships": [
        {"org_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001",
         "role": "member"},
    ]}, uuid_fidelity=False)
    with pytest.raises(ValueError, match="unsupported filter op"):
        f.query("org_memberships", method="DELETE",
                filters=[("org_id", "in", ["t1"])])

    # ...but the row scan is not the DELETE guard's only line of defence. With
    # no row to test (an EMPTY table) _matches is never called, so the guard
    # must come from the branch itself (#2642 re-review P2).
    with pytest.raises(ValueError, match="unsupported filter op"):
        FakeControlPlane(tables={"org_memberships": []},
                         uuid_fidelity=False).query(
            "org_memberships", method="DELETE",
            filters=[("org_id", "in", ["t1"])])

    # Same gap when a row exists but an EARLIER filter already failed: _matches
    # short-circuits before reaching the unsupported op, so the whole list must
    # be validated up front, not only as the scan reaches each predicate
    # (#2642 re-review P2).
    with pytest.raises(ValueError, match="unsupported filter op"):
        f.query("org_memberships", method="DELETE",
                filters=[("org_id", "eq", "NOMATCH"),
                         ("org_id", "in", ["t1"])])

    # The guard is NOT scoped to a non-empty body: the empty-body early
    # return scans no rows, so without an explicit pre-return validation it
    # carried the query past ``_matches`` and returned ``[]`` with no error
    # (#2642 re-review P2). An op the fake cannot model must raise whether or
    # not a body is supplied.
    with pytest.raises(ValueError, match="unsupported filter op"):
        f.query("org_memberships", select=["org_id"], method="PATCH",
                filters=[("org_id", "in", ["t1"])], json_body={})

    # ...and the DELETE-branch validation must NOT reach POST, which ignores
    # ``filters`` (it inserts the body). Hoisting ``_validate_filter_ops`` to
    # the top of ``_query_impl`` would newly reject this query — the placement
    # the #2642 re-review P2 prescription called out explicitly.
    assert f.query("org_memberships", method="POST",
                   filters=[("org_id", "in", ["t1"])],
                   json_body={"org_id": "t1", "user_id": "u"}) == [
        {"org_id": "t1", "user_id": "u"}]

    # the supported-op control: the same PATCH/DELETE path still works
    assert f.query("org_memberships", method="DELETE",
                   filters=[("org_id", "eq", "t1")]) == []


def test_patch_empty_body_makes_no_updates_and_returns_no_representation() -> None:
    """#2642 re-review P2 — the PATCH empty-body branch is load-bearing.

    PostgREST "makes no updates" for a bodyless PATCH, so the representation
    is ``[]`` even when the filter matches (UpdateSpec.hs:
    ``PATCH /items?select=id`` + ``{}`` → ``[]``). Without that branch the
    fake returns the MATCHED row's representation, which is precisely the
    fake/production divergence that hid the empty-body 404 — so the branch
    must be pinned by a test, not merely exercised when the seam fix is
    absent.

    REDs on: deleting the ``if not json_body: return []`` branch (case 1
    then returns ``[{"id": "A"}]`` instead of ``[]``)."""
    def _cp() -> FakeControlPlane:
        return FakeControlPlane(tables={"t": [{"id": "A", "org_id": "O"}]})

    match: list[tuple[str, str, object]] = [("org_id", "eq", "O"), ("id", "eq", "A")]

    # 1. empty body + select, filter MATCHES → no representation, no mutation.
    #    This is the assertion that discriminates the empty-body branch.
    cp = _cp()
    assert cp.query("t", select=["id"], method="PATCH", filters=match, json_body={}) == []
    assert cp.tables["t"] == [{"id": "A", "org_id": "O"}]

    # 2. empty body, select-less → still no representation
    assert _cp().query("t", method="PATCH", filters=match, json_body={}) == []

    # 3. non-empty body, select-less, filter MATCHES → no representation
    #    (PostgREST echoes a representation only when ``?select=`` is given)
    cp = _cp()
    assert cp.query("t", method="PATCH", filters=match, json_body={"org_id": "O"}) == []
    assert cp.tables["t"] == [{"id": "A", "org_id": "O"}]

    # 4. non-empty body + select, filter does NOT match → no representation
    #    (a genuinely non-matching filter, so this is empty for the ordinary
    #    reason — it guards against the empty-body branch ever being widened)
    nomatch: list[tuple[str, str, object]] = [("org_id", "eq", "OTHER")]
    cp = _cp()
    assert cp.query("t", select=["id"], method="PATCH", filters=nomatch,
                    json_body={"org_id": "B"}) == []
    assert cp.tables["t"] == [{"id": "A", "org_id": "O"}]

    # 5. positive control — SAME shape as (1) but with a non-empty body: the
    #    filter matches and the updated row IS returned. This proves (1)'s
    #    ``[]`` came from the empty-body branch, not from a non-matching
    #    filter.
    cp = _cp()
    assert cp.query("t", select=["id"], method="PATCH", filters=match,
                    json_body={"org_id": "O"}) == [{"id": "A"}]
    assert cp.tables["t"] == [{"id": "A", "org_id": "O"}]

    # 5b. and the match really MUTATES (not just echoes): a changing body
    #     lands on the stored row
    cp = _cp()
    assert cp.query("t", select=["id", "org_id"], method="PATCH", filters=match,
                    json_body={"org_id": "O2"}) == [{"id": "A", "org_id": "O2"}]
    assert cp.tables["t"] == [{"id": "A", "org_id": "O2"}]
