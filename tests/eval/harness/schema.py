"""W3 harness fixture/gold/baseline schemas (DM-6/7/8, plan §4.3.4).

Cat-34-shaped scripted-conversation eval artifacts for the volunteering-
memory harness (epic #2080, issue #2099 W3-a):

* **fixture** — harness-visible fields ONLY: ``{suite, seed, holdout,
  turns}`` (per-turn ``role``/``content``; a writer-fixture carries the
  conversation whose write-back the continuity suite consumes).  A ``gold``
  key inside a fixture is a VALIDATION ERROR (answer-key contamination —
  Cat-35 rule, sealed separately).
* **gold** — SEALED: per-turn ``should_retrieve`` labels + expected pointer
  ids (know-to-ask / push suites), suite-specific expectations.  Gold lives
  in its own dir; ``fixtures_hash`` covers fixture AND gold files (a
  gold-only edit changes the hash ⇒ invalidates committed baselines).
* **baseline** — the W2-b committed-baseline discipline is REUSED wholesale
  (``write_path.schema``'s ``bless_baseline`` / ``compare_run`` /
  ``validate_baseline``): justification-to-bless, config mismatch ⇒
  inconclusive, regression fails CI, skipped never counts as pass.  The
  harness metric vocabulary differs (per-suite scores, not the W2 6-metric
  snapshot) so this module validates the harness-specific gold/fixture
  shapes and delegates the baseline machinery to the shared schema.

Hermetic: pure validation + hashing — no DB, no network, no LLM.
"""
from __future__ import annotations

from pathlib import Path

from eval.write_path import schema as ws

# Harness metric vocabulary (the W3 graded surface — per-suite aggregates).
# Directions follow the W2 convention: maximize = higher-better;
# minimize = lower-better.
METRIC_DIRECTIONS: dict[str, str] = {
    # know-to-ask: inject exactly when gold says should_retrieve.  Failure
    # rate = missed injections / should_retrieve turns; 0.00 target.
    "know_to_ask_failure_rate": "minimize",
    # false-fire: fires on courtesy/re-mention/below-notability turns;
    # ≤ 0.03 target (of non-retrieve turns that must stay silent).
    "false_fire_rate": "minimize",
    # push precision under the pointer budget (3 default): of the pointers
    # injected, what fraction were gold-acceptable; ≥ 1.000 target.
    "push_precision": "maximize",
    # push recall: of gold-acceptable pointers, what fraction injected.
    "push_recall": "maximize",
    # write-back fidelity: of gold planted points, what fraction written
    # back with provenance intact.
    "write_back_fidelity": "maximize",
    # continuity: of reader-cell planted decisions, what fraction surfaced
    # in the reader session's recall.
    "continuity_recall": "maximize",
    # source isolation: cross-team content leaks across ALL suites; 0 gate.
    "source_isolation_violations": "minimize",
}
METRIC_VALUES = frozenset(METRIC_DIRECTIONS)

# Compare-verdict vocabulary (shared with W2-b).
VERDICT_PASS = ws.VERDICT_PASS
VERDICT_REGRESSION = ws.VERDICT_REGRESSION
VERDICT_INCONCLUSIVE = ws.VERDICT_INCONCLUSIVE
VERDICT_VALUES = ws.VERDICT_VALUES

# Suite vocabulary (the harness grades per-suite aggregates that fold into
# the baseline metrics above).
SUITE_VALUES = frozenset({
    "know_to_ask", "push", "write_back", "continuity", "isolation",
})

# Harness-visible field enums.
ROLE_VALUES = frozenset({"user", "assistant"})
HOLDOUT_RATIO = 0.15  # ~15% holdout (plan §4.3.4), membership PINNED per fixture

SCHEMA_VERSION = 1

# Re-export shared hash primitives only (baseline machinery is vocabulary-
# bound — harness metrics differ from W2's, so compare/validate are defined
# here against the harness METRIC_DIRECTIONS; the SHAPE discipline mirrors
# write_path.schema exactly).
sha256_bytes = ws.sha256_bytes
sha256_file = ws.sha256_file

# Gold-locked quality thresholds (research-shaped; issue #2099 Targets —
# the harness ENFORCES these on the gate corpus; a null-reflex baseline is
# honest and may fail per the fix-wave protocol until the W4 reflex lands).
KTA_FAILURE_TOLERANCE = 0.0        # inject exactly when gold says retrieve
FALSE_FIRE_TOLERANCE = 0.03        # courtesy/re-mention/below-notability never fire
PUSH_PRECISION_FLOOR = 1.0         # every injected pointer is gold-acceptable
SOURCE_ISOLATION_TOLERANCE = 0     # E2E-4: violations across all suites = 0


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


def _expect_enum(doc: dict, key: str, allowed: frozenset[str], where: str, issues: list[str]) -> str | None:
    value = doc.get(key)
    if value is None:
        issues.append(f"{where}.{key}: expected one of {sorted(allowed)} (missing)")
        return None
    if value not in allowed:
        issues.append(f"{where}.{key}: expected one of {sorted(allowed)}, got {value!r}")
        return None
    return value


def _expect_int(doc: dict, key: str, where: str, issues: list[str], *, minimum: int) -> int | None:
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        issues.append(f"{where}.{key}: expected an int ≥ {minimum}, got {value!r}")
        return None
    if value < minimum:
        issues.append(f"{where}.{key}: expected an int ≥ {minimum}, got {value!r}")
        return None
    return value


def validate_fixture(fixture: dict) -> list[str]:
    """DM-6 validation: harness-visible fields only.

    ``{suite, seed, holdout, turns}`` — turns are ``{role, content}`` pairs.
    A ``gold`` key is a VALIDATION ERROR (answer-key contamination).  A
    writer-fixture (continuity suite) may carry ``writer: true`` so the
    reader cell knows which session produced the planted decision.
    """
    issues: list[str] = []
    _require_mapping(fixture, "fixture", issues)
    if not issues:
        allowed = frozenset({"suite", "seed", "holdout", "turns", "writer", "team"})
        _reject_unknown_keys(fixture, allowed, "fixture", issues)
        suite = _expect_enum(fixture, "suite", SUITE_VALUES, "fixture", issues)
        _expect_int(fixture, "seed", "fixture", issues, minimum=0)
        _expect_bool(fixture, "holdout", "fixture", issues)
        if fixture.get("writer") is not None and not isinstance(fixture["writer"], bool):
            issues.append("fixture.writer: expected a boolean (writer-fixture marker)")
        team = fixture.get("team")
        if team is not None and (not isinstance(team, str) or not team.strip()):
            issues.append("fixture.team: expected a non-empty string (multi-team isolation)")

        turns = fixture.get("turns")
        if not isinstance(turns, list) or not turns:
            issues.append("fixture.turns: expected a non-empty list")
        else:
            for index, turn in enumerate(turns):
                where = f"fixture.turns[{index}]"
                _require_mapping(turn, where, issues)
                if isinstance(turn, dict):
                    _reject_unknown_keys(turn, frozenset({"role", "content"}), where, issues)
                    _expect_enum(turn, "role", ROLE_VALUES, where, issues)
                    content = _expect_str(turn, "content", where, issues)
                    if content is not None and len(content) < 1:
                        issues.append(f"{where}.content: expected non-empty")
        # A know_to_ask/push fixture must have at least one user turn that
        # gold marks should_retrieve — but that cross-check lives in
        # validate_gold (gold is the authority). Here we only require shape.
        if suite == "isolation" and not isinstance(fixture.get("team"), str):
            issues.append(
                "fixture.suite=isolation requires a fixture.team string "
                "(the isolation suite replays per-team fixtures)"
            )
    return issues


def _validate_per_turn(gold: dict, fixture: dict | None, issues: list[str]) -> None:
    """per_turn entries: ``{turn, should_retrieve, pointers?}``.

    ``turn`` is 1-based (matches the fixture turn list).  ``pointers`` are
    the gold-acceptable pointer ids for a should_retrieve turn (the push
    suite's precision/recall numerator).  A ``should_retrieve: false`` turn
    MUST NOT carry pointers (a fire with nowhere to aim is a false fire).
    """
    entries = gold.get("per_turn")
    if not isinstance(entries, list) or not entries:
        issues.append("gold.per_turn: expected a non-empty list")
        return
    n_turns = len(fixture.get("turns", [])) if fixture else None
    seen_turns: set[int] = set()
    for index, entry in enumerate(entries):
        where = f"gold.per_turn[{index}]"
        _require_mapping(entry, where, issues)
        if not isinstance(entry, dict):
            continue
        _reject_unknown_keys(entry, frozenset({"turn", "should_retrieve", "pointers"}), where, issues)
        turn = _expect_int(entry, "turn", where, issues, minimum=1)
        if turn is not None:
            if turn in seen_turns:
                issues.append(f"{where}: duplicate turn {turn}")
            seen_turns.add(turn)
            if n_turns is not None and turn > n_turns:
                issues.append(
                    f"{where}.turn: {turn} exceeds fixture turn count {n_turns}"
                )
        should_retrieve = entry.get("should_retrieve")
        if not isinstance(should_retrieve, bool):
            issues.append(f"{where}.should_retrieve: expected a boolean")
        pointers = entry.get("pointers")
        if pointers is not None:
            if not isinstance(pointers, list) or not pointers or not all(
                isinstance(p, str) and p.strip() for p in pointers
            ):
                issues.append(f"{where}.pointers: expected a non-empty list of strings")
            elif should_retrieve is False:
                issues.append(
                    f"{where}: a should_retrieve:false turn cannot carry pointers "
                    "(anti-gaming — courtesy turns never fire)"
                )


def _validate_continuity(gold: dict, issues: list[str]) -> None:
    """Continuity gold: writer→reader pairs.

    ``writer_session`` names the writer fixture whose write-back produced the
    planted decision; ``reader_planted`` lists the decision/entity anchors
    the reader cell must surface; ``reader_queries`` are the probe queries
    the reader session issues.  Scored on the READER cell.
    """
    cont = gold.get("continuity")
    if cont is None:
        return
    _require_mapping(cont, "gold.continuity", issues)
    if not isinstance(cont, dict):
        return
    _reject_unknown_keys(
        cont,
        frozenset({"writer_session", "reader_planted", "reader_queries"}),
        "gold.continuity", issues,
    )
    _expect_str(cont, "writer_session", "gold.continuity", issues)
    planted = cont.get("reader_planted")
    if not isinstance(planted, list) or not planted or not all(
        isinstance(p, str) and p.strip() for p in planted
    ):
        issues.append(
            "gold.continuity.reader_planted: expected a non-empty list of anchors"
        )
    queries = cont.get("reader_queries")
    if not isinstance(queries, list) or not queries or not all(
        isinstance(q, str) and q.strip() for q in queries
    ):
        issues.append(
            "gold.continuity.reader_queries: expected a non-empty list of probe queries"
        )


def _validate_write_back(gold: dict, issues: list[str]) -> None:
    """Write-back gold: planted points that must survive write-back with
    provenance intact."""
    wb = gold.get("write_back")
    if wb is None:
        return
    _require_mapping(wb, "gold.write_back", issues)
    if not isinstance(wb, dict):
        return
    _reject_unknown_keys(
        wb,
        frozenset({"planted_points", "provenance_required"}),
        "gold.write_back", issues,
    )
    planted = wb.get("planted_points")
    if not isinstance(planted, list) or not planted or not all(
        isinstance(p, str) and p.strip() for p in planted
    ):
        issues.append(
            "gold.write_back.planted_points: expected a non-empty list of anchor strings"
        )
    prov = wb.get("provenance_required")
    if prov is not None and not isinstance(prov, bool):
        issues.append("gold.write_back.provenance_required: expected a boolean")


def validate_gold(gold: dict, fixture: dict | None = None) -> list[str]:
    """DM-7 validation: sealed gold for a fixture (shape + cross-checks).

    Suite expectations:
    * know_to_ask / push — ``per_turn`` should_retrieve labels + pointers.
    * write_back — ``write_back`` planted anchors + provenance requirement.
    * continuity — ``continuity`` writer→reader spec.
    * isolation — per-team fixtures share the multi-team gold (validated by
      the isolation runner step; gold carries ``teams`` expectation).
    """
    issues: list[str] = []
    _require_mapping(gold, "gold", issues)
    if not issues:
        _reject_unknown_keys(
            gold,
            frozenset({"suite", "per_turn", "write_back", "continuity",
                       "teams", "schema_version", "session_id"}),
            "gold", issues,
        )
        suite = _expect_enum(gold, "suite", SUITE_VALUES, "gold", issues)
        _expect_int(gold, "schema_version", "gold", issues, minimum=1)
        _expect_str(gold, "session_id", "gold", issues)
        if suite in ("know_to_ask", "push"):
            _validate_per_turn(gold, fixture, issues)
        elif suite == "write_back":
            _validate_write_back(gold, issues)
        elif suite == "continuity":
            _validate_continuity(gold, issues)
        elif suite == "isolation":
            teams = gold.get("teams")
            if not isinstance(teams, dict) or not teams or not all(
                isinstance(v, dict) for v in teams.values()
            ):
                issues.append(
                    "gold.teams: expected an object mapping team name → its gold"
                )
    return issues


def fixture_gold_consistent(fixture: dict, gold: dict, session_id: str) -> list[str]:
    """Fixture↔gold pair consistency (run at pre-flight, mirrors W2-b's
    paired validation).  Suite must agree; the gold's session_id must match
    the pair's filename stem (the fixture is keyed by filename — DM-6 has no
    embedded session_id); a continuity writer-fixture must not carry
    per_turn labels it can't honor (no reflex graded on a writer pass)."""
    issues: list[str] = []
    if fixture.get("suite") != gold.get("suite"):
        issues.append(
            f"suite mismatch: fixture {fixture.get('suite')!r} vs "
            f"gold {gold.get('suite')!r}"
        )
    gid = str(gold.get("session_id", "")).removesuffix(".json")
    if gid and gid != session_id:
        issues.append(f"session_id mismatch: stem {session_id!r} vs gold {gid!r}")
    return issues


# ═══ Per-suite graded aggregates → baseline metrics ═════════════════════════

def aggregate_metrics(session_results: list[dict]) -> dict:
    """Fold per-session suite grades into the canonical metric snapshot.

    ``session_results`` is one dict per replayed session (see
    ``runner.grade_session``)::

        {"session_id", "suite", "kta": {"missed": int, "should": int},
         "false_fire": {"fires": int, "silent_required": int},
         "push": {"prec": float, "recall": float},
         "write_back": {...}, "continuity": {...},
         "isolation": {"violations": int}, "emitted": bool}

    Aggregation is POOLED.  ``know_to_ask_failure_rate`` = missed / should;
    ``false_fire_rate`` = fires / turns that required silence; push
    precision/recall pooled; ``source_isolation_violations`` is a raw count
    (the 0 gate).  An empty denominator collapses the metric to its WORST
    value (minimize rates → 1.0, maximize rates → 0.0): a suite with no
    graded demand must never read as a clean pass (review round-1 P1/C —
    the preflight suite-denominator floor + the runner's suite-coverage
    check make a 0-denominator suite on a real run a runner error; this
    collapse keeps an empty/partial aggregate honest too).
    """
    kta_missed = sum((r.get("kta") or {}).get("missed", 0) for r in session_results)
    kta_should = sum((r.get("kta") or {}).get("should", 0) for r in session_results)
    ff_fires = sum((r.get("false_fire") or {}).get("fires", 0) for r in session_results)
    ff_silent = sum((r.get("false_fire") or {}).get("silent_required", 0)
                    for r in session_results)
    push_prec_num = sum((r.get("push") or {}).get("prec_num", 0) for r in session_results)
    push_prec_den = sum((r.get("push") or {}).get("prec_den", 0) for r in session_results)
    push_rec_num = sum((r.get("push") or {}).get("recall_num", 0) for r in session_results)
    push_rec_den = sum((r.get("push") or {}).get("recall_den", 0) for r in session_results)
    wb_survived = sum((r.get("write_back") or {}).get("survived", 0)
                      for r in session_results)
    wb_total = sum((r.get("write_back") or {}).get("total", 0)
                   for r in session_results)
    cont_surfaced = sum((r.get("continuity") or {}).get("surfaced", 0)
                        for r in session_results)
    cont_total = sum((r.get("continuity") or {}).get("total", 0)
                     for r in session_results)
    iso_violations = sum((r.get("isolation") or {}).get("violations", 0)
                         for r in session_results)
    return {
        # Empty denominator ⇒ WORST: minimize rates collapse to 1.0 (a
        # suite with no graded demand must not read as a clean pass).
        "know_to_ask_failure_rate": (kta_missed / kta_should if kta_should else 1.0),
        "false_fire_rate": (ff_fires / ff_silent if ff_silent else 1.0),
        "push_precision": (push_prec_num / push_prec_den if push_prec_den else 0.0),
        "push_recall": (push_rec_num / push_rec_den if push_rec_den else 0.0),
        "write_back_fidelity": (wb_survived / wb_total if wb_total else 0.0),
        "continuity_recall": (cont_surfaced / cont_total if cont_total else 0.0),
        "source_isolation_violations": iso_violations,
    }


def read_json(path: Path) -> dict:
    """Read a JSON doc (validated upstream by the corpus/gold validators)."""
    import json

    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump_json_bytes(doc: dict) -> bytes:
    import json

    return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode("utf-8")

# ═══ Baseline machinery (harness vocabulary; discipline mirrors W2-b) ═══════

def _validate_metric_values(metrics: dict, where: str, issues: list[str]) -> None:
    """Type/range-check one metrics snapshot (current or a history entry).

    Rate metrics (all but source_isolation_violations) are fractions in
    [0, 1]; ``source_isolation_violations`` is an int >= 0 (the E2E-4 gate
    count).  String or out-of-range committed values would crash compare_run
    at the gate or bless an impossible target.
    """
    for key, value in metrics.items():
        if key not in METRIC_VALUES:
            issues.append(f"{where}: unknown metric {key!r} (vocabulary: {sorted(METRIC_VALUES)})")
            continue
        if key == "source_isolation_violations":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                issues.append(f"{where}.{key}: expected a non-negative int, got {value!r}")
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(f"{where}.{key}: expected a number, got {value!r}")
        elif not (0.0 <= float(value) <= 1.0):
            issues.append(f"{where}.{key}: expected a fraction in [0, 1], got {value!r}")


def compare_run(run_metrics: dict, baseline: dict, *, resolved_config: dict,
                run_fixtures_hash: str, run_judge_pin: str | None = None) -> str:
    """The --compare verdict for a run against the committed baseline
    (same contract as W2-b, harness vocabulary).

    Verdicts:
    * INCONCLUSIVE — fixtures_hash / resolved_config / judge_pin mismatch
      (cross-corpus, cross-posture, or cross-protocol runs never compare).
    * REGRESSION  — any metric moved in its wrong direction vs the committed
      snapshot, OR the standing quality bars tripped (below).
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
    # Standing quality bars (research-shaped targets from issue #2099):
    # know-to-ask failure > 0.00, false-fire > 0.03, push precision < 1.000
    # are REGRESSIONS on every run — even when the committed baseline was
    # itself over-tolerance (a bad first number records per the fix-wave
    # protocol but never legitimizes a future run at the same level).  The
    # reflex-graded bars activate only when the committed baseline's
    # config.reflex == "graded": under a NULL-reflex baseline (pre-W4) the
    # kta/push numbers are the honest baseline, not a gate.  Source-isolation
    # > 0 is ALWAYS a regression (E2E-4 — the rig's isolation property, this
    # issue's own pass gate, live from day one).
    committed_reflex = (baseline.get("config") or {}).get("reflex")
    if committed_reflex == "graded":
        if run_metrics.get("know_to_ask_failure_rate", 0.0) > KTA_FAILURE_TOLERANCE:
            return VERDICT_REGRESSION
        if run_metrics.get("false_fire_rate", 0.0) > FALSE_FIRE_TOLERANCE:
            return VERDICT_REGRESSION
        if run_metrics.get("push_precision", 1.0) < PUSH_PRECISION_FLOOR:
            return VERDICT_REGRESSION
    if run_metrics.get("source_isolation_violations", 0) > SOURCE_ISOLATION_TOLERANCE:
        return VERDICT_REGRESSION
    return VERDICT_PASS


def bless_baseline(previous: dict, run: dict, *, justification: str,
                   corpus_bless: bool = False, protocol_bless: bool = False) -> dict:
    """Produce the next committed baseline from a run result (discipline
    ported from write_path.schema.bless_baseline — same guards, harness
    vocabulary)."""
    if not isinstance(justification, str) or not justification.strip():
        raise ValueError("blessing a baseline requires a non-empty justification string")
    judge_pin = run.get("judge_pin")
    if not isinstance(judge_pin, str) or not judge_pin.strip():
        raise ValueError(
            "publishing a baseline requires a non-null judge_pin "
            "(the pinned judge prompt version)"
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
    hash_changed = bool(previous.get("fixtures_hash")) and \
        run["fixtures_hash"] != previous.get("fixtures_hash")
    pin_changed = bool(previous_pin) and judge_pin != previous_pin
    reflex_repin = False
    verdict = None
    if hash_changed and not corpus_bless:
        raise ValueError(
            "cannot bless: run fixtures_hash differs from the committed "
            "baseline (corpus drift). For an INTENTIONAL fixture/gold "
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
        if run["fixtures_hash"] != previous.get("fixtures_hash") and not (
            hash_changed and corpus_bless
        ):
            raise ValueError(
                "cannot bless first publish: run fixtures_hash does not match the "
                "committed pending baseline (corpus drift)"
            )
        if run["config"] != previous.get("config") and not (hash_changed and corpus_bless):
            raise ValueError(
                "cannot bless first publish: run config does not match the committed "
                "baseline config snapshot (config mismatch => inconclusive)"
            )
        verdict = None
    elif (hash_changed and corpus_bless) or (pin_changed and protocol_bless):
        verdict = None  # deliberate re-pin — no comparability, no compare
    else:
        # A config diff is only ever blessable as a deliberate protocol
        # re-pin of the reflex switch (null -> graded when the W4 reflex
        # lands) — any other config drift raises below.
        config_diff = run["config"] != previous.get("config")
        reflex_repin = False
        if config_diff and protocol_bless:
            prev_cfg = {k: v for k, v in (previous.get("config") or {}).items()}
            run_cfg = {k: v for k, v in run["config"].items()}
            prev_cfg.pop("reflex", None)
            run_cfg.pop("reflex", None)
            reflex_repin = prev_cfg == run_cfg and (
                (previous.get("config") or {}).get("reflex") == "null"
                and run["config"].get("reflex") == "graded"
            )
        if config_diff and not reflex_repin:
            raise ValueError(
                "cannot bless: run config differs from the committed baseline "
                "config snapshot (config mismatch => inconclusive)"
            )
        if reflex_repin:
            verdict = None  # null->graded reflex switch is a protocol change
        else:
            verdict = compare_run(
                run["metrics"], previous,
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
    if reflex_repin:
        history_entry["protocol_change"] = True
        history_entry["reflex_graded"] = True
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
        frozenset({"date", "values", "failure_classes", "justification",
                   "verdict", "corpus_change", "protocol_change",
                   "reflex_graded", "correction"}),
        where, issues,
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
    """Validate a committed harness baseline document + invariants (same
    shape discipline as W2-b, harness config/metric vocabulary)."""
    issues: list[str] = []
    _require_mapping(baseline, "baseline", issues)
    if not issues:
        _reject_unknown_keys(
            baseline,
            frozenset({"schema_version", "fixtures_hash", "judge_pin", "config",
                       "justification", "metrics", "history"}),
            "baseline", issues,
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
                frozenset({"suites", "mode", "reflex", "holdout_excluded", "seed",
                           "extractor_posture"}),
                "baseline.config", issues,
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
            _expect_enum(config, "extractor_posture", frozenset({"llm", "m2"}),
                         "baseline.config", issues)
            seed = config.get("seed")
            if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
                issues.append("baseline.config.seed: expected an int or null")
        justification = baseline.get("justification")
        if justification is not None and (
            not isinstance(justification, str) or not justification.strip()
        ):
            issues.append("baseline.justification: expected null or a non-empty string")
        judge_pin = baseline.get("judge_pin")
        if judge_pin is not None and (
            not isinstance(judge_pin, str) or not judge_pin.strip()
        ):
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
        # Cross-invariants (review round-1 P2, parity with W2-b): a PUBLISHED
        # baseline (non-empty metrics) must carry a judge_pin AND a
        # justification; a first-run PENDING baseline (empty metrics) must
        # carry neither — a published snapshot without its pin would make
        # compare_run's pin guard inert (numbers blessed under one protocol
        # compared against a pin-less target).
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
