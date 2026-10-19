"""#5009 — §12.4's SUFFICIENCY ROUTER and §12.5's ESCALATION WRITE-BACK.

§12.4's rule is *"escalate on SUFFICIENCY, not on query type"*: *"what kind of
query is this?"* is a classifier that must be trained, drifts, and is wrong at
the boundary; *"did the cheap tier answer it?"* is a **measured property of the
answer**. §12.5 names the half that compounds — the escalation must WRITE BACK
what it verified, or the same escalation is paid for forever.

These tests pin the decision at the point where it is made:

* the verdict is **three-state** — ``undetermined`` is never flattened into a
  neighbouring state at either end (a boolean seam is how an unmeasured read
  becomes a false negative or a false green);
* **residency outranks sufficiency** (§17.3) — a read the store could not see
  has not established insufficiency, so it may never escalate;
* the trigger is **bounded** — one walk, and a still-insufficient verdict after
  it is reported rather than re-walked (§12.7 *router overhead*);
* the router takes **no query class** — pinned structurally, so the classifier
  §12.4 rejects cannot be reintroduced without reddening a test;
* the write-back carries its **span**, never promotes a class, never carries the
  raw payload, and **dedupes by identity** (the attribution loop, without which
  the escalation is a permanent tax).

MUTATION PROOF — the guard each test exists to hold, and the edit that turns it
RED:

* **Flatten the third state** — make ``SufficiencyVerdict.sufficient`` return
  ``False`` for ``undetermined`` → ``test_an_unassessed_verdict_is_neither_true_nor_false``
  and ``test_an_unassessed_read_serves_rather_than_escalates`` RED.
* **Let a residency miss escalate** — drop the ``resident`` guard at the top of
  ``assess_sufficiency`` → ``test_a_residency_miss_is_never_an_insufficiency``
  RED (and ``route_residency`` receives a sufficiency verdict it must not have).
* **Score an unfetched provenance field as zero** — count every row instead of
  the ``assessable`` subset → ``test_a_provenance_measure_over_unfetched_rows_is_unassessed``
  RED (a flag-off read becomes "insufficient evidence").
* **Default a threshold** — give ``required_anchor_coverage`` a default →
  ``test_there_is_no_default_threshold`` RED (a target nobody chose).
* **Unbound the trigger** — drop the ``escalated``/``budget`` guard →
  ``test_a_second_insufficient_verdict_is_reported_not_re_walked`` RED.
* **Turn the router into a classifier** — add a ``query_type`` parameter →
  ``test_the_router_takes_no_query_class`` RED.
* **Give the router a clock / a model / a graph** — import ``time`` or
  ``tortoise.embeddings`` → ``test_the_router_is_pure_no_io_no_model_no_clock`` RED.
* **Collapse the two routers** — emit ``fetch-record`` from the sufficiency
  route → ``test_a_sufficiency_route_is_never_the_residency_repair`` RED.
* **Write back without a span** — drop the ``span is None`` refusal →
  ``test_a_finding_without_a_span_is_refused_not_downgraded`` RED.
* **Promote an episodic fact** — drop the ``promote_to`` refusal →
  ``test_the_write_back_refuses_to_promote_a_claim_class`` RED.
* **Drop the attribution loop** — remove the ``seen`` dedupe →
  ``test_the_same_verified_finding_writes_back_once`` RED.
* **Let a unit carry the raw payload** — add a ``raw``/``supersedes`` field to
  ``SummaryUnit`` → ``test_a_summary_unit_can_neither_carry_the_raw_payload_nor_supersede_it``
  RED.
* **Silently drop a refusal** — return only ``units`` from ``plan_writeback`` →
  ``test_an_unverified_finding_is_refused_and_reported`` RED.
* **Read provenance from the attribute key only** — drop the nested
  ``provenance.source`` path (the shape ``SearchResult.to_dict()`` emits) →
  ``test_the_provenance_link_is_read_from_the_wire_shape_the_read_path_emits``
  and ``test_a_real_search_result_row_is_assessed_for_provenance`` RED (the
  measure then has no producer on the only shape a caller holds).
* **Match an anchor as an infix** — return to a bare substring test →
  ``test_a_short_anchor_does_not_match_inside_an_unrelated_word`` RED.
* **Count a blank anchor as covered** — drop the blank-anchor refusal →
  ``test_an_empty_anchor_is_refused_not_counted_as_covered`` RED.
* **Let an empty anchor set disable a supplied threshold** — drop the
  ``required_anchor_coverage``/``anchors`` mismatch guard →
  ``test_an_anchor_threshold_with_no_anchors_is_refused_not_silently_unassessed``
  RED.
* **Accept any falsy ``resident``** — return to ``resident is None or resident is
  False`` with no type check →
  ``test_a_non_bool_resident_is_refused_not_treated_as_established`` RED.
* **Iterate a single row as if it were the row list** — drop the
  ``str``/``bytes``/``Mapping`` refusal on ``evidence`` →
  ``test_a_single_row_cannot_be_passed_where_a_sequence_of_rows_goes`` RED (a
  bare mapping measures its KEYS and a bare string its CHARACTERS, so the
  verdict is a confident one about evidence nobody assembled).
* **Report an exhausted budget that is not exhausted** — drop the
  ``already-escalated`` reason →
  ``test_an_already_escalated_verdict_does_not_claim_an_exhausted_budget`` RED.
* **Default the budget** — restore ``budget: int = 1`` →
  ``test_there_is_no_default_budget`` RED (§12.3's tier count is OPEN).
* **Bypass a ``SummaryUnit`` invariant** — drop any check in its
  ``__post_init__`` →
  ``test_a_summary_unit_cannot_be_constructed_without_its_invariants`` RED.
* **Read only ``content`` for anchor coverage** — drop the ``subject``/``topics``
  entity fields (or read ``kind``) →
  ``test_an_entity_named_in_subject_or_topics_counts_as_coverage`` RED.
* **Match against the raw haystack** — drop the whitespace normalisation on the
  row text → ``test_a_multi_word_anchor_matches_across_irregular_whitespace``
  RED.
* **Let a verdict carry a reason that is not its own** — drop the allow-list (or
  the empty-reason refusal) →
  ``test_a_verdict_must_carry_a_reason_that_belongs_to_it`` RED.
* **Let a route accept a non-int ``budget_remaining`` / a non-bool
  ``escalated``** — drop those checks →
  ``test_a_route_refuses_scalars_of_the_wrong_type`` RED.
* **Omit a vocabulary from ``__all__``** — drop any exported name →
  ``test_the_vocabulary_a_consumer_needs_is_exported`` RED.
* **Read provenance only from a mapping** — drop the attribute fallback in
  ``evidence_provenance`` →
  ``test_the_exported_provenance_helper_reads_an_attribute_row_too`` RED.

Hermetic: no DB, no graph, no network (runs on the fast ``core`` lane).
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import unicodedata
from pathlib import Path
from typing import ClassVar

import pytest

from tortoise import status_vocabulary
from tortoise.residency import RESIDENCY_HOT, RESIDENCY_NOT_HOT
from tortoise.residency_router import (
    ROUTE_ESCALATE_TIER,
    ROUTE_FETCH_RECORD,
    ROUTE_SERVE,
    ROUTES,
    route_residency,
)
from tortoise.search_engine import SearchResult
from tortoise.sufficiency_router import (
    INSUFFICIENT,
    MEASURE_ANCHOR_COVERAGE,
    MEASURE_PROVENANCE_COVERAGE,
    REASON_ALREADY_ESCALATED,
    REASON_ANCHORS_UNCOVERED,
    REASON_BUDGET_EXHAUSTED,
    REASON_EVIDENCE_EMPTY,
    REASON_MEASURES_MET,
    REASON_NO_MEASURE,
    REASON_PROVENANCE_THIN,
    REASON_PROVENANCE_UNASSESSED,
    REASON_RESIDENT_MISS,
    REASON_RESIDENT_UNASSESSED,
    REFUSAL_CLASS_CONFLICT,
    REFUSAL_NO_CLAIM,
    REFUSAL_NO_PROVENANCE,
    REFUSAL_PROMOTION,
    REFUSAL_UNVERIFIED,
    SPAN_OFFSET_MAX,
    SUFFICIENT,
    UNDETERMINED,
    VERDICTS,
    SourceSpan,
    SufficiencyRoute,
    SufficiencyVerdict,
    SummaryUnit,
    VerifiedFinding,
    assess_sufficiency,
    evidence_provenance,
    evidence_record,
    plan_writeback,
    route_sufficiency,
    unit_key,
)

MODULE_PATH = Path(__file__).resolve().parent.parent / "tortoise" / "sufficiency_router.py"


def _rows(*contents: str, provenance: bool = False) -> list[dict]:
    """Assembled evidence rows in the shape the read path emits.

    The provenance-carrying rows are built from a real
    ``SearchResult(...).to_dict()`` rather than hand-written, because the
    hand-written version drifted: it invented a TOP-LEVEL ``source_ref`` key,
    which no producer emits, and so every provenance test was green against a
    shape that does not exist on the wire while the real one went unread.
    """
    out = []
    for i, content in enumerate(contents):
        if provenance:
            out.append(SearchResult(
                id=f"p{i}", content=content, point_kind="fact",
                source_ref=f"https://example.test/{i}",
            ).to_dict())
            continue
        out.append({"id": f"p{i}", "content": content})
    return out


# ── the verdict: three states, never two ───────────────────────────────────


def test_the_verdict_vocabulary_has_three_states() -> None:
    assert VERDICTS == (SUFFICIENT, INSUFFICIENT, UNDETERMINED)
    assert len(set(VERDICTS)) == 3


def test_an_unassessed_verdict_is_neither_true_nor_false() -> None:
    """``undetermined`` is a real state, not a softened boolean.

    The ``bool | None`` bridge is what the residency router already accepts; a
    third state flattened here becomes a false escalation (``False``) or a
    silent "ok" (``True``).
    """
    verdict = assess_sufficiency([], resident=None)
    assert verdict.verdict == UNDETERMINED
    assert verdict.sufficient is None
    assert verdict.is_insufficient is False


def test_an_unassessed_read_serves_rather_than_escalates() -> None:
    """No measurement, no escalation — and the reason says which absence it was."""
    verdict = assess_sufficiency(_rows("anything at all"), resident=True)
    assert verdict.verdict == UNDETERMINED
    assert verdict.reasons == (REASON_NO_MEASURE,)

    route = route_sufficiency(verdict, budget=1)
    assert route.route == ROUTE_SERVE
    assert route.reason == REASON_NO_MEASURE


def test_a_measure_that_met_its_threshold_is_sufficient() -> None:
    evidence = _rows("acme corp signed the contract", "unrelated filler")
    verdict = assess_sufficiency(
        evidence, anchors=["ACME Corp"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.sufficient is True
    assert verdict.reasons == (REASON_MEASURES_MET,)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


def test_an_uncovered_anchor_is_insufficient_and_names_the_measure() -> None:
    evidence = _rows("unrelated filler", "more filler")
    verdict = assess_sufficiency(
        evidence,
        anchors=["ACME Corp", "northwind"],
        required_anchor_coverage=0.5,
        resident=True,
    )
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_ANCHORS_UNCOVERED,)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


def test_a_partially_covered_anchor_set_is_measured_not_guessed() -> None:
    evidence = _rows("acme corp signed the contract")
    verdict = assess_sufficiency(
        evidence,
        anchors=["acme corp", "northwind"],
        required_anchor_coverage=0.5,
        resident=True,
    )
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.5),)
    assert verdict.verdict == SUFFICIENT


def test_the_anchor_match_is_case_insensitive_and_can_hit_the_row_id() -> None:
    evidence = [{"id": "entity:acme", "content": "signed"}]
    verdict = assess_sufficiency(
        evidence, anchors=["ENTITY:ACME"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT


def test_a_short_anchor_does_not_match_inside_an_unrelated_word() -> None:
    """A substring test over-reports coverage in the fail-open direction.

    ``"ai" in "he said the main thing"`` is ``True``, so a short entity name
    (``AI``, ``PR``, ``Q3``, ``Art``) scored COVERED on text that never mentions
    it — a confident ``sufficient`` on evidence that does not answer the
    question, which is the §14.3 V4 silent green arriving through the measure's
    front door. The comparison beside it (``row.id``) is exact, so the weaker
    signal was also the looser one.
    """
    verdict = assess_sufficiency(
        _rows("He said the main thing"),
        anchors=["AI"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_ANCHORS_UNCOVERED,)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)

    # "Art" must not be covered by "start the process" either.
    assert assess_sufficiency(
        _rows("start the process"),
        anchors=["Art"], required_anchor_coverage=1.0, resident=True,
    ).verdict == INSUFFICIENT


def test_a_short_anchor_still_matches_its_own_word() -> None:
    """The boundary is a boundary, not a length heuristic."""
    verdict = assess_sufficiency(
        _rows("we shipped an AI model in Q3"),
        anchors=["AI", "q3"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


def test_a_multi_word_anchor_matches_across_irregular_whitespace() -> None:
    """Both sides are normalised: spacing is not part of a phrase's identity.

    The needle was collapsed while the row text was matched raw, so an anchor
    and a row that share irregular spacing stopped matching — and a wrapped
    ``content``/``summary`` (a real multi-paragraph wire field) made a
    multi-word entity unmatchable. Under-reporting is fail-closed, but the
    docstring claimed a property the code did not have.
    """
    double_spaced = assess_sufficiency(
        _rows("acme  corp signed"),
        anchors=["ACME  Corp"], required_anchor_coverage=1.0, resident=True)
    assert double_spaced.verdict == SUFFICIENT

    wrapped = assess_sufficiency(
        _rows("acme\ncorp signed the contract"),
        anchors=["ACME Corp"], required_anchor_coverage=1.0, resident=True)
    assert wrapped.verdict == SUFFICIENT


def test_an_entity_named_in_subject_or_topics_counts_as_coverage() -> None:
    """A hit whose ``subject``/``topics`` name the anchor IS that evidence.

    ``SearchResult.to_dict()`` carries the point's entities at ``subject``
    (``{id, name, kind}``) and ``topics`` (a list) — outside ``content``. Reading
    only ``content`` scored a row that literally IS the ACME Corp evidence as
    ``anchor-coverage 0.0``, escalating a tier for the hit that already answered
    the question. ``kind`` is deliberately NOT read: it is a label class, and an
    anchor like ``point`` would then match every row.
    """
    row = SearchResult(
        id="p1", content="signed the contract", point_kind="fact",
        subject={"id": "subject:acme", "name": "ACME Corp", "kind": "org"},
        topics=["ACME Corp", "contracts"],
    ).to_dict()
    verdict = assess_sufficiency(
        [row], anchors=["ACME Corp"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)

    # The label class is not an entity name: it must not create coverage.
    kind_only = SearchResult(
        id="p2", content="signed the contract", point_kind="fact",
        subject={"id": "subject:acme", "name": "ACME Corp", "kind": "organization"},
    ).to_dict()
    assert assess_sufficiency(
        [kind_only], anchors=["organization"],
        required_anchor_coverage=1.0, resident=True,
    ).verdict == INSUFFICIENT


def _rows_without_link(*contents: str) -> list[dict]:
    """Rows READ WITH provenance on that carry no LINK (a real wire shape).

    ``SearchResult.to_dict()`` emits the ``provenance`` block whenever
    ``source_ref``, ``captured_at`` or ``currency`` is present, so a hit with
    ``captured_at`` alone proves the field WAS fetched while carrying no source.
    That is evidence with no source — a measured ``0.0`` for the row — and it is
    a different fact from a row whose provenance was never fetched at all.
    """
    return [
        SearchResult(
            id=f"q{i}", content=content, point_kind="fact",
            captured_at="2026-01-01T00:00:00Z",
        ).to_dict()
        for i, content in enumerate(contents)
    ]


def test_thin_provenance_is_insufficient() -> None:
    evidence = _rows("acme corp signed", provenance=True)
    evidence += _rows_without_link("filler")  # fetched, but carries no link
    assert evidence[1]["provenance"] == {"captured_at": "2026-01-01T00:00:00Z"}
    verdict = assess_sufficiency(
        evidence, required_provenance_coverage=1.0, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_PROVENANCE_THIN,)
    assert verdict.measures == ((MEASURE_PROVENANCE_COVERAGE, 0.5),)


def test_the_provenance_link_is_read_from_the_wire_shape_the_read_path_emits() -> None:
    """``SearchResult.to_dict()`` nests the link at ``provenance.source``.

    The module read only the top-level ``source_ref`` ATTRIBUTE key, which no
    producer emits on the wire — so on every real assembled-evidence row
    (``sdk.search``/``tortoise_fts_query`` return ``[r.to_dict() …]``, and
    ``ask_lane`` consumes exactly those) the provenance measure was permanently
    unassessed: a declared measure with no producer, one layer down. Built from
    a REAL ``SearchResult`` so the fixture cannot drift from the wire again.
    """
    row = SearchResult(
        id="p1", content="acme corp signed", point_kind="fact",
        source_ref="https://example.test/acme",
    ).to_dict()
    assert row["provenance"] == {"source": "https://example.test/acme"}
    assert evidence_provenance(row) == "https://example.test/acme"
    assert evidence_record(row).provenance == "https://example.test/acme"


def test_a_real_search_result_row_is_assessed_for_provenance() -> None:
    """The measure has a PRODUCER on the only shape a caller actually holds."""
    rows = _rows("acme corp signed", "netherlands renewal", provenance=True)
    verdict = assess_sufficiency(
        rows, required_provenance_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.measures == ((MEASURE_PROVENANCE_COVERAGE, 1.0),)


def test_a_partially_provenanced_real_wire_read_is_measured_not_unassessed() -> None:
    rows = _rows("acme corp signed", provenance=True) + _rows_without_link("filler")
    verdict = assess_sufficiency(
        rows, required_provenance_coverage=0.9, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_PROVENANCE_THIN,)
    assert verdict.measures == ((MEASURE_PROVENANCE_COVERAGE, 0.5),)


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_an_empty_anchor_is_refused_not_counted_as_covered(blank: str) -> None:
    """``"" in "anything"`` is ``True``, so a blank anchor is FULL coverage.

    ``anchors=[""]`` with ``required_anchor_coverage=1.0`` measured ``1.0`` and
    returned ``sufficient`` — a confident green from an anchor that names no
    entity at all. Every other input in this module is refused when it is
    unusable; an anchor element is no different, and an empty element is a
    realistic residue of anchor resolution (``extract_subject_terms`` returns
    ``[]`` for shapes with no resolvable subject), not a hypothetical.
    """
    with pytest.raises(ValueError):
        assess_sufficiency(
            _rows("unrelated filler"), anchors=[blank],
            required_anchor_coverage=1.0, resident=True)


def test_a_blank_anchor_cannot_hide_among_valid_ones() -> None:
    with pytest.raises(ValueError):
        assess_sufficiency(
            _rows("acme corp signed"), anchors=["acme corp", " "],
            required_anchor_coverage=1.0, resident=True)
    with pytest.raises(TypeError):
        assess_sufficiency(
            _rows("acme corp signed"), anchors=["acme corp", 7],  # type: ignore[list-item]
            required_anchor_coverage=1.0, resident=True)


def test_an_anchor_threshold_with_no_anchors_is_refused_not_silently_unassessed() -> None:
    """A configured measure cannot be disabled by an empty resolver result.

    ``anchors=()`` with a supplied threshold skipped the measure entirely and
    reported ``no-measure`` — blaming a configuration that DOES exist, and
    converting "the caller could not even resolve what the question is about"
    into an unassessed serve. Disabling the measure is the caller's explicit
    act (``required_anchor_coverage=None``), not a side effect of an empty list.
    """
    with pytest.raises(ValueError):
        assess_sufficiency(
            _rows("acme corp signed"), anchors=(),
            required_anchor_coverage=0.9, resident=True)

    # Opting out explicitly is still legitimate, and still says `no-measure`.
    opted_out = assess_sufficiency(
        _rows("acme corp signed"), anchors=(),
        required_anchor_coverage=None, resident=True)
    assert opted_out.verdict == UNDETERMINED
    assert opted_out.reasons == (REASON_NO_MEASURE,)


def test_a_provenance_measure_over_unfetched_rows_is_unassessed() -> None:
    """A flag-off read must not be scored as insufficient evidence.

    ``search_provenance_enabled()`` off means ``source_ref`` is never fetched, so
    no row carries the key. Scoring that as ``0.0`` would red the cheap tier for
    an unfetched field — the §17.3 "unassessed is not negative" discipline, one
    layer down. The measure is therefore NOT ASSESSED, and a verdict resting on
    it alone is ``undetermined``.
    """
    evidence = _rows("acme corp signed", "filler")  # no provenance anywhere
    verdict = assess_sufficiency(
        evidence, required_provenance_coverage=0.9, resident=True)
    assert verdict.verdict == UNDETERMINED
    # A SPECIFIC reason, not `no-measure`: a measure WAS configured and could not
    # be taken, and the two states are not the same claim.
    assert verdict.reasons == (REASON_PROVENANCE_UNASSESSED,)
    assert verdict.measures == ()


def test_a_configured_provenance_measure_that_could_not_be_taken_never_greens() -> None:
    """A CONFIGURED measure that was never taken cannot be satisfied by another.

    This test used to assert ``sufficient`` here. That was the fail-open this
    module exists to kill: the caller demanded 90% sourced evidence, no row had
    fetched provenance at all, and the verdict reported a green because the
    ANCHOR measure passed. "Unassessed neither helps nor hurts" is true of a
    measure the caller did not ask for; it is false of one they configured.
    Reverting the guard reddens this test.
    """
    evidence = _rows("acme corp signed", "filler")  # no provenance at all
    verdict = assess_sufficiency(
        evidence,
        anchors=["acme corp"],
        required_anchor_coverage=1.0,
        required_provenance_coverage=0.9,
        resident=True,
    )
    assert verdict.verdict == UNDETERMINED
    assert verdict.reasons == (REASON_PROVENANCE_UNASSESSED,)
    assert verdict.sufficient is None
    # The measure that DID run is still reported — the caller can see it passed.
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)

    # Opting out of the provenance measure is the caller's explicit act, and it
    # restores the green — the point is that omission must be DECLARED.
    opted_out = assess_sufficiency(
        evidence,
        anchors=["acme corp"],
        required_anchor_coverage=1.0,
        required_provenance_coverage=None,
        resident=True,
    )
    assert opted_out.verdict == SUFFICIENT


def test_there_is_no_default_threshold() -> None:
    """Every threshold is the caller's, and there are none to omit.

    A default here would be a target nobody chose (§14.1 O2), wearing a
    constant's clothes — so the signature has no default for either threshold.
    """
    params = inspect.signature(assess_sufficiency).parameters
    assert params["required_anchor_coverage"].default is None
    assert params["required_provenance_coverage"].default is None

    # Omitting both is not "pass" — it is `no-measure`.
    verdict = assess_sufficiency(_rows("acme corp"), anchors=["acme corp"], resident=True)
    assert verdict.verdict == UNDETERMINED


@pytest.mark.parametrize("bad", [-0.1, 1.1, "0.9", True, object()])
def test_an_unusable_threshold_is_refused_not_defaulted(bad: object) -> None:
    # ``anchors`` is NON-EMPTY on purpose: with none supplied the earlier "a
    # configured measure needs something to measure" guard raises first, and this
    # test would then pass with the threshold validation DELETED — a guard with no
    # failure behind it.
    with pytest.raises(ValueError):
        assess_sufficiency(
            _rows("acme corp"), anchors=["acme corp"],
            required_anchor_coverage=bad, resident=True)  # type: ignore[arg-type]


def test_the_span_ceiling_is_the_one_home_and_cannot_drift() -> None:
    """``commit_schema`` owns the span-address rule, and it CLAMPS past the ceiling.

    E4 / ``#5007``: an offset past ``SPAN_OFFSET_MAX`` is silently clamped on
    write, so the committed address and the stored address diverge — the unit
    would then claim a span it does not have. ``SourceSpan`` refuses it. The
    constant is repeated rather than imported because this module keeps its
    imports pure (``commit_schema`` pulls pydantic and yaml at import), so THIS
    assertion is what makes the repetition safe: the two cannot drift.
    """
    from tortoise.commit_schema import SPAN_OFFSET_MAX as ONE_HOME

    assert SPAN_OFFSET_MAX == ONE_HOME

    # At the ceiling is still a real address.
    assert SourceSpan(source_id="s", start=0, end=ONE_HOME).end == ONE_HOME

    with pytest.raises(ValueError):
        SourceSpan(source_id="s", start=0, end=ONE_HOME + 1)
    with pytest.raises(ValueError):
        SourceSpan(source_id="s", turn=ONE_HOME + 1)


# ── §17.3: residency outranks sufficiency ─────────────────────────────────


def test_a_non_bool_resident_is_refused_not_treated_as_established() -> None:
    """§17.3's guard is an ALLOW-LIST, and its input is type-checked.

    ``resident is None or resident is False`` are IDENTITY tests: every falsy
    non-``False`` value (``0`` from a JSON boolean column, ``""``, ``[]``,
    ``0.0``) satisfied neither, so the assessment proceeded as if residency had
    been ESTABLISHED — and a read the cache never saw then walked a tier, the
    one wrong answer §17.3 says is indistinguishable from a right one.
    """
    for bad in (0, 1, 0.0, "", [], {}, object()):
        with pytest.raises(TypeError):
            assess_sufficiency(_rows("filler"), resident=bad)  # type: ignore[arg-type]


def test_a_residency_miss_is_never_an_insufficiency() -> None:
    """The collapse guard, at the sufficiency end of the seam.

    A node the cache cannot see makes *"insufficient"* unestablished (§17.3), so
    the assessment REFUSES to judge — even over evidence that would otherwise be
    a clear failure.
    """
    miss = assess_sufficiency(_rows("filler"), resident=False)
    assert miss.verdict == UNDETERMINED
    assert miss.reasons == (REASON_RESIDENT_MISS,)
    assert miss.is_insufficient is False

    unassessed = assess_sufficiency(_rows("filler"), resident=None)
    assert unassessed.reasons == (REASON_RESIDENT_UNASSESSED,)


def test_a_residency_miss_routes_to_the_record_before_any_tier_is_walked() -> None:
    """The two routers COMPOSE, and the precedence holds across the seam."""
    miss = assess_sufficiency(_rows("filler"), resident=False)
    # The sufficiency router alone would serve; the residency router owns the repair.
    assert route_sufficiency(miss, budget=1).route == ROUTE_SERVE
    assert route_residency(
        marker=RESIDENCY_NOT_HOT, sufficient=miss.sufficient).route == ROUTE_FETCH_RECORD
    # And a MEASURED insufficiency on a RESIDENT read still reaches the tier walk.
    thin = assess_sufficiency(
        _rows("filler"), anchors=["acme corp"], required_anchor_coverage=1.0, resident=True)
    assert thin.verdict == INSUFFICIENT
    assert route_residency(
        marker=RESIDENCY_HOT, sufficient=thin.sufficient).route == ROUTE_ESCALATE_TIER


def test_the_verdict_feeds_the_residency_router_three_distinctly() -> None:
    """``True`` / ``False`` / ``None`` must not collapse on the way across."""
    sufficient = assess_sufficiency(
        _rows("acme corp"), anchors=["acme corp"],
        required_anchor_coverage=1.0, resident=True)
    insufficient = assess_sufficiency(
        _rows("filler"), anchors=["acme corp"],
        required_anchor_coverage=1.0, resident=True)
    undetermined = assess_sufficiency(_rows("filler"), resident=True)

    seen = [
        route_residency(marker=RESIDENCY_HOT, sufficient=v.sufficient).route
        for v in (sufficient, insufficient, undetermined)
    ]
    assert seen == [ROUTE_SERVE, ROUTE_ESCALATE_TIER, ROUTE_SERVE]
    assert len({v.sufficient for v in (sufficient, insufficient, undetermined)}) == 3


def test_the_bound_is_carried_across_the_seam_by_the_ROUTE() -> None:
    """A refused walk must not be re-walked one module over.

    ``route_residency`` escalates on ``sufficient is False`` UNCONDITIONALLY, so
    handing it the raw verdict re-walked a tier ``route_sufficiency`` had just
    refused (budget gone, or a walk already behind it) — the §12.7 *router
    overhead* this module claims to bound, reintroduced at the seam. The route
    carries the BOUNDED value: ``None`` when the walk was refused.
    """
    verdict = assess_sufficiency(
        _rows("filler"), anchors=["acme corp"],
        required_anchor_coverage=1.0, resident=True)
    refused = route_sufficiency(verdict, budget=0)
    walked = route_sufficiency(verdict, budget=1)

    assert verdict.sufficient is False           # what was MEASURED
    assert refused.sufficient is None            # what will be PAID FOR
    assert walked.sufficient is False
    assert refused.escalates_tier is False
    assert walked.escalates_tier is True

    # The composition the docstrings require, pinned across the real seam.
    assert route_residency(
        marker=RESIDENCY_HOT, sufficient=refused.sufficient).route == ROUTE_SERVE
    assert route_residency(
        marker=RESIDENCY_HOT, sufficient=walked.sufficient,
    ).route == ROUTE_ESCALATE_TIER


# ── the bounded trigger ───────────────────────────────────────────────────


def _insufficient() -> SufficiencyVerdict:
    return assess_sufficiency(
        _rows("filler"), anchors=["acme corp"],
        required_anchor_coverage=1.0, resident=True)


def test_empty_evidence_on_a_resident_read_escalates_once() -> None:
    verdict = assess_sufficiency([], resident=True)
    assert verdict.reasons == (REASON_EVIDENCE_EMPTY,)

    route = route_sufficiency(verdict, budget=1)
    assert route.route == ROUTE_ESCALATE_TIER
    assert route.route in ROUTES
    assert route.escalated is True
    assert route.budget_remaining == 0


def test_a_second_insufficient_verdict_is_reported_not_re_walked() -> None:
    """§12.7 "router overhead": an unbounded repair loop costs more than the read.

    The second look at the same answer is REPORTED (``budget-exhausted``), never
    re-walked — and the verdict still says the evidence was short, so a caller
    cannot mistake the serve for a green.
    """
    verdict = _insufficient()
    first = route_sufficiency(verdict, budget=1)
    assert first.route == ROUTE_ESCALATE_TIER

    second = route_sufficiency(verdict, escalated=first.escalated, budget=first.budget_remaining)
    assert second.route == ROUTE_SERVE
    assert second.reason == REASON_BUDGET_EXHAUSTED
    assert second.verdict == INSUFFICIENT


def test_an_already_escalated_verdict_does_not_claim_an_exhausted_budget() -> None:
    """The reason is a CLAIM, and ``budget_remaining`` must not contradict it.

    ``route_sufficiency(insufficient, escalated=True, budget=3)`` reported
    ``escalation-budget-exhausted`` with ``budget_remaining=3`` — two causes
    ("already walked" and "nothing left to spend") sharing one reason, so the
    record asserted an exhaustion that had not happened. ``residency_router.py``
    states the rule this broke: *"the reason must name the truth too, or a
    `reason`-keyed consumer reads a claim."*
    """
    route = route_sufficiency(_insufficient(), escalated=True, budget=3)
    assert route.route == ROUTE_SERVE
    assert route.reason == REASON_ALREADY_ESCALATED
    assert route.reason != REASON_BUDGET_EXHAUSTED
    assert route.budget_remaining == 3          # nothing was actually spent
    assert route.verdict == INSUFFICIENT        # still not a green

    # ... and a genuinely empty budget still says so, with nothing left.
    spent = route_sufficiency(_insufficient(), escalated=True, budget=0)
    assert spent.reason == REASON_BUDGET_EXHAUSTED
    assert spent.budget_remaining == 0


def test_there_is_no_default_budget() -> None:
    """§12.3's tier count is OPEN — so it cannot be settled by an omission.

    ``budget: int = 1`` IS the one-step/two-tier policy, hard-coded as a
    default for exactly the callers who did not choose it, while the docstring
    claimed it was not hard-coded here. Same class of un-chosen target
    ``test_there_is_no_default_threshold`` refuses for the thresholds.
    """
    budget = inspect.signature(route_sufficiency).parameters["budget"]
    assert budget.default is inspect.Parameter.empty
    assert budget.kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        route_sufficiency(_insufficient())  # type: ignore[call-arg]


def test_a_zero_budget_never_escalates() -> None:
    route = route_sufficiency(_insufficient(), budget=0)
    assert route.route == ROUTE_SERVE
    assert route.reason == REASON_BUDGET_EXHAUSTED


def test_rescinding_a_sufficiency_verdict_is_refused_not_coerced() -> None:
    """A bare boolean cannot carry WHY, and ``None`` is a real state."""
    with pytest.raises(TypeError):
        route_sufficiency(False, budget=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        route_sufficiency(None, budget=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [-1, True, 1.0, "1"])
def test_an_unusable_budget_is_refused(bad: object) -> None:
    with pytest.raises(ValueError):
        route_sufficiency(_insufficient(), budget=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [0, 0.0, "", [], {}, None, 1, "yes"])
def test_the_factory_refuses_a_non_bool_escalated(bad: object) -> None:
    """``escalated`` is the parameter whose job is to STOP a second walk.

    It was never type-checked, so a falsy non-bool (``0`` from a JSON column,
    ``""``, ``[]``) read as "not escalated" and silently bought a second tier —
    the same type gap the ``resident`` guard closed, on the other input that
    bounds cost. A truthy non-bool was refused only one layer down, by the route
    type, so the guard was asymmetric.
    """
    with pytest.raises(TypeError):
        route_sufficiency(_insufficient(), budget=3, escalated=bad)  # type: ignore[arg-type]


def test_a_route_cannot_carry_a_reason_that_contradicts_its_verdict() -> None:
    """The ROUTE is the artifact a consumer records, so it holds the invariants.

    ``_VERDICT_REASONS`` refused an incoherent pairing on the verdict and not on
    the route — which is where a reason-keyed consumer actually reads it, and the
    type is public. An ``escalate-tier`` route on a sufficient verdict, or an
    escalation justified by ``measures-met``, was constructible.

    Every ``escalate-tier`` case names the MESSAGE of the check it pins, not just
    that *some* ``ValueError`` fired: with ``escalated`` left at its ``False``
    default the ``escalate-tier ⇒ escalated`` axis raises FIRST, so both
    coherence checks in that block were reachable by no test and could be
    deleted with this function still green (#5009 review). The factory only ever
    emits ``escalate-tier`` for a measured insufficiency whose reason is one of
    ``_VERDICT_REASONS[INSUFFICIENT]``, so the refuse-only inputs below are the
    hand-built/replayed shapes.
    """
    with pytest.raises(ValueError, match="MEASURED insufficiency"):
        # Directs a walk on a GREEN — the one thing an escalation cannot mean.
        SufficiencyRoute(route=ROUTE_ESCALATE_TIER, reason=REASON_ANCHORS_UNCOVERED,
                         verdict=SUFFICIENT, escalated=True)
    with pytest.raises(ValueError, match="not an insufficiency of the evidence"):
        # ... and the reason it escalates ON must be an insufficiency too, so a
        # policy-shaped reason (the budget pair, or a green's ``measures-met``)
        # cannot be replayed as the cause of a walk.
        SufficiencyRoute(route=ROUTE_ESCALATE_TIER, reason=REASON_MEASURES_MET,
                         verdict=INSUFFICIENT, escalated=True)
    with pytest.raises(ValueError, match="not an insufficiency of the evidence"):
        SufficiencyRoute(route=ROUTE_ESCALATE_TIER, reason=REASON_BUDGET_EXHAUSTED,
                         verdict=INSUFFICIENT, escalated=True, budget_remaining=0)
    with pytest.raises(ValueError, match="does not belong to a"):
        SufficiencyRoute(route=ROUTE_SERVE, reason=REASON_RESIDENT_MISS,
                         verdict=SUFFICIENT)
    with pytest.raises(ValueError, match="bounded insufficiency"):
        # A budget reason implies an insufficiency — never a green.
        SufficiencyRoute(route=ROUTE_SERVE, reason=REASON_BUDGET_EXHAUSTED,
                         verdict=SUFFICIENT, budget_remaining=0)

    # Every route the factory emits still constructs.
    assert route_sufficiency(_insufficient(), budget=0).reason == REASON_BUDGET_EXHAUSTED
    assert route_sufficiency(_insufficient(), budget=1).route == ROUTE_ESCALATE_TIER
    assert route_sufficiency(
        assess_sufficiency(_rows("x"), resident=True), budget=1).route == ROUTE_SERVE


def test_a_route_cannot_contradict_its_own_escalation_or_budget() -> None:
    """The ``escalated`` axis and the budget's coherence are type-held too.

    The factory gets these right, but the route is what a consumer RECORDS and
    later REPLAYS (``route_sufficiency(verdict, escalated=route.escalated,
    budget=route.budget_remaining)``), so a hand-built or deserialised route that
    contradicts itself defeated the ONE bound that stops a walk loop — the same
    standard this type already applies to the verdict and the reason.
    """
    with pytest.raises(ValueError):
        # Directs a walk while claiming none happened: replay walks forever.
        SufficiencyRoute(route=ROUTE_ESCALATE_TIER, reason=REASON_ANCHORS_UNCOVERED,
                         verdict=INSUFFICIENT, escalated=False, budget_remaining=0)
    with pytest.raises(ValueError):
        # Claims a walk is behind it while its own flag says otherwise.
        SufficiencyRoute(route=ROUTE_SERVE, reason=REASON_ALREADY_ESCALATED,
                         verdict=INSUFFICIENT, escalated=False, budget_remaining=5)
    with pytest.raises(ValueError):
        # Claims the budget is gone while reporting three steps remaining.
        SufficiencyRoute(route=ROUTE_SERVE, reason=REASON_BUDGET_EXHAUSTED,
                         verdict=INSUFFICIENT, budget_remaining=3)

    # Every route the factory emits still constructs, in every bound state.
    verdict = assess_sufficiency(
        _rows("filler"), anchors=["acme corp"],
        required_anchor_coverage=1.0, resident=True)
    for budget, escalated in ((0, False), (1, False), (2, True), (0, True)):
        assert route_sufficiency(
            verdict, budget=budget, escalated=escalated).route in ROUTES


# ── the route is a directive, not a status, and never the other router ────


def test_a_sufficiency_route_is_never_the_residency_repair() -> None:
    with pytest.raises(ValueError):
        SufficiencyRoute(route=ROUTE_FETCH_RECORD, reason=REASON_MEASURES_MET,
                         verdict=SUFFICIENT)


@pytest.mark.parametrize("verdict", [
    assess_sufficiency([], resident=True),
    _insufficient(),
    assess_sufficiency(_rows("x"), resident=True),
])
def test_route_sufficiency_only_ever_emits_two_of_the_three_routes(
        verdict: SufficiencyVerdict) -> None:
    emitted = route_sufficiency(verdict, budget=1).route
    assert emitted in (ROUTE_SERVE, ROUTE_ESCALATE_TIER)
    assert emitted in ROUTES


def test_the_route_names_are_not_status_terms() -> None:
    """§17.3: the read-status vocabulary is four terms and mints no fifth."""
    statuses = set(status_vocabulary.CLIENT_STATUS_TERMS)  # the recorded four
    assert set(ROUTES).isdisjoint(statuses), (ROUTES, statuses)


def test_the_decision_itself_refuses_an_unrecognised_verdict_or_route() -> None:
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict="maybe")
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=SUFFICIENT, reasons=("because-i-said-so",))
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=SUFFICIENT, measures=(("vibes", 1.0),))
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=SUFFICIENT, reasons=(REASON_MEASURES_MET,),
                           measures=((MEASURE_ANCHOR_COVERAGE, 1.5),))
    with pytest.raises(ValueError):
        SufficiencyRoute(route="fetch", reason=REASON_MEASURES_MET, verdict=SUFFICIENT)
    with pytest.raises(ValueError):
        SufficiencyRoute(route=ROUTE_SERVE, reason=REASON_MEASURES_MET,
                         verdict=SUFFICIENT, budget_remaining=-1)


def test_a_verdict_must_carry_a_reason_that_belongs_to_it() -> None:
    """``reason`` is a CLAIM, and the route republishes it as WHY it fired.

    A reasonless ``insufficient`` verdict made the route assert ``evidence-empty``
    — a cause nothing measured — and an ``insufficient`` verdict justified by
    ``measures-met`` escalated on a claim about a measurement that PASSED. The
    allow-list is what keeps the route from naming a cause its verdict never
    stated.
    """
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=INSUFFICIENT)  # nothing to report WHY
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=INSUFFICIENT, reasons=(REASON_MEASURES_MET,))
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=SUFFICIENT, reasons=(REASON_ANCHORS_UNCOVERED,))
    with pytest.raises(ValueError):
        SufficiencyVerdict(verdict=UNDETERMINED, reasons=(REASON_MEASURES_MET,))

    # Every verdict ``assess_sufficiency`` emits still constructs, with its own
    # reason set — the allow-list describes the producers, it does not narrow them.
    for verdict in (assess_sufficiency([], resident=True), _insufficient(),
                    assess_sufficiency(_rows("x"), resident=True)):
        assert verdict.reasons
        assert route_sufficiency(verdict, budget=1).reason == verdict.reasons[0]


def test_a_route_refuses_scalars_of_the_wrong_type() -> None:
    """Every sibling refuses bad input; ``budget_remaining``/``escalated`` did not.

    ``budget_remaining=2.0`` and ``escalated="yes"`` constructed, and
    ``budget_remaining="3"`` crashed with the ``TypeError`` a comparison raises
    instead of a refusal.
    """
    for bad in (2.0, "3", True):
        with pytest.raises(ValueError):
            SufficiencyRoute(route=ROUTE_SERVE, reason=REASON_MEASURES_MET,
                             verdict=SUFFICIENT, budget_remaining=bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SufficiencyRoute(route=ROUTE_SERVE, reason=REASON_MEASURES_MET,
                         verdict=SUFFICIENT, escalated="yes")  # type: ignore[arg-type]


# ── the two structural pins that keep it from becoming the rejected design ─


def test_the_router_takes_no_query_class() -> None:
    """§12.4 rejects the query-type classifier — so it cannot be typed into the API.

    ``anchors`` is the ONE query-derived input and it is a set of referenced
    ENTITIES (a coverage measurement over the answer), not a question type.
    """
    forbidden = {"query", "query_type", "query_class", "complexity", "intent",
                 "kind", "question_type", "question_class", "difficulty"}
    for fn in (assess_sufficiency, route_sufficiency, plan_writeback):
        names = {p.casefold() for p in inspect.signature(fn).parameters}
        assert names.isdisjoint(forbidden), (fn.__name__, names & forbidden)


def test_the_router_is_pure_no_io_no_model_no_clock() -> None:
    """§12.7 "router overhead": the router must not cost more than the retrieval.

    Pinned on the module's own imports rather than on behaviour, because the
    failure mode is an ADDITION (a clock, a model call, a graph read) that no
    behavioural test would notice.
    """
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative in-package import
                imported.add(f".{node.module}")
            elif node.module:
                imported.add(node.module.split(".")[0])
    # ``unicodedata`` is here because the anchor boundary is decided from the
    # MATCH's neighbours: a bare substring test scores a short entity name
    # (``AI``, ``Art``) as covered by unrelated text, while a Unicode ``\\w``
    # lookaround can never hold next to an ideograph. It is a pure table lookup —
    # no I/O, no clock, no model.
    allowed_stdlib = {"__future__", "collections", "hashlib", "dataclasses",
                      "typing", "unicodedata"}
    # The ONLY in-package import is the route vocabulary's single home.
    allowed_local = {".residency_router"}
    assert imported <= allowed_stdlib | allowed_local, imported - (allowed_stdlib | allowed_local)
    assert not {"time", "socket", "subprocess", "os", "uuid", "random"} & imported


# ── §12.5 the escalation write-back ────────────────────────────────────────


def _span() -> SourceSpan:
    return SourceSpan(source_id="source:raw-1", turn=3, start=10, end=24)


def _finding(**overrides: object) -> VerifiedFinding:
    base = dict(claim="acme corp signed the contract in March",
                span=_span(), claim_class="semantic", verified=True)
    base.update(overrides)
    return VerifiedFinding(**base)  # type: ignore[arg-type]


def test_a_verified_finding_with_a_span_writes_back_as_a_derived_unit() -> None:
    plan = plan_writeback([_finding()])
    assert plan.ok is True
    assert plan.refusals == ()
    (unit,) = plan.units
    assert isinstance(unit, SummaryUnit)
    assert unit.derived is True            # never authoritative
    assert unit.claim == "acme corp signed the contract in March"  # verbatim
    assert unit.claim_class == "semantic"  # the finding's class, unchanged
    assert unit.span == _span()            # the ADDRESS, not the payload
    assert unit.unit_key == unit_key(_span(), unit.claim)


def test_a_finding_without_a_span_is_refused_not_downgraded() -> None:
    """*"a written-back unit carries its own source link, or it is an
    unattributable belief"* (#5009)."""
    plan = plan_writeback([_finding(span=None)])
    assert plan.units == ()
    assert [r.reason for r in plan.refusals] == [REFUSAL_NO_PROVENANCE]
    assert plan.ok is False


def test_an_unverified_finding_is_refused_and_reported() -> None:
    plan = plan_writeback([_finding(verified=False)])
    assert plan.units == ()
    assert [r.reason for r in plan.refusals] == [REFUSAL_UNVERIFIED]
    assert plan.refusals[0].claim == "acme corp signed the contract in March"


def test_an_empty_claim_is_refused() -> None:
    plan = plan_writeback([_finding(claim="   ")])
    assert [r.reason for r in plan.refusals] == [REFUSAL_NO_CLAIM]


def test_the_write_back_refuses_to_promote_a_claim_class() -> None:
    """§12.7 "incorrect promotion": an episodic fact must not become a rule."""
    episodic = _finding(claim_class="episodic")
    plan = plan_writeback([episodic], promote_to="semantic")
    assert plan.units == ()
    assert [r.reason for r in plan.refusals] == [REFUSAL_PROMOTION]

    # Naming the finding's OWN class is not a promotion.
    same = plan_writeback([episodic], promote_to="episodic")
    assert same.ok is True
    assert same.units[0].claim_class == "episodic"


def test_the_same_verified_finding_writes_back_once() -> None:
    """The attribution loop: a repeated escalation must not pay for a duplicate."""
    first = plan_writeback([_finding()])
    again = plan_writeback([_finding()])
    both = plan_writeback([_finding(), _finding()])
    assert len(first.units) == 1
    assert len(again.units) == 1
    assert len(both.units) == 1
    assert both.units[0].unit_key == first.units[0].unit_key


def test_two_classes_for_one_identity_are_refused_not_settled_by_order() -> None:
    """The dedupe may not decide a claim's CLASS, and must not decide it silently.

    ``unit_key`` is ``(span, normalised claim)``, so two findings sharing an
    identity but disagreeing about the class collapsed to ONE unit — whichever
    arrived first — with no refusal and ``ok`` still True. That is §12.7's
    incorrect promotion arriving through the dedupe instead of ``promote_to``:
    an episodic fact and a semantic rule for the same span+claim, decided by
    iteration order. Neither may be written back.
    """
    episodic = _finding(claim="acme corp signed", claim_class="episodic")
    semantic = _finding(claim="acme corp signed", claim_class="semantic")

    forward = plan_writeback([episodic, semantic])
    backward = plan_writeback([semantic, episodic])

    for plan in (forward, backward):
        assert plan.units == ()
        assert [r.reason for r in plan.refusals] == [REFUSAL_CLASS_CONFLICT]
        assert plan.ok is False


def test_identity_is_the_span_plus_the_claim_not_the_span_alone() -> None:
    other_claim = _finding(claim="acme corp renewed in April")
    plan = plan_writeback([_finding(), other_claim])
    assert len(plan.units) == 2
    assert len({u.unit_key for u in plan.units}) == 2
    # A different address for the same claim is a different unit too.
    elsewhere = _finding(span=SourceSpan(source_id="source:raw-2", turn=7, start=1, end=9))
    assert unit_key(elsewhere.span, elsewhere.claim) != unit_key(_span(), _finding().claim)


def test_whitespace_and_case_do_not_mint_a_second_unit() -> None:
    variant = _finding(claim="Acme Corp   signed the contract in March")
    plan = plan_writeback([_finding(), variant])
    assert len(plan.units) == 1
    # ... and the unit keeps the FIRST occurrence verbatim.
    assert plan.units[0].claim == "acme corp signed the contract in March"


def test_a_summary_unit_can_neither_carry_the_raw_payload_nor_supersede_it() -> None:
    """Extractor §2.4.2: the raw fact behind a written-back finding is immutable.

    Pinned as a REFUSAL OF FIELDS, because the failure is a future addition: a
    ``raw``/``payload`` field turns the summary tier into a second divergent copy
    of the evidence, and a ``supersedes`` field lets it overwrite the log it is
    supposed to summarise.
    """
    names = {f.name for f in dataclasses.fields(SummaryUnit)}
    assert names == {"unit_key", "claim", "claim_class", "span", "derived"}
    forbidden = {"raw", "raw_text", "payload", "source_text", "content",
                 "supersedes", "superseded_by", "replaces", "overwrites"}
    assert names.isdisjoint(forbidden), names & forbidden


def test_planning_a_write_back_mutates_nothing_it_was_given() -> None:
    """The write-back is a PLAN over verified findings; the findings are untouched."""
    findings = (_finding(), _finding(claim="", verified=True), _finding(verified=False))
    before = tuple(dataclasses.astuple(f) for f in findings)
    plan_writeback(list(findings), promote_to=None)
    assert tuple(dataclasses.astuple(f) for f in findings) == before


@pytest.mark.parametrize("kwargs", [
    {"source_id": ""},
    {"source_id": "s", "start": 1},            # all-or-nothing offsets
    {"source_id": "s", "start": 5, "end": 5},  # end must be past start
    {"source_id": "s", "turn": -1},
    {"source_id": "s", "start": True, "end": 4},
])
def test_an_unaddressable_span_is_refused(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        SourceSpan(**kwargs)


def test_a_summary_unit_cannot_be_constructed_without_its_invariants() -> None:
    """The TYPE holds its own invariants, not only ``plan_writeback``'s output.

    ``SummaryUnit`` is public (``__all__``) and §12.5's persistence seam is the
    caller's, so a hand-built unit is the realistic route by which an
    unattributable belief reaches the store. ``span``/``derived``/``unit_key``
    were type HINTS and defaults only — ``span=None``, ``span="not-a-span"`` and
    ``derived=False`` all constructed fine, so §12.5's "no unattributable
    belief" and "always derived" were true of one function, not of the type.
    """
    good = dict(unit_key=unit_key(_span(), "acme corp signed the contract in March"),
                claim="acme corp signed the contract in March",
                claim_class="semantic", span=_span())
    assert SummaryUnit(**good).derived is True  # the valid shape still works

    with pytest.raises(TypeError):
        SummaryUnit(**{**good, "span": None})  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SummaryUnit(**{**good, "span": "not-a-span"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SummaryUnit(**{**good, "derived": False})
    with pytest.raises(ValueError):
        SummaryUnit(**{**good, "unit_key": ""})
    with pytest.raises(ValueError):
        SummaryUnit(**{**good, "unit_key": "hand-written-key"})
    # ``plan_writeback`` refuses `no-claim`; the type must not accept it either.
    with pytest.raises(ValueError):
        SummaryUnit(**{**good, "claim": "   "})
    with pytest.raises(ValueError):
        SummaryUnit(**{**good, "claim": 7})  # type: ignore[arg-type]


def test_a_unit_key_requires_an_address() -> None:
    with pytest.raises(TypeError):
        unit_key(None, "claim")  # type: ignore[arg-type]


def test_a_write_back_plan_reports_refusals_rather_than_dropping_them() -> None:
    """A partial write-back must be VISIBLE: silently dropping a finding is how a
    belief becomes evidence nobody can audit."""
    plan = plan_writeback([_finding(), _finding(claim="no address", span=None)], promote_to=None)
    assert len(plan.units) == 1
    assert [r.reason for r in plan.refusals] == [REFUSAL_NO_PROVENANCE]
    assert plan.ok is False


def test_a_write_back_refusal_reason_must_be_declared() -> None:
    from tortoise.sufficiency_router import WritebackRefusal

    with pytest.raises(ValueError):
        WritebackRefusal(claim="x", reason="looked-suspicious")


# ── the evidence normaliser ───────────────────────────────────────────────


def test_evidence_rows_are_read_from_the_wire_shape_and_from_attributes() -> None:
    class Row:
        id = "p9"
        content = "acme corp signed"
        source_ref: ClassVar[dict] = {"url": "https://example.test/9"}

    wire = evidence_record({"id": "p1", "content": "signed", "source_ref": {"url": "u"}})
    obj = evidence_record(Row())
    text = evidence_record("just a string")

    assert (wire.id, wire.text, wire.provenance) == ("p1", "signed", {"url": "u"})
    assert (obj.id, obj.text, obj.provenance) == ("p9", "acme corp signed",
                                                  {"url": "https://example.test/9"})
    assert text.text == "just a string"


def test_provenance_absent_is_none_and_provenance_present_is_the_link() -> None:
    """``None`` = the field was never fetched; that is NOT "no source"."""
    assert evidence_provenance({"id": "p1", "content": "x"}) is None
    assert evidence_provenance({"source_ref": {"url": "u"}}) == {"url": "u"}
    assert evidence_provenance({"provenance": {"source_ref": "s1"}}) == "s1"
    assert evidence_provenance({"source_ref": None}) is None


def test_the_exported_provenance_helper_reads_an_attribute_row_too() -> None:
    """``evidence_record`` accepts an object; the public helper must agree.

    ``evidence_record(Row()).provenance`` read the attribute while
    ``evidence_provenance(Row())`` returned ``None`` — the "field never fetched"
    answer for a row that carries it, which suppresses the provenance measure for
    any caller reaching for the exported helper.
    """

    class Row:
        id = "p9"
        content = "acme corp signed"
        source_ref = "u://9"

    assert evidence_provenance(Row()) == "u://9"
    assert evidence_record(Row()).provenance == "u://9"
    assert evidence_provenance(Row()) == evidence_record(Row()).provenance

    class Linkless:
        id = "p10"
        content = "x"

    assert evidence_provenance(Linkless()) is None


def test_the_vocabulary_a_consumer_needs_is_exported() -> None:
    """``__all__`` must carry the vocabularies a ``reason``-keyed consumer reads.

    ``MEASURES`` is how a reader interprets ``verdict.measures``, and
    ``EVIDENCE_PROVENANCE_WIRE_KEY`` is the key the module's own docstring cites
    as the reason the wire path is read. Both were reachable but unexported.
    """
    from tortoise import sufficiency_router as module

    for name in ("MEASURES", "VERDICTS", "REASONS", "REFUSALS", "ROUTES",
                 "EVIDENCE_PROVENANCE_KEY", "EVIDENCE_PROVENANCE_WIRE_KEY"):
        assert name in module.__all__, name
        assert hasattr(module, name), name

    # No dead names: everything exported still exists.
    assert not [n for n in module.__all__ if not hasattr(module, n)]
    # And no underscore-private name is exported.
    assert not [n for n in module.__all__ if n.startswith("_")]


def test_a_non_spaced_script_anchor_matches_its_own_text() -> None:
    """``\\w`` is Unicode-aware, so a word boundary can never hold inside CJK text.

    ``(?<!\\w)合同(?!\\w)`` is incapable of matching ``我们签署了合同``, so an
    ideographic anchor was unmatchable in its own evidence — silent zero coverage
    for every such entity, and a tier bought for a row that answered. The word
    rule follows the ANCHOR's script: an anchor drawn from an UNSPACED script has
    no word separators to respect in its own script, so the substring test IS the
    correct semantics for it.
    """
    verdict = assess_sufficiency(
        _rows("我们签署了合同 with ACME"),
        anchors=["合同"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)

    # ... while an ASCII anchor keeps its boundary rule in the same text.
    assert assess_sufficiency(
        _rows("我们签署了合同 with ACME"),
        anchors=["AI"], required_anchor_coverage=1.0, resident=True,
    ).verdict == INSUFFICIENT


@pytest.mark.parametrize("text", [
    "我们签署了ACME的合同",          # CJK: the match is wrapped in ideographs
    "เราเซ็นสัญญากับACME",            # Thai: no word separators either
    "我们签署了合同 with ACME",       # control: the spaced case already worked
    # A mixed CJK/Latin sentence CONTAINING SEPARATORS. A haystack-level
    # ``" " not in haystack`` gate switched the relaxation off here and scored the
    # row that literally answers at 0.0 — so the boundary is decided at the MATCH.
    "我们与ACME的合同已于 2026 年 3 月 签署",
    "我们签署了ACME的合同，详见附录 A",
    "ACME的合同 有效",
])
def test_a_latin_anchor_matches_inside_unspaced_text(text: str) -> None:
    """The boundary is decided at the MATCH, not for the haystack as a whole.

    A word boundary is a property of the anchor's own script: ``\\w`` is
    Unicode-aware, so a Latin anchor buried in CJK/Thai text can never satisfy
    ``(?<!\\w) … (?!\\w)`` — the ideograph next to it IS a word character. Scoring
    that ``0.0`` bought a tier for the row that answered.

    The rule is therefore adjacency-based: reject an occurrence only when a
    neighbour is a word character of a SPACED script (``a``, ``á``, ``7``,
    ``_``), accept it when the neighbours are unspaced-script letters or
    non-word characters. A haystack-level test ("does this contain a space?") is
    a proxy that breaks on the very common "Chinese sentence containing a year"
    case above, which is why it is not used.
    """
    verdict = assess_sufficiency(
        _rows(text), anchors=["ACME"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == SUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


@pytest.mark.parametrize("text,anchor", [
    ("la práctica firmada", "PR"),   # á is a Unicode word char, not an ASCII one
    ("a Itália assinou", "IT"),
    ("naïve", "NA"),                 # single-token residual: now CLOSED
    ("Práctica", "PR"),
    ("mainthing", "AI"),             # plain infix, no accents involved
    ("he said the main thing", "AI"),
    ("start the process", "ART"),
    # ANCHOR-SIDE script. `isascii()` granted the word rule to Latin anchors only,
    # so a Cyrillic/Greek/Hebrew/Arabic anchor was infix-matched — the same
    # fail-open class, one operand over.
    ("экономика России", "ИИ"),
    ("ΜΑΙΟΣ", "ΑΙ"),
    ("משלם", "של"),
    ("المؤمنون", "من"),
    ("Über", "Ü"),                   # Latin, but with no ASCII letter in the anchor
    # DECORATED Latin neighbours. `Ａ` and `𝐀` ARE `isalpha()` letters — what
    # catches them is the INVERTED polarity (their name's first word is
    # `FULLWIDTH`/`MATHEMATICAL`, not `LATIN`); the `NAME contains LATIN` branch
    # is needed only for the `So` circled/squared forms like `Ⓐ`.
    ("Ａai", "AI"),
    ("aiＡ", "AI"),
    ("𝐀ai", "AI"),
    ("aiⒶ", "AI"),
    # UNNAMED code points split by RANGE, because they are not one thing: an
    # unassigned/surrogate code point has no script and blocks (fail-closed, since
    # reading it as a separator let `AI` match `ai\u0378`), while BMP private use
    # is icon-font territory — a Word Symbol-font bullet is `U+F0B7` — and sits
    # BETWEEN tokens. See the two dedicated tests below.
    ("ai\u0378", "AI"),
])
def test_the_boundary_holds_inside_a_spaced_script(
        text: str, anchor: str) -> None:
    """Adjacency to a Latin letter IS a boundary, so these are not mentions.

    ``á``/``ê``/``ï`` are Unicode word characters that are not ASCII ones, so an
    ASCII-only relaxation matched ``PR`` inside ``práctica``, ``IT`` inside
    ``Itália`` and ``NA`` inside ``naïve`` — dropping the word boundary in a
    spaced script and INFLATING coverage, the fail-open class the boundary exists
    to prevent. Deciding at the match rejects all of them, including the
    single-token case a haystack-level gate could not see.
    """
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


def test_a_partially_fetched_assembly_is_measured_over_every_row() -> None:
    """A row that never fetched provenance is still an UNSOURCED row of the answer.

    Measured over the FETCHED subset instead, a mixed assembly — one sourced Point
    plus non-Point rows, which carry no ``provenance`` block however the flag is
    set — reported ``1.0`` on a 25%-sourced answer. That is the same silent green,
    produced by the measurement rather than removed by it. The denominator is every
    row; only an assembly where NO row fetched the field is unassessed.
    """
    linked = SearchResult(
        id="p1", content="ACME signed the contract", point_kind="point",
        source_ref="u://doc-1", captured_at="2026-01-01T00:00:00Z",
    ).to_dict()
    unsourced = [
        {"id": f"d{i}", "content": "ACME renewal notes", "point_kind": "document"}
        for i in range(3)
    ]
    verdict = assess_sufficiency(
        [linked, *unsourced], anchors=["ACME"],
        required_anchor_coverage=1.0, required_provenance_coverage=0.9, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.reasons == (REASON_PROVENANCE_THIN,)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),
                                (MEASURE_PROVENANCE_COVERAGE, 0.25))


@pytest.mark.parametrize("text,anchor,match", [
    # The two operands come from different pipelines: the extractor and canonical
    # stores emit NFC, while filesystem/OCR text and caller anchors may be NFD.
    # Without normalisation the verdict depended on the FORM, and cross-form
    # pairs never matched at all — 0.0 for the row that answers.
    ("cafe\u0301 ma\u0301s", "CAFÉ", True),
    (unicodedata.normalize("NFC", "café más"),
     unicodedata.normalize("NFD", "CAFÉ"), True),
    (unicodedata.normalize("NFC", "café más"), "CAFE", False),
    ("ma\u0301s", "MA", False),
])
def test_the_boundary_is_normalisation_form_independent(
        text: str, anchor: str, match: bool) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0 if match else 0.0),)


@pytest.mark.parametrize("text,anchor", [
    # KOREAN. Hangul separates WORDS with spaces but attaches case PARTICLES with
    # none, so a Hangul neighbour read as a word continuation made the canonical
    # form of nearly every Korean sentence unmatchable: `삼성전자는 반도체를 만든다`
    # and `ACME의 계약` both scored 0.0. The particle set is closed, and `어`/`시`/
    # `인` are not in it, so the derived forms below stay out.
    ("삼성전자는 반도체를 만든다", "삼성전자"),
    ("삼성전자가 발표했다", "삼성전자"),
    ("삼성전자를 인수했다", "삼성전자"),
    ("삼성전자의 실적", "삼성전자"),
    ("삼성전자와 계약했다", "삼성전자"),
    ("한국의 수도는 서울이다", "한국"),
    ("서울에는 사람이 많다", "서울"),
    ("한국에서는 인기가 높다", "한국"),
    ("ACME의 계약", "ACME"),          # a LATIN anchor in Korean text
    ("AI를 도입했다", "AI"),
    ("OpenAI의 모델", "OpenAI"),
])
def test_a_korean_particle_is_a_boundary_not_a_continuation(
        text: str, anchor: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


@pytest.mark.parametrize("text,anchor", [
    ("한국어를 배운다", "한국"),   # 어 is not a particle
    ("한국도시 계획", "한국"),     # 도 + 시, and 시 is not
    ("한국인이 많다", "한국"),     # 인 is not
])
def test_a_korean_derived_form_is_still_not_a_mention(text: str, anchor: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


@pytest.mark.parametrize("text,anchor", [
    # COMPATIBILITY WIDTH. Full-width Latin and digits are standard in Japanese and
    # Chinese formal/legal/OCR text, and half-width kana is standard in the same
    # corpora — all four of these scored 0.0 under canonical (NFC) folding only.
    ("当社のＡＩ技術は進化する", "AI"),
    ("当社のAI技術", "ＡＩ"),
    ("２０２６年に署名した", "2026"),
    ("ｱｸﾒｼｽﾃﾑ", "ｱｸﾒ"),
    ("ｼｽﾃﾑACMEｼｽﾃﾑ", "ACME"),   # half-width kana neighbour, not full-width katakana
])
def test_a_compatibility_width_spelling_is_the_same_entity(
        text: str, anchor: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


@pytest.mark.parametrize("text,anchor,expected", [
    # BMP private use is icon-font territory: a Word Symbol-font bullet (U+F0B7)
    # sits BETWEEN tokens, so it separates rather than blocks.
    ("ACME\uf0b7契約を締結", "ACME", 1.0),
    ("ACME\uf0b7contract signed", "ACME", 1.0),
    ("\uf0b7ACME", "ACME", 1.0),
    # Unnamed code points OUTSIDE BMP private use have no script to consult and
    # BLOCK — the fail-closed side.
    ("ai\u0378", "AI", 0.0),
    ("ai\U000F0000", "AI", 0.0),
])
def test_unnamed_code_points_are_split_by_range(
        text: str, anchor: str, expected: float) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, expected),)


@pytest.mark.parametrize("text,anchor", [
    # Symbols whose NFKC decomposition is LETTERS OR DIGITS. NFKC is a
    # COMPATIBILITY folding, so without a pre-map `™` becomes `TM` and glues onto
    # the anchor (`ACME™契約` measured 0.0), and `½` becomes `1⁄2`, which CREDITED
    # a single-digit anchor `1`. A symbol keeps separating.
    ("ACME™契約", "ACME"),
    ("Tortoise™", "Tortoise"),
    ("ACME℠", "ACME"),
    ("№1234", "1234"),
    ("文書№1234", "1234"),
    ("PhaseⅠ", "Phase"),
    ("ACME㎏", "ACME"),
    ("1º andar", "1"),               # ordinal marker after a digit
    ("2ª via", "2"),
])
def test_a_symbol_that_folds_to_letters_still_separates(
        text: str, anchor: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


@pytest.mark.parametrize("text,anchor", [
    # A FRACTION is not the integer its decomposition starts with.
    ("½", "1"),
    ("½", "2"),
    ("¼", "4"),
    ("no ½ done", "1"),
    # A circled LETTER is a decorated letter, not a separator, and a circled DIGIT
    # is not the plain digit either.
    ("aiⒶ", "AI"),
    ("①の条項", "1"),
])
def test_a_compatibility_decomposition_does_not_credit_a_digit(
        text: str, anchor: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


@pytest.mark.parametrize("text,anchor", [
    # Korean BOUND NOUNS and COUNTERS attach exactly like particles, so the
    # natural written form of a date, a place or a quantity scored 0.0.
    ("2026년 3월 매출", "2026"),
    ("２０２６년 3월", "2026"),      # the full-width form NFKC exists for
    ("서울시 정책", "서울"),
    ("5명 참석", "5"),
    ("3개월", "3"),
    ("100원", "100"),
    ("1위", "1"),
    # ...and so do the CONNECTIVE particles, which are not case particles.
    ("AI나 로봇", "AI"),
    ("AI라도", "AI"),
    ("AI이면", "AI"),
    ("AI이므로", "AI"),
    ("AI로서", "AI"),
    ("AI랑", "AI"),
    ("AI마다", "AI"),
    ("AI밖에", "AI"),
    ("AI조차", "AI"),
])
def test_a_korean_bound_noun_or_connective_is_also_a_boundary(
        text: str, anchor: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


@pytest.mark.parametrize("text,anchor", [
    # Real Korean WORDS decompose into two set members, so the stack is accepted
    # only in the grammatical order — CASE particle then AUXILIARY. `도로` is
    # aux+case, `가을` and `의의` are case+case; none is a noun plus particle.
    ("한국도로, 3만km", "한국"),
    ("한국의의", "한국"),
    ("한국가을 축제", "한국"),
    ("한국도의 논쟁", "한국"),
    ("한국만만", "한국"),
    ("한국이의 제기", "한국"),
])
def test_a_two_member_stack_is_only_legal_in_case_then_aux_order(
        text: str, anchor: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


@pytest.mark.parametrize("anchor", ["™", "Ⅳ", "①", "µ", "ª", "½", "№"])
def test_an_anchor_of_nothing_but_premapped_symbols_is_refused(anchor: str) -> None:
    """A symbol-only anchor folds BLANK, and a blank anchor covers everything.

    The pre-map that keeps `™` from gluing onto its anchor also erases an anchor
    that is nothing but symbols. Collapsing whitespace BEFORE the pre-map left
    `_fold("™") == " "` — truthy — so the blank guard passed and `" " in haystack`
    reported FULL coverage for an anchor nobody named on every multi-word row.
    """
    verdict = assess_sufficiency(
        _rows("quarterly hiring fell across every region"),
        anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


@pytest.mark.parametrize("text,anchor", [
    # The circled-numeral series is CONTINUOUS (①–⑳, ㉑–㉟, ㊱–㊿) and the
    # enclosed digits sit at the head of a block whose LETTERS must keep gluing.
    # Splitting the block by range arithmetic dropped 36–50 into the excluded
    # section, so `㊱ACME` and `ACME㊿` fell to 0.0 while `㉑ACME` stayed 1.0.
    ("㊱ACME", "ACME"),
    ("ACME㊿", "ACME"),
    ("㊿ACME", "ACME"),
    ("ACME🄀", "ACME"),
    ("ACME🄊", "ACME"),
    # The RIGHT adjacency is the discriminating direction here: `㋀ACME` scores
    # 1.0 even without the range, because the anchor's left neighbour becomes `月`
    # (CJK, unspaced) — so it cannot prove the range exists.
    ("ACME㋀", "ACME"),          # telegraph January -> 1月
    ("ACME㋋", "ACME"),          # telegraph December -> 12月
])
def test_the_circled_and_enclosed_numeral_series_separates(
        text: str, anchor: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=[anchor], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


@pytest.mark.parametrize("text", [
    # The copula and its connectives attach to a consonant-final token. Narrowing
    # the two-member stack to CASE+AUX silently dropped `이라` from the closed set
    # and took these with it (1.0 -> 0.0): Korean writes `AI이라도`, not `AI라도`,
    # after a consonant. The set is documented as additive, so this pins it.
    "AI이라",
    "AI이라도",
    "AI이라야",
    "AI이라 불리는 기술",
])
def test_the_copula_forms_are_kept(text: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=["AI"], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)


@pytest.mark.parametrize("text", [
    # DECORATED LETTERS glue, in every block. The enclosed-Latin supplement and
    # the circled Hangul/Katakana sub-ranges must NOT be pre-mapped: they are
    # letters, and the `NAME contains LATIN` branch is what catches them.
    "ai🅐", "ai🅰", "ai㉠", "aiⒶ",
    # ...and a decorated underscore behaves like `_`, a word character.
    "ai＿", "ai︳",
])
def test_a_decorated_letter_or_underscore_still_glues(text: str) -> None:
    verdict = assess_sufficiency(
        _rows(text), anchors=["AI"], required_anchor_coverage=1.0, resident=True)
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


class _AttrRow:
    """A row in the ATTRIBUTE shape ``evidence_record`` documents as accepted."""

    def __init__(self, **kw: object) -> None:
        self.__dict__.update(kw)


def test_the_attribute_row_shape_agrees_with_the_wire_shape() -> None:
    """Both documented input shapes must measure provenance the SAME way.

    ``evidence_record`` rebuilds a dict from a fixed key set for an object row,
    which dropped ``provenance``/``captured_at`` — so an object that LITERALLY
    carries a link measured as *unassessed* while the identical facts on the wire
    measured ``1.0``. That is the fail-open the fetched/linked split exists to
    close, surviving on the other shape these functions accept.
    """
    linked = _AttrRow(id="a1", content="ACME signed", provenance={"source": "u://1"})
    no_link = _AttrRow(id="a2", content="ACME signed", captured_at="2026-01-01")

    assert evidence_record(linked).provenance == "u://1"
    assert assess_sufficiency(
        [linked], anchors=["ACME"], required_anchor_coverage=1.0,
        required_provenance_coverage=0.9, resident=True,
    ).measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),
                   (MEASURE_PROVENANCE_COVERAGE, 1.0))

    # Fetched with no link is a measured 0.0, not "unassessed".
    assert evidence_record(no_link).provenance is None
    assert evidence_record(no_link).provenance_fetched is True
    assert assess_sufficiency(
        [no_link], anchors=["ACME"], required_anchor_coverage=1.0,
        required_provenance_coverage=0.9, resident=True,
    ).verdict == INSUFFICIENT


def test_a_phrase_cannot_be_stitched_across_two_entity_terms() -> None:
    """Entity names stay separate: a join manufactures mentions nobody made.

    Concatenating ``subject`` + ``topics`` into one haystack let the tail of one
    entity meet the head of the next, so the anchor ``Corp subject`` measured
    ``1.0`` — coverage for a phrase that appears nowhere.
    """
    row = SearchResult(
        id="p1", content="signed the contract", point_kind="fact",
        subject={"id": "subject:acme", "name": "ACME Corp", "kind": "org"},
        topics=["ACME Corp", "contracts"],
    ).to_dict()
    verdict = assess_sufficiency(
        [row], anchors=["Corp contracts"], required_anchor_coverage=1.0, resident=True)
    assert verdict.verdict == INSUFFICIENT
    assert verdict.measures == ((MEASURE_ANCHOR_COVERAGE, 0.0),)


def test_the_snippet_is_read_last_so_a_truncated_preview_never_wins() -> None:
    row = evidence_record({"id": "p1", "content": "full text here", "snippet": "full"})
    assert row.text == "full text here"
    assert evidence_record({"id": "p1", "snippet": "preview"}).text == "preview"


def test_anchors_cannot_be_a_bare_string() -> None:
    with pytest.raises(TypeError):
        assess_sufficiency(_rows("x"), anchors="acme", resident=True)


@pytest.mark.parametrize("row", [
    "acme corp signed",
    b"acme corp signed",
    bytearray(b"acme corp signed"),
    {"id": "p1", "content": "acme corp signed"},
])
def test_a_single_row_cannot_be_passed_where_a_sequence_of_rows_goes(
    row: object,
) -> None:
    """ONE row is not an iterable OF rows, and iterating it invents evidence.

    Both shapes are ``Iterable``, so neither was refused: a bare ``str`` iterated
    its CHARACTERS (nine one-character rows) and a bare ``Mapping`` iterated its
    KEYS (the rows ``"id"``, ``"content"``) — synthetic rows carrying text no
    producer assembled. The anchor ``content`` then word-boundary-matched the row
    whose text IS ``"id"``/``"content"`` and the measure reported ``1.0``: a
    confident verdict about evidence that does not exist, in the direction that
    serves rather than escalates. ``evidence_record`` legitimately accepts a bare
    string as one row, so the ambiguity is real rather than obviously wrong — the
    caller wraps the row (``[row]``).
    """
    with pytest.raises(TypeError, match="sequence of rows"):
        assess_sufficiency(
            row, anchors=["acme corp"], required_anchor_coverage=1.0,  # type: ignore[arg-type]
            resident=True,
        )


def test_the_refusal_is_about_the_container_not_the_row() -> None:
    """The two row shapes refused as a container are legitimate INSIDE one.

    ``evidence_record`` accepts a bare string and a ``Mapping`` as ONE row, so the
    guard must not turn that support into a refusal — wrapping the row is the
    supported call, and it measures the row's real content.
    """
    for row in ("acme corp signed", {"id": "p1", "content": "acme corp signed"}):
        assert assess_sufficiency(
            [row], anchors=["acme corp"], required_anchor_coverage=1.0, resident=True,
        ).measures == ((MEASURE_ANCHOR_COVERAGE, 1.0),)
