"""Append-only JSONL domain event log — reconstruction + audit, never durability.

This is a DOMAIN EVENT LOG: it reconstructs a projection under changed fold
logic, migrates engines, and audits beyond `:GraphEvent`'s 30-day window. It is
NOT the durability mechanism — for any deployment running a FalkorDB server,
durability is the store's own persistence plus an off-box copy (see
docs/durability-posture.md).

M0 implements append + read_all only. Idempotency (the ingest cursor / dedup
keys) and streaming tail arrive in M1/M4.

Cursors
-------
:meth:`read_after` accepts an opaque cursor token that encodes a 0-based
line index into the log.  Callers obtain a cursor from :meth:`cursor_at_end`
(or by encoding the index of the last event they already processed).

Cursor tokens are **not** guaranteed to survive log rotation, compaction, or
rebuild — they are valid only for the lifetime of a single append-only
JSONL file.  For polling use-cases (e.g. subscription change-notification)
the pattern is::

    # Initial sync
    events = log.read_after()          # all events so far
    cursor = log.cursor_at_end()       # snapshot current end

    # Subsequent polls
    new_events = log.read_after(cursor)
    if new_events:
        process(new_events)
        cursor = log.cursor_at_end()   # advance cursor

Internal format (opaque — callers MUST NOT depend on this)::

    base64(json({"v": 1, "i": <0-based index of last-seen event>}))
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

# ── torn-tail revival classification (#3316) ────────────────────────────
#
# ``read_all`` tolerates a torn TRAILING line on purpose: a crash mid-append
# is an expected artifact, and a dropped REGISTRATION record means data LOSS.
# The other direction is not symmetric. A dropped TERMINAL record — a
# retraction, a supersession, a hard delete — means data RESURRECTION: the
# replay rebuilds the graph WITHOUT the removal, so state a later read serves
# as current comes back.
#
# A replay engine must therefore not treat every torn tail as tolerable. The
# records below are the ones whose loss can revive state; a replayed journal
# that dropped one is not a recovery of that journal.
TORN_TAIL_REVIVAL_EVENT_TYPES = frozenset({
    # Point lifecycle (``PointsMerged`` deletes the merged-away Points).
    "PointRetracted", "PointSuperseded", "PointInvalidated", "PointsMerged",
    # Object lane: ``ObjectSuperseded`` exists on this tree; ``ObjectRetracted``
    # is the "$2977" lane (#3316 names it) and is listed so a journal carrying
    # one is not silently replayed once that lane lands.
    "ObjectSuperseded", "ObjectRetracted",
    # The generic durable-mutation record: ``op="delete"`` is a hard delete and
    # a ``state`` op can terminalize. A torn record's ``op`` may not have
    # survived the tear, so it can never be proven non-destructive.
    "EntityMutated",
})

# ``"type"`` is matched anywhere in the partial record (the JSONL envelope is
# ``event_id`` / ``ts`` / ``type`` / …), so a tear after the type field is
# legible; one before it is not, and an unlegible type is NOT assumed
# harmless.
_RECORD_TYPE_RE = re.compile(r'"type"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"')


def record_type_from_partial(raw: str) -> str | None:
    """Best-effort ``type`` of a possibly-truncated JSONL record.

    Returns ``None`` when the type did not survive the tear.
    """
    m = _RECORD_TYPE_RE.search(raw)
    return m.group(1) if m else None


def torn_record_may_revive_state(raw: str) -> bool:
    """True when dropping *raw* (a torn trailing record) could REVIVE state.

    False only for a record whose type is legible AND is not a
    removal/terminal record — the data-LOSS direction the torn-tail tolerance
    was designed for. An illegible type is True: it cannot be proven harmless.
    """
    t = record_type_from_partial(raw)
    return t is None or t in TORN_TAIL_REVIVAL_EVENT_TYPES


def torn_tail_revival_records(raws) -> list[str]:
    """The dropped torn-tail records whose loss can REVIVE state (#3316)."""
    return [r for r in raws if torn_record_may_revive_state(r)]


def describe_torn_tail_revival(revival_records) -> str:
    """The legible record types of *revival_records*, for an operator message.

    A tear before the ``type`` field is named ``<unreadable>`` — the state it
    dropped cannot even be identified, so it is reported as such rather than
    omitted.
    """
    return ", ".join(sorted({
        record_type_from_partial(r) or "<unreadable>"
        for r in (revival_records or [])
    }))


class TornTailResurrectionError(RuntimeError):
    """A replay was refused because its journal had dropped a removal record.

    Subclasses :exc:`RuntimeError` so existing ``except RuntimeError`` callers
    keep their contract; it is a distinct type so the operator surfaces can
    turn it into a message instead of a traceback (``tortoise rebuild``).
    """


def refuse_torn_tail_revival(revival_records) -> None:
    """Raise when dropping a torn tail would RESURRECT removed state (#3316).

    *revival_records* is the already-classified output of
    :func:`torn_tail_revival_records` / :meth:`EventLog.torn_tail_revival_records`.
    A truncated record cannot be reconstructed, so the only faithful replay is
    no replay at all: the caller MUST invoke this BEFORE any wipe or fold — a
    verdict after the mutation cannot un-apply it. The raise is the intended
    outcome for such a journal, not a crash; nothing is changed on disk.
    """
    revival = list(revival_records or [])
    if not revival:
        return
    raise TornTailResurrectionError(
        "refusing to replay: the journal's torn trailing record is a "
        f"removal/terminal record ({describe_torn_tail_revival(revival)}); "
        "replaying without it would resurrect the state it removed (#3316). "
        "The graph was NOT touched — repair or truncate the journal, then "
        "retry."
    )


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict]:
        """Read all events in the log.

        LINE-TOLERANCE (epic #900 S15/T12, cycle-21): a malformed TRAILING
        line (a torn tail from a SIGKILL mid-append — ``append`` is a bare
        ``f.write`` with no fsync) is skipped with a warning + count
        (``self.torn_trailing_count``), never raised — a raised parse would
        kill the very recovery tool (``rebuild_all`` / ``_auto_health_recover``).
        A malformed MID-FILE line is a separate corruption class (not a torn
        append) and raises an actionable error naming the file and line.

        The raw text of every skipped trailing line is kept in
        :attr:`torn_trailing_raw` so a replay engine can tell a harmful tear
        from a harmless one (:func:`torn_record_may_revive_state`);
        :attr:`torn_trailing_count` remains the count.
        """
        import logging
        if not self.path.exists():
            return []
        self.torn_trailing_count = 0
        self.torn_trailing_raw: list[str] = []
        out = []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for idx, raw in enumerate(lines):
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                if idx == len(lines) - 1:
                    self.torn_trailing_count += 1
                    # Capped: a file with no newline is one "line", and only
                    # the type-bearing prefix is ever consulted.
                    self.torn_trailing_raw.append(line[:4096])
                    logging.getLogger(__name__).warning(
                        "EventLog %s: skipping torn trailing line %d "
                        "(SIGKILL mid-append tolerance, S15) — %d line(s) skipped",
                        self.path, idx + 1, self.torn_trailing_count)
                else:
                    raise ValueError(
                        f"EventLog {self.path}: malformed line {idx + 1} — "
                        "mid-file corruption is not a torn append; refusing to "
                        "skip (line-tolerance covers the trailing line only)"
                    ) from None
        return out

    def torn_tail_revival_records(self) -> list[str]:
        """Skipped trailing records whose loss can REVIVE state (#3316)."""
        return torn_tail_revival_records(getattr(self, "torn_trailing_raw", []))

    # ── streaming tail (M1 / M4) ──────────────────────────────────────

    def read_after(self, cursor: str | None = None) -> list[dict]:
        """Return events appended after *cursor*.

        If *cursor* is ``None`` (the default), returns **all** events in the
        log — equivalent to :meth:`read_all`.

        If *cursor* is an opaque token (obtained from a previous
        :meth:`cursor_at_end` call), returns only events appended after
        that position.  Returns an empty list when no new events exist.

        Raises :exc:`ValueError` if *cursor* is not a valid token.
        """
        if cursor is None:
            return self.read_all()
        last_idx = self._decode_cursor(cursor)
        events = self._read_all_indexed()
        return events[last_idx + 1:]

    def cursor_at_end(self) -> str:
        """Return an opaque cursor token pointing to the current end of the
        log.  A subsequent :meth:`read_after` with this token will only
        return events appended after this call.

        Returns a valid cursor even when the log is empty (in which case
        ``read_after(token)`` will return all future events).
        """
        events = self._read_all_indexed()
        # When log is empty, encode index -1 so that read_after returns
        # events starting at index 0.
        return self._encode_cursor(len(events) - 1)

    # ── cursor helpers (internal) ─────────────────────────────────────

    @staticmethod
    def _encode_cursor(idx: int) -> str:
        """Encode a 0-based line index as an opaque cursor token."""
        payload = json.dumps({"v": 1, "i": idx}, separators=(",", ":"))
        return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str) -> int:
        """Decode an opaque cursor token to a 0-based line index.

        Raises :exc:`ValueError` if the token is malformed or has an
        unsupported version.
        """
        try:
            raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Invalid cursor token: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise ValueError(
                f"Unsupported cursor version: {payload.get('v')!r}"
            )
        idx = payload.get("i")
        if not isinstance(idx, int):
            raise ValueError(f"Invalid cursor payload: missing index")  # noqa: F541
        return idx

    def _read_all_indexed(self) -> list[dict]:
        """Return all events as a list, same as :meth:`read_all`.

        Separate internal helper so cursor logic can reuse the parsed list
        without re-reading the file.
        """
        return self.read_all()
