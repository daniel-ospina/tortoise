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
import errno
import inspect
import io
import json
import logging
import os
import re
import threading
import time
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


def _lock_exclusive(fd: int, key: str) -> None:
    """Serialise the append against OTHER PROCESSES, where the OS can.

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
    peer must not become an outage — and it is why the warning says so.
    """
    if fcntl is None:                  # pragma: no cover - platform branch
        return
    stamp = _FLOCK_UNHEALTHY.get(key)
    now = time.monotonic()
    if stamp is not None:
        if now - stamp < _FLOCK_UNHEALTHY_FOR_S:
            return
        _FLOCK_UNHEALTHY.pop(key, None)
    deadline = now + _FLOCK_TIMEOUT_S
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno in _FLOCK_UNSUPPORTED:
                return
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
                return
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
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, event: dict) -> None:
        """Append one record, sealing any torn tail first.

        A writer killed mid-append leaves a record with no terminating
        newline. Appending onto that fragment merges the two, and the merged
        line is malformed and FINAL — so ``read_all`` skips it as a torn tail
        and the record this call wrote is silently LOST, with the count
        blaming a tear (#5917 measured). Sealing avoids that: the fragment is
        terminated and marked, so it stays classified (#3316) while the new
        record starts on a line of its own. Nothing is truncated or
        rewritten — the seal is another append.

        CONCURRENCY (#5612): the tail check and the write are ONE critical
        section — a per-path thread lock plus ``flock`` on the journal fd —
        and the (seal + record) bytes leave in a single ``write(2)`` on an
        ``O_APPEND`` descriptor. Two writers therefore never interleave inside
        a record, and the reader never sees a fabricated seal. See the
        ``_APPEND_LOCKS`` block above for the guarantee relied on and its
        limits.
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
            _lock_exclusive(f.fileno(), key)
            seal = self._seal_prefix(f)
            _write_record(f.fileno(), (seal + record).encode("utf-8"))

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

    def _seal_prefix(self, f) -> str:
        """What must precede *record* for it to start on a clean line.

        Three states, and the third is why this reads a LINE rather than the
        last byte: a seal torn at byte 0 leaves the fragment terminated but
        UNMARKED, and a last-byte check would then report "clean" forever —
        every later record would make the fragment mid-file corruption and
        ``read_all`` would raise permanently (measured).

        * ends mid-record           -> terminate it, then mark it
        * ends on a record/marker   -> nothing to do
        * ends on a terminated
          MALFORMED line            -> mark it (no extra terminator)
        """
        try:
            size = f.seek(0, 2)
        except OSError:
            return ""
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
