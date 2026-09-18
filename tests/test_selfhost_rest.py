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

    # HERMETICITY (#3834/#3993): the fleet shell exports
    # TORTOISE_API_URL=https://api.premiselabs.co, and ``sdk.ask()`` delegates
    # to the REMOTE ``_post_ask`` whenever that var is set
    # (``if os.environ.get("TORTOISE_API_URL"): return self._post_ask(...)``
    # in ``sdk.ask``) — so
    # every ask test in this file POSTed REAL requests at PRODUCTION and
    # never resolved its fake reader seam (the ambient shell, not this repo's
    # .env, which carries no such var). Cleared here for the whole file.
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    # The ask-reader cache is module-level and a factory SWAP does not
    # invalidate it, so a reader built by a previous test in this process is
    # silently reused (the C6 class) — a test that installs a hung factory
    # would then never see it. Reset per test.
    from tortoise.sdk import _reset_ask_reader_cache_for_tests
    _reset_ask_reader_cache_for_tests()
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


class TestAsk:
    """Self-host /v1/ask (#1987 Task 9): 200 13-field shape, canonical 400,
    and non-ask paths keep the default error body (path-scoped handler)."""

    def _install_fake_reader(self, monkeypatch, reply="selfhost answer"):
        import tortoise.sdk as sdk_mod
        calls = {"n": 0}

        def _factory():
            class _R:
                last_completion_tokens = 12

                def complete(self, *, system, user):
                    calls["n"] += 1
                    return reply

                def close(self):
                    pass
            return _R()

        monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _factory)
        return calls

    def test_ask_returns_200_shape(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        calls = self._install_fake_reader(monkeypatch)
        with tc:
            r = tc.post("/v1/ask", json={"question": "what is the schedule?"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert set(body) == {"answer", "abstained", "question_type",
                                 "question_date", "evidence", "context_tokens",
                                 "model", "provider", "route", "cost_estimate_usd",
                                 "duration_ms", "retrieval_degraded",
                                 "retrieved_session_ids"}
            assert body["answer"] == "selfhost answer"
            assert calls["n"] == 1

    def test_ask_bound_breach_504_parity(self, monkeypatch, tmp_path):
        """#3834/#3993: the selfhost 504 is as LEGIBLE as the hosted one —
        ``Retry-After`` header + body ``code``/``retry_after``/``message``.

        Parity is the whole point of the ticket (three transports, one
        vocabulary), and the selfhost lane reaches it through DIFFERENT code
        (its own path-scoped handler mirroring the header) — so a hosted-only
        test would not notice the selfhost body regressing to the bare code.
        """
        import time

        import tortoise.quota as quota_mod
        import tortoise.sdk as sdk_mod
        from tortoise.schemas import ASK_BUSY_MESSAGE

        built: list = []

        def _hung_factory():
            built.append(1)

            class _R:
                last_completion_tokens = 0

                def complete(self, *, system, user):
                    time.sleep(5)
                    return "late"

                def close(self):
                    pass
            return _R()

        monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory",
                            _hung_factory)
        monkeypatch.setattr(quota_mod, "_ASK_TIMEOUT_S", 0.5)
        # Below the timeout, else acquire_timeout == 0 cancels a FREE acquire.
        monkeypatch.setattr(quota_mod, "_ASK_EXEC_FLOOR_S", 0.1)
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            t0 = time.monotonic()
            r = tc.post("/v1/ask", json={"question": "q"})
            elapsed = time.monotonic() - t0
        assert r.status_code == 504, r.text
        assert r.headers["Retry-After"] == "2"
        assert r.json() == {"error": {"code": "timeout", "retry_after": 2,
                                      "message": ASK_BUSY_MESSAGE}}
        # The bound really bounded (a hung reader that slept 5s would blow it).
        assert elapsed < 0.5 + 2.5, f"refusal took {elapsed:.2f}s"
        # NON-VACUITY: the injected hung reader was actually REACHED, so a
        # reader-build failure (which maps to 502) cannot masquerade as the 504
        # under test.
        assert built, "the injected reader factory was never called"

    def test_ask_empty_question_400(self, monkeypatch, tmp_path):
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.post("/v1/ask", json={"question": ""})
            assert r.status_code == 400, r.text
            assert r.json() == {"error": {"code": "invalid_question"}}

    def test_unknown_path_keeps_default_body(self, monkeypatch, tmp_path):
        """Non-ask paths keep FastAPI's default {"detail": …} — the
        path-scoped /v1/ask handler never touches them."""
        tc = _client_for_env(monkeypatch, tmp_path)
        with tc:
            r = tc.get("/v1/points/nonexistent-id")
            assert r.status_code == 404
            assert "detail" in r.json()


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
