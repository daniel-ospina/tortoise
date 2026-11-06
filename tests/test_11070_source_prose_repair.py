"""#11070 — repair the PRE-#10852 ``:Source`` prose population, and a census
that can actually reach zero.

WHAT IS BROKEN (measured, not reasoned)
=======================================
A capture that ran BEFORE #10852 wrote the transcript's first substantive
utterance into ``Source.summary`` and derived ``_searchText`` from that same
value. #10852 stopped the writer doing it again; it deliberately did not heal
the nodes already holding the pair, and no route reaches them:

* a re-capture is the #1727 no-op replay (the mint gate), pinned in
  ``tests/test_10000_source_prose.py``;
* a record-sourced re-projection DECLINES the pair for an episodic Source
  (``cache_projection._EPISODIC_SOURCE_PROSE_COLUMNS``), and a decline means
  "do not SET", so the existing value stays;
* ``record_reconcile`` — the module the tracker points at — is the COMPARISON.
  Its ``declined = episodic_source_prose_properties()`` marks the pair
  CACHE-OWNED so it lands in ``graph_only`` instead of as a permanent
  ``field_mismatch``. It has no write path to the cache and should not get one.

⛔ THE SECOND HALF OF THIS FILE IS THE CENSUS, AND IT IS NOT BOOKKEEPING.
#11070's own indicator is

    MATCH (s:Source) WHERE s.is_episodic = true
      AND (s.summary <> '' OR s._searchText IS NOT NULL) RETURN count(s)

and that count CANNOT REACH ZERO on a healthy graph, for two independent
reasons, each with a leg below:

1. **``is_episodic`` is not the session-Source identity.** The capture commit
   stamps it on every EXTERNAL source of ``payload.sources``, and
   ``backfill_is_episodic.py`` stamps it on every Source reachable from a
   session point — so a DOCUMENT Source carries it. A document legitimately has
   ``summary`` and ``_searchText`` (its FTS field is declared on the latter and
   ``backfill_document_search_text`` writes it), so the number is structurally
   pinioned above zero by the document population. The identity is the repo's
   ONE declaration, :func:`tortoise.cache_projection.is_episodic_source_node`
   (two arms, never a flag).
2. **Non-empty is not prose.** A POST-#10852 node carries a topic LEXICON:
   ``summary`` is ``SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics)`` and
   ``_searchText`` is the same lexicon bare. Both are non-empty by design, so
   ``summary <> ''`` counts every healthy node. The WITNESS that separates the
   two derivations is the prefix: a pre-#10000 row's prose carries no prefix
   (``session_source_display``).

A predicate that cannot reach zero cannot verify a fix, so the census in this
module is built from those two declarations and the legs below pin both.
"""

from __future__ import annotations

from tortoise.session_source_display import SESSION_SOURCE_SUMMARY_PREFIX

#: The transcript slice a pre-fix capture left on the node.
PROSE = "We decided the weekly rebuild happens on Fridays."


# ── fakes ──────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _RecordingGraph:
    """A graph that RECORDS every write, so ``dry_run`` can be proven.

    Reads answer from ``rows``; a write returns one node. Every statement (and
    its params) is kept in ``statements`` so a test can assert that a dry run
    issued none, and that a repair wrote the values it claims.
    """

    WRITE_MARKERS = ("SET ", "REMOVE ", "DELETE ", "MERGE ")

    #: The columns the census read projects (it must NOT pull `embedding`, so the
    #: graph is asked for these by name and the fake answers in the same order).
    COLUMNS = ("url", "sourceKind", "summary", "_searchText", "topics", "sessionId")

    def __init__(self, rows):
        self._rows = rows
        self.statements: list[tuple[str, dict]] = []

    def query(self, cypher, params=None, timeout=None):
        if any(m in cypher for m in self.WRITE_MARKERS):
            self.statements.append((cypher, dict(params or {})))
            return _Result([("session:whatever",)])
        return _Result([tuple(props.get(c) for c in self.COLUMNS)
                        for (props,) in self._rows])

    @property
    def writes(self):
        return [s for s in self.statements]


def _session_node(**overrides):
    props = {
        "url": "session:s-11070",
        "sourceKind": "agentSession",
        "sessionId": "s-11070",
        "is_episodic": True,
        "summary": PROSE,
        "_searchText": PROSE,
    }
    props.update(overrides)
    return props


def _document_node(**overrides):
    """A DOCUMENT Source that ``is_episodic`` misclassifies as a session Source."""
    props = {
        "url": "https://example.com/q3-pricing",
        "sourceKind": "document",
        "is_episodic": True,
        "summary": "Q3 pricing changes",
        "_searchText": "Q3 pricing changes",
    }
    props.update(overrides)
    return props


def _post_fix_node(**overrides):
    topics = ["migration", "plan", "friday"]
    props = {
        "url": "session:s-post",
        "sourceKind": "agentSession",
        "sessionId": "s-post",
        "is_episodic": True,
        "summary": SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics),
        "_searchText": ", ".join(topics),
    }
    props.update(overrides)
    return props


# ── the census: it must be able to reach 0 ─────────────────────────────────


def test_the_census_finds_a_session_source_carrying_pre_fix_prose():
    from tortoise import session_source_prose_migration as m

    found = m.find_session_sources_carrying_prose(_RecordingGraph([(_session_node(),)]))
    assert [n["url"] for n in found] == ["session:s-11070"]
    assert found[0]["summary_bytes"] == len(PROSE)
    assert found[0]["search_text_bytes"] == len(PROSE)


def test_a_document_source_is_not_in_the_census_even_with_is_episodic_true():
    """Leg 1: ``is_episodic`` is not the identity — the tracker's predicate is wrong.

    This node is a healthy document. Counting it pins the census above zero
    forever, which is exactly what makes a fixed graph look unfixed.
    """
    from tortoise import session_source_prose_migration as m

    assert m.find_session_sources_carrying_prose(_RecordingGraph([(_document_node(),)])) == []
    assert m.census(_RecordingGraph([(_document_node(),)])) == 0


def test_a_post_fix_lexicon_source_is_not_in_the_census():
    """Leg 2: non-empty is not prose — the prefix is the witness.

    A post-#10852 node carries a non-empty ``summary`` and ``_searchText`` by
    design. ``summary <> ''`` counts it; the prefix test does not.
    """
    from tortoise import session_source_prose_migration as m

    assert m.find_session_sources_carrying_prose(_RecordingGraph([(_post_fix_node(),)])) == []


def test_the_census_reaches_zero_on_a_graph_holding_only_healthy_sources():
    """The whole point: 0 is REACHABLE, so 0 is evidence."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([
        (_post_fix_node(),),
        (_document_node(),),
        (_document_node(url="corpus://c/transcript.md", sourceKind="agentSession"),),
        ({"url": "session:s-stub", "sourceKind": "agentSession", "summary": "",
          "_searchText": ""},),
    ])
    assert m.census(graph) == 0


# ── the repair ─────────────────────────────────────────────────────────────


def _reader(transcript):
    """A record reader that answers with ``transcript`` for any session."""
    def read(session_id):
        return transcript
    return read


def test_the_repair_re_derives_the_lexicon_from_the_RECORD():
    """The constraint: derive from the record, never from the node's own prose.

    The repair must not read ``summary`` off the node and call that a repair —
    the narrative's authoritative copy is the record.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph,
        read_transcript=_reader("User: migrate the friday rebuild plan\n"
                                "Assistant: the plan is the migration"),
        dry_run=False,
    )

    assert report["repaired"] == ["session:s-11070"]
    assert report["no_record"] == []
    assert len(graph.writes) == 1
    params = graph.writes[0][1]
    assert params["sum"].startswith(SESSION_SOURCE_SUMMARY_PREFIX)
    assert PROSE not in params["sum"], "the prose survived into the re-derived summary"
    assert PROSE not in params["st"]
    assert params["st"], "the fixture transcript must derive a lexicon"
    assert not params["st"].startswith(SESSION_SOURCE_SUMMARY_PREFIX), (
        "the indexed column must be the BARE lexicon — the display prefix in "
        "_searchText makes every session match a query for 'session'/'capture'")
    assert params["st"] == params["sum"][len(SESSION_SOURCE_SUMMARY_PREFIX):], (
        "the display column and the indexed column must be one datum")


def test_the_clear_arm_leaves_topics_alone():
    """`topics` is NOT the prose datum — the pre-fix writer derived it separately.

    The pre-#10000 capture took `topics` from `Counter.most_common(6)` (word
    frequencies) while `summary` was the first utterance, so the two were
    independent on this very population. Clearing it would destroy the only
    non-prose descriptor the node has, and would take a third field on a route
    whose declared bound is the pair.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([
        (_session_node(topics=["rebuild", "friday", "weekly"]),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=False)

    statement, params = graph.writes[0]
    assert "topics" not in statement, (
        "the clear-only arm wrote `topics` — it is not part of the pair")
    assert "topics" not in params


def test_neither_arm_writes_topics():
    """`topics` is NOT the pair, and `record-reconcile` compares it EXACTLY.

    The pre-fix writer took `topics` from `Counter.most_common(6)` while `summary`
    was the first utterance, so the record's own `topics` column is an independent
    value that the reconciler compares field-by-field (it is a projected property,
    NOT one of the declined pair). Writing a re-derived `topics` here would make
    `record-reconcile` permanently divergent on exactly the population this repair
    heals — the "signal indistinguishable from noise" class, one door over.
    """
    from tortoise import session_source_prose_migration as m

    for transcript in (None, "User: friday migration plan"):
        graph = _RecordingGraph([(_session_node(),)])
        m.reconcile_session_source_prose(
            graph, read_transcript=_reader(transcript), dry_run=False)
        statement, params = graph.writes[0]
        assert "topics" not in statement, f"wrote `topics` (transcript={transcript!r})"
        assert "topics" not in params


def test_a_record_restored_node_is_not_a_candidate():
    """P0 (#11070 review): the recovery door's OWN node shape must not be prose.

    `node_from_row` DECLINES `summary` for an episodic Source while projecting the
    lexicon `_searchText` (#10965), so a `record-restore`d graph holds exactly
    `{_searchText: <lexicon>, topics: [...]}` with NO `summary`. Keying the
    blank-summary arm on emptiness alone made every restored session a candidate,
    so `--repair` cleared the whole tenant's Source index — the same unreachable-
    zero defect the tracker's own predicate had, one door over.
    """
    from tortoise import session_source_prose_migration as m

    topics = ["migration", "plan", "friday"]
    restored = {
        "url": "session:s-post", "sourceKind": "agentSession", "sessionId": "s-post",
        "_searchText": ", ".join(topics), "topics": topics,
    }
    assert m.carries_pre_lexicon_prose(restored) is False
    graph = _RecordingGraph([(restored,)])
    report = m.reconcile_session_source_prose(graph, read_transcript=_reader(None))
    assert report["candidates"] == []
    assert graph.writes == []


def test_a_restored_node_whose_topics_do_not_prove_the_lexicon_IS_a_candidate():
    """The proof is EXACT equality, and its polarity is fail-toward-candidate.

    `record-restore` always projects the pair together, so a blank `summary` with
    a `_searchText` its `topics` do not explain is the blanked-summary pre-fix
    hazard — prose with no prefix to witness it — and must be repaired.
    """
    from tortoise import session_source_prose_migration as m

    blanked = _session_node(
        summary="", _searchText=PROSE, topics=["rebuild", "friday", "weekly"])
    assert m.carries_pre_lexicon_prose(blanked) is True


def test_record_turn_reader_maps_the_records_three_answers(monkeypatch):
    """Fix pins the PRODUCTION reader, not just an injected lambda.

    The injected-lambda leg below passed while `record_turn_reader` collapsed
    `TurnAnswer.EMPTY` (an answer) into `None` (no answer) — so the `empty_sessions`
    bucket was dead in the only path that runs in production.
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    def fake(sid):
        return {
            "complete": record_read.TurnAnswer(
                answered=True, turns=(_turn("User: hi"),), turn_count=1,
                kind=record_read.TurnAnswer.COMPLETE),
            "empty": record_read.TurnAnswer(
                answered=True, turns=(), turn_count=0,
                kind=record_read.TurnAnswer.EMPTY),
            "none": record_read.TurnAnswer(
                answered=False, turns=(), turn_count=None,
                kind=record_read.TurnAnswer.NO_ACCOUNT),
            "partial": record_read.TurnAnswer(
                answered=False, turns=(_turn("User: hi"),), turn_count=9,
                kind=record_read.TurnAnswer.PARTIAL),
        }[sid]

    monkeypatch.setattr(record_read, "session_turns", lambda *a, **k: fake(k["session_id"]))
    read = m.record_turn_reader(object(), org_id="o", graph_id="g")

    assert read("complete") == "User: hi"
    assert read("empty") == "", "EMPTY is an ANSWER (record spoke, 0 turns)"
    assert read("none") is None
    assert read("partial") is None, (
        "a PARTIAL set must not build a lexicon from half a transcript")


class _Line:
    def __init__(self, line):
        self.line = line


def _turn(line):
    return _Line(line)


def test_a_record_that_spoke_and_states_zero_turns_is_not_reported_as_no_record():
    """`TurnAnswer.EMPTY` is an ANSWER; `NO_ACCOUNT` is not, and they differ.

    Collapsing them sent an operator hunting a DSN or row-level-security problem
    for a session whose record had spoken.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(""), dry_run=False)

    assert report["empty_sessions"] == ["session:s-11070"]
    assert report["no_record"] == []
    assert len(graph.writes) == 1  # the prose still goes (§9.1/D1)


def test_the_repair_clears_the_pair_when_the_record_does_not_answer():
    """No record → no invented lexicon, and the §9.1 prose still goes.

    Clearing is not a new state: the writer itself produces ``summary=''`` and a
    bare ``_searchText`` for a text-less capture, so the repair does not mint a
    shape no writer reaches.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=False)

    assert report["repaired"] == []
    assert report["no_record"] == ["session:s-11070"]
    assert len(graph.writes) == 1
    params = graph.writes[0][1]
    assert params["sum"] == ""
    # The writer's text-less shape: `summary=''` and NO `_searchText` (its SET is
    # `coalesce($st, s._searchText)` with `$st` None, and FalkorDB drops a null
    # property). `''` would be a state no writer reaches.
    assert params["st"] is None


def test_the_repair_is_dry_by_default_and_writes_nothing():
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"))

    assert report["dry_run"] is True
    assert report["candidates"] == ["session:s-11070"]
    assert graph.writes == [], "dry_run wrote to the graph"


def test_dry_run_reports_the_same_candidate_set_as_a_real_run():
    """`dry_run=True` must return the SAME report shape (#4125's contract)."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    plan = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=True)
    done = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)

    assert plan["candidates"] == done["candidates"]
    assert set(plan) == set(done)


def test_the_candidate_read_is_never_conflated_with_a_healthy_node():
    """A repair that re-derived over a good node would rewrite it every run."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_post_fix_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: anything"))
    assert report["candidates"] == []
    assert graph.writes == []


def test_a_second_run_finds_nothing_left_to_repair():
    """Idempotent: the repair's own output must not be a candidate.

    A reconciler whose output re-enters its input is a rewrite loop, and the
    census would then never reach zero on the repaired graph — the failure this
    whole lane exists to prevent.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)
    params = graph.writes[0][1]

    healed = _session_node(summary=params["sum"], _searchText=params["st"])
    after = _RecordingGraph([(healed,)])
    report = m.reconcile_session_source_prose(
        after, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)

    assert report["candidates"] == [], (
        "the repair's own output is still a candidate — the census can never "
        "reach zero on a repaired graph")
    assert after.writes == []


def test_the_repaired_node_is_still_findable_through_the_source_fts_field():
    """#3518: a repair that only BLANKS the pair un-indexes every healed session."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday migration plan"),
        dry_run=False)
    st = graph.writes[0][1]["st"]
    assert st and "migration" in st, (
        "the repaired node carries no searchable text, so the Source FTS probe "
        "can never find the session again")


class _Proj:
    def __init__(self, graph):
        self.g = graph


def test_the_replay_hook_is_PLAN_ONLY_without_a_record():
    """Cycle-3 P1: the hook must never take the destructive arm unasked.

    A replay engine holds no record handle, and the clear arm is destructive AND
    not self-correcting (its output is no longer a candidate), so clearing here
    would un-index the pre-#10852 population irreversibly and then report
    `census == 0` — a false PASS of the exact kind this issue exists to close.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_after_replay(_Proj(graph), engine="rebuild")

    assert report is not None
    assert report["dry_run"] is True
    assert report["candidates"] == ["session:s-11070"]
    assert graph.writes == [], "the rebuild hook wrote without a record handle"


def test_the_replay_hook_writes_when_it_HAS_the_record():
    """The guard is the RECORD HANDLE, not a blanket refusal to act."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_after_replay(
        _Proj(graph), engine="rebuild",
        read_transcript=_reader("User: friday migration plan"))

    assert len(graph.writes) == 1
    assert graph.writes[0][1]["st"], "with the record, the lexicon is re-derived"


def test_the_replay_hook_is_a_no_op_without_a_graph_handle():
    from tortoise import session_source_prose_migration as m

    class _NoGraph:
        pass

    assert m.reconcile_after_replay(_NoGraph(), engine="rebuild") is None


def test_the_prose_never_reaches_the_re_derived_summary():
    """A one-word-prose session must still be repaired, not skipped.

    ``summary`` prose shorter than a lexicon is the shape an over-eager "is
    this really prose?" heuristic would skip.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(summary="ok", _searchText="ok"),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: ok go"), dry_run=False)
    assert report["candidates"] == ["session:s-11070"]
    healed = graph.writes[0][1]
    assert healed["sum"] != "ok" or healed["st"] != "ok"
