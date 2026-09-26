"""#4240 — the hosted lane must JOURNAL its ``aboutObject`` edges.

The defect (pre-fix): ``hosted_api._make_sdk`` / ``_data_sdk`` built every
hosted SDK with **no** ``event_log_path``, so the JSONL-only reassembly records
(``EntityLinked``, ``SessionRecorded``, ``ObjectRegistered``, ``PointAdded``)
were silent no-ops. The hosted capture's entity attachment was therefore
**live-only**: ``rebuild_all`` / ``rebuild`` / ``recover_from_log`` had nothing
to replay and the ``(Session|Point)-[:aboutObject]->(Object)`` edges vanished
while the rebuild reported success (the #2296 hazard class).

These tests pin the two named hosted call sites:
  * the SDK-construction seam both sites share (``_make_sdk``), and
  * ``_relink_sessions_after_index`` (the index-completion re-link).

Class-B doctrine — every test states (1) the value/state that makes it fail and
(2) that the state is reachable in its fixture:

* ``test_make_sdk_wires_the_journal_when_the_base_dir_is_set`` —
  FAILS while ``_event_log_path is None``; the fixture sets
  ``TORTOISE_EVENT_LOG_BASE_DIR`` (reachable: it is the documented switch).
* ``test_capture_aboutobject_edges_survive_rebuild_all`` —
  FAILS while the capture's edges are absent from the journal (the rebuild's
  edge set collapses to empty); the fixture mints a resolvable WorkItem Object
  and captures a conversation that references it, so the live edge set is
  non-empty (reachable: pinned by the pre-rebuild assertion).
* ``test_relink_aboutobject_edge_survives_rebuild_all`` —
  FAILS while the re-link's ``EntityLinked`` record is not written; the fixture
  captures BEFORE the Object exists (honest no-match) then materializes it, so
  the re-link really creates an edge (reachable: pinned on the live count).

The journal is a DOMAIN EVENT LOG, never the durability mechanism
(``docs/durability-posture.md``): these tests assert the derived graph is
REBUILDABLE, not that anything keeps it alive.
"""
from __future__ import annotations

import contextlib
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tortoise import hosted_api as ha

# A conversation whose turn text carries a resolvable GitHub issue URL — the
# `session_link` trigger the capture's entity-linking pass reads.
CONV = [
    {"role": "user",
     "content": "we must ship github.com/test/repo/issues/42 today; "
                "the auth dead-end is the top issue."},
    {"role": "assistant", "content": "agreed"},
]


@pytest.fixture(autouse=True)
def _mock_extractor(monkeypatch):
    # The offline v2 seam — no provider key / network.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture
def hosted_lane(tmp_path, monkeypatch):
    """The hosted lane's own SDK constructor, with a per-run journal base dir.

    Uses whatever ``TORTOISE_DB_URI`` the lane provides (the docker test graph)
    so the REAL ``_make_sdk`` is under test — no monkeypatched constructor.
    """
    events = tmp_path / "events"
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(events))
    org = f"test-4240-{uuid.uuid4().hex[:10]}"
    yield SimpleNamespace(org=org, events=events, tmp_path=tmp_path)
    # Best-effort teardown: drop the test graph so a shared container does not
    # accumulate leaked graphs (#2979). The conftest hygiene sweep owns
    # `test_`-prefixed graphs too; this is the belt to that braces.
    with contextlib.suppress(Exception):
        _drop = ha._make_sdk(namespace=org)
        _drop._get_proj().g.query("MATCH (n) DETACH DELETE n")
        _drop.close()


def _about_edges(proj) -> set:
    """(source_label, source_id, target_key) for every live aboutObject edge.

    Not a count: a count cannot tell a Session edge from a turn edge.
    """
    rows = proj.g.query(
        "MATCH (s)-[:aboutObject]->(o:Object) "
        "RETURN labels(s), coalesce(s.id, s.eventId, s.name), "
        "coalesce(o.id, o.name)").result_set
    return {(tuple(r[0]), r[1], r[2]) for r in rows}


def _journal_types(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            with contextlib.suppress(ValueError):
                out.append(json.loads(line).get("type"))
    return out


# ── the resolver (§ the lane's per-graph journal path) ─────────────────────

def test_resolve_event_log_path_is_per_graph_and_none_when_unset(monkeypatch):
    """Unset base dir ⇒ None (byte-identical to pre-#4240); set ⇒ one file per
    graph, keyed by the lane's own graph-name derivation, and the control-plane
    registry / default graph are excluded.

    MUTATION: drop the ``if not base: return None`` guard → an unconfigured
    lane starts writing a journal at ``None/…`` (RED). Drop the registry
    exclusion → the control plane gets an org data journal (RED).
    """
    monkeypatch.delenv("TORTOISE_EVENT_LOG_BASE_DIR", raising=False)
    assert ha._resolve_event_log_path(namespace="acme", graph_name=None) is None
    assert ha._resolve_event_log_path(namespace=None, graph_name=None) is None

    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", "/data/events")
    p = ha._resolve_event_log_path(namespace="acme", graph_name=None)
    assert p == "/data/events/org_acme/events.jsonl", p
    # An explicit full graph name is used verbatim (the custom-org-graph seam).
    p = ha._resolve_event_log_path(namespace=None, graph_name="org_abc_def")
    assert p == "/data/events/org_abc_def/events.jsonl", p
    # Two projects in one namespace never share a journal file.
    assert (ha._resolve_event_log_path(namespace="acme", graph_name=None)
            != ha._resolve_event_log_path(namespace="acme2", graph_name=None))
    # The control plane is not an org data graph — never journaled here.
    assert ha._resolve_event_log_path(namespace="registry", graph_name=None) is None


# ── the seam: the hosted SDK constructor carries the journal ───────────────

def test_make_sdk_wires_the_journal_when_the_base_dir_is_set(hosted_lane):
    """``_make_sdk(namespace=…)`` must build a journaled SDK when
    ``TORTOISE_EVENT_LOG_BASE_DIR`` is set.

    MUTATION: drop the ``event_log_path=`` argument from ``_make_sdk``'s
    ``TortoiseSDK(...)`` construction → ``_get_event_log()`` returns None and
    every JSONL-only record is a silent no-op (the pre-#4240 state) → RED.
    """
    sdk = ha._make_sdk(namespace=hosted_lane.org)
    try:
        log = sdk._get_event_log()
        assert log is not None, (
            "hosted _make_sdk built a journal-less SDK — every JSONL-only "
            "record (EntityLinked / SessionRecorded / ObjectRegistered) is a "
            "silent no-op, so a rebuild cannot reconstruct the capture (#4240)"
        )
        expected = ha._resolve_event_log_path(
            namespace=hosted_lane.org, graph_name=None)
        assert str(log.path) == expected, (str(log.path), expected)
    finally:
        sdk.close()


# ── the capture path: aboutObject edges survive rebuild_all ────────────────

def test_capture_aboutobject_edges_survive_rebuild_all(hosted_lane):
    """A hosted-lane capture's Session/turn aboutObject edges must survive
    ``rebuild_all`` — live == rebuild.

    Reachable: the fixture mints the WorkItem Object and captures a
    conversation referencing it, so the live edge set is asserted non-empty
    BEFORE the rebuild.

    MUTATION: drop the journal wiring → the capture's PointAdded /
    ObjectRegistered / SessionRecorded / EntityLinked records never land, the
    rebuild replays an empty journal and the edge set collapses to empty → RED.
    """
    sdk = ha._make_sdk(namespace=hosted_lane.org)
    try:
        proj = sdk._get_proj()
        sdk.create_object("test/repo#42", objectKind="pm:issue")
        r = sdk.capture_session(CONV, session_id="s-4240-capture")
        assert r.get("ok") is True, r

        live = _about_edges(proj)
        assert live, "fixture must produce live aboutObject edges to lose"
        assert any(src == ("Session",) for src, _, _ in live), live
        assert any(src == ("Point",) for src, _, _ in live), live

        log_path = Path(ha._resolve_event_log_path(
            namespace=hosted_lane.org, graph_name=None))
        types = _journal_types(log_path)
        assert "EntityLinked" in types, (
            f"the capture's links were not journaled: {sorted(set(types))}")
        assert "PointAdded" in types and "ObjectRegistered" in types, (
            f"the capture's nodes were not journaled, so the EntityLinked "
            f"fold has no endpoints to resolve: {sorted(set(types))}")

        proj.rebuild_all(str(log_path.parent))
        assert _about_edges(proj) == live, (
            "aboutObject edge drift across rebuild\n"
            f" live={sorted(live)}\n post={sorted(_about_edges(proj))}")
    finally:
        sdk.close()


# ── the index-completion re-link path ──────────────────────────────────────

def test_relink_aboutobject_edge_survives_rebuild_all(hosted_lane):
    """The index-completion re-link (``_relink_sessions_after_index``) writes
    ``aboutObject`` edges through ``_make_sdk(namespace=org_id)``; they must be
    journaled and survive a rebuild.

    Reachable: the session is captured BEFORE its Object exists (an honest
    no-match), then the Object materializes and the re-link creates the edge —
    asserted non-empty live before the rebuild.

    MUTATION: drop the journal wiring in ``_make_sdk`` → the re-link's
    ``EntityLinked`` record is a no-op and the rebuilt edge set is empty → RED.
    """
    org = hosted_lane.org
    sdk = ha._make_sdk(namespace=org)
    try:
        proj = sdk._get_proj()
        # Capture first — no Object yet, so the conversation-reference pass is
        # an honest no-match (the extractor's own claim edges are a different
        # source and do not count).
        r = sdk.capture_session(CONV, session_id="s-4240-relink")
        assert r.get("ok") is True, r
        assert not any(src == ("Session",)
                       for src, _, _ in _about_edges(proj)), \
            "no referenced entity yet — the Session pass must be an honest no-match"

        # Index completes → the WorkItem Object materializes.
        sdk.create_object("test/repo#42", objectKind="pm:issue")
        ha._relink_sessions_after_index(org)

        live = _about_edges(proj)
        assert any(src == ("Session",) for src, _, _ in live), \
            "the re-link must resolve the now-present Object"

        log_path = Path(ha._resolve_event_log_path(namespace=org, graph_name=None))
        assert "EntityLinked" in _journal_types(log_path), (
            "the re-link's EntityLinked record was not journaled")

        proj.rebuild_all(str(log_path.parent))
        assert _about_edges(proj) == live, (
            "re-linked aboutObject edge drift across rebuild\n"
            f" live={sorted(live)}\n post={sorted(_about_edges(proj))}")
    finally:
        sdk.close()
