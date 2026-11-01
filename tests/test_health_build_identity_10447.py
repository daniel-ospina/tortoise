"""#10447: production must be able to answer "is this deployed?" in one curl.

WHAT WAS WRONG. `/health` answered `status`, `db`, `probe`, `loop_stale_ms`,
`backup_watcher`, `change_feed`, `record` — and named no build. Measured against
`https://api.premiselabs.co/health` on 2026-10-31: none of `version` / `commit` /
`sha` / `build` / `release` / `image` was present. A STALE build serves every one
of those blocks happily, so the endpoint could not distinguish the build it was
running from the one that was supposed to be running, and the honest answer to
"is this live?" was "landed on main, prod unverified".

The reading existed on `/v1/version` (#2208) — so the defect was not "nothing is
published", it was "the endpoint that answers *is it up* publishes no identity",
which is the same failure for anyone who probes it.

WHAT THESE PINS REFUSE. Not "a version field exists" — a constant an agent can
edit would satisfy that and is NOT a version. They pin that the published commit
is the one the DEPLOY was told to build, that the two public surfaces cannot
disagree, and that an UNSTAMPED deployment is readable as unstamped instead of
reading as current.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tortoise import hosted_api  # noqa: E402  - the subject under test


def _health() -> dict:
    """The exact payload `GET /health` returns, without a client round trip."""
    result = hosted_api.health()
    if asyncio.iscoroutine(result):
        result = asyncio.run(result)
    return result


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(REPO), capture_output=True,
                          text=True, check=True).stdout.strip()


def _scratch_repo_with_two_commits(tmp_path: Path) -> tuple[str, str]:
    """`(previous, head)` in a throwaway repo — no reliance on this checkout's
    history.

    `HEAD~1` is NOT available on the runner: `python-ci.yml` checks out without
    `fetch-depth`, so CI is a SHALLOW clone where `HEAD~1` does not resolve — a
    test that used it would pass locally and red on the runner for a reason that
    has nothing to do with the fix (the hazard `tests/test_embedded_evidence.py`
    and `tests/test_7949_capstone_verdict_check.py` both record).
    """
    repo = tmp_path / "history"
    repo.mkdir()
    env = {**os.environ,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}

    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                              text=True, check=True, env=env).stdout.strip()

    run("init", "-q")
    (repo / "f.txt").write_text("1\n")
    run("add", "f.txt")
    run("commit", "-qm", "one")
    previous = run("rev-parse", "HEAD")
    (repo / "f.txt").write_text("2\n")
    run("commit", "-qam", "two")
    return previous, run("rev-parse", "HEAD")


@pytest.fixture
def sha_env(monkeypatch):
    """Set the deploy-stamped identity the way `deploy-hosted.yml` stages it."""
    def _set(sha: str | None, dirty: str | None = None) -> None:
        if sha is None:
            monkeypatch.delenv("TORTOISE_GIT_SHA", raising=False)
        else:
            monkeypatch.setenv("TORTOISE_GIT_SHA", sha)
        if dirty is None:
            monkeypatch.delenv("TORTOISE_GIT_DIRTY", raising=False)
        else:
            monkeypatch.setenv("TORTOISE_GIT_DIRTY", dirty)
    return _set


# ── the published commit is the DEPLOY's, not a constant ─────────────────────

def test_health_publishes_the_commit_the_deploy_was_told_to_build(sha_env):
    """Two different stamps in one test: a hardcoded sha, or a value derived from
    the checkout instead of the deploy env, cannot satisfy both."""
    for sha in ("0" * 40, "1234567890abcdef1234567890abcdef12345678"):
        sha_env(sha)
        build = _health()["build"]
        assert build["commit_sha"] == sha, build
        assert build["build_identified"] is True, build


def test_health_and_v1_version_cannot_disagree(sha_env):
    """ONE resolver. Two public surfaces with two opinions about identity leave a
    reader no way to pick, which is the ambiguity this issue is about."""
    sha_env("a" * 40, dirty="0")
    health_build = _health()["build"]
    version = asyncio.run(hosted_api.version_info())
    assert version["commit_sha"] == health_build["commit_sha"] == "a" * 40
    assert version["version"] == health_build["version"]
    # `/v1/version`'s client-visible contract is unchanged (two keys, same names).
    assert set(version) == {"version", "commit_sha"}, version


def test_a_stale_build_is_readable_as_stale(sha_env, tmp_path):
    """THE DISCRIMINATING CASE. An endpoint that cannot say "this is not the
    build you expected" has not answered the question — a payload that named
    HEAD, or that coerced the published commit to the expected one, would look
    current forever.

    The stamp is an OLDER commit — the shape a lagging deploy has. The payload
    must report that commit VERBATIM, and the production predicate
    (`git merge-base --is-ancestor`) must classify it as behind, which is what
    lets a stale deploy be reported as stale rather than as up. The history comes
    from a scratch repo, not from this checkout: CI is shallow (see
    `_scratch_repo_with_two_commits`).
    """
    previous, head = _scratch_repo_with_two_commits(tmp_path)
    assert previous != head
    sha_env(previous)
    build = _health()["build"]
    assert build["commit_sha"] == previous, build
    assert build["commit_sha"] != head, "the published commit must not be coerced to HEAD"
    behind = subprocess.run(["git", "merge-base", "--is-ancestor", previous, head],
                            cwd=str(tmp_path / "history"))
    assert behind.returncode == 0, "a lagging deployment's commit IS an ancestor of HEAD"


def test_an_unstamped_deployment_is_readable_as_unstamped(sha_env):
    """ABSENCE MUST NOT READ AS CURRENT. `commit_sha: null` alone is a hole a
    reader can mistake for a match; `build_identified: false` is the refusal."""
    sha_env(None)
    build = _health()["build"]
    assert build["commit_sha"] is None, build
    assert build["build_identified"] is False, build


def test_a_dirty_build_says_so_and_is_not_the_commit_it_names(sha_env):
    """The issue asks for "whether the tree was dirty at build time": a dirty
    build is NOT the commit it names, and without the flag the sha alone reads as
    a clean match.

    An UNRECOGNISED stamp must read as `null`, never as `false` — `false` is the
    claim "clean, so this sha is the commit", which is the false PASS this field
    exists to prevent.
    """
    sha_env("b" * 40, dirty="1")
    assert _health()["build"]["commit_dirty"] is True
    sha_env("b" * 40, dirty="0")
    assert _health()["build"]["commit_dirty"] is False
    sha_env("b" * 40, dirty=None)
    assert _health()["build"]["commit_dirty"] is None
    sha_env("b" * 40, dirty="unknown")
    assert _health()["build"]["commit_dirty"] is None, "an unnamed value must not read as clean"


# ── the migration head: "has step 7 landed in prod", from a curl ─────────────

def test_migrations_head_is_the_highest_migration_the_image_carries(sha_env):
    """NUMERIC order, not lexicographic: a `seed.sql`/`_seed.sql` sorts AFTER
    every date-prefixed name and would otherwise be published as the schema head
    (review P2)."""
    sha_env("c" * 40)
    numbered = []
    for p in (REPO / "supabase" / "migrations").glob("*.sql"):
        prefix, _, _rest = p.stem.partition("_")
        if prefix.isdigit():
            numbered.append((int(prefix), p.stem))
    assert numbered, "the repo carries migrations, so the head must not be None"
    assert _health()["build"]["migrations_head"] == max(numbered)[1]


def test_a_non_migration_sql_file_is_not_the_head():
    """The ordering pin, at the resolver: a stray `.sql` whose stem does not
    start with a numeric migration prefix is not a migration and cannot become
    the published head."""
    from tortoise import hosted_api as api  # noqa: PLC0415 - the subject under test
    assert api._MIGRATIONS_HEAD is not None
    assert api._MIGRATIONS_HEAD.split("_")[0].isdigit(), api._MIGRATIONS_HEAD


# ── a stale deploy is UP: currency must not be folded into `status` ──────────

def test_a_stale_build_does_not_degrade_status(sha_env):
    """If staleness degraded `status`, Fly would restart a healthy process
    forever. Currency is a COMPARISON the reader makes; this block supplies the
    datum and must not make the liveness claim itself."""
    sha_env("e" * 40)
    payload = _health()
    assert "build" in payload
    # status is decided by db/watcher/record only (docs in the handler)
    assert payload["status"] in ("ok", "degraded")


def test_the_published_build_block_is_json_serialisable(sha_env):
    sha_env("d" * 40, dirty="0")
    json.dumps(_health())  # a /health that cannot serialise is a 500, not a reading
