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
    """Set the build identity the way the DEPLOY would.

    `TORTOISE_GIT_SHA` IS staged by `deploy-hosted.yml`
    (`ARGS+=(TORTOISE_GIT_SHA="${GITHUB_SHA}")`). `TORTOISE_GIT_DIRTY` is NOT
    staged by anything yet — see the note beside `_PROCESS_STARTED_AT` in
    `tortoise/hosted_api.py` — so the dirty cases below pin what the payload does
    with a stamp the deploy HANDS it, not a claim that a stamp exists.
    """
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


def test_an_unnamed_commit_stamp_is_not_an_identified_build(sha_env):
    """`build_identified` is a POLARITY ALLOW-LIST, not `sha is not None`.

    `unknown`, `main`, `dev` and a literally unexpanded `${GITHUB_SHA}` are all
    non-empty, and a reader keyed on the old rule was told "we know which commit"
    about a value that cannot be compared to `origin/main`. `commit_sha` stays RAW
    so the reader can see what the deploy actually staged.
    """
    for bad in ("unknown", "main", "dev", "${GITHUB_SHA}", "abc", "XYZ" * 8):
        sha_env(bad)
        build = _health()["build"]
        assert build["build_identified"] is False, (bad, build)
        assert build["commit_sha"] == bad, "the raw stamp must survive for the reader"


def test_a_full_sha_and_a_short_sha_are_both_identified(sha_env):
    for good in ("a" * 40, "b" * 7, "1234567890abcdef1234567890abcdef12345678"):
        sha_env(good)
        assert _health()["build"]["build_identified"] is True, good


# ── the migration head: "has step 7 landed in prod", from a curl ─────────────

def test_migrations_head_is_the_highest_migration_the_image_carries(sha_env):
    r"""The REAL-TREE pin: a resolved head exists and is the highest migration the
    checkout carries. The NUMERIC-ORDERING claim is owned by
    `test_a_non_migration_sql_file_is_not_the_head` — every real name matches
    `^(\d+)_`, so this test alone cannot discriminate ordering (review P2)."""
    sha_env("c" * 40)
    numbered = []
    for p in (REPO / "supabase" / "migrations").glob("*.sql"):
        prefix, _, _rest = p.stem.partition("_")
        if prefix.isdigit():
            numbered.append((int(prefix), p.stem))
    assert numbered, "the repo carries migrations, so the head must not be None"
    assert _health()["build"]["migrations_head"] == max(numbered)[1]


def test_a_non_migration_sql_file_is_not_the_head(monkeypatch, tmp_path):
    r"""THE DISCRIMINATOR for the numeric-ordering fix, on a CONTROLLED directory.

    A pin that re-derives the implementation's own algorithm from the real
    `supabase/migrations` cannot fail when the ordering is reverted — every real
    name matches `\d+_`. This one supplies the file that breaks a lexicographic
    sort (`seed.sql`, `_seed.sql` sort after every digit start) and asserts the
    head anyway.
    """
    from tortoise import hosted_api as api
    from tortoise import record_projection
    d = tmp_path / "migrations"
    d.mkdir()
    for name in ("0001_a.sql", "9999_b.sql", "seed.sql", "_seed.sql"):
        (d / name).write_text("-- x\n")
    monkeypatch.setattr(record_projection, "_migrations_dir", lambda: d)
    monkeypatch.setattr(record_projection, "PACKAGED_MIGRATIONS", tmp_path / "nowhere")
    head, source = api._migrations_head()
    assert head == "9999_b", head
    lexicographic = max(p.stem for p in d.glob("*.sql"))
    assert lexicographic != head, "the pinned fixture must defeat a lexicographic sort"
    assert source == "checkout", source


def test_migrations_source_names_the_population_it_read(monkeypatch, tmp_path):
    """A wheel ships only the record lineage, so its `max` is plausible and
    wrong for "has the schema migration landed". The payload must SAY which
    population it read (review P2)."""
    from tortoise import hosted_api as api
    from tortoise import record_projection
    packaged = tmp_path / "record_migrations"
    packaged.mkdir()
    (packaged / "20261029000001_record_point_cache_tier.sql").write_text("-- x\n")
    monkeypatch.setattr(record_projection, "PACKAGED_MIGRATIONS", packaged)
    monkeypatch.setattr(record_projection, "_migrations_dir", lambda: packaged)
    head, source = api._migrations_head()
    assert head == "20261029000001_record_point_cache_tier", head
    assert source == "package", source


def test_the_migrations_head_is_resolved_once_not_per_request(sha_env, monkeypatch):
    """`/health` must do no I/O per request (#2850's contract, restated in
    `.github/scripts/deploy-health-gate.sh`): the resolver is memoised at import,
    so a burst of checks cannot stat 13 paths and glob 77 files each time.

    THE PIN IS THE BOOBY TRAP (review P3). A counter that nothing installs asserts
    `0 == 0` and would pass even if `_build_identity()` re-resolved per request.
    Replacing the resolver with one that RAISES fails the moment the payload
    calls it, and passes only while the payload reads the constants.
    """
    sha_env("f" * 40)
    from tortoise import hosted_api as api

    assert api._MIGRATIONS_HEAD is not None

    def boom(*_a, **_k):
        raise AssertionError("the migrations head was resolved per request")

    monkeypatch.setattr(api, "_migrations_head", boom)
    first = _health()["build"]
    second = _health()["build"]
    assert first["migrations_head"] == api._MIGRATIONS_HEAD == second["migrations_head"]


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
