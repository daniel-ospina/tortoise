"""#4917 — the owner-confirmed salience labels, and the coverage they measure.

**What this file is.** `tests/eval/write_path/salience_labels.jsonl` holds 25 real
Object names that the OWNER ruled are not entities (`verdict: not_an_entity`,
`owner_confirmed: 2026-09-23`, drawn from `prod-graph-audit-2026-09-23`). They are
the labels the extractor's salience gate has to be measured against.

**Why the file is in the repo.** It lived only at
`~/.pi/agent/state/eval-datasets/` — the label FILE was outside version control,
one disk failure from gone, which is the exposure #4917 already records for the
B7 reports. The names were not wholly unknown in-repo: **9 of the 25** appear in
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §1 as illustrative non-entity
Objects — **3 definite descriptions**, **3 pointers** (`PR #465`, `issue #3775`,
`§24`) and **3 references** — but unlabelled: no class, no verdict, no reason.
The path below is the one the repo *already* names
(`docs/research/2026-09-23-pipeline-stage-ordering/research-brief.md:227`), and
landing it executes #4917 §3.2 item 1.

⚠️ **Four of the 25 names carry a SECOND, LATER owner label — and it is not a
contradiction, it is the scope of this file.** `docs/engineering/1026-calibration-set.jsonl`
(owner ruling 2026-09-26) rules `keep-object` (must-keep) on `guard 2`,
`_TOOL_BY_NAME`, `daniel-ospina/agent-infra` and `_capture_cost_props`, because
the proposed drop rule would be irreversible at mint time.

**⇒ These labels classify NAMES, not Objects.** `not_an_entity` means the name is
not an entity; it is **not** authority to delete the Object, and the same name can
be a must-keep. So this file is a **detection specification, never a drop list** —
which is also why `DESTRUCTIVE_DISPOSITIONS` is empty and why the shipped rule's
`the `-class delete-precision (~48%) disqualifies it from deleting anything. A
lane that reads the 25 rows as discard targets would delete must-keeps.

**The schema departs from the one #4917 §1.11 proposed.** §1.11 sketched rows of
`jev_proposal` / `owner_verdict` / `owner_note` / `reviewed`, scored by the
disagreement rate. The rows that actually exist carry **no `jev_proposal`**: they
are the owner's verdicts alone (`class`, `verdict`, `reason`, `statement_excerpt`,
`origin`, `owner_confirmed`). That is deliberate — a row recording what the model
proposed would make this a mixed-provenance set, and the measurement below needs
labels whose provenance is the owner and nobody else. The disagreement-rate
framing is therefore **not** available from this file, and nothing here claims it.

**The method it implements (owner's ruling, 2026-09-23).** The eval is NOT built
top-down: *"we won't get a perfect eval set unless I spend hours… easier to do it
bottom up"* — **the owner's review verdicts ARE the labels**, so the labelled set
is a byproduct of review rather than a prerequisite. That is why every row is
`owner_confirmed` and none is model-proposed: a row this model generated would
stop being the ground truth the measurement rests on.

**What the measurement says (this is the number the volume objective lacked).**
Scored against the shipped predicate (`tortoise/definite_description_backfill.py`:
`is_bare_definite_description` shape + the conservative `positive_evidence`
gate): **15/25 are in scope by shape, 9/25 are positively classified**, and **7 of
the 8 labelled classes have no detector at all**. So the labelled set is not a
regression test the shipped rule passes — it is the specification the missing
**detectors** must meet (detectors, not deletions: see the `keep-object` overlap
above).

⚠️ **The honest limit, recorded so it cannot be misread as precision later.**
This is a **fixed prod-audit set, not a random draw from the rejects**, so
`9/25` is a **coverage** figure and never a population precision. Estimating
precision over rejects needs a sample drawn at random from the rejects
specifically — the increment the pipeline-ordering brief names. This file does
not claim it.

Hermetic: no database, no network, no environment mutation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import definite_description_backfill as dd

LABELS_PATH = (
    Path(__file__).resolve().parent / "eval" / "write_path" / "salience_labels.jsonl"
)

#: The closed vocabulary of the audit. A `class` outside this set means the label
#: file grew a name class nobody classified — which is a finding, not a pass.
LABELLED_CLASSES = frozenset({
    "definite_description",
    "symbol_name",
    "file_path",
    "pr_or_issue_number",
    "section_marker",
    "code_fragment",
    "branch_name",
    "repo_slug",
})

ALLOWED_VERDICTS = frozenset({"not_an_entity", "is_an_entity"})

#: MEASURED 2026-09-29 against the shipped predicate. These are deliberately
#: literal: they are the drift pin. If the predicate changes, this test forces the
#: measurement to be re-taken and the new number to be written down on purpose,
#: rather than letting coverage move silently.
MEASURED_SHAPE_IN_SCOPE = 15
MEASURED_POSITIVELY_CLASSIFIED = 9
#: Of the 15 definite descriptions in scope by shape, this many are deferred by
#: the conservative gate — names the OWNER has already ruled are not entities.
MEASURED_DEFERRED_DEFINITE_DESCRIPTIONS = 6
MEASURED_CLASSES_WITH_NO_DETECTOR = frozenset({
    "branch_name",
    "code_fragment",
    "file_path",
    "pr_or_issue_number",
    "repo_slug",
    "section_marker",
    "symbol_name",
})


def _rows() -> list[dict]:
    return [
        json.loads(line)
        for line in LABELS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_labels_are_well_formed_and_provenanced():
    """Well-formedness is checkable here; provenance is established elsewhere.

    This asserts the row SHAPE. It cannot verify that the owner wrote `reason` —
    no test can — so provenance rests on the `origin`/`owner_confirmed` fields and
    their source in #4917, not on this assertion.
    """
    rows = _rows()
    assert rows, "the labelled set is empty — the measurement has no denominator"

    for row in rows:
        missing = {
            "object_name", "class", "verdict", "statement_excerpt",
            "origin", "owner_confirmed", "reason",
        } - set(row)
        assert not missing, f"row {row.get('object_name')!r} lacks {sorted(missing)}"

        assert row["verdict"] in ALLOWED_VERDICTS, (
            f"{row['object_name']!r} has verdict {row['verdict']!r}, outside the "
            f"closed vocabulary — an unclassifiable verdict must not silently "
            f"become a label"
        )
        assert row["class"] in LABELLED_CLASSES, (
            f"{row['object_name']!r} has class {row['class']!r}, which the audit "
            f"never declared — add it to LABELLED_CLASSES deliberately"
        )
        # An owner-sourced label names WHO confirmed it and WHEN. A row without
        # this is a model proposal wearing the owner's authority.
        assert row["owner_confirmed"].strip(), (
            f"{row['object_name']!r} is not owner-confirmed; it cannot be used as "
            f"ground truth"
        )
        # `reason` is the label's justification. A bare restatement of the class
        # would make the row unusable as a boundary example.
        assert len(row["reason"].strip()) >= 15, (
            f"{row['object_name']!r} carries reason {row['reason']!r} — too thin to "
            f"justify a label"
        )

    names = [r["object_name"] for r in rows]
    duplicates = {n for n in names if names.count(n) > 1}
    assert not duplicates, f"duplicate labelled names: {sorted(duplicates)}"

    # The PREMISE the whole measurement rests on: every row is a name the owner
    # ruled is NOT an entity. Without this, a row flipped to `is_an_entity` would
    # pass silently and make the 9/25 coverage narrative false.
    verdicts = {r["verdict"] for r in rows}
    assert verdicts == {"not_an_entity"}, (
        f"the label set is no longer uniformly not_an_entity: {sorted(verdicts)} — "
        f"the coverage figure is only meaningful over confirmed non-entities"
    )


def test_measured_coverage_of_the_shipped_predicate():
    """The measurement, pinned. Coverage is the deficit E1 is working against."""
    rows = _rows()

    in_scope = [r for r in rows if dd.is_bare_definite_description(r["object_name"])]
    positively = [
        r for r in rows
        if dd.is_positively_bare_definite_description(r["object_name"])
    ]

    assert len(in_scope) == MEASURED_SHAPE_IN_SCOPE, (
        f"shape coverage moved: {len(in_scope)}/{len(rows)} in scope, expected "
        f"{MEASURED_SHAPE_IN_SCOPE}. Re-take the measurement and update the "
        f"constant — do not let the number drift silently."
    )
    assert len(positively) == MEASURED_POSITIVELY_CLASSIFIED, (
        f"positive coverage moved: {len(positively)}/{len(rows)} classified, "
        f"expected {MEASURED_POSITIVELY_CLASSIFIED}."
    )

    # A class is "detected" only if the predicate positively classifies a row in
    # it. `definite_description` is the ONLY detected class, and even it is partial.
    detected_classes = {r["class"] for r in positively}
    undetected = {r["class"] for r in rows} - detected_classes
    assert undetected == MEASURED_CLASSES_WITH_NO_DETECTOR, (
        f"the set of classes with no positive detector changed: "
        f"{sorted(undetected ^ MEASURED_CLASSES_WITH_NO_DETECTOR)} differs. This "
        f"is the specification the missing detectors must meet — moving it is a "
        f"finding."
    )
    assert detected_classes == {"definite_description"}, (
        f"more than one class now has a detector ({sorted(detected_classes)}); "
        f"re-take the measurement"
    )

    # The sharper half of the finding: even inside the one detected class the gate
    # cannot decide 6 of the 15 names the owner already ruled are not entities.
    deferred_dd = {
        r["object_name"] for r in rows
        if r["class"] == "definite_description"
        and dd.is_bare_definite_description(r["object_name"])
        and not dd.is_positively_bare_definite_description(r["object_name"])
    }
    assert len(deferred_dd) == MEASURED_DEFERRED_DEFINITE_DESCRIPTIONS, (
        f"deferred definite descriptions moved: {len(deferred_dd)}, expected "
        f"{MEASURED_DEFERRED_DEFINITE_DESCRIPTIONS}"
    )

    # The finding in one line, for a reader who never opens the constants:
    # 25 owner-confirmed non-entities; 15 in scope by shape; 9 classified.
    assert (len(rows), len(in_scope), len(positively)) == (25, 15, 9)


def test_a_deferral_names_its_trigger_and_is_never_a_guess():
    """The conservative gate must REPORT what it cannot decide, and say why.

    `positive_evidence` is the only thing between a shape match and a non-`KEEP`
    rung. Six labelled definite descriptions are deferred by it even though the
    owner ruled they are not entities. That deferral is the gate working as
    designed (fail-closed), so this pins it as *diagnosed*: a deferral carries a
    concrete trigger, and it is never a silent assumption that the name IS an
    entity.
    """
    rows = [r for r in _rows() if r["class"] == "definite_description"]

    deferred = [
        r for r in rows
        if dd.is_bare_definite_description(r["object_name"])
        and not dd.is_positively_bare_definite_description(r["object_name"])
    ]
    assert deferred, (
        "no labelled definite description is deferred — either the gate changed "
        "or the sample did; re-take the measurement"
    )

    for row in deferred:
        evidence = dd.positive_evidence(row["object_name"])
        # `evidence` is non-None BY CONSTRUCTION for a deferred row (that is what
        # `is_positively_...` delegates to), so asserting it would be tautological.
        # What is worth pinning is that the trigger is STRUCTURAL: a restatement of
        # the name would make the deferral undiagnosable.
        assert row["object_name"] not in evidence, (
            f"the trigger for {row['object_name']!r} restates the name: {evidence!r}"
        )

    # The `DESTRUCTIVE_DISPOSITIONS` invariant is owned by
    # tests/test_definite_description_backfill_5059.py — it is that planner's
    # contract, and asserting it here would give one invariant two owners. What
    # this file adds is the EVIDENCE for why it must stay empty: the shipped rule's
    # delete-precision on the `the ` class is ~48% (2,570 of its 4,901 matches are
    # load-bearing), so a name SHAPE is never authority to delete a memory.
