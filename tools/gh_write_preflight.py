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

4. **RATE LIMIT** — checked from the READ response *before* either write probe, and again from each
   probe's own response. A `403` is either a permission refusal or a rate limit, and confusing them
   wastes an hour the wrong way: re-minting a token does nothing for a rate limit, waiting does
   nothing for a missing permission. The discriminator is the body plus the headers
   (`x-ratelimit-remaining: 0`, `retry-after`), never the bare status code.

5. **RATE LIMIT** is checked from the READ response *before* any write probe, and again from each
   probe's own response, for the reason in the paragraph below.

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
  usage                        bad arguments

USAGE
─────
  uv run python tools/gh_write_preflight.py --repo daniel-ospina/tortoise
  uv run python tools/gh_write_preflight.py --repo "$OR" --json
  GH_WRITE_PREFLIGHT_API=http://127.0.0.1:PORT uv run python tools/gh_write_preflight.py …   # test seam

  exit 0 = GH-WRITE-OK(issues+pull-requests+contents writable)
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
import json
import os
import secrets
import urllib.error
import urllib.request

DEFAULT_API = "https://api.github.com"
TIMEOUT_S = 25

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
    tok, login = _token_from_hosts()
    if tok:
        return tok, f"hosts.yml:{(login or 'active')}"
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


def _classify_403(headers: dict, body: str) -> tuple[str, str]:
    """Distinguish a RATE LIMIT from a PERMISSION refusal. Order matters."""
    low = (body or "").lower()
    remaining = (headers.get("x-ratelimit-remaining") or "").strip()
    retry_after = (headers.get("retry-after") or "").strip()
    # Rate limit FIRST: mis-classifying it sends the operator to re-mint a perfectly good token.
    if "rate limit" in low or "abuse detection" in low or "secondary rate" in low:
        return "rate-limited", "the API named a rate limit in the refusal body"
    if remaining == "0" or retry_after:
        return "rate-limited", (f"no quota left (x-ratelimit-remaining={remaining or '?'}, "
                                f"retry-after={retry_after or '?'})")
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
        return {"ok": False, "reason": "transport", "detail": f"could not reach {url}: {payload[:160]}",
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
        reason, detail = _classify_403(headers, payload)
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
    return {"ok": False, "reason": "forbidden", "detail": f"unexpected HTTP {status}: {payload[:200]}",
            "status": status}


def preflight(repo: str, api_base: str, token: str, source: str) -> dict:
    result: dict = {
        "repo": repo,
        "credential_source": source,
        "token_shape": token_shape(token) if token else "none",
        "ok": False,
        "reason": "",
        "detail": "",
        "surfaces": {},
    }

    gh_host = (os.environ.get("GH_HOST") or "").strip().lower()
    if gh_host and gh_host not in ("github.com", "api.github.com"):
        result.update(reason="wrong-host",
                      detail=f"GH_HOST={gh_host!r} — this preflight, and the rail it protects, speak github.com")
        return result

    if not token:
        result.update(reason="no-credential",
                      detail="GH_TOKEN and GITHUB_TOKEN are unset and hosts.yml holds no active github.com token")
        return result

    # ── 1. READ: the disambiguator for any 404 that follows, and the source of default_branch.
    status, headers, body = _request(f"{api_base}/repos/{repo}", token)
    if status == 0:
        result.update(reason="transport", detail=f"could not reach {api_base}: {body[:160]}")
        return result
    if status == 401:
        result.update(reason="bad-credentials", detail="the API returned 401 — expired, revoked, or mistyped")
        return result
    if status == 404:
        result.update(reason="repo-not-visible",
                      detail=f"{repo} is not visible to this credential — an access question, not a write question")
        return result
    if status == 403:
        reason, detail = _classify_403(headers, body)
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
        result.update(reason="forbidden", detail=f"unexpected HTTP {status} on the read probe: {body[:160]}")
        return result

    result["read_ok"] = True
    result["scopes"] = headers.get("x-oauth-scopes", "")      # classic/OAuth ONLY
    result["ratelimit_remaining"] = (headers.get("x-ratelimit-remaining") or "").strip()

    # ── 2. RATE LIMIT pre-check: never spend a write probe on an exhausted account, and never
    #      report a permission defect for a bucket that will refill.
    if result["ratelimit_remaining"] == "0":
        result.update(reason="rate-limited",
                      detail=f"x-ratelimit-remaining=0 on the read (reset={headers.get('x-ratelimit-reset', '?')}); "
                             f"the token may be perfectly good — shared-account exhaustion, not a permission defect")
        return result

    default_branch = "main"
    with contextlib.suppress(ValueError, AttributeError):
        default_branch = json.loads(body).get("default_branch") or "main"

    # ── 3. ISSUES WRITE — empty payload, so nothing can be created.
    issues = _write_probe(f"{api_base}/repos/{repo}/issues", token, b"")
    result["surfaces"]["issues"] = issues["status"]

    # ── 4. PULL REQUESTS WRITE — a head ref that cannot exist, so no PR can be created.
    bogus_head = f"gh-write-preflight-{secrets.token_hex(8)}"
    pulls_body = json.dumps({"head": f"refs/heads/{bogus_head}", "base": default_branch,
                             "title": "gh-write-preflight (never created)"}).encode()
    pulls = _write_probe(f"{api_base}/repos/{repo}/pulls", token, pulls_body)
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
    contents = _write_probe(f"{api_base}/repos/{repo}/git/refs", token, contents_body)
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
                    help="API base URL (test seam; default https://api.github.com)")
    ap.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = ap.parse_args(argv)

    if not args.repo or "/" not in args.repo:
        print("GH-WRITE-UNAVAILABLE(usage) — pass --repo owner/name (or set $OR)")
        return 1

    token, source = resolve_credential()
    res = preflight(args.repo, args.api_base.rstrip("/"), token, source)

    if args.json:
        print(json.dumps(res, indent=2, sort_keys=True))
    elif res["ok"]:
        print(f"GH-WRITE-OK({res['reason']}) — {res['detail']}")
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
            print(f"  oauth scopes: {res['scopes']}", file=sys.stderr)
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
