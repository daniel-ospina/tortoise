"""#7968 / PR #8179 P2 — the org purge must reach the journal's purge flag.

WHY THIS EXISTS. ``purge_org_control_plane`` hard-deletes the ``organizations``
row; the FK cascade ``organizations -> graphs -> journal_event`` fires the
append-only trigger on ``journal_event``, which refuses DELETE unless the
session sets ``app.record_journal_purge = 'on'``. A PostgREST table DELETE
(``cp.query(..., method="DELETE")``) cannot set a session GUC, so the org row
must go through the SECURITY DEFINER RPC ``purge_org_record`` (migration
``20261012000005``) that sets the flag transaction-locally. Before this fix the
flag was never set by any production path: once the journal is populated
(#7969), the erasure would fail closed, ``_purge_deleted_orgs`` would log and
skip, and the org row (the retry anchor) would survive forever.

RED ON THE PRE-FIX CODE. ``purge_org_control_plane`` issued a raw
``cp.query("organizations", method="DELETE")`` and never called the RPC, so
``test_routes_the_org_delete_through_the_rpc`` fails (RPC missing) and
``test_never_issues_a_raw_organizations_delete`` fails (the injected
organizations-DELETE fault fires).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from tests.fake_control_plane import FakeControlPlane  # noqa: E402
from tortoise.supabase_control import purge_org_control_plane  # noqa: E402


def test_routes_the_org_delete_through_the_rpc():
    """The org row is deleted by ``purge_org_record``, whose ``SET LOCAL``
    makes the journal trigger's purge flag true for the cascade."""
    cp = FakeControlPlane()
    cp.seed("organizations", [{"id": "org_1", "name": "n"}])

    purge_org_control_plane(cp, "org_1")

    assert ("purge_org_record", {"p_org_id": "org_1"}) in cp.rpc_calls
    assert all(r["id"] != "org_1" for r in cp.tables.get("organizations", []))


def test_never_issues_a_raw_organizations_delete():
    """A raw organizations table DELETE fires the append-only trigger with the
    flag unset and fails closed once the journal is populated. Prove the purge
    never takes that path by making it fatal if it does."""
    cp = FakeControlPlane()
    cp.seed("organizations", [{"id": "org_1", "name": "n"}])
    cp.fail_query(
        table="organizations", method="DELETE",
        exc=RuntimeError("raw organizations DELETE bypassed purge_org_record"),
    )

    purge_org_control_plane(cp, "org_1")  # must not raise

    assert all(r["id"] != "org_1" for r in cp.tables.get("organizations", []))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
