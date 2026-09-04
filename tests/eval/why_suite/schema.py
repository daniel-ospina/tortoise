"""W3-b why-suite fixture/gold/baseline schemas (DM-9 conventions, epic #2080
issue #2100; plan §4.3.4 DM-6/7/8 discipline adapted).

Planted-conflict gold conventions (the suite's research-original part):

* **manifest (fixture side, harness-visible)**: the jointly-pinned corpus
  manifest — deterministic seed spec (composition 40 = 30 conflicted [10
  P9 / 5 decision / 5 superseded subsets] + 10 clean + per-family planted
  role templates) shared with W4-a's E2E-1 seeding.  A ``gold`` key inside
  the manifest is a VALIDATION ERROR (answer-key contamination, Cat-35).
* **gold (SEALED)**: per-planted-point expectations — ``expected.
  conflict_surfacing`` + ``expected.dig_deeper_targets`` (``{kind,
  target_role}`` pointers) + ``expected.support_chain_sufficient`` /
  ``tradeoff_sufficient``.  Gold lives in its own dir; ``fixtures_hash``
  covers manifest AND gold (a gold-only edit changes the hash).
* **Manifest resolution at contract-validation time**: every gold entry's
  ``point_id`` (topic key) must exist in the manifest's deterministic topic
  list AND its family's plant spec must support every expected pointer kind
  (a clean point may never expect a nand/superseded/tradeoff target; a
  non-superseded topic may never expect a superseded-kind target) — schema
  conformance alone cannot catch gold referencing point IDs the seed never
  plants, so the resolution happens HERE against the jointly-pinned spec.
* **baseline**: W2-b committed-baseline discipline reused wholesale
  (justification-to-bless, config mismatch ⇒ inconclusive, regression fails
  CI, skipped never counts as pass).  The why-suite metric vocabulary
  differs (conflict-surfacing / dig-deeper navigation / sufficiency rates +
  false-positive rate) so this module validates the suite shapes and
  implements compare/bless against the suite METRIC_DIRECTIONS, mirroring
  the harness schema exactly; the standing bars (>= 0.95 / 0.0) are armed
  immediately (config.reflex == "graded" — the W4 assembly this suite grades
  is already delivered, unlike W3-a's null-reflex seam).

Hermetic: pure validation + hashing — no DB, no network, no LLM.
"""

from __future__ import annotations

from pathlib import Path

from eval.write_path import schema as ws

# Re-export shared primitives (baseline discipline mirrors write_path).
sha256_bytes = ws.sha256_bytes
sha256_file = ws.sha256_file
VERDICT_PASS = ws.VERDICT_PASS
VERDICT_REGRESSION = ws.VERDICT_REGRESSION
VERDICT_INCONCLUSIVE = ws.VERDICT_INCONCLUSIVE
VERDICT_VALUES = ws.VERDICT_VALUES

SCHEMA_VERSION = 1
SUITE_VALUES = frozenset({"why_suite"})

# ── Why-suite metric vocabulary (the E2E-7 graded surface) ────────────────
# Directions follow the W2/W3 convention: maximize = higher-better,
# minimize = lower-better.
METRIC_DIRECTIONS: dict[str, str] = {
    # conflict-surfacing: of the planted-conflict points, the fraction whose
    # SURFACED context identifies the contradiction (conflicts.contested
    # true + >= 1 NAND + a dig-deeper nand pointer) — E2E-1/E2E-7 >= 0.95.
    "conflict_surfacing_rate": "maximize",
    # dig-deeper navigation: fraction of gold-expected {kind, target}
    # pointers that resolve to the correct planted point from the surfaced
    # context — E2E-7 >= 0.95.
    "dig_deeper_navigation_accuracy": "maximize",
    # support-chain sufficiency: "why is this believed?" answerable from the
    # surfaced context's support_chain + ep (measured, not aspirational).
    "support_chain_sufficiency": "maximize",
    # trade-off sufficiency: decision points whose surfaced tradeoffs answer
    # "which alternative does EP favor?" (>= 2 alternatives with ep_weight +
    # mitigation + the favored alternative is the max-ep_weight one).
    "tradeoff_sufficiency": "maximize",
    # false-positive arm: clean points whose surfaced context invents a
    # contradiction (conflicts present / contested / nand pointer) — 0 bar.
    "false_positive_rate": "minimize",
}
METRIC_VALUES = frozenset(METRIC_DIRECTIONS)

# Standing quality bars (issue #2100 Targets + epic E2E-1/E2E-7).  Armed
# from day one — the graded artifact (W4 assembly) is delivered; the suite
# IS the A11 pilot gate (config.reflex == "graded").
CONFLICT_SURFACING_FLOOR = 0.95
DIG_DEEPER_NAV_FLOOR = 0.95
FALSE_POSITIVE_TOLERANCE = 0.0

# Controlled vocabulary (ONTOLOGY §5 — the why-block response vocabulary).
DIG_DEEPER_KINDS = frozenset({"supports", "nand", "superseded", "tradeoff"})
FAMILY_VALUES = frozenset({"p9", "decision", "superseded", "plain", "clean"})

# Family → pointer kinds the plant structure CAN legitimately surface
# (clean points carry ONLY a supports pointer — no contradiction surface;
# decision/superseded are conflicted subsets with extra dimensions).
FAMILY_CONFLICTED: dict[str, bool] = {
    "p9": True,
    "plain": True,
    "decision": True,
    "superseded": True,
    "clean": False,
}
FAMILY_DECISION = frozenset({"decision"})
FAMILY_SUPERSEDED = frozenset({"superseded"})


def _require_mapping(doc: object, where: str, issues: list[str]) -> None:
    if not isinstance(doc, dict):
        issues.append(f"{where}: expected an object, got {type(doc).__name__}")


def _reject_unknown_keys(doc: dict, allowed: frozenset[str], where: str, issues: list[str]) -> None:
    for key in doc:
        if key not in allowed:
            issues.append(f"{where}: unexpected key {key!r}")


def _expect_str(doc: dict, key: str, where: str, issues: list[str]) -> str | None:
    value = doc.get(key)
    if not isinstance(value, str) or not value.strip():
        issues.append(f"{where}.{key}: expected a non-empty string, got {value!r}")
        return None
    return value


def _expect_bool(doc: dict, key: str, where: str, issues: list[str]) -> bool | None:
    value = doc.get(key)
    if not isinstance(value, bool):
        issues.append(f"{where}.{key}: expected a boolean, got {value!r}")
        return None
    return value


def _expect_int(
    doc: dict, key: str, where: str, issues: list[str], *, minimum: int = 0
) -> int | None:
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        issues.append(f"{where}.{key}: expected an int >= {minimum}, got {value!r}")
        return None
    return value


def _expect_enum(
    doc: dict, key: str, allowed: frozenset[str], where: str, issues: list[str]
) -> str | None:
    value = doc.get(key)
    if value is None:
        issues.append(f"{where}.{key}: expected one of {sorted(allowed)} (missing)")
        return None
    if value not in allowed:
        issues.append(f"{where}.{key}: expected one of {sorted(allowed)}, got {value!r}")
        return None
    return value


def read_json(path: Path) -> dict:
    import json

    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ── Manifest (fixture side) validation ─────────────────────────────────────


def _validate_topics(manifest: dict, issues: list[str]) -> None:
    """Topic lists per family + composition cross-check.  The manifest pins
    the deterministic topic keys the seed plants (the jointly-pinned point
    set); composition numbers must match the actual list lengths so a
    seeding change cannot silently resize a denominator."""
    topics = manifest.get("topics")
    _require_mapping(topics, "manifest.topics", issues)
    if not isinstance(topics, dict):
        return
    for key in topics:
        if key not in FAMILY_VALUES:
            issues.append(f"manifest.topics: unknown family {key!r}")
            continue
        lst = topics[key]
        if (
            not isinstance(lst, list)
            or not lst
            or not all(isinstance(t, str) and t.strip() for t in lst)
        ):
            issues.append(f"manifest.topics.{key}: expected a non-empty list of topic keys")
        elif len(set(lst)) != len(lst):
            issues.append(f"manifest.topics.{key}: duplicate topic keys")
    composition = manifest.get("composition")
    _require_mapping(composition, "manifest.composition", issues)
    if not isinstance(composition, dict):
        return
    expected_composition = {
        "p9": 10,
        "decision": 5,
        "superseded": 5,
        "plain": 10,
        "clean": 10,
        "conflicted": 30,
        "total": 40,
    }
    for key, want in expected_composition.items():
        got = len(topics[key]) if isinstance(topics.get(key), list) else None
        if composition.get(key) != want:
            issues.append(
                f"manifest.composition.{key}: expected {want}, got "
                f"{composition.get(key)!r} (composition is jointly pinned)"
            )
        if got is not None and got != want:
            issues.append(f"manifest.topics.{key}: list length {got} != pinned composition {want}")
    # Disjoint topic keys across families (a topic in two families would make
    # the subset denominators ambiguous).
    seen: set[str] = set()
    for lst in topics.values():
        if not isinstance(lst, list):
            continue
        for t in lst:
            if t in seen:
                issues.append(f"manifest.topics: topic {t!r} appears in >1 family")
            seen.add(t)


def _validate_plant_roles(manifest: dict, issues: list[str]) -> None:
    """Per-family planted-role templates (content strings the seed plants —
    the resolution target for gold's target_role refs)."""
    plant = manifest.get("plant_roles")
    _require_mapping(plant, "manifest.plant_roles", issues)
    if not isinstance(plant, dict):
        return
    topics = manifest.get("topics") or {}
    for family, roles in plant.items():
        if family not in FAMILY_VALUES:
            issues.append(f"manifest.plant_roles: unknown family {family!r}")
            continue
        if family not in topics:
            issues.append(f"manifest.plant_roles.{family}: family not in topics")
        _require_mapping(roles, f"manifest.plant_roles.{family}", issues)
        if not isinstance(roles, dict):
            continue
        for role, template in roles.items():
            if not isinstance(template, str) or "{topic}" not in template:
                issues.append(
                    f"manifest.plant_roles.{family}.{role}: expected a content "
                    f"template containing '{{topic}}', got {template!r}"
                )
    # Required roles per family (what role resolution needs at run time).
    required = {
        "p9": {"claim", "support", "counter"},
        "plain": {"claim", "support", "counter"},
        "decision": {"decision", "support", "option_a", "option_b", "counter"},
        "superseded": {"old", "support", "counter", "successor"},
        "clean": {"claim", "support_a", "support_b"},
    }
    for family, roles in (plant or {}).items():
        if family not in required:
            continue
        if not isinstance(roles, dict):
            continue
        missing = sorted(required[family] - set(roles))
        if missing:
            issues.append(f"manifest.plant_roles.{family}: missing required roles {missing}")


def validate_manifest(manifest: dict) -> list[str]:
    """DM-6/9 validation: harness-visible seeding spec ONLY.

    ``{corpus, protocol, schema_version, seed, source?, composition, topics,
    plant_roles}``.  A ``gold`` key is a VALIDATION ERROR (answer-key
    contamination — the sealed gold lives in gold/).
    """
    issues: list[str] = []
    _require_mapping(manifest, "manifest", issues)
    if not issues:
        allowed = frozenset(
            {
                "corpus",
                "protocol",
                "schema_version",
                "seed",
                "source",
                "composition",
                "topics",
                "plant_roles",
            }
        )
        _reject_unknown_keys(manifest, allowed, "manifest", issues)
        if "gold" in manifest:
            issues.append(
                "manifest.gold: a gold key inside the manifest is a VALIDATION "
                "ERROR (answer-key contamination — sealed gold lives in gold/)"
            )
        _expect_str(manifest, "corpus", "manifest", issues)
        _expect_str(manifest, "protocol", "manifest", issues)
        _expect_int(manifest, "schema_version", "manifest", issues, minimum=1)
        _expect_int(manifest, "seed", "manifest", issues, minimum=0)
        _validate_topics(manifest, issues)
        _validate_plant_roles(manifest, issues)
    return issues


# ── Gold validation (manifest-resolved) ────────────────────────────────────

_LEGAL_KIND_FAMILY: dict[str, frozenset[str]] = {
    # Per-family LEGAL expected pointer kinds (derived from structure flags):
    # every conflicted family surfaces supports + nand; the decision subset
    # adds the tradeoff pointer; the superseded subset adds superseded.
    "p9": frozenset({"supports", "nand"}),
    "plain": frozenset({"supports", "nand"}),
    "decision": frozenset({"supports", "nand", "tradeoff"}),
    "superseded": frozenset({"supports", "nand", "superseded"}),
    "clean": frozenset({"supports"}),
}

# kind → roles that pointer kind may target, per family (a nand pointer
# must target the planted counterargument role; the superseded pointer the
# successor; the tradeoff pointer an option role; supports the evidence).
_KIND_ROLE_TARGETS: dict[str, dict[str, frozenset[str]]] = {
    "supports": {
        "p9": frozenset({"support"}),
        "plain": frozenset({"support"}),
        "decision": frozenset({"support"}),
        "superseded": frozenset({"support"}),
        "clean": frozenset({"support_a", "support_b"}),
    },
    "nand": {
        "p9": frozenset({"counter"}),
        "plain": frozenset({"counter"}),
        "decision": frozenset({"counter"}),
        "superseded": frozenset({"counter"}),
    },
    "tradeoff": {"decision": frozenset({"option_a", "option_b"})},
    "superseded": {"superseded": frozenset({"successor"})},
}


def _validate_gold_entry(entry: dict, manifest: dict, index: int, issues: list[str]) -> None:
    where = f"gold.entries[{index}]"
    _require_mapping(entry, where, issues)
    if not isinstance(entry, dict):
        return
    allowed = frozenset({"point_id", "family", "clean", "expected"})
    _reject_unknown_keys(entry, allowed, where, issues)
    pid = _expect_str(entry, "point_id", where, issues)
    family = _expect_enum(entry, "family", FAMILY_VALUES, where, issues)
    clean = _expect_bool(entry, "clean", where, issues)
    expected = entry.get("expected")
    _require_mapping(expected, f"{where}.expected", issues)
    if not isinstance(expected, dict):
        return
    _reject_unknown_keys(
        expected,
        frozenset(
            {
                "conflict_surfacing",
                "dig_deeper_targets",
                "support_chain_sufficient",
                "tradeoff_sufficient",
            }
        ),
        f"{where}.expected",
        issues,
    )
    conflict_surfacing = _expect_bool(expected, "conflict_surfacing", f"{where}.expected", issues)
    _expect_bool(expected, "support_chain_sufficient", f"{where}.expected", issues)
    _expect_bool(expected, "tradeoff_sufficient", f"{where}.expected", issues)

    # Manifest resolution: every gold point_id must be a topic the seed
    # plants (jointly-pinned topic list), and the entry's family/clean flags
    # must agree with the manifest's family classification.
    topics = manifest.get("topics") or {}
    planted = {t for lst in topics.values() if isinstance(lst, list) for t in lst}
    if pid is not None:
        if pid not in planted:
            issues.append(
                f"{where}.point_id: {pid!r} is not a topic the jointly-pinned "
                "seed plants (seed → planted point-ID drift — gold references "
                "a point the seed never creates)"
            )
        elif family is not None and family in topics and pid not in topics.get(family, []):
            issues.append(f"{where}.point_id {pid!r}: not in manifest family {family!r}")
    if family is not None and clean is not None:
        want_conflicted = FAMILY_CONFLICTED.get(family)
        if want_conflicted is True and clean is not False:
            issues.append(
                f"{where}: family {family!r} is planted CONFLICTED — clean "
                f"must be false (got {clean!r})"
            )
        if want_conflicted is False and clean is not True:
            issues.append(
                f"{where}: family {family!r} is planted CLEAN — clean must be true (got {clean!r})"
            )
        if conflict_surfacing is not None and conflict_surfacing != want_conflicted:
            issues.append(
                f"{where}.expected.conflict_surfacing: family {family!r} plants "
                f"conflict_surfacing={want_conflicted}, got {conflict_surfacing!r}"
            )
    # Expected dig_deeper_targets: {kind, target_role} — kind must be legal
    # for the family and the target_role must resolve to a role the family's
    # plant spec produced (a clean point may never expect a contradiction
    # pointer; a non-superseded topic may never expect a superseded target).
    targets = expected.get("dig_deeper_targets")
    if targets is None:
        issues.append(f"{where}.expected.dig_deeper_targets: missing (required)")
        return
    if not isinstance(targets, list):
        issues.append(f"{where}.expected.dig_deeper_targets: expected a list")
        return
    seen_kinds: set[str] = set()
    plant_roles = manifest.get("plant_roles") or {}
    for tidx, target in enumerate(targets):
        twhere = f"{where}.expected.dig_deeper_targets[{tidx}]"
        _require_mapping(target, twhere, issues)
        if not isinstance(target, dict):
            continue
        _reject_unknown_keys(target, frozenset({"kind", "target_role"}), twhere, issues)
        kind = _expect_enum(target, "kind", DIG_DEEPER_KINDS, twhere, issues)
        role = _expect_str(target, "target_role", twhere, issues)
        if family is None or kind is None:
            continue
        if kind not in _LEGAL_KIND_FAMILY.get(family, frozenset()):
            issues.append(
                f"{twhere}.kind: family {family!r} never surfaces a "
                f"{kind!r} pointer (clean points must not invent "
                "contradictions; non-decision/superseded topics carry no "
                "tradeoff/superseded dimension)"
            )
            continue
        if kind in seen_kinds:
            issues.append(f"{twhere}: duplicate expected kind {kind!r}")
        seen_kinds.add(kind)
        allowed_roles = _KIND_ROLE_TARGETS.get(kind, {}).get(family, frozenset())
        if role is not None and role not in allowed_roles:
            issues.append(
                f"{twhere}.target_role: kind {kind!r} on family {family!r} must "
                f"target one of {sorted(allowed_roles)}, got {role!r}"
            )
        if role is not None:
            family_roles = plant_roles.get(family) or {}
            if role not in family_roles:
                issues.append(
                    f"{twhere}.target_role: role {role!r} is not a role the "
                    f"manifest plants for family {family!r} "
                    f"(roles: {sorted(family_roles)})"
                )
    # Decision points must expect the tradeoff target (the EP-favored
    # alternative); superseded points the successor.
    if family == "decision" and "tradeoff" not in seen_kinds:
        issues.append(
            f"{where}: decision-family gold must expect a tradeoff pointer "
            "(E2E-1 decision-point trade-offs)"
        )
    if family == "superseded" and "superseded" not in seen_kinds:
        issues.append(
            f"{where}: superseded-family gold must expect the superseded "
            "(successor) pointer (E2E-7 dig-deeper navigation)"
        )


def validate_gold(gold: dict, manifest: dict | None = None) -> list[str]:
    """DM-7/9 validation: sealed gold against the jointly-pinned manifest.

    Gold keys: ``{corpus, protocol, schema_version, suite, seed, entries}``
    — entries carry per-planted-point ``expected`` blocks (conflict
    surfacing + dig_deeper targets resolved against the manifest's plant
    roles at contract-validation time).
    """
    issues: list[str] = []
    _require_mapping(gold, "gold", issues)
    if not issues:
        _reject_unknown_keys(
            gold,
            frozenset({"corpus", "protocol", "schema_version", "suite", "seed", "entries"}),
            "gold",
            issues,
        )
        _expect_str(gold, "corpus", "gold", issues)
        _expect_str(gold, "protocol", "gold", issues)
        _expect_int(gold, "schema_version", "gold", issues, minimum=1)
        _expect_enum(gold, "suite", SUITE_VALUES, "gold", issues)
        _expect_int(gold, "seed", "gold", issues, minimum=0)
        entries = gold.get("entries")
        if not isinstance(entries, list) or not entries:
            issues.append("gold.entries: expected a non-empty list")
            return issues
        manifest = manifest if manifest is not None else {}
        planted_total = 0
        topics = manifest.get("topics")
        if isinstance(topics, dict):
            planted_total = sum(len(v) for v in topics.values() if isinstance(v, list))
        if planted_total and len(entries) != planted_total:
            issues.append(
                f"gold.entries: {len(entries)} entries but the manifest plants "
                f"{planted_total} topics (coverage must be exact — every "
                "planted point is a graded datum)"
            )
        seen_pids: set[str] = set()
        for index, entry in enumerate(entries):
            _validate_gold_entry(entry, manifest, index, issues)
            pid = entry.get("point_id") if isinstance(entry, dict) else None
            if isinstance(pid, str):
                if pid in seen_pids:
                    issues.append(f"gold.entries[{index}]: duplicate point {pid!r}")
                seen_pids.add(pid)
    return issues


# ═══ Metric aggregation → baseline metrics ═════════════════════════════════


def aggregate_metrics(point_results: list[dict]) -> dict:
    """Fold per-point why-grades into the canonical metric snapshot.

    ``point_results`` is one dict per graded point (see
    ``runner.grade_point``)::

        {"point_id", "family", "clean", "expected_conflict",
         "conflict_surfaced": bool, "nav_correct": int, "nav_total": int,
         "support_sufficient": bool, "tradeoff_sufficient": bool|None,
         "false_positive": bool}

    Aggregation is POOLED: conflict_surfacing_rate = correctly-surfaced /
    expected-conflict points (30); navigation accuracy = correct expected
    pointer targets / total expected targets; support-chain sufficiency
    over every point; trade-off sufficiency over decision points; the
    false-positive rate over clean points.  An empty denominator collapses
    to the WORST value (minimize rates → 1.0, maximize → 0.0): a missing
    graded dimension must never read as a clean pass.
    """
    expected_conflict = [r for r in point_results if r.get("expected_conflict")]
    conflicted_ok = sum(1 for r in expected_conflict if r.get("conflict_surfaced"))
    nav_num = sum(r.get("nav_correct", 0) for r in point_results)
    nav_den = sum(r.get("nav_total", 0) for r in point_results)
    support = [r for r in point_results if r.get("support_sufficient") is not None]
    support_ok = sum(1 for r in support if r.get("support_sufficient") is True)
    decisions = [r for r in point_results if r.get("tradeoff_sufficient") is not None]
    tradeoff_ok = sum(1 for r in decisions if r.get("tradeoff_sufficient") is True)
    clean = [r for r in point_results if r.get("clean")]
    fp = sum(1 for r in clean if r.get("false_positive"))
    return {
        "conflict_surfacing_rate": (
            conflicted_ok / len(expected_conflict) if expected_conflict else 0.0
        ),
        "dig_deeper_navigation_accuracy": (nav_num / nav_den if nav_den else 0.0),
        "support_chain_sufficiency": (support_ok / len(support) if support else 0.0),
        "tradeoff_sufficiency": (tradeoff_ok / len(decisions) if decisions else 0.0),
        "false_positive_rate": (fp / len(clean) if clean else 1.0),
    }


# ═══ Baseline machinery (why-suite vocabulary; discipline mirrors W2-b) ════


def _validate_metric_values(metrics: dict, where: str, issues: list[str]) -> None:
    for key, value in metrics.items():
        if key not in METRIC_VALUES:
            issues.append(f"{where}: unknown metric {key!r} (vocabulary: {sorted(METRIC_VALUES)})")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(f"{where}.{key}: expected a number, got {value!r}")
        elif not (0.0 <= float(value) <= 1.0):
            issues.append(f"{where}.{key}: expected a fraction in [0, 1], got {value!r}")


def _standing_bars_tripped(metrics: dict, baseline: dict) -> bool:
    """The why-suite standing bars (E2E-7 / issue targets): conflict
    surfacing >= 0.95, dig-deeper navigation >= 0.95, 0 clean false
    positives.  Armed when the committed baseline's config.reflex ==
    "graded" (the graded artifact is delivered — this suite is the A11
    pilot gate from day one; a null-reflex baseline would record honestly
    pre-gate without gating, mirroring the W3-a harness semantics)."""
    committed_reflex = (baseline.get("config") or {}).get("reflex")
    if committed_reflex != "graded":
        return False
    return (
        metrics.get("conflict_surfacing_rate", 0.0) < CONFLICT_SURFACING_FLOOR
        or metrics.get("dig_deeper_navigation_accuracy", 0.0) < DIG_DEEPER_NAV_FLOOR
        or metrics.get("false_positive_rate", 1.0) > FALSE_POSITIVE_TOLERANCE
    )


def compare_run(
    run_metrics: dict,
    baseline: dict,
    *,
    resolved_config: dict,
    run_fixtures_hash: str,
    run_judge_pin: str | None = None,
) -> str:
    """The --compare verdict against the committed baseline (same contract
    as the W3-a harness, why-suite vocabulary).

    * INCONCLUSIVE — fixtures_hash / resolved_config / judge_pin mismatch
      (cross-corpus, cross-posture or cross-protocol runs never compare).
    * REGRESSION  — any metric moved in its wrong direction vs the committed
      snapshot, OR a standing bar tripped (below the >= 0.95 floors / a
      clean false positive).
    * PASS        — otherwise.
    """
    if run_fixtures_hash != baseline.get("fixtures_hash"):
        return VERDICT_INCONCLUSIVE
    if resolved_config != baseline.get("config"):
        return VERDICT_INCONCLUSIVE
    committed_pin = baseline.get("judge_pin")
    if run_judge_pin is not None and committed_pin and run_judge_pin != committed_pin:
        return VERDICT_INCONCLUSIVE
    committed_metrics = baseline.get("metrics") or {}
    if not committed_metrics:
        return VERDICT_INCONCLUSIVE
    missing = [m for m in committed_metrics if m not in run_metrics]
    if missing:
        return VERDICT_REGRESSION
    for metric, committed_value in committed_metrics.items():
        run_value = run_metrics[metric]
        if METRIC_DIRECTIONS[metric] == "minimize":
            worse = run_value > committed_value
        else:
            worse = run_value < committed_value
        if worse:
            return VERDICT_REGRESSION
    if _standing_bars_tripped(run_metrics, baseline):
        return VERDICT_REGRESSION
    return VERDICT_PASS


def bless_baseline(
    previous: dict,
    run: dict,
    *,
    justification: str,
    corpus_bless: bool = False,
    protocol_bless: bool = False,
) -> dict:
    """Produce the next committed baseline (same guards as the W3-a harness
    bless — a regression re-publish records its verdict in history per the
    fix-wave protocol)."""
    if not isinstance(justification, str) or not justification.strip():
        raise ValueError("blessing a baseline requires a non-empty justification string")
    judge_pin = run.get("judge_pin")
    if not isinstance(judge_pin, str) or not judge_pin.strip():
        raise ValueError(
            "publishing a baseline requires a non-null judge_pin (the pinned judge prompt version)"
        )
    run_metric_issues: list[str] = []
    _validate_metric_values(run["metrics"], "run.metrics", run_metric_issues)
    if run_metric_issues:
        raise ValueError(
            "cannot bless: run metrics are not valid published values — "
            + "; ".join(run_metric_issues)
        )
    previous_pin = previous.get("judge_pin")
    previous_metrics = previous.get("metrics") or {}
    first_publish = not previous_metrics
    hash_changed = bool(previous.get("fixtures_hash")) and run["fixtures_hash"] != previous.get(
        "fixtures_hash"
    )
    pin_changed = bool(previous_pin) and judge_pin != previous_pin
    verdict = None
    if hash_changed and not corpus_bless:
        raise ValueError(
            "cannot bless: run fixtures_hash differs from the committed "
            "baseline (corpus drift). For an INTENTIONAL manifest/gold "
            "regeneration use corpus_bless=True with a justification "
            "recording the corpus change."
        )
    if pin_changed and not protocol_bless:
        raise ValueError(
            "cannot bless: run judge_pin differs from the committed baseline's "
            f"pin ({previous_pin!r} vs {judge_pin!r}) — a judge-protocol change "
            "is a new protocol, not a comparable run (re-run under the pinned "
            "judge, or use protocol_bless=True to deliberately re-pin)"
        )
    if first_publish:
        if run["config"] != previous.get("config"):
            raise ValueError(
                "cannot bless first publish: run config does not match the "
                "committed baseline config snapshot (config mismatch => "
                "inconclusive)"
            )
        verdict = None
    elif (hash_changed and corpus_bless) or (pin_changed and protocol_bless):
        verdict = None  # deliberate re-pin — no comparability, no compare
    else:
        config_diff = run["config"] != previous.get("config")
        if config_diff:
            raise ValueError(
                "cannot bless: run config differs from the committed baseline "
                "config snapshot (config mismatch => inconclusive)"
            )
        verdict = compare_run(
            run["metrics"],
            previous,
            resolved_config=run["config"],
            run_fixtures_hash=run["fixtures_hash"],
            run_judge_pin=judge_pin,
        )
        if verdict == VERDICT_INCONCLUSIVE:
            raise ValueError(
                f"cannot bless: compare verdict is {VERDICT_INCONCLUSIVE} "
                f"(config, fixtures_hash, or judge_pin mismatch)"
            )
    missing_metrics = sorted(METRIC_VALUES - set(run["metrics"]))
    if missing_metrics:
        raise ValueError(
            "cannot bless: run metrics are missing graded dimensions "
            f"{missing_metrics} — a published baseline must snapshot the full "
            f"{len(METRIC_VALUES)}-metric vocabulary"
        )
    history_entry = {
        "date": run["date"],
        "values": run["metrics"],
        "failure_classes": run.get("failure_classes", []),
        "justification": justification,
    }
    if hash_changed and corpus_bless:
        history_entry["corpus_change"] = True
    if pin_changed and protocol_bless:
        history_entry["protocol_change"] = True
    if verdict is not None:
        history_entry["verdict"] = verdict
    return {
        "schema_version": previous.get("schema_version", SCHEMA_VERSION),
        "fixtures_hash": run["fixtures_hash"],
        "judge_pin": judge_pin,
        "config": run["config"],
        "justification": justification,
        "metrics": run["metrics"],
        "history": [*previous.get("history", []), history_entry],
    }


def _validate_history_entry(entry: dict, index: int, issues: list[str]) -> None:
    where = f"baseline.history[{index}]"
    _reject_unknown_keys(
        entry,
        frozenset(
            {
                "date",
                "values",
                "failure_classes",
                "justification",
                "verdict",
                "corpus_change",
                "protocol_change",
                "correction",
            }
        ),
        where,
        issues,
    )
    correction = entry.get("correction")
    if correction is not None and (not isinstance(correction, str) or not correction.strip()):
        issues.append(f"{where}.correction: expected null or a non-empty string")
    date = entry.get("date")
    if not isinstance(date, str) or not date.strip():
        issues.append(f"{where}.date: expected a non-empty string (ISO date)")
    values = entry.get("values")
    if not isinstance(values, dict):
        issues.append(f"{where}.values: expected an object (the run's metrics)")
    else:
        _validate_metric_values(values, f"{where}.values", issues)
    failure_classes = entry.get("failure_classes", [])
    if not isinstance(failure_classes, list) or not all(
        isinstance(f, str) for f in failure_classes
    ):
        issues.append(f"{where}.failure_classes: expected a list of strings")
    justification = entry.get("justification")
    if not isinstance(justification, str) or not justification.strip():
        issues.append(f"{where}.justification: expected a non-empty string")
    verdict = entry.get("verdict")
    if verdict is not None and verdict not in VERDICT_VALUES:
        issues.append(f"{where}.verdict: expected one of {sorted(VERDICT_VALUES)} or null")


def validate_baseline(baseline: dict) -> list[str]:
    """Validate a committed why-suite baseline (same shape discipline as the
    harness schema, why config/metric vocabulary)."""
    issues: list[str] = []
    _require_mapping(baseline, "baseline", issues)
    if not issues:
        _reject_unknown_keys(
            baseline,
            frozenset(
                {
                    "schema_version",
                    "fixtures_hash",
                    "judge_pin",
                    "config",
                    "justification",
                    "metrics",
                    "history",
                }
            ),
            "baseline",
            issues,
        )
        _expect_int(baseline, "schema_version", "baseline", issues, minimum=1)
        fixtures_hash = baseline.get("fixtures_hash")
        if not isinstance(fixtures_hash, str) or not fixtures_hash.startswith("sha256:"):
            issues.append("baseline.fixtures_hash: expected a 'sha256:<hex>' string")
        config = baseline.get("config")
        _require_mapping(config, "baseline.config", issues)
        if isinstance(config, dict):
            _reject_unknown_keys(
                config,
                frozenset(
                    {"suites", "mode", "reflex", "holdout_excluded", "seed", "extractor_posture"}
                ),
                "baseline.config",
                issues,
            )
            suites = config.get("suites")
            if not isinstance(suites, list) or not suites or not set(suites) <= SUITE_VALUES:
                issues.append(
                    f"baseline.config.suites: expected a non-empty subset of {sorted(SUITE_VALUES)}"
                )
            _expect_enum(config, "mode", frozenset({"BPRE", "full"}), "baseline.config", issues)
            _expect_enum(config, "reflex", frozenset({"null", "graded"}), "baseline.config", issues)
            if config.get("holdout_excluded") is not None and not isinstance(
                config["holdout_excluded"], bool
            ):
                issues.append("baseline.config.holdout_excluded: expected a boolean")
            _expect_enum(
                config, "extractor_posture", frozenset({"llm", "m2"}), "baseline.config", issues
            )
            seed = config.get("seed")
            if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
                issues.append("baseline.config.seed: expected an int or null")
        justification = baseline.get("justification")
        if justification is not None and (
            not isinstance(justification, str) or not justification.strip()
        ):
            issues.append("baseline.justification: expected null or a non-empty string")
        judge_pin = baseline.get("judge_pin")
        if judge_pin is not None and (not isinstance(judge_pin, str) or not judge_pin.strip()):
            issues.append("baseline.judge_pin: expected null or a non-empty string")
        metrics = baseline.get("metrics")
        if not isinstance(metrics, dict):
            issues.append("baseline.metrics: expected an object")
        elif metrics:
            _validate_metric_values(metrics, "baseline.metrics", issues)
            missing_metrics = sorted(METRIC_VALUES - set(metrics))
            if missing_metrics:
                issues.append(
                    "baseline.metrics: published baseline is missing graded "
                    f"dimensions {missing_metrics} — must snapshot the full "
                    f"{len(METRIC_VALUES)}-metric vocabulary"
                )
        # Cross-invariants (parity with the harness): a PUBLISHED baseline
        # (non-empty metrics) must carry a judge_pin AND a justification; a
        # pending baseline (empty metrics) must carry neither.
        published = bool(metrics)
        if published:
            if judge_pin is None:
                issues.append(
                    "baseline.judge_pin: published baseline (non-empty metrics) "
                    "requires a pinned judge"
                )
            if justification is None:
                issues.append(
                    "baseline.justification: published baseline (non-empty "
                    "metrics) requires the blessing justification"
                )
        else:
            if judge_pin is not None:
                issues.append(
                    "baseline.judge_pin: pending baseline (empty metrics) must "
                    "have a null judge_pin"
                )
            if justification is not None:
                issues.append(
                    "baseline.justification: pending baseline (empty metrics) "
                    "must have a null justification"
                )
        history = baseline.get("history", [])
        if not isinstance(history, list):
            issues.append("baseline.history: expected a list")
        else:
            for index, entry in enumerate(history):
                _require_mapping(entry, f"baseline.history[{index}]", issues)
                if isinstance(entry, dict):
                    _validate_history_entry(entry, index, issues)
    return issues
