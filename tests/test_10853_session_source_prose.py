"""#10853 — the session `:Source` carries no turn prose, on the RECOVERY door.

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

import pytest

from tortoise import record_projection as rp
from tortoise.cache_projection import node_from_row
from tortoise.projection import _FULLTEXT_INDEX_LABEL_FIELDS
from tortoise.sdk import (
    SOURCE_SEARCH_TEXT_FIELD,
    _session_source_metadata,
    _source_search_text,
)

#: The transcript slice a pre-fix capture left on the row.
UTTERANCE = "We decided the weekly rebuild happens on Fridays."


def _source_row(**overrides: object) -> dict[str, object]:
    """A `public.source` row of the SHAPE Postgres returns (every column)."""
    row: dict[str, object] = {c: None for c in sorted(rp.table_columns()["source"])}
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
        "words), not a transcript slice — it is the metadata #10000 keeps"
    )


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


# ── THE PROVENANCE LEG (#10965): a post-#10000 pair is METADATA, not prose ──

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
    the §4.4 display DESCRIPTOR and `_searchText` is the bare lexicon, and both
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
    (#3518), silently, on the door this file pins. The pair is no longer prose
    here: it is the topic lexicon, and the lexicon is the whole point of the
    capture-side change (#10000).
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

    # ⛔ AND THE KEEP IS NOT A PROSE LEAK: what lands is the bag of words, never
    # the turn. The transcript's own phrase must not appear in the restored field
    # — the property this file's sibling guard (`test_10000_source_prose.py`)
    # scans every node's strings for.
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


def test_the_display_summary_stays_declined_and_the_residual_is_named():
    """The residual, pinned so it cannot become an accident (#10965).

    `_searchText` is restored because its value is mechanically decidable from the
    row (`_search_text == ", ".join(topics)` AND `summary` carries the post-#10000
    display prefix — both terms, see `_episodic_source_search_text_is_the_lexicon`
    and the P2 leg below). `summary` — the descriptor itself — is NOT restored:
    the door exists for the field the FTS index is declared on (#3518), and
    re-materialising the display carrier is a change with its own decision. Cost,
    stated rather than implied: a record-restored session `:Source` carries no
    `summary` while a live one does.

    ⭐ The prefix is no longer out of reach (#10852 P2): it lives in
    `session_projection.SESSION_SOURCE_SUMMARY_PREFIX`, the dependency-free leaf
    module both sides import, and it is the provenance TEST's second term. So the
    residual is now a scope choice, and this leg keeps it one — the recognisability
    that made the test sound must not quietly become a projection.
    """
    node = node_from_row("Source", _post_10000_session_row())

    assert SOURCE_SEARCH_TEXT_FIELD in node.props
    assert "summary" not in node.props, (
        "`summary` was restored without a decision about the display carrier — "
        "that is not a relaxation this guard may take on its own"
    )


def test_a_list_shaped_legacy_row_is_not_mistaken_for_the_lexicon():
    """#10852 review P2 — the join ALONE is not proof of provenance.

    The pre-#10000 capture derived `summary` from the transcript's first
    substantive LINE and `topics` from the same words in first-encounter order
    (`Counter.most_common`), so a session whose first line was a comma-space list
    wrote `summary == ", ".join(topics)` EXACTLY:

        first line  : "milk, eggs, bread, cheese, apples, oranges"
        summary     : the same string            (legacy: the utterance)
        topics      : ["milk", ..., "oranges"]   (first-encounter order)

    A join-only predicate calls that "the post-#10000 derivation" and
    re-materialises the session's own sentence as `_searchText` — the §9.1/D1
    datum on the FTS-indexed field, with nothing red. So the row's DISPLAY column
    must carry the post-#10000 prefix too, and this leg is the one that reds if
    that term is dropped (measured pre-fix: it did).
    """
    listing = "milk, eggs, bread, cheese, apples, oranges"
    row = _captured_session_row(
        summary=listing,
        _search_text=listing,
        topics=["milk", "eggs", "bread", "cheese", "apples", "oranges"],
    )
    # The trap is REAL: this legacy row satisfies the join term exactly.
    assert row["_search_text"] == ", ".join(row["topics"])
    assert row["summary"] == row["_search_text"]

    node = node_from_row("Source", row)
    assert SOURCE_SEARCH_TEXT_FIELD not in node.props, (
        "a pre-#10000 row whose first line happened to be a comma-separated list "
        "was re-materialised as the session's indexed text — the list shape "
        "satisfies `_search_text == \", \".join(topics)` and so the display "
        "column has to carry the post-#10000 prefix as well"
    )


def test_the_prefix_is_the_second_term_of_the_provenance_test():
    """The keep is not decided by the join alone — the prefix term is load-bearing.

    Same `_searchText`, same `topics`, same identity; only `summary` differs. Each
    of these is a row where the join holds and the DISPLAY column does not carry
    the post-#10000 prefix, so no derivation is proven and the field stays
    declined (fail-closed). Without these the guard could be satisfied by the
    join alone again and #10000's index leak would return silently.
    """
    row = _post_10000_session_row()
    joined = row["_search_text"]
    summary = row["summary"]
    assert isinstance(joined, str) and isinstance(summary, str)

    for why, unproven_summary in (
        ("the bare join — exactly what a list-shaped legacy row holds", joined),
        ("the prefix without the join", summary[: len(summary) - len(joined)]),
        ("a prose sentence beside the lexicon", "We decided the rebuild is on Fridays."),
        ("absent", None),
    ):
        node = node_from_row("Source",
                             _post_10000_session_row(summary=unproven_summary))
        assert SOURCE_SEARCH_TEXT_FIELD not in node.props, (
            f"`summary` held {why!r}, which does not prove the post-#10000 "
            "derivation, and `_searchText` was re-materialised anyway"
        )

    # And the control: with BOTH terms present the field IS restored, so the
    # three declines above are a discrimination and not the guard being off.
    assert SOURCE_SEARCH_TEXT_FIELD in node_from_row("Source", row).props


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
