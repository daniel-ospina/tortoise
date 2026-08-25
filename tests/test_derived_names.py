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

_TESTS_ROOT = Path(__file__).resolve().parent


def _docker_reachable(host: str = "localhost", port: int = 6379) -> bool:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


@pytest.fixture
def uri_env(monkeypatch):
    if not _docker_reachable():
        pytest.skip("live FalkorDB (localhost:6379) not reachable")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
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
    "test_session_capture_e2e.py": [r"from_uri\(os\.environ\[.TORTOISE_DB_URI.\]\)"],
    "test_ingest.py": [
        # module availability probe (env pre-set to a test-prefixed URI)
        r"from_uri\(os\.environ\[.TORTOISE_DB_URI.\]\)",
        # per-test test-prefixed path via _live_uri(test_ingest125_<uuid>)
        r"from_uri\(\s*uri\s*\)",
    ],
    # CLI routing unit — from_uri is stubbed by the test (never connects)
    "test_pipeline_cli.py": [r"from_uri\("],
    # invalid-scheme raise tests (raise BEFORE any connection)
    "test_projection.py": [r"from_uri\(\"postgresql://", r"from_uri\(\"localhost:6379"],
    # __init__-stubbed parse asserts (fake_init captures kwargs, never connects)
    "test_sdk_props_coercion.py": [r'from_uri\(\s*"rediss://', r'from_uri\(\s*"docker://'],
    # raw-client migration test — per-test tortoise_test_r2_migrate_<uuid> path
    "test_search_engine.py": [
        # module availability probe (env pre-set to a test-prefixed URI)
        r"from_uri\(\s*os\.environ\[.TORTOISE_DB_URI.\]",
        r'from_uri\(\s*"docker://:@localhost:16379/" \+ gname\)',
    ],
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


def _from_uri_sites():
    """Yield (file_name, line_number, call_block) for every from_uri( call
    (FalkorProjection or any module-level alias like _FP — review P2: an
    aliased construction must not evade the census) in a migrated file."""
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    exempt = _FROM_URI_EXEMPT_FILES | set(TEST_NO_REDIRECT_STEMS)
    call_re = re.compile(r"\b(\w+)\.from_uri\s*\(")
    for f in sorted(_TESTS_ROOT.glob("test_*.py")):
        if f.name in exempt:
            continue
        src = f.read_text(encoding="utf-8")
        for m in call_re.finditer(src):
            # skip matches inside comments/docstrings (the call-block is
            # empty or the line is a comment)
            line_no = src.count("\n", 0, m.start()) + 1
            line = src.split("\n")[line_no - 1].strip()
            if line.startswith("#"):
                continue
            start = m.end()
            depth = 1
            i = start
            while i < len(src) and depth:
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                i += 1
            block = src[m.start():i]
            if not block.strip().rstrip(")").strip():
                continue  # comment-shaped reference (from_uri() with no args)
            yield f.name, line_no, " ".join(block.split())


def test_from_uri_sites_resolve_test_prefixed():
    """Cycle-5 P1-6 census: every no-graph_name from_uri( call in a migrated
    file either passes graph_name=, carries a literal test-prefixed URI path,
    or is declared in _ROUTED_FROM_URI_SITES. A new no-graph-name site that
    could bulk-DETACH the shared env-URI graph reds (the cross-file DETACH
    clobber regression: test_search_engine's bulk DETACH destroyed
    test_projection's live writes pre-fix)."""
    import urllib.parse
    violations = []
    for fname, lineno, block in _from_uri_sites():
        if "graph_name=" in block:
            continue  # explicit per-test graph
        # literal first argument → parse the URI path
        m = re.match(r"\w+\.from_uri\(\s*\"([^\"]+)\"", block)
        if m:
            path = urllib.parse.urlparse(m.group(1)).path.lstrip("/")
            if path.startswith(("test_", "tortoise_test")):
                continue
        declared = _ROUTED_FROM_URI_SITES.get(fname, [])
        if any(re.search(p, block) for p in declared):
            continue
        violations.append(f"{fname}:{lineno}: {block[:110]}")
    assert not violations, (
        "un-declared no-graph_name from_uri site(s) — pass an explicit "
        "graph_name=test_<file>_<uuid> or declare in _ROUTED_FROM_URI_SITES:\n"
        + "\n".join(violations))


def test_env_uri_assignment_uses_live_uri_helper():
    """Review P2: the declared pattern `from_uri(uri)` for test_ingest is a
    loose net — a NEW `uri = os.environ.get(\"TORTOISE_DB_URI\")` + a
    no-graph_name from_uri(uri) in that file would match the declaration and
    pass without being a per-test graph. Pin the invariant instead: every
    `uri = os.environ...` assignment in test_ingest must route through
    _live_uri (the per-test test-prefixed path helper)."""
    import re as _re
    src = (_TESTS_ROOT / "test_ingest.py").read_text(encoding="utf-8")
    bad = [ln for ln, line in enumerate(src.split("\n"), 1)
           if _re.search(r"(?<!_)\buri\s*=\s*os\.environ\.get\(\s*[\"']TORTOISE_DB_URI",
                         line)]
    assert not bad, (
        f"test_ingest: uri assignment from the raw env at L{bad} — must "
        f"route through _live_uri(test_..._<uuid>) (cycle-5 P1-6 per-test "
        f"path; the env URI path is the shared job path)")


def test_from_uri_routing_table_keys_exist():
    for fname in _ROUTED_FROM_URI_SITES:
        assert (_TESTS_ROOT / fname).exists(), \
            f"_ROUTED_FROM_URI_SITES key {fname!r} is not a test module"


# ── raw falkordb-client census (cycle-6 P1-3 / cycle-8 P2-2) ────────────────


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
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    exempt = _FROM_URI_EXEMPT_FILES | set(TEST_NO_REDIRECT_STEMS)
    violations = []
    for f in sorted(_TESTS_ROOT.glob("test_*.py")):
        if f.name in exempt:
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
    assert not violations, "\n".join(violations)


def _enclosing_block(src: str, pos: int) -> str:
    """The nearest enclosing def block containing `pos` (review P1: the
    per-site raw-client scan needs the site's block, not the module)."""
    head = src[:pos]
    last_def = head.rfind("\ndef ")
    last_def = max(head.rfind("\ndef "), head.rfind("\n    def "))
    if last_def < 0:
        return head  # module-level site — the whole head is the block
    return src[last_def:]


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
