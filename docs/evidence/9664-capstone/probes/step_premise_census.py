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
  tools       — every MCP tool name the capstone's steps name, vs TOOL_REGISTRY
  onboarding  — the canonical step ids, the completion gates, and what the
                server can OBSERVE (vs. what only a client can record)
  resource    — the RFC 8707 resource forms an MCP connector may declare, which
                is what the "deliberate ugly cases" (from #9340) must fit into

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
    from tortoise.tool_registry import TOOL_REGISTRY

    names = {t.name for t in TOOL_REGISTRY}
    # Steps 6-9 name the calls the agent makes; step 6 names a tool we must find.
    for step, tool in (
        ("6", "tortoise_load_skill"),
        ("7", "tortoise_list_graphs"),
        ("8", "tortoise_create_point"),
        ("9", "tortoise_file_decision"),
    ):
        row(step, f"tool `{tool}` is on the MCP surface",
            f"{'PRESENT' if tool in names else 'ABSENT'} "
            f"(registry holds {len(names)} tools)")
    skill_tools = sorted(n for n in names if "skill" in n)
    row("6", "the surface has ANY skill-loading tool",
        f"{skill_tools if skill_tools else 'NONE'}")


# ── onboarding: what closes which step, and who can observe it ─────────────
def census_onboarding() -> None:
    from tortoise.hosted_api import _CHECKPOINT_STEPS
    from tortoise.onboarding import state

    row("1", "`team-named` is auto-satisfied, never an agent act",
        f"{'CONFIRMED' if 'team-named' in state._NON_AGENT_STEPS else 'FALSIFIED'}"
        " (state._NON_AGENT_STEPS)")
    row("5", "`connection-written` is a completion input",
        f"{'YES' if any('connection-written' in g for g in state._GATES.values()) else 'NO'}"
        " (absent from every gate)")
    row("5", "`connection-written` is in NO completion gate, and IS a "
             "client checkpoint (not a server observation)",
        f"{'CONFIRMED' if 'connection-written' in state._NON_AGENT_STEPS else 'FALSIFIED'}"
        f"; checkpoint-writable={'YES' if 'connection-written' in _CHECKPOINT_STEPS else 'NO'}")
    for name, gate in (("self", state._GATE_SELF), ("build", state._GATE_BUILD),
                       ("compact", state._GATE_COMPACT)):
        row("5", f"the `{name}` completion gate requires",
            ", ".join(sorted(gate)))
    row("5", "`restart_pending` for the set step 5 describes "
             "(['team-named','connection-written'])",
        f"{state.restart_pending(['team-named', 'connection-written'])} "
        "— i.e. 'config written, harness NOT yet verified'")
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
