"""The attestation-census CLASSIFIER: repair is separated from fabrication by rule, not by judgement.

Every case here is one measured shape from the 2026-11-03 `ai-review-gate` red sweep:

  * #10610/#10452 — a record bound to the LIVE head with a real verdict and evidence → `RESTORE`
    (the review happened; only its published copy is missing).
  * #10543/#10633/#10575 — a record bound to an OLDER head → `RE-REVIEW` (the code moved; re-posting
    the old verdict is the laundering the rule forbids).
  * #10648/#10504/#10546 — no record at all → `NOTHING-TO-RESTORE` (never infer a review from a green
    test run).
  * a `mint:"carried"` record → never restorable, whatever its verdict says (#10177 measured a carry
    re-binding an older `clean` onto a head whose own review found a P1).

The tool is a reader: these tests assert it never writes, mints or posts anything.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parent.parent / "tools" / "attest_census.py"


def _load():
    spec = importlib.util.spec_from_file_location("attest_census_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations through sys.modules[cls.__module__], so the
    # module must be REGISTERED before exec_module — otherwise every @dataclass in
    # the tool raises AttributeError at import time.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


ac = _load()

LIVE = "a" * 40
OLD = "b" * 40
GREEN = ("completed", "success")
RED = ("completed", "failure")


def rec(head=LIVE, mint="fresh", verdict="clean", evidence="comment:6593768046"):
    return ac.Record(path=Path("/nowhere.json"), head_sha=head, mint=mint, verdict=verdict, evidence=evidence)


# ── RESTORE: the only licensed re-post ───────────────────────────────────────


def test_live_head_with_real_evidence_is_restore_when_the_gate_is_red():
    outcome, why = ac.classify(LIVE, rec(), *RED)
    assert outcome == ac.RESTORE, why


def test_live_head_with_real_evidence_but_green_gate_is_not_a_restore():
    # Nothing to restore: the proof already reached the gate. Re-posting would dilute the evidence.
    assert ac.classify(LIVE, rec(), *GREEN)[0] == ac.OK


def test_an_in_flight_gate_is_not_resent():
    # ⛔ a queued check is not a green check, and it is not a reason to re-post either.
    assert ac.classify(LIVE, rec(), "queued", "")[0] == ac.IN_FLIGHT


# ── RE-REVIEW: the code moved ────────────────────────────────────────────────


def test_record_at_an_older_head_requires_re_review_never_a_repost():
    outcome, why = ac.classify(LIVE, rec(head=OLD), *RED)
    assert outcome == ac.RE_REVIEW
    assert OLD[:12] in why and LIVE[:12] in why


def test_a_carried_mint_is_never_evidence_of_this_head():
    # #10177: a carry points at ANOTHER review. Restoring it as proof of this head is laundering.
    outcome, why = ac.classify(LIVE, rec(mint="carried", evidence=None), *RED)
    assert outcome != ac.RESTORE
    assert "carried" in why


# ── NOTHING-TO-RESTORE: there is no artifact ─────────────────────────────────


def test_no_record_is_nothing_to_restore():
    assert ac.classify(LIVE, None, *RED)[0] == ac.NOTHING


def test_null_evidence_is_nothing_to_restore():
    outcome, why = ac.classify(LIVE, rec(evidence=None), *RED)
    assert outcome == ac.NOTHING
    assert "no evidence" in why


def test_a_non_clean_verdict_is_nothing_to_restore():
    outcome, why = ac.classify(LIVE, rec(verdict="issues"), *RED)
    assert outcome == ac.NOTHING
    assert "not a clean verdict" in why


def test_a_verdict_that_merely_contains_clean_is_not_clean():
    # "NOT CLEAN" contains "CLEAN"; the vocabulary must be an allow-list, not a substring test.
    assert ac.classify(LIVE, rec(verdict="NOT CLEAN"), *RED)[0] == ac.NOTHING


# ── the record-reader itself ─────────────────────────────────────────────────


def test_load_record_prefers_the_canonical_path_and_reads_every_field(tmp_path):
    canonical = tmp_path / "daniel-ospina-tortoise-7.json"
    canonical.write_text(
        '{"pr":7,"head_sha":"' + LIVE + '","mint":"fresh","verdict":"clean-micro",'
        '"evidence":"comment:1","diff_sha256":"' + "c" * 64 + '","reviewed_at":"2026-11-03T02:21:22Z"}'
    )
    (tmp_path / "7.json").write_text('{"head_sha":"' + OLD + '"}')  # legacy must lose
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
    assert got is not None
    assert got.path == canonical
    assert got.head_sha == LIVE
    assert got.verdict == "clean-micro"
    assert got.real_evidence is True


def test_a_corrupt_record_is_reported_not_crashed(tmp_path):
    (tmp_path / "daniel-ospina-tortoise-9.json").write_text("{not json")
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 9)
    assert got is not None
    assert got.error.startswith("unreadable:")
    assert got.real_evidence is False


def test_missing_record_is_none(tmp_path):
    assert ac.load_record(tmp_path, "daniel-ospina", "tortoise", 11) is None


# ── review cycle 1 findings (each measured before it was fixed) ──────────────


def test_a_carried_record_WITH_evidence_at_the_live_head_is_re_review():
    """⛔ The dangerous shape — the only test that pins `real_evidence`'s mint guard.

    Clean verdict + evidence + head equal means records WITHOUT the mint exclusion reach
    RESTORE by the first branch — #10177's laundering. Deleting `self.mint != "carried"`
    from `real_evidence` must REDDEN this test; before it existed that mutation left the
    suite green (measured).
    """
    outcome, why = ac.classify(LIVE, rec(mint="carried", evidence="comment:1"), *RED)
    assert outcome == ac.RE_REVIEW, why
    assert "carried" in why


def test_a_corrupt_record_is_UNREADABLE_never_nothing_to_restore(tmp_path):
    """A read failure is not evidence of absence, so it may not license "nothing to restore"."""
    (tmp_path / "daniel-ospina-tortoise-2.json").write_text("{not json")
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 2)
    outcome, why = ac.classify(LIVE, got, *RED)
    assert outcome == ac.UNREADABLE, why
    assert "could not be used" in why


def test_json_that_parses_to_a_non_object_is_unreadable_not_a_crash(tmp_path):
    (tmp_path / "daniel-ospina-tortoise-3.json").write_text("[1, 2]")
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 3)
    assert got is not None and "not an object" in got.error


def test_a_record_with_no_head_sha_is_unreadable_with_an_honest_reason():
    r = ac.Record(path=Path("/x.json"), head_sha="", mint="fresh", verdict="clean", evidence="comment:1")
    outcome, why = ac.classify(LIVE, r, *RED)
    assert outcome == ac.UNREADABLE
    assert "head_sha" in why and "not a clean verdict" not in why


def test_empty_container_evidence_is_not_evidence():
    """The COLON rule is what refuses a stringified empty — not a nullish list.

    (Cycle 3 measured the previous `_NULLISH_EVIDENCE` set to be unreachable behind the colon
    requirement, and therefore a guard that could never fire.)
    """
    for bogus in ([], {}, "null", "   ", "None", "none", "n/a", "undefined"):
        r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=bogus)
        assert r.real_evidence is False, bogus
        assert ac.classify(LIVE, r, *RED)[0] != ac.RESTORE, bogus


def test_mint_is_normalised_the_carried_guard_must_not_miss_a_variant():
    """This guard is FAIL-OPEN if it misses: a carried record reaching RESTORE is fabrication.

    Cycle 3 measured `"Carried"` / `"CARRIED"` / `" carried"` / `"carried "` all reaching
    RESTORE before normalisation.
    """
    for variant in ("Carried", "CARRIED", " carried", "carried ", "Carried\t"):
        r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint=variant, verdict="clean", evidence="comment:1")
        assert r.real_evidence is False, variant
        outcome, why = ac.classify(LIVE, r, *RED)
        assert outcome == ac.RE_REVIEW, (variant, outcome, why)


def test_evidence_must_be_artifact_shaped():
    # A reference is `comment:<id>` / `review:<id>` / a URL ending in one.
    for good in ("comment:6593768046", "review:123", "https://github.com/o/r/pull/1#issuecomment-9"):
        r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=good)
        assert r.real_evidence is True, good
    assert ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence="looks fine").real_evidence is False


def test_a_foreign_repos_record_is_never_evidence(tmp_path):
    (tmp_path / "daniel-ospina-tortoise-9.json").write_text(
        '{"repo":"someone/else","pr":9,"head_sha":"' + LIVE + '","mint":"fresh",'
        '"verdict":"clean","evidence":"comment:1"}'
    )
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 9)
    assert got is not None and "foreign record" in got.error
    assert ac.classify(LIVE, got, *RED)[0] == ac.UNREADABLE


def test_the_filename_lookup_is_case_insensitive_like_gh(tmp_path):
    # `gh` treats DMeer/dmeer as one repo; the registry path is case-sensitive (#426 class).
    (tmp_path / "daniel-ospina-DMeer-5.json").write_text(
        '{"repo":"daniel-ospina/DMeer","pr":5,"head_sha":"' + LIVE + '","mint":"fresh",'
        '"verdict":"clean","evidence":"comment:1"}'
    )
    got = ac.load_record(tmp_path, "daniel-ospina", "dmeer", 5)
    assert got is not None, "a case-differing repo name must still find its record"
    assert got.real_evidence is True


def test_an_unreadable_registry_raises_instead_of_reporting_absence(tmp_path):
    import pytest

    with pytest.raises(ac.RegistryUnreadable):
        ac.load_record(tmp_path / "does-not-exist", "daniel-ospina", "tortoise", 1)


def test_read_gate_picks_the_newest_attempt_by_id_not_by_order(monkeypatch):
    """Requirement (c): newest by `id` — and the fixture must DISAGREE with list order.

    The endpoint answers newest-first in production, so list order and id order agree there; a
    fixture that also agrees cannot catch `runs[-1]`, which reads the OLDEST attempt (the rollup
    error requirement (c) forbids). So the newest id here sits FIRST in the list.
    """
    payload = {
        "check_runs": [
            {"id": 99, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure"},
            {"id": 10, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success"},
        ]
    }
    monkeypatch.setattr(ac, "gh_json", lambda args: payload)
    assert ac.read_gate("o/r", "a" * 40) == ("completed", "failure")


def test_read_gate_also_refuses_LIST_ORDER_when_the_newest_id_comes_last(monkeypatch):
    """The sibling fixture puts the newest id FIRST (killing `runs[-1]`); this one puts it LAST
    (killing `runs[0]`), so positional reads are refused in both directions (cycle 4, finding 3)."""
    payload = {
        "check_runs": [
            {"id": 10, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success"},
            {"id": 99, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure"},
        ]
    }
    monkeypatch.setattr(ac, "gh_json", lambda args: payload)
    assert ac.read_gate("o/r", "a" * 40) == ("completed", "failure")


def test_read_gate_requests_the_exact_production_query(monkeypatch):
    """`filter=all` and `check_name=` are the request, not decoration: `filter=all`'s default is
    NOT all (a measured 26-of-40), so dropping it silently reads the verdict from a stale subset."""
    seen = {}

    def gh(args):
        seen["args"] = args
        return {"check_runs": []}

    monkeypatch.setattr(ac, "gh_json", gh)
    ac.read_gate("o/r", "a" * 40)
    argv = " ".join(seen["args"])
    assert "filter=all" in argv, argv
    assert f"check_name={ac.GATE_CHECK}" in argv, argv
    assert "-X GET" in argv, argv


def test_read_gate_refuses_a_non_integer_id(monkeypatch):
    """An unorderable id is a payload we cannot judge — a transport verdict, never a TypeError."""
    for bad in (None, "99", 99.5):
        monkeypatch.setattr(
            ac,
            "gh_json",
            lambda args, b=bad: {"check_runs": [{"id": b, "name": ac.GATE_CHECK, "status": "completed"}]},
        )
        try:
            ac.read_gate("o/r", "a" * 40)
        except RuntimeError as exc:
            assert "integer id" in str(exc)
        else:
            raise AssertionError(f"id={bad!r} must be a transport failure")


def test_gh_json_wraps_an_undecodable_answer_as_runtime_error(monkeypatch):
    def boom(*a, **k):
        raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")

    monkeypatch.setattr(ac.subprocess, "run", boom)
    try:
        ac.gh_json(["api", "x"])
    except RuntimeError as exc:
        assert "undecodable" in str(exc)
    else:
        raise AssertionError("an undecodable answer must be RuntimeError (contract: never exit 1)")


def test_a_legacy_named_record_without_a_repo_is_never_evidence(tmp_path):
    """`<pr>.json` carries no repo in its NAME, so an undeclared one cannot be attributed —
    adopting it would let a fork/mirror's PR #7 attest THIS repo's PR #7 (cycle 4, finding 5)."""
    (tmp_path / "7.json").write_text(
        '{"head_sha":"' + LIVE + '","mint":"fresh","verdict":"clean","evidence":"comment:1"}'
    )
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
    assert got is not None and "declares no repo" in got.error
    assert ac.classify(LIVE, got, *RED)[0] != ac.RESTORE


def test_a_legacy_named_record_that_DECLARES_this_repo_is_accepted(tmp_path):
    (tmp_path / "7.json").write_text(
        '{"repo":"daniel-ospina/tortoise","pr":7,"head_sha":"' + LIVE + '","mint":"fresh",'
        '"verdict":"clean","evidence":"comment:1"}'
    )
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
    assert got is not None and got.error == ""
    assert ac.classify(LIVE, got, *RED)[0] == ac.RESTORE


def test_main_returns_2_on_a_non_list_pr_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "gh_json", lambda args: {"message": "Not Found"})
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 2


def test_read_gate_ignores_another_check_that_has_the_highest_id(monkeypatch):
    """The local `name ==` filter is load-bearing: check-run ids are repo-global and monotonic,
    so if the `check_name` query is ever not honoured the newest run of ANY check would become
    the gate verdict (a false OK, or a false RESTORE)."""
    payload = {
        "check_runs": [
            {"id": 50, "name": "python-ci", "status": "completed", "conclusion": "failure"},
            {"id": 10, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success"},
        ]
    }
    monkeypatch.setattr(ac, "gh_json", lambda args: payload)
    assert ac.read_gate("o/r", "a" * 40) == ("completed", "success")


def test_no_gate_run_at_all_reports_absent_not_green(monkeypatch):
    monkeypatch.setattr(ac, "gh_json", lambda args: {"check_runs": []})
    assert ac.read_gate("o/r", "a" * 40) == ("", "")


def test_main_returns_2_when_the_registry_cannot_be_listed(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "gh_json", lambda args: [])
    assert ac.main(["--reviews-dir", str(tmp_path / "nope"), "--out", str(tmp_path / "o.md")]) == 2


def test_main_returns_2_for_a_row_it_calls_unreadable(monkeypatch, tmp_path):
    # A census that could not read every surface is NOT a completed census (rc 0).
    monkeypatch.setattr(
        ac, "gh_json", lambda args: [{"number": 1, "headRefOid": "short", "headRefName": "b", "title": "t"}]
    )
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 2


def test_main_returns_2_on_a_CORRUPT_record_row(monkeypatch, tmp_path):
    """Pins the ONE rc-2 increment site: a corrupt record is UNREADABLE ⇒ rc 2.

    Cycle 2 measured that deleting the generic increment left the suite green, because the
    bad-head test incremented on its own path — so a corrupt record returned rc 0 while the
    report printed UNREADABLE.
    """
    (tmp_path / "daniel-ospina-tortoise-1.json").write_text("{not json")
    monkeypatch.setattr(
        ac, "gh_json", lambda args: [{"number": 1, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
    )
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 2


def test_main_returns_2_when_the_check_run_read_fails(monkeypatch, tmp_path):
    """The RATE-LIMIT path: `gh pr list` succeeds, the per-PR check-run read is throttled.

    Cycle 3 measured that the previous version patched every call to raise, so it returned 2 from
    the PR-list failure and never reached `read_gate` — deleting that handler left the suite green.
    """
    def gh(args):
        if args[0] == "pr":
            return [{"number": 1, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
        raise RuntimeError("HTTP 403: API rate limit exceeded")

    monkeypatch.setattr(ac, "gh_json", gh)
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 2


def test_main_returns_2_when_a_pr_row_has_no_integer_number(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "gh_json", lambda args: [{"headRefOid": LIVE, "headRefName": "b"}])
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 2


def test_main_returns_2_when_the_registry_becomes_unreadable_mid_run(monkeypatch, tmp_path):
    """The pre-loop probe passes, then the loop's own call raises (dir removed / perms changed)."""
    calls = {"n": 0}

    def flaky(d, o, r, p):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # the probe
        raise ac.RegistryUnreadable("registry disappeared")

    monkeypatch.setattr(ac, "load_record", flaky)
    monkeypatch.setattr(
        ac, "gh_json", lambda args: [{"number": 1, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
    )
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 2


def test_an_invalid_utf8_record_is_unreadable_not_a_crash(tmp_path):
    # UnicodeDecodeError is a ValueError, not an OSError (cycle 3: it escaped as rc 1).
    (tmp_path / "daniel-ospina-tortoise-4.json").write_bytes(b'{"head_sha":"\xff\xfe"}')
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 4)
    assert got is not None and got.error.startswith("unreadable:")


def test_read_gate_refuses_a_malformed_payload_as_a_transport_failure(monkeypatch):
    for payload in ([], None, "ok", {"check_runs": ["x"]}, {"check_runs": {"id": 1}}):
        monkeypatch.setattr(ac, "gh_json", lambda args, p=payload: p)
        try:
            ac.read_gate("o/r", "a" * 40)
        except RuntimeError as exc:
            assert "shape" in str(exc)
        else:
            raise AssertionError(f"payload {payload!r} must be a transport failure, not a verdict")


def test_gh_json_wraps_a_missing_gh_binary_as_runtime_error(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    monkeypatch.setattr(ac.subprocess, "run", boom)
    try:
        ac.gh_json(["pr", "list"])
    except RuntimeError as exc:
        assert "could not run gh" in str(exc)
    else:
        raise AssertionError("a missing gh must be RuntimeError (contract: never exit 1)")


def test_an_unwritable_out_path_is_rc_2_not_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setattr(ac, "gh_json", lambda args: [])
    blocker = tmp_path / "file-not-dir"
    blocker.write_text("x")
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(blocker / "o.md")]) == 2


def test_no_gate_does_not_overwrite_a_record_present_reason(monkeypatch, tmp_path):
    """A record-present NOTHING row keeps its own reason (evidence-less), not "no record".

    Cycle 3 measured the previous rewrite firing for every NOTHING row, so the row asserted the
    opposite of its own mint/verdict/evidence columns.
    """
    (tmp_path / "daniel-ospina-tortoise-2.json").write_text(
        '{"repo":"daniel-ospina/tortoise","pr":2,"head_sha":"' + LIVE + '","mint":"recovered-body",'
        '"verdict":"clean","evidence":null}'
    )
    monkeypatch.setattr(
        ac, "gh_json", lambda args: [{"number": 2, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
    )
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 0
    text = (tmp_path / "o.md").read_text()
    assert "record carries no evidence" in text
    assert "no record on this box at any head; the published-marker surface was NOT" not in text


def test_an_absent_gate_run_is_not_reported_as_a_marker_absence(monkeypatch, tmp_path):
    """No check-run on the head is not a read of the marker surface (cycle 3, finding 4)."""
    monkeypatch.setattr(
        ac, "gh_json",
        lambda args: [{"number": 3, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
        if args[0] == "pr"
        else {"check_runs": []},
    )
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 0
    text = (tmp_path / "o.md").read_text()
    assert "gate reported nothing readable on this head" in text
    assert "and no head-bound published marker" not in text


def test_a_directory_named_like_a_record_is_unreadable_not_a_crash(tmp_path):
    """An unreadable record (perm denied, dangling link, a DIRECTORY named *.json) must not
    raise out of main — that would be rc 1, against the 0/2-never-1 contract."""
    (tmp_path / "daniel-ospina-tortoise-9.json").mkdir()
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 9)
    assert got is not None and got.error.startswith("unreadable:")


def test_a_record_naming_another_pr_is_never_evidence(tmp_path):
    (tmp_path / "daniel-ospina-tortoise-7.json").write_text(
        '{"repo":"daniel-ospina/tortoise","pr":8,"head_sha":"' + LIVE + '","mint":"fresh",'
        '"verdict":"clean","evidence":"comment:1"}'
    )
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
    assert got is not None and "foreign record" in got.error
    assert ac.classify(LIVE, got, *RED)[0] == ac.UNREADABLE


def test_no_gate_run_does_not_assert_a_github_absence_it_never_read(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ac, "gh_json", lambda args: [{"number": 1, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
    )
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 0
    text = (tmp_path / "o.md").read_text()
    assert "published-marker surface was NOT" in text
    assert "and no head-bound published marker" not in text


def test_main_returns_2_and_writes_no_artifact_when_gh_answers_non_json(monkeypatch, tmp_path):
    def boom(args):
        raise RuntimeError("gh returned non-JSON")

    monkeypatch.setattr(ac, "gh_json", boom)
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 2
    assert not (tmp_path / "o.md").exists()


def test_gh_json_wraps_a_non_json_answer_as_runtime_error(monkeypatch):
    class Proc:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(ac.subprocess, "run", lambda *a, **k: Proc())
    try:
        ac.gh_json(["api", "x"])
    except RuntimeError as exc:
        assert "non-JSON" in str(exc)
    else:
        raise AssertionError("a non-JSON answer must raise RuntimeError (contract: never exit 1)")
