"""Hermetic, RED-FIRST tests for tools/gh_write_preflight.py.

No network, no Docker, no FalkorDB, no GitHub. Each test builds a real temporary git repo with a
real bare remote, so the probe exercises the genuine `git push` path — the same path a lane's
deliverable uses. The "read-only credential" case is simulated the way a read-only credential
actually manifests to git: the remote REFUSES the write. A `pre-receive` hook that prints
``remote: Permission … denied`` and exits non-zero produces the same observable shape (non-zero
exit, no ref created) as GitHub returning 403 for a token without ``contents: write``. It is not a
byte-for-byte reproduction of GitHub's auth handshake, and this docstring does not claim it is.

WHY RED-FIRST MATTERS HERE. This tool's entire value is that it says NO when the credential cannot
write. A suite that only proves it says YES would pass on a tool that always returns 0 — precisely
the fail-open it replaces (`gh api … .permissions.push` reads `true` for a read-only fine-grained
PAT because that field describes the USER's repo role, not the token's grant). So the refusal cases
are the load-bearing ones and are asserted first.

Run standalone:      python3 tests/test_gh_write_preflight.py
Run under pytest:    python3 -m pytest tests/test_gh_write_preflight.py -q

The bare `python3 tests/test_gh_write_preflight.py` form works as written: the `__main__` block sets
`TORTOISE_TEST_CARVE_OUT=1` itself, because the run still loads `tests/conftest.py`, whose
session-scoped autouse `_p4_uri_required` fixture (`tests/_embedded.py`) otherwise fails the session
with "default pytest requires TORTOISE_DB_URI (epic #1647 P4)" — this suite opens no database, so
it is exactly the carve-out case (same one-block fix, same reasoning, as
tests/test_closes_landing.py's `__main__`). Under a bare `-m pytest` invocation the carve-out is the
caller's to set, and a lane container works too
(`uv run python tools/test_lane.py run -- tests/test_gh_write_preflight.py -q`). Pinned by
`test_documented_standalone_command_actually_runs`.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.gh_write_preflight import (
    EXIT_UNAVAILABLE,
    EXIT_WRITABLE,
    REASONS,
    Proc,
    Runner,
    _child_env,
    _insteadof_env,
    _neutral_push_env,
    _read_env,
    _read_targets,
    _ref_state,
    _url_has_credential,
    check_writable,
    main,
    redact,
)

TOOL = Path(__file__).resolve().parent.parent / "tools" / "gh_write_preflight.py"
_GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "HOME": "/tmp",
}
_PROBE = "gh-write-preflight"


# ── fixtures ────────────────────────────────────────────────────────────────────────────────────
def _git(*argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *argv],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=True,
        env=_GIT_ENV,
    )


def _make_pair(tmp_path: Path, *, read_only: bool = False, noisy: bool = False) -> tuple[Path, Path]:
    """Create <tmp>/work (a clone with one commit) and <tmp>/remote.git (bare origin)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    _git("init", "--bare", "-q", str(remote), cwd=tmp_path)
    _git("init", "-q", str(work), cwd=tmp_path)
    (work / "f.txt").write_text("hello\n")
    _git("add", "f.txt", cwd=work)
    _git("commit", "-q", "-m", "init", cwd=work)
    _git("remote", "add", "origin", str(remote), cwd=work)
    _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=work)

    if read_only:
        hook = remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            'echo "remote: Permission to daniel-ospina/tortoise.git denied to lane." >&2\n'
            'echo "remote: The requested token lacks contents: write." >&2\n'
            "exit 1\n"
        )
        hook.chmod(0o755)

    if noisy:
        # A remote that ACCEPTS the push but prints a lot of advisory output first — the shape that
        # pushed the porcelain ref line out of the last-800-char window `step()` keeps.
        hook = remote / "hooks" / "pre-receive"
        hook.write_text(
            "#!/bin/sh\n"
            + "".join(
                f'echo "remote: advisory line {i} ' + "x" * 40 + '" >&2\n'
                for i in range(40)
            )
            + "exit 0\n"
        )
        hook.chmod(0o755)

    return work, remote


def _remote_refs(remote: Path) -> str:
    return _git("ls-remote", "--heads", str(remote), cwd=remote.parent).stdout


# ══ (1) RED: a credential that cannot write MUST be refused ════════════════════════════════════
def test_read_only_credential_is_refused(tmp_path: Path):
    """THE load-bearing case: a rejecting remote must produce a refusal, not a pass."""
    work, _remote = _make_pair(tmp_path, read_only=True)

    verdict = check_writable(work)

    assert verdict.writable is False, "a read-only credential MUST NOT report writable"
    assert verdict.reason == "push-refused"
    assert verdict.cleaned_up is True, "nothing was created, so nothing needed deleting"


def test_read_only_exits_nonzero_with_the_stable_marker(tmp_path: Path, capsys):
    """The CLI contract a lane's preamble keys on: exit 1 + GH-WRITE-UNAVAILABLE(<reason>)."""
    work, _remote = _make_pair(tmp_path, read_only=True)

    rc = main(["--repo", str(work)])

    cap = capsys.readouterr()          # captured ONCE — readouterr() clears on read
    assert rc == EXIT_UNAVAILABLE == 1
    assert "GH-WRITE-UNAVAILABLE(push-refused)" in cap.err
    assert "GH-WRITE-OK" not in cap.out, "a refusal must never print the OK marker"


def test_read_only_leaves_no_ref_on_the_remote(tmp_path: Path):
    """A refused push must not leave a probe ref behind — and the push must really be attempted."""
    work, remote = _make_pair(tmp_path, read_only=True)
    before = _remote_refs(remote)

    verdict = check_writable(work)

    assert _remote_refs(remote) == before
    assert _PROBE + "/" not in _remote_refs(remote)
    # Discriminating power: a tool that pushed NOTHING would also leave no ref, so pin that the
    # push was actually attempted and actually failed.
    push = [s for s in verdict.steps if s["step"] == "push"]
    assert push and push[0]["rc"] != 0, "the refusal must come from an attempted, failed push"


# ══ (2) GREEN: a write-capable credential MUST pass, and clean up ══════════════════════════════
def test_write_credential_passes(tmp_path: Path):
    work, _remote = _make_pair(tmp_path)

    verdict = check_writable(work)

    assert verdict.writable is True
    assert verdict.reason is None
    assert any(s["step"] == "ls-remote-verify" and s["rc"] == 0 for s in verdict.steps)
    assert verdict.cleaned_up is True


def test_write_exits_zero_with_the_ok_marker(tmp_path: Path, capsys):
    work, _remote = _make_pair(tmp_path)

    rc = main(["--repo", str(work)])

    cap = capsys.readouterr()
    assert rc == EXIT_WRITABLE == 0
    assert "GH-WRITE-OK(origin)" in cap.out
    assert "deleted" in cap.out


def test_write_probe_ref_is_deleted_from_the_remote(tmp_path: Path):
    """The pass must not litter the remote: after the run, no probe ref exists."""
    work, remote = _make_pair(tmp_path)

    verdict = check_writable(work)

    assert verdict.writable is True
    refs = _remote_refs(remote)
    assert _PROBE + "/" not in refs, f"probe ref survived: {refs}"
    assert "refs/heads/main" in refs, "the real branch must be untouched"


def test_write_credential_does_not_need_a_new_commit(tmp_path: Path):
    """The probe pushes the EXISTING HEAD, so it is safe in a dirty worktree."""
    work, _remote = _make_pair(tmp_path)
    head_before = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    (work / "dirty.txt").write_text("uncommitted\n")

    verdict = check_writable(work)

    assert verdict.writable is True
    assert _git("rev-parse", "HEAD", cwd=work).stdout.strip() == head_before
    assert verdict.head_sha == head_before
    assert "dirty.txt" in _git("status", "--porcelain", cwd=work).stdout


# ══ (3) THE FALSE-PASS REGRESSION: GIT_DIR must not redirect the probe ═════════════════════════
def test_git_dir_cannot_redirect_the_probe(tmp_path: Path, monkeypatch):
    """A caller's `GIT_DIR` must not make a read-only repo look writable.

    Measured defect: with `GIT_DIR` exported (git exports it inside hooks — and a pre-push gate is
    the natural integration point), `--repo <read-only>` reported `writable=True` while
    `repo_root` and `remote_url` named DIFFERENT repos.
    """
    ro_work, _ro_remote = _make_pair(tmp_path / "ro", read_only=True)
    good_work, _good_remote = _make_pair(tmp_path / "good")

    monkeypatch.setenv("GIT_DIR", str(good_work / ".git"))

    verdict = check_writable(ro_work)

    assert verdict.writable is False, "GIT_DIR redirected the probe — false PASS"
    assert verdict.reason == "push-refused"
    assert str(verdict.repo_root).endswith("ro/work")
    assert "ro/remote.git" in (verdict.remote_url or "")


def test_git_work_tree_cannot_redirect_the_probe(tmp_path: Path, monkeypatch):
    """The sibling of the GIT_DIR regression: GIT_WORK_TREE must not redirect either."""
    ro_work, _ro_remote = _make_pair(tmp_path / "ro", read_only=True)
    good_work, _good_remote = _make_pair(tmp_path / "good")

    monkeypatch.setenv("GIT_WORK_TREE", str(good_work))

    verdict = check_writable(ro_work)

    assert verdict.writable is False
    assert str(verdict.repo_root).endswith("ro/work")


# ══ (4) the env contract: strip REDIRECTION and TRACE, keep CREDENTIAL-CARRYING config ═════════
def test_child_env_strips_redirectors_and_traces_but_keeps_credential_config(monkeypatch):
    """Over-stripping `GIT_*` would probe a DIFFERENT credential path than a lane's real push.

    That is a false block — a lane parks work it could have landed — which is this tool's purpose
    inverted. `GIT_CONFIG_*` and `GIT_SSH_COMMAND` carry the credential on such a box and must
    survive; the repository redirectors and the trace variables must not.
    """
    monkeypatch.setenv("GIT_DIR", "/somewhere/else/.git")
    monkeypatch.setenv("GIT_WORK_TREE", "/somewhere/else")
    monkeypatch.setenv("GIT_INDEX_FILE", "/tmp/idx")
    monkeypatch.setenv("GIT_CURL_VERBOSE", "1")
    monkeypatch.setenv("GIT_TRACE", "1")
    monkeypatch.setenv("GIT_TRACE_PACKET", "1")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "http.extraheader")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Authorization: Basic abc")
    monkeypatch.setenv("GIT_SSH_COMMAND", "ssh -i /tmp/key")

    env = _child_env()

    for stripped in (
        "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CURL_VERBOSE",
        "GIT_TRACE", "GIT_TRACE_PACKET",
    ):
        assert stripped not in env, f"{stripped} must be stripped"
    for kept in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "GIT_SSH_COMMAND"):
        assert kept in env, f"{kept} must survive (it carries the credential)"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_child_env_keeps_a_supplied_askpass(monkeypatch):
    """A box that supplies an askpass helper must not have it replaced."""
    monkeypatch.setenv("GIT_ASKPASS", "/opt/askpass-helper")

    assert _child_env()["GIT_ASKPASS"] == "/opt/askpass-helper"


# ══ (5) distinct refusals for distinct causes (network != credential != bad path) ══════════════
def test_missing_remote_is_named_not_guessed(tmp_path: Path):
    work, _remote = _make_pair(tmp_path)

    verdict = check_writable(work, remote="nope")

    assert verdict.writable is False
    assert verdict.reason == "no-remote"


def test_not_a_repo_is_named(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()

    verdict = check_writable(plain)

    assert verdict.writable is False
    assert verdict.reason == "not-a-repo"


def test_nonexistent_path_is_not_a_repo_not_git_missing(tmp_path: Path):
    """A bad `--repo` must not be misreported as 'git is absent'."""
    verdict = check_writable(tmp_path / "does-not-exist")

    assert verdict.writable is False
    assert verdict.reason == "not-a-repo"
    assert "not found" not in verdict.detail.lower()


def test_regular_file_as_repo_exits_with_marker_not_traceback(tmp_path: Path, capsys):
    """A file as `--repo` used to raise NotADirectoryError: a traceback with no marker."""
    f = tmp_path / "afile.txt"
    f.write_text("x")

    rc = main(["--repo", str(f)])

    cap = capsys.readouterr()
    assert rc == EXIT_UNAVAILABLE
    assert "GH-WRITE-UNAVAILABLE(not-a-repo)" in cap.err
    assert "Traceback" not in cap.err


def test_empty_repo_without_head_is_named(tmp_path: Path):
    work = tmp_path / "empty"
    _git("init", "-q", str(work), cwd=tmp_path)
    _git("remote", "add", "origin", str(tmp_path / "nowhere.git"), cwd=work)

    verdict = check_writable(work)

    assert verdict.writable is False
    assert verdict.reason == "no-head"


def test_git_absent_is_named(tmp_path: Path):
    work, _remote = _make_pair(tmp_path)

    class NoGit(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[0] == "git" and argv[1] == "--version":
                return Proc(127, err="git: not found")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=NoGit())

    assert verdict.writable is False
    assert verdict.reason == "git-absent"


# ══ (6) the marker contract is TOTAL: no path exits 1 without a REASONS token ═════════════════
def test_unexpected_exception_becomes_internal_error_not_none(tmp_path: Path, capsys):
    """A `return` inside `finally` used to SWALLOW an exception and emit `(None)`."""
    work, _remote = _make_pair(tmp_path)
    boom = {"fired": False}

    class Boom(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "push" and "--delete" not in argv:
                boom["fired"] = True
                raise RuntimeError("simulated crash")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=Boom(), keep_ref=True)

    assert boom["fired"] is True
    assert verdict.writable is False
    assert verdict.reason == "internal-error", f"got {verdict.reason!r} — marker would be (None)"
    assert verdict.reason in REASONS


def test_cli_never_prints_a_none_reason(tmp_path: Path, capsys, monkeypatch):
    """`main()` must map a missing reason onto a REASONS token, never print `(None)`.

    Patches `check_writable` itself (not a Runner): `main()` builds its own Runner, so a stub
    injected at the Runner seam would never be reached and the test would assert nothing.
    """
    import tools.gh_write_preflight as gwp

    monkeypatch.setattr(
        gwp, "check_writable", lambda *a, **k: gwp.Verdict(False, None, detail="boom")
    )

    rc = main(["--repo", str(tmp_path), "--json"])
    cap = capsys.readouterr()

    assert rc == EXIT_UNAVAILABLE
    assert "GH-WRITE-UNAVAILABLE(None)" not in cap.err
    assert "GH-WRITE-UNAVAILABLE(internal-error)" in cap.err
    # The JSON document is the branchable artifact — the belay must reach it too, not just stderr.
    payload = json.loads(cap.out)
    assert payload["reason"] == "internal-error"
    assert payload["reason_meaning"]  # non-empty


def test_the_contract_publishes_the_closed_reason_set():
    """The published token list must be the WHOLE of `REASONS` — and `REASONS` the whole of it.

    A consumer written from the contract (a lane brief, a caller's `case` table) branches on the
    list the contract publishes. The PR body called EIGHT tokens a "closed set" while `REASONS`
    held ten, omitting exactly the two that need a distinct branch: `push-unknown` (the push did
    not COMPLETE, so capability is UNKNOWN — re-run, never "cannot write") and `internal-error`
    (no verdict exists at all). A caller with no arm for `push-unknown` falls through to the
    park-deliverable-work diagnosis that token was split out to prevent, so an omission here is not
    a typo. The list therefore lives in the module docstring — where the next consumer reads it —
    instead of only in a PR body.
    """
    import tools.gh_write_preflight as gwp

    published = {
        "git-absent",
        "not-a-repo",
        "no-remote",
        "no-head",
        "remote-unreachable",
        "push-refused",
        "push-unknown",
        "verify-mismatch",
        "delete-failed",
        "internal-error",
    }
    # Both directions: an unpublished token is a caller's silent `default:`, and a published token
    # the code cannot emit sends that caller chasing a branch that never fires.
    assert set(REASONS) == published, (
        f"REASONS and the published set disagree: unpublished={set(REASONS) - published}, "
        f"unemittable={published - set(REASONS)}"
    )

    flat = " ".join((gwp.__doc__ or "").split())
    assert "CLOSED" in flat, "the contract must SAY the set is closed, or a caller cannot trust it"
    missing = [tok for tok in sorted(published) if f"``{tok}``" not in flat]
    assert not missing, f"the contract's closed set omits {missing}"
    # The two additions are only useful if the contract says what a caller must DO with them.
    assert "never read it as evidence of a read-only credential" in flat
    assert "re-run" in flat
    assert "NO verdict" in flat and "not a refusal" in flat


def test_cleanup_unconfirmed_never_claims_deleted(tmp_path: Path, capsys, monkeypatch):
    """A `cleaned_up=None` verdict must not render "deleted".

    The tri-state was computed correctly but the render keyed only on `kept_ref`, so stdout claimed
    "deleted" while stderr said UNCONFIRMED — a verified-clean claim from unverified cleanup, in
    the line lanes actually parse.
    """
    import tools.gh_write_preflight as gwp

    monkeypatch.setattr(
        gwp,
        "check_writable",
        lambda *a, **k: gwp.Verdict(
            True, None, probe_ref="gh-write-preflight/x", cleaned_up=None, remote="origin"
        ),
    )

    rc = main(["--repo", str(tmp_path)])
    cap = capsys.readouterr()

    assert rc == EXIT_WRITABLE
    assert "UNCONFIRMED" in cap.out
    assert "deleted" not in cap.out, "stdout must not claim a deletion it could not confirm"


def test_probe_ref_prefix_is_honoured(tmp_path: Path):
    """A repo whose ruleset permits only certain branch patterns can aim the probe."""
    work, _remote = _make_pair(tmp_path)

    verdict = check_writable(work, probe_ref_prefix="fix/gh-probe")

    assert verdict.probe_ref.startswith("fix/gh-probe/")
    assert verdict.writable is True


def test_success_verdict_keeps_reason_none(tmp_path: Path, capsys):
    """A PASS must not carry a REASONS token.

    Regression: the belay that protects the `--json` payload from `reason=None` on a REFUSAL was
    applied unconditionally, so every successful run reported `internal-error` — "no verdict is
    available" about a credential that had just proved it can write.
    """
    work, _remote = _make_pair(tmp_path)

    rc = main(["--repo", str(work), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == EXIT_WRITABLE
    assert payload["writable"] is True
    assert payload["reason"] is None, "a passing verdict must not be stamped internal-error"


def test_option_shaped_prefix_is_refused_before_any_push(tmp_path: Path, capsys):
    """A leading-dash prefix must be a usage error, not a ref that `git push` reads as a flag.

    Measured before the guard: `--probe-ref-prefix=-f` created the ref (the push refspec is
    `HEAD:refs/heads/...`, so it is not option-parsed) but the DELETE exited 129, leaving the ref
    behind AND reporting the credential unwritable — litter plus a false block.
    """
    work, remote = _make_pair(tmp_path)
    before = _remote_refs(remote)

    for bad in ("-f", "--force", "/abs", "a b", "a..b", "a:b", "*", ""):
        with pytest.raises(SystemExit) as exc:
            main(["--repo", str(work), f"--probe-ref-prefix={bad}"])
        assert exc.value.code == 2, f"{bad!r} must be a usage error (exit 2)"

    assert _remote_refs(remote) == before, "a refused prefix must not push anything"


def test_unrenderable_prefix_is_refused_by_git_not_reported_as_push_refused(tmp_path: Path):
    """Refname rules the hand-written belt cannot enumerate must still be a USAGE error.

    Measured before this: prefixes `.`, `.hidden`, `a/.hidden`, `foo.lock` and a tab rendered refs
    git rejects, and the tool returned `GH-WRITE-UNAVAILABLE(push-refused)` on a fully writable
    remote — a usage mistake reported as "your credential cannot write", the inversion the guard
    exists to prevent.

    ⚠️ HONEST SCOPE: every prefix in this list is ALSO rejected by `_valid_prefix`, and over 734
    measured candidates the belt accepts nothing `git check-ref-format` rejects — so `main()`'s
    git arm is DEFENCE IN DEPTH and is not reachable through this list. The arm itself is pinned
    directly, by `test_prefix_ok_rejects_gits_own_invalid_verdict`. If the belt is ever loosened,
    this test keeps the USAGE-error contract but would no longer prove the git arm fired.
    """
    work, remote = _make_pair(tmp_path)
    before = _remote_refs(remote)

    for bad in (".", ".hidden", "a/.hidden", "foo.lock", "a\tb"):
        with pytest.raises(SystemExit) as exc:
            main(["--repo", str(work), f"--probe-ref-prefix={bad}"])
        assert exc.value.code == 2, f"{bad!r} must be a usage error (exit 2)"

    assert _remote_refs(remote) == before


def test_legal_prefixes_are_still_accepted(tmp_path: Path):
    """The guard must not over-block ordinary namespaces."""
    work, _remote = _make_pair(tmp_path)

    for good in ("fix/gh-probe", "gh-probe", "lane/probe"):
        verdict = check_writable(work, probe_ref_prefix=good)
        assert verdict.writable is True, f"{good!r} should be a legal prefix"
        assert verdict.probe_ref.startswith(good + "/")


def test_legal_prefix_passes_the_cli_guard(tmp_path: Path, capsys):
    """The CLI guard must ACCEPT a legal prefix (it runs at parse time, before any push).

    Without this, the guard could reject everything and the suite would not notice.
    """
    work, _remote = _make_pair(tmp_path)

    rc = main(["--repo", str(work), "--probe-ref-prefix=fix/ci-probe"])
    cap = capsys.readouterr()

    assert rc == EXIT_WRITABLE, f"a legal prefix must pass the guard: {cap.err}"
    assert "fix/ci-probe/" in cap.out


def test_confirmation_reads_the_PUSH_url_not_the_fetch_url(tmp_path: Path):
    """A fork-style `pushurl` must be the remote we confirm against.

    Measured before: with `url=…/fetch.git` writable and `pushurl=…/push.git` READ-ONLY, the push
    was refused but the tool then read the FETCH url — a different repository. And in the mirror
    case (fetch read-only, push writable) it reported `verify-mismatch` for a credential that can
    write.
    """
    work, fetch_remote = _make_pair(tmp_path, read_only=True)      # fetch side: read-only
    push_remote = tmp_path / "push.git"
    _git("init", "--bare", "-q", str(push_remote), cwd=tmp_path)
    _git("remote", "set-url", "origin", str(fetch_remote), cwd=work)
    _git("remote", "set-url", "--push", "origin", str(push_remote), cwd=work)

    verdict = check_writable(work)

    # The PUSH target is writable, so the credential CAN deliver — a PASS, not verify-mismatch.
    assert verdict.writable is True, f"got {verdict.reason!r} — confirmed the wrong remote"
    assert verdict.push_url and "push.git" in verdict.push_url
    assert "gh-write-preflight/" not in _remote_refs(push_remote), "probe ref must be cleaned up"


def test_read_only_pushurl_is_refused_even_when_fetch_url_is_writable(tmp_path: Path):
    """The mirror case: a writable fetch url must not mask a read-only push target."""
    work, _fetch_remote = _make_pair(tmp_path)                      # fetch side: writable
    push_remote = tmp_path / "push.git"
    _git("init", "--bare", "-q", str(push_remote), cwd=tmp_path)
    hook = push_remote / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'remote: denied' >&2\nexit 1\n")
    hook.chmod(0o755)
    _git("remote", "set-url", "--push", "origin", str(push_remote), cwd=work)

    verdict = check_writable(work)

    assert verdict.writable is False
    assert verdict.reason == "push-refused"


def test_split_pushurl_shows_the_measured_remote_and_its_source(tmp_path: Path, capsys):
    """The answer must NAME the remote it measured, and say how it resolved it.

    On a fork config the `remote:` line names the FETCH url, which is not where the probe pushed —
    so a `push:` line and a `push_url_source` field are the only way a lane can tell which remote
    the verdict is about.
    """
    work, _fetch_remote = _make_pair(tmp_path)
    push_remote = tmp_path / "push.git"
    _git("init", "--bare", "-q", str(push_remote), cwd=tmp_path)
    _git("remote", "set-url", "--push", "origin", str(push_remote), cwd=work)

    rc = main(["--repo", str(work), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == EXIT_WRITABLE
    assert payload["push_url_source"] == "configured"
    assert "push.git" in payload["push_url"]
    assert "fetch" not in (payload["push_url"] or "")

    rc2 = main(["--repo", str(work)])
    out = capsys.readouterr().out
    assert rc2 == EXIT_WRITABLE
    assert "push:" in out, f"the render must name the measured remote: {out}"


def test_push_url_source_fellback_is_visible(tmp_path: Path, capsys):
    """When `get-url --push` cannot answer, the fallback must be VISIBLE, not silent."""

    class NoPushUrl(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "remote" and "--push" in argv:
                return Proc(129, err="error: unknown option `push'")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    work, _remote = _make_pair(tmp_path)
    verdict = check_writable(work, runner=NoPushUrl())

    assert verdict.push_url_source == "fellback"


def test_prefix_ok_rejects_gits_own_invalid_verdict():
    """The authoritative arm must be REACHABLE and refuse on git's rc==1.

    Without this, `_prefix_ok`'s git call could be deleted with a green suite (the belt already
    covers every shape the tests use).
    """
    class Invalid(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "check-ref-format":
                return Proc(1, err="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    class CannotRun(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "check-ref-format":
                return Proc(124, err="timed out")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    from tools.gh_write_preflight import _prefix_ok

    assert _prefix_ok("fix/ok", Invalid()) is False, "git's rc==1 must be a refusal"
    assert _prefix_ok("fix/ok", CannotRun()) is True, (
        "an unrunnable check must not be rendered as 'git rejects this refname'"
    )
    assert _prefix_ok("-bad", CannotRun()) is False, "the belt still refuses a leading dash"


def test_no_head_still_reports_the_push_url(tmp_path: Path):
    """`push_url` must not be dropped on the `no-head` path, where it is already resolved."""
    work = tmp_path / "empty"
    _git("init", "-q", str(work), cwd=tmp_path)
    _git("remote", "add", "origin", str(tmp_path / "nowhere.git"), cwd=work)

    verdict = check_writable(work)

    assert verdict.reason == "no-head"
    assert verdict.push_url, "push_url was resolved and then dropped"
    assert verdict.push_url_source in ("configured", "fellback")


def test_cleanup_reads_the_PUSH_remote_not_the_fetch_remote(tmp_path: Path):
    """The fail-open half of the wrong-remote bug: cleanup must confirm on the push target.

    Config: the FETCH url is writable and gets the probe ref deleted; the PUSH url is a different
    remote where the ref SURVIVES. Confirming cleanup against the fetch url would report
    `cleaned_up=True` while litter sits on the push target. Reverting the confirmation to the
    fetch url must fail here.
    """
    work, _fetch_remote = _make_pair(tmp_path)                      # fetch side: writable
    push_remote = tmp_path / "push.git"
    _git("init", "--bare", "-q", str(push_remote), cwd=tmp_path)
    _git("remote", "set-url", "--push", "origin", str(push_remote), cwd=work)

    class PushSurvives(Runner):
        """The ref is created on the push remote and its delete never takes effect there."""

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                ref = joined.split()[-1]
                if "push.git" in joined:
                    return Proc(0, out=f"deadbeef\t{ref}\n")     # survives on the push target
                return Proc(0, out="")                          # absent on the fetch url
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                return Proc(0, out="deleted")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=PushSurvives())

    assert verdict.push_url and "push.git" in verdict.push_url
    assert verdict.cleaned_up is False, "cleanup must be confirmed on the PUSH remote"
    assert verdict.leftover_ref is True


def test_multiple_pushurls_all_must_be_clean(tmp_path: Path):
    """`git push` writes to EVERY pushurl; confirming only the first is a false clean claim."""
    work, _remote = _make_pair(tmp_path)
    push_a = tmp_path / "push_a.git"
    push_b = tmp_path / "push_b.git"
    for r in (push_a, push_b):
        _git("init", "--bare", "-q", str(r), cwd=tmp_path)
    _git("remote", "set-url", "--push", "origin", str(push_a), cwd=work)
    _git("remote", "set-url", "--push", "--add", "origin", str(push_b), cwd=work)

    class SurvivesOnB(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                ref = joined.split()[-1]
                if "push_b.git" in joined:
                    return Proc(0, out=f"deadbeef\t{ref}\n")
                return Proc(0, out="")
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                return Proc(0, out="deleted")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=SurvivesOnB())

    assert verdict.cleaned_up is False, "a ref surviving on the SECOND pushurl must be reported"
    assert verdict.leftover_ref is True


def test_argv_never_carries_the_display_value_or_a_credential(tmp_path: Path):
    """argv is world-readable (/proc/<pid>/cmdline is 0444) and lanes share a cell.

    Two rules, both pinned here:
      * the REDACTED display value must never be handed to git as data (`redact()` rewrites
        userinfo, so git would contact a URL the config never named — a false block);
      * the common case reads the remote BY NAME, so no URL appears on argv at all.
    """
    work, _remote = _make_pair(tmp_path)
    raw = "ssh://git@example.invalid/x.git"
    _git("remote", "set-url", "origin", raw, cwd=work)

    seen = []

    class Recording(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            seen.append((list(argv), dict(env_extra or {})))
            if argv[1] == "ls-remote":
                return Proc(128, err="stub: unreachable")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=Recording())

    for call, _env in seen:
        joined = " ".join(call)
        assert "<userinfo-redacted>" not in joined, f"display value reached argv: {call}"
        assert raw not in joined, f"a URL was put on argv when the remote name would do: {call}"
    assert any("origin" in c for c, _e in seen), f"expected a read by remote name; saw {seen}"
    # ...while the DISPLAY value stored on the verdict is redacted.
    assert "<userinfo-redacted>" in (verdict.remote_url or "")
    assert "<userinfo-redacted>" in (verdict.push_url or "")


def test_credential_bearing_pushurl_never_reaches_argv(tmp_path: Path):
    """A push url embedding a token must reach git via an env `insteadOf`, never as an argument.

    argv is world-readable (/proc/<pid>/cmdline is 0444) and lanes share a cell. Measured after
    the cycle-6 fix: the raw token appeared in the process table for every read.

    ⛔ The stub must let the run REACH the push and the delete — an earlier version returned 128 on
    the first read, so no push/delete argv was ever inspected and a raw secret on those argv slots
    escaped (verified by mutation).
    """
    work, _fetch = _make_pair(tmp_path)
    push_remote = tmp_path / "push.git"
    _git("init", "--bare", "-q", str(push_remote), cwd=tmp_path)
    secret_url = "https://x-access-token:ghp_SUPERSECRETTOKEN123@example.invalid/push.git"
    _git("remote", "set-url", "--push", "origin", secret_url, cwd=work)
    head = _git("rev-parse", "HEAD", cwd=work).stdout.strip()

    seen = []
    deleted = []

    class Recording(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            seen.append((list(argv), dict(env_extra or {})))
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                if deleted:
                    return Proc(0, out="")
                return Proc(0, out=f"{head}\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" in argv:
                deleted.append(argv[-1])
                return Proc(0, out="")
            if argv[1] == "push":
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=Recording())

    assert [a[1] for a, _e in seen].count("push") >= 2, (
        f"the run must reach push AND delete; saw {[a[1] for a, _e in seen]}"
    )
    for call, _env in seen:
        assert "ghp_SUPERSECRETTOKEN123" not in " ".join(call), (
            f"a credential reached the process argv: {call}"
        )
    # The rewrite carries the url in the env (0400): `url.<raw url>.insteadOf = <alias>:`, so the
    # secret may sit in the KEY as well as the value — scan both.
    assert any(
        "ghp_SUPERSECRETTOKEN123" in f"{k}={v}"
        for _c, env in seen
        for k, v in env.items()
        if k.startswith("GIT_CONFIG_")
    ), f"the insteadOf rewrite never carried the push url; envs: {[e for _c, e in seen]}"
    assert verdict.writable is True, verdict


def test_lone_userinfo_is_treated_as_a_credential(tmp_path: Path):
    """`https://mytoken123@host/p.git` is a token form GitHub documents: no password, no prefix.

    It must not reach argv. Measured false negative before the fix.
    """
    work, _fetch = _make_pair(tmp_path)
    _git("remote", "set-url", "--push", "origin", "https://mytoken123@example.invalid/p.git", cwd=work)

    seen = []

    class Recording(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            seen.append((list(argv), dict(env_extra or {})))
            if argv[1] == "ls-remote":
                return Proc(128, err="stub: unreachable")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    check_writable(work, runner=Recording())
    for call, _env in seen:
        assert "mytoken123" not in " ".join(call), f"a lone-userinfo token reached argv: {call}"
    assert any(
        "mytoken123" in f"{k}={v}"
        for _c, env in seen
        for k, v in env.items()
        if k.startswith("GIT_CONFIG_")
    ), "a lone userinfo must route through the alias path, not be passed as data"


def test_ordinary_ssh_userinfo_is_not_treated_as_a_credential(tmp_path: Path):
    """`ssh://git@host` carries NO secret — routing it through the alias path would be a false block."""
    from tools.gh_write_preflight import _url_has_credential

    for url in (
        "ssh://git@example.invalid/x.git",
        "https://git@example.invalid/x.git",
        "git@example.invalid:o/r.git",
        "https://example.invalid/x.git",
    ):
        assert _url_has_credential(url) is False, url
    for url in (
        "https://mytoken123@example.invalid/p.git",
        "https://x-access-token:ghp_abc@example.invalid/p.git",
        "https://user:pw@example.invalid/x.git",
        # scp-LIKE with a password: `urlsplit` reads "user" as the SCHEME, so a branch gated on
        # `not parts.scheme` was dead and this form went to argv. GitHub's scp syntax carries no
        # password, but a credential written this way must still not be treated as data.
        "user:pass@example.invalid:o/r.git",
    ):
        assert _url_has_credential(url) is True, url


def test_noisy_accepting_remote_still_proves_the_write(tmp_path: Path):
    """A remote that prints >800 chars of advisory output while ACCEPTING the push.

    `steps[].detail` is truncated to the last 800 chars, so parsing the write flag from it loses
    the porcelain line on exactly this shape: a real write becomes `push-refused` (false block) and
    cleanup is skipped while being reported as done. Measured against real git.
    """
    work, _remote = _make_pair(tmp_path)
    head = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    noise = chr(10).join(f"remote: advisory line {i} " + "x" * 40 for i in range(40))

    deleted = []

    class Noisy(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                if deleted:
                    return Proc(0, out="")
                return Proc(0, out=f"{head}\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" in argv:
                deleted.append(argv[-1])
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" not in argv:
                # The REAL shape: `--porcelain` writes the ref line to STDOUT while the remote's
                # advisory output arrives on STDERR. `Proc.detail` is `out + err`, so the porcelain
                # line ends up FIRST — and a 40-line advisory tail pushes it out of the last-800
                # window `step()` stores. That is exactly how a real write became `push-refused`.
                return Proc(
                    0,
                    out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n",
                    err=noise,
                )
            if argv[1] == "push":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=Noisy())

    assert len(verdict.steps[1]["detail"]) <= 800
    assert verdict.writable is True, (
        f"a noisy ACCEPTING remote must still prove the write; got reason={verdict.reason!r}"
    )
    assert verdict.cleaned_up is True


def test_up_to_date_push_still_deletes_and_confirms_cleanup(tmp_path: Path):
    """`push` exiting 0 with no ref update must NOT short-circuit cleanup.

    A pre-existing probe ref (or a remote that reports differently) still has to be deleted and the
    deletion CONFIRMED. Reporting `cleaned_up=True` without a read abandons litter it claims not to
    have left.
    """
    work, _remote = _make_pair(tmp_path)
    deleted = []

    class UpToDate(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                # Present until the delete runs, absent after — so cleanup is confirmable.
                return Proc(0, out="" if deleted else "0" * 40 + "\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" in argv:
                deleted.append(argv[-1])
                return Proc(0, out="")
            if argv[1] == "push":
                return Proc(0, out="=")  # porcelain: up to date, NO write
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=UpToDate())

    assert verdict.writable is False
    assert verdict.reason == "push-refused"
    assert deleted, "cleanup was skipped for an up-to-date push"
    assert verdict.cleaned_up is True, f"cleanup must be CONFIRMED, saw {verdict.cleaned_up}"
    assert verdict.leftover_ref is False


def test_updated_ref_flag_porcelain_plus_proves_the_write(tmp_path: Path):
    """The `+` porcelain flag is a write, exactly like `*` — an UPDATE of a pre-existing ref.

    Every other fixture in this suite emits `*` (new branch) or `=` (up to date), so mutating
    `parts[0].strip() in ("*", "+")` to `("*",)` left all 89 cases green: the arm was unpinned (the
    residual the adversarial review recorded at `07423b9f1`). It is reachable in production —
    `_probe_ref()` stamps a second/pid/random ref, so a crashed run's leftover ref makes the next
    probe's push an UPDATE rather than a creation. Refusing that would be a false block on a
    credential that CAN write.
    """
    work, _remote = _make_pair(tmp_path)
    head = _git("rev-parse", "HEAD", cwd=work).stdout.strip()
    deleted = []

    class Updated(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                if deleted:
                    return Proc(0, out="")
                return Proc(0, out=f"{head}\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" in argv:
                deleted.append(argv[-1])
                return Proc(0, out="")
            if argv[1] == "push":
                # Real git's `--porcelain` line for a forced/updated ref (git-push(1) flag table:
                # `+` forced update). The ref existed, so this is an UPDATE, not a creation.
                return Proc(
                    0,
                    out="+\toldsha:newsha\tforced update\n",
                )
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=Updated())

    assert verdict.writable is True, (
        "an updated-ref (`+`) porcelain line proves the write; got "
        f"reason={verdict.reason!r} detail={verdict.detail!r}"
    )
    assert verdict.cleaned_up is True
    assert verdict.leftover_ref is False


def test_present_at_a_different_sha_is_a_verify_mismatch(tmp_path: Path):
    """The content proof: a ref that exists at ANOTHER sha is not OUR write.

    Mutating `state == "present" and seen == head` to `state == "present"` escaped a 57-test suite,
    so the whole difference between "a ref exists" and "our write landed" was unpinned.
    """
    work, _remote = _make_pair(tmp_path)

    class WrongSha(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            other = "a" * 40
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                return Proc(0, out=f"{other}\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" not in argv:
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            if argv[1] == "push":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=WrongSha())

    assert verdict.writable is False, "a ref at another sha must not prove OUR write"
    assert verdict.reason == "verify-mismatch"


def test_every_credential_pushurl_gets_its_own_insteadof_rewrite(tmp_path: Path):
    """Two credential-bearing pushurls must BOTH be resolvable, not just the last one.

    The rewrite for each credential target is an `insteadOf` alias carried in the child env, and
    the slots are absolute `GIT_CONFIG_*` indices. If every rewrite were derived from the SAME
    ambient `GIT_CONFIG_COUNT`, the second `GIT_CONFIG_KEY_n` would overwrite the first and alias 0
    would name nothing — git would fail to resolve the reachability read and the probe would
    report `remote-unreachable` for a credential that CAN write. That is a false block, the one
    failure mode this tool must never add, and it is invisible on a single-pushurl config.

    Mutation check: derive the slot inside the rewrite helper instead of counting up, and the
    first `ls-remote` here returns 128 -> `remote-unreachable` -> `writable is False`.
    """
    work, _fetch = _make_pair(tmp_path)
    secret_a = "https://x-access-token:ghp_AAAAAAAAAAAAAAAA@example.invalid/a.git"
    secret_b = "https://x-access-token:ghp_BBBBBBBBBBBBBBBB@example.invalid/b.git"
    _git("remote", "set-url", "--push", "origin", secret_a, cwd=work)
    _git("remote", "set-url", "--push", "--add", "origin", secret_b, cwd=work)

    head = {"sha": None}
    state = {"deleted": False}

    class AliasAware(Runner):
        """Fails a read whose `insteadOf` alias the env does not define — as git would."""

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            joined = " ".join(argv)
            if argv[1] == "rev-parse":
                p = super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)
                if p.rc == 0 and p.out.strip():
                    head["sha"] = p.out.strip().splitlines()[-1]
                return p
            if argv[1] == "ls-remote":
                target = next(
                    (a for a in argv if a.startswith("gh-write-preflight-push")), None
                )
                if target is not None:
                    defined = {
                        v
                        for k, v in (env_extra or {}).items()
                        if k.startswith("GIT_CONFIG_VALUE_")
                    }
                    if target not in defined:
                        return Proc(128, err=f"fatal: could not resolve {target}")
                if _PROBE in joined:
                    if state["deleted"]:
                        return Proc(0, out="")
                    return Proc(0, out=f"{head['sha']}\t{joined.split()[-1]}\n")
                return Proc(0, out=f"{head['sha']}\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                state["deleted"] = True
                return Proc(0, out="deleted")
            if argv[1] == "push":
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=AliasAware())

    assert verdict.reason != "remote-unreachable", (
        "a second credential pushurl must not make the first one unnameable: "
        f"{verdict.detail}"
    )
    assert verdict.writable is True, verdict.detail
    assert verdict.cleaned_up is True, verdict.detail


def test_scp_like_password_never_reaches_argv():
    """A password in scp-like userinfo must be routed through the ENV, never onto argv.

    `user:secret@host:path` is the shape `urlsplit` parses worst: `user` becomes the SCHEME, so
    `parts.password` is None and a scheme-based credential check cannot see the secret — measured
    on the pre-fix code, `_url_has_credential("user:hunter2@host:repo.git")` returned False, so
    `_read_targets` handed the raw URL to `git ls-remote`. argv is mode 0444 (`/proc/<pid>/cmdline`)
    on a cell every lane shares, so that is a live credential readable by any process on the box.
    The tool's contract is that a secret never reaches argv or output: `redact()` covers the output
    half, and this is the argv half.

    The opposite direction is asserted too, because over-blocking is the failure mode this tool
    must NEVER add: `git@host:repo.git` carries an SSH user and no secret, so it stays a plain
    argv target rather than being pushed through a rewrite it does not need.
    """
    fetch_url = "https://example.invalid/fetch.git"
    secret_url = "user:hunter2@example.invalid:repo.git"

    assert _url_has_credential(secret_url) is True, (
        "a password in scp-like userinfo IS a credential — urlsplit reads `user` as the scheme, "
        "so the parts.password check cannot see it and the raw URL would land on argv"
    )
    targets = _read_targets("origin", fetch_url, [secret_url])
    assert targets == ["gh-write-preflight-push0:"], targets
    assert all(secret_url not in t for t in targets)
    env = _read_env("origin", fetch_url, [secret_url])
    # The rewrite itself must live in the env (mode 0400), not on argv: `url.<secret>.insteadOf`
    # names the alias, so the secret is a KEY here and never an argv element.
    assert env["GIT_CONFIG_KEY_0"] == f"url.{secret_url}.insteadOf", env
    assert env["GIT_CONFIG_VALUE_0"] == "gh-write-preflight-push0:", env
    assert all(secret_url not in t for t in _read_targets("origin", fetch_url, [secret_url]))

    # False-block direction — an SSH user is not a secret, so it stays on argv.
    assert _url_has_credential("git@example.invalid:repo.git") is False
    assert _read_targets("origin", fetch_url, ["git@example.invalid:repo.git"]) == [
        "git@example.invalid:repo.git"
    ]
    # A local path that merely CONTAINS `@` has no userinfo at all.
    assert _url_has_credential("/tmp/bare/remote@1.git") is False


def test_an_unparseable_url_is_credited_with_a_secret_and_never_handed_raw():
    """`urlsplit` RAISES on an unmatched `[`, so that arm must not answer "no credential".

    Measured on the pre-fix head:

        urlsplit("https://user:pa[ssw0rd@example.invalid/r.git")
            -> ValueError: Invalid IPv6 URL
        _url_has_credential(same)                              -> False
        _read_targets("origin", "https://x/fetch.git", [same]) -> [same]   # raw, on argv
        redact(same)                       -> https://<userinfo-redacted>@example.invalid/r.git

    So the password `pa[ssw0rd` went to `git ls-remote` as an argument — argv is mode 0444
    (`/proc/<pid>/cmdline`), readable by every process on a cell lanes share — while `redact()`
    masked the very same string in the display.

    ⚠️ The defect is the argv copy, NOT reachability: `urlsplit`'s refusal is urllib's rule, not
    git's. Measured, `git ls-remote --heads 'https://user:pa[ss@example.invalid/r.git'` strips the
    userinfo and proceeds to DNS/connect exactly like the bracket-free control, so this arm never
    produced a `remote-unreachable`. Erring toward True costs nothing for the same reason it fixes
    the leak: the target is reached through the `insteadOf` alias, which git rewrites to this same
    URL string — it moves where the secret lives (0400 env), not what git does.
    """
    from urllib.parse import urlsplit

    leaky = "https://user:pa[ssw0rd@example.invalid/r.git"
    # The premise, asserted rather than assumed: no credential check AFTER the parse can run.
    with pytest.raises(ValueError):
        urlsplit(leaky)

    assert _url_has_credential(leaky) is True, (
        "an unparseable URL must be assumed to carry a secret — a false negative here puts the "
        "password on world-readable argv"
    )
    targets = _read_targets("origin", "https://x/fetch.git", [leaky])
    assert targets == ["gh-write-preflight-push0:"], targets
    assert all(leaky not in t for t in targets), "the raw credential url reached argv"
    env = _read_env("origin", "https://x/fetch.git", [leaky])
    assert env["GIT_CONFIG_KEY_0"] == f"url.{leaky}.insteadOf", env
    assert env["GIT_CONFIG_VALUE_0"] == "gh-write-preflight-push0:", env


def test_real_git_accepts_an_insteadof_base_holding_the_bracket_urlsplit_refuses(tmp_path: Path):
    """REAL git: the `[` that makes `urlsplit` raise does NOT stop git's `insteadOf` rewrite.

    The unparseable-URL arm is only SOUND because git tolerates a `url.<base>.insteadOf` key whose
    base contains that `[`: the secret is withheld from argv (0444) and reached through the alias
    instead. If git REJECTED such a key, the alias would name nothing and a writable credential
    would come back `remote-unreachable` — a false block, the one failure this tool must never add.
    The comment on that arm asserts this was measured of real git; a review carried it as the
    AUTHOR's measurement, because nothing in this suite executed it. This executes it, twice, with
    no network:

      1. git's config grammar accepts the EXACT key `_read_env` emits for the leaky URL — a `[`
         inside the userinfo of an https URL — and reads it back verbatim;
      2. the rewrite `_insteadof_env` builds RESOLVES an alias to a real bare repo whose path
         holds a `[`, measured against the control where the same alias resolves nowhere.

    Scope stated, not overstated: (1) covers the arm's real output and (2) covers the rewrite its
    product performs. Neither is a byte-for-byte replica of GitHub's transport, which is why the
    control assertion below is what makes the resolution half non-vacuous.
    """
    leaky = "https://user:pa[ssw0rd@example.invalid/r.git"
    alias = "gh-write-preflight-push0:"

    # (1) Git ACCEPTS the base it is handed. No transport involved: `git config` only parses.
    arm_env = _read_env("origin", "https://x/fetch.git", [leaky])
    assert arm_env["GIT_CONFIG_KEY_0"] == f"url.{leaky}.insteadOf", arm_env
    read_back = subprocess.run(
        ["git", "config", "--get-regexp", r"^url\."],
        capture_output=True,
        text=True,
        env={**_GIT_ENV, **arm_env},
        check=True,
    ).stdout.strip()
    assert read_back == f"url.{leaky}.insteadof {alias}", (
        f"git refused the insteadOf base the arm builds: {read_back!r}"
    )

    # (2) That rewrite form RESOLVES. A bare repo whose own path carries the bracket is the
    #     resolvable stand-in; the position of `[` is irrelevant to insteadOf, which is a plain
    #     prefix substitution.
    remote = tmp_path / "re[1].git"
    work = tmp_path / "work"
    _git("init", "--bare", "-q", str(remote), cwd=tmp_path)
    _git("init", "-q", str(work), cwd=tmp_path)
    (work / "f.txt").write_text("hello\n")
    _git("add", "f.txt", cwd=work)
    _git("commit", "-q", "-m", "init", cwd=work)
    _git("remote", "add", "origin", str(remote), cwd=work)
    _git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=work)

    def _ls_remote(extra_env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "ls-remote", "--heads", alias],
            capture_output=True,
            text=True,
            env={**_GIT_ENV, **extra_env},
        )

    control = _ls_remote({})
    assert control.returncode != 0, (
        f"the alias resolved with no rewrite at all, so the resolution half proves nothing: "
        f"{control.stdout!r}"
    )
    rewritten = _ls_remote(_insteadof_env(f"file://{remote}", alias, 0))
    assert rewritten.returncode == 0, rewritten.stderr
    assert "refs/heads/main" in rewritten.stdout, rewritten.stdout


def test_delete_success_but_ref_still_present_is_leftover(tmp_path: Path):
    """Cleanup is confirmed by ls-remote, NOT by the delete's own exit code.

    Isolated deliberately: the verify read returns the CORRECT head (so `writable` is True and
    `reason` is None), and only the cleanup read-back disagrees — so a `writable is False` result
    can only come from the leftover flip, not from an unrelated `verify-mismatch`.
    """
    head = {"sha": None}

    class LyingDelete(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                ref = joined.split()[-1]
                # present on BOTH the verify and the cleanup read
                return Proc(0, out=f"{head['sha']}\t{ref}\n")
            if argv[1] == "rev-parse":
                p = super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)
                if p.rc == 0 and p.out.strip():
                    head["sha"] = p.out.strip().splitlines()[-1]
                return p
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                return Proc(0, out="deleted")        # LIES: ref is still there
            if argv[1] == "push":
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    work, _remote = _make_pair(tmp_path)
    verdict = check_writable(work, runner=LyingDelete())

    assert verdict.cleaned_up is False
    assert verdict.leftover_ref is True
    assert verdict.writable is False, "a surviving ref must not be reported as a pass"


def test_delete_uses_a_double_dash_guard(tmp_path: Path):
    """The delete argv must separate options from the ref."""
    seen = []

    class Recording(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            seen.append(list(argv))
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    work, _remote = _make_pair(tmp_path)
    check_writable(work, runner=Recording())

    deletes = [a for a in seen if a[1] == "push" and "--delete" in a]
    assert deletes, "a delete must have been attempted"
    assert "--" in deletes[0], f"delete argv needs a `--` guard: {deletes[0]}"


def test_oserror_is_mapped_not_raised():
    """`Runner.run`'s exception arms must return a Proc, never raise — a raised OSError exits 1
    with NO GH-WRITE-UNAVAILABLE marker, which breaks the contract.

    This patches `subprocess.run` so the REAL `Runner.run` handler is entered. Stubbing
    `Runner.run` itself (as an earlier version did) never executes the handler, so it proved
    nothing.
    """
    def raiser(*a, **k):
        raise OSError(24, "Too many open files")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess, "run", raiser)
        proc = Runner().run(["git", "--version"])

    assert isinstance(proc, Proc)
    assert proc.rc == 126
    assert "Too many open files" in proc.err


def test_missing_binary_is_mapped_to_127():
    """The 127 (missing binary) arm is a different reason from 126 — pin it."""
    def raiser(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess, "run", raiser)
        proc = Runner().run(["git", "--version"])

    assert proc.rc == 127


def test_timeout_is_mapped_to_124():
    """A probe that times out must become a verdict, not a hang."""
    def raiser(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess, "run", raiser)
        proc = Runner().run(["git", "--version"], timeout=1)

    assert proc.rc == 124
    assert "timed out" in proc.err


def test_stdin_is_not_inherited():
    """A child that reads the terminal (ssh passphrase, gpg pinentry) must get no stdin."""
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        captured.update(kwargs)
        return FakeProc()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(subprocess, "run", fake_run)
        Runner().run(["git", "--version"])

    assert captured.get("stdin") == subprocess.DEVNULL, "stdin must be DEVNULL, not inherited"
    assert captured.get("errors") == "replace"


# ══ (7) a surviving probe ref must be reported WITHOUT erasing the primary reason ══════════════
def test_undeletable_probe_ref_is_reported(tmp_path: Path):
    """A tool that leaves garbage on a shared remote must not report a clean PASS."""
    work, remote = _make_pair(tmp_path)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read -r old new ref; do\n"
        '  case "$new" in 0000000000000000000000000000000000000000) '
        '    echo "remote: deletion denied" >&2; exit 1;; esac\n'
        "done\n"
        "exit 0\n"
    )
    hook.chmod(0o755)

    verdict = check_writable(work)

    assert verdict.writable is False
    assert verdict.reason == "delete-failed"
    assert verdict.leftover_ref is True
    assert verdict.cleaned_up is False


def test_known_survivor_exits_nonzero_with_delete_failed_marker(tmp_path: Path, capsys):
    """A KNOWN survivor must exit 1 with `delete-failed` — and must not read as "cannot write".

    The published Contract used to say a known survivor "does not change the exit code", while
    forcing `writable=False`, which the CLI turns into exit 1. A lane whose preamble keyed on that
    sentence would park deliverable work on a `delete-failed` — a false block produced by the
    documentation, the one failure this module says it must never add. The BEHAVIOUR (exit 1) is
    the safe side and is not changed here; this case pins it so the sentence cannot drift back,
    and pins the marker's wording as litter rather than capability.

    Red-first: a revision that "fixes" the contradiction by making the code exit 0 on a known
    survivor (the false-PASS direction) fails the first assertion.

    ⛔ THE PROSE NEEDS ITS OWN PIN. Every other assertion in this case is about BEHAVIOUR, and the
    behaviour was never the defect — `_push_verify_cleanup` always forced `writable=False`. Measured
    on this head: putting the pre-fix Contract sentence back left the suite GREEN (5 passed, 88
    deselected), i.e. the one paragraph a lane's preamble is written from could regress with nothing
    red. That is the "false rationale" class this PR already fixed by hand twice (`5f4bf4bbc`,
    `1fe6f6f4b`), so the sentence is pinned at the source.
    """
    work, remote = _make_pair(tmp_path)
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        "while read -r old new ref; do\n"
        '  case "$new" in 0000000000000000000000000000000000000000) '
        '    echo "remote: deletion denied" >&2; exit 1;; esac\n'
        "done\n"
        "exit 0\n"
    )
    hook.chmod(0o755)

    rc = main(["--repo", str(work)])

    cap = capsys.readouterr()          # captured ONCE — readouterr() clears on read
    assert rc == EXIT_UNAVAILABLE == 1, (
        "a known survivor is not a pass, so it must not exit 0"
    )
    assert "GH-WRITE-UNAVAILABLE(delete-failed)" in cap.err
    assert "GH-WRITE-OK" not in cap.out, "a refusal must never print the OK marker"
    # The refusal is about LITTER, not capability: the push already succeeded. Wording a lane's
    # preamble would classify as "this credential is read-only" is the false block.
    assert "cannot write" not in cap.err
    assert "read-only" not in cap.err
    assert "push already succeeded" in REASONS["delete-failed"], (
        "the REASONS gloss must clear the credential: the write already happened"
    )
    assert "cannot write" not in REASONS["delete-failed"]

    # The CONTRACT sentence that produced the false block must not come back. Asserted on the
    # module source rather than on behaviour because the defect WAS the prose: the code already
    # answered 1, so nothing behavioural can red on a relapse of the sentence alone.
    source = (Path(__file__).resolve().parent.parent / "tools" / "gh_write_preflight.py").read_text()
    # Whitespace-normalised so a harmless re-wrap is not a false block: only rewriting the CLAIM
    # (which is exactly what a reviewer must re-check) reds here.
    flat = " ".join(source.split())
    assert "does not change the exit code either, but it is not a" not in flat, (
        "the Contract must not claim a known survivor keeps exit 0 — the code returns 1"
    )
    assert (
        "UNREQUESTED known survivor is the one cleanup outcome that DOES change the exit code" in flat
    ), "the Contract must say plainly that an unrequested survivor DOES change the exit code"


def test_push_refused_but_remote_accepted_reports_leftover_without_erasing_the_cause(tmp_path: Path):
    """Push may report failure AFTER the remote applied the update — cleanup must still run.

    Simulated: push returns 1, the ref IS on the remote, and the delete ALSO fails. The ref
    survives, so `leftover_ref` must be True while `reason` stays `push-refused` — a caller has to
    learn BOTH that its lanes cannot deliver and that there is litter. A regression that overwrote
    `push-refused` with `delete-failed` fails here.
    """
    state = {"ref_exists": False, "delete_attempted": False}

    class TransportLie(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            joined = " ".join(argv)
            is_probe_query = argv[1] == "ls-remote" and _PROBE in joined
            if is_probe_query:
                ref = joined.split()[-1]
                return Proc(0, out=f"deadbeef\t{ref}\n") if state["ref_exists"] else Proc(0, out="")
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                state["delete_attempted"] = True
                return Proc(1, err="remote: deletion denied")   # delete FAILS
            if argv[1] == "push":
                state["ref_exists"] = True        # applied, then the transport died
                return Proc(1, err="fatal: the remote end hung up unexpectedly")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    work, _remote = _make_pair(tmp_path)
    verdict = check_writable(work, runner=TransportLie())

    assert state["delete_attempted"] is True, "cleanup must run even when push reports failure"
    assert verdict.writable is False
    assert verdict.reason == "push-refused", "the primary cause must NOT be overwritten"
    assert verdict.leftover_ref is True, "a surviving ref must be reported"
    assert verdict.cleaned_up is False


def test_push_failed_and_nothing_created_is_clean_not_leftover(tmp_path: Path):
    """The common case: push refused, no ref exists, so cleanup is genuinely clean."""
    class RefusedClean(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                return Proc(0, out="")
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push":
                return Proc(1, err="remote: denied")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    work, _remote = _make_pair(tmp_path)
    verdict = check_writable(work, runner=RefusedClean())

    assert verdict.reason == "push-refused"
    assert verdict.leftover_ref is False
    assert verdict.cleaned_up is True


def test_unverifiable_cleanup_is_none_not_true(tmp_path: Path):
    """A failed confirming read must NOT be reported as verified-clean.

    `_ref_state` used to fold "could not check" into "absent", so `cleaned_up=True` was asserted
    from an absence of evidence — the exact property the tool claims to verify.
    """
    class Unreadable(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                return Proc(128, err="fatal: could not read from remote")
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                return Proc(1, err="deletion denied")
            if argv[1] == "push":
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    work, _remote = _make_pair(tmp_path)
    verdict = check_writable(work, runner=Unreadable())

    assert verdict.cleaned_up is None, "unconfirmed cleanup must be None, never True"
    assert verdict.leftover_ref is False
    assert "UNCONFIRMED" in verdict.detail


def test_ref_state_is_tristate(tmp_path: Path):
    class R(Runner):
        def __init__(self, rc, out):
            self._rc, self._out = rc, out

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            return Proc(self._rc, out=self._out)

    assert _ref_state(R(0, "abc\trefs/heads/x\n"), ".", "origin", "x") == "present"
    assert _ref_state(R(0, ""), ".", "origin", "x") == "absent"
    assert _ref_state(R(128, ""), ".", "origin", "x") == "unverifiable"


def test_verify_mismatch_still_attempts_cleanup(tmp_path: Path):
    """A push that 'succeeds' but leaves no visible ref must still be cleaned up."""
    state = {"delete_attempted": False}

    class LyingPush(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                return Proc(0, out="")            # nothing visible -> verify-mismatch
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                state["delete_attempted"] = True
                return Proc(0, out="deleted")
            if argv[1] == "push":
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    work, _remote = _make_pair(tmp_path)
    verdict = check_writable(work, runner=LyingPush())

    assert verdict.writable is False
    assert verdict.reason == "verify-mismatch"
    assert state["delete_attempted"] is True


def test_keep_ref_is_explicit_and_the_message_does_not_lie(tmp_path: Path, capsys):
    """`--keep-ref` must not claim the ref was deleted."""
    work, remote = _make_pair(tmp_path)

    rc = main(["--repo", str(work), "--keep-ref"])

    cap = capsys.readouterr()
    assert rc == EXIT_WRITABLE
    assert "KEPT" in cap.out
    assert "probe ref" in cap.out and "deleted" not in cap.out
    assert _PROBE + "/" in _remote_refs(remote), "--keep-ref must leave the ref"


# ══ (8) stdout/stderr are parsed separately (a stderr warning must not become data) ════════════
def test_stderr_warning_does_not_become_the_repo_path(tmp_path: Path):
    """`warning: redirecting to https://…` on stderr once became the repo path."""
    work, _remote = _make_pair(tmp_path)

    class WarningOnStderr(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            p = super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)
            if argv[1] == "rev-parse" and "--show-toplevel" in argv:
                return Proc(p.rc, out=p.out, err="warning: unrelated advisory\n")
            return p

    verdict = check_writable(work, runner=WarningOnStderr())

    assert verdict.writable is True, "a stderr warning must not break the verdict"
    # Compare to the REAL path: the tmp_path itself contains "warning" in its name, so a
    # substring test would be satisfied by the fixture directory rather than by the code.
    assert verdict.repo_root == str(work)
    assert "unrelated advisory" not in (verdict.repo_root or "")


# ══ (9) no credential ever reaches output, and none is retained in memory ══════════════════════
def test_redaction_covers_every_url_scheme_and_auth_header():
    cases = {
        "https://x-access-token:ghs_abc123456789@github.com/o/r.git": "ghs_abc123456789",
        "ssh://user:hunter2supersecret@example.com/r.git": "hunter2supersecret",
        "git+ssh://user:hunter2supersecret@example.com/r.git": "hunter2supersecret",
        "git://user:hunter2supersecret@example.com/r.git": "hunter2supersecret",
        "//user:hunter2supersecret@example.com/r.git": "hunter2supersecret",
        "Authorization: Basic dXNlcjp0b2tlbg==": "dXNlcjp0b2tlbg==",
        "Authorization: Bearer abc123secret": "abc123secret",
        # one-token header (no scheme word) — previously survived
        "Authorization: ZXhhbXBsZTpiZWFyZXI=": "ZXhhbXBsZTpiZWFyZXI=",
        "{'Authorization': 'Basic dXNlcjp0b2tlbg=='}": "dXNlcjp0b2tlbg==",
        "Proxy-Authorization: Basic cHJveHk=": "cHJveHk=",
        "api_token = abcdef123": "abcdef123",
        "using token ghp_ABCDEFGH12345678": "ghp_ABCDEFGH12345678",
        "github_pat_11ABCDEFG0_xyz": "github_pat_11ABCDEFG0_xyz",
    }
    for dirty, secret in cases.items():
        assert secret not in redact(dirty), f"{secret!r} leaked from {dirty!r}"


def test_non_json_mode_does_not_print_credentials(tmp_path: Path, capsys):
    """The non-JSON render path must be clean too (only --json was previously checked)."""
    work, _remote = _make_pair(tmp_path, read_only=True)
    _git(
        "remote", "set-url", "origin",
        "ssh://user:ghp_SECRETSECRET123@example.invalid/x.git", cwd=work,
    )

    rc = main(["--repo", str(work)])

    cap = capsys.readouterr()
    assert rc == EXIT_UNAVAILABLE
    assert "ghp_SECRETSECRET123" not in cap.out + cap.err


def test_json_verdict_is_redacted_and_keys_on_writable(tmp_path: Path, capsys):
    work, _remote = _make_pair(tmp_path, read_only=True)
    _git(
        "remote", "set-url", "origin",
        "https://user:ghp_SECRETSECRET123@example.invalid/x.git", cwd=work,
    )

    rc = main(["--repo", str(work), "--json"])

    cap = capsys.readouterr()
    assert rc == EXIT_UNAVAILABLE
    assert "ghp_SECRETSECRET123" not in cap.out + cap.err
    payload = json.loads(cap.out)
    assert payload["writable"] is False
    # The marker must ALSO reach stderr in --json mode, or a caller grepping for it sees nothing.
    assert "GH-WRITE-UNAVAILABLE(" in cap.err


def test_verdict_object_holds_no_credential(tmp_path: Path):
    """Redaction is at INGESTION: a caller that logs/reprs the Verdict cannot leak."""
    work, _remote = _make_pair(tmp_path, read_only=True)
    _git(
        "remote", "set-url", "origin",
        "https://user:ghp_LEAKLEAKLEAK123@example.invalid/x.git", cwd=work,
    )

    verdict = check_writable(work)

    assert "ghp_LEAKLEAKLEAK123" not in repr(verdict)
    assert "ghp_LEAKLEAKLEAK123" not in json.dumps(verdict.steps)


# ══ (10) the tool is importable/runnable the way the repo's tools are ══════════════════════════
def test_help_documents_the_exit_contract():
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--help"],
        capture_output=True,
        text=True,
        check=False,
        # argparse wraps to COLUMNS; pin it so a narrow terminal cannot break the assertion.
        env={**os.environ, "COLUMNS": "200"},
    )
    assert proc.returncode == 0
    assert "Exit 0 writable" in proc.stdout
    assert "1 unavailable" in proc.stdout


# ══ (11) the docstring's standalone command runs for real (cycle-2 review P3-3) ═════════════════
def test_documented_standalone_command_actually_runs():
    """`Run standalone: python3 tests/test_gh_write_preflight.py` must actually RUN the suite.

    MEASURED pre-fix, on a fresh worktree with no DB configured:

        $ .venv/bin/python tests/test_gh_write_preflight.py
        … ERROR at setup of test_…: Failed: default pytest requires TORTOISE_DB_URI (epic #1647 P4)
        55 errors in 2.36s          rc=1

    The docstring's command is what a contributor on a fresh box types first; a suite that reds for
    an unrelated reason is how a green suite earns "known-broken" status. The fix belongs in the
    `__main__` block (the carve-out), not in a caveat added to the docstring — the repo already
    records that shape in tests/test_closes_landing.py's `__main__`.

    DISCRIMINATION: delete the `os.environ.setdefault("TORTOISE_TEST_CARVE_OUT", "1")` line and this
    test fails (the child re-reports the session-gate ERROR and exits 1). To keep the child from
    recursing into all its cases, `PYTEST_ADDOPTS` narrows it to ONE; and the child env has
    TORTOISE_DB_URI / TORTOISE_TEST_CARVE_OUT STRIPPED, so the assertion cannot be satisfied
    vacuously on a lane that happens to export a live URI.

    TORTOISE_TEST_EXPECT_URI is stripped for the same reason and it is not optional: CI's docker
    half exports `TORTOISE_TEST_EXPECT_URI=1` (`.github/workflows/python-ci.yml:1567`) and the
    E2E-6 tripwire (`tests/conftest.py:1789`) FAILS any session that claims to be a docker session
    while `TORTOISE_DB_URI` is unset. Keeping the expectation while deleting the only URI is a
    self-contradictory child env — and it red `test (c)` at 07423b9f1 with
    "TORTOISE_TEST_EXPECT_URI=1 but TORTOISE_DB_URI unset", i.e. the harness pointed at itself.
    The child is DELIBERATELY a URI-less carve-out session, so the parent lane's expectation does
    not describe it; the thing under test here is the `__main__` carve-out, and it stays the only
    thing supplying that opt-in.
    """
    stripped = (
        "TORTOISE_DB_URI",
        "TORTOISE_TEST_EXPECT_URI",
        "TORTOISE_TEST_CARVE_OUT",
        "TORTOISE_LIVE",
        "PYTEST_ADDOPTS",
        "PYTEST_CURRENT_TEST",
        "PYTEST_XDIST_WORKER",
    )
    env = {k: v for k, v in os.environ.items() if k not in stripped}
    env["PYTEST_ADDOPTS"] = "-k test_help_documents_the_exit_contract"
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=600,
    )
    tail = f"\n--- stdout ---\n{proc.stdout[-2500:]}\n--- stderr ---\n{proc.stderr[-2500:]}"
    assert proc.returncode == 0, f"the documented standalone command failed:{tail}"
    assert "1 passed" in proc.stdout, f"the child ran the wrong selection:{tail}"
    assert "requires TORTOISE_DB_URI" not in (proc.stdout + proc.stderr), (
        f"the session gate refused the documented command:{tail}"
    )


def test_noisy_ACCEPTING_remote_proves_the_write_against_real_git(tmp_path: Path):
    """REAL git: a bare remote whose `pre-receive` prints 40 advisory lines and exits 0.

    This is the measured reproduction of the P1: the porcelain ref line sits FIRST in
    `Proc.detail` (= out + err) and the advisory tail pushes it past the 800-char cut, so parsing
    the write flag from `steps[-1]["detail"]` turned a real write into `push-refused` — a false
    block that also skipped cleanup while reporting it. No stub: a real remote, a real hook.
    """
    work, remote = _make_pair(tmp_path, noisy=True)

    verdict = check_writable(work)

    assert verdict.writable is True, (
        f"a noisy ACCEPTING remote must prove the write; got reason={verdict.reason!r} "
        f"detail={verdict.detail!r}"
    )
    assert verdict.cleaned_up is True, "the probe ref must be deleted and the deletion confirmed"
    assert verdict.leftover_ref is False
    assert "gh-write-preflight" not in _remote_refs(remote), "the probe ref was left on the remote"


# ══ (9) cycle-9 findings, each pinned by the mutation that proves it ═══════════════════════════
def test_scp_like_userinfo_is_REDACTED_in_output(tmp_path: Path, capsys):
    """Detection without redaction is a leak — this tool detected the shape and then printed it.

    `_url_has_credential` was fixed to recognise `user:secret@host:path`, but `redact()` still only
    covered `scheme://userinfo@`, so both render paths printed the secret in cleartext. Measured
    before the fix: a `--json` run carried it in BOTH remote_url and push_url.
    """
    work, _remote = _make_pair(tmp_path)
    secret_url = "user:hunter2@example.invalid:repo.git"
    _git("remote", "set-url", "origin", secret_url, cwd=work)

    assert _url_has_credential(secret_url) is True
    assert "hunter2" not in redact(secret_url), "redact() must cover what the detector calls a secret"

    rc = main(["--repo", str(work)])
    out = capsys.readouterr().out
    assert rc in (EXIT_WRITABLE, EXIT_UNAVAILABLE)
    assert "hunter2" not in out, f"the credential was printed: {out}"

    main(["--repo", str(work), "--json"])
    payload = capsys.readouterr().out
    assert "hunter2" not in payload, f"the credential reached the JSON payload: {payload}"


def test_scp_like_lone_userinfo_is_redacted_but_git_user_is_not():
    """The scp exemption is by USER, not by shape: `git@host:` must survive intact.

    Measured before: this line asserted `"hunter2" not in redact("1b2c3d4e5f@example.invalid:…")`
    — but `hunter2` is a token BLED IN from the neighbouring cases, and it is not in the input
    string at all, so no implementation that does not *invent* a token could fail it. The lone,
    non-exempt userinfo is the only shape that enters `_redact_scp_userinfo`'s exemption branch
    (`if ":" not in userinfo and user.lower() in _NON_SECRET_USERINFO`) WITHOUT being exempt, and
    nothing else in this suite reached it — so a regression that returned that userinfo verbatim
    shipped green. Assert on the input's OWN secret, and assert the host SURVIVES so the check
    cannot be satisfied by a `redact()` that masks everything.
    """
    lone = "1b2c3d4e5f@example.invalid:repo.git"
    masked = redact(lone)
    assert "1b2c3d4e5f" not in masked, f"a lone non-exempt scp userinfo leaked: {masked!r}"
    assert masked == "<userinfo-redacted>@example.invalid:repo.git"
    assert redact("git@github.com:o/r.git") == "git@github.com:o/r.git"
    assert "git@" not in redact("ssh://git@example.invalid/x.git")


def test_scp_form_lone_userinfo_is_detected_as_a_credential():
    """Same token, no scheme: `token@host:path` is the same credential as `https://token@host/…`.

    Measured before: `_url_has_credential("mytoken123@example.invalid:p.git")` was False, so
    `_read_targets` handed the raw string to git on argv (0444 on a shared cell).
    """
    assert _url_has_credential("mytoken123@example.invalid:p.git") is True
    assert _url_has_credential("git@example.invalid:repo.git") is False
    assert _url_has_credential("/tmp/bare/remote@1.git") is False


def test_a_transient_extra_read_does_not_false_block(tmp_path: Path):
    """ONE read per target: the sha comes from the read that establishes presence.

    Measured before (real push to two pushurls, the separate verify re-read returning 128 while the
    multi-read said present): `writable=False reason=verify-mismatch` on a credential that writes.
    """
    work, primary = _make_pair(tmp_path)
    second = tmp_path / "second.git"
    _git("init", "--bare", "-q", str(second), cwd=tmp_path)
    # TWO pushurls explicitly: `set-url --push` pins the first, `--add --push` appends the second
    # (`--add` alone leaves a single pushurl, which would make this test exercise one target).
    _git("remote", "set-url", "--push", "origin", str(primary), cwd=work)
    _git("remote", "set-url", "--add", "--push", "origin", str(second), cwd=work)
    pushurls = _git("remote", "get-url", "--push", "--all", "origin", cwd=work).stdout.split()
    assert len(pushurls) == 2, f"the fixture must define two pushurls, got {pushurls}"
    head = _git("rev-parse", "HEAD", cwd=work).stdout.strip()

    class TwoTargets(Runner):
        def __init__(self):
            self.reads = 0
            self.deleted = False

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                self.reads += 1
                # Budget: exactly one verify read PER pushurl (2), then 2 cleanup reads after the
                # delete. A pre-delete read beyond that can only be a redundant standalone re-read
                # of a single target — and that read, not the per-target reads, is what must not
                # decide the verdict, so it is the one that fails.
                if self.reads > 2 and not self.deleted:
                    return Proc(128, err="stub: transient")
                if self.deleted:
                    return Proc(0, out="")
                return Proc(0, out=f"{head}\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" in argv:
                self.deleted = True
                return Proc(0, out="")
            if argv[1] == "push":
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=TwoTargets())
    assert verdict.writable is True, (
        f"a proven write must not be refused for a redundant re-read: {verdict.reason!r}"
    )


def test_bad_prefix_at_the_API_seam_is_a_usage_error_not_a_credential_verdict(tmp_path: Path):
    """`main()` refuses a bad prefix with exit 2; the programmatic seam must not blame the credential.

    Measured before: `check_writable(w, probe_ref_prefix="a b")` returned
    `GH-WRITE-UNAVAILABLE(push-refused)` on a fully writable remote.
    """
    work, _remote = _make_pair(tmp_path)
    for bad in ("a b", "x*", "-f"):
        try:
            check_writable(work, probe_ref_prefix=bad)
        except ValueError:
            continue
        raise AssertionError(f"prefix {bad!r} must raise, not return a verdict")


def test_an_exception_after_the_push_still_deletes_the_probe_ref(tmp_path: Path):
    """A Ctrl-C (`SIGINT`) after the push has landed must not leave the ref on a shared remote.

    Measured before: `KeyboardInterrupt` raised on the verify read escaped `check_writable` and the
    remote still held `refs/heads/gh-write-preflight/…`, while the module promises it is always
    cleaned up.

    ⛔ The name is deliberately `SIGINT`, not `SIGTERM`: this case raises `KeyboardInterrupt`, which
    is what Python's `SIGINT` handler produces. `SIGTERM` has no default handler in Python, so it
    terminates WITHOUT unwinding and the `finally` under test never runs — that residual is stated
    in the module docstring and pinned by
    `test_the_cleanup_contract_does_not_claim_SIGTERM_coverage`, so a reader does not infer coverage
    this case cannot show.
    """
    work, remote = _make_pair(tmp_path)
    deleted = []

    class Interrupted(Runner):
        def __init__(self):
            self.pushed = False

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "push" and "--delete" not in argv:
                self.pushed = True
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            if argv[1] == "push" and "--delete" in argv:
                deleted.append(argv[-1])
                return Proc(0, out="")
            if self.pushed and argv[1] == "ls-remote":
                raise KeyboardInterrupt
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    with contextlib.suppress(KeyboardInterrupt):
        check_writable(work, runner=Interrupted())
    assert deleted, "the probe ref was NOT deleted after an interrupt"
    assert "gh-write-preflight" not in _remote_refs(remote)


def test_the_cleanup_contract_does_not_claim_SIGTERM_coverage():
    """The cleanup prose must claim `SIGINT` only, and STATE the `SIGTERM` residual.

    The contract said the probe ref "is **always** cleaned up" and named "a Ctrl-C, a fleet SIGTERM
    or any exception" as covered, while the mechanism is a `try/finally`. Python installs default
    handlers for only `SIGPIPE` (ignored) and `SIGINT` (→ `KeyboardInterrupt`), so a `SIGTERM` — what
    a fleet watchdog sends — terminates WITHOUT unwinding: no `finally`, no delete, no output, and
    the probe ref stays on a remote every lane shares. The code cannot show that alongside the fix
    (a test would have to kill its own process), so the CLAIM is pinned at the source, exactly as
    the known-survivor Contract sentence is pinned above: the defect is the prose, and nothing
    behavioural reds on a relapse of it.
    """
    source = (Path(__file__).resolve().parent.parent / "tools" / "gh_write_preflight.py").read_text()
    flat = " ".join(source.split())
    assert "a Ctrl-C, a fleet SIGTERM or any exception" not in flat, (
        "the cleanup comment must not claim SIGTERM coverage — a try/finally cannot deliver it"
    )
    assert "**always** cleaned up" not in flat, (
        "the contract must not say the ref is ALWAYS cleaned up while SIGTERM escapes the finally"
    )
    # The safe side: the narrowing is stated, and the residual is visible to a lane reading the
    # docstring — never merely deleted, which would leave the gap silent.
    assert "every path that unwinds the interpreter" in flat, (
        "the cleanup guarantee must be narrowed to the paths a finally can actually cover"
    )
    assert "STATED RESIDUAL" in flat and "a probe killed by" in flat, (
        "the SIGTERM hole must be a STATED RESIDUAL, not an unstated one"
    )
    assert "SIGTERM" in flat, "the residual must NAME the signal that escapes the finally"


def test_ambient_GIT_CONFIG_slots_are_NOT_clobbered(tmp_path: Path):
    """The neutralisation must count UP from the ambient count, not start at zero.

    A box that injects its credential through `GIT_CONFIG_*` (`url.<base>.insteadOf` +
    `credential.helper`) had every slot overwritten, which replaced the working credential with
    git's fallback: measured `writable=False, push-refused` on a repo whose raw `git push` exited 0,
    and `credential.helper` silently changed. That is a false block.
    """
    work, _remote = _make_pair(tmp_path)
    # An ambient slot that the probe must NOT clobber: a harmless setting whose loss is observable.
    os.environ["GIT_CONFIG_COUNT"] = "1"
    os.environ["GIT_CONFIG_KEY_0"] = "ghwritepreflight.testslot"
    os.environ["GIT_CONFIG_VALUE_0"] = "kept"
    try:
        extra = _neutral_push_env("origin")
        merged = _child_env(extra)
    finally:
        for k in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
            os.environ.pop(k, None)

    # The ambient slot SURVIVES the merge — that is what "not clobbered" means.
    assert merged["GIT_CONFIG_KEY_0"] == "ghwritepreflight.testslot", (
        f"the ambient slot was overwritten: {merged.get('GIT_CONFIG_KEY_0')!r}"
    )
    assert merged["GIT_CONFIG_VALUE_0"] == "kept"
    assert int(merged["GIT_CONFIG_COUNT"]) > 1, "our settings must EXTEND the count, not replace it"
    assert "GIT_CONFIG_KEY_0" not in extra, "we must not occupy the ambient slot"
    assert any(
        v == "push.followTags" and k != "GIT_CONFIG_KEY_0"
        for k, v in merged.items()
        if k.startswith("GIT_CONFIG_KEY_")
    ), f"our settings must start ABOVE the ambient count: {merged}"
    # ...and a real probe still passes with an ambient slot present.
    assert check_writable(work, remote="origin").writable is True


def test_signed_push_and_push_options_config_do_not_fake_a_refusal(tmp_path: Path):
    """REAL git: `push.gpgSign` / `push.pushOption` make a writable remote refuse the push.

    Measured before: `push.gpgSign=true` -> `fatal: the receiving end does not support --signed
    push`; `push.pushOption=ci.skip` -> `does not support push options`; both surfaced as
    `push-refused` (= read-only credential) on a remote that writes fine.
    """
    for key, value in (("push.gpgSign", "true"), ("push.pushOption", "ci.skip")):
        work, remote = _make_pair(tmp_path / key.replace(".", "_"))
        _git("config", key, value, cwd=work)
        verdict = check_writable(work)
        assert verdict.writable is True, (
            f"{key}={value} must not be read as a credential refusal: "
            f"{verdict.reason!r} {verdict.detail!r}"
        )
        assert verdict.cleaned_up is True
        assert "gh-write-preflight" not in _remote_refs(remote)


def test_step_details_are_redacted_AT_INGESTION(tmp_path: Path):
    """The `redact()` inside `step()` is load-bearing and must be pinned.

    Deleting it left the suite green (a mutation survived a whole review), yet the refusal paths do
    `verdict.detail = verdict.steps[-1]["detail"]` and `main()` prints that unredacted, so a
    credential in a remote's error text would reach stderr. The existing
    `test_verdict_object_holds_no_credential` never puts a secret into a step detail.
    """
    work, _remote = _make_pair(tmp_path)
    secret_url = "https://example.invalid/x.git?access_token=SECRETSTEPTOKEN"

    class EchoesTheUrl(Runner):
        """Real git echoes the URL it failed on — including the query token."""

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "ls-remote":
                return Proc(128, err=f"fatal: unable to access '{secret_url}/': FAILED")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=EchoesTheUrl())

    assert verdict.reason == "remote-unreachable", verdict.reason
    blob = repr(verdict) + str(verdict.detail) + str(verdict.steps)
    assert "SECRETSTEPTOKEN" not in blob, "the credential reached a step detail or the verdict"
    assert "<redacted>" in blob or "userinfo-redacted" in blob


def test_a_query_parameter_credential_never_reaches_argv(tmp_path: Path):
    """`?access_token=…` is masked in the display, so it must also be kept off argv.

    Measured before: `_url_has_credential` scanned only the bare-token shapes, so the raw URL went
    to `git ls-remote` as an argument (0444 on a shared cell) while `redact()` hid it.
    """
    from tools.gh_write_preflight import _read_targets, _url_has_credential

    q = "https://example.invalid/x.git?access_token=SECRETQUERY123"
    assert _url_has_credential(q) is True, "a query credential is a credential"
    assert _read_targets("origin", "https://example.invalid/fetch.git", [q]) == [
        "gh-write-preflight-push0:"
    ], "the raw query url must be reached through the alias, never as data"
    # ...and an ordinary path/url is unaffected.
    assert _url_has_credential("https://example.invalid/x.git") is False
    assert _url_has_credential("/tmp/x/y.git") is False


def test_bare_userinfo_with_no_separator_is_redacted(tmp_path: Path):
    """`mytoken123@host.example` — detected as a credential, so it must be redacted too.

    Neither redaction pattern matched this shape, so the secret reached `--json`
    remote_url/push_url verbatim (measured). `git remote get-url` prints exactly this for a
    scheme-less remote.
    """
    assert _url_has_credential("mytoken123@host.example") is True
    assert "mytoken123" not in redact("mytoken123@host.example")
    assert redact("git@github.com") == "git@github.com", "the exempt user must survive"


def test_internal_error_does_not_name_a_ref_that_never_existed(tmp_path: Path):
    """No push step means no possible survivor — the note must not be invented.

    Measured before: an exception on the REACHABILITY read produced `steps: []` and still said the
    probe ref "may still be on origin", while the remote held only `refs/heads/main`.
    """
    work, _remote = _make_pair(tmp_path)

    class DiesEarly(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "ls-remote":
                raise RuntimeError("stub: reachability read died")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=DiesEarly())

    assert verdict.reason == "internal-error"
    assert not any(s["step"] == "push" for s in verdict.steps)
    assert "may still be on" not in (verdict.detail or ""), verdict.detail


def test_a_push_timeout_is_reported_as_UNKNOWN_not_as_a_refusal(tmp_path: Path):
    """rc 124 is OUR deadline, not the remote's answer.

    Without the note, a slow remote reads as "read-only credential" and the lane hunts a
    permissions problem that does not exist — the plausible-diagnosis-for-the-wrong-cause failure
    this tool exists to end.
    """
    work, _remote = _make_pair(tmp_path)

    class SlowPush(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "push" and "--delete" not in argv:
                return Proc(124, err="git: timed out after 120s")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=SlowPush())

    assert verdict.writable is False
    # A DISTINCT token: `push-refused` is published as "the credential is at fault", so branching on
    # it parked deliverable work on a slow push. A caller must be able to tell the two apart.
    assert verdict.reason == "push-unknown", verdict.reason
    assert "push-unknown" in REASONS
    assert "UNKNOWN" in REASONS["push-unknown"]
    assert "TIMED OUT" in (verdict.detail or "")


def test_a_SIGNALLED_push_is_reported_as_UNKNOWN_not_as_a_refusal(tmp_path: Path):
    """A killed push (rc < 0 — the OOM killer, documented on these cells) is not a refusal.

    Measured before: `Proc(-9, out="", err="")` produced `push-refused` with an EMPTY detail, so
    the only text a lane saw was "read-only credential, branch protection, or no network" — the
    plausible-diagnosis-for-the-wrong-cause failure the rc-124 note exists to prevent.
    """
    work, _remote = _make_pair(tmp_path)

    class Killed(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "push" and "--delete" not in argv:
                return Proc(-9, out="", err="")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=Killed())

    assert verdict.writable is False
    assert verdict.reason == "push-unknown", verdict.reason
    assert "UNKNOWN, not refused" in (verdict.detail or ""), verdict.detail


def test_the_retry_REREADS_and_records_its_step(tmp_path: Path):
    """The retry's bookkeeping must be observable, not thrown away.

    Mutation that survived a whole review cycle: making the retry set `cleaned_up = True` with NO
    re-read left all 74 tests green — so the "RE-READS state" claim was unpinned, and the same
    machinery is what the `internal-error` wrapper relies on to report a surviving probe ref.
    """
    work, _remote = _make_pair(tmp_path)

    class DiesThenDeletes(Runner):
        """Push lands, the verify read raises, and the retry's delete succeeds."""

        def __init__(self):
            self.pushed = False
            self.deleted = False

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "push" and "--delete" not in argv:
                self.pushed = True
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            if argv[1] == "push" and "--delete" in argv:
                self.deleted = True
                return Proc(0, out="")
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                if not self.deleted:
                    raise RuntimeError("stub: read died")
                return Proc(0, out="")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=DiesThenDeletes())

    assert verdict.reason == "internal-error"
    # The probe ref is NAMED — an operator can clean up what this reports.
    assert verdict.probe_ref, "the internal-error path must still name the probe ref"
    # The in-flight steps survived the exception (the verdict was threaded, not rebuilt).
    assert any(s["step"] == "push" for s in verdict.steps), verdict.steps
    # The retry ran AND re-read: only a post-retry read can produce `cleaned_up=True` here.
    assert any(s["step"] == "delete-retry" for s in verdict.steps), verdict.steps
    assert verdict.cleaned_up is True, (
        "the retry's effect must be READ BACK, not assumed"
    )
    assert "may still be on" not in (verdict.detail or ""), (
        "a confirmed delete must not warn about litter"
    )


def test_internal_error_names_the_ref_when_it_may_survive(tmp_path: Path):
    """When the retry cannot clean up, the verdict must SAY the ref may still be there."""
    work, _remote = _make_pair(tmp_path)

    class NeverDeletes(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "push" and "--delete" not in argv:
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            if argv[1] == "push" and "--delete" in argv:
                return Proc(1, err="stub: denied")
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                raise RuntimeError("stub: read died")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=NeverDeletes())

    assert verdict.reason == "internal-error"
    assert verdict.probe_ref
    assert "may still be on" in (verdict.detail or ""), verdict.detail
    assert verdict.writable is False


def test_keep_ref_on_a_refused_push_does_not_claim_a_survivor(tmp_path: Path):
    """`cleaned_up=False` documents a PROVEN SURVIVOR. With nothing created it must be None."""
    work, _remote = _make_pair(tmp_path, read_only=True)

    verdict = check_writable(work, keep_ref=True)

    assert verdict.writable is False
    assert verdict.kept_ref is True
    assert verdict.cleaned_up is None, (
        f"nothing was created, so there is no survivor to report: {verdict.cleaned_up}"
    )


def test_keep_ref_on_an_up_to_date_push_does_not_claim_a_survivor(tmp_path: Path):
    """`push_ok` is not evidence of a write: "Everything up-to-date" exits 0 having created nothing.

    Keying the enum on `push_ok` reported `cleaned_up=False` (documented as a PROVEN SURVIVOR) for a
    run with no write, beside `leftover_ref=False`.
    """
    work, _remote = _make_pair(tmp_path)

    class UpToDate(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "push":
                return Proc(0, out="=")  # porcelain: up to date, NO write
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=UpToDate(), keep_ref=True)

    assert verdict.reason == "push-refused"
    assert verdict.kept_ref is True
    assert verdict.cleaned_up is None, (
        f"no write happened, so there is no survivor: {verdict.cleaned_up}"
    )
    assert verdict.leftover_ref is False


def test_a_push_follow_tags_style_config_cannot_widen_the_probe(tmp_path: Path):
    """REAL git: an annotated tag at HEAD must NOT be pushed by the probe.

    With `push.followTags=true` the probe pushed the tag to the shared remote too — a mutation
    BEYOND the probe ref that survived cleanup while the verdict still said `cleaned_up: true`
    (measured), and a tag push is exactly what `publish-pypi` (`tags: v*`) triggers on.
    """
    work, remote = _make_pair(tmp_path)
    _git("tag", "-a", "v9.9.9", "-m", "probe must not push me", cwd=work)
    _git("config", "push.followTags", "true", cwd=work)

    verdict = check_writable(work)

    assert verdict.writable is True, verdict.detail
    assert verdict.cleaned_up is True
    tags = _git("ls-remote", "--tags", str(remote), cwd=remote.parent).stdout.strip()
    assert "v9.9.9" not in tags, f"the probe pushed a TAG to the shared remote: {tags!r}"


def test_a_mirrored_remote_is_not_reported_as_a_credential_refusal(tmp_path: Path):
    """`remote.origin.mirror=true` makes `git push <refspec>` fail — that is CONFIG, not the token."""
    work, _remote = _make_pair(tmp_path)
    _git("config", "remote.origin.mirror", "true", cwd=work)

    verdict = check_writable(work)

    assert verdict.writable is True, (
        f"a mirrored remote must not be read as a read-only credential: {verdict.reason!r} "
        f"{verdict.detail!r}"
    )


def test_the_retry_reports_a_REFUSED_delete_as_a_survivor(tmp_path: Path):
    """The retry's delete can itself be refused, and the read-back can say PRESENT.

    Mutation that survived: setting `cleaned_up = True` on the retry WITHOUT reading left every
    test green — so the re-read was unpinned. Here the delete claims rc 0 but the ref is still
    there, so only a real read catches it.
    """
    work, _remote = _make_pair(tmp_path)

    class LyingDelete(Runner):
        def __init__(self):
            self.deleted = False

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "push" and "--delete" not in argv:
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            if argv[1] == "push" and "--delete" in argv:
                return Proc(0, out="")  # claims success; the ref never goes away
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                if not self.deleted:
                    self.deleted = True
                    raise RuntimeError("stub: first read died")
                return Proc(0, out="a" * 40 + "\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=LyingDelete())

    assert verdict.reason == "internal-error"
    assert verdict.cleaned_up is not True, (
        "the retry must READ BACK; a delete that merely exits 0 does not prove removal"
    )
    assert "may still be on" in (verdict.detail or ""), verdict.detail


def test_password_containing_an_at_sign_is_redacted():
    """`user:p@ss@host:path` — the password holds the separator character itself.

    The first-`@` redaction form matched nothing, so the tool classified this as a credential and
    then printed it (measured: `--json` showed it in remote_url AND push_url).
    """
    out = redact("user:p@ss@example.invalid:repo.git")
    assert "p@ss" not in out, out
    assert "example.invalid" in out, "the host must survive so the remote is still identifiable"
    # The SCHEME form is the same leak through a different pattern: a single-userinfo match stopped
    # at the first `@` and printed the rest of the password (measured: 12 chars).
    for scheme_form in (
        "https://deploy:p@ssw0rdS3cret@example.invalid/o/r.git",
        "ssh://deploy:p@ssw0rdS3cret@example.invalid/o/r.git",
    ):
        got = redact(scheme_form)
        assert "ssw0rdS3cret" not in got, f"partial password printed: {got}"
        assert "p@ss" not in got, f"password printed: {got}"
    # A host with an underscore, and the scheme-less `host/path` form, were detected but printed.
    assert "hunter2" not in redact("user:hunter2@my_host.example:repo.git")
    assert "hunter2" not in redact("user:hunter2@host.example/repo.git")
    # A plain address with a space after the colon is prose, not a URL — leave it alone.
    assert redact("write to a@b.com: please") == "write to a@b.com: please"


def test_the_retry_does_NOT_run_on_the_ordinary_delete_failed_path(tmp_path: Path):
    """Retry only while an exception is propagating, and read the result back.

    Measured before: guarding on the verdict alone made the retry delete the ref on the ordinary
    `delete-failed` path without re-reading, so the verdict claimed `leftover_ref=True` / "PROBE REF
    SURVIVED" for a remote that was already clean — a false block plus a false litter claim.
    """
    work, _remote = _make_pair(tmp_path)
    deletes = []

    class FirstDeleteDenied(Runner):
        """The delete is refused ONCE, then a retry would succeed if one were attempted."""

        def __init__(self):
            self.deleted = False

        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                return Proc(0, out="" if self.deleted else "a" * 40 + "\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" in argv:
                deletes.append(argv[-1])
                if len(deletes) == 1:
                    return Proc(1, err="stub: denied once")
                self.deleted = True
                return Proc(0, out="")
            if argv[1] == "push":
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=FirstDeleteDenied())

    assert len(deletes) == 1, f"the retry must not run on a normal path; saw {deletes}"
    assert verdict.cleaned_up is False
    assert verdict.leftover_ref is True, "with no retry, the verdict must report the survivor"


def test_verify_mismatch_is_not_rendered_as_an_ok_step(tmp_path: Path):
    """A step whose outcome decides a refusal must not print `[ok]`.

    Measured before: the step's rc was keyed on readability, so a verify-mismatch rendered
    `[ok] ls-remote-verify (rc=0)` directly above `GH-WRITE-UNAVAILABLE(verify-mismatch)`.
    """
    work, _remote = _make_pair(tmp_path)

    class WrongSha(Runner):
        def run(self, argv, cwd=None, timeout=120, env_extra=None):
            if argv[1] == "ls-remote" and "refs/heads/" in " ".join(argv):
                return Proc(0, out="a" * 40 + "\trefs/heads/gh-write-preflight/x\n")
            if argv[1] == "ls-remote":
                return Proc(0, out="")
            if argv[1] == "push" and "--delete" not in argv:
                return Proc(0, out="*\tHEAD:refs/heads/gh-write-preflight/x\t[new branch]\n")
            if argv[1] == "push":
                return Proc(0, out="")
            return super().run(argv, cwd=cwd, timeout=timeout, env_extra=env_extra)

    verdict = check_writable(work, runner=WrongSha())
    verify_steps = [s for s in verdict.steps if s["step"] == "ls-remote-verify"]
    assert verify_steps and verify_steps[0]["rc"] == 1, verify_steps
    assert verdict.reason == "verify-mismatch"


def test_keep_ref_json_never_advertises_cleanup(tmp_path: Path, capsys):
    """`--keep-ref` must say `cleaned_up: false` in the MACHINE payload, not just in prose.

    Mutation that escaped: forcing `cleaned_up = True` on the keep-ref path left every test green,
    because the only assertion read the word "KEPT" from stdout.
    """
    work, _remote = _make_pair(tmp_path)
    rc = main(["--repo", str(work), "--keep-ref", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == EXIT_WRITABLE
    assert payload["kept_ref"] is True
    assert payload["cleaned_up"] is False, "a deliberately kept ref is not a clean cleanup"
    assert payload["leftover_ref"] is False, "kept_ref is not an accidental survivor"


def test_the_carve_out_guard_is_the_LAST_block_in_this_file():
    """Nothing at all may follow the `__main__` block that supplies the carve-out.

    Measured at the pre-fix head: 26 cases sat below it. `pytest` RE-IMPORTS the module, so they
    were still collected and nothing was red — which is exactly why the drift is invisible: the
    guard stops being "the one block" (the shape that silently defeated
    `tests/test_closes_landing.py`'s carve-out with a second mid-file copy) while every check stays
    green.

    ⛔ NO NAME HEURISTICS — BOTH EARLIER VERSIONS OF THIS PIN REPORTED PASS ON THE DRIFT.

    * v1 was a substring test (`"\\ndef test_" not in below`) and measured GREEN on a
      `class TestX:` (its methods are indented) and on a module-level `test_x = lambda: ...`.
    * v2 walked the AST for `test*`-named defs/classes/assignments and still measured GREEN on
      `class TestZ(MixinTests): pass` — pytest collects capital-T classes and INHERITED methods,
      so the case below the guard was collected while the pin passed — and on
      `globals()["test_foo"] = ...`, `exec("def test_e(): ...")`, `setattr(sys.modules[__name__],
      ...)` and `type("TestC", (), {...})`, none of which is a `def` node at all. It also
      OVER-blocked a plain local named `test_cmd` inside the guard's own body, because the guard's
      `if` line made its own body "below".

    The invariant that needs no knowledge of what pytest collects: the guard is the LAST node of
    the module. That is what `tests/test_closes_landing.py` records, and it is decidable from the
    AST in one comparison.
    """
    tree = ast.parse(Path(__file__).read_text())
    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test)
    ]
    assert len(guards) == 1, f"expected exactly one `__main__` block, found {len(guards)}"
    assert tree.body[-1] is guards[0], (
        "the `__main__` carve-out guard must be the LAST statement in this file — anything after "
        "it can be collected by pytest or shadow the guard: "
        f"{ast.unparse(tree.body[-1])[:90]!r}"
    )
    # ⛔ POSITION IS NOT ENOUGH: an `else:`/`elif:` clause is INSIDE the guard node, so it is not a
    # later `tree.body` element — and `orelse` is what executes on import while `body` never runs.
    # Measured: `if __name__ == "__main__": … else: def test_else_branch()…` left the positional
    # assert GREEN and pytest collected the case (`1/93 tests collected`).
    assert not guards[0].orelse, (
        "the `__main__` carve-out guard must carry no `else:`/`elif:` clause — that branch runs on "
        "import, so a case defined there is collected while the guard is not the whole story: "
        f"{ast.unparse(guards[0].orelse[0])[:90]!r}"
    )


if __name__ == "__main__":
    # THE ONE BLOCK that makes the docstring's `Run standalone:` line real. The repo's session-start
    # gate (`_p4_uri_required`, epic #1647 P4) refuses a URI-less pytest session, so the advertised
    # command used to report 55 session-gate ERRORS for a suite that opens no graph at all — the
    # carve-out is exactly right here and is not optional. `setdefault`, so an explicit value in the
    # caller's env still wins. Same one-block fix, same reasoning, as
    # tests/test_closes_landing.py's `__main__` (where a second mid-file copy once silently defeated
    # it). Pinned by test_documented_standalone_command_actually_runs.
    #
    # ⛔ IT STAYS THE **LAST** BLOCK IN THE FILE. Every case lives above this line on purpose: a test
    # appended below it re-creates the shape that silently defeated `tests/test_closes_landing.py`'s
    # carve-out, and reads as "the guard is the one place, except here". (`pytest` re-imports the
    # module so such a case is still collected, which is exactly why the drift is invisible until
    # the guard stops being the only copy.)
    os.environ.setdefault("TORTOISE_TEST_CARVE_OUT", "1")
    raise SystemExit(pytest.main([__file__, "-q"]))
