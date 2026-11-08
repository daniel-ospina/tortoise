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
import os
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


def test_usage_outcomes_never_borrow_a_verdict_code(tmp_path):
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
        'cmd:A="I did not run this" && uv run pytest '
        "tests/test_9664_capstone_verdict_ledger.py -q"
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
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, command])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{command!r} was REFUSED ({proc.returncode}) — bash accepts it (rc 0)\n"
            f"{proc.stdout}{proc.stderr}"
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


def test_an_attached_script_body_is_graded_whatever_it_contains(tmp_path):
    """An attached `-cBODY`/`--eval=BODY` is a SCRIPT, not prose — always.

    ⛔ MEASURED false PASS (fifth review cycle, P1). The whitespace deferral ran
    BEFORE the `_split_flag` grading, so every body that carried a SENTENCE was
    parked as `unread_prose` and `continue`d — the grading was never reached for
    exactly the bodies it exists for. With a held artifact token after the entry the
    parked prose was then CLEARED by `_names_artifact`, and:

        cmd:bash -c'NOT RUN - see tools/capstone_ledger.py' tests/…py

    reported `verified: 1 uncovered: 0` / `CAPSTONE-LEDGER-RESULT: CLEAN` / exit 0,
    while the SAME value WITHOUT the trailing path was refused. Bash proves the
    command cannot have run: `bash -c "bash -c'NOT RUN - see x' y"` answers
    `-N: invalid option`, rc 2.

    ⛔ AND THE FIX MUST NOT BUY IT WITH A FALSE BLOCK. Routing every whitespace
    body through `_split_flag` only works because THAT function now stops scanning
    at the end of the leading flag cluster; while it scanned the whole token it split
    `-c'uv run pytest tools/capstone_ledger.py'` on the `e` of "ledger", graded
    `'r.py'` as the script and REFUSED the legitimate record. Both directions are
    pinned here so neither can be spelled as the other's bypass.
    """
    for entry in (
        "cmd:bash -c'NOT RUN - see tools/capstone_ledger.py' "
        "tests/test_9664_capstone_verdict_ledger.py",
        'cmd:bash --eval="NOT RUN - see tools/capstone_ledger.py" '
        "tests/test_9664_capstone_verdict_ledger.py",
        "cmd:bash -e'NOT RUN - see tools/capstone_ledger.py' "
        "tests/test_9664_capstone_verdict_ledger.py",
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
        assert proc.returncode == cl.EXIT_INVALID, (
            f"{entry!r} was ACCEPTED ({proc.returncode}) — an attached script body is "
            f"graded as a script, so a disclaimer in it cannot carry the verdict\n"
            f"{proc.stdout}{proc.stderr}"
        )
    # The same spelling, with a body that really names an artifact, must be ACCEPTED.
    for entry in (
        "cmd:bash -c'uv run pytest tools/capstone_ledger.py'",
        'cmd:bash -c"uv run pytest tools/capstone_ledger.py"',
    ):
        proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[REAL_EVIDENCE, entry])]))
        assert proc.returncode == cl.EXIT_CLEAN, (
            f"{entry!r} was REFUSED ({proc.returncode}) — an attached body that names "
            f"an artifact is a legitimate record, and refusing it is the false block "
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

    ⛔ CORRECTED CLAIM (fourth review cycle): these are REFUSED, and for TWO
    different reasons that must not be conflated. A body containing whitespace is
    caught as an unread quoted token BEFORE `_split_flag` runs, and refusing it is
    right — real bash parses `bash -c'echo F'` as an invalid option cluster and
    "echo F" never runs, so grading the body would ACCEPT a record for a command
    that cannot have executed. An attached body with NO whitespace is graded as a
    script. Both end in a refusal for prose, which is the property that matters.

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


def test_a_checkout_without_git_abstains_AND_PRINTS_IT(monkeypatch, capsys):
    """No `git` on PATH is an abstention with a NOTE, never a traceback and never a red.

    Measured on the mutant that removes the `except OSError` arm: a
    `FileNotFoundError` escaped `validate()` with no `CAPSTONE-LEDGER: INVALID`
    marker, so a reader keying on the marker got nothing at all. And the abstention
    must be PRINTED, because `_tracked_unprovable -> False` turns it into a false red
    on a legitimate ledger in any git-less checkout (a tarball, a build context).
    Both directions are asserted here, so either revert reds this test.
    """
    import types

    def _boom(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(cl, "subprocess", types.SimpleNamespace(run=_boom))
    monkeypatch.setattr(cl, "_commit_state", lambda sha, repo_root: "exists")
    assert [row["id"] for row in cl.validate(_ledger([_row("A")]))] == ["A"]
    assert "git is unavailable here" in capsys.readouterr().err


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
    """
    repo = tmp_path / "treerepo"
    repo.mkdir()
    (repo / "committed.txt").write_text("held\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("not in any clone\n", encoding="utf-8")
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "committed.txt"], check=True)
    subprocess.run(
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
