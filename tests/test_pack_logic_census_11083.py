"""#11083 — the per-pack relation census, hermetic half (no DB, no network, no model).

The sixth capstone row asks: **does the writer produce the connections the packs
declare?** A census is the instrument, and an instrument that cannot disagree with
reality is decoration. So this file holds the artifact to a LIVE re-derivation and
holds the vocabulary to the rulings — nothing here reads a number a human typed.

What is pinned, and the failure each pin catches:

  H1  the committed artifact equals a live re-derivation of every declared-side
      cell — a hand-edited artifact, and any declaration change that does not
      move the census. The artifact rots silently otherwise.
  H2  the denominator is DERIVED (`Σ relations + Σ hierarchies`) per pack and in
      total, and every pack appears with BOTH surface keys — a relations-only
      census (this issue's own stated failure: it under-counts 6 of 42) and a
      pack silently dropped.
  H3  ruling (b) as it now stands: `produces` is a MEANING (`<-`, IMPL) and has
      left `SEMANTICS_TRANSITION`; `funds` is NOT a meaning and STAYS retired —
      the withdrawn 2026-11-05 promotion must not come back.
  H4  every meaning in `SEMANTICS_VALUES` is ADVERTISED in `OUTPUT_CONTRACT` and
      in its derived core-only variant. Only `advertised ⊆ accepted` was checked
      before, never `accepted ⊆ advertised`, so a meaning could exist in the gate
      and be un-emittable.
  H5  every `declarationOnly: true` row carries a printable reason, and no
      shipped row is `predicate-not-in-allowlist` without a disposition.
  H6  `producedBy` and the `Program → Asset` `hasPart` path are both present and
      NEITHER is flagged duplicate — a CONTROL: it must not fire.
  H7  the census's hierarchy rows are exactly `pack_hierarchy.declared_hierarchy()`'s
      — a second, prose-based parser in the census would be the `#11001` defect.
  H8  a bad relation `direction` and an empty `declarationOnlyReason` WARN and do
      not drop the pack. `_load_one` raises on any `_validate` error and
      `load_all` then DELETES the pack, so a vocabulary-shaped field in the
      pack-dropping class would drop a whole pack over one typo.
  H9  a row whose union verdict claims `structural` while the structural WRITER
      refuses the predicate is FLAGGED, proven on a fixture relation — the
      `pack_hierarchy._binding` class, latent in the shipped catalog.

Hermetic by construction: this file imports no SDK and touches no graph.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tortoise import pack_registry  # noqa: E402
from tortoise.pack_registry import (  # noqa: E402
    RELATION_DECLARATION_ONLY,
    RELATION_PREDICATE_NOT_IN_ALLOWLIST,
    SEMANTICS_DIRECTIONS,
    SEMANTICS_MECHANISMS,
    SEMANTICS_TRANSITION,
    SEMANTICS_VALUES,
    effective_direction,
)

ARTIFACT = REPO_ROOT / "docs" / "evidence" / "11083" / "pack-relation-census.json"


def _census(**kwargs):
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import pack_relation_census as census

    return census


@pytest.fixture(scope="module")
def artifact() -> dict:
    assert ARTIFACT.exists(), (
        f"{ARTIFACT.relative_to(REPO_ROOT)} is not committed — the sixth capstone "
        "row reads this artifact, so its absence is the gap, not a detail"
    )
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def fresh() -> dict:
    return _census().build_census(REPO_ROOT / "packs")


# ── H1 — the artifact equals a live re-derivation ────────────────────────────
def test_h1_committed_artifact_equals_a_live_rederivation(artifact, fresh):
    """A hand-edited artifact, and any declaration change that does not move the
    census. `written` is excluded on purpose: it is a GRAPH fact, compared by the
    write-probe gate instead of by a check nobody runs."""
    diffs = _census()._diff_hermetic(artifact, fresh)
    assert diffs == [], "the committed census has drifted from the packs:\n" + "\n".join(diffs)


def test_h1b_the_artifact_records_its_own_provenance(artifact):
    """A census with no re-derivation line is a number with no owner."""
    assert artifact["issue"] == 11083
    assert "--write-probe" in artifact["generated_by"], (
        "the committed artifact must be the WRITTEN one — a hermetic-only census "
        "has no `written` column, which is the column the sixth row is about"
    )


def test_h1c_the_check_cli_reports_drift_and_agreement(monkeypatch, tmp_path, capsys):
    """`--check`'s EXIT CODE is the contract the README tells readers to rely on.

    Nothing invoked `_main(["--check"])` before: `test_h1` calls `_diff_hermetic`
    directly, so making the `--check` branch `return 0` unconditionally kept the
    whole suite green while `DRIFT: n cell(s)` could never be reported. All three
    exits are asserted here, so neither an always-0 nor an always-1 branch
    survives.
    """
    census = _census()
    # 0 — the committed artifact agrees with a live re-derivation.
    assert census._main(["--check"]) == 0, capsys.readouterr()

    # 1 — a doctored copy differs, and the drift is NAMED.
    doctored = json.loads(ARTIFACT.read_text())
    doctored["population"]["declared"] = doctored["population"]["declared"] + 1
    target = tmp_path / "doctored.json"
    target.write_text(json.dumps(doctored))
    monkeypatch.setattr(census, "ARTIFACT_REL", str(target))
    assert census._main(["--check"]) == 1, (
        "`--check` returned success on an artifact that disagrees with the packs "
        "— the DRIFT contract has no reddening row")
    assert "DRIFT: 1 cell" in capsys.readouterr().err

    # 2 — a missing artifact is its own exit, not a silent pass.
    monkeypatch.setattr(census, "ARTIFACT_REL", str(tmp_path / "absent.json"))
    assert census._main(["--check"]) == 2


# ── H2 — the population is derived, and both surfaces are present ────────────
def test_h2_population_is_derived_not_remembered(artifact):
    """A hard-coded 42 becomes a FALSE denominator the moment a disposition
    changes (§11083 R8), and a relations-only census under-counts by 14%."""
    sections = artifact["packs"]
    rel = sum(len(s["relations"]) for s in sections.values())
    hier = sum(len(s["hierarchies"]) for s in sections.values())
    pop = artifact["population"]
    assert pop["relations"] == rel
    assert pop["hierarchies"] == hier
    assert pop["declared"] == rel + hier, (
        "the declared population must be Σ relations + Σ hierarchies, derived")
    assert pop["declared"] == 42, (
        "the declared population MOVED — update every downstream reference "
        "(the issue body, the capstone row) rather than this assertion alone, "
        "and check the move was a declaration change and not a deletion")
    for ns, section in sections.items():
        assert "relations" in section and "hierarchies" in section, (
            f"{ns} is missing a surface key — a zero must be an explicit 0, not "
            "an absent key a reader could mistake for 'not measured'")
        assert section["declared"] == len(section["relations"]) + len(section["hierarchies"])


def test_h2b_no_shipped_row_is_unexplained(artifact):
    """The Done criterion, numerically: unbound-and-unexplained == 0."""
    unexplained = [
        (ns, r["predicate"])
        for ns, section in artifact["packs"].items()
        for r in section["relations"]
        if r["verdict"] == RELATION_PREDICATE_NOT_IN_ALLOWLIST
    ]
    assert unexplained == [], (
        "a declared relation binds no write path and says nothing about why: "
        f"{unexplained} — resolve it or mark it `declarationOnly: true` (#2766)")
    counts = artifact["verdicts"]
    assert counts[RELATION_PREDICATE_NOT_IN_ALLOWLIST] == 0


# ── H3 — the vocabulary rulings, as they now stand ───────────────────────────
def test_h3_produces_is_a_meaning_and_left_the_transition_map():
    assert "produces" in SEMANTICS_VALUES
    assert SEMANTICS_DIRECTIONS["produces"] == "<-", (
        "production runs producer → product while the shipped declaration is "
        "asset → program, so `->` asserts the wrong cause")
    assert SEMANTICS_MECHANISMS["produces"] == "IMPL"
    assert "produces" not in SEMANTICS_TRANSITION, (
        "`produces` folded onto `causes` changed the meaning AND inverted the "
        "direction — it must not be a retired spelling (#11083, OVERRIDES on #7848)")


def test_h3b_funds_stays_a_label_and_is_NOT_a_meaning():
    """The WITHDRAWN ruling must not come back (#7848's correction, 2026-11-08).

    #2766's three-layer test: nothing in the machine acts on a relation being a
    funding relation, so `funds` is Layer 3. This test exists because the issue
    body still asks for the opposite, and the body is what a lane reads first.
    """
    assert "funds" not in SEMANTICS_VALUES, (
        "#7848's 2026-11-08 correction withdrew `funds` from SEMANTICS_VALUES — "
        "it is a Layer-3 label riding an existing meaning")
    assert "funds" not in SEMANTICS_DIRECTIONS
    assert SEMANTICS_TRANSITION.get("funds") == "transacts", (
        "`funds` stays a retired spelling: the label rides the canonical meaning")


def test_h3c_every_meaning_has_a_direction_and_a_token_from_the_model(artifact):
    assert set(SEMANTICS_DIRECTIONS) == set(SEMANTICS_VALUES)
    assert set(SEMANTICS_DIRECTIONS.values()) <= {"->", "<-", "<->", "-"}


def test_h3d_the_effective_direction_of_a_row_reports_the_DECLARATION():
    """`effective_direction` is the declaration-side reader ruling (c) needs.

    A declared per-edge `direction` outranks the meaning (with `ownedBy`'s target
    left open, the predicate can no longer say which end owns which); otherwise
    the meaning's direction resolves through the SAME transition → table chain the
    write path uses, so the census and the writer cannot disagree about a row.
    """
    assert effective_direction({"semantics": "produces"}) == "<-"
    assert effective_direction({"semantics": "funds"}) == "-"       # via transacts
    assert effective_direction({"semantics": "holds"}) == "->"      # via ownedBy
    assert effective_direction(
        {"semantics": "holds", "direction": "<-"}) == "<-", (
        "the declared per-edge sense must outrank the meaning — otherwise "
        "`venture:holds` is reported as the company owning the investment")
    assert effective_direction({}) is None, (
        "absence is not a default: a reader must decide what it means")
    assert effective_direction({"direction": "sideways"}) is None, (
        "an invalid token is not a direction — it falls through to the meaning, "
        "and with no meaning it is None, never a plausible `->`")


def test_h3e_a_LEGACY_token_is_honoured_exactly_as_the_writer_honours_it():
    """The one-normalizer rule (#7852) inside the declaration-side reader.

    `create_operator` accepts SIX direction tokens — the four model values plus
    the legacy `unidirectional`/`bidirectional` aliases — and `live.py` forbids
    re-spelling that set at a call site. A reader that tested membership in the
    four model values alone therefore WARNED-AND-IGNORED `unidirectional` while
    the writer honoured it as `->`: for a row whose meaning is `produces` (table
    `<-`), the census reported `<-` and the graph stored `->`. One wrong sign on
    the very cell this PR exists to make trustworthy.
    """
    assert effective_direction(
        {"semantics": "produces", "direction": "unidirectional"}) == "->", (
        "`unidirectional` IS `->`; reporting the meaning's `<-` here would put the "
        "census and `create_operator` on opposite sides of one row")
    assert effective_direction(
        {"semantics": "produces", "direction": "bidirectional"}) == "<->"
    # The four model values are unaffected, and a declared token still outranks
    # the meaning (H3d's rule) — the normalizer must not widen what is honoured.
    assert effective_direction({"semantics": "produces", "direction": "<-"}) == "<-"


# ── H4 — every accepted meaning is ADVERTISED ────────────────────────────────
def test_h4_every_meaning_is_advertised_in_the_prompt_contract():
    """`accepted ⊆ advertised`, the direction #10999 never checked.

    Without this, a meaning can be accepted by the gate while the contract never
    offers it, so the model can never emit it — checklist row 4 green on a
    vocabulary the pipeline cannot use.
    """
    from tortoise.extractor_v2 import OUTPUT_CONTRACT, OUTPUT_CONTRACT_CORE_ONLY

    relations_line = next(
        line for line in OUTPUT_CONTRACT.splitlines() if '"relations":' in line)
    for meaning in SEMANTICS_VALUES:
        assert meaning in relations_line, (
            f"meaning {meaning!r} is accepted by the gate but never advertised in "
            "OUTPUT_CONTRACT.relations — the model cannot emit it")
        assert meaning in OUTPUT_CONTRACT_CORE_ONLY, (
            f"{meaning!r} missing from the DERIVED core-only contract")
    for mechanism in SEMANTICS_MECHANISMS:
        assert mechanism in relations_line, mechanism


# ── H5 — declarationOnly is a disposition, never a silencer ──────────────────
def test_h5_every_declaration_only_row_carries_a_printable_reason(artifact):
    rows = [
        (ns, r) for ns, section in artifact["packs"].items()
        for r in section["relations"] if r["declaration_only"]
    ]
    assert rows, (
        "expected at least `pm:onBoard` — if the count is zero, check whether a "
        "real gap was silently deleted instead of acknowledged")
    for ns, row in rows:
        assert row.get("declaration_only_reason"), (
            f"{ns}:{row['predicate']} is `declarationOnly: true` with no reason — "
            "a marker without a reason SILENCES the gap (#11083 R7)")
        assert row["verdict"] == RELATION_DECLARATION_ONLY
        assert row["written"] == "none", (
            "a declaration-only row cannot be written; if the probe wrote it, the "
            "marker has no effect and must be removed (#2766)")


# ── H6 — the pair that must NOT be flagged as a duplicate ────────────────────
def test_h6_production_and_containment_are_both_present_and_not_duplicates(artifact):
    """The owner ruled (2026-11-05) these are different assertions.

    This is a CONTROL: it passes today and its job is to keep passing. A lane
    that "improves" the census into a duplicate detector reds here instead of
    shipping the regression.
    """
    venture = artifact["packs"]["venture"]
    predicates = {(r["surface"], r["predicate"]) for r in
                  venture["relations"] + venture["hierarchies"]}
    assert ("relations", "producedBy") in predicates
    assert ("hierarchies", "hasPart") in predicates, (
        "the `Program → Asset` `hasPart` path must stay declared")
    assert artifact["duplicate_check"]["flagged_duplicates"] == [], (
        "production and containment are DIFFERENT assertions and both are "
        "written — reporting them as duplicates is the error")


# ── H7 — one hierarchy reader, not two ──────────────────────────────────────
def test_h7_hierarchy_rows_are_exactly_the_declared_hierarchy(artifact):
    """`pack_hierarchy` is the reader. A prose re-parse in the census would be a
    second answer to one question — the #11001 defect one level down."""
    from tortoise.pack_hierarchy import declared_hierarchy

    declared = declared_hierarchy()
    expected = {
        (d.pack, d.from_kind, d.predicate, d.to_kind) for d in declared
    }
    got = {
        (r["pack"], r["from_kind"], r["predicate"], r["to_kind"])
        for section in artifact["packs"].values()
        for r in section["hierarchies"]
    }
    assert got == expected
    assert len(expected) == 6, (
        f"the declared hierarchy population is {len(expected)}, not 6 — a move "
        "here changes the declared total and every reference to it")


def test_h7b_the_hierarchy_sense_is_read_from_the_table_not_a_literal(
        monkeypatch):
    """The `sense` column is the RESOLVER's answer, for hierarchies too.

    `census.py`'s own layer table says the sense column is
    `pack_registry.effective_direction`. The hierarchy half wrote a literal —
    `"hasPart" and "<->" if entry.predicate == "hasPart" else None` — whose
    precedence makes it `"<->" if predicate == "hasPart" else None`, with the dead
    `"hasPart" and` operand as the residue. Nothing could red it: H1 compares two
    derivations that would BOTH have hard-coded it, H3c only checks the value SET
    and H7 never looks at direction. So moving `SEMANTICS_DIRECTIONS["hasPart"]`
    would leave the census reporting a sense the table no longer holds. This test
    moves the table and requires the rows to move with it.
    """
    census = _census()

    def _hier():
        return [r for section in
                census.build_census(REPO_ROOT / "packs")["packs"].values()
                for r in section["hierarchies"]]

    rows = _hier()
    assert rows, "no declared hierarchies — the fixture premise is gone"
    assert {r["effective_direction"] for r in rows} == {
        SEMANTICS_DIRECTIONS["hasPart"]}, (
        "a hierarchy row's sense must BE the table's value for its predicate")
    monkeypatch.setitem(SEMANTICS_DIRECTIONS, "hasPart", "->")
    assert {r["effective_direction"] for r in _hier()} == {"->"}, (
        "the hierarchy sense is still a hard-coded literal — the census would "
        "disagree with `pack_registry.effective_direction`, which this column "
        "claims to report")


# ── H8 — vocabulary-shaped fields warn, they never drop a pack ───────────────
def _write_pack(tmp_path: Path, ns: str, relations: list[dict]) -> Path:
    packs = tmp_path / "packs"
    packs.mkdir(parents=True, exist_ok=True)
    (packs / ns).mkdir(parents=True, exist_ok=True)
    lines = [f"namespace: {ns}", f"name: {ns}", 'version: "0.0.1"', "tier: free"]
    lines.append("ontology:")
    lines.append("  extends: core")
    lines.append("  objectKinds:")
    lines.append("  - widget")
    lines.append("  relations:")
    for rel in relations:
        lines.append(f"  - predicate: {rel['predicate']}")
        for key, value in rel.items():
            if key == "predicate":
                continue
            lines.append(f"    {key}: {json.dumps(value)}")
    (packs / ns / "manifest.yaml").write_text("\n".join(lines) + "\n")
    return packs


def test_h8_a_bad_direction_warns_and_does_not_drop_the_pack(tmp_path, caplog):
    """A `direction` typo must not read as "no direction", and must not be in the
    pack-DROPPING error class: `_load_one` raises on any `_validate` error and
    `load_all` then deletes the whole pack (`pack_registry.py` load_all).

    ⛔ The WARN half is asserted, not just the survival half. Asserting survival
    alone left the arm deletable: remove the `_warn_packs_once` call and this test
    stayed green, so the message — the only thing that tells an author why their
    token was ignored — had no reddening row anywhere.
    """
    packs = _write_pack(tmp_path, "fixture", [{
        "predicate": "widgetLink",
        "mechanism": "IMPL",
        "semantics": "dependsOn",
        "fromKind": "fixture:widget",
        "toKind": "fixture:widget",
        "direction": "sideways",
    }])
    with caplog.at_level("WARNING"):
        reg = pack_registry.PackRegistry(packs)
        reg.load_all()
    assert "fixture" in reg.packs, (
        "a vocabulary-shaped field dropped the pack — the direction check must "
        "be warn-class, not errors.append")
    assert "'sideways'" in caplog.text and (
        "is not a recognised direction token" in caplog.text), (
        "the invalid token was ignored SILENTLY — the author is never told which "
        "value was rejected or that the meaning resolved it instead")
    row = reg.packs["fixture"].relations[0]
    assert effective_direction(row) == "->", (
        "an invalid token is ignored and the meaning resolves the direction")


def test_h8b_an_empty_declaration_only_reason_warns_and_does_not_drop(
        tmp_path, caplog):
    packs = _write_pack(tmp_path, "fixture2", [{
        "predicate": "widgetView",
        "from": "Object",
        "to": "Object",
        "declarationOnly": True,
        "declarationOnlyReason": "",
    }])
    with caplog.at_level("WARNING"):
        reg = pack_registry.PackRegistry(packs)
        reg.load_all()
    assert "fixture2" in reg.packs
    assert "'declarationOnlyReason' must be a non-empty string" in caplog.text, (
        "`declarationOnly: true` with an empty reason is a SILENCER — the warning "
        "is the only thing that surfaces it (#11083 R7)")


def test_h8c_an_orphan_declaration_only_reason_warns(tmp_path, caplog):
    """The third arm: a reason with no `declarationOnly: true` above it.

    It is a distinct failure (a reason explaining a disposition the row does not
    claim) with its own key and its own message, and it had no reddening row at
    all — the census would print a reason for a row it calls writable.
    """
    packs = _write_pack(tmp_path, "fixture4", [{
        "predicate": "widgetPanel",
        "mechanism": "IMPL",
        "semantics": "hasPart",
        "fromKind": "fixture:widget",
        "toKind": "fixture:widget",
        "declarationOnlyReason": "a panel is a view",
    }])
    with caplog.at_level("WARNING"):
        reg = pack_registry.PackRegistry(packs)
        reg.load_all()
    assert "fixture4" in reg.packs
    assert "is recorded but 'declarationOnly' is not true" in caplog.text, (
        "an orphaned reason must be named — otherwise a row reads as having a "
        "disposition it never claimed")


# ── H9 — the union-verdict vs writer disagreement is FLAGGED ─────────────────
def test_h8d_the_two_surfaces_overlap_and_the_overlap_is_disclosed(artifact):
    """`hierarchies[]` and `relations[]` can be ONE declaration spelled twice.

    `pack_hierarchy` says so and joins them in `_matching_row`. 4 of the 6 shipped
    hierarchy rows restate a `relations[]` row of the same pack and pair, so the
    42-row denominator is 38 DISTINCT connections. The denominator stays (it is
    the issue's own); the overlap is stated, because a total a reader takes as 42
    distinct assertions is the quiet mis-count this census exists to end.
    """
    pop = artifact["population"]
    assert pop["restated_hierarchy_rows"] == 4, pop
    assert pop["distinct_connections"] == pop["declared"] - 4, pop
    restated = [
        (ns, r["from_kind"], r["to_kind"], r["restates_relation"])
        for ns, section in artifact["packs"].items()
        for r in section["hierarchies"] if r.get("restates_relation")
    ]
    assert len(restated) == 4, restated
    # The two venture rows are the connections a relations-only census misses.
    assert all(not r.get("restates_relation")
               for r in artifact["packs"]["venture"]["hierarchies"])


def test_h9_a_union_only_structural_predicate_is_flagged(tmp_path):
    """`relation_write_verdict`'s structural path is the UNION of three writers'
    sets; `create_edge` accepts a narrower one. A row in the union but not in the
    writer is reported writable by `pack validate` and refused by the writer it
    would use — the `pack_hierarchy._binding` class, latent in the shipped
    catalog and flagged rather than fixed here.
    """
    from tortoise.projection.edges import (
        _VALID_EDGE_PREDICATES,
        STRUCTURAL_WRITE_PREDICATES,
    )

    union_only = "aboutPoint"
    assert union_only in STRUCTURAL_WRITE_PREDICATES
    assert union_only not in _VALID_EDGE_PREDICATES, (
        "the fixture relies on the divergence being real — if the two sets "
        "converge, this control should be replaced with a synthetic predicate")

    # ⛔ The fixture is LOAD-BEARING. It was dead setup before: the row was built
    # from a hand-written dict and the test closed with `assert packs.exists()` —
    # true because `_write_pack` created it two statements earlier, so the
    # assertion could not vary under ANY mutation. The pack is now LOADED and the
    # flagged row comes out of `PackRegistry`, which is what the docstring says.
    packs = _write_pack(tmp_path, "fixture3", [{
        "predicate": union_only,
        "mechanism": "IMPL",
        "semantics": "related",
        "fromKind": "fixture:widget",
        "toKind": "fixture:widget",
    }])
    census = _census()
    fresh = census.build_census(packs)
    rows = [r for r in fresh["packs"]["fixture3"]["relations"]
            if r["predicate"] == union_only]
    assert len(rows) == 1, (
        "the fixture's declared row did not survive pack load — the assertion "
        "below would then be about a row built by hand, not by the registry")
    row = rows[0]
    assert row["write_path"] == "structural"
    assert row["path_disagreement"] is True, (
        "`pack validate` would call this writable while create_edge refuses it — "
        "the disagreement must be a printed cell, not an invisible optimism")


def test_h9b_no_shipped_row_hits_the_latent_class(artifact):
    """Reported, not fixed: it is latent. If a shipped row ever hits it, this red
    is the signal that a `pack validate` answer and the writer have diverged."""
    offenders = [
        (ns, r["predicate"])
        for ns, section in artifact["packs"].items()
        for r in section["relations"]
        if r["path_disagreement"]
    ]
    assert offenders == [], offenders
