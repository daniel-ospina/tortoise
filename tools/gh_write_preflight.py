#!/usr/bin/env python3
"""gh_write_preflight — "can this credential WRITE repository contents here?", answered before
a lane spends an hour it cannot deliver.

Why this exists
---------------
gh resolves its credential in a fixed order (GitHub's own manual, `gh help environment`):
``GH_TOKEN`` / ``GITHUB_TOKEN`` — *"takes precedence over previously stored credentials"* — and
only then ``$HOME/.config/gh/hosts.yml``. A cell that exports a **read-only** token as ``GH_TOKEN``
therefore never reaches the write-capable OAuth token sitting in ``hosts.yml``, and every lane on
that cell can read and comment but can open nothing.

Measured consequence (the reason this tool is not optional): a **17-lane wave produced zero
landings** while every lane honestly reported "built, parked". Nothing before the push said the
credential could not push, so the failure surfaced only at the end of each lane's work.

Why `gh auth status` is NOT the answer
-------------------------------------
``gh auth status`` exits 0 for a read-only credential: it reports that the credential exists and is
valid, never that it can write.

Why `gh api repos/<o>/<r> --jq .permissions.push` is NOT the answer either
------------------------------------------------------------------------
That field is documented as *the permissions **the authenticated user** has on the repository*, not
the permissions the **token** was granted. A fine-grained PAT with ``contents: read`` authenticates
AS THE USER, so ``.permissions.push`` reads ``true`` for an account with push/admin on the repo
while every ``git push`` it makes is refused. That is a fail-open, and it is the shape
`cloud-cell-bringup.sh` relies on in agent-infra (patched separately).

The only ground truth is the operation itself: write a ref, read it back, delete it. This tool does
that against the SAME remote and the SAME credential path a lane's real `git push` uses (the git
credential helper, which on these boxes is `!gh auth git-credential`) — so a PASS here is evidence
about the deliverable path, not about a proxy for it.

Contract
--------
* Exit **0** — the credential can write: a throwaway branch was created, confirmed by
  ``git ls-remote``, and deleted.
* Exit **1** — ``GH-WRITE-UNAVAILABLE(<reason>)`` on stderr (emitted in ``--json`` mode too; stdout
  stays pure JSON). Reasons are stable tokens, listed in ``REASONS``, so a caller can branch on them
  without parsing prose.
* Exit **2** — usage error (argparse's own code, deliberately not reused for a verdict).

Safety
------
* **The child environment is stripped of every ``GIT_*`` variable.** Git's own state variables
  (``GIT_DIR``, ``GIT_WORK_TREE``, ``GIT_INDEX_FILE``, ``GIT_OBJECT_DIRECTORY``, ``GIT_NAMESPACE``,
  …) redirect repository resolution away from ``--repo``; inheriting them made this tool report
  ``writable=True`` about a DIFFERENT, writable repository than the one asked for — a false PASS of
  exactly the class it exists to remove (measured; pinned by
  ``test_git_dir_cannot_redirect_the_probe``). Stripping them also removes ``GIT_TRACE`` /
  ``GIT_CURL_VERBOSE``, which would otherwise route git's own ``Authorization:`` header into our
  output.
* The probe ref is namespaced (``gh-write-preflight/<utc>-<pid>-<rand>``) so it cannot collide with
  a real branch, and it is **always** cleaned up — including when the push *reports* failure, since
  a transport error can arrive after the remote applied the update. Cleanup is verified by
  ``ls-remote``, not assumed, and a ref that survives DEMOTES the verdict.
* The probe pushes the commit already at ``HEAD`` (``HEAD:refs/heads/<probe>``). It does not create
  a commit and does not touch the working tree or index, so it is safe to run in a dirty worktree.
* A credential never reaches the output: redaction happens **at ingestion** (``step()`` stores an
  already-redacted detail, ``remote_url`` is stored redacted), so no render path can forget it.
* A branch push does not trigger CI in this repo (no workflow has an unfiltered ``on: push``;
  ``publish-pypi`` is ``tags: v*`` only). That is a property of THIS repo: the tool is generic via
  ``--repo``, so probing another checkout inherits that repo's triggers.

Usage
-----
    uv run python tools/gh_write_preflight.py                # probe this checkout's origin
    uv run python tools/gh_write_preflight.py --json         # machine-readable verdict
    uv run python tools/gh_write_preflight.py --repo <path>  # probe another checkout

Enablement in this repo: tests/test_gh_write_preflight.py (red-first: a rejecting remote MUST
produce exit 1; an accepting remote MUST produce exit 0 and leave no probe ref behind).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import string
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Exit codes. 0/1 are the verdict; 2 is argparse's usage code.
EXIT_WRITABLE = 0
EXIT_UNAVAILABLE = 1

# Stable reason tokens. A caller may key on these; never invent one outside this set.
REASONS = {
    "git-absent": "the git binary is not on PATH",
    "not-a-repo": "the target is not a directory inside a git work tree",
    "no-remote": "the checkout has no remote of that name",
    "no-head": "HEAD does not resolve to a commit (no pushable object)",
    "remote-unreachable": "the remote refused or failed `ls-remote`",
    "push-refused": "`git push` was refused (read-only credential, branch protection, or no network)",
    "verify-mismatch": "the push reported success but `ls-remote` does not show the ref",
    "delete-failed": "the probe ref could not be deleted; it was left on the remote",
}

# ── redaction ──────────────────────────────────────────────────────────────────────────────────
# Applied at INGESTION (see `step()`), so every later render is safe by construction. Deliberately
# broad: a false redaction costs one diagnostic, a leak costs a credential.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # userinfo in a URL — ANY scheme, plus scheme-relative `//user:pw@host`. The leftmost
    # alternative wins, so `https://…` keeps its scheme and `ssh://`/`git://` are covered too.
    (re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://|//)[^@/\s]+@"), r"\1<userinfo-redacted>@"),
    # git's own HTTP auth header — reachable when GIT_TRACE/GIT_CURL_VERBOSE was in the env.
    (re.compile(r"(?i)\b(authorization:\s*\S+)\s+\S+"), r"\1 <redacted>"),
    # `token = …`, `api_token: …`, `DEEPSEEK_API_KEY=…` and friends.
    (
        re.compile(r"(?i)([A-Za-z0-9_.\-]*(?:token|password|secret|passwd|api_?key))(\s*[=:]\s*)\S+"),
        r"\1\2<redacted>",
    ),
    # Bare token-shaped runs.
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{8,})"), "<redacted>"),
    (re.compile(r"\b(github_pat_[A-Za-z0-9_]{8,})"), "<redacted>"),
)


def redact(text: str) -> str:
    """Strip credentials from any text this tool emits or stores."""
    out = text
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


@dataclass
class Verdict:
    """The answer. `writable` is the only field a caller should branch on.

    Every string field is stored ALREADY REDACTED (see `check_writable`), so a caller that logs,
    reprs, or prints this object cannot leak a credential. `redact()` is still applied on render as
    a belt, not as the only guard.
    """

    writable: bool
    reason: str | None = None
    detail: str = ""
    repo_root: str | None = None
    remote: str | None = None
    remote_url: str | None = None
    probe_ref: str | None = None
    head_sha: str | None = None
    steps: list[dict] = field(default_factory=list)
    cleaned_up: bool | None = None
    kept_ref: bool = False
    # A probe ref that SURVIVED on the remote. Reported ALONGSIDE `reason` rather than replacing it:
    # a leftover is an operational problem, but it must not erase the primary verdict (a credential
    # that cannot write). Overwriting `push-refused` with `delete-failed` is how a caller learns
    # about litter and never learns its lanes cannot deliver.
    leftover_ref: bool = False

    def as_json(self) -> str:
        return json.dumps(
            {
                "writable": self.writable,
                "reason": self.reason,
                "reason_meaning": REASONS.get(self.reason or "", ""),
                "detail": redact(self.detail),
                "repo_root": self.repo_root,
                "remote": self.remote,
                "remote_url": redact(self.remote_url or ""),
                "probe_ref": self.probe_ref,
                "head_sha": self.head_sha,
                "cleaned_up": self.cleaned_up,
                "kept_ref": self.kept_ref,
                "leftover_ref": self.leftover_ref,
                "steps": [
                    {**s, "detail": redact(s.get("detail", ""))} for s in self.steps
                ],
            },
            indent=2,
        )


# Git's own state variables redirect WHICH repository git operates on, regardless of `cwd`. Any of
# them inherited from the caller would let the probe describe a different repo than `--repo` — a
# false PASS. They are stripped wholesale (prefix match, so a variable added by a future git is
# covered too), and `GIT_TRACE`/`GIT_CURL_VERBOSE` go with them, which also keeps git's
# `Authorization:` header out of our output.
_GIT_ENV_PREFIX = "GIT_"


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(_GIT_ENV_PREFIX)}
    # Never let git prompt for a credential: a preflight blocked on an interactive username prompt
    # is a hung lane, not a verdict.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "true"
    return env


class Runner:
    """A thin, testable seam over subprocess.

    Tests inject a Runner subclass rather than monkeypatching `subprocess`; the real implementation
    is the only thing that ever runs git for real.
    """

    def run(self, argv: list[str], cwd: str | None = None, timeout: int = 120) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_child_env(),
            )
        except FileNotFoundError:
            # Covers BOTH a missing binary and a `cwd` that does not exist; the caller
            # distinguishes them (see check_writable's is_dir() guard) so the detail stays true.
            return 127, f"{argv[0]}: not found"
        except NotADirectoryError as exc:
            return 126, f"cannot enter working directory: {exc}"
        except PermissionError as exc:
            return 126, f"permission denied: {exc}"
        except subprocess.TimeoutExpired:
            return 124, f"{argv[0]}: timed out after {timeout}s"
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _probe_ref() -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"gh-write-preflight/{stamp}-{os.getpid()}-{rand}"


def _ref_present(runner: Runner, root: str, remote: str, ref: str) -> bool:
    """Does `ref` exist on `remote` right now? Used to tell a no-op delete from a real leftover."""
    rc, out = runner.run(["git", "ls-remote", remote, f"refs/heads/{ref}"], cwd=root)
    return rc == 0 and bool(out.strip())


def check_writable(
    repo: str | Path = ".",
    remote: str = "origin",
    runner: Runner | None = None,
    keep_ref: bool = False,
) -> Verdict:
    """Answer "can this credential write repository contents here?".

    Returns a Verdict. Never raises for an expected failure; every refusal carries a `REASONS` token
    so no path can exit 1 without a marker.
    """
    runner = runner or Runner()
    repo_path = Path(repo).expanduser()

    # (0) git present?
    rc, out = runner.run(["git", "--version"])
    if rc != 0:
        return Verdict(False, "git-absent", detail=redact(out))

    # (1) the target must be a directory. Checked FIRST so a bad `--repo` cannot masquerade as
    # "git is missing" (a nonexistent cwd raises FileNotFoundError inside subprocess).
    if not repo_path.is_dir():
        return Verdict(
            False, "not-a-repo", detail=f"not a directory: {repo_path}"
        )

    # (2) is this a work tree, and where is its root?
    rc, out = runner.run(["git", "rev-parse", "--show-toplevel"], cwd=str(repo_path))
    if rc != 0:
        return Verdict(False, "not-a-repo", detail=redact(out))
    root = out.strip().splitlines()[-1].strip()

    # (3) does the named remote exist?
    rc, out = runner.run(["git", "remote", "get-url", remote], cwd=root)
    if rc != 0:
        return Verdict(
            False, "no-remote", detail=redact(out), repo_root=root, remote=remote
        )
    url = redact(out.strip())  # stored redacted at ingestion

    # (4) HEAD must resolve — we push an EXISTING commit, never a new one.
    rc, out = runner.run(["git", "rev-parse", "HEAD"], cwd=root)
    if rc != 0:
        return Verdict(
            False,
            "no-head",
            detail=redact(out),
            repo_root=root,
            remote=remote,
            remote_url=url,
        )
    head = out.strip().splitlines()[-1].strip()

    verdict = Verdict(
        False,
        None,
        repo_root=root,
        remote=remote,
        remote_url=url,
        head_sha=head,
        probe_ref=_probe_ref(),
    )

    def step(name: str, rc: int, out: str) -> bool:
        # REDACT AT INGESTION. Nothing downstream has to remember.
        verdict.steps.append({"step": name, "rc": rc, "detail": redact(out[-800:])})
        return rc == 0

    # (5) reachability. A remote we cannot even read is not a write verdict — say so distinctly,
    # because "network is down" and "credential is read-only" call for different fixes.
    if not step("ls-remote", *runner.run(["git", "ls-remote", "--heads", remote], cwd=root)):
        verdict.reason, verdict.detail = "remote-unreachable", verdict.steps[-1]["detail"]
        return verdict

    push_attempted = False
    try:
        # (6) THE REAL WRITE. Push the commit already at HEAD onto a namespaced throwaway ref.
        push_attempted = True
        push_ok = step(
            "push",
            *runner.run(
                ["git", "push", "--no-verify", remote, f"HEAD:refs/heads/{verdict.probe_ref}"],
                cwd=root,
            ),
        )

        if not push_ok:
            # The defect this tool exists to catch: a read-only credential fails HERE. Do NOT
            # conclude the ref is absent — a transport error can arrive AFTER the remote applied
            # the update, so cleanup below still runs and is verified by ls-remote.
            verdict.reason = "push-refused"
            verdict.detail = verdict.steps[-1]["detail"]
            return verdict

        # (7) Confirm on the REMOTE, not from push's own word.
        rc, out = runner.run(
            ["git", "ls-remote", remote, f"refs/heads/{verdict.probe_ref}"], cwd=root
        )
        step_ok = step("ls-remote-verify", rc, out)
        seen = out.split()[0] if step_ok and out.strip() else ""
        if not step_ok or seen != head:
            verdict.reason = "verify-mismatch"
            verdict.detail = (
                f"push reported success but ls-remote returned {seen or '<nothing>'} "
                f"for refs/heads/{verdict.probe_ref} (expected {head})"
            )
            return verdict
        verdict.writable = True
    finally:
        # ALWAYS clean up once a push was attempted — on every path, including the failures.
        if not push_attempted:
            verdict.cleaned_up = True
            return verdict if not verdict.writable else verdict
        if keep_ref:
            verdict.kept_ref = True
            verdict.cleaned_up = False
            return verdict if not verdict.writable else verdict

        rc, out = runner.run(
            ["git", "push", "--no-verify", remote, "--delete", verdict.probe_ref], cwd=root
        )
        step("delete", rc, out)
        if rc == 0:
            verdict.cleaned_up = True
        else:
            # A delete "fails" harmlessly when there was no ref to delete (the push was refused).
            # Only a ref that actually SURVIVES is a leftover, and only that demotes the verdict.
            survived = _ref_present(runner, root, remote, verdict.probe_ref)
            verdict.cleaned_up = not survived
            if survived:
                verdict.writable = False
                verdict.leftover_ref = True
                if verdict.reason is None:
                    verdict.reason = "delete-failed"
                note = (
                    f"PROBE REF SURVIVED on {remote}: refs/heads/{verdict.probe_ref} "
                    f"({verdict.steps[-1]['detail']})"
                )
                verdict.detail = f"{verdict.detail} | {note}" if verdict.detail else note

    return verdict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gh_write_preflight",
        description=(
            "Prove this credential can WRITE repository contents: push a throwaway branch, "
            "confirm it with ls-remote, delete it. Exit 0 writable / 1 unavailable."
        ),
        epilog=(
            "A branch push triggers no workflow in THIS repo (no unfiltered on:push; publish-pypi "
            "is tags-only); a different --repo inherits that repo's triggers."
        ),
    )
    parser.add_argument("--repo", default=".", help="checkout to probe (default: cwd)")
    parser.add_argument("--remote", default="origin", help="remote name (default: origin)")
    parser.add_argument("--json", action="store_true", help="machine-readable verdict on stdout")
    parser.add_argument(
        "--keep-ref",
        action="store_true",
        help="do NOT delete the probe ref (debugging only — leaves a ref on the remote)",
    )
    args = parser.parse_args(argv)

    verdict = check_writable(args.repo, args.remote, keep_ref=args.keep_ref)

    # The marker ALWAYS goes to stderr, in both modes, so a caller that greps for it works with
    # --json too (stdout stays pure JSON there).
    if not verdict.writable:
        print(
            f"GH-WRITE-UNAVAILABLE({verdict.reason}) — {REASONS.get(verdict.reason or '', '')}",
            file=sys.stderr,
        )

    if args.json:
        print(verdict.as_json())
    else:
        print(f"repo:   {verdict.repo_root or '<unresolved>'}")
        print(f"remote: {verdict.remote or '<none>'} {verdict.remote_url or ''}")
        if verdict.head_sha:
            print(f"head:   {verdict.head_sha[:12]}")
        for s in verdict.steps:
            print(f"  [{'ok' if s['rc'] == 0 else 'FAIL'}] {s['step']} (rc={s['rc']})")
        if verdict.writable:
            cleanup = (
                f"probe ref {verdict.probe_ref} KEPT (--keep-ref)"
                if verdict.kept_ref
                else f"probe ref {verdict.probe_ref} deleted"
            )
            print(f"GH-WRITE-OK({verdict.remote}) — push confirmed by ls-remote; {cleanup}")
        elif verdict.detail:
            print(verdict.detail.strip()[-600:], file=sys.stderr)

    return EXIT_WRITABLE if verdict.writable else EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())
