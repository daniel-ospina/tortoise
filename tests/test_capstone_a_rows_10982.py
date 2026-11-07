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
2. **the amendment is an amendment** — §10.9 quotes the superseded pins line verbatim, so the change
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


def test_the_a_rows_are_registered_but_not_REQUIRED():
    """⛔ The load-bearing half: adding a row to the record is not adding it to the gate.

    FAILS IF `required_criteria_rows` grows — that would demand `A1`–`A5` of every verdict table,
    including the committed one, for rows nobody has run yet (`#10984`–`#10988` own those runs).
    """
    assert check.REQUIRED_CRITERIA == ("E1", "E2", "E3", "E4", "E5")
    text = PREREG.read_text()
    assert "required_criteria_rows: [E1, E2, E3, E4, E5]" in text
    pins = check.parse_pins(text)
    assert pins["required_criteria_rows"] == ["E1", "E2", "E3", "E4", "E5"]
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
    arm pass on a document whose §10.9 was `#10997`'s cite re-point (#10982 review round 2).
    """
    text = PREREG.read_text()
    heading = "### 10.9 — five `A`-rows added to the criteria table"
    assert heading in text, "the A-rows amendment is not a §10.9 with its own heading"
    assert text.count("### 10.9") == 1, "two sections claim §10.9 — a pointer to it is ambiguous"
    section = text[text.index(heading):]
    section = section[: section.index("## 11.")]
    assert "criteria: [E1, E1b, E2a, E2b, E3, E4, E5]" in section, (
        "the superseded pins line is not quoted — a reader cannot see what changed")
    assert "required_criteria_rows" in section, (
        "the section does not say the required set is unchanged")


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
                             "e5_rate_min_sentences": 10}


def test_the_amended_record_still_passes_its_own_gate():
    """The acceptance command, git-free and corpus-checked — the cheapest complete run of the half."""
    report = check.run(PREREG_DIR, use_git=False, use_corpus=True)
    assert report["ok"], report["failures"]
    assert {"A1", "A2", "A3", "A4", "A5"} <= {r["id"] for r in report["rows"]}


def test_the_registered_a_corpus_is_the_one_the_probe_REFUSES_to_substitute():
    """FAILS IF the A-rows could be scored on a corpus the record did not pin (`G1`).

    Non-vacuity: this drives the probe's own `check_corpus`, so a record whose `a_corpus` pointed
    somewhere else — or a probe that stopped refusing — reds here rather than in prose.
    """
    probe = _probe()
    corpus = _record()["a_corpus"]
    assert corpus["path"] == _record()["corpus"]["path"]
    assert "#10983" in corpus["status"], (
        "the provisional status must name the child that owns the reconciliation")
    # the record's own pin PASSES …
    probe.check_corpus(_record())
    # … and a different corpus is REFUSED, never scored.
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
        "RETURN type(r) AS t, count(DISTINCT r) AS n": [["aboutSubject", 40]],
        "WHERE n.pointKind = 'statement'": [[40]],
    })
    out = probe._a5_subjects(projection)
    assert out["subjects"] == 26
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

    ⛔ `:Object` carries `objectKind` (`tortoise/projection/entities.py:4675`); reading `kind`
    returned NULL on every real graph while the row reported the "by endpoint kind" half as
    measured (#10982 review, finding 3).
    """
    probe = _probe()
    projection = _FakeProjection({
        "RETURN type(r) AS t, count(*) AS n ORDER BY n DESC, t": [["hasPart", 7]],
        "a.objectKind AS ak": [["hasPart", "dev:epic", "dev:issue", 7]],
    })
    out = probe._a1_object_edges(projection)
    assert out["edges_total"] == 7
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
    # ⛔ A graph whose only edges carry the DECLARED spelling must report 0 for the pinned family:
    # `type(r)` is what the query counts, and crediting the declared spelling here is exactly how
    # the first draft's bar read as satisfiable on a vocabulary no writer produces.
    projection = _FakeProjection({
        "RETURN type(r) AS t, count(*) AS n ORDER BY n DESC, t": [["decomposesInto", 7]],
        "a.objectKind AS ak": [["decomposesInto", "dev:epic", "dev:issue", 7]],
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
    for bad, needle in (
        ("UNMEASURED(the bar is owner-gated on a fork nobody opened)", "owner decision"),
        ("UNMEASURED(nothing here) `>= 30` typed statements", "outside its floor"),
    ):
        failures = with_threshold(bad)
        assert any(needle in f for f in failures), (bad, failures)


def _full_census(probe) -> dict:
    return {
        "criteria": list(probe.A_CRITERIA),
        "A1": {"by_type": [], "by_endpoint_kind": [], "edges_total": 0,
               "by_pinned_family": {name: 0 for name in probe.A1_RELATION_FAMILIES},
               "rollup_queries": probe.unmeasured("a rollup query")},
        "A2": {"attach": probe.unmeasured("a counter"), "mint": probe.unmeasured("a counter")},
        "A3": {"statements": 10, "statements_with_an_event_root": 10,
               "cheap_joined": 2, "cheap_rate": 0.2, "event_root_coverage": 0.5,
               "cross_batch_joined": 1, "cross_batch_rate": 0.1, "relations": ["IMPL", "NAND"]},
        "A4": {"claim_points": 10, "excluded_capture_rows": 0, "typed": 1, "statement": 8,
               "null": 1, "typed_share": 0.1, "typed_by_kind": {"constraint": 1}},
        "A5": {"subjects": 2, "named_actors": probe.unmeasured("the corpus's actor list"), "families": {"aboutSubject": 1, "memberOf": 0,
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
    """FAILS IF a row is dropped from the log."""
    probe = _probe()
    rendered = "\n".join(probe.a_row_census_lines(_full_census(probe)))
    for row in ("a1 ", "a2 ", "a3 ", "a4 ", "a5 ", "TAGGED_edges=0"):
        assert row in rendered, rendered
    assert "UNMEASURED(" in rendered


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
