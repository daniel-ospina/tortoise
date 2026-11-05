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
* Exit **0** — the credential can write: a throwaway branch was created and confirmed by
  ``git ls-remote`` at OUR sha, and the deletion was then **attempted** and confirmed whenever the
  remote stayed readable. ``cleaned_up`` records exactly what is known: ``True`` only on a positive
  "ref absent" read, ``False`` on a proven survivor, ``None`` when the confirming read failed (the
  ref may or may not be gone). An unconfirmed cleanup is NOT a failed write — the credential still
  wrote — so it does not change the exit code; it is reported loudly on the ``cleanup UNCONFIRMED``
  line and in ``--json``. An UNREQUESTED known survivor is the one cleanup outcome that DOES change
  the exit code: it exits **1** with ``GH-WRITE-UNAVAILABLE(delete-failed)``, because a tool must
  not call a credential usable while leaving garbage on a shared remote. (``--keep-ref`` requests a
  survivor deliberately — that one keeps ``cleaned_up=False`` and still exits 0; its litter was
  asked for.) **Read the unrequested-survivor refusal as litter, not as capability** — the probe ref
  was created, so this credential demonstrably CAN write; the 1 means "re-run and clean up", never
  "your lanes cannot deliver". (An exit of 0 is reserved for a fully successful run;
  ``sysexits(3)``: "The successful exit is always indicated by a status of 0" — so an unrequested
  survivor MUST NOT exit 0. Pinned by
  ``test_known_survivor_exits_nonzero_with_delete_failed_marker``.)
* Exit **1** — ``GH-WRITE-UNAVAILABLE(<reason>)`` on stderr (emitted in ``--json`` mode too; stdout
  stays pure JSON). Reasons are stable tokens from ``REASONS`` (top-level, non-nested), so a caller
  can branch on them without parsing prose. **No path returns 1 without a token from that set** —
  including an unexpected exception, which is caught and mapped to ``internal-error``. That set is
  CLOSED, and this list is the whole of it: ``git-absent``, ``not-a-repo``, ``no-remote``,
  ``no-head``, ``remote-unreachable``, ``push-refused``, ``push-unknown``, ``verify-mismatch``,
  ``delete-failed``, ``internal-error``.
  Two of the ten need their own branch in a caller's table — folding either into a neighbour
  re-creates a diagnosis error this tool exists to remove. ``push-unknown`` means the push did not
  COMPLETE (timed out, was signalled, produced no output), so write capability is UNKNOWN: re-run,
  and never read it as evidence of a read-only credential, because that reading parks deliverable
  work on a slow or OOM-killed push. ``internal-error`` means the probe itself failed, so NO verdict
  exists — not a refusal. Both directions of this contract are pinned by
  ``test_the_contract_publishes_the_closed_reason_set``, which reds if ``REASONS`` gains a token
  this list omits and if this list names a token ``REASONS`` never emits.
* Exit **2** — usage error (argparse's own code, deliberately not reused for a verdict).

Safety and soundness
--------------------
* **Repository redirection is stripped; credential-carried config is not.** Git's *state* variables
  (``GIT_DIR``, ``GIT_WORK_TREE``, ``GIT_INDEX_FILE``, ``GIT_OBJECT_DIRECTORY``,
  ``GIT_ALTERNATE_OBJECT_DIRECTORIES``, ``GIT_COMMON_DIR``, ``GIT_NAMESPACE``, ``GIT_PREFIX``) and
  the trace/verbose pair (``GIT_TRACE*``, ``GIT_CURL_VERBOSE``) are removed: the first redirect
  which repository git operates on, which once made this tool report ``writable=True`` about a
  DIFFERENT, writable repository than the one asked for (a false PASS, pinned by
  ``test_git_dir_cannot_redirect_the_probe``); the second would route git's own ``Authorization:``
  header into our output. ``GIT_CONFIG_*`` and ``GIT_SSH_COMMAND`` are deliberately KEPT — they
  carry the credential on boxes that inject it through config or a custom SSH command, and dropping
  them would make the probe test a different path than the lane's real push and refuse a credential
  that can write (a false block, the failure this tool exists to prevent, inverted).
* **stdout and stderr are parsed separately.** git writes diagnostics to stderr (e.g. ``warning:
  redirecting to https://…``); reading a "last line" out of a merged stream mistook a warning for a
  path and could invent a leftover ref.
* The probe ref is namespaced (``gh-write-preflight/<utc>-<pid>-<rand>``) so it cannot collide with
  a real branch, and it is cleaned up on **every path that unwinds the interpreter** — the ordinary
  path, a failed push that still applied the update (a transport error can arrive after the remote
  took it), a Ctrl-C (``SIGINT`` → ``KeyboardInterrupt``) and any Python exception, all via one
  ``try/finally``. ⛔ It is NOT covered when the process is killed by a signal Python does not
  translate: ``SIGTERM`` (what a fleet watchdog sends) takes its default action and terminates
  **without unwinding**, so no ``finally`` runs and the ref can be left behind. That is stated as a
  residual below rather than claimed away, because a docstring a lane trusts is worse than a
  residual it can see. Cleanup is confirmed by
  ``ls-remote`` and the result is **tri-state**: ``cleaned_up=True`` only on a positive
  "ref absent" read; a read that could not be performed sets ``cleaned_up=None`` rather than
  claiming verified-clean from an absence of evidence.
* The probe pushes the commit already at ``HEAD`` (``HEAD:refs/heads/<probe>``). It does not create
  a commit and does not touch the working tree or index, so it is safe to run in a dirty worktree.
* A credential never reaches the output: redaction happens **at ingestion** (``step()`` stores an
  already-redacted detail, ``remote_url`` is stored redacted), so no render path can forget it.
* A branch push does not trigger CI in this repo (no workflow has an unfiltered ``on: push``;
  ``publish-pypi`` is ``tags: v*`` only). That is a property of THIS repo: the tool is generic via
  ``--repo``, so probing another checkout inherits that repo's triggers.
* The probe pushes with ``--no-verify``, so a PASS says the CREDENTIAL can write; it does not
  claim a lane's own pre-push hook would cooperate.
* **STATED RESIDUAL — a ruleset that restricts branch *creation* to a pattern.** If the repository
  only permits `main`/`fix/*` and refuses `refs/heads/gh-write-preflight/…`, this tool reports
  `push-refused` for a credential that CAN write — the tool's purpose inverted (work parked that
  could have landed). It is not silent: git's own refusal text ("protected branch" / "policy
  prohibits") lands in the failing step's detail on stderr, next to an explicit `--repo`, so it is
  diagnosable rather than mysterious. No read-only credential can pass through this residual in the
  other direction. `--probe-ref-prefix` aims the probe at a namespace a constrained repo allows.
* **STATED RESIDUAL — a probe killed by ``SIGTERM`` can leave the ref on the remote.** The cleanup
  guarantee is `try/finally`, and Python installs default handlers for only `SIGPIPE` (ignored) and
  `SIGINT` (→ `KeyboardInterrupt`); `SIGTERM` therefore terminates the process without unwinding,
  so the delete never issues. The outcome is **litter, not a false verdict** — nothing on the
  verdict path changes and no read-only credential passes through it — and the ref is namespaced
  and unique per run (`<utc>-<pid>-<rand>`), so it cannot collide with a later probe. `#11053`
  item 2 records the same residual for the sibling implementation; closing it needs a handler or
  a pre-run sweep, not a docstring edit.

Usage
-----
    uv run python tools/gh_write_preflight.py                # probe this checkout's origin
    uv run python tools/gh_write_preflight.py --json         # machine-readable verdict
    uv run python tools/gh_write_preflight.py --repo <path>  # probe another checkout

Enablement in this repo: tests/test_gh_write_preflight.py (red-first: a rejecting remote MUST
produce exit 1; an accepting remote MUST produce exit 0 and leave no probe ref behind).
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/gh_write_preflight.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/gh_write_preflight.py`"
    )

import argparse
import json
import os
import random
import re
import string
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# Exit codes. 0/1 are the verdict; 2 is argparse's usage code.
EXIT_WRITABLE = 0
EXIT_UNAVAILABLE = 1

# Stable TOP-LEVEL reason tokens. Every `writable=False` verdict carries one of these, so a caller
# can branch on a flat enum rather than walking nested state.
REASONS = {
    "git-absent": "the git binary is not on PATH",
    "not-a-repo": "the target is not a directory inside a git work tree",
    "no-remote": "the checkout has no remote of that name",
    "no-head": "HEAD does not resolve to a commit (no pushable object)",
    "remote-unreachable": "the remote refused or failed `ls-remote`",
    "push-refused": "`git push` was refused (read-only credential, branch protection, or no network)",
    "push-unknown": (
        "`git push` did not complete (timed out, was signalled, or produced no output), so write "
        "capability is UNKNOWN — this is NOT evidence the credential is read-only"
    ),
    "verify-mismatch": "the push reported success but `ls-remote` does not show the ref",
    "delete-failed": (
        "the probe ref is left on the remote because it could not be deleted. The push already "
        "succeeded, so this refusal is about LITTER on a shared remote — not about whether the "
        "credential can deliver"
    ),
    "internal-error": "the probe itself failed unexpectedly (no verdict is available)",
}

# ── redaction ──────────────────────────────

# Userinfo that carries NO secret. `git@`/`ssh@`/`hg@` are ordinary SSH accounts; routing them
# through the credential-alias path would rewrite and break the most common remote there is.
_NON_SECRET_USERINFO = frozenset({"git", "ssh", "hg", "svn", "deploy"})
# ──────────────────────────────────────────────────────────────────────────────────────────────
# Applied at INGESTION (see `step()`), so every later render is safe by construction. Deliberately
# broad: a false redaction costs one diagnostic, a leak costs a credential.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # userinfo in a URL — ANY scheme, plus scheme-relative `//user:pw@host`. The leftmost
    # alternative wins, so `https://…` keeps its scheme and `ssh://`/`git://` are covered too.
    # ⛔ GREEDY to the LAST `@` before the authority terminator. A single-userinfo form stopped at
    # the FIRST `@`, so a password containing `@` was only partially hidden:
    # `https://deploy:p@ssw0rdS3cret@host/…` rendered as `https://<userinfo-redacted>@ssw0rdS3cret@host/…`
    # — twelve characters of the secret printed (measured). `[^/\s]*@` cannot leave the authority,
    # because a `/` ends it.
    (re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://|//)[^/\s]*@"), r"\1<userinfo-redacted>@"),
    # Any Authorization header, one token or two, quoted-key repr included. The whole value goes —
    # preserving the scheme word buys a diagnostic nobody needs and risks a one-token miss.
    (
        re.compile(r"(?i)([\"']?(?:proxy-)?authorization[\"']?\s*[:=]\s*)\S+(?:\s+\S+)?"),
        r"\1<redacted>",
    ),
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
    return _redact_scp_userinfo(out)


# The scp-like userinfo shape (`user:secret@host:path` / `token@host:path`). `_url_has_credential`
# recognises it, so `redact()` MUST cover it too — otherwise the tool detects a credential and then
# prints it, which is the one asymmetry that matters: detection without redaction is a leak.
# The userinfo is the text before the FIRST `@` and can never contain `/`, which is what separates a
# credential from a local path that merely contains an `@`.
_SCP_LIKE_USERINFO = re.compile(
    # Userinfo is everything up to the LAST `@` before the host, so a password that itself contains
    # `@` (`user:p@ss@host:path`) is covered — the earlier first-`@` form left that shape intact and
    # therefore PRINTED a credential the detector had already classified as one (measured).
    # The host class allows `_` (an underscore host was detected but printed in full), and the
    # separator may be `:` (scp) or `/` (scheme-less host/path), which is captured so the shape is
    # preserved on replacement.
    r"(?<![\w@./-<])([^<>\s/]+)@([A-Za-z0-9._-]+)([:/])(?=\S)"
)


_SCP_LIKE_BARE = re.compile(
    # Same shape, anchored at the end of the token: `user@host` with nothing after it.
    r"(?<![\w@./-<])([^<>\s/@]+)@([A-Za-z0-9._-]+)(?=\s|$)"
)


def _redact_scp_userinfo(text: str) -> str:
    def _sub(m: re.Match[str]) -> str:
        userinfo, host = m.group(1), m.group(2)
        sep = m.group(3) if m.lastindex and m.lastindex >= 3 else ""
        user = userinfo.split(":", 1)[0]
        # `git@host:` / `ssh@host:` carry no secret — redacting them would destroy ordinary
        # diagnostics and is not required by anything.
        if ":" not in userinfo and user.lower() in _NON_SECRET_USERINFO:
            return m.group(0)
        return f"<userinfo-redacted>@{host}{sep}"

    out = _SCP_LIKE_USERINFO.sub(_sub, text)
    # A BARE userinfo with NO separator at all (`token@host.example`) was classified as a credential
    # and then printed VERBATIM: neither pattern matched, so the secret reached `--json`
    # remote_url/push_url. `git remote get-url` prints exactly this for a scheme-less remote.
    return _SCP_LIKE_BARE.sub(_sub, out)


@dataclass
class Proc:
    """One subprocess result, with stdout and stderr kept APART.

    Parsing a merged stream is how a `warning: redirecting to …` on stderr became a repository
    path, and how a harmless warning could invent a leftover ref. Callers read `.out` for data and
    `.err` for diagnosis.
    """

    rc: int
    out: str = ""
    err: str = ""

    @property
    def detail(self) -> str:
        return (self.out or "") + (self.err or "")


@dataclass
class Verdict:
    """The answer. `writable` is the only field a caller should branch on.

    `remote_url`, `detail` and each `steps[].detail` are stored ALREADY REDACTED, so a caller that
    logs, reprs, or prints this object cannot leak a credential through them. (`repo_root`,
    `remote`, `probe_ref` and `head_sha` are not passed through `redact()`: they are filesystem
    paths, a remote NAME, and commit ids — no credential can reach them.) A passing verdict carries
    `reason=None`; only a refusal carries a `REASONS` token.
    """

    writable: bool
    reason: str | None = None
    detail: str = ""
    repo_root: str | None = None
    remote: str | None = None
    remote_url: str | None = None
    # The URL git actually PUSHES to (`remote.<name>.pushurl` when set), redacted for display.
    # All confirmation reads use the RAW value (never this one) — see `_check_writable`.
    push_url: str | None = None
    # "configured" when `get-url --push` answered, "fellback" when we had to use the fetch url
    # (git too old for `--push`, or no pushurl set and the read failed). Makes finding-"wrong
    # remote" residuals visible instead of silent.
    push_url_source: str | None = None
    probe_ref: str | None = None
    head_sha: str | None = None
    steps: list[dict] = field(default_factory=list)
    # None = cleanup could not be CONFIRMED (the confirming read failed) — never "clean".
    # On an early return no read was attempted, so this also means "nothing to clean"; `steps`
    # shows whether a push was ever made, which is what distinguishes the two cases.
    cleaned_up: bool | None = None
    kept_ref: bool = False
    # A probe ref that SURVIVED on the remote, reported ALONGSIDE `reason` so a leftover does not
    # erase the primary verdict (a credential that cannot write).
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
                "push_url": redact(self.push_url or ""),
                "push_url_source": self.push_url_source,
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


# Git variables that REDIRECT which repository git operates on, or that trace traffic. Removed.
_GIT_REDIRECT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
    "GIT_CURL_VERBOSE",
)
# Traces are prefix-matched (`GIT_TRACE`, `GIT_TRACE_PACKET`, … — new ones keep appearing).
_GIT_TRACE_PREFIX = "GIT_TRACE"


def _child_env(env_extra: dict[str, str] | None = None) -> dict[str, str]:
    """The env the probe runs under.

    Strips only what can REDIRECT the repository or TRACE traffic. Credential-carrying config
    (`GIT_CONFIG_*`, `GIT_SSH_COMMAND`) is kept on purpose: a box that injects its write credential
    that way must be probed through the same path its real `git push` uses, or the tool refuses a
    credential that can write — a false block, and the inversion of its purpose.
    """
    drop = set(_GIT_REDIRECT_ENV)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in drop and not k.startswith(_GIT_TRACE_PREFIX)
    }
    # Never let git prompt for a credential: a preflight blocked on an interactive username prompt
    # is a hung lane, not a verdict. `setdefault` so a box that deliberately supplies an askpass
    # helper keeps it (see above).
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_ASKPASS", "true")
    if env_extra:
        env.update(env_extra)
    return env


def _insteadof_env(url: str, alias: str, slot: int) -> dict[str, str]:
    """Env that makes git rewrite the inert `alias:` to `url`, keeping the secret OUT of argv.

    `GIT_CONFIG_COUNT`/`GIT_CONFIG_KEY_n`/`GIT_CONFIG_VALUE_n` (kept by `_child_env`) define the
    rewrite inside the PROCESS ENV instead of on the command line.

    ⛔ WHAT THIS BUYS, EXACTLY — do not overstate it. argv (`/proc/<pid>/cmdline`) is mode 0444:
    readable by EVERY process on the box, including other users and `ps`. `/proc/<pid>/environ` is
    mode 0400, owned by the lane's own user. On a cell where all lanes run as the SAME user a
    sibling lane can still read it (measured), so this is a narrowing, not isolation: it removes the
    blank-world-readable copy and the `ps` snapshot, and it separates lanes that run as different
    users. A future lane should not treat the value as secret from its own user.

    `slot` is the absolute `GIT_CONFIG_*` index to occupy. It is passed in rather than derived
    from the ambient `GIT_CONFIG_COUNT` because a caller may need SEVERAL rewrites in one env:
    deriving the slot inside this function would make every call return the SAME index, so the
    later `GIT_CONFIG_KEY_n` would overwrite the earlier one and the first alias would be
    unnameable — git would then fail to resolve it and the probe would refuse a credential that
    CAN write (a false block, the one failure this tool must never add).
    """
    return {
        f"GIT_CONFIG_KEY_{slot}": f"url.{url}.insteadOf",
        f"GIT_CONFIG_VALUE_{slot}": alias,
        "GIT_CONFIG_COUNT": str(slot + 1),
    }


def _read_targets(remote: str, url_raw: str, push_urls_raw: list[str]) -> list[str]:
    """What to hand `git ls-remote` for EVERY push target.

    * push urls identical to the fetch url (the common case) -> address the remote BY NAME. No URL
      on argv at all, which is both correct (git resolves it itself) and exposure-free.
    * a distinct push url with NO credential (`/path`, `ssh://git@host`) -> the raw url is safe on
      argv and is the only way to reach a push-only target.
    * a distinct push url WITH a credential -> an `insteadOf` alias (`_read_env` supplies the
      rewrite), so the secret lives in the environment and never in the world-readable process
      table.
    """
    if push_urls_raw == [url_raw]:
        return [remote]
    out = []
    for idx, u in enumerate(push_urls_raw):
        if _url_has_credential(u):
            out.append(f"gh-write-preflight-push{idx}:")
        else:
            out.append(u)
    return out


def _read_env(remote: str, url_raw: str, push_urls_raw: list[str]) -> dict[str, str]:
    """The env that makes `_read_targets` resolve (empty when reading by name).

    Every credential-bearing target gets its OWN `GIT_CONFIG_*` slot, counting up from the ambient
    `GIT_CONFIG_COUNT` so a box that already injects config through that channel is not clobbered.
    """
    if push_urls_raw == [url_raw]:
        return {}
    env: dict[str, str] = {}
    slot = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    for idx, u in enumerate(push_urls_raw):
        if _url_has_credential(u):
            env.update(_insteadof_env(u, f"gh-write-preflight-push{idx}:", slot))
            slot += 1
    return env


def _url_has_credential(url: str) -> bool:
    """Does this URL carry a secret in its userinfo (or a token anywhere)?

    argv is world-readable (`/proc/<pid>/cmdline` is 0444) and lanes share a cell, so a URL that
    embeds a credential must NEVER be handed to git as an argument. `git@host` (an SSH user, no
    password) does NOT count — it carries no secret, and refusing it would be a false block.
    """
    # ⛔ ONE DEFINITION OF "SECRET" FOR BOTH DIRECTIONS. This used `_REDACT_PATTERNS[3:]` (bare
    # `ghp_…`/`github_pat_…` shapes only), so a credential carried as a QUERY PARAMETER
    # (`…/x.git?access_token=SECRET`) was masked by `redact()` but NOT treated as a credential —
    # and `_read_targets` therefore handed the RAW url to `git ls-remote` on argv (0444), while
    # real git even echoes the query token in its error text. `[2:]` adds the keyword form
    # (`token=`/`api_key=`/`password=`), which requires a `=`/`:` separator so ordinary paths are
    # unaffected.
    if any(p.search(url) for p, _ in _REDACT_PATTERNS[2:]):   # bare token + keyword shapes
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        # ⛔ AN UNPARSEABLE-FOR-URLLIB URL IS TREATED AS CARRYING A CREDENTIAL, never as data.
        # `urlsplit` raises `ValueError("Invalid IPv6 URL")` for a netloc holding an unmatched
        # `[` (and for netloc chars that NFKC-decompose into `/`, `?`, `#`, `@`, `:`), so a
        # pushurl like `https://user:pa[ss@host/r.git` fell through this arm as "no credential"
        # while `redact()` masked the same string — and the raw password went to `git ls-remote`
        # on argv (0444 on a shared cell) for the reachability and confirmation reads.
        #
        # ⚠️ THE DEFECT IS THE ARGV COPY AND ONLY THAT — do not restate it as a false block.
        # Measured with real git: `git ls-remote --heads 'https://user:pa[ss@example.invalid/r.git'`
        # strips the userinfo and proceeds to DNS/connect, exactly like the bracket-free control,
        # so `urlsplit`'s refusal is urllib's rule and NOT git's — the read never became
        # `remote-unreachable` because of this arm. Returning True costs nothing either way: the
        # target is then reached through the `insteadOf` alias, which git rewrites to this same
        # URL string (measured); it changes WHERE the string lives (0400 env), not what git does.
        return True
    if parts.password:
        return True
    # A userinfo with a password and NO parseable scheme, e.g. the scp-like
    # `user:secret@host:path`. `urlsplit` reads the leading `user:` as the SCHEME, so
    # `parts.password` stays None and the check above cannot see it — measured:
    # `_url_has_credential("user:hunter2@host:repo.git")` returned False while the raw URL was
    # handed to git on argv, which is mode 0444 on a shared cell. The userinfo is the text before
    # the FIRST `@`, and a userinfo can never contain `/`, so that pair of facts separates a
    # credential from a path that merely contains `@`. Erring toward True is safe and is the right
    # asymmetry: a false positive only routes the target through the `insteadOf` env rewrite, which
    # git resolves to the same URL, while a false negative puts a secret on world-readable argv.
    userinfo = url.split("@", 1)[0]
    if "@" in url and "/" not in userinfo and ":" in userinfo:
        return True
    # A LONE userinfo with no scheme (`token@host:path`) is the SAME credential as
    # `https://token@host/…` — it must not be treated as data just because `urlsplit` sees no
    # scheme. `git@host:path` and a bare local path stay non-secrets (the exemption list, and the
    # `"/" not in userinfo` test above).
    #
    # ⚠️ DELIBERATE OVER-DETECTION, accepted. An account-name user we do not list
    # (`daniel-ospina@github.com:o/r.git`, `repo@backup.git`) is treated as a credential too, so it
    # is routed through the `insteadOf` alias and its display is masked. That is not free: the
    # `remote:`/`remote_url` line shows `<userinfo-redacted>@host:` instead of the account name.
    # It is still the right side of the asymmetry — a false positive costs a diagnostic, and the
    # HOST and PATH (which is what identifies two different remotes) are preserved, while a false
    # negative puts a token on world-readable argv. Do not "fix" this by adding a token-shape
    # heuristic: an opaque token has no shape, and the heuristic would reopen the leak on exactly
    # the credentials hardest to recognise.
    if (
        "@" in url
        and "/" not in userinfo
        and ":" not in userinfo
        and userinfo
        and userinfo.lower() not in _NON_SECRET_USERINFO
    ):
        return True
    # ⛔ A LONE userinfo is a credential too: `https://mytoken123@host/p.git` is a form GitHub
    # documents, carries no `ghp_`/`github_pat_` prefix, and has no password field — so every check
    # above misses it while `redact()` hides it in the display, and the raw token lands on argv
    # (0444) on a shared cell. Measured false negative: userinfo `mytoken123` -> False.
    # Only the KNOWN non-secret account names are exempt: `git@`/`ssh@`/`hg@` carry no secret, and
    # treating them as credentials would route every ordinary SSH remote through the alias path (a
    # false block on the most common configuration there is).
    if parts.scheme and parts.netloc and "@" in parts.netloc:
        candidate = parts.netloc.rsplit("@", 1)[0].split(":", 1)[0]
        if candidate and candidate.lower() not in _NON_SECRET_USERINFO:
            return True
    return False


class Runner:
    """A thin, testable seam over subprocess.

    Tests inject a Runner subclass rather than monkeypatching `subprocess`; the real implementation
    is the only thing that ever runs git for real.
    """

    def run(
        self,
        argv: list[str],
        cwd: str | None = None,
        timeout: int = 120,
        env_extra: dict[str, str] | None = None,
    ) -> Proc:
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                # `replace` so a non-UTF-8 byte in git's error text cannot raise out of the
                # verdict path and exit 1 with no marker.
                errors="replace",
                # No controlling terminal on stdin. `GIT_TERMINAL_PROMPT=0` suppresses GIT's own
                # prompt only; an ssh passphrase prompt (`GIT_SSH_COMMAND`, kept so a box can
                # inject its key), a gpg pinentry for a signed push, or a credential helper that
                # reads stdin would all inherit the lane's terminal — a 120 s stall plus possible
                # keystroke theft.
                stdin=subprocess.DEVNULL,
                env=_child_env(env_extra),
            )
        except FileNotFoundError:
            # Covers a missing binary AND a missing `cwd`; check_writable's is_dir() guard means
            # the cwd case cannot reach here, so the message stays true.
            return Proc(127, err=f"{argv[0]}: not found")
        except subprocess.TimeoutExpired:
            return Proc(124, err=f"{argv[0]}: timed out after {timeout}s")
        except OSError as exc:
            # EMFILE/ENOMEM/EACCES/E2BIG… — any of these used to escape as an unmarked traceback
            # (exit 1 with no GH-WRITE-UNAVAILABLE token), breaking the contract.
            return Proc(126, err=f"{argv[0]}: {exc}")
        return Proc(proc.returncode, proc.stdout or "", proc.stderr or "")


def _neutral_push_env(remote: str) -> dict[str, str]:
    """Config that stops ambient settings from widening or breaking the probe push.

    Set through `GIT_CONFIG_*` (env, kept by `_child_env`) rather than `git -c` so argv stays the
    documented shape and no caller's flags are inherited.

    ⛔ OCCUPY SLOTS **ABOVE** THE AMBIENT COUNT, never from zero. `GIT_CONFIG_COUNT=3` with keys
    0-2 OVERWRITES every ambient slot, and on a box that injects its credential through that
    channel (`url.<base>.insteadOf` + `credential.helper`) this REPLACED the working credential with
    git's fallback — measured: a repo whose push target is resolved by an ambient `insteadOf`
    returned `writable=False, push-refused` while a raw `git push` under the same env exited 0, and
    `credential.helper` silently changed from the lane's helper to `!gh auth git-credential`. That
    is a false block — the one failure this module says it must never add. `_read_env` counts up
    from the ambient count for the same reason; do the same here.
    """
    settings = {
        "push.followTags": "false",
        f"remote.{remote}.mirror": "false",
        "push.useForceIfIncludes": "false",
        # A signed push is refused outright by a remote (or a hook) that does not accept signatures
        # (`fatal: the receiving end does not support --signed push`), and push options are refused
        # by a remote that does not advertise them. Both then read as "read-only credential".
        # An EMPTY value disables the ambient push options — `--no-push-option` does NOT
        # countermand a configured `push.pushOption` (measured: still rc 128).
        "push.gpgSign": "false",
        "push.pushOption": "",
    }
    base = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    env: dict[str, str] = {"GIT_CONFIG_COUNT": str(base + len(settings))}
    for offset, (key, value) in enumerate(settings.items()):
        env[f"GIT_CONFIG_KEY_{base + offset}"] = key
        env[f"GIT_CONFIG_VALUE_{base + offset}"] = value
    return env


def _probe_ref(prefix: str = "gh-write-preflight") -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{prefix.strip('/')}/{stamp}-{os.getpid()}-{rand}"


# A prefix that cannot form a valid refname, or that starts with `-`, must be refused at the CLI
# boundary rather than handed to git as an option-looking argv element. This belt stops the
# OBVIOUS shapes; `git check-ref-format` is the authoritative gate (see `_prefix_ok`) because
# refname rules (no component starting `.`, none ending `.lock`, no control chars, …) cannot be
# enumerated safely by hand.
_BAD_PREFIX_CHARS = set(" ~^:?*[\\")


def _valid_prefix(prefix: str) -> bool:
    if not prefix or prefix.startswith("-") or prefix.strip("/") != prefix:
        return False
    if ".." in prefix or "//" in prefix or "@{" in prefix:
        return False
    if any(ord(ch) <= 0x20 or ord(ch) == 0x7F for ch in prefix):
        return False
    for comp in prefix.split("/"):
        if comp.startswith(".") or comp.endswith(".lock"):
            return False
    return not (_BAD_PREFIX_CHARS & set(prefix))


def _prefix_ok(prefix: str, runner: Runner) -> bool:
    """Authoritative prefix check: ask git whether `<prefix>/probe` is a legal refname.

    The hand-written rules above are a fast belt; this is the verdict. Without it, an impossible
    refname (`.`, `foo.lock`, a component starting with `.`) renders a ref git rejects, and the
    tool reports `GH-WRITE-UNAVAILABLE(push-refused)` on a fully writable remote — a usage
    mistake reported as "your credential cannot write".
    """
    if not _valid_prefix(prefix):
        return False
    proc = runner.run(["git", "check-ref-format", f"refs/heads/{prefix}/probe"])
    # rc==1 is git's own verdict: not a legal refname. 0 is a pass; 127 = git absent and
    # 124/126 = this check could not run, so those fall back to the belt (the verdict path fails
    # on its own `git --version` check and reports `git-absent`) rather than render a broken
    # environment as "git rejects this refname".
    return proc.rc != 1


def _ref_state(
    runner: Runner,
    root: str,
    remote: str,
    ref: str,
    env_extra: dict[str, str] | None = None,
) -> str:
    """Is `ref` on `remote`? Tri-state, because "could not check" is not "absent".

    Returns ``present`` / ``absent`` / ``unverifiable``.
    """
    proc = runner.run(
        ["git", "ls-remote", remote, f"refs/heads/{ref}"], cwd=root, env_extra=env_extra
    )
    if proc.rc != 0:
        return "unverifiable"
    return "present" if proc.out.strip() else "absent"


def _ref_reads(
    runner: Runner,
    root: str,
    urls: list[str],
    ref: str,
    env_extra: dict[str, str] | None = None,
) -> list[tuple[str, str, str]]:
    """Per-target (url, state, sha). ONE read per target, so no caller re-reads and disagrees.

    The sha is what distinguishes "a ref exists" from "OUR write landed". Returning both from the
    same read is deliberate: a second, separate read of the first target (as an earlier revision
    did) can fail transiently and turn a proven write into `verify-mismatch` while the multi-read
    says `present` — a false block on exactly the multi-pushurl configs this must get right.
    """
    reads: list[tuple[str, str, str]] = []
    for u in urls:
        proc = runner.run(
            ["git", "ls-remote", u, f"refs/heads/{ref}"], cwd=root, env_extra=env_extra
        )
        if proc.rc != 0:
            reads.append((u, "unverifiable", ""))
        elif proc.out.strip():
            reads.append((u, "present", proc.out.split()[0]))
        else:
            reads.append((u, "absent", ""))
    return reads


def _ref_state_multi(runner: Runner, root: str, urls: list[str], ref: str,
                     env_extra: dict[str, str] | None = None) -> str:
    """Is `ref` on ANY of `urls`?

    ⛔ A remote may define SEVERAL pushurls, and `git push` writes to ALL of them while
    `git remote get-url --push` (without `--all`) lists only the first. Confirming against the
    first alone reports `cleaned_up=True` while the ref survives on another — a false verified
    claim and litter on a shared remote. So: present if ANY says present; unverifiable if none
    says present and ANY could not be read; absent only when every read positively says absent.

    An EMPTY list is `unverifiable`, never `absent` — a vacuous truth here would report a verified
    clean from zero reads.
    """
    reads = _ref_reads(runner, root, urls, ref, env_extra=env_extra)
    if not reads:
        return "unverifiable"
    states = [state for _u, state, _sha in reads]
    if "present" in states:
        return "present"
    if "unverifiable" in states:
        return "unverifiable"
    return "absent"


def _push_verify_cleanup(
    runner: Runner,
    root: str,
    remote: str,
    read_targets: list[str],
    read_env: dict[str, str],
    verdict: Verdict,
    head: str,
    keep_ref: bool,
    step: Callable[[str, Proc], bool],
) -> None:
    """Push the probe ref, verify it, delete it, verify the delete.

    Mutates `verdict`; returns nothing. The real invariant is that this helper must not RAISE on
    an ordinary path: the caller wraps it in a `try/finally` and gates its retry on
    `sys.exc_info()[0] is not None`, so a swallowed exception here would make that guard fire a
    second delete it must not issue. (The `finally` lives in the CALLER, not here, so a bare
    `return` in this body is not the hazard that sentence used to describe.)
    """
    push_proc = runner.run(
        [
            "git", "push", "--porcelain", "--no-verify", "--no-follow-tags", remote,
            f"HEAD:refs/heads/{verdict.probe_ref}",
        ],
        cwd=root,
        # ⛔ NEUTRALISE AMBIENT PUSH CONFIG (through the env, so argv stays the documented shape).
        # `push.followTags=true` made the probe push an annotated TAG to the shared remote as well —
        # a mutation BEYOND the probe ref that survived cleanup while the verdict still said
        # `cleaned_up: true` (measured) — and a tag push is precisely what `publish-pypi`
        # (`tags: v*`) triggers on. `remote.<name>.mirror=true` made the push fail outright and was
        # reported as a credential refusal on a writable remote. `--no-follow-tags` covers the CLI
        # flag; these cover the config, which the flag alone does not override everywhere.
        env_extra=_neutral_push_env(remote),
    )
    push_ok = step("push", push_proc)
    # ⛔ Parse the write flag from the RAW stdout — NEVER from `steps[-1]["detail"]`, which `step()`
    # has already truncated to its last 800 chars and redacted. A remote that prints >800 chars of
    # advisory output while ACCEPTING the push pushes the porcelain line out of that window, turning
    # a real write into `push-refused` (a false block: the lane parks deliverable work). MEASURED
    # against real git: a bare remote whose `pre-receive` prints 40 advisory lines and exits 0.
    wrote = False
    for line in push_proc.out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip() in ("*", "+"):
            wrote = True
            break

    if push_ok and wrote:
        # (7) Confirm on the REMOTE(S) WE PUSHED TO, not from push's own word. ONE read per target,
        # carrying the sha — a separate second read of the first target can fail transiently and
        # flip a proven write into `verify-mismatch` (a false block) while this read says present.
        reads = _ref_reads(runner, root, read_targets, verdict.probe_ref, env_extra=read_env)
        # ⛔ EVERY target must be present AT OUR SHA. A ref that exists at another sha is not our
        # write; a target that cannot be read is not proven.
        bad = [(u, st, sha) for u, st, sha in reads if not (st == "present" and sha == head)]
        verdict.steps.append(
            {
                "step": "ls-remote-verify",
                # The step's rc follows the VERDICT-DECIDING set, not mere readability: keying it on
                # readability rendered `[ok] ls-remote-verify (rc=0)` immediately above
                # `GH-WRITE-UNAVAILABLE(verify-mismatch)` — a false-PASS line in the log an
                # operator reads first.
                "rc": 1 if bad else 0,
                "detail": redact(
                    "; ".join(f"{u.split('@')[-1]}={st}:{sha[:12] or '-'}" for u, st, sha in reads)
                ),
            }
        )
        if not bad:
            verdict.writable = True
        else:
            verdict.reason = "verify-mismatch"
            verdict.detail = redact(
                "push reported success but ls-remote does not confirm "
                f"refs/heads/{verdict.probe_ref} at {head[:12]}: "
                + "; ".join(f"{u.split('@')[-1]}={st}:{sha[:12] or '<nothing>'}" for u, st, sha in bad)
            )
    elif push_ok:
        # No line claimed a write -> "up to date": nothing was created, so nothing is proven.
        # ⛔ Do NOT `return` here and do NOT claim cleanup: a pre-existing probe ref (or a remote
        # that accepted the update but reported it differently) still has to be DELETED and the
        # deletion CONFIRMED below. Returning early here reported `cleaned_up=True` with no read at
        # all — abandoning the litter it was claiming not to have left.
        verdict.reason = "push-refused"
        verdict.detail = (
            "push exited 0 but reported NO ref update (Everything up-to-date) — nothing was "
            "written, so write capability is not proven"
        )
    else:
        # The defect this tool exists to catch: a read-only credential fails HERE. Do NOT conclude
        # the ref is absent — a transport error can arrive AFTER the remote applied the update, so
        # cleanup below still runs and is CONFIRMED by ls-remote.
        verdict.reason = "push-refused"
        verdict.detail = verdict.steps[-1]["detail"]
        # ⛔ A timeout is NOT a refusal. rc 124 is OUR OWN deadline (Runner maps TimeoutExpired),
        # so the credential's capability is UNKNOWN, not absent. Without this note the verdict reads
        # "read-only credential" and a lane goes hunting for a permissions problem that is really a
        # slow remote — the same "plausible diagnosis for the wrong cause" the tool exists to end.
        # A signalled push (rc < 0 - e.g. the OOM killer, documented for these cells) or one that
        # produced no output at all says nothing about the credential either.
        if push_proc.rc == 124 or push_proc.rc < 0 or not (push_proc.err or "").strip():
            # A DISTINCT TOKEN, not just prose: the published contract is that a caller can branch
            # on `reason` without parsing detail, and `push-refused` means the credential is at
            # fault. A timeout or a kill is not that, so pinning the token to `push-refused` parked
            # deliverable work on a slow or OOM-killed push — the exact diagnosis error the note
            # below exists to prevent.
            verdict.reason = "push-unknown"
            verdict.detail = (
                f"{verdict.detail} | the push TIMED OUT (our own deadline) — write capability is "
                "UNKNOWN, not refused; re-run before concluding the credential is read-only"
            )

    # (8) CLEANUP — one place. The `--keep-ref` arm returns EARLY (a bare `return`: this helper
    # returns nothing), and that is safe because the `try/finally` that could swallow an exception
    # is in the CALLER, not in this body — there is no `finally` here to return through.
    if keep_ref:
        verdict.kept_ref = True
        # A REQUESTED survivor: the litter was asked for, so unlike the unrequested survivor below
        # it does NOT change the exit code (`writable` stays True, the run exits 0) — see
        # `test_keep_ref_is_explicit_and_the_message_does_not_lie`, which pins that exit 0.
        # `cleaned_up=False` is documented as "a PROVEN SURVIVOR". On a `--keep-ref` run where the
        # push was refused nothing was ever created, so claiming a survivor overloads the field a
        # consumer branches on; `None` (not applicable / unknown) is the honest value.
        # Keyed on EVIDENCE of a write, not on `push_ok`: an "up to date" push exits 0 having
        # created nothing, and calling that a PROVEN SURVIVOR invents litter while `leftover_ref`
        # stays False beside it.
        verdict.cleaned_up = False if (push_ok and wrote) else None
        return

    dp = runner.run(
        # `--` so a ref that begins with `-` can never be parsed as an option by `git push`
        # (a `--probe-ref-prefix=-f` renders `-f/<stamp>`, which without this makes the delete
        # exit 129 and leaves the ref behind while reporting the credential unwritable).
        ["git", "push", "--no-verify", remote, "--delete", "--", verdict.probe_ref],
        cwd=root,
        # The same ambient config breaks a --delete ("--mirror can't be combined with refspecs"),
        # which then read as `delete-failed` / "PROBE REF SURVIVED" on a remote the probe had in
        # fact written to and could clean: measured with `remote.origin.mirror=true`.
        env_extra=_neutral_push_env(remote),
    )
    step("delete", dp)

    # CONFIRM ON THE REMOTE(S) WE PUSHED TO, not from the delete's own exit code — the same rule
    # the push path follows. `cleaned_up=True` only on a positive "ref absent" read of EVERY push
    # url; an unreadable answer is `None` (unconfirmed), never "clean".
    state = _ref_state_multi(
        runner, root, read_targets, verdict.probe_ref, env_extra=read_env
    )
    if state == "absent":
        verdict.cleaned_up = True
    elif state == "present":
        verdict.cleaned_up = False
        verdict.leftover_ref = True
        # A surviving ref is NOT a pass: the tool must not report writable while it has left
        # garbage on a shared remote.
        verdict.writable = False
        if verdict.reason is None:
            verdict.reason = "delete-failed"
        note = (
            f"PROBE REF SURVIVED on {remote}: refs/heads/{verdict.probe_ref} "
            f"({verdict.steps[-1]['detail']})"
        )
        verdict.detail = f"{verdict.detail} | {note}" if verdict.detail else note
    else:
        verdict.cleaned_up = None
        verdict.leftover_ref = False
        # Do NOT assert that the delete failed — it may have SUCCEEDED and only the confirming
        # read failed (a rate limit, a transient error). Report what is actually known.
        note = (
            f"cleanup UNCONFIRMED — the delete's outcome could not be confirmed "
            f"(delete rc={dp.rc}) and ls-remote could not be read for "
            f"refs/heads/{verdict.probe_ref}; check the remote before assuming it is clean"
        )
        verdict.detail = f"{verdict.detail} | {note}" if verdict.detail else note



def check_writable(
    repo: str | Path = ".",
    remote: str = "origin",
    runner: Runner | None = None,
    keep_ref: bool = False,
    probe_ref_prefix: str | None = None,
) -> Verdict:
    """Answer "can this credential write repository contents here?".

    Returns a Verdict. Never raises for an expected failure, and never returns `writable=False`
    without a `REASONS` token.
    """
    runner = runner or Runner()
    # ⛔ VALIDATE THE PREFIX AT THE API SEAM TOO. `main()` refuses a bad prefix with exit 2, but a
    # programmatic caller goes straight to `check_writable`, where an unrenderable refspec previously
    # surfaced as `GH-WRITE-UNAVAILABLE(push-refused)` — blaming the CREDENTIAL for a usage
    # mistake, on a remote that writes fine. Raises (rather than returning a verdict) because no
    # credential verdict exists to return: nothing was probed.
    if probe_ref_prefix is not None:
        if not _valid_prefix(probe_ref_prefix):
            raise ValueError(
                f"invalid probe_ref_prefix {probe_ref_prefix!r}: it must be a ref-shaped path "
                f"segment (no empty components, no '..', no leading '-')"
            )
        if not _prefix_ok(probe_ref_prefix, runner):
            raise ValueError(
                f"invalid probe_ref_prefix {probe_ref_prefix!r}: git check-ref-format rejects "
                f"the ref name it would produce"
            )
    # ⛔ THE IN-FLIGHT VERDICT IS THREADED THROUGH, not thrown away. Building a fresh Verdict on the
    # exception path discarded `probe_ref`, `steps` and `leftover_ref` — so the one branch where
    # litter is MOST likely (the probe died mid-push) was the branch that reported
    # `leftover_ref: false` and did not name the ref an operator must delete. Measured: exception on
    # the verify read with deletes denied left `refs/heads/gh-write-preflight/…` on the real remote
    # while the verdict said `probe_ref=None, leftover_ref=False`.
    seen: list[Verdict] = []
    try:
        return _check_writable(repo, remote, runner, keep_ref, probe_ref_prefix, seen)
    except Exception as exc:  # the contract is "no exit 1 without a marker"
        verdict = seen[0] if seen else Verdict(False, repo_root=str(repo), remote=remote)
        verdict.writable = False
        verdict.reason = "internal-error"
        note = redact(f"{type(exc).__name__}: {exc}")
        verdict.detail = f"{verdict.detail} | {note}" if verdict.detail else note
        pushed = any(s.get("step") == "push" for s in verdict.steps)
        if pushed and verdict.probe_ref and verdict.cleaned_up is None and not verdict.kept_ref:
            # A ref may survive this path; say so rather than implying a clean remote.
            verdict.detail += (
                f" | the probe ref {verdict.probe_ref} may still be on {remote} — clean it up"
            )
        return verdict


def _check_writable(
    repo: str | Path,
    remote: str,
    runner: Runner,
    keep_ref: bool,
    probe_ref_prefix: str | None = None,
    seen: list[Verdict] | None = None,
) -> Verdict:
    repo_path = Path(repo).expanduser()

    # (0) git present?
    if runner.run(["git", "--version"]).rc != 0:
        return Verdict(False, "git-absent", detail="git --version failed")

    # (1) the target must be a directory. Checked FIRST so a bad `--repo` cannot masquerade as
    # "git is missing" (a nonexistent cwd raises FileNotFoundError inside subprocess).
    if not repo_path.is_dir():
        return Verdict(False, "not-a-repo", detail=f"not a directory: {repo_path}")

    # (2) is this a work tree, and where is its root? stdout ONLY.
    p = runner.run(["git", "rev-parse", "--show-toplevel"], cwd=str(repo_path))
    root_lines = [ln.strip() for ln in p.out.splitlines() if ln.strip()]
    if p.rc != 0 or not root_lines:
        return Verdict(False, "not-a-repo", detail=redact(p.detail))
    root = root_lines[-1]

    # (3) does the named remote exist? stdout only, first line.
    p = runner.run(["git", "remote", "get-url", remote], cwd=root)
    url_lines = [ln.strip() for ln in p.out.splitlines() if ln.strip()]
    if p.rc != 0 or not url_lines:
        return Verdict(False, "no-remote", detail=redact(p.detail), repo_root=root, remote=remote)
    # ⛔ KEEP A RAW COPY FOR ARGV. `redact()` rewrites URL userinfo, so passing the redacted value
    # to git asks it to contact a URL the config never named — `ssh://git@host/…` (no secret at
    # all) becomes `ssh://<userinfo-redacted>@host/…` and the read fails, refusing a credential
    # that can write. The redacted form is for DISPLAY only.
    url_raw = url_lines[0]
    url = redact(url_raw)

    # (3b) THE PUSH URL(S), NOT THE FETCH URL. git pushes to `remote.<name>.pushurl` when set, so a
    # fork-style config (`url=…/fetch.git`, `pushurl=…/push.git`) means every confirmation read
    # below would ask a DIFFERENT remote than the one we wrote to. And a remote may define SEVERAL
    # pushurls — the push writes to all of them, so all of them must be confirmed. `--all` is what
    # lists them; without it git reports only the first.
    pa = runner.run(["git", "remote", "get-url", "--push", "--all", remote], cwd=root)
    push_lines = [ln.strip() for ln in pa.out.splitlines() if ln.strip()]
    if pa.rc == 0 and push_lines:
        push_urls_raw, push_url_source = push_lines, "configured"
    else:
        push_urls_raw, push_url_source = [url_raw], "fellback"
    push_url = redact(push_urls_raw[0])

    # (4) HEAD must resolve — we push an EXISTING commit, never a new one.
    p = runner.run(["git", "rev-parse", "HEAD"], cwd=root)
    head_lines = [ln.strip() for ln in p.out.splitlines() if ln.strip()]
    if p.rc != 0 or not head_lines:
        return Verdict(
            False,
            "no-head",
            detail=redact(p.detail),
            repo_root=root,
            remote=remote,
            remote_url=url,
            push_url=push_url,
            push_url_source=push_url_source,
        )
    head = head_lines[-1]

    verdict = Verdict(
        False,
        repo_root=root,
        remote=remote,
        remote_url=url,
        push_url=push_url,
        push_url_source=push_url_source,
        head_sha=head,
        probe_ref=_probe_ref(probe_ref_prefix or "gh-write-preflight"),
    )
    # Publish the in-flight verdict so the exception wrapper can report what the probe learned
    # (probe_ref, steps) instead of discarding it exactly when litter is most likely.
    if seen is not None:
        seen.append(verdict)
    # Confirmation reads address the PUSH target(s). When the push url differs from the fetch url we
    # must name a URL rather than the remote; a credential-bearing URL goes through an `insteadOf`
    # alias so the secret stays in the env (0400), never on argv (0444, world-readable on a shared
    # cell).
    read_targets = _read_targets(remote, url_raw, push_urls_raw)
    read_env = _read_env(remote, url_raw, push_urls_raw)

    def step(name: str, proc: Proc) -> bool:
        # REDACT AT INGESTION. Nothing downstream has to remember.
        verdict.steps.append(
            {"step": name, "rc": proc.rc, "detail": redact(proc.detail[-800:])}
        )
        return proc.rc == 0

    # (5) Reachability of the target we will actually push to. ⛔ It MUST be the PUSH target: the
    # fetch url would refuse a writable push target whose upstream is retired (a fork config) and
    # then report the refusal against a remote that was never measured.
    if not step(
        "ls-remote",
        runner.run(
            ["git", "ls-remote", "--heads", read_targets[0]], cwd=root, env_extra=read_env
        ),
    ):
        verdict.reason, verdict.detail = "remote-unreachable", verdict.steps[-1]["detail"]
        return verdict

    # (6) THE REAL WRITE. Push the commit already at HEAD onto a namespaced throwaway ref.
    # `--porcelain` is REQUIRED: a push that is "Everything up-to-date" exits 0 having written
    # NOTHING, so an exit code alone does not prove a write happened. The ref flag must be `*`
    # (new) or `+` (updated).
    # ⛔ CLEANUP RUNS ON EVERY PATH THAT UNWINDS. Steps (6)-(8) live in a helper so the caller can
    # wrap them in try/finally: a Ctrl-C (`SIGINT` -> `KeyboardInterrupt`) or any exception AFTER
    # the push has landed would otherwise leave the probe ref on a REMOTE every lane shares.
    # ⛔ NOT a fleet `SIGTERM`: Python translates only `SIGINT` into an exception, so `SIGTERM`
    # terminates without unwinding and this `finally` never runs — stated as a residual in the
    # module docstring, not claimed away here.
    try:
        _push_verify_cleanup(
            runner, root, remote, read_targets, read_env, verdict, head, keep_ref, step
        )
    finally:
        # ⛔ ONLY WHEN AN EXCEPTION IS ACTUALLY PROPAGATING. An earlier revision guarded on the
        # verdict alone (`not cleaned_up`), so the retry ALSO ran on the ordinary "delete failed"
        # path, deleted the ref for real, and never re-read — leaving the verdict claiming
        # `leftover_ref=True` / "PROBE REF SURVIVED" for a remote that was already clean: a false
        # block (the one failure this tool must never add) plus a false litter claim, with two
        # deletes issued while `steps` recorded one. `sys.exc_info()` is the in-flight test; on the
        # ordinary path the cleanup has ALREADY reported the truth and must not be second-guessed.
        if sys.exc_info()[0] is not None and not keep_ref and not verdict.cleaned_up:
            try:
                rp = runner.run(
                    ["git", "push", "--no-verify", remote, "--delete", "--", verdict.probe_ref],
                    cwd=root,
                    env_extra=_neutral_push_env(remote),
                )
                verdict.steps.append(
                    {"step": "delete-retry", "rc": rp.rc, "detail": redact(rp.detail[-800:])}
                )
                # The retry's effect is READ BACK, so the verdict describes the remote as it now is
                # rather than as it was before the retry.
                state = _ref_state_multi(
                    runner, root, read_targets, verdict.probe_ref, env_extra=read_env
                )
                if state == "absent":
                    verdict.cleaned_up = True
                    verdict.leftover_ref = False
            except Exception:
                pass

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
        "--probe-ref-prefix",
        default=None,
        help=(
            "namespace for the throwaway probe ref (default: gh-write-preflight). Use this on a "
            "repo whose ruleset only permits certain branch patterns, or the probe is refused "
            "for a credential that can write."
        ),
    )
    parser.add_argument(
        "--keep-ref",
        action="store_true",
        help="do NOT delete the probe ref (debugging only — leaves a ref on the remote)",
    )
    args = parser.parse_args(argv)

    if args.probe_ref_prefix is not None and not _valid_prefix(args.probe_ref_prefix):
        parser.error(
            f"invalid --probe-ref-prefix {args.probe_ref_prefix!r}: it must be a ref-shaped path "
            f"(no leading '-' or '/', no whitespace or control characters, no component starting "
            f"with '.' or ending '.lock', no ': ? * [ \\ ~ ^', no '..' or '@{{')"
        )
    if args.probe_ref_prefix is not None and not _prefix_ok(args.probe_ref_prefix, Runner()):
        parser.error(
            f"invalid --probe-ref-prefix {args.probe_ref_prefix!r}: git check-ref-format rejects "
            f"refs/heads/{args.probe_ref_prefix}/probe as a refname"
        )

    verdict = check_writable(
        args.repo, args.remote, keep_ref=args.keep_ref, probe_ref_prefix=args.probe_ref_prefix
    )
    # Belt: the type promises a REASONS token; never emit a bare `(None)`.
    reason = verdict.reason if verdict.reason in REASONS else "internal-error"
    # Apply it to the PAYLOAD as well as the marker — but ONLY on a refusal. A passing verdict
    # legitimately carries `reason=None`, and stamping `internal-error` on it would report "no
    # verdict is available" about a credential that just proved it can write.
    if not verdict.writable:
        verdict.reason = reason

    # The marker ALWAYS goes to stderr, in both modes, so a caller that greps for it works with
    # --json too (stdout stays pure JSON there).
    if not verdict.writable:
        print(f"GH-WRITE-UNAVAILABLE({reason}) — {REASONS[reason]}", file=sys.stderr)

    if args.json:
        print(verdict.as_json())
    else:
        print(f"repo:   {verdict.repo_root or '<unresolved>'}")
        print(f"remote: {verdict.remote or '<none>'} {verdict.remote_url or ''}")
        if verdict.push_url and verdict.push_url != verdict.remote_url:
            # Name the remote that was actually MEASURED — on a fork-style config `remote:` above
            # names the fetch url, which is not where the probe pushed.
            print(f"push:   {verdict.push_url} ({verdict.push_url_source or 'configured'})")
        if verdict.head_sha:
            print(f"head:   {verdict.head_sha[:12]}")
        for s in verdict.steps:
            print(f"  [{'ok' if s['rc'] == 0 else 'FAIL'}] {s['step']} (rc={s['rc']})")
        if verdict.writable:
            # Key the wording on the TRI-STATE, never on `kept_ref` alone: claiming "deleted" when
            # cleanup could not be confirmed is the same verified-clean-from-absence-of-evidence
            # error the field itself was fixed to avoid — and this is the line lanes parse.
            if verdict.kept_ref:
                cleanup = f"probe ref {verdict.probe_ref} KEPT (--keep-ref)"
            elif verdict.cleaned_up is True:
                cleanup = f"probe ref {verdict.probe_ref} deleted"
            else:
                cleanup = (
                    f"probe ref {verdict.probe_ref} deletion UNCONFIRMED — check the remote "
                    f"(see stderr)"
                )
            print(f"GH-WRITE-OK({verdict.remote}) — push confirmed by ls-remote; {cleanup}")
        if verdict.detail:
            print(verdict.detail.strip()[-600:], file=sys.stderr)

    return EXIT_WRITABLE if verdict.writable else EXIT_UNAVAILABLE


if __name__ == "__main__":
    sys.exit(main())
