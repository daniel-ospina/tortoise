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


def _capture(sdk, session_id: str) -> dict:
    return sdk._capture_session(
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

    The node is seeded rather than captured so this test isolates the SCAN.
    ⛔ Do not read it as evidence that a pre-fix node heals: it does not — see
    `test_a_re_capture_does_not_heal_a_pre_fix_node`, which pins the replay.
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


def test_the_first_capture_replaces_the_prose_a_pre_seeded_stub_left_behind(
        sdk):
    """A node with NO `:Session` yet is replaced by its FIRST capture.

    This is a real shape and not the pre-fix one: `_link_source` MERGEs a
    `session:<id>` Source STUB before any capture of that id has run, and the
    first capture of the session is a genuine (non-replay) capture — so it DOES
    reach `_materialize_session_source` and the derivation replaces whatever
    the stub carried.

    ⛔ WHAT THIS TEST DOES *NOT* PROVE, stated so nobody infers it (the earlier
    title/name claimed the opposite, `#10852` review P1). It does **not** show
    that a PRE-FIX node heals on re-capture: a pre-fix node coexists with the
    `:Session` node of the capture that wrote it, so re-capturing it is the
    #1727 no-op REPLAY — `_materialize_session_source` is never called, and the
    stale pair survives. `test_a_re_capture_does_not_heal_a_pre_fix_node` pins
    that state. The `_searchText` SET is also still `coalesce($st,
    s._searchText)`, and THAT is not the mechanism here either — `coalesce`
    already yields the new derivation whenever one exists. An earlier cut of
    this PR rewrote the clause as an equivalent `CASE WHEN $st IS NOT NULL …`
    and cited this test as its proof; it passes under BOTH forms (measured: 2
    passed with the clause reverted).
    """
    session_id = "sess-source-prose-stub-10000"
    url = f"session:{session_id}"
    stale = f"{MARKER} stale prose from a pre-fix write"
    proj = sdk._get_proj()
    proj.g.query(
        "MERGE (s:Source {url:$u}) SET s.summary=$sum, s._searchText=$st",
        params={"u": url, "sum": stale, "st": stale})
    assert _nodes_carrying(sdk, f"{MARKER} stale"), (
        "the seeded pre-fix state is missing")
    assert not proj.g.query(
        "MATCH (n:Session {id:$sid}) RETURN count(n) AS n",
        params={"sid": session_id},
    ).result_set[0][0], (
        "this fixture must stay the STUB shape (no `:Session`). With one "
        "present the capture is a replay and this test silently becomes a "
        "second copy of `test_a_re_capture_does_not_heal_a_pre_fix_node`, "
        "passing green while covering nothing — the exact fixture/claim "
        "mismatch `#10852` review P1 found in this test's earlier form")

    _capture(sdk, session_id)

    offenders = [(labels, props) for labels, props, node_id
                 in _nodes_carrying(sdk, f"{MARKER} stale")
                 if not _is_turn_store(labels, node_id, session_id)]
    assert not offenders, (
        "the FIRST capture left the pre-seeded stub's prose on the node — its "
        "own derivation (a topic lexicon, non-NULL for any transcript with "
        "content) must replace it:\n"
        + "\n".join(f"  {labels} {props}" for labels, props in offenders))


def test_a_re_capture_does_not_heal_a_pre_fix_node(sdk):
    """⛔ A pre-fix node is NOT healed by a re-capture — a replay never writes.

    THE CLAIM THIS REPLACES. This file and two docstrings used to say the pre-fix
    population repairs itself: *"a pre-fix node HEALS on re-capture, and the
    reason is the DERIVATION"*. That is false, and the mechanism is the MINT
    GATE: `_materialize_session_source`'s only production caller sits inside
    `if not session_existed or (retry_failed_capture and not no_provider)`, so a
    re-capture of an ALREADY CAPTURED session id is the #1727 no-op replay and
    never reaches the `SET`s. A node holding the pre-fix pair was written by a
    run of that same method — i.e. one where `session_existed` was False — so it
    coexists with the `:Session` node of its own capture (`capture_ok` True),
    and the pair survives every later capture of that id, in the graph AND in
    the Source FTS index (the OR-union pollution #10000 measures). Measured:
    `#10852` review P1.

    ⛔ WHAT THIS TEST IS. A PIN ON A RECORDED DECISION, not an endorsement of a
    leak. #2104/#1727 require a re-capture of an existing session to be a TRUE
    no-op (it must not re-SET the first capture's `capturedAt`/`contentHash`
    either), so the repairs for a legacy node are a retry of a prior FAILED
    capture, `session_source_prose_migration.reconcile_session_source_prose`
    (`#11070`/`#11152`, in place) or a full `record-restore` (whose wipe is
    whole-graph) — never a replay that writes. A capture under a NEW `session_id` and
    a record-sourced re-projection are PREVENTION routes: they produce a
    prose-free node where none existed and leave an existing one alone
    (`#10852` review, P3: the earlier cut of this docstring named them as
    repairs, which is the same misdirection the retraction removes). It
    therefore REDS in the good direction too: if someone
    "fixes" the healing by re-SETting on the replay arm, this fails, and the
    replay invariant above is the argument to read first.
    """
    session_id = "sess-source-prose-noheal-10000"
    url = f"session:{session_id}"
    stale = f"{MARKER} stale prose from a pre-fix write"
    proj = sdk._get_proj()
    # The pre-fix state EXACTLY: the prose-carrying Source and the `:Session`
    # node of the completed capture that wrote it. Seeding only the Source — as
    # the stub test above does — is a DIFFERENT state (a first capture, not a
    # replay), and that difference is the whole finding.
    proj.g.query(
        "MERGE (s:Source {url:$u}) SET s.summary=$sum, s._searchText=$st",
        params={"u": url, "sum": stale, "st": stale})
    proj.g.query(
        "MERGE (n:Session {id:$sid}) SET n.capture_ok=true",
        params={"sid": session_id})

    receipt = _capture(sdk, session_id)
    assert receipt["extraction_mode"] == "replayed", (
        "the fixture is not the state under test: a re-capture of a captured "
        f"session must REPLAY, got {receipt['extraction_mode']!r}")

    survivors = [(labels, props) for labels, props, node_id
                 in _nodes_carrying(sdk, f"{MARKER} stale")
                 if not _is_turn_store(labels, node_id, session_id)]
    assert survivors, (
        "the replay HEALED the pre-fix prose. It cannot: "
        "`_materialize_session_source` is only called behind the fresh-capture "
        "gate, so a replay never re-derives the Source. If this arm now clears "
        "it, the replay is WRITING — read #2104/#1727 before keeping that.")
    assert any("summary" in props or "_searchText" in props
               for _, props in survivors), survivors
    # The two derived columns are one derivation and must keep together or
    # replace together (`_source_search_text` ties them). A replay that moved
    # only one would split the display column from the indexed column — the
    # divergence `#10000` exists to remove, arriving by a different door.
    stored = proj.g.query(
        "MATCH (s:Source {url:$u}) RETURN s.summary, s._searchText",
        params={"u": url},
    ).result_set[0]
    assert list(stored) == [stale, stale], (
        "the replay changed ONE of the two derived columns and not the other — "
        f"they must move together or not at all: {stored!r}")


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


def test_a_non_latin_transcript_still_derives_and_is_findable(sdk):
    """The presence test's word class must not be ASCII (#10852 review, P2).

    The token pattern was ``[A-Za-z][A-Za-z0-9_-]*`` — an ASCII word class — so
    a Cyrillic, Greek, Hebrew, Arabic or CJK transcript had no word tokens as
    far as ``_session_source_metadata`` could see and took the ``("", [])``
    arm. Since #10000 BOTH derived columns come from that one token list, so
    such a session's ``:Source`` was written ``summary=''`` and with **no**
    ``_searchText`` at all — unfindable through
    ``tortoise_fts_query(entity_type='source')``, which is the #3518 regression
    the function's own docstring says it exists to prevent.

    ⛔ MEASURED PRE-FIX (`5e9bd35cb`): every shape below derives `('', [])`,
    which is what the first assertion reds on. The fix is the word class, not a
    special case: ``[^\\W\\d_][\\w-]*`` is a Unicode LETTER followed by word
    characters — identical behaviour for ASCII, correct for every other script.
    """
    from tortoise.sdk import _session_source_metadata

    shapes = {
        "russian": "пользователь: мы решили перенести релиз на пятницу",
        "greek": "ο χρήστης: μεταφέραμε την κυκλοφορία",
        "arabic": "المستخدم: قررنا تأجيل الإصدار",
        "chinese": "我们今天讨论迁移计划",
    }
    for script, transcript in shapes.items():
        summary, topics = _session_source_metadata(transcript)
        assert topics, (
            f"a {script} transcript derived NO lexicon — the presence test's "
            "word class is ASCII-only, so this session is written summary='' "
            "with no _searchText and cannot be found (#3518)")
        assert summary == DISPLAY_PREFIX + ", ".join(topics), (summary, topics)
        # ⛔ The framing labels are still filtered, in every script: the
        # stopword/framing sets are ASCII but they are a PRECISION filter, and
        # the russian shape's own `пользователь` is NOT one of them — it is the
        # word the session used, so it belongs in the lexicon.
        assert not ({"user", "assistant", "speaker"} & set(topics)), topics

    # ⛔ NEGATIVE CONTROL — the widening stops at "letters are present". A word
    # class that matched anything (``\\S+``, say) would give the punctuation and
    # framing-only shapes a "lexicon" built from ``"..."`` / ``"user"``, which
    # is the same defect swapped end for end. These are unchanged by the fix and
    # must stay silent.
    for wordless in ("", "   ", "...", "?!", "user:\nassistant:"):
        assert _session_source_metadata(wordless) == ("", []), wordless

    # ── the live leg: the derived pair actually reaches the node and the index
    cyrillic_turns = [
        {"role": "user",
         "content": "пользователь: перенеси релиз на пятницу"},
        {"role": "assistant", "content": "договорились, пятницу"},
    ]
    sdk._capture_session(conversation=cyrillic_turns,
                        session_id="sess-cyrillic-10000")
    row = _source_rows(sdk)["session:sess-cyrillic-10000"]
    assert row[1], (
        "the Cyrillic session's :Source got no _searchText, so it is unfindable "
        f"through the Source FTS index: {row!r}")
    assert row[0].startswith(DISPLAY_PREFIX), row
    assert row[1] in row[0], (
        "the indexed value is not the lexicon the display value carries — the "
        f"two derived columns split: {row!r}")
    assert "пятниц" in row[1], row

    found = [r["id"] for r in sdk.tortoise_fts_query("пятницу",
                                                     entity_type="source")]
    assert "session:sess-cyrillic-10000" in found, (
        f"the Cyrillic session is not findable by a word it used: {row!r} -> {found}")


def test_a_digit_only_transcript_still_derives_and_is_findable(sdk):
    """A transcript whose only tokens are NUMBERS must still derive a lexicon.

    ⛔ MEASURED PRE-FIX at this branch's head: ``("", [])`` for every shape
    below, which is what the first assertion reds on. The presence test was
    ``[^\\W\\d_][\\w-]*`` — a Unicode LETTER — so a transcript whose only
    non-framing tokens are digits had "no word tokens", took the ``("", [])"
    arm, and this writer (which never passes ``summary`` to
    ``_source_search_text``) wrote ``summary=''`` and ``_searchText`` NULL. The
    session is then absent from the Source FTS index: the #3518 unfindability
    the function's own docstring says it exists to prevent, for the one shape
    an ID/number-only session has.

    Against ``main`` this is a REGRESSION, not only a doc inaccuracy: the
    pre-#10000 derivation fell back to ``transcript.strip()[:200]``, so those
    digits DID reach the index (as prose, which is the §9.1/D1 violation this
    change removes — but the findability was real). RediSearch tokenises on
    whitespace and punctuation, so a numeric run is an ordinary index term.

    The fix is the presence class, not a special case: ``[^\\W_][\\w-]*``
    (any word-character run that is not underscore-only, letter OR digit) for
    PRESENCE, with the letter class kept for the CONTENT/ranking arm — so a
    numeric token keeps the session findable through the fallback without
    displacing a word from the six-token descriptor.
    """
    from tortoise.sdk import _session_source_metadata

    # ── the derivation: presence is about tokens, not about letters
    summary, topics = _session_source_metadata("User: 42\nAssistant: 100 200")
    assert topics, (
        "a transcript whose only tokens are DIGITS derived no lexicon — the "
        "presence test's word class excludes numbers, so this session is "
        "written summary='' with no _searchText and cannot be found (#3518)")
    assert summary == DISPLAY_PREFIX + ", ".join(topics), (summary, topics)
    assert {"42", "100", "200"} <= set(topics), topics
    # …and the framing labels are still gone from a digit-only transcript.
    assert not ({"user", "assistant"} & set(topics)), topics

    # ⛔ THE REACHABLE TRANSCRIPT SHAPE — the live leg below depends on it, so it
    # is pinned here rather than discovered by a failure. `_session_llm_transcript`
    # drops a turn whose marker-free body carries no >=3-char extractable
    # sentence, so the bare `42` turn in the capture below never reaches this
    # function: the transcript the writer actually derives from is the second
    # turn alone. Asserted directly so a future change to that upstream predicate
    # shows up HERE rather than silently moving the live leg's coverage onto a
    # shape the builder can no longer emit.
    reachable = _session_source_metadata("Assistant: 100 200")
    assert reachable[1] == ["100", "200"], reachable
    assert reachable[0] == DISPLAY_PREFIX + "100, 200", reachable

    # ⛔ The CONTENT arm keeps its letter class: numbers do not RANK against the
    # session's words. Without this leg the presence fix could be "solved" by
    # putting digits in the ranking pool, which would let an ID crowd a word
    # out of the descriptor of an ordinary session — the ranking this function's
    # 3-character floor exists to protect.
    _, mixed = _session_source_metadata(
        "user: deployed 3 servers 4 replicas 5 shards")
    assert mixed == ["deployed", "replicas", "servers", "shards"], (
        f"a digit ranked against the session's words: {mixed!r}")

    # ── the live leg: the derived pair reaches the node AND the index
    sdk._capture_session(
        conversation=[{"role": "user", "content": "42"},
                      {"role": "assistant", "content": "100 200"}],
        session_id="sess-digits-10000")
    row = _source_rows(sdk)["session:sess-digits-10000"]
    assert row[1], (
        "the digit-only session's :Source got no _searchText, so it is "
        f"unfindable through the Source FTS index: {row!r}")
    assert row[0].startswith(DISPLAY_PREFIX), row
    assert row[1] in row[0], (
        "the indexed value is not the lexicon the display value carries — the "
        f"two derived columns split: {row!r}")

    found = [r["id"] for r in sdk.tortoise_fts_query("100",
                                                    entity_type="source")]
    assert "session:sess-digits-10000" in found, (
        f"the digit-only session is not findable by a number it used: {row!r} -> {found}")

    # ⛔ NEGATIVE CONTROL — widening for digits must NOT reach "anything is a
    # token". A class that admitted punctuation or a bare underscore run would
    # give these shapes a "lexicon", which is the same defect swapped end for
    # end (and would index `"..."` for every punctuation-only session).
    for wordless in ("", "   ", "...", "?!", "___", "---", ": ; ,",
                     "user:\nassistant:", "user: ...\nassistant: ---"):
        assert _session_source_metadata(wordless) == ("", []), (
            f"a transcript with no token of its own ({wordless!r}) derived a "
            "descriptor — the presence test was widened past 'tokens present'")


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
