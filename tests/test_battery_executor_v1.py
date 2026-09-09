"""Task 9 — executor v1 hermetic tests (envelope schema + TVDE scaffold).

No network: scripted callers return fixed deliberation prose + JSON
envelopes. Locks: envelope validation (unknown intent -> ValueError,
confidence type/range, bools, lists), the envelope-as-ONLY-channel rule
(missing JSON block -> ValueError, never prose-mined), byte-identical
scaffold across arms/families (prompt = f(render, phase) only),
progressive per-turn delivery, and the early-exit rule (n >= 3 ceiling;
early exit on a no-revision REVISE).
"""
from __future__ import annotations

import pytest

from battery.exceptions import ConfigError
from battery.runner.executor import (
    envelope_from_response,
    execute_tvde_episode,
    tvde_prompt,
    validate_envelope,
)


def _good_envelope(position="Proceed with the migration", conf=0.8,
                   **over):
    e = {"position": position, "stated_confidence": conf,
         "undecided": False, "defeat_conditions": ["data-loss risk"],
         "intents": ["register_conflict"], "citations": ["src-1"]}
    e.update(over)
    return e


# ── Envelope schema ───────────────────────────────────────────────────
def test_valid_envelope_roundtrip():
    env = validate_envelope(_good_envelope())
    assert env.position == "Proceed with the migration"
    assert env.stated_confidence == 0.8
    t = env.to_trace()
    assert t["stated_undecided"] is False
    assert t["stated_defeat_conditions"] == ["data-loss risk"]


def test_unknown_intent_value_error():
    with pytest.raises(ValueError):
        validate_envelope(_good_envelope(intents=["teleport"]))


def test_confidence_type_and_range_rejected():
    with pytest.raises(TypeError):
        validate_envelope(_good_envelope(stated_confidence="high"))
    with pytest.raises(ValueError):
        validate_envelope(_good_envelope(stated_confidence=1.4))


def test_undecided_must_be_bool():
    with pytest.raises(TypeError):
        validate_envelope(_good_envelope(undecided="yes"))


def test_defeat_conditions_must_be_str_list():
    with pytest.raises(TypeError):
        validate_envelope(_good_envelope(defeat_conditions=[1, 2]))


def test_empty_position_rejected():
    with pytest.raises(ValueError):
        validate_envelope(_good_envelope(position="  "))


# ── Envelope is the ONLY scalar channel ───────────────────────────────
def test_no_json_block_never_prose_mined():
    with pytest.raises(ValueError):
        envelope_from_response(
            "I believe the position is sound and my confidence is 0.9.")


def test_unparseable_json_block_raises():
    with pytest.raises(ValueError):
        envelope_from_response("text {position: broken")


# ── TVDE scaffold ─────────────────────────────────────────────────────
def test_prompt_is_function_of_render_and_phase():
    p1 = tvde_prompt("Scenario render A", "challenge")
    p2 = tvde_prompt("Scenario render A", "challenge")
    p3 = tvde_prompt("Scenario render B", "challenge")
    assert p1 == p2                       # byte-identical same render+phase
    assert p1 != p3                       # family variation = render only
    with pytest.raises(ConfigError):
        tvde_prompt("render", "nonsense")


class _ScriptedCaller:
    """Scripted caller: deliberation prose + JSON envelope per call.
    Positions are consumed PER CALL (last repeats if the episode outruns
    the script) so tests control exactly when REVISE holds vs changes the
    ALIGN position."""

    def __init__(self, positions: list[str]):
        self._positions = positions
        self.calls = []
        self.last_prompt_tokens = 10
        self.last_completion_tokens = 20

    def call(self, *, prompt: str) -> str:
        self.calls.append(prompt)
        i = min(len(self.calls) - 1, len(self._positions) - 1)
        import json
        env = {"position": self._positions[i],
               "stated_confidence": 0.8, "undecided": False,
               "defeat_conditions": [], "intents": [], "citations": []}
        return (f"deliberation for {prompt[:30]}\n"
                f"{json.dumps(env)}")


def test_episode_progressive_turns_and_early_exit():
    # REVISE (call 4) holds the ALIGN position -> early exit after 1 cycle
    # (align + challenge + deepen + revise; no converge).
    caller = _ScriptedCaller(["hold", "notes", "notes", "hold"])
    ep = execute_tvde_episode(caller=caller,
                              scenario_render="render",
                              scenario_id="d-001")
    phases = [t["phase"] for t in ep.turns]
    assert phases == ["align", "challenge", "deepen", "revise"]
    assert ep.converged_early is True
    assert ep.decide_cycles == 1
    # one model call per turn (progressive delivery)
    assert len(caller.calls) == 4


def test_episode_cycles_until_revision_then_converge():
    # REVISE changes the position on cycles 1-2, HOLDS on cycle 3 -> early
    # exit at the n>=3 boundary (no converge turn).
    script = ["A", "n", "n", "B",      # cycle 1: revise B != A
              "n", "n", "C",            # cycle 2: revise C != A
              "n", "n", "A"]            # cycle 3: revise holds A
    caller = _ScriptedCaller(script)
    ep = execute_tvde_episode(caller=caller,
                              scenario_render="render",
                              scenario_id="d-001")
    phases = [t["phase"] for t in ep.turns]
    assert phases[:2] == ["align", "challenge"]
    assert "converge" not in phases  # early-exit before converge
    assert ep.decide_cycles == 3
    assert len(ep.envelopes) == 10


def test_episode_max_cycles_ceiling_converges():
    # always-revising agent -> full max_cycles then CONVERGE (ceiling).
    script = ["A", "n", "n", "B", "n", "n", "C",
              "n", "n", "D", "n", "n", "final"]
    caller = _ScriptedCaller(script)
    ep = execute_tvde_episode(caller=caller,
                              scenario_render="render",
                              scenario_id="d-001")
    phases = [t["phase"] for t in ep.turns]
    assert phases[-1] == "converge"      # ceiling reached -> converge
    assert ep.decide_cycles == 3
    assert ep.converged_early is False


def test_empty_turn_never_fabricated():
    class _EmptyCaller:
        last_prompt_tokens = last_completion_tokens = 0
        def call(self, *, prompt: str) -> str:
            return "   "
    with pytest.raises(ValueError):
        execute_tvde_episode(caller=_EmptyCaller(),
                             scenario_render="render",
                             scenario_id="d-001")


def test_envelope_extraction_trailing_prose_with_braces():
    """#1416 real-run fix: trailing model prose that contains braces must
    not break extraction — the LAST COMPLETE JSON object wins (raw_decode
    walk), never a naive last-brace slice. Envelope honesty unchanged: the
    object must still validate."""
    env = '{"position": "Proceed with the migration", ' \
          '"stated_confidence": 0.8, "undecided": false, ' \
          '"defeat_conditions": ["data-loss"], "intents": [], ' \
          '"citations": ["src-1"]}'
    text = ("I weigh the migration risk against the benefit; the "
            "counter-argument names data-loss during migration.\n"
            f"{env}\n"
            "I considered option {2} and option {1} before deciding "
            "(the risk curve favors deferral here).}")
    e = envelope_from_response(text)
    assert e.position == "Proceed with the migration"
    assert e.stated_confidence == 0.8


def test_envelope_extraction_requires_last_valid_object():
    """An earlier malformed object is skipped; the trailing valid envelope
    wins (the scaffold instructs the model to END with the envelope)."""
    env = '{"position": "hold", "stated_confidence": 0.6, "undecided": false, ' \
          '"defeat_conditions": [], "intents": [], "citations": []}'
    text = ('{"position": "early", "stated_confidence": 0.9, "undecided": false, '
            '"defeat_conditions": [], "intents": [], "citations": [], }}\n'
            f"{env}")
    e = envelope_from_response(text)
    assert e.position == "hold"


def test_envelope_extraction_garbage_still_fails_closed():
    """No complete object -> ValueError (never prose-mined)."""
    with pytest.raises(ValueError):
        envelope_from_response("I think we should defer the migration.")
