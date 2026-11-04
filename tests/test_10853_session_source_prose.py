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

⚠️ ONE LEG PER ROW-SIGNAL, and one for the shape that must NOT be caught. A
single positive row carrying all the signals at once cannot tell which one
fired: deleting any one branch of `_is_episodic_source_row` would leave the
suite green. The last leg is the reason this matters — a transcript DOCUMENT
carries `session_id` as a legitimate coordinate (`EventAPI.add_document` takes
it; `tortoise/ingest.py` passes the frontmatter's `sessionId`), so a guard keyed
on `session_id` dropped that document's own frontmatter `summary` (review P1 on
this PR, measured).
"""

from __future__ import annotations

import pytest

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


@pytest.mark.parametrize("signal", ["url", "source_kind", "is_episodic"])
def test_each_episodic_signal_catches_the_prose_on_its_own(signal):
    """One leg per signal: any ONE makes the row episodic on its own.

    They are ORed because a LEGACY row is exactly the row that may be missing
    one of them, so each signal has to carry the rule alone. A single positive
    row setting all of them would leave every branch untested — deleting one
    would keep the suite green.
    """
    # ⚠️ `url` is the label's CONTRACT KEY, so no row can omit it — and a leg
    # for `source_kind`/`is_episodic` therefore needs a url that is NOT a
    # `session:` ref, with the OTHER two signals ABSENT, or an earlier signal
    # would do the work and the branch under test would never be reached. The
    # non-session url is the legacy shape the OR exists for: a captured session's
    # row whose url no longer spells the prefix.
    overrides: dict[str, object] = {
        "url": ("session:s-10853" if signal == "url"
                else "https://example.com/legacy-session-s-10853"),
        "source_kind": "agentSession" if signal == "source_kind" else None,
        "is_episodic": True if signal == "is_episodic" else None,
    }
    row = _source_row(id="s-10853", summary=UTTERANCE, _search_text=UTTERANCE,
                      **overrides)

    node = node_from_row("Source", row)

    assert "summary" not in node.props, (
        f"the {signal!r} signal alone did not make the row episodic: the turn "
        f"prose reached the node ({node.props.get('summary')!r})")
    assert "_searchText" not in node.props, (
        f"the {signal!r} signal alone did not make the row episodic: the prose "
        f"survived as _searchText ({node.props.get('_searchText')!r})")


def test_a_transcript_document_keeps_its_summary_even_though_it_has_a_session_id():
    """⛔ THE MIRROR DEFECT — review P1 on this PR, pinned so it cannot return.

    `session_id` is NOT an episodic signal. It is a legitimate DOCUMENT
    coordinate: `EventAPI.add_document` accepts one (`tortoise/api.py`) and
    `tortoise/ingest.py` passes a transcript's `sessionId` frontmatter into it,
    so a transcript document is a `public.source` row with `source_kind` a
    document kind, `summary` its own frontmatter summary, AND a non-empty
    `session_id`. A guard keyed on `session_id` dropped that document's summary
    — silently, on the recovery door, which is the defect class this change
    exists to close, in the opposite direction.
    """
    row = _source_row(
        url="corpus://transcripts/s-10853.md",
        id="corpus://transcripts/s-10853.md",
        source_kind="document",
        document_kind="transcript",
        session_id="s-10853",
        title="Weekly rebuild transcript",
        summary="A transcript whose frontmatter names its own summary.",
        _search_text="Weekly rebuild transcript A transcript whose frontmatter…",
    )
    node = node_from_row("Source", row)

    assert node.props["summary"] == (
        "A transcript whose frontmatter names its own summary."), (
        "a transcript DOCUMENT lost its own frontmatter summary: `session_id` "
        "was treated as an episodic signal, so the guard fired on a document"
    )
    assert node.props["_searchText"].startswith("Weekly rebuild transcript")


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
