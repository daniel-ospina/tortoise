"""Self-host REST surface tests (#525).

Covers /v1/points CRUD, /v1/search, /v1/dream + static-key auth on the
self-host daemon's REST router (auth_mode none + static).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest  # noqa: F401


def _client_for_env(monkeypatch, tmp_path, **env):
    from starlette.testclient import TestClient

    if "TORTOISE_DB_URI" not in env:
        monkeypatch.setenv("TORTOISE_DB_URI", "")  # force embedded
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "rest.db"))

    import importlib

    from tortoise import selfhost
    from tortoise import selfhost_api as _sha

    # #1512: the anchor dict is module-level and survives importlib.reload
    # (reload only re-executes selfhost, not its cached selfhost_api import) —
    # clear it so each test's fresh TORTOISE_DB_PATH gets its own anchor and
    # prior tests' pinned servers are released at session end (not leaked).
    _sha._SELFHOST_KEEPALIVE.clear()
    importlib.reload(selfhost)
    return TestClient(selfhost.app)


class TestPointsCRUD:
    def test_create_and_list(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post("/v1/points", json={"content": "rest test point", "kind": "statement"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["id"]
            assert body["content"] == "rest test point"

            r2 = tc.get("/v1/points")
            assert r2.status_code == 200
            ids = [p["id"] for p in r2.json()]
            assert body["id"] in ids

    def test_get_by_id(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            created = tc.post("/v1/points", json={"content": "by-id", "kind": "statement"}).json()
            r = tc.get(f"/v1/points/{created['id']}")
            assert r.status_code == 200
            assert r.json()["id"] == created["id"]

    def test_missing_id_404(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.get("/v1/points/nonexistent-id")
            assert r.status_code == 404

    def test_invalid_kind_400(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post("/v1/points", json={"content": "x", "kind": "not-a-real-kind"})
            assert r.status_code == 422  # pydantic validator rejects

    def _persisted_props(self, pid: str) -> dict:
        """Read the stored node props for `pid`.

        The selfhost REST read surface (list + by-id) is deliberately
        narrow (``PointResponse`` = id/content/kind/created_at), so it cannot
        show arbitrary props; read them straight from the graph the route
        wrote to (same TORTOISE_DB_PATH).
        """
        from tortoise import selfhost_api as _sha

        sdk = _sha._sdk()
        try:
            return dict(sdk.get_point(pid) or {})
        finally:
            sdk.close()

    def test_confidence_and_author_persist(self, monkeypatch, tmp_path):
        """#4032: same silent-drop class as hosted_api — `confidence` /
        `authoredBy` must be stored, never dropped while the write reports ok."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/v1/points",
                json={
                    "content": "selfhost confidence round trip",
                    "kind": "hypothesis",
                    "confidence": 0.8,
                    "authoredBy": "research-skill",
                },
            )
            assert r.status_code == 200, r.text
            props = self._persisted_props(r.json()["id"])
            assert props.get("confidence") == 0.8, props
            assert props.get("authoredBy") == "research-skill", props

    def test_confidence_zero_persists_not_dropped(self, monkeypatch, tmp_path):
        # 0.0 is FALSY — a truthiness guard at the boundary would drop it.
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/v1/points",
                json={
                    "content": "selfhost zero confidence",
                    "kind": "hypothesis",
                    "confidence": 0.0,
                },
            )
            assert r.status_code == 200, r.text
            props = self._persisted_props(r.json()["id"])
            assert props.get("confidence") == 0.0, props

    def test_out_of_range_confidence_is_rejected_not_dropped(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post(
                "/v1/points",
                json={"content": "selfhost bad confidence", "kind": "statement", "confidence": 5},
            )
            assert r.status_code == 422, r.text


class TestSearch:
    def test_search_finds_point(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            tc.post("/v1/points", json={"content": "unique-searchable-phrase-alpha", "kind": "decision"})
            r = tc.get("/v1/search", params={"q": "unique-searchable-phrase-alpha"})
            assert r.status_code == 200
            hits = [p for p in r.json() if "unique-searchable-phrase-alpha" in p["content"]]
            assert hits
            # FTS result shape (point_kind) must map to the response kind field
            assert hits[0]["kind"] == "decision"


class TestStaticAuth:
    def test_no_key_401(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="rest-secret")
        with tc:
            r = tc.post("/v1/points", json={"content": "x", "kind": "statement"})
            assert r.status_code == 401

    def test_wrong_key_401(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="rest-secret")
        with tc:
            r = tc.post("/v1/points", json={"content": "x", "kind": "statement"},
                        headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401

    def test_correct_key_200(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path, TORTOISE_API_KEY="rest-secret")
        with tc:
            r = tc.post("/v1/points", json={"content": "authed", "kind": "statement"},
                        headers={"Authorization": "Bearer rest-secret"})
            assert r.status_code == 200, r.text
