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
    assert "must START with a program that runs something" in proc.stderr, proc.stderr


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


def test_a_cmd_target_that_is_not_a_regular_file_is_invalid(tmp_path):
    """T6 in the `cmd:` arm via a BARE PATH — the member that had no pin.

    The glob half is pinned (`test_a_glob_whose_matches_are_all_directories_…`),
    but `_names_artifact`'s `is_file()` is a SEPARATE clause for bare paths, and
    nothing pinned it: changing it to `exists()` left the whole suite green while
    a one-row `verified` ledger citing `cmd:sh docs` exited **0** with
    `UNCOVERED LIST: empty — the only clean exit` — a directory (or a FIFO) counted
    as held evidence. That is the false PASS this artifact exists to refuse.
    """
    tree = tmp_path / "tree"
    (tree / "docs" / "sub").mkdir(parents=True)
    os.mkfifo(tree / "pipe")

    for entry in ("cmd:sh docs", "cmd:sh pipe"):
        with pytest.raises(cl.InvalidLedger):
            cl.validate(_ledger([_row("A", evidence=[entry])]), repo_root=tree)

    # End to end: the empty-uncovered-list shape must not be reachable at all.
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=["cmd:sh docs"])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    assert "UNCOVERED LIST" not in proc.stdout, proc.stdout


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
        assert "must START with a program that runs something" in proc.stderr, proc.stderr


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
    """Honouring `cd` must not let a record walk the check out of the checkout."""
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
    and BOTH are f-string shaped — so everything after the `ast.JoinedStr` early
    return had no coverage at all, and the fail-closed `None` (UNPROVEN) return
    could be mutated to `1` with the whole file GREEN. Measured: that mutation
    left `assert status is not None` untouched, because no pinned guard can reach
    it. A table is the only way to execute those arms; the resolver's docstring
    already asserts this table, so it is now executed rather than described.

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


def _advertised_bare_forms(rel: str, lines, census) -> list[str]:
    """The census's advertised-form predicate, applied to `lines` (#5128).

    Factored OUT of the sweep so the predicate itself can be executed (see
    `test_the_advertised_form_filter_is_not_deaf`). Inline, a filter that had lost
    its condition (`if False and (…)` — the check deleted, the assertion kept) left
    the whole suite GREEN, because the two pinned tools genuinely advertise no bare
    form: "no offenders in this file" and "this filter flags nothing" are the same
    observation until something feeds it a line that SHOULD be flagged, and the
    only thing that does is a tool that already has an offender.
    """
    return [
        f"{rel}:{n}: {line.strip()}"
        for n, line in enumerate(lines, 1)
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


@pytest.mark.parametrize("tool", PINNED_TOOLS)
def _preamble_index(body: list[ast.stmt]) -> int | None:
    """Index of the `#5128` guard in a tool's module body, or None (#9664).

    The census's `_guard_index` walk, and nothing more: skip EXACTLY ONE module
    docstring, then ZERO OR MORE `__future__` imports, then exactly ONE **bare**
    `import sys`, then the `if sys.version_info < (3, 12)` guard.

    Requiring a `__future__` import would red a census-LEGAL tool — measured, 3 of
    185 tracked `tools/*.py` carry none as their first post-docstring statement —
    and skipping every leading `ast.Expr` is wrong too: a second bare string literal
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
    """
    assert _preamble_index(list(ast.parse(source).body)) == expected, source


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

    DECLARED BOUND — what this pin does NOT mirror, and why that is not a hole:
    it mirrors the census's arms over the TWO TOOLS' OWN FILES (preamble order,
    floor, guard shape, inertness at the floor, both refusal shapes, the RENDERED
    message, the derived process status, the census row that status must match, and
    the advertised-invocation sweep driven by the census's own `_BARE_INVOCATION` +
    exemption sets), PLUS one arm the census does carry but this pin can only carry
    for one tool: the invocation the `tools/build_site_icons.py` refusal names
    RESOLVING (its `--source` is `required=True`, so only the `--help` form it names
    resolves). ⛔ NOTHING here grades what `tools/capstone_ledger.py`'s refusal
    NAMES — the census has no such arm for it either, so that dimension is ungraded
    for that tool on a tool-only diff; measured: mutating its message to name
    `--help` (exit 5, no verdict) or `--ledger NONEXISTENT` leaves this file
    completely GREEN — no arm notices. (Deliberately no count: a number here went
    stale three rounds running as arms were added.) It does not mirror the
    census's `docs/` + `graph-scripts/` + sibling-`tools/` walk, nor its corpus
    vacuity, `RUNTIME_39`/`UNGUARDABLE` accuracy, detector self-test or `-m`
    resolution arms: reaching any of those needs an edit to `docs/`, to another
    tool, or to the census file itself — and `tests/` edits select 788 files
    INCLUDING the census, so the census itself would then run. This bound is
    declared rather than implied because a pin that silently claims parity is how
    each of six review rounds found it weaker than the census by one more
    dimension.
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
    # ★ An `else:` clause is a SILENT NO-OP, and a worse fail-open than a bad
    # message: `if old: raise … else: raise SystemExit(0)` passes every assertion
    # about the body, and then the tool does NOTHING at all on the interpreter the
    # guard is meant to pass — no work, no message, exit 0. Measured: the pin was
    # green and the tool ran `exit 0` with no output. The census reds it because its
    # floor probe raises out of the guard; this pin is the only gate on a
    # `tools/`-only diff, so it must red it too.
    assert not guard.orelse, (
        f"the guard must carry NO `else` clause: it would run at the floor, on the "
        f"interpreter the guard exists to PASS — measured, `else: raise "
        f"SystemExit(0)` makes the tool a silent no-op with a green pin: "
        f"{[type(n).__name__ for n in guard.orelse]}"
    )
    assert _run_guard(guard, monkeypatch, (3, 12, 0, "final", 0)) == "", (
        "the guard must be INERT at the floor — it refused on a 3.12 interpreter"
    )
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
    assert expected_status is not None, (
        f"the census pins no `REFUSAL_CODES` row for `tools/{tool.name}`, so this "
        f"pin has nothing to compare the refusal's status against and would accept "
        f"ANY non-zero one. Every pinned tool must carry a row "
        f"(`tests/test_entry_point_python_guard.py::REFUSAL_CODES`): without it the "
        f"census does not grade this tool by RUNNING it either, so the exit status "
        f"is pinned nowhere. Add the row, and its `REFUSAL_WHY` reason."
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

    rendered = "".join(
        n.value
        for n in ast.walk(message)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    )
    # The message must name the floor, the file, and an invocation at all. ⛔ This
    # clause is a PREFIX test only — `uv run python tools/<name>` is a prefix of
    # BOTH forms — so it cannot distinguish the form that resolves from one that
    # does not. The polarity differs per tool and is stated rather than generalised:
    # `tools/build_site_icons.py`'s bare path is argparse's usage error (exit 2) and
    # its `--help` form resolves (exit 0); `tools/capstone_ledger.py`'s NAMED form
    # IS the bare path, which resolves to a computed verdict (exit 2 =
    # `EXIT_UNCOVERED`), while its `--help` exits 5 with no verdict. The WORKING
    # claim is proved by executing it, in
    # `test_the_site_icon_refusal_names_the_form_that_actually_RESOLVES`. Measured:
    # reverting the site-icon message to the bare path left this file GREEN while
    # the census reded, which is why the proof lives in its own arm and not here.
    #
    # The subject is asserted as ONE clause, not as a bare `"<tool name>" in
    # rendered` substring: the invocation clause below already contains that
    # basename, so a substring test would pass on a refusal that named a
    # DIFFERENT tool in its subject line.
    assert f"tools/{tool.name} requires Python >= 3.12" in rendered, (
        f"the refusal must name the file and the floor in its subject; renders as "
        f"{rendered!r} (message node: {ast.dump(message)})"
    )
    assert f"uv run python tools/{tool.name}" in rendered, (
        f"the refusal must name an invocation at all (PREFIX only — whether it "
        f"RESOLVES is proved in "
        f"test_the_site_icon_refusal_names_the_form_that_actually_RESOLVES, and "
        f"only for tools/build_site_icons.py); renders as {rendered!r} "
        f"(message node: {ast.dump(message)})"
    )

    # The census also requires the refusal to name the interpreter it GOT — and
    # this is asserted BEHAVIOURALLY, because a structural check cannot see the
    # ORDER: `(got {version_info[1]}.{version_info[0]})` renders `(got 9.3)` and
    # reds the census while satisfying any set-of-subscripts test.
    said = _refusal_text(guard, capsys, monkeypatch, (3, 9, 6, "final", 0))
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
    offenders = _advertised_bare_forms(rel, text.splitlines(), census)
    assert not offenders, (
        f"the tool advertises `python3 tools/{tool.name}`, the form its guard "
        f"REFUSES; the census's "
        f"`test_no_doc_or_usage_advertises_the_form_the_guard_refuses` reds on "
        f"it, and that test does not run on a tool-only diff: {offenders}"
    )


def test_the_site_icon_refusal_names_the_form_that_actually_RESOLVES(
    capsys, monkeypatch
):
    """The census's #11507 arm, mirrored — because the census's copy does not run here.

    `tests/test_entry_point_python_guard.py::
    test_the_site_icon_guard_names_an_invocation_that_actually_resolves` proves the
    invocation the refusal NAMES works, and it is the only gate that does one
    class of claim: the prefix `uv run python tools/<name>` is satisfied by the
    bare path AND by the `--help` form, so a prefix test cannot see the
    regression. A `tools/build_site_icons.py`-only diff selects 82 test files
    INCLUDING this one and EXCLUDING the census, so on the diff that can
    introduce the regression this file is the whole gate.

    Measured at the head that added this test: reverting the tool's message to the
    pre-#11507 bare path — the exact SECOND unattributed error #5128 was filed for
    — left this file GREEN (2 passed on the tool's arm) while the census reded on
    its own copy. The pin was strictly weaker than the gate it claims to stand in
    for, on precisely the diff where it is the only one that runs.

    Proven rather than read off the string: `--source` is `required=True`, so the
    bare path is argparse's own usage error and only the named form resolves.
    """
    path = REPO_ROOT / "tools" / "build_site_icons.py"
    guard = _guard_if(ast.parse(path.read_text(encoding="utf-8")))
    assert guard is not None, f"{path} carries no #5128 interpreter guard"
    said = _refusal_text(guard, capsys, monkeypatch, (3, 9, 6, "final", 0))
    assert "tools/build_site_icons.py --help" in said, (
        "the refusal must name the `--help` form, which needs no `--source` "
        f"token: {said!r}"
    )
    resolved = subprocess.run(
        [sys.executable, str(path), "--help"], capture_output=True, text=True
    )
    assert resolved.returncode == 0, (
        "the invocation the refusal names must RESOLVE; it exited "
        f"{resolved.returncode}: {resolved.stderr[-400:]!r}"
    )
    bare = subprocess.run([sys.executable, str(path)], capture_output=True, text=True)
    assert bare.returncode == 2, (
        "the bare path must stay a USAGE error (argparse, `--source` is required) — "
        "if it stops being one, this guard's advice is no longer the only form "
        f"that resolves: rc={bare.returncode}, {bare.stderr[-300:]!r}"
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
    assert _advertised_bare_forms(rel, [f"run `python3 {rel} check`"], census), (
        "the filter flags NO offender — the sweep is deaf, and a tool advertising the "
        "refused form would pass it"
    )
    assert not _advertised_bare_forms(rel, [f"run `uv run python {rel} check`"], census), (
        "the filter flags a COMPLIANT invocation — every pinned tool would red"
    )
    carve = sorted(census._CARVE_OUTS)[0]
    assert not _advertised_bare_forms(rel, [f"`python3 {carve}`"], census), (
        f"a census carve-out ({carve}) must stay exempt — the exemptions are the "
        f"census's data, not a re-derived filter"
    )
    assert _advertised_bare_forms(rel, ["`python3 ...`"], census), (
        "the ELIDED form has no path, so no carve-out can exempt it; the census reds "
        "it and the filter must too"
    )
    # The RECORD exemption is a third clause, and it is executed by nothing else:
    # both pinned tools carry no `_DELIBERATE_BARE_FORM` line, so deleting the
    # clause left the suite GREEN (measured). It is keyed by (path, stripped line),
    # so the census's own first entry is the only honest subject.
    record_rel, record_line = sorted(census._DELIBERATE_BARE_FORM)[0]
    assert not _advertised_bare_forms(record_rel, [record_line], census), (
        f"a census RECORD exemption must stay exempt ({record_rel!r}): rewriting "
        f"recorded evidence to satisfy a gate falsifies provenance, which is why the "
        f"census exempts it"
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
