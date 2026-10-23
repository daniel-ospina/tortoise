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
moment a rebuild's source of truth is the journal, "the record is the system of
record" (§2.0) is false for exactly the text §9.1 says must survive.

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
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "RecordReadError",
    "SessionText",
    "Turn",
    "session_text",
    "source_narrative",
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

    @property
    def body(self) -> str:
        """The un-framed body of this turn (§9.4's verbatim text for one turn)."""
        _role, body = _role_and_body(self.content)
        return body


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
        lines = []
        for turn in self.turns:
            role, body = _role_and_body(turn.content)
            # `"unknown"` is the INVERSE's sentinel, not a speaker the record
            # knows — and the writer stores it in `point.speaker` too
            # (`_capture_turn_frame` emits `[unknown] …` for a role-less turn),
            # so the sentinel has to be filtered on BOTH sides or every
            # role-less turn is served as `"unknown: …"`.
            speaker = turn.speaker
            if speaker == _UNFRAMED_ROLE:
                speaker = None
            label = speaker or (role if role != _UNFRAMED_ROLE else None)
            lines.append(f"{label}: {body}" if label else body)
        return "\n\n".join(lines)

    @property
    def verbatim_available(self) -> bool:
        """Is there any §9.4-usable text at all?"""
        return bool(self.narrative or self.summary or self.turns)


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
        f"SELECT node_key, content, speaker FROM public.{POINT_TABLE} "
        f"WHERE org_id = %s AND graph_id = %s AND is_episodic IS TRUE "
        f"AND node_key >= %s AND node_key < %s AND node_key ~ %s",
        (org_id, graph_id, session_id + "_t", session_id + "_u",
         _turn_pattern(session_id)),
    )
    turns: list[Turn] = []
    for row in _rows(cursor):
        turn_id, content, speaker = row[0], row[1], row[2]
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


def source_narrative(conn: Any, *, org_id: str, graph_id: str,
                     url: str) -> SessionText | None:
    """§9.4's verbatim fetch: the session text behind a `:Source`'s url.

    §9.4: *"show me the verbatim text behind this claim"* → **the link** — the
    source's session is `public.source.session_id`, and the text is the
    session's narrative + turns. This is the record-sourced arm of that fetch:
    with the graph destroyed the *link* still resolves, because it is a column
    on the record's own `source` row rather than an edge in the cache.

    ⚠️ The lookup probes `node_key`, NOT `url`. For a `:Source` the canonical key
    IS the url (`(label, key)` = `Source -> url`, and `_upsert_statement` writes
    `node_key = <that key>`), so both columns hold the same string while only
    `node_key` is indexed (`PRIMARY KEY (org_id, graph_id, node_key)`; there is
    no index on `url`). `url = %s` would resolve against the tenant prefix and
    filter the tenant's whole source set on §9.4's answer-time path.
    """
    if not url:
        raise RecordReadError("source_narrative needs a source url")
    cursor = _cursor(conn)
    cursor.execute(
        "SELECT session_id FROM public.source "
        "WHERE org_id = %s AND graph_id = %s AND node_key = %s",
        (org_id, graph_id, url),
    )
    rows = _rows(cursor)
    if not rows or not isinstance(rows[0][0], str) or not rows[0][0].strip():
        return None
    return session_text(conn, org_id=org_id, graph_id=graph_id,
                        session_id=rows[0][0])
