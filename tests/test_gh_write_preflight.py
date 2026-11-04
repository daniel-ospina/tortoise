"""Hermetic tests for `tools/gh_write_preflight.py` (#10930).

No network, no GitHub, no credentials. A **fake `gh` is put on PATH**, so the tool
runs its real code path — real argv construction, real `gh api -i` parsing, real
classification — against scripted responses. Nothing about the tool is stubbed
out, which matters: the defect this guards is *which identity `gh` resolves*, and
a test that stubbed the `gh` layer would test the stub.

The three falsification directions
----------------------------------
(a) IT REFUSES a read-only token. The whole finding (#10930, and the graph point
    `pt_d3f2785f…`: "the fine-grained PAT lacks issues:write and
    pull_requests:write, as X-Accepted-Github-Permissions names verbatim") is a
    token that **reads fine and writes nothing**. A preflight that only read
    would pass on the broken box, which is the defect reproduced rather than
    fixed.
(b) IT DOES NOT MISNAME THE CAUSE. A rate-limited 403 and a permission 403 are
    both 403 and have opposite repairs — wait vs re-mint. Classifying the second
    when the first is true sends a lane to re-mint a token that was never the
    problem, so the arms are asserted to be mutually exclusive, including the
    case where a rate-limit 403 ALSO carries a permission-shaped body.
(c) IT DOES NOT PASS WHAT IT CANNOT MEASURE. No PR to probe is
    `GH-WRITE-UNVERIFIABLE` (exit 3), never exit 0.

It also pins the two properties a probe must have to be safe to run before every
landing: it performs real writes (so a passing read cannot mask it), and it never
prints the token.

Run standalone:      python3 tests/test_gh_write_preflight.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parent.parent / "tools" / "gh_write_preflight.py"

_spec = importlib.util.spec_from_file_location("gh_write_preflight", MODULE_PATH)
assert _spec and _spec.loader, f"cannot load {MODULE_PATH}"
ghwp = importlib.util.module_from_spec(_spec)
# Registered in `sys.modules` BEFORE exec_module: `@dataclass` resolves
# `cls.__module__` at class-creation time and raises AttributeError on a module
# object that is not registered yet, which would be a collection ERROR rather
# than a test failure.
sys.modules.setdefault("gh_write_preflight", ghwp)
_spec.loader.exec_module(ghwp)

SECRET = "ghp_SENTINEL_must_never_be_printed_0123456789"

# ── the fake `gh`: scripted responses, and a log of what it was asked ─────────
# It is a separate PROCESS because the tool under test shells out; that is the
# only way to exercise the argv/parse path. The mode comes from the environment
# so one file serves every arm, and the log is what proves the tool WRITES.
FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys

MODE = os.environ.get("FAKE_GH_MODE", "ok")
LOG = os.environ.get("FAKE_GH_LOG", "")
argv = sys.argv[1:]

if LOG:
    with open(LOG, "a") as fh:
        fh.write(json.dumps(argv) + "\n")

if os.environ.get("FAKE_GH_NOISE"):
    # The harness shim prints its own pacing banner and gh prints warnings; both go
    # to stderr. A tool that merges the streams parses them as data — the arm this
    # switch exists to falsify.
    sys.stderr.write("[gh-limit] pacing 1.2s (core 41/5000)\n")

INCLUDE = "-i" in argv or "--include" in argv

def emit(code, reason, headers=None, body=""):
    if not INCLUDE:
        # Real `gh api` without `-i` prints ONLY the body — no status line. The
        # fake must do the same, or a tool that forgot `-i` would still pass
        # because every parse would find a status line the real CLI never sends.
        sys.stdout.write(body + "\n")
        sys.exit(0 if code < 400 else 1)
    out = [f"HTTP/2.0 {code} {reason}"]
    for k, v in (headers or {}).items():
        out.append(f"{k}: {v}")
    out.append("")
    out.append(body)
    sys.stdout.write("\n".join(out) + "\n")
    # A non-zero rc for an error status, exactly as `gh api` behaves — the tool
    # must classify from the STATUS LINE, not from rc or from `gh`'s stderr
    # prose ("gh: Resource not accessible …"), because the prose is a second,
    # lossy copy of the same fact.
    sys.exit(0 if code < 400 else 1)

def write_headers(path):
    if "git/refs" in path:
        return {"X-Accepted-Github-Permissions": "contents=write; contents=write,workflows=write",
                "X-Ratelimit-Remaining": "2603"}
    if path.rstrip("/").endswith("/pulls/4242") or "/pulls/" in path:
        return {"X-Accepted-Github-Permissions": "pull_requests=write",
                "X-Ratelimit-Remaining": "2603"}
    if "comments" in path:
        return {"X-Accepted-Github-Permissions": "issues=write; pull_requests=write",
                "X-Ratelimit-Remaining": "2603"}
    return {"X-Ratelimit-Remaining": "2603"}

if argv[:1] == ["auth"]:
    if argv[1:2] == ["token"]:
        print(os.environ.get("FAKE_GH_SECRET", "tok"))
        sys.exit(0)
    print("github.com")
    print("  \u2713 Logged in to github.com account probe-user (keyring)")
    print("  - Active account: false")
    print("  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'")
    print("  \u2713 Logged in to github.com account probe-user (GH_TOKEN)")
    print("  - Active account: true")
    print("  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'")
    sys.exit(0)

if argv[:2] == ["repo", "view"]:
    print("owner/repo")
    sys.exit(0)

if argv[:2] == ["pr", "list"]:
    # The tool asks for the NUMBER via `--jq`, so there is no JSON to locate in a
    # stream that may carry a shim banner.
    if MODE in ("nopr", "readonly-nopr"):
        sys.exit(0)            # `.[0].number` over an empty list prints nothing
    if "--jq" in argv and argv[argv.index("--jq") + 1] == ".[0].number":
        print("4242")
    else:
        print(json.dumps([{"number": 4242}]))
    sys.exit(0)

# gh api …
i = 1
method = "GET"
path = ""
while i < len(argv):
    a = argv[i]
    if a in ("-X", "--method"):
        method = argv[i + 1]; i += 2; continue
    if a in ("-i", "--include"):
        i += 1; continue
    if a in ("--jq", "-q"):
        if argv[i + 1] == ".login":
            print("probe-user")
            sys.exit(0)
        i += 2; continue
    if a in ("-f", "--raw-field", "-F", "--field"):
        i += 2; continue
    if not a.startswith("-"):
        path = a
    i += 1

if path == "user":
    emit(200, "OK", {"X-Ratelimit-Remaining": "2603"},
         json.dumps({"login": "probe-user"}))
    sys.exit(0)

if method == "GET" and path == "repos/owner/repo":
    emit(200, "OK", {"X-Ratelimit-Remaining": "2603"},
         json.dumps({"full_name": "owner/repo", "default_branch": "main",
                     "permissions": {"admin": True, "maintain": True, "push": True,
                                     "triage": True, "pull": True}}))
    sys.exit(0)

if method == "GET" and "git/ref/heads/" in path:
    emit(200, "OK", {"X-Ratelimit-Remaining": "2603"},
         json.dumps({"ref": "refs/heads/main", "object": {"sha": "a" * 40}}))
    sys.exit(0)

if method == "GET" and "/pulls/" in path:
    emit(200, "OK", {"X-Ratelimit-Remaining": "2603"},
         json.dumps({"number": 4242, "title": "an unchanged title",
                     "updated_at": "2026-11-04T18:19:01Z"}))
    sys.exit(0)

# ── writes ────────────────────────────────────────────────────────────────────
h = write_headers(path)
if MODE in ("ok", "nopr"):
    if method == "DELETE":
        emit(204, "No Content", h)
    emit(201 if method == "POST" else 200, "Created",
         h, json.dumps({"id": 999, "ref": "refs/heads/probe"}))
elif MODE in ("readonly-pat", "readonly-nopr"):
    emit(403, "Forbidden", h,
         json.dumps({"message": "Resource not accessible by personal access token",
                     "status": "403"}))
elif MODE == "rate":
    hh = dict(h); hh["X-Ratelimit-Remaining"] = "0"; hh["X-Ratelimit-Reset"] = "1793816933"
    emit(403, "Forbidden", hh,
         json.dumps({"message": "API rate limit exceeded for user ID 81560491.",
                     "status": "403"}))
elif MODE == "rate-with-permission-body":
    # The discriminator arm: a spent bucket whose body ALSO reads like a scope
    # refusal. The headers are the contract and must win.
    hh = dict(h); hh["X-Ratelimit-Remaining"] = "0"
    emit(403, "Forbidden", hh,
         json.dumps({"message": "Resource not accessible by personal access token "
                                "(secondary rate limit)", "status": "403"}))
elif MODE == "expired":
    emit(401, "Unauthorized", h, json.dumps({"message": "Bad credentials", "status": "401"}))
elif MODE == "sso":
    hh = dict(h)
    hh["X-GitHub-SSO"] = "required; url=https://github.com/orgs/acme/sso?authorization_request=abc"
    emit(403, "Forbidden", hh,
         json.dumps({"message": "Resource not accessible by personal access token",
                     "status": "403"}))
else:
    raise SystemExit(f"unknown FAKE_GH_MODE {MODE}")
"""


@pytest.fixture()
def fake_gh(tmp_path):
    """A PATH with only the fake `gh` (plus the real PATH behind it)."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "gh"
    exe.write_text(FAKE_GH, encoding="utf-8")
    exe.chmod(0o755)
    log = tmp_path / "gh.log"

    def run(
        mode: str, *args: str, noise: bool = False, secret: str | None = None
    ) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
        env["FAKE_GH_MODE"] = mode
        env["FAKE_GH_LOG"] = str(log)
        env["FAKE_GH_SECRET"] = SECRET if secret is None else secret
        if noise:
            env["FAKE_GH_NOISE"] = "1"
        else:
            env.pop("FAKE_GH_NOISE", None)
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        env["NO_COLOR"] = "1"
        return subprocess.run(
            [sys.executable, str(MODULE_PATH), "--repo", "owner/repo", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    run.log = log  # type: ignore[attr-defined]
    return run


def calls(run) -> list[list[str]]:
    if not run.log.exists():  # type: ignore[attr-defined]
        return []
    return [json.loads(ln) for ln in run.log.read_text().splitlines() if ln.strip()]  # type: ignore[attr-defined]


def write_calls(run) -> list[list[str]]:
    """Every argv that mutates: `-X POST|PATCH|DELETE`."""
    out = []
    for argv in calls(run):
        for i, a in enumerate(argv):
            if a in ("-X", "--method") and i + 1 < len(argv) and argv[i + 1] != "GET":
                out.append(argv)
                break
    return out


# ── (a) a read-only token is REFUSED, by name, for each resource kind ────────


def test_a_read_only_token_is_refused_for_every_write_resource(fake_gh):
    """The measured defect: reads 200, issue writes work, PR + ref writes 403.

    Asserts BOTH resource kinds are named, because a token can hold one and not
    the other — that asymmetry is the entire finding (`Issues: write` present,
    `Pull requests: write` absent), and a preflight that reported only the first
    failing probe would leave the second to be discovered at the closure step.
    """
    res = fake_gh("readonly-pat")
    assert res.returncode == ghwp.EXIT_UNAVAILABLE, res.stdout + res.stderr
    assert "GH-WRITE-UNAVAILABLE(permission:contents-write)" in res.stderr
    assert "GH-WRITE-UNAVAILABLE(permission:pull_requests-write)" in res.stderr
    # …and it must NOT be reported as anything else.
    for wrong in ("rate-limited", "bad-credentials", "sso-unauthorized"):
        assert wrong not in res.stderr, f"misclassified a scope refusal as {wrong}"


def test_a_read_only_token_never_reports_writable(fake_gh):
    res = fake_gh("readonly-pat", "--json")
    assert res.returncode == ghwp.EXIT_UNAVAILABLE
    report = json.loads(res.stdout)
    assert report["writable"] is False
    # The trap the finding names: the user's role says push:true while the token
    # can write nothing. It is reported for context and must not flip the verdict.
    assert report["user_role_permissions"]["push"] is True


# ── (b) the cause is named correctly, and rate-limit is not confused with it ──


def test_a_spent_bucket_is_rate_limited_not_a_permission_problem(fake_gh):
    """The repair is 'wait', not 're-mint'. Reporting the other costs a lane an hour."""
    res = fake_gh("rate")
    assert res.returncode == ghwp.EXIT_UNAVAILABLE
    assert "GH-WRITE-UNAVAILABLE(rate-limited)" in res.stderr
    assert "permission:" not in res.stderr
    assert "x-ratelimit-remaining=0" in res.stderr


def test_rate_limit_wins_over_a_permission_shaped_body(fake_gh):
    """Headers are the contract; the body is a fallback.

    A secondary-limit 403 can arrive with a body that scans like a scope refusal.
    If the body arm were tested first, this arm would tell the operator to re-mint
    a token that is perfectly granted — the exact inversion the tool exists to
    prevent.
    """
    res = fake_gh("rate-with-permission-body")
    assert res.returncode == ghwp.EXIT_UNAVAILABLE
    assert "GH-WRITE-UNAVAILABLE(rate-limited)" in res.stderr
    assert "permission:" not in res.stderr


def test_an_expired_token_is_bad_credentials(fake_gh):
    res = fake_gh("expired")
    assert res.returncode == ghwp.EXIT_UNAVAILABLE
    assert "GH-WRITE-UNAVAILABLE(bad-credentials)" in res.stderr


def test_missing_org_saml_authorisation_is_named_as_such(fake_gh):
    """Re-minting does not fix SSO; the X-GitHub-SSO header is what distinguishes it."""
    res = fake_gh("sso")
    assert res.returncode == ghwp.EXIT_UNAVAILABLE
    assert "GH-WRITE-UNAVAILABLE(sso-unauthorized)" in res.stderr


# ── (c) what it cannot measure is never a pass ────────────────────────────────


def test_no_pr_to_probe_is_unverifiable_and_never_writable(fake_gh):
    res = fake_gh("nopr")
    assert res.returncode == ghwp.EXIT_UNVERIFIABLE, res.stdout + res.stderr
    assert "GH-WRITE-UNVERIFIABLE(no-pr-to-probe)" in res.stderr
    assert "GH-WRITE-PREFLIGHT OK" not in res.stdout


def test_a_healthy_identity_passes_and_says_so(fake_gh):
    res = fake_gh("ok")
    assert res.returncode == ghwp.EXIT_WRITABLE, res.stdout + res.stderr
    assert "GH-WRITE-PREFLIGHT OK" in res.stdout
    assert "probes=contents,pull_requests" in res.stdout
    report = json.loads(fake_gh("ok", "--json").stdout)
    assert report["writable"] is True
    assert set(report["probes"]) == {"contents", "pull_requests"}


# ── the probe must WRITE, and must be a no-op on the PR ──────────────────────


def test_the_probe_actually_writes_so_a_passing_read_cannot_mask_it(fake_gh):
    """Red-first on the *shape* of the probe: reads pass in every failure mode.

    If this tool only read, it would pass on the broken box — so the assertion is
    that it issues a real POST (ref) and a real PATCH (PR) even on the healthy
    arm.
    """
    fake_gh("ok")
    writes = write_calls(fake_gh)
    methods_paths = []
    for argv in writes:
        method = "GET"
        path = ""
        skip = False
        for i, a in enumerate(argv):
            if skip:
                skip = False
                continue
            if a in ("-X", "--method"):
                method = argv[i + 1]
                skip = True
            elif a in ("-f", "--raw-field", "-F", "--field", "--jq", "-q"):
                skip = True  # the VALUE is not a positional path
            elif not a.startswith("-"):
                path = a
        methods_paths.append((method, path))
    assert ("POST", "repos/owner/repo/git/refs") in methods_paths, methods_paths
    assert any(
        m == "PATCH" and p.startswith("repos/owner/repo/pulls/") for m, p in methods_paths
    ), methods_paths
    # …and every write it made was cleaned up.
    assert any(m == "DELETE" and "git/refs" in p for m, p in methods_paths), methods_paths


def test_the_pull_request_probe_writes_the_title_it_read(fake_gh):
    """A probe must not change the PR it probes.

    Measured 2026-11-04 on `daniel-ospina/tortoise#10902`: `PATCH` with the title
    it already has returns 200 and leaves `title` + `updated_at` byte-identical —
    the property that lets this run before every landing with no residue.
    """
    fake_gh("ok")
    for argv in write_calls(fake_gh):
        if any(a == "PATCH" for a in argv):
            pair = next((argv[i + 1] for i, a in enumerate(argv) if a in ("-f", "--raw-field")), "")
            assert pair == "title=an unchanged title", pair
            return
    pytest.fail("no PATCH was issued, so the PR-write probe never ran")


# ── the token is never disclosed ─────────────────────────────────────────────


def test_the_token_is_never_printed(fake_gh):
    """`gh auth status` is safe to show a human; an env dump is not.

    The identity is reported as a truncated sha256 instead, so a token SWAP stays
    visible without the token becoming readable — including in `--json`, which is
    the mode most likely to be pasted into an artifact.
    """
    for mode in ("ok", "readonly-pat"):
        for extra in ((), ("--json",)):
            res = fake_gh(mode, *extra)
            assert SECRET not in res.stdout, f"{mode}{extra} stdout leaked the token"
            assert SECRET not in res.stderr, f"{mode}{extra} stderr leaked the token"
    ok = fake_gh("ok")
    assert "token=sha256:" in ok.stdout


# ── the parser: the shim wraps gh, so its output is not a bare HTTP block ────


def test_parse_takes_the_last_http_block_past_a_shim_banner():
    raw = "\n".join(
        [
            "[gh-limit] pacing 1.2s (core 41/5000)",
            "HTTP/2.0 200 OK",
            "X-Ratelimit-Remaining: 41",
            "",
            '{"id": 1}',
            "HTTP/2.0 403 Forbidden",
            "X-Accepted-Github-Permissions: pull_requests=write",
            "X-Ratelimit-Remaining: 40",
            "",
            '{"message":"Resource not accessible by personal access token"}',
        ]
    )
    res = ghwp.parse_http_output(raw)
    assert res.status == 403, "the banner or the earlier block was taken as the answer"
    assert res.accepted_permissions == ["pull_requests=write"]
    assert "personal access token" in res.body


def test_parse_survives_a_body_with_blank_lines_and_no_status():
    res = ghwp.parse_http_output("HTTP/2.0 201 Created\n\nline one\n\nline three")
    assert res.status == 201
    assert "line three" in res.body
    assert ghwp.parse_http_output("no http here").status is None


def test_classify_treats_a_null_status_as_no_response_not_a_pass():
    """A call that produced nothing is never a pass (fail closed)."""
    res = ghwp.HttpResult(status=None)
    refusal = ghwp.classify(res, ghwp.PERM_CONTENTS, "contents")
    assert refusal is not None and refusal.reason == "no-response"
    assert refusal.reason in ghwp.UNVERIFIABLE_REASONS


def test_classify_reads_only_success_codes_as_success():
    for status in (200, 201, 204):
        assert ghwp.classify(ghwp.HttpResult(status=status), ghwp.PERM_CONTENTS, "p") is None
    for status in (301, 400, 422, 500):
        assert ghwp.classify(ghwp.HttpResult(status=status), ghwp.PERM_CONTENTS, "p") is not None


# ── #10945 review round 1: each finding gets an arm that fails without its fix ──


def test_a_shim_banner_on_stderr_does_not_corrupt_the_repo_or_the_identity(fake_gh):
    """#10945 review P2 (1). Merging stdout+stderr made the shim's own banner data.

    `gh()` used to return `stdout + stderr`, and three parsers read the last line
    of that: the repo became the banner text, `--jq .login`'s value became the
    banner text, and the IDENTITY DIGEST hashed the banner instead of the token —
    which would report the same credential for two different ones. With the
    streams separated, a noisy stderr changes nothing.
    """
    res = fake_gh("ok", noise=True)
    assert res.returncode == ghwp.EXIT_WRITABLE, res.stdout + res.stderr
    assert "repo=owner/repo" in res.stdout
    assert "identity=probe-user" in res.stdout
    assert "gh-limit" not in res.stdout


def test_the_identity_digest_is_the_tokens_hash_and_a_swap_changes_it(fake_gh):
    """#10945 review P2 (2). `"token=sha256:" in stdout` passes for a CONSTANT.

    The hash's whole job is to make a credential SWAP visible, so the assertion
    has to be the real digest of the real secret — and a second run with a
    different secret has to produce a different one.
    """
    expected = f"token=sha256:{hashlib.sha256(SECRET.encode()).hexdigest()[:12]}"
    res = fake_gh("ok")
    assert expected in res.stdout, res.stdout
    other = fake_gh("ok", secret="a-completely-different-token")
    assert expected not in other.stdout
    assert "token=sha256:" in other.stdout
    assert SECRET not in other.stdout


def test_the_reported_credential_source_is_the_ACTIVE_account_not_the_first(fake_gh):
    """#10945 review P2 (3). `gh auth status` lists the active account SECOND here.

    Returning the first account line names a credential that is not in use, which
    sends the human to re-mint the wrong one — the exact misdiagnosis this tool
    exists to prevent.
    """
    res = fake_gh("ok")
    assert "source=GH_TOKEN" in res.stdout, res.stdout
    assert "source=keyring" not in res.stdout


def test_a_measured_refusal_outranks_an_unmeasurable_probe(fake_gh):
    """#10945 review P2 (4). Exit 3 used to mask a real, measured permission defect.

    `contents` is refused with a definite 403 and `pull_requests` cannot be probed
    (no PR): the caller's `|| exit` recipe must see the REPAIRABLE finding, not
    "cannot measure, file a bug".
    """
    res = fake_gh("readonly-nopr")
    assert res.returncode == ghwp.EXIT_UNAVAILABLE, res.stdout + res.stderr
    assert "GH-WRITE-UNAVAILABLE(permission:contents-write)" in res.stderr
    # The unmeasurable probe is still disclosed — it is just not the verdict.
    assert "GH-WRITE-UNVERIFIABLE(no-pr-to-probe)" in res.stderr


def test_a_404_on_the_pull_request_probe_never_claims_the_repo_is_invisible():
    """#10945 review P2 (5). The role read already succeeded, so the repo IS visible.

    A `--pr` given an ISSUE number is the plausible lane mistake; telling the
    operator to chase `--hostname` or their account sends them the wrong way.
    """
    pr = ghwp.classify(ghwp.HttpResult(status=404), ghwp.PERM_PULL_REQUESTS, "pull_requests")
    repo = ghwp.classify(ghwp.HttpResult(status=404), ghwp.PERM_CONTENTS, "contents")
    assert pr is not None and pr.reason == "pr-not-visible"
    assert "not a pull request" in pr.detail
    assert repo is not None and repo.reason == "repo-not-visible"


def test_the_cleanup_deletes_exactly_the_ref_the_probe_created(fake_gh):
    """#10945 review P2 (6). `"git/refs" in path` cannot tell the probe ref from main.

    A wrong-ref delete would destroy the default branch and the old assertion would
    stay green, so the deleted path is pinned to the ref the POST actually named.
    """
    fake_gh("ok")
    created, deleted = None, None
    for argv in write_calls(fake_gh):
        method = ""
        path = ""
        ref = ""
        for i, a in enumerate(argv):
            if a in ("-X", "--method") and i + 1 < len(argv):
                method = argv[i + 1]
            elif a.startswith("ref="):
                ref = a.removeprefix("ref=")
            elif a.startswith("repos/"):
                path = a
        if method == "POST":
            created = ref
        elif method == "DELETE":
            deleted = path
    assert created and created.startswith("refs/heads/gh-write-preflight-"), created
    name = created.removeprefix("refs/heads/")
    assert deleted == f"repos/owner/repo/git/refs/heads/{name}", deleted
    assert not (deleted or "").endswith("/main"), deleted


def test_the_tool_passes_include_so_a_bare_body_is_never_mistaken_for_an_exchange(fake_gh):
    """#10945 review P2 (8). The fake now prints ONLY the body without `-i`.

    Real `gh api` without `-i` emits no status line, so a tool that forgot it would
    report `no-response` forever. This arm keeps the suite honest about that: every
    `api` invocation carries `-i`, which is why the healthy arm parses a status.
    """
    fake_gh("ok")
    # Only the calls that are PARSED as HTTP exchanges need `-i`; `gh api user
    # --jq .login` legitimately returns a bare value.
    api_calls = [argv for argv in calls(fake_gh) if argv[:1] == ["api"] and "-X" in argv]
    assert api_calls, "no parsed `gh api` exchange was logged"
    for argv in api_calls:
        assert "-i" in argv or "--include" in argv, argv


def test_the_noop_patch_is_re_read_and_its_title_claim_is_checked(fake_gh):
    """#10945 review P2 (9). "No residue" was asserted, never verified.

    The tool sends back the title it read, then re-reads the PR: the detail must say
    the title was unchanged on the re-read, so the property that licenses running
    this before every landing is measured rather than promised.
    """
    res = fake_gh("ok")
    assert "title unchanged on re-read" in res.stdout, res.stdout
    api = [argv for argv in calls(fake_gh) if argv[:1] == ["api"]]
    patched = [i for i, argv in enumerate(api) if "PATCH" in argv]
    assert patched, "no PATCH was issued"
    after = api[patched[0] + 1 :]
    assert any("GET" in argv and any("/pulls/" in a for a in argv) for argv in after), (
        "the PR was never re-read after the PATCH"
    )


if __name__ == "__main__":
    # The carve-out is not optional: this file opens no graph, so without it the
    # session gate refuses every test with "default pytest requires
    # TORTOISE_DB_URI" — a standalone run that reds for an unrelated reason
    # (same block, same reason, as tests/test_closes_landing.py).
    os.environ.setdefault("TORTOISE_TEST_CARVE_OUT", "1")
    raise SystemExit(pytest.main([str(Path(__file__).resolve()), "-q"]))
