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
# is an expected artifact, and a raised parse would kill the very recovery tool
# the tolerance exists to save (epic #900 S15/T12).
#
# That tolerance was written for ONE direction. A dropped RECORDATION line
# (a registration, a revision, an annotation) means data LOSS, which the
# tolerance accepts. The other direction is not symmetric: a dropped REMOVAL
# or other TERMINAL line — ``PointRetracted``, ``PointsMerged``,
# ``EntityMutated`` op=delete, ``ObjectSuperseded``, a ``DirectEdgeRepoint``
# delete leg, a ``ConfidenceChanged`` carrying ``outdated=true`` — means
# RESURRECTION. The replay rebuilds the graph WITHOUT the removal, so state
# the journal recorded as gone is served as current again, while recovery
# reports success (#3316).
#
# So the classifier is an ALLOWLIST of record types whose loss is provably the
# data-LOSS direction. The criterion, precisely: the fold's effect on the graph
# is additive, OR the removal it performs is itself carried by a DEDICATED
# terminal record type that this classifier refuses on its own
# (``PointRetracted`` / ``PointSuperseded`` / ``PointInvalidated`` /
# ``EntityMutated`` op=delete / ``PointsMerged`` / ``ObjectSuperseded`` /
# ``DirectEdgeRepoint`` / ``ConfidenceChanged`` with ``outdated=true``). So a
# member's folds MERGE/SET authoritative state and never delete a node or edge
# (clearing a DERIVED embedding so it is recomputed is a cache clear, not such
# a removal), or the member has no fold at all, so its loss is a proof-carrying
# no-op. A torn tail whose type is legible and wholly inside this set keeps the
# pre-existing tolerance. EVERYTHING ELSE is refused: an unlisted type and an
# unreadable type cannot be proven harmless, and a silent resurrection is worse
# than a loud refusal — the refusal touches no graph and leaves the journal for
# the operator.
#
# ONE DISCLOSED EXCEPTION to "never terminalize a lifecycle" — read before
# trusting the criterion above. Root cause in one line: a fold that writes a
# lifecycle property STRAIGHT from the record's payload is payload-dependent,
# and a torn prefix cannot prove that payload absent.
#   * ``PointAdded`` / ``OperatorAdded`` are members, but their fold reaches
#     ``n.status = coalesce($st, n.status, 'live')`` with ``$st`` taken from the
#     payload (projection/entities.py:770, :795) and ``create_point`` accepts
#     every value in ``POINT_STATUS_VALUES`` (sdk.py:3489) — a BORN-TERMINAL
#     create (``_born_terminal``, sdk.py:3655, a first-class case: see #2422)
#     journals ``status`` in its ``PointAdded`` point snapshot (sdk.py:3828). A
#     torn born-terminal ``PointAdded`` for an id an EARLIER record left live is
#     therefore a resurrection this classifier tolerates. It is NOT a
#     first-order path (unlike ``EventRecorded``'s connector leg, which every
#     connector ingest reaches, or ``SessionRecorded`` above): it needs a second
#     ``PointAdded`` for an EXISTING id with an explicit TERMINAL status, and
#     excluding the two types would refuse the most common torn record of all.
#     Born-terminal creates are a supported, behaviorally pinned write
#     (tests/test_2952_write_path_determinism.py::test_born_terminal_point_is_not_a_dirty_root),
#     which is why this is a disclosure and not a mistake. Filed as #5921 with
#     the reproduction. The SAME root cause also reaches the
#     ``PointPromoted`` / ``OperatorPromoted`` ``get_point`` snapshots, which
#     share ``_upsert_point_props``; no writer path can put a terminal status in
#     one (promotion is terminal-guarded), so the reachable composition is the
#     create path above.
#
# ⛔ THE POLARITY IS DELIBERATE: a NEW event type defaults to REFUSED, not
# tolerated. Adding one here is the claim that its loss cannot revive state —
# check its fold in ``tortoise.projection`` first. The inverse (listing the
# removal types instead) fails OPEN: a new terminal type would silently
# resurrect, which is exactly the defect class this exists to close.
TORN_TAIL_HARMLESS_EVENT_TYPES = frozenset({
    # Point / operator lifecycle additions and updates (MERGE + SET only).
    "PointAdded", "OperatorAdded", "PointRevised", "OperatorAnnotated",
    "PointPromoted", "OperatorPromoted",
    # Object / subject lane additions (MERGE + SET; an object or subject
    # upsert clears the DERIVED embedding so it is recomputed).
    "ObjectRegistered", "SubjectAdded",
    # Source lane addition (MERGE + SET only).
    "SourceCreated",
    # Bookkeeping records: ``_NO_PROJECTION_FOLD`` (projection/__init__.py:
    # 1868) — recognized and INTENTIONALLY not folded by any dispatcher
    # (``apply``/``_apply_one``/``rebuild_all`` pass over them), so their loss
    # is a proof-carrying NO-OP on the graph. Refusing them would be pure
    # over-refusal on a record that is often the FIRST line of an ingest.
    "BatchIdStamped", "IngestStarted", "CalibrationRecorded",
    "DedupeRecorded", "DedupeRejected", "EntityBindingRefused",
    "DirectEdgeCreated",
    # Additive edge / prop writes (MERGE + SET only).
    "EntityLinked",
})
# Deliberately NOT harmless although the name reads as an addition:
#   DocumentCreated — ``_upsert_document`` scrubs the RETIRED props
#                     ``content`` / ``doc_status`` / ``docStatus`` /
#                     ``objectKind`` / ``object_kind``
#                     (projection/entities.py:1989), so a dropped tear leaves
#                     state the live write removed.
#   ConfidenceChanged — can carry ``outdated=true``, an EP-terminal flag.
#   DirectEdgeRepoint — its ``delete_only=true`` leg removes an edge.
#   EventRecorded — additive ONLY in the common case; see below.
#   SessionRecorded — ``_fold_session_recorded`` OVERWRITES ``capture_ok`` /
#                     ``capture_extractor`` / ``capture_redactions`` from the
#                     payload (projection/entities.py:1261-1266; the only gate
#                     is payload PRESENCE — ``val is not None and
#                     _annotator_value_ok(val)``), and the SDK emits it as one of
#                     the capture's TRAILING records (sdk.py:4844) with
#                     ``capture_ok=False`` on a failed or keyless capture. A
#                     dropped tear restores ``capture_ok=NULL``, which
#                     ``hosted_api`` reads as the legacy "presumed captured"
#                     replay case (hosted_api.py:10041-10065) — so the failed
#                     session silently STOPS being re-attempted while recovery
#                     reports success. Same root cause as #5921 (a payload-only
#                     lifecycle property), but its harmful payload is COMMON and
#                     it is a trailing record, so it is refused outright.
#
# ⛔ ``EventRecorded`` IS NOT IN THE SET, although its loss is the data-LOSS
# direction for most records. ``_upsert_event`` →
# ``_materialize_connector_source`` DELETEs the superseded
# ``(Source)-[:references]->(Event)`` edge and the orphaned ``:Source`` node
# (projection/entities.py:2390, :2399) whenever the record carries a registered
# connector ``sourceKind`` or an explicit ``sourceUrl``. A torn record is a
# BYTE PREFIX of the record being written, so the ABSENCE of those keys in it
# is NOT evidence that the full record lacked them — the tear can land before
# they would have been written. No prefix-only test can prove such a loss
# harmless, so the type is refused; a marker-substring exception would fail
# OPEN, the exact shape the allowlist polarity exists to prevent. The cost is
# paid deliberately and is bounded: the refusal only blocks a replay, the
# journal is left intact, and it is reachable on the lost-graph recovery path
# (``_recover_or_raise``) — where the alternative is a silently resurrected
# provenance node. Restoring the tolerance requires making that delete leg
# replay-idempotent — a FOLD change, not a classifier change.

# ``"type"`` is matched anywhere in the partial record (the JSONL envelope is
# ``event_id`` / ``ts`` / ``type`` / …), so a tear after the type field is
# legible; one before it is not, and an unlegible or AMBIGUOUS type is NOT
# assumed harmless (see :func:`has_truncated_type_value`).
_RECORD_TYPE_RE = re.compile(r'"type"\s*:\s*"([A-Za-z_][A-Za-z0-9_]*)"')
# A ``"type"`` KEY — see :func:`has_truncated_type_value`. Deliberately stops
# at the COLON (not the opening quote): a tear between ``:`` and the quote is
# still "the value did not survive".
_TYPE_KEY_RE = re.compile(r'"type"\s*:')
# A ``"type"`` key torn MID-TOKEN at the end of the line (``"t`` / ``"ty``
# / ``"typ`` / ``"type``). Anchored at the end because elsewhere a prefix of
# the word collides with other keys (``"title``, ``"tag``) — and required to
# be preceded by ``,`` / ``{`` (a KEY position), so a torn STRING VALUE that
# merely begins ``"t`` (``"id": "t``) is not mistaken for a torn key.
_TORN_TYPE_KEY_RE = re.compile(r'[{,]\s*"t(?:y(?:p(?:e)?)?)?$')


def record_types_from_partial(raw: str) -> list[str]:
    """Every legible ``type`` value in a possibly-truncated JSONL record.

    ALL of them, not just the first: the envelope carries ``type`` before any
    payload, but ``append`` is public and ``read_all`` parses arbitrary bytes,
    so a nested payload dict can carry a ``type`` too. Classification is
    conservative over the whole set (see :func:`torn_record_may_revive_state`).
    """
    return _RECORD_TYPE_RE.findall(raw)


def has_truncated_type_value(raw: str) -> bool:
    """True when a torn record's ``type`` cannot be established unambiguously.

    Three shapes, all refused:

    * **No** legible type at all — the tear landed before or inside the
      envelope's ``type``.
    * **More than one** legible type, or a ``"type"`` key with no legible
      value. The envelope writes ``type`` first, but ``EventLog.append`` is
      public and a journal is operator-editable, so a payload-first record can
      carry a nested ``"type"`` BEFORE the envelope's — and once the envelope's
      is torn away, the remaining prefix is byte-identical to a prefix of a
      harmless record. Refusing is the only safe read: a nested type must never
      stand in for an envelope that did not survive.
    * A ``"type"`` KEY torn mid-token at the end of the line (``"t`` / ``"ty`` /
      ``"typ`` / ``"type`` with no colon), in KEY position (after ``,`` or
      ``{``) — a torn string VALUE that happens to start ``"t`` is not one.

    Named limit, so it is not overstated: a hand-written PAYLOAD-FIRST record
    whose nested ``"type"`` survives while the envelope's key has not yet been
    written (and left no fragment) is byte-identical to a producer prefix and
    cannot be distinguished from one. No writer path emits that shape —
    ``api._emit`` / ``sdk._emit_event`` / ``mining._emit_event`` all place
    ``type`` in the envelope dict before any payload — so the limit is reachable
    only for a hand-edited or foreign journal.

    (An ESCAPED ``\"type\": \"X\"`` inside a string value matches neither
    pattern — the backslash breaks the ``"`` - ``:`` - ``"`` shape — so a value
    that merely mentions the key is not miscounted.)
    """
    if len(_RECORD_TYPE_RE.findall(raw)) != 1:
        return True
    if len(_TYPE_KEY_RE.findall(raw)) != 1:
        return True
    return _TORN_TYPE_KEY_RE.search(raw) is not None


def torn_record_may_revive_state(raw: str) -> bool:
    """True when dropping *raw* (a torn trailing record) could REVIVE state.

    False only when the record's ``type`` is legible and UNambiguous and is a
    member of :data:`TORN_TAIL_HARMLESS_EVENT_TYPES` — the data-LOSS direction
    the torn-tail tolerance was designed for. An unreadable or ambiguous type,
    and a type outside the set, are both True: neither can be proven harmless.
    The classification is over the TYPE ALONE, never over the payload bytes: a
    torn record is a prefix, so a key that is absent from it may still have been
    present in the record being written (this is why ``EventRecorded`` is not in
    the set).
    """
    if has_truncated_type_value(raw):
        return True
    return record_types_from_partial(raw)[0] not in TORN_TAIL_HARMLESS_EVENT_TYPES


def torn_tail_revival_records(raws) -> list[str]:
    """The dropped torn-tail records whose loss can REVIVE state (#3316)."""
    return [r for r in raws if torn_record_may_revive_state(r)]


def describe_torn_tail_revival(revival_records) -> str:
    """The legible record types of *revival_records*, for an operator message.

    EVERY legible type per record, not just the first, plus ``<unreadable>``
    when the type is not unambiguous: classification refuses a record whose type
    could not be established, so a message naming only a surviving nested type
    would contradict the decision it reports. The state such a record dropped
    cannot be identified, so it is reported as such rather than omitted.
    """
    kinds: set[str] = set()
    for raw in (revival_records or []):
        if has_truncated_type_value(raw) or not record_types_from_partial(raw):
            # Either the record carried no type at all, or a type KEY whose
            # VALUE did not survive: the state it dropped cannot even be
            # identified, so it is reported as such rather than as the
            # remaining legible type alone.
            kinds.update(record_types_from_partial(raw) or [])
            kinds.add("<unreadable>")
        else:
            kinds.update(record_types_from_partial(raw))
    return ", ".join(sorted(kinds))


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
        "refusing to replay: the journal's torn trailing record cannot be "
        f"proven harmless (type(s): {describe_torn_tail_revival(revival)}); "
        "a truncated record cannot be reconstructed, so replaying without it "
        "could resurrect state its fold would have removed (#3316). "
        "No record was replayed: the graph was NOT rebuilt. Repair or "
        "truncate the journal, then retry."
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
        (``self.torn_trailing_count``) and this READ never raises — a raised
        parse would kill the very recovery tool (``rebuild_all`` /
        ``_auto_health_recover``). The tolerance is for the data-LOSS
        direction and is qualified by #3316: a torn tail whose loss could
        REVIVE state is still skipped here (the file is not rejected and not
        modified) but is REFUSED by the REPLAY, which consults
        :attr:`torn_trailing_raw` through
        :func:`torn_record_may_revive_state` BEFORE any wipe or fold.
        A malformed MID-FILE line is a separate corruption class (not a torn
        append) and raises an actionable error naming the file and line.

        The raw text of every skipped trailing line is kept, UNCAPPED, in
        :attr:`torn_trailing_raw` so a replay engine can tell a harmful tear
        from a harmless one (:func:`torn_record_may_revive_state`);
        :attr:`torn_trailing_count` remains the count. Both are reset on EVERY
        call (including a missing file), so a reused ``EventLog`` never
        reports a previous call's tear.

        Lines are split on ``"\n"`` — the byte ``append`` terminates a record
        with. ``str.splitlines()`` would ALSO split on U+2028/U+2029/U+0085,
        which ``json.dumps(..., ensure_ascii=False)`` writes RAW inside a
        content string, so a valid single-line record would be read as two and
        the first fragment would look like mid-file corruption.
        """
        import logging
        self.torn_trailing_count = 0
        self.torn_trailing_raw: list[str] = []
        if not self.path.exists():
            return []
        out = []
        text = self.path.read_text(encoding="utf-8")
        if text.endswith("\n"):
            # Exactly one terminator: the byte `append` writes. Dropping it
            # keeps the torn-tail rule identical whether or not the last
            # record's newline survived the crash (a malformed final line is a
            # torn tail either way), WITHOUT the U+2028/U+2029/U+0085 splits
            # `str.splitlines()` would introduce below.
            text = text[:-1]
        lines = text.split("\n")
        for idx, raw in enumerate(lines):
            line = raw.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                if idx == len(lines) - 1:
                    self.torn_trailing_count += 1
                    # The FULL line, UNCAPPED. Classification is conservative
                    # over EVERY legible type, so a cap would let a
                    # non-allowlisted type hiding BEYOND it ride an allowlisted
                    # one before it — fail OPEN, the exact shape the allowlist
                    # polarity exists to prevent. ``line`` already exists here,
                    # so retaining it costs no extra allocation.
                    self.torn_trailing_raw.append(line)
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
