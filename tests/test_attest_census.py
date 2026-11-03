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

The tool never MINTS a verdict, never writes a registry record and never posts to GitHub — those it
only reads. (It does write its own report: `--out`, default `operations/attest-census.md`, which the
snapshot warning marks as per-box and short-lived.)
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


def test_evidence_urls_must_be_github_artifacts():
    """Cycle 9: the URL arm checked the ANCHOR, not the host — so example.com, bitbucket and
    localhost all licensed RESTORE for a pointer that resolves to no artifact on any repo."""
    for foreign in (
        "https://example.com/#issuecomment-9",
        "https://bitbucket.org/x#issuecomment-9",
        "http://localhost/#pullrequestreview-1",
    ):
        r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=foreign)
        assert r.real_evidence is False, foreign
        assert ac.classify(LIVE, r, *RED)[0] != ac.RESTORE, foreign


def test_the_record_cell_does_not_call_an_intact_foreign_record_corrupt(tmp_path):
    """Cycle 9: `CORRUPT` keyed on `rec.error == ""`, so an intact foreign/unattributable record —
    JSON that merely names another PR/repo — was labelled corrupt, whose remedy is destructive."""
    (tmp_path / "7.json").write_text(
        '{"head_sha":"' + LIVE + '","mint":"fresh","verdict":"clean","evidence":"comment:1"}'
    )
    (tmp_path / "8.json").write_text("{not json")
    good = ac.Row(pr=7, head=LIVE, branch="b", title="t", record=ac.load_record(tmp_path, "o", "r", 7))
    assert good.record is not None and "legacy" in good.record.error
    text = ac.render([good], "o/r", "2026-11-03T00:00:00Z")
    assert "(unattributable)" in text and "(corrupt)" not in text, text
    bad = ac.Row(pr=8, head=LIVE, branch="b", title="t", record=ac.load_record(tmp_path, "o", "r", 8))
    assert "(corrupt)" in ac.render([bad], "o/r", "2026-11-03T00:00:00Z")


def test_the_pr_list_guard_refuses_a_scalar_payload(monkeypatch, tmp_path):
    """Cycle 9: the list clause was unpinned — a dict payload is refused by the all-dicts clause
    (iterating its string keys), but a SCALAR (`null` from a proxy/rate-limit answer, or a number)
    reached `for pr in prs` and raised TypeError -> rc 1."""
    for scalar in (None, 5, "x"):
        monkeypatch.setattr(ac, "gh_json", lambda args, s=scalar: s)
        assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 2, scalar


def test_a_github_url_with_no_path_is_not_an_artifact():
    r"""Cycle 10: the URL arm's `\S*` matched the empty path, so `https://github.com/#issuecomment-9`
    licensed RESTORE from a pointer that names no repo, no PR and no artifact."""
    for empty in ("https://github.com/#issuecomment-9", "https://github.com/.#issuecomment-9"):
        r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=empty)
        assert r.real_evidence is False, empty
        assert ac.classify(LIVE, r, *RED)[0] != ac.RESTORE, empty


def test_a_truncated_population_is_stated_in_the_report(monkeypatch, tmp_path):
    """Cycle 10: `--limit` silently censused the first N PRs and reported rc 0 / "one row per open
    PR" — an absence-as-verdict at population scale."""

    def gh(args):
        if args[0] == "pr":
            return [{"number": n, "headRefOid": LIVE, "headRefName": "b", "title": "t"} for n in range(1, 4)]
        return {"check_runs": []}

    monkeypatch.setattr(ac, "gh_json", gh)
    out = tmp_path / "o.md"
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--limit", "2", "--out", str(out)]) == 0
    text = out.read_text()
    assert "TRUNCATED" in text and "MORE than 2" in text, text
    assert text.count("| #") == 2, text


def test_a_population_that_fits_is_not_labelled_truncated(monkeypatch, tmp_path):
    def gh(args):
        if args[0] == "pr":
            return [{"number": 1, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
        return {"check_runs": []}

    monkeypatch.setattr(ac, "gh_json", gh)
    out = tmp_path / "o.md"
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--limit", "5", "--out", str(out)]) == 0
    assert "TRUNCATED" not in out.read_text()


def test_a_hyphenless_discussion_r_permalink_is_an_artifact():
    """Round 11: the cycle-10 tightening required a `-` for ALL anchor kinds, so a real
    `#discussion_r<id>` permalink (no hyphen on GitHub) was refused as "prose-shaped"."""
    url = "https://github.com/cli/cli/pull/3#discussion_r332430752"
    r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=url)
    assert r.real_evidence is True
    assert ac.classify(LIVE, r, *RED)[0] == ac.RESTORE

    for good in (
        "https://github.com/o/r/pull/1#issuecomment-9",
        "https://github.com/o/r/pull/1#pullrequestreview-9",
    ):
        assert ac.Record(
            path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=good
        ).real_evidence is True, good


def test_json_surface_states_truncation(monkeypatch, tmp_path, capsys):
    """Round 11 P1: `--json` carried `limit` rows and rc 0 with no truncation marker, so a
    programmatic consumer read a partial census as the whole population."""
    import json as _json

    def gh(args):
        if args[0] == "pr":
            return [{"number": n, "headRefOid": LIVE, "headRefName": "b", "title": "t"} for n in range(1, 4)]
        return {"check_runs": []}

    monkeypatch.setattr(ac, "gh_json", gh)
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--limit", "2", "--json"]) == 0
    payload = _json.loads(capsys.readouterr().out)
    assert payload["truncated"] is True
    assert payload["limit"] == 2
    assert len(payload["rows"]) == 2


def test_json_surface_is_not_truncated_when_the_population_fits(monkeypatch, tmp_path, capsys):
    import json as _json

    def gh(args):
        if args[0] == "pr":
            return [{"number": 1, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
        return {"check_runs": []}

    monkeypatch.setattr(ac, "gh_json", gh)
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--limit", "2", "--json"]) == 0
    assert _json.loads(capsys.readouterr().out)["truncated"] is False


def test_missing_record_is_none(tmp_path):
    assert ac.load_record(tmp_path, "daniel-ospina", "tortoise", 11) is None


def test_a_head_that_differs_ONLY_IN_THE_LAST_CHARACTER_is_re_review():
    """⛔ Pins "byte-for-byte" (cycle 8). Every other fixture differs in the FIRST character, so a
    truncated comparison (`rec_head[:12] == head[:12]`, or `[:7]`) stayed green — and would restore
    a proof bound to a DIFFERENT commit. One flipped last char is the discriminator."""
    near = LIVE[:-1] + ("b" if LIVE[-1] != "b" else "c")
    assert len(near) == 40 and near[:39] == LIVE[:39] and near != LIVE
    outcome, why = ac.classify(LIVE, rec(head=near), *RED)
    assert outcome == ac.RE_REVIEW, why


def test_a_record_head_that_is_not_40_lowercase_hex_is_unreadable(tmp_path):
    """`rec_head` must be a real sha, not just non-empty: `"abc"` or uppercase hex is a record the
    tool cannot bind, and reporting it as `RE-REVIEW` would be a completed census over a record it
    could not read (cycle 8)."""
    for bad in ("abc", "A" * 40, LIVE + "0"):
        r = ac.Record(path=Path("/x.json"), head_sha=bad, mint="fresh", verdict="clean", evidence="comment:1")
        outcome, why = ac.classify(LIVE, r, *RED)
        assert outcome == ac.UNREADABLE, (bad, outcome)
        assert "head_sha" in why


def test_the_pr_list_guard_refuses_a_list_of_non_objects(monkeypatch, tmp_path):
    """`not all(isinstance(p, dict) …)` is load-bearing: without it a `["x"]` payload raises
    AttributeError out of main as rc 1 (cycle 8)."""
    monkeypatch.setattr(ac, "gh_json", lambda args: ["x"])
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 2


def test_the_rendered_report_carries_its_snapshot_warning(monkeypatch, tmp_path):
    """The banner is the artifact's only defence against a stale census being read as live state."""
    monkeypatch.setattr(ac, "gh_json", lambda args: [])
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 0
    text = (tmp_path / "o.md").read_text()
    assert "SNAPSHOT, NOT A LIVE VERDICT" in text
    assert "per-machine" in text


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
    # ⛔ the phrase the TOOL actually emits (cycle 5: the old `"and no head-bound …"` matched
    # nothing the tool can print, so the assertion that reads as the guard could not fail).
    assert "found no head-bound published marker" not in text


def test_the_positive_marker_clause_does_fire_when_the_gate_reported_red(monkeypatch, tmp_path):
    """The mirror of the test above: a REAL red gate must produce the marker clause, so neither
    direction can be satisfied by a phrase the tool never prints."""
    monkeypatch.setattr(
        ac,
        "gh_json",
        lambda args: [{"number": 3, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
        if args[0] == "pr"
        else {"check_runs": [{"id": 5, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure"}]},
    )
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 0
    text = (tmp_path / "o.md").read_text()
    # A red conclusion does NOT establish that the marker surface was read (cycle 6): the gate
    # fails before reading markers when its key is unset, when every candidate fails HMAC, when
    # the comment fetch 403s, or when the diff hash cannot be computed.
    assert "whether a published marker exists is answered by the gate's own annotation" in text
    assert "found no head-bound published marker" not in text


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
    assert "found no head-bound published marker" not in text


def test_a_record_declaring_another_pr_AS_A_STRING_is_never_evidence(tmp_path):
    """Cycle 5: `isinstance(rec_pr, int)` let `"pr": "8"` through, so PR 8's artifact could
    attest PR 7 — a fabricated attribution."""
    for bogus_pr in ("8", 8.0, " 8 "):
        (tmp_path / "daniel-ospina-tortoise-7.json").write_text(
            '{"repo":"daniel-ospina/tortoise","pr":' + json.dumps(bogus_pr) + ',"head_sha":"' + LIVE + '",'
            '"mint":"fresh","verdict":"clean","evidence":"comment:1"}'
        )
        got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
        assert got is not None and "foreign record" in got.error, bogus_pr
        assert ac.classify(LIVE, got, *RED)[0] != ac.RESTORE, bogus_pr


def test_prose_carrying_a_colon_is_not_evidence():
    """Cycle 5: a bare `":" in evidence` test accepted `"TODO: verify"` and licensed RESTORE."""
    for prose in ("TODO: verify", "evidence: none", " : ", "see PR body: n/a", "comment:abc"):
        r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=prose)
        assert r.real_evidence is False, prose
        outcome, why = ac.classify(LIVE, r, *RED)
        assert outcome != ac.RESTORE, prose
        # The reason must name the REAL defect: a clean verdict with an unusable pointer is not
        # "a non-clean verdict" (cycle 6 measured the row contradicting its own verdict column).
        assert "not an artifact pointer" in why, why
        assert "is not a clean verdict" not in why, why


def test_the_reason_never_claims_a_non_clean_verdict_is_clean():
    """Cycle 7: the evidence-pointer branch printed "is clean" beside `verdict=ISSUES`, sending a
    lane to repair a pointer on a review that FOUND ISSUES — the mirror of the cycle-6 defect."""
    for verdict in ("ISSUES", "NOT CLEAN", "", "clean"):
        r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict=verdict, evidence="TODO: verify")
        outcome, why = ac.classify(LIVE, r, *RED)
        assert outcome == ac.NOTHING, (verdict, outcome)
        assert "is clean" not in why, (verdict, why)
        assert "not an artifact pointer" in why, (verdict, why)


def test_an_unresolvable_out_home_is_rc_2_and_writes_nothing(tmp_path, monkeypatch):
    """Cycle 7: without the guard `--out '~nosuchuser/o.md'` did not raise — it created a literal
    `./~nosuchuser/` directory and returned 0, i.e. a completed census at a path nobody named."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ac, "gh_json", lambda args: [])
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", "~nosuchuser12345/o.md"]) == 2
    assert not (tmp_path / "~nosuchuser12345").exists()


def test_an_unresolvable_reviews_dir_home_is_rc_2_not_a_traceback(tmp_path):
    # `pathlib.expanduser` raises RuntimeError for `~nosuchuser` — rc 1 would break the contract.

    assert ac.main(["--reviews-dir", "~nosuchuser12345/rev", "--out", str(tmp_path / "o.md")]) == 2


def test_both_real_registry_evidence_shapes_are_accepted():
    # The 3 742-record registry holds exactly two shapes: `comment:<id>`, and one GitHub URL.
    for good in (
        "comment:6194795895",
        "https://github.com/daniel-ospina/tortoise/pull/8725#issuecomment-6194795895",
    ):
        r = ac.Record(path=Path("/x.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=good)
        assert r.real_evidence is True, good


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


def test_an_old_interpreter_is_refused_with_exit_2_and_an_attributable_message(monkeypatch, capsys):
    """The #5128 guard's CODE, which the class-wide gate (`test_entry_point_python_guard.py`)
    deliberately does not decide per tool.

    Its shape and message are gated there; its exit code is this file's own contract. `classify`
    publishes 0 completed / 2 unreadable — "never 1, so a caller cannot mistake 'unreadable' for
    'found a problem'" — and `raise SystemExit(<str>)` exits 1. So the guard is compiled from the
    tool's own AST and EXECUTED at a simulated 3.9, asserting the code and the attribution: it
    refuses rc-1 and a silent refusal, the two ways an old interpreter could read as a verdict.
    """
    tree = ast.parse(TOOL.read_text())
    guard = next(
        node
        for node in tree.body
        if isinstance(node, ast.If) and "version_info" in ast.unparse(node.test)
    )
    module = ast.Module(body=[guard], type_ignores=[])
    ast.fix_missing_locations(module)
    code = compile(module, "tools/attest_census.py", "exec")

    monkeypatch.setattr(sys, "version_info", (3, 9, 6))
    with pytest.raises(SystemExit) as refusal:
        exec(code, {"__name__": "__main__", "sys": sys})
    message = str(refusal.value) + capsys.readouterr().err
    assert refusal.value.code == 2, f"must not exit 1 (rc 1 is the tool's forbidden status): {message!r}"
    assert ">= 3.12" in message and "(got 3.9)" in message, f"unattributable refusal: {message!r}"
    assert "uv run python tools/attest_census.py" in message, f"must name the working form: {message!r}"


# ── MD036: the report's shape is gated by the REQUIRED `docs` check (#10687) ─────────
#
# `render` writes `operations/attest-census.md`, a COMMITTED artifact. It therefore sits
# in the changed-markdown set of every PR that regenerates it, is linted WHOLE by the
# `docs` job's pinned `markdownlint-cli2@0.23.3`, and a finding in it is `new` against
# `config/docs-lint-baseline.json` — which under #7435's Option 1 (the snapshot is a
# CEILING, not an amnesty) is a real failure. The totals line used to be
# `f"**{totals}**"`, a paragraph consisting ENTIRELY of emphasis = MD036, so ANY change
# that re-ran the census reddened a required check on a finding it did not write.

# `markdownlint@0.41.1` `helpers/helpers.cjs:41` `allPunctuation`, VERBATIM — the rule
# builds `new RegExp("[" + punctuation + "]$")` from it (`lib/md036.mjs:29-30`). Note it
# has NO ellipsis and DOES carry six full-width marks: an earlier hand-written
# `".,;:!?…"` was wrong in both directions and measured so (`**Foo bar…**` FIRES,
# `**Foo bar。**` does not).
_MD036_PUNCTUATION = ".,;:!?。，；：！？"

# `md036.mjs:45-46` fires only when the emphasis TEXT token has exactly one child and it
# is `data`. A code span, link, autolink or NESTED emphasis adds a child, so the paragraph
# is not a heading candidate (measured: `**Foo *bar* baz**`, `_Foo **bar**_`,
# `**Foo `code` bar**` and `**Foo [l](u)**` are all clean).
_MD036_INLINE_MARKUP = "`[]<*"


def _emphasis_only_paragraphs(text: str) -> list[str]:
    """Return the paragraphs in `text` that MD036 fires on.

    A PROXY for the pinned linter, not a reimplementation of markdownlint: the AUTHORITY
    is the `docs` job. It is pinned to 21 measured shapes in
    `test_the_md036_model_matches_the_measured_linter`; if the two ever disagree, this
    model is what is wrong.

    It models the rule's three conditions (`lib/md036.mjs`):
      * the paragraph must have ONE meaningful child — i.e. the whole paragraph is one
        emphasis span, so a PARAGRAPH is judged, not a line (`**Foo**\\nbar` is two
        children and is clean). Non-paragraph lines are skipped: headings, blockquotes,
        table rows, list items (`* ` with the SPACE — `*Foo*` is emphasis, not a bullet),
        and code.
      * the emphasis text must be plain: `_MD036_INLINE_MARKUP` absent, and at most one
        `_` (a single intraword underscore stays `data` — `**a_b**` FIRES).
      * the text must not end in `_MD036_PUNCTUATION`.
    Delimiters are tried longest-first so `**` wins over `*`.

    A 4-space line is indented code only at the START of a paragraph: after a paragraph
    line it is a LAZY CONTINUATION, so `**Foo**\\n    bar` is one two-child paragraph and
    is clean (measured).
    """
    offenders: list[str] = []
    paragraph: list[str] = []
    in_fence = False

    def flush() -> None:
        if not paragraph:
            return
        joined = "\n".join(paragraph).strip()
        paragraph.clear()
        for marker in ("**", "__", "*", "_"):
            if len(joined) <= 2 * len(marker):
                continue
            if not (joined.startswith(marker) and joined.endswith(marker)):
                continue
            inner = joined[len(marker) : -len(marker)]
            if not inner.strip():
                return
            if any(ch in inner for ch in _MD036_INLINE_MARKUP) or inner.count("_") > 1:
                return
            if not inner.rstrip().endswith(tuple(_MD036_PUNCTUATION)):
                offenders.append(joined)
            return

    for raw in [*text.splitlines(), ""]:
        s = raw.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            flush()
            continue
        if in_fence:
            continue
        is_bullet = len(s) > 1 and s[0] in "-+*" and s[1] == " "
        indented_code = not paragraph and (raw.startswith("    ") or raw.startswith("\t"))
        if not s or indented_code or s.startswith(("#", ">", "|")) or is_bullet:
            flush()
            continue
        paragraph.append(s)
    return offenders


# Every shape below was MEASURED with the `docs` job's own producer
# (`markdownlint-cli2@0.23.3`, repo config) over a real file under `operations/`, each run
# confirming `Linting: 1 file`. That check is not ceremony: a copy outside the lint root
# reports `Linting: 0 files` and a vacuous 0-issue "pass", which is how the first attempt
# at this verification nearly went.
_MD036_FIRES = [
    "_Foo bar baz_",  # underscore emphasis — a `*`-only model missed this
    "__Foo bar baz__",
    "**Foo bar baz**",
    "*Foo bar baz*",
    "**Foo bar…**",  # ellipsis is NOT in the rule's punctuation set, so this FIRES
    "**a_b**",  # one intraword underscore is still plain `data`
]
_MD036_CLEAN = [
    "**Totals:**",  # ends in `:` — the shape this PR introduces
    "**Totals:** RE-REVIEW=5",  # text outside the span: two children
    "**Foo bar baz...**",  # punctuation
    "**Foo bar baz.**",  # punctuation
    "**Foo bar。**",  # full-width punctuation IS in the set
    "**Foo**\nbar",  # one paragraph, two meaningful children
    "**Foo**\n    bar",  # 4 spaces after a paragraph line is a LAZY CONTINUATION
    "    **Indented code**",  # code block
    "> **In a quote**",  # not a top-level paragraph
    "- **In a list**",  # not a paragraph
    "****",  # empty span
    "**Foo *bar* baz**",  # nested emphasis -> more than one child
    "_Foo **bar**_",  # nested strong -> more than one child
    "**Foo `code` bar**",  # code span -> more than one child
    "**Foo [l](u)**",  # link -> more than one child
]


def test_the_md036_model_matches_the_measured_linter():
    """Pin the proxy above to the pinned linter's MEASURED boundary.

    If markdownlint's rule changes, THIS test is what notices that the proxy and the gate
    have parted company — rather than a docstring continuing to claim an agreement that
    no longer holds.
    """
    for body in _MD036_FIRES:
        assert _emphasis_only_paragraphs(f"Prose.\n\n{body}\n\n|x|\n|-|\n"), f"must fire (measured): {body!r}"
    for body in _MD036_CLEAN:
        assert not _emphasis_only_paragraphs(f"Prose.\n\n{body}\n\n|x|\n|-|\n"), f"measured clean: {body!r}"
def test_the_report_never_renders_an_emphasis_only_paragraph():
    """#10687: the required `docs` check was RED on this PR's own regenerated census.

    Pinned as the CLASS, not the one line that broke: `render` builds ~20 lines and a
    future one could reintroduce MD036 without touching the totals. The summary is also
    asserted to be LABELLED — the label is what clears the rule, and it says what the
    numbers are (they were previously unlabelled totals a reader had to infer).
    """
    ac = _load()

    rows = [
        ac.Row(pr=1, head=LIVE, branch="b", title="t", record=None, outcome=ac.IN_FLIGHT, why="w"),
        ac.Row(pr=2, head=LIVE, branch="b", title="t", record=None, outcome=ac.OK, why="w"),
    ]
    text = ac.render(rows, "o/r", "2026-11-03T00:00:00Z", truncated=True, limit=1)
    offenders = _emphasis_only_paragraphs(text)
    assert not offenders, f"render() emits an MD036 paragraph — the `docs` gate reds on it: {offenders}"

    # The summary is LABELLED, and its content is the totals it always carried.
    assert "**Totals:** IN-FLIGHT=1 · OK=1" in text, text

    # An empty census has no totals, and a blank `**Totals:**` is a malformed report:
    # the run read nothing and must say so rather than render a bare label.
    empty = ac.render([], "o/r", "2026-11-03T00:00:00Z")
    assert "**Totals:** no rows" in empty, empty
    assert not _emphasis_only_paragraphs(empty), empty


def test_a_pipe_in_a_record_field_cannot_break_the_table():
    """#10687: registry and `gh` text reached `render` unescaped.

    `evidence`, `verdict` and `mint` are read straight out of a record JSON on disk, and
    `why` embeds `gh`'s MULTI-LINE stderr. A `|` emitted an extra table cell — measured
    with the pinned linter, an `evidence` of `"comment:1 | injected"` produced
    `MD056/table-column-count [Expected: 10; Actual: 11]`, the same "a regenerated census
    reds the required `docs` check on a finding the lane did not write" class this PR
    exists to close, plus silent column-shifting in a document lanes read to decide
    RE-POST vs RE-REVIEW.

    Three inputs are here because each was a SEPARATE leak, found in a separate cycle:
      * `head` — `gh`'s `headRefOid`, rendered even when it is not a sha (the UNREADABLE
        path), and escaped INSIDE its code span because a GFM table splits cells BEFORE
        inline parsing.
      * a value already containing `\\|` — escaping `|` alone turns it into `\\\\|`, an
        escaped backslash plus a LIVE delimiter.
      * a multi-line `why` — a raw newline breaks out of the bullet it was written into.
    """
    ac = _load()

    rec = ac.Record(
        path=Path("/tmp/daniel-ospina-tortoise-1.json"),
        head_sha=LIVE,
        mint="fresh",
        verdict="clean | sneaky",
        evidence="comment:1 | injected " + "\\" + "| tail",
    )
    row = ac.Row(
        pr=1,
        head="abc|def",  # not a sha: reachable via the UNREADABLE path
        branch="b | c",
        title="t",
        record=rec,
        outcome=ac.RE_REVIEW,
        why="check-run read failed: gh pr view\n  failed rc=1: boom | splat",
        notes=["note with | pipe\nand a newline"],
    )
    text = ac.render([row], "o/r", "2026-11-03T00:00:00Z")

    def cells(line: str) -> int:
        """Count live delimiters, not every `|`.

        A naive `line.count("|")` counts the escaped ones too and measures nothing, and
        "preceded by a backslash means escaped" shares `_cell`'s original blind spot: `\\|`
        is an ESCAPED BACKSLASH followed by a LIVE delimiter, so a pipe is escaped only
        after an EVEN run of backslashes (#10687 cycle 2).
        """

        def escaped(i: int) -> bool:
            n = 0
            while i - n - 1 >= 0 and line[i - n - 1] == "\\":
                n += 1
            return n % 2 == 1

        return sum(1 for i, ch in enumerate(line) if ch == "|" and not escaped(i)) - 1

    # No cell is injected: every table row carries the header's cell count.
    header = next(ln for ln in text.splitlines() if ln.startswith("| PR |"))
    width = cells(header)
    for ln in text.splitlines():
        if ln.startswith("| #"):
            assert cells(ln) == width, f"row does not have {width} cells: {ln!r}"
    # And the injected pipes survive as escaped DATA rather than becoming structure.
    assert "comment:1 \\| injected" in text, text
    assert "clean \\| sneaky" in text, text
    assert "abc\\|def" in text, text

    # A multi-line `why`/note cannot break out of its bullet.
    assert "\n  failed rc=1" not in text, text
    assert "\nand a newline" not in text, text


@pytest.mark.parametrize("hostile", ["comment:1 | injected", "comment:1" + "\\" + "|x", "a\nb|c"])
def test_the_pinned_linter_accepts_the_rendered_table(hostile, tmp_path):
    """The assertion above counts cells with a helper; this asserts the LINTER agrees.

    Two independent checks on purpose: the helper is a model, the pinned markdownlint is the
    gate, and `test_the_md036_model_matches_the_measured_linter` exists precisely because a
    model and its gate can drift. This one writes the rendered report to a file the linter
    linted and asserts on its OWN verdict — `Linting: 1 file` included, so a run that linted
    nothing cannot pass as clean. Skipped where the pinned linter is not installed (the unit
    suite is hermetic; the `docs` job is the authority and runs it in CI).
    """
    ac = _load()
    script = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "docs-markdownlint.sh"
    if not script.exists():
        pytest.skip("docs-markdownlint.sh not present")
    target = Path(__file__).resolve().parent.parent / "operations" / "zz-attest-census-probe.md"
    nodelist = tmp_path / "probe.nul"
    report = tmp_path / "probe.out"
    rec = ac.Record(
        path=Path("/tmp/r-1.json"), head_sha=LIVE, mint="fresh", verdict="clean", evidence=hostile
    )
    row = ac.Row(pr=1, head=LIVE, branch="b", title="t", record=rec, outcome=ac.OK, why="w")
    target.write_text(ac.render([row], "o/r", "2026-11-03T00:00:00Z"), encoding="utf-8")
    nodelist.write_bytes(b"./operations/zz-attest-census-probe.md\0")
    try:
        import subprocess

        subprocess.run(
            ["bash", str(script), "--cli2", "markdownlint-cli2@0.23.3",
             "--files-from", str(nodelist), "--output", str(report), "--label", "probe"],
            cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=300,
        )
        out = report.read_text() if report.exists() else ""
        if "Linting: 1 file" not in out:
            pytest.skip(f"pinned linter unavailable here (report: {out[:200]!r})")
        assert "Summary: 0 issues in 0 files" in out, f"the pinned linter rejects a hostile cell {hostile!r}: {out}"
    finally:
        target.unlink(missing_ok=True)
