#!/usr/bin/env python3
"""gh_write_preflight — "can the credential this lane resolves write repository CONTENTS here?"

Why this exists
---------------
`gh` resolves its credential in a fixed order — ``GH_TOKEN`` -> ``GITHUB_TOKEN`` -> the stored
login in ``$HOME/.config/gh/hosts.yml`` (gh's own environment manual; it is the order the CLI
documents and implements). ``git push`` inherits that choice, because a fleet box configures
``credential.helper = !/usr/bin/gh auth git-credential``.

So on a box where BOTH a narrow token and a wide one exist, a single exported environment
variable silently decides which one every child process uses — and the narrowing failure is
invisible from the inside. Measured on a cell (2026-11-05): a fine-grained PAT with
``contents=read`` was the credential lanes resolved, so every ``git push`` was refused with a 403
while ``gh api`` reads and issue comments kept working. Lanes therefore did an hour of work,
reported "built, parked" in good faith, and a **17-lane wave landed nothing**. The symptom that
made it expensive is that the lane could not tell "I cannot deliver" from "I finished".

What this tool does
-------------------
One call answers the only question that matters before the work starts: **push a throwaway ref to
this repo, confirm it with ``ls-remote``, delete it, confirm it is gone.** It is the real thing,
not a permission bit: a token can carry the right scope and still be refused by branch protection
or a ruleset, and an API permission field cannot see the credential the *push* path will use.

It is a PREFLIGHT, not a gate on every git write: it runs once, answers in seconds, and leaves no
trace. Every failure path after a successful push ATTEMPTS the delete — a probe that cannot clean
up says so and names the ref, and no path returns while silently leaving one behind.

Exit codes (callers branch on them; they are not interchangeable)
----------------------------------------------------------------
    0   GH-WRITE-OK(...)                     the credential can write; the probe ref is gone
    1   GH-WRITE-UNAVAILABLE(<reason>)       MEASURED refusal — a lane must not start
    2   GH-WRITE-UNAVAILABLE(cannot-verify:<reason>)   the probe COULD NOT RUN (usage, missing
                                                      tool, timeout, unconfirmable). A
                                                      cannot-verify is never a pass — still non-zero.

Reasons (stable, greppable — the whole point is that a lane's log says which one)
-------------------------------------------------------------------------------
    no-remote                  no `origin` to probe (and no --repo to assert against)
    no-git-checkout            --workdir is not a git work tree
    contents-write-denied      the server refused the push (403 / "Permission to … denied")
    push-refused               the push failed for another measured reason (SSH key, DNS, …)
    ls-remote-mismatch         push reported success but the ref is not on the remote, or its sha
                               differs — the write did not land as claimed
    cleanup-failed             the probe WROTE and the ref could not be removed — names the ref
    probe-ref-exists           a ref of the probe name already exists (refusing to reuse it) —
                               cannot-verify, because nothing about the credential was measured
    cannot-verify:*            usage / bad-repo-argument / repo-mismatch / git-missing / timeout /
                               ls-remote-failed / probe-error / probe-ref-exists

`--repo` is an ASSERTION, never a redirect
------------------------------------------
The probe can only ever exercise the checkout's own ``origin`` — that is the remote whose credential
path is being tested. Passing ``--repo`` therefore *asserts* that the slug being named is the one
`:origin` resolves to, and a disagreement is ``cannot-verify:repo-mismatch`` (exit 2). Redirecting
the label without redirecting the probe would let the tool certify ``owner/name`` writable having
never touched it — the exact false pass this file exists to prevent.

Safety
------
* ⛔ Never prints a credential. The report carries a PREFIX and a LENGTH only, on the same
  standing rule as the lane that commissioned this tool: prefixes and presence, never a value.
  Every string this tool echoes — including the resolved remote URL and the ``--repo`` argument — is
  passed through a userinfo scrubber first, because ``https://x-access-token:<TOKEN>@host/o/n.git``
  is a real remote shape and echoing it verbatim was a measured leak.
* The probe ref is ``gh-write-preflight-<branch-slug>-<utc-stamp>-<pid>-<6 hex>`` — derived from the LANE's own
  branch, so a leftover is attributable to the lane that made it (and a reaper can find it).
* A throwaway BRANCH is CI-safe **in this repo**: every ``push:`` workflow is filtered to
  ``branches: [main]`` or ``tags: ['v*']`` (verified — python-ci, main-lint, deploy-*, redis-guard,
  post-merge-validation, prod-csp-monitor, welcome-e2e-monitor, supabase-deploy, publish-pypi), so
  a probe branch starts no run. Re-verify that filter if a workflow is ever added unfiltered.
* ``--no-verify`` on the push: a lane's local pre-push hook must not be able to make the credential
  look broken (or to run the lane's tests as a side effect of a probe).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PROBE_PREFIX = "gh-write-preflight"
DEFAULT_TIMEOUT_S = 120

# The argv shape the tool runs. A constant so a test can stub it.
GIT = os.environ.get("GH_WRITE_PREFLIGHT_GIT") or "git"
GH = os.environ.get("GH_WRITE_PREFLIGHT_GH") or "gh"


class Verdict:
    def __init__(self) -> None:
        self.ok = False
        self.repo = ""
        self.ref = ""
        self.sha = ""
        self.credential = "unknown"
        self.reason = ""
        self.detail = ""
        self.target = ""

    def as_dict(self) -> dict:
        # ⛔ SCRUBBED HERE, NOT AT THE PRINT SITE: `--json` is the line a lane actually logs and greps,
        # so leaving the raw field in it while scrubbing only the human lines was a real bypass.
        return {
            "ok": self.ok,
            "repo": _scrub(self.repo),
            "ref": _scrub(self.ref),
            "sha": self.sha,
            "credential": self.credential,
            "reason": self.reason,
            "detail": _scrub(self.detail),
        }


def _run(argv: list[str], *, cwd: Path, timeout: int = DEFAULT_TIMEOUT_S) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False
    )


def _git(cwd: Path, *args: str, timeout: int = DEFAULT_TIMEOUT_S) -> subprocess.CompletedProcess:
    return _run([GIT, *args], cwd=cwd, timeout=timeout)


# ── secret hygiene ────────────────────────────────────────────────────────────────────────────────


# `https://x-access-token:<TOKEN>@host/…`, `https://<user>:<TOKEN>@host/…` — the userinfo segment of
# any URL this tool might echo. It is dropped WHOLE rather than pattern-matched on the token's shape:
# a userinfo password has no guaranteed prefix, so a shape-based mask would miss a prefixless one.
# Three patterns because userinfo reaches us in three spellings, all measured:
#   1. after a scheme, where the password may itself contain `/`;
#   2. scheme-less — `x-access-token:<TOKEN>@gitlab.example:o/n.git` (no `://` to anchor on);
#   3. the belt: ANY remaining `…@…` token keeps only the part after the LAST `@`. That is the only
#      construction that cannot be defeated by a password containing `/` and no scheme, and it can
#      never remove a secret (a userinfo secret is always BEFORE the last `@`).
_USERINFO = re.compile(r"(?<=://)[^@\s]+@")
_SCHEMELESS_USERINFO = re.compile(r"[^\s/@:]+:[^\s/@]*@")
_TOKEN_SHAPED = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")


def _strip_residual_userinfo(token: str) -> str:
    if "@" not in token:
        return token
    return token.rsplit("@", 1)[-1]


def _scrub(text: str) -> str:
    """Remove URL userinfo, then mask any token-shaped run.

    ⛔ ONE OWNER: this runs at the ECHO BOUNDARY — `_emit()` and `Verdict.as_dict()` — and nowhere
    else. Scrubbing again at each construction site was measured to be undiscriminating (removing it
    changed no observable behaviour), i.e. untested duplicate logic; a single boundary is both
    simpler and fully pinned by the leak tests.
    """
    if not text:
        return text
    out = _USERINFO.sub("", text)
    out = _SCHEMELESS_USERINFO.sub("", out)
    out = _TOKEN_SHAPED.sub(lambda m: f"{m.group(1)[:4]}<REDACTED>", out)
    return " ".join(_strip_residual_userinfo(tok) for tok in out.split(" "))


def _credential_fingerprint(value: str) -> str:
    """A PREFIX and a length. Never the value — this string goes into lane logs and artifacts.

    ⛔ A SHORT VALUE IS NOT A FINGERPRINT. A 1-4 character credential would have been shown in full by
    a naive `value[:4]`, so anything short enough to be its own secret reports a length only.
    """
    if not value:
        return "absent"
    if len(value) <= 8:
        return f"<REDACTED> len={len(value)}"
    return f"{value[:4]}<REDACTED> len={len(value)}"


def _credential_source(cwd: Path) -> str:
    """Which link of gh's chain the credential will come from, as a label and a prefix."""
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(name, "")
        if value.strip():
            return f"env:{name}={_credential_fingerprint(value)}"
    # The store. `gh auth token` is the ONE supported way to read it, and its stdout is the value,
    # so it is fingerprinted immediately and never echoed.
    try:
        res = _run([GH, "auth", "token"], cwd=cwd, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return "store:unreadable"
    if res.returncode != 0 or not res.stdout.strip():
        return "absent"
    return f"store:{_credential_fingerprint(res.stdout.strip())}"


# ── resolution ───────────────────────────────────────────────────────────────────────────────────


def _is_git_worktree(cwd: Path) -> bool:
    try:
        return _git(cwd, "rev-parse", "--is-inside-work-tree", timeout=30).stdout.strip() == "true"
    except (OSError, subprocess.SubprocessError):
        return False


def _git_available() -> bool:
    """⛔ A MISSING `git` IS NOT AN UNWRITABLE CREDENTIAL. Without this, `_is_git_worktree`'s OSError
    reads as "not a checkout", which the caller reports as a measured refusal (exit 1) and sends an
    operator to the token's permissions page for a broken toolchain."""
    try:
        return subprocess.run(
            [GIT, "--version"], capture_output=True, timeout=30, check=False
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _push_targets(cwd: Path) -> list[str]:
    """EVERY url `git push origin` writes to — `remote.origin.pushurl` is multi-valued.

    ⛔ `git push origin` writes to ALL pushurls and fails if any of them refuses, while `ls-remote`
    reads one URL at a time. So the write goes through the NAME `origin` (git exercises every pushurl)
    and each resolved target is confirmed separately. Addressing a single resolved URL instead was a
    FALSE PASS: with a writable first pushurl and a refusing second one the tool said `GH-WRITE-OK`
    while the real push exited 1 (measured, round 11).
    """
    try:
        out = _git(cwd, "remote", "get-url", "--push", "--all", "origin", timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    targets = [u for u in out.split() if u]
    if not targets:
        fetch = _fetch_url(cwd)
        targets = [fetch] if fetch else []
    return targets


def _fetch_url(cwd: Path) -> str:
    try:
        return _git(cwd, "remote", "get-url", "origin", timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _repo_from_url(url: str) -> str:
    """`https://github.com/owner/name(.git)` or `git@github.com:owner/name.git` -> `owner/name`.

    A non-GitHub URL (a local path, a file:// remote, a mirror) is returned as-is: the probe still
    works against it, and a caller that expected a slug can read what was actually measured.

    ⛔ USERINFO IS NOT STRIPPED HERE, ON PURPOSE — two other owners already guarantee it and a third
    was measured to be unobservable (an equivalent mutant): the `@`/`//`/`^` anchor above means userinfo
    can never become part of a SLUG, and `_scrub` at the echo boundary masks it in every string that is
    printed. Duplicating it here was untested code in the tool's most safety-critical area.
    """
    clean = url or ""
    if not clean:
        return ""
    # ⛔ HOST-ANCHORED. A bare `github\.com` match also fires on `mygithub.com/o/n`, on
    # `/srv/mirrors/github.com/o/n.git` and on a vendor mirror path — and then the tool reports a
    # GitHub slug (`GH-WRITE-OK(repo=owner/name …)`) for a remote it never touched, even with NO
    # `--repo` argument. Only a scheme boundary, a `//` authority, or an `@` (scp form) may precede it.
    m = re.search(r"(?:^|//|@)github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", clean)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return clean


def _branch_slug(cwd: Path) -> str:
    try:
        branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD", timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        branch = ""
    if not branch or branch == "HEAD":
        branch = "detached"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-")[:48]
    return slug or "lane"


_SHA = re.compile(r"[0-9a-fA-F]{40,64}")


def _head_sha(cwd: Path) -> str:
    """The commit the probe ref will point at, or '' — the RETURN CODE is checked, not just stdout.

    ⛔ `git rev-parse HEAD` on an unborn branch prints the literal `HEAD` and exits 128, so a
    stdout-only read yields a truthy `"HEAD"` and the "no commit" guard never fires: the probe then
    pushes `HEAD:refs/heads/…`, git refuses with `src refspec HEAD does not match any`, and an
    unmeasured git-shape condition is reported as a MEASURED credential refusal — the mislabel class
    this file exists to remove.
    """
    try:
        res = _git(cwd, "rev-parse", "HEAD", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    if res.returncode != 0:
        return ""
    sha = res.stdout.strip()
    return sha if _SHA.fullmatch(sha) else ""


# ── the probe ───────────────────────────────────────────────────────────────────────────────────


def _classify_push_failure(stderr: str) -> str:
    """Name the failure without over-claiming a scope verdict.

    `Permission denied (publickey)` is an SSH auth failure and says NOTHING about contents scope, so
    it is classified first — reporting it as `contents-write-denied` would send an operator to the
    token's permission page for a missing key.
    """
    low = stderr.lower()
    if "publickey" in low:
        # Covers `Permission denied (publickey)` and the hybrid `Permission to o/n.git denied
        # (publickey)`: an SSH key failure says nothing about contents scope. `host key verification`
        # needs no clause of its own — it matches no contents arm below and falls through.
        return "push-refused"
    if "403" in low or "permission to" in low or "not authorized" in low:
        return "contents-write-denied"
    return "push-refused"


def _remote_has_ref(
    cwd: Path, target: str, ref: str, timeout: int = DEFAULT_TIMEOUT_S
) -> tuple[str, bool]:
    """`(sha, ok)` for `ref` on `target`. ok=False means the read itself failed (not "absent")."""
    try:
        res = _git(cwd, "ls-remote", "--heads", target, ref, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return "", False
    if res.returncode != 0:
        return "", False
    # ⛔ EXACT ref match, not a suffix. `git ls-remote <pattern>` matches the pattern from the RIGHT,
    # so a pattern of `gh-write-preflight-…` also returns `refs/heads/decoy/gh-write-preflight-…`
    # (measured round 8). A suffix test accepted that decoy as the probe ref: harmless at the pre-check
    # (it fails closed as `probe-ref-exists`), but a decoy arriving in the window between the pre-check
    # and the post-push confirm produced `cleanup-failed` naming a ref the probe had in fact deleted.
    wanted = f"refs/heads/{ref}"
    for line in res.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == wanted:
            return parts[0], True
    return "", True


def _exists_any(
    cwd: Path, targets: list[str], ref: str, timeout: int = DEFAULT_TIMEOUT_S
) -> tuple[str, bool]:
    """`(sha, ok)` — the ref's sha if it is on ANY target, ok=False if any read failed.

    The question a PRE-check asks: "would I be probing over something that already exists?" Any target
    that has it means yes.
    """
    found = ""
    for target in targets:
        sha, ok = _remote_has_ref(cwd, target, ref, timeout)
        if not ok:
            return "", False
        found = found or sha
    return found, True


def _confirmed_all(
    cwd: Path, targets: list[str], ref: str, timeout: int = DEFAULT_TIMEOUT_S
) -> tuple[str, bool]:
    """`(sha, ok)` — the sha ONLY if EVERY target holds it; ok=False if any read failed.

    ⛔ THE POST-PUSH QUESTION IS NOT THE PRE-CHECK ONE (measured, round 11). `found or sha` returned a
    sha as soon as ONE target had it, so a second pushurl that received nothing was reported as
    confirmed: `GH-WRITE-OK` for a destination the lane believes it pushed to that has no such ref.
    Every target must agree on the same sha, which is what "the write landed" actually means when
    `remote.origin.pushurl` is multi-valued.
    """
    agreed = ""
    for target in targets:
        sha, ok = _remote_has_ref(cwd, target, ref, timeout)
        if not ok:
            return "", False
        if not sha:
            return "", True  # absent on a target: the write did not land there
        if agreed and sha != agreed:
            return "", True  # the targets disagree: not one landed write
        agreed = sha
    return agreed, True


def _cleanup(cwd: Path, targets: list[str], ref: str, timeout: int = DEFAULT_TIMEOUT_S) -> str:
    """Delete the probe ref. Returns '' when the ref is GONE, else a (scrubbed) failure detail.

    ⛔ THE REMOTE DECIDES, NEVER THE DELETE'S EXIT CODE — measured (round 6). Real git ERRORS on
    `push --delete` for a ref that does not exist (`error: unable to delete …: remote ref does not
    exist`, rc 1), so an exit-code test reported `cleanup-failed` for a ref that was already absent —
    and `cleanup-failed`'s message asserts "the credential CAN write" and sends the operator hunting a
    branch that provably is not there. That is the wrong-diagnosis failure this whole file exists to
    prevent. Confirming with `ls-remote` makes the function idempotent and makes "already absent"
    success, which is what a probe needs on every path.
    """
    rc_err = ""
    try:
        # ⛔ the NAME, not a target: a delete must reach every pushurl, exactly like a push does
        dele = _git(cwd, "push", "--no-verify", "origin", "--delete", ref, timeout=timeout)
        if dele.returncode != 0:
            rc_err = " ".join((dele.stderr or dele.stdout or "").split())[:200]
    except (OSError, subprocess.SubprocessError) as exc:
        # A timeout may still have deleted it — fall through to the confirmation.
        rc_err = f"{type(exc).__name__}: {exc}"
    for target in targets:
        sha, ok = _remote_has_ref(cwd, target, ref, timeout)
        if not ok:
            return f"could not confirm the delete on {_scrub(target)} (ls-remote failed)"
        if sha:
            return (
                f"{ref} still present on {_scrub(target)} after delete"
                + (f" ({rc_err})" if rc_err else "")
            )
    return ""


def _cleanup_failure(verdict: Verdict, detail: str) -> Verdict:
    verdict.ok = False
    verdict.reason = "cleanup-failed"
    verdict.detail = (
        f"the credential CAN write, but the probe ref {verdict.ref} could not be removed — "
        f"delete it by hand: git push origin --delete {verdict.ref} "
        f"(origin pushes to {verdict.target or '<unresolved>'}) ({detail}). "
        f"This is a REPO-STATE problem, not a credential problem: a ruleset that restricts deleting "
        f"refs/heads/* produces it, and every run then leaves one more probe branch behind"
    )
    return verdict


def _after_push_failure(
    v: Verdict,
    cwd: Path,
    targets: list[str],
    ref: str,
    *,
    reason: str,
    detail: str,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> Verdict:
    """Every post-push failure path funnels here, so none of them can leave a ref behind.

    ⛔ THIS EXISTS BECAUSE A `try/except` AROUND THE PUSH IS NOT ENOUGH. `TimeoutExpired` — a
    post-receive hook that hangs, a 120 s stall AFTER the ref was written — RAISES rather than
    returning non-zero, so a push that had already landed was reported as a plain refusal with the
    branch left on the remote: no `cleanup-failed`, no ref named, no remedy. Measured. So the three
    failure shapes (non-zero exit, timeout, OSError) all confirm-and-clean here.
    """
    v.reason = reason
    v.detail = detail
    landed, ok = _exists_any(cwd, targets, ref, timeout)
    if not ok:
        # Cannot confirm whether the ref was created. Deleting would be the right attempt, but its
        # own post-delete confirmation would also fail and would mask `reason` with `cleanup-failed`,
        # so say what is unknown instead of guessing in either direction.
        v.detail = (
            f"{detail} — and whether {ref} was created could not be confirmed (ls-remote failed); "
            f"check it and delete it if present: git ls-remote --heads <target> {ref}, where the "
            f"target is {v.target or '<unresolved>'} (in a fork checkout `origin` is the FETCH url and "
            f"would answer empty for a ref that IS on the push target)"
        )
        return v
    if landed:
        cleanup_detail = _cleanup(cwd, targets, ref, timeout)
        if cleanup_detail:
            return _cleanup_failure(v, cleanup_detail)
    return v


def probe(cwd: Path, repo: str, targets: list[str], timeout: int = DEFAULT_TIMEOUT_S) -> Verdict:
    v = Verdict()
    v.repo = repo
    v.target = ", ".join(_scrub(t) for t in targets)
    v.credential = _credential_source(cwd)

    sha = _head_sha(cwd)
    if not sha:
        v.reason = "cannot-verify:no-head"
        v.detail = "cannot resolve HEAD to a commit — a probe ref needs one to point at"
        return v
    v.sha = sha

    # ⛔ UNIQUE PER RUN, not per second. A one-second stamp collided when two gates ran in the same
    # checkout inside one second (parent + dispatched child, or the tool's own "re-run in a second"
    # remedy) — measured 30 parallel runs: 9 false `push-refused` ("reference already exists" — a
    # measured refusal for a credential that writes fine), 4 false `ls-remote-mismatch` caused by a peer
    # deleting this run's ref, and 9 `probe-ref-exists`. pid + 6 random hex digits cannot collide.
    stamp = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{os.getpid()}-{secrets.token_hex(3)}"
    ref = f"{PROBE_PREFIX}-{_branch_slug(cwd)}-{stamp}"
    v.ref = ref

    existing, ok = _exists_any(cwd, targets, ref, timeout)
    if not ok:
        v.reason = "cannot-verify:ls-remote-failed"
        v.detail = (
            f"could not list refs on {v.target or '<unresolved>'} to check {ref} — nothing was pushed"
        )
        return v
    if existing:
        # ⛔ cannot-verify, NOT a measured refusal: nothing about the CREDENTIAL was measured here, and
        # exit 1 tells a lane "do not start" for a condition a re-run in a second will clear.
        v.reason = "cannot-verify:probe-ref-exists"
        v.detail = f"{ref} already exists (sha {existing[:12]}) — refusing to probe over it"
        return v

    # ── 1. the real write: a genuine push through the SAME credential path a lane uses ───────────
    try:
        # ⛔ the NAME, so git exercises EVERY pushurl — a single resolved URL produced a
        # FALSE PASS when a second pushurl refused (measured, round 11)
        push = _git(cwd, "push", "--no-verify", "origin", f"HEAD:refs/heads/{ref}", timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # ⚠️ ORDER MATTERS: `TimeoutExpired` subclasses `SubprocessError`, so this arm must come
        # first or the timeout is swallowed as a generic failure — and it is the arm that most needs
        # the cleanup (the ref is the most likely to have landed).
        return _after_push_failure(
            v,
            cwd,
            targets,
            ref,
            reason="cannot-verify:timeout",
            detail=f"the push did not answer within {timeout}s ({exc})",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _after_push_failure(
            v,
            cwd,
            targets,
            ref,
            reason="cannot-verify:probe-error",
            detail=f"{type(exc).__name__}: {exc}",
            timeout=timeout,
        )
    if push.returncode != 0:
        # ⛔ A FAILED PUSH MAY STILL HAVE CREATED THE REF (a post-receive refusal, a timeout after the
        # ref update). `_after_push_failure` confirms and cleans rather than assuming.
        stderr = push.stderr or push.stdout or ""
        return _after_push_failure(
            v,
            cwd,
            targets,
            ref,
            reason=_classify_push_failure(stderr),
            detail=" ".join(stderr.split())[:400],
            timeout=timeout,
        )

    # ── 2. confirmed by ls-remote, not by the push's own exit code ──────────────────────────────
    landed, ok = _confirmed_all(cwd, targets, ref, timeout)
    if not ok:
        # ⛔ cannot-verify, NEVER `cleanup-failed`. Presence was never MEASURED on this path, so
        # "could not be removed" is an unknown — not a cleanup failure — and `cleanup-failed` asserts
        # "the credential CAN write", which nothing here established (measured round 7: a push that
        # exited 0 writing nothing plus an `ls-remote` exiting 128 produced exactly that false claim
        # about a ref that never existed). The `_after_push_failure` wording is the honest one: name
        # the ref, say where it may be, and let the operator check.
        detail = _cleanup(cwd, targets, ref, timeout)
        v.reason = "cannot-verify:ls-remote-failed"
        v.detail = (
            f"the push exited 0 but {ref} could not be confirmed on origin"
            + ("" if not detail else f" (the delete also could not be confirmed: {detail})")
            + " — check it and delete it by hand if it is present"
        )
        return v
    if not landed:
        # ⛔ `_confirmed_all` ANSWERS "" FOR TWO DIFFERENT STATES, and only one of them means "nothing
        # to delete": (i) absent on a target, (ii) PRESENT on every target with DISAGREEING shas.
        # Treating both as absent left the probe ref on every pushurl (measured, round 12: two
        # `LEFTOVER` refs from one run) — one branch per run, forever. Ask the presence question.
        # This NO LONGER reports `cleanup-failed` for a ref that never existed: it reaches `_cleanup`
        # only when a ref is measured PRESENT.
        present, pok = _exists_any(cwd, targets, ref, timeout)
        if pok and present:
            detail = _cleanup(cwd, targets, ref, timeout)
            if detail:
                return _cleanup_failure(v, detail)
        v.reason = "ls-remote-mismatch"
        v.detail = (
            f"push exited 0 but {ref} is not on EVERY push target "
            f"({v.target or '<unresolved>'}) — the write did not land everywhere it was sent"
        )
        return v
    if landed != sha:
        # The ref EXISTS with a different sha, so it does have to go: a caller retries after a
        # mismatch, and leaving it would accumulate one branch per attempt.
        detail = _cleanup(cwd, targets, ref, timeout)
        if detail:
            return _cleanup_failure(v, detail)
        v.reason = "ls-remote-mismatch"
        v.detail = f"remote {ref} is {landed[:12]}, expected {sha[:12]}"
        return v

    # ── 3. leave no trace ───────────────────────────────────────────────────────────────────────
    detail = _cleanup(cwd, targets, ref, timeout)
    if detail:
        return _cleanup_failure(v, detail)

    v.ok = True
    v.reason = "ok"
    v.detail = f"pushed, ls-remote-confirmed and deleted {ref}"
    return v


# ── CLI ─────────────────────────────────────────────────────────────────────────────────────────


def _fail(reason: str, detail: str, *, repo: str = "") -> Verdict:
    v = Verdict()
    v.reason = reason
    v.detail = detail
    v.repo = repo
    return v


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gh_write_preflight.py",
        description="Answer, in one call, whether the credential this checkout resolves can write "
        "repository contents — by pushing a throwaway ref, confirming it, and deleting it.",
        epilog="Exit 0 = can write. Exit 1 = measured refusal. Exit 2 = cannot verify (still not a pass).",
    )
    parser.add_argument(
        "--workdir",
        default=".",
        help="the checkout whose `origin` and credential are probed (default: cwd)",
    )
    parser.add_argument(
        "--repo",
        help="ASSERT that this owner/name is the checkout's origin (it never redirects the probe; "
        "a disagreement is cannot-verify:repo-mismatch)",
    )
    parser.add_argument("--json", action="store_true", help="emit one JSON object as the last line")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_S,
        help=f"bound each NETWORK git call in seconds (default {DEFAULT_TIMEOUT_S}); local resolution "
        f"reads use a fixed 30s. SHORTENING it can only "
        "make the probe stricter, never weaker — a probe that cannot finish is cannot-verify",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print nothing to stdout on success (the exit code is the answer); --json still prints",
    )
    args = parser.parse_args(argv)

    cwd = Path(args.workdir).resolve()
    timeout = max(1, args.timeout)
    if not _git_available():
        return _emit(
            _fail("cannot-verify:git-missing", f"`{GIT}` is not runnable — nothing was measured"),
            json_out=args.json,
            quiet=args.quiet,
            cannot_verify=True,
        )
    if not cwd.is_dir() or not _is_git_worktree(cwd):
        return _emit(
            _fail("no-git-checkout", f"{cwd} is not a git work tree"),
            json_out=args.json,
            quiet=args.quiet,
            cannot_verify=True,
        )

    fetch_url = _fetch_url(cwd)
    targets = _push_targets(cwd)
    # the REPO IDENTITY is what the checkout is: the fetch url's slug. Every push target is verified
    # separately, so a fork-style split is measured rather than mislabelled.
    origin_url = fetch_url or (targets[0] if targets else "")
    origin_repo = _repo_from_url(origin_url)
    push_repos = [_repo_from_url(t) for t in targets]
    split_note = (
        ""
        if not push_repos or all(p.lower() == origin_repo.lower() for p in push_repos)
        else " [origin pushes to "
        + ", ".join(_scrub(p) for p in push_repos)
        + "; each of those targets was confirmed]"
    )

    explicit = (args.repo or "").strip()
    if explicit:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", explicit):
            return _emit(
                _fail(
                    "cannot-verify:bad-repo-argument", f"--repo {explicit!r} is not owner/name"
                ),
                json_out=args.json,
                quiet=args.quiet,
                cannot_verify=True,
            )
        # ⛔ AN ASSERTION, NOT A REDIRECT. The probe can only exercise this checkout's `origin`; a
        # label naming anything else would certify a repo the tool never touched.
        if not origin_url:
            return _emit(
                _fail(
                    "no-remote",
                    f"no `origin` remote in {cwd} — --repo {explicit} cannot be probed without one",
                    repo=explicit,
                ),
                json_out=args.json,
                quiet=args.quiet,
            )
        if origin_repo.lower() != explicit.lower():
            return _emit(
                _fail(
                    "cannot-verify:repo-mismatch",
                    f"--repo {explicit} does not match the checkout's origin ({origin_repo}); the "
                    f"probe can only test this checkout's origin, so it will not certify another repo",
                    repo=explicit,
                ),
                json_out=args.json,
                quiet=args.quiet,
                cannot_verify=True,
            )
        repo = explicit
    else:
        if not origin_url:
            return _emit(
                _fail("no-remote", f"no `origin` remote in {cwd}"),
                json_out=args.json,
                quiet=args.quiet,
            )
        repo = origin_repo

    try:
        verdict = probe(cwd, repo, targets, timeout)
    except subprocess.TimeoutExpired as exc:
        verdict = _fail(
            "cannot-verify:timeout", f"{exc.cmd} did not answer within {timeout}s"
        )
        verdict.repo = repo
        verdict.detail += split_note
        return _emit(verdict, json_out=args.json, quiet=args.quiet, cannot_verify=True)
    except (OSError, subprocess.SubprocessError) as exc:
        verdict = _fail("cannot-verify:probe-error", f"{type(exc).__name__}: {exc}", repo=repo)
        verdict.detail += split_note
        return _emit(verdict, json_out=args.json, quiet=args.quiet, cannot_verify=True)

    if split_note and verdict.detail:
        verdict.detail += split_note
    return _emit(
        verdict,
        json_out=args.json,
        quiet=args.quiet,
        # ⛔ `cleanup-failed` belongs here, NOT in the measured-refusal bucket: its own detail says the
        # credential CAN write (the write was measured). Exiting 1 told a lane that CAN deliver
        # "do not start" — and because each run left another probe ref behind, it would say so forever.
        cannot_verify=verdict.reason.startswith("cannot-verify") or verdict.reason == "cleanup-failed",
    )


def _emit(verdict: Verdict, *, json_out: bool, quiet: bool, cannot_verify: bool = False) -> int:
    if verdict.ok:
        if not quiet:
            print(
                f"GH-WRITE-OK(repo={_scrub(verdict.repo)} ref={_scrub(verdict.ref)} "
                f"sha={verdict.sha[:12]} credential={verdict.credential}) — "
                f"{_scrub(verdict.detail)}"
            )
        if json_out:
            print(json.dumps(verdict.as_dict(), sort_keys=True))
        return 0

    print(
        f"GH-WRITE-UNAVAILABLE({verdict.reason}) — {_scrub(verdict.detail)}\n"
        f"  repo={_scrub(verdict.repo) or '<unresolved>'} ref={_scrub(verdict.ref) or '<none>'} "
        f"credential={verdict.credential}\n"
        f"  a lane must NOT start work it cannot deliver: fix the credential "
        f"(gh resolves GH_TOKEN -> GITHUB_TOKEN -> $HOME/.config/gh/hosts.yml) or unset the "
        f"shadowing variable, then re-run."
    )
    if json_out:
        print(json.dumps(verdict.as_dict(), sort_keys=True))
    return 2 if cannot_verify else 1


if __name__ == "__main__":
    sys.exit(main())
