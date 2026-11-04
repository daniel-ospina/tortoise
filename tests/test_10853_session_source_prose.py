"""#10853 — the session `:Source` carries no turn prose, on the RECOVERY door.

The capture writes the transcript's first substantive utterance as the session
`Source`'s `summary`, and derives `_searchText` from that same value (#1352,
`_source_search_text(summary=…)` — the capture path has no title). #10000's
ruling (§9.1/D1) is that captured turn text does not live on a graph node.

⛔ THE DOOR THIS FILE PINS IS NOT THE CAPTURE. Every record-sourced read and
rebuild goes through `cache_projection.node_from_row`: `tortoise record-restore`
and `rebuild_from_record` MATERIALISE a node with it, and
`record_serve.read_node` answers with the same map because its contract
is that a served node and a rebuilt node cannot drift. So a row written by a
build that still placed the key either re-creates the prose on the graph or makes
the served answer disagree with the rebuild — and nothing reds either way.

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
    """A `public.source` row of the SHAPE Postgres returns (every column).

    ⚠️ But NOT in the ORDER it returns them: `sorted()` puts `'props'` BEFORE
    `'source_kind'`/`'title'`/`'url'`, while the DDL puts the tail LAST — see
    :func:`_row_in_select_star_order`, the ordering a real row has.
    """
    row: dict[str, object] = {c: None for c in sorted(rp.table_columns()["source"])}
    row.update(overrides)
    return row


def _row_in_select_star_order(**overrides: object) -> dict[str, object]:
    """A `public.source` row in the order `SELECT *` ACTUALLY yields: tail last.

    `props` is declared last in `CREATE TABLE public.source` (the `props jsonb
    NOT NULL DEFAULT '{}'` line, immediately above the PRIMARY KEY), and Postgres
    numbers ordinary columns in creation order (`pg_attribute.attnum`, *"ordinary
    columns are numbered from 1 up"*), so `SELECT *` iterates the tail LAST —
    the coordinate `_props_from_row` used to write last, i.e. the one that won
    every collision it carried.

    ⛔ This ordering is the whole point of the legs that use it: `_source_row`'s
    `sorted()` keys put `props` first, so a column overwrote the tail and the row
    predicate and the node map AGREED on the one ordering real rows do not have
    (#10867 review ISSUE 1).
    """
    row: dict[str, object] = {
        c: None for c in sorted(rp.table_columns()["source"]) if c != "props"}
    # Inserted LAST — and `update` never moves an existing key, so `props` stays
    # the final coordinate however many overrides follow.
    row["props"] = ""
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


def _every_column_populated_session_row(**overrides: object) -> dict[str, object]:
    """A `public.source` row with **every** column Non-NULL, as the schema allows.

    The drop set is a *set* declaration (`_EPISODIC_SOURCE_PROSE_COLUMNS`), so
    pinning it needs a row in which every projected column is present — a row
    that leaves a column NULL cannot tell "absent because dropped" from "absent
    because NULL" (`projected_columns` are written only when non-None). Values
    are placeholders; only their NON-NULL-ness is load-bearing.
    """
    row: dict[str, object] = {c: f"v-{c}" for c in sorted(rp.table_columns()["source"])}
    for column in ("topics", "url_aliases", "about_entities"):
        row[column] = [f"v-{column}"]
    row.update(
        embedding=[0.125, 0.25],
        is_episodic=True,
        needs_extraction=True,
        credibility_tier=2,
        version=3,
        graph_id="g-10853",
        id="s-10853",
        node_key="s-10853",
        node_label="Source",
        name="s-10853",
        url="session:s-10853",
        source_kind="agentSession",
        # An EMPTY tail: the drop set is what this leg pins, and a non-empty tail
        # would add a property that is not in `projected_properties` at all.
        props="",
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


def test_the_DROP_SET_itself_is_exactly_the_declared_pair():
    """The narrowing is a SET, and a set nobody pins grows silently (#10867).

    ⛔ THE UNGUARDED EXTENSION POINT THIS LEG CLOSES. `_EPISODIC_SOURCE_PROSE_COLUMNS`
    is a frozenset, and `test_the_drop_is_narrow_…` above pins only four
    SURVIVORS (`url`/`sourceKind`/`sessionId`/`topics`) — so WIDENING the set was
    invisible: measured by review at this head, adding `title` (a placed,
    record-held `public.source` column) left all 21 legs green *and* the whole
    record/projection neighbourhood green (443 passed), while the recovery door
    silently stripped a record-held property — the exact silent-loss class this
    change exists to close, on a table the very next lane (#10852) edits.

    So this leg pins the DIFFERENCE between the record's projected property set
    and the node's property map, over a row in which every projected column is
    non-NULL: it must be EXACTLY the declared pair. Both directions are covered:
    widening the set adds a missing name (red), and emptying it makes the pair
    project again (red).
    """
    from tortoise import cache_projection as cp

    node = node_from_row("Source", _every_column_populated_session_row())

    dropped = set(cp.projected_properties("source")) - set(node.props)
    assert dropped == {"summary", "_searchText"}, (
        f"the drop set is {sorted(dropped)} — `_EPISODIC_SOURCE_PROSE_COLUMNS` "
        "must strip the turn-prose PAIR and nothing else; a wider set "
        "silently strips a record-held property off a session Source"
    )


def test_the_drop_set_leg_would_catch_a_widened_set(monkeypatch):
    """Non-vacuity: WIDENING the declared set must red the leg above.

    The mutation is applied the way a future lane would apply it — a wider
    frozenset — rather than asserted, because "the leg pins the set" is exactly
    the claim that is false when the leg compares against the set itself.
    """
    from tortoise import cache_projection as cp

    monkeypatch.setattr(
        cp, "_EPISODIC_SOURCE_PROSE_COLUMNS",
        cp._EPISODIC_SOURCE_PROSE_COLUMNS | {"title"})
    monkeypatch.setattr(
        cp, "episodic_source_prose_properties",
        lambda: frozenset(cp.canonical_property("source", c)
                          for c in cp._EPISODIC_SOURCE_PROSE_COLUMNS))

    node = node_from_row("Source", _every_column_populated_session_row())
    dropped = set(cp.projected_properties("source")) - set(node.props)
    assert dropped != {"summary", "_searchText"}, (
        "a widened `_EPISODIC_SOURCE_PROSE_COLUMNS` did not change the drop set "
        "— the pin above would stay green while the guard strips `title`"
    )
    assert "title" in dropped


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

    At this head the capture STILL writes and journals the prose, so the
    reconciler's record side (built through `node_from_row`) omits it while the
    cache holds it — for EVERY session this build captures, not only legacy
    rows. Left as a `field_mismatch` that is permanent non-clean on healthy
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


# ── THE IDENTITY CAN MOVE TOO, and the guard must follow it there ──────────


def test_the_identity_is_read_through_the_props_tail():
    """A tail-carried `sourceKind` makes an EPISODIC node, so the guard must fire.

    `props` is written to the node VERBATIM, so this row produces a node whose
    `sourceKind` says `agentSession` while its `source_kind` COLUMN is NULL. A
    predicate reading the columns alone answered "document", the column door
    never fired, and the prose landed on the node — while
    `is_episodic_source_node` (reading the merged map) called that same node a
    session and the reconciler therefore counted the pair as CACHE-OWNED. R1
    ("from any row shape") failed open, and nothing reddened (#10867 review).
    """
    row = _source_row(
        url="session.md",
        id="s-10853",
        name="s-10853",
        source_kind=None,
        summary=UTTERANCE,
        _search_text=UTTERANCE,
        props='{"sourceKind": "agentSession", "summary": "TAIL PROSE"}',
    )
    before = dict(row)
    node = node_from_row("Source", row)

    assert "summary" not in node.props, (
        "a tail-carried `sourceKind` classified the node as a session but the "
        f"prose reached it anyway: {node.props.get('summary')!r}"
    )
    assert "_searchText" not in node.props
    assert node.props["sourceKind"] == "agentSession", (
        "the identity the tail carries must still project — this leg must not be "
        "satisfiable by projecting nothing"
    )
    assert row == before, "the projection MUTATED the record row"


def test_a_tail_carried_kind_does_not_strip_a_DOCUMENT():
    """The separator arm still applies through the tail — the false positive is worse.

    `file_indexer` stamps `agentSession` on per-FILE `corpus://` permalinks, so a
    tail-carried kind alone must never be a signal. This is the leg that reds if
    the tail read is implemented as "the kind is present" instead of "the kind
    is present AND the url names a session".
    """
    row = _document_row(
        source_kind=None,
        props='{"sourceKind": "agentSession", "url": "corpus://t/weekly.md"}',
    )
    _assert_kept(node_from_row("Source", row),
                 "a `corpus://` permalink in the tail was read as episodic")


def test_the_row_and_node_predicates_agree_when_the_tail_carries_the_identity():
    """The shape that made them disagree: the predicate must judge the node's map.

    The reconciler reads the NODE (`is_episodic_source_node`, merged properties)
    while the projector read the ROW. Any row whose identity lives in the tail
    put those two functions on opposite answers — a permanently suppressed
    divergence on one side or a stripped document on the other.

    ⚠️ ONLY THE KIND CAN TRAVEL: `url` is the label's CONTRACT KEY, so
    `node_from_row` refuses a row whose `url` column is NULL
    (`UnprojectableRecord` — the schema's own CHECK would refuse it too). A
    `url` in the tail therefore never decides the arm, and the COLUMN wins where
    the two carry the same coordinate — which is what the third case pins.
    """
    from tortoise import cache_projection as cp

    # (column url, props tail, expected). The COLUMN url is the contract key and
    # always wins the coordinate, so the corpus/https rows stay documents even
    # when the tail claims the session kind — that is the false positive the
    # separator arm exists to prevent.
    for column_url, props_tail, expected in (
        ("session.md", '{"sourceKind": "agentSession"}', True),
        ("session.md", '{"sourceKind": "agentSession", "url": "session:s-1"}', True),
        ("https://example.com/q3", '{"sourceKind": "agentSession"}', False),
        ("https://example.com/q3", '{"sourceKind": "document"}', False),
        ("session:s-10853", "{}", True),
    ):
        row = _source_row(url=column_url, source_kind=None, props=props_tail)
        node = node_from_row("Source", row)
        assert cp.is_episodic_source_node(node.props) is expected, (column_url, props_tail)
        assert cp._is_episodic_source_row(row) is expected, (
            f"the row predicate disagreed with the node predicate on "
            f"{column_url!r} + {props_tail}"
        )


def test_only_a_NULL_column_is_silence_an_empty_url_is_a_VALUE():
    """The collision rule is the same in BOTH readings, down to the falsy edge.

    `_props_from_row` writes a column whenever it is not `None` (`if value is not
    None`), so an EMPTY-STRING `url` still lands on the node and shadows a
    tail-carried `url`; `_row_identity_view` filled from the tail on any FALSY
    column (`not merged.get("url")`), which made the two disagree on exactly
    that shape — the same class of drift as the `sourceKind` collision, one edge
    over, and the reason the rule is now stated once and applied twice.

    Unreachable as a node identity — the key column is refused when blank
    (`UnprojectableRecord`, and the schema's own CHECK) — and pinned anyway:
    an unused difference between the two readings is how the reachable one came
    back.
    """
    from tortoise import cache_projection as cp

    row = _source_row(url="", source_kind=None, props='{"url": "session:s-1"}')
    view = cp._row_identity_view(row, cp._decoded_tail(row["props"]))

    assert view["url"] == "", (
        "the identity view filled an EMPTY column from the tail, which the node "
        "map does not do — the two readings of the same row disagreed"
    )


def test_a_column_value_wins_the_tail_on_a_real_SELECT_star_row():
    """ISSUE 1 (#10867 review): on the real row ordering the TAIL won the identity.

    A row whose `source_kind` COLUMN says `document` and whose `props` tail says
    `agentSession` is the one shape where the two readings of the same
    coordinate disagree. `_row_identity_view` declares the rule — *"a COLUMN
    VALUE WINS over the tail's spelling of the same coordinate"* — but
    `_props_from_row` never applied it: it wrote the tail with `out.update(...)`
    wherever the `props` column happened to sit, so the winner was the row's
    MAPPING ORDER. `props` is declared LAST in the record DDL, so on a real row
    the TAIL won while the row predicate read the COLUMN: the node carried
    `sourceKind='agentSession'` — `is_episodic_source_node` calls that a session
    — WITH the turn prose on it, so R1 (*"from any row shape"*) failed open and
    `scan_cache_view` then subtracted the pair from `placed`, reporting a real
    divergence as `graph_only`.

    The three existing agreement legs could not see it: they build `_source_row`,
    whose `sorted()` keys put `props` first, i.e. the one ordering in which the
    column wins the node map too.
    """
    from tortoise import cache_projection as cp

    row = _row_in_select_star_order(
        url="session.md",
        id="session.md",
        name="session.md",
        source_kind="document",
        summary=UTTERANCE,
        _search_text=UTTERANCE,
        props='{"sourceKind": "agentSession"}',
    )
    node = node_from_row("Source", row)

    assert node.props["sourceKind"] == "document", (
        "the `props` tail overwrote the record's own `source_kind` COLUMN — a "
        "column-backed property's authority is the COLUMN (the writer routes it "
        "there, `record_projection._split_properties`), and `_row_identity_view` "
        "already declares that it wins this coordinate"
    )
    assert cp.is_episodic_source_node(node.props) is False, (
        "the node is classified a DOCUMENT by the column, so nothing may strip "
        "its own metadata"
    )
    assert cp._is_episodic_source_row(row) is False
    assert (cp.is_episodic_source_node(node.props)
            == cp._is_episodic_source_row(row)), (
        "the row predicate and the node predicate answered differently about "
        "the SAME row — that disagreement is what let the prose through while "
        "the reconciler suppressed the divergence"
    )
    # The AMBIGUOUS row is read as a DOCUMENT (the declared asymmetry), so it
    # keeps its prose. Pinned so a future change cannot "fix" the collision the
    # other way and start stripping a document's own metadata.
    assert node.props.get("summary") == UTTERANCE
    assert node.props.get("_searchText") == UTTERANCE


def test_the_projection_does_not_depend_on_the_row_mapping_order():
    """The same record must project the SAME map whichever way the row arrives.

    Pre-fix a column/tail collision was decided by the row's mapping order, so
    `SELECT *` (tail last) and a `sorted()` fixture (tail early) built different
    nodes out of identical data. Pinned on a NON-identity column too (`title`),
    so a fix narrowed to `sourceKind` cannot pass: the rule is about the whole
    map, not about the one coordinate a review happened to name.
    """
    from tortoise import cache_projection as cp

    overrides: dict[str, object] = {
        "url": "session.md",
        "id": "session.md",
        "name": "session.md",
        "source_kind": "document",
        "title": "the COLUMN's title",
        "summary": UTTERANCE,
        "_search_text": UTTERANCE,
        "props": '{"sourceKind": "agentSession", "title": "the TAIL title"}',
    }
    fixture_order = node_from_row("Source", _source_row(**overrides))
    select_star = node_from_row("Source", _row_in_select_star_order(**overrides))

    assert select_star.props == fixture_order.props, (
        "the projection's result depends on the row's MAPPING ORDER: the same "
        "record yields two different node maps"
    )
    assert select_star.props["title"] == "the COLUMN's title"
    assert select_star.props["sourceKind"] == "document"
    assert (cp.is_episodic_source_node(select_star.props)
            == cp._is_episodic_source_row(_row_in_select_star_order(**overrides))
            is False)


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


def test_a_MALFORMED_props_tail_is_REFUSED_not_dropped_in_silence():
    """A non-object `props` tail must not vanish from the projection (#10867 P3).

    `public.source.props` is `jsonb NOT NULL DEFAULT '{}'` with **no CHECK that it
    is an object**, so a corrupt row can carry a scalar or an array. The identity
    read must tolerate that (it answers for EVERY shape a row can hold), but the
    WRITER path must not: `_decoded_tail` answers `{}` for a non-mapping, and
    seeding `out` from that dropped the tail's whole content with no signal —
    the silent loss this module refuses by name everywhere else. Before the tail
    was seeded, the identical row raised `TypeError` out of `dict(tail)`; this
    leg pins the loudness, in the module's own error type.

    Both shapes are exercised because both are reachable: an ARRAY
    (`jsonb`'s other collection form) and a bare SCALAR. ⛔ AND IN BOTH
    SPELLINGS, because a row can carry the tail either as JSON TEXT (`'[]'` —
    a `::text` cast, a pre-serialised tail, or a fixture) or already DECODED
    (`[]` — what `psycopg2`'s `jsonb` typecaster answers) — so a refusal keyed
    on falsiness refused `'[]'` and projected `[]`, i.e. the same corrupt row
    aborted `record-restore` when it arrived as TEXT and projected cleanly when
    it arrived decoded (#10867 review, second pass). The ABSENT test is identity
    against `None`/`''`, never truthiness.
    """
    from tortoise.cache_projection import CacheProjectionError

    malformed = (
        ("[1, 2]", "an array (text)"),
        ([1, 2], "an array (decoded)"),
        ('"just a string"', "a scalar (text)"),
        (7, "a scalar (decoded)"),
        # FALSY non-objects: `not raw` would have coerced every one of these.
        ("[]", "an EMPTY array (text)"),
        ([], "an EMPTY array (decoded)"),
        ("0", "zero (text)"),
        (0, "zero (decoded)"),
        (False, "False (decoded)"),
    )
    for payload, what in malformed:
        row = _captured_session_row()
        row["props"] = payload
        with pytest.raises(CacheProjectionError) as caught:
            node_from_row("Source", row)
        assert ".props is a" in str(caught.value) and "not a JSON object" in str(
            caught.value
        ), (
            f"a `props` tail holding {what} must be REFUSED by name — it was "
            f"dropped in silence instead: {caught.value}"
        )

    # The LENIENT shapes must stay lenient, and the set of them is EXACTLY the
    # two spellings of ABSENT: `None` (the column's own silence) and the empty
    # string (`''`, plus the schema default `'{}'` and its decoded form).
    for empty in (None, "", "{}", {}):
        row = _captured_session_row()
        row["props"] = empty
        node = node_from_row("Source", row)
        assert "summary" not in node.props and "_searchText" not in node.props, (
            f"an empty `props` tail ({empty!r}) is not a malformed one — it must "
            "project normally, with the prose still declined")

    # A STRING tail is decoded, so a syntactically broken one raises
    # `JSONDecodeError` (a `ValueError`) rather than projecting as `{}`.
    row = _captured_session_row()
    row["props"] = "{not json"
    with pytest.raises(ValueError):
        node_from_row("Source", row)

    # ⛔ THE DECLARED RESIDUAL, PINNED SO IT CANNOT BE READ AS PARITY (#10867
    # review, closing pass). PostgreSQL's `jsonb` maps JSON `null` and JSON `""`
    # onto the SAME Python values as the two spellings of ABSENT, so a DECODED
    # tail cannot be told apart from an absent one: it projects while the
    # identical row as TEXT is refused. Not closable at the
    # absence test — the decoded values ARE the lenient spellings — and it carries
    # NO content (neither has keys), so it is declared and pinned, not fixed.
    for text_form in ("null", '""'):
        row = _captured_session_row()
        row["props"] = text_form
        with pytest.raises(CacheProjectionError):
            node_from_row("Source", row)


def test_the_row_predicate_ANSWERS_on_a_decoded_json_STRING_tail():
    """The predicate must never raise on a shape a driver can hand back (#10867
    review, closing pass).

    `_decoded_tail` answers for EVERY shape a row can hold — that is its whole
    contract, because `_is_episodic_source_row` is called before anything decides
    whether to project. But it routed every `str` through `json.loads`, which is
    right for a JSON-TEXT tail (a `jsonb` string arrives as its JSON SOURCE,
    `'"hello"'`) and wrong for a DECODED one (a decoding read hands back the
    Python `str` `'hello'`, which is not JSON text): the predicate raised
    `JSONDecodeError` on the decoded shape and answered on the JSON-text one, for
    the same row.
    It now answers `{}` — the DOCUMENT arm, this module's declared safe
    asymmetry. The WRITER path is untouched and stays LOUD, which the same test
    pins beside it so a later fix cannot quieten both.
    """
    from tortoise.cache_projection import _decoded_tail, _is_episodic_source_row

    # The DECODED form of `props='"hello"'::jsonb`: not JSON text at all.
    assert _decoded_tail("hello") == {}, (
        "a decoded `jsonb` string tail must answer `{}` — the row predicate must "
        "answer for every shape a row can hold, never raise"
    )
    row = _captured_session_row()
    row["props"] = "hello"
    assert _is_episodic_source_row(row) is True, (
        "the predicate must still ANSWER (the agentSession column arm decides "
        "here) rather than raising out of the identity read"
    )

    # ⛔ The writer path must NOT inherit that leniency: this row would otherwise
    # project with its tail silently dropped, which is the loss the module
    # refuses by name. A ``str`` that is not valid JSON text stays LOUD — as
    # `JSONDecodeError` (a `ValueError`), the same shape the broken-text case
    # above pins, because the writer decodes through `_tail_payload` directly.
    assert row.get("source_kind") == "agentSession"
    with pytest.raises(ValueError):
        node_from_row("Source", row)

    # And the TEXT spelling of the same column (`'"hello"'`, valid JSON text)
    # decodes to a `str` payload, so the predicate answers too — parity where
    # parity is possible.
    assert _decoded_tail('"hello"') == {}


def test_the_TAIL_SHAPE_prose_names_no_driver_this_repo_never_imports():
    """The tail-shape attribution must be MEASURED, not assumed (#10867 P2).

    The second-opinion review found `_tail_payload`'s docstring calling the
    text-vs-decoded split an **asyncpg-vs-psycopg** divergence. Verified false on
    this tree: nothing under `tortoise/` imports asyncpg, and the record layer
    imports `psycopg2` ONLY (`record_writer._default_connect`), whose docs state
    that `json`/`jsonb` "will be automatically converted to Python objects". The
    split is a SHAPE split — JSON TEXT vs already-decoded — and naming a driver
    no code reaches sends the next reader to preserve compatibility for a code
    path that does not exist.

    This leg is the anti-drift pin, and it is why the correction cannot rot back:
    the claim is checkable (`git grep asyncpg -- tortoise/` answered the docstring
    and nothing else), so a future edit that re-introduces the phantom driver
    reds here instead of in a reviewer's read. ⛔ THE SCAN IS SOURCE-ONLY on
    purpose — this file names the driver BECAUSE it refutes it, and that is not
    the claim under test. If asyncpg ever really becomes a driving read here,
    delete this leg in the same change that adds the import; the assertion says
    so, so the failure cannot be mistaken for a typo.
    """
    from pathlib import Path

    import tortoise

    root = Path(tortoise.__file__).resolve().parent
    offenders = sorted(
        f"{path.relative_to(root)}:{lineno}"
        for path in root.rglob("*.py")
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1)
        if "asyncpg" in line
    )
    assert not offenders, (
        "no module under `tortoise/` imports asyncpg, so prose that attributes "
        "the tail-shape split to it names a driver no code reaches — describe "
        "the SHAPE (JSON TEXT vs decoded) instead. If asyncpg is now really a "
        f"read here, drop this leg with the import: {offenders}"
    )


def test_the_identity_view_NEVER_aliases_the_record_row():
    """`_row_identity_view` promises a FRESH mapping on EVERY branch (#10867 P3).

    Its docstring is what R3 leans on to say "the record row is not mutated", and
    the empty-tail branch broke it: `return row` made the "view" the SAME object,
    so a caller writing identity into the view wrote into the record. Latent today
    (`_is_episodic_source_row` short-circuits before calling it with no tail), and
    exactly therefore unpinned — the guarantee has to hold for the caller that
    arrives next, not only for today's.
    """
    from tortoise import cache_projection as cp

    row = _captured_session_row()
    before = dict(row)

    view = cp._row_identity_view(row, {})
    assert view is not row, (
        "`_row_identity_view(row, {})` returned the RECORD ROW itself, so its "
        "own \"the row itself is NEVER mutated\" guarantee is false")
    view["url"] = "session:MUTATED"
    view["source_kind"] = "MUTATED"
    assert dict(row) == before, (
        "mutating the returned identity view mutated the RECORD ROW — the "
        "record is the authority for these values and must not be rewritten by "
        "a projection read")

    # The tail path was already fresh; pin it so a later refactor cannot make the
    # two branches disagree.
    view = cp._row_identity_view(row, {"sourceKind": "agentSession"})
    assert view is not row
    view["source_kind"] = "MUTATED"
    assert dict(row) == before, "the tail branch aliased the record row"
