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
    doors (``commit_session``, deleted by #4282, and ``capture_session``, its public
    name retired — the body survives as ``_capture_session``),
    ``extractor_v2.extract_session_v2`` and the hosted ``/v1/sessions`` handler.
    Four spellings of one value is the #5416 class: a caller reading one of them
    cannot tell whether the others agree. All four used to call this (#9599);
    after #4282 THREE create sites remain — the private ``_capture_session`` body,
    ``extract_session_v2`` and the hosted handler — because the SDK
    ``commit_session`` door is gone and ``capture_session`` survives only as that
    private body.
    """
    return f"session_{uuid.uuid4().hex[:12]}"


#: ⛔ A refusal is REASON + REMEDY, and the REMEDY belongs to the DOOR, not to
#: the rule (#11576 review P2). The three caller-id doors share
#: :func:`session_id_refusal`, but only two of them MINT: the SDK capture door
#: (``_capture_session``; its public name was retired by #4282) and the hosted
#: ``/v1/sessions`` ``SessionRequest`` boundary. ``commit_schema.CommitPayload``
#: — the ``POST /v1/sessions/commit`` model — does not: its ``session_id`` is a
#: required ``str`` (``Field(min_length=1)``) written VERBATIM by the handler, so
#: ``None`` — and an omission — is a 400 ``missing_required_fields`` raised before
#: the model is ever validated
#: (``commit_schema.missing_required_fields``, tested with
#: ``raw.get(f) in (None, "")``). Serving "pass None to let Tortoise mint one"
#: there told a caller to take a remedy that door rejects, and the text reaches
#: it verbatim (``shape_errors`` copies ``msg`` into the 422 ``detail``). So this
#: module owns every REASON and each door picks its own REMEDY (``may_mint``):
#: still one home for the wording, so the doors cannot drift into two
#: explanations of one rule. The engine mechanics (:Session duplication / dropped
#: CONTAINS edge) stay in the docstrings, never on a customer's wire (#9599
#: review P3).
SESSION_ID_BLANK_REASON = (
    "session_id is blank — a session id must name something (be non-blank)")

#: The remedy for a door whose ``session_id`` is OPTIONAL — the SDK capture
#: door (``_capture_session``) and the hosted ``SessionRequest`` boundary,
#: which mint for ``None``.
SESSION_ID_MINT_REMEDY = "pass None to let Tortoise mint one"

#: The remedy for a door whose ``session_id`` is REQUIRED and never minted —
#: ``commit_schema.CommitPayload``. What it takes is the id itself, and the payload
#: writes that id verbatim as a ``:Session``. ⛔ ONE caveat, measured: that door's
#: ``@field_validator("session_id")`` is ``mode="after"``, so pydantic's own ``str``
#: type gate answers the NON-``str`` class FIRST (``string_type``, e.g. for ``0``)
#: and this predicate only ever composes the BLANK and RESERVED refusals there.
#: The wording is therefore true of all three classes rather than pretending the
#: third reaches it — pinned at the model in
#: ``tests/test_demo_session_read_exclusion_8290.py::test_the_commit_door_never_advises_a_mint_it_cannot_do``.
SESSION_ID_NO_MINT_REMEDY = (
    "this door requires a non-blank string id outside the RESERVED "
    "demo/sample namespace, and never mints one")

#: The CLIENT-FACING refusal text for a PRESENT-but-blank session id at a
#: MINT-CAPABLE door. One composed string, so the enforcement points that answer
#: such a caller cannot drift apart.
SESSION_ID_BLANK_REFUSAL = f"{SESSION_ID_BLANK_REASON}; {SESSION_ID_MINT_REMEDY}"

#: A PRESENT session id of the wrong SHAPE. Refused with its own message rather
#: than the blank one: naming "blank" for an int is the #8018 class (a report
#: naming a cause that is false for the record it describes).
SESSION_ID_NOT_STR_REASON = "session_id must be a string — got %s"

#: A caller-supplied id inside the RESERVED demo/sample namespace (#8290).
#:
#: ⛔ This is what makes the demo-Session READ exclusion SOUND. ``#8290`` excludes
#: the demo ``:Session`` by the prefix :data:`tortoise.session_schema.
#: DEMO_SESSION_ID_PREFIX`, and a prefix test is only safe if no real capture can
#: occupy that namespace. ``mint_session_id()`` cannot (``demo_`` is neither hex
#: nor 12 chars), but the doors are NOT mint-only: ``SessionRequest.session_id``
#: and the SDK capture door accept a CALLER-SUPPLIED id of any shape. Without this
#: refusal a genuine capture filed as ``session_demo_…`` would be written, paid
#: for, and then invisible on ALL THREE read surfaces — the list, the detail
#: door (404) and the SDK mirror (``None``). A silent disappearance is the exact
#: class this refusal converts into an audible one, so the prefix is reserved
#: rather than merely unused.
#:
#: The reasoning is shared with the other refusal classes: ONE predicate for
#: every door (the SDK capture door ``_capture_session``, the hosted
#: ``SessionRequest`` boundary, and ``commit_schema.CommitPayload`` — the
#: Layer-1 model of the public ``POST /v1/sessions/commit``, whose
#: ``session_id`` is written verbatim as a ``:Session``), so the three cannot
#: disagree about which ids may name a session. ⚠️ Three-of-four was NOT enough
#: before #4282 (#8290 review P1): the commit payload boundary was the door that
#: used to admit the namespace and file a session every read surface hides — and
#: the count is three now because #4282 retired the SDK ``commit_session`` door,
#: not because a door stopped being gated.
SESSION_ID_RESERVED_REASON = (
    "session_id is inside the RESERVED demo/sample namespace (%s) — every "
    "session read surface excludes that namespace, so a capture filed under it "
    "would be invisible to its owner")
#: ⛔ The three REASON constants compose with :func:`session_id_refusal`'s
#: ``may_mint`` remedy — the mint-door spelling of this one, for instance, is
#: ``SESSION_ID_RESERVED_REASON % prefix`` + ``"; " + SESSION_ID_MINT_REMEDY``.
#: They are not restated as constants because the DOOR, not the rule, decides the
#: tail: a fourth constant would be a second home to drift from.
#: ``SESSION_ID_BLANK_REFUSAL`` is the exception — a test pins the exact wire
#: string a mint-capable SDK door returns, so it is composed once, above.


def session_id_refusal(session_id: object, *,
                       may_mint: bool = True) -> str | None:
    """The DOOR half of the session-id rule — one predicate, every door (#9599).

    Returns a refusal (reason + the DOOR's remedy), or ``None`` when the value
    may be used.

    ``may_mint`` is the one thing the doors do not share (#11576 review P2): the
    SDK capture door (``_capture_session``) and the hosted ``SessionRequest``
    boundary MINT for ``None``, so they take the default and their refusal ends
    with "pass None to let Tortoise mint one". ``commit_schema.CommitPayload``
    passes
    ``may_mint=False``, because its ``session_id`` is a REQUIRED ``str`` that the
    handler writes verbatim — there, that advice is a remedy the door rejects
    (a 400 ``missing_required_fields``), so it gets
    :data:`SESSION_ID_NO_MINT_REMEDY` instead. The flag chooses the REMEDY only;
    which values are refused, and the wording of every reason, stay here.

    ⛔ The blank rule has TWO homes at two layers, deliberately, and neither is
    a duplicate of the other (#9599 review P2):

    * THIS function is the **DOOR half** — a caller-supplied ``session_id`` at
      ``_capture_session`` (the private SDK door; its public name
      ``capture_session`` was retired by #4282), the hosted ``SessionRequest``
      boundary and ``commit_schema.CommitPayload`` (``#8290`` review P1 — the
      Layer-1 model of the public ``POST /v1/sessions/commit``, whose
      ``session_id`` is written verbatim as a ``:Session`` and as an
      ``AgentSession`` ``:Event``) — where the answer has to name a reason.
      ⛔ The SDK ``commit_session`` door is GONE (#4282): the hosted
      ``/v1/sessions`` boundary is the surviving door for that path, so the
      three remaining doors share it and cannot drift apart.
    * ``projection._writable_container_id`` is the **REPLAY half** —
      ``_writable_id`` PLUS this same blank clause — and it gates every fold
      that merges a session key (``_link_session``'s ``contains_session``,
      ``_fold_session_recorded``'s id) plus the sidecar restore paths, where
      there is no caller to explain anything to. It landed on main with #9594
      from the **#9553 review P2**, so ⚠️ **#9553 IS the correct citation for
      the blank rule** — do not read the padded note below as retracting that.
      This branch extends that predicate to the ``SessionRecorded`` carrier
      rather than stacking a second predicate in front of it.

    The two halves agree on BLANK and on a non-``str`` — pinned class by class
    in
    ``tests/test_9599_blank_session_id.py::test_the_fold_gate_and_the_door_gate_are_one_rule``
    — and they differ, DELIBERATELY and in three named classes (that same test
    asserts each one, so neither half can be "unified" silently):

    * **ABSENT** (``None``/``""``) — a MINT-CAPABLE door mints for it (every
      door except ``commit_schema.CommitPayload``, whose ``session_id`` is
      required and is refused as a 400 ``missing_required_fields`` before the
      model validates); a FOLD has nothing to mint with and refuses it. This is
      the ORIGINAL asymmetry, older than
      #9599's pin, and it is why the two are NOT "equal on every non-empty
      string" either.
    * **the RESERVED demo/sample namespace** (#8290) — this predicate REFUSES
      it; the fold must still ADMIT it. The reservation belongs on the LIVE
      WRITE path, where a caller can be told and can re-file under a minted id,
      while a REPLAY must accept every id an older tree legitimately wrote or it
      loses those rows (#9594 / #9553 govern the replay half). Widening
      ``_writable_container_id`` to the reserved class is a DATA-LOSS change.
    * **an id the ENGINE cannot parse** (NUL, lone surrogate) — the fold's
      ``_writable_id`` refuses it and it is not the blank rule.

    ⛔ Do not read those three as drift to be repaired: each is a recorded
    decision, and each reds in the cited test if either half is widened.

    ABSENT (``None`` or ``""``) is not a refusal at this PREDICATE — the host
    named no session, and the two mint-capable doors mint one
    (``mint_session_id``); ``commit_schema.CommitPayload`` never mints and
    rejects an absent id one layer up (400 ``missing_required_fields``). That is
    today's contract and it is preserved exactly. The test is PRESENCE, not truthiness
    (``identity_absent``'s rule): ``0``/``False`` are PRESENT values of the
    wrong shape and get the shape message.

    ⛔ BOTH doors therefore diagnose the caller's RAW argument BEFORE they mint
    (#9599 review). Doing it the other way round — mint, then diagnose — let
    ``session_id or mint()`` swallow a falsy non-``str``, so ``0``/``False``
    were minted over inside the SDK while the hosted boundary's typed model
    422'd them, and the two SDK doors disagreed with each other about
    ``123``/``b"  "``. One order, both doors: diagnose, then mint.

    PRESENT inside the RESERVED demo namespace (``session_demo_…``) IS a
      refusal, and it is the WATCHDOG for ``#8290``'s read exclusion: the prefix
      is how all three session read surfaces exclude the demo ``:Session``, so a
      capture filed under it would be written and then invisible everywhere —
      the sinkhole the reservation closes (see ``SESSION_ID_RESERVED_REASON``).
      ⚠️ This class is why the sentence "no real capture can carry the demo
      prefix" is a FACT rather than a convention about ``mint_session_id``:
      minted ids cannot collide, AND a caller-supplied one is refused.

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
    remedy = SESSION_ID_MINT_REMEDY if may_mint else SESSION_ID_NO_MINT_REMEDY
    if not isinstance(session_id, str):
        return f"{SESSION_ID_NOT_STR_REASON % type(session_id).__name__}; {remedy}"
    if not session_id.strip():
        return f"{SESSION_ID_BLANK_REASON}; {remedy}"
    # #8290: the demo/sample namespace is RESERVED, because the session read
    # surfaces exclude it by that prefix (see SESSION_ID_RESERVED_REASON).
    # Checked AFTER the shape/blank classes so each refusal names its own cause.
    #
    # Local import: `tortoise.session_schema` is stdlib-only and declares the
    # prefix ONCE, but it is a higher-layer module than this id helper — importing
    # it at module scope would make the dependency eager for every `ids` caller
    # (the same local-import discipline the SDK uses for `session_schema`).
    from tortoise.session_schema import DEMO_SESSION_ID_PREFIX

    if session_id.startswith(DEMO_SESSION_ID_PREFIX):
        return f"{SESSION_ID_RESERVED_REASON % DEMO_SESSION_ID_PREFIX}; {remedy}"
    return None


def session_id_or_absent(value: object) -> str | None:
    """The value if it can NAME a session, else ``None`` (ABSENT).

    Every door MINTs for ``None``, so a blank reaching one is a refusal while a
    blank reaching a MINT FALLBACK must not become one: it names no session, so
    the fallback (a path-anchored id, a content digest) is the right answer and
    the capture is still fileable. Three callers need that spelling — the CLI
    writer (``__main__._spool_transcript``, ``session import``, the backlog
    replay) and the spool WIRE (``capture_spool._normalise_session_id``) — so it
    lives here rather than being re-typed at each, and it composes
    :func:`session_id_refusal` so EVERY refusal class — blank, non-``str`` and
    the RESERVED demo namespace of #8290 — is judged by the doors' ONE
    predicate rather than re-derived here.

    A reserved-namespace id therefore falls back (``None``) rather than being
    put on the wire: the server mints a fresh id, so that capture is still
    FILED and still readable, instead of being written under a prefix the read
    surfaces exclude and then quarantined by a permanent 422.

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
