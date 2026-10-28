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
record index into the log.  Callers obtain a cursor from :meth:`cursor_at_end`
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
import contextlib
import errno
import inspect
import io
import json
import logging
import os
import re
import stat
import threading
import time
import weakref
from pathlib import Path

try:                      # advisory locking is POSIX-only; Windows has none
    import fcntl
except ImportError:       # pragma: no cover - import-time platform branch
    fcntl = None          # type: ignore[assignment]

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
# NOR CLEAR an authoritative property — a ``SET n.x = $x`` whose payload value
# is a legitimate ``null`` IS a removal (the live write cleared it), even
# though nothing is deleted (see ``PointRevised`` below). The owned-null
# ``embedding`` clear is such a removal too, NOT a recompute: writing it away
# RESURRECTED the vector an earlier record set (``_upsert_point_props``'
# ``embedding_clear`` arm, projection/entities.py:719-730 → :772, whose own
# ``#5004 round-6`` comment records that bug). The OTHER embedding writes are a
# genuine recompute cache clear: a ``REMOVE n.embedding`` gated on a NEW vector
# being written, in the Point fold (entities.py:853), the Subject/Object upserts
# (:1650, :1713-1719), the Document fold (:1951) and the Event fold (:2127).
# A member with no fold at all is a proof-carrying no-op. A torn tail whose
# type is legible and wholly inside this set keeps the pre-existing tolerance.
# EVERYTHING ELSE is refused: an unlisted type and an
# unreadable type cannot be proven harmless, and a silent resurrection is worse
# than a loud refusal — the refusal touches no graph and leaves the journal for
# the operator.
#
# ONE DISCLOSED EXCEPTION, AND ITS BOUNDARY — read before trusting the criterion
# above. Root cause in one line: a fold that writes an authoritative value
# STRAIGHT from the record's payload is payload-dependent, and a torn prefix
# cannot prove that payload absent.
#   * ``PointAdded`` / ``OperatorAdded`` are members, but their fold reaches
#     ``n.status = coalesce($st, n.status, 'live')`` with ``$st`` taken from the
#     payload (projection/entities.py:770, :795) and ``create_point`` accepts
#     every value in ``POINT_STATUS_VALUES`` — a BORN-TERMINAL
#     create (``_born_terminal``, a first-class case: see #2422)
#     journals ``status`` in its ``PointAdded`` point snapshot. A
#     torn born-terminal ``PointAdded`` for an id an EARLIER record left live is
#     therefore a resurrection this classifier tolerates. ``n.content=$content``
#     and the operator ``n.direction=$dir`` (entities.py:766, :825) are the same
#     family on the same re-create composition, and so is an owned-null
#     ``embedding`` (re-capture of a deterministic turn id — the
#     ``f"{session_id}_t{i}"`` form ``sdk._capture_turn_id`` mints — with
#     changed content and nothing
#     encoded, whose snapshot journals the read-back ``None``):
#     the replay then keeps a stale vector the live write cleared. It is NOT a
#     first-order path (unlike ``EventRecorded``'s connector leg, which every
#     connector ingest reaches, or ``SessionRecorded`` above): every arm needs a
#     SECOND ``PointAdded``/``OperatorAdded`` for an EXISTING id, and excluding
#     the two types would refuse the most common torn record of all.
#     Born-terminal creates are a supported, behaviorally pinned write
#     (tests/test_2952_write_path_determinism.py::test_born_terminal_point_is_not_a_dirty_root),
#     which is why this is a disclosure and not a mistake. Filed as #5921 with
#     the reproductions. The SAME root cause also reaches the
#     ``PointPromoted`` / ``OperatorPromoted`` ``get_point`` snapshots, which
#     share ``_upsert_point_props``; no writer path can put a terminal status in
#     one (promotion is terminal-guarded), so the reachable composition is the
#     create path above.
#
# ⛔ WHY THE FOUR REMAIN WHILE ``PointRevised`` / ``OperatorAnnotated`` ARE
# REFUSED — the boundary, since it is not "which fold can clear". Those two
# write whatever props the caller passed on a record that ONLY updates, so a
# ``null`` clear is part of the record's ORDINARY payload shape with no second
# event required. Here the clear needs a same-id RE-CREATE, so the ordinary
# payload of the type is still a creation. Removing these four would instead
# refuse the dominant torn shape of an ingest journal (a crash mid-append), i.e.
# turn the crash-resume tolerance of epic #900 S15/T12 — a recorded decision
# (tests/test_index_restore.py::test_s15_torn_tail_journal_rebuilds_to_crash_free_state,
# tests/test_index_directory.py::test_e2e18_hard_crash_sigkill_resume) — into a
# blocked recovery that then loses the fragment anyway. That is a DECISION, not
# a bug fix: the durable fix is writer-side (give the owned-null clear its own
# refused record type, or stop journaling an owned-null ``embedding`` on a
# re-create), which is what #5921 asks for. Reopen #5921 before widening this
# refusal.
#
# ⛔ THE POLARITY IS DELIBERATE: a NEW event type defaults to REFUSED, not
# tolerated. Adding one here is the claim that its loss cannot revive state —
# check its fold in ``tortoise.projection`` first. The inverse (listing the
# removal types instead) fails OPEN: a new terminal type would silently
# resurrect, which is exactly the defect class this exists to close.
TORN_TAIL_HARMLESS_EVENT_TYPES = frozenset({
    # Point / operator lifecycle additions (MERGE + SET). ``PointAdded`` /
    # ``OperatorAdded`` also carry a payload-dependent write — see the
    # disclosed exception below.
    "PointAdded", "OperatorAdded",
    "PointPromoted", "OperatorPromoted",
    # Object / subject lane additions (MERGE + SET; an object or subject
    # upsert clears the DERIVED embedding so it is recomputed).
    "ObjectRegistered", "SubjectAdded",
    # Source lane additions (MERGE + SET only). `SourceVersioned` is the
    # #5024 T6 re-materialisation record: `_fold_source_versioned` delegates to
    # the IDENTICAL `_upsert_source` fold `SourceCreated` already rides
    # (MERGE + SET, hash-diff-gated, no removal), so its loss cannot revive
    # state by the module's own criterion. Before #5024 that same transition
    # was journalled as `SourceCreated`, whose torn tail was tolerated —
    # refusing the new type would be a silent tightening of the recovery
    # posture (it blocks `rebuild_all`/`recover_from_log`/`backup.restore`).
    "SourceCreated", "SourceVersioned",
    # Bookkeeping records: ``_NO_PROJECTION_FOLD`` / ``_NO_POINT_FOLD``
    # (projection/__init__.py) — recognized and INTENTIONALLY not folded by any
    # dispatcher, so their loss is a proof-carrying NO-OP on the graph.
    # Refusing them would be pure over-refusal on a record that is often the
    # FIRST line of an ingest.
    #
    # ⛔ ``DirectEdgeCreated`` is listed here but its loss is NO LONGER a
    # no-op: #8499 landed a real `rebuild_all` pass-2b fold for it (it used to
    # sit unfolded in `_NO_PROJECTION_FOLD`), so a TORN FINAL LINE now costs
    # that one edge its replay. It stays tolerated — refusing a torn tail
    # would block `rebuild_all`/`recover_from_log`/`backup.restore` for a
    # journal whose last write was merely cut off, and the loss is bounded to
    # the torn record itself (the edge was written live; only its descriptor
    # is missing) — but the tolerant posture now has a real, named cost
    # instead of being free. The one-record ``apply()`` engine still does not
    # fold it (`_APPLY_WARN_ONLY_TYPES`), so for `rebuild(log)` the no-op
    # claim still holds.
    "BatchIdStamped", "IngestStarted", "CalibrationRecorded",
    "DedupeRecorded", "DedupeRejected", "EntityBindingRefused",
    "DirectEdgeCreated",
    # #9555/R7: §9.1's narrative carrier, which is in `_NO_PROJECTION_FOLD`
    # — no dispatcher folds it, so its loss is the same proof-carrying
    # NO-OP on the graph as the bookkeeping records above. ⛔ The polarity
    # here is deliberate ("a NEW event type defaults to REFUSED"), so
    # omitting it is not neutral: a commit-path append cut off mid-line (the
    # S15 case this tolerance exists for) would then make `tortoise
    # rebuild`, `recover_from_log` and `backup.restore` REFUSE an otherwise
    # intact journal — measured on a SIGKILL-truncated tail in PR #9862's
    # review. The record-side cost is that one capture's narrative cannot
    # be replayed from that line, which is bounded to the torn record
    # itself: the live row was written before the tear, and a torn line
    # could not be replayed anyway.
    "SessionNarrated",
    # #10017 R2: §9.1's raw-turn carrier, TOLERATED — and this is step 7's
    # SETTLED answer to the placeholder #10129 declared (`TURNS_RECORD_TYPE`'s
    # "Deliberately NOT harmless" paragraph above states the open question and
    # names the two things that decide it: the WRITE ORDER, and whether the
    # turns survive in the record when the append tears). Both measured:
    #
    #   * WRITE ORDER — `sdk._emit_event` returns on an append failure
    #     (`return False`) BEFORE `_write_record_projection` runs, so a torn
    #     carrier line means that capture's turn rows never reached the record
    #     EITHER. Refusing the tail recovers nothing for it — the transcript is
    #     already lost — while `recover_from_log` then reports
    #     `recovered=False, nodes=0` for the WHOLE journal (measured in the
    #     placeholder's note). That is a strictly larger loss than the one
    #     capture it protects, and it is the SIGKILL-mid-append shape #9555
    #     added the narrative carrier to stop.
    #   * POLARITY — the carrier is ADDITIVE: it creates rows and never removes
    #     or re-states any. The state a tolerated torn tail could revive is a
    #     REMOVAL's opposite (this list exists because a torn `EntityMutated`
    #     op=delete would resurrect a deleted turn), and an additive type cannot
    #     express a revival at all. Its loss is bounded to the capture whose
    #     append tore.
    #
    # ⛔ NOT the #10017 R1 removal record (`EntityMutated` op=delete, tier
    # `record_only`). A tolerated torn DELETE is a resurrection — the record's
    # removal would be lost and the replayed `PointAdded` would stand. That
    # asymmetry is the whole reason this list is an ALLOW-list of additive types
    # rather than a deny-list of removals.
    "TurnsRecorded",
    # Additive edge / prop writes (MERGE + SET only).
    "EntityLinked",
})
# Deliberately NOT harmless although the name reads as an addition or an
# update:
#   PointRevised — its fold WRITES a payload property with a plain
#                  ``SET n.{key} = ${key}`` (PointRevised belief props
#                  projection/__init__.py:6734-6736, in-memory :2082-2083;
#                  the point's content :6660-6712; the annotator dims :6717),
#                  and ``None`` is a LEGITIMATE payload value that CLEARS — the
#                  code says so at ``_belief_prop_value_ok``
#                  (projection/entities.py:104-106, "``None`` is VALID for
#                  every key — it is the journaled CLEAR … dropping it would
#                  leave a stale prior in place across a rebuild") and at
#                  ``_annotator_value_ok`` (projection/__init__.py:1790-1792,
#                  the same sentence for ``annotate_operator``/``update_point``
#                  with ``x=None``). ``update_point(id, confidence=None)``
#                  journals ``"confidence": null``, so a
#                  torn ``PointRevised`` that drops a CLEAR leaves the stale
#                  prior live and the rebuild disagrees with the live graph.
#                  Unlike ``EventRecorded`` this needs no prefix-inference
#                  argument: the clear is visible IN THE SURVIVING PREFIX when
#                  it is the torn key, and the type alone cannot exclude it.
#   OperatorAnnotated — same shape: ``set_clauses = [f"n.{key} = ${key}" for
#                  key in dims]`` (projection/__init__.py:6585), and
#                  ``annotator_bias=None`` / a dim of ``None`` is the journaled
#                  CLEAR (``_annotator_value_ok``).
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
#                     the capture's TRAILING records with
#                     ``capture_ok=False`` on a failed or keyless capture. A
#                     dropped tear restores ``capture_ok=NULL``, which
#                     ``hosted_api`` reads as the legacy "presumed captured"
#                     replay case (hosted_api.py:10041-10065) — so the failed
#                     session silently STOPS being re-attempted while recovery
#                     reports success. Same root cause as #5921 (a payload-only
#                     lifecycle property), but its harmful payload is COMMON and
#                     it is a trailing record, so it is refused outright.
#   TurnsRecorded — SETTLED BY STEP 7 IN FAVOUR OF TOLERATING IT (see the entry
#                  in the set above for the measurement: the write order makes a
#                  torn carrier already-lost, and refusing makes the WHOLE
#                  journal unrecoverable — `recover_from_log` returns
#                  `recovered=False, nodes=0` — for a capture that reached no
#                  store either way). It is ADDITIVE, so the revival class this
#                  list exists for cannot be expressed by it. #10129 declared it
#                  a PLACEHOLDER precisely so step 7 would settle it here rather
#                  than inherit it.
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
# A ``"type"`` key torn MID-TOKEN at the end of the line (``"t`` / ``"ty`` /
# ``"typ`` / ``"type``). Used only with
# :func:`_torn_type_key_at_end_of_line`, which requires the fragment to sit in
# a KEY position of an OBJECT — see that helper for why a bare delimiter test
# over-refuses (an array ELEMENT follows a ``,`` too).
_TORN_TYPE_FRAGMENT_RE = re.compile(r'"t(?:y(?:p(?:e)?)?)?$')


def _unclosed_openers(raw: str) -> list[str]:
    """The ``{`` / ``[`` characters still open at the end of *raw*.

    A minimal scanner: string literals (and their ``\\`` escapes) are skipped,
    so a brace inside a value cannot shift the depth. Truncation is expected —
    an unterminated string simply stops the scan.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    for ch in raw:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]" and stack:
            stack.pop()
    return stack


def _torn_type_key_at_end_of_line(raw: str) -> bool:
    """True when *raw* ends in a ``"type"`` key torn mid-token (#3316).

    Both conditions are needed, and each closes an over-refusal:

    * the fragment must sit after a ``,`` or ``{`` rather than a ``:`` — the
      latter is a torn string VALUE (``"id": "t``), not a torn key;
    * the innermost still-open container must be an OBJECT. A ``,`` also
      precedes an array ELEMENT, and ``{"point": {"tags": ["a", "t`` is a
      torn tag, not a torn key — refusing it would turn a routine crash
      mid-array into a full replay refusal.
    """
    m = _TORN_TYPE_FRAGMENT_RE.search(raw)
    if m is None:
        return False
    prefix = raw[:m.start()].rstrip()
    if not prefix or prefix[-1] not in ",{":
        return False
    stack = _unclosed_openers(prefix)
    return bool(stack) and stack[-1] == "{"


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
      ``"typ`` / ``"type`` with no colon), in an OBJECT's key position — a torn
      string VALUE that happens to start ``"t`` (``"id": "t``) and a torn
      ARRAY element (``["a", "t``) are both not one.

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
    return _torn_type_key_at_end_of_line(raw)


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
        "refusing to replay: the journal's damaged record line(s) cannot be "
        f"proven harmless (type(s): {describe_torn_tail_revival(revival)}); "
        "a damaged record cannot be reconstructed, so replaying without it "
        "could resurrect state its fold would have removed (#3316). "
        "No record was replayed: the graph was NOT rebuilt. Find and repair "
        "the torn record — an append seals one mid-file, so it is not always "
        "at the end of the file — then retry."
    )


# The sealed-fragment sentinel (#5917). A torn record is terminated and
# annotated with this line by the NEXT ``append``, so the fragment stops
# merging into the following record and ``read_all`` can still classify it.
# DELIBERATELY not JSON: every record ``append`` writes begins ``{"``, so a
# sentinel that no record can begin with makes a TORN SEAL unambiguous — a
# `{"` record fragment can never be matched as one, which is what keeps it
# counted and classified instead of silently skipped (the fail-open direction
# #3316 forbids). The journal's grammar already admits non-JSON lines (a torn
# fragment is one), and the other direct line readers
# (``subject_binding``, ``backup``) already skip an unparseable line.
# Append-only is preserved: sealing writes bytes, it never rewrites.
SEAL_SENTINEL = "__TornTailSealed__"


def is_seal_annotation(text: str) -> bool:
    """True when *text* is the seal annotation line (never a record)."""
    return text == SEAL_SENTINEL


def _is_seal_prefix(text: str) -> bool:
    """True when *text* is the annotation, complete or TORN (a prefix of it).

    Only ever used for the line FOLLOWING a malformed fragment: the seal is
    written by the same non-atomic write whose tearing this module already
    tolerates, so a crash can leave `__Torn` where the complete annotation was
    intended. Without the prefix arm that journal is unreadable FOREVER — the
    fragment sits mid-file with an unrecognizable successor, and no later
    ``append`` repairs it, because each seal adds a complete annotation one
    line further down (measured).

    The sentinel is deliberately NOT JSON (see :data:`SEAL_SENTINEL`), so no
    prefix of it can be a prefix of a record and this test cannot swallow a
    torn record fragment.
    """
    return bool(text) and SEAL_SENTINEL.startswith(text)


def _decode_line(raw: bytes) -> str | None:
    """The line's text, or ``None`` when it is not valid UTF-8."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _classifier_text(raw: bytes) -> str:
    """What the #3316 classifier sees for a malformed line.

    Undecodable bytes become U+FFFD: a broken byte makes a type unreadable,
    and an unreadable type is refused rather than assumed harmless.
    """
    return raw.decode("utf-8", errors="replace").strip()


# ── append serialisation (#5612) ─────────────────────────────────────────
#
# The hosted lane appends to ONE per-graph journal from a bounded threadpool
# (``_graph_offload`` / ``asyncio.to_thread``), so two ``append`` calls for the
# same graph really do run at once. Two writers interleaving inside one record
# produce a malformed line that is NOT a torn tail: ``read_all`` refuses
# mid-file corruption, so a single interleave would permanently break
# ``rebuild_all`` / ``recover_from_log`` — the tools the journal exists for.
#
# WHAT IS RELIED ON, stated so a reviewer can check it (measured, see the
# commit):
#
# 1. ONE ``write(2)`` PER RECORD. ``append`` used to hand the record to
#    ``BufferedWriter``; that is one syscall only while the record fits the
#    writer's buffering, and the buffered path is CPython-version-internal. A
#    raw fd written with a single ``os.write`` removes the question.
# 2. POSIX ``O_APPEND``. The kernel assigns the offset AND copies the bytes
#    under the inode lock for a regular file on a local filesystem, so two
#    concurrent appenders never interleave WITHIN one ``write(2)``. This is the
#    guarantee the concurrency test pins; it is NOT the pipe ``PIPE_BUF`` rule
#    (that bound applies to pipes/FIFOs, where a write above ``PIPE_BUF`` may be
#    split — a journal is a regular file, so the record length does not bound
#    it).
# 3. A LOCK ON TOP, because (1)+(2) are not enough everywhere: a short write
#    (ENOSPC mid-record, a signal) leaves a fragment, and on a filesystem whose
#    ``O_APPEND`` is not atomic (NFSv3, some FUSE/CSI network volumes) two
#    writers can interleave even with one syscall each. ``flock`` serialises
#    across PROCESSES where the OS supports it; the in-process lock serialises
#    threads (and keeps working where ``flock`` is unsupported). The lock also
#    covers the READ-MODIFY-WRITE in :meth:`EventLog._seal_prefix`: unserialised,
#    a writer that read the tail before a peer's record landed attaches its
#    seal to the PEER's record, leaving a genuine torn fragment unmarked
#    mid-file. MEASURED on the unmodified writer: 938 stray ``SEAL_SENTINEL``
#    lines over 27,000 appends (~3.5%) at 8 KiB records, rising with the record
#    size (~7% at 20 KiB) — the same window that can strand a fragment. Zero
#    with the lock.
#
# ``_APPEND_LOCKS`` is bounded by the number of DISTINCT journals this process
# appends to (at most the live graphs), and is deliberately never pruned: a lock
# a waiter already holds a reference to cannot be identified from the map, so
# evicting one could hand two threads two different locks for one path and
# silently drop the serialisation this block exists for. The THREAD-lock state
# is reinitialised in a FORKED CHILD (see ``_reinit_append_locks_after_fork``):
# a lock held by another thread at ``fork()`` time has no owner on the other
# side, so inheriting it would wedge the child on its first append for ever.
# WHAT THAT DOES NOT COVER, stated so it is not overclaimed: a child forked
# while a thread is INSIDE an append inherits a reference to the same OPEN FILE
# DESCRIPTION, so the parent's ``flock`` stays held until the CHILD exits (an
# advisory lock is released at the last close, not the first). That window is
# fork-during-append, which nothing under ``tortoise/`` does today; closing the
# child's inherited copies is the remaining half, and bounding the journal's
# growth is #9025.
_APPEND_LOCKS: dict[str, threading.Lock] = {}
_APPEND_LOCKS_GUARD = threading.Lock()
# BOUNDED cross-process wait. A peer that holds the lock and is wedged (SIGSTOP,
# a hung network mount) must not park a hosted capture worker forever — the same
# reason ``tortoise/index_lock.py`` takes ``LOCK_NB`` and attributes its holder.
# On timeout the append PROCEEDS on the single-write ``O_APPEND`` guarantee
# (which still holds for a regular file on a local filesystem) and says so at
# WARNING: a stall must not silently become an outage, and a timeout must not
# silently become a lost record.
_FLOCK_TIMEOUT_S = 5.0
_FLOCK_POLL_S = 0.001
# The bound is per INCIDENT, not per append. Once a peer has been observed
# holding the lock past the deadline, later appends skip the wait (and the
# WARNING) for this long and go straight to the write guarantee: otherwise one
# wedged peer costs every queued append the full deadline on the hottest path
# the lock guards, and logs a WARNING per append for ever. Keyed by resolved
# journal path; single assignments, so the GIL is enough (the reader also holds
# that path's thread lock).
_FLOCK_UNHEALTHY_FOR_S = 30.0
_FLOCK_UNHEALTHY: dict[str, float] = {}
# The write seam: one attribute, so a test can pin "exactly one write(2) per
# record" without patching the global ``os`` module out from under the
# interpreter.
_OS_WRITE = os.write

# Advisory-lock failures that mean "this filesystem does not do advisory
# locks", not "the append is broken". Degrading to the single-write guarantee
# is the documented posture; any OTHER errno propagates.
_FLOCK_UNSUPPORTED = frozenset({
    errno.EOPNOTSUPP, errno.ENOLCK, errno.ENOSYS, errno.EINVAL,
})


def _reinit_append_locks_after_fork() -> None:
    """Drop the INHERITED append-lock state in a forked child (#5612).

    ``threading.Lock`` state is copied into a forked child. If any thread held
    a journal's lock (or ``_APPEND_LOCKS_GUARD``) at the instant of ``fork()``,
    the child's copy is permanently locked with no surviving owner, and the
    child's first ``append`` — on the capture/delete path, and reachable from a
    prefork server that warms up before forking — blocks FOREVER.

    Clearing is safe in the child precisely because only the forking thread
    survives: no peer can be holding one of these locks there. The same remedy
    is already registered in ``tortoise/embedded_lifecycle.py``.

    The child's inherited OPEN FILE DESCRIPTORS are out of scope here — see the
    ``_APPEND_LOCKS`` block for what that leaves open.
    """
    global _APPEND_LOCKS_GUARD
    _APPEND_LOCKS.clear()
    _FLOCK_UNHEALTHY.clear()
    _POSITION_CACHE_DEAD.clear()
    _UNPROVEN_SEAL_REPORTED.clear()
    _APPEND_LOCKS_GUARD = threading.Lock()


# POSIX only: Windows has no ``fork``, so there is nothing to reinitialise.
if hasattr(os, "register_at_fork"):        # pragma: no branch - platform
    os.register_at_fork(after_in_child=_reinit_append_locks_after_fork)


def _append_lock(key: str) -> threading.Lock:
    """The process-wide lock for a journal, keyed by its RESOLVED path.

    Resolved by the caller (:meth:`EventLog._lock_key`), so two spellings of
    the same journal (a symlinked ``/data`` mount, a relative path) contend on
    ONE lock instead of two — and so the resolution is not repeated on every
    append.
    """
    with _APPEND_LOCKS_GUARD:
        lock = _APPEND_LOCKS.get(key)
        if lock is None:
            lock = _APPEND_LOCKS[key] = threading.Lock()
        return lock


def _lock_exclusive(fd: int, key: str) -> bool:
    """Serialise against OTHER PROCESSES, where the OS can. Returns whether it did.

    The return value exists for the journal POSITION (#9190): ``True`` means
    the cross-process lock was acquired here. That is NECESSARY but not
    sufficient for an exclusive count — a peer already inside the
    ``_FLOCK_UNHEALTHY`` window writes without contending at all, so the
    position also checks the record's own byte offset after the write (see
    ``EventLog.append``). ``False`` means *this call did NOT hold the
    cross-process lock* — either the wait timed out ("WHAT A TIMEOUT GIVES UP"
    below) or the path is inside the ``_FLOCK_UNHEALTHY`` window, where it does
    not even try. So a count taken now could be taken by a peer at the same
    instant, and a position derived from it could be handed out twice. It is NOT
    evidence that a peer holds the lock: in the window branch nobody may. Callers that merely append a
    record ignore this (the write-side guarantee still holds); the position
    must not.

    ``flock`` on the journal fd itself (released by ``os.close``). Two fds on
    the same file contend even inside one process, so this arm also covers a
    fork; :func:`_append_lock` covers the threads. A filesystem without
    advisory locks is not an error — the single-``write`` ``O_APPEND``
    guarantee still holds there.

    *key* is the ALREADY-RESOLVED journal path (:meth:`EventLog._lock_key`),
    passed in rather than recomputed: the append path calls ``realpath`` once
    per journal (memoised on the instance), which is the whole reason that memo
    exists.

    The wait is BOUNDED (``_FLOCK_TIMEOUT_S``) and non-blocking-polled, because
    an unbounded ``LOCK_EX`` lets one wedged peer park every appender to that
    journal with nothing distinguishing the stall from a slow disk. On timeout
    the append proceeds on the write-side guarantee and logs a WARNING — and
    the path is marked UNHEALTHY for ``_FLOCK_UNHEALTHY_FOR_S``, so the cost is
    paid once per incident instead of once per queued append.

    WHAT A TIMEOUT GIVES UP, stated so it is not overclaimed: the seal's
    read-modify-write is no longer serialised against a peer (a peer that
    appends during it can make this writer attach its seal to the peer's
    record), and on a filesystem whose ``O_APPEND`` is not atomic there is no
    cross-process serialisation at all. That is the deliberate trade — a wedged
    peer must not become an outage — and it is why the warning says so. It is
    also exactly why this function reports whether the lock was held.
    """
    if fcntl is None:                  # pragma: no cover - platform branch
        # No advisory locks on this platform, so exclusivity is never held —
        # and the position must say so rather than assume it.
        return False
    stamp = _FLOCK_UNHEALTHY.get(key)
    now = time.monotonic()
    if stamp is not None:
        if now - stamp < _FLOCK_UNHEALTHY_FOR_S:
            return False
        _FLOCK_UNHEALTHY.pop(key, None)
    deadline = now + _FLOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as exc:
            if exc.errno in _FLOCK_UNSUPPORTED:
                return False
            # ``EINTR`` is TRANSIENT here, not an append failure: the poll
            # loop makes thousands of non-blocking ``flock`` calls exactly
            # while a peer holds the lock, i.e. exactly when a signal (SIGCHLD
            # from a reaped worker, a timer) can interrupt one. ``fcntl.flock``
            # is NOT in PEP 475's retry-on-EINTR set, so an unhandled ``EINTR``
            # would propagate out of ``append`` as a lost record — a failure
            # class this lock introduced. Retrying costs nothing and does not
            # consume the deadline meaningfully.
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EINTR):
                raise
            now = time.monotonic()
            if now >= deadline:
                _FLOCK_UNHEALTHY[key] = now
                logging.getLogger(__name__).warning(
                    "EventLog %s: another process held the append lock for "
                    "%.1fs — proceeding WITHOUT it on the single-write "
                    "O_APPEND guarantee until it is released: a regular file "
                    "on a local filesystem still cannot interleave within one "
                    "write, but the tail check stays unprotected and a "
                    "filesystem whose O_APPEND is not atomic gets no "
                    "cross-process serialisation at all. Not retrying for "
                    "%.0fs.",
                    key, _FLOCK_TIMEOUT_S, _FLOCK_UNHEALTHY_FOR_S)
                return False
            time.sleep(min(_FLOCK_POLL_S, max(0.0, deadline - now)))


def _write_record(fd: int, payload: bytes) -> None:
    """Emit one whole record with ONE ``write(2)`` on an ``O_APPEND`` fd.

    The loop exists ONLY for a partial write — a regular file returns one on
    ENOSPC, ``EFBIG``, or an interrupted syscall, i.e. the failure class that
    already leaves a torn record behind, so it cannot be papered over here and
    must not be silently dropped either. On the normal path
    ``os.write`` returns the full length and this is exactly one syscall; that
    is what makes the record un-interleavable (see the ``_APPEND_LOCKS``
    comment for the guarantee and its limits).
    """
    view = memoryview(payload)
    while view:
        written = _OS_WRITE(fd, view)
        if written <= 0:                 # pragma: no cover - defensive
            # A regular file does not return 0 for a non-empty buffer; looping
            # would spin forever if it ever did.
            raise OSError(f"short write to the journal: {written} bytes")
        view = view[written:]


def _read_exactly(f, want: int) -> bytes:
    """Read *want* bytes, looping over short reads.

    The journal fd is an UNBUFFERED ``io.FileIO``, whose ``read(n)`` is ONE
    ``read(2)`` and may return FEWER than *n* bytes (``RawIOBase`` makes no
    completeness promise). The tail scan below MUST NOT assume otherwise: a
    short read would silently skip the bytes between the window edge and the
    bytes actually returned — the very bytes that decide whether the tail is a
    fragment — and could fabricate a seal or leave a real one unmarked on the
    network/FUSE volumes this append path is hardened for.
    """
    parts: list[bytes] = []
    got = 0
    while got < want:
        more = f.read(want - got)
        if not more:                     # EOF: nothing left to consume
            break
        parts.append(more)
        got += len(more)
    return b"".join(parts)


# ── the journal POSITION — the ordinal a live write needs (#9190) ────────
#
# `public.journal_event` keys the position a record consumed on
# `(org_id, graph_id, seq)`, and the schema calls `seq` *"the idempotency key:
# applying a seq twice is a no-op"*. A FULL REPLAY derives it from the record's
# index in the read (`record_projection.replay_plan`'s
# `enumerate(records, start=1)`). The LIVE path had no ordinal at all, so a
# writer could only guess `MAX(seq)+1` — a number a later replay would either
# collide with (`ON CONFLICT DO NOTHING` silently absorbs the replay's position
# row) or duplicate (one event recorded under two seqs). Both are silent, which
# is the class #9078 exists to remove.
#
# THE SOURCE IS THE JOURNAL ITSELF. A record's position is its 1-based index
# among the records a REPLAY would read — `read_all(salvage=True)`, the ONE
# reader the replay engines use (`read_for_replay`) — so a live write and a full
# replay number the same record the same way, by construction rather than by
# agreement. Seals and skipped damage contribute no index there, so they
# contribute none here either.
#
# The sidecar is only a CACHE of reading the journal, and it never has to be
# trusted. It records the FILE STATE it describes -- byte size AND `mtime_ns`,
# taken from one `fstat` -- so a cache that does not describe this exact state
# sends the count back to the journal instead of answering for it:
#
#   * the journal is append-only (nothing rewrites or truncates a record), so
#     any append changes the size and an append by an OLDER writer (one that
#     does not maintain the sidecar), a rotated/restored journal, or a
#     half-written cache all fail the check and cost a RECOUNT;
#   * a REPLACEMENT that happens to land on the same byte length -- the case a
#     restore can actually produce -- moves `mtime_ns`, so it fails too. The
#     residual is a same-length rewrite INSIDE ONE mtime tick: `mtime_ns` is
#     nanosecond-resolution on APFS but a jiffy (1-4 ms) on some ext4 mounts and
#     a whole SECOND on HFS+/FAT and many NFS/FUSE exports, so there a
#     replacement inside one tick is indistinguishable from the state the cache
#     describes. Named rather than papered over: only a full read can tell those
#     apart, and the cost of being wrong is a stale count (a wrong `seq`).
#   * a `position()` COUNT taken while a peer writes WITHOUT contending is behind
#     by whatever that peer wrote during the count — not by one record, and not a
#     `_FLOCK_UNHEALTHY` window's worth: the peer keeps appending for the whole
#     window. It is a separate residual from the cache's, because no cache is
#     consulted here: the count is fresh, and it is stale the moment it is
#     returned. The cache is left alone (the state it counted has moved); the
#     NUMBER cannot be, because `position()` has no `None` to give.
#
# The cache is written THROUGH on every append whose position is PROVEN (and by
# `position()`, when the state it counted is still current) -- the paths that
# report `None` deliberately leave it alone, because a cache entry is a statement
# about a count, and there is no count to state. Write-through is what a cache
# that lags cannot do, and a lagging cache is worth nothing in the shape that
# needs it: with more
# than one process appending, EVERY append moves the file, so every append is a
# miss and would recount the whole journal INSIDE the append's critical section.
# It is affordable only because
# the write is a `pwrite`+`ftruncate` on a descriptor this instance keeps open.
# Costs measured on the dev box 2026-10-15 (min of 7, microseconds): `pwrite`+
# `ftruncate` on a kept descriptor ~10; open/write/close ~110-140; `write_text`
# ~120-230. End to end, write-through adds ~25 us to a ~130 us append.
# See `EventLog._cache_fd`.

#: Suffix of the position cache beside a journal (`events.jsonl.position`).
POSITION_SUFFIX = ".position"

#: What the position's optional work is allowed to raise. Named once, because
#: every one of these sites has the same job: report an unproven position, never
#: fail the append. ``OSError`` covers the syscalls; ``MemoryError`` covers the
#: recount (it holds the journal, ~8x its size); ``RecursionError`` covers a
#: deeply nested record, which ``json.loads`` raises BEFORE ``ValueError`` is
#: even considered. Anything else is a bug in this module and must NOT be
#: swallowed here.
_COUNT_MAY_RAISE = (OSError, MemoryError, RecursionError)

#: The cache's own format version — change it if what a field MEANS changes, so
#: a sidecar written by a different reader is a miss rather than a misreading.
_POSITION_VERSION = 1

# How long a journal whose sidecar could not be MAINTAINED stops being recounted
# on the append path. The sidecar is the ONLY thing that makes the count O(1) on
# a journal another writer keeps moving, so a sidecar that cannot be written
# (a directory at its path, a read-only data dir, ENOSPC, EMFILE) turns every
# append into an O(journal) recount — the count holds the whole journal in RSS —
# taken INSIDE the append's critical section, on a journal that is unbounded
# (#9025). The append's whole job is the record; the position must not make it
# quadratic. So a failed cache write marks the path DEAD for this long and the
# append path reports ``None`` (a named absence, never a guess) instead of
# recounting; an explicit ``position()`` still recounts, because a measurement
# the caller asked for is not the hot path. The mark EXPIRES rather than being
# sticky, so a fixed permission or a freed directory recovers by itself, and one
# WARNING per episode is the signal an operator needs (a per-append one is noise
# they cannot act on). Keyed by resolved journal path, like
# ``_FLOCK_UNHEALTHY``; single assignments, so the GIL is enough.
_POSITION_CACHE_DEAD_FOR_S = 30.0
_POSITION_CACHE_DEAD: dict[str, float] = {}

#: Journals whose unproven-seal state has already been reported in this process.
#: A set, not a window: the condition is a property of the MOUNT, and repeating
#: it while it persists is noise, while a restart re-reports it once.
_UNPROVEN_SEAL_REPORTED: set[str] = set()


def _position_cache_path(path: Path) -> Path:
    return path.with_name(path.name + POSITION_SUFFIX)


def _position_cache_dead(key: str) -> bool:
    """Whether *key*'s sidecar failed to be maintained inside the retry window.

    Expiring on read (like ``_FLOCK_UNHEALTHY``) is what keeps the degradation
    bounded AND self-healing: past the window the next append recounts once,
    which either proves a position and repairs the cache or re-marks the path.
    """
    stamp = _POSITION_CACHE_DEAD.get(key)
    if stamp is None:
        return False
    if time.monotonic() - stamp < _POSITION_CACHE_DEAD_FOR_S:
        return True
    _POSITION_CACHE_DEAD.pop(key, None)
    return False


#: The errnos that make a sidecar PATH unusable — as opposed to this PROCESS
#: being briefly unable to reach it.
#:
#: The distinction is load-bearing: a mark costs the window's worth of positions,
#: so one transient ``EMFILE`` (fd-table exhaustion, cured by the next request
#: ending) must not take 30 s of ``seq`` away. ``EMFILE``/``ENFILE``, ``ESTALE``,
#: ``EIO`` and ``EBADF`` are therefore NOT markable: they cost a recount per
#: append — the pre-existing cost — until the blip passes, which fails SAFE.
#: ``ENOSPC``/``EDQUOT`` are markable because nothing frees space by itself, and
#: ``ESPIPE``/``ENXIO`` because a FIFO at the path is a standing shape (the open
#: fails with ENXIO when nobody is reading, the `pwrite` with ESPIPE when
#: somebody is).
_CACHE_PATH_ERRNOS = frozenset(
    {errno.EACCES, errno.EPERM, errno.EISDIR, errno.ENOTDIR, errno.EROFS,
     errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC), errno.ESPIPE,
     errno.ENAMETOOLONG, errno.ELOOP,
     # ENXIO: `O_WRONLY|O_NONBLOCK` on a FIFO nobody is reading. A FIFO at the
     # cache path is a standing shape, and it is what the FIFO tests pin.
     errno.ENXIO})


def _mark_position_cache_dead(key: str, path: Path, exc: BaseException) -> None:
    """Record a failed sidecar maintenance, and SAY SO once per episode.

    WARNING, unlike the per-append DEBUG in ``EventLog._refresh_position_cache``:
    this branch is not one missed optimization but a standing one — every append
    to this journal now reports an unproven position, which is the ``seq``
    coverage the whole position mechanism exists to provide. An operator has to
    be able to see that. Repeats inside the same episode stay at DEBUG, so the
    one line is not buried under a per-append warning.
    """
    first = not _position_cache_dead(key)
    _POSITION_CACHE_DEAD[key] = time.monotonic()
    if first:
        logging.getLogger(__name__).warning(
            "EventLog %s: the position cache beside it cannot be written (%s: "
            "%s), so this journal's records report NO position for the next "
            "%.0fs — recounting the journal on every append would hold it in "
            "memory inside the append's lock, and a journal is unbounded. "
            "Fix the sidecar path (a read-only data dir, a directory in the "
            "way, a full disk) and the positions come back by themselves.",
            path, type(exc).__name__, exc, _POSITION_CACHE_DEAD_FOR_S)


def _journal_state(f) -> tuple[int, int, int, int]:
    """``(size, mtime_ns, st_dev, st_ino)`` of the open journal — one ``fstat``.

    The first pair is what the position cache is validated against and the
    offset the append checks its own write against; the second pair is WHICH
    FILE that state belongs to (`_identity`), needed because the count opens the
    PATH and the record is written through this descriptor — see the identity
    comment in `append`. They come from ONE ``fstat`` on purpose: an append makes
    two of these calls (before and after the write), and a second syscall for the
    identity would be paid on every append to answer a question this one already
    answered. (``_seal_prefix`` still seeks to the end for the tail check — this
    does not replace that read.)
    """
    st = os.fstat(f.fileno())
    return st.st_size, st.st_mtime_ns, st.st_dev, st.st_ino


def _identity(st: os.stat_result) -> tuple[int, int]:
    """``(st_dev, st_ino)`` — the pair that says WHICH file a stat describes."""
    return st.st_dev, st.st_ino


def _path_identity(path: str | os.PathLike[str]) -> tuple[int, int] | None:
    """The identity of the file the PATH names now, or ``None``."""
    try:
        return _identity(os.stat(path))
    except _COUNT_MAY_RAISE:
        return None


def _report_unproven_seal(key: str, path: Path) -> None:
    """Say, ONCE per journal, that an append's seal could not be proven.

    A broken mount makes ``seek`` fail on EVERY append, and a WARNING per record
    is exactly the noise ``_FLOCK_UNHEALTHY`` and ``_POSITION_CACHE_DEAD`` exist
    to avoid; the returned ``None`` is the per-call signal, so the log carries
    the operator-actionable event once. A restart re-reports it.

    Called AFTER the record has landed, so it is TOTAL: a ``MemoryError`` from
    the set insert or a raising log handler must not turn a durable record into
    a reported failure whose retry duplicates it. (The condition it reports is a
    mount that cannot ``seek`` — not the environment to assume anything in.)
    """
    with contextlib.suppress(Exception):
        if key in _UNPROVEN_SEAL_REPORTED:
            return
        _UNPROVEN_SEAL_REPORTED.add(key)
        logging.getLogger(__name__).warning(
            "EventLog %s: the journal's tail could not be sized, so the record "
            "just appended carries NO seal: if the previous writer was killed "
            "mid-record, this record is glued onto that fragment and the next "
            "replay will skip BOTH. No position is reported for this record. "
            "(Reported once per journal.)", path)


def _read_regular_file(path: Path) -> bytes | None:
    """The file's bytes, or ``None`` if it is not a REGULAR file.

    ``O_NONBLOCK`` is load-bearing, not tidiness: its one caller
    (`_read_position_cache`) runs inside the journal's critical section (from
    ``append``, and from ``position()``) — the per-path THREAD lock, always held;
    the ``flock`` is conditional (see `_lock_exclusive`), so it is the thread
    lock, not the ``flock``, that keeps this from racing a peer in this process.
    A FIFO — or any other special file someone left at the cache path — would
    BLOCK the open or the read there, parking every appender to that journal in
    every process with nothing distinguishing it from a wedged peer. Non-blocking plus an
    ``S_ISREG`` check makes a special file a cache MISS instead: the cache is an
    optimization, so "not a regular file" is a reason to recount, never to wait.

    ``O_NOFOLLOW``: a symlink at the cache path is not a cache — we would be
    writing a JSON payload through an attacker-chosen link. A miss costs a
    recount.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            return f.read()
    finally:
        os.close(fd)


def _open_regular_for_write(path: Path) -> int:
    """An open descriptor for the cache, which must be a REGULAR file.

    Same reasoning as :func:`_read_regular_file` (``O_NONBLOCK`` so a FIFO
    cannot park the append's critical section, ``O_NOFOLLOW`` so a symlink
    cannot redirect the write), and it RAISES rather than returning ``None``:
    its caller (`EventLog._refresh_position_cache`) is the one total guard.
    """
    # No `S_ISREG` gate here, unlike the read side, and deliberately: `open`
    # already refuses a directory and a FIFO without a reader, `O_NOFOLLOW`
    # refuses a symlink, and a FIFO WITH a reader accepts the open but then
    # refuses the `pwrite` (ESPIPE) — measured. So a write-side gate would be a
    # branch whose outcome is identical either way, i.e. machinery nothing can
    # verify. (A device at this path WOULD accept the write, harmlessly: the
    # cache simply misses forever, which is a recount.)
    return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NONBLOCK
                   | getattr(os, "O_NOFOLLOW", 0), 0o644)


def _read_position_cache(cache: Path, size: int, mtime_ns: int) -> int | None:
    """The count a cache file holds for a journal in exactly this state, or ``None``.

    *cache* is the CACHE FILE (`_position_cache_path` of the resolved journal),
    not the journal — the suffix is applied once, at the caller.

    ``None`` means *the cache cannot speak for this file*: absent, unreadable,
    another version, or — the case that matters — describing a different file
    state. See the position block above for why ``(size, mtime_ns)`` is a sound
    identity here; a caller that gets ``None`` recounts, which is correct and
    merely costs a pass over the journal.
    """
    try:
        data = _read_regular_file(cache)
        if data is None:
            return None
        raw = json.loads(data)
    except (OSError, ValueError, RecursionError, MemoryError):
        # RecursionError: `json.loads` raises it (not ValueError) past ~10k
        # nesting, and a sidecar is a file anything may have written.
        # MemoryError: reading it holds the file (and its parse) in memory —
        # this sidecar is not size-bounded by us, and this call runs on the
        # append path, where the one thing that must not happen is losing a
        # record to a cache.
        return None
    if not isinstance(raw, dict) or raw.get("v") != _POSITION_VERSION:
        return None
    if raw.get("size") != size or raw.get("mtime_ns") != mtime_ns:
        return None
    count = raw.get("records")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    # SHAPE-BOUNDED, because the sidecar is a file anything may have written and
    # a flip in the `records` digits otherwise survives every check above (it
    # still describes this exact file state) and is believed verbatim for ever —
    # a fabricated `seq` no recount can ever correct. Every record costs at
    # least one byte of separator plus a delimiter, so a count above the byte
    # size is impossible for any journal this reader would produce: refuse it
    # and recount. (This bounds CORRUPTION, not tampering — a plausible-looking
    # forged count is the same class as the same-length replacement the POSITION
    # block names, and neither is worth a per-append digest.)
    if count > size:
        return None
    return count


def _write_position_cache(fd: int, records: int, size: int,
                          mtime_ns: int) -> None:
    """Refresh the cache through an ALREADY-OPEN descriptor.

    ``pwrite`` at offset 0 then ``ftruncate`` to the payload length: the pair
    costs ~8 us measured, against ~114 us for open/write/close and ~230 us for
    `write_text`, and this runs on the append path (see the POSITION block).
    The reader is another process, so a torn payload is expected and harmless —
    invalid JSON is a cache MISS, which recounts.

    It RAISES on failure, and that is deliberate: exactly one function in this
    module is allowed to swallow a cache failure (`EventLog._refresh_position_cache`,
    which is the one that runs after the record has landed), so the "a failed
    cache write must not fail the append" promise has ONE owner instead of two
    overlapping guards. Here, raising is the honest report.
    """
    payload = json.dumps({"v": _POSITION_VERSION, "records": records,
                          "size": size, "mtime_ns": mtime_ns},
                         separators=(",", ":")).encode("utf-8")
    written = os.pwrite(fd, payload, 0)
    os.ftruncate(fd, written)


def _close_quietly(fd: int) -> None:
    with contextlib.suppress(OSError):
        os.close(fd)


def _count_records(path: Path) -> int:
    """How many records a REPLAY reads from *path* — the ordinal's DEFINITION.

    Deliberately this module's own replay reader, on a THROWAWAY instance: the
    count must be the same number a replay derives with
    ``enumerate(records, start=1)``, and a second counting implementation is
    exactly how the two would drift. The throwaway also keeps the caller's
    torn-tail diagnostics intact — they are per-instance state reset by every
    ``read_all``, so counting through ``self`` would clobber a damage report.

    ``_quiet_damage`` stops the throwaway REPORTING what it skips: the tally is
    still kept on the throwaway (it is what a salvage read reports), but the
    WARNING/ERROR is the REPLAY's job — the count's input is the number of
    RECORDS, not the damage counters. Without it a successful ``append`` to a
    damaged journal would itself log "the records they carried are NOT
    replayed" at ERROR — an alarm an operator would page on, raised by the write
    path rather than by the recovery that actually skips them.
    """
    counter = EventLog(path)
    counter._quiet_damage = True
    return len(counter.read_all(salvage=True))


def read_for_replay(log) -> list[dict]:
    """``log.read_all(salvage=True)`` — the RECOVERY read (#5612).

    Every replay / recovery entry point reads through here, so a damaged
    mid-file line is SKIPPED, counted and reported instead of making
    ``rebuild_all`` / ``recover_from_log`` refuse forever over a line they
    cannot repair. The #3316 refusal that protects the graph is unaffected:
    :meth:`EventLog.torn_tail_revival_records` classifies the skipped line, so
    a loss that could REVIVE removed state still refuses the replay before any
    wipe or fold.

    The ``salvage`` capability is PROBED, not assumed, because a replay engine
    also accepts a SYNTHETIC log object (a projection test stub, a
    registry-only log) that predates the keyword and only has ``read_all()``.
    It is a SIGNATURE probe rather than a ``TypeError`` catch: catching would
    also swallow a ``TypeError`` raised INSIDE a real ``read_all`` and then
    silently retry the read.

    A caller that needs the STRICT reader (the integrity gate — a journal that
    cannot be trusted must not be replayed silently) calls
    ``EventLog.read_all()`` directly; that contract is unchanged.
    """
    read_all = log.read_all
    try:
        params = inspect.signature(read_all).parameters
    except (TypeError, ValueError):        # a builtin / C callable
        params = {}
    if "salvage" in params or any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return read_all(salvage=True)
    return read_all()


class EventLog:
    #: Is this log a JOURNAL — a durable, replayable truth set — or a scratch
    #: buffer that must never feed the record? ``record_writer.write_live_record``
    #: refuses to file a row from a non-durable log (a DECLINE counted on
    #: ``monitoring.metrics()["record_non_journal_skips"]``; #9231 review P2), and
    #: that guard is only sound if the answer is EXPLICIT: reading it as
    #: ``getattr(log, "durable", True)`` makes a temp buffer durable by
    #: accident and files rows whose only journal is a directory nothing
    #: references — ``record != replay(journal)``, unhealable (#9231 review P1).
    #: A log constructed onto a scratch path (the MCP mining lane's temp file)
    #: is built with :meth:`scratch`, which sets this ``False`` HERE rather than
    #: leaving it to each site to remember the assignment.
    durable: bool = True

    def __init__(self, path: str | Path):
        self.path = Path(path)

    @classmethod
    def scratch(cls, path: str | Path) -> EventLog:
        """An `EventLog` onto a SCRATCH path — born non-durable, on purpose.

        A scratch log is a per-run buffer (a `mkdtemp()` file, the demo lane's
        `NamedTemporaryFile` that is unlinked a few lines later): nothing holds
        it after the call, so a record row filed from it could never be
        reproduced or healed by a replay. `write_live_record` reads `durable`
        off the log, so it MUST be `False` at construction rather than an
        assignment each site is trusted to add — the seam's whole non-journal
        guard is worthless if a scratch site forgets it
        (#9231 review P2: the MCP mining lane's marker had no detector).

        Call this instead of ``EventLog(path)`` whenever the path is temporary;
        the scratch construction sites are pinned by a test.
        """
        log = cls(path)
        log.durable = False
        return log

    def append(self, event: dict) -> int | None:
        """Append one record, sealing any torn tail first; report its POSITION.

        A writer killed mid-append leaves a record with no terminating
        newline. Appending onto that fragment merges the two, and the merged
        line is malformed and FINAL — so ``read_all`` skips it as a torn tail
        and the record this call wrote is silently LOST, with the count
        blaming a tear (#5917 measured). Sealing avoids that: the fragment is
        terminated and marked, so it stays classified (#3316) while the new
        record starts on a line of its own. Nothing is truncated or
        rewritten — the seal is another append.

        RETURNS THE RECORD'S JOURNAL POSITION (#9190) — its 1-based index
        among the records a replay reads. That is exactly the ``seq``
        ``public.journal_event`` keys on, which ``record_projection`` numbers
        ``enumerate(records, start=1)`` over the SAME read, so a live write and
        a full replay give one record one number by construction. The ordinal
        is therefore a fact of the journal, not a number to invent: the shape
        this replaces (``MAX(seq)+1``) could only collide with a later replay
        — ``ON CONFLICT DO NOTHING`` silently swallowing its position row — or
        record one event under two seqs. See the ``POSITION`` block above.

        ``None`` means the append SUCCEEDED but its position is NOT PROVEN,
        and it is a NAMED absence, never a guess — a caller that writes
        ``journal_event`` must leave that row unwritten rather than fabricate
        a ``seq``. The bullets below are the STATES, and no number is stated on
        purpose: two of them name two cases each (a failed ``fstat`` before and
        after the write; an unusable path that is either a swap or an unreadable
        ``stat``), so a count is the one thing this list must not invite a reader
        to re-derive — it has already been miscounted twice. Holding the lock
        does not exclude any of the others: the lock speaks only to the state
        that names it.

        * the tail could not be SIZED, so the record may not have started on a
          clean line (``_seal_prefix`` returned ``None``). The record is
          appended, but whether a later replay counts it as a record is
          unknown, so nothing is claimed and nothing is cached. A healthy lock
          and a successful write do NOT rule this out — a flaky mount does. It
          is also REPORTED at WARNING: it is the one state where a record can be
          silently absent from the replay, and nothing after the write can tell
          whether the seal was needed;
        * the cross-process lock could not be taken (a wedged peer, or a
          platform with no advisory locks — ``_lock_exclusive`` says so);
        * the journal could not be COUNTED — ``_COUNT_MAY_RAISE``, all three:
          an unreadable journal (``OSError``: EACCES/EIO, and note that a parse
          problem is not one — ``read_all`` already skips that damage), a
          journal too large to hold in memory while counting it
          (``MemoryError``; the recount materializes the whole file — ~6x its
          size in RSS, measured in the COST paragraph below, and the ~8x in
          `_records_before`'s own note and the `POSITION` block), and a record
          nested deep enough to trip the reader's ``json.loads`` recursion limit
          (``RecursionError``,
          which is not a ``ValueError`` and which a foreign/restored journal
          can carry). None of the three is a ``TypeError`` on a bad argument,
          so the family is named once, in ``_COUNT_MAY_RAISE``. The record is
          still appended: the position must not give the append a read
          dependency it never had;
        * a peer wrote INSIDE this call's window without taking the lock — the
          documented degraded state, where ``_lock_exclusive`` stops
          contending for ``_FLOCK_UNHEALTHY_FOR_S`` after one bounded wait.
          Holding the lock therefore does not prove the count was exclusive;
          the record's own byte offset does, so the post-write offset is
          checked and a mismatch means someone else's record now sits between
          the count and this one;
        * a ``fstat`` failed — before the write (no count is possible) or after
          it (no witness for the offset). Two syscalls, two states, and the
          post-write one is the case where the record is durable and its
          position is simply unknown;
        * the PATH stopped naming the file this append wrote to — a restore or
          rotation replaced the journal inside the window (its own lock does not
          exclude a writer). The count was taken from the path and the record
          went into the inode we held, so no position is claimed and nothing is
          cached: the count describes a DIFFERENT file. This one is new with the
          ordinal and is why the journal's identity is re-read before the
          witness is trusted — a size check cannot see an inode swap. The same
          branch fires when the path cannot be ``stat``-ed AT ALL (``ESTALE``,
          ``EIO`` on the networked volumes this path is hardened for): nothing
          moved, the position is simply unprovable — fail-closed on an
          unreadable path, not evidence of a restore;
        * the sidecar could not be MAINTAINED and its ``_POSITION_CACHE_DEAD``
          window is live, so counting was REFUSED rather than paid (see THE
          OTHER SHAPE below). This one is deliberate and self-healing: it is
          what keeps an unwritable cache from making every append an O(journal)
          read, and it lapses on its own.

        Note the AMPLIFICATION of the LOCK state: one bounded wait stamps the
        path UNHEALTHY for ``_FLOCK_UNHEALTHY_FOR_S`` (30 s), and every append
        in that window returns ``None`` without making a single attempt. So a
        five-second stall costs 30 s of unproven positions on a lock that is
        perfectly healthy again — a caller must treat ``None`` as "no seq for
        this record", not as evidence that the deployment is broken.

        CONCURRENCY (#5612): the tail check and the write are ONE critical
        section — a per-path thread lock plus ``flock`` on the journal fd —
        and the (seal + record) bytes leave in a single ``write(2)`` on an
        ``O_APPEND`` descriptor. Two writers therefore never interleave inside
        a record, and the reader never sees a fabricated seal. See the
        ``_APPEND_LOCKS`` block above for the guarantee relied on and its
        limits. The position's read-modify-write rides the SAME critical
        section (the count and the record land under one lock and one offset
        check), which is what keeps two appenders off one number.

        COST. The common path adds two ``fstat`` calls (the pre-write state and
        the post-write offset), ONE ``stat`` of the journal path (the identity
        witness — the same-as-the-fd check that a restore did not replace the
        journal under this append), a memo compare and one write-through of the
        sidecar — measured +20 to +30 us on a ~130 us append (in-process loop on
        the same dev box, min of 7; the earlier +17 to +25 us figure predates the
        witness; see the ``POSITION`` block for the per-syscall numbers). A cache MISS re-counts the journal INSIDE this critical
        section, in both time and memory. Measured across runs on this box (a
        loaded fleet box, so ranges not points): 1 MiB 0.007-0.18 s; 3 MiB
        0.16-0.29 s; 20 MiB ~1.95 s with a peak RSS of ~127 MiB (~6x the
        journal — ``MemoryError`` is a real branch, not a formality).

        THE MISS AND THE LOCK DEADLINE, stated because they interact: the
        recount happens with ``_FLOCK_TIMEOUT_S`` (5 s) held, so a journal past
        roughly 50 MiB can outlast a PEER's deadline. That peer then appends
        unserialised and stamps ``_FLOCK_UNHEALTHY`` for 30 s (see the
        AMPLIFICATION note on the ``None`` states), during which every append
        returns ``None`` without contending, and its record landing inside this
        call's window also fails this call's offset check so nothing is cached —
        the next append recounts from scratch. While the journal stays large
        that is a repeatable recount -> degrade cycle. The fix is a
        checkpointed counter with a tail reader (count only what follows a
        recorded offset), which is #9252, not a knob here.

        THE OTHER SHAPE, a sidecar that cannot be WRITTEN at all (a read-only
        data dir, ENOSPC, a directory at the sidecar path), is bounded HERE
        rather than deferred: `_refresh_position_cache` marks the path with
        ``_POSITION_CACHE_DEAD`` for ``_POSITION_CACHE_DEAD_FOR_S`` and logs ONE
        warning, and while that mark is live an append does NOT recount — it
        reports ``None``. Without the mark every append by a writer without a
        memo (one ``EventLog`` per request, the hosted shape) would read the
        whole journal into RSS inside this critical section, for ever, which is
        the quadratic capture path #9190 must not trade for a ``seq``. The mark
        EXPIRES, so a repaired sidecar recovers by itself (one recount per
        window until then), and an explicit :meth:`position` still recounts
        immediately — a measurement the caller asked for is not the hot path.

        A miss happens on the first append against a journal with no sidecar
        yet (a restart, or a journal that never carried one), after a
        restore/rotation, and after an append by a writer that does not
        maintain the sidecar. It does NOT happen on the steady-state path —
        not even with several processes appending — because the sidecar is
        written through on every append: the next writer's read is a hit.
        Removing the remaining miss cost needs a checkpointed counter with a
        tail reader (count only the records after a recorded offset), which is
        its own change. A miss is a RECOUNT: it changes that stall, never the
        record's position — the one way the cache itself could answer wrongly
        is the same-length-inside-one-mtime-tick replacement named in the
        ``POSITION`` block above.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = json.dumps(event, ensure_ascii=False) + "\n"
        # Resolved ONCE per journal (memoised), then handed to both locks: the
        # ``flock`` key must be the same resolved path the thread lock uses, or
        # two spellings of one journal would contend on different flock keys.
        key = self._lock_key()
        # ONE descriptor: the tail check and the write share it, so sealing
        # costs no second open/read on the write path.
        # UNBUFFERED ``io.FileIO``: no ``BufferedWriter`` in the path, so the
        # one ``os.write`` below is one ``write(2)`` regardless of CPython's
        # buffering rules or the record's size.
        with (_append_lock(key), io.FileIO(self.path, mode="a+b") as f):
            locked = _lock_exclusive(f.fileno(), key)
            # BEFORE the write: the file state this append starts from — both
            # the cache-identity check and the offset the cache records. An
            # ``fstat`` that fails (ESTALE/EIO on the networked volumes this
            # path is hardened for) must not cost the RECORD: the position is
            # unproven, the append is not. Below, the post-write state read is
            # guarded for the mirror reason — raising there would hand the
            # caller a failure for a write that already landed, and a retry
            # would duplicate it.
            try:
                size, mtime_ns, dev, ino = _journal_state(f)
            except _COUNT_MAY_RAISE:
                size = mtime_ns = dev = ino = None
            # WHICH FILE this append is writing to, not merely how big it is.
            # The count below opens the PATH, and a restore can `os.replace` a
            # DIFFERENT inode onto that path while this append waits for the
            # lock (`backup.restore` stages the old journal aside and copies the
            # new one in; its lock documents that a process already writing the
            # journal is not excluded by it). Counting that inode while writing
            # through OURS would report a position this journal does not hold —
            # two records under one `seq`, which is the silent duplicate #9190
            # exists to remove — and the offset check below cannot see it,
            # because it is intra-descriptor by construction. So the identity is
            # read again before any position is claimed, and a swap anywhere in
            # this window costs the position, never a wrong one.
            #
            # The witness stats ``self.path`` — the SAME string the count opens —
            # not the resolved ``key``: `key` is the realpath MEMOISED on this
            # instance, so if the journal path is (or contains) a symlink that is
            # retargeted, `key` keeps naming the old target and the witness would
            # then disagree with a HEALTHY append (costing its position) and
            # agree with a swapped one (minting a wrong `seq`).
            identity = (dev, ino) if size is not None else None
            # Total by contract, given the lock key this caller has already
            # resolved (`_lock_key` below would run `realpath`, the one thing in
            # `_records_before` its guards do not cover): both of its remaining
            # inputs are guarded, so nothing here can lose the record before it is
            # written.
            # ``respect_dead_cache``: a sidecar that cannot be WRITTEN is not
            # merely a missed optimization — it would force this recount on
            # every append (see `_POSITION_CACHE_DEAD`), so while it is dead
            # the position is a named absence instead. The record still lands.
            count = (self._records_before(size, mtime_ns, respect_dead_cache=True)
                     if locked and size is not None else None)
            position = None if count is None else count + 1
            seal = self._seal_prefix(f)
            payload = ((seal or "") + record).encode("utf-8")
            _write_record(f.fileno(), payload)
            if seal is None:
                # The tail could not be SIZED, so it was never examined, and the
                # record was written without a seal. If the previous writer was
                # killed mid-record, this record is now glued onto that
                # fragment: the merged line is malformed and FINAL, and the next
                # replay skips it — so the record this call reports as written
                # is silently absent from the replay. Nothing here can repair
                # that (the bytes are gone into a line a replay refuses), which
                # is exactly why it must be SAID rather than inferred from a
                # later discrepancy.
                _report_unproven_seal(key, self.path)
            if position is not None and seal is None:
                # The tail could not be examined, so "this record adds exactly
                # one to the count" is unproven even though the write landed:
                # if the record merged into a torn fragment the next replay
                # skips it, and a number reported here would belong to a later
                # record. Nothing is cached either — a cache entry is a
                # statement about the NEXT writer's identity, and this one may
                # already be off by one.
                return None
            if position is not None:
                try:
                    after, after_mtime, _, _ = _journal_state(f)
                except _COUNT_MAY_RAISE:
                    return None
                if after != size + len(payload):
                    # A PEER wrote inside our window and did NOT take the lock:
                    # `_FLOCK_UNHEALTHY` is the documented degraded window (a
                    # previous append gave up waiting and stopped contending),
                    # and a platform without advisory locks has no window only
                    # because it has no lock at all. So the lock being HELD does
                    # not prove the count was exclusive — the byte offset does.
                    # Our count is stale by that peer's record, so the number we
                    # derived is not this record's position: say so, and leave
                    # the cache alone (it would record a boundary nothing
                    # counted).
                    return None
                if identity is not None and _path_identity(self.path) != identity:
                    # The PATH no longer names the file this append wrote to: a
                    # restore/rotation replaced it inside our window. The
                    # record landed (in the inode we held), and the count we
                    # took describes the OTHER file, so `position` is not this
                    # record's place in this journal. Report no position and
                    # cache nothing — a cache entry would describe a state no
                    # later reader will see, and the wrong number would stand.
                    return None
                # The record we just wrote parses by construction, so it adds
                # exactly one to the count a replay will read — seals and
                # skipped damage add none, here or there. Written THROUGH, so
                # the next writer (this process or another) reads a hit.
                self._refresh_position_cache(key, position, after, after_mtime)
            return position

    def _cache_path(self, key: str) -> Path:
        """The sidecar beside the RESOLVED journal, so one journal has ONE cache.

        ``key`` is :meth:`_lock_key`'s resolved path: two spellings of one
        journal (a symlinked data mount, a relative path) must share the cache
        the way they already share the lock, or each spelling keeps its own
        sidecar and misses on the other's writes.

        "ONE cache" holds for the spelling, WITH ONE EXCEPTION: ``key`` is the
        instance's MEMOISED resolution, so if a symlinked journal path is
        retargeted mid-life this instance keeps writing its cache beside the OLD
        target until it re-derives the key (a restart, or a new instance). The
        payload then describes a state the old target does not have, so a reader
        rejects it on ``(size, mtime_ns)`` — a wrong-FILE entry, not a wrong
        ``seq``, with the same single exception the ``POSITION`` block names (a
        same-length replacement inside one ``mtime_ns`` tick). Filed as #9285,
        where the fix is to invalidate this memo when the path's resolution
        moves. (``append``'s identity witness stats ``self.path`` for exactly
        this reason — see the comment there.)
        """
        return _position_cache_path(Path(key))

    def _refresh_position_cache(self, key: str, records: int, size: int,
                                mtime_ns: int) -> None:
        """Write the sidecar through — TOTALLY. Nothing here may reach the caller.

        Both call sites run AFTER the record has landed, so an exception escaping
        this method would report a failure for a write that succeeded, and the
        caller's retry would duplicate the record. That is why the guard is a
        blanket ``except Exception`` rather than a list of errno families: the
        promise is structural, and the categories are not knowable in advance —
        the syscalls raise ``OSError``, the payload build can raise
        ``RecursionError`` on a deeply nested record, and the descriptor's
        finalizer raises ``TypeError`` the day ``EventLog`` gains ``__slots__``
        without ``__weakref__``. Swallowing here cannot hide a WRONG count: a
        failure costs either a RECOUNT next time (an unclassifiable blip) or —
        for an ``OSError`` whose errno names the PATH, see
        ``_CACHE_PATH_ERRNOS`` — the positions for the retry window below, which
        an operator is warned about once. Both are losses of the OPTIMIZATION.

        ONLY an ``OSError`` marks the path DEAD (``_POSITION_CACHE_DEAD``): that
        is the shape where the sidecar PATH is unusable — EACCES on the
        directory, EISDIR, ENOSPC, a FIFO — and where leaving it unmarked would
        turn every later append into an O(journal) recount inside the append's
        lock. An allocation failure (``MemoryError`` building the memo or the
        payload) says nothing about the cache path and must NOT stop the next
        append from using it, so it is swallowed here without marking anything.

        DEBUG, not WARNING, for a failure that RECURS: a full disk or a
        read-only data mount makes this fire on every append, and a per-append
        warning is noise an operator cannot act on. The ONE actionable signal is
        emitted by :func:`_mark_position_cache_dead` the first time a path goes
        dead, which is also what stops the append path from recounting the
        journal on every append while the sidecar stays unwritable.
        """
        # The memo is set FIRST, and it is set even when the cache write below
        # fails: it is the only statement here that is not a syscall, so a
        # `MemoryError` building the tuple must not report failure for a durable
        # record (the caller's retry would duplicate it), and a failed write
        # still leaves the count known. Suppressed rather than fatal — the same
        # guard `_records_before` uses for the same allocation.
        with contextlib.suppress(*_COUNT_MAY_RAISE):
            self._position_memo = (os.fspath(self.path), size, mtime_ns, records)
        # EVERY branch below is inside this blanket, including the marking and
        # the logging: a handler is not a safe place to assume anything, and this
        # runs AFTER the record has landed. (A `MemoryError` from the mark's dict
        # insert, or a raising log handler, used to escape from here and report a
        # failure for a durable record — the retry then duplicates it.)
        with contextlib.suppress(Exception):
            try:
                _write_position_cache(self._cache_fd(key), records, size,
                                      mtime_ns)
            except OSError as exc:
                if exc.errno in _CACHE_PATH_ERRNOS:
                    # The sidecar PATH is unusable. Mark it, so the append path
                    # stops paying a recount it cannot amortise until it is fixed.
                    _mark_position_cache_dead(key, self.path, exc)
                logging.getLogger(__name__).debug(
                    "EventLog %s: position cache not written", self.path,
                    exc_info=True)
            except Exception:
                logging.getLogger(__name__).debug(
                    "EventLog %s: position cache not written", self.path,
                    exc_info=True)
            else:
                # A cache that just answered is not dead: clear the mark at once,
                # so a repaired sidecar does not keep the positions away for a
                # whole retry window (the mark's only other exit is expiry).
                # KNOWN IMPRECISION, fail-safe by construction: a kept descriptor
                # can still name an UNLINKED inode, so this clear can be evidence
                # about a file the path no longer names. The worst it does is make
                # the next memo-less append RECOUNT — the pre-existing cost — never
                # a wrong number.
                _POSITION_CACHE_DEAD.pop(key, None)

    def _cache_fd(self, key: str) -> int:
        """An open descriptor for the sidecar, kept for this instance.

        The cache is written on EVERY append (see the POSITION block), so the
        descriptor must not be opened per write: open+close measured ~114 us and
        `write_text` ~230 us, against ~8 us for `pwrite`+`ftruncate` on a
        descriptor already open. Keyed on the RESOLVED path and re-opened when
        ``self.path`` changes (the same rule ``_lock_key`` follows).

        It RAISES if the sidecar cannot be opened (EACCES, EMFILE, a directory
        in the way), because that is the honest report from here: this method's
        only caller is `_refresh_position_cache`, which is TOTALLY guarded and
        runs AFTER the record has landed — so the exception is logged at DEBUG
        and cannot reach the append. The OPEN is retried on every append (it is
        a single failing syscall, and retrying it is what lets a fixed
        permission recover without a restart); what is NOT retried per append is
        the O(journal) recount it would otherwise force — that is
        `_POSITION_CACHE_DEAD`, marked by the caller.

        Lifetime: ``weakref.finalize`` closes it when the instance is collected
        (CPython refcounting makes that the common case; a reference cycle
        defers it to the collector), which is what keeps the per-request /
        per-SDK shape from accumulating descriptors. The
        finalizer is DETACHED when the descriptor is replaced: a stale finalizer
        holding a closed fd NUMBER would eventually close an unrelated file that
        inherited the number. A fork inherits the descriptor, which is harmless
        — every write is at an absolute offset under the journal's own lock. A
        sidecar deleted underneath us keeps receiving writes on the unlinked
        inode; the visible sidecar then simply misses, which is a recount, never
        a wrong count.
        """
        held = getattr(self, "_cache_fd_state", None)
        if held is not None and held[0] == key:
            return held[1]
        # The journal moved (the same instance reused, or the first use): a
        # descriptor opened for ANOTHER journal's sidecar must not keep serving
        # this one, so it is dropped before the new one is opened — including
        # its finalizer, which would otherwise close a number we no longer own.
        self._cache_fd_state = None
        fin = getattr(self, "_cache_fd_finalizer", None)
        if fin is not None:
            fin.detach()
            self._cache_fd_finalizer = None
        if held is not None:
            _close_quietly(held[1])
        # Via `_cache_path`, the SAME derivation the reader uses: two
        # computations of this path would let a write land where no read looks,
        # which reads as a permanent cache miss and is invisible.
        fd = _open_regular_for_write(self._cache_path(key))
        self._cache_fd_state = (key, fd)
        self._cache_fd_finalizer = weakref.finalize(self, _close_quietly, fd)
        return fd

    def _records_before(self, size: int, mtime_ns: int, *,
                        respect_dead_cache: bool = False) -> int | None:
        """How many records the journal holds, for this file state — or ``None``.

        Memo, then cache, then the journal — each step validated by the
        ``(size, mtime_ns)`` identity described in the ``POSITION`` block, so
        every step but the last is O(1) and none of them can answer for a file
        state it did not observe. The memo is this instance's own last answer,
        re-derived when ``self.path`` changes (the same rule ``_lock_key``
        follows), because one process appending repeatedly is the common shape;
        it is REFRESHED here on every answer a caller paid for, so a
        ``position()`` does not re-read the journal on every call. Writing it
        here cannot race: the memo is this instance's, and both call sites hold
        the per-path THREAD lock, which is what excludes another thread of this
        process. (It does NOT exclude another PROCESS — the ``flock`` is
        conditional, `_lock_exclusive` may return ``False`` — so the memo's
        validity rests on the ``(size, mtime_ns)`` identity, not on exclusion.)

        ``None`` is a NAMED absence, not a zero: the journal could not be READ
        (``OSError`` — EACCES/EIO, not a parse problem, which ``read_all``
        already handles) or could not be HELD in memory (``MemoryError`` —
        ``read_all`` materializes the whole journal, ~8x its size in RSS
        measured, and ``MemoryError`` is not an ``OSError``). Counting must
        never turn into an append failure — the record's own write is what this
        path is for, and it had no read dependency before the position existed
        — so the caller reports an unproven position and still appends.

        ``respect_dead_cache`` is the APPEND path's flag (``position()`` leaves
        it off): while this journal's sidecar is in its ``_POSITION_CACHE_DEAD``
        window, the recount is REFUSED instead of paid. That is the difference
        between a bounded degradation and an O(n^2) capture path: with the
        sidecar unwritable, every append by a writer without a memo would
        otherwise read the whole journal into memory inside the append's lock,
        for ever, on a journal that is unbounded (#9025). ``None`` here means
        "no proven position" — the same contract as an unreadable journal, and
        the record still lands.
        """
        raw = os.fspath(self.path)
        memo = getattr(self, "_position_memo", None)
        if (memo is not None and memo[0] == raw and memo[1] == size
                and memo[2] == mtime_ns):
            return memo[3]
        cached = _read_position_cache(self._cache_path(self._lock_key()),
                                      size, mtime_ns)
        if cached is not None:
            count: int | None = cached
        elif respect_dead_cache and _position_cache_dead(self._lock_key()):
            # The sidecar is dead right now (see `_POSITION_CACHE_DEAD`): the
            # count would cost an O(journal) read on the append path, in the
            # append's critical section. Unproven position, record lands.
            return None
        else:
            try:
                count = _count_records(self.path)
            except _COUNT_MAY_RAISE:
                # OSError: unreadable. MemoryError: reciting the journal needs
                # O(journal) RSS (~8x measured). RecursionError: `json.loads` in
                # the reader raises it (not ValueError) on deeply nested input,
                # which a foreign/restored journal can carry. None of the three
                # is an OSError, and each would otherwise escape from `append`
                # BEFORE the write — the one failure the position must not add.
                return None
        # The count is already derived; only the memo can be lost (and then the
        # next call recounts). Guarded because the tuple alloc is the one
        # statement after a RECOUNT — the allocation spike — and this runs
        # BEFORE the record's write.
        with contextlib.suppress(*_COUNT_MAY_RAISE):
            self._position_memo = (raw, size, mtime_ns, count)
        return count

    def position(self) -> int:
        """How many records the journal holds — ``0`` when it holds none.

        AN OBSERVABILITY COUNT, never an allocator: it is a measurement of the
        journal at this instant, and it reserves nothing. A record's ``seq`` is
        ONLY ever what that record's own :meth:`append` returned — deriving one
        from ``position() + 1`` is the read-then-fabricate shape #9190 exists to
        remove, and under concurrency the number it names belongs to a peer's
        record. Read it to report what the journal has consumed, not to mint.

        Numbers the same records a replay does — the replay's reader,
        ``read_all(salvage=True)``, so damage is skipped here exactly as it is
        there — and it is 1-based (the `seq` spelling) where
        :meth:`cursor_at_end` indexes the same records 0-based through the
        STRICT reader, which number a journal with mid-file damage differently
        (that read refuses it rather than skipping it).

        Read from the position cache and VERIFIED against the journal's own
        ``(size, mtime_ns)`` (``POSITION`` block above), so a cache that does
        not describe this exact file state costs one recount — never a silently
        wrong number out of the CACHE. One exception to that, which the
        ``POSITION`` block names: the same-length replacement inside one
        ``mtime_ns`` tick. (A peer that writes DURING the count is not a cache
        exception — nothing is read from the cache, and no cache entry is written
        for a state that has moved — but it does make the returned NUMBER stale by
        however much the peer wrote, and this method has no ``None`` to give.)
        ``position()`` has no ``None`` to give; a caller that needs the ordinal of
        a record it is writing must take it from that record's own ``append``.

        Side effects worth knowing at a call site: it takes the append lock (so
        a wedged peer can park it for ``_FLOCK_TIMEOUT_S``), and a timeout there
        stamps the path UNHEALTHY for ``_FLOCK_UNHEALTHY_FOR_S`` (30 s), during
        which every :meth:`append` returns ``None`` WITHOUT contending — a
        read-shaped call can therefore suppress the ``seq`` of writes for 30 s,
        which is the amplification described on the ``append`` ``None`` states
        and the one cost of polling this method; it opens the
        journal ``a+b``, so it needs WRITE permission and a journal deleted
        between the ``exists`` check and the open is re-created empty, which is
        the answer ``0`` for the file that then exists; and it writes the
        position cache through — but only when it holds the cross-process lock
        AND the journal still holds the state it counted. Two DIFFERENT "the
        journal moved under the count" cases have two different outcomes, and
        neither one writes a cache entry: a PEER appended during the count
        (including in the degraded window, where the lock proves nothing) makes
        the state re-read differ, so the count is returned and the cache is left
        alone; and a RESTORE that replaced the journal's inode during the count
        raises (``:raises OSError:`` below), because the number counted belongs
        to the file the path has since left and there is no count to give for
        the one it now names. Neither case caches anything.

        Unlike :meth:`append` it RECOUNTS even while the journal's sidecar is in
        its ``_POSITION_CACHE_DEAD`` window: the append path declines that cost
        because it runs on every write, whereas this is an explicit measurement
        whose caller asked for a number. A successful one also repairs the
        cache, which clears the mark early and hands the appends their positions
        back.

        :raises OSError: the journal exists but could not be opened, stat'ed or
            read (a read-only journal raises here — this method needs the
            write fd for its lock), **or** the path stopped naming the journal
            whose count was taken (a restore or a rotation inside the count, so
            the number the count produced is another file's) — in either case
            there is no count to give. It raises rather than answering ``0``,
            which is a fact about an EMPTY journal and would be the same
            silence this method's ordinal exists to remove; and rather than
            returning the number of a journal the path has left, which once
            cached would be a fabricated count no later recount could correct.
        """
        if not self.path.exists():
            return 0
        key = self._lock_key()
        with (_append_lock(key), io.FileIO(self.path, mode="a+b") as f):
            locked = _lock_exclusive(f.fileno(), key)
            try:
                size, mtime_ns, dev, ino = _journal_state(f)
            except OSError as exc:
                raise OSError(
                    f"EventLog {self.path}: cannot stat the journal to count "
                    f"its records ({type(exc).__name__})") from exc
            count = self._records_before(size, mtime_ns)
            if count is None:
                # Unreadable journal: nothing to cache, and no number to give.
                raise OSError(f"EventLog {self.path}: cannot read the journal "
                              f"to count its records")
            # WHICH FILE the count describes, because `_records_before` counts
            # by opening the PATH while this descriptor names the journal it was
            # opened on: a restore can `os.replace` a different inode onto the
            # path DURING the count (seconds wide on a large journal — see COST
            # in `append`), and then the number is the other file's. The state
            # re-read below cannot see that (it is the same descriptor, by
            # construction), and a cache entry written from it would carry THIS
            # journal's exact `(size, mtime_ns)` — so a later reader would accept
            # it as a fact for ever: a fabricated `seq` no recount could correct,
            # which is the one failure this method must not produce. Same witness
            # as `append`'s, and the same fail-closed answer — there is no count
            # to give, which is this method's documented raise.
            if _path_identity(self.path) != (dev, ino):
                raise OSError(
                    f"EventLog {self.path}: the journal at this path is not the "
                    f"one the count was taken on (it was replaced during the "
                    f"count), so there is no count to give")
            if locked:
                # The count is only a statement about THE STATE IT WAS TAKEN
                # ON, and `flush` does not prove it was exclusive (a peer in
                # `_FLOCK_UNHEALTHY` writes without contending). Unlike
                # `append`, this call has no record of its own to check the
                # offset against, so it re-reads the state and writes the cache
                # ONLY if the journal still holds it: the alternative is a
                # cache entry asserting "this 18-byte file has 3 records" for a
                # state that really had 2, which a restore to that state would
                # then serve as a fact — forever, because no later writer can
                # make it miss.
                try:
                    after, after_mtime, _, _ = _journal_state(f)
                except OSError:
                    # Same contract as the pre-count read: this method's one
                    # raise is "there is no count to give", and a successfully
                    # counted journal has one. A state read that fails here only
                    # costs the cache.
                    after = after_mtime = None
                if (after, after_mtime) == (size, mtime_ns):
                    self._refresh_position_cache(key, count, size, mtime_ns)
            return count

    def _lock_key(self) -> str:
        """The resolved path this journal's append lock is keyed on.

        Cached per EventLog because ``realpath`` costs about as much as a
        syscall (measured ~21 us against a ~146 us append) and the hosted lane
        holds one EventLog per graph. Re-derived when ``self.path`` changes, so
        the cache can never describe a path the instance no longer holds.
        """
        raw = os.fspath(self.path)
        memo = getattr(self, "_lock_key_memo", None)
        if memo is not None and memo[0] == raw:
            return memo[1]
        resolved = os.path.realpath(raw)
        self._lock_key_memo = (raw, resolved)
        return resolved

    def _seal_prefix(self, f) -> str | None:
        """What must precede *record* for it to start on a clean line, or ``None``.

        Three states, and the third is why this reads a LINE rather than the
        last byte: a seal torn at byte 0 leaves the fragment terminated but
        UNMARKED, and a last-byte check would then report "clean" forever —
        every later record would make the fragment mid-file corruption and
        ``read_all`` would raise permanently (measured).

        * ends mid-record           -> terminate it, then mark it
        * ends on a record/marker   -> nothing to do
        * ends on a terminated
          MALFORMED line            -> mark it (no extra terminator)

        ``None`` means the tail's SIZE could not be read, so the tail was never
        examined. It is distinct from ``""`` on purpose: ``""`` asserts "this tail needs
        nothing", which the position then relies on — a record that is in fact
        glued onto a torn fragment is skipped by the next replay, so it consumes
        no position, and claiming one is a permanently wrong ``seq`` (and a cache
        entry that describes the file state exactly, so nothing ever recounts
        it). The caller treats ``None`` as "append it, prove nothing".

        A failure while EXAMINING the tail (the seek/`read(2)`s below) is NOT
        this — it raises, and `append` therefore fails before writing, which is
        what this method did before the position existed. The asymmetry is
        pre-existing, not a safety argument: a failed SIZE probe has always
        appended WITHOUT a seal (the caller must not lose a record because the
        mount hiccupped, and refusing the write there is not this issue's to
        change), and on a torn tail that append can glue the record onto the
        fragment, so the next replay skips it. What is new is only that such an
        append no longer CLAIMS a position for a record whose fate the seal never
        established. Making the size-probe path refuse the write as well is a
        separate decision about the append's own contract, recorded in #9254.
        """
        try:
            size = f.seek(0, 2)
        except OSError:
            return None
        if not size:
            return ""
        # The terminator is decided ONCE, from the file's final byte — never
        # re-derived per window, which would let a window's own last byte
        # masquerade as the file's and hand back an interior line (measured: a
        # last line of exactly the window size made this report "clean" and
        # the next record merged into the fragment — the very loss this seal
        # exists to prevent). CR is a terminator too: `read_all` normalises
        # universal newlines, so this scan must use the same line model or it
        # would annotate a fragment of a CR-separated journal that is actually
        # a complete record (measured).
        # ``seek`` + ``read`` on an UNBUFFERED ``io.FileIO``, at an absolute
        # offset. ``read(n)`` is ONE ``read(2)`` that may return fewer than
        # ``n`` bytes, so the loop below reads EXACTLY the window
        # (``_read_exactly``) — a short read must not skip the bytes between
        # the window edge and what was actually returned.
        # Nothing else in this process shares the position (the append
        # serialises on the path lock plus ``flock``), and the append's write is
        # an ``os.write`` on an ``O_APPEND`` descriptor, which ignores the read
        # position entirely.
        f.seek(size - 1)
        final = f.read(1)
        terminated = final in (b"\n", b"\r")
        end = size - 1 if terminated else size
        if final == b"\n" and size >= 2:
            f.seek(size - 2)
            if f.read(1) == b"\r":
                end = size - 2          # the \r\n PAIR is the terminator
        parts: list[bytes] = []
        window = 4096
        start = end
        while start > 0:
            # Each byte is read and copied ONCE. Two earlier versions of this
            # loop were quadratic (in read bytes, then in copies) — measured at
            # 140 MB read for a 4 MiB record and 53 s for a 32 MiB torn tail.
            # The window now starts SMALL and doubles: a 64 KiB first read
            # costs `max(64 KiB, last_line)` on EVERY append, which measured
            # 988x read amplification and ~3.5x CPU over 8000 small appends.
            # Chunks arrive newest-first, so the join is reversed.
            wstart = max(0, start - window)
            f.seek(wstart)
            chunk = _read_exactly(f, start - wstart)
            # A separator is a `\n` or a `\r` (read_all normalises universal
            # newlines, so CR ends a line here too), and the LAST of the two
            # in raw bytes is the last separator of the normalised text: for
            # `\r\n` that is the `\n`, and for a lone `\r` it is the `\r`.
            # Searching RAW bytes means a window that is wholly part of a
            # line contains no separator and needs no normalisation at all.
            idx = max(chunk.rfind(b"\n"), chunk.rfind(b"\r"))
            if idx >= 0:
                parts.append(chunk[idx + 1:])
                break
            parts.append(chunk)
            start = wstart
            window = min(window * 2, 1 << 20)
        last_line = b"".join(reversed(parts))
        text = _decode_line(last_line)
        if text is None:
            # Undecodable is MALFORMED, never empty: collapsing it to ""
            # would leave a torn fragment unmarked, and the next record would
            # make it mid-file corruption for good (measured).
            return ("\n" + SEAL_SENTINEL + "\n" if not terminated
                    else SEAL_SENTINEL + "\n")
        line = text.strip()
        if not terminated:
            # A tail that is ALREADY a complete record, or already the
            # complete annotation, needs the terminator and NOTHING else.
            # Annotating a record would be redundant, and a tear of that
            # annotation would leave a marker prefix after a VALID record — an
            # unclassifiable fragment that refuses every later replay; adding a
            # second sentinel for an unterminated sentinel is pure journal
            # noise. (Both measured.) A TORN sentinel is still repaired below.
            if is_seal_annotation(line):
                return "\n"
            try:
                json.loads(line)
            except ValueError:
                return "\n" + SEAL_SENTINEL + "\n"
            return "\n"
        if not line or is_seal_annotation(line):
            return ""
        try:
            json.loads(line)
        except ValueError:
            # A terminated malformed last line: the fragment was cut after
            # its terminator but before its annotation. Mark it — with no
            # leading newline, or the journal gains a blank line per repair.
            return SEAL_SENTINEL + "\n"
        return ""

    def read_all(self, *, salvage: bool = False) -> list[dict]:
        """Read all events in the log.

        TWO MODES, and the difference is the MID-FILE line (#5612):

        * **strict** (the default) — a malformed MID-FILE line raises. One
          reader's contract, unchanged: a journal that cannot be trusted must
          not be replayed, and a silent skip is the fail-open direction #3316
          forbids.
        * **salvage** (``salvage=True``) — a malformed mid-file line is
          SKIPPED, counted (:attr:`corrupt_line_count`), retained verbatim
          (:attr:`corrupt_line_raw`) and logged at ERROR, and the read
          continues. This is what the REPLAY engines use: the journal an
          operator most needs rebuilt is the one with a damaged line, and a
          raise there makes ``rebuild_all`` / ``recover_from_log`` refuse
          forever over a line they cannot repair. The lost record is NOT
          silently forgiven — it is classified by the same #3316 allowlist
          through :meth:`torn_tail_revival_records` (which covers mid-file
          skips too), so a loss that could REVIVE removed state still refuses
          the replay before any wipe or fold. Salvage changes who refuses, not
          whether.

        LINE-TOLERANCE (epic #900 S15/T12, cycle-21): a malformed TRAILING
        line (a torn tail from a SIGKILL mid-append — ``append`` is a single
        ``write(2)`` with no fsync) is skipped with a warning + count
        (``self.torn_trailing_count``) and this READ never raises — a raised
        parse would kill the very recovery tool (``rebuild_all`` /
        ``_auto_health_recover``). The tolerance is for the data-LOSS
        direction and is qualified by #3316: a torn tail whose loss could
        REVIVE state is still skipped here (the file is not rejected and not
        modified) but is REFUSED by the REPLAY, which consults
        :attr:`torn_trailing_raw` through
        :func:`torn_record_may_revive_state` BEFORE any wipe or fold.
        A malformed MID-FILE line is a separate corruption class (not a torn
        append) and raises an actionable error naming the file and line in
        STRICT mode — EXCEPT a fragment sealed by :meth:`append`: that fragment
        sits mid-file by construction, is annotated by a seal marker on the
        following line (or by a TORN marker, its prefix), and is tolerated
        like any other tear, so a fragment never costs the journal the record
        appended after it (#5917). A seal annotation is never returned as a
        record and contributes no index. In SALVAGE mode an unsealed mid-file
        malformed line is skipped and counted instead (see above).

        DECODING is per line: a line that is not valid UTF-8 is a malformed
        line under the same rules (tolerated at the end, refused mid-file).
        ``append`` writes ``ensure_ascii=False``, so a tear inside a
        multi-byte character is ordinary — and it must reach the classifier
        rather than raising ``UnicodeDecodeError`` for the whole file (#5917).

        Bytes are decoded with universal newlines first (``\r\n`` and a bare
        ``\r`` become ``\n``), which is what ``Path.read_text`` did for the
        string read this replaced: a foreign journal that separates records
        with a bare CR keeps reading.

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
        self.torn_trailing_count = 0
        self.torn_trailing_raw: list[str] = []
        # #5612: mid-file lines skipped in SALVAGE mode. Reset on EVERY call,
        # like the torn tail, so a reused EventLog never reports a previous
        # read's damage.
        self.corrupt_line_count = 0
        self.corrupt_line_raw: list[str] = []
        if not self.path.exists():
            return []
        out = []
        data = self.path.read_bytes()
        # Universal newlines, which `Path.read_text` used to apply here: a
        # foreign journal that separates records with a bare CR must keep
        # reading the way it did. Only the BYTES are held between passes (the
        # decoded strings are made on demand) so a whole-journal read does not
        # hold two copies of the journal, which matters while the journal is
        # unbounded (#5612).
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if data.endswith(b"\n"):
            # Exactly one terminator: the byte `append` writes. Dropping it
            # keeps the torn-tail rule identical whether or not the last
            # record's newline survived the crash (a malformed final line is a
            # torn tail either way), WITHOUT the U+2028/U+2029/U+0085 splits
            # `str.splitlines()` would introduce below. Splitting the BYTES on
            # b"\n" is the same split for a well-formed file.
            data = data[:-1]
        lines = data.split(b"\n")
        # Indices of lines the tolerance path consumed as a seal annotation
        # (annotations are never records, and never counted as fragments).
        consumed: set[int] = set()
        for idx, raw_bytes in enumerate(lines):
            if idx in consumed:
                continue
            raw = _decode_line(raw_bytes)
            if raw is None:
                # An undecodable line is MALFORMED, never an empty one: the
                # empty-line branch below would drop it WITHOUT counting or
                # classifying it, which is the fail-OPEN direction #3316
                # forbids.
                self._tolerate_or_raise(raw_bytes, idx, lines, consumed,
                                        salvage)
                continue
            line = raw.strip()
            if not line:
                continue
            if is_seal_annotation(line):
                # A COMPLETE marker is an annotation wherever it sits: it
                # exists only to legitimize the fragment before it (handled
                # there). A marker PREFIX is not accepted here — `{"` also
                # begins every record, so a torn record fragment would be
                # silently dropped instead of classified.
                continue
            try:
                # Parsed ONCE. The marker test above is a string test, so this
                # is the only parse per line (#5612 is why that matters: the
                # journal is unbounded and read_all is O(file)).
                out.append(json.loads(line))
            except ValueError:
                self._tolerate_or_raise(raw_bytes, idx, lines, consumed,
                                        salvage)
        return out

    def _tolerate_or_raise(self, raw_bytes: bytes, idx: int,
                           lines: list[bytes],
                           consumed: set[int],
                           salvage: bool = False) -> None:
        """Tolerate a malformed line at EOF or sealed mid-file, else refuse.

        A fragment sealed by `append` is mid-file by construction, so without
        the seal branch the journal would trade a silently lost record for a
        permanently unreadable one (#5917). The successor may itself be a TORN
        seal (`{"ty`), so the successor test accepts a marker prefix — and
        only there, and only for a line that directly follows a line this
        method just kept: a `{"` fragment standing on its own is a torn
        RECORD and must be counted and classified, not skipped. Any OTHER
        mid-file malformed line keeps the #3316 posture in STRICT mode:
        refuse, never skip. In SALVAGE mode it is skipped AND counted AND
        classified (#5612) — never skipped silently.

        The classifier text is derived here, not carried from the caller — a
        malformed line is rare, so the decode is not worth paying per line.
        """
        if _is_seal_prefix(_classifier_text(raw_bytes)):
            # A TORN seal that is no longer the immediate successor of its
            # fragment (two torn seals in a row, or a torn seal before a
            # complete one) is still an annotation, never a fragment:
            # counting it would refuse a replay over a harmless tear and
            # inflate the count. Safe by construction — the sentinel is not
            # JSON, so no record fragment can be a prefix of it.
            return
        if idx == len(lines) - 1:
            self._keep_torn(_classifier_text(raw_bytes), idx)
            return
        successor = _decode_line(lines[idx + 1])
        if successor is not None and _is_seal_prefix(successor.strip()):
            self._keep_torn(_classifier_text(raw_bytes), idx)
            consumed.add(idx + 1)
            return
        if salvage:
            self._keep_mid_file_damage(_classifier_text(raw_bytes), idx)
            return
        raise ValueError(
            f"EventLog {self.path}: malformed line {idx + 1} — "
            "mid-file corruption is not a torn append; refusing to "
            "skip (line-tolerance covers the trailing line only)"
        ) from None

    def _keep_torn(self, line: str, idx: int) -> None:
        """Count and retain a tolerated torn fragment (the full line).

        The FULL line, UNCAPPED. Classification is conservative over EVERY
        legible type, so a cap would let a non-allowlisted type hiding BEYOND
        it ride an allowlisted one before it — fail OPEN, the exact shape the
        allowlist polarity exists to prevent. ``line`` already exists at every
        call site, so retaining it costs no extra allocation.
        """
        import logging
        self.torn_trailing_count += 1
        self.torn_trailing_raw.append(line)
        if getattr(self, "_quiet_damage", False):
            return              # a COUNT wants the tally, not the alarm
        logging.getLogger(__name__).warning(
            "EventLog %s: skipping torn line %d "
            "(SIGKILL mid-append tolerance, S15) — %d line(s) skipped",
            self.path, idx + 1, self.torn_trailing_count)

    def _keep_mid_file_damage(self, line: str, idx: int) -> None:
        """Count, retain and REPORT a mid-file line skipped in salvage mode.

        Same retention rule as :meth:`_keep_torn` (the FULL line, uncapped,
        so the #3316 classifier sees every legible type), but it is its OWN
        tally and its own log level: the tail tolerance is expected crash
        damage, while mid-file damage is a corrupted journal an operator must
        be told about. ERROR, not WARNING, and the count goes in the message
        so the report survives a log level filter that drops the record.
        """
        import logging
        self.corrupt_line_count += 1
        self.corrupt_line_raw.append(line)
        if getattr(self, "_quiet_damage", False):
            return              # a COUNT wants the tally, not the alarm
        logging.getLogger(__name__).error(
            "EventLog %s: SKIPPING malformed mid-file line %d during a "
            "salvage read (#5612) — %d line(s) skipped so far; the records "
            "they carried are NOT replayed, and the replay is refused only "
            "if their loss could revive removed state",
            self.path, idx + 1, self.corrupt_line_count)

    @property
    def skipped_line_count(self) -> int:
        """Every line the last read consumed without yielding a record.

        Torn trailing fragments PLUS mid-file lines skipped in salvage mode —
        the number a recovery report must state, so an operator never has to
        infer "nothing was skipped" from silence. Seal annotations are not
        damage and are not counted.
        """
        return (getattr(self, "torn_trailing_count", 0)
                + getattr(self, "corrupt_line_count", 0))

    def torn_tail_revival_records(self) -> list[str]:
        """Skipped records whose loss can REVIVE state (#3316 / #5612).

        BOTH classes of damage: the tolerated torn TAIL and the mid-file lines
        a salvage read skipped. A mid-file loss is the same hazard as a tail
        loss — the record is gone and the surviving records still replay — so
        it must reach the same classifier. Leaving it out would be the
        fail-open direction: a salvage read that skipped a ``PointRetracted``
        and then replayed the rest would serve removed state as current.
        """
        return torn_tail_revival_records(
            getattr(self, "torn_trailing_raw", [])
            + getattr(self, "corrupt_line_raw", []))

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
        """Encode a 0-based RECORD index as an opaque cursor token.

        Records, not physical lines: a seal annotation and a torn fragment
        contribute no index, so a cursor is stable across a seal.
        """
        payload = json.dumps({"v": 1, "i": idx}, separators=(",", ":"))
        return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii")

    @staticmethod
    def _decode_cursor(cursor: str) -> int:
        """Decode an opaque cursor token to a 0-based record index.

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
