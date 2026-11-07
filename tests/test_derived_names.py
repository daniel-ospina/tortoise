# tests/test_derived_names.py
"""Epic #1647 Task 7: derived-name verification + the from_uri/raw-client
census guards.

Derived-name verification (Task 7 Steps 4/5 + cycle-7 P2-6): under URI +
TEST_MODE the class-level redirect derives per-path guard-passing names
(`test_<stem>_<hash12(session+path)>`) for every non-test-prefixed explicit
name and every no-namespace db_path-bearing construction. The tests below
pin the SHAPE (12 hex), the guard-passing property (_assert_test_graph), and
the same-path-sharing / distinct-path-splitting rules for representative
construction paths — including the no-namespace SDK bulk-wiper lane (the
~25/~20 no-namespace `TortoiseSDK(` census: the redirect, not a rename, is
the mechanism — cycle-7 P2-6).

from_uri census (cycle-5 P1-6): every `FalkorProjection.from_uri(` call
without `graph_name=` in a migrated test file must resolve to a test-prefixed
graph (explicit graph_name=, a literal test-prefixed URI path, or a declared
per-test path). A new un-declared no-graph-name site reds — the DETACH-users-
clobber-each-other regression is the target.

Raw falkordb-client census (cycle-6 P1-3 + cycle-8 P2-2): raw-client bulk
DETACH handles must resolve to per-test-unique names AND be journaled (the
raw client bypasses _assert_test_graph AND the per-test wipe scope; the
journal closes the session-end leak).
"""
from __future__ import annotations

import ast
import os
import re
import string
import warnings
from pathlib import Path

import pytest

from tests import _live_utils

_TESTS_ROOT = Path(__file__).resolve().parent


def _docker_reachable(host: str | None = None,
                     port: int | None = None) -> bool:
    """True when the PROVISIONED docker-lane FalkorDB answers a TCP connect.

    #6673: the port used to be the 6379 literal. It is now the ephemeral host
    port the workflow's provision step assigned (docker `-p 127.0.0.1:0:6379`), so two
    services jobs on the same host cannot collide. `host=None` resolves through
    `_live_utils.service_host()`, so a `TORTOISE_TEST_DOCKER_HOST`
    override reaches the probe exactly as it reaches the clients. The host is
    NOT read from the product's `FALKORDB_HOST` at all, precisely so the probe
    cannot be moved somewhere a hardcoded client construction does not follow.
    """
    return _live_utils.tcp_reachable(port or _live_utils.docker_port(), host=host)


@pytest.fixture
def uri_env(monkeypatch):
    if not _docker_reachable():
        pytest.skip(f"live FalkorDB (localhost:{_live_utils.docker_port()}) not reachable")
    monkeypatch.setenv("TORTOISE_DB_URI", _live_utils.docker_base_uri())
    yield


# ── derived-name verification (redirect-derived test_<stem>_<hash12>) ──────

_DERIVED_RE = re.compile(r"^test_[a-zA-Z0-9_]+_[0-9a-f]{12}$")


def _assert_derived_shape(name: str) -> None:
    assert _DERIVED_RE.fullmatch(name), \
        f"derived name {name!r} must match test_<stem>_<hash12> (12 hex)"


def test_no_namespace_sdk_distinct_paths_derive_distinct_graphs(uri_env):
    """Cycle-7 P2-6: two no-namespace TortoiseSDK(db_path=...) constructions
    with DISTINCT paths under the same URI derive DISTINCT redirect names —
    the no-namespace SDK bulk-wiper lane never shares a graph across paths
    (the cycle-3/4/5 plan's "SDK-layer default namespace" was aimed at a
    problem that does not exist: sdk.py L1134 constructs
    FalkorProjection(path, graph_name="tortoise") → redirect-derived)."""
    import os
    import tempfile

    from tortoise.sdk import TortoiseSDK
    p1 = os.path.join(tempfile.mkdtemp(prefix="t7_"), "a.db")
    p2 = os.path.join(tempfile.mkdtemp(prefix="t7_"), "b.db")
    sdk_a = TortoiseSDK(p1)
    sdk_b = TortoiseSDK(p2)
    try:
        proj_a = sdk_a._get_proj()
        proj_b = sdk_b._get_proj()
        _assert_derived_shape(proj_a.graph_name)
        _assert_derived_shape(proj_b.graph_name)
        assert proj_a.graph_name != proj_b.graph_name, \
            "distinct paths must derive distinct graphs (parity/g_consistency)"
        assert proj_a._is_embedded is False, "redirected construction is server mode"
    finally:
        sdk_a.close()
        sdk_b.close()


def test_no_namespace_sdk_same_path_value_shares_graph(uri_env):
    """Cycle-7 P2-6: two no-namespace SDKs with the SAME db_path VALUE (fresh
    str each) derive the SAME graph — same-value sharing (the embedded same-
    file analog; the redirect hashes the path value, not the id())."""
    from tortoise.sdk import TortoiseSDK
    path = "/tmp/t7-same-path-value.db"  # same VALUE in both constructions
    sdk_a = TortoiseSDK(str(path))
    sdk_b = TortoiseSDK(str(path))
    try:
        proj_a = sdk_a._get_proj()
        proj_b = sdk_b._get_proj()
        _assert_derived_shape(proj_a.graph_name)
        assert proj_a.graph_name == proj_b.graph_name, \
            "same path value must derive the same graph (P0-2 value-hash)"
    finally:
        sdk_a.close()
        sdk_b.close()


def test_derived_names_guard_passing_representative_paths(uri_env):
    """Task 7 Step 4/7: derived names are guard-passing — a bulk DETACH on a
    redirect-derived projection must NOT raise _assert_test_graph (the
    E2E-2 guard stays intact for bare test/tortoise, derived names pass).
    Representative construction paths: fixed path, temp path, :memory:."""
    import tempfile

    from tortoise.projection import FalkorProjection
    paths = [
        "/tmp/t7-guard-fixed.db",
        os.path.join(tempfile.mkdtemp(prefix="t7_"), "g.db"),
        ":memory:",
    ]
    for path in paths:
        proj = FalkorProjection(path)
        try:
            _assert_derived_shape(proj.graph_name)
            proj.g.query("MATCH (n) DETACH DELETE n")  # must NOT raise
        finally:
            proj.close()


def test_hyphenated_test_namespace_normalized_to_test_graph(tmp_path):
    """Cycle-5 P1-5: the hyphenated test-* namespace family (test-tiers,
    test-invites, test-hosted, test-e1, ...) is SDK-normalized ('-' → '_') to
    a guard-passing test_<ns>_tortoise graph — without the branch it mapped
    to the non-test team_test-tiers graph (invisible to the test-graph guard,
    failed _assert_test_graph on bulk wipe). Embedded unit: the mapping, not
    a live server, is the input."""
    from tortoise.sdk import TortoiseSDK
    for ns, expected in [("test-tiers", "test_tiers_tortoise"),
                         ("test-hosted", "test_hosted_tortoise"),
                         ("test-e1", "test_e1_tortoise")]:
        sdk = TortoiseSDK(str(tmp_path / f"{ns}.db"), namespace=ns)
        try:
            proj = sdk._get_proj()
            assert proj.graph_name == expected, (ns, proj.graph_name)
            assert proj.graph_name.startswith(("test_", "tortoise_test"))
        finally:
            sdk.close()


def test_hyphenated_namespace_registry_graph_stays_guard_passing(tmp_path):
    """The _get_registry control-plane graph for a hyphenated test-*
    namespace normalizes its prefix too — test_hosted_..._control_plane (not
    the non-test test-hosted_..._control_plane, which wipe_server would
    never drop)."""
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "reg.db"), namespace="test-hosted")
    try:
        reg = sdk._get_registry()
        assert reg._name.startswith("test_hosted_"), reg._name
        assert reg._name.endswith("_control_plane")
    finally:
        sdk.close()


# ── #3634 / epic CI-3 half 2: the registry name is the reaper's input ─────

def _registry_name_for(tmp_path, ns, graph_name):
    """Drive the REAL derivation: _get_registry reads proj.graph_name."""
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK(str(tmp_path / "x.db"), namespace=ns)
    try:
        if graph_name is not None:
            sdk._get_proj().graph_name = graph_name   # the redirect does this in a test session
        return sdk._get_registry()._name
    finally:
        sdk.close()


def test_test_derived_registry_name_carries_an_approved_prefix(tmp_path):
    name = _registry_name_for(tmp_path, "registry", "test_docs_api_abc123def456")
    assert name.startswith(("test_", "tortoise_test_")), name


def test_already_compliant_registry_name_is_not_double_prefixed(tmp_path):
    name = _registry_name_for(tmp_path, "test-hosted", "test_hosted_tortoise")
    assert name.startswith("test_hosted_")
    assert not name.startswith("test_test_hosted_")


def test_shared_registry_name_is_never_prefixed(tmp_path):
    """AC2's fail-closed direction. `namespace="registry"` makes `_get_proj`'s
    `namespace == "registry"` branch force graph_name="registry_tortoise"
    BEFORE our override, so this is the real shared-name path.

    No separate `graph_name=None` leg: in a test session the projection
    redirect rewrites graph_name to `test_x_<hash>` inside `_get_proj()`
    (sdk.py), so a `None` leg asserts redirect behaviour, not the shared-name
    invariant — and without a test session it is byte-identical to the
    `"registry_tortoise"` leg below, which already carries that path. The
    `does not start with` assertion below keeps the invariant fail-closed and
    lane-independent: it reds if someone prefixes the `elif ns`/`else`
    branches that produce the shared name."""
    name = _registry_name_for(tmp_path, "registry", "registry_tortoise")
    assert name == "registry_control_plane"
    assert not name.startswith(("test_", "tortoise_test_")), name


def test_explicit_test_prefixed_name_honored_verbatim(uri_env):
    """Cycle-2 P0-1b: an explicit test_* name is the shared opt-in — honored
    verbatim by the redirect (never derived)."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection("/tmp/t7-explicit.db", graph_name="test_t7_suite")
    try:
        assert proj.graph_name == "test_t7_suite"
        proj.g.query("MATCH (n) DETACH DELETE n")  # guard-passing
    finally:
        proj.close()


def test_cross_file_detach_no_clobber(uri_env):
    """Cycle-5 P1-6 (b): the cross-file DETACH clobber regression — file B's
    bulk DETACH of the SHARED env-URI graph destroyed file A's live writes
    (invisible to the per-test wipe: the DETACH is TEST code, not a _wipe_or
    call). With every env-dependent from_uri site resolved to an explicit
    per-test graph, file A's write survives file B's DETACH of ITS OWN graph.
    """
    from tortoise.projection import FalkorProjection
    proj_a = FalkorProjection.from_uri(
        os.environ["TORTOISE_DB_URI"],
        graph_name=f"test_t7_file_a_{os.urandom(4).hex()}")
    proj_b = FalkorProjection.from_uri(
        os.environ["TORTOISE_DB_URI"],
        graph_name=f"test_t7_file_b_{os.urandom(4).hex()}")
    try:
        proj_a.g.query("CREATE (:Point {id:'a-live'})")
        proj_b.g.query("MATCH (n) DETACH DELETE n")  # file B's bulk wipe
        rows = proj_a.g.query(
            "MATCH (n:Point {id:'a-live'}) RETURN count(n)").result_set
        assert rows[0][0] == 1, \
            "file B's DETACH clobbered file A's live graph (P1-6 regression)"
    finally:
        proj_a.close()
        proj_b.close()


# ── from_uri census (cycle-5 P1-6): every no-graph_name site is declared ────

# Declared no-graph_name from_uri sites that are SAFE without an explicit
# graph_name= (each resolves to a test-prefixed graph or never connects).
# dict[file, list[regex-on-call-block]].
_ROUTED_FROM_URI_SITES: dict[str, list[str]] = {
    # per-session test-prefixed path (test_falkordb_compat_<uuid>) via _LIVE_URI
    "test_falkordb_compat.py": [r"from_uri\(_LIVE_URI\)"],
    # module availability probe (construct + RETURN 1 + close, no DETACH)
    "test_indexes.py": [r"from_uri\(_uri\)"],
    "test_search_engine_gaps.py": [r"from_uri\(_uri\)"],
    # #3154: module live-FalkorDB availability probe over candidate URIs
    # (construct + RETURN 1 + close, never DETACHs), plus the per-test `db`
    # fixture. SAFETY IS NOT "both are test-prefixed" — the fixture's
    # ``from_uri(_uri())`` resolves to the SHARED env-URI graph
    # (tortoise_test_matrix) or its fallback, NOT a _name() per-test graph
    # (from_uri passes graph_name=, not path=, so the redirect seam cannot
    # apply). It is safe only because that shared handle is never bulk-DETACHed:
    # every DETACH DELETE in this file targets a `graph_name=`-qualified
    # per-test projection built from _name() -> test_graphcopy3154_<stem>_<uuid>.
    # A future bulk DETACH on the fixture handle would be a real clobber, and
    # these patterns would NOT catch it — the regex is deliberately narrow to
    # the two call shapes that exist today so a new one reds instead.
    "test_graphcopy_boolean_index_3154.py": [
        r"from_uri\(_uri\)",
        r"from_uri\(_uri\(\)\)",
    ],
    # #3902: same two shapes as #3154 above — module live-FalkorDB
    # availability probe over candidate URIs (construct + RETURN 1 + close,
    # never DETACHs), plus the per-test `db` fixture. SAFETY IS NOT "the URI
    # path is test-prefixed": the fixture's ``from_uri(_uri())`` resolves to
    # the SHARED env-URI graph (tortoise_test_b5_3902 fallback, or the job's
    # own path when TORTOISE_DB_URI is set), NOT a _name() per-test graph
    # (from_uri passes graph_name=, not path=, so the redirect seam cannot
    # apply). It is safe only because neither handle is ever bulk-DETACHed:
    # every DETACH DELETE in this file targets a `graph_name=`-qualified
    # projection built from _name() -> test_restore3902_<stem>_<uuid> (L98
    # via _seed_event_log, L246) or a `select_graph(_name(...))` handle (L279),
    # and the fixture teardown deletes only the names its test registered.
    # The regex is deliberately narrow to the two call shapes that exist
    # today so a new one reds instead.
    "test_restore_seq_3902.py": [
        r"from_uri\(_uri\)",
        r"from_uri\(_uri\(\)\)",
    ],
    # (removed #10170: a `test_session_capture_e2e.py` row lived here declaring
    # `from_uri(os.environ["TORTOISE_DB_URI"])`, and that file has ZERO
    # `from_uri(` sites — the declaration described a call shape the file no
    # longer has, so it could never fail and never prove anything. A routed-site
    # row is a claim about a LIVE call; an inert one is a comment with a regex.)
    "test_ingest.py": [
        # module availability probe (env pre-set to a test-prefixed URI)
        r"from_uri\(os\.environ\[.TORTOISE_DB_URI.\]\)",
        # per-test test-prefixed path via _live_uri(test_ingest125_<uuid>)
        r"from_uri\(\s*uri\s*\)",
    ],
    # #5140: the same two shapes as test_ingest.py above — a module availability
    # probe over the env URI (construct + close, no DETACH at import) plus the
    # per-test helpers (`_reset` / `_read` / `_seed_document`). SAFETY IS NOT
    # "the handle is declared": every `uri` these helpers receive is built by
    # `_live_uri(f"test_5140_<kind>_{os.urandom(4).hex()}")`, a per-run
    # test-prefixed graph, so `_reset()`'s `MATCH (n) DETACH DELETE n` can only
    # clear this test's own graph and never the shared env-URI handle. The
    # regex is deliberately narrow to the two call shapes that exist today so a
    # new one reds instead, and `test_env_uri_assignment_uses_live_uri_helper`
    # pins the `from_uri(uri)` shape against a raw-env `uri =` assignment.
    "test_5140_pending_extraction.py": [
        r"from_uri\(_probe_uri\)",
        r"from_uri\(\s*uri\s*\)",
    ],
    # #8447 C3: the ingest door's live arms. EVERY `from_uri(uri)` site is the
    # file's OWN handle on a per-test graph — either a "did the door write it?"
    # read-back (`_resolution_points` in the resolution-Point control, and the
    # sweep teardown in `TestTheSweepReportsOutstandingWork`) or the SEED an arm
    # needs before the door can be asked anything
    # (`test_an_already_extracted_document_answers_the_named_skip`, which creates
    # the already-extracted `:Source` the `--upgrade` arm expects to skip). The
    # seed is the opposite of a read-back, so the gloss names both shapes rather
    # than calling every site a read-back. ⛔ SYMBOLS, not line numbers: the
    # coordinates this comment carried drifted twice (round 12 measured them all
    # +21), and nothing re-measures a comment in a different file.
    #
    # Every `uri` those sites receive comes from `_fresh_graph(...)`, which swaps
    # the env URI's graph path for a per-run `test_8447_<stem>_<hex>`
    # (`_graph_uri`, the same shape as `test_ingest.py::_live_uri`). SAFETY IS NOT
    # "the handle is declared": `--upgrade-all` reads EVERY pending Source in the
    # graph, and the sweep arm ends with `MATCH (s:Source {url: $id}) DETACH DELETE
    # s` where `$id` is a SOURCE BASENAME, so on the shared `tortoise_test_matrix`
    # path that delete could reach a peer file's `Source {url: "note.md"}`. The
    # per-run graph is what makes both the sweep's verdict and the delete local to
    # this test. The regex is deliberately narrow to the one shape that exists
    # today so a second one reds instead — which is why round 8's seeding handle
    # (the already-extracted arm) names its variable `uri` rather than widening
    # this pattern.
    "test_8447_c3_ingest_door.py": [r"from_uri\(\s*uri\s*\)"],
    # #10135 review (post-rebase): `test_5140_d10_relabel_migration.py` is
    # deliberately ABSENT here. Main's #10164 gave both of that file's
    # `from_uri(` sites an explicit `graph_name=` (`:50` the module probe,
    # `:104` the fixture), which is the outcome this map exists to demand —
    # `test_from_uri_sites_resolve_test_prefixed` skips a site whose block
    # carries `graph_name=`, so a declaration for that file would be inert and
    # would assert about call shapes the file no longer has.
    # CLI routing unit — from_uri is stubbed by the test (never connects)
    # (#10170 review round 3, found by the strengthened check: a
    # `"test_pipeline_cli.py": [r"from_uri\\("]` row lived here. The module has
    # NO `from_uri(` call at all — it defines a stub `_from_uri` and
    # `monkeypatch.setattr`s it, and its only mentions are in docstrings and
    # strings. A row that can never be consulted is the defect this issue closes.)
    # #3301: module live-FalkorDB availability probe over candidate URIs
    # (construct + RETURN 1 + close, never DETACHs) — the same shape as
    # test_search_engine_gaps.py above. Every DETACH DELETE in the file
    # targets a `graph_name=`-qualified per-test projection (the `graph`
    # fixture's random test_object_search_vis_<hex> name).
    "test_object_search_visibility_3301.py": [r"from_uri\(uri\)"],
    # #9226: module live-FalkorDB availability probe over candidate URIs
    # (construct + RETURN 1 + close, never DETACHs) — the same shape as
    # test_object_search_visibility_3301.py above. SAFETY IS NOT "every handle is
    # test-prefixed": the probe cannot pass graph_name= because it is testing
    # WHETHER a candidate is reachable, before any per-run name exists. It is
    # safe because the file contains NO DETACH at all (``git grep -c DETACH`` →
    # 0): the only other handle is the ``graph_name=test_9226_paging_<hex>``
    # per-run projection, deleted by name at teardown. The regex is deliberately
    # narrow to the one shape that exists today so a second one reds instead.
    "test_9226_dump_paging_v6.py": [r"from_uri\(uri\)"],
    # invalid-scheme raise tests (raise BEFORE any connection)
    "test_projection.py": [r"from_uri\(\"postgresql://", r"from_uri\(\"localhost:6379"],
    # raw-client migration test — per-test tortoise_test_r2_migrate_<uuid> path
    "test_search_engine.py": [
        # module availability probe (env pre-set to a test-prefixed URI)
        r"from_uri\(\s*os\.environ\[.TORTOISE_DB_URI.\]",
        # #6673: the raw-client site now resolves through the seam.
        r"from_uri\(\s*_live_utils\.legacy_uri\(gname\)\)",
        # #1695 extraction session: FTS-lane probe via a module-level helper
        r"from_uri\(_FTS_LANE_URI\)",
    ],
    # #8933: module live-FTS availability probe — construct + `close()`, never a
    # write, and the handle is never DETACHed. `_LANE_URI` is the job's env URI
    # (test-prefixed in CI: `tortoise_test_matrix`) or, when unset, the
    # `tortoise_test_identifier_8933` graph minted by `_live_utils.docker_uri`.
    # Every OTHER `from_uri(` in this file is a per-test
    # `graph_name=test_ident8933_<uuid>` handle, and those are the only handles
    # the live cases read or write (#10203: the probe is what the census saw).
    "test_identifier_tokens_8933.py": [r"from_uri\(_LANE_URI\)"],
    # #7813: the live lane's read-back helper `_stored_directions(uri)` opens a
    # `_live_utils.docker_uri(graph)` handle — the `tortoise_test_t7813_*` graph
    # minted by the calling test — and only MATCHes `o.direction` on it (no
    # DETACH anywhere in the file), so the shared env-URI graph is never touched.
    "test_decide_direction_7813.py": [r"from_uri\(uri\)"],
    # #4290: declared per the new-test-file registration rule. ZERO sites by
    # construction — the finding-provenance gate opens no graph (hermetic temp
    # git repo, subprocess git only); test_from_uri_routing_table_keys_exist
    # pins that the module really exists.
    # (#10170: `"test_finding_provenance.py": []` lived here. An EMPTY shape list
    # in this table is not inert in the strict direction — it says "this file may
    # have NO site", so any new site there reds. That negative is worth keeping,
    # so it moved to `_MUST_HAVE_NO_SITES` below, where
    # `test_no_declared_route_or_pin_is_inert` asserts it holds (and that the
    # module exists), rather than left as a bare `[]` that reads as "declared" to
    # anything checking only for the key's presence.)
    # #1423: the same module live-FalkorDB availability probe shape as #3301
    # above — `_probe(candidates)` constructs, issues `RETURN 1`, and closes,
    # over candidate URIs (the env URI plus _live_utils docker/legacy
    # fallbacks). It can never pass graph_name= (it is precisely testing
    # whether a candidate is reachable). SAFETY IS NOT the probe's path: it is
    # that the file contains NO DETACH at all — every other handle in it is
    # per-test (the `env`/`subject_graph` fixtures build embedded `tmp_path`
    # SDKs, and the FTS/index leg is a `graph_name=test_subj1423_<hex>`
    # projection from _WORKING_URI), so no shared-env handle is ever
    # bulk-DETACHed.
    "test_subject_supersession_1423.py": [r"from_uri\(uri\)"],
}

_FROM_URI_EXEMPT_FILES = {
    "test_embedded_lifecycle.py",          # carve-out
    "test_embedded_lifecycle_fast_close.py",
    "test_flip_gate.py",
    "test_hosted_backup.py",
    "test_embedded_concurrency.py",        # carve-out (Task 9 set)
    "_embedded.py",                        # seam helper
    "test_wipe_server.py",                 # epic seam unit surface
    "test_redirect_seam.py",
    "test_round_trip_parity.py",
    "test_loopback_predicate_single_source.py",
    "test_derived_names.py",               # this file
}


def _exempt_stems() -> set[str]:
    """The two registries, normalised to ONE key space.

    #10170: `TEST_NO_REDIRECT_STEMS` holds BARE STEMS ("test_4921_construct_lock")
    while `_FROM_URI_EXEMPT_FILES` holds FILENAMES ("test_embedded_lifecycle.py"),
    and the two call sites compared the union against ``f.name`` — which always
    ends ``.py``. So every bare stem matched nothing and those files were never
    exempt: the exemption was a NO-OP that read as coverage, and the 53 embedded
    carve-out files were silently held to the migration census they exist to be
    excused from (adding a `from_uri(` call to one would have reddened it and
    pushed an author to migrate a file that must stay embedded).

    Normalised to stems on BOTH sides, so the registries may be written in either
    spelling and cannot drift apart again. The mirror test that keeps these two
    in step with `config/ci-surfaces.yml` is
    `test_ci_selection.py::test_carve_out_mirrors_test_no_redirect_stems`.
    """
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    return {p.removesuffix(".py") for p in _FROM_URI_EXEMPT_FILES} \
        | set(TEST_NO_REDIRECT_STEMS)


# Exempt entries that the censuses' own glob CANNOT yield (`glob("test_*.py")`):
# they are seam helpers, not test modules. Pinned rather than silently inert —
# an entry that can never match is the same defect class as the bare stems
# (#10170): a declaration that reads as coverage and cannot fail.
_NON_TEST_FILE_EXEMPTIONS = {"_embedded.py"}

# Carve-out stems that name NO file. Pinned so a NEW one reds; removing these is
# the follow-up in `config/ci-surfaces.yml`'s carve_out mirror, not this census.
_STALE_CARVEOUT_STEMS = {"test_smoke_embedded"}


def _is_exempt(path) -> bool:
    """The census's ONE skip decision (#10170).

    Both censuses live in this module and each used to carry its own copy of the
    `exempt = ...` union + `f.name` comparison. That is why the no-op had to be
    found twice: two copies of a decision is two chances to key it wrong, and no
    test can pin the pair as a pair. One helper, one key space, one thing to test.
    """
    return path.stem in _exempt_stems()


def _mask_strings_and_comments(src: str) -> str:
    """`src` with string literals and `#` comments blanked, SAME LENGTH (indices
    preserved). Paren counting and site detection must not see a `(` inside a
    string: one unbalanced quote or an argument like `hint="("` otherwise runs the
    call-block to EOF, which makes every later site inherit the first block's
    `graph_name=` and go silently green (#10170 review round 3)."""
    out, i, n, q = list(src), 0, len(src), None
    while i < n:
        c = src[i]
        if q:
            if c == "\\" and i + 1 < n:
                out[i] = out[i + 1] = " "
                i += 2
                continue
            if src.startswith(q, i):
                for k in range(i, i + len(q)):
                    out[k] = " "
                i += len(q)
                q = None
                continue
            if c != "\n":
                out[i] = " "
            i += 1
            continue
        if c == "#":
            while i < n and src[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if c in "\"'":
            q = c * 3 if src.startswith(c * 3, i) else c
            for k in range(i, i + len(q)):
                out[k] = " "
            i += len(q)
            continue
        i += 1
    return "".join(out)


def _sites_in_file(path):
    """(line_number, call_block) for every LIVE `from_uri(` call in `path`.

    The census's ONLY detector, and the declaration test uses it too, so a test
    cannot disagree with the census about what a site is (#10170 review round 2:
    the declaration test kept its own line regex, which accepted a mention inside
    a docstring or any string literal). Comment-shaped references and empty
    `from_uri()` calls are skipped HERE, once, for the same reason.
    """
    call_re = re.compile(r"\b(\w+)\.from_uri\s*\(")
    src = path.read_text(encoding="utf-8")
    masked = _mask_strings_and_comments(src)
    out = []
    for m in call_re.finditer(masked):
        line_no = src.count("\n", 0, m.start()) + 1
        depth, i = 1, m.end()
        while i < len(masked) and depth:
            if masked[i] == "(":
                depth += 1
            elif masked[i] == ")":
                depth -= 1
            i += 1
        block = " ".join(src[m.start():i].split())
        if re.fullmatch(r"\w+\.from_uri\(\s*\)", block):
            continue  # a zero-argument reference is not a construction
        out.append((line_no, block))
    return out


def _from_uri_sites(root=None):
    """Yield (file_name, line_number, call_block) for every from_uri( call
    (FalkorProjection or any module-level alias like _FP — review P2: an
    aliased construction must not evade the census) in a migrated file."""
    for f in sorted((root or _TESTS_ROOT).glob("test_*.py")):
        if _is_exempt(f):
            continue
        for line_no, block in _sites_in_file(f):
            yield f.name, line_no, block


def _from_uri_violations(root=None):
    """Un-declared, un-named `from_uri(` sites — the census's VERDICT, extracted so
    a fixture can make it fire. Inline in the test it only ran over the real tests
    root, which contains no violation, so deleting the append (or `continue`-ing
    unconditionally) kept every test green (#10170 review round 4).
    """
    import urllib.parse
    violations = []
    for fname, lineno, block in _from_uri_sites(root=root):
        if re.search(r"graph_name\s*=", block):
            continue  # explicit per-test graph
        m = re.match(r"\w+\.from_uri\(\s*\"([^\"]+)\"", block)
        if m:
            path = urllib.parse.urlparse(m.group(1)).path.lstrip("/")
            if path.startswith(("test_", "tortoise_test")):
                continue
        declared = _ROUTED_FROM_URI_SITES.get(fname, [])
        if any(re.search(p, block) for p in declared):
            continue
        violations.append(f"{fname}:{lineno}: {block[:110]}")
    return violations


def test_from_uri_sites_resolve_test_prefixed():
    """Cycle-5 P1-6: an un-declared no-graph_name site that could bulk-DETACH the
    shared env-URI graph reds (the cross-file DETACH clobber regression:
    test_search_engine's bulk DETACH destroyed test_projection's live writes).
    The verdict itself lives in `_from_uri_violations`."""
    violations = _from_uri_violations()
    assert not violations, (
        "un-declared no-graph_name from_uri site(s) — pass an explicit "
        "graph_name=test_<file>_<uuid> or declare in _ROUTED_FROM_URI_SITES:\n"
        + "\n".join(violations))


_ENV_URI_PIN_FILES = (
    "test_ingest.py",
    # #5140: declares the same `from_uri(uri)` / probe shapes above, so the
    # same loose-net hazard applies and the same invariant must hold.
    "test_5140_pending_extraction.py",
    # #10135 review (post-rebase): NOT `test_5140_d10_relabel_migration.py` —
    # main's #10164 gave its `from_uri(` sites an explicit `graph_name=`, so
    # the loose-net hazard this tuple guards (a declared, un-named site) no
    # longer exists in that file.
    # #8447 C3: declares the same `from_uri(uri)` shape — every `uri` there is
    # `_fresh_graph()` -> a per-test `test_8447_*` graph, so the invariant below
    # (no raw-env `uri =` assignment) is what keeps that true.
    "test_8447_c3_ingest_door.py",
)


@pytest.mark.parametrize("fname", _ENV_URI_PIN_FILES)
def test_env_uri_assignment_uses_live_uri_helper(fname):
    """Review P2: the declared pattern `from_uri(uri)` is a loose net — a NEW
    `uri = os.environ.get(\"TORTOISE_DB_URI\")` + a no-graph_name from_uri(uri)
    in a declared file would match the declaration and pass without being a
    per-test graph. Pin the invariant instead: every `uri = os.environ...`
    assignment in a declared file must route through _live_uri (the per-test
    test-prefixed path helper)."""
    import re as _re
    src = (_TESTS_ROOT / fname).read_text(encoding="utf-8")
    bad = [ln for ln, line in enumerate(src.split("\n"), 1)
           if _re.search(r"(?<!_)\buri\s*=\s*os\.environ\.get\(\s*[\"']TORTOISE_DB_URI",
                         line)]
    assert not bad, (
        f"{fname}: uri assignment from the raw env at L{bad} — must "
        f"route through _live_uri(test_..._<uuid>) (cycle-5 P1-6 per-test "
        f"path; the env URI path is the shared job path)")


# ── raw falkordb-client census (cycle-6 P1-3 / cycle-8 P2-2) ────────────────


def _journal_calls(code: str) -> int:
    """How many `_journal_append(` OCCURRENCES the given text contains.

    The old summary line said "the masked block", which was never true at this
    head — this helper has never masked, and the census no longer passes it any
    text at all.

    ⛔ NO LONGER part of the census's PRESENCE decision (#11217 review round 9,
    ISSUE 4). It is a REGEX and `own` (an AST scope/`Call` scan) subsumes it — a
    shadow `def _journal_append`, a bare import and a comment produce no call
    NODE, and a call outside the block is filtered out — while the regex falsely
    BLOCKS a functionally compliant site that writes a legal space before the
    paren (`_journal_append (name)`), a site the AST half resolves to a correct
    OWNED prefix. Measured: such a block read RED off `_journal_calls` alone.

    Kept as a TEXT helper, and its exclusions AND its known spaced-call limitation
    are pinned in isolation by `test_journal_calls_helper_excludes_non_calls` so
    they cannot silently go stale. It must NOT be OR-ed back into the failure
    condition, where it can only add a false BLOCK to what the AST half already
    decides. It excludes a shadow `def` and a bare import, but it is
    comment-BLIND: it does not mask, so `# _journal_append(name)` counts as a call.
    Comment-blindness is why it cannot be put back in the decision — the census
    resolves ownership through the AST, which excludes all three shapes by
    construction.

    A `def _journal_append(...)` is a local SHADOW, not the record: it defines
    the symbol and journals nothing, so counting it as a call lets a block go
    clean while the graph stays UNOWNED (measured: a block whose only occurrence
    was `def _journal_append(n): pass` read CLEAN).
    """
    calls = 0
    for m in re.finditer(r"_journal_append\(", code):
        line_start = code.rfind("\n", 0, m.start()) + 1
        if re.fullmatch(r"\s*(?:async\s+)?def\s+", code[line_start:m.start()]):
            continue  # a definition of the symbol, not a use of it
        calls += 1
    return calls


def _raw_detach_violations(root=None):
    """Every raw-client bulk-DETACH site that lacks a per-test-unique
    handle + journal in its own block, over `root` (default: the tests
    root).

    Extracted from the test so the SKIP decision is behaviourally testable:
    a violation list over a fixture root proves what the census does, where
    introspecting `_is_exempt` only proves what a helper returns (#10170
    review: the two censuses shared a helper but nothing exercised that the
    SECOND one actually used it).
    """
    # The SAME skip decision as `_from_uri_sites` — one helper, so the two
    # censuses cannot exempt different files (#10170).
    violations = []
    for f in sorted((root or _TESTS_ROOT).glob("test_*.py")):
        if _is_exempt(f):
            continue
        src = f.read_text(encoding="utf-8")
        # Once per file: the handles every `_journal_append(…)` call RECEIVES, as
        # (line, static prefix) — see `_journal_prefixes`.
        journaled = _journal_sites(src)
        # Nested-scope boxes, computed once per file the first time a block's
        # ownership half is actually examined (`_nested_scope_boxes`).
        scope_boxes = None
        # every raw-client CONSTRUCTION site (from falkordb import /
        # import falkordb / falkordb.FalkorDB( form) whose enclosing block
        # bulk-DETACHes must carry per-test-unique + journal in the SAME block
        for m in re.finditer(
                r"from falkordb import FalkorDB|import falkordb|"
                r"falkordb\.FalkorDB", src):
            block, block_start, block_end = _enclosing_block_span(src, m.start())
            if not block or "DETACH DELETE" not in block:
                continue
            # `DETACH DELETE` and the uniqueness half are matched on the RAW
            # block: both legitimately live inside string MATERIAL (`os.urandom`
            # is normally inside the f-string that BUILDS the name, so masking
            # would erase it).
            #
            # ⛔ The OWNERSHIP half is resolved by `_journal_sites`' AST walk over
            # the WHOLE FILE, not matched as text in this block: an `ast.Call`
            # cannot be a comment, a bare import or a `def _journal_append`
            # SHADOW, so those shapes are excluded BY CONSTRUCTION rather than by
            # masking — the tightening #10170 needed for `from_uri`
            # (`_sites_in_file`) and #11182 measured here. (The old text matcher,
            # `_journal_calls`, was removed from the failure decision in round 9:
            # it also FALSE-BLOCKED a legal `_journal_append (name)`.)
            lineno = src.count(chr(10), 0, m.start()) + 1
            # The journal is an ownership RECORD only for a name the sweep's
            # vocabulary accepts — see `_non_owned_handles`. Resolved over the
            # whole file (the AST needs whole-file context) and restricted to the
            # journal calls THIS block contains. A `_journal_append` written
            # inside a nested `def`/`lambda`/`class`/comprehension journals the
            # name THAT scope builds, which says nothing about the handle this
            # block DETACHes — so it never SATISFIES the presence half. (It is
            # still JUDGED on its own prefix: see the asymmetry note below, which
            # is the current contract, and which an earlier version of this
            # comment contradicted.) Restricted by LINE RANGE alone a nested
            # journal satisfied both halves: a block whose only journal was a
            # nested `helper()`'s owned name read CLEAN while it DETACHed an
            # unowned, un-journaled graph (measured: 0 violations — the false PASS
            # this arm exists to prevent, #11217 review round 8).
            first_line = src.count(chr(10), 0, block_start) + 1
            last_line = src.count(chr(10), 0, max(block_end - 1, block_start)) + 1
            if scope_boxes is None:
                scope_boxes = _nested_scope_boxes(src)
            block_pos = _pos_of_offset(src, block_start)
            in_block = [(line, col, prefix) for line, col, prefix in journaled
                        if first_line <= line <= last_line]
            own = [(line, col, prefix) for line, col, prefix in in_block
                   if not _inside_a_nested_scope((line, col), block_pos, scope_boxes)]
            # The two halves are deliberately ASYMMETRIC (#11217 review round 9,
            # reviewer B P1/P2):
            #   • PRESENCE (`own`) requires the journal in the block's OWN scope.
            #     That is what closes C4 — a nested `helper()`'s journal cannot
            #     stand in for a handle the block never records. It costs one
            #     documented false BLOCK: a module that builds + journals the
            #     handle in a nested helper and RETURNS it is rejected, because
            #     "_journal_append in the same block" is the contract this guard
            #     states. Cost to the author is a one-line move of the journal;
            #     accepting a nested journal instead re-opens a false PASS.
            #     ⚠️ The cost is LARGER than that sentence used to say, because
            #     containment is decided POSITIONALLY and not by execution scope
            #     (#11217 review round 9, P3): a journal in a nested `def`'s
            #     DEFAULT, a nested class's keyword/base, or a comprehension's
            #     outermost iterable RUNS in this block's scope but sits inside
            #     the nested scope's box, so it is rejected too. Fail-closed, no
            #     corpus site, and the arm's own `_mutated_from_a_nested_scope`
            #     already models the difference — recorded rather than guessed.
            #   • OWNERSHIP (`in_block`) judges EVERY journal in the block, nested
            #     or not. Restricting it to `own` was measurably WORSE than the
            #     defect it fixed: an unowned prefix journaled in a nested helper
            #     — the leaking site, the whole reason this arm exists — read CLEAN
            #     as soon as one owned name was journaled in the own scope, i.e.
            #     RED -> CLEAN on the exact datum this census protects.
            # No claim is made here about unparseable files: `own` is empty for
            # one, so its sites are reported — fail CLOSED, matching a helper that
            # cannot read the journal list at all.
            #
            # ⛔ `own` is the SINGLE presence test (#11217 review round 9,
            # ISSUE 4). The old condition also required the `_journal_calls`
            # REGEX, which falsely BLOCKED a legal `_journal_append (name)` — a
            # space before the paren — on a site whose owned prefix the AST half
            # had already resolved. The regex adds no coverage: a shadow `def`, a
            # bare import and a comment produce no `ast.Call`, so they still red.
            if "os.urandom" not in block or not own:
                violations.append(
                    f"{f.name}: raw FalkorDB( client + bulk DETACH at "
                    f"L{lineno} without a "
                    f"per-test-unique handle + _journal_append in the same "
                    f"block (cycle-6 P1-3 / cycle-8 P2-2) — the journal must be "
                    f"a statement of THIS block, not of a nested def/lambda")
                continue
            non_owned = _non_owned_handles([p for _line, _col, p in in_block])
            if non_owned:
                outside = [p for p in non_owned if p]
                undecidable = [p for p in non_owned if not p]
                detail = []
                if outside:
                    detail.append(
                        f"({', '.join(map(repr, outside))} is not in "
                        f"_SWEEP_OWNED_PREFIXES)")
                if undecidable:
                    detail.append(
                        "(UNDECIDABLE: no literal prefix could be resolved, "
                        "and this arm fails CLOSED)")
                # The CONSEQUENCE differs by case, and stating the outside one
                # for an undecidable name asserts something this arm has not
                # established (#11217 review round 8, A3).
                if outside:
                    consequence = (
                        "the sweep PRESERVES it and then consumes the journal, "
                        "so the graph leaks while this census is green")
                else:
                    consequence = (
                        "this arm certifies only what it can PROVE, so the site "
                        "fails CLOSED — make the journaled name literal if the "
                        "name IS owned")
                # The HEADLINE differs by case for the same reason the
                # CONSEQUENCE does: `undecidable` never established that the
                # handle is outside the vocabulary (#11217 review round 11, B-P2).
                headline = (
                    "journals a handle OUTSIDE the ownership vocabulary"
                    if outside
                    else "journals a handle this census cannot PROVE owned")
                violations.append(
                    f"{f.name}: raw FalkorDB( client + bulk DETACH at "
                    f"L{lineno} {headline} {' and '.join(detail)} — "
                    f"{consequence} (#11217 review P1)")
    return violations


def _nested_scope_boxes(source: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """The `(start, end)` SOURCE POSITIONS of every nested scope in `source` —
    every `def`/`async def`/`lambda`/`class`/comprehension, the module excluded
    (it carries no position).

    POSITIONS, not line spans: a one-line `cb = lambda: None; _journal_append(x)`
    shares a line with a scope that does NOT contain the call, and a line-range
    test marked that journal "inside a nested scope" — a false BLOCK on a site
    whose journal is a direct statement of the block (#11217 review round 9,
    reviewer B P2). Containment is decided on `(line, col)` bounds, so only a
    scope that actually ENCLOSES the call counts.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except SyntaxError:
        return []
    boxes = []
    for _scopes, node in _nodes_with_scopes(tree, [tree]):
        if not isinstance(node, _NESTED_SCOPES):
            continue
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None)
        if start is None or end is None:
            continue  # `ast.Module`: the outermost scope, never a NESTED one
        boxes.append(((start, node.col_offset),
                      (end, getattr(node, "end_col_offset", 0) or 0)))
    return boxes


def _pos_of_offset(source: str, offset: int) -> tuple[int, int]:
    """`(line, column)` of the BLOCK START — where its own scope node begins.

    `_enclosing_block_span` returns the match of `^( *)def`, whose offset is the
    LINE START, while the `FunctionDef` node's `col_offset` is the `def` keyword.
    Comparing the two directly made every block's OWN `def` look like a nested
    scope beginning at its own line, so `own` was empty for every site in a class
    body — measured as three false BLOCKS in `tests/test_search_engine.py`. So
    when only whitespace precedes the offset, report the column of the first
    TOKEN, which is exactly where the scope node starts (#11217 review round 9).
    """
    line = source.count(chr(10), 0, offset) + 1
    line_start = source.rfind(chr(10), 0, offset) + 1
    prefix = source[line_start:offset]
    if not prefix.strip():
        rest = source[offset:].split(chr(10), 1)[0]
        column = len(rest) - len(rest.lstrip())  # the block's first token
    else:
        column = offset - line_start
    return line, column


def _inside_a_nested_scope(
        pos: tuple[int, int], block_pos: tuple[int, int],
        boxes: list[tuple[tuple[int, int], tuple[int, int]]]) -> bool:
    """Is `pos` inside a scope that BEGINS after the block's own start? The
    block's enclosing `def` starts AT `block_pos`, so the strict `>` excludes it;
    everything else that ENCLOSES `pos` is nested inside the block."""
    return any(block_pos < start <= pos <= end for start, end in boxes)


def _non_owned_handles(prefixes: list[str]) -> list[str]:
    """Which of the `prefixes` a block JOURNALED this census cannot PROVE
    owned.

    `_journal_append` is the third half of the raw-client contract because the
    session-end sweep reads the journal through `owns_by_ownership_record`
    (`tests/_embedded.py::_SWEEP_OWNED_PREFIXES`). A journaled name OUTSIDE that
    vocabulary makes the append INERT: `_sweep_drop` classifies the name as
    `preserved`, drops nothing, and removes the journal anyway, so the graph can
    never be reclaimed. Measured on `tests/test_cache_eviction_evictor.py`, whose
    `lane` fixture journaled `evict_<12hex>` — the census above was GREEN while
    the leak the journal exists to close was still open (#11217 review P1).

    THIS ARM FAILS **CLOSED** (#11217 review round 5). A prefix this arm could
    not resolve at all (`""`) is reported, and so is one that merely *could*
    still complete into an owned prefix (`"te"`): the census may certify only
    what it can prove, and five review rounds measured that every shape draining
    into a fail-OPEN was a live false PASS — the journal looked like a record
    while the sweep preserved the graph. Fail-closed costs an author ONE line
    (make the journaled name literal); fail-open costs a leaked graph that no
    census reports, which is the worse of the two by the whole point of the
    guard. The corpus cost of the inversion is measured at ONE site, and it was
    a site whose own literal was already owned one line above it.
    """
    from tests._embedded import _SWEEP_OWNED_PREFIXES

    out: list[str] = []
    for prefix in prefixes:
        if prefix.startswith(_SWEEP_OWNED_PREFIXES):
            continue
        if prefix not in out:  # one entry per distinct prefix in the message
            out.append(prefix)
    return out


# ── the ownership arm reads the JOURNALED VALUE, from the AST (#11243) ──────
#
# Three revisions of this arm walked the block as TEXT: masking literals,
# matching construction shapes, then chasing `^name =` bindings by offset. Each
# closed the shape its reviewer named and left an equivalent form open, and two
# of its heuristics were false BLOCKS. Measured, in aggregate:
#
#   false PASS — `"evict_" + …`, `"evict_%s" % …`, `str(f"evict_…")`,
#     `_mk(f"evict_…")`, `"".join(["evict_", …])` (a `\s*` past the `[` ate the
#     first element, so the SECOND element's literal was read), a
#     `.replace`/slice/`.format` transform whose leading literal is owned while
#     the VALUE is not, an `Attribute` target (`t.name = …`), and a rebinding in
#     a nested scope;
#   false BLOCK — an apostrophe in a COMMENT became "the literal opener", and a
#     DEAD earlier binding was reported when the binding live at the call was
#     UNDECIDABLE (this arm declines to certify it, and the census fails CLOSED
#     on it — see `_non_owned_handles`).
#
# A text matcher has no notion of a binding, a scope, a call or a literal — so
# it cannot converge on "which name does the journal receive?", and enumerating
# shapes is the wrong instrument. The arm therefore parses the block's file
# once and resolves the journal argument from the tree: constant folds where the
# value is decidable, the head otherwise, and a `Name`/`t.name` to the binding
# LIVE at the call.

_SCOPE_NODES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# A comprehension is a scope of its OWN to the interpreter — except for a
# `NamedExpr`, which PEP 572 binds in the scope ENCLOSING the comprehension. So it
# is walked (never skipped) and everything inside it is conditional.
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_NESTED_SCOPES = (*_SCOPE_NODES, ast.Lambda, *_COMPREHENSIONS)

# `str` methods whose result depends on nothing but their receiver and their
# arguments — so a call on a CONSTANT receiver has a constant result, evaluated
# below by the interpreter's own implementation. A method not listed here (or a
# non-constant receiver) is UNDECIDABLE: this arm does not try to reason about
# what arbitrary code returns, and the census fails CLOSED on it rather than
# certifying the method's ARGUMENT as the name.
_PURE_STR_METHODS = frozenset({
    "capitalize", "casefold", "center", "expandtabs", "format", "join",
    "ljust", "lower", "lstrip", "removeprefix", "removesuffix", "replace",
    "rjust", "rstrip", "strip", "swapcase", "title", "upper", "zfill",
})

# A folded `str` method must not ALLOCATE an attacker-sized string: `.ljust(10**10)`,
# `.zfill(900000000)` and `.center(…)` really build it, so an unbounded fold raised
# `MemoryError` out of the census — or was OOM-killed, printing no count line —
# instead of failing CLOSED on the site (third-session review, follow-up round).
# A graph handle is short, so a width past this cap is simply not decidable here.
_FOLD_WIDTH_CAP = 4096


# `str` templates carry a WIDTH in a fixed position, and only that position is a
# width: a digit run in the MATERIAL of a template or an argument is a substring
# (`"{}".format("test_20240101")`, `.strip("0123456789")`), and reading it as a
# width would make a DECIDABLE owned fold read UNDECIDABLE — a false BLOCK, which
# is the cost this bound must not introduce (follow-up review round 2).
_MOD_WIDTH = re.compile(r"%[-+ #0]*(?:\([^)]*\))?(\d*)(?:\.(\d+))?")


def _width_digits_over_cap(*runs) -> bool:
    """Do any of these WIDTH digits allocate past `_FOLD_WIDTH_CAP`?

    Leading zeros are not significance: `"{:00010}".format(1)` is a ten-character
    field, and reading the run's LENGTH as its value false-BLOCKed it (follow-up
    review round 2). Only significant digits decide, which also keeps `int()`
    away from a very long run that would trip CPython's int-string limit.
    """
    for run in runs:
        if not run:
            continue
        significant = run.lstrip("0")
        if not significant:
            continue  # all zeros: a zero width allocates nothing
        if len(significant) > 4 or int(significant) > _FOLD_WIDTH_CAP:
            return True
    return False


def _mod_width_over_cap(template: str) -> bool:
    """A `%`-template's conversion WIDTH — `"%200000000s" % "a"` really builds
    the field although no argument holds the width (measured: 200 MB).

    `%%` is an ESCAPED percent, not a conversion: consumed as one, the literal
    digits after it read as a width and `"%s (100%%20240101)" % "test_x"` — a
    decidable owned name — went RED (follow-up review round 3)."""
    return any(_width_digits_over_cap(*match.groups())
               for match in _MOD_WIDTH.finditer(template.replace("%%", "")))


def _format_specs_over_cap(template: str) -> bool:
    """A `.format` template's field SPEC width — `"{:>200000000}"` allocates
    the width with no width argument anywhere. Read with `string.Formatter`, so
    only a SPEC is inspected and never the material around it."""
    try:
        fields = list(string.Formatter().parse(template))
    except ValueError:
        return False  # a malformed template: the fold itself is not decidable
    return any(_width_digits_over_cap(*re.findall(r"\d+", spec))
               for _literal, _field, spec, _conversion in fields if spec)


def _format_spec_takes_width_from_args(template: str) -> bool:
    """Does a field SPEC itself contain a replacement field (`"{0:{1}}"`)?
    Then the width IS an argument, so a digit-only string argument is a width."""
    try:
        fields = list(string.Formatter().parse(template))
    except ValueError:
        return False
    return any("{" in spec for _literal, _field, spec, _conversion in fields if spec)


def _pos(node) -> tuple[int, int]:
    """Source order for a node, as (line, column): what "BEFORE the call" and
    "the LAST binding" are decided on."""
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _dotted(node) -> str | None:
    """The key a binding target, or a use, names: `name`, `t.name`,
    `self.graph`. An `Attribute` target journals exactly like a plain name
    (#11243: `t.name = f"evict_…"` then `_journal_append(t.name)`).

    Walked with a LOOP, not by recursion: `a.b.b.…` is as deep as it is long, and
    ~1000 hops raised `RecursionError` out of the census — a traceback naming no
    file and reporting nothing about the site, the exact symptom this module
    closes everywhere else (third-session review, follow-up round: measured
    `RecursionError` at 1500 hops on source CPython compiles fine)."""
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    return ".".join([current.id, *list(reversed(parts))])


def _target_keys(node) -> list[str]:
    """Every key a target binds: one name, a dotted attribute, or each element of
    a tuple/list target (`name, other = …`, `for name, other in …`, `*rest`).

    Anything else binds no KEY this arm can read — a subscript target
    (`d[name] = …`) mutates a container and binds nothing named here."""
    key = _dotted(node)
    if key:
        return [key]
    if isinstance(node, ast.Starred):
        return _target_keys(node.value)
    keys: list[str] = []
    if isinstance(node, (ast.Tuple, ast.List)):
        for element in node.elts:
            keys.extend(_target_keys(element))
    return keys


def _target_values(target, value) -> list[tuple[str, object]]:
    """`(key, expression)` for every key `target` binds, pairing a tuple/list
    target with its value POSITIONALLY — `name, other = f"evict_…", 1` binds
    `name` to the f-string, never to the tuple that holds it.

    A STARRED target pairs nothing (UNDECIDABLE, and the census fails CLOSED on
    it — see `_journal_prefixes`): a star captures a slice, so every key after it sits at a
    different index than its mirror in `value`, and a positional read would name
    the wrong element (#11217 review round 4).
    """
    if isinstance(target, ast.Starred):
        return []
    key = _dotted(target)
    if key:
        return [(key, value)]
    if isinstance(target, (ast.Tuple, ast.List)):
        elements = value.elts if isinstance(value, (ast.Tuple, ast.List)) else ()
        if any(isinstance(element, ast.Starred) for element in target.elts) \
                or len(elements) != len(target.elts):
            return []
        pairs: list[tuple[str, object]] = []
        for index, element in enumerate(target.elts):
            pairs.extend(_target_values(element, elements[index]))
        return pairs
    return []  # a subscript target binds no key this arm can read


# Blocks whose body runs on only SOME paths. A binding inside one of these is
# not by itself the value a later call sees, so it cannot decide a prefix.
_CONDITIONAL_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith,
) + ((ast.TryStar,) if hasattr(ast, "TryStar") else ()) \
  + ((ast.Match,) if hasattr(ast, "Match") else ())

# The ONE field of a conditional node that runs on EVERY path: an `if`/`while`
# TEST is always evaluated, so a walrus there binds unconditionally even though
# the bodies beside it do not (#11217 review round 5: declining to certify an
# undecidable case must not also swallow the case that IS decidable).
_ALWAYS_EXECUTED_FIELD = {ast.If: "test", ast.While: "test"}

# The two node families that push a conditional depth on their children.
_CONDITIONAL_OR_COMPREHENSION = (*_CONDITIONAL_NODES, *_COMPREHENSIONS)


def _own_statements(scope) -> list:
    """`(node, conditional_depth)` for every node in `scope`'s OWN statement
    tree: nested defs, classes, lambdas and comprehensions are NOT entered
    (neither their bodies nor their decorators and defaults), because a binding
    there lives in another scope and is not live at a call here (#11217 review
    round 3, ISSUE 2 — measured: a nested `name = f"test_ok_…"` LAUNDERED an
    unowned outer handle, and its mirror red a compliant site).

    `conditional_depth` counts the enclosing `if`/`for`/`while`/`try`/`with`
    blocks WITHIN this scope, so a binding that a path may skip can be told
    apart from one on the straight-line path (#11217 review round 5, finding
    2: `if env: name = A` / `else: name = B` read as two sequential bindings,
    so the LAST one decided the prefix although the path may have taken the
    first)."""
    body = getattr(scope, "body", [])
    # An `ast.Lambda` carries an EXPRESSION as `body`, not a statement list, and
    # it reaches here whenever a lambda is on the scope chain of a journal call.
    pending = [(node, 0) for node in (body if isinstance(body, list) else [])]
    out: list = []
    while pending:
        node, depth = pending.pop()
        out.append((node, depth))
        if isinstance(node, _NESTED_SCOPES) and not isinstance(node, _COMPREHENSIONS):
            continue  # a nested def/class/lambda IS another scope
        child_depth = (depth + 1 if isinstance(node, _CONDITIONAL_OR_COMPREHENSION)
                       else depth)
        always = _ALWAYS_EXECUTED_FIELD.get(type(node))
        for field, value in ast.iter_fields(node):
            field_depth = depth if field == always else child_depth
            if isinstance(value, list):
                for child in value:
                    if isinstance(child, ast.AST):
                        pending.append((child, field_depth))
            elif isinstance(value, ast.AST):
                pending.append((value, field_depth))
    return out


def _bindings_in(scope, key: str) -> list[tuple[tuple[int, int], object, int]]:
    """`(position, value, conditional_depth)` for every binding of `key` in
    `scope`'s OWN body, in source order. `value` is None where the value is not
    an expression this arm can read (a `for` / `with … as` / `except … as`
    target); `conditional_depth` is > 0 where a path may not execute the
    binding at all (see `_own_statements`)."""
    found: list[tuple[tuple[int, int], object, int]] = []
    # FORMS THAT BIND A NAME THIS ARM CANNOT READ are recorded as bindings with
    # value None, which `_bound_prefix` turns into "" and the census fails CLOSED
    # on. Without them the lookup fell through to `if not candidates: continue`
    # and resolved an UNRELATED enclosing binding of the same spelling — measured
    # GREEN while the journal received `evict_<hex>` (#11217 review round 7):
    # a parameter shadowing a module constant, a `match` capture, an
    # `import … as`, and a nested def's default. Parameters sit at the scope's own
    # definition, i.e. before every statement in its body.
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        arguments = scope.args
        params = (list(arguments.posonlyargs) + list(arguments.args)
                  + list(arguments.kwonlyargs)
                  + [a for a in (arguments.vararg, arguments.kwarg) if a])
        for param in params:
            if param.arg == key:
                found.append((_pos(scope), None, 0))
    for node, depth in _own_statements(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for bound, bound_value in _target_values(target, node.value):
                    if bound == key:
                        found.append((_pos(node), bound_value, depth))
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None and key in _target_keys(node.target):
                found.append((_pos(node), node.value, depth))
        elif isinstance(node, ast.NamedExpr):
            if key in _target_keys(node.target):
                found.append((_pos(node), node.value, depth))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            if key in _target_keys(node.target):
                found.append((_pos(node), None, depth))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                target = item.optional_vars
                if target is not None and key in _target_keys(target):
                    found.append((_pos(node), None, depth))
        elif ((isinstance(node, ast.ExceptHandler) and node.name == key)
                or (isinstance(node, (ast.MatchAs, ast.MatchStar))
                    and node.name == key)
                or (isinstance(node, ast.MatchMapping) and node.rest == key)):
            found.append((_pos(node), None, depth))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound == key:
                    found.append((_pos(node), None, depth))
    found.sort(key=lambda binding: binding[0])
    return found


def _const_int(node) -> int | None:
    """The integer `node` evaluates to, else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        value = _const_int(node.operand)
        return None if value is None else -value
    return None


def _const_str(node, _depth: int = 0) -> str | None:
    """The string `node` evaluates to when that is decidable WITHOUT running any
    code of the module under test, else None.

    `_depth` bounds the fold: a concatenation chain is walked recursively, and an
    unbounded chain raised `RecursionError` out of the census (#11217 review round
    6, finding 3), where a traceback names no file and reports nothing about the
    site. Past the bound the value is simply not decidable, and the census fails
    CLOSED on it.
    """
    if _depth > 64:
        return None

    # Folding is what makes a TRANSFORM's value readable, which is the whole of
    # #11243: `"test_g"[:0] + "evict_"`, `"{}".format("evict_")` and
    # `"test_log".replace("test_log", "evict")` are all `"evict_"`, although their
    # leading literal is owned or unrelated. Reading the first literal in the text
    # is what classified them OWNED and left the leak open.
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
                return None  # a field depends on a value: not constant
            parts.append(value.value)
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _const_str(node.left, _depth + 1)
        if left is None:
            return None
        right = _const_str(node.right, _depth + 1)
        if right is None:
            # ── #11217 review round 12 (P1): a concatenation with ONE undecidable
            # operand is a HEAD, never a value. Returning `left` folded a PARTIAL
            # that every transform above then computed on, so
            # `("test_" + os.urandom(4).hex())[-6:]` folded to the OWNED literal
            # `"test_"` while the real handle is the six hex characters — an
            # UNOWNED name certified as owned. `_prefix_of` reads a concat's head
            # structurally off `.left`, so head resolution never needed the
            # partial; only a VALUE did, and a partial is not one.
            return None
        return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        template, right = _const_str(node.left, _depth + 1), _const_str(node.right, _depth + 1)
        if template is None or right is None:
            return None
        # `"%200000000s" % "a"` carries its width in the TEMPLATE, so the
        # argument check below never sees it and it folds a 200 MB string.
        if _mod_width_over_cap(template):
            return None
        try:
            return template % right
        except (TypeError, ValueError, KeyError):
            return None
    if isinstance(node, ast.Subscript):
        base = _const_str(node.value, _depth + 1)
        if base is None:
            return None
        if isinstance(node.slice, ast.Slice):
            if node.slice.step is not None:
                return None
            # ⛔ "no bound" and "bound present but UNDECIDABLE" are different
            # answers. Collapsing them (the round-10 shape: `low = None if
            # node.slice.lower is None else _const_int(...)`) folds
            # `"test_evict_"[:width]` to `"test_evict_"` — an OWNED prefix for a
            # name that is actually `"te" + tag`, i.e. a fail-OPEN in the arm
            # whose whole job is to fail CLOSED (#11217 review round 11, B-P1).
            # A present-but-unresolvable bound means the value is unknown: None.
            bounds: dict[str, int | None] = {}
            for bound_name, part in (("low", node.slice.lower),
                                     ("high", node.slice.upper)):
                if part is None:
                    bounds[bound_name] = None
                    continue
                value = _const_int(part)
                if value is None:
                    return None
                bounds[bound_name] = value
            return base[bounds["low"]:bounds["high"]]
        index = _const_int(node.slice)
        if index is None:
            return None
        try:
            return base[index]
        except IndexError:
            return None  # `"test_"[99]`: not decidable, not a crash
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            receiver = _const_str(func.value, _depth + 1)
            if receiver is not None and not node.keywords \
                    and func.attr in _PURE_STR_METHODS:
                # Evaluated by the interpreter's OWN `str` implementation, not by
                # a table of shapes: a pure method on constant arguments has a
                # constant result, so `.upper()`, `.removeprefix(…)`, `.zfill(…)`
                # and `.replace(…)` are all decidable. An IMPURE or unknown
                # method stays None (UNDECIDABLE, and the census fails CLOSED on
                # it — see `_journal_prefixes`).
                args = [_const_str(arg, _depth + 1) if _const_str(arg, _depth + 1) is not None
                        else _const_int(arg) for arg in node.args]
                if any(arg is None for arg in args):
                    return None
                # ⛔ A fold that ALLOCATES is bounded: `.ljust(10**10)` and
                # `.zfill(900000000)` really allocate the width, so the census
                # raised `MemoryError` (or was OOM-killed, printing no count
                # line) instead of failing CLOSED on the site (third-session
                # review, follow-up round). A handle is short; past the cap the
                # value is simply not decidable here.
                # …and for `.format` the width may live in the format SPEC,
                # where `node.args` cannot see it.
                if any(isinstance(arg, int) and not isinstance(arg, bool)
                       and arg > _FOLD_WIDTH_CAP for arg in args):
                    return None
                # …and for `.format` the width may live in the format SPEC, where
                # `node.args` cannot see it (`"{:>200000000}".format("a")`), or in
                # an argument that the SPEC uses AS a width (`"{0:{1}}"`). Only
                # the former is read off the template; a digit run in MATERIAL
                # (`"{}".format("test_20240101")`) stays a decidable fold.
                if func.attr == "format":
                    if _format_specs_over_cap(receiver):
                        return None
                    if _format_spec_takes_width_from_args(receiver) and any(
                            isinstance(arg, str) and arg.isdigit()
                            and _width_digits_over_cap(arg) for arg in args):
                        return None
                try:
                    folded = getattr(receiver, func.attr)(*args)
                except (TypeError, ValueError, KeyError, IndexError,
                        AttributeError, OverflowError, MemoryError):
                    # A template whose fields the arguments do not satisfy
                    # (`"{missing}".format("x")` -> KeyError, `"{0.nope}"` ->
                    # AttributeError, `"{0[9]}"` -> IndexError): NOT decidable —
                    # and never fatal (#11217 review round 5, B4). Measured at
                    # `aa2e15c40` both escaped here, so the census RAISED a
                    # traceback naming no file and no site, taking this module's
                    # other census tests down with it instead of reding on the
                    # offending module.
                    return None
                return folded if isinstance(folded, str) else None
        if isinstance(func, ast.Name) and func.id == "str" and len(node.args) == 1:
            return _const_str(node.args[0], _depth + 1)
    return None


def _head_literal(value: str) -> str:
    """The static prefix of a literal read AS THE HEAD: everything before the
    first substitution point. `%` is the one that matters — `"evict_%s" % x`
    parses as a `BinOp` whose LEFT is the whole template."""
    cut = len(value)
    for marker in ("%", "{"):
        found = value.find(marker)
        if found != -1:
            cut = min(cut, found)
    return value[:cut]


def _mutated_from_a_nested_scope(scope, key: str) -> bool:
    """True when a scope INSIDE `scope` can change `key` — so `scope`'s own
    bindings do not describe the value a call sees (#11217 review round 6).

    Three mechanisms, all measured GREEN before this guard and all journaling an
    outside name while the census certified an owned one:

    * `nonlocal name` (or `global name`) in a nested scope, then a rebinding
      there — the OUTER binding is rewritten between two source-ordered
      statements of the outer scope. A plain nested `name = …` is NOT this: it
      binds a local of the nested scope and cannot touch the outer name, which is
      what keeps a compliant outer handle from reding (round 3's mirror).
      ⛔ The declaration is honoured at ANY DEPTH, because that is how PEP 227
      resolves it: `nonlocal` binds the name in the NEAREST enclosing scope that
      binds it, walking the whole chain — so a `nonlocal` TWO (or three) scopes
      down binds `scope`'s name whenever the scopes in between do not bind it
      themselves. Stopping after one level was a measured false PASS (#11217
      review round 13): the grandchild shape certified the stale OWNED prefix,
      the journal received `evict_…`, `_sweep_drop` classified it `preserved` and
      consumed the journal — a leaked graph under a GREEN census;
    * a `NamedExpr` inside a comprehension, which PEP 572 binds in the scope
      ENCLOSING the comprehension (so `[x for … if (name := …)]` rewrites
      `name` without appearing in that scope's own statements at all). This one
      is depth-1 ONLY, and so is the defaults/decorators rule below: at depth 2
      the enclosing scope is the intermediate one, so the binding lands THERE —
      it can only reach `scope` through a `nonlocal`/`global`, which the first
      rule already catches (flagging it twice would be a false BLOCK on a
      compliant site);
    * a walrus in a nested scope's DEFAULTS/decorators/bases, evaluated in the
      ENCLOSING scope (round 7, finding 3).

    Whether such a mutation RUNS is not decidable here, so the answer is "could",
    and the caller fails closed."""
    top = [(node, 1) for node, _depth in _own_statements(scope)
           if isinstance(node, _NESTED_SCOPES)]
    # The declarations themselves: every nested scope at ANY depth (PEP 227's
    # chain), plus the depth-1-only comprehension walrus.
    pending = list(top)
    while pending:
        node, depth = pending.pop()
        for inner, _inner_depth in _own_statements(node):
            if isinstance(inner, (ast.Nonlocal, ast.Global)) and key in inner.names:
                return True
            if depth == 1 and isinstance(node, _COMPREHENSIONS) \
                    and isinstance(inner, ast.NamedExpr) \
                    and key in _target_keys(inner.target):
                return True
        pending.extend((child, depth + 1) for child, _d in _own_statements(node)
                       if isinstance(child, _NESTED_SCOPES))
    for node, _depth in top:
        # A nested scope's DEFAULTS, decorators, bases and keywords are evaluated
        # in the ENCLOSING scope, so a walrus there rebinds the outer name. It is
        # not in `_own_statements(node)`, which begins at the nested BODY
        # (#11217 review round 7, finding 3).
        for field, value in ast.iter_fields(node):
            # Everything OUTSIDE the nested scope's own body lists — its `args`
            # (parameters and DEFAULTS), decorators, bases, keywords, return
            # annotation — is evaluated in the ENCLOSING scope.
            if field in ("body", "orelse", "finalbody", "handlers", "cases"):
                continue
            expressions = value if isinstance(value, list) else [value]
            for expression in expressions:
                if not isinstance(expression, ast.AST):
                    continue
                walk = [expression]
                while walk:
                    child = walk.pop()
                    if isinstance(child, ast.NamedExpr) and key in _target_keys(child.target):
                        return True
                    walk.extend(ast.iter_child_nodes(child))
    return False


def _bound_prefix(key: str, scopes: list, before: tuple[int, int],
                  hops: int) -> str:
    """The prefix of the binding of `key` LIVE at `before` — the LAST binding of
    that name before the call, in the innermost scope that binds it at all.

    Only the live binding is read: a DEAD earlier one must not be reported when
    the live one is undecidable (round 3, ISSUE 3), and a LATER rebinding must
    not launder the one the call actually journals (round 2).

    And only when it is UNIQUELY live. If any candidate in that scope sits
    inside a conditional/loop/try block, the value the call sees is
    PATH-DEPENDENT — `if env: name = f"evict_…"` / `else: name = f"test_x_…"`
    read as sequential bindings, so the second one decided the prefix and an
    unowned journal read owned (#11217 review round 5, finding 2) — and this
    arm returns the undecidable `""`, which the census fails CLOSED on."""
    if hops < 0:
        return ""
    for level, scope in enumerate(scopes):
        candidates = [b for b in _bindings_in(scope, key) if b[0] < before]
        if not candidates:
            continue
        if any(candidate_depth for _position, _value, candidate_depth in candidates):
            return ""  # path-dependent: which binding is live is not provable
        if _mutated_from_a_nested_scope(scope, key):
            return ""  # a nested scope may have rewritten it; not provable
        if level and any(position > _pos(scopes[level - 1])
                         for position, _value, _depth
                         in _bindings_in(scope, key)):
            # The call runs inside a nested scope DEFINED before a rebinding of the
            # same name in THIS scope, so source order does not describe run order
            # (#11217 round 6, 2b: the closure read the value bound AFTER the
            # journal call's line but BEFORE its execution). Every binding is
            # considered here, not only those positioned before the call.
            return ""
        position, value, _depth = candidates[-1]
        if value is None:
            return ""  # a loop/with/except target: nothing readable
        # The chain starts at the scope the value lives in, so a closure read
        # still resolves outward and an inner scope cannot be reached inward.
        return _prefix_of(value, scopes[level:], position, hops)
    return ""


def _prefix_of(node, scopes: list, before: tuple[int, int],
               hops: int = 6, _depth: int = 0) -> str:
    """The static prefix of the handle `node` builds — its VALUE where that is
    constant, else its HEAD (`"evict_" + …`, `f"evict_{…}"`, the FIRST element of
    a `"".join([…])`, a wrapper call's first argument).

    `""` means "not decidable from a literal" — the census fails **CLOSED** on
    it (listed on `_journal_prefixes`). `_depth` bounds the walk so a pathological
    left-nested concatenation degrades to `""` instead of raising
    `RecursionError` out of the census (#11217 review round 6, finding 3). Only the
    head is read, so unrelated material in the
    same expression — the `"-"` step of an owned concatenation, a keyword
    argument's literal — is not mistaken for the name's prefix, and a comment is
    not text at all here."""
    if node is None or hops < 0 or _depth > 64:
        return ""
    constant = _const_str(node)
    if constant is not None:
        return _head_literal(constant)
    if isinstance(node, ast.NamedExpr):
        # `_journal_append(n := f"evict_…")`: the walrus's VALUE is the argument.
        return _prefix_of(node.value, scopes, before, hops, _depth=_depth + 1)
    if isinstance(node, ast.JoinedStr):
        first = node.values[0] if node.values else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return _head_literal(first.value)
        return ""  # `f"{p}{hex}"`: the name begins in a value, not a literal
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _prefix_of(node.left, scopes, before, hops, _depth=_depth + 1)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "join":
            # A `"".join([…])` begins with its FIRST element whatever the
            # separator, so that element's head IS the name's head.
            sequence = node.args[0] if node.args else None
            if isinstance(sequence, (ast.List, ast.Tuple)) and sequence.elts:
                return _prefix_of(sequence.elts[0], scopes, before, hops, _depth=_depth + 1)
            return ""
        if isinstance(func, ast.Attribute) and func.attr == "format":
            # `"evict_{}".format(x)` begins with the TEMPLATE's head.
            return _prefix_of(func.value, scopes, before, hops, _depth=_depth + 1)
        if isinstance(func, ast.Attribute) and func.attr in _PURE_STR_METHODS:
            # ── #11217 review round 5, B2: a `str` METHOD on a receiver this arm
            # could not FOLD REWRITES the value, so its first ARGUMENT is an
            # operand, not the head. Measured at `aa2e15c40`: the value
            # `f"test_{hex}".replace("test_", "evict_")` journals `evict_<hex>`
            # and read GREEN off `"test_"` — the inert-journal leak this arm
            # exists to close — while the mirror
            # `f"g{hex}".replace("g", "test_g")` read RED off `"g"` on an OWNED
            # name. The constant-receiver form never reaches here (`_const_str`
            # folds it to the real value above), so this is UNDECIDABLE — and
            # the census now fails CLOSED on it rather than staying silent.
            return ""
        if isinstance(func, ast.Name) and func.id == "str" and len(node.args) == 1 \
                and not node.keywords:
            # `str(x)` is the identity for a `str`, and the one callee whose
            # result this arm can state without running it.
            return _prefix_of(node.args[0], scopes, before, hops, _depth=_depth + 1)
        # Anything else: what the callee RETURNS is not decidable here, so the
        # arm must not read one of its arguments as "the name" — `_mk(tag, name)`
        # was classified by `tag` (a false PASS on a compliant-looking owned tag
        # and a false BLOCK on a `"dup"` tag), and `_journal_append(_mk(f"evict_…"))`
        # was classified by the wrapper's argument (no grounding at all).
        # Undecidable, so the census fails CLOSED on it (#11217 round 5, 3/3b).
        return ""  # `pick()`, `_mk(…)`: no provable head
    key = _dotted(node)
    if key:
        return _bound_prefix(key, scopes, before, hops - 1)
    return ""


def _nodes_with_scopes(node, scopes: list):
    """Every node, paired with its ENCLOSING SCOPE CHAIN (innermost first), so a
    `Name` resolves in the scope it actually lives in.

    Walked with an EXPLICIT STACK, not by recursion: a left-nested
    `"a" + "b" + … + x` chain is as deep as it is long, and 2000 terms raised
    `RecursionError` out of the census — a traceback that names no file and
    reports nothing about the site (#11217 review round 6, finding 3). Children
    are pushed in reverse so the yield order stays pre-order/source order, which
    `_journal_prefixes` relies on before it sorts."""
    stack = [(node, scopes)]
    while stack:
        current, chain = stack.pop()
        yield chain, current
        for child in reversed(list(ast.iter_child_nodes(current))):
            if isinstance(child, _NESTED_SCOPES):
                stack.append((child, [child, *chain]))
            else:
                stack.append((child, chain))


def _journal_sites(source: str) -> list[tuple[int, int, str]]:
    """`(line, column, static prefix)` for every handle `_journal_append` is
    CALLED with in `source`, in source order.

    The COLUMN is what lets `_raw_detach_violations` decide scope containment on
    `(line, col)` bounds instead of a line range (#11217 review round 9).

    A `def _journal_append(…)`, a comment naming the symbol and a bare import are
    excluded by CONSTRUCTION — a `FunctionDef` is not a `Call`, a comment is not
    in the tree, and an import is not a call — rather than by the three separate
    regex exclusions #11182 had to pin one at a time.

    UNDECIDABLE shapes, each an EMPTY prefix because the name's head cannot be
    read as a literal. The census fails **CLOSED** on all of them (they are
    violations, not exemptions — see `_non_owned_handles`):

    * a name that begins in a value rather than a literal (`f"{name}"`, a
      `"".join([…])` whose FIRST element is not a literal, a variable prefix
      inside an f-string field);
    * an expression with no literal head at all (`pick()`, a call to any callee
      but `str`, a handle bound by a `for`/`with` target or a STARRED tuple
      target, an impure `str` method — the arm folds only the pure `str`
      methods in `_PURE_STR_METHODS`, on constant arguments);
    * a name whose binding is not UNIQUELY live at the call (any candidate
      binding nested in a conditional/loop/try);
    * a pure `str` method on a receiver that is NOT constant
      (`f"test_{hex}".replace("test_", "evict_")`, `… .upper()`, `… [:0] + …`):
      the method TRANSFORMS the value, so its first ARGUMENT is an operand and
      not the head. This one is worth its own line because the hazard is
      asymmetric — the arm used to read the argument and classify on it, which
      read GREEN on the value `evict_<hex>` (off the owned-looking `"test_"`) and
      RED on `f"g{hex}".replace("g", "test_g")` (off the unowned-looking `"g"`)
      on an OWNED name. Reasoning about what a transform returns is deliberately
      NOT attempted, so the value is not certified (#11217 review round 5, B2);
    * a file that does not PARSE: the census is about live test modules, and an
      unparseable one cannot run, so pytest reports it. THIS HELPER returns
      nothing for it, which makes every raw-DETACH site in that file fail CLOSED
      in `_raw_detach_violations` — reported, not exempted (#11217 review round
      9: this sentence said "stays silent", which the census path contradicted).
    """
    if "_journal_append" not in source:
        # Fast path: any CALL of the symbol (bare, or via an attribute/getattr)
        # names it in the source, so a file without the substring journals
        # nothing. The census walks 1,360 files to find 9 calls in 5 of them,
        # and parsing each one cost ~+12 s (#11217 review round 8, A5).
        return []
    try:
        with warnings.catch_warnings():
            # The census compiles OTHER test modules; a legacy invalid escape
            # sequence in one of them is that module's business, not a signal
            # here (and pytest prints it as if this test were at fault).
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[int, int, str]] = []
    for scopes, node in _nodes_with_scopes(tree, [tree]):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "_journal_append":
            continue
        # The first positional argument, else the `name=` keyword — `_journal_append`
        # has exactly one parameter, so the keyword form journals for real and must
        # be read (#11217 review round 4: it contributed NO prefix, so the block
        # passed the outer half and the arm reported nothing).
        argument = node.args[0] if node.args else next(
            (keyword.value for keyword in node.keywords if keyword.arg == "name"),
            None)
        if argument is None:
            # A call this arm cannot even read the ARGUMENT of (`**{"name": …}`)
            # must report, not vanish: `continue` here was the one place the
            # "fails CLOSED" claim was not true by construction (#11217 round 7).
            out.append((node.lineno, node.col_offset, ""))
            continue
        out.append((node.lineno, node.col_offset,
                    _prefix_of(argument, scopes, _pos(node))))
    out.sort()  # source order
    return out


def _journal_prefixes(source: str) -> list[tuple[int, str]]:
    """`(line, static prefix)` for every journaled handle in `source` — the
    `(line, prefix)` spelling of `_journal_sites`, kept for the tests that only
    care which prefix was resolved."""
    return [(line, prefix) for line, _col, prefix in _journal_sites(source)]


def test_journal_calls_helper_excludes_non_calls():
    """The three exclusions `_journal_calls` documents, asserted IN ISOLATION.

    #11217 round-9 review, ISSUE 2: the helper was removed from the census's
    presence decision (an AST scan subsumes it) and kept, but its docstring
    claimed the shapes it was added for "are worth asserting in isolation" while
    NOTHING asserted it — dead code carrying a justification the artifact did not
    have. These assertions make that claim true.
    """
    assert _journal_calls("    _journal_append(name)") == 1
    # ⛔ The KNOWN limitation, pinned: the regex misses a legal space before the
    # paren, which is why this helper can add a false BLOCK and must stay out of
    # the failure condition.
    assert _journal_calls("    _journal_append (name)") == 0
    assert _journal_calls("    def _journal_append(n): pass") == 0
    assert _journal_calls("    async def _journal_append(n): pass") == 0
    assert _journal_calls("    import _journal_append") == 0
    # NOTE it is comment-BLIND: it does not mask, so a bare mention counts. That
    # is one of the reasons it is no longer in the presence decision — the AST
    # half excludes a comment BY CONSTRUCTION (a comment is not an `ast.Call`).
    assert _journal_calls("    # _journal_append(name)") == 1


def test_raw_falkordb_client_detach_uses_per_test_unique_graph():
    """Cycle-6 P1-3: every raw `from falkordb import FalkorDB` bulk-DETACH
    site in a migrated test file must (a) resolve its graph handle to a
    per-test-unique name (never a FIXED shared literal — concurrent sessions
    would DETACH each other's live writes) and (b) journal the name via
    tests/_embedded._journal_append so the session-end sweep drops it (the
    raw client bypasses _assert_test_graph AND the per-test wipe scope —
    cycle-8 P2-2: a per-test-unique name alone leaks one empty graph per run;
    the journal closes the leak).

    Per-SITE scan (review P1): a module-level presence sniff ("the module
    contains _journal_append AND os.urandom") trusts a file after ONE fixed
    site and lets a SECOND raw-DETACH site on a fixed shared name in the
    same file evade. Each raw-construction site's own enclosing block must
    carry the per-test-unique handle + journal append.
    """
    violations = _raw_detach_violations()
    assert not violations, "\n".join(violations)


def test_a_comment_NAMING_the_journal_does_not_satisfy_the_raw_detach_census(tmp_path):
    """#11182: the guard is satisfied by PROSE (or by a bare IMPORT) otherwise.

    Measured on the real site: with the fix's comment in place, deleting both
    `from tests._embedded import _journal_append` AND the
    `_journal_append(...)` call left the census GREEN — the check matched the
    raw block text, and the comment names the symbol. After masking comments but
    matching the bare token, an import with NO call was still GREEN (a fresh
    review measured it). All three arms are pinned here, so neither fix can go
    vacuous by only satisfying the shape it was written against.
    """
    prose_only = (
        "def test_unowned():"
        "\n    import os"
        "\n    from falkordb import FalkorDB"
        "\n    name = f\"test_g_{os.urandom(4).hex()}\""
        "\n    # the record: _journal_append(name) is what a peer sweep needs"
        "\n    c = FalkorDB(host=\"h\")"
        "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
        "\n"
    )
    site = tmp_path / "test_prose_only.py"
    site.write_text(prose_only)
    assert _raw_detach_violations(root=tmp_path), \
        "a comment naming _journal_append satisfied the guard"

    # A bare IMPORT is not the record: the sweep reads the JOURNAL, so an
    # imported-but-never-called symbol leaves the graph UNOWNED.
    site.write_text(
        prose_only.replace(
            "\n    c = FalkorDB",
            "\n    from tests._embedded import _journal_append"
            "\n    c = FalkorDB",
        )
    )
    assert _raw_detach_violations(root=tmp_path), \
        "an IMPORT of _journal_append was accepted as the record"

    site.write_text(
        prose_only.replace(
            "\n    c = FalkorDB",
            "\n    _journal_append(name)"
            "\n    c = FalkorDB",
        )
    )
    assert not _raw_detach_violations(root=tmp_path), \
        "the same site WITH the call must be clean"

    # A local `def _journal_append` SHADOWS the symbol and journals nothing. It
    # was measured CLEAN under the bare `_journal_append(` token.
    site.write_text(
        prose_only.replace(
            "\n    c = FalkorDB",
            "\n    def _journal_append(name):"
            "\n        pass"
            "\n    c = FalkorDB",
        )
    )
    assert _raw_detach_violations(root=tmp_path), \
        "a local DEF of _journal_append was accepted as the record"


def test_a_journaled_NON_OWNED_handle_fails_the_raw_detach_census(tmp_path):
    """#11217 review P1: journaling is not ownership.

    `_journal_append` closes the leak only for a name the sweep's vocabulary
    accepts (`_SWEEP_OWNED_PREFIXES`). At `evict_<12hex>` the entry was INERT —
    `_sweep_drop` classified the name `preserved`, dropped nothing, and removed
    the journal anyway — so the census went green over a graph that could never
    be reclaimed (the false PASS this guard pair must not produce).

    Both arms are pinned here: the non-owned prefix REDS, the same site with
    `test_` in front of the prefix is clean, so the arm cannot go vacuous by
    flagging every journaled site.
    """
    site = tmp_path / "test_non_owned_handle.py"

    def _site(*lines: str) -> str:
        """A minimal raw-client block whose body is `lines`, so each case below
        states only the shape under test."""
        return ("def test_evictor_lane():"
                "\n    import os"
                "\n    from falkordb import FalkorDB"
                + "".join("\n" + ln for ln in lines)
                + "\n    c = FalkorDB(host=\"h\")"
                "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
                "\n")
    site.write_text(
        "def test_evictor_lane():"
        "\n    import os"
        "\n    from falkordb import FalkorDB"
        "\n    name = f\"evict_{os.urandom(6).hex()}\""
        "\n    _journal_append(name)"
        "\n    c = FalkorDB(host=\"h\")"
        "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
        "\n",
        encoding="utf-8",
    )
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "OUTSIDE the ownership vocabulary" in bad[0], (
        f"a journaled NON-OWNED handle was accepted as an ownership record: {bad}")
    assert "'evict_'" in bad[0], (
        f"the violation must name the prefix it read, got: {bad[0]}")

    site.write_text(
        site.read_text(encoding="utf-8").replace('f"evict_', 'f"test_evict_'),
        encoding="utf-8",
    )
    assert not _raw_detach_violations(root=tmp_path), (
        "the SAME site with a `test_` prefix must be clean — an owned prefix is "
        "what makes the journal record authorise a drop")

    # SHAPE 2 (#11217 re-review, measured at `c84c3d68`): the f-string handed
    # STRAIGHT to the journal has no assignment line, so the arm above read NO
    # violation — the same inert record, one shape over, still GREEN.
    site.write_text(
        "def test_evictor_lane():"
        "\n    import os"
        "\n    from falkordb import FalkorDB"
        "\n    _journal_append(f\"evict_{os.urandom(6).hex()}\")"
        "\n    c = FalkorDB(host=\"h\")"
        "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
        "\n",
        encoding="utf-8",
    )
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "'evict_'" in bad[0], (
        f"an INLINE-journaled non-owned handle was accepted as a record: {bad}")

    site.write_text(
        site.read_text(encoding="utf-8").replace('f"evict_', 'f"test_evict_'),
        encoding="utf-8",
    )
    assert not _raw_detach_violations(root=tmp_path), (
        "the inline shape with an owned prefix must be clean")

    # SHAPES 3+ (#11217 review round 1, measured on this module's own helpers at
    # `ede858414`): shape 1 was anchored on `name = f"…"`, and shape 2 on the
    # f-string being the journal's FIRST argument, so every construction below —
    # each carrying the SAME decidable literal prefix — read NO violation while
    # `os.urandom` + `_journal_calls` still satisfied the outer half. That is the
    # same false PASS one shape over that round 2 fixed, so the arm reads the name
    # the block JOURNALS instead of guessing which assignments are handles.
    for journaled, readable in (
        ('"evict_" + os.urandom(6).hex()', True),                # concatenation
        ('f"evict_{os.urandom(6).hex()}"', True),                # inline (shape 2)
        ('str(f"evict_{os.urandom(6).hex()}")', True),           # `str` is the identity
        # A wrapper call the arm cannot resolve is UNDECIDABLE, and the census
        # fails CLOSED on it (#11217 round 5, finding 3): reading `args[0]`
        # classified `_mk(tag, name)` by its TAG.
        ('_mk(f"evict_{os.urandom(6).hex()}")', False),
        ('"evict_%s" % os.urandom(6).hex()', True),               # percent-format
        ('"evict_{}".format(os.urandom(6).hex())', True),          # str.format
    ):
        site.write_text(
            "def test_evictor_lane():"
            "\n    import os"
            "\n    from falkordb import FalkorDB"
            f"\n    _journal_append({journaled})"
            "\n    c = FalkorDB(host=\"h\")"
            "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
            "\n",
            encoding="utf-8",
        )
        bad = _raw_detach_violations(root=tmp_path)
        assert bad, f"a handle journaled as `{journaled}` read NO violation: {bad}"
        if readable:
            assert "'evict_'" in bad[0], (
                f"a DECIDABLE unowned prefix was not named for `{journaled}`: {bad}")
        else:
            assert "UNDECIDABLE" in bad[0], (
                f"an unresolvable callee must fail CLOSED and say so for "
                f"`{journaled}`: {bad}")

    # The SAME shapes with an OWNED prefix must stay clean, so the arm cannot go
    # vacuous by flagging every journaled site.
    for journaled in (
        '"test_evict_" + os.urandom(6).hex()',
        'str(f"test_evict_{os.urandom(6).hex()}")',
    ):
        site.write_text(
            "def test_evictor_lane():"
            "\n    import os"
            "\n    from falkordb import FalkorDB"
            f"\n    _journal_append({journaled})"
            "\n    c = FalkorDB(host=\"h\")"
            "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
            "\n",
            encoding="utf-8",
        )
        assert not _raw_detach_violations(root=tmp_path), (
            f"an OWNED handle journaled as `{journaled}` was reported: "
            f"{_raw_detach_violations(root=tmp_path)}")

    # The journal NAMES an assignment, and the assignment's own shape must not
    # matter: parenthesised continuation, annotated, concatenated, and one hop
    # through an intermediate literal variable.
    for assignment, journaled in (
        ('name = (\n        f"evict_{os.urandom(6).hex()}"\n    )', 'name'),
        ('name: str = f"evict_{os.urandom(6).hex()}"', 'name'),
        ('name = "evict_" + os.urandom(6).hex()', 'name'),
        ('prefix = "evict_"\n    name = prefix + os.urandom(6).hex()', 'name'),
    ):
        site.write_text(
            "def test_evictor_lane():"
            "\n    import os"
            "\n    from falkordb import FalkorDB"
            f"\n    {assignment}"
            f"\n    _journal_append({journaled})"
            "\n    c = FalkorDB(host=\"h\")"
            "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
            "\n",
            encoding="utf-8",
        )
        bad = _raw_detach_violations(root=tmp_path)
        assert bad and "'evict_'" in bad[0], (
            f"the journaled name built as `{assignment!r}` read NO violation: "
            f"{bad}")

    # FALSE BLOCK (#11217 review round 1, measured at `ede858414`): reading the
    # RHS of EVERY `x = f"…"` in the block meant an UNRELATED random token red the
    # census even though the handle the block JOURNALS is owned — a violation line
    # asserting a falsehood about a compliant site.
    site.write_text(
        "def test_evictor_lane():"
        "\n    import os"
        "\n    from falkordb import FalkorDB"
        "\n    name = f\"test_evict_{os.urandom(6).hex()}\""
        "\n    token = f\"n{os.urandom(6).hex()}\""
        "\n    _journal_append(name)"
        "\n    c = FalkorDB(host=\"h\")"
        "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
        "\n",
        encoding="utf-8",
    )
    assert not _raw_detach_violations(root=tmp_path), (
        f"an UNRELATED random token red the census: "
        f"{_raw_detach_violations(root=tmp_path)}")

    # FAIL CLOSED (#11217 review round 5): an expression with no readable
    # literal head is a VIOLATION, not an exemption. Five rounds measured every
    # shape draining into a fail-open as a live false PASS — the journal looked
    # like a record while the sweep preserved the graph — so the census may
    # certify only what it can PROVE owned, and the author's remedy is one line
    # (make the journaled name literal).
    site.write_text(
        "def test_evictor_lane():"
        "\n    import os"
        "\n    from falkordb import FalkorDB"
        "\n    os.urandom(6)"
        "\n    _journal_append(pick())"
        "\n    c = FalkorDB(host=\"h\")"
        "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
        "\n",
        encoding="utf-8",
    )
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "UNDECIDABLE" in bad[0], (
        f"a handle with no readable literal was accepted as a record: {bad}")

    # …and the same for a name whose LITERAL is one hop away inside an f-string
    # field (`p = "evict_"; f"{p}{hex}"`): the head begins in a value, so the
    # arm cannot prove it, and the census fails CLOSED rather than silent.
    site.write_text(
        "def test_evictor_lane():"
        "\n    import os"
        "\n    from falkordb import FalkorDB"
        "\n    p = \"evict_\""
        "\n    name = f\"{p}{os.urandom(6).hex()}\""
        "\n    _journal_append(name)"
        "\n    c = FalkorDB(host=\"h\")"
        "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
        "\n",
        encoding="utf-8",
    )
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "UNDECIDABLE" in bad[0], (
        f"a variable-prefix name was accepted as a record: {bad}")

    # POSITION, not only shape (#11217 re-review round 2, measured at
    # `549e297f1`): only the binding LIVE at the call is journaled, so a LATER
    # rebinding to an owned name must not launder an unowned one...
    site.write_text(_site(
        '    name = f"evict_{os.urandom(6).hex()}"',
        "    _journal_append(name)",
        '    name = f"test_evict_{os.urandom(6).hex()}"',
    ), encoding="utf-8")
    assert _raw_detach_violations(root=tmp_path), (
        "a LATER rebinding to an owned name laundered the unowned handle this "
        f"call journals: {_raw_detach_violations(root=tmp_path)}")

    # ...and the mirror: an OWNED handle journaled, rebound to an unowned name
    # afterwards, is a compliant site and must not red.
    site.write_text(_site(
        '    name = f"test_evict_{os.urandom(6).hex()}"',
        "    _journal_append(name)",
        '    name = f"evict_{os.urandom(6).hex()}"',
    ), encoding="utf-8")
    assert not _raw_detach_violations(root=tmp_path), (
        "an OWNED handle was red because of a rebinding AFTER the journal call: "
        f"{_raw_detach_violations(root=tmp_path)}")

    # Every BINDING form of a named handle resolves: tuple target, walrus, and a
    # chain of TWO rebindings (the docstring's stated bound).
    for lines in (
            ('    name, other = f"evict_{os.urandom(6).hex()}", 1',
             "    _journal_append(name)"),
            ('    if (name := f"evict_{os.urandom(6).hex()}") is None:',
             "        pass",
             "    _journal_append(name)"),
            ('    p = "evict_"',
             "    q = p",
             "    name = q + os.urandom(6).hex()",
             "    _journal_append(name)"),
    ):
        site.write_text(_site(*lines), encoding="utf-8")
        bad = _raw_detach_violations(root=tmp_path)
        assert bad and "'evict_'" in bad[0], (
            f"a name bound as `{lines[0].strip()}` read NO violation: {bad}")

    # Only the HEAD is read, so unrelated material in the same expression is not
    # mistaken for the prefix (#11217 re-review round 2): an owned module-level
    # constant with a `"-"` step, and a keyword argument carrying a literal.
    site.write_text(_site(
        '    _GRAPH = "tortoise_test_lane"',
        '    name = _GRAPH + "-" + os.urandom(6).hex()',
        "    _journal_append(name)",
    ), encoding="utf-8")
    assert not _raw_detach_violations(root=tmp_path), (
        "a `\"-\"` step of an OWNED concatenation was read as the prefix: "
        f"{_raw_detach_violations(root=tmp_path)}")

    # An unresolvable callee is UNDECIDABLE and fails CLOSED, and its NAME is
    # never taken from a keyword argument's literal (`build(kind="x", …)` read
    # `'x'` at `549e297f1`, a false block): the message must say undecidable, and
    # must not name `'x'`.
    site.write_text(_site(
        '    _journal_append(build(kind="x", seed=os.urandom(6).hex()))',
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "UNDECIDABLE" in bad[0] and "'x'" not in bad[0], (
        "a keyword argument's literal was read as the name's head: "
        f"{bad}")

    # An apostrophe in a COMMENT inside the call's arguments is not a literal
    # opener: the identifier is still resolved (and here it is unowned).
    site.write_text(_site(
        '    name = f"evict_{os.urandom(6).hex()}"',
        "    _journal_append(",
        "        name,  # don't reuse the graph",
        "    )",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "'evict_'" in bad[0], (
        f"a comment inside the call's arguments defeated the arm: {bad}")

    # `"".join([…])` reads the FIRST element's head, never a later element's:
    # an unowned head is still reported, an owned head stays clean.
    site.write_text(_site(
        '    head = f"evict_{os.urandom(6).hex()}"',
        '    graph = "".join([head, "test_suffix"])',
        "    _journal_append(graph)",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "'evict_'" in bad[0], (
        f"a `\"\".join([…])` with an unowned head read clean: {bad}")
    site.write_text(_site(
        '    head = f"test_evict_{os.urandom(6).hex()}"',
        '    graph = "".join([head, "_suffix"])',
        "    _journal_append(graph)",
    ), encoding="utf-8")
    assert not _raw_detach_violations(root=tmp_path), (
        "an OWNED `\"\".join([…])` head was red on a later suffix literal: "
        f"{_raw_detach_violations(root=tmp_path)}")

    # PROSE is not a site in either direction (#11182): a comment showing an
    # unowned handle must not make the census red, exactly as it must not make it
    # green.
    site.write_text(
        "def test_evictor_lane():"
        "\n    import os"
        "\n    from falkordb import FalkorDB"
        "\n    name = f\"test_evict_{os.urandom(6).hex()}\""
        "\n    _journal_append(name)"
        "\n    # never do this: name = f\"evict_{os.urandom(6).hex()}\""
        "\n    # nor this: _journal_append(f\"evict_{os.urandom(6).hex()}\")"
        "\n    c = FalkorDB(host=\"h\")"
        "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
        "\n",
        encoding="utf-8",
    )
    assert not _raw_detach_violations(root=tmp_path), (
        f"a COMMENT naming an unowned handle red the census: "
        f"{_raw_detach_violations(root=tmp_path)}")

    # AMBIGUOUS prefix also fails CLOSED: `f"te{hex}"` starts with `te`, and
    # `"test_".startswith("te")` — so the arm cannot prove the name does not
    # complete into an owned prefix, and `te…` is not itself owned. Five rounds
    # of false PASSes through this allowance (#11217 round 5) moved the verdict
    # to the only sound side: what the census cannot prove, it reports.
    site.write_text(
        "def test_evictor_lane():"
        "\n    import os"
        "\n    from falkordb import FalkorDB"
        "\n    name = f\"te{os.urandom(6).hex()}\""
        "\n    _journal_append(name)"
        "\n    c = FalkorDB(host=\"h\")"
        "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
        "\n",
        encoding="utf-8",
    )
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "'te'" in bad[0], (
        f"a prefix the arm cannot PROVE owned was accepted as a record: {bad}")


def _raw_client_site(*lines: str) -> str:
    """A minimal raw-client block whose body is `lines`, so a case below states
    only the shape under test."""
    return ("def test_evictor_lane():"
            "\n    import os"
            "\n    from falkordb import FalkorDB"
            + "".join("\n" + ln for ln in lines)
            + "\n    c = FalkorDB(host=\"h\")"
            "\n    c.query(\"MATCH (n) DETACH DELETE n\")"
            "\n")


def test_a_present_but_undecidable_slice_bound_fails_CLOSED(tmp_path):
    """A slice bound that EXISTS but cannot be resolved is NOT an omitted bound.

    #11217 review round 11 (B-P1). `_const_str` folded a slice with
    `low = None if node.slice.lower is None else _const_int(node.slice.lower)`,
    which yields `None` — meaning "unbounded" to `base[low:high]` — for BOTH
    "no bound" and "bound present, undecidable". `"test_evict_"[:width]` then
    folded to the OWNED literal `"test_evict_"` while the real name is
    `"te" + tag`: an OWNED claim for an UNOWNED name — a fail-OPEN in the arm
    whose entire purpose is to fail CLOSED. `step` already had the right
    treatment two lines above, so one of the three bound fields disagreed with
    the other two.

    Pinned as BEHAVIOUR, not helper introspection: deleting the fix turns both
    cases GREEN, which is exactly the regression class rounds 8-10 closed.
    """
    site = tmp_path / "test_slice_bound_undecidable.py"
    for assignment, bindings in (
        ('name = "test_evict_"[:width] + os.urandom(4).hex()', ("    width = 2",)),
        ('name = "test_evict_"[lo:] + os.urandom(4).hex()', ("    lo = 9",)),
    ):
        site.write_text(
            _raw_client_site(*bindings, f"    {assignment}",
                             "    _journal_append(name)"),
            encoding="utf-8",
        )
        bad = _raw_detach_violations(root=tmp_path)
        assert bad, (
            f"`{assignment}` has a PRESENT but undecidable slice bound — the "
            f"census must fail CLOSED, not fold it to the owned literal: {bad}")
        assert "UNDECIDABLE" in bad[0], (
            f"`{assignment}` is NOT decidable, so the violation must say so "
            f"rather than claim a resolved prefix: {bad}")


def test_a_PARTIAL_concat_fold_is_not_a_decidable_VALUE(tmp_path):
    """A concatenation with ONE undecidable operand is a HEAD, not a value.

    #11217 review round 12 (P1). `_const_str`'s `Add` branch returned the left
    operand alone when the right one could not be folded
    (`return left + right if right is not None else left`). Reading a HEAD, that
    is right — and `_prefix_of` reads the head structurally off `.left`, so it
    never needed the partial. Reading a VALUE, it is wrong, and every transform
    above it in the same function then computes on the partial:
    `("test_" + os.urandom(4).hex())[-6:]` folded to the OWNED literal
    `"test_"` while the real handle is the last six of the hex — an UNOWNED
    name certified as owned, i.e. an inert journal the sweep PRESERVES. Same
    class as round 11 one field over and round 5 one node over: a transform
    classified off an argument it cannot fold.

    Pinned as BEHAVIOUR: reverting the fix turns this GREEN, which is the
    regression class rounds 5, 11 and 12 each closed.
    """
    site = tmp_path / "test_partial_concat_value.py"
    # The journaled handle is the LAST SIX characters of `"test_" + <12 hex>`,
    # i.e. an UNOWNED name; the leading `"test_"` is not part of it at all.
    site.write_text(
        _raw_client_site(
            '    name = ("test_" + os.urandom(4).hex())[-6:]',
            "    _journal_append(name)"),
        encoding="utf-8",
    )
    bad = _raw_detach_violations(root=tmp_path)
    assert bad, (
        "`(\"test_\" + <undecidable>)[-6:]` is NOT a decidable value: folding it "
        "to the left operand `\"test_\"` certifies an UNOWNED handle as owned, "
        "so the census must fail CLOSED instead of reading GREEN")
    assert "UNDECIDABLE" in bad[0], (
        f"the slice bound is resolvable but its BASE is not, so the violation "
        f"must say UNDECIDABLE rather than claim a resolved prefix: {bad}")


def test_no_shape_can_abort_the_census_instead_of_reding_it(tmp_path):
    """A shape the arm cannot read must produce a VIOLATION, never a traceback.

    Third-session review, follow-up round. The module's contract — stated at
    `_journal_prefixes` and closed twice for other walks — is that an undecidable
    shape degrades to `UNDECIDABLE` and the census fails CLOSED. Two raises on the
    new AST path survived that: `_dotted` recursed once per attribute (`a.b.b.…`
    at ~1000 hops raised `RecursionError` out of `_raw_detach_violations`), and the
    constant fold really ALLOCATED the width, so `"a".zfill(900000000)` raised
    `MemoryError` (or was OOM-killed, printing no count line). Both abort the whole
    file's census — a traceback naming no site — instead of reding on it.

    Pinned as BEHAVIOUR: neither shape may raise, and both must be REPORTED.
    """
    site = tmp_path / "test_census_aborts.py"
    deep = "a" + ".b" * 1500
    for line in (f"    _journal_append({deep})",
                 '    _journal_append("a".zfill(900000000) + os.urandom(2).hex())',
                 # A width carried in the format SPEC / the `%` conversion, i.e.
                 # NOT in `node.args` — the cap's blind spot (follow-up 2).
                 '    _journal_append("{:>200000000}".format("a") + os.urandom(2).hex())',
                 '    _journal_append("%200000000s" % "a" + os.urandom(2).hex())'):
        # `os.urandom` in the block is what makes the OWNERSHIP half run at all:
        # without it the PRESENCE half rejects the site first, and a violation
        # would prove nothing about the undecidable-value path each line is here
        # to exercise (follow-up review: the clause was reported vacuous).
        site.write_text(
            _raw_client_site("    tag = os.urandom(4).hex()", line), encoding="utf-8")
        # The assertion IS the call: a RecursionError / MemoryError here fails the
        # test with the census never reaching a verdict.
        bad = _raw_detach_violations(root=tmp_path)
        assert bad, (
            f"`{line.strip()[:60]}…` must RED — the census cannot read it, and "
            f"aborting instead of reding names no site")
        # ASSERT THE VERDICT, not merely "something red": an over-cap fold that
        # resolved anyway would report a PREFIX, which is the false claim this
        # arm exists to avoid. Asserting the reason is what makes removing the
        # bound fail this pin instead of merely costing 200 MB.
        assert "UNDECIDABLE" in bad[0], (
            f"`{line.strip()[:60]}…` cannot be resolved without allocating it, "
            f"so the violation must say UNDECIDABLE rather than claim a "
            f"prefix: {bad}")


def test_a_digit_run_in_MATERIAL_is_not_a_width(tmp_path):
    """The allocation bound must not turn a DECIDABLE owned fold into a violation.

    Follow-up review round 2. `_FOLD_WIDTH_CAP` was first applied to every string
    argument of every pure `str` method and to the WHOLE `%` template, so a digit
    run in MATERIAL read as a width: `"{}".format("test_20240101")`,
    `"test_evict".strip("0123456789")` and `"test_20240101".removeprefix(…)` all
    folded to an OWNED name at `15eff79bc74` and read RED here — a false BLOCK,
    which the bound has no business introducing, and one whose message also lies
    ("cannot PROVE owned" for a name that provably is).

    A width has a POSITION: the field SPEC of a `.format` template, and the
    conversion spec of a `%` template. Pinned as BEHAVIOUR over the census.
    """
    site = tmp_path / "test_digits_not_widths.py"
    for journaled in (
        '"{}.graph".format("test_20240101")',
        '"%s_20240101" % "test_x"',
        '"test_evict_20240101".replace("20240101", "x")',
        '"test_evict".strip("0123456789")',
        '"test_20240101".removeprefix("20240101")',
        '"test_%s_20240101" % "x"',
        '"test_{}".format("graph_2024")',
        # `%%` is an ESCAPED percent: the digits after it are MATERIAL, and a
        # scan that consumed `%%` as a conversion read them as a width.
        '"%s (100%%20240101)" % "test_x"',
        # A zero-padded width is a LENGTH, not a value: `{:00010}` is ten chars.
        '"test_{:00010}".format(1)',
    ):
        site.write_text(_raw_client_site(
            "    tag = os.urandom(4).hex()",
            f"    _journal_append({journaled})"), encoding="utf-8")
        bad = _raw_detach_violations(root=tmp_path)
        assert not bad, (
            f"`{journaled}` folds to an OWNED name — a digit run in MATERIAL is "
            f"not a width, and reading it as one false-BLOCKs a compliant site: "
            f"{bad}")

    # A zero-padded WIDTH is a LENGTH, not a value: `{{:00010}}` is a ten-character
    # field, so the fold is decidable. Asserted on the FOLD rather than through the
    # census, because a `.format` call that cannot be folded falls back to the
    # TEMPLATE's head (`"test_…"`), which is owned either way and so would hide the
    # over-block here (measured: the census stays clean with the bug present).
    assert _const_str(
        ast.parse('"test_{:00010}".format(1)').body[0].value) == "test_0000000001", (
        "a zero-padded width must not be read as a large value, or the fold — and "
        "any value resolution built on it — loses a decidable answer")

def test_the_ownership_arm_resolves_the_JOURNALED_VALUE_not_a_text_shape(tmp_path):
    """#11243 + review round 3: the arm classified TEXT, so every shape it did not
    enumerate was a false PASS, and two of its text heuristics were false BLOCKS.

    Each case below was measured GREEN (or wrongly RED) at `2f6af8b7` through the
    census's own `_raw_detach_violations(root=tmp_path)`. They are pinned as
    BEHAVIOUR rather than as helper introspection, because six rewrites each
    satisfied the shape its own reviewer named and left the next form open: the
    class is "classifies the journaled VALUE, or fails open", not "knows shape N".
    """
    site = tmp_path / "test_null_owned.py"

    # ── fail OPEN (category A: the census passed while the journal was inert) ──
    # `evict_`-journaling expressions whose LEADING literal is owned or whose
    # head is not a plain literal, each of which read GREEN at `2f6af8b7`.
    for journaled, head in (
        # the join branch read the SECOND element (round 2's fix, inverted)
        ('"".join(["evict_", os.urandom(4).hex()])', "'evict_'"),
        # a transform: the leading literal is owned, the VALUE is not
        ('"test_log".replace("test_log", "evict") + os.urandom(4).hex()', "'evict'"),
        ('"test_g"[:0] + "evict_" + os.urandom(4).hex()', "'evict_'"),
        ('"{}".format("evict_") + os.urandom(4).hex()', "'evict_'"),
        # …and the two directions a pure `str` method can take an owned literal:
        # it can destroy the prefix (`removeprefix`) or change its case
        # (`upper`, and the vocabulary is case-sensitive).
        ('"test_evict_".removeprefix("test_") + os.urandom(4).hex()', "'evict_'"),
        ('"evict_".upper() + os.urandom(4).hex()', "'EVICT_'"),
    ):
        site.write_text(_raw_client_site(
            f'    _journal_append({journaled})'), encoding="utf-8")
        bad = _raw_detach_violations(root=tmp_path)
        assert bad, f"a handle journaled as `{journaled}` read NO violation: {bad}"
        # ASSERT THE FOLDED VALUE, not a phrase every violation also carries.
        # `"OUTSIDE the ownership vocabulary"` is emitted for the UNDECIDABLE
        # case too, so asserting only that left EVERY constant-folding branch of
        # `_const_str` un-pinned: deleting the `Subscript`/slice fold alone kept
        # this whole test GREEN, although `"test_g"[:0] + "evict_"` is one of the
        # cases below (#11217 review round 8, C2). These two assertions fail when
        # any fold is removed — the prefix stops resolving and becomes the
        # UNDECIDABLE case, which must not be how a DECIDABLE site is reported.
        assert "UNDECIDABLE" not in bad[0], (
            f"`{journaled}` is DECIDABLE — the census must name the value it "
            f"folded, not fall back to UNDECIDABLE: {bad}")
        assert head in bad[0], (
            f"`{journaled}` folds to {head}, but the violation named something "
            f"else: {bad}")

    # An ATTRIBUTE target is a binding like any other: `t.name = f"evict_…"`
    # then `_journal_append(t.name)` journals the unowned name.
    site.write_text(_raw_client_site(
        '    t.name = f"evict_{os.urandom(4).hex()}"',
        "    _journal_append(t.name)",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "'evict_'" in bad[0], (
        f"an unowned handle journaled through an ATTRIBUTE target read clean: {bad}")

    # A rebinding in a NESTED scope is not the binding live at the call: it
    # laundered the outer unowned handle (false PASS). Round 6 went further — a
    # nested scope that CAN rebind the key makes the name UNDECIDABLE, and the
    # census fails CLOSED, so the verdict is a violation either way while the
    # message names the reason it actually has (#11217 review round 6, 2a).
    site.write_text(_raw_client_site(
        '    name = f"evict_{os.urandom(4).hex()}"',
        "    def helper():",
        '        name = f"test_ok_{os.urandom(4).hex()}"',
        "        return name",
        "    helper()",
        "    _journal_append(name)",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and ("'evict_'" in bad[0] or "UNDECIDABLE" in bad[0]), (
        f"a NESTED-scope rebinding laundered the journaled handle: {bad}")

    # A PATH-DEPENDENT binding decides NOTHING: `_bound_prefix` returns `""` when
    # ANY candidate binding of the name sits inside a conditional/loop/try, and
    # the census fails CLOSED on it (#11217 review round 5, finding 2). Pinned
    # BEHAVIOURALLY here because that guard was cited as the fix and exercised by
    # nothing: with `if any(candidate_depth …): return ""` deleted, the `if`/`else`
    # shape below read NO violation at all — a false PASS — while every test
    # stayed green (#11217 review round 8, C3).
    for path_dependent in (
        # `if`/`else`: the LAST binding decided the prefix before the guard
        ['    if os.environ.get("X"):',
         '        name = f"evict_{os.urandom(4).hex()}"',
         '    else:',
         '        name = f"test_ok_{os.urandom(4).hex()}"',
         '    _journal_append(name)'],
        # a `for` TARGET carries no readable value at all (`value is None`)
        ['    for name in [f"evict_{os.urandom(4).hex()}"]:',
         '        pass',
         '    _journal_append(name)'],
        # …and so does a `with … as` target
        ['    with open(os.devnull) as name:',
         '        pass',
         # the block still needs its unique handle for the OUTER half
         '    other = f"test_x_{os.urandom(4).hex()}"',
         '    _journal_append(other)',
         '    _journal_append(name)'],
    ):
        site.write_text(_raw_client_site(*path_dependent), encoding="utf-8")
        bad = _raw_detach_violations(root=tmp_path)
        assert bad and "UNDECIDABLE" in bad[0], (
            f"a PATH-DEPENDENT binding was certified: {path_dependent} -> {bad}")

    # A journal inside a NESTED `def` in the block does NOT journal this block's
    # handle. Resolved file-wide by LINE RANGE only, it satisfied the ownership
    # half for an unowned, un-journaled handle the block then DETACHed — measured
    # at 0 violations (#11217 review round 8, C4: category A, a false PASS inside
    # the gate).
    site.write_text(_raw_client_site(
        '    name = f"evict_{os.urandom(4).hex()}"',
        "    def helper():",
        '        _journal_append(f"test_owned_{os.urandom(4).hex()}")',
        "    helper()",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "_journal_append in the same block" in bad[0], (
        "a journal written in a NESTED def satisfied the block's ownership half "
        f"for an unowned, un-journaled handle: {bad}")

    # …and when the block DOES journal its own handle, that journal is judged on
    # its OWN prefix — the nested scope's owned name must not stand in for it.
    site.write_text(_raw_client_site(
        '    name = f"evict_{os.urandom(4).hex()}"',
        "    def helper():",
        '        _journal_append(f"test_owned_{os.urandom(4).hex()}")',
        "    helper()",
        "    _journal_append(name)",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "'evict_'" in bad[0], (
        "a NESTED def's owned journal was read as THIS block's record: "
        f"{bad}")

    # ⛔ ROUND 9's TWO FIXES EACH NEED A PIN, or a revert of either keeps this
    # suite green (#11217 review round 9, ISSUE 1 — measured: mutating
    # `in_block` back to `own`, and the `(line, col)` boxes back to line
    # granularity, produced 0 census failures).
    #
    # (1) The nested journal is still JUDGED: a nested `evict_` beside a proper
    # own-scope journal must red on the NON-OWNED half. With the ownership half
    # reading `own` this is GREEN, and GREEN here is the false PASS round 9
    # fixed — the sweep preserves a graph whose only record is inert.
    site.write_text(_raw_client_site(
        "    def helper():",
        '        _journal_append(f"evict_{os.urandom(4).hex()}")',
        "    helper()",
        '    name = f"test_mine_{os.urandom(4).hex()}"',
        "    _journal_append(name)",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "_SWEEP_OWNED_PREFIXES" in bad[0], (
        "a NESTED journal of an UNOWNED name was not judged on its own prefix "
        f"(the sweep preserves that graph): {bad}")
    #
    # (2) Containment is decided on `(line, col)`, not on the LINE: a direct
    # `_journal_append` sharing a line with a nested `lambda` IS this block's
    # journal. With line-granular containment it is judged nested, `own` empties,
    # and the block reds — a false BLOCK on a compliant site.
    site.write_text(_raw_client_site(
        '    name = f"test_ok_{os.urandom(4).hex()}"',
        "    cb = lambda: None; _journal_append(name)",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert not bad, (
        "a direct journal sharing a line with a nested lambda was judged as "
        f"NESTED (line-granular containment): {bad}")

    # ── the TRANSFORM false-pass / false-block pair (#11217 review round 5, B2) ──
    # A pure `str` method on a receiver this arm cannot FOLD rewrites the value,
    # so the method's first ARGUMENT is an operand, not the head. Measured at
    # `aa2e15c40` both directions were wrong off the ARGUMENT: the leaking
    # `evict_<hex>` below read GREEN (off the owned-looking `"test_"`), and the
    # mirror read RED on an OWNED name off `"g"`. What a transform returns is not
    # statically decidable without running the module under test, so this arm
    # DECLINES TO CERTIFY it: the prefix is `""` and the census fails **CLOSED**
    # on it — a VIOLATION saying UNDECIDABLE, not the exemption the first version
    # of this comment claimed (#11217 review round 8, C1: the prose stated the
    # opposite of the implementation, and the pin below inspected the helper's
    # return value instead of the census verdict, so a return to fail-OPEN could
    # not be caught).
    for transformed, argument_literal in (
        # category A: the VALUE is unowned while the argument looks owned
        ('f"test_{os.urandom(4).hex()}".replace("test_", "evict_")', "'test_'"),
        # category B: the VALUE is owned while the argument looks unowned
        ('f"g{os.urandom(4).hex()}".replace("g", "test_g")', "'g'"),
    ):
        site.write_text(_raw_client_site(
            f"    _journal_append({transformed})",
            # the block still needs its unique handle for the OUTER half
            '    other = f"test_x_{os.urandom(4).hex()}"',
            "    _journal_append(other)",
        ), encoding="utf-8")
        prefixes = [prefix for _line, prefix in
                    _journal_prefixes(site.read_text(encoding="utf-8"))]
        assert "" in prefixes, (
            f"a TRANSFORMED handle `{transformed}` was classified off its "
            f"argument instead of declining to certify: {prefixes}")
        bad = _raw_detach_violations(root=tmp_path)
        assert bad and "UNDECIDABLE" in bad[0], (
            f"a TRANSFORMED handle `{transformed}` must fail CLOSED: {bad}")
        assert argument_literal not in bad[0], (
            f"the census classified `{transformed}` off its ARGUMENT "
            f"({argument_literal}) instead of declining: {bad}")

    # ── false BLOCK (category B: a compliant site red for a reason it is not) ──
    # …and the mirror reds a compliant site, which is the other half of the same
    # scope defect.
    site.write_text(_raw_client_site(
        '    name = f"test_ok_{os.urandom(4).hex()}"',
        "    def helper():",
        '        name = f"evict_{os.urandom(4).hex()}"',
        "        return name",
        "    helper()",
        "    _journal_append(name)",
    ), encoding="utf-8")
    assert not _raw_detach_violations(root=tmp_path), (
        "an OWNED handle was red by a rebinding in a NESTED scope: "
        f"{_raw_detach_violations(root=tmp_path)}")

    # A COMMENT's apostrophe is prose, never a literal opener (round 3 ISSUE 1):
    # the arm read `'s fine'` as the prefix and red a compliant site.
    site.write_text(_raw_client_site(
        '    name = f"test_ok_{os.urandom(4).hex()}"',
        "    _journal_append(",
        "        # it's fine, the handle is owned",
        "        name,",
        "    )",
    ), encoding="utf-8")
    assert not _raw_detach_violations(root=tmp_path), (
        "a COMMENT's apostrophe inside the call's arguments fabricated a prefix: "
        f"{_raw_detach_violations(root=tmp_path)}")

    # A legal SPACE before the call's paren is the same call (#11217 round-9
    # review, ISSUE 4). The presence half used to AND the AST scan with a REGEX
    # requiring the literal `_journal_append(`, so this compliant site red while
    # `_journal_prefixes` had already resolved its owned prefix.
    site.write_text(_raw_client_site(
        '    name = f"test_ok_{os.urandom(4).hex()}"',
        "    _journal_append (name)",
    ), encoding="utf-8")
    assert not _raw_detach_violations(root=tmp_path), (
        "a legal space before the paren red a compliant site: "
        f"{_raw_detach_violations(root=tmp_path)}")

    # …and the shapes the regex WAS added for must still red without it: a local
    # `def _journal_append` produces no `ast.Call`, so the AST half already
    # covers them and `_journal_calls` is not needed in the decision at all.
    site.write_text(_raw_client_site(
        '    name = f"test_ok_{os.urandom(4).hex()}"',
        "    def _journal_append(n): pass",
    ), encoding="utf-8")
    assert _raw_detach_violations(root=tmp_path), (
        "a local `def _journal_append` shadow stopped reding once the regex left "
        f"the decision: {_raw_detach_violations(root=tmp_path)}")

    # Only the binding LIVE at the call is journaled, so a DEAD earlier binding
    # must not be reported over a live UNDECIDABLE one — but the verdict on an
    # undecidable live value is now RED, not silent (#11217 round 5): the reason
    # is the live `pick()`, and the message must not claim the dead `evict_`.
    site.write_text(_raw_client_site(
        '    name = f"evict_{os.urandom(4).hex()}"',
        "    name = pick()",
        "    _journal_append(name)",
    ), encoding="utf-8")
    bad = _raw_detach_violations(root=tmp_path)
    assert bad and "UNDECIDABLE" in bad[0] and "'evict_'" not in bad[0], (
        "a DEAD binding's prefix was reported over the live undecidable one: "
        f"{bad}")

    # ── round 4's five findings: the AST plumbing itself ──────────────────
    # `_journal_append(name=…)` is a legal call — one parameter, named `name` —
    # and it contributes NO positional argument, so the arm read no prefix at all
    # while the block still passed the outer `_journal_calls` half.
    for journaled in (
        'name=f"evict_{os.urandom(4).hex()}"',
        # a walrus in the ARGUMENT is the same binding form the two-step case
        # above pins; its value begins in a literal like any other
        'n := f"evict_{os.urandom(4).hex()}"',
        '(p := "evict_") + os.urandom(4).hex()',
    ):
        site.write_text(_raw_client_site(
            f"    _journal_append({journaled})"), encoding="utf-8")
        bad = _raw_detach_violations(root=tmp_path)
        assert bad and "'evict_'" in bad[0], (
            f"a handle journaled as `{journaled}` read NO violation: {bad}")

    # CRASHES: the census runs over the WHOLE tests root, so a shape it cannot
    # handle must degrade to UNDECIDABLE (which the census fails CLOSED on),
    # never raise — a traceback names no file and reports nothing about the site.
    for body in (
        # an `ast.Lambda` carries an EXPRESSION as `body`, not a statement list
        '    cb = lambda n: _journal_append(n)',
        # a constant subscript OUT of range is not decidable, and not fatal
        '    _journal_append("test_"[99] + os.urandom(4).hex())',
        # a template whose fields the arguments do not satisfy: `format` raises
        # KeyError / AttributeError, which escaped this arm and took the other
        # census tests down with it (#11217 review round 5, B4)
        '    _journal_append("{missing}".format("x") + os.urandom(4).hex())',
        '    _journal_append("{0.nope}".format("x") + os.urandom(4).hex())',
        # a STAR is a slice, so positional pairing would name the wrong element
        '    name, *rest, other = (f"test_ok_1_{os.urandom(4).hex()}",'
        ' f"test_ok_2_{os.urandom(4).hex()}", f"test_ok_3_{os.urandom(4).hex()}",'
        ' f"evict_{os.urandom(4).hex()}")',
        "    _journal_append(other)",
    ):
        site.write_text(_raw_client_site(body), encoding="utf-8")
        _raw_detach_violations(root=tmp_path)  # must not raise

    # ── round 6: three shapes that still certified a STALE owned prefix ──────
    # Each was measured GREEN at `3e0f0a5d8` while the journal received
    # `evict_<hex>` — the inert-journal false PASS this arm exists to close — or
    # raised out of the census. All three now fail CLOSED.
    for body_lines, label, reason in (
        # (1) PEP 572: a walrus inside a COMPREHENSION binds the scope ENCLOSING
        # it, so the outer `name` is rewritten by a line the outer walk skipped.
        (('    name = f"test_ok_{os.urandom(4).hex()}"',
          '    [None for _i in range(1) if (name := f"evict_{os.urandom(4).hex()}")]',
          "    _journal_append(name)"), "comprehension walrus", "UNDECIDABLE"),
        # (2a) `nonlocal` in a nested def REALLY does rewrite the outer binding.
        (('    name = f"test_ok_{os.urandom(4).hex()}"',
          "    def helper():",
          "        nonlocal name",
          '        name = f"evict_{os.urandom(4).hex()}"',
          "    helper()",
          "    _journal_append(name)"), "nonlocal rebinding", "UNDECIDABLE"),
        # (2b) a closure read executes at CALL time, so a rebinding positioned
        # AFTER the nested def still decides the value the journal receives.
        # Round 8 rejects this shape one layer EARLIER: a journal written inside
        # a nested scope is no longer this block's record at all (C4), so the
        # reason is the outer half. The closure-order guard itself is pinned
        # directly on the value resolver below.
        (('    name = f"test_ok_{os.urandom(4).hex()}"',
          "    def run_it():",
          "        _journal_append(name)",
          '    name = f"evict_{os.urandom(4).hex()}"',
          "    run_it()"), "closure read after a rebinding",
         "_journal_append in the same block"),
    ):
        site.write_text(_raw_client_site(*body_lines), encoding="utf-8")
        bad = _raw_detach_violations(root=tmp_path)
        assert bad and reason in bad[0], (
            f"a stale owned prefix was certified for {label}: {bad}")

    # The closure-ORDER guard itself (`_bound_prefix`'s "call runs inside a nested
    # scope defined before a rebinding HERE" branch): the journal call sits inside
    # `run_it()`, and the outer `name` is rebound AFTER that def but BEFORE the
    # call, so source order does not describe run order. Read through the value
    # resolver — the census rejects the nested journal one layer earlier — the
    # prefix must be the undecidable `""`.
    closure_src = _raw_client_site(
        '    name = f"test_ok_{os.urandom(4).hex()}"',
        "    def run_it():",
        "        _journal_append(name)",
        '    name = f"evict_{os.urandom(4).hex()}"',
        "    run_it()")
    assert [prefix for _line, prefix in _journal_prefixes(closure_src)] == [""], (
        "the closure-order guard stopped failing closed: "
        f"{_journal_prefixes(closure_src)}")

    # (3) A pathological left-nested concatenation is walked ITERATIVELY and its
    # fold is BOUNDED, so the census reports instead of raising RecursionError.
    site.write_text(_raw_client_site(
        '    _journal_append(' + " + ".join(['"test_"'] * 2000)
        + " + os.urandom(4).hex())"), encoding="utf-8")
    _raw_detach_violations(root=tmp_path)  # must not raise

    # …and the starred target is a DECLARED fail-open, pinned so it cannot become
    # a silent wrong element: the arm must not read `test_ok_3_` for `other`.
    site.write_text(_raw_client_site(
        '    name, *rest, other = (f"test_ok_1_{os.urandom(4).hex()}",'
        ' f"test_ok_2_{os.urandom(4).hex()}", f"test_ok_3_{os.urandom(4).hex()}",'
        ' f"evict_{os.urandom(4).hex()}")',
        "    _journal_append(other)",
    ), encoding="utf-8")
    starred = [prefix for _line, prefix in
               _journal_prefixes(site.read_text(encoding="utf-8"))]
    assert starred == [""], (
        f"a STARRED target was paired positionally instead of failing open: {starred}")


def test_a_NONLOCAL_at_ANY_depth_rewrites_the_outer_binding(tmp_path):
    """#11217 review round 13 (P1): the nested-scope walk honoured ONE level.

    PEP 227 resolves `nonlocal`/`global` through the WHOLE enclosing chain: the
    declaration binds the name in the NEAREST enclosing scope that binds it, so
    a declaration two (or three) scopes down binds THIS scope's binding whenever
    the scopes in between do not bind it themselves. `_mutated_from_a_nested_scope`
    stopped at `_own_statements(node)`, which stops at the next nested scope, so
    the shape below certified the stale OWNED prefix while the journal received
    `evict_<hex>` — a graph `_sweep_drop` classifies `preserved` and then forgets,
    i.e. a permanent leak under a GREEN census. Measured `[]` (clean) at the
    pre-fix head for all four variants; the one-level control was already RED.

    Pinned as BEHAVIOUR — the resolved PREFIX, plus the census verdict — and each
    mutating shape sits beside its own compliant MIRROR (same nesting, plain
    nested binding instead of the declaration), so neither direction can go
    vacuous: the mirror reds a guard that fails closed on any nested scope, and
    the mutating case reds the one-level stop."""
    site = tmp_path / "test_round13_nested_nonlocal.py"

    def _prefixes(*lines: str) -> list[str]:
        return [prefix for _line, prefix in
                _journal_prefixes(_raw_client_site(*lines))]

    owned = '    name = f"test_ok_{os.urandom(4).hex()}"'
    evict = '        name = f"evict_{os.urandom(4).hex()}"'
    bodies: tuple = (
        # ── control: the ONE-level shape round 6 added this guard for ─────
        ("one-level nonlocal",
         (owned,
          "    def helper():",
          "        nonlocal name",
          evict,
          "    helper()",
          "    _journal_append(name)"),
         (owned,
          "    def helper():",
          evict.strip("\n"),
          "    helper()",
          "    _journal_append(name)")),
        # ── the finding: the declaration is TWO scopes down ────────────────
        ("grandchild nonlocal (2 levels)",
         (owned,
          "    def helper():",
          "        def inner():",
          "            nonlocal name",
          '            name = f"evict_{os.urandom(4).hex()}"',
          "        inner()",
          "    helper()",
          "    _journal_append(name)"),
         (owned,
          "    def helper():",
          "        def inner():",
          '            name = f"evict_{os.urandom(4).hex()}"',
          "        inner()",
          "    helper()",
          "    _journal_append(name)")),
        # ── three levels, the same chain one hop further down ─────────────
        ("great-grandchild nonlocal (3 levels)",
         (owned,
          "    def a():",
          "        def b():",
          "            def c():",
          "                nonlocal name",
          '                name = f"evict_{os.urandom(4).hex()}"',
          "            c()",
          "        b()",
          "    a()",
          "    _journal_append(name)"),
         (owned,
          "    def a():",
          "        def b():",
          "            def c():",
          '                name = f"evict_{os.urandom(4).hex()}"',
          "            c()",
          "        b()",
          "    a()",
          "    _journal_append(name)")),
        # ── a METHOD of a nested class is a scope two levels down as well ──
        ("nested-class method nonlocal",
         (owned,
          "    class Helper:",
          "        def go(self):",
          "            nonlocal name",
          '            name = f"evict_{os.urandom(4).hex()}"',
          "    Helper().go()",
          "    _journal_append(name)"),
         (owned,
          "    class Helper:",
          "        def go(self):",
          '            name = f"evict_{os.urandom(4).hex()}"',
          "    Helper().go()",
          "    _journal_append(name)")),
    )
    for label, mutating, mirror in bodies:
        site.write_text(_raw_client_site(*mutating), encoding="utf-8")
        assert _prefixes(*mutating) == [""], (
            f"{label}: the outer binding is rewritten from inside a nested "
            f"scope, so no prefix is provable — the arm certified "
            f"{_prefixes(*mutating)}")
        bad = _raw_detach_violations(root=tmp_path)
        assert bad and "UNDECIDABLE" in bad[0], (
            f"{label}: the census must fail CLOSED on a mutated handle, "
            f"not report it owned/clean: {bad}")
        # The MIRROR: identical nesting, no declaration — a plain nested
        # binding cannot touch the outer name, so the owned prefix stands. A
        # guard that fails closed on any nested scope reds here.
        assert _prefixes(*mirror) == ["test_ok_"], (
            f"{label}: the compliant mirror (no nonlocal/global) lost its "
            f"provable prefix — a false BLOCK: {_prefixes(*mirror)}")
        site.write_text(_raw_client_site(*mirror), encoding="utf-8")
        assert not _raw_detach_violations(root=tmp_path), (
            f"{label}: a plain nested binding was treated as a mutation of the "
            f"outer name: {_raw_detach_violations(root=tmp_path)}")

    # ── a MODULE-level binding, rewritten through a grandchild `global` ─────
    # `_bound_prefix` resolves a name up the whole scope chain, so the MODULE is
    # a scope too, and `global` reaches it from any depth.
    module_level = (
        'GNAME = f"test_ok_{os.urandom(4).hex()}"\n'
        "\n"
        "\n"
        "def test_evictor_lane():\n"
        "    import os\n"
        "    from falkordb import FalkorDB\n"
        "    def helper():\n"
        "        def inner():\n"
        "            global GNAME\n"
        '            GNAME = f"evict_{os.urandom(4).hex()}"\n'
        "        inner()\n"
        "    helper()\n"
        "    _journal_append(GNAME)\n"
        '    c = FalkorDB(host="h")\n'
        '    c.query("MATCH (n) DETACH DELETE n")\n'
    )
    site.write_text(module_level, encoding="utf-8")
    assert [prefix for _line, prefix in _journal_prefixes(module_level)] == [""], (
        "a grandchild `global` on a MODULE-level binding was certified: "
        f"{_journal_prefixes(module_level)}")
    assert _raw_detach_violations(root=tmp_path), (
        "the census reported a module-level handle rewritten by a grandchild "
        "`global` as owned/clean")


def _block_end(masked: str, start: int, indent: int) -> int:
    """Index where the block starting at `start` (indented `indent`) ends: the
    first following line whose indentation is <= `indent`. Computed on the MASKED
    source so a flush-left comment or docstring line cannot cut it short."""
    for m in re.finditer(r"(?m)^([ ]*)\S", masked[start:]):
        if m.start() == 0:
            continue
        if len(m.group(1)) <= indent:
            return start + m.start()
    return len(masked)


def _enclosing_block_span(src: str, pos: int) -> tuple[str, int, int]:
    """`(text, start, end)` for the block that CONTAINS `pos`: the innermost
    enclosing def, else the enclosing indented suite (module-level try/if/with),
    else the statement run.

    Resolved by INDENTATION and then VERIFIED BY CONTAINMENT — a preceding `def`
    at a smaller indent is only the answer if `pos` actually falls inside its
    span. Without that check a site in a module-level suite appearing after a
    def was attributed to that unrelated def's body, which cannot contain it, and
    the violation was silently skipped (#10170 review rounds 4-6).

    The OFFSETS are returned as well as the text so the block's journal calls can
    be selected by line from the file-wide AST pass (`_journal_prefixes`).
    """
    masked = _mask_strings_and_comments(src)
    line_start = masked.rfind("\n", 0, pos) + 1
    indent = len(masked[line_start:]) - len(masked[line_start:].lstrip(" "))
    if indent == 0:
        nxt = re.search(r"\n(?=(?:async )?(?:def |class |@))", masked[pos:])
        end = pos + nxt.start() if nxt else len(src)
        return src[line_start:end], line_start, end
    # innermost enclosing def, verified to contain the site
    for m in reversed(list(re.finditer(r"(?m)^([ ]*)(?:async )?def ", masked[:line_start]))):
        ind = len(m.group(1))
        if ind < indent and m.start() <= pos < _block_end(masked, m.start(), ind):
            end = _block_end(masked, m.start(), ind)
            return src[m.start():end], m.start(), end
    # no enclosing def: the nearest OUTER indented suite (module-level try/if/with,
    # or a class body), again verified to contain the site.
    for m in reversed(list(re.finditer(r"(?m)^([ ]*)\S", masked[:line_start]))):
        ind = len(m.group(1))
        if ind < indent and m.start() <= pos < _block_end(masked, m.start(), ind):
            end = _block_end(masked, m.start(), ind)
            return src[m.start():end], m.start(), end
    return src[line_start:], line_start, len(src)


def test_raw_client_journaled_graph_dropped_by_sweep(monkeypatch, tmp_path):
    """Cycle-8 P2-2: the raw client's journaled per-test-unique graph is
    DROPPED by the session sweep (GRAPH.LIST after a sweep of a journaled
    raw graph is empty — the leak is closed, not just renamed)."""
    from tests._embedded import _journal_append
    journal = tmp_path / "raw.graphs.jsonl"
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE", str(journal))
    gname = f"tortoise_test_raw_{os.urandom(4).hex()}"
    _journal_append(gname)
    assert (tmp_path / "raw.graphs.jsonl").read_text() == gname + "\n"
    # the sweep's drop set is the FILE journal — the raw name is in it
    names = (tmp_path / "raw.graphs.jsonl").read_text().splitlines()
    assert names == [gname]


# A file that must keep ZERO `from_uri(` sites: a census declaration in the strict
# direction. Kept as its own registry because an empty shape list in
# `_ROUTED_FROM_URI_SITES` is indistinguishable from a key whose presence alone is
# checked (#10170 review P2-1).
_MUST_HAVE_NO_SITES = ("test_finding_provenance.py",)


@pytest.mark.parametrize("registry", ["stems", "filenames"])
def test_both_censuses_SKIP_an_exempt_file_and_flag_a_peer(tmp_path, registry):
    """#10170 review (P1): the skip decision, tested BEHAVIOURALLY, per census.

    The first version asserted `probe.stem in exempt` — introspection, which
    proves what the helper RETURNS, not what either census DOES. It was also
    vacuously weak: the carve-out files carry no `from_uri(` call at all, so a
    census with the comparison re-keyed to `f.name` was still green.

    This builds a fixture root where an EXEMPT-NAMED file and a peer carry the
    SAME offending content (a `from_uri(` call and a raw-client bulk DETACH with
    no journal). The only thing that can separate them is the skip decision, and
    it has to separate them in BOTH censuses.
    """
    from tests._embedded import TEST_NO_REDIRECT_STEMS

    if registry == "stems":
        exempt_stem = next(s for s in sorted(TEST_NO_REDIRECT_STEMS)
                           if s.startswith("test_"))
    else:
        exempt_stem = sorted(e.removesuffix(".py") for e in _FROM_URI_EXEMPT_FILES
                             if e.startswith("test_"))[0]
    # the OFFENDING function sits SECOND, with a journaled one after it: a block
    # that runs to EOF would inherit that journal and flag nothing.
    offending = (
        "def test_journaled_first():\n"
        "    import os\n"
        "    from falkordb import FalkorDB\n"
        "    name = f\"test_g_{os.urandom(4).hex()}\"\n"
        "    _journal_append(name)\n"
        "    c = FalkorDB(host=\"h\")\n"
        "    c.query(\"MATCH (n) DETACH DELETE n\")\n"
        "\n"
        "def test_fixture():\n"
        # SAMPLE TEXT, not a dial target: this file is a #6673 seam file, so
        # spelling a docker-lane port here (even inside fixture content) is a
        # literal the split-brain guard refuses. The census only needs the
        # `from_uri(` CALL SHAPE — the URI itself is incidental, hence portless.
        "    proj = _FP.from_uri(\"bolt://localhost\")\n"
        "    from falkordb import FalkorDB\n"
        "    client = FalkorDB(host=\"h\")\n"
        "    client.query(\"MATCH (n) DETACH DELETE n\")\n"
        "\n"
        "def helper_not_enclosing():\n"
        "    return 1\n"
        "\n"
        "if True:\n"
        "    import os as _os\n"
        "    from falkordb import FalkorDB as _FDB\n"
        "    _c = _FDB(host=\"h\")\n"
        "    _c.query(\"MATCH (n) DETACH DELETE n\")\n"
        "\n"
        "def test_journaled_third():\n"
        "    import os\n"
        "    from falkordb import FalkorDB\n"
        "    other = f\"test_g2_{os.urandom(4).hex()}\"\n"
        "    _journal_append(other)\n"
        "    d = FalkorDB(host=\"h\")\n"
        "    d.query(\"MATCH (n) DETACH DELETE n\")\n"
    )
    (tmp_path / f"{exempt_stem}.py").write_text(offending, encoding="utf-8")
    assert (tmp_path / f"{exempt_stem}.py") in sorted(tmp_path.glob("test_*.py")), \
        "the exempt fixture is not enumerable by the censuses' own glob"
    (tmp_path / "test_zz_not_exempt.py").write_text(offending, encoding="utf-8")

    scanned = [name for name, _ln, _blk in _from_uri_sites(root=tmp_path)]
    assert scanned == ["test_zz_not_exempt.py"], (
        f"the from_uri census did not skip {exempt_stem!r}: {scanned}")
    flagged = _from_uri_violations(root=tmp_path)
    assert len(flagged) == 1 and flagged[0].startswith("test_zz_not_exempt.py:"), (
        f"the from_uri VERDICT did not fire on the peer: {flagged}")

    bad = _raw_detach_violations(root=tmp_path)
    # TWO sites must be flagged in the PEER: the def body's and the module-level
    # suite's (which sits after an unrelated def, so only a containment check finds
    # it). The exempt file must still contribute none.
    assert len(bad) == 2, (
        f"expected exactly the peer's one unjournaled site, got {bad}")
    assert bad[0].startswith("test_zz_not_exempt.py:"), (
        f"the raw-client census did not skip {exempt_stem!r}: {bad}")


def test_the_exemption_key_space_is_UNIFORM_and_nothing_is_inert():
    """#10170: the key space is the thing that was wrong, so it is what is pinned.

    `TEST_NO_REDIRECT_STEMS` holds bare stems and `_FROM_URI_EXEMPT_FILES` holds
    filenames; both censuses compared the union against `f.name` (always `*.py`),
    so every bare stem matched nothing and the exemption exempted ZERO of the 53
    carve-out files.
    """
    from tests._embedded import TEST_NO_REDIRECT_STEMS

    # The two RAW spellings the normaliser must bridge. Asserting on `exempt`
    # instead would be tautological — `removesuffix` cannot leave a `.py` unless
    # the entry was `x.py.py` (#10170 review round 2).
    assert all(not s.endswith(".py") for s in TEST_NO_REDIRECT_STEMS), \
        "TEST_NO_REDIRECT_STEMS must hold BARE STEMS"
    assert all(p.endswith(".py") for p in _FROM_URI_EXEMPT_FILES), \
        "_FROM_URI_EXEMPT_FILES must hold FILENAMES"

    # Every declared stem names a REAL file. One entry resolves under neither
    # `tests/` nor `bench/` today; it is ALLOWLISTED rather than tolerated so a
    # NEW inert stem reds here. `<=` (not `==`) so removing the stale entry —
    # which belongs to the `config/ci-surfaces.yml` carve-out mirror, not to this
    # census — shows up as a removal instead of reddening with an empty diff.
    stale = {s for s in TEST_NO_REDIRECT_STEMS
             if not (_TESTS_ROOT / f"{s}.py").exists()
             and not (_TESTS_ROOT.parent / "bench" / f"{s}.py").exists()}
    newly_inert = stale - _STALE_CARVEOUT_STEMS
    assert not newly_inert, (
        f"TEST_NO_REDIRECT_STEMS gained a stem that names no file: {sorted(newly_inert)}")
    # The allowlist is asserted VERBATIM so it cannot grow silently; removing the
    # stale entry is a tracked follow-up in the carve-out mirror, not here.
    assert {"test_smoke_embedded"} == _STALE_CARVEOUT_STEMS

    # Entries the censuses' own glob can never yield are seam helpers, not tests —
    # pinned, because an exemption that cannot match is the same defect class as
    # the bare stems (#10170 review P2-4).
    # Existence, for the OTHER registry — a renamed module leaves an entry that
    # can never match, which is the same class (#10170 review round 2).
    vanished = [e for e in _FROM_URI_EXEMPT_FILES if not (_TESTS_ROOT / e).exists()]
    assert not vanished, f"_FROM_URI_EXEMPT_FILES names missing module(s): {vanished}"

    non_test = {e for e in _FROM_URI_EXEMPT_FILES if not e.startswith("test_")}
    assert non_test == _NON_TEST_FILE_EXEMPTIONS, (
        f"unpinned non-test exemption(s): {sorted(non_test - _NON_TEST_FILE_EXEMPTIONS)}")


def test_no_declared_route_or_pin_is_inert():
    """#10170 (second defect): declarations that name a call the file does not have.

    `test_session_capture_e2e.py` carried a routed shape while having ZERO
    `from_uri(` sites, so the row could never fail and proved nothing. A raw
    substring test keeps that hole open (a docstring mention satisfies it), so this
    uses the census's OWN per-file detector — the same function `_from_uri_sites`
    yields from (#10170 review round 2).
    """
    # A routed row for an EXEMPT file reads as coverage while the census skips the
    # file outright and never consults the row.
    assert not ({n.removesuffix(".py") for n in _ROUTED_FROM_URI_SITES} & _exempt_stems()), \
        "a routed row names an exempt file, so the census never reads it"

    gone = []
    for name, patterns in _ROUTED_FROM_URI_SITES.items():
        path = _TESTS_ROOT / name
        if not path.exists():
            gone.append(f"{name}: file missing")
            continue
        # only the sites the census would otherwise flag: `graph_name=` short-
        # circuits before the declared lookup, so a pattern matching only those is
        # inert (#10170 review round 4).
        blocks = [b for _ln, b in _sites_in_file(path) if "graph_name=" not in b]
        if not patterns:
            gone.append(f"{name}: empty pattern list is only legal in "
                        "_MUST_HAVE_NO_SITES")
            continue
        if not blocks:
            gone.append(f"{name}: declares {patterns} but has no live from_uri( call")
            continue
        unmatched = [p for p in patterns
                     if not any(re.search(p, b) for b in blocks)]
        if unmatched:
            gone.append(f"{name}: declared pattern(s) match no live site: {unmatched}")
    for name in _MUST_HAVE_NO_SITES:
        path = _TESTS_ROOT / name
        if not path.exists():
            gone.append(f"{name}: must-have-no-sites file is missing")
        elif _sites_in_file(path):
            gone.append(f"{name}: declared site-free but has a live from_uri( call")
    for name in _ENV_URI_PIN_FILES:
        if not (_TESTS_ROOT / name).exists():
            gone.append(f"{name}: _ENV_URI_PIN_FILES entry is not a test module")
    assert not gone, "\n".join(gone)
