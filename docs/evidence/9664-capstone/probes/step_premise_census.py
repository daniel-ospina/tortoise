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
  packs       — step 2's "the default is catalog union + core": the THREE
                resolution arms of `pack_state.resolved_pack_set` (driven
                through the real function), the starter-set/catalog relation,
                and the self-heal that narrows a record-less graph on its
                first packs READ
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

import contextlib
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
    # The measured question is the NAME sweep, and the row says so: whether a
    # tool could load a skill without naming it `skill` is not something a name
    # sweep can answer, and a row that answered "ANY skill-loading tool" from it
    # would report a capability it never read (the compound-premise defect this
    # file's docstring forbids).
    skill_named = sorted(n for n in names if "skill" in n)
    row("6", "no registered tool NAME contains `skill`",
        f"{skill_named if skill_named else 'CONFIRMED (0 of the ' + str(len(names)) + ' live names)'}")


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


# ── packs: step 2's "the default is catalog union + core" (#9691) ──────────
#
# Step 2's parenthetical is the ONLY premise in the ten-step table that the
# census never measured — the evidence bundle called it "premise STATIC" — and
# that is how it was falsified by hand instead of by this instrument: on a
# hosted organization's default graph `configured: false` is NOT the catalog
# union (#9691). The rows below measure the arms of the ONE authority
# (`pack_state.resolved_pack_set`), so the next falsification shows up here.


class _FakeResult:
    def __init__(self, result_set) -> None:
        self.result_set = result_set


class _FakeGraph:
    """A graph HANDLE for the packs rows — inputs only, never a decision.

    `pack_state.resolved_pack_set_for_graph` is what these rows MEASURE, and
    it reads a graph through exactly three query shapes. This stand-in answers
    those shapes and nothing else, and an unmodelled query RAISES — so a change
    in the reader surfaces as an UNMEASURED section (exit 3), never as a
    silently satisfied row. The RESOLUTION is never re-implemented here: the
    install records and the marker are the INPUTS, and the real function
    resolves them.

    The KIND scans (the data leg of the union) all answer empty: every probe
    graph holds no data, which is what makes `namespaces == recorded` below a
    readable assertion instead of a coincidence. A row that depends on it says
    so in the row itself.
    """

    def __init__(self, installs=(), marker=None) -> None:
        self.installs = [tuple(r) for r in installs]
        self.marker = marker

    def query(self, cypher: str, params=None, **_kw):
        p = params or {}
        if "MERGE (p:PackInstall" in cypher:  # ensure_tenant_packs' seeding
            ns = p.get("ns")
            if ns and not any(r[0] == ns for r in self.installs):
                self.installs.append((ns, p.get("version"), "active",
                                      "starter", p.get("now")))
            return _FakeResult([])
        if ":PackInstall" in cypher:
            return _FakeResult(self.installs)
        if ":PackSelection" in cypher:
            return _FakeResult([self.marker] if self.marker else [])
        if "RETURN DISTINCT n." in cypher:  # graph_kind_namespaces — no data
            return _FakeResult([])
        raise AssertionError(
            "the packs rows do not model this query, so they cannot measure "
            f"what depends on it: {cypher[:100]!r}")


@contextlib.contextmanager
def _sdks_absent(graph: _FakeGraph):
    """Substitute ONLY the SDK→graph-handle seam, for the self-heal row.

    `get_tenant_packs` / `ensure_tenant_packs` are the REAL functions under
    measurement — this probe holds no SDK, so the two helpers that turn one
    into a graph handle are pointed at the fake graph. Nothing about the
    resolution is re-implemented, and the substitution is undone in a
    ``finally`` so a raised row cannot leak it into the next section.
    """
    from tortoise import pack_state

    saved = (pack_state._target_graph, pack_state._resolved_graph_name)
    pack_state._target_graph = lambda *a, **k: graph
    pack_state._resolved_graph_name = lambda *a, **k: "9664-probe"
    try:
        yield
    finally:
        pack_state._target_graph, pack_state._resolved_graph_name = saved


def census_packs() -> None:
    from tortoise import pack_state

    catalog = pack_state._resolve_catalog()
    starter = pack_state._starter_namespaces()
    only_catalog = sorted(set(catalog) - set(starter))
    row("2", "the platform STARTER set is narrower than the shared catalog "
             "(a provisioned graph is not seeded with the union)",
        f"{'CONFIRMED' if only_catalog else 'FALSIFIED'} "
        f"(catalog {len(catalog)}={', '.join(sorted(catalog))}; "
        f"starter {len(starter)}={', '.join(starter)}; "
        f"catalog−starter={only_catalog or 'NONE'})")

    # The three arms, in the order `resolved_pack_set` documents them. Every
    # arm is driven through the real function; only the INPUTS differ.
    union_arm = pack_state.resolved_pack_set_for_graph(_FakeGraph())
    row("2", "no `:PackInstall` records AND no `:PackSelection` marker → "
             "`namespaces=None`, the catalog union (#2714 indicator 3)",
        f"{'CONFIRMED' if union_arm.namespaces is None and not union_arm.configured else 'FALSIFIED'}"
        f" (namespaces={'None' if union_arm.namespaces is None else sorted(union_arm.namespaces)};"
        f" configured={union_arm.configured})")

    seeded = _FakeGraph(installs=((ns, "1.0.0", "active", "starter",
                                   "2026-01-01T00:00:00Z") for ns in starter))
    record_arm = pack_state.resolved_pack_set_for_graph(seeded)
    resolved = sorted(record_arm.namespaces) if record_arm.namespaces is not None else None
    missing = sorted(set(catalog) - set(resolved or ()))
    row("2", "`configured=False` is NOT the catalog union: `:PackInstall` "
             "records with no marker resolve to the RECORDED set (#9691)",
        f"{'CONFIRMED' if resolved == sorted(starter) and not record_arm.configured else 'FALSIFIED'}"
        f" (configured={record_arm.configured}; namespaces={resolved}; "
        f"catalog−resolved={missing or 'NONE'}; data leg: none in the probe graph)")

    marker = (1, "2026-01-01T00:00:00Z", "9664-probe")
    marked_arm = pack_state.resolved_pack_set_for_graph(
        _FakeGraph(installs=tuple(seeded.installs), marker=marker))
    row("2", "the `:PackSelection` MARKER, not the records, is what makes "
             "`configured=True`",
        f"{'CONFIRMED' if not record_arm.configured and marked_arm.configured else 'FALSIFIED'}"
        f" (records alone → configured={record_arm.configured};"
        f" records+marker → configured={marked_arm.configured}, revision={marked_arm.revision})")

    # The union arm is REAL but transitory for a hosted graph: the first packs
    # READ seeds the starter set (`get_tenant_packs` self-heal, #318/#9583), so
    # the state a user reaches at step 2 is the narrowed one. Measured by
    # running the real ensure-then-read path, not by reading its source.
    if pack_state._self_heal_disabled():
        raise RuntimeError(
            "PACK_STATE_DISABLE_SELF_HEAL is set — the self-heal arm cannot be "
            "measured in this environment (unset it and re-run)")
    fresh = _FakeGraph()
    with _sdks_absent(fresh):
        listed = pack_state.get_tenant_packs(None)
    after = pack_state.resolved_pack_set_for_graph(fresh)
    row("2", "the FIRST packs read on a record-less unconfigured graph merges "
             "the starter set — so step 2's union arm does not survive the "
             "surface the user opens",
        f"{'CONFIRMED' if listed and after.namespaces is not None and not after.configured else 'FALSIFIED'}"
        f" ({len(listed)} records after the read; "
        f"resolved={sorted(after.namespaces) if after.namespaces is not None else 'None (still the union)'};"
        f" configured={after.configured})")


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
    for section in (census_tools, census_onboarding, census_packs,
                    census_resource):
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
