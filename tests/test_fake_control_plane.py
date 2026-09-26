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


# ── #4037: PostgREST `order` grammar fidelity ─────────────────────────────

def _order_fake() -> FakeControlPlane:
    return FakeControlPlane(tables={"events": [
        {"id": "old", "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": "new", "created_at": "2026-03-01T00:00:00+00:00"},
        {"id": "mid", "created_at": "2026-02-01T00:00:00+00:00"},
    ]})


def test_order_legacy_dash_prefix_raises() -> None:
    """#4037: the fake used to ACCEPT ``-created_at`` (``order.lstrip("-")``)
    while PostgREST answers PGRST100 / HTTP 400. That inverted dialect is why
    ``SupabaseAbuseStore``'s invalid order stayed green in CI while 400ing in
    prod (Stage-2 suspension never ran; ``/v1/team/alerts`` was always empty).

    REDs on: reverting the order handling to ``order.lstrip("-")`` /
    ``order.startswith("-")``. This is the SOLE witness for the double —
    under the old dialect every fake-backed integration test still passed."""
    f = _order_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("events", order="-created_at")


def test_order_dot_desc_sorts_newest_first() -> None:
    """The valid ``col.desc`` form must actually order. The old dialect looked
    up a column literally named ``created_at.desc`` → ``None`` → every key
    equal → silent no-op, so this ordering was never exercised.

    REDs on: reverting to the ``lstrip("-")`` dialect (no reorder) or
    dropping the ``.desc`` direction."""
    f = _order_fake()
    rows = f.query("events", select=["id"], order="created_at.desc")
    assert [r["id"] for r in rows] == ["new", "mid", "old"]


def test_order_bare_column_and_dot_asc_ascend() -> None:
    f = _order_fake()
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at")] == ["old", "mid", "new"]
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.asc")] == [
            "old", "mid", "new"]


def test_order_null_placement_follows_postgres_defaults() -> None:
    """Postgres places NULLs LAST on ``asc`` and FIRST on ``desc``; the old
    key (``value or ""``) was the inverse in both directions. Explicit
    ``nullsfirst``/``nullslast`` override."""
    f = FakeControlPlane(tables={"events": [
        {"id": "null", "created_at": None},
        {"id": "a", "created_at": "2026-01-01T00:00:00+00:00"},
    ]})
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at")] == ["a", "null"]
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.desc")] == ["null", "a"]
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.nullsfirst")] == [
            "null", "a"]
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.desc.nullslast")] == [
            "a", "null"]


def test_order_by_a_missing_column_is_refused_like_select_and_filter() -> None:
    """Ordering by an absent column is the SAME PostgREST rejection as the
    ``select``/``filter`` drift (#1001/#302): real PostgREST 400s on an
    undefined column rather than returning the rows unordered. Left accepted,
    it is the #4037 mask again — an invalid order term that 400s in production
    and stays invisible in CI, with a fail-soft consumer reading ``rows[0]``."""
    f = FakeControlPlane(
        tables={"events": [
            {"id": "a", "created_at": "2026-01-01T00:00:00+00:00"}]},
        missing_columns={"events": {"ghost"}})
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("events", select=["id"], order="ghost.desc")
    # Not a blanket refusal: the declared column still orders.
    assert [r["id"] for r in f.query(
        "events", select=["id"], order="created_at.desc")] == ["a"]


def test_order_multi_term_is_stable() -> None:
    """``a.desc,b.asc`` — ties on ``a`` resolve by ``b``, and the relative
    order of ``a``-ties is preserved (successive stable per-term sorts)."""
    f = FakeControlPlane(tables={"events": [
        {"id": "1", "g": "x", "n": 2},
        {"id": "2", "g": "y", "n": 5},
        {"id": "3", "g": "x", "n": 1},
    ]})
    rows = f.query("events", select=["id"], order="g.desc,n.asc")
    assert [r["id"] for r in rows] == ["2", "3", "1"]


@pytest.mark.parametrize("term", ["-x", "a b", "a.desc.desc",
                                  "a.asc.nullslast.desc", "a..b", "a.desc."])
def test_order_unparseable_term_raises(term: str) -> None:
    """#4037: an unparseable order term must RAISE, never silently no-op —
    a permissive fallback is the mask this issue removes.

    REDs on: any permissive fallback (the old ``lstrip("-")`` dialect, or
    "accept if the leftmost segment names a known column")."""
    f = _order_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("events", order=term)


def test_order_validation_is_method_agnostic() -> None:
    """A malformed order 400s regardless of method (PGRST100 is a query-string
    parse error), so the parse must precede the PATCH/DELETE dispatch, exactly
    as the 22P02 uuid check does.

    REDs on: moving the parse into the GET branch only."""
    f = _order_fake()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        f.query("events", method="PATCH", order="-created_at",
                filters=[("id", "eq", "old")], json_body={"id": "old"})


def test_falsy_order_is_accepted_like_the_real_seam() -> None:
    """``None`` and ``""`` are dropped by the real client
    (``supabase_control.query`` guards ``if order:``), so the double must not
    400 on them — that would be a false refusal the wire never sees."""
    f = _order_fake()
    assert len(f.query("events", order=None)) == 3
    assert len(f.query("events", order="")) == 3


@pytest.mark.parametrize("order", ["created_at", "created_at.asc",
                                   "created_at.desc", "deleted_at",
                                   "created_at.nullsfirst",
                                   "created_at.desc.nullslast"])
def test_every_order_form_used_in_the_repo_is_accepted(order: str) -> None:
    """False-refusal guard: every order spelling this repo transmits — and
    the optional modifiers the issue names — must parse. ``supabase_control``
    sends ``created_at`` / ``created_at.asc`` / ``deleted_at``; ``abuse.py``
    and ``hosted_api.py`` send ``created_at.desc``."""
    _order_fake().query("events", order=order)  # must not raise
