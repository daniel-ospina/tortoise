"""#10853 — the session `:Source` carries no turn prose, on the RECOVERY door.

The capture writes the transcript's first substantive utterance as the session
`Source`'s `summary`, and derives `_searchText` from that same value (#1352,
`_source_search_text(summary=…)` — the capture path has no title). #10000's
ruling (§9.1/D1) is that captured turn text does not live on a graph node.

⛔ THE DOOR THIS FILE PINS IS NOT THE CAPTURE. `tortoise record-restore` and
`record_serve`'s `fetch-record` repair re-materialise a node from the RECORD ROW
through `cache_projection.node_from_row` — so a row written by a build that
still placed the key re-creates the prose on the graph, and nothing reds.

The property pinned is a PAIR, because they are one datum: the capture derives
`_searchText` from the summary (`_source_search_text`), so dropping only
`summary` would leave the identical sentence on the node under the column next
door — the #10620-review naming, `_CONTENT_COLUMNS` calls them *"the SAME
datum"*.

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
