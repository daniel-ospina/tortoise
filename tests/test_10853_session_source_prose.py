"""#10853 — the session `:Source` carries no turn prose, on the RECOVERY door
(except a space-less (CJK) run, which arrives whole — the residual the
`_session_source_metadata` docstring names).

A PRE-#10000 capture wrote the transcript's first substantive utterance as the
session `Source`'s `summary` and derived `_searchText` from that same value
(#1352, `_source_search_text(summary=…)` — the capture path has no title).
#10000's ruling (§9.1/D1) is that captured turn text does not live on a graph
node.

⭐ #10000 REPLACED THAT DERIVATION, and the door's rule has to follow it
(#10965). The capture now writes a topic LEXICON: `summary` is a display
descriptor over it and `_searchText` is `", ".join(topics)`, both from the same
`topics` the record keeps. So the drop is decided per ROW, from the row's own
values — a lexicon `_searchText` is projected (it is the only field the Source
FTS index is declared on, so dropping it un-indexes a restored session — #3518),
a prose one is still declined. See
`cache_projection._episodic_source_search_text_is_the_lexicon`.

⛔ THE DOOR THIS FILE PINS IS NOT THE CAPTURE. Every record-sourced read and
rebuild goes through `cache_projection.node_from_row`: `tortoise record-restore`
and `rebuild_from_record` MATERIALISE a node with it, and
`record_serve.read_node` answers with the same map because its contract
is that a served node and a rebuilt node cannot drift. So a row written by a
build that still placed the key either re-creates the prose on the graph or makes
the served answer disagree with the rebuild — and nothing reds either way.

The prose halves are pinned as a PAIR, because a pre-#10000 capture derived them
from one datum (`_source_search_text`), so dropping only `summary` would leave the
identical sentence on the node under the column next door — the #10620-review
naming, `_CONTENT_COLUMNS` calls them *"the SAME datum"*.

⛔ **THE HALF OF THIS FILE THAT MATTERS MOST IS THE NEGATIVE LEGS.** The
predicate has two identity arms (a `session:` url; a pre-#4005 bare-filename
`agentSession`), and THREE plausible-looking terms that are NOT signals because
each one fires on real DOCUMENTS:

* `session_id` — `EventAPI.add_document` takes one and `tortoise/ingest.py`
  passes a transcript's `sessionId` frontmatter into it;
* `source_kind='agentSession'` alone — `file_indexer` classifies a transcript
  FILE from its frontmatter and writes that kind on a per-FILE `corpus://`
  permalink (`CLASSIFIER_TO_SOURCE_KIND`, pinned §6.2/I26);
* `is_episodic=True` — the capture commit stamps it on every EXTERNAL source of
  `payload.sources` (`hosted_api`, step 4), and `backfill_is_episodic.py` stamps
  it on every Source reachable from a session point.

Each of the three was caught by a review round before this landed, and each has
a leg below that reds if it is used as a signal. A guard keyed on any of them
silently stripped a document's own metadata — the mirror of the defect this
change closes.
"""

from __future__ import annotations

import ast

import pytest

from tortoise import record_projection as rp
from tortoise.cache_projection import node_from_row
from tortoise.projection import _FULLTEXT_INDEX_LABEL_FIELDS
from tortoise.sdk import (
    _SESSION_SOURCE_SUMMARY_PREFIX,
    SOURCE_SEARCH_TEXT_FIELD,
    _session_source_metadata,
    _source_search_text,
)

#: The transcript slice a pre-fix capture left on the row.
UTTERANCE = "We decided the weekly rebuild happens on Fridays."


def _source_row(**overrides: object) -> dict[str, object]:
    """A `public.source` row of the SHAPE Postgres returns (every column).

    ⚠️ But NOT in the ORDER it returns them: `sorted()` puts `'props'` BEFORE
    `'source_kind'`/`'title'`/`'url'`, while the DDL puts the tail LAST — see
    :func:`_row_in_select_star_order`, the ordering a real row has.
    """
    row: dict[str, object] = {c: None for c in sorted(rp.table_columns()["source"])}
    row.update(overrides)
    return row


def _row_in_select_star_order(**overrides: object) -> dict[str, object]:
    """A `public.source` row in the order `SELECT *` ACTUALLY yields: tail last.

    `props` is declared last in `CREATE TABLE public.source` (the `props jsonb
    NOT NULL DEFAULT '{}'` line, immediately above the PRIMARY KEY), and Postgres
    numbers ordinary columns in creation order (`pg_attribute.attnum`, *"ordinary
    columns are numbered from 1 up"*), so `SELECT *` iterates the tail LAST —
    the coordinate `_props_from_row` used to write last, i.e. the one that won
    every collision it carried.

    ⛔ This ordering is the whole point of the legs that use it: `_source_row`'s
    `sorted()` keys put `props` BEFORE the identity columns it can collide with
    (`source_kind`, `title`, `url`), so a column overwrote the tail and the row
    predicate and the node map AGREED on the one ordering real rows do not have
    (#10867 review ISSUE 1).
    """
    row: dict[str, object] = {
        c: None for c in sorted(rp.table_columns()["source"]) if c != "props"}
    # Inserted LAST — and `update` never moves an existing key, so `props` stays
    # the final coordinate however many overrides follow.
    row["props"] = ""
    row.update(overrides)
    return row


def _captured_session_row(**overrides: object) -> dict[str, object]:
    """The row a PRE-#10000 capture wrote: a session Source whose columns hold prose."""
    row = _source_row(
        url="session:s-10853",
        id="s-10853",
        name="s-10853",
        title="session:s-10853",
        canonical_url="session:s-10853",
        source_kind="agentSession",
        session_id="s-10853",
        topics=["fridays", "rebuild"],
        summary=UTTERANCE,
        _search_text=UTTERANCE,
    )
    row.update(overrides)
    return row


def _document_row(**overrides: object) -> dict[str, object]:
    """A DOCUMENT Source whose own `summary`/`_searchText` must survive."""
    row = _source_row(
        url="https://example.com/q3-pricing",
        id="src-1",
        source_kind="document",
        title="Q3 pricing memo",
        summary="A memo about the Q3 pricing change.",
        _search_text="Q3 pricing memo A memo about the Q3 pricing change.",
    )
    row.update(overrides)
    return row


def _every_column_populated_session_row(**overrides: object) -> dict[str, object]:
    """A `public.source` row with **every** column Non-NULL, as the schema allows.

    The drop set is a *set* declaration (`_EPISODIC_SOURCE_PROSE_COLUMNS`), so
    pinning it needs a row in which every projected column is present — a row
    that leaves a column NULL cannot tell "absent because dropped" from "absent
    because NULL" (`projected_columns` are written only when non-None). Values
    are placeholders; only their NON-NULL-ness is load-bearing.
    """
    row: dict[str, object] = {c: f"v-{c}" for c in sorted(rp.table_columns()["source"])}
    for column in ("topics", "url_aliases", "about_entities"):
        row[column] = [f"v-{column}"]
    row.update(
        embedding=[0.125, 0.25],
        is_episodic=True,
        needs_extraction=True,
        credibility_tier=2,
        version=3,
        graph_id="g-10853",
        id="s-10853",
        node_key="s-10853",
        node_label="Source",
        name="s-10853",
        url="session:s-10853",
        source_kind="agentSession",
        # An EMPTY tail: the drop set is what this leg pins, and a non-empty tail
        # would add a property that is not in `projected_properties` at all.
        props="",
    )
    row.update(overrides)
    return row


def _assert_kept(node, why: str) -> None:
    assert node.props.get("summary") == "A memo about the Q3 pricing change.", (
        f"{why} — the projection dropped a DOCUMENT's own summary, which is the "
        "mirror of the defect this change closes"
    )
    assert node.props.get("_searchText") == (
        "Q3 pricing memo A memo about the Q3 pricing change."), (
        f"{why} — the projection dropped a document's title-derived `_searchText`, "
        "which silently un-indexes it in the cache"
    )


def test_a_legacy_session_source_row_projects_no_turn_prose():
    """RED-FIRST: the recovery door re-created the sentence on the node."""
    node = node_from_row("Source", _captured_session_row())

    assert "summary" not in node.props, (
        "the session `:Source` carries the transcript's first utterance as "
        "`summary` — the record->cache door re-materialised §9.1/D1 turn prose "
        f"on a graph node: {node.props.get('summary')!r}"
    )
    assert "_searchText" not in node.props, (
        "the same utterance survives under `_searchText`, which the capture "
        "derives FROM the summary — dropping one half leaves the prose on the "
        f"node: {node.props.get('_searchText')!r}"
    )


def test_the_drop_is_narrow_the_session_metadata_still_projects():
    """The guard must not be satisfied by projecting nothing at all."""
    node = node_from_row("Source", _captured_session_row())

    assert node.props["url"] == "session:s-10853"
    assert node.props["sourceKind"] == "agentSession"
    assert node.props["sessionId"] == "s-10853"
    assert node.props["topics"] == ["fridays", "rebuild"], (
        "`topics` is the capture's word LEXICON (its most frequent content "
        "words) — not a CHOSEN transcript slice (a space-less CJK run arrives "
        "whole, so it can be one; see `_session_source_metadata`'s residual)"
    )


def test_the_DROP_SET_itself_is_exactly_the_declared_pair():
    """The narrowing is a SET, and a set nobody pins grows silently (#10867).

    ⛔ THE UNGUARDED EXTENSION POINT THIS LEG CLOSES. `_EPISODIC_SOURCE_PROSE_COLUMNS`
    is a frozenset, and `test_the_drop_is_narrow_…` above pins only four
    SURVIVORS (`url`/`sourceKind`/`sessionId`/`topics`) — so WIDENING the set was
    invisible: measured by review at this head, adding `title` (a placed,
    record-held `public.source` column) left all 21 legs green *and* the whole
    record/projection neighbourhood green (443 passed), while the recovery door
    silently stripped a record-held property — the exact silent-loss class this
    change exists to close, on a table the very next lane (#10852) edits.

    So this leg pins the DIFFERENCE between the record's projected property set
    and the node's property map, over a row in which every projected column is
    non-NULL: it must be EXACTLY the declared pair. Both directions are covered:
    widening the set adds a missing name (red), and emptying it makes the pair
    project again (red).
    """
    from tortoise import cache_projection as cp

    node = node_from_row("Source", _every_column_populated_session_row())

    dropped = set(cp.projected_properties("source")) - set(node.props)
    assert dropped == {"summary", "_searchText"}, (
        f"the drop set is {sorted(dropped)} — `_EPISODIC_SOURCE_PROSE_COLUMNS` "
        "must strip the turn-prose PAIR and nothing else; a wider set "
        "silently strips a record-held property off a session Source"
    )


def test_the_drop_set_leg_would_catch_a_widened_set(monkeypatch):
    """Non-vacuity: WIDENING the declared set must red the leg above.

    The mutation is applied the way a future lane would apply it — a wider
    frozenset — rather than asserted, because "the leg pins the set" is exactly
    the claim that is false when the leg compares against the set itself.
    """
    from tortoise import cache_projection as cp

    monkeypatch.setattr(
        cp, "_EPISODIC_SOURCE_PROSE_COLUMNS",
        cp._EPISODIC_SOURCE_PROSE_COLUMNS | {"title"})
    monkeypatch.setattr(
        cp, "episodic_source_prose_properties",
        lambda: frozenset(cp.canonical_property("source", c)
                          for c in cp._EPISODIC_SOURCE_PROSE_COLUMNS))

    node = node_from_row("Source", _every_column_populated_session_row())
    dropped = set(cp.projected_properties("source")) - set(node.props)
    assert dropped != {"summary", "_searchText"}, (
        "a widened `_EPISODIC_SOURCE_PROSE_COLUMNS` did not change the drop set "
        "— the pin above would stay green while the guard strips `title`"
    )
    assert "title" in dropped


def test_the_legacy_bare_filename_agent_session_is_episodic():
    """The pre-#4005 identity: `os.path.basename(ref.path)`, literally `session.md`.

    `session_source_migration._LEGACY_CANDIDATES` is this arm's definition, and
    part of that population survives indefinitely (the migration leaves a node it
    cannot attribute). It carries NO `session:` prefix — the kind plus a
    separator-free url IS the identity.
    """
    row = _source_row(
        url="session.md",
        id="session.md",
        source_kind="agentSession",
        summary=UTTERANCE,
        _search_text=UTTERANCE,
    )
    node = node_from_row("Source", row)

    assert "summary" not in node.props
    assert "_searchText" not in node.props


# ── NEGATIVE LEGS: the three plausible flags that are NOT signals ───────────


def test_a_plain_document_keeps_its_summary_and_search_text():
    """The baseline negative leg: `summary` on a DOCUMENT is its own metadata."""
    _assert_kept(node_from_row("Source", _document_row()), "a plain document")


def test_a_transcript_document_keeps_its_summary_even_though_it_has_a_session_id():
    """`session_id` is a DOCUMENT coordinate (review P1 on this PR)."""
    row = _document_row(
        url="corpus://transcripts/s-10853.md",
        id="corpus://transcripts/s-10853.md",
        document_kind="transcript",
        session_id="s-10853",
        title="Weekly rebuild transcript",
    )
    _assert_kept(node_from_row("Source", row),
                 "a transcript document carrying a session_id")


def test_a_corpus_permalink_is_not_episodic_even_when_its_kind_says_agent_session():
    """`source_kind='agentSession'` ALONE is a DOCUMENT shape (review P1, round 2).

    `file_indexer` classifies a transcript FILE from its frontmatter (`sessionId`
    present → `agent_session` → `CLASSIFIER_TO_SOURCE_KIND` → `'agentSession'`)
    and writes that kind on a per-FILE `corpus://` permalink — so a transcript
    document IS an `agentSession` Source. Only a SEPARATOR-FREE url makes the
    kind an identity.
    """
    row = _document_row(
        url="corpus://transcripts/weekly-rebuild.md",
        id="corpus://transcripts/weekly-rebuild.md",
        source_kind="agentSession",
        document_kind="transcript",
    )
    _assert_kept(node_from_row("Source", row),
                 "a corpus:// permalink stamped agentSession by the indexer")


def test_an_external_source_is_not_episodic_even_when_is_episodic_is_true():
    """`is_episodic=True` is stamped on every EXTERNAL source (review P1, round 2).

    The capture commit writes it on every source of `payload.sources`
    (`hosted_api`, step 4) and `backfill_is_episodic.py` on every Source
    reachable from a session point — so the flag alone is not an identity.
    """
    row = _document_row(
        url="corpus://transcripts/q3-pricing.md",
        id="corpus://transcripts/q3-pricing.md",
        document_kind="transcript",
        is_episodic=True,
    )
    _assert_kept(node_from_row("Source", row),
                 "a document the capture commit stamped is_episodic")


@pytest.mark.parametrize("signal", ["session_url", "legacy_bare_filename"])
def test_each_episodic_arm_catches_the_prose_on_its_own(signal):
    """One leg per arm: either identity makes the row episodic by itself.

    A single positive row carrying both would leave each arm untested — delete
    one and the suite stays green.
    """
    overrides: dict[str, object] = (
        {"url": "session:s-10853"}
        if signal == "session_url"
        else {"url": "session.md", "source_kind": "agentSession"}
    )
    row = _source_row(id="s-10853", summary=UTTERANCE, _search_text=UTTERANCE,
                      **overrides)
    node = node_from_row("Source", row)

    assert "summary" not in node.props, (
        f"the {signal!r} arm alone did not make the row episodic: the turn prose "
        f"reached the node ({node.props.get('summary')!r})")
    assert "_searchText" not in node.props


def test_the_record_row_is_not_mutated_by_the_projection():
    """The record stays authoritative — the projection drops, it does not delete."""
    row = _captured_session_row()
    before = dict(row)

    node_from_row("Source", row)

    assert row == before, (
        "the projection MUTATED the record row: the record is the authority for "
        "the value, and only the cache may decline to project it"
    )
    assert row["summary"] == UTTERANCE


# ── THE THIRD COST'S REPAIR: the reconciler must not call this a divergence ──


class _FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    """The minimum `scan_cache_view` needs: one `MATCH` returning properties."""

    def __init__(self, rows):
        self._rows = rows

    def query(self, cypher, timeout=None):  # signature parity with the driver
        return _FakeResult(self._rows)


def test_the_reconciler_counts_the_declined_pair_as_cache_owned_not_divergence():
    """Review cycle 3, P1: the third cost, repaired instead of merely declared.

    A PRE-#10000 row still holds the prose pair, so the reconciler's record side
    (built through `node_from_row`) omits it while a cache written by that build
    holds it. Left as a `field_mismatch` that is permanent non-clean on healthy
    data: the class the reconciler's own docstring grades P1 ("a signal
    indistinguishable from noise is not observability"). So `scan_cache_view`
    marks the pair CACHE-OWNED for an episodic Source, and the run reports it
    under `graph_only` — visible, and not a divergence against a correct record.
    """
    from tortoise import record_reconcile as rr

    key = "Source:session:s-10853"
    cache_props = {
        "url": "session:s-10853", "sourceKind": "agentSession", "sessionId": "s-10853",
        "summary": UTTERANCE, "_searchText": UTTERANCE,
    }
    scan = rr.scan_cache_view(_FakeGraph([(cache_props,)]), labels=["Source"])

    assert scan.placed[key] >= frozenset({"url", "sourceKind"})
    assert not (scan.placed[key] & frozenset({"summary", "_searchText"})), (
        "the reconciler still thinks the record PLACES the prose for an episodic "
        "Source, so it will count it as a field mismatch forever"
    )

    result = rr.reconcile_record_and_cache(
        # the RECORD side: what `node_from_row` yields — no prose.
        {key: {"url": "session:s-10853", "sourceKind": "agentSession",
               "sessionId": "s-10853"}},
        scan.view, placed=scan.placed)

    assert result.report.field_mismatches == 0, (
        f"the reconciler counted the projection's own narrowing as divergence: "
        f"{result.report.as_dict() if hasattr(result.report, 'as_dict') else result.report}")
    assert result.divergent == 0
    assert result.verdict() == rr.VERDICT_CLEAN
    assert result.graph_only_total == 2, (
        "the pair must still be VISIBLE, as its own named count — a comparison "
        f"that declines to judge a field has to say so: {result.graph_only!r}"
    )


def test_a_document_source_with_the_pair_is_still_compared_field_by_field():
    """The exclusion is scoped to an EPISODIC Source — not to the column pair.

    A document that the cache holds with a DIFFERENT summary than the record is a
    real divergence and must still be counted: an exclusion keyed on the property
    name alone would hide it.
    """
    from tortoise import record_reconcile as rr

    key = "Source:https://example.com/q3-pricing"
    cache_props = {
        "url": "https://example.com/q3-pricing", "sourceKind": "document",
        "summary": "a DIFFERENT summary from the record's",
    }
    scan = rr.scan_cache_view(_FakeGraph([(cache_props,)]), labels=["Source"])
    assert {"summary"} <= set(scan.placed[key])

    result = rr.reconcile_record_and_cache(
        {key: {"url": "https://example.com/q3-pricing", "sourceKind": "document",
               "summary": "A memo about the Q3 pricing change."}},
        scan.view, placed=scan.placed)

    assert result.report.field_mismatches == 1
    assert result.divergent == 1


def test_the_props_tail_cannot_smuggle_the_prose_past_the_guard():
    """The OPEN-SET door: `props` is written verbatim, so it must be filtered too.

    Not reachable through today's writer (`record_projection` classifies both
    names as COLUMNS, so they never reach the tail) — and filtered for that
    reason, not despite it: a classification that moves is the #9710 class, and a
    guard that stops guarding without reddening is worse than no guard. Review
    P2 on this PR measured the hole before it was closed.
    """
    # ⚠️ BOTH spellings, because `props` is an OPEN set keyed by whatever the
    # payload said: `_search_text` is a real COLUMN, so the tail can carry the
    # snake spelling where it cannot carry the canonical one (review cycle 5, P3
    # — the first cut of this filter missed it).
    row = _captured_session_row(
        props='{"summary": "TAIL PROSE", "_searchText": "TAIL INDEX", '
              '"_search_text": "SNAKE TAIL PROSE"}')
    node = node_from_row("Source", row)

    assert "summary" not in node.props
    assert "_searchText" not in node.props
    assert "_search_text" not in node.props


def test_a_tail_carried_index_name_cannot_ERASE_the_lexicon_the_column_writes():
    """The mirror of the leg above, and the one that distinguishes the ORDERINGS.

    On a post-#10000 row the COLUMN door deliberately KEEPS `_searchText` (it is
    the only field the Source FTS index is declared on, #3518). So when the tail
    also names that coordinate, the tail's refused value must not take the
    column's legitimate one with it — otherwise a caller-controlled `props`
    payload can UN-INDEX a restored session, which is the exact harm the
    narrowing exists to prevent, reachable from the open set.

    Popping the tail's declined names AFTER the column loop did precisely that:
    the tail's key had overwritten `out` first, the column then wrote the
    lexicon over it, and the pop removed the coordinate entirely. Measured at
    the parent, this leg's first case returned `_searchText is None`; it now
    returns the join. ⛔ The leg is here rather than beside the provenance legs
    because it is an ORDERING property, not a provenance one: the same row with
    no tail, with a canonical tail key and with a snake tail key must produce the
    SAME node map.
    """
    # ⭐ The lexicon is the helper's OWN derivation, never a hand-typed list — the
    # row is built from the capture's functions so this leg tracks a change to
    # the derivation instead of staling against it.
    base = _post_10000_session_row()
    topics = base["topics"]
    assert isinstance(topics, list) and topics
    joined = ", ".join(topics)
    expected = node_from_row("Source", base)
    assert expected.props.get(SOURCE_SEARCH_TEXT_FIELD) == joined

    for smuggled in ('{"_searchText": "SMUGGLED"}',
                     '{"_search_text": "SMUGGLED"}',
                     '{"summary": "SMUGGLED"}',
                     '{"_searchText": "SMUGGLED", "_search_text": "SMUGGLED",'
                     ' "summary": "SMUGGLED"}'):
        node = node_from_row("Source",
                             _post_10000_session_row(props=smuggled))
        assert node.props.get(SOURCE_SEARCH_TEXT_FIELD) == joined, (
            f"a caller-controlled `props` payload {smuggled} erased the lexicon "
            "the COLUMN door legitimately projects — the restored session is now "
            f"un-indexed: {node.props!r}")
        assert "summary" not in node.props, (
            f"{smuggled} put a summary on the node — `summary` is declined on "
            "EVERY row, tail door included")
        assert "SMUGGLED" not in str(node.props), (
            f"{smuggled} reached the node under some spelling: {node.props!r}")


# ── THE IDENTITY CAN MOVE TOO, and the guard must follow it there ──────────


def test_the_identity_is_read_through_the_props_tail():
    """A tail-carried `sourceKind` makes an EPISODIC node, so the guard must fire.

    `props` is written to the node VERBATIM, so this row produces a node whose
    `sourceKind` says `agentSession` while its `source_kind` COLUMN is NULL. A
    predicate reading the columns alone answered "document", the column door
    never fired, and the prose landed on the node — while
    `is_episodic_source_node` (reading the merged map) called that same node a
    session. R1 ("from any row shape") failed open, and nothing reddened — not
    because the reconciler hid it, but because it CANNOT see it: it re-projects
    the record through this same projector, so both sides carry the prose and
    compare equal (`record_reconcile.py:449-451`, `:540`) (#10867 review).
    """
    row = _source_row(
        url="session.md",
        id="s-10853",
        name="s-10853",
        source_kind=None,
        summary=UTTERANCE,
        _search_text=UTTERANCE,
        props='{"sourceKind": "agentSession", "summary": "TAIL PROSE"}',
    )
    before = dict(row)
    node = node_from_row("Source", row)

    assert "summary" not in node.props, (
        "a tail-carried `sourceKind` classified the node as a session but the "
        f"prose reached it anyway: {node.props.get('summary')!r}"
    )
    assert "_searchText" not in node.props
    assert node.props["sourceKind"] == "agentSession", (
        "the identity the tail carries must still project — this leg must not be "
        "satisfiable by projecting nothing"
    )
    assert row == before, "the projection MUTATED the record row"


def test_a_tail_carried_kind_does_not_strip_a_DOCUMENT():
    """The separator arm still applies through the tail — the false positive is worse.

    `file_indexer` stamps `agentSession` on per-FILE `corpus://` permalinks, so a
    tail-carried kind alone must never be a signal. This is the leg that reds if
    the tail read is implemented as "the kind is present" instead of "the kind
    is present AND the url names a session".
    """
    row = _document_row(
        source_kind=None,
        props='{"sourceKind": "agentSession", "url": "corpus://t/weekly.md"}',
    )
    _assert_kept(node_from_row("Source", row),
                 "a `corpus://` permalink in the tail was read as episodic")


def test_the_row_and_node_predicates_agree_when_the_tail_carries_the_identity():
    """The shape that made them disagree: the predicate must judge the node's map.

    The reconciler reads the NODE (`is_episodic_source_node`, merged properties)
    while the projector read the ROW. Any row whose identity lives in the tail
    put those two functions on opposite answers — a permanently suppressed
    divergence on one side or a stripped document on the other.

    ⚠️ ONLY THE KIND CAN TRAVEL — for a row that can BECOME A NODE. The bound is
    REACHABILITY, not the predicate: `url` is the label's CONTRACT KEY, `node_from_row`
    refuses a row whose `url` column is NULL (`UnprojectableRecord` — the schema's own
    CHECK `url IS NOT NULL AND node_key = url` would refuse it too), so a tail-carried
    `url` can never reach a projected node. `_row_identity_view` DOES fill a NULL column
    from the tail, though (`if merged.get("url") is None`), so on the PREDICATE alone a
    row with `url=None` and `props={"url": "session:abc"}` is episodic — the tail decides
    the arm. That shape is pinned separately, below; the COLUMN still wins wherever the
    two carry the same coordinate, which is what the third case here pins.
    """
    from tortoise import cache_projection as cp

    # (column url, props tail, expected). The COLUMN url is the contract key and
    # always wins the coordinate, so the corpus/https rows stay documents even
    # when the tail claims the session kind — that is the false positive the
    # separator arm exists to prevent.
    for column_url, props_tail, expected in (
        ("session.md", '{"sourceKind": "agentSession"}', True),
        ("session.md", '{"sourceKind": "agentSession", "url": "session:s-1"}', True),
        ("https://example.com/q3", '{"sourceKind": "agentSession"}', False),
        ("https://example.com/q3", '{"sourceKind": "document"}', False),
        ("session:s-10853", "{}", True),
    ):
        row = _source_row(url=column_url, source_kind=None, props=props_tail)
        node = node_from_row("Source", row)
        assert cp.is_episodic_source_node(node.props) is expected, (column_url, props_tail)
        assert cp._is_episodic_source_row(row) is expected, (
            f"the row predicate disagreed with the node predicate on "
            f"{column_url!r} + {props_tail}"
        )


def test_only_a_NULL_column_is_silence_an_empty_url_is_a_VALUE():
    """The collision rule is the same in BOTH readings, down to the falsy edge.

    `_props_from_row` writes a column whenever it is not `None` (`if value is not
    None`), so an EMPTY-STRING `url` still lands on the node and shadows a
    tail-carried `url`; `_row_identity_view` filled from the tail on any FALSY
    column (`not merged.get("url")`), which made the two disagree on exactly
    that shape — the same class of drift as the `sourceKind` collision, one edge
    over, and the reason the rule is now stated once and applied twice.

    Unreachable as a node identity — the key column is refused when blank
    (`UnprojectableRecord`, and the schema's own CHECK) — and pinned anyway:
    an unused difference between the two readings is how the reachable one came
    back.
    """
    from tortoise import cache_projection as cp

    row = _source_row(url="", source_kind=None, props='{"url": "session:s-1"}')
    view = cp._row_identity_view(row, cp._decoded_tail(row["props"]))

    assert view["url"] == "", (
        "the identity view filled an EMPTY column from the tail, which the node "
        "map does not do — the two readings of the same row disagreed"
    )


def test_a_tail_only_url_DOES_decide_the_arm_but_never_reaches_a_node():
    """The TRUE bound is reachability, not the predicate (#10867 review, P3).

    The epic doc and this file's neighbour above used to say *"a `url` in the tail
    therefore never decides the arm"*. **False as a statement about
    `_is_episodic_source_row`**: `_row_identity_view` fills a NULL `url` column from
    the tail, so this row is episodic and the tail alone decided it.

    What actually bounds it is that no such row can be PROJECTED: `url` is
    `Source`'s contract key, the schema refuses a NULL one
    (`20261012000001_record_event_stream.sql:197`,
    `CHECK (url IS NOT NULL AND node_key = url)`), and `node_from_row` refuses the
    row (`UnprojectableRecord`) before `_props_from_row` runs. So the tail can
    decide the arm and can never reach a node — unreachable, not absent.

    Pinned because the false wording is the reasoning the column-wins rule rests
    on: a lane that believed "the tail can never decide the arm" would skip this
    shape, which is the one that would break it if the contract key ever relaxed.
    """
    from tortoise import cache_projection as cp

    row = _source_row(url=None, source_kind=None, props='{"url": "session:abc"}')
    assert cp._is_episodic_source_row(row) is True, (
        "the identity view is supposed to fill a NULL url from the tail — if this "
        "answer changed, the doc bullet about what the tail can decide is stale again"
    )
    with pytest.raises(cp.UnprojectableRecord):
        node_from_row("Source", row)


def test_a_column_value_wins_the_tail_on_a_real_SELECT_star_row():
    """ISSUE 1 (#10867 review): on the real row ordering the TAIL won the identity.

    A row whose `source_kind` COLUMN says `document` and whose `props` tail says
    `agentSession` is the one shape where the two readings of the same
    coordinate disagree. `_row_identity_view` declares the rule — *"a COLUMN
    VALUE WINS over the tail's spelling of the same coordinate"* — but
    `_props_from_row` never applied it: it wrote the tail with `out.update(...)`
    wherever the `props` column happened to sit, so the winner was the row's
    MAPPING ORDER. `props` is declared LAST in the record DDL, so on a real row
    the TAIL won while the row predicate read the COLUMN: the node carried
    `sourceKind='agentSession'` — `is_episodic_source_node` calls that a session
    — WITH the turn prose on it. R1 ("from any row shape") failed open, and
    NOTHING reported it: the record side is built through this same projector, so
    it carried the prose too, and `keep` includes ``record_props``
    (`record_reconcile.py:540`) — so the pair compared EQUAL, landing in neither
    `field_mismatches` nor `graph_only`.

    The three existing agreement legs could not see it: they build `_source_row`,
    whose `sorted()` keys put `props` BEFORE the identity columns it can collide
    with, i.e. the one ordering in which the
    column wins the node map too.
    """
    from tortoise import cache_projection as cp

    row = _row_in_select_star_order(
        url="session.md",
        id="session.md",
        name="session.md",
        source_kind="document",
        summary=UTTERANCE,
        _search_text=UTTERANCE,
        props='{"sourceKind": "agentSession"}',
    )
    node = node_from_row("Source", row)

    assert node.props["sourceKind"] == "document", (
        "the `props` tail overwrote the record's own `source_kind` COLUMN — a "
        "column-backed property's authority is the COLUMN (the writer routes it "
        "there, `record_projection._split_properties`), and `_row_identity_view` "
        "already declares that it wins this coordinate"
    )
    assert cp.is_episodic_source_node(node.props) is False, (
        "the node is classified a DOCUMENT by the column, so nothing may strip "
        "its own metadata"
    )
    assert cp._is_episodic_source_row(row) is False
    assert (cp.is_episodic_source_node(node.props)
            == cp._is_episodic_source_row(row)), (
        "the row predicate and the node predicate answered differently about "
        "the SAME row — that disagreement is what let the prose through, and "
        "nothing downstream caught it, because both sides carried the prose and "
        "compared equal"
    )
    # The AMBIGUOUS row is read as a DOCUMENT (the declared asymmetry), so it
    # keeps its prose. Pinned so a future change cannot "fix" the collision the
    # other way and start stripping a document's own metadata.
    assert node.props.get("summary") == UTTERANCE
    assert node.props.get("_searchText") == UTTERANCE


def test_the_projection_does_not_depend_on_the_row_mapping_order():
    """The same record must project the SAME map whichever way the row arrives.

    Pre-fix a column/tail collision was decided by the row's mapping order, so
    `SELECT *` (tail last) and a `sorted()` fixture (tail early) built different
    nodes out of identical data. Pinned on a NON-identity column too (`title`),
    so a fix narrowed to `sourceKind` cannot pass: the rule is about the whole
    map, not about the one coordinate a review happened to name.
    """
    from tortoise import cache_projection as cp

    overrides: dict[str, object] = {
        "url": "session.md",
        "id": "session.md",
        "name": "session.md",
        "source_kind": "document",
        "title": "the COLUMN's title",
        "summary": UTTERANCE,
        "_search_text": UTTERANCE,
        "props": '{"sourceKind": "agentSession", "title": "the TAIL title"}',
    }
    fixture_order = node_from_row("Source", _source_row(**overrides))
    select_star = node_from_row("Source", _row_in_select_star_order(**overrides))

    assert select_star.props == fixture_order.props, (
        "the projection's result depends on the row's MAPPING ORDER: the same "
        "record yields two different node maps"
    )
    assert select_star.props["title"] == "the COLUMN's title"
    assert select_star.props["sourceKind"] == "document"
    assert (cp.is_episodic_source_node(select_star.props)
            == cp._is_episodic_source_row(_row_in_select_star_order(**overrides))
            is False)


@pytest.mark.parametrize("shape", [
    {"url": "session:s-1"},
    {"url": "session.md", "source_kind": "agentSession"},
    {"url": "corpus://t/weekly.md", "source_kind": "agentSession"},
    {"url": "https://example.com/x", "source_kind": "document"},
    {"url": "https://example.com/x", "source_kind": "document", "is_episodic": True},
    {"url": "https://example.com/x", "session_id": "s-1"},
])
def test_the_row_and_node_predicates_agree_on_every_shape(shape):
    """The reader must judge the row the projector wrote.

    They are two functions over two vocabularies (column names vs graph property
    names). If they disagree, the reconciler excludes the pair on a node the
    projector did NOT decline (a permanently suppressed divergence) or fails to
    exclude it on one it did. Pinned shape by shape rather than by construction,
    because the construction is exactly what drifts.
    """
    from tortoise import cache_projection as cp

    row = _source_row(**shape)
    as_props = {}
    for column, value in row.items():
        if value is None:
            continue
        as_props[cp.canonical_property("source", column)] = value

    assert cp.is_episodic_source_node(as_props) == cp._is_episodic_source_row(row)


def test_the_reconciler_exclusion_is_scoped_to_the_Source_label():
    """The label gate is load-bearing: `Event` projects a `summary` too.

    Deleting the gate leaves every other leg green (no `Event` row carries a
    `url`), and the exclusion would then strip `summary` from any Event node that
    happened to look episodic — the same false red one label over. This is the
    leg that makes the gate non-vacuous. (Review cycle 5, P3.)
    """
    from tortoise import record_reconcile as rr

    rows = [({"eventId": "e-10853", "summary": "an event summary",
             "url": "session:s-10853"},)]
    scan = rr.scan_cache_view(_FakeGraph(rows), labels=["Event"])
    (key,) = scan.view

    assert key.startswith("Event:")
    assert "summary" in scan.placed[key], (
        "the exclusion fired on a non-Source label: `Event`'s own `summary` was "
        "treated as the declined Source pair"
    )


# ── THE PROVENANCE LEG (#10965): a post-#10000 pair is METADATA, not a CHOSEN utterance ──

#: The transcript a post-#10000 capture derives its metadata from. Deliberately
#: NOT the same string as :data:`UTTERANCE`: the two rows these legs discriminate
#: differ only in WHICH derivation wrote their pair, so the identity, the
#: `topics` and the column set must stay equal.
POST_10000_TRANSCRIPT = "User: the weekly rebuild happens on Fridays."


def _post_10000_session_row(**overrides: object) -> dict[str, object]:
    """The row #10000's capture writes — built from the LIVE derivation.

    ⭐ Not a hand-typed shape: `_session_source_metadata` and `_source_search_text`
    are the capture's own functions (the single writer of the pair), so this row
    tracks a change to the derivation instead of staling against it. `summary` is
    the §4.6 display DESCRIPTOR and `_searchText` is the bare lexicon, and both
    come from the SAME `topics` list in one statement — which is exactly what
    makes the pair's provenance decidable from a record row.
    """
    summary, topics = _session_source_metadata(POST_10000_TRANSCRIPT)
    assert topics, "the fixture transcript must derive a lexicon"
    row = _source_row(
        url="session:s-10000",
        id="s-10000",
        name="s-10000",
        title="session:s-10000",
        canonical_url="session:s-10000",
        source_kind="agentSession",
        session_id="s-10000",
        topics=list(topics),
        summary=summary,
        _search_text=_source_search_text(topics=topics),
    )
    row.update(overrides)
    return row


def test_a_post_10000_session_row_keeps_the_field_its_fts_index_is_declared_on():
    """#10965 RED-FIRST: the record door dropped the ONE indexed Source field.

    `record-restore` / `rebuild_from_record` MATERIALISE through `node_from_row`,
    so dropping `_searchText` left a restored session `:Source` with no searchable
    text at all — `tortoise_fts_query(entity_type='source')` never returns it
    (#3518), silently, on the door this file pins. The pair is no longer a CHOSEN
    utterance here: it is the topic lexicon (a space-less CJK run arrives whole, so
    for such a turn the run IS the member — the documented residual), and the
    lexicon is the whole point of the capture-side change (#10000).
    """
    row = _post_10000_session_row()
    topics = row["topics"]
    assert isinstance(topics, list)
    node = node_from_row("Source", row)

    # Name the field through the constant the index reads, and prove the index
    # really is declared on it — a rename must not leave this leg asserting a
    # dead name while the FTS leg stays broken.
    assert SOURCE_SEARCH_TEXT_FIELD in dict(_FULLTEXT_INDEX_LABEL_FIELDS)["Source"]
    assert node.props.get(SOURCE_SEARCH_TEXT_FIELD) == ", ".join(topics), (
        "a record-restored session `:Source` lost "
        f"{SOURCE_SEARCH_TEXT_FIELD!r} — the only field the Source FTS index is "
        "declared on — so the restored session is unfindable through "
        f"`tortoise_fts_query(entity_type='source')`: {node.props!r}"
    )
    # `derived == replay(row)`: the restored value IS the value the capture
    # wrote, so a served node and a rebuilt node cannot disagree on it.
    assert node.props[SOURCE_SEARCH_TEXT_FIELD] == _source_search_text(topics=topics)

    # ⛔ AND THE KEEP IS NOT A PROSE LEAK in this fixture: what lands is the bag of
    # words, never the turn. (A space-less (CJK) run is the one shape where the run
    # IS the field — `_session_source_metadata`'s residual — so this leg's English,
    # word-delimited transcript is what makes the absence assertion meaningful.)
    # The transcript's own phrase must not appear in the restored field, which is
    # the property this file's sibling guard (`test_10000_source_prose.py`) scans
    # every node's strings for.
    needle = POST_10000_TRANSCRIPT.split(": ", 1)[1].rstrip(".")
    assert needle.lower() not in node.props[SOURCE_SEARCH_TEXT_FIELD].lower(), (
        "the record door put the transcript's own phrase back on the node"
    )


def test_the_drop_is_decided_by_the_rows_own_provenance_not_by_the_column_name():
    """The narrowing is EXACT equality with the lexicon join — not a shape guess.

    Same identity, same `topics`, same column; only the provenance differs:
    `_captured_session_row` above carries the pre-#10000 prose and is still
    dropped. A `startswith`/`in`/"looks like a word list" implementation would
    pass the keep leg above while silently re-indexing a legacy sentence, which is
    the #3518 findability paid for with §9.1/D1 prose — so the near-misses are
    pinned here, not left to the implementation's good behaviour.
    """
    topics = _post_10000_session_row()["topics"]
    assert isinstance(topics, list)
    joined = ", ".join(topics)

    for near_miss in (f"{joined} and that is all", f" {joined}", joined.upper(),
                      joined.replace(", ", ",")):
        node = node_from_row("Source",
                             _post_10000_session_row(_search_text=near_miss))
        assert SOURCE_SEARCH_TEXT_FIELD not in node.props, (
            f"{near_miss!r} is not the lexicon derivation (it merely looks like "
            "one) and it reached the graph as the session's indexed text")

    # And the legacy prose row is still dropped — the control that makes the
    # keep leg above a DISCRIMINATION rather than the guard being off.
    legacy = node_from_row("Source", _captured_session_row())
    assert SOURCE_SEARCH_TEXT_FIELD not in legacy.props


def test_a_list_shaped_legacy_utterance_is_not_mistaken_for_the_lexicon():
    """#10852 review P2 — exact equality ALONE re-indexed a legacy sentence.

    The pre-#10000 capture wrote `summary` = the transcript's first substantive
    utterance and derived `_searchText` FROM that same value, so a legacy row
    holds `summary == _search_text == <the utterance>`. When the utterance IS a
    comma-space list, `_search_text` coincides with `", ".join(topics)` WORD FOR
    WORD and the join test alone cannot tell it from the post-#10000 lexicon —
    the prose is projected, lands in the Source FTS index, and nothing reds (the
    exact cost `_EPISODIC_SOURCE_PROSE_COLUMNS` says a false POSITIVE has).

    A shopping list, a tag list, a comma-separated set of names: all reach it.
    The second term is the WITNESS — the post-#10000 `summary` carries
    `SESSION_SOURCE_SUMMARY_PREFIX` and a prose utterance does not.

    NON-VACUITY (measured, not asserted): with the witness removed from
    `_episodic_source_search_text_is_the_lexicon`, this leg fails on its first
    assertion (`_searchText` reached the node) while the legacy control in
    `test_the_drop_is_decided_by_the_rows_own_provenance_not_by_the_column_name`
    still passes — i.e. every pre-existing legacy leg was blind to this shape.
    """
    #: The legacy derivation, by construction: the utterance is the list, and the
    #: legacy `topics` are its first-encounter word order (`Counter.most_common`),
    #: which for an all-count-1 list IS this order.
    utterance = "milk, eggs, bread, cheese, apples, oranges"
    legacy = _source_row(
        url="session:s-list",
        id="s-list",
        name="s-list",
        title="session:s-list",
        canonical_url="session:s-list",
        source_kind="agentSession",
        session_id="s-list",
        topics=["milk", "eggs", "bread", "cheese", "apples", "oranges"],
        summary=utterance,
        _search_text=utterance,
    )
    # The hole, stated as the premise it is: this row's pair IS the join.
    assert legacy["_search_text"] == ", ".join(legacy["topics"])

    node = node_from_row("Source", legacy)
    assert SOURCE_SEARCH_TEXT_FIELD not in node.props, (
        "a legacy utterance that happens to be a comma-space list satisfied the "
        "join test and its PROSE was projected as the session's indexed text — "
        "§9.1/D1 prose in the Source FTS index, and nothing reds")
    assert "summary" not in node.props, (
        "the pair is one datum; declining one half re-created the sentence under "
        "the column next door")

    # ⛔ AND THE LEG IS A DISCRIMINATION, not the guard switched off: the same
    # `topics`, the same join, differing only in the display prefix, is kept.
    post = node_from_row("Source", dict(legacy, summary=(
        _SESSION_SOURCE_SUMMARY_PREFIX + utterance)))
    assert post.props.get(SOURCE_SEARCH_TEXT_FIELD) == utterance, (
        "the witness is not a shape test on the IDENTITY — a post-#10000 row "
        "with identical `topics`/`_searchText` must still project the lexicon, "
        "or the fix has un-indexed every restored session (#3518)")


def test_the_witness_fails_closed_on_every_unprovable_summary():
    """The second term is FAIL-CLOSED: no `summary` ⇒ no keep, never a guess.

    `summary` is the witness, so it is also a new way to lose the keep: a row
    whose display column is absent, empty, or carries the join WITHOUT the
    declared prefix cannot prove the post-#10000 derivation and stays declined.
    That direction is the acceptable one (an un-indexed session until it is
    re-captured, visible and named at the table) — the opposite direction is
    prose on a node, which nothing reds.

    Pinned so a later lane cannot "simplify" the witness into a truthiness test
    (`if summary:`) — an empty string is falsy and a non-prefixed sentence is
    truthy, and both are the wrong answer.
    """
    row = _post_10000_session_row()
    joined = row["_search_text"]
    assert isinstance(joined, str)

    for summary, why in (
        (None, "the display column is absent"),
        ("", "the display column is empty"),
        (joined, "the legacy shape: the join with no display prefix"),
        (f" {_SESSION_SOURCE_SUMMARY_PREFIX}{joined}",
         "a prefix that is not the declared one"),
        (f"{_SESSION_SOURCE_SUMMARY_PREFIX}{joined} and that is all",
         "the prefix followed by more than the lexicon"),
    ):
        node = node_from_row("Source", dict(row, summary=summary))
        assert SOURCE_SEARCH_TEXT_FIELD not in node.props, (
            f"{why} was accepted as proof of the post-#10000 derivation — the "
            "witness is not fail-closed")

    # The control: the LIVE derivation's own row still keeps the field, so the
    # loop above is a discrimination rather than a blanket drop.
    assert SOURCE_SEARCH_TEXT_FIELD in node_from_row("Source", row).props


def test_the_display_summary_stays_declined_and_the_residual_is_named():
    """The residual, pinned so it cannot become an accident (#10965).

    `_searchText` is restored because its value is mechanically decidable from the
    row — `_search_text == ", ".join(topics)` AND the display column carries that
    join behind `SESSION_SOURCE_SUMMARY_PREFIX`, both of which the capture writes
    from one list in one statement. `summary` itself stays DECLINED on every row:
    restoring it would put the whole descriptor on the node for no retrieval gain
    (the index reads `_searchText`), so the column is read as a WITNESS and never
    projected. Cost, stated rather than implied: a record-restored session
    `:Source` carries no `summary` while a live one does.
    """
    node = node_from_row("Source", _post_10000_session_row())

    assert SOURCE_SEARCH_TEXT_FIELD in node.props
    assert "summary" not in node.props, (
        "`summary` was restored without a way to tell the descriptor from the "
        "prose — that is not a relaxation this guard may take on its own"
    )


def test_the_reconciler_compares_the_restored_lexicon_on_a_post_10000_row():
    """The partition must stay PROVENANCE-correct — not "the pair is never judged".

    A post-#10000 row's `_searchText` is now projected by the record door, so the
    cache-owned exclusion has nothing left to shield it: `_partition_cache_owned`
    keeps every name the RECORD side holds, which means a DRIFTED restored
    `_searchText` reds instead of hiding in `graph_only`. Without this leg the
    narrowing could be satisfied by an implementation that also stops comparing
    the field — a guard that stops guarding without reddening.
    """
    from tortoise import record_reconcile as rr

    row = _post_10000_session_row()
    truth = node_from_row("Source", row).props
    key = f"Source:{row['url']}"
    drifted = dict(truth, **{SOURCE_SEARCH_TEXT_FIELD: "stale, lexicon"})
    scan = rr.scan_cache_view(_FakeGraph([(drifted,)]), labels=["Source"])

    result = rr.reconcile_record_and_cache(
        {key: dict(truth)}, {key: drifted}, placed=scan.placed)

    assert result.report.field_mismatches == 1, (
        "a restored session's indexed text drifted from the record and the "
        "reconciler did not red — the cache-owned exclusion swallowed a field "
        "the record door now PROJECTS"
    )
    assert not dict(result.graph_only).get(SOURCE_SEARCH_TEXT_FIELD)


def test_the_placement_reads_stay_at_CALL_time():
    """A module-level placement read breaks `import` on an installed tree.

    `canonical_property` reaches the placement table ON DISK
    (`docs/epics/.../02-identity-and-placement.md`), which a wheel does not ship.
    Round 4 evaluated it at module level and `import tortoise.cache_projection`
    failed outright on such a tree — taking `tortoise.record_reconcile`,
    `tortoise.legacy_ids` and the `record-reconcile`/`record-restore` verbs with
    it (review cycle 5, P1). Nothing pinned the laziness until this leg: reverting
    either helper to a module constant left the whole suite green.

    Re-executing the MODULE BODY is the import-time evaluation, so a reload is the
    torch: after it, both caches must still be EMPTY (nothing was resolved while
    the module body ran).
    """
    import importlib

    import tortoise.cache_projection as cp

    try:
        importlib.reload(cp)
        assert cp.episodic_source_prose_properties.cache_info().currsize == 0, (
            "the declined-pair frozenset was resolved while the module body ran — "
            "moving it back to module level breaks `import` on an installed tree")
        assert cp._episodic_source_kind_property.cache_info().currsize == 0, (
            "the kind's property spelling was resolved while the module body ran — "
            "same import-time hazard")
    finally:
        # The reload rebuilt the module object's contents; drop the caches so a
        # later test cannot inherit a half-initialised module.
        cp.episodic_source_prose_properties.cache_clear()
        cp._episodic_source_kind_property.cache_clear()


def test_a_MALFORMED_props_tail_is_REFUSED_not_dropped_in_silence():
    """A non-object `props` tail must not vanish from the projection (#10867 P3).

    `public.source.props` is `jsonb NOT NULL DEFAULT '{}'` with **no CHECK that it
    is an object**, so a corrupt row can carry a scalar or an array. The identity
    read must tolerate that (it answers for EVERY shape a row can hold), but the
    WRITER path must not: `_decoded_tail` answers `{}` for a non-mapping, and
    seeding `out` from that dropped the tail's whole content with no signal —
    the silent loss this module refuses by name everywhere else. Before the tail
    was seeded the block was gated on `if tail:`, so the class split by
    TRUTHINESS: a TRUTHY non-object still raised `TypeError` out of the tail
    block (`set(tail)` for a non-iterable scalar, `dict(tail)` for an array of
    the wrong shape), while a FALSY one was skipped and projected CLEAN. This
    leg pins the loudness — NEW for the falsy half — in the module's own error
    type.

    Both shapes are exercised because both are reachable: an ARRAY
    (`jsonb`'s other collection form) and a bare SCALAR. ⛔ AND IN BOTH
    SPELLINGS, because a row can carry the tail either as JSON TEXT (`'[]'` —
    a `::text` cast, a pre-serialised tail, or a fixture) or already DECODED
    (`[]` — what `psycopg2`'s `jsonb` typecaster answers) — so a refusal keyed
    on falsiness refused `'[]'` and projected `[]`, i.e. the same corrupt row
    aborted `record-restore` when it arrived as TEXT and projected cleanly when
    it arrived decoded (#10867 review, second pass). The ABSENT test is identity
    against `None`/`''`, never truthiness.
    """
    from tortoise.cache_projection import CacheProjectionError

    malformed = (
        ("[1, 2]", "an array (text)"),
        ([1, 2], "an array (decoded)"),
        ('"just a string"', "a scalar (text)"),
        (7, "a scalar (decoded)"),
        # FALSY non-objects: `not raw` would have coerced every one of these.
        ("[]", "an EMPTY array (text)"),
        ([], "an EMPTY array (decoded)"),
        ("0", "zero (text)"),
        (0, "zero (decoded)"),
        (False, "False (decoded)"),
    )
    for payload, what in malformed:
        row = _captured_session_row()
        row["props"] = payload
        with pytest.raises(CacheProjectionError) as caught:
            node_from_row("Source", row)
        assert ".props is a" in str(caught.value) and "not a JSON object" in str(
            caught.value
        ), (
            f"a `props` tail holding {what} must be REFUSED by name — it was "
            f"dropped in silence instead: {caught.value}"
        )

    # The LENIENT shapes must stay lenient, and the set of them is EXACTLY the
    # two spellings of ABSENT: `None` (the column's own silence) and the empty
    # string (`''`, plus the schema default `'{}'` and its decoded form).
    for empty in (None, "", "{}", {}):
        row = _captured_session_row()
        row["props"] = empty
        node = node_from_row("Source", row)
        assert "summary" not in node.props and "_searchText" not in node.props, (
            f"an empty `props` tail ({empty!r}) is not a malformed one — it must "
            "project normally, with the prose still declined")

    # A STRING tail is decoded, so a syntactically broken one raises
    # `JSONDecodeError` (a `ValueError`) rather than projecting as `{}`.
    row = _captured_session_row()
    row["props"] = "{not json"
    with pytest.raises(ValueError):
        node_from_row("Source", row)

    # ⛔ THE DECLARED RESIDUAL, PINNED SO IT CANNOT BE READ AS PARITY (#10867
    # review, closing pass). PostgreSQL's `jsonb` maps JSON `null` and JSON `""`
    # onto the SAME Python values as the two spellings of ABSENT, so a DECODED
    # tail cannot be told apart from an absent one: it projects while the
    # identical row as TEXT is refused. Not closable at the
    # absence test — the decoded values ARE the lenient spellings — and it carries
    # NO content (neither has keys), so it is declared and pinned, not fixed.
    for text_form in ("null", '""'):
        row = _captured_session_row()
        row["props"] = text_form
        with pytest.raises(CacheProjectionError):
            node_from_row("Source", row)


def test_the_row_predicate_ANSWERS_on_a_decoded_json_STRING_tail():
    """The predicate must never raise on a shape a driver can hand back (#10867
    review, closing pass).

    `_decoded_tail` answers for EVERY shape a row can hold — that is its whole
    contract, because `_is_episodic_source_row` is called before anything decides
    whether to project. But it routed every `str` through `json.loads`, which is
    right for a JSON-TEXT tail (a `jsonb` string arrives as its JSON SOURCE,
    `'"hello"'`) and wrong for a DECODED one (a decoding read hands back the
    Python `str` `'hello'`, which is not JSON text): the predicate raised
    `JSONDecodeError` on the decoded shape and answered on the JSON-text one, for
    the same row.
    It now answers `{}` — the DOCUMENT arm, this module's declared safe
    asymmetry. The WRITER path is untouched and stays LOUD, which the same test
    pins beside it so a later fix cannot quieten both.
    """
    from tortoise.cache_projection import _decoded_tail, _is_episodic_source_row

    # The DECODED form of `props='"hello"'::jsonb`: not JSON text at all.
    assert _decoded_tail("hello") == {}, (
        "a decoded `jsonb` string tail must answer `{}` — the row predicate must "
        "answer for every shape a row can hold, never raise"
    )
    row = _captured_session_row()
    row["props"] = "hello"
    assert _is_episodic_source_row(row) is True, (
        "the predicate must still ANSWER (the agentSession column arm decides "
        "here) rather than raising out of the identity read"
    )

    # ⛔ The writer path must NOT inherit that leniency: this row would otherwise
    # project with its tail silently dropped, which is the loss the module
    # refuses by name. A ``str`` that is not valid JSON text stays LOUD — as
    # `JSONDecodeError` (a `ValueError`), the same shape the broken-text case
    # above pins, because the writer decodes through `_tail_payload` directly.
    assert row.get("source_kind") == "agentSession"
    with pytest.raises(ValueError):
        node_from_row("Source", row)

    # And the TEXT spelling of the same column (`'"hello"'`, valid JSON text)
    # decodes to a `str` payload, so the predicate answers too — parity where
    # parity is possible.
    assert _decoded_tail('"hello"') == {}


def test_the_TAIL_SHAPE_prose_names_no_driver_this_repo_never_imports():
    """The tail-shape attribution must be MEASURED, not assumed (#10867 P2).

    The second-opinion review found `_tail_payload`'s docstring calling the
    text-vs-decoded split an **asyncpg-vs-psycopg** divergence. Verified false on
    this tree: nothing under `tortoise/` imports asyncpg, and the record layer
    imports `psycopg2` ONLY (`record_writer._default_connect`), whose docs state
    that `json`/`jsonb` "will be automatically converted to Python objects". The
    split is a SHAPE split — JSON TEXT vs already-decoded — and naming a driver
    no code reaches sends the next reader to preserve compatibility for a code
    path that does not exist.

    This leg pins the PREMISE the correction rests on — that no module under
    `tortoise/` imports `asyncpg` — so a future edit that ACTUALLY ADOPTS the
    driver reds here instead of in a reviewer's read. It does NOT pin the
    CORRECTION's prose: re-wording `_tail_payload`'s docstring back to an
    asyncpg narrative would leave this leg green, and that is deliberate rather
    than a gap, for the reason stated below. ⛔ THE PREDICATE IS AN IMPORT, NOT A SUBSTRING. The scan is
    scoped to `tortoise/` — this file sits outside it, precisely because it names
    the driver in order to REFUTE it — and inside that scope it parses each
    module and matches an `Import`/`ImportFrom` of `asyncpg` alone. A substring
    scan would red on a COMMENT that mentions the driver, with a message
    accusing an import that does not exist: the [#10675] text-scanning-pin class
    this same change removes from `_names_the_reader`
    (`tests/test_metering_capture_usage_reader.py`), and the two scans must not
    disagree, one file apart, about whether prose may fail a pin. If `asyncpg`
    ever really becomes a driving read here, delete this leg in the same change
    that adds the import; the assertion says so, so the failure cannot be
    mistaken for a typo.
    """
    from pathlib import Path

    import tortoise

    root = Path(tortoise.__file__).resolve().parent

    def _imports_asyncpg(path: Path) -> bool:
        tree = ast.parse(path.read_bytes())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                a.name.split(".")[0] == "asyncpg" for a in node.names
            ):
                return True
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "asyncpg"
            ):
                return True
        return False

    offenders = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if _imports_asyncpg(path)
    )
    assert not offenders, (
        "no module under `tortoise/` imports asyncpg, so prose that attributes "
        "the tail-shape split to it names a driver no code reaches — describe "
        "the SHAPE (JSON TEXT vs decoded) instead. If asyncpg is now really a "
        f"read here, drop this leg with the import: {offenders}"
    )


def test_the_identity_view_NEVER_aliases_the_record_row():
    """`_row_identity_view` promises a FRESH mapping on EVERY branch (#10867 P3).

    Its docstring is what R3 leans on to say "the record row is not mutated", and
    the empty-tail branch broke it: `return row` made the "view" the SAME object,
    so a caller writing identity into the view wrote into the record. Latent today
    (`_is_episodic_source_row` short-circuits before calling it with no tail), and
    exactly therefore unpinned — the guarantee has to hold for the caller that
    arrives next, not only for today's.
    """
    from tortoise import cache_projection as cp

    row = _captured_session_row()
    before = dict(row)

    view = cp._row_identity_view(row, {})
    assert view is not row, (
        "`_row_identity_view(row, {})` returned the RECORD ROW itself, so its "
        "own \"the row itself is NEVER mutated\" guarantee is false")
    view["url"] = "session:MUTATED"
    view["source_kind"] = "MUTATED"
    assert dict(row) == before, (
        "mutating the returned identity view mutated the RECORD ROW — the "
        "record is the authority for these values and must not be rewritten by "
        "a projection read")

    # The tail path was already fresh; pin it so a later refactor cannot make the
    # two branches disagree.
    view = cp._row_identity_view(row, {"sourceKind": "agentSession"})
    assert view is not row
    view["source_kind"] = "MUTATED"
    assert dict(row) == before, "the tail branch aliased the record row"


def test_the_10852_obligation_quotes_the_authority_AS_IT_NOW_READS():
    """The quote the `#10852` narrowing rests on must be VERBATIM.

    ⚠️ WHAT THIS GUARDS CHANGED, and the change is recorded here because the pin
    kept its name. It was written for an OBLIGATION — `#10852` was
    expected to DELETE this narrowing, and the quotation was the authority that
    obligation rested on. `#10852` landed on `main` (`ba87797c4`) and did NOT
    delete it: it made the decision PROVENANCE-correct instead (the lexicon
    `_searchText` is projected on a post-#10000 row, `summary` stays declined),
    so the obligation is moot and the forward-merge dropped it. The CITATION is
    not moot and must not go with it: `STORAGE-ARCHITECTURE.md` §9.1 — "§4.6
    metadata, not the S1 narrative" — is precisely why a §4.6-metadata value is
    projected while the narrative is not, i.e. why this door's test is on the
    row's PROVENANCE rather than on the column's NAME. So the same three
    properties are held, now for the narrowing that actually shipped.

    The authority is a quoted ruling in `docs/architecture/STORAGE-ARCHITECTURE.md`
    §9.1 — cited by SECTION here and in `cache_projection`, because the LINE form
    (`:527`, the locator `03b50919dc` wrote) rots the moment text is inserted
    above it: `#4051`'s own §2.0 insertion moved the sentence from `:527` to
    `:551`. ⚠️ **That drift was SILENT**: the old pin never resolved the line —
    it matched the literal `:527` in `cache_projection`'s docstring and then
    asserted the quotation existed *somewhere* in the document, so nothing
    could notice the sentence leaving line 527. It reddened only when the code
    was RE-CITED to `§9.1`, a spelling its hardcoded literal could not match.
    Both halves are the pin's job now, and the LOCATOR RESOLUTION below is how:
    a line has to still hold the sentence, a section has to still exist and
    still contain it.

    That sentence was REWRITTEN
    under us: `#10978` (`b748df7d`) changed it from "§4.4 metadata, not the
    prose" to "§4.6 metadata, not the S1 narrative", so the merge landed four
    citations asserting a sentence the document no longer contains — including
    the wrong section number — with nothing policing it (#10962 review P2).

    So the pin is the property that actually failed: the quoted span, EXTRACTED
    from the code, must still appear in the authority's own text. It is a
    CITATION-ROT pin, not a proof of correctness — if the ruling is legitimately
    restated, update the quotation in the same change (the `#10978` precedent)
    and this leg goes green again without weakening anything.
    """
    import inspect
    import re
    from pathlib import Path

    from tortoise import cache_projection as cp

    root = Path(inspect.getfile(cp)).resolve().parent.parent
    authority = (root / "docs/architecture/STORAGE-ARCHITECTURE.md").read_text(
        encoding="utf-8")

    # ⛔ THE LOCATOR IS EITHER A LINE (`:527`) OR A SECTION (`§9.1`), and BOTH are
    # ACCEPTED — but a locator that is not RESOLVED is not a citation.
    # ⚠️ WHAT THE OLD PIN ACTUALLY DID, because it is why this exists: it matched
    # the literal `STORAGE-ARCHITECTURE.md:527` INSIDE `cache_projection`'s
    # source, then asserted the quotation appeared SOMEWHERE in the document. So
    # it never resolved the line at all — it could not notice the sentence moving
    # away from 527 (this PR's §2.0 insertion pushed it to 551, silently), and it
    # reddened only when the code was RE-CITED to `§9.1`, a form its hardcoded
    # literal could not match. The locator is now RANGED against the authority.
    # ⚠️ The GRAMMAR that finds a citation is deliberately permissive and the
    # CLASSIFIER is what is strict: any citation shape is FOUND, so an
    # unrecognised locator is diagnosed as the PARSER gap it is — never as a
    # missing quotation, which sends a maintainer hunting for a mutation that did
    # not happen (#11481 review round 2: the first cut gated that diagnosis on a
    # 24-character window and on `.` not spanning newlines, so a wrapped or
    # verbose locator still landed on "restore the quotation").
    #
    # ⛔ BUT THE CITATION IS FOUND BY ITS OWN ANCHOR, NEVER BY FIRST MATCH.
    # `re.search` over the whole module takes the FIRST citation-shaped span, so
    # ANY other `STORAGE-ARCHITECTURE.md … : *"…"*` in a 3,400-line file SHADOWS
    # the narrowing's — measured (#11481 review round 3): with a valid decoy above,
    # a citation broken to `§9.2` AND a fully DELETED quotation both certified
    # GREEN, because the decoy was what got resolved. Anchoring is not cosmetic;
    # it is what makes the candidate the NARROWING'S citation — and the anchor is
    # asserted, so deleting it is a loud failure rather than a quiet re-point.
    source = inspect.getsource(cp)
    ANCHOR = "The authority for the pair is"
    # ⛔ THE ANCHOR ITSELF MUST BE UNIQUE, because `str.find` is FIRST MATCH — the
    # same defect one level up. Measured (#11481 review round 4): a SECOND
    # "The authority for the pair is …" paragraph placed above this one, carrying
    # its own valid citation, silently became the candidate, so the narrowing's own
    # citation could be broken to `§9.2` while the pin stayed GREEN. Duplication is
    # not malice — any future paragraph reusing the phrase has that shape — so the
    # count is part of what this pin asserts.
    assert source.count(ANCHOR) == 1, (
        f"the pin's ANCHOR {ANCHOR!r} occurs {source.count(ANCHOR)} times in "
        "`cache_projection` — only the FIRST is used, so a second one silently "
        "becomes the narrowing's citation. Leave exactly one, or re-point the "
        "anchor at phrasing that is unique")
    # ⛔ AND IT MUST BE IN THE MODULE DOCSTRING, not merely in the module: a count
    # of one is satisfied by a `#` COMMENT that carries the phrase plus its own
    # valid citation, which resolves while the narrowing's real citation is broken
    # (#11481 review round 5, measured: relocate the phrase into a line-1 comment,
    # reword the narrowing to "Its authority is", break its locator → GREEN).
    assert ANCHOR in (cp.__doc__ or ""), (
        f"the pin's ANCHOR {ANCHOR!r} is not in `cache_projection`'s MODULE "
        "DOCSTRING — the narrowing is prose, so its anchor must be too; a comment "
        "carrying the phrase is a different paragraph and must not stand in")
    anchor = source.find(ANCHOR)
    WINDOW = 600
    window = source[anchor:anchor + WINDOW]
    # ⚠️ A locator may not SWALLOW another `STORAGE-ARCHITECTURE.md` mention: the
    # tempered `(?!)` makes a span that starts at an earlier bare mention fail to
    # reach `: *"`, so the match begins at the mention that carries the locator.
    # Without it the span could start at an earlier mention and yield a locator of
    # "`STORAGE-ARCHITECTURE.md` §9.1", which the classifier would then report as a
    # PARSER gap — fail-closed, but a wrong diagnosis (#11481 review round 4).
    # ⚠️ The quote group admits an ESCAPED `\"` (`(?:\\.|[^"\\])+`): with a plain
    # `[^"]+`, a quoted sentence containing an escaped quote matched nothing and
    # the pin reported a MISSING QUOTATION for a parser gap (#11481 review round 5).
    # ⚠️ ONE definition of the citation grammar. It is used twice below (the
    # tethered search, and the beyond-tether diagnosis); a second inline copy
    # would mean a future grammar widening fixes one call site and re-creates the
    # very misdiagnosis the second one exists to remove (#11481 review round 10).
    CITATION = (
        r'STORAGE-ARCHITECTURE\.md'
        r'(?P<loc>(?:(?!STORAGE-ARCHITECTURE\.md)[\s\S]){0,200}?)'
        r':\s*\*"(?P<quote>(?:\\.|[^"\\])+)"\*'
    )
    cands = list(re.finditer(CITATION, window))
    # ⚠️ An EMPTY window result must not be reported as a MISSING quotation when
    # the quotation is merely FURTHER AWAY than `WINDOW` (#11481 review round 9,
    # measured: ~640 characters of prose inserted between the anchor and an
    # INTACT citation reddened this pin with "found 0 — restore the quotation",
    # pointing a maintainer at prose that is already correct). Search the whole
    # source so the two causes stay distinguishable.
    if not cands:
        beyond = list(re.finditer(CITATION, source[anchor:]))
        if beyond:
            raise AssertionError(
                "the narrowing's citation IS present, but "
                f"{beyond[0].start()}-{beyond[0].end()} characters after the anchor "
                f"{ANCHOR!r} — its match runs past this pin's {WINDOW}-character "
                "window edge, so the windowed search never sees it. (Reported as a "
                "SPAN, not a start offset: the arm is decided by whether the match "
                "FITS, so a start inside the window with a tail past it lands here "
                "too, and naming only the start would call a 534-character gap "
                "\"beyond the 600-character tether\" — #11481 review round 11.) The "
                "quotation does not need restoring; either move the citation back "
                "beside the anchor or widen WINDOW, so the pin can see the shape "
                "it grades")
    # ⚠️ EXACTLY ONE, because two is AMBIGUOUS and ambiguity must be loud: nothing
    # here can tell which of two citations the narrowing rests on, so resolving
    # either silently certifies a coin flip.
    assert len(cands) == 1, (
        "the narrowing's authority is not a SINGLE citation in the "
        f"{WINDOW}-character window after its anchor {ANCHOR!r}: found "
        f"{len(cands)} — "
        f"{[(' '.join(m.group('loc').split()).strip('`').strip()) for m in cands]}. "
        "The citation shape this pin looks for is `<doc>.md <locator>: *\"…\"*` "
        "(the quote grammar admits an escaped quote). This pin cannot tell which "
        "one the narrowing rests on; leave exactly one — and if the list is EMPTY, "
        "restore the quotation unless the shape itself changed, in which case widen "
        "the grammar rather than the prose")
    # The quotation wraps across lines in the docstring; the document states it
    # as one sentence, so compare on normalised whitespace, and normalise the
    # locator the same way before stripping its backticks.
    quote = " ".join(cands[0].group("quote").split())
    loc = " ".join(cands[0].group("loc").split()).strip("`").strip()
    if re.fullmatch(r":\d+", loc):
        # A LINE locator must still HOLD the sentence. Without this arm the line
        # number is decorative: any insertion above it re-points it silently.
        lineno = int(loc[1:])
        lines = authority.splitlines()
        assert 0 < lineno <= len(lines), (
            f"`cache_projection` cites `STORAGE-ARCHITECTURE.md{loc}`, which is "
            f"past the end of the authority ({len(lines)} lines) — cite the "
            "SECTION (`§9.1`) instead, which does not rot")
        assert quote in " ".join(lines[lineno - 1].split()), (
            f"`cache_projection` cites `STORAGE-ARCHITECTURE.md{loc}` but line "
            f"{lineno} does not hold the whole quotation {quote!r} — either the "
            "sentence moved, or it now WRAPS across lines (this arm compares a "
            "single line, so a reflow is enough to red it). Cite the SECTION "
            "instead (`§9.1`), which cannot rot that way")
    else:
        # A SECTION locator must still NAME a section, and the sentence must live
        # INSIDE it — presence anywhere in the document would certify a citation
        # to the wrong section, the same rot class #10978 corrected (§4.4 → §4.6).
        # ⚠️ The `§` belongs to the CITATION, not to the heading: NO heading in
        # this document NUMBERS ITSELF with it — `## 9. What belongs where` for a
        # section and `### 9.1 …` for a subsection — and two headings mention one
        # in passing (§5's `READ WITH §6` header and `#### 17.4.5 What §17.4 does
        # NOT claim`), which is why the claim is "numbers itself" and not "carries
        # the character". So the locator is stripped to its bare number and ranged
        # at ANY heading level, and the `/D<n>` tag is not part of the heading
        # either.
        # ⛔ `fullmatch`, NOT `search`: an unanchored classifier silently DISCARDS
        # whatever prose follows a valid-looking `§N.N`, so up to 200 characters of
        # arbitrary text could ride along in the locator and the quotation resolved
        # against a section the citation did not name (#11481 review round 3,
        # measured). A locator with trailing words is a PARSER gap, and says so.
        section_match = re.fullmatch(
            r"§\s*(?P<sec>\d+(?:\.\d+)*)(?:/(?P<tag>D\d+))?", loc)
        assert section_match, (
            "the ruling is still quoted in `cache_projection`, but its LOCATOR is "
            f"not one this pin parses ({loc!r}) — accepted forms are "
            "`STORAGE-ARCHITECTURE.md:NNN` and `STORAGE-ARCHITECTURE.md` §N.N, "
            "either with an optional `/DN` tag. This is a PARSER gap, not a "
            "missing quotation: widen the grammar here rather than going looking "
            "for a mutation that did not happen")
        section = section_match.group("sec")
        tag = section_match.group("tag")
        cited = f"§{section}" + (f"/{tag}" if tag else "")
        heading = re.search(
            rf"^(?P<hashes>#+) {re.escape(section)}\b", authority, re.MULTILINE)
        assert heading, (
            f"`cache_projection` cites `{cited}` but the authority has no such "
            "heading — a section citation to a section the document does not "
            "carry sends the reader nowhere")
        # ⛔ THE BODY RUNS TO THE NEXT HEADING OF THE SAME OR A HIGHER LEVEL, not
        # to the next heading of ANY level: a section OWNS its subsections, so
        # slicing at the first `child heading` made `§9` a two-line body that
        # excluded `§9.1`, where this quotation lives — a correct coarse citation
        # refused, with a message saying the sentence was not in the section it is
        # in (#11481 review; measured: `§9` → 2 lines, §9's real body runs to
        # `§10`). Fail-closed on the wrong axis is still wrong.
        level = len(heading.group("hashes"))
        body = authority[heading.end():]
        following = re.search(rf"^#{{1,{level}}} ", body, re.MULTILINE)
        body = body[:following.start()] if following else body
        assert quote in " ".join(body.split()), (
            f"`cache_projection` cites `{cited}`, but the quotation is not in "
            f"that section: {quote!r}. It was restated or moved (#10978 is the "
            "precedent) — re-cite and re-quote where it now reads")
        # ⚠️ THE DECISION TAG IS CHECKED, not merely parsed (#11481 review: a tag
        # that `split("/")` throws away is decorative — `§9.1/D99` passed). This is
        # a SUBSTRING check against the section's body, which is the strongest
        # claim it can make: it proves the tag is named in the section cited, not
        # that the decision id itself is well-formed (the tree has no decision
        # register to resolve it against).
        if tag:
            # ⛔ BOUNDED ON BOTH SIDES, not `tag in body`: §9.1's body names `D30`,
            # so a bare substring check certified `§9.1/D3` — a decision the section
            # does not carry (#11481 review round 5, measured). The lookarounds make
            # `D3` fail against `D30` while `D1`/`D2`/`D30` still pass.
            assert re.search(
                rf"(?<![0-9A-Za-z]){re.escape(tag)}(?![0-9])", body), (
                f"`cache_projection` cites `{cited}`, but `{tag}` is not named in "
                "that section — the decision tag is part of the citation, and a "
                "wrong one names a decision this section does not carry")
    # ⚠️ THERE IS NO THIRD, `quote in authority` ARM. Both arms above assert the
    # quotation against a SLICE of `authority`, so each already implies presence
    # in the whole document; a third assertion could never fire, and carrying it
    # would describe coverage the pin does not have (#11481 review — this is a
    # correction of an earlier claim that it covered a case the arms missed).

    # ⛔ THE REST OF THE SITES THAT CITE THE SAME RULING BY NUMBER, and the list
    # is the DEFINITION (#10962 review, P2 — this leg claimed "the other three
    # sites" and read three files while FOUR live citations of the same ruling
    # sat outside its scope having gone stale to §4.4: `hosted_api.py` twice —
    # the exact sentence #10978 corrected, in a file this PR changes —
    # `test_10000_source_prose.py` once, and this file's own line 811). A scope
    # claim that is short by the sites that are actually stale is the false PASS
    # the leg exists to stop, so the list is widened to the measured population.
    #
    # The assertion is CASE-INSENSITIVE on the phrase, because the stale sites
    # spell it both ways (`§4.4 METADATA` in `hosted_api.py`).
    for rel in (
        "docs/epics/2026-10-10-7879-postgres-record/06-cache-projection.md",
        "tortoise/cache_projection.py",
        "tortoise/hosted_api.py",
        "MEMORY.md",
        "tests/test_10000_source_prose.py",
        "tests/test_10853_session_source_prose.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        if rel == "tests/test_10853_session_source_prose.py":
            # ⛔ EXCISE THIS GUARD'S OWN BLOCK — the repo's documented pattern for
            # a narrative pin. This function QUOTES the old wording as history and
            # asserts the new one's absence, so scanning itself would red on the
            # record of the fix instead of on a fix that is missing. The excision
            # is asserted, so it cannot silently stop excising anything.
            marker = ("def test_the_10852_obligation_quotes_the_authority"
                      "_AS_IT_NOW_READS(")
            assert marker in text, (
                "this leg's own block is gone from its file, so the excision "
                "below is unsafe — re-point it rather than deleting the marker")
            text = text[:text.index(marker)]
        # ⛔ THE ABSENCE ARM COVERS BOTH PHRASINGS. `§4.4\s+metadata` alone could
        # not catch a regression of this file's own line 858, which states the
        # ruling as "the §4.6 display DESCRIPTOR" — and the PRESENCE arm below is
        # satisfied by ANY `§4.6` in the scanned prefix, so a stale line 858 would
        # ship silently as soon as one other §4.6 mention preceded it (measured:
        # revert line 858 + add one unrelated §4.6 earlier → this leg PASSED).
        # `git grep "display DESCRIPTOR"` shows the phrase only in its corrected
        # form, so widening adds no false-red risk.
        stale = re.search(
            r"§4\.4\s+metadata|§4\.4\s+display\s+DESCRIPTOR",
            text, re.IGNORECASE)
        assert not stale, (
            f"{rel} still cites \u00a74.4 for the pair ({stale.group(0)!r}); "
            "the authority (#10978) now says \u00a74.6 — the section number is "
            "part of the citation, and a stale one sends the reader to the "
            "wrong section")
        # ⚠️ PRESENCE IS `§4.6`, NOT `§4.6 metadata`: this file states the ruling
        # as "the §4.6 display DESCRIPTOR", which cites the section without
        # repeating the claim's noun. The ABSENCE arm above stays on the phrase,
        # because that is the rot — a §4.4 that names the metadata class.
        assert "§4.6" in text, (
            f"{rel} no longer cites the authority's section number at all")
