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
    "prose",
    [
        "cmd:done",
        "cmd:I verified this by hand, screenshots are in the issue",
        "cmd:uv run pytest",
        "cmd:cat /etc/hostname",
    ],
)
def test_a_cmd_entry_must_name_an_artifact_of_this_tree(tmp_path, prose):
    """`cmd:` used to accept ANY non-empty string, so `cmd:done` satisfied `verified`.

    One word — or a sentence explaining that the work was done — defeated the
    strongest verdict in the vocabulary and turned the uncovered list empty.
    A command entry must name the script or test file it ran.
    """
    proc = _run_cli(tmp_path, _ledger([_row("A", evidence=[prose])]))
    assert proc.returncode == cl.EXIT_INVALID, proc.stdout + proc.stderr
    # Two arms name the same defect: a one-word entry is not a command at all,
    # a sentence-shaped one simply names no file. Both are refusals.
    assert "`cmd:` evidence" in proc.stderr


def test_a_cmd_entry_that_names_a_real_script_is_accepted(tmp_path):
    """The rule must not be so tight that the committed ledger's own rows fail."""
    proc = _run_cli(
        tmp_path,
        _ledger([_row("A", evidence=["cmd:uv run python tools/test_lane.py run -- x -q"])]),
    )
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


def test_a_sha_that_names_no_commit_is_invalid(tmp_path):
    """Shape is not existence: a fabricated 40-hex sha rendered as provenance.

    ⛔ Exercised IN-PROCESS with the shallow probe forced OFF, because a depth-1
    checkout — the CI `test` job (`python-ci.yml:489`) and this box — cannot tell a
    fabricated sha from an ancestor the clone does not hold. Forcing the arm is what
    keeps it REACHABLE from every checkout instead of silently never running on the
    boxes that matter (agent-infra#3442 rule 3; #11217).
    """
    data = _ledger([_row("A")])
    data["measured_at"]["sha"] = "deadbeef" * 5
    original = cl._is_shallow
    cl._is_shallow = lambda: False
    try:
        with pytest.raises(cl.InvalidLedger) as excinfo:
            cl.validate(data)
    finally:
        cl._is_shallow = original
    assert "names no commit" in str(excinfo.value)


def test_a_shallow_checkout_ABSTAINS_on_a_sha_it_cannot_resolve(tmp_path):
    """A shallow clone holds no ancestor, so absence is NOT evidence — abstain.

    The counterpart of the arm above, and why 22 committed-ledger rows stopped going
    red on every CI shard: the shape of the CLONE is not a defect in the record. The
    residual is stated rather than hidden — in a shallow checkout a genuinely
    fabricated sha is not caught, which is the trade `python-ci.yml:489` already made
    for the gate it describes as reporting UNPROVABLE.
    """
    data = _ledger([_row("A")])
    data["measured_at"]["sha"] = "deadbeef" * 5
    original = cl._is_shallow
    cl._is_shallow = lambda: True
    try:
        try:
            cl.validate(data)
        except cl.InvalidLedger as exc:
            assert "names no commit" not in str(exc), (
                "a shallow checkout must ABSTAIN rather than red the record for "
                "history it does not hold: " + str(exc)
            )
    finally:
        cl._is_shallow = original


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
