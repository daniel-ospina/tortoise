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

import re

import pytest

#: A token that exists nowhere else, so a hit cannot be a coincidence. The
#: capture's own turn nodes carry it BY DESIGN (they are the episodic store the
#: §9.1 rule keeps); the assertion below excludes exactly the turn store and
#: nothing else.
MARKER = "TURNZEBRA10000SOURCEPROSE"

#: The needle the prose guard scans for: the marker TOGETHER WITH the word that
#: follows it in the captured turn — a phrase, not a token.
#:
#: ⛔ WHY A PHRASE AND NOT THE BARE MARKER. The shipped derivation is a TOPIC
#: LEXICON, and a session's own words legitimately appear in it as single
#: comma-joined tokens (§4.4 metadata, which is what keeps a capture findable —
#: #3518). A bag of words can never contain a SPACE, so this needle is
#: unreachable by the lexicon by construction, while every prose route carries
#: it: the pre-fix verbatim copy, a lower-casing writer, a truncating writer.
#: Scanning for the bare marker would report the lexicon itself — a false FAIL
#: that would push the next fixer to delete the findability the lexicon exists
#: for.
PROSE_NEEDLE = f"{MARKER} the"


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


def _nodes_carrying(sdk, needle: str) -> list[tuple[list[str], dict, object]]:
    """Every node whose STRING properties contain ``needle``, CASE-INSENSITIVELY.

    Walks the live handle instead of a named field on purpose: the rule is
    about the text's presence on a node, so a hit on any label or property is
    the same defect wearing a different name. Returns the labels, the ONE
    offending property (not the whole property map — it can carry embeddings),
    and the node's ``id`` so the caller can tell the turn store from any other
    node wearing the same label.

    ⛔ CASE-INSENSITIVE IS LOAD-BEARING, not a convenience. The writer lowercases
    every token (``transcript.lower()``), so a case-sensitive comparison let a
    writer that copies the turn's PROSE and normalises it pass unseen — the
    non-vacuity guard was satisfied solely by the turn store's upper-case copy
    while three ``:Source`` properties carried the offender (measured: `#10852`
    review, P1). Casing is the writer's choice; presence is the defect.
    """
    folded = needle.lower()
    rows = sdk._get_proj().g.query(
        "MATCH (n) RETURN labels(n), properties(n)").result_set
    hits: list[tuple[list[str], dict, object]] = []
    for labels, props in rows:
        node_id = (props or {}).get("id")
        for key, value in (props or {}).items():
            if isinstance(value, str) and folded in value.lower():
                hits.append((list(labels), {key: value}, node_id))
                break
            if isinstance(value, list) and any(
                    isinstance(x, str) and folded in x.lower() for x in value):
                hits.append((list(labels), {key: value}, node_id))
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

    hits = _nodes_carrying(sdk, PROSE_NEEDLE)
    assert hits, (
        "the fixture did not capture anything — a scan that finds nothing "
        "anywhere proves nothing (the turn nodes must carry the marker)")

    offenders = [
        (labels, props) for labels, props, node_id in hits
        if not _is_turn_store(labels, node_id, session_id)
    ]
    assert not offenders, (
        "the transcript reached a node that is NOT the episodic turn store — "
        "§9.1/D1 keeps the session's own words out of the graph:\n"
        + "\n".join(f"  {labels} {props}" for labels, props in offenders))


def _is_turn_store(labels: list[str], node_id: object, session_id: str) -> bool:
    """The episodic turn nodes are the one place a capture's text belongs.

    `_write_capture_turns` MERGEs `(t:Point {id: '<session>_t<i>'})`
    (`_capture_turn_id`), so the exemption is the Point label **and** that id
    space — never the label alone.

    ⛔ §9.1/D1 says the transcript is "not in the graph, not a `Document` node,
    **not a `Point`**", so a `:Point` holding a capture's words for any other
    reason is the same defect wearing the label the ruling names in bold:
    exempting `Point` wholesale would make this guard blind to exactly that
    route, while its own scope claims say otherwise. The id bound costs nothing
    — in a capture, `<session>_t0` is the ONLY Point that carries the marker.
    """
    return (list(labels) == ["Point"]
            and isinstance(node_id, str)
            and re.fullmatch(re.escape(session_id) + r"_t\d+",
                             node_id) is not None)


def test_the_guard_reports_a_lower_casing_writer(sdk):
    """The prose guard is not case-blind: a normalising copy is still reported.

    ⛔ THE REGRESSION THIS PINS. The scan used to compare case-sensitively while
    the derivation lowercases every token, so a writer that copied the turn's
    prose and lowercased it — one ``.lower()`` away from this file's own
    derivation — produced ZERO offenders, and the non-vacuity guard was
    satisfied solely by the turn store's upper-case copy. Measured at that head:
    the ``:Source`` carried the marker in ``summary``, ``topics`` and
    ``_searchText`` and the guard reported none of them (`#10852` review, P1).

    The node is seeded rather than captured because the capture path heals it
    (the recapture test proves that half); this one isolates the SCAN.
    """
    prose = (f"{MARKER} the raw transcript text must not live on a node"
             .lower())
    sdk._get_proj().g.query(
        "MERGE (s:Source {url:$u}) SET s.summary=$s, s._searchText=$s",
        params={"u": "session:sess-source-prose-lowercased-10000",
                "s": prose})

    hits = _nodes_carrying(sdk, PROSE_NEEDLE)
    assert hits, (
        "the guard did not see a lower-cased copy of the turn's prose — a "
        "case-sensitive scan is blind to the writer this module ships, and its "
        "non-vacuity guard can still pass on the turn store alone")
    # The offender is the seeded node and nothing else. Asserted on the VALUE, not
    # the property NAME: both `summary` and `_searchText` hold the prose and the
    # property iteration order is not part of any contract.
    ((labels, props, _),) = hits
    assert labels == ["Source"], hits
    assert list(props.values()) == [prose], (
        f"the offender reported is not the normalising writer: {hits}")


def test_the_guard_reds_on_the_pre_fix_derivation(sdk, monkeypatch):
    """NON-VACUITY against the real pre-fix code, not a hypothetical writer.

    ``816b37f1876``'s ``_session_source_metadata`` returned the transcript's
    first substantive utterance as ``summary`` — the route §9.1/D1 forbids — and
    ``_source_search_text`` had no lexicon then (its rule was ``title or
    summary``, and the capture passed the derived ``summary``), so **the same
    sentence also landed in ``_searchText``**, the field the Source FTS index is
    declared on (#3518). BOTH routes are reproduced here: patching only the
    metadata function would leave the ``_searchText`` half — the half this
    file's module docstring calls load-bearing — unconstructed, so the arm could
    not show it reds.

    Both are monkeypatched in (the capture path resolves the module attributes at
    call time) so this suite proves RED-before without a second checkout: the
    guard must report the ``:Source`` that derivation produces.
    """
    from tortoise import sdk as sdk_mod

    #: The summaries the pre-fix derivation produced, in call order — the pre-fix
    #: `_source_search_text` is handed `topics=` by the CURRENT call site, so the
    #: pre-fix search text has to be recovered from here rather than recomputed.
    derived_summaries: list[str] = []

    def pre_fix_derivation(transcript: str) -> tuple[str, list[str]]:
        # Verbatim from `git show 816b37f1876:tortoise/sdk.py`, the topic block
        # INCLUDED: the pre-fix function returned COMPUTED topics, and a fake
        # that returns `[]` would make the current capture derive
        # `_search_text = None` — quietly deleting the route under test.
        if not transcript.strip():
            return "", []
        summary = ""
        for line in transcript.splitlines():
            text = line.split(": ", 1)[1] if ": " in line else line
            text = text.strip()
            if len(text) >= 10:
                summary = text[:200]
                break
        if not summary:
            summary = transcript.strip()[:200]
        from collections import Counter
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", transcript.lower())
        counts = Counter(w for w in words
                        if w not in sdk_mod._SESSION_SOURCE_STOPWORDS)
        topics = [w for w, _ in counts.most_common(6)]
        derived_summaries.append(summary)
        return summary, topics

    def pre_fix_source_search_text(*, title: str | None = None,
                                   summary: str | None = None,
                                   topics: list[str] | None = None):
        """``816b37f1876``'s rule: ``text = title or summary; return text or None``.

        The pre-fix capture passed its derived ``summary`` (there was no
        ``topics`` kwarg), so the utterance went into ``_searchText`` as well.
        The current call site passes ``topics=``; the pre-fix prose is therefore
        read back from the derivation above rather than recomputed.
        """
        text = title or (derived_summaries[-1] if derived_summaries else None)
        return text or None

    monkeypatch.setattr(sdk_mod, "_session_source_metadata",
                        pre_fix_derivation)
    monkeypatch.setattr(sdk_mod, "_source_search_text",
                        pre_fix_source_search_text)
    session_id = "sess-source-prose-prefix-10000"
    _capture(sdk, session_id)

    offenders = [
        (labels, props) for labels, props, node_id in _nodes_carrying(
            sdk, PROSE_NEEDLE)
        if not _is_turn_store(labels, node_id, session_id)
    ]
    assert offenders, (
        "the guard passed the PRE-FIX derivation — a guard that cannot red on "
        "the code it was written to police proves nothing about the fix")
    assert any("Source" in labels for labels, _ in offenders), offenders
    # ⛔ THE `_searchText` HALF IS ASSERTED, NOT ASSUMED. The module docstring
    # calls the indexed copy the load-bearing one (#3518), so this arm has to
    # show the pre-fix capture actually CONSTRUCTED it — otherwise a fake that
    # empties `topics` would red on `summary` alone and this test would still
    # pass while the route it exists to police went unexercised.
    #
    # Read the node directly rather than through `_nodes_carrying`: that helper
    # deliberately returns ONE offending property per node (it `break`s after the
    # first hit), so a node whose `summary` matched first would never expose its
    # `_searchText`.
    indexed = [
        props
        for _labels, props in sdk._get_proj().g.query(
            "MATCH (n:Source) RETURN labels(n), properties(n)").result_set
        if PROSE_NEEDLE.lower()
        in str((props or {}).get("_searchText") or "").lower()
    ]
    assert indexed, (
        "the pre-fix arm did not reproduce the `_searchText` prose route — the "
        "fake derivation or the `_source_search_text` patch is not faithful"
    )


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
    assert _nodes_carrying(sdk, f"{MARKER} stale"), (
        "the seeded pre-fix state is missing")

    _capture(sdk, session_id)

    offenders = [(labels, props) for labels, props, node_id
                 in _nodes_carrying(sdk, f"{MARKER} stale")
                 if not _is_turn_store(labels, node_id, session_id)]
    assert not offenders, (
        "a re-capture left the pre-fix prose on the node — the capture's own "
        "derivation (a topic lexicon, non-NULL for any transcript with content) "
        "must replace it:\n"
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

    Measured on an EARLIER CUT OF THIS PR: two sessions with disjoint
    vocabularies (a migration plan and a fencing budget) were BOTH returned for
    the query "session capture topics" — the prefix alone answered a query
    neither transcript had anything to do with. (Not "pre-fix": on `main` no
    prefix existed at all, so that query could not have matched either session —
    the cut that introduced the prefix introduced the defect.)
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
    assert not ({"user", "assistant", "speaker"} & set(topics)), (
        "the stopword-only fallback re-admitted the transcript's own framing "
        f"labels into the lexicon (and so into the index): {topics!r}")
    found = [r["id"] for r in sdk.tortoise_fts_query(
        "yes", entity_type="source")]
    assert "session:sess-ack-only" in found, (topics, found)
    # …and the constant pair is NOT in the index: under the OR-union it would
    # make every acknowledgement-only session match a query for "user" — the
    # same class of defect the display prefix is kept out of `_searchText` for.
    framing_hits = [r["id"] for r in sdk.tortoise_fts_query(
        "user", entity_type="source")]
    assert "session:sess-ack-only" not in framing_hits, (topics, framing_hits)

    # ⛔ THE SHORT-WORD ARM (#10852 review, P2) — the cliff this test used to
    # step over by one character. The presence test used to run the 3-character
    # CONTENT floor (`[A-Za-z][A-Za-z0-9_-]{2,}`) inside "does this transcript
    # have any word tokens at all", so a non-empty transcript whose words are
    # ALL short fell through to `("", [])`: `summary` blank AND no `_searchText`
    # at all (this writer never passes `summary` to `_source_search_text`, so
    # the empty topics list resolves to `None`) — the #3518 unfindability the
    # lexicon exists to prevent. The fixture above (`"ok"` / `"yes"`) sat one
    # character above the cliff, so it could not see it.
    # MEASURED PRE-FIX (`7ca6221b4`): topics=[] , summary='' , `_searchText` None.
    short_summary, short_topics = _session_source_metadata("user: ok go")
    assert short_topics, (
        "a non-empty transcript made of SHORT words derived nothing — the "
        "3-char content floor is being applied to the presence test instead of "
        "only to the ranking")
    assert short_summary, short_summary

    sdk._materialize_session_source(
        "sess-short-words", None, "2026-09-01T11:00:00+00:00",
        [{"role": "user", "content": "ok go"}])
    short_written = _source_rows(sdk)["session:sess-short-words"]
    assert short_written[1], (
        "the short-word session's :Source got no _searchText, so it is "
        f"unfindable through the Source FTS index: {short_written!r}")
    assert short_written[1] == ", ".join(short_topics), short_written

    # …and it is findable by a word it actually used. `go`/`ok` are not
    # RediSearch stopwords, so the OR-union leg matches them.
    assert "session:sess-short-words" in [
        r["id"] for r in sdk.tortoise_fts_query("go", entity_type="source")]

    # ⛔ NEGATIVE CONTROL — the widening must stop at "tokens actually present".
    # The two arms above pin the derivation in ONE direction only ("a non-empty
    # transcript must not derive nothing"), so on its own this test would stay
    # green if the presence test were widened again — to `\S+`, say — and a
    # transcript of punctuation started deriving a "lexicon" from `"..."` and
    # `"?"`. That is the same class of defect swapped end for end: the docstring
    # states `("", [])` happens *"only for a transcript with no word tokens at
    # all (empty, blank, punctuation-only, or nothing but the Speaker: framing
    # labels)"*, and until this control existed nothing executed that promise.
    # The framing-only shape is the one that must stay silent now that the
    # `Speaker:` filter runs over a FLOORLESS pattern: it is the shape the old
    # `{2,}` floor used to drop by accident, so nothing else pins it.
    for wordless in ("", "   ", "...", "?!", "user:\nassistant:"):
        assert _session_source_metadata(wordless) == ("", []), (
            f"a transcript with NO word tokens ({wordless!r}) derived a "
            "descriptor — the presence test was widened past 'tokens present' "
            "and would now index punctuation or bare framing labels")


def test_the_lexicon_does_not_depend_on_the_transcript_word_order():
    """The descriptor is a function of the word MULTISET, not of turn order.

    ⛔ MEASURED PRE-FIX (`0f381f8e1`): ``Counter.most_common`` breaks ties by
    first-encountered order, and nearly every word in a short session occurs
    once — so the lexicon WAS the transcript's first six content words in the
    transcript's own order, i.e. the session's opening instruction in a comma
    list. Two orderings of the same words therefore produced different
    descriptors, contradicting the module's own "never word order" claim
    (`#10852` review, P1).
    """
    from tortoise.sdk import _session_source_metadata

    utterance = "rotate the production database password tomorrow morning"
    forward = _session_source_metadata(utterance)
    backward = _session_source_metadata(
        "morning tomorrow password database production the rotate")

    assert forward == backward, (
        "reordering the same words changed the descriptor — the tie-break is "
        f"still the transcript's own order: {forward!r} vs {backward!r}")
    # The utterance's OWN order must not survive as the descriptor either.
    assert forward[0] != (DISPLAY_PREFIX
                          + "rotate, production, database, password, "
                            "tomorrow, morning"), forward
    # …and it is still the six most frequent words, not an arbitrary six.
    assert set(forward[1]) == {"rotate", "production", "database", "password",
                               "tomorrow", "morning"}, forward


def test_the_lexicon_still_prefers_the_most_frequent_words():
    """Frequency outranks the tie-break, so sorting ties changed nothing else.

    Sorting the ties by token must not be mistaken for sorting the WHOLE lexicon
    alphabetically: a word said three times still leads its one-off neighbours.
    """
    from tortoise.sdk import _session_source_metadata

    _, topics = _session_source_metadata(
        "zebra zebra zebra apple mango turnip walnut radish")
    assert topics[0] == "zebra", topics
    assert topics == ["zebra", *sorted(topics[1:])], topics
    assert len(topics) == 6, topics


def test_a_text_less_recapture_keeps_the_two_derived_columns_tied(sdk, tmp_path):
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

    # ⛔ AND THE RECORD MUST STATE WHAT THE WRITE *STORED* (review round 4).
    # The keep-arm above is live-side only unless `SourceCreated` carries the
    # STORED columns instead of the call's derivation: journalling the call's
    # empty derivation made `derived != replay(journal)` — a rebuild wrote `''`
    # over the display column while `_searchText` survived, splitting this pair
    # on the exact recovery path the carrier exists for (#5024/#5097).
    from tortoise.log import EventLog

    topics = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.topics",
        params={"u": url}).result_set[0][0]
    assert topics, f"the lexicon column blanked: {topics!r}"

    sdk._get_proj().rebuild(
        EventLog(str(tmp_path / "events.jsonl")), confirm_destructive=True)

    replayed_summary, replayed_search = _source_rows(sdk)[url]
    replayed_topics = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.topics",
        params={"u": url}).result_set[0][0]
    assert (replayed_summary, replayed_search) == (summary, search), (
        "replay(journal) diverged from the live node — the record carried the "
        f"call's derivation, not the stored columns: live {(summary, search)!r} "
        f"vs replayed {(replayed_summary, replayed_search)!r}")
    assert replayed_topics == topics, (replayed_topics, topics)


def test_a_point_that_is_not_a_turn_is_reported_not_exempted(sdk):
    """The exemption is the TURN-STORE id space, never the `Point` label.

    §9.1/D1 names `Point` explicitly ("not in the graph, not a `Document` node,
    not a `Point`"), so an exemption keyed on the label alone would make this
    guard blind to the one route the ruling writes down in bold — and the two
    scope claims above would be false. This pins the narrow predicate: a
    `:Point` outside `<session>_t<i>` is an OFFENDER.
    """
    session_id = "sess-source-prose-pointexempt-10000"
    sdk._get_proj().g.query(
        "MERGE (p:Point {id:'point-not-a-turn-10000'}) SET p.text=$t",
        params={"t": MARKER})
    _capture(sdk, session_id)

    hits = _nodes_carrying(sdk, MARKER)
    assert len(hits) >= 2, f"the seeded non-turn Point is missing: {hits}"
    offenders = [(labels, props) for labels, props, node_id in hits
                 if not _is_turn_store(labels, node_id, session_id)]
    assert offenders, (
        "a non-turn `:Point` carrying the capture's text was EXEMPTED — the "
        "predicate must bound the exemption to the turn-store ids, because "
        "§9.1/D1 forbids the transcript on a `Point` too")
