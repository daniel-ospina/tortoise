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
    """A pre-fix node HEALS on re-capture — and the healing is the DERIVATION.

    A Source captured before this change carries the utterance in ``summary``
    and (because #3518 puts the summary in the searchable field) in
    ``_searchText``. This seeds that exact pre-fix state and asserts the next
    capture replaces it, so the fix does not merely stop new leaks.

    ⛔ WHAT THIS TEST DOES NOT PROVE, stated so nobody infers it: the ``_searchText``
    SET is still ``coalesce($st, s._searchText)``, and THAT is not the healing
    mechanism — ``coalesce`` already yields the new derivation whenever one
    exists. An earlier cut of this PR rewrote the clause as an equivalent
    ``CASE WHEN $st IS NOT NULL …`` and cited this test as its proof; it passes
    under BOTH forms, so it is evidence for the metadata change and for nothing
    about the clause (measured: 2 passed with the clause reverted).
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
        "a re-capture left the pre-fix prose on the node — the capture's own "
        "derivation (a topic lexicon, non-NULL for any transcript with content) "
        "must replace it:\n"
        + "\n".join(f"  {labels} {props}" for labels, props in offenders))
