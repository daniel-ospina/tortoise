"""Self-host POST /v1/context parity tests (issue #2103 — D1 self-host leg).

The SELF-HOST container entry point (``selfhost.app`` — the real daemon app
factory, NOT the hosted API) serves the same §3.2 contract through the SAME
canonical pipeline (TortoiseSDK.volunteer_context).  These tests boot the
real selfhost ASGI app + router (boundary assertion; a shared-impl shortcut
would be the named anti-pattern) on the embedded lane with static-key auth.

The full Docker-container boot leg (docker build → boot → client) is the
E2E-9 container-boundary assertion; this round ships the mechanism + these
hermetic boundary tests and documents the container leg in the PR body.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")


def _client_for_env(monkeypatch, tmp_path, **env):
    from starlette.testclient import TestClient

    monkeypatch.setenv("TORTOISE_DB_URI", "")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "vc-selfhost.db"))

    import importlib

    from tortoise import selfhost
    from tortoise import selfhost_api as _sha

    _sha._SELFHOST_KEEPALIVE.clear()
    importlib.reload(selfhost)
    return TestClient(selfhost.app)


def _seed(sdk, content: str, counter: str | None = None) -> str:
    proj = sdk._get_proj()
    ev = sdk.create_point("evidence", f"{content} [supporting record]")
    claim = sdk.create_point("statement", content)
    sdk.create_operator("IMPL", ev["id"], [claim["id"]])
    for pid, a, b in ((claim["id"], 2.0, 0.7), (ev["id"], 12.0, 1.0)):
        mean = round(a / (a + b), 4)
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
            "n.posterior_alpha=$a, n.posterior_beta=$b",
            params={"id": pid, "a": a, "b": b, "c": mean})
    if counter:
        c = sdk.create_point("statement", counter)
        sdk.create_operator("NAND", c["id"], [claim["id"]])
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence=0.9, "
            "n.posterior_alpha=10.0, n.posterior_beta=1.0",
            params={"id": c["id"]})
    return claim["id"]


def _seed_via_app(tmp_path, content: str, counter: str | None = None) -> str:
    """Seed via the app's anchored per-request SDK — a second TortoiseSDK
    on the same redislite path would open its own server (last-close-wins on
    the DB file, an inherent race), so seeding shares the app's server."""
    import tortoise.selfhost_api as _sha
    sdk = _sha._sdk()
    try:
        return _seed(sdk, content, counter)
    finally:
        sdk.close()


def test_selfhost_context_contract_and_content(monkeypatch, tmp_path):
    """Boot the REAL selfhost app → POST /v1/context → §3.2 contract shape +
    contested why content (contested flag + variance + nand dig-deeper)."""
    tc = _client_for_env(monkeypatch, tmp_path)
    claim = _seed_via_app(
        tmp_path, "Acme security review was due May 1 and has not shipped",
        "Acme security review shipped on April 30")
    with tc:
        r = tc.post("/v1/context", json={
            "window": [{"role": "user", "content": "What is the status of the "
                                                   "Acme security review?"}],
            "session_id": "sess_sh_acme", "why": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["degraded_reason"] is None
        assert set(body) == {"pointers", "why", "surfaced", "block",
                             "degraded_reason"}
        ids = [p["id"] for p in body["pointers"]]
        assert claim in ids, ids
        entry = next(w for w in body["why"] if w["point_id"] == claim)
        assert entry["ep"]["contested"] is True
        assert entry["ep"]["variance"] > 0.04
        assert "nand" in [d["kind"] for d in entry["dig_deeper"]]
        assert len(body["surfaced"]) == len(body["pointers"])
        assert len(body["block"].encode("utf-8")) <= 8 * 1024


def test_selfhost_clean_empty_and_422(monkeypatch, tmp_path):
    tc = _client_for_env(monkeypatch, tmp_path)
    with tc:
        empty = tc.post("/v1/context", json={"window": []})
        assert empty.status_code == 422
        big = tc.post("/v1/context", json={
            "window": [{"role": "user", "content": "x" * 20_000}]})
        assert big.status_code == 422
        r = tc.post("/v1/context", json={
            "window": [{"role": "user", "content": "What is the weather?"}]})
        assert r.status_code == 200
        assert r.json() == {"pointers": [], "why": [], "surfaced": [],
                            "block": "", "degraded_reason": None}


def test_selfhost_static_key_auth(monkeypatch, tmp_path):
    tc = _client_for_env(monkeypatch, tmp_path,
                         TORTOISE_API_KEY="static-test-key")
    with tc:
        # Static auth mode: no/wrong Bearer → 401.
        missing = tc.post("/v1/context", json={
            "window": [{"role": "user", "content": "hi"}]})
        assert missing.status_code == 401
        wrong = tc.post("/v1/context", json={
            "window": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401
        ok = tc.post("/v1/context", json={
            "window": [{"role": "user", "content": "What did Alice say?"}]},
            headers={"Authorization": "Bearer static-test-key"})
        assert ok.status_code == 200
        assert ok.json()["degraded_reason"] is None


def test_selfhost_fail_open_never_503(monkeypatch, tmp_path):
    """A retrieval/assembly failure inside the selfhost pipeline degrades to
    200 + degraded_reason — never a 503 on the zero-LLM read path."""
    from tortoise.sdk import TortoiseSDK

    tc = _client_for_env(monkeypatch, tmp_path)
    _seed_via_app(tmp_path, "Orion migration plan is on track")
    orig = TortoiseSDK.volunteer_context

    def _boom(self, window, *a, **k):
        raise RuntimeError("simulated failure")

    TortoiseSDK.volunteer_context = _boom
    try:
        with tc:
            r = tc.post("/v1/context", json={
                "window": [{"role": "user", "content": "Orion migration "
                                                       "status?"}]})
            assert r.status_code == 200, r.text
            assert r.json()["degraded_reason"] == "assembly_error"
    finally:
        TortoiseSDK.volunteer_context = orig
