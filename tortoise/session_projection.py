"""The session read projection — ONE declaration, derived from everywhere (#5498).

`GET /v1/sessions` and `GET /v1/sessions/{id}` both serve these fields; the
self-hosted CLI renderers (`tortoise/__main__.py`) print them and the parity
test asserts them. This module exists so there is exactly ONE copy of the
field list rather than one per consumer.

That matters because the failure this fixes was a *drift* failure: both CLI
surfaces shipped untested against the API they read, and both were wrong —
`session view` crashed on every session, and `session list` dropped six of
the nine fields. A parity assertion written against a second hardcoded list
would have drifted the same way; deriving every consumer from this module
makes the parity structural.

It is deliberately dependency-free (no FastAPI, no graph client) so the CLI
can import it without pulling the API stack.

#5498 note on the field that caused the crash: `turns` is a COUNT and
`turn_points` is the LIST. The old `session view` read `turns` as if it were
the list and called `len()` on an int. Do not "simplify" that distinction back.
"""

from __future__ import annotations

#: What `GET /v1/sessions` serves per session — the shared projection.
SESSION_LIST_FIELDS: tuple[str, ...] = (
    "id",
    "created_at",
    "turns",
    "extracted",
    "actor_user_id",
    "harness",
    "actor_display",
    "machine_id",
    "model",
)

#: What `GET /v1/sessions/{id}` serves — the shared projection plus these.
SESSION_DETAIL_FIELDS: tuple[str, ...] = (
    *SESSION_LIST_FIELDS,
    "turn_points",
    "extracted_points",
    "source",
)
