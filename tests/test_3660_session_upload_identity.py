"""#3660 — transcript-upload surfaces must not resolve identity from a repo
(cwd) ``.tortoise`` file.

Attack: a repository ships ``./.tortoise = {"api_key": ..., "api_url":
"http://attacker.example:9999"}``.  The transcript-upload family resolved with
the permissive ``cwd → global`` order, so the repo both **authorized** and
**redirected** the upload of the full session transcript — on a host with no
env key, the user's own global identity was simply shadowed.

Fix: ``session capture`` / ``session drain`` / ``sessions import`` resolve
through ``_resolve_transmit_config()`` — the user-global config only, the same
posture the per-turn reflex and the session-start digest already had
(#2369 D1.2).  Env is still honoured (#2369 D1.1 co-sources its URL with its
key).  These tests assert the identity that reaches the UPLOADER, so every
"attacker" case FAILS on the permissive resolver and PASSES once the surface
is hardened; the last case pins that the documented env-keyed setup still
transmits.

MUTATIONS THAT RED THESE:
  * change ``_resolve_transmit_config`` back to ``_resolve_config_path()``
    (drop ``global_only=True``) → the repo key/host win again;
  * drop env from the transmitting resolver → the env-keyed case fails.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

from tortoise import __main__ as main

ATTACKER_URL = "http://attacker.example:9999"
LEGIT_URL = "https://legit.example.com"


def _seed_global(home):
    d = home / ".tortoise"
    d.mkdir(parents=True, exist_ok=True)
    (d / "credentials.json").write_text(json.dumps(
        {"api_key": "tt_global", "api_url": LEGIT_URL}))


def _seed_repo_attacker(repo):
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".tortoise").write_text(json.dumps(
        {"api_key": "tt_repo", "api_url": ATTACKER_URL}))


@pytest.fixture
def places(monkeypatch, tmp_path):
    """HOME holds the user-global identity; cwd is a repo that ships its own
    attacker-named ``./.tortoise``.  Both exist, so the resolver's precedence
    is the only variable under test."""
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    _seed_global(home)
    _seed_repo_attacker(repo)
    monkeypatch.chdir(repo)
    return SimpleNamespace(home=home, repo=repo)


def _pi_transcript(tmp_path, turns=4):
    lines = []
    for i in range(turns):
        role = "user" if i % 2 == 0 else "assistant"
        lines.append(json.dumps(
            {"type": "message",
             "message": {"role": role, "content": f"secret turn {i}"}}))
    p = tmp_path / "session.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ── The identity that reaches each uploader ───────────────────────────────


def test_session_capture_uses_global_identity_not_repo_config(places, monkeypatch):
    """`session capture` POSTs the whole transcript — the repo's `.tortoise`
    must not be the identity that reaches the uploader."""
    seen: dict = {}
    monkeypatch.setattr(
        main, "_cmd_session_capture",
        lambda args, api_key, api_url: seen.update(
            key=api_key, url=api_url) or 0)

    rc = main._cmd_session(SimpleNamespace(session_cmd="capture"))

    assert rc == 0
    assert seen["key"] == "tt_global"
    assert seen["url"] == LEGIT_URL


def test_session_drain_uses_global_identity_not_repo_config(places, monkeypatch):
    """`session drain` replays spooled transcripts — same trust boundary."""
    seen: dict = {}
    monkeypatch.setattr(
        main, "_cmd_session_drain",
        lambda api_key, api_url, exclude_session_id=None: seen.update(
            key=api_key, url=api_url) or 0)

    rc = main._cmd_session_drain_best_effort(
        SimpleNamespace(exclude_session_id=None))

    assert rc == 0
    assert seen["key"] == "tt_global"
    assert seen["url"] == LEGIT_URL


def test_sessions_import_posts_transcript_to_global_host(places, tmp_path,
                                                         monkeypatch):
    """End-to-end: the actual POST goes to the user-global host with the
    user-global key — the repo cannot redirect the transcript."""
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")  # #3615 consent gate
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    transcript = _pi_transcript(tmp_path)

    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"session_id": "s-1"}'

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.headers.get("Authorization")
        captured["body"] = json.loads(req.data.decode())
        return _Resp()

    with mock.patch("urllib.request.urlopen", _fake_urlopen):
        rc = main._cmd_sessions_import(SimpleNamespace(
            file=str(transcript), harness="pi", session_id=None))

    assert rc == 0
    assert captured["url"].startswith(LEGIT_URL), (
        "the transcript was POSTed to a repo-chosen host")
    assert captured["auth"] == "Bearer tt_global"
    # and it really was the transcript
    assert any("secret turn" in t["content"]
               for t in captured["body"]["conversation"])


# ── Fail closed: a repo config alone must not authorize an upload ─────────


def test_repo_config_alone_does_not_authorize_capture(places, monkeypatch, capsys):
    """With ONLY a repo `.tortoise` present there is no transmitting identity,
    so `session capture` must refuse (no send), not upload with the repo key."""
    (places.home / ".tortoise" / "credentials.json").unlink()
    called = []
    monkeypatch.setattr(main, "_cmd_session_capture",
                        lambda *a, **k: called.append(a) or 0)

    rc = main._cmd_session(SimpleNamespace(session_cmd="capture"))

    assert rc == 1
    assert called == [], "the repo config authorized a transcript upload"
    assert "never chooses the upload host" in capsys.readouterr().err


def test_repo_config_alone_does_not_authorize_drain(places, monkeypatch):
    """`session drain` is backgrounded and always exits 0, but with only a
    repo config it must file NOTHING — the spool stays local."""
    (places.home / ".tortoise" / "credentials.json").unlink()
    called = []
    monkeypatch.setattr(main, "_cmd_session_drain",
                        lambda *a, **k: called.append(a) or 0)

    rc = main._cmd_session_drain_best_effort(
        SimpleNamespace(exclude_session_id=None))

    assert rc == 0
    assert called == [], "the repo config authorized a spool drain"


def test_repo_config_alone_does_not_authorize_import(places, tmp_path,
                                                     monkeypatch):
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    (places.home / ".tortoise" / "credentials.json").unlink()
    transcript = _pi_transcript(tmp_path)

    with mock.patch("urllib.request.urlopen") as urlopen:
        rc = main._cmd_sessions_import(SimpleNamespace(
            file=str(transcript), harness="pi", session_id=None))

    assert rc == 1
    assert not urlopen.called, "the repo config authorized a transcript upload"


# ── No regression: the documented env-keyed setup still transmits ─────────


def test_env_identity_still_honoured_for_capture(places, monkeypatch):
    """The fix narrows the FILE candidate list, not env: an explicit
    `TORTOISE_API_KEY` + co-sourced `TORTOISE_API_URL` is a single, coherent,
    user-controlled identity and must keep working."""
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_env")
    monkeypatch.setenv("TORTOISE_API_URL", "https://env.example.com")
    seen: dict = {}
    monkeypatch.setattr(
        main, "_cmd_session_capture",
        lambda args, api_key, api_url: seen.update(
            key=api_key, url=api_url) or 0)

    rc = main._cmd_session(SimpleNamespace(session_cmd="capture"))

    assert rc == 0
    assert seen["key"] == "tt_env"
    assert seen["url"] == "https://env.example.com"
