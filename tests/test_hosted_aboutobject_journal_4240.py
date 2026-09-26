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

They also pin the two review findings on the wiring:

* **F2** — the journal key must be the graph the SDK ACTUALLY opens. The
  derivation lives in ``sdk._derive_graph_name`` (ONE source of truth); a local
  re-derivation in the resolver diverged on the hyphenated ``test-*`` branch
  (``test-foo`` opens ``test_foo_tortoise``, not ``org_test-foo``), so
  ``test_journal_key_matches_the_graph_the_sdk_opens`` asserts the two agree,
  and ``test_no_journal_key_can_escape_the_base_dir`` pins the traversal guard.
* **F1** — a configured journal whose append fails is not silent:
  ``test_unwritable_journal_is_disclosed_and_counted`` pins the ERROR/counter
  signal and the additive capture-receipt warning. The append stays fail-soft
  by design (the graph mutation already succeeded), so ``ok`` remains True —
  flipping it would trip the #2335 TRUE-retry gate over a persisted write.

Class-B doctrine — every test states (1) the value/state that makes it fail and
(2) that the state is reachable in its fixture:

* ``test_make_sdk_wires_the_journal_when_the_base_dir_is_set`` — FAILS while
  ``_event_log_path is None``; the fixture sets ``TORTOISE_EVENT_LOG_BASE_DIR``
  (reachable: it is the documented switch).
* ``test_journal_key_matches_the_graph_the_sdk_opens`` — FAILS while the
  resolver derives its own key (the pre-F2 state: ``org_test-…`` vs
  ``test_…_tortoise``); reachable: the fixture namespace is hyphenated.
* ``test_no_journal_key_can_escape_the_base_dir`` — FAILS while a ``..`` key is
  accepted; reachable: the pre-F2 guard kept ``.``.
* ``test_capture_aboutobject_edges_survive_rebuild_all`` — FAILS while the
  capture's edges are absent from the journal (the rebuild's edge set
  collapses to empty); the fixture mints a resolvable WorkItem Object and
  captures a conversation that references it, so the live edge set is
  non-empty (reachable: pinned by the pre-rebuild assertion). **Honest RED:** at
  the pre-#4240 base this test fails with ``AttributeError`` (no
  ``_resolve_event_log_path``) — a wiring pin, not the drift. The drift itself
  is exercised by ``test_live_only_capture_loses_aboutobject_edges_without_the_journal``.
* ``test_relink_aboutobject_edge_survives_rebuild_all`` — FAILS while the
  re-link's ``EntityLinked`` record is not written; the fixture captures BEFORE
  the Object exists (honest no-match) then materializes it, so the re-link
  really creates an edge (reachable: pinned on the live count). Same honest-RED
  note as above.
* ``test_live_only_capture_loses_aboutobject_edges_without_the_journal`` — the
  ONE test whose RED is the drift itself: it mutates only the wiring (the
  pre-#4240 journal-less construction) and shows the live-only edges do NOT
  survive ``rebuild_all``.
* ``test_unwritable_journal_is_disclosed_and_counted`` — FAILS while a failed
  append is silent (no counter, no receipt warning); reachable: the fixture
  chmods the journal base dir to 0500.

The journal is a DOMAIN EVENT LOG, never the durability mechanism
(``docs/durability-posture.md``): these tests assert the derived graph is
REBUILDABLE, not that anything keeps it alive.
"""
from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tortoise import hosted_api as ha
from tortoise import monitoring
from tortoise.sdk import TortoiseSDK

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
    graph, keyed by the SDK's OWN derivation, and the control-plane registry /
    default graph are excluded.

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
    # F2: the hyphenated test-* namespace maps to `<ns>_tortoise`, NOT
    # `org_<ns>` — the branch the pre-fix local derivation got wrong.
    p = ha._resolve_event_log_path(namespace="test-foo", graph_name=None)
    assert p == "/data/events/test_foo_tortoise/events.jsonl", p
    # Two projects in one namespace never share a journal file.
    assert (ha._resolve_event_log_path(namespace="acme", graph_name=None)
            != ha._resolve_event_log_path(namespace="acme2", graph_name=None))
    # The control plane is not an org data graph — never journaled here.
    assert ha._resolve_event_log_path(namespace="registry", graph_name=None) is None


def test_no_journal_key_can_escape_the_base_dir(monkeypatch):
    """F2(b): a journal key that is not a valid graph name must be REFUSED, not
    sanitized — sanitizing silently keyed the journal to a directory no graph
    uses, and the pre-fix guard kept ``.`` so ``..`` survived to the SDK's own
    constructor (i.e. the stated anti-traversal guard was dead).

    MUTATION: restore the sanitize-instead-of-validate guard → ``..`` is
    accepted and this test REDs (path would be ``/data/events/../events.jsonl``).
    Reachable: the SDK's graph-name rule rejects ``..``/``.``/``/``; the
    resolver runs BEFORE that constructor, so it is the only guard at build time.
    """
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", "/data/events")
    for bad in ("..", ".", "a/b", "a.b", "org x", ""):
        with pytest.raises(ValueError):
            ha._resolve_event_log_path(namespace=None, graph_name=bad)
    # And a bad namespace cannot smuggle one in through the derivation either.
    with pytest.raises(ValueError):
        ha._resolve_event_log_path(namespace="a/b", graph_name=None)


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


def test_journal_key_matches_the_graph_the_sdk_opens(hosted_lane):
    """F2(a): the journal key must be the graph ``_get_proj`` actually opens.

    A second, local derivation in the resolver keyed the journal to a graph the
    SDK never opened (the hyphenated ``test-*`` branch: ``org_test-…`` vs
    ``test_…_tortoise``), so a rebuild would replay the wrong file. This asserts
    the single-source-of-truth property directly.

    MUTATION: reintroduce a local key derivation in ``_resolve_event_log_path``
    → the hyphenated fixture namespace diverges → RED. Reachable: the fixture
    org is ``test-4240-<hex>``, a name that exercises the divergent branch.
    """
    sdk = ha._make_sdk(namespace=hosted_lane.org)
    try:
        proj = sdk._get_proj()
        log = sdk._get_event_log()
        assert log is not None
        assert Path(log.path).parent.name == proj.graph_name, (
            "journal key and the graph the SDK opened disagree — a rebuild "
            f"would replay the wrong file: key={Path(log.path).parent.name!r} "
            f"graph={proj.graph_name!r}")
        # The explicit graph-name seam keeps the same property.
        custom = ha._make_sdk(graph_name="test_4240_custom_tortoise")
        try:
            assert (Path(custom._get_event_log().path).parent.name
                    == custom._get_proj().graph_name
                    == "test_4240_custom_tortoise")
        finally:
            custom.close()
    finally:
        sdk.close()


# ── the capture path: aboutObject edges survive rebuild_all ────────────────

def test_capture_aboutobject_edges_survive_rebuild_all(hosted_lane):
    """A hosted-lane capture's Session/turn aboutObject edges must survive
    ``rebuild_all`` — live == rebuild.

    What this pins: the COMPOSITE seam (the resolver exists AND ``_make_sdk``
    passes its result to the SDK AND the fold restores the edges). Its RED at
    the pre-#4240 base is ``AttributeError`` (``_resolve_event_log_path`` does
    not exist), so it does NOT by itself demonstrate the edge drift — the drift
    is demonstrated by
    ``test_live_only_capture_loses_aboutobject_edges_without_the_journal``.

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

    What this pins: the composite re-link seam, same honest-RED note as
    ``test_capture_aboutobject_edges_survive_rebuild_all``.

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


# ── the honest RED for the drift: mutate ONLY the wiring ───────────────────

def test_live_only_capture_loses_aboutobject_edges_without_the_journal(
        hosted_lane, monkeypatch):
    """The #4240 drift itself, reproduced with the wiring mutated (F4).

    The two ``live == rebuild`` tests above cannot show the drift at the pre-fix
    base (they fail on ``AttributeError`` first). This test removes ONLY the
    journal wiring — exactly the pre-#4240 ``_make_sdk`` — while every other
    piece is present, so the failure it produces IS the live-only edge loss the
    issue describes.

    Reachable: the live edge set is asserted non-empty BEFORE the rebuild, and
    the journal file is asserted absent (nothing landed to replay).

    MUTATION (inverse): wire the journal back in → the rebuild preserves the
    edges and the ``not post`` assertion REDs.
    """

    def _journal_less_make_sdk(*, namespace=None, graph_name=None):
        # The pre-#4240 construction: identical graph seam, no event_log_path.
        return TortoiseSDK(namespace=namespace, graph_name=graph_name)

    monkeypatch.setattr(ha, "_make_sdk", _journal_less_make_sdk)
    hosted_lane.events.mkdir(parents=True, exist_ok=True)

    sdk = ha._make_sdk(namespace=hosted_lane.org)
    try:
        proj = sdk._get_proj()
        sdk.create_object("test/repo#42", objectKind="pm:issue")
        r = sdk.capture_session(CONV, session_id="s-4240-liveonly")
        assert r.get("ok") is True, r

        live = _about_edges(proj)
        assert live, "fixture must produce live aboutObject edges to lose"
        assert not (hosted_lane.events / proj.graph_name / "events.jsonl").exists(), \
            "the journal-less SDK must not have written a journal"

        proj.rebuild_all(str(hosted_lane.events))
        post = _about_edges(proj)
        assert not post, (
            "the live-only edges SURVIVED a rebuild_all — the mutation is inert "
            "and the drift is not being exercised\n"
            f" live={sorted(live)}\n post={sorted(post)}")
    finally:
        sdk.close()


# ── F1: a failed append on a CONFIGURED journal is not silent ──────────────

@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root bypasses directory mode bits")
def test_unwritable_journal_is_disclosed_and_counted(tmp_path, monkeypatch):
    """F1: with ``TORTOISE_EVENT_LOG_BASE_DIR`` set to an EXISTING but
    unwritable dir (the reviewer's repro: mode 0500), ``mkdir -p`` succeeds and
    every append fails. The capture must still succeed at the graph level
    (``ok`` True — the write persisted) but must NO LONGER be silent: the
    failure counter increments and the receipt carries an additive warning.

    Reachable: chmod 0500 makes ``EventLog.append``'s ``mkdir``/``open`` raise
    PermissionError, which ``_emit_event`` catches.

    MUTATION: restore the WARNING-only swallow → no counter, no receipt warning
    → RED. Note ``ok`` is intentionally still True: marking the capture failed
    would trip the #2335 TRUE-retry gate over a write that persisted.
    """
    events = tmp_path / "events"
    events.mkdir()
    events.chmod(0o500)
    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(events))
    org = f"test-4240-{uuid.uuid4().hex[:10]}"
    before = monitoring.journal_write_failure_count()

    sdk = ha._make_sdk(namespace=org)
    try:
        sdk.create_object("test/repo#42", objectKind="pm:issue")
        r = sdk.capture_session(CONV, session_id="s-4240-unwritable")
        assert r.get("ok") is True, r
        assert any("journal append failed" in w for w in r["warnings"]), (
            "a failed append on a configured journal was not disclosed on the "
            f"capture receipt: {r['warnings']}")
        assert monitoring.journal_write_failure_count() > before, (
            "a failed append must increment the journal-write-failure counter")
        assert monitoring.metrics().get("journal_write_failures", 0) >= \
            monitoring.journal_write_failure_count() - before, \
            "the failure counter must ride monitoring.metrics()"
    finally:
        sdk.close()
        events.chmod(0o700)  # restore before teardown
    with contextlib.suppress(Exception):
        _drop = ha._make_sdk(namespace=org)
        _drop._get_proj().g.query("MATCH (n) DETACH DELETE n")
        _drop.close()
