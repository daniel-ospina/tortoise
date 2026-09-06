# tests/test_markers.py — epic #1647 Task 7 SKELETON
"""Epic #1647 Task 7 Step 5 skeleton: the namespace/select_graph census
guards + routing tables.

Task 5 (#1667) APPENDS the marker-semantics tests (embedded_only marker skip
+ stem-registry tests) to this file — do not move the guards; the marker
tests append below.

Guards (the census's executable form — cycle-5 P1-5 / cycle-6 P2-10/P2-15):
  test_namespace_literals_guard_passing_or_routed
      grep every namespace="..." literal inside TortoiseSDK(/_make_sdk(/
      sdk_factory( call blocks in NON-allowlist (migrated) test files; each
      must be guard-passing (test_/tortoise_test/test- — the hyphenated
      family is SDK-normalized, sdk.py) OR listed in ROUTED_NAMESPACES.
      A new non-test literal in a migrated file reds.
  test_select_graph_literals_guard_passing_or_routed
      grep every select_graph("team_*/"registry_*") literal (plain + f-string)
      in migrated files; each must be ROUTED through the per-test seam,
      DECLARED read-only/unit-mock/endpoint-constrained, or a projection-
      constructed name. A new un-routed WRITE site reds.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_TESTS_ROOT = Path(__file__).resolve().parent

# ── ROUTED_NAMESPACES (cycle-5 P1-5 + cycle-6 P2-15) ────────────────────────
# dict[file, dict[literal, disposition]]. Dispositions:
#   "prod-coupled"     — the literal is the canonical namespace PROD code
#                        resolves (quota.py/metering.py/hosted_api.py
#                        `_make_sdk(namespace="registry")`, team_graph_name).
#                        Renaming would break the seed→resolution coupling
#                        (VERIFIED this task: the rename makes the test's
#                        seed land on a verbatim test_* graph while prod
#                        resolves registry_tortoise — quota test went red).
#                        On the server lane the redirect's per-path
#                        derivation isolates these db_path-bound constructions
#                        per test (cycle-7 P2-6) — the shared-graph hazard is
#                        closed by derivation, not rename (documented
#                        divergence from the plan's blanket-rename wording).
#   "team-identity"    — the literal is a TEAM id consumed by prod flows
#                        (provision hooks, /v1/packs, backfill script, MCP
#                        handler contextvars) — the id IS the namespace; a
#                        rename breaks the contextvar/prod coupling.
#   "assertion"        — the test ASSERTS the SDK's namespace→graph mapping
#                        (test_namespace_uri_mode, test_sdk_legacy_coverage);
#                        the literal must stay byte-for-byte.
# The swept (renamed) sites are the test_-prefixed literals/constants the
# guard passes on their own — the table documents the residual declarations.
ROUTED_NAMESPACES: dict[str, dict[str, str]] = {
    # 2026-08-28 merge-reconciliation: #1785/#1816 files use the 'registry'
    # literal (session/extraction tests) — routed so the markers gate passes
    # repo-wide.
    "test_capture_session.py": {"registry": "session-capture"},
    "test_index_docs_api.py": {"registry": "index-docs"},
    "test_session_extraction_modes.py": {"registry": "session-extraction"},
    "test_agent_signup.py": {"registry": "prod-coupled"},
    "test_agent_signup_idempotency.py": {"registry": "prod-coupled"},
    "test_billing.py": {"registry": "prod-coupled"},
    "test_cli_serve.py": {"registry": "prod-coupled"},
    "test_commit_endpoint.py": {"registry": "prod-coupled"},
    "test_dr_endpoints.py": {"registry": "prod-coupled"},
    "test_export_delete.py": {"registry": "prod-coupled"},
    "test_free_team_entitlement.py": {"registry": "entitlement-gate"},   # added with ci-surfaces drift fix (#1929) — file now runs in selection
    "test_github_index_lifecycle.py": {"registry": "prod-coupled"},
    "test_hosted_auth.py": {"registry": "prod-coupled"},   # C2 #2111 TestTkPrefixAuth mirrors test_hosted_api's registry-resolve pattern
    "test_hosted_api.py": {"registry": "prod-coupled",
                           "team-002": "team-identity"},
    "test_acl_graph_users.py": {"registry": "prod-coupled"},   # C4 #2113 — team seeding via _make_sdk(namespace="registry")
    "test_delivery_tenancy.py": {"registry": "prod-coupled"},  # C6 #2115 — registry seeding in _spine_env
    "test_tenancy_spine.py": {"registry": "prod-coupled"},   # C5 #2114 — registry seeding in _spine_env
    "test_hosted_volunteer_context.py": {"registry": "prod-coupled"},   # #2103 (W4C) — registry control-plane mint/revoke mirrors test_hosted_auth
    "test_capture_phase_d_dedup.py": {"team-001": "team-identity"},  # #2104 (W5-D) — hosted _make_sdk(namespace="team-001") mirror arm
    "test_import_endpoint.py": {"registry": "import-ledger"},
    "test_index_mcp.py": {"registry": "prod-coupled",
                           "e2e-900": "redirect-derived per-path"},
    "test_invites_email_http.py": {"registry": "prod-coupled"},
    "test_invites_http.py": {"registry": "prod-coupled"},
    "test_invite_fusion_http.py": {"registry": "prod-coupled"},    # #2003 (W7): registry lane invite-fusion HTTP tests
    "test_invite_fusion_docker.py": {"registry": "prod-coupled"}, # #2003 (W7): docker-lane fusion journeys
    "test_mcp_http.py": {"registry": "prod-coupled"},
    "test_mcp_server_auth_modes.py": {"registry": "prod-coupled"},   # C2 #2111 TestTenantModeDefault tk_ resolve mirrors test_mcp_http's registry pattern
    "test_metering.py": {"registry": "prod-coupled"},
    "test_namespace_uri_mode.py": {"registry": "assertion",
                                   "team-abc123": "assertion"},
    "test_onboarding_endpoints.py": {"registry": "prod-coupled"},
    "test_onboarding_integration.py": {"registry": "prod-coupled"},
    "test_onboarding_seed_endpoint.py": {"registry": "prod-coupled"},  # #1999 (W3): seed/decide endpoint tests
    "test_onboarding_state_split.py": {"registry": "prod-coupled"},
    "test_onboarding_state.py": {"registry": "unit-only"},
    "test_pack_state.py": {
        "tenant-a": "team-identity", "tenant-b": "team-identity",
        "team-k": "team-identity", "t-reg-2": "team-identity",
        "mcp-team": "team-identity", "t-bf-1": "team-identity",
    },
    "test_quota.py": {"registry": "prod-coupled"},
    "test_sdk_legacy_coverage.py": {"team-beta": "assertion"},
    "test_writer_inventory.py": {"registry": "prod-coupled"},
    "test_ask_sdk.py": {
        "team-a": "assertion", "team-b": "assertion",
    },
    "test_session_key_http.py": {"registry": "prod-coupled"},
    "test_signup_token_revoke.py": {"registry": "prod-coupled"},
    "test_suspension_parity.py": {
        "registry": "prod-coupled", "reg-team-1": "team-identity",
    },
    "test_billing_upgrade.py": {"registry": "prod-coupled"},
    # e2e-900 (cycle-4 P2-7 / cycle-5 P1-5): the SHARED non-test team_e2e-900
    # graph of the index suite — routed by REDIRECT DERIVATION, not rename:
    # the SDK maps the literal to team_e2e-900, the redirect derives
    # test_<stem>_<hash12(session+path)> per construction — each test's fresh
    # db path yields a per-test isolated graph and same-path SDKs share (the
    # embedded analog). A per-file test_* constant was tried and REVERTED
    # (documented divergence): the redirect honors test_* verbatim, collapsing
    # every test in a file onto ONE server graph (77 URI failures).
    "test_index_surfacing.py": {"e2e-900": "redirect-derived per-path"},
    "test_backfill_sources.py": {"e2e-900": "redirect-derived per-path"},
    "test_index_restore.py": {"e2e-900": "redirect-derived per-path"},
    "test_index_directory.py": {"e2e-900": "redirect-derived per-path"},
}

# ── ROUTED_SELECT_GRAPH_SITES (cycle-6 P2-10) ───────────────────────────────
# dict[file, dict[literal-or-descriptor, disposition]]:
#   "read-only"              — the site only reads (count/assert).
#   "unit-mock"              — MagicMock/param — no live graph.
#   "endpoint-constrained"   — the WRITE target's name is production-shape by
#                              CONTRACT (the DR drill endpoint resolves
#                              team_{team_id} from the registry; the backup
#                              endpoint dumps teams.graph_name). Renaming
#                              breaks the endpoint contract (VERIFIED this
#                              task) — declared, never silent.
ROUTED_SELECT_GRAPH_SITES: dict[str, dict[str, str]] = {
    "test_dr_endpoints.py": {
        'f"team_{team_id}"': "endpoint-constrained",  # seed write — drill/backup resolve team_{id}
        '"team_team_x"': "read-only",                  # post-drill count assert
        # #2313 custom-graph drill seeds (per-graph sweep/restore E2E); the
        # server-lane _clean_team_graphs fixture drops team_* graphs per test
        '"team_team_x_g_c1"': "endpoint-constrained",  # custom drill seed write
        '"team_team_x_g_x"': "endpoint-constrained",   # custom drill seed write
    },
    "test_writer_inventory.py": {
        '"team_myapp"': "endpoint-constrained",  # seed write — backup dumps teams.graph_name
        # #1903 graph_name-parity sites: raw select_graph(f"team_{team_id}")
        # seed + restore probes on the test team's own graph (#2025 merge
        # freshness — main added the literals without routing; gate reds
        # otherwise, #1970 main hygiene).
        'f"team_{team_id}"': "endpoint-constrained",
    },
    "test_onboarding_state_split.py": {
        'f"team_{name}"': "endpoint-constrained",  # #2001 W5 eager-init seed probes
        'f"team_{team_id}"': "endpoint-constrained",  # #2001 W5 node read/delete probes
    },
    "test_pack_state.py": {
        "legacy_graph": "read-only",  # variable — legacy-graph PackInstall assert
    },
    "test_navigation.py": {
        "name (MagicMock param)": "unit-mock",
    },
}

# Carve-out / non-migrated files exempt from both guards.
_GUARD_EXEMPT_FILES = {
    "test_embedded_lifecycle.py",          # documented carve-out (Task 9 set)
    "test_embedded_lifecycle_fast_close.py",
    "test_flip_gate.py",                    # carve-out (RAW_EMBEDDED_ALLOWLIST)
    "test_hosted_backup.py",               # carve-out — untouched
    "_embedded.py",                        # seam helper
    "test_redirect_seam.py",               # epic seam unit surface
    "test_wipe_server.py",
    "test_round_trip_parity.py",
    "test_loopback_predicate_single_source.py",
    "test_uri_env_mutations_declared.py",  # this task's own guard file
    "test_markers.py",
    "test_derived_names.py",
}


def _sdk_call_namespace_literals():
    """Yield (file_name, line_number, literal) for every namespace="..."
    literal inside TortoiseSDK(/_make_sdk(/sdk_factory( call blocks of
    scanned (migrated) test files. Single-quoted literals are matched too
    (review P2: a namespace='team-x' must not evade); comment lines are
    skipped; f-strings/variables (runtime values) are out of scope — the
    redirect's per-path derivation covers them."""
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    exempt = _GUARD_EXEMPT_FILES | set(TEST_NO_REDIRECT_STEMS)
    call_re = re.compile(r"\b(TortoiseSDK|_make_sdk|sdk_factory)\s*\(")
    lit_re = re.compile(r"namespace\s*=\s*(['\"])([^'\"]+)\1")
    for f in sorted(_TESTS_ROOT.glob("test_*.py")) \
            + sorted((_TESTS_ROOT / "e2e").glob("test_*.py")):
        if f.name in exempt:
            continue
        src = f.read_text(encoding="utf-8")
        for m in call_re.finditer(src):
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
            nm = lit_re.search(block)
            if not nm:
                continue
            line = src.count("\n", 0, m.start()) + 1
            if src.split("\n")[line - 1].strip().startswith("#"):
                continue  # comment/docstring sample — not a construction
            yield f.name, line, nm.group(2)


def _select_graph_literals():
    """Yield (file_name, line_number, arg) for every select_graph(...) call
    whose first argument is a team_/registry_-prefixed literal or f-string in
    a scanned (migrated) test file (review P2: f-strings like
    f"team_{team_id}" must be caught — the historical write site
    test_dr_endpoints L106 is exactly that shape; an un-routed new one reds)."""
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    exempt = _GUARD_EXEMPT_FILES | set(TEST_NO_REDIRECT_STEMS)
    sg_re = re.compile(r"\.select_graph\s*\(\s*(f?)(['\"])")
    for f in sorted(_TESTS_ROOT.glob("test_*.py")) \
            + sorted((_TESTS_ROOT / "e2e").glob("test_*.py")):
        if f.name in exempt:
            continue
        src = f.read_text(encoding="utf-8")
        for m in sg_re.finditer(src):
            is_f = m.group(1) == "f"
            quote = m.group(2)
            body = src[m.end():]
            end = body.find(quote)
            if end < 0 or end > 80:
                continue  # malformed/opaque — not a static literal
            literal = body[:end]
            if literal.startswith(("team_", "registry_")):
                line = src.count("\n", 0, m.start()) + 1
                rendered = f"{quote}{literal}{quote}"
                if is_f:
                    rendered = f"f{rendered}"
                yield f.name, line, rendered


def test_namespace_literals_guard_passing_or_routed():
    """Cycle-5 P1-5: every namespace literal in a migrated file is either
    guard-passing (test_/tortoise_test/test- — the hyphenated test-* family
    is SDK-normalized, sdk.py L1115+) or declared in ROUTED_NAMESPACES. A
    new non-test literal reds — the registry's 19-file spread, e2e-900's
    5-file spread, and the team_test-* hyphenated family all trip this until
    routed."""
    violations = []
    for fname, lineno, literal in _sdk_call_namespace_literals():
        if literal.startswith(("test_", "tortoise_test", "test-")):
            continue  # guard-passing (verbatim or SDK-normalized)
        routed = ROUTED_NAMESPACES.get(fname, {}).get(literal)
        if not routed:
            violations.append(f"{fname}:{lineno}: namespace={literal!r}")
    assert not violations, (
        "un-routed non-test namespace literal(s) — rename to a test_* "
        "namespace or declare in ROUTED_NAMESPACES:\n" + "\n".join(violations))


def test_namespace_routing_table_keys_exist():
    """The routing table's file keys must resolve to real test modules."""
    for fname in ROUTED_NAMESPACES:
        assert (_TESTS_ROOT / fname).exists() \
            or (_TESTS_ROOT / "e2e" / fname).exists(), \
            f"ROUTED_NAMESPACES key {fname!r} is not a test module"


def test_select_graph_literals_guard_passing_or_routed():
    """Cycle-6 P2-10: every select_graph("team_*/registry_*") literal in a
    migrated file is ROUTED through the per-test seam, DECLARED
    read-only/unit-mock/endpoint-constrained, or a projection-constructed
    name. A new un-routed site reds — the cycle-6 P2-10 census becomes
    executable."""
    violations = []
    for fname, lineno, literal in _select_graph_literals():
        routed = ROUTED_SELECT_GRAPH_SITES.get(fname, {}).get(literal)
        if not routed:
            violations.append(f"{fname}:{lineno}: select_graph({literal})")
    assert not violations, (
        "un-routed select_graph('team_*/registry_*') literal(s) — route "
        "through the per-test seam or declare in ROUTED_SELECT_GRAPH_SITES:\n"
        + "\n".join(violations))


def test_select_graph_routing_table_keys_exist():
    for fname in ROUTED_SELECT_GRAPH_SITES:
        assert (_TESTS_ROOT / fname).exists(), \
            f"ROUTED_SELECT_GRAPH_SITES key {fname!r} is not a test module"


# ── Epic #1647 Task 5 (#1667): embedded_only marker semantics + stems ─────
# Task 5 APPENDS below the Task 7 census guards (above) — the D-2 skip
# mechanism pin + the TEST_NO_REDIRECT_STEMS registry (cycle-2 P2-9).


_REPO_ROOT = _TESTS_ROOT.parent


def test_embedded_only_marker_skips_when_uri_set(monkeypatch):
    # Cycle-5 P2-12: the D-2 skip-mechanism pin. The autouse skip is a
    # conftest hook keyed on the marker; this test drives the hook directly:
    # with URI set, a request carrying `embedded_only` must call pytest.skip
    # with the embedded-only reason (the visible-skip contract — never a
    # silent pass, and never a reason containing the "FalkorDB" substring,
    # which would trip the Task 3 skip-guard). The hook is a named helper
    # `_embedded_only_skip` in conftest so it is testable.
    import types

    from tests._embedded import _embedded_only_skip
    # Divergence from the plan's literal code (review P0): the plan imports
    # from tests.conftest — pytest loads conftest as the top-level `conftest`
    # module, so the namespace-package tests.conftest import is a SECOND
    # module instance that re-executes conftest's top-level code mid-session
    # (overwrites TORTOISE_TEST_SESSION, re-points the journal). The helper
    # lives in tests/_embedded.py — a cached module — so imports resolve
    # without re-execution.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    seen = {}
    # Divergence from the plan's literal test code: the plan accesses
    # pytest.skip.Exception AFTER monkeypatching pytest.skip with a fake —
    # the attribute no longer exists (AttributeError). Capture the exception
    # class BEFORE the patch; the fake raises the real skip exception so the
    # hook's contract (pytest.skip is the only skip path) is unchanged.
    skip_exc = pytest.skip.Exception

    def _fake_skip(reason, **kw):
        seen["reason"] = reason
        raise skip_exc(reason)

    monkeypatch.setattr(pytest, "skip", _fake_skip)
    fake_request = types.SimpleNamespace(node=types.SimpleNamespace(
        get_closest_marker=lambda name: types.SimpleNamespace()
        if name == "embedded_only" else None))
    with pytest.raises(skip_exc):
        _embedded_only_skip(fake_request)
    assert "embedded-only" in seen["reason"], \
        "skip must carry the embedded-only reason (visible-skip contract)"
    assert "FalkorDB" not in seen["reason"], \
        "reason must not contain the FalkorDB substring (Task 3 guard trip)"


def test_embedded_only_marker_inert_without_uri(monkeypatch):
    # D-2: URI unset (the embedded lane) → the marker is inert — the hook
    # returns without skipping even for a marked request.
    import types

    from tests._embedded import _embedded_only_skip
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    fake_request = types.SimpleNamespace(node=types.SimpleNamespace(
        get_closest_marker=lambda name: types.SimpleNamespace()
        if name == "embedded_only" else None))
    _embedded_only_skip(fake_request)  # must NOT raise / call pytest.skip


def test_no_redirect_stems_exist_as_modules():
    # Cycle-2 P2-9: every TEST_NO_REDIRECT_STEMS entry must resolve to a
    # real test module — a stale/typo'd stem silently fails to exempt.
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    for stem in TEST_NO_REDIRECT_STEMS:
        hit = list((_REPO_ROOT / "tests").glob(f"{stem}.py")) or \
              list((_REPO_ROOT / "tests/bench").glob(f"{stem}.py"))
        assert hit, f"TEST_NO_REDIRECT_STEMS entry {stem!r} is not a test module"


def test_no_redirect_stems_registry_exact():
    # Task 5 pin: the carve-out registry is EXACTLY the 7 plan stems — a
    # new/excised stem is a deliberate epic change (Task 9's carve-out
    # expansion updates this list), never an accidental edit. A carve-out
    # FILE missing its stem silently flips to the server lane at P2.
    # Task 9 (P3): the registry is the FULL 17-file carve-out set (cycle-3
    # P2-12 count — 7 Task-5 stems + 10 additions; fixtures/redis-guard/*
    # are subprocess scripts, not test modules, and test_smoke_embedded is
    # already one of the 7). Mirrors config/ci-surfaces.yml `carve_out:`.
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    expected = frozenset({
        "test_backup_e2e",
        "test_config",
        "test_embedded_concurrency",
        "test_embedded_lifecycle",
        "test_embedded_lifecycle_fast_close",
        "test_flip_gate",
        "test_guard",
        "test_hard_reject",
        "test_hosted_backup",
        "test_migrate_db",
        "test_ops_safety",
        "test_pre_migration_safety",
        "test_projection_lifecycle",
        "test_reaper",
        "test_reaper_orphan",
        "test_redis_guard",
        "test_smoke_embedded",
        # 2026-08-28 merge-reconciliation: #1785/#1816 added these three to
        # TEST_NO_REDIRECT_STEMS (eval/graph-integrity carve-outs) — the pin
        # test drifted; aligned here so the repo-wide markers gate passes.
        "test_graph_integrity_gate",
        "test_per_session_census",
        "test_resume_gate_parity",
        # #1928/#1944: the embedded-only eval retry + health suites (no
        # db_uri) belong in the carve-out lane — the docker fast-matrix
        # process exhausts redislite servers as the suite grows
        # (RedisLiteServerStartError).
        "test_eval_ingest_retry",
        "test_eval_resume_retry_failed",
        "test_eval_extraction_health",
    })
    assert frozenset(TEST_NO_REDIRECT_STEMS) == expected, (
        "TEST_NO_REDIRECT_STEMS drifted from the 17 plan stems: "
        f"{sorted(frozenset(TEST_NO_REDIRECT_STEMS) ^ expected)}")


def test_no_carve_out_imports_test_helpers():
    # Cycle-4 P1-4 guard: _caller_test_stem() keys on the NEAREST test_
    # frame — a carve-out file constructing through tests/test_helpers.py
    # would resolve to stem "test_helpers" (not its own exempted stem), so
    # its redirect exemption silently never fires. Assert no carve-out
    # module (a TEST_NO_REDIRECT_STEMS entry) imports it.
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    for stem in TEST_NO_REDIRECT_STEMS:
        for p in list((_REPO_ROOT / "tests").glob(f"{stem}.py")) \
                + list((_REPO_ROOT / "tests/bench").glob(f"{stem}.py")):
            src = p.read_text()
            assert "test_helpers" not in src, \
                f"carve-out {stem} imports test_helpers — loses its stem exemption (P1-4)"


def test_embedded_only_marked_tests_registered():
    # D-2=A pin: the 3 busy-error tests carry the embedded_only marker in
    # their source — a dropped/renamed mark silently runs them on the
    # server lane where busy-error semantics differ (they would fail, not
    # skip). test_audit's mark is parametrize-level: only the (d) busy case
    # skips, the CLI error-path siblings run on both lanes.
    needles = {
        "test_audit.py": "pytest.param(\"embedded_busy\", "
                         "marks=pytest.mark.embedded_only)",
        "test_pack_state.py": "@pytest.mark.embedded_only",
        "test_index_directory.py": "@pytest.mark.embedded_only",
    }
    for fname, needle in needles.items():
        src = (_TESTS_ROOT / fname).read_text(encoding="utf-8")
        assert needle in src, \
            f"{fname} lost its embedded_only mark (D-2=A)"


def test_session_token_present_and_hex12_during_docker_session(monkeypatch):
    # Cycle-4 P2-14: mid-session TEST_SESSION mutation would strand this
    # session's graphs (journal filename + derived names key off the ORIGINAL
    # value; Task 1's no-mutation probe covers drift, this covers presence/
    # shape on docker lanes). Only asserted when the URI is actually set
    # (the docker-half session shape) — embedded sessions need no token.
    # Cycle-5 P2-1: the shape is 12 hex (48 bits), was 8 hex.
    import os
    import re
    if not os.environ.get("TORTOISE_DB_URI"):
        pytest.skip("no docker session — token not required")
    assert re.fullmatch(r"[0-9a-f]{12}", os.environ.get("TORTOISE_TEST_SESSION", "")), \
        "docker session must carry TORTOISE_TEST_SESSION = 12 hex (conftest export)"


# ── Epic #1647 Task 10 (P4, plan-review P1-9): URI-required enforcement ──
def test_p4_uri_required_enforcement(monkeypatch):
    """The P4 enforcement (conftest session-start): a URI-less run fails
    UNLESS TORTOISE_TEST_CARVE_OUT=1 is set. Driven through the named helper
    in tests/_embedded.py (the session fixture is autouse and cannot be
    exercised directly; the tests.conftest import would re-execute conftest's
    top-level code). The URI gate is SUPPORTED-URI (is_db_uri) — a
    set-but-unsupported value never redirects, so it must not satisfy the
    enforcement (symmetry with the E2E-6 tripwire's EXPECT_URI handling)."""
    from tests._embedded import _assert_p4_uri_required
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_TEST_CARVE_OUT", raising=False)
    with pytest.raises(pytest.fail.Exception, match="TORTOISE_DB_URI"):
        _assert_p4_uri_required()
    # carve-out opt-in passes URI-less
    monkeypatch.setenv("TORTOISE_TEST_CARVE_OUT", "1")
    _assert_p4_uri_required()
    # a SUPPORTED URI passes even without the carve-out flag
    monkeypatch.setenv(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    _assert_p4_uri_required()
    # a set-but-UNSUPPORTED URI does NOT satisfy the enforcement (it would
    # never redirect — migrated files would run embedded)
    monkeypatch.setenv("TORTOISE_DB_URI", "postgres://x@y/z")
    monkeypatch.delenv("TORTOISE_TEST_CARVE_OUT", raising=False)
    with pytest.raises(pytest.fail.Exception, match="TORTOISE_DB_URI"):
        _assert_p4_uri_required()
    # ... but CARVE_OUT=1 still opts the operator out of the URI-less shape
    monkeypatch.setenv("TORTOISE_TEST_CARVE_OUT", "1")
    _assert_p4_uri_required()
