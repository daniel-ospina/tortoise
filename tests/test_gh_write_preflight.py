"""Hermetic tests for tools/gh_write_preflight.py (#10951, agent-infra#3453).

No network, no Docker, no FalkorDB, no credential. The "credential" is simulated at the layer that
actually decides it — the REMOTE:

  * a WRITE credential  = a bare repo that accepts the probe push;
  * a READ-ONLY credential = the same bare repo with a `pre-receive` hook that rejects the push with
    the exact message git prints when the server refuses a non-writer
    (`remote: Permission to … denied`).

That is deliberate. Stubbing `git push` to return 0 would prove the script can read its own exit
code; a real push against a real remote proves the push/ls-remote/delete loop the tool depends on.
The cases a working git CANNOT produce (a push that "succeeds" leaving no ref, a delete that fails, an
SSH-key refusal) use a PATH stub — the same technique test_collision_preflight.py uses for its `gh`
surfaces.

Discrimination is asserted, not assumed: each test below names the defect it would catch if the
assertion were weakened. The two the reviewer measured as vacuous (a 20-char prefix leak, and a `repo`
field that is merely slash-bearing) are now exact.

Run standalone:      python3 tests/test_gh_write_preflight.py
Run under pytest:    python3 -m pytest tests/test_gh_write_preflight.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "gh_write_preflight.py"

GIT = shutil.which("git") or "/usr/bin/git"


# ── harness ────────────────────────────────────────────────────────────────────────────────────────


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [GIT, *args], cwd=str(cwd), capture_output=True, text=True, timeout=120, check=False
    )


def _make_remote_and_clone(tmp: Path, *, readonly: bool = False) -> Path:
    """A bare 'origin' plus a clone with one commit. `readonly` installs a rejecting hook."""
    remote = tmp / "origin.git"
    remote.mkdir(parents=True)
    _git("init", "--bare", "-q", str(remote), cwd=tmp)

    clone = tmp / "work"
    clone.mkdir()
    _git("init", "-q", "--initial-branch=feat/lane-branch", cwd=clone)
    _git("config", "user.email", "t@example.invalid", cwd=clone)
    _git("config", "user.name", "Test", cwd=clone)
    (clone / "README.md").write_text("probe\n", encoding="utf-8")
    _git("add", "README.md", cwd=clone)
    _git("commit", "-q", "-m", "init", cwd=clone)
    _git("remote", "add", "origin", str(remote), cwd=clone)

    if readonly:
        hook = remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            "echo 'remote: Permission to daniel-ospina/tortoise.git denied to read-only-sim.' >&2\n"
            "echo 'fatal: unable to access: The requested URL returned error: 403' >&2\n"
            "exit 1\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
    return clone


def _run(workdir: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    run_env = dict(os.environ)
    run_env.pop("GH_TOKEN", None)
    run_env.pop("GITHUB_TOKEN", None)
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, str(TOOL), "--workdir", str(workdir), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=run_env,
    )


def _stub_bin(tmp: Path, git_body: str) -> Path:
    """A PATH dir whose `git` is a stub that otherwise falls through to the real git."""
    bindir = tmp / "stubbin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "git"
    stub.write_text(git_body, encoding="utf-8")
    stub.chmod(0o755)
    return bindir


def _with_path(bindir: Path) -> dict:
    out = dict(os.environ)
    out["PATH"] = f"{bindir}{os.pathsep}{out.get('PATH', '')}"
    out.pop("GH_TOKEN", None)
    out.pop("GITHUB_TOKEN", None)
    return out


def _run_with_path(workdir: Path, bindir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--workdir", str(workdir), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=_with_path(bindir),
    )


def _remote_heads(workdir: Path) -> str:
    return _git("ls-remote", "--heads", "origin", cwd=workdir).stdout


# ── 1. a WRITE credential passes, and the probe ref does not survive ───────────────────────────────


def test_write_credential_passes(tmp_path: Path) -> None:
    """Discriminates a missing/unexercised DELETE: the ref would still be on the real remote."""
    clone = _make_remote_and_clone(tmp_path)
    before = _remote_heads(clone)
    res = _run(clone)
    assert res.returncode == 0, f"expected exit 0, got {res.returncode}: {res.stdout}{res.stderr}"
    assert "GH-WRITE-OK" in res.stdout, res.stdout
    after = _remote_heads(clone)
    assert "gh-write-preflight" not in after, f"probe ref left on the remote:\n{after}"
    assert after.strip() == before.strip(), f"remote refs changed:\nbefore={before!r}\nafter={after!r}"


# ── 2. RED-FIRST: a read-only credential is REFUSED, loudly, before any work ───────────────────────


def test_readonly_credential_refused(tmp_path: Path) -> None:
    """Discriminates a tool that treats any push failure as "not my problem" (or as success)."""
    clone = _make_remote_and_clone(tmp_path, readonly=True)
    res = _run(clone)
    assert res.returncode != 0, f"a read-only credential MUST NOT pass:\n{res.stdout}{res.stderr}"
    combined = res.stdout + res.stderr
    assert "GH-WRITE-UNAVAILABLE(" in combined, combined
    assert "contents-write-denied" in combined, f"reason not named: {combined}"
    assert "denied" in combined.lower(), combined
    assert "gh-write-preflight" not in _remote_heads(clone)


def test_readonly_refusal_happens_before_the_lane_does_work(tmp_path: Path) -> None:
    """A measured refusal is exit 1 and never carries an OK line."""
    clone = _make_remote_and_clone(tmp_path, readonly=True)
    res = _run(clone)
    assert res.returncode == 1, f"a measured refusal is exit 1, got {res.returncode}"
    assert "GH-WRITE-OK" not in res.stdout


# ── 3. no origin at all — and `--repo` cannot substitute for one ──────────────────────────────────


def test_no_remote_refused(tmp_path: Path) -> None:
    repo = tmp_path / "noremote"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@example.invalid", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    _git("add", "f", cwd=repo)
    _git("commit", "-q", "-m", "i", cwd=repo)
    res = _run(repo)
    assert res.returncode != 0
    assert "GH-WRITE-UNAVAILABLE(no-remote)" in res.stdout + res.stderr


def test_repo_argument_without_origin_is_no_remote_not_push_refused(tmp_path: Path) -> None:
    """Discriminates the misclassification measured earlier: an unlistable origin read as a refusal."""
    repo = tmp_path / "noremote2"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@example.invalid", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "f").write_text("x", encoding="utf-8")
    _git("add", "f", cwd=repo)
    _git("commit", "-q", "-m", "i", cwd=repo)
    res = _run(repo, "--repo", "daniel-ospina/tortoise")
    assert "GH-WRITE-UNAVAILABLE(no-remote)" in res.stdout + res.stderr


# ── 4. `--repo` is an ASSERTION — it must never certify a repo the probe did not touch ────────────


def test_repo_mismatch_refuses_instead_of_certifying_another_repo(tmp_path: Path) -> None:
    """THE FALSE-PASS TEST. Pre-fix this returned exit 0 and printed the OTHER repo's slug."""
    clone = _make_remote_and_clone(tmp_path)  # origin is a local bare path, not daniel-ospina/x
    res = _run(clone, "--repo", "daniel-ospina/some-other-repo")
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"a mismatched --repo must not pass: {combined}"
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:repo-mismatch)" in combined, combined
    assert "GH-WRITE-OK" not in combined


# ── 5. push "succeeds" but the ref is not there -> the push was not real, AND nothing is left ──────


def test_ls_remote_mismatch_refused_and_cleaned_up(tmp_path: Path) -> None:
    """Discriminates BOTH a false success and the leftover-ref defect: a mismatch must clean up."""
    clone = _make_remote_and_clone(tmp_path)
    wrong = "d" * 40  # the remote reports a DIFFERENT sha than the one pushed — the mismatch shape
    deletes = tmp_path / "deletes.log"
    gone = tmp_path / "deleted.marker"
    # ls-remote: absent on the pre-check, then a ref carrying a WRONG sha (the mismatch), then —
    # after the delete marker exists — absent again, so the cleanup can succeed and is reported as
    # a mismatch rather than a cleanup failure.
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
N="{tmp_path}/lsremote.count"
case "$*" in
  *"--delete"*) echo "$*" >> "{deletes}"; : > "{gone}"; exit 0 ;;
esac
for a in "$@"; do
  if [ "$a" = "ls-remote" ]; then
    [ -f "{gone}" ] && exit 0
    n=$(cat "$N" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$N"
    [ "$n" -le 1 ] && exit 0
    for r in "$@"; do
      case "$r" in gh-write-preflight-*) printf '%s\trefs/heads/%s\n' "{wrong}" "$r"; exit 0 ;; esac
    done
    exit 0
  fi
done
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, combined
    assert "GH-WRITE-UNAVAILABLE(ls-remote-mismatch" in combined, combined
    # the mismatch path MUST have attempted the delete — this is the leftover-ref defect
    assert deletes.exists(), "the probe ref was left on the remote after a mismatch"
    assert "gh-write-preflight" in deletes.read_text(encoding="utf-8"), deletes.read_text()
    assert "cleanup-failed" not in combined, f"the cleanup succeeded: {combined}"


# ── 5b. push "succeeds" writing NOTHING: a mismatch, never a cleanup failure (round 6) ────────────


def test_push_reports_success_but_nothing_lands_is_a_mismatch_not_a_cleanup_failure(
    tmp_path: Path,
) -> None:
    """⛔ THE WRONG-DIAGNOSIS TEST. Measured pre-fix on a real remote:

        GH-WRITE-UNAVAILABLE(cleanup-failed) — the credential CAN write, but the probe ref
        gh-write-preflight-… could not be removed — delete it by hand …

    for a ref that NEVER EXISTED — because real git ERRORS on `push --delete` of an absent ref
    (`error: unable to delete '…': remote ref does not exist`, rc 1) and `_cleanup` read that exit
    code as a cleanup failure. The reason string a lane greps then names the OPPOSITE of what was
    measured and sends the operator hunting a branch that is provably not there. Nothing landed, so
    there is nothing to delete: the answer is `ls-remote-mismatch`, and the tool must not even attempt
    a delete. The delete here is REAL git (no stub), which is the part the stubbed test could not see.
    """
    clone = _make_remote_and_clone(tmp_path)  # a REAL bare remote
    deletes = tmp_path / "deletes.log"
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
case "$*" in
  *"--delete"*) echo "$*" >> "{deletes}" ;;
esac
for a in "$@"; do
  if [ "$a" = "push" ]; then
    # a push that reports success and writes NOTHING — the shape real git cannot produce
    case "$*" in *"--delete"*) exec {GIT} "$@" ;; esac
    exit 0
  fi
done
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, combined
    assert "GH-WRITE-UNAVAILABLE(ls-remote-mismatch" in combined, combined
    assert "the write did not land" in combined, combined
    assert "cleanup-failed" not in combined, f"nothing landed, so nothing could fail to be removed: {combined}"
    assert "the credential CAN write" not in combined, f"the diagnosis must not claim a write capability: {combined}"
    assert not deletes.exists(), f"no delete should be attempted for a ref that never existed: {deletes.read_text() if deletes.exists() else ''}"
    # and the remote really is clean
    refs = _git("ls-remote", "--heads", "origin", cwd=clone).stdout.strip()
    assert refs == "", f"the remote is not clean: {refs!r}"


def test_cleanup_of_an_absent_ref_is_success(tmp_path: Path) -> None:
    """`_cleanup` must be idempotent — against a real remote, deleting an ABSENT ref FAILS.

    ⛔ THE DELETE MUST ERROR, or this test is vacuous. A **local** bare remote is lenient — measured:
    `remote: warning: deleting a non-existent ref` and **rc 0** — so a fixture built on a local path
    would never produce the shape this test exists for. GitHub (and any receive-pack that refuses a
    delete of a missing ref) errors with `error: unable to delete '<ref>': remote ref does not exist`,
    rc 1. The stub supplies exactly that; `ls-remote` stays REAL, so the confirmation is real.

    Pre-fix, `_cleanup` read that rc as a cleanup failure and the probe reported
    `cleanup-failed` — whose message asserts "the credential CAN write" and tells the operator to
    delete a branch that provably does not exist.
    """
    clone = _make_remote_and_clone(tmp_path)
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
for a in "$@"; do
  if [ "$a" = "push" ]; then
    case "$*" in
      *"--delete"*) echo "error: unable to delete 'x': remote ref does not exist" >&2; exit 1 ;;
    esac
  fi
done
exec {GIT} "$@"
""",
    )
    import importlib

    sys.path.insert(0, str(ROOT / "tools"))
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
    try:
        mod = importlib.import_module("gh_write_preflight")
        detail = mod._cleanup(clone, ["origin"], "gh-write-preflight-never-existed", 30)
    finally:
        os.environ["PATH"] = old_path
        sys.path.pop(0)
    assert detail == "", f"an absent ref is already cleaned up, not a failure: {detail!r}"


# ── 5c. `--timeout` bounds every git call, not only the push ─────────────────────────────────────


def test_timeout_bounds_the_ls_remote_calls_too(tmp_path: Path) -> None:
    """The help says "each git call". It was only applied to the push, so `--timeout 1` still blocked
    for the 120 s default on each of the three `ls-remote` calls.

    ⛔ THE STUB MUST FALL THROUGH TO THE REAL GIT (an f-string; the first cut wrote a literal
    `{GIT}`, so `git --version` failed, the tool answered `cannot-verify:git-missing` in 0.1 s, and
    every assertion here passed without reaching the timeout at all — a vacuous test, caught by the
    mutant for this very arm).
    """
    import time

    clone = _make_remote_and_clone(tmp_path)
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
for a in "$@"; do
  if [ "$a" = "ls-remote" ]; then sleep 5; exit 0; fi
done
exec {GIT} "$@"
""",
    )
    started = time.monotonic()
    res = _run_with_path(clone, bindir, "--timeout", "1")
    elapsed = time.monotonic() - started
    combined = res.stdout + res.stderr
    assert res.returncode != 0, combined
    # the stub really was in the path — without this the run proves nothing about `--timeout`
    assert "git-missing" not in combined, f"the stub broke git, so no bound was exercised: {combined}"
    assert elapsed < 6, f"--timeout 1 did not bound the ls-remote calls ({elapsed:.1f}s)"
    # it must fail open about the UNKNOWN, never as a measured credential refusal
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:ls-remote-failed" in combined, combined


# ── 5d. presence UNMEASURABLE after a push that exited 0 -> cannot-verify, not cleanup-failed ──


def test_unconfirmable_ref_after_a_successful_push_is_cannot_verify_never_cleanup_failed(
    tmp_path: Path,
) -> None:
    """⛔ MEASURED ROUND 7. A push that exits 0 writing nothing, plus a post-push `ls-remote` that
    fails, hit the arm that called `_cleanup` and escalated its "could not confirm" detail into
    `cleanup-failed` — exit 1, i.e. a MEASURED REFUSAL ("a lane must NOT start"), whose message
    asserts **"the credential CAN write"** about a ref that never existed and tells the operator to
    delete it by hand. Presence was never measured on that path, so the honest verdict is exit 2,
    `cannot-verify`, naming the ref and what to check. `_after_push_failure` already worded it that way;
    this arm did not.
    """
    clone = _make_remote_and_clone(tmp_path)
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
N="{tmp_path}/lsremote.count"
for a in "$@"; do
  if [ "$a" = "ls-remote" ]; then
    n=$(cat "$N" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$N"
    # the PRE-check answers ("absent"), every later read FAILS — presence is then unknown
    [ "$n" -le 1 ] && exit 0
    echo "stub: connection reset" >&2
    exit 128
  fi
done
for a in "$@"; do
  if [ "$a" = "push" ]; then
    case "$*" in *"--delete"*) exec {GIT} "$@" ;; esac
    exit 0  # reports success, writes NOTHING
  fi
done
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"an unmeasured presence is 'cannot verify' (exit 2), not a refusal: {combined}"
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:ls-remote-failed" in combined, combined
    assert "cleanup-failed" not in combined, f"presence was never measured here: {combined}"
    assert "the credential CAN write" not in combined, f"nothing measured a write capability: {combined}"
    # it must still tell the operator what to check, and name the ref
    assert "gh-write-preflight" in combined, combined
    assert "check it" in combined, combined


# ── 6. a delete that fails is reported, and the leftover is named ─────────────────────────────────


def test_cleanup_failure_is_reported_and_names_the_leftover(tmp_path: Path) -> None:
    clone = _make_remote_and_clone(tmp_path)
    head = _git("rev-parse", "HEAD", cwd=clone).stdout.strip()
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
N="{tmp_path}/lsremote.count"
case "$*" in
  *"--delete"*) echo "stub: delete refused" >&2; exit 1 ;;
esac
for a in "$@"; do
  if [ "$a" = "ls-remote" ]; then
    n=$(cat "$N" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$N"
    [ "$n" -le 1 ] && exit 0
    for r in "$@"; do
      case "$r" in gh-write-preflight-*) printf '%s\\trefs/heads/%s\\n' "{head}" "$r"; exit 0 ;; esac
    done
    exit 0
  fi
done
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, combined
    assert "GH-WRITE-UNAVAILABLE(cleanup-failed" in combined, combined
    assert "gh-write-preflight" in combined, f"the leftover ref must be named: {combined}"
    assert "delete it by hand" in combined, "the remedy must be spelled out"


# ── 7. secret hygiene, exact ───────────────────────────────────────────────────────────────────────


def test_never_prints_a_token(tmp_path: Path) -> None:
    """Discriminates a FULL leak AND a partial one: no 20+ char run of the token may appear."""
    clone = _make_remote_and_clone(tmp_path)
    fake = "ghp_" + "Z" * 36
    res = _run(clone, env={"GH_TOKEN": fake})
    combined = res.stdout + res.stderr
    assert fake not in combined, "the tool printed the token"
    # every 20-char window of the secret must be absent (a prefix leak is still a leak) — and the
    # range must include the FINAL window, or a leak of only the trailing 20 chars goes undetected
    for i in range(len(fake) - 20 + 1):
        assert fake[i : i + 20] not in combined, f"leaked {fake[i : i + 20]!r}"
    assert res.returncode == 0, combined


def test_origin_userinfo_token_is_never_printed(tmp_path: Path) -> None:
    """A `https://x-access-token:<TOKEN>@host/…` origin is a real shape — it must not be echoed."""
    clone = _make_remote_and_clone(tmp_path)
    secret = "S3CRETTOKENVALUE" + "q" * 24
    _git("remote", "set-url", "origin", f"https://x-access-token:{secret}@127.0.0.1:1/o/n.git", cwd=clone)
    res = _run(clone)
    combined = res.stdout + res.stderr
    assert secret not in combined, "the tool echoed the URL userinfo"
    assert combined.strip(), "the tool must still explain itself"


def test_schemeless_userinfo_with_a_slash_is_scrubbed(tmp_path: Path) -> None:
    """The case ONLY the residual belt can serve: a password containing `/`, with no scheme.

    `_SCHEMELESS_USERINFO` is an `@`-anchored run that cannot span a `/`, and `_USERINFO` needs a
    `://`. Measured to leak before the belt existed, and removing the belt makes this test fail.
    """
    clone = _make_remote_and_clone(tmp_path)
    secret = "pa/ss" + "S3CRETTOKENVALUE" + "q" * 16
    _git(
        "remote",
        "set-url",
        "origin",
        f"x-access-token:{secret}@gitlab.example:o/n.git",
        cwd=clone,
    )
    res = _run(clone)
    combined = res.stdout + res.stderr
    assert "S3CRETTOKENVALUE" not in combined, f"slash-password userinfo leaked: {combined}"
    assert combined.strip(), "the tool must still explain itself"


def test_https_userinfo_output_still_names_the_scheme(tmp_path: Path) -> None:
    """Pins `_USERINFO`'s own contribution: the scheme survives when it can (belt alone drops it)."""
    clone = _make_remote_and_clone(tmp_path)
    secret = "S3CRETTOKENVALUE" + "q" * 24
    _git("remote", "set-url", "origin", f"https://x-access-token:{secret}@127.0.0.1:1/o/n.git", cwd=clone)
    res = _run(clone)
    combined = res.stdout + res.stderr
    assert secret not in combined, combined
    assert "https://" in combined, f"the scheme should survive scrubbing: {combined}"


def test_repo_argument_token_is_never_printed(tmp_path: Path) -> None:
    """The `--repo` argument reaches the detail string — a token in it must be scrubbed."""
    clone = _make_remote_and_clone(tmp_path)
    secret = "S3CRETTOKENVALUE" + "q" * 24
    res = _run(clone, "--repo", f"https://x-access-token:{secret}@github.com/o/n")
    combined = res.stdout + res.stderr
    assert res.returncode == 2, combined
    assert secret not in combined, "the tool echoed the --repo argument's userinfo"


def test_json_payload_is_scrubbed_too(tmp_path: Path) -> None:
    """`--json` is the line a lane logs — the scrubber must not run only on the human lines."""
    clone = _make_remote_and_clone(tmp_path)
    fake = "ghp_" + "Z" * 36
    res = _run(clone, "--json", "--repo", f"o/{fake}")
    combined = res.stdout + res.stderr
    assert fake not in combined, "the JSON payload carried the unscrubbed value"
    for i in range(len(fake) - 20 + 1):
        assert fake[i : i + 20] not in combined, f"leaked {fake[i : i + 20]!r} into --json"


def test_schemeless_userinfo_is_scrubbed(tmp_path: Path) -> None:
    """A scheme-less `user:pass@host:path` remote has no `://` to anchor on — it must still scrub."""
    clone = _make_remote_and_clone(tmp_path)
    secret = "S3CRETTOKENVALUE" + "q" * 24
    _git(
        "remote",
        "set-url",
        "origin",
        f"x-access-token:{secret}@gitlab.example:o/n.git",
        cwd=clone,
    )
    res = _run(clone)
    combined = res.stdout + res.stderr
    assert secret not in combined, f"scheme-less userinfo leaked: {combined}"
    assert combined.strip(), "the tool must still explain itself"


# ── 8. `--quiet` means quiet, and `--json` stays machine-readable ─────────────────────────────────


def test_quiet_prints_nothing_on_success(tmp_path: Path) -> None:
    clone = _make_remote_and_clone(tmp_path)
    res = _run(clone, "--quiet")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout == "", f"--quiet printed {res.stdout!r}"


# ── 9. usage / cannot-verify is its own non-zero code ─────────────────────────────────────────────


def test_cannot_verify_on_bad_repo_argument(tmp_path: Path) -> None:
    clone = _make_remote_and_clone(tmp_path)
    res = _run(clone, "--repo", "not-a-slug")
    combined = res.stdout + res.stderr
    assert res.returncode == 2, combined
    assert "GH-WRITE-UNAVAILABLE(cannot-verify" in combined, combined


def test_no_git_checkout_is_its_own_reason(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    res = _run(plain)
    assert res.returncode != 0
    assert "GH-WRITE-UNAVAILABLE(no-git-checkout)" in res.stdout + res.stderr


# ── 10. reason classification must not over-claim a scope verdict ─────────────────────────────────


def test_publickey_failure_is_not_called_contents_write_denied(tmp_path: Path) -> None:
    """An SSH key failure says nothing about contents scope — misreporting it sends the wrong remedy.

    ⛔ THE MESSAGE IS DELIBERATELY HYBRID (`Permission to … denied (publickey)`): a pure
    `Permission denied (publickey)` already classifies as `push-refused` through the FALLTHROUGH, so it
    would leave the publickey arm itself unpinned (measured as a surviving mutant).
    """
    clone = _make_remote_and_clone(tmp_path)
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
case "$*" in
  *) do_push=0; for a in "$@"; do [ "$a" = "push" ] && do_push=1; done
     case "$*" in *--delete*) do_push=0 ;; esac
     case "$*" in *"push "*|*"push --no-verify origin "*)
       [ "$do_push" = 1 ] && {{ echo "remote: Permission to o/n.git denied (publickey)." >&2; exit 128; }} ;;
     esac ;;
esac
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode == 1, combined
    assert "GH-WRITE-UNAVAILABLE(push-refused)" in combined, combined
    assert "contents-write-denied" not in combined, f"over-claimed scope: {combined}"


# ── 11. the post-push failure FUNNEL — a ref that landed must be deleted, not left ──────────────


def _load_tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gh_write_preflight_under_test", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_post_push_failure_funnel_cleans_a_landed_ref(tmp_path: Path) -> None:
    """REGRESSION: a push that LANDED then raised `TimeoutExpired` used to leave the branch behind.

    `TimeoutExpired` RAISES instead of returning non-zero, so the `returncode != 0` arm never ran and
    the ref stayed on the remote with the verdict reported as a plain refusal — no ref named, no
    remedy, one branch accumulated per attempt. Everything post-push now funnels through
    `_after_push_failure`, which is what this test drives directly (a real 120 s stall is not a test).
    """
    mod = _load_tool()
    clone = _make_remote_and_clone(tmp_path)
    ref = "gh-write-preflight-landed-then-timeout"
    _git("push", "origin", f"HEAD:refs/heads/{ref}", cwd=clone)
    assert ref in _remote_heads(clone), "fixture: the ref must be on the remote first"

    v = mod.Verdict()
    v.ref = ref
    v.repo = "fixture"
    out = mod._after_push_failure(
        v, clone, ["origin"], ref, reason="cannot-verify:timeout", detail="simulated stall"
    )

    assert out.ok is False
    assert out.reason == "cannot-verify:timeout", out.reason
    assert ref not in _remote_heads(clone), "the landed ref was left on the remote"


def test_post_push_failure_funnel_reports_a_failed_cleanup(tmp_path: Path) -> None:
    """If the landed ref cannot be removed, that is `cleanup-failed` and the ref must be NAMED."""
    mod = _load_tool()
    clone = _make_remote_and_clone(tmp_path)
    ref = "gh-write-preflight-landed-unremovable"
    _git("push", "origin", f"HEAD:refs/heads/{ref}", cwd=clone)
    # make the delete impossible: a pre-receive hook is not consulted for deletes, so deny via the
    # remote's own config instead — the stub is simpler and is what the tool sees.
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
case "$*" in
  *"--delete"*) echo "stub: delete refused" >&2; exit 1 ;;
esac
exec {GIT} "$@"
""",
    )
    real_path = os.environ["PATH"]
    os.environ["PATH"] = f"{bindir}{os.pathsep}{real_path}"
    try:
        v = mod.Verdict()
        v.ref = ref
        v.repo = "fixture"
        out = mod._after_push_failure(
            v, clone, ["origin"], ref, reason="cannot-verify:timeout", detail="simulated stall"
        )
    finally:
        os.environ["PATH"] = real_path

    assert out.reason == "cleanup-failed", out.reason
    assert ref in out.detail, out.detail
    assert "delete" in out.detail and ref in out.detail, out.detail


# ── 13. INTEGRATION: the call sites, not just the funnel ───────────────────────────────────────
# The round-3 review measured that a direct unit test of `_after_push_failure` did NOT catch a
# regression at probe()'s three call sites: reverting the timeout arm to a plain `return` left the
# suite green. These tests drive the REAL tool end-to-end against a stub remote, so the call site is
# what fails.


def _stub_that_pushes_then(tmp_path: Path, tail: str, name: str = "stubbin") -> Path:
    """A git stub that performs the REAL push, then runs `tail` (exit 1 / sleep) unconditionally.

    It matches on the ARGUMENTS, not on the remote's spelling: the probe now addresses the resolved
    PUSH URL rather than the name `origin`, so a stub keyed on the literal string `origin` would
    silently stop intercepting \u2014 which is exactly what happened when the target was threaded through.
    """
    bindir = tmp_path / name
    bindir.mkdir(exist_ok=True)
    stub = bindir / "git"
    stub.write_text(
        f"""#!/bin/sh
is_push=0
is_delete=0
for a in "$@"; do
  [ "$a" = "push" ] && is_push=1
  [ "$a" = "--delete" ] && is_delete=1
done
if [ "$is_push" = 1 ] && [ "$is_delete" = 0 ]; then
  {GIT} "$@" || exit $?
  {tail}
  exit 0
fi
exec {GIT} "$@"
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return bindir


def test_push_that_writes_the_ref_then_fails_still_cleans_up(tmp_path: Path) -> None:
    """Post-receive shape: the ref LANDED and the push still exited non-zero. Must not leave it."""
    clone = _make_remote_and_clone(tmp_path)
    before = _remote_heads(clone)
    bindir = _stub_that_pushes_then(tmp_path, "echo 'remote: post-receive rejected' >&2; exit 1")
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, combined
    assert "GH-WRITE-OK" not in combined, combined
    assert "gh-write-preflight" not in _remote_heads(clone), "the landed probe ref was left behind"
    assert _remote_heads(clone).strip() == before.strip()
    assert "cleanup-failed" not in combined, f"the cleanup succeeded: {combined}"


def test_push_that_lands_then_times_out_is_cannot_verify_and_cleans_up(tmp_path: Path) -> None:
    """REGRESSION (round 2, R2-1/R2-2): a timeout RAISES — it must exit 2 and delete the ref."""
    clone = _make_remote_and_clone(tmp_path)
    before = _remote_heads(clone)
    bindir = _stub_that_pushes_then(tmp_path, "sleep 10")
    res = _run_with_path(clone, bindir, "--timeout", "1")
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"a timeout is cannot-verify (2), not a refusal: {combined}"
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:timeout)" in combined, combined
    assert "gh-write-preflight" not in _remote_heads(clone), "the landed ref survived a timeout"
    assert _remote_heads(clone).strip() == before.strip()


def test_delete_that_reports_success_but_removes_nothing_is_cleanup_failed(tmp_path: Path) -> None:
    """REGRESSION (round 3): the post-delete re-check is the ONLY thing between this and a false PASS."""
    clone = _make_remote_and_clone(tmp_path)
    bindir = tmp_path / "stubbin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "git"
    stub.write_text(
        f"""#!/bin/sh
N="{tmp_path}/lsremote.count"
case "$*" in
  *"--delete"*) exit 0 ;;   # claims success, removes nothing
  *"push --no-verify origin HEAD:"*) {GIT} "$@"; exit $? ;;
esac
for a in "$@"; do
  if [ "$a" = "ls-remote" ]; then
    n=$(cat "$N" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$N"
    [ "$n" -le 1 ] && exit 0
    for r in "$@"; do
      case "$r" in
        gh-write-preflight-*)
          sha=$({GIT} rev-parse HEAD)
          printf '%s\\trefs/heads/%s\\n' "$sha" "$r"
          exit 0 ;;
      esac
    done
    exit 0
  fi
done
exec {GIT} "$@"
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"cleanup-failed is cannot-verify (the write was measured): {combined}"
    assert "GH-WRITE-UNAVAILABLE(cleanup-failed)" in combined, combined
    assert "GH-WRITE-OK" not in combined, combined
    assert "gh-write-preflight" in combined, f"the leftover must be named: {combined}"


# ── 14. a token-shaped value flowing through a real path is MASKED (makes the window loop testable) ─


def test_push_stderr_token_is_masked(tmp_path: Path) -> None:
    """A server message that echoes a URL with a token must be scrubbed, not just the URL userinfo."""
    clone = _make_remote_and_clone(tmp_path)
    fake = "ghp_" + "Z" * 36
    bindir = tmp_path / "stubbin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "git"
    stub.write_text(
        f"""#!/bin/sh
do_push=0; for a in "$@"; do [ "$a" = "push" ] && do_push=1; done
case "$*" in *--delete*) do_push=0 ;; esac
if [ "$do_push" = 1 ]; then
  # ⛔ THE TOKEN IS NOT INSIDE URL USERINFO HERE — userinfo is stripped earlier by `_USERINFO`, so a
  # token hidden there would leave `_TOKEN_SHAPED` unpinned (a vacuous test). A bare token-shaped run
  # in the message is the only thing the mask can catch.
  echo "remote: bad credential {fake} rejected" >&2
  exit 1
fi
exec {GIT} "$@"
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode == 1, combined
    assert fake not in combined, "the stderr token was not masked"
    for i in range(len(fake) - 20 + 1):
        assert fake[i : i + 20] not in combined, f"leaked window {fake[i : i + 20]!r}"


# ── 15. a missing toolchain is cannot-verify, never a credential verdict ──────────────────────────


def test_git_missing_is_cannot_verify_not_a_credential_refusal(tmp_path: Path) -> None:
    clone = _make_remote_and_clone(tmp_path)
    empty = tmp_path / "emptybin"
    empty.mkdir()
    # PATH must be ONLY the empty dir — prepending would leave the real git discoverable later.
    env = dict(os.environ)
    env["PATH"] = str(empty)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    res = subprocess.run(
        [sys.executable, str(TOOL), "--workdir", str(clone)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=env,
    )
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"a broken toolchain is not a credential refusal: {combined}"
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:git-missing)" in combined, combined


def test_short_credential_is_never_printed_in_full(tmp_path: Path) -> None:
    clone = _make_remote_and_clone(tmp_path)
    res = _run(clone, env={"GH_TOKEN": "abc"})
    combined = res.stdout + res.stderr
    assert "abc" not in combined, f"a 3-char credential was echoed: {combined}"
    assert "<REDACTED>" in combined, combined


# ── 17. the github.com URL branch, and the reason arms — unit-pinned ─────────────────────────
# Round 4 measured that every fixture built `origin` as a LOCAL PATH, where `_repo_from_url` skips the
# `github.com` branch entirely — so four mutants confined to that branch survived, one of them a FALSE
# PASS (a probe certifying a repo it never touched). These pin it.


def test_repo_from_url_handles_every_real_github_shape() -> None:
    mod = _load_tool()
    cases = {
        "https://github.com/daniel-ospina/tortoise.git": "daniel-ospina/tortoise",
        "https://github.com/daniel-ospina/tortoise": "daniel-ospina/tortoise",
        "https://github.com/daniel-ospina/tortoise/": "daniel-ospina/tortoise",
        "https://github.com/daniel-ospina/tortoise.git/": "daniel-ospina/tortoise",
        "git@github.com:daniel-ospina/tortoise.git": "daniel-ospina/tortoise",
        "git@github.com:daniel-ospina/tortoise": "daniel-ospina/tortoise",
        "ssh://git@github.com/daniel-ospina/tortoise.git": "daniel-ospina/tortoise",
    }
    for url, want in cases.items():
        assert mod._repo_from_url(url) == want, f"{url} -> {mod._repo_from_url(url)!r}, want {want!r}"


def test_repo_assertion_passes_on_a_real_github_origin(tmp_path: Path) -> None:
    """The assertion must NOT refuse a checkout whose origin is a real GitHub URL."""
    clone = _make_remote_and_clone(tmp_path)
    _git("remote", "set-url", "origin", "git@github.com:daniel-ospina/tortoise.git", cwd=clone)
    # a stub so this never touches the network: ls-remote empty, push refused
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
for a in "$@"; do [ "$a" = "ls-remote" ] && exit 0; done
do_push=0; for a in "$@"; do [ "$a" = "push" ] && do_push=1; done
case "$*" in *--delete*) do_push=0 ;; esac
[ "$do_push" = 1 ] && {{ echo "remote: Permission to o/n.git denied" >&2; exit 1; }}
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir, "--repo", "daniel-ospina/tortoise")
    combined = res.stdout + res.stderr
    assert "repo-mismatch" not in combined, f"a matching origin must not be refused: {combined}"
    assert "contents-write-denied" in combined, f"it should have reached the push: {combined}"


def test_repo_assertion_refuses_a_different_github_repo(tmp_path: Path) -> None:
    """THE FALSE-PASS TEST for the github.com branch: never certify a repo the probe did not touch."""
    clone = _make_remote_and_clone(tmp_path)
    _git("remote", "set-url", "origin", "git@github.com:someone-else/OTHER-REPO.git", cwd=clone)
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
for a in "$@"; do [ "$a" = "ls-remote" ] && exit 0; done
do_push=0; for a in "$@"; do [ "$a" = "push" ] && do_push=1; done
case "$*" in *--delete*) do_push=0 ;; esac
[ "$do_push" = 1 ] && exit 0
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir, "--repo", "someone-else/tortoise")
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"a different repo must be refused: {combined}"
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:repo-mismatch)" in combined, combined
    assert "GH-WRITE-OK" not in combined, combined


def test_classify_push_failure_pins_every_arm() -> None:
    """Each arm exists for a real git spelling; an unpinned arm is a wrong remedy page."""
    mod = _load_tool()
    denied = [
        "fatal: unable to access 'https://github.com/o/n.git/': The requested URL returned error: 403",
        "remote: Permission to o/n.git denied to daniel-ospina.",
        "remote: not authorized",
    ]
    for msg in denied:
        assert mod._classify_push_failure(msg) == "contents-write-denied", msg
    not_scope = [
        "git@github.com: Permission denied (publickey).",
        "Permission to o/n.git denied (publickey)",  # publickey wins over the `denied` token
        "Could not resolve host: github.com",
        "fatal: Authentication failed for 'https://github.com/o/n.git/'",
    ]
    for msg in not_scope:
        assert mod._classify_push_failure(msg) == "push-refused", msg


# ── 18. cannot-verify branches that were measured as unpinned ─────────────────────────────────


def test_unlistable_origin_is_cannot_verify_and_pushes_nothing(tmp_path: Path) -> None:
    """A failed `ls-remote` is UNMEASURED — exit 2, and the push must not even be attempted."""
    clone = _make_remote_and_clone(tmp_path)
    sentinel = tmp_path / "push-called"
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
for a in "$@"; do
  if [ "$a" = "ls-remote" ]; then echo "stub: cannot list" >&2; exit 128; fi
done
do_push=0; for a in "$@"; do [ "$a" = "push" ] && do_push=1; done
case "$*" in *--delete*) do_push=0 ;; esac
[ "$do_push" = 1 ] && {{ : > "{sentinel}"; exit 0; }}
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode == 2, combined
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:ls-remote-failed)" in combined, combined
    assert not sentinel.exists(), "the tool pushed on a remote it could not list"


def test_probe_ref_exists_is_cannot_verify_not_a_refusal(tmp_path: Path) -> None:
    """A pre-existing probe ref is a re-run-in-a-second condition, not a credential verdict."""
    mod = _load_tool()
    clone = _make_remote_and_clone(tmp_path)  # needs a real HEAD: the ref check runs after it
    mod._exists_any = lambda _cwd, _t, _ref, _t2=30: ("a" * 40, True)  # type: ignore[assignment]
    v = mod.probe(clone, "o/n", ["origin"])
    assert v.ok is False
    assert v.reason == "cannot-verify:probe-ref-exists", v.reason
    assert v.reason.startswith("cannot-verify"), "this must map to exit 2, not 1"


def test_after_push_failure_names_the_ref_when_it_cannot_confirm(tmp_path: Path) -> None:
    """Losing this branch made a possibly-created ref silently unfindable (measured as a mutant)."""
    mod = _load_tool()
    mod._exists_any = lambda _cwd, _t, _ref, _t2=30: ("", False)  # type: ignore[assignment]
    v = mod.Verdict()
    v.ref = "gh-write-preflight-cannot-confirm"
    out = mod._after_push_failure(
        v, tmp_path, ["origin"], v.ref, reason="push-refused", detail="boom"
    )
    assert out.reason == "push-refused", out.reason
    assert "gh-write-preflight-cannot-confirm" in out.detail, out.detail
    assert "git ls-remote" in out.detail, out.detail


def test_a_pushurl_that_silently_receives_nothing_is_a_mismatch(tmp_path: Path) -> None:
    """\u26d4 The OTHER multi-pushurl shape: the push succeeds, but a target the lane believes it reached
    has nothing. A confirmation that checks only the FIRST target reports `GH-WRITE-OK` for a
    destination that never received the ref, so every target is confirmed.

    With real git a `pre-receive` hook that exits 0 always accepts the update, so this shape is built
    with a git stub: the push is REAL (it exercises every pushurl, as a lane's does) and only the
    `ls-remote` against the SECOND target is made to answer empty.
    """
    clone = _make_remote_and_clone(tmp_path)
    first = tmp_path / "first2.git"
    second = tmp_path / "second2.git"
    _git("init", "--bare", "-q", str(first), cwd=tmp_path)
    _git("init", "--bare", "-q", str(second), cwd=tmp_path)
    _git("remote", "set-url", "origin", str(first), cwd=clone)
    _git("config", "--add", "remote.origin.pushurl", str(first), cwd=clone)
    _git("config", "--add", "remote.origin.pushurl", str(second), cwd=clone)
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
is_remote=0
for a in "$@"; do
  [ "$a" = "ls-remote" ] && is_remote=1
done
if [ "$is_remote" = 1 ]; then
  case "$*" in
    *second2.git*) exit 0 ;;   # this destination reports nothing
  esac
fi
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, f"a target that received nothing is not a pass: {combined}"
    assert "GH-WRITE-OK" not in combined, combined


def test_targets_that_disagree_still_clean_up(tmp_path: Path) -> None:
    """\u26d4 `_confirmed_all` answers "" for TWO states: absent on a target, and PRESENT on every target
    with DISAGREEING shas. The second one has something to delete, and treating it as "nothing landed"
    left the probe ref on every pushurl \u2014 measured round 12: two `LEFTOVER` refs from ONE run, so one
    branch accumulates per run. The arm must ask the presence question before deciding.

    Built with a git stub for the read only: the push is REAL (it goes to every pushurl), and each
    target is then made to report a DIFFERENT sha \u2014 the shape a server-side hook produces when it
    rewrites the ref.
    """
    clone = _make_remote_and_clone(tmp_path)
    first = tmp_path / "first3.git"
    second = tmp_path / "second3.git"
    _git("init", "--bare", "-q", str(first), cwd=tmp_path)
    _git("init", "--bare", "-q", str(second), cwd=tmp_path)
    _git("remote", "set-url", "origin", str(first), cwd=clone)
    _git("config", "--add", "remote.origin.pushurl", str(first), cwd=clone)
    _git("config", "--add", "remote.origin.pushurl", str(second), cwd=clone)
    counter = tmp_path / "lsr.count"
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
is_remote=0
for a in "$@"; do [ "$a" = "ls-remote" ] && is_remote=1; done
if [ "$is_remote" = 1 ]; then
  [ -f "{tmp_path}/gone.marker" ] && exit 0   # after a successful delete nothing is there
  n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "{counter}"
  # the first two reads are the PRE-check: nothing exists yet
  [ "$n" -le 2 ] && exit 0
  case "$*" in
    *first3.git*)  printf '%s\\t%s\\n' "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" "$(for r in "$@"; do case "$r" in gh-write-preflight-*) echo "refs/heads/$r";; esac; done)"; exit 0 ;;
    *second3.git*) printf '%s\\t%s\\n' "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" "$(for r in "$@"; do case "$r" in gh-write-preflight-*) echo "refs/heads/$r";; esac; done)"; exit 0 ;;
  esac
fi
for a in "$@"; do
  if [ "$a" = "push" ]; then
    case "$*" in *"--delete"*) : > "{tmp_path}/gone.marker" ;; esac
  fi
done
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, combined
    assert "ls-remote-mismatch" in combined, combined
    # the delete MUST have been attempted: a ref is present on both targets
    assert "gh-write-preflight" not in _git("ls-remote", "--heads", str(first), cwd=clone).stdout, (
        f"a present ref was left behind: {combined}"
    )
    assert "gh-write-preflight" not in _git("ls-remote", "--heads", str(second), cwd=clone).stdout


def test_a_second_refusing_pushurl_cannot_pass(tmp_path: Path) -> None:
    """\u26d4 FALSE PASS, measured round 11. `remote.origin.pushurl` is MULTI-VALUED: `git push origin`
    writes to every pushurl and fails if any refuses, while `ls-remote` reads one URL at a time. When the
    probe addressed a single resolved URL it reported `GH-WRITE-OK` with a writable first pushurl and a
    refusing second one, while the real push exited 1. The write must go through the NAME `origin` and
    every target must be confirmed separately.
    """
    clone = _make_remote_and_clone(tmp_path)
    first = tmp_path / "first.git"
    second = tmp_path / "second.git"
    _git("init", "--bare", "-q", str(first), cwd=tmp_path)
    _git("init", "--bare", "-q", str(second), cwd=tmp_path)
    hook = second / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'remote: Permission denied' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _git("remote", "set-url", "origin", str(first), cwd=clone)
    _git("config", "--add", "remote.origin.pushurl", str(first), cwd=clone)
    _git("config", "--add", "remote.origin.pushurl", str(second), cwd=clone)

    res = _run(clone)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, f"a refusing pushurl means the lane CANNOT deliver: {combined}"
    assert "GH-WRITE-OK" not in combined, combined
    # ⛔ AND NO LEFTOVER: pushing to one resolved target instead of the name leaves the ref on the
    # target that accepted, because git never reports the refusal and the mismatch arm
    # (correctly) does not delete a ref it believes was never created.
    assert "gh-write-preflight" not in _git("ls-remote", "--heads", str(first), cwd=clone).stdout


# ── 21. round-10 merge-review findings: each one pinned ───────────────────────────────────────


def test_probe_uses_and_labels_the_PUSH_url_not_the_fetch_url(tmp_path: Path) -> None:
    """⛔ MEASURED. `git push origin` writes to `remote.origin.pushurl` when it is set, but the tool read
    `remote get-url origin` (the FETCH url) and addressed every operation by the NAME `origin`. With a
    fork-style pushurl the probe therefore confirmed the wrong repo and labelled `repo=` with the
    upstream one — certifying a repo it never wrote to, the exact class `--repo` exists to prevent.
    The push target is now resolved with `--push` and used for every git operation, so a lane that
    writes to its fork measures the fork.
    """
    clone = _make_remote_and_clone(tmp_path)
    other = tmp_path / "fork.git"
    _git("init", "--bare", "-q", str(other), cwd=tmp_path)
    _git("remote", "set-url", "origin", "https://github.com/daniel-ospina/upstream.git", cwd=clone)
    _git("remote", "set-url", "--push", "origin", str(other), cwd=clone)

    res = _run(clone)
    combined = res.stdout + res.stderr
    assert res.returncode == 0, f"a lane that can push to its fork must pass: {combined}"
    # the label is the checkout's own identity (the fetch url), and the split is stated explicitly
    assert "origin pushes to" in combined, f"the split must be stated, not hidden: {combined}"
    # the write really went to the pushurl target, and to nothing else
    heads = _git("ls-remote", "--heads", str(other), cwd=clone).stdout
    assert heads == "", f"the probe ref was left on the push target: {heads}"
    upstream_refs = _git("ls-remote", "--heads", "origin", cwd=clone).stdout
    assert "gh-write-preflight" not in upstream_refs, upstream_refs


def test_probe_ref_is_unique_across_runs_in_the_same_second(tmp_path: Path) -> None:
    """⛔ MEASURED. The ref was branch-slug + a ONE-SECOND stamp, so two gates in one checkout inside one
    second collided: 30 parallel runs produced 9 false `push-refused` ("reference already exists"), 4
    false `ls-remote-mismatch` (a peer deleted this run's ref), 9 `probe-ref-exists`. A credential that
    writes fine was reported as a measured refusal, and one run deleted a peer's ref. Nothing pinned the
    uniqueness — a mutant that hardcoded the stamp passed the whole suite.
    """
    mod = _load_tool()
    clone = _make_remote_and_clone(tmp_path)
    seen = set()
    for _ in range(6):
        v = mod.probe(clone, "o/n", ["origin"], 30)
        assert v.ref.startswith("gh-write-preflight"), v.ref
        assert v.ref not in seen, f"two probes in the same second shared the ref name {v.ref}"
        seen.add(v.ref)
    assert len(seen) == 6


def test_not_a_git_checkout_is_cannot_verify_not_a_credential_refusal(tmp_path: Path) -> None:
    """\u26d4 A wrong `--workdir` measured NOTHING about the credential, so it must exit 2 with the
    cannot-verify wording. Exit 1 is the "do not start work" bucket and its message says "fix the
    credential", which would send a lane to the wrong repair \u2014 the same mistake that made a missing
    `git` binary a credential verdict.
    """
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    res = _run(plain)
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"a non-checkout is cannot-verify (2), not a refusal: {combined}"
    assert "GH-WRITE-UNAVAILABLE(no-git-checkout)" in combined, combined


def test_cleanup_failure_is_cannot_verify_not_a_measured_refusal(tmp_path: Path) -> None:
    """⛔ The write WAS measured, so a leftover ref is a REPO-STATE problem: exit 2, not the "do not
    start" bucket of exit 1. Reachable on any repo whose ruleset restricts deleting `refs/heads/*`
    — and because each run leaves another probe ref behind, exit 1 would refuse forever."""
    mod = _load_tool()
    v = mod.Verdict()
    v.ref = "gh-write-preflight-leftover"
    out = mod._cleanup_failure(v, "remote refused the delete")
    assert out.reason == "cleanup-failed", out.reason
    assert "the credential CAN write" in out.detail, out.detail


# ── 20. round-5 findings: each one pinned ─────────────────────────────────────────────────────
# Every test here exists because a reviewer measured that the behaviour could be regressed with the
# suite still green. They are grouped so the reason is legible at the assertion.


def test_text_echo_scrubs_the_ref_too(tmp_path: Path) -> None:
    """A BRANCH NAME can carry a token (`ghp_…` survives `_branch_slug`), and it reaches `ref=`."""
    fake = "ghp_" + "Z" * 36
    clone = _make_remote_and_clone(tmp_path)
    _git("checkout", "-q", "-b", f"lane-{fake}", cwd=clone)
    res = _run(clone)
    combined = res.stdout + res.stderr
    assert res.returncode == 0, combined
    assert fake not in combined, f"the ref echo leaked the token: {combined}"
    for i in range(len(fake) - 20 + 1):
        assert fake[i : i + 20] not in combined, f"leaked {fake[i : i + 20]!r}"


def test_non_github_hosts_are_never_reported_as_a_github_slug() -> None:
    """HOST-ANCHORING: a mirror path containing `github.com` must not yield `owner/name`."""
    mod = _load_tool()
    not_github = [
        "https://mygithub.com/daniel-ospina/tortoise.git",
        "https://gitlab.example/mirrors/github.com/daniel-ospina/tortoise.git",
        "/srv/mirrors/github.com/daniel-ospina/tortoise.git",
        "https://notgithub.com/o/n",
    ]
    for url in not_github:
        out = mod._repo_from_url(url)
        assert out != "daniel-ospina/tortoise", f"{url} was reported as a GitHub slug ({out!r})"
        assert out != "o/n", f"{url} was reported as a GitHub slug ({out!r})"
    # …and the real host still works, including the scp form
    assert mod._repo_from_url("git@github.com:daniel-ospina/tortoise.git") == "daniel-ospina/tortoise"


def test_cleanup_that_cannot_be_confirmed_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    """Delete exits 0 but the post-delete `ls-remote` fails — the ref's absence is UNPROVEN."""
    clone = _make_remote_and_clone(tmp_path)
    marker = tmp_path / "deleted"
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    stub = bindir / "git"
    stub.write_text(
        f"""#!/bin/sh
N="{tmp_path}/lsremote.count"
case "$*" in
  *"--delete"*) : > "{marker}"; exit 0 ;;
esac
for a in "$@"; do
  if [ "$a" = "ls-remote" ]; then
    [ -f "{marker}" ] && {{ echo "stub: cannot list" >&2; exit 128; }}
    n=$(cat "$N" 2>/dev/null || echo 0); n=$((n+1)); echo "$n" > "$N"
    [ "$n" -le 1 ] && exit 0
    for r in "$@"; do
      case "$r" in
        gh-write-preflight-*) printf '%s\\trefs/heads/%s\\n' "$({GIT} rev-parse HEAD)" "$r"; exit 0 ;;
      esac
    done
    exit 0
  fi
done
exec {GIT} "$@"
""",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"an unconfirmed delete is cannot-verify (the write was measured): {combined}"
    assert "GH-WRITE-UNAVAILABLE(cleanup-failed)" in combined, combined
    assert "GH-WRITE-OK" not in combined, combined


def test_unborn_head_is_cannot_verify_not_a_credential_refusal(tmp_path: Path) -> None:
    """`rev-parse HEAD` on an unborn branch prints "HEAD" and exits 128 — must not be trusted."""
    repo = tmp_path / "unborn"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("remote", "add", "origin", str(tmp_path / "nowhere.git"), cwd=repo)
    res = _run(repo)
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"no commit is cannot-verify, not a refusal: {combined}"
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:no-head)" in combined, combined
    assert "push-refused" not in combined, combined


def test_every_token_shape_is_masked(tmp_path: Path) -> None:
    """`ghp_` is not the only shape — the fine-grained PAT (`github_pat_`) is the incident's own.

    ⛔ THE TOKEN IS ROUTED THROUGH A PATH THAT ACTUALLY ECHOES IT (a push stderr), not through
    `GH_TOKEN`: the fingerprint only ever shows 4 characters, so setting `GH_TOKEN` exercises the
    FINGERPRINT, not the mask — and a dropped `github_pat_` arm survived that version of this test.
    """
    shapes = [
        "ghp_" + "Z" * 36,
        "gho_" + "Y" * 36,
        "ghu_" + "X" * 36,
        "ghs_" + "W" * 36,
        "github_pat_" + "V" * 50,
    ]
    for n, tok in enumerate(shapes):
        clone = _make_remote_and_clone(tmp_path / f"case{n}")
        bindir = tmp_path / f"case{n}" / "stubbin"
        bindir.mkdir(parents=True, exist_ok=True)
        stub = bindir / "git"
        stub.write_text(
            f"""#!/bin/sh
do_push=0; for a in "$@"; do [ "$a" = "push" ] && do_push=1; done
case "$*" in *--delete*) do_push=0 ;; esac
if [ "$do_push" = 1 ]; then
  echo "remote: bad credential {tok} rejected" >&2
  exit 1
fi
exec {GIT} "$@"
""",
            encoding="utf-8",
        )
        stub.chmod(0o755)
        res = _run_with_path(clone, bindir)
        combined = res.stdout + res.stderr
        assert res.returncode == 1, combined
        assert tok not in combined, f"{tok[:12]}… was printed in full"
        for i in range(len(tok) - 20 + 1):
            assert tok[i : i + 20] not in combined, f"{tok[:12]}… leaked {tok[i : i + 20]!r}"


def test_fingerprint_shows_a_four_char_prefix_and_a_length() -> None:
    """The strongest invariant in the file: pin the prefix WIDTH and the short-value boundary."""
    mod = _load_tool()
    assert mod._credential_fingerprint("ABCDEFGHIJKL") == "ABCD<REDACTED> len=12"
    # short values are never echoed at all
    for short in ("a", "abcd", "abcdefgh"):
        out = mod._credential_fingerprint(short)
        assert short not in out, f"a {len(short)}-char credential was echoed: {out!r}"
        assert "<REDACTED>" in out, out
    assert mod._credential_fingerprint("") == "absent"


def test_gh_token_wins_over_github_token_in_the_diagnostic(tmp_path: Path) -> None:
    """`gh`'s documented precedence — the tool must NAME the variable that actually shadows."""
    clone = _make_remote_and_clone(tmp_path)
    res = _run(clone, env={"GH_TOKEN": "ghp_" + "A" * 36, "GITHUB_TOKEN": "ghp_" + "B" * 36})
    combined = res.stdout + res.stderr
    assert "env:GH_TOKEN=" in combined, combined
    assert "env:GITHUB_TOKEN=" not in combined, combined


def test_repo_assertion_is_case_insensitive(tmp_path: Path) -> None:
    """GitHub slugs are case-insensitive; refusing on case sends a lane to fix a non-problem."""
    clone = _make_remote_and_clone(tmp_path)
    _git("remote", "set-url", "origin", "git@github.com:daniel-ospina/tortoise.git", cwd=clone)
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
for a in "$@"; do [ "$a" = "ls-remote" ] && exit 0; done
do_push=0; for a in "$@"; do [ "$a" = "push" ] && do_push=1; done
case "$*" in *--delete*) do_push=0 ;; esac
[ "$do_push" = 1 ] && {{ echo "remote: Permission to o/n.git denied" >&2; exit 1; }}
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir, "--repo", "Daniel-Ospina/Tortoise")
    combined = res.stdout + res.stderr
    assert "repo-mismatch" not in combined, combined


def test_bare_repo_workdir_is_refused(tmp_path: Path) -> None:
    bare = tmp_path / "bare.git"
    _git("init", "--bare", "-q", str(bare), cwd=tmp_path)
    res = _run(bare)
    combined = res.stdout + res.stderr
    assert res.returncode != 0, combined
    assert "GH-WRITE-UNAVAILABLE(no-git-checkout)" in combined, combined


def test_remote_has_ref_does_not_match_a_suffixed_branch(tmp_path: Path) -> None:
    """`endswith(ref)` would mistake `x-<probe>` for the probe; only the exact ref may match.

    ⛔ THE SECOND DECOY IS THE ONE THAT BIT (measured round 8): `git ls-remote <pattern>` tail-matches,
    so the same pattern also returns `refs/heads/decoy/<probe>` — a `/<ref>` suffix test accepted it.
    Harmless at the pre-check (fails closed as `probe-ref-exists`) but a decoy in the window between the
    pre-check and the post-push confirm produced `cleanup-failed` naming a ref the probe had deleted.
    Both decoys are asserted here so neither can be reintroduced.
    """
    mod = _load_tool()
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
for a in "$@"; do
  if [ "$a" = "ls-remote" ]; then
    printf '%s\\trefs/heads/x-gh-write-preflight-lane\n' "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    printf '%s\\trefs/heads/decoy/gh-write-preflight-lane\n' "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    exit 0
  fi
done
exec {GIT} "$@"
""",
    )
    real = os.environ["PATH"]
    os.environ["PATH"] = f"{bindir}{os.pathsep}{real}"
    try:
        sha, ok = mod._remote_has_ref(tmp_path, "origin", "gh-write-preflight-lane")
    finally:
        os.environ["PATH"] = real
    assert ok is True
    assert sha == "", f"a prefixed or decoy-path branch was mistaken for the probe ref: {sha!r}"


def test_the_probe_does_not_publish_the_lanes_tags(tmp_path: Path) -> None:
    """\u26d4 MEASURED ROUND 15. With `push.followTags=true` configured, a plain push of
    `HEAD:refs/heads/<probe>` ALSO publishes every annotated tag. The run reported `GH-WRITE-OK` and
    "leaves no trace" while leaving `refs/tags/v1` on the shared remote \u2014 and this repo's
    `publish-pypi.yml` / `publish-selfhost.yml` trigger on `push: tags: ['v*']`, so a probe could start
    a release. Pushing a resolved sha does not help; only `-c push.followTags=false` does.
    """
    clone = _make_remote_and_clone(tmp_path)
    _git("config", "push.followTags", "true", cwd=clone)
    _git("-c", "user.email=a@b", "-c", "user.name=a", "tag", "-a", "v1", "-m", "release", cwd=clone)

    res = _run(clone)
    combined = res.stdout + res.stderr
    assert res.returncode == 0, combined
    tags = _git("ls-remote", "--tags", "origin", cwd=clone).stdout
    assert "refs/tags/v1" not in tags, f"the probe published the lane's tags: {tags!r}"
    assert "gh-write-preflight" not in _git("ls-remote", "--heads", "origin", cwd=clone).stdout


def test_a_git_shape_refusal_is_not_a_credential_verdict(tmp_path: Path) -> None:
    """\u26d4 `remote.origin.mirror=true` is decided BEFORE any credential is used, but it was reported as
    `push-refused` \u2014 exit 1, "fix the credential", "a lane must NOT start" \u2014 sending the operator to
    the token page for a repo-config problem. It is `cannot-verify` (exit 2), like the other
    'nothing about the credential was measured' shapes.
    """
    clone = _make_remote_and_clone(tmp_path)
    _git("config", "remote.origin.mirror", "true", cwd=clone)
    res = _run(clone)
    combined = res.stdout + res.stderr
    assert res.returncode == 2, f"a git-shape refusal measures no credential: {combined}"
    assert "GH-WRITE-UNAVAILABLE(cannot-verify:git-push-form)" in combined, combined


def test_a_workflow_scope_refusal_is_named(tmp_path: Path) -> None:
    """A PAT without the `workflow` scope is refused on a branch touching `.github/workflows/*` \u2014 a
    MEASURED refusal, and the incident's own shape for any lane that edits a workflow. It must be named
    as such, not as generic "permission denied".
    """
    mod = _load_tool()
    clone = _make_remote_and_clone(tmp_path)
    bindir = _stub_bin(
        tmp_path,
        f"""#!/bin/sh
do_push=0; for a in "$@"; do [ "$a" = "push" ] && do_push=1; done
case "$*" in *--delete*) do_push=0 ;; esac
if [ "$do_push" = 1 ]; then
  echo "remote: refusing to allow a Personal Access Token to create or update workflow `.github/workflows/x.yml` without `workflow` scope" >&2
  exit 1
fi
exec {GIT} "$@"
""",
    )
    res = _run_with_path(clone, bindir)
    combined = res.stdout + res.stderr
    assert res.returncode == 1, combined
    assert "GH-WRITE-UNAVAILABLE(workflow-scope-denied)" in combined, combined
    # ⛔ BOTH REAL SPELLINGS: GitHub backticks the path AND the word, and the GitHub-App variant says
    # "without `workflows` permission". Matching only the plain spelling missed the dominant message
    # (measured round 16) while the test and its mutant passed.
    for message in (
        "refusing to allow a Personal Access Token to create or update workflow `.github/workflows/x.yml` without `workflow` scope",
        "refusing to allow a GitHub App to create or update workflow `.github/workflows/x.yml` without `workflows` permission",
    ):
        assert mod._classify_push_failure(message) == "workflow-scope-denied", message


def test_userinfo_origin_does_not_break_the_repo_assertion(tmp_path: Path) -> None:
    """Pins the `@`/`//` anchor in `_repo_from_url`: userinfo must not become part of the identity."""
    mod = _load_tool()
    secret = "S3CRETTOKENVALUE" + "q" * 24
    got = mod._repo_from_url(f"https://x-access-token:{secret}@github.com/daniel-ospina/tortoise.git")
    assert got == "daniel-ospina/tortoise", got
    assert secret not in got


# ── 19. the probe ref is derived from the LANE's own branch, and --json is well-formed ────────────


def test_probe_ref_and_json_payload(tmp_path: Path) -> None:
    clone = _make_remote_and_clone(tmp_path)
    res = _run(clone, "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert set(payload) >= {"ok", "repo", "ref", "sha", "credential", "reason"}
    assert payload["ok"] is True
    assert payload["ref"].startswith("gh-write-preflight-"), payload
    assert "lane-branch" in payload["ref"], payload
    # EXACT, not "contains a slash": the pre-fix assertion could not tell a probed target from a label
    assert payload["repo"] == str(tmp_path / "origin.git"), payload
    assert "<REDACTED>" in payload["credential"] or payload["credential"] in {
        "absent",
        "store:unreadable",
    }, payload


if __name__ == "__main__":
    import inspect
    import tempfile

    failures = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        # ⛔ A test that takes no `tmp_path` must not be handed one — calling every test with a fixture
        # made this advertised command print 4 ERRORs on a clean tree, so a lane could not tell signal
        # from noise. Pass the fixture only when the signature asks for it.
        takes_tmp = "tmp_path" in inspect.signature(fn).parameters
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(Path(td)) if takes_tmp else fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:
                failures += 1
                print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{'FAILED' if failures else 'OK'} — {failures} failure(s)")
    sys.exit(1 if failures else 0)
