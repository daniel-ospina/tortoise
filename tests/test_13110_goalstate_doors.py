"""#13110 — `goalState` must be settable through BOTH public doors.

The field existed, was validated, and neither customer-facing door could carry
it. Measured on the tree before this change (2026-11-17):

| door | `{"goalState": "unmet"}` |
|---|---|
| `POST /v1/objects` | **200, then the node reads back `goalState: None`** — pydantic's default `extra='ignore'` dropped the field, and an INVALID word was dropped just as silently (`"green"` also answered 200) |
| MCP `tortoise_create_object(goalState=…)` | **`unexpected keyword argument`** — the served schema is derived from the HANDLER's signature, which declared `props` but not the field, so an agent reading `tools/list` could not learn it exists |
| MCP `tortoise_create_object(props={"goalState": …})` | already worked (the free-form bag reaches the SDK) — reported as measured, NOT claimed as this change's work |
| SDK `sdk.create_object(..., goalState=…)` | already worked |

So the two defects were the SILENT DROP (REST) and the UNDECLARED field (MCP).
This file pins one test per door in both directions, plus the two properties
that keep the axes from drifting: the REST refusal and the MCP refusal name the
SAME closed set, and a re-create that does not name the field never wipes a
stored one.

Falsification (how RED-on-the-old-tree was proved) is in the PR body: with
`CreateObjectRequest.goalState` removed, both `rest` create tests fail on the
read-back (`None != 'unmet'`) and the invalid-word test fails on the status
(200, not 422); with the MCP parameter removed, the declared-parameter test
raises TypeError and the schema test fails on `goalState` being absent from
`inputSchema.properties`.

Runnable with:
    uv run python tools/test_lane.py run -- tests/test_13110_goalstate_doors.py -v
"""
from __future__ import annotations

import asyncio
import contextlib
import os

import pytest

# #67: mandatory before `tortoise.auth` import in a hosted-API test process.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tortoise.sdk import GOAL_STATE_VALUES  # noqa: E402

_ORG = "team-13110"


def _team() -> dict:
    """The org dict `get_current_org` resolves — full limits (a stub missing
    them makes the quota gate 500 instead of passing, #310)."""
    return {
        "org_id": _ORG, "key_id": "test-key-13110", "tier": "free",
        "graph_id": None, "scopes": [], "legacy_full_access": True,
        "delegation_depth": None, "created_by_key_id": None,
        "max_users": 1, "max_graphs": 1, "max_points": 100000,
        "max_api_keys": 2, "max_sessions": None,
    }


def _seed_registry(ha) -> None:
    """The MCP/quota lanes read the quota from the REGISTRY graph, so an org
    that exists nowhere is refused before any write — provision the `:Team`
    node the production path creates."""
    sdk = ha._make_sdk(namespace="registry")
    try:
        sdk._get_registry().query("CREATE (t:Team {id:$id})", params={"id": _ORG})
    finally:
        sdk.close()


@contextlib.contextmanager
def _graph(db_path):
    """One temp graph both doors in a test can share (so a write made through
    REST is visible to a read made after an MCP call)."""
    from tests._http_fixtures import patched_tortoise_sdk

    with patched_tortoise_sdk(db_path):
        import tortoise.hosted_api as ha

        _seed_registry(ha)
        yield ha


@contextlib.contextmanager
def _rest_client():
    from tortoise.hosted_api import app, get_current_org
    from tests._verdict import verdict_client

    app.dependency_overrides[get_current_org] = _team
    try:
        with verdict_client(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


@contextlib.contextmanager
def _mcp_ctx():
    """The MCP transport ContextVars the tool bodies read (#236's gate), on the
    shared graph — what a stdio caller is."""
    from tortoise import mcp_auth

    tokens = [
        mcp_auth._transport_mode.set("stdio"),
        mcp_auth._current_org_id.set(_ORG),
        mcp_auth._current_org_limits.set(
            {"org_id": _ORG, "tier": "free", "max_points": 100000,
             "max_sessions": None}),
    ]
    try:
        yield
    finally:
        for var, token in reversed(list(zip(
                (mcp_auth._transport_mode, mcp_auth._current_org_id,
                 mcp_auth._current_org_limits), tokens, strict=True))):
            var.reset(token)


def _stored(ha, node_id: str):
    """The PERSISTED goalState — a create-return echo is not evidence."""
    sdk = ha._make_sdk(namespace=_ORG)
    try:
        return sdk.get_entity(node_id).get("goalState")
    finally:
        sdk.close()


def _objects_named(ha, name: str) -> int:
    sdk = ha._make_sdk(namespace=_ORG)
    try:
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {name:$n}) RETURN count(o)",
            params={"n": name}).result_set
        return rows[0][0] if rows else 0
    finally:
        sdk.close()


def _vocabulary_in(text: str) -> bool:
    """Every member of the closed set is named by the refusal (#13110 asks for
    the vocabulary, not just a rejection)."""
    return all(word in text for word in sorted(GOAL_STATE_VALUES))


# ── Door 1 — REST `POST /v1/objects` ────────────────────────────────────────

def test_rest_create_persists_and_returns_goal_state(tmp_path):
    with _graph(str(tmp_path / "rest.db")) as ha, _rest_client() as client:
        r = client.post("/v1/objects", json={
            "name": "REVENUE", "objectKind": "goal", "goalState": "unmet"})
        assert r.status_code == 200, r.text
        node = r.json()
        assert node["goalState"] == "unmet", node
        assert _stored(ha, node["id"]) == "unmet"


def test_rest_create_refuses_an_unknown_word_with_the_closed_set(tmp_path):
    """RED on the pre-fix tree in the WORST direction: 200 with the value
    dropped. A field that cannot be set correctly must not fail silently when
    set incorrectly either."""
    with _graph(str(tmp_path / "rest-bad.db")) as ha, _rest_client() as client:
        r = client.post("/v1/objects", json={
            "name": "BAD-GOAL", "objectKind": "goal", "goalState": "green"})
        assert r.status_code == 422, (r.status_code, r.text)
        assert _vocabulary_in(r.text), r.text
        # The refusal lands BEFORE the write: the goal was never created.
        assert _objects_named(ha, "BAD-GOAL") == 0


@pytest.mark.parametrize("word", ["draft", "live", "retracted"])
def test_rest_refuses_a_lifecycle_word_as_a_goal_state(tmp_path, word):
    """ONTOLOGY §4.3: `status` and `goalState` are ORTHOGONAL axes — a lifecycle
    word is not a member of the achievement vocabulary, and the refusal says
    so rather than quietly accepting it (the pre-#7856 behaviour)."""
    with _graph(str(tmp_path / f"rest-{word}.db")) as _ha, _rest_client() as client:
        r = client.post("/v1/objects", json={
            "name": f"LIFECYCLE-{word}", "objectKind": "goal",
            "goalState": word})
        assert r.status_code == 422, (r.status_code, r.text)
        assert "goalState is not" in r.text, r.text


def test_rest_re_create_without_the_field_does_not_wipe_a_stored_state(tmp_path):
    """This door MERGEs by name, so "absent" must stay absent — forwarding a
    default would wipe a stored achievement state on every repeat call, the
    #9572 clobber class documented on `objectKind`."""
    with _graph(str(tmp_path / "rest-repeat.db")) as ha, _rest_client() as client:
        first = client.post("/v1/objects", json={
            "name": "REPEAT", "objectKind": "goal", "goalState": "met"})
        assert first.status_code == 200, first.text
        again = client.post("/v1/objects", json={
            "name": "REPEAT", "objectKind": "goal"})
        assert again.status_code == 200, again.text
        assert _stored(ha, first.json()["id"]) == "met"


def test_rest_explicit_null_is_accepted_and_clearing_stays_the_update_door(tmp_path):
    """`None` is a LEGAL value (never a 4xx) — it is the explicit "no
    achievement state". Measured SDK contract, which this door must not
    re-invent: on the CREATE fold a named `None` preserves a stored state (so a
    re-create cannot clobber), while `update_knowledge(…, goalState=None)`
    CLEARS it. Pinned here so a later "None should clear" reading of the issue
    cannot silently turn the create door into a wiping door."""
    with _graph(str(tmp_path / "rest-null.db")) as ha, _rest_client() as client:
        first = client.post("/v1/objects", json={
            "name": "NULLED", "objectKind": "goal", "goalState": "active"})
        assert first.status_code == 200, first.text
        node_id = first.json()["id"]
        r = client.post("/v1/objects", json={
            "name": "NULLED", "objectKind": "goal", "goalState": None})
        assert r.status_code == 200, (r.status_code, r.text)
        assert _stored(ha, node_id) == "active"
        # The CLEAR is the update door's, and it does clear.
        sdk = ha._make_sdk(namespace=_ORG)
        try:
            sdk.update_knowledge(node_id, goalState=None)
        finally:
            sdk.close()
        assert _stored(ha, node_id) is None


# ── Door 2 — MCP `tortoise_create_object` ───────────────────────────────────

def test_mcp_create_persists_the_declared_goal_state_parameter(tmp_path):
    """The exact call the issue reports as rejected
    (`goalState=` as a top-level keyword)."""
    from tortoise import mcp_server

    with _graph(str(tmp_path / "mcp-param.db")) as ha, _mcp_ctx():
        out = mcp_server.tortoise_create_object(
            "MCP-GOAL", "goal", goalState="blocked")
        assert isinstance(out, dict) and not out.get("error"), out
        assert out["goalState"] == "blocked", out
        assert _stored(ha, out["id"]) == "blocked"


def test_mcp_create_still_accepts_the_props_bag_form(tmp_path):
    """The form that already worked before this change — it must keep working
    (a client written against the old surface is not broken by the new one)."""
    from tortoise import mcp_server

    with _graph(str(tmp_path / "mcp-props.db")) as ha, _mcp_ctx():
        out = mcp_server.tortoise_create_object(
            "MCP-PROPS-GOAL", "goal", props={"goalState": "guideline"})
        assert not out.get("error"), out
        assert _stored(ha, out["id"]) == "guideline"


def test_mcp_refuses_an_unknown_word_with_the_closed_set(tmp_path):
    from tortoise import mcp_server

    with _graph(str(tmp_path / "mcp-bad.db")) as _ha, _mcp_ctx():
        for out in (
            mcp_server.tortoise_create_object("MCP-BAD", "goal",
                                              goalState="green"),
            mcp_server.tortoise_create_object("MCP-BAD-2", "goal",
                                              props={"goalState": "green"}),
        ):
            assert isinstance(out, dict) and out.get("error"), out
            assert _vocabulary_in(out["error"]), out


def test_the_served_schema_advertises_goal_state():
    """The DISCOVERABILITY half: `props` is an untyped `Any` with no
    description, so an agent reading `tools/list` could not learn the field
    existed and reached for the keyword that did not exist. The declared
    parameter is what puts it in the advertised contract."""
    from tortoise import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    spec = next(t for t in tools if t.name == "tortoise_create_object")
    assert "goalState" in spec.parameters["properties"], spec.parameters
    # A parameter, NOT a new surface: the tool keeps its name and its required
    # arguments, and the SDK method behind it is the same one.
    assert spec.parameters["required"] == ["name", "objectKind"]


# ── The two doors cannot drift apart ────────────────────────────────────────

@pytest.mark.parametrize("word", sorted(GOAL_STATE_VALUES))
def test_both_doors_store_every_member_identically(tmp_path, word):
    """One vocabulary, one store, two spellings: every member must be settable
    and stored identically through the REST door and the MCP door (the issue's
    indicator 4). A door that accepted a word the other refused — or stored a
    different one — is the drift this pins shut."""
    from tortoise import mcp_server

    with (_graph(str(tmp_path / f"both-{word}.db")) as ha,
          _rest_client() as client, _mcp_ctx()):
        r = client.post("/v1/objects", json={
            "name": f"REST-{word}", "objectKind": "goal", "goalState": word})
        assert r.status_code == 200, r.text
        mcp_out = mcp_server.tortoise_create_object(
            f"MCP-{word}", "goal", goalState=word)
        assert not mcp_out.get("error"), mcp_out
        assert _stored(ha, r.json()["id"]) == word
        assert _stored(ha, mcp_out["id"]) == word


def test_the_sdk_guard_is_the_only_home_of_the_vocabulary(tmp_path):
    """No second copy of the closed set: the REST refusal and the SDK write
    boundary must be the SAME sentence, produced by one function. A door that
    restated the vocabulary would drift the moment the set is widened."""
    from tortoise.sdk import TortoiseSDK, validate_goal_state

    with pytest.raises(ValueError) as via_guard:
        validate_goal_state("green")
    guard_message = str(via_guard.value)
    assert _vocabulary_in(guard_message)

    sdk = TortoiseSDK(str(tmp_path / "guard.db"))
    try:
        with pytest.raises(ValueError) as via_write:
            sdk.create_object("GUARD", objectKind="goal", goalState="green")
    finally:
        sdk.close()
    assert str(via_write.value) == guard_message

    # …and the REST door's 422 body is that same sentence, not a paraphrase.
    with _graph(str(tmp_path / "guard-rest.db")) as _ha, _rest_client() as client:
        r = client.post("/v1/objects", json={
            "name": "GUARD-REST", "objectKind": "goal", "goalState": "green"})
        assert r.status_code == 422, (r.status_code, r.text)
        assert guard_message in r.text, r.text
