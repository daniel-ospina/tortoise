"""Unit tests for tools/check_pages_bindings.py — the #3616 deploy gate.

Everything here is offline: `evaluate()` is a pure function over the Pages API's
`deployment_configs` shape. The point of these tests is that the GATE CAN FAIL —
a checker that always returns [] would have let #3616 ship again.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import check_pages_bindings as cpb  # noqa: E402
import check_pages_upload_root as cpur  # noqa: E402

MANIFEST_PATH = REPO / "config" / "required-bindings.yml"
UPLOAD_CLASSIFICATION_PATH = REPO / "config" / "pages-upload-classification.txt"


def _manifest() -> dict:
    return cpb.load_manifest(MANIFEST_PATH)


def _project(name: str) -> dict:
    """One project's manifest entry (`{"project": ..., "bindings": [...]}`)."""
    return next(p for p in _manifest()["projects"] if p["project"] == name)


def _complete_configs(project: dict | str | None = None) -> dict:
    """Build a Pages `deployment_configs` where EVERY declared binding exists.

    Derived from the manifest rather than hand-written, so adding a binding to
    the manifest cannot silently leave these fixtures incomplete (which is
    exactly what happened when SUPABASE_SERVICE_ROLE_KEY and
    OPENROUTER_API_KEY were added: three tests went red for the right reason).

    Multiple projects share binding names (both need SESSIONS and SUPABASE_URL),
    so this is now per-project: `_complete_configs("tortoise-dashboard")` returns
    exactly what THAT project's manifest entry requires.
    """
    if project is None:
        project = _project("premise-labs")
    elif isinstance(project, str):
        project = _project(project)
    configs: dict = {}
    for envname in ("production", "preview"):
        buckets: dict = {}
        for spec in project["bindings"]:
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
    """`SESSIONS` is the binding whose absence caused the #3616 outage — for
    BOTH projects, because the BFF moved onto `tortoise-dashboard` (#4054) and
    the D1 store has to follow it."""
    for project in _manifest()["projects"]:
        names = {b["name"] for b in project["bindings"]}
        assert "SESSIONS" in names, f"{project['project']} declares no SESSIONS"
        sessions = next(b for b in project["bindings"] if b["name"] == "SESSIONS")
        assert sessions["kind"] == "required", (
            f"{project['project']}: SESSIONS must be `required` — without it "
            "/auth/* answers 503"
        )
        assert sessions["type"] == "d1_databases"
        assert "production" in sessions["envs"]


def test_a_complete_configuration_passes() -> None:
    for project in _manifest()["projects"]:
        missing_required, _ = cpb.evaluate(project, _complete_configs(project))
        assert missing_required == [], project["project"]


def test_absent_sessions_is_reported_as_missing_required() -> None:
    """The exact production state at the #3616 outage: env vars present, no D1.

    This is the assertion that would have blocked the deploy — and it must hold
    for `tortoise-dashboard` too, whose 0 bindings at the #4054 move would have
    503'd every /auth/* request on a green deploy.
    """
    for project in _manifest()["projects"]:
        configs = _complete_configs(project)
        # Reproduce the real outage shape: everything else present, no D1 at all.
        for envname in ("production", "preview"):
            configs[envname].pop("d1_databases", None)

        missing_required, _ = cpb.evaluate(project, configs)
        pname = project["project"]
        assert f"{pname}:production:d1_databases:SESSIONS" in missing_required
        assert f"{pname}:preview:d1_databases:SESSIONS" in missing_required


def test_required_env_var_absence_is_reported() -> None:
    project = _project("premise-labs")
    configs = _without(
        _complete_configs(project), "production", "env_vars", "SUPABASE_URL"
    )
    missing_required, _ = cpb.evaluate(project, configs)
    assert "premise-labs:production:env_vars:SUPABASE_URL" in missing_required


def test_every_required_binding_is_individually_load_bearing() -> None:
    """Each required binding, in EACH project, must red the gate ON ITS OWN.

    A blanket "the required list is non-empty" check passes even if one entry is
    dead weight. This parameterizes over both projects so a newly added required
    binding (or a new project) is proven to be enforced rather than assumed to be.
    """
    cases = 0
    for project in _manifest()["projects"]:
        complete = _complete_configs(project)
        for spec in project["bindings"]:
            if spec.get("kind", "required") != "required":
                continue
            for env in spec.get("envs", ["production"]):
                cases += 1
                broken = _without(complete, env, spec["type"], spec["name"])
                missing, _ = cpb.evaluate(project, broken)
                label = f"{project['project']}:{env}:{spec['type']}:{spec['name']}"
                assert label in missing, (
                    f"removing {spec['name']} from {env} in {project['project']} "
                    "did not red the gate — it is declared `required` but is not "
                    "enforced"
                )
    assert cases, "no required bindings — the gate asserts nothing"


def test_recommended_absence_warns_but_does_not_block() -> None:
    """Recommended bindings have correct code defaults, so absence must warn.

    A gate that failed the deploy over these would be a gate people disable.
    """
    for project in _manifest()["projects"]:
        configs = _complete_configs(project)
        recommended = [
            spec
            for spec in project["bindings"]
            if spec.get("kind", "required") == "recommended"
        ]
        assert recommended, (
            f"{project['project']}: no recommended bindings — this test would be "
            "vacuous"
        )
        for spec in recommended:
            for env in spec.get("envs", ["production"]):
                configs = _without(configs, env, spec["type"], spec["name"])

        missing_required, missing_recommended = cpb.evaluate(project, configs)
        assert missing_required == [], "recommended absence must not block the deploy"
        pname = project["project"]
        for spec in recommended:
            for env in spec.get("envs", ["production"]):
                assert f"{pname}:{env}:{spec['type']}:{spec['name']}" in missing_recommended


def test_a_binding_of_the_wrong_TYPE_does_not_count() -> None:
    """A KV namespace named SESSIONS is not the D1 database the code expects.

    Presence-by-name alone would pass this — which is why the check is keyed on
    (project, env, type, name), not name alone.
    """
    project = _project("premise-labs")
    configs = _complete_configs(project)
    for envname in ("production", "preview"):
        configs[envname]["kv_namespaces"] = {"SESSIONS": {"id": "x"}}
        configs[envname].pop("d1_databases", None)
    missing_required, _ = cpb.evaluate(project, configs)
    assert "premise-labs:production:d1_databases:SESSIONS" in missing_required


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
    missing, _ = cpb.evaluate(m["projects"][0], {})
    assert "p:production:d1_databases:SESSIONS" in missing


def test_a_null_valued_binding_does_not_count_as_present() -> None:
    """`{"SESSIONS": None}` is not a usable binding.

    Key-membership alone counted it as present; truthiness is the correct test.
    """
    project = _project("premise-labs")
    configs = _complete_configs(project)
    for envname in ("production", "preview"):
        configs[envname]["d1_databases"] = {"SESSIONS": None}
    missing_required, _ = cpb.evaluate(project, configs)
    assert "premise-labs:production:d1_databases:SESSIONS" in missing_required


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

#: #3616 — the gate's preflight in the `deploy` job (scoped to premise-labs).
PREFLIGHT = "Preflight — required Pages bindings exist"
#: #4054 — the dashboard job's counterpart. The BFF moved onto
#: `tortoise-dashboard`, so the same gate is wired with a different project.
DASHBOARD_PREFLIGHT = "Preflight — required Pages bindings exist (tortoise-dashboard)"
DASHBOARD_DEPLOY = "Deploy to Cloudflare Pages (tortoise-dashboard project)"
PROBE = "Post-deploy — sign-in is actually reachable"
DEPLOY = "Deploy to Cloudflare Pages (premise-labs project)"
#: #3620 — the pre-upload gate that every tracked top-level entry under
#: `website/` is classified before anything is staged/uploaded.
UPLOAD_PREFLIGHT = "Preflight — every website/ top-level entry is classified (#3620)"
#: #3620 — the post-deploy assertion that the staged upload root excluded the
#: internal paths that `wrangler pages deploy .` used to serve.
LEAK_PROBE = "Post-deploy — internal paths are not publicly served (#3620)"

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


def _dashboard_steps() -> list[dict]:
    wf = cpb.yaml.safe_load(WF_PATH.read_text(encoding="utf-8"))
    return wf["jobs"]["deploy-dashboard"]["steps"]


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
    # Hop 1 must pin THIS PROJECT's routing contract. Without these, the probe
    # only ever asked "is the app origin healthy" and a revert to a 301 — or to
    # the 404 a stale bookmark used to get — reds nothing (#4346).
    assert "tortoise.premiselabs.co/auth/start" in code, "hop 1 lost the marketing host"
    assert "app.premiselabs.co/auth/start" in code, "hop 1 lost the app-origin target"
    assert 'if [ "$loc" != "$app" ]' in code, (
        "hop 1 no longer pins WHERE it redirects — a 302 to the wrong place would pass"
    )
    assert "start2.hdr" in code, (
        "the PKCE grep must read the APP origin's header. The marketing-hop 302 is a "
        "redirect TO the app origin and can never carry a PKCE challenge, so grepping "
        "hop 1's header is either vacuous or fatal."
    )
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
        "tools/check_pages_upload_root.py",
        "config/pages-upload-classification.txt",
    ):
        result = cs.select([path], "pull_request", manifest)
        assert result.get("surfaces"), (
            f"{path} selects no surface — its guard test would never run"
        )
        assert "test_pages_bindings.py" in (result.get("test_files") or []), (
            f"{path} does not select test_pages_bindings.py"
        )


def test_evaluate_is_not_vacuous_over_the_real_manifest() -> None:
    """Non-vacuity: EVERY project must declare at least one required binding with
    at least one env, so an empty config cannot silently pass."""
    for project in _manifest()["projects"]:
        required = [
            b for b in project["bindings"] if b.get("kind", "required") == "required"
        ]
        assert required, (
            f"{project['project']}: no required bindings — the gate asserts nothing"
        )
        assert all(b.get("envs") for b in required), "a required binding has no envs"
        missing, _ = cpb.evaluate(project, {})
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


def test_cli_returns_1_when_only_the_SECOND_project_is_missing_bindings(monkeypatch) -> None:
    """`main` must not stop at the first project nor report the first project's
    result as the whole verdict.

    A loop that fetched only `premise-labs`, or that returned as soon as the
    first project was clean, would deploy a broken `tortoise-dashboard` on a
    green gate — the multi-project #3616 shape.
    """

    def fake(_account, project, _token):
        if project == "tortoise-dashboard":
            return {}
        return _complete_configs("premise-labs")

    monkeypatch.setattr(cpb, "fetch_configs", fake)
    rc = cpb.main(["--manifest", str(MANIFEST_PATH), "--account-id", "a", "--api-token", "t"])
    assert rc == 1


def test_cli_returns_0_when_everything_is_present(monkeypatch) -> None:
    monkeypatch.setattr(
        cpb, "fetch_configs", lambda _account, project, _token: _complete_configs(project)
    )
    rc = cpb.main(["--manifest", str(MANIFEST_PATH), "--account-id", "a", "--api-token", "t"])
    assert rc == 0


def test_cli_rejects_an_unknown_project_instead_of_passing_vacuously(monkeypatch) -> None:
    """`--project typo` must exit 2, not 0.

    With no matching project the loop body would not run and the gate would
    report success having checked NOTHING — the #3616 vacuous gate, one flag
    away.
    """
    monkeypatch.setattr(cpb, "fetch_configs", lambda *a, **k: {})
    rc = cpb.main(
        [
            "--manifest",
            str(MANIFEST_PATH),
            "--project",
            "tortoise-dashbord",
            "--account-id",
            "a",
            "--api-token",
            "t",
        ]
    )
    assert rc == 2


def test_cli_scopes_to_a_single_project_when_asked(monkeypatch) -> None:
    """`--project premise-labs` must NOT be blocked by a broken dashboard.

    The two deploy jobs are independent surfaces: the marketing deploy passes
    `--project premise-labs` precisely so the not-yet-configured dashboard
    cannot block it (#4054).
    """

    def fake(_account, project, _token):
        if project == "tortoise-dashboard":
            return {}
        return _complete_configs("premise-labs")

    monkeypatch.setattr(cpb, "fetch_configs", fake)
    rc = cpb.main(
        [
            "--manifest",
            str(MANIFEST_PATH),
            "--project",
            "premise-labs",
            "--account-id",
            "a",
            "--api-token",
            "t",
        ]
    )
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
# Which hop is this? A healthy deploy costs TWO calls per attempt: the marketing
# host first, then the app origin. The stub must answer them DIFFERENTLY or it
# cannot model the seam at all — a single-response stub makes the hop-1 Location
# assertion unsatisfiable and the tests would have to be weakened to pass.
case "$url" in
  https://tortoise.premiselabs.co/*) hop=marketing;;
  *) hop=app;;
esac
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
SUPABASE_LOC="https://x.supabase.co/auth/v1/authorize?provider=email&code_challenge=abc&code_challenge_method=s256"
APP_LOC="https://app.premiselabs.co/auth/start"
case "$STUB_MODE" in
  ok|rc7once)
    if [ "$hop" = marketing ]; then code=302; loc="$APP_LOC"; body=''
    else code=302; loc="$SUPABASE_LOC"; body=''; fi
    ;;
  hop1-301)
    if [ "$hop" = marketing ]; then code=301; loc="$APP_LOC"; body=''
    else code=302; loc="$SUPABASE_LOC"; body=''; fi
    ;;
  hop1-404)
    if [ "$hop" = marketing ]; then code=404; loc=''; body='<html>404</html>'
    else code=302; loc="$SUPABASE_LOC"; body=''; fi
    ;;
  hop1-wrongdest)
    if [ "$hop" = marketing ]; then
      code=302; loc="https://tortoise.premiselabs.co/auth"; body=''
    else code=302; loc="$SUPABASE_LOC"; body=''; fi
    ;;
  503)
    if [ "$hop" = marketing ]; then code=302; loc="$APP_LOC"; body=''
    else code=503; loc=''; body='{"error":"session_store_unavailable"}'; fi
    ;;
  nochallenge)
    if [ "$hop" = marketing ]; then code=302; loc="$APP_LOC"; body=''
    else code=302; loc="https://x.supabase.co/auth/v1/authorize?provider=email"; body=''; fi
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
        run.replace("/tmp/start", '"$PROBE_TMP"/start')
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
        ("hop1-301", 1),     # #4346: the browser-persistent regression
        ("hop1-404", 1),     # #4346: what a stale bookmark used to get
        ("hop1-wrongdest", 1),  # a 302 to somewhere that is not the app origin
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
    # 4, not 3: a healthy deploy now costs TWO calls (marketing host, then app
    # origin), so 2 transport failures + one success per hop = 4. Pinning the
    # count is what proves the retry guard wraps BOTH loops — a guard on only
    # the first would leave hop 2 aborting under `bash -e`.
    assert calls == 4, f"expected 4 attempts (2 failures + 1 success per hop), saw {calls}"


@pytest.mark.parametrize("mode", ["hop1-301", "hop1-404", "hop1-wrongdest"])
def test_the_probe_rejects_a_broken_marketing_host_hop(tmp_path, mode: str) -> None:
    """#4346: the marketing host must answer /auth/start with a 302 to the app
    origin, and the probe must say so.

    The probe this replaces asked for `302 + PKCE` from the marketing host in a
    single request. Verified by execution against a stub modelling the correct
    two-hop architecture, that version FAILS — hop 1 redirects to the app origin
    and carries no PKCE — so it was unsatisfiable, red on `main`, and named the
    wrong remedy ("expected a 302 to Supabase" from the marketing host). These
    modes pin the shape it should have asked for. A 301 is worth pinning
    explicitly because it is worse than the 404 in one respect: it is
    browser-persistent and cannot be reclaimed by a later deploy, which is why
    `SCOPE.md` §12/F12 makes a NEW branch 302 only (OVERRIDES marker on
    #3501/#3521)."""
    rc, out = _run_probe(tmp_path, mode)
    assert rc == 1, f"mode={mode} must fail — the probe is not pinning hop 1\n{out}"
    assert "expected a 302" in out or "redirected to" in out, (
        f"mode={mode} failed without naming the hop-1 contract: {out}"
    )


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
    # project: {name: (kind, envs)}
    "premise-labs": {
        "SESSIONS": ("required", ["production", "preview"]),
        "SUPABASE_URL": ("required", ["production"]),
        "SUPABASE_ANON_KEY": ("required", ["production"]),
        "SUPABASE_SERVICE_ROLE_KEY": ("required", ["production"]),
        "OPENROUTER_API_KEY": ("required", ["production"]),
        # recommended: correct in-source default
        "APP_ORIGIN": ("recommended", ["production"]),
        "AUTH_CALLBACK_URL": ("recommended", ["production"]),
        # required: SET in production, and website/apps/dashboard/functions/api/v1/[[path]].ts
        # answers `503 proxy_not_configured` without it (verified live: after it was
        # set, /api/v1/teams returns 401 not_signed_in instead). The old
        # `recommended` note said "the moment a client calls it" — that is now.
        "API_ORIGIN": ("required", ["production"]),
        # recommended: cloudflare-purge.ts is best-effort and fail-open by design
        "CF_API_TOKEN": ("recommended", ["production"]),
        "CF_ZONE_ID": ("recommended", ["production"]),
        # #2409: the public contact form's intake seam. `recommended` NOT
        # `required`, deliberately — `required` reds the deploy, which would
        # block the very deploy that ships the form. Absence is a visible 503
        # from functions/contact/submit.ts with the mailto fallback, not an
        # outage of the rest of the site. Promote once the endpoint is bound.
        "CONTACT_INTAKE_URL": ("recommended", ["production"]),
        # An INBOUND credential only: it can submit an item and nothing else
        # (no send, no read) — distinct from any send-capable provider key.
        "CONTACT_INTAKE_SECRET": ("recommended", ["production"]),
    },
    # #4054: the BFF appended a second project. SESSIONS points at the SAME
    # account-level tortoise-sessions database; the env vars are what the moved
    # Functions read (verified with `rg -n 'env\.[A-Z_]+'`).
    "tortoise-dashboard": {
        "SESSIONS": ("required", ["production", "preview"]),
        "SUPABASE_URL": ("required", ["production"]),
        "SUPABASE_ANON_KEY": ("required", ["production"]),
        # Deliberately ABSENT: SUPABASE_SERVICE_ROLE_KEY. No Function under
        # website/apps/dashboard/functions/ reads it, and the one flow needing a
        # privileged admin call (#801 email signup) does not make it in the BFF —
        # /auth/signup proxies POST {API_ORIGIN}/v1/signup/email, so the key stays
        # on the API. Declaring it would put a privileged secret on the public app
        # origin. Pinned by this table so re-adding it fails loudly.
        # recommended: correct in-source default, same as premise-labs
        "APP_ORIGIN": ("recommended", ["production"]),
        "AUTH_CALLBACK_URL": ("recommended", ["production"]),
        # #4171: the /blog/api/* Token Handler proxy. ``recommended`` because the
        # proxy falls back to https://tortoise.premiselabs.co in code.
        "BLOG_ORIGIN": ("recommended", ["production"]),
        # required: api/v1/[[path]].ts answers `503 proxy_not_configured` without it
        "API_ORIGIN": ("required", ["production"]),
    },
}


def test_the_manifest_classification_matches_the_reviewed_table() -> None:
    """Flipping OPENROUTER_API_KEY to `recommended`, or dropping `preview` from
    SESSIONS, previously left the whole suite green — the fixtures derive from
    whatever the manifest currently says. Now the table is pinned PER PROJECT,
    so a kind change on the new project cannot hide behind the old one."""
    actual = {
        project["project"]: {
            spec["name"]: (
                spec.get("kind", "required"),
                spec.get("envs", ["production"]),
            )
            for spec in project["bindings"]
        }
        for project in _manifest()["projects"]
    }
    assert actual == EXPECTED_CLASSIFICATION


def test_the_classification_table_covers_every_binding() -> None:
    """Non-vacuity: the table above must not silently miss a new binding OR a
    new project — the whole point of the multi-project extension (#4054)."""
    manifest = _manifest()
    for project in manifest["projects"]:
        names = {spec["name"] for spec in project["bindings"]}
        expected = EXPECTED_CLASSIFICATION.get(project["project"])
        assert expected is not None, (
            f"project {project['project']!r} is not in EXPECTED_CLASSIFICATION — a "
            "new project was added without reviewing its bindings"
        )
        assert names == set(expected), (
            "a binding was added or removed without updating "
            f"EXPECTED_CLASSIFICATION[{project['project']!r}]"
        )
    assert {p["project"] for p in manifest["projects"]} == set(EXPECTED_CLASSIFICATION), (
        "a project was added or removed without updating EXPECTED_CLASSIFICATION"
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
    monkeypatch.setattr(
        cpb, "fetch_configs", lambda _account, project, _token: _complete_configs(project)
    )
    rc = cpb.main(
        ["--manifest", str(MANIFEST_PATH), "--account-id", "a",
         "--api-token", "t", "--json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # must not raise
    assert payload["missing_required"] == []
    # BOTH projects are reported per-project, not collapsed to one.
    assert [p["project"] for p in payload["projects"]] == [
        "premise-labs",
        "tortoise-dashboard",
    ]
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
    # The failure names BOTH projects: neither can hide behind the other.
    assert {label.split(":", 1)[0] for label in payload["missing_required"]} == {
        "premise-labs",
        "tortoise-dashboard",
    }
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
    # Read the COMMENT-STRIPPED block, not the raw `run`: this test exists to
    # prove the guard is in the CODE, and a comment-only guard would otherwise
    # satisfy it (the defect class already fixed at the LEAK_PROBE bound test).
    run = _step_code(PROBE)
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


def _run_checker_script(
    tmp_path: Path, script_path: Path, exits: str
) -> tuple[int, str, int]:
    """Run a shipped preflight block under `bash -e` with `check_stub` on PATH.

    Shared by the premise-labs preflight and the #4054 tortoise-dashboard
    preflight, so both are proven to invoke the checker and to propagate its
    exit code.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
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
        ["bash", "-e", str(script_path)],
        capture_output=True,
        text=True,
        env=env,
    )
    calls_file = tmp_path / "checker_calls"
    calls = int(calls_file.read_text()) if calls_file.exists() else 0
    return r.returncode, r.stdout + r.stderr, calls


def _run_preflight(tmp_path: Path, exits: str) -> tuple[int, str, int]:
    return _run_checker_script(tmp_path, _preflight_script(tmp_path), exits)


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
    """The manifest must not live under `website/`.

    `.wranglerignore` is NOT honoured by `wrangler pages deploy` (verified four
    ways in cycle 4, including a live 200 on
    https://tortoise.premiselabs.co/.wranglerignore) — which is why #3620
    replaced `pages deploy .` with a staged upload root. That staging is a
    DENYLIST: a NEW top-level file added under `website/` IS uploaded unless it
    is explicitly excluded. Keeping the binding inventory class of file in
    `config/` is what makes it immune to that residual risk.
    """
    assert MANIFEST_PATH.exists(), f"manifest missing: {MANIFEST_PATH}"
    assert MANIFEST_PATH.parent.name == "config", (
        f"the manifest is at {MANIFEST_PATH} — a file under website/ is staged for "
        "public upload unless the deploy step explicitly excludes it"
    )
    assert "required-bindings" not in [
        p.name for p in (REPO / "website").glob("required-bindings*")
    ]


# ---------------------------------------------------------------------------
# #3620: the Pages upload root is STAGED, not `website/` wholesale.
#
# `wrangler pages deploy .` published EVERY file under `website/`, and
# `website/.wranglerignore` had no effect at all (the Pages upload path uses a
# hardcoded IGNORE_LIST and reads no ignore file; `wranglerignore` appears in 0
# files across every installed bundle). Verified live: `/.wranglerignore`,
# `/README.md`, `/website_architecture.md`, `/migrations/0001_auth_sessions.sql`
# and `/apps/dashboard/src/main.jsx` all answered 200.
#
# The deploy step now stages an explicit upload set and the new leak probe
# asserts each of those paths 404s.
#
# Every test below EXECUTES the shipped shell against a synthetic `website/`
# tree with stubbed `npx`/`curl`. A string assertion cannot distinguish a
# staging step from a comment describing one — cycle 3 of #3616 proved that on
# this exact file, and #3620 is the second-order consequence.
# ---------------------------------------------------------------------------

#: The entries under `website/` at the time of #3620 — the public surface, the
#: internal paths that were leaking, and a committed `node_modules` tree (as
#: `website/apps/dashboard/node_modules` really is).
#:
#: #4171: the generated `admin/` tree is GONE from this project — the console
#: moved to the app origin and is staged by the `deploy-dashboard` job into
#: `website/apps/dashboard/dist/admin/`.
_WEBSITE_FIXTURE = (
    "_redirects",
    "_headers",
    "index.html",
    "404.html",
    "product.html",
    "privacy.html",
    "tos.html",
    # #4054: `welcome.html` (plus `signup.html` / `invite-accept.html`) moved to
    # the `tortoise-dashboard` project (`website/apps/dashboard/public/`) and is
    # no longer staged by the `premise-labs` upload — the classification table
    # below reflects that. It is deliberately NOT in this fixture: a fixture that
    # still emits it would stage a file the reviewed table says is excluded.
    "robots.txt",
    "logo.png",
    "consent.js",
    "assets/app.css",
    "blog/blog.js",
    "blog/og-image.png",
    "functions/_middleware.ts",
    "functions/auth/start.ts",
    "functions/api/session.ts",
    "apps/dashboard/src/main.jsx",
    "apps/dashboard/deploy.sh",
    "apps/dashboard/package.json",
    "apps/dashboard/node_modules/react/index.js",
    "apps/blog-admin/src/App.tsx",
    "apps/blog-admin/package.json",
    # A top-level `website/node_modules` (a local `npm install` in website/).
    # `/apps/` already prunes the dashboard's copy; this entry is what makes the
    # separate `--exclude='node_modules/'` load-bearing rather than decorative.
    "node_modules/some-pkg/index.js",
    "migrations/0001_auth_sessions.sql",
    ".wranglerignore",
    "README.md",
    "website_architecture.md",
    "2479-re-auth-implementation-plan.md",
)

def _expected_public(rels) -> set[str]:
    """The public set the REVIEWED classification implies for `rels`.

    `rels` are paths RELATIVE to `website/` — the synthetic `_WEBSITE_FIXTURE`
    tuple and `git ls-files website` differ only by that prefix. A path is public
    iff its top-level entry is classified `public` and it is not caught by a
    non-top-level rule the deploy step applies (`*.md` and `node_modules/` at any
    depth).

    This is derived from the tree + the reviewed table, NOT a hand-written sample
    of "load-bearing" entries. The previous 14-of-26 sample (`_STAGED_MUST_EXIST`)
    is exactly why `--exclude='consent.js'` (M22) and `--exclude='*.xml'` (M23)
    could drop a public file with the whole suite green.
    """
    expected: set[str] = set()
    for rel in rels:
        if _WEBSITE_TOP_LEVEL_STAGED.get(rel.split("/", 1)[0]) is not True:
            continue
        if rel.endswith(".md") or "node_modules" in rel.split("/"):
            continue
        expected.add(rel)
    return expected


def _assert_stage_matches(actual: set[str], expected: set[str], out: str) -> None:
    """Assert the stage is EXACTLY the classified public set, both directions.

    #4171: the generated `admin/` tree (once a special case here) is gone — it
    is staged into the app-origin project's `dist/` now, not into this upload.
    """
    missing = sorted(expected - actual)
    added = sorted(actual - expected)
    assert not missing, (
        "PUBLIC files were dropped by the denylist staging — the leak probe "
        f"asserts 404s only, so it can never notice this (#3620): {missing}\n{out}"
    )
    assert not added, (
        f"internal files were staged for public upload (#3620): {added}\n{out}"
    )

#: Entries that MUST NOT be staged — the live-verified #3620 leak.
_STAGED_MUST_NOT_EXIST = (
    ".wranglerignore",
    "README.md",
    "website_architecture.md",
    "2479-re-auth-implementation-plan.md",
    "migrations/0001_auth_sessions.sql",
    "apps/dashboard/src/main.jsx",
    "apps/dashboard/deploy.sh",
    "apps/dashboard/package.json",
    "apps/dashboard/node_modules/react/index.js",
    "apps/blog-admin/src/App.tsx",
    "apps/blog-admin/package.json",
    "node_modules/some-pkg/index.js",
)

#: The paths the leak probe asserts on. Deliberately the SAME set issue #3620
#: enumerated from production, because a probe that drifts from the evidence
#: stops being evidence.
LEAK_PATHS = (
    "/.wranglerignore",
    "/README.md",
    "/website_architecture.md",
    "/2479-re-auth-implementation-plan.md",
    "/migrations/0001_auth_sessions.sql",
    "/apps/dashboard/src/main.jsx",
    "/apps/blog-admin/src/App.tsx",
    "/apps/dashboard/package.json",
    "/apps/blog-admin/package.json",
)

#: Stub `npx`. Records its argv AND its working directory, so the harness can
#: prove what the shipped command was pointed at — and that wrangler's cwd (which
#: is where it resolves `functions/`) IS the upload root — plus that it never ran
#: when a survival guard failed.
STUB_NPX = r"""#!/bin/bash
printf '%s\n' "$@" > "$STUB_DIR/npx_args"
pwd -P > "$STUB_DIR/npx_pwd"
exit 0
"""

#: Stub `curl` for the leak probe. STUB_SERVED lists paths that still answer 200;
#: STUB_ERROR lists paths that answer 500; STUB_TRANSPORT_FAIL fails the
#: connection; STUB_SERVED_CALLS limits serving to the first N probes (the "Pages
#: is still serving the previous, leaking deployment" shape). Every requested path
#: is recorded so the harness can prove the loop probed ALL of them, on the right
#: number of attempts, and is reachable under `bash -e`.
STUB_LEAK_CURL = r"""#!/bin/bash
out=; fmt=; url=
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift 2;;
    -w) fmt=$2; shift 2;;
    -m) shift 2;;
    -s|-S|-sS|-f|--fail) shift;;
    *) url=$1; shift;;
  esac
done
path="/${url#*//*/}"
echo "$path" >> "$STUB_DIR/probed"
if [ "$STUB_TRANSPORT_FAIL" = "1" ]; then
  echo "curl: (7) Failed to connect to host" >&2
  printf '000'
  exit 7
fi
code=404
n=$(wc -l < "$STUB_DIR/probed")
if [ -z "$STUB_SERVED_CALLS" ] || [ "$n" -le "$STUB_SERVED_CALLS" ]; then
  for s in $STUB_SERVED; do [ "$s" = "$path" ] && code=200; done
  for s in $STUB_ERROR; do [ "$s" = "$path" ] && code=500; done
fi
[ -n "$out" ] && : > "$out"
case "$fmt" in *http_code*) printf '%s' "$code";; esac
exit 0
"""


def _write_website_fixture(root: Path, omit: str | None = None) -> Path:
    """Build a synthetic `website/` tree; `omit` drops one entry (or dir)."""
    site = root / "website"
    for rel in _WEBSITE_FIXTURE:
        if omit and (rel == omit or rel.startswith(f"{omit}/")):
            continue
        f = site / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n", encoding="utf-8")
    return site


def _stub_bin(root: Path, name: str, body: str) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / name
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return bin_dir


def _run_deploy(
    tmp_path: Path, omit: str | None = None, set_runner_temp: bool = True
) -> tuple[int, str, Path, Path]:
    """Execute the shipped Deploy step with the shell exactly as CI runs it.

    `bash -e` mirrors GitHub Actions' default Linux shell. The run block is the
    comment-stripped shipped text — comments are inert, so executing without them
    proves the CODE carries the behaviour, not the prose.
    """
    bin_dir = _stub_bin(tmp_path, "npx", STUB_NPX)
    site = _write_website_fixture(tmp_path, omit)
    script = tmp_path / "deploy.sh"
    script.write_text(_step_code(DEPLOY), encoding="utf-8")

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
    }
    if set_runner_temp:
        env["RUNNER_TEMP"] = str(tmp_path)
    else:
        env.pop("RUNNER_TEMP", None)

    r = subprocess.run(
        ["bash", "-e", str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    return r.returncode, r.stdout + r.stderr, tmp_path / "pages-upload", site


def test_the_deploy_stages_a_controlled_upload_set(tmp_path) -> None:
    """The whole point of #3620: WHAT gets uploaded is decided by the repo."""
    rc, out, stage, site = _run_deploy(tmp_path)
    assert rc == 0, f"the shipped deploy step failed:\n{out}"

    actual = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
    _assert_stage_matches(actual, _expected_public(_WEBSITE_FIXTURE), out)

    # Non-vacuity: the source tree really did contain each internal path, so the
    # exclusion (not a missing fixture file) is what kept it out of the stage.
    for rel in _STAGED_MUST_NOT_EXIST:
        assert (site / rel).exists(), f"fixture is missing {rel} — assertion vacuous"
    leaked = [rel for rel in _STAGED_MUST_NOT_EXIST if (stage / rel).exists()]
    assert not leaked, f"internal paths were staged for public upload: {leaked}\n{out}"


def test_the_deploy_clears_a_stale_stage_directory(tmp_path) -> None:
    """`rm -rf "$STAGE"` is load-bearing: a stale file in `$RUNNER_TEMP/pages-upload`
    would otherwise survive `mkdir -p` + rsync and be uploaded.

    `$RUNNER_TEMP` is fresh on a hosted runner, but the step must not depend on
    that — a local rerun and a self-hosted runner both reuse it. Before this test
    the `rm -rf` was pinned by nothing.
    """
    stale = tmp_path / "pages-upload" / "stale-internal.txt"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("stale\n", encoding="utf-8")
    rc, out, stage, _site = _run_deploy(tmp_path)
    assert rc == 0, out
    assert not (stage / "stale-internal.txt").exists(), (
        'a stale file survived into the upload root — `rm -rf "$STAGE"` is gone'
    )


def _tracked_website_files() -> list[str]:
    """Every tracked path under `website/`, RELATIVE to `website/`."""
    out = subprocess.run(
        ["git", "ls-files", "website"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    return [p[len("website/") :] for p in out if p.startswith("website/")]


def _copy_tracked_website(root: Path) -> None:
    """Reproduce the CI checkout for the staging step: every TRACKED file under
    `website/` (copying the `git ls-files` list, so untracked local artifacts stay
    out of the comparison).

    #4171: no generated `admin/` tree is added — the console is built and staged
    by the `deploy-dashboard` job into the app-origin project's `dist/`.
    """
    for rel in _tracked_website_files():
        src = REPO / "website" / rel
        # A tracked path can be absent from the WORKING TREE mid-change (the
        # #4054 move deleted several tracked files before committing). CI checks
        # out the committed tree, where `git ls-files` no longer lists them, so
        # skipping a missing path reproduces the checkout instead of crashing.
        if not src.exists():
            continue
        dst = root / "website" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def test_the_staged_upload_root_equals_the_classified_public_tree(tmp_path) -> None:
    """COMPLETENESS over the REAL tree, not a sample: run the shipped staging step
    and assert the stage is EXACTLY `tracked(website) − classified_excluded`.

    Both directions must hold:
      * a PUBLIC file missing from the stage is a silent 404 the leak probe can
        never see (it asserts 404s only). A 14-of-26 sampled hand-list let
        `--exclude='consent.js'` (M22) drop the consent banner + PostHog init,
        and `--exclude='*.xml'` (M23) drop both public sitemaps — with the whole
        suite green;
      * an INTERNAL file present in the stage is the #3620 leak itself.

    The expected set is derived from `git ls-files website` and the reviewed
    classification, so it grows and shrinks with the real tree rather than needing
    a hand-list edit.
    """
    _copy_tracked_website(tmp_path)
    bin_dir = _stub_bin(tmp_path, "npx", STUB_NPX)
    script = tmp_path / "deploy.sh"
    script.write_text(_step_code(DEPLOY), encoding="utf-8")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
        "RUNNER_TEMP": str(tmp_path),
    }
    r = subprocess.run(
        ["bash", "-e", str(script)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"the shipped deploy step failed on the real tree:\n{out}"

    stage = tmp_path / "pages-upload"
    actual = {p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file()}
    _assert_stage_matches(actual, _expected_public(_tracked_website_files()), out)


def test_the_deploy_uploads_the_staged_directory_not_website(tmp_path) -> None:
    """BOTH halves of the upload root must be the staged directory.

    Assets: the directory argument. Functions: `wrangler pages deploy` resolves
    them from the **process cwd** (`path.join(process.cwd(), "functions")` —
    there is no `--functions-directory` flag on `pages deploy`, only the global
    `--cwd`), and its IGNORE_LIST never uploads `functions/` as an asset.

    Running from the repo root therefore deploys a site with NO Functions —
    every /auth/*, /api/*, /blog/* and /admin/* route 404s — while
    `test -d "$STAGE/functions"` still passes, because the ASSET tree has it.
    That is exactly the silent-dead-auth failure this change exists to avoid
    (the old `cd website` + `deploy .` got it right by accident), so the cwd is
    pinned here by execution, not by reading the workflow.
    """
    rc, out, stage, _site = _run_deploy(tmp_path)
    assert rc == 0, out
    args = (tmp_path / "npx_args").read_text(encoding="utf-8").splitlines()
    assert args[:4] == ["--yes", "wrangler@4", "pages", "deploy"], args
    target = args[4]
    assert target == str(stage), (
        f"the deploy was pointed at {target!r}, not the staged upload root {stage}"
    )
    assert target not in (".", "./", "website", "website/"), (
        "the deploy still uploads website/ wholesale — every file under it is served"
    )
    assert args[5:] == ["--project-name=premise-labs", "--branch=main"], args
    # The cwd is where wrangler looks for `functions/`. If it is not the stage,
    # the deploy has no Functions — a green deploy with dead auth.
    ran_in = (tmp_path / "npx_pwd").read_text(encoding="utf-8").strip()
    assert ran_in == os.path.realpath(str(stage)), (
        f"wrangler ran with cwd={ran_in!r}, not the upload root — it would resolve "
        "`<cwd>/functions`, find none, and deploy NO Functions"
    )


@pytest.mark.parametrize(
    ("omitted", "needle"),
    [
        # Each needle is unique to ITS OWN guard's diagnostic. `functions/` would
        # also match the `_middleware.ts` guard's message, so deleting the
        # directory guard would still look "caught".
        ("functions", "compiles Functions from the cwd"),
        ("functions/_middleware.ts", "host routing and the /admin"),
        ("_redirects", "the redirect contract is gone"),
        ("_headers", "the security-header contract is gone"),
    ],
)
def test_the_deploy_refuses_to_upload_when_a_load_bearing_entry_is_missing(
    tmp_path, omitted: str, needle: str
) -> None:
    """The denylist is only safe because these guards fail LOUD, and BEFORE the
    upload.

    A denylist was chosen over an allowlist precisely because these are easy to
    forget. If a guard is removed — or moved after the `npx` call — the failure
    returns to its silent form: a green deploy with dead auth. #4171 removed the
    `admin/` guard along with the console: the tree is no longer staged here.
    """
    rc, out, _stage, _site = _run_deploy(tmp_path, omit=omitted)
    assert rc != 0, f"staging {omitted} away did not fail the step:\n{out}"
    assert needle in out, f"no diagnostic naming {needle}:\n{out}"
    assert not (tmp_path / "npx_args").exists(), (
        "the deploy ran anyway — a survival guard must abort BEFORE the upload"
    )


def test_the_stage_path_refuses_an_empty_RUNNER_TEMP(tmp_path) -> None:
    """`STAGE="$RUNNER_TEMP/pages-upload"` with RUNNER_TEMP unset is
    `/pages-upload` — and the very next command is `rm -rf "$STAGE"`.

    Runners always set the variable; a local invocation does not, and the failure
    mode is deleting a path derived from an empty value. `${VAR:?}` fails fast.
    """
    rc, out, _stage, _site = _run_deploy(tmp_path, set_runner_temp=False)
    assert rc != 0, f"an unset RUNNER_TEMP must fail the step:\n{out}"
    assert "RUNNER_TEMP" in out, out
    assert not (tmp_path / "npx_args").exists(), out


def test_the_workflow_asserts_the_upload_root_in_the_deploy_job() -> None:
    """Wiring: the leak probe must exist, and run in the SAME job as the deploy.

    A step defined in another job would be here to satisfy a reader, not the
    deploy — `verify-legal` runs only after the `deploy` job succeeds.
    """
    names = [s.get("name", "") for s in _deploy_steps()]
    assert LEAK_PROBE in names, "the post-deploy leak assertion is gone"
    assert names.index(DEPLOY) < names.index(LEAK_PROBE), (
        "the leak assertion must run AFTER the upload"
    )


def test_the_leak_assertion_cannot_be_masked_by_the_sign_in_probe() -> None:
    """A red sign-in probe must not skip the leak check.

    The two assertions are independent, and a publicly served internal file
    cannot be un-published by retrying the other one — so the leak step carries
    `if: always()`.
    """
    step = next(s for s in _deploy_steps() if s.get("name") == LEAK_PROBE)
    assert step.get("if") == "always()", (
        "the leak assertion is skippable when the sign-in probe fails"
    )


def _leak_script(tmp_path: Path) -> Path:
    """The shipped leak probe with ONLY these benign rewrites:
      - 10 attempts -> 3, sleep 15 -> sleep 0 (so retry cases are fast)
    The control flow under test — the `|| curl_rc=$?` guard, the exact-404 case
    arms, the per-attempt accumulation, the retry bound and the final `exit 1` —
    is untouched. The BOUND is rewritten too, so the retry is genuinely exercised
    rather than pinned by a source string (the weakness this file was caught on
    twice already).
    """
    rewritten = (
        _step_code(LEAK_PROBE)
        .replace("MAX_ATTEMPTS=10", "MAX_ATTEMPTS=3")
        .replace("sleep 15", "sleep 0")
    )
    assert "MAX_ATTEMPTS=3" in rewritten, "the retry bound was not rewritten"
    assert "MAX_ATTEMPTS=10" not in rewritten, "a second bound was left at 10"
    p = tmp_path / "leak.sh"
    p.write_text(rewritten, encoding="utf-8")
    return p


def _run_leak_probe(
    tmp_path: Path,
    served: str = "",
    error: str = "",
    transport_fail: bool = False,
    served_calls: int | None = None,
) -> tuple[int, str, list[str]]:
    bin_dir = _stub_bin(tmp_path, "curl", STUB_LEAK_CURL)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
        "STUB_SERVED": served,
        "STUB_ERROR": error,
        "STUB_TRANSPORT_FAIL": "1" if transport_fail else "0",
        "STUB_SERVED_CALLS": "" if served_calls is None else str(served_calls),
    }
    r = subprocess.run(
        ["bash", "-e", str(_leak_script(tmp_path))],
        capture_output=True,
        text=True,
        env=env,
    )
    probed_file = tmp_path / "probed"
    probed = probed_file.read_text(encoding="utf-8").split() if probed_file.exists() else []
    return r.returncode, r.stdout + r.stderr, probed


def test_the_leak_probe_passes_when_every_internal_path_is_404(tmp_path) -> None:
    rc, out, probed = _run_leak_probe(tmp_path)
    assert rc == 0, out
    # Non-vacuity: the loop must have ACTUALLY probed every declared path. A
    # probe that requests nothing passes every other assertion here.
    assert probed == list(LEAK_PATHS), probed
    assert "::error::" not in out, out


@pytest.mark.parametrize("path", LEAK_PATHS)
def test_the_leak_probe_fails_for_each_path_that_is_still_served(tmp_path, path: str) -> None:
    """Every path #3620 enumerated must independently red the deploy."""
    rc, out, _probed = _run_leak_probe(tmp_path, served=path)
    assert rc == 1, f"{path} still served but the probe exited {rc}:\n{out}"
    assert "::error::" in out, out
    assert f"{path}(HTTP 200)" in out, out
    assert "publicly served" in out, out
    assert "#3620" in out, out


def test_the_leak_probe_reports_every_leak_in_one_run(tmp_path) -> None:
    """The loop must CONTINUE after a leak, and the failure must name every path.

    Exiting on the first hit would report one path per re-run, so the operator
    never sees the whole exposed set — and the loop must stay reachable under
    `bash -e` to report anything at all.
    """
    rc, out, probed = _run_leak_probe(tmp_path, served=" ".join(LEAK_PATHS))
    assert rc == 1
    for path in LEAK_PATHS:
        assert f"{path}(HTTP 200)" in out, out
    # Every path on every attempt: the loop never short-circuits, and the retry
    # really ran to its bound.
    assert probed == list(LEAK_PATHS) * 3, probed


def test_the_leak_probe_retries_because_pages_can_serve_the_previous_deploy(tmp_path) -> None:
    """Why the probe polls rather than probing once.

    Pages can briefly serve the PREVIOUS deployment after upload (the sign-in
    probe above documents the same lag). That cuts both ways: on the deploy that
    INTRODUCES this fix the previous deployment is the leaking one (a single-shot
    probe reds the fix), and on a deploy that re-introduces a leak the previous
    deployment is the clean one (a single-shot probe PASSES the regression —
    fail-open). Here the leak is visible for exactly one attempt.
    """
    rc, out, probed = _run_leak_probe(
        tmp_path, served=LEAK_PATHS[1], served_calls=len(LEAK_PATHS)
    )
    assert rc == 0, f"a leak that clears on the next attempt must not fail:\n{out}"
    assert probed == list(LEAK_PATHS) * 2, probed
    assert "::warning::" in out, "the transient was not reported at all"


def test_the_leak_probe_refuses_a_non_404_answer(tmp_path) -> None:
    """`curl -sf` — the obvious spelling — treats a 5xx as a PASS.

    A 5xx means the file could not be read back, not that it is absent, so the
    probe asserts 404 EXACTLY. This is the assertion that separates the gate from
    a one-line `curl -sf` check.
    """
    rc, out, _probed = _run_leak_probe(tmp_path, error=LEAK_PATHS[1])
    assert rc == 1, f"a 500 must not pass the leak check:\n{out}"
    assert "did not return 404 (HTTP 500)" in out, out
    assert f"{LEAK_PATHS[1]}(HTTP 500)" in out, out
    assert "publicly served" not in out, out


def test_the_leak_probe_fails_closed_on_a_transport_failure(tmp_path) -> None:
    """Not knowing is not the same as knowing it is fine — the same contract as
    `check_pages_bindings.main()`'s exit 2."""
    rc, out, probed = _run_leak_probe(tmp_path, transport_fail=True)
    assert rc == 1, f"a leak check that could not run must not pass:\n{out}"
    assert "could not verify" in out, out
    assert "(unverifiable)" in out, out
    # `bash -e` must not abort the substitution: every path on every attempt.
    assert probed == list(LEAK_PATHS) * 3, probed


def test_the_leak_probe_harness_exercises_the_retry_bound() -> None:
    """The harness must rewrite the bound, not merely the sleep.

    If it left `MAX_ATTEMPTS=10`, the retry cases below would take 10 rounds and
    the bound would be pinned only by a source string — the failure mode this
    file has already been caught on twice.
    """
    import inspect

    assert "MAX_ATTEMPTS=10" in _step_code(LEAK_PROBE), (
        "the shipped retry bound changed shape — update _leak_script and this test"
    )
    src = inspect.getsource(_leak_script)
    assert "MAX_ATTEMPTS=10" in src and "MAX_ATTEMPTS=3" in src
    assert "sleep 15" in src and "sleep 0" in src


#: Every top-level entry under `website/` and whether the Pages upload STAGES it.
#: Single-sourced from `config/pages-upload-classification.txt` — the SAME table
#: the deploy job's pre-upload preflight reads (`tools/check_pages_upload_root.py`)
#: — so the test's expectations and the deploy gate cannot drift.
#:
#: `admin/` is deliberately absent: it used to be generated into `website/` by
#: the blog-admin build step and was never tracked. #4171 moved the console to
#: the app-origin project, which stages it into `website/apps/dashboard/dist/admin/`
#: — so this upload never sees an `admin/` tree at all now.
_WEBSITE_TOP_LEVEL_STAGED = cpur.load_classification(UPLOAD_CLASSIFICATION_PATH)


def _rsync_exclude_patterns(code: str) -> list[str]:
    """Every `--exclude='…'` value in a shipped shell run block."""
    return re.findall(r"--exclude='([^']*)'", code)


def _rsync_pattern_matches(pattern: str, name: str, is_dir: bool) -> bool:
    """rsync exclude semantics for a TOP-LEVEL entry.

    The rules that matter for this step's patterns:
      * a trailing `/` matches directories only;
      * a leading `/` anchors the pattern to the transfer root, so `/apps/` does
        NOT match a nested `x/apps/`;
      * a pattern with NO internal `/` matches the BASENAME at any depth, so
        `*.md` and `node_modules/` match top-level entries too.

    Matching the anchored literal only — the old probe — misses an unanchored
    `--exclude='consent.js'`, which rsync DOES apply and which drops the consent
    banner + PostHog init (M22), and `--exclude='*.xml'`, which drops both public
    sitemaps (M23).
    """
    dir_only = pattern.endswith("/")
    pat = pattern.rstrip("/")
    if dir_only and not is_dir:
        return False
    if pat.startswith("/"):
        # Anchored at the transfer root: only a root-level entry can match, and a
        # remaining `/` would be a sub-path this helper does not model.
        return "/" not in pat[1:] and fnmatch.fnmatchcase(name, pat[1:])
    return fnmatch.fnmatchcase(name, pat)


def test_every_top_level_entry_under_website_is_classified() -> None:
    """The enumeration acceptance criterion, made executable.

    #3620 required the publicly served set to be *decided*, not inherited. The
    deploy step decides it with a DENYLIST, so a new top-level entry is uploaded
    unless it is excluded. This test makes the decision forced instead of silent:
    adding `website/internal/` fails here until it is classified. The table is
    single-sourced from `config/pages-upload-classification.txt` — the same file
    the deploy preflight reads.
    """
    unclassified, stale = cpur.compare(
        cpur.tracked_top_level(), _WEBSITE_TOP_LEVEL_STAGED
    )
    assert not unclassified, (
        "a top-level entry under website/ is not classified — an unclassified "
        "entry is staged for public upload unless the deploy step excludes it "
        f"(#3620): {unclassified}"
    )
    assert not stale, (
        f"a classification row names a path that no longer exists (#3620): {stale}"
    )

    # Non-vacuity: the table must carry both answers, or it pins nothing.
    assert any(_WEBSITE_TOP_LEVEL_STAGED.values()), "no entry is classified public"
    assert not all(_WEBSITE_TOP_LEVEL_STAGED.values()), "no entry is classified excluded"

    # Every entry classified NOT staged must actually be matched by a shipped
    # `--exclude`, and every public entry must NOT be — otherwise this table is a
    # claim, not a pin. Patterns are evaluated with rsync semantics (basename
    # match when unanchored), not an anchored-substring probe: an unanchored
    # `--exclude='consent.js'` or `--exclude='*.xml'` really does drop the file.
    patterns = _rsync_exclude_patterns(_step_code(DEPLOY))
    for name, staged in _WEBSITE_TOP_LEVEL_STAGED.items():
        is_dir = (REPO / "website" / name).is_dir()
        excluded = any(_rsync_pattern_matches(p, name, is_dir) for p in patterns)
        assert excluded != staged, (
            f"{name} is classified {'public' if staged else 'excluded'} but the "
            f"deploy step {'excludes' if excluded else 'does not exclude'} it "
            f"(patterns: {patterns})"
        )


def test_the_upload_root_preflight_runs_before_the_deploy() -> None:
    """The deploy job must RUN the classification check before it stages/upload.

    Without this step the ratchet is only a post-merge detector: a push adding an
    unclassified top-level entry would publish it and only red afterwards (#3620).
    """
    steps = _deploy_steps()
    names = [s.get("name", "") for s in steps]
    check = next(
        (s for s in steps if "check_pages_upload_root.py" in (s.get("run") or "")), None
    )
    assert check is not None, (
        "the deploy job does not run tools/check_pages_upload_root.py — an "
        "unclassified top-level entry would be uploaded on a green deploy (#3620)"
    )
    assert names.index(check["name"]) < names.index(DEPLOY), (
        "the classification check must run BEFORE the upload"
    )


def _upload_preflight_script(tmp_path: Path) -> Path:
    """The shipped upload-root preflight run block with ONLY the checker path
    rewritten to a stub on `PATH` — exactly as `_preflight_script` does for the
    sibling binding preflight. The `bash -e` semantics are left as shipped, so
    the checker's exit code is what aborts the step."""
    step = next(s for s in _deploy_steps() if s.get("name") == UPLOAD_PREFLIGHT)
    rewritten = _strip_bash_comments(step["run"]).replace(
        "python3 tools/check_pages_upload_root.py", "check_upload_stub"
    )
    assert "check_upload_stub" in rewritten, (
        "the upload-root checker invocation was not substituted"
    )
    p = tmp_path / "upload_preflight.sh"
    p.write_text(rewritten, encoding="utf-8")
    return p


def _run_upload_preflight(tmp_path: Path, exits: str) -> tuple[int, str, int]:
    """Run the shipped upload-root preflight under `bash -e` with the checker
    replaced by the recording stub (reuses `STUB_CHECKER`)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    stem = tmp_path / "check_upload_stub_impl"
    stem.write_text(STUB_CHECKER, encoding="utf-8")
    stem.chmod(0o755)
    stub = bin_dir / "check_upload_stub"
    stub.write_text(f'#!/bin/bash\nexec "{stem}"\n', encoding="utf-8")
    stub.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "STUB_DIR": str(tmp_path),
        "STUB_EXITS": exits,
    }
    r = subprocess.run(
        ["bash", "-e", str(_upload_preflight_script(tmp_path))],
        capture_output=True,
        text=True,
        env=env,
    )
    calls_file = tmp_path / "checker_calls"
    calls = int(calls_file.read_text()) if calls_file.exists() else 0
    return r.returncode, r.stdout + r.stderr, calls


@pytest.mark.parametrize(("exits", "want_rc"), [("0", 0), ("1", 1), ("2", 2)])
def test_the_upload_root_preflight_actually_invokes_the_checker(
    tmp_path, exits: str, want_rc: int
) -> None:
    """The anti-neutering assertion for the deploy gate.

    `test_the_upload_root_preflight_runs_before_the_deploy` only compares a
    substring + step index; it never EXECUTES the step. So the whole deploy-time
    classification gate could be turned into a no-op with the suite green:

        run: python3 tools/check_pages_upload_root.py || true
        run: echo 'python3 tools/check_pages_upload_root.py'
        run: python3 tools/check_pages_upload_root.py || exit 0

    Each leaves the substring in place. This test runs the shipped block and
    proves (a) the checker was ACTUALLY invoked and (b) its non-zero exits
    (1 = unclassified/stale, 2 = could-not-determine) abort the step. It is the
    execution-based counterpart of `test_the_preflight_actually_invokes_the_checker`,
    for the one gate this PR adds.
    """
    rc, out, calls = _run_upload_preflight(tmp_path, exits)
    assert calls == 1, (
        "the upload-root preflight never invoked the checker — replacing the "
        "invocation with `echo` leaves every source-level string in place (#3620)"
    )
    assert "STUB_CHECKER_INVOKED:1" in out
    assert rc == want_rc, f"checker exit {exits} must propagate, rc={rc} want={want_rc}\n{out}"


def test_the_deploy_gate_and_the_tests_read_the_same_classification() -> None:
    """One source of truth for the reviewed table.

    The deploy step invokes `check_pages_upload_root.py` with NO arguments, so
    the tool's `DEFAULT_CLASSIFICATION` IS the deploy gate's table, while the
    ratchet above validates `UPLOAD_CLASSIFICATION_PATH`. Repointing either
    constant at a rival table (e.g. one with `consent.js` flipped to `excluded`)
    left the whole suite green while the deploy gate checked an UNREVIEWED set —
    so the two must be the same file, and the step must not override it (#3620).
    """
    assert cpur.DEFAULT_CLASSIFICATION == UPLOAD_CLASSIFICATION_PATH, (
        "tools/check_pages_upload_root.py's DEFAULT_CLASSIFICATION and the "
        "ratchet's UPLOAD_CLASSIFICATION_PATH are different files — the deploy "
        "gate would validate a table the tests never reviewed"
    )
    code = _step_code(UPLOAD_PREFLIGHT)
    assert "--classification" not in code, (
        "the deploy step overrides the classification table, so the tool's "
        "DEFAULT_CLASSIFICATION (the file the ratchet pins) would no longer "
        "govern what the deploy gate checks"
    )


def test_the_upload_root_preflight_reports_an_unclassified_entry() -> None:
    """The deploy gate must red on a new entry, and not on a known one."""
    table = cpur.load_classification(UPLOAD_CLASSIFICATION_PATH)
    tracked = cpur.tracked_top_level()
    unclassified, stale = cpur.compare(tracked | {"brand-new-internal"}, table)
    assert unclassified == ["brand-new-internal"]
    assert stale == []
    # A row whose path no longer exists on disk is stale and must fail too.
    _unclassified, stale = cpur.compare(tracked - {"consent.js"}, table)
    assert stale == ["consent.js"]


def test_the_upload_root_preflight_fails_closed_on_a_malformed_table(tmp_path) -> None:
    """A malformed row is a configuration error, not an unclassified path — it
    must raise rather than be silently skipped into a vacuous comparison."""
    bad = tmp_path / "bad.txt"
    bad.write_text("maybe thing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected"):
        cpur.load_classification(bad)


def test_the_inert_wranglerignore_is_gone() -> None:
    """`website/.wranglerignore` excluded nothing.

    Wrangler's Pages upload path uses a hardcoded IGNORE_LIST and reads no ignore
    file (verified four ways in #3620, including a live 200 on the file itself).
    A config file that appears to enforce a security property but does not is
    worse than no config file — it stops people from checking. The enforced
    mechanism is now the staged upload (deploy step) plus the 404 assertions
    (leak probe), both executed above.
    """
    assert not (REPO / "website" / ".wranglerignore").exists(), (
        "website/.wranglerignore is back — wrangler never reads it, so it claims "
        "a protection that does not exist (#3620)"
    )


# ---------------------------------------------------------------------------
# #4054: the BFF moved onto the `tortoise-dashboard` Pages project, so the
# binding gate now covers TWO projects.
#
# The dashboard had 0 env vars and 0 D1 bindings when the move landed. Without
# these assertions the gate would still report green for `premise-labs` while
# the app's every /auth/* request 503'd — the #3616 outage with a different
# project name in it.
# ---------------------------------------------------------------------------

#: The env vars the dashboard Functions actually READ, recovered from source by
#: `rg -n 'env\.[A-Z_]+' website/apps/dashboard/functions/`.
_BFF_ENV_RE = re.compile(r"\benv\.([A-Z][A-Z0-9_]*)")


def _env_names_read_by_the_bff() -> set[str]:
    root = REPO / "website" / "apps" / "dashboard" / "functions"
    names: set[str] = set()
    for path in sorted(root.rglob("*.ts")):
        names |= set(_BFF_ENV_RE.findall(path.read_text(encoding="utf-8")))
    return names


def test_the_manifest_declares_both_projects() -> None:
    """Well-formedness for BOTH projects (task requirement (c)).

    Each project must carry a name, a non-empty binding list and at least one
    required binding — otherwise `evaluate` would be vacuous for that project
    and the gate would report success without asserting anything.
    """
    projects = _manifest()["projects"]
    assert [p["project"] for p in projects] == ["premise-labs", "tortoise-dashboard"]
    for project in projects:
        assert project["bindings"], project["project"]
        assert any(
            b.get("kind", "required") == "required" for b in project["bindings"]
        ), f"{project['project']} declares no required binding"


def test_tortoise_dashboard_declares_every_env_var_the_moved_bff_reads() -> None:
    """The manifest must be derived from the CODE, not from a guess.

    `website/apps/dashboard/functions/` moved off `premise-labs`; the manifest
    has to name every env var those Functions read, or a 503 ships green. This
    scans the source and fails if the code reads a var the manifest omits.
    """
    project = _project("tortoise-dashboard")
    declared = {b["name"] for b in project["bindings"]}
    read = _env_names_read_by_the_bff()
    # ASSETS is a Pages built-in (the static-asset fetcher); it is never a
    # user-configured binding and must not be demanded in the manifest.
    read -= {"ASSETS"}
    assert read, "no env vars found in the dashboard Functions — the scan is broken"
    assert read <= declared, (
        "the dashboard Functions read env vars the manifest does not declare: "
        f"{sorted(read - declared)}"
    )
    # The moved BFF is useless without the D1 store, and it must be a D1 binding
    # (a same-named KV namespace is not the session store the code opens).
    assert "SESSIONS" in declared
    sessions = next(b for b in project["bindings"] if b["name"] == "SESSIONS")
    assert sessions["type"] == "d1_databases"
    assert sessions["kind"] == "required"


def test_tortoise_dashboard_missing_sessions_is_reported_as_a_failure() -> None:
    """Task requirement (a): a missing required binding on the NEW project fails.

    This is the project's own #3616 shape — it had ZERO D1 bindings when the
    BFF moved onto it.
    """
    project = _project("tortoise-dashboard")
    configs = _complete_configs(project)
    for envname in ("production", "preview"):
        configs[envname].pop("d1_databases", None)
    missing_required, _ = cpb.evaluate(project, configs)
    assert "tortoise-dashboard:production:d1_databases:SESSIONS" in missing_required
    assert "tortoise-dashboard:preview:d1_databases:SESSIONS" in missing_required


def test_tortoise_dashboard_missing_bff_env_var_fails_the_gate() -> None:
    """Every required env var on the new project reds it on its own.

    `API_ORIGIN` is the proxy's configuration; without it /api/v1/* answers
    `503 proxy_not_configured` on a green deploy.
    """
    project = _project("tortoise-dashboard")
    for name in ("SUPABASE_URL", "SUPABASE_ANON_KEY", "API_ORIGIN"):
        configs = _without(_complete_configs(project), "production", "env_vars", name)
        missing_required, _ = cpb.evaluate(project, configs)
        assert f"tortoise-dashboard:production:env_vars:{name}" in missing_required


def test_tortoise_dashboard_present_bindings_pass() -> None:
    """Task requirement (b): a fully-bound NEW project passes."""
    project = _project("tortoise-dashboard")
    missing_required, _ = cpb.evaluate(project, _complete_configs(project))
    assert missing_required == []


def test_the_dashboard_job_has_a_binding_preflight_that_gates_the_deploy() -> None:
    """Wiring: the dashboard job must RUN the check, with the right project, and
    BEFORE the upload.

    A check defined in another job (or after the deploy) is a reader's comfort,
    not a gate. `--project tortoise-dashboard` is asserted so a copy-paste of the
    premise-labs step — which would report green for the wrong project — fails
    here.
    """
    steps = _dashboard_steps()
    names = [s.get("name", "") for s in steps]
    assert DASHBOARD_PREFLIGHT in names, "the dashboard binding preflight is gone"
    assert DASHBOARD_DEPLOY in names
    assert names.index(DASHBOARD_PREFLIGHT) < names.index(DASHBOARD_DEPLOY), (
        "the dashboard binding check must run BEFORE the upload"
    )
    step = next(s for s in steps if s.get("name") == DASHBOARD_PREFLIGHT)
    code = _strip_bash_comments(step["run"])
    assert "tools/check_pages_bindings.py" in code
    assert "config/required-bindings.yml" in code
    assert "--project tortoise-dashboard" in code
    # A gate that cannot fail is worthless: no `|| true`, no continue-on-error.
    assert step.get("continue-on-error") is not True
    assert "|| true" not in code
    env = step.get("env") or {}
    assert "CLOUDFLARE_API_TOKEN" in env
    assert env.get("CLOUDFLARE_ACCOUNT_ID")


def test_the_marketing_preflight_is_scoped_to_premise_labs() -> None:
    """The two surfaces stay independent: the not-yet-configured dashboard must
    not block the marketing deploy (#4054)."""
    code = _step_code(PREFLIGHT)
    assert "--project premise-labs" in code


def test_the_blog_admin_console_is_built_and_staged_by_the_dashboard_job() -> None:
    """#4171: the console ships with the app-origin project, staged into dist/admin/.

    The gate Function reads `/admin/index.html` from ASSETS, and ASSETS for the
    `tortoise-dashboard` project is `website/apps/dashboard/dist/` — so the build
    output must land there, and it must land BEFORE the deploy step.
    """
    steps = _dashboard_steps()
    names = [s.get("name", "") for s in steps]
    build = "Build blog admin SPA (vite) → stage into dist/admin/ (#4171)"
    assert build in names, "the dashboard job no longer builds the blog admin SPA"
    assert names.index(build) < names.index(DASHBOARD_DEPLOY), (
        "the console must be staged BEFORE the dashboard deploy"
    )
    code = _strip_bash_comments(next(s for s in steps if s.get("name") == build)["run"])
    assert "website/apps/blog-admin" in code
    assert "../dashboard/dist/admin" in code, (
        "the console is not staged into the app project's dist/ — the gate's ASSETS read would 404"
    )
    assert "npm run build" in code


def test_the_marketing_job_no_longer_builds_the_blog_admin_console() -> None:
    """#4171: the console left the marketing origin — its build must not linger.

    Two producers staging the same SPA into different projects is how the old
    tortoise.*/admin copy silently came back, and the staging denylist test above
    would still pass (the file simply would not be in this upload).
    """
    names = [s.get("name", "") for s in _deploy_steps()]
    assert not any("blog admin SPA" in n for n in names), (
        "the marketing deploy job still builds the blog admin SPA"
    )


def _dashboard_preflight_script(tmp_path: Path) -> Path:
    """The shipped dashboard preflight with the same benign rewrites as the
    premise-labs one: checker -> stub, pip -> no-op, sleep -> 0."""
    step = next(s for s in _dashboard_steps() if s.get("name") == DASHBOARD_PREFLIGHT)
    rewritten = (
        step["run"]
        .replace("python3 tools/check_pages_bindings.py", "check_stub")
        .replace("python3 -m pip install --quiet 'pyyaml==6.0.3'", ":")
        .replace("sleep 10", "sleep 0")
    )
    assert "check_stub" in rewritten, "the checker invocation was not substituted"
    assert "exit $rc" in rewritten, "the preflight no longer propagates its exit code"
    p = tmp_path / "dashboard_preflight.sh"
    p.write_text(rewritten, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    ("exits", "want_rc", "want_calls"),
    [
        ("0 0 0", 0, 1),   # configured dashboard: one call, passes
        ("1 1 1", 1, 3),   # a REQUIRED binding is absent -> the deploy is blocked
        ("2 2 0", 0, 3),   # transient API failure -> retry saves the deploy
        ("2 2 2", 2, 3),   # API down -> fail closed with the right code
    ],
)
def test_the_dashboard_preflight_shell_behaves_correctly(
    tmp_path, exits: str, want_rc: int, want_calls: int
) -> None:
    """Anti-neutering, execution-based: the dashboard gate must ACTUALLY invoke
    the checker and propagate its non-zero exit.

    Replacing the invocation with `echo`, or appending `|| true`, leaves every
    source-level string in place. Only executing the shipped block catches it —
    and the step's ONLY job is to fail the deploy.
    """
    rc, out, calls = _run_checker_script(
        tmp_path, _dashboard_preflight_script(tmp_path), exits
    )
    assert calls == want_calls, f"exits={exits!r} calls={calls} want={want_calls}\n{out}"
    assert "STUB_CHECKER_INVOKED:1" in out
    assert rc == want_rc, f"exits={exits!r} rc={rc} want={want_rc}\n{out}"


def test_a_duplicate_project_is_rejected(tmp_path) -> None:
    """Two entries for one project would check the same Cloudflare project twice
    and silently drop the second's bindings."""
    p = tmp_path / "m.yml"
    p.write_text(
        "projects:\n"
        "  - project: a\n    bindings:\n"
        "      - name: X\n        kind: required\n        type: env_vars\n"
        "        envs: [production]\n"
        "  - project: a\n    bindings:\n"
        "      - name: Y\n        kind: required\n        type: env_vars\n"
        "        envs: [production]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate project"):
        cpb.load_manifest(p)


def test_a_project_without_a_name_is_rejected(tmp_path) -> None:
    p = tmp_path / "m.yml"
    p.write_text(
        "projects:\n  - bindings:\n"
        "      - name: X\n        kind: required\n        type: env_vars\n"
        "        envs: [production]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing a `project` name"):
        cpb.load_manifest(p)


def test_an_empty_projects_list_is_rejected(tmp_path) -> None:
    p = tmp_path / "m.yml"
    p.write_text("projects: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non-empty list of projects"):
        cpb.load_manifest(p)


def test_a_second_project_with_no_required_binding_is_rejected(tmp_path) -> None:
    """Per-project validation: a project whose bindings are all `recommended`
    would exit 0 forever — a gate that cannot fail, scoped to one project."""
    p = tmp_path / "m.yml"
    p.write_text(
        "projects:\n"
        "  - project: a\n    bindings:\n"
        "      - name: X\n        kind: required\n        type: env_vars\n"
        "        envs: [production]\n"
        "  - project: b\n    bindings:\n"
        "      - name: Y\n        kind: recommended\n        type: env_vars\n"
        "        envs: [production]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no binding is marked"):
        cpb.load_manifest(p)
