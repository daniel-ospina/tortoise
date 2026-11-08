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
    assert "could not be run here" in msg, msg
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
        with pytest.raises(cl.InvalidLedger, match="could not be run here"):
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

    `_tracked_unprovable()` is a MEASURED answer ("git says this is not a work tree");
    `_tracked_unrunnable()` is NOTHING answered. Collapsing them is the fail-open, so
    this asserts they are distinct at the function level, not just end to end.
    """
    assert cl._tracked_unrunnable(OSError(24, "Too many open files"), "x") is None
    assert cl._tracked_unprovable() is True


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
    assert "could not be run here" in why, why
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
    this test reddens on its MESSAGE assertion (`"could not be run here" in why`)
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
    assert "could not be run here" in why, why
    assert "not TRACKED" not in why, why


def test_a_cmd_target_behind_an_UNRUNNABLE_probe_is_REFUSED_end_to_end(monkeypatch):
    """The same arm's USER-VISIBLE consequence: the record may not exit CLEAN.

    The seam pin above drives `_names_artifact` directly; this one pins what the
    RECORD does with the answer — a one-row `verified` ledger citing a `cmd:` target
    whose TRACKED probe never ran is INVALID. ⛔ MEASURED AT `21c446ab002`: with the
    arm reverted (`if tracked is None:` -> `if False:`) the row is **still INVALID**
    — the fourth-answer guard later in the SAME arm refuses it with "the TRACKED
    check answered None, which is not a measurement that it is in the tree" — so
    this test reddens on its message assertion (`"TRACKED check could not be run
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
    assert "TRACKED check could not be run here" in message, message
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
    assert "could not be run here" in why, why
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
    assert "REFUSED" in err, "the per-path refusal must still be PRINTED, not silent"
    assert "the row does not count" not in err, (
        "the probe cannot know the row's fate — a later tracked match accepted it, so "
        f"the per-path line must not claim the row was refused: {err}"
    )


def test_an_ABSTAINING_tree_is_accepted_and_prints_the_NOTE(tmp_path, monkeypatch, capsys):
    """The `return _tracked_unprovable()` CALL SITE, end to end — it had no pin at all.

    `test_the_unrunnable_probe_path_is_not_the_abstention_path` asserts the HELPER's
    return value (`_tracked_unprovable() is True`) and nothing asserted that
    `_is_tracked` still RETURNS it: MEASURED at this head, reverting that call site to
    `return False` left the suite green at **173 passed**, so the whole abstention arm
    could be deleted as dead code and no row would notice (#11474 review).

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
    assert "git is unavailable here" in err, err
    assert "not TRACKED" not in err, (
        "git answered (it is present and said 'not a work tree'), so no line may claim "
        f"it measured this path as untracked: {err}"
    )


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
    ],
    ids=["_evidence_is_real", "_names_artifact_bare_path", "_names_artifact_glob"],
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
    ok, why = call()
    assert ok is False, f"a truthy non-True answer is not 'tracked' on the {site} arm"
    assert "not a measurement" in why, why
    assert "not TRACKED" not in why, why
