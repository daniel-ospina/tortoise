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
4. Step **2** had no row at all (``row("2"`` → 0 hits), so the bundle recorded
   its premise as ``STATIC`` — the only step whose basis it recorded that way
   (steps 3 and 10 have no census row either, but their premises are measured
   by other logs, ``01``/``05``) — and #9691 then falsified it *by hand*,
   finding that ``configured: false`` is **not** the catalog union on a hosted
   default graph. A premise no artifact re-reads is a premise nothing can
   falsify.

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
5. Step 2's THREE resolution arms each have a row, driven through the real
   ``pack_state.resolved_pack_set`` — and each row FALSIFIES on the defect it
   exists to catch: records resolving to the union (#9691), a starter set equal
   to the catalog, a first packs read that does not narrow a record-less graph.
6. A query the packs rows do not model leaves that section UNMEASURED (exit 3),
   never a satisfied row — including a query whose raise its caller SWALLOWS
   (the data leg, and the best-effort seeding write).
7. An unreadable pack catalog is UNMEASURED (exit 3), never a section of
   confident inverse verdicts: ``_resolve_catalog`` degrades to ``{}`` and
   never raises.
8. Rows 2c and 2e evaluate the whole clause they print — "**NOT** the catalog
   union" and "merges the **starter set**" — not a narrowed-ness proxy.
9. Row 2b's printed "data leg: not consulted — the arm returns early" is a claim
   about the reader's CONTROL FLOW, and `_require_served` cannot see it (it is a
   lower bound). A reader that consults the data leg on that arm must leave the
   section UNMEASURED (exit 3), not print the opposite of what it did.
10. Row 2b's OTHER clause — "the catalog union (#2714 indicator 3)" — is measured
   at the write gate that owns it (``pack_state.unapproved_kind_pack``: ``None``
   admits, an explicit set constrains), so narrowing that ``return None`` into a
   fallback FALSIFIES the row instead of leaving it CONFIRMED (round 4, P2).
11. ``TORTOISE_STARTER_PACKS`` is refused (exit 3, section UNMEASURED) rather than
   measured: an override makes rows 2a-2e describe a starter set the product does
   not ship, and it misreports in both directions — a false FALSIFIED when the
   override names an unknown pack, a green section when it names a subset
   (round 4, P3).
12. The documented recipe (``README.md`` → "Re-running the measurable half")
   reproduces ``MEASURED: 26 premises, 0 unmeasured sections.`` on a box that
   exports ``TORTOISE_API_KEY`` without a pepper, its stdout is byte-identical
   when the key is absent, and the documented command's fail-closed tail exits 3
   on a deliberate refusal.

Non-vacuity: on the pre-fix probe, (1) has no ``tortoise_health`` /
``tortoise_overview`` / ``tortoise_onboarding_state`` row, (2) has no
"is in a completion gate" row and reads 2 of the 3 gates, (4) prints
``CONFIRMED`` while the org-create fragment writes no ``team-named`` edge, and
(5) has no step-2 row at all — ``row("2"`` matched nothing, so the whole step-2
group of guards reds against it. Two mutations of the FIX were measured at
cd536eedb: removing the guard at its call site reds the exported arm
(``assert 3 == 0``), and mutating the probe's tail to a bare ``main()`` reds the
refusal arm (``assert 0 == 3``) — the fail-open that no in-process pin could see.

Static, plus THREE ``sys.executable`` child runs of the probe (no DB, no network).
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
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
    # #4282: `tortoise_overview` retired into the `graph_overview` container, so the
    # ADVERTISED name is the container and the old name is the retired row — the same
    # two-row shape `tortoise_health` has above.
    container = _measured(probe, "tool `tortoise_graph_overview` is on the MCP surface")
    assert container.startswith("PRESENT"), (
        f"`tortoise_graph_overview` is the advertised name and must measure PRESENT, "
        f"got {container!r}"
    )
    overview = _measured(probe, "tool `tortoise_overview` is on the MCP surface")
    assert overview.startswith("ABSENT"), (
        f"`tortoise_overview` is retired and must measure ABSENT, got {overview!r}"
    )
    assert RETIRED_USE_INSTEAD["tortoise_overview"] in overview, (
        "a retired tool's row must name its advertised replacement"
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


# ── onboarding: an interpretation is printed only for the value it explains ──

def test_the_restart_pending_gloss_follows_the_value(probe, monkeypatch) -> None:
    """`False` must not print the `True` interpretation.

    `state.restart_pending` really can answer `False` for this set (`state.py`
    documents `["team-named"] → False`, and the neighbouring row prints `False`
    for the connected set), so an unconditional `"— i.e. 'config written,
    harness NOT yet verified'"` would print a self-contradicting row in exactly
    the flipped case the census exists to detect — the same unconditional-suffix
    defect as the gate rows.
    """
    from tortoise.onboarding import state

    monkeypatch.setattr(state, "restart_pending", lambda _steps: False)
    probe.census_onboarding()
    measured = _measured(probe, "`restart_pending` for the set step 5 describes")
    assert measured.startswith("False"), (
        f"the row must print the derivation's value, got {measured!r}"
    )
    assert "harness NOT yet verified" not in measured, (
        f"a False derivation must not carry the True gloss: {measured!r}"
    )


# ── packs: step 2's premise is MEASURED, and each arm can falsify ──────────


#: One needle per resolution arm the packs section must measure. The pre-fix
#: census matched NONE of them (it had no step-2 row at all), which is how
#: #9691 falsified step 2's parenthetical by hand instead of by this probe.
STEP2_ARMS = (
    "the platform STARTER set is narrower",
    "no `:PackInstall` records AND no `:PackSelection` marker",
    "`configured=False` is NOT the catalog union",
    "the `:PackSelection` MARKER, not the records",
    "the FIRST packs read on a record-less unconfigured graph",
)


def test_step_2_has_a_measured_row_for_every_resolution_arm(probe) -> None:
    """Non-vacuity: the census had ZERO step-2 rows before this guard.

    Step 2's basis was the only one the bundle recorded as ``STATIC``
    (``row("2"`` had 0 hits; steps 3 and 10 have no census row either and are
    measured by other logs), i.e. the one premise nothing re-read — so the
    instrument could not falsify it and #9691 had to be caught by hand.
    """
    probe.census_packs()
    still_two = [(premise, measured) for step, premise, measured in probe.rows
                 if step == "2"]
    assert still_two, "the census still gives step 2 no measured row"
    premises = [premise for premise, _m in still_two]
    for needle in STEP2_ARMS:
        assert any(needle in p for p in premises), (
            f"the step-2 rows do not measure {needle!r}: {premises}")
    for premise, measured in still_two:
        assert measured.startswith(("CONFIRMED", "FALSIFIED")), (
            f"a premise row must carry a verdict, got {measured!r} for {premise!r}")


def test_the_record_arm_row_falsifies_if_records_resolve_to_the_union(
    probe, monkeypatch
) -> None:
    """#9691 re-introduced: a resolution that answers the union for a graph that
    HOLDS records. That is the exact defect the Settings copy shipped, so the
    row must read FALSIFIED rather than stay CONFIRMED on the flag alone.

    The wrapper keeps the REAL resolution's reads (so the row is still
    measurable) and only corrupts the answer — replacing the function outright
    would leave the arm unmeasured, which is a different (also pinned) exit.
    """
    from tortoise import pack_state

    real = pack_state.resolved_pack_set_for_graph

    def _union_whenever_records(_g):
        out = real(_g)
        if out.namespaces is not None:  # the arm that holds records
            return pack_state.ResolvedPackSet(namespaces=None, revision=0,
                                              configured=False)
        return out

    monkeypatch.setattr(pack_state, "resolved_pack_set_for_graph",
                        _union_whenever_records)
    probe.census_packs()
    verdict = _measured(probe, "`configured=False` is NOT the catalog union")
    assert verdict.startswith("FALSIFIED"), (
        f"records resolving to `namespaces=None` IS the #9691 defect: {verdict!r}")
    assert "namespaces=None" in verdict, (
        f"the row must print the set it resolved, got {verdict!r}")


def test_the_starter_row_falsifies_when_the_starter_set_is_the_catalog(
    probe, monkeypatch
) -> None:
    """A starter set equal to the catalog would make step 2's parenthetical TRUE."""
    from tortoise import pack_state

    monkeypatch.setattr(pack_state, "_resolve_catalog",
                        lambda: {"a": {"version": "1"}, "b": {"version": "1"}})
    monkeypatch.setattr(pack_state, "_starter_namespaces",
                        lambda *_a, **_k: ["a", "b"])
    probe.census_packs()
    verdict = _measured(probe, "the platform STARTER set is narrower")
    assert verdict.startswith("FALSIFIED"), (
        f"a starter set spanning the catalog does not narrow anything: {verdict!r}")
    assert "catalog−starter=NONE" in verdict, (
        f"the row must print the difference it measured, got {verdict!r}")
    # Review round 2, P2: the same state reaches row 2c, whose verdict must
    # include its own "NOT the catalog union" clause. It used to print
    # CONFIRMED while its evidence line read `catalog−resolved=NONE`.
    record_verdict = _measured(probe, "`configured=False` is NOT the catalog union")
    assert record_verdict.startswith("FALSIFIED"), (
        "a resolved set equal to the catalog IS the catalog union, so the row "
        f"must not claim the opposite: {record_verdict!r}")
    assert "catalog−resolved=NONE" in record_verdict, record_verdict


def test_the_self_heal_row_falsifies_when_the_read_does_not_seed(
    probe, monkeypatch
) -> None:
    """The union arm is transitory: the first packs READ seeds the starter set.

    If `get_tenant_packs` stops self-healing, a record-less graph STAYS on the
    catalog union and step 2's parenthetical becomes true again for it.
    """
    from tortoise import pack_state

    monkeypatch.setattr(pack_state, "ensure_tenant_packs",
                        lambda *_a, **_k: [])
    probe.census_packs()
    verdict = _measured(probe, "the FIRST packs read on a record-less unconfigured graph")
    assert verdict.startswith("FALSIFIED"), (
        f"a read that seeds nothing leaves the union arm in place: {verdict!r}")
    assert "still the union" in verdict, (
        f"the row must say the graph is still un-narrowed, got {verdict!r}")


def test_the_self_heal_row_falsifies_when_only_part_of_the_starter_set_is_seeded(
    probe, monkeypatch
) -> None:
    """Review round 2, P2: "merges the starter set" is not "narrows at all".

    The verdict was `listed and after.namespaces is not None and not
    after.configured`, so a seeding change recording ONE of the five starter
    namespaces printed CONFIRMED with `resolved=['dev']` — a clause about the
    starter set satisfied by a strictly weaker observation.
    """
    from tortoise import pack_state

    real_ensure = pack_state.ensure_tenant_packs

    def _one_pack(sdk, *, starter=None, graph_name=None):
        return real_ensure(sdk, starter=["dev"], graph_name=graph_name)

    monkeypatch.setattr(pack_state, "ensure_tenant_packs", _one_pack)
    probe.census_packs()
    verdict = _measured(probe, "the FIRST packs read on a record-less unconfigured graph")
    assert verdict.startswith("FALSIFIED"), (
        f"one seeded namespace is not the starter set: {verdict!r}")
    assert "resolved=['dev']" in verdict, (
        f"the row must print the set it resolved, got {verdict!r}")


def test_an_unreadable_catalog_leaves_the_packs_section_unmeasured(
    probe, monkeypatch, capsys
) -> None:
    """Review round 2, P2 — `_resolve_catalog` degrades to `{}`, it never raises.

    Unguarded, an unavailable pack registry made the section print confident
    verdicts under exit 0: 2a and 2e FALSIFIED (both false — nothing was
    readable) and 2c CONFIRMED while printing `catalog−resolved=NONE`, the
    inverse of the finding this PR records. The file's own contract is exit 3.
    """
    from tortoise import pack_state

    monkeypatch.setattr(pack_state, "_resolve_catalog", lambda: {})
    rc = probe.main()
    out = capsys.readouterr().out
    assert rc == 3, f"an unreadable catalog is unmeasurable, got exit {rc}: {out}"
    assert "census_packs" in out, out
    assert "could not be read" in out, out
    assert "`configured=False` is NOT the catalog union" not in out, (
        f"no row may print when its input could not be read: {out}")


def test_a_seed_refusal_swallowed_by_the_best_effort_seeder_is_unmeasured(
    probe, monkeypatch, capsys
) -> None:
    """Review round 2, P2 — the seed leg's refusal is swallowed one layer up.

    `ensure_tenant_packs` wraps its seeding write in `except Exception: …
    best-effort` (`pack_state.py:482`), so a changed seed write returns an
    EMPTY activation instead of raising into `main()`: the row then printed
    `FALSIFIED (0 records after the read)` — the log recording a broken product
    self-heal that the probe never read. The fake now RECORDS the refusal and
    `_require_served` raises on it regardless of `listed`.

    The subclass rewrites the seed text the stand-in sees, so the REAL
    ensure-then-read path runs against the write a regressed `pack_state`
    would issue.
    """
    base = probe._FakeGraph

    class _ChangedSeed(base):
        def query(self, cypher, params=None, **kw):
            if "MERGE (p:PackInstall" in cypher:
                cypher = cypher.replace("p.status = 'active'",
                                        "p.status = 'removed'")
            return super().query(cypher, params, **kw)

    monkeypatch.setattr(probe, "_FakeGraph", _ChangedSeed)
    rc = probe.main()
    out = capsys.readouterr().out
    assert rc == 3, (
        f"a seed write the stand-in refused must fail closed, got exit {rc}: {out}")
    assert "census_packs" in out, out
    assert "REFUSED ['seed']" in out, out
    assert "the FIRST packs read on a record-less unconfigured graph" not in out, (
        "the self-heal row must not print a verdict for a read whose write was "
        f"refused: {out}")


def test_an_unmodelled_pack_query_leaves_the_packs_section_unmeasured(
    probe, monkeypatch, capsys
) -> None:
    """Fail-closed polarity, end to end: exit 3, never a satisfied row.

    The packs rows answer three query shapes and RAISE on any other, so a change
    in `pack_state`'s readers surfaces as an UNMEASURED section instead of a row
    that never read what it prints.
    """
    from tortoise import pack_state

    def _unmodelled(graph):
        graph.query("MATCH (z:BrandNewLabel) RETURN z")
        raise AssertionError("unreachable: the fake graph must refuse that query")

    monkeypatch.setattr(pack_state, "resolved_pack_set_for_graph", _unmodelled)
    rc = probe.main()
    out = capsys.readouterr().out
    assert rc == 3, f"an unmeasurable premise must exit 3, got {rc}"
    assert "UNMEASURED SECTIONS" in out, out
    assert "census_packs" in out, out


def test_a_data_leg_refusal_cannot_be_swallowed_into_a_green_section(
    probe, monkeypatch, capsys
) -> None:
    """Review round 1, P1 — the one fail-open direction this file had.

    `graph_kind_namespaces` wraps its scan in `except Exception: continue` BY
    DESIGN (`pack_state.py:803-812`, "narrow the union, never widen it"), so an
    `AssertionError` from the fake's DATA-LEG branch is SWALLOWED: the union
    silently loses that leg, every row still prints CONFIRMED with "data leg:
    none", and the section never reds. Measured on the pre-fix probe: with the
    `RETURN DISTINCT n.` branch removed, all five step-2 rows printed CONFIRMED
    and `census_packs` added nothing to `unmeasured`.

    The `served` bookkeeping is the fix: the refusal happens in `census_packs`,
    where nothing catches it.
    """
    base = probe._FakeGraph

    class _NoKindScan(base):
        def query(self, cypher, params=None, **kw):
            if "RETURN DISTINCT n." in cypher:
                raise AssertionError("the data-leg scan's shape changed")
            return super().query(cypher, params, **kw)

    monkeypatch.setattr(probe, "_FakeGraph", _NoKindScan)
    rc = probe.main()
    out = capsys.readouterr().out
    assert rc == 3, (
        "a data-leg query the fake no longer models must make the census fail "
        f"closed, got exit {rc}")
    assert "did not read ['kind_scan']" in out, out
    assert "`configured=False` is NOT the catalog union" not in out, (
        "the arm whose data leg was refused must not be printed at all — a row "
        f"that appears while its read was swallowed is the defect: {out}")


def test_the_starter_override_is_refused_not_measured(probe, monkeypatch, capsys) -> None:
    """Review round 4, P3 — an env override made the section measure a set we do not ship.

    `_starter_namespaces` honours `TORTOISE_STARTER_PACKS` (`pack_state.py:254-268`), so
    an override silently substitutes the shipped `DEFAULT_STARTER_PACKS` for whatever the
    runner's shell carries — and it misreports in BOTH directions: measured in review,
    `TORTOISE_STARTER_PACKS=dev,typo-nonexistent` printed 2e FALSIFIED (*"the self-heal
    no longer seeds"*) over a documented `unknown in shared catalog — skipped`
    (`pack_state.py:462-468`) — our own environment reported as a product finding — and
    `=dev` alone read the whole section green over a one-namespace set.

    The section already refuses this for its sibling switch
    (`PACK_STATE_DISABLE_SELF_HEAL`), so the override gets the same refuse→UNMEASURED
    treatment rather than a disclosed-but-graded row.
    """
    monkeypatch.setenv("TORTOISE_STARTER_PACKS", "dev,typo-nonexistent")
    rc = probe.main()
    out = capsys.readouterr().out
    assert rc == 3, (
        "a starter set read through an override is not the shipped one, so the "
        f"section must fail closed; got exit {rc}: {out}")
    assert "census_packs" in out and "TORTOISE_STARTER_PACKS is set" in out, out
    assert "the platform STARTER set is narrower" not in out, (
        f"no packs row may be printed from an overridden starter set: {out}")
    assert "catalog union (#2714 indicator 3)" not in out, out


def test_the_union_arm_clause_about_the_catalog_union_is_measured(probe, monkeypatch) -> None:
    """Review round 4, P2 — "the catalog union (#2714 indicator 3)" was a literal.

    The verdict read only `union_arm.namespaces is None and not union_arm.configured`;
    what `None` MEANS to a consumer is the WRITE GATE's answer
    (`pack_state.unapproved_kind_pack`'s `if approved is None: return None`, whose
    docstring IS the #2714 indicator-3 statement). Measured in review: replacing that
    `return None` with a narrowed fallback (`approved = frozenset({'dev'})`) left all
    five packs rows CONFIRMED, `26 premises, 0 unmeasured sections`, exit 0 — the
    instrument green-lit the fallback being dead.

    The wrapper mutates exactly that branch and leaves the rest of the function real,
    so the row is falsified by the behaviour it prints and not by a broken reader.
    """
    from tortoise import pack_state

    real = pack_state.unapproved_kind_pack

    def _narrowed_fallback(kind, approved, *, attribute_bare=False):
        if approved is None:  # the #2714 indicator-3 branch, narrowed
            approved = frozenset({"dev"})
        return real(kind, approved, attribute_bare=attribute_bare)

    monkeypatch.setattr(pack_state, "unapproved_kind_pack", _narrowed_fallback)
    rc = probe.main()
    assert rc == 0, f"a falsified premise is DATA, not an unmeasured one; got exit {rc}"
    verdict = _measured(
        probe, "no `:PackInstall` records AND no `:PackSelection` marker")
    assert verdict.startswith("FALSIFIED"), (
        "`namespaces=None` no longer means 'the catalog union', so the row must "
        f"not claim it does: {verdict!r}")
    assert "admitted with approved=None (False)" in verdict, (
        f"the row must print the gate reading it measured, got {verdict!r}")


def test_the_union_arm_absence_clause_is_measured_not_asserted(
    probe, monkeypatch, capsys
) -> None:
    """Review round 3, P1 — row 2b prints an ABSENCE: "data leg: not consulted".

    That clause is an unconditional literal, and `_require_served` is a LOWER
    bound — it fails on a shape that was not read and says nothing about one
    that was. So a reader that consults the data leg on the record-less arm
    (hoisting `graph_kind_namespaces(g)` above the early return,
    `pack_state.py:974-975`) keeps every expectation satisfied while the row
    prints the opposite of what happened. Measured at `9d8350652`: the whole
    census reported `26 premises, 0 unmeasured sections`, exit 0, and this
    file was 18/18 green.

    The wrapper keeps the REAL resolution's reads (so the arms stay
    measurable) and only adds the read the clause says does not happen —
    replacing the function outright would leave the section unmeasured for a
    different reason.
    """
    from tortoise import pack_state

    real = pack_state.resolved_pack_set_for_graph

    def _also_consults_the_data_leg(graph):
        out = real(graph)
        if out.namespaces is None:  # the record-less arm, whose clause is printed
            pack_state.graph_kind_namespaces(graph)
        return out

    monkeypatch.setattr(pack_state, "resolved_pack_set_for_graph",
                        _also_consults_the_data_leg)
    rc = probe.main()
    out = capsys.readouterr().out
    assert rc == 3, (
        "a union arm that consults the data leg can no longer measure the "
        f"clause it prints, so the section must fail closed; got exit {rc}: {out}")
    assert "consulted the data leg" in out, out
    assert "no `:PackInstall` records" not in out, (
        f"the row must not print the clause it could not measure: {out}")


def test_the_stand_in_records_a_query_it_cannot_model(probe) -> None:
    """A swallowed refusal must still REACH `_require_served`.

    Both readers that consult the packs stand-in swallow a raise
    (`graph_kind_namespaces`' `except Exception: continue`, `pack_state.py:803-812`,
    and `ensure_tenant_packs`' best-effort seed at `:482`), so the raise alone
    is invisible to the instrument — and silence is what a row prints as "not
    consulted" or "none in the probe graph". The record is the only signal that
    a read was ATTEMPTED and refused.
    """
    graph = probe._FakeGraph()
    with pytest.raises(AssertionError, match="do not model this query"):
        graph.query("MATCH (z:BrandNewLabel) RETURN z")
    assert graph.refused == {"unmodelled"}, (
        "a refused query that its caller swallows must be recorded, or the "
        f"section cannot fail closed: {graph.refused!r}")
    assert not graph.served, "a refused query must not be counted as served"


def test_the_seeding_stand_in_refuses_a_changed_seed_write(probe) -> None:
    """Review round 1, P3 — the stand-in must not bookkeep a write it did not model.

    `get_tenant_packs` filters `status == 'removed'` out, so a stub that
    accepted ANY status would report "5 records after the read" over a
    regression that seeded them inactive. The branch pins the shape instead.
    """
    graph = probe._FakeGraph()
    with pytest.raises(AssertionError, match="status='active'"):
        graph.query(
            "MERGE (p:PackInstall {namespace: $ns}) SET p.version = $version",
            params={"ns": "dev", "version": "1.0.0"})
    assert not graph.installs, "a refused seed must leave the graph untouched"


def _run_recipe(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the README's recipe EXACTLY as documented — the probe as `__main__`.

    Importing the probe and calling `main()` would leave
    `if __name__ == "__main__": sys.exit(main())` unexecuted, so the fail-closed
    exit-code contract the reader relies on would be verified by a hand-copy
    instead of by the command in the README. Measured: mutating that tail to a
    bare `main()` keeps every in-process pin green while the documented command
    prints `UNMEASURED SECTIONS (fail closed …)` and exits **0**.
    """
    return subprocess.run(
        [sys.executable, str(PROBE_PATH)], cwd=str(ROOT), env=env,
        capture_output=True, text=True, timeout=120,
    )


def test_the_onboarding_section_measures_on_a_box_that_exports_the_hosted_key() -> None:
    """The re-run recipe must work where lanes actually run, not only on a laptop.

    `tortoise/auth.py:28-38` raises at **import** time when `TORTOISE_API_KEY` is
    set and `TORTOISE_SECRET_PEPPER` is unset — a STARTUP guard for the hosted
    server (#67). A fleet cell exports exactly that key for its MCP client, so
    `census_onboarding`'s `from tortoise.hosted_api import _CHECKPOINT_STEPS`
    raised, the `onboarding` section was UNMEASURED (rows 1 and 5a-5e never
    printed) and the probe exited 3: the documented recipe (`README.md` →
    "Re-running the measurable half") could not reproduce `26 premises, 0
    unmeasured sections` anywhere but a box without the key or with a pepper set.
    These pins are green over it because `tests/conftest.py:18` supplies the pepper
    the recipe does not — so this one reproduces the recipe, in a fresh process.

    Three arms: the exported arm proves the section measures at all and that the
    documented command's verdict is the published one; the unexported arm proves
    the fix does not CHANGE the output (both arms' full stdout is compared); and
    the refusal arm pins the contract the repo's ``__main__`` tail must not
    swallow (``main()`` returning 3 ⇒ the process exits 3). The probe's other
    refusal knobs are stripped too, so a red pin cannot be an ambient-env
    artifact — it means the hosted-key regression, or a real change in what a
    section can measure.
    """
    base = {k: v for k, v in os.environ.items()
            if k not in ("TORTOISE_API_KEY", "TORTOISE_SECRET_PEPPER",
                         "TORTOISE_STARTER_PACKS", "PACK_STATE_DISABLE_SELF_HEAL")}
    # Both arms share a hash seed: a set-ordered measured string would otherwise
    # differ between two processes and read as a distortion that is not there.
    base["PYTHONHASHSEED"] = "0"

    exported = _run_recipe(dict(base, TORTOISE_API_KEY="tt_hosted_key_a_cell_exports"))
    assert exported.returncode == 0, (
        "the recipe must exit 0 with the hosted key exported; a non-zero exit IS "
        "the census verdict (fail-closed 3 = a section unmeasured): "
        f"{exported.stdout[-1600:]} / {exported.stderr[-800:]}"
    )
    assert "MEASURED: 26 premises, 0 unmeasured sections." in exported.stdout, (
        f"the recipe's own verdict must be the published one: {exported.stdout[-1600:]}")
    assert "UNMEASURED SECTIONS" not in exported.stdout, (
        f"a section went unmeasured on a box with only the hosted key set: "
        f"{exported.stdout[-1600:]}")
    for needle in ("`team-named` is auto-satisfied",
                   "`connection-written` is in NO completion gate"):
        assert needle in exported.stdout, (
            f"row never printed: {needle!r} in {exported.stdout[-1600:]}")

    unexported = _run_recipe(dict(base))
    assert unexported.returncode == 0, (
        f"the recipe must exit 0 with no key either: {unexported.stdout[-1600:]}")
    assert unexported.stdout == exported.stdout, (
        "clearing the key for the import must not change the census output — "
        "otherwise the fix is distorting the measurement rather than unblocking it")

    # The documented command's FAIL-CLOSED contract, through the same `__main__`
    # the recipe uses. `TORTOISE_STARTER_PACKS` is a deliberate refusal
    # (`step_premise_census.py` census_packs), so `main()` returns 3 and the tail
    # must turn that into the process's exit code — a bare `main()` there would
    # print `UNMEASURED SECTIONS` and exit 0, the fail-open the contract forbids.
    refused = _run_recipe(dict(base, TORTOISE_STARTER_PACKS="dev"))
    assert refused.returncode == 3, (
        "the recipe must exit 3 when a section cannot be measured — exit 0 here is "
        f"exactly the fail-open the census forbids: {refused.stdout[-1200:]}")
    assert "UNMEASURED SECTIONS" in refused.stdout, refused.stdout[-1200:]
