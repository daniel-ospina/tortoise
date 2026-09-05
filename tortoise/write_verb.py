"""W5 frozen version-stamped write verb (issue #2104, epic #2080, DM-2/S12).

gbrain MEMORY_VERBS_v1 ADAPT (epic plan §4.4/§6.4): every session->graph
WRITE-BACK response (the 2xx path — gate rejections are HTTP-level errors,
not write responses, and keep their HTTP semantics) speaks ``memory_write_v1``:

    protocol_version: "memory_write_v1"   REQUIRED in every write response
    status:            ok | accepted | partial | error
    provenance:        { source_session, source_harness, ingested_at }  REQUIRED
    points:            [{ point_id, kind, status, ep_updated, dedup }]
    error:             null | { code, suggestion }   (canonical enumerated)

Enumerated error codes + suggestions are the gbrain pattern: a caller that
sees a code knows the canonical next action.  Version drift between harness
and ingestion is an error, never a silent mismatch — ``protocol_version``
makes the drift visible at the response surface.

This module is pure shape construction + the canonical code/suggestion
table.  It holds NO graph logic: the caller (hosted capture impl, SDK
mirror) supplies the per-point facts (kind/status/ep_updated/dedup) it has
truthfully observed.  Anti-gaming: a key is only ever reported when the
caller has actually observed the fact it names (Phase A reports
``dedup:"new"`` only for points minted by THIS request).  Phase D (#2104)
lands the per-point dedup classification: the capture seams resolve each
claim against existing nodes (in-capture repeats and content-addressed
re-ingests — see tortoise/dedup_classify.py) and report
``content_hash_hit``/``rephrase_linked`` ONLY for claims that actually
resolved to an existing node; when the write-back cannot determine dedup
state the point stays ``dedup:"new"`` — never fabricated.

Scope note (P3, review): the envelope is wired on the hosted capture
surface NOW; the self-host SDK mirror (``sdk.capture_session``) still
returns the legacy dict — its verb wrap lands with the mirror-parity
phase.  The per-point entries on the capture surface are the ENRICHED raw
extractor dicts (``id``-keyed, plus additive ``point_id``/``status``/
``ep_updated``/``dedup``) — ``point_entry`` is the canonical factory for
write surfaces that build their own point lists.
"""
from __future__ import annotations

MEMORY_WRITE_V1 = "memory_write_v1"

# Status branch: ok (all writes clean) | accepted (durable, async-confirmed)
# | partial (some writes failed — errors[] carry the codes) | error.
STATUS_OK = "ok"
STATUS_ACCEPTED = "accepted"
STATUS_PARTIAL = "partial"
STATUS_ERROR = "error"

# Dedup classification per point.
DEDUP_NEW = "new"
DEDUP_CONTENT_HASH_HIT = "content_hash_hit"
DEDUP_REPHRASE_LINKED = "rephrase_linked"

# ── Canonical enumerated error codes + suggestions (S12 / §6.4) ───────────

ERROR_PROVENANCE_MISSING = "PROVENANCE_MISSING"
ERROR_INVALID_KIND = "INVALID_KIND"
ERROR_WRITE_CONFLICT = "WRITE_CONFLICT"
ERROR_DEDUP_CONFLICT = "DEDUP_CONFLICT"
ERROR_EP_UPDATE_FAILED = "EP_UPDATE_FAILED"

ERROR_SUGGESTIONS: dict[str, str] = {
    ERROR_PROVENANCE_MISSING: "stamp source_session/source_harness/ingested_at",
    ERROR_INVALID_KIND: "kind must be statement (write-compat legacy kinds accepted)",
    ERROR_WRITE_CONFLICT: "resolve status branch before retrying",
    ERROR_DEDUP_CONFLICT: (
        "content-hash hit point blocks the link (retracted/cross-session conflict)"),
    ERROR_EP_UPDATE_FAILED: "retry dream() on the point; alpha/beta state unchanged",
}


def error_block(code: str) -> dict:
    """{code, suggestion} — the canonical error shape (§6.4)."""
    return {"code": code, "suggestion": ERROR_SUGGESTIONS.get(code, "")}


def point_entry(
    point_id: str,
    *,
    kind: str | None = None,
    status: str = "live",
    ep_updated: bool = False,
    dedup: str = DEDUP_NEW,
) -> dict:
    """One verb points[] entry.  ep_updated/dedup default to the honest
    Phase-A facts (no EP pass has run; only request-minted points are
    ``new``) — callers MUST override them with what they observed."""
    entry: dict = {
        "point_id": point_id,
        "status": status,
        "ep_updated": bool(ep_updated),
        "dedup": dedup,
    }
    if kind:
        entry["kind"] = kind
    return entry


# The envelope's protocol-owned keys — a surface's legacy response can
# never clobber them (guarded merge below).
_PROTOCOL_KEYS = frozenset(
    {"protocol_version", "status", "provenance", "points", "error"})


def build_write_verb(
    *,
    source_session: str,
    source_harness: str | None,
    ingested_at: str,
    points: list[dict] | None = None,
    status: str = STATUS_OK,
    error: dict | None = None,
    extra: dict | None = None,
) -> dict:
    """Build the memory_write_v1 envelope.  ``extra`` carries the surface's
    existing response keys (additive evolution per D8 — the verb keys are
    ADDED to the legacy response, never renamed/removed).  Merge rule:
    ``extra`` keys are merged EXCEPT the protocol-owned keys
    (``_PROTOCOL_KEYS``) which always win — except ``points``, where a
    legacy ``points`` list (capture's raw extracted points, enriched with
    status/ep_updated/dedup) is PREFERRED as the verb's per-point array
    (it already carries the same information under additive keys).  A
    surface can therefore never silently corrupt protocol_version/status/
    provenance/error with a same-named legacy key.
    """
    verb: dict = {
        "protocol_version": MEMORY_WRITE_V1,
        "status": status,
        "provenance": {
            "source_session": source_session,
            "source_harness": source_harness,
            "ingested_at": ingested_at,
        },
        "points": points or [],
        "error": error,
    }
    if extra:
        for k, v in extra.items():
            if k in _PROTOCOL_KEYS and k != "points":
                continue  # protocol keys always win
            verb[k] = v
    return verb


def assert_provenance(provenance: dict | None) -> dict | None:
    """Return the PROVENANCE_MISSING error block when a provenance dict does
    not carry all three required keys, else None.  The rejection seam write
    surfaces call BEFORE writing when provenance is caller-supplied (a write
    without provenance is rejected, never written-then-lamented).  The
    capture surface builds provenance SERVER-SIDE from session_id/harness/
    now (never caller-optional beyond harness, which normalizes to
    "unknown"), so the seam is defensive there and live on surfaces where
    the caller stamps provenance."""
    if not provenance:
        return error_block(ERROR_PROVENANCE_MISSING)
    missing = [
        k for k in ("source_session", "source_harness", "ingested_at")
        if not provenance.get(k)
    ]
    if missing:
        return error_block(ERROR_PROVENANCE_MISSING)
    return None
