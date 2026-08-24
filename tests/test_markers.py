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
    "test_agent_signup.py": {"registry": "prod-coupled"},
    "test_billing.py": {"registry": "prod-coupled"},
    "test_cli_serve.py": {"registry": "prod-coupled"},
    "test_commit_endpoint.py": {"registry": "prod-coupled"},
    "test_dr_endpoints.py": {"registry": "prod-coupled"},
    "test_export_delete.py": {"registry": "prod-coupled"},
    "test_hosted_api.py": {"registry": "prod-coupled",
                           "team-002": "team-identity"},
    "test_index_mcp.py": {"registry": "prod-coupled",
                           "e2e-900": "redirect-derived per-path"},
    "test_invites_email_http.py": {"registry": "prod-coupled"},
    "test_invites_http.py": {"registry": "prod-coupled"},
    "test_mcp_http.py": {"registry": "prod-coupled"},
    "test_metering.py": {"registry": "prod-coupled"},
    "test_namespace_uri_mode.py": {"registry": "assertion",
                                   "team-abc123": "assertion"},
    "test_onboarding_integration.py": {"registry": "prod-coupled"},
    "test_pack_state.py": {
        "tenant-a": "team-identity", "tenant-b": "team-identity",
        "team-k": "team-identity", "t-reg-2": "team-identity",
        "mcp-team": "team-identity", "t-bf-1": "team-identity",
    },
    "test_quota.py": {"registry": "prod-coupled"},
    "test_sdk_legacy_coverage.py": {"team-beta": "assertion"},
    "test_session_key_http.py": {"registry": "prod-coupled"},
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
    },
    "test_writer_inventory.py": {
        '"team_myapp"': "endpoint-constrained",  # seed write — backup dumps teams.graph_name
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
