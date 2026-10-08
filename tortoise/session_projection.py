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
)

#: What `GET /v1/sessions` serves per session: the shared projection plus
#: `actor_display`, which the handler derives per-request from the member map.
SESSION_LIST_FIELDS: tuple[str, ...] = (
    "id", "created_at", "turns", "extracted",
    "actor_user_id", "harness", "actor_display", "machine_id", "model",
)

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
assert set(SESSION_LIST_FIELDS) == set(SESSION_READ_FIELDS) | {"actor_display"}, (
    f"list projection drifted: {sorted(SESSION_LIST_FIELDS)} vs "
    f"{sorted(set(SESSION_READ_FIELDS) | {'actor_display'})}")
assert set(SESSION_DETAIL_FIELDS) == set(SESSION_LIST_FIELDS) | {
    "turn_points", "extracted_points", "source",
}, (
    f"detail projection drifted: {sorted(SESSION_DETAIL_FIELDS)} vs "
    f"{sorted(set(SESSION_LIST_FIELDS) | {'turn_points', 'extracted_points', 'source'})}")
