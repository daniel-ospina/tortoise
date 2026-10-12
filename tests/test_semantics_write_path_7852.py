"""#7852 W1 — the Layer-2 `semantics` write path (owner-approved: "w1 yes approved").

The three-layer relation model (ONTOLOGY §8) records a direction DERIVED FROM
the relation's meaning. Before this change `create_operator` and
`tortoise_create_operator` took no `semantics` at all, so the chain's step 2
("the edge's declared meaning → the direction") could not fire and the
direction always fell through to the per-op_type default — the shadowing #7813
exists to remove.

What this pins:
  * a declared `semantics` resolves the direction and records the basis
    (`direction_basis == "label"`);
  * an EXPLICIT non-default `direction` outranks the semantics (`"explicit"`);
  * the historical default is UNCHANGED when no semantics is given
    (`"default"`, bidirectional for every op_type — the #7813 ruling);
  * an unknown semantics warns and never blocks; a retired one resolves to its
    canonical replacement; a neutral one (`-`) carries no direction;
  * the MCP tool exposes the parameter; and the basis survives a journal
    rebuild (`derived = replay(journal)`).

Runnable embedded:
  TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_semantics_write_path_7852.py -q
"""
from __future__ import annotations

import os

import pytest

from tortoise.pack_registry import SEMANTICS_VALUES
from tortoise.sdk import TortoiseSDK


def _point(sdk, content: str) -> dict:
    return sdk.create_point("statement", content, status="live")


def _stored(sdk, op_id: str) -> tuple[str, str]:
    row = sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) RETURN o.direction, o.direction_basis",
        params={"id": op_id},
    ).result_set[0]
    return row[0], row[1]


@pytest.fixture()
def sdk(tmp_path):
    db = os.path.join(str(tmp_path), "semantics.db")
    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield s
    s.close()


# ── the chain fires: meaning → direction ─────────────────────────────────

@pytest.mark.parametrize("semantics,expected", [
    ("addresses", "unidirectional"),
    ("supports", "unidirectional"),
    ("refutes", "unidirectional"),
    ("hasPart", "bidirectional"),
    ("mutuallyExclusive", "bidirectional"),
    ("mutuallySupportive", "bidirectional"),
])
def test_semantics_resolves_direction(sdk, semantics, expected):
    """A declared Layer-2 meaning records the direction it carries — the chain
    step that had no way to run before this change."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    op = sdk.create_operator("NAND", a["id"], [b["id"]], semantics=semantics)
    direction, basis = _stored(sdk, op["id"])
    assert direction == expected, (semantics, direction)
    assert basis == "label", basis
    assert op.get("direction") == expected


def test_explicit_direction_outranks_semantics(sdk):
    """An explicit NON-default direction wins: the caller actually declared it,
    so the meaning cannot silently reverse it (chain step 1)."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    op = sdk.create_operator(
        "NAND", a["id"], [b["id"]],
        direction="unidirectional", semantics="mutuallyExclusive",
    )
    assert _stored(sdk, op["id"]) == ("unidirectional", "explicit")


def test_default_unchanged_without_semantics(sdk):
    """No semantics → the historical default, unchanged for every op_type
    (the #7813 ruling keeps the SDK default at bidirectional)."""
    a, b, c = _point(sdk, "a"), _point(sdk, "b"), _point(sdk, "c")
    impl = sdk.create_operator("IMPL", a["id"], [b["id"]])
    nand = sdk.create_operator("NAND", a["id"], [c["id"]])
    assert _stored(sdk, impl["id"]) == ("bidirectional", "default")
    assert _stored(sdk, nand["id"]) == ("bidirectional", "default")


# ── validation is warn-not-block (never refuses) ─────────────────────────

def test_unknown_semantics_warns_and_proceeds(sdk):
    """An out-of-vocabulary word names the valid alternatives and the write
    proceeds — the write path must never refuse on the common case."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]], semantics="sideways")
    assert _stored(sdk, op["id"]) == ("bidirectional", "default")
    warns = [w for w in op.get("warnings", []) if w.get("code") == "undeclared_relation"]
    assert warns, op.get("warnings")
    assert "sideways" in warns[0]["message"]
    assert "addresses" in warns[0]["message"]


def test_retired_semantics_resolves_to_canonical(sdk):
    """A word the shipped packs still declare (`opposes`) is recognised and
    resolved to its canonical replacement, then resolves the direction."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    op = sdk.create_operator("NAND", a["id"], [b["id"]], semantics="opposes")
    assert _stored(sdk, op["id"]) == ("bidirectional", "label")
    warns = [w for w in op.get("warnings", []) if w.get("code") == "undeclared_relation"]
    assert warns and "mutuallyExclusive" in warns[0]["message"]


def test_neutral_semantics_carries_no_direction(sdk):
    """`related` is `-`: the connection exists and no confidence transfers, so
    no direction is recorded — the chain falls through and says so."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]], semantics="related")
    assert _stored(sdk, op["id"]) == ("bidirectional", "default")
    warns = [w for w in op.get("warnings", []) if w.get("code") == "undeclared_relation"]
    assert warns and "no transferable direction" in warns[0]["message"]


def test_every_ruled_semantics_maps_or_is_neutral(sdk):
    """Exhaustive over the closed vocabulary: a ruled value either resolves to a
    runtime direction or is the neutral `-` class. Guards against a vocabulary
    addition silently becoming unmapped."""
    a, b = _point(sdk, "a"), _point(sdk, "b")
    for semantics in sorted(SEMANTICS_VALUES):
        op = sdk.create_operator("IMPL", a["id"], [b["id"]], semantics=semantics)
        direction, basis = _stored(sdk, op["id"])
        assert direction in ("bidirectional", "unidirectional")
        assert basis in ("label", "default"), (semantics, basis)


# ── the MCP surface exposes it ────────────────────────────────────────────

def test_mcp_tool_exposes_semantics(sdk, monkeypatch):
    """The surface agents actually call accepts `semantics` and resolves the
    direction through it."""
    import tortoise.mcp_server as mcp
    from tortoise.mcp_auth import _current_org_id, _transport_mode
    # The module-level `sdk` is `_get_sdk`'s documented test-swap override.
    monkeypatch.setattr(mcp, "sdk", sdk, raising=False)
    tok_mode = _transport_mode.set("stdio")
    tok_org = _current_org_id.set(None)
    try:
        a, b = _point(sdk, "a"), _point(sdk, "b")
        res = mcp.tortoise_create_operator(
            "NAND", a["id"], [b["id"]], semantics="refutes")
        assert "error" not in res, res
        assert _stored(sdk, res["id"]) == ("unidirectional", "label")
    finally:
        _transport_mode.reset(tok_mode)
        _current_org_id.reset(tok_org)


# ── rebuild parity: the basis is a journaled node property ───────────────

def test_direction_basis_survives_rebuild(tmp_path):
    """`derived = replay(journal)`: the basis written live must come back from
    the journal, not be silently dropped by the replay."""
    db = os.path.join(str(tmp_path), "rebuild.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    try:
        a, b = _point(sdk, "a"), _point(sdk, "b")
        op = sdk.create_operator("NAND", a["id"], [b["id"]], semantics="refutes")
        live = _stored(sdk, op["id"])
        assert live == ("unidirectional", "label")

        sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
        assert _stored(sdk, op["id"]) == live
    finally:
        sdk.close()
