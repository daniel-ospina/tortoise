"""Tests for the degraded-fallback corpus snapshot (#1375).

Covers: cached-vs-legacy result parity (the #399 sklearn contract), write
invalidation (create/operator/delete via the _mark_dirty hook), the lazy TTL
backstop, the size cap, and the include_terminal/kind/exclude_status
semantics.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile  # noqa: F401
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tortoise.sdk import TortoiseSDK
from tortoise import fallback_snapshot as fs

TEST_GRAPH = "tortoise_test_1375_fallback_snapshot"

# FTS-miss queries: a token NOT present in any test corpus — retrieval
# strategies miss and the degraded TF-IDF fallback fires deterministically.
# NOTE (#1625): must NOT contain 'zzz' — several tests seed points whose
# content includes 'zzz...' tokens, which FTS matches (the R2 multi-field
# index matches content tokens; a 'zzz'-containing miss query would no
# longer be a miss and the snapshot would never build.
FTS_MISS = "qqqzxwv nonexistent"


@pytest.fixture

def sdk(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "fb.db"), namespace=TEST_GRAPH)
    fs._store.clear()
    yield sdk
    fs._store.clear()
    try:
        sdk.test_guard()
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """#1625: pin the sparse leg — the R3 dense leg (vector nearest-neighbor)
    returns results for EVERY query (k-NN semantics, no threshold), so a
    "FTS-miss" query like 'zzz' would still hit via vector and the degraded
    TF-IDF fallback snapshot would never build. With EmbeddingModel.get →
    None the vector strategy is never submitted (sdk.py records
    no_embedder) and the fallback fires deterministically — the original
    #1375 contract. build_snapshot itself degrades to sklearn TF-IDF when
    no embedder, so the snapshot still builds."""
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get",
                        staticmethod(lambda load_timeout=None: None))


def _no_match_query(sdk, q=FTS_MISS):
    return sdk.tortoise_fts_query(query=q, limit=10)


def _cached_results(sdk, q=FTS_MISS):
    return [r["id"] for r in _no_match_query(sdk, q)]


def _store_has(sdk) -> bool:
    proj = sdk._get_proj()
    return fs._store.get(fs.snapshot_key(proj, sdk._namespace)) is not None


# ── Parity ─────────────────────────────────────────────────────────────

def test_fallback_snapshot_parity_with_legacy(sdk, monkeypatch):
    """Cached snapshot results == legacy path results (identical ids+order)."""
    for i in range(6):
        sdk.create_point("statement", f"pricing tier {i} enterprise plans memory")
    # #1625: the query must MISS FTS (a 'zzz pricing memory' query now matches
    # the corpus directly via the R2 multi-field index, so the fallback never
    # fires and the snapshot never builds). FTS_MISS has no token in any
    # corpus — both the legacy TF-IDF path and the cached snapshot path then
    # run the same degraded scorer on the full corpus.
    q = FTS_MISS

    # 1. legacy (snapshot capped off) — restore the cap BEFORE the cached leg
    monkeypatch.setattr(fs, "MAX_CORPUS_POINTS", 0)
    legacy = [r["id"] for r in sdk.tortoise_fts_query(query=q, limit=10)]
    assert not _store_has(sdk), "legacy leg must not build the snapshot"

    monkeypatch.setattr(fs, "MAX_CORPUS_POINTS", 50_000)
    fs._store.clear()
    cached = _cached_results(sdk, q=q)
    assert _store_has(sdk), "cached leg must build the snapshot"
    assert cached == legacy, f"parity broken: cached={cached} legacy={legacy}"

    # 3. cached again (from store — same result)
    again = _cached_results(sdk, q=q)
    assert again == cached, "snapshot path must be deterministic"


# ── Status/kind semantics ───────────────────────────────────────────────

def test_fallback_snapshot_status_and_kind_semantics(sdk):
    """include_terminal + exclude_status + kind mirror the legacy semantics."""
    a = sdk.create_point("statement", "onboarding zero cost strategy")
    b = sdk.create_point("statement", "onboarding zero cost strategy")
    c = sdk.create_point("decision", "onboarding zero cost strategy")  # other kind
    sdk.supersede_point(b["id"], a["id"])  # b → superseded (terminal)

    _no_match_query(sdk)  # build the snapshot
    proj = sdk._get_proj()
    snap = fs._store.get(fs.snapshot_key(proj, sdk._namespace))
    assert snap is not None, "snapshot must be built"

    # kind filter: only 'statement' points
    ids_kind = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, kind="statement")]
    assert c["id"] not in ids_kind and a["id"] in ids_kind

    # include_terminal=False (default): superseded b excluded
    ids_default = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, kind="statement")]
    assert b["id"] not in ids_default and a["id"] in ids_default

    # include_terminal=True: b back
    ids_terminal = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, kind="statement", include_terminal=True)]
    assert b["id"] in ids_terminal

    # exclude_status composes on top
    ids_ex = [r["id"] for r in fs.search_snapshot(
        "onboarding", snap, limit=10, kind="statement",
        exclude_status=["superseded"])]
    assert b["id"] not in ids_ex and a["id"] in ids_ex


def test_fallback_snapshot_turn_echo_exclusion_is_pre_ranking(sdk):
    """#4509: the degraded fallback honours the same OPT-IN turn-echo exclusion
    as the primary path — the capture's own turn echoes are dropped from the
    CORPUS before ranking (so they cannot consume the window), and naming
    another session is a no-op.

    FALSIFIER — (1) *what value makes this test fail?* Whether ``fb_t0`` is in
    the excluded result and whether ``fb_real`` survives. (2) *reachable?* Yes:
    the fallback returns the whole corpus on a non-matching query, so all four
    rows are in the window; only the corpus filter can remove the echoes."""
    for i in range(3):
        sdk.create_point("event", f"[user] unrelated filler {i}",
                         id=f"fb_t{i}", is_episodic=True)
    sdk.create_point("statement", "unrelated filler prior", id="fb_real")

    default = [r["id"] for r in _no_match_query(sdk)]
    assert {"fb_t0", "fb_t1", "fb_t2", "fb_real"} <= set(default), default

    other = [r["id"] for r in sdk.tortoise_fts_query(
        query=FTS_MISS, limit=10, exclude_turn_echo_session="other")]
    assert other == default, (default, other)

    opted = [r["id"] for r in sdk.tortoise_fts_query(
        query=FTS_MISS, limit=10, exclude_turn_echo_session="fb")]
    assert "fb_real" in opted, opted
    assert not any(i.startswith("fb_t") for i in opted), opted


def test_fallback_snapshot_supersede_decoration(sdk):
    """E5 Task 5 (#1537): the embedded TF-IDF fallback decorates its hits
    with the promoted epistemic state (fetch_point_epistemic_state — the D8
    machinery the full path uses) so [SUPERSEDED BY] renders in embedded
    mode. A superseded hit (include_terminal=True) carries status + the
    superseding claim; undecorated hits stay byte-identical."""
    a = sdk.create_point("statement", "espresso preferred in the morning",  # noqa: F841
                         id="ku_old", status="live")
    b = sdk.create_point("statement", "drip coffee preferred now",  # noqa: F841
                         id="ku_new", status="live")
    sdk.supersede_point("ku_old", "ku_new")

    hits = sdk.tortoise_fts_query("espresso preferred morning drip coffee",
                                  entity_type="point",
                                  include_terminal=True, limit=10)
    old = next((h for h in hits if h["id"] == "ku_old"), None)
    assert old is not None, "superseded point must co-retrieve"
    assert old["status"] == "superseded"
    assert old["superseded_by"]["id"] == "ku_new"
    assert old.get("supersedes", []) == []  # no outgoing CORRECTS (omit-empty)
    new = next((h for h in hits if h["id"] == "ku_new"), None)
    assert new is not None and new["supersedes"][0]["id"] == "ku_old"


def test_fallback_snapshot_no_corrects_renders_byte_identical(sdk):
    """E5 Task 5 negative: a graph with no CORRECTS edges renders
    byte-identically to today — status/superseded_by/supersedes keys are
    absent (the to_dict convention), markers stay empty."""
    sdk.create_point("statement", "plain espresso preference",
                     id="plain", status="live")
    hits = sdk.tortoise_fts_query("espresso preference",
                                  entity_type="point", limit=10)
    assert hits, "the plain point must surface"
    h = next(x for x in hits if x["id"] == "plain")
    assert "superseded_by" not in h
    assert "supersedes" not in h


def test_fallback_snapshot_invalidated_point_absent():
    """outdated=true points (invalidate_point) are excluded at build."""
    # covered structurally: _SNAPSHOT_QUERY has coalesce(n.outdated,false)=false
    assert "outdated" in fs._SNAPSHOT_QUERY


# ── Invalidation ────────────────────────────────────────────────────────

def test_fallback_snapshot_invalidated_on_write(sdk):
    """A new point invalidates the snapshot (store dropped) and reappears."""
    sdk.create_point("statement", "alpha beta gamma delta")
    _no_match_query(sdk)  # build
    assert _store_has(sdk)
    sdk.create_point("statement", "alpha beta gamma delta zzz-new")
    assert not _store_has(sdk), "create must invalidate the snapshot"
    # #1625: query the NEW point's unique token ('zzz-new' is in the new
    # point only; the old point has no zzz token) — the pre-R2 query
    # 'zzz alpha beta' now matches BOTH points via the R2 multi-field FTS.
    rebuilt = _cached_results(sdk, q="zzz-new")
    assert len(rebuilt) == 1


def test_fallback_snapshot_invalidated_on_operator(sdk):
    """create_operator invalidates the snapshot (via _mark_dirty)."""
    a = sdk.create_point("statement", "alpha beta gamma delta")
    b = sdk.create_point("statement", "alpha beta gamma delta")
    _no_match_query(sdk)  # build
    assert _store_has(sdk)
    sdk.create_operator("IMPL", a["id"], [b["id"]])
    assert not _store_has(sdk), "operator write must invalidate the snapshot"


def test_fallback_snapshot_invalidated_on_delete(sdk):
    """A delete invalidates the snapshot — the deleted point leaves the corpus."""
    keep = sdk.create_point("statement", "zzz unique deleted content token")
    pid = sdk.create_point("statement", "zzz unique deleted content other")["id"]
    # Force the fallback (FTS-miss query) → snapshot built with both points
    _no_match_query(sdk)
    proj = sdk._get_proj()
    key = fs.snapshot_key(proj, sdk._namespace)
    assert fs._store.get(key) is not None, "snapshot must be built"
    assert pid in {p["id"] for p in fs._store.get(key)["points"]}

    sdk.delete_point(pid)
    assert fs._store.get(key) is None, "delete must invalidate the snapshot"

    # Rebuild → the deleted point is gone from the corpus; the keeper remains
    _no_match_query(sdk)
    rebuilt = fs._store.get(key)
    rebuilt_ids = {p["id"] for p in rebuilt["points"]}
    assert pid not in rebuilt_ids and keep["id"] in rebuilt_ids


# ── Lazy TTL backstop ───────────────────────────────────────────────────

def test_fallback_snapshot_lazy_ttl_fires(monkeypatch):
    """TTL fires at read time (not a background timer) → rebuild, logged."""
    monkeypatch.setattr(fs, "SNAPSHOT_TTL_SECONDS", 1.0)
    # ``built_at`` must be seeded TTL-RELATIVE, not as an absolute literal:
    # ``time.monotonic()`` is seconds since BOOT, so a bare ``0.0`` only reads
    # as expired while host uptime exceeds the TTL — the same uptime coupling
    # as #3416 (harmless at a 1s TTL, but the same latent defect).
    fs._store.put(("g", "n"), {
        "built_at": time.monotonic() - fs.SNAPSHOT_TTL_SECONDS - 1.0,
        "dirty": False, "points": [],
        "vectorizer": None, "doc_vecs": None, "model_id": None,
    })
    got = fs._store.get(("g", "n"))
    assert got is None, "stale snapshot must be dropped at read"


def test_fallback_snapshot_dirty_dropped():
    fs._store.put(("g", "n"), {
        "built_at": 10 ** 9, "dirty": True, "points": [],
        "vectorizer": None, "doc_vecs": None, "model_id": None,
    })
    assert fs._store.get(("g", "n")) is None, "dirty snapshot dropped"


# ── Size cap + legacy delegation ────────────────────────────────────────

def test_fallback_snapshot_size_cap(sdk, monkeypatch):
    """Above the cap the snapshot is skipped → legacy path, no crash."""
    sdk.create_point("statement", "some content here")
    monkeypatch.setattr(fs, "MAX_CORPUS_POINTS", 0)
    results = sdk.tortoise_fts_query(query=FTS_MISS, limit=10)
    assert isinstance(results, list), "must fall back to legacy, not crash"
    assert not _store_has(sdk), "snapshot must be skipped over the cap"


def test_search_snapshot_legacy_delegation(sdk, monkeypatch):
    """No cached vectors (sklearn/model unavailable) → legacy scorer runs."""
    sdk.create_point("statement", "alpha beta gamma delta")
    _no_match_query(sdk)  # build (sklearn available in this env → vectors present)
    proj = sdk._get_proj()
    snap = fs._store.get(fs.snapshot_key(proj, sdk._namespace))
    assert snap is not None
    # Simulate missing vectors: the store's snapshot has none
    snap["vectorizer"] = None
    snap["doc_vecs"] = None
    snap["model_id"] = None
    results = fs.search_snapshot("alpha", snap, limit=10)
    assert len(results) == 1, "must delegate to the legacy scorer"


# ── #7760: per-store isolation ───────────────────────────────────────

def test_snapshot_store_isolated_across_embedded_dbs(tmp_path, monkeypatch):
    """#7760: the snapshot store must not serve one embedded DB's corpus to
    another.

    Every embedded DB defaults to ``graph_name='tortoise'``, so the old
    ``(graph_name, namespace)`` key collided across files: a point written by
    one test's SDK surfaced in another test's degraded search — the leaked
    ``fallback producer probe point`` in
    ``tests/test_tortoise_search.py::test_sdk_fts_query_empty`` caused by
    ``tests/test_subject_layer_read_surfaces_4889.py``. Both files must be
    safe in ONE process, which is the property the sharder assumes.

    The embedded mode is forced explicitly. On a URI-bearing session (the lane
    CI runs for a full selection) ``TortoiseSDK(<path>)`` is redirected to a
    server and derives a per-path graph name, so the old key would already
    differ and this test would pass without the fix. Deleting the URI keeps
    both SDKs genuinely embedded, so the test guards ``snapshot_key`` on every
    lane instead of only the carve-out lane.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    fs._store.clear()
    a = TortoiseSDK(str(tmp_path / "a.db"))
    b = TortoiseSDK(str(tmp_path / "b.db"))
    try:
        proj_a = a._get_proj()
        proj_b = b._get_proj()
        assert proj_a._is_embedded and proj_b._is_embedded, (
            "this test must run against embedded stores, not a redirected "
            "server — otherwise it passes without the fix")
        ka = fs.snapshot_key(proj_a, None)
        kb = fs.snapshot_key(proj_b, None)
        assert ka != kb, (
            "distinct embedded DBs must derive distinct snapshot keys, got "
            f"{ka!r} for both")
        a.create_point("statement", "fallback producer probe point")
        _no_match_query(a)  # builds + caches A's corpus snapshot
        assert fs._store.get(ka) is not None, "A must cache its own snapshot"
        assert fs._store.get(kb) is None, (
            "another embedded DB's snapshot is visible under this DB's key")
        # End-to-end shape of the original leak: B's degraded search must not
        # return A's point.
        b_hits = _no_match_query(b)
        assert all(r["content"] != "fallback producer probe point"
                   for r in b_hits), (
                    "another embedded DB's point leaked into this search: "
                    f"{[r['content'] for r in b_hits]}")
    finally:
        for s in (a, b):
            try:  # noqa: SIM105
                s.close()
            except Exception:
                pass
        fs._store.clear()


def test_snapshot_key_memory_shares_the_store_identity(tmp_path, monkeypatch):
    """#7773 P1: two REAL ``:memory:`` SDKs in one process attach to the SAME
    embedded store, so a write through one MUST invalidate the other's cached
    snapshot.

    redislite keys its daemon registry by ``<cwd>/:memory:.settings``, so both
    projections resolve to one store. The old key minted a per-projection
    ``uuid4`` on the false premise "a fresh server per projection", giving the
    two projections distinct keys: ``_mark_dirty``'s ``invalidate`` then
    reached only the writer's key, so the sibling's cached snapshot survived a
    create/delete on the same store and its degraded search kept serving the
    pre-write corpus (a just-written point invisible, a just-deleted point
    still returned) for up to ``SNAPSHOT_TTL_SECONDS``.

    MUTATION PIN: restoring the per-projection ``uuid4`` makes ``ka == kb``
    false and the invalidation assertion below fail.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.chdir(tmp_path)  # isolate the shared :memory: store to tmp
    fs._store.clear()
    a = TortoiseSDK(":memory:")
    b = None
    try:
        b = TortoiseSDK(":memory:")
        proj_a, proj_b = a._get_proj(), b._get_proj()
        assert proj_a._is_embedded and proj_b._is_embedded, (
            "this test must run against the embedded store, not a redirected "
            "server — otherwise it passes without the fix")
        assert proj_a.db.client.socket_file == \
            proj_b.db.client.socket_file, (
                "precondition: both projections must attach to one shared "
                ":memory: store")

        ka = fs.snapshot_key(proj_a, None)
        kb = fs.snapshot_key(proj_b, None)
        assert ka == kb, (
            "two :memory: projections over one shared store must derive ONE "
            f"snapshot key, got {ka!r} and {kb!r}")
        assert ka[1] == ("memory", os.path.realpath(
            proj_a.db.client.socket_file)), (
                "the :memory: identity must be the live daemon's endpoint "
                f"(socket), not an instance token: {ka[1]!r}")

        # A builds and caches its corpus snapshot under the shared key.
        a.create_point("statement", "shared memory store alpha beta")
        _no_match_query(a)  # FTS-miss → snapshot built
        assert fs._store.get(ka) is not None, "A must cache its snapshot"

        # A write through the SIBLING SDK dirties the same store → same key.
        b.create_point("statement", "shared memory store gamma delta")
        assert fs._store.get(ka) is None, (
            "a create through the sibling :memory: SDK must invalidate the "
            "cached snapshot — per-projection keys leave it stale")

        # End-to-end: the new point is visible on the rebuilt degraded read.
        rebuilt = fs._store.get(ka)
        if rebuilt is None:
            _no_match_query(a)
            rebuilt = fs._store.get(ka)
        assert any(p["content"] == "shared memory store gamma delta"
                   for p in rebuilt["points"]), (
                       "the sibling's write must reach the rebuilt corpus")
    finally:
        for s in filter(None, (a, b)):
            try:  # noqa: SIM105
                s._get_proj().g.query("MATCH (n) DETACH DELETE n")
            except Exception:
                pass
            try:  # noqa: SIM105
                s.close()
            except Exception:
                pass
        fs._store.clear()


def test_snapshot_key_memory_tracks_the_daemon_not_the_registry_slot(
        tmp_path, monkeypatch):
    """#7773 review: the registry path is a SLOT, not a store INSTANCE.

    A daemon that replaces a dead one at the same ``<cwd>/:memory:.settings``
    slot starts a fresh (empty) graph, so a key on the slot still matches the
    dead store's cached snapshot — a phantom point served for up to
    ``SNAPSHOT_TTL_SECONDS``. Keying on the live daemon's socket distinguishes
    the instance while still unifying siblings over one daemon.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.chdir(tmp_path)  # isolate the shared :memory: store to tmp
    fs._store.clear()
    sdk = TortoiseSDK(":memory:")
    try:
        proj = sdk._get_proj()
        client = proj.db.client
        key = fs.snapshot_key(proj, None)
        assert key[1] == ("memory", os.path.realpath(client.socket_file)), (
            f"the identity must be the live daemon endpoint: {key[1]!r}")
        orig_socket = client.socket_file
        try:
            # Simulate a replacement daemon at the SAME registry slot: it gets
            # a new temp socket, so the dead store's cached snapshot must not
            # be reachable under the new store's key.
            client.socket_file = "/tmp/replacement-daemon.socket"
            assert fs.snapshot_key(proj, None) != key, (
                "a replacement daemon at the same registry slot must not "
                "reuse the dead store's snapshot slot")
        finally:
            client.socket_file = orig_socket
    finally:
        try:  # noqa: SIM105
            sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
        except Exception:
            pass
        try:  # noqa: SIM105
            sdk.close()
        except Exception:
            pass
        fs._store.clear()


class _NoStoreHandle:
    """A duck-typed projection that exposes no store handle AND REJECTS the
    stamp the pre-fix code attempted (``__slots__``).

    Both halves are load-bearing for the #7773 P2 test: without ``__slots__``
    the per-projection stamp SUCCEEDS, so the pre-fix code's key was stable
    anyway and ``k1 == k2`` passed without the fix. Only a client the token
    cannot be stamped on reproduces the per-call re-mint. Shared by both
    unidentified-identity tests so the double cannot drift between them.
    """
    __slots__ = ()
    _path = ":memory:"
    graph_name = "tortoise"
    db = object()


def test_snapshot_key_memory_fallback_is_stable_not_minted(caplog):
    """#7773 P2: an unreadable store identity must NOT re-mint a key per call.

    The old code minted a fresh ``uuid4`` and then suppressed the failed
    assignment, so a client the token could not be stamped on returned a NEW
    key on every ``snapshot_key`` call: the entry could never be cache-hit and
    the corpus re-fetch + TF-IDF re-fit ran silently on every degraded search.
    The key must stay stable, and the shortfall must be reported loudly.
    """
    proj = _NoStoreHandle()
    with caplog.at_level(logging.ERROR, logger="tortoise.fallback_snapshot"):
        k1 = fs.snapshot_key(proj, None)
        k2 = fs.snapshot_key(proj, None)
    assert k1 == k2, (
        "an unreadable :memory: store identity must yield a STABLE key, not a "
        f"fresh per-call key — got {k1!r} then {k2!r}")
    assert any(r.levelno >= logging.ERROR for r in caplog.records), (
        "the fallback must be loud, not a silent fail-open")


def test_snapshot_key_memory_fallback_is_process_wide_shared():
    """#7827: the unidentified-store identity is a PROCESS-WIDE constant, not
    a per-projection token.

    The sibling test pins per-call stability of ONE instance — a property a
    per-projection MEMO also satisfies, while two distinct unidentified
    projections then derive two keys, so ``_mark_dirty``'s ``invalidate``
    reaches only the writer's and a sibling serves the pre-write corpus: the
    #7773 under-invalidation, in the branch the source comment calls the safe
    direction. The stated contract (``fallback_snapshot.py``,
    ``_UNIDENTIFIED_MEMORY_STORE``) is that ALL such projections share it.

    The sharing is pinned together with its documented cost: the shared tuple
    keys the READ path too, so the branch is deliberately NOT
    isolation-preserving. Pinning that residual means a later change to this
    branch has to be deliberate rather than silent.

    MUTATION PIN: replacing the constant with a per-projection token
    (``_memo.setdefault(id(proj), str(uuid.uuid4()))``) leaves the sibling
    test green and reddens this one.
    """
    a, b = _NoStoreHandle(), _NoStoreHandle()
    ka = fs.snapshot_key(a, None)
    kb = fs.snapshot_key(b, None)
    assert ka == kb, (
        "every projection whose store identity is unreadable must share ONE "
        f"process-wide key, got {ka!r} for one instance and {kb!r} for another")
    assert ka[1] == ("memory", fs._UNIDENTIFIED_MEMORY_STORE), (
        f"the shared identity must be the documented constant: {ka[1]!r}")

    fs._store.clear()
    try:
        fs._store.put(ka, _snap())
        assert fs._store.get(kb) is not None, (
            "the unidentified branch is deliberately not isolation-preserving "
            "— one such store's cached snapshot is visible under another's "
            "key, which is the residual the source comment states")
    finally:
        fs._store.clear()


def test_snapshot_key_memory_prefers_the_socket_over_the_registry_slot(
        tmp_path):
    """#7827 finding 3: with BOTH handles exposed the socket wins.

    ``socket_file`` identifies the running daemon INSTANCE (a replacement
    daemon at the same registry slot gets a new socket); the registry path is
    only the SLOT, so keying on it alone serves a dead store's phantom point
    (#7773 review). The #7786 reviewers confirmed the socket-first ordering is
    the safer direction but no test pinned it.

    MUTATION PIN: swapping the tuple order in ``_embedded_store_identity`` to
    ``("settingregistryfile", "socket_file")`` reddens this test.
    """
    class _Client:
        socket_file = str(tmp_path / "live-daemon.socket")
        settingregistryfile = str(tmp_path / ":memory:.settings")

    class _Proj:
        _path = ":memory:"
        graph_name = "tortoise"
        db = SimpleNamespace(client=_Client())

    key = fs.snapshot_key(_Proj(), None)
    assert key[1] == ("memory", os.path.realpath(_Client.socket_file)), (
        "a client exposing both handles must key on the live daemon's socket "
        f"(the registry path is only a reusable slot): {key[1]!r}")


@pytest.mark.parametrize("handle", ["client", "db"])
def test_snapshot_key_memory_registry_slot_is_the_fallback(tmp_path, handle):
    """#7827 finding 3: a client exposing ONLY ``settingregistryfile`` must key
    on the registry path, not fall through to the process-wide constant.

    Both real handles always expose ``socket_file``, so no real client reaches
    the fallback — the tuple entry appeared only in prose (an error message).
    Deleting it therefore changed no real behaviour and reddened nothing,
    which left the stated intent ("the registry path as fallback")
    unverifiable: a registry-only client silently became the process-wide
    key, i.e. over-invalidation plus the loud ERROR.

    ``handle`` selects WHERE the attribute lives — ``_embedded_store_identity``
    reads the projection's ``db`` and then ``db.client``, and only the
    ``client`` shape is what a real projection exposes.

    MUTATION PIN: ``for attr in ("socket_file",)`` reddens both parameters
    (the identity becomes the constant); dropping the ``os.path.realpath``
    normalization also reddens them (the symlinked registry path must resolve
    to one identity).
    """
    real = tmp_path / "real"
    real.mkdir()
    (real / ":memory:.settings").write_text("")
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    linked_registry = str(link / ":memory:.settings")

    class _Client:
        def __init__(self):
            self.settingregistryfile = linked_registry

    class _Proj:
        _path = ":memory:"
        graph_name = "tortoise"

        def __init__(self):
            self.db = (_Client() if handle == "db"
                       else SimpleNamespace(client=_Client()))

    key = fs.snapshot_key(_Proj(), None)
    assert key[1] == ("memory", os.path.realpath(str(real / ":memory:.settings"))), (
        f"a registry-only client (attribute on `{handle}`) must key on the "
        f"registry path, not the process-wide constant: {key[1]!r}")


def _snap():
    return {"built_at": time.monotonic(), "dirty": False, "points": [],
            "vectorizer": None, "doc_vecs": None, "model_id": None}


def test_snapshot_store_is_bounded_and_lru(monkeypatch):
    """#7760 review: per-backend keys must not make the store grow without
    bound; eviction is LRU so a hot store survives.

    The old ``(graph_name, namespace)`` key was bounded by the number of such
    pairs a process held; a per-backend key is not, so a long-lived process
    that opens many distinct embedded DBs (or ``:memory:`` stores) would retain
    one corpus each. A read bumps an entry so the hosted per-request-SDK
    pattern keeps its one graph's snapshot.
    """
    monkeypatch.setattr(fs, "MAX_SNAPSHOT_ENTRIES", 3)
    fs._store.clear()
    try:
        for i in range(5):
            fs._store.put((f"g{i}", None, None), _snap())
        assert len(fs._store._store) == 3, (
            "the store must not grow past MAX_SNAPSHOT_ENTRIES")
        assert fs._store.get(("g0", None, None)) is None, "oldest must be gone"

        fs._store.clear()
        for i in range(3):
            fs._store.put((f"g{i}", None, None), _snap())
        assert fs._store.get(("g0", None, None)) is not None  # read bumps g0
        fs._store.put(("g3", None, None), _snap())
        assert fs._store.get(("g0", None, None)) is not None, (
            "a recently-read entry must survive the cap (LRU, not FIFO)")
        assert fs._store.get(("g1", None, None)) is None, (
            "the least-recently-used entry is evicted")
    finally:
        fs._store.clear()


# ── #10602: single-flight corpus build ─────────────────────────────────
#
# The degraded read path built the snapshot with a bare ``build_snapshot``
# call, so EVERY concurrent reader ran its own whole-corpus fetch. Measured
# (idle local store, 10,000 points): N simultaneous degraded reads cost N
# builds — 8 readers took 4.0–4.6 s each while ONE took 0.8 s, i.e. a search
# got slower the more agents were searching, which is #10602's own signature
# (5 concurrent agents, every call ~13.2 s). ``get_or_build`` coalesces.

def _snap_stub(tag="s"):
    return {"built_at": time.monotonic(), "dirty": False, "points": [],
            "vectorizer": None, "doc_vecs": None, "model_id": None,
            "tag": tag}


def _flight_for(key: tuple, generation: tuple[int, int], store=None):
    """The registered flight for ``key``+``generation`` on the module store (or ``store``).

    The registry slot is ``(store, key, generation)`` -- store identity is part of it
    (review round 13), so a test cannot look the slot up by key alone.
    """
    target = fs._store if store is None else store
    for slot, flight in list(fs._FLIGHTS.items()):
        if slot[0] is target and slot[1] == key and slot[2] == generation:
            return flight
    return None


def test_get_or_build_coalesces_concurrent_readers():
    """N concurrent misses → exactly ONE build; every reader gets that snapshot."""
    import threading

    fs._store.clear()
    key = ("coalesce-graph", "coalesce-ns")
    builds = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def slow_builder(_proj):
        with lock:
            builds.append(1)
        time.sleep(0.25)  # long enough that all 8 readers are inside the miss
        return _snap_stub()

    out = {}

    def reader(i):
        start.wait()
        out[i] = fs.get_or_build(None, key, builder=slow_builder)

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(builds) == 1, (
        f"one corpus build for {len(out)} concurrent readers — got {len(builds)} "
        "(the pre-#10602 shape: N readers, N whole-corpus fetches)")
    assert len(out) == 8 and all(v is not None for v in out.values())
    assert len({id(v) for v in out.values()}) == 1, \
        "every reader must be served the same built snapshot"
    assert fs._FLIGHTS == {}, (
        "the last waiter out must remove the flight — a leak serves every later "
        "reader this stale result without ever reaching the store (review round 4, "
        "P3; nothing pinned the registry before)")


def test_get_or_build_reuses_cached_snapshot_without_building():
    fs._store.clear()
    key = ("cached-graph", "cached-ns")
    fs._store.put(key, _snap_stub("warm"))
    built = []

    def builder(_proj):
        built.append(1)
        return _snap_stub("cold")

    snap = fs.get_or_build(None, key, builder=builder)
    assert snap is not None and snap["tag"] == "warm"
    assert built == [], "a cached snapshot must not trigger a build"


def test_get_or_build_rebuilds_after_invalidation_and_survives_failure():
    """A write's invalidate() is still honoured, and a None build caches nothing."""
    fs._store.clear()
    key = ("inval-graph", "inval-ns")
    calls = []

    def builder(_proj):
        calls.append(1)
        return None if len(calls) == 1 else _snap_stub("second")

    assert fs.get_or_build(None, key, builder=builder) is None, \
        "a failed build must yield None (the legacy path), not a stale snapshot"
    assert fs._store.get(key) is None, "a failed build must not be cached"
    snap = fs.get_or_build(None, key, builder=builder)
    assert snap is not None and snap["tag"] == "second"
    fs._store.invalidate(key)
    assert fs._store.get(key) is None, "invalidate() still drops the entry"
    # …and the drop is HONOURED on the next read (review round 2: this half was
    # only asserted on the store, so a get_or_build that served the invalidated
    # snapshot across a write would have passed).
    rebuilt = fs.get_or_build(None, key, builder=lambda _p: _snap_stub("third"))
    assert rebuilt is not None and rebuilt["tag"] == "third", \
        "a read after invalidate() must REBUILD, not serve the dropped snapshot"
    assert len(calls) == 2, "the failed build must not have been retried implicitly"


def test_get_or_build_coalesces_a_FAILED_build_too():
    """The failure path must cost ONE attempt, not N (review round 2, P2).

    ``build_snapshot`` returns None above MAX_CORPUS_POINTS and on any query
    exception, and a None is never cached — so a per-key LOCK serialised those N
    readers into N identical doomed builds. A flight publishes the single outcome
    to everyone who joined it.
    """
    import threading

    fs._store.clear()
    key = ("fail-graph", "fail-ns")
    attempts = []
    lock = threading.Lock()
    start = threading.Barrier(6)
    out = {}

    def failing(_proj):
        with lock:
            attempts.append(1)
        time.sleep(0.15)
        return None

    def reader(i):
        start.wait()
        out[i] = fs.get_or_build(None, key, builder=failing)

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(attempts) == 1, (
        f"a failed build must be attempted ONCE for {len(out)} concurrent "
        f"readers — got {len(attempts)} (serialised failure is concurrency-linear "
        "latency on the over-cap / failing-store path)")
    assert all(v is None for v in out.values()), \
        "every joiner must get the None contract, so the legacy path runs"
    assert fs._store.get(key) is None, "a failure must never be cached"
    assert fs._FLIGHTS == {}, (
        "the flight registry must be empty after a failed cohort, or the next "
        "reader joins a completed flight and is served its None forever")


def test_a_raising_build_serves_the_whole_cohort_the_legacy_path():
    """A build that RAISES must not split the cohort (review round 4, P3).

    ``build_snapshot`` swallows its own two documented failures (over-cap, query
    exception), so a raise is an UNEXPECTED error. Left uncaught it propagates to
    the LEADER while every joiner reads ``result is None`` and quietly takes the
    legacy path — one failure, two different answers. The leader now publishes the
    documented None contract to the whole cohort, so every reader takes one path.
    """
    import threading

    fs._store.clear()
    key = ("raise-graph", "raise-ns")
    start = threading.Barrier(4)
    out = {}
    errors = []
    calls = []
    lock = threading.Lock()

    def raising(_proj):
        with lock:
            calls.append(1)
        time.sleep(0.15)
        raise RuntimeError("boom")

    def reader(i):
        try:
            start.wait()
            out[i] = fs.get_or_build(None, key, builder=raising)
        except Exception as exc:  # noqa: BLE001, RUF100
            errors.append(exc)

    threads = [threading.Thread(target=reader, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(calls) == 1, f"a raising build is attempted ONCE — got {len(calls)}"
    assert errors == [], (
        "the cohort must NOT see the raise: it degrades to the legacy path, as the "
        "pre-#10602 per-reader build's None contract did once build_snapshot "
        "swallowed its own failures")
    assert len(out) == 4 and all(v is None for v in out.values()), \
        "every reader gets the documented None contract"
    assert fs._store.get(key) is None, "a raise must never be cached"
    assert fs._FLIGHTS == {}, "the registry must be empty after a raising build"


def test_a_reader_arriving_AFTER_a_write_does_not_get_pre_write_rows():
    """Read-your-writes for the DELIVERY half (review round 6, P2).

    The generation guard stopped a build overtaken by a write from being CACHED —
    but the flight stays registered and joinable, so a reader whose own ``get``
    missed AFTER the write could still join that build and be served rows read
    before it. Pre-#10602 that reader ran its own build and saw the write, so
    serving the leader's rows loses the caller's own write. Measured before the
    fix: a reader starting after the write got the PRE-write rows.
    """
    import threading

    fs._store.clear()
    key = ("ryw-graph", "ryw-ns")
    started = threading.Event()
    release = threading.Event()
    out = {}

    def leader_builder(_proj):
        started.set()
        release.wait(timeout=10)
        return _snap_stub("pre-write")

    joiner_building = threading.Event()

    def joiner_builder(_proj):
        joiner_building.set()
        return _snap_stub("fresh")

    t1 = threading.Thread(
        target=lambda: out.__setitem__(
            "leader", fs.get_or_build(None, key, builder=leader_builder)))
    t1.start()
    assert started.wait(timeout=10), "the leader's build never started"

    # A write lands WHILE the build runs.
    fs._store.invalidate(key)

    t2 = threading.Thread(
        target=lambda: out.__setitem__(
            "joiner", fs.get_or_build(None, key, builder=joiner_builder)))
    t2.start()
    # ⛔ NOT "wait until the joiner has joined the leader's flight": with flights
    # keyed by ``(key, generation)`` a reader that arrives AFTER a write can never
    # join the pre-write build at all (it holds a different stamp), so it LEADS the
    # build for its own generation. The deterministic condition is therefore "the
    # late reader's own build has started", which is the mechanism this test exists
    # to pin — a shared-waiter count would no longer exist to wait on.
    assert joiner_building.wait(timeout=10), (
        "a reader that arrived after the write never started its own build — it "
        "must not be able to join the pre-write flight")

    release.set()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert out.get("leader") is not None and out["leader"]["tag"] == "pre-write"
    assert out.get("joiner") is not None, "the late joiner still gets an answer"
    assert out["joiner"]["tag"] == "fresh", (
        f"a reader that arrived AFTER the write must not be served the leader's "
        f"pre-write rows — got {out['joiner']['tag']!r}, i.e. read-your-writes is "
        f"broken for the degraded tier")
    # The joiner's fresh build is cached (it was not overtaken), the stale one is not.
    cached = fs._store.get(key)
    assert cached is not None and cached["tag"] == "fresh"
    assert fs._FLIGHTS == {}, "no flight may leak"


def test_a_raising_build_is_LOGGED(caplog):
    """The swallow is acceptable only because it is loud (review round 6, P3).

    ``except Exception`` publishes ``None`` to the cohort instead of raising; that
    is only right if it is observable. Deleting the ``logger.exception`` call kept
    every other test green, so the reason the swallow is safe was unpinned.
    """
    import logging

    fs._store.clear()
    key = ("logged-graph", "logged-ns")

    def raising(_proj):
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR):
        assert fs.get_or_build(None, key, builder=raising) is None

    assert any("corpus build raised" in r.getMessage() for r in caplog.records), (
        "an unexpected build failure must be logged, not silently swallowed")
    assert fs._FLIGHTS == {}


def test_get_or_build_does_not_publish_a_build_overtaken_by_a_write():
    """A write landing MID-BUILD must not be undone by that build's put.

    Review round 2: rows are read at t1, a write invalidates at t2, and a naive
    put publishes the PRE-write rows for the full 60 s TTL — to every reader,
    where before each reader built its own and the later one self-healed. The
    generation guard returns the answer to this cohort but caches nothing, so the
    next reader rebuilds.
    """
    fs._store.clear()
    key = ("race-graph", "race-ns")

    def builder_overtaken_by_a_write(_proj):
        # A write lands while the corpus is being read.
        fs._store.invalidate(key)
        return _snap_stub("stale")

    first = fs.get_or_build(None, key, builder=builder_overtaken_by_a_write)
    assert first is not None, "this cohort still gets an answer"
    assert fs._store.get(key) is None, (
        "a snapshot overtaken by a write must NOT be cached — caching it serves "
        "pre-write rows to every reader for the whole TTL")
    second = fs.get_or_build(None, key, builder=lambda _p: _snap_stub("fresh"))
    assert second is not None and second["tag"] == "fresh", \
        "the next reader must rebuild (self-healing, as the pre-#10602 path got free)"
    assert fs._store.get(key) is not None, "the clean build IS cached"


def test_a_cohort_overtaken_by_one_write_costs_ONE_build_not_one_per_reader():
    """A write during a build must not fan the cohort out into N builds.

    Review round 9, P2. The fix for round 6 (a late joiner must not be served
    pre-write rows) was written as an inline rebuild per reader, gated on "the
    generation changed" — a condition that is true for the WHOLE cohort, so one
    leader + 5 joiners cost **6 whole-corpus builds**: the exact amplifier this
    module exists to remove, restored, triggered by the ordinary event of a write
    landing during a build. Measured at that head: 6 builds.

    The mechanism that replaces it is structural rather than compensatory: the
    flight slot is ``(key, generation)``, so a reader that arrived before the
    write shares the leader's build (whose rows are no older than its own
    arrival), and a reader that arrived AFTER the write holds a different stamp
    and cannot join it at all. No reader needs to rebuild, so the cohort costs
    ONE build however many readers join it.
    """
    import threading

    fs._store.clear()
    key = ("cohort-graph", "cohort-ns")
    n_joiners = 5
    builds: list[int] = []
    builds_lock = threading.Lock()
    started = threading.Event()
    release = threading.Event()
    out: dict[str, dict | None] = {}

    def builder(_proj):
        with builds_lock:
            first = not builds
            builds.append(1)
        if first:
            started.set()
            release.wait(timeout=10)
            return _snap_stub("pre-write")
        return _snap_stub("rebuilt")

    g0 = fs._store.generation(key)
    leader = threading.Thread(
        target=lambda: out.__setitem__("leader", fs.get_or_build(None, key, builder=builder)))
    leader.start()
    assert started.wait(timeout=10), "the leader's build never started"

    joiners = [
        threading.Thread(
            target=lambda i=i: out.__setitem__(
                f"j{i}", fs.get_or_build(None, key, builder=builder)))
        for i in range(n_joiners)
    ]
    for t in joiners:
        t.start()
    # Deterministic: every joiner must be IN the leader's flight before the write,
    # so this pins "a cohort overtaken by a write" and not a scheduling accident.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        fl = _flight_for(key, g0)
        if fl is not None and fl.waiters >= n_joiners + 1:
            break
        time.sleep(0.005)
    else:
        raise AssertionError("the cohort never assembled on the leader's flight")

    fs._store.invalidate(key)   # the write that overtakes the in-flight build
    release.set()
    for t in [leader, *joiners]:
        t.join(timeout=20)

    assert len(out) == n_joiners + 1, f"every reader gets an answer, got {sorted(out)}"
    assert all(v is not None for v in out.values()), "no reader is dropped"
    assert len(builds) == 1, (
        f"a cohort of {n_joiners + 1} readers overtaken by ONE write must cost ONE "
        f"whole-corpus build, not one per reader — got {len(builds)} builds")
    # The overtaken build is still not CACHED (round 2's rule stands) …
    assert fs._store.get(key) is None, "an overtaken build must not be cached"
    assert fs._FLIGHTS == {}, "no flight may leak"


def test_same_key_writes_leave_the_degraded_cache_cold_which_is_the_stated_trade():
    """The cost of the freshness guard, PINNED rather than discovered (round 9, P1).

    ``_mark_dirty`` invalidates this key on every write surface, so when the write
    rate exceeds the inverse of the build time EVERY build window contains an
    invalidation: nothing is ever cached and the degraded tier costs one corpus
    fetch per read. The pre-#10602 code cached that overtaken build unconditionally,
    so on such a graph it achieved hits and this does not. That is a deliberate
    trade — the alternative serves rows that predate a committed write, so an agent
    that has just written a memory cannot find it — and this test exists so the
    trade is a decision rather than an accident: if someone later "restores" the
    unconditional ``put``, this test tells them what they are giving up, and if
    someone makes the guard cost more than one build per read, it fails.
    """
    fs._store.clear()
    key = ("trade-graph", "trade-ns")
    builds = []

    def builder(_proj):
        # Every build is overtaken: the write lands inside the build window, exactly
        # as it does on a graph written to faster than a corpus build completes.
        builds.append(1)
        fs._store.invalidate(key)
        return _snap_stub(f"build-{len(builds)}")

    for _ in range(5):
        assert fs.get_or_build(None, key, builder=builder) is not None

    assert len(builds) == 5, (
        f"the stated trade is one corpus fetch per read under continuous same-key "
        f"writes; got {len(builds)} builds for 5 reads — if this DROPPED, someone "
        f"started caching pre-write rows (see the guard's comment); if it ROSE, the "
        f"cohort is fanning out again")
    assert fs._store.get(key) is None, (
        "the cache must stay cold while every build is overtaken — caching a "
        "pre-write snapshot here is what round 2 forbade")
    assert fs._FLIGHTS == {}, "no flight may leak"


def test_a_reader_racing_the_commit_invalidate_tail_gets_the_cohorts_rows():
    """The bound is ONE FLIGHT, not "no result crosses a write boundary" (round 10, P2).

    ``_mark_dirty`` runs ``invalidate()`` at the TAIL of each write surface, AFTER the
    graph commit (``sdk.py:17590`` -> ``:17594``). So there is a window between a write
    COMMITTING and its invalidate landing. A reader whose ``get`` misses inside that
    window samples the PRE-write stamp and joins the pre-write flight.

    This test exists because the earlier round-9 tests modelled the write AS the
    invalidate (``fs._store.invalidate(key)`` at the exact instant of the write), which
    pins the idealised zero-width case and cannot see this window at all — so the
    comment's claim of "read-your-writes, structurally" went unchallenged. Here the
    commit and the invalidate are separated, which is what the real code does.

    The guarantee that DOES hold is the writer's own: a write surface invalidates before
    it returns, so a caller cannot search before its own write's stamp has moved —
    asserted at the end of this test.
    """
    import threading

    fs._store.clear()
    key = ("tail-graph", "tail-ns")
    built = threading.Event()
    release = threading.Event()
    rows: list[dict] = []
    # The write COMMITS between the leader's rows being read and its invalidate landing;
    # the racing reader arrives in that window. Modelled as an event the leader's builder
    # READS AT BUILD TIME, NOT as ``fs._store.invalidate`` — modelling the write AS the
    # invalidate is exactly what hid this window from the round-9 tests. ⛔ The event must
    # be READ BY THE BUILDER and asserted FALSE there: a ``committed`` flag set and
    # asserted by the test thread alone is unfalsifiable (review round 12, P3: that is
    # what the previous version did — wrapper changed, the property did not).
    committed = threading.Event()
    seen_at_build: list[bool] = []

    def leader_builder(_proj):
        # Read the commit state AT BUILD TIME, so the assertion below tests a fact the
        # test thread cannot set afterwards.
        seen_at_build.append(committed.is_set())
        built.set()
        release.wait(timeout=10)
        return _snap_stub("pre-commit")

    t1 = threading.Thread(
        target=lambda: rows.append(fs.get_or_build(None, key, builder=leader_builder) or {}))
    t1.start()
    assert built.wait(timeout=10), "the leader's build never started"

    # The write COMMITS now — after the leader's rows were read, BEFORE its invalidate.
    committed.set()

    heard: list[dict] = []
    t2 = threading.Thread(
        target=lambda: heard.append(
            fs.get_or_build(None, key, builder=lambda _p: _snap_stub("post-commit")) or {}))
    t2.start()
    # Deterministic: wait until that reader is IN the pre-write flight.
    g0 = fs._store.generation(key)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        fl = _flight_for(key, g0)
        if fl is not None and fl.waiters >= 2:
            break
        time.sleep(0.005)
    else:
        raise AssertionError("the racing reader never joined the pre-write flight")

    # NOW the write's tail runs.
    fs._store.invalidate(key)
    release.set()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert seen_at_build == [False], (
        f"the leader's rows must be read BEFORE the write committed (that is what makes "
        f"them pre-commit rows), and the builder records that fact at build time — got "
        f"{seen_at_build}. If this is [True] the test has modelled the commit as landing "
        f"before the build, i.e. it is no longer pinning the window it names")
    assert rows and rows[0].get("tag") == "pre-commit"
    assert heard and heard[0].get("tag") == "pre-commit", (
        "the documented behaviour: a reader racing the commit->invalidate tail is served "
        "the cohort's pre-commit rows, bounded by one flight. If this now returns "
        "'post-commit' the window was closed — GOOD, but update the COMMIT->INVALIDATE "
        "WINDOW note on get_or_build, because it currently states this bound")
    assert fs._FLIGHTS == {}, "no flight may leak"

    # ⭐ THE GUARANTEE THAT DOES HOLD, and the one a writer depends on: once the write
    # surface has RETURNED (its invalidate has run), a reader cannot join the pre-write
    # flight — its stamp has moved and it builds for itself.
    fs.get_or_build(None, key, builder=lambda _p: _snap_stub("after-tail"))
    post = fs.get_or_build(None, key, builder=lambda _p: _snap_stub("rebuilt"))
    assert post is not None and post.get("tag") == "after-tail", (
        "a writer that has already returned must not be served the pre-write cohort's "
        "rows — this is the read-your-writes half that DOES hold")


def test_two_stores_sharing_a_key_never_share_a_flight():
    """The ``store`` test seam must not hand one store's rows to another (round 13, P3).

    ``get_or_build`` accepts ``store`` as a test seam. With the flight slot keyed on
    ``(key, generation)`` alone, two DIFFERENT stores sharing a key were handed each
    other's flight: a reader on store B joined store A's in-flight build and was served
    **store A's snapshot**, while ``b.get(key)`` stayed ``None``. Unreachable in
    production (the only instantiation is the module singleton), which is worse rather
    than better — a store-injection test would have been a **false PASS**.
    """
    import threading

    store_a = fs.FallbackSnapshotStore()
    store_b = fs.FallbackSnapshotStore()
    key = ("shared-key-graph", "shared-key-ns")
    started = threading.Event()
    release = threading.Event()
    got: dict[str, dict | None] = {}

    def a_builder(_proj):
        started.set()
        release.wait(timeout=10)
        return _snap_stub("FROM-STORE-A")

    def b_builder(_proj):
        return _snap_stub("FROM-STORE-B")

    t1 = threading.Thread(
        target=lambda: got.__setitem__(
            "a", fs.get_or_build(None, key, store=store_a, builder=a_builder)))
    t1.start()
    assert started.wait(timeout=10), "store A's build never started"

    t2 = threading.Thread(
        target=lambda: got.__setitem__(
            "b", fs.get_or_build(None, key, store=store_b, builder=b_builder)))
    t2.start()
    t2.join(timeout=15)
    b_snap = got.get("b")
    assert b_snap is not None and b_snap["tag"] == "FROM-STORE-B", (
        f"store B's reader must be served STORE B's rows, got "
        f"{b_snap['tag'] if b_snap else b_snap!r} — a reader handed another store's "
        f"snapshot is a false PASS in any test using the ``store`` seam")
    cached_b = store_b.get(key)
    assert cached_b is not None and cached_b["tag"] == "FROM-STORE-B", (
        "store B's own cache must hold its own build")

    release.set()
    t1.join(timeout=20)
    a_snap = got.get("a")
    assert a_snap is not None and a_snap["tag"] == "FROM-STORE-A"
    assert fs._FLIGHTS == {}, "no flight may leak"


def test_a_write_opens_a_concurrent_build_which_is_the_stated_concurrency_bound():
    """At most one build per IN-FLIGHT GENERATION, not one per key (round 10, P3).

    The module banner used to say "one whole-corpus fetch per key, not one per reader".
    That is not what the code does: the slot is ``(key, generation)``, so a reader holding
    a newer stamp opens its own build while the older one is still running. That is a
    consequence of the correctness rule (a reader must never be served rows predating a
    write it could have seen), not of coalescing — the pre-#10602 code ran every reader's
    build concurrently too.

    What IS guaranteed, and what this test pins, is that the bound is the number of
    distinct generations in flight rather than the number of readers: the readers sharing
    a stamp still coalesce, so 4 same-stamp readers cost ONE build even though a second
    generation is building alongside.
    """
    import threading

    fs._store.clear()
    key = ("gen-graph", "gen-ns")
    builds: list[str] = []
    lock = threading.Lock()
    g0_started = threading.Event()
    g0_done = threading.Event()
    release_g0 = threading.Event()
    release_g1 = threading.Event()

    def builder(tag):
        def _b(_proj):
            with lock:
                builds.append(tag)
            if tag == "g0":
                g0_started.set()
                try:
                    release_g0.wait(timeout=10)
                finally:
                    g0_done.set()
            else:
                # ⛔ The g1 leader BLOCKS until released, so ``count("g1") == 1`` below
                # can only be reached by the other three SHARING this flight. Without
                # this the count also reads 1 when every late reader simply hits the
                # cache, or when several sequential flights each re-check the cache —
                # i.e. it would pass with ZERO coalescing (review round 11, P3: measured
                # 19/20 trials with 2-4 non-coalescing leaders while the count was 1).
                release_g1.wait(timeout=10)
            return _snap_stub(tag)
        return _b

    t0 = threading.Thread(
        target=lambda: fs.get_or_build(None, key, builder=builder("g0")))
    t0.start()
    assert g0_started.wait(timeout=10)

    # A write lands: the stamp moves while the g0 build is still running.
    fs._store.invalidate(key)

    # Four readers arrive on the NEW stamp. They must coalesce with EACH OTHER (one
    # build), and — the pinned bound — that build runs CONCURRENTLY with g0's.
    late = [
        threading.Thread(target=lambda: fs.get_or_build(None, key, builder=builder("g1")))
        for _ in range(4)
    ]
    for t in late:
        t.start()
    # Deterministic: wait until the four late readers are SHARING the g1 flight, so the
    # coalescing assertion below cannot be satisfied by cache hits or by sequential
    # flights. The g1 leader is blocking in the builder, so no read can finish early.
    g1 = fs._store.generation(key)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        fl = _flight_for(key, g1)
        with lock:
            # ⛔ BOTH facts, not just the waiter count (review round 12, P3):
            # ``_flight_begin`` bumps ``waiters`` for the LEADER before it reaches
            # ``produce()`` -> ``builder``, so ``waiters >= 4`` alone can be satisfied
            # while no g1 build has started — which then failed ``concurrent`` below and
            # printed "generations were serialised", a misleading diagnosis of code that
            # had not changed. Require the build to have entered as well.
            entered = builds.count("g1") >= 1
        if fl is not None and fl.waiters >= 4 and entered:
            break
        time.sleep(0.005)
    else:
        with lock:
            _entered = builds.count("g1")
        raise AssertionError(
            f"the four same-stamp readers never shared one flight "
            f"(waiters={getattr(_flight_for(key, g1), 'waiters', None)}, "
            f"g1_builds={_entered}) — they are not coalescing")

    with lock:
        # ⛔ ``not g0_done`` — the EVENT that says the g0 build has returned, not
        # ``not release_g0.is_set()``, which is True even after the builder's timeout
        # fired (review round 11, P3: the event was never set on timeout, so the proxy
        # was true when g0 had already finished).
        concurrent = builds.count("g0") == 1 and builds.count("g1") == 1 and not g0_done.is_set()
    assert concurrent, (
        "the stated bound: a newer generation builds CONCURRENTLY with an in-flight "
        "older one. If this fails, generations were serialised — a real change, and "
        "the BANNER note must be updated to say so")

    release_g1.set()
    release_g0.set()
    for t in [t0, *late]:
        t.join(timeout=20)

    with lock:
        assert builds.count("g0") == 1, "one build for the old generation"
        assert builds.count("g1") == 1, (
            f"the 4 same-stamp readers must coalesce onto ONE build, got "
            f"{builds.count('g1')} — the bound is distinct generations, not readers")
    assert fs._FLIGHTS == {}, "no flight may leak"


def test_degraded_read_with_claim_class_never_uses_the_snapshot(sdk, monkeypatch):
    """#2351 must survive the #10602 reshape (review round 2, P3).

    The snapshot is a LEAN projection with no class column, so a claim-class
    filter cannot be honoured from it — the read must take the legacy path and
    never call ``get_or_build``. A later "simplification" that calls
    ``get_or_build`` first would silently break the pre-truncation class filter
    with every other test green.
    """
    sdk.create_point("statement", "alpha beta gamma delta")
    called = []
    real = fs.get_or_build

    def spy(proj, key, **kw):
        called.append(key)
        return real(proj, key, **kw)

    monkeypatch.setattr(fs, "get_or_build", spy)
    trace: list[dict] = []
    results = sdk.tortoise_fts_query(
        query=FTS_MISS, limit=10, claim_class="asserted", leg_trace=trace)
    # ⛔ NOT a bare ``called == []``: that assertion alone passes VACUOUSLY if the
    # degraded arm stops firing for any reason (a leg starts answering, the miss
    # stops being a miss, the fallback is gated off) — ``get_or_build`` is equally
    # not called, and the #2351 invariant it exists to protect goes unpinned while
    # the test stays green (review round 4, P3: a false PASS). Pin that the LEGACY
    # arm actually served this read.
    assert isinstance(results, list) and results, (
        "the seeded point must survive the class filter — an empty result would "
        "make every assertion below vacuous")
    assert any(t.get("reason") == "tfidf_legacy" for t in trace), (
        "the claim-class read must be served by the LEGACY tf-idf arm; without "
        "this the test cannot tell 'correctly bypassed the snapshot' from 'never "
        "reached the degraded path at all'")
    assert called == [], (
        "a claim_class-filtered degraded read must NOT touch the snapshot "
        "(#2351: the lean projection cannot honour it)")


def test_degraded_read_path_goes_through_get_or_build(sdk, monkeypatch):
    """The #10602 wiring: the read path must COALESCE, not build per reader.

    A regression here is silent (results stay correct, latency grows with the
    fleet), so the call itself is pinned: one degraded read → ``get_or_build``
    once and no bare ``build_snapshot``.
    """
    sdk.create_point("statement", "alpha beta gamma delta")
    calls = []
    real = fs.get_or_build

    def counting(proj, key, **kw):
        calls.append(key)
        return real(proj, key, **kw)

    monkeypatch.setattr(fs, "get_or_build", counting)
    trace: list[dict] = []
    results = sdk.tortoise_fts_query(
        query=FTS_MISS, limit=10, leg_trace=trace)
    assert isinstance(results, list)
    assert calls, (
        "the degraded read never reached get_or_build — it is still calling "
        "build_snapshot directly, i.e. one whole-corpus fetch PER READER "
        "(#10602)")
    assert len(calls) == 1, f"one degraded read → one coalesced build ({len(calls)})"
    assert any(t.get("reason") == "tfidf_snapshot" for t in trace), \
        "the read must still be served by the snapshot arm it went through"


def test_put_if_generation_refuses_a_write_that_landed_after_the_read():
    """A write landing between the caller's ``generation()`` and its ``put`` must win.

    WHAT THIS PINS: the refused-insert contract — a snapshot whose build started
    under generation N is not cached once a write has invalidated, and a put under
    an unchanged generation still caches (the guard is not a veto).

    WHAT THIS DOES NOT PIN (review round 3, and the reason this note exists): the
    ATOMICITY of the compare+insert. This test is sequential, so it passes
    identically against a two-critical-section implementation that re-opens the
    window between the compare and the insert. That atomicity is enforced by
    construction — ``put_if_generation`` does both inside ONE ``with self._lock``
    hold — and is verified by READING that method, not by this test. Do not treat a
    green run here as coverage of it.
    """
    fs._store.clear()
    key = ("atomic-graph", "atomic-ns")

    # The build starts under generation N…
    generation = fs._store.generation(key)
    # …a write lands while it runs (i.e. before it reaches its put)…
    fs._store.invalidate(key)
    # …and the build's insert must therefore be refused.
    cached = fs._store.put_if_generation(key, _snap_stub("stale"), generation)
    assert cached is False, "an insert overtaken by a write must not report success"
    assert fs._store.get(key) is None, "the overtaken snapshot must NOT be cached"

    # A put under an unchanged generation still caches (the guard is not a veto).
    generation = fs._store.generation(key)
    assert fs._store.put_if_generation(key, _snap_stub("fresh"), generation) is True
    warm = fs._store.get(key)
    assert warm is not None and warm["tag"] == "fresh"


def test_a_write_to_ANOTHER_graph_must_not_refuse_this_keys_insert():
    """The guard is PER-KEY, not one process-global counter (review round 4, P1).

    ``_store`` is a module-level singleton shared by every tenant in a hosted
    process, so a global counter meant a write to ANY other graph refused the cache
    insert of every build overlapping it. Measured before the fix: 20 sequential
    reads of key A with ONE ``invalidate(keyB)`` inside each build window → 20
    builds / 0 cache hits, against 19/20 with no foreign write. In a writing fleet —
    #10602's own scenario — that turns the warm cache into a rebuild-per-read.
    """
    fs._store.clear()
    key = ("mine-graph", "mine-ns")
    other = ("their-graph", "their-ns")

    generation = fs._store.generation(key)
    fs._store.invalidate(other)  # a DIFFERENT graph's write lands mid-build
    assert fs._store.put_if_generation(key, _snap_stub("mine"), generation) is True, (
        "a write to another graph must not cost this key its cache insert — that is "
        "the global-counter regression (rebuild-per-read in a writing fleet)")
    warm = fs._store.get(key)
    assert warm is not None and warm["tag"] == "mine"

    # …and a write to THIS key still refuses it (the guard is not weakened).
    generation = fs._store.generation(key)
    fs._store.invalidate(key)
    assert fs._store.put_if_generation(key, _snap_stub("stale"), generation) is False

    # clear() resets the whole store, so it must refuse every key.
    generation = fs._store.generation(key)
    fs._store.clear()
    assert fs._store.put_if_generation(key, _snap_stub("pre-clear"), generation) is False, (
        "clear() is a whole-store reset — a build captured before it must not cache")


def test_get_or_build_still_caches_when_another_graph_writes_mid_build():
    """End-to-end form of the same pin, through ``get_or_build`` itself.

    The unit test above pins ``put_if_generation``; this pins the path callers
    actually take, so a future reshape that reintroduces a global stamp is caught
    at the seam that matters.
    """
    fs._store.clear()
    key = ("mine2-graph", "mine2-ns")
    other = ("their2-graph", "their2-ns")
    builds = []

    def builder(_proj):
        builds.append(1)
        fs._store.invalidate(other)  # foreign graph writes while we build
        return _snap_stub("mine2")

    first = fs.get_or_build(None, key, builder=builder)
    assert first is not None and first["tag"] == "mine2"
    assert len(builds) == 1
    assert fs._store.get(key) is not None, (
        "a foreign write during the build must NOT leave this key uncached "
        "(a global generation counter would — rebuild-per-read)")
    second = fs.get_or_build(None, key, builder=lambda _p: _snap_stub("rebuilt"))
    assert second is not None and second["tag"] == "mine2", (
        "the second read must be a CACHE HIT, not a rebuild")
    assert len(builds) == 1, "no second build — the foreign write did not evict us"
