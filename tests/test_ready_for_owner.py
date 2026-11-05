"""Condition 2 of the owner-readiness gate: every REQUIRED check is green at head.

WHY THESE CASES
---------------
The defect this tool closes was measured: a readiness verdict said "READY", the
rail said `CONFLICTING`, and a required check was `ai-review-gate: CANCELLED`
(#10962). Four different lanes had four different definitions of "green". So the
cases below are the definitions, one per spelling of not-green:

  * `cancelled` is NOT a pass — and the aggregate that the merge rail uses
    (`ci_verdict.aggregate`) DOES call that commit green, which is why this
    module may not use it. Pinned in `test_aggregate_would_call_it_green`;
  * `null`/absent conclusion, `neutral`/`skipped`, a missing check and an
    in-flight one are all NOT passes;
  * a name in BOTH condition lists is a CONFIGURATION ERROR, not a pass;
  * an unreadable required set is `FAIL(unreadable)` (exit 2), never an assumed
    pass — the same rule `check-required-set.py` states for itself;
  * a fully green head is a PASS (without this row, every "always FAIL" mutant
    would satisfy the suite).

Everything here is pure: `ci_verdict.compute_verdict` builds the verdict from
check-run payloads, so nothing touches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import ci_verdict, ready_for_owner
from tools.ready_for_owner import (
    FAIL,
    GATE_CONFIG_ERROR,
    GATE_FAIL,
    GATE_PASS,
    GATE_UNREADABLE,
    PASS,
    STATE_REQUIRED_SET_ONLY,
    RequiredSet,
    Unreadable,
    derive_required_set,
    evaluate,
    main,
    read_live_contexts,
    read_snapshot,
)

SHA = "0123456789abcdef0123456789abcdef01234567"

# The repo's real required set, as `.mergify.yml` derives it today. Pinned as a
# fixture so a change to the config is a visible edit here, not a silent
# re-baseline — and so a broadcast failure below is not mistaken for a real one.
QUEUE = ("pricing-artifact", "docs", "test-isolation", "license-surface",
         "legal-e2e", "ai-review-gate")
MERGE = ("python-ci-gate",)
ALL_REQUIRED = QUEUE + MERGE


def required(queue=QUEUE, merge=MERGE, source="mergify") -> RequiredSet:
    return RequiredSet(queue=frozenset(queue), merge=frozenset(merge), source=source)


def check(name: str, conclusion: str | None = "success", status: str = "completed",
          check_id: int = 1, run: int | None = None) -> dict:
    """One check-run payload, shaped as the REST API returns it.

    `run` is the Actions run the check belongs to. Two attempts of the SAME job
    share a run (and therefore a group); two different workflows emitting the
    same name do not — which is the distinction the two group tests below turn on.
    """
    run_id = check_id if run is None else run
    return {
        "name": name,
        "id": check_id,
        "status": status,
        "conclusion": conclusion,
        "app": {"slug": "github-actions"},
        "details_url": f"https://github.com/daniel-ospina/tortoise/actions/runs/{run_id}/job/1",
        "started_at": "2026-11-04T23:00:00Z",
    }


def verdict_of(*check_runs: dict):
    return ci_verdict.compute_verdict(SHA, list(check_runs), (),
                                      repo="daniel-ospina/tortoise")


def all_green(**overrides: dict) -> dict:
    """A head where every required check is `success`, with named overrides."""
    runs = []
    for index, name in enumerate(ALL_REQUIRED):
        runs.append(check(name, check_id=index + 1, **(overrides.get(name) or {})))
    return verdict_of(*runs)


# ── the positive row (a suite of only negative rows cannot see "always FAIL") ──


def test_a_fully_green_head_passes():
    result = evaluate(required(), all_green())
    assert result.state == GATE_PASS, result.reasons
    assert {row.name for row in result.rows} == set(ALL_REQUIRED)
    assert all(row.state == PASS for row in result.rows)


# ── every spelling of not-green ───────────────────────────────────────────────


def test_a_cancelled_required_check_fails():
    """#10962's shape: `ai-review-gate: CANCELLED` is not green."""
    result = evaluate(required(), all_green(**{"ai-review-gate": {"conclusion": "cancelled"}}))
    assert result.state == GATE_FAIL
    row = next(r for r in result.rows if r.name == "ai-review-gate")
    assert row.state == FAIL and "cancelled" in row.detail


def test_aggregate_would_call_it_green_which_is_why_it_is_not_used():
    """The divergence is DELIBERATE and pinned, not an oversight.

    `ci_verdict.aggregate` treats `cancelled` as ABSENT (non-red) for the merge
    rail, so the whole commit reads `green` while a required check never ran.
    Condition 2 answers Mergify's `check-success` question, where that is a FAIL.
    If this assertion ever fails, `ci_verdict` changed its rail semantics — check
    which question the change was for before touching either side.
    """
    verdict = all_green(**{"ai-review-gate": {"conclusion": "cancelled"}})
    assert verdict.verdict == ci_verdict.GREEN
    assert evaluate(required(), verdict).state == GATE_FAIL


def test_a_null_conclusion_fails():
    result = evaluate(required(), all_green(**{"docs": {"conclusion": None}}))
    assert result.state == GATE_FAIL
    row = next(r for r in result.rows if r.name == "docs")
    assert row.state == FAIL and "no conclusion" in row.detail


def test_a_missing_required_check_fails():
    verdict = verdict_of(*[check(n, check_id=i + 1) for i, n in enumerate(QUEUE)
                           if n != "python-ci-gate"])
    result = evaluate(required(), verdict)
    assert result.state == GATE_FAIL
    row = next(r for r in result.rows if r.name == "python-ci-gate")
    assert row.state == FAIL and row.detail.startswith("missing")


def test_an_in_flight_required_check_is_not_yet_ready():
    verdict = all_green(**{"legal-e2e": {"status": "in_progress", "conclusion": None}})
    result = evaluate(required(), verdict)
    assert result.state == GATE_FAIL
    row = next(r for r in result.rows if r.name == "legal-e2e")
    assert row.state == FAIL and "in-flight" in row.detail


@pytest.mark.parametrize("conclusion", ["neutral", "skipped"])
def test_neutral_or_skipped_is_not_a_check_success_pass(conclusion):
    """`.mergify.yml` contract point 3: `check-success` is deliberate."""
    result = evaluate(required(), all_green(**{"docs": {"conclusion": conclusion}}))
    assert result.state == GATE_FAIL
    row = next(r for r in result.rows if r.name == "docs")
    assert row.state == FAIL and "not a check-success pass" in row.detail


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "action_required",
                                        "startup_failure", "some-new-conclusion"])
def test_every_other_completed_conclusion_fails(conclusion):
    """Fail-closed polarity: an undocumented conclusion is never green."""
    result = evaluate(required(), all_green(**{"docs": {"conclusion": conclusion}}))
    assert result.state == GATE_FAIL


def test_a_name_in_both_lists_is_a_configuration_error():
    """The contract requires EXACTLY ONE list; an overlap cannot be 'green'."""
    result = evaluate(required(queue=(*QUEUE, "python-ci-gate")), all_green())
    assert result.state == GATE_CONFIG_ERROR
    assert any("BOTH" in reason for reason in result.reasons)


def test_an_empty_required_set_is_unreadable_not_passed():
    result = evaluate(RequiredSet(queue=frozenset(), merge=frozenset(), source="mergify"),
                      all_green())
    assert result.state == GATE_UNREADABLE


# ── latest attempt, and several groups sharing a name ─────────────────────────


def test_a_newer_success_clears_an_earlier_cancellation():
    """Cancelled is never green — but it is not permanent either."""
    verdict = verdict_of(
        check("docs", "cancelled", check_id=10, run=999),
        check("docs", "success", check_id=11, run=999),
        *[check(n, check_id=i + 20) for i, n in enumerate(ALL_REQUIRED) if n != "docs"],
    )
    assert evaluate(required(), verdict).state == GATE_PASS


def test_a_newer_cancellation_is_not_masked_by_an_earlier_success():
    verdict = verdict_of(
        check("docs", "success", check_id=10, run=999),
        check("docs", "cancelled", check_id=11, run=999),
        *[check(n, check_id=i + 20) for i, n in enumerate(ALL_REQUIRED) if n != "docs"],
    )
    result = evaluate(required(), verdict)
    assert result.state == GATE_FAIL


def test_one_cancelled_producer_of_a_name_fails_the_whole_name():
    """Two workflows emitting `docs`: the WORST one decides, never the best.

    ⚠️ The two DIFFERENT `run=` ids are load-bearing — they are what makes these
    two check-runs two GROUPS. With one shared run id `ci_verdict` collapses them
    into a single group, the latest attempt wins, and the reduction under test
    never executes: measured, "return the passing row if any" then left the suite
    green. Asserting the detail keeps that visible.
    """
    verdict = verdict_of(
        check("docs", "success", check_id=10, run=1000),
        check("docs", "cancelled", check_id=11, run=1001),
        *[check(n, check_id=i + 20) for i, n in enumerate(ALL_REQUIRED) if n != "docs"],
    )
    assert len([g for g in verdict.groups if g.job == "docs"]) == 2
    result = evaluate(required(), verdict)
    assert result.state == GATE_FAIL
    row = next(r for r in result.rows if r.name == "docs")
    assert "cancelled" in row.detail


def test_a_measured_red_survives_a_red_less_rerun():
    """`voided_red`: a failure followed by a cancellation is STILL red.

    Without this row the `voided_red` branch could return PASS and the suite would
    not notice — a `cancelled` attempt cannot clear a red.
    """
    verdict = verdict_of(
        check("docs", "failure", check_id=10, run=999),
        check("docs", "cancelled", check_id=11, run=999),
        *[check(n, check_id=i + 20) for i, n in enumerate(ALL_REQUIRED) if n != "docs"],
    )
    result = evaluate(required(), verdict)
    assert result.state == GATE_FAIL
    row = next(r for r in result.rows if r.name == "docs")
    assert "red" in row.detail


# ── the derived set ───────────────────────────────────────────────────────────


def test_the_real_repo_derives_seven_names_from_the_config():
    """The repo's own set, with no credential and no network."""
    derived = derive_required_set()
    assert derived.source == "mergify"
    assert derived.names == frozenset(ALL_REQUIRED)
    assert derived.overlap == frozenset()
    assert derived.queue == frozenset(QUEUE) and derived.merge == frozenset(MERGE)
    # The injection bucket is EMPTY today; if a name is ever filed there it joins
    # `names` (and this assertion is the visible edit).
    assert derived.injected == frozenset()
    assert any("injection bucket: empty" in note for note in derived.notes), derived.notes


def test_the_committed_snapshot_is_compared_and_agrees_today():
    derived = derive_required_set()
    assert any("required-contexts.json agrees" in note for note in derived.notes), derived.notes


def test_a_record_naming_a_check_the_config_drops_is_unsafe_drift():
    """The record is the LIVE picture; a config edit cannot remove a live requirement.

    So `snapshot - derived` means those names would be UNGRADED — certified against a
    smaller set than the queue blocks on. It must refuse, not footnote.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text(json.dumps({"required_contexts": ["docs", "ghost-check"]}))
        derived = derive_required_set(snapshot_path=snapshot)
    assert derived.source.startswith("mergify")
    assert derived.names == frozenset(ALL_REQUIRED)
    assert derived.unsafe_drift == frozenset({"ghost-check"})
    assert any("ghost-check" in note and "UNGRADED" in note for note in derived.notes), derived.notes
    refused = evaluate(derived, all_green())
    assert refused.state == GATE_CONFIG_ERROR
    assert "ghost-check" in " ".join(refused.reasons)


def test_a_record_that_misses_a_name_is_stale_and_inert_not_unsafe():
    """Direction matters: `derived - snapshot` is a stale record, and the config wins."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text(json.dumps({"required_contexts": ["docs"]}))
        derived = derive_required_set(snapshot_path=snapshot)
    assert derived.unsafe_drift == frozenset()
    assert any("stale, and inert" in note for note in derived.notes), derived.notes
    assert evaluate(derived, all_green()).state == GATE_PASS


def test_the_snapshot_is_the_fallback_when_the_config_is_unreadable():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text(json.dumps({"required_contexts": sorted(ALL_REQUIRED)}))
        derived = derive_required_set(mergify_path=Path(tmp) / "absent.yml",
                                      snapshot_path=snapshot)
    assert derived.source.startswith("snapshot")
    assert derived.names == frozenset(ALL_REQUIRED)
    assert any("fell back" in note for note in derived.notes), derived.notes


def test_both_sources_unreadable_raises_unreadable():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp, pytest.raises(Unreadable) as excinfo:
        derive_required_set(mergify_path=Path(tmp) / "absent.yml",
                            snapshot_path=Path(tmp) / "absent.json")
    assert "no usable committed snapshot" in str(excinfo.value)


def test_a_corrupt_snapshot_alone_does_not_make_the_set_unreadable():
    """It is a fallback + cross-check; a broken one is noted, and the config answers."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text("{not json")
        derived = derive_required_set(snapshot_path=snapshot)
    assert derived.source.startswith("mergify")
    assert any("snapshot unreadable" in note for note in derived.notes), derived.notes


def test_a_snapshot_that_is_not_a_mapping_cannot_be_measured():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text("[1, 2, 3]")
        with pytest.raises(Unreadable):
            read_snapshot(snapshot)


# ── the CLI's fail-closed exits ───────────────────────────────────────────────


def test_the_cli_exits_two_when_the_required_set_is_unreadable(capsys):
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        code = main(["--mergify", str(Path(tmp) / "absent.yml"),
                     "--snapshot", str(Path(tmp) / "absent.json")])
    assert code == 2
    assert "FAIL(unreadable)" in capsys.readouterr().out


def test_the_cli_prints_the_derived_set_without_a_network_call(capsys):
    assert main(["--required-only"]) == 0
    out = capsys.readouterr().out
    assert "python-ci-gate" in out and "overlap" in out and "contract satisfied" in out


# ── the credential gap, reported as what it is ───────────────────────────────


class _FakeChecker:
    """A stand-in for the imported guard module, so the read is exercised offline."""

    def __init__(self, *, contexts=None, message=None):
        self._contexts = contexts
        self._message = message

    def read_live_protection(self):
        if self._message is not None:
            raise RuntimeError(self._message)
        return set(self._contexts or ()), False


def test_a_scope_403_is_not_reported_as_a_rate_limit(monkeypatch):
    """The measured message must survive: a lane must not wait on a bucket.

    `read_live_protection` is the guard's (one reader, not two), and its
    CannotMeasure message carries gh's stderr — including
    "needs admin-scoped credentials", which is the SCOPE shape.
    """
    fake = _FakeChecker(
        message="could not read branch protection (needs admin-scoped credentials): "
                "gh: Resource not accessible by personal access token (HTTP 403)")
    monkeypatch.setattr(ready_for_owner, "load_checker", lambda path=None: fake)
    with pytest.raises(Unreadable) as excinfo:
        read_live_contexts("daniel-ospina/tortoise")
    message = str(excinfo.value)
    # Anchored on the PHRASE, not on "scope": the echoed text contains "scoped"
    # (from "admin-scoped"), which satisfied a bare substring assertion even when
    # the scope branch was deleted.
    assert "PROTECTION-UNREADABLE" in message and "scope, not rate limit" in message
    assert "GH-RATE-LIMITED" not in message


def test_a_rate_limited_protection_read_says_so(monkeypatch):
    """THE SHAPE THE REAL GUARD EMITS: every failure carries the scope prefix.

    `check-required-set.py::read_live_protection` unconditionally writes
    "could not read branch protection (needs admin-scoped credentials): " and then
    appends gh's stderr — so a rate-limited read ALSO matches a scope test. Ordering
    the rate-limit test first is the fix; this fixtures that exact string, because a
    friendly "could not read branch protection: API rate limit exceeded" never occurs
    in production and let the misdiagnosis through.
    """
    fake = _FakeChecker(
        message="could not read branch protection (needs admin-scoped credentials): "
                "gh: API rate limit exceeded for user ID 1. (HTTP 403)")
    monkeypatch.setattr(ready_for_owner, "load_checker", lambda path=None: fake)
    with pytest.raises(Unreadable) as excinfo:
        read_live_contexts("daniel-ospina/tortoise")
    message = str(excinfo.value)
    assert message.startswith("GH-RATE-LIMITED(")
    # the scope VERDICT must not appear (the echoed guard text still says "admin-scoped")
    assert "scope, not rate limit" not in message


def test_an_unrecognised_protection_failure_is_not_labelled_a_rate_limit(monkeypatch):
    """A 404 / expired token / network error must not send a reader to a bucket."""
    fake = _FakeChecker(message="could not read branch protection: HTTP 404 Branch not protected")
    monkeypatch.setattr(ready_for_owner, "load_checker", lambda path=None: fake)
    with pytest.raises(Unreadable) as excinfo:
        read_live_contexts("daniel-ospina/tortoise")
    message = str(excinfo.value)
    assert "PROTECTION-UNREADABLE" in message and "GH-RATE-LIMITED" not in message


def test_the_live_read_is_refused_for_another_repo(monkeypatch):
    """The guard's reader is pinned to this repo; a wrong-repo read is worse than none."""
    monkeypatch.setattr(ready_for_owner, "load_checker",
                        lambda path=None: _FakeChecker(contexts=ALL_REQUIRED))
    with pytest.raises(Unreadable) as excinfo:
        read_live_contexts("someone-else/other-repo")
    assert "refused" in str(excinfo.value)


def test_the_live_read_goes_through_the_imported_guard(monkeypatch):
    """ONE reader: a success is returned as-is, and `strict` is validated by the guard."""
    monkeypatch.setattr(ready_for_owner, "load_checker",
                        lambda path=None: _FakeChecker(contexts=ALL_REQUIRED))
    assert read_live_contexts("daniel-ospina/tortoise") == frozenset(ALL_REQUIRED)


def test_live_protection_agreement_is_reachable_offline(monkeypatch, capsys):
    """`--live` compares; it must not be a second, unchecked source of truth.

    The assertion is on the NOTE the comparison prints: exit 0 plus "required set"
    is also satisfied when `--live` does nothing at all, so a mutation that
    disables the block left the earlier version of this test green.
    """
    monkeypatch.setattr(ready_for_owner, "read_live_contexts",
                        lambda repo: frozenset(ALL_REQUIRED))
    assert main(["--live", "--required-only"]) == 0
    out = capsys.readouterr().out
    assert "live protection agrees: 7 context(s)" in out
    assert "required set" in out


def test_live_protection_disagreement_refuses_to_pass(monkeypatch, capsys):
    monkeypatch.setattr(ready_for_owner, "read_live_contexts",
                        lambda repo: frozenset({"docs"}))
    assert main(["--live", "--required-only"]) == 1
    assert GATE_CONFIG_ERROR in capsys.readouterr().out


# ── the two ways a name can be ABSENT (measured on #10962) ────────────────────


def test_an_absent_name_while_its_dependencies_run_is_not_yet_reported():
    """`python-ci-gate` has a 13-job `needs:` list, so it gets NO check-run until
    they finish. Measured on #10962: absent while 22 test shards were `queued`.
    Reporting that as "never produced" would name a deadlock that is not there.
    """
    verdict = verdict_of(
        *[check(n, check_id=i + 1) for i, n in enumerate(ALL_REQUIRED)
          if n != "python-ci-gate"],
        check("test (a)", status="queued", conclusion=None, check_id=90),
    )
    result = evaluate(required(), verdict)
    row = next(r for r in result.rows if r.name == "python-ci-gate")
    assert result.state == GATE_FAIL
    assert "not yet reported" in row.detail and "still running" in row.detail


def test_an_absent_name_with_nothing_running_is_missing():
    """Nothing in flight, no check-run: this is the config/deadlock shape."""
    verdict = verdict_of(*[check(n, check_id=i + 1) for i, n in enumerate(ALL_REQUIRED)
                           if n != "python-ci-gate"])
    row = next(r for r in evaluate(required(), verdict).rows if r.name == "python-ci-gate")
    assert row.detail.startswith("missing")


# ── an empty set is never a pass (finding: the CLI printed "contract satisfied" on 0 names) ──


def test_an_empty_snapshot_is_refused_as_a_fallback(capsys):
    """A snapshot recording ZERO contexts is not a fallback — it is nothing compared."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text(json.dumps({"required_contexts": []}))
        code = main(["--mergify", str(Path(tmp) / "absent.yml"),
                     "--snapshot", str(snapshot), "--required-only"])
    assert code == 2
    assert "FAIL(unreadable)" in capsys.readouterr().out


def test_a_config_with_no_check_conditions_and_no_snapshot_is_unreadable(capsys):
    """Only `base=main`/`-draft` in the lists: nothing to compare, so exit 2."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mergify = Path(tmp) / ".mergify.yml"
        mergify.write_text("queue_rules:\n  - name: main\n    queue_conditions:\n"
                           "      - base=main\n")
        code = main(["--mergify", str(mergify),
                     "--snapshot", str(Path(tmp) / "absent.json"), "--required-only"])
    assert code == 2
    assert "FAIL(unreadable)" in capsys.readouterr().out


# ── the injection bucket: required on main, named in NEITHER list ────────────


class _FakeDerivingChecker:
    """A guard module whose injection bucket is non-empty."""

    def load_mergify(self, path=None):
        return {"queue": {"docs"}, "merge": {"python-ci-gate"}}

    def injected_names(self):
        return {"injected-gate"}


def test_an_injected_name_is_part_of_the_required_set():
    """`queue ∪ merge` is NOT the whole required set (contract point 1)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text(json.dumps(
            {"required_contexts": ["docs", "injected-gate", "python-ci-gate"]}))
        derived = derive_required_set(
            mergify_path=Path(tmp) / "any.yml", snapshot_path=snapshot,
            checker=_FakeDerivingChecker())
    assert derived.injected == frozenset({"injected-gate"})
    assert derived.names == frozenset({"docs", "injected-gate", "python-ci-gate"})
    assert any("injection bucket: ['injected-gate']" in note for note in derived.notes)


def test_a_red_injected_name_fails_condition_2():
    """The latent false PASS: an injected required check that never went green."""
    req = RequiredSet(queue=frozenset({"docs"}), merge=frozenset(),
                      source="mergify", injected=frozenset({"injected-gate"}))
    verdict = verdict_of(check("docs"), check("injected-gate", "cancelled", check_id=2))
    result = evaluate(req, verdict)
    assert result.state == GATE_FAIL
    row = next(r for r in result.rows if r.name == "injected-gate")
    assert "cancelled" in row.detail


def test_a_broken_injection_bucket_cannot_be_measured():
    """Silently dropping the bucket would be a false PASS; it raises instead."""
    import tempfile

    class Broken(_FakeDerivingChecker):
        def injected_names(self):
            raise RuntimeError("enumeration unreadable")

    with tempfile.TemporaryDirectory() as tmp, pytest.raises(Unreadable) as excinfo:
        derive_required_set(mergify_path=Path(tmp) / "any.yml",
                            snapshot_path=Path(tmp) / "none.json", checker=Broken())
    assert "injected bucket" in str(excinfo.value)


# ── `--json` emits ONE parseable document, and it never omits a failing PR ───


def test_json_on_a_pr_run_parses(capsys, monkeypatch):
    """Prose must not be appended to a JSON document (it was, and json.loads raised)."""
    monkeypatch.setattr(ready_for_owner, "read_pr_head", lambda repo, n: (SHA, "blocked"))
    monkeypatch.setattr(ready_for_owner, "evaluate_head",
                        lambda req, repo, sha: evaluate(req, all_green(), sha))
    assert main(["--pr", "1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reports"][0]["pr"] == 1 and payload["reports"][0]["state"] == GATE_PASS


def test_json_includes_a_pr_whose_surface_is_unreadable(capsys, monkeypatch):
    """A failing PR must appear in the record; an omission reads as "no problem"."""
    monkeypatch.setattr(ready_for_owner, "read_pr_head", lambda repo, n: (SHA, "blocked"))

    def boom(req, repo, sha):
        raise Unreadable("check surface unreadable for " + sha)

    monkeypatch.setattr(ready_for_owner, "evaluate_head", boom)
    assert main(["--pr", "7", "--json"]) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["reports"][0]["pr"] == 7
    assert payload["reports"][0]["state"] == GATE_UNREADABLE
    assert "::error::" in captured.err, "annotations must go to stderr under --json"


def test_a_derivation_that_yields_no_names_is_unreadable():
    """Defence in depth: even a parser that returns EMPTY sets cannot produce a pass.

    The reachable paths already fail closed (the guard's own `load_mergify` raises
    when both lists carry no check, and an empty snapshot is refused), so this pins
    the third guard directly — without it, a mutation that removes the explicit
    raise left the whole suite green.
    """
    import tempfile

    class EmptyChecker:
        def load_mergify(self, path=None):
            return {"queue": set(), "merge": set()}

        def injected_names(self):
            return set()

    with tempfile.TemporaryDirectory() as tmp, pytest.raises(Unreadable) as excinfo:
        derive_required_set(mergify_path=Path(tmp) / "any.yml",
                            snapshot_path=Path(tmp) / "none.json",
                            checker=EmptyChecker())
    assert "EMPTY after derivation" in str(excinfo.value)


# ── round-2 findings ─────────────────────────────────────────────────────────


def test_required_only_with_pr_still_emits_ONE_json_document(capsys, monkeypatch):
    """`--required-only --pr N` used to print TWO concatenated JSON objects.

    A strict parser raised; a first-document reader saw `"prs": []` — a report that
    silently omits the PR, the very shape the omission fix was for.
    """
    monkeypatch.setattr(ready_for_owner, "read_pr_head", lambda repo, n: (SHA, "blocked"))
    monkeypatch.setattr(ready_for_owner, "evaluate_head",
                        lambda req, repo, sha: evaluate(req, all_green(), sha))
    assert main(["--required-only", "--pr", "3", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)  # raises if two documents
    assert payload["reports"][0]["pr"] == 3
    assert payload["required"]["union"]


def test_a_guard_without_the_injected_reader_cannot_be_measured():
    """An ABSENT `injected_names()` is not an empty bucket.

    Treating it as empty is a silent false PASS for any required check that is
    enforced by branch-protection injection — the round-1 fix would be undone by a
    rename in the guard.
    """
    import tempfile

    class NoReader:
        def load_mergify(self, path=None):
            return {"queue": {"docs"}, "merge": {"python-ci-gate"}}

    with tempfile.TemporaryDirectory() as tmp, pytest.raises(Unreadable) as excinfo:
        derive_required_set(mergify_path=Path(tmp) / "any.yml",
                            snapshot_path=Path(tmp) / "none.json",
                            checker=NoReader())
    assert "injected_names" in str(excinfo.value)


def test_grading_another_repo_is_refused(capsys):
    """`--repo other/name` would grade it against THIS checkout's required set."""
    assert main(["--repo", "someone-else/other-repo", "--required-only"]) == 2
    assert "refused" in capsys.readouterr().out


# ── the rate-limit marker, as the run rule requires ──────────────────────────


def test_a_rate_limited_pr_read_names_the_endpoint_and_the_bucket(monkeypatch):
    """A 403 from an exhausted bucket must not read as a scope failure or a mystery."""
    import subprocess

    class Limited:
        returncode = 1
        stdout = ""
        stderr = "gh: API rate limit exceeded for user ID 1. (HTTP 403)"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Limited())
    with pytest.raises(Unreadable) as excinfo:
        ready_for_owner.read_pr_head("daniel-ospina/tortoise", 42)
    message = str(excinfo.value)
    assert message.startswith("GH-RATE-LIMITED(repos/daniel-ospina/tortoise/pulls/42,")
    assert "rate limit" in message.lower()


def test_a_rate_limited_check_surface_names_the_bucket(monkeypatch):
    class Boom(Exception):
        pass

    def fake_read_verdict(repo, sha):
        raise ci_verdict.SurfaceReadError("gh api ... failed (rc=1): API rate limit exceeded")

    monkeypatch.setattr(ci_verdict, "read_verdict", fake_read_verdict)
    with pytest.raises(Unreadable) as excinfo:
        ready_for_owner.evaluate_head(required(), "daniel-ospina/tortoise", SHA)
    assert str(excinfo.value).startswith("GH-RATE-LIMITED(")


@pytest.mark.parametrize("argv,needle", [
    (["--json", "--mergify", "/nonexistent/.mergify.yml",
      "--snapshot", "/nonexistent/snap.json"], "unreadable"),
    (["--json", "--repo", "someone-else/other-repo", "--required-only"], "refused"),
])
def test_json_emits_a_document_on_the_pre_loop_refusals(capsys, argv, needle):
    """Every exit path under `--json` writes ONE document — including the refusals.

    These two paths used to print nothing at all, so a JSON consumer could not tell
    "cannot measure" from a truncated run and had to special-case exactly two exits.
    """
    assert main(argv) == 2
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # empty stdout raises here
    assert payload["state"] == GATE_UNREADABLE
    assert any(needle in reason for reason in payload["reasons"]), payload


# ── round-4 findings ─────────────────────────────────────────────────────────


def test_the_entry_point_guard_is_the_first_statement():
    """The repo's #5128 contract: `[docstring] [__future__] import sys <guard>`.

    `tests/test_entry_point_python_guard.py::_guard_index` requires exactly that
    preamble and reds `python-ci-gate` otherwise. This asserts it HERE too, so the
    requirement is visible in the file that owns the guard — and so a re-ordering
    fails fast instead of at the required check.
    """
    import ast

    body = ast.parse(Path(ready_for_owner.__file__).read_text()).body
    index = 1 if isinstance(body[0], ast.Expr) else 0
    while isinstance(body[index], ast.ImportFrom) and body[index].module == "__future__":
        index += 1
    node = body[index]
    assert isinstance(node, ast.Import) and [a.name for a in node.names] == ["sys"], ast.dump(node)
    guard = body[index + 1]
    assert isinstance(guard, ast.If), "the #5128 guard must follow `import sys`"


def test_a_bare_invocation_measures_nothing_and_is_not_a_pass(capsys):
    """No `--pr` and no `--required-only`: nothing was compared, so exit 2."""
    assert main([]) == 2
    assert "nothing was measured" in capsys.readouterr().out


def test_required_only_never_claims_condition_2_pass(capsys):
    """`--required-only` answers "what IS required" — it must not report PASS."""
    assert main(["--required-only", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == STATE_REQUIRED_SET_ONLY
    assert payload["state"] != GATE_PASS


def test_a_dead_run_with_a_negative_conclusion_is_red_not_in_flight():
    """`ci_verdict` calls a non-completed status with a failure conclusion RED.

    Reporting it as in-flight tells a lane to keep waiting on a run that has
    already failed.
    """
    verdict = verdict_of(
        *[check(n, check_id=i + 1) for i, n in enumerate(ALL_REQUIRED) if n != "docs"],
        check("docs", "failure", status="weird", check_id=90),
    )
    row = next(r for r in evaluate(required(), verdict).rows if r.name == "docs")
    assert row.state == FAIL and row.detail.startswith("red")


def test_the_snapshot_fallback_keeps_the_injection_bucket():
    """`.mergify.yml` is unreadable exactly WHILE IT IS BEING EDITED — fallback time.

    Dropping the injection bucket there would return a set missing an injected
    required check, i.e. a false PASS on the only path with no cross-check.
    """
    import tempfile

    class Raiser:
        def load_mergify(self, path=None):
            raise RuntimeError("CannotMeasure: mergify config not found")

        def injected_names(self):
            return {"injected-gate"}

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text(json.dumps({"required_contexts": ["docs", "python-ci-gate"]}))
        derived = derive_required_set(mergify_path=Path(tmp) / "absent.yml",
                                      snapshot_path=snapshot, checker=Raiser())
    assert derived.source.startswith("snapshot")
    assert derived.injected == frozenset({"injected-gate"})
    assert "injected-gate" in derived.names
    assert any("fallback keeps the injection bucket" in note for note in derived.notes)


def test_an_unrecognised_protection_failure_is_not_called_a_scope_failure_either():
    """The guard prefixes EVERY failure with "(needs admin-scoped credentials)":.

    Keying the scope message on that prefix classified a 404 / renamed endpoint as a
    credential failure. Only the real 403 marker may claim a scope problem.
    """
    fake = _FakeChecker(
        message="could not read branch protection (needs admin-scoped credentials): "
                "gh: Not Found (HTTP 404)")
    monkeypatch_marker = pytest.MonkeyPatch()
    monkeypatch_marker.setattr(ready_for_owner, "load_checker", lambda path=None: fake)
    try:
        with pytest.raises(Unreadable) as excinfo:
            read_live_contexts("daniel-ospina/tortoise")
    finally:
        monkeypatch_marker.undo()
    message = str(excinfo.value)
    assert "PROTECTION-UNREADABLE" in message
    assert "scope, not rate limit" not in message
    assert "GH-RATE-LIMITED" not in message


# ── round-6 findings ─────────────────────────────────────────────────────────


def test_a_not_green_pr_exits_one_and_never_reports_pass(capsys, monkeypatch):
    """THE wiring that turns a graded FAIL into exit 1 — untested until round 6.

    With this branch neutralised, a PR whose required `docs` check is `cancelled`
    printed `state: PASS` and exited 0. Every other test asserted `evaluate()`;
    none asserted the process contract a shell consumer actually reads.
    """
    monkeypatch.setattr(ready_for_owner, "read_pr_head", lambda repo, n: (SHA, "blocked"))
    cancelled = all_green(**{"docs": {"conclusion": "cancelled"}})
    monkeypatch.setattr(ready_for_owner, "evaluate_head",
                        lambda req, repo, sha: evaluate(req, cancelled, sha))
    assert main(["--pr", "5"]) == 1
    out = capsys.readouterr()
    assert "⛔ docs" in out.out and "cancelled" in out.out
    assert "PASS" not in out.out.split("→")[-1]

    assert main(["--pr", "5", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == GATE_FAIL
    assert payload["reports"][0]["state"] == GATE_FAIL


def test_the_human_report_renders_every_row(capsys, monkeypatch):
    """The documented plain invocation (`--pr N`, no `--json`) is pinned here.

    `_print_report` was executed by no test, so it could raise or print nothing and
    the suite stayed green — while it is the form a lane actually reads.
    """
    monkeypatch.setattr(ready_for_owner, "read_pr_head", lambda repo, n: (SHA, "blocked"))
    monkeypatch.setattr(ready_for_owner, "evaluate_head",
                        lambda req, repo, sha: evaluate(req, all_green(), sha))
    assert main(["--pr", "9"]) == 0
    out = capsys.readouterr().out
    assert "PR #9" in out and SHA in out and "mergeable_state=blocked" in out
    assert out.count("✅") == len(ALL_REQUIRED)
    assert "→ PASS" in out


def test_an_unreadable_snapshot_dir_becomes_a_note_not_a_traceback(tmp_path):
    """`Path.exists()` re-raises EACCES on 3.12 — unguarded it escaped as a TRACEBACK.

    A traceback exits 1, which the rail reads as "a required check is red", and it
    writes zero bytes to `--json` stdout. The snapshot is a fallback + cross-check,
    so an unreadable one is a NOTE and the CONFIG still answers. (A path whose
    parent is a FILE is a different, deliberate case: `exists()` returns False, and
    "no snapshot" is then correct — the guard this module imports records the same
    distinction.)
    """
    import os

    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "required-contexts.json").write_text(
        json.dumps({"required_contexts": ["docs"]}))
    locked.chmod(0o000)
    try:
        derived = derive_required_set(snapshot_path=locked / "required-contexts.json")
    finally:
        locked.chmod(0o700)
    assert derived.source.startswith("mergify")
    assert any("snapshot unreadable" in note for note in derived.notes), derived.notes


def test_a_snapshot_declaring_nothing_is_not_read_as_absent(capsys):
    """PRESENT-but-undeclared must be noted, never silently dropped."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "required-contexts.json"
        snapshot.write_text(json.dumps({}))
        derived = derive_required_set(snapshot_path=snapshot)
    assert derived.source.startswith("mergify")
    assert any("snapshot unreadable" in note and "declares no" in note
               for note in derived.notes), derived.notes


# ── round-7 findings (test gaps, plus `reasons` on every refusal) ─────────────


def test_required_only_exits_one_on_an_overlapping_config(capsys):
    """The credential-free form a lane is most likely to run must not exit 0.

    `evaluate()`'s overlap was unit-tested; the CLI's exit code on this path was not.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        mergify = Path(tmp) / ".mergify.yml"
        mergify.write_text(
            "queue_rules:\n  - name: main\n    queue_conditions:\n"
            "      - check-success=docs\n      - check-success=python-ci-gate\n"
            "    merge_conditions:\n      - check-success=python-ci-gate\n")
        code = main(["--mergify", str(mergify),
                     "--snapshot", str(Path(tmp) / "absent.json"), "--required-only"])
    assert code == 1
    assert "config-error" in capsys.readouterr().out


def test_every_json_refusal_carries_a_reason(capsys):
    """A consumer reading `payload["reasons"]` must not KeyError on a refusal."""
    assert main(["--json"]) == 2                      # bare invocation
    bare = json.loads(capsys.readouterr().out)
    assert bare["state"] == GATE_UNREADABLE and bare["reasons"]

    assert main(["--json", "--repo", "someone-else/other-repo", "--required-only"]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["reasons"]

    monkeypatch_live = pytest.MonkeyPatch()
    monkeypatch_live.setattr(ready_for_owner, "read_live_contexts",
                             lambda repo: frozenset({"docs"}))
    try:
        assert main(["--json", "--live", "--required-only"]) == 1
        disagree = json.loads(capsys.readouterr().out)
    finally:
        monkeypatch_live.undo()
    assert disagree["state"] == GATE_CONFIG_ERROR and disagree["reasons"]

    monkeypatch_unreadable = pytest.MonkeyPatch()

    def unreadable(repo):
        raise Unreadable("PROTECTION-UNREADABLE(daniel-ospina/tortoise: scope, not rate limit)")

    monkeypatch_unreadable.setattr(ready_for_owner, "read_live_contexts", unreadable)
    try:
        assert main(["--json", "--live", "--required-only"]) == 2
        from_live = json.loads(capsys.readouterr().out)
    finally:
        monkeypatch_unreadable.undo()
    assert from_live["state"] == GATE_UNREADABLE and from_live["reasons"]


def test_a_cancelled_check_detail_says_never_green(capsys):
    """The `GROUP_ABSENT` branch must be DISCRIMINATED, not just reach a FAIL.

    Deleting it leaves the catch-all `FAIL(conclusion)` — same verdict, and an
    assertion on "cancelled" alone still passes. The reason string is the pinned part.
    """
    verdict = all_green(**{"docs": {"conclusion": "cancelled"}})
    row = next(r for r in evaluate(required(), verdict).rows if r.name == "docs")
    assert row.detail == "cancelled: never green"


def test_evaluate_head_binds_the_requested_sha(monkeypatch):
    """The verdict must be stamped with the head that was ASKED for.

    Every PR-path test mocked `evaluate_head`, so nothing exercised the sha binding —
    a substituted sha would still have read as a pass in the suite.
    """
    seen = {}

    def spy(repo, sha):
        seen["sha"] = sha
        # a verdict carrying THIS sha and no check-runs: enough to see the binding
        return ci_verdict.compute_verdict(sha, [], ())

    monkeypatch.setattr(ci_verdict, "read_verdict", spy)
    result = ready_for_owner.evaluate_head(required(), "daniel-ospina/tortoise", SHA)
    assert seen["sha"] == SHA
    assert result.sha == SHA


# ── round-8 findings ──────────────────────────────────────────────────────────


def test_read_pr_head_reads_the_key_its_jq_filter_emits(monkeypatch):
    """The jq filter emitted `.mergeable_state` as `state`; the reader read
    `mergeable_state` — so the field was ALWAYS "unknown", on every run.

    This exercises `read_pr_head` itself: the renderer test monkeypatches it, so it
    could never see the parse.
    """
    import subprocess

    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=json.dumps({"sha": SHA, "mergeable_state": "blocked", "pr_state": "open"}),
            stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    sha, mergeable = ready_for_owner.read_pr_head("daniel-ospina/tortoise", 10962)
    assert sha == SHA
    assert mergeable == "blocked", mergeable
    # the filter must ASK for the key the reader reads
    assert "mergeable_state: .mergeable_state" in " ".join(seen["cmd"])


def test_a_measured_fail_outranks_an_unreadable_sibling(capsys):
    """`max()` let exit 2 dominate exit 1, so a caller whose rule is "2 → retry"
    retried forever over a PR that had already answered FAIL."""
    monkeypatch_marker = pytest.MonkeyPatch()
    def heads(repo, number):
        if number == 2:
            raise Unreadable("GH-RATE-LIMITED(repos/x/y/check-runs,0)")
        return SHA, "clean"

    monkeypatch_marker.setattr(ready_for_owner, "read_pr_head", heads)
    monkeypatch_marker.setattr(ready_for_owner, "evaluate_head",
                               lambda required, repo, sha: evaluate(
                                   required, all_green(**{"docs": {"conclusion": "cancelled"}})))
    try:
        code = main(["--pr", "1", "--pr", "2", "--json"])
    finally:
        monkeypatch_marker.undo()
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == GATE_FAIL

    # …and in the OTHER order: an unreadable PR FIRST must not swallow the later
    # red (the two directions are separate code paths, so both are pinned).
    monkeypatch_marker = pytest.MonkeyPatch()
    monkeypatch_marker.setattr(ready_for_owner, "read_pr_head", heads)
    monkeypatch_marker.setattr(ready_for_owner, "evaluate_head",
                               lambda required, repo, sha: evaluate(
                                   required, all_green(**{"docs": {"conclusion": "cancelled"}})))
    try:
        reversed_code = main(["--pr", "2", "--pr", "1", "--json"])
    finally:
        monkeypatch_marker.undo()
    assert reversed_code == 1
    assert json.loads(capsys.readouterr().out)["state"] == GATE_FAIL


def test_a_supplied_config_is_named_by_its_path_not_as_the_repo_config(tmp_path, capsys):
    """A caller-invented config must not certify as if it were the repo's own set."""
    mergify = tmp_path / ".mergify.yml"
    mergify.write_text("queue_rules:\n  - name: main\n    queue_conditions:\n"
                       "      - check-success=docs\n    merge_conditions:\n"
                       "      - check-success=python-ci-gate\n")
    record = tmp_path / "required-contexts.json"
    record.write_text(json.dumps({"required_contexts": ["docs", "python-ci-gate"]}))
    assert main(["--mergify", str(mergify), "--snapshot", str(record),
                 "--json", "--required-only"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["required"]["source"] == f"mergify:{mergify} (record: {record})"
    # …and the JSON document for this non-verdict state still explains itself
    assert payload["state"] == STATE_REQUIRED_SET_ONLY and payload["reasons"]


# ── round-9 findings ──────────────────────────────────────────────────────────


def test_required_only_refuses_unsafe_drift_too(tmp_path, capsys):
    """The docstring lists the record-drift config error as exit 1 with NO `--pr`
    qualifier, but only the overlap branch refused on the credential-free path."""
    record = tmp_path / "required-contexts.json"
    record.write_text(json.dumps({"required_contexts": [*sorted(ALL_REQUIRED), "ghost"]}))
    assert main(["--snapshot", str(record), "--json", "--required-only"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == GATE_CONFIG_ERROR
    assert payload["required"]["unsafe_drift"] == ["ghost"]
    assert payload["reasons"]


def test_live_protection_clears_a_stale_record(tmp_path, capsys):
    """`--live` is the authority on live protection: if it agrees with the config,
    the disagreeing record is provably stale, so the drift refusal must lift."""
    record = tmp_path / "required-contexts.json"
    record.write_text(json.dumps({"required_contexts": [*sorted(ALL_REQUIRED), "ghost"]}))
    marker = pytest.MonkeyPatch()
    marker.setattr(ready_for_owner, "read_live_contexts",
                   lambda repo: frozenset(ALL_REQUIRED))
    try:
        code = main(["--snapshot", str(record), "--live", "--json", "--required-only"])
    finally:
        marker.undo()
    payload = json.loads(capsys.readouterr().out)
    assert code == 0, payload
    assert payload["state"] == STATE_REQUIRED_SET_ONLY
    assert payload["required"]["unsafe_drift"] == []
    assert "drift cleared" in payload["required"]["source"]
    assert any("stale" in note for note in payload["required"]["notes"])


def test_a_supplied_record_is_never_reported_as_the_committed_one(tmp_path):
    """The cross-check must not go vacuous while the note names a file nobody read."""
    record = tmp_path / "mine.json"
    record.write_text(json.dumps({"required_contexts": [*sorted(ALL_REQUIRED), "ghost"]}))
    derived = derive_required_set(snapshot_path=record)
    joined = " | ".join(derived.notes)
    assert str(record) in joined
    assert "docs/ci/required-contexts.json" not in joined, joined
    assert str(record) in derived.source


def test_the_graded_json_document_carries_reasons(capsys):
    """Every refusal carries `reasons`; the VERDICT — the document a consumer most
    needs to explain — must too."""
    marker = pytest.MonkeyPatch()
    marker.setattr(ready_for_owner, "read_pr_head", lambda repo, number: (SHA, "clean"))
    marker.setattr(ready_for_owner, "evaluate_head",
                   lambda required, repo, sha: evaluate(
                       required, all_green(**{"docs": {"conclusion": "cancelled"}})))
    try:
        assert main(["--pr", "1", "--json"]) == 1
    finally:
        marker.undo()
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == GATE_FAIL and payload["reasons"]
    assert any("docs" in reason for reason in payload["reasons"])


# ── round-10 findings ─────────────────────────────────────────────────────────


def test_clearing_drift_removes_the_note_it_disproved(tmp_path, capsys):
    """A PASS that still says "these names would go UNGRADED" is a contradiction.

    `replace(notes=(*old, "drift cleared"))` APPENDED, so the ⚠️ line survived into
    both the machine record and the human report.
    """
    record = tmp_path / "required-contexts.json"
    record.write_text(json.dumps({"required_contexts": [*sorted(ALL_REQUIRED), "ghost"]}))
    marker = pytest.MonkeyPatch()
    marker.setattr(ready_for_owner, "read_live_contexts",
                   lambda repo: frozenset(ALL_REQUIRED))
    try:
        assert main(["--snapshot", str(record), "--live", "--json", "--required-only"]) == 0
    finally:
        marker.undo()
    notes = json.loads(capsys.readouterr().out)["required"]["notes"]
    assert not any("UNGRADED" in note for note in notes), notes
    assert any("drift cleared" in note for note in notes), notes


def test_the_graded_report_and_the_derived_document_agree_on_keys():
    """ONE builder: both views must be the same document, not two literals."""
    derived = derive_required_set()
    from_required_only = ready_for_owner._required_doc(derived)
    from_verdict = evaluate(derived, all_green()).to_dict()["required"]
    assert from_verdict == from_required_only


def test_a_config_error_at_the_head_is_not_flattened_to_plain_fail(capsys):
    """`FAIL(config-error)` must stay distinguishable: re-running never fixes it."""
    marker = pytest.MonkeyPatch()
    marker.setattr(ready_for_owner, "read_pr_head", lambda repo, number: (SHA, "clean"))
    marker.setattr(ready_for_owner, "evaluate_head",
                   lambda required, repo, sha: evaluate(required, all_green()))
    bad = RequiredSet(queue=frozenset({"docs"}), merge=frozenset({"docs"}),
                      source="mergify")
    marker.setattr(ready_for_owner, "derive_required_set", lambda *a, **k: bad)
    try:
        code = main(["--pr", "1", "--json"])
    finally:
        marker.undo()
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["state"] == GATE_CONFIG_ERROR, payload["state"]
    assert payload["reports"][0]["state"] == GATE_CONFIG_ERROR


def test_a_usage_error_still_emits_one_document(capsys):
    """`--json` promises one document on EVERY path; argparse wrote zero bytes.

    A consumer parsing stdout got an empty document from a run that exited 2 —
    indistinguishable from a truncated run.
    """
    assert main(["--json", "--pr", "notanint"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == GATE_UNREADABLE and payload["reasons"]


def test_help_is_not_turned_into_a_refusal(capsys):
    """`--help` raises SystemExit(0); mapping it to 2 would make `--help` an error."""
    assert main(["--help"]) == 0
    capsys.readouterr()
    # …and help + --json must not ALSO emit a usage-error document
    assert main(["--help", "--json"]) == 0
    assert '"state"' not in capsys.readouterr().out


def test_notes_are_not_double_prefixed(capsys):
    """The printer adds `note: `; a note that already says it prints `note: note: `."""
    record_dir = Path(__file__).resolve().parent.parent / "docs" / "ci"
    snapshot = record_dir / "required-contexts.json"
    assert main(["--snapshot", str(snapshot), "--required-only"]) == 0
    out = capsys.readouterr().out
    assert "note: note:" not in out, out


def test_an_unread_config_does_not_claim_the_contract_is_satisfied(tmp_path, capsys):
    """`overlap: none — contract satisfied` for a config that was NEVER READ is a
    measurement that did not happen — the class of claim this tool removes."""
    record = tmp_path / "required-contexts.json"
    record.write_text(json.dumps({"required_contexts": sorted(ALL_REQUIRED)}))
    assert main(["--mergify", str(tmp_path / "absent.yml"), "--snapshot", str(record),
                 "--required-only"]) == 0
    out = capsys.readouterr().out
    assert "contract satisfied" not in out, out
    assert "not measured" in out
