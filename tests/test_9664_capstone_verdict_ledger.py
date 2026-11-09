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

import ast
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
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

#: A glob that matches real files in the tree — the `_names_artifact` GLOB arm's
#: subject, i.e. the third `_is_tracked` call site. `_names_artifact` takes the
#: artifact TOKEN, not the whole `cmd:` sentence (its caller has already cut it).
_GLOB_EVIDENCE = "docs/evidence/9664-capstone/*.md"

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
        "cmd:I verified this by hand, screenshots are in the issue",
        "cmd:uv run pytest",
        "cmd:cat /etc/hostname",
        # Cycle-2 attack: prose that MENTIONS a real file used to pass a token
        # scan, including prose that says the work was NOT done.
        "cmd:we did not run this; the suite lives in tests/test_9664_capstone_verdict_ledger.py",
        # Cycle-3 attack (measured, all exited 0 before the argument-position rule):
        # prepend ONE runner word to the cycle-2 prose and the token scan found the
        # named file again. The decision now happens at the first argument.
        "cmd:sh NOT RUN - see tools/capstone_ledger.py",
        "cmd:env NOT RUN - see tools/capstone_ledger.py",
        "cmd:timeout 1 NOT RUN - see tools/capstone_ledger.py",
        "cmd:make I did not run anything, see docs/evidence/9664-capstone/README.md",
        "cmd:bash not executed, ref tools/capstone_ledger.py",
        # The vacuous glob: names the repo root, therefore no artifact in particular.
        "cmd:sh *",
        "cmd:git *",
        "cmd:uv run pytest *.yml",
        # The same vacuity spelled with `.`/`..`: a raw text split called these literal
        # directory components, so each passed while resolving to the ROOT.
        "cmd:sh ./*",
        "cmd:sh docs/../*",
        "cmd:sh tests/..//*",
        "cmd:sh */../*",
        # An absolute glob cannot be resolved against the tree at all.
        "cmd:sh /tmp/*",
        # A `..` AT OR AFTER the first wildcard climbs back out to the root: every
        # match is root-level (the `./*` class, one wildcard deep).
        "cmd:sh docs/*/../../*",
        "cmd:sh tests/*/../../*",
        "cmd:sh docs/[a-z]*/../../*",
        "cmd:sh docs/evidence/*/../../../*",
        # ⚠️ NOT a pin on the `RUNNERS` gate, even though it looks like one: this
        # row cites the `cmd:` entry ALONE, so `validate` refuses it for the
        # separate reason that a `verified` row needs a held artifact — that refusal
        # is present whether or not the shape gate runs. The gate's own pin is
        # `test_a_prose_cmd_is_refused_beside_a_held_artifact` below, which is the
        # configuration of the false pass this branch exists to close.
        "cmd:see tools/capstone_ledger.py",
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
    """A real command survives the wrapper; the HELD artifact is what carries it."""
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:bash -c 'uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'",
                    ],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "script",
    [
        "cmd:bash -c 'verified manually by reading pyproject.toml'",
        "cmd:bash -c 'NOT RUN - see tools/capstone_ledger.py'",
        "cmd:uv run python -c 'nothing was executed'",
        "cmd:sh -c 'see docs/evidence/9664-capstone/README.md'",
    ],
)
def test_prose_wrapped_in_a_shell_script_is_refused(tmp_path, script):
    """⛔ THE `-c` SCRIPT IS GRADED AS A COMMAND IN ITS OWN RIGHT.

    Leaving it ungraded made the shape gate ONE WORD WIDE: the prose this arm
    refuses naked was accepted as soon as it was wrapped in `bash -c`. Measured
    at the previous head — `cmd:bash -c 'NOT RUN - see tools/capstone_ledger.py'`
    beside a held artifact validated the row, `uncovered: 0`, exit 0.

    The script is therefore required to start with a program that RUNS something,
    exactly like the outer command. That rule is fail-closed on purpose: a
    legitimate `bash -c 'echo hi'` is also refused, because `echo` cannot be
    admitted to `RUNNERS` without re-admitting `cmd:echo NOT RUN …` in its
    cheapest form. A false block costs a re-recorded entry; a false pass costs the
    artifact's whole purpose.
    """
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, script])]))
    assert proc.returncode == cl.EXIT_INVALID, (script, proc.stdout + proc.stderr)
    # The refusal may come from either arm — the script body is graded as its own
    # command line now, so the wording is the compound-record one, not the
    # runner-position one. The DECISION is what is pinned, not the sentence.
    assert "not a command" in proc.stderr, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        "cmd:uv run python tools/test_lane.py run -- x -q",
        # The most precise citation shape must not be a false block.
        "cmd:uv run python -m pytest tests/test_9664_capstone_verdict_ledger.py::TestX::test_y -q",
        "cmd:uv run pytest tests/test_9664_capstone_*.py",
        # A runner's own literal argument (a duration / an env assignment) must not
        # be mistaken for the target, or `timeout`/`env` become false blocks.
        "cmd:timeout 300 uv run python tools/capstone_ledger.py check",
        "cmd:env TORTOISE_CAPSTONE_LEDGER=off uv run python tools/capstone_ledger.py check",
        # Globs over a NAMED directory stay legal, however they are spelled —
        # `docs/./*` normalises to `docs/*`, so refusing it would be a false block.
        "cmd:uv run pytest ./tests/test_9664_capstone_*.py",
        "cmd:uv run python docs/./*.md",
        "cmd:uv run python docs/**/*.md",
        # A runner declared in `_CHAIN_WORDS` must be reachable at position 0 too,
        # and the chain example in the docstring must not be a false block.
        "cmd:uvx pytest tests/test_9664_capstone_verdict_ledger.py",
        "cmd:poetry run pytest tests/test_9664_capstone_verdict_ledger.py",
    ],
)
def test_a_cmd_entry_that_names_a_real_artifact_is_accepted(command):
    """The ENTRY is accepted — it is not a false block.

    Asserted on `_evidence_is_real` rather than through the CLI because the ROW
    rule is different and stricter: a `verified` row must also hold at least one
    TRACKED artifact (main's arm, kept here), so a `cmd:`-only row is INVALID for
    that reason and could not distinguish "the entry was refused" from "the row
    has no held artifact". This test is about the entry.
    """
    ok, why = cl._evidence_is_real(command)
    assert ok, why


@pytest.mark.parametrize("runnerless", ["cmd:npm", "cmd:verified", "cmd:look at tools/x.py"])
def test_a_cmd_entry_must_start_with_a_runner(tmp_path, runnerless):
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[runnerless])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr


def test_a_cmd_entry_that_names_a_real_script_is_accepted(tmp_path):
    """The rule must not be so tight that a real script record fails."""
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:bash -c 'uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'",
                    ],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


def test_an_empty_cmd_entry_is_refused_and_prose_is_still_refused(tmp_path):
    """An empty `cmd:` is refused; a shell-valid record is not invalidated.

    ⛔ The `cmd:'unbalanced` case is REFUSED here, and that is the merged decision
    rather than an oversight: accepting any unparseable text as a record reopens
    the hole this branch exists to close, because prose with an apostrophe
    (`cmd:I don't run this`) is unparseable too. The shell-valid-but-unparseable
    case that actually occurs — a trailing `#` note containing an apostrophe — is
    handled by stripping the comment (see
    `test_a_cmd_entry_with_a_shell_comment_or_apostrophe_is_accepted`), so a real
    record still round-trips to a clean exit.
    """
    for empty in ["cmd:", "cmd:   "]:
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, empty])]))
        assert proc.returncode == cl.EXIT_INVALID, (empty, proc.stdout + proc.stderr)
        assert "names no command" in proc.stderr
    # Prose that merely fails to parse is not a command: refused, with a named
    # reason and no traceback.
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, "cmd:'unbalanced"])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "CAPSTONE-LEDGER: INVALID" in proc.stderr
    assert "Traceback" not in proc.stderr


@pytest.mark.parametrize("escape", ["/etc/hostname", "..", ".", "docs"])
def test_a_path_outside_or_not_a_file_is_invalid(tmp_path, escape):
    """`Path.exists()` accepted an absolute path outside the checkout, a bare
    directory and `.` — each on a `verified` row with a clean exit. The rule the
    artifact states is "a FILE *in the tree*", so all three are refusals."""
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[escape])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "evidence path" in proc.stderr


def test_validate_honours_its_repo_root_argument(tmp_path, monkeypatch):
    """`repo_root` was accepted and never forwarded, so a caller scoping validation to
    another tree silently validated against THIS checkout and the "not in the tree"
    assertion could never fail on the developer's box.

    ⛔ This test could not fail for the property it names. `tmp_path/elsewhere` is not a
    git repository, so the SHA arm answers `unmeasurable` — and its problem ALONE
    satisfied `pytest.raises(InvalidLedger)`, because `validate` accumulates problems
    rather than stopping at the first. The assertion therefore could not tell WHICH arm
    fired, and MEASURED with `_evidence_is_real(entry, repo_root)` reverted to
    `REPO_ROOT` at both call sites, this test still passed.

    The SHA arm is stubbed to `exists` so the evidence arm is the only arm that can
    raise, and the assertion matches that arm's own message so a raise from the wrong
    arm cannot satisfy it. Same mutant now reddens this test.
    """
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root=None: "exists")
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    data = _ledger([_row("A")])  # cites a file that exists only in the real tree
    with pytest.raises(cl.InvalidLedger, match="not a file in the tree"):
        cl.validate(data, repo_root=empty)


def test_validate_forwards_repo_root_to_the_SHA_arm(monkeypatch, tmp_path):
    """The sibling arm: `_commit_state` honours `repo_root`, but `validate` must HAND
    it one.

    ⛔ That forward was pinned only INCIDENTALLY. Reverting it to `_commit_state(sha)`
    does redden tests, but not one of them ASSERTS which root is handed over: the
    existing stubs fail on the changed call arity, and the behavioural red is a
    downstream difference in the answer, not a statement about the argument. A pin on
    a call shape is not a pin on the property, so this one asserts the property.

    The mutant is a fail-open: a caller doing `validate(data, repo_root=<other tree>)`
    gets `exists` for a sha that exists only in THIS checkout, and `render()` prints
    `measured at origin/main@<sha>` as provenance for a tree that does not contain it.
    """
    seen: list[object] = []

    def fake_commit_state(sha, repo_root=None):
        seen.append(repo_root)
        return "exists"

    monkeypatch.setattr(cl, "_commit_state", fake_commit_state)
    # A blocked row: no evidence entry, so the evidence arm cannot raise and the SHA
    # arm is the only arm left that could decide anything.
    cl.validate(_ledger([_row("A", "blocked")]), repo_root=tmp_path)
    assert seen == [tmp_path]


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
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root: "absent")
    data = _ledger([_row("A")])
    data["measured_at"]["sha"] = "deadbeef" * 5
    with pytest.raises(cl.InvalidLedger) as excinfo:
        cl.validate(data)
    assert "names no commit" in str(excinfo.value)


def test_an_unprovable_sha_is_not_a_defect(monkeypatch, capsys):
    """The other half of the shallow-clone fix, and the half that had no pin.

    Cycle 2 rejected `_commit_exists` because a `fetch-depth: 1` checkout makes a
    LEGITIMATE ancestor sha unresolvable, so `python-ci-gate` redded on a good
    record. `_commit_state`'s third outcome is what stops that — but a pin on the
    state MACHINE never asserts the DECISION: `validate` could go back to
    appending a problem for `unprovable` and every other test in this file would
    still pass, because a full clone resolves the real ledger's sha to `exists`
    and the shallow test SKIPS here (this checkout is not shallow). Asserted on
    the wiring, for the same reason the `absent` arm is: a shallow clone cannot
    produce the input that decides it.

    ⛔ …and abstaining must not be SILENT (#11217). `render()` prints
    `measured_at.sha` as provenance unconditionally, so a check that verifies
    nothing while the sha is still printed as fact is the substitution the whole
    function exists to prevent. The row is not blamed AND the abstention says so.
    """
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root: "unprovable")
    data = _ledger([_row("A")])
    data["measured_at"]["sha"] = "0" * 40
    # `validate` RAISES on a problem and returns the normalized rows otherwise, so
    # "not blamed" is asserted by the call simply not raising.
    assert [row["id"] for row in cl.validate(data)] == ["A"]
    assert "UNPROVABLE" in capsys.readouterr().err, (
        "the abstention must be ANNOUNCED — the sha is still rendered as provenance, "
        "so silence is the substitution this check exists to prevent"
    )


def test_the_object_probe_never_lazy_fetches(monkeypatch):
    """A partial clone must not turn a missing-commit probe into a promisor REFETCH (#11217).

    `--filter=blob:none` clones report `--is-shallow-repository` as FALSE, so the
    shallow probe does not cover them. An absent sha then makes `git cat-file`
    reach for the origin, which took 2m29s here before exiting 128.

    ⛔ This pins the ENV, not a verdict: the measured fetch exited 128 either way,
    so the state was `absent` with and without it. What the env buys is latency,
    and the flip it costs (a fetch that would have materialised a commit the
    promisor holds but this clone's refspec does not is suppressed, so a real sha
    can answer `absent`) is recorded in `_commit_state`'s docstring rather than
    hidden here.
    """
    seen: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = ""

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(cl.subprocess, "run", _fake_run)
    assert cl._commit_state("0" * 40) == "exists"
    assert seen["cmd"][:2] == ["git", "cat-file"], seen["cmd"]
    assert (seen["env"] or {}).get("GIT_NO_LAZY_FETCH") == "1", (
        "the object probe must suppress the promisor refetch: a partial clone is "
        "not shallow, so nothing else stops it"
    )


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


def test_an_unresolvable_evidence_entry_is_invalid_not_a_traceback(tmp_path):
    """`validate`'s contract is "the rows, or `InvalidLedger` naming every defect".

    Two entries broke it by raising out of the file-system layer instead: an
    absolute glob (`Path.glob` -> NotImplementedError) and a NUL byte
    (`Path.resolve` -> ValueError). Both exited 1, so the closed exit SET held — but
    the `CAPSTONE-LEDGER: INVALID` marker a log reader or a caller keys on was
    replaced by a traceback, and an in-process caller catching `InvalidLedger`
    crashed instead.
    """
    for entry in (
        "cmd:sh /tmp/*",
        # The cmd arm's own guard, reached via the NUL byte rather than the absolute
        # pre-check: narrow the `except` to `(OSError,)` and THIS is the case that
        # reds, which is what makes the guard actually pinned.
        "cmd:sh docs/evidence/9664-capstone/READ\x00ME.md",
        "docs/evidence/9664-capstone/READ\x00ME.md",
        # `is_file()` stats the path, so an over-long component raises OSError from
        # OUTSIDE a guard that wraps only `resolve()`.
        "tools/" + "a" * 400 + ".py",
        # ⛔ The two shapes below are what pin `_names_artifact`'s OWN guard. The
        # bare-path over-long entry above exercises `_evidence_is_real`'s path arm,
        # never this one, and the NUL entry only covers the ValueError half — so
        # narrowing that `except` to `(ValueError,)` (or dropping the shlex
        # `except ValueError`) left the whole suite green while the CLI tracebacked.
        "cmd:sh tools/" + "a" * 400 + ".py",
        "cmd:sh docs/*  'unclosed",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[entry])]))
        assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
        assert "Traceback" not in proc.stderr, proc.stderr
        assert "CAPSTONE-LEDGER: INVALID" in proc.stderr, proc.stderr


def test_a_cmd_entry_naming_an_absolute_path_outside_the_tree_is_invalid(tmp_path):
    """T5 in the `cmd:` arm, which had NO pin.

    The suite's only absolute-path case is a bare `"/etc/hostname"`, so it pins the
    PATH arm and never `_names_artifact`. MEASURED: deleting `_names_artifact`'s
    `resolved.is_relative_to(root)` accepted `cmd:sh <abs path outside>` as
    `verified` — exit 0, `UNCOVERED LIST: empty — the only clean exit` — while the
    whole suite stayed green.

    The target is built in `tmp_path` (which is outside this checkout on every box),
    not `/etc/hostname`, so the pin cannot go vacuous on a host that lacks it.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x", encoding="utf-8")

    for entry in (f"cmd:sh {outside / 'secret.md'}", f"cmd:uv run python {outside / 'secret.md'}"):
        data = _ledger([_row("A", evidence=[entry])])
        with pytest.raises(cl.InvalidLedger) as excinfo:
            cl.validate(data, repo_root=REPO_ROOT)
        assert "`cmd:` evidence" in str(excinfo.value)


def test_a_cmd_target_that_names_no_held_artifact_is_invalid(tmp_path):
    """T6 in the `cmd:` arm via a BARE PATH — a FIFO refuses, and so does the ROOT.

    The glob half is pinned (`test_a_glob_whose_matches_are_all_directories_…`),
    but `_names_artifact`'s file clause for bare paths is a SEPARATE one, and
    nothing pinned it: changing it to `exists()` left the whole suite green while
    a one-row `verified` ledger citing `cmd:sh docs` exited **0** with
    `UNCOVERED LIST: empty — the only clean exit` — a FIFO counted as held evidence.

    ⛔ A DIRECTORY is the interesting case, and the answer was REVERSED here after a
    measurement: refusing every directory made `cmd:uv run pytest tests/ -v` — the
    command this repo's own `AGENTS.md` hands a lane under "Run commands/tests" —
    INVALID on `origin/main`, and `exit 1` computes NOTHING, so a lane that recorded
    what it actually ran lost the entire uncovered list. That is the same
    "over-block loses the finding" failure the repo's own review rejected
    (`810eb838c5b`: an arm that confers nothing must not invalidate anything).

    A directory now counts IFF it is strictly below the root and holds at least one
    TRACKED file. Both halves are asserted, because either one alone is spellable as
    the other's bypass: `.` and `/` name EVERYTHING (the vacuity of a bare `*`), and
    an empty or untracked directory names nothing a reader's clone has.
    """
    import subprocess as sp

    tree = tmp_path / "tree"
    (tree / "held" / "sub").mkdir(parents=True)
    (tree / "held" / "sub" / "a_test.py").write_text("x\n", encoding="utf-8")
    (tree / "empty").mkdir()
    (tree / "untracked").mkdir()
    (tree / "untracked" / "x.txt").write_text("not in any clone\n", encoding="utf-8")
    os.mkfifo(tree / "pipe")
    git = ["git", "-C", str(tree)]
    sp.run([*git, "init", "-q"], check=True)
    sp.run([*git, "add", "held"], check=True)
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
    sha = sp.run(
        [*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    def row_with(entry: str) -> dict:
        """A row whose ONLY possible defect is the `cmd:` entry.

        It holds a TRACKED artifact of this temp tree (so the row rule is satisfied)
        and cites a sha that really exists there (so provenance is not the
        complaint) — otherwise a refusal below would be green for the wrong reason.
        """
        data = _ledger([_row("A", evidence=["held/sub/a_test.py", entry])])
        data["measured_at"]["sha"] = sha
        return data

    # The row rule and provenance both pass in this tree: prove it BEFORE asserting
    # that the cases below are refused for the `cmd:` reason.
    cl.validate(row_with("cmd:sh held/sub"), repo_root=tree)

    # Refused: a FIFO, the ROOT (names everything), an empty dir, an untracked dir.
    for entry in ("cmd:sh pipe", "cmd:sh .", "cmd:sh ./", "cmd:sh empty", "cmd:sh untracked"):
        with pytest.raises(cl.InvalidLedger):
            cl.validate(row_with(entry), repo_root=tree)

    # Accepted: a directory that holds a TRACKED file — `pytest tests/` in this repo.
    for entry in ("cmd:uv run pytest held -v", "cmd:uv run pytest held/sub", "cmd:sh held/"):
        cl.validate(row_with(entry), repo_root=tree)

    # End to end: the empty-uncovered-list shape must not be reachable at all.
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=["cmd:sh pipe"])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "UNCOVERED LIST" not in proc.stdout, proc.stdout


@pytest.mark.parametrize(
    "command",
    [
        # Shapes a lane ACTUALLY records. Only TWO were measured FALSE BLOCKS on
        # origin/main — `pytest tests/ -v` and `-m pytest tests/ -q`, both refused
        # because a directory is not a file. The other ten were ACCEPTED there and
        # are pinned here so the every-segment rule cannot start refusing them (it
        # did, for the `tail`/`cat` tails, which is why the reader family is in the
        # decorative set).
        "cmd:uv run pytest tests/ -v",
        "cmd:uv run python -m pytest tests/ -q",
        "cmd:TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:`uv run python tools/capstone_ledger.py check`",
        "cmd:cd tests && uv run pytest test_9664_capstone_verdict_ledger.py -q",
        # A runner with no target at all is a record.
        "cmd:make",
        "cmd:uv run pytest",
        # A pure shell preamble in front of the real command (main accepted these;
        # normalising only `cd`/`NAME=value` made them a regression).
        "cmd:set -e && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:export FOO=1 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:source .venv/bin/activate && uv run pytest "
        "tests/test_9664_capstone_verdict_ledger.py -q",
        # A decorative tail and a second REAL command, both after the graded one.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && echo PASS",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
        "&& ruff check tools/capstone_ledger.py",
        # Readers — how a run is normally recorded. All three were measured false
        # blocks against the every-segment rule (ACCEPTED on main, REFUSED here).
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && tail -20 log.txt",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cat .pytest.log",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
        '&& grep -E "passed" out.txt',
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q | tail -20 log",
        # ⛔ REDIRECTIONS — all four were ACCEPTED on `origin/main` and REFUSED
        # once `&` was split as a separator, which is the P1 regression the first
        # review of this branch caught. `&>`/`&>>` put the `&` before the `>`, and
        # `2>&1`/`>&2` put it after; neither is a separator, and exit 1 prints no
        # report, so the false block hid the whole uncovered list.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q 2>&1 | tail -20",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q > /tmp/o.txt 2>&1",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q &> /tmp/o.txt",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q &>> /tmp/o.txt",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q 2>&1",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q | tee pytest.log",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q | logger -t pytest",
        "cmd:bash -c 'uv run pytest tests/test_9664_capstone_verdict_ledger.py -q 2>&1'",
        # ⛔ `>|` is bash's NOCLOBBER-OVERRIDE redirection, and it was the one spelling
        # the first redirection fix missed: `|` was still split, so the segment
        # `out.txt` came back as prose and the record was REFUSED (measured at
        # `d38377417b`; ACCEPTED on `origin/main`, so the every-segment rule owned
        # the false block). Verified against bash itself: `printf A >| f1` exits 0
        # and writes `f1` — one command, a redirection.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q >| out.txt",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q >|out.txt",
        # ⛔ A runner's own SUBCOMMAND verb is not an artifact. `ruff check <path>`
        # is a record this repo's lint gate really produces, and it was REFUSED at
        # `0c259a39f3` with `'check' is not a file in this tree` — exit 1, so the
        # whole uncovered list disappeared. `ruff` was already in `RUNNERS`, so the
        # entry was inert.
        "cmd:ruff check tools/capstone_ledger.py",
        "cmd:uv run ruff check tools/capstone_ledger.py",
        "cmd:uv run ruff format --check tools/capstone_ledger.py",
        "cmd:uv run ruff check tools/",
        # ⛔ SHELL COMMENTS (#11506 review, cycle 2). An unquoted `#` at a word
        # boundary ends the command; everything after it is a comment, so a `;` or
        # `&` inside one must not split. Both of the first two were ACCEPTED on
        # `origin/main` and REFUSED at the cycle-1 head, where the tail came back
        # as a prose segment, exit 1, and no uncovered list at all. The third is a
        # quoted `#` and was never a comment.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q  # ran; 194 passed",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q  # see PR #11506 & #11520",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -k 'issue#123' -q",
        # ⛔ A LINE CONTINUATION (`\` + newline) is not a separator — the shell
        # JOINS the lines (#11506 review, cycle 4). Tearing at the `\` refused a
        # record pasted from a wrapped terminal command: measured, both shapes below
        # exited 1 with NO uncovered list, while `origin/main` ACCEPTED them.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q \\\n  --maxfail=1",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && \\\n"
        "  ruff check tools/capstone_ledger.py",
    ],
)
def test_the_commands_agents_md_hands_a_lane_are_not_false_blocked(tmp_path, command):
    """A real recorded command must never make the ledger INVALID.

    ⛔ Measured FALSE BLOCKS on `origin/main`: `cmd:uv run pytest tests/ -v` and
    `cmd:uv run python -m pytest tests/ -q` (the repo's own documented test form,
    refused because a directory is not a file), and `cmd:make test` / `cmd:npm test`
    (a label read as a citation). Exit 1 computes NOTHING, so a lane recording what
    it actually ran lost the entire uncovered list.

    The `make test` / `npm test` half of that is now answered the OTHER way — by
    refusing every non-path argument behind a runner rather than by inventing a
    "project label" arm, which re-admitted `cmd:make NOT-RUN`. See
    `test_a_NON_PATH_argument_behind_a_runner_is_not_a_citation`. The rest of this
    test pins that the every-segment rule does not start refusing real records.

    ⛔ SCOPE OF THIS CLAIM, corrected after review. This test covers the
    pytest / ruff / redirection / comment / line-continuation families — NOT every
    command `AGENTS.md` hands a lane. Four shapes it also hands over are still
    REFUSED, and are pinned as such by
    `test_the_agents_md_shapes_this_arm_still_refuses_are_pinned` so the gap is
    disclosed rather than implied closed.
    """
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


def test_the_agents_md_shapes_this_arm_still_refuses_are_pinned(tmp_path):
    """KNOWN-OPEN: three `AGENTS.md` commands are still REFUSED, and that is disclosed.

    Measured at this head (each `EXIT_INVALID`, and exit 1 prints NO uncovered list,
    so a lane recording what it actually ran loses the whole report):

        cmd:uv sync --extra embeddings --extra parity
        cmd:git fetch origin main && git rev-parse origin/main
        cmd:git ls-files --others --exclude-standard

    ⛔ WHY THESE THREE AND NOT `uv lock --check`. `uv lock --check` was in this list
    until the second review cycle measured that it needs NO new mechanism at all —
    only a `_RUNNER_SUBCOMMANDS` entry (`"uv": frozenset({"lock"})`), the arm the
    file already has for `ruff check`. It is now ACCEPTED, and
    `cmd:uv lock NOT RUN` is still REFUSED, so nothing was bought by widening.

    The three below DO need the arm to know that a flag or a subcommand TAKES A
    VALUE (`--extra <name>`, `fetch <remote>`, `ls-files --others`), which is the
    same mechanism that would let `--anything NOT RUN` skip the token saying the
    work was not run — it would re-open the fail-opens this review closed. Trading
    a disclosed false block for a silent false PASS is the wrong direction for a
    gate, so the gap is recorded here and on #11520 instead.

    ⛔ ASSERT THE REASON, not only the code (second review cycle): an exit-code-only
    pin would still pass if one of these began refusing for an UNRELATED cause.
    """
    for command in (
        "cmd:uv sync --extra embeddings --extra parity",
        "cmd:git fetch origin main && git rev-parse origin/main",
        "cmd:git ls-files --others --exclude-standard",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} is now ACCEPTED — if `_RUNNER_SUBCOMMANDS`/flag-value "
            f"handling was added, move this entry to the false-block test and prove "
            f"`--anything NOT RUN` still refuses.\n{proc.stdout}{proc.stderr}"
        )
        assert "argument position" in proc.stderr, (
            f"{command!r} was refused for a reason OTHER than its argument position "
            f"— this pin exists to track THAT gap, not to freeze any refusal:\n"
            f"{proc.stderr}"
        )


def test_a_NON_PATH_argument_behind_a_runner_is_not_a_citation(tmp_path):
    """There is no "project label" arm — a word is not an artifact.

    ⛔ An earlier revision of this branch accepted a lone bare word for
    `make`/`npm`/`docker` (`cmd:make test`), reasoning that a subcommand is not a
    path. Measured, that re-admitted the one-word claim this gate exists to refuse:
    `cmd:make NOT-RUN` beside a held artifact was ACCEPTED, and a 22-row ledger
    using it reported `uncovered: 0`, `CAPSTONE-LEDGER-RESULT: CLEAN`, exit 0. It
    bought nothing here either — this repo has no `Makefile` and no root
    `package.json` (`AGENTS.md`), so `make test` names no target that exists.

    A runner with NO argument at all (`cmd:make`) is still a record; a runner with a
    non-path argument is not.
    """
    for command in (
        "cmd:make NOT-RUN",
        "cmd:make failed",
        "cmd:make verified",
        "cmd:npm not_run",
        "cmd:docker DONE",
        "cmd:make test",
        "cmd:make I did not run anything, see docs/evidence/9664-capstone/README.md",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (command, proc.stdout + proc.stderr)

    # …while a runner with no target is a record that confers nothing.
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, "cmd:make"])]))
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


def test_prose_on_EITHER_side_of_a_real_command_is_refused(tmp_path):
    """A compound record cannot carry a disclaimer, before or after the command.

    ⛔ Measured on `origin/main`: `_command_body` scanned for the first RUNNER-shaped
    `&&` segment and SILENTLY DROPPED the rest, so
    `cmd:NOT RUN && uv run pytest tests/test_9664_capstone_verdict_ledger.py` — a
    record that says on its own line that the work was not run — was ACCEPTED. A
    22-row ledger citing the always-available tracked
    `docs/evidence/9664-capstone/README.md` plus that entry reported
    `criteria: 22  verified: 22  uncovered: 0`, printed
    `CAPSTONE-LEDGER-RESULT: CLEAN` and exited 0. The comment that justified the
    skip said "prose … never contains one at all", which is false the moment prose
    can be chained in FRONT of a command.

    ⛔ And the SAME hole mirrors: the first fix graded only the first segment, so
    `cmd:uv run pytest tests/… && I did not run this` was still ACCEPTED and still
    produced a clean 22-row ledger. A disclaimer after the command is the same
    claim as one before it, so EVERY segment is graded.
    """
    for attack in (
        "cmd:NOT RUN && uv run pytest tests/test_9664_capstone_verdict_ledger.py",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py && I did not run this",
        # ⛔ The same class, one SEPARATOR over. Measured at the round-2 head, which
        # split only on `&&`: every one of these was ACCEPTED, and a 22-row ledger
        # relabelled that way reported `verified: 22 uncovered: 0`,
        # `CAPSTONE-LEDGER-RESULT: CLEAN`, exit 0 — including the same text inside a
        # `bash -c` body, which inherits the splitter.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q; NOT RUN",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q || NOT RUN",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q | NOT RUN",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q & NOT RUN",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q\nNOT RUN",
        "cmd:bash -c 'uv run pytest tests/test_9664_capstone_verdict_ledger.py -q; NOT RUN'",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, attack])]))
        assert proc.returncode == cl.EXIT_INVALID, (attack, proc.stdout + proc.stderr)
        assert "not a command" in proc.stderr, proc.stderr

    # A compound record made only of commands, wrappers and decorative printers is
    # still a legitimate record — the two measured false blocks stay accepted.
    for legitimate in (
        "cmd:cd tests && uv run pytest test_9664_capstone_verdict_ledger.py -q",
        "cmd:set -e && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && echo PASS",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
        "&& ruff check tools/capstone_ledger.py",
        # A pipeline into a reader — the separator set must not cost this.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q | tail -20 log",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, legitimate])]))
        assert proc.returncode == cl.EXIT_CLEAN, (legitimate, proc.stdout + proc.stderr)


def test_a_comment_ends_the_LINE_and_the_next_line_is_still_graded(tmp_path):
    """A comment ends the command, so nothing hidden in one is graded.

    The comment rule must not become a hiding place: `NOT RUN # && uv run pytest
    …` is the prose `NOT RUN` followed by a comment, and prose before the command
    is still refused because the segment that reaches the grader is `NOT RUN`.
    Stated as a test because "the tail is a comment, so it is ignored" is one
    refactor away from "the tail is a comment, so skip the whole segment".

    A `cmd:` entry that is ALL comment names no command at all and stays refused —
    the comment rule shortens the line, it does not invent one.
    """
    for entry in (
        "cmd:NOT RUN # && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:# NOT RUN - I did not run this",
        # ⛔ A COMMENT ENDS THE LINE, NOT THE RECORD (#11506 review, cycle 3).
        # `break` at the `#` discarded the rest of the STRING, so a comment on line
        # 1 made every following line invisible — and because this same PR made
        # `\n` a separator, that RE-OPENED the newline fail-open it had just
        # closed. Measured at `e7776efc7`: the entry below printed
        # `uncovered: 0`, `CAPSTONE-LEDGER-RESULT: CLEAN`, exit 0, while the parent
        # commit refused it. `yaml.safe_dump` emits it as a double-quoted scalar, so
        # the `\n` reaches the tool as a real newline.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q  # ran; "
        "194 passed\nNOT RUN - I did not run this",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q  # ran\n; NOT RUN",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
        assert proc.returncode == cl.EXIT_INVALID, (entry, proc.stdout + proc.stderr)

    # …and the other half of the same defect: a LEADING comment is not the record,
    # so the command on the next line must still be graded (and accepted).
    leading = "cmd:# step 1\nuv run pytest tests/test_9664_capstone_verdict_ledger.py -q"
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, leading])]))
    assert proc.returncode == cl.EXIT_CLEAN, (
        "a comment-only first LINE is not the record: the shell runs the command on "
        "line 2, so this must be ACCEPTED. `break` on the `#` discarded line 2 and "
        "refused the whole entry — the opposite defect, from the same line: "
        + proc.stdout
        + proc.stderr
    )


@pytest.mark.parametrize(
    "disclaimer",
    [
        # Every one of these is a record that disclaims its own work and is
        # ACCEPTED at this head. They are pinned so the gap is DISCOVERABLE
        # instead of being assumed closed by the prose around them.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
        "&& bash I did not run this",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && python -c 'NOT RUN'",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
        "NOT RUN - I did not run this",
        # ⛔ A disclaimer carried in a SHELL COMMENT is ungraded (#11520 root 3).
        # The identical words outside a comment are refused, but behind a `#` they
        # yield a `verified` row and `uncovered: 0`. Grading comment CONTENT is a
        # prose scan this module deliberately rejects — it re-introduces the
        # measured false block `… -q  # ran; 194 passed` — and refusing a LEADING
        # comment line would false-block the accepted `# step 1` NEWLINE `uv run
        # pytest …`. So it is recorded here rather than closed at that price.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
        " # NOT RUN - I did not run this",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q ; # NOT RUN",
        # ⛔ A disclaimer carried INSIDE a trusted head's argument (#11520 root 4).
        # A segment headed by a _WRAPPER_VERB or a _DECORATIVE reader is admitted
        # WITHOUT grading one argument, so the disclaimer rides in as that head's
        # parameter and the record still exits 0 with `uncovered: 0`. Measured at
        # `cc63f614` and still at this head; the WRAPPER half (`export "I did not
        # run this" && …`) has since been closed and is asserted REFUSED below.
        # Closing the decorative half means requiring shell-shaped arguments from
        # `echo`/`printf`/…, which must first be measured against the accepted
        # `… && echo PASS` — so it is recorded, not closed at that price.
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
        '&& echo "I did not run this"',
        'cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && printf "%s" "NOT RUN"',
        # ⛔ Root 5: a QUOTED disclaimer in argument position, with a real artifact
        # after it (`pytest "-q I did not run this" <tracked path>`). Measured at
        # this head, not inferred: rc=0, `uncovered: 0`. The deferral that stops
        # root 2's unquoted sibling from false-blocking `pytest "-k not slow"
        # <tracked path>` cannot also refuse this one — the two tokens are
        # syntactically IDENTICAL, and telling a flag+VALUE from a SENTENCE is a
        # semantic judgement, not a grammar. The arm keeps the weaker side on
        # purpose: the module's own rule, recorded twice, is that "a refusal that
        # loses the finding is worse than the weaker arm", and exit 1 prints no
        # report. Without the artifact the same spelling IS refused (pinned by
        # `_UNREAD_TOKEN_BYPASSES`), so the deferral still closes the naked case.
        'cmd:uv run pytest "-q I did not run this" tests/test_9664_capstone_verdict_ledger.py',
        # ⛔ Root 6 was pinned HERE as a known-open false PASS. It is now CLOSED and
        # asserted REFUSED at the bottom of this test: `set -o <name>` validates the
        # option name against bash's own list (`set -o NOTRUN` → rc 2, so the `&&`
        # short-circuits and the graded command never ran). The inversion is the one
        # this docstring instructed, not a deletion.
    ],
)
def test_the_known_residual_false_PASSes_are_pinned_as_OPEN(tmp_path, disclaimer):
    """⛔ These still exit 0 — this test pins the GAP, not a guarantee (#11520).

    The `cmd:` arm refuses a bare-prose disclaimer on either side of the command,
    and on every separator. It does **not** grade a LATER segment that is
    runner-HEADED (`… && bash I did not run this`, `… && python -c 'NOT RUN'`), nor
    prose appended INSIDE the graded command's own segment after its artifact
    token (which is also what a hand-written multi-line YAML scalar folds to —
    PyYAML joins the lines with a space, so the `newline` separator never sees it).

    ⛔ DO NOT read a green suite as "the disclaimer class is closed". Closing the
    runner-headed half by grading every runner segment the way the FIRST is graded
    was measured to re-introduce the false block this whole ruleset exists to
    remove — `… && git status --short` and `… && git log --oneline` name a
    SUBCOMMAND in argument position, not a path, and would be refused. That trade
    is not worth making silently here; the gap is tracked as #11520 instead.

    If #11520 is closed, this test must be INVERTED (EXIT_INVALID), not deleted.

    ⛔ SIX carriages are recorded, not five — the list read complete while one was
    missing, which is how this branch's residual prose kept being wrong:
    (root 1) a LATER runner-headed segment, ungraded;
    (root 2) prose appended inside the graded command's own segment, after its
             artifact token (what a folded multi-line YAML scalar also becomes);
    (root 3) a disclaimer carried in a `#` comment;
    (root 4) a disclaimer carried as a TRUSTED HEAD's argument (`&& echo "I did not
             run this"`), because a decorative/wrapper segment is admitted without
             grading one argument. The WRAPPER half of root 4 is CLOSED — asserted
             at the bottom of this test — and the DECORATIVE half is not.
    (root 5) a QUOTED disclaimer in argument position beside a real artifact
             (`pytest "-q I did not run this" <tracked path>`), because the
             deferral cannot tell a quoted flag+VALUE from a quoted SENTENCE.

    ⛔ WHY NOT `xfail(strict=True)` — the standard idiom for a known defect, and its
    XPASS would be a tripwire no one can ignore. Two reasons it is not used here:
    (a) the same parametrized test also asserts the CLOSED halves below, which must
    never xfail, so root 5 alone would have to be split into its own test; and
    (b) every other carriage is pinned by the SAME assert-EXIT_CLEAN + documented
    inversion contract, so one root on a different contract makes the enumeration
    that this branch kept getting wrong harder to keep complete. The inversion
    instruction below is the tripwire, and it is checked by the reviewer, not by
    the runner.
    (root 6) `set -o <invalid-option>` before a `&&` — **NOW CLOSED** (this branch
             inverted this pin, as instructed below). `set -o <name>` validates the
             name against bash's own option list, so `set -o nosuchoption` is
             REFUSED: bash exits 2, the chain short-circuits, and the graded
             command never ran. The same validation now covers `set` short flags
             and the `export`/`unset` flag letters (all measured). So FIVE carriages
             remain open, and root 6 is asserted CLOSED at the bottom of this test.
    """
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, disclaimer])]))
    assert proc.returncode == cl.EXIT_CLEAN, (
        disclaimer,
        "#11520 may have been fixed — invert this pin to EXIT_INVALID rather "
        "than deleting it: " + proc.stdout + proc.stderr,
    )

    # Root 4's WRAPPER half is closed: a wrapper verb's argument is graded, so a
    # quoted sentence behind `export` is prose. Pinned here so the closed half
    # cannot silently reopen while the decorative half stays documented as open.
    for closed in (
        'cmd:export "I did not run this" && uv run pytest tests/ -q',
        'cmd:source "I did not run this" && uv run pytest tests/ -q',
        # Root 6, inverted from OPEN to CLOSED (see the docstring above).
        "cmd:set -o nosuchoption && uv run pytest tests/ -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, closed])]))
        assert proc.returncode == cl.EXIT_INVALID, (closed, proc.stdout + proc.stderr)


def test_a_subcommand_verb_is_admitted_per_runner_and_nowhere_else(tmp_path):
    """A subcommand word is a NON-ARTIFACT argument, so it is admitted PER RUNNER.

    ⛔ Measured false block at `0c259a39f3` AND on `origin/main` at `5ef5805f0`: the
    artifact rule reads the first token in argument position, so
    `cmd:ruff check tools/capstone_ledger.py` was refused with `'check' is not a
    file in this tree`, and `cmd:uv run ruff check …` / `cmd:uv run ruff format
    --check …` with it. Exit 1 prints NO report, so a lane recording the lint gate
    it actually ran lost the entire uncovered list — the P2 harm this PR exists to
    remove. `ruff` being in `RUNNERS` made the entry look supported while buying
    nothing, because the only shape it helped was a LATER segment, which is not
    graded at all (#11520).

    ⛔ The fix is NOT a global subcommand word set. A global set makes a bare word
    behind ANY runner skippable — measured under that mutant it newly admits
    `cmd:make check` and `cmd:docker format` (both pinned below), i.e. the
    one-word claim behind a runner this gate exists to refuse. Keyed by the runner
    ACTUALLY invoked — so `uv run ruff check` resolves the subcommand to `ruff` —
    and scoped to the runners this repo writes down: one subcommand token, once,
    immediately after its runner.

    ⛔ STILL REFUSED, and pinned as such: `cmd:ruff check .`. The repo's own
    `.github/workflows/main-lint.yml:215` runs a bare `ruff check .`, but `.`
    resolves to the repository root and names everything — the same vacuity that
    refuses `cmd:pytest .` — so a lane recording that shape must spell the target
    as a path (`ruff check tools/`, `ruff check <file>`, both accepted below).
    Admitting `.` under a consumed subcommand is a defensible narrowing (it would
    confer nothing, like `cmd:make`), but it weakens a load-bearing rule and
    belongs in its own change; here it is a DECLARED residual, not a silent one.
    """
    for legitimate in (
        "cmd:ruff check tools/capstone_ledger.py",
        "cmd:ruff format --check tools/capstone_ledger.py",
        "cmd:uv run ruff check tools/capstone_ledger.py",
        "cmd:uv run ruff format --check tests/test_9664_capstone_verdict_ledger.py",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, legitimate])]))
        assert proc.returncode == cl.EXIT_CLEAN, (legitimate, proc.stdout + proc.stderr)

    for attack in (
        # No map entry for these runners: a bare word behind them is prose.
        "cmd:git NOT-RUN",
        "cmd:make check",
        "cmd:docker format",
        "cmd:bash check NOT-RUN",
        # `ruff`'s map holds no such word, so the prose this arm exists to refuse
        # is still refused behind the runner that HAS a map.
        "cmd:ruff not - see tools/capstone_ledger.py",
        # The repo's own CI shape (`.github/workflows/main-lint.yml:215` is
        # `run: ruff check .`). `.` names everything, which is the vacuity the
        # artifact rule refuses — declared, pinned, and answered by spelling the
        # target as a path (`ruff check tools/` is accepted below).
        "cmd:ruff check .",
        "cmd:uv run ruff check .",
        # ONE subcommand token, immediately after its runner: a second bare word is
        # back in artifact position and must name something a clone holds.
        "cmd:ruff check format NOT-RUN",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, attack])]))
        assert proc.returncode == cl.EXIT_INVALID, (attack, proc.stdout + proc.stderr)

    # …and the shapes that already worked must keep working: a LATER runner
    # segment is not graded (#11520), so these are accepted for the same reason
    # they were accepted before — pinned so the subcommand map cannot start
    # refusing a compound record.
    for unchanged in (
        "cmd:uv run pytest tests/ -q && git status --short",
        "cmd:uv run pytest tests/ -q && git log --oneline",
        "cmd:uv run pytest tests/ -q && ruff check tools/capstone_ledger.py",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, unchanged])]))
        assert proc.returncode == cl.EXIT_CLEAN, (unchanged, proc.stdout + proc.stderr)


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


def test_a_glob_whose_matches_are_all_directories_names_no_artifact(tmp_path):
    """T6 in the GLOB arm, which had NO pin: `is_file()` is the only clause
    refusing a glob whose in-tree matches are all DIRECTORIES.

    MEASURED: deleting `resolved_match.is_file()` accepted `cmd:sh .github/w*`
    (-> `.github/workflows`) as `verified` with an EMPTY uncovered list, and the
    whole suite stayed green — the over-long/NUL entries and the symlink test never
    produce a directory-only match.
    """
    tree = tmp_path / "tree"
    (tree / "docs" / "only-a-directory").mkdir(parents=True)
    data = _ledger([_row("A", evidence=["cmd:sh docs/*"])])
    with pytest.raises(cl.InvalidLedger) as excinfo:
        cl.validate(data, repo_root=tree)
    assert "no file of this tree matches" in str(excinfo.value)


def test_a_glob_whose_only_match_resolves_outside_the_tree_is_invalid(tmp_path):
    """T6's last member, and the one declared class member that had NO pin.

    The glob arm's per-match `is_relative_to(root)` reads like belt-and-braces next
    to the literal-prefix check, but it is the ONLY thing refusing a glob whose
    PREFIX is a genuine in-tree directory while every match under it is a symlink
    pointing outside. MEASURED: deleting that one clause left the whole suite
    green and let `cmd:sh pure/*` (a directory holding one symlink pointing out of
    the tree) validate as `verified` with an EMPTY uncovered list — a false PASS on
    a citation that names no artifact of this tree.

    ⛔ The link target is part of the FIXTURE, not `/etc/hostname`: a dangling
    target is refused whether or not the containment clause is present (measured),
    so a host-shaped target would make this test pass vacuously on any box that
    lacks it — including the box a developer runs the suite on.
    """
    tree = tmp_path / "tree"
    (tree / "pure").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("x", encoding="utf-8")
    os.symlink(outside / "secret.md", tree / "pure" / "evil.md")

    with pytest.raises(cl.InvalidLedger) as glob_excinfo:
        cl.validate(_ledger([_row("A", evidence=["cmd:sh pure/*"])]), repo_root=tree)
    assert "no file of this tree matches" in str(glob_excinfo.value)

    # The same tree via a bare path: the link names a file that IS not in the tree.
    with pytest.raises(cl.InvalidLedger):
        cl.validate(_ledger([_row("B", evidence=["pure/evil.md"])]), repo_root=tree)


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


def test_the_committed_ledger_pins_its_denominator():
    data = cl.load_ledger(LEDGER)
    assert data["criteria_expected"] == 22
    assert len(data["criteria"]) == 22


def test_an_omitted_denominator_pin_is_invalid(tmp_path):
    """The pin must be enforced by the TOOL, not only by one test on one file.

    Measured before this: a ledger that simply left the key out exited 0, so
    "drop the uncovered rows AND delete the block" reached a clean exit — the
    committed artifact was protected by a property of itself, not of the check.
    """
    data = _ledger([_row("A")])
    del data["criteria_expected"]
    proc = _run_cli(tmp_path, data)
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "criteria_expected" in proc.stderr


@pytest.mark.parametrize("coerced", [True, False, 1.9])
def test_a_non_integer_denominator_is_invalid_not_coerced(tmp_path, coerced):
    """`True` IS an int in Python and `int(1.9)` truncates to 1: either would pass
    as a one-row pin against a one-row ledger, which is the shrink this pins.

    ⚠️ The QUOTED string `"1"` used to be in this list and is deliberately NOT any
    more: a quoted count is the same count, main accepted it, and refusing it was a
    fail-closed regression that bought no protection (`test_a_quoted_count_is_…`).
    """
    data = _ledger([_row("A")], criteria_expected=coerced)
    proc = _run_cli(tmp_path, data)
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "INTEGER" in proc.stderr


@pytest.mark.parametrize("quoted", ["1", " 1 "])
def test_a_quoted_count_is_the_same_count(tmp_path, quoted):
    """`criteria_expected: "1"` pins the same denominator as `criteria_expected: 1`.

    Main `810eb838c` accepted it; an earlier revision of this branch refused it,
    which was a gratuitous fail-closed regression (exit 1 also hides every
    uncovered row). Kept permissive, and pinned so it cannot silently re-tighten.
    """
    data = _ledger([_row("A")], criteria_expected=quoted)
    proc = _run_cli(tmp_path, data)
    assert proc.returncode == cl.EXIT_CLEAN, (quoted, proc.stdout + proc.stderr)


def test_a_shallow_checkout_is_unprovable_never_absent(tmp_path):
    """CI test jobs check out at `fetch-depth: 1`, where an ANCESTOR sha is not in
    the object database. Blaming the record reds the required check on a legitimate
    ledger (#9075 r4 P1), so the third outcome has to exist.

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


def test_a_prose_cmd_is_refused_beside_a_held_artifact(tmp_path):
    """⛔ THE PIN ON THE `cmd:` SHAPE GATE — the false pass this PR exists to close.

    This is deliberately NOT a `cmd:`-only row. A `cmd:`-only row is refused by
    `validate`'s held-artifact rule whether or not the shape gate runs, so it pins
    nothing here (measured: neutralising the gate in place left such a test green).

    The real configuration is a row that DOES hold a tracked artifact — and
    `docs/evidence/9664-capstone/README.md` is available to every row, so it is the
    cheapest way to manufacture a green ledger — plus a `cmd:` whose text says the
    work was not done. Before the gate this validated at `verified: 22`,
    `uncovered: 0`, exit 0. Mutation-verified: with the `RUNNERS` check removed this
    test fails and the row is CLEAN.
    """
    for prose in [
        "cmd:NOT RUN - see tools/capstone_ledger.py for the intended command",
        "cmd:verified manually by reading pyproject.toml",
        "cmd:see tools/capstone_ledger.py",
    ]:
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, prose])]))
        assert proc.returncode == cl.EXIT_INVALID, (prose, proc.stdout + proc.stderr)
        assert "not a command" in proc.stderr, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        # ⛔ These pin `_command_body`'s NORMALISATIONS, each of which was
        # mutation-tested as UNPINNED: reverting the backtick unwrap, the leading
        # `NAME=value` strip, or the `cd`-aware cwd each left the whole suite green
        # while the shape gate false-blocked a real record. A false block is exit 1,
        # and exit 1 computes NOTHING — it hides every uncovered row.
        "cmd:`uv run pytest tests/test_9664_capstone_verdict_ledger.py -q`",
        "cmd:TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:TORTOISE_DB_URI='docker://:f@localhost:6379/x' uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        # `cd` moves the cwd: `../tests/...` only resolves INSIDE tools/, so a
        # validator that dropped the `cd` and resolved at the root refuses it.
        "cmd:cd tools && uv run pytest ../tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:cd tools && uv run python capstone_ledger.py check",
    ],
)
def test_a_wrapped_command_is_not_a_false_block(tmp_path, command):
    """The shell WRAPPER around a real command must not invalidate the record."""
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
    assert proc.returncode == cl.EXIT_CLEAN, (command, proc.stdout + proc.stderr)


def test_a_script_containing_a_quoted_and_channel_is_accepted(tmp_path):
    """`&&` INSIDE a quoted `-c` script must not be split.

    A plain `text.split('&&')` cuts inside the quotes, so the record came back as
    `bash -c 'uv run pytest …` — unparseable, therefore INVALID, therefore exit 1,
    which computes NOTHING and hides the whole uncovered list. Mutation-verified:
    reverting `_split_chains` to `split("&&")` reddens this test.
    """
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:bash -c 'uv run pytest "
                        "tests/test_9664_capstone_verdict_ledger.py -q && echo done'",
                    ],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


def test_a_glob_whose_matches_are_all_untracked_is_refused(monkeypatch):
    """A glob is satisfied by a TRACKED match, not by whatever `glob` yields first.

    Two halves, both mutation-verified: (a) the tracked rule reaches the glob arm at
    all, and (b) it looks for ANY tracked match rather than refusing on the first
    untracked one — the order-dependent verdict that made the same tree accepted or
    refused depending on the filenames.
    """
    # (a) every match untracked -> refused
    monkeypatch.setattr(cl, "_is_tracked", lambda resolved, root: False)
    ok, why = cl._evidence_is_real("cmd:uv run python docs/evidence/9664-capstone/*.md", REPO_ROOT)
    assert ok is False, why
    assert "untracked" in why, why

    # (b) ONE tracked match is enough, whatever the enumeration order
    monkeypatch.setattr(
        cl,
        "_is_tracked",
        lambda resolved, root: resolved.name == "README.md",
    )
    ok, why = cl._evidence_is_real("cmd:uv run python docs/evidence/9664-capstone/*.md", REPO_ROOT)
    assert ok is True, why


def test_a_cd_that_would_leave_the_tree_is_refused(tmp_path):
    """Honouring `cd` must not let a record walk the check out of the checkout.

    ⛔ CORRECTED ENFORCEMENT POINT (fifth review cycle): this record is still
    refused, but no longer by the `cd` arm. `cd ../../..` RESOLVES and is a
    directory, so the shell's own precondition for `cd` holds and the segment is
    admitted; what refuses the record is the ARTIFACT rule, because `tests/x.py`
    no longer resolves inside the tree from the moved `cwd`. That is the right
    reason and it is strictly stronger: the tree rule is enforced on the artifact
    that is actually cited, rather than on every `cd` including a trailing one that
    no later segment depends on. Asserted on the message so a regression cannot
    quietly refuse it for an unrelated reason.
    """
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[REAL_EVIDENCE, "cmd:cd ../../.. && uv run pytest tests/x.py"],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "names no artifact of this tree" in proc.stderr, proc.stderr


def test_a_cd_that_cannot_succeed_is_refused_not_silently_ignored(tmp_path):
    """A `cd` whose target does not EXIST must refuse the record, not be skipped.

    Measured fail-open: `cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py
    -q && cd I did not run this` was ACCEPTED while the naked `… && I did not run
    this` was REFUSED — the same disclaimer, admitted by prefixing it with a `cd`
    that cannot succeed. In the shell a failing `cd` short-circuits the `&&`, so the
    graded command CANNOT have run; the old arm `continue`d regardless and graded it
    anyway.

    ⛔ `is_dir()` is the load-bearing half: `(root / target).resolve()` does NOT
    fail on a name that does not exist, so a containment test alone still accepted
    this record. Pinned here so a future simplification to a containment test reds.
    """
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
                        "&& cd I did not run this",
                    ],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "short-circuits" in proc.stderr, proc.stderr


def test_set_and_umask_accept_their_own_option_operands(tmp_path):
    """A canonical bash preamble must not invalidate the ledger.

    ⛔ MEASURED REGRESSION, caught by the second review cycle of this branch. The
    wrapper-argument grammar first required EVERY `set` operand to start with
    `-`/`+`, which refused `set -euo pipefail` — the line every `.ci-checks/*.sh`
    in this repo opens with — and the first fix recognised only a bare `-o`,
    missing the CLUSTER one word over (`-euo pipefail` is `-e -u -o pipefail`).
    `umask -S` had the same shape: `man bash` gives `umask [-p] [-S] [mode]`.

    Both are `EXIT_INVALID`, and exit 1 prints no report, so this class of false
    block costs the whole uncovered list — the harm the arm's own comment ranks as
    worse than the weaker verdict.
    """
    for command in (
        "cmd:set -euo pipefail && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:set -o pipefail && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:set -eu -o pipefail && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:umask -S && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:umask -p 022 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a false block on a "
            f"legitimate wrapper\n{proc.stdout}{proc.stderr}"
        )


def test_wrapper_grammars_admit_the_forms_bash_itself_accepts(tmp_path):
    """Three shell facts, each measured against bash — and each refused here.

    ⛔ MEASURED FALSE BLOCKS, all three found by the final review cycle of this
    branch, each verified by RUNNING bash and the classifier rather than by
    reading the grammar:

    * `umask u=rwx,g=rx,o=` — the symbolic mask is a COMMA-SEPARATED CLAUSE LIST,
      which is the exact spelling `_UMASK_ARG`'s own comment advertised, and the
      first grammar matched only ONE clause. bash: rc 0.
    * `set -- a b` — `--` ENDS option parsing, so every later operand is a
      POSITIONAL PARAMETER, not an option. bash: rc 0.
    * `unset 'A[0]'` — an array ELEMENT, not a bare NAME. bash: rc 0.

    All three are `EXIT_INVALID`, and exit 1 prints no report, so each one cost
    the whole uncovered list — the harm this arm's own comment ranks as worse
    than the weaker verdict. `export 'A[0]'` stays refused in the companion test:
    bash rejects it, so admitting it would be a false PASS instead.

    ⛔ DISCRIMINATION, MEASURED AND STATED HONESTLY: these three cases PASS against
    `origin/main`'s `tools/capstone_ledger.py`, because main has NO `umask`/`set`/
    `unset` argument grammar at all and therefore accepted them trivially. So this
    test is a REGRESSION PIN for the grammar THIS BRANCH introduces, not evidence
    that main was wrong here. The companion
    `test_the_stricter_wrapper_grammars_still_refuse_prose` DOES fail on main (main
    admitted `umask "I did not run this"`), which is where the discrimination lives.
    """
    for command in (
        "cmd:umask u=rwx,g=rx,o= && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:umask u=rwx && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:set -- a b && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:unset 'A[0]' && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — bash accepts it (rc 0), "
            f"so this is a false block\n{proc.stdout}{proc.stderr}"
        )


def test_the_stricter_wrapper_grammars_still_refuse_prose(tmp_path):
    """Widening the three grammars must not re-open the carriage they guard.

    The fix above admits `--`-terminated `set` operands and `unset` array
    elements; a grammar that admits EVERY operand would admit the disclaimer
    these wrappers were given an argument grammar to refuse. `export 'A[0]'` is
    pinned here too: bash errors on it ("not a valid identifier"), so its refusal
    is correct and must not be widened along with `unset`'s.
    """
    for command in (
        'cmd:umask "I did not run this" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q',
        'cmd:set "I did not run this" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q',
        "cmd:set -e 'I did not run this' && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        'cmd:unset "I did not run this" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q',
        "cmd:export 'A[0]' && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — prose rode in on a "
            f"wrapper the grammar was widened for\n{proc.stdout}{proc.stderr}"
        )


def test_the_assignment_carriage_is_open_and_declared(tmp_path):
    """`NAME=value` is an OPEN carriage, and the ledger now enumerates it as one.

    ⛔ MEASURED, third finding of the final review cycle: `_command_body` strips a
    leading `NAME=value` and `continue`s, so the assignment's VALUE is never
    graded and a disclaimer can ride in it while the row still reads `verified`
    (`uncovered: 0`, `CAPSTONE-LEDGER-RESULT: CLEAN`).

    Admitting it is CORRECT — an assignment runs nothing and cites nothing, so it
    is inert exactly like `cd`/`set` — so this is not a grammar bug. The defect was
    that the carriage was UNDECLARED: `NAME=value` was listed among the allowed
    wrappers while the ledger's "what is NOT closed" list omitted it, so it read as
    closed. This test pins the behaviour the ledger now declares, so the doc and
    the code cannot drift apart silently in either direction.
    """
    command = (
        'cmd:A="I did not run this" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'
    )
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
    assert proc.returncode == cl.EXIT_CLEAN, (
        "the assignment carriage changed shape without the ledger being updated "
        f"({proc.returncode})\n{proc.stdout}{proc.stderr}"
    )
    # ⛔ THE DECLARATION HALF IS READ, not assumed. Asserting only the exit code
    # left the ledger paragraph deletable with all tests still green — so the
    # "pinned" claim was unenforced. This reads the artifact the tool validates.
    ledger_text = LEDGER.read_text(encoding="utf-8")
    assert "`NAME=value` is the FIFTH carriage" in ledger_text, (
        "the ledger no longer declares the assignment carriage — a carriage that "
        "is open but UNLISTED reads as closed, which is the drift this pin exists "
        "to prevent"
    )


def test_the_carriage_total_is_the_list_it_summarises():
    """The arm's stated total must equal its own enumerated ordinals.

    ⛔ MEASURED, review of `4f9209849b`: the ledger enumerated `(1)`–`(6)` — the
    sixth added as a FALSE PASS, deliberately listed because "an unlisted carriage
    reads as closed" — while the paragraph that summarises the arm still said
    "FIVE carriages". Nothing reddened: `test_the_assignment_carriage_is_open_and
    _declared` asserts the ORDINAL ("`NAME=value` is the FIFTH carriage"), never
    the total, so the summary was the one number in the artifact with no reader.
    An UNDERCOUNTED total is the same harm as an unlisted entry by a shorter
    route: a reader who trusts the number has no reason to look for an entry
    above it.

    Nothing is hard-coded: the ordinals and the total are both READ from the
    ledger, so adding a `(7)` fails here until the summary says SEVEN. Both
    anchors are asserted, so a reworded section fails loudly instead of silently
    checking nothing.
    """
    text = LEDGER.read_text(encoding="utf-8")
    start_anchor = "(1) a later RUNNER-HEADED segment"
    assert start_anchor in text, (
        "the carriage section moved — update this pin's anchor, do not delete the "
        "pin: the total it checks is what reads as 'complete' to a reader"
    )
    section = text[text.rindex("\n", 0, text.index(start_anchor)) + 1 :]

    ordinals = [int(m.group(1)) for m in re.finditer(r"(?m)^\s*#\s*\((\d)\)\s", section)]
    assert ordinals, (
        "no numbered carriage markers found in the ledger section — the "
        "enumeration itself is gone, so the total cannot be checked"
    )
    assert ordinals == list(range(1, len(ordinals) + 1)), (
        f"the carriage list is no longer numbered 1..N consecutively: {ordinals!r}"
    )

    spelled = {
        "ONE": 1,
        "TWO": 2,
        "THREE": 3,
        "FOUR": 4,
        "FIVE": 5,
        "SIX": 6,
        "SEVEN": 7,
        "EIGHT": 8,
        "NINE": 9,
        "TEN": 10,
    }
    summary = re.search(r"\b([A-Z]+) carriages are enumerated above", section)
    assert summary, (
        "the arm's closing summary no longer states a total — reword it back or "
        "update this pin, but a summary that says no number cannot be checked "
        "and the enumeration then reads as whatever the reader assumes"
    )
    claimed = spelled.get(summary.group(1))
    assert claimed is not None, (
        f"the summary's total {summary.group(1)!r} is not a number this pin reads "
        "(extend the map, do not delete the pin)"
    )
    assert claimed == len(ordinals), (
        f"the arm's summary claims {summary.group(1)} carriages but the section "
        f"enumerates {len(ordinals)} — a total that undercounts its own list is "
        "how a listed carriage reads as closed"
    )

    # ⛔ THE SAME TOTAL IS STATED TWICE, so BOTH statements are read. The first
    # instance of this defect was in the section's OPENING sentence — "There are
    # FIVE such carriages" over an enumeration that ran to (6) — and the
    # closing-summary anchor above cannot see it; a pin that checks one of two
    # statements of one number leaves the other exactly as unread as the total was
    # before this test existed. (The quoted "FIVE carriages remain open" in the
    # summary's own prose is a DIFFERENT axis — #11520's ROOTS, not this list — so
    # the anchor stays on the phrasing that states THIS list's total.)
    intro = re.search(
        r"\b([A-Z]+) carriages are enumerated below",
        text[: text.index(start_anchor)],
    )
    assert intro, (
        "the carriage section's opening no longer states a total in the anchored "
        "phrasing — restore it, or delete this half of the pin AND the opening's "
        "number, so the total is stated in exactly one place instead of drifting "
        "in two"
    )
    claimed_intro = spelled.get(intro.group(1))
    assert claimed_intro is not None, (
        f"the opening's total {intro.group(1)!r} is not a number this pin reads "
        "(extend the map, do not delete the pin)"
    )
    assert claimed_intro == len(ordinals), (
        f"the section's opening claims {intro.group(1)} carriages but the section "
        f"enumerates {len(ordinals)} — this total has drifted twice, in two "
        "different sentences, and both are now checked"
    )


def test_wrapper_flags_and_option_names_the_shell_REJECTS_are_refused(tmp_path):
    """A flag bash rejects makes the `&&` short-circuit, so the command NEVER RAN.

    ⛔ MEASURED FALSE PASS, found by the review cycle after the grammar was
    widened, and worse than a false block because the record names a command that
    provably did not execute:

    * `set -o "I did not run this"` — bash exits 2 ("invalid option name"). The
      arm consumed ANY bare word as an option name, so the row reached
      `verified: 1` / `uncovered: 0` / `CLEAN` / exit 0.
    * `export -e NOTRUN` / `unset -e NOTRUN` — bash exits 2 ("invalid option").
      Every dashed token was skipped unvalidated.
    * `set -ZZZ` — bash exits 2; any dash-led token was accepted.

    `export -f fn` is NOT in this list even though bash exits 1: the FLAG is valid
    and the failure is an operand (`fn` is not a defined function), which is
    box-dependent and not this grammar's business.
    """
    for command in (
        'cmd:set -o "I did not run this" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q',
        "cmd:set -o NOTRUN && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:set -ZZZ && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:export -e NOTRUN && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:unset -p A && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED as clean ({proc.returncode}) — bash rejects the "
            f"option, so the `&&` short-circuits and the graded command never ran\n"
            f"{proc.stdout}{proc.stderr}"
        )


def test_umask_accepts_the_end_of_options_marker(tmp_path):
    """`umask -- 022` is valid bash (rc 0) and was refused — a false block.

    `set` was given `--` handling one commit earlier and `umask` was not, so the
    SAME asymmetry left `umask -- 022` refused while `set -- a b` was accepted.
    Exit 1 prints no report, so the false block lost the whole uncovered list.
    """
    for command in (
        "cmd:umask -- 022 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:umask -S -- 022 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        # ⛔ `umask 07777` exits 0; an octal cap of four digits false-blocked it.
        "cmd:umask 07777 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        # ⛔ `o` ANYWHERE in a `set` cluster takes the next bare word as the option
        # name — `set -oe pipefail` / `-eox` / `-ou` all exit 0. An `endswith("o")`
        # test false-blocked all three.
        "cmd:set -oe pipefail && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:set -ou pipefail && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        # ⛔ When the word after `-o` is ITSELF a flag, bash does not read it as an
        # option name: `set -o -e` and `set -o --` both exit 0.
        "cmd:set -o -e && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:set -o -- && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        # ⛔ `unset -` exits 0. (`shift $#` is NOT listed here: whether `shift`
        # fails is governed by the separate "control builtin fails before a chain"
        # rule, not by its operand grammar, and mid-chain it is still refused.)
        "cmd:unset - && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — bash accepts it (rc 0)\n"
            f"{proc.stdout}{proc.stderr}"
        )


def test_umask_and_export_rejections_match_bash(tmp_path):
    """`umask`/`export` spellings bash REJECTS must not be admitted.

    ⛔ MEASURED, and this is the same false-PASS class the enforced half exists to
    close — an invalid argument makes bash fail, the `&&` short-circuits, and the
    graded command never ran:

    * `umask --symbolic` — rc 2 (bash has no such option); it was on the accepted
      flag list, which contradicted the ledger sentence directly above it.
    * `umask a+s` / `g+s` / `o+t` / `u=X` — rc 1, "invalid symbolic mode character".
      The only symbolic mode characters `umask` accepts are `rwx`; `s`/`t`/`X` are
      `chmod`'s, and admitting them admitted records that never ran.
    * `export -` and `export +` — rc 1 ("not a valid identifier"); an empty flag
      string passed the subset test vacuously.
    """
    for command in (
        "cmd:umask --symbolic && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:umask a+s && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:umask u=X && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:export - && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:export + && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED as clean ({proc.returncode}) — bash rejects it, "
            f"so the graded command never ran\n{proc.stdout}{proc.stderr}"
        )


def test_a_quoted_flag_and_value_together_is_not_prose(tmp_path):
    """`pytest "-k not slow" <tracked path>` is a real record, not a disclaimer.

    ⛔ MEASURED FALSE BLOCK, caught by the second review cycle: the whitespace
    refusal was taken AT THE TOKEN, so a quoted FLAG+VALUE — a legitimate spelling
    a lane really types — was refused while the artifact after it was never reached.
    The refusal is now deferred to the end of the scan, which keeps the fail-open
    closed (`"-q I did not run this"` still names no artifact and is still refused,
    pinned by `_UNREAD_TOKEN_BYPASSES`) without catching this shape with it.
    """
    for command in (
        'cmd:uv run pytest "-k not slow" tests/test_9664_capstone_verdict_ledger.py',
        'cmd:uv run pytest "-q -x" tests/test_9664_capstone_verdict_ledger.py',
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a quoted flag+value is "
            f"a legitimate record when an artifact follows it\n{proc.stdout}{proc.stderr}"
        )


def test_a_clustered_short_flag_takes_its_body_from_the_next_token(tmp_path):
    """`bash -ec "<script>"` is `-e -c <script>`, not `-e` with body `c`.

    ⛔ `man bash` allows the operand-taking flag anywhere the group ENDS, so
    `_split_flag`'s first-two-characters rule graded the leftover `c`/`uc` as the
    script body and refused a legitimate record with the nonsense reason
    "script 'c' is not a command" (second review cycle).
    """
    for command in (
        'cmd:bash -ec "uv run pytest tests/test_9664_capstone_verdict_ledger.py"',
        'cmd:bash -euc "uv run pytest tests/test_9664_capstone_verdict_ledger.py"',
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a clustered short flag "
            f"takes its body from the NEXT token\n{proc.stdout}{proc.stderr}"
        )


#: ⛔ SCRIPT-FLAG and QUOTED-VALUE bypasses, each measured ACCEPTED at the head this
#: review was run against and each reported `verified` / `CLEAN` / exit 0. They are
#: one defect: a token the loop never READ. `-cBODY`/`--eval=BODY` is ONE shlex token
#: so `token in _SCRIPT_FLAGS` was False; a quoted token containing whitespace was
#: skipped as a "flag" and reached the runner-with-no-target arm.
_UNREAD_TOKEN_BYPASSES = [
    "cmd:bash -c'NOT RUN - see tools/capstone_ledger.py'",
    'cmd:python -c"NOT RUN"',
    'cmd:bash --eval="NOT RUN"',
    "cmd:bash -e'NOT RUN'",
    'cmd:uv run pytest "-q I did not run this"',
    'cmd:python "-NOT RUN - see tools/capstone_ledger.py"',
]


def test_an_attached_script_body_is_refused_whatever_it_contains(tmp_path):
    """An attached `-cBODY`/`--eval=BODY` is REFUSED: it cannot be a shell body.

    ⛔ MEASURED false PASS (fifth review cycle, P1). The whitespace deferral ran
    BEFORE the `_split_flag` arm, so every body that carried a SENTENCE was parked
    as `unread_prose` and `continue`d — the arm was never reached for exactly the
    bodies it exists for. With a held artifact token after the entry the parked
    prose was then CLEARED by `_names_artifact`, and:

        cmd:bash -c'NOT RUN - see tools/capstone_ledger.py' tests/…py

    reported `verified: 1 uncovered: 0` / `CAPSTONE-LEDGER-RESULT: CLEAN` / exit 0.

    ⛔ AND GRADING THE BODY WAS ITSELF THE WRONG FIX (sixth review cycle, P2; the
    primary source measured on this box):

        bash -c'echo hi'   -> rc 1, stderr `bash: - : invalid option`, body never
                              runs; STDOUT carries the `set -o` table, because `o`
                              parses as `-o`  (an earlier revision of this docstring
                              said "no output" — that was a MIS-STATED measurement)
        bash -c 'echo hi'  -> prints `hi`, rc 0

    With no space, `-cecho hi` is ONE argv word, so a shell reads what follows `-c`
    as more OPTION LETTERS and the body never runs — grading it recorded `verified`
    for a command bash refuses.

    ⛔ NOT UNIVERSAL (seventh review cycle, P2): for CPython and node the attached
    value IS the runnable form — `python3 -c'print(3)'` prints `3`, rc 0, and
    `node --eval='console.log(1)'` prints `1`. Those bodies are python/JS PROGRAMS,
    and this arm grades a body as a SHELL command line, which they can never be — so
    they are refused too. That refusal is a declared known-open FALSE BLOCK
    (#11520), NOT a claim that the spelling is unrunnable; this docstring and the
    arm's message say so explicitly, because asserting non-runnability there would
    be a false written justification.

    Refusing also makes the verdict independent of the cluster split, so `-cmake x`
    / `-cec x` / `-cverified` / `--eval=…` are all refused and a mis-split can
    neither admit a body nor silently drop a PREFIX (measured before this:
    `bash -c'ec uv run pytest <path>'` graded the remainder CLEAN, inventing a
    command that does not exist).

    The SPACED form — the only runnable spelling — must stay ACCEPTED, and is
    pinned here so the refusal cannot be widened into a false block.
    """
    for entry in (
        "cmd:bash -c'NOT RUN - see tools/capstone_ledger.py' "
        "tests/test_9664_capstone_verdict_ledger.py",
        'cmd:bash --eval="NOT RUN - see tools/capstone_ledger.py" '
        "tests/test_9664_capstone_verdict_ledger.py",
        "cmd:bash -e'NOT RUN - see tools/capstone_ledger.py' "
        "tests/test_9664_capstone_verdict_ledger.py",
        # Attached bodies that NAME an artifact are refused too — not because the
        # intent is dishonest, but because the spelling cannot run.
        "cmd:bash -c'uv run pytest tools/capstone_ledger.py'",
        'cmd:bash -c"uv run pytest tools/capstone_ledger.py"',
        # A body whose PREFIX is dropped by a mis-split would otherwise be graded
        # as a command that does not exist (`ec` is not a command).
        "cmd:bash -c'ec uv run pytest tools/capstone_ledger.py'",
        "cmd:bash -c'make tools/capstone_ledger.py'",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{entry!r} was ACCEPTED ({proc.returncode}) — an attached script body "
            f"cannot execute at all, so it cannot carry the verdict\n"
            f"{proc.stdout}{proc.stderr}"
        )
    # The SPACED spelling is the only runnable form and must stay ACCEPTED.
    for entry in (
        "cmd:bash -c 'uv run pytest tools/capstone_ledger.py'",
        'cmd:bash -c "uv run pytest tools/capstone_ledger.py"',
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{entry!r} was REFUSED ({proc.returncode}) — the SPACED form is the "
            f"only spelling bash actually runs, and refusing it is the false block "
            f"this fix must not introduce\n{proc.stdout}{proc.stderr}"
        )


def test_a_control_builtin_that_fails_before_a_chain_is_terminating(tmp_path):
    """A control builtin that FAILS short-circuits the `&&`, so nothing after ran.

    ⛔ MEASURED false PASS (fifth review cycle, P1). Only `exit` was
    position-restricted, and `return` was exempted on the strength of a `;`
    measurement (`bash -c 'return 0 ; echo RAN-AFTER'` prints RAN-AFTER) — but a
    RECORD chains with `&&`, where the same failure short-circuits. Measured on this
    box, each followed by `&& echo RAN`:

        return 0/1   rc 2   no RAN   → `cmd:return 0 && uv run pytest …` was CLEAN
        shift 5      rc 1   no RAN   → `cmd:shift 5 && uv run pytest …` was CLEAN
        shift        rc 1   no RAN   (bare `shift` is `shift 1`)
        shift 0      rc 0   RAN      → must stay legal
        break        rc 0   RAN      → must stay legal
        continue     rc 0   RAN      → must stay legal

    DECLARED RESIDUAL (#11520 root 6): `set -o <invalid-option>` fails the same way
    and is NOT refused here — that needs the closed set of bash option NAMES, which
    is flag-VALUE knowledge this module deliberately refuses to carry. Pinned OPEN in
    `test_the_known_residual_false_PASSes_are_pinned_as_OPEN`.
    """
    command_tail = " && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q"
    for head in ("return 0", "return 1", "shift 5", "shift"):
        entry = f"cmd:{head}{command_tail}"
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{entry!r} was ACCEPTED ({proc.returncode}) — `{head}` fails at script "
            f"level, so the `&&` short-circuits and the runner never ran\n"
            f"{proc.stdout}{proc.stderr}"
        )
    # The spellings that SUCCEED at script level must stay legal.
    for head in ("shift 0", "break", "continue"):
        entry = f"cmd:{head}{command_tail}"
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{entry!r} was REFUSED ({proc.returncode}) — `{head}` succeeds at script "
            f"level (measured rc 0), so the runner after it really runs\n"
            f"{proc.stdout}{proc.stderr}"
        )
    # A TRAILING control builtin stays legal: nothing after it is being claimed.
    entry = "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q ; return 0"
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
    assert proc.returncode == cl.EXIT_CLEAN, (
        f"{entry!r} was REFUSED ({proc.returncode}) — a TRAILING `return` is legal\n"
        f"{proc.stdout}{proc.stderr}"
    )


@pytest.mark.parametrize("entry", _UNREAD_TOKEN_BYPASSES)
def test_a_token_the_loop_never_reads_cannot_carry_the_verdict(tmp_path, entry):
    """Prose in a wrapper or in quotes is prose: it must not reach `verified`."""
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
    assert proc.returncode == cl.EXIT_INVALID, (
        f"{entry!r} was ACCEPTED ({proc.returncode}): a token the loop never reads "
        f"carried the verdict\n{proc.stdout}{proc.stderr}"
    )


# ── THIRD review cycle: four measured false blocks ──────────────────
#
# Each of these is a record `origin/main` ACCEPTED and this branch REFUSED — and
# exit 1 prints no uncovered list, so each one cost the whole report. They are
# pinned individually because they have FOUR distinct roots, and a single test
# over all of them would let a regression in one hide behind a pass in another.


def test_a_flag_cluster_before_a_separate_script_token_is_not_a_body(tmp_path):
    """`bash -eu -c CMD` is `-e -u -c CMD`; `bash -cu CMD` is `-c -u CMD`.

    ⛔ MEASURED against real bash: both print their script. `_split_flag` took the
    LAST script flag it found and graded everything after it as the script BODY, so
    the leftover `u` became "script 'u' is not a command" — the old split only held
    when the operand-taking flag happened to be LAST in the group (`-ec`, `-euc`,
    already pinned above). A false block is exit 1, and exit 1 computes NOTHING.
    """
    for command in (
        "cmd:bash -eu -c 'uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'",
        "cmd:sh -eu -c 'uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'",
        "cmd:bash -cu 'uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'",
        "cmd:bash -eux -c 'uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a pure option cluster is "
            f"MORE FLAGS, and the script is the next token\n{proc.stdout}{proc.stderr}"
        )


def test_an_attached_script_body_is_never_trusted(tmp_path):
    """The cluster fix must NOT re-open the `-cBODY` prose bypass it sits beside.

    ⛔ The reason `_split_flag` exists at all is that `bash -c'NOT RUN'` is ONE
    shlex token. Treating every remainder as "more flags" would restore exactly the
    fail-open `_UNREAD_TOKEN_BYPASSES` closes.

    ⛔ KEPT CORRECT AT THIS HEAD (sixth review cycle): the claim above is the
    REASON the arm now REFUSES every attached body instead of grading it. A body
    containing whitespace is refused by the attached-script arm, and so is one with
    NO whitespace — both spellings are commands bash cannot execute, because
    `-cBODY` makes the body read as more OPTION LETTERS (measured: `bash -c'echo
    hi'` -> `- : invalid option`, rc 1). Grading a body would ACCEPT a record for a
    command that cannot have executed, which is the fail-open class this whole arm
    exists to close.

    The lowercase cases are the regression pin: "all lowercase" is NOT "an option
    cluster", and treating it as one admitted `cmd:bash -cverified` to `verified` /
    `uncovered: 0` / exit 0 — a one-word CLAIM satisfying the strongest verdict.
    """
    for command in (
        "cmd:bash -c'NOT RUN - see tools/capstone_ledger.py'",
        'cmd:bash -e"NOT RUN"',
        "cmd:bash -cNOTRUNx",
        "cmd:bash -cnotrun",
        "cmd:bash -cverified",
        "cmd:sh -cdidnotrun",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — an attached body is "
            f"graded as a script, and prose names no artifact\n{proc.stdout}{proc.stderr}"
        )


def test_a_control_builtin_tail_is_not_prose(tmp_path):
    """`|| exit 1` is this repo's OWN recipe, and `.` is POSIX `source`.

    ⛔ MEASURED false block. `AGENTS.md` itself records `uri="$(…)" || exit 1`, and
    CI docs use `&& exit 0`; `. .venv/bin/activate` is `source` spelled the POSIX way.
    All were ACCEPTED on `origin/main` and REFUSED here as "neither a command nor a
    shell wrapper" — while the message asserted the record "may carry commands and
    wrappers, not prose". `Path(".").name` is `""`, which is why `.` never matched
    even after it joined `_WRAPPER_VERBS`.
    """
    for command in (
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q || exit 1",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && exit 0",
        "cmd:. .venv/bin/activate && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q || return 1",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a shell CONTROL builtin "
            f"cites nothing and must not be graded as prose\n{proc.stdout}{proc.stderr}"
        )


def test_a_record_of_only_wrappers_still_names_no_command(tmp_path):
    """Admitting control builtins must not admit a record made ONLY of them.

    ⛔ This is the pin behind `_WRAPPER_VERBS`' safety claim. If `exit`/`.` could
    satisfy a row on their own, `cmd:exit 1` would be a one-word verdict — the exact
    fail-open the whole `cmd:` arm exists to refuse.
    """
    for command in (
        "cmd:exit 1",
        "cmd:exit 0 && true",
        "cmd:. .venv/bin/activate",
        "cmd:export FOO=1 || exit 1",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — a record whose only "
            f"segments are wrappers names no command\n{proc.stdout}{proc.stderr}"
        )


def test_a_tilde_in_a_source_wrapper_is_expanded(tmp_path):
    """`source ~/.bashrc` must resolve against `$HOME`, not `<repo>/~`.

    ⛔ MEASURED false block: `cwd / "~/.bashrc"` is `<repo>/~/.bashrc`, a path that
    exists on NO box, so the record was refused on every machine — not the disclosed
    `.venv` box-dependence, but a definite non-resolution. `HOME` is pinned to a
    tmp dir here so the test proves the EXPANSION and not the runner's own dotfiles.
    """
    home = tmp_path / "home"
    home.mkdir()
    (home / ".bashrc").write_text("# probe\n", encoding="utf-8")
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:source ~/.bashrc && uv run pytest "
                        "tests/test_9664_capstone_verdict_ledger.py -q",
                    ],
                )
            ]
        ),
        env={"HOME": str(home)},
    )
    assert proc.returncode == cl.EXIT_CLEAN, (
        f"`source ~/.bashrc` was REFUSED ({proc.returncode}) with HOME={home} — the "
        f"`~` is not expanded before the join\n{proc.stdout}{proc.stderr}"
    )


def test_a_stacked_cd_option_group_is_stripped_not_skipped(tmp_path):
    """A second option group must not turn the `cd` invisible.

    ⛔ MEASURED false PASS (sixth review cycle): stripping exactly ONE option group
    left `cd -P -L /tmp` with `-L /tmp` still dashed, and the dashed target was
    silently `continue`d — so the segment was neither graded nor APPLIED and the
    command after it was graded from the UNMOVED directory. `cmd:cd -P -L /tmp &&
    uv run pytest tests/…` was ACCEPTED while bash runs it from `/tmp`, where the
    file does not exist. The single-group `cd -P tools` and the stacked
    `cd -P -L tools` must also agree.
    """
    for command in (
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cd -P tools",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cd -P -L tools",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cd -L -- tools",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — the option groups are not "
            f"all stripped, so the spellings disagree\n{proc.stdout}{proc.stderr}"
        )
    # The false PASS: bash cds to /tmp, so the artifact below cannot resolve there.
    for command in (
        "cmd:cd -P -L /tmp && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:cd -L -- /tmp && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — the `cd` moved to /tmp, "
            f"where the cited artifact does not exist\n{proc.stdout}{proc.stderr}"
        )
    # ⛔ A MERGED dash group is NOT two groups. `cd -PL tests` is rc 0 but
    # `cd -P-L tests` is rc 2 (`cd: --: invalid option`), so the `&&` short-circuits
    # and the graded command never ran. A zero-width separator between groups turned
    # `-P-L` into `-P` + `-L` and ACCEPTED that record (seventh review cycle) — the
    # exact false PASS this branch exists to close, and one `origin/main` refuses.
    for opt in ("-P-L", "-L-P", "-P--", "-P@", "-e-L"):
        command = f"cmd:cd {opt} tests && uv run pytest test_9664_capstone_verdict_ledger.py -q"
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — bash rejects `cd {opt}` "
            f"with 'invalid option', so the graded command never ran\n"
            f"{proc.stdout}{proc.stderr}"
        )
    # …while every spelling bash DOES accept must be admitted.
    for opt in ("-P", "-L", "-PL", "-LP", "-Pe", "--"):
        command = f"cmd:cd {opt} tests && uv run pytest test_9664_capstone_verdict_ledger.py -q"
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — bash accepts `cd {opt}` "
            f"(rc 0)\n{proc.stdout}{proc.stderr}"
        )


def test_a_lone_assignment_segment_is_a_wrapper_not_prose(tmp_path):
    """`FOO=1 && <cmd>` is legal shell that really runs the cmd.

    ⛔ MEASURED false block (ninth review cycle): `_LEADING_ASSIGNMENT` ended in
    `\\s+`, so a segment consisting ONLY of `NAME=value` — with nothing after it in
    that segment, the ordinary `FOO=1 && …` spelling — never matched and fell through
    to the prose refusal, while the refusal message itself listed `NAME=value` among
    the admissible wrappers. `origin/main` accepted the record and
    `bash -c 'FOO=1 && echo RAN=$FOO'` prints RAN=1. Exit 1 also hides the whole
    uncovered list, so the cost is the entire report.
    """
    for command in (
        "cmd:FOO=1 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:FOO=1 BAR=2 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:TORTOISE_DB_URI='docker://u:p@h:6379/g' && uv run pytest "
        "tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a lone NAME=value segment "
            f"is a wrapper, and the refusal message says so\n{proc.stdout}{proc.stderr}"
        )


def test_a_null_cd_target_is_a_no_op(tmp_path):
    """bash: "A null directory name is the same as the current directory."

    ⛔ MEASURED false block (ninth review cycle): `cd ""` was lumped in with the
    "working directory cannot be known" refusal (`cd --` is `$HOME`, `cd -` is
    `$OLDPWD`), but a null target CHANGES NOTHING — `bash -c 'cd ""; echo rc=$?'` is
    rc 0 with the cwd unchanged — so the later segment provably ran from `cwd`.
    `origin/main` accepted the record. The two genuinely-unknowable spellings are
    still refused, pinned below.
    """
    for command in (
        'cmd:cd "" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q',
        "cmd:cd '' && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a null directory name is "
            f"the current directory\n{proc.stdout}{proc.stderr}"
        )
    for command in (
        "cmd:cd -- && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:cd - && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — `$HOME`/`$OLDPWD` cannot "
            f"be known from the record\n{proc.stdout}{proc.stderr}"
        )
    # ⛔ `-P` DEFEATS the null-name rule. Measured: `cd ""`, `cd -L ""`, `cd -e ""`
    # and `cd -- ""` are rc 0, but `cd -P ""` and `cd -Pe ""` are rc 1 with
    # `cd: : No such file or directory` — so the `&&` short-circuits and no later
    # segment ran. Admitting the `-P` spelling was a false PASS (tenth review cycle).
    for opt in ("-P", "-Pe", "-P --"):
        command = f'cmd:cd {opt} "" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f'{command!r} was ACCEPTED ({proc.returncode}) — `cd -P ""` exits 1 in '
            f"bash, so the graded command never ran\n{proc.stdout}{proc.stderr}"
        )
    # ⛔ bash resolves `-L`/`-P` by their LAST occurrence, not by "contains a P".
    # Measured: `cd -PL ""`, `cd -P -L ""` and `cd -PeL ""` are rc 0; `cd -LP ""`,
    # `cd -L -P ""` and `cd -LeP ""` are rc 1. Testing containment refused all six
    # and false-blocked four legitimate spellings (final review pass).
    for opt, reaches in (
        ("-PL", True),
        ("-P -L", True),
        ("-PeL", True),
        ("-LP", False),
        ("-L -P", False),
        ("-LeP", False),
    ):
        command = f'cmd:cd {opt} "" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        if reaches:
            assert proc.returncode == cl.EXIT_CLEAN, (
                f"{command!r} was REFUSED ({proc.returncode}) — bash reaches the `&&` "
                f'for `cd {opt} ""` (last of L/P wins)\n{proc.stdout}{proc.stderr}'
            )
        else:
            assert proc.returncode == cl.EXIT_INVALID, (
                f'{command!r} was ACCEPTED ({proc.returncode}) — `cd {opt} ""` exits 1 '
                f"in bash (last of L/P wins)\n{proc.stdout}{proc.stderr}"
            )
    # ⛔ `--` ENDS OPTION PARSING. Reading the mode across a post-`--` group treated
    # the operand `-L` as an option, so `cd -P -- -L ""` came out non-physical and
    # was ADMITTED — while bash exits 1 with "too many arguments" (`-L` is a second
    # operand there) and the graded command never ran. MEASURED: the previous head
    # admitted 68 of the 584 spellings whose `cd` bash rejects, every one containing
    # a `--`; this head admits 0 and agrees with bash on all 584.
    for opt in ("-P -- -L", "-- -L", "-- -P", "-PL -- -L"):
        command = f'cmd:cd {opt} "" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q'
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f'{command!r} was ACCEPTED ({proc.returncode}) — bash exits 1 with "too '
            f'many arguments" for `cd {opt} ""`\n{proc.stdout}{proc.stderr}'
        )


def test_an_exit_followed_only_by_inert_segments_is_still_last(tmp_path):
    """`<cmd> ; exit 0 ; A=1` DID run the command; `A=1` never executes.

    ⛔ MEASURED false block (tenth review cycle): the `exit`-position rule counted
    every raw segment, so an assignment AFTER the `exit` made it look non-final —
    while widening `_LEADING_ASSIGNMENT` to end-of-segment was what made that
    assignment an admitted wrapper in the first place. `origin/main` accepted the
    record and `bash -c 'echo RAN ; exit 0 ; A=1'` prints RAN. An empty segment, a
    lone `NAME=value` and a `cd` all execute nothing that is being cited, so none of
    them may count as a segment "after" the `exit`.

    ⛔ A RUNNER after the `exit` must still be refused — that is the false PASS the
    guard exists for, pinned below.
    """
    for tail in (
        " ; exit 0 ; A=1",
        " ; exit 0 ; A=1; B=2",
        " ; exit 0 ; cd docs",
        " ; exit 0 ; export FOO=1",
    ):
        command = "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q" + tail
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — only an INERT segment "
            f"follows the exit, so the exit is still last\n{proc.stdout}{proc.stderr}"
        )
    for command in (
        "cmd:exit 0 ; uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:exit 0 ; bash I did not run this",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — a RUNNER after the exit "
            f"never ran\n{proc.stdout}{proc.stderr}"
        )


def test_an_exit_that_is_not_the_last_segment_is_refused(tmp_path):
    """`exit` TERMINATES the shell, so a later segment provably never ran.

    ⛔ MEASURED false PASS (eighth review cycle): admitting `exit` as a harmless
    wrapper let `cmd:exit 0 ; uv run pytest …` and `cmd:exit 0 || uv run pytest …`
    reach `verified` / `uncovered: 0` / `CAPSTONE-LEDGER-RESULT: CLEAN` / exit 0,
    while `bash -c 'exit 0 ; echo RAN-AFTER'` prints NOTHING — the runner was never
    reached. `origin/main` refuses both. This is the unconditional form of exactly
    what the `cd` guard beside it closes.

    `return` is NOT terminating on a `;` — bash prints an error and CONTINUES
    (measured: `bash -c 'return 0 ; echo RAN-AFTER'` prints RAN-AFTER). ⛔ BUT IT IS
    TERMINATING ON A `&&`, which is how a RECORD chains: measured,
    `bash -c 'return 0 && echo RAN-AFTER'` prints only the `return` error, rc 2, and
    NO RAN-AFTER. That distinction was the fifth review cycle's P1: this docstring
    used to generalise the `;` measurement to a general "not position-restricted",
    and `cmd:return 0 && uv run pytest …` was measured CLEAN / exit 0 for a runner
    bash never reached. See `_fails_before_a_chain` and the dedicated test below.
    """
    for command in (
        "cmd:exit 0 ; uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:exit 0 || uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:exit 1 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — `exit` ends the shell, so "
            f"the runner after it never ran\n{proc.stdout}{proc.stderr}"
        )
    # A TRAILING `exit` is the spelling this repo actually records, and stays legal.
    for tail in (" || exit 1", " ; exit $?", " && exit 0", " || exit"):
        command = "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q" + tail
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a TRAILING exit is legal\n"
            f"{proc.stdout}{proc.stderr}"
        )


def test_a_quoted_cd_target_grades_like_an_unquoted_one(tmp_path):
    """`cd -L "tests"` is `cd tests`; quoting the path is ordinary shell.

    ⛔ MEASURED false block (eighth review cycle): quote characters were stripped off
    the WHOLE target BEFORE the options came off, so `cd -L "tests"` lost only its
    trailing quote and kept a literal leading one, failing `is_dir()` with "does not
    name a directory" — while `bash -c 'cd -L "tests"'` is rc 0. The target is now
    quote-unwrapped with `shlex` AFTER the option strip. The artifact cited below is
    one that genuinely resolves from the moved `cwd`; the cd, not the artifact, is
    what is under test.
    """
    for command in (
        'cmd:cd -L "tests" && uv run pytest test_9664_capstone_verdict_ledger.py -q',
        "cmd:cd -P 'tests' && uv run pytest test_9664_capstone_verdict_ledger.py -q",
        'cmd:cd -- "tests" && uv run pytest test_9664_capstone_verdict_ledger.py -q',
        'cmd:cd -PL "tests" && uv run pytest test_9664_capstone_verdict_ledger.py -q',
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a quoted target must grade "
            f"like an unquoted one\n{proc.stdout}{proc.stderr}"
        )


def test_a_cd_with_no_resolvable_directory_is_refused(tmp_path):
    """`cd --` is `$HOME` and `cd -` is `$OLDPWD`: neither cwd can be known.

    ⛔ Grading a later artifact from the UNMOVED directory would admit a record for
    a command the shell runs somewhere else, so this is the one `cd` case where
    refusing is the fail-closed answer rather than the false block. Disclosed in the
    ledger's NOT-CLOSED block as part of the `cd` box-dependence (#11520).
    """
    for command in (
        "cmd:cd -- && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:cd - && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — its working directory "
            f"cannot be determined\n{proc.stdout}{proc.stderr}"
        )


def test_a_pushd_or_popd_segment_is_a_known_false_block(tmp_path):
    """KNOWN-OPEN (#11520): pushd/popd are refused, and the reason is the STACK.

    ⛔ Not because their operand is a directory — the `cd` arm grades a directory
    operand too. It is that pushd/popd manipulate a directory STACK this parser
    cannot model, so the cwd after them is unknowable. Pinned because the refusal is
    a new false block introduced by this branch (`origin/main` accepted both) and was
    previously uncovered by any test.
    """
    for command in (
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && popd",
        "cmd:pushd tools && uv run pytest ../tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} is no longer refused ({proc.returncode}) — the pushd/popd arm "
            f"CLOSED. Invert this pin and drop the NOT-CLOSED sentence\n"
            f"{proc.stdout}{proc.stderr}"
        )


def test_a_control_builtin_argument_is_graded_not_skipped(tmp_path):
    """A control builtin's argument has a GRAMMAR, and prose does not match it.

    ⛔ MEASURED prose carriage (fifth review cycle): adding `exit`/`return`/`shift`/
    `break`/`continue` to `_WRAPPER_VERBS` without an argument branch left their
    arguments UNREAD — `_wrapper_argument_defect` fell through to `return None` — so
    `… && exit "I did not run this"` reached `verified` / `uncovered: 0` / exit 0
    behind every control head. `export`/`unset`/`umask`/`set` refused the same
    carriage, which is what made the ledger's "the WRAPPER half of that root is
    closed" sentence false. `man bash` gives each of them one optional integer.
    """
    for wrapper in ("exit", "return", "shift", "break", "continue"):
        command = (
            "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && "
            f'{wrapper} "I did not run this"'
        )
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — a control builtin's "
            f"argument is prose, not an integer\n{proc.stdout}{proc.stderr}"
        )
    # ⛔ ARITY: `man bash` allows ONE operand and the shell ENFORCES it. Measured,
    # `bash -c 'exit 0 0 && echo RAN'` prints "exit: too many arguments" and the
    # chain status is 1, so the graded command never ran. Validating each argument
    # without bounding the count admitted these (sixth review cycle).
    for command in (
        "cmd:exit 0 0 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && exit 0 0",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && shift 1 2",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — the shell refuses more "
            f"than one argument, so the chain short-circuits\n{proc.stdout}{proc.stderr}"
        )


_SENTINEL_OPERANDS = (
    "'I did not run this'",
    "/nonexistent/9664-carriage-probe",
)


def test_the_reader_head_carriage_enumerates_every_decorative_head(tmp_path):
    """The (6) carriage's head set must BE `_DECORATIVE`, split by measured behaviour.

    ⛔ MEASURED, review of `b610f0d03`: carriage (6) declared a brace set of TWELVE
    reader/sink heads while `_DECORATIVE` holds TWENTY, so `cut`, `tr` and `uniq`
    reached `uncovered: 0` / CLEAN / exit 0 with an "I did not run this" operand and
    were OUTSIDE the enumerated family — measured: `cut "I did not run this" && …`
    graded CLEAN while bash exited 1, the `&&` short-circuiting so the graded
    command provably never ran. The ledger's own rule is "an unlisted carriage
    reads as closed", which makes an enumeration that is narrower than the code the
    same defect by a shorter route.

    ⛔ AND THE SPLIT IS MEASURED, NOT ASSERTED — the review after that fix caught
    the first version of this pin classifying `awk` as a false-PASS head on the
    claim that it "fails on a path that does not exist". It does not: `awk` parses
    its operand as an awk PROGRAM, and both a bare word and a path are valid ones,
    so it exits 0 on both shapes and can never short-circuit the `&&`. A hard-coded
    `inert` set would have shipped that misclassification as a green pin — and the
    same sentence had already been wrong twice. So the split is DERIVED BY RUNNING
    bash: each `_DECORATIVE` head is executed with two operand shapes in an empty
    temp directory, and a head is INERT iff it exits 0 on both. Both halves then
    come from measurement plus the code, and neither can disagree with reality
    without this test going red.
    """
    # MEASURED, in a FRESH EMPTY DIRECTORY PER HEAD: a head is INERT iff it exits 0 on
    # BOTH a bare-word operand and a path that cannot exist.
    # ⛔ WHY PER HEAD AND NOT ONE SHARED CWD — measured, review of `85cbdb99d`: the
    # `tee` probe CREATES the file the bare-word operand names, so every head measured
    # after it ran in a directory that was no longer empty and `uniq`/`wc` then exited 0
    # on the bare word (measuring them 0/1 instead of 1/1). The bucket happened to come
    # out the same, but the instrument's stated purity was false, and a head whose ONLY
    # failing shape is the bare word would have been silently bucketed INERT — the
    # under-declaration this pin exists to prevent, passing green.
    # (The bare-word shape alone is not a discriminator — `cat "x"`'s rc depends on
    # whether a file of that name happens to exist in the cwd.)
    inert = set()
    for head in sorted(cl._DECORATIVE):
        rcs = set()
        for operand in _SENTINEL_OPERANDS:
            with tempfile.TemporaryDirectory() as probe_cwd:
                rcs.add(
                    subprocess.run(
                        ["bash", "-c", f"{head} {operand}"],
                        cwd=probe_cwd,
                        capture_output=True,
                        check=False,
                    ).returncode
                )
        if rcs == {0}:
            inert.add(head)
    # A vacuous measurement would silently put every head in one bucket: assert that
    # the run actually discriminated, so a box where `bash` is missing cannot pass.
    assert len(inert) < len(cl._DECORATIVE), (
        "no `_DECORATIVE` head failed on either operand shape — the measurement did "
        f"not discriminate (inert={sorted(inert)}); check that bash ran at all"
    )
    text = LEDGER.read_text(encoding="utf-8")
    declared_match = re.search(r"READER/SINK head `: \{([^}]*)\}`", text)
    assert declared_match, (
        "carriage (6)'s head set is no longer declared in the anchored form — "
        "restore it, do not delete this pin: an enumeration that is not checked "
        "against the code is how three failing heads went undeclared"
    )
    # The set is WRAPPED across comment lines, so each token carries the line's `#`
    # prefix and indentation. Stripping it here (rather than reformatting the ledger
    # to suit the parser) keeps the anchor independent of where the line breaks fall.
    declared = {
        h.strip().lstrip("#").strip()
        for h in declared_match.group(1).split(",")
        if h.strip().lstrip("#").strip()
    }
    expected = set(cl._DECORATIVE) - inert
    assert declared == expected, (
        "carriage (6) enumerates the false-PASS family incompletely: "
        f"missing={sorted(expected - declared)} extra={sorted(declared - expected)}. "
        "An unlisted carriage reads as closed — declare the head (or, if it is "
        f"inert, move it to the inert set, measured here as {sorted(inert)}) "
        "rather than leaving it out"
    )


def test_a_reader_head_before_the_command_is_a_known_false_PASS(tmp_path):
    """A READER/SINK head's operand is ungraded BEFORE the command — pinned OPEN.

    ⛔ MEASURED at `22668a11`, and this pin records a FALSE PASS, not a guarantee.
    `_wrapper_argument_defect` returns `None` for every `_DECORATIVE` head, and that
    family is not inert: `cat`, `grep`, `wait`, `tee`, `ls`, `sed`, `awk`, `jq`,
    `sort`, `wc`, `head` and `tail` FAIL on an operand that does not resolve, so the
    `&&` short-circuits and the graded command provably never runs. Likewise the
    `export`/`unset` branch SKIPS flag-prefixed operands, so `export -f FOO`'s NAME
    is never read. Each record below reaches `uncovered: 0` / CLEAN / exit 0 with the
    committed tool while bash exits non-zero:

        cmd:cat "I did not run this" && uv run pytest …   bash rc 1
        cmd:grep "I did not run this" /nonexistent && …   bash rc 2
        cmd:export -f FOO && uv run pytest …             bash rc 1

    ⛔ WHY THE ECHO CARRIAGE IS DIFFERENT. `… && echo "I did not run this"` IS inert
    data: bash executes `echo` and it SUCCEEDS, so nothing short-circuits. The
    ledger used to give that same reason for the whole decorative family, which is
    false for readers/sinks — the justification, not only the carriage, was wrong.

    ⛔ DO NOT delete this pin without closing the carriage: the fix is a GRAMMAR
    decision (grade a non-runner head's operative operand the way `source` already
    is, measured first against the accepted `… && echo PASS`), tracked on #11520
    with the other five carriages. Eight review cycles have each closed one spelling
    and opened the next; inverting this pin is what closing it looks like.
    """
    for command in (
        'cmd:cat "I did not run this" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q',
        'cmd:grep "I did not run this" /nonexistent && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q',
        'cmd:cut "I did not run this" && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q',
        "cmd:export -f FOO && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} changed behaviour ({proc.returncode}) — #11520 may have been "
            f"fixed: INVERT this pin to EXIT_INVALID rather than deleting it\n"
            f"{proc.stdout}{proc.stderr}"
        )


def test_a_cd_is_graded_on_the_shell_precondition_not_on_containment(tmp_path):
    """`cd /tmp` SUCCEEDS, so refusing it asserted a failure that did not happen.

    ⛔ MEASURED false block (fifth review cycle): the arm required the target to be
    inside the tree, so a trailing `&& cd /tmp` made the whole ledger INVALID and
    exit 1 printed no report. The verdict also turned on a DASH — `cd -P /tmp` and
    `cd -` were accepted because the leading `-` skipped grading entirely, while
    `cd /tmp` was refused. Options are now stripped first and the test is the
    shell's own: does the target exist and is it a directory?

    ⛔ The anti-bypass is preserved and re-verified: a `cd` that does not resolve is
    still refused, and a `cd` that escapes the tree cannot rescue a later artifact,
    because the artifact rule still requires the target INSIDE the tree.
    """
    for command in (
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cd /tmp",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cd -P /tmp",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cd -L /tmp",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cd tools",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — a `cd` that resolves did "
            f"not fail\n{proc.stdout}{proc.stderr}"
        )
    # The bypass this arm exists for: a `cd` that CANNOT succeed, and one that
    # escapes the tree to rescue a later artifact from the tree rule.
    for command in (
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && cd I did not run this",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q "
        "&& cd /nonexistent-capstone-probe",
        "cmd:cd /tmp && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} was ACCEPTED ({proc.returncode}) — a `cd` that cannot "
            f"succeed, or one that moves the tree rule out of reach\n{proc.stdout}{proc.stderr}"
        )


def test_a_non_runner_prep_segment_is_a_known_false_block(tmp_path):
    """KNOWN-OPEN (#11520 root 5): a non-runner program heading a segment is REFUSED.

    ⛔ This is a FALSE BLOCK, pinned rather than fixed. `mkdir -p out && uv run
    pytest …` is shell-valid and `origin/main` accepted it, so the refusal (and the
    `exit 1` that hides the whole uncovered list) costs a real finding. It stays
    open because both obvious repairs were measured to re-open a prose carriage:

      * admit the segment when its HEAD is a bare program name → re-opens
        `… && I did not run this` (head `I`);
      * admit it when it LOOKS shell-shaped (has a `-`-flag or a numeric arg) →
        re-opens `… && I did not run this -q`.

    ⛔ IF THIS TEST STARTS FAILING, the arm learned to admit prep segments — that is
    a CLOSURE, not a regression: invert this pin (assert `EXIT_CLEAN`), re-check that
    `… && I did not run this` and `… && I did not run this -q` are still refused,
    and drop the corresponding paragraph from the ledger's NOT-CLOSED block.
    """
    for command in (
        "cmd:mkdir -p /tmp/capstone-out && uv run pytest "
        "tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:rm -rf .pytest_cache && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:sleep 2 && uv run pytest tests/test_9664_capstone_verdict_ledger.py -q",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{command!r} is no longer refused ({proc.returncode}) — the prep-segment "
            f"arm CLOSED. Invert this pin and drop the NOT-CLOSED paragraph, after "
            f"re-checking `… && I did not run this` is still refused\n"
            f"{proc.stdout}{proc.stderr}"
        )


def test_a_deciding_wrapper_argument_is_read_not_skipped(tmp_path):
    """A wrapper's argument is checked where the SHELL's grammar decides it.

    ⛔ `source` is gradeable only on EXISTENCE. `is_file()` proves the target is
    there, not that sourcing it SUCCEEDS, and requiring TRACKED would refuse
    `source .venv/bin/activate` — a command this branch already pins as a
    legitimate record. The residual is pinned by
    `test_source_of_an_existing_but_unsourceable_file_is_a_known_gap`.
    """
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:set NOT-RUN && uv run pytest "
                        "tests/test_9664_capstone_verdict_ledger.py",
                    ],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr


def test_source_of_an_existing_but_unsourceable_file_is_a_known_gap(tmp_path):
    """KNOWN-OPEN (#11520): `source` is graded on EXISTENCE, not on success.

    ⛔ Measured: `source tests/test_9664_capstone_verdict_ledger.py` names a real
    file that bash REFUSES (exit 2), so the `&&` short-circuits and the graded
    command provably never ran — yet the record is ACCEPTED. Closing it needs the
    target to be TRACKED and `bash -n` clean, which would refuse
    `source .venv/bin/activate` (gitignored, and already pinned as a legitimate
    record). Flagged by the second review cycle; disclosed here instead of bought
    with a new false block.

    INVERT this pin (EXIT_INVALID) when #11520 is closed — do not delete it.
    """
    command = (
        "cmd:source tests/test_9664_capstone_verdict_ledger.py && uv run pytest "
        "tests/test_9664_capstone_verdict_ledger.py -q"
    )
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
    assert proc.returncode != cl.EXIT_INVALID, (
        f"{command!r} is now REFUSED — if `source` gained a TRACKED/`bash -n` rule, "
        f"invert this pin and re-check `source .venv/bin/activate` is not refused.\n"
        f"{proc.stdout}{proc.stderr}"
    )


#: ⛔ DEPLOYED RESIDUAL, pinned as OPEN rather than left for a reader to infer as
#: closed. Three wrapper heads cannot be decided without a runtime judgement:
#: * a decorative PRINTER — `echo` may legitimately print any text, and
#:   `… && echo PASS` must stay legal;
#: * `export` — `export NOT RUN` splits into two VALID shell names;
#: * `source` — see `test_a_deciding_wrapper_argument_is_read_not_skipped`:
#:   existence does not prove it succeeds, and a strict rule is box-dependent.
#: Closing any of them needs a semantic judgement, not a grammar — so it is
#: recorded, on #11520 (the open root for `cmd:` disclaimers the arm does not
#: grade), and pinned here so a future fix is a DELIBERATE change.
_DEPLOYED_WRAPPER_HEAD_RESIDUALS = [
    "cmd:echo NOT RUN - I did not run this && uv run pytest "
    "tests/test_9664_capstone_verdict_ledger.py -q",
    "cmd:export NOT RUN && uv run pytest tests/test_9664_capstone_verdict_ledger.py",
]


@pytest.mark.parametrize("entry", _DEPLOYED_WRAPPER_HEAD_RESIDUALS)
def test_the_deployed_wrapper_head_residual_is_pinned(tmp_path, entry):
    """Known-open (#11520): a wrapper HEAD an author can put anything behind.

    This asserts the CURRENT behaviour so the residual cannot rot into an
    undocumented state. When #11520 is closed, THIS test is the one that flips —
    and flipping it is the deliberate act, not an accident.

    ⛔ `EXIT_CLEAN` exactly (second review cycle): `!= EXIT_INVALID` was satisfied by
    0, 2, 3 AND 4, so the pin said "not refused" rather than "admitted as a record"
    and would have passed if the row became UNCOVERED or the mode changed.
    """
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
    assert proc.returncode == cl.EXIT_CLEAN, (
        f"{entry!r} did not reach the admitted-as-a-record arm ({proc.returncode}). "
        f"If it is now REFUSED, #11520 may have been closed — delete this pin and "
        f"move the entry to `_UNREAD_TOKEN_BYPASSES`.\n{proc.stdout}{proc.stderr}"
    )
    assert "CAPSTONE-LEDGER-RESULT: CLEAN" in proc.stdout, proc.stdout


def test_a_cmd_target_that_is_not_tracked_is_refused(monkeypatch):
    """The TRACKED rule applies to a `cmd:` target, not only to a bare path entry.

    Both arms make the same claim — "an artifact a reader's clone holds" — so a
    gitignored `WORKTREE-REPORT.md` must not count as a named artifact through the
    command arm while the path arm refuses it. Asserted on the wiring: `_is_tracked`
    is monkeypatched, so this needs no gitignored file on disk.
    """
    monkeypatch.setattr(cl, "_is_tracked", lambda resolved, root: False)
    ok, why = cl._evidence_is_real(f"cmd:uv run python {REAL_EVIDENCE}", REPO_ROOT)
    assert ok is False, why
    assert "not TRACKED by git" in why, why


def test_commit_state_is_unmeasurable_when_git_cannot_be_asked(monkeypatch):
    """No `git` on PATH is NOT "this clone is shallow" — they must not be folded.

    Folding them was a measured fail-open: with git unreachable, a forged
    `measured_at.sha` rendered as provenance with no diagnostic. Mutation-verified:
    changing this branch's `unmeasurable` back to `unprovable` reddens this test.
    """

    def no_git(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(cl.subprocess, "run", no_git)
    assert cl._commit_state("0" * 40) == "unmeasurable"


def test_commit_state_is_unmeasurable_when_the_shallow_probe_also_fails(monkeypatch):
    """A failing shallow PROBE is also "could not be asked", not "shallow".

    Mutation-verified: changing that branch's `unmeasurable` to `unprovable`
    reddens exactly this test (ported from #11474's mutation-proven pair).
    """

    class _Failed:
        returncode = 128
        stdout = ""

    monkeypatch.setattr(cl.subprocess, "run", lambda *a, **k: _Failed())
    assert cl._commit_state("0" * 40) == "unmeasurable"


def test_an_unmeasurable_sha_is_a_defect(monkeypatch):
    """`validate` must REFUSE `unmeasurable` — the state is worthless unhandled.

    Mutation-verified: removing the `elif state == "unmeasurable"` branch leaves
    every other test green and lets a forged sha through as `uncovered: 0`.
    """
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root=cl.REPO_ROOT: "unmeasurable")
    data = _ledger([_row("A")])
    data["measured_at"]["sha"] = "deadbeef" * 5
    with pytest.raises(cl.InvalidLedger) as excinfo:
        cl.validate(data)
    assert "cannot be verified" in str(excinfo.value)


# --- Ported from the peer lane's 35ea7658e (three commits on top of this same
# rebase head, written independently against the same review findings). Kept
# because each pins something this branch did not: the `absent` arm END TO END in
# a real full clone, the quoted-script split, the unquoted chain, a vacuous glob
# beside a HELD path, and the PRINTED git-unavailable abstention.


def test_a_fabricated_sha_is_refused_end_to_end_in_a_full_clone(tmp_path):
    """HERMETIC, and the arm the wiring pin above cannot reach by itself.

    `test_a_sha_that_names_no_commit_is_invalid` pins `validate`'s DECISION with the
    state machine stubbed, because the live checkout's DEPTH is not a property a
    deterministic test may depend on: on this box the shared `.git` is a FULL clone
    (`git rev-parse --is-shallow-repository` -> false, so a forged sha already
    answers `absent` here), while a CI shard's depth-1 checkout answers `unprovable`
    for the same input. That leaves the real path — a full clone, a forged sha,
    `validate` calling `_commit_state` — with no end-to-end pin at all.
    `_commit_state` honouring `repo_root` is what makes this reachable: a temp FULL
    clone answers `absent`, so the refusal fires for real rather than by stubbing.

    The cited artifact is committed in that same temp repo, so the ONLY defect is
    the sha — otherwise this would pass on the tracked rule instead, for the wrong
    reason.
    """
    import subprocess as sp

    repo = tmp_path / "fullrepo"
    repo.mkdir()
    (repo / "held.txt").write_text("held\n", encoding="utf-8")
    git = ["git", "-C", str(repo)]
    sp.run([*git, "init", "-q"], check=True)
    sp.run([*git, "add", "held.txt"], check=True)
    # `commit.gpgsign=false` is part of being HERMETIC: this fixture SEEDS a commit,
    # so on a box that signs commits it would ERROR (exit 128, "gpg failed to sign
    # the data") instead of asserting — a pin failing for a reason unrelated to its
    # arm. The sibling temp-repo fixture in this file already configures it.
    sp.run(
        [
            *git,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    assert (
        sp.run(
            [*git, "rev-parse", "--is-shallow-repository"], capture_output=True, text=True
        ).stdout.strip()
        == "false"
    ), "this fixture must be a FULL clone, or `absent` is unreachable"

    data = _ledger([_row("A", evidence=["held.txt"])])
    data["measured_at"]["sha"] = "deadbeef" * 5
    with pytest.raises(cl.InvalidLedger) as excinfo:
        cl.validate(data, repo_root=repo)
    # The refusal is the SHA, not a tracked-artifact complaint.
    assert "names no commit" in str(excinfo.value), str(excinfo.value)
    assert "TRACKED" not in str(excinfo.value), str(excinfo.value)


def test_a_quoted_script_body_with_a_chain_is_one_command(tmp_path):
    """`bash -c 'a && b'` is ONE command; splitting the raw string breaks it.

    `text.split("&&")` cuts a quoted script body mid-quote, so `shlex` rejects the
    halves and the row is refused as "not a parseable command" — exit 1, which
    prints no uncovered list at all. Measured on a legitimate record, and it lands
    on the exact shape `_SCRIPT_FLAGS` exists to accept (`bash -c '…'`), which
    `origin/main` accepted before this branch added a shape gate. Reverting
    `_split_on_chain` to `text.split("&&")` reds this and nothing else.
    """
    proc = _run_cli(
        tmp_path,
        _ledger(
            [
                _row(
                    "A",
                    evidence=[
                        REAL_EVIDENCE,
                        "cmd:bash -c 'uv run pytest "
                        "tests/test_9664_capstone_verdict_ledger.py -q && echo done'",
                    ],
                )
            ]
        ),
    )
    assert proc.returncode == cl.EXIT_CLEAN, proc.stderr


@pytest.mark.parametrize(
    "command",
    [
        # The discriminating case for BOTH halves of the splitter: `cd tools` moves
        # the effective directory, so the second segment's `../tests/...` only
        # resolves if the chain WAS split AND the `cd` was applied.
        "cmd:cd tools && uv run pytest ../tests/test_9664_capstone_verdict_ledger.py -q",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && echo PASS",
        "cmd:uv run pytest tests/test_9664_capstone_verdict_ledger.py -q && uv run pytest tests/test_ci_manifest.py -q",
    ],
)
def test_a_real_unquoted_chain_is_split_and_each_segment_graded(tmp_path, command):
    """A bare `&&` IS a separator, and the chained record is the normal case.

    The quoted-body test above pins the mirror-image half (`&&` inside quotes must
    NOT split). Replacing the splitter with `return [text.strip()]` — never split
    at all — left all 150 tests green while refusing `cd tools && uv run …` as
    "must START with a program that runs something", which is exit 1 and prints no
    uncovered list: the exact false block this round exists to remove. A pin in one
    direction only is not a pin on the contract.
    """
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
    assert proc.returncode == cl.EXIT_CLEAN, proc.stdout + proc.stderr


def test_a_vacuous_glob_cannot_carry_a_verified_row_BESIDE_a_held_path(tmp_path):
    """The glob-vacuity guard only ever matters on a row that ALREADY holds an artifact.

    Every `cmd:sh ./*` string in this file sits on a row citing no held artifact, so
    the row rule refuses it with or without the glob arm — which left the arm
    unpinned: `_glob_names_a_directory -> True` kept the whole suite green. Measured
    end-to-end on that mutant, a 22-row all-`verified` ledger citing `README.md`
    plus `cmd:sh ./*` exited **0** with an empty uncovered list — the false PASS the
    arm was added to refuse.
    """
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, "cmd:sh ./*"])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "names no artifact" in proc.stderr, proc.stderr


def test_a_checkout_without_git_REFUSES_and_NAMES_IT(monkeypatch, capsys):
    """No `git` on PATH REFUSES the row — never a traceback, and never a pass.

    ⛔ THIS TEST USED TO PIN THE OPPOSITE, and the opposite shipped. It asserted an
    ABSTENTION (`_tracked_unprovable()` -> True, record not blamed), on the reasoning
    that a git-less checkout (tarball, build context) must not false-red a legitimate
    ledger. That reasoning is right about the false red and wrong about the mechanism:
    `subprocess.run` raises `OSError` for exactly one reason — the probe could not be
    SPAWNED — and EMFILE/ENOMEM/EAGAIN is the same exception. Measured: with only the
    `ls-files` probe raising `OSError(EMFILE)` on a box that HAS git, a `.gitignore`d
    artifact answered `(True, '')` and the record exited **0 CLEAN**. An abstention
    keyed on the exception class cannot tell "no git here" from "the fork failed",
    so it fails OPEN in the case that matters.

    What the old test actually needed is preserved and asserted below: NO TRACEBACK,
    and the reason is PRINTED with the ledger marker. A git-less box still refuses the
    record — at the sha arm first, and here at the TRACKED arm too — which is the
    pre-shallow doctrine (`unmeasurable` = nothing measured = refused).

    ⛔ WHAT ISOLATES THE TRACKED ARM IS THE STDERR ASSERTION, NOT THE STUB. `validate`
    ACCUMULATES problems, so with `_commit_state` left unstubbed the sha problem and the
    TRACKED problem travel in one joined message and every assertion here still passes
    (measured). The stub narrows the message; the `could not be RUN` line is the only
    evidence that the arm under test is the one that fired.
    """
    import types

    def _boom(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(cl, "subprocess", types.SimpleNamespace(run=_boom))
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root: "exists")
    with pytest.raises(cl.InvalidLedger) as excinfo:
        cl.validate(_ledger([_row("A")]))
    msg = str(excinfo.value)
    assert "did not answer here" in msg, msg
    # ⛔ It must NOT claim git answered. On this box git was never asked, and the file
    # is tracked and present in every clone — reporting it "not TRACKED by git" is the
    # same guess-the-cause defect this change exists to remove, one layer up.
    assert "not TRACKED by git" not in msg, msg
    err = capsys.readouterr().err
    assert "could not be RUN" in err, err  # the real cause is named, not guessed
    assert "Traceback" not in err, err


# --- Ported VERBATIM from #11476 (main) at 810eb838c, so its B-arm and forgery
# pins are not lost in the rebase. These are not new tests: every one below is
# byte-identical to main and pins an arm that existed on main and that THIS
# branch (before the rebase) did not have — the tracked-path rule, the merge-key
# refusal, the reserved report token, the printed-field flattening, NUL and
# ENAMETOOLONG handling.


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
    # ⛔ The MESSAGE is the assertion, not just the exit code: without the refusal
    # the same file is still INVALID, but as "not parseable YAML" — a different
    # defect that happens to share a code. Mutation-verified: neutralising the merge
    # tag check leaves the exit code at 1 and changes only this text.
    assert "merge key" in proc.stderr, proc.stderr
    assert "not parseable YAML" not in proc.stderr, proc.stderr


def test_an_untracked_artifact_is_invalid(tmp_path):
    """HERMETIC: a temp git repo with one committed file and one untracked file.

    The previous version of this test used `WORKTREE-REPORT.md`, which is
    gitignored and created only by a lane — so the test SKIPPED in every CI run and
    the refusal it claims to pin was unpinned there: deleting `_is_tracked` from the
    resolver would have stayed green in CI while a gitignored lane artifact counted
    as held evidence.

    The repository setup lives in `_hermetic_repo` — it was duplicated here verbatim,
    and the signed-commit-proof flag (`-c commit.gpgsign=false`) only has to be right
    in ONE place (#11474 review).
    """
    repo = _hermetic_repo(tmp_path)

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


def _refusal_exit_status(exc: ast.Call) -> int | None:
    """The PROCESS status a guard's `raise SystemExit(...)` leaves behind (#11342).

    The caller has ALREADY pinned the shape this function is handed — a one-argument
    `raise SystemExit(<msg>)` with no keywords (the assertions above the only call
    site) — so the no-argument spellings are not this function's business: a
    `SystemExit()` guard is refused there, by name, before it reaches here. Guarding
    for them here as well was an unreachable branch behind an assert, which is why
    it is gone rather than mirrored.

    Mirrors CPython's own conversion — `.code` becomes a status via `0 if code is
    None else (code if isinstance(code, int) else 1)` — and the OS then keeps
    only the low 8 bits. Both steps matter: `SystemExit(None)`,
    `SystemExit(False)`, `SystemExit(0)`, `SystemExit(256)` and `SystemExit(768)`
    all leave the process at **0**, so a bare `code != 0` would report a guard
    that prints the full refusal and then exits CLEAN as passing.

    The argument is RESOLVED with `ast.literal_eval`, not pattern-matched on
    `ast.Constant`: `-256` parses as `UnaryOp(USub, Constant(256))`, which leaves
    the process at **0** while a Constant-only check calls it "a message" and
    returns 1 — the same fail-open, one node type over. No separate `_UNRESOLVED`
    sentinel exists: an unresolvable argument IS the ``None`` return, and two
    spellings of one state is a trap for the next editor.

    `literal_eval` folds sign and number literals but NOT arithmetic: `0 + 0`,
    `2 ** 8`, `len('')` and `int(0)` all raise `ValueError`, so they — and every
    bare `Name` — return ``None`` and the caller reports them UNPROVEN. That is
    deliberate rather than a gap: the guard is the first statement after
    `import sys`, so a module-level constant it named would be assigned BELOW it
    and the refusal would be a `NameError` traceback (`EXIT_INVALID` is assigned
    at :151 under a guard at :113), which the census reds.
    """
    arg = exc.args[0]
    # An f-string is a MESSAGE, and CPython prints a str argument then exits 1.
    # `raise SystemExit(f"…")` is the shape both pinned tools use.
    if isinstance(arg, ast.JoinedStr):
        return 1
    try:
        value: object = ast.literal_eval(arg)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None
    if value is None:
        return 0
    if isinstance(value, str):
        return 1
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        # `int.__and__`, not `&`: an int SUBCLASS can override `__and__`, and the
        # OS applies int arithmetic whatever the subclass declares.
        return int.__and__(value, 0xFF)
    # Any OTHER resolvable object (a float, a list, a tuple, bytes) is the `else`
    # arm of CPython's own conversion: it prints the object and exits **1**. It is
    # reachable, so it is NOT the UNPROVEN return — `None` here would report a shape
    # CPython grades exactly as unproven, i.e. a red over a legal guard.
    return 1


@pytest.mark.parametrize(
    "argument,expected",
    [
        ('f"msg"', 1),  # an f-string IS a message: CPython prints it and exits 1
        ('"EXIT_INVALID"', 1),  # so is a str literal
        ("0", 0),
        ("1", 1),
        ("2", 2),
        ("256", 0),  # the OS keeps only the low 8 bits
        ("-256", 0),
        ("768", 0),
        ("None", 0),
        ("False", 0),
        ("True", 1),
        ("0 + 0", None),  # `literal_eval` folds no BinOp — UNPROVEN, not 0
        ("EXIT_INVALID", None),  # nor a bare Name
        ("3.5", 1),  # a resolvable NON-int is CPython's `else` arm: print, exit 1
        ("[1, 2]", 1),  # …and so is any other object literal
    ],
)
def test_the_refusal_status_resolver_matches_cpython(argument, expected):
    """The resolver's OWN table, executed.

    Without this row set the resolver was covered only by the two pinned guards,
    and those reach exactly TWO arms — the f-string one
    (`capstone_ledger.py`'s `SystemExit(f"…")`) and the small-int one
    (`build_site_icons.py`'s `SystemExit(4)`) — so the `str`, `None`, `bool` and
    trailing-`return 1` arms had no coverage at all, and the fail-closed `None`
    (UNPROVEN) return could be mutated to `1` with the whole file GREEN. Measured:
    that mutation left `assert status is not None` untouched, because no pinned
    guard reaches the `except`. A table is the only way to execute those arms; the
    resolver's docstring already asserts this table, so it is now executed rather
    than described.

    The table carries BOTH terminal arms, which is why the `3.5` / `[1, 2]` rows
    exist: they reach the trailing `return 1` (CPython's `else` arm — print the
    object, exit 1), which the `0 + 0` / `EXIT_INVALID` rows never touch because
    those leave through the `except` instead. Measured: with only the unresolvable
    rows, mutating the trailing `return 1` to `return None` left the whole table
    GREEN — the arm was described by the docstring and exercised by nothing.
    """
    exc = ast.parse(f"raise SystemExit({argument})").body[0].exc
    assert isinstance(exc, ast.Call)
    assert _refusal_exit_status(exc) == expected, (argument, expected)


@pytest.mark.parametrize(
    "source,expected",
    [
        ('print("m", file=sys.stderr)', True),
        ('print("m")', False),
        ('print("m", file=sys.stdout)', False),
        ('print("m", file=stderr)', False),
        ('print("m", flush=True)', False),
    ],
)
def test_the_stderr_print_detector_requires_the_file_keyword(source, expected):
    """The print-shape detector on its own, executed in both directions.

    `tools/build_site_icons.py` DOES take this branch — it refuses with
    `print(…, file=sys.stderr)` then `raise SystemExit(4)` — so the detector is
    reached from a pinned tool. What no pinned tool reaches is its FALSE side: the
    row that says a print WITHOUT `file=sys.stderr` is wrong, which is the arm a
    future edit can silently invert. Mutating `_is_stderr_print` to `return True`
    was GREEN in both cases before these rows existed, which is why the table tests
    both directions rather than only the shape a live guard happens to have.
    """
    call = ast.parse(source).body[0].value
    assert isinstance(call, ast.Call)
    assert _is_stderr_print(call) is expected, source


def _census_module():
    """The entry-point census, loaded by path — the SINGLE OWNER of its predicates.

    A copy of the census's `_BARE_INVOCATION` regex inside this file would drift
    narrower again (that is how the advertised-form arm ended up weaker than the
    census it mirrors: a docstring-only substring misses a COMMENT, a `USAGE`
    constant and the `python3 ./tools/...` spelling, all measured), so the
    pattern is imported, not restated. Loaded by path with `importlib` — the same
    mechanism this file already uses for the tools under test — so it cannot
    depend on pytest's `sys.path` layout.
    """
    path = REPO_ROOT / "tests" / "test_entry_point_python_guard.py"
    spec = importlib.util.spec_from_file_location("_entry_point_python_guard_census", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_stderr_print(call: ast.Call) -> bool:
    """Whether a `print(...)` call writes to `sys.stderr`.

    The census reads a refusal from TWO channels, and only ONE of them makes a
    stdout print invisible:

      * for a tool it pins a `REFUSAL_CODES` row for, it RUNS the tool and reads
        `probe.stderr` (`test_the_refusal_exit_code_is_the_one_the_tool_can_spare`,
        parametrized over `REFUSAL_CODES`) — a refusal printed to stdout is then
        invisible to it;
      * for a tool with NO row it only executes the guard node and joins BOTH
        channels — `str(refusal.value) + captured.out + captured.err` — so a
        stdout print IS visible there.

    The channel is therefore a CONTRACT only where the census pins one: a stdout
    print for a tool with a `REFUSAL_CODES` row is unreadable to the census and
    must red; the same shape for a tool with NO row is legal, and demanding stderr
    there would red a census-LEGAL guard — the false-red class this pin must not
    add. Both tools carry a row TODAY (main's #11507 added
    `tools/build_site_icons.py: 4`), and the caller now REFUSES a pinned tool whose
    row is missing (`assert expected_status is not None`), so from any pinned tool
    the `None` arm below is UNREACHABLE by construction: it is kept executable only
    by `test_the_stderr_requirement_is_gated_on_the_census_pinning`, whose job is to
    pin the DECISION rather than the state of today's census. Production is
    row-required; the arm exists so the rule "a row is a contract, no row is not"
    is a decision with a test, not an inline conditional nobody can mutate safely.
    """
    return any(
        kw.arg == "file"
        and isinstance(kw.value, ast.Attribute)
        and kw.value.attr == "stderr"
        and isinstance(kw.value.value, ast.Name)
        and kw.value.value.id == "sys"
        for kw in call.keywords
    )


def _channel_is_wrong(print_call: ast.Call, expected_status: int | None) -> bool:
    """Whether the census would MISREAD this print-shape refusal (#5128).

    TRUE only for the combination the census cannot see: a `print(<message>)` to
    STDOUT in a tool whose `REFUSAL_CODES` row makes the census RUN the tool and
    read `probe.stderr`. FALSE for a stderr print (readable) and FALSE for a tool
    with no row (the census executes the guard node and joins both channels).

    Factored out and table-tested because inlined it was unpinnable: both pinned
    tools carry a row today, so the false branch is unreachable from them, and
    inverting the condition (`is not None` -> `is None` — one token, not dead code)
    while dropping the tool's `file=sys.stderr` left the whole suite GREEN (measured,
    190 passed). That is the exact defect the arm exists to catch, so the decision
    itself is executed by `test_the_stderr_requirement_is_gated_on_the_census_pinning`.
    """
    return expected_status is not None and not _is_stderr_print(print_call)


@pytest.mark.parametrize(
    "source,pinned,expected",
    [
        ('print("m", file=sys.stderr)', 4, False),  # readable by the census's stderr arm
        ('print("m", file=sys.stdout)', 4, True),  # stdout while the census reads stderr
        ('print("m")', 4, True),  # the same hole, defaulted
        ('print("m")', None, False),  # no row: the census joins BOTH channels
        ('print("m", file=sys.stderr)', None, False),
    ],
)
def test_the_stderr_requirement_is_gated_on_the_census_pinning(source, pinned, expected):
    """The channel DECISION on its own, because no pinned tool can reach both sides.

    Both tools carry a `REFUSAL_CODES` row (main's #11507 added
    `tools/build_site_icons.py: 4`), so a pin that only exercised them could not
    tell "the gate is `is not None`" from "the gate is always true": measured,
    inverting it to `is None` AND dropping the tool's `file=sys.stderr` left the
    suite at 190 passed. The `None` rows pin the other direction — a tool with no
    row must NOT be required to print to stderr, or this pin reds a census-LEGAL
    guard (the false-red class its docstring says it must not add).
    """
    call = ast.parse(source).body[0].value
    assert isinstance(call, ast.Call)
    assert _channel_is_wrong(call, pinned) is expected, (source, pinned, expected)


def _advertise_offenders(rel: str, text: str, census) -> list[str]:
    """The census's advertised-form predicate, applied to a file's TEXT (#5128).

    Factored OUT of the sweep so the predicate AND its wiring to a real subject can
    be executed (see `test_the_advertised_form_filter_is_not_deaf`, which drives
    this same function with synthetic text). Inline, a filter that had lost its
    condition (`if False and (…)` — the check deleted, the assertion kept) left the
    whole suite GREEN, because the two pinned tools genuinely advertise no bare
    form: "no offenders in this file" and "this filter flags nothing" are the same
    observation until something feeds it a line that SHOULD be flagged, and the
    only thing that does is a tool that already has an offender. Routing BOTH
    callers through one function means a mutation that empties it reds the
    synthetic-subject test, rather than leaving only the call site to notice.
    """
    return [
        f"{rel}:{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        for m in census._BARE_INVOCATION.finditer(line)
        if (m.group(1) is None or f"{m.group(1)}/{m.group(2)}" not in census._CARVE_OUTS)
        and (rel, line.strip()) not in census._DELIBERATE_BARE_FORM
    ]


def _run_guard(guard: ast.If, monkeypatch, version: tuple[int, ...]) -> str:
    """Execute the guard ALONE with `sys.version_info` faked, return what it says.

    Returns the refusal text appended to whichever channel carried it, or "" when
    the guard did not refuse (i.e. it was INERT — what must happen at the floor).

    This is BEHAVIOURAL on purpose. A structural check of the message can pin the
    floor, the file and an invocation and still accept `(got 9.3)` — the two
    subscripts in the wrong order render a value the census's own `"(got 3.9)"`
    assertion rejects, and no set-membership test on the AST can see the order or
    the separator. Executing the guard closes ordering, separators and formatting
    at once.
    """
    module = ast.Module(body=[guard], type_ignores=[])
    code = compile(module, f"<guard@{guard.lineno}>", "exec")
    monkeypatch.setattr(sys, "version_info", version)
    refused: SystemExit | None = None
    try:
        # The node is parsed from a TRACKED file; nothing here is input.
        exec(code, {"sys": sys})
    except SystemExit as exc:
        refused = exc
    return "" if refused is None else str(refused.code)


def _refusal_text(guard: ast.If, capsys, monkeypatch, version: tuple[int, ...]) -> str:
    """What the guard SAYS on a faked interpreter, across every channel."""
    said = _run_guard(guard, monkeypatch, version)
    captured = capsys.readouterr()
    return said + captured.out + captured.err


def _guard_if(tree: ast.Module) -> ast.If | None:
    """The module's #5128 interpreter guard, FOUND without pinning its position.

    Position is `test_the_tool_carries_the_5128_interpreter_guard_first`'s claim,
    not this helper's: a test that only needs to EXECUTE the guard must not
    re-assert (and so re-litigate) the preamble order in a second place.
    """
    for node in tree.body:
        if isinstance(node, ast.If) and "version_info" in ast.unparse(node.test):
            return node
    return None


PINNED_TOOLS = [
    pytest.param(TOOL, id="capstone_ledger"),
    pytest.param(REPO_ROOT / "tools" / "build_site_icons.py", id="build_site_icons"),
]


def _preamble_index(body: list[ast.stmt]) -> int | None:
    """Index of the `#5128` guard in a tool's module body, or None (#9664).

    The census's `_guard_index` walk, and nothing more: skip EXACTLY ONE module
    docstring, then ZERO OR MORE `__future__` imports, then exactly ONE **bare**
    `import sys`, then the `if sys.version_info < (3, 12)` guard.

    Requiring a `__future__` import would red census-LEGAL files — measured at this
    head over the census's OWN corpus (237 files: 178 under `tools/`, 59 under
    `graph-scripts/`): 19 of the 59 `graph-scripts/` files carry none as their first
    post-docstring statement, against 0 of the 178 `tools/` files. (Of the 185
    tracked `tools/*.py`, 3 are `__init__.py` — excluded by name — and 4 are
    `RUNTIME_39`, which is why the corpus is 178; of `graph-scripts/`'s 60 tracked,
    `profile_395_local_ep.py` is `UNGUARDABLE`, which is why it is 59.) Skipping
    every leading `ast.Expr` is wrong
    too: a second bare string literal
    before the guard passed a skip-all-Expr loop and reded the census.

    `asname is None` is the conjunct nothing else can see, and it is NOT redundant:
    `import sys as _sys` binds no `sys`, so the guard is a `NameError` at runtime
    while a name-only comparison passes — and the behavioural arms cannot observe
    it, because `_run_guard` EXECUTES the guard with `sys` injected into its
    namespace. Measured: deleting this conjunct and aliasing the import in
    `tools/build_site_icons.py` left the pin GREEN (`2 passed`) over a tool whose
    `--help` raises `NameError: name 'sys' is not defined`. A false PASS on the only
    gate a `tools/`-only diff gets, so the walk is a helper with its own table
    (`test_the_preamble_walk_rejects_what_the_census_rejects`) rather than an
    inline chain whose removal nothing notices.
    """
    i = 0
    if (
        i < len(body)
        and isinstance(body[i], ast.Expr)
        and isinstance(body[i].value, ast.Constant)
        and isinstance(body[i].value.value, str)
    ):
        i += 1
    while (
        i < len(body)
        and isinstance(body[i], ast.ImportFrom)
        and body[i].module == "__future__"
    ):
        i += 1
    # Bounds-checked before each access: a truncated preamble returns None, so the
    # caller diagnoses it rather than raising an IndexError the reader must decode.
    if i >= len(body) or not isinstance(body[i], ast.Import):
        return None
    if [a.name for a in body[i].names] != ["sys"] or body[i].names[0].asname is not None:
        return None
    i += 1
    if i >= len(body) or not isinstance(body[i], ast.If):
        return None
    return i


@pytest.mark.parametrize(
    "source,expected",
    [
        ("import sys\nif sys.version_info < (3, 12):\n    pass\n", 1),
        (
            '\"\"\"d\"\"\"\nfrom __future__ import annotations\nimport sys\n'
            "if sys.version_info < (3, 12):\n    pass\n",
            3,
        ),
        # ZERO OR MORE `__future__` imports is the walk's OWN claim, and no row
        # carried two, so `while` -> `if` stayed GREEN (measured, 9 passed). It
        # cannot false-PASS — one `if` REJECTS a legal two-`__future__` preamble,
        # i.e. an over-block — but an unpinned claim is the vacuous-arm class this
        # file keeps removing. The census's `_guard_index` returns 4 here as well
        # (asserted below), so this row is parity, not a stricter local rule.
        (
            '\"\"\"d\"\"\"\nfrom __future__ import annotations\n'
            'from __future__ import generator_stop\nimport sys\n'
            "if sys.version_info < (3, 12):\n    pass\n",
            4,
        ),
        # the load-bearing rejection: an ALIASED import binds no `sys`
        ("import sys as _sys\nif sys.version_info < (3, 12):\n    pass\n", None),
        # two names in one import
        ('\"\"\"d\"\"\"\nimport sys, os\nif sys.version_info < (3, 12):\n    pass\n', None),
        # a SECOND bare string literal is not a docstring the census skips
        ('\"\"\"d\"\"\"\n\"second\"\nimport sys\nif sys.version_info < (3, 12):\n    pass\n', None),
        # truncated preambles
        ("import sys\n", None),
        ("from __future__ import annotations\n", None),
    ],
)
def test_the_preamble_walk_rejects_what_the_census_rejects(source, expected):
    """The preamble WALK, executed on synthetic bodies.

    `asname is None` was the one conjunct no other arm could see: both pinned tools
    import `sys` bare, and the behavioural arms inject `sys` into the exec
    namespace, so an ALIASED import passes them all while the real tool raises
    `NameError` at runtime. Its deletion left the whole file GREEN (measured), which
    is the vacuous-arm class this file's own docstrings keep removing — so the walk
    is executed here in both directions, not described in a comment above an
    assertion that nothing can falsify.

    ⛔ AND against the CENSUS's own walk, row by row. This helper re-implements
    `tests/test_entry_point_python_guard.py::_guard_index` + `_is_bare_sys_import`,
    so hard-coded expectations alone would let the two drift apart silently: a
    census TIGHTENING would make this pin an over-block, a census LOOSENING would
    make it a future false PASS — both measured, in both directions, with this pin
    GREEN. The census is loaded by path (`_census_module`) and asserted to agree,
    which is the file's own doctrine for the regex and the refusal table.
    """
    body = list(ast.parse(source).body)
    assert _preamble_index(body) == expected, source
    assert _census_module()._guard_index(body) == expected, (
        f"this pin's walk and the census's `_guard_index` disagree: pin="
        f"{_preamble_index(body)!r} census="
        f"{_census_module()._guard_index(body)!r} for {source!r}"
    )


@pytest.mark.parametrize("tool", PINNED_TOOLS)
def test_the_tool_carries_the_5128_interpreter_guard_first(tool, capsys, monkeypatch):
    """The guard must precede EVERY other import, and refuse attributably.

    Measured on CI: `tools/capstone_ledger.py` was added without the guard, and
    shard `a` reded on `tests/test_entry_point_python_guard.py` — while every
    local suite run was green.

    That census cannot be the only pin, and the reason is a measured ASYMMETRY
    in `tools/ci_selection.py`: for a PR that touches only one tool,
    `select(['tools/capstone_ledger.py'], 'pull_request', load_manifest())["test_files"]`
    returns 82 file names (basenames, no `tests/` prefix) that INCLUDE
    `test_9664_capstone_verdict_ledger.py` (it is
    `tier1`-registered in `config/ci-surfaces.yml` — the
    `- test_9664_capstone_verdict_ledger.py` row whose comment begins `# #9664:`,
    dual-registered with `core`; located by that quote, never by a line number,
    because every merge that inserts a row above it moves the number) and EXCLUDE
    `tests/test_entry_point_python_guard.py`. So pinning here is what makes the
    guard checked on a tool-only diff; pinning in the census alone is not. Both
    tools this PR touches are parameterised for that reason — an unpinned second
    file is the same gap one tool over.

    DECLARED BOUND — what this pin mirrors, and what is UNGRADED instead.

    It MIRRORS these census arms over the two tools' OWN files: preamble order,
    floor, guard shape, both refusal shapes, the RENDERED message (read from the
    text the operator sees, never from the AST's string constants), the derived
    process status, the census `REFUSAL_CODES` row that status must match, and
    the invocation the refusal names RESOLVING — executed PER TOOL, on the
    census's own criterion: a form that ADDS a token is the `--help` class and
    must exit 0, while a form that adds NONE is the tool's own entry point and is
    graded on not crashing rather than on its verdict code, since
    `capstone_ledger.py`'s 2 is its UNCOVERED verdict — and the
    advertised-invocation sweep driven by the census's own `_BARE_INVOCATION` +
    exemption sets, never a copy, CALL SITE included (`_WIRED_into_the_pin`
    drives the pin with the sweep disconnected, so deleting the call cannot pass).

    ⛔ EVERYTHING ELSE in `tests/test_entry_point_python_guard.py` is NOT mirrored —
    and this paragraph deliberately does NOT enumerate that set, because a list is
    what went wrong here: two enumerations of "the arms I do not mirror" each came
    back missing arms that exist, and an unlisted arm reads as a covered one. The
    rule is the claim: if the assertion is not named in the MIRRORS list above, this
    file does not grade it.

    ⚠️ AND ON THE DIFFS THAT REACH THIS FILE WITHOUT THE CENSUS, those unmirrored
    arms are UNGRADED, not "closed by selection". That is the whole gap, stated
    once: this file is `tier1`-registered, so it is selected by diff shapes that do
    not select the census — measured via `select(…, 'pull_request', load_manifest())`
    with `census = 'test_entry_point_python_guard.py' in test_files`:

        tools/capstone_ledger.py     82 files, census OUT   <- the tools/ surface
        tools/build_site_icons.py    82 files, census OUT
        docs/plans/x.md              82 files, census OUT   <- the docs/ surface
        tests/test_x.py             788 files, census IN
        graph-scripts/audit_graph.py 788 files, census IN

    ⛔ `tools/edge_census.py` and `tools/branch_reaper.py` are deliberately NOT
    rows here, and this paragraph used to carry them as "3 files, census OUT …
    nothing grades it" — FALSE, and false in the direction that inflated the gap
    (#11491 review, ISSUE 2): both are `TOOL_CARVEOUTS` entries in
    `tools/ci_selection.py` (`#4408`, `#4503`) — not `SHARED_MODULES`, which holds
    no `tools/` path — so a diff touching either fails closed to the FULL matrix
    and DOES run the census. They are named, not tabulated: a re-measured number
    per tool is this block's own failure mode, and `ci_selection.py`'s own tables
    are the single owner of which paths fail closed.

    Find them by QUOTE, not by line number (which rots on every edit, and did:
    this paragraph named the wrong table for three rounds): the anchor for the
    reaper is the comment block opening `# #4408: the local-branch reaper`,
    whose last line is `-> FULL matrix (fail closed) — the safe default for a
    destructive-ref tool.`; the census's is the block opening
    `# #4503: the edge census/probe`, ending `-> FULL\n    # matrix (fail closed)`
    and naming its own pin, `test_edge_census_tool_change_fails_closed_to_full`.
    ⛔ The CLAIM ITSELF is executed, not asserted —
    `test_the_two_named_carve_outs_REALLY_do_run_the_census` below drives
    `select()` on both paths and fails if either stops failing closed. Without it
    a lane could "fix" this paragraph by deleting the carve-out rows that hold
    the guarantee and every other arm here would stay green (measured: deleting
    `"tools/branch_reaper.py",` from `TOOL_CARVEOUTS` left this file at
    207 passed until that test existed).

    So on a `tools/`-only or `docs/`-only diff, every unmirrored arm above is
    ungraded and this pin is the only gate. Measured, both ways: appending a line
    advertising `python3 tools/capstone_ledger.py check` to a tracked DOC leaves
    this file GREEN while the census reds
    `test_no_doc_or_usage_advertises_the_form_the_guard_refuses`; and the same line
    appended to an UNPINNED sibling tool (`tools/ask_pool_admission_probe.py`, 82
    files, census OUT — re-measured when the two "3 files" rows above were
    corrected) likewise leaves it GREEN (206 passed) while the census reds
    (1 failed, 536 passed, 2 skipped). That is a routing gap in
    `tools/ci_selection.py`, not something a pin in this file can close; it is
    tracked as #11363 (same root: a diff that never selects the census leaves its
    #5128 guard unchecked).

    What IS closed by selection is only this: a `tests/` or `graph-scripts/` edit
    selects 788 files INCLUDING the census, so those diffs are graded by the census
    itself rather than by this pin. ⛔ `tools/` and `docs/` are NOT in that set —
    which is the distinction this paragraph exists to keep visible, and the reason
    it says "ungraded" instead of "not a hole".

    """
    tree = ast.parse(tool.read_text(encoding="utf-8"))
    body = list(tree.body)
    i = _preamble_index(body)
    assert i is not None, (
        f"the #5128 preamble must be one module docstring, ZERO OR MORE "
        f"`__future__` imports, then a BARE `import sys`, then the interpreter "
        f"guard (the census's `_guard_index` walk); got "
        f"{[type(n).__name__ for n in body[:4]]} — `_preamble_index`'s table "
        f"(`test_the_preamble_walk_rejects_what_the_census_rejects`) names each "
        f"rejection"
    )
    guard = body[i]
    test = guard.test
    assert isinstance(test, ast.Compare) and isinstance(test.left, ast.Attribute), ast.dump(test)
    assert isinstance(test.left.value, ast.Name), (
        f"the guard must test `sys.version_info`; got {ast.dump(test.left)}"
    )
    assert test.left.attr == "version_info" and test.left.value.id == "sys"
    assert len(test.ops) == 1 and isinstance(test.ops[0], ast.Lt), ast.dump(test)
    assert isinstance(test.comparators[0], ast.Tuple), ast.dump(test.comparators[0])
    # `len(elts) == 2` BEFORE indexing: a malformed floor (`< (3,)`, `< (3, 12, 0)`)
    # must fail with this diagnosis, not raise IndexError. Mirrors the census's
    # `_floor_in_test`, which returns None rather than indexing unguarded.
    elts = test.comparators[0].elts
    assert len(elts) == 2, (
        f"the floor must be a 2-tuple `(major, minor)`; got {ast.dump(test.comparators[0])}"
    )
    # `isinstance(..., int)` and the bool exclusion matter: `[3.0, 12.0] == [3, 12]`
    # is True in Python, so a value-only comparison accepts a FLOAT floor the
    # census's own `_floor_in_test` rejects (`isinstance(element.value, int)`) — a
    # green pin over a census-red guard, the one thing this arm must never do.
    assert [
        e.value
        for e in elts
        if isinstance(e, ast.Constant)
        and isinstance(e.value, int)
        and not isinstance(e.value, bool)
    ] == [3, 12], f"the floor must be (3, 12); got {ast.dump(test.comparators[0])}"

    # The guard's BODY must be one of the two shapes the census accepts — that
    # rule is `_system_exit_message`'s, not a search for "any Raise somewhere":
    # a three-statement body (a stray `print`, a binding, then the raise) carries
    # a correct message and still reds the census, so it must red here too.
    body_nodes = guard.body
    print_shape = (
        len(body_nodes) == 2
        and isinstance(body_nodes[0], ast.Expr)
        and isinstance(body_nodes[0].value, ast.Call)
        and isinstance(body_nodes[0].value.func, ast.Name)
        and body_nodes[0].value.func.id == "print"
        and body_nodes[0].value.args
        and isinstance(body_nodes[1], ast.Raise)
    )
    shapes_ok = (len(body_nodes) == 1 and isinstance(body_nodes[0], ast.Raise)) or print_shape
    assert shapes_ok, (
        f"the guard must be exactly one of the two #5128 shapes — `raise "
        f"SystemExit(<message>)`, or `print(<message>)` + `raise "
        f"SystemExit(<int>)`; got {[type(n).__name__ for n in body_nodes]} "
        f"(tests/test_entry_point_python_guard.py reds on any other shape)"
    )

    raises = [n for n in body_nodes if isinstance(n, ast.Raise)]
    # ★ An `else:` clause runs at the floor — on the interpreter this guard exists
    # to PASS — so it can change what a 3.12 run does. Two classes:
    #   * `else: raise SystemExit(0)` — a SILENT NO-OP: the tool does nothing, says
    #     nothing, and exits 0. Measured: a green pin over a tool that ran `exit 0`
    #     with no output. The floor-inertness assert below also catches this one.
    #   * `else: print('x')` / any NON-RAISING else — no raise to observe, so
    #     inertness is blind to it and this is the assert that reds it (measured:
    #     the assert disabled + that else = the whole file GREEN; assert intact = RED).
    # The census reds both via its floor probe; this pin is the only gate on a
    # `tools/`-only diff, so it must red them too.
    assert not guard.orelse, (
        f"the guard must carry NO `else` clause: it would run at the floor, on the "
        f"interpreter the guard exists to PASS — measured, `else: raise "
        f"SystemExit(0)` makes the tool a silent no-op with a green pin: "
        f"{[type(n).__name__ for n in guard.orelse]}"
    )
    # NO `_run_guard(..., (3, 12, 0, "final", 0)) == ""` assertion here: the test
    # node above is pinned to exactly `sys.version_info < (3, 12)`, and
    # `(3, 12, 0, "final", 0) < (3, 12)` is False on every run, so the body
    # provably cannot execute and the probe's `""` is entailed — the assertion can
    # never fire. Inertness at the floor stays entailed by the node, floor and
    # `orelse` pins above; the 3.9/3.10 probes below assert the refusal TEXT, not
    # inertness at 3.12, so they are not what grades it. No coverage is lost.
    # Same class as the `len(raises) == 1` arm removed below.
    # NO separate `len(raises) == 1` assertion here: `shapes_ok` above admits
    # exactly two bodies, each carrying exactly one `Raise`, so such an assertion
    # is entailed and can never fire. Measured — it is the vacuous-arm class this
    # pin's own `_refusal_exit_status` docstring removed rather than mirrored.
    raise_node = raises[0]
    exc = raise_node.exc
    assert isinstance(exc, ast.Call), (
        f"the guard must `raise SystemExit(...)`; got {ast.dump(raise_node)}"
    )
    assert isinstance(exc.func, ast.Name) and exc.func.id == "SystemExit", (
        f"the guard must raise `SystemExit`; any other exception reads to the "
        f"operator as the tool being broken: {ast.dump(raise_node)}"
    )
    assert len(exc.args) == 1 and not exc.keywords, (
        f"`raise SystemExit(...)` must carry exactly one argument; the census "
        f"rejects `SystemExit(<msg>, 1)` as an unattributable refusal: "
        f"{ast.dump(raise_node)}"
    )

    # ★ #11342: the refusal must not report SUCCESS. A guard that prints the
    # full, attributable message and then `raise SystemExit(0)` passes every
    # message assertion above while telling every caller the tool ran clean.
    status = _refusal_exit_status(exc)
    assert status is not None, (
        f"cannot statically resolve the refusal's exit code, so its status is "
        f"UNPROVEN — use an int literal or an f-string message (arithmetic over "
        f"literals and bare names are NOT resolved): got {ast.dump(raise_node)}"
    )
    # The expected status is the CENSUS's own pin, not a constant read back from
    # the module under test: measured, a coordinated edit (`EXIT_INVALID = 3` +
    # `SystemExit(3)`) kept a self-referential assertion green while the census
    # reded, and 3 is `EXIT_DISABLED` — a refusal indistinguishable from the
    # off-switch.
    #
    # A MISSING row is a REFUSAL here, not a licence. It used to fall back to
    # `status != 0` ("the census accepts any non-zero status"), and that fallback
    # was a fail-open: measured at the head that added this, dropping
    # `build_site_icons.py`'s `REFUSAL_CODES` + `REFUSAL_WHY` rows AND downgrading
    # its refusal from `SystemExit(4)` to `SystemExit(1)` left this whole file
    # GREEN (195 passed) while the regression the tool's own comment forbids went
    # in: "an environment refusal must not wear 1 or a caller cannot tell 'the
    # tool refused' from 'the interpreter is too old'". Silently widening to any
    # non-zero is the pin accepting whatever a coordinated edit leaves behind, so
    # every PINNED tool must be in the census's table and the demand is stated
    # rather than inferred.
    expected_status = _census_module().REFUSAL_CODES.get(f"tools/{tool.name}")
    # (#11491 review, ISSUE 1) The block above used to be followed by a
    # `status != 0` PARITY fallback for a missing row, under a second comment that
    # said so — while the block above it, and this test's own docstring, claimed
    # the caller REFUSED a rowless pinned tool. It did not: measured, dropping
    # `build_site_icons.py`'s `REFUSAL_CODES` + `REFUSAL_WHY` rows and downgrading
    # its guard from `SystemExit(4)` to `SystemExit(1)` left this whole file GREEN
    # while `_channel_is_wrong`'s `expected_status is not None` short-circuit also
    # stopped checking the CHANNEL — two gates lost to one edit, on the only
    # surface a `tools/`-only diff gets. So the demand is now real, not prose.
    #
    # This IS a stricter local contract than the census (which accepts any
    # non-zero for an unpinned tool), and deliberately so: the census's row is what
    # this pin COMPARES AGAINST, so without one there is nothing to mirror and the
    # arm silently degrades to "the tool failed somehow". A new tool is not
    # false-redded by this — it is told to get its row, which is the census's own
    # registration step, not a demand this file invents. `_channel_is_wrong`'s
    # `None` branch is therefore unreachable FROM A PINNED TOOL by construction,
    # exactly as its docstring says; it is kept executable by its own table test.
    assert expected_status is not None, (
        f"the census pins no `REFUSAL_CODES` row for `tools/{tool.name}`, so this "
        f"pin has no status to compare against and would widen to 'any non-zero'"
        f" — the fail-open measured at the head that added this clause (delete the "
        f"row, downgrade the guard to `SystemExit(1)`, and both the status and the "
        f"CHANNEL checks go green). Add `tools/{tool.name}` to `REFUSAL_CODES` and "
        f"`REFUSAL_WHY` in `tests/test_entry_point_python_guard.py`, or stop "
        f"pinning the tool here."
    )
    assert status == expected_status, (
        f"the refusal must exit the status the census pins for this tool "
        f"({expected_status!r}), and a coordinated edit of the module's own "
        f"constant is exactly what this compares against. Got {status}: "
        f"{ast.dump(raise_node)}"
    )

    if print_shape:
        # The channel is only a CONTRACT where the census pins one. A REFUSAL_CODES
        # tool is refused by RUNNING it and reading `probe.stderr`, so a stdout
        # print is a refusal the census never sees; a tool with no row is graded by
        # executing the guard node, where the census joins stdout AND stderr, so
        # demanding stderr there would red a census-LEGAL guard — the false-red
        # class this pin must not add. The decision is a helper, not an inline
        # conditional: inlined, its false branch was unreachable from both pinned
        # tools and invertible with the suite green (see `_channel_is_wrong`).
        assert not _channel_is_wrong(body_nodes[0].value, expected_status), (
            f"the refusal must be printed to `sys.stderr`: the census RUNS this "
            f"tool and reads `probe.stderr` (its REFUSAL_CODES arm), so a "
            f"`print(<message>)` to stdout is a refusal the operator and the "
            f"census both miss: {ast.dump(body_nodes[0].value)}"
        )
        message = body_nodes[0].value.args[0]
    else:
        message = exc.args[0]

    # The message is graded BEHAVIOURALLY — from the text the operator actually sees
    # on a faked 3.9 host — and NOT from the string constants that happen to appear
    # in the AST. Measured (review round 16): joining every `ast.Constant` string in
    # the message left this file at 206 passed GREEN when the subject was moved into
    # a never-taken branch (`f"{'… requires Python >= 3.12' if False else ''}"`), so a
    # refusal that no longer names its file in the operator's output satisfied the
    # "RENDERED message" claim, while the census reded on the identical mutation
    # (`2 failed`) because it adjudicates `rel in str(refusal.value) + out + err`.
    said = _refusal_text(guard, capsys, monkeypatch, (3, 9, 6, "final", 0))
    # The message must name the floor, the file, and an invocation at all. ⛔ This
    # clause is a PREFIX test only — `uv run python tools/<name>` is a prefix of
    # BOTH forms — so it cannot distinguish the form that resolves from one that
    # does not. The polarity differs per tool and is stated rather than generalised:
    # `tools/build_site_icons.py`'s bare path is argparse's usage error (exit 2) and
    # its `--help` form resolves (exit 0); `tools/capstone_ledger.py`'s NAMED form
    # IS the bare path, which resolves to a computed verdict (exit 2 =
    # `EXIT_UNCOVERED`), while its `--help` exits 5 with no verdict. The WORKING
    # claim is proved by executing it PER TOOL, in
    # `test_the_refusal_names_a_form_that_actually_RESOLVES`. Measured: reverting
    # the site-icon message to the bare path left this file GREEN while the census
    # reded, and `tools/capstone_ledger.py`'s advice naming `--nope` left it GREEN
    # too — which is why the proof lives in its own parametrized arm, not here.
    #
    # The subject is asserted as ONE clause, not as a bare `"<tool name>" in
    # rendered` substring: the invocation clause below already contains that
    # basename, so a substring test would pass on a refusal that named a
    # DIFFERENT tool in its subject line.
    assert f"tools/{tool.name} requires Python >= 3.12" in said, (
        f"the refusal must name the file and the floor in its subject AS RENDERED; "
        f"it said {said!r} (message node: {ast.dump(message)})"
    )
    assert f"uv run python tools/{tool.name}" in said, (
        f"the refusal must name an invocation at all AS RENDERED (PREFIX only — "
        f"whether it RESOLVES is proved per tool in "
        f"test_the_refusal_names_a_form_that_actually_RESOLVES); it said {said!r} "
        f"(message node: {ast.dump(message)})"
    )

    # The census also requires the refusal to name the interpreter it GOT — and
    # this is asserted BEHAVIOURALLY, because a structural check cannot see the
    # ORDER: `(got {version_info[1]}.{version_info[0]})` renders `(got 9.3)` and
    # reds the census while satisfying any set-of-subscripts test.
    assert "(got 3.9)" in said, (
        f"the refusal must name the interpreter it GOT as the operator sees it "
        f"(`(got 3.9)` on a 3.9 host); it said {said!r}"
    )
    # A SECOND interpreter, because one faked version cannot tell an interpolated
    # message from a hard-coded string: `(got 3.9)` written as a literal passes the
    # 3.9 run. Measured: the census has the same single-version blind spot, so on a
    # tool-only diff this is the only place the difference can be caught.
    said_310 = _refusal_text(guard, capsys, monkeypatch, (3, 10, 9, "final", 0))
    assert "(got 3.10)" in said_310, (
        f"the refusal must INTERPOLATE the interpreter it got — a hard-coded "
        f"`(got 3.9)` passes a single-version probe: on 3.10 it said {said_310!r}"
    )

    # The census's `test_no_doc_or_usage_advertises_the_form_the_guard_refuses`
    # sweeps THIS tool's docstring too, and it is the same gate-class as the pin
    # above: it greps `python3 <path>` for a tool that IS guarded, because the
    # reader copies the advertised form and meets a refusal the doc never
    # predicted. Measured on a tool-only diff —
    # `select(['tools/capstone_ledger.py'])['test_files']` returns 82 names,
    # INCLUDING this test's file and EXCLUDING
    # `tests/test_entry_point_python_guard.py` — so
    # this pin is the only gate there and must not be narrower than the sweep.
    # (Not hypothetical: `tools/build_site_icons.py` merged advertising
    # `python3 tools/build_site_icons.py`, and the sweep is what caught it.)
    #
    # Driven by the CENSUS'S OWN PATTERN over the tool's WHOLE TEXT, not a
    # substring over `ast.get_docstring`: measured, a `# (an older revision was
    # invoked as python3 tools/<name> check)` COMMENT and the `python3 ./tools/...`
    # spelling both left a docstring-substring pin GREEN while the census reded.
    # A copied regex would drift the same way, so the predicate — not a copy of
    # it — is the single owner.
    census = _census_module()
    rel = tool.relative_to(REPO_ROOT).as_posix()
    text = tool.read_text(encoding="utf-8")
    # EVERY match on EVERY line, with the census's own exemptions — `finditer`,
    # not `search`. One match per line is a hole the census does not have: a line
    # whose FIRST match is a carve-out hides a real offender later on it. Measured
    # (review round 7):
    #   `# historical: python3 tools/tmpdir_sweep.py --help; python3 tools/capstone_ledger.py check`
    # left this pin GREEN (2 passed) while the census reddened. The exemptions are
    # the census's data, not a re-derived filter: filtering on
    # `group(1) == "tools" and group(2) == tool.name` also re-narrows the regex's
    # ELIDED branch (`python3 ...`, groups None) and its cross-path matches
    # (`python3 graph-scripts/...`), both of which the census reds too.
    offenders = _advertise_offenders(rel, text, census)
    assert not offenders, (
        f"the tool advertises `python3 tools/{tool.name}`, the form its guard "
        f"REFUSES; the census's "
        f"`test_no_doc_or_usage_advertises_the_form_the_guard_refuses` reds on "
        f"it, and that test does not run on a tool-only diff: {offenders}"
    )


def _ci_selection_module():
    """`tools/ci_selection.py`, loaded by path — the owner of the routing claim.

    Loaded the same way `_census_module()` loads the census, so the test below
    reads the tables that DECIDE routing instead of copying them.
    """
    path = REPO_ROOT / "tools" / "ci_selection.py"
    spec = importlib.util.spec_from_file_location("_ci_selection_for_9664", path)
    assert spec is not None and spec.loader is not None, path
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_two_named_carve_outs_REALLY_do_run_the_census():
    """The DECLARED BOUND's routing paragraph, EXECUTED in both directions.

    That paragraph exists so a reader can find out which paths still reach the
    census on a `tools/`-only diff, and it has been wrong once already: it named
    `SHARED_MODULES` where `TOOL_CARVEOUTS` holds the guarantee (round 15, P2).
    Prose about routing cannot grade itself — a re-measured table in a docstring
    is this block's own failure mode — so the claim is driven through `select()`,
    the single owner, and BOTH directions are pinned:

      * the two named carve-outs must fail closed to the FULL matrix, i.e. the
        census DOES run on a diff touching either (the paragraph's claim);
      * the two tools this pin covers must NOT fail closed, and the census must
        stay OUT of their selection while THIS file stays IN — otherwise the gap
        the whole pin exists to fill would not exist and the pin would be
        redundant rather than load-bearing.

    Non-vacuity, measured: deleting `"tools/branch_reaper.py",` from
    `TOOL_CARVEOUTS` reds this test (the path falls back under the flat `tools/`
    prefix, so the selection is no longer full), and so does deleting
    `"tools/edge_census.py",`. That is the concrete reason this is not prose: the
    earlier review warned a lane could "fix" the paragraph by deleting the rows
    that hold the guarantee, and before this arm existed that edit was green.
    """
    ci = _ci_selection_module()
    manifest = ci.load_manifest()

    for rel in ("tools/branch_reaper.py", "tools/edge_census.py"):
        explain: dict = {}
        selection = ci.select([rel], "pull_request", manifest, explain)
        assert explain.get("reasons") == ["unknown-paths"], (
            f"{rel} no longer routes through the unknown-path fail-closed branch; "
            f"the DECLARED BOUND paragraph says it does. explain={explain!r}"
        )
        assert selection["full"] is True, (
            f"a diff touching {rel} no longer selects the FULL matrix, so the "
            f"census no longer runs on the one diff that changes it — the "
            f"paragraph's operative claim is now FALSE: {selection['surfaces']!r}"
        )
        assert selection["test_files"] == "ALL", (
            f"{rel} is not routed to ALL tests; got {selection['test_files']!r}"
        )

    # The opposite direction: the pinned tools are NOT fail-closed, which is what
    # makes this pin the only gate on a tool-only diff.
    for pinned in PINNED_TOOLS:
        # `PINNED_TOOLS` holds `pytest.param` objects (they carry the ids the
        # parametrised arm above shows), so unwrap rather than restate the paths.
        tool = pinned.values[0] if hasattr(pinned, "values") else pinned
        rel = tool.relative_to(REPO_ROOT).as_posix()
        selection = ci.select([rel], "pull_request", manifest)
        assert selection["full"] is False, (
            f"{rel} now fails closed to the FULL matrix, so the census would run "
            f"on its diff and this pin would stop being the only gate — the "
            f"paragraph's premise (82 files, census OUT) is FALSE: "
            f"{selection['surfaces']!r}"
        )
        assert "test_9664_capstone_verdict_ledger.py" in selection["test_files"], (
            f"this pin is not selected for a {rel} diff; the paragraph says 82 "
            f"files INCLUDING this one"
        )
        assert "test_entry_point_python_guard.py" not in selection["test_files"], (
            f"the census IS selected for a {rel} diff, which contradicts the "
            f"paragraph's routing argument (the pin would then be redundant)"
        )


class _CensusMissingRow:
    """The REAL census, with one `REFUSAL_CODES` row deleted. Nothing else faked.

    Delegating rather than a `SimpleNamespace(REFUSAL_CODES=...)` on purpose: the
    arms AFTER the row check (`_advertise_offenders`, `_channel_is_wrong`) read
    `_BARE_INVOCATION` and friends from this same object, so a stub missing them
    would red the caller for the WRONG reason and the test below would pass for a
    reason it does not have.
    """

    def __init__(self, census, drop: str) -> None:
        self._census = census
        self.REFUSAL_CODES = {
            k: v for k, v in census.REFUSAL_CODES.items() if k != drop
        }

    def __getattr__(self, name):  # every other census attribute stays LIVE
        return getattr(self._census, name)


def test_a_pinned_tool_with_NO_census_row_is_a_REFUSAL_not_a_licence(capsys, monkeypatch):
    """#11491 review ISSUE 1 (P2): the row demand is EXECUTED, not just documented.

    The measured fail-open this closes: delete `tools/build_site_icons.py`'s
    `REFUSAL_CODES` + `REFUSAL_WHY` rows and downgrade its guard from
    `SystemExit(4)` to `SystemExit(1)`. With the old `status != 0` fallback this
    whole file stayed GREEN (the census's own parametrization just loses the row,
    and on a `tools/`-only diff it is not selected), so a tool whose refusal wears
    1 — indistinguishable from "the interpreter is too old" — shipped with the pin
    that exists to catch exactly that.

    NON-VACUITY: on the pre-fix code this test FAILS — `pytest.raises` sees the
    caller complete instead of refusing, because `expected_status is None` took the
    parity fallback. Re-measured by reverting the `assert expected_status is not
    None` clause: `1 failed` (the `DID NOT RAISE` path), and green again with it.
    """
    module = importlib.import_module(
        test_the_tool_carries_the_5128_interpreter_guard_first.__module__
    )
    census = _census_module()
    assert "tools/build_site_icons.py" in census.REFUSAL_CODES, (
        "this arm's premise is main's #11507 row; if it was dropped, the row "
        "deletion is itself the regression and every pinned tool is ungradeable"
    )
    monkeypatch.setattr(
        module,
        "_census_module",
        lambda: _CensusMissingRow(census, "tools/build_site_icons.py"),
    )
    with pytest.raises(AssertionError, match="pins no `REFUSAL_CODES` row"):
        test_the_tool_carries_the_5128_interpreter_guard_first(
            REPO_ROOT / "tools" / "build_site_icons.py", capsys, monkeypatch
        )


@pytest.mark.parametrize("tool", PINNED_TOOLS)
def test_the_refusal_names_a_form_that_actually_RESOLVES(tool, capsys, monkeypatch):
    """The census's #11507 arm, mirrored PER TOOL — because its copy does not run here.

    `tests/test_entry_point_python_guard.py::
    test_the_site_icon_guard_names_an_invocation_that_actually_resolves` proves the
    invocation the refusal NAMES works, and it is the only gate that does one class
    of claim: the prefix `uv run python tools/<name>` is satisfied by the bare path
    AND by a form that adds the token the tool actually needs, so a prefix test
    cannot see the regression. A `tools/<name>`-only diff selects 82 test files
    INCLUDING this one and EXCLUDING the census, so on the diff that can introduce
    the regression this file is the whole gate.

    ⛔ PARAMETRIZED, not icons-only. An earlier cut of this arm hard-wired
    `build_site_icons.py` while the case docstring claimed it covered "the
    invocation the refusal names" for the pinned tools — so `capstone_ledger.py`'s
    advice stayed pinned by the PREFIX alone, and rewording it to
    `… --nope` (argparse: exit 5, a SECOND unattributed error, the exact class
    #5128 exists for) left this file at 203 passed. Measured.

    The claim proved is the census's, generalised: the form the refusal names must
    not be a USAGE error. That is what makes the advice resolvable on both tools —
    `build_site_icons.py` names `--help` (its bare path is a usage error because
    `--source` is required), and `capstone_ledger.py` names the bare entry point,
    which runs its check. So the named form is executed, and its stderr is read for
    argparse's own `usage:` / `error:` pair rather than for one tool's exit code.
    """
    path = tool
    guard = _guard_if(ast.parse(path.read_text(encoding="utf-8")))
    assert guard is not None, f"{path} carries no #5128 interpreter guard"
    said = _refusal_text(guard, capsys, monkeypatch, (3, 9, 6, "final", 0))
    named_prefix = f"uv run python tools/{path.name}"
    assert named_prefix in said, (
        f"the refusal must name `{named_prefix}` in its advice: {said!r}"
    )
    # The tokens the refusal ADDS after the entry point are part of the advice —
    # `--help` for the tool whose bare path is a usage error, nothing for the tool
    # whose entry point is its own command.
    tail = said.split(named_prefix, 1)[1].split("`", 1)[0]
    extra = [tok for tok in tail.split() if tok.startswith("-")]
    named = subprocess.run(
        [sys.executable, str(path), *extra], capture_output=True, text=True
    )
    # argparse writes BOTH lines for a usage error; a working form writes neither.
    # ⛔ "Not a usage error" is NOT "resolves" — the census's #11507 arm asserts
    # `resolved.returncode == 0` for the named form, and any criterion weaker than
    # that is a shadow. Measured (review round 16): rewording the icons refusal to
    # name `--source=/nope.png` left THIS arm at 2 passed GREEN — that run fails
    # with `/nope.png: not a file` (rc=1), which writes neither argparse line —
    # while the census reded on the identical mutation. The polarity below is the
    # census's, split per tool because only one tool adds a token.
    stderr_lower = named.stderr.lower()
    assert "usage:" not in stderr_lower and "error:" not in stderr_lower, (
        f"the invocation the refusal names is a USAGE error — advice that is a "
        f"SECOND unattributed error, the class #5128 exists for: "
        f"{named_prefix} {' '.join(extra)} -> rc={named.returncode}, "
        f"{named.stderr[-400:]!r}"
    )
    if extra:
        # The census's own criterion, for the form the census pins: an invocation
        # that ADDS tokens is the `--help` class, whose only right answer is 0.
        assert named.returncode == 0, (
            f"the invocation the refusal names must RESOLVE — the census asserts "
            f"`resolved.returncode == 0` for exactly this form, and this pin must "
            f"not be narrower than the census on the one diff shape where the "
            f"census does not run: {named_prefix} {' '.join(extra)} -> "
            f"rc={named.returncode}, {named.stderr[-400:]!r}"
        )
        # Why the extra token is there at all: the bare path must be the usage
        # error the named form avoids. If it ever stops being one, the advice is
        # no longer necessary and this arm should be revisited rather than kept.
        bare = subprocess.run(
            [sys.executable, str(path)], capture_output=True, text=True
        )
        assert "usage:" in bare.stderr.lower() and "error:" in bare.stderr.lower(), (
            f"`{named_prefix}` names {' '.join(extra)}, so the BARE path must be a "
            f"usage error that form avoids; it was not: rc={bare.returncode}, "
            f"{bare.stderr[-300:]!r}"
        )
    else:
        # The named form adds NOTHING, so it IS the tool's own entry point, and its
        # exit code is a VERDICT (`capstone_ledger.py` exits 2 = UNCOVERED) —
        # grading that as a failure is the over-block this arm must not add. What is
        # still graded is that the advice does not CRASH: a traceback is the same
        # unattributed error as a usage error.
        assert "Traceback (most recent call last)" not in named.stderr, (
            f"the refusal names `{named_prefix}` with no extra token, so that must "
            f"be the tool's working entry point; it raised instead of producing a "
            f"verdict: rc={named.returncode}, {named.stderr[-400:]!r}"
        )


def test_the_advertised_form_filter_is_not_deaf():
    """The sweep's FILTER, executed on synthetic lines — not only "this file is clean".

    Measured before this test existed: replacing the sweep comprehension's condition
    with `if False and (…)` — the check deleted, the assertion kept — left the suite
    at 724 passed, exactly as unmutated. The arm was live only for a file that
    already had an offender, which is the one state it exists to detect, so nothing
    executed its correctness. Each assertion below pins one side of the predicate:
    it must flag an offender, must NOT flag a compliant invocation, must honour the
    census's own carve-outs, and must flag the ELIDED form (whose regex branch has
    no path, so no carve-out can exempt it).
    """
    census = _census_module()
    rel = "tools/capstone_ledger.py"
    assert _advertise_offenders(rel, f"run `python3 {rel} check`", census), (
        "the filter flags NO offender — the sweep is deaf, and a tool advertising the "
        "refused form would pass it"
    )
    assert not _advertise_offenders(rel, f"run `uv run python {rel} check`", census), (
        "the filter flags a COMPLIANT invocation — every pinned tool would red"
    )
    carve = sorted(census._CARVE_OUTS)[0]
    assert not _advertise_offenders(rel, f"`python3 {carve}`", census), (
        f"a census carve-out ({carve}) must stay exempt — the exemptions are the "
        f"census's data, not a re-derived filter"
    )
    assert _advertise_offenders(rel, "`python3 ...`", census), (
        "the ELIDED form has no path, so no carve-out can exempt it; the census reds "
        "it and the filter must too"
    )
    # MULTIPLICITY — the round-7 hole, and the only fail-OPEN arm in this table.
    # Measured at this head: reverting the sweep's `finditer` to `search` left the
    # whole file GREEN (204 passed), so the comment above the sweep asserting
    # "`finditer`, not `search`" pinned nothing. One match per line lets a line
    # whose FIRST match is a carve-out hide a real offender later on it — a hole
    # the census does not have. The carve-out is asserted to BE one, so this row
    # cannot rot into a line carrying no exemption.
    hiding_carve = "tools/tmpdir_sweep.py"
    assert hiding_carve in census._CARVE_OUTS, (
        f"{hiding_carve} is no longer a census carve-out, so this row would test "
        f"nothing — pick a current exemption from `_CARVE_OUTS`"
    )
    hiding = f"# historical: python3 {hiding_carve} --help; python3 {rel} check"
    assert _advertise_offenders(rel, hiding, census), (
        "a carve-out match FIRST on the line hid a real offender later on it: the "
        "sweep must use `finditer`, not `search` (review round 7). The census reds "
        f"this line; the filter found nothing in {hiding!r}"
    )
    # The RECORD exemption is a third clause, and it is executed by nothing else:
    # both pinned tools carry no `_DELIBERATE_BARE_FORM` line, so deleting the
    # clause left the suite GREEN (measured). It is keyed by (path, stripped line),
    # so the census's own first entry is the only honest subject.
    record_rel, record_line = sorted(census._DELIBERATE_BARE_FORM)[0]
    assert not _advertise_offenders(record_rel, record_line, census), (
        f"a census RECORD exemption must stay exempt ({record_rel!r}): rewriting "
        f"recorded evidence to satisfy a gate falsifies provenance, which is why the "
        f"census exempts it"
    )


def test_the_advertised_form_sweep_is_WIRED_into_the_pin(monkeypatch, capsys):
    """The sweep's CALL SITE, not only its filter.

    `test_the_advertised_form_filter_is_not_deaf` proves `_advertise_offenders`
    flags an offender — but it calls the helper DIRECTLY, so its caller can stop
    calling it and every one of those assertions still passes. Measured (review
    round 16): replacing the call site with `offenders = []` left this file at
    **206 passed**, identical to unmutated — because both pinned tools legitimately
    yield no offenders, so "the sweep ran and found nothing" and "the sweep is
    gone" are the SAME observation at that site. A recorded claim
    ("the advertised-invocation sweep driven by the census's own
    `_BARE_INVOCATION` + exemption sets") could then be deleted silently.

    So the sweep is DISCONNECTED here and the pin is driven directly — the same
    technique `test_a_pinned_tool_with_NO_census_row_is_a_REFUSAL_not_a_licence`
    uses for the `REFUSAL_CODES` arm. If the call site is removed, the injected
    offender never reaches the assertion and this test reds for the reason it
    exists rather than passing on an empty result.
    """
    sentinel = [
        "tools/capstone_ledger.py:1: `python3 tools/capstone_ledger.py`"
    ]
    monkeypatch.setattr(
        sys.modules[__name__],
        "_advertise_offenders",
        lambda *args, **kwargs: list(sentinel),
    )
    with pytest.raises(AssertionError, match="advertises"):
        test_the_tool_carries_the_5128_interpreter_guard_first(
            TOOL, capsys, monkeypatch
        )


def test_capstone_ledgers_refusal_code_is_the_one_the_census_pins():
    """`REFUSAL_CODES["tools/capstone_ledger.py"] == 1`, and it must stay there.

    The census's `REFUSAL_WHY` entry explains the choice: the tool's 0-4 set is
    CLOSED and fully spent, so `EXIT_INVALID` is reused on purpose as the
    fail-closed neighbour. Adding 5 to satisfy a lint would change a documented
    public contract, so the constant itself is pinned — a coordinated edit of
    the constant AND the guard is what `status == 1` in the parametrised test
    catches.
    """
    assert cl.EXIT_INVALID == 1
    # The row the sentence above is about, asserted against the census ITSELF: a
    # census edit that moved the pin would otherwise leave this test green while
    # its own name became false (measured by mutating the row to 2 — only the
    # parametrised status arm reded).
    census = _census_module()
    assert census.REFUSAL_CODES["tools/capstone_ledger.py"] == 1, (
        f"the census's own row for this tool must be 1; got "
        f"{census.REFUSAL_CODES['tools/capstone_ledger.py']!r}"
    )
# ── the fail-open that SHIPPED to main, and the two pins that catch it ──────────
#
# `_is_tracked` folded "the probe could not be SPAWNED" into `_tracked_unprovable()`
# (NOTE printed, record not blamed) and it landed on `main` via #11470. Measured on
# `origin/main`'s own file: with only `git ls-files` raising `OSError(EMFILE)`, a
# `.gitignore`d artifact answered `(True, '')` and the record exited 0 CLEAN. These
# two pins are the ones that were missing there; both are HERMETIC — a temp repo with
# one committed and one untracked file, never the lane-only `WORKTREE-REPORT.md`.


def _hermetic_repo(tmp_path) -> Path:
    """A throwaway git repo with exactly `committed.txt` tracked. Signed-commit-proof."""
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
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    return repo


def test_an_EMFILE_on_the_ls_files_probe_cannot_manufacture_a_clean_exit(tmp_path):
    """The probe could not RUN. The row must be REFUSED, not counted.

    `OSError` covers both "no `git` binary" and "the fork failed under load", and the
    shipped guard answered the second with an abstention: `_is_tracked -> True`, so an
    untracked artifact satisfied `verified`. That is a fail-OPEN — a CLEAN exit made
    out of a check that never ran — and it is what this pin exists to stop.
    """
    import subprocess as sp

    repo = _hermetic_repo(tmp_path)
    real_run = sp.run
    fired: list[str] = []

    def explode(argv, *a, **kw):
        if argv[:2] == ["git", "ls-files"]:
            fired.append(" ".join(argv[:2]))
            raise OSError(24, "Too many open files")
        return real_run(argv, *a, **kw)

    import types

    mp = pytest.MonkeyPatch()
    # `types.SimpleNamespace`, NOT `cl.subprocess.run`: the latter mutates the real
    # `subprocess` module object, so every module in the process is patched for the
    # duration and any concurrent machinery that shells out sees this fake.
    mp.setattr(cl, "subprocess", types.SimpleNamespace(run=explode))
    mp.setattr(cl, "_commit_state", lambda sha, repo_root=None: "exists")
    try:
        data = _ledger([_row("A", evidence=["untracked.txt"])])
        with pytest.raises(cl.InvalidLedger, match="did not answer here"):
            cl.validate(data, repo_root=repo)
    finally:
        mp.undo()
    # ⛔ THE STUB MUST HAVE FIRED. Without this the test passes with `explode` neutered
    # — the refusal it would then observe is the ordinary UNTRACKED one, already pinned
    # by `test_an_untracked_artifact_is_invalid`, and the EMFILE cause would be covered
    # by nothing while this row stayed green. If `_is_tracked` ever imports `run`
    # directly instead of through the module attribute, the stub silently stops
    # matching and this assertion is what notices.
    assert fired == ["git ls-files"], fired
    # ⛔ OUTSIDE the patched window on purpose: with git working, a TRACKED artifact is
    # ACCEPTED, so this pin is not passing on a tool that refuses everything. (It cannot
    # be checked inside the window — there `None` is the true answer for every path, and
    # asserting acceptance there would assert the bug.)
    ok, why = cl._evidence_is_real("committed.txt", repo)
    assert ok, why
    # And with git answering, the same artifact is still refused BY NAME — the refusal
    # must name the artifact, not degrade every row into "could not be run".
    ok, why = cl._evidence_is_real("untracked.txt", repo)
    assert not ok and "not TRACKED" in why, why


def test_the_unrunnable_probe_path_is_not_the_abstention_path():
    """The two OSError causes must not share a return value — that sharing WAS the bug.

    `_tracked_unprovable()` returns the ABSTENTION (`True`) — git could not answer for
    the TREE at all; `_tracked_unrunnable()` returns `None` — NOTHING was measured.
    Collapsing them is the fail-open, so this asserts they are distinct at the function
    level, not just end to end.

    ⛔ `_tracked_unprovable` has TWO causes and BOTH are driven by a measured probe,
    which is why its argument is required (#11474 review): `git is present and says
    this is not a work tree` (a bare repo over `GIT_DIR`, rc 0 + `false`) and "did not
    answer `true`" (a tarball, rc 128). It is NOT "a MEASURED answer"
    unconditionally: on the no-repository branch git was asked and could not answer.
    Both ABSTAIN — #11503 Option 1 — and the distinction that matters here is
    abstention vs. no-measurement, not the sub-cause.
    """
    import types

    answered_false = types.SimpleNamespace(returncode=0, stdout="false\n", stderr="")
    assert cl._tracked_unrunnable(OSError(24, "Too many open files"), "x") is None
    assert cl._tracked_unprovable(answered_false) is True


def test_an_EMFILE_on_the_FOLLOW_UP_probe_also_refuses():
    """The SECOND `OSError` site, which had no pin at all.

    `_is_tracked` calls `git ls-files`, and then — only when that exits non-zero — a
    follow-up `git rev-parse --is-inside-work-tree`. Both calls can fail to SPAWN, and
    both were folded into the abstention, so reverting only the follow-up guard back
    to the shipped fail-open left the whole suite green at the head BEFORE this pin was
    added. With this pin present the same revert is red and NAMES this test — which is
    the point: the pin is the only thing standing between that guard and deletion as
    dead code.

    ⛔ No absolute pass count is quoted, and that is deliberate: a count goes stale
    within one commit (this file gains pins every round), so a reader can never
    reproduce it and the number becomes a claim nobody measured. The failure SHAPE —
    "green before the pin, red naming this test after it" — is the part that is
    checkable, and it is the part the claim rests on.
    """
    import types

    class _Result:
        returncode = 1  # ls-files ran and said "no such entry" -> the probe is reached
        stdout = ""
        stderr = ""

    def boom_on_rev_parse(argv, **kwargs):
        if argv[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            raise OSError(24, "Too many open files")
        return _Result()

    mp = pytest.MonkeyPatch()
    mp.setattr(cl, "subprocess", types.SimpleNamespace(run=boom_on_rev_parse))
    try:
        assert cl._is_tracked(REPO_ROOT / "docs" / "x.md", REPO_ROOT) is None
    finally:
        mp.undo()


def test_the_glob_arm_does_not_read_an_UNRUNNABLE_probe_as_a_held_match(monkeypatch):
    """`None` is not a match. The glob arm is the one place the tri-state is unpinned.

    `if _is_tracked(...) is True:` is what keeps an UNRUNNABLE probe out of the
    `return True, ""` arm — and nothing exercised it: reverting it to
    `is not False` (so `None` counts as held) left the whole suite green at the head
    this pin was added to. `None` is falsy, so the mutant is invisible to any plain
    truthiness test; only an assertion that drives `None` through THIS arm catches it.

    It is a genuine fail-open rather than a wording nit, because it is the arm that
    can ANSWER "a held artifact exists" from a check that never ran.
    """
    monkeypatch.setattr(cl, "_is_tracked", lambda resolved, root: None)
    ok, why = cl._evidence_is_real("cmd:uv run python docs/evidence/9664-capstone/*.md", REPO_ROOT)
    assert ok is False, why
    # ⛔ And it must say nothing was MEASURED — not that git answered "untracked",
    # which would be the collapsed-cause defect one layer down.
    assert "did not answer here" in why, why
    assert "untracked" not in why, why


def test_the_bare_path_arm_does_not_read_an_UNRUNNABLE_probe_as_a_held_match(monkeypatch):
    """`None` is not a match on the BARE-PATH arm either — and nothing pinned it.

    The glob arm's `is True` is pinned above and `_evidence_is_real` has its own
    `None` pin, but `_names_artifact`'s bare-path arm is a THIRD call site of the
    tri-state and it carried no assertion at all — so a revert of `if tracked is
    None:` was unobservable, which is the whole reason this pin exists.

    ⛔ MEASURED AT `21c446ab002`, because an earlier revision of this docstring got
    it wrong: reverting `if tracked is None:` to `if False:` does **not** answer
    `(True, '')` at this head. The fourth-answer guard later in the SAME arm
    (`if tracked is not True:`) catches the `None` first, so the arm answers
    ``(False, "'<path>' cannot be verified: the TRACKED check answered None, which
    is not a measurement that it is in the tree")``, the row is still REFUSED, and
    this test reddens on its MESSAGE assertion (`"did not answer here" in why`)
    rather than on a fail-open. Both guards are load-bearing for different halves:
    this one for the CAUSE a reader is told, the fourth-answer one for the exit
    code. Recorded rather than left as a "MEASURED" claim the head falsifies.

    ⛔ THE BLANKET `lambda …: None` STUB IN THIS PIN CANNOT REACH THE LEDGER'S EXIT
    CODE — it refuses EVERY path, so a `cmd:` row goes INVALID even with this arm
    reverted and the assertion would read as coverage that is not there. The
    END-TO-END consequence IS pinnable with a stub that answers `None` for the target
    only, and the pin below does exactly that: for a bare-path `cmd:` target,
    `_names_artifact` is the ONLY decider (the `-c` script-body arm is the one that
    also reaches `_evidence_is_real`), so this arm is all that stands between an
    unmeasured probe and a CLEAN exit.
    """
    monkeypatch.setattr(cl, "_is_tracked", lambda resolved, root: None)
    ok, why = cl._names_artifact(REAL_EVIDENCE, REPO_ROOT, REPO_ROOT)
    assert ok is False, f"an UNRUNNABLE probe must not count as a held match: {why}"
    # ⛔ And it must name the UNMEASURED cause: `not TRACKED` is the sentence for
    # git's MEASURED "no" (the `False` arm owns it), and collapsing the two is the
    # guess-the-cause defect this function exists to remove.
    assert "did not answer here" in why, why
    assert "not TRACKED" not in why, why


def test_a_cmd_target_behind_an_UNRUNNABLE_probe_is_REFUSED_end_to_end(monkeypatch):
    """The same arm's USER-VISIBLE consequence: the record may not exit CLEAN.

    The seam pin above drives `_names_artifact` directly; this one pins what the
    RECORD does with the answer — a one-row `verified` ledger citing a `cmd:` target
    whose TRACKED probe never ran is INVALID. ⛔ MEASURED AT `21c446ab002`: with the
    arm reverted (`if tracked is None:` -> `if False:`) the row is **still INVALID**
    — the fourth-answer guard later in the SAME arm refuses it with "the TRACKED
    check answered None, which is not a measurement that it is in the tree" — so
    this test reddens on its message assertion (`"TRACKED check did not answer
    here" in message`), NOT on an accepted row. An earlier revision of this
    docstring claimed the row was ACCEPTED: that was true before the fourth-answer
    guard landed in the same change, and is false at this head.

    The stub is SELECTIVE on purpose: blanket-`None` would also silence the arms that
    run for every other path, and the row would be refused for the wrong reason — the
    `asked == []` assertion below is what proves the failure came from THIS arm.
    """
    asked: list[str] = []

    def only_the_command_target_is_unmeasurable(resolved, root):
        asked.append(resolved.name)
        return None if resolved.name.endswith("README.md") else True

    monkeypatch.setattr(cl, "_is_tracked", only_the_command_target_is_unmeasurable)
    entry = f"cmd:uv run python {REAL_EVIDENCE}"
    with pytest.raises(cl.InvalidLedger) as excinfo:
        cl.validate(_ledger([_row("A", evidence=[entry])]), repo_root=REPO_ROOT)
    message = str(excinfo.value)
    assert "TRACKED check did not answer here" in message, message
    assert asked and asked[0] == "README.md", asked


def test_a_glob_mixing_untracked_and_unrunnable_reports_the_measured_refusal(monkeypatch):
    """A mixed glob names BOTH causes; it may not speak for the file it never asked about.

    Both causes are refusals, so this cannot change an exit code — it fixes WHICH
    sentence a reader gets. ⛔ `untracked_seen` winning outright emitted the universal
    claim "EVERY file the glob matches is untracked by git" about matches git was
    never asked about (#11474 review), so the mixed sentence must carry the measured
    cause AND the unmeasured one, and must not generalise to "every".
    """
    seen: list[str] = []

    def mixed(resolved, root):
        seen.append(resolved.name)
        return None if len(seen) == 1 else False

    monkeypatch.setattr(cl, "_is_tracked", mixed)
    ok, why = cl._evidence_is_real(
        "cmd:uv run python docs/evidence/9664-capstone/run-2026-10-26/*.txt", REPO_ROOT
    )
    # Two matches are required to exercise precedence at all — with one, only the first
    # cause is ever produced and the assertion below would pass for the wrong reason.
    assert len(seen) >= 2, f"needs two matches to exercise precedence, saw {seen}"
    assert ok is False, why
    assert "untracked by git" in why, why
    assert "did not answer here" in why, why
    assert "every file the glob" not in why, (
        "a mixed glob must not claim EVERY match is untracked: one of them was never "
        f"measured, and that is the guess-the-cause defect this branch removes: {why}"
    )


def test_a_glob_accepted_by_a_later_match_does_not_announce_the_rows_fate(monkeypatch, capsys):
    """A PER-PATH refusal is not a row verdict — the glob can accept on a later match.

    Measured shape that made this a defect rather than a wording nit: one `ls-files`
    fork raising `OSError(24)` under load and the next answering normally. The probe
    printed "the row does not count" for the unmeasured match, the tracked match then
    satisfied the glob, and the ledger exited **0 CLEAN** — a printed refusal on a
    passing row, which is the legibility failure `_tracked_unrunnable` exists to
    remove. The row-level sentence belongs to the caller, which knows the verdict.
    """
    calls: list[str] = []

    def first_unrunnable_then_tracked(resolved, root):
        calls.append(resolved.name)
        if len(calls) == 1:
            cl._tracked_unrunnable(OSError(24, "Too many open files"), resolved)
            return None
        return True

    monkeypatch.setattr(cl, "_is_tracked", first_unrunnable_then_tracked)
    ok, why = cl._evidence_is_real(
        "cmd:uv run python docs/evidence/9664-capstone/run-2026-10-26/*.txt", REPO_ROOT
    )
    assert len(calls) >= 2, f"needs a later match to accept, saw {calls}"
    assert ok is True, why
    err = capsys.readouterr().err
    assert "UNMEASURED" in err, "the per-path refusal must still be PRINTED, not silent"
    # ⛔ THE CAUSE IS THE HALF THAT WAS UNPINNED (#11474 review). MEASURED: replacing
    # `_tracked_unrunnable`'s line with the MEASURED sentence ("… so {rel} is not
    # TRACKED by git") — the exact defect its docstring forbids — left all 179 tests
    # green, because nothing asserted the text of the line this test exists to read.
    # A renderer whose cause is unpinned is a renderer free to borrow the other one.
    assert "could not be RUN" in err, (
        f"the per-path refusal must name the cause it measured (the probe never ran): {err}"
    )
    assert "not TRACKED" not in err, (
        f"the probe never ran, so no line may borrow git's measured answer: {err}"
    )
    assert "the row does not count" not in err, (
        "the probe cannot know the row's fate — a later tracked match accepted it, so "
        f"the per-path line must not claim the row was refused: {err}"
    )
    # ⛔ AND THE MARKER IS NOT AN EXCEPTION TO THAT (#11474 review). This test asserts
    # `ok is True` four lines above and a verdict word here, which is the contradiction
    # a reader of a CLEAN run's stderr hits: 0 exit, and "REFUSED" printed. Measured
    # shape: the marker read `REFUSED` for all four per-path renderers while this pin
    # demanded it, so the one line that could be false on a passing row was frozen in
    # place by its own test.
    assert "REFUSED" not in err, (
        f"this row PASSED (a later match was tracked) — no line may announce a refusal: {err}"
    )
    # …and the FILE arm's sentence answers the FILE question. The mirror direction is
    # pinned on the two directory pins: measured, the four renderers' sentences were
    # freely swappable with the whole suite green (#11474 review).
    assert "about what" not in err, (
        f"this renderer asks about a FILE, not what a directory holds: {err}"
    )


def test_an_ABSTAINING_tree_is_accepted_and_prints_the_NOTE(tmp_path, monkeypatch, capsys):
    """The `return _tracked_unprovable()` CALL SITE, end to end — it had no pin at all.

    `test_the_unrunnable_probe_path_is_not_the_abstention_path` asserts the HELPER's
    return value (`_tracked_unprovable() is True`) and nothing asserted that
    `_is_tracked` still RETURNS it: MEASURED at the head BEFORE this pin landed
    (`8bb34e5bb^`, 173 collected), reverting that call site to
    `return False` left the whole suite green at 173 passed, so the whole abstention
    arm could be deleted as dead code and no row would notice (#11474 review).

    ⛔ That measurement is DATED rather than restated as a fact about "this head"
    (#11474 review): at any head that CARRIES this pin the revert is **not** green —
    it reddens this pin itself, which is the whole point of adding it. Re-measured at
    the #11474 rebase, and again with the exit-128 pin beside it. No FRESH pass count
    is quoted: a count copied forward is the one claim a reader can falsify in one
    command, and this file gains a pin most rounds. (The sha here is `8bb34e5bb^` and
    not `21c446ab002`: that later head already CARRIES the pin and collects 178, so
    citing it for "before the pin existed" made this paragraph refute its own next
    one — measured, `git show 21c446ab002:tests/… | grep -c 'def test_an_ABSTAINING'`
    is 1.)

    Real git, not a stub: a BARE repo over `GIT_DIR` makes `ls-files --error-unmatch`
    exit 1 and `rev-parse --is-inside-work-tree` answer `false` with rc 0 — git is
    present, ran, and answered "this is not a work tree", which is the abstention's own
    trigger.

    ⛔ THIS PINS #11503 OPTION 1, WHICH IS THE RECORDED DECISION — "the probe could not
    RUN refuses; 'git answered no' / 'git cannot tell' abstains with the NOTE". It is
    pinned rather than left implicit because the REOPEN ARGUMENT filed on that issue
    (2026-11-08T04:21Z) measures the cost of the abstention in exactly this shape: the
    sha arm can answer `exists` while the tracked arm abstains, so an artifact in no
    index reaches a CLEAN exit. That amendment is NOT adopted in this PR (it would
    reverse the ruling quietly); when it is decided, this pin flips with it — and the
    `_commit_state` stub below is what makes the shape reachable, exactly as the
    reopen's ledger did.

    ⛔ What must NOT change silently either way is the CAUSE. `not TRACKED by git` is
    git's MEASURED answer and is not what happened here, so no refusal on this path may
    say it — that is the guess-the-cause defect this whole change removes.
    """
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "held.txt").write_text("held\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(bare))
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root=None: "exists")

    # The call site itself: anything but `True` here means the abstention was dropped.
    assert cl._is_tracked(tree / "held.txt", tree) is True
    ok, why = cl._evidence_is_real("held.txt", repo_root=tree)
    assert ok is True, why
    # And the ROW consequence, which is what the helper's value is FOR.
    cl.validate(_ledger([_row("A", evidence=["held.txt"])]), repo_root=tree)
    err = capsys.readouterr().err
    assert "git is present and says this is not a work tree" in err, (
        "git WAS asked and answered `false` (rc 0) — the NOTE must say that, not claim "
        f"git was unavailable, which is the cause it did not measure: {err}"
    )
    assert "git is unavailable here" not in err, (
        "this is the BARE/`GIT_DIR` arm: git ran and said 'not a work tree'. The "
        "abstention VERDICT is #11503 Option 1 and is unchanged, but its printed "
        f"CAUSE may not be one that was never measured: {err}"
    )
    assert "not TRACKED" not in err, (
        "git answered (it is present and said 'not a work tree'), so no line may claim "
        f"it measured this path as untracked: {err}"
    )


def test_a_probe_that_did_NOT_answer_false_states_the_OTHER_cause(capsys):
    """The round-4 discriminator, pinned in BOTH directions — it was pinned in one.

    `_tracked_unprovable` serves TWO causes: a tree git could not answer in at all (a
    tarball, no repository), and a tree where git **ran and answered `false`** (a bare
    repo over `GIT_DIR`). Round 4 added the discriminator

        if probe is not None and probe.returncode == 0 and probe.stdout.strip() == "false":

    so each path states the cause it measured, and
    `test_an_ABSTAINING_tree_is_accepted_and_prints_the_NOTE` asserts the SECOND one.
    ⛔ Nothing asserted the FIRST. MEASURED at `ac896fa34f`, both simplifications of
    that exact line left the whole suite green at **180 passed**:

        if probe is not None:          -> 180 passed
        if True:                       -> 180 passed

    and the first is not cosmetic: a no-repository tree makes
    `rev-parse --is-inside-work-tree` exit **128**, so under it a tarball renders
    *"git is present and says this is not a work tree"* — a cause git never gave. That
    is precisely the defect this PR removes, re-introducible by deleting one token
    from the line this round added. `"git is unavailable here"` appeared in this file
    only as a NEGATIVE assertion, i.e. nothing ever produced it — a renderer whose
    cause is unpinned is a renderer free to borrow the other one.

    Driven through the helper rather than end to end: the branch this pins is the
    ternary on the `probe` argument, and `_tracked_unprovable` is the only place that
    reads it.
    """
    import types as _types

    answered_bare = _types.SimpleNamespace(returncode=0, stdout="false\n", stderr="")
    could_not_answer = _types.SimpleNamespace(
        returncode=128, stdout="", stderr="fatal: not a git repository"
    )

    # (1) git could NOT answer: the cause is what the PROBE measured, not a `false`
    # answer and not an inference ("no repository") the exit code does not support —
    # `fatal: detected dubious ownership in repository at '<path>'` is also rc 128, in
    # a repository that exists.
    assert cl._tracked_unprovable(could_not_answer) is True
    err = capsys.readouterr().err
    assert "did not answer `true`" in err, (
        f"rc 128 is git failing to answer, and the NOTE must say that: {err}"
    )
    assert "exited 128" in err, f"the NOTE must name the measurement it took — the exit code: {err}"
    assert "not a git repository" in err, f"git's own first stderr line is carried verbatim: {err}"
    assert "git is present and says this is not a work tree" not in err, (
        "git exited 128 — it did NOT answer `false`, so that cause was never measured "
        f"and may not be printed: {err}"
    )

    # (2) git RAN and answered `false`: the other cause, and only that one.
    assert cl._tracked_unprovable(answered_bare) is True
    err = capsys.readouterr().err
    assert "git is present and says this is not a work tree" in err, (
        f"rc 0 + `false` is git answering, and the NOTE must say so: {err}"
    )
    assert "git is unavailable here" not in err, (
        f"git was asked and replied — it was not unavailable: {err}"
    )

    # (3) rc 0 with an answer that is neither `true` nor `false` — git does not produce
    # it, and the renderer must still name the BYTES rather than fall back to a cause
    # no probe gave (#11474 review: that fallback was the dead "git is unavailable
    # here" branch, and its removal is what makes the argument required).
    answered_other = _types.SimpleNamespace(returncode=0, stdout="maybe\n", stderr="")
    assert cl._tracked_unprovable(answered_other) is True
    err = capsys.readouterr().err
    assert "did not answer `true`" in err, err
    assert "'maybe'" in err, (
        f"the exit code is 0, so the BYTES are the measurement that must be named: {err}"
    )
    assert "git is unavailable here" not in err, (
        f"the probe ran and answered — nothing may call git unavailable: {err}"
    )
    assert "git is present and says this is not a work tree" not in err, (
        f"it did not answer `false` either, so neither measured cause applies: {err}"
    )


def test_a_TARBALL_on_a_box_WITH_git_does_not_claim_git_is_unavailable(
    tmp_path, monkeypatch, capsys
):
    """No repository, but git IS installed — so nothing may say "git is unavailable".

    ⛔ THE THIRD CAUSE REACHING `_tracked_unprovable` (#11474 review). MEASURED in a
    directory with no `.git` on a box carrying git 2.43.0: `git ls-files` exits **128**
    (`fatal: not a git repository …`) and `rev-parse --is-inside-work-tree` exits 128
    too. Git was PRESENT and RAN and said "there is no repository here".

    The renderer's default sentence said *"git is unavailable here"* for that case, and
    because the genuinely git-ABSENT case now routes to `_tracked_unrunnable`, that
    falsified default was left as the SOLE production rendering of this renderer — the
    guess-the-cause defect this PR removes, on the first cause its own docstring listed.

    The CONTROL comes first: this box must actually HAVE git, or the case is not the one
    this pin is about and a pass would mean nothing.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    assert _shutil.which("git"), (
        "this pin needs a box WITH git; without it the case is not a tarball"
    )

    tarball = tmp_path / "unpacked-tarball"
    tarball.mkdir()
    (tarball / "held.txt").write_text("held\n", encoding="utf-8")

    # Prove the shape: git answers, and its answer is "no repository" (128), not silence.
    probe = _subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(tarball),
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 128, (
        f"expected 'not a git repository' (rc 128) from git {_subprocess.run(['git', '--version'], capture_output=True, text=True).stdout.strip()}, "
        f"got rc {probe.returncode} — the fixture is not a tarball"
    )

    # The abstention VERDICT is #11503 Option 1 and is unchanged: this path still counts.
    assert cl._is_tracked(tarball / "held.txt", tarball) is True
    err = capsys.readouterr().err
    assert "did not answer `true`" in err, (
        "git ran and exited 128 — the NOTE must say what the PROBE measured, not infer "
        f"'no repository' from a non-zero exit: {err}"
    )
    assert "exited 128" in err, (
        f"the note must name the measurement it took (the exit code), not just a verdict: {err}"
    )
    assert "not a git repository" in err, (
        "git's own words are what separates a tarball from a repository git REFUSED, "
        f"so they are carried verbatim: {err}"
    )
    assert "not TRACKED" not in err, (
        f"nothing was measured about this path, so no line may claim a git answer: {err}"
    )


def test_a_repository_git_REFUSES_is_not_reported_as_a_MISSING_repository(
    tmp_path, monkeypatch, capsys
):
    """A non-zero `rev-parse` is a MEASUREMENT, not the inference "no repository".

    ⛔ THE CAUSE THE INFERENCE GOT WRONG (#11474 review). The renderer read every
    non-zero `rev-parse --is-inside-work-tree` exit as "there is no repository to ask"
    — and real git produces that exit IN a repository that exists: `GIT_TEST_ASSUME_
    DIFFERENT_OWNER=1` makes it print `fatal: detected dubious ownership in repository
    at '<path>'` and exit **128**. The shipped line therefore claimed no repository
    while quoting git naming one, which is the guess-the-cause defect this PR removes,
    on the renderer that was added to remove it.

    ⛔ THE REFUSAL IS DRIVEN BY A STUB, NOT BY GIT'S OWN TEST HOOK (#11474 review, round
    7 — this pin was RED in CI at `78602ee66cce`). An earlier revision made git refuse
    with `GIT_TEST_ASSUME_DIFFERENT_OWNER=1`, which is git's harness-internal switch
    for simulating a checkout owned by another user (`t/t0033-safe-directory.sh`) — not
    a supported interface, and not portable. Measured on both boxes, same probe:

        git 2.43.0 (dev box)      -> rc 128, `fatal: detected dubious ownership …`
        git 2.56.0 (CI runner)    -> rc 0   (the failing assertion's own words:
                                     `needs git to REFUSE the repository (rc 128), not: rc=0`)

    so this pin PASSED here and FAILED deterministically in the required `test (a)`
    leg. Whether the difference is that newer git routes the hook differently or that
    the runner's global `safe.directory` covers the fixture, the pin must be immune to
    BOTH rather than assume either. Its subject is the RENDERER's behaviour on a
    non-zero `rev-parse` exit — not git's ownership implementation — so the refusal is
    produced by a `git` on PATH that RAN and REFUSED the repository, exactly the shape
    a dubious-ownership checkout produces. The real-git control is kept (it is what
    proves the fixture IS a repository, and therefore that "no repository" is a false
    cause), and it is invoked by ABSOLUTE path so the stub cannot answer for it.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    real_git = _shutil.which("git")
    assert real_git, "this pin needs a box WITH git"

    repo = tmp_path / "refused"
    repo.mkdir()
    (repo / "held.txt").write_text("held\n", encoding="utf-8")
    _subprocess.run([real_git, "-C", str(repo), "init", "-q"], check=True)

    # CONTROL: on the same repo, real git answers normally. Without this a broken
    # fixture would look like the refused shape and the pin would pass for the wrong
    # reason. It runs BEFORE the stub is on PATH, by absolute path.
    control = _subprocess.run(
        [real_git, "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert (control.returncode, control.stdout.strip()) == (0, "true"), control

    # The refusal. Every invocation of this `git` RAN and REFUSED the repository — the
    # shape git produces when `.git` is present and it will not trust it, and the shape
    # whose exit 128 the renderer used to read as "there is no repository to ask".
    stub_dir = tmp_path / "refused-bin"
    stub_dir.mkdir()
    stub = stub_dir / "git"
    stub.write_text(
        "#!/bin/sh\n"
        f"echo \"fatal: detected dubious ownership in repository at '{repo}'\" >&2\n"
        "exit 128\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{stub_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    # CONTROL for the stub itself: it must be what `git` RESOLVES to here, and it must
    # produce the refused shape. Without this a stub that never ran (wrong PATH order,
    # a bad shebang) would leave the renderer to abstain for a different cause while
    # the downstream assertions still read green.
    assert Path(_shutil.which("git") or "") == stub, (
        f"the stub must be the git this PATH resolves, got {_shutil.which('git')!r}"
    )
    refused = _subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 128, (
        "this pin needs git to REFUSE the repository (rc 128), not: "
        f"rc={refused.returncode} {refused.stderr}"
    )
    assert "dubious ownership" in refused.stderr, refused.stderr
    # The repository is right there — that is what makes "no repository" a false cause.
    assert (repo / ".git").is_dir(), (
        "if the repository were absent this test would be the tarball case over again"
    )

    # The verdict is the recorded #11503 abstention and is unchanged; only the CAUSE is
    # at issue here.
    assert cl._is_tracked(repo / "held.txt", repo) is True
    err = capsys.readouterr().err
    assert "exited 128" in err, f"the note must name the exit code it measured: {err}"
    assert "dubious ownership" in err, (
        f"git's own words must reach the reader, or the cause is guessable only: {err}"
    )
    assert "no repository" not in err, (
        "git named the repository in its own message, so a line claiming there is none "
        f"is a cause nobody measured — the defect this PR removes: {err}"
    )


def test_a_git_that_COULD_NOT_ANSWER_is_not_read_as_a_measured_untracked(
    tmp_path, monkeypatch, capsys
):
    """`git ls-files` exit 128 is a FAILURE to answer, NOT the measured `no` (exit 1).

    ⛔ THE THIRD COLLAPSED CAUSE (see `_tracked_unanswered`). `ls-files --error-unmatch`
    exits **1** for "no match" and **128** when it cannot read its own index — and in
    that second state `git rev-parse --is-inside-work-tree` still answers `true` rc 0.
    The old `returncode != 0` test therefore read a failure-to-answer as the MEASURED
    sentence, so a file every clone has was refused with *"is not TRACKED by git, so a
    reader's clone does not have it"* — the same guess-the-cause defect `_is_tracked`
    was fixed for, one cause over, and it is what `_tracked_unrunnable` does NOT cover
    because nothing raised.

    Real git, no `subprocess` stub: a valid repo with a TRACKED file, then
    `GIT_INDEX_FILE` pointed at bytes that are not an index. The CONTROL assertion
    comes first and is the reason this pin can fail for the right cause — without it, a
    spelling mistake in the path would make the arm look covered while git was never
    given a bad index at all.
    """
    import subprocess as sp

    repo = tmp_path / "answered"
    repo.mkdir()
    (repo / "held.txt").write_text("held\n", encoding="utf-8")
    git = ["git", "-C", str(repo)]
    sp.run([*git, "init", "-q"], check=True)
    sp.run([*git, "add", "held.txt"], check=True)
    sp.run(
        [
            *git,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )

    # CONTROL: with its real index, git answers, and the answer is "tracked".
    assert cl._is_tracked(repo / "held.txt", repo) is True

    corrupt = tmp_path / "index.bin"
    corrupt.write_bytes(b"not a git index")
    monkeypatch.setenv("GIT_INDEX_FILE", str(corrupt))

    # The shape the arm exists for, asserted on git itself rather than assumed: the
    # work-tree probe still SUCCEEDS (so the failure cannot route to the abstention),
    # while the path probe FAILS rather than answering "no match".
    probe = sp.run([*git, "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True)
    assert (probe.returncode, probe.stdout.strip()) == (0, "true"), (
        "this pin needs git to still answer for the TREE, or the arm is unreachable "
        f"and the assertion below would pass for the wrong reason: {probe}"
    )
    ls_files = sp.run(
        [*git, "ls-files", "--error-unmatch", "--", "held.txt"],
        capture_output=True,
        text=True,
    )
    assert ls_files.returncode not in (0, 1), (
        "this pin needs a FAILED probe (not 'no match'), not: "
        f"rc={ls_files.returncode} {ls_files.stderr}"
    )

    # The seam: nothing was measured, so neither a `True` nor a `False` may be invented.
    assert cl._is_tracked(repo / "held.txt", repo) is None

    ok, why = cl._evidence_is_real("held.txt", repo_root=repo)
    assert ok is False, why
    # ⛔ THE CAUSE IS PINNED ON `err`, AND THE WHOLE OF IT. Two earlier assertions here
    # could not fire: the exit code — the only thing this arm MEASURED — was never
    # asserted, so dropping it from the line left the suite green (measured: mutant
    # `(`git ls-files` exited {returncode})` -> `(git did not answer)` = 181 passed);
    # and the negatives below were asserted on `why`, which is `_evidence_is_real`'s
    # ROW-level sentence and structurally cannot carry the PER-PATH line at all.
    err = capsys.readouterr().err
    assert "did not get an ANSWER" in err, err
    assert f"exited {ls_files.returncode}" in err, err
    assert "not TRACKED" not in err, (
        f"git never answered for this path, so that sentence is a cause nobody measured: {err}"
    )
    # ⛔ AND THIS RENDERER'S MARKER AND QUESTION WERE UNPINNED (#11474 review).
    # MEASURED at the head that renamed the marker to `UNMEASURED` "on all four
    # renderers": reverting THIS one to `REFUSED`, and separately swapping its sentence
    # for the directory arm's, each left the suite at 320 passed. Every assertion here
    # described the sentence's subject; none described the word a reader greps for, or
    # which of the two probes this line belongs to. The claim is load-bearing because
    # this renderer CAN fire on a row that is then ACCEPTED (a later glob match), so a
    # verdict marker here prints `REFUSED` on a CLEAN run.
    assert "UNMEASURED" in err, f"the marker names the measurement, not a fate: {err}"
    assert "REFUSED" not in err, f"no per-path line may announce a verdict: {err}"
    assert "about what" not in err, (
        f"this renderer asks about a FILE, not what a directory holds: {err}"
    )

    # And the CALLER's row-level sentence must not pick one of `_is_tracked`'s two
    # `None` causes either — "could not be RUN" is false here, the check DID run.
    assert "did not answer here" in why, why
    assert "not TRACKED" not in why, why
    # ⛔ AND THE OTHER CAUSE, NOT JUST THE OTHER RENDERER (#11474 review). A delta
    # REPLACED this negative with the `not TRACKED` one above and left the suite
    # green: measured at `445cf2cd3af`, making `_evidence_is_real`'s `None` branch
    # print "… did not answer here; the check could not be run here: …" — a cause
    # nobody measured, for a check that DID run — left **184 passed**. The two
    # negatives guard different borrowings and neither substitutes for the other.
    assert "could not be run here" not in why, why


def test_a_PATTERN_shaped_filename_does_not_inherit_its_tracked_SIBLING(tmp_path):
    """`-- <rel>` is a PATHSPEC, so a `*` in a NAME matched a DIFFERENT tracked file.

    ⛔ THE WILDCARD DIRECTION OF THAT PATHSPEC (#11474 review), and it is the
    fail-open this whole change exists to refuse, reached through the probe itself.
    `_is_tracked` handed `str(rel)` straight to git, so with `docs/ev-axb.md` tracked
    and `docs/ev-a*b.md` present but in NO index:

        $ git ls-files --error-unmatch -- 'docs/ev-a*b.md'
        docs/ev-axb.md                 # rc 0 — a DIFFERENT file answered

        _is_tracked(docs/ev-a*b.md)               -> True
        _evidence_is_real("docs/ev-a*b.md")      -> (True, '')   # a `verified` row

    An artifact no clone has therefore satisfied the TRACKED rule. The docstring that
    dismissed the pathspec hazard argued only the PREFIX direction ("only reachable
    for a DIRECTORY-shaped name"), which does not cover a metacharacter in the
    FILENAME. `:(literal)` makes the name a name; this pin holds both directions — the
    wildcard-shaped name is NOT tracked, and the sibling it used to match still IS.
    """
    import subprocess as sp

    repo = tmp_path / "pathspec"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "ev-axb.md").write_text("real\n", encoding="utf-8")
    git = ["git", "-C", str(repo)]
    sp.run([*git, "init", "-q"], check=True)
    sp.run([*git, "add", "docs/ev-axb.md"], check=True)
    sp.run(
        [
            *git,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    star = repo / "docs" / "ev-a*b.md"
    star.write_text("untracked\n", encoding="utf-8")

    # CONTROL: the bare pathspec really does resolve to the tracked sibling, so this
    # pin cannot pass merely because the fixture never reached the hazard.
    raw = sp.run(
        [*git, "ls-files", "--error-unmatch", "--", "docs/ev-a*b.md"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert raw.returncode == 0 and raw.stdout.strip() == "docs/ev-axb.md", (
        "this pin needs the pathspec to MATCH the tracked sibling, or it tests nothing: "
        f"rc={raw.returncode} stdout={raw.stdout!r} stderr={raw.stderr!r}"
    )

    assert cl._is_tracked(star, repo) is False, (
        "a file in no index must not inherit a tracked sibling's status through its "
        "NAME — that is the fail-open the TRACKED rule exists to refuse"
    )
    ok, why = cl._evidence_is_real("docs/ev-a*b.md", repo_root=repo)
    assert ok is False and "not TRACKED" in why, why

    # The other direction: the file that IS tracked still answers.
    assert cl._is_tracked(repo / "docs" / "ev-axb.md", repo) is True


@pytest.mark.parametrize(
    ("site", "call"),
    [
        ("evidence", lambda: cl._evidence_is_real(REAL_EVIDENCE, REPO_ROOT)),
        ("artifact", lambda: cl._names_artifact(REAL_EVIDENCE, REPO_ROOT, REPO_ROOT)),
        # ⛔ The THIRD `_is_tracked` call site (#11474 review, F3). It was the one
        # arm a fourth answer still reached: `is True … is None … else` filed a
        # truthy non-`True` return under `untracked_seen`, so a reader was told the
        # MEASURED sentence "every file the glob … is untracked by git" about a value
        # git never measured. The guard is applied at all three sites or the counter
        # is wrong about what it counts.
        ("glob", lambda: cl._names_artifact(_GLOB_EVIDENCE, REPO_ROOT, REPO_ROOT)),
        # ⛔ The DIRECTORY arm's fourth-answer guard, the one site this row set did not
        # cover (#11474 review). MEASURED: replacing `if dir_tracked is not True:` with
        # `if False:` left the suite at 320 passed, so deleting that guard — which
        # falls through to `return True, ""`, i.e. ACCEPTS the directory — was
        # invisible. The file arm's three sites were pinned; the sibling probe's was
        # not, which is the same one-probe-over gap the fall-open pin closed.
        ("dir", lambda: cl._names_artifact("tests", REPO_ROOT, REPO_ROOT)),
    ],
    ids=[
        "_evidence_is_real",
        "_names_artifact_bare_path",
        "_names_artifact_glob",
        "_names_artifact_directory_arm",
    ],
)
def test_a_truthy_non_True_TRACKED_answer_is_not_read_as_tracked(site, call, monkeypatch):
    """The FOURTH answer: `is False … is None … return True` ACCEPTED everything else.

    `_is_tracked` is annotated `bool | None`, so `1` / `"yes"` cannot arrive today — and
    that is precisely why the guard is the wrong shape: it reads every future truthy
    return as the measurement "this artifact is in the tree". MEASURED at the pre-fix
    shape, `_is_tracked -> 1` made `_evidence_is_real` answer `(True, '')` AND
    `_names_artifact`'s bare-path arm answer `(True, '')` — both call sites were open,
    so both are pinned, each by its own row (deleting either guard alone reddens only
    its own parameter).

    A refusal, not a crash: the value is NAMED, because "not a measurement" is the
    truth and "not TRACKED by git" would be a cause nobody measured.

    Re-measured at `21c446ab002` for the glob arm (`_names_artifact(_GLOB_EVIDENCE)`):
    reverting the third guard to the shipped `is None … else` shape answers
    `(False, "every file the glob … is untracked by git…")` — a refusal that names a
    cause nobody measured. So that row reddens on its own assertion (`"not a
    measurement" in why`), not on an exit code, and it is fail-closed either way.
    """
    monkeypatch.setattr(cl, "_is_tracked", lambda resolved, root: 1)
    if site == "dir":
        # The DIRECTORY arm reads its own probe; the file one is irrelevant there.
        monkeypatch.setattr(cl, "_is_tracked_dir", lambda resolved, root: 1)
    ok, why = call()
    assert ok is False, f"a truthy non-True answer is not 'tracked' on the {site} arm"
    assert "not a measurement" in why, why
    assert "not TRACKED" not in why, why


# =============================================================================
# `_is_tracked_dir` — the SIBLING probe `#11506` added, carrying both collapsed
# causes this PR removes from `_is_tracked` (#11474 review, merge-order finding).
#
# The reviewer's finding: this branch's base predates `#11506`, so before the merge
# there was one probe; after it there are two, and the divergence is the bug — one
# refuses on a spawn failure, the other ABSTAINS (which counts the row). Nothing
# pinned either half (`grep -c '_is_tracked_dir'` over this file was 0), so both
# were free to regress. The tests below pin the three answers, each with a
# CONTROL so a tool that simply refused every directory could not pass them — plus the
# round-3 parity pin: the two arms must reach the SAME answer on ONE tree (the
# bare/`GIT_DIR` abstaining shape), which is what the divergence was.
# =============================================================================


def _hermetic_dir_repo(tmp_path) -> Path:
    """A throwaway repo where `held/inside.txt` is TRACKED and `empty/` holds nothing."""
    repo = tmp_path / "dirrepo"
    (repo / "held").mkdir(parents=True)
    (repo / "held" / "inside.txt").write_text("held\n", encoding="utf-8")
    (repo / "empty").mkdir()
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "held/inside.txt"], check=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    return repo


def test_an_EMFILE_on_the_DIRECTORY_probe_cannot_manufacture_a_clean_exit(tmp_path, capsys):
    """The directory fork could not RUN. The row must be REFUSED, not counted.

    ⛔ THE FAIL-OPEN THIS PIN EXISTS FOR, verbatim from `#11506`'s arm: `except
    OSError: return _tracked_unprovable()` — and `_tracked_unprovable()` is `True`, so
    `if not _is_tracked_dir(...)` fell through to `return True, ""` and a `verified`
    row citing `cmd:uv run pytest held -v` counted with NOTHING measured. That is the
    same shape as the EMFILE fail-open on `_is_tracked` one function over, reached
    through the guard meant to make it safe; under EMFILE/EAGAIN the `cat-file -e` and
    file-shaped `ls-files` forks can succeed while this one does not.

    ⛔ THE STUB IS SELECTIVE AND ITS FIRING IS ASSERTED. A blanket `OSError` would
    refuse the row for some other path's cause; keying on the ABSENCE of
    `--error-unmatch` (the directory form, as opposed to `_is_tracked`'s) and then
    asserting `fired` is what proves the refusal came from THIS fork — a stub that
    silently stopped matching would leave the fail-open covered by nothing.
    """
    import types

    repo = _hermetic_dir_repo(tmp_path)
    real_run = subprocess.run
    fired: list[str] = []

    def explode(argv, *a, **kw):
        if argv[:2] == ["git", "ls-files"] and "--error-unmatch" not in argv:
            fired.append(" ".join(argv[:2]))
            raise OSError(24, "Too many open files")
        return real_run(argv, *a, **kw)

    mp = pytest.MonkeyPatch()
    mp.setattr(cl, "subprocess", types.SimpleNamespace(run=explode))
    mp.setattr(cl, "_commit_state", lambda sha, repo_root=None: "exists")
    try:
        data = _ledger([_row("A", evidence=["cmd:uv run pytest held -v"])])
        with pytest.raises(cl.InvalidLedger, match="did not answer here"):
            cl.validate(data, repo_root=repo)
        # ⛔ CONTROL INSIDE THE WINDOW: the FILE-shaped probe is untouched here, so a
        # tracked file is still ACCEPTED — this pin is not passing on a tool that
        # refuses everything, and the split between the two forks is what is measured.
        ok, why = cl._evidence_is_real("held/inside.txt", repo)
        assert ok, why
    finally:
        mp.undo()
    assert fired == ["git ls-files"], fired

    # ⛔ THE CAUSE IS PINNED TOO (#11474 review). Measured: replacing this renderer's
    # sentence with the MEASURED one ("… is a directory holding no TRACKED file") —
    # the exact defect its docstring forbids — left the whole suite green, because
    # nothing asserted the text of the line this fork produces. The file arm's twin has
    # carried this assertion since it was added; the directory arm had not.
    err = capsys.readouterr().err
    assert "could not be RUN" in err, (
        f"the per-path line must name the cause it measured (the fork never ran): {err}"
    )
    assert "holding no TRACKED file" not in err, (
        f"the fork never ran, so no line may borrow git's measured answer: {err}"
    )
    # ⛔ AND THE MARKER AND THE QUESTION ARE PINNED ON THIS RENDERER TOO (#11474
    # review). MEASURED: reverting BOTH directory markers to `REFUSED` — and, in a
    # separate run, swapping both directory sentences for the file arm's — left every
    # directory test GREEN, because each assertion read a substring present in both
    # variants. Fix #1's own commit message claimed "one rule, four renderers"; only
    # the file renderer was enforcing it. These four assertions are what make the
    # claim true: the marker names the measurement, and the sentence answers the
    # DIRECTORY question ("about what {rel} holds") rather than the file one.
    assert "UNMEASURED" in err, f"the marker names the measurement, not a fate: {err}"
    assert "REFUSED" not in err, f"no per-path line may announce a verdict: {err}"
    assert "about what" in err and "holds:" in err, (
        f"this renderer reports what the DIRECTORY holds, not the file question: {err}"
    )

    # ⛔ OUTSIDE the patched window: with git working, the SAME directory is
    # ACCEPTED when it holds a tracked file, and refused BY THE MEASURED CAUSE when it
    # does not — so "nothing was measured" is not the answer for either normal case.
    ok, why = cl._evidence_is_real("cmd:uv run pytest held -v", repo)
    assert ok, why
    ok, why = cl._evidence_is_real("cmd:uv run pytest empty -v", repo)
    assert not ok and "holding no TRACKED file" in why, why


def test_an_EMFILE_on_the_DIRECTORY_FOLLOW_UP_probe_also_refuses(tmp_path, capsys):
    """The DIRECTORY arm's SECOND `OSError` site — the twin of the file arm's pin.

    `_is_tracked_dir` spawns `git ls-files`, and then — only when that answered
    nothing — a follow-up `git rev-parse --is-inside-work-tree`. Both spawns can fail,
    and both were folded into the abstention by `#11506`, so reverting only the
    follow-up guard back to `return _tracked_unprovable()` (a `True`, i.e. ACCEPT the
    directory) is a full fail-open. Measured at the head BEFORE this pin: the whole
    suite stayed green under exactly that revert, because the sibling pin's stub keys
    on `--error-unmatch` and therefore only ever made the FIRST fork fail.

    ⛔ The stub is selective in the other direction and its firing is asserted: only
    the `rev-parse` spawn raises, so the refusal cannot be coming from the directory
    `ls-files` fork the sibling test covers.
    """
    import types

    repo = _hermetic_dir_repo(tmp_path)
    fired: list[str] = []

    class _LsFilesRan:
        returncode = 1  # non-matching exit -> the follow-up probe is reached (rc 1 is
        # NOT a "nothing matched" for this form: measured on git 2.43.0, the plain
        # `ls-files -- ':(literal)<rel>'` miss is rc 0, and the sole documented
        # `return 1` belongs to `--error-unmatch`. Any non-zero falls through here.)
        stdout = ""
        stderr = ""

    def boom_on_rev_parse(argv, *a, **kw):
        if argv[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
            fired.append(" ".join(argv[:3]))
            raise OSError(24, "Too many open files")
        return _LsFilesRan()

    mp = pytest.MonkeyPatch()
    mp.setattr(cl, "subprocess", types.SimpleNamespace(run=boom_on_rev_parse))
    mp.setattr(cl, "_commit_state", lambda sha, repo_root=None: "exists")
    try:
        # The helper answers the tri-state directly: `None` is NOTHING measured, and
        # `True` here would be the fail-open (the directory accepted, the row counted).
        assert cl._is_tracked_dir(repo / "held", repo) is None
        assert fired == ["git rev-parse --is-inside-work-tree"], fired
        # …and end to end the row is refused, named by the cause rather than the verdict.
        data = _ledger([_row("A", evidence=["cmd:uv run pytest held -v"])])
        with pytest.raises(cl.InvalidLedger, match="did not answer here"):
            cl.validate(data, repo_root=repo)
    finally:
        mp.undo()

    # This renderer's OWN sentence and marker, not the file arm's (#11474 review):
    # measured, both directory markers could be reverted to `REFUSED` with every
    # directory test green, because no assertion read either one.
    err = capsys.readouterr().err
    assert "could not be RUN" in err, f"the cause that was measured must be named: {err}"
    assert "UNMEASURED" in err, f"the marker names the measurement, not a fate: {err}"
    assert "REFUSED" not in err, f"no per-path line may announce a verdict: {err}"
    assert "about what" in err and "holds:" in err, (
        f"this renderer reports what the DIRECTORY holds: {err}"
    )


def test_a_DIRECTORY_in_the_ABSTAINING_tree_gets_the_FILE_arms_answer(
    tmp_path, monkeypatch, capsys
):
    """⛔ THE PARITY THE DOCSTRING CLAIMED AND THE CODE DID NOT DELIVER (#11474 round 3).

    The abstaining shape was pinned for `_is_tracked` only: `grep -n 'GIT_DIR'` over
    this file found ONE test and it drives the FILE arm. So the two probes judged one
    tree TWO WAYS — the directory arm answered `rc 0 -> bool(stdout)` and `rc 1 ->
    False` from the EXIT CODE alone, without the `rev-parse --is-inside-work-tree` gate
    the file arm requires before either is a measurement.

    ⛔ MEASURED ON GIT 2.43.0 AT THIS PIN'S OWN FIXTURE — asserted below rather than
    quoted, so a future git that changes the pair makes this pin say so instead of
    passing for the wrong reason:

        git ls-files -- ':(literal)held'                            -> rc 0, EMPTY stdout
        git rev-parse --is-inside-work-tree                         -> `false`, rc 0
        git ls-files --error-unmatch -- ':(literal)held/inside.txt' -> rc 1

    In the bare/`GIT_DIR` tree the FILE arm therefore abstains
    (`_tracked_unprovable(probe)` -> `True`, #11503 Option 1, pinned end to end by
    `test_an_ABSTAINING_tree_is_accepted_and_prints_the_NOTE`) while the DIRECTORY arm
    read that same rc 0 as a MEASURED "holds no TRACKED file" and REFUSED
    `cmd:uv run pytest held -v` — the command shape the arm was added for — with a
    cause git never gave: it could not answer about a work tree at all, and a reader's
    clone does hold those files.

    ⛔ NON-VACUITY, MEASURED: reverting the probe gate to the shipped
    `if proc.returncode == 0: return bool(proc.stdout.strip())` reddens the
    `_is_tracked_dir(...) is True` assertion AND `_evidence_is_real` below (the row is
    refused with "holding no TRACKED file").
    """
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    tree = tmp_path / "tree"
    (tree / "held").mkdir(parents=True)
    (tree / "held" / "inside.txt").write_text("held\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(bare))
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root=None: "exists")

    # PREMISE, re-measured rather than quoted: `rc 0` with EMPTY stdout is the hazard.
    dir_form = subprocess.run(
        ["git", "ls-files", "--", ":(literal)held"],
        cwd=str(tree),
        capture_output=True,
        text=True,
        check=False,
    )
    assert dir_form.returncode == 0 and dir_form.stdout == "", (
        "the divergence is `rc 0 with EMPTY stdout` from the directory form; measured "
        f"rc={dir_form.returncode} stdout={dir_form.stdout!r}"
    )
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=str(tree),
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0 and probe.stdout.strip() == "false", (
        f"the probe must ANSWER `false`, not fail: rc={probe.returncode} {probe.stdout!r}"
    )

    # PARITY, on the SAME tree: both arms must reach the abstention.
    assert cl._is_tracked(tree / "held" / "inside.txt", tree) is True
    assert cl._is_tracked_dir(tree / "held", tree) is True, (
        "git answered `false` to the work-tree probe here, so NOTHING was measured "
        "about what this directory holds — the directory arm may not answer the "
        "measured `False` its sibling refuses to answer on the same tree"
    )

    # And the ROW consequence, which is what the pair is FOR. The row carries a PATH
    # as well as the command: a `cmd:`-only row can never reach `verified` (T8), so a
    # `cmd:`-only row would refuse here for a reason that has nothing to do with this
    # pin.
    ok, why = cl._evidence_is_real("cmd:uv run pytest held -v", tree)
    assert ok is True, why
    row = _row("A", evidence=["held/inside.txt", "cmd:uv run pytest held -v"])
    cl.validate(_ledger([row]), repo_root=tree)
    err = capsys.readouterr().err
    assert "git is present and says this is not a work tree" in err, (
        f"the NOTE must state the cause the probe DID measure: {err}"
    )
    assert "holding no TRACKED file" not in err, (
        "git never got to answer about a work tree, so no line may claim it measured "
        f"this directory as holding no TRACKED file: {err}"
    )


def test_a_FAILED_ls_files_on_a_DIRECTORY_is_not_the_measured_no(monkeypatch, capsys):
    """Any non-zero `ls-files` on a directory is a FAILURE to answer, NOT the measured `no`.

    ⛔ THE CAUSE HALF of the finding, measured shape: with a corrupt `GIT_INDEX_FILE`,
    `git ls-files -- tests/` exits 128 while `rev-parse --is-inside-work-tree` still
    answers `true` rc 0 — so the shipped `if probe…: return False` told the reader
    *"is a directory holding no TRACKED file, so it holds nothing a reader's clone
    has"*, a cause git never gave. Same third collapsed cause `_tracked_unanswered`
    exists for, one probe over.

    ⛔ **rc 1 IS NOT AN ANSWER EITHER, AND THIS PIN USED TO CLAIM IT WAS (#11474
    review).** It asserted `rc 1 -> measured False` on the strength of *"`git ls-files`'
    documented 'nothing matched' exit is 1"*. The manual does not say that, and this was
    verified against the primary source before the pin was changed:

        $ man git-ls-files | grep -n 'return 1'
        111:         (return 1).          # and line 109 puts it under --error-unmatch

    `--error-unmatch` is the FILE arm's form (`_is_tracked`), not this one. MEASURED on
    git 2.43.0 against a temp repo: this form (`git ls-files -- ':(literal)nope'`, no
    `--error-unmatch`) exits **0** with empty stdout, while the same pathspec under
    `--error-unmatch` exits 1. So rc 1 carries no documented meaning here and blaming
    it as "holds no TRACKED file" prints a cause git never gave. Both exits still
    REFUSE the row (fail-closed either way) — what changes is the sentence, which now
    names the measurement instead of an inference.
    """
    import types

    class _Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def run_with(rc):
        def fake(argv, *a, **kw):
            if argv[:2] == ["git", "ls-files"]:
                return _Result(rc, "", "fatal: index file smaller than expected")
            if argv[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
                return _Result(0, "true\n")
            raise AssertionError(f"unexpected probe: {argv}")

        return fake

    # --- exit 128: git is here and did not answer for the directory -----------
    monkeypatch.setattr(cl, "subprocess", types.SimpleNamespace(run=run_with(128)))
    ok, why = cl._names_artifact("tests", REPO_ROOT, REPO_ROOT)
    assert ok is False, why
    assert "did not answer here" in why, why
    assert "holding no TRACKED file" not in why, (
        f"git exited 128 — it never answered `no`, so the measured sentence may not "
        f"be printed: {why}"
    )
    err = capsys.readouterr().err
    assert "exited 128" in err, f"the per-path line must name the measurement it took: {err}"
    # The DIRECTORY renderer's marker and question, which nothing asserted (#11474
    # review): measured, reverting both directory markers to `REFUSED` and, separately,
    # swapping both directory sentences for the file arm's left this test green.
    assert "UNMEASURED" in err, f"the marker names the measurement, not a fate: {err}"
    assert "REFUSED" not in err, f"no per-path line may announce a verdict: {err}"
    assert "about what" in err and "holds:" in err, (
        f"this renderer reports what the DIRECTORY holds, not the file question: {err}"
    )

    # --- exit 1: NOT a documented answer for this form, so it may not borrow the
    # measured sentence either (measured 2026-11-08, git 2.43.0: this form's miss is
    # rc 0; the ONLY documented `return 1` belongs to `--error-unmatch`).
    monkeypatch.setattr(cl, "subprocess", types.SimpleNamespace(run=run_with(1)))
    assert cl._is_tracked_dir(REPO_ROOT / "tests", REPO_ROOT) is None
    ok, why = cl._names_artifact("tests", REPO_ROOT, REPO_ROOT)
    assert ok is False and "did not answer here" in why, why
    assert "holding no TRACKED file" not in why, why
    err = capsys.readouterr().err
    assert "exited 1" in err, f"the per-path line must name the measurement it took: {err}"

    # --- exit 129 (a usage error, e.g. pre-2.11 `:(literal)`): unanswered, not `no`
    monkeypatch.setattr(cl, "subprocess", types.SimpleNamespace(run=run_with(129)))
    assert cl._is_tracked_dir(REPO_ROOT / "tests", REPO_ROOT) is None
    capsys.readouterr()


def test_a_directory_name_carrying_a_WILDCARD_cannot_borrow_a_siblings_match(tmp_path):
    """`-- <rel>` is a PATHSPEC here too, and that direction was LIVE on the dir probe.

    ⛔ MEASURED on git 2.43.0 before the fix: with `docs/ev-axb/f.md` TRACKED and a
    real, untracked directory literally named `docs/ev-*`, the probe ran
    `git ls-files -- 'docs/ev-*'`, which printed `docs/ev-axb/f.md` and exited 0 — so
    `_is_tracked_dir` answered "a reader's clone holds a TRACKED file under it" for a
    directory no clone holds anything under, borrowing a SIBLING's match. That is the
    same fail-open this PR closes for the file probe (`docs/ev-a*b.md` borrowing
    `docs/ev-axb.md`), one probe over; `:(literal)` makes the name a name (the same
    call then answers nothing, rc 0).

    A name carrying `*` needs no privilege — it is a legal POSIX directory name — and
    `Path.resolve()` does not glob, so the tool really does ask git about that name.

    ⛔ SCOPED HONESTLY: this hazard is LATENT end to end, not live through
    `_names_artifact` — that arm routes any token carrying a wildcard to the GLOB arm
    first, which refuses it (asserted below), so no record today reaches the directory
    probe with a `*` in the name. What this pin covers is the SEAM: `_is_tracked_dir`
    is a function any future caller can pass a resolved directory to, and before the
    fix it answered `True` for one that holds nothing. `:(literal)` is what makes that
    answer independent of what the name looks like.
    """
    repo = tmp_path / "globrepo"
    (repo / "docs" / "ev-axb").mkdir(parents=True)
    (repo / "docs" / "ev-axb" / "f.md").write_text("held\n", encoding="utf-8")
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "docs/ev-axb/f.md"], check=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-q",
            "-m",
            "seed",
        ],
        check=True,
    )
    decoy = repo / "docs" / "ev-*"
    decoy.mkdir()
    (decoy / "inner.txt").write_text("nothing tracked under here\n", encoding="utf-8")

    # CONTROL: the tracked sibling IS accepted, so a probe that refused every
    # directory could not pass this test.
    assert cl._is_tracked_dir(repo / "docs" / "ev-axb", repo) is True
    # The decoy holds nothing tracked and may not borrow the sibling's match.
    assert cl._is_tracked_dir(decoy, repo) is False
    # …and it cannot do so by any spelling: a relative token for the decoy is refused
    # too, by the GLOB arm (whose precedence over the directory arm is why the borrow
    # was never reachable through a record).
    ok, why = cl._names_artifact("docs/ev-*", repo, repo)
    assert ok is False, f"an untracked dir borrowed its sibling's match: {why}"
    ok, why = cl._names_artifact(str(decoy), repo, repo)
    assert ok is False, why
