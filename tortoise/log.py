"""Append-only JSONL event log — the source of truth.

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
from pathlib import Path


class EventLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

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
            raise ValueError(f"Invalid cursor payload: missing index")
        return idx

    def _read_all_indexed(self) -> list[dict]:
        """Return all events as a list, same as :meth:`read_all`.

        Separate internal helper so cursor logic can reuse the parsed list
        without re-reading the file.
        """
        return self.read_all()
