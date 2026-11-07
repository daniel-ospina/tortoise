"""#9664 — the capstone verdict ledger's mechanism, pinned.

`tools/capstone_ledger.py` exists so that the capstone's second acceptance
criterion ("an empty uncovered list is the only clean exit") is a COMPUTED
answer instead of a fresh prose re-read. Three passes over this issue produced
three denominators for the same rows (14 / 18 / 22), so the point of a pin here
is not that a particular row is green — it is that the ROLLUP cannot be talked
around:

  * a ledger whose every row is `verified` exits 0;
  * one uncovered row makes the whole thing exit 2 and NAMES that row;
  * `verified` with no evidence, evidence that is not in the tree, a verdict
    word outside the vocabulary, an unexplained uncovered row, a duplicated id
    and an empty ledger are all INVALID (exit 1) rather than quietly counted —
    because each of those would let a row disappear from the uncovered list
    without anyone deciding it had been covered;
  * the off-switch can never be mistaken for a pass (exit 3, not 0).

The last test pins the REAL committed ledger. It asserts the denominator (22
criteria, and their ids) so that adding or dropping a criterion is a visible
change, and it asserts the uncovered set by id so that a row cannot silently
stop being reported.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "capstone_ledger.py"
LEDGER = REPO_ROOT / "docs" / "evidence" / "9664-capstone" / "verdict-ledger.yml"

#: The criteria the issue itself names: 4 "Done means" boxes, 10 run steps,
#: 4 #9340 ugly cases, 4 "Evidence and verdict" requirements.
EXPECTED_IDS = [
    "C1",
    "C2",
    "C3",
    "C4",
    "S1",
    "S2",
    "S3",
    "S4",
    "S5",
    "S6",
    "S7",
    "S8",
    "S9",
    "S10",
    "U1",
    "U2",
    "U3",
    "U4",
    "E1",
    "E2",
    "E3",
    "E4",
]

#: The rows this pass measured as NOT satisfied, by id. Pinned so that a later
#: pass cannot make the uncovered list shorter without saying which row it
#: decided was covered.
EXPECTED_UNCOVERED = [
    "C1",
    "C2",
    "C4",
    "S1",
    "S2",
    "S3",
    "S4",
    "S5",
    "S6",
    "U1",
    "U2",
    "U4",
    "E1",
]

#: A distinctive fragment of EVERY criterion as the ISSUE spells it. The ledger
#: rule is "verbatim, never paraphrased", but the test above can only check
#: shape (non-empty, not a shorthand label) because the issue body is not
#: readable from the suite. These needles close that gap: each one is text that
#: exists in #9664's own body (`The run` table cells, the #9340 sentence, the
#: "Evidence and verdict" bullets, the "Done means" boxes) and would DISAPPEAR
#: from a row whose author shortened or rewrote the cell it is quoting.
#:
#: MEASURED: this is not hypothetical. The S2 row originally quoted only its
#: `Do` cell and dropped `Observe` ("chosen set persists; `configured` reflects
#: whether narrowing was applied"), i.e. it dropped the whole observable — the
#: one half a reader needs to know what would count as passing. The needle for
#: S2 below is that dropped half.
EXPECTED_TEXT_NEEDLES: dict[str, str] = {
    "C1": "One run executed end to end, evidence posted here.",
    "C2": "an empty uncovered list is the only clean exit",
    "C3": "Anything that failed has an issue.",
    "C4": "Only then may the ChatGPT-path issues be closed.",
    "S1": "`team-named` closes",
    "S2": "chosen set persists; `configured` reflects whether narrowing was applied",
    "S3": "the card's steps, as rendered",
    "S4": "consent shows the right workspace; token issued",
    "S5": "`connection-written` + `harness-connected` close at token issue",
    "S6": "the agent calls `tortoise_load_skill`",
    "S7": "returns only entitled graphs",
    "S8": "`first-points-filed` closes",
    "S9": "`decide-completed` closes",
    "S10": "capture is unavailable for ChatGPT, not silence",
    "U1": "an explicit non-default graph",
    "U2": "a duplicate graph name",
    "U3": "a graph id from another workspace",
    "U4": "a soft-deleted graph",
    "E1": "Screenshots per step, dated, linked in a comment on this issue.",
    "E2": "The tool-call trace for steps 6-9",
    "E3": "never an aggregate",
    "E4": "not patched mid-run",
}

#: A real file in the tree, usable as evidence by any fixture row.
REAL_EVIDENCE = "docs/evidence/9664-capstone/README.md"

SHA = "e1ba0974ef8452d57ad17ecaff18579d4772cd2b"


def _load_module():
    spec = importlib.util.spec_from_file_location("capstone_ledger", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cl = _load_module()


def _ledger(rows, **overrides):
    data = {
        "version": 1,
        "issue": 9664,
        # Required, and pinned to the row count: an OPTIONAL denominator was
        # measured to be no pin at all (a 9-row ledger without it exits 0).
        "criteria_expected": len(rows),
        "measured_at": {"ref": "origin/main", "sha": SHA, "date": "2026-11-07"},
        "criteria": rows,
    }
    data.update(overrides)
    return data


def _row(cid, verdict="verified", **extra):
    row = {"id": cid, "text": f"criterion {cid}", "verdict": verdict}
    if verdict == "verified":
        row["evidence"] = [REAL_EVIDENCE]
    else:
        row["reason"] = f"{cid} is not satisfied because of a named thing"
    row.update(extra)
    return row


def _run_cli(tmp_path, data, env=None):
    import os

    ledger = tmp_path / "ledger.yml"
    ledger.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    environment = dict(os.environ)
    environment.pop("TORTOISE_CAPSTONE_LEDGER", None)
    if env:
        environment.update(env)
    return subprocess.run(
        [sys.executable, str(TOOL), "check", "--ledger", str(ledger)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=environment,
        check=False,
    )


# ── the rollup itself ───────────────────────────────────────────────────────


def test_all_verified_is_the_only_clean_exit(tmp_path):
    proc = _run_cli(tmp_path, _ledger([_row("A"), _row("B")]))
    assert proc.returncode == cl.EXIT_CLEAN, proc.stderr
    assert "UNCOVERED LIST: empty" in proc.stdout


def test_one_uncovered_row_exits_two_and_names_the_row(tmp_path):
    proc = _run_cli(tmp_path, _ledger([_row("A"), _row("B", "blocked")]))
    assert proc.returncode == cl.EXIT_UNCOVERED, proc.stdout + proc.stderr
    assert "B" in proc.stdout
    # The reason must travel with the row: an uncovered list without reasons is
    # the "explicit" half of the criterion going missing.
    assert "B is not satisfied because of a named thing" in proc.stdout


# ── fail-closed: each of these would hide a row from the uncovered list ─────


@pytest.mark.parametrize("orphan", ["docs/evidence/does-not-exist.md", "tools/nope.py"])
def test_evidence_path_that_is_not_in_the_tree_is_invalid(tmp_path, orphan):
    """Described evidence must not count — only held evidence does."""
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[orphan])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout
    assert orphan in proc.stderr


def test_verified_with_no_evidence_is_invalid(tmp_path):
    proc = _run_cli(tmp_path, _ledger([{"id": "A", "text": "t", "verdict": "verified"}]))
    assert proc.returncode == cl.EXIT_INVALID
    assert "no evidence" in proc.stderr


@pytest.mark.parametrize("word", ["partial", "green", "done", "pass", "VERIFIED"])
def test_verdict_outside_the_closed_vocabulary_is_invalid(tmp_path, word):
    """Every pass over this issue invented its own word for the same row."""
    proc = _run_cli(tmp_path, _ledger([_row("A", word)]))
    assert proc.returncode == cl.EXIT_INVALID
    assert "closed vocabulary" in proc.stderr


def test_uncovered_row_without_a_reason_is_invalid(tmp_path):
    proc = _run_cli(tmp_path, _ledger([{"id": "A", "text": "t", "verdict": "not-covered"}]))
    assert proc.returncode == cl.EXIT_INVALID
    assert "no `reason`" in proc.stderr


def test_duplicate_ids_are_invalid(tmp_path):
    proc = _run_cli(tmp_path, _ledger([_row("A"), _row("A")]))
    assert proc.returncode == cl.EXIT_INVALID
    assert "duplicates an earlier id" in proc.stderr


def test_empty_ledger_is_invalid_not_clean(tmp_path):
    """An empty criteria list would report an empty uncovered list — for free."""
    proc = _run_cli(tmp_path, _ledger([]))
    assert proc.returncode == cl.EXIT_INVALID
    assert "non-empty" in proc.stderr


def test_missing_ledger_file_is_invalid_not_clean(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(TOOL), "check", "--ledger", str(tmp_path / "absent.yml")],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == cl.EXIT_INVALID
    assert "no ledger at" in proc.stderr


def test_duplicate_key_in_a_row_is_invalid_not_silently_last_wins(tmp_path):
    """A repeated key is AMBIGUITY, and the default parser hides it.

    `yaml.safe_load` keeps only the LAST of two identical keys, so a row can
    carry two contradictory `reason:` values and the validator sees one. Measured:
    a duplicate `reason:` block left by a copy-paste edit passed `check` (exit 2)
    and the whole suite while being invalid for any strict YAML reader. The loader
    must refuse it, or this module's "an ambiguous row is a refusal" rule is a
    claim about a check it does not perform.
    """
    ledger = tmp_path / "ledger.yml"
    ledger.write_text(
        "version: 1\n"
        "issue: 9664\n"
        "measured_at: {ref: origin/main, sha: " + SHA + ", date: 2026-11-07}\n"
        "criteria:\n"
        "  - id: A\n"
        "    text: a criterion\n"
        "    verdict: blocked\n"
        "    reason: first justification\n"
        "    reason: second, contradictory justification\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(TOOL), "check", "--ledger", str(ledger)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "repeats the key" in proc.stderr
    assert "reason" in proc.stderr


def test_wrong_issue_number_is_invalid(tmp_path):
    proc = _run_cli(tmp_path, _ledger([_row("A")], issue=9567))
    assert proc.returncode == cl.EXIT_INVALID
    assert "issue" in proc.stderr


def test_bad_provenance_sha_is_invalid(tmp_path):
    data = _ledger([_row("A")])
    data["measured_at"]["sha"] = "not-a-sha"
    proc = _run_cli(tmp_path, data)
    assert proc.returncode == cl.EXIT_INVALID
    assert "40-hex" in proc.stderr


# ── the off-switch can never be read as a pass ─────────────────────────────


def test_off_switch_exits_disabled_never_clean(tmp_path):
    proc = _run_cli(tmp_path, _ledger([_row("A")]), env={"TORTOISE_CAPSTONE_LEDGER": "off"})
    # Exit 0 here would make "disabled" indistinguishable from "clean" to any
    # caller that branches on the exit code.
    assert proc.returncode == cl.EXIT_DISABLED, proc.stdout + proc.stderr
    assert "no verdict was computed" in proc.stderr


def test_advisory_reports_but_does_not_enforce_with_its_OWN_exit_code(tmp_path):
    """Advisory must not reuse EXIT_CLEAN.

    It used to return 0, which meant a caller branching on the exit status read
    "advisory, rows uncovered" as "clean" — measured on the real ledger: 13
    uncovered rows, exit 0. A distinct code is the only version a caller cannot
    misread, because the caller cannot be assumed to read stdout.
    """
    proc = _run_cli(
        tmp_path,
        _ledger([_row("A"), _row("B", "blocked")]),
        env={"TORTOISE_CAPSTONE_LEDGER": "advisory"},
    )
    assert proc.returncode == cl.EXIT_ADVISORY, proc.stdout + proc.stderr
    assert proc.returncode != cl.EXIT_CLEAN
    assert proc.returncode != cl.EXIT_UNCOVERED  # it did NOT enforce either
    assert "B" in proc.stdout  # the report still names the uncovered row
    assert "ADVISORY" in proc.stderr


# ── the anti-forgery arms a review broke, pinned ───────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "cmd:done",
        "cmd:verified manually by reading pyproject.toml",
        "cmd:NOT RUN - see tools/capstone_ledger.py for the intended command",
        "cmd:bash -c 'verified manually by reading pyproject.toml'",
        "cmd:NOT RUN && uv run pytest tests/test_9664_capstone_verdict_ledger.py",
    ],
)
def test_a_cmd_entry_cannot_alone_satisfy_verified(tmp_path, command):
    """A `cmd:` entry is a RECORD, not a proof — so it cannot carry `verified`.

    Four review rounds each defeated a shape heuristic with one more shell form
    (a quoted prose argument, an `&& echo PASS` tail, `python` in front of a
    sentence, a merge-key anchor). The resolution is to stop claiming the arm
    proves anything: `verified` needs an artifact the tree HOLDS.
    """
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[command])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "cites no artifact IN THE TREE" in proc.stderr


def test_a_cmd_entry_is_accepted_alongside_a_held_artifact(tmp_path):
    """The record may keep the command it ran; the HELD artifact is what counts."""
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:bash -c 'verified manually by reading pyproject.toml'",
                    ],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


def test_only_an_EMPTY_cmd_entry_is_refused(tmp_path):
    """No parse rule survives: an unmatched quote is a shell-valid record's business,
    not the ledger's, and refusing it hid the whole uncovered list."""
    for empty in ["cmd:", "cmd:   "]:
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, empty])]))
        assert proc.returncode == cl.EXIT_INVALID, (empty, proc.stdout + proc.stderr)
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, "cmd:'unbalanced"])]))
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


@pytest.mark.parametrize("escape", ["/etc/hostname", "..", ".", "docs"])
def test_a_path_outside_or_not_a_file_is_invalid(tmp_path, escape):
    """`Path.exists()` accepted an absolute path outside the checkout, a bare
    directory and `.` — each on a `verified` row with a clean exit. The rule the
    artifact states is "a FILE *in the tree*", so all three are refusals."""
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[escape])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "evidence path" in proc.stderr


def test_validate_honours_its_repo_root_argument(tmp_path):
    """`repo_root` was accepted and never forwarded, so a caller scoping
    validation to another tree silently validated against THIS checkout and the
    "not in the tree" assertion could never fail on the developer's box."""
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    data = _ledger([_row("A")])  # cites a file that exists only in the real tree
    with pytest.raises(cl.InvalidLedger):
        cl.validate(data, repo_root=empty)


def test_an_unknown_version_is_invalid(tmp_path):
    proc = _run_cli(tmp_path, _ledger([_row("A")], version=99))
    assert proc.returncode == cl.EXIT_INVALID
    assert "version" in proc.stderr


def test_a_sha_that_names_no_commit_is_invalid(monkeypatch):
    """Shape is not existence: a fabricated 40-hex sha rendered as provenance.

    Asserted on the WIRING, with the state machine pinned as `absent`, because a
    fleet worktree is a SHALLOW clone and cannot tell a fabricated sha from an
    ancestor it does not carry — see the next test. The end-to-end version of this
    arm genuinely cannot fire here, and a test that pretended otherwise would be
    green for the wrong reason.
    """
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root=cl.REPO_ROOT: "absent")
    data = _ledger([_row("A")])
    data["measured_at"]["sha"] = "deadbeef" * 5
    with pytest.raises(cl.InvalidLedger) as excinfo:
        cl.validate(data)
    assert "names no commit" in str(excinfo.value)


def test_an_unprovable_sha_is_not_a_defect(monkeypatch):
    """The other half of the shallow-clone fix, and the half that had no pin.

    Cycle 2 rejected `_commit_exists` because a `fetch-depth: 1` checkout makes a
    LEGITIMATE ancestor sha unresolvable, so `python-ci-gate` redded on a good
    record. `_commit_state`'s third outcome is what stops that — but a pin on the
    state MACHINE never asserts the DECISION: `validate` could go back to
    appending a problem for `unprovable` and every other test in this file would
    still pass, because the shallow test below drives `_commit_state` DIRECTLY and
    this one asserts on the wiring for the same reason the `absent` arm does —
    `validate` is the caller that could start blaming.
    """
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root=cl.REPO_ROOT: "unprovable")
    data = _ledger([_row("A")])
    data["measured_at"]["sha"] = "0" * 40
    # `validate` RAISES on a problem and returns the normalized rows otherwise, so
    # "not blamed" is asserted by the call simply not raising.
    assert [row["id"] for row in cl.validate(data)] == ["A"]


def test_dropping_rows_without_moving_the_pin_is_invalid(tmp_path):
    """Deleting the uncovered rows reported CLEAN (`criteria: 9 ... uncovered: 0`).

    That is the 14/18/22 drift reproduced by the very tool built to end it: the
    denominator has to be pinned in the RECORD, or the tool computes it from the
    file it is auditing.
    """
    data = _ledger([_row("A")])
    data["criteria_expected"] = 22
    proc = _run_cli(tmp_path, data)
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "criteria_expected" in proc.stderr


def test_the_committed_ledger_pins_its_denominator():
    data = cl.load_ledger(LEDGER)
    assert data["criteria_expected"] == 22
    assert len(data["criteria"]) == 22


def test_a_shallow_checkout_is_unprovable_never_absent(tmp_path):
    """CI test jobs check out at `fetch-depth: 1`, where an ANCESTOR sha is not in
    the object database. Blaming the record for that reds the required check on a
    legitimate ledger (#9075 r4 P1), so the third outcome must exist.

    Built as a REAL depth-1 clone rather than skipped. The previous version ran
    against the live checkout and skipped whenever it was not shallow — which is
    every developer box and most CI legs — so it asserted nothing on the path that
    decides. This also pins `_commit_state`'s `repo_root` argument: the same sha is
    `unprovable` in the shallow clone and `exists` in the full one, so a function
    that ignored the tree it was asked about could not return both.
    """

    def git(*args: str, cwd: Path) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", cwd=origin)
    git("config", "user.email", "pins@example.com", cwd=origin)
    git("config", "user.name", "pins", cwd=origin)
    git("config", "commit.gpgsign", "false", cwd=origin)
    (origin / "f.txt").write_text("one", encoding="utf-8")
    git("add", "f.txt", cwd=origin)
    git("commit", "-qm", "one", cwd=origin)
    ancestor = git("rev-parse", "HEAD", cwd=origin)
    (origin / "f.txt").write_text("two", encoding="utf-8")
    git("commit", "-qam", "two", cwd=origin)

    # `file://` forces the real transport; a plain local-path clone silently
    # ignores `--depth` and would make this test pass for the wrong reason.
    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(shallow)],
        check=True,
        capture_output=True,
    )
    full = tmp_path / "full"
    subprocess.run(
        ["git", "clone", "-q", f"file://{origin}", str(full)], check=True, capture_output=True
    )

    assert cl._commit_state("0" * 40, repo_root=shallow) == "unprovable"
    assert cl._commit_state("0" * 40, repo_root=full) == "absent"
    # The ancestor the ledger legitimately cites: unprovable where history is
    # truncated, and NOT blamed there — which is the whole fix.
    assert cl._commit_state(ancestor, repo_root=shallow) == "unprovable"
    assert cl._commit_state(ancestor, repo_root=full) == "exists"


def test_usage_outcomes_never_borrow_a_verdict_code():
    """`--help` exited 0 (== CLEAN) and a bad flag exited 2 (== UNCOVERED).

    A caller branching on the exit status read "a help screen was printed" as "the
    ledger is clean", and a typo'd flag as "uncovered rows exist". Both mean "no
    verdict was computed", so both now answer EXIT_USAGE and the other four codes
    keep one meaning each.
    """
    for argv in (["--help"], ["-h"], ["check", "--nope"]):
        proc = subprocess.run(
            [sys.executable, str(TOOL), *argv],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            check=False,
        )
        assert proc.returncode == cl.EXIT_USAGE, (argv, proc.returncode, proc.stderr)
        assert proc.returncode not in {cl.EXIT_CLEAN, cl.EXIT_UNCOVERED}


@pytest.mark.parametrize(
    "payload",
    [
        # Unparseable YAML: `load_ledger`'s `except Exception` is the only guard.
        "not: [valid: yaml",
        # A root-level list: `isinstance(data, dict)` is the only guard.
        "- just\n- a list\n",
        # `measured_at` not a mapping, and a row that is not a mapping: each has its
        # own `isinstance(…, dict)` guard, and neither was pinned.
        "version: 1\nissue: 9664\ncriteria_expected: 1\nmeasured_at: garbage\ncriteria: []\n",
        "version: 1\nissue: 9664\ncriteria_expected: 1\nmeasured_at: {ref: r, sha: "
        + SHA
        + ", date: 2026-11-07}\ncriteria:\n  - 42\n",
    ],
)
def test_a_malformed_ledger_is_invalid_with_no_traceback(tmp_path, payload):
    """T7 for the LEDGER SHAPE, not just for evidence entries.

    Dropping any one of those four guards left the suite green and made the CLI
    print an `AttributeError`/`yaml.YAMLError` traceback instead of the
    `CAPSTONE-LEDGER: INVALID` marker that a log reader — and an in-process caller
    catching `InvalidLedger` — keys on.
    """
    ledger = tmp_path / "ledger.yml"
    ledger.write_text(payload, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(TOOL), "check", "--ledger", str(ledger)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr, proc.stderr
    assert "CAPSTONE-LEDGER: INVALID" in proc.stderr, proc.stderr


def test_a_ledger_without_criteria_expected_is_invalid(tmp_path):
    """OPTIONAL was no pin: a 9-row ledger omitting the key exits 0 and prints
    "UNCOVERED LIST: empty" — the denominator computed from the record itself."""
    data = _ledger([_row("A")])
    del data["criteria_expected"]
    proc = _run_cli(tmp_path, data)
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "criteria_expected" in proc.stderr


def test_an_absolute_wildcard_is_refused_not_crashed(tmp_path):
    """`Path.glob` raises NotImplementedError on a non-relative pattern, so this
    used to be a traceback instead of a named defect."""
    absolute = str(REPO_ROOT / "tests" / "test_9664_capstone_*.py")
    # As a `cmd:` entry it confers nothing; as a PATH entry it must resolve, and a
    # wildcard cannot, which is the refusal rather than the old traceback.
    for entry in (f"cmd:uv run pytest {absolute} -q", absolute):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[entry])]))
        assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
        assert "Traceback" not in proc.stderr


def test_a_merge_key_is_refused_by_name(tmp_path):
    """`<<` is not "just another key": SafeConstructor splices the merged pairs in
    without constructing the node, so the duplicate scan cannot see inside it.

    Measured before the refusal: `<<: { … verdict: not-covered, verdict: verified}`
    loaded as `verified` and emptied the uncovered list — a false PASS through the
    one place the duplicate-key rule could not look. Every row must state its own
    verdict, so the tag is refused with a message that says why.
    """
    ledger = tmp_path / "ledger.yml"
    ledger.write_text(
        "version: 1\n"
        "issue: 9664\n"
        "criteria_expected: 1\n"
        "measured_at: {ref: origin/main, sha: " + SHA + ", date: 2026-11-07}\n"
        "criteria:\n"
        "  - <<: {id: A, text: t, verdict: not-covered, verdict: verified}\n"
        "    evidence: [" + REAL_EVIDENCE + "]\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(TOOL), "check", "--ledger", str(ledger)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "merge key" in proc.stderr
    assert "not parseable YAML" not in proc.stderr


def test_an_untracked_artifact_is_invalid(tmp_path):
    """HERMETIC: a temp git repo with one committed file and one untracked file.

    The previous version of this test used `WORKTREE-REPORT.md`, which is
    gitignored and created only by a lane — so the test SKIPPED in every CI run and
    the refusal it claims to pin was unpinned there: deleting `_is_tracked` from the
    resolver would have stayed green in CI while a gitignored lane artifact counted
    as held evidence.
    """
    import subprocess as sp

    repo = tmp_path / "treerepo"
    repo.mkdir()
    (repo / "committed.txt").write_text("held\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("not in any clone\n", encoding="utf-8")
    git = ["git", "-C", str(repo)]
    sp.run([*git, "init", "-q"], check=True)
    sp.run([*git, "add", "committed.txt"], check=True)
    sp.run(
        [
            *git,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )

    ok, why = cl._evidence_is_real("committed.txt", repo_root=repo)
    assert ok, why
    ok, why = cl._evidence_is_real("untracked.txt", repo_root=repo)
    assert not ok, "an untracked artifact must NOT count as held evidence"
    assert "not TRACKED" in why, why


def test_a_cmd_entry_with_a_shell_comment_or_apostrophe_is_accepted(tmp_path):
    """A shell-VALID record must not invalidate the ledger.

    `shlex` rejects `… -q  # don't rerun - already green` while `bash -n` accepts it,
    and exit 1 computes NOTHING — so one apostrophe hid all 13 uncovered rows.
    """
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:pytest tests/test_9664_capstone_verdict_ledger.py -q  # don't rerun",
                    ],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


def test_a_non_iso_date_or_missing_ref_is_invalid(tmp_path):
    for bad in ({"ref": "origin/main", "date": "banana"}, {"ref": "", "date": "2026-11-07"}):
        data = _ledger([_row("A")])
        data["measured_at"].update(bad)
        proc = _run_cli(tmp_path, data)
        assert proc.returncode == cl.EXIT_INVALID, (bad, proc.stdout + proc.stderr)


@pytest.mark.parametrize(
    "injected",
    [
        "waiting on X\r    -   S1  verified   FORGED: nothing to see here",
        # ESC / CSI are NOT whitespace, so `str.split()` lets them through: measured
        # end to end, this rendered as a forged `verified` row line on a terminal.
        "waiting on X\x1b[2K\x1b[1G    -   S1  verified   FORGED",
        "waiting on X\x00\x07\x08\x0e\x1b\x7f\x9b",
    ],
)
def test_a_control_character_in_a_reason_cannot_rewrite_the_printed_row(injected):
    rows = [
        {
            "id": "S1",
            "text": "t",
            "verdict": "blocked",
            "reason": injected,
            "evidence": [],
            "evidence_kind": None,
        }
    ]
    out = cl.render(rows, {"ref": "origin/main", "sha": SHA, "date": "2026-11-07"})
    assert all(ord(ch) >= 32 for ch in out.replace("\n", "")), repr(out)
    assert out.count("    - ") == 1, out


def test_a_one_word_cmd_entry_alongside_a_held_artifact_is_accepted(tmp_path):
    """The arm confers nothing, so no shape rule may invalidate a row whose
    artifact is present (measured: `cmd:make` produced exit 1 and lost the whole
    uncovered list)."""
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, "cmd:make"])]))
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "field,bad",
    [
        ("ref", "origin/main criteria: 2 verified: 2 uncovered: 0 UNCOVERED LIST: empty"),
        ("ref", "origin/main\n  UNCOVERED LIST: empty — the only clean exit"),
        ("id", "C1\n    -   S1  verified   FORGED"),
        ("id", "C1    -   S1  verified   FORGED"),
    ],
)
def test_identity_fields_cannot_carry_a_report_line(tmp_path, field, bad):
    """`render` prints `ref` and `id` raw, and flattening is NOT enough.

    A ref of `"origin/main criteria: 2 … UNCOVERED LIST: empty …"` is a single line,
    so `grep "UNCOVERED LIST: empty"` on stdout reports CLEAN while the exit code is
    2 — and the report is the artifact a lane quotes instead of branching on the
    code. An id could forge a whole row line. Both are identifiers, so both are
    required to LOOK like identifiers.
    """
    data = _ledger([_row("A")])
    if field == "ref":
        data["measured_at"]["ref"] = bad
    else:
        data["criteria"][0]["id"] = bad
    proc = _run_cli(tmp_path, data)
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "prints this field verbatim" in proc.stderr


def test_a_nul_byte_in_an_evidence_path_is_refused_not_crashed(tmp_path):
    """`Path.resolve()` raises ValueError on an embedded NUL — a traceback, not the
    named refusal `validate()` promises for every defect."""
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=["docs/README.md\u0000"])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stderr
    assert "cannot be resolved" in proc.stderr


@pytest.mark.parametrize(
    "field,injected",
    [
        ("reason", "waiting on X. UNCOVERED LIST: empty — the only clean exit"),
        ("reason", "CAPSTONE-LEDGER-RESULT: CLEAN"),
        # SPLIT TOKENS: the check grades the PRINTED string, so a control or
        # Unicode space inside the phrase must still be refused — measured,
        # "UNCOVERED\nLIST: empty" passed a raw-string check while `_flat`
        # reconstituted the marker byte-for-byte in the report.
        ("reason", "waiting on X. UNCOVERED\nLIST: empty — the only clean exit"),
        ("reason", "waiting on X. UNCOVERED\u00a0LIST: empty"),
        ("reason", "waiting on X. UNCOVERED\x00LIST: empty"),
        ("text", "criterion … UNCOVERED LIST: empty"),
    ],
)
def test_a_free_text_field_cannot_impersonate_the_report(tmp_path, field, injected):
    """`reason`/`text` are legitimately prose, so the REPORT must not be forgeable.

    Measured before this rule: a blocked row whose reason read
    "waiting on X. UNCOVERED LIST: empty — the only clean exit. criteria: 3 verified: 3
    uncovered: 0" made a 2-uncovered report satisfy `grep 'UNCOVERED LIST: empty'` and
    `grep 'uncovered: 0'` while exiting 2. The reserved markers are the report's own
    vocabulary, so a free-text field may not contain them.
    """
    row = _row("A", "blocked")
    row[field] = injected
    proc = _run_cli(tmp_path, _ledger([row]))
    if field == "text":
        # `render()` never prints `text`, so reserving the phrase there would
        # refuse a criterion that legitimately QUOTES the report's output.
        assert proc.returncode == cl.EXIT_UNCOVERED, proc.stdout + proc.stderr
        return
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "reserved report token" in proc.stderr


def test_the_report_carries_a_reserved_result_line():
    """The machine answer must not depend on prose: one reserved line, always."""
    clean = cl.render([_row_row()], {"ref": "origin/main", "sha": SHA, "date": "2026-11-07"})
    assert clean.strip().endswith(f"{cl.RESULT_PREFIX} CLEAN"), clean
    blocked = _row_row("blocked")
    uncovered = cl.render([blocked], {"ref": "origin/main", "sha": SHA, "date": "2026-11-07"})
    assert uncovered.strip().endswith(f"{cl.RESULT_PREFIX} UNCOVERED(1)"), uncovered


def _row_row(verdict="verified"):
    return {
        "id": "A",
        "text": "a criterion",
        "verdict": verdict,
        "reason": "" if verdict == "verified" else "a named reason",
        "evidence": [REAL_EVIDENCE] if verdict == "verified" else [],
        "evidence_kind": None,
    }


@pytest.mark.parametrize("bad", [True, 1.9, "twelve", None])
def test_criteria_expected_must_be_a_literal_integer(tmp_path, bad):
    """`int(True) == 1` and `int(1.9) == 1`: a bare cast turned a non-count into a
    pin that happened to match the row count."""
    data = _ledger([_row("A")])
    data["criteria_expected"] = bad
    if bad is None:
        del data["criteria_expected"]
    proc = _run_cli(tmp_path, data)
    assert proc.returncode == cl.EXIT_INVALID, (bad, proc.stdout + proc.stderr)
    assert "criteria_expected" in proc.stderr


def test_a_long_path_is_truncated_in_the_error_line(tmp_path):
    long = "docs/" + "x" * 400 + ".md"
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[long])]))
    assert proc.returncode == cl.EXIT_INVALID
    assert "…" in proc.stderr, proc.stderr


# ── the REAL committed ledger ──────────────────────────────────────────────


def test_committed_ledger_is_valid_and_has_the_issue_denominator():
    rows = cl.validate(cl.load_ledger(LEDGER))
    assert [row["id"] for row in rows] == EXPECTED_IDS
    assert len(rows) == 22


def test_committed_ledger_uncovered_set_is_pinned():
    rows = cl.validate(cl.load_ledger(LEDGER))
    assert [row["id"] for row in cl.uncovered(rows)] == EXPECTED_UNCOVERED


def test_committed_ledger_exits_uncovered():
    """The capstone is NOT done; the tool must say so, and say why."""
    proc = subprocess.run(
        [sys.executable, str(TOOL), "check"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == cl.EXIT_UNCOVERED, proc.stdout + proc.stderr
    assert "criteria: 22" in proc.stdout


def test_committed_ledger_quotes_every_criterion_verbatim():
    """A paraphrased criterion drifts from the thing being accepted.

    The issue body is not readable offline, so the floor is pinned in two layers:
    a structural one (non-empty, not a shorthand label) and — the layer that
    actually catches a defect — a distinctive fragment per row, quoted from
    #9664's own body. A row that shortens or rewrites the cell it quotes loses
    its needle and fails here.
    """
    rows = cl.validate(cl.load_ledger(LEDGER))
    for row in rows:
        assert len(row["text"]) > 8, row["id"]
        assert row["text"].lower() not in {"step 1", "done", "n/a", "tbd"}, row["id"]


@pytest.mark.parametrize("cid", EXPECTED_IDS)
def test_committed_ledger_keeps_each_criterion_cell(cid):
    rows = {row["id"]: row for row in cl.validate(cl.load_ledger(LEDGER))}
    assert cid in rows, cid
    present = EXPECTED_TEXT_NEEDLES[cid]
    assert present in rows[cid]["text"], (
        f"{cid} no longer contains the issue's own wording: {present!r}\n"
        f"row text: {rows[cid]['text']!r}"
    )
