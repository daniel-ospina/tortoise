"""#10000 (§9.1/D1) — the session `:Source` must not carry TURN prose.

THE RULE. `docs/architecture/STORAGE-ARCHITECTURE.md` §9.1 (D1, owner ruling
2026-09-23) puts the transcript in the RECORD: *"not in the graph, not a
`Document` node, not a `Point`"*. #10000's item 2 measured the route that is
still open — `:Source.summary` holds the session's first substantive utterance,
i.e. raw turn text, written by `sdk._session_source_metadata` — and recorded it
as *"the second prose passthrough onto a node"* (capstone artifact
`docs/verify/2026-10-31-record-capstone.md` §4 item 2, and the same reading in
`docs/verify/2026-10-26-record-capstone.md`).

WHY IT IS WORTH A TEST RATHER THAN A NOTE. The same string is the Source's
`_searchText` (#3518), so the leak is on a field the FTS index is declared on:
a session's own words are duplicated into the graph AND into the index that
serves `tortoise_fts_query(entity_type='source')`. Redaction (#4911) does not
close it — a scrubbed sentence is still the session's sentence.

THE ASSERTION IS A GRAPH-WIDE SCAN, not a property read: the defect is "this
text is on a node", and a scan over every node's string properties is the only
form of it that cannot be satisfied by moving the field. A future writer that
copies the utterance onto a different label or property fails this test too.

RED before the fix (measured on `816b37f1876`), GREEN after.
"""
from __future__ import annotations

import pytest

#: A token that exists nowhere else, so a hit cannot be a coincidence. The
#: capture's own turn nodes carry it BY DESIGN (they are the episodic store the
#: §9.1 rule keeps); the assertion below excludes exactly the turn store and
#: nothing else.
MARKER = "TURNZEBRA10000SOURCEPROSE"


@pytest.fixture()
def sdk(tmp_path, monkeypatch):
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(db_path=str(tmp_path / "source_prose.db"),
                      event_log_path=str(tmp_path / "events.jsonl"))
    try:
        yield sdk
    finally:
        sdk.close()


def _nodes_carrying(sdk, needle: str) -> list[tuple[list[str], dict]]:
    """Every node whose STRING properties contain ``needle``.

    Walks the live handle instead of a named field on purpose: the rule is
    about the text's presence on a node, so a hit on any label or property is
    the same defect wearing a different name.
    """
    rows = sdk._get_proj().g.query(
        "MATCH (n) RETURN labels(n), properties(n)").result_set
    hits: list[tuple[list[str], dict]] = []
    for labels, props in rows:
        for key, value in (props or {}).items():
            if isinstance(value, str) and needle in value:
                hits.append((list(labels), {key: value}))
                break
            if isinstance(value, list) and any(
                    isinstance(x, str) and needle in x for x in value):
                hits.append((list(labels), {key: value}))
                break
    return hits


def _capture(sdk, session_id: str) -> None:
    sdk.capture_session(
        conversation=[
            {"role": "user",
             "content": f"{MARKER} the raw transcript text must not live on a "
                        "node unless that node is the episodic turn store"},
            {"role": "assistant",
             "content": "Acknowledged."},
        ],
        session_id=session_id,
    )


def test_the_session_source_does_not_carry_the_transcript(sdk):
    """§9.1/D1: only the turn store may hold a session's own words."""
    session_id = "sess-source-prose-10000"
    _capture(sdk, session_id)

    hits = _nodes_carrying(sdk, MARKER)
    assert hits, (
        "the fixture did not capture anything — a scan that finds nothing "
        "anywhere proves nothing (the turn nodes must carry the marker)")

    offenders = [
        (labels, props) for labels, props in hits
        if not _is_turn_store(labels)
    ]
    assert not offenders, (
        "the transcript reached a node that is NOT the episodic turn store — "
        "§9.1/D1 keeps the session's own words out of the graph:\n"
        + "\n".join(f"  {labels} {props}" for labels, props in offenders))


def _is_turn_store(labels: list[str]) -> bool:
    """The episodic turn nodes are the one place a capture's text belongs.

    `_write_capture_turns` MERGEs `(t:Point {id: '<session>_t<i>'})`, i.e. a
    `:Point` — so the exemption is exactly the Point label. Everything else
    (`:Source`, `:Session`, `:Event`, …) is a derived record of the session and
    must not echo its text.
    """
    return labels == ["Point"] or (
        "Point" in labels and len(labels) == 1)


def test_a_recapture_replaces_the_prose_a_pre_fix_write_left_behind(sdk):
    """The fix must HEAL, not only stop new leaks.

    A Source captured before this change carries the utterance in ``summary``
    and (because #3518's ``_searchText`` SET was a ``coalesce``) in the field
    the FTS index is declared on — so a re-capture that merely stopped
    PRODUCING prose would leave the old prose on the node and in the index
    forever. This seeds that exact pre-fix state and asserts the next capture
    replaces it.
    """
    session_id = "sess-source-prose-heal-10000"
    url = f"session:{session_id}"
    stale = f"{MARKER} stale prose from a pre-fix write"
    proj = sdk._get_proj()
    proj.g.query(
        "MERGE (s:Source {url:$u}) SET s.summary=$sum, s._searchText=$st",
        params={"u": url, "sum": stale, "st": stale})
    assert _nodes_carrying(sdk, MARKER), "the seeded pre-fix state is missing"

    _capture(sdk, session_id)

    offenders = [(labels, props) for labels, props in _nodes_carrying(sdk, MARKER)
                 if not _is_turn_store(labels)]
    assert not offenders, (
        "a re-capture left the pre-fix prose on the node — the SET must replace "
        "a non-empty derivation instead of coalescing into the old value:\n"
        + "\n".join(f"  {labels} {props}" for labels, props in offenders))


# ── the derived metadata is still FINDABLE, and the display prefix is not ──
# `_searchText` is the field the Source FTS index is declared on, and the
# Source retrieval leg OR-unions its query terms
# (`search_engine` → `sparse.build_or_query`). So the constant display prefix
# `summary` carries would make EVERY captured session match any source query
# containing "session", "capture" or "topics" — words no transcript said, and
# none of them an FTS stopword. The two tests below pin both halves: the index
# carries the bare lexicon, and a capture whose transcript has no content word
# still derives one (a blank derivation is unfindable — the #3518 regression).


def _source_rows(sdk) -> dict[str, tuple[str, str]]:
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source) RETURN s.url, s.summary, s._searchText").result_set
    return {r[0]: (r[1] or "", r[2] or "") for r in rows}


#: The descriptor's DISPLAY prefix, repeated as a literal — pinned to the module
#: constant by `test_the_display_prefix_constant_is_pinned` — so the behaviour
#: tests below fail on their ASSERTIONS, not on an import, against code that
#: predates #10000 (the `SEARCH_TEXT_FIELD` precedent in `test_source_fts.py`).
DISPLAY_PREFIX = "session capture topics: "


def test_the_display_prefix_constant_is_pinned() -> None:
    from tortoise.sdk import _SESSION_SOURCE_SUMMARY_PREFIX

    assert _SESSION_SOURCE_SUMMARY_PREFIX == DISPLAY_PREFIX


def test_the_display_prefix_is_not_indexed(sdk):
    """The lexicon is indexed; the descriptor's constant prefix is not.

    Measured pre-fix: two sessions with disjoint vocabularies (a migration plan
    and a fencing budget) were BOTH returned for the query "session capture
    topics" — the prefix alone answered a query neither transcript had anything
    to do with.
    """
    sdk._materialize_session_source(
        "sess-prefix-migration", None, "2026-09-01T10:00:00+00:00",
        [{"role": "user",
          "content": "we adopted the QUOKKA migration plan for the ledger"}])
    sdk._materialize_session_source(
        "sess-prefix-fencing", None, "2026-09-02T10:00:00+00:00",
        [{"role": "user", "content": "badger fencing budget spreadsheet"}])
    rows = _source_rows(sdk)

    # NON-VACUITY: the field is populated, so the "no match" below cannot be
    # the empty-index pass.
    assert rows["session:sess-prefix-migration"][1], rows
    assert DISPLAY_PREFIX.strip() not in \
        rows["session:sess-prefix-migration"][1], rows

    hits = [r["id"] for r in sdk.tortoise_fts_query(
        "session capture topics", entity_type="source")]
    assert hits == [], (
        "a source query made only of the display prefix matched a captured "
        f"session — the prefix is in the index: {hits}")

    # …and the session is still findable by a word it actually used.
    found = [r["id"] for r in sdk.tortoise_fts_query(
        "QUOKKA", entity_type="source")]
    assert "session:sess-prefix-migration" in found, found


def test_an_acknowledgement_only_session_still_derives_and_is_findable(sdk):
    """A non-empty transcript with no content word must still derive SOMETHING.

    ``_session_source_metadata`` promised that in its own docstring, and #3518
    needs it: an empty derivation blanked ``summary``, wrote no ``_searchText``
    and left the session unfindable through
    ``tortoise_fts_query(entity_type='source')``.
    """
    from tortoise.sdk import _session_source_metadata

    summary, topics = _session_source_metadata("user: ok\nassistant: yes")
    assert topics, "a transcript made of words must not derive an empty lexicon"
    assert summary, summary

    sdk._materialize_session_source(
        "sess-ack-only", None, "2026-09-01T10:00:00+00:00",
        [{"role": "user", "content": "ok"},
         {"role": "assistant", "content": "yes"}])
    rows = _source_rows(sdk)
    assert rows["session:sess-ack-only"][1], rows

    assert "yes" in topics, topics
    found = [r["id"] for r in sdk.tortoise_fts_query(
        "yes", entity_type="source")]
    assert "session:sess-ack-only" in found, (topics, found)


def test_a_text_less_recapture_keeps_the_two_derived_columns_tied(sdk):
    """``summary`` and ``_searchText`` are two views of ONE derivation.

    ``summary`` was written unconditionally while ``_searchText`` kept its
    previous value when the derivation was empty, so a text-less re-capture
    BLANKED the display column and left the indexed one — the pair the shared
    rule ties together, diverging. They must keep together or replace together.
    """
    session_id = "sess-columns-tied-10000"
    url = f"session:{session_id}"
    _capture(sdk, session_id)
    before_summary, before_search = _source_rows(sdk)[url]
    assert before_summary and before_search, "the capture derived nothing"
    assert before_search in before_summary, (before_summary, before_search)

    # A text-less re-capture (no turns to derive from) must not split the pair.
    sdk._materialize_session_source(
        session_id, None, "2026-09-02T10:00:00+00:00", [])

    summary, search = _source_rows(sdk)[url]
    assert summary == before_summary, (
        "a text-less re-capture blanked `summary` while `_searchText` kept its "
        f"value: {summary!r} vs the indexed {search!r}")
    assert search == before_search, (search, before_search)
    assert summary == DISPLAY_PREFIX + search, (summary, search)
