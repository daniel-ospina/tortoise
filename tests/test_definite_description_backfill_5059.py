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
  same census (identical :func:`plan_fingerprint`) and over a census whose rows
  already carry ``backfill_applied`` (empty actionable set).
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
