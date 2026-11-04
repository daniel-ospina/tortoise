#!/usr/bin/env python3
"""gh_write_preflight — can THIS lane's `gh` identity actually WRITE to its repo?

Why this exists
---------------
A landing lane's whole job is writes: post the verdict comment (which is the
`--evidence` artifact `record-review.sh` mints from and the rail reads), close
each constituent, merge the PR. On 2026-11-04 every landing lane on several
cloud cells could do **none** of them and discovered it *after* the analysis it
was dispatched to perform, so a batch that was ready to land got reported
BLOCKED (#10930). The refusal is always the same shape and always at the last
step:

    403 {"message":"Resource not accessible by personal access token"}

The trap is that **every read succeeds in this failure mode**, so nothing short
of a write can see it. Measured on one box, one minute, one repo
(2026-11-04T18:17Z, pi-cell-3, `daniel-ospina/tortoise`), changing ONLY the
identity:

    identity                       read repo   issue comment   PR comment   create ref
    gho_… OAuth (repo scope)       200         201             201          201
    github_pat_… fine-grained      200         201             403          403

and `gh auth status` lists BOTH, silently activating one. So a lane cannot tell
"my token is fine" from "my token cannot write" by reading anything at all.

The asymmetry is not a bug in GitHub: a fine-grained PAT is granted per resource
*kind*, and authorization is checked against the **target resource**, not the URL
route
(https://docs.github.com/en/rest/issues/comments — a comment on a PR *number*
needs `Pull requests: write` even though the path is `/issues/{n}/comments`,
while a comment on an issue number needs `Issues: write`). A token can therefore
hold `Issues: write` and no `pull_requests`/`contents` write, which is exactly
the shape measured above. `GET /repos/{o}/{r}`'s `permissions` object does NOT
report this: it reports the *user's* role (`push: true`), which is true even when
the token grants nothing — the field is a trap, is printed here for context, and
is never a verdict.

What it does
------------
Two real WRITES, each harmless and reversible, classified by the response:

1. `contents=write` — create `refs/heads/gh-write-preflight-<nonce>` at the
   default branch's head, then delete it. This is the permission `git push` and
   every merge need. Invisible (a ref creates no notification and no workflow —
   every `on: push` in this repo is branch-filtered to `main`).
2. `pull_requests=write` — `PATCH /repos/{o}/{r}/pulls/{n}` with the PR's
   **unchanged title**. A no-op: measured 2026-11-04, the PR's `title` and
   `updated_at` are byte-identical before and after, so there is no residue, no
   notification, and nothing to clean up. A fine-grained token without
   `pull_requests=write` still gets the 403, which is what makes this a probe
   rather than a write.

Neither probe is a read: a read passing is not evidence (see the table above).
That is the entire point — #5120's note is the same lesson one layer over, that
"a dispatch/404 probe cannot answer a permission question".

Why a no-op PATCH instead of a comment
--------------------------------------
The obvious probe is "create and delete a comment". This tool does not, because
a comment on a live PR notifies every watcher and the deletion is a *second*
write that can itself fail, leaving residue on someone else's PR. A no-op PATCH
has the same discriminating power with neither problem. `--comment-probe` is
kept for the case where a caller must prove the *comment* path specifically
(the artifact the rail reads); it creates the comment and deletes it, and says
so out loud.

Named refusals — one code per cause, because each has a different repair
-------------------------------------------------------------------------
`GH-WRITE-UNAVAILABLE(<reason>)` on stderr, exit 2:

    rate-limited                 not a permission problem: the shared account's
                                 bucket is spent. Checked FIRST, from the
                                 RESPONSE HEADERS (`x-ratelimit-remaining: 0`,
                                 `retry-after`, `x-ratelimit-reset`) and the
                                 documented bodies — never from the status code,
                                 because a 403 is either cause (#10882 measured
                                 the exhaustion one; `gh-shim`'s covered refusal
                                 was a body shape GitHub never sends).
    sso-unauthorized             `X-GitHub-SSO` present: org SAML authorisation
                                 missing on the token. Re-authorise, not re-mint.
    bad-credentials              401: expired or revoked. Re-mint.
    permission:<perm>            a 403 naming the permission the endpoint WANTED
                                 (`X-Accepted-Github-Permissions`) — the token
                                 is authenticated and reads fine, it is just not
                                 granted that resource kind.
    forbidden-unclassified       a 403 none of the above explains.
    repo-not-visible             404 on the repo read.
    pr-not-visible               404 on the PR probe: that number is not a PR this
                                 identity can see (often `--pr` given an issue
                                 number) — never "the repo is not visible" when the
                                 repo read already succeeded.
    resource-not-found           404 on a WRITE leg whose repo read succeeded, so the
                                 resource is missing, not the identity.
    unexpected:<status>

`GH-WRITE-UNVERIFIABLE(<what>)`, exit 3 — never 0: `gh` missing, the probe timed
out, no PR exists to probe, a transport failure. A preflight that cannot measure
must say so, not pass.

Order is load-bearing: **rate-limit before permission**, and `bad-credentials`
before either. Answering "your token lacks scope" when the bucket is spent sends
a lane to re-mint a token that was never the problem.

Usage
-----
    uv run python tools/gh_write_preflight.py [--repo owner/name] [--pr N] [--json]

A landing lane runs it FIRST and stops on a non-zero exit before doing any work:

    uv run python tools/gh_write_preflight.py --repo daniel-ospina/tortoise \\
      || { echo "refusing to start: the write path is unavailable"; exit 1; }

Exit: 0 writable · 2 unavailable (a named refusal) · 3 unverifiable.

What this is NOT
----------------
It is not the repair. A repaired token is one box; this is the instrument that
makes the next broken box say so in the first second instead of the last hour.
Nor is it a substitute for `gh-limit`: that layer serialises and backs off the
shared bucket and is why this tool sees a clean 403 rather than a secondary
limit; this tool asks a different question (may I write at all) and reads the
headers `gh-limit` already knows how to distinguish.

Never prints a token: the identity is reported as `login` plus a truncated
sha256 of the token, so a token *swap* is visible without the token being.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would fail first (the D9 shape, tools/longmem_eval/run.py).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/gh_write_preflight.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/gh_write_preflight.py`"
    )

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import subprocess
from dataclasses import dataclass, field

# ── exit codes: a caller branches on these, so they are a contract ────────────
EXIT_WRITABLE = 0
EXIT_UNAVAILABLE = 2
EXIT_UNVERIFIABLE = 3

REF_PREFIX = "gh-write-preflight"
COMMENT_MARKER = "gh-write-preflight"

# `Permission` names as the fleet's reason strings spell them; the value is the
# `x-accepted-github-permissions` token GitHub reports for the endpoint.
PERM_CONTENTS = "contents-write"
PERM_PULL_REQUESTS = "pull_requests-write"

# Documented rate-limit bodies. Ordered so the earliest, most specific wins; a
# body match is a LAST resort behind the header checks, because the headers are
# what GitHub actually contracts on.
_RATE_BODY = re.compile(
    r"rate limit|rate-limit|secondary rate|abuse detection|too many requests",
    re.IGNORECASE,
)
_PERMISSION_BODY = re.compile(
    r"Resource not accessible by (?:personal access token|integration|OAuth App)",
    re.IGNORECASE,
)

_HTTP_LINE = re.compile(r"^HTTP/\S+\s+(\d{3})")


@dataclass
class HttpResult:
    """One HTTP exchange, parsed from `gh api -i`'s merged output."""

    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")

    @property
    def accepted_permissions(self) -> list[str]:
        raw = self.header("x-accepted-github-permissions")
        return [p.strip() for p in raw.split(";") if p.strip()]


@dataclass
class Refusal:
    """A named refusal: the reason is grep-stable and the detail is for a human."""

    reason: str
    detail: str
    probe: str

    def line(self) -> str:
        return f"GH-WRITE-UNAVAILABLE({self.reason})"


def parse_http_output(raw: str) -> HttpResult:
    """Parse `gh api -i` output.

    `-i` prints the status line, the headers, a blank line and the body — and the
    harness `gh` shim may print a pacing banner around it, so the block taken is
    the LAST status line onward. A body that itself contains a blank line must
    survive intact, which is why the body is everything after the FIRST blank
    line following the chosen status line rather than a `.split()`.
    """
    lines = raw.replace("\r\n", "\n").split("\n")
    starts = [i for i, ln in enumerate(lines) if _HTTP_LINE.match(ln)]
    if not starts:
        return HttpResult(status=None, body=raw.strip())
    begin = starts[-1]
    status = int(_HTTP_LINE.match(lines[begin]).group(1))
    headers: dict[str, str] = {}
    i = begin + 1
    while i < len(lines) and lines[i].strip():
        name, _, value = lines[i].partition(":")
        if name.strip():
            headers[name.strip().lower()] = value.strip()
        i += 1
    body = "\n".join(lines[i + 1 :]).strip()
    return HttpResult(status=status, headers=headers, body=body)


def classify(
    res: HttpResult, perm: str, probe: str, *, repo_read_ok: bool = False
) -> Refusal | None:
    """Decide what a probe's response means. `None` means the write was accepted.

    ⛔ THE ORDER IS THE CORRECTNESS. `rate-limited` is a statement about the shared
    account and is repaired by waiting; `permission:<perm>` is a statement about
    the token and is repaired by re-minting. Reporting the second when the first
    is true is the defect this function exists to avoid, so the rate-limit arms
    are tested before the permission arms, and `bad-credentials` before both
    (a 401 cannot be a permission grant problem).
    """
    if res.status is None:
        return Refusal(
            "no-response",
            f"`gh api` produced no HTTP status line at all for the {probe} probe",
            probe,
        )
    if 200 <= res.status < 300:
        return None

    # ── 401: expired / revoked. Never a scope problem. ────────────────────────
    if res.status == 401:
        return Refusal("bad-credentials", _detail(res, "the token was rejected (401)"), probe)

    # ── rate limit: headers FIRST, body second ────────────────────────────────
    if res.status in (403, 429) and _looks_rate_limited(res):
        remaining = res.header("x-ratelimit-remaining") or "?"
        reset = res.header("x-ratelimit-reset") or "?"
        retry = res.header("retry-after") or "-"
        return Refusal(
            "rate-limited",
            f"the shared account's bucket is spent, NOT a permission problem "
            f"(x-ratelimit-remaining={remaining}, x-ratelimit-reset={reset}, "
            f"retry-after={retry}). Wait it out; do not re-mint. {_detail(res, '')}".strip(),
            probe,
        )

    # ── org SAML: the token is fine, its authorisation for the org is not ─────
    if res.header("x-github-sso"):
        return Refusal(
            "sso-unauthorized",
            f"org SAML authorisation is missing (X-GitHub-SSO: "
            f"{res.header('x-github-sso')}). Authorise the token for the org — "
            f"re-minting does not help. {_detail(res, '')}".strip(),
            probe,
        )

    # ── permission: the token is authentic and reads fine, it is not GRANTED ──
    if res.status == 403 and (_PERMISSION_BODY.search(res.body) or res.accepted_permissions):
        accepted = ", ".join(res.accepted_permissions) or "unreported"
        return Refusal(
            f"permission:{perm}",
            f"the token is NOT granted this write. The endpoint requires "
            f"[{accepted}] and the token's grant does not include it — reads and "
            f"other resource kinds still succeed, which is why only a write can "
            f"see this. {_detail(res, '')}".strip(),
            probe,
        )

    if res.status == 403:
        return Refusal(
            "forbidden-unclassified", _detail(res, "403 with no recognised cause"), probe
        )

    if res.status == 404:
        # ⛔ "THE REPO IS NOT VISIBLE" IS ONLY TRUE BEFORE THE REPO WAS READ.
        # The old arm keyed this on the probe NAME, so a 404 from the ref-create
        # (which runs AFTER the role read succeeded) and a 404 from the PR read
        # both told the operator to chase a wrong `--hostname` or account while
        # the repo was provably readable (#10945 review round 2, P2s 4+1). The
        # fact is carried in explicitly instead of inferred.
        if not repo_read_ok:
            return Refusal(
                "repo-not-visible",
                f"404 on the {probe} probe: the repo is not visible to this identity "
                f"(a wrong `--hostname`, a different account, or a private repo the "
                f"token cannot see). {_detail(res, '')}".strip(),
                probe,
            )
        if probe == "pull_requests":
            return Refusal(
                "pr-not-visible",
                f"404 on the {probe} probe: that number is not a pull request this "
                f"identity can see — most often `--pr` was given an ISSUE number, or "
                f"the PR is in another repo. The repository itself IS visible (the "
                f"role read succeeded). {_detail(res, '')}".strip(),
                probe,
            )
        return Refusal(
            "resource-not-found",
            f"404 on the {probe} probe, but the repo IS visible — so this is the "
            f"resource, not the identity (a sha that is not on the remote, or a "
            f"path this token may not address). {_detail(res, '')}".strip(),
            probe,
        )

    return Refusal(f"unexpected:{res.status}", _detail(res, f"HTTP {res.status}"), probe)


def _looks_rate_limited(res: HttpResult) -> bool:
    """Rate limit iff a HEADER says so, else iff the body uses GitHub's wording.

    The header test is the contract: `x-ratelimit-remaining: 0` is a spent primary
    bucket and `retry-after` is a secondary-limit instruction — neither is ever
    sent with a scope refusal. The body test is a fallback for the secondary shape
    that arrives without headers, and it is written narrowly: "API rate limit
    exceeded for user ID <id>" / "secondary rate limit" / "abuse detection", not
    the word "403".
    """
    if res.header("retry-after"):
        return True
    if res.header("x-ratelimit-remaining") == "0":
        return True
    if res.status == 429:
        return True
    return bool(_RATE_BODY.search(res.body))


def _detail(res: HttpResult, prefix: str) -> str:
    body = res.body.strip().replace("\n", " ")
    if len(body) > 300:
        body = body[:300] + "…"
    if prefix and body:
        return f"{prefix}: {body}"
    return prefix or body


# ── the gh layer ─────────────────────────────────────────────────────────────


def gh(args: list[str], timeout: float) -> tuple[int, str, str]:
    """Run `gh` and return (rc, stdout, stderr).

    ⛔ Invoked as `gh`, NOT by path: the harness `gh` in PATH is the shim that
    serialises the shared bucket and whose `auth` layer resolves the SAME identity
    a lane will use. Resolving gh by absolute path would probe an identity the
    lane does not have — and a probe of the wrong identity is worse than none,
    because it reports a pass.
    """
    env = dict(os.environ)
    env.setdefault("GH_PAGER", "cat")
    env.setdefault("NO_COLOR", "1")
    try:
        out = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        return 127, "", ""
    except subprocess.TimeoutExpired:
        return 124, "", ""
    # ⛔ THE STREAMS STAY SEPARATE (#10945 review P2). They were merged here, and
    # three parsers read "the last line" or located JSON in the merged text — so a
    # single stderr line (the harness shim's own pacing banner, or gh's
    # "warning: using token from GH_TOKEN") became the repo name, or `--jq .login`'s
    # value, or — worst — the bytes that get hashed into the identity digest, which
    # would make two DIFFERENT credentials hash identically and silently defeat the
    # token-swap property this tool reports on. Only `api()` sees both, and it
    # prefers stdout.
    return out.returncode, out.stdout or "", out.stderr or ""


def api(method: str, path: str, fields: dict[str, str] | None, timeout: float) -> HttpResult:
    args = ["api", "-i", "-X", method, path]
    for k, v in (fields or {}).items():
        args += ["-f", f"{k}={v}"]
    _, out, err = gh(args, timeout)
    res = parse_http_output(out)
    if res.status is None and err.strip():
        # A shim that merges the exchange onto stderr is still answerable; what is
        # NOT allowed is mixing stderr into a parse that already found a status.
        res = parse_http_output(err)
    return res


def token_identity(timeout: float) -> tuple[str, str]:
    """(login, `sha256:<12>` of the resolved token) — never the token itself.

    A truncated hash is what makes a token SWAP visible: two runs on the same box
    with different hashes used different credentials, which is the first thing to
    know when one of them passed. It is deliberately not reversible.
    """
    rc, out, _ = gh(["api", "user", "--jq", ".login"], timeout)
    login = out.strip() if rc == 0 else "unknown"
    rc_t, tok, _ = gh(["auth", "token"], timeout)
    login = re.sub(r"[^A-Za-z0-9_.-]", "", login.splitlines()[-1]) if login else "unknown"
    digest = "unavailable"
    if rc_t == 0 and tok.strip():
        digest = hashlib.sha256(tok.strip().splitlines()[-1].encode()).hexdigest()[:12]
    return login or "unknown", f"sha256:{digest}"


def credential_source(timeout: float) -> str:
    """Which credential `gh` **activated**: `GH_TOKEN`, `GITHUB_TOKEN`, or the store.

    Reported because it is the single most useful fact when a lane is refused: a
    box with a working OAuth token in `GH_TOKEN` and an under-scoped fine-grained
    PAT in `hosts.yml` flips between them purely on whether the variable survived
    into the lane's environment (gh's documented precedence is `GH_TOKEN` >
    `GITHUB_TOKEN` > stored credentials, and it activates the first it finds).

    ⛔ The ADDRESSED account, not merely the first one (#10945 review P2).
    `gh auth status` prints an account line and then its own
    `Active account: true|false`, so the source has to be carried forward from the
    last account line and only returned once the active marker is seen. Returning
    the first account line instead names a credential that is not in use — which
    sends the human to re-mint the wrong one, the exact misdiagnosis this tool
    exists to prevent. Unparseable is reported, never guessed.

    ⛔ AND THAT HOLDS ON THE FALLBACK TOO (#10945 review F2). The loop's `return`
    enforces "only once the active marker is seen", but the fall-through used to be
    `pending or "unreported"` — so account lines with NO active marker (an older
    `gh`, a localisation, one line this parse misses) returned the LAST account
    seen, i.e. the one field whose whole purpose is preventing a wrong-credential
    chase took the loosest path in the function. What was seen is named as CONTEXT
    after the verdict, in parentheses, so `source=` never reads as a credential
    name it cannot substantiate.
    """
    rc, out, err = gh(["auth", "status"], timeout)
    text = out or err
    if rc != 0 and not text.strip():
        return "unknown"
    pending: str | None = None
    for line in text.splitlines():
        m = re.search(r"account\s+(\S+)\s+\(([^)]*)\)", line)
        if m:
            pending = m.group(2)
            continue
        if "Active account:" in line and "true" in line.lower() and pending is not None:
            return pending
    if pending is None:
        return "unreported"
    return f"unreported (saw {pending})"


def detect_repo(timeout: float) -> str | None:
    """`owner/name` from the checkout's origin, else gh's own answer."""
    rc, out, _ = gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], timeout)
    if rc == 0 and "/" in out:
        return out.strip().splitlines()[-1]
    return None


def default_branch_head(repo: str, timeout: float) -> tuple[str | None, HttpResult]:
    res = api("GET", f"repos/{repo}", None, timeout)
    branch = "main"
    if res.status and 200 <= res.status < 300:
        try:
            branch = json.loads(res.body).get("default_branch") or "main"
        except (ValueError, AttributeError):
            pass
        else:
            ref = api("GET", f"repos/{repo}/git/ref/heads/{branch}", None, timeout)
            if ref.status and 200 <= ref.status < 300:
                try:
                    return json.loads(ref.body)["object"]["sha"], res
                except (ValueError, KeyError, TypeError):
                    pass
            return None, res
    return None, res


def pick_pr(repo: str, explicit: int | None, timeout: float) -> int | None:
    """The PR to probe `pull_requests=write` against.

    Default is the most recently MERGED PR: it is the least disruptive target
    (nobody is waiting on it) and it exercises the same authorization path as a
    live one. `--pr` pins it when a caller must prove a specific PR.
    """
    if explicit:
        return explicit
    # ⛔ ASK `gh` FOR THE NUMBER, do not locate JSON in its output (#10945 review
    # P2). `out.index("[")` on the merged stream matched the harness shim's own
    # pacing banner (`[gh-limit] pacing 1.2s …`) before the real array, so both
    # queries failed, `pick_pr` returned None, and a box whose writes were
    # perfectly fine got `no-pr-to-probe` -> exit 3 on EVERY landing — a total
    # false block of exactly the lanes this tool exists to unblock. `--jq` leaves
    # nothing to locate.
    for state in ("merged", "all"):
        rc, out, _ = gh(
            [
                "pr",
                "list",
                "--repo",
                repo,
                "--state",
                state,
                "--limit",
                "1",
                "--json",
                "number",
                "--jq",
                ".[0].number",
            ],
            timeout,
        )
        if rc == 0 and out.strip().isdigit():
            return int(out.strip())
    return None


def probe_contents(repo: str, timeout: float, repo_read_ok: bool) -> tuple[Refusal | None, str]:
    """Create and delete a throwaway ref: the `contents=write` probe."""
    sha, repo_res = default_branch_head(repo, timeout)
    if repo_res.status is not None and not (200 <= (repo_res.status or 0) < 300):
        # The repo read itself failed; the write probe cannot be attempted, but
        # the read's refusal is classified for the caller (a 401/403/404 is a
        # real answer about this identity).
        return classify(repo_res, PERM_CONTENTS, "contents"), "repo read refused"
    if not sha:
        return (
            Refusal(
                "no-default-branch-sha",
                f"could not read the head of {repo}'s default branch, so there is "
                f"no sha to hang the probe ref on",
                "contents",
            ),
            "no sha",
        )
    ref = f"refs/heads/{REF_PREFIX}-{secrets.token_hex(6)}"
    made = api("POST", f"repos/{repo}/git/refs", {"ref": ref, "sha": sha}, timeout)
    verdict = classify(made, PERM_CONTENTS, "contents", repo_read_ok=repo_read_ok)
    if verdict is not None:
        # ⛔ A POST THAT PRODUCED NO EXCHANGE MAY STILL HAVE CREATED THE REF. A
        # timeout is `no-response` (a transport failure), and the write may have
        # been applied before the connection died — so the tidiness debt is named
        # here rather than left for someone to discover as a stray branch.
        if verdict.reason == "no-response":
            return verdict, (
                f"create {ref} produced no response — it MAY have been created; "
                f"check for {ref} and delete it"
            )
        return verdict, f"create {ref} refused"
    # Cleanup is best effort and NEVER changes the verdict: the write already
    # succeeded, so the capability is proven; a failed delete is a tidiness
    # problem and is reported as one rather than converted into a refusal.
    deleted = api("DELETE", f"repos/{repo}/git/refs/{ref.removeprefix('refs/')}", None, timeout)
    residue = ""
    if not (deleted.status and 200 <= deleted.status < 300):
        residue = f"probe ref {ref} could not be deleted (HTTP {deleted.status}); remove it by hand"
    return None, residue or f"created and deleted {ref}"


def probe_pull_requests(
    repo: str, pr: int | None, timeout: float, comment_probe: bool, repo_read_ok: bool
) -> tuple[Refusal | None, str]:
    """The `pull_requests=write` probe.

    Default: `PATCH` the PR with its own unchanged title — a no-op that a
    fine-grained token without `pull_requests=write` still refuses (measured
    2026-11-04: 200 for `repo`-scoped OAuth, 403 +
    `X-Accepted-Github-Permissions: pull_requests=write` for the PAT, with the
    PR's `title` and `updated_at` byte-identical afterwards).

    `--comment-probe` creates and deletes a real comment instead, for the caller
    that must prove the *comment* path — the artifact the merge rail reads.
    """
    if pr is None:
        return (
            Refusal(
                "no-pr-to-probe",
                f"no pull request could be found in {repo} to probe "
                f"`pull_requests=write` against; pass `--pr N`. A read passing is "
                f"not evidence, so this is UNVERIFIED rather than a pass",
                "pull_requests",
            ),
            "no target PR",
        )
    if comment_probe:
        # ⛔ THE COMMENT ROUTE IS THE ONE THAT NEEDS THE TARGET CHECK MOST (#10945
        # review round 3, P2). `POST /issues/{n}/comments` authorizes against the
        # TARGET RESOURCE, not the route — so on an ISSUE number it needs only
        # `issues=write` and returns 201, and this branch would then report
        # "writable" having never exercised `pull_requests=write`, which is
        # precisely the leg whose 403 the rail's `--evidence` comment hits. The
        # default PATCH branch is safe because it reads `GET /pulls/{n}` first;
        # this one must do the same, and for the same reason.
        target = api("GET", f"repos/{repo}/pulls/{pr}", None, timeout)
        if target.status is None or not (200 <= target.status < 300):
            return (
                classify(target, PERM_PULL_REQUESTS, "pull_requests", repo_read_ok=repo_read_ok),
                f"PR #{pr} is not a pull request this identity can read",
            )
        made = api(
            "POST",
            f"repos/{repo}/issues/{pr}/comments",
            {"body": f"{COMMENT_MARKER}: automated token preflight, deleted immediately"},
            timeout,
        )
        verdict = classify(made, PERM_PULL_REQUESTS, "pull_requests", repo_read_ok=repo_read_ok)
        if verdict is not None:
            return verdict, f"comment on PR #{pr} refused"
        cid = None
        with contextlib.suppress(ValueError, AttributeError):
            cid = json.loads(made.body).get("id")
        if cid is None:
            return None, f"comment on PR #{pr} created but its id was unreadable; delete it by hand"
        deleted = api("DELETE", f"repos/{repo}/issues/comments/{cid}", None, timeout)
        if not (deleted.status and 200 <= deleted.status < 300):
            return None, f"probe comment {cid} on PR #{pr} could not be deleted; remove it by hand"
        return None, f"created and deleted a comment on PR #{pr}"

    current = api("GET", f"repos/{repo}/pulls/{pr}", None, timeout)
    # ⛔ CLASSIFY THE READ (#10945 review round 2, P1). Every non-2xx used to
    # collapse into `no-pr-title`, which is an UNVERIFIABLE reason — so a 404 on
    # the PR read (a `--pr` given an issue number) and a 403 (this is the
    # `X-Accepted-Github-Permissions` header GitHub would have sent) were both
    # reported as "no title, cannot measure" with exit 3, and the PATCH was never
    # attempted. That made a PROVABLE `permission:pull_requests-write` refusal —
    # the exact #10930 shape — invisible on the one path that produces it.
    if current.status is None or not (200 <= current.status < 300):
        return (
            classify(current, PERM_PULL_REQUESTS, "pull_requests", repo_read_ok=repo_read_ok),
            f"read of PR #{pr} refused",
        )
    title = None
    pre_updated = None
    with contextlib.suppress(ValueError, AttributeError):
        before = json.loads(current.body)
        title = before.get("title")
        # ⛔ THE PRE-WRITE STAMP, taken from the SAME read as the title (#10945
        # review F3). `updated_at` is the only token available here — GitHub
        # documents conditional requests as a GET-caching mechanism
        # (`If-None-Match`/304) and documents no `If-Match` on
        # `PATCH /repos/{owner}/{repo}/pulls/{n}`, so there is no compare-and-swap
        # to use instead. It works because a no-op PATCH leaves it byte-identical:
        # measured on PR #10902 (200 after re-sending the same title, `title` and
        # `updated_at` unchanged), which is why an unchanged stamp is evidence and
        # not a coincidence.
        pre_updated = before.get("updated_at")
    if not title:
        return (
            Refusal(
                "no-pr-title",
                f"PR #{pr} was readable but carried no title, and the no-op PATCH "
                f"probe needs it (writing anything else would change the PR)",
                "pull_requests",
            ),
            "no title",
        )
    res = api("PATCH", f"repos/{repo}/pulls/{pr}", {"title": title}, timeout)
    verdict = classify(res, PERM_PULL_REQUESTS, "pull_requests", repo_read_ok=repo_read_ok)
    if verdict is not None:
        return verdict, f"no-op PATCH on PR #{pr} refused"
    # ⛔ VERIFY THE NO-OP CLAIM INSTEAD OF ASSERTING IT (#10945 review P2s, rounds
    # 1 and 2). "No residue" is this probe's whole licence to run before every
    # landing and nothing else checks it. Three outcomes, and they must not be
    # conflated: what OUR write stored (read off the PATCH's own response, which is
    # race-free), what the PR says afterwards, and failing to read either.
    stored = None
    post_updated = None
    with contextlib.suppress(ValueError, AttributeError):
        patched = json.loads(res.body)
        stored = patched.get("title")
        post_updated = patched.get("updated_at")
    if stored is not None and stored != title:
        return None, (
            f"no-op PATCH on PR #{pr} accepted, BUT our own write stored {stored!r} "
            f"instead of {title!r} — residue: restore the title"
        )
    # ⛔ THE MIRROR CASE OF THE ONE BELOW (#10945 review F3). The re-read catches a
    # change AFTER our write; nothing caught a change BEFORE it. If a human renamed
    # the PR between our read and our PATCH, then the PATCH RESTORED THE TITLE WE
    # READ — discarding a real edit — and the re-read then compares against the
    # value we just wrote, so it takes the clean branch and the tool certifies
    # "title unchanged on re-read" over an edit it destroyed. A moved `updated_at`
    # is the one signal that survives, because it moves for the other actor's write
    # and not for ours. No test can drive this through the interleaving itself; it
    # is driven here as the stamp disagreement the interleaving produces.
    if pre_updated and post_updated and pre_updated != post_updated:
        return None, (
            f"no-op PATCH on PR #{pr} accepted, BUT the PR moved between our read "
            f"({pre_updated}) and our write ({post_updated}) — another actor changed "
            f"it, so this probe may have reverted that edit. Check PR #{pr}'s title by "
            f"hand (ours was {title!r}); this probe does NOT claim a no-op here."
        )
    after = api("GET", f"repos/{repo}/pulls/{pr}", None, timeout)
    got = None
    if after.status is not None and 200 <= after.status < 300:
        with contextlib.suppress(ValueError, AttributeError):
            got = json.loads(after.body).get("title")
    if got is None:
        # NOT the clean wording: the re-read failed, so the claim was never
        # measured (it used to be printed for a 403, a 404 and garbage alike).
        return None, (
            f"no-op PATCH on PR #{pr} accepted, BUT the re-read could not confirm "
            f"(HTTP {after.status}) — check that PR's title by hand: it should "
            f"still be {title!r}"
        )
    if got != title:
        return None, (
            f"no-op PATCH on PR #{pr} accepted; the title was {title!r} when written "
            f"and is {got!r} now — changed AFTER the probe by another actor, which "
            f"is not this probe's residue. Do not restore it on our say-so."
        )
    return None, f"no-op PATCH on PR #{pr} accepted (title unchanged on re-read)"


# ── CLI ──────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gh_write_preflight",
        description=(
            "Prove this lane's gh identity can WRITE to its repo, before the lane "
            "does any work. Reads pass in every failure mode, so this writes."
        ),
    )
    ap.add_argument("--repo", default=None, help="owner/name (default: derived from the origin)")
    ap.add_argument("--pr", type=int, default=None, help="the PR to probe (default: newest merged)")
    ap.add_argument(
        "--timeout",
        type=float,
        default=45.0,
        help="per-`gh`-call bound in seconds (default 45)",
    )
    ap.add_argument(
        "--comment-probe",
        action="store_true",
        help="prove the COMMENT path (creates and deletes a comment) instead of a no-op PATCH",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable result")
    args = ap.parse_args(argv)

    repo = args.repo or detect_repo(args.timeout)
    if not repo:
        print(
            "GH-WRITE-UNVERIFIABLE(no-repo) — no `--repo` and the checkout has no "
            "resolvable origin",
            file=sys.stderr,
        )
        return EXIT_UNVERIFIABLE

    login, ident = token_identity(args.timeout)
    source = credential_source(args.timeout)

    report: dict[str, object] = {
        "repo": repo,
        "identity": {"login": login, "token": ident, "credential_source": source},
        "probes": {},
        "writable": False,
    }
    refusals: list[Refusal] = []

    # The user's ROLE, not the token's grant. Printed as context and never used as
    # a verdict — `permissions.push: true` is true for a token that can write
    # nothing, which is the trap #10930 fell into.
    repo_res = api("GET", f"repos/{repo}", None, args.timeout)
    repo_read_ok = bool(repo_res.status and 200 <= repo_res.status < 300)
    role: object = None
    if repo_read_ok:
        try:
            role = json.loads(repo_res.body).get("permissions")
        except (ValueError, AttributeError):
            role = None
    report["user_role_permissions"] = role
    report["note_user_role_permissions"] = (
        "reports the USER's role, NOT the token's grant — never a verdict"
    )

    pr = pick_pr(repo, args.pr, args.timeout)
    report["probe_pr"] = pr

    for name, fn in (
        ("contents", lambda: probe_contents(repo, args.timeout, repo_read_ok)),
        (
            "pull_requests",
            lambda: probe_pull_requests(repo, pr, args.timeout, args.comment_probe, repo_read_ok),
        ),
    ):
        verdict, detail = fn()
        report["probes"][name] = {
            "ok": verdict is None,
            "detail": detail,
            "reason": verdict.reason if verdict else None,
        }
        if verdict is not None:
            refusals.append(verdict)

    if refusals:
        # A probe we could not RUN is `unverifiable`, not `unavailable`: the
        # difference decides whether a human sees a repair or a bug report.
        unrun = [r for r in refusals if r.reason in UNVERIFIABLE_REASONS]
        definite = [r for r in refusals if r.reason not in UNVERIFIABLE_REASONS]
        report["refusals"] = [
            {"reason": r.reason, "probe": r.probe, "detail": r.detail} for r in refusals
        ]
        if args.json:
            print(json.dumps(report, indent=2))
        # ⛔ A MEASURED REFUSAL OUTRANKS AN UNMEASURED ONE (#10945 review P2).
        # `unrun` used to be tested first, so `contents=permission:contents-write`
        # alongside `pull_requests=no-pr-to-probe` printed ONLY
        # `GH-WRITE-UNVERIFIABLE` and exited 3 — the caller's documented
        # `|| exit` recipe then reports "cannot measure, file a bug" while a real,
        # measured `contents=write` defect sits unrepaired and is named nowhere on
        # stderr. Exit 3 is reserved for "nothing definite was measured".
        if definite:
            for r in definite:
                print(
                    f"{r.line()} probe={r.probe} identity={login} token={ident} source={source}",
                    file=sys.stderr,
                )
                print(f"  {r.detail}", file=sys.stderr)
            for r in unrun:
                print(
                    f"GH-WRITE-UNVERIFIABLE({r.reason}) probe={r.probe} — {r.detail}",
                    file=sys.stderr,
                )
            reasons = ",".join(r.reason for r in refusals)
            if not args.json:
                # `--json` keeps stdout parseable: the machine form is the whole
                # body there, and a trailing summary line would make it invalid
                # JSON (which is how a caller's `json.loads` becomes an
                # unexplained crash).
                print(
                    f"GH-WRITE-PREFLIGHT REFUSED repo={repo} reasons={reasons} "
                    f"identity={login} token={ident} source={source}"
                )
            return EXIT_UNAVAILABLE
        print(f"GH-WRITE-UNVERIFIABLE({unrun[0].reason}) — {unrun[0].detail}", file=sys.stderr)
        return EXIT_UNVERIFIABLE

    report["writable"] = True
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"GH-WRITE-PREFLIGHT OK repo={repo} identity={login} token={ident} "
            f"source={source} probes=contents,pull_requests pr={pr}"
        )
        for name, probe in report["probes"].items():  # type: ignore[union-attr]
            print(f"  {name}: {probe['detail']}")
    return EXIT_WRITABLE


# Reasons that mean "the probe could not run", which is never a refusal of the
# token and must never be reported as one (a caller reads exit 2 as "repair the
# credential").
UNVERIFIABLE_REASONS = frozenset(
    {"no-response", "no-pr-to-probe", "no-pr-title", "no-default-branch-sha"}
)


if __name__ == "__main__":
    sys.exit(main())
