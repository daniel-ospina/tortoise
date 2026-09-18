"""Unit tests for tools/check_pages_bindings.py — the #3616 deploy gate.

Everything here is offline: `evaluate()` is a pure function over the Pages API's
`deployment_configs` shape. The point of these tests is that the GATE CAN FAIL —
a checker that always returns [] would have let #3616 ship again.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import check_pages_bindings as cpb  # noqa: E402

MANIFEST_PATH = REPO / "config" / "required-bindings.yml"


def _manifest() -> dict:
    return cpb.load_manifest(MANIFEST_PATH)


def _complete_configs(manifest: dict | None = None) -> dict:
    """Build a Pages `deployment_configs` where EVERY declared binding exists.

    Derived from the manifest rather than hand-written, so adding a binding to
    the manifest cannot silently leave these fixtures incomplete (which is
    exactly what happened when SUPABASE_SERVICE_ROLE_KEY and
    OPENROUTER_API_KEY were added: three tests went red for the right reason).
    """
    manifest = manifest or _manifest()
    configs: dict = {}
    for envname in ("production", "preview"):
        buckets: dict = {}
        for spec in manifest["bindings"]:
            if envname not in spec.get("envs", ["production"]):
                continue
            buckets.setdefault(spec["type"], {})[spec["name"]] = {"id": "x"}
        configs[envname] = buckets
    return configs


def _without(configs: dict, env: str, btype: str, name: str) -> dict:
    """A copy of `configs` with one binding removed."""
    import copy

    out = copy.deepcopy(configs)
    (out.get(env, {}).get(btype) or {}).pop(name, None)
    return out


def test_the_repo_manifest_parses_and_declares_sessions() -> None:
    """`SESSIONS` is the binding whose absence caused the #3616 outage."""
    m = _manifest()
    names = {b["name"] for b in m["bindings"]}
    assert "SESSIONS" in names
    sessions = next(b for b in m["bindings"] if b["name"] == "SESSIONS")
    assert sessions["kind"] == "required", (
        "SESSIONS must be `required` — without it /auth/* answers 503"
    )
    assert sessions["type"] == "d1_databases"
    assert "production" in sessions["envs"]


def test_a_complete_configuration_passes() -> None:
    configs = _complete_configs()
    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert missing_required == []


def test_absent_sessions_is_reported_as_missing_required() -> None:
    """The exact production state at the #3616 outage: env vars present, no D1.

    This is the assertion that would have blocked the deploy.
    """
    configs = _complete_configs()
    # Reproduce the real outage shape: everything else present, no D1 at all.
    for envname in ("production", "preview"):
        configs[envname].pop("d1_databases", None)

    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert "production:d1_databases:SESSIONS" in missing_required
    assert "preview:d1_databases:SESSIONS" in missing_required


def test_required_env_var_absence_is_reported() -> None:
    configs = _without(_complete_configs(), "production", "env_vars", "SUPABASE_URL")
    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert "production:env_vars:SUPABASE_URL" in missing_required


def test_every_required_binding_is_individually_load_bearing() -> None:
    """Each required binding must red the gate ON ITS OWN when removed.

    A blanket "the required list is non-empty" check passes even if one entry is
    dead weight. This parameterises over the manifest so a newly added required
    binding is proven to be enforced rather than assumed to be.
    """
    manifest = _manifest()
    complete = _complete_configs(manifest)
    cases = [
        (spec, env)
        for spec in manifest["bindings"]
        if spec.get("kind", "required") == "required"
        for env in spec.get("envs", ["production"])
    ]
    assert cases, "no required bindings — the gate asserts nothing"

    for spec, env in cases:
        broken = _without(complete, env, spec["type"], spec["name"])
        missing, _ = cpb.evaluate(manifest, broken)
        assert f"{env}:{spec['type']}:{spec['name']}" in missing, (
            f"removing {spec['name']} from {env} did not red the gate — "
            "it is declared `required` but is not enforced"
        )


def test_recommended_absence_warns_but_does_not_block() -> None:
    """Recommended bindings have correct code defaults, so absence must warn.

    A gate that failed the deploy over these would be a gate people disable.
    """
    manifest = _manifest()
    configs = _complete_configs(manifest)
    recommended = [
        spec for spec in manifest["bindings"]
        if spec.get("kind", "required") == "recommended"
    ]
    assert recommended, "no recommended bindings — this test would be vacuous"
    for spec in recommended:
        for env in spec.get("envs", ["production"]):
            configs = _without(configs, env, spec["type"], spec["name"])

    missing_required, missing_recommended = cpb.evaluate(manifest, configs)
    assert missing_required == [], "recommended absence must not block the deploy"
    for spec in recommended:
        for env in spec.get("envs", ["production"]):
            assert f"{env}:{spec['type']}:{spec['name']}" in missing_recommended


def test_a_binding_of_the_wrong_TYPE_does_not_count() -> None:
    """A KV namespace named SESSIONS is not the D1 database the code expects.

    Presence-by-name alone would pass this — which is why the check is keyed on
    (env, type, name), not name alone.
    """
    configs = _complete_configs()
    for envname in ("production", "preview"):
        configs[envname]["kv_namespaces"] = {"SESSIONS": {"id": "x"}}
        configs[envname].pop("d1_databases", None)
    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert "production:d1_databases:SESSIONS" in missing_required


# ---------------------------------------------------------------------------
# Vacuous-gate defences. Each case below was an actual hole found by review:
# the manifest validated "successfully" and the gate silently stopped asserting.
# ---------------------------------------------------------------------------


def _manifest_yaml(sessions_block: str) -> str:
    """A minimal manifest whose SESSIONS entry is spliced in verbatim."""
    return (
        "project: p\nbindings:\n"
        f"{sessions_block}"
        "  - name: OTHER\n    kind: required\n    type: env_vars\n"
        "    envs: [production]\n"
    )


@pytest.mark.parametrize("kind", ["Required", "REQUIRED", "rquired", "optional", "true", ""])
def test_an_invalid_kind_is_rejected_not_downgraded(tmp_path, kind: str) -> None:
    """A misspelled `kind` must be a HARD ERROR, not a silent downgrade.

    Before this check, `kind: Required` validated fine and `evaluate()` compared
    `== "required"`, so a one-character typo turned SESSIONS into a `::warning::`
    and the deploy went green — the exact #3616 failure mode this gate exists to
    prevent. A fail-open gate is worse than no gate, because it is trusted.
    """
    p = tmp_path / "m.yml"
    p.write_text(
        _manifest_yaml(
            "  - name: SESSIONS\n"
            f"    kind: {kind!r}\n"
            "    type: d1_databases\n"
            "    envs: [production]\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid kind"):
        cpb.load_manifest(p)


@pytest.mark.parametrize("envs", ["[]", "null", '\"production\"', "{a: b}"])
def test_empty_or_non_list_envs_is_rejected(tmp_path, envs: str) -> None:
    """`envs: []` used to make a binding INVISIBLE to the gate.

    `spec.get("envs", ["production"])` does not apply its default when the key
    is present-but-empty, so the inner loop never ran: the binding was neither
    required nor recommended, and `evaluate()` reported nothing for it. A
    binding that cannot be reported missing cannot be checked.
    """
    p = tmp_path / "m.yml"
    p.write_text(
        _manifest_yaml(
            "  - name: SESSIONS\n"
            "    kind: required\n"
            "    type: d1_databases\n"
            f"    envs: {envs}\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="envs"):
        cpb.load_manifest(p)


def test_envs_is_optional_and_defaults_to_production(tmp_path) -> None:
    """Non-vacuity guard for the check above: omitting `envs` is LEGAL.

    Without this, the `envs` validation could be over-tightened into rejecting
    the documented default and every test would still pass.
    """
    p = tmp_path / "m.yml"
    p.write_text(
        _manifest_yaml(
            "  - name: SESSIONS\n    kind: required\n    type: d1_databases\n"
        ),
        encoding="utf-8",
    )
    m = cpb.load_manifest(p)
    missing, _ = cpb.evaluate(m, {})
    assert "production:d1_databases:SESSIONS" in missing


def test_a_null_valued_binding_does_not_count_as_present() -> None:
    """`{"SESSIONS": None}` is not a usable binding.

    Key-membership alone counted it as present; truthiness is the correct test.
    """
    configs = _complete_configs()
    for envname in ("production", "preview"):
        configs[envname]["d1_databases"] = {"SESSIONS": None}
    missing_required, _ = cpb.evaluate(_manifest(), configs)
    assert "production:d1_databases:SESSIONS" in missing_required


# ---------------------------------------------------------------------------
# The gate's own wiring (review P3-2). Nothing asserted that the workflow
# actually contains the preflight and the probe, or that the preflight precedes
# the deploy — the "requirement that cannot fail" pattern of #3616, one level
# up. These pin the wiring to the workflow file itself.
#
# IMPORTANT: every source-level assertion below runs against the run block with
# BASH COMMENTS STRIPPED. A comment is not code — cycle 3 proved that raw
# substring checks were satisfied by the prose explaining the very bug they were
# meant to catch (`assert "|| curl_rc=$?" in run` passed on a workflow where the
# guard existed only in a comment).
# ---------------------------------------------------------------------------

WF_PATH = REPO / ".github" / "workflows" / "deploy-pages.yml"

PREFLIGHT = "Preflight — required Pages bindings exist"
PROBE = "Post-deploy — sign-in is actually reachable"
DEPLOY = "Deploy to Cloudflare Pages (premise-labs project)"

#: Strip `#`-comments from a shell run block. Quote-aware: a `#` inside single
#: or double quotes, inside a `${VAR#...}` expansion, or escaped with `\#` is NOT
#: a comment. A naive `(^|\s)#[^\n]*` regex corrupts quoted text — cycle 4 showed
#: it reduced `echo 'see issue #3616.'` to `echo 'see issue `, which would turn
#: any assertion about a quoted token into a false alarm. (Removal-only, so it
#: could never cause a false PASS, but a fragile guard is still a bad guard.)

def _strip_bash_comments(script: str) -> str:
    out: list[str] = []
    for line in script.splitlines(keepends=True):
        in_s = in_d = False
        i = 0
        cut = len(line)
        while i < len(line):
            ch = line[i]
            if in_s:
                if ch == "'":
                    in_s = False
            elif in_d:
                if ch == "\\":
                    i += 2
                    continue
                if ch == '"':
                    in_d = False
            elif ch == "\\":
                i += 2
                continue
            elif ch == "'":
                in_s = True
            elif ch == '"':
                in_d = True
            # `#` starts a comment only at a word boundary: start of the line,
            # or preceded by whitespace. That preserves `a#b`, `${VAR#p}`, and
            # `http://x/#frag`.
            elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
                cut = i
                break
            i += 1
        out.append(line[:cut].rstrip() + ("\n" if line.endswith("\n") else ""))
    return "".join(out)


def _deploy_steps() -> list[dict]:
    wf = cpb.yaml.safe_load(WF_PATH.read_text(encoding="utf-8"))
    return wf["jobs"]["deploy"]["steps"]


def _step_code(name: str) -> str:
    """The step's run block with comments removed — the only thing assertions
    about behaviour may read."""
    step = next(s for s in _deploy_steps() if s.get("name") == name)
    return _strip_bash_comments(step["run"])


def test_the_workflow_has_a_preflight_and_a_probe() -> None:
    names = [s.get("name", "") for s in _deploy_steps()]
    assert PREFLIGHT in names, "the binding preflight step is gone"
    assert PROBE in names, "the post-deploy sign-in probe is gone"


def test_the_preflight_runs_before_the_deploy_and_the_probe_after() -> None:
    """Ordering is the whole point: checking after the upload already reached
    users, and probing before it tests the previous deployment."""
    steps = _deploy_steps()
    names = [s.get("name", "") for s in steps]
    i_pre, i_dep, i_probe = (
        names.index(PREFLIGHT),
        names.index(DEPLOY),
        names.index(PROBE),
    )
    assert i_pre < i_dep, "the binding check must run BEFORE the deploy"
    assert i_probe > i_dep, "the sign-in probe must run AFTER the deploy"


def test_the_preflight_checks_the_manifest_in_this_repo() -> None:
    """The step must gate on the checked-in manifest, not a copy of the list."""
    code = _step_code(PREFLIGHT)
    assert "tools/check_pages_bindings.py" in code
    assert "config/required-bindings.yml" in code
    assert MANIFEST_PATH.exists()


def test_the_preflight_retries_a_transient_api_failure() -> None:
    """A Cloudflare API blip must not red the deploy.

    The probe retries 10x; the preflight had no retry at all, so a single 5xx or
    rate-limit cost a manual re-run — the "required check failing for unrelated
    reasons" pattern that teaches people to ignore it.
    """
    code = _step_code(PREFLIGHT)
    assert "for " in code and "seq 1" in code, "no retry loop in the preflight"


def test_the_preflight_receives_both_cloudflare_credentials() -> None:
    """Without credentials the checker exits 2 (fail closed), which would look
    like a gate failure on every deploy — so they must be wired."""
    step = next(s for s in _deploy_steps() if s.get("name") == PREFLIGHT)
    env = step.get("env") or {}
    assert "CLOUDFLARE_API_TOKEN" in env
    assert env.get("CLOUDFLARE_ACCOUNT_ID")


def test_the_probe_asserts_the_right_behaviours_in_CODE_not_comments() -> None:
    """Every assertion here reads the comment-stripped block.

    Cycle 3 mutation-tested the previous version: deleting the guard from the
    CODE while leaving the comment that mentions it kept every test green.
    """
    code = _step_code(PROBE)
    assert "code_challenge_method=s256" in code, "the PKCE assertion is gone"
    assert "|| curl_rc=$?" in code, (
        "the `|| curl_rc=$?` guard is gone from the CODE — under `bash -e` a "
        "failed curl aborts the step and makes the retry loop unreachable"
    )
    assert "session_store_unavailable" in code, "no #3616 diagnostic in the probe"
    assert "/auth/start" in code, "not probing the endpoint that checks SESSIONS first"
    assert "302" in code
    # The comments must NOT be what satisfies the checks above. Assert the
    # stripper behaves, rather than merely "changed something" — cycle 4 showed
    # the length comparison could pass while stripping nothing useful.
    raw = next(s for s in _deploy_steps() if s.get("name") == PROBE)["run"]
    assert _strip_bash_comments("# whole line\necho ok\n") == "\necho ok\n"
    assert _strip_bash_comments("echo ok  # trailing\n") == "echo ok\n"
    assert _strip_bash_comments("echo 'a # b'\n") == "echo 'a # b'\n"
    assert _strip_bash_comments('echo "a # b"\n') == 'echo "a # b"\n'
    assert _strip_bash_comments("x=${V#p}\n") == "x=${V#p}\n"
    assert _strip_bash_comments("echo http://x/#frag\n") == "echo http://x/#frag\n"
    assert len(_strip_bash_comments(raw)) < len(raw), (
        "the real probe block has no comments — the stripper is not being exercised"
    )


def test_the_comment_stripper_is_quote_aware() -> None:
    r"""A naive `(^|\s)#[^\n]*` regex eats quoted text. It removed the `#3616`
    from `echo 'see issue #3616.'`, which is a REAL line in the probe's 503
    diagnostic — so any assertion about a quoted token would false-alarm."""
    assert _strip_bash_comments("echo 'see issue #3616.'\n") == "echo 'see issue #3616.'\n"
    assert _strip_bash_comments('echo "see #42"\n') == 'echo "see #42"\n'
    # A real trailing comment is still removed.
    assert _strip_bash_comments("echo x # note\n") == "echo x\n"


def test_the_probe_requires_a_302_and_a_pkce_challenge() -> None:
    """Legacy alias retained: the substantive checks live in the test above."""
    test_the_probe_asserts_the_right_behaviours_in_CODE_not_comments()


def test_the_gate_files_select_a_surface_so_their_test_runs() -> None:
    """The gate's own files must select a surface, or a PR editing the gate's
    logic (or downgrading a binding) runs NO gate test — the #3616 pattern one
    level up. Verified against the real selector."""
    sys.path.insert(0, str(REPO / "tools"))
    import ci_selection as cs

    manifest = cs.load_manifest()
    for path in (
        "tools/check_pages_bindings.py",
        "config/required-bindings.yml",
    ):
        result = cs.select([path], "pull_request", manifest)
        assert result.get("surfaces"), (
            f"{path} selects no surface — its guard test would never run"
        )
        assert "test_pages_bindings.py" in (result.get("test_files") or []), (
            f"{path} does not select test_pages_bindings.py"
        )


def test_evaluate_is_not_vacuous_over_the_real_manifest() -> None:
    """Non-vacuity: the manifest must declare at least one required binding with
    at least one env, so an empty config cannot silently pass."""
    m = _manifest()
    required = [b for b in m["bindings"] if b.get("kind", "required") == "required"]
    assert required, "no required bindings declared — the gate asserts nothing"
    assert all(b.get("envs") for b in required), "a required binding has no envs"
    missing, _ = cpb.evaluate(m, {})
    assert missing, "an EMPTY config must report missing required bindings"


def test_cli_fails_closed_when_the_api_is_unreachable(monkeypatch) -> None:
    """Not knowing is not the same as knowing it is fine."""
    def boom(*_a, **_k):
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(cpb, "fetch_configs", boom)
    rc = cpb.main(["--manifest", str(MANIFEST_PATH), "--account-id", "a", "--api-token", "t"])
    assert rc == 2, "an unreachable API must fail CLOSED (exit 2), not pass"


def test_cli_returns_1_when_required_binding_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(cpb, "fetch_configs", lambda *a, **k: {"production": {}, "preview": {}})
    rc = cpb.main(["--manifest", str(MANIFEST_PATH), "--account-id", "a", "--api-token", "t"])
    assert rc == 1


def test_cli_returns_0_when_everything_is_present(monkeypatch) -> None:
    monkeypatch.setattr(cpb, "fetch_configs", lambda *a, **k: _complete_configs())
    rc = cpb.main(["--manifest", str(MANIFEST_PATH), "--account-id", "a", "--api-token", "t"])
    assert rc == 0


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ("bindings: []\n", "non-empty list"),
        ("project: x\n", "non-empty list"),
        ("", "expected a mapping"),
        ("- a\n- b\n", "expected a mapping"),
    ],
)
def test_malformed_manifest_is_rejected(tmp_path, bad: str, message: str) -> None:
    """Each shape fails for its OWN reason, not merely "something raised".

    Accepting any (ValueError, AttributeError, TypeError) would let an unrelated
    crash pass as validation.
    """
    p = tmp_path / "m.yml"
    p.write_text(bad, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        cpb.load_manifest(p)


def test_a_manifest_with_no_required_binding_is_rejected(tmp_path) -> None:
    """An all-`recommended` manifest would make the gate a no-op that always
    exits 0 — a gate that cannot fail."""
    p = tmp_path / "m.yml"
    p.write_text(
        "project: p\nbindings:\n"
        "  - name: A\n    kind: recommended\n    type: env_vars\n"
        "    envs: [production]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no binding is marked"):
        cpb.load_manifest(p)


# ---------------------------------------------------------------------------
# The probe is EXECUTED, not string-matched.
#
# Cycle-2 review found two bugs in this shell that a string assertion cannot see,
# both confirmed by running bash:
#   1. Under GitHub's `bash -e`, a bare `code=$(curl ...)` whose substitution
#      fails ABORTS the step, so the retry loop and every diagnostic below it
#      were unreachable on a transport blip — a transient DNS failure would red
#      the deploy on the first attempt with an EMPTY log.
#   2. `echo "... `session_store_unavailable` ..."` is command substitution:
#      bash tried to RUN that token, stripped it from the message, and printed
#      "command not found".
# Both shipped green. The block is now extracted verbatim and run against a stub
# `curl` with controlled exit codes.
# ---------------------------------------------------------------------------

STUB_CURL = r"""#!/bin/bash
# Stub for curl. Honours STUB_MODE.
out=; hdr=; fmt=; url=
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift 2;;
    -D) hdr=$2; shift 2;;
    -w) fmt=$2; shift 2;;
    -m) shift 2;;
    -sS|-s|-S) shift;;
    *) url=$1; shift;;
  esac
done
count_file="$STUB_DIR/calls"
n=$(cat "$count_file" 2>/dev/null || echo 0)
n=$((n+1))
echo "$n" > "$count_file"
case "$STUB_MODE" in
  rc7|rc7once)
    if [ "$STUB_MODE" = rc7 ] || [ "$n" -lt 3 ]; then
      echo "curl: (7) Failed to connect to host" >&2
      [ -n "$out" ] && : > "$out"
      [ -n "$hdr" ] && : > "$hdr"
      printf '000'
      exit 7
    fi
    ;;
esac
case "$STUB_MODE" in
  ok|rc7once)
    code=302
    loc="https://x.supabase.co/auth/v1/authorize?provider=email&code_challenge=abc&code_challenge_method=s256"
    body=''
    ;;
  503)
    code=503
    loc=''
    body='{"error":"session_store_unavailable"}'
    ;;
  nochallenge)
    code=302
    loc="https://x.supabase.co/auth/v1/authorize?provider=email"
    body=''
    ;;
  200)
    code=200
    loc=''
    body='<html>landing page</html>'
    ;;
  *)
    code=500
    loc=''
    body=''
    ;;
esac
if [ -n "$hdr" ]; then
  printf 'HTTP/1.1 %s\n' "$code" > "$hdr"
  [ -n "$loc" ] && printf 'Location: %s\n' "$loc" >> "$hdr"
fi
[ -n "$out" ] && printf '%s' "$body" > "$out"
case "$fmt" in *http_code*) printf '%s' "$code";; esac
exit 0
"""


def _probe_script(tmp_path: Path) -> Path:
    """Extract the real Post-deploy shell with ONLY these benign rewrites:
      - /tmp/start.* -> a per-test tempdir (so tests cannot collide)
      - 10 attempts -> 3, sleep 15 -> sleep 0 (so failing cases are fast)
    The control flow under test — the `|| curl_rc=$?` guard, the 302 break, the
    status branching and the PKCE grep — is untouched. The guard is asserted to
    be present, so this test cannot pass against a version without it.
    """
    step = next(s for s in _deploy_steps() if s.get("name") == PROBE)
    run = step["run"]
    assert "|| curl_rc=$?" in _strip_bash_comments(run), (
        "the `|| curl_rc=$?` guard is gone — under `bash -e` a failed curl "
        "aborts the step and makes the retry loop unreachable"
    )
    # Quote the temp directory: an unquoted path breaks the generated shell when
    # TMPDIR contains a space (cycle-3 review: `TMPDIR='/tmp/x y' pytest` produced
    # 3 spurious failures). `"$PROBE_TMP"/start.body` quotes the variable while
    # leaving the literal suffix safe.
    rewritten = (
        run.replace("/tmp/start.", '"$PROBE_TMP"/start.')
        .replace("$(seq 1 10)", "$(seq 1 3)")
        # The sleep guard's BOUND must be rewritten too, or it stays `-lt 10` and
        # is unreachable with 3 attempts — so the guard would only ever be pinned
        # by a source string, not exercised. (Final-cycle review, P3.)
        .replace('-lt 10 ] && sleep 15', '-lt 3 ] && sleep 0')
        .replace("sleep 15", "sleep 0")
    )
    p = tmp_path / "probe.sh"
    p.write_text(rewritten, encoding="utf-8")
    return p


def _run_probe(tmp_path: Path, mode: str) -> tuple[int, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "curl"
    stub.write_text(STUB_CURL, encoding="utf-8")
    stub.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_MODE": mode,
        "STUB_DIR": str(tmp_path),
        "PROBE_TMP": str(tmp_path),
    }
    # `bash -e` mirrors GitHub Actions' default Linux shell — the exact condition
    # under which the original bug reproduced.
    r = subprocess.run(
        ["bash", "-e", str(_probe_script(tmp_path))],
        capture_output=True,
        text=True,
        env=env,
    )
    return r.returncode, r.stdout + r.stderr


@pytest.mark.parametrize(
    ("mode", "want_rc"),
    [
        ("ok", 0),           # healthy production shape
        ("503", 1),          # the #3616 outage shape
        ("nochallenge", 1),  # silent write-drop
        ("200", 1),          # a landing page is not a sign-in endpoint
        ("rc7", 1),          # transport failure on every attempt
        ("rc7once", 0),      # transient blip, then healthy -> retry must save it
    ],
)
def test_the_probe_shell_behaves_correctly(tmp_path, mode: str, want_rc: int) -> None:
    rc, out = _run_probe(tmp_path, mode)
    assert rc == want_rc, f"mode={mode} rc={rc} want={want_rc}\n{out}"
    assert "command not found" not in out, (
        f"mode={mode}: the shell tried to EXECUTE a backticked token: {out}"
    )


def test_the_probe_reports_the_503_error_code_verbatim(tmp_path) -> None:
    """The DIAGNOSTIC MESSAGE must survive bash, not merely exist in the YAML.

    Two separate bugs lived here and a source-level check saw neither:
      - backticks around `session_store_unavailable` were command substitution:
        bash ran the token, stripped it from the message, and printed 'command
        not found'.
      - cycle 3 deleted the message text and every test stayed green, because
        the assertion was satisfied by `cat /tmp/start.body` echoing the stub's
        JSON body.
    """
    _rc, out = _run_probe(tmp_path, "503")
    assert "command not found" not in out
    # The operator-facing line itself — what a human reads when sign-in is down.
    # Cycle 3 gutted this sentence and all 47 tests stayed green, because the
    # assertion was satisfied by `cat /tmp/start.body` echoing the stub's JSON.
    # The diagnostic must be asserted in the LOG, not inferred from a file.
    assert "the session store is unavailable" in out
    assert "SESSIONS D1 binding" in out
    assert "#3616" in out


def test_the_probe_retries_a_transient_transport_failure(tmp_path) -> None:
    """Proves the retry loop is REACHABLE under `bash -e`.

    Before the `|| curl_rc=$?` guard, the first failed substitution aborted the
    step: `rc7once` (two failures then a healthy 302) exited non-zero and the
    deploy went red on a blip, with an empty log. The stub counts its calls.
    """
    rc, out = _run_probe(tmp_path, "rc7once")
    assert rc == 0, f"a transient failure must be retried, not fatal\n{out}"
    calls = int((tmp_path / "calls").read_text())
    assert calls == 3, f"expected 3 attempts (2 failures + 1 success), saw {calls}"


def test_the_probe_does_not_treat_a_landing_page_as_sign_in(tmp_path) -> None:
    """A 200 is not success."""
    rc, out = _run_probe(tmp_path, "200")
    assert rc == 1
    assert "expected a 302" in out


# ---------------------------------------------------------------------------
# Manifest classification pins.
#
# Fixtures derive from the manifest (so they cannot drift), which also means a
# change to a binding's `kind` silently re-derives every expectation. These pin
# the specific decisions, each reasoned about, so none can change by accident.
# ---------------------------------------------------------------------------

EXPECTED_CLASSIFICATION = {
    # name: (kind, envs)
    "SESSIONS": ("required", ["production", "preview"]),
    "SUPABASE_URL": ("required", ["production"]),
    "SUPABASE_ANON_KEY": ("required", ["production"]),
    "SUPABASE_SERVICE_ROLE_KEY": ("required", ["production"]),
    "OPENROUTER_API_KEY": ("required", ["production"]),
    # recommended: correct in-source default, or no caller yet
    "APP_ORIGIN": ("recommended", ["production"]),
    "AUTH_CALLBACK_URL": ("recommended", ["production"]),
    "API_ORIGIN": ("recommended", ["production"]),
    # recommended: cloudflare-purge.ts is best-effort and fail-open by design
    "CF_API_TOKEN": ("recommended", ["production"]),
    "CF_ZONE_ID": ("recommended", ["production"]),
    # #2409 contact form: absent → the endpoint answers 503 not_configured
    # (loud, actionable) while the rest of the site serves normally. Deliberately
    # NOT `required`: that would red every deploy — including the one shipping
    # the form — until the endpoint is bound. Promote it once bound. It is a
    # plain intake URL, not a credential: the form has no email leg by design
    # (premise-labs#393 — the outbound sender is over its daily quota).
    "CONTACT_INTAKE_URL": ("recommended", ["production"]),
}


def test_the_manifest_classification_matches_the_reviewed_table() -> None:
    """Flipping OPENROUTER_API_KEY to `recommended`, or dropping `preview` from
    SESSIONS, previously left the whole suite green — the fixtures derive from
    whatever the manifest currently says."""
    actual = {
        spec["name"]: (spec.get("kind", "required"), spec.get("envs", ["production"]))
        for spec in _manifest()["bindings"]
    }
    assert actual == EXPECTED_CLASSIFICATION


def test_the_classification_table_covers_every_binding() -> None:
    """Non-vacuity: the table above must not silently miss a new binding."""
    names = {spec["name"] for spec in _manifest()["bindings"]}
    assert names == set(EXPECTED_CLASSIFICATION), (
        "a binding was added or removed without updating EXPECTED_CLASSIFICATION"
    )


def test_a_non_string_kind_is_a_ValueError_not_a_TypeError(tmp_path) -> None:
    """`kind: [required]` must hit the documented ValueError contract.

    An unhashable kind raised TypeError from the set-membership test, escaping
    the documented contract (main() still failed closed, but on the wrong path).
    """
    p = tmp_path / "m.yml"
    p.write_text(
        _manifest_yaml(
            "  - name: SESSIONS\n    kind: [required]\n    type: d1_databases\n"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid kind"):
        cpb.load_manifest(p)


def test_json_output_is_a_single_parseable_document(monkeypatch, capsys) -> None:
    """`--json` must print exactly one JSON document on stdout.

    Cycle 4 found the `::warning::` lines and the success line followed the JSON
    on stdout, so `json.loads(stdout)` failed with "Extra data: line 12 column
    1" — i.e. the flag was not machine-readable. Diagnostics go to stderr.
    """
    monkeypatch.setattr(cpb, "fetch_configs", lambda *a, **k: _complete_configs())
    rc = cpb.main(
        ["--manifest", str(MANIFEST_PATH), "--account-id", "a",
         "--api-token", "t", "--json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # must not raise
    assert payload["missing_required"] == []
    assert payload["project"] == "premise-labs"
    assert rc == 0


def test_json_output_still_reports_missing_required_and_exits_1(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cpb, "fetch_configs", lambda *a, **k: {})
    rc = cpb.main(
        ["--manifest", str(MANIFEST_PATH), "--account-id", "a",
         "--api-token", "t", "--json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["missing_required"], "a missing binding must appear in the JSON"
    assert rc == 1


def test_json_output_on_the_exit_2_path_is_still_a_parseable_document(tmp_path, capsys) -> None:
    """`--json` must emit ONE document for every exit code, including 2.

    Without this, the exit-2 paths printed nothing, so an unconditional
    `json.loads(stdout)` crashed with JSONDecodeError instead of reading an
    error document. (Final-cycle review, P3.)
    """
    rc = cpb.main(
        ["--manifest", str(tmp_path / "nope.yml"), "--account-id", "a",
         "--api-token", "t", "--json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # must not raise
    assert payload["error"], "the error path must carry a reason"
    assert payload["project"] is None
    assert rc == 2


def test_the_probe_harness_exercises_the_sleep_GUARD_not_just_the_string() -> None:
    """The harness must rewrite the guard's BOUND too.

    `_probe_script` reduces 10 attempts to 3; if it did not also reduce `-lt 10`
    to `-lt 3`, the guard would be unreachable and pinned only by a source
    string — the same weakness this PR has already been caught on twice.
    """
    step = next(s for s in _deploy_steps() if s.get("name") == PROBE)
    run = step["run"]
    assert "[ \"$attempt\" -lt 10 ] && sleep 15" in run, (
        "the shipped guard changed shape — update _probe_script and this test"
    )
    import inspect

    src = inspect.getsource(_probe_script)
    assert "-lt 10 ] && sleep 15" in src and "-lt 3 ] && sleep 0" in src, (
        "_probe_script does not rewrite the sleep guard's bound, so the guard "
        "is never exercised by the harness"
    )


def test_a_malformed_success_payload_exits_2_not_1(monkeypatch) -> None:
    """`success: true` with no `result` is could-not-determine (2), not
    binding-missing (1).

    A KeyError escaping to exit 1 would misreport the reason and send an
    operator hunting for a binding that is not the problem.
    """

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"success": true}'

    monkeypatch.setattr(cpb.urllib.request, "urlopen", lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError, match="malformed payload"):
        cpb.fetch_configs("acct", "proj", "token")


# ---------------------------------------------------------------------------
# The PREFLIGHT is executed too.
#
# Cycle 4 showed the preflight could be turned into a no-op with all 49 tests
# green — e.g. `exit $rc` -> `exit 0`, or replacing the checker invocation with
# an `echo`. The preflight's ONLY job is to fail the deploy, so a suite that
# cannot tell it from `echo` is not testing the gate at all. This is the #3616
# pattern one level up, again.
# ---------------------------------------------------------------------------

STUB_CHECKER = r"""#!/bin/bash
# Stub standing in for tools/check_pages_bindings.py. Records each invocation so
# the harness can prove the checker was ACTUALLY RUN (an `echo` replacement
# would leave the log empty), then exits with the code from STUB_EXITS.
dir=$STUB_DIR
n=$(cat "$dir/checker_calls" 2>/dev/null || echo 0)
n=$((n+1))
echo "$n" > "$dir/checker_calls"
echo "STUB_CHECKER_INVOKED:$n"
exits="$STUB_EXITS"
i=1
code=""
for e in $exits; do
  if [ "$i" -eq "$n" ]; then code=$e; break; fi
  i=$((i+1))
done
[ -z "$code" ] && code=$(echo "$exits" | awk '{print $NF}')
exit "$code"
"""


def _preflight_script(tmp_path: Path) -> Path:
    """Extract the real Preflight run block with only these rewrites:
      - the checker path -> the stub on PATH
      - 3 attempts -> 3, sleep 10 -> sleep 0 (so failures are fast)
    The `rc=$?` capture, the `&& rc=0 && break` idiom, the no-sleep-on-last
    -attempt guard and `exit $rc` are all left exactly as shipped.
    """
    step = next(s for s in _deploy_steps() if s.get("name") == PREFLIGHT)
    run = step["run"]
    rewritten = (
        run.replace("python3 tools/check_pages_bindings.py", "check_stub")
        .replace("python3 -m pip install --quiet 'pyyaml==6.0.3'", ":")
        .replace("sleep 10", "sleep 0")
    )
    assert "exit $rc" in rewritten, "the preflight no longer propagates its exit code"
    assert "check_stub" in rewritten, "the checker invocation was not substituted"
    p = tmp_path / "preflight.sh"
    p.write_text(rewritten, encoding="utf-8")
    return p


def _run_preflight(tmp_path: Path, exits: str) -> tuple[int, str, int]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (tmp_path / "bin" / "check_stub").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    stem = tmp_path / "check_stub_impl"
    stem.write_text(STUB_CHECKER, encoding="utf-8")
    stem.chmod(0o755)
    (bin_dir / "check_stub").write_text(
        f'#!/bin/bash\nexec "{stem}"\n', encoding="utf-8"
    )
    (bin_dir / "check_stub").chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
        "STUB_EXITS": exits,
    }
    r = subprocess.run(
        ["bash", "-e", str(_preflight_script(tmp_path))],
        capture_output=True,
        text=True,
        env=env,
    )
    calls_file = tmp_path / "checker_calls"
    calls = int(calls_file.read_text()) if calls_file.exists() else 0
    return r.returncode, r.stdout + r.stderr, calls


@pytest.mark.parametrize(
    ("exits", "want_rc", "want_calls"),
    [
        ("0 0 0", 0, 1),      # healthy: one call, done
        ("1 1 1", 1, 3),      # a REQUIRED binding is absent -> still fails
        ("2 2 0", 0, 3),      # transient API failure -> retry saves the deploy
        ("2 2 2", 2, 3),      # API down -> fail closed with the right code
    ],
)
def test_the_preflight_shell_behaves_correctly(
    tmp_path, exits: str, want_rc: int, want_calls: int
) -> None:
    rc, out, calls = _run_preflight(tmp_path, exits)
    assert rc == want_rc, f"exits={exits!r} rc={rc} want={want_rc}\n{out}"
    assert calls == want_calls, f"exits={exits!r} checker calls={calls} want={want_calls}"


def test_the_preflight_actually_invokes_the_checker(tmp_path) -> None:
    """The anti-neutering assertion.

    Replacing the checker invocation with an `echo` (or `exit 0`) leaves every
    source-level string in place; only counting invocations catches it.
    """
    _rc, _out, calls = _run_preflight(tmp_path, "0 0 0")
    assert calls >= 1, (
        "the preflight never invoked the checker — it can be turned into a no-op "
        "that passes every other test"
    )
    assert "STUB_CHECKER_INVOKED:1" in _out


def test_the_preflight_does_not_sleep_after_its_last_attempt(tmp_path) -> None:
    """A genuinely-missing binding must not cost 3 x sleep before failing."""
    step = next(s for s in _deploy_steps() if s.get("name") == PREFLIGHT)
    code = _strip_bash_comments(step["run"])
    assert "sleep" in code, "no retry delay at all"
    assert "[ \"$attempt\" -lt 3 ] && sleep" in code, (
        "the retry sleeps unconditionally, including after the final attempt"
    )


def test_the_probe_does_not_sleep_after_its_last_attempt() -> None:
    code = _step_code(PROBE)
    assert '[ "$attempt" -lt 10 ] && sleep' in code, (
        "the probe sleeps after its final attempt"
    )


def test_the_manifest_is_not_inside_the_pages_upload_root() -> None:
    """`wrangler pages deploy .` uploads ALL of `website/`.

    `.wranglerignore` is NOT honoured by `wrangler pages deploy` (verified four
    ways in cycle 4, including a live 200 on
    https://tortoise.premiselabs.co/.wranglerignore). So any file left under
    `website/` is published — which is why the manifest lives in `config/`.
    A future move back under `website/` would silently publish the binding
    inventory and the D1 id.
    """
    assert MANIFEST_PATH.exists(), f"manifest missing: {MANIFEST_PATH}"
    assert MANIFEST_PATH.parent.name == "config", (
        f"the manifest is at {MANIFEST_PATH} — anything under website/ is served "
        "publicly by wrangler pages deploy"
    )
    assert "required-bindings" not in [
        p.name for p in (REPO / "website").glob("required-bindings*")
    ]
