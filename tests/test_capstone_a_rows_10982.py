"""The `A`-rows of `#7949` — registered by `#10982`, and the census that reports them.

`#7949` was extended on 2026-11-04 with five owner assertions (`A1`–`A5`) about the **object layer**,
and `E1`–`E5` name none of them: they cover volume, retention, anchoring, operator edges and
narrative fidelity. So the object layer had no acceptance criterion at all, and the five rows exist to
give `C3`–`C7` a fixed bar to report against.

Three properties are pinned here, and each names the mutation that makes it FAIL:

1. **the record registers them, and does NOT require them** — `required_criteria_rows` stays
   `[E1, E2, E3, E4, E5]`, because a required row cannot be inferred from the rest (`V2`) and making
   them required would red every verdict table that predates their scoring runs, the committed
   `capstone-measured-9152448d.log` included;
2. **the amendment is an amendment** — §10.11 quotes the superseded pins line verbatim, so the change
   is findable rather than a quiet overwrite (`#9736` is the model, not a new registration);
3. **the census reports, and where it cannot measure it says so** — a population no instrument has
   counted emits `UNMEASURED(<what would measure it>)`, never a `0`, because a zero is a NUMBER and
   it reads as a measured absence.

The census tests drive the probe's helpers through a FAKE projection, so they need no database: the
property under test is the arithmetic and the vocabulary (statement-only denominator, `NULL` as a
third state, per-family subject counting, cheap-vs-registered chain join), not FalkorDB.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import capstone_preregistration_check as check  # noqa: E402

PREREG_DIR = REPO_ROOT / "docs" / "evidence" / "7949-capstone"
PREREG = PREREG_DIR / "PRE-REGISTRATION.md"
RECORD = PREREG_DIR / "preregistration.json"
PROBE = PREREG_DIR / "probes" / "capstone_measure.py"


def _probe():
    """The census probe, loaded by path (it is evidence, not an importable package)."""
    spec = importlib.util.spec_from_file_location("capstone_measure_10982", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


A_ROWS = ("A1", "A2", "A3", "A4", "A5")


def _record() -> dict:
    return json.loads(RECORD.read_text())


# ── 1. the record registers the five rows, and does not require them ───────


def test_the_record_carries_all_five_a_rows_with_metric_denominator_and_threshold():
    """FAILS IF a row is dropped, or is added without the three cells a bar needs."""
    by_id = {c["id"]: c for c in _record()["criteria"]}
    for row in A_ROWS:
        assert row in by_id, f"{row} is not registered"
        for part in ("metric", "denominator", "threshold"):
            assert str(by_id[row].get(part) or "").strip(), f"{row} has no {part}"


def test_every_a_row_declares_its_floor_and_its_invisibility_ground():
    """FAILS IF a row's bar arrives without a floor, or its `false` without a ground.

    ⛔ A bare `measurement_visible_to_author: false` is UNFALSIFIABLE — that was `E4`'s defect
    (§10.4, `#10525`) — so the field is only honest beside the basis that makes it checkable.
    """
    record = _record()
    floors = record["denominator_floors"]
    by_id = {c["id"]: c for c in record["criteria"]}
    for row in A_ROWS:
        crit = by_id[row]
        assert crit["floor"] in floors, f"{row} names floor {crit['floor']!r}, which is not pinned"
        assert floors[crit["floor"]] == 30, f"{row}'s floor is {floors[crit['floor']]!r}, not 30"
        assert crit["measurement_visible_to_author"] is False
        assert str(crit.get("measurement_visible_to_author_basis") or "").strip(), (
            f"{row} asserts a measurement was invisible and states no ground")
        assert row not in record["prior_measurements"], (
            f"{row} is cited as a prior measurement, which falsifies its own `false`")


def test_every_pinned_A_floor_names_its_own_value_so_none_can_be_lowered_silently():
    """FAILS IF any `a*` floor's pinned VALUE moves, or one is dropped from the pin set.

    ⛔ `a2_candidates` and `a5_points` are pinned floors that are **no row's** `floor:` cell, so the
    per-row loop above never reads their value — a coordinated record edit lowering either to `5`
    left every arm green while §3.7's "all **30**" went stale (#10982 review round 4, F3). §3.7
    pins **nine** A floors: seven population floors of `30`, plus the two refusal boundaries that
    are not population counts.
    """
    floors = _record()["denominator_floors"]
    assert {k: v for k, v in floors.items() if k.startswith("a")} == {
        "a1_objects": 30,
        "a2_candidates": 30,
        "a2_decided_candidates": 30,
        "a3_statements": 30,
        "a4_typed_statements": 30,
        "a5_points": 30,
        "a5_actors": 30,
        "a1_rollup_queries": 1,
        "a3_event_root_coverage": 0.5,
    }, ("an A-row floor's pinned value moved, or the pin set changed shape — §3.7 pins NINE "
        "(seven population floors of 30, plus a1_rollup_queries: 1 and "
        "a3_event_root_coverage: 0.5)")


def test_the_a_rows_are_registered_but_not_REQUIRED():
    """⛔ The load-bearing half: adding a row to the record is not adding it to the gate.

    FAILS IF `required_criteria_rows` grows — that would demand `A1`–`A5` of every verdict table,
    including the committed one, for rows nobody has run yet (`#10984`–`#10988` own those runs).

    ⛔ AND THE SET DID MOVE ONCE, FOR A ROW THAT IS NOT AN `A`-ROW (`#11673`). `E6` is the capstone's
    sixth row, added on the owner's 2026-11-05 ruling (`#7949` comment `6664402530`), and it is
    REQUIRED — so the assertion below is no longer "unchanged since §10.11", it is "exactly the six
    `E`-rows and no `A`-row". Both halves are asserted: the equality alone would pass on a set that
    happened to have the right length, and the A-row half is the one this test has always protected.
    """
    assert check.REQUIRED_CRITERIA == ("E1", "E2", "E3", "E4", "E5", "E6")
    assert not (set(A_ROWS) & set(check.REQUIRED_CRITERIA)), (
        "an A-row became REQUIRED — that demands rows nobody has run from every verdict table")
    text = PREREG.read_text()
    assert "required_criteria_rows: [E1, E2, E3, E4, E5, E6]" in text
    pins = check.parse_pins(text)
    assert pins["required_criteria_rows"] == ["E1", "E2", "E3", "E4", "E5", "E6"]
    # …and the RECOGNISED set did grow, or the rows would be unreachable.
    assert set(pins["criteria"]) >= set(A_ROWS)
    # ⛔ The A1 family pin exists in BOTH artefacts, and must be the same list: the pins fence is the
    # one a human reads, the machine record is the one a consumer reads, and the instrument's own
    # constant is a THIRD copy (pinned by the `_VALID_EDGE_PREDICATES` arm below).
    assert pins["a1_relation_families"] == _record()["a1_relation_families"], (
        "the pins fence and the machine record pin different A1 families")
    # …and every refusal boundary the A cells register is pinned, not only the population counts.
    floors = pins["denominator_floors"]
    assert floors["a1_rollup_queries"] == 1, (
        "A1's rollup-query floor lives only in §3.1 prose — a child reading the pins would score "
        "A1 with ZERO queries")
    assert floors["a3_event_root_coverage"] == 0.5, (
        "A3's event-root-coverage floor lives only in §3.1 prose — a child reading the pins would "
        "score A3 at 0.05 coverage")
    assert _record()["denominator_floors"] == floors


def test_the_record_and_the_criteria_table_agree_on_every_a_row():
    """FAILS IF a cell is paraphrased in one artefact: the probe freezes the record, readers read the table."""
    rows = {r["id"]: r for r in check.parse_criteria_rows(PREREG.read_text())}
    by_id = {c["id"]: c for c in _record()["criteria"]}
    for row in A_ROWS:
        assert row in rows, f"{row} has no row in the criteria table"
        for part in ("metric", "denominator", "threshold"):
            assert check._plain(str(by_id[row][part])) == check._plain(rows[row][part]), (
                f"{row}.{part} differs between the record and the table")


def test_the_probe_and_the_gate_name_the_same_a_rows_as_the_record():
    """FAILS IF a row is renamed or dropped in one of the three copies and not the others."""
    probe = _probe()
    from tools import capstone_verdict_check as cvc
    record_ids = {c["id"] for c in _record()["criteria"]}
    assert set(probe.A_CRITERIA) == set(cvc.A_CRITERIA) == set(A_ROWS)
    assert set(A_ROWS) <= record_ids


def test_the_record_pins_the_document_it_describes():
    """FAILS IF the document is edited without the record being re-cut (§8)."""
    want = "sha256:" + hashlib.sha256(PREREG.read_bytes()).hexdigest()
    assert _record()["document_sha256"] == want


# ── 2. the amendment is an amendment ───────────────────────────────────────


def test_the_amendment_quotes_the_superseded_pins_line_verbatim():
    """FAILS IF the A-rows are a quiet overwrite instead of a versioned amendment.

    ⛔ The slice is bounded by the A-rows heading's own TEXT, not by `text.index("### 10.8")`. The
    branch first wrote a §10.8 and then merged `origin/main`, which brought `#10997`'s own §10.8 —
    so an ordinal first-match resolved to the WRONG section and the slice spanned both, letting this
    arm pass on a document whose §10.11 was `#10997`'s cite re-point (#10982 review round 2).

    ⛔ AND THE NUMBER IS PINNED FROM BOTH SIDES, because it has now been taken TWICE. §10.9 was
    claimed by `main`'s `#11292` cite re-point (round 2), so this amendment moved to §10.10; then
    `main`'s `#11368` cite re-point landed its own §10.10 (#11331 rebase), so it moved to §10.11.
    Each time, the failure mode is identical and silent: a forward merge leaves TWO sections
    claiming one number, every pointer to it becomes ambiguous, and a bare `count(...) == 1` is
    satisfied by `main`'s section while this one has vanished. So every number this branch has had
    to step over is asserted present exactly once, and the amendment's own number exactly once.
    """
    text = PREREG.read_text()
    heading = "### 10.11 — five `A`-rows added to the criteria table"
    assert heading in text, "the A-rows amendment is not a §10.11 with its own heading"
    assert text.count("### 10.11") == 1, "two sections claim §10.11 — a pointer to it is ambiguous"
    assert text.count("### 10.10") == 1, (
        "`main`'s §10.10 (`#11368`'s cite re-point) must survive the forward merge — if it is "
        "gone, this amendment has taken a number `main` owns instead of the next free one")
    assert text.count("### 10.9") == 1, "`main`'s §10.9 must survive the forward merge"
    section = text[text.index(heading):]
    section = section[: section.index("## 11.")]
    assert "criteria: [E1, E1b, E2a, E2b, E3, E4, E5]" in section, (
        "the superseded pins line is not quoted — a reader cannot see what changed")
    assert "required_criteria_rows" in section, (
        "the section does not say the required set is unchanged")
    # ⛔ §10.11 is where the next child reads its FLOORS from, and it said **Seven** while listing
    # seven plus two and while §3.7 and the record both carried nine (#10982 review round 4, F3).
    # Checked against the ACTUAL count, not against a word: a re-wording that restores the
    # understatement reds here rather than going stale beside `test_every_pinned_A_floor_…`,
    # which pins the VALUES and cannot see the prose.
    a_floors = [k for k in check.parse_pins(text)["denominator_floors"] if k.startswith("a")]
    assert len(a_floors) == 9, a_floors
    assert re.sub(r"\s+", " ", "**Nine** floors are pinned") in re.sub(r"\s+", " ", section), (
        "§10.11 understates how many A-floors the record pins — a reader scoring A1 from this "
        "section would miss two of them")


def test_the_amendment_entry_names_the_issue_and_the_required_set_is_unmoved():
    """FAILS IF the amendment is filed under the wrong issue, or the required set grew."""
    amendments = _record()["amendments"]
    entry = [a for a in amendments if a.get("issue") == 10982]
    assert len(entry) == 1, [a.get("issue") for a in amendments]
    assert entry[0]["criterion"] == "A1+A2+A3+A4+A5"
    assert entry[0]["measurement_date"] is None, (
        "an amendment that names a measurement date claims an A-row run this change does not have")


def test_no_existing_bar_moved_the_probe_reads_the_same_seven():
    """⛔ An invariant, not a sentence: the probe PARSES the bars out of the record.

    FAILS IF the amendment moved any E-row bar — the property the amendment entry claims — because
    the probe's own `parse_bars` reads them out of this record and nothing else.

    ⛔ `e6_declared_rows` is NOT a moved bar, and the distinction is why this map is exact rather
    than a subset check: `#11673` registered a SIXTH e-floor for a row that did not exist when the
    five above were written. A moved bar is one of the five below changing value; a new row's own
    floor is an addition, and the census that registers it declares it in the same record. The
    exact-map form is what keeps BOTH properties: a changed value still reds here, and an
    unregistered key still reds here, so neither a silent edit nor a silent addition passes.
    """
    probe = _probe()
    bars = probe.parse_bars(_record())
    assert bars["E1"] == {"multiplier": 0.5}
    assert bars["E1b"] == {"required": 90, "of": 90}
    assert bars["E2a"] == {"required": 13, "of": 14}
    assert bars["E2b"] == {"max_fused_share": 0.05}
    assert bars["E3"] == {"required_share": 1.0}
    assert bars["E4"] == {"required": 15, "of": 15}
    assert bars["E5"] == {"ratio": 0.9, "raw_absolute": 0.8}
    assert bars["floors"] == {"e1_objects": 30, "e1_min_before_share": 0.2,
                             "e2b_surviving_units": 30, "e3_derived_nodes": 30,
                             "e5_rate_min_sentences": 10, "e6_declared_rows": 30}


def test_the_amended_record_still_passes_its_own_gate():
    """The acceptance command, git-free and corpus-checked — the cheapest complete run of the half."""
    report = check.run(PREREG_DIR, use_git=False, use_corpus=True)
    assert report["ok"], report["failures"]
    assert {"A1", "A2", "A3", "A4", "A5"} <= {r["id"] for r in report["rows"]}


def test_the_registered_a_corpus_is_the_one_the_probe_REFUSES_to_substitute():
    """FAILS IF the A-rows could be scored on a corpus the record did not pin (`G1`).

    Non-vacuity, both halves. The record registers ONE corpus for both row families (§3.7):
    `a_corpus.path` must be `corpus.path`, and a record whose `a_corpus` points elsewhere must be
    REFUSED — before the fix nothing read `a_corpus` at all, so pointing it at another corpus left
    this arm green (#10982 review, F3: a test named for a guard it never drove). The second half
    drives the `G1` hash itself, so a probe that stopped refusing reds here rather than in prose.
    """
    probe = _probe()
    corpus = _record()["a_corpus"]
    assert corpus["path"] == _record()["corpus"]["path"]
    assert "#10983" in corpus["status"], (
        "the provisional status must name the child that owns the reconciliation")
    # the record's own pins PASS …
    probe.check_corpus(_record())
    # … a SECOND corpus is REFUSED, never scored (§3.7's "one corpus" rule) …
    moved = json.loads(RECORD.read_text())
    moved["a_corpus"]["path"] = "tests/eval/another_corpus"
    with pytest.raises(SystemExit) as refused:
        probe.check_corpus(moved)
    assert "REFUSED" in str(refused.value) and "a_corpus.path" in str(refused.value), (
        str(refused.value))
    # … and the corpus HASH pin still refuses a moved corpus.
    moved = json.loads(RECORD.read_text())
    moved["corpus"]["fixtures_hash"] = "sha256:" + "0" * 64
    with pytest.raises(SystemExit) as refused:
        probe.check_corpus(moved)
    assert "REFUSED" in str(refused.value)


# ── 3. the verdict gate RECOGNISES an A-row without DEMANDING one ──────────


def _verdict_module():
    from tools import capstone_verdict_check as cvc
    return cvc


def _verdict_row(criterion: str) -> dict:
    return {"criterion": criterion, "verdict": "BLOCKED", "evidence_kind": "refusal",
            "value": "unmeasured", "numerator": None, "denominator": None,
            "artifact": None, "blocked_by": None, "note": "registered, not yet scored"}


def test_the_verdict_gate_accepts_a_table_that_carries_an_a_row():
    """FAILS IF A-rows stay unnameable: `V2` used to red any criterion outside E1–E5."""
    cvc = _verdict_module()
    record = {"schema": cvc.SCHEMA,
              "rows": [_verdict_row(c) for c in cvc.CRITERIA] + [_verdict_row("A1")]}
    findings = cvc.check(record, repo=REPO_ROOT, require_green=False)
    rendered = findings.render()
    assert "not an E1–E5" not in rendered, rendered
    # …and V2 must not DEMAND the other four A-rows either: recognised, not required.
    assert "row is MISSING" not in rendered, rendered
    assert not [f for f in findings.items if f[0] == "V2" and "A1" in f[1]], rendered


def test_a_duplicated_a_row_is_refused_exactly_as_a_duplicated_e_row():
    """FAILS IF `V2`'s "decided once, not voted on" stops at `CRITERIA`.

    ⛔ Widening the recognised vocabulary to `A1`–`A5` removed an ACCIDENTAL protection: two `A1`
    rows used to be refused as unknown criteria. The duplicate half of `V2` iterates `CRITERIA`, so
    after the widening a table could carry one GREEN and one RED `A1` with no finding — the row an
    owner reads as "A1 decided" holding two contradictory verdicts (#10982 review round 2). The
    control is the same mutation on `E1`, which must keep reding.
    """
    cvc = _verdict_module()
    base = [_verdict_row(c) for c in cvc.CRITERIA]
    cases = {"A1": [*base, _verdict_row("A1"), _verdict_row("A1")],   # carried row, twice
             "E1": [*base, _verdict_row("E1")]}                        # the control
    for criterion, rows in cases.items():
        findings = cvc.check({"schema": cvc.SCHEMA, "rows": rows},
                             repo=REPO_ROOT, require_green=False)
        dupes = [f for f in findings.items
                 if f[0] == "V2" and f"rows[{criterion}]" in str(f[1])]
        assert dupes, (
            f"a duplicated {criterion} row is accepted — V2's decided-once rule must cover "
            f"every RECOGNISED criterion: {findings.render()}")
        assert any("decided once" in str(f) for f in dupes), dupes


def test_an_a_row_citing_a_closed_blocker_is_refused_by_v12():
    """FAILS IF the new rows are exempt from the freshness rule `V12` applies to every row."""
    cvc = _verdict_module()
    row = dict(_verdict_row("A4"), blocked_by="#10988")
    record = {"schema": cvc.SCHEMA,
              "run": {"status": "COMPLETED"},
              "rows": [dict(_verdict_row(c), verdict="RED", evidence_kind="measurement",
                            value="n/a", numerator=0, denominator=1, artifact="x.log")
                       for c in cvc.CRITERIA] + [row]}
    caught = cvc.check(record, repo=REPO_ROOT, require_green=False,
                       blocker_states={"_checked_at": "2026-11-06T00:00:00Z",
                                       "#10988": {"state": "CLOSED",
                                                  "closed_at": "2026-11-05T00:00:00Z"}})
    assert "V12" in [f[0] for f in caught.items], caught.render()
    opened = cvc.check(record, repo=REPO_ROOT, require_green=False,
                       blocker_states={"_checked_at": "2026-11-06T00:00:00Z",
                                       "#10988": {"state": "OPEN", "closed_at": None}})
    assert "V12" not in [f[0] for f in opened.items], opened.render()


# ── 4. the census reports, and names what it cannot measure ────────────────


class _FakeGraph:
    def __init__(self, rows: dict):
        self._rows = rows
        self.queries: list[str] = []

    def query(self, cypher: str):
        self.queries.append(cypher)
        for needle, payload in self._rows.items():
            if needle in cypher:
                return type("R", (), {"result_set": payload})()
        raise AssertionError(f"unexpected query: {cypher}")


class _FakeProjection:
    def __init__(self, rows: dict):
        self.g = _FakeGraph(rows)


def test_the_pointkind_histogram_keeps_null_as_a_THIRD_state_and_drops_capture_rows():
    """FAILS IF `NULL` (or a BLANK kind) is folded into `statement`, or a turn row counts as typed.

    ⛔ Both folds manufacture a typing win nobody made: a blank `pointKind` is what the projection
    writes for a kindless operator, and turn rows are written `pointKind='event'` — on the committed
    corpus 159 of 354 Points are turn rows, so counting them read ~45% "typed" against the owner's
    0.6% ceiling (#10982 review, finding 4).
    """
    probe = _probe()
    projection = _FakeProjection({
        "coalesce(n.is_episodic, false) OR coalesce(n.is_operator, false)": [[193]],
        "coalesce(n.pointKind, 'NULL')": [["statement", 150], ["NULL", 10], ["", 3],
                                          ["constraint", 88], ["preference", 21]],
    })
    out = probe._a4_pointkind(projection)
    assert out["excluded_capture_rows"] == 193
    assert out["statement"] == 150
    assert out["null"] == 13, "a blank kind is ABSENT, not typed"
    assert out["typed"] == 109 and out["typed_by_kind"] == {"constraint": 88, "preference": 21}
    assert out["claim_points"] == 150 + 13 + 109
    # ⛔ THE FALSIFIER, which the canned rows above cannot supply. `_FakeGraph` matches on the
    # substring `coalesce(n.pointKind, 'NULL')`, so it answers the SAME rows with BOTH exclusion
    # predicates deleted — every number above stays put and the count silently swallows the 159
    # turn-row Points again (#10982 review round 4, F2; the agent-infra#3442 shape: the guard
    # exists, the mutant that reverts the fix does not reach it). Assert the query TEXT, the way
    # the A5 and A1 siblings in this file already do.
    histogram = [q for q in projection.g.queries if "AS k" in q]
    assert len(histogram) == 1, f"expected ONE histogram query, got {len(histogram)}"
    assert "NOT coalesce(n.is_episodic, false)" in histogram[0], (
        "the histogram query dropped the is_episodic exclusion, so capture (turn) rows count as "
        "typed claims — the ~45%-vs-0.6% fold this row exists to prevent")
    assert "NOT coalesce(n.is_operator, false)" in histogram[0], (
        "the histogram query dropped the is_operator exclusion, so operator Points count as typed "
        "claims (#10982 review round 2: `tortoise/quota.py:1133` tests `is_episodic` alone)")


def test_the_chain_join_denominator_is_STATEMENTS_only_and_the_cheap_rate_is_not_the_bar():
    """FAILS IF the denominator widens to every Point, or the cheap rate is passed off as the row."""
    probe = _probe()
    projection = _FakeProjection({
        "WHERE n.pointKind = 'statement' RETURN count(n)": [[15534]],
        "AND n.eventId IS NOT NULL RETURN count(n)": [[184]],
        "AND n.eventId IS NOT NULL AND m.eventId IS NOT NULL": [[5624]],
        "WHERE n.pointKind = 'statement' RETURN count(DISTINCT n)": [[5624]],
    })
    out = probe._a3_chain_join(projection)
    assert out["statements"] == 15534
    assert out["statements_with_an_event_root"] == 184
    assert out["cheap_joined"] == 5624 and out["cross_batch_joined"] == 5624
    assert out["cheap_rate"] == 5624 / 15534
    assert out["event_root_coverage"] == 184 / 15534
    assert out["cross_batch_rate"] == 5624 / 184, (
        "the cross-batch rate divides by the STAMPED subpopulation, not by every statement")
    # the registered definition can never exceed the cheap one — the invariant a broader strict
    # query would break (#10982 review, finding 12).
    if isinstance(out["cross_batch_joined"], int) and isinstance(out["cheap_joined"], int):
        assert out["cross_batch_joined"] <= out["cheap_joined"]
    rendered = "\n".join(probe.a_row_census_lines(_full_census(probe)))
    # ⛔ The CHEAP rate is reported and is NOT the bar. Asserting the phrase alone was a spell-check
    # of the renderer's own constant — it passed against any arithmetic (#10982 review round 2).
    # The property that discriminates: the cheap figure and the registered figure are BOTH rendered,
    # as DIFFERENT numbers, and the note that says which is the bar is attached to them.
    cheap_line = next(line for line in rendered.splitlines() if line.startswith("a3 statements="))
    assert "cheap_rate=0.2" in cheap_line and "cross_batch_rate=0.1" in cheap_line, cheap_line
    assert cheap_line.index("cheap_rate=") < cheap_line.index("cross_batch_rate="), (
        "the registered rate must not be rendered as the cheap one")
    assert "NOT the bar" in rendered, (
        "the cheap rate is rendered with no statement that it is not the bar")


def test_an_unstamped_statement_population_reports_the_cross_batch_leg_as_UNMEASURED():
    """FAILS IF an absent `eventId` reads as "crossed a boundary" — the fail-open of finding 5.

    With no stamp on either end there is no "different Event root" to establish, so the leg is a
    NAMED gap; a `coalesce(eventId,'')` comparison counted those as crossing.
    """
    probe = _probe()
    projection = _FakeProjection({
        "WHERE n.pointKind = 'statement' RETURN count(n)": [[15534]],
        "AND n.eventId IS NOT NULL RETURN count(n)": [[0]],
        "WHERE n.pointKind = 'statement' RETURN count(DISTINCT n)": [[5624]],
    })
    out = probe._a3_chain_join(projection)
    assert out["statements"] == 15534
    assert str(out["cross_batch_joined"]).startswith("UNMEASURED(")
    assert str(out["cross_batch_rate"]).startswith("UNMEASURED(")
    assert "n.eventId IS NOT NULL AND m.eventId IS NOT NULL" not in "".join(projection.g.queries)


def test_a_zero_denominator_reports_UNMEASURED_never_a_number():
    """FAILS IF an empty population is reported as a 0% rate — that is a number, not a gap."""
    probe = _probe()
    projection = _FakeProjection({
        "WHERE n.pointKind = 'statement' RETURN count(n)": [[0]],
        "AND n.eventId IS NOT NULL RETURN count(n)": [[0]],
        "WHERE n.pointKind = 'statement' RETURN count(DISTINCT n)": [[0]],
    })
    out = probe._a3_chain_join(projection)
    assert str(out["cheap_rate"]).startswith("UNMEASURED("), out["cheap_rate"]
    a4 = probe._a4_pointkind(_FakeProjection({
        "coalesce(n.is_episodic, false) OR coalesce(n.is_operator, false)": [[0]],
        "coalesce(n.pointKind, 'NULL')": [],
    }))
    assert str(a4["typed_share"]).startswith("UNMEASURED("), a4["typed_share"]


def test_the_four_subject_families_are_counted_per_family_and_only_statements_carry():
    """FAILS IF a single wired family can read as "Subjects are populated", or a turn row counts."""
    probe = _probe()
    projection = _FakeProjection({
        "MATCH (n:Subject) RETURN count(n)": [[26]],
        # A5's registered denominator promises the Point population BESIDE the Subject count, so
        # the block must carry it (#11331 review round 8, P2-4b).
        "MATCH (n:Point) RETURN count(n)": [[64]],
        "RETURN type(r) AS t, count(DISTINCT r) AS n": [["aboutSubject", 40]],
        "WHERE n.pointKind = 'statement'": [[40]],
    })
    out = probe._a5_subjects(projection)
    assert out["subjects"] == 26
    assert out["point_population"] == 64, (
        "A5 no longer reports the Point population its registered denominator promises beside the "
        f"Subject count — the pinned floors a5_points/a2_candidates would have no field to guard: {out}")
    assert out["families"] == {"aboutSubject": 40, "memberOf": 0,
                               "ownedBy": 0, "managedBy": 0}
    assert out["statements_with_a_subject_edge"] == 40
    # the three properties whose absence published a wrong number or let a turn row satisfy the bar:
    # undirected, `count(DISTINCT r)` (an undirected multi-rel-type pattern is traversed in BOTH
    # orientations, so `count(*)` reports 2x the edges — measured on falkordb-server:v4.20.6), and a
    # statement filter on the carrying leg.
    families_query, carrying_query = projection.g.queries[1], projection.g.queries[2]
    assert "]-()" in families_query and "]->()" not in families_query
    assert "count(DISTINCT r)" in families_query, families_query
    assert "n.pointKind = 'statement'" in carrying_query


def test_object_object_edges_are_reported_by_type_and_by_objectKind():
    """FAILS IF the census reports a bare count, or reads a property the writer never stamps.

    ⛔ `:Object` carries `objectKind` (`tortoise/projection/entities.py:4699`); reading `kind`
    returned NULL on every real graph while the row reported the "by endpoint kind" half as
    measured (#10982 review, finding 3).
    """
    probe = _probe()
    projection = _FakeProjection({
        "RETURN type(r) AS t, count(*) AS n ORDER BY n DESC, t": [["hasPart", 7]],
        "a.objectKind AS ak": [["hasPart", "dev:epic", "dev:issue", 7]],
        "MATCH (n:Object) RETURN count(n)": [[3]],
    })
    out = probe._a1_object_edges(projection)
    assert out["edges_total"] == 7
    assert out["objects"] == 3, (
        "A1's registered floor `a1_objects` divides by the `:Object` population, so the block must "
        "carry it — a floor consumed by no field cannot refuse")
    assert out["by_type"] == [{"relation": "hasPart", "edges": 7}]
    assert out["by_endpoint_kind"][0]["from_kind"] == "dev:epic"
    assert "a.kind AS ak" not in "".join(projection.g.queries), (
        "the census reads a property no writer stamps")
    assert out["by_pinned_family"] == {"hasPart": 7}, out["by_pinned_family"]


def test_the_pinned_a1_family_is_a_WRITTEN_label_not_a_declared_spelling():
    """FAILS IF the pin names a relationship label no writer can stamp — an UNSATISFIABLE bar.

    ⛔ The first draft pinned `("decomposesInto", "hasPart")`, the pack's two DECLARED hierarchy
    predicates. `decomposesInto` is never a stored label: both hierarchy entries
    (`packs/dev/manifest.yaml:192-196`) are written `type: hasPart`, every writer folds
    `composedOf`/`decomposesInto`/`contains`/`wraps` onto it (`tortoise/projection/edges.py:1513`),
    and `hasPart` is in `create_edge`'s own predicate set while `decomposesInto` is not
    (`edges.py:621-632`). The registered bar is "≥ 1 edge for EACH pinned family", so a pin on the
    declared spelling made it UNREACHABLE on every graph this repo can write — `#10984` (C3) would
    have scored A1 RED forever on a correct product (#10982 review round 2).

    Three mutations red it: re-pinning `decomposesInto`; dropping the record↔instrument parity; and
    letting `by_pinned_family` credit a declared spelling that the writer never stores.
    """
    from tortoise.projection.edges import _VALID_EDGE_PREDICATES

    probe = _probe()
    pinned = tuple(_record()["a1_relation_families"])
    assert pinned == probe.A1_RELATION_FAMILIES, (
        "the instrument and the record pin different A1 families — two copies that can disagree")
    for name in probe.A1_RELATION_FAMILIES:
        assert name in _VALID_EDGE_PREDICATES, (
            f"A1 pins {name!r}, which no writer stamps — the bar can never be met")
    assert "decomposesInto" not in probe.A1_RELATION_FAMILIES
    assert "decomposesInto" in probe.A1_RELATION_FAMILY_FOLD["hasPart"], (
        "the fold must still NAME the declared spelling it absorbs, or a reader sees one family "
        "where the pack declares two")
    # …and the fold itself is a THIRD copy (pins fence, machine record, instrument) — the families
    # list got a parity arm and the fold did not, so the record's fold could be edited with the gate
    # and the whole suite green (#10982 review round 3).
    record_fold = {k: list(v) for k, v in _record()["a1_relation_family_fold"].items()}
    assert record_fold == {k: list(v) for k, v in probe.A1_RELATION_FAMILY_FOLD.items()}, (
        "the instrument and the machine record fold different declared spellings onto the pin")
    assert dict(check.parse_pins(PREREG.read_text())["a1_relation_family_fold"]) == record_fold, (
        "the pins fence and the machine record fold different declared spellings onto the pin")
    # ⛔ A graph whose only edges carry the DECLARED spelling must report 0 for the pinned family:
    # `type(r)` is what the query counts, and crediting the declared spelling here is exactly how
    # the first draft's bar read as satisfiable on a vocabulary no writer produces.
    projection = _FakeProjection({
        "RETURN type(r) AS t, count(*) AS n ORDER BY n DESC, t": [["decomposesInto", 7]],
        "a.objectKind AS ak": [["decomposesInto", "dev:epic", "dev:issue", 7]],
        "MATCH (n:Object) RETURN count(n)": [[3]],
    })
    out = probe._a1_object_edges(projection)
    assert out["by_pinned_family"] == {"hasPart": 0}, out["by_pinned_family"]


def test_the_pinned_a5_subject_families_are_the_four_the_record_names():
    """FAILS IF a family is renamed, dropped or added in the instrument alone.

    The four names live in the A5 cell's prose (there is no `a5_subject_families` pin), so the
    instrument's constant is the copy a reviewer has to reconcile by reading — this arm is what
    turns that into a mechanical check.
    """
    probe = _probe()
    cell = next(r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] == "A5")
    assert set(probe.SUBJECT_EDGE_FAMILIES) == {
        name for name in ("aboutSubject", "memberOf", "ownedBy", "managedBy")
        if name in cell["metric"]
    }, "the A5 cell and the instrument disagree about the subject-edge families"
    assert len(probe.SUBJECT_EDGE_FAMILIES) == 4


def test_a_registered_non_bar_must_name_its_owner_decision_and_cannot_borrow_the_floor_clause():
    """FAILS IF a row with NO bar satisfies the "has a bar" arm on the floor beside it.

    ⛔ A4's threshold is `UNMEASURED(the bar is owner-gated on the D3 fork — #10988 and #1022 …)`. It
    passed `check_completeness` only because the cell also contains the floor clause
    `` above the floor of `>= 30` typed statements ``, i.e. the arm read the FLOOR as the bar — a
    false PASS in the completeness gate (§7 item 1; #10982 review round 2). Three mutations red it:
    dropping the owner decision, keeping a comparator outside the floor clause, and restating A4's
    threshold as a bare placeholder number.
    """
    rows = check.parse_criteria_rows(PREREG.read_text())
    a4 = next(r for r in rows if r["id"] == "A4")
    assert check.check_completeness(rows) == []

    def with_threshold(cell: str) -> list[str]:
        return check.check_completeness([dict(r, threshold=cell) if r is a4 else r for r in rows])

    assert with_threshold("UNMEASURED(the bar is owner-gated — `#10988` decides)") == []
    # ⛔ The floor's comparator is accepted in EVERY spelling `COMPARATORS` declares, because the
    # pinned record itself writes floors the unicode way (`(floor ≥ 30 Objects)`,
    # PRE-REGISTRATION.md:268). The hand-written alternation omitted ≤/≥ and refused those rows as a
    # false RED (#10982 review round 4).
    for floor in (">= 30", "≥ 30", "≤ 30", ">=30"):
        assert with_threshold(
            f"UNMEASURED(owner-gated — `#10988` decides) above the floor of `{floor}` typed "
            "statements"
        ) == [], floor
    for bad, needle in (
        ("UNMEASURED(the bar is owner-gated on a fork nobody opened)", "names no issue"),
        ("UNMEASURED(nothing here) `>= 30` typed statements", "outside its floor"),
        # ⛔ THE SAME CELL WITH ONE SPACE BEFORE THE PARENTHESIS — the natural prose spelling, and
        # the one that defeated the arm until round 6: the detector was the literal `UNMEASURED(`,
        # so `UNMEASURED (…)` fell through to the NUMERIC arms and read the floor clause's `>= 30`
        # as its bar. Both mutations below pin the tolerance, so reverting `\s*` reds an arm.
        ("UNMEASURED (nothing here) `>= 30` typed statements", "outside its floor"),
        ("UNMEASURED (owner-gated — `#10988` decides) `>= 30` typed statements", "outside its floor"),
        # ⛔ The cell that defeated the first cut of this arm: it declares a real bar AFTER the floor
        # clause, and a `floor of[^;]*` strip ate it (#10982 review round 3). The strip is now the
        # comparator THE FLOOR OWNS, so a bar stated afterwards is still seen.
        ("UNMEASURED(owner-gated — `#10988` decides) above the floor of `>= 30` typed statements "
         "and the bar it will be scored against is `>= 0.5` typed_share", "outside its floor"),
        # ⛔ …and a bar stated BEFORE the marker, which the arm could not see until round 7: the
        # scan started at `UNMEASURED(`, so a comparator in the PREFIX was never examined and this
        # cell exited OK with an empty finding list (#10982 review round 7). A4 is the one row
        # registered as having NO bar, and its threshold cell is what the run child reads.
        ("`>= 0.006` typed_share — UNMEASURED(the bar is owner-gated — `#10988` decides)",
         "outside its floor"),
    ):
        failures = with_threshold(bad)
        assert any(needle in f for f in failures), (bad, failures)


def test_the_floor_clause_exemption_is_BOUND_to_the_rows_own_pinned_floor():
    """FAILS IF the words "floor of" can install an arbitrary bar on a bar-less row.

    ⛔ The arm's named property is "a registered non-bar cannot also be a bar" (§7 item 1), but it
    decided what a floor clause WAS by the two words in front of the comparator — so
    `UNMEASURED(#10988 decides) floor of >= 0.5 typed_share` exited OK with no finding while the
    same `>= 0.5` without the words was refused: a RECORD-ONLY edit giving A4 a bar, which is the
    thing the `REGISTERED_NON_BAR_ROWS` register exists to make impossible (#11331 review round 8).

    The clause is now identified by its VALUE: only a number the record PINS is a floor (§3.7), so
    the binding both refuses the laundering and accepts the natural spelling of the row's OWN
    floor — `floor ≥ 30`, which the phrase-only pattern refused as a false RED.
    """
    rows = check.parse_criteria_rows(PREREG.read_text())
    floors = check._registered_floor_values(PREREG_DIR)
    assert floors.get("A4") == 30.0, floors
    assert "A1" in floors and "A5" in floors, floors
    # The real record passes WITH the binding — i.e. the binding did not turn the pinned cells red.
    assert check.check_completeness(rows, floors) == []

    a4 = next(r for r in rows if r["id"] == "A4")

    def failures(cell: str) -> list[str]:
        return check.check_completeness(
            [dict(r, threshold=cell) if r is a4 else r for r in rows], floors)

    # (1) A bar laundered behind the words is REFUSED: 0.5 is not A4's pinned floor (30).
    for cell in (
        "UNMEASURED(#10988 decides) floor of >= 0.5 typed_share",
        "UNMEASURED(#10988 decides) floor of ≥ 0.5 typed_share",
        "UNMEASURED(#10988 decides) floor ≥ 0.5 typed_share",
    ):
        assert any("outside its floor" in f for f in failures(cell)), (cell, failures(cell))

    # (2) A4's OWN floor, in every spelling `COMPARATORS` declares, still passes — including the
    #     natural `floor ≥ 30` that a phrase-only pattern false-REDded.
    for clause in ("floor of >= 30", "floor of ≥ 30", "floor >= 30", "floor ≥ 30",
                   "floor of >=30"):
        cell = f"UNMEASURED(#10988 decides) {clause} typed statements"
        assert failures(cell) == [], (cell, failures(cell))


# The one-directional binding the gate uses: a row absent from the map can never have a clause
# stripped, which fails CLOSED rather than open.
def test_a_row_ABSENT_from_the_floor_map_never_has_a_floor_clause_stripped():
    """FAILS IF the absent-floor path lands on the LAUNDERING branch instead of a refusal.

    ⛔ The first cut of this arm asserted the opposite of its own name: it pinned
    `_strip_the_rows_own_floor(…, None) == " x"`, i.e. it DEMONSTRATED that an unbound row strips
    the clause unconditionally — which put the absent-row path back on the laundering branch. The
    composed consequence was measured: deleting `criteria[A4].floor` while writing a bar into
    A4's cell left `capstone_preregistration_check.py` at **exit 0**, while the identical cell with
    the pointer kept exited 1. A record-only edit REMOVED the refusal (#11331 review round 9).

    Three directions are pinned: an unbound row strips NOTHING (fail CLOSED); a bound row strips
    only its own value; and the CHECKER handed an empty map — what an unreadable record yields —
    refuses a barred A4 exactly as it does with the real map.
    """
    assert check._registered_floor_values(Path("/nonexistent-evidence-dir")) == {}
    # An unbound row strips NOTHING — the comparator survives and reds the arm.
    assert check._strip_the_rows_own_floor("floor of >= 0.5 x", None) == "floor of >= 0.5 x"
    assert check._strip_the_rows_own_floor("floor of >= 30 x", None) == "floor of >= 30 x"
    # A pinned row strips only the value the record pins — in the row's own UNIT of measure.
    assert (
        check._strip_the_rows_own_floor("floor of >= 0.5 typed statements", 30.0, "typed statements")
        == "floor of >= 0.5 typed statements"
    )
    assert (
        check._strip_the_rows_own_floor("floor of >= 30 typed statements", 30.0, "typed statements")
        == " typed statements"
    )
    # ⛔ THE SAME NUMBER IN ANOTHER UNIT IS A BAR, NOT THE FLOOR (#11331 review round 10). `>= 30`
    # typed statements is a population count; `>= 30 % typed` is a RATE that reuses the number, and
    # it is the A4-vs-D3 bar the owner has not ruled on.
    assert (
        check._strip_the_rows_own_floor("floor of >= 30 % typed statements", 30.0, "typed statements")
        == "floor of >= 30 % typed statements"
    )
    # …and with NO unit supplied nothing is classifiable, so it fails CLOSED rather than lenient.
    assert (
        check._strip_the_rows_own_floor("floor of >= 30 typed statements", 30.0)
        == "floor of >= 30 typed statements"
    )
    # …and through the CHECKER: an EMPTY map is the unreadable-record / absent-pointer case, and it
    # must refuse a laundered bar rather than excuse it.
    rows = check.parse_criteria_rows(PREREG.read_text())
    a4 = next(r for r in rows if r["id"] == "A4")
    barred = "UNMEASURED(#10988 decides) floor of >= 0.5 typed_share"
    failures = check.check_completeness(
        [dict(r, threshold=barred) if r is a4 else r for r in rows], {})
    assert any("outside its floor" in f for f in failures), (
        "an EMPTY floor map must strip nothing and refuse the bar — this is the path an "
        f"unreadable record takes: {failures}")
    # The real map still accepts the record, so the fail-closed direction did not red the artifact.
    assert check.check_completeness(rows, check._registered_floor_values(PREREG_DIR)) == []

    # ⛔ ROUND 10 — the SAME pinned NUMBER spent as a RATE is still a bar, and only the real map can
    # prove it (the `{}` case above strips nothing for a different reason). `>= 30 % typed` reuses
    # `A4`'s count floor, so binding on the bare number excused exactly the A4-vs-D3 bar the record
    # says may not be invented — with `criteria[A4].floor` left untouched.
    real = check._registered_floor_values(PREREG_DIR)
    assert real.units.get("A4") == "typed statements", real.units
    rate = "UNMEASURED(#10988 decides) floor of >= 30 % typed and compared to no bar)"
    assert any(
        "outside its floor" in f
        for f in check.check_completeness(
            [dict(r, threshold=rate) if r is a4 else r for r in rows], real)
    ), "a RATE reusing the floor's number is a bar, not the floor"


def test_the_floor_exemption_binds_the_WHOLE_unit_phrase_not_a_PREFIX_of_it():
    """FAILS IF a RATE spelled AFTER the row's own floor unit is laundered as the floor.

    ⛔ Round 10 bound the exemption to the row's pinned VALUE **and its floor unit**, but on a
    PREFIX of that unit — so `typed statements per claim` (and `… share`, `… ratio`) started with
    the unit and was exempted, which is the `A4`-vs-`D3` rate bar `#10988`/`#1022` have not ruled
    on, arriving through the other side of the hole `>= 30 % typed` came through. Measured at
    `#11331` review round 11: all three spellings left `check_completeness` with NO finding while
    `criteria[A4].floor` was untouched, i.e. a RECORD-ONLY edit gave `A4` a bar — the one thing
    §7 item 1 says is impossible.

    The clause must now END the cell at the unit: only closing punctuation may follow it. That is an
    ALLOWLIST, deliberately, because a denylist of rate spellings is bypassed by the spelling it
    does not list, and a delimiter is not a licence either (`typed statements, per claim` is
    refused exactly as `typed statements per claim` is). The cost is that the floor clause has to be
    the TAIL of the cell, which is why the pinned `A4` cell is re-worded that way and asserted here.
    """
    rows = check.parse_criteria_rows(PREREG.read_text())
    real = check._registered_floor_values(PREREG_DIR)
    assert real.units.get("A4") == "typed statements", real.units
    # The pinned record — punctuated so the clause ENDS at its unit — is still accepted.
    assert check.check_completeness(rows, real) == []

    a4 = next(r for r in rows if r["id"] == "A4")

    def failures(cell: str) -> list[str]:
        return check.check_completeness(
            [dict(r, threshold=cell) if r is a4 else r for r in rows], real)

    # (1) A rate spelled AFTER the row's own unit is a BAR, however its denominator is named — and
    #     a delimiter before it does not license it, which is the second half of the prefix hole.
    for tail in ("per claim", "share", "ratio", "rate", "proportion", "over claims",
                 "for each claim", "per decided candidate", ", per claim", ", share",
                 ", per decided candidate"):
        cell = f"UNMEASURED(#10988 decides) floor of `>= 30` typed statements {tail}, and no bar"
        assert any("outside its floor" in f for f in failures(cell)), (cell, failures(cell))

    # (2) …and so is ANY continuation that is not closing punctuation: the clause has to END the
    #     cell at the unit, so `typed statements and compared to no bar` (no punctuation) is refused
    #     too. This is the fail-CLOSED direction, and its message says how to fix it rather than only
    #     that it failed.
    undelimited = ("UNMEASURED(#10988 decides) floor of `>= 30` typed statements "
                   "and compared to no bar)")
    found = failures(undelimited)
    assert any("outside its floor" in f for f in found), (undelimited, found)
    assert any("END of the cell" in f for f in found), (
        "the refusal must name the rule a fixer has to satisfy, or the report reads as 'the floor "
        f"is not the floor': {found}")

    # (3) The row's OWN floor, ending the cell at its unit, is still exempted in every spelling — and
    #     it is the CLOSING PUNCTUATION that does it, not a particular mark.
    for ending in ("", ".", ",", ";", ")", " —"):
        cell = f"UNMEASURED(#10988 decides) floor of `>= 30` typed statements{ending}"
        assert failures(cell) == [], (cell, failures(cell))

    # (4) The PINNED cell is written so its floor clause ENDS it: reverting §10.13 turns the record
    #     itself RED, so the document edit is load-bearing rather than tidying.
    cell = a4["threshold"]
    assert cell.rstrip().endswith("`>= 30` typed statements)"), (
        f"A4's pinned cell must END at its floor clause's unit (§10.13): {cell!r}")
    plain = check._plain(cell)
    assert check._strip_the_rows_own_floor(plain, 30.0, "typed statements") != plain, (
        "A4's own cell is not exempted at all — the gate would refuse the shipped record")
    laundered = plain.replace("typed statements)", "typed statements per claim)")
    assert check._strip_the_rows_own_floor(laundered, 30.0, "typed statements") == laundered, (
        "a rate appended after A4's own unit must NOT be stripped as the floor")

    # (5) The direct binding, at the function the composed cases above go through.
    for text in ("floor of >= 30 typed statements per claim",
                 "floor of >= 30 typed statements, per claim",
                 "floor of >= 30 typed statements share",
                 "floor of >= 30 typed statements and compared to no bar"):
        assert check._strip_the_rows_own_floor(text, 30.0, "typed statements") == text, text
    assert check._strip_the_rows_own_floor(
        "floor of >= 30 typed statements)", 30.0, "typed statements") == " typed statements)"


def test_a_REQUIRED_row_cannot_declare_itself_a_non_bar():
    """FAILS IF the `UNMEASURED(...)` exemption is reachable from a required row.

    ⛔ The exemption `continue`s past BOTH numeric arms, so an UNSCOPED branch let `E1`–`E5` declare
    themselves bar-less and the gate still printed OK — a false PASS introduced by the arm meant to
    close one, and a direct contradiction of §7 item 1's "every criteria row names a metric, a
    denominator and a numeric threshold" (#10982 review round 5). Two directions are pinned: a
    non-registered row that claims the exemption is REFUSED, and the exemption set can never
    intersect `REQUIRED_CRITERIA`.
    """
    assert not (check.REGISTERED_NON_BAR_ROWS & set(check.REQUIRED_CRITERIA)), (
        "a required row must never be registered as a non-bar")
    rows = check.parse_criteria_rows(PREREG.read_text())
    assert check.check_completeness(rows) == []
    e1 = next(r for r in rows if r["id"] == "E1")
    for cell in (
        "UNMEASURED(the bar is owner-gated — `#10988` decides)",
        "UNMEASURED(owner-gated — `#10988` decides) above the floor of `>= 30` typed statements",
        # ⛔ The SPACED spelling of the same two cells. `E1` is a REQUIRED row, so a detector that
        # misses the space lets the record's own floor clause stand in for its bar and the gate
        # prints OK — the exact false PASS this arm exists to close (#11331 review round 6).
        "UNMEASURED (the bar is owner-gated — `#10988` decides)",
        "UNMEASURED (owner-gated — `#10988` decides) above the floor of `>= 30` typed statements",
    ):
        failures = check.check_completeness(
            [dict(r, threshold=cell) if r is e1 else r for r in rows])
        assert any("registers as a non-bar" in f for f in failures), (cell, failures)
    # …and the reverse direction: a row the code exempts must still BE bar-less in the record.
    a4 = next(r for r in rows if r["id"] == "A4")
    failures = check.check_completeness(
        [dict(r, threshold="`>= 0.5` typed_share") if r is a4 else r for r in rows])
    assert any("registered as a non-bar" in f for f in failures), failures


def test_the_registered_non_bar_register_is_CLOSED_and_disclosed():
    """FAILS IF the non-bar exemption can be widened by a record-only edit, or is undisclosed.

    ⛔ The exemption skips BOTH numeric arms, so which rows may claim it is a TOOL pin, not a free
    permission: a lane amending §3.1 under §8 gets refused until it edits the constant in the same
    PR. That is only fair if the record SAYS so, which is what the second half of this arm checks
    (#10982 review round 6).
    """
    assert {"A4"} == check.REGISTERED_NON_BAR_ROWS, (
        "the closed register changed — this test is the record of what the record discloses")
    md = PREREG.read_text()
    assert "REGISTERED_NON_BAR_ROWS" in md, (
        "§7 item 1 must name the register, or a lane discovers the rule by failing")
    # …and a non-required A-row is refused with a message that does not pretend it is `E`-row-shaped:
    rows = check.parse_criteria_rows(md)
    a5 = next(r for r in rows if r["id"] == "A5")
    failures = check.check_completeness(
        [dict(r, threshold="UNMEASURED(`#10988` decides)") if r is a5 else r for r in rows])
    msg = " ".join(failures)
    assert "registers as a non-bar" in msg, failures
    assert "required row" not in msg, (
        "A5 is NOT a required row — the message must not tell it to write a bar it may not own")
    assert "REGISTERED_NON_BAR_ROWS" in msg, failures


def _full_census(probe) -> dict:
    return {
        "criteria": list(probe.A_CRITERIA),
        "A1": {"by_type": [], "by_endpoint_kind": [], "edges_total": 0, "objects": 0,
               "by_pinned_family": {name: 0 for name in probe.A1_RELATION_FAMILIES},
               "rollup_queries": probe.unmeasured("a rollup query")},
        "A2": {"attach": probe.unmeasured("a counter"),
               "mint": probe.unmeasured("a counter"),
               "held": probe.unmeasured("a counter"),
               "candidates": probe.unmeasured("a counter"),
               "decided_candidates": probe.unmeasured("a counter"),
               "attach_share": probe.unmeasured("a counter")},
        "A3": {"statements": 10, "statements_with_an_event_root": 10,
               "cheap_joined": 2, "cheap_rate": 0.2, "event_root_coverage": 0.5,
               "cross_batch_joined": 1, "cross_batch_rate": 0.1, "relations": ["IMPL", "NAND"]},
        "A4": {"claim_points": 10, "excluded_capture_rows": 0, "typed": 1, "statement": 8,
               "null": 1, "typed_share": 0.1, "typed_by_kind": {"constraint": 1}},
        "A5": {"subjects": 2, "named_actors": probe.unmeasured("the corpus's actor list"),
               "point_population": 40,
               "families": {"aboutSubject": 1, "memberOf": 0,
                                           "ownedBy": 0, "managedBy": 0},
               "statements_with_a_subject_edge": 1},
        "TAGGED": {"edges": 0, "note": probe.TAGGED_NOTE},
    }


def test_an_absent_census_renders_a_named_gap_never_nothing():
    """FAILS IF a census that did not run prints nothing — silence reads as "no findings"."""
    probe = _probe()
    assert probe.a_row_census_lines(None) == [
        "a-rows: UNMEASURED(the A-row census did not run in this pass)"]
    failed = probe.a_row_census_lines({"error": "ConnectionRefused"})
    assert "UNMEASURED" in failed[0] and "ConnectionRefused" in failed[0]


def test_the_rendered_block_emits_every_row():
    """FAILS IF a row is dropped from the log, or a registered PART of one is.

    ⛔ A row PRESENT is not the same as a row COMPLETE. A2's block emitted only ``attach``/``mint``
    while its registration names a third arm (``held``, whose silence is RED), the DECIDED-candidate
    population its bar divides by, the all-candidate count reported beside it, and ``attach_share``
    — so the registered RED leg was unevaluable from the block a scoring child reads (#11331 review
    round 8, P2-1). The renderer is the artifact; this arm reads it, not the dict it came from.
    """
    probe = _probe()
    rendered = "\n".join(probe.a_row_census_lines(_full_census(probe)))
    for row in ("a1 ", "a2 ", "a3 ", "a4 ", "a5 ", "TAGGED_edges=0"):
        assert row in rendered, rendered
    assert "UNMEASURED(" in rendered
    # ⛔ `"candidates="` IS A SUBSTRING OF `"decided_candidates="`, so a substring test cannot see
    # the all-candidate count go missing — measured at #11331 review round 9: deleting
    # `candidates={a2['candidates']}` from the renderer left the substring form GREEN. The key is
    # matched at a word boundary that `_` does not satisfy, so `decided_candidates=` cannot stand
    # in for `candidates=`.
    a2_line = rendered.split("a2 ", 1)[1].split("\n", 1)[0]
    for part in ("attach", "mint", "held", "candidates", "decided_candidates",
                 "attach_share"):
        assert re.search(rf"(?<![\w-]){re.escape(part)}=", a2_line), (
            f"the a2 line does not render {part}= — the block would carry a registered part the "
            f"log never shows:\n{a2_line}")
    # A5's registered denominator promises the Point population BESIDE the Subject count; a block
    # field the log never renders is one a scorer never reads (#11331 review round 8, P2-4b).
    assert "point_population=40" in rendered.split("a5 ", 1)[1].split("\n", 1)[0], rendered


def test_the_PRODUCTION_tagged_note_is_the_not_a_target_annotation(monkeypatch):
    """⛔ ON THE PRODUCTION PATH, never on a fixture that supplies the marker (#10982 review, 12).

    FAILS IF `a_row_census` starts presenting `TAGGED` as a target — the fixture-built block in
    `_full_census` injects `TAGGED_NOTE` itself, so only this test can see that.
    """
    probe = _probe()
    empty = _FakeProjection({
        "MATCH (a:Object)-[r]->(b:Object) RETURN type(r) AS t, a.objectKind AS ak": [],
        "MATCH (a:Object)-[r]->(b:Object) RETURN type(r) AS t, count(*) AS n": [],
        "WHERE n.pointKind = 'statement' RETURN count(n)": [[0]],
        "AND n.eventId IS NOT NULL RETURN count(n)": [[0]],
        "WHERE n.pointKind = 'statement' RETURN count(DISTINCT n)": [[0]],
        "coalesce(n.is_episodic, false) OR coalesce(n.is_operator, false)": [[0]],
        "coalesce(n.pointKind, 'NULL')": [],
        "MATCH (n:Subject) RETURN count(n)": [[0]],
        "MATCH (n:Point) RETURN count(n)": [[0]],
        "RETURN type(r) AS t, count(DISTINCT r) AS n": [],
        "WHERE n.pointKind = 'statement'": [[0]],
    })
    monkeypatch.setattr(probe, "FalkorProjection", None, raising=False)
    from tortoise import projection as projection_module
    monkeypatch.setattr(projection_module.FalkorProjection, "from_uri",
                        classmethod(lambda cls, uri: empty))
    census = probe.a_row_census("docker://h:1/g")
    assert census["TAGGED"]["note"] == probe.TAGGED_NOTE
    assert "NOT a target" in census["TAGGED"]["note"]
    # …and one broken row does not discard the others (findings 2 and 11). The break must be on the
    # GRAPH (the projection delegates to `projection.g.query`), not on the projection.
    class _A4BreaksGraph(_FakeGraph):
        def query(self, cypher):
            if "coalesce(n.is_episodic, false) OR coalesce(n.is_operator, false)" in cypher:
                raise RuntimeError("boom")
            return super().query(cypher)

    class _A4Breaks(_FakeProjection):
        def __init__(self, rows):
            self.g = _A4BreaksGraph(rows)

    monkeypatch.setattr(projection_module.FalkorProjection, "from_uri",
                        classmethod(lambda cls, uri: _A4Breaks(dict(empty.g._rows))))
    census = probe.a_row_census("docker://h:1/g")
    assert str(census["A4"]["typed_share"]).startswith("UNMEASURED(")
    # ⛔ a thrown row publishes NO numeric measurement under a key a real measurement occupies —
    # otherwise a consumer reads "measured absence" and scores the row RED on an infra failure.
    for key in ("claim_points", "typed", "statement", "null"):
        assert str(census["A4"][key]).startswith("UNMEASURED("), (key, census["A4"][key])
    assert census["A5"]["subjects"] == 0, "a broken A4 row discarded A5's measured number"


# ── 4b. the census block is COMMITTED, so it carries no credentials ────────

#: A credentialed URI in the shape this probe is actually handed: the fleet's lane URI is
#: `docker://:pw@host:port/graph`, and the hosted `live-v4` graph adds user and password the same
#: way (`from_uri` semantics). The password is the sentinel every assertion below looks for.
CREDENTIALED_URI = "docker://:hunter2@live-v4.internal:6379/capstone_measure_abcd1234"

#: The query table for a graph with NOTHING in it: every `A`-row query answers zero, so the census
#: completes and a test that uses it is about the block's SHAPE, never about a thrown row.
_EMPTY_ROWS: dict = {
    "MATCH (a:Object)-[r]->(b:Object) RETURN type(r) AS t, a.objectKind AS ak": [],
    "MATCH (a:Object)-[r]->(b:Object) RETURN type(r) AS t, count(*) AS n": [],
    "MATCH (n:Object) RETURN count(n)": [[0]],
    "WHERE n.pointKind = 'statement' RETURN count(n)": [[0]],
    "AND n.eventId IS NOT NULL RETURN count(n)": [[0]],
    "WHERE n.pointKind = 'statement' RETURN count(DISTINCT n)": [[0]],
    "coalesce(n.is_episodic, false) OR coalesce(n.is_operator, false)": [[0]],
    "coalesce(n.pointKind, 'NULL')": [],
    "MATCH (n:Subject) RETURN count(n)": [[0]],
    "MATCH (n:Point) RETURN count(n)": [[0]],
    "RETURN type(r) AS t, count(DISTINCT r) AS n": [],
    "WHERE n.pointKind = 'statement'": [[0]],
}


def _census_over(monkeypatch, probe, graph, uri: str = CREDENTIALED_URI) -> dict:
    """`a_row_census` with the projection faked — no DB, no container, no network."""
    from tortoise import projection as projection_module

    class _Projection:
        def __init__(self):
            self.g = graph

    monkeypatch.setattr(projection_module.FalkorProjection, "from_uri",
                        classmethod(lambda cls, _uri: _Projection()))
    return probe.a_row_census(uri)


def test_the_census_block_carries_no_connection_uri_at_all(monkeypatch):
    """⛔ FAILS IF the block gains a `uri` key — the committed-log credential leak (#10982 review F1).

    The block is `json.dumps`-ed WHOLE into `capstone-measured-<sha>.log`, which is committed to a
    public repository, so a URI on it is the graph's host, user and password in git. Not even a
    redacted one: traceability is `run.graph`, which the record already carries.
    """
    probe = _probe()
    census = _census_over(monkeypatch, probe, _FakeGraph(_EMPTY_ROWS))
    assert "uri" not in census, "the machine record embeds this dict whole — no URI key, redacted or not"
    blob = json.dumps(census)
    assert "hunter2" not in blob, blob
    assert CREDENTIALED_URI not in blob, blob
    assert "hunter2" not in "\n".join(probe.a_row_census_lines(census))


def test_a_census_row_that_throws_does_not_echo_the_credentials(monkeypatch):
    """⛔ FAILS IF a driver/projection exception is emitted VERBATIM into the committed log.

    A refused connection is the likeliest way this block meets a real exception, and a driver
    message quotes the URI it could not reach — `f"{exc}"` was the second path, beside the `uri`
    key, by which the password reached the artefact.
    """

    class _Breaks(_FakeGraph):
        def query(self, cypher: str):
            raise RuntimeError(f"ConnectionRefused: could not reach {CREDENTIALED_URI}")

    probe = _probe()
    census = _census_over(monkeypatch, probe, _Breaks({}))
    blob = json.dumps(census)
    assert "hunter2" not in blob, blob
    assert CREDENTIALED_URI not in blob, blob
    # …and the failure is still NAMED with its cause: a scrub that hides the row is the other defect.
    assert "UNMEASURED(" in blob and "ConnectionRefused" in blob, blob
    assert "hunter2" not in "\n".join(probe.a_row_census_lines(census))


def test_the_census_error_reason_scrubs_the_uri_userinfo():
    """FAILS IF `census_reason` stops scrubbing — it is the `main()` fallback's only message."""
    probe = _probe()
    reason = probe.census_reason(RuntimeError(f"cannot connect to {CREDENTIALED_URI}"))
    assert "hunter2" not in reason, reason
    assert "***" in reason, reason
    assert reason.startswith("RuntimeError: "), reason
