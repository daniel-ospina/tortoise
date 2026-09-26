"""#3784 — the server must not record an onboarding completion the user did
not produce.

The defect: ``tortoise/mcp_server.py::_maybe_onboarding_auto_complete`` used to
file ``decide-completed`` (label: "Make your first decision") on ANY successful
agent write, and to flip the server-owned ``status`` to ``complete`` without
consulting the canonical fork-aware gate. One ``tortoise_create_point`` call —
the product's own setup prompt — therefore produced::

    completed_steps: ["team-named","harness-connected","first-points-filed",
                      "decide-completed"]
    status: "complete"
    onboarding_complete: true

with no decision ever filed. It also filed the build fork's
``catalog-presented`` ("Review the catalog") on any write, and cached ``True``
in the 60 s tools/list verdict while the org was not actually complete.

The pin: a step edge (and the server-owned status) is a record of something the
server OBSERVED — "every onboarding completion fact is a record of something the
server observed" (the onboarding truth-surface invariant, issue #3784).

Two layers are tested here:
1. the auto-complete's contract (which steps a given observation files, gate
   delegation, cache truthfulness) — no DB needed;
2. the CALL SITES — a point write must not claim a decision, a decision write
   must (this is the layer that actually reds on the pre-fix code).

#3913 RED evidence (measured 2026-09-21 by running THIS file against the
merge-base tree, where the build gate still required `catalog-presented`):
exactly ONE test reds — `test_build_fork_completes_on_the_two_observed_acts`.
`test_build_fork_fail_closed_when_an_observed_act_is_missing` and the #3784-era
guards pass on both sides of the ruling. The census that previously stood here
("19 of the 24 tests below red on the pre-fix code", verified against
`origin/main`) does not reproduce at the merge base — 24 passed, 1 failed — so
it is replaced by the measurement above and must not be read as this PR's
evidence.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault(
    "TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest

from tortoise import hosted_api
from tortoise import mcp_server as mcp
from tortoise.mcp_auth import SELFHOST_ORG_ID, _current_org_id
from tortoise.onboarding import state as onboarding_state

ORG = "org-3784-autocomplete"


class _Recorder:
    """Captures every write the auto-complete performs."""

    def __init__(self, *, projection=None, legacy=None, gate_result=False):
        self.steps: list[tuple[str, str, bool | None]] = []
        self.gate_calls: list[str] = []
        self.projection = projection if projection is not None else {
            "onboarding_complete": False, "fork": "self"}
        self.legacy = legacy if legacy is not None else {}
        self.gate_result = gate_result

    def install(self, monkeypatch):
        monkeypatch.setattr(hosted_api, "_org_proj", lambda org_id: object())
        monkeypatch.setattr(
            hosted_api, "_get_onboarding_projection",
            lambda org_id: dict(self.projection))
        monkeypatch.setattr(
            hosted_api, "_get_onboarding_state", lambda org_id: dict(self.legacy))
        monkeypatch.setattr(
            hosted_api, "_maybe_apply_completion", self._gate)
        monkeypatch.setattr(
            onboarding_state, "write_completed_step", self._write_step)
        # A direct server-owned status write is EXACTLY the #3784 defect —
        # record it so a test can prove the auto-complete no longer does it.
        self.status_writes: list[tuple] = []
        monkeypatch.setattr(
            onboarding_state, "write_status",
            lambda *a, **k: self.status_writes.append((a, k)))
        mcp._onboarding_state_cache.pop(ORG, None)

    def _gate(self, org_id):
        self.gate_calls.append(org_id)
        return self.gate_result

    def _write_step(self, proj, org_id, step_id, *, status_from_mirror=None):
        self.steps.append((org_id, step_id, status_from_mirror))
        return {"created": True, "step_id": step_id}

    @property
    def step_ids(self) -> list[str]:
        return [s[1] for s in self.steps]


@pytest.fixture
def org_ctx(monkeypatch):
    """A hosted (non-selfhost) org context + a clean TTL cache."""
    token = _current_org_id.set(ORG)
    mcp._onboarding_state_cache.pop(ORG, None)
    try:
        yield ORG
    finally:
        _current_org_id.reset(token)
        mcp._onboarding_state_cache.pop(ORG, None)


# ── the auto-complete contract ────────────────────────────────

class TestObservedSteps:
    def test_plain_point_write_files_only_the_two_inferred_steps(
            self, monkeypatch, org_ctx):
        """#3784 core: a write that observed no decision files no decision."""
        rec = _Recorder()
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete()

        assert rec.step_ids == ["harness-connected", "first-points-filed"]
        assert "decide-completed" not in rec.step_ids, (
            "a plain point write recorded `decide-completed` — the exact fact "
            "the user never produced (#3784)")

    def test_decision_observed_files_decide_completed(self, monkeypatch, org_ctx):
        """The documented decide path still completes the decide step."""
        rec = _Recorder()
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        assert rec.step_ids == [
            "harness-connected", "first-points-filed", "decide-completed"]

    def test_build_fork_never_infers_catalog_presented(self, monkeypatch, org_ctx):
        """#3784: `catalog-presented` ("Review the catalog") is observed where
        the catalog is presented — never inferred from an unrelated write."""
        for decision in (False, True):
            rec = _Recorder(projection={"onboarding_complete": False,
                                        "fork": "build"})
            rec.install(monkeypatch)
            mcp._maybe_onboarding_auto_complete(decision_observed=decision)
            assert "catalog-presented" not in rec.step_ids, (
                "the build fork's catalog step was inferred from a write — the "
                "catalog presentation is observed at the render/checkpoint, "
                "never from an unrelated write (#3784)")
            expected = ["harness-connected", "first-points-filed"]
            if decision:  # a decision is a decision on any fork
                expected.append("decide-completed")
            assert rec.step_ids == expected

    def test_grandfathered_projection_short_circuits(self, monkeypatch, org_ctx):
        """An already-complete projection writes nothing (legacy completer is
        never re-onboarded)."""
        rec = _Recorder(projection={"onboarding_complete": True,
                                    "fork": "self"})
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        assert rec.step_ids == []
        assert rec.gate_calls == []
        assert mcp._onboarding_state_cache[ORG][1] is True

    def test_legacy_mirror_is_passed_to_every_step_write(
            self, monkeypatch, org_ctx):
        """status_from_mirror is the create-on-write seam's grandfathered
        mapping — dropping it would reopen the poisoned-false window."""
        rec = _Recorder(legacy={"onboarding_complete": True})
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        assert all(s[2] is True for s in rec.steps), rec.steps


class TestCompletionGate:
    def test_completion_is_delegated_to_the_canonical_gate(
            self, monkeypatch, org_ctx):
        """The server-owned status is never written by the auto-complete —
        the fork-aware gate decides (fork=None→self, compact, unsure)."""
        rec = _Recorder(gate_result=True)
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        assert rec.gate_calls == [ORG]
        assert rec.status_writes == [], (
            "the auto-complete wrote `status` directly — bypassing "
            "completion_gate_satisfied (#3784)")

    def test_gate_refusing_completion_leaves_the_org_active(
            self, monkeypatch, org_ctx):
        """fork_unsure_at / a missing required step → NOT complete."""
        rec = _Recorder(gate_result=False)
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete()
        assert rec.gate_calls == [ORG]
        assert mcp._onboarding_state_cache.get(ORG) is None, (
            "the tools/list verdict cache was set True for an org the gate "
            "did not report complete (#3784 secondary defect)")
        assert rec.status_writes == []

    def test_cache_true_only_on_real_completion(self, monkeypatch, org_ctx):
        """AC5 — pinned together with the gate-refusal case above (which is
        the discriminating half: pre-fix it also cached True)."""
        rec = _Recorder(gate_result=True)
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete()
        assert mcp._onboarding_state_cache[ORG][1] is True


class TestFailClosedContract:
    def test_decision_observed_is_keyword_only(self, monkeypatch, org_ctx):
        """A future caller cannot accidentally pass it positionally."""
        rec = _Recorder()
        rec.install(monkeypatch)
        with pytest.raises(TypeError):
            mcp._maybe_onboarding_auto_complete(True)  # type: ignore[misc]

    def test_default_claims_no_decision(self, monkeypatch, org_ctx):
        """The default is the SAFE one — a caller that forgets to declare its
        observation must not fabricate a decision."""
        rec = _Recorder()
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete()
        assert "decide-completed" not in rec.step_ids

    def test_selfhost_and_stdio_orgs_are_noops(self, monkeypatch):
        """No hosted onboarding state → zero writes."""
        rec = _Recorder()
        rec.install(monkeypatch)
        for org in (None, SELFHOST_ORG_ID):
            token = _current_org_id.set(org) if org else None
            try:
                mcp._maybe_onboarding_auto_complete(decision_observed=True)
            finally:
                if token is not None:
                    _current_org_id.reset(token)
        assert rec.step_ids == []
        assert rec.gate_calls == []

    def test_transient_failure_still_fails_open(self, monkeypatch, org_ctx):
        """A graph/control-plane error must never block the agent's write."""
        rec = _Recorder()
        rec.install(monkeypatch)

        def boom(org_id):
            raise RuntimeError("synthetic control-plane outage")
        monkeypatch.setattr(hosted_api, "_get_onboarding_projection", boom)
        mcp._maybe_onboarding_auto_complete(decision_observed=True)  # no raise

    def test_replay_is_idempotent_and_does_not_oscillate(
            self, monkeypatch, org_ctx):
        rec = _Recorder(gate_result=False)
        rec.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete()
        first = list(rec.step_ids)
        mcp._maybe_onboarding_auto_complete()
        assert rec.step_ids == first + first  # same FWW set, no new claims
        assert "decide-completed" not in rec.step_ids


# ── the call sites (this is the layer that reds pre-fix) ──────

class _FakeGraph:
    """A tiny in-memory OnboardingState (step edges + node status) driven by
    the REAL writers the auto-complete calls.

    ``completion_gate_satisfied`` and ``hosted_api._maybe_apply_completion``
    are NOT stubbed — this is the integration-shaped pin the unit tests
    above deliberately cannot give (they stub the gate so they can assert
    delegation). Here the actual fork-aware gate decides.
    """

    def __init__(self, *, fork="self", compact=False, fork_unsure_at=None):
        self.steps = {"team-named"}
        self.node = {"status": "active", "fork": fork, "compact": compact,
                     "fork_unsure_at": fork_unsure_at}
        self.status_writes = []

    def install(self, monkeypatch):
        monkeypatch.setattr(hosted_api, "_org_proj", lambda org_id: object())
        monkeypatch.setattr(
            hosted_api, "_get_onboarding_projection",
            lambda org_id: {"onboarding_complete": False,
                            "fork": self.node["fork"]})
        monkeypatch.setattr(hosted_api, "_get_onboarding_state",
                            lambda org_id: {})
        monkeypatch.setattr(onboarding_state, "read_onboarding_node",
                            lambda proj, org_id: dict(self.node))
        monkeypatch.setattr(onboarding_state, "completed_steps",
                            lambda proj, org_id: sorted(self.steps))
        monkeypatch.setattr(onboarding_state, "write_completed_step",
                            self._write_step)
        monkeypatch.setattr(onboarding_state, "write_status",
                            self._write_status)
        mcp._onboarding_state_cache.pop(ORG, None)

    def _write_step(self, proj, org_id, step_id, *, status_from_mirror=None):
        self.steps.add(step_id)
        return {"created": True, "step_id": step_id}

    def _write_status(self, proj, org_id, status, *, status_from_mirror=None):
        self.status_writes.append(status)
        self.node["status"] = status


class TestRealGateIntegration:
    """The REAL fork-aware gate, through the REAL _maybe_apply_completion."""

    def test_self_fork_point_write_leaves_the_org_active(
            self, monkeypatch, org_ctx):
        """#3784 end-to-end: a plain point write cannot complete a self-fork
        org — the decide step is missing and the gate says so."""
        g = _FakeGraph(fork="self")
        g.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete()
        assert g.steps == {"team-named", "harness-connected",
                           "first-points-filed"}
        assert g.node["status"] == "active"
        assert g.status_writes == []
        assert mcp._onboarding_state_cache.get(ORG) is None

    def test_self_fork_decision_write_completes(self, monkeypatch, org_ctx):
        g = _FakeGraph(fork="self")
        g.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        assert "decide-completed" in g.steps
        assert g.node["status"] == "complete"
        assert g.status_writes == ["complete"]
        assert mcp._onboarding_state_cache[ORG][1] is True

    def test_fork_unsure_org_is_never_auto_completed(
            self, monkeypatch, org_ctx):
        """#2407: fork still unanswered → the gate refuses, even with the
        full self checklist done."""
        g = _FakeGraph(fork=None, fork_unsure_at="2026-09-17T00:00:00Z")
        g.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete(decision_observed=True)
        assert g.node["status"] == "active"
        assert g.status_writes == []

    def test_build_fork_completes_on_the_two_observed_acts(
            self, monkeypatch, org_ctx):
        """#3913 (owner ruling 2026-09-20): the build gate is the two acts the
        server OBSERVES (harness-connected + first-points-filed). It no longer
        requires `catalog-presented` — RED on origin/main."""
        g = _FakeGraph(fork="build")
        g.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete()
        assert g.steps == {"team-named", "harness-connected",
                           "first-points-filed"}
        assert "catalog-presented" not in g.steps
        assert g.node["status"] == "complete"
        assert g.status_writes == ["complete"]
        assert mcp._onboarding_state_cache[ORG][1] is True

    def test_build_fork_fail_closed_when_an_observed_act_is_missing(
            self, monkeypatch, org_ctx):
        """#3913 fail-closed: a build org carrying only ONE of the two observed
        acts stays active — the gate never completes on half the evidence."""
        g = _FakeGraph(fork="build")
        g.install(monkeypatch)

        def _partial(proj, org_id, step_id, *, status_from_mirror=None):
            # simulate a graph that stored only the harness act
            if step_id == "first-points-filed":
                return {"created": False, "step_id": step_id}
            g.steps.add(step_id)
            return {"created": True, "step_id": step_id}
        monkeypatch.setattr(onboarding_state, "write_completed_step", _partial)
        mcp._maybe_onboarding_auto_complete()
        assert g.steps == {"team-named", "harness-connected"}
        assert g.node["status"] == "active"
        assert g.status_writes == []
        assert mcp._onboarding_state_cache.get(ORG) is None

    def test_compact_org_completes_on_the_two_inferred_steps(
            self, monkeypatch, org_ctx):
        g = _FakeGraph(fork=None, compact=True)
        g.install(monkeypatch)
        mcp._maybe_onboarding_auto_complete()
        assert g.node["status"] == "complete"

    def test_grandfathered_node_write_preserves_the_mirror(
            self, monkeypatch, org_ctx):
        """A node-absent org with jsonb complete=true must be created
        complete (the create-on-write seam) — never re-onboarded."""
        g = _FakeGraph(fork="self")
        g.install(monkeypatch)
        seen: list[bool | None] = []

        def _mirror_step(proj, org_id, step_id, *, status_from_mirror=None):
            seen.append(status_from_mirror)
            if status_from_mirror:
                g.node["status"] = "complete"
            return {"created": True, "step_id": step_id}
        monkeypatch.setattr(onboarding_state, "write_completed_step",
                            _mirror_step)
        monkeypatch.setattr(hosted_api, "_get_onboarding_state",
                            lambda org_id: {"onboarding_complete": True})
        mcp._maybe_onboarding_auto_complete()
        assert seen and all(s is True for s in seen), seen


class _FakeSDK:
    def __init__(self):
        self.calls = []

    def create_point(self, kind, content, **kwargs):
        self.calls.append(("create_point", kind, content))
        return {"id": "p-1", "pointKind": kind, "content": content}

    def file_decision(self, options, evidence, choice):
        self.calls.append(("file_decision", options, evidence, choice))
        return {"decision_id": "d-1", "option_ids": [], "evidence_ids": []}


@pytest.fixture
def stdio_mode(monkeypatch):
    """Dev-mode stdio transport, reset afterwards — a leaked ContextVar would
    weaken the fail-closed `_safe` gate for later tests in the process."""
    monkeypatch.setenv("TORTOISE_API_KEY", "")
    token = mcp._transport_mode.set("stdio")
    try:
        yield
    finally:
        mcp._transport_mode.reset(token)


@pytest.fixture
def handler_env(stdio_mode, monkeypatch):
    """Real handlers, fake SDK, stdio dev mode (mirrors
    test_de2e7_gates_mcp.py::test_mcp_handler_error_contract)."""
    sdk = _FakeSDK()
    monkeypatch.setattr(mcp, "_get_org_sdk", lambda: sdk)
    recorded: list[dict] = []
    monkeypatch.setattr(
        mcp, "_maybe_onboarding_auto_complete",
        lambda **kw: recorded.append(dict(kw)))
    return sdk, recorded


class TestCallSiteObservation:
    def test_statement_point_write_claims_no_decision(self, handler_env):
        """The walk's actual call: `create_point(kind="statement")`."""
        sdk, recorded = handler_env
        res = mcp.tortoise_create_point(
            "statement", "the product's setup prompt told me to file this")
        assert "error" not in res, res
        assert sdk.calls and sdk.calls[0][1] == "statement"
        assert recorded == [{"decision_observed": False}], (
            "a statement point write reached the onboarding auto-complete "
            "WITHOUT declaring its (non-)observation — the pre-#3784 call "
            "site passed no observation at all")

    def test_decision_kind_point_write_claims_a_decision(self, handler_env):
        """The documented EP decide protocol: `kind="decision"`
        (tortoise/onboarding/SKILL.md §5)."""
        _sdk, recorded = handler_env
        res = mcp.tortoise_create_point("decision", "Decision: Postgres")
        assert "error" not in res, res
        assert recorded == [{"decision_observed": True}]

    def test_observation_follows_the_persisted_pointkind(self, stdio_mode,
                                                         monkeypatch):
        """The server records what the graph STORED, not what was asked for —
        a handler whose SDK normalizes `Decision` to `decision` still
        observes a decision."""
        sdk = _FakeSDK()
        monkeypatch.setattr(mcp, "_get_org_sdk", lambda: sdk)
        recorded: list[dict] = []
        monkeypatch.setattr(mcp, "_maybe_onboarding_auto_complete",
                            lambda **kw: recorded.append(dict(kw)))

        def _normalized(kind, content, **kwargs):
            return {"id": "p-2", "pointKind": (kind or "").strip().lower(),
                    "content": content}
        sdk.create_point = _normalized
        res = mcp.tortoise_create_point(" Decision ", "choose")
        assert "error" not in res, res
        assert recorded == [{"decision_observed": True}]

    def test_file_decision_claims_a_decision(self, handler_env):
        _sdk, recorded = handler_env
        res = mcp.tortoise_file_decision(["JSON", "YAML"], ["fits"], 0)
        assert "error" not in res, res
        assert recorded == [{"decision_observed": True}]

    def test_failed_write_never_reaches_the_auto_complete(self, handler_env):
        """An errored write observes nothing."""
        _sdk, recorded = handler_env

        def boom(*a, **k):
            raise RuntimeError("synthetic write failure")
        mcp._get_org_sdk().create_point = boom
        res = mcp.tortoise_create_point("decision", "irrelevant")
        assert "error" in res, res
        assert recorded == []
