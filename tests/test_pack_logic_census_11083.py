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
  H10 `--uri` binds the URI the SDK actually opens (through `TORTOISE_DB_URI`,
      the only door it counts — its first positional parameter is the embedded
      store's file path) and the probe collects its mints in MINT ORDER, so the
      cleanup's "operators first" is an order and not hash order.
  H11 the distinct-connection denominator is MEASURED (`restates_relation` per
      hierarchy row), not subtracted in prose — a total a reader takes as N
      distinct assertions is the quiet mis-count this census exists to end.
  H12 the cleanup contract is a MEASURED cell (`probe.leftovers` /
      `minted` / `deleted`), not a docstring claim — twice before this PR a
      docstring asserted a delete that did not exist in the file.
  H13 the hierarchy sense goes through `effective_direction` (so a retired
      spelling reports the sense the WRITER records, not `None` from the table);
      H13b `written_direction` distinguishes NOT-APPLICABLE (`n/a`) from
      NOT-MEASURED (empty), so "no operator direction" cannot hide "never ran".
  H14 the `pipeline` UNMEASURED cell cites the SENTENCE, not a line number —
      a line cite re-stales on every edit above it and `--write-probe` would
      reproduce the stale pointer forever.

Hermetic by construction: this file imports no SDK and touches no graph.
"""
from __future__ import annotations

import json
import os
import re
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

#: A `file.py:NNNN` citation — the shape that re-stales on every edit above it.
_LINE_CITE = re.compile(r"[\w/]+\.py:\d+")


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
        "a junk token resolves to `None` — and the WRITER refuses it rather than "
        "reading the meaning, so it is not 'no direction, fall through to the "
        "meaning'; a reader that did the latter would report a sense for a row "
        "the write door rejects (#11083 review)")


def test_h3e_a_LEGACY_token_is_honoured_exactly_as_the_writer_honours_it():
    """The one-normalizer rule (#7852) inside the declaration-side reader.

    `create_operator` accepts SIX direction tokens — the four model values plus
    the legacy `unidirectional`/`bidirectional` aliases — and `live.py` forbids
    re-spelling that set at a call site. A reader that tested membership in the
    four model values alone therefore WARNED-AND-IGNORED `unidirectional` while
    the writer honoured it as `->`: for a row whose meaning is `produces` (table
    `<-`), the census reported `<-` and the graph stored `->`. One wrong sign on
    the very cell this PR exists to make trustworthy.

    ⛔ The MIRROR-IMAGE divergence is the other half of the same rule, and a
    reader honouring "any declared token" gets it wrong: the writer resolves a
    declared direction only when it DIFFERENTIATES (`sdk.py:23192` — `derived =
    self._direction_from_semantics(semantics); if derived is not None and
    is_default_direction(direction): direction = derived`), and `live.py` says
    `bidirectional`/`<->` IS the non-differentiating default. So a row declaring
    `{semantics: produces, direction: bidirectional}` is stored `<-`, and a
    census cell saying `<->` would be the census and the writer disagreeing
    again — this time claiming a mutual edge the graph does not hold.
    """
    assert effective_direction(
        {"semantics": "produces", "direction": "unidirectional"}) == "->", (
        "`unidirectional` IS `->`; reporting the meaning's `<-` here would put the "
        "census and `create_operator` on opposite sides of one row")
    # A DEFAULT token does not differentiate, so the MEANING wins — exactly as
    # `create_operator` overwrites it. Both spellings of the default, and both
    # because `is_default_direction` is the one predicate, not a token list.
    assert effective_direction(
        {"semantics": "produces", "direction": "bidirectional"}) == "<-", (
        "`bidirectional` is the writer's non-declaration default, so the writer "
        "stores the meaning's `<-`; a census cell of `<->` would describe an "
        "edge the graph does not hold")
    assert effective_direction(
        {"semantics": "produces", "direction": "<->"}) == "<-", (
        "the model spelling of the same default must resolve identically")
    # `-` DOES differentiate (no confidence transfers, owner ruling), so it is
    # honoured over the meaning — the normalizer must not widen `default` into
    # "everything that is not one-way".
    assert effective_direction(
        {"semantics": "produces", "direction": "-"}) == "-"
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

    # ⛔ Each CLAUSE is read separately. `OUTPUT_CONTRACT.relations` advertises two
    # vocabularies on one line — the `predicate` allowlist and the `semantics`
    # (layer-2 meaning) allowlist — so a membership test on the whole LINE stays
    # green while either list alone loses an entry (measured: deleting `produces`
    # from the predicate list left the line-level assertion green). Same clause
    # parsing as `tests/test_10999_batch_story_relations.py`.
    relations = OUTPUT_CONTRACT.split('"relations":', 1)[1]
    predicates = relations.split('"predicate": "', 1)[1].split('"', 1)[0]
    proposed = relations.split('"semantics": "', 1)[1].split('"', 1)[0]
    for meaning in SEMANTICS_VALUES:
        assert meaning in predicates, (
            f"meaning {meaning!r} is accepted by the gate as a predicate but never "
            "advertised in OUTPUT_CONTRACT's predicate allowlist — the model "
            "cannot emit it")
        assert meaning in proposed, (
            f"meaning {meaning!r} is accepted as a layer-2 semantic but missing "
            "from OUTPUT_CONTRACT's semantics allowlist — the model cannot emit it")
        assert meaning in OUTPUT_CONTRACT_CORE_ONLY, (
            f"{meaning!r} missing from the DERIVED core-only contract")
    # ⛔ NOT `for mechanism in SEMANTICS_MECHANISMS`. That iterates the dict's
    # KEY set, which is a SUBSET of `SEMANTICS_VALUES` — already asserted above,
    # and equally satisfiable by the predicate list — so it cannot vary under any
    # mutation of this diff (the non-vacuity rule). What a writer actually stamps
    # is the dict's VALUES, a different surface: hold them to the contract's own
    # `op_type` allowlist.
    op_types = OUTPUT_CONTRACT.split('"op_type": "', 1)[1].split('"', 1)[0]
    for meaning, mechanism in SEMANTICS_MECHANISMS.items():
        assert mechanism in op_types, (
            f"{meaning!r} resolves to mechanism {mechanism!r}, which is missing "
            f"from OUTPUT_CONTRACT's op_type allowlist ({op_types!r}) — the model "
            "can never emit the operator the registry maps this meaning to")


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
        "value was rejected")
    row = reg.packs["fixture"].relations[0]
    # ⛔ None, NOT the meaning's `->`. The writer does NOT resolve a junk token
    # from `semantics`: `is_default_direction("sideways")` is False (a typo must
    # not read as absent), so the meaning never overwrites it and
    # `create_operator` then REFUSES the write (`direction must be one of …, got
    # 'sideways'`). A relation whose write path raises has no sense to report;
    # reporting the meaning's `->` describes a write that cannot happen.
    assert effective_direction(row) is None, (
        "the writer refuses an unrecognised direction token rather than reading "
        "the meaning; the census must report no resolvable sense")
    # A declared DEFAULT token with NO meaning is the other side of the same
    # contract: the writer STORES `<->` (the semantics step never runs and the
    # value is not None, so nothing canonicalizes it), so the census must not
    # report `None` for an edge the graph holds.
    assert effective_direction({"direction": "<->"}) == "<->", (
        "a declared default with no meaning is still the row's recorded direction")


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


# ── H10 — `--uri` reaches the graph it names, and the cleanup has an ORDER ───
def test_h10_the_uri_option_binds_the_uri_the_sdk_actually_opens(monkeypatch):
    """`--uri` was advertised three times (module docstring, `--help`, README) and
    reached a real graph zero times.

    `TortoiseSDK`'s first positional parameter is `db_path` — the EMBEDDED
    store's FILE path — so `TortoiseSDK(uri)` set `_db_path="docker://…"` and
    left `_db_uri=None`: the first graph touch then raised `ValueError: …
    relative path` from a layer that never saw a URI, i.e. the documented "probe
    a real graph" path died on a message about the one parameter that was
    correct. The SDK opens a URI only through `TORTOISE_DB_URI`, so that door is
    pinned here — as is the RESTORE, because a probe that leaves its URI in the
    environment changes which graph the next reader in the process writes to
    (the residue class #5084 exists to stop).
    """
    census = _census()
    seen: dict = {}

    class _FakeSDK:
        def __init__(self, *args, **kwargs):
            seen["args"] = args
            seen["kwargs"] = kwargs
            seen["env"] = os.environ.get("TORTOISE_DB_URI")

    monkeypatch.setenv("TORTOISE_DB_URI", "docker://previous:6379/peer_graph")
    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    census._sdk("docker://:pw@localhost:6379/census_target")

    assert (seen["args"], seen["kwargs"]) == ((), {}), (
        "the URI was handed to `TortoiseSDK`'s first positional parameter — which "
        "is `db_path`, an embedded FILE path — so it never reached "
        "`FalkorProjection.from_uri` and `--uri` probed whatever the env named")
    assert seen["env"] == "docker://:pw@localhost:6379/census_target", (
        "the URI never became the one the SDK opens (`TORTOISE_DB_URI` while "
        "`db_path` is None is the only door it counts)")
    assert os.environ["TORTOISE_DB_URI"] == "docker://previous:6379/peer_graph", (
        "the probe left its URI in the process environment — the SDK captured it "
        "at construction, so the mutation has no purpose after that and only "
        "redirects the next reader")


def test_h10b_the_cleanup_deletes_in_reverse_mint_order(monkeypatch):
    """`_delete_minted` deletes operators first, *in reverse mint order*.

    `minted` was a `set`, so `reversed(list(minted))` was reverse-HASH order and
    the docstring's claim was provably false — harmless only because
    `delete_entity` issues `DETACH DELETE` today, and load-bearing the moment a
    non-`DETACH` delete is added. Driven through `_run_write_probe` (not
    `_delete_minted` directly) because the defect would be the CALLER's
    container, not the loop.
    """
    census = _census()
    issued: list[str] = []
    deleted: list[str] = []

    class _FakeSDK:
        def create_entity(self, kind, name, objectKind=None):
            node_id = f"node-{len(issued)}"
            issued.append(node_id)
            return {"node": {"id": node_id}}

        def create_operator(self, mechanism, src, targets, label=None,
                            semantics=None, direction=None):
            op_id = f"op-{len(issued)}"
            issued.append(op_id)
            return {"id": op_id}

        def delete_entity(self, node_id):
            deleted.append(node_id)
            return True

        def close(self):
            pass

    rows = [
        census._relation_row("dev", {
            "predicate": "blocks",
            "mechanism": "IMPL",
            "semantics": "dependsOn",
            "direction": "<-",
            "fromKind": "dev:widget",
            "toKind": "dev:gadget",
        })
        for _ in range(2)
    ]
    monkeypatch.setattr(census, "_sdk", lambda uri=None: _FakeSDK())
    monkeypatch.setattr(census, "_read_back",
                        lambda sdk, src, dst, predicate: ("mediated", "<-"))
    census._run_write_probe(rows, REPO_ROOT / "packs", None)

    assert len(issued) > 2, issued
    assert deleted == list(reversed(issued)), (
        "the probe did not delete its mints in reverse mint order — the container "
        "it collects them in is not insertion-ordered, so \"operators first\" is "
        "hash order")



# ── H11 — rows ≠ connections, and the difference is MEASURED ────────────────
def test_h11_the_distinct_denominator_is_measured_not_subtracted_in_prose(artifact):
    """`pack_hierarchy` calls a `hierarchies[]` entry and a `relations[]` row over
    the same pack + pair + meaning *"one declaration spelled twice"*.

    So `declared` is a count of ROWS and the distinct-connection denominator is a
    different number. It used to live only in a paragraph: the PR body quoted
    `population.distinct_connections` and a per-row `restates_relation` that the
    artifact did not carry, so the arithmetic was unverifiable from the artifact a
    reader actually opens. Both are cells now, and this test re-derives them from
    the module that owns the join rather than from the paragraph.
    """
    from tortoise.pack_hierarchy import declared_hierarchy

    rows = [r for s in artifact["packs"].values() for r in s["hierarchies"]]
    pop = artifact["population"]
    restated = sum(1 for r in rows if r["restates_relation"])
    assert pop["restated_hierarchy_rows"] == restated, (
        "`population.restated_hierarchy_rows` is not the number of hierarchy rows "
        "that carry `restates_relation` — the denominator is remembered, not derived")
    assert pop["distinct_connections"] == pop["declared"] - restated, (
        "distinct connections must be declared − restated; a number that does not "
        "subtract is the prose claim this cell replaced")
    assert restated == 4, (
        f"{restated} hierarchy row(s) restate a `relations[]` row — if this moved, "
        "the distinct denominator moved with it and the README/body must too")
    assert pop["distinct_connections"] == 38

    # The per-row flag is the JOIN's answer, not an inference of the census's: it
    # must agree with `declared_hierarchy()` entry for entry (H7's rule, one
    # column over). A census that re-derived the join itself would answer twice.
    expected = {(d.pack, d.predicate, d.from_kind, d.to_kind)
                for d in declared_hierarchy() if d.restates_relation}
    got = {(r["pack"], r["predicate"], r["from_kind"], r["to_kind"])
           for r in rows if r["restates_relation"]}
    assert got == expected, (
        "the artifact's `restates_relation` flags disagree with "
        "`pack_hierarchy.declared_hierarchy()` — two answers to one join")


# ── H12 — the probe's cleanup contract is a CELL, not a claim ───────────────
def test_h12_the_cleanup_contract_is_a_measured_cell(artifact, fresh):
    """Twice before this PR, a docstring claimed the probe deletes what it mints
    while no `delete` existed in the file at all; then the artifact's own
    `layers.written` described a probe that did not exist ("one endpoint pair
    minted per row on a throwaway graph") and `population.note` — which the PR
    body named as carrying the cleanup statement — was absent.

    A prose contract cannot be checked by a reader and cannot be reddened by a
    test. `probe.leftovers` is the MEASURED version: the ids a delete failed on.
    """
    probe = artifact["probe"]
    assert probe is not None, (
        "the committed artifact carries no `probe` block — the cleanup contract "
        "an operator checks before a `--uri` run is unverifiable again")
    assert probe["leftovers"] == [], (
        f"the probe could not delete {probe['leftovers']} — a residue in whatever "
        "graph it was pointed at, reported instead of swallowed (and NOT silent)")
    assert probe["minted"] > 0 and probe["deleted"] == probe["minted"], probe
    assert fresh["probe"] is None, (
        "a HERMETIC build has no probe — if it had one, the artifact could have "
        "been produced without ever touching a graph")

    written = artifact["layers"]["written"]
    assert "DELETED" in written and "probe.leftovers" in written, (
        "`layers.written` is the field a reader trusts about how `written` was "
        "obtained; it must state the deletion and where a failure is reported")
    assert "per row" not in written.lower(), (
        "the layer description claims one endpoint pair per ROW — the probe mints "
        "one node per declared KIND and caches it (the 'fictional probe' defect)")


# ── H13 — the sense column reads the RESOLVER, for hierarchies too ──────────
def _write_pack_with_hierarchy(tmp_path: Path, ns: str) -> Path:
    packs = tmp_path / "packs"
    (packs / ns).mkdir(parents=True, exist_ok=True)
    (packs / ns / "manifest.yaml").write_text(
        f"namespace: {ns}\n"
        f"name: {ns}\n"
        'version: "0.0.1"\n'
        "tier: free\n"
        "ontology:\n"
        "  extends: core\n"
        "  objectKinds:\n"
        "  - widget\n"
        "  relations:\n"
        "  - predicate: widgetHolds\n"
        "    mechanism: IMPL\n"
        "    semantics: holds\n"
        f"    fromKind: {ns}:widget\n"
        f"    toKind: {ns}:widget\n"
        "  hierarchies:\n"
        "  - path: Widget → Widget\n"
        "    type: holds\n",
        encoding="utf-8",
    )
    return packs


def test_h13_a_hierarchy_declaring_a_RETIRED_spelling_reports_the_RESOLVED_sense(
        tmp_path):
    """The hierarchy half read the raw `SEMANTICS_DIRECTIONS` table while its own
    layer description says the column is `pack_registry.effective_direction`.

    All six shipped rows use `hasPart`, which IS in the table, so nothing is
    wrong today — but the resolver also applies `SEMANTICS_TRANSITION`, and a
    hierarchy declaring a retired spelling would report NO sense for a row the
    writer records `->`. Same one-line rule as the relations path.
    """
    assert "holds" not in SEMANTICS_DIRECTIONS, (
        "the fixture relies on `holds` being ABSENT from the raw table — that "
        "absence is what makes a table-read report None")

    census = _census()
    packs = _write_pack_with_hierarchy(tmp_path, "fixture5")
    fresh = census.build_census(packs)
    rows = [r for r in fresh["packs"]["fixture5"]["hierarchies"]
            if r["predicate"] == "holds"]
    assert len(rows) == 1, (
        "the fixture's declared hierarchy did not survive pack load — the "
        "assertion below would then be about nothing")
    assert rows[0]["effective_direction"] == "->", (
        "a hierarchy declaring a retired spelling must report the sense the "
        "writer records (`holds` → `ownedBy` → `->`); reading the raw table "
        "answers None and the census disagrees with `effective_direction`")


def test_h13b_written_direction_tells_NOT_APPLICABLE_from_NOT_MEASURED(artifact):
    """`written_direction` was populated for the 33 `mediated` rows and `None` for
    every row written `direct` — so a reader could not tell "this edge carries no
    operator direction" from "this row was never measured".

    Three states, three readings (#10995's rule applied to a column): a real token
    for `mediated`, an explicit `n/a` for `direct`, and empty ONLY where nothing
    was written to read.
    """
    census = _census()
    rows = [r for s in artifact["packs"].values()
            for r in s["relations"] + s["hierarchies"]]
    direct = [r for r in rows if r["written"] == "direct"]
    mediated = [r for r in rows if r["written"] == "mediated"]
    none = [r for r in rows if r["written"] == "none"]
    assert direct and mediated and none, (len(direct), len(mediated), len(none))
    assert {r["written_direction"] for r in direct} == {census.DIRECT_EDGE_DIRECTION}, (
        "a `direct` row must say the edge carries no operator direction; `None` "
        "reads as unmeasured")
    assert all(r["written_direction"] in {"->", "<-", "<->", "-"} for r in mediated)
    assert all(r["written_direction"] is None for r in none), (
        "a row nothing was written for has no direction to report — that is the "
        "ONE state where the cell may be empty")


# ── H14 — the UNMEASURED cell's only citation must be FOLLOWABLE ────────────
def test_h14_the_pipeline_unmeasured_cell_cites_the_SENTENCE_not_a_line(artifact):
    """The `pipeline.relations_channel` cell is `UNMEASURED` with #11237 as its
    owner, and the ONLY support for that reading was a line cite —
    `extractor_v2.py:10815` — generated into the artifact.

    Line 10815 is inside the MITIGATES-strength warning block; the sentence is in
    the S5 Job 1 block, 76 lines below. A reader following the artifact's only
    pointer to confirm why the column is unmeasured landed on unrelated code. The
    body says the fix for exactly this class is to cite the SENTENCE, so the cite
    is a sentence here — and this test follows it.
    """
    assert not _LINE_CITE.search(artifact["pipeline"]["relations_channel"]["why"]), (
        "the pipeline cell cites a LINE (`file.py:NNNN`), which re-stales on every "
        "edit above it and which `--write-probe` reproduces into the artifact")
    why = artifact["pipeline"]["relations_channel"]["why"]
    assert "nothing in this tree consumes" in why
    src = (REPO_ROOT / "tortoise" / "extractor_v2.py").read_text()
    assert "nothing in this tree consumes `result[\"relations\"]`" in src, (
        "the cited SENTENCE is not in the file it points at — an unfollowable "
        "citation is the same defect as a stale line number")


def test_h2c_the_dangling_kinds_are_spelled_as_the_packs_DECLARE_them(artifact):
    """`_dangling` joins endpoints on the LOWERCASED kind (`kind.lower()`), which
    is the right key for a case-insensitive comparison — and the wrong thing to
    REPORT. Emitting the join key named kinds no manifest declares: the artifact
    said `marketing:competitorcontent` and `pm:kanbanboard` where the packs say
    `competitorContent` and `kanbanBoard`, and nothing read this cell, so the
    misspelling could not red anything.

    Pinned on the declared spelling: a `core` row comes from the canonical
    constant (its own spelling), any other row must match its pack's declared
    object kind EXACTLY.
    """
    from tortoise.pack_registry import PackRegistry

    reg = PackRegistry(REPO_ROOT / "packs")
    reg.load_all()
    declared = {ns: set(pack.object_kinds or ()) for ns, pack in reg.packs.items()}
    rows = artifact["dangling"]
    assert rows, "the section the commitment spine shows up in is empty"
    for row in rows:
        if row["namespace"] == "core":
            continue
        assert row["kind"] in declared.get(row["namespace"], set()), (
            f"{row['namespace']}:{row['kind']} is not a declared object kind of "
            f"that pack — the artifact is reporting the lowercased join KEY "
            f"(declared: {sorted(declared.get(row['namespace'], ()))})")
