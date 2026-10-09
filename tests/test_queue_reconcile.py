"""Hermetic tests for `tools/queue_reconcile.py` (#7800).

No network, no DB, no `gh`, and — deliberately — **the real queue is never
touched**: every test writes its own `CLAIMS.tsv` under `tmp_path` and injects a
`FakeResolver`, so a regression can corrupt only a temporary file.

The tool exists because a merge is a code event and nothing updates the queue
row (row 7678 read BLOCKED for hours after PR #7686 merged). The tests pin the
parts that can silently produce a WRONG VERDICT (and therefore a wrong live
mutation):

  * the four required classifications — merged-while-BLOCKED, closed-issue-while-
    non-terminal, open-and-correctly-BLOCKED, unresolvable -> UNKNOWN;
  * `SKIPPED` is NON-TERMINAL (the documented trap), so a SKIPPED row on a merged
    PR is flagged rather than silently accepted;
  * terminality is a property of the HISTORY, not the last row (#7329);
  * `--apply` is append-only and idempotent, and dry-run writes nothing;
  * a number resolving to a PR can never contradict a LANDED row that names an
    ISSUE (the false-positive this tool must not manufacture).

EVERY TEST STATES, IN ITS DOCSTRING, (a) THE EXACT STATE THAT MAKES IT FAIL and
(b) WHY THAT STATE IS REACHABLE. Registered in `config/ci-surfaces.yml`
(`manifest-integrity` fails on an unregistered test file).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import queue_reconcile as q  # noqa: E402


HEADER = "# pr\tlane\tverdict\treason"


def write_queue(tmp_path: Path, rows: list[str], header: bool = True) -> Path:
    path = tmp_path / "CLAIMS.tsv"
    lines = ([HEADER] if header else []) + rows
    path.write_text("\n".join(lines) + "\n")
    return path


class FakeResolver:
    """Map identifier -> Resolution; anything unlisted is UNKNOWN (404)."""

    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, number: str) -> q.Resolution:
        return self.mapping.get(number, q.Resolution(q.UNKNOWN, detail="not found (HTTP 404)"))


def findings_for(tmp_path: Path, rows: list[str], mapping) -> list[q.Finding]:
    rows_text = write_queue(tmp_path, rows).read_text()
    parsed, _ = q.parse_queue(rows_text)
    return q.build_findings(q.histories(parsed), FakeResolver(mapping))


# ==========================================================================
# The four required classifications
# ==========================================================================


def test_merged_while_blocked_is_flagged_and_applyable(tmp_path):
    """(a) FAILS if a BLOCKED row on a MERGED PR is classified OK, or if the
    finding is not APPLYABLE. (b) Reachable: row 7678 read BLOCKED for hours
    after PR #7686 merged (measured 2026-10-08)."""
    findings = findings_for(
        tmp_path,
        ["7600\tlane-a\tBLOCKED\trail refused"],
        {"7600": q.Resolution(q.PR_MERGED, merge_commit="abc123", merged_at="2026-10-08T17:22:27Z")},
    )
    (f,) = findings
    assert f.kind == q.MERGED_WHILE_NOT_LANDED
    assert f.kind in q.APPLYABLE
    assert f.resolution.merge_commit == "abc123"


def test_closed_issue_while_non_terminal_is_flagged(tmp_path):
    """(a) FAILS if a CLAIMED row on a CLOSED issue is classified OK. (b)
    Reachable: an issue closed by a merged PR while the queue row is still a
    non-terminal CLAIMED."""
    findings = findings_for(
        tmp_path,
        ["7601\tlane-a\tCLAIMED\tworking"],
        {"7601": q.Resolution(q.ISSUE_CLOSED, detail="completed")},
    )
    (f,) = findings
    assert f.kind == q.CLOSED_ISSUE_WHILE_NON_TERMINAL
    assert f.kind not in q.APPLYABLE


def test_open_and_correctly_blocked_is_not_flagged(tmp_path):
    """(a) FAILS if a BLOCKED row whose PR is still OPEN is reported. (b)
    Reachable: every live BLOCKED row (e.g. 7679) names an open PR."""
    findings = findings_for(
        tmp_path,
        ["7602\tlane-a\tBLOCKED\twaiting on CI"],
        {"7602": q.Resolution(q.PR_OPEN, detail="open")},
    )
    (f,) = findings
    assert f.kind == q.OK


def test_unresolvable_is_unknown_never_inferred(tmp_path):
    """(a) FAILS if a number GitHub does not know is folded into OK (the
    documented false-green). (b) Reachable: a 404 from `/issues/<N>`."""
    findings = findings_for(
        tmp_path,
        ["7603\tlane-a\tBLOCKED\tstale row"],
        {},  # FakeResolver -> UNKNOWN
    )
    (f,) = findings
    assert f.kind == q.UNKNOWN
    assert f.state == q.UNKNOWN
    assert f.kind != q.OK


# ==========================================================================
# Taxonomy: SKIPPED is not terminal (#7329)
# ==========================================================================


def test_skipped_on_merged_pr_is_flagged_not_treated_terminal(tmp_path):
    """(a) FAILS if SKIPPED is treated as terminal — a prior lane did exactly
    that and silently killed 43 queued items. (b) Reachable: CLAIMS.tsv carries
    327 SKIPPED rows, 3 of them on merged PRs (measured 2026-10-08)."""
    findings = findings_for(
        tmp_path,
        ["7604\tlane-a\tSKIPPED\tnot now"],
        {"7604": q.Resolution(q.PR_MERGED, merge_commit="def456")},
    )
    (f,) = findings
    assert f.verdict == "SKIPPED"
    assert f.kind == q.MERGED_WHILE_NOT_LANDED


def test_terminal_verdict_is_history_not_last_row(tmp_path):
    """(a) FAILS if a later non-terminal row un-terminates an earlier BLOCKED
    one (#7329). (b) Reachable: row histories BLOCKED -> CLAIMED -> SKIPPED are
    common in CLAIMS.tsv."""
    text = write_queue(
        tmp_path,
        [
            "7605\tlane-a\tBLOCKED\tdone",
            "7605\tlane-b\tCLAIMED\t11:00:00",
            "7605\tlane-b\tSKIPPED\tnot now",
        ],
    ).read_text()
    parsed, _ = q.parse_queue(text)
    hist = q.histories(parsed)
    assert hist["7605"].effective_verdict == "BLOCKED"


def test_revived_clears_a_terminal_verdict(tmp_path):
    """(a) FAILS if REVIVED does not re-open a terminal item (the operator
    verb). (b) Reachable: the `__revive__` rows appended by the orchestrator."""
    text = write_queue(
        tmp_path,
        ["7606\tlane-a\tBLOCKED\tdone", "7606\t__revive__\tREVIVED\tstale"],
    ).read_text()
    parsed, _ = q.parse_queue(text)
    assert q.histories(parsed)["7606"].effective_verdict == "REVIVED"


# ==========================================================================
# The #7678 shape (issue key, PR merged) and the #7541 inverse
# ==========================================================================


def test_blocked_on_closed_issue_is_flagged(tmp_path):
    """(a) FAILS if BLOCKED + CLOSED issue is classified OK. (b) Reachable and
    MEASURED: row `7678` keys the ISSUE (its PR is #7686, named only in the
    reason), so a PR-only check would miss it."""
    findings = findings_for(
        tmp_path,
        ["7607\tlane-a\tBLOCKED\tblocked on CI red"],
        {"7607": q.Resolution(q.ISSUE_CLOSED, detail="completed")},
    )
    (f,) = findings
    assert f.kind == q.BLOCKED_ON_CLOSED_ISSUE
    assert f.kind not in q.APPLYABLE


def test_fixed_on_closed_issue_is_consistent(tmp_path):
    """(a) FAILS if a terminal FIXED row on a closed issue is reported — that
    would make every remedy close a false positive. (b) Reachable: rows 4621 /
    4880 are FIXED with the issue closed."""
    findings = findings_for(
        tmp_path,
        ["7608\tlane-a\tFIXED\tclosed with evidence"],
        {"7608": q.Resolution(q.ISSUE_CLOSED, detail="completed")},
    )
    (f,) = findings
    assert f.kind == q.OK


def test_landed_but_pr_not_merged_is_flagged(tmp_path):
    """(a) FAILS if a LANDED row whose PR is still OPEN is accepted (the #7541
    defect-#2 direction). (b) Reachable: #7537/#7536 carried LANDED while
    `gh pr view` reported OPEN."""
    findings = findings_for(
        tmp_path,
        ["7609\tlane-a\tLANDED\tclaimed merge"],
        {"7609": q.Resolution(q.PR_OPEN, detail="open")},
    )
    (f,) = findings
    assert f.kind == q.LANDED_BUT_PR_NOT_MERGED


def test_landed_row_naming_an_issue_is_not_a_false_positive(tmp_path):
    """(a) FAILS if a LANDED row is checked against the PR state of an ISSUE
    number. (b) Reachable: many LANDED rows key an ISSUE and name the PR in the
    reason (e.g. 5254 -> PR #7745); ISSUE_OPEN must stay OK."""
    findings = findings_for(
        tmp_path,
        ["7610\tlane-a\tLANDED\tPR #7999 merged"],
        {"7610": q.Resolution(q.ISSUE_OPEN, detail="open")},
    )
    (f,) = findings
    assert f.kind == q.OK


# ==========================================================================
# Parse + data quality
# ==========================================================================


def test_parse_skips_header_and_reports_malformed():
    """(a) FAILS if the `# pr...` header is parsed as a row, or a <3-field line
    is guessed at. (b) Reachable: CLAIMS.tsv's header plus garbled rows (#6915)."""
    rows, malformed = q.parse_queue(
        "# pr\tlane\tverdict\treason\n"
        "\n"
        "7700\tlane-a\tBLOCKED\treason here\n"
        "garbled\ttoo-few\n"
    )
    assert [r.number for r in rows] == ["7700"]
    assert len(malformed) == 1


def test_non_numeric_identifier_is_malformed_not_unknown(tmp_path):
    """(a) FAILS if a garbled identifier is reported as a failed GitHub lookup
    (which would force the UNKNOWN exit for a data-quality issue). (b)
    Reachable: CLAIMS.tsv carries `PR`, `NONE`, `tortoise`, and empty keys."""
    findings = findings_for(tmp_path, ["NONE\tlane-a\tFREE\t17:39:22"], {})
    (f,) = findings
    assert f.kind == q.MALFORMED


# ==========================================================================
# apply: append-only, locked-free (tmp), idempotent
# ==========================================================================


def test_apply_appends_landed_and_is_idempotent(tmp_path):
    """(a) FAILS if apply rewrites the file, loses the original rows, or appends
    a second correction on re-run. (b) Reachable: the live correction path."""
    path = write_queue(tmp_path, ["7611\tlane-a\tBLOCKED\trail refused"])
    original = path.read_text()
    findings = findings_for(
        tmp_path,
        ["7611\tlane-a\tBLOCKED\trail refused"],
        {"7611": q.Resolution(q.PR_MERGED, merge_commit="abc999", merged_at="2026-10-08T17:22:27Z")},
    )
    applied = q.apply_corrections(path, findings, backup=False)
    assert applied == [
        {"number": "7611", "verdict": "BLOCKED", "applied": True, "skipped": False,
         "reason": applied[0]["reason"]}
    ]
    text = path.read_text()
    assert text.startswith(original)  # append-only: originals preserved
    assert "\tqueue-reconcile\tLANDED\t" in text
    assert "abc999" in text  # the proof travels with the correction

    # Re-run: the queue now says LANDED, so nothing is applied.
    parsed, _ = q.parse_queue(path.read_text())
    refind = q.build_findings(
        q.histories(parsed),
        FakeResolver({"7611": q.Resolution(q.PR_MERGED, merge_commit="abc999")}),
    )
    assert [f.kind for f in refind] == [q.OK]
    assert q.apply_corrections(path, refind, backup=False) == []
    # No second row was written.
    assert path.read_text().count("\tqueue-reconcile\t") == 1


def test_apply_recheck_skips_a_number_already_terminal(tmp_path):
    """(a) FAILS if apply appends for a number another lane already fixed. (b)
    Reachable: concurrent lanes append to a shared queue."""
    path = write_queue(
        tmp_path,
        ["7612\tlane-a\tBLOCKED\trail refused", "7612\tlane-b\tLANDED\tlanded by peer"],
    )
    stale_finding = q.Finding(
        number="7612", verdict="BLOCKED", state=q.PR_MERGED,
        kind=q.MERGED_WHILE_NOT_LANDED, lane="lane-a",
    )
    applied = q.apply_corrections(path, [stale_finding], backup=False)
    assert applied[0]["applied"] is False
    assert applied[0]["skipped"] is True
    assert path.read_text().count("\tqueue-reconcile\t") == 0


def test_apply_takes_a_backup(tmp_path):
    """(a) FAILS if apply mutates the queue without a recoverable backup. (b)
    Reachable: every live `--apply` run."""
    path = write_queue(tmp_path, ["7613\tlane-a\tBLOCKED\trail refused"])
    finding = q.Finding(
        number="7613", verdict="BLOCKED", state=q.PR_MERGED,
        kind=q.MERGED_WHILE_NOT_LANDED, lane="lane-a",
        resolution=q.Resolution(q.PR_MERGED, merge_commit="cafebabe"),
    )
    q.apply_corrections(path, [finding], backup=True)
    backups = list(tmp_path.glob("CLAIMS.tsv.bak-reconcile-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == HEADER + "\n7613\tlane-a\tBLOCKED\trail refused\n"


# ==========================================================================
# main(): dry-run writes nothing; apply writes only the correction
# ==========================================================================


def test_main_dry_run_writes_nothing(tmp_path, monkeypatch):
    """(a) FAILS if the default (no --apply) run mutates the queue. (b)
    Reachable: the report-only mode is the default."""
    path = write_queue(tmp_path, ["7614\tlane-a\tBLOCKED\trail refused"])
    before = path.read_text()
    monkeypatch.setattr(
        q, "GitHubResolver",
        lambda repo: FakeResolver({"7614": q.Resolution(q.PR_MERGED, merge_commit="beef")}),
    )
    rc = q.main(["--queue", str(path), "--workers", "1"])
    assert rc == 1  # contradiction found
    assert path.read_text() == before  # but nothing written


def test_main_apply_writes_the_correction(tmp_path, monkeypatch):
    """(a) FAILS if --apply does not append the LANDED row, or exits non-zero
    while the only remaining finding is a report-only one. (b) Reachable: the
    live correction path."""
    path = write_queue(tmp_path, ["7615\tlane-a\tBLOCKED\trail refused"])
    monkeypatch.setattr(
        q, "GitHubResolver",
        lambda repo: FakeResolver({"7615": q.Resolution(q.PR_MERGED, merge_commit="beef")}),
    )
    rc = q.main(["--queue", str(path), "--apply", "--no-backup", "--workers", "1"])
    assert rc == 0  # the only contradiction was corrected
    assert "\tqueue-reconcile\tLANDED\t" in path.read_text()


def test_main_unknown_forces_exit_2(tmp_path, monkeypatch):
    """(a) FAILS if a run with an unresolvable number exits 0 (the documented
    false-green). (b) Reachable: a stale/garbled identifier."""
    path = write_queue(tmp_path, ["7616\tlane-a\tBLOCKED\treason"])
    monkeypatch.setattr(q, "GitHubResolver", lambda repo: FakeResolver({}))
    assert q.main(["--queue", str(path), "--workers", "1"]) == 2
