"""#10017 step 9 — the purge GUARD's refusals, pinned.

A destructive tool whose guard has never refused anything is not gated. This
file therefore pins the REFUSALS (the three the lane brief mandates, plus the
ones the issue adds) and the two pure decisions the refusals rest on:
per-session CLASSIFICATION and the exact-set DIGEST.

⛔ AND IT PINS THEM **BY EFFECT** WHERE IT CAN (#10483 review, P1): a refusal
asserted only as a pure function's return value leaves `main`'s wiring — and
`_execute` itself — free to be inverted while every test stays green. The
`test_execute_wiring_*` and `test_execute_refuses_*_before_any_delete` cases
below drive `main([... "--execute" ...])` or `_execute(...)` with the store legs
patched, and assert that no DELETE was issued; the positive arm is there too, so
the set is not "a guard that always says no".

Nothing here touches a graph, a Postgres, or a container: the census legs are
monkeypatched, `evaluate_gate` and `classify_sessions` are pure over a
census-shaped report, and the SDK/projection seams `_execute` imports are
replaced with fakes that record the statements they are asked to run.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import purge_legacy_turns as purge

# ── fixtures: census-shaped reports, built by hand ───────────────────────

def _clean(session: str, ids: list[str], record_rows: int | None = None):
    return {"session": session,
            "graph_turn_nodes": len(ids),
            "record_turn_rows": (len(ids) if record_rows is None
                                 else record_rows),
            "counts_equal": (len(ids) == (len(ids) if record_rows is None
                                          else record_rows))}


def _failed(session: str, ids: list[str], problems: list[dict]):
    return {"session": session, "graph_turn_nodes": len(ids),
            "record_turn_rows": len(ids), "counts_equal": True,
            "problems": problems}


def _report(clean=None, failed=None) -> dict:
    clean = clean or []
    failed = failed or []
    would_delete = [tid for v in clean for tid in v.get("ids", [])] or []
    # The census emits `would_delete_ids` from its own per-session node map; the
    # fixture carries each clean session's ids on a side key so the helper can
    # reproduce that list without pretending to be the store legs.
    return {
        "population": {"episodic_graph_nodes": 9, "turn_shaped_nodes": 9,
                       "sessions_with_graph_turns": len(clean) + len(failed),
                       "outside_population": 0},
        "record": {"turn_rows_total": 9, "episodic_rows_not_turn_shaped": 0},
        "parity": {"clean_sessions": clean, "failed_sessions": failed},
        "anomalies": {k: [] for k in ("orphan", "cross_wired",
                                      "non_decimal_suffix", "operator_flagged",
                                      "session_mismatch", "non_turn_point_kind",
                                      "non_string_id")},
        "dry_run": {"would_delete_turn_ids": would_delete,
                    "would_delete_turn_nodes": len(would_delete),
                    "held_back_turn_nodes": sum(v["graph_turn_nodes"]
                                                for v in failed),
                    "would_delete_ids": would_delete},
        "verdict": "CLEAN" if not failed else "REFUSED",
        "refused_for": ["parity"] if failed else [],
    }


def _clean_with_ids(session: str, ids: list[str], record_rows=None):
    v = _clean(session, ids, record_rows)
    v["ids"] = ids
    return v


def _ts(age_seconds: float = 60) -> str:
    """An ISO-8601 stamp `age_seconds` old — every caller-supplied artefact now
    has to state WHEN it was read (a file with no age describes any run)."""
    stamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _target(graph_name: str | None = None) -> dict:
    """The target block `_target_from_args` derives for `_TARGET_ARGV`."""
    return {"graph": graph_name or "<uri default>",
            "uri_host": "127.0.0.1:1", "db_path": None,
            "org_id": "org", "graph_id": "graph",
            "record_dsn_present": True}


_TARGET_ARGV = ["--uri", "docker://:pw@127.0.0.1:1/x",
                "--org-id", "org", "--graph-id", "graph"]


def _health(armed=True, age: float = 60) -> dict:
    return {"record": {"armed": armed, "ok": True,
                       "org_id": "org", "graph_id": "graph",
                       "recorded_at": _ts(age)}}


def _proof(session: str = "s1", age: float = 60, org: str = "org",
           graph_id: str = "graph", graph: str = "<uri default>") -> dict:
    """A restore proof that names a session the default plan actually purges —
    a proof about some OTHER session proves nothing (see REFUSE_RESTORE_PROOF_STALE).
    It also states its ADDRESS and its AGE, which the gate now requires."""
    return {"issue": 10017, "scratch_session": session,
            "served_from_record": True, "recorded_at": _ts(age),
            "org_id": org, "graph_id": graph_id, "graph": graph}


def _approval(digest: str | None = None, who: str = "owner") -> dict:
    """The approval, recorded OUTSIDE the plan file: the plan cannot approve
    itself (`--confirm-plan-digest` alone compares two fields of one artefact)."""
    return {"issue": 10017, "plan_digest": digest, "approved_by": who,
            "recorded_at": _ts(60)}


def _plan(clean, *, journal_path: str | None = "/tmp/journal.jsonl") -> dict:
    report = _report(clean=clean)
    return purge.build_plan(report, target=_target(), health=_health(),
                            restore_proof=_proof(), target_note="test target",
                            recovery_missing=[], journal_path=journal_path)


def _gate(plan, live=None, health=None, proof=None, confirm=None,
          note="test target", live_target=None, approval=None):
    return purge.evaluate_gate(
        plan=plan, live_report=live,
        health=_health() if health is None else health,
        restore_proof=_proof() if proof is None else proof,
        approval=(_approval(plan["plan_digest"]) if approval is None
                  else approval),
        confirm_digest=(plan["plan_digest"] if confirm is None else confirm),
        target_note=note,
        live_target=(plan["target"] if live_target is None else live_target),
    )


# ── classification: what may be touched, and what may never be ───────────

def test_parity_session_is_eligible_and_record_ahead_is_counted_separately():
    report = _report(clean=[_clean_with_ids("s1", ["s1_t0", "s1_t1"],
                                           record_rows=5)])
    out = purge.classify_sessions(report)
    assert [e["session"] for e in out["eligible"]] == ["s1"]
    # Same class, but the surplus is reported rather than swallowed: a session
    # whose record holds MORE turns is still parity, and the operator sees it.
    assert out["eligible"][0]["record_ahead"] == 3
    assert out["counts"][purge.CLASS_PARITY] == 1
    assert out["counts"][purge.CLASS_RECORD_AHEAD] == 1
    assert out["excluded"] == []


def test_record_behind_session_is_excluded_and_never_eligible():
    report = _report(failed=[_failed("s2", ["s2_t0", "s2_t1"], [
        {"node": "s2_t1", "field": "key",
         "problem": "missing_in_record"}])])
    out = purge.classify_sessions(report)
    assert out["eligible"] == []
    assert out["excluded"][0]["class"] == purge.CLASS_RECORD_BEHIND
    assert out["excluded"][0]["missing"] == 1
    assert "backfill" in out["excluded"][0]["reason"]


def test_field_mismatch_is_mismatch_not_record_behind():
    report = _report(failed=[_failed("s3", ["s3_t0"], [
        {"node": "s3_t0", "field": "text",
         "problem": "field_mismatch"}])])
    out = purge.classify_sessions(report)
    assert out["excluded"][0]["class"] == purge.CLASS_MISMATCH
    assert out["counts"][purge.CLASS_MISMATCH] == 1


def test_equal_counts_with_a_mismatched_field_is_not_parity():
    """The brief's core rule: a count-only comparison is not parity."""
    report = _report(failed=[_failed("s4", ["s4_t0"], [
        {"node": "s4_t0", "field": "text",
         "problem": "field_mismatch"}])])
    verdict = report["parity"]["failed_sessions"][0]
    assert verdict["counts_equal"] is True          # counts say "same"
    out = purge.classify_sessions(report)            # parity says otherwise
    assert out["eligible"] == []
    assert out["excluded"][0]["class"] == purge.CLASS_MISMATCH


# ── the digest: the approval token ───────────────────────────────────────

def test_digest_is_order_independent_and_sensitive_to_one_node():
    a = [{"session": "s1", "ids": ["s1_t0", "s1_t1"]},
         {"session": "s2", "ids": ["s2_t0"]}]
    b = [{"session": "s2", "ids": ["s2_t0"]},
         {"session": "s1", "ids": ["s1_t1", "s1_t0"]}]
    assert purge.plan_digest(a) == purge.plan_digest(b)
    off_by_one = [{"session": "s1", "ids": ["s1_t0", "s1_t1", "s1_t2"]},
                  {"session": "s2", "ids": ["s2_t0"]}]
    assert purge.plan_digest(a) != purge.plan_digest(off_by_one)
    rehomed = [{"session": "s1", "ids": ["s1_t0"]},
               {"session": "s2", "ids": ["s2_t0", "s1_t1"]}]
    assert purge.plan_digest(a) != purge.plan_digest(rehomed)


# ── REFUSAL 1 of 3 (mandated): the record is not armed ──────────────────

def test_execute_refuses_when_the_record_is_not_armed():
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]),
        health=_health(armed=False))
    assert code == purge.REFUSE_RECORD_NOT_ARMED
    assert "record" in message and "armed" in message.lower()


def test_a_string_armed_is_not_armed():
    """`{"record": {"armed": "true"}}` is a STRING — truthiness would pass it."""
    assert purge._health_armed({"record": {"armed": "true"}}) is False
    assert purge._health_armed({"record.armed": "true"}) is False
    assert purge._health_armed({"record": {"armed": True}}) is True


# ── REFUSAL 2 of 3 (mandated): a target session is RECORD-BEHIND ────────

def test_execute_refuses_when_a_target_session_is_record_behind():
    ids = ["s1_t0", "s1_t1"]
    plan = _plan([_clean_with_ids("s1", ids)])
    # The GRAPH still holds exactly the approved ids — so the exact-set digest
    # is UNCHANGED. The record is what moved. If the gate checked only the
    # digest, this run would delete a transcript the record cannot serve back.
    live = _report(failed=[_failed("s1", ids, [
        {"node": "s1_t1", "field": "key",
         "problem": "missing_in_record"}])])
    assert purge.classify_sessions(live)["eligible"] == []
    code, _ = _gate(plan, live=live)
    # The refusal is the CLASS, not the digest: the graph still holds exactly
    # the approved ids, so `exact-set-mismatch` would send the operator to
    # re-approve a set that is still correct while the real remedy is a
    # backfill. The class check runs first for exactly this reason.
    assert code == purge.REFUSE_RECORD_BEHIND


def test_record_behind_refusal_fires_when_the_planned_set_is_unchanged():
    """Isolate the class check: the plan still names the session, the live
    classification excludes it."""
    ids = ["s1_t0"]
    plan = _plan([_clean_with_ids("s1", ids)])
    live = _report(clean=[_clean_with_ids("s1", ids)])
    # Force the planned session to be RECORD-BEHIND on the live side while the
    # graph-side id set stays identical, by moving it into `failed` too.
    live["parity"]["failed_sessions"] = [
        _failed("s1", ids, [{"node": "s1_t0", "field": "key",
                             "problem": "missing_in_record"}])]
    code, message = _gate(plan, live=live)
    assert code == purge.REFUSE_RECORD_BEHIND
    assert "s1" in message and "destroys the transcript" in message


# ── REFUSAL 3 of 3 (mandated): the live census is off by one node ───────

def test_execute_refuses_when_the_live_census_differs_by_one_node():
    ids = ["s1_t0", "s1_t1"]
    plan = _plan([_clean_with_ids("s1", ids)])
    # One node APPEARED since the plan was approved (a capture landed, a
    # backfill replayed). The approved set is a strict subset: a purge driven
    # by the plan would leave it, and a purge driven by the live graph would
    # delete an unapproved node. Either way the set is not the approved one.
    live = _report(clean=[_clean_with_ids("s1", ["s1_t0", "s1_t1", "s1_t2"])])
    code, message = _gate(plan, live=live)
    assert code == purge.REFUSE_EXACT_SET_MISMATCH
    assert "s1_t2" in message          # the difference is NAMED, not just hashed
    assert "NOT approved" in message


def test_execute_refuses_when_a_planned_node_disappeared():
    plan = _plan([_clean_with_ids("s1", ["s1_t0", "s1_t1"])])
    live = _report(clean=[_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=live)
    assert code == purge.REFUSE_EXACT_SET_MISMATCH
    assert "s1_t1" in message and "absent live" in message


# ── the two the issue adds ──────────────────────────────────────────────

def test_execute_refuses_without_a_restore_proof():
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), proof={})
    assert code == purge.REFUSE_NO_RESTORE_PROOF
    assert "precondition 3" in message


def test_execute_refuses_a_confirmation_token_that_is_not_the_plan_digest():
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), confirm="deadbeef")
    assert code == purge.REFUSE_CONFIRMATION_MISMATCH
    assert "deadbeef" in message


def test_execute_refuses_without_a_plan_or_a_target_note():
    code, _ = purge.evaluate_gate(plan=None, live_report=None,
                                  health=_health(),
                                  restore_proof=_proof(), approval=_approval("x"),
                                  confirm_digest="x", target_note="t",
                                  live_target=_target())
    assert code == purge.REFUSE_NO_PLAN
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, _ = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), note="")
    assert code == purge.REFUSE_NO_TARGET_NOTE


def test_the_gate_has_no_default_live_target():
    """The target binding shipped as `live_target=None`, i.e. a check any
    caller could omit by forgetting a keyword — a fail-open default in a guard
    whose doctrine is fail-closed (#10483, P2)."""
    import inspect
    params = inspect.signature(purge.evaluate_gate).parameters
    assert params["live_target"].default is inspect.Parameter.empty
    # …and an EMPTY target is itself a refusal, not a pass.
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), live_target={})
    assert code == purge.REFUSE_TARGET_DRIFT
    assert "no live target" in message


def test_a_clean_gate_passes_every_precondition():
    """The positive arm, so the refusals above are not a guard that always
    says no."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]))
    assert code == ""
    assert "every precondition" in message


# ── CLI: the DEFAULT is a preview, and it writes the plan artefact ───────

def test_cli_default_is_a_dry_run_and_writes_the_plan(tmp_path, capsys):
    report = _report(clean=[_clean_with_ids("s1", ["s1_t0", "s1_t1"])])
    census_path = tmp_path / "census.json"
    plan_path = tmp_path / "plan.json"
    census_path.write_text(json.dumps(report), encoding="utf-8")
    rc = purge.main(["--uri", "docker://:pw@127.0.0.1:1/x",
                     "--org-id", "org", "--graph-id", "graph",
                     "--target-note", "unit test", "--census-json",
                     str(census_path), "--plan-out", str(plan_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "NOTHING WAS DELETED" in out
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["purge"]["turn_node_total"] == 2
    assert plan["plan_digest"] == purge.plan_digest(
        [{"session": "s1", "ids": ["s1_t0", "s1_t1"]}])
    # No armed evidence and no restore proof were supplied: the plan says so
    # rather than implying a recovery path that has not been proven.
    assert plan["recovery"]["verdict"].startswith("NEEDS-RECOVERY-PATH")
    assert any("restore-proof" in m for m in plan["recovery"]["missing"])


def test_cli_refuses_execute_from_a_cached_census(tmp_path, capsys):
    census_path = tmp_path / "census.json"
    census_path.write_text(json.dumps(_report()), encoding="utf-8")
    rc = purge.main(["--uri", "docker://:pw@127.0.0.1:1/x",
                     "--org-id", "org", "--graph-id", "graph",
                     "--census-json", str(census_path), "--execute",
                     "--plan", str(tmp_path / "nope.json")])
    assert rc == 1
    assert "PREVIEW aid" in capsys.readouterr().err


def test_entry_point_carries_the_python_guard():
    """`tests/test_entry_point_python_guard.py` pins the shape for tools/; this
    asserts the contract locally so a refactor that drops the guard reds here
    too, not only in that sweep."""
    source = (Path(__file__).resolve().parents[1]
              / "tools" / "purge_legacy_turns.py").read_text(encoding="utf-8")
    assert "requires Python >= 3.12" in source
    assert source.index("requires Python >= 3.12") < source.index(
        "import argparse")


@pytest.mark.parametrize("code", [
    purge.REFUSE_NO_PLAN, purge.REFUSE_RECORD_NOT_ARMED,
    purge.REFUSE_NO_RESTORE_PROOF, purge.REFUSE_CONFIRMATION_MISMATCH,
    purge.REFUSE_EXACT_SET_MISMATCH, purge.REFUSE_RECORD_BEHIND,
    purge.REFUSE_MISMATCH, purge.REFUSE_NO_TARGET_NOTE,
])
def test_every_refusal_code_is_a_distinct_name(code):
    """A guard whose refusals are one undifferentiated "no" cannot be
    diagnosed — or tested. The codes are names, and they are unique."""
    codes = {purge.REFUSE_NO_PLAN, purge.REFUSE_PLAN_UNREADABLE,
             purge.REFUSE_NO_TARGET_NOTE, purge.REFUSE_RECORD_NOT_ARMED,
             purge.REFUSE_NO_RESTORE_PROOF, purge.REFUSE_RESTORE_PROOF_STALE,
             purge.REFUSE_CONFIRMATION_MISMATCH,
             purge.REFUSE_EXACT_SET_MISMATCH, purge.REFUSE_RECORD_BEHIND,
             purge.REFUSE_MISMATCH, purge.REFUSE_NOT_IN_PLAN,
             purge.REFUSE_JOURNAL_UNCONFIRMED, purge.REFUSE_NO_JOURNAL,
             purge.REFUSE_JOURNAL_UNUSABLE, purge.REFUSE_CENSUS_NOT_CLEAN,
             purge.REFUSE_CENSUS_UNREADABLE, purge.REFUSE_TARGET_DRIFT,
             purge.REFUSE_ARMED_EVIDENCE_STALE, purge.REFUSE_NO_APPROVAL,
             purge.REFUSE_PARTIAL_SWEEP}
    assert len(codes) == 20
    assert code in codes


# ── review round 1: the artefact/gate bindings that were missing ─────────

def test_execute_refuses_a_plan_whose_purge_list_does_not_serialise_to_its_digest():
    """A plan is two fields of one file: the digest a human approved and the
    list `--execute` deletes. An edited/merged plan must not delete nodes the
    digest never covered."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    plan["purge"]["sessions"].append({"session": "s9", "ids": ["s9_t0"],
                                      "graph_turn_nodes": 1})
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]))
    assert code == purge.REFUSE_NOT_IN_PLAN
    assert "serialise" in message


def test_execute_refuses_a_restore_proof_that_proves_nothing():
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    live = _report(clean=[_clean_with_ids("s1", ["s1_t0"])])
    for garbage in ({"garbage": 1}, ["nope"],
                    {"scratch_session": "s1", "served_from_record": False},
                    {"scratch_session": "other", "served_from_record": True}):
        code, _ = purge.evaluate_gate(
            plan=plan, live_report=live, health=_health(),
            restore_proof=garbage, approval=_approval(plan["plan_digest"]),
            confirm_digest=plan["plan_digest"], target_note="test target",
            live_target=_target())
        assert code in (purge.REFUSE_NO_RESTORE_PROOF,
                        purge.REFUSE_RESTORE_PROOF_STALE), garbage


def test_execute_refuses_when_the_census_refused_on_its_anomaly_partition():
    """An is_episodic node the census could not classify is in the DELETE SET
    and in NO session's plan, so no exact-set check can cover it."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    live = _report(clean=[_clean_with_ids("s1", ["s1_t0"])])
    live["verdict"] = "REFUSED"
    live["refused_for"] = ["outside_population"]
    code, message = _gate(plan, live=live)
    assert code == purge.REFUSE_CENSUS_NOT_CLEAN
    assert "anomaly" in message


def test_a_session_whose_count_and_id_list_disagree_cannot_be_planned():
    """The census's per-session total and its id list must be the same set, or
    the plan's headline count is a number the delete will not act on."""
    report = _report(clean=[_clean_with_ids("s1", ["s1_t0", "s1_t1"])])
    report["dry_run"]["would_delete_ids"] = ["s1_t0"]      # one id lost
    with pytest.raises(ValueError, match="not the same set"):
        purge.classify_sessions(report)


# ── review round 2: the deployment binding and the credential ──────────

def test_execute_refuses_a_plan_approved_for_another_deployment():
    """The digest covers (session, node) pairs, which a restored copy or a
    staging clone re-derives BYTE-IDENTICALLY. Only the target block can tell
    the two deployments apart."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    live = _report(clean=[_clean_with_ids("s1", ["s1_t0"])])
    elsewhere = dict(plan["target"], uri_host="prod.host:6379")
    code, message = _gate(plan, live=live, live_target=elsewhere)
    assert code == purge.REFUSE_TARGET_DRIFT
    assert "uri_host" in message and "DIFFERENT deployment" in message


def test_the_plan_never_carries_a_credential():
    """`netloc` carries the userinfo — i.e. the FalkorDB password — and a plan
    artefact is a file meant to be shared."""
    host = purge._uri_host("docker://:s3cret@127.0.0.1:16417/tortoise_test_matrix")
    assert host == "127.0.0.1:16417"
    assert "s3cret" not in host
    assert purge._uri_host("redis://user:pw@h.example") == "h.example"


def test_execute_refuses_a_plan_whose_counts_disagree_with_its_ids():
    plan = _plan([_clean_with_ids("s1", ["s1_t0", "s1_t1"])])
    live = _report(clean=[_clean_with_ids("s1", ["s1_t0", "s1_t1"])])
    plan["purge"]["turn_node_total"] = 1          # edited after approval
    code, message = _gate(plan, live=live)
    assert code == purge.REFUSE_NOT_IN_PLAN
    assert "turn_node_total" in message
