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
import os
import subprocess
import sys
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


# ── the §17.1 gate must not SILENTLY break the onboarding writers (P0) ────

def test_onboarding_writes_are_not_refused_on_a_journal_backed_lane(
        sdk_with_journal, monkeypatch):
    """P0 regression: the raw-query gate silently killed the hosted
    onboarding writers, so this pins that they still write.

    `hosted_api._org_proj(org_id)` returns a JOURNAL-BACKED projection on the
    hosted lane (`fly.toml` sets `TORTOISE_EVENT_LOG_BASE_DIR=/data/events`),
    and every onboarding writer reached the graph through
    `onboarding.state._run`, which tried the PUBLIC `FalkorProjection.query`
    lane FIRST. Once the gate was armed that lane refused every `MERGE`/`SET`/
    `REMOVE` with `DirectCacheWriteRefused`, and the callers are FAIL-OPEN
    (`hosted_api._maybe_apply_completion` -> `except Exception: return False`,
    `mcp_server._maybe_onboarding_auto_complete` -> `return`,
    `supabase_control` logs "self-heals on first write" where the self-heal IS
    the refused write), so orgs silently never reached `complete`, the fork
    write and the completion gate stopped, and no counter fired.

    The writers take the SAME raw-handle lane the SDK's own live writes use
    (`proj.g.query`). ⚠️ That lane was #7951's still-open residual; it is no
    longer open-ended — `#9121` DECLARED this writer family (`onboarding.
    metadata`, see `ONBOARDING_WRITE_LANE` in `onboarding/state.py`) ahead of
    `#9079` closing the handle, so these writes stay sanctioned once the third
    door is armed. The OTHER guards on that path are unchanged: the handle's
    #3595 operator and #7174 numeric-domain refusals and its L2 bulk-wipe
    check all still run; the armed-handle version of this pin lives in
    `tests/test_9079_onboarding_declared_lane.py`.

    RED before the fix: the first `write_completed_step` raises
    `DirectCacheWriteRefused`. MUTATION: revert `onboarding.state._run` to
    `graph.query(...)` and this fails again.
    """
    from tortoise import hosted_api
    from tortoise.onboarding import seed as _seed
    from tortoise.onboarding import state as _os

    monkeypatch.setattr(hosted_api, "_make_sdk",
                        lambda **_kw: sdk_with_journal)
    proj = hosted_api._org_proj("org7951")
    assert proj._journal_backed is True, (
        "the fixture did not arm §17.1 — the gate is inert and this pin would "
        "be vacuous"
    )

    # The write set the hosted lane actually issues (state.py writers), in
    # the order the endpoints issue them.
    _os.ensure_onboarding_state_node(proj, "org7951")
    _os.write_fork(proj, "org7951", _os.FORK_SELF)
    _os.write_completed_step(proj, "org7951", "team-named")
    _os.write_member_progress(proj, "org7951", "user-1", ["team-named"])
    _os.write_status(proj, "org7951", _os.STATUS_COMPLETE)

    assert _count(proj, "OnboardingState", "org_id", "org7951") == 1
    node = _os.read_onboarding_node(proj, "org7951")
    assert node is not None and node["status"] == _os.STATUS_COMPLETE
    assert "team-named" in _os.completed_steps(proj, "org7951")
    # The seed module's duck-typed primitive takes the same raw-handle lane.
    assert _seed.find_subject_by_name(proj, "nobody-seeded-yet") is None


def test_the_selfhost_onboarding_lane_writes_on_a_journal_backed_proj(
        sdk_with_journal):
    """The selfhost-HTTP lane is ARMED by this PR (`mcp_auth._get_org_sdk`
    now binds the journal), so its one owned write point must keep working.

    `selfhost.ensure_selfhost_onboarding(sdk)` reaches
    `state.ensure_onboarding_state_node` on the same journal-backed
    projection, from `ensure_selfhost_onboarding_once` — best-effort and
    never-raise, so a refusal here is exactly as silent as the hosted one.

    RED before the fix: the create-on-write MERGE raises
    `DirectCacheWriteRefused`.

    ⚠️ This pin dispatches through the RAW HANDLE (`proj.g`, the THIRD door), not
    the public `FalkorProjection.query` lane: `state._run` prefers `graph.g` when
    the graph exposes one. Nothing here guards that door, so arming it (`#9124`)
    changes nothing this pin can observe — the write keeps landing because `_run`
    declares it (`#9121`). What it CANNOT tell you is whether the declaration is
    doing its job, because it never arms the handle; that is the job of
    `tests/test_9079_onboarding_declared_lane.py
    ::test_the_selfhost_onboarding_lane_reaches_the_graph_with_the_guard_armed`,
    which issues the same call with the `#9124` predicate installed. Nothing in
    this file pins the public raw-query lane for onboarding, because `state._run`
    no longer routes through it.
    """
    from tortoise.onboarding import selfhost
    from tortoise.onboarding import state as _os

    sdk = sdk_with_journal
    proj = sdk._get_proj()
    assert proj._journal_backed is True

    selfhost.ensure_selfhost_onboarding(sdk)

    org_id = selfhost.selfhost_org_id()
    node = _os.read_onboarding_node(proj, org_id)
    assert node is not None, (
        "the selfhost onboarding node was not written — its one owned write "
        "point was refused (the P0)"
    )
    assert node.get("fork") == _os.FORK_SELF


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


def test_create_point_journals_the_record_before_the_cache_write(
        sdk_with_journal, monkeypatch):
    """#7951 §17.1 — `create_point` is now JOURNAL-first (the outbox ordering).

    `create_point` builds its `PointAdded` payload from the WRITE INTENT
    (`_create_map`, the properties the `CREATE` will write) and appends the
    record BEFORE the cache write, so the DERIVED store can never lead the
    TRUTH. Before this lane the record was the post-`CREATE` `get_point`
    read-back and the cache write came first (window (a) of the ordering
    contract at the top of `tortoise/sdk.py`, the recorded apply-first
    deviation).

    This is the flipped pin: the same spy that used to assert `[1]` (cache
    already held the node when the record was journalled) now asserts `[0]`.

    MUTATION: move the `_emit_event("PointAdded", …)` back below the `CREATE`
    (or delete the append) and this fails with `[1]` (or `[]`).
    """
    sdk = sdk_with_journal
    proj = sdk._get_proj()
    # How many Point rows the CACHE holds at the instant `PointAdded` is
    # journalled. `0` = the record went first (journal-first, §17.1); `1` =
    # the node was already written (the old apply-first deviation).
    cache_at_journal: list[int] = []

    real_emit = sdk._emit_event

    def spy_emit(type_, *args, **kwargs):
        if type_ == "PointAdded" and args:
            cache_at_journal.append(
                _count(proj, "Point", "id", args[0]["id"]))
        return real_emit(type_, *args, **kwargs)

    monkeypatch.setattr(sdk, "_emit_event", spy_emit)

    point = sdk.create_point("statement", "a journal-first claim")
    assert point.get("id"), f"create_point returned no id: {point!r}"

    assert cache_at_journal == [0], (
        "create_point must append its record BEFORE the cache write (§17.1): "
        "the observation must be [0] (the record went first). A [1] means the "
        "order regressed to apply-first; an [] means the record was never "
        f"appended (observed: {cache_at_journal!r})"
    )
    assert _count(proj, "Point", "id", point["id"]) == 1


# ── the P1: the MCP lane must NOT drag the hosted app onto stdio/self-host ─
#
# `tortoise.hosted_api`'s PRESENCE in `sys.modules` IS the runtime self-host
# deployment discriminator: `mcp_server.tortoise_pack_install`'s HOSTED-only
# stub, `operator_alert.alert_store`'s light leg, and the fast-path invariant
# stated at the top of `mcp_server` all read it. Resolving the MCP lane's
# journal through a function-level `from tortoise import hosted_api` therefore
# put the whole hosted FastAPI app in `sys.modules` on EVERY stdio/self-host
# process — at startup, via `monitoring.register(_get_sdk())` — for a call that
# provably returns `None` on that lane (`namespace=None, graph_name=None`).
# The derivation now lives in the lightweight `tortoise.config`.

_ROOT = Path(__file__).resolve().parents[1]


def _fresh_interpreter(code: str, tmp_path) -> str:
    """Run ``code`` in a FRESH interpreter on the stdio/self-host MCP lane.

    A subprocess is REQUIRED to test the invariant: the whole suite shares one
    interpreter, so an in-process ``sys.modules`` check cannot fail once any
    test (or another module) has imported ``hosted_api``. The lane's own
    invariant is about a process that has NOT imported it. This is also how
    the review measured the regression.
    """
    env = dict(os.environ)
    env.pop("TORTOISE_DB_URI", None)
    env.pop("TORTOISE_EVENT_LOG_BASE_DIR", None)
    env["TORTOISE_DB_PATH"] = str(tmp_path / "stdio-lane.db")
    proc = subprocess.run(
        [sys.executable, "-c", "import os, sys\n" + code],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=600, env=env,
    )
    assert proc.returncode == 0, (
        f"the fresh-interpreter probe FAILED (rc={proc.returncode})\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return proc.stdout


def test_the_stdio_mcp_lane_never_imports_the_hosted_app(tmp_path):
    """P1 — `mcp_server._get_sdk` must not build the hosted app (#7951).

    RED before the fix (measured): after `_get_sdk()`,
    ``'tortoise.hosted_api' in sys.modules`` is ``True``, so this assertion
    fails. The journal is `None` on this lane either way, so the stubbed call
    bought nothing and cost the self-host discriminator.
    """
    code = (
        "assert 'tortoise.hosted_api' not in sys.modules, 'preloaded'\n"
        "import tortoise.mcp_server as m\n"
        "assert 'tortoise.hosted_api' not in sys.modules, (\n"
        "    'importing tortoise.mcp_server already built the hosted app')\n"
        "sdk = m._get_sdk()\n"
        "assert 'tortoise.hosted_api' not in sys.modules, (\n"
        "    '_get_sdk() imported tortoise.hosted_api — the #3834 fast-path "
        "invariant is broken')\n"
        "assert sdk._event_log_path is None, sdk._event_log_path\n"
        "from tortoise import operator_alert as oa\n"
        "oa.alert_store()\n"
        "assert 'tortoise.hosted_api' not in sys.modules, (\n"
        "    'alert_store() took the hosted leg on the stdio lane')\n"
        "print('STDIO-LANE-CLEAN')\n"
    )
    assert "STDIO-LANE-CLEAN" in _fresh_interpreter(code, tmp_path)


def test_the_selfhost_pack_install_stub_survives_stdio_startup(tmp_path):
    """P1, second harm — the self-host deployment gate stays reachable.

    ``tortoise_pack_install``'s HOSTED-only stub fires only while
    ``'tortoise.hosted_api'`` is ABSENT from ``sys.modules``. Once startup
    imported it, a stdio/self-host call fell past the gate to the
    transport check. RED before the fix (measured): the error is
    ``'pack install requires the HTTP transport.'``, not the HOSTED-only stub.
    """
    code = (
        "import tortoise.mcp_server as m\n"
        "m._get_sdk()\n"
        "out = m.tortoise_pack_install('')\n"
        "assert out.get('installed') is False, out\n"
        "assert 'HOSTED-only' in (out.get('error') or ''), out\n"
        "print('PACK-STUB-OK')\n"
    )
    assert "PACK-STUB-OK" in _fresh_interpreter(code, tmp_path)


def test_the_mcp_org_journal_resolves_without_the_hosted_app(tmp_path):
    """P1 — the org lane still gets its journal, WITHOUT importing the app.

    The arming itself (the P0) must be preserved: `mcp_auth._get_org_sdk` still
    passes this resolved path as `event_log_path=`. Only the import is gone —
    the derivation now comes from `tortoise.config` (and `hosted_api`
    re-exports the same function, so there is still exactly ONE derivation).

    RED before the fix (measured): `_journal_path_for` loaded
    ``tortoise.hosted_api`` at call time.
    """
    base = tmp_path / "ev"
    expected = base / "org_org7951" / "events.jsonl"
    code = (
        f"os.environ['TORTOISE_EVENT_LOG_BASE_DIR'] = {str(base)!r}\n"
        "from tortoise import mcp_auth\n"
        "p = mcp_auth._journal_path_for(namespace='org7951', graph_name=None)\n"
        f"assert p == {str(expected)!r}, p\n"
        "assert 'tortoise.hosted_api' not in sys.modules, (\n"
        "    '_journal_path_for imported the hosted app')\n"
        "print('JOURNAL-OK')\n"
    )
    assert "JOURNAL-OK" in _fresh_interpreter(code, tmp_path)
