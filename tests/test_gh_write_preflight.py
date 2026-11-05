#!/usr/bin/env python3
"""Red-first tests for `tools/gh_write_preflight.py` (#10930).

WHY THIS FILE EXISTS
────────────────────
The lander that hit the 403 could read, verify, and decide, and only discovered it could not WRITE at
the very end — after the merge, when the closures were owed. A preflight a lane runs first is only
worth anything if it REFUSES the token that causes that, so the first test here is the RED one, and
it is the *verbatim* shape from #10930:

    POST /issues/10902/comments → 403 "Resource not accessible by personal access token"   (#10902 is a PR)
    POST /issues/10774/comments → 201                                                     (#10774 is an ISSUE)

i.e. **issues writable, pull requests not**. A preflight that probes only an issues-surface resource
passes that token — which is exactly the bug the first version of this tool had, and
`test_probes_the_pull_requests_surface_not_only_issues` exists to stop it coming back.

Every test is hermetic — a local `http.server` stub, no network, no credential, no real repo. The
tool's `GH_WRITE_PREFLIGHT_API` seam makes that possible; it defaults to the real API.

Run:
    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_gh_write_preflight.py -q
    TORTOISE_TEST_CARVE_OUT=1 uv run python tests/test_gh_write_preflight.py   # standalone, no DB

The carve-out is required: this file is stdlib-only and needs NO database, but the repo's default
pytest session refuses to start without `TORTOISE_DB_URI` (epic #1647 P4) unless the carve-out is
set — so `uv run pytest tests/test_gh_write_preflight.py` alone errors once per COLLECTED TEST, for a
reason that has nothing to do with these tests. (No count is stated on purpose: the number is the size
of this file, so any number written here re-stales the next time a test is added — the defect a review
finding already charged to this branch twice.)
"""

from __future__ import annotations

import http.server
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "gh_write_preflight.py"

OK_READ = (200, {}, '{"full_name":"daniel-ospina/tortoise","default_branch":"main"}')
OK_ISSUES = (422, {}, '{"message":"Invalid request.","status":"422"}')
OK_PULLS = (422, {}, '{"message":"Validation Failed","errors":[{"field":"head","code":"invalid"}],"status":"422"}')
# MEASURED 2026-11-04 on a live box: POST /git/refs with an unresolvable sha answers 422
# "Object does not exist" when authorised, 403 when refused, and matching-refs stays empty either way.
OK_CONTENTS = (422, {}, '{"message":"Object does not exist","status":"422"}')
READONLY_403 = (403, {}, '{"message":"Resource not accessible by personal access token","status":"403"}')


class _Stub:
    """A GitHub API stub. Records (method, path, body) so a test can assert the probe SHAPE."""

    def __init__(self, read=OK_READ, issues=OK_ISSUES, pulls=OK_PULLS, contents=OK_CONTENTS,
                 reject_tokens=()):
        self.read, self.issues, self.pulls, self.contents = read, issues, pulls, contents
        self.reject_tokens = set(reject_tokens)   # bearer tokens this stub REFUSES on the write probes
        self.redirect_writes = False
        self.seen: list[tuple[str, str, str]] = []
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _serve(self, method):
                length = int(self.headers.get("Content-Length") or 0)
                req_body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                stub.seen.append((method, self.path, req_body))
                if stub.redirect_writes and method == "POST":
                    self.send_response(301)
                    self.send_header("Location", self.path)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                parts = [p for p in self.path.split("?")[0].split("/") if p]
                bearer = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
                refused = method == "POST" and bearer in stub.reject_tokens
                if refused:
                    status, headers, body = READONLY_403
                elif method == "GET" and len(parts) == 3 and parts[0] == "repos":
                    status, headers, body = stub.read
                elif method == "POST" and len(parts) == 4 and parts[3] == "issues":
                    status, headers, body = stub.issues
                elif method == "POST" and len(parts) == 4 and parts[3] == "pulls":
                    status, headers, body = stub.pulls
                elif method == "POST" and len(parts) == 5 and parts[3] == "git" and parts[4] == "refs":
                    status, headers, body = stub.contents
                else:                                    # anything unexpected is a test failure signal
                    status, headers, body = 400, {}, '{"message":"unexpected probe path"}'
                payload = body.encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if payload:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)

            def do_GET(self):
                self._serve("GET")

            def do_POST(self):
                self._serve("POST")

            def do_DELETE(self):
                self._serve("DELETE")

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _env(stub_base: str, token: str | None, **overrides: str) -> dict:
    """The ONE child environment: no host credential, no host gh config, no host proxy.

    `overrides` are applied LAST so a test can plant `GH_CONFIG_DIR`/`HOME` instead of hand-building a
    second copy of this dict — three tests used to build their own with a bare comprehension and so
    escaped every rule below (review finding F2 at `7d7ce3d57`). One construction site is the point.
    """
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST")}
    # ⛔ BOTH CONFIG-DIR LEGS ARE STRIPPED, SO THE TESTS READ THE TEST'S OWN SETUP AND NOT THE HOST'S.
    # The tool resolves `GH_CONFIG_DIR` → `XDG_CONFIG_HOME` → `~/.config`, so either variable left in
    # would let a host gh config answer `test_no_credential_at_all` for it.
    # MEASURED: with `XDG_CONFIG_HOME` holding a gh/hosts.yml the test fails; with `GH_CONFIG_DIR`
    # holding one, 2 failed / 22 passed.
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("GH_CONFIG_DIR", None)
    # ⛔ A PROXY INHERITED FROM THE HOST BREAKS THE PREMISE EVERY `seen` ASSERTION RESTS ON (review
    # finding F1 at `a9007b139`). `_request` opens through `build_opener()`, which is proxy-aware, so a
    # probe can be routed off the box and the stub then records NOTHING.
    # MEASURED for the `http://` stub bases this file uses: `http_proxy` (any case) → the probe goes to
    # the proxy and `seen` stays empty on a probe-everything mutant, i.e. an inert arm again, silently.
    # `https_proxy` is not a vector for an `http://` base, and it IS one for an `https://` base
    # (measured: the CLIENT sends `CONNECT 127.0.0.1:1 HTTP/1.1` to the proxy), so stripping it also
    # covers a future test that binds one. `all_proxy` is a different case:
    # `getproxies_environment` files it under the key `all`, `ProxyHandler` mints `all_open` from it,
    # and nothing ever dispatches to that name — URL
    # dispatch is `<scheme>_open`, i.e. `http`/`https` — so it is read and never CONSULTED (measured:
    # `ALL_PROXY` set → direct, stub recorded the request). It is stripped because a reader expects it
    # to matter and the strip is free, NOT because it covers any base.
    for key in [k for k in env if k.lower() in ("http_proxy", "https_proxy", "all_proxy")]:
        env.pop(key, None)
    env["HOME"] = "/tmp/gh-preflight-no-home"          # keeps hosts.yml out of the picture
    env["GH_WRITE_PREFLIGHT_API"] = stub_base
    if token is not None:
        env["GH_TOKEN"] = token
    env.update(overrides)
    return env


def _run(stub_base: str, token: str | None = "ghp_dummy") -> tuple[int, str, str]:
    """Run the tool as its own process — the real CLI contract, including the exit code."""
    p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                       capture_output=True, text=True, env=_env(stub_base, token), timeout=120)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


# ── THE RED TEST — the verbatim #10930 shape ────────────────────────────────────────────────────
def test_issues_writable_but_pull_requests_refused_is_the_bug():
    """THE RED CASE: `Issues: write` present, `Pull requests: write` missing → must REFUSE.

    This is #10930 exactly: the token comments on an issue and is refused on a PR, so the landing
    lane's verdict comment (the rail's `--evidence`) never gets posted.
    """
    with _Stub(issues=OK_ISSUES, pulls=READONLY_403) as s:
        rc, out, _ = _run(s.base, token="github_pat_dummy_finegrained")
    assert rc != 0, f"a token that cannot write to PRs PASSED the preflight — that is the whole bug: {out}"
    assert "GH-WRITE-UNAVAILABLE(pull-requests-write-missing)" in out, out


def test_issues_and_pull_requests_writable_but_contents_refused_is_also_the_bug():
    """THE SECOND RED CASE — review round 6, and it was a live FALSE PASS.

    Merging a PR and creating a ref are charged to `contents: write`, NOT `pull_requests: write`.
    MEASURED 2026-11-04 on a live box: `PUT /pulls/{n}/merge` and `POST /git/refs` both answer
    `x-accepted-github-permissions: contents=write`, and a token with `Contents: read` was refused
    by `git push` outright. So a fine-grained PAT minted `Issues: write` + `Pull requests: write` +
    `Contents: read` — an ordinary grant, recorded in #10930 in the wild as
    `X-Accepted-Github-Permissions: contents=read` — passed the two-surface version of this tool and
    then could not publish its branch at all. The tool must refuse it.
    """
    with _Stub(issues=OK_ISSUES, pulls=OK_PULLS, contents=READONLY_403) as s:
        rc, out, _ = _run(s.base, token="github_pat_dummy_finegrained")
    assert rc != 0, f"a token that cannot push a branch PASSED the preflight — that is a false pass: {out}"
    assert "GH-WRITE-UNAVAILABLE(contents-write-missing)" in out, out


def test_contents_is_probed_as_a_write_surface():
    """The contents probe must hit POST /git/refs — the same permission the merge is charged to."""
    with _Stub() as s:
        rc, _, _ = _run(s.base)
        seen = list(s.seen)
    assert rc == 0
    refs = [body for m, p, body in seen if m == "POST" and p.endswith("/git/refs")]
    assert refs, f"no contents (git/refs) write probe ran: {[p for m, p, _ in seen]}"
    payload = json.loads(refs[0])
    assert payload["ref"].startswith("refs/heads/gh-write-preflight-"), payload
    assert set(payload["sha"]) == {"0"}, f"the sha must be unresolvable so no ref is created: {payload}"


def test_pull_requests_writable_but_issues_refused_is_named_too():
    """A landing also CLOSES issues, so a PR-only token is refused, with the opposite name."""
    with _Stub(issues=READONLY_403, pulls=OK_PULLS) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "GH-WRITE-UNAVAILABLE(issues-write-missing)" in out, out


def test_all_three_surfaces_refused_is_token_read_only():
    with _Stub(issues=READONLY_403, pulls=READONLY_403, contents=READONLY_403) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "token-read-only" in out, out


def test_two_refused_of_three_names_the_earliest_blocker_and_hides_nothing():
    """`token-read-only` means ALL THREE. Two refused must name the one that blocks the lane soonest,
    and the message must still disclose the other — collapsing it into `token-read-only` would state
    something false (one surface IS writable)."""
    with _Stub(issues=OK_ISSUES, pulls=READONLY_403, contents=READONLY_403) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "token-read-only" not in out, out
    assert "contents-write-missing" in out, out          # earliest in landing order
    assert "pull_requests" in out, f"the also-refused surface must be disclosed: {out}"


# ── the trap that made the first version blind: assert the PR surface is really probed ──────────
def test_probes_the_pull_requests_surface_not_only_issues():
    """The probe set MUST include a pull-requests write, not just an issues one.

    The first version of this tool deleted a repo LABEL — an *issues* resource. It passes a token
    with `Issues: write` and no `Pull requests: write`, i.e. it cannot see #10930. This test fails if
    anyone narrows the probe back to issues-only.
    """
    with _Stub() as s:
        rc, _, _ = _run(s.base)
        seen = list(s.seen)
    assert rc == 0
    posts = [(m, p, b) for m, p, b in seen if m == "POST"]
    paths = [p for _, p, _ in posts]
    assert any(p.endswith("/pulls") for p in paths), f"no pull-requests write probe: {paths}"
    assert any(p.endswith("/issues") for p in paths), f"no issues write probe: {paths}"
    assert any(p.endswith("/git/refs") for p in paths), f"no contents write probe: {paths}"
    # …and it must NOT be a mutating label delete (the old, blind shape).
    assert not any(m == "DELETE" for m, _, _ in seen), f"a DELETE probe reappeared: {seen}"


def test_write_probes_cannot_create_anything():
    """Zero side effect BY CONSTRUCTION: the payloads must be invalid, so nothing is created.

    * issues probe → an EMPTY body (an issue needs a title, so validation must reject it)
    * pulls  probe → a head ref that cannot exist, under `refs/heads/gh-write-preflight-<hex>`
    * refs   probe → an UNRESOLVABLE sha, so the ref itself cannot be created
    """
    with _Stub() as s:
        rc, _, _ = _run(s.base)
        seen = list(s.seen)
    assert rc == 0
    issues_body = pulls_body = refs_body = None
    for method, path, body in seen:
        if method != "POST":
            continue
        if path.endswith("/issues"):
            issues_body = body
        elif path.endswith("/pulls"):
            pulls_body = body
        elif path.endswith("/git/refs"):
            refs_body = body
    assert issues_body in ("", "{}"), f"the issues probe sent a body that could CREATE an issue: {issues_body!r}"
    assert pulls_body, "no pulls probe body recorded"
    payload = json.loads(pulls_body)
    assert payload["head"].startswith("refs/heads/gh-write-preflight-"), payload
    assert payload["base"] == "main", payload
    assert refs_body, "no contents (git/refs) probe body recorded"
    assert set(json.loads(refs_body)["sha"]) == {"0"}, refs_body


# ── classification: a 403 is either a permission or a rate limit, and they must not be confused ─
def test_rate_limit_is_not_reported_as_a_permission_defect():
    """A 403 that is a rate limit must NOT be blamed on the token (different fix entirely)."""
    with _Stub(pulls=(403, {}, '{"message":"API rate limit exceeded for user ID 81560491"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "rate-limited" in out, out
    assert "pull-requests-write-missing" not in out, out


def test_exhausted_quota_header_counts_as_rate_limited():
    with _Stub(pulls=(403, {"X-RateLimit-Remaining": "0"}, '{"message":"Forbidden"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "rate-limited" in out, out


def test_429_is_rate_limited_not_a_permission_defect():
    """HTTP 429 must NOT be reported as a missing PR permission (review round-1 defect).

    Every status other than 401/403/404 used to fall through to the generic branch, so a 429 on the
    pulls surface was reported as `pull-requests-write-missing` — sending the operator to re-mint a
    perfectly good token, the exact misdirection the tool exists to prevent.
    """
    with _Stub(pulls=(429, {"Retry-After": "30"},
                      '{"message":"You have exceeded a secondary rate limit"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "rate-limited" in out, out
    assert "pull-requests-write-missing" not in out, out


def test_read_429_is_rate_limited():
    with _Stub(read=(429, {}, '{"message":"API rate limit exceeded"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "rate-limited" in out, out


def test_server_error_is_not_a_permission_verdict():
    """A 5xx is GitHub's fault; it must not be charged to the token as a missing permission."""
    with _Stub(issues=(503, {}, '{"message":"Service Unavailable"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "server-error" in out, out
    assert "issues-write-missing" not in out, out


def test_unexpected_4xx_is_not_a_permission_verdict():
    """A 410 (Issues disabled), 400 (proxy/WAF) or 451 must NOT be reported as a missing permission.

    Review round-2 defect: every status that was not 401/403/404/429/5xx fell to `forbidden`, and the
    single-surface verdict then PROMOTED any non-ok probe to `pull-requests-write-missing` /
    `issues-write-missing`. The verdict now only names a permission for a `not-permitted` probe, so an
    unknown status fails closed (reports itself) instead of misdirecting a token re-mint.
    """
    for code in (400, 410, 418, 451):
        with _Stub(issues=(code, {}, '{"message":"Gone"}'), pulls=OK_PULLS) as s:
            rc, out, _ = _run(s.base)
        assert rc != 0, (code, out)
        assert "issues-write-missing" not in out, (code, out)
        assert f"HTTP {code}" in out or "forbidden" in out, (code, out)
    for code in (400, 410):
        with _Stub(issues=OK_ISSUES, pulls=(code, {}, '{"message":"Gone"}')) as s:
            rc, out, _ = _run(s.base)
        assert rc != 0
        assert "pull-requests-write-missing" not in out, (code, out)


def test_redirect_is_not_a_pass():
    """A 3xx must never become a FALSE PASS (review round-3 defect, category A).

    urllib's default opener FOLLOWS redirects and stdlib `HTTPRedirectHandler.redirect_request`
    DOWNGRADES `POST` to `GET` on 301/302/303. A stub that 301s the write probes and 200s the
    resulting collection GETs made this tool print `GH-WRITE-OK` and exit 0 while NO write was ever
    answered — a broken token would then have lead the lane into a merge whose closures it cannot post.
    """
    stub = _Stub()
    stub.redirect_writes = True
    with stub as s:
        rc, out, _ = _run(s.base)
    assert rc != 0, f"a redirecting endpoint produced a PASS: {out}"
    assert "GH-WRITE-OK" not in out, out
    # …and the write must NOT have been re-issued as a downgraded GET that returned 200.
    assert not any(m == "GET" and p.endswith("/issues") for m, p, _ in s.seen), s.seen


def test_2xx_on_a_write_probe_is_not_a_pass():
    """200 on an empty-body issue POST means something accepted it — not that permission was proven."""
    with _Stub(issues=(200, {}, '{"number":1}'), pulls=(201, {}, '{"number":2}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0, f"a 2xx on a must-be-invalid payload was read as a PASS: {out}"
    assert "GH-WRITE-OK" not in out, out


def test_read_403_is_a_read_problem_not_a_write_verdict():
    """A refused READ must not emit an undocumented reason carrying write-shaped advice."""
    with _Stub(read=(403, {}, '{"message":"Resource not accessible by personal access token"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "repo-not-visible" in out, out
    assert "lacks write" not in out, out


def test_gh_config_dir_is_honoured_over_xdg():
    """gh's OWN order is GH_CONFIG_DIR, then XDG_CONFIG_HOME, then ~/.config.

    Omitting the GH_CONFIG_DIR leg made the tool read a different store from the one `gh` uses
    (review round-3 defect), so its verdict could be about a credential the lane never uses.

    ⛔ THE TOKEN LIVES ONLY IN GH_CONFIG_DIR, DELIBERATELY. A first version seeded BOTH stores, which
    made the test VACUOUS for the defect it names: a mutant with the GH_CONFIG_DIR leg deleted fell
    through to the XDG store, found its token, and still printed GH-WRITE-OK (measured — the reviewer
    ran exactly that mutant). With the token only in GH_CONFIG_DIR, deleting the leg yields
    `no-credential`, so the test goes red on the regression it exists for.
    """
    with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as xdg:
        os.makedirs(os.path.join(xdg, "gh"))
        # The XDG store exists but holds NO token: it is the decoy, not a fallback.
        with open(os.path.join(xdg, "gh", "hosts.yml"), "w", encoding="utf-8") as fh:
            fh.write("github.com:\n    user: someone\n")
        with open(os.path.join(cfg, "hosts.yml"), "w", encoding="utf-8") as fh:
            fh.write("github.com:\n    oauth_token: ghp_from_gh_config_dir\n")
        with _Stub() as s:
            env = _env(s.base, None)
            env["XDG_CONFIG_HOME"] = xdg
            env["GH_CONFIG_DIR"] = cfg
            p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                               capture_output=True, text=True, env=env, timeout=120)
        # ⛔ THE SOURCE LABEL IS ASSERTED ON A REFUSED RUN, BECAUSE THAT IS THE ONLY PATH THAT PRINTS
        # IT. `main()` emits the `credential:` line inside `if not res["ok"]:` only, so on the success
        # path above stderr is ALWAYS empty — the previous assertion ended `or p.stderr == ""`, a
        # disjunct that is true pass or fail and therefore proved nothing about which store answered
        # (review round 9). Same store, a stub that REFUSES the token: the run fails, and the label
        # must name `hosts.yml` — the config-dir store — never `env:` (both env legs are unset by
        # `_env`) and never a bare `none`.
        with _Stub(reject_tokens={"ghp_from_gh_config_dir"}) as refused_stub:
            env = _env(refused_stub.base, None)
            env["XDG_CONFIG_HOME"] = xdg
            env["GH_CONFIG_DIR"] = cfg
            refused = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                                     capture_output=True, text=True, env=env, timeout=120)
    # The ONLY store with a token is GH_CONFIG_DIR, so a pass PROVES that leg was read; ignoring it
    # would find no credential at all and answer `no-credential`.
    assert p.returncode == 0, p.stdout + p.stderr
    assert "GH-WRITE-OK" in p.stdout, p.stdout
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "credential: hosts.yml:" in refused.stderr, refused.stderr
    assert "credential: env:" not in refused.stderr, refused.stderr


def test_exhausted_quota_is_caught_before_any_write_probe():
    """Quota already 0 on the READ → refuse without spending either write probe."""
    with _Stub(read=(200, {"X-RateLimit-Remaining": "0"}, "{}")) as s:
        rc, out, _ = _run(s.base)
        methods = [m for m, _, _ in s.seen]
    assert rc != 0
    assert "rate-limited" in out, out
    assert "POST" not in methods, f"spent a write probe on an exhausted account: {methods}"


def test_sso_refusal_is_named():
    with _Stub(pulls=(403, {}, '{"message":"Resource protected by organization SAML enforcement"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "sso-unauthorized" in out, out


def test_write_probe_401_is_bad_credentials():
    """The write-probe 401 branch must be pinned (review round-4 coverage gap: a mutant deleting it
    survived the whole suite — live behaviour was right, but nothing asserted it)."""
    with _Stub(issues=(401, {}, '{"message":"Bad credentials"}'), pulls=OK_PULLS) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "bad-credentials" in out, out
    assert "issues-write-missing" not in out, out


def test_bad_credentials():
    with _Stub(read=(401, {}, '{"message":"Bad credentials"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "bad-credentials" in out, out


def test_repo_not_visible_is_not_a_write_verdict():
    with _Stub(read=(404, {}, '{"message":"Not Found"}')) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "repo-not-visible" in out, out


def test_no_credential_at_all():
    with _Stub() as s:
        rc, out, _ = _run(s.base, token=None)
    assert rc != 0
    assert "no-credential" in out, out


def test_no_credential_is_hermetic_against_xdg_config_home():
    """`_env` must neutralise XDG_CONFIG_HOME, or this test depends on the HOST's gh config.

    The tool resolves hosts.yml via XDG_CONFIG_HOME FIRST, so a host that happens to have one there
    made `test_no_credential_at_all` fail (review round-1 defect). The property under test: with the
    HOST environment carrying an XDG gh config, `_env` must still produce a no-credential run.
    """
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "gh"))
        with open(os.path.join(td, "gh", "hosts.yml"), "w", encoding="utf-8") as fh:
            fh.write("github.com:\n    oauth_token: ghp_from_xdg\n    user: someone\n")
        previous = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = td          # the HOST carries one…
        try:
            with _Stub() as s:
                env = _env(s.base, None)             # …`_env` must strip it
                assert "XDG_CONFIG_HOME" not in env
                p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                                   capture_output=True, text=True, env=env, timeout=120)
        finally:
            if previous is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = previous
    assert p.returncode != 0
    assert "no-credential" in p.stdout, p.stdout


# ── the GREEN case, the usage guard, and credential hygiene ─────────────────────────────────────
def test_fully_writable_token_passes():
    """Both surfaces authorised → GH-WRITE-OK, and the line NAMES the endpoint it measured."""
    with _Stub() as s:
        rc, out, _ = _run(s.base, token="gho_dummy_writecapable")
    assert rc == 0, f"a fully write-capable token was refused: {out}"
    assert "GH-WRITE-OK" in out, out
    # ⛔ review round 7: a pass measured against something that is not GitHub is not a pass, so the
    # verdict must say WHAT it is a verdict about — in the human line, not only under --json.
    assert s.base in out, f"the OK line does not name the endpoint that answered: {out}"


def test_the_child_env_carries_no_proxy():
    """A host proxy would send the tool's probe where no stub can see it (review F1 at `a9007b139`).

    `_request` opens through `build_opener()`, which reads `http_proxy` (any case) from the environment
    — and NOT `all_proxy`, which `OpenerDirector` never consults (measured). MEASURED: with
    `http_proxy` exported, the probe-everything mutant left `seen == []` — the arm that is supposed to
    catch it went inert instead of red, silently. This file promises a hermetic stub, so the child
    must not inherit a route off the box.
    """
    planted = {"http_proxy": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
               "all_proxy": "http://127.0.0.1:9"}
    try:
        os.environ.update(planted)
        env = _env("http://127.0.0.1:9", "ghp_dummy")
        # …and through the OVERRIDES leg, which is how the hosts.yml tests plant `GH_CONFIG_DIR`: an
        # override applied after the strip must not resurrect a proxy (review finding F2 at
        # `7d7ce3d57` — three tests hand-built their own env and escaped every rule in `_env`).
        env_override = _env("http://127.0.0.1:9", None, GH_CONFIG_DIR="/tmp/gh-preflight-ghdir")
    finally:
        for key in planted:
            os.environ.pop(key, None)
    leaked = [k for k in env if k.lower() in ("http_proxy", "https_proxy", "all_proxy")]
    assert leaked == [], f"the child inherits a proxy, so its probes can bypass the stub: {leaked}"
    assert env["GH_WRITE_PREFLIGHT_API"] == "http://127.0.0.1:9", env
    assert env_override["GH_CONFIG_DIR"] == "/tmp/gh-preflight-ghdir", env_override
    assert not [k for k in env_override if k.lower() in ("http_proxy", "https_proxy", "all_proxy")], \
        env_override

    # ⛔ ONE CONSTRUCTION SITE — enforced on the AST, not on a list of spellings (review F1 at
    # `231bae2d7`). The strip lives in `_env`, so a test that assembles its own child environment from
    # the host env opts out of it silently; the review found this twice (four `os.environ`+`.items()`
    # comprehensions, then an `os.environ` spread behind them) and then showed the spelling list missed
    # `dict(os.environ)`, `{** os.environ}`, `os.environ | {…}` and an omitted `env=`. `_child_env_
    # violations` asks the question structurally, so a new spelling cannot sneak past it.
    violations = _child_env_violations(Path(__file__))
    assert violations == [], (
        "a child environment is not built through `_env(…, **overrides)`, so it silently opts out of "
        f"the proxy/config-dir/token rules: {violations}")


def _child_env_violations(path: Path) -> list[str]:
    """Every `subprocess` call in this file must pass `env=` derived from `_env(…)` (review F1/F2).

    A SPELLING LIST IS NOT A PIN, and neither is a FILE-GLOBAL NAME MAP. Two measured rounds of
    holes got it here: counting spellings missed `dict(os.environ)`, `{** os.environ}`,
    `os.environ | {…}` and an omitted `env=`; then a file-global "every assignment of this name is a
    `_env(…)` call" map missed `def f(env): … env=env` (a parameter), `for env in [{**os.environ}]`,
    `(env,) = (dict(os.environ),)`, `if (env := dict(os.environ))`, `with cfg as env` — because ~30
    innocent `env = _env(…)` records elsewhere in the file vouched for the name — and it named 29
    innocent lines instead of the one offending site.

    So: per SCOPE binding facts, and derivation as a RECURSIVE predicate over the expression.
      - `_env(…)` is derived. A name is derived when the nearest scope binds it (only) to derived
        values — a scope that declares it unknown (parameter, `for`/`with`/`except`/walrus target,
        import) makes it NOT derived, which fails closed. A composite expression (a dict spread, a
        `dict(...)`/`vars(...)` wrapper) is derived when any of its sub-expressions is, so
        `{**_env(…), "K": "v"}` passes while `{**os.environ}` does not.
      - `_env`'s own body is graded like any other scope rather than skipped: its local `env` is the
        host-env comprehension, so a `subprocess` call hidden inside the builder is flagged instead of
        being vouched for by the builder itself.
    Each violation names the SUBPROCESS call's line — and, for a name, the lines that bound it.
    """
    import ast

    def target_names(target: ast.AST) -> set[str]:
        # ⛔ NEVER `ast.walk` HERE: a `env["K"] = v` target is a Subscript whose walk INCLUDES the name
        # `env`, which would mark the very binding the site just made as unknown (measured: 13 false
        # positives, all of them `env = _env(…)` followed by `env["…"] = …`). Only a binding NAME — or an
        # unpacking of them — is a target that shadows.
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, ast.Starred):
            return target_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            names: set[str] = set()
            for element in target.elts:
                names |= target_names(element)
            return names
        return set()

    class Checker(ast.NodeVisitor):
        """One ordered pass: bindings are recorded before the calls that read them (they precede)."""

        def __init__(self) -> None:
            self.scopes: list[dict[str, list[ast.expr] | None]] = [{}]
            self.violations: list[str] = []

        # ── scope facts ──────────────────────────────────────────────────────────────────────
        def _unknown(self, names: set[str]) -> None:
            for name in names:
                self.scopes[-1][name] = None

        def _bind(self, name: str, value: ast.expr) -> None:
            scope = self.scopes[-1]
            if scope.get(name) is None and name in scope:      # already unknown: stays unknown
                return
            scope.setdefault(name, []).append(value)

        def _lookup(self, name: str) -> tuple[bool, list[ast.expr] | None]:
            for scope in reversed(self.scopes):
                if name in scope:
                    return True, scope[name]
            return False, None

        def _derived(self, expr: ast.expr, seen: frozenset[str] = frozenset()) -> bool:
            if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and expr.func.id == "_env":
                return True
            if isinstance(expr, ast.Name):
                found, bindings = self._lookup(expr.id)
                if not found or not bindings or expr.id in seen:
                    return False
                return all(self._derived(value, seen | {expr.id}) for value in bindings)
            return any(self._derived(child, seen) for child in ast.iter_child_nodes(expr)
                       if isinstance(child, ast.expr))

        # ── declarations that make a name unknown (fail closed) ─────────────────────────────
        def _push_function(self, node) -> None:
            args = node.args
            params = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
            for extra in (args.vararg, args.kwarg):
                if extra is not None:
                    params.add(extra.arg)
            for default in (*args.defaults, *args.kw_defaults):
                if default is not None:
                    self.visit(default)                        # evaluated in the ENCLOSING scope
            self.scopes.append({name: None for name in params})
            for stmt in node.body:
                self.visit(stmt)
            self.scopes.pop()

        visit_FunctionDef = _push_function
        visit_AsyncFunctionDef = _push_function

        def visit_Lambda(self, node: ast.Lambda) -> None:
            # visit in place: lambdas here are `key=` arguments, never a child-env source
            for child in ast.iter_child_nodes(node):
                self.visit(child)

        def _push_comprehension(self, node) -> None:
            self.scopes.append({})
            for gen in node.generators:
                self.visit(gen.iter)
                self._unknown(target_names(gen.target))
                for cond in gen.ifs:
                    self.visit(cond)
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
            self.scopes.pop()

        visit_ListComp = _push_comprehension
        visit_SetComp = _push_comprehension
        visit_DictComp = _push_comprehension
        visit_GeneratorExp = _push_comprehension

        def visit_For(self, node: ast.For) -> None:
            self.generic_visit(node)
            self._unknown(target_names(node.target))

        visit_AsyncFor = visit_For

        def visit_With(self, node: ast.With) -> None:
            self.generic_visit(node)
            for item in node.items:
                if item.optional_vars is not None:
                    self._unknown(target_names(item.optional_vars))

        visit_AsyncWith = visit_With

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            self.generic_visit(node)
            if node.name:
                self._unknown({node.name})

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
            self.generic_visit(node)
            self._unknown(target_names(node.target))

        def visit_Import(self, node: ast.Import) -> None:
            self._unknown({(a.asname or a.name.split(".")[0]) for a in node.names})

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            self._unknown({(a.asname or a.name) for a in node.names})

        # ── bindings and the check itself ───────────────────────────────────────────────────
        def visit_Assign(self, node: ast.Assign) -> None:
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self._bind(node.targets[0].id, node.value)
            else:
                for target in node.targets:                    # unpacking: origin unknown
                    self._unknown(target_names(target))
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            if isinstance(node.target, ast.Name) and node.value is not None:
                self._bind(node.target.id, node.value)
            elif isinstance(node.target, ast.Name):
                self._unknown({node.target.id})
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            is_subprocess = (isinstance(node.func, ast.Attribute)
                             and isinstance(node.func.value, ast.Name)
                             and node.func.value.id == "subprocess")
            if is_subprocess:
                env_kw = next((kw for kw in node.keywords if kw.arg == "env"), None)
                if env_kw is None:
                    self.violations.append(
                        f"line {node.lineno}: no `env=` — the child inherits the host environment")
                elif not self._derived(env_kw.value):
                    detail = ""
                    if isinstance(env_kw.value, ast.Name):
                        found, bindings = self._lookup(env_kw.value.id)
                        where = ("unknown origin" if not found or not bindings
                                 else "line(s) " + ", ".join(str(b.lineno) for b in bindings))
                        detail = f" (`{env_kw.value.id}`: {where})"
                    self.violations.append(
                        f"line {node.lineno}: `env=` is not derived from `_env(…)`{detail}")
            self.generic_visit(node)

    checker = Checker()
    checker.visit(ast.parse(path.read_text(encoding="utf-8")))
    return checker.violations


def test_non_loopback_api_base_is_refused_before_anything_is_contacted():
    """The blocking round-7 defect: an env-overridable base made `GH-WRITE-OK` a verdict about
    something that is not GitHub.

    MEASURED on the pre-fix head with the suite's own stub as the "API" and a token that is not a
    credential at all: `GH_WRITE_PREFLIGHT_API=http://127.0.0.1:40933`, token
    `ghp_NOT_A_REAL_CREDENTIAL` → rc 0, `GH-WRITE-OK(issues+pull-requests+contents writable)`, and no
    output naming the base. Here the base is a host that is neither GitHub nor loopback, so the ONLY
    acceptable outcome is a refusal that happens before any request: reason `usage` — the reason ONLY
    `_validate_api_base` produces — with no OK line, no `transport` verdict and no traceback.

    Pre-fix, this same invocation reached the network and reported `GH-WRITE-UNAVAILABLE(transport)`,
    i.e. it both sent the credential nobody had vetted and blamed the token's environment for it.

    ⛔ NON-VACUITY, and why there is no `seen == []` on the HOST-NAME clause below (review finding
    F1). `_run(base, …)` is the only thing that points the tool at an endpoint, so a `seen == []`
    assertion taken while the tool is pointed at `api.github.example.com`, against a local `_Stub()` it
    never learns about, would be true for EVERY implementation — including one that probes first and
    refuses afterwards. That is an inert assertion cited as evidence, the class this branch already
    paid a cycle for. So the host-name clause pins the refusal through the `usage` reason instead, and
    the REACHABLE clause repeats it against `0.0.0.0:<stub port>`: a base the tool must refuse
    (`ipaddress` does not call `0.0.0.0` loopback) that nonetheless routes to the bound listener, so a
    probe sent before validation IS recorded and reddens here. Measured: a `0.0.0.0:<port>` request
    reaches an `HTTPServer` bound to `127.0.0.1:<port>`.

    Both clauses discriminate, by DIFFERENT mechanisms — measured, and named per clause because one
    sentence covering both was wrong: stub `_validate_api_base` out of `preflight()` and the HOST-NAME
    clause answers `transport` (it reaches for a name that does not resolve), while the REACHABLE
    clause answers `GH-WRITE-OK` with `rc 0` (the stub authorises all three writes) and records four
    requests. Either way the assertion that reds names the defect.
    """
    rc, out, err = _run("http://api.github.example.com", token="ghp_NOT_A_REAL_CREDENTIAL")
    assert rc != 0, f"an unvetted endpoint produced a PASS: {out}"
    assert "GH-WRITE-UNAVAILABLE(usage)" in out, out
    assert "GH-WRITE-OK" not in out, out
    assert "api.github.example.com" in out, out
    # ⛔ `transport` is exactly what a tool WITHOUT the base check answers here — it reaches the
    # network and blames the path to it. Naming it is what makes this arm red on that pre-fix code.
    assert "transport" not in out, f"the base was contacted before it was validated: {out}"
    assert "Traceback" not in err, err

    # ⛔ THE REACHABLE CLAUSE: same refusal, and now `seen == []` is an OBSERVATION about the order of
    # validation and contact rather than a tautology.
    with _Stub() as s:
        # ⛔ PROVE THE PREMISE BEFORE ASSERTING AN EMPTINESS (review finding F1 at `a9007b139`). `seen ==
        # []` discriminates only if a request to THIS base would have been recorded: a host that does
        # not route `0.0.0.0` (Windows rejects it as a destination) would leave the assertion true
        # while measuring nothing. A raw TCP connect uses no HTTP machinery and no proxy, so it asks
        # exactly the question the clause depends on.
        try:
            with socket.create_connection(("0.0.0.0", s.port), timeout=10):
                pass
        except OSError as exc:
            raise AssertionError(
                f"this clause cannot measure anything on this host: TCP to 0.0.0.0:{s.port} failed "
                f"({type(exc).__name__}) — a probe sent before validation would not have been recorded "
                f"either, so `seen == []` would be a tautology here") from exc
        rc2, out2, _r = _run(f"http://0.0.0.0:{s.port}", token="ghp_NOT_A_REAL_CREDENTIAL")
        seen = list(s.seen)
    assert rc2 != 0, f"an unvetted endpoint produced a PASS: {out2}"
    assert "GH-WRITE-UNAVAILABLE(usage)" in out2, out2
    assert "GH-WRITE-OK" not in out2, out2
    assert "transport" not in out2, f"the base was contacted before it was validated: {out2}"
    assert seen == [], f"the credential was sent to an unvalidated base: {seen}"


def test_api_base_carrying_credentials_is_refused_and_never_echoed():
    """`user:pass@` in the base is (a) refused and (b) NOT reprinted in the refusal (review round 7).

    Reachability is the point: this base names the LIVE stub, so a tool without the check would run
    the whole probe sequence against it and print `GH-WRITE-OK` for `ghp_NOT_A_REAL_CREDENTIAL` — the
    stub records every request, so `seen == []` is the direct evidence that the refusal happened
    before the credential left the process.
    """
    with _Stub() as s:
        rc, out, err = _run(f"http://user:hunter2@{s.base.split('//', 1)[1]}",
                            token="ghp_NOT_A_REAL_CREDENTIAL")
        seen = list(s.seen)
    assert rc != 0, f"a credential-bearing base produced a PASS: {out}"
    assert "GH-WRITE-UNAVAILABLE(usage)" in out, out
    assert "GH-WRITE-OK" not in out, out
    assert seen == [], f"the credential was sent to a base that must have been refused first: {seen}"
    assert "hunter2" not in out and "hunter2" not in err, (out, err)


def test_json_verdict_redacts_a_credentialed_base():
    """`--json` must not print the `user:pass@` it just refused — the artifact IS the leak.

    MEASURED on the round-7 fix (head `e42a5376e`): `--api-base http://user:hunter2@127.0.0.1:9 --json`
    exited 1 with `reason: usage` — and serialised `"api_base": "http://user:hunter2@127.0.0.1:9"` into
    stdout. The refusal branch deliberately does not echo the base, then the result dict carried it
    verbatim, so the secret reached the machine-readable verdict — the one a lane pastes into a PR, an
    issue or a `record-review` comment (Review Scope Contract `R3`: the credential is never printed,
    logged, or placed in output).
    """
    with _Stub() as s:
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise", "--json",
                            "--api-base", f"http://user:hunter2@{s.base.split('//', 1)[1]}"],
                           capture_output=True, text=True,
                           env=_env(s.base, "ghp_NOT_A_REAL_CREDENTIAL"), timeout=120)
        seen = list(s.seen)
    assert p.returncode != 0, p.stdout
    assert "hunter2" not in p.stdout + p.stderr, (
        f"the refused base's credential was serialised into the verdict: {p.stdout}")
    res = json.loads(p.stdout)
    assert res["reason"] == "usage", res
    # …still attributable: the host survives the redaction, so a consumer can see what was refused.
    assert res["api_base"].endswith(s.base.split("//", 1)[1]), res
    assert "@" not in res["api_base"], res
    assert seen == [], f"the credential was sent before the base was refused: {seen}"


def test_api_base_refusals_never_echo_a_credential_in_any_shape():
    """EVERY refusal branch of `_validate_api_base` must redact userinfo — not just the one that names it.

    MEASURED at head `76a21dfd8` (the defect this arm exists for): round 8 redacted the `api_base` KEY,
    but the userinfo guard is the FOURTH check, so the query/fragment guard, the scheme/host guard and
    the port guard all echoed the raw value into `detail` — which is printed on the
    `GH-WRITE-UNAVAILABLE(...)` line and serialised by `--json`:

        --api-base 'http://user:hunter2@127.0.0.1:9?x=1'  → the secret, in stdout
        --api-base 'user:hunter2@host'                    → `urlsplit` sees scheme='user', not userinfo

    Each row asserts the refusal BOTH refuses and does not carry the password, so a future check added
    ahead of the userinfo guard cannot silently reopen the class.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gh_write_preflight", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    leaking_shapes = [
        "http://user:hunter2@127.0.0.1:9?x=1",        # query guard fires BEFORE the userinfo guard
        "http://user:hunter2@127.0.0.1:9#f",          # fragment guard, same ordering
        "ftp://user:hunter2@127.0.0.1:9/x",           # scheme guard
        "user:hunter2@host",                          # schemeless: urlsplit cannot see the userinfo
        "http://user:hunter2@127.0.0.1:notaport",     # port guard
        "http://user:hunter2@",                       # no host
    ]
    for base in leaking_shapes:
        msg = mod._validate_api_base(base)
        assert msg != "", f"accepted a base it must refuse: {base!r}"
        assert "hunter2" not in msg, f"the refusal echoed the credential: {msg!r}"
    # …and the redaction must not be achieved by echoing NOTHING: the host still has to be attributable
    # on the shapes where one exists, or a lane cannot tell which endpoint was refused.
    assert "host" in mod._validate_api_base("user:hunter2@host")
    assert "127.0.0.1" in mod._validate_api_base("http://user:hunter2@127.0.0.1:9?x=1")
    # ⛔ HOST ONLY, EVEN FOR AN UNCREDENTIALED BASE (review cycle 19). This line used to assert the WHOLE
    # base was echoed — and that is exactly how a credential in the query, the fragment or the `@`-tail
    # reached output when nothing resolved to scrub it by value. The endpoint's identity is the host.
    _msg = mod._validate_api_base("http://127.0.0.1:9?x=1")
    assert "127.0.0.1" in _msg and "?x=1" not in _msg, _msg


def test_json_verdict_never_echoes_a_credentialed_base_bearing_a_query():
    """The union shape the three existing arms all missed: credentials AND a query (review round 9).

    `test_api_base_carrying_credentials_is_refused_and_never_echoed` uses a credentialed base with NO
    query, and `test_json_verdict_redacts_a_credentialed_base` likewise — both take the one branch that
    redacts. `test_api_base_validation_table` lists the query-bearing and the credentialed shapes as
    SEPARATE rows and only asserts `!= ""`, so the combination — the leaking one — was untested.
    """
    with _Stub() as s:
        credentialed = s.base.replace("http://", "http://user:hunter2@", 1) + "?x=1"
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise", "--json",
                            "--api-base", credentialed],
                           capture_output=True, text=True,
                           env=_env(s.base, "ghp_NOT_A_REAL_CREDENTIAL"), timeout=120)
        seen = list(s.seen)
    assert p.returncode != 0, p.stdout
    assert "hunter2" not in p.stdout + p.stderr, (
        f"the refusal echoed the credential into the verdict artifact: {p.stdout}")
    res = json.loads(p.stdout)
    assert res["reason"] == "usage", res
    assert "hunter2" not in json.dumps(res), res
    assert seen == [], f"the credential was sent before the base was refused: {seen}"


def test_api_base_validation_table():
    """The accepted/refused table, pinned at the unit level so a weakened rule reddens here first.

    Deliberately includes the near-misses a prefix/string test would wave through: a lookalike host,
    a plain-`http` GitHub host, an empty host, a bare `?`/`#` (which parse to an EMPTY query/fragment),
    a real non-`.1` loopback, and a non-numeric port.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gh_write_preflight", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    accepted = [mod.DEFAULT_API, mod.DEFAULT_API + "/", "http://127.0.0.1:9", "http://127.0.0.2:9",
                "http://localhost:9", "http://[::1]:9", "https://127.0.0.1:9"]
    for base in accepted:
        assert mod._validate_api_base(base) == "", base

    refused = ["", "https://evil.example.com", "http://api.github.com", "https://api.github.com.evil.example",
               "http://127.0.0.1:9?x=1", "http://127.0.0.1:9#frag", "http://user:pw@127.0.0.1:9",
               "127.0.0.1:9", "http://127.0.0.1:notaport", "https://", "http://0.0.0.0:9"]
    for base in refused:
        assert mod._validate_api_base(base) != "", f"accepted a base it must refuse: {base!r}"


def test_nested_users_entry_does_not_lend_a_peer_token():
    """The depth-1 read must win inside a multi-account hosts.yml (#2138 r4 hazard).

    gh nests alternate logins under `users:` ABOVE the active account's own keys. A parser that takes
    the first `oauth_token` in the block returns a PEER's credential — and with the peer's token
    write-capable this produced a `GH-WRITE-OK` labelled `hosts.yml:active-user` for a lane whose
    `gh` uses the read-only active token (measured by review round 5 as a surviving mutant: a
    verifiable FALSE PASS, not just an unpinned branch).

    The stub ACCEPTS the peer token and REFUSES the active one, so a parser reading the peer gets a
    green preflight and this test fails — which is the discrimination the depth-1 guard needs.
    """
    with tempfile.TemporaryDirectory() as cfg:
        with open(os.path.join(cfg, "hosts.yml"), "w", encoding="utf-8") as fh:
            fh.write(
                "github.com:\n"
                "    users:\n"
                "        peer-user:\n"
                "            oauth_token: ghp_peer_writecapable\n"
                "    oauth_token: ghp_active_readonly\n"
                "    user: active-user\n"
            )
        with _Stub(reject_tokens={"ghp_active_readonly"}) as s:
            env = _env(s.base, None)
            env["GH_CONFIG_DIR"] = cfg
            p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                               capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode != 0, f"the peer's write-capable token produced a PASS: {p.stdout}"
    assert "token-read-only" in p.stdout, p.stdout
    # ⛔ THE LABEL IS NOW THE DEPTH-1 FACT, NOT THE FILE'S `user:` TEXT (review cycle 16): a shape
    # allowlist admits a 32-hex secret, so no value from the file is echoed. The security property this
    # test exists for is the PEER token, which is still refused above.
    assert "hosts.yml:active" in p.stderr, p.stderr
    assert "ghp_peer_writecapable" not in (p.stdout + p.stderr), p.stdout + p.stderr


def test_github_token_env_leg_is_honoured():
    """`GITHUB_TOKEN` alone (the standard GitHub Actions credential) must not answer no-credential."""
    with _Stub() as s:
        env = _env(s.base, None)
        env.pop("GH_TOKEN", None)
        env["GITHUB_TOKEN"] = "ghp_via_github_token"
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "GH-WRITE-OK" in p.stdout, p.stdout


def test_wrong_host_is_named():
    """A GH_HOST pointing at GHES must report `wrong-host`, not the misleading `no-credential`."""
    with _Stub() as s:
        env = _env(s.base, None)
        env["GH_TOKEN"] = "ghp_dummy"
        env["GH_HOST"] = "github.example.com"
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode != 0
    assert "wrong-host" in p.stdout, p.stdout


def test_malformed_response_is_a_transport_verdict_not_a_traceback():
    """`http.client.HTTPException` is NOT an `OSError` — a garbage status line must still produce
    `GH-WRITE-UNAVAILABLE(transport)` on STDOUT, not a traceback with empty stdout.

    Round-4 added the exception; round 5 found that deleting it survived the whole suite. Measured
    impact of the deletion: `rc=1` with EMPTY stdout, so a `--json` consumer gets `json.loads("")`
    instead of a verdict.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            try:
                conn.recv(4096)
                conn.sendall(b"GARBAGE NOT HTTP\r\n\r\n")
                conn.close()
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    try:
        env = _env(f"http://127.0.0.1:{port}", "ghp_dummy")
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    finally:
        srv.close()
    assert p.returncode != 0
    assert "GH-WRITE-UNAVAILABLE(transport)" in p.stdout, (p.stdout, p.stderr)
    assert "Traceback" not in p.stderr, p.stderr


def test_usage_requires_a_repo():
    # ⛔ THROUGH `_env`, NOT A HAND-BUILT DICT (review finding F1 at `a18251648`): an `os.environ`
    # SPREAD inherits the host's proxies AND the host's hosts.yml, so this arm's verdict depended on
    # the box — MEASURED with `http_proxy` planted, a child built that way never reached a stub at all.
    p = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True,
                       env=_env("http://127.0.0.1:9", "ghp_x", OR=""), timeout=60)
    assert p.returncode != 0
    assert "GH-WRITE-UNAVAILABLE(usage)" in p.stdout, p.stdout


def test_json_mode_reports_the_same_verdict_and_never_leaks_the_credential():
    secret = "ghs_dummy_secret_value_do_not_print"
    with _Stub(pulls=READONLY_403) as s:
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise", "--json"],
                           capture_output=True, text=True, env=_env(s.base, secret), timeout=120)
    assert p.returncode != 0
    res = json.loads(p.stdout)
    assert res["ok"] is False
    assert res["reason"] == "pull-requests-write-missing", res
    assert res["surfaces"] == {"issues": 422, "pull_requests": 403, "contents": 422}, res
    # ⛔ review round 7: the machine-readable verdict must carry the endpoint too, or a consumer
    # holding the artifact cannot tell a measurement of GitHub from a measurement of anything else.
    assert res["api_base"] == s.base, res
    assert secret not in p.stdout and secret not in p.stderr, "the credential leaked into the output"


# ── TOKEN HYGIENE — the guard must be PROVEN, not merely present (review cycle 2) ────────────────
# ⛔ BOTH TESTS BELOW EXIST BECAUSE THE GUARD SHIPPED UNPINNED: a mutation deleting the refusal block
# left 38/38 GREEN, so a regression would silently restore the token-printing traceback. Each asserts
# the three things that made the original defect invisible: a VERDICT (not a traceback), exit 1, and
# no fragment of the credential in stdout+stderr.
def test_wrapped_token_is_named_not_thrown():
    """A token with an interior newline must be REFUSED, never reach `putheader`.

    MEASURED before the guard: `http.client.putheader` raises
    `ValueError("Invalid header value b'Bearer <TOKEN>'")` — a message that CONTAINS the credential —
    and `_request` does not catch `ValueError`, so it escaped as a traceback on stderr with EMPTY
    stdout (breaking the `GH-WRITE-UNAVAILABLE(<reason>)` and `--json` contracts at once).
    """
    with _Stub() as s:            # a base that WOULD answer OK — the refusal must preempt it
        rc, out, err = _run(s.base, token="ghp_dummySECRET\ndumped")
    assert rc == 1, f"a header-unsafe token must exit 1, got {rc}: {out}{err}"
    assert out.startswith("GH-WRITE-UNAVAILABLE(bad-credentials)"), out
    blob = out + err
    assert "dumped" not in blob and "SECRET" not in blob, f"the credential reached the output: {blob}"
    assert "Traceback" not in err, err


def test_non_ascii_token_is_named_not_thrown():
    """A non-latin-1 token must be REFUSED too — `UnicodeEncodeError` is a `ValueError` subclass.

    Reachable from the tool's OWN reader: `_token_from_hosts` opens with `errors="replace"`, so a
    corrupt hosts.yml yields `U+FFFD`; `putheader` then raises `UnicodeEncodeError`, which the
    whitespace/control-character predicate alone does NOT catch.
    """
    with _Stub() as s:
        rc, out, err = _run(s.base, token="ghp_bad\ufffdbyte")
    assert rc == 1, f"a non-ASCII token must exit 1, got {rc}: {out}{err}"
    assert out.startswith("GH-WRITE-UNAVAILABLE(bad-credentials)"), out
    assert "Traceback" not in err, err


# ── FORGED-VERDICT AND CRASH INPUTS — the other two values that reach a request path (cycle 3) ───
# ⛔ AGENTS.md's HARD RULE TELLS A LANE TO READ THE `GH-WRITE-OK` LINE, so a value that can INJECT that
# line into stdout satisfies the gate with no probe: a newline anywhere in `api_base` did exactly that
# (measured: stdout began with the documented pass string while the process exited 1). These three tests
# pin the refusal at the input, and assert the fragment never appears in stdout.
def test_api_base_with_a_newline_cannot_forge_a_verdict_line():
    # ⛔ THE FIXTURE MUST NOT CONTAIN `@` (review cycle 4). The first version appended
    # `\nGH-WRITE-OK(… @ https://api.github.com)`, whose `@` made the USERINFO check refuse first — so
    # deleting the control-character guard left every assertion here satisfied (measured: the mutant
    # survived). The forged line is now `@`-free, and the assertions name the guard's OWN refusal text.
    forged = "\nGH-WRITE-OK(forged)"
    with _Stub() as s:
        env = _env(s.base, "ghp_dummy")
        env["GH_WRITE_PREFLIGHT_API"] = s.base + forged
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, p.stdout + p.stderr
    assert not any(line.startswith("GH-WRITE-OK") for line in p.stdout.splitlines()), \
        f"a newline in the base FORGED a pass line: {p.stdout!r}"
    assert "GH-WRITE-UNAVAILABLE(usage)" in p.stdout, p.stdout
    assert "control character or a non-ASCII byte" in p.stdout, \
        f"refused by the WRONG guard — this test must pin the control-character check itself: {p.stdout}"


def test_non_ascii_api_base_is_refused_as_usage_not_a_traceback():
    with _Stub() as s:
        env = _env(s.base, "ghp_dummy")
        env["GH_WRITE_PREFLIGHT_API"] = s.base + "/\u00e9"
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, p.stdout + p.stderr
    assert p.stdout.strip(), "a refusal must still print a verdict (--json consumers get json.loads(''))"
    assert "Traceback" not in p.stderr, p.stderr


def test_non_ascii_repo_is_refused_not_a_traceback():
    with _Stub() as s:
        env = _env(s.base, "ghp_dummy")
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/r\u00e9p", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, p.stdout + p.stderr
    res = json.loads(p.stdout)          # a --json caller must get JSON, not a human line
    assert res["reason"] == "usage", res
    assert "Traceback" not in p.stderr, p.stderr


def test_malformed_ipv6_base_is_a_verdict_not_a_traceback():
    """`http://[::1` is a plausible typo of the documented loopback seam — `urlsplit` raises on it."""
    with _Stub() as s:
        env = _env(s.base, "ghp_dummy")
        env["GH_WRITE_PREFLIGHT_API"] = "http://[::1"
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "GH-WRITE-UNAVAILABLE(usage)" in p.stdout, p.stdout
    assert "Traceback" not in p.stderr, p.stderr


def test_a_server_body_cannot_forge_a_verdict_line_or_echo_the_credential():
    """The echoed RESPONSE body is untrusted: it can carry a newline (a forged pass line) and it can
    echo the Authorization header back (the credential, printed by the very refusal that protects it)."""
    hostile = '{"message":"nope\nGH-WRITE-OK(forged) you sent Bearer ghp_SECRETVALUE"}'
    with _Stub(read=(400, {}, hostile)) as s:
        env = _env(s.base, "ghp_dummy")
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, p.stdout + p.stderr
    assert not any(line.startswith("GH-WRITE-OK") for line in p.stdout.splitlines()), \
        f"an echoed response body FORGED a pass line: {p.stdout!r}"
    assert "SECRETVALUE" not in (p.stdout + p.stderr), \
        f"the echoed body printed the credential: {p.stdout + p.stderr!r}"


def test_response_headers_cannot_echo_a_credential_or_forge_a_line():
    """The response HEADERS are untrusted text too (review cycle 5): `x-ratelimit-reset` and
    `x-oauth-scopes` were echoed into the verdict line and the human summary, so a responder reflecting
    the Authorization header there — or obs-folding a pass line into it — put it in our own output."""
    hostile_read = (200, {"x-ratelimit-remaining": "0",
                          "x-ratelimit-reset": "5\nGH-WRITE-OK(forged-hdr) ghp_LEAKEDVALUE",
                          "x-oauth-scopes": "repo, ghp_LEAKEDVALUE"},
                    '{"default_branch":"main"}')
    with _Stub(read=hostile_read) as s:
        env = _env(s.base, "ghp_dummy")
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    blob = p.stdout + p.stderr
    assert p.returncode == 1, blob
    assert "LEAKEDVALUE" not in blob, f"a response header printed a credential: {blob!r}"
    assert not any(line.startswith("GH-WRITE-OK") for line in blob.splitlines()), \
        f"a response header FORGED a pass line: {blob!r}"


def test_a_prefixless_credential_is_redacted_by_value_not_only_by_shape():
    """Redaction must not depend on a documented prefix (review cycle 6). A legacy 40-hex PAT — or any
    non-GitHub value in `GH_TOKEN`, which `token_shape()` explicitly anticipates — has no `ghp_` shape,
    so the prefix regex cannot see it; only removing the RESOLVED VALUE closes the echo."""
    secret = "0123456789abcdef0123456789abcdef01234567"
    hostile = (200, {"x-oauth-scopes": f"repo, {secret}",
                     "x-ratelimit-remaining": secret},
               '{"default_branch":"main"}')
    with _Stub(read=hostile) as s:
        env = _env(s.base, secret)
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    blob = p.stdout + p.stderr
    assert p.returncode == 0, blob          # all three probes answer 422 on this stub
    assert secret not in blob, f"the credential VALUE reached the output: {blob!r}"


def test_a_long_prefixless_credential_is_not_leaked_even_TRUNCATED():
    """A credential longer than a field's bound must not survive as its first N characters (cycle 7).

    `_sanitise_result` runs after the field was truncated, so value-redaction there cannot match a
    prefix; each echoed header/body must be value-scrubbed at the SOURCE, before `[:N]`. These arms use a
    secret LONGER than the bound and assert no >=8-character prefix of it appears anywhere.
    """
    long_secret = "0123456789abcdef" * 6 + "0123"      # 100 chars, no documented prefix
    def assert_clean(rc, blob, secret):
        for n in range(8, min(len(secret), 40) + 1):
            assert secret[:n] not in blob, f"{n} leading char(s) of the credential leaked: {blob!r}"

    # the retry-after branch (bound 32)
    with _Stub(read=(403, {"retry-after": long_secret},
                 '{"message":"Resource not accessible by personal access token"}')) as s:
        env = _env(s.base, long_secret)
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, p.stdout
    assert_clean(p.returncode, p.stdout + p.stderr, long_secret)

    # the scopes branch (bound 64), on a PASSING --json run
    with _Stub(read=(200, {"x-oauth-scopes": f"repo, {long_secret}"}, '{"default_branch":"main"}')) as s:
        env = _env(s.base, long_secret)
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 0, p.stdout + p.stderr
    assert_clean(p.returncode, p.stdout + p.stderr, long_secret)


def test_a_TRUNCATED_echo_of_the_credential_is_redacted_too():
    """A responder echoing an EXCERPT — not the whole value — must not leak it (review cycle 8).

    Truncation is the typical form of the echo: a proxy that returns the first N characters of the
    blocked request, or a folded header. The exact-value replace cannot see a fragment, and a
    prefix-less credential defeats the shape regex, so the fragment redactor must cover prefixes AND
    suffixes."""
    secret = "0123456789abcdef" * 6 + "0123"          # 100 chars, no documented prefix
    arms = [
        (403, {"retry-after": secret[:20]}, '{"message":"Resource not accessible"}'),
        (403, {"x-oauth-scopes": f"repo, {secret[:30]}"}, '{"default_branch":"main"}'),
        (400, {}, '{"error":"blocked","request":"Authorization: Bearer ' + secret[:61] + '"}'),
        (200, {"x-oauth-scopes": f"repo, {secret[24:]}"}, '{"default_branch":"main"}'),   # SUFFIX
    ]
    for read in arms:
        with _Stub(read=read) as s:
            env = _env(s.base, secret)
            p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise", "--json"],
                               capture_output=True, text=True, env=env, timeout=120)
        blob = p.stdout + p.stderr
        assert secret[:8] not in blob, f"a truncated echo leaked the credential PREFIX: {blob!r}"
        assert secret[-8:] not in blob, f"a folded echo leaked the credential SUFFIX: {blob!r}"


def test_an_INTERIOR_fragment_of_the_credential_is_redacted_too():
    """A fragment need not touch either end of the value (review cycle 9).

    A fixed-width "here is what you sent" panel elides both ends and prints the MIDDLE, and a
    multi-segment fold leaves an interior segment unanchored — so end-anchored redaction leaks."""
    secret = "Zq7HXq65" + "fut3Bk4rNw0EP8q0KYb9JvsW5L6pu33vmJJ6RX62kX4JP4Kh8s" + "J256vd3aE"
    middle = secret[20:70]                       # 50 chars, anchored at NEITHER end
    folds = secret[:30] + "\n" + secret[30:60] + "\n" + secret[60:]
    arms = [
        (200, {"x-oauth-scopes": f"repo, {middle}"}, '{"default_branch":"main"}'),
        (400, {}, '{"message":"blocked: ' + middle + '"}'),
        (400, {}, '{"request":"' + folds + '"}'),
        (200, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": secret[40:90]},
         '{"default_branch":"main"}'),
    ]
    for read in arms:
        with _Stub(read=read) as s:
            env = _env(s.base, secret)
            p = subprocess.run([sys.executable, str(TOOL), "--repo", "daniel-ospina/tortoise", "--json"],
                               capture_output=True, text=True, env=env, timeout=120)
        blob = p.stdout + p.stderr
        for start in (0, 10, 20, 30, 40, 50, 60):
            if start + 8 > len(secret):      # ⛔ a slice past the end is '' and `'' in blob` is ALWAYS true
                continue
            frag = secret[start:start + 8]
            assert frag not in blob, f"an INTERIOR fragment leaked from {read[0]} (offset {start}): {blob!r}"


def test_a_repo_value_cannot_collapse_the_three_probes_onto_one_endpoint():
    """`--repo 'o/r/issues#'` made all three write probes the SAME request (review cycle 10).

    `http.client` strips everything after `#`, so one 422 on `/repos/o/r/issues` satisfied the issues,
    pulls and contents gates at once and the tool printed GH-WRITE-OK for a token that cannot push. This
    is reachable from the documented `$OR` default, with no hostile responder.
    """
    with _Stub() as s:
        env = _env(s.base, "github_pat_dummy_finegrained")
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/r/issues#"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, f"a collapsing repo value PASSED: {p.stdout}{p.stderr}"
    assert "GH-WRITE-UNAVAILABLE(usage)" in p.stdout, p.stdout
    assert "GH-WRITE-OK" not in p.stdout, p.stdout


def test_preflight_itself_refuses_a_collapsing_repo_not_only_the_cli():
    """The repo guard must live in `preflight()`, not only in `main()` (review cycle 11).

    Cycle 10 added a distinctness assertion over the URL STRINGS — which share a prefix and differ by
    suffix, so it was true for every possible `repo` and could never fire. A direct caller got
    `ok=True` with the stub seeing three POSTs to `/repos/o/r/issues`.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("gh_write_preflight", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    with _Stub() as s:                      # every write probe answers 422
        res = mod.preflight("o/r/issues#", s.base, "tok", "env:GH_TOKEN")
        seen = list(s.seen)
    assert res["ok"] is False, f"preflight() PASSED a collapsing repo: {res}"
    assert res["reason"] == "usage", res
    posts = [p for m, p, _ in seen if m == "POST"]
    assert posts == [], f"no probe may run for a refused repo, got {posts}"


def test_the_api_base_cannot_carry_the_credential_into_the_output():
    """`api_base` is printed on the OK line and stored in `--json`, and `_printable_base` only redacts
    `user:pass@` userinfo — so a base with the credential in its PATH printed it (review cycle 11)."""
    secret = "Zq7HXq65" + "fut3Bk4rNw0EP8q0KYb9JvsW5L6pu33vmJJ6RX62kX4JP4Kh8s" + "J256vd3aE"
    with _Stub() as s:
        env = _env(s.base, secret)
        env["GH_WRITE_PREFLIGHT_API"] = s.base + "/" + secret
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/r", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    blob = p.stdout + p.stderr
    for off in range(0, max(1, len(secret) - 7)):
        assert secret[off:off + 8] not in blob, f"api_base leaked the credential at offset {off}: {blob!r}"


def test_the_early_repo_refusals_scrub_the_base_and_serialise_under_json():
    """Both `--repo` refusal tiers run BEFORE the probe, so they must scrub and serialise themselves
    (review cycle 12): the path-embedded credential leaked through the `--json` tier, and the
    missing-slash tier printed a human line at a JSON consumer."""
    secret = "Zq7HXq65" + "fut3Bk4rNw0EP8q0KYb9JvsW5L6pu33vmJJ6RX62kX4JP4Kh8s" + "J256vd3aE"
    for bad_repo in ("or", "o/r#", "o/r/issues#"):
        with _Stub() as s:
            env = _env(s.base, secret)
            env["GH_WRITE_PREFLIGHT_API"] = s.base + "/" + secret
            p = subprocess.run([sys.executable, str(TOOL), "--repo", bad_repo, "--json"],
                               capture_output=True, text=True, env=env, timeout=120)
        assert p.returncode == 1, f"{bad_repo!r} must be refused: {p.stdout}{p.stderr}"
        res = json.loads(p.stdout)               # a --json caller must get JSON on EVERY refusal
        assert res["reason"] == "usage", res
        blob = p.stdout + p.stderr
        for off in range(0, max(1, len(secret) - 7)):
            assert secret[off:off + 8] not in blob, \
                f"{bad_repo!r} leaked the credential from api_base at offset {off}: {blob!r}"


def test_every_json_refusal_carries_the_same_schema():
    """A `--json` consumer must not have to branch on WHICH refusal it got (review finding I4).

    The `--repo` argument-error tier serialised its own four-key payload while every refusal out of
    `preflight()` carried `credential_source`, `token_shape` and `surfaces`, so `res["surfaces"]`
    raised `KeyError` **precisely on "you typed the command wrong"** — the first path a lane reaches,
    and the one this branch exists to make parseable. Both tiers are asserted against one key set, so a
    key added to either shape alone reds here.
    """
    schema = {"repo", "api_base", "credential_source", "token_shape", "ok", "reason", "detail",
              "surfaces"}

    # (a) the argument-error tier — `--repo` with no `/`, refused before any request
    with _Stub() as s:
        env = _env(s.base, "github_pat_dummy_finegrained")
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "or", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, p.stdout + p.stderr
    usage = json.loads(p.stdout)
    assert usage["reason"] == "usage", usage
    assert set(usage) == schema, sorted(set(usage) ^ schema)

    # (b) the `preflight()` tier — a well-formed repo with no credential
    with _Stub() as s:
        env = _env(s.base, None)
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/r", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    assert p.returncode == 1, p.stdout + p.stderr
    measured = json.loads(p.stdout)
    assert measured["reason"] == "no-credential", measured
    assert set(measured) == schema, sorted(set(measured) ^ schema)


def test_the_repo_field_is_scrubbed_because_or_can_hold_the_credential():
    """`--repo` defaults to `$OR`, and the repo field is echoed in the `--json` artifact (review cycle 13).

    The sibling `api_base` key was scrubbed in cycle 12 while this one was not, so a mis-set `$OR` that
    contained the credential printed it in full. Both the guard tier and `preflight()` are covered here.
    """
    secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    # ⛔ TWO DIFFERENT ASSERTIONS, ON PURPOSE: a BARE token has no `/`, so it is a usage refusal; a token in
    # the NAME half (`o/<token>`) is a syntactically valid repo, so it is measured (and may pass) — the
    # property under test there is the REDACTION, not a refusal. Asserting a refusal for the second shape
    # was a false red in the first version of this test.
    for repo_value, expect_rc in ((secret, 1), ("o/" + secret, 0)):
        with _Stub() as s:
            env = _env(s.base, secret)
            p = subprocess.run([sys.executable, str(TOOL), "--repo", repo_value, "--json"],
                               capture_output=True, text=True, env=env, timeout=120)
        assert p.returncode == expect_rc, f"{repo_value[:12]}… expected rc {expect_rc}: {p.stdout}{p.stderr}"
        res = json.loads(p.stdout)
        if expect_rc:
            assert res["reason"] == "usage", res
        blob = p.stdout + p.stderr
        for off in range(0, max(1, len(secret) - 7)):
            assert secret[off:off + 8] not in blob, \
                f"the repo field leaked the credential at offset {off}: {blob!r}"


def test_the_credential_source_label_is_scrubbed_because_it_carries_hosts_yml_text():
    """`credential_source` is `hosts.yml:{login}` — FILE CONTENT, not a constant (review cycle 14).

    A hosts.yml whose `user:` holds the token printed the credential on the summary line and in the
    `--json` artifact, so the "label is never a value" promise was unenforced.
    """
    secret = "0123456789abcdef0123456789abcdef01234567"      # prefix-less: the shape regex cannot help
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "hosts.yml"), "w", encoding="utf-8") as fh:
            fh.write(f"github.com:\n    user: {secret}\n    oauth_token: {secret}\n    git_protocol: https\n")
        env = _env("http://127.0.0.1:9", None, GH_CONFIG_DIR=d)
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/r", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    blob = p.stdout + p.stderr
    for off in range(0, max(1, len(secret) - 7)):
        assert secret[off:off + 8] not in blob, \
            f"credential_source leaked the credential at offset {off}: {blob!r}"


def test_a_login_shaped_value_is_kept_but_file_content_never_becomes_the_label():
    """`hosts.yml:{login}` printed arbitrary `user:` content (review cycle 15).

    The depth-1 `user:` line is file content. A prefix-less legacy value there survived the value scrub
    (it is not the resolved token) and the shape regex (no documented prefix), so it was printed. A real
    GitHub login is kept; anything else collapses to `active`, which loses no diagnostic value because
    the depth-1 rule already says which account was read.
    """
    token = "0123456789abcdef0123456789abcdef01234567"          # the credential actually used
    not_a_login = "fedcba9876543210fedcba9876543210fedcba98"     # a DIFFERENT prefix-less secret
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "hosts.yml"), "w", encoding="utf-8") as fh:
            fh.write(f"github.com:\n    user: {not_a_login}\n    oauth_token: {token}\n    git_protocol: https\n")
        env = _env("http://127.0.0.1:9", None, GH_CONFIG_DIR=d)
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/r", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    blob = p.stdout + p.stderr
    assert not_a_login not in blob, f"the hosts.yml user: line was echoed: {blob!r}"
    res = json.loads(p.stdout)
    assert res["credential_source"] == "hosts.yml:active", res["credential_source"]


def test_inputs_that_are_not_credentials_are_still_never_echoed_as_one():
    """Three echo sites could print a credential that was NOT the resolved token (review cycle 16):
    `GH_HOST` on the wrong-host path (where the env-token leg never runs, so nothing was scrubbed),
    `repo` on the no-credential path (nothing to scrub by value), and the hosts.yml `user:` label (a
    shape allowlist still admits a 32-hex secret)."""
    secret = "fedcba9876543210fedcba9876543210"          # 32 hex — passes a naive "login shape" allowlist

    # (a) GH_HOST on the wrong-host path
    with _Stub() as s:
        env = _env(s.base, None)
        env["GH_HOST"] = secret
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/r"],
                           capture_output=True, text=True, env=env, timeout=120)
    blob = p.stdout + p.stderr
    assert p.returncode == 1 and "wrong-host" in blob, blob
    assert secret not in blob, f"GH_HOST echoed a credential: {blob!r}"

    # (b) no credential resolved: the repo value must not be echoed
    with _Stub() as s:
        env = _env(s.base, None)
        env["HOME"] = "/tmp/gh-preflight-no-home-2"
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/" + secret, "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    blob = p.stdout + p.stderr
    assert secret not in blob, f"a no-credential refusal echoed the repo: {blob!r}"

    # (c) the hosts.yml label carries no file content at all
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "hosts.yml"), "w", encoding="utf-8") as fh:
            fh.write(f"github.com:\n    user: {secret}\n    oauth_token: 0123456789abcdef0123456789abcdef01234567\n    git_protocol: https\n")
        env = _env("http://127.0.0.1:9", None, GH_CONFIG_DIR=d, HOME="/tmp/gh-preflight-no-home-3")
        p = subprocess.run([sys.executable, str(TOOL), "--repo", "o/r", "--json"],
                           capture_output=True, text=True, env=env, timeout=120)
    blob = p.stdout + p.stderr
    assert secret not in blob, f"the hosts.yml user: line was echoed: {blob!r}"


def test_the_git_credential_residual_is_stated_where_a_lane_reads_it():
    """`exit 0` proves the credential **`gh`** resolves, not the one **`git`** uses (P2-3).

    `git` reads `credential.helper`, which can hold a different or older PAT, or be unset (the box
    #10930 records: unset, alongside that token's `git push` -> 403). Where that holds, a box prints
    `GH-WRITE-OK` and the push is still refused — the false-PASS class the AGENTS.md HARD RULE
    exists to close, one layer further out.

    The fix for the review's P2-3 is to state the residual rather than widen the promise, so this
    pins the statement: an edit that drops the caveat from either artifact — the tool's own
    docstring (where the keyring residual already lives) or the AGENTS.md line a lane actually
    reads — reds here instead of silently re-widening the guarantee.
    """
    tool_src = TOOL.read_text(encoding="utf-8")
    assert "WHAT THE VERDICT DOES NOT COVER" in tool_src, \
        "the tool docstring lost its \"does not cover the credential git uses\" section"
    assert "credential.helper" in tool_src, \
        "the residual must NAME credential.helper, or it says nothing checkable"

    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    ok_line = [ln for ln in agents.splitlines() if "GH-WRITE-OK(issues+pull-requests+contents" in ln]
    assert len(ok_line) == 1, f"expected exactly one GH-WRITE-OK contract line, found {len(ok_line)}"
    i = agents.splitlines().index(ok_line[0])
    block = "\n".join(agents.splitlines()[i:i + 6])
    assert "provided `git` resolves the same credential" in block, \
        f"the HARD RULE states a guarantee wider than the tool proves: {block!r}"


def _reason_class_table() -> dict[str, list[str]]:
    """Parse AGENTS.md's `THE REASON NAMES ITS OWN CLASS` bullets into {class: [reason, …]}."""
    lines = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()
    head = next(i for i, ln in enumerate(lines) if "THE REASON NAMES ITS OWN CLASS" in ln)
    tail = next(i for i, ln in enumerate(lines) if "Why it is a gate and not a courtesy call" in ln)
    bullets: list[list[str]] = []
    for ln in lines[head:tail]:
        m = re.match(r"\s*- \*\*(?P<name>[^*]+)\*\*\s+—(?P<body>.*)$", ln)
        if m:
            bullets.append([m.group("name").strip(), m.group("body")])
        elif bullets and ln.strip():                      # a wrapped continuation of the last bullet
            bullets[-1][1] += " " + ln.strip()
    return {name: sorted(set(re.findall(r"`([a-z][a-z0-9]*(?:-[a-z0-9]+)*)`", body)))
            for name, body in bullets}


def test_the_agents_md_reason_table_covers_every_emittable_reason_and_forbidden_is_structural():
    """The rule a lane reads must carry the tool's WHOLE reason space, and `forbidden` is structural (F2).

    The HARD RULE is the artifact this change exists to deliver — a lane is told to read the reason and
    take the fix from its class, so a reason missing from the table is a fix that lane never finds, and
    a reason in the WRONG class is a lane doing the wrong thing on purpose. `forbidden` was filed under
    **transient** ("Re-run; do not park the work"), but the tool emits it for conditions re-running
    cannot change: a `2xx` on a payload GitHub must reject (a proxy/WAF/captive portal or a followed
    redirect rewrote the request — `:571-576`), an unexpected status such as `410` with Issues disabled
    or `451` (`:601`, `:727`), and a `403` carrying none of GitHub's own refusal signals (`:555`). A
    lane following the old text loops forever on a repo setting.

    So this pins three things, each of which reds on the pre-fix AGENTS.md: the classes exist as a set,
    the table's reason tokens are EXACTLY the tool's emittable set (both directions — no undocumented
    reason, no stale one), and `forbidden` sits in `structural` and not in `transient`.
    """
    tool_src = TOOL.read_text(encoding="utf-8")
    start = tool_src.index("REASONS (`GH-WRITE-UNAVAILABLE(<reason>)`)")
    reasons_doc = tool_src[start:tool_src.index("THE ENDPOINT IS PART OF THE VERDICT", start)]
    # the REASONS block lists one reason per two-space-indented row; continuation rows (`fix: …`) are
    # indented far enough that the token+gap rule below does not see them.
    emittable = set(re.findall(r"^  ([a-z][a-z0-9-]*)\s{2,}\S", reasons_doc, re.M))
    assert len(emittable) > 10, f"the REASONS block did not parse as a reason table: {sorted(emittable)}"

    table = _reason_class_table()
    assert set(table) == {"credential verdict", "environment", "transient", "structural"}, \
        f"the HARD RULE's reason classes changed shape: {sorted(table)}"

    listed = [reason for reasons in table.values() for reason in reasons]
    assert len(listed) == len(set(listed)), f"a reason is filed under two classes: {sorted(listed)}"
    assert set(listed) == emittable, (
        "the AGENTS.md reason table and the tool disagree — undocumented: "
        f"{sorted(emittable - set(listed))}, stale: {sorted(set(listed) - emittable)}")

    assert "forbidden" in table["structural"], \
        f"`forbidden` is not filed as structural: {table['structural']}"
    assert "forbidden" not in table["transient"], \
        f"`forbidden` is instructed to be RE-RUN, but it names conditions that cannot change: {table['transient']}"


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in fns:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
