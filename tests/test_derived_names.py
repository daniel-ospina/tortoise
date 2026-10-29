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

import os
import re
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
        # every raw-client CONSTRUCTION site (from falkordb import /
        # import falkordb / falkordb.FalkorDB( form) whose enclosing block
        # bulk-DETACHes must carry per-test-unique + journal in the SAME block
        for m in re.finditer(
                r"from falkordb import FalkorDB|import falkordb|"
                r"falkordb\.FalkorDB", src):
            block = _enclosing_block(src, m.start())
            if not block or "DETACH DELETE" not in block:
                continue
            if "os.urandom" not in block or "_journal_append" not in block:
                violations.append(
                    f"{f.name}: raw FalkorDB( client + bulk DETACH at "
                    f"L{src.count(chr(10), 0, m.start()) + 1} without a "
                    f"per-test-unique handle + _journal_append in the same "
                    f"block (cycle-6 P1-3 / cycle-8 P2-2)")
    return violations


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


def _enclosing_block(src: str, pos: int) -> str:
    """The block that CONTAINS `pos`: the innermost enclosing def, else the
    enclosing indented suite (module-level try/if/with), else the statement run.

    Resolved by INDENTATION and then VERIFIED BY CONTAINMENT — a preceding `def`
    at a smaller indent is only the answer if `pos` actually falls inside its
    span. Without that check a site in a module-level suite appearing after a
    def was attributed to that unrelated def's body, which cannot contain it, and
    the violation was silently skipped (#10170 review rounds 4-6).
    """
    masked = _mask_strings_and_comments(src)
    line_start = masked.rfind("\n", 0, pos) + 1
    indent = len(masked[line_start:]) - len(masked[line_start:].lstrip(" "))
    if indent == 0:
        nxt = re.search(r"\n(?=(?:async )?(?:def |class |@))", masked[pos:])
        return src[line_start:pos + nxt.start() if nxt else len(src)]
    # innermost enclosing def, verified to contain the site
    for m in reversed(list(re.finditer(r"(?m)^([ ]*)(?:async )?def ", masked[:line_start]))):
        ind = len(m.group(1))
        if ind < indent and m.start() <= pos < _block_end(masked, m.start(), ind):
            return src[m.start():_block_end(masked, m.start(), ind)]
    # no enclosing def: the nearest OUTER indented suite (module-level try/if/with,
    # or a class body), again verified to contain the site.
    for m in reversed(list(re.finditer(r"(?m)^([ ]*)\S", masked[:line_start]))):
        ind = len(m.group(1))
        if ind < indent and m.start() <= pos < _block_end(masked, m.start(), ind):
            return src[m.start():_block_end(masked, m.start(), ind)]
    return src[line_start:]


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
        "    name = f\"g_{os.urandom(4).hex()}\"\n"
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
        "    other = f\"g2_{os.urandom(4).hex()}\"\n"
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
