"""The read whose question is "what are we currently committed to?" (#13557).

WHY THIS MODULE EXISTS
----------------------
The capstone (#13038) measured the failure precisely:

| read | result |
|---|---|
| ``search('current commitments', object)`` | **0 rows** |
| ``search('launch event', object)`` | **1 row** — the plan |

The 12 December launch plan was ``live``, carried ``pipelineStageId=scheduled``
and ``isClosed=false``, and was returned by a query that *named it*. It was
invisible to the query a customer actually asks, because the word "commitment"
does not occur in its text. **A commitment is a KIND, not a phrase**: the model
already declares the commitment-state family (ONTOLOGY §5 — ``strategy``,
``plan``, ``goal``, ``target``) and plans carry a stage, so "what are we
committed to" is answerable by kind + stage without hoping a noun appears.

So this is not a new state, a visibility field or a second vocabulary. It is the
**positive half of the one determinant** the design already fixes (§6), selected
by the kinds the model already declares: the records
``live._terminal_excluded`` keeps as current, narrowed to the commitment kinds,
served with the stage each one stands on.

WHAT IT DOES *NOT* DO
---------------------
- **It does not decide what is current.** The currency predicate is
  ``live._terminal_excluded`` — the SAME expression every reader filters on, in
  the keep direction, with the family's own vocabulary from
  ``search_engine._status_vocab_for`` (the one home for that seam). A word or a
  slot the predicate stops treating as current stops being served here, and
  nothing in this module needs editing. ⛔ The position axis is part of it
  (#13034): a plan sitting in a ``terminal`` slot, or carrying ``isClosed``, has
  ended and is NOT a live commitment — which is what keeps a cancelled plan out
  of this read while the plain text read may still serve it.
- **It does not name the kinds' spellings.** ``COMMITMENT_OBJECT_KINDS`` is the
  UNPREFIXED family name (ONTOLOGY §5); the caller expands each through
  ``sdk._read_filter_kinds`` so a ``core:plan`` row, a pack-subclassed plan kind
  and a bare ``plan`` are one kind to this read, exactly as they are to every
  other kind filter (#11679). A read that hard-coded ``core:plan`` would miss a
  tenant's own spelling; a read that hard-coded ``plan`` would miss the graph's.
- **It does not text-match.** "What are we committed to" is a STATE question, so
  the population is the kind + the stage, never the relevance of a query. The
  edge from the customer's sentence to this population is
  ``tortoise/state_intent.py``, and it is the read path that applies it — a
  caller never has to know this module exists.
- **It does not rank.** The population is served newest-touched first (then by
  id), because "the commitments" is an enumeration, not a top-k. There is no
  relevance score to truncate by, which is why a caller's ``order_by`` /
  ``min_confidence`` / ``threshold`` are not accepted beside it: the read path
  applies a state population only to a call that supplied no retrieval knob
  (``tortoise_fts_query``'s knob contract), and a caller that set one keeps the
  relevance read it asked for.

DRAFTS ARE CURRENT BUT NOT COMMITMENTS (§4)
-------------------------------------------
``draft`` is in ``live.CURRENT_POINT_STATUS_VALUES`` — a draft is not dead — but
design §4 defines it as *"created, not yet a commitment … not counted as in
force"*. So the draft literal is excluded HERE, from
``live.DRAFT_STATUS`` (the declared constant, not a new one): this read is the
one place where the two facts ("current" and "a commitment") differ, and the
exclusion is the design's sentence rather than a deny-list invented here. An
ABSENT status stays in — it is live by the vocabulary's own rule (legacy rows
and the ordinary birth state), and a legacy commitment is still a commitment.
"""
from __future__ import annotations

from .live import DRAFT_STATUS, _terminal_excluded
from .search_engine import _status_vocab_for

#: The commitment-state family of ONTOLOGY §5 — the Object kinds a commitment
#: is. ONE declaration: the read narrows on these, and the kind EXPANSION of
#: each (``core:``-prefixed spellings, pack subclasses) is the caller's, through
#: ``sdk._read_filter_kinds``.
#:
#: ⛔ ``decision`` is deliberately NOT here. A decision is an OBJECT kind in the
#: core vocabulary and a POINT/EVENT kind in the extractor's, and the model does
#: not declare it a commitment-state kind — a pricing decision is a claim about a
#: choice, and the capstone's Q1 already reaches it as a Point. Widening this
#: tuple by one plausible-sounding member is how a "commitments" read becomes
#: "everything", so it takes the ontology's list, not a judgement call here.
COMMITMENT_OBJECT_KINDS: tuple[str, ...] = ("strategy", "plan", "goal", "target")

#: The budget a family-wide commitment scan is given, in ms, when the caller
#: supplied none — the same reasoning as ``ended.ENDED_SCAN_BUDGET_MS``, and the
#: same number: this statement is the same expensive shape (``MATCH … OPTIONAL
#: MATCH … collect() … ORDER BY … LIMIT`` over a predicate no single index
#: covers), so a 500 ms dense-leg budget would degrade the population the read
#: exists for. FalkorDB's own general-query default is 10 000 ms
#: (``ConnectionConfig.query_timeout_ms``), and ``GRAPH.QUERY`` takes its
#: ``TIMEOUT`` in milliseconds and terminates the statement on expiry.
#:
#: ⛔ It is NOT read as a fallback inside ``tortoise_fts_query`` (the read passes
#: its own ``_elevated_timeout_ms`` or ``None``): a second fallback site in the
#: fused read is a second budget binding the #11691 guard has to admit, and the
#: module that owns the number is the right place to default it.
COMMITMENTS_SCAN_BUDGET_MS = 10_000


def _keep_expression(alias: str) -> str:
    """Cypher predicate: the node is CURRENT on either axis (design §6).

    Composed from ``live._terminal_excluded`` with the OBJECT family's
    vocabulary — resolved through ``search_engine._status_vocab_for`` so the
    family seam is not re-stated here — and narrowed to exclude a DRAFT, which
    the design calls *"not yet a commitment"* (§4). Both facts come from their
    declared owners; neither is a hand-written status list.
    """
    excluded, include_flag = _status_vocab_for("Object")
    return (_terminal_excluded(f"{alias}.status", excluded,
                               include_outdated_flag=include_flag)
            + f" AND coalesce({alias}.status, '') <> '{DRAFT_STATUS}'")


def build_cypher(*, narrow_kinds: bool) -> str:
    """The ONE enumeration statement for the commitment population.

    ``narrow_kinds`` adds ``… IN $kinds`` to the WHERE — a parameter of the
    STATEMENT, not of the read path — because ``$kinds`` may not be passed when
    the caller named no kind (a ``None`` parameter cannot be compared with
    ``IN``), exactly as ``ended.build_cypher`` does for the same reason.

    The row carries the record, its KIND, its POSITION (the stage id, the slot,
    the outcome and when it was entered) and the events linked to it — every
    field a caller needs to say *what* is outstanding and *how far along* it is,
    and no field that would be a second vocabulary for either.
    """
    where = _keep_expression("n")
    if narrow_kinds:
        where = f"({where} AND n.objectKind IN $kinds)"
    return (
        "MATCH (n:Object) WHERE " + where + " "
        "OPTIONAL MATCH (e:Event)-[:aboutObject]->(n) "
        "WITH n, collect(DISTINCT e.id) AS linked_events "
        "RETURN n.id, n.name, n.objectKind, n.status, n.goalState, "
        "n.pipelineStageId, n.pipelineSlot, n.pipelineOutcome, "
        "n.pipelineStageSince, n.isClosed, n.updatedAt, linked_events "
        "ORDER BY coalesce(n.updatedAt, '') DESC, n.id "
        "LIMIT $limit"
    )


def _commitment_row(raw: tuple) -> dict:
    """One served row: the record, its kind, its position, and nothing derived.

    The keys mirror ``ended._ended_row`` wherever the two reads describe the same
    thing (``id``/``name``/``status``/``stage``/``isClosed``/``linkedEvents``), so
    a caller can hold both answers without learning two shapes — and there is no
    ``isCommitment``/``live`` token here, because the population this read is
    drawn from IS the answer and a token would be a second name for it.
    """
    (node_id, name, kind, status, goal_state, stage_id, slot, outcome, since,
     is_closed, updated_at, linked_events) = raw
    return {
        "id": node_id,
        "entity_type": "object",
        "objectKind": kind,
        "name": name,
        "status": status,
        "goalState": goal_state,
        "stage": (
            {"id": stage_id, "slot": slot, "outcome": outcome, "since": since}
            if stage_id or slot
            else None
        ),
        "isClosed": is_closed,
        "updatedAt": updated_at,
        "linkedEvents": sorted(
            {i for i in (linked_events or []) if isinstance(i, str) and i}),
    }


def enumerate_live_commitments(graph, *, kinds: list[str], limit: int = 50,
                               timeout_ms: int | None = None) -> list[dict]:
    """Every LIVE commitment, newest-touched first.

    ``graph`` is the live graph handle the read path already holds, so this costs
    no second connection. ``kinds`` is the caller's EXPANDED kind set (the
    commitment kinds of :data:`COMMITMENT_OBJECT_KINDS` unioned through
    ``sdk._read_filter_kinds``, and narrowed by the caller's own ``kind`` when
    one was given) — an unexpanded kind here would miss the ``core:``-prefixed
    and pack-subclassed spellings the caller's own filter matches.

    An EMPTY ``kinds`` is refused rather than answered: ``[]`` from this read is
    the factual claim "nothing is currently committed", and an empty kind set
    means the caller asked for a population this read does not enumerate — the
    two must not read the same. (The read path checks the intersection before
    calling, so a caller that narrowed to a non-commitment kind keeps the
    relevance read instead of being told there are no commitments.)

    ``timeout_ms`` BOUNDS the statement and defaults to
    :data:`COMMITMENTS_SCAN_BUDGET_MS`. As with ``ended.enumerate_ended``, an
    EXCEEDED budget is a store that could not answer: this function raises
    rather than returning ``[]``, because ``[]`` is the claim above. The READ
    PATH — whose answer is a graded status rather than an exception — degrades
    (zero rows, ``incomplete`` truncation, a degraded leg) when the caller has a
    sink that can carry the marking, and re-raises when it has none; that is the
    same contract as every other bounded statement the read issues.

    The read is BOUNDED (``LIMIT``) and ordered by the record's own last touch,
    so a caller gets the most recently changed commitments rather than an
    arbitrary page. It returns exactly what it was asked for: the read path asks
    for one row beyond its page and compares the length before it writes its
    truncation marker, so neither a capped nor a full page can be read as
    "that is all of them".
    """
    if not kinds:
        raise ValueError(
            "enumerate_live_commitments needs at least one committed kind to "
            "enumerate — an empty kind set is a question this read does not "
            f"answer (it answers {', '.join(COMMITMENT_OBJECT_KINDS)}), and "
            "answering it [] would claim nothing is currently committed."
        )
    cypher = build_cypher(narrow_kinds=True)
    params: dict = {"limit": int(limit), "kinds": list(kinds)}
    kwargs = {"timeout": int(COMMITMENTS_SCAN_BUDGET_MS
                             if timeout_ms is None else timeout_ms)}
    rows = graph.query(cypher, params=params, **kwargs).result_set
    return [_commitment_row(tuple(r)) for r in rows if r and isinstance(r[0], str)]


__all__ = [
    "COMMITMENTS_SCAN_BUDGET_MS",
    "COMMITMENT_OBJECT_KINDS",
    "build_cypher",
    "enumerate_live_commitments",
]
