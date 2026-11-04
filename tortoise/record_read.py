"""The record's READ side for §9.1 text — the narrative and the verbatim turns.

WHY THIS MODULE EXISTS
======================
`docs/architecture/STORAGE-ARCHITECTURE.md` §9.1 (owner ruling, D1) splits the
capture in two: the **narrative** (the connected prose form of a captured
session) and the **raw turn transcript** it was derived from are stored as text
in **Postgres**, never in the graph — *"the narrative is not something we're
suggesting to store in the graph (same as raw) but store in supabase"*. §9.4
makes the link load-bearing rather than a citation nicety: *"at answer time,
send the source's VERBATIM text, not the extracted claim"* (measured 15.9 /
22.0 pts). A claim with no reachable verbatim source cannot be served as
evidence.

`record_projection` is the record's WRITE side (Interface A, journal → Postgres).
This module is the matching READ side: given a tenant and a session (or a
`:Source`), return the narrative and the raw turns **from the record only**.
That is what makes §9.1 checkable — the epic's capstone `#9567` item D7 is
*"the S1 narrative and raw turn text come back"*, measured with the graph
destroyed and the journal unavailable.

WHAT IT READS, AND WHAT IT DELIBERATELY DOES NOT
================================================
* `public.session_narrative` — the narrative home (`#9555`, migration
  20261023000001): the narrative text, its summary, the redaction count and the
  turn count, one row per session.
* `public.point` — the RAW TURNS. They are already first-class record rows
  (`is_episodic = true`, `node_key = '<session>_t<i>'`), each with its own
  identity and journal carrier, so the transcript is SERVED from them and not
  copied into the narrative table: a second copy of the same text can only
  drift from the first, and §9.1 rejects paying twice for the same input one
  level up.

⛔ It never reads `public.journal_event`, and it never reads the graph. The
journal is a reconstruction source only — the durability authority is
`docs/durability-posture.md` — so a rebuild that had to replay it in order to
serve this text would make "the record is the system of record" (§2.0) false
for exactly the text §9.1 says must survive.

TURN IDENTITY, STATED AS A RULE
===============================
A turn's id is `{session_id}_t{index}` — the derivation owned by
`sdk._capture_turn_id` (one definition, `#3551`). Its **reader** half is the
suffix SHAPE: the prefix must match the session id literally and the remainder
must be all digits, which is the same rule `sdk._capture_turn_ids` applies in
Cypher (`t.id STARTS WITH $prefix` plus an all-numeric index). The prefix alone
is not enough: a session whose id itself ends in `_t<i>` would otherwise match
another session's turn ids (`sec` vs `sec_t0`), which is the bug that comment
records.

⚠️ **The STORED text of a turn is FRAMED** — `"[role] body"`
(`sdk._capture_turn_frame`, the single definition) — so the **`transcript` is the
only un-framed, §9.4-usable surface**, and `Turn.content` is the stored framed
form exactly as the record holds it (it is what a reader needs to prove what was
captured). `transcript` un-frames with the writer's own inverse
(`sdk._capture_turn_role_text`, imported lazily so this module stays importable
without the SDK's weight) and labels the line once; `Turn.body` exposes the same
un-framed body for one turn. Serving the framed string as the transcript would
read `"user: [user] hello"`; a second local copy of the frame grammar would be a
second declaration of it, which is why the SDK's inverse is used.

TENANCY IS A PER-CALL ARGUMENT
==============================
`org_id` and `graph_id` are required keyword arguments and are bound into every
statement — the same rule (and the same reason) as `record_projection`: the
hosted lane serves several tenant graphs from one process, so a tenant resolved
from anything process-global files one tenant's read under another with no
error. The record's RLS policies are the boundary underneath (`app.
current_org_id` + the `*_read_org` policy); this module does not rely on them
being armed, it filters on the same pair explicitly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "RecordReadError",
    "SessionText",
    "Turn",
    "TurnAnswer",
    "merge_turn_payloads",
    "served_turn_count",
    "session_text",
    "session_turns",
    "source_narrative",
    "source_session",
    "turn_high_water",
    "turn_keys_at_or_above",
    "turn_payloads",
    "turn_prior_vectors",
]

#: The narrative home (#9555).
NARRATIVE_TABLE = "session_narrative"

#: The turn rows in the record. `node_key` is the turn's canonical key
#: (`Point` → `id`), which is the `{session}_t{i}` id.
POINT_TABLE = "point"


class RecordReadError(Exception):
    """A record read failed structurally — never silently returns empty.

    A missing session is `None` (an honest absence). This class is for the
    cases where "absent" and "broken" must not look alike: a turn id the shape
    rule cannot parse, or a turn stream the record cannot order.
    """


@dataclass(frozen=True)
class Turn:
    """One turn in capture order.

    ``content`` is the **STORED** text — the framed `"[role] body"` string
    (`sdk._capture_turn_frame`), unchanged. Use ``body`` (or ``SessionText.
    transcript``) for un-framed prose: building §9.4's verbatim text out of
    ``content`` would prepend the frame to the line and read
    `"user: [user] hello"`. ``speaker`` is the record's own label, and is
    ``None`` when the capture had none — as is the frame's ``"unknown"``
    sentinel, which is the writer's placeholder and never a speaker.
    """

    turn_id: str
    index: int
    content: str
    speaker: str | None = None
    #: The record's ``created_at`` for the turn, verbatim (a text column). It is
    #: carried so a reader serving from the record can return the SAME payload the
    #: graph read returned — without it, switching the source silently drops the
    #: timestamp from every transcript (#10000).
    created_at: str | None = None

    @property
    def body(self) -> str:
        """The un-framed body of this turn (§9.4's verbatim text for one turn)."""
        _role, body = _role_and_body(self.content)
        return body

    @property
    def line(self) -> str:
        """This turn as ONE labelled line — the transcript's own rendering rule.

        Declared ONCE, here, because two readers need it and a second copy is
        how the writer's frame and its readers drift: the per-turn `body` alone
        loses the speaker, which for a transcript is part of the evidence (a
        claim quoted from the assistant is not a claim the user made).
        """
        role, body = _role_and_body(self.content)
        # `"unknown"` is the inverse's SENTINEL, not a speaker the record knows,
        # and the writer stores it in `point.speaker` too, so it has to be
        # filtered on BOTH sides or every role-less turn is served `unknown: …`.
        speaker = self.speaker
        if speaker == _UNFRAMED_ROLE:
            speaker = None
        label = speaker or (role if role != _UNFRAMED_ROLE else None)
        return f"{label}: {body}" if label else body


@dataclass(frozen=True)
class SessionText:
    """§9.1's two tiers for one session, as the record holds them.

    ``narrative``/``summary`` come from `public.session_narrative`;
    ``turns`` come from the episodic `public.point` rows. ``redactions`` is
    the record's `capture_redactions` column (the column keeps the journal
    key's name, §3). Both are the record's,
    neither is a second copy of the other.
    """

    session_id: str
    narrative: str | None = None
    summary: str | None = None
    redactions: int | None = None
    turn_count: int | None = None
    turns: tuple[Turn, ...] = field(default=())

    @property
    def transcript(self) -> str:
        """The raw turns joined in capture order — §9.1's "raw transcript".

        A turn's STORED text is framed (`"[role] body"`, `sdk._capture_turn_frame`),
        so each line is un-framed with the writer's own inverse and labelled ONCE
        — the frame's role, or `point.speaker` when the record knows one. Serving
        the frame raw would read `"user: [user] hello"`. This is the string
        §9.4's verbatim path reads; it is derived from the turn rows, never
        stored a second time.
        """
        return "\n\n".join(turn.line for turn in self.turns)

    @property
    def verbatim_available(self) -> bool:
        """Is there any §9.4-usable text at all?"""
        return bool(self.narrative or self.summary or self.turns)


@dataclass(frozen=True)
class PriorTurn:
    """What the record already holds for ONE turn id, as a re-capture must see it.

    ⛔ ``created_at``/``status`` ARE HERE BECAUSE THE RECORD'S UPSERT IS ABSOLUTE
    (#10000 review P1). On the graph path a re-capture cannot restamp or demote a
    stored turn: `_TURN_WRITE_CYPHER` sets ``t.status=coalesce(t.status, ...)``
    and ``t.createdAt=coalesce(t.createdAt, $now)``, and the writer journals the
    values the write LEFT because the write hands them back. The record path has
    no node to read back, and `record_projection._upsert_statement` writes
    ``created_at``/``status`` from EXCLUDED outright — so journalling this
    capture's own ``now``/``"draft"`` for a turn the record already holds
    re-stamps its ``created_at`` and resets a promoted turn to ``draft``, on the
    store that IS the system of record, permanently and silently. Reading the
    prior row is how the journal carries what the write leaves, which is the same
    rule the vector half applies below.
    """

    content: str
    embedding: Any = None
    created_at: str | None = None
    status: str | None = None


@dataclass(frozen=True)
class TurnAnswer:
    """This record's answer about ONE session's transcript — and whether it SPOKE.

    ⛔ THE DISTINCTION THIS TYPE EXISTS FOR (#10000 review P0-3): an EMPTY answer
    is only authoritative when the record also holds the capture's own account of
    that session. `_turns` returning `()` is not provenance — it is what an
    unscoped connection, the wrong tenant, a session captured before the record
    was armed, a capture whose record write was refused, and a genuinely empty
    session ALL look like, and a reader that cannot tell them apart serves an
    empty transcript over a store that still holds the text.

    So "the record says there are no turns" and "the record did not answer" are
    different answers, and the second one is not a value: it is the caller's cue
    to read the store that DOES hold them (the graph, until the purge step of
    #10000 removes it). ``answered`` carries that, and ``reason`` names WHICH
    silence it was for the log an operator reads.

    ⛔ AND A **SHORT** ANSWER IS NOT AN ANSWER (#10000 review P0). "Did the record
    speak" and "did it speak for the WHOLE session" are two questions, and only
    the second one licenses serving the record INSTEAD of the other store. The
    writer this module is paired with deliberately produces a SPLIT store: a
    per-turn record write refused after an earlier one landed leaves the landed
    turns in the record and the refused ones in the graph
    (`sdk._write_capture_turns`), and an append on an armed deployment writes the
    new window to the record while the pre-arming turns stay in the graph. Any
    non-empty row set treated as authoritative therefore serves a TRUNCATED
    transcript over a store that holds the rest — the same loss this type exists
    to prevent, one arm over from the empty one.

    ``turns`` is what the record HOLDS. When ``answered`` is True that is the
    whole session's transcript. When it is False the tuple is what the record
    could still contribute (empty for the silent cases, the landed prefix for a
    short answer) and it must be served ONLY IN ADDITION to the other store's
    turns, never instead of them — see :func:`merge_turn_payloads`.

    ⛔ ``kind`` IS NOT PROSE, AND THAT IS WHY IT EXISTS (#10021 review P2). A
    serving door narrates a not-answered answer, and the LEVEL it narrates at
    decides whether an operator can still see a real split store. The shapes
    above are NOT uniform in that respect: ``"no-account"`` is the ordinary
    migrate-phase state — every session captured BEFORE the record was armed
    looks exactly like it, which is the LARGEST population on the deployment
    this transitional read exists to serve — while ``"partial"`` and
    ``"contradiction"`` are the divergences an operator must be able to find.
    A door that warns on all three fills the one line that means "the two
    stores disagree" with healthy traffic, so a real divergence becomes
    indistinguishable from it. ``reason`` stays the human sentence; ``kind`` is
    what a caller may branch on, and the two doors warn only where it is not
    ``"no-account"``.
    """

    #: The whole session's transcript is here.
    COMPLETE = "complete"
    #: The record states 0 turns and holds 0 rows — an authoritative empty answer.
    EMPTY = "empty"
    #: The record spoke and holds only PART of the session's turns (shorter than
    #: its own count, an interior hole, or a tail below the session's mark).
    PARTIAL = "partial"
    #: The record states a count but holds no rows for it — the two stores
    #: disagree outright.
    CONTRADICTION = "contradiction"
    #: The record holds no narrative row and no turn rows: it did not SPEAK.
    #: The pre-arming population and every unscoped/wrong-tenant read look like
    #: this, so it is NOT evidence of a divergence.
    NO_ACCOUNT = "no-account"

    answered: bool
    turns: tuple[Turn, ...] = field(default=())
    turn_count: int | None = None
    reason: str = ""
    #: One of the five constants above. Empty only for a caller that built the
    #: type itself; :func:`session_turns` always sets it.
    kind: str = ""


def session_turns(conn: Any, *, org_id: str, graph_id: str,
                  session_id: str, turn_max_index: int | None = None) -> TurnAnswer:
    """THE turn read for a serving door: scope, read, and say whether it answered.

    ⛔ THE TENANT SCOPE IS INSTALLED HERE, by this function, because a turn read
    on an UNSCOPED connection is a ZERO-ROW read under the record's row-level
    security (`point_read_org` filters on `app.current_org_id`, and a policy
    treats a missing GUC as "no rows", never as an error). That is the #10000
    review P0-1: `record_serve.open_record()` opens and bounds a connection but
    deliberately does not scope it — scoping is per-TENANT, and the opener knows
    no tenant — so a caller that opens one and reads directly gets `()` and
    cannot tell it from an absence. Two serving doors read turns (the hosted
    `GET /v1/sessions/{id}` and `TortoiseSDK.get_session_detail`), so the scope
    lives in the door they share instead of in each of them: a reader cannot
    forget what it does not call.

    ⚠️ The connection must be in a TRANSACTION (the GUC is transaction-local);
    `scope_connection` refuses a connection where the value did not stick rather
    than reading on unscoped (see its docstring).

    ``answered`` is decided by the record's OWN account of the session:
    :func:`_turns` returning rows is an answer only when those rows COVER the
    session's turns — a contiguous run from index 0 long enough for the
    `turn_count` the same row states, AND reaching the id space's end when the
    caller can name it (`turn_max_index`); with no rows, the
    answer is only the record's when `public.session_narrative` holds this
    session's `turn_count` — the column the capture's `SessionRecorded` writes
    (`record_projection._narrative_statements`). A count of 0 says "this session
    has no turns"; a positive count with no rows, or no count row at all, says
    "this record did not answer for these turns" and leaves the decision to the
    caller. A row set that is short of the count, that does not start at
    index 0 (the `turn_offset` append shape), that has a HOLE in the middle (a
    per-turn write refused between two that landed), or that stops BELOW the
    id space's end (a refused HIGHEST turn — the shape `turn_count`
    cannot see, see :func:`_shortfall`) is a PARTIAL answer: it is reported as
    not-answered and carries the rows it does hold, so the caller merges them
    with the other store instead of serving them alone.

    ⛔ THE TAIL NEEDS A WITNESS THIS STORE DOES NOT HOLD (#10000 review P2).
    ``turn_count`` is a WINDOW length, so this record's own row cannot see a
    missing HIGHEST index; the datum is the graph `:Session`'s
    ``turn_max_index`` (inclusive, the session's LIVE id-space end, written by
    the capture MERGE and restored by the fold), which the two serving doors
    already read. It is a CALLER argument rather than a record column because
    this module reads the record only — and ``None`` (a session captured before
    the mark existed) degrades to the checks above rather than inventing one.

    ⛔ IT MUST DESCRIBE THE LIVE STATE, NOT THE PEAK (#10000 review P2). The
    writer stored the larger value while this mark was only the sweep bound, and
    a SHRUNK session (`_t0.._t3` re-captured as `_t0`) then kept a mark of 3, so
    this reader called a COMPLETE record a partial transcript on every read,
    forever — a WARNING and a graph query per read on healthy data, and no way
    to tell a real split-store divergence from it. The write now lowers the mark
    in the capture that sweeps the surplus, so ``max(held) < turn_max_index``
    means what it says: a turn the session still owns has no record row.
    """
    from tortoise.record_projection import scope_connection

    if not session_id:
        raise RecordReadError("session_turns needs a session id")
    scope_connection(conn, org_id)
    row = _narrative_row(conn, org_id, graph_id, session_id)
    turns = _turns(conn, org_id, graph_id, session_id)
    turn_count = row[3] if row is not None and isinstance(row[3], int) else None
    if turns:
        shortfall = _shortfall(turns, turn_count, turn_max_index)
        if shortfall is None:
            return TurnAnswer(answered=True, turns=turns, turn_count=turn_count,
                              reason="turn rows", kind=TurnAnswer.COMPLETE)
        return TurnAnswer(answered=False, turns=turns, turn_count=turn_count,
                          reason=shortfall, kind=TurnAnswer.PARTIAL)
    if turn_count == 0:
        return TurnAnswer(answered=True, turns=(), turn_count=0,
                          reason="the record states 0 turns",
                          kind=TurnAnswer.EMPTY)
    if turn_count is not None:
        return TurnAnswer(
            answered=False, turn_count=turn_count,
            reason=(f"the record states {turn_count} turn(s) but holds no turn "
                    "rows — not an empty transcript"),
            kind=TurnAnswer.CONTRADICTION)
    return TurnAnswer(
        answered=False, turn_count=None,
        reason=("the record holds no narrative row and no turn rows for this "
                "session — it did not answer"),
        kind=TurnAnswer.NO_ACCOUNT)


# How many gap indices a shortfall message NAMES (#10021 review P2). The
# diagnosis is the arithmetic below; this bound is only how much text it prints,
# and it is why the gap walk can stop early.
_SHORTFALL_GAPS_NAMED = 5


def _shortfall(turns: Sequence[Turn], turn_count: int | None,
               turn_max_index: int | None = None) -> str | None:
    """WHY these rows are not the session's whole transcript — or ``None``.

    Three facts make a row set incomplete, and each is decidable from a datum
    this read already has, so none needs a second store to ask:

    * it is SHORTER than the count the record itself states on the narrative row
      (`len(turns) < turn_count`) — the shape a mid-capture refusal leaves, where
      the refused turns went to the graph;
    * it does not cover index ``0 .. max(index)`` — the `turn_offset` append
      shape (`_t2`/`_t3` in the record, `_t0`/`_t1` still in the graph), where
      `turn_count` is the WINDOW length and therefore cannot see the gap, and an
      interior hole left by one refused write between two that landed;
    * it stops BELOW the id space's end (``max(index) < turn_max_index``) — a
      refused HIGHEST turn. ⛔ THIS ONE is
      invisible to the other two AND to the row set itself (#10000 review P2):
      a missing tail is ABOVE ``max(held)``, and the run stays ``0 .. max(held)``
      with `len(rows) >= turn_count` (a WINDOW length) whenever an append window
      is partially refused — `{_t0,_t1}` plus an append window `{_t2,_t3}` whose
      `_t3` was refused is `turn_count=2` and three contiguous rows, with the
      fourth turn readable through no door. The mark is the only witness, and it
      is the graph `:Session`'s, which is why it is a parameter (``None`` when
      the caller cannot name it — degrade, never invent). It is the session's
      LIVE end, not a peak: the writer lowers it on a shorter re-capture (see
      ``session_turns``), so a shrink is not reported as a missing tail.
    * it holds a row ABOVE the id space's end (``max(index) > turn_max_index``) —
      residue from a shorter re-capture whose surplus sweep did not land, the
      mirror of the bullet above and the direction `served_turn_count` cannot
      refuse (a LONGER transcript is also the honest APPEND shape, #10021 review
      P2). An append writes ``turn_max_index = turn_offset + count - 1``, so it
      can never leave a row above the mark; residue can.

    A record that HOLDS FEWER than its own count, or whose ids start above 0,
    skip an index, or stop short of the mark has not answered for the session —
    see ``TurnAnswer``.
    """
    if turn_count is not None and len(turns) < turn_count:
        return (f"the record states {turn_count} turn(s) but holds only "
                f"{len(turns)} — a partial transcript, not an answer for the "
                "whole session")
    held = {turn.index for turn in turns}
    if not held:
        return None
    # ⛔ THE GAP CHECK IS ARITHMETIC, NOT A WALK OF THE ID SPACE (#10021 review
    # P2 — CWE-407 *Inefficient Algorithmic Complexity*, Scope: Availability).
    # This arm used to be `[i for i in range(max(held) + 1) if i not in held]`
    # and then use at most five of the result: a per-read cost of O(max index)
    # whose bound is a STORED integer, not the request's own size, on every
    # turn read and on BOTH serving doors (`GET /v1/sessions/{id}` and
    # `TortoiseSDK.get_session_detail`). `max(held)` comes from the stored key
    # (`_turn_index` accepts any suffix `int()` can parse), `_turns` selects the
    # whole row set with no LIMIT, and the write end never validates the id
    # space — a legacy/copied row (`tools/backfill_graph_turns.py` carries
    # `<session>_t<index>` ids into the record verbatim) at index 10**9 cost
    # ≈16 minutes and ≈40 GB inside one request, materialised and then thrown
    # away.
    #
    # All three facts the arm wants are still decidable, in O(len(turns)):
    # `missing_count` (the subtraction) says WHETHER there is a gap, and the
    # gaps themselves are found by walking the rows `_turns` has already SORTED
    # — never by iterating the id space. The walk stops at
    # `_SHORTFALL_GAPS_NAMED` appends, so it allocates a constant either way.
    # ⛔ Do NOT substitute a cap (`if span > 10**6: report a gap`): that swaps
    # one invented bound for another and misreports a legitimately long
    # session. The arithmetic needs no threshold at all.
    span = max(held) + 1                 # the index space 0 .. max(held)
    missing_count = span - len(held)     # whether there is a gap, no scan
    if missing_count:
        gaps: list[int] = []
        expected = 0
        for index in sorted(held):
            while expected < index and len(gaps) < _SHORTFALL_GAPS_NAMED:
                gaps.append(expected)
                expected += 1
            if len(gaps) >= _SHORTFALL_GAPS_NAMED:
                break
            expected = index + 1
        shown = ", ".join(str(i) for i in gaps)
        if missing_count > len(gaps):
            shown += f" (+{missing_count - len(gaps)} more)"
        return (f"the record holds {len(turns)} turn(s) but not the whole run "
                f"(no turn row for index {shown}) — a partial transcript")
    if turn_max_index is not None and max(held) < turn_max_index:
        return (f"the record's turn rows stop at index {max(held)} while the "
                f"session's turn ids reach {turn_max_index} — the tail is "
                "missing, so this is a partial transcript")
    if turn_max_index is not None and max(held) > turn_max_index:
        # ⛔ THE MARK IS A WITNESS IN BOTH DIRECTIONS (#10021 review P2). Only
        # the SHORT side was read above, so rows ABOVE the session's live
        # id-space end — a shorter re-capture whose surplus sweep did not land
        # (the record delete is journal-emitted and can be refused) — answered
        # COMPLETE and were served as this session's transcript. An APPEND
        # cannot produce this shape: it writes `turn_max_index =
        # turn_offset + count - 1` (`sdk._write_session_and_turns`), so for a
        # healthy append `max(held) <= turn_max_index` always. A row above the
        # live end is residue, and `served_turn_count` deliberately tolerates a
        # LONGER served set when the record answered, so nothing downstream
        # would have questioned it either — this comparison is the only one that
        # can, and the datum is already in hand.
        #
        # ⛔ DETECTION IS NOT A GUARD (#10021 review P3). Reporting the residue
        # as not-answered left the rows in `turns`, and `merge_turn_payloads`
        # merged every one of them into the served body — a 4-row answer against
        # a 1-turn session published `{"turns": 4, "turn_points": [4 rows]}`
        # with only a WARNING to show for it. The same mark is handed to the
        # merge, which DROPS the rows above it from either store: this arm is
        # what tells a caller the record does not own them, and the merge is
        # what stops them reaching a client.
        return (f"the record holds turn rows up to index {max(held)} while the "
                f"session's turn ids reach only {turn_max_index} — rows the "
                "session no longer owns, so this is not its transcript")
    return None


def turn_payloads(turns: Sequence[Turn]) -> list[dict]:
    """The ``turn_points`` payload both serving doors return — ONE definition.

    The hosted ``GET /v1/sessions/{id}`` and ``TortoiseSDK.get_session_detail``
    serve the same key set for the same session (#3557's parity contract), and a
    second copy of the record→payload field map is how the two come to disagree
    the moment one of them grows a field. So the map lives here, next to the
    reader that produces the values, and both doors call it.

    ``role`` prefers the record's own ``speaker`` and falls back to the writer's
    frame inverse (`sdk._capture_turn_role_text`) — the same pair the graph read
    uses, which is what makes the two stores serve identical payloads for the
    same turn (#4675: a client comparing a stored turn against a served detail
    depends on the two agreeing).
    """
    out: list[dict] = []
    for turn in turns:
        out.append({
            "id": turn.turn_id,
            "role": turn.speaker or _role_and_body(turn.content)[0],
            "content": turn.body,
            "created_at": turn.created_at,
        })
    return out


def merge_turn_payloads(answer: TurnAnswer, other: Sequence[dict],
                        turn_max_index: int | None = None) -> list[dict]:
    """The record's rows AND the other store's, ONE turn each, in turn order.

    ⛔ WHY A MERGE AND NOT A CHOICE (#10000 review P0). The read's store
    decision is per SESSION while the writer's is per TURN, so the transitional
    dual-store state includes sessions whose transcript is SPLIT: a per-turn
    record write refused after an earlier one landed (`sdk._write_capture_turns`
    puts the refused ones in the graph), or an append on an armed deployment
    whose earlier turns were captured before arming. In that state NEITHER store
    holds the whole transcript, so serving either one alone is a truncated
    answer — and serving the record alone, which any non-empty row set used to
    license, silently dropped the turns the fallback had just rescued.

    So the caller serves the UNION: the record's rows first, then the other
    store's rows for the ids the record does not hold, ordered by the index the
    turn id carries (`{session}_t{i}`, the contract `_turn_index` states). A row
    present in BOTH stores is the record's — the record is the store of record
    (§9.1/D1), and the two are the same turn.

    ⛔ AND A ROW ABOVE THE SESSION'S LIVE ID-SPACE END IS NOT MERGED IN
    (#10021 review P3). ``turn_max_index`` is the session's inclusive id-space
    end, and a row above it is RESIDUE — a shorter re-capture whose surplus
    sweep did not land (the record delete is journal-emitted and can be
    refused). Before this filter the residue rows were DETECTED by
    :func:`_shortfall` (the answer came back not-answered, at WARNING) and then
    served ANYWAY: this merge added every ``answer.turns`` row, and
    :func:`served_turn_count` deliberately tolerates ``served > expected`` when
    the record answered, so a 4-row answer against a 1-turn session published
    ``{"turns": 4, "turn_points": [4 rows]}`` and the only observable change
    was the log line. A detector whose served body is unchanged is not a guard,
    so the row is dropped here — from EITHER store, because where the residue
    sits is not what makes it residue (the failed sweep leaves it in both).

    Dropping is safe in the healthy direction: an APPEND writes
    ``turn_max_index = turn_offset + count - 1`` (``sdk._write_session_and_turns``),
    so no live turn can sit above the mark. ``turn_max_index=None`` (a session
    captured before the mark existed) abstains and merges everything, exactly
    as it did before this filter — degrade, never invent.

    This is the transition half of #10000, exactly like the graph fallback it
    extends: it disappears with the graph-side purge step. Until then it is what
    makes "no path leaves a turn in neither store" true of the READ as well as
    of the write.
    """
    def _is_residue(payload: Any) -> bool:
        """A row the session no longer owns — above its live id-space end.

        Only for a row this module can PLACE: an id that does not carry an index
        (`{session}_t{i}`) cannot be compared to the mark without inventing an
        order, so it is kept (the same degradation the ordering below applies).
        """
        if turn_max_index is None or not isinstance(payload, dict):
            return False
        turn_id = payload.get("id")
        if not isinstance(turn_id, str):
            return False
        match = re.search(r"_t([0-9]+)$", turn_id)
        if match is None:
            return False
        return int(match.group(1)) > turn_max_index

    merged: dict[str, dict] = {}
    order: list[str] = []
    for payload in turn_payloads(answer.turns):
        turn_id = payload.get("id")
        if (isinstance(turn_id, str) and turn_id and turn_id not in merged
                and not _is_residue(payload)):
            merged[turn_id] = payload
            order.append(turn_id)
    for payload in other:
        turn_id = payload.get("id") if isinstance(payload, dict) else None
        if (isinstance(turn_id, str) and turn_id and turn_id not in merged
                and not _is_residue(payload)):
            merged[turn_id] = payload
            order.append(turn_id)

    def _order_key(turn_id: str) -> tuple:
        match = re.search(r"_t([0-9]+)$", turn_id)
        # Ids that do not carry an index keep their arrival order at the end
        # rather than raising in a serving read: ordering is a nicety here, and
        # a legacy row must not cost the caller its transcript.
        if match is None:
            return (1, 0, turn_id)
        return (0, int(match.group(1)), turn_id)

    return [merged[turn_id] for turn_id in sorted(order, key=_order_key)]


def served_turn_count(*, answer: TurnAnswer | None, served: int,
                      declared: int | None,
                      read_failed: bool = False) -> tuple[int | None, str]:
    """The ``turns`` a detail body may publish beside ``served`` rows — or why not.

    ⛔ ONE RULE FOR BOTH ARMS (#10021 review P2). Both doors publish two numbers
    taken from DIFFERENT stores: ``turns`` was the ``:Session``'s ``turn_count``
    (the WRITER's claim — a WINDOW length) while ``turn_points`` is the UNION the
    record read and the graph copy produce. On the failure arm the doors already
    compared the two and refused; on the SUCCESS arm nothing did, so a record
    that ANSWERED SHORT served ``{"turns": 4, "turn_points": [2 rows]}`` with no
    error code — the truncated-result-indistinguishable-from-complete shape
    ``#8447`` exists to prevent, arriving through the success path.

    The union itself is right and stays: it must not drop the turns a per-turn
    fallback rescued (:func:`merge_turn_payloads`). What was missing is that the
    COUNT has to describe the transcript being served, or the body contradicts
    itself. So the count and the refusal are decided HERE, once, for both doors:

    * **The record was never consulted** (``answer is None`` and the read did not
      fail — an unarmed deployment): one store, nothing to reconcile. Publish the
      session's own ``declared`` count, unchanged from pre-``#10000``.
    * **Otherwise the record was consulted**, so this body is a judgement about
      two stores. ``expected`` is the LARGEST claim either store makes: the
      session's window length AND the record's own ``turn_count``. A transcript
      SHORTER than the larger claim is provably incomplete → refuse (an explicit
      error, ``#8447``'s sanctioned shape: a new reply FIELD would widen a public
      wire surface, which is owner approval and not a lane decision — see
      ``hosted_api._record_unavailable``).
    * **A transcript LONGER than every claim is the APPEND, not a shortfall**, so
      it is published with its own length (#10021 review P2). Both claims are
      WINDOW lengths — `sdk._write_session_and_turns` writes
      ``turn_count = len(turns)`` for the window it is storing, and that value is
      what ``SessionRecorded`` carries into
      ``public.session_narrative.turn_count``. An append at ``turn_offset=2``
      therefore leaves ``offset + count`` record rows against a ``turn_count`` of
      ``count``: on a HEALTHY, COMPLETE transcript ``served > expected``, and a
      ``served != expected`` rule refused it on both doors. Measured on the real
      writer shape (4 rows ``_t0.._t3``, ``narrative.turn_count=2``,
      ``s.turn_count=2``, ``turn_max_index=3``):
      ``served_turn_count(served=4, declared=2, answer.turn_count=2)`` returned
      ``(None, 'the served transcript holds 4 of the 2 turn(s) …')`` while
      ``session_turns`` had already answered COMPLETE for those same 4 turns.
      ⛔ The direction matters, and this module's own bullets above are what
      settle it: "an append leaves ``offset + count`` rows against a window
      length of ``count``" is only a coherent sentence if a longer transcript is
      SERVEABLE.
    * ⛔ **BUT ONLY WHEN THE RECORD WAS CONSULTED.** ``answer is None`` with
      ``read_failed`` is the GRAPH-ONLY fallback: one store, no second witness,
      and the session's own count is all there is. A copy there that is LONGER
      than that count is the stale-superset state the turn sweep can leave —
      reachable whenever a shorter re-capture's sweep is skipped — and it is not
      this session's transcript, so it is still refused (fail-closed). An append
      cannot arrive on this arm: its older turns live in the RECORD, and the
      record is exactly what is unreachable.

    ⛔ ``turn_max_index`` IS DELIBERATELY NOT ONE OF THE CLAIMS, though both doors
    already hold it and it is the only datum that describes the whole id space.
    It is a CEILING witness (``_shortfall`` refuses a record that stops below it),
    while ``expected`` is a FLOOR: folding the mark in as ``turn_max_index + 1``
    makes the graph-only stale-superset state (4 nodes, ``turn_count=2``,
    ``turn_max_index=3`` — the shape the sibling hosted pins build) compute
    ``served == expected`` and SERVES exactly the transcript the bullet above
    refuses. The count answers "is any turn missing"; the mark answers "is there
    a row the session does not own", and one number cannot answer both.

    ``expected`` is deliberately the max rather than either store alone: an
    append's older turns predate arming, so their count lives on the record's
    side, while a mid-capture refusal leaves the record's own count short of the
    session's. Taking the larger claim can only REFUSE more (fail-closed), never
    serve a shorter transcript as complete.

    ``read_failed`` keeps the pre-existing failure arm: a record that could not
    be read and a session carrying no count cannot be verified at all.
    """
    if answer is None and not read_failed:
        return declared, ""
    claims = [c for c in (declared, getattr(answer, "turn_count", None))
              if c is not None]
    expected = max(claims) if claims else None
    if expected is None:
        if read_failed:
            return None, (
                "the record did not answer and the session carries no "
                "turn_count, so no copy of this transcript can be proven "
                "complete")
        return declared, ""
    if served < expected:
        return None, (
            f"the served transcript holds {served} of the {expected} turn(s) "
            "the two stores claim — a short copy is not a complete answer")
    if served > expected and answer is None:
        # The graph-only fallback: no record to attribute the extra rows to, so
        # a copy longer than the session's own count is the stale superset the
        # sweep can leave behind, not an append (see the docstring).
        return None, (
            f"the served transcript holds {served} turn(s) of the {expected} "
            "the session claims, and the record did not answer — a copy longer "
            "than the session's own count is a stale superset, not this "
            "transcript")
    return served, ""


def turn_prior_vectors(conn: Any, *, org_id: str, graph_id: str,
                       session_id: str,
                       turn_ids: Sequence[str]) -> dict[str, PriorTurn]:
    """``{turn_id: PriorTurn}`` — what the record already HOLDS for these ids.

    The record's half of #5004 round-3's PRESERVE rule (#10000 review P1). The
    graph path learns what a write LEFT on the node from the write's own
    read-back (`_TURN_WRITE_CYPHER`'s `RETURN ... t.embedding`), because a
    re-capture with the embedder unavailable must journal the vector the write
    PRESERVED, not the `None` this capture encoded — journalling `None` tells the
    replay the vector is genuinely gone and CLEARS it. With the turns in the
    record there is no node to read back, so the prior value is read from the
    record instead, through the same three rules the Cypher applies: the vector
    survives only when the stored CONTENT is unchanged (a preserved vector for
    changed text ranks the turn by text no longer stored).

    ``embedding`` is the record's jsonb column, returned as PostgreSQL hands it
    back (a list of floats, or ``None``). A read that fails must not be mistaken
    for "no prior vector": the caller decides, and the graph path's answer for
    that case is a CLEARED vector — see `sdk._write_capture_turns`.

    ⛔ IT ALSO CARRIES ``created_at`` AND ``status``, because the vector is not
    the only field a re-capture must not overwrite (see :class:`PriorTurn`).

    SCOPED, like :func:`session_turns`: an unscoped read here would silently
    report "no prior vector" for every turn and clear the whole window's vectors.
    """
    from tortoise.record_projection import scope_connection

    ids = tuple(t for t in turn_ids if isinstance(t, str) and t)
    if not ids:
        return {}
    scope_connection(conn, org_id)
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT node_key, content, embedding, created_at, status "
        f"FROM public.{POINT_TABLE} "
        f"WHERE org_id = %s AND graph_id = %s AND node_key IN %s",
        (org_id, graph_id, ids),
    )
    out: dict[str, PriorTurn] = {}
    for row in _rows(cursor):
        node_key = row[0]
        if not isinstance(node_key, str):
            continue
        content = row[1]
        created_at = row[3] if len(row) > 3 else None
        status = row[4] if len(row) > 4 else None
        out[node_key] = PriorTurn(
            content=content if isinstance(content, str) else "",
            embedding=row[2],
            created_at=created_at if isinstance(created_at, str) else None,
            status=status if isinstance(status, str) else None,
        )
    return out


def _session_turn_rows(conn: Any, *, org_id: str, graph_id: str,
                       session_id: str) -> list[tuple[int, str]]:
    """``[(index, node_key)]`` for every turn row the RECORD holds — O(rows).

    THE ONE declaration of "which turn rows does this session have", shared by
    its two consumers: :func:`turn_high_water` keeps the maximum index, and
    :func:`turn_keys_at_or_above` keeps the KEYS from a floor up. Both used to be
    derived from an id-space walk built out of a stored bound; see
    :func:`turn_keys_at_or_above` for why the ROWS are the only thing either may
    be built from (#10021 review P2, CWE-407 / CWE-770: an allocation sized by a
    stored integer).

    SCOPED: an unscoped read here sees NOTHING under the production role, so
    install the tenant scope first — the same rule every other read in this
    module states.

    Ordered by the id's own index, in PYTHON: only a key-per-row scan can order
    ``_t9`` before ``_t10`` (a lexical ``max(node_key)`` cannot). A key whose
    suffix cannot be PARSED is skipped, not swept and not ordered — the regex
    above already refuses every key without a digit run, and ``int()`` can still
    refuse a run past CPython's conversion limit; ``_turn_index`` states that
    trap.
    """
    from tortoise.record_projection import scope_connection

    if not session_id:
        raise RecordReadError("a record turn read needs a session id")
    scope_connection(conn, org_id)
    cursor = _cursor(conn)
    # The same sargable range + anchored regex `_turns` uses, projected down to
    # the KEY: the index lives in the id.
    cursor.execute(
        f"SELECT node_key FROM public.{POINT_TABLE} "
        f"WHERE org_id = %s AND graph_id = %s AND is_episodic IS TRUE "
        f"AND node_key >= %s AND node_key < %s AND node_key ~ %s",
        (org_id, graph_id, session_id + "_t", session_id + "_u",
         _turn_pattern(session_id)),
    )
    out: list[tuple[int, str]] = []
    for row in _rows(cursor):
        key = row[0] if row else None
        if not isinstance(key, str):
            continue
        try:
            index = _turn_index(key, session_id)
        except RecordReadError:
            # The regex above already refused every key without a parseable
            # index, so this is unreachable from a real row set; skip rather
            # than abort a read bound on one damaged key.
            continue
        out.append((index, key))
    out.sort(key=lambda pair: pair[0])
    return out


def turn_keys_at_or_above(conn: Any, *, org_id: str, graph_id: str,
                          session_id: str,
                          first_index: int) -> tuple[str, ...]:
    """The turn KEYS the RECORD holds at or above ``first_index`` — the residue.

    The record's own answer to "which ids must the #1920 surplus sweep remove",
    asked instead of the question it used to ask (#10021 review P2).
    :func:`turn_high_water` answers the same fact as ONE NUMBER, and the sweep
    turned that number into an id LIST —
    ``[_capture_turn_id(session, i) for i in range(_first_live, bound + 1)]``.
    The bound is a STORED integer the write end never validates
    (``_turn_index`` accepts any suffix ``int()`` can parse, ``_turns`` selects
    with no LIMIT, and ``tools/backfill_graph_turns.py`` copies turn ids in
    verbatim), so one row at index ``10**9`` cost the capture a ~10⁹-element id
    list, ~2 × 10⁹ journal appends and a 10⁹-element ``IN $ids`` parameter —
    CWE-407 (Scope: Availability) plus CWE-770 (allocation without a limit),
    inside a write the caller sees SUCCEED.

    ⛔ ENUMERATION, NOT A CAP. The surplus is by definition the rows at/above the
    floor, and this read already visits every one of them, so naming them costs
    O(rows) and needs no threshold at all. A cap (``if span > 10**6: report``)
    would swap one invented bound for another and misreport a legitimately long
    session — the same ruling ``_shortfall``'s interior-gap arm records for the
    READ side, applied to the second consumer of the same datum.

    ``()`` means "the record holds no turn row at or above the floor" — a clean
    answer. A FAILED read raises, so the caller can tell an empty answer from
    one it never got: a read this process could not make must never be read as
    "the record holds nothing", which would sweep nothing at all.
    """
    return tuple(key for (index, key) in _session_turn_rows(
        conn, org_id=org_id, graph_id=graph_id, session_id=session_id)
        if index >= first_index)


def turn_high_water(conn: Any, *, org_id: str, graph_id: str,
                    session_id: str) -> int | None:
    """The HIGHEST turn index the RECORD holds for this session — or ``None``.

    The record's own answer to the question `sdk._write_capture_turns` asks
    before it removes a shorter re-capture's surplus (#10021 review P2). The
    bound of that sweep used to come from ONE place: the graph `:Session`'s
    ``turn_max_index``, read only when a probe of the graph `:Session` node said
    the session already existed. That is a fact about the GRAPH, and the bound it
    supplies is a fact about the RECORD's residue — the two coincide only while
    every captured session also has a live graph node, which §2.0/§9.1
    explicitly deny (the graph is the cache; D7 is measured with it destroyed).
    With the node gone, a 4-turn session re-captured as 1 turn swept NOTHING and
    the record kept ``_t0.._t3``, which both serving doors then presented as a
    COMPLETE transcript (``record_read._shortfall`` refuses a SHORT answer, and
    a longer one is the append shape it deliberately serves).

    ⛔ IT IS THE ROW SET'S ANSWER, NOT THE NARRATIVE'S. ``turn_count`` on
    ``session_narrative`` is a WINDOW length (an append leaves ``offset + count``
    rows against it — see :func:`served_turn_count`), so it cannot name the last
    index; a row the session no longer owns is only visible in the ids.

    ``None`` means "the record holds no turn row for this session", which is a
    clean answer (a fresh capture), not a failure — a FAILED read raises, so the
    caller's ``except`` can tell the two apart and decline to sweep rather than
    sweep an invented range. Scoped like :func:`_turns`: an unscoped read here
    would report "no rows" for every session under the production role.

    ⚠️ THE SWEEP NO LONGER ALLOCATES FROM THIS NUMBER (#10021 review P2). It is
    still the honest answer to "how far does the record's id space reach", and
    :func:`turn_keys_at_or_above` — which reads the SAME row set — is what the
    sweep now names its ids from, because a number that sizes a list is a
    stored-integer allocation (see that function). Both derive from
    :func:`_session_turn_rows`, so the two cannot disagree about which rows
    exist.
    """
    rows = _session_turn_rows(conn, org_id=org_id, graph_id=graph_id,
                             session_id=session_id)
    return rows[-1][0] if rows else None


def _cursor(conn: Any):
    """A DB-API cursor, matching `record_projection.replay`'s protocol."""
    return conn.cursor()


def _rows(cursor) -> list[Any]:
    rows = cursor.fetchall()
    return list(rows or ())


#: One role the writer's inverse returns for text carrying NO frame.
_UNFRAMED_ROLE = "unknown"


def _role_and_body(stored: str) -> tuple[str, str]:
    """`(role, body)` for one stored turn text — the WRITER's own inverse.

    Imported lazily: `sdk` is heavy and this module is on the read path. The
    alternative — a local copy of the `"[role] body"` grammar — would be a second
    declaration of the frame, and `sdk._capture_turn_role_text` exists precisely
    so the writer and its readers cannot drift.
    """
    from tortoise.sdk import _capture_turn_role_text

    return _capture_turn_role_text(stored)


def _turn_pattern(session_id: str) -> str:
    """The POSIX regex that matches exactly this session's turn keys.

    `^<session_id>_t[0-9]+$`. `re.escape` escapes regex metacharacters in the
    session id (`+`, `.`, `(`, …) and leaves `_` alone, so a session named
    `a_t1` cannot match another session's turns.
    """
    return "^" + re.escape(session_id) + "_t[0-9]+$"


def _turn_index(turn_id: str, session_id: str) -> int:
    """The numeric index encoded in a turn key, or a LOUD refusal."""
    match = re.search(r"_t([0-9]+)$", turn_id)
    if match is None or not turn_id.startswith(session_id + "_t"):
        raise RecordReadError(
            f"turn key {turn_id!r} does not follow `{{session_id}}_t{{index}}` "
            f"for session {session_id!r} — the record's turn ordering is the "
            "id's own index (sdk._capture_turn_id), and a key that does not "
            "carry one cannot be ordered without inventing an order."
        )
    try:
        return int(match.group(1))
    except ValueError as exc:  # a digit run past CPython's conversion limit
        # `sdk._capture_turn_ids` records the SAME trap: `isdecimal()` being
        # true does not imply `int()` parseable, and the failure it raises is a
        # bare ValueError that would escape this module's typed contract and
        # take the whole session read (narrative included) with it.
        raise RecordReadError(
            f"turn key {turn_id!r} carries a numeric suffix this process cannot "
            f"parse as an index ({exc}) — the turn cannot be ordered."
        ) from exc


def _narrative_row(conn: Any, org_id: str, graph_id: str,
                   session_id: str) -> tuple | None:
    cursor = _cursor(conn)
    cursor.execute(
        f"SELECT narrative, summary, capture_redactions, turn_count "
        f"FROM public.{NARRATIVE_TABLE} "
        f"WHERE org_id = %s AND graph_id = %s AND session_id = %s",
        (org_id, graph_id, session_id),
    )
    rows = _rows(cursor)
    return rows[0] if rows else None


def _turns(conn: Any, org_id: str, graph_id: str,
           session_id: str) -> tuple[Turn, ...]:
    cursor = _cursor(conn)
    # SARGEABLE RANGE FIRST, shape rule SECOND. `node_key ~ <regex>` alone is not
    # sargable, so the plan would be "every point row in this tenant graph" on
    # §9.4's answer-time path. `(org_id, graph_id, node_key)` is the primary key,
    # so a prefix RANGE bounds the scan to this session's neighbourhood (`_t` …
    # `_u`, since `t` < `u`), and the anchored regex still decides membership —
    # including the case the range cannot: a session id that is a prefix of
    # another (`sec` vs `sec_t0`).
    cursor.execute(
        f"SELECT node_key, content, speaker, created_at FROM public.{POINT_TABLE} "
        f"WHERE org_id = %s AND graph_id = %s AND is_episodic IS TRUE "
        f"AND node_key >= %s AND node_key < %s AND node_key ~ %s",
        (org_id, graph_id, session_id + "_t", session_id + "_u",
         _turn_pattern(session_id)),
    )
    turns: list[Turn] = []
    for row in _rows(cursor):
        turn_id, content, speaker = row[0], row[1], row[2]
        created_at = row[3] if len(row) > 3 else None
        if not isinstance(turn_id, str) or not isinstance(content, str):
            raise RecordReadError(
                f"a turn row for session {session_id!r} has no usable "
                f"key/content (key={turn_id!r}, content type="
                f"{type(content).__name__}) — the record is malformed for "
                "this session."
            )
        turns.append(Turn(
            turn_id=turn_id,
            index=_turn_index(turn_id, session_id),
            content=content,
            speaker=speaker if isinstance(speaker, str) and speaker else None,
            created_at=created_at if isinstance(created_at, str) else None,
        ))
    # Ordered by the id's own index, in PYTHON: the order is a property of the
    # key contract, and re-deriving it in SQL (a regex substring cast) would be
    # a second declaration of the same rule.
    turns.sort(key=lambda t: t.index)
    return tuple(turns)


def session_text(conn: Any, *, org_id: str, graph_id: str,
                 session_id: str) -> SessionText | None:
    """§9.1's text for one session, read from the record only.

    Returns ``None`` when the record holds neither a narrative row nor any
    turn — an honest absence (a session captured by a lane that never
    distilled a narrative, or before this table existed). A session with only
    one of the two returns what exists: the halves arrive from different
    records, in either order (see `record_projection._narrative_statements`).
    """
    if not session_id:
        raise RecordReadError("session_text needs a session id")
    row = _narrative_row(conn, org_id, graph_id, session_id)
    turns = _turns(conn, org_id, graph_id, session_id)
    if row is None and not turns:
        return None
    narrative, summary, redactions, turn_count = (
        row if row is not None else (None, None, None, None)
    )
    return SessionText(
        session_id=session_id,
        narrative=narrative if isinstance(narrative, str) else None,
        summary=summary if isinstance(summary, str) else None,
        redactions=redactions if isinstance(redactions, int) else None,
        turn_count=turn_count if isinstance(turn_count, int) else None,
        turns=turns,
    )


def source_session(conn: Any, *, org_id: str, graph_id: str,
                   url: str) -> str | None:
    """THE LINK: the session id behind a `:Source`'s url, or ``None``.

    §9.4 makes the claim→source link load-bearing, and this is that link read
    as its OWN fact rather than folded into the text lookup. It matters because
    ``None`` here is a **different statement** from "the link resolved and the
    session holds no text" (:func:`session_text` returning ``None``):

    * ``None`` — **no link was resolved**: no `public.source` row with this url
      in this tenant (a mistyped ``--source-url``, a url never captured, a url
      outside the tenant), or the row's ``session_id`` is NULL/blank;
    * a session id — the link resolved, so “this session holds no §9.1 text” is
      then a true statement about that session.

    ⛔ Collapsing the two is a claim-level fail-open, measured in review (PR
    #9862, P2): the verb printed *"the source's session was resolved, and the
    record holds no text for it"* for BOTH, i.e. it narrated a proof step it had
    not performed — in the one string an operator debugging "why is there no
    text?" reads first, and with no second signal in ``--json`` either.

    ⚠️ The lookup probes `node_key`, NOT `url`. For a `:Source` the canonical key
    IS the url (`(label, key)` = `Source -> url`, and `_upsert_statement` writes
    `node_key = <that key>`), so both columns hold the same string while only
    `node_key` is indexed (`PRIMARY KEY (org_id, graph_id, node_key)`; there is
    no index on `url`). `url = %s` would resolve against the tenant prefix and
    filter the tenant's whole source set on §9.4's answer-time path.
    """
    if not url:
        raise RecordReadError("source_session needs a source url")
    cursor = _cursor(conn)
    cursor.execute(
        "SELECT session_id FROM public.source "
        "WHERE org_id = %s AND graph_id = %s AND node_key = %s",
        (org_id, graph_id, url),
    )
    rows = _rows(cursor)
    if not rows or not isinstance(rows[0][0], str) or not rows[0][0].strip():
        return None
    return rows[0][0]


def source_narrative(conn: Any, *, org_id: str, graph_id: str,
                     url: str) -> SessionText | None:
    """§9.4's verbatim fetch: the session text behind a `:Source`'s url.

    §9.4: *"show me the verbatim text behind this claim"* → **the link** — the
    source's session is `public.source.session_id`, and the text is the
    session's narrative + turns. This is the record-sourced arm of that fetch:
    with the graph destroyed the *link* still resolves, because it is a column
    on the record's own `source` row rather than an edge in the cache.

    ⚠️ ``None`` here is AMBIGUOUS BY CONSTRUCTION (an unresolved link and an
    empty session both answer ``None``), so a caller that REPORTS on the miss
    must call :func:`source_session` and :func:`session_text` itself rather
    than infer the cause from this return value — see that function's note.
    """
    session_id = source_session(conn, org_id=org_id, graph_id=graph_id, url=url)
    if session_id is None:
        return None
    return session_text(conn, org_id=org_id, graph_id=graph_id,
                        session_id=session_id)
