"""#5498 — the CLI session read surfaces must match the API they read.

Both shipped untested and both were wrong:

* ``tortoise session view`` raised on EVERY session — it read the ``turns``
  COUNT as if it were the turn LIST and called ``len()`` on an int.
* ``tortoise session list`` rendered only ``ID / Turns / Created``, dropping
  six of the nine fields the shared projection carries.

The parity assertions below iterate ``SESSION_LIST_FIELDS`` — the ONE shared
declaration in ``tortoise/session_projection.py`` — rather than a hardcoded
subset. That is the point: a test carrying its own copy of the field list
would drift exactly as the CLI did. Deriving every consumer from one
declaration makes parity structural rather than asserted.
"""

from __future__ import annotations

import io
import json
import re
from unittest import mock

from tortoise.__main__ import (
    _cmd_session_list,
    _cmd_session_view,
    _session_fields,
)
from tortoise.hosted_api import _session_row_dict
from tortoise.session_projection import (
    SESSION_DETAIL_FIELDS,
    SESSION_LIST_FIELDS,
)

# A realistic `GET /v1/sessions/{id}` body. `turns` is the COUNT and the LIST
# is `turn_points` — this is the exact shape the old CLI crashed on.
DETAIL = {
    "id": "sess-1",
    "created_at": "2026-09-26T00:00:00Z",
    "turns": 2,
    "extracted": 3,
    "actor_user_id": "3326a01e-aaaa-bbbb-cccc-ddddeeeeffff",
    "harness": "claude-code",
    "actor_display": "me@example.com",
    "machine_id": "mbp-14",
    "model": "claude-sonnet-4",
    "turn_points": [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ],
    "extracted_points": [],
    "source": "cli",
}

# A positional row exactly as `list_sessions`' SQL RETURN clause produces it.
ROW = (
    "sess-1",                      # s.id
    "2026-09-26T00:00:00Z",        # s.created_at
    2,                             # s.turn_count
    3,                             # count(p)
    "3326a01e-aaaa-bbbb-cccc-ddddeeeeffff",   # s.actor_user_id
    "claude-code",                 # s.harness
    "mbp-14",                      # s.machine_id
    "claude-sonnet-4",             # s.model
)


def _fake_urlopen(payload):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return lambda *a, **k: _Resp(json.dumps(payload).encode())


# ── the P0: it crashed on every session ───────────────────────────────────


def test_session_view_renders_turn_points_without_crashing(capsys):
    """`len()` on the `turns` COUNT raised TypeError on every session."""
    args = mock.Mock(id="sess-1")
    with mock.patch("urllib.request.urlopen", _fake_urlopen(DETAIL)):
        rc = _cmd_session_view(args, "k", "http://example.invalid")

    out = capsys.readouterr().out
    assert rc == 0
    # the count comes from `turns`, the rendered turns come from `turn_points`
    assert re.search(r"^turns\s+2$", out, re.M), out
    assert "[1] USER: hello" in out
    assert "[2] ASSISTANT: hi" in out


def test_turns_is_a_count_and_turn_points_is_the_list():
    """The distinction the old CLI got wrong — pinned so it cannot come back."""
    assert isinstance(DETAIL["turns"], int)
    assert isinstance(DETAIL["turn_points"], list)
    assert DETAIL["turns"] == len(DETAIL["turn_points"])


# ── parity: iterate the SHARED declaration, never a local subset ──────────


def test_cli_renders_every_field_of_the_shared_projection():
    """Every declared field must be visible — the six-field gap this fixes."""
    rendered = _session_fields(DETAIL)
    missing = [f for f in SESSION_LIST_FIELDS if f not in rendered]
    assert not missing, f"CLI drops declared projection field(s): {missing}"
    # and the values are actually carried, not blanked
    assert rendered["id"] == "sess-1"
    assert rendered["turns"] == "2"
    assert rendered["extracted"] == "3"
    assert rendered["harness"] == "claude-code"
    assert rendered["actor_display"] == "me@example.com"
    assert rendered["machine_id"] == "mbp-14"
    assert rendered["model"] == "claude-sonnet-4"


def test_cli_renders_nothing_outside_the_declaration():
    """The other direction: the CLI must not invent fields of its own."""
    payload = dict(DETAIL)
    payload["not_a_projection_field"] = "surprise"
    assert set(_session_fields(payload)) == set(SESSION_LIST_FIELDS)


def test_api_row_dict_serves_exactly_the_shared_projection():
    """The BINDING: the wire dict and the declaration cannot disagree.

    Exercised on the real unit (`_session_row_dict`), not re-implemented here —
    a test that rebuilds the dict it is checking would be green on a no-op.
    """
    d = _session_row_dict(ROW, {})
    assert set(d) == set(SESSION_LIST_FIELDS)
    assert d["turns"] == 2  # the COUNT, straight from s.turn_count
    # fail-soft: with no member map the display FALLS BACK to the raw id
    assert d["actor_display"] == ROW[4]
    d2 = _session_row_dict(ROW, {ROW[4]: "me@x.com"})
    assert d2["actor_display"] == "me@x.com"
    # a legacy row with NO actor resolves to None, not to a stringified None
    legacy = (*ROW[:4], None, *ROW[5:])
    assert _session_row_dict(legacy, {})["actor_display"] is None


def test_detail_projection_is_the_shared_list_plus_detail_only_fields():
    """The detail payload is the shared projection plus the detail extras."""
    assert set(SESSION_DETAIL_FIELDS) >= set(SESSION_LIST_FIELDS)
    extras = set(SESSION_DETAIL_FIELDS) - set(SESSION_LIST_FIELDS)
    assert extras == {"turn_points", "extracted_points", "source"}
    missing = [f for f in SESSION_DETAIL_FIELDS if f not in DETAIL]
    assert not missing, f"detail fixture is missing declared field(s): {missing}"


# ── the renderers themselves, not just the helper they share ──────────────
#
# `_session_fields` being correct does NOT prove the commands use it. A test
# that only exercises the helper stays green if `_cmd_session_list` is reverted
# to its old hardcoded `ID / Turns / Created` table — which is the very
# regression this change fixes. So drive the REAL command bodies.


def test_session_list_command_renders_every_declared_field(capsys):
    """The regression: `session list` dropped 6 of the 9 declared fields."""
    with mock.patch(
        "urllib.request.urlopen", _fake_urlopen({"sessions": [DETAIL]})
    ):
        rc = _cmd_session_list("k", "http://example.invalid")

    out = capsys.readouterr().out
    assert rc == 0
    for field in SESSION_LIST_FIELDS:
        assert field in out, f"`session list` drops declared field {field!r}"
    # the VALUES, not merely the labels
    assert "claude-code" in out
    assert "mbp-14" in out
    assert "claude-sonnet-4" in out
    assert "me@example.com" in out


def test_session_list_command_handles_a_session_with_no_actor(capsys):
    """A legacy row must render `-`, never the string `None`."""
    legacy = dict(DETAIL)
    legacy["actor_user_id"] = None
    legacy["actor_display"] = None
    legacy["machine_id"] = None
    legacy["model"] = None
    with mock.patch(
        "urllib.request.urlopen", _fake_urlopen({"sessions": [legacy]})
    ):
        rc = _cmd_session_list("k", "http://example.invalid")

    out = capsys.readouterr().out
    assert rc == 0
    assert "None" not in out


def test_session_view_command_renders_every_declared_detail_field(capsys):
    """`session view` prints 5 fields by hand before this — not the other 4."""
    args = mock.Mock(id="sess-1")
    with mock.patch("urllib.request.urlopen", _fake_urlopen(DETAIL)):
        rc = _cmd_session_view(args, "k", "http://example.invalid")

    out = capsys.readouterr().out
    assert rc == 0
    for field in SESSION_DETAIL_FIELDS:
        if field == "id":  # rendered as the `Session:` header
            continue
        assert field in out, f"`session view` drops declared field {field!r}"


# ── ONE declaration, not two that must be edited in lockstep ──────────────


def test_the_sdk_read_tuple_is_the_same_object_as_the_shared_declaration():
    """#3557 declared these in `sdk.py`; #5498 hoisted them into one module.

    Identity, not equality: an equal-but-separate tuple in `sdk.py` would be a
    second copy that has to be edited in lockstep — the drift class this change
    removes. `tests/test_hosted_api.py` binds whichever object `tortoise.sdk`
    exposes, so that assertion only means something if it IS this one.
    """
    from tortoise.sdk import SESSION_READ_FIELDS as SDK_READ_FIELDS
    from tortoise.session_projection import SESSION_READ_FIELDS

    assert SDK_READ_FIELDS is SESSION_READ_FIELDS
