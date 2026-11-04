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
set — so `uv run pytest tests/test_gh_write_preflight.py` alone errors 24 times for a reason that has
nothing to do with these tests.
"""

from __future__ import annotations

import http.server
import json
import os
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
READONLY_403 = (403, {}, '{"message":"Resource not accessible by personal access token","status":"403"}')


class _Stub:
    """A GitHub API stub. Records (method, path, body) so a test can assert the probe SHAPE."""

    def __init__(self, read=OK_READ, issues=OK_ISSUES, pulls=OK_PULLS, reject_tokens=()):
        self.read, self.issues, self.pulls = read, issues, pulls
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


def _env(stub_base: str, token: str | None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST")}
    # ⛔ BOTH CONFIG-DIR LEGS ARE STRIPPED, SO THE TESTS READ THE TEST'S OWN SETUP AND NOT THE HOST'S.
    # The tool resolves `GH_CONFIG_DIR` → `XDG_CONFIG_HOME` → `~/.config`, so either variable left in
    # would let a host gh config answer `test_no_credential_at_all` for it.
    # MEASURED: with `XDG_CONFIG_HOME` holding a gh/hosts.yml the test fails; with `GH_CONFIG_DIR`
    # holding one, 2 failed / 22 passed.
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("GH_CONFIG_DIR", None)
    env["HOME"] = "/tmp/gh-preflight-no-home"          # keeps hosts.yml out of the picture
    env["GH_WRITE_PREFLIGHT_API"] = stub_base
    if token is not None:
        env["GH_TOKEN"] = token
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


def test_pull_requests_writable_but_issues_refused_is_named_too():
    """A landing also CLOSES issues, so a PR-only token is refused, with the opposite name."""
    with _Stub(issues=READONLY_403, pulls=OK_PULLS) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "GH-WRITE-UNAVAILABLE(issues-write-missing)" in out, out


def test_both_surfaces_refused_is_token_read_only():
    with _Stub(issues=READONLY_403, pulls=READONLY_403) as s:
        rc, out, _ = _run(s.base)
    assert rc != 0
    assert "token-read-only" in out, out


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
    # …and it must NOT be a mutating label delete (the old, blind shape).
    assert not any(m == "DELETE" for m, _, _ in seen), f"a DELETE probe reappeared: {seen}"


def test_write_probes_cannot_create_anything():
    """Zero side effect BY CONSTRUCTION: the payloads must be invalid, so nothing is created.

    * issues probe → an EMPTY body (an issue needs a title, so validation must reject it)
    * pulls  probe → a head ref that cannot exist, under `refs/heads/gh-write-preflight-<hex>`
    """
    with _Stub() as s:
        rc, _, _ = _run(s.base)
        seen = list(s.seen)
    assert rc == 0
    issues_body = pulls_body = None
    for method, path, body in seen:
        if method != "POST":
            continue
        if path.endswith("/issues"):
            issues_body = body
        elif path.endswith("/pulls"):
            pulls_body = body
    assert issues_body in ("", "{}"), f"the issues probe sent a body that could CREATE an issue: {issues_body!r}"
    assert pulls_body, "no pulls probe body recorded"
    payload = json.loads(pulls_body)
    assert payload["head"].startswith("refs/heads/gh-write-preflight-"), payload
    assert payload["base"] == "main", payload


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
    # The ONLY store with a token is GH_CONFIG_DIR, so a pass PROVES that leg was read; ignoring it
    # would find no credential at all and answer `no-credential`.
    assert p.returncode == 0, p.stdout + p.stderr
    assert "GH-WRITE-OK" in p.stdout, p.stdout
    assert "gh_config_dir" in p.stdout or "GH_CONFIG_DIR" in p.stderr or p.stderr == "", p.stderr


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
    """Both surfaces authorised → GH-WRITE-OK."""
    with _Stub() as s:
        rc, out, _ = _run(s.base, token="gho_dummy_writecapable")
    assert rc == 0, f"a fully write-capable token was refused: {out}"
    assert "GH-WRITE-OK" in out, out


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
    assert "hosts.yml:active-user" in p.stderr, p.stderr


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
    p = subprocess.run([sys.executable, str(TOOL)], capture_output=True, text=True,
                       env={**os.environ, "OR": "", "GH_TOKEN": "ghp_x"}, timeout=60)
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
    assert res["surfaces"] == {"issues": 422, "pull_requests": 403}, res
    assert secret not in p.stdout and secret not in p.stderr, "the credential leaked into the output"


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
