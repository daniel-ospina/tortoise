"""Minimal ID generation — ULID-style unique IDs and content hashes.

#3590: identity is an OPAQUE id minted once (``ulid()``). There is no longer a
name-derived entity id here: ``obj-<sha26(name)>`` made a rename mint a second
entity and a delete+re-create resurrect the first one. The derived spelling is
still RECOGNISED (``sdk._CANONICAL_ENTITY_ID_RE``) so a pre-change graph's ids
are treated as ids, but nothing MINTS it. The one remaining producer of the
spelling lives in ``legacy_ids`` (an offline legacy-key normaliser, never an
entity create path).
"""
from __future__ import annotations

import hashlib
import time
import uuid


def ulid() -> str:
    """Return a time-sortable unique ID: <timestamp-hex>-<uuid>."""
    ts = hex(int(time.time() * 1000))[2:]
    return f"{ts}-{uuid.uuid4().hex[:12]}"


def content_hash(text: str) -> str:
    """SHA-256 hex digest of text — used for idempotency keys."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mint_session_id() -> str:
    """Mint a session id — the ONE spelling every capture door uses.

    ``session_<12 hex>`` was hand-copied at FOUR create sites — the two SDK
    doors (``commit_session``, ``capture_session``), ``extractor_v2.
    extract_session_v2`` and the hosted ``/v1/sessions`` handler. Four
    spellings of one value is the #5416 class: a caller reading one of them
    cannot tell whether the others agree. All four now call this (#9599).
    """
    return f"session_{uuid.uuid4().hex[:12]}"


#: The CLIENT-FACING refusal text for a PRESENT-but-blank session id. One
#: string, so the two enforcement points (the SDK doors and the hosted
#: ``/v1/sessions`` boundary) cannot drift into two explanations of one rule.
#: It is returned verbatim to callers (a 422 detail on the hosted surface), so
#: it names the rule and the remedy only — the engine mechanics
#: (`:Session` duplication / dropped CONTAINS edge) stay in the docstrings,
#: never on a customer's wire (#9599 review P3).
SESSION_ID_BLANK_REFUSAL = (
    "session_id is blank — a session id must name something (be non-blank); "
    "pass None to let Tortoise mint one"
)

#: A PRESENT session id of the wrong SHAPE. Refused with its own message rather
#: than the blank one: naming "blank" for an int is the #8018 class (a report
#: naming a cause that is false for the record it describes).
SESSION_ID_NOT_STR_REFUSAL = (
    "session_id must be a string — got %s; pass None to let Tortoise mint one"
)


def session_id_refusal(session_id: object) -> str | None:
    """The DOOR half of the session-id rule — one predicate, every door (#9599).

    Returns a refusal reason, or ``None`` when the value may be used.

    ⛔ The blank rule has TWO homes at two layers, deliberately, and neither is
    a duplicate of the other (#9599 review P2):

    * THIS function is the **DOOR half** — a caller-supplied ``session_id`` at
      ``capture_session``, ``commit_session`` and the hosted
      ``SessionRequest`` boundary — where the answer has to name a reason.
      The three doors share it, so they cannot drift apart.
    * ``projection._writable_container_id`` is the **REPLAY half** —
      ``_writable_id`` PLUS this same blank clause — and it gates every fold
      that merges a session key (``_link_session``'s ``contains_session``,
      ``_fold_session_recorded``'s id) plus the sidecar restore paths, where
      there is no caller to explain anything to. It landed on main with #9594
      from the **#9553 review P2**, so ⚠️ **#9553 IS the correct citation for
      the blank rule** — do not read the padded note below as retracting that.
      This branch extends that predicate to the ``SessionRecorded`` carrier
      rather than stacking a second predicate in front of it.

    The two are pinned equal on every non-empty string by
    ``tests/test_9599_blank_session_id.py::test_the_fold_gate_and_the_door_gate_are_one_rule``.

    ABSENT (``None`` or ``""``) is not a refusal — the host named no session,
    and every door mints one (``mint_session_id``). That is today's contract
    and it is preserved exactly. The test is PRESENCE, not truthiness
    (``identity_absent``'s rule): ``0``/``False`` are PRESENT values of the
    wrong shape and get the shape message.

    ⛔ BOTH doors therefore diagnose the caller's RAW argument BEFORE they mint
    (#9599 review). Doing it the other way round — mint, then diagnose — let
    ``session_id or mint()`` swallow a falsy non-``str``, so ``0``/``False``
    were minted over inside the SDK while the hosted boundary's typed model
    422'd them, and the two SDK doors disagreed with each other about
    ``123``/``b"  "``. One order, both doors: diagnose, then mint.

    PRESENT-but-BLANK (``"   "``, ``"\t"``) IS a refusal. It is truthy, so the
    doors' ``session_id or mint()`` default never fired for it and it went
    straight to the write — where ``MERGE (s:Session {id:$sid})`` could not
    match it again (the engine indexes the trimmed term but does not trim the
    seek) and the turn container's CONTAINS edge landed nothing (#9599,
    measured). A blank is not an identity; the recorded rule already says so at
    Layer-1 (``commit_schema.CommitPayload._non_blank_session_id``, #4005 — the
    validator lives on ``CommitPayload``, not on a ``CommitRecord``: no such
    class exists, and citing one sent a review to re-derive a third spelling), and
    this is the same sentence at the doors.

    PADDED-but-REAL (``"  s1  "``) is NOT a refusal here. #9599's own body
    records those as legitimate identities, in the sentence that explains why a
    one-projection refusal cannot fix them ("those are legitimate identities, and
    ducking them in one projection would not stop the engine duplicating nodes on
    the other paths"). Refusing or TRIMMING one would therefore REOPEN that
    recorded position, so it is an OPEN OWNER QUESTION on #9599 (comment
    6342343424, with the three candidate answers and their measured
    consequences), not this lane's decision: this PR lands the BLANK half only.
    ⚠️ Do not cite #9553 for THAT position — #9553 is R5 (materialising
    point-property edge families) and states no position on padded ids at all
    (it speaks to the BLANK case only, which is why it owns the replay half
    above); the padded position is quoted in #9599's body, and citing the wrong
    home is how a
    later lane "tidies" it into a refusal.
    """
    if session_id is None or (isinstance(session_id, str) and session_id == ""):
        return None
    if not isinstance(session_id, str):
        return SESSION_ID_NOT_STR_REFUSAL % type(session_id).__name__
    if session_id.strip():
        return None
    return SESSION_ID_BLANK_REFUSAL


def session_id_or_absent(value: object) -> str | None:
    """The value if it can NAME a session, else ``None`` (ABSENT).

    Every door MINTs for ``None``, so a blank reaching one is a refusal while a
    blank reaching a MINT FALLBACK must not become one: it names no session, so
    the fallback (a path-anchored id, a content digest) is the right answer and
    the capture is still fileable. Three callers need that spelling — the CLI
    writer (``__main__._spool_transcript``, ``session import``, the backlog
    replay) and the spool WIRE (``capture_spool._normalise_session_id``) — so it
    lives here rather than being re-typed at each, and it composes
    :func:`session_id_refusal` so the BLANK half cannot drift from the doors.

    ``""`` normalises to ``None`` for the same reason ``None`` does: both are
    ABSENT to the doors, and a fallback must fire for both.
    """
    if session_id_refusal(value) is not None:
        return None
    if not isinstance(value, str):
        return None
    return value or None


def now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017
