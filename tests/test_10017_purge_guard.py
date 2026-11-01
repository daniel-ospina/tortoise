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

import hashlib
import json
import re
import sys
from datetime import UTC, datetime, timedelta
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
    stamp = datetime.now(UTC) - timedelta(seconds=age_seconds)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _target(graph_name: str | None = None) -> dict:
    """The target block `_target_from_args` derives for `_TARGET_ARGV`."""
    return {"graph": graph_name or "<uri default>",
            "uri_host": "127.0.0.1:1", "db_path": None,
            "org_id": "org", "graph_id": "graph",
            "record_dsn_present": True}


_TARGET_ARGV = ["--uri", "docker://:pw@127.0.0.1:1/x",
                "--org-id", "org", "--graph-id", "graph"]

#: The record DSN this "run" reads. Its FINGERPRINT is the address the armed
#: evidence has to carry (`record.dsn`, #9548) — the only deployment handle a
#: real `/health` body publishes.
_DSN = "postgresql://record_app:pw@127.0.0.1:5432/tortoise"
_FINGERPRINT = hashlib.sha256(_DSN.encode()).hexdigest()[:8]


def _health(armed: bool = True, age: float = 60,
            fingerprint: str | None = _FINGERPRINT,
            fetched_at: object = "default") -> dict:
    """A REAL `/health` record block (#9548) — the shape the endpoint emits.

    `armed`, `dsn` (the DSN FINGERPRINT, never the value) and `fetched_at`;
    and NO `org_id`/`graph_id`, because a multi-tenant process does not know
    the tenant. The previous fixture built the invented shape the guard
    demanded, which is how a requirement no real body could satisfy stayed
    green here and refused every real deployment (#10483 review round 4, P1).
    """
    block = {"armed": armed, "ok": True, "dsn": fingerprint,
             "fetched_at": (_ts(age) if fetched_at == "default"
                            else fetched_at)}
    return {"status": "ok", "db": {"ok": True}, "record": block}


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
          note="test target", live_target=None, approval=None,
          record_dsn: str = _DSN, health_fetched: bool = True):
    return purge.evaluate_gate(
        plan=plan, live_report=live,
        health=_health() if health is None else health,
        restore_proof=_proof() if proof is None else proof,
        approval=(_approval(plan["plan_digest"]) if approval is None
                  else approval),
        confirm_digest=(plan["plan_digest"] if confirm is None else confirm),
        target_note=note,
        live_target=(plan["target"] if live_target is None else live_target),
        # The `--execute` default: the arming fact was MEASURED by this run
        # (`--health-url`). `--health-json` cannot authorise a delete (#10483
        # review, P2-3).
        health_fetched=health_fetched,
        record_dsn=record_dsn,
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
    # The GRAPH still holds exactly the approved ids. That does NOT make the
    # digest byte-identical: `evaluate_gate` re-derives the live digest from
    # `classify_sessions(live_report)["eligible"]`, and a RECORD-BEHIND session
    # is classified EXCLUDED, so the live digest drops its pairs and cannot
    # equal the approved one. A digest-only gate would therefore have refused
    # too — what the class check buys is the BETTER DIAGNOSIS: `record-behind`
    # names the backfill, instead of `exact-set-mismatch` sending the operator
    # to re-approve a set that is still correct (#10483 review round 8, P3).
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
                                  live_target=_target(), health_fetched=True)
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
    purge.REFUSE_NO_ARMED_EVIDENCE,
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
             purge.REFUSE_NO_ARMED_EVIDENCE,
             purge.REFUSE_CONFIRMATION_MISMATCH,
             purge.REFUSE_EXACT_SET_MISMATCH, purge.REFUSE_RECORD_BEHIND,
             purge.REFUSE_MISMATCH, purge.REFUSE_NOT_IN_PLAN,
             purge.REFUSE_JOURNAL_UNCONFIRMED, purge.REFUSE_NO_JOURNAL,
             purge.REFUSE_JOURNAL_UNUSABLE, purge.REFUSE_CENSUS_NOT_CLEAN,
             purge.REFUSE_CENSUS_UNREADABLE, purge.REFUSE_TARGET_DRIFT,
             purge.REFUSE_ARMED_EVIDENCE_STALE, purge.REFUSE_NO_APPROVAL,
             purge.REFUSE_PARTIAL_SWEEP}
    assert len(codes) == 21
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
            live_target=_target(), health_fetched=True)
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


# ── review round 3: the refusals proven BY EFFECT ─────────────────────────
#
# A refusal asserted as a pure function's return value leaves the wiring free to
# be inverted: `main` could drop its `if code: return 2`, or `_execute` could be
# called before the gate, and every assertion above would still pass. The cases
# below drive the real entry points with the store seams replaced, and assert on
# the STATEMENTS that were — or were not — issued.

import types  # noqa: E402 - kept next to the fakes that use it


class _Result:
    def __init__(self, rows):
        self.result_set = rows


def _lane_in_flight():
    """The §17.1 write lane open at THIS instant, or `None`.

    Read from the one ContextVar the guarded write handle consults
    (`_journal_write_authority` → `_CACHE_WRITE_AUTHORIZED`), so a fake store
    observes exactly what a real handle would decide on.
    """
    from tortoise.projection import _CACHE_WRITE_AUTHORIZED
    return _CACHE_WRITE_AUTHORIZED.get()


class _FakeGraph:
    """Records every statement `_execute` asks for.

    `survivors_after` is what the post-delete read still finds standing, so a
    partial sweep is expressible without a store. `lanes` pairs each statement
    with the §17.1 write lane in flight when it was issued, so a test can pin
    that the DELETE was DECLARED rather than merely asserted about.
    """

    def __init__(self, survivors_after=()):
        self.queries: list[str] = []
        self.lanes: list[tuple[str, object]] = []
        self.survivors_after = list(survivors_after)

    def statements(self, needle: str) -> list[str]:
        return [q for q in self.queries if needle in q]

    def query(self, cypher: str, params=None):
        self.queries.append(cypher)
        self.lanes.append((cypher, _lane_in_flight()))
        if "count(t)" in cypher:
            return _Result([[len(self.survivors_after)]])
        if "DETACH DELETE" in cypher:
            return _Result([])
        return _Result([(tid,) for tid in self.survivors_after])


def _patch_store(monkeypatch, *, survivors_after=(), sdk_graph=None,
                 journal_configured=True, journal_result=True,
                 graph_fields=None, graph_missing=(), journal_omit=()):
    """Replace the SDK / projection seams `_execute` imports, and record what
    they were asked to do.

    `sdk_graph` FORCES the graph the fake SDK reports (a drift injection). When
    it is `None` the fake derives its graph the way the real SDK does — an
    explicit `graph_name=` verbatim, else `"tortoise"`
    (`tortoise/sdk.py::_derive_graph_name`) — so a run that never HANDS the
    graph over is visible here instead of being satisfied by the fixture
    (#10483 review round 7, P1-A).

    `graph_fields` / `graph_missing` drive the NARROWED graph re-read `_execute`
    makes immediately before each session's delete (`read_graph_turns(node_ids=…)`,
    #10483 review P2-1): per-node field overrides, and ids the graph no longer
    returns. The fake mirrors the real leg's return shape, so the census's OWN
    comparison decides parity — this fixture never spells a verdict.
    """
    graph = _FakeGraph(survivors_after)
    journaled: list[str] = []
    handed_graph_names: list = []
    overrides = dict(graph_fields or {})
    absent = {str(t) for t in graph_missing}
    omit = {str(t) for t in journal_omit}

    def fake_read_graph(uri=None, db_path=None, graph_name=None, *,
                        node_ids=None, g=None):
        wanted = [str(t) for t in (node_ids or ())]
        nodes: dict[str, dict] = {}
        for tid in wanted:
            if tid in absent:
                continue
            match = purge.census_mod._TURN_ID_RE.match(tid)
            entry = {"id": tid, "content": None, "speaker": None,
                     "status": None, "createdAt": None,
                     "pointKind": "event", "is_operator": False,
                     "embedding": None,
                     "sessions": {match.group("sid")} if match else set()}
            entry.update(overrides.get(tid) or {})
            nodes[tid] = entry
        return {"nodes": nodes, "unidentifiable": []}

    class _Proj:
        def __init__(self, *args, **kwargs):
            self.g = graph
            # The census projection's own graph (what the DELETEs go through).
            self.graph_name = kwargs.get("graph_name") or "tortoise"

        @classmethod
        def from_uri(cls, uri, graph_name=None):
            return cls(graph_name=graph_name)

    class _SDK:
        def __init__(self, db_path=None, event_log_path=None, graph_name=None):
            self.db_path = db_path
            self.event_log_path = event_log_path
            self.graph_name = graph_name
            handed_graph_names.append(graph_name)

        def _get_event_log(self):
            return object() if journal_configured else None

        def _get_proj(self):
            derived = self.graph_name if self.graph_name is not None else \
                "tortoise"
            return types.SimpleNamespace(
                graph_name=sdk_graph if sdk_graph is not None else derived)

        def _journal_turn_removal(self, ids):
            journaled.extend(ids)
            # `journal_omit` models a result that SILENTLY drops an id — the
            # shape a `set(outcomes) == set(absent)` assertion exists for.
            return {tid: journal_result for tid in ids
                    if tid not in omit}

    monkeypatch.setattr("tortoise.projection.FalkorProjection", _Proj)
    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _SDK)
    monkeypatch.setattr(purge.census_mod, "read_graph_turns", fake_read_graph)
    # What the tool HANDED the SDK (`graph_name=`), so a test can pin that the
    # binding is explicit rather than derived from a fixture.
    graph.sdk_graph_names = handed_graph_names
    return graph, journaled


def _record_row(tid: str, **over) -> dict:
    """One `public.point` row as the census reads it, matching a default graph
    node field-for-field.

    A row carrying only `node_key` is NOT a parity row: the graph holds
    `pointKind: "event"`, so the census's own field comparison reports a
    `pointKind` mismatch — which is the comparison working, not a fixture
    problem. Both sides are therefore stated here, so a test that wants drift
    has to introduce it deliberately.
    """
    row = {"node_key": tid, "content": None, "speaker": None,
           "status": None, "created_at": None, "point_kind": "event",
           "embedding": None, "is_episodic": True}
    row.update(over)
    return row


def _patch_record(monkeypatch, rows_by_key: dict):
    """The record leg, scoped: returns only the keys asked for, like the real
    `node_keys=` filter."""
    calls: list[dict] = []

    def fake(dsn, org_id, graph_id, node_keys=None):
        wanted = list(node_keys or ())
        calls.append({"org_id": org_id, "graph_id": graph_id,
                      "node_keys": wanted})
        if node_keys is None:
            return {"rows_by_key": dict(rows_by_key), "non_turn_shaped": 0}
        keep = set(wanted)
        return {"rows_by_key": {k: v for k, v in rows_by_key.items()
                                if k in keep}, "non_turn_shaped": 0}

    monkeypatch.setattr(purge.census_mod, "read_record_turns", fake)
    return calls


class _Exec:
    def __init__(self, args, plan, target, graph, journaled, record_calls):
        self.args, self.plan, self.target = args, plan, target
        self.graph, self.journaled = graph, journaled
        self.record_calls = record_calls


def _exec_setup(tmp_path, monkeypatch, *, ids=("s1_t0",), session="s1",
                journal_lines=1, plan_journal="same", argv_journal="same",
                survivors_after=(), sdk_graph="g", journal_configured=True,
                journal_result=True, record_keys=None, graph_name="g",
                graph_fields=None, graph_missing=(), journal_omit=()):
    """A hermetic `_execute` call: real args, real plan, fake stores.

    `graph_name=None` OMITS `--graph-name`, which is the case the SDK/census
    alignment check used to skip entirely (#10483 review round 7, P1-A).
    """
    journal = tmp_path / "journal.jsonl"
    if journal_lines:
        journal.write_text('{"type": "PointAdded"}\n', encoding="utf-8")
    argv_journal_path = (journal if argv_journal == "same"
                         else tmp_path / "missing-journal.jsonl")
    argv = ["--db-path", str(tmp_path / "graph.db"),
            "--org-id", "org", "--graph-id", "graph",
            "--record-dsn", "postgresql://record",
            "--event-log-path", str(argv_journal_path)]
    if graph_name is not None:
        argv += ["--graph-name", graph_name]
    args = purge._build_parser().parse_args(argv)
    target = purge._target_from_args(args)
    plan_journal_path = (str(journal) if plan_journal == "same"
                         else None if plan_journal is None
                         else str(tmp_path / "other-journal.jsonl"))
    plan = purge.build_plan(
        _report(clean=[_clean_with_ids(session, list(ids))]),
        target=target, health=_health(), restore_proof=_proof(session=session),
        target_note="t", recovery_missing=[], journal_path=plan_journal_path)
    graph, journaled = _patch_store(monkeypatch, survivors_after=survivors_after,
                                    sdk_graph=sdk_graph,
                                    journal_configured=journal_configured,
                                    journal_result=journal_result,
                                    graph_fields=graph_fields,
                                    graph_missing=graph_missing,
                                    journal_omit=journal_omit)
    if record_keys is None:
        record_keys = {tid: _record_row(tid) for tid in ids}
    record_calls = _patch_record(monkeypatch, record_keys)
    return _Exec(args, plan, target, graph, journaled, record_calls)


# ── the wiring: a refusal must stop the delete at `main`, not only in a pure
#    function (`_execute` is replaced with a sentinel) ────────────────────

def _patch_live_census(monkeypatch, report):
    # One node so the zero-node refusal (an absence is not established) does not
    # pre-empt the gate this test is driving; the verdict itself comes from the
    # patched `census`.
    monkeypatch.setattr(purge.census_mod, "read_graph_turns",
                        lambda *a, **k: {"nodes": {"s1_t0": {"pointKind": "event"}},
                                         "unidentifiable": []})
    monkeypatch.setattr(purge.census_mod, "read_record_turns",
                        lambda *a, **k: {"rows_by_key": {}, "non_turn_shaped": 0})
    monkeypatch.setattr(purge.census_mod, "census", lambda *a, **k: report)


def _write(tmp_path, name: str, obj) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(obj, default=str), encoding="utf-8")
    return str(path)


def _cli_execute(tmp_path, monkeypatch, *, report, plan=None, health=None,
                 proof=None, approval=None, note="test target",
                 digest=None, journal_lines=1, health_url=None,
                 record_dsn: str = _DSN, health_json: bool = False):
    _patch_live_census(monkeypatch, report)
    called: list[dict] = []
    monkeypatch.setattr(purge, "_execute",
                        lambda p, a, t: (called.append(p), 0)[1])
    journal = tmp_path / "journal.jsonl"
    if journal_lines:
        journal.write_text('{"type": "PointAdded"}\n', encoding="utf-8")
    if plan is None:
        plan = _plan([_clean_with_ids("s1", ["s1_t0"])],
                     journal_path=str(journal))
    argv = [*_TARGET_ARGV, "--target-note", note, "--execute",
            "--record-dsn", record_dsn,
            "--event-log-path", str(journal),
            "--plan", _write(tmp_path, "plan.json", plan),
            "--confirm-plan-digest", plan["plan_digest"] if digest is None
            else digest]
    if health_url:
        argv += ["--health-url", health_url]
    elif health_json:
        argv += ["--health-json", _write(tmp_path, "health.json",
                                         _health() if health is None else health)]
    else:
        # The DEFAULT `--execute` path is the live read (`--health-url`): a
        # captured body cannot authorise a delete (#10483 review, P2-3), so the
        # wiring is driven the way an operator must drive it.
        def _fetch(url, **_kwargs):
            body = dict(_health() if health is None else health)
            body["read_at"] = _ts(1)
            return body

        monkeypatch.setattr(purge, "_fetch_health", _fetch)
        argv += ["--health-url", "https://tortoise.example/health"]
    argv += ["--restore-proof", _write(tmp_path, "proof.json",
                                       _proof() if proof is None else proof)]
    if approval is not False:
        argv += ["--approval-record", _write(
            tmp_path, "approval.json",
            _approval(plan["plan_digest"]) if approval is None else approval)]
    return purge.main(argv), called


def test_execute_wiring_refuses_a_not_armed_record_before_any_delete(
        tmp_path, monkeypatch, capsys):
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        health=_health(armed=False))
    assert rc == 2
    assert called == []                       # _execute was NEVER reached
    assert purge.REFUSE_RECORD_NOT_ARMED in capsys.readouterr().err


def test_execute_wiring_refuses_a_record_behind_target_before_any_delete(
        tmp_path, monkeypatch, capsys):
    ids = ["s1_t0", "s1_t1"]
    live = _report(failed=[_failed("s1", ids, [
        {"node": "s1_t1", "field": "key", "problem": "missing_in_record"}])])
    journal = tmp_path / "journal.jsonl"
    rc, called = _cli_execute(
        tmp_path, monkeypatch, report=live,
        plan=_plan([_clean_with_ids("s1", ids)], journal_path=str(journal)))
    assert rc == 2
    assert called == []
    assert purge.REFUSE_RECORD_BEHIND in capsys.readouterr().err


def test_execute_wiring_refuses_a_live_census_off_by_one_before_any_delete(
        tmp_path, monkeypatch, capsys):
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0", "s1_t1", "s1_t2"])]),
        plan=_plan([_clean_with_ids("s1", ["s1_t0", "s1_t1"])],
                   journal_path=str(tmp_path / "journal.jsonl")))
    assert rc == 2
    assert called == []
    err = capsys.readouterr().err
    assert purge.REFUSE_EXACT_SET_MISMATCH in err and "s1_t2" in err


def test_execute_wiring_refuses_target_drift_before_any_delete(
        tmp_path, monkeypatch, capsys):
    """The target-drift refusal was added by the previous commit with ZERO
    coverage — a refusal nobody tests is the defect this review is about."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])],
                 journal_path=str(tmp_path / "journal.jsonl"))
    plan["target"] = dict(plan["target"], uri_host="prod.example:6379")
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]), plan=plan)
    assert rc == 2
    assert called == []
    err = capsys.readouterr().err
    assert purge.REFUSE_TARGET_DRIFT in err and "uri_host" in err


def test_execute_wiring_reaches_the_delete_when_every_precondition_holds(
        tmp_path, monkeypatch):
    """The positive arm of the wiring, so the refusals above are not "a guard
    that always refuses"."""
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]))
    assert rc == 0
    assert len(called) == 1


# ── `_execute` itself: before the first DELETE ──────────────────────────

def test_execute_refuses_a_journal_that_does_not_exist(
        tmp_path, monkeypatch, capsys):
    """`EventLog.append` CREATES a missing path, so a typo silently starts a
    journal whose ordinals begin at 1 and whose removals the target's rebuild
    never reads (#10483, P1)."""
    setup = _exec_setup(tmp_path, monkeypatch, journal_lines=0)
    assert not Path(setup.args.event_log_path).exists()
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert not Path(setup.args.event_log_path).exists()   # NOT created
    assert setup.graph.statements("DETACH DELETE") == []
    assert purge.REFUSE_JOURNAL_UNUSABLE in capsys.readouterr().err


def test_execute_refuses_an_empty_journal(tmp_path, monkeypatch, capsys):
    journal = tmp_path / "journal.jsonl"
    journal.write_text("", encoding="utf-8")
    setup = _exec_setup(tmp_path, monkeypatch, journal_lines=0)
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    assert "EMPTY" in capsys.readouterr().err


def test_execute_refuses_a_journal_the_plan_did_not_name(
        tmp_path, monkeypatch, capsys):
    setup = _exec_setup(tmp_path, monkeypatch, plan_journal=None)
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    err = capsys.readouterr().err
    assert purge.REFUSE_JOURNAL_UNUSABLE in err and "names no journal" in err


def test_execute_refuses_a_different_journal_than_the_plan_names(
        tmp_path, monkeypatch, capsys):
    setup = _exec_setup(tmp_path, monkeypatch, plan_journal="other")
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    assert purge.REFUSE_JOURNAL_UNUSABLE in capsys.readouterr().err


def test_execute_refuses_when_no_journal_is_configured(
        tmp_path, monkeypatch, capsys):
    setup = _exec_setup(tmp_path, monkeypatch, journal_configured=False)
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    assert purge.REFUSE_NO_JOURNAL in capsys.readouterr().err


def test_execute_refuses_when_the_sdk_addresses_another_graph(
        tmp_path, monkeypatch, capsys):
    setup = _exec_setup(tmp_path, monkeypatch, sdk_graph="some-other-graph")
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    assert purge.REFUSE_TARGET_DRIFT in capsys.readouterr().err


def test_execute_hands_the_censused_graph_to_the_sdk(
        tmp_path, monkeypatch):
    """P1-A: `--execute` must TELL the SDK which graph to address.

    Without `graph_name=`, the SDK derives `uri_graph or "tortoise"`
    (`sdk.py::_derive_graph_name`), so a named-graph target compared two graphs
    the tool had itself put out of step: `target-drift` on every real run, and
    removals journaled into the fallback graph's projection. Pre-fix this
    refuses (rc 2, nothing journaled) and the SDK is handed `None`.
    """
    setup = _exec_setup(tmp_path, monkeypatch, sdk_graph=None)
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 0, "a named-graph target must not refuse its own alignment"
    assert setup.graph.sdk_graph_names == ["g"]
    assert setup.journaled == ["s1_t0"]


def test_execute_checks_the_alignment_when_no_graph_name_is_passed(
        tmp_path, monkeypatch, capsys):
    """P1-A: with `--graph-name` omitted the check used to be DEAD CODE
    (`if args.graph_name and ...`) — the one case where the SDK's derivation and
    the census projection's derivation are free to diverge."""
    setup = _exec_setup(tmp_path, monkeypatch, graph_name=None,
                        sdk_graph="some-other-graph")
    assert setup.args.graph_name is None
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    assert purge.REFUSE_TARGET_DRIFT in capsys.readouterr().err


# ── the TOCTOU gate: the record is re-read immediately before the delete ──

def test_execute_refuses_when_the_record_lost_a_row_before_the_delete(
        tmp_path, monkeypatch, capsys):
    """P0: the record read in `main` can be minutes stale by the time session
    k's DELETE runs. A row lost in that window makes RECORD-BEHIND invisible and
    the graph copy the only copy — so no DELETE may be issued."""
    setup = _exec_setup(tmp_path, monkeypatch, ids=("s1_t0", "s1_t1"),
                        record_keys={"s1_t0": _record_row("s1_t0")})
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    assert setup.journaled == []
    err = capsys.readouterr().err
    assert purge.REFUSE_RECORD_BEHIND in err
    assert "s1_t1" in err          # the id that is gone is NAMED
    assert setup.record_calls == [{
        "org_id": "org", "graph_id": "graph",
        "node_keys": ["s1_t0", "s1_t1"]}]


def test_execute_verifies_the_record_once_per_session_before_that_delete(
        tmp_path, monkeypatch):
    """Two sessions, two scoped record reads, each carrying only that session's
    ids — the gate is per session, not one check for the whole run."""
    journal = tmp_path / "journal.jsonl"
    journal.write_text("{}\n", encoding="utf-8")
    args = purge._build_parser().parse_args([
        "--db-path", str(tmp_path / "graph.db"), "--graph-name", "g",
        "--org-id", "org", "--graph-id", "graph",
        "--record-dsn", "postgresql://record",
        "--event-log-path", str(journal)])
    target = purge._target_from_args(args)
    plan = purge.build_plan(
        _report(clean=[_clean_with_ids("s1", ["s1_t0"]),
                       _clean_with_ids("s2", ["s2_t0"])]),
        target=target, health=_health(), restore_proof=_proof(session="s1"),
        target_note="t", recovery_missing=[], journal_path=str(journal))
    graph, journaled = _patch_store(monkeypatch, sdk_graph="g")
    calls = _patch_record(monkeypatch, {tid: _record_row(tid)
                                        for tid in ("s1_t0", "s2_t0")})
    rc = purge._execute(plan, args, target)
    assert rc == 0
    assert [c["node_keys"] for c in calls] == [["s1_t0"], ["s2_t0"]]
    assert isinstance(graph, _FakeGraph) and journaled == ["s1_t0", "s2_t0"]


def test_execute_stops_before_the_session_whose_record_row_is_gone(
        tmp_path, monkeypatch, capsys):
    """Session 1 is verified and deleted; session 2's row is gone, so the run
    stops there and says that session 1's deletes already happened."""
    journal = tmp_path / "journal.jsonl"
    journal.write_text("{}\n", encoding="utf-8")
    args = purge._build_parser().parse_args([
        "--db-path", str(tmp_path / "graph.db"), "--graph-name", "g",
        "--org-id", "org", "--graph-id", "graph",
        "--record-dsn", "postgresql://record",
        "--event-log-path", str(journal)])
    target = purge._target_from_args(args)
    plan = purge.build_plan(
        _report(clean=[_clean_with_ids("s1", ["s1_t0"]),
                       _clean_with_ids("s2", ["s2_t0"])]),
        target=target, health=_health(), restore_proof=_proof(session="s1"),
        target_note="t", recovery_missing=[], journal_path=str(journal))
    _patch_store(monkeypatch, sdk_graph="g")
    _patch_record(monkeypatch, {"s1_t0": _record_row("s1_t0")})
    rc = purge._execute(plan, args, target)
    assert rc == 2
    err = capsys.readouterr().err
    assert purge.REFUSE_RECORD_BEHIND in err and "s2" in err
    assert "1 node(s) were deleted and journaled BEFORE" in err


def test_execute_refuses_when_the_journal_cannot_carry_a_removal(
        tmp_path, monkeypatch, capsys):
    setup = _exec_setup(tmp_path, monkeypatch, journal_result=False)
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert purge.REFUSE_JOURNAL_UNCONFIRMED in capsys.readouterr().err


def test_execute_refuses_a_journal_result_that_OMITS_a_deleted_id(
        tmp_path, monkeypatch, capsys):
    """P3-2: the check iterated `outcomes.items()` — what the journal REPORTED
    — so an id it never mentioned was invisible, the totals still balanced, and
    the run exited 0 "purged" with an unjournaled removal on the books (a
    rebuild would resurrect it, the #3316 class).

    The delete lands (both ids go), so `purged` must NOT be printed.
    """
    setup = _exec_setup(tmp_path, monkeypatch, ids=("s1_t0", "s1_t1"),
                        journal_omit=("s1_t1",))
    rc = purge._execute(setup.plan, setup.args, setup.target)
    captured = capsys.readouterr()
    assert rc == 2
    assert purge.REFUSE_JOURNAL_UNCONFIRMED in captured.err
    assert "s1_t1" in captured.err          # the OMITTED id is named
    assert "purged     :" not in captured.out
    assert setup.journaled == ["s1_t0", "s1_t1"]


# ── a partial sweep is its OWN diagnosis, and "purged" is not printed ────

def test_execute_reports_a_partial_sweep_with_its_own_code(
        tmp_path, monkeypatch, capsys):
    """The shortfall shared `exact-set-mismatch` with the pre-delete drift, and
    printed `purged : N` BEFORE the refusal — so the log read as success."""
    setup = _exec_setup(tmp_path, monkeypatch, ids=("s1_t0", "s1_t1"),
                        survivors_after=("s1_t1",))
    rc = purge._execute(setup.plan, setup.args, setup.target)
    captured = capsys.readouterr()
    assert rc == 2
    assert purge.REFUSE_PARTIAL_SWEEP in captured.err
    assert "s1_t1" in captured.err                 # the survivor is NAMED
    assert "ALREADY HAPPENED" in captured.err
    assert "purged     :" not in captured.out      # no success line before it
    # The surviving id is never journaled: it still stands in the graph.
    assert setup.journaled == ["s1_t0"]


def test_a_full_sweep_prints_purged_and_exits_zero(tmp_path, monkeypatch,
                                                   capsys):
    setup = _exec_setup(tmp_path, monkeypatch, ids=("s1_t0", "s1_t1"),
                        survivors_after=())
    rc = purge._execute(setup.plan, setup.args, setup.target)
    captured = capsys.readouterr()
    assert rc == 0
    assert "purged     : 2 turn node(s)" in captured.out
    assert purge.REFUSE_PARTIAL_SWEEP not in captured.err


def test_execute_refuses_a_record_rewritten_after_the_census_before_the_delete(
        tmp_path, monkeypatch, capsys):
    """P2-1: the KEY was still present, so the old re-verify passed — and the
    delete removed the graph's copy of the OLD transcript while the record now
    served a DIFFERENT one, which exists nowhere else.

    Pre-fix this run DELETED (the membership read `tid not in held` is False for
    a rewritten row) and exited 0; `DETACH DELETE` == [] is what makes this
    test fail on that code rather than merely describing it.
    """
    setup = _exec_setup(
        tmp_path, monkeypatch,
        record_keys={"s1_t0": _record_row("s1_t0", content="redacted")},
        graph_fields={"s1_t0": {"content": "the original transcript"}})
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    assert setup.journaled == []
    err = capsys.readouterr().err
    assert purge.REFUSE_MISMATCH in err and "s1_t0" in err
    assert "rewritten AFTER the census" in err


def test_execute_refuses_a_GRAPH_node_rewritten_after_the_census(
        tmp_path, monkeypatch, capsys):
    """The other direction of P2-1: an `update_point` on the graph side moves
    the transcript while the record row stands still. Checking only the record
    cannot see it, so the re-read has to cover BOTH stores."""
    setup = _exec_setup(
        tmp_path, monkeypatch,
        record_keys={"s1_t0": _record_row("s1_t0",
                                          content="the original transcript")},
        graph_fields={"s1_t0": {"content": "a rewritten graph turn"}})
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    err = capsys.readouterr().err
    assert purge.REFUSE_MISMATCH in err and "s1_t0" in err


def test_execute_refuses_a_planned_turn_that_is_no_longer_comparable(
        tmp_path, monkeypatch, capsys):
    """A planned turn the narrowed re-read cannot compare at all — gone from the
    graph, un-flagged, re-wired or re-kinded — is an EXACT-SET divergence, not a
    parity pass: the set the DELETE would touch is not the approved set."""
    setup = _exec_setup(tmp_path, monkeypatch, graph_missing=("s1_t0",))
    rc = purge._execute(setup.plan, setup.args, setup.target)
    assert rc == 2
    assert setup.graph.statements("DETACH DELETE") == []
    err = capsys.readouterr().err
    assert purge.REFUSE_EXACT_SET_MISMATCH in err and "s1_t0" in err


def test_the_delete_declares_an_apply_first_write_lane(tmp_path, monkeypatch,
                                                      capsys):
    """§17.1 (#9124 / #9206): a journal-backed graph will not accept a mutating
    statement with no declared lane. `_execute` declared none, and at that
    moment the graph IS journal-backed (`sdk._get_proj()` bound the journal,
    which registered the `(store, graph)` pair), so two things followed:

      * today (`_HANDLE_GUARD_ENFORCES = False`) every session's `DETACH
        DELETE` was counted as an UNDECLARED handle write — a destructive tool
        silently joining the very §17.1 census that part 2 is sized from;
      * once part 2 arms, the same statement raises
        `DirectCacheWriteRefused`, uncaught, killing the destructive arm on its
        FIRST delete in front of an operator holding an approved plan.

    Pinned where a real handle decides: the ContextVar
    `_journal_write_authority` itself reads, sampled at the instant each
    statement is issued. `_apply_first_write_scope` — not
    `_live_write_scope` — is the honest lane here: the delete lands FIRST and
    the record describing it is appended AFTER, which is exactly
    `_journal_turn_removal`'s contract (#10483 review round 8, P2).
    """
    setup = _exec_setup(tmp_path, monkeypatch, ids=("s1_t0", "s1_t1"))
    rc = purge._execute(setup.plan, setup.args, setup.target)
    capsys.readouterr()
    assert rc == 0
    deletes = [lane for stmt, lane in setup.graph.lanes
               if "DETACH DELETE" in stmt]
    assert deletes, "the fake store saw no DELETE — this pin would be vacuous"
    assert all(lane for lane in deletes), (
        "the DETACH DELETE was issued with NO declared §17.1 write lane, so an "
        f"armed handle would refuse it: lanes={deletes!r}")
    assert all(lane.startswith("apply-first") for lane in deletes), deletes
    # The record the lane covers really was appended, in the same lane.
    assert setup.journaled == ["s1_t0", "s1_t1"]
    # NON-VACUITY: the probe answers `None` when no lane is open, so "every
    # delete saw a lane" is a measurement and not a constant.
    assert _lane_in_flight() is None


# ── #3175: a file that never held a census is not an empty census ────────

@pytest.mark.parametrize("payload", [
    {}, {"parity": {}}, {"parity": {"clean_sessions": [], "failed_sessions": []}},
    {"population": {}, "parity": {"clean_sessions": [], "failed_sessions": []},
     "dry_run": {"would_delete_ids": []}},            # no verdict
])
def test_cli_refuses_a_census_file_that_never_held_a_census(
        tmp_path, capsys, payload):
    census_path = _write(tmp_path, "census.json", payload)
    plan_path = tmp_path / "plan.json"
    rc = purge.main([*_TARGET_ARGV, "--target-note", "t", "--census-json",
                     census_path, "--plan-out", str(plan_path)])
    assert rc == 3
    assert not plan_path.exists()          # no signable plan was written
    err = capsys.readouterr().err
    assert purge.REFUSE_CENSUS_UNREADABLE in err


def test_cli_refuses_a_missing_census_file_with_a_named_refusal(
        tmp_path, capsys):
    """Documented as exit 3; it used to be an uncaught FileNotFoundError."""
    rc = purge.main([*_TARGET_ARGV, "--target-note", "t", "--census-json",
                     str(tmp_path / "nope.json")])
    assert rc == 3
    assert purge.REFUSE_CENSUS_UNREADABLE in capsys.readouterr().err


def test_the_plan_records_where_its_population_came_from(tmp_path, capsys):
    """P3-4: the plan summarised a census without saying which census.

    With `--census-json` a plan can name `target: prod-A` while its population
    was read from dev-B. Not a deletion path (`--execute` re-derives live and
    refuses a cached census outright), but the artefact a HUMAN approves has to
    be honest about its source.
    """
    report = _report(clean=[_clean_with_ids("s1", ["s1_t0"])])
    census = _write(tmp_path, "census.json", report)
    plan_path = tmp_path / "plan.json"
    assert purge.main([*_TARGET_ARGV, "--target-note", "t", "--census-json",
                       census, "--plan-out", str(plan_path)]) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["census"]["source"]["kind"] == "census-json"
    assert plan["census"]["source"]["path"] == census
    assert "FILE" in capsys.readouterr().out          # and the PREVIEW says so


def test_the_plan_records_a_LIVE_read_as_its_source(tmp_path, monkeypatch,
                                                    capsys):
    """The mirror: a plan whose population came from the stores says that, so
    the field is a measurement and not a constant."""
    _patch_live_census(monkeypatch,
                       _report(clean=[_clean_with_ids("s1", ["s1_t0"])]))
    plan_path = tmp_path / "plan.json"
    assert purge.main([*_TARGET_ARGV, "--target-note", "t", "--record-dsn",
                       _DSN, "--plan-out", str(plan_path)]) == 0
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["census"]["source"]["kind"] == "live-read"
    assert plan["census"]["source"]["uri_host"] == "127.0.0.1:1"
    assert "FILE" not in capsys.readouterr().out


# ── a malformed-but-parseable plan is a refusal, not a traceback ─────────

def test_execute_refuses_a_plan_entry_without_ids(
        tmp_path, monkeypatch, capsys):
    """It used to reach `plan_digest` and escape `main` as a `KeyError`
    traceback — indistinguishable from a crash, with the documented refusal
    never printed (#10483 review, P1)."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    del plan["purge"]["sessions"][0]["ids"]
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]), plan=plan)
    assert rc == 2
    assert called == []
    err = capsys.readouterr().err
    assert purge.REFUSE_PLAN_UNREADABLE in err and "ids" in err
    assert "Traceback" not in err


def test_execute_refuses_a_plan_entry_that_names_no_ids(tmp_path, monkeypatch,
                                                       capsys):
    """P2-2: an entry with `ids: []` is DIGEST-INVISIBLE and DELETE-INVISIBLE,
    and it satisfies the restore proof for a session the run never purges.

    The evidence that the entry really is invisible is asserted first (the
    digest is byte-identical with and without it, so no live-vs-approved
    comparison can ever see it); then the ONLY defence there is — a plan-shape
    refusal — is asserted, by effect, through `main`.
    """
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    ghost = {"session": "never-purged", "class": "PARITY",
             "graph_turn_nodes": 0, "record_turn_rows": 0, "ids": []}
    before = purge.plan_digest(plan["purge"]["sessions"])
    plan["purge"]["sessions"].append(ghost)
    plan["purge"]["session_count"] += 1
    # The approved token does NOT move: the ghost contributes no pairs.
    assert purge.plan_digest(plan["purge"]["sessions"]) == before
    assert before == plan["plan_digest"]
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]), plan=plan,
        # …and a proof naming the ghost session is exactly what precondition 3
        # must NOT accept: nothing is ever restored from it.
        proof=_proof(session="never-purged"))
    assert rc == 2
    assert called == []
    err = capsys.readouterr().err
    assert purge.REFUSE_PLAN_UNREADABLE in err and "never-purged" in err
    assert "empty id list" in err


def test_execute_refuses_a_plan_that_names_no_session(tmp_path, monkeypatch,
                                                     capsys):
    """The degenerate member of P2-2: with no entries the loop runs zero
    times and `deleted_total == planned_total == 0`, i.e. a run that purges
    nothing would report a full success (`purged : 0 turn node(s)`)."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    plan["purge"]["sessions"] = []
    plan["purge"]["session_count"] = 0
    plan["purge"]["turn_node_total"] = 0
    plan["plan_digest"] = purge.plan_digest([])
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]), plan=plan)
    assert rc == 2
    assert called == []
    err = capsys.readouterr().err
    assert purge.REFUSE_PLAN_UNREADABLE in err and "purges nothing" in err


@pytest.mark.parametrize("mutate, needle", [
    (lambda p: p["purge"].__setitem__("turn_node_total", "many"),
     "turn_node_total"),
    (lambda p: p["purge"]["sessions"][0].__setitem__("graph_turn_nodes",
                                                      ["1"]),
     "graph_turn_nodes"),
    (lambda p: p["purge"].__setitem__("session_count", 1.5),
     "session_count"),
])
def test_a_plan_with_an_unreadable_count_is_named_not_a_traceback(
        mutate, needle):
    """P3-1: `int("many")` / `int([...])` escaped the gate as a `ValueError`
    traceback — exit 1 ("usage error") where the module promises the guard's
    refusals are NAMES. Every one of these used to reach `int(...)` below
    `_plan_problem`."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    mutate(plan)
    code, message = _gate(plan)
    assert code == purge.REFUSE_PLAN_UNREADABLE, message
    assert needle in message


def test_a_live_census_whose_counts_disagree_is_a_named_refusal():
    """P3-1: `classify_sessions` raises when a session's count and its id list
    diverge. The PREVIEW caught it (`except (ValueError, KeyError)`); the
    destructive arm did not, so a live census that could not be turned into a
    set reached a traceback instead of a refusal."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    diverging = _report(clean=[{"session": "s1", "graph_turn_nodes": 3,
                               "record_turn_rows": 3, "counts_equal": True,
                               "ids": ["s1_t0"]}])
    code, message = _gate(plan, live=diverging)
    assert code == purge.REFUSE_CENSUS_UNREADABLE, message
    assert "purge set" in message


def test_the_destructive_gate_refuses_a_missing_live_census():
    """P3-3: this branch is the hinge of "execute re-verifies nothing" — with
    no live report, the approved set cannot be re-derived at all — and it had
    no test. An unverifiable set is not an approved set."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=None)
    assert code == purge.REFUSE_EXACT_SET_MISMATCH, message
    assert "no report" in message


def test_the_destructive_gate_refuses_a_live_census_it_cannot_read():
    """P3-3: `REFUSE_CENSUS_UNREADABLE` was asserted only on PREVIEW paths.
    An empty `{}` live report reaching `--execute` must refuse as a census it
    cannot read — not as an empty set it can."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live={})
    assert code == purge.REFUSE_CENSUS_UNREADABLE, message
    assert "population" in message



# ── the approval is OUT OF BAND: a plan cannot approve itself ────────────

def test_execute_refuses_without_an_out_of_band_approval(
        tmp_path, monkeypatch, capsys):
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        approval=False)
    assert rc == 2
    assert called == []
    assert purge.REFUSE_NO_APPROVAL in capsys.readouterr().err


def test_execute_refuses_an_approval_for_another_digest(
        tmp_path, monkeypatch, capsys):
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        approval=_approval("0" * 64))
    assert rc == 2
    assert called == []
    assert purge.REFUSE_CONFIRMATION_MISMATCH in capsys.readouterr().err


def test_an_edited_plan_cannot_borrow_the_approval_of_the_original():
    """The two fields of one file are rewritten together: `purge.sessions` and
    `plan_digest` both change, so `--confirm-plan-digest` still matches. Only an
    approval recorded OUTSIDE the plan can tell the difference."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    edited = json.loads(json.dumps(plan))
    edited["purge"]["sessions"] = [{"session": "s1", "ids": ["s1_t0", "s1_t9"],
                                    "graph_turn_nodes": 2}]
    edited["purge"]["turn_node_total"] = 2
    edited["plan_digest"] = purge.plan_digest(edited["purge"]["sessions"])
    live = _report(clean=[_clean_with_ids("s1", ["s1_t0", "s1_t9"])])
    # The gate's own self-consistency check passes on the edited file…
    code, _ = purge.evaluate_gate(
        plan=edited, live_report=live, health=_health(),
        restore_proof=_proof(), approval=_approval(plan["plan_digest"]),
        confirm_digest=edited["plan_digest"], target_note="test target",
        live_target=edited["target"], health_fetched=True)
    assert code == purge.REFUSE_CONFIRMATION_MISMATCH      # …the approval does not


# ── evidence has an AGE and an ADDRESS ──────────────────────────────────

@pytest.mark.parametrize("age,code", [
    (25 * 3600, "armed-evidence-stale"),              # beyond the 24h bound
    (60, ""),                                          # inside it
])
def test_armed_evidence_is_bounded_in_time(age, code):
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    got, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), health=_health(age=age))
    assert got == code, message


def test_armed_evidence_from_another_tenant_is_not_evidence_for_this_one():
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    foreign = {"record": {"armed": True, "dsn": _FINGERPRINT,
                          "org_id": "someone-else", "graph_id": "graph",
                          "fetched_at": _ts(60)}}
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), health=foreign)
    assert code == purge.REFUSE_TARGET_DRIFT
    assert "someone-else" in message


def test_armed_evidence_without_a_read_time_cannot_be_dated():
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    undated = {"record": {"armed": True, "dsn": _FINGERPRINT}}
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), health=undated)
    assert code == purge.REFUSE_ARMED_EVIDENCE_STALE
    assert "fetched_at" in message and "--health-url" in message


def test_a_plan_without_armed_evidence_cannot_authorise_a_delete():
    """A plan generated with no /health artefact was approved over an unarmed
    record; a fresher /health today does not repair that."""
    report = _report(clean=[_clean_with_ids("s1", ["s1_t0"])])
    plan = purge.build_plan(report, target=_target(), health=None,
                            restore_proof=_proof(), target_note="test target",
                            recovery_missing=[])
    code, message = _gate(plan, live=report)
    assert code == purge.REFUSE_NO_ARMED_EVIDENCE
    assert "armed evidence" in message


@pytest.mark.parametrize("overrides,fragment", [
    ({"age": 8 * 86400}, "day(s) ago"),        # beyond the 7-day bound
    ({"org": "other-org"}, "DIFFERENT deployment"),
    ({"graph_id": "other-graph"}, "DIFFERENT deployment"),
    ({"graph": "other-graph"}, "DIFFERENT deployment"),
])
def test_a_restore_proof_must_describe_this_deployment_and_be_recent(
        overrides, fragment):
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    proof = _proof(**overrides)
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), proof=proof)
    assert code == purge.REFUSE_RESTORE_PROOF_STALE
    assert fragment in message


# ── the docstring's flags are the parser's flags ───────────────────────

#: Named in the docstring but NOT flags of THIS parser: `--dry-run` is named only
#: to say it does not exist, and `--drill` belongs to
#: `python -m tortoise record-backup`, quoted as the recovery recipe.
_NON_FLAGS = {"--dry-run", "--drill"}


def test_every_flag_the_docstring_names_exists_in_the_parser():
    """The module documented `--dry-run` (no such flag) and "Required:
    --event-log-path" against an optional argument, so a reader following the
    docstring got an argparse usage error (#10483 review, P2)."""
    parser = purge._build_parser()
    known = {opt for action in parser._actions for opt in action.option_strings}
    named = {f for f in re.findall(r"--[a-z][a-z-]+", purge.__doc__ or "")
             if f not in _NON_FLAGS}
    assert named <= known, named - known
    assert "no `--dry-run` flag" in (purge.__doc__ or "")


# ── round 4: the ARMED evidence must be satisfiable by the REAL /health ──
#
# The guard demanded `org_id`, `graph_id` and `recorded_at` INSIDE the body.
# `_record_health()` (tortoise/hosted_api.py) emits none of the first two — a
# multi-tenant process does not know the tenant — and published no read
# instant, so every real body was refused and the only passing input was a
# hand-written file. The ADDRESS is `record.dsn` (the fingerprint of the record
# DSN the deployment is armed with) and the read instant is the endpoint's own
# `fetched_at` or this run's `read_at`.

def test_a_real_health_body_satisfies_the_armed_precondition():
    """The body the deployment actually serves must PASS — the defect was that
    it could not, which left hand-authoring as the only passing input."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    body = _health()
    # The shape the endpoint emits: armed + the DSN fingerprint + fetched_at,
    # and NO tenant (a multi-tenant deployment serves several).
    assert body["record"]["armed"] is True
    assert body["record"]["dsn"] == _FINGERPRINT
    assert "org_id" not in body["record"] and "graph_id" not in body["record"]
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), health=body)
    assert code == "", message
    assert "every precondition asserted" in message


def test_armed_evidence_is_bound_to_the_record_this_run_reads():
    """The address is the RECORD, not the tenant: two records can serve one
    graph, so a body armed with another DSN proves nothing about this one."""
    other_fingerprint = hashlib.sha256(b"postgresql://other").hexdigest()[:8]
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]),
        health=_health(fingerprint=other_fingerprint))
    assert code == purge.REFUSE_TARGET_DRIFT
    assert other_fingerprint in message and _FINGERPRINT in message


def test_an_armed_body_that_names_no_record_fingerprint_is_not_evidence():
    """The invented shape (tenant + armed, no `dsn`) is exactly what an
    operator had to write by hand to get past the old check."""
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    invented = {"record": {"armed": True, "org_id": "org", "graph_id": "graph",
                           "fetched_at": _ts(60)}}
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), health=invented)
    assert code == purge.REFUSE_RECORD_NOT_ARMED
    assert "record.dsn" in message


def test_the_live_health_read_stamps_its_own_instant_and_drops_credentials(
        monkeypatch):
    """`--health-url` makes the arming fact and its AGE a measurement: the read
    instant is ours, and the URL's userinfo never reaches the plan artefact
    (the same credential leak the target block already refuses)."""
    payload = _health()
    del payload["record"]["fetched_at"]        # nothing from the producer

    class _Response:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, timeout=None: _Response())
    body = purge._fetch_health("https://user:SEKRIT@tortoise.example/health")

    assert purge._age_seconds(body["read_at"]) is not None   # stamped NOW
    assert body["health_url"] == "tortoise.example"
    assert "SEKRIT" not in json.dumps(body)


def test_execute_wiring_reads_the_health_from_the_deployment_when_given_a_url(
        tmp_path, monkeypatch):
    """The strong path through `main`: no operator-authored file at all."""
    seen: list[str] = []

    def _fetch(url, **_kwargs):
        seen.append(url)
        body = _health()
        del body["record"]["fetched_at"]      # the live read supplies the age
        body["read_at"] = _ts(1)
        return body

    monkeypatch.setattr(purge, "_fetch_health", _fetch)
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        health_url="https://tortoise.example/health")
    assert rc == 0
    assert len(called) == 1
    assert seen == ["https://tortoise.example/health"]


def test_a_health_url_that_cannot_be_read_is_exit_three_not_a_refusal(
        tmp_path, monkeypatch, capsys):
    """An unreadable deployment is NOT "unarmed": that would be a different,
    false fact. Exit 3, the unreadable-side code."""
    def _boom(url, **_kwargs):
        raise purge._HealthUnreadable("URLError: connection refused")

    monkeypatch.setattr(purge, "_fetch_health", _boom)
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        health_url="https://down.example/health")
    assert rc == 3
    assert called == []                      # nothing was ever deleted
    assert "ARMED" in capsys.readouterr().err


def test_a_captured_health_body_cannot_authorise_a_delete(tmp_path, monkeypatch,
                                                        capsys):
    """P2-3: `main` could not tell a live `/health` read from a FILE, and the
    file's own `fetched_at` is whatever its author wrote — so a hand-written
    `{"record": {"armed": true, "dsn": "<fp>"}}`, or a capture up to 24 h
    old, satisfied `--execute`'s arming precondition for a deployment that is
    UNARMED now.

    A CAPTURE still produces a plan (it deletes nothing): the preview arm below
    runs the same body through `main` and exits 0.
    """
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        health_json=True)
    assert rc == 2
    assert called == []                      # `_execute` was NEVER reached
    err = capsys.readouterr().err
    assert purge.REFUSE_ARMED_EVIDENCE_STALE in err
    assert "--health-url" in err

    # …and the same captured body is a legitimate PREVIEW input.
    census = _write(tmp_path, "c.json",
                    _report(clean=[_clean_with_ids("s1", ["s1_t0"])]))
    health = _write(tmp_path, "h.json", _health())
    assert purge.main([*_TARGET_ARGV, "--target-note", "t",
                       "--census-json", census, "--health-json", health,
                       "--plan-out", str(tmp_path / "plan.json")]) == 0


def test_the_gate_asks_for_a_measured_arming_fact_and_has_no_default():
    """A guard that is opt-out by omission is not a guard: `health_fetched`
    must be a REQUIRED keyword so a caller cannot forget it into a pass."""
    import inspect

    params = inspect.signature(purge.evaluate_gate).parameters
    assert "health_fetched" in params
    assert params["health_fetched"].default is inspect.Parameter.empty
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), health_fetched=False)
    assert code == purge.REFUSE_ARMED_EVIDENCE_STALE
    assert "FILE" in message


def test_both_health_sources_at_once_is_a_usage_error(tmp_path, capsys):
    """Two sources for one fact: refuse rather than silently pick one."""
    argv = [*_TARGET_ARGV, "--target-note", "t",
            "--census-json", _write(tmp_path, "c.json", _report()),
            "--health-json", _write(tmp_path, "h.json", _health()),
            "--health-url", "https://tortoise.example/health"]
    assert purge.main(argv) == 1
    assert "not both" in capsys.readouterr().err


def test_the_preview_closing_line_names_every_flag_execute_requires(tmp_path,
                                                                   capsys):
    """An operator following the preview's own last line was refused
    `no-approval`: `--approval-record` was named nowhere in the output
    (#10483 review round 4, P2)."""
    rc = purge.main([*_TARGET_ARGV, "--target-note", "t",
                     "--census-json", _write(tmp_path, "c.json", _report()),
                     "--plan-out", str(tmp_path / "plan.json")])
    assert rc == 0
    out = capsys.readouterr().out
    closing = [line for line in out.splitlines() if line.startswith("dry run")]
    assert closing, out
    for flag in ("--execute", "--approval-record", "--health-url",
                 "--restore-proof"):
        assert flag in closing[0], (flag, closing[0])


def test_the_tool_does_not_claim_event_log_path_is_required_at_preview(
        tmp_path, capsys):
    """P3 (#10483 review round 7): the docstring said `--event-log-path` "is
    REQUIRED here" (at preview) and the parser help said "Required at PREVIEW
    time too", while the preview path enforces nothing — a claim-vs-artefact
    mismatch of the same shape as the phantom `--dry-run`.

    The prose now states what the code does: preview without the flag SUCCEEDS
    and records `journal: null`, and every `--execute` of that plan refuses.
    """
    claims = " ".join([purge.__doc__ or "",
                       next((a.help or "") for a in purge._build_parser()._actions
                            if a.dest == "event_log_path")])
    assert "REQUIRED here" not in claims
    assert "Required at PREVIEW time too" not in claims
    # ...and the behaviour the reworded prose describes is the real one.
    rc = purge.main([*_TARGET_ARGV, "--target-note", "t",
                     "--census-json", _write(tmp_path, "c.json", _report()),
                     "--plan-out", str(tmp_path / "plan.json")])
    assert rc == 0
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert plan["journal"]["path"] is None
    assert any("no --event-log-path" in m for m in plan["recovery"]["missing"])
    assert "--event-log-path" in capsys.readouterr().out


def test_the_tool_does_not_claim_target_note_is_required_at_preview(
        tmp_path, capsys):
    """P3 (#10483 review round 8): the parser help said `--target-note`
    "REQUIRED", the preview enforced nothing, and a plan written without it
    records `target_note: ""` — which `evaluate_gate`'s FIRST branch refuses
    outright, so the artefact was dead on arrival from the moment it was
    written, and nothing in the preview output said so. Same claim-vs-artefact
    shape as the `--event-log-path` P3 round 7 fixed, and the same direction of
    fix: state what is true, and NAME the gap in the plan's own MISSING list and
    in the closing guidance."""
    claims = " ".join([purge.__doc__ or "",
                       next((a.help or "")
                            for a in purge._build_parser()._actions
                            if a.dest == "target_note")])
    assert "REQUIRED: name the deployment" not in claims
    assert "Required at PREVIEW time" not in claims
    # …and the behaviour the reworded prose describes is the real one.
    rc = purge.main([*_TARGET_ARGV,
                     "--census-json", _write(tmp_path, "c.json", _report()),
                     "--plan-out", str(tmp_path / "plan.json")])
    assert rc == 0
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert plan["target_note"] == ""
    assert any("no --target-note" in m for m in plan["recovery"]["missing"])
    out = capsys.readouterr().out
    assert "MISSING   no --target-note" in out, out
    closing = [line for line in out.splitlines() if line.startswith("dry run")]
    assert closing and "--target-note" in closing[0], closing
    # …and the refusal the preview predicts is the one `--execute` really gives.
    code, _ = purge.evaluate_gate(
        plan=plan, live_report=_report(), health=_health(),
        restore_proof=_proof(), approval=_approval(plan["plan_digest"]),
        confirm_digest=plan["plan_digest"], target_note="",
        live_target=plan["target"], health_fetched=True, record_dsn=_DSN)
    assert code == purge.REFUSE_NO_TARGET_NOTE


# ── round 5: the ARMED evidence's own inputs ──────────────────────────────
#
# Three defects in the `_armed_evidence_problem` family, all of the "the guard
# refuses real evidence / accepts invented evidence" class that round 4 named:
#
#   1. the fingerprint hashed the RAW DSN while `/health` publishes the
#      fingerprint of the STRIPPED one, so a correctly-armed run was refused
#      `target-drift` with a message asserting the operator was wrong;
#   2. `_health_armed` accepted a flattened body the address reader could not
#      read, so a PREVIEW certified an arming fact `--execute` then rejected;
#   3. `None` from `_record_fingerprint` meant both "no DSN" and "the seam could
#      not answer", and the gate read either as "skip the record binding".

def test_a_padded_record_dsn_names_the_same_record():
    """`/health` fingerprints the STRIPPED DSN (`record_dsn()`'s `.strip()`),
    so the guard must compare like with like. libpq reads a conninfo string as
    settings separated by whitespace: a trailing newline from a secret file or
    a `fly secret` is not a different record, and refusing it told a correctly
    armed operator to "pass the DSN the deployment is armed with" — which they
    had (#10483 review round 5, P2)."""
    assert purge._record_fingerprint(_DSN + "\n") == _FINGERPRINT
    assert purge._record_fingerprint(" " + _DSN + "  ") == _FINGERPRINT
    # An EMPTY DSN is still "nothing to compare" — not a fingerprint failure.
    assert purge._record_fingerprint("") is None


def test_a_padded_record_dsn_passes_the_gate_instead_of_reporting_target_drift():
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), record_dsn=_DSN + "\n")
    assert code == "", message
    assert "every precondition asserted" in message


def test_a_padded_record_dsn_does_not_stop_execute_at_the_wiring(
        tmp_path, monkeypatch):
    """The same fact through `main`: no delete is refused for a trailing
    newline, and a node really is deleted (the positive arm)."""
    rc, called = _cli_execute(
        tmp_path, monkeypatch,
        report=_report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        record_dsn=_DSN + "\n")
    assert rc == 0
    assert len(called) == 1


def test_a_flattened_health_body_is_readable_by_every_reader():
    """`_health_armed` accepts `{"record.armed": true}`; the ADDRESS reader did
    not, so the gate refused a body it had just called armed, with the false
    statement "does not state record.dsn" (#10483 review round 5, P2)."""
    flat = {"record.armed": True, "record.dsn": _FINGERPRINT,
            "fetched_at": _ts(60)}
    assert purge._health_armed(flat) is True
    assert purge._evidence_address(flat)["record_dsn_fingerprint"] == _FINGERPRINT
    assert purge._read_instant(flat) == flat["fetched_at"]
    code, message = purge._armed_evidence_problem(
        flat, _target(), record_dsn=_DSN)
    assert (code, message) == ("", "")


def test_a_flattened_health_body_passes_the_gate():
    plan = _plan([_clean_with_ids("s1", ["s1_t0"])])
    flat = {"record.armed": True, "record.dsn": _FINGERPRINT,
            "fetched_at": _ts(60)}
    code, message = _gate(plan, live=_report(
        clean=[_clean_with_ids("s1", ["s1_t0"])]), health=flat)
    assert code == "", message


def test_a_flattened_body_that_names_no_record_is_refused_in_both_shapes():
    """The tolerance must not become a hole: neither shape may pass without an
    address, and both must refuse identically."""
    for body in ({"record.armed": True, "fetched_at": _ts(60)},
                 {"record": {"armed": True, "fetched_at": _ts(60)}}):
        assert purge._health_armed(body) is True
        assert purge._armed_evidence_gap(body), body
        code, _ = purge._armed_evidence_problem(
            body, _target(), record_dsn=_DSN)
        assert code == purge.REFUSE_RECORD_NOT_ARMED


def _arming_missing(health, *, health_url: bool = True) -> list[str]:
    """The preview's arming entries, isolated from the two unconditional notes
    `_recovery_missing` always adds (record-backup drill, reverse graph path).

    `health_url` says HOW the body reached the run — the live read
    (`--health-url`, the only source `--execute` accepts) or a capture. The
    source is part of the arming question now, so the argv has to state it.
    """
    argv = [*_TARGET_ARGV, "--target-note", "t", "--event-log-path", "j.jsonl"]
    if health_url:
        argv += ["--health-url", "https://tortoise.example/health"]
    missing = purge._recovery_missing(
        purge._build_parser().parse_args(argv),
        _proof(), health, _report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        _target())
    return [m for m in missing if "ARMED" in m]


def _proof_missing(proof) -> list[str]:
    """The preview's RESTORE-PROOF entries (precondition 3), isolated the same
    way — the other entries are about arming, the journal and the recovery
    drill, none of which this pin is about."""
    missing = purge._recovery_missing(
        purge._build_parser().parse_args(
            [*_TARGET_ARGV, "--target-note", "t", "--event-log-path", "j.jsonl"]),
        proof, _health(), _report(clean=[_clean_with_ids("s1", ["s1_t0"])]),
        _target())
    return [m for m in missing if "precondition 3" in m]


def test_the_preview_and_the_execute_gate_ask_the_same_arming_question():
    """A preview that certifies a precondition `--execute` rejects is worse than
    no preview: the operator produces the artefact the tool asked for, signs the
    digest, and only then learns the plan can never authorise anything (#10483
    review round 5, P2). Every body is judged by BOTH arms and must agree."""
    bodies = [
        ("real", _health()),
        ("flattened", {"record.armed": True, "record.dsn": _FINGERPRINT,
                       "fetched_at": _ts(60)}),
        ("armed-no-dsn", {"record": {"armed": True, "fetched_at": _ts(60)}}),
        ("flattened-no-dsn", {"record.armed": True, "fetched_at": _ts(60)}),
        ("unarmed", _health(armed=False)),
        ("absent", None),
    ]
    for name, body in bodies:
        preview_gap = bool(_arming_missing(body))
        code, _ = purge._armed_evidence_problem(
            body, _target(), record_dsn=_DSN)
        assert preview_gap == bool(code), (name, preview_gap, code)


def test_an_armed_body_without_a_record_fingerprint_is_named_by_the_preview():
    """The specific case round 5 named: a preview over this body printed
    RECOVERY-PATH-STATED and exited 0 while `--execute` refused it."""
    body = {"record": {"armed": True, "fetched_at": _ts(60)}}
    arming = _arming_missing(body)
    assert arming, "the preview must not certify an arming fact it cannot use"
    assert "record.dsn" in arming[0]


def test_a_real_health_body_is_not_named_by_the_preview():
    """The positive arm: the fix must not turn every preview red."""
    assert _arming_missing(_health()) == []


def test_the_preview_and_the_gate_agree_that_a_FILE_is_not_arming_evidence():
    """The SOURCE half of the agreement (P2-3). `--execute` refuses a captured
    body outright; a preview that printed RECOVERY-PATH-STATED over the same
    body would certify the very artefact the destructive arm rejects — the
    divergence rounds 5 and 8 closed for the body's CONTENT."""
    named = _arming_missing(_health(), health_url=False)
    assert named, "the preview must not certify a captured arming fact"
    assert "--health-url" in named[0]
    # …and the live source of the SAME body is clean, so this is a measurement
    # of the source and not a constant.
    assert _arming_missing(_health(), health_url=True) == []


# ── round 8: the SAME agreement question, for the SECOND evidence artefact ──
#
# Round 5 shared one function between the two arms for `/health` and left the
# restore proof judged by bare truthiness, so the preview certified
# precondition 3 for any non-empty JSON the destructive arm then refused.

def test_the_preview_and_the_execute_gate_ask_the_same_restore_proof_question():
    """Every proof body is judged by BOTH arms and the two must agree (#10483
    review round 8, P2). `_execute`'s arm applies nine requirements
    (`_restore_proof_problem`); the preview applied none beyond truthiness, so
    `{"garbage": 1}` produced an APPROVABLE plan reading
    `recovery : RECOVERY-PATH-STATED` with the `no --restore-proof` line gone —
    and then `--execute` refused that exact artefact."""
    clean = [_clean_with_ids("s1", ["s1_t0"])]
    plan = _plan(clean)
    bodies = [
        ("real", _proof()),
        ("absent", None),
        ("garbage", {"garbage": 1}),
        ("empty-object", {}),
        ("not-served-from-record", {**_proof(), "served_from_record": False}),
        ("session-not-in-plan", _proof(session="s9")),
        ("eight-days-old", _proof(age=8 * 86400)),
        ("unparseable-age", {**_proof(), "recorded_at": "yesterday"}),
        ("wrong-tenant", _proof(org="other")),
    ]
    for name, body in bodies:
        preview_gap = bool(_proof_missing(body))
        code, _ = purge.evaluate_gate(
            plan=plan, live_report=_report(clean=clean), health=_health(),
            restore_proof=body, approval=_approval(plan["plan_digest"]),
            confirm_digest=plan["plan_digest"], target_note="test target",
            live_target=plan["target"], health_fetched=True,
            record_dsn=_DSN)
        assert preview_gap == bool(code), (name, preview_gap, code)
        # …and the agreement is not "both always refuse" / "both always pass".
        assert preview_gap == (name != "real"), (name, preview_gap)


def test_the_preview_names_a_restore_proof_the_execute_arm_would_refuse():
    """The specific case round 8 named, asserted on its own so the message the
    operator reads is pinned, not only the boolean."""
    missing = _proof_missing({"garbage": 1})
    assert missing, "the preview must not certify a proof `--execute` rejects"
    assert purge.REFUSE_RESTORE_PROOF_STALE in missing[0]
    assert "scratch_session" in missing[0]


def test_a_real_restore_proof_is_not_named_by_the_preview():
    """Positive arm: the fix must not turn every preview red."""
    assert _proof_missing(_proof()) == []


def test_an_unfingerprintable_record_dsn_is_refused_not_skipped(monkeypatch):
    """`None` used to mean BOTH "no DSN" and "the seam could not answer", and the
    gate read either as "skip the record binding" — so a broken record seam
    silently disabled the check that binds the arming evidence to the record
    (#10483 review round 5, P2). An unanswered question is not a passed one."""
    def _boom(dsn):
        raise RuntimeError("record seam unavailable")

    monkeypatch.setattr("tortoise.record_writer.dsn_fingerprint", _boom)
    with pytest.raises(purge._FingerprintUnavailable):
        purge._record_fingerprint(_DSN)
    code, message = purge._armed_evidence_problem(
        _health(), _target(), record_dsn=_DSN)
    assert code == purge.REFUSE_RECORD_NOT_ARMED
    assert "could not fingerprint" in message


def test_no_record_dsn_at_all_still_skips_the_binding():
    """Case (a) is legitimate and must stay: `_execute` refuses a missing DSN on
    its own (exit 3), so the gate does not need to refuse it twice."""
    code, message = purge._armed_evidence_problem(
        _health(), _target(), record_dsn="")
    assert (code, message) == ("", "")
