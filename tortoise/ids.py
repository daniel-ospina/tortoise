"""Minimal ID generation — ULID-style unique IDs, plus the deterministic
canonical entity id (`entity_name_id`) shared by the entity writers."""
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


def entity_name_id(label: str, name: str) -> str:
    """Deterministic entity id from ``(label, name)`` — the CANONICAL id.

    Mirrors `create_point`'s content-hash dedup so the projection's
    MERGE-by-name is IDEMPOTENT: the same ``(label, name)`` always yields the
    same id, so a second create returns the canonical node id (#452) and the
    #1155-P1 ``coalesce($id, o.id)`` ON-MATCH write is a no-op (same id).

    Two ID-MINTING writers read this, and they must agree:

    * the generic entity path (`sdk.create_entity`/`add_object`), which mints
      the canonical id for a name; and
    * `_event_plain_merge`'s name stubs (`tortoise/projection/entities.py`),
      which materialize the `:Object`/`:Subject` an `EventRecorded` implies
      when no registration for the name has landed yet — a poll/webhook event
      arrives before the entity path's ``ObjectRegistered`` (#1155-P1).

    NOT every name-keyed stub uses it: `edges._mint_subject_stub` writes the
    bare ``name`` as the id for the about-edge Subject stub, by its own
    live==replay design. That is a THIRD spelling for a `(Subject, name)`, so a
    reader widening a digest over entities must not assume ONE id shape per
    name.

    Using ONE identity across the two id-minting writers makes a node's
    IDENTITY a pure function of the JOURNAL (#7936): before this the stub id
    was a random `ulid()`, so replaying one journal twice minted different ids,
    and any digest over the entity view moved on every replay — a false
    `unrecorded-mutation`. It also makes the later generic registration a no-op
    instead of a re-key.

    The WHOLE graph is still not a pure function of the journal, and a wider
    digest must not assume it is: a registration path stamps `createdAt` from
    the replay clock (`_now_iso()`) when the record carries none.

    ``label[:3]`` keeps the entity id space disjoint from a bare ULID/PID, and
    the shape is the one `_check_refs` reserves (a ref may not shadow it).
    """
    digest = hashlib.sha256(f"{label}:{name}".encode("utf-8")).hexdigest()[:26]  # noqa: UP012
    return f"{label[:3].lower()}-{digest}"


def mint_session_id() -> str:
    """Mint a session id — the ONE spelling every capture door uses.

    ``session_<12 hex>`` was hand-copied at FOUR create sites — the two SDK
    doors (``commit_session``, ``capture_session``), ``extractor_v2.
    extract_session_v2`` and the hosted ``/v1/sessions`` handler. Four
    spellings of one value is the #5416 class: a caller reading one of them
    cannot tell whether the others agree. All four now call this (#9599).
    """
    return f"session_{uuid.uuid4().hex[:12]}"


#: The refusal text for a PRESENT-but-blank session id. One string, so the two
#: enforcement points (the SDK doors and the hosted ``/v1/sessions`` boundary)
#: cannot drift into two explanations of one rule.
SESSION_ID_BLANK_REFUSAL = (
    "session_id is blank — a session id must name something (be non-blank); "
    "pass None to let Tortoise mint one. A blank id is not an identity: the "
    "graph engine cannot serve it back to an equality seek, so the write "
    "mints an unfindable duplicate :Session and silently drops its CONTAINS "
    "edge (#9599)"
)


def session_id_refusal(session_id: str | None) -> str | None:
    """The ONE home for the capture doors' session-id rule (#9599).

    Returns a refusal reason, or ``None`` when the value may be used.

    ABSENT (``None`` or ``""``) is not a refusal — the host named no session,
    and every door mints one (``mint_session_id``). That is today's contract
    and it is preserved exactly.

    PRESENT-but-BLANK (``"   "``, ``"\t"``) IS a refusal. It is truthy, so the
    doors' ``session_id or mint()`` default never fired for it and it went
    straight to the write — where ``MERGE (s:Session {id:$sid})`` could not
    match it again and the turn container's CONTAINS edge landed nothing
    (#9599, measured). A blank is not an identity; the recorded rule already
    says so at Layer-1 (``commit_schema.CommitRecord._non_blank_session_id``,
    #4005), and this is the same sentence at the doors.

    PADDED-but-REAL (``"  s1  "``) is NOT a refusal — #9553 records those as
    legitimate identities. The engine still cannot index them; refusing them
    would be a product-surface decision and is deliberately NOT taken here.
    """
    if session_id is None or session_id == "":
        return None
    if isinstance(session_id, str) and session_id.strip():
        return None
    return SESSION_ID_BLANK_REFUSAL


def now_iso() -> str:
    """Current UTC time as ISO 8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017
