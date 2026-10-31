"""G5's existing half — the definite-description backfill planner (#5059).

What this file pins, and why each is load-bearing:

- **No destructive disposition exists.** ``DESTRUCTIVE_DISPOSITIONS`` is empty,
  and ``DESTRUCTIVE_DISPOSITIONS`` is disjoint from ``DISPOSITIONS``. Removing
  an existing Object is memory loss with no write path behind it, and this lane
  measured a name-pattern rule at **≈48% precision** on the ``the `` class
  (4,901 matches, 2,570 of them load-bearing).
- **I1 — ``ref_count >= 1`` ⇒ ``KEEP``, by dispatch order alone.** The hub guard
  precedes every rule that can emit a non-``KEEP`` disposition. Tested against
  the *real* measured hubs (``config/ci-surfaces.yml`` at 123 references, the
  six refs=1 must-keeps) **with a resolver that tries to resolve them** — the
  guard outranks the resolver.
- **I2 — a row it cannot positively classify is REPORTED, never advanced**; and
  an *unreadable* reference count is not read as zero.
- **Report-not-delete**: every record carries a rule id, a reason, a
  counterfactual, a ``from_state``/``to_state`` and ``destructive=False``.
- **Bounded and idempotent** — a pure function, proved by re-running over the
  same census (identical :func:`plan_fingerprint`) and by applying the plan's
  **own** ``to_state`` back onto the rows (so the marker the rule reads is the
  marker a rung writes): the applied rungs advance nothing on the re-run, while
  a ``MERGE_CANDIDATE`` stays a recorded candidate until ``#5006`` applies it.
- **The census is read-only** — the guard is executed against every mutation
  verb, not asserted in prose.
- **Each rule's authority is declared somewhere real** — a drift pin tying
  every rule id to the design-document line (or landed decision) it implements,
  the ``#4899`` ``DECLARED_CLASS`` pattern.

Hermetic: no database, no network, no environment mutation beyond ``enabled=``
being passed explicitly. The census figures are the issue's own measurement
(``#5059``), quoted rather than re-read, because the live graph is not this
change's to touch.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import definite_description_backfill as dd

ROOT = Path(__file__).resolve().parent.parent


# ── helpers ────────────────────────────────────────────────────────────────


def _row(name: str, ref_count: object = 0, **extra) -> dict:
    return {"id": f"o-{name}", "name": name, "ref_count": ref_count, **extra}


def _plan(rows, **kwargs) -> dict:
    return dd.plan_backfill(rows, enabled=True, **kwargs)


def _disposition(plan: dict, name: str) -> str:
    return plan["records"][f"o-{name}"]["disposition"]


def _rule(plan: dict, name: str) -> str:
    return plan["records"][f"o-{name}"]["rule_id"]


def _as_ref_count(record: dict) -> int | None:
    """The census ref_count a record echoes back, or ``None`` when it has none."""
    state = record.get("from_state")
    if isinstance(state, dict):
        value = state.get("ref_count")
        if isinstance(value, int):
            return value
    return None


#: The live graph's twelve most-referenced Objects (measured 2026-09-25 on
#: ``org_3326a01ea34ae595d84de5d8f9``). **Eleven of the twelve match a drop
#: pattern**, and #1 is a file path. Every one of these MUST survive.
REAL_HUBS = [
    ("config/ci-surfaces.yml", 123),
    ("the admin-merge rail", 70),
    ("cal-trigger.py", 64),
    ("the plan doc", 63),
    ("tortoise/hosted_api.py", 56),
    ("the extractor lane", 41),
    ("the merge rail", 38),
    ("the review gate", 33),
    ("the commit door", 29),
    ("the extractor", 24),
    ("the design doc", 22),
    ("the census script", 18),
]

#: The six rows an earlier sample named must-keep — **all refs = 1**, sitting in
#: a 2,614-row single-use class that also holds obvious noise. This is the
#: measurement that refutes "reference count separates keepers from junk", and it
#: is why the guard is ``>= 1`` and not ``>= 2``.
REFS_ONE_MUST_KEEP = [
    "guard 2",
    "_TOOL_BY_NAME",
    "NON_SDK_READ_OPERATIONS",
    "prune_backups",
    "daniel-ospina/agent-infra",
    "_capture_cost_props",
]

#: The noise sitting in the SAME single-use class. It must ALSO survive: no
#: mechanical test separates it from the must-keeps above, and the corrected
#: policy is "report, do not drop".
REFS_ONE_NOISE = ["the T2 guard", "the T3 rule"]


# ── the vocabulary cannot remove anything ──────────────────────────────────


def test_no_destructive_disposition_exists() -> None:
    """The structural half: there is no rung that removes or overwrites a row."""
    assert not dd.DESTRUCTIVE_DISPOSITIONS, (
        "a destructive backfill disposition was added; #5059 authorises "
        "report-not-delete only — that is an owner ruling, not a code decision"
    )
    assert not (dd.DESTRUCTIVE_DISPOSITIONS & dd.DISPOSITIONS)
    # Bound to locals first: ruff's SIM300 reads an all-caps attribute as a
    # constant, so `dd.DISPOSITIONS == <expr>` trips a bogus Yoda check.
    declared = set(dd.DISPOSITIONS)
    from_enum = {d.value for d in dd.Disposition}
    expected = {"KEEP", "RESOLVE", "RECLASSIFY", "MERGE_CANDIDATE", "REPORT"}
    assert declared == expected
    assert declared == from_enum


def test_every_record_is_marked_non_destructive() -> None:
    """The observable half: the property is on the artifact, not just in flow."""
    plan = _plan([_row(n, c) for n, c in REAL_HUBS]
                 + [_row(n, 1) for n in REFS_ONE_MUST_KEEP + REFS_ONE_NOISE]
                 + [_row("the staging-only constraint", 0)])
    assert plan["records"], "the fixture produced no records"
    for record in plan["records"].values():
        assert record["destructive"] is False, record
        assert record["disposition"] not in dd.DESTRUCTIVE_DISPOSITIONS
    assert plan["stats"]["destructive"] == 0


def test_rule_to_disposition_map_is_pinned() -> None:
    """A rule cannot be re-pointed at another disposition unnoticed."""
    assert dd.RULE_DISPOSITION == {
        dd.RULE_NOT_DESCRIPTION: dd.KEEP,
        dd.RULE_LOAD_BEARING: dd.KEEP,
        dd.RULE_ALREADY_APPLIED: dd.KEEP,
        dd.RULE_REF_COUNT_UNREADABLE: dd.REPORT,
        dd.RULE_RESOLVE_ARBITER: dd.RESOLVE,
        dd.RULE_RESOLVE_ARTICLE: dd.RESOLVE,
        dd.RULE_RECLASSIFY: dd.RECLASSIFY,
        dd.RULE_MERGE: dd.MERGE_CANDIDATE,
        dd.RULE_UNCLASSIFIED: dd.REPORT,
    }
    assert set(dd.RULE_DISPOSITION) == set(dd.RULES)
    for rule_id, disposition in dd.RULE_DISPOSITION.items():
        assert disposition in dd.DISPOSITIONS, rule_id


# ── I1: the hub guard, at ref_count >= 1 ───────────────────────────────────


def test_load_bearing_precedes_the_non_keep_rules() -> None:
    """The dispatch-order proof, read off the rule table itself.

    Every rule that can emit a non-``KEEP`` disposition must come AFTER the hub
    guard. If someone inserts a rung above it, this fails.
    """
    order = list(dd.RULES)
    guard = order.index(dd.RULE_LOAD_BEARING)
    non_keep = [
        i for i, r in enumerate(order) if dd.RULE_DISPOSITION[r] not in dd.INERT_DISPOSITIONS
    ]
    assert non_keep, "there must be at least one actionable rung"
    assert guard < min(non_keep), (
        "RULE_LOAD_BEARING must precede every non-KEEP rule; a rung was moved "
        "above the hub guard"
    )


@pytest.mark.parametrize("name,refs", REAL_HUBS)
def test_real_hubs_survive_with_a_resolver_trying_to_move_them(name: str, refs: int) -> None:
    """I1 against the real population — and the resolver does not outrank it.

    The resolver is the *most permissive* rung, so it is the sharpest probe: if
    the guard were reachable-after, this fixture would resolve/move a hub.

    ⚠️ A hub whose name is not description-shaped is a ``KEEP`` under
    ``dd.not_description`` rather than ``dd.load_bearing`` — the invariant the
    guard exists for is the **disposition**, and it holds either way. Asserting
    the exact rule here would be asserting something the guard does not promise.
    """
    plan = _plan(
        [{"id": f"o-{name}", "name": name, "ref_count": refs},
         _row("the plan doc referent", 1)],
        resolver=lambda n: f"o-{name}",  # resolves *this* row if ever consulted
    )
    assert _disposition(plan, name) == dd.KEEP
    assert _rule(plan, name) in {dd.RULE_NOT_DESCRIPTION, dd.RULE_LOAD_BEARING}


@pytest.mark.parametrize("name", REFS_ONE_MUST_KEEP + REFS_ONE_NOISE)
def test_single_use_rows_also_survive(name: str) -> None:
    """The refs=1 class — where the must-keeps actually live.

    The issue asks to prove a ≥2-reference Object survives. This proves the
    strictly stronger ≥1 property, because the measurement showed the must-keep
    sample is refs=1 and the noise is indistinguishable from it.
    """
    plan = _plan([_row(name, 1)], resolver=lambda n: "o-somewhere-else")
    assert _disposition(plan, name) == dd.KEEP
    assert _rule(plan, name) in {dd.RULE_NOT_DESCRIPTION, dd.RULE_LOAD_BEARING}


@pytest.mark.parametrize("refs", [1, 2, 3, 5, 17, 123])
def test_any_inbound_reference_is_a_keeper(refs: int) -> None:
    """Every ref_count ≥ 1, over a description-shaped name, is a KEEP."""
    plan = _plan([_row("the plan doc", refs)])
    assert _disposition(plan, "the plan doc") == dd.KEEP


def test_the_population_shape_yields_zero_actionable_hubs() -> None:
    """A fixture mirroring the measured census: hubs and single-use rows survive.

    2,570 load-bearing ``the `` rows + 2,614 single-use rows is the measured
    shape. Every referenced row must be KEEP — under whichever KEEP rule
    applies — and only unreferenced rows may be advanced at all, and then only
    to a non-destructive rung.
    """
    rows = [_row(n, c) for n, c in REAL_HUBS]
    rows += [_row(f"the hub {i}", 2 + (i % 60)) for i in range(200)]
    rows += [_row(f"the single-use {i}", 1) for i in range(200)]
    rows += [_row(f"the unused {i}", 0) for i in range(40)]
    plan = _plan(rows)
    for name, _refs in REAL_HUBS:
        assert _disposition(plan, name) == dd.KEEP
    # Every row the census reported as referenced is a KEEP — no exceptions.
    for record in plan["records"].values():
        both = _as_ref_count(record)
        if both is not None and both >= 1:
            assert record["disposition"] == dd.KEEP, record
    kept = plan["stats"]["by_disposition"][dd.KEEP]
    assert kept >= len(REAL_HUBS) + 400
    assert plan["stats"]["destructive"] == 0


# ── I2: fail-closed on the unreadable and the unclassifiable ───────────────


@pytest.mark.parametrize("bad", [None, "", "many", [], {}, 1.5, float("nan"), True])
def test_unreadable_ref_count_is_reported_not_assumed_zero(bad: object) -> None:
    """Absence of a measurement is not a measurement of zero.

    ⚠️ A row with an unreadable count cannot be *proved* unreferenced, so it
    must not advance — and the resolver must not be consulted for it.
    """
    calls: list[str] = []

    def resolver(name: str) -> str:
        calls.append(name)
        return "o-something"

    plan = _plan([_row("the plan doc", bad)], resolver=resolver)
    assert _disposition(plan, "the plan doc") == dd.REPORT
    assert _rule(plan, "the plan doc") == dd.RULE_REF_COUNT_UNREADABLE
    assert calls == [], "the resolver was consulted for an unmeasurable row"


@pytest.mark.parametrize(
    "name",
    [
        "the T2 guard",          # digit + capital
        "the T3 rule",           # digit + capital
        "the cycle-4 ruling",    # digit
        "the #4771 decision record",
        "the API",               # acronym — may be a real entity
        "the Staging-Only Constraint",
        "the config/ci-surfaces.yml",
        "the _TOOL_BY_NAME",
        "the 2.2 gate",
    ],
)
def test_unclassifiable_rows_are_reported_not_dropped(name: str) -> None:
    """The positive classifier is conservative; what it cannot classify is
    REPORTED. Nothing here is removed, and nothing is mislabelled."""
    plan = _plan([_row(name, 0)])
    assert _disposition(plan, name) == dd.REPORT
    assert _rule(plan, name) == dd.RULE_UNCLASSIFIED
    assert plan["records"][f"o-{name}"]["destructive"] is False


def test_positive_classification_requires_every_token_to_be_a_common_noun() -> None:
    """The classifier's gate, stated in both directions."""
    for name in ("the staging-only constraint", "the plan doc", "the lane ownership rule"):
        assert dd.is_positively_bare_definite_description(name), name
    for name in ("the T2 guard", "the cycle-4 ruling", "the API", "the x/y.py",
                 "the _TOOL_BY_NAME", "the owner's ruling"):
        assert not dd.is_positively_bare_definite_description(name), name


def test_shape_is_not_a_verdict() -> None:
    """``the owner`` has the shape and may still be a real entity.

    Shape alone never decides a rung — that is what the guard and the
    conservative classifier are for. This is the objection that killed the
    ``the ``-prefix rule (70–77% fire rate on real statement rows).
    """
    assert dd.is_bare_definite_description("the owner")
    plan = _plan([_row("the owner", 3)])
    assert _disposition(plan, "the owner") == dd.KEEP


# ── the rungs ──────────────────────────────────────────────────────────────


def test_resolves_via_the_injected_lookup() -> None:
    plan = _plan(
        [_row("the plan doc", 0), _row("docs/plan-5059.md", 4)],
        resolver=lambda n: "o-docs/plan-5059.md" if n == "the plan doc" else None,
    )
    record = plan["records"]["o-the plan doc"]
    assert record["disposition"] == dd.RESOLVE
    assert record["rule_id"] == dd.RULE_RESOLVE_ARBITER
    assert record["to_state"]["denotes"] == "o-docs/plan-5059.md"
    assert record["to_state"]["referent_validated"] is True
    assert record["destructive"] is False


def test_resolves_by_the_article_stripped_name_without_a_model() -> None:
    """A deterministic resolution: ``the plan doc`` denotes ``plan doc``."""
    plan = _plan([_row("the plan doc", 0), _row("plan doc", 7)])
    record = plan["records"]["o-the plan doc"]
    assert record["disposition"] == dd.RESOLVE
    assert record["rule_id"] == dd.RULE_RESOLVE_ARTICLE
    assert record["to_state"]["denotes"] == "o-plan doc"


def test_reclassify_is_additive_and_preserves_the_existing_kind() -> None:
    """The highest non-resolving rung overwrites NO field."""
    plan = _plan([_row("the staging-only constraint", 0, object_kind="dev:issue")])
    record = plan["records"]["o-the staging-only constraint"]
    assert record["disposition"] == dd.RECLASSIFY
    assert record["rule_id"] == dd.RULE_RECLASSIFY
    assert record["to_state"]["overwrites"] == []
    assert record["to_state"]["kind_untouched"] is True
    assert "objectKind" in record["preserves"]
    assert record["destructive"] is False


def test_reclassify_outranks_merge() -> None:
    """The ladder: resolve > reclassify > merge > destructive.

    A positively-classified duplicate is RECLASSIFY — the less-invasive rung —
    not a merge candidate. Only a row the classifier refuses reaches MERGE.
    """
    plan = _plan([_row("the plan doc", 0), _row("the plan doc", 0)])
    for record in plan["records"].values():
        assert record["disposition"] == dd.RECLASSIFY


def test_merge_candidate_is_recorded_but_never_applied() -> None:
    """A spelling-duplicate the classifier refused is a RECORDED candidate.

    The merge machinery is ``#5006``'s (union-of-attachments + the never-across
    bar), so this module records and stops.
    """
    plan = _plan([_row("the T2 guard", 0), _row("the T2 Guard", 0)])
    for record in plan["records"].values():
        assert record["disposition"] == dd.MERGE_CANDIDATE
        assert record["rule_id"] == dd.RULE_MERGE
        assert record["to_state"]["applied"] is False


def test_not_a_description_is_out_of_scope() -> None:
    plan = _plan([_row("tortoise/hosted_api.py", 0), _row("guard 2", 0)])
    for name in ("tortoise/hosted_api.py", "guard 2"):
        assert _disposition(plan, name) == dd.KEEP
        assert _rule(plan, name) == dd.RULE_NOT_DESCRIPTION


# ── report-not-delete: every record is auditable ───────────────────────────


def test_every_record_carries_rule_reason_and_counterfactual() -> None:
    """The ``#4899`` trio, asserted on the record rather than on the call."""
    plan = _plan(
        [_row(n, c) for n, c in REAL_HUBS]
        + [_row("the staging-only constraint", 0),
           _row("the T2 guard", 0), _row("the T2 Guard", 0),
           _row("the unreadable one", "many"),
           _row("tortoise/hosted_api.py", 0),
           _row("the plan doc", 0), _row("plan doc", 3)]
        + [_row(n, 1) for n in REFS_ONE_MUST_KEEP]
    )
    assert plan["records"]
    for record in plan["records"].values():
        assert record["rule_id"] in dd.RULES, record
        assert record["reason"], record
        assert record["counterfactual"], "no counterfactual ⇒ no recovery path"
        assert record["disposition_key"], record
        assert record["from_state"] or record["to_state"], record


# ── the flag contract (#4899 safeguard 1) ──────────────────────────────────


def test_flag_off_produces_an_empty_zeroed_plan(monkeypatch) -> None:
    monkeypatch.delenv(dd.ENV_FLAG, raising=False)
    plan = dd.plan_backfill([_row("the plan doc", 0)])
    assert plan["enabled"] is False
    assert plan["records"] == {}
    assert plan["stats"]["enabled"] is False
    assert plan["stats"]["rows"] == 0
    assert plan["stats"]["destructive"] == 0


def test_flag_on_is_read_through_the_declared_truthy_contract(monkeypatch) -> None:
    """``"true"``/``"1"``/``"yes"`` agree here too (#4097)."""
    for spelling in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(dd.ENV_FLAG, spelling)
        assert dd.backfill_enabled() is True, spelling
    for spelling in ("0", "false", "no", "off", ""):
        monkeypatch.setenv(dd.ENV_FLAG, spelling)
        assert dd.backfill_enabled() is False, spelling


# ── bounded and idempotent ─────────────────────────────────────────────────


def test_the_planner_is_pure_so_two_runs_are_byte_identical() -> None:
    """Idempotence's first half: the plan is a function of the census alone."""
    rows = [_row(n, c) for n, c in REAL_HUBS] + [
        _row("the staging-only constraint", 0),
        _row("the T2 guard", 0),
        _row("the plan doc", 0), _row("plan doc", 2),
    ]
    first = _plan(rows)
    second = _plan(rows)
    assert first["stats"]["fingerprint"] == second["stats"]["fingerprint"]
    assert first["records"] == second["records"]
    assert first["order"] == second["order"]


def test_replanning_an_applied_census_produces_an_empty_actionable_set() -> None:
    """Idempotence's second half: applying changes nothing on the next run.

    The plan is applied *to the fixture's recorded state* (no graph involved),
    and the re-run must advance nothing.
    """
    rows = [
        _row("the staging-only constraint", 0),
        _row("the T2 guard", 0), _row("the T2 Guard", 0),
        _row("the plan doc", 0), _row("plan doc", 2),
        _row("the unreadable one", "many"),
    ]
    before = _plan(rows)
    actionable_before = {
        rid for rid, r in before["records"].items()
        if r["disposition"] not in dd.INERT_DISPOSITIONS
    }
    assert actionable_before, "the fixture must have something to do"

    applied = []
    for row in rows:
        record = before["records"].get(f"o-{row['name']}") or {}
        if record.get("disposition") in dd.INERT_DISPOSITIONS:
            applied.append(row)
            continue
        applied.append({
            **row,
            "backfill_applied": True,
            "backfill_class": record["to_state"].get("backfill_class")
            if isinstance(record["to_state"], dict) else None,
        })
    after = _plan(applied)
    assert after["stats"]["actionable"] == 0, after["stats"]
    for rid in actionable_before:
        assert after["records"][rid]["rule_id"] == dd.RULE_ALREADY_APPLIED
        assert after["records"][rid]["disposition"] == dd.KEEP


def test_applying_the_plans_own_to_state_makes_the_second_run_a_no_op() -> None:
    """Idempotence through the module's **own** contract, not a hand-built row.

    An applier is told to write ``to_state``. If a rung's ``to_state`` omits the
    marker the idempotence rule reads, applying the plan changes nothing on the
    next run and every count of "what remains" is wrong — so this applies
    ``to_state`` **verbatim** rather than hand-writing ``backfill_applied``.
    """
    rows = [
        _row("the staging-only constraint", 0),          # RECLASSIFY
        _row("the plan doc", 0), _row("plan doc", 2),   # RESOLVE (article)
        _row("the T2 guard", 0), _row("the T2 Guard", 0),  # MERGE_CANDIDATE
        _row("the unreadable one", "many"),             # REPORT
        _row("config/ci-surfaces.yml", 5),              # KEEP
    ]
    before = _plan(rows)
    applied_rungs = {dd.RESOLVE, dd.RECLASSIFY}
    assert {
        r["disposition"] for r in before["records"].values()
    } >= applied_rungs | {dd.MERGE_CANDIDATE, dd.REPORT}, before["stats"]

    applied = []
    for row in rows:
        to_state = before["records"][f"o-{row['name']}"]["to_state"]
        applied.append({**row, **to_state} if isinstance(to_state, dict) else row)

    after = _plan(applied)
    # The rungs the plan describes an application for are now no-ops...
    assert after["stats"]["by_disposition"][dd.RESOLVE] == 0, after["stats"]
    assert after["stats"]["by_disposition"][dd.RECLASSIFY] == 0, after["stats"]
    assert after["stats"]["actionable"] < before["stats"]["actionable"]
    for rid, record in before["records"].items():
        if record["disposition"] in applied_rungs:
            assert after["records"][rid]["rule_id"] == dd.RULE_ALREADY_APPLIED
            assert after["records"][rid]["disposition"] == dd.KEEP
    # ...and the one rung that describes NO application of its own stays a
    # recorded candidate: #5006 applies merges, so `applied` is deliberate.
    merges = [
        r for r in after["records"].values()
        if before["records"][r["id"]]["disposition"] == dd.MERGE_CANDIDATE
    ]
    assert merges
    assert all(r["disposition"] == dd.MERGE_CANDIDATE for r in merges)

    # A third run over the same state is stable — applying is monotone.
    assert _plan(applied)["stats"]["actionable"] == after["stats"]["actionable"]


def test_every_applied_rung_records_the_marker_the_idempotence_rule_reads() -> None:
    """The writer and the reader of the applied marker agree on one spelling.

    A rung that describes an application but omits :data:`dd.APPLIED_MARKER`
    makes its own rows permanently actionable — the rule would decide on the
    marker's absence rather than on the row's state.
    """
    plan = _plan([
        _row("the staging-only constraint", 0),
        _row("the plan doc", 0), _row("plan doc", 2),
        _row("the T2 guard", 0), _row("the T2 Guard", 0),
    ])
    seen: set[str] = set()
    for record in plan["records"].values():
        to_state = record["to_state"]
        seen.add(record["disposition"])
        if record["disposition"] in {dd.RESOLVE, dd.RECLASSIFY}:
            assert isinstance(to_state, dict), record
            assert to_state.get(dd.APPLIED_MARKER) is True, record
        if record["disposition"] == dd.MERGE_CANDIDATE:
            assert to_state["applied"] is False, record
            assert to_state.get(dd.APPLIED_MARKER) is not True, record
    assert seen >= {dd.RESOLVE, dd.RECLASSIFY, dd.MERGE_CANDIDATE}, seen


def test_bounded_one_disposition_per_row() -> None:
    rows = [_row(f"the unused {i}", 0) for i in range(500)]
    plan = _plan(rows)
    assert len(plan["records"]) == 500
    assert len(plan["order"]) == 500
    assert plan["stats"]["rows"] == 500


# ── the census is read-only, and a cap that bites is reported ──────────────


def test_the_census_query_has_no_mutating_verb() -> None:
    dd.assert_read_only(dd.CENSUS_QUERY)  # does not raise


@pytest.mark.parametrize(
    "verb", ["CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "FOREACH"],
)
def test_the_read_only_guard_rejects_every_mutation_verb(verb: str) -> None:
    with pytest.raises(dd.ReadOnlyViolation):
        dd.assert_read_only(f"MATCH (o:Object) {verb} o.name = 'x' RETURN o")


def test_the_census_query_projects_the_marker_the_idempotence_rule_reads() -> None:
    """A marker the census does not return is a marker the planner cannot see.

    ``RULE_ALREADY_APPLIED`` reads :data:`dd.APPLIED_MARKER`. If
    :data:`dd.CENSUS_QUERY` does not project it, the rule is **unreachable
    through the module's own read path** — a rule reading a column the census
    never returns can never fire, and every applied row would be re-planned on
    every run. Pinned on the query text *and* executed through ``census_rows``.
    """
    for field in (dd.APPLIED_MARKER, dd.APPLIED_CLASS):
        assert f" AS {field}" in dd.CENSUS_QUERY, field

    def run(query: str, params: dict):
        dd.assert_read_only(query)          # the executor itself re-checks
        return [{
            "id": "o-applied", "name": "the plan doc", "ref_count": 0,
            "ref_edges": [], "status": None, "object_kind": None,
            dd.APPLIED_MARKER: True, dd.APPLIED_CLASS: "definite_description",
        }]

    rows, warnings = dd.census_rows(run)
    assert warnings == []
    plan = _plan(rows)
    record = plan["records"]["o-applied"]
    assert record["rule_id"] == dd.RULE_ALREADY_APPLIED
    assert record["disposition"] == dd.KEEP
    assert plan["stats"]["actionable"] == 0, plan["stats"]


def test_census_rows_passes_only_the_read_only_query_and_reports_a_cap() -> None:
    seen: list[str] = []

    def run(query: str, params: dict):
        seen.append(query)
        dd.assert_read_only(query)          # the executor itself re-checks
        assert "Object" in query
        return [{"id": f"o-{i}", "name": f"the object {i}", "ref_count": 0}
                for i in range(3)]

    rows, warnings = dd.census_rows(run, limit=3)
    assert len(rows) == 3
    assert seen and "$limit" in seen[0]
    assert any("TRUNCATED" in w for w in warnings), warnings

    rows, warnings = dd.census_rows(run)
    assert len(rows) == 3
    assert warnings == []


# ── totality / fail-open ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "census",
    [None, "not a census", 42, {"name": "the plan doc", "ref_count": 0},
     [None, 7, "x"], [{"name": None}], [{"ref_count": 0}]],
)
def test_the_planner_is_total_on_a_malformed_census(census: object) -> None:
    plan = _plan(census)
    assert plan["enabled"] is True
    assert plan["stats"]["destructive"] == 0
    for record in plan["records"].values():
        assert record["disposition"] in dd.DISPOSITIONS


def test_a_raising_resolver_is_fail_open() -> None:
    def boom(_name: str) -> str:
        raise RuntimeError("arbiter down")

    plan = _plan([_row("the plan doc", 0)], resolver=boom)
    assert _disposition(plan, "the plan doc") in {dd.RECLASSIFY, dd.REPORT}
    assert any("resolver failed" in w for w in plan["warnings"]), plan["warnings"]
    assert plan["stats"]["destructive"] == 0


def test_an_unvalidated_resolution_is_recorded_and_flagged() -> None:
    plan = _plan([_row("the plan doc", 0)], resolver=lambda n: "not-in-the-census")
    record = plan["records"]["o-the plan doc"]
    assert record["disposition"] == dd.RESOLVE
    assert record["to_state"]["referent_validated"] is False
    assert any("not in the census" in w for w in plan["warnings"])


# ── the drift pin: every rule is declared somewhere real ───────────────────


def test_every_rule_is_declared_by_its_source() -> None:
    """``#4899``'s ``DECLARED_CLASS`` pattern, for the backfill's rules.

    A rule that stops being declared stops being justified. ``anchor`` (when
    present) must sit on the **same line** as ``declares``, so a generic phrase
    cannot be satisfied by an unrelated occurrence elsewhere in a large file.
    """
    assert set(dd.DECLARED_BY) == set(dd.RULES), (
        "every rule must carry a declaration pin, and no pin may outlive its rule"
    )
    for rule_id, pin in dd.DECLARED_BY.items():
        path = ROOT / pin["source"]
        assert path.is_file(), f"{rule_id}: declared source {pin['source']} missing"
        lines = path.read_text(encoding="utf-8").splitlines()
        hits = [ln for ln in lines if pin["declares"] in ln]
        assert hits, f"{rule_id}: {pin['declares']!r} not found in {pin['source']}"
        anchor = pin.get("anchor")
        if anchor:
            assert any(anchor in ln for ln in hits), (
                f"{rule_id}: anchor {anchor!r} is not on the same line as "
                f"{pin['declares']!r} in {pin['source']}"
            )
        assert pin.get("label"), f"{rule_id}: a pin needs a human-readable label"


# ── the DESIGN DOC must not invert or over-claim this file's measurement ────


def _g5_destructive_half() -> str:
    """§4.3's relocation subsection — the passage a ruling is made against."""
    doc = ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
    text = doc.read_text(encoding="utf-8")
    heading = "#### G5's destructive half"
    assert heading in text, (
        f"{heading!r} is gone from {doc.name} — the measured state of G5's "
        "destructive half (#5333) no longer has a home."
    )
    start = text.index(heading)
    return text[start:text.index("### S3 — RESOLVE", start)]


def _measured_the_class() -> tuple[int, int]:
    """``(matches, load_bearing)`` for the bare ``the `` class, from THIS module.

    Read from the module's own docstring (the recorded ``#5059`` measurement)
    rather than restated here, so the doc's percentage is checked against the
    number it claims to quote and a corrected measurement moves both.
    """
    source = Path(dd.__file__).read_text(encoding="utf-8")
    match = re.search(
        r"``the `` alone: \*\*(\d[\d,]*), of which ([\d,]+) are load-bearing",
        source,
    )
    assert match, (
        "the recorded `the ` measurement is no longer in the module docstring — "
        "this guard would pass vacuously; re-point it (#5333)."
    )
    return (int(match.group(1).replace(",", "")), int(match.group(2).replace(",", "")))


def test_the_doc_states_the_load_bearing_share_not_its_complement():
    """⚠️ 48% is the DELETE PRECISION, and the complement is what the bar weighs.

    ``#5059`` measured 4,901 ``the <X>`` rows of which 2,570 are load-bearing —
    *"≈48% precision on an irreversible delete"* (this module's docstring,
    ``:20``). The load-bearing share is therefore ≈52%, and the design document
    inverted them: it read *"≈48% of the `the <X>` rows are load-bearing"*,
    which says a bare name-pattern delete is *worse* than the measurement
    supports and, read literally, that a majority of the class is droppable.
    The passage exists to justify never letting a name pattern be authority, so
    the number that argues for the strictest bar must be the measured one.
    """
    matches, load_bearing = _measured_the_class()
    share = round(load_bearing / matches * 100)
    precision = 100 - share
    assert share != precision, (
        f"the recorded measurement came out even ({share}%), so this guard "
        "cannot distinguish the two quantities — re-read the docstring."
    )

    section = _g5_destructive_half()
    stated = re.search(r"≈(\d+)% of the `the <X>` rows are load-bearing", section)
    assert stated, (
        "the evidence-bar paragraph no longer states the load-bearing share of "
        "the `the <X>` class — the one measured number behind 'never a name "
        "pattern' is gone (#5333)."
    )
    assert int(stated.group(1)) == share, (
        f"the doc states ≈{stated.group(1)}% of the `the <X>` rows are "
        f"load-bearing; the measurement is {load_bearing:,} of {matches:,} = "
        f"{share}% (≈{precision}% is the DELETE PRECISION, i.e. the complement — "
        "not the load-bearing share)."
    )
    assert re.search(rf"≈{precision}% precision", section), (
        f"the doc must state the ≈{precision}% delete precision that the "
        f"{share}% load-bearing share implies — the two numbers are different "
        "quantities and both belong in the sentence (#5333)."
    )


#: The no-writer ABSOLUTES this guard enumerates — the spellings of "`archived`
#: has no writer" that a docs-only edit would reintroduce. Scoped to ONE line
#: that also names `archived` (the claim is about that status, never about the
#: raw layer), and enumerated rather than generative: the guard's message may
#: only claim the spellings listed here (#10069 review P2, third round).
_ABSENT_WRITER_CLAIM = re.compile(
    r"archived[^.\n]{0,80}\b(?:no writer|never written|nothing writes|"
    r"not written|unwritten)\b"
    r"|\b(?:no writer|never written|nothing writes|not written|unwritten)\b"
    r"[^.\n]{0,80}archived",
    re.IGNORECASE,
)

#: A claim that is ALREADY SCOPED to the acting class is not the refuted
#: absolute (#10069 review, P3). `no PASS writes it` is this decision's own
#: qualified form, so "`archived` is never written by a relocation pass" is
#: TRUE — measured: without this exemption the enumeration above reds it, and
#: the failure message then calls a correct statement the refuted absolute
#: (a false BLOCK, and the message the writer was told to satisfy). Scoped to
#: the SAME ±90-character neighbourhood as the match, never to the whole line:
#: these paragraphs are single lines of thousands of characters, so a line-wide
#: exemption would let an unrelated absolute ride in on a distant "by a pass".
_PASS_SCOPED_CLAIM = re.compile(
    r"\b(?:by|through|from)\s+(?:(?:a|an|any|the)\s+)?(?:relocation\s+)?pass\b",
    re.IGNORECASE,
)


def _unqualified_writer_absolutes(section: str) -> list[str]:
    """Lines of ``section`` asserting a no-writer absolute WITHOUT a scope.

    The co-located guard's hit list, minus any match whose own neighbourhood
    scopes the claim to the acting class (``_PASS_SCOPED_CLAIM``). One line is
    reported once, however many matches it carries.
    """
    hits: list[str] = []
    for line in section.splitlines():
        for match in _ABSENT_WRITER_CLAIM.finditer(line):
            window = line[max(0, match.start() - 90):match.end() + 90]
            if _PASS_SCOPED_CLAIM.search(window):
                continue
            hits.append(line)
            break
    return hits


def test_the_no_writer_guard_exempts_a_pass_scoped_claim_but_not_a_bare_one():
    """The co-located guard's grading, measured in both directions.

    It has to be able to tell the refuted absolute from the QUALIFIED form the
    same test demands (`no pass writes it`), or it reds a correct sentence and
    then reads like it is asking for one. The exemption is deliberately
    NEIGHBOURHOOD-scoped: the passage's paragraphs are single lines, so the
    scope word must sit next to the claim it scopes (#10069 review, P3).
    """
    assert _unqualified_writer_absolutes(
        "`archived` is never written by a relocation pass."
    ) == []
    assert _unqualified_writer_absolutes(
        "`archived` is not written by any pass."
    ) == []
    assert _unqualified_writer_absolutes("Nothing writes `archived` today.")
    assert _unqualified_writer_absolutes(
        "`archived` has no writer at all today."
    )
    # …and the scope has to be NEAR the claim, not merely on the same line.
    assert _unqualified_writer_absolutes(
        "Nothing writes `archived` today. " + "x" * 400 + " — written by a pass."
    )


def test_the_doc_does_not_claim_archived_has_no_writer():
    """`archived` IS writable today through the generic, unvalidated path.

    The pre-fix passage read *"Nothing writes `archived` today — it is declared
    and reserved (`sdk.py:2955`)"*, and cited that line as the authority. That
    line is an entry in ``_ALLOWED_TRANSITIONS`` (``sdk.py:2942``), a
    declarative **Point**-lifecycle spec that **nothing consults** — so it
    neither establishes that the Object family has no writer nor bounds what a
    write does. Meanwhile ``_update_entity`` issues an unvalidated
    ``SET n += $props`` (``assembly.py:4108``), which is the same path the
    document itself names for ``outdated`` — an example, not a closed set. The
    consequence the passage must state is the one a relocation pass depends on:
    **the existing `archived` population is unmeasured**, not empty.
    """
    section = _g5_destructive_half()
    assert "Nothing writes `archived`" not in section, (
        "the passage again claims nothing writes `archived`. It is false: the "
        "generic unvalidated write path reaches it on an :Object (#5333)."
    )
    # The CLAIM, not one spelling of it (#10069 review P2, third round). The
    # first form of this guard checked the literal "Nothing writes `archived`",
    # and a rephrase evaded it ("nothing writes it at all" — lowercase, pronoun
    # instead of the noun). Widening to the single token "nothing writes" was
    # STILL a literal: measured, inserting "`archived` has no writer at all
    # today." left every arm of this guard green. So the guard now rejects any
    # of the no-writer ABSOLUTES below, on one line with `archived`, in either
    # order — and the claim stated in the message is only ever as wide as the
    # spellings enumerated here, never "any rephrasing".
    hits = _unqualified_writer_absolutes(section)
    assert not hits, (
        "the passage asserts, in a spelling this guard enumerates, that "
        "NOTHING writes `archived` — the refuted absolute: `archived` IS "
        "reachable through the generic unvalidated write path (and through "
        "pack-declared status folds), so the population is UNMEASURED, not "
        "empty. Qualify the claim to the writers this decision governs (a "
        "PASS) — and, if the spelling is one this guard enumerates, say so "
        "in the neighbourhood ('… by a relocation pass'), because an "
        "enumerated spelling with no scope next to it reads as the absolute "
        f"(#5333). Offending line(s): {hits[:2]}"
    )
    assert "no pass writes it" in section.lower(), (
        "the fold must still be stated as inert until authorised — the "
        "QUALIFIED form ('no pass writes it') is what replaces the refuted "
        "absolute. Without it the paragraph stops saying the fold does not "
        "run today, and a lane reads the permitted form as a live "
        "mechanism (#5333)."
    )
    # …and the hedge that keeps the population UNMEASURED rather than zero: an
    # asserted provenance ('was placed by hand') over-claims for data the same
    # subsection says is uncounted, and invites 'scan the update_entity audit
    # trail' as an implementation of the enumeration precondition.
    assert (
        "may have been" in section.lower()
        and "placed by hand" in section.lower()
        and "pack-declared status fold" in section.lower()
    ), (
        "the exception sentence must HEDGE the provenance of the "
        "already-`archived` population ('may have been placed by hand', and "
        "or by a pack-declared status fold) — asserting it upgrades a "
        "measurement the passage calls UNMEASURED into a known one "
        "(#5333)."
    )
    assert "UNMEASURED" in section, (
        "the passage must state that the number of existing `archived` Objects "
        "is UNMEASURED — enumerating it is a precondition of any relocation "
        "pass (#5333)."
    )
    # The OTHER half of the same claim, and the one `_ABSENT_WRITER_CLAIM`
    # cannot enumerate (#10069 review, fourth and fifth rounds): the target
    # paragraph read *"No first-class producer or lifecycle transition writes
    # `archived`"*. That is FALSE — a pack-declared status fold IS a
    # lifecycle transition, and `status_folds.py:25` validates a fold's status
    # only against the DECLARING kind's `statuses` ("a kind that declares a
    # lifecycle OWNS its state"), so a pack declaring `archived` writes it.
    # It is also self-contradicting: the same subsection says the population
    # `may have been` placed `by a pack-declared status fold`. The absolute
    # carries none of the guard's spellings ("transition writes"), so it is
    # pinned the other way round — by the two EXISTING WRITERS the claim must
    # name. ⚠️ The axis matters: `tortoise_update_entity` (`tool_registry.py`)
    # IS core-shipped and IS the writer, so a provenance qualifier
    # ("core-shipped") is false about this repo and must not be pinned; the
    # breadth that holds is purpose-built-ness. Scoped to the claim CLAUSE
    # (sentence start → `declares it terminal`), so dropping EITHER named
    # writer reds — a paragraph-scoped check would stay green on the pair.
    target_lines = [
        line for line in section.splitlines()
        if line.startswith("**The target, if a relocation is ever authorised")
    ]
    assert len(target_lines) == 1, (
        "the target paragraph ('The target, if a relocation is ever "
        f"authorised …') is gone or duplicated ({len(target_lines)} found) — "
        "the passage stating what a relocation writes has no home (#5333)."
    )
    target = target_lines[0].lower()
    claim_start = target.find("**no purpose-built producer")
    claim_end = target.find("declares it terminal", claim_start + 1)
    assert claim_start != -1 and claim_end > claim_start, (
        "the target paragraph's no-writer claim is gone or unrecognisable — "
        "it must open 'No purpose-built producer writes `archived`, and no "
        "`_ALLOWED_TRANSITIONS` transition does' and run to the map's "
        "`declares it terminal` entry (#10069 review P2)."
    )
    claim = target[claim_start:claim_end]
    assert "generic unvalidated" in claim and "pack-declared status fold" in claim, (
        "the no-writer claim must name BOTH writers that exist — the generic "
        "unvalidated `update_entity` path and a pack-declared status fold — "
        "in the same clause. Unqualified it is refuted by this repo in two "
        "directions: by `_fold_object_status` (a fold writes whatever status "
        "the declaring kind's vocabulary names) and by "
        "`tortoise_update_entity` itself (`tool_registry.py`), which is "
        "core-shipped and reaches the store with no vocabulary check — so a "
        "lane enumerating the already-`archived` population (a stated "
        "precondition of any relocation) would leave one or both writers out "
        "of the scan and undercount a population this passage calls "
        "UNMEASURED (#10069 review P2)."
    )
    lowered = section.lower()
    assert "_allowed_transitions" in lowered, (
        "the passage must name the map it cites (`_ALLOWED_TRANSITIONS`) so the "
        "reader can check what it is (#5333)."
    )
    assert "nothing consults" in lowered, (
        "the passage must say that nothing consults `_ALLOWED_TRANSITIONS`; "
        "citing it as an authority is what made the claim false (#5333)."
    )


#: The map the design document cites as *declaring* the Point lifecycle while
#: `nothing consults it` (§4.3, `#5333`). The claim is about READS of the name.
TRANSITION_MAP = "_ALLOWED_TRANSITIONS"


def _transition_map_sites(
    root: Path, symbol: str = TRANSITION_MAP
) -> tuple[list[str], list[str]]:
    """``(definitions, reads)`` of ``symbol`` under ``root``, as ``rel:line``.

    ⚠️ **A READ, not a mention.** The claim the design document makes is that no
    code *consults* the map. A textual search cannot tell that apart from a
    comment or a docstring that merely names it — and this tree is comment-dense
    (the map's own block is nine comment lines), so a substring search both
    reds a guard whose message says "now has a production reader" for prose, and
    cannot see a reader that lives in the file the definition is in.

    Parsing settles both: a definition binds the name (`ast.Store`), every
    consultation loads it (`ast.Load`), and comments do not survive the parse at
    all.
    """
    definitions: list[str] = []
    reads: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        if symbol not in text:
            continue  # cheap prefilter: parsing 244 modules for nothing is tier1 cost
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Name) or node.id != symbol:
                continue
            site = f"{rel}:{node.lineno}"
            (reads if isinstance(node.ctx, ast.Load) else definitions).append(site)
    return definitions, reads


def test_the_transition_map_the_doc_cites_has_no_production_reader() -> None:
    """The doc's own reason for distrusting `_ALLOWED_TRANSITIONS`, executed.

    If a consumer is ever wired up, the doc's *"nothing consults that map"*
    becomes false and this reds — the pin is the claim, in both directions.

    The scan covers **`sdk.py` too**, and it counts READS rather than textual
    mentions. Both halves are load-bearing: the map and every verb that would
    plausibly consult it (`update_point`, `retract_point`, `supersede_point`)
    live in the same file, so a guard that skipped the definition's own file
    could not red on the most likely consumer — the one the map's own comment
    names — while reding on a comment elsewhere.
    """
    definitions, reads = _transition_map_sites(ROOT / "tortoise")
    assert definitions, (
        f"`{TRANSITION_MAP}` is not defined anywhere under `tortoise/`, so the "
        "design doc's citation of it is stale — re-point this guard (#5333)."
    )
    assert reads == [], (
        f"`{TRANSITION_MAP}` now has a production reader ({reads}), so the "
        "design doc's 'nothing consults that map' is stale — and so is the "
        "claim that it cannot bound a write (#5333)."
    )
    assert "sdk.py:" in definitions[0], (
        f"the map moved out of `sdk.py` (defined at {definitions}); the doc's "
        "`sdk.py:2942` citation is stale (#5333)."
    )


def test_the_reader_scan_sees_a_consumer_in_the_definitions_own_file(tmp_path):
    """Non-vacuity: a reader beside the definition is a READ, and is seen.

    This is the shape the reviewer named: wiring the map into `update_point` —
    the verb the map's own comment says it is *"NOT consulted by … per-call"* —
    inside `tortoise/sdk.py`. The pre-fix guard skipped that whole file
    (`if path == definition: continue`), so it reported no reader here.
    """
    (tmp_path / "sdk.py").write_text(
        f'{TRANSITION_MAP} = {{"live": frozenset()}}\n'
        "\n"
        "def update_point(status, cur):\n"
        f"    if status not in {TRANSITION_MAP}[cur]:\n"
        "        raise ValueError(status)\n",
        encoding="utf-8",
    )
    definitions, reads = _transition_map_sites(tmp_path)
    assert definitions == ["sdk.py:1"], definitions
    assert reads == ["sdk.py:4"], (
        "a read of the map in the file that defines it is not reported — the "
        "guard is blind to the one place a consumer is most likely to be "
        "wired (#5333)."
    )


def test_the_reader_scan_ignores_a_mention_that_is_not_a_read(tmp_path):
    """Non-vacuity, other direction: prose is not a reader.

    A comment or a docstring naming the map does not consult it. Counting
    mentions would red this guard — whose message says *"now has a production
    reader"* — on a comment.
    """
    (tmp_path / "notes.py").write_text(
        f'# {TRANSITION_MAP} is a Point-lifecycle spec; nothing consults it.\n'
        "def describe():\n"
        f'    """`{TRANSITION_MAP}` is declared and reserved."""\n'
        "    return None\n",
        encoding="utf-8",
    )
    definitions, reads = _transition_map_sites(tmp_path)
    assert (definitions, reads) == ([], []), (
        "a comment/docstring naming the map was counted as a definition or a "
        f"read ({definitions}, {reads}) — prose is not a consultation (#5333)."
    )


# ── the DESIGN DECISION is recorded, not open — and authorises nothing ──────


def test_the_design_decision_is_recorded_add_only_never_delete():
    """§4.3's relocation subsection must carry the decision, all of it.

    Recorded 2026-10-26 against #5333's design half. The decision has four
    load-bearing clauses — and, beyond them, a provenance claim and two
    index/table claims this pin also holds. A passage that drops ANY of them
    re-opens a question the recorded chain has already answered (``#5059``'s
    options and recommendation; §2.4.2 row 3's never-delete rule; D7's
    appended-Event lifecycle; the 2026-10-24 capstone's real-subject-matter
    finding):

    1. **add-only** — after COMMIT no pass may remove, archive or withdraw a
       committed row, and a field write only by an appended lifecycle Event;
    2. **never a physical delete** — the only deletion trigger is GDPR /
       retention (Option C is closed on the recorded *never* recommendation);
    3. **the reconstruction invariant** — the fold's event stream is the
       reconstruction source; reversal is a journaled status write back;
    4. **no authorisation** — whether a relocation may ever RUN stays the
       owner's (task 1); the passage must not read as a licence to build one.

    The failure this pin prevents: a docs-only edit that softens or deletes
    one clause (most plausibly the never-delete one, which reads as
    inconvenient next to a "clean up the graph" ask) merging green because
    the module-level guards never re-read the passage (#4309 silent-drop
    class). Non-vacuous by construction: **every assertion reads the DECISION
    PARAGRAPH itself**, never the enclosing subsection, because two of the four
    clauses ("reversal is a journaled status write back", "it authorises
    nothing") already exist in the prose ABOVE it — a section-wide assertion
    stays green after the clause is deleted from the decision, which is the
    false-PASS this test exists to forbid (measured: the softened paragraph
    kept the section-wide form green).
    """
    section = _g5_destructive_half()
    assert "The design decision" in section, (
        "the design decision paragraph is gone from §4.3's relocation "
        "subsection — G5's destructive half again reads as an open question "
        "(#5333)."
    )
    # THE SCOPE: the decision paragraph and nothing else. It is one blank-line
    # separated paragraph, so it ends at the next newline.
    start = section.index("**The design decision")
    end = section.find("\n", start)
    decision = (section[start:] if end == -1 else section[start:end]).lower()
    assert "no pass may remove" in decision, (
        "the add-only clause is gone: after COMMIT no pass may remove a "
        "committed row from the graph, archive it as a storage action, or "
        "withdraw it from recall except through the journaled fold (#5333)."
    )
    # The PROHIBITION's exception list must carry BOTH carve-outs the same
    # paragraph permits (#10069 review, P2). It read "— except through the one
    # journaled fold below —", and "the one journaled fold" resolves to the
    # NEXT sentence's successorless `restatus`→`archived`; the D7
    # `ObjectSuperseded` arm — permitted forty words later, and itself a
    # withdrawal (its `status='superseded'` IS recall-excluded,
    # `commit_ops.py:51`/`:59`) — was not in it. A lane implementing the
    # operative "What a pass may never do" list literally therefore rejects the
    # live `apply_supersessions` path. Presence of `no pass may remove` alone
    # must not satisfy this: measured, the one-armed exception read green.
    assert "or through the d7 `objectsuperseded` arm" in decision, (
        "the prohibition's exception list must name BOTH permitted removal "
        "arms — the journaled successorless fold AND the D7 "
        "`ObjectSuperseded` arm where a visible successor exists. Without "
        "the second, 'no pass may … withdraw it from recall' contradicts "
        "the D7 arm the same paragraph permits, and a guard written from "
        "this list refuses successor resolution (#5333)."
    )
    assert "only deletion trigger is gdpr / retention" in decision, (
        "the never-delete rule is gone or softened — nothing is ever deleted "
        "on supersession; the only deletion trigger is GDPR / retention "
        "(§2.4 row 3, #5333)."
    )
    assert "option c is closed on the recorded recommendation" in decision, (
        "the never-delete clause lost its ground: Option C (deletion) is "
        "closed on the RECORDED recommendation (*never*), not merely "
        "deprioritised — without it the clause reads as a preference a later "
        "lane may revisit (#5333)."
    )
    assert "reconstruction source" in decision, (
        "the reconstruction invariant is gone — the fold's event stream is "
        "the reconstruction source (#5333)."
    )
    assert "reversal is a journaled status write back" in decision, (
        "the reconstruction invariant is gone — reversal must stay a "
        "journaled status write back (#5333)."
    )
    assert "authorises nothing" in decision and "task 1" in decision, (
        "the decision paragraph must keep stating that it AUTHORISES "
        "NOTHING and that the authorisation (task 1) remains the owner's — "
        "a recorded design answer is not a licence to build the pass "
        "(#5333)."
    )
    # `ONLY` must be SCOPED to the successorless case. Unqualified it denies the
    # D7 arm the same paragraph permits — `ObjectSuperseded` writes
    # `status='superseded'`, and `OBJECT_TERMINAL_STATUSES` IS the
    # recall-exclusion set (commit_ops.py:51/:59), so supersession is a
    # removal-from-recall. A lane implementing "the ONLY permitted form" would
    # reject the live `apply_supersessions` path, or route rows that HAVE a
    # successor through `restatus`→`archived` and break successor resolution
    # while reporting success (#10069 review P1).
    assert "where no visible successor exists" in decision, (
        "'the ONLY permitted form of removal-from-recall' is unscoped again — "
        "it must be limited to the successorless case, or it contradicts the "
        "D7 arm (`ObjectSuperseded` → `status='superseded'`) that the same "
        "paragraph permits and that `OBJECT_TERMINAL_STATUSES` already "
        "recall-excludes (#5333)."
    )
    assert "only permitted form of removal-from-recall" in decision, (
        "the permitted form must be named in the SAME paragraph as the "
        "prohibition — 'no pass may remove, archive or withdraw' without it "
        "reads as forbidding the journaled `restatus`→`archived` fold that "
        "the paragraph itself records as the target (a lane implementing the "
        "guard cannot tell prohibition from permitted form) (#5333)."
    )
    # The FORECLOSURE itself, not merely its grounds. Measured (#10069 review
    # P1): while only the GDPR/retention quote and Option C were asserted,
    # rewriting this sentence to "A physical delete is PERMITTED to a
    # relocation pass" left every arm green — the primary clause of the
    # decision was unpinnned, and no other guard in the tree reads it.
    assert "a physical delete is foreclosed to every pass" in decision, (
        "the never-delete foreclosure is gone or inverted. It is the clause "
        "the decision exists for, and `grep -rn foreclosed tests/ config/ "
        "tools/` finds no other guard that reads it: without this assertion "
        "a docs-only edit can PERMIT a physical delete and merge green "
        "(#5333)."
    )
    assert "or withdraw it from recall" in decision, (
        "the prohibition lost its third verb — 'no pass may remove, archive "
        "or withdraw' is three distinct prohibitions, and dropping 'withdraw "
        "it from recall' leaves the recall-view removal unnamed (#5333)."
    )
    # The rest of clause 1: the two other verbs of the prohibition, and the
    # field-write rule. Without these the paragraph can be INVERTED — "fields
    # may be rewritten freely" is D7's defect class, and it is the one clause
    # an implementer is most likely to relax — while this pin stays green.
    assert "archive it as a storage action" in decision, (
        "the prohibition lost 'archive it as a storage action' — dropping "
        "the qualifier leaves 'archive' reading as the very mechanism the "
        "paragraph permits (a journaled `restatus` to `archived`), so the "
        "clause contradicts itself again (#5333)."
    )
    assert (
        "no field of a committed row may be written except by an appended "
        "lifecycle event" in decision
    ), (
        "D7's field-write rule is gone or inverted: after COMMIT no field of "
        "a committed row may be written except by an appended lifecycle "
        "Event — a raw `SET` onto `Object.status` is #3304's defect class "
        "(#5333)."
    )
    # The two capstone figures may not stand uncited: they have no committed
    # artifact behind them, so the paragraph must name their record.
    assert "#7949" in decision and "#9889" in decision, (
        "the two capstone figures (0 of 34 bare-definite drops; share 0.54) "
        "lost their provenance — they are auditable only in the #7949 "
        "capstone pass recorded on #5333, whose probe #9889 has yet to land, "
        "so a reader cannot re-derive them from the tree (#5333)."
    )
    # The gaps table is the section's index of what is still open; it may not
    # report the design question open after the decision answered it. (The
    # table sits ABOVE the `#### G5's destructive half` heading, so it is read
    # from the document — a subsection-scoped assertion could not see it.)
    doc_text = (
        ROOT / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
    ).read_text(encoding="utf-8")
    g5_rows = [
        line for line in doc_text.splitlines()
        if line.startswith("| **G5** → `#5333`")
    ]
    assert len(g5_rows) == 1, (
        "§4.3's gaps table no longer carries exactly one G5 row "
        f"({len(g5_rows)} found) — the reader's index of what is still open "
        "in G5 is gone (#5333)."
    )
    g5_row = g5_rows[0].lower()
    assert "no decision on the ~4,900" not in g5_row, (
        "the §4.3 gaps table still reports 'no decision on the ~4,900 "
        "`the <X>` Objects' — the decision is recorded below it, so the row "
        "must narrow the open item to the AUTHORISATION (task 1) (#5333)."
    )
    assert "no decision authorising a relocation pass" in g5_row, (
        "the §4.3 gaps table no longer states what IS still open — a "
        "relocation pass has no authorising decision (task 1, #5333)."
    )
