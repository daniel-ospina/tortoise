#!/usr/bin/env python3
"""gh_write_preflight — prove THIS lane's credential can WRITE, before it does any work.

WHY THIS EXISTS (measured 2026-11-04, issue #10930)
───────────────────────────────────────────────────
A landing lane on a cell reported: *"the lane's token cannot write to PRs (403 …)"*. It could read,
verify trees, and decide — but **could not write**, and every closure it owed died there. The
verdict comment it could not post is the `--evidence` artifact the merge rail reads, so the failure
does not look like an error: a landing that merges and then cannot close its constituents is
**indistinguishable from "landed and closed nothing"** — the exact failure mode the fleet spent the
day fighting. A lane discovers this at the END, after the merge, when the closures are owed.

⛔ THE TRAP THIS TOOL EXISTS TO AVOID — AND THAT ITS FIRST VERSION FELL INTO
────────────────────────────────────────────────────────────────────────────
**`Issues: write` and `Pull requests: write` are DIFFERENT permissions, and a PR comment uses the
PR one.** On a fine-grained PAT they are granted separately. The verbatim evidence from #10930:

    POST /repos/daniel-ospina/tortoise/issues/10902/comments  → 403 "Resource not accessible by personal access token"   (#10902 IS A PR)
    POST /repos/daniel-ospina/tortoise/issues/10774/comments  → 201                                                       (#10774 is an ISSUE)
    GET  /repos/daniel-ospina/tortoise/pulls/10902            → 200
    GET  /repos/daniel-ospina/tortoise/issues/10902/comments   → 200

Note those two comment calls are the **same endpoint** — `/issues/{n}/comments` — and differ only in
whether `{n}` names a PR or an issue. A token with `Issues: write` and no `Pull requests: write`
therefore comments on issues and is refused on PRs.

⇒ **A probe that only touches an issue proves the wrong permission.** The first version of this tool
deleted a repo label (an *issues* resource); it passes exactly the token this bug is about. This
version probes **both surfaces independently** and refuses unless **both** are writable, naming
which one is missing.

WHAT IT PROBES, AND WHY EACH CHOICE IS THE ONE IT IS
───────────────────────────────────────────────────
1. **READ** — `GET /repos/{owner}/{repo}`. The *disambiguator*: without it a `404` from a write probe
   could mean "no permission" or "you cannot see this repo", and those need different fixes. 200
   here also yields `default_branch` for the pull probe.

2. **ISSUES WRITE** — `POST /repos/{o}/{r}/issues` with an **empty body**.
   MEASURED 2026-11-04: `422 Invalid request` when authorised, `403` when not. ⛔ **Zero side
   effect by construction**: the payload fails validation, so no issue is created. No issue number
   is needed, so it runs before the lane has chosen a target.

3. **PULL REQUESTS WRITE** — `POST /repos/{o}/{r}/pulls` with a **deliberately non-existent head
   ref** (`refs/heads/gh-write-preflight-<16 random hex>`) and the repo's default branch as base.
   MEASURED: `422 Validation Failed` (`field: head, code: invalid`) when authorised, `403` when not.
   ⛔ Zero side effect: the head ref cannot exist, so no PR can be created. (The random name is what
   makes that a property rather than a hope.)

4. **CONTENTS WRITE** — `POST /repos/{o}/{r}/git/refs` with a random branch name and an
   **unresolvable sha** (`000…0`).
   MEASURED 2026-11-04 ON A LIVE BOX: `422 "Object does not exist"` when authorised, `403` when not,
   and `GET /git/matching-refs/heads/gh-write-preflight` returns **`[]`** after both — the ref is
   never created, in either outcome.
   ⛔ **THIS PROBE IS NOT BELT-AND-BRACES — WITHOUT IT THE TOOL FALSE-PASSES.** Merging a PR and
   creating a ref are charged to `contents: write`, NOT `pull_requests: write`. MEASURED, headers
   included, on `PUT /pulls/{n}/merge` and on `POST /git/refs`, both answering
   `x-accepted-github-permissions: contents=write` (the merge attempt returned `403` with
   `x-ratelimit-remaining: 3313` — a permission, not a limit). So a fine-grained PAT minted with
   `Issues: write` + `Pull requests: write` and `Contents: read` — an ordinary grant, and one #10930
   records in the wild as `X-Accepted-Github-Permissions: contents=read` — passes the two probes
   above and is then refused by `git push` and by the merge. A landing lane CANNOT PUBLISH ITS
   BRANCH at all under that token, so the two-surface version of this tool reported `GH-WRITE-OK`
   over a credential that can do none of the landing work. #10930 says it directly: *"a preflight
   that proves only `pull_requests: write` is not sufficient — the instrument must probe
   `contents: write` too. That choice is load-bearing, not belt-and-braces."*
   Rejected alternative: create-then-delete a real throwaway ref. It is side-effecting *by
   construction* and it leaves debris when the delete fails, which is exactly the class of failure
   a preflight must not introduce. The unresolvable sha proves the permission gate and creates
   nothing.

   Alternatives rejected, with the measurement behind each:
     * `DELETE .../labels/<name>` — this is what the FIRST version used. It is an **issues**
       resource, so it passes a token with `Issues: write` and no `Pull requests: write` — i.e. it
       cannot see this bug. Kept out on purpose.
     * `POST .../issues/{n}/labels` with a real label — mutates, and notifies watchers.
     * `POST .../issues/{n}/comments` — creates a notification even when deleted immediately, and
       needs a target number.
     * `GET /repos/{o}/{r}` → `.permissions` — **DOES NOT WORK, and this is the second trap.**
       MEASURED: this same read-only-for-PRs token reports `{"admin":true,"maintain":true,
       "push":true,"pull":true,"triage":true}`, because `permissions` describes the USER's access to
       the repo, not the granular permissions the TOKEN carries. #10930's own body records the same
       finding. Any preflight built on it passes the broken token.
     * `X-Oauth-Scopes` — a real signal, but **only for classic/OAuth tokens**. A fine-grained PAT
       (`github_pat_…`) sends **no scope header at all**, so absence of `repo` is not evidence.
       The broken token in #10930 IS a fine-grained PAT (the box's `gh auth status` says so).

5. **RATE LIMIT** — checked from the READ response *before* either write probe, and again from each
   probe's own response. A `403` is either a permission refusal or a rate limit, and confusing them
   wastes an hour the wrong way: re-minting a token does nothing for a rate limit, waiting does
   nothing for a missing permission. The discriminator is the body plus the headers
   (`x-ratelimit-remaining: 0`, `retry-after`), never the bare status code.

6. **REDIRECTS ARE NEVER FOLLOWED.** Only `422` counts as "authorised"; a 2xx on a must-be-invalid
   payload is evidence the response did not come from GitHub's validation path. See `_WRITE_OK`.

REASONS (`GH-WRITE-UNAVAILABLE(<reason>)`), each with its own fix
─────────────────────────────────────────────────────────────────
  no-credential                no GH_TOKEN, no GITHUB_TOKEN, no github.com entry in hosts.yml
  bad-credentials              401 — expired, revoked, or mistyped
  repo-not-visible             404 on the READ probe, or the READ itself refused — an access
                               question, never a missing write permission
  wrong-host                   GH_HOST names a host other than github.com
  rate-limited                 403/429 with rate-limit wording / remaining==0 / retry-after
                               NOT a permission problem — do not re-mint the token for it
  server-error                 5xx from GitHub — a service fault, not a verdict about the token
  token-read-only              403 "Resource not accessible by …" on ALL THREE surfaces
  contents-write-missing       contents refused  ← CANNOT PUSH A BRANCH OR MERGE
                               fix: re-mint with `Contents: write`
  pull-requests-write-missing  PRs refused  ← THE #10930 BUG (verdict comment, closes)
                               fix: re-mint with `Pull requests: write`
  issues-write-missing         issues refused (a landing also closes issues)

  When more than one surface is refused (but not all three) the reason names the FIRST in landing
  order — `contents` → `pull_requests` → `issues` — because that is the one that blocks the lane
  soonest; `detail` and the `surfaces` map name every refused surface, so nothing is hidden. Every
  one of these is fixed by the same action: re-mint the token carrying ALL THREE write permissions.
  sso-unauthorized             403 naming SAML/SSO — authorise the token for the org
  forbidden                    4xx that is neither a permission refusal nor a known class (e.g. 410
                               Issues-disabled, 400 from a proxy, 451) — NOT charged to the token
  transport                    could not reach the API — NOT a verdict about the token
  usage                        bad arguments — including an `--api-base` / `$GH_WRITE_PREFLIGHT_API`
                               that is neither DEFAULT_API nor a loopback stub, or one carrying a query,
                               a fragment, or `user:pass@` credentials. Nothing is contacted first.

⛔ **THE ENDPOINT IS PART OF THE VERDICT, AND IT IS VALIDATED BEFORE THE TOKEN MOVES.** `api_base` is
in the result (`--json` → `"api_base"`, with any `user:pass@` userinfo REDACTED — see `_printable_base`)
and in the `GH-WRITE-OK` line, because a pass measured against
something that is not GitHub is not a pass: point the base at any server that answers `422` and the
tool would otherwise print `GH-WRITE-OK(issues+pull-requests+contents writable)` for a token that
cannot write anything. So a non-default base is accepted ONLY when it is loopback (`127.0.0.0/8`,
`::1`, `localhost`) — the hermetic test seam — and any base carrying `user:pass@` is refused, because
the token is sent as an `Authorization` header and never in the URL.

USAGE
─────
  uv run python tools/gh_write_preflight.py --repo daniel-ospina/tortoise
  uv run python tools/gh_write_preflight.py --repo "$OR" --json
  GH_WRITE_PREFLIGHT_API=http://127.0.0.1:PORT uv run python tools/gh_write_preflight.py …   # test seam
                                                                       (loopback only; anything else is refused as `usage`)

  exit 0 = GH-WRITE-OK(issues+pull-requests+contents writable @ <api_base>)
  exit 1 = GH-WRITE-UNAVAILABLE(<reason>)

The credential is NEVER printed, logged, or included in any output — not even truncated.
"""

from __future__ import annotations

import sys

# ⛔ THE INTERPRETER GUARD IS THE FIRST STATEMENT AFTER THE DOCSTRING AND `__future__`, BEFORE EVERY
# OTHER IMPORT (#5128). That position is load-bearing: a 3.12-only import or stdlib name raises an
# UNATTRIBUTED error on an older interpreter, which reads as "this tool is broken" rather than
# "your interpreter is too old". Pinned by `tests/test_entry_point_python_guard.py`.
# `UP036` fires because `pyproject` already pins >=3.12, but that is exactly the point: the pin governs
# the INSTALLED environment, not the AMBIENT `python3` a human types, and the ambient one is what this
# guard exists for (#5128). It is a deliberate runtime guard, not an outdated version block.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard, see above
    raise SystemExit(
        f"tools/gh_write_preflight.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/gh_write_preflight.py`"
    )

import argparse
import contextlib
import http.client
import ipaddress
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_API = "https://api.github.com"
TIMEOUT_S = 25

# ⛔ THE ONE INPUT THAT CAN MAKE EVERY OTHER FAIL-CLOSED CHECK MOOT (review round 7). Every guard in
# this module assumes the answer came from GitHub: redirects are not followed, a 2xx on a must-be-
# invalid payload fails closed, and an unknown 4xx may not name a permission. `api_base` defeats all
# three at once — MEASURED on the pre-fix head: `GH_WRITE_PREFLIGHT_API=http://127.0.0.1:40933` (the
# suite's own stub) with `token=ghp_NOT_A_REAL_CREDENTIAL` printed
# `GH-WRITE-OK(issues+pull-requests+contents writable)` and exited 0, and NOTHING in stdout, stderr or
# the `--json` artifact named the endpoint. So the base is now (a) reported in the verdict and the OK
# line, and (b) accepted only when it IS GitHub, or when it is a loopback stub — the only seam the
# hermetic suite needs. `tools/activation_cohort.py` validates its base for the same reason.
_LOOPBACK_NAMES = frozenset({"localhost"})

# Probe outcomes that prove "the authorisation gate was PASSED and the request was merely invalid".
# ⛔ 422 IS THE ONLY SUCH ANSWER, and 2xx must NOT be read as success.
# MEASURED (review round 3): urlopen's default opener FOLLOWS redirects, and stdlib
# `HTTPRedirectHandler.redirect_request` DOWNGRADES POST to GET on 301/302/303. A stub that 301s the
# write probes and 200s the resulting collection GETs made this tool print
# `GH-WRITE-OK(issues+pull-requests writable)` and exit 0 while the server log showed NO write was
# ever answered — a FALSE PASS of the very gate the tool exists to be. 200 is the one status that
# cannot distinguish "authorised" from "GitHub was never asked": a proxy, WAF, captive portal, or a
# renamed-repo redirect all answer 200. So a 2xx on a must-be-invalid payload is itself evidence the
# response did not come from GitHub's validation path, and it fails closed below.
_WRITE_OK = (422,)
_SURPRISING_SUCCESS = (200, 201, 204)


def _is_loopback(host: str) -> bool:
    """True for `localhost`, any `127.0.0.0/8` address, and `::1` — and for nothing else.

    `ipaddress` rather than a string compare, so `127.0.0.2` (a real loopback the suite could bind)
    is not silently refused while `127.0.0.1.evil.example` is not silently allowed.
    """
    if host.strip().lower() in _LOOPBACK_NAMES:
        return True
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(host).is_loopback
    return False


def _validate_api_base(api_base: str) -> str:
    """Return '' when this base is safe to send the credential to, else a NAMED refusal reason.

    Refusals are reported as `usage` (the documented bad-arguments verdict) and happen BEFORE any
    request, so a rejected base never receives so much as the READ probe.
    """
    if not api_base:
        return "--api-base is empty — pass " + DEFAULT_API + " or a loopback stub"
    # ⛔ EVERY MESSAGE BELOW ECHOES `_printable_base(api_base)`, NEVER THE RAW VALUE. This function's
    # refusals ARE a printing path: the string becomes `result["detail"]`, which reaches stdout on the
    # `GH-WRITE-UNAVAILABLE(...)` line AND inside the `--json` artifact a lane pastes into a PR. Round 8
    # redacted only the `api_base` KEY, so the two guards below — which run BEFORE the userinfo check —
    # printed `user:pass@` verbatim. MEASURED at head `76a21dfd8`:
    #     --api-base 'http://user:hunter2@127.0.0.1:9?x=1'
    #       → ... must not carry a query or fragment (got 'http://user:hunter2@127.0.0.1:9?x=1')
    # Hoisting the userinfo check does NOT close the class (see `_printable_base`), and redacting here
    # keeps the invariant true regardless of branch order, so a future check added above cannot reopen it.
    # A bare `?`/`#` parses to an EMPTY query/fragment, so `parts.query` alone cannot see it — but a
    # base carrying one still gets mangled when the probe path is joined onto it.
    # ⛔ A NEWLINE IN THE BASE FORGES A VERDICT LINE (review cycle 3). MEASURED: an `api_base` carrying
    # `\nGH-WRITE-OK(issues+pull-requests+contents writable @ https://api.github.com)` printed a stdout
    # line BEGINNING with the documented pass string while exiting 1 — and AGENTS.md's HARD RULE tells a
    # lane to read exactly that line, so the tool could satisfy its own gate without running a probe.
    # Non-ASCII is the same class one character over: `http.client` raises `UnicodeEncodeError` on the
    # request path, which is a `ValueError` and therefore NOT caught by `_request`. Both are refused as
    # `usage` BEFORE any request, and the message is `!r`-escaped so it cannot carry the newline either.
    if not api_base.isascii() or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in api_base):
        return (f"--api-base carries a control character or a non-ASCII byte (got "
                f"{_printable_base(api_base)!r}) — it would forge a verdict line or crash the request")
    if "?" in api_base or "#" in api_base:
        return f"--api-base must not carry a query or fragment (got {_printable_base(api_base)!r})"
    parts = None
    try:
        parts = urllib.parse.urlsplit(api_base)
    except ValueError:
        # ⛔ `urlsplit` RAISES on a malformed IPv6 host — `http://[::1` → `ValueError: Invalid IPv6 URL` —
        # and that escaped `main` as a TRACEBACK with EMPTY stdout, breaking the
        # `GH-WRITE-UNAVAILABLE(<reason>)` / `--json` contract on a plausible typo of the documented
        # loopback seam (`::1`). `_printable_base` one function down already guards the same call.
        return f"--api-base is not a parseable URL (got {_printable_base(api_base)!r})"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"--api-base must be an absolute http(s) URL with a host (got {_printable_base(api_base)!r})"
    # ⛔ NEVER ECHO THESE CREDENTIALS BACK. The refused base's userinfo is NOT repeated in this message
    # on purpose: `user:pass@host` would otherwise be printed to stdout, which is the leak this branch
    # exists to stop, arriving in the error message about stopping it.
    if parts.username is not None or parts.password is not None:
        return (f"--api-base host {parts.hostname!r} carries credentials ('user:pass@' in the URL) — the "
                f"token is sent as an Authorization header, never in the URL")
    try:
        _ = parts.port
    except ValueError:
        return f"--api-base has an invalid port (got {_printable_base(api_base)!r})"
    if api_base.rstrip("/") == DEFAULT_API:
        return ""
    if not _is_loopback(parts.hostname):
        return (f"--api-base {_printable_base(api_base)!r} is neither {DEFAULT_API} nor a loopback stub — a verdict from an "
                f"unrecognised endpoint is not a verdict about the credential (only 127.0.0.0/8, ::1 and "
                f"localhost are accepted as the hermetic test seam)")
    return ""


def _printable_base(api_base: str) -> str:
    """`api_base` as it may be PUT IN A VERDICT: scheme + host[:port] ONLY.

    ⛔ THIS FUNCTION MUST NOT BE ABLE TO RETURN CALLER-SUPPLIED TEXT (review cycles 17-19). Every earlier
    version returned *some* part of the base — the whole string on the no-path branch, the path, the
    `@`-tail — and each one leaked a credential when nothing resolved to scrub it by value (`--api-base`
    is a caller input, and `GH_WRITE_PREFLIGHT_API` is the documented seam). A verdict needs the
    ENDPOINT'S IDENTITY ("was this measured against GitHub?"), which is scheme + netloc. Anything else a
    reader might want is available from their own command line.
    """
    try:
        parts = urllib.parse.urlsplit(api_base)
    except ValueError:
        return "[unparseable --api-base, not echoed]"
    if not parts.scheme or not parts.netloc:
        return "[unparseable --api-base, not echoed]"
    # userinfo is dropped by construction: netloc starts at the LAST '@', so `user:pass@` cannot survive.
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], "", "", ""))


def _scrub(text: str, secret: str = "") -> str:
    """Remove anything credential-shaped from UNTRUSTED text before it is echoed into a verdict.

    ⛔ THE RESPONSE BODY AND ITS HEADERS ARE THE CALLER'S OPPONENT (review cycles 4–6). Every `detail`
    is printed on the verdict line (and `--json` serialises the whole result), and a responder that
    echoes the request headers puts `<TOKEN>` in its own output — the module's absolute guarantee
    ("the credential is NEVER printed … in any output") is only true if the echoed text is scrubbed,
    because `!r` escaping alone only removes newlines.

    ⛔ REDACT BY VALUE **AND** BY SHAPE. The prefix regex cannot see a credential that has no documented
    prefix — a legacy 40-hex PAT, or any non-GitHub value in `GH_TOKEN` (which `token_shape()` already
    anticipates by returning "unrecognised prefix") — so the resolved value is removed explicitly first.
    The regex stays as belt-and-braces for a token that appears in text the value-replace missed.
    """
    out = text or ""
    if secret:
        out = out.replace(secret, "<redacted-token>")
        # ⛔ MATCH **ANY WINDOW** OF THE VALUE, NOT JUST ITS ENDS (review cycles 8-9). A proxy that returns
        # an excerpt, or a responder that folds a long header, echoes a FRAGMENT — and the fragment need
        # not touch either end (a fixed-width "here is what you sent" panel elides both ends and prints the
        # middle). End-anchored replaces missed exactly that, and the shape regex cannot help a value with
        # no documented prefix. So: index every 8-char window of the credential, slide an 8-char window
        # over the text, and extend the match while the extended needle is still a substring of the value.
        # 8 chars is the shortest fragment this tool treats as identifying (the tests' own standard).
        if len(secret) >= 8:
            windows = {secret[_i:_i + 8] for _i in range(len(secret) - 7)}
            pieces: list[str] = []
            _i = 0
            while _i < len(out):
                if out[_i:_i + 8] in windows:
                    # ⛔ ANCHOR THE EXTENSION (review cycle 20). `out[_i:_j+1] in secret` is a FREE substring
                    # test, and a 1-2 char continuation is virtually always a substring of any token — so
                    # the redaction ran far past the fragment and mangled text that was never a credential
                    # (measured: the marker was placed INSIDE an unrelated 40-hex value). Extend only while
                    # the span still matches the SAME position in the secret.
                    _p = secret.find(out[_i:_i + 8])
                    _j = _i + 8
                    while (_j < len(out) and _p >= 0
                           and secret[_p:_p + (_j + 1 - _i)] == out[_i:_j + 1]):
                        _j += 1
                    pieces.append("<redacted-token>")
                    _i = _j
                else:
                    pieces.append(out[_i])
                    _i += 1
            out = "".join(pieces)
    return re.sub(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+", "<redacted-token>", out)


def _sanitise_result(res: dict, secret: str) -> dict:
    """Scrub every field of the verdict that is built from RESPONSE TEXT, by value and by shape.

    One place, because the printing paths are several (the OK line, the refusal line, the human summary
    on stderr, and `--json`), and a fix applied per call site left `ratelimit_remaining` raw after
    cycles 5 and 6 both closed a sibling header — the exact "scrub at the source" lesson.
    """
    for key in ("detail", "ratelimit_remaining", "scopes", "api_base", "repo",
                "credential_source"):
        if isinstance(res.get(key), str):
            res[key] = _scrub(res[key], secret)
    if isinstance(res.get("surfaces"), dict):
        res["surfaces"] = {k: (_scrub(v, secret) if isinstance(v, str) else v)
                           for k, v in res["surfaces"].items()}
    return res


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Surface a 3xx instead of silently re-issuing it — and never as a downgraded GET.

    Returning None leaves the request unhandled, so urllib raises the HTTPError carrying the real
    status (301/302/303/307/308), which then fails closed like any other unexpected status.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


# ── credential resolution ────────────────────────────────────────────────────────────────────────
# gh's OWN ORDER: GH_TOKEN, then GITHUB_TOKEN, then the config file. Reading the file beats asking
# the binary (`gh auth token`): that is a subprocess on the critical path, and it would inherit the
# same shim/host hazards this tool exists to report. Mirrors `objective.py::_gh_token` and
# `pr-doctor.py::_gh_token` deliberately, so the three cannot disagree about who the lane is.
def _hosts_path() -> str:
    """gh's OWN directory resolution: `GH_CONFIG_DIR`, then `XDG_CONFIG_HOME`, then `~/.config`.

    ⛔ THE `GH_CONFIG_DIR` LEG IS NOT COSMETIC. This tool's docstring says it mirrors
    `objective.py::_gh_token` and `pr-doctor.py::_gh_token` so the three cannot disagree about who
    the lane is — and BOTH siblings consult `GH_CONFIG_DIR` FIRST (`objective.py:2285`,
    `pr-doctor.py:1761`). Omitting it made this tool read a DIFFERENT store from the one `gh` uses
    on any box that sets it: a false `no-credential` for a lane whose `gh` can write, or a verdict
    about a credential the landing lane will never use (#2138 r4's wrong-identity hazard).
    MEASURED in review round 3: with `GH_CONFIG_DIR` holding a token, this returned `no-credential`.

    Residual, stated rather than implied: the OS KEYRING is not read (no `gh auth token`
    subprocess), exactly as `objective.py` documents for itself — a keyring-only box answers
    `no-credential` here, which fails closed rather than guessing.
    """
    cfg = os.environ.get("GH_CONFIG_DIR")
    if cfg:
        return os.path.join(os.path.expanduser(cfg), "hosts.yml")
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return os.path.join(os.path.expanduser(xdg), "gh", "hosts.yml")
    return os.path.expanduser("~/.config/gh/hosts.yml")


def _token_from_hosts() -> tuple[str, str]:
    """Return (token, login) from the ACTIVE (depth-1) github.com entry only.

    A multi-account hosts.yml nests other logins under `users:` ABOVE the active account's own keys,
    so taking the first `oauth_token` anywhere in the block returns a DIFFERENT identity's credential
    (#2138 r4). gh's own layout puts the active account at depth 1.
    """
    try:
        with open(_hosts_path(), encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return "", ""
    in_github = False
    token = login = ""
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[:1].isspace():                      # a depth-0 key starts a new host block
            in_github = line.split(":", 1)[0].strip() == "github.com"
            continue
        if not in_github or not line.startswith("    ") or line.startswith("        "):
            continue
        stripped = line.strip()
        if stripped.startswith("oauth_token:") and not token:
            token = stripped.split(":", 1)[1].strip().strip("\"'")
        elif stripped.startswith("user:") and not login:
            login = stripped.split(":", 1)[1].strip().strip("\"'")
    return token, login


def resolve_credential() -> tuple[str, str]:
    """Return (token, source). `source` is a LABEL, never a value."""
    host = (os.environ.get("GH_HOST") or "github.com").strip().lower()
    if host in ("github.com", "api.github.com"):
        for var in ("GH_TOKEN", "GITHUB_TOKEN"):
            tok = (os.environ.get(var) or "").strip()
            if tok:
                return tok, f"env:{var}"
    tok, _login = _token_from_hosts()
    if tok:
        # ⛔ THE LABEL MUST NOT CARRY FILE CONTENT (review cycle 15). `login` is the depth-1 `user:` line —
        # arbitrary text in the file — so a prefix-less legacy value there survived both the value scrub
        # (it is not the resolved token) and the shape regex (no documented prefix) and was printed.
        # The depth-1 rule already guarantees WHICH account was read, so `active` loses no diagnostic
        # value; a real GitHub login is kept because it is useful and cannot be a credential.
        # ⛔ NO VALUE FROM THE FILE, NOT EVEN A "LOGIN-SHAPED" ONE (review cycle 16): a shape allowlist is
        # still a guess, and a 32-hex secret is a common secret shape that passes it. The depth-1 read
        # already says WHICH account answered, so the label carries only that fact.
        return tok, "hosts.yml:active"
    return "", "none"


def token_shape(tok: str) -> str:
    """Classify the token TYPE from its documented prefix. Never the value."""
    for prefix, label in (
        ("github_pat_", "fine-grained-PAT (no OAuth scopes by design; permissions are per-resource)"),
        ("ghp_", "classic-PAT (OAuth scopes apply)"),
        ("gho_", "OAuth user-to-server (OAuth scopes apply)"),
        ("ghs_", "server-to-server (OAuth scopes apply)"),
        ("ghu_", "user-to-server (OAuth scopes apply)"),
    ):
        if tok.startswith(prefix):
            return label
    return "unrecognised prefix"


# ── the probes ───────────────────────────────────────────────────────────────────────────────────
def _request(url: str, token: str, method: str = "GET", body: bytes | None = None) -> tuple[int, dict, str]:
    """One API call. Returns (status, headers-lowercased, body). Transport failure -> status 0."""
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "gh-write-preflight/2")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, \
                resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", "replace") if e.fp else ""
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, payload
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
        # ⛔ `http.client.HTTPException` IS NOT AN `OSError` (measured: `BadStatusLine` and
        # `IncompleteRead` are both `HTTPException` and NEITHER subclasses `OSError`), so a proxy, a
        # captive portal or a truncated response produced a raw TRACEBACK with EMPTY stdout — which
        # breaks the documented contract (`GH-WRITE-UNAVAILABLE(<reason>)`) and gives a `--json`
        # consumer `json.loads("")`. It failed closed, but without a verdict (review round 4).
        return 0, {}, str(e)


def _classify_403(headers: dict, body: str, secret: str = "") -> tuple[str, str]:
    """Distinguish a RATE LIMIT from a PERMISSION refusal. Order matters."""
    low = (body or "").lower()
    remaining = (headers.get("x-ratelimit-remaining") or "").strip()
    retry_after = (headers.get("retry-after") or "").strip()
    # Rate limit FIRST: mis-classifying it sends the operator to re-mint a perfectly good token.
    if "rate limit" in low or "abuse detection" in low or "secondary rate" in low:
        return "rate-limited", "the API named a rate limit in the refusal body"
    if remaining == "0" or retry_after:
        # ⛔ HEADER VALUES ARE UNTRUSTED TEXT TOO, AND THEY LAND ON THE VERDICT LINE. `retry-after` is not
        # validated numerically, so a responder could put a newline + `GH-WRITE-OK(...)` in it (measured
        # against a stub: stdout then carried a line beginning with the pass string). Scrubbed and
        # `!r`-escaped like the bodies, and bounded so a hostile header cannot flood the line either.
        # ⛔ HEADER VALUES ARE NOT ECHOED EITHER (review cycle 17). Scrubbing them by the RESOLVED token
        # could not cover a DIFFERENT credential reflected there, and a shape allowlist is a guess — the
        # only total fix is to report the FACT and the size, never the text.
        return "rate-limited", (f"no quota left (x-ratelimit-remaining present={bool(remaining)}, "
                                f"retry-after present={bool(retry_after)})")
    if "saml" in low or "sso" in low:
        return "sso-unauthorized", "the refusal names SAML/SSO — the token is not authorised for the org"
    # The exact wording GitHub uses when a FINE-GRAINED token lacks a resource permission.
    if "resource not accessible" in low:
        return "not-permitted", "the token lacks write on this resource ('Resource not accessible by …')"
    return "forbidden", "403 with no rate-limit or SSO signal — see the body below"


def _write_probe(url: str, token: str, body: bytes) -> dict:
    """Run one write-surface probe. Returns {'ok': bool, 'reason': str, 'detail': str, 'status': int}."""
    status, headers, payload = _request(url, token, method="POST", body=body)
    if status == 0:
        # ⛔ NEVER THE RAW URL (review cycles 17-20): it embeds the caller-supplied `api_base` PATH, and a
        # legacy value there survives both scrubs when the credential that resolves is a different one.
        # The READ probe already reports `_printable_base(api_base)`; this branch is the same rule.
        return {"ok": False, "reason": "transport", "detail": f"could not reach {_printable_base(url)}: transport failure ({len(payload)} bytes of response text, not echoed)",
                "status": 0}
    if status in _WRITE_OK:
        return {"ok": True, "reason": "permitted",
                "detail": f"authorised (HTTP {status} — the payload was rejected, the permission was not)",
                "status": status}
    if status in _SURPRISING_SUCCESS:
        # ⛔ A 2xx ON A MUST-BE-INVALID PAYLOAD IS NOT A PASS. Either the payload was accepted (it
        # cannot be — an issue needs a title, the head ref cannot exist) or the response came from
        # something that is not GitHub's validation path (a redirect downgraded to a GET, a proxy, a
        # captive portal). Fail closed and say so.
        return {"ok": False, "reason": "forbidden",
                "detail": f"HTTP {status} on a must-be-invalid payload — the request was accepted or "
                          f"rewritten, so this did NOT prove the permission gate",
                "status": status}
    if status == 401:
        return {"ok": False, "reason": "bad-credentials", "detail": "401 from the write probe", "status": status}
    if status == 403:
        reason, detail = _classify_403(headers, payload, token)
        return {"ok": False, "reason": reason, "detail": detail, "status": status}
    if status == 404:
        return {"ok": False, "reason": "repo-not-visible", "detail": "404 from the write probe", "status": status}
    # ⛔ AN HTTP 429 IS A RATE LIMIT REGARDLESS OF ITS BODY. It is NOT a permission refusal, and it
    # must never fall through to the generic branch below — a 429 on the pulls surface would then be
    # reported as `pull-requests-write-missing` and send the operator to re-mint a perfectly good
    # token, which is the exact hour-wasting misdirection this tool exists to prevent.
    if status == 429:
        return {"ok": False, "reason": "rate-limited",
                "detail": "HTTP 429 Too Many Requests — a shared-account limit, not a permission defect",
                "status": status}
    # ⛔ A 5xx IS GITHUB'S FAULT, NOT THE TOKEN'S. Reporting it as a permission verdict would charge
    # an outage to the credential.
    if status >= 500:
        return {"ok": False, "reason": "server-error",
                "detail": f"HTTP {status} from GitHub — a service fault, not a verdict about the token",
                "status": status}
    return {"ok": False, "reason": "forbidden", "detail": f"unexpected HTTP {status}: {len(payload)} bytes of response text, not echoed",
            "status": status}


def preflight(repo: str, api_base: str, token: str, source: str) -> dict:
    result: dict = {
        # ⛔ THE REPO IS ECHOED TOO — scrub it by value, like api_base (review cycle 13): `--repo`
        # defaults to `$OR`, so a mis-set `$OR` (or a literal token) would otherwise be printed in the
        # `--json` artifact. Added to the sanitise key list as well, for any field added later.
        # ⛔ WITH NO RESOLVED CREDENTIAL THERE IS NOTHING TO SCRUB BY VALUE, so a `$OR` holding one would
        # be printed verbatim (review cycle 16). Withhold the echo in that case; a repo identifier is not
        # load-bearing for a refusal that already names the reason.
        "repo": _scrub(repo, token) if token else "<not echoed — no credential resolved>",
        # ⛔ WHICH ENDPOINT ANSWERED IS PART OF THE VERDICT. Without this key a consumer holding the
        # `--json` artifact cannot tell a measurement of GitHub from a measurement of anything else.
        # ⛔ REDACTED, because this dict IS the `--json` output: see `_printable_base`.
        "api_base": _scrub(_printable_base(api_base), token or ""),
        # ⛔ THE SOURCE LABEL IS BUILT FROM FILE CONTENT (`hosts.yml:{login}`), so it must be scrubbed
        # like every other echoed field (review cycle 14): a hosts.yml holding the token in `user:` made
        # the "label is never a value" promise unenforced and printed the credential on stderr and in --json.
        "credential_source": _scrub(source, token or ""),
        "token_shape": token_shape(token) if token else "none",
        "ok": False,
        "reason": "",
        "detail": "",
        "surfaces": {},
    }

    # ⛔ BEFORE THE TOKEN IS EVEN RESOLVED: a base we would refuse must never be sent a credential.
    # This is also the defence for callers that reach `preflight()` directly, not through `main()`.
    base_refusal = _validate_api_base(api_base)
    if base_refusal:
        result.update(reason="usage", detail=base_refusal)
        return result

    gh_host = (os.environ.get("GH_HOST") or "").strip().lower()
    if gh_host and gh_host not in ("github.com", "api.github.com"):
        # ⛔ `GH_HOST` IS ECHOED INPUT, AND ON THIS PATH NOTHING ELSE WAS SCRUBBED (review cycle 16): the
        # env-token leg is skipped for a non-github host, so `token` can be "" and a credential placed in
        # GH_HOST printed verbatim. Echo it only when it is hostname-shaped; the refusal needs the FACT
        # that it is not github.com, not the value.
        # ⛔ NOT EVEN A "HOSTNAME-SHAPED" ECHO: a 32-hex secret IS hostname-shaped, so a shape allowlist
        # leaks it (measured). The value adds nothing the reader needs — the verdict is "not github.com".
        _host_echo = "GH_HOST is set to a value that is not github.com (not echoed)"
        result.update(reason="wrong-host",
                      detail=f"{_host_echo} — this preflight, and the rail it protects, speak github.com")
        return result

    # ⛔ THE REPO SHAPE IS VALIDATED HERE, NOT ONLY IN `main` (review cycle 11). The CLI guard is the
    # first line of defence, but `preflight()` is also reachable directly (the suite does it), and a
    # `repo` carrying `?` or `#` makes `http.client` send the SAME target for all three probes — one 422
    # then satisfies all three gates. A guard that only exists at one caller is not the gate.
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", repo or ""):
        result.update(reason="usage",
                      detail="--repo must be exactly `owner/name` (letters, digits, '.', '_', '-'); a value "
                             "carrying '?' or '#' collapses the three write probes onto one endpoint "
                             "(the value is not echoed)")
        return result

    if not token:
        result.update(reason="no-credential",
                      detail="GH_TOKEN and GITHUB_TOKEN are unset and hosts.yml holds no active github.com token")
        return result

    # ⛔ A CREDENTIAL THAT CANNOT GO IN A HEADER MUST BE NAMED, NOT THROWN (review P1, #10930).
    # `http.client.putheader` raises `ValueError('Invalid header value b\'Bearer <TOKEN>\'')` for a
    # token carrying an interior newline/CR (a severed or wrapped secret file) — a message that
    # CONTAINS THE CREDENTIAL. `_request` catches URLError/TimeoutError/OSError/HTTPException, and
    # `ValueError` is none of them, so it escaped `main()` as a TRACEBACK with the token on stderr and
    # EMPTY stdout (breaking both "the credential is never printed" and the "always a verdict" / --json
    # contract). Adding `ValueError` to that except clause would be WORSE, not better: `_request`
    # returns `str(e)` into `detail`, which IS printed — a traceback leak would become a first-class
    # printed one. So it is refused HERE, before the value is ever interpolated, and the reason never
    # echoes it (not even truncated).
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in token) or not token.isascii():
        result.update(reason="bad-credentials",
                      detail="the resolved credential carries whitespace, a control character or a non-ASCII "
                             "byte (a severed, wrapped or corrupt secret) — re-source it; the value is "
                             "deliberately not echoed")
        return result

    # ── 1. READ: the disambiguator for any 404 that follows, and the source of default_branch.
    status, headers, body = _request(f"{api_base}/repos/{repo}", token)
    if status == 0:
        result.update(reason="transport", detail=f"could not reach {_printable_base(api_base)!r}: transport failure ({len(body)} bytes of response text, not echoed)")
        return result
    if status == 401:
        result.update(reason="bad-credentials", detail="the API returned 401 — expired, revoked, or mistyped")
        return result
    if status == 404:
        result.update(reason="repo-not-visible",
                      detail=f"{repo} is not visible to this credential — an access question, not a write question")
        return result
    if status == 403:
        reason, detail = _classify_403(headers, body, token)
        # ⛔ A REFUSED **READ** IS A READ PROBLEM, NOT A MISSING WRITE PERMISSION. `_classify_403`'
        # wording/name is written for a write probe ("the token lacks write …"), and `not-permitted`
        # is deliberately absent from the REASONS contract — so feeding it straight through emitted an
        # UNDOCUMENTED reason carrying write-shaped advice about a read (review round 3).
        if reason == "not-permitted":
            reason = "repo-not-visible"
            detail = "the token cannot READ this repo — an access question, not a write question"
        result.update(reason=reason, detail=f"on the READ probe: {detail}")
        return result
    if status == 429:
        result.update(reason="rate-limited",
                      detail="HTTP 429 on the READ probe — a shared-account limit, not a permission defect")
        return result
    if status >= 500:
        result.update(reason="server-error",
                      detail=f"HTTP {status} on the READ probe — a GitHub service fault, not a verdict about the token")
        return result
    if status != 200:
        result.update(reason="forbidden", detail=f"unexpected HTTP {status} on the read probe: {len(body)} bytes of response text, not echoed")
        return result

    result["read_ok"] = True
    # ⛔ THE SCOPE HEADER IS ALSO RESPONSE TEXT (review cycle 5). It is printed on the human summary line,
    # so a responder that reflects the request's Authorization header there — or obs-folds a
    # `GH-WRITE-OK(...)` into it — put that text in the tool's own output. Scrubbed at the source: the
    # scope NAMES that make the line useful are unaffected, only token-shaped strings and control
    # characters go.
    # ⛔ SCRUB BY VALUE **BEFORE** BOUNDING IT (review cycle 7). `_sanitise_result` runs after the field
    # was already truncated, so a credential longer than the bound survived as its first N characters —
    # the guarantee is "never printed … not even truncated". The token is in scope here, so each echoed
    # header/body is value-scrubbed at the SOURCE and only then cut to size.
    # ⛔ NOT ECHOED: the scope header is response text, and a response can reflect the request's
    # Authorization header back. The presence of scopes is the useful, safe fact.
    result["scopes"] = "<present — not echoed>" if headers.get("x-oauth-scopes") else ""   # classic/OAuth ONLY
    # ⛔ A RESPONSE HEADER VALUE IS NOT ECHOED (review cycle 18) — same rule as `scopes`: report the FACT.
    # Only `"0"` is meaningful (it gates the write probes) and it is a constant, so it is safe to carry.
    _rl_raw = (headers.get("x-ratelimit-remaining") or "").strip()
    result["ratelimit_remaining"] = "0" if _rl_raw == "0" else ("present — not echoed" if _rl_raw else "")

    # ── 2. RATE LIMIT pre-check: never spend a write probe on an exhausted account, and never
    #      report a permission defect for a bucket that will refill.
    if result["ratelimit_remaining"] == "0":
        result.update(reason="rate-limited",
                      detail=f"x-ratelimit-remaining=0 on the read (reset header present={bool(headers.get('x-ratelimit-reset'))}); "
                             f"the token may be perfectly good — shared-account exhaustion, not a permission defect")
        return result

    default_branch = "main"
    with contextlib.suppress(ValueError, AttributeError):
        default_branch = json.loads(body).get("default_branch") or "main"

    # ── 3. ISSUES WRITE — empty payload, so nothing can be created.
    # ⛔ COMPARE THE TARGETS `http.client` WILL ACTUALLY SEND (review cycle 11). The first version of this
    # assertion compared the URL STRINGS, which share a prefix and differ by suffix — so it was true for
    # every possible `repo` and could never fire (measured: `preflight('o/r/issues#', …)` still returned
    # ok=True with the stub seeing three POSTs to /repos/o/r/issues). The fragment and query are exactly
    # what the client drops, so they are stripped before the comparison.
    def _sent_target(u: str) -> str:
        parts = urllib.parse.urlsplit(u)
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    _probe_urls = (f"{api_base}/repos/{repo}/issues", f"{api_base}/repos/{repo}/pulls",
                   f"{api_base}/repos/{repo}/git/refs")
    if len({_sent_target(u) for u in _probe_urls}) != 3:
        result.update(reason="usage",
                      detail="the three write probes do not name three distinct endpoints — a repo value "
                             "carrying '?' or '#' collapses them onto one path, so one 422 would satisfy "
                             "all three gates (the value is not echoed)")
        return result
    issues = _write_probe(_probe_urls[0], token, b"")
    result["surfaces"]["issues"] = issues["status"]

    # ── 4. PULL REQUESTS WRITE — a head ref that cannot exist, so no PR can be created.
    bogus_head = f"gh-write-preflight-{secrets.token_hex(8)}"
    pulls_body = json.dumps({"head": f"refs/heads/{bogus_head}", "base": default_branch,
                             "title": "gh-write-preflight (never created)"}).encode()
    pulls = _write_probe(_probe_urls[1], token, pulls_body)
    result["surfaces"]["pull_requests"] = pulls["status"]

    # ── 5. CONTENTS WRITE — a random ref name and an unresolvable sha, so no ref can be created.
    # ⛔ LOAD-BEARING, NOT BELT-AND-BRACES. Merging a PR and creating a ref are charged to
    # `contents: write`, not `pull_requests: write` — measured on PUT /pulls/{n}/merge and
    # POST /git/refs, both answering `x-accepted-github-permissions: contents=write`. A token with
    # Issues+PR write and Contents: read therefore passed the two probes above and was then refused
    # by `git push`: a lane could not publish its branch at all while this tool said GH-WRITE-OK.
    # MEASURED: 422 "Object does not exist" authorised, 403 refused, and matching-refs stays empty.
    bogus_ref = f"gh-write-preflight-{secrets.token_hex(8)}"
    contents_body = json.dumps({"ref": f"refs/heads/{bogus_ref}", "sha": "0" * 40}).encode()
    contents = _write_probe(_probe_urls[2], token, contents_body)
    result["surfaces"]["contents"] = contents["status"]

    issues_ok, pulls_ok, contents_ok = issues["ok"], pulls["ok"], contents["ok"]

    # ── 6. Verdict. ⛔ ONLY A `not-permitted` PROBE MAY PRODUCE A PERMISSION VERDICT.
    # This is deliberately a ONE-item allow-list and not a list of known non-permission reasons.
    # It used to be the latter, which meant every status nobody had thought of — a 410 (Issues
    # disabled on the repo), a 400 from a proxy/WAF, a 451 — fell through to `forbidden` and was then
    # PROMOTED to a missing-permission verdict, sending the operator to re-mint a perfectly good
    # token. A list of known-bad reasons fails open for the unknown ones; naming the ONE reason that
    # is allowed to claim a missing permission fails closed instead.
    for name, probe in (("issues", issues), ("pull_requests", pulls), ("contents", contents)):
        if not probe["ok"] and probe["reason"] != "not-permitted":
            result.update(reason=probe["reason"], detail=f"on the {name} surface: {probe['detail']}")
            return result

    if issues_ok and pulls_ok and contents_ok:
        result.update(ok=True, reason="issues+pull-requests+contents writable",
                      detail=f"all three write surfaces authorised (issues HTTP {issues['status']}, "
                             f"pull_requests HTTP {pulls['status']}, contents HTTP {contents['status']})")
        return result

    # ── 7. Name WHICH permission is missing. Every probe reaching here is `not-permitted`, so the
    # naming below is a real statement about which permission is missing rather than a guess about an
    # unrecognised status. All three refused is a different fact from one refused.
    surfaces = (("issues", issues_ok, issues), ("pull_requests", pulls_ok, pulls),
                ("contents", contents_ok, contents))
    refused = [name for name, ok, _ in surfaces if not ok]
    statuses = ", ".join(f"{name} HTTP {probe['status']}" for name, _ok, probe in surfaces)
    if len(refused) == 3:
        result.update(reason="token-read-only",
                      detail=f"all three write surfaces refused ({statuses}): {contents['detail']}")
        return result

    # Landing order: a lane pushes a branch first, then comments on the PR, then closes issues. Naming
    # the earliest blocker is the one that actually stops it; `detail` and `surfaces` name all of them.
    _MISSING = {
        "contents": ("contents-write-missing",
                     "CONTENTS are not writable — the lane cannot push its branch or merge at all, so it "
                     "would fail before it could comment"),
        "pull_requests": ("pull-requests-write-missing",
                          "PULL REQUESTS are not writable — a landing lane posts its verdict comment on a "
                          "PR, so it would fail at the closure step"),
        "issues": ("issues-write-missing",
                   "ISSUES are not writable — a landing also closes issues"),
    }
    for name in ("contents", "pull_requests", "issues"):             # landing order, earliest first
        if name in refused:
            reason, why = _MISSING[name]
            writable = ", ".join(n for n, ok, _ in surfaces if ok) or "none"
            result.update(reason=reason,
                          detail=f"{why}. Writable: {writable}; refused: {', '.join(refused)} "
                                 f"({statuses})")
            return result
    result.update(reason="forbidden", detail=f"unrecognised surface state: {refused}")
    return result


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="Prove this lane's credential can WRITE (issues, pull requests, contents) before it "
                    "does any work.")
    ap.add_argument("--repo", default=os.environ.get("OR", ""), help="owner/name (default: $OR)")
    ap.add_argument("--api-base", default=os.environ.get("GH_WRITE_PREFLIGHT_API", DEFAULT_API),
                    help="API base URL — default https://api.github.com, or a LOOPBACK stub for the "
                         "hermetic test seam; anything else is refused as `usage`")
    ap.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = ap.parse_args(argv)

    # ⛔ RESOLVE THE CREDENTIAL FIRST: THE EARLY REFUSALS PRINT `api_base`, AND WITHOUT THE TOKEN THEY
    # CANNOT VALUE-SCRUB IT (review cycle 12). MEASURED: `--repo 'o/r#'` + `--json` with the credential in
    # the base's PATH printed the whole value while the same base with a VALID repo was correctly
    # redacted — the fix lived only after this guard. Resolution makes no request and cannot fail.
    token, source = resolve_credential()

    # ⛔ ONE GUARD, BOTH TIERS, AND A `--json` CALLER ALWAYS GETS JSON. The `--repo` value reaches a
    # request path; `?`/`#` collapse the three write probes onto one endpoint (cycle 10), and non-ASCII or
    # control characters raise `UnicodeEncodeError` inside `http.client` (cycle 3). A missing `/` is the
    # same refusal class, so it serialises the same way instead of printing a human line at a JSON consumer.
    def _repo_refusal(detail: str) -> int:
        print(json.dumps({"repo": _scrub(args.repo, token) if token else "<not echoed — no credential resolved>",
                          "api_base": _scrub(_printable_base(args.api_base), token),
                          "ok": False, "reason": "usage", "detail": detail},
                         indent=2, sort_keys=True) if args.json
              else f"GH-WRITE-UNAVAILABLE(usage) — {detail}")
        return 1

    if not args.repo or "/" not in args.repo:
        return _repo_refusal("pass --repo owner/name (or set $OR)")
    if (not args.repo.isascii() or any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in args.repo)
            or not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", args.repo)):
        return _repo_refusal("--repo must be exactly `owner/name` (letters, digits, '.', '_', '-') with "
                             "no whitespace, control character or non-ASCII byte; a value carrying '?' "
                             "or '#' collapses the three write probes onto one endpoint (the value is "
                             "not echoed)")
    res = _sanitise_result(preflight(args.repo, args.api_base.rstrip("/"), token, source), token)

    if args.json:
        print(json.dumps(res, indent=2, sort_keys=True))
    elif res["ok"]:
        # ⛔ THE OK LINE NAMES THE ENDPOINT. A pass is only a pass if it was measured against GitHub,
        # so the verdict carries the thing it is a verdict about — in the human line, not only in JSON.
        print(f"GH-WRITE-OK({res['reason']} @ {res['api_base']}) — {res['detail']}")
    else:
        print(f"GH-WRITE-UNAVAILABLE({res['reason']}) — {res['detail']}")

    if not res["ok"]:
        print(f"  credential: {res['credential_source']} · shape: {res['token_shape']}", file=sys.stderr)
        if res.get("surfaces"):
            print(f"  surfaces: issues={res['surfaces'].get('issues')} "
                  f"pull_requests={res['surfaces'].get('pull_requests')} "
                  f"contents={res['surfaces'].get('contents')} "
                  f"(422 = authorised+rejected, 403 = refused)", file=sys.stderr)
        if res.get("scopes"):
            print(f"  oauth scopes: {res['scopes']!r}", file=sys.stderr)
        elif res["token_shape"].startswith("fine-grained"):
            print("  oauth scopes: (none sent — fine-grained PATs carry PER-RESOURCE permissions, not scopes)",
                  file=sys.stderr)
        print("  fix per reason: contents-write-missing→re-mint with `Contents: write` · "
              "pull-requests-write-missing→re-mint with `Pull requests: write` · "
              "issues-write-missing→re-mint with `Issues: write` · "
              "rate-limited→wait (shared account), do NOT re-mint · "
              "token-read-only→re-mint with all three write permissions · "
              "bad-credentials→re-source · no-credential→supply one · sso→authorise for org",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
