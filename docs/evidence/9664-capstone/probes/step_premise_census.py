#!/usr/bin/env python3
"""#9664 capstone — premise census for the ChatGPT onboarding journey's steps.

The capstone (issue #9664) is a MANUAL, agent-run clickthrough whose evidence is
a per-step verdict. Before a runner can give a step a verdict, the step's
PREMISE has to be true of the shipped system: a step that names a tool we never
registered, or an event the server never observes, cannot pass — and a runner
who does not know that will record a healthy system as FAILED.

This probe measures the premises that can be measured without a browser, a
ChatGPT account or a live tenant, and prints one row per premise. It does NOT
drive the journey (that is the human part, by design — #9664 "Not a script")
and it encodes no answer: every row is a fact about the code we ship.

Sections
  tools       — every MCP tool name the capstone's steps name (the corrected
                wording included: the name the served onboarding document
                MENTIONS and its advertised replacement), vs TOOL_REGISTRY
  onboarding  — the canonical step ids, the completion gates, and what the
                server can OBSERVE (vs. what only a client can record)
  resource    — the RFC 8707 resource forms an MCP connector may declare, which
                is what the "deliberate ugly cases" (from #9340) must fit into

Every printed row measures EXACTLY the clause it prints. A premise with more
than one clause is measured clause-by-clause in the same expression — a row
that reports a two-part premise from a one-clause probe would print a
"satisfied" verdict for the half it never read, which is the fail-open
direction for an evidence instrument (review P2 on this file).

Exit codes
  0 — every premise was measured (falsified premises are DATA, not a failure)
  3 — a premise could not be measured (an import/shape change); fail closed,
      never report an unmeasured row as satisfied
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))

BASE = "https://api.premiselabs.co"

rows: list[tuple[str, str, str]] = []
unmeasured: list[str] = []


def row(step: str, premise: str, measured: str) -> None:
    rows.append((step, premise, measured))


# ── tools: the tool names the capstone's steps name ────────────────────────
def census_tools() -> None:
    from tortoise.tool_registry import RETIRED_USE_INSTEAD, TOOL_REGISTRY

    names = {t.name for t in TOOL_REGISTRY}
    # Steps 6-9 name the calls the agent makes; step 6 names a tool we must find.
    # `tortoise_health` is here because the SERVED onboarding document the
    # ChatGPT path pastes mentions it (mostly to disclose the retirement) while
    # its instruction names the ADVERTISED `tortoise_overview(section="health")`
    # — both names are claims about the surface, so both are measured, and the
    # retired one carries its replacement in the row rather than reading as a
    # bare absence.
    for step, tool in (
        ("6", "tortoise_load_skill"),
        ("6", "tortoise_health"),
        ("6", "tortoise_overview"),
        ("6", "tortoise_onboarding_state"),
        ("7", "tortoise_list_graphs"),
        ("8", "tortoise_create_point"),
        ("9", "tortoise_file_decision"),
    ):
        present = tool in names
        replacement = RETIRED_USE_INSTEAD.get(tool)
        note = "" if present or not replacement else f"; RETIRED → {replacement}"
        row(step, f"tool `{tool}` is on the MCP surface",
            f"{'PRESENT' if present else 'ABSENT'} "
            f"(registry holds {len(names)} live tools){note}")
    skill_tools = sorted(n for n in names if "skill" in n)
    row("6", "the surface has ANY skill-loading tool",
        f"{skill_tools if skill_tools else 'NONE'}")


# ── onboarding: what closes which step, and who can observe it ─────────────
def _completion_gates() -> tuple[tuple[str, frozenset[str]], ...]:
    """The THREE completion gates, by name.

    ``state._GATES`` holds only the two fork gates (``FORK_SELF`` /
    ``FORK_BUILD``); the compact gate is consumed directly (`state.py`
    ``if compact: required = _GATE_COMPACT``) and is therefore NOT a member of
    ``_GATES``. Iterating ``_GATES.values()`` would answer a question about
    "every gate" from 2 of the 3, and would keep printing ``NO`` if the step
    were added to the compact gate alone.
    """
    from tortoise.onboarding import state

    return (("self", state._GATE_SELF), ("build", state._GATE_BUILD),
            ("compact", state._GATE_COMPACT))


def gates_containing(step_id: str) -> list[str]:
    """The completion gates that REQUIRE ``step_id`` — read from all three."""
    return [name for name, gate in _completion_gates() if step_id in gate]


def census_onboarding() -> None:
    from tortoise.hosted_api import _CHECKPOINT_STEPS
    from tortoise.onboarding import state

    # Both clauses of row 1, measured separately: the org-create init fragment
    # is what makes the step auto-satisfied, and `_NON_AGENT_STEPS` is what
    # makes it never an agent act. Measuring only the second would print
    # CONFIRMED for the first, unread half.
    init_writes_it = "team-named" in state.onboarding_node_init_fragment(
        org_named_edge=True)
    init_skips_it = "team-named" not in state.onboarding_node_init_fragment(
        org_named_edge=False)
    non_agent = "team-named" in state._NON_AGENT_STEPS
    row("1", "`team-named` is auto-satisfied at org-create, never an agent act",
        f"{'CONFIRMED' if init_writes_it and init_skips_it and non_agent else 'FALSIFIED'}"
        f" (org-create init writes the edge={init_writes_it}, follows the flag={init_skips_it};"
        f" state._NON_AGENT_STEPS={non_agent})")
    gates = gates_containing("connection-written")
    row("5", "`connection-written` is in a completion gate",
        f"{'YES' if gates else 'NO'} "
        f"({', '.join('present in ' + g for g in gates) if gates else 'absent from self, build, compact'})")
    client_checkpoint = "connection-written" in _CHECKPOINT_STEPS
    row("5", "`connection-written` is in NO completion gate, and IS a "
             "client checkpoint (not a server observation)",
        f"{'CONFIRMED' if not gates and client_checkpoint else 'FALSIFIED'}"
        f" (gates requiring it: {', '.join(gates) or 'NONE'};"
        f" checkpoint-writable={client_checkpoint};"
        f" excluded from agent steps={'YES' if 'connection-written' in state._NON_AGENT_STEPS else 'NO'})")
    for name, gate in _completion_gates():
        row("5", f"the `{name}` completion gate requires",
            ", ".join(sorted(gate)))
    pending = state.restart_pending(['team-named', 'connection-written'])
    row("5", "`restart_pending` for the set step 5 describes "
             "(['team-named','connection-written'])",
        f"{pending} "
        + ("— i.e. 'config written, harness NOT yet verified'" if pending
           else "— NOT the config-written/restart-pending condition"))
    row("5", "`restart_pending` once the harness has connected",
        f"{state.restart_pending(['team-named', 'connection-written', 'harness-connected'])}")
    # Steps 8/9 (which step a given write observes) are NOT restated here: a
    # local variable is not measurable without re-encoding it, and a re-encoding
    # would pin the answer this capstone exists to observe. They are measured by
    # EXECUTION instead — tests/test_onboarding_auto_complete.py's call-site
    # layer (`-k handler_env`), whose log sits beside this one.


# ── resource: the connector targets the OAuth AS will accept (#9340) ───────
def census_resource() -> None:
    from tortoise.oauth import OAuthError, parse_resource

    org = "11111111-2222-3333-4444-555555555555"
    gid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    cases = [
        ("bare /mcp (what ChatGPT's UI gets today)", f"{BASE}/mcp"),
        ("origin root (ChatGPT echoes resource=origin)", BASE),
        ("org-scoped (the #1701 R1 team selector)", f"{BASE}/mcp/organizations/{org}"),
        ("GRAPH-scoped (the ugly cases need this)", f"{BASE}/mcp/organizations/{org}/graphs/{gid}"),
    ]
    for label, value in cases:
        try:
            _canonical, got_org = parse_resource(BASE, value)
            outcome = f"ACCEPTED → org_id={got_org}"
        except OAuthError as exc:
            outcome = f"REFUSED({exc.status} {exc.error})"
        row("4+ugly", label, outcome)


def main() -> int:
    for section in (census_tools, census_onboarding, census_resource):
        try:
            section()
        except Exception as exc:  # fail closed, name the section
            unmeasured.append(f"{section.__name__}: {type(exc).__name__}: {exc}")

    width = max(len(s) for s, _, _ in rows) if rows else 2
    print("#9664 capstone — step-premise census")
    print(f"repo={REPO}  base={BASE}")
    print()
    print(f"{'step':<{width}}  {'premise':<62}  measured")
    print(f"{'-'*width}  {'-'*62}  {'-'*40}")
    for step, premise, measured in rows:
        print(f"{step:<{width}}  {premise:<62}  {measured}")
    print()
    if unmeasured:
        print("UNMEASURED SECTIONS (fail closed — do not read a missing row as satisfied):")
        for line in unmeasured:
            print(f"  ! {line}")
        return 3
    print(f"MEASURED: {len(rows)} premises, 0 unmeasured sections.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
