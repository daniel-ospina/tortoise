"""Cross-tenant scoping for the connector CRUD surface (#2642 re-review P1).

Every connector helper runs on the service-role key, which BYPASSES RLS — so
the ``org_id`` predicate the seam builds is the ONLY tenancy boundary. These
tests pin that boundary at both levels:

* **helper level** (``FakeControlPlane``; no HTTP — runs in every lane): the
  built filter list carries the org predicate, and a foreign org's connector is
  invisible / immutable / undeletable; no returned row carries
  ``credential_enc``.
* **endpoint level** (``TestClient`` + dependency override + fake control
  plane): org B receives **404** (not 200) for org A's connector id on
  GET / PATCH / DELETE, and no response ever carries ``credential_enc``.

The 404 (not 403) choice matches the neighbouring opaque-id endpoints in
``hosted_api.py`` — ``/v1/index/github/{job_id}`` and the invitation
endpoints raise ``404 "not found"`` for a cross-org id, so a foreign id is
indistinguishable from an absent one (no existence oracle).

**Discrimination:** each test names the unfixed form it goes red against.
Reverting the org predicate turns the ``*_is_org_scoped`` /
``foreign_org`` / endpoint tests red; reverting the public select allow-list
turns the ``credential_enc`` tests red.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault(
    "TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tortoise import supabase_control as sc

ORG_A = "org-a-connector"
ORG_B = "org-b-connector"
CONN_A = "aaaaaaaa-0000-0000-0000-00000000000a"
CONN_B = "bbbbbbbb-0000-0000-0000-00000000000b"


def _rows() -> list[dict]:
    return [
        {"id": CONN_A, "org_id": ORG_A, "source_type": "github",
         "config": {"repo": "alpha"}, "sync_status": "idle",
         "credential_enc": "enc-blob-a",
         "created_at": "2026-01-01T00:00:00+00:00"},
        {"id": CONN_B, "org_id": ORG_B, "source_type": "github",
         "config": {"repo": "beta"}, "sync_status": "idle",
         "credential_enc": "enc-blob-b",
         "created_at": "2026-01-02T00:00:00+00:00"},
    ]


def _cp() -> FakeControlPlane:
    return FakeControlPlane(tables={"connectors": _rows()})


class _RecordingCP:
    """Wraps a FakeControlPlane and records each query's (table, kwargs).

    Used to assert the *built* predicate, not merely its observable effect —
    the reviewer's suggested proof that the org term is in the filter list.
    """

    def __init__(self, inner: FakeControlPlane) -> None:
        self.inner = inner
        self.calls: list[tuple[str, dict]] = []

    def query(self, table: str, **kwargs):
        self.calls.append((table, dict(kwargs)))
        return self.inner.query(table, **kwargs)

    def last(self) -> tuple[str, dict]:
        return self.calls[-1]


# ── Helper level — the predicate itself ─────────────────────────────────────

def test_connector_by_id_filters_on_org_and_id() -> None:
    """The built filter list carries BOTH the org term and the id term.

    REDs on: reverting ``filters=[("id", "eq", connector_id)]`` (id-only).
    """
    rec = _RecordingCP(_cp())
    sc.connector_by_id(rec, ORG_A, CONN_A)
    table, kw = rec.last()
    assert table == "connectors"
    assert ("org_id", "eq", ORG_A) in kw["filters"]
    assert ("id", "eq", CONN_A) in kw["filters"]


def test_connector_update_filters_on_org_and_id() -> None:
    """REDs on: reverting the UPDATE helper to an id-only filter."""
    rec = _RecordingCP(_cp())
    sc.connector_update(rec, ORG_A, CONN_A, sync_status="syncing")
    _, kw = rec.last()
    assert ("org_id", "eq", ORG_A) in kw["filters"]
    assert ("id", "eq", CONN_A) in kw["filters"]


def test_connector_delete_filters_on_org_and_id() -> None:
    """Both the existence read AND the DELETE carry the org term.

    REDs on: reverting the DELETE helper to an id-only filter.
    """
    rec = _RecordingCP(_cp())
    sc.connector_delete(rec, ORG_A, CONN_A)
    assert len(rec.calls) == 2
    for table, kw in rec.calls:
        assert table == "connectors"
        assert ("org_id", "eq", ORG_A) in kw["filters"]
        assert ("id", "eq", CONN_A) in kw["filters"]


def test_connector_by_id_foreign_org_returns_none() -> None:
    """Org A reading org B's id → None (the API then answers 404).

    REDs on: an id-only filter, which returns org B's row.
    """
    cp = _cp()
    assert sc.connector_by_id(cp, ORG_A, CONN_B) is None
    assert sc.connector_by_id(cp, ORG_B, CONN_B)["id"] == CONN_B


def test_connector_by_id_excludes_credential_enc() -> None:
    """The row handed to an API caller never contains ``credential_enc``.

    REDs on: dropping the explicit ``select`` (the implicit ``*`` read returns
    the ciphertext).
    """
    rec = _RecordingCP(_cp())
    row = sc.connector_by_id(rec, ORG_A, CONN_A)
    _, kw = rec.last()
    assert "credential_enc" not in kw["select"]
    assert "credential_enc" not in row
    assert row["id"] == CONN_A and row["config"] == {"repo": "alpha"}


def test_connector_by_org_excludes_credential_enc() -> None:
    """The list read is column-projected too (regression lock — it already was).

    REDs on: replacing the explicit select with the implicit ``*``.
    """
    rec = _RecordingCP(_cp())
    rows = sc.connector_by_org(rec, ORG_A)
    _, kw = rec.last()
    assert "credential_enc" not in kw["select"]
    assert rows and all("credential_enc" not in r for r in rows)


def test_connector_update_foreign_org_is_noop_and_false() -> None:
    """A foreign-org PATCH changes nothing and reports False.

    REDs on: an id-only filter, which mutates org B's row and returns True.
    """
    cp = _cp()
    assert sc.connector_update(cp, ORG_A, CONN_B, config={"hacked": True}) is False
    other = next(r for r in cp.tables["connectors"] if r["id"] == CONN_B)
    assert other["config"] == {"repo": "beta"}
    # positive control: the owning org's PATCH applies
    assert sc.connector_update(cp, ORG_A, CONN_A, config={"repo": "alpha2"}) is True
    mine = next(r for r in cp.tables["connectors"] if r["id"] == CONN_A)
    assert mine["config"] == {"repo": "alpha2"}


def test_connector_update_empty_body_reads_existence_org_scoped() -> None:
    """An empty update body reads existence FIRST, scoped to the org.

    PostgREST makes zero updates for ``{}``, so the PATCH representation is
    ``[]`` whether or not the row exists — it cannot establish existence.
    REDs on: inferring existence from ``bool(rows)`` of the PATCH (the
    own-org call returns False), and on dropping the org term from the
    existence read (a foreign id returns True).
    """
    rec = _RecordingCP(_cp())
    assert sc.connector_update(rec, ORG_A, CONN_A) is True
    _, kw = rec.last()
    assert kw.get("method") != "PATCH"
    assert ("org_id", "eq", ORG_A) in kw["filters"]
    assert ("id", "eq", CONN_A) in kw["filters"]

    cp = _cp()
    assert sc.connector_update(cp, ORG_A, CONN_B) is False


def test_connector_delete_foreign_org_is_noop_and_false() -> None:
    """A foreign-org DELETE removes nothing and reports False.

    REDs on: an id-only filter, which deletes org B's connector.
    """
    cp = _cp()
    assert sc.connector_delete(cp, ORG_A, CONN_B) is False
    assert any(r["id"] == CONN_B for r in cp.tables["connectors"])
    # positive control: the owning org's DELETE applies
    assert sc.connector_delete(cp, ORG_A, CONN_A) is True
    remaining = [r["id"] for r in cp.tables["connectors"]]
    assert CONN_A not in remaining and CONN_B in remaining


def test_connector_create_echo_excludes_credential_enc() -> None:
    """The POST echo is column-projected, and the create call is valid.

    REDs on (a): dropping ``select=`` (the echo contains ``credential_enc``).
    REDs on (b): restoring the stale ``headers={"Prefer": ...}`` kwarg, which
    is not a ``query()`` parameter — it is a TypeError, so this line raises.
    """
    cp = _cp()
    row = sc.connector_create(cp, org_id=ORG_A, source_type="slack",
                              config={"channels": ["#general"]})
    assert row is not None
    assert "credential_enc" not in row
    assert row["org_id"] == ORG_A and row["source_type"] == "slack"


def test_connector_sync_sweep_reads_idle_and_error_only() -> None:
    """The system-side sweep stays org-agnostic and returns idle + error.

    Also locks the dialect fix: the previous ``("sync_status", "in", ...)``
    filter is an op the query dialect does not implement (ValueError).
    """
    cp = FakeControlPlane(tables={"connectors": [
        {"id": "c1", "org_id": ORG_A, "sync_status": "idle",
         "credential_enc": "e1"},
        {"id": "c2", "org_id": ORG_B, "sync_status": "error",
         "credential_enc": "e2"},
        {"id": "c3", "org_id": ORG_A, "sync_status": "completed",
         "credential_enc": "e3"},
    ]})
    rows = sc.connector_list_by_sync_eligible(cp)
    ids = sorted(r["id"] for r in rows)
    assert ids == ["c1", "c2"]
    # the sync engine is the ONE intentional credential reader
    assert all("credential_enc" in r for r in rows)


# ── Endpoint level — 404 across tenants, no ciphertext in responses ─────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with a fake control plane and a resolved org-A session.

    No live Supabase: the connector handlers resolve ``is_supabase_enabled`` /
    ``get_control_plane`` lazily from ``tortoise.supabase_control`` at call
    time, so patching the SOURCE module is enough.
    """
    db_path = str(tmp_path / "connectors.db")
    cp = _cp()
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    from tortoise.hosted_api import app, get_current_org_session_ungated
    app.dependency_overrides[get_current_org_session_ungated] = \
        lambda: {"org_id": ORG_A}
    with patched_tortoise_sdk(db_path):
        try:
            with TestClient(app) as tc:
                yield tc, cp
        finally:
            app.dependency_overrides.clear()


def test_get_foreign_connector_404_and_own_ok(client) -> None:
    """Org A GET org B's id → 404; GET its own → 200 without credential_enc.

    REDs on: an id-only read, which answers 200 with org B's row (credential
    ciphertext included).
    """
    tc, _ = client
    foreign = tc.get(f"/v1/connectors/{CONN_B}")
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["detail"] == "Connector not found"

    own = tc.get(f"/v1/connectors/{CONN_A}")
    assert own.status_code == 200, own.text
    body = own.json()["connector"]
    assert body["id"] == CONN_A and body["config"] == {"repo": "alpha"}
    assert "credential_enc" not in body


def test_list_endpoint_returns_own_org_only(client) -> None:
    """The list endpoint is org-scoped and never carries credential_enc."""
    tc, _ = client
    r = tc.get("/v1/connectors")
    assert r.status_code == 200, r.text
    connectors = r.json()["connectors"]
    assert [c["id"] for c in connectors] == [CONN_A]
    assert all("credential_enc" not in c for c in connectors)


def test_patch_foreign_connector_404_and_row_unchanged(client) -> None:
    """Org A PATCH org B's id → 404 and org B's row is untouched.

    REDs on: an id-only filter, which mutates org B and answers 200.
    """
    tc, cp = client
    r = tc.patch(f"/v1/connectors/{CONN_B}", json={"config": {"hacked": True}})
    assert r.status_code == 404, r.text
    other = next(x for x in cp.tables["connectors"] if x["id"] == CONN_B)
    assert other["config"] == {"repo": "beta"}

    ok = tc.patch(f"/v1/connectors/{CONN_A}", json={"config": {"repo": "alpha3"}})
    assert ok.status_code == 200, ok.text
    mine = next(x for x in cp.tables["connectors"] if x["id"] == CONN_A)
    assert mine["config"] == {"repo": "alpha3"}


def test_patch_empty_body_own_connector_200(client) -> None:
    """An empty update body still succeeds for the caller's OWN connector.

    ``{}`` (and the equivalent ``{"config": null}``, which
    ``exclude_none=True`` reduces to ``{}``) makes PostgREST perform zero
    updates and answer ``[]`` under ``return=representation``, so the
    representation cannot establish existence. REDs on: inferring existence
    from ``bool(rows)`` — that 404s the caller's own connector.
    """
    tc, _ = client
    for payload in ({}, {"config": None}):
        r = tc.patch(f"/v1/connectors/{CONN_A}", json=payload)
        assert r.status_code == 200, (payload, r.text)
        assert r.json() == {"status": "updated"}


def test_patch_empty_body_foreign_connector_404(client) -> None:
    """An empty-body PATCH on a FOREIGN id still 404s and touches nothing.

    The existence read is org-scoped, so "nothing to update" never becomes an
    existence oracle. REDs on: dropping the org term from the existence read
    (200 for a foreign id).
    """
    tc, cp = client
    r = tc.patch(f"/v1/connectors/{CONN_B}", json={})
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "Connector not found"
    other = next(x for x in cp.tables["connectors"] if x["id"] == CONN_B)
    assert other["config"] == {"repo": "beta"}


def test_delete_foreign_connector_404_and_row_present(client) -> None:
    """Org A DELETE org B's id → 404 and org B's row survives.

    REDs on: an id-only filter, which deletes org B and answers 200.
    """
    tc, cp = client
    r = tc.delete(f"/v1/connectors/{CONN_B}")
    assert r.status_code == 404, r.text
    assert any(x["id"] == CONN_B for x in cp.tables["connectors"])

    ok = tc.delete(f"/v1/connectors/{CONN_A}")
    assert ok.status_code == 200, ok.text
    remaining = [x["id"] for x in cp.tables["connectors"]]
    assert CONN_A not in remaining and CONN_B in remaining


def test_create_endpoint_returns_no_credential_enc(client) -> None:
    """POST /v1/connectors answers 200 and never echoes the credential column.

    REDs on: the stale ``headers=`` kwarg on ``connector_create`` (TypeError →
    the request never succeeds) or a select-less POST echo.
    """
    tc, cp = client
    r = tc.post("/v1/connectors", json={"source_type": "slack"})
    assert r.status_code == 200, r.text
    body = r.json()["connector"]
    assert body["org_id"] == ORG_A and body["source_type"] == "slack"
    assert "credential_enc" not in body
    assert any(x["source_type"] == "slack" and x["org_id"] == ORG_A
               for x in cp.tables["connectors"])
