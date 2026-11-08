"""#10951 — the queue CONSUMER must consult the fleet's work-unit rule, not just the board.

WHY THIS FILE EXISTS
--------------------
The fleet decides "what is a unit of work" with ONE predicate: `POOL_JQ` in
agent-infra's `refresh-candidates.sh`. The issue that added it (#10951) is what
happens when that answer exists in more than one place: four inline copies with
two meanings, the narrowest one on the P1 tier that actually decides a lane's next
unit — so `epic:`, `design(…)`, `coord:`, `measure(…)` and tortoise#10175 ("the
hosted capture cap is HELD deliberately — never override it") were ranked as
work units and handed a runnable `dispatch-merge.sh … 10175`.

The BOARD half landed (agent-infra#3467, `e43f2f4f6`). The CONSUMER half —
`tools/next_work.py` — had **no rule at all**: `_search_issues` projected rows to
`{number,title,priority}`, so `labels` and `assignees` were discarded at
collection; `Unit` had no label field; `pickable` consulted only `duplicate_of`
and `reason`. Measured over one live snapshot: the board withheld 43 of 229
tortoise rows and the consumer withheld **0** — the two surfaces disagreed about
every single container.

WHAT IS PINNED HERE
-------------------
(a) the motivating row (#10175) is withheld, and its reason names the DECLARATION
    (`standing-constraint`) rather than inferring anything from its prose;
(b) EVERY container spelling the repo actually emits is withheld, and every
    spelling that is REAL WORK stays pickable — including the bare-word collisions
    (`Measure …`, `lane-actions: …`) that a prefix regex swallows;
(c) the two surfaces agree: over ONE fixture population, the consumer withholds
    exactly the set the shipped program withholds;
(d) a missing `labels`/`assignees` key is not an exclusion, and a plain-string
    label (the natural hand-written form) is coerced, not fatal — the shipped jq
    arm raises `Cannot index string with string "name"` on it;
(e) an UNREADABLE rule withholds every row (POP-UNMEASURED) and says so — it never
    resolves to "every row is a unit", which is this issue's defect reached by
    accident;
(f) the off-switch (`NEXT_WORK_POPULATION=0`) restores the pre-#10951 behaviour
    EXACTLY, and is reported as disarmed rather than passing for a measurement;
(g) a replayed snapshot that carries no verdicts is INCOMPLETE, not a pass.

NO NETWORK, NO `gh`, NO AGENT-INFRA CHECKOUT: the rule is supplied as bytes
(`tests/fixtures/10951/pool-rule.sh`, a stated mirror) through the same env
override the production path honours, and the mirror is asserted equal to the
shipped line whenever that line is resolvable on the box.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import next_work
from tools.next_work import (
    REASON_ASSIGNED,
    REASON_CONTAINER,
    REASON_DECLARED,
    REASON_NOT_A_UNIT,
    REASON_POP_UNMEASURED,
    Snapshot,
    _label_objects,
    _pool_jq,
    _population_program,
    _population_script,
    _population_verdicts,
    _top_level_stages,
    build_units,
    next_command,
)
from tools.queue_resweep import parse_config

REPO = "daniel-ospina/tortoise"

#: The gate the READY-TO-LAND premise needs, with the live label vocabulary.
CONFIG_TEXT = """
queue_rules:
  - name: default
    queue_conditions:
      - base=main
      - -draft
      - -label=superseded-by-fold
      - check-success=docs
      - check-success=test-isolation
"""

CFG = parse_config(CONFIG_TEXT)
HERE = Path(__file__).resolve().parent
FIXTURE_RULE = HERE / "fixtures" / "10951" / "pool-rule.sh"
REPO_ROOT = HERE.parent

pytestmark = pytest.mark.skipif(
    shutil.which("jq") is None,
    reason="the shipped rule is a jq PROGRAM: without jq there are no bytes to drive",
)


# ── helpers ────────────────────────────────────────────────────────────────
def issue(
    n: int,
    title: str,
    *,
    labels: list | None = None,
    assignees: list | None = None,
    priority: str = "P1-severe",
) -> dict:
    row: dict = {"number": n, "title": title, "priority": priority}
    if labels is not None:
        row["labels"] = labels
    if assignees is not None:
        row["assignees"] = assignees
    return row


def snap_with(rows: list[dict], monkeypatch, rule: Path | None = None) -> Snapshot:
    """A snapshot whose eligibility verdicts came from the SUPPLIED rule bytes."""
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(rule or FIXTURE_RULE))
    monkeypatch.delenv("NEXT_WORK_POPULATION", raising=False)
    monkeypatch.delenv("AGENT_INFRA_PATH", raising=False)
    snap = Snapshot(issues=rows)
    _population_verdicts(REPO_ROOT, snap)
    return snap


def units_by_issue(snap: Snapshot) -> dict[int, next_work.Unit]:
    return {int(u.issue): u for u in build_units(snap, None) if u.issue is not None}


def run_shipped_program(rows: list[dict], rule: Path | None = None) -> set[int]:
    """The rule's own verdict, computed WITHOUT this tool — the independent side."""
    prog = _pool_jq(str(rule or FIXTURE_RULE))
    payload = [
        {
            "number": r["number"],
            "title": r.get("title") or "",
            "labels": _label_objects(r.get("labels")),
            "assignees": r.get("assignees") or [],
        }
        for r in rows
    ]
    wrapped = "reduce .[] as $r ([]; . + [{n: $r.number, kept: ([$r | " + prog + "]|length)}])"
    out = subprocess.run(
        ["jq", "-c", wrapped], input=json.dumps(payload), capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    return {int(v["n"]) for v in json.loads(out.stdout) if not int(v["kept"])}


# ── (a) the motivating row ─────────────────────────────────────────────────
def test_the_motivating_row_is_withheld_by_its_declaration_not_its_prose(monkeypatch):
    """#10175 says "the cap is HELD deliberately — never override it" — in a title.

    The title is `ops(capture):`, which is NOT a container prefix (nor may it
    become one: `ops(quota):` and `ops(ci):` are real units). The exclusion must
    therefore come from the DECLARATION, and the reason must say so — that is the
    difference between a mechanical marker and reading English (#10902).
    """
    rows = [
        issue(
            10175,
            "ops(capture): the hosted capture cap is HELD deliberately — never override it",
            labels=["P1-severe", "standing-constraint"],
            assignees=[],
        )
    ]
    snap = snap_with(rows, monkeypatch)
    unit = units_by_issue(snap)[10175]
    assert snap.population == {"10175": REASON_DECLARED}, snap.population
    assert unit.pickable is False
    assert unit.reason == REASON_DECLARED
    assert next_command(unit, REPO) is None, "a withheld row must not be handed a command"
    assert any("standing-constraint" in d for d in unit.detail), unit.detail


# ── (b) every spelling the repo emits ──────────────────────────────────────
#: The container spellings measured on the live population of both repos
#: (2026-11-08, `P0 ∪ P1 ∪ P2`: 766 rows, 52 withheld by the shipped rule).
CONTAINER_TITLES = [
    "epic: Extractor v4 — the selective write path",
    "epic(extractor-v4): CAPSTONE — end-to-end proof",
    "epic-scope: multi-session retrieval evidence-surface",
    "Epic: Agent-reasoning eval battery",
    "decision(security): Cloudflare Web Analytics is edge-injected",
    "Decision needed: reinstate or drop the deferred checks",
    "OWNER DECISION (OD-6): what do we promise about a restore takes",
    "owner decision (small): restore a cheap search rung",
    "design(extractor): the destructive/relocation half of G5",
    "design(tiering): the sufficiency router",
    "coord: memory quality + cost — measurements",
    "measure(ask): seal and publish the epic Q3 exit measurement",
    "measure(tiering): #5009's premise is unmeasured",
    "analysis(review-enforcer): narrowing the ANSI-C rule",
    "investigation(ci): why the shard went quiet",
    "requirement(objective-4): the done-state has never been measured",
    "tracking: making the hosted graph usable",
    "residual(extractor): the boundary's NER/POS residuals",
    "map(pricing): every place tied to the pricing model",
    "arch(storage): own the storage architecture",
    "architecture: the fleet is not a lifecycle owner",
    "lane L3: extractor — the pipeline body",
]

#: Titles that carry a container-LOOKING stem as an ordinary word, or a bare
#: spoken form. Every one is REAL WORK and a prefix regex silently hides it —
#: the shape the first candidate fix for this issue shipped, and the reason the
#: matching is structural (`<kind>(` / `<kind>:` / `lane <id>:`) rather than `^`.
REAL_WORK_TITLES = [
    "feat(extractor): the whole-substrate rewrite",
    "fix(quota): a defect that must be picked",
    "bug(rail): nothing detects a SILENT rail hang",
    "test(assembly): a flaky admission table",
    "chore(security): dated re-verification",
    "refactor(extractor): one v4 write-path substrate",
    "ops(quota): the per-org allowance is wrong",
    "docs(ontology): the predicate list drifted",
    "perf(extractor): the quantity/COGS lever",
    "Measure long-context ANSWER QUALITY at >=300K tokens",  # bare `Measure`
    "Measure summarization latency + time-to-first-byte",  # bare `Measure`
    "lane-actions: _is_linked_worktree reads an ANCESTOR's .git",  # bare `lane-`
    "lane-watchdog.py: human_above() stops at any ancestor",  # bare `lane-`
    "design-tokens: the palette is duplicated in three places",
    "analysis-paralysis fix: the option list never converges",
    "coord-lock: a stale lock blocks the run",
    "A bare title that names no kind at all",
]


@pytest.mark.parametrize("title", CONTAINER_TITLES)
def test_every_container_spelling_the_repo_emits_is_withheld(title, monkeypatch):
    snap = snap_with([issue(7001, title, labels=[], assignees=[])], monkeypatch)
    unit = units_by_issue(snap)[7001]
    assert snap.population == {"7001": REASON_CONTAINER}, (title, snap.population)
    assert unit.pickable is False
    assert next_command(unit, REPO) is None


@pytest.mark.parametrize("title", REAL_WORK_TITLES)
def test_real_work_is_never_hidden(title, monkeypatch):
    """A filter that hides work is the same defect pointed the other way."""
    snap = snap_with([issue(7002, title, labels=[], assignees=[])], monkeypatch)
    unit = units_by_issue(snap)[7002]
    assert snap.population == {}, (title, snap.population)
    assert unit.pickable is True
    assert next_command(unit, REPO) is not None


# ── (c) the two surfaces agree — this issue's acceptance clause ────────────
def test_the_consumer_withholds_exactly_what_the_shipped_rule_withholds(monkeypatch):
    """ONE fixture, TWO surfaces: the rule's own jq verdict vs the picker's rows.

    This is the clause the issue closes on — *"the container-title filter … used by
    both surfaces"*, and *"closes when those exist and the two surfaces report the
    same population"*. The board side is computed here by running the program
    directly (`run_shipped_program`), never by asking this tool what it decided.
    """
    rows = [issue(7100 + i, t, labels=[], assignees=[]) for i, t in enumerate(CONTAINER_TITLES)]
    rows += [issue(7200 + i, t, labels=[], assignees=[]) for i, t in enumerate(REAL_WORK_TITLES)]
    rows.append(
        issue(7300, "ops(capture): the cap is HELD", labels=["standing-constraint"], assignees=[])
    )
    rows.append(issue(7301, "feat(x): assigned to a human", labels=[], assignees=[{"login": "d"}]))
    rows.append(issue(7302, "bug(y): no labels or assignees key at all"))

    board = run_shipped_program(rows)
    snap = snap_with(rows, monkeypatch)
    consumer = {int(n) for n, _ in snap.population.items()}

    assert consumer == board, {
        "consumer_only": sorted(consumer - board),
        "board_only": sorted(board - consumer),
    }
    assert 7300 in board and 7301 in board
    assert 7302 not in board, "a MISSING field is not an exclusion"
    assert 7100 + len(CONTAINER_TITLES) - 1 in board


# ── (d) the field-shape traps ─────────────────────────────────────────────
def test_a_missing_labels_or_assignees_key_is_not_an_exclusion(monkeypatch):
    snap = snap_with([issue(7400, "fix(a): no fields at all")], monkeypatch)
    assert snap.population == {}
    assert units_by_issue(snap)[7400].pickable is True


def test_plain_string_labels_are_coerced_and_the_rule_still_runs(monkeypatch):
    """`labels: ["standing-constraint"]` must not take the WHOLE run down.

    Measured against the shipped arm: `["standing-constraint"]` makes jq die with
    `Cannot index string with string "name"`, which would surface as an UNREADABLE
    population and withhold the entire queue for one hand-written snapshot. The
    coercion that already existed for PR labels is applied to issue rows at the
    same door, so the DECLARATION is still honoured.
    """
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.delenv("NEXT_WORK_POPULATION", raising=False)
    raw = {
        "repo": REPO,
        "base": "main",
        "issues": [issue(7500, "ops(capture): the cap is HELD", labels=["standing-constraint"])],
        "population": {},
    }
    snap = Snapshot.from_json(raw)
    _population_verdicts(REPO_ROOT, snap)
    assert not any("population:" in u for u in snap.unreadable), snap.unreadable
    assert snap.population == {"7500": REASON_DECLARED}, snap.population
    assert units_by_issue(snap)[7500].pickable is False


def test_a_nameless_label_element_does_not_take_the_run_down(monkeypatch):
    """One malformed element is not a reason to withhold (or grant) a whole queue."""
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    raw = {
        "repo": REPO,
        "base": "main",
        "issues": [issue(7501, "fix(a): real work", labels=[{"id": 1}])],
        "population": {},
    }
    snap = Snapshot.from_json(raw)
    _population_verdicts(REPO_ROOT, snap)
    assert not any("population:" in u for u in snap.unreadable), snap.unreadable
    assert snap.population == {}
    assert units_by_issue(snap)[7501].pickable is True
    # ⛔ THE OTHER HALF OF THAT PROMISE (#10951 review, F3). "The rule survives" is
    # pinned above; "the run is REFUSED" was pinned by nothing, so deleting the
    # recording kept the suite green while a hand-written snapshot with
    # `labels: [{"id": 1}]` would have run clean at exit 0/1 with no warning.
    assert any("labels element has no name" in u for u in snap.unreadable), snap.unreadable


# ── (e) an unreadable rule withholds everything ────────────────────────────
def test_an_unreadable_rule_withholds_every_row_and_says_why(monkeypatch):
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", "/nonexistent/refresh-candidates.sh")
    monkeypatch.delenv("NEXT_WORK_POPULATION", raising=False)
    monkeypatch.delenv("AGENT_INFRA_PATH", raising=False)
    snap = Snapshot(issues=[issue(7600, "fix(a): real work"), issue(7601, "epic: a container")])
    _population_verdicts(REPO_ROOT, snap)

    assert set(snap.population.values()) == {REASON_POP_UNMEASURED}
    assert any("population:" in u for u in snap.unreadable), snap.unreadable
    assert snap.population_source.startswith("UNREADABLE")
    for unit in build_units(snap, None):
        assert unit.pickable is False
        assert next_command(unit, REPO) is None


def test_a_rule_whose_program_will_not_run_withholds_every_row(tmp_path, monkeypatch):
    """jq REFUSING is not a population that passed — the fail-open this rule fears."""
    broken = tmp_path / "broken-rule.sh"
    broken.write_text("POOL_JQ='select(nosuchfunction)'\n", encoding="utf-8")
    snap = snap_with([issue(7602, "fix(a): real work")], monkeypatch, rule=broken)
    assert set(snap.population.values()) == {REASON_POP_UNMEASURED}
    assert any("could not be driven" in u for u in snap.unreadable), snap.unreadable


# ── (f) the off-switch ────────────────────────────────────────────────────
def test_the_off_switch_restores_the_old_behaviour_and_is_reported(monkeypatch):
    """`NEXT_WORK_POPULATION=0` is the pre-#10951 tool, exactly — and it says so.

    If the switch is never tested it is not a switch, it is a rumour. What the OLD
    path does when it is off: NO population rule is consulted at all, so every
    collected row is admitted on its title and labels — an epic, a `design(…)`
    decision, a `measure(…)` and #10175 all receive a runnable dispatch command.
    """
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.setenv("NEXT_WORK_POPULATION", "0")
    snap = Snapshot(
        issues=[
            issue(7700, "epic: a container"),
            issue(7701, "design(x): a decision", labels=[], assignees=[]),
            issue(7702, "ops(capture): held", labels=["standing-constraint"], assignees=[]),
        ]
    )
    _population_verdicts(REPO_ROOT, snap)
    assert snap.population == {}
    assert snap.population_source.startswith("OFF")
    assert snap.unreadable == []
    for unit in build_units(snap, None):
        assert unit.pickable is True, unit.reason
        assert next_command(unit, REPO) is not None


# ── (g) a replay without verdicts is not a pass ───────────────────────────
def test_a_snapshot_without_verdicts_cannot_replay_as_a_pass():
    """An ABSENT measurement granted as a pass is the same defect one level up."""
    raw = {"repo": REPO, "base": "main", "issues": [issue(7800, "epic: a container")]}
    snap = Snapshot.from_json(raw)
    assert snap.population_absent is True
    assert any("population:" in u for u in snap.unreadable), snap.unreadable
    unit = units_by_issue(snap)[7800]
    assert unit.pickable is False
    assert unit.reason == REASON_POP_UNMEASURED
    assert next_command(unit, REPO) is None


def test_a_snapshot_that_carries_its_verdicts_replays_them():
    raw = {
        "repo": REPO,
        "base": "main",
        "issues": [issue(7801, "epic: a container"), issue(7802, "fix(a): real work")],
        "population": {"7801": REASON_CONTAINER},
        "population_source": "replay fixture",
    }
    snap = Snapshot.from_json(raw)
    assert snap.population_absent is False
    assert not any("population:" in u for u in snap.unreadable), snap.unreadable
    units = units_by_issue(snap)
    assert units[7801].pickable is False and units[7801].reason == REASON_CONTAINER
    assert units[7802].pickable is True


# ── the seam itself: extraction, attribution, provenance ──────────────────
def test_the_fixture_rule_mirrors_the_shipped_one():
    """The mirror is tied to the shipped bytes wherever those are resolvable."""
    shipped = _population_script(REPO_ROOT)
    if not shipped:
        pytest.skip(
            "no refresh-candidates.sh on this box (scripts/ is gitignored, #9475) — the "
            "fixture's POOL_JQ is a STATED mirror and is not tied to the shipped bytes here"
        )
    theirs = _pool_jq(shipped)
    ours = _pool_jq(str(FIXTURE_RULE))
    assert theirs, f"the shipped script at {shipped} carries no POOL_JQ assignment"
    assert hashlib.sha256(ours.encode()).hexdigest() == hashlib.sha256(theirs.encode()).hexdigest(), (
        "the fixture mirrors a DIFFERENT program than the shipped one — update "
        "tests/fixtures/10951/pool-rule.sh from agent-infra "
        "scripts/orchestrator-harness/refresh-candidates.sh"
    )


def test_the_arm_split_is_reversible_and_attribution_is_by_identity():
    """The three stage names are an IDENTITY mapping; the split must round-trip."""
    prog = _pool_jq(str(FIXTURE_RULE))
    stages = _top_level_stages(prog)
    assert len(stages) == 3, stages
    assert "|".join(stages) == prog, "a lossy split must not be used to label a row"
    # The label arm contains a NESTED pipe; a naive `split("|")` finds four stages.
    assert prog.count("|") > 2


def test_a_changed_program_shape_still_withholds_but_stops_claiming_an_arm(tmp_path, monkeypatch):
    """A shape change loses the LABEL, never the WITHHOLD."""
    two_stage = tmp_path / "two-stage.sh"
    two_stage.write_text(
        "POOL_JQ='select((.assignees|length)==0)|select((.title//\"\")|test(\"^epic\")|not)'\n",
        encoding="utf-8",
    )
    snap = snap_with([issue(7900, "epic: a container")], monkeypatch, rule=two_stage)
    assert snap.population == {"7900": REASON_NOT_A_UNIT}, snap.population
    unit = units_by_issue(snap)[7900]
    assert unit.pickable is False
    assert next_command(unit, REPO) is None
    _prog, attributable = _population_program("select(.a)|select(.b)")
    assert attributable is False


def test_the_provenance_names_the_script_and_the_program_digest(monkeypatch):
    snap = snap_with([issue(7950, "fix(a): real work")], monkeypatch)
    prog = _pool_jq(str(FIXTURE_RULE))
    assert str(FIXTURE_RULE) in snap.population_source
    assert hashlib.sha256(prog.encode()).hexdigest()[:12] in snap.population_source


def test_the_assignment_extraction_refuses_a_second_quote(tmp_path):
    """`[^']*` not `.*`: a line that gains a quote yields NOTHING, not a truncation."""
    bad = tmp_path / "bad.sh"
    bad.write_text("POOL_JQ='select(.a)' ; echo hi'\n", encoding="utf-8")
    assert _pool_jq(str(bad)) == ""
    good = tmp_path / "good.sh"
    good.write_text("x=1\nPOOL_JQ='select(.a)'\n", encoding="utf-8")
    assert _pool_jq(str(good)) == "select(.a)"


def test_the_arm_reasons_are_all_refused_by_pickable():
    """Every population reason must be in the ONE refusal tuple, or a row leaks."""
    for reason in (REASON_ASSIGNED, REASON_DECLARED, REASON_CONTAINER,
                   REASON_NOT_A_UNIT, REASON_POP_UNMEASURED):
        unit = next_work.Unit(
            key="1", kind="issue", issue=1, fold_pr=None, priority="P1-severe",
            title="t", pr=None, head=None, position="NO-PR", reason=reason,
        )
        assert unit.pickable is False, reason
        assert next_command(unit, REPO) is None, reason


# ── the two arms the first cut of this suite could not see ────────────────
# A reviewer measured BOTH of the following as MUTANTS THAT SURVIVED the suite as
# first written: deleting the with-PR consult and deleting the `_search_issues`
# field carry each left 29+333 tests green while restoring the #10951 failure.
def _pull(n: int, closes: list[int]) -> dict:
    """The collector's PR shape (`collect` builds exactly these keys)."""
    return {
        "number": n,
        "head": "b" * 40,
        "draft": False,
        "base": {"ref": "main"},
        "labels": [],
        "closing_issues": closes,
        "refs_issues": [],
        "title": f"pr {n}",
        "fold_line": "",
    }


def _ready_snapshot(rows: list[dict], monkeypatch) -> Snapshot:
    """A snapshot in which each row's PR is READY-TO-LAND.

    Worst case for the population rule: everything else about the row says "merge
    it now", so only the population verdict can withhold it. Without this the
    with-PR branch was exercised by nothing, and deleting it changed no assertion.
    """
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.setenv("NEXT_WORK_POPULATION", "1")
    monkeypatch.delenv("AGENT_INFRA_PATH", raising=False)
    pulls = [_pull(8000 + i, [int(r["number"])]) for i, r in enumerate(rows)]
    snap = Snapshot(
        repo=REPO,
        base="main",
        config_source="origin/main:.mergify.yml",
        gate={
            "queue_name": CFG.queue_name,
            "base": CFG.base,
            "required_checks": list(CFG.required_checks),
            "required_labels": list(CFG.required_labels),
            "forbidden_labels": list(CFG.forbidden_labels),
        },
        issues=rows,
        pulls=pulls,
        check_runs={
            "b" * 40: [
                {"id": i, "name": name, "status": "completed", "conclusion": "success",
                 "app": {"slug": "github-actions"}}
                for i, name in enumerate(CFG.required_checks)
            ]
        },
        attestation={str(p["number"]): {"verdict": "head"} for p in pulls},
        merge_tree={str(p["number"]): {"clean": True, "why": ""} for p in pulls},
    )
    _population_verdicts(REPO_ROOT, snap)
    return snap


@pytest.mark.parametrize(
    "row,expected",
    [
        (issue(8100, "design(extractor): the destructive half", labels=[], assignees=[]),
         REASON_CONTAINER),
        (issue(8101, "epic: a container", labels=[], assignees=[]), REASON_CONTAINER),
        (
            issue(8102, "ops(capture): the cap is HELD", labels=["standing-constraint"], assignees=[]),
            REASON_DECLARED,
        ),
        (issue(8103, "feat(x): assigned", labels=[], assignees=[{"login": "d"}]), REASON_ASSIGNED),
    ],
)
def test_a_container_with_a_merge_ready_pr_is_still_not_a_work_unit(row, expected, monkeypatch):
    """The with-PR branch — the half the code calls load-bearing, and had no test.

    Measured by the reviewer: deleting `elif population:` left everything green,
    and a `design(extractor):` container whose PR was attested + merge-tree-clean
    came out `READY-TO-LAND, executable`, with a runnable `merge-one.sh` — the
    #10951 failure, on the branch that decides merges.
    """
    snap = _ready_snapshot([row], monkeypatch)
    unit = {int(u.issue): u for u in build_units(snap, CFG)}[int(row["number"])]
    assert unit.position == "READY-TO-LAND", unit.detail          # the premise
    assert snap.population == {str(row["number"]): expected}, snap.population
    assert unit.reason == expected
    assert unit.withheld_by_population is True
    assert unit.pickable is False
    assert next_command(unit, REPO) is None


def test_a_merge_ready_real_unit_keeps_its_command(monkeypatch):
    """The control for the test above: the SAME shape, a title the rule keeps."""
    snap = _ready_snapshot([issue(8110, "fix(quota): real work", labels=[], assignees=[])], monkeypatch)
    unit = {int(u.issue): u for u in build_units(snap, CFG)}[8110]
    assert unit.position == "READY-TO-LAND"
    assert unit.pickable is True
    assert next_command(unit, REPO) is not None


def test_a_withheld_container_does_not_steal_a_real_units_action_target(monkeypatch):
    """One PR closing a container AND real work must not leave NEITHER offered.

    Measured by the reviewer before this fix: `#8488 … not offered: duplicate of
    #5333` with no command anywhere, while the footer said "every measured one is
    held, superseded, or a duplicate" — none of which the target owner was. A
    container is not a unit, so it owns nothing; the real unit keeps the command.
    """
    rows = [
        issue(8120, "design(extractor): a container", labels=[], assignees=[]),
        issue(8121, "feat(extractor): real work", labels=[], assignees=[]),
    ]
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.setenv("NEXT_WORK_POPULATION", "1")
    monkeypatch.delenv("AGENT_INFRA_PATH", raising=False)
    shared = _pull(8200, [8120, 8121])   # ONE PR closes both
    snap = Snapshot(
        repo=REPO,
        base="main",
        config_source="origin/main:.mergify.yml",
        gate={
            "queue_name": CFG.queue_name,
            "base": CFG.base,
            "required_checks": list(CFG.required_checks),
            "required_labels": list(CFG.required_labels),
            "forbidden_labels": list(CFG.forbidden_labels),
        },
        issues=rows,
        pulls=[shared],
        check_runs={
            "b" * 40: [
                {"id": i, "name": name, "status": "completed", "conclusion": "success",
                 "app": {"slug": "github-actions"}}
                for i, name in enumerate(CFG.required_checks)
            ]
        },
        attestation={"8200": {"verdict": "head"}},
        merge_tree={"8200": {"clean": True, "why": ""}},
    )
    _population_verdicts(REPO_ROOT, snap)
    assert snap.population == {"8120": REASON_CONTAINER}

    units = {int(u.issue): u for u in build_units(snap, CFG)}
    assert units[8120].pickable is False
    assert units[8120].duplicate_of is None
    assert units[8121].pickable is True, units[8121].detail
    assert units[8121].duplicate_of is None, "the real unit is not a duplicate of a container"
    assert next_command(units[8121], REPO) is not None


def test_the_collector_carries_the_fields_the_rule_reads(monkeypatch):
    """`_search_issues` must CARRY labels/assignees — the ordering constraint.

    Measured by the reviewer: deleting the two projection lines left the suite
    green. On a live collect the payload's labels/assignees would then be empty, so
    the DECLARED and ASSIGNED arms could never fire — the motivating row and every
    owner-assigned issue come back pickable — while `population_source` still claims
    a measurement.
    """

    class StubClient:
        def _json(self, args):
            return {
                "total_count": 2,
                "items": [
                    {
                        "number": 8300,
                        "title": "ops(capture): the cap is HELD",
                        "labels": [{"name": "P1-severe"}, {"name": "standing-constraint"}],
                        "assignees": [],
                    },
                    {
                        "number": 8301,
                        "title": "fix(quota): real work",
                        "labels": [{"name": "P1-severe"}],
                        "assignees": [{"login": "daniel-ospina"}],
                    },
                ],
            }

    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.setenv("NEXT_WORK_POPULATION", "1")
    monkeypatch.delenv("AGENT_INFRA_PATH", raising=False)
    snap = Snapshot()
    rows = next_work._search_issues(StubClient(), REPO, "P1-severe", snap)
    assert snap.unreadable == [], snap.unreadable
    assert rows[0]["labels"] and rows[0]["assignees"] == []
    assert rows[1]["labels"] and rows[1]["assignees"][0]["login"] == "daniel-ospina"

    snap.issues = rows
    _population_verdicts(REPO_ROOT, snap)
    assert snap.population == {"8300": REASON_DECLARED, "8301": REASON_ASSIGNED}, snap.population
    units = units_by_issue(snap)
    assert units[8300].pickable is False
    assert units[8301].pickable is False
    assert next_command(units[8300], REPO) is None
    assert next_command(units[8301], REPO) is None


def test_an_unrecognised_verdict_string_is_still_refused(monkeypatch):
    """The refusal is by PRESENCE, not by a literal lookup — measured fail-open.

    A snapshot carrying a verdict from a NEWER vocabulary used to be labelled
    withheld and handed a runnable command in the same row, because `pickable` only
    knew the seven literals this version writes.
    """
    for verdict in ("STANDING-CONSTRAINT-V2", "container", "CONTAINER "):
        snap = Snapshot(
            repo=REPO,
            base="main",
            issues=[issue(8400, "fix(a): looks like work")],
            population={"8400": verdict},
            population_source="replay from a newer vocabulary",
        )
        unit = units_by_issue(snap)[8400]
        assert unit.withheld_by_population is True, verdict
        assert unit.pickable is False, verdict
        assert next_command(unit, REPO) is None, verdict


def test_the_render_names_the_population_source(monkeypatch):
    """The provenance is a FEATURE of this change; it needs an assertion."""
    snap = snap_with([issue(8500, "fix(a): real work")], monkeypatch)
    text, _pick = next_work.render(build_units(snap, None), None, snap, repo=REPO, top=5)
    assert f"population: {snap.population_source}" in text
    assert "sha256:" in text


def test_a_replay_with_a_wrong_typed_population_is_a_named_refusal():
    """`population: null` used to surface as a raw AttributeError inside INCOMPLETE."""
    raw = {"repo": REPO, "base": "main", "issues": [issue(8600, "epic: a container")],
           "population": None}
    snap = Snapshot.from_json(raw)
    assert any("population:" in u and "not a map" in u for u in snap.unreadable), snap.unreadable
    assert snap.population == {}


@pytest.mark.parametrize("value", ["OFF", "Off", "NO", "False", "0"])
def test_the_off_switch_is_case_insensitive(monkeypatch, value):
    """`NEXT_WORK_POPULATION=False` must not silently leave the rule ON."""
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.setenv("NEXT_WORK_POPULATION", value)
    snap = Snapshot(issues=[issue(8700, "epic: a container")])
    _population_verdicts(REPO_ROOT, snap)
    assert snap.population == {}
    assert snap.population_source.startswith("OFF")


# ── the WIRING: `collect()` must actually call the rule ───────────────────
def test_collect_wires_the_population_rule_in(monkeypatch):
    """The live integration point — the one seam every other test bypasses.

    Measured by a reviewer: replacing `collect()`'s `_population_verdicts(...)` call
    with `pass` left 377/377 GREEN, on both the runner shape and a box with
    `scripts/`. That mutant is not cosmetic — `snap.population` stays `{}`, the
    provenance prints nothing, and every container (`epic:`, `design(…)`, `#10175`)
    is handed `merge-one.sh` / `dispatch-merge.sh` again. Exactly #10951, with a
    fully green suite.

    Every other test here calls `_population_verdicts` or `build_units` directly;
    this one goes through `collect()`, which is what a live run executes.
    """
    container = {
        "number": 9001,
        "title": "epic: a container the rule must withhold",
        "priority": "P0-blocker",
        "labels": [],
        "assignees": [],
    }

    class FakeClient:
        def _get_list(self, path, params="", select=".[]"):
            return []

        def list_check_runs(self, repo, sha):
            return []

    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.setenv("NEXT_WORK_POPULATION", "1")
    monkeypatch.delenv("AGENT_INFRA_PATH", raising=False)
    monkeypatch.setattr(next_work, "GhCli", lambda: FakeClient())
    monkeypatch.setattr(
        next_work, "_search_issues",
        lambda c, r, label, s: [dict(container)] if label == "P0-blocker" else [],
    )
    monkeypatch.setattr(next_work, "_closing_refs", lambda *a, **k: {})
    monkeypatch.setattr(next_work, "_local_holders", lambda *a, **k: {})
    monkeypatch.setattr(next_work, "_claim_verdicts", lambda *a, **k: None)

    snap, _cfg, _src = next_work.collect(REPO, REPO_ROOT)

    assert snap.population == {"9001": REASON_CONTAINER}, snap.population
    assert "sha256:" in snap.population_source, snap.population_source
    unit = units_by_issue(snap)[9001]
    assert unit.pickable is False
    assert next_command(unit, REPO) is None


def test_the_empty_pick_footer_names_the_class_that_withheld(monkeypatch):
    """The footer used to say "held, superseded, or a duplicate" over a container queue.

    That is the misdescription the earlier stolen-target finding was filed for, and
    the arm that fixes it had no assertion — reverting it left the suite green.
    """
    snap = snap_with(
        [
            issue(9100, "epic: a container", labels=[], assignees=[]),
            issue(9101, "design(x): a decision", labels=[], assignees=[]),
        ],
        monkeypatch,
    )
    text, pick = next_work.render(build_units(snap, None), None, snap, repo=REPO, top=5)
    assert pick is None
    assert "withheld as" in text, text
    assert REASON_CONTAINER in text
    assert "every measured one is held, superseded, or a duplicate" not in text


def test_a_wrong_typed_population_source_is_a_named_refusal():
    """Not an AttributeError raised from inside INCOMPLETE."""
    raw = {"repo": REPO, "base": "main", "issues": [], "population": {},
           "population_source": 5}
    snap = Snapshot.from_json(raw)
    assert any("population_source" in u for u in snap.unreadable), snap.unreadable
    # The refusal resets it; because the MAP is usable the replay label then takes
    # over — the point is that the wrong-typed value never survives as a source.
    assert isinstance(snap.population_source, str)
    assert snap.population_source != 5


def test_a_refused_map_does_not_claim_to_be_a_replay_measurement():
    """`population: null` must not print a provenance line asserting a measurement."""
    raw = {"repo": REPO, "base": "main", "issues": [], "population": None}
    snap = Snapshot.from_json(raw)
    assert any("not a map" in u for u in snap.unreadable), snap.unreadable
    assert "REPLAY" not in snap.population_source, snap.population_source


def test_a_superseded_container_is_not_reported_as_population_withheld():
    """The flag mirrors the reason that WON, so `--json` cannot contradict itself."""
    snap = Snapshot(
        repo=REPO,
        base="main",
        issues=[issue(9200, "epic: a container AND a fold constituent", labels=[], assignees=[])],
        pulls=[dict(_pull(9300, [9200]), labels=[{"name": "superseded-by-fold"}])],
        population={"9200": REASON_CONTAINER},
        population_source="replay fixture",
    )
    unit = units_by_issue(snap)[9200]
    assert unit.reason == next_work.REASON_SUPERSEDED
    assert unit.withheld_by_population is False
    assert unit.pickable is False


def test_a_zero_row_queue_still_names_the_rule_it_would_have_used(monkeypatch):
    """`the source is printed ALWAYS` is a render property, not a non-empty-run one.

    BOTH branches are pinned: the rule-present one (the digest) and the no-rule one
    (`UNREADABLE …(no rows)`), because deleting the second left the suite green while
    making the docstring's "the source is printed" false again — zero rows are the
    one population where nothing else would ever notice.
    """
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.delenv("NEXT_WORK_POPULATION", raising=False)
    snap = Snapshot(issues=[])
    _population_verdicts(REPO_ROOT, snap)
    assert "sha256:" in snap.population_source

    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", "/nonexistent/refresh-candidates.sh")
    monkeypatch.delenv("AGENT_INFRA_PATH", raising=False)
    no_rule = Snapshot(issues=[])
    _population_verdicts(REPO_ROOT, no_rule)
    assert no_rule.population_source.startswith("UNREADABLE"), no_rule.population_source
    assert no_rule.unreadable == [], "zero rows means nothing to withhold, so nothing is refused"


# ── the two reporting arms the fourth round found unpinned ────────────────
def test_an_empty_queue_with_the_switch_off_still_reports_OFF(monkeypatch):
    """The zero-row branch used to run BEFORE the off-switch and shadow it.

    Measured: an empty queue with `NEXT_WORK_POPULATION=0` printed the rule's path
    and `sha256:` with NO ⚠️ marker — so the one artifact a closer audits said the
    deployed rule had decided the population, the opposite of the switch state.
    """
    monkeypatch.setenv("NEXT_WORK_POOL_JQ_SH", str(FIXTURE_RULE))
    monkeypatch.setenv("NEXT_WORK_POPULATION", "0")
    snap = Snapshot(issues=[])
    _population_verdicts(REPO_ROOT, snap)
    assert snap.population_source.startswith("OFF"), snap.population_source
    assert "sha256:" not in snap.population_source


def test_the_footer_names_an_unrecognised_verdict_by_presence(monkeypatch):
    """The footer keyed on the tuple, so a newer-vocabulary verdict was misdescribed."""
    snap = Snapshot(
        repo=REPO,
        base="main",
        issues=[issue(9400, "fix(a): looks like work")],
        population={"9400": "STANDING-CONSTRAINT-V2"},
        population_source="replay from a newer vocabulary",
    )
    text, pick = next_work.render(build_units(snap, None), None, snap, repo=REPO, top=5)
    assert pick is None
    assert "STANDING-CONSTRAINT-V2" in text, text
    assert "every measured one is held, superseded, or a duplicate" not in text


def test_arm_attribution_is_by_content_not_by_position(monkeypatch, tmp_path):
    """A reordered upstream program must not mislabel the arm.

    Measured by the reviewer: joining the SAME three stages in another order keeps
    `len == 3` and the rejoin identity, so positional attribution made the container
    arm read "its labels DECLARE `standing-constraint`" — confidently, with a normal
    provenance line, and the mirror-tie test cannot see it on a runner (that is the
    one test that skips without `scripts/`). The withhold is unaffected; the LABEL
    must not lie.
    """
    prog = _pool_jq(str(FIXTURE_RULE))
    stages = _top_level_stages(prog)
    reordered = "|".join([stages[2], stages[1], stages[0]])
    rule = tmp_path / "reordered.sh"
    rule.write_text(f"POOL_JQ='{reordered}'\n", encoding="utf-8")

    snap = snap_with(
        [
            issue(9500, "epic: a container", labels=[], assignees=[]),
            issue(9501, "ops(x): declared", labels=["standing-constraint"], assignees=[]),
            issue(9502, "fix(a): assigned", labels=[], assignees=[{"login": "d"}]),
        ],
        monkeypatch,
        rule=rule,
    )
    # The WITHHOLD is unchanged by the reorder …
    assert set(snap.population) == {"9500", "9501", "9502"}, snap.population
    # … and the LABELS are still the right ones, because they come from the stage's
    # CONTENT, not from its index.
    assert snap.population["9500"] == REASON_CONTAINER
    assert snap.population["9501"] == REASON_DECLARED
    assert snap.population["9502"] == REASON_ASSIGNED


# ── the last two unpinned arms of the rendering + JSON contracts ──────────
def test_the_footer_does_not_describe_a_claimed_queue_in_population_words():
    """TWO vocabularies, TWO sentences — and the split had no assertion.

    Measured: reverting the split left the suite green while a CLAIMED-only queue
    read "…withheld as CLAIMED (a queue whose rows are containers, declared
    constraints or otherwise not units is an ANSWER…)" — a gloss about containers
    over a queue that contains none.
    """
    snap = Snapshot(
        repo=REPO,
        base="main",
        issues=[issue(9600, "fix(a): real work")],
        pulls=[_pull(9601, [9600])],
        claims={"refs/claims/issue-9600": {"verdict": "claim-held", "owner": "box-a:4242"}},
        population={},
        population_source="replay fixture",
    )
    text, pick = next_work.render(build_units(snap, None), None, snap, repo=REPO, top=5)
    assert pick is None
    assert "CLAIMED" in text
    assert "containers, declared constraints" not in text, text


def test_the_json_carries_the_population_provenance():
    """`--json` is the machine surface; the provenance must reach it.

    Measured: deleting `population`, `population_source` AND
    `withheld_by_population` from the payload left the suite green, while the code
    comment beside them says a machine consumer needs exactly this to tell a
    measurement from OFF / UNREADABLE / ABSENT.
    """
    import io
    from contextlib import redirect_stdout

    # ⛔ THE FIXTURE CARRIES THE GATE, so the CLI leg below is a REFUSED=false run.
    # Without it `main` appends "queue gate: the snapshot carries none" and returns
    # 2 with `refused: true` — under which `next_command is None` is satisfied by the
    # REFUSAL and proves nothing about the withhold (measured by the reviewer: the
    # assertion read as evidence but was tautological).
    snap = Snapshot(
        repo=REPO,
        base="main",
        config_source="fixture:.mergify.yml",
        gate={
            "queue_name": CFG.queue_name,
            "base": CFG.base,
            "required_checks": list(CFG.required_checks),
            "required_labels": list(CFG.required_labels),
            "forbidden_labels": list(CFG.forbidden_labels),
        },
        gate_text=CONFIG_TEXT,
        issues=[issue(9700, "epic: a container"), issue(9701, "fix(a): real work")],
        pulls=[_pull(9702, [9701])],
        check_runs={
            "b" * 40: [
                {"id": i, "name": name, "status": "completed", "conclusion": "success",
                 "app": {"slug": "github-actions"}}
                for i, name in enumerate(CFG.required_checks)
            ]
        },
        attestation={"9702": {"verdict": "head"}},
        merge_tree={"9702": {"clean": True, "why": ""}},
        claims={},
        local_holders={},
        population={"9700": REASON_CONTAINER},
        population_source="replay fixture · POOL_JQ sha256:deadbeef",
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        next_work.render(build_units(snap, None), None, snap, repo=REPO, top=5)
    payload = json.loads(
        json.dumps(
            {
                "population_source": snap.population_source,
                "population": snap.population,
                "units": [
                    {"issue": u.issue, "withheld_by_population": u.withheld_by_population}
                    for u in build_units(snap, None)
                ],
            }
        )
    )
    assert payload["population_source"].startswith("replay fixture")
    assert payload["population"] == {"9700": REASON_CONTAINER}
    withheld = next(u for u in payload["units"] if u["issue"] == 9700)
    assert withheld["withheld_by_population"] is True

    # …and the same fields really are emitted by the CLI's own payload, not only by
    # this reconstruction: the `--json` branch is executed against a real snapshot.
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snap.json"
        path.write_text(json.dumps(snap.to_json()), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = next_work.main(["--from", str(path), "--repo", REPO, "--json"])
        emitted = json.loads(buf.getvalue())   # the payload is indented
    assert rc == 0, (rc, emitted.get("unreadable"))
    assert emitted["refused"] is False, emitted["unreadable"]
    assert emitted["population"] == {"9700": REASON_CONTAINER}
    assert emitted["population_source"].startswith("replay fixture")
    row = next(u for u in emitted["units"] if u["issue"] == 9700)
    assert row["withheld_by_population"] is True
    assert row["next_command"] is None
    # …and the CONTROL: the REAL unit in the same payload DOES carry a command, so
    # the `None` above is the withhold talking, not a blanket refusal.
    real = next(u for u in emitted["units"] if u["issue"] == 9701)
    assert real["withheld_by_population"] is False
    assert real["next_command"] is not None, real
