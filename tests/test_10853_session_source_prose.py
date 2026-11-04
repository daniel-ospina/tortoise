"""#10853 — the session `:Source` carries no turn prose, on the RECOVERY door.

The capture writes the transcript's first substantive utterance as the session
`Source`'s `summary`, and derives `_searchText` from that same value (#1352,
`_source_search_text(summary=…)` — the capture path has no title). #10000's
ruling (§9.1/D1) is that captured turn text does not live on a graph node.

⛔ THE DOOR THIS FILE PINS IS NOT THE CAPTURE. `tortoise record-restore` and
`record_serve`'s `fetch-record` repair re-materialise a node from the RECORD ROW
through `cache_projection.node_from_row` — so a row written by a build that
still placed the key re-creates the prose on the graph, and nothing reds. The
row below is that shape: `source_kind='agentSession'`, `summary` = the
utterance, `_search_text` = the same utterance.

The property pinned is a PAIR, because they are one datum: the capture derives
`_searchText` from the summary (`_source_search_text`), so dropping only
`summary` would leave the identical sentence on the node under the column next
door — the #10620-review naming, `_CONTENT_COLUMNS` calls them *"the SAME
datum"*.
"""

from __future__ import annotations

from tortoise import record_projection as rp
from tortoise.cache_projection import node_from_row

#: The transcript slice a pre-fix capture left on the row.
UTTERANCE = "We decided the weekly rebuild happens on Fridays."


def _source_row(**overrides: object) -> dict[str, object]:
    """A `public.source` row of the SHAPE Postgres returns (every column)."""
    row: dict[str, object] = {c: None for c in sorted(rp.table_columns()["source"])}
    row.update(overrides)
    return row


def _captured_session_row(**overrides: object) -> dict[str, object]:
    """The row a legacy capture wrote: a session Source whose column holds prose."""
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


def test_a_document_source_keeps_its_summary_and_search_text():
    """The negative leg: `summary` on a DOCUMENT is its own metadata, not prose.

    A document's `_searchText` is the shared rule's title+summary+topics, so
    neither may be dropped — a guard that fired on every Source would silently
    un-index every document in the cache.
    """
    row = _source_row(
        url="https://example.com/q3-pricing",
        id="src-1",
        source_kind="document",
        title="Q3 pricing memo",
        summary="A memo about the Q3 pricing change.",
        _search_text="Q3 pricing memo A memo about the Q3 pricing change.",
    )
    node = node_from_row("Source", row)

    assert node.props["summary"] == "A memo about the Q3 pricing change."
    assert node.props["_searchText"] == (
        "Q3 pricing memo A memo about the Q3 pricing change.")


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
