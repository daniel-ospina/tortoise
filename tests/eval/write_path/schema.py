"""W2 write-path corpus schemas + validation (epic #2080, issue #2097, W2-a).

Canonical JSON schemas for the planted-gold write-path corpus (plan DM-3/4/5,
``docs/planning/2026-09-01-2080-gbrain-plan.md`` §4.3.1–4.3.3) plus the
contract semantics the W2-b benchmark runner (#2098) grades against:

* **Fixture** (DM-3) — ``{session_id, harness, conversation}`` ONLY.  The
  answer key NEVER lives in the corpus: a ``gold`` key anywhere inside a
  fixture is a validation error (gbrain-evals Cat-35 rule).
* **Gold** (DM-4) — sealed answer key in ``gold/<session_id>.gold.json``:
  ``planted_units`` (verbatim anchors, notability, depth_bucket,
  planted_turn), true-but-routine ``distractors``, ``attribution_hazards``,
  and ``salient_units`` carrying point-level ``survival`` semantics
  (``via_anchor`` + REPHRASE-linked acceptance — A1; REPHRASE operator
  semantics per ``docs/epistemic-layer-eval-spec.md`` §P5).
* **Baseline** (DM-5) — committed ``baselines/main.json`` that the
  ``--compare`` gate can fail against.  A regression requires a
  ``justification`` string to bless; a resolved-config mismatch ⇒
  ``inconclusive``, never a rubber-stamp.

Validators are hand-rolled (this repo pins no JSON-schema dependency); every
validator returns a ``list[str]`` of issues (empty = valid) so callers can
aggregate or raise.  All cross-file invariants (anchor ∈ planted turn,
fixtures_hash coverage, blessing discipline) live here so the generator, the
contract tests, and the W2-b runner share ONE source of truth.

Hermetic: pure stdlib, no DB/network/LLM (per the #2093 S4 surface
contract — unit + contract layer).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

# ── Schema version ──────────────────────────────────────────────────────────

SCHEMA_VERSION = 1

# Research-recommended distractor-leakage tolerance: ≤1/run (gbrain measured
# 1/86).  Supersedes the epic's literal "zero" wording.  The sealed gold
# carries it (plan DM-4) and the schema enforces the locked value via this
# constant so a sealed-gold edit cannot silently relax the tolerance.
DISTRACTOR_LEAKAGE_TOLERANCE = 1

# ── Enumerations ────────────────────────────────────────────────────────────
# Harness values mirror the session-capture boundary (hosted_api.py
# ``_SESSION_HARNESS_VALUES``: claude/claude-desktop/claude-web/codex/cursor/pi).
# Kept as a literal here (not an import of hosted_api) so the schema stays
# hermetic and import-light; drift between this set and the capture boundary
# is a review item at W2-b wiring time.
HARNESS_VALUES = frozenset({"claude", "claude-desktop", "claude-web", "codex", "cursor", "pi"})
ROLE_VALUES = frozenset({"user", "assistant"})
KIND_VALUES = frozenset({"fact", "idea", "decision", "vibe", "entity"})
NOTABILITY_VALUES = frozenset({"high", "medium", "low"})
# Research-grounded Cat-35 enumeration (research-brief W2 row + raw-notes
# 10:10Z gold line-ref): the third of the session the unit was planted in.
# ``planted_turn`` carries the exact position; the plan §4.3.2 "explicit"
# example value is illustrative, not an enum member.
DEPTH_BUCKET_VALUES = frozenset({"early", "middle", "late"})

# Comparison lanes + mode snapshot for the committed baseline config (the
# resolved-config snapshot the --compare gate checks — plan §4.3.3).
LANE_VALUES = frozenset({"verbatim", "facts", "dream"})
MODE_VALUES = frozenset({"BPRE", "full"})

# Canonical graded-metric vocabulary (plan §4.3.3) with compare direction.
# maximize: run < committed baseline ⇒ regression.  minimize: run > committed
# baseline ⇒ regression (leakage is lower-better).
METRIC_DIRECTIONS: dict[str, str] = {
    "salient_unit_survival_macro": "maximize",   # macro survival ≥ committed target
    "salient_unit_survival_strict": "maximize",  # strict survival ≥ committed target
    "distractor_leakage_per_run": "minimize",    # ≤ tolerance (gold carries the tolerance)
    "sessions_emitting": "maximize",             # 100% sessions-emit invariant
    "quote_fidelity": "maximize",                # every quoted string grounds
    "provenance_accuracy": "maximize",           # every surviving point carries provenance
}
METRIC_VALUES = frozenset(METRIC_DIRECTIONS)

# Compare-verdict vocabulary (plan §4.3.3): pass | regression | inconclusive.
VERDICT_PASS = "pass"
VERDICT_REGRESSION = "regression"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_VALUES = frozenset({VERDICT_PASS, VERDICT_REGRESSION, VERDICT_INCONCLUSIVE})

# ── Text normalization (shared with the mechanical anchor check) ────────────
_WS = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normalized-ws, case-insensitive text for anchor substring checks.

    Mirrors the gbrain Cat-35 mechanical check semantics ("is this phrase
    present is a case-insensitive question" — raw-notes 10:10Z): collapse all
    whitespace runs to single spaces and lowercase.  The W2-b runner MUST use
    this same function for ``survival.via_anchor`` substring grading so the
    gold's planted anchors and the grader's predicate can never drift.
    """
    return _WS.sub(" ", text).strip().lower()


def anchor_present(anchor: str, text: str) -> bool:
    """True when ``anchor`` is a normalized substring of ``text``."""
    return normalize_text(anchor) in normalize_text(text)


def sha256_bytes(data: bytes) -> str:
    """``sha256:<hex>`` digest string used across fixtures_hash + manifest."""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


# ── Validator helpers ───────────────────────────────────────────────────────


def _require_mapping(doc: object, where: str, issues: list[str]) -> None:
    if not isinstance(doc, dict):
        issues.append(f"{where}: expected a JSON object, got {type(doc).__name__}")


def _reject_unknown_keys(doc: dict, allowed: frozenset[str], where: str, issues: list[str]) -> None:
    for key in doc:
        if key not in allowed:
            hint = " (a `gold` key inside a fixture is a VALIDATION ERROR — answer-key "
            hint += "content lives only in the sealed gold file)" if key == "gold" else ""
            issues.append(f"{where}: unexpected key {key!r}{hint}")


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


def _expect_enum(doc: dict, key: str, allowed: frozenset[str], where: str, issues: list[str]) -> str | None:
    value = doc.get(key)
    if not isinstance(value, str) or value not in allowed:
        issues.append(
            f"{where}.{key}: expected one of {sorted(allowed)}, got {value!r}"
        )
        return None
    return value


def _expect_int(doc: dict, key: str, where: str, issues: list[str], *, minimum: int) -> int | None:
    value = doc.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        issues.append(f"{where}.{key}: expected an int ≥ {minimum}, got {value!r}")
        return None
    return value


# ── Fixture (DM-3) ──────────────────────────────────────────────────────────


def validate_fixture(fixture: dict) -> list[str]:
    """Validate one fixture document (``{session_id, harness, conversation}``).

    Returns a list of issues (empty = valid).  Any extra top-level key — in
    particular ``gold`` — is an error: the answer key never lives in the
    corpus.
    """
    issues: list[str] = []
    _require_mapping(fixture, "fixture", issues)
    if not issues:
        _reject_unknown_keys(
            fixture, frozenset({"session_id", "harness", "conversation"}), "fixture", issues
        )
        _expect_str(fixture, "session_id", "fixture", issues)
        _expect_enum(fixture, "harness", HARNESS_VALUES, "fixture", issues)
        conversation = fixture.get("conversation")
        if not isinstance(conversation, list) or not conversation:
            issues.append("fixture.conversation: expected a non-empty list of turns")
        elif isinstance(conversation, list):
            for i, turn in enumerate(conversation):
                where = f"fixture.conversation[{i}]"
                _require_mapping(turn, where, issues)
                if isinstance(turn, dict):
                    _reject_unknown_keys(turn, frozenset({"role", "content"}), where, issues)
                    _expect_enum(turn, "role", ROLE_VALUES, where, issues)
                    content = turn.get("content")
                    if not isinstance(content, str) or not content.strip():
                        issues.append(f"{where}.content: expected a non-empty string")
    return issues


# ── Gold (DM-4 — sealed answer key) ─────────────────────────────────────────


def _validate_planted_units(gold: dict, fixture: dict | None, issues: list[str]) -> None:
    units = gold.get("planted_units")
    if not isinstance(units, list) or not units:
        issues.append("gold.planted_units: expected a non-empty list")
        return
    n_turns = len(fixture["conversation"]) if fixture else None
    seen_ids: set[str] = set()
    for i, unit in enumerate(units):
        where = f"gold.planted_units[{i}]"
        _require_mapping(unit, where, issues)
        if not isinstance(unit, dict):
            continue
        _reject_unknown_keys(
            unit,
            frozenset({"id", "kind", "verbatim_anchor", "notability", "depth_bucket", "planted_turn"}),
            where,
            issues,
        )
        unit_id = _expect_str(unit, "id", where, issues)
        if unit_id is not None:
            if unit_id in seen_ids:
                issues.append(f"{where}.id: duplicate planted-unit id {unit_id!r}")
            seen_ids.add(unit_id)
        _expect_enum(unit, "kind", KIND_VALUES, where, issues)
        _expect_enum(unit, "notability", NOTABILITY_VALUES, where, issues)
        _expect_enum(unit, "depth_bucket", DEPTH_BUCKET_VALUES, where, issues)
        anchor = _expect_str(unit, "verbatim_anchor", where, issues)
        planted_turn = _expect_int(unit, "planted_turn", where, issues, minimum=1)
        if fixture is not None and planted_turn is not None and n_turns is not None:
            if planted_turn > n_turns:
                issues.append(
                    f"{where}.planted_turn: {planted_turn} > {n_turns} conversation turns"
                )
                continue
            content = fixture["conversation"][planted_turn - 1].get("content", "")
            if anchor is not None and not anchor_present(anchor, content):
                issues.append(
                    f"{where}.verbatim_anchor: {anchor!r} is not a normalized substring of "
                    f"conversation[{planted_turn - 1}] content (fixture/gold drift)"
                )
        # depth_bucket ↔ planted_turn coherence: bucket is the third of the
        # session the unit was planted in (1-based turn vs session length).
        if (
            fixture is not None
            and n_turns is not None
            and planted_turn is not None
            and isinstance(unit.get("depth_bucket"), str)
        ):
            expected = depth_bucket_for(planted_turn, n_turns)
            if unit["depth_bucket"] != expected:
                issues.append(
                    f"{where}.depth_bucket: {unit['depth_bucket']!r} inconsistent with "
                    f"planted_turn {planted_turn} of {n_turns} (expected {expected!r})"
                )


def depth_bucket_for(planted_turn: int, n_turns: int) -> str:
    """Bucket a 1-based turn into the third of the session it falls in."""
    third = max(1, n_turns // 3)
    if planted_turn <= third:
        return "early"
    if planted_turn <= 2 * third:
        return "middle"
    return "late"


def _validate_distractors(gold: dict, fixture: dict | None, issues: list[str]) -> None:
    distractors = gold.get("distractors")
    if not isinstance(distractors, list) or not distractors:
        issues.append("gold.distractors: expected a non-empty list (true-but-routine leakage probes)")
        return
    n_turns = len(fixture["conversation"]) if fixture else None
    seen_ids: set[str] = set()
    for i, distractor in enumerate(distractors):
        where = f"gold.distractors[{i}]"
        _require_mapping(distractor, where, issues)
        if not isinstance(distractor, dict):
            continue
        _reject_unknown_keys(
            distractor, frozenset({"id", "statement", "anchor", "planted_turn"}), where, issues
        )
        distractor_id = _expect_str(distractor, "id", where, issues)
        if distractor_id is not None:
            if distractor_id in seen_ids:
                issues.append(f"{where}.id: duplicate distractor id {distractor_id!r}")
            seen_ids.add(distractor_id)
        _expect_str(distractor, "statement", where, issues)
        anchor = _expect_str(distractor, "anchor", where, issues)
        planted_turn = _expect_int(distractor, "planted_turn", where, issues, minimum=1)
        if fixture is not None and planted_turn is not None and n_turns is not None:
            if planted_turn > n_turns:
                issues.append(
                    f"{where}.planted_turn: {planted_turn} > {n_turns} conversation turns"
                )
                continue
            content = fixture["conversation"][planted_turn - 1].get("content", "")
            if anchor is not None and not anchor_present(anchor, content):
                issues.append(
                    f"{where}.anchor: {anchor!r} is not a normalized substring of "
                    f"conversation[{planted_turn - 1}] content (fixture/gold drift)"
                )


def _validate_hazards(gold: dict, fixture: dict | None, issues: list[str]) -> None:
    hazards = gold.get("attribution_hazards")
    if not isinstance(hazards, list) or not hazards:
        issues.append("gold.attribution_hazards: expected a non-empty list")
        return
    n_turns = len(fixture["conversation"]) if fixture else None
    seen_ids: set[str] = set()
    for i, hazard in enumerate(hazards):
        where = f"gold.attribution_hazards[{i}]"
        _require_mapping(hazard, where, issues)
        if not isinstance(hazard, dict):
            continue
        _reject_unknown_keys(
            hazard, frozenset({"id", "quote", "source", "planted_turn"}), where, issues
        )
        hazard_id = _expect_str(hazard, "id", where, issues)
        if hazard_id is not None:
            if hazard_id in seen_ids:
                issues.append(f"{where}.id: duplicate hazard id {hazard_id!r}")
            seen_ids.add(hazard_id)
        quote = _expect_str(hazard, "quote", where, issues)
        _expect_str(hazard, "source", where, issues)
        planted_turn = _expect_int(hazard, "planted_turn", where, issues, minimum=1)
        if fixture is not None and planted_turn is not None and n_turns is not None:
            if planted_turn > n_turns:
                issues.append(
                    f"{where}.planted_turn: {planted_turn} > {n_turns} conversation turns"
                )
                continue
            content = fixture["conversation"][planted_turn - 1].get("content", "")
            # A hazard's quote must ground in the transcript (quote-fidelity
            # discipline: an ungrounded quote is a hallucination).
            if quote is not None and not anchor_present(quote, content):
                issues.append(
                    f"{where}.quote: {quote!r} is not a normalized substring of "
                    f"conversation[{planted_turn - 1}] content (ungrounded hazard quote)"
                )


def _validate_salient_units(gold: dict, issues: list[str]) -> None:
    units = gold.get("planted_units")
    salient = gold.get("salient_units")
    if not isinstance(salient, list) or not salient:
        issues.append("gold.salient_units: expected a non-empty list (the graded set)")
        return
    planted_ids = {u.get("id") for u in units} if isinstance(units, list) else set()
    planted_anchor_by_id = {
        u.get("id"): u.get("verbatim_anchor")
        for u in units
        if isinstance(u, dict) and isinstance(u.get("verbatim_anchor"), str)
    }
    salient_ids: set[str] = set()
    for i, entry in enumerate(salient):
        where = f"gold.salient_units[{i}]"
        _require_mapping(entry, where, issues)
        if not isinstance(entry, dict):
            continue
        _reject_unknown_keys(entry, frozenset({"id", "survival"}), where, issues)
        unit_id = _expect_str(entry, "id", where, issues)
        if unit_id is not None:
            if unit_id not in planted_ids:
                issues.append(f"{where}.id: {unit_id!r} has no matching planted_units entry")
            if unit_id in salient_ids:
                issues.append(f"{where}.id: duplicate salient-unit id {unit_id!r}")
            salient_ids.add(unit_id)
        survival = entry.get("survival")
        where_s = f"{where}.survival"
        _require_mapping(survival, where_s, issues)
        if isinstance(survival, dict):
            _reject_unknown_keys(
                survival,
                frozenset({"via_anchor", "accepts_rephrase_linked", "provenance_required", "ep_update_required"}),
                where_s,
                issues,
            )
            via_anchor = _expect_str(survival, "via_anchor", where_s, issues)
            _expect_bool(survival, "accepts_rephrase_linked", where_s, issues)
            _expect_bool(survival, "provenance_required", where_s, issues)
            _expect_bool(survival, "ep_update_required", where_s, issues)
            # The survival predicate MUST be the planted verbatim anchor for
            # the same id — a desynced via_anchor silently changes what the
            # W2 runner grades against.
            if (
                unit_id is not None
                and via_anchor is not None
                and unit_id in planted_anchor_by_id
                and via_anchor != planted_anchor_by_id[unit_id]
            ):
                issues.append(
                    f"{where_s}.via_anchor: {via_anchor!r} != planted_units "
                    f"verbatim_anchor {planted_anchor_by_id[unit_id]!r} for {unit_id!r}"
                )
    # 1:1 planted ↔ salient: every planted unit carries survival semantics.
    if isinstance(units, list) and isinstance(salient, list):
        missing = planted_ids - salient_ids
        if missing:
            issues.append(
                f"gold.salient_units: planted units missing survival semantics: {sorted(missing)}"
            )
        extra = salient_ids - planted_ids
        if extra:
            issues.append(
                f"gold.salient_units: ids with no planted_units entry: {sorted(extra)}"
            )


def validate_gold(gold: dict, fixture: dict | None = None) -> list[str]:
    """Validate one gold document, cross-checked against its fixture when given.

    ``fixture`` supplies the conversation so anchors/distractor anchors/hazard
    quotes can be verified as planted content (fixture/gold drift detection)
    and depth buckets can be coherence-checked against ``planted_turn``.
    """
    issues: list[str] = []
    _require_mapping(gold, "gold", issues)
    if not issues:
        _reject_unknown_keys(
            gold,
            frozenset(
                {
                    "schema_version",
                    "session_id",
                    "scenario",
                    "planted_units",
                    "distractors",
                    "attribution_hazards",
                    "salient_units",
                    "distractor_leakage_tolerance",
                }
            ),
            "gold",
            issues,
        )
        if _expect_int(gold, "schema_version", "gold", issues, minimum=1) != SCHEMA_VERSION:
            issues.append(f"gold.schema_version: expected {SCHEMA_VERSION}")
        if fixture is not None and gold.get("session_id") != fixture.get("session_id"):
            issues.append(
                f"gold.session_id: {gold.get('session_id')!r} != fixture "
                f"session_id {fixture.get('session_id')!r}"
            )
        else:
            _expect_str(gold, "session_id", "gold", issues)
        _expect_str(gold, "scenario", "gold", issues)
        _validate_planted_units(gold, fixture, issues)
        _validate_distractors(gold, fixture, issues)
        _validate_hazards(gold, fixture, issues)
        _validate_salient_units(gold, issues)
        tolerance = _expect_int(
            gold, "distractor_leakage_tolerance", "gold", issues, minimum=1
        )
        if tolerance is not None and tolerance != DISTRACTOR_LEAKAGE_TOLERANCE:
            issues.append(
                f"gold.distractor_leakage_tolerance: expected "
                f"{DISTRACTOR_LEAKAGE_TOLERANCE} (research-recommended ≤1/run), got {tolerance}"
            )
    return issues


# ── Baseline (DM-5 — committed, can-fail CI gate) ───────────────────────────


def validate_baseline(baseline: dict) -> list[str]:
    """Validate a committed baseline document + its invariants.

    Invariants beyond shape:
    * first-run-pending state: ``metrics == {}`` and ``history == []`` and
      ``judge_pin is None`` — the benchmark-first state before W2-b publishes
      the first (expected-bad) number.
    * published state: non-empty ``metrics`` requires a non-null ``judge_pin``
      (numbers are only publishable against a pinned judge prompt).
    * blessing a regression REQUIRES a non-null ``justification`` string
      (gbrain decision 4 — "without that discipline the gate degrades to
      rubber-stamp within months").
    """
    issues: list[str] = []
    _require_mapping(baseline, "baseline", issues)
    if not issues:
        _reject_unknown_keys(
            baseline,
            frozenset(
                {"schema_version", "fixtures_hash", "judge_pin", "config", "justification", "metrics", "history"}
            ),
            "baseline",
            issues,
        )
        if _expect_int(baseline, "schema_version", "baseline", issues, minimum=1) != SCHEMA_VERSION:
            issues.append(f"baseline.schema_version: expected {SCHEMA_VERSION}")
        fixtures_hash = baseline.get("fixtures_hash")
        if not isinstance(fixtures_hash, str) or not fixtures_hash.startswith("sha256:"):
            issues.append(f"baseline.fixtures_hash: expected a 'sha256:<hex>' string, got {fixtures_hash!r}")

        config = baseline.get("config")
        where = "baseline.config"
        _require_mapping(config, where, issues)
        if isinstance(config, dict):
            _reject_unknown_keys(config, frozenset({"lanes", "mode", "harness", "seed"}), where, issues)
            lanes = config.get("lanes")
            if not isinstance(lanes, list) or not lanes or not set(lanes) <= LANE_VALUES:
                issues.append(f"{where}.lanes: expected a non-empty subset of {sorted(LANE_VALUES)}")
            _expect_enum(config, "mode", MODE_VALUES, where, issues)
            _expect_enum(config, "harness", HARNESS_VALUES | {"all"}, where, issues)
            if config.get("seed") is not None:
                seed = config["seed"]
                if not isinstance(seed, int) or isinstance(seed, bool):
                    issues.append(f"{where}.seed: expected an int or null, got {seed!r}")

        justification = baseline.get("justification")
        if justification is not None and (not isinstance(justification, str) or not justification.strip()):
            issues.append("baseline.justification: expected null or a non-empty string")
        judge_pin = baseline.get("judge_pin")
        if judge_pin is not None and (not isinstance(judge_pin, str) or not judge_pin.strip()):
            issues.append("baseline.judge_pin: expected null or a non-empty string")

        metrics = baseline.get("metrics")
        if not isinstance(metrics, dict):
            issues.append("baseline.metrics: expected an object")
        elif metrics:
            for key in metrics:
                if key not in METRIC_VALUES:
                    issues.append(f"baseline.metrics: unknown metric {key!r} (vocabulary: {sorted(METRIC_VALUES)})")
            if judge_pin is None:
                issues.append(
                    "baseline.judge_pin: non-empty metrics require a pinned judge "
                    "(published numbers must name the judge prompt version)"
                )

        history = baseline.get("history")
        if not isinstance(history, list):
            issues.append("baseline.history: expected a list (fix-wave trail)")
        elif history and isinstance(history[-1], dict):
            # The newest (last) entry is the latest bless — appended by
            # ``bless_baseline``; blessing a regression requires justification.
            entry = history[-1]
            values = entry.get("values")
            if entry.get("justification") is None and isinstance(values, dict) and values:
                issues.append(
                    "baseline.history[-1].justification: blessing a regression requires a "
                    "non-null justification string"
                )

        pending = (metrics == {}) and (history == [])
        if pending and justification is not None:
            issues.append(
                "baseline.justification: a first-run-pending baseline (empty metrics) "
                "cannot carry a justification — nothing has been blessed yet"
            )
        if not pending and justification is None:
            issues.append(
                "baseline.justification: blessing a baseline (non-empty metrics) requires "
                "a non-null justification string"
            )
    return issues


def compare_run(run_metrics: dict, baseline: dict, *, resolved_config: dict, run_fixtures_hash: str) -> str:
    """The --compare verdict for a run against the committed baseline.

    Verdict vocabulary (plan §4.3.3): ``pass`` | ``regression`` | ``inconclusive``.

    * corpus-hash mismatch (run fixtures_hash ≠ committed fixtures_hash) ⇒
      ``inconclusive`` — a gold-only edit invalidates baselines; a mismatch
      must never rubber-stamp a pass (E2E-2 negative).
    * resolved-config mismatch ⇒ ``inconclusive`` — never a rubber-stamp.
    * first-run-pending baseline (empty metrics = no committed targets) ⇒
      ``inconclusive`` — targets are set FROM first-run data (benchmark-first
      decision), so there is nothing to regress against yet.
    * otherwise: per-metric directional compare (METRIC_DIRECTIONS) — any run
      metric worse than the committed value ⇒ ``regression``; missing graded
      metrics ⇒ ``regression`` (a skipped lane never counts as a pass).
    """
    if run_fixtures_hash != baseline.get("fixtures_hash"):
        return VERDICT_INCONCLUSIVE
    if resolved_config != baseline.get("config"):
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
    return VERDICT_PASS


def bless_baseline(previous: dict, run: dict, *, justification: str) -> dict:
    """Produce the next committed baseline from a run result.

    Blessing ALWAYS requires a non-empty ``justification`` string — every
    committed-baseline refresh records its rationale (plan §2.1 bless /
    bless-with-justification / corpus-bless discipline), and blessing a
    regression without one raises ValueError (the CI-gate discipline; the
    schema-level ``validate_baseline`` enforces the same rule on the result).

    Two cases:
    * **first publish** — ``previous`` is the first-run-pending baseline
      (empty ``metrics`` = no committed targets, benchmark-first decision):
      nothing can be compared against, so the compare is skipped; the run's
      metrics become the first committed targets (expected-bad number per the
      fix-wave protocol).
    * **subsequent bless** — ``previous`` has committed targets: a compare
      verdict of ``inconclusive`` (config or fixtures_hash mismatch) raises;
      a ``regression`` requires the justification to bless; a ``pass``
      records the improvement.  Every history entry records its ``verdict``
      (pass | regression) so the fix-wave trail is unambiguous.

    A published baseline also requires a non-null ``run["judge_pin"]``
    (numbers are only publishable against a pinned judge prompt).
    """
    if not isinstance(justification, str) or not justification.strip():
        raise ValueError("blessing a baseline requires a non-empty justification string")
    judge_pin = run.get("judge_pin")
    if not isinstance(judge_pin, str) or not judge_pin.strip():
        raise ValueError(
            "publishing a baseline requires a non-null judge_pin "
            "(the pinned judge prompt version)"
        )
    previous_metrics = previous.get("metrics") or {}
    first_publish = not previous_metrics
    if first_publish:
        verdict = None  # no committed targets to compare against (first number)
    else:
        verdict = compare_run(
            run["metrics"],
            previous,
            resolved_config=run["config"],
            run_fixtures_hash=run["fixtures_hash"],
        )
        if verdict == VERDICT_INCONCLUSIVE:
            raise ValueError(
                f"cannot bless: compare verdict is {VERDICT_INCONCLUSIVE} "
                f"(config or fixtures_hash mismatch) — re-run on the frozen corpus "
                f"with the resolved config before blessing"
            )
    history_entry = {
        "date": run["date"],
        "values": run["metrics"],
        "failure_classes": run.get("failure_classes", []),
        "justification": justification,
    }
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


def validate_manifest(manifest: dict) -> list[str]:
    """Light shape validation for a corpus ``_manifest.json`` document.

    Covers the structural fields (``schema_version``, ``corpus``, ``seed``,
    ``generator``, ``fixtures_hash``, per-file ``files`` digest map).  The
    coverage/byte-accuracy check against on-disk files is
    ``corpus.verify_manifest``'s job — this validator guards the pre-flight
    gate from malformed manifests (e.g. ``files`` not a dict).
    """
    issues: list[str] = []
    _require_mapping(manifest, "manifest", issues)
    if issues:
        return issues
    _reject_unknown_keys(
        manifest,
        frozenset({"schema_version", "corpus", "seed", "generator", "fixtures_hash", "files"}),
        "manifest",
        issues,
    )
    if _expect_int(manifest, "schema_version", "manifest", issues, minimum=1) != SCHEMA_VERSION:
        issues.append(f"manifest.schema_version: expected {SCHEMA_VERSION}")
    _expect_enum(manifest, "corpus", frozenset({"write_path"}), "manifest", issues)
    if manifest.get("seed") is not None:
        seed = manifest["seed"]
        if not isinstance(seed, int) or isinstance(seed, bool):
            issues.append(f"manifest.seed: expected an int, got {seed!r}")
    _expect_str(manifest, "generator", "manifest", issues)
    fixtures_hash = manifest.get("fixtures_hash")
    if not isinstance(fixtures_hash, str) or not fixtures_hash.startswith("sha256:"):
        issues.append(f"manifest.fixtures_hash: expected a 'sha256:<hex>' string, got {fixtures_hash!r}")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        issues.append("manifest.files: expected a non-empty object mapping rel paths to digests")
    elif all(isinstance(v, str) for v in files.values()) is False:
        issues.append("manifest.files: every value must be a 'sha256:<hex>' digest string")
    return issues


def read_json(path: Path) -> dict:
    """Read + parse a corpus JSON file (fixture/gold/baseline/manifest)."""
    return json.loads(path.read_text(encoding="utf-8"))
