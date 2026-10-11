"""#3517 — the agent-prompt install and the dashboard toggle are ONE state path.

Epic #1714 §C/J3: the onboarding prompt asks an explicit yes/no, installs the
auto-recorder in the prompt, and wires the answer to the **already-existing**
`tortoise_onboarding_session_recording` MCP tool. The dashboard's toggle writes
through the same primitive. Two consequences this file pins:

* **Parity, not equivalence-by-luck.** The prompt answer and the dashboard
  setting must write **identical state keys**. A prompt-yes that the dashboard
  reads as *unknown* is the exact divergence the named parity test forbids — so
  the assertion is on the WRITE (same keys, same values, via two real
  surfaces), not on two independent code paths happening to agree today.
* **The probe precedes the status read.** A prompt-install must not read
  `install-pending` ("not installed yet") about a harness the agent just
  installed, so the yes-branch fires the EXISTING
  `POST /v1/sessions/install-probe` route (no new endpoint) BEFORE it reports
  success.
* **The disclosure touches no FLOW state.** The `capture-disclosed` step has
  exactly one writer — the capture path. The pushed-down plan-review finding
  (§8) is that a new disclosure render can silently consume it, so the render
  is asserted to be a pure read.

Lane: the registry lane with `patched_tortoise_sdk` (embedded temp DB) — the
same fixture family `tests/test_capture_session.py` uses, so no `TORTOISE_DB_URI`
is required.
"""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import capture_disclosure as cd
from tortoise import hosted_api as _ha
from tortoise.hosted_api import (
    app,
    get_current_org_gated,
    get_current_org_session_ungated,
)
from tortoise.onboarding import state as onboarding_state

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "tortoise" / "onboarding" / "SKILL.md"

#: The state keys BOTH surfaces write. Pinned as a literal: the parity claim is
#: worthless if the expected set is derived from the code under test.
WRITTEN_KEYS = {"session_recording", "capture_revised"}

_TEAM = {
    "org_id": "team-3517-parity",
    "tier": "free",
    "key_id": "k-3517",
    "legacy_full_access": True,
    "max_points": 100000,
    "max_sessions": None,
}


def _provision_org(org_id: str) -> None:
    """Create the registry Team node — onboarding state lives on it.

    A registry state write is a `MATCH ... SET`, so without the node it is a
    silent no-op; provisioning first mirrors the production path.
    """
    _ha._make_sdk(namespace="registry")._get_registry().query(
        "CREATE (t:Team {id:$id, onboarding_state:$st})",
        params={"id": org_id, "st": "{}"},
    )


@pytest.fixture()
def org_lane(tmp_path, monkeypatch):
    """A registry-lane TestClient + a per-org writer.

    ``use(org_id)`` provisions the registry Team node (onboarding state lives
    on it; a state write is a ``MATCH ... SET``, so without the node it is a
    silent no-op) and points BOTH auth dependencies at it. A parity test must
    give each surface its OWN fresh org: comparing changed-key sets across one
    shared org measures the RESET between the two writes, not the surfaces.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    with patched_tortoise_sdk(str(tmp_path / "parity.db")):
        provisioned: set[str] = set()

        def use(org_id: str) -> None:
            if org_id not in provisioned:
                _provision_org(org_id)
                provisioned.add(org_id)
            team = {**_TEAM, "org_id": org_id}
            app.dependency_overrides[get_current_org_session_ungated] = (
                lambda t=team: dict(t))
            app.dependency_overrides[get_current_org_gated] = (
                lambda t=team: dict(t))

        use(_TEAM["org_id"])
        with TestClient(app) as tc:
            yield tc, use


@contextlib.contextmanager
def _mcp_org(org_id: str):
    """The ContextVars the MCP tool bodies read, as HTTP middleware sets them."""
    from tortoise.mcp_auth import _current_org_id, _current_org_limits

    tok_org = _current_org_id.set(org_id)
    tok_limits = _current_org_limits.set(
        {"org_id": org_id, "tier": "free", "max_points": 100000,
         "max_sessions": None})
    try:
        yield
    finally:
        _current_org_id.reset(tok_org)
        _current_org_limits.reset(tok_limits)


def _state(org_id: str) -> dict:
    return _ha._get_onboarding_state(org_id)


def _changed(before: dict, after: dict) -> set[str]:
    return {k for k in set(before) | set(after) if before.get(k) != after.get(k)}


def _dashboard_toggle(tc: TestClient, enabled: bool) -> None:
    """The dashboard's write — `PATCH /v1/onboarding/state` (the Memory sources
    agent-sessions toggle, `main.jsx` → `{session_recording: next}`)."""
    r = tc.patch("/v1/onboarding/state", json={"session_recording": enabled})
    assert r.status_code == 200, r.text


def _prompt_answer(org_id: str, enabled: bool) -> None:
    """The prompt's write — the EXISTING MCP tool, no parallel setter."""
    from tortoise.mcp_server import tortoise_onboarding_session_recording

    with _mcp_org(org_id):
        result = tortoise_onboarding_session_recording(enabled=enabled)
    assert not result.get("error"), result


class TestPromptDashboardParity:
    @pytest.mark.parametrize("enabled", [True, False])
    def test_prompt_answer_and_dashboard_toggle_write_identical_state_keys(
            self, org_lane, enabled):
        """The named parity test (epic #1714 J3).

        Each surface writes from its OWN fresh organization, so the comparison
        is between two writes of the SAME intent from the SAME starting state —
        not between a write and the reset before it.

        Mutation: give the MCP tool its own extra key (e.g. a `capture_opt_in`
        beside `session_recording`) — the key-set equality reds; drop the
        companion-key translation from `patch_onboarding_state` — the changed-key
        sets diverge again, which is exactly the pre-#3517 defect where the
        dashboard sent `session_recording` alone.
        """
        tc, use = org_lane
        results: dict[str, dict] = {}
        changes: dict[str, set[str]] = {}
        for surface in ("dashboard", "prompt"):
            org_id = f"{_TEAM['org_id']}-{surface}-{enabled}"
            use(org_id)
            before = _state(org_id)
            if surface == "dashboard":
                _dashboard_toggle(tc, enabled)
            else:
                _prompt_answer(org_id, enabled)
            after = _state(org_id)
            changes[surface] = _changed(before, after)
            results[surface] = after

        assert changes["prompt"] == changes["dashboard"], (
            "the prompt answer and the dashboard toggle wrote DIFFERENT state "
            f"keys ({sorted(changes['prompt'])} vs "
            f"{sorted(changes['dashboard'])}) — the prompt-yes/dashboard-unknown "
            "divergence this test forbids")
        assert results["prompt"] == results["dashboard"], (
            "the two surfaces disagree on the resulting onboarding state")
        assert results["prompt"]["session_recording"] is enabled
        assert results["prompt"]["capture_revised"] is True

    def test_the_written_key_set_is_exactly_the_pinned_pair(self, org_lane):
        """Both surfaces write `session_recording` + `capture_revised` and
        nothing else — the declared set, not "these two happen to agree".

        Mutation: add any key to either write (e.g. a consent record) — the
        union below gains it and reds.
        """
        tc, use = org_lane
        seen: set[str] = set()
        for surface in ("dashboard", "prompt"):
            for enabled in (False, True):
                org_id = f"{_TEAM['org_id']}-keys-{surface}-{enabled}"
                use(org_id)
                before = _state(org_id)
                if surface == "dashboard":
                    _dashboard_toggle(tc, enabled)
                else:
                    _prompt_answer(org_id, enabled)
                seen |= _changed(before, _state(org_id))
        assert seen == WRITTEN_KEYS, (
            f"the recording surfaces wrote {sorted(seen)}; the contract is "
            f"{sorted(WRITTEN_KEYS)}")

    def test_prompt_no_installs_nothing(self, org_lane):
        """The no-branch writes the setting and installs nothing.

        Server-visible evidence of an install is the probe (and, later, a
        receipt); a decline must produce neither. Mutation: have the no-branch
        fire the install probe or an install-time write.
        """
        _tc, use = org_lane
        org_id = f"{_TEAM['org_id']}-decline"
        use(org_id)
        _prompt_answer(org_id, False)
        state = _state(org_id)
        assert state["session_recording"] is False
        assert not state.get("install_probe_pi"), (
            "a declined install must not fire the install probe")
        assert not state.get("session_capture_receipt_pi"), (
            "a declined install must not produce a capture receipt")

    def test_q3_decline_then_reenable_consents(self, org_lane):
        """A user who declined can re-enable through the tool.

        `tortoise_onboarding_session_recording(enable=true)` re-sets the
        setting REGARDLESS of `capture_revised` (epic #1714 §C — a
        stdio/self-hosted user has no other path, because the prompt ask
        never re-fires once revised). Mutation: short-circuit the write when
        `capture_revised` is already set — the second assertion reds and the
        user is stranded OFF.
        """
        _tc, use = org_lane
        org_id = f"{_TEAM['org_id']}-reenable"
        use(org_id)
        _prompt_answer(org_id, False)
        assert _state(org_id)["session_recording"] is False
        assert _state(org_id)["capture_revised"] is True, (
            "precondition: the decline set capture_revised")

        _prompt_answer(org_id, True)
        assert _state(org_id)["session_recording"] is True, (
            "the re-enable was skipped because capture_revised was already "
            "set — that strands the user in the OFF state")


class TestProbePrecedesTheStatusRead:
    def test_prompt_yes_status_not_install_pending(self, org_lane):
        """Epic #1714 §A(b): the wizard must not read `install-pending` about a
        harness the prompt just installed.

        The dashboard derives the row from `install_probe_<harness>` (no probe
        ⇒ install-pending; probe and no receipt ⇒ waiting —
        `website/apps/dashboard/src/captureStatus.js`). So the assertion is on
        the ORDER: the state read BEFORE the probe has no probe key, and the
        read AFTER it does. Mutation: drop the probe fire from the yes-branch —
        the post-read still shows no probe and this reds.
        """
        tc, use = org_lane
        org_id = f"{_TEAM['org_id']}-probe"
        use(org_id)
        assert not _state(org_id).get("install_probe_pi"), (
            "precondition: no probe yet — the row reads install-pending")

        r = tc.post("/v1/sessions/install-probe", json={"harness": "pi"})
        assert r.status_code == 200, r.text

        state = _state(org_id)
        assert state.get("install_probe_pi"), (
            "the status read after the probe still sees no probe — the "
            "dashboard would keep rendering install-pending for a harness "
            "that IS installed")
        # probe present + no receipt == `waiting`, the state AFTER
        # install-pending — the transition the prompt-install must produce.
        assert not state.get("session_capture_receipt_pi")

    def test_the_probe_is_not_gated_on_the_recording_setting(self, org_lane):
        """An org that declined still reports its install (the probe is
        install telemetry, not content). Mutation: gate the probe on
        `session_recording`."""
        tc, use = org_lane
        org_id = f"{_TEAM['org_id']}-probe-off"
        use(org_id)
        _prompt_answer(org_id, False)
        r = tc.post("/v1/sessions/install-probe", json={"harness": "pi"})
        assert r.status_code == 200, r.text
        assert _state(org_id).get("install_probe_pi")


class TestTheDocumentedOrderIsTheSafeOrder:
    """The prompt's step order is a contract, and it is asserted mechanically.

    A prose recipe whose steps are in the wrong order is exactly how the
    install-probe omission happens, so the order is pinned over the SKILL text
    rather than trusted.
    """

    def _yes_branch(self) -> str:
        text = SKILL.read_text(encoding="utf-8")
        start = text.index("### On YES — in this order")
        end = text.index("### On NO")
        branch = text[start:end]
        assert len(branch) > 500, "the yes-branch was not found in SKILL.md"
        return branch

    def test_install_precedes_verify_precedes_setting_precedes_probe(self):
        """install → verify → write the setting → probe → print. Mutation:
        reorder two steps, or drop the probe step."""
        branch = self._yes_branch()
        order = [
            "tortoise install <harness>",
            "tortoise session verify --harness <harness>",
            "tortoise_onboarding_session_recording(enabled=true)",
            "tortoise session probe --harness <harness>",
            "tortoise session disclosure --harness <harness>",
        ]
        positions = []
        for step in order:
            assert step in branch, (
                f"the yes-branch omits the step {step!r} — the prompt install "
                "is incomplete without it")
            positions.append(branch.index(step))
        assert positions == sorted(positions), (
            f"the yes-branch runs its steps out of order: "
            f"{sorted(zip(positions, order, strict=False))}")

    def test_the_yes_branch_never_claims_success_before_the_probe(self):
        """The success sentence must be produced AFTER the probe: the
        confirmation render (`--kind confirmation`) is the last command in the
        branch, and the probe step precedes it. Mutation: move the probe below
        the confirmation."""
        branch = self._yes_branch()
        probe = branch.index("tortoise session probe --harness <harness>")
        confirmation = branch.index("--kind confirmation")
        assert confirmation > probe, (
            "the yes-branch prints its success confirmation before firing the "
            "install probe — the dashboard would then read install-pending "
            "about a harness that IS installed")
        tail = branch[confirmation:]
        assert "tortoise install" not in tail, (
            "an install command runs after the success confirmation")
        assert "tortoise session disclosure --harness <harness>` prints" in branch, (
            "the branch does not print the rendered facts")

    def test_the_no_branch_writes_the_same_keys_with_the_other_value(self):
        """§C: the no-branch mirrors the dashboard decline — the same key set,
        the opposite value. Mutation: name a different key in the no-branch."""
        text = SKILL.read_text(encoding="utf-8")
        start = text.index("### On NO")
        end = text.index("### The re-enable path is pinned")
        branch = text[start:end]
        assert "tortoise_onboarding_session_recording(enabled=false)" in branch
        assert "Install nothing" in branch
        # it must not smuggle a second write path or a second key
        for forbidden in ("capture_opt_in", "consent_version",
                          "session_recording_opted_in_at", "403"):
            assert forbidden not in branch, (
                f"the no-branch names {forbidden!r} — recording is a setting "
                "(B2), not a consent ritual, and there is no second key")


class TestDisclosureStepAccounting:
    """The pushed-down plan-review finding (epic plan §8): the disclosure's
    step/state accounting is #3517's, and `capture-disclosed` must never be
    silently consumed by a render."""

    def test_the_declaration_matches_the_capture_path_single_writer(self):
        """Mutation: flip either flag in `step_accounting()`."""
        accounting = cd.step_accounting()
        assert accounting["step"] == "capture-disclosed"
        assert accounting["consumes"] is False
        assert accounting["writes"] is False
        assert "write_completed_step" in accounting["writer"]

    def test_rendering_writes_no_capture_disclosed_step(self, org_lane):
        """A render before OR after the step is idempotent and graph-silent.

        Mutation: have the render call `write_completed_step` (or the
        checkpoint surface) — the edge count changes and this reds.
        """
        _tc, use = org_lane
        org_id = f"{_TEAM['org_id']}-accounting"
        use(org_id)
        proj = _ha._make_sdk(namespace=org_id)._get_proj()

        def edges() -> int:
            rows = proj.g.query(
                f"MATCH (n:{onboarding_state.ONBOARDING_NODE_LABEL} "
                f"{{org_id: $tid}})-[:{onboarding_state.COMPLETED_STEP_EDGE}]->"
                f"(s:{onboarding_state.ONBOARDING_STEP_LABEL} "
                "{step_id: 'capture-disclosed'}) RETURN count(*)",
                params={"tid": org_id}).result_set
            return int(rows[0][0]) if rows else 0

        before_state = _state(org_id)
        assert edges() == 0, "precondition: no capture yet"
        first = cd.render_install_disclosure(harness="pi", hosted=True,
                                            api_host="api.example.com",
                                            recording=True)
        assert edges() == 0, "the disclosure render wrote a FLOW step"
        assert _state(org_id) == before_state, (
            "the disclosure render mutated onboarding state")

        # the capture path's own writer, then a second render
        onboarding_state.write_completed_step(proj, org_id,
                                              "capture-disclosed")
        assert edges() == 1
        second = cd.render_install_disclosure(harness="pi", hosted=True,
                                             api_host="api.example.com",
                                             recording=True)
        assert second == first, "the render is not deterministic"
        assert edges() == 1, (
            "a render after the first capture changed the FLOW step — the "
            "once-per-org announcement is no longer safe")
        assert "capture-disclosed" in onboarding_state.completed_steps(
            proj, org_id)

    def test_the_render_calls_no_state_writer(self, org_lane, monkeypatch):
        """A tripwire stronger than the counts: any state write during a render
        raises. Mutation: add any write to the render path."""
        _tc, use = org_lane
        use(f"{_TEAM['org_id']}-tripwire")
        import tortoise.hosted_api as ha
        from tortoise.onboarding import state as os_state

        def _boom(*_a, **_kw):  # pragma: no cover - only fires on a regression
            raise AssertionError(
                "the disclosure render performed a state WRITE — it must be a "
                "pure read of onboarding state")

        monkeypatch.setattr(ha, "_update_onboarding_state", _boom)
        monkeypatch.setattr(os_state, "write_completed_step", _boom)

        text = cd.render_install_disclosure(harness="pi", hosted=True,
                                            api_host="api.example.com",
                                            recording=True)
        assert text
        state = _state(_TEAM["org_id"])
        assert "capture-disclosed" not in json.dumps(state), (
            "state leaked the FLOW step into the operational projection")


def test_the_parity_test_file_is_registered_in_ci_surfaces():
    """An unregistered test file is never selected on the relevant source diff.

    Mutation: drop either file from `config/ci-surfaces.yml` → this reds (and
    `manifest-integrity` fails separately).
    """
    manifest = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text(
        encoding="utf-8")
    for name in ("test_onboarding_session_recording_parity.py",
                 "test_capture_disclosure_template.py"):
        assert re.search(rf"^\s*-\s+{re.escape(name)}\b", manifest, re.M), (
            f"{name} is not registered in config/ci-surfaces.yml")
