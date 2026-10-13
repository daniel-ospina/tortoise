"""#7951 §17.1 — the single-writer guard: **a direct cache write FAILS.**

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.1 is a consequence of the §2.0
owner ruling of 2026-10-08 (**Postgres is the system of record; FalkorDB is a
derived hot cache**), and it states the rule as a rule:

    > every write goes to the truth store first, and the cache is projected
    > from it.

Its reason is definitional:

    > **A derived cache that is also written to directly is not derived.**

`#8127` built the chokepoint (`TortoiseSDK._apply_and_journal`, journal-first
then project) and `#8315` enumerated the doors to the fold — and both
deliberately left `#7951` OPEN, because the fold stayed **directly callable**:
the enumeration is a drift guard over three AST node kinds, not a soundness
proof, and no lexical scan can be sound by construction. This suite pins the
third step — **enforce it** — so a direct cache write *raises* rather than
silently succeeding:

* a **direct fold** (`FalkorProjection.apply`) on a journal-backed projection
  is refused (`DirectCacheWriteRefused`) — the "hidden door" class the door
  inventory names as the remaining S2 failure mode;
* a **mutating statement on the public raw-query lane**
  (`FalkorProjection.query`) is refused;
* the **sanctioned** paths still fold: the chokepoint appends first and folds
  inside the live scope, replay engines fold inside the replay scope, and every
  other public SDK write is unaffected.

The guard is RUNTIME and FAIL-CLOSED (default = REFUSAL) because the failure
mode is a *new* unjournaled writer appearing silently. It is armed by the
journal binding (`FalkorProjection._bind_journal`, called by the live doors
that own a journal), so a projection with no journal configured is outside
§17.1's reach — a boundary this suite pins explicitly rather than leaving to be
discovered.

⛔ SCOPE — what this suite does NOT claim. It does not claim the raw guarded
handle (`proj.g.query`) is closed: that handle is *also* the SDK's own live
write path (`create_point`'s `CREATE (:Point)`, `update_point`'s
`SET n += $props`), so a blanket refusal there would break the product, not a
violation of it. Converting that 300+-site surface is §17.1's mechanism (1),
the atomic write, and it remains OPEN on `#7951`.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from tortoise.projection import (
    DirectCacheWriteRefused,
    _live_write_scope,
    _replay_write_scope,
)


@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK with a configured JSONL journal (so the guard is ARMED).

    Lane-agnostic, exactly like the door inventory's fixture: the explicit
    `db_path` makes the projection embedded when no URI is configured, and the
    SDK's own construction is redirected to the docker server in a URI-set
    session. `event_log_path` is what arms §17.1's guard.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "enforce.db"), event_log_path=str(journal))
    sdk._journal_path_for_test = journal  # convenience for the assertions
    try:
        yield sdk
    finally:
        sdk.close()


def _journal_types(path: Path) -> list[str]:
    if not path.exists():
        return []
    out: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            out.append(json.loads(raw).get("type"))
    return out


def _count(proj, label: str, prop: str, value: str) -> int:
    return int(proj.g.query(
        f"MATCH (n:{label} {{{prop}:$v}}) RETURN count(n)",
        params={"v": value},
    ).result_set[0][0])


def _subject(proj, sid: str) -> dict:
    return {"type": "SubjectAdded", "id": sid, "name": sid}


# ── the load-bearing pin: a direct fold FAILS ─────────────────────────────

def test_a_direct_fold_on_a_journal_backed_projection_is_refused(
        sdk_with_journal):
    """RED before this lane (and GREEN after): a direct `proj.apply` folds.

    Before §17.1's enforcement, `FalkorProjection.apply` folded ANY event
    handed to it — so an unjournaled writer put the DERIVED store ahead of the
    TRUTH with no record and no repair path (`rebuild_all` replays the journal,
    does not reproduce the node, and the write is gone in silence). The guard
    makes that a loud `DirectCacheWriteRefused` instead.

    MUTATION: drop the guard in `FalkorProjection.apply` and this test fails
    with `DID NOT RAISE` — the write silently lands.
    """
    sdk = sdk_with_journal
    proj = sdk._get_proj()
    assert proj._journal_backed, (
        "the fixture must ARM the guard (`event_log_path` configured); an "
        "unarmed projection is outside §17.1 and this test would be vacuous"
    )

    with pytest.raises(DirectCacheWriteRefused) as exc:
        proj.apply(_subject(proj, "subj-direct"))

    assert "17.1" in str(exc.value), (
        "the refusal must cite the rule it enforces so the next writer can "
        f"read why; message={str(exc.value)!r}"
    )


def test_a_refused_fold_lands_nothing(sdk_with_journal):
    """The refusal is PRE-WRITE: no half-folded node, no derived-ahead-of-truth.

    A guard that raised *after* the fold would be worse than none — it would
    leave exactly the corruption it claims to prevent, with a stack trace on
    top."""
    proj = sdk_with_journal._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.apply(_subject(proj, "subj-nothing"))
    assert _count(proj, "Subject", "id", "subj-nothing") == 0


# ── the raw-query lane ────────────────────────────────────────────────────

def test_a_mutating_statement_on_the_raw_query_lane_is_refused(
        sdk_with_journal):
    """`FalkorProjection.query` is the raw lane a caller reaches for to write
    the cache without the projection; on a journal-backed projection its
    mutating statements are refused. Reads pass (the next test)."""
    proj = sdk_with_journal._get_proj()
    with pytest.raises(DirectCacheWriteRefused):
        proj.query("CREATE (n:Point {id:'raw-7951'})")
    assert _count(proj, "Point", "id", "raw-7951") == 0


def test_reads_on_the_raw_query_lane_still_pass(sdk_with_journal):
    """A READ is not a write: the guard is not a read-blocker, and a classifier
    that refused reads would break every diagnostic and every test."""
    proj = sdk_with_journal._get_proj()
    assert proj.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 0


# ── the sanctioned paths still work ───────────────────────────────────────

def test_the_chokepoint_folds_and_journals_in_order(
        sdk_with_journal, monkeypatch):
    """The documented path still succeeds, and it is journal-first.

    ORDER is observed at the fold: when `proj.apply` is entered, the journal
    must ALREADY hold the `SubjectAdded` record. Apply-first leaves the derived
    store ahead of the truth for the whole fold — the window no replay can
    undo (the ordering contract at the top of `tortoise/sdk.py`)."""
    sdk = sdk_with_journal
    proj = sdk._get_proj()
    journal = sdk._journal_path_for_test
    original = proj.apply
    seen_at_fold: list[list[str]] = []

    def spy(event, **kwargs):
        seen_at_fold.append(_journal_types(journal))
        return original(event, **kwargs)

    monkeypatch.setattr(proj, "apply", spy)

    sdk._apply_and_journal(proj, _subject(proj, "subj-ok"))

    assert seen_at_fold, "the fold never ran — the write is broken, not guarded"
    assert "SubjectAdded" in seen_at_fold[0], (
        "the fold ran BEFORE the journal held the record — the append is the "
        f"commit point; observed journal at fold time: {seen_at_fold[0]!r}"
    )
    assert _count(proj, "Subject", "id", "subj-ok") == 1


def test_a_replay_scope_allows_the_fold(sdk_with_journal):
    """A replay engine's record CAME FROM the journal — folding it is not a
    second write, so the replay scope authorizes it (and it must not journal
    again)."""
    proj = sdk_with_journal._get_proj()
    with _replay_write_scope():
        proj.apply(_subject(proj, "subj-replay"))
    assert _count(proj, "Subject", "id", "subj-replay") == 1


def test_the_rebuild_engine_still_replays_a_journal_backed_projection(
        sdk_with_journal):
    """The `rebuild` engine drives `apply` once per journal record; its own
    replay scope must cover that, or recovery would be self-blocked by the
    guard — the worst possible failure mode for a durability gate."""
    from tortoise.log import EventLog

    sdk = sdk_with_journal
    proj = sdk._get_proj()
    sdk._apply_and_journal(proj, _subject(proj, "subj-rb"))

    proj.rebuild(EventLog(str(sdk._journal_path_for_test)),
                 confirm_destructive=True)

    assert _count(proj, "Subject", "id", "subj-rb") == 1, (
        "the replay lost the record — the guard refused a fold it must allow"
    )


def test_the_event_api_door_still_folds(sdk_with_journal):
    """`EventAPI._emit` is the other journal-first live door: it appends, then
    folds inside the live scope."""
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    sdk = sdk_with_journal
    proj = sdk._get_proj()
    api = EventAPI(EventLog(str(sdk._journal_path_for_test)),
                   initiated_by="extractor", agent_id="test", projection=proj)

    api._emit("SubjectAdded", id="subj-emit", name="subj-emit")

    assert _count(proj, "Subject", "id", "subj-emit") == 1


def test_the_sdk_public_write_api_is_unaffected(sdk_with_journal):
    """The guard must not break the product's own live write path: the SDK's
    public writers issue raw Cypher through the guarded HANDLE, which this lane
    deliberately does not close (recorded on #7951)."""
    sdk = sdk_with_journal
    point = sdk.create_point("statement", "a claim", source="s1")
    assert point.get("id"), f"create_point returned no id: {point!r}"


# ── the boundary and the isolation property ───────────────────────────────

def test_the_guard_is_armed_by_the_journal_binding(sdk_with_journal):
    """The guard's reach is the JOURNAL-BACKED projection, and this pins both
    sides of that boundary.

    A projection with no journal configured is outside §17.1 (there is no
    record for its cache to be derived from) — that is the recorded residual
    the connectors and `#4240`'s journal-less hosted path live in, and pinning
    it here keeps it a stated boundary rather than a silent hole."""
    sdk = sdk_with_journal
    proj = sdk._get_proj()

    with pytest.raises(DirectCacheWriteRefused):
        proj.apply(_subject(proj, "subj-unbound"))

    proj._bind_journal(None)  # a pre-guard projection: writes, no journal
    try:
        proj.apply(_subject(proj, "subj-unbound"))
    finally:
        proj._bind_journal(sdk._event_log_path)

    assert _count(proj, "Subject", "id", "subj-unbound") == 1


def test_the_authorization_is_context_local(sdk_with_journal):
    """The scope is a ContextVar, so a concurrent thread writing the same
    projection does NOT inherit another thread's authorization — it sees the
    refusing default (the same isolation property the #7951 replay anchors
    were moved to in round 3). MUTATION: make `_CACHE_WRITE_AUTHORIZED` an
    instance attribute and the worker's fold is authorized → this test fails.
    """
    proj = sdk_with_journal._get_proj()
    refused: list[BaseException] = []

    def worker() -> None:
        try:
            proj.apply(_subject(proj, "subj-thread"))
        except BaseException as exc:  # recorded and asserted below
            refused.append(exc)

    with _live_write_scope("test"):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        # The MAIN context IS authorized, so the scope is genuinely armed.
        proj.apply(_subject(proj, "subj-main"))

    assert refused and isinstance(refused[0], DirectCacheWriteRefused), (
        "a thread inherited the caller's authorization — the scope is not "
        f"context-local: {refused!r}"
    )
    assert _count(proj, "Subject", "id", "subj-main") == 1
    assert _count(proj, "Subject", "id", "subj-thread") == 0


# ── the P0: the MCP surface must ARM the guard ───────────────────────────

def test_the_mcp_lane_is_armed(tmp_path, monkeypatch):
    """P0 — the surface agents actually write through must not bypass §17.1.

    Before this fix, `mcp_auth._get_org_sdk` built `TortoiseSDK(graph_name=ns)`
    / `TortoiseSDK(namespace=org)` with NO `event_log_path`, so `_get_proj`'s
    `_bind_journal` bound `None` and the guard was INERT on the hosted MCP
    lane: a direct `proj.apply` silently wrote the derived cache (reproduced
    live before the fix — `journal_backed=False`, NO RAISE). The journal is now
    resolved through the SAME `hosted_api._resolve_event_log_path` the REST
    lane uses, so the two lanes cannot disagree.

    Covers BOTH branches of `_get_org_sdk`: an org-wide key (namespace) and a
    graph-bound key (explicit `graph_name`). Also proves the arming did not
    break the lane's write path (the guarded handle still writes; the
    sanctioned fold door still folds).

    MUTATION: drop `event_log_path=` from either `TortoiseSDK(...)` in
    `mcp_auth._get_org_sdk` and the matching assertion fails.
    """
    from tortoise import mcp_auth

    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))

    def _armed(sdk):
        proj = sdk._get_proj()
        assert proj._journal_backed is True, (
            "the MCP lane built a journal-LESS SDK — §17.1's guard is inert "
            "on the surface agents write through (the P0): "
            f"event_log_path={sdk._event_log_path!r}"
        )
        with pytest.raises(DirectCacheWriteRefused) as exc:
            proj.apply(_subject(proj, "subj-mcp-direct"))
        assert "17.1" in str(exc.value)
        return proj

    # (a) hosted HTTP mode, ORG-WIDE key → the namespace branch.
    tok = mcp_auth._current_org_id.set("org7951")
    try:
        sdk = mcp_auth._get_org_sdk()
        assert sdk._event_log_path == str(
            tmp_path / "org_org7951" / "events.jsonl"), (
            f"the MCP lane resolved a different journal: {sdk._event_log_path!r}"
        )
        proj = _armed(sdk)
        # The arming must not have broken the lane's own writes: the SDK's
        # live handle write still lands, and the chokepoint still folds.
        assert sdk.create_point("statement", "mcp write").get("id")
        sdk._apply_and_journal(proj, _subject(proj, "subj-mcp-ok"))
        assert _count(proj, "Subject", "id", "subj-mcp-ok") == 1
    finally:
        mcp_auth._current_org_id.reset(tok)

    # (b) hosted HTTP mode, GRAPH-BOUND key → the explicit graph_name branch.
    toks = [
        mcp_auth._current_org_id.set("org7951"),
        mcp_auth._current_graph_id.set("g7951"),
        mcp_auth._current_graph_namespace.set("org_t7951_g7951"),
    ]
    try:
        sdk_g = mcp_auth._get_org_sdk()
        assert sdk_g._event_log_path == str(
            tmp_path / "org_t7951_g7951" / "events.jsonl"), (
            f"the graph-bound MCP lane resolved a different journal: "
            f"{sdk_g._event_log_path!r}"
        )
        _armed(sdk_g)
    finally:
        # LIFO: reset in the reverse of the set order above.
        mcp_auth._current_graph_namespace.reset(toks[2])
        mcp_auth._current_graph_id.reset(toks[1])
        mcp_auth._current_org_id.reset(toks[0])


# ── §17.1's ordering, seen from S9 (the engine stopped) ───────────────────

def test_a_live_write_whose_fold_fails_is_already_in_the_journal(
        sdk_with_journal, monkeypatch):
    """S9 in miniature: if the fold cannot complete, the record is not lost.

    The append is the COMMIT POINT and it precedes the fold, so a fold that
    fails (a stopped/deleted graph is the production case) leaves the record in
    the TRUTH journal and the derived store merely LAGGING — healable by the
    next replay. The reverse order would die with the derived store AHEAD of
    truth, which no replay can undo. This is the whole reason §17.1 fixes an
    ORDER while the atomic write is still to come."""
    sdk = sdk_with_journal
    proj = sdk._get_proj()
    journal = sdk._journal_path_for_test

    def boom(*_a, **_k):
        raise RuntimeError("FalkorDB is down")

    monkeypatch.setattr(proj, "apply", boom)

    with pytest.raises(RuntimeError):
        sdk._apply_and_journal(proj, _subject(proj, "subj-s9"))

    assert "SubjectAdded" in _journal_types(journal), (
        "the journal does NOT hold the record although the fold failed — the "
        "append did not precede the fold, so this write is unrecoverable"
    )


def test_create_point_is_apply_first_a_recorded_deviation(
        sdk_with_journal, monkeypatch):
    """§17.1's ORDER is NOT yet journal-first on `create_point` — PINNED, not
    claimed away.

    `create_point` writes the Point through the guarded HANDLE
    (`proj.g.query("CREATE (n:Point {…})")`) and appends its `PointAdded`
    record only afterwards, from `get_point(pid)` — so the DERIVED store is
    briefly AHEAD of the TRUTH (window (a) of the ordering contract at the top
    of `tortoise/sdk.py`). It is that contract's recorded exception 2: the
    record IS the applied node (`RETURN properties(n)`), so there is no
    pre-fold payload to append, and the canonical id can be an apply result. A
    crash in the gap leaves a node no replay reproduces.

    The order CANNOT be corrected in this increment without changing the
    journal payload's shape on the highest-traffic write path (the pre-fold
    values differ from `properties(n)` — e.g. a server-computed embedding is
    stored `vecf32`-narrowed, #5004), which is exactly the `derived =
    replay(journal)` break this lane exists to prevent. Closing it needs
    §17.1's mechanism (1), the atomic write, still OPEN on #7951.

    This test pins the ACTUAL order so the residual is checkable and no later
    lane can claim `create_point` is journal-first (the review's P1).

    ⛔ WHEN THE ATOMIC WRITE LANDS: flip the assertion to
    `order.index("journal-PointAdded") < order.index("cache-create")` and
    delete exception 2 from the ordering contract.
    """
    sdk = sdk_with_journal
    proj = sdk._get_proj()
    # How many Point rows the CACHE holds at the instant `PointAdded` is
    # journalled. `1` = the node was already written (apply-first); `0` = the
    # record went first (journal-first, what §17.1 requires).
    cache_at_journal: list[int] = []

    real_emit = sdk._emit_event

    def spy_emit(type_, *args, **kwargs):
        if type_ == "PointAdded" and args:
            cache_at_journal.append(
                _count(proj, "Point", "id", args[0]["id"]))
        return real_emit(type_, *args, **kwargs)

    monkeypatch.setattr(sdk, "_emit_event", spy_emit)

    point = sdk.create_point("statement", "an apply-first claim")
    assert point.get("id"), f"create_point returned no id: {point!r}"

    assert cache_at_journal == [1], (
        "create_point's order CHANGED — if the journal record now precedes the "
        "cache write (observed count 0), delete this deviation pin and remove "
        "exception 2 from the ordering contract in `tortoise/sdk.py` "
        f"(observed: {cache_at_journal!r})"
    )
    assert _count(proj, "Point", "id", point["id"]) == 1


# ── P0 (review round 4): the onboarding FLOW writers must not be refused ──

def test_onboarding_flow_writes_are_not_refused_on_a_journal_backed_projection(
        sdk_with_journal):
    """P0 — the armed guard must not break the onboarding FLOW writers.

    `tortoise/onboarding/state.py::_run` used to dispatch through the PUBLIC
    raw-query lane (`FalkorProjection.query`). §17.1 closes that lane on a
    journal-backed projection, and the hosted lane (`TORTOISE_EVENT_LOG_BASE_DIR`
    → `hosted_api._org_proj()` → a journal-bound SDK) is exactly that — so
    every onboarding writer (`ensure_onboarding_state_node`, `write_status`,
    `write_completed_step`, `write_member_progress`, `write_fork`, …) raised
    `DirectCacheWriteRefused` in production, and the callers swallow it
    (fail-open) so orgs silently never reached `complete`.

    Onboarding FLOW state is OPERATIONAL state, not a projection of the event
    journal, so the fix routes `_run` through the projection's own graph handle
    (`proj.g.query`) — the lane the SDK's live writes use, and the residual
    §17.1 records as still-open. This test drives every writer the reported
    path uses and asserts they all land.

    RED before the fix: the first write raises `DirectCacheWriteRefused`.
    """
    from tortoise.onboarding import state as onboarding_state

    sdk = sdk_with_journal
    proj = sdk._get_proj()
    org = "org-7951-onboarding"
    assert proj._journal_backed, (
        "the fixture must ARM the guard, or this regression pin is vacuous"
    )

    onboarding_state.ensure_onboarding_state_node(proj, org)
    onboarding_state.write_completed_step(proj, org, "harness-connected")
    onboarding_state.write_member_progress(
        proj, org, "user-1", ["harness-connected"])
    onboarding_state.write_fork(proj, org, "self")
    onboarding_state.write_status(proj, org, onboarding_state.STATUS_ACTIVE)

    node = onboarding_state.read_onboarding_node(proj, org)
    assert node is not None, "the FLOW node was never created"
    assert node.get("fork") == "self"
    assert "harness-connected" in onboarding_state.completed_steps(proj, org)


def test_the_selfhost_lane_is_armed_and_its_onboarding_writes_succeed(
        tmp_path, monkeypatch):
    """P2 — the self-host MCP lane IS armed, and must keep onboarding working.

    `mcp_auth._get_org_sdk` arms the guard for `SELFHOST_ORG_ID`
    (`namespace='selfhost'`), so the earlier boundary claim that this lane
    "is NOT armed, and could not be" was wrong. This pins the corrected
    boundary AND the P0 fix on it: the ARMING must not stop
    `ensure_selfhost_onboarding` from creating the node.
    """
    from tortoise import mcp_auth
    from tortoise.onboarding import selfhost as _selfhost
    from tortoise.onboarding import state as onboarding_state

    monkeypatch.setenv("TORTOISE_EVENT_LOG_BASE_DIR", str(tmp_path))
    # Force the once-per-process memo to run for THIS test.
    monkeypatch.setattr(_selfhost, "_INITIALIZED", set())
    monkeypatch.setattr(_selfhost, "_INIT_FAILURE_WARNED", set())

    tok = mcp_auth._current_org_id.set(mcp_auth.SELFHOST_ORG_ID)
    try:
        sdk = mcp_auth._get_org_sdk()
        proj = sdk._get_proj()
        assert proj._journal_backed is True, (
            "the self-host lane is not armed — the corrected boundary says it is"
        )
        assert sdk._event_log_path == str(
            tmp_path / "org_selfhost" / "events.jsonl")
        # `_get_org_sdk` runs the selfhost init seam; pre-fix its `_run` was
        # refused and the fail-open wrapper hid it, leaving no node.
        node = onboarding_state.read_onboarding_node(
            proj, mcp_auth.SELFHOST_ORG_ID)
        assert node is not None, (
            "the selfhost onboarding init was silently refused on the armed lane"
        )
        assert node.get("fork") == "self", (
            "the selfhost init wrote the node but not its pinned fork value"
        )
    finally:
        mcp_auth._current_org_id.reset(tok)


# ── P2 (review round 4): the stdio fast path must not build hosted_api ────

def test_the_stdio_fast_path_never_resolves_a_journal(tmp_path, monkeypatch):
    """P2 — `_get_sdk` must not call the hosted-api journal resolver.

    On the base/stdio lane the journal key is `namespace=None, graph_name=None`
    by construction, and the ONE derivation returns ``None`` for it — so
    resolving it bought nothing while importing `hosted_api` (and the whole
    FastAPI app it builds) on the tool fast path, which the module's own
    invariant forbids (#3834). The resolver is patched to FAIL if called, so
    this is RED on the pre-fix code and GREEN after.
    """
    from tortoise import mcp_auth, mcp_server

    monkeypatch.setattr(mcp_server, "_sdk", None)
    monkeypatch.setenv("TORTOISE_DB_URI", str(tmp_path / "fastpath.db"))
    called: list[tuple] = []

    def _fail_if_called(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError(
            "the stdio fast path resolved a journal — it must not import "
            "hosted_api for a path that is always None (#7951 P2)"
        )

    monkeypatch.setattr(mcp_auth, "_journal_path_for", _fail_if_called)

    sdk = mcp_server._get_sdk()
    assert not called, "_journal_path_for was called on the journal-less lane"
    assert sdk._event_log_path is None
    sdk.close()
