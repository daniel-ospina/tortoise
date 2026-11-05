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
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.gh_write_preflight import (  # noqa: E402
    EXIT_UNAVAILABLE,
    EXIT_WRITABLE,
    REASONS,
    Proc,
    Runner,
    _child_env,
    _ref_state,
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


def _make_pair(tmp_path: Path, *, read_only: bool = False) -> tuple[Path, Path]:
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
        def run(self, argv, cwd=None, timeout=120):
            if argv[0] == "git" and argv[1] == "--version":
                return Proc(127, err="git: not found")
            return super().run(argv, cwd=cwd, timeout=timeout)

    verdict = check_writable(work, runner=NoGit())

    assert verdict.writable is False
    assert verdict.reason == "git-absent"


# ══ (6) the marker contract is TOTAL: no path exits 1 without a REASONS token ═════════════════
def test_unexpected_exception_becomes_internal_error_not_none(tmp_path: Path, capsys):
    """A `return` inside `finally` used to SWALLOW an exception and emit `(None)`."""
    work, _remote = _make_pair(tmp_path)
    boom = {"fired": False}

    class Boom(Runner):
        def run(self, argv, cwd=None, timeout=120):
            if argv[1] == "push" and "--delete" not in argv:
                boom["fired"] = True
                raise RuntimeError("simulated crash")
            return super().run(argv, cwd=cwd, timeout=timeout)

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


def test_oserror_is_mapped_not_raised(tmp_path: Path):
    """EMFILE/ENOMEM-class OSError must not escape as an unmarked traceback."""
    work, _remote = _make_pair(tmp_path)

    class OSFail(Runner):
        def run(self, argv, cwd=None, timeout=120):
            if argv[1] == "rev-parse":
                return Proc(126, err=f"{argv[0]}: [Errno 24] Too many open files")
            return super().run(argv, cwd=cwd, timeout=timeout)

    verdict = check_writable(work, runner=OSFail())

    assert verdict.writable is False
    assert verdict.reason == "not-a-repo"  # rev-parse failed → clean, named refusal


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


def test_push_refused_but_remote_accepted_reports_leftover_without_erasing_the_cause(tmp_path: Path):
    """Push may report failure AFTER the remote applied the update — cleanup must still run.

    Simulated: push returns 1, the ref IS on the remote, and the delete ALSO fails. The ref
    survives, so `leftover_ref` must be True while `reason` stays `push-refused` — a caller has to
    learn BOTH that its lanes cannot deliver and that there is litter. A regression that overwrote
    `push-refused` with `delete-failed` fails here.
    """
    state = {"ref_exists": False, "delete_attempted": False}

    class TransportLie(Runner):
        def run(self, argv, cwd=None, timeout=120):
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
            return super().run(argv, cwd=cwd, timeout=timeout)

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
        def run(self, argv, cwd=None, timeout=120):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                return Proc(0, out="")
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push":
                return Proc(1, err="remote: denied")
            return super().run(argv, cwd=cwd, timeout=timeout)

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
        def run(self, argv, cwd=None, timeout=120):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                return Proc(128, err="fatal: could not read from remote")
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                return Proc(1, err="deletion denied")
            if argv[1] == "push":
                return Proc(0, out="pushed")
            return super().run(argv, cwd=cwd, timeout=timeout)

    work, _remote = _make_pair(tmp_path)
    verdict = check_writable(work, runner=Unreadable())

    assert verdict.cleaned_up is None, "unconfirmed cleanup must be None, never True"
    assert verdict.leftover_ref is False
    assert "UNCONFIRMED" in verdict.detail


def test_ref_state_is_tristate(tmp_path: Path):
    class R(Runner):
        def __init__(self, rc, out):
            self._rc, self._out = rc, out

        def run(self, argv, cwd=None, timeout=120):
            return Proc(self._rc, out=self._out)

    assert _ref_state(R(0, "abc\trefs/heads/x\n"), ".", "origin", "x") == "present"
    assert _ref_state(R(0, ""), ".", "origin", "x") == "absent"
    assert _ref_state(R(128, ""), ".", "origin", "x") == "unverifiable"


def test_verify_mismatch_still_attempts_cleanup(tmp_path: Path):
    """A push that 'succeeds' but leaves no visible ref must still be cleaned up."""
    state = {"delete_attempted": False}

    class LyingPush(Runner):
        def run(self, argv, cwd=None, timeout=120):
            joined = " ".join(argv)
            if argv[1] == "ls-remote" and _PROBE in joined:
                return Proc(0, out="")            # nothing visible -> verify-mismatch
            if argv[1] == "ls-remote":
                return Proc(0, out="deadbeef\trefs/heads/main\n")
            if argv[1] == "push" and "--delete" in argv:
                state["delete_attempted"] = True
                return Proc(0, out="deleted")
            if argv[1] == "push":
                return Proc(0, out="pushed")
            return super().run(argv, cwd=cwd, timeout=timeout)

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
        def run(self, argv, cwd=None, timeout=120):
            p = super().run(argv, cwd=cwd, timeout=timeout)
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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
