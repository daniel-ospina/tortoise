"""The #9664 premise census measures EXACTLY the clause it prints.

THE RULE THIS PINS
------------------
`docs/evidence/9664-capstone/probes/step_premise_census.py` is the instrument an
evidence bundle rests on, so a row that prints a two-part premise from a
one-condition expression reports a *satisfied* verdict for a half it never read
— the fail-open direction for a probe whose own polarity is "exit 3 rather than
report an unmeasured premise as satisfied". Review of PR #9686 found two such
rows and a coverage overclaim:

1. ``team-named`` row: printed "auto-satisfied **at org-create**, never an agent
   act" and measured only ``'team-named' in _NON_AGENT_STEPS`` (the second half).
2. ``connection-written`` rows: the "in NO completion gate" clause was never
   evaluated, the ``" (absent from every gate)"`` suffix was UNCONDITIONAL (so a
   ``YES`` would print its own negation), and "every gate" was read from
   ``state._GATES`` — which holds only ``FORK_SELF`` / ``FORK_BUILD``.
   ``_GATE_COMPACT`` is consumed directly (``state.py``: ``if compact: required
   = _GATE_COMPACT``) and is NOT in ``_GATES``, so a requirement added to the
   compact gate alone left the row printing ``NO``.
3. The ``tools`` section's docstring promised *every* MCP tool name the capstone's
   steps name, but never measured ``tortoise_health`` — the name the served
   onboarding document mentions — nor its advertised replacement
   ``tortoise_overview(section="health")``. That omission is why the PR's own
   correction could name a RETIRED tool and the instrument stayed silent.

WHAT THIS PINS
--------------
1. Every tool name the corrected steps introduce is measured, and a retired name
   carries its replacement from ``RETIRED_USE_INSTEAD``.
2. Adding ``connection-written`` to ANY ONE of the three completion gates flips
   the verdict to YES naming THAT gate — and the row never both says YES and
   claims absence.
3. A mapping entry that is not a completion gate does not flip the verdict.
4. The ``team-named`` row reads both clauses: the org-create init fragment and
   ``_NON_AGENT_STEPS``; either one failing makes it FALSIFIED.

Non-vacuity: on the pre-fix probe, (1) has no ``tortoise_health`` /
``tortoise_overview`` / ``tortoise_onboarding_state`` row, (2) has no
"is in a completion gate" row and reads 2 of the 3 gates, and (4) prints
``CONFIRMED`` while the org-create fragment writes no ``team-named`` edge.

Static: imports the probe and the onboarding modules; no DB, no network.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBE_PATH = (
    ROOT / "docs" / "evidence" / "9664-capstone" / "probes" / "step_premise_census.py"
)

#: The three completion gates a "every gate" claim must read. `state._GATES` is
#: NOT this tuple — it omits `_GATE_COMPACT`.
GATE_ATTRS = ("_GATE_SELF", "_GATE_BUILD", "_GATE_COMPACT")


def _load_probe():
    spec = importlib.util.spec_from_file_location("step_premise_census_9664", PROBE_PATH)
    assert spec is not None and spec.loader is not None, f"cannot load {PROBE_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def probe():
    """The probe module with an empty row buffer, so a test reads one section."""
    module = _load_probe()
    module.rows.clear()
    return module


def _measured(probe, needle: str) -> str:
    hits = [measured for _step, premise, measured in probe.rows if needle in premise]
    assert len(hits) == 1, (
        f"expected exactly one census row whose premise contains {needle!r}, "
        f"found {len(hits)} — the row shape moved, re-point this guard"
    )
    return hits[0]


# ── tools: the names the corrections introduce ─────────────────────────────


def test_the_tool_names_the_correction_introduces_are_measured(probe) -> None:
    """Review P2: the census never measured `tortoise_health`.

    The PR's correction 6 substitutes a first call for the ChatGPT trace, and
    `tortoise_health` is RETIRED (absent from `TOOL_REGISTRY`, absent from
    `tools/list`). A census that does not measure that name cannot catch the
    mistake it exists to prevent, so both the mentioned name and the advertised
    replacement must have a row, and the retired one must name its replacement.
    """
    from tortoise.tool_registry import RETIRED_USE_INSTEAD

    probe.census_tools()
    premises = {premise for _step, premise, _m in probe.rows}
    for name in (
        "tortoise_load_skill",
        "tortoise_health",
        "tortoise_overview",
        "tortoise_onboarding_state",
    ):
        assert f"tool `{name}` is on the MCP surface" in premises, (
            f"the census does not measure `{name}` — the tools section promises "
            "every name the capstone's steps name"
        )
    health = _measured(probe, "tool `tortoise_health` is on the MCP surface")
    assert health.startswith("ABSENT"), (
        f"`tortoise_health` is retired and must measure ABSENT, got {health!r}"
    )
    assert RETIRED_USE_INSTEAD["tortoise_health"] in health, (
        "a retired tool's row must name its advertised replacement, or a runner "
        f"reads a bare absence as 'the product is broken': {health!r}"
    )
    overview = _measured(probe, "tool `tortoise_overview` is on the MCP surface")
    assert overview.startswith("PRESENT"), (
        f"`tortoise_overview` is the advertised name and must measure PRESENT, got {overview!r}"
    )


# ── onboarding: the completion-input verdict reads ALL three gates ─────────


@pytest.mark.parametrize("gate_attr", GATE_ATTRS)
def test_adding_the_step_to_one_gate_flips_the_verdict_to_that_gate(
    probe, monkeypatch, gate_attr: str
) -> None:
    """One gate at a time — including `_GATE_COMPACT`, which `_GATES` omits.

    The verdict follows the gate the step was added to and NAMES it. A row that
    says YES and then "(absent from every gate)" asserts its own negation; the
    `"absent" not in verdict` arm is that defect's regression check.
    """
    from tortoise.onboarding import state

    gate = getattr(state, gate_attr)
    assert "connection-written" not in gate, (
        "premise changed: `connection-written` now closes a completion gate — "
        "re-point this guard and the README's correction 5"
    )
    monkeypatch.setattr(state, gate_attr, frozenset(set(gate) | {"connection-written"}))

    probe.census_onboarding()
    verdict = _measured(probe, "`connection-written` is in a completion gate")
    label = gate_attr.removeprefix("_GATE_").lower()
    assert verdict.startswith("YES"), (
        f"adding `connection-written` to {gate_attr} must read as a completion "
        f"input, got {verdict!r} — `_GATES` does not contain that gate"
    )
    assert f"present in {label}" in verdict, (
        f"the verdict must name the gate that requires it, got {verdict!r}"
    )
    assert "absent" not in verdict, (
        f"a YES verdict must not claim absence: {verdict!r}"
    )
    client_row = _measured(probe, "is in NO completion gate")
    assert client_row.startswith("FALSIFIED"), (
        "the 'no completion gate' row must flip with the gate it claims to read: "
        f"{client_row!r}"
    )


def test_a_mapping_entry_that_is_not_a_gate_does_not_flip_the_verdict(
    probe, monkeypatch
) -> None:
    """`state._GATES` is a mapping of fork gates, not the definition of "every".

    A key added to it (a future fork, a test fixture) must not answer the
    completion-gate question — the answer is the three named gates.
    """
    from tortoise.onboarding import state

    monkeypatch.setitem(state._GATES, "not-a-completion-gate",
                        frozenset({"connection-written"}))
    probe.census_onboarding()
    verdict = _measured(probe, "`connection-written` is in a completion gate")
    assert verdict.startswith("NO"), (
        f"a non-gate `_GATES` entry must not read as a completion gate: {verdict!r}"
    )
    assert "absent from self, build, compact" in verdict, (
        f"the row must name every gate it read, got {verdict!r}"
    )


# ── onboarding: the `team-named` row reads both of its clauses ─────────────


def test_the_team_named_row_measures_the_org_create_half(probe, monkeypatch) -> None:
    """The "auto-satisfied at org-create" half, not only `_NON_AGENT_STEPS`.

    Non-vacuity: `_NON_AGENT_STEPS` still contains `team-named`, so the pre-fix
    row printed CONFIRMED here — it never read the org-create fragment at all.
    """
    from tortoise.onboarding import state

    assert "team-named" in state._NON_AGENT_STEPS, (
        "premise changed: `team-named` is no longer excluded from agent steps"
    )
    monkeypatch.setattr(
        state,
        "onboarding_node_init_fragment",
        lambda **_kwargs: "MERGE (n:OnboardingState {org_id: $org_id})",
    )
    probe.census_onboarding()
    verdict = _measured(probe, "`team-named` is auto-satisfied")
    assert verdict.startswith("FALSIFIED"), (
        "an org-create init that writes no `team-named` edge falsifies the "
        f"'auto-satisfied' half even though the non-agent half holds: {verdict!r}"
    )
    assert "org-create init writes the edge=False" in verdict, (
        f"the row must show which clause failed, got {verdict!r}"
    )


def test_the_team_named_row_also_measures_the_non_agent_half(probe, monkeypatch) -> None:
    """The other clause: the step is never an agent act."""
    from tortoise.onboarding import state

    monkeypatch.setattr(
        state, "_NON_AGENT_STEPS", frozenset(state._NON_AGENT_STEPS - {"team-named"})
    )
    probe.census_onboarding()
    verdict = _measured(probe, "`team-named` is auto-satisfied")
    assert verdict.startswith("FALSIFIED"), (
        f"an agent-act step falsifies the 'never an agent act' half: {verdict!r}"
    )
    assert "state._NON_AGENT_STEPS=False" in verdict, (
        f"the row must show which clause failed, got {verdict!r}"
    )
