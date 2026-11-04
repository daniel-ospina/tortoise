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
import contextlib
import importlib.util
import json
import os
import re
import subprocess
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


def test_evidence_url_naming_another_repo_or_pr_is_not_evidence(tmp_path):
    """⛔ Cycle 22: the URL arm checked the HOST and the ANCHOR but never the SLUG, so a record
    declaring THIS repo and THIS PR could carry `evidence` pointing at another repository's
    artifact and still reach `RESTORE` — and `RESTORE` is the outcome that tells a lane to
    re-post that pointer as this PR's proof. That is the laundering this tool exists to refuse.

    `load_record` already refuses a record whose own `repo`/`pr` FIELDS disagree (cycles 4/5);
    this is the same rule applied to the field a lane actually re-posts. Measured on the pre-fix
    tree: both records below classified `RESTORE`. Mutation check: pass `self.repo`/`self.pr` as
    `""`/`0` in `real_evidence` (i.e. drop the attribution) and both go green again.
    """
    record = (
        '{"repo":"daniel-ospina/tortoise","pr":7,"head_sha":"' + LIVE + '","mint":"fresh",'
        '"verdict":"clean","evidence":"%s"}'
    )
    for foreign in (
        "https://github.com/evil-org/evil-repo#issuecomment-1",
        "https://github.com/daniel-ospina/tortoise/pull/999#discussion_r5",
    ):
        (tmp_path / "daniel-ospina-tortoise-7.json").write_text(record % foreign, encoding="utf-8")
        got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
        assert got is not None and got.error == "", (foreign, got and got.error)
        assert got.real_evidence is False, foreign
        outcome, why = ac.classify(LIVE, got, *RED)
        assert outcome != ac.RESTORE, (foreign, outcome)
        # The row must name WHICH artifact the pointer belongs to, not a generic "bad pointer".
        assert ("not daniel-ospina/tortoise" in why) or ("not #7" in why), (foreign, why)

    # ... and this PR's OWN artifact still restores: the guard is an attribution test, not a ban.
    own = "https://github.com/daniel-ospina/tortoise/pull/7#issuecomment-1"
    (tmp_path / "daniel-ospina-tortoise-7.json").write_text(record % own, encoding="utf-8")
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
    assert got is not None and got.real_evidence is True
    assert ac.classify(LIVE, got, *RED)[0] == ac.RESTORE
    # A `<kind>:<id>` pointer states no slug, so there is nothing for it to disagree with — the
    # census must not invent an attribution the artifact does not carry.
    (tmp_path / "daniel-ospina-tortoise-7.json").write_text(record % "comment:1", encoding="utf-8")
    got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
    assert got is not None and got.real_evidence is True


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


def test_read_gate_refuses_a_mixed_payload_rather_than_reading_a_older_attempt(monkeypatch):
    """⛔ Cycle 22: `ordered = [r for r in runs if isinstance(r['id'], int)]` DROPPED the
    unorderable attempt and took `max` of what was left, so a payload whose NEWEST attempt had
    `id: null` returned the OLDER attempt's verdict.

    Measured on the pre-fix tree: an older green attempt + a newer `failure` with `id: null` read
    as `('completed', 'success')`, so the row said "gate green; no re-post owed" on a head whose
    newest gate attempt was red — §622's rollup error reached from the other side. The existing
    single-element test above cannot see it (with ONE attempt there is nothing older to fall back
    to, so it stays red either way). Mutation check: restore the `ordered = [...]` filter and this
    test reads `('completed', 'success')` instead of raising.
    """
    payload = {
        "check_runs": [
            # newest first, exactly as the endpoint answers
            {"id": None, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure"},
            {"id": 7, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success"},
        ]
    }
    monkeypatch.setattr(ac, "gh_json", lambda args: payload)
    try:
        ac.read_gate("o/r", "a" * 40)
    except RuntimeError as exc:
        assert "integer id" in str(exc)
    else:
        raise AssertionError("a mixed payload must not fall back to an older attempt's verdict")

    # ... and the all-integer payload still orders by id, not by list position.
    monkeypatch.setattr(
        ac,
        "gh_json",
        lambda args: {
            "check_runs": [
                {"id": 9, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure"},
                {"id": 7, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success"},
            ]
        },
    )
    assert ac.read_gate("o/r", "a" * 40) == ("completed", "failure")


def test_gh_json_pins_utf8_so_a_c_locale_cannot_blank_a_real_gh_answer(monkeypatch):
    """⛔ Cycle 22: `subprocess.run(..., text=True)` decoded `gh`'s stdout with the AMBIENT locale.

    `gh --json` emits raw UTF-8 (`—` in a PR title), so under `LC_ALL=C PYTHONUTF8=0` the decode
    was `ascii` and the whole census died on `'ascii' codec can't decode byte 0xe2` — rc 2,
    "could not list PRs", for a surface that had answered perfectly. `load_record` pins the same
    argument on the record read; a guard on one of two sibling readers is a coin flip.

    The assertion is on the KWARG, deliberately: the failure needs a real child process with a
    real locale, so `tests/test_attest_census.py::test_a_c_locale_box_can_still_read_a_utf8_gh_payload`
    is the end-to-end pin and this one keeps the seam from being removed on the way to it.
    """
    seen: dict[str, object] = {}

    class Proc:
        returncode = 0
        stdout = "[]"
        stderr = ""

    def fake_run(*a, **k):
        seen.update(k)
        return Proc()

    monkeypatch.setattr(ac.subprocess, "run", fake_run)
    assert ac.gh_json(["pr", "list"]) == []
    assert seen.get("encoding") == "utf-8", seen
    assert seen.get("text") is True, seen


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
            # ⛔ A resolvable `details_url` is now REQUIRED for a GREEN to be reported (see the §622
            # fallback in `read_gate`): a group this census cannot attribute is UNKNOWN, never GREEN.
            # The fixture carries one so the filter is what this test measures, not the fallback.
            {"id": 10, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success",
             "app": {"slug": "github-actions"},
             "details_url": "https://github.com/o/r/actions/runs/77/job/88"},
        ]
    }
    monkeypatch.setattr(ac, "gh_json", lambda args: payload)
    assert ac.read_gate("o/r", "a" * 40) == ("completed", "success")


def test_read_gate_refuses_a_green_whose_workflow_identity_is_unresolvable(monkeypatch):
    """⛔ Cycle 26, P2: this function implemented §622's GROUPING but not its FALLBACK.

    §622: *"an unresolvable `details_url` makes that group `UNKNOWN` (exit 2), **never GREEN**"* — and
    the repo's two canonical readings say the same in code (`tools/merge_throughput.py`: *"a GREEN is
    unprovable and is UNKNOWN, never GREEN"*; `tools/ci_verdict.py`). Measured pre-fix with two
    attempts sharing `details_url: null`, an older red and a newer green: `read_gate` answered
    `('completed', 'success')` where `merge_throughput` answered `UNKNOWN`, and `classify` turned that
    into `OK` — "gate green; no re-post owed" — on the attestation gate itself. That is a false PASS on
    the one predicate whose job is to refuse one.

    `details_url` is schema-legal as `null` (the CheckRun schema has it required **and** nullable), so
    this is a payload GitHub can hand back, not a hypothetical. Today only one workflow publishes the
    name, which makes the failure LATENT rather than absent — and a latent false GREEN here is exactly
    what this tool exists to refuse.
    """
    for share_null in (True, False):
        payload = {
            "check_runs": [
                {"id": 2, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure",
                 "app": {"slug": "github-actions"}},
                {"id": 3, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success",
                 "app": {"slug": "github-actions"}},
            ]
        }
        if not share_null:
            # the SINGLETON green shape, which §622 refuses for the same reason
            payload = {"check_runs": [payload["check_runs"][1]]}
        monkeypatch.setattr(ac, "gh_json", lambda args, p=payload: p)
        try:
            ac.read_gate("o/r", "a" * 40)
        except RuntimeError as exc:
            assert "workflow identity" in str(exc), exc
        else:
            raise AssertionError("a GREEN from an unattributable group must be UNKNOWN, not reported green")

    # ... and the SAME payload with a resolvable identity still answers, so this is an attribution test
    # and not a ban on reporting success.
    monkeypatch.setattr(
        ac, "gh_json",
        lambda args: {"check_runs": [
            {"id": 3, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success",
             "app": {"slug": "github-actions"},
             "details_url": "https://github.com/o/r/actions/runs/1001/job/9001"},
        ]},
    )
    assert ac.read_gate("o/r", "a" * 40) == ("completed", "success")
    # ⛔ A RED is not a positive claim about the workflow, so it is NOT refused — turning a real red
    # into UNREADABLE would over-block in the direction that hides a refusal a lane needs to see.
    monkeypatch.setattr(
        ac, "gh_json",
        lambda args: {"check_runs": [
            {"id": 3, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure",
             "app": {"slug": "github-actions"}},
        ]},
    )
    assert ac.read_gate("o/r", "a" * 40) == ("completed", "failure")
    # The canonical run-id shapes (`/runs/<id>` ending the URL, `?query`, `/job/<id>`) all resolve.
    for url in (
        "https://github.com/o/r/actions/runs/1001/job/9001",
        "https://github.com/o/r/actions/runs/1001",
        "https://github.com/o/r/actions/runs/1001?check_suite_id=5",
    ):
        monkeypatch.setattr(
            ac,
            "gh_json",
            lambda args, u=url: {"check_runs": [
                {"id": 3, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success",
                 "app": {"slug": "github-actions"}, "details_url": u},
            ]},
        )
        assert ac.read_gate("o/r", "a" * 40) == ("completed", "success"), url


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


def test_a_c_locale_cannot_turn_a_good_run_into_exit_1(tmp_path):
    """The artifact's BYTES must not depend on the ambient locale, and no encoding failure may
    escape as rc 1 — the published contract is `0`/`2`, never `1`.

    Measured shape (review cycle 14, the finding this test closes): under
    `LC_ALL=C PYTHONCOERCECLOCALE=0 PYTHONUTF8=0`, `Path.write_text(text)` with NO `encoding=`
    raises `UnicodeEncodeError` — a `ValueError`, **not** an `OSError` — so the `except OSError`
    around the write let it escape as a traceback and rc 1, AFTER a run that had already read
    everything correctly. A branch name or a `gh` stderr line carrying one non-ASCII character is
    the whole trigger, and the file would otherwise have been the report every downstream reader
    consumes (the pinned markdownlint, `verify_census_document`, a lane grepping rows): UTF-8 by
    contract, so its encoding cannot be whatever the invoking shell happened to export.

    Runs the real entry point in a subprocess on purpose: the default encoding of `open()` is
    decided by the C library at interpreter start-up and cannot be monkeypatched in-process, so an
    in-process test could not fail here. A `gh` shim supplies the payload — no network.
    """
    shim = tmp_path / "bin"
    shim.mkdir()
    payload = json.dumps(
        [{"number": 5, "headRefOid": LIVE, "headRefName": "fix/naïve-ünicode", "title": "t"}]
    )
    gh = shim / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s' '" + payload + "'\n", encoding="utf-8")
    gh.chmod(0o755)
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    out = tmp_path / "o.md"
    # Drop every locale/encoding variable the ambient shell may export, then force the C locale:
    # a leftover `PYTHONIOENCODING`/`PYTHONUTF8` would make this test vacuous on the box that runs it.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONCOERCECLOCALE", "LC_ALL", "LANG")
    }
    env.update(
        {
            "PATH": shim.as_posix() + os.pathsep + env.get("PATH", ""),
            "LC_ALL": "C",
            "LANG": "C",
            "PYTHONCOERCECLOCALE": "0",
            "PYTHONUTF8": "0",
        }
    )
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--reviews-dir", str(reviews), "--no-gate", "--out", str(out)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"a C-locale box turned a good run into rc {proc.returncode} "
        f"(contract: 0 or 2, never 1)\nstdout={proc.stdout[-2000:]!r}\nstderr={proc.stderr[-2000:]!r}"
    )
    assert out.exists(), f"rc 0 but no report was written: {sorted(p.name for p in tmp_path.iterdir())}"
    # Decoded as UTF-8 deliberately: mojibake or a locale-encoded byte would not decode here.
    assert "naïve-ünicode" in out.read_bytes().decode("utf-8")


def test_cell_sanitises_a_lone_surrogate_so_the_document_stays_encodable():
    """The digest, the file write and the stdout all encode — so one unencodable code point
    anywhere in a dynamic cell killed a census that had read everything correctly.

    MEASURED (review cycle 15): RFC 8259 permits a lone surrogate, `json.loads` accepts one, and a
    hand-written record carrying `"evidence": "comment:1 \\ud800"` reached the table. `_content_digest`
    encodes the WHOLE document, so `.encode("utf-8")` raised `UnicodeEncodeError` inside `render()`
    — BEFORE the `except (OSError, ValueError)` around the write could see it — and the tool exited
    1, the one code this file publishes as impossible. `print(text)` raised on the same code point.

    Mutation proof: remove the `.encode("utf-8", "backslashreplace")` half of `_cell` and the
    `render(...)` call below raises `UnicodeEncodeError` instead of returning a document.
    """
    sanitised = ac._cell("comment:1 \ud800")
    sanitised.encode("utf-8")  # the encode that used to raise, one layer down
    assert "\\ud800" in sanitised, "the evidence must stay READABLE, not silently dropped"

    record = ac.Record(
        path=Path("daniel-ospina-tortoise-5.json"),
        head_sha=LIVE,
        mint="fresh",
        verdict="clean",
        evidence="comment:1 \ud800",
    )
    row = ac.Row(
        pr=5,
        head=LIVE,
        branch="b\ud800",
        title="t\ud800",
        record=record,
        gate_status="completed",
        gate_conclusion="failure",
        gate_checked=True,
    )
    row.outcome, row.why = ac.classify(LIVE, record, "completed", "failure")
    assert row.why, "precondition: the surrogate must actually reach a rendered cell"

    text = ac.render([row], "daniel-ospina/tortoise", "2026-11-04T00:00:00Z")
    text.encode("utf-8")  # `_content_digest`'s own encode: this is the line that used to raise
    assert ac.verify_census_document(text) == "", "and the document must still prove its provenance"


def test_an_unrenderable_character_is_rc_2_not_a_traceback(monkeypatch, tmp_path):
    """The belt behind `_cell`: a value that reaches `render` WITHOUT passing a cell (argv, e.g.
    `--repo $'\\ud800'`) must still land inside the published `0`/`2` contract.

    Before the guard, `render` was called OUTSIDE every handler, so its `ValueError` escaped `main`
    as a traceback and rc 1. Pins the exit code, not the message.

    ⛔ The escape is DOUBLED in this docstring, and that is load-bearing (review cycle 23): CPython
    encodes docstrings to UTF-8 at COMPILE time (CPython #142411, 3.13+), so a docstring containing a
    LONE SURROGATE raises out of `compile()` and the whole module fails to load — measured at rc 1 on
    CPython 3.14, the code this file publishes as impossible. `test_every_docstring_is_utf8_encodable`
    below is the class-wide pin; the literal in the test BODY is fine (it is runtime data, not source
    text).
    """

    def _boom(*_args, **_kwargs):
        raise UnicodeEncodeError("utf-8", "x\ud800", 0, 1, "surrogates not allowed")

    monkeypatch.setattr(ac, "gh_json", lambda args: [])
    monkeypatch.setattr(ac, "render", _boom)
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 2
    assert not (tmp_path / "o.md").exists()


def test_a_lone_surrogate_in_a_record_cannot_turn_a_good_census_into_exit_1(tmp_path):
    """The end-to-end measurement of the same defect: the REAL entry point, a record on disk whose
    evidence carries a lone surrogate, `gh` shimmed — no network.

    Runs in a subprocess because the claim under test is the process's EXIT CODE (`0`/`2`, never
    `1`), which an in-process assertion can only approximate. On the pre-fix tree this returns 1
    with a `UnicodeEncodeError` traceback and writes no report.
    """
    shim = tmp_path / "bin"
    shim.mkdir()
    payload = json.dumps([{"number": 5, "headRefOid": LIVE, "headRefName": "b", "title": "t"}])
    gh = shim / "gh"
    gh.write_text("#!/bin/sh\nprintf '%s' '" + payload + "'\n", encoding="utf-8")
    gh.chmod(0o755)
    reviews = tmp_path / "reviews"
    reviews.mkdir()
    # Written as literal JSON text on purpose: `\ud800` is a legal RFC 8259 escape, which is
    # exactly why a hand-written or `gh`-relayed record can carry one.
    (reviews / "daniel-ospina-tortoise-5.json").write_text(
        '{"repo":"daniel-ospina/tortoise","pr":5,"head_sha":"' + LIVE + '","mint":"fresh",'
        '"verdict":"clean","evidence":"comment:1 \\ud800"}',
        encoding="utf-8",
    )
    out = tmp_path / "o.md"
    env = dict(os.environ)
    env["PATH"] = shim.as_posix() + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--reviews-dir", str(reviews), "--no-gate", "--out", str(out)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"a lone surrogate in a record turned a good run into rc {proc.returncode} "
        f"(contract: 0 or 2, never 1)\nstdout={proc.stdout[-2000:]!r}\nstderr={proc.stderr[-2000:]!r}"
    )
    text = out.read_bytes().decode("utf-8")
    assert "\\ud800" in text, "the sanitised evidence must appear, so the row still says what it read"
    assert ac.verify_census_document(text) == "", "and the written report must still verify"


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


def test_the_unqualified_marker_absence_claim_cannot_reappear_in_the_emitter():
    """Cycle 5 removed a sentence that read as a guard but could not fail, and left the same
    defect behind: three tests asserted an "absence" phrase NO code path can emit, so those
    lines held on every tree, including one that had re-introduced the claim.

    The claim they meant to forbid is real — telling a reader "no marker was ever published"
    when the gate never read markers (cycle 3) or failed before reading them (cycle 6) — so the
    pin lives where it CAN fail: the emitter's own source. Putting the phrase back into
    `tools/attest_census.py` reds this; the runtime tests above assert the QUALIFIED clauses that
    must actually appear (`gate reported nothing readable on this head`, `whether a published
    marker exists is answered by the gate's own annotation`, `published-marker surface was NOT`).
    """
    assert "found no head-bound published marker" not in TOOL.read_text()


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


# ── the artifact's own provenance: a digest of ITS OWN BODY (#10687 cycle 13) ────────
#
# `operations/attest-census.md` is COMMITTED, and the body of this PR promises it is
# "regenerated, never hand-edited — the file is the authority". At the review head that was
# false: the header read `generated 2026-11-03T05:22:14Z` while lines 15-19 had been reflowed
# BY HAND eight hours later, so a lane filtering rows by that stamp read a population older
# than the document it sat in. A run-time timestamp can never notice an edit after the run;
# a digest of the body can, offline and without `gh`. Two tests: the artifact is checked as
# committed, and the check itself is proved to discriminate.


def test_the_committed_census_matches_its_own_content_digest():
    """The committed snapshot must be exactly what `render` emitted.

    This FAILS on a hand-edited census that did NOT also recompute the header hex — including the
    prose-only edit that produced the review finding (no row moved, so nothing else in the suite
    could see it) — and it fails on a document that carries no digest at all, because absence
    proves nothing.

    ⛔ It is NOT a provenance proof, and cycle 15 measured the difference: the digest is unkeyed and
    its algorithm is in this repo, so an edit that re-derives the hex afterwards verifies. This test
    answers "was this file edited by accident after generation", never "did `render` emit these
    rows" — see the qualified prose `render` emits, and `verify_census_document`'s own docstring.
    """
    ac = _load()
    committed = Path(__file__).resolve().parent.parent / "operations" / "attest-census.md"
    assert committed.exists(), f"{committed} is the committed snapshot this PR promises"

    reason = ac.verify_census_document(committed.read_text(encoding="utf-8"))
    assert reason == "", (
        f"{committed} does not match its own header digest → {reason}. "
        "Regenerate it with `uv run python tools/attest_census.py`; never edit it by hand."
    )


def test_a_hand_edit_anywhere_in_the_body_breaks_the_digest():
    """Non-vacuity: the three shapes a hand edit actually takes.

    The review's incident was a PROSE edit (the `Totals:` line plus the RESTORE paragraph),
    so the digest must cover prose and not merely the table. The row edit and the appended
    footnote are the other two shapes, and the undigested document is the pre-fix tree.
    """
    ac = _load()
    rows = [
        ac.Row(pr=1, head=LIVE, branch="b", title="t", record=None, outcome=ac.IN_FLIGHT, why="w"),
        ac.Row(pr=2, head=LIVE, branch="b", title="t", record=None, outcome=ac.OK, why="w"),
    ]
    text = ac.render(rows, "o/r", "2026-11-03T00:00:00Z")
    assert ac.verify_census_document(text) == "", "a freshly rendered document must verify"

    edits = {
        # the measured incident: prose re-stated by hand, no row touched
        "prose": text.replace("IN-FLIGHT=1 · OK=1", "IN-FLIGHT=2 · OK=1"),
        "row": text.replace("| #1 |", "| #9 |"),
        "appended": text + "\n> hand-written footnote\n",
    }
    for name, edited in edits.items():
        assert edited != text, f"the {name} edit did not apply — this assertion would be vacuous"
        reason = ac.verify_census_document(edited)
        assert "edited after generation" in reason, f"the {name} edit went undetected: {reason!r}"

    # And the pre-fix shape — no digest line at all — is REFUSED, never accepted as clean.
    undigested = "\n".join(
        ln for ln in text.splitlines() if "content sha256" not in ln
    )
    assert "no `content sha256`" in ac.verify_census_document(undigested)


def test_a_record_containing_the_digest_placeholder_still_verifies():
    """`render` substitutes the placeholder ONCE, in the header — never in record text.

    A registry record is arbitrary JSON from disk, so its `why` can contain the token. If
    `render` substituted every occurrence, the header hex plus the record's own hex could
    never be re-derived into the pre-digest text, and a legitimately generated census would
    report itself hand-edited.
    """
    ac = _load()
    hostile = ac._DIGEST_PLACEHOLDER
    rec = ac.Record(path=Path("/tmp/r-1.json"), head_sha=LIVE, mint="fresh", verdict="clean",
                    evidence="comment:1")
    row = ac.Row(pr=1, head=LIVE, branch="b", title="t", record=rec, outcome=ac.RE_REVIEW, why=hostile)
    text = ac.render([row], "o/r", "2026-11-03T00:00:00Z")

    assert hostile in text, "the record's own text must survive verbatim"
    assert ac.verify_census_document(text) == "", "a record containing the token must still verify"


def test_a_repo_containing_the_digest_placeholder_still_verifies():
    """The OTHER field that can carry the token, and the one that sits ahead of the digest.

    MEASURED (#10687 review cycle 16): the header line emits `repo` FIRST and the digest slot last
    on the same line, so `body.replace(token, digest, 1)` filled the repo field and left the
    header's slot literal — `--repo '__CENSUS_CONTENT_SHA256__'` exited 0 and wrote a census that
    refuses itself ("no `content sha256` in the header"). The substitution is now by INDEX, so the
    invariant holds for every value of an argv field rather than for every value except one.

    Mutation proof: restore `body.replace(_DIGEST_PLACEHOLDER, digest, 1)` in `render` and this
    test fails on the verifier reason below.
    """
    ac = _load()
    text = ac.render([], ac._DIGEST_PLACEHOLDER, "2026-11-04T00:00:00Z")

    header = text.splitlines()[2]
    assert re.search(r"content sha256 `[0-9a-f]{64}`\.$", header), (
        f"the header must carry the real digest, not the token: {header!r}"
    )
    assert ac.verify_census_document(text) == "", "and it must verify, not refuse itself"
    # The other half of the same line: a repo whose text LOOKS like a digest must not be the span
    # the verifier reads (the pattern is end-of-line anchored for exactly this).
    lookalike = "r`content sha256 `" + "0" * 64 + "`."
    assert ac.verify_census_document(ac.render([], lookalike, "2026-11-04T00:00:00Z")) == ""


def test_a_repo_that_injects_a_line_cannot_displace_the_header_digest():
    """`repo` is the one field interpolated into the header, and the header must be ONE LINE.

    MEASURED (#10687 review cycle 17): the digest is read back by an END-OF-LINE anchored pattern,
    so a repo carrying a newline put an earlier digest-shaped span in front of the real one and a
    genuinely rendered document refused itself (`header says 000…, body hashes to 0d78aa…`).
    Unreachable through `main` — `gh pr list --repo` refuses the slug first — so this pins the
    LIBRARY boundary the suite and any future caller reach directly.

    Mutation proof: delete `repo = _cell(repo)` in `render` and this fails on the verifier reason.
    """
    ac = _load()
    hostile = "a\ncontent sha256 `" + "0" * 64 + "`.\nrest"
    text = ac.render([], hostile, "2026-11-04T00:00:00Z")

    assert len([ln for ln in text.splitlines() if ln.startswith("Repo `")]) == 1, (
        "the header must be exactly one line whatever the repo value contains"
    )
    # ⛔ THREE assertions, and two of them were INERT for two cycles — so state what discriminates.
    # After the fix the header element is ONE line carrying the real digest; with `repo` newline-
    # injected the first line starting with `Repo ` holds only `Repo `a` and the token lands on an
    # INJECTED line, so it does not contain `content sha256` at all. Testing `splitlines()` elements
    # for `\n` is a tautology (they cannot contain it), and "line 3 is the first `Repo ` line" is
    # true on the pre-fix tree too — measured, cycle 19, with both mutations.
    header_line = next(ln for ln in text.splitlines() if ln.startswith("Repo `"))
    assert "content sha256 `" in header_line, (
        f"the header line must carry the digest itself, not merely start the header: {header_line!r}"
    )
    assert text.splitlines()[2] == header_line, "the header must be line 3 of the document"
    assert ac.verify_census_document(text) == "", "and the document must not refuse itself"


def test_a_closed_stdout_pipe_is_rc_2_not_1(monkeypatch, tmp_path):
    """`BrokenPipeError` is an `OSError`, so the old `except UnicodeEncodeError` let it escape as rc 1.

    MEASURED (#10687 review cycle 17): `tools/attest_census.py | head -1` reported rc 1 for a run
    that had ALREADY written its report — the one exit code the module docstring publishes as
    impossible. The stdout guard now catches the same pair (`OSError`, `ValueError`) the `--out`
    write does.
    """
    ac = _load()
    monkeypatch.setattr(ac, "gh_json", lambda args: [])

    class _ClosedPipe:
        def reconfigure(self, **_kwargs):  # `main` suppresses a stream without it — be honest
            return None

        def write(self, _data):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            return None

    monkeypatch.setattr(ac.sys, "stdout", _ClosedPipe())
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 2
    assert (tmp_path / "o.md").exists(), "the report was written before the pipe broke"


def test_a_flush_that_fails_only_at_exit_is_rc_2_not_120(monkeypatch, tmp_path):
    """The SIZE-DEPENDENT escape: a report under the ~8 KiB stdout buffer never flushes inside
    `print`, so the reader-closed pipe surfaced in CPython's exit-time `flush_std_files` — outside
    every handler in this file — and `Py_Exit` turned it into rc **120**.

    MEASURED (#10687 review cycle 18): the same census exited 2 with a 48-row payload and 120 with a
    1-row one. `_emit_report` now flushes inside its `try`, which is what this stub models: `write`
    succeeds, `flush` is where the pipe is found dead.
    """
    ac = _load()
    monkeypatch.setattr(ac, "gh_json", lambda args: [])

    class _BufferedPipe:
        def reconfigure(self, **_kwargs):
            return None

        def write(self, _data):  # a real buffered stream accepts this and fails later
            return len(_data)

        def flush(self):
            raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr(ac.sys, "stdout", _BufferedPipe())
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")]) == 2


def _run_with_shim(tmp_path, extra_args, stdout, preexec_fn=None, stderr=subprocess.PIPE, env_extra=None,
                   out_name="o.md", gh_stdout="[]"):
    """Run the REAL entry point against a `gh` shim, with `stdout` as the child's fd 1.

    A subprocess is not optional for these cases: one needs fd 1 CLOSED at interpreter start
    (CPython then sets `sys.stdout = None`), one needs fd 2 closed (so `sys.stderr` is None, and
    `print(file=None)` falls back to stdout), and one needs the failure to happen at SHUTDOWN,
    after `main` has returned. None of the three can be modelled by patching `sys.stdout` in-process.

    `gh_stdout` is written RAW (UTF-8 bytes on disk, emitted by `printf`), not `json.dumps`'d — the
    locale test below needs a payload that is UTF-8 on the wire exactly as real `gh` emits it, and
    `json.dumps`' `ensure_ascii=True` default would escape every non-ASCII character away and let
    the test go vacuous.
    """
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    gh = shim / "gh"
    assert "'" not in gh_stdout, "the shim body is single-quoted shell — keep the JSON quote-free"
    gh.write_text(f"#!/bin/sh\nprintf '%s' '{gh_stdout}'\n", encoding="utf-8")
    gh.chmod(0o755)
    reviews = tmp_path / "reviews"
    reviews.mkdir(exist_ok=True)
    env = dict(os.environ)
    # ⛔ `env_extra` is applied BEFORE the PATH prefix, not after: a caller's env snapshot carries
    # its own PATH, and merging it afterwards silently discards the shim — the child then runs the
    # REAL `gh`, hits the network, and fails for a reason that has nothing to do with what the test
    # is about (measured cycle 21: an LC_ALL=C arm went rc 2 on "undecodable output", 214 bytes from
    # `gh` instead of the shim's two).
    env.update(env_extra or {})
    env["PATH"] = shim.as_posix() + os.pathsep + env.get("PATH", "")
    cmd = [
        sys.executable, str(TOOL), *extra_args,
        "--reviews-dir", str(reviews), "--no-gate", "--out", str(tmp_path / out_name),
    ]
    return subprocess.run(cmd, cwd=str(tmp_path), env=env, stdout=stdout, stderr=stderr,
                          timeout=120, preexec_fn=preexec_fn)


def test_a_c_locale_box_can_still_read_a_utf8_gh_payload(tmp_path):
    """⛔ Cycle 22, end to end: `gh_json`'s `text=True` without `encoding=` decodes `gh`'s stdout
    with the AMBIENT locale. `gh --json` emits RAW UTF-8 (`—` in a PR title), so under
    `LC_ALL=C PYTHONUTF8=0` the decode is `ascii` and the census dies rc 2 — "could not list PRs",
    blaming GitHub for a locale — on a box where the answer was perfect.

    The shim emits the payload as literal UTF-8 bytes precisely so this cannot go vacuous: the
    suite's other `gh` stubs build their JSON with `json.dumps`, whose `ensure_ascii=True` default
    escapes every non-ASCII character back to `\\uXXXX`, and an escaped body decodes fine in any
    locale. Mutation check: delete `encoding="utf-8"` from `gh_json` and this reds with
    `'ascii' codec can't decode byte 0xe2` (measured on the pre-fix tree).
    """
    payload = (
        '[{"number": 1, "headRefOid": "' + LIVE + '", "headRefName": "b", '
        '"title": "a title with an em dash \u2014 in it"}]'
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONCOERCECLOCALE", "LC_ALL", "LANG")}
    env.update({"LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"})
    proc = _run_with_shim(tmp_path, [], subprocess.DEVNULL, env_extra=env, gh_stdout=payload)
    # ⛔ The payload must be UTF-8 ON THE WIRE or this test is vacuous: `json.dumps`' default
    # `ensure_ascii=True` writes `\u2014` as six ASCII bytes, which any locale decodes. Assert the
    # bytes the shim will actually emit.
    assert b"\xe2\x80\x94" in (tmp_path / "bin" / "gh").read_bytes(), "the em dash was escaped away"
    assert proc.returncode == 0, (
        f"a C-locale box must still read a UTF-8 `gh` payload (got {proc.returncode})\n"
        f"stderr={proc.stderr[-1500:]!r}"
    )
    # ⛔ Non-vacuity: the row has to have been RENDERED from that payload. An EMPTY `gh pr list`
    # answers rc 0 without ever decoding a non-ASCII byte, so rc alone would not prove the read.
    text = (tmp_path / "o.md").read_text(encoding="utf-8")
    assert "| #1 |" in text, f"no row rendered from the UTF-8 payload: {text[-800:]}"
    assert "NOTHING-TO-RESTORE" in text, f"the row did not classify: {text[-800:]}"


def test_a_closed_fd1_is_rc_2_not_1(tmp_path):
    """`1>&-` makes CPython set `sys.stdout = None`, where `print()` SILENTLY no-ops — so only the
    `flush()` in `_emit_report` raises, and `AttributeError` was not in its `except`.

    MEASURED (#10687 review cycle 19): `… --out o.md 1>&-` went **rc 0 → rc 1** on the parent commit
    versus `origin/main` — a contract regression introduced by the very call that closes the
    size-dependent escape. Both surfaces are checked, because that is exactly how the previous one
    of these hid.

    ⛔ The `expect_out` arm is not decoration. `--json` NEVER writes `--out` (it returns before the
    write), so a bare `exists()` assertion is (a) satisfied by the previous arm's leftover file and
    (b) FAILS on correct code if the arms are reordered or parametrized. Measured, cycle 21: with
    `return 2` injected at the head of the `--json` branch — the exact arm the original bug lived on
    — both arms stayed green.
    """

    def _close_fd1():
        os.close(1)

    for args, expect_out in (([], True), (["--json"], False)):
        arm = tmp_path / ("json" if args else "markdown")
        arm.mkdir()
        proc = _run_with_shim(arm, args, None, preexec_fn=_close_fd1)
        assert proc.returncode == 2, (
            f"{args or ['markdown']}: a closed fd 1 must be rc 2, never 1 or 120 "
            f"(got {proc.returncode})\nstderr={proc.stderr[-1500:]!r}"
        )
        assert b"Traceback" not in proc.stderr, proc.stderr[-1500:]
        # ⛔ The MARKDOWN arm is the one this can prove progress on: it writes `--out` before
        # emitting, so a child that bailed early leaves no file. `--json` returns BEFORE that write,
        # so its `expect_out=False` is the documented behaviour rather than a progress signal —
        # stated plainly because an assertion that reads as a pin but cannot fail on that arm is the
        # exact defect this test was accused of twice (cycles 19 and 21).
        assert (arm / "o.md").exists() is expect_out, (
            f"--out presence must be {expect_out} on the {args or ['markdown']} arm "
            f"(present={(arm / 'o.md').exists()}); on the markdown arm this is what proves the child "
            "reached the emit rather than bailing early"
        )


def test_the_c_locale_plus_a_closed_fd2_plus_a_non_ascii_out_is_rc_0(tmp_path):
    """The triad that was rc 1: closed fd 2 + C locale + a non-ASCII `--out`.

    With fd 2 closed, `sys.stderr` is None and `print(..., file=None)` falls back to STDOUT — so
    under a C-locale argv (non-ASCII `--out` decoded as lone surrogates) the courtesy message raised
    `UnicodeEncodeError` AFTER a complete census, and the process exited 1.

    ⛔ Cycle 25 (P3) CORRECTED this docstring's claim, and the correction is the useful part. It used
    to say `errors="backslashreplace"` on stdout's `reconfigure` was "that guard's ONLY mutation
    proof". At this head that is **false**: reverting only `errors=` leaves the suite GREEN, because
    cycle 22's `_say()` catches `ValueError` (of which `UnicodeEncodeError` is one) and the exit code
    is decided before the courtesy message is attempted at all. The discriminating mutation is now
    TWO-fold — remove `errors=` AND narrow `_say`'s handler to `(OSError, AttributeError)` — which
    reds here with rc 1. So this test pins the CONTRACT (a C-locale box completes at rc 0); the
    `errors=` setting is one of two independent guards behind it, and `_say` is the one carrying it
    today. Kept as-is rather than deleted because the contract is what matters, and a claim corrected
    in place beats a guard that reads stronger than it is.
    """

    def _close_fd2():
        os.close(2)

    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONCOERCECLOCALE", "LC_ALL", "LANG")}
    # The C locale makes the child decode argv as ASCII, turning `café` into lone surrogates.
    env.update({"LC_ALL": "C", "LANG": "C", "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"})
    out_name = "caf\u00e9-census.md"
    proc = _run_with_shim(
        tmp_path, [], subprocess.DEVNULL, preexec_fn=_close_fd2, stderr=None, env_extra=env,
        out_name=out_name,
    )
    assert proc.returncode == 0, (
        f"a closed fd 2 + C locale + a non-ASCII --out must be rc 0 (got {proc.returncode}); "
        "the courtesy message must be escaped, not raise"
    )
    assert (tmp_path / out_name).exists(), "the census itself must still complete"


def test_a_small_report_against_a_dead_reader_is_rc_2_not_120(tmp_path):
    """The SIZE-DEPENDENT escape, end to end: a report under the ~8 KiB stdout buffer flushes only at
    SHUTDOWN, and a dead reader there makes `_Py_Finalize` return -1 and `Py_Exit` exit **120**.

    `_emit_report`'s `flush()` + `_neutralise_stdout()` are what turn that into 2. This is the only
    test that pins `_neutralise_stdout` — delete it on a copy and this must red, because the
    in-process stub above cannot model a shutdown-time flush.
    """
    read_fd, write_fd = os.pipe()
    os.close(read_fd)  # the reader is already gone before the child starts
    try:
        proc = _run_with_shim(tmp_path, [], write_fd)
    finally:
        os.close(write_fd)
    assert proc.returncode == 2, (
        f"a dead reader on a SMALL report must be rc 2, not 120 (got {proc.returncode})\n"
        f"stderr={proc.stderr[-1500:]!r}"
    )
    assert (tmp_path / "o.md").exists(), "the report was still written to --out"


def test_the_json_surface_has_the_SAME_stdout_guard(monkeypatch, tmp_path):
    """The sibling path that drifted: `--json | jq` was unguarded while the markdown report was not,
    so it exited **1** on a closed pipe (measured, cycle 18) — the exact invariant cycle 17 claimed
    to have closed. Both branches now go through `_emit_report`, and this pins the second one.
    """
    ac = _load()
    monkeypatch.setattr(ac, "gh_json", lambda args: [])

    class _ClosedPipe:
        def reconfigure(self, **_kwargs):
            return None

        def write(self, _data):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            return None

    monkeypatch.setattr(ac.sys, "stdout", _ClosedPipe())
    assert ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--json"]) == 2


def test_a_uppercase_JSON_suffix_record_is_found_not_read_as_absent(monkeypatch, tmp_path):
    """`load_record` promises CASE-INSENSITIVE filename matching, but the directory filter was
    `name.endswith(".json")` — so a record whose EXTENSION is upper-case was never opened and the row
    reported `NOTHING-TO-RESTORE` (measured, cycle 18). The direction is the safe one (a real proof
    is under-reported, never invented), which is why it is a residual and not a gate — but a false
    absence is still the class this tool exists to refuse.
    """
    ac = _load()
    (tmp_path / "DANIEL-OSPINA-TORTOISE-7.JSON").write_text(
        '{"repo":"daniel-ospina/tortoise","pr":7,"head_sha":"' + LIVE + '","mint":"fresh",'
        '"verdict":"clean","evidence":"comment:1"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ac, "gh_json",
        lambda args: [{"number": 7, "headRefOid": LIVE, "headRefName": "b", "title": "t"}]
        if args and args[0] == "pr"
        else {"check_runs": [{"id": 5, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure"}]},
    )
    assert ac.main(["--reviews-dir", str(tmp_path), "--out", str(tmp_path / "o.md")]) == 0
    text = (tmp_path / "o.md").read_text(encoding="utf-8")
    assert "**RESTORE**" in text or "**OK**" in text, f"the upper-case record was not read: {text[-800:]}"
    assert "no record on this box" not in text


# ── the STDERR surface, and the linter-safety of a hostile evidence cell (#10687 cycle 22) ──
#
# Two P2s from the cycle-22 review, both reproduced before being fixed, and both of the same
# shape this PR keeps finding: a contract that holds on the path the author was looking at and
# not on its sibling.
#
# 1. `_neutralise_stdout` closed the shutdown-flush hole on fd 1 ONLY. `flush_std_files()`
#    flushes `sys.stdout` AND `sys.stderr`, and a failure of either makes `Py_Exit` return 120
#    (`Python/pylifecycle.c` 3.12: `:1673-1708` set the status, `:3097-3104` map it to 120), so
#    twelve unguarded `print(..., file=sys.stderr)` calls in `main` could publish a code the file
#    documents as impossible — from a census that had completed. The exit code is now decided
#    BEFORE any message is attempted, every write goes through `_say`, and `_say` neutralises fd 2
#    on failure so the shutdown flush cannot overwrite it.
#
# 2. `_cell` escaped `\`, `|`, `\r`, `\n` and lone surrogates but NOT a hard tab (MD010) and not a
#    bare URL (MD034) — and `operations/attest-census.md` is linted WHOLE by the REQUIRED `docs`
#    job with no key of its own in `config/docs-lint-baseline.json`, so ONE such record would red a
#    check the lane did not cause, on an artifact its own header forbids hand-editing.


@pytest.mark.parametrize(
    "evidence",
    [
        # The two shapes that reached the table unfixed. Both are ACCEPTED evidence shapes, not
        # exotic input: a tab arrives from any hand-written or corrupt record, and the URL is one of
        # `_EVIDENCE_REF`'s two documented forms (one of the live registry's 3,703 records uses it).
        "comment:1\tTAB",
        "https://github.com/daniel-ospina/tortoise/pull/8725#issuecomment-6194795895",
    ],
)
def test_the_pinned_linter_accepts_a_hostile_evidence_cell(evidence, tmp_path):
    """The `docs`-check half of both cycle-22 findings, measured by the LINTER rather than a model.

    MEASURED pre-fix with the pinned `markdownlint-cli2@0.23.3`:
      `comment:1\\tTAB`   -> `MD010/no-hard-tabs [Column: 84]`
      the github URL      -> `MD034/no-bare-urls Bare URL used`
    Either one is a NEW key for `operations/attest-census.md`, which the baseline does not carry, so
    a regeneration would red the REQUIRED `docs` job. The unit assertions below pin the sanitiser;
    this pins the OUTCOME the gate reads.
    """
    ac = _load()
    script = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "docs-markdownlint.sh"
    if not script.exists():
        pytest.skip("docs-markdownlint.sh not present")
    target = Path(__file__).resolve().parent.parent / "operations" / "zz-attest-census-probe.md"
    nodelist = tmp_path / "probe.nul"
    report = tmp_path / "probe.out"
    rec = ac.Record(path=Path("/tmp/r-1.json"), head_sha=LIVE, mint="fresh", verdict="clean",
                    evidence=evidence)
    row = ac.Row(pr=1, head=LIVE, branch="b", title="t", record=rec, outcome=ac.RESTORE, why="w")
    target.write_text(ac.render([row], "o/r", "2026-11-03T00:00:00Z"), encoding="utf-8")
    nodelist.write_bytes(b"./operations/zz-attest-census-probe.md\0")
    try:
        subprocess.run(
            ["bash", str(script), "--cli2", "markdownlint-cli2@0.23.3",
             "--files-from", str(nodelist), "--output", str(report), "--label", "probe"],
            cwd=Path(__file__).resolve().parent.parent, capture_output=True, timeout=300,
        )
        out = report.read_text() if report.exists() else ""
        if "Linting: 1 file" not in out:
            pytest.skip(f"pinned linter unavailable here (report: {out[:200]!r})")
        assert "Summary: 0 issues in 0 files" in out, (
            f"the pinned linter rejects the cell rendered from evidence {evidence!r}: {out}"
        )
    finally:
        target.unlink(missing_ok=True)


def test_a_hard_tab_is_flattened_to_a_space():
    """MD010 is a `_cell` gap, not a linter quirk: the sanitiser must emit no tab at all."""
    ac = _load()
    assert "\t" not in ac._cell("comment:1\tTAB"), "a hard tab reaches the table as MD010"
    assert ac._cell("comment:1\tTAB") == "comment:1 TAB"


def test_a_bare_url_becomes_an_autolink_and_is_not_double_wrapped():
    """MD034, plus the idempotence a corrupt record can reach: an already-autolinked value."""
    ac = _load()
    url = "https://github.com/daniel-ospina/tortoise/pull/8725#issuecomment-6194795895"
    assert ac._cell(url) == f"<{url}>"
    # A record whose evidence already carries an autolink must not become `<<…>>`.
    assert ac._cell(f"<{url}>") == f"<{url}>"
    # Trailing sentence punctuation stays OUT of the link, and prose around it is untouched.
    assert ac._cell(f"see {url}.") == f"see <{url}>."
    # A `comment:<id>` reference carries no URL, so the column is unchanged for the common shape.
    assert ac._cell("comment:6596200336") == "comment:6596200336"
    # ⛔ A URL whose own PATH ends in a BALANCED paren keeps it. `rstrip(".,;:!?)]")` peeled the
    # closing paren off `…/wiki/Foo_(bar)` and so pointed the link at a different page — measured
    # (#10687 cycle 22). The peel is now conditional on the parens being unbalanced.
    paren = "https://en.wikipedia.org/wiki/Foo_(bar)"
    assert ac._cell(paren) == f"<{paren}>", "a balanced trailing paren belongs to the path"
    # …and a paren that closes NOTHING is still peeled, together with any punctuation behind it.
    assert ac._cell(f"({url})") == f"(<{url}>)"
    assert ac._cell(f"{url}).") == f"<{url}>)."
    # A scheme with no host is not a URL: nothing is peeled out of it, and it is still autolinked so
    # a corrupt record cannot emit a bare URL (MD034) — the value survives verbatim instead.
    assert ac._cell("https://.") == "<https://.>"


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="/dev/full is Linux-only")
def test_an_unwritable_stderr_does_not_turn_a_complete_census_into_rc_120(tmp_path):
    """A census that READ EVERYTHING CORRECTLY must not report a crash because its message failed.

    MEASURED pre-fix (#10687 cycle 22) on the real entry point:

        attest_census.py --reviews-dir <empty> --no-gate --out /tmp/zz.md 2>/dev/full
        -> the full report printed, /tmp/zz.md written (2,945 bytes), exit **120**

    with `OSError: [Errno 28] No space left on device` raised by the courtesy
    `print(f"attest_census: wrote {out}", file=sys.stderr)`. A caller keying on the published
    `0`/`2` contract therefore reads "crash" from a successful run — and ENOSPC is not exotic here:
    this PR's own body records it on the x64 pool (#10713).
    """
    sink = open("/dev/full", "wb")  # noqa: SIM115 — the child owns it for its whole life
    try:
        proc = _run_with_shim(tmp_path, [], subprocess.DEVNULL, stderr=sink)
    finally:
        sink.close()
    assert proc.returncode == 0, (
        f"an unwritable stderr must not decide the exit code (got {proc.returncode}, want 0); "
        "the census completed and wrote its report"
    )
    assert (tmp_path / "o.md").exists(), "the artifact must still be written"


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="/dev/full is Linux-only")
def test_an_unwritable_stderr_cannot_turn_the_out_failure_into_rc_120(tmp_path):
    """The OTHER side of the same hole: `--out` unwritable is documented as 2, not 120.

    Pre-fix, both writes raised (a full disk makes `--out` and stderr fail together), and the
    courtesy message's `OSError` escaped `main` — so "could not write the report" published 120 and
    became indistinguishable from a crash, which is the one confusion the 0/2 contract exists to
    prevent.
    """
    blocked = tmp_path / "blocked"
    blocked.mkdir()  # an `--out` that is a DIRECTORY: IsADirectoryError, an OSError
    sink = open("/dev/full", "wb")  # noqa: SIM115
    try:
        proc = _run_with_shim(tmp_path, [], subprocess.DEVNULL, stderr=sink, out_name="blocked")
    finally:
        sink.close()
    assert proc.returncode == 2, (
        f"an unwritable --out with an unwritable stderr is rc 2 (got {proc.returncode})"
    )


def test_a_dead_stderr_pipe_does_not_turn_a_complete_census_into_rc_120(tmp_path):
    """The `BrokenPipeError` half of the same class — a stderr PIPE whose reader is already gone.

    This is a different state from `2>/dev/full` (an unwritable file) and from fd 2 CLOSED
    (`sys.stderr is None`): here `sys.stderr` is a real object and the WRITE raises `BrokenPipeError`,
    which is an `OSError`. stderr is line-buffered, so it raises at the `print`, not at shutdown —
    `_say`'s handler must catch it exactly as the stdout path does. The read end is closed BEFORE the
    child starts, so the child cannot win the race.
    """
    read_fd, write_fd = os.pipe()
    os.close(read_fd)  # nobody will ever read: the first write gets EPIPE
    with os.fdopen(write_fd, "wb") as sink:  # fdopen OWNS write_fd; closing it here is the only close
        proc = _run_with_shim(tmp_path, [], subprocess.DEVNULL, stderr=sink)
    assert proc.returncode == 0, (
        f"a dead stderr pipe must not decide the exit code (got {proc.returncode}, want 0); "
        "the census completed and the child's stderr was sent to a pipe with no reader"
    )
    assert (tmp_path / "o.md").exists(), "the artifact must still be written"


def test_a_closed_fd2_still_completes_rc_0(tmp_path):
    """The adjacent state, kept next to its siblings so the three cannot drift apart one at a time.

    fd 2 CLOSED makes `sys.stderr = None`, where `print(..., file=None)` falls back to STDOUT — a
    DIFFERENT code path from an unwritable fd 2 and from a dead pipe, and the one cycle 21 pinned
    for the `backslashreplace` fix.
    """

    def _close_fd2():
        os.close(2)

    proc = _run_with_shim(tmp_path, [], subprocess.DEVNULL, preexec_fn=_close_fd2)
    assert proc.returncode == 0, f"a closed fd 2 with a complete census is rc 0 (got {proc.returncode})"
    assert (tmp_path / "o.md").exists(), "the artifact must still be written"


# ── review cycle 23: the compile-time docstring, the argparse stderr, the path normalisation ──


def test_every_docstring_in_this_suite_and_its_tool_is_utf8_encodable():
    r"""⛔ Cycle 23, P2: a LONE SURROGATE in a DOCSTRING is a COMPILE-TIME failure, not a runtime one.

    CPython encodes a docstring to UTF-8 while compiling (CPython #142411, 3.13+), so
    `compile()` raises `UnicodeEncodeError: surrogates not allowed` before any bytecode exists —
    no `try`/`except`, no `raise SystemExit(2)` guard and no `main()` can observe it. Measured:
    with a single backslash in `_cell`'s docstring, `python tools/attest_census.py --help` exited
    **1** on CPython 3.14 while `origin/main`'s copy compiled fine — the exact code this file
    publishes as impossible, from a run that could not even load.

    `pyproject.toml` declares `requires-python >= 3.12`, so 3.13/3.14 are SUPPORTED interpreters:
    this is a supported-configuration refusal, not an exotic one. Fix is one doubled backslash
    (`\\ud800`, the escape as TEXT), which is why the pin is class-wide rather than per-site: the
    defect is invisible on the pinned 3.12 that every CI leg uses, so only a walk finds the next one.
    """
    offenders: list[str] = []
    for path in (TOOL, Path(__file__).resolve()):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            doc = ast.get_docstring(node, clean=False)
            if doc is None:
                continue
            try:
                doc.encode("utf-8")
            except UnicodeEncodeError as exc:
                offenders.append(f"{path.name}:{node.lineno} {getattr(node, 'name', '<module>')}: {exc}")
    assert offenders == [], (
        "a docstring holding a lone surrogate cannot be COMPILED on CPython 3.13+ — double the "
        "backslash so the escape is text, not a surrogate character: " + "; ".join(offenders)
    )


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="/dev/full is Linux-only")
def test_a_usage_error_with_an_unwritable_stderr_is_rc_2_not_120(tmp_path):
    """⛔ Cycle 23, P3: `argparse` was the one stderr write the cycle-22 sweep could not reach.

    `parse_args` prints `usage: … error: …` to STDERR and then raises `SystemExit(2)` — outside
    every handler in the file. With an unwritable stderr that write raised `OSError(ENOSPC)`, the
    traceback could not print either, and CPython's shutdown flush published **120**.

    A usage error is a caller error and the file's contract publishes 2, so 2 is the honest code.
    Mutation check: unwrap `parse_args` and this reds with 120.
    """
    sink = open("/dev/full", "wb")  # noqa: SIM115 — the child owns fd 2 for its whole life
    try:
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--definitely-not-a-flag"],
            stdout=subprocess.DEVNULL, stderr=sink, timeout=120,
        )
    finally:
        sink.close()
    assert proc.returncode == 2, (
        f"a usage error with an unwritable stderr must be rc 2, not 120 (got {proc.returncode})"
    )
    # ... and a well-formed `--help` still exits 0: catching `SystemExit` would break it.
    ok = subprocess.run(
        [sys.executable, str(TOOL), "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120
    )
    assert ok.returncode == 0, f"--help must still exit 0 (got {ok.returncode})"
    # ... AND it must stay 0 when its own output cannot be written. `argparse` writes help to a
    # BLOCK-buffered stdout, so the failure lands in CPython's shutdown flush and used to exit 120
    # (review cycle 24, measured both ways: `--help >/dev/full` and `--help | true` under `pipefail`).
    full = open("/dev/full", "wb")  # noqa: SIM115
    try:
        blocking = subprocess.run(
            [sys.executable, str(TOOL), "--help"], stdout=full, stderr=subprocess.DEVNULL, timeout=120
        )
    finally:
        full.close()
    assert blocking.returncode == 0, (
        f"--help with an unwritable stdout must be 0, not 120 (got {blocking.returncode})"
    )


def test_evidence_attribution_cannot_be_redirected_by_a_path_traversal_or_a_query(tmp_path):
    """⛔ Cycle 23, P3: the cycle-22 slug guard was bypassable two ways, both reaching `RESTORE`.

    * `…/daniel-ospina/tortoise/../evil-org/evil-repo#issuecomment-1` — the FIRST TWO segments are
      this repo, so a slug test that only looks at those passed while the URL actually normalises
      to a DIFFERENT repository;
    * `…/daniel-ospina/tortoise/pull/999?x=1#issuecomment-1` — `/pull/(\\d+)(?:/|$)` could not match
      across the query string, so "no PR number found" was silently read as "no PR to disagree with".

    Both classify identically to a genuine proof, which is what makes them the laundering this
    guard exists to refuse.

    ⛔ Cycle 24 (P2) FALSIFIED the mutation claim this docstring used to make, and the correction is
    the reason two of the vectors below exist. "Drop the traversal rejection and these go back to
    `RESTORE`" was **false**: every vector above is also caught by the slug / PR-page checks that
    follow, so the whole suite stayed green with the traversal block deleted — an unpinned guard
    behind a docstring that invited exactly the refactor that would remove it. Measured, the shape
    only the traversal check refuses is a traversal placed AFTER a MATCHING `pull/<pr>`:
    `…/tortoise/pull/7/../../evil-org/evil-repo#issuecomment-1` — the page check sees `pull/7` and is
    satisfied, and only normalising the path reveals a different repository. With the guard deleted
    that shape measures `real_evidence=True, classify=RESTORE`; with it, `NOTHING-TO-RESTORE`.
    """
    tpl = (
        '{"repo":"daniel-ospina/tortoise","pr":7,"head_sha":"' + LIVE + '","mint":"fresh",'
        '"verdict":"clean","evidence":"%s"}'
    )
    for crafted in (
        "https://github.com/daniel-ospina/tortoise/../evil-org/evil-repo#issuecomment-1",
        "https://github.com/daniel-ospina/tortoise/%2e%2e/evil-org/evil-repo#issuecomment-1",
        # ⛔ These two are the ONLY shapes the traversal check refuses: the page segment is a
        # matching `/pull/7`, so every other check is satisfied and only path normalisation
        # exposes the different repository. Delete the traversal block and these become RESTORE
        # (measured) — which is what makes them the pin rather than the five above.
        "https://github.com/daniel-ospina/tortoise/pull/7/../../evil-org/evil-repo#issuecomment-1",
        "https://github.com/daniel-ospina/tortoise/pull/7/%2e%2e/%2e%2e/evil-org/evil-repo#issuecomment-1",
        "https://github.com/daniel-ospina/tortoise/pull/999?x=1#issuecomment-1",
        "https://github.com/daniel-ospina/tortoise/issues/999#issuecomment-1",
        "https://github.com/daniel-ospina/tortoise/settings#issuecomment-1",
        # ⛔ Cycle 25 (P1): the guard FAILED OPEN on a URL it could not parse. `_EVIDENCE_REF`'s two
        # path segments may contain a `/`, `_GITHUB_URL_SLUG`'s may not, and the difference was read
        # as "attributed, nothing to check". GitHub COLLAPSES an empty segment, so the first two
        # resolve — 200 OK — to a DIFFERENT repository while the canonical spelling of that same repo
        # is refused. All three measured `RESTORE` before the fix.
        "https://github.com/daniel-ospina//agent-infra#issuecomment-1",
        "https://github.com/daniel-ospina//agent-infra/pull/999#issuecomment-1",
        "https://github.com//evil-org/evil-repo/pull/1#issuecomment-1",
        # ⛔ Cycle 25 (P2): `isdigit()` is true for characters `int()` REJECTS, and the conversion was
        # outside every handler — one such record made the whole census exit 1 with a traceback.
        "https://github.com/daniel-ospina/tortoise/pull/\u00b2#issuecomment-1",
    ):
        (tmp_path / "daniel-ospina-tortoise-7.json").write_text(tpl % crafted, encoding="utf-8")
        got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
        assert got is not None and got.error == "", (crafted, got and got.error)
        assert got.real_evidence is False, crafted
        outcome, why = ac.classify(LIVE, got, *RED)
        assert outcome != ac.RESTORE, (crafted, outcome, why)
        # The row must name WHICH artifact is wrong, not fall back to a generic message.
        assert (
            "not daniel-ospina/tortoise" in why
            or "not #7" in why
            or "no attributable repository" in why
            or "not a number" in why
            or "path-traversal segment" in why
            or "not a pull request" in why
        ), (crafted, why)

    # The genuine shapes still restore: this is an attribution test, not a ban. `comment:<id>` is
    # here because the attribution logic must NOT touch a pointer that carries no slug — the first
    # cut of the cycle-25 fix refused it as "unattributable", which would have "fixed" a fail-open
    # by breaking the registry's own dominant shape.
    for own in (
        "comment:6593768046",
        "https://github.com/daniel-ospina/tortoise/pull/7#issuecomment-1",
        "https://github.com/daniel-ospina/tortoise/pull/7/files#discussion_r5",
        "https://github.com/daniel-ospina/tortoise/issues/7#issuecomment-1",
    ):
        (tmp_path / "daniel-ospina-tortoise-7.json").write_text(tpl % own, encoding="utf-8")
        got = ac.load_record(tmp_path, "daniel-ospina", "tortoise", 7)
        assert got is not None and got.real_evidence is True, own
        assert ac.classify(LIVE, got, *RED)[0] == ac.RESTORE, own


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="/dev/full is Linux-only")
def test_an_in_process_caller_with_buffered_stdout_gets_no_oserror(monkeypatch, tmp_path):
    """⛔ Cycle 24, P3: `sys.stdout.reconfigure` FLUSHES, and its `suppress` tuple was the one place
    in this file missing `OSError`.

    `_neutralise_stdout`, `_neutralise_stderr`, `_say` and `_emit_report` all handle the triple
    `(OSError, ValueError, AttributeError)`; `reconfigure` suppressed only the last two. A caller
    that wrote to stdout before calling `main` — and this suite calls `main` in-process in dozens of
    places — leaves that data BUFFERED, so `reconfigure`'s flush hits the broken stream and
    `OSError(ENOSPC)` escaped `main` as an exception. Measured pre-fix: "ESCAPED: OSError [Errno 28]
    No space left on device".

    Unreachable from the CLI (the buffer is empty at interpreter start, so every fd-closed /
    `/dev/full` arm correctly measures 0 or 2), which is why this is a library-boundary pin rather
    than an end-to-end one. Mutation check: remove `OSError` from that `suppress` tuple and this
    raises instead of asserting a code.
    """
    sink = open("/dev/full", "w", encoding="utf-8")  # noqa: SIM115
    try:
        sink.write("x" * 100)  # stays in the buffer: nothing raises yet, that is the point
        monkeypatch.setattr(ac.sys, "stdout", sink)
        monkeypatch.setattr(ac, "gh_json", lambda args: [])
        rc = ac.main(["--reviews-dir", str(tmp_path), "--no-gate", "--out", str(tmp_path / "o.md")])
    finally:
        monkeypatch.undo()
        with contextlib.suppress(OSError):
            sink.close()
    # A failed stdout write is the CODE 2 (the report could not be emitted), never a raise.
    assert rc == 2, f"a failed stdout write must be rc 2, not an exception (got {rc!r})"
    assert (tmp_path / "o.md").exists(), "the artifact is written before the stdout emit"


def test_read_gate_does_not_let_one_workflow_mask_a_red_attempt_in_another(monkeypatch):
    """§622 / AGENTS.md: group by `(app.slug, workflow, job name)`, then apply POLARITY.

    ⛔ A NAME-ONLY read merges two workflows that publish the same job name, so the newest of one
    masks the other. Measured (review cycle 24): the payload below — an older `success` from run 1
    and a newer `failure` from run 2 — answered `('completed', 'success')`, i.e. "gate green, no
    re-post owed" on a head whose gate was red. Today only one job emits `ai-review-gate`, so this
    is a latent false GREEN; the rule is implemented anyway, because a latent false GREEN on the
    attestation gate is the exact failure this tool exists to refuse.

    The workflow is not in the check-runs payload, but the RUN ID inside `details_url` pins it (one
    run = one workflow), so the group key is `(app.slug, run id)`.
    """
    payload = {
        "check_runs": [
            # ⛔ The RED attempt is the OLDER one and the newer is GREEN, which is what makes this
            # fixture discriminating: with a name-only read the newest id (30, success) wins and the
            # red one is masked. Reversing them would make the test pass on the buggy code.
            {"id": 20, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "failure",
             "app": {"slug": "github-actions"},
             "details_url": "https://github.com/o/r/actions/runs/1002/job/9002"},
            {"id": 30, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "success",
             "app": {"slug": "github-actions"},
             "details_url": "https://github.com/o/r/actions/runs/1001/job/9001"},
        ]
    }
    monkeypatch.setattr(ac, "gh_json", lambda args: payload)
    assert ac.read_gate("o/r", "a" * 40) == ("completed", "failure")


def test_read_gate_reports_in_flight_over_a_non_red_completed_group(monkeypatch):
    """Polarity, the other two arms: red wins outright; otherwise in-flight beats a settled non-red.

    A settled non-red group (here `cancelled`) must NOT be reported while another group is still
    running, because the head's answer is not yet known — and `cancelled` is non-red, not green.
    """
    payload = {
        "check_runs": [
            # ⛔ The SETTLED group must be the NEWER one, or this test is vacuous: with the in-flight
            # attempt also newest, plain newest-by-`id` returns the same answer and the polarity arm
            # could be deleted with the suite green (measured, review cycle 25 — the first version of
            # this fixture had exactly that defect, and its own docstring claimed otherwise).
            {"id": 50, "name": ac.GATE_CHECK, "status": "completed", "conclusion": "cancelled",
             "app": {"slug": "github-actions"},
             "details_url": "https://github.com/o/r/actions/runs/2001/job/9101"},
            {"id": 40, "name": ac.GATE_CHECK, "status": "in_progress", "conclusion": None,
             "app": {"slug": "github-actions"},
             "details_url": "https://github.com/o/r/actions/runs/2002/job/9102"},
        ]
    }
    monkeypatch.setattr(ac, "gh_json", lambda args: payload)
    assert ac.read_gate("o/r", "a" * 40) == ("in_progress", "")


def test_a_foreign_record_cannot_report_real_evidence_on_the_json_surface(tmp_path):
    """The JSON boolean must agree with the row that says `(foreign)` — attribution is the claim.

    ⛔ `real_evidence` alone answers "does this record attest a review of SOME PR"; a foreign record
    answers that about another PR. Measured (review cycle 24): under an `UNREADABLE` row, `--json`
    emitted `real_evidence: true`, so `jq 'select(.record.real_evidence)'` selected a record that is
    not this PR's evidence at all. The markdown table labels that row `(foreign)`; a machine
    consumer keying the boolean must not get the opposite answer.

    Driven through the REAL entry point, because `Record.error` is set by `load_record` — a
    hand-built `Record` cannot reproduce the state this pins.
    """
    import json as _json

    reviews = tmp_path / "reviews"
    reviews.mkdir(exist_ok=True)
    # A well-formed record that claims to be for ANOTHER repo — the state the attribution gate exists
    # for. Its own head matches, so only `repo` can refuse it.
    (reviews / "daniel-ospina-tortoise-1.json").write_text(
        _json.dumps({
            "head_sha": LIVE,
            "verdict": "clean",
            "evidence": "comment:6596200336",
            "repo": "evil/evil",
            "pr": 1,
        }),
        encoding="utf-8",
    )
    payload = '[{"number": 1, "headRefOid": "' + LIVE + '", "headRefName": "b", "title": "t"}]'
    proc = _run_with_shim(
        tmp_path, ["--json"], subprocess.PIPE, gh_stdout=payload,
    )
    assert proc.returncode in (0, 2), f"unexpected rc {proc.returncode}: {proc.stderr[-800:]!r}"
    rows = _json.loads(proc.stdout.decode("utf-8"))["rows"]
    assert len(rows) == 1, rows
    assert rows[0]["record"] is not None, "the record must have been read, or the pin is vacuous"
    assert rows[0]["outcome"] == "UNREADABLE", rows[0]
    assert rows[0]["record"]["real_evidence"] is False, (
        "a foreign record must not report real_evidence on the machine-readable surface"
    )


def test_help_from_an_in_process_caller_does_not_swallow_the_hosts_stdout(tmp_path):
    """⛔ Cycle 25, P3: `--help` through an IN-PROCESS caller permanently pointed the process's fd 1 at
    /dev/null.

    `_neutralise_stdout()` dup2s `/dev/null` onto fd 1 for the life of the PROCESS, and it was called
    on the `SystemExit(0)` path even when the flush had SUCCEEDED — so everything written afterwards in
    the host was discarded, including (measured) a `print()` after `ac.main(["--help"])` and, in
    principle, pytest's own summary. The CLI half is unaffected (it has already flushed), which is why
    only a caller that keeps running after `main` can see it — and this PR explicitly cites that
    boundary as its own concern: the suite calls `main` in-process in dozens of places.

    The fix neutralises ONLY when the flush actually failed. Mutation check: make the neutralise
    unconditional again and the sentinel never reaches the file.
    """
    out = tmp_path / "host.txt"
    code = (
        "import sys\n"
        f"sys.path.insert(0, {str(TOOL.parent)!r})\n"
        "import attest_census as ac\n"
        "try:\n"
        "    ac.main(['--help'])\n"
        "except SystemExit:\n"
        "    pass\n"
        "sys.stdout.write('HOST-SENTINEL\\n')\n"
        "sys.stdout.flush()\n"
    )
    with out.open("w", encoding="utf-8") as sink:
        subprocess.run([sys.executable, "-c", code], stdout=sink, stderr=subprocess.DEVNULL, timeout=120)
    text = out.read_text(encoding="utf-8")
    assert "--out" in text, f"the help text itself must still be emitted: {text[-400:]!r}"
    assert "HOST-SENTINEL" in text, (
        "an in-process caller's stdout must survive `main(['--help'])` — it was redirected to "
        f"/dev/null for the rest of the process: {text[-400:]!r}"
    )
