"""The session read projection — ONE declaration, derived from everywhere (#5498).

`GET /v1/sessions` and `GET /v1/sessions/{id}` both serve these fields; the
SDK's `get_session` read, the self-hosted CLI renderers (`tortoise/__main__.py`)
and the parity tests all derive from them. This module exists so there is
exactly ONE copy of the field list rather than one per consumer.

That matters because the failure this fixes was a *drift* failure: both CLI
surfaces shipped untested against the API they read, and both were wrong —
`session view` crashed on every session, and `session list` dropped six of
the nine fields. A parity assertion written against a second hardcoded list
would have drifted the same way; deriving every consumer from this module
makes the parity structural.

It is deliberately dependency-free (no FastAPI, no graph client) so the CLI
can import it without pulling the API stack. `tortoise/sdk.py` re-exports
`SESSION_READ_FIELDS` from here, so the SDK-side declaration that
`tests/test_hosted_api.py` binds is this one — not a parallel copy.

#5498 note on the field that caused the crash: `turns` is a COUNT and
`turn_points` is the LIST. The old `session view` read `turns` as if it were
the list and called `len()` on an int. Do not "simplify" that distinction back.
"""

from __future__ import annotations

#: The durable-session read projection (#3557): the field list the hosted read
#: surfaces (`GET /v1/sessions` and `GET /v1/sessions/{id}`) both serve, and
#: the subset `TortoiseSDK.get_session` returns for a captured `:Session`.
#:
#: This is the SDK-side declaration (re-exported from `tortoise/sdk.py` under
#: this same name) and the parity test BINDS the surfaces in BOTH directions:
#: it iterates this tuple (a field dropped from or renamed on the SDK read
#: reddens it) AND pins the detail response's key SET to this tuple plus the
#: detail endpoint's known extras — `actor_display`, `turn_points`,
#: `extracted_points`, `source` — so a column ADDED to the hosted detail
#: handler reddens it too, the direction inline columns would otherwise let
#: drift silently. The `GET /v1/sessions` LIST key set is pinned the same way
#: (this tuple plus `actor_display`); its `extracted` COUNT is pinned too — the
#: list counts with the same non-turn predicate as the detail endpoint and the
#: SDK read, so all three agree (#3555).
#:
#: Ordered as the hosted handlers append their columns: existing positions are
#: stable and new columns go at the END, so a consumer reading positionally
#: never shifts. (`GET /v1/sessions` additionally serves `actor_display`; the
#: by-id endpoint additionally serves `actor_display`, the point lists and
#: `source`; those are derived per-request and are deliberately not part of
#: this shared list — they are the additions below.)
SESSION_READ_FIELDS: tuple[str, ...] = (
    "id", "created_at", "turns", "extracted",
    "actor_user_id", "harness", "machine_id", "model",
    # #3520: the per-session EXTRACTION status object. APPENDED at the END by
    # the rule this module documents — `list_sessions` builds its wire dicts
    # from POSITIONAL columns, so a column inserted anywhere earlier silently
    # re-binds `r[0..3]` to the wrong values.
    "extraction",
    # #3516: the capture-provenance triplet. The capture path has STORED these
    # since #3516 §B / #3515 piece 12 (`sdk._write_session_and_turns`), but the
    # read projection never served them — so `client_capture_floor_verdict`,
    # the store-proven floor's own verdict function, could not be called with a
    # real record by ANY consumer. That is why it had zero production callers:
    # not a missing wiring, a missing READ. Serving them is the precondition
    # for the install-verify path to evaluate the floor at all.
    #
    # They are client-claimed and NULL on a legacy / hook-less session, exactly
    # as `machine_id`/`model` are — same class, same position rule (APPENDED, so
    # a positional consumer of the earlier columns never shifts).
    "client_captured_at", "client_captured_at_source", "capture_lane",
)

#: What `GET /v1/sessions` serves per session: the shared projection plus
#: `actor_display`, which the handler derives per-request from the member map.
SESSION_LIST_FIELDS: tuple[str, ...] = (
    "id", "created_at", "turns", "extracted",
    "actor_user_id", "harness", "actor_display", "machine_id", "model",
    "extraction",
    # #3516: the capture-provenance triplet, mirrored from SESSION_READ_FIELDS
    # (the bind below fails loudly if these two ever diverge).
    "client_captured_at", "client_captured_at_source", "capture_lane",
)

#: #3520: the session-scoped EXTRACTION-status vocabulary — the members any
#: value of ``extraction["status"]`` must be drawn from.
#:
#: Deliberately SEPARATE from the per-harness INSTALL vocabulary (`off` /
#: `install-pending` / `waiting` / `active`, which lives dashboard-side in
#: `captureStatus.js::CAPTURE_STATES` and is UNCHANGED — still exactly four
#: members). The two axes measure different things: install is per harness
#: (a probe + a receipt), extraction is per session (what happened to THIS
#: session's memory). Conflating them would leave the aggregate undefined
#: whenever some sessions processed and some did not, so an install pill
#: stays `active` beside a `stored-extraction-failed` session and there is no
#: harness-level rollup of this axis.
#:
#: THREE members, deliberately — a fourth (`stored-outcome-unrecorded`) was
#: considered and REFUSED: the pinned set's set-agreement test is worth more
#: than a member, and the "stored, outcome unrecorded" state is carried by the
#: `outcome_known` qualifier BESIDE the set, not inside it.
SESSION_EXTRACTION_STATES: tuple[str, ...] = (
    "stored",
    "stored-and-processed",
    "stored-extraction-failed",
)

#: #3892 / #4258: the extractor lanes that mean "no extraction was ATTEMPTED".
#: A keyless capture (`none`) and the team's extraction off-switch
#: (`disabled`) both record `capture_ok=False` on the Session — but nothing
#: FAILED, so reporting them as `stored-extraction-failed` would be a NEW
#: false positive of exactly the class #3520 exists to remove. They are
#: "stored, and known not to have been processed" — the honest reading of the
#: `stored` member with `outcome_known` true. The lane names are the same two
#: `_CAPTURE_EXTRACTOR_LANES_RETRYABLE` carries (hosted_api), declared here so
#: this module stays dependency-free.
SESSION_STORE_ONLY_LANES: tuple[str, ...] = ("none", "disabled")


def session_extraction_status(
    capture_ok: bool | None, capture_extractor: str | None,
) -> dict:
    """The server-resolved per-session extraction status (#3520).

    ONE place derives this, and every read surface calls it — the hosted
    ``GET /v1/sessions`` and ``GET /v1/sessions/{id}`` plus the SDK's
    ``get_session`` — so no consumer re-implements the mapping and no two
    surfaces can disagree about one session.

    ``status`` is ALWAYS a member of ``SESSION_EXTRACTION_STATES``; the
    qualifier rides BESIDE it:

    * ``capture_ok is True``  -> ``stored-and-processed``;
    * ``capture_ok is False`` and the recorded lane RAN extraction
      (``v2`` / ``m2``) -> ``stored-extraction-failed``;
    * ``capture_ok is False`` on a store-only lane (extraction never
      attempted, by the #3892 keyless ruling or the #4258 team toggle) ->
      ``stored``;
    * ``capture_ok is None`` -> ``stored`` with ``outcome_known False``.

    ``outcome_known`` is the pinned qualifier: ``capture_ok IS NOT NULL``.
    It is what carries the "stored, outcome UNRECORDED" case — a
    commit-created / budget-hold / demo-seeded / longmem-ingest session was
    written by a driver that never records an extraction outcome, so a naive
    ``stored`` would read as "was not processed" for a session that may well
    have been. A consumer must therefore read the PAIR: (status, qualifier).
    The dashboard's label table (`capturedSessions.js`) renders the False
    qualifier as its own honest state rather than the bare `stored` word.

    ``extractor`` is the lane that ran, verbatim as recorded — ``v2`` / ``m2``
    for a real attempt, ``none`` / ``disabled`` for a store-only write, and
    ``None`` when the record predates the lane property. It is returned BESIDE
    the status because the status alone cannot say WHY a session was not
    processed; the pair is what a reader needs.
    """
    outcome_known = capture_ok is not None
    if capture_ok is True:
        status = "stored-and-processed"
    elif capture_ok is False:
        status = ("stored" if capture_extractor in SESSION_STORE_ONLY_LANES
                  else "stored-extraction-failed")
    else:
        status = "stored"
    # The binding, not a comment: a member added here without the declaration
    # (or the reverse) fails at the derivation every read surface calls.
    assert status in SESSION_EXTRACTION_STATES, (
        f"extraction status {status!r} is outside the declared vocabulary "
        f"{SESSION_EXTRACTION_STATES}")
    return {
        "status": status,
        "extractor": capture_extractor,
        "outcome_known": outcome_known,
    }

#: What `GET /v1/sessions/{id}` serves: the list projection plus the detail-only
#: fields (the turn/point lists and the session source).
SESSION_DETAIL_FIELDS: tuple[str, ...] = (
    *SESSION_LIST_FIELDS,
    "turn_points",
    "extracted_points",
    "source",
)

# The three lists are one declaration in three shapes, not three opinions. These
# binds fail loudly at import if a future edit lets them diverge — the drift
# class this module exists to make unrepresentable.
assert len(SESSION_EXTRACTION_STATES) == len(set(SESSION_EXTRACTION_STATES)) == 3, (
    "the extraction vocabulary is exactly THREE members (#3520) — a fourth "
    "member carries no meaning the `outcome_known` qualifier does not, and the "
    "per-harness INSTALL set is a different axis, never a place to put this one")
assert set(SESSION_LIST_FIELDS) == set(SESSION_READ_FIELDS) | {"actor_display"}, (
    f"list projection drifted: {sorted(SESSION_LIST_FIELDS)} vs "
    f"{sorted(set(SESSION_READ_FIELDS) | {'actor_display'})}")
assert set(SESSION_DETAIL_FIELDS) == set(SESSION_LIST_FIELDS) | {
    "turn_points", "extracted_points", "source",
}, (
    f"detail projection drifted: {sorted(SESSION_DETAIL_FIELDS)} vs "
    f"{sorted(set(SESSION_LIST_FIELDS) | {'turn_points', 'extracted_points', 'source'})}")
