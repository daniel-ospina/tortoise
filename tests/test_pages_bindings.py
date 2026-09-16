"""Unit tests for tools/check_pages_bindings.py — the #3616 deploy gate.

Everything here is offline: `evaluate()` is a pure function over the Pages API's
`deployment_configs` shape. The point of these tests is that the GATE CAN FAIL —
a checker that always returns [] would have let #3616 ship again.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import check_pages_bindings as cpb  # noqa: E402

MANIFEST_PATH = REPO / "website" / "required-bindings.yml"


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
# ---------------------------------------------------------------------------

WF_PATH = REPO / ".github" / "workflows" / "deploy-pages.yml"

PREFLIGHT = "Preflight — required Pages bindings exist"
PROBE = "Post-deploy — sign-in is actually reachable"
DEPLOY = "Deploy to Cloudflare Pages (premise-labs project)"


def _deploy_steps() -> list[dict]:
    wf = cpb.yaml.safe_load(WF_PATH.read_text(encoding="utf-8"))
    return wf["jobs"]["deploy"]["steps"]


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
    step = next(s for s in _deploy_steps() if s.get("name") == PREFLIGHT)
    assert "tools/check_pages_bindings.py" in step["run"]
    assert "website/required-bindings.yml" in step["run"]
    assert MANIFEST_PATH.exists()


def test_the_preflight_receives_both_cloudflare_credentials() -> None:
    """Without credentials the checker exits 2 (fail closed), which would look
    like a gate failure on every deploy — so they must be wired."""
    step = next(s for s in _deploy_steps() if s.get("name") == PREFLIGHT)
    env = step.get("env") or {}
    assert "CLOUDFLARE_API_TOKEN" in env
    assert env.get("CLOUDFLARE_ACCOUNT_ID")


def test_the_probe_requires_a_302_and_a_pkce_challenge() -> None:
    """The probe must not accept a bare 200, and must assert the PKCE challenge
    (which is what proves the auth_flows INSERT succeeded)."""
    step = next(s for s in _deploy_steps() if s.get("name") == PROBE)
    run = step["run"]
    assert "session_store_unavailable" in run, "no #3616 diagnostic in the probe"
    assert "code_challenge_method=s256" in run, "the PKCE assertion is gone"
    assert "302" in run
    # Probed at the documented endpoint — the one whose FIRST check is
    # `if (!env.SESSIONS)` and therefore actually touches the store.
    assert "/auth/start" in run


def test_the_gate_files_select_a_surface_so_their_test_runs() -> None:
    """The gate's own files must select a surface, or a PR editing the gate's
    logic (or downgrading a binding) runs NO gate test — the #3616 pattern one
    level up. Verified against the real selector."""
    sys.path.insert(0, str(REPO / "tools"))
    import ci_selection as cs

    manifest = cs.load_manifest()
    for path in (
        "tools/check_pages_bindings.py",
        "website/required-bindings.yml",
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
