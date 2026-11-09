"""#10000 (§9.1/D1) — the session `:Source` must not carry TURN prose.
⛔ BOUND OF THIS GUARD, stated because it is blind here BY CONSTRUCTION: the
scan's needle is a Latin phrase containing a SPACE, and a lexicon member can
never contain one — so a space-less (CJK) capture, whose single run arrives
WHOLE and is the turn's text verbatim (`_session_source_metadata`'s residual),
would pass this file while carrying it. The absence leg is therefore evidence
about word-delimited scripts; the residual is named, not claimed away.

THE RULE. `docs/architecture/STORAGE-ARCHITECTURE.md` §9.1 (D1, owner ruling
2026-09-23) puts the transcript in the RECORD: *"not in the graph, not a
`Document` node, not a `Point`"*. #10000's item 2 measured the route that was
still open AT THAT STAMP — `:Source.summary` HELD the session's first substantive
utterance, i.e. raw turn text, written by `sdk._session_source_metadata` — and
recorded it as *"the second prose passthrough onto a node"* (capstone artifact
`docs/verify/2026-10-31-record-capstone.md` §4 item 2, and the same reading in
`docs/verify/2026-10-26-record-capstone.md`). ⛔ PAST TENSE ON PURPOSE: on today's
tree that builder returns a topic-lexicon DESCRIPTOR behind
`_SESSION_SOURCE_SUMMARY_PREFIX` and the indexed text is the bare join, so a
present-tense reading of this paragraph would re-arm the premise the change
removed (#10852 review, P3).

WHY IT WAS WORTH A TEST RATHER THAN A NOTE. The same string WAS the Source's
`_searchText` (#3518), so the leak sat on a field the FTS index is declared on:
a session's own words were duplicated into the graph AND into the index that
serves `tortoise_fts_query(entity_type='source')`. Redaction (#4911) did not
close it — a scrubbed sentence is still the session's sentence. The legs below
stay armed against a REINTRODUCTION of either shape, which is why they are
stated as a rule rather than as a historical note.

THE ASSERTION IS A GRAPH-WIDE SCAN, not a property read: the defect is "this
text is on a node", and a scan over every node's string properties is the only
form of it that cannot be satisfied by moving the field. A future writer that
copies the utterance onto a different label or property fails this test too.

RED before the fix (measured on `816b37f1876`), GREEN after.
"""
from __future__ import annotations

import inspect
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
#: comma-joined tokens (§4.6 metadata, which is what keeps a capture findable —
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


def _the_search_text_set_comment_block() -> str:
    """The comment block IMMEDIATELY above the ``_searchText`` SET, verbatim.

    The block is the ONLY prose at the write site, so it is the text a future
    editor reads while deciding whether to touch the ``coalesce``. It is
    extracted from the live source rather than restated here: a guard that
    quotes the claim in its own body can go green while the claim returns to
    ``sdk.py``.

    ⛔ THE ANCHOR IS ASSERTED TO MATCH EXACTLY ONCE. A ``break``-on-first-hit
    walk over a source with two candidate lines would silently inspect the
    wrong one, and the whole guard would then be measuring a comment nobody
    reads. An anchor that stops matching is a FAIL, never an empty block.
    """
    from tortoise.sdk import TortoiseSDK

    src = inspect.getsource(TortoiseSDK._materialize_session_source)
    lines = src.splitlines()
    anchor = "SOURCE_SEARCH_TEXT_FIELD}=coalesce($st,"
    found = [i for i, line in enumerate(lines) if anchor in line]
    assert len(found) == 1, (
        f"the `_searchText` SET ({anchor!r}) must appear EXACTLY ONCE in "
        f"`_materialize_session_source`; found it at {found}. Re-point this "
        "guard at the SET rather than deleting it — it is the only thing "
        "tying this file to the comment that justifies the clause")
    block: list[str] = []
    for line in reversed(lines[:found[0]]):
        if not line.lstrip().startswith("#"):
            break
        block.append(line)
    return "\n".join(reversed(block))


def test_the_write_site_does_not_claim_a_re_capture_heals_a_pre_fix_node():
    """The comment above the `_searchText` SET must not assert the retraction.

    THE DEFECT THIS PINS, and why it is not cosmetic. This PR measured that a
    pre-#10000 node is **NOT** healed by a re-capture (the MINT GATE makes the
    re-capture a #1727 no-op replay, so these SETs are never reached) and
    corrected three artifacts that said otherwise — the method docstring, the
    PR body, and a test that was RENAMED off the claim. A FOURTH artifact was
    then written by the same PR, ten lines below the docstring's ``⛔``: the
    inline block quoted the retracted mechanism back at the exact line a future
    editor changes, and cross-referenced a note that does not exist.

    That is not a typo class. The false premise had already cost this PR two
    rounds: an earlier cut rewrote this ``coalesce`` as an equivalent
    ``CASE WHEN $st IS NOT NULL …`` and claimed the rewrite WAS the healing
    (it is the same function — a provable no-op), and a test was named for
    healing while exercising a different state. Both were later falsified. A
    comment that re-arms the premise at the write site is the cheapest possible
    route back to a third round, and the docstring's ``⛔ Do NOT make the
    healing true by re-SETting on the replay arm`` is exactly what it
    undercuts — a reader who believes the population self-heals has no reason
    to respect that guard, and re-SETting on the replay arm reverses
    #2104/#1727.

    WHAT IS ASSERTED is therefore the CORRECTION, in both directions: the
    retracted claim is ABSENT, and the correcting mechanism (mint gate / replay
    / #1727) is PRESENT. The second half is what stops the cheap green of
    deleting the block: a write site whose ``coalesce`` has no comment at all
    is how the claim got re-derived in the first place.

    Citations are made EXECUTABLE rather than trusted (#7948's lesson — a
    hand-maintained prose reference rots and nothing notices): every ``test_*``
    name the block cites must exist in this module, and a reference to
    ``_session_source_metadata``'s "note" must resolve to a note that is
    actually there.
    """
    from tortoise.sdk import _session_source_metadata

    block = _the_search_text_set_comment_block()
    assert block.strip(), (
        "no comment sits above the `_searchText` SET — the clause is a "
        "keep-arm whose reasoning has been re-derived from scratch twice "
        "already; restore the corrected comment rather than deleting it")

    # (1) The retracted claim is ABSENT. These are the shapes a future lane
    # re-introduces it in; the second is the pre-fix text verbatim, so this
    # arm was RED against `4529894849`.
    for retracted in ("makes an old node heal", "the DERIVATION is",
                      "so it replaces that text"):
        assert retracted not in block, (
            f"the comment above the `_searchText` SET asserts the RETRACTED "
            f"mechanism ({retracted!r}). A re-capture does not heal a "
            f"pre-fix node — the mint gate makes it a replay, so these SETs "
            f"are never reached (#10852 review P2). Cite the docstring "
            f"above and the surviving-pair test instead.\n\n{block}")

    # (2) The CORRECTION is PRESENT — the mint-gate mechanism the docstring
    # states, in the block's own words. Deleting the block cannot pass this.
    lowered = block.lower()
    assert "replay" in lowered and "1727" in lowered, (
        "the comment above the `_searchText` SET states neither the REPLAY "
        "(the mechanism that makes a re-capture a no-op) nor #1727 — the "
        "two things that make the clause correct. Without them the block is "
        "readable as 're-capture repairs the old population', which is "
        f"false:\n\n{block}")

    # (3) Executable citations. A dangling reference is how the previous block
    # sent a reader to `_session_source_metadata`'s "note" — a note that has
    # never existed on that function.
    #
    # The pattern skips a FILE reference (`tests/this_file.py`, which is the
    # path the block cites) and admits only the `::symbol` / bare-symbol use,
    # so the guard resolves the thing that must exist rather than the filename
    # it lives in — measured: matching the bare `\btest_…` shape red the guard
    # on its own file path instead of on this arm.
    for cited in set(re.findall(
            r"(?<![/\w])(test_[a-z0-9_]+)(?!\.py)", block)):
        assert cited in globals(), (
            f"the comment above the `_searchText` SET cites `{cited}`, which "
            "does not exist in this module — a prose reference to a test that "
            "is not there reads as proof and pins nothing")

    if "_session_source_metadata" in block:
        announced = (block.split("_session_source_metadata", 1)[1][:60]
                     .lower())
        if "note" in announced:
            assert "heal" in (_session_source_metadata.__doc__ or "").lower(), (
                "the comment above the `_searchText` SET points at "
                "`_session_source_metadata`'s note on healing, and that "
                "function's docstring carries no such note (measured at "
                "`4529894849`) — the note is in `_materialize_session_source`'s "
                "own docstring. Point at the artifact that says it")


# NOTE (reconciliation, `#10852`): the projection-door doc guard that briefly
# lived here as `test_the_projection_door_doc_does_not_claim_a_re_capture_repairs_a_legacy_node`
# now lives in `tests/test_10000_cache_projection_doc_pin.py`. Two lanes reached
# the same fix; the surviving guard is the DB-free one registered in `tier1`,
# because the guarded artifact is under `docs/` and a docs-only diff runs `tier1`
# ONLY — in this file (the `core` surface, one throwaway FalkorDB per case) the
# doc arm would sleep on exactly the diff that re-arms the claim. Nothing is
# lost: that file asserts the same absence/presence pair, over the WHOLE doc,
# plus the doc<->module-docstring agreement and the resolvability of every
# `test_*` the doc's "### The pin" section cites.


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

    The node is seeded rather than captured so this test isolates the SCAN.
    ⛔ Do not read it as evidence that a pre-fix node heals: it does not. The
    replay arm that used to pin that (`test_a_re_capture_does_not_heal_a_pre_fix_node`)
    went with the SDK `capture_session` door this branch deletes — `#4282`, loss
    recorded on `#10227` — so the claim now stands on the WRITE SITE alone, pinned
    by `test_the_write_site_does_not_claim_a_re_capture_heals_a_pre_fix_node` above,
    and on the hosted lane, which is the surviving reader.
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
    # ⛔ `system:` / `tool:` are the LOAD-BEARING pair of
    # `_SESSION_SOURCE_TURN_LABEL_RE` (#10852 review, P2): `user`/`assistant`/
    # `speaker` are also stopwords, so a fixer could drop the per-line label
    # strip and believe the stopword arm still covers it — it does not, the
    # stopword arm does not reach the fallback, and `system`/`tool` are on no
    # stopword list at all. This leg reds on that removal alone (verified by
    # patching the strip out: it derives `['system', 'tool']`, and so does every
    # such transcript in the Source FTS index). The pair no longer reds on a
    # token-set filter, because there is none: the strip is positional, so a
    # `system`/`tool` BODY word survives while the LABEL does not —
    # `test_a_role_word_that_is_also_a_content_word_survives` is the other half.
    for wordless in ("", "   ", "...", "?!", "user:\nassistant:",
                     "system:\ntool:"):
        assert _session_source_metadata(wordless) == ("", []), (
            f"a transcript with NO word tokens ({wordless!r}) derived a "
            "descriptor — the presence test was widened past 'tokens present' "
            "and would now index punctuation or bare framing labels")


def test_a_role_word_that_is_also_a_content_word_survives(sdk):
    """The ``Speaker:`` label is dropped by POSITION, never by SPELLING.

    THE DEFECT THIS PINS (#10852 review, P2 — measured at `7ca4871c06`). The
    framing filter was a ``frozenset({"user", "assistant", "speaker", "system",
    "tool"})`` applied to EVERY token, so ``system`` and ``tool`` — ordinary
    English content words — could never enter a session's lexicon anywhere:

    * ``"User: the system keeps restarting after the deploy"`` derived
      ``['deploy', 'keeps', 'restarting']`` — ``system`` silently gone.
    * ``"User: system\\nAssistant: tool"`` derived ``("", [])``: the two bodies
      ARE the transcript's own tokens, so the ``("", [])`` arm fired on a
      transcript that has words, writing ``summary=''`` and no ``_searchText`` —
      the #3518 unfindability the docstring reserves for a transcript with no
      token of its own, and a small REGRESSION against ``main`` (whose prose
      fallback indexed ``system``).

    ⛔ The suite could not see it: the ack-only leg pins the labels-ONLY
    direction and asserts the absence of ``{'user', 'assistant', 'speaker'}`` —
    the three that are also stopwords — so nothing asserted that a role word
    survives as CONTENT. Both shapes below red on the pre-fix code and green on
    the per-line strip, while the labels-only control in the ack-only test keeps
    the constant-pair defect closed.
    """
    from tortoise.sdk import _session_source_metadata

    # (1) A role word in the BODY ranks like any other content word.
    summary, topics = _session_source_metadata(
        "User: the system keeps restarting after the deploy")
    assert "system" in topics, (
        "a body word that spells a role was filtered out of the lexicon: "
        f"{topics!r} — the framing filter is reading SPELLING, not POSITION")
    assert summary.endswith(", ".join(topics)), (summary, topics)

    # (2) The two-body cliff: the bodies ARE tokens, so the transcript is NOT
    # wordless and must not take the `("", [])` arm.
    body_summary, body_topics = _session_source_metadata(
        "User: system\nAssistant: tool")
    assert body_topics, (
        "a transcript whose own words are `system` / `tool` derived an EMPTY "
        "lexicon — summary='' and no _searchText, so the session is "
        "unfindable through tortoise_fts_query(entity_type='source') (#3518)")
    assert set(body_topics) == {"system", "tool"}, body_topics
    assert body_summary, body_summary

    # (3) NON-VACUITY for the strip: the LABELS still never become vocabulary.
    # Without this, widening the strip to nothing (`re.sub("", ...)`) would
    # satisfy (1) and (2) and re-open the constant-pair defect.
    assert not ({"user", "assistant"} & set(body_topics)), (
        "the transcript's own framing labels were re-admitted into the "
        f"lexicon: {body_topics!r}")

    # (4) …and the reachable shape writes a findable node. The capture path
    # builds its transcript from the turns, so this is the same derivation on
    # the same input a real session produces.
    sdk._materialize_session_source(
        "sess-role-words", None, "2026-09-01T12:00:00+00:00",
        [{"role": "user", "content": "system"},
         {"role": "assistant", "content": "tool"}])
    written = _source_rows(sdk)["session:sess-role-words"]
    assert written[1], (
        "the :Source got no _searchText, so a session whose words are role "
        f"words is unfindable through the Source FTS index: {written!r}")
    assert written[0], written
    assert "session:sess-role-words" in [
        r["id"] for r in sdk.tortoise_fts_query("tool", entity_type="source")]


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

