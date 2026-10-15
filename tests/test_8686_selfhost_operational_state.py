"""#8686 — the self-hosted capture control must actually persist.

THE DEFECT. The hosted write leg is

    MATCH (t:Team {id: $id}) SET t.onboarding_state = $state

A self-hosted install has NO control-plane team record. A ``MATCH`` that finds
nothing matches 0 rows **without raising**, so the write silently did nothing —
and because the caller's echo re-added the value it had just been asked to set,
the tool reported SUCCESS over a write that never happened. The user's only
capture control was a switch wired to nothing.

These tests pin the fix at the seam. The key one is
``test_a_write_lands_on_an_absent_node``: it FAILS against the pre-fix
MATCH-no-op (the blob is never stored, so the read returns ``{}``) and passes
once the write creates the node on demand.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.onboarding import state as st


class _Res:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    """Minimal store: node properties per org_id.

    Deliberately models the ONE property that matters — a node that does not
    exist. Without the create-on-write, ``SET n.operational_state = $blob`` has
    no node to write to and the value is dropped on the floor, which is exactly
    the production no-op.
    """

    def __init__(self):
        self.nodes: dict[str, dict] = {}

    def query(self, cypher, params=None, **kw):
        params = params if params is not None else kw
        org = params.get("org_id")
        if "operational_state = $blob" in cypher:
            # A real MATCH-no-op: no node -> no write, no error.
            if org in self.nodes:
                self.nodes[org][st.OPERATIONAL_PROP] = params["blob"]
            return _Res([])
        if "MERGE" in cypher:
            self.nodes.setdefault(org, {})
            return _Res([])
        if "RETURN n" in cypher:
            node = self.nodes.get(org)
            return _Res([] if node is None else [[node]])
        if cypher.lstrip().upper().startswith("MATCH") and " SET " in cypher:
            if org in self.nodes:
                self.nodes[org]["status"] = params.get("status")
            return _Res([])
        return _Res([])


def test_a_write_lands_on_an_absent_node():
    """THE PIN. An absent node is the production shape; the write must create
    it. Revert ``write_operational_state`` to a bare MATCH+SET and this goes
    RED with ``{} == {'session_recording': False}``."""
    g = _FakeGraph()
    assert g.nodes == {}, "precondition: nothing exists yet"

    st.write_operational_state(g, "selfhost", {"session_recording": False})

    assert st.read_operational_state(g, "selfhost") == {
        "session_recording": False}


def test_the_off_switch_survives_a_reread():
    """A switch that cannot be read back is the false ACK. Read twice."""
    g = _FakeGraph()
    st.write_operational_state(g, "selfhost", {"session_recording": False})
    assert st.read_operational_state(g, "selfhost")["session_recording"] is False
    assert st.read_operational_state(g, "selfhost")["session_recording"] is False


def test_keys_merge_and_do_not_clobber_each_other():
    """Toggling one key must not erase the others — the hosted leg merges, and
    a divergent self-hosted leg would be a second, silent data-loss path."""
    g = _FakeGraph()
    st.write_operational_state(g, "selfhost", {"session_recording": False})
    st.write_operational_state(g, "selfhost", {"capture_extract": False})
    got = st.read_operational_state(g, "selfhost")
    assert got == {"session_recording": False, "capture_extract": False}, got


def test_an_absent_node_reads_as_empty_not_as_defaults():
    """The seam returns what is STORED; the caller owns the defaults. If this
    ever returned defaults, the read leg's merge would be untestable and the
    'unknown' vs 'off' distinction would collapse."""
    assert st.read_operational_state(_FakeGraph(), "selfhost") == {}


def test_orgs_are_isolated():
    """Two deployments on one store must not see each other's capture state."""
    g = _FakeGraph()
    st.write_operational_state(g, "selfhost", {"session_recording": False})
    st.write_operational_state(g, "acme", {"session_recording": True})
    assert st.read_operational_state(g, "selfhost") == {
        "session_recording": False}
    assert st.read_operational_state(g, "acme") == {"session_recording": True}


@pytest.mark.parametrize("raw", ["not json", "{broken", 12345, b"\xff\xfe"])
def test_an_unreadable_blob_reads_as_empty_and_never_raises(raw):
    """A corrupt property must not take down the capture path (the setting
    falls back to its default) — it is REPORTED instead."""
    assert st._decode_operational(raw) == {}


def test_a_json_blob_round_trips():
    assert st._decode_operational(json.dumps({"session_recording": True})) == {
        "session_recording": True}


def test_the_cas_read_leg_agrees_with_the_store(monkeypatch):
    """#8686 review P1 — THE REGRESSION TEST THAT WAS MISSING.

    `_read_onboarding_state_and_version` is what the CAS fallback composes the
    dict it writes FROM. Without a selfhost branch it returned the full DEFAULT
    set (session_recording=True among it), and the write's merge put those stale
    defaults straight back over a stored False — the off-switch silently
    reverted on the next, unrelated write.

    This pins the read leg to the STORE, not to the defaults.
    """
    ha = pytest.importorskip("tortoise.hosted_api")
    g = _FakeGraph()
    st.write_operational_state(g, "selfhost", {"session_recording": False})
    monkeypatch.setattr(ha, "_org_proj", lambda org_id: g)

    state, version = ha._read_onboarding_state_and_version("selfhost")

    assert state["session_recording"] is False, (
        "the CAS read leg handed back the DEFAULT (True) instead of the stored "
        "False — this is the revert that made the switch a lie")
    assert version is None, "selfhost identity is absent; version must be None"
    assert "capture_extract" in state, "defaults must still be merged underneath"
