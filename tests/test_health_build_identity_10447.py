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


def _git_env() -> dict[str, str]:
    """Environment for the fixture's git calls **and** for the measured one.

    Every `GIT_*` variable is stripped: `GIT_DIR`, `GIT_WORK_TREE` and
    `GIT_INDEX_FILE` would otherwise aim git at a different repository or index
    than `cwd` — and a hook-invoked run (or a lane that exports them) would then
    `git init` / `git commit` into THAT repo instead of `tmp_path/history`.
    Global and system config are redirected to `/dev/null` so the runner's
    `core.autocrlf` / `core.fsmonitor` / `core.excludesFile` cannot decide what
    the pin sees.

    This is the #4203 shape, documented and fixed in
    `tests/test_embedded_evidence.py::_git_env`. It is unrolled here rather than
    imported: importing a test module for a helper drags its whole import cost
    into this file's collection.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    })
    return env


def _stub_health_dependencies(monkeypatch) -> None:
    """Neutralise the three blocks that DECIDE `status` (db / watcher / record).

    The DB verdict is a snapshot from the background refresher (#2850), so a cold
    probe reports `{"ok": False, "error": "probe has not produced a result"}` and
    would degrade `status` for a reason that has nothing to do with the build
    block. Same rationale, same shape, as
    `tests/test_9548_record_arming_health.py::_stub_health_dependencies`.
    """
    verdict = {"ok": True, "latency_ms": 0.1, "error": None}
    monkeypatch.setattr(hosted_api, "_probe_db", lambda: dict(verdict))
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(verdict))
    monkeypatch.setattr(hosted_api, "_WATCHER", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_START_ERROR", None)
    monkeypatch.setattr(hosted_api, "_WATCHER_EXPECTED", False)
    monkeypatch.setattr(
        hosted_api, "_record_health",
        lambda: {"armed": True, "dsn": None, "ok": True, "error": None},
    )


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
    env = {**_git_env(),
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
                            cwd=str(tmp_path / "history"), env=_git_env())
    assert behind.returncode == 0, "a lagging deployment's commit IS an ancestor of HEAD"


def test_the_scratch_history_ignores_an_ambient_git_dir(tmp_path, monkeypatch):
    """NON-VACUITY for `_git_env()` (#4203): the sanitisation must be LOAD-BEARING.

    A decoy repo is placed at the ambient `GIT_DIR` and the fixture is then run.
    With `_git_env()` in place the commits land in `tmp_path/history` and the
    decoy stays empty; without it, git aims at the decoy and this pin fails. The
    decoy is throwaway, so the failure mode can be demonstrated without ever
    being able to point git at a real checkout.
    """
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(decoy), env=_git_env(),
                   check=True, capture_output=True)
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))

    previous, head = _scratch_repo_with_two_commits(tmp_path)
    assert previous != head, "the scratch history must be built despite GIT_DIR"

    decoy_head = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"],
                                cwd=str(decoy), env=_git_env(),
                                capture_output=True, text=True)
    assert decoy_head.returncode != 0, (
        "the ambient GIT_DIR redirected the scratch commits into the decoy — "
        "the fixture is inheriting GIT_* and cannot prove anything about cwd"
    )


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

    THE VOCABULARY IS `tortoise/env_truthy.py`'s, not a local `== "1"` map
    (review P2): `true` is the spelling this fleet's own deploy uses
    (`BACKUP_SWEEP_ENABLED=true`), and a stamped `true` must not read as "not
    claimed". An UNRECOGNISED stamp must read as `null`, never as `false` —
    `false` is the claim "clean, so this sha is the commit", the false PASS this
    field exists to prevent.
    """
    for dirty_true in ("1", "true", "TRUE", "yes", "on", " True "):
        sha_env("b" * 40, dirty=dirty_true)
        build = _health()["build"]
        assert build["commit_dirty"] is True, (dirty_true, build)
        # ...and a dirty build is NOT the commit it names.
        assert build["build_identified"] is False, (dirty_true, build)
    for dirty_false in ("0", "false", "no", "off"):
        sha_env("b" * 40, dirty=dirty_false)
        build = _health()["build"]
        assert build["commit_dirty"] is False, (dirty_false, build)
        assert build["build_identified"] is True, (dirty_false, build)
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
    # The fixture defeats BOTH halves of the docstring's claim (review P2):
    #   * `seed` / `_seed` sort AFTER every digit-prefixed stem — they catch an
    #     implementation that drops the `\d+_` filter;
    #   * `999_b` sorts AFTER `1000_a` as a STRING — it catches an implementation
    #     that keeps the filter but drops `int()`, which the round-2 fixture (all
    #     digit prefixes of equal width) could not distinguish.
    for name in ("0001_first.sql", "999_b.sql", "1000_a.sql", "seed.sql", "_seed.sql"):
        (d / name).write_text("-- x\n")
    monkeypatch.setattr(record_projection, "_migrations_dir", lambda: d)
    monkeypatch.setattr(record_projection, "PACKAGED_MIGRATIONS", tmp_path / "nowhere")
    head, source = api._migrations_head()
    assert head == "1000_a", head
    assert max(p.stem for p in d.glob("*.sql")) == "seed", (
        "the fixture must defeat an implementation with NO `\\d+_` filter"
    )
    assert max(p.stem for p in d.glob("*.sql") if p.stem[:1].isdigit()) == "999_b", (
        "the fixture must defeat an implementation that sorts the filtered stems "
        "as STRINGS (999_b > 1000_a lexicographically)"
    )
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

    THE PIN IS A BOOBY TRAP, NOT A COUNTER (review P3). The round-2 version built a
    counting wrapper and never installed it, so it asserted `0 == 0` under every
    implementation — including the per-request `_migrations_head()[0]` regression
    it is named after. pytest's own rule is that a patch is applied to the name the
    code under test LOOKS UP, before it runs
    (docs.pytest.org `how-to/monkeypatch.html`), so substituting a resolver that
    RAISES fails the moment the payload calls it and passes only while the payload
    reads the module constants.
    """
    sha_env("f" * 40)
    from tortoise import hosted_api as api

    assert api._MIGRATIONS_HEAD is not None, "the real tree carries migrations"

    # The memoised value is a real resolve of the population on disk, so the pin
    # below cannot be satisfied by an import-time constant nobody ever read.
    fresh_head, _source = api._migrations_head()
    assert fresh_head == api._MIGRATIONS_HEAD, (
        "the module constant drifted from a fresh resolve — `/health` would "
        "publish a build identity that is not this image's"
    )

    def boom(*_a, **_k):
        raise AssertionError("the migrations head was resolved per request")

    monkeypatch.setattr(api, "_migrations_head", boom)
    first = _health()["build"]
    second = _health()["build"]
    assert first["migrations_head"] == api._MIGRATIONS_HEAD == second["migrations_head"]


def test_the_published_head_is_documented_as_CARRIED_not_APPLIED():
    """#10467 review P2: the field must not be documented as answering a question
    it cannot answer.

    `_migrations_head` reads the highest migration-shaped file the **image
    carries**. It said — here and in the PR body — that it answers "has migration
    7 landed in prod", which is a DIFFERENT and unanswerable question from inside
    this process: `entrypoint.sh` has no migration step (`supabase-deploy.yml`
    applies the schema) and the deploy's drift gate classes index-only drift as
    WARN, exit 0 (`.github/scripts/check-migration-drift:25-26`), so an
    index-only migration can be carried by an image prod has not applied.
    Publishing carried as applied is a false PASS on the exact question #10447
    exists to make answerable.

    The documentation IS the deliverable of that finding, so the CLAIM is what is
    pinned: the honest reading present, the false one absent, and the applied
    set pointed at its real home instead of implied here. Pre-fix the docstring
    carried `Answers "has migration 7 landed in prod"` and no CARRIES wording, so
    this reds on both halves.
    """
    from tortoise import hosted_api as api

    doc = api._migrations_head.__doc__ or ""
    lowered = doc.lower()
    assert "carries" in lowered, (
        "`_migrations_head` no longer says the head is what the IMAGE CARRIES — "
        "the only reading this process can honestly produce"
    )
    assert "landed in prod" not in lowered, (
        "`_migrations_head` claims 'has migration 7 landed in prod' again — the "
        "applied set is a database fact this endpoint does not read (and must not: "
        "`/health` does no DB work), so the field would be a false PASS on it"
    )
    assert "applied" in lowered and "database" in lowered, (
        "`_migrations_head` does not name the distinction (applied / database) — "
        "a docstring that drops it invites the carried value being read as the "
        "applied one"
    )


# ── a stale deploy is UP: currency must not be folded into `status` ──────────

def test_a_stale_build_does_not_degrade_status(sha_env, monkeypatch):
    """If staleness degraded `status`, Fly would restart a healthy process
    forever. Currency is a COMPARISON the reader makes; this block supplies the
    datum and must not make the liveness claim itself.

    THE ASSERTION MUST BE ABLE TO FAIL (review P2). `status`'s entire codomain is
    `"ok" | "degraded"` (`health()`: `"ok" if (db.ok and watcher.ok and
    record.ok) else "degraded"`), so `status in ("ok", "degraded")` held for
    every reachable payload — including the one the docstring refuses, where a
    stale build published `"degraded"` and Fly restarted the process forever.
    The three blocks that DECIDE `status` are therefore pinned healthy first, and
    the stamp is stale (`e`*40 is not this checkout's HEAD): holding all of that
    fixed, a staleness fold is the only thing left that can make this "degraded".
    """
    sha_env("e" * 40)
    _stub_health_dependencies(monkeypatch)
    payload = _health()
    assert payload["build"]["commit_sha"] == "e" * 40, payload["build"]
    assert payload["status"] == "ok", (
        f"a stale-but-healthy build must not degrade liveness: {payload['status']!r} — "
        "currency is the reader's comparison, not this endpoint's claim"
    )


def test_the_published_build_block_is_json_serialisable(sha_env):
    sha_env("d" * 40, dirty="0")
    payload = _health()
    # The BLOCK, not the whole payload (review P2): `json.dumps(payload)` was
    # satisfied by a payload with NO `build` key at all, so it passed against the
    # unfixed code and proved nothing about the block it is named for.
    assert "build" in payload
    json.dumps(payload["build"])
