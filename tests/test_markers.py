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
      DECLARED read-only/unit-mock/endpoint-constrained/test-constructed, or a
      projection-constructed name. A new un-routed WRITE site reds.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_TESTS_ROOT = Path(__file__).resolve().parent

# ── ROUTED_NAMESPACES (cycle-5 P1-5 + cycle-6 P2-15) ────────────────────────
# dict[file, dict[literal, disposition]]. Dispositions:
#   "prod-coupled"     — the literal is the canonical namespace PROD code
#                        resolves (quota.py/metering.py/hosted_api.py
#                        `_make_sdk(namespace="registry")`, org_graph_name).
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
    "test_cross_tenant_read_isolation.py": {"registry": "prod-coupled"},  # #3663 — registry control-plane seeding for the cross-tenant read proof
    # #3926: the registry `Team` seed (hosted_api `_make_sdk(namespace="registry")`)
    # and the registry resolve that `mcp_server.tortoise_create_point` performs
    # internally must be the SAME graph — a test_* rename would seed a verbatim
    # test_* graph while the tool path still resolved `registry_tortoise`, so the
    # create would fail closed and the observation assertion would go red. The
    # literal IS the canonical namespace PROD code resolves (prod-coupled).
    "test_3926_error_prop_guard.py": {"registry": "prod-coupled"},
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
    "test_one_free_org_entitlement.py": {"registry": "entitlement-gate"},  # #2789/#2822: same registry-resolve seeding via _make_sdk(namespace="registry") — declared with the manifest drift fix
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
    "test_issue_4010_sessions_unlimited.py": {"registry": "prod-coupled"},  # #4010: registry seeding (org_create + registry-lane auth) mirrors test_quota/test_commit_endpoint
    "test_index_mcp.py": {"registry": "prod-coupled",
                           "e2e-900": "redirect-derived per-path"},
    "test_invites_email_http.py": {"registry": "prod-coupled"},
    "test_invites_http.py": {"registry": "prod-coupled"},
    "test_invite_fusion_http.py": {"registry": "prod-coupled"},    # #2003 (W7): registry lane invite-fusion HTTP tests
    "test_invite_fusion_docker.py": {"registry": "prod-coupled"}, # #2003 (W7): docker-lane fusion journeys
    "test_mcp_http.py": {"registry": "prod-coupled"},
    "test_mcp_server_auth_modes.py": {"registry": "prod-coupled"},   # C2 #2111 TestTenantModeDefault tk_ resolve mirrors test_mcp_http's registry pattern (the #2657 TestAskConnectedAssemblyExposure selfhost site went with the ask surface, #3849)
    "test_metering.py": {"registry": "prod-coupled"},
    "test_namespace_uri_mode.py": {"registry": "assertion",
                                   "team-abc123": "assertion"},
    "test_onboarding_endpoints.py": {"registry": "prod-coupled"},
    "test_onboarding_integration.py": {"registry": "prod-coupled"},
    # #3912 repair guard: TestGuardHelpers.test_registry_cross_check_keys_on_
    # namespace_not_display_name seeds a `Graph` row into the registry graph and
    # then calls `_has_scoped_graphs`, whose OWN body constructs
    # `TortoiseSDK(namespace="registry")` (sdk.py L1784 maps that literal to
    # `registry_tortoise`). Seed and read must therefore be the SAME graph: a
    # test_* rename would seed a verbatim test_* graph while the guard still
    # read `registry_tortoise`: the first arm then passes VACUOUSLY (the seeded
    # row is invisible, so nothing is "scoped") and the second goes red
    # (`assert False is True`) — the custom-graph row is never found. Renaming
    # breaks the coupling; the namespace IS the identity here. VERIFIED by
    # rename probe this task.
    "test_onboarding_false_completion_repair.py": {"registry": "prod-coupled"},  # #3912: registry seed read back by the guard's own TortoiseSDK(namespace="registry")
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
    "test_session_key_recovery_gate.py": {"registry": "prod-coupled"},  # #2380 — recovery-gate registry-lane parity via TortoiseSDK(namespace="registry")
    "test_signup_token_revoke.py": {"registry": "prod-coupled"},
    "test_suspension_parity.py": {
        "registry": "prod-coupled", "reg-team-1": "team-identity",
    },
    "test_billing_upgrade.py": {"registry": "prod-coupled"},
    # #2724 churn-wave hygiene (2026-09-09): the attribution/oauth registry +
    # team-identity surfaces — the namespace literal IS the identity under
    # test, so renaming it would decouple the seed from the resolution the
    # test asserts. (Files: #2600 attribution (#2599 Phase 2 for the
    # machine-model file), #524 OAuth for the mcp file.)
    "test_attribution_actor.py": {"registry": "prod-coupled",   # :171/:192/:454/:500 — db_path-pinned registry lane (TortoiseSDK at :171/:192, _make_sdk at :454/:500) mirrors hosted_api
                                  "team-strip-2600": "team-identity",   # :468 — the registry seed, the contextvar and the read-back all key off this team id
                                  "team-sweep-2600": "team-identity"},  # :530/:550 — the sweep fixture's own seeded team id is its graph
    "test_attribution_machine_model.py": {"registry": "prod-coupled"},   # :178 — _make_sdk(namespace="registry") mirrors the hosted_api registry resolve
    "test_oauth_mcp.py": {"team-free-001": "team-identity"},   # :1342 — the OAuth token's org_id IS the graph namespace the journal assert reads
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
#                              org_{org_id} from the registry; the backup
#                              endpoint dumps teams.graph_name). Renaming
#                              breaks the endpoint contract (VERIFIED this
#                              task) — declared, never silent.
#   "test-constructed"       — the test builds its OWN dedicated server graph
#                              and hands its name straight to the code under
#                              test; NOT production-shape, and no endpoint or
#                              registry resolves it — so no contract fixes the
#                              name, but it must stay CONSISTENT between the
#                              seed, the call and the read-back assert.
ROUTED_SELECT_GRAPH_SITES: dict[str, dict[str, str]] = {
    "test_dr_endpoints.py": {
        'f"org_{org_id}"': "endpoint-constrained",  # seed write — drill/backup resolve org_{id}
        # #2823 Supabase-lane sweep seed — the DATA plane stays FalkorDB in
        # both lanes; the endpoint resolves graph_name from organizations.graph_name
        'f"org_{tid}"': "endpoint-constrained",  # Supabase-lane sweep seed write
        '"org_team_x"': "read-only",                  # post-drill count assert
        # #2313 custom-graph drill seeds (per-graph sweep/restore E2E); the
        # server-lane _clean_team_graphs fixture drops team_* graphs per test
        '"team_team_x_g_c1"': "endpoint-constrained",  # custom drill seed write
        '"team_team_x_g_x"': "endpoint-constrained",   # custom drill seed write
        # #2304 tombstoned-oldest sweep E2E — custom-graph seed named
        # team_team_x_g_dead (namespace registry row kind:'custom')
        '"team_team_x_g_dead"': "endpoint-constrained",  # custom drill seed write
        # #3813 read-bound guards — these call `_restore_into_temp_verify_swap`
        # DIRECTLY (never the drill endpoint): the test seeds a raw source and
        # a raw stale target, hands the target's name to the helper as
        # `live_name`, and reads it back. Test-constructed, not
        # production-shape — nothing resolves org_swap_*/org_bound_* from a
        # registry, so `endpoint-constrained` would be false here.
        '"org_swap_source"': "test-constructed",   # seeded source for the swap
        '"org_swap_target"': "test-constructed",   # live target + read-back
        '"org_bound_source"': "test-constructed",  # seeded source (ordinary-bound guard)
        '"org_bound_target"': "test-constructed",  # live target (ordinary-bound guard)
    },
    "test_eval_ingest_cache.py": {
        'f"org_{namespace}"': "endpoint-constrained",  # #2626 regression — own-graph cleanup delete (namespace=icache-<tag>-<uuid>, docker lane)
    },
    "test_writer_inventory.py": {
        '"team_myapp"': "endpoint-constrained",  # seed write — backup dumps teams.graph_name
        # #1903 graph_name-parity sites: raw select_graph(f"org_{org_id}")
        # seed + restore probes on the test team's own graph (#2025 merge
        # freshness — main added the literals without routing; gate reds
        # otherwise, #1970 main hygiene).
        'f"org_{org_id}"': "endpoint-constrained",
    },
    "test_onboarding_state_split.py": {
        'f"org_{name}"': "endpoint-constrained",  # #2001 W5 eager-init seed probes
        'f"org_{org_id}"': "endpoint-constrained",  # #2001 W5 node read/delete probes
    },
    "test_pack_state.py": {
        "legacy_graph": "read-only",  # variable — legacy-graph PackInstall assert
    },
    # #3845 fork-slot wedge: the harness builds its OWN dedicated graphs — one
    # per round (wedge / nofork / retry) — because the defect being reproduced
    # is a GRAPH.COPY module fork, so the round must own the live destination
    # the fork child was told to copy. The names are test-constructed, not
    # production-shape: nothing outside the harness resolves them, and they
    # must stay byte-for-byte because the reap is keyed to THIS daemon's socket
    # prefix and each round's assertions read back the same graph it seeded.
    # Renaming would not break a prod contract, it would silently turn a
    # wedge round into a no-op against an empty graph.
    "test_fork_slot_wedge_3845.py": {
        '"org_wedge"': "test-constructed",    # round 1 — the graph the fork copies
        '"org_nofork"': "test-constructed",   # rounds 2-3 — control + post-copy read
        '"org_retry"': "test-constructed",    # round 4 — retry-after-reap target
    },
    "test_navigation.py": {
        "name (MagicMock param)": "unit-mock",
    },
    "test_dump_edge_asymmetry_3895.py": {
        # #3895: a scratch registry handle for the create_backup stamp seam
        # (`_stamp_backup_latest` MATCHes Team.id) — the test seeds it itself
        # and no production seam resolves the name.
        '"registry_3895"': "test-constructed",  # scratch registry handle for create_backup's stamp seam
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
    f"org_{org_id}" must be caught — the historical write site
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
            if literal.startswith(("org_", "registry_")):
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
    read-only/unit-mock/endpoint-constrained/test-constructed, or a
    projection-constructed name. A new un-routed site reds — the cycle-6 P2-10
    census becomes executable."""
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
    # Task 9 (P3): the registry was the FULL 17-file carve-out set at P3
    # (cycle-3 P2-12 count — 7 Task-5 stems + 10 additions; fixtures/
    # redis-guard/* are subprocess scripts, not test modules, and
    # test_smoke_embedded is already one of the 7). Later reconciliations
    # grew it past 17 (graph-integrity + eval_* + longmem additions below,
    # each dated). Mirrors config/ci-surfaces.yml `carve_out:`.
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    expected = frozenset({
        "test_backup_e2e",
        "test_config",
        "test_embedded_concurrency",
        # #2879: embedded AOF durability drift pin (carve-out — the docker
        # redirect hides the on-disk appendonlydir it measures).
        "test_embedded_durability_claim",
        "test_embedded_lifecycle",
        "test_embedded_lifecycle_fast_close",
        "test_flip_gate",
        "test_guard",
        "test_hard_reject",
        # #4047: both #3845 fork-guard files carry a module-level
        # `pytestmark = pytest.mark.embedded_only` (their subject is the
        # embedded daemon's module-fork wedge) but were absent from
        # carve_out/TEST_NO_REDIRECT_STEMS, so a full selection collected them
        # and every test skipped — a permanently unexecuted gate on main.
        "test_fork_safety_3845",
        "test_fork_slot_wedge_3845",
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
        # #2573-restored bge cache + P3 lane flip: longmem eval harness is
        # 100% embedded (_fresh_sdk(tmp_path) only) — its D2-D4 vector-leg
        # asserts are embedded-FalkorDBLite-only; moved to the carve-out
        # lane with the other eval_* suites.
        "test_longmem_runner",
        # #3420 (36fce6431, "bound the embedded DB lane's socket timeout and
        # retry multiplier"): its test module was added to
        # TEST_NO_REDIRECT_STEMS but this pin was not updated, so the
        # repo-wide markers gate red'd on every PR until reconciled here.
        "test_projection_embedded_socket_timeout",
        # 2026-09-18 #4028: the surface half asserts embedded brute-force
        # relevance-floor semantics (the docker sig-A vector branch returns no
        # absolute similarity), so the module joins the carve-out lane —
        # registered in ci-surfaces.yml:carve_out and TEST_NO_REDIRECT_STEMS.
        "test_precision_leak_4028",
        # #3663: the cross-tenant read-isolation proof asserts PRODUCTION
        # graph names (org_{org_id}) on the MCP list_graphs filter, the
        # namespace probe and its opener. The exemption is load-bearing:
        # without it the class-level test redirect would rename path-built
        # graphs to test_<hash>, so under a server URI no production name
        # would exist and those assertions would FAIL — a hard RED, not a false
        # pass. Runs embedded in every lane (same rationale as
        # test_hosted_backup).
        "test_cross_tenant_read_isolation",
    })
    assert frozenset(TEST_NO_REDIRECT_STEMS) == expected, (
        "TEST_NO_REDIRECT_STEMS drifted from the pinned carve-out stems "
        "(17 at Task-9 P3; dated reconciliations grew the set): "
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


_MARKER_NAME = "embedded_only"


def _folded_str(node):
    """The string a constant expression folds to, or None.

    `getattr(pytest.mark, "embedded" + "_only")` builds a string pytest resolves
    to the marker, so the name has to be folded before it can be compared.
    Handles plain constants, `+` chains of them, and an f-string with no
    interpolation.
    """
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded_str(node.left), _folded_str(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        parts = [_folded_str(v) for v in node.values]
        return None if any(p is None for p in parts) else "".join(parts)
    return None


def _marker_key(node, aliases: dict, seen: tuple = ()) -> str | None:
    """The string an access key denotes, when that string is statically known.

    The key of `getattr(pytest.mark, <key>)` / `pytest.mark[<key>]` is usually a
    literal, but a module-level constant is just as static: `MARKER =
    "embedded_only"` then `getattr(pytest.mark, MARKER)` marks the module, and
    the name resolves through the same alias map the mark expressions use.
    """
    folded = _folded_str(node)
    if folded is not None:
        return folded
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        # `_folded_str` folds literals only; a chain built from module constants
        # (`MARKER = PREFIX + "_only"`) is just as static, so resolve each side.
        left = _marker_key(node.left, aliases, seen)
        right = _marker_key(node.right, aliases, seen)
        return None if left is None or right is None else left + right
    if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == 1:
        # `K = ("embedded_only",)` / `for K in ("embedded_only",):`
        return _marker_key(node.elts[0], aliases, seen)
    if isinstance(node, ast.Name) and node.id not in seen:
        for value in aliases.get(node.id, ()):
            got = _marker_key(value, aliases, (*seen, node.id))
            if got is not None:
                return got
    return None


def _mentions_marker(node, aliases: dict, seen: tuple = ()) -> bool:
    """True when an expression may reference the embedded_only marker.

    A whole-subtree walk, not a shape match. pytest accepts the mark in a list,
    a tuple, a set, a conditional expression, a starred expansion, a subscript,
    a comprehension or a concatenation, and any of those may nest it
    arbitrarily — a hand-written unwrapper that enumerates the shapes it knows
    leaves whichever form nobody thought of silently unguarded, which is exactly
    the failure this guard exists to catch. A bare name met ANYWHERE inside the
    expression is resolved through `aliases` (a container can hide the mark one
    hop down: `marks = [mark]`).

    The marker name is accepted as a STRING only where a string is the access
    key — `getattr(pytest.mark, "embedded_only")` and
    `pytest.mark["embedded_only"]` — because matching the bare token anywhere
    would red on prose that merely shares the word
    (`reason="embedded_only"`), and a red on a module that is not embedded-only
    asks for a registration that would move a server-lane test file into the
    URI-unset carve-out job.

    `seen` breaks alias cycles; a name already visited is not re-entered.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == _MARKER_NAME:
            return True
        if isinstance(sub, ast.Subscript) \
                and _marker_key(sub.slice, aliases) == _MARKER_NAME:
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                and sub.func.id == "getattr" \
                and any(_marker_key(a, aliases) == _MARKER_NAME
                        for a in sub.args[1:]):
            return True
        if (isinstance(sub, ast.Name) and sub.id not in seen
                and aliases.get(sub.id)
                and any(_references_embedded_only(v, aliases, (*seen, sub.id))
                        for v in aliases[sub.id])):
            return True
    return False


def _bound_names(target) -> list:
    """Names bound by an assignment or loop target, flattening tuple unpacking.

    `globals()["pytestmark"] = ...` (and the `locals()` spelling) binds the
    module global too, so a subscript assignment whose key folds to a string
    counts as binding that name.
    """
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for el in target.elts for n in _bound_names(el)]
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    if isinstance(target, ast.Subscript):
        base = target.value
        if isinstance(base, ast.Call) and isinstance(base.func, ast.Name) \
                and base.func.id in ("globals", "locals"):
            name = _folded_str(target.slice)
            return [name] if name else []
    return []


def _references_embedded_only(expr, aliases: dict, seen: tuple = ()) -> bool:
    """True when a module-scope value may BE the embedded_only marker.

    Conservative by construction. A bare Name is resolved through the aliases
    bound BEFORE the point of use (Python runs top to bottom); anything else is
    accepted if the marker is mentioned anywhere inside it. Prose does not
    match: the token inside a docstring or a `skipif(reason=...)` string is an
    ast.Constant holding the whole sentence, not one equal to the bare name.

    `aliases` maps a name to every value bound to it so far, so a name rebound
    in a branch counts as marked if ANY of its bindings is. `seen` breaks alias
    cycles. Both choices, and the subtree walk above, err toward a false RED —
    a false RED costs one line of registration, a false GREEN is the silent
    collect-and-skip hole.

    OUT of contract — a mark constructed at runtime (returned by a helper, read
    off a config object): this is a source scan, not an interpreter.
    """
    if isinstance(expr, ast.Name):
        if expr.id in seen:
            return False
        return any(_references_embedded_only(v, aliases, (*seen, expr.id))
                   for v in aliases.get(expr.id, ()))
    return _mentions_marker(expr, aliases, seen)


# Module-scope compound statements: a mark set inside one of these IS a
# module-level mark, so the walk descends into them. FunctionDef / ClassDef
# bodies are NOT module scope (a mark set there does not mark the module), so
# the walk stops at them.
#
# The walk is a conservative OVER-APPROXIMATION: it also descends into branches
# that cannot execute (`if False:`), where pytest would not see the mark. That
# direction is deliberate — a false RED costs an unnecessary registration, a
# false GREEN is the silent-collect-and-skip hole this guard exists to close —
# and it is not detectable without evaluating conditions.
_COMPOUND_STMTS = tuple(
    c for c in (ast.If, ast.Try, ast.For, ast.While, ast.With,
                ast.AsyncFor, ast.AsyncWith, ast.Match,
                getattr(ast, "TryStar", None))
    if c is not None)


def _iter_module_scope(stmts):
    """Yield module-scope statements, descending into nested compound bodies."""
    for node in stmts:
        yield node
        if not isinstance(node, _COMPOUND_STMTS):
            continue
        # Read every container defensively: `match` is in _COMPOUND_STMTS but
        # carries only `cases`, and only `try` carries `handlers`/`finalbody`.
        yield from _iter_module_scope(getattr(node, "body", None) or [])
        for handler in getattr(node, "handlers", None) or []:
            yield from _iter_module_scope(handler.body)
        # `match` stores its bodies per case, not in `body`/`orelse`.
        for case in getattr(node, "cases", None) or []:
            yield from _iter_module_scope(case.body)
        yield from _iter_module_scope(getattr(node, "orelse", None) or [])
        yield from _iter_module_scope(getattr(node, "finalbody", None) or [])


def _module_level_embedded_only(tree) -> bool:
    """True when the module sets `pytestmark` to the embedded_only marker.

    Every module-scope assignment to `pytestmark` counts — not only the first
    (`pytestmark = pytest.mark.slow` then a later
    `pytestmark = pytest.mark.embedded_only` marks the whole module) and not
    only a bare one (a mark inside an `if`/`try`/`match` body at module scope
    marks it too). `+=` counts as well.

    Aliases are bound IN SOURCE ORDER: Python runs top to bottom, so a name
    assigned after the `pytestmark` line cannot have been its value, while a
    name bound in several places keeps every prior binding (see
    `_references_embedded_only`). Tuple/list unpacking and `for` targets bind
    names too and are recorded.

    A module-scope `pytestmark` that references the marker is enough to red even
    when a LATER assignment overrides it back off — the same conservative
    direction as a branch that cannot execute. Both are false REDs by
    construction, and both are cheaper than a silent miss.

    Two module-scope bindings cannot be read from here at all — a walrus in a
    condition (`if (pytestmark := ...):`) and an import
    (`from _marks import pytestmark`) — so they count as marked: the value is
    unknowable at scan time, and the conservative direction is a red.
    """
    aliases: dict[str, list] = {}
    for node in _iter_module_scope(tree.body):
        targets: list = []
        if isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AugAssign):
            targets, value = [node.target], node.value
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            # `for _m in [pytest.mark.embedded_only]:` binds `_m` before any
            # later use, so the loop's iterable is the alias's value.
            targets, value = [node.target], node.iter
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # The value lives in another module — unknowable here.
            if any((a.asname or a.name) == "pytestmark" for a in node.names):
                return True
            continue
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) \
                and any(kw.arg == "pytestmark" for kw in node.value.keywords):
            # `globals().update(pytestmark=...)` — bound through a call, so the
            # value cannot be read here; unknowable counts as marked.
            return True
        else:
            continue
        if value is None:
            continue
        names = [n for t in targets for n in _bound_names(t)]
        if "pytestmark" in names and _references_embedded_only(value, aliases):
            return True
        for name in names:
            aliases.setdefault(name, []).append(value)

    # A walrus binds mid-expression, not through a statement target.
    for node in ast.walk(tree):
        if isinstance(node, ast.NamedExpr) \
                and isinstance(node.target, ast.Name) \
                and node.target.id == "pytestmark" \
                and _references_embedded_only(node.value, aliases):
            return True
    return False


def test_module_level_embedded_only_modules_are_carve_out(request):
    # #4047: it is the MODULE-level form of the marker that can go silent.
    # `@pytest.mark.embedded_only` on one test skips that test under a URI and
    # the file's other tests still run on the docker legs — a docker-leg half
    # is the design (test_audit's CLI error-path siblings, etc.).
    # `pytestmark = pytest.mark.embedded_only` skips the WHOLE module, so such
    # a module is only ever executed if it is routed to the URI-unset carve-out
    # job; if it is not registered it is collected on the docker legs, skipped
    # there, and the run reports GREEN while skipping every test in it. Both
    # #3845 fork guards were in exactly that state (#3845's evidence never ran
    # on a full selection) and nothing red'd, because the exact-set pin below
    # compares the registry to a literal and was in agreement with it.
    #
    # Two halves, because they cover different populations and fail differently:
    #   EXACT — every module pytest actually collected in this session. pytest
    #     has already imported them, so we read what `pytestmark` EVALUATED to
    #     (conditionals, aliases, comprehensions, getattr: all resolved by the
    #     interpreter). No guessing, no false verdicts.
    #   SOURCE SCAN — every test module on disk. Catches a module that this
    #     session did not collect (a docs-only or surface-scoped selection) and
    #     is deliberately conservative, so it can over-report.
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    registered = set(TEST_NO_REDIRECT_STEMS)

    # ── EXACT ────────────────────────────────────────────────────────────────
    exact = []
    visited = set()
    for item in request.session.items:
        mod = item.getparent(pytest.Module)
        if mod is None or mod.nodeid in visited:
            continue
        visited.add(mod.nodeid)
        marks = getattr(getattr(mod, "obj", None), "pytestmark", None)
        if marks is None:
            continue
        marks = marks if isinstance(marks, (list, tuple)) else [marks]
        if "embedded_only" in {getattr(m, "name", None) for m in marks} \
                and Path(str(mod.path)).stem not in registered:
            exact.append(mod.nodeid)

    # ── SOURCE SCAN ─────────────────────────────────────────────────────────
    offenders = []
    # The universe mirrors pytest's own collection: BOTH default `python_files`
    # patterns (`test_*.py` and `*_test.py`) — a marker module named the second
    # way is collected and skipped exactly the same. Skips key on real path
    # PARTS: a substring test over the whole absolute path would step over a
    # collected file under a directory that merely contains ".venv".
    skip_parts = {"__pycache__", ".venv", ".git", "node_modules"}
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        if not (path.name.startswith("test_") or path.name.endswith("_test.py")):
            continue
        if skip_parts & set(path.relative_to(_TESTS_ROOT).parts):
            continue
        try:
            # parse BYTES: ast honours a PEP 263 coding declaration, so a
            # latin-1 module (importable, collected) is read rather than
            # skipped by a utf-8 decode error.
            tree = ast.parse(path.read_bytes())
        except (SyntaxError, UnicodeDecodeError, ValueError):
            continue  # not importable source; pytest would not collect it either
        if _module_level_embedded_only(tree) and path.stem not in registered:
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert not exact, (
        "a test module in this session carries a module-level embedded_only "
        "pytestmark but is not in the carve-out registry — it is collected here "
        "and every test in it is skipped, so the run is green without "
        "executing; register the stem in tests/_embedded.py "
        f"TEST_NO_REDIRECT_STEMS AND config/ci-surfaces.yml `carve_out:`: {exact}")
    assert not offenders, (
        "module-level `pytestmark = pytest.mark.embedded_only` outside the "
        "carve-out registry — a full selection collects these and reports "
        "them green while skipping every test in them; register the stem in "
        "tests/_embedded.py TEST_NO_REDIRECT_STEMS AND "
        f"config/ci-surfaces.yml `carve_out:`: {offenders}")


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
