"""Preview-then-confirm deletion (#9660, owner ruling 2026-10-25).

The owner approved a confirmation step for permanent deletion, because a bare
``confirm=True`` flag is set by the assistant in the same breath as the call.
These tests pin the protocol that replaces the flag:

* a preview **changes nothing** and describes the blast radius;
* a token is bound to ONE target and cannot be carried to another;
* an expired, tampered, or foreign token **raises** and deletes nothing — it
  never degrades into a bare delete;
* the delete itself still goes through :meth:`TortoiseSDK.delete`, so every kind
  keeps its own path.

No database: the token module is pure, and the SDK-level cases drive
``delete_confirmed`` through a projection double, so the protocol is testable
without a live graph (the delete paths themselves are covered by the existing
delete tests).
"""

from __future__ import annotations

import pytest

from tortoise import deletion
from tortoise.sdk import TortoiseSDK


class _QueryResult:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    def __init__(self, edges):
        self._edges = edges

    def query(self, statement, params=None):  # noqa: ARG002 - signature parity
        return _QueryResult([[self._edges]])


class _FakeProj:
    """Just the surface ``delete_confirmed`` reads: resolution and the edge count."""

    def __init__(self, node=None, edges=0):
        self.node = node
        self.g = _FakeGraph(edges)

    def _resolve_entity(self, id_val, *, by_id=True, by_eventId=True, by_url=True):  # noqa: N803
        if self.node is None:
            return []
        return [self.node]


class _FakeSDK(TortoiseSDK):
    """A TortoiseSDK whose projection and delete are doubled."""

    def __init__(self, *, node=None, edges=0):
        self._proj = _FakeProj(node, edges)
        self.delete_calls = []

    def _get_proj(self):
        return self._proj

    def delete(self, id):
        self.delete_calls.append(id)
        return True


def _point(id="1a16c18e0a2-11a121b63f34", content="Pricing is $49 per seat"):
    return {"label": "Point", "key": "id", "value": id,
            "properties": {"id": id, "content": content}}


# --------------------------------------------------------------------------
# the token module
# --------------------------------------------------------------------------

def test_a_token_confirms_the_target_it_was_issued_for():
    token = deletion.issue("p1", kind="Point")
    payload = deletion.verify(token, target="p1")
    assert payload["t"] == "p1" and payload["k"] == "Point"


def test_a_token_cannot_be_carried_to_a_different_target():
    token = deletion.issue("p1")
    with pytest.raises(deletion.ConfirmationInvalid):
        deletion.verify(token, target="p2")


def test_an_expired_token_is_refused():
    token = deletion.issue("p1", ttl_seconds=1, now=1000.0)
    with pytest.raises(deletion.ConfirmationExpired):
        deletion.verify(token, target="p1", now=1001.5)


def test_a_token_issued_to_another_caller_is_refused():
    token = deletion.issue("p1", actor="agent-a")
    with pytest.raises(deletion.ConfirmationInvalid):
        deletion.verify(token, target="p1", actor="agent-b")
    # ...and it still works for the caller it names.
    assert deletion.verify(token, target="p1", actor="agent-a")["t"] == "p1"


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", "!!!!.!!!!"])
def test_a_malformed_token_is_refused(token):
    with pytest.raises(deletion.ConfirmationInvalid):
        deletion.verify(token, target="p1")


def test_a_tampered_payload_never_verifies():
    token = deletion.issue("p1")
    body, signature = token.split(".", 1)
    forged_body = body[:-2] + ("AA" if body[-2:] != "AA" else "BB")
    with pytest.raises(deletion.ConfirmationInvalid):
        deletion.verify(f"{forged_body}.{signature}", target="p1")


def test_the_receipt_is_a_preview_not_a_delete():
    token = deletion.issue("p1", kind="Point")
    receipt = deletion.receipt(target="p1", kind="Point", title="Pricing is $49 per seat",
                               edges=4, token=token)
    assert receipt["deleted"] is False and receipt["preview"] is True
    assert receipt["confirmation"] == token
    assert receipt["edges"] == 4
    assert "4 connections" in receipt["summary"]
    assert "cannot be undone" in receipt["warning"].lower()


# --------------------------------------------------------------------------
# the MCP tool
# --------------------------------------------------------------------------

class _FakeSDK:
    def __init__(self):
        self.delete_calls = []

    def delete(self, id):
        self.delete_calls.append(id)
        return True


def _wire(monkeypatch, *, preview_found=True):
    """Drive `tortoise_delete` with the preview and the SDK doubled."""
    from tortoise import mcp_server

    sdk = _FakeSDK()
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: sdk)
    monkeypatch.setattr(mcp_server, "_safe", lambda fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(
        mcp_server, "_preview_delete",
        lambda _sdk, id: {"found": preview_found,
                          "target": {"id": id, "label": "Point"},
                          "nodes_removed": 1, "edges_removed": 4,
                          "nodes": [{"id": id, "label": "Point"}],
                          "edges": [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}, {"id": "e4"}]},
    )
    return mcp_server, sdk


def test_the_first_call_changes_nothing_and_returns_a_token(monkeypatch):
    mcp_server, sdk = _wire(monkeypatch)
    result = mcp_server.tortoise_delete("1a16c18e0a2-11a121b63f34")
    assert sdk.delete_calls == [], "a preview must not delete"
    assert result["deleted"] is False and result["preview"] is True
    assert result["edges_removed"] == 4
    assert deletion.verify(result["confirmation"], target=result["id"])["t"] == result["id"]


def test_dry_run_is_the_same_preview_and_never_deletes(monkeypatch):
    mcp_server, sdk = _wire(monkeypatch)
    result = mcp_server.tortoise_delete("1a16c18e0a2-11a121b63f34", dry_run=True)
    assert sdk.delete_calls == []
    assert result["preview"] is True
    assert deletion.verify(result["confirmation"], target=result["id"])["t"] == result["id"]


def test_the_second_call_with_the_token_deletes_once(monkeypatch):
    mcp_server, sdk = _wire(monkeypatch)
    token = mcp_server.tortoise_delete("1a16c18e0a2-11a121b63f34")["confirmation"]
    result = mcp_server.tortoise_delete("1a16c18e0a2-11a121b63f34", confirmation=token)
    assert sdk.delete_calls == ["1a16c18e0a2-11a121b63f34"]
    assert result == {"deleted": True, "id": "1a16c18e0a2-11a121b63f34"}


def test_an_invalid_token_deletes_nothing_and_raises(monkeypatch):
    mcp_server, sdk = _wire(monkeypatch)
    with pytest.raises(deletion.ConfirmationInvalid):
        mcp_server.tortoise_delete("1a16c18e0a2-11a121b63f34", confirmation="forged.token")
    assert sdk.delete_calls == []


def test_a_token_for_another_item_deletes_nothing(monkeypatch):
    mcp_server, sdk = _wire(monkeypatch)
    other = deletion.issue("a-different-id")
    with pytest.raises(deletion.ConfirmationInvalid):
        mcp_server.tortoise_delete("1a16c18e0a2-11a121b63f34", confirmation=other)
    assert sdk.delete_calls == []


def test_an_expired_token_deletes_nothing(monkeypatch):
    mcp_server, sdk = _wire(monkeypatch)
    stale = deletion.issue("1a16c18e0a2-11a121b63f34", ttl_seconds=1, now=1000.0)
    with pytest.raises(deletion.ConfirmationExpired):
        mcp_server.tortoise_delete("1a16c18e0a2-11a121b63f34", confirmation=stale)
    assert sdk.delete_calls == []


def test_the_registry_description_states_the_protocol():
    """The description is the only thing an agent reads before calling (#9660).

    Pinned because the previous text promised a `dry_run` preview while the tool
    deleted by default with no confirmation at all — the promise was in prose
    and absent from the behaviour.
    """
    from tortoise.tool_registry import TOOL_REGISTRY

    entry = next(t for t in TOOL_REGISTRY if t.name == "tortoise_delete")
    assert entry.risk == "destructive"
    assert "confirmation=<token>" in entry.description
    assert "The FIRST call changes nothing" in entry.description
    assert "cannot be undone" in entry.description.lower()
