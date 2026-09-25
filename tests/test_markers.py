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
    # #3665: `_provision` seeds the org's REAL `created_at` into the registry
    # graph (the column the cohort is derived from) and the cap's cohort
    # resolution reads that same graph, so the literal IS the seed→resolution
    # coupling — a test_* rename would seed a different graph than the code
    # resolves. Same class as test_quota/test_commit_endpoint.
    "test_cohort_cost_cap.py": {"registry": "prod-coupled"},
    "test_cross_tenant_read_isolation.py": {"registry": "prod-coupled"},  # #3663 — registry control-plane seeding for the cross-tenant read proof
    "test_3926_error_prop_guard.py": {"registry": "prod-coupled"},  # #3926 — the literal IS the canonical namespace PROD code resolves
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
    "test_capture_loop_responsiveness.py": {"registry": "prod-coupled"},  # #3086 — the capture-writer loop-affinity proof reaches the graph class via _make_sdk(namespace="registry") to record writer-thread affinity
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
    # #3825: the metering-WINDOW tests put a real billing anchor on an org's
    # ``:Team`` node and drive the ledger through the production writer; the
    # registry store IS the coupling under test (same class as test_metering).
    "test_metering_period_window.py": {"registry": "prod-coupled"},
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
    "test_onboarding_truth_surface.py": {"registry": "prod-coupled"},  # #3670/#3671/#3681: registry-resolve seeding for the server-owned capture receipts (same _make_sdk(namespace="registry") lane as the siblings above)
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
    # #3718 (read half): :114 — _make_sdk(namespace="registry") WARMS the same
    # registry graph the hosted read handlers resolve (the anchor must be warm
    # for the embedded lane to survive between requests). A test_* rename would
    # warm a different graph and leave the prod registry anchor cold — same
    # class as test_attribution_machine_model / test_metering.
    "test_read_routes_loop_responsiveness.py": {"registry": "prod-coupled"},
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
    # #5137 landed this fixture without a route, which reds this guard on main.
    # The gold fixture writes :Source rows into an EMBEDDED graph reached through
    # the `shared_embedded_db` path (the redirect derives a per-path test_* graph);
    # "gold" is a fixture graph name, not a server graph the SDK resolves from the
    # registry and not a production-shape namespace.
    "test_document_source_gold.py": {"gold": "test-constructed"},
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
        '"org_team_x"': "endpoint-constrained",  # post-drill count assert + #4233 marker write (drill/backup resolve org_{id})
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
        # #4233 outcome-settle guards — same direct-helper shape as the #3813
        # block above: a raw source and a raw destination whose names are
        # handed straight to `_restore_into_temp_verify_swap` /
        # `_graph_copy_with_restore_bound` / `_restore_copy_settled` and read
        # back. Test-constructed, not production-shape.
        '"org_settle_source"': "test-constructed",  # seeded source
        '"org_settle_target"': "test-constructed",  # destination + read-back
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
    # #4290: declared per the new-test-file registration rule. ZERO sites by
    # construction — the finding-provenance gate is hermetic over a temp git
    # repo and never calls select_graph; the key is a deliberate "considered,
    # nothing to route" statement, and test_select_graph_routing_table_keys_exist
    # pins that the module really exists.
    "test_finding_provenance.py": {},
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
        # #2814: authoritative-config durability across rebuild_all (embedded
        # carve-out — see config/ci-surfaces.yml `carve_out:`).
        "test_rebuild_config_preservation",
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
        # #4524: the vecf32 overwrite-seam guards assert the EMBEDDED engine's
        # silent vecf32-overwrite behaviour (the server lane lands the same
        # write), so the module joins the carve-out lane — registered in
        # ci-surfaces.yml:carve_out + the core surface and in
        # TEST_NO_REDIRECT_STEMS.
        "test_vecf32_overwrite_seams_4524",
        # #5148: `test_sdk_emit_event_survives_unreachable_seam` is
        # `embedded_only` (it constructs a real embedded store). Without the
        # carve-out routing it is collected by every URI-set lane and skipped
        # via the marker hook — a permanently green, permanently unexecuted
        # gate on main (the #4047/#4524 shape). Registered in all three homes:
        # ci-surfaces.yml:carve_out, TEST_NO_REDIRECT_STEMS, and here.
        "test_write_path_unreachable_seam_5148",
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


# ── #4164: the marked tests must be SELECTED BY THE MARKER in CI ────────────
# ── #4164: the marked tests must be SELECTED BY THE MARKER in CI ────────────
# pytest flags that SUBTRACT from a selection, or execute nothing at all. Every
# spelling argparse accepts is refused: attached short (`-kslow`), `=`-joined
# long (`--deselect=x`), and the space form.
_NARROWING_FLAGS = (
    "-k", "--deselect", "--ignore", "--ignore-glob", "--last-failed", "--lf",
    "--failed-first", "--new-first", "--stepwise", "--collect-only", "--co",
    "--setup-only", "--setup-plan", "--fixtures")


def _is_narrowing(arg: str) -> bool:
    """Whether a pytest argument narrows (or empties) the selected set."""
    for flag in _NARROWING_FLAGS:
        if arg == flag or arg.startswith(flag + "="):
            return True
        # attached short-option value: `-kslow` IS `-k slow` to argparse (a
        # bare `--keep…` must not match `-k`, hence the single-dash test)
        if flag.startswith("-") and not flag.startswith("--") \
                and arg.startswith(flag) and len(arg) > len(flag):
            return True
    return False


def test_ci_runs_the_embedded_only_marker_selection():
    """The embedded_only-marked tests must be selected BY THE MARKER, not by a
    hand-written list.

    #4164: the job that runs them URI-less (`test-d14-hosted-api`) selected
    `tests/test_hosted_api.py -k "concurrent_first_calls or …"` — a hand list
    of the five D14 guards (#2188). A hand list can only cover the marks
    someone remembered, and the rest were collected-and-skipped on every
    docker leg (the autouse D-2 hook) and executed nowhere: the marked tests
    in test_audit, test_export_delete, test_index_directory, test_indexes,
    test_lme_m6_evidence, test_onboard_prompt_ref, test_pack_state and
    test_session_extraction_modes. Selecting `-m embedded_only` over `tests/`
    cannot drift — any new marked test, in any file, in any of the three mark
    forms (per-test decorator, class-level, class-body `pytestmark`) is picked
    up by the next run with no edit here.

    Pinned as the PROPERTY (a marker-driven selection exists, in a URI-less
    CARVE_OUT job), never as the job's name or its step text — the job is free
    to be renamed, and the count of marked tests is free to grow.
    """
    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (root / ".github" / "workflows" / "python-ci.yml").read_text())
    assert isinstance(workflow, dict), "python-ci.yml did not parse as a mapping"

    def _steps(job: dict) -> list[dict]:
        return list(job.get("steps") or [])

    marker_selections = []
    for job_name, job in (workflow.get("jobs") or {}).items():
        job_env = dict(workflow.get("env") or {})
        job_env.update(job.get("env") or {})
        for step in _steps(job):
            run = step.get("run") or ""
            # the STEP's own env counts too: a URI declared on the step (or
            # exported inline in the run) leaks exactly as one on the job does
            step_env = dict(job_env)
            step_env.update(step.get("env") or {})
            # Read EVERY shell segment of the run — a marker selection may be
            # the second command in a block, and the marker selection is
            # identified per segment: `pytest`-ish words elsewhere (a
            # `tee /tmp/pytest-d14.log`, a comment) do not carry the marker
            # and are therefore never read as arguments. A quoted `-m
            # "embedded_only"` is the same selection, so it is normalized
            # rather than being a false red.
            for segment in re.split(r"\|\||&&|[|;]", run.replace("\\\n", " ")):
                if not re.search(r"-m\s+[\"']?embedded_only[\"']?", segment):
                    continue
                marker_selections.append((job_name, step_env, segment, run))

    assert marker_selections, (
        "no CI job runs `pytest -m embedded_only` — the marked tests are then "
        "executed only where a hand-written list happens to name them, which "
        "is the #4164 defect (silently skipped everywhere else)")

    for job_name, step_env, run, whole_step in marker_selections:
        assert "TORTOISE_DB_URI" not in step_env, (
            f"{job_name}: the embedded_only marker selection must run with "
            "TORTOISE_DB_URI UNSET — with a supported URI the autouse D-2 "
            "hook skips every marked test, so the job would report a silent "
            "all-skip (the workflow-level, job-level AND step-level env are "
            "all checked)")
        assert not re.search(r"\bTORTOISE_DB_URI\s*=", whole_step), (
            f"{job_name}: the marker selection must not export "
            "TORTOISE_DB_URI inline (`VAR=… pytest`) — same silent all-skip")
        assert step_env.get("TORTOISE_TEST_CARVE_OUT") == "1", (
            f"{job_name}: a URI-less run needs TORTOISE_TEST_CARVE_OUT=1 "
            "(epic #1647 P4 — default pytest fails without one of the two)")
        # a step that selects by marker but ALSO names test files is a hand
        # list wearing a marker flag — the marker flag alone does not make it
        # drift-free. `-m embedded_only tests/` scans the tree; `-m
        # embedded_only tests/test_hosted_api.py` is the #2188 shape with one
        # more flag, and the next marked test in another file stays dark.
        #
        # Everything below reads PYTEST's own arguments of THIS segment: the
        # shell wrapper's flags are not pytest's (`timeout -s INT -k 10 15m`
        # carries a `-k` that kills the process, not a test filter).
        #
        # The invocation is matched as a PATTERN, not as a bare token: the
        # attached spelling `python -mpytest` is valid and yields no `pytest`
        # token, which would skip every check below — a fail-OPEN, the one
        # direction a guard may never take. No invocation but a marker present
        # is therefore an assertion failure, not a `continue`.
        invocation = re.search(
            r"\bpython[0-9.]*\s+-m\s*pytest\b|\bpytest\b", run)
        assert invocation is not None, (
            f"{job_name}: the embedded_only marker appears in a run block with "
            f"no recognisable pytest invocation — this guard cannot read it, "
            f"and silence is not an option: {run[:200]!r}")
        args = run[invocation.end():].split()

        def _bare(token: str) -> str:
            """The token without shell quoting — `"tests/x.py"` IS `tests/x.py`."""
            return token.replace('"', "").replace("'", "")

        # (a nodeid's PATH half counts: `--deselect tests/x.py::SomeClass`
        # names a test file while ending in a class name — a documented way to
        # drop one marked test that neither the file check below nor the
        # runtime floor would notice)
        named = [a for a in args if _bare(a).split("::", 1)[0].endswith(".py")]
        assert not named, (
            f"{job_name}: the marker selection must scan the test TREE, not "
            f"name test files — that is a hand list with a marker flag, and "
            f"the next marked test elsewhere stays dark: {named}")
        # nor may it be NARROWED after being selected — a narrowing flag
        # subtracts from the marked set exactly as a hand list would, and a
        # single subtracted test usually stays above the runtime floor, so
        # nothing else would report it. Every spelling argparse accepts is
        # refused: attached short (`-kslow`), `=`-joined long (`--deselect=x`),
        # and the space form. The list is the pytest surface that can SUBTRACT
        # from a selection or execute nothing — not just the two flags this
        # step happens to avoid today (`--collect-only` and friends leave the
        # job green with nothing run, which the floor only catches when it is
        # total).
        narrowing = [a for a in args if _is_narrowing(a)]
        assert not narrowing, (
            f"{job_name}: the marker selection must not be narrowed by "
            f"{narrowing} — a subtracted marked test runs nowhere, which is "
            f"the #4164 defect behind a normal pytest flag")
        # and the `-m` value must be EXACTLY the marker: `-m "embedded_only and
        # not slow"` is an expression that narrows the set while still
        # containing the token this test searches for. The value is read with
        # a quote-aware pattern — a whitespace split would stop at the first
        # word of a quoted expression and call the rest unrelated arguments.
        pytest_text = " ".join(args)
        marker_exprs = [
            match.group(1).strip("\"'")
            for match in re.finditer(r"-m\s+(\"[^\"]*\"|'[^']*'|\S+)", pytest_text)]
        assert marker_exprs, (
            f"{job_name}: could not read the -m value of the marker "
            f"selection: {run[:120]!r}")
        for expr in marker_exprs:
            assert expr == "embedded_only", (
                f"{job_name}: the marker selection must be exactly "
                f"`-m embedded_only`, not the expression {expr!r} — an "
                f"expression selects a subset, and the rest run nowhere")
        assert any(_bare(a).rstrip("/") in {"tests", "tests/.", "./tests"}
                   for a in args), (
            f"{job_name}: the marker selection must target the tests tree "
            f"(`tests/` as a pytest argument, not a word in the surrounding "
            f"prose): {run[:120]!r}")


# ── platform-gated tests: evidence CI can never execute ─────────────────────
# A test skipped by a PLATFORM gate is invisible in a green run: the job passes
# and nothing in the result says the gate did not execute. #4162 is the case
# that matters — `tests/test_fork_safety_3845.py`'s producer + mutation-proof
# pair, which is dev-machine evidence ONLY because the hazard it reproduces
# belongs to macOS libsystem.
#
# The gate is not a defect and must not be "fixed" by loosening it: a fork that
# lands while libc's process-global timezone rwlock is held wedges the child on
# darwin, and does NOT on glibc or musl. Measured 2026-09-19 (#4162), same
# mechanism (owned zone file, mtime bumped before every call, six spinning
# threads, then fork):
#     macOS (libsystem, clang)   -> WEDGED on the first fork
#     glibc 2.36 (debian bookworm) -> 20/20 children completed
#     musl (alpine)                -> 20/20 children completed
# So no Linux job can produce this evidence, and a "Linux variant" would be
# testing a different mechanism — not a mutation proof for the #3845 fix.
#
# Declared boundary: what the scan above does NOT see, so the registry reads
# as the floor it is. A platform-conditional `os.name`/`sysconfig` check; a
# `collect_ignore` written through `globals()[…]`; a platform value computed at
# runtime; a helper that early-returns so its test passes vacuously; a skip mark
# imported from a module the scan does not read; and any spelling nobody has
# written yet — which is why the guard's last resort is a runtime check (see
# #4215) rather than this list getting longer. Those are recorded here rather
# than guessed at.
#
# What this registry buys: a platform gate cannot be added silently in the
# shapes the scan reads — a skip-like call naming the platform, a skip-like call
# or a test definition under a condition that names the platform (whichever
# container the condition is written in), and the `skipif(...)` calls that are
# the idiomatic spellings in both pytest and unittest. Declared OUT of the
# scan's reach, deliberately: a helper that early-returns so its test passes
# vacuously (there is no call to recognise), a platform value computed at
# runtime, and a skip mark defined in another file and imported
# (`from helpers import macos_only`) — a mark imported from a module the scan
# does not read is invisible to it. (`conftest.py` IS read, so a mark defined
# there is covered.) Those are recorded here rather than guessed at — the
# registry is a floor, not a proof.
PLATFORM_GATED_TESTS: dict[str, str] = {
    "test_fork_safety_3845.py": (
        "#3845 producer + MUTATION PROOF. `_build_holder()` skips unless "
        "sys.platform == 'darwin', and every CI job is ubuntu-latest (12/12), "
        "so `test_fork_child_does_not_hang_with_the_production_config` (the "
        "fix) and `test_without_the_fix_the_same_race_hangs_a_child` (the "
        "mutation that proves it) execute on a dev-machine macOS run only. "
        "CI still runs this file's other two tests "
        "(`test_embedded_choke_point_runs_at_the_proven_fork_safe_level`, "
        "`test_env_override_is_honoured_and_loud`), so a green carve-out leg "
        "means 'the choke point is wired', NEVER 'the fork hazard is "
        "reproduced here'. The provenance of that gap is measured, not "
        "assumed — see the table above."
    ),
}


def _call_name(func) -> str:
    """`pytest.skipif` / `skip` / `unittest.SkipTest` -> the bare name."""
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


# Every spelling that makes a test not run, in the case each library uses:
# `pytest.skip`/`skipif`/`importorskip`/`xfail` and `unittest.skipIf`/
# `skipUnless`/`SkipTest`. Compared case-folded, so `skipIf` is `skipif`.
_SKIP_LIKE_NAMES = frozenset({
    "skip", "skipif", "skipunless", "skiptest", "importorskip", "xfail",
})


def _skip_kind(func, aliases=None) -> str | None:
    """What this callable is, or None.

    A callable can also arrive under another name — `skipper = pytest.skip`,
    `from pytest import skip as s`, `getattr(pytest, "skip")`, and (for a mark)
    `skipper = pytest.mark.skipif` — and a gate spelled that way is the same
    gate. The RESOLVED name is returned, not the surface one, because a caller
    has to know whether it is a `skipif` (whose first argument is a condition)
    or a `skip` (whose arguments are messages).
    """
    name = _call_name(func)
    if name.casefold() in _SKIP_LIKE_NAMES:
        return name.casefold()
    if aliases and name in aliases:
        return aliases[name]
    if isinstance(func, ast.Call) and _call_name(func.func) == "partial" \
            and func.args:
        # `macos_only = functools.partial(pytest.mark.skipif, reason="macOS")`
        # then `@macos_only(sys.platform != "darwin")` is the same gate.
        return _skip_kind(func.args[0], aliases)
    if isinstance(func, ast.Call) and _call_name(func.func) == "getattr" \
            and len(func.args) == 2 \
            and isinstance(func.args[1], ast.Constant) \
            and isinstance(func.args[1].value, str):
        kind = func.args[1].value.casefold()
        if kind in _SKIP_LIKE_NAMES:
            return kind
    return None


def _is_skip_like(func, aliases=None) -> bool:
    return _skip_kind(func, aliases) is not None


# The spellings whose first positional argument is a CONDITION, not a message:
# `skipif(cond, reason)` versus `skip(msg)`. They are the only ones where a
# string literal is a condition, so they are the only ones where the string
# route applies — `pytest.skip("… on this platform.")` is prose.
_SKIPIF_NAMES = frozenset({"skipif", "skipunless"})


# A parametrization whose argument list is platform-conditional is a gate with
# no skip call: an empty parameter set makes pytest SKIP the test.
_PARAMETRIZE_NAMES = frozenset({"parametrize", "fixture"})


# A collection hook or `collect_ignore` target that filters on the platform is
# the same hole with no skip call at all: the test is not skipped, it is never
# collected, and the run is green.
_COLLECT_EXCLUSION_HOOKS = frozenset({
    "pytest_ignore_collect", "pytest_collection_modifyitems",
})


def _has_skip_mark(node, aliases=None) -> bool:
    """Whether a skip-like callable or mark appears anywhere in this subtree.

    A binding is not a call: `pytestmark = pytest.mark.skip`,
    `pytestmark = [pytest.mark.skip]`, and
    `pytestmark = pytest.mark.skip if <condition> else []` are the same gate
    written three ways, and only the first was visible to a call scan.
    """
    return any(_skip_kind(sub, aliases) is not None for sub in ast.walk(node))


def _skip_names(tree) -> dict[str, str]:
    """Module-scope names bound to a skip-like callable -> its resolved kind."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("pytest", "unittest"):
            for alias in node.names:
                if alias.name.casefold() in _SKIP_LIKE_NAMES:
                    aliases[alias.asname or alias.name] = alias.name.casefold()
    for node in _iter_module_scope(getattr(tree, "body", None) or []):
        targets, value = None, None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if targets is None or value is None:
            continue
        kind = _skip_kind(value, aliases)
        if kind is not None:
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases[target.id] = kind
    return aliases


# Names that mean the platform, as (the `sys` module, a platform VALUE, the
# `platform` module). Each is a set because any of them can be imported under
# another name — `import sys as s`, `from sys import platform as plat`,
# `import platform as p` — and an alias is not a weaker gate.
_DEFAULT_PLATFORM_NAMES = ({"sys"}, {"platform"}, {"platform"})


def _mentions_platform(node, platform_names=None, strings=True) -> bool:
    """Whether an expression refers to the platform.

    Four ways, in the order they are written: `sys.platform` (or its `sys`
    alias); the bare name `platform` (which means a value only when it came
    from `sys`, alias included); a `platform.<attr>` module query —
    `platform.system() != "Darwin"` is the stdlib spelling of the same gate;
    and a STRING condition that names it, because pytest's documented form is
    `@pytest.mark.skipif("sys.platform != 'darwin'")` — a static literal a
    source scan can read, unlike a computed value.

    The module-query and string routes are deliberately over-inclusive: a
    non-platform call that happens to mention `platform`
    (`platform.python_version()`) also matches, and costs one line of
    registration. The opposite direction — missing a gate, and leaving a test
    that runs nowhere with a green job — is the hole this exists to close.

    `strings=False` turns the string route off, for the positions where a
    literal is prose rather than a condition: `reason="runs on every
    platform."` is not a gate, and pytest's documented string condition
    `skipif("sys.platform != 'darwin'")` is.
    """
    sys_names, value_names, module_names = platform_names \
        if platform_names is not None else _DEFAULT_PLATFORM_NAMES
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr == "platform" \
                and isinstance(sub.value, ast.Name) \
                and sub.value.id in sys_names:
            return True
        if isinstance(sub, ast.Attribute) \
                and isinstance(sub.value, ast.Name) \
                and sub.value.id in module_names:
            return True
        # `from sys import platform`, or a module-level alias derived from it
        # (`IS_DARWIN = sys.platform == "darwin"`)
        if isinstance(sub, ast.Name) and sub.id in value_names:
            return True
        if strings and isinstance(sub, ast.Constant) \
                and isinstance(sub.value, str) \
                and ("sys.platform" in sub.value or "platform." in sub.value):
            return True
        # `getattr(sys, "platform")` — the attribute named by a literal.
        if isinstance(sub, ast.Call) and _call_name(sub.func) == "getattr" \
                and len(sub.args) == 2 \
                and isinstance(sub.args[0], ast.Name) \
                and sub.args[0].id in sys_names \
                and isinstance(sub.args[1], ast.Constant) \
                and sub.args[1].value == "platform":
            return True
    return False


def _platform_aliases(tree):
    """What means "the platform" in this file, under whatever names.

    Bound names are collected, not just the `sys`/`platform` literals: a module
    imported under another name is the same module, and a module-level alias
    (`IS_DARWIN = sys.platform == "darwin"`) is the same gate written twice.
    """
    sys_names, value_names, module_names = ({"sys"}, {"platform"}, {"platform"})
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sys":
                    sys_names.add(alias.asname or alias.name)
                elif alias.name == "platform":
                    module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "sys":
            for alias in node.names:
                if alias.name == "platform":
                    value_names.add(alias.asname or alias.name)
    names = (sys_names, value_names, module_names)
    for node in _iter_module_scope(getattr(tree, "body", None) or []):
        targets, value = None, None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        if targets and value is not None and _mentions_platform(value, names):
            value_names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _under_platform_condition(node, parents, platform_names) -> bool:
    """Whether `node` sits under a condition that names the platform.

    Ancestor walk instead of a fixed list of container shapes: an earlier
    version enumerated `ast.If` and was defeated three times in a row by the
    spellings nobody had thought of — a decorator, then the stdlib's
    case-sensitive `skipIf`, then `match`/`BoolOp`/`IfExp`. The container a
    condition is written in is not the interesting part; *that it is a platform
    condition* is.
    """
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.If, ast.While, ast.IfExp)):
            if _mentions_platform(cur.test, platform_names):
                return True
        elif isinstance(cur, ast.BoolOp):
            # `sys.platform == "darwin" or pytest.skip("macOS only")`
            if _mentions_platform(cur, platform_names):
                return True
        elif isinstance(cur, ast.Match) \
                and _mentions_platform(cur.subject, platform_names):
            # `match sys.platform:` + a `case _:` that skips
            return True
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            break  # a condition in another function is another function's gate
        cur = parents.get(cur)
    return False


def _platform_gates(path: Path) -> list[str]:
    """Names of test files that are dev-machine-only evidence.

    A file is gated if, anywhere in its source:
      * a skip-like call names the platform in its own arguments — the
        `@pytest.mark.skipif(sys.platform …)` decorator,
        `pytestmark = pytest.mark.skipif(…)`, `unittest.skipIf`/
        `skipUnless`/`SkipTest` (names compared case-folded), `importorskip`;
      * a skip-like call sits under a condition that names the platform,
        whichever container the condition is written in (`if`, `match`, a
        boolean short-circuit, a conditional expression);
      * a test function is DEFINED under such a condition, so it is never
        collected off that platform — a gate with no skip call at all;
      * a COLLECTION EXCLUSION is applied under such a condition — a
        module-level `collect_ignore`/`collect_ignore_glob` target, or a
        `pytest_ignore_collect`/`pytest_collection_modifyitems` hook that
        mentions the platform. The test is not skipped, it is never collected,
        and the run is green;
      * a whole-module skip is BOUND under such a condition (`pytestmark =
        pytest.mark.skip` has no call for a skip scan to find), or a
        parametrization is platform-conditional (`parametrize`/`fixture`
        with an argument naming the platform), since an empty parameter set
        skips the test rather than running it.

    Naming the platform includes `from sys import platform`, a one-hop module
    alias (`IS_DARWIN = sys.platform == "darwin"`), an import under another
    name (`import sys as s`, `from sys import platform as plat`), a
    `platform.<attr>` module query, and pytest's string condition
    (`skipif("sys.platform != 'darwin'")`).

    Deliberately over-inclusive where it is unsure: the module query and the
    string condition count as naming the platform (`platform.system() !=
    "Darwin"` is the stdlib spelling of the same gate), so a non-platform
    condition that merely mentions `platform` can red this and want a one-line
    registration. That direction is chosen: a false red costs a line, a false
    green leaves tests running nowhere under a job that looks healthy.

    Unreadable source yields no gates rather than an exception: a file pytest
    cannot parse cannot be collected either, so the suite is already red.
    """
    try:
        tree = ast.parse(path.read_bytes())
    except (SyntaxError, UnicodeDecodeError):
        return []
    platform_names = _platform_aliases(tree)
    skip_names = _skip_names(tree)
    parents = {child: parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}
    try:
        relative = path.resolve().relative_to(_TESTS_ROOT).as_posix()
    except ValueError:  # a path outside the tests tree (a unit-test probe)
        relative = path.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_skip_like(node.func, skip_names):
            # A skipif's condition is its first positional argument; for
            # `skip(msg)` every positional is a message. Strings are a
            # condition only in the former — and the KIND is the resolved one,
            # so an aliased `skipper = pytest.mark.skipif` still counts.
            first_is_condition = _skip_kind(node.func, skip_names) \
                in _SKIPIF_NAMES
            named = bool(node.args) and _mentions_platform(
                node.args[0], platform_names, strings=first_is_condition)
            named = named or any(
                _mentions_platform(arg, platform_names, strings=False)
                for arg in node.args[1:])
            # `skipif` takes its condition by NAME too — `skipif(condition=
            # "sys.platform == 'darwin'")` is the same string condition, while
            # `reason=` beside it is prose.
            named = named or any(
                _mentions_platform(kw.value, platform_names,
                                   strings=first_is_condition
                                   and kw.arg == "condition")
                for kw in node.keywords)
            if named or _under_platform_condition(node, parents,
                                                 platform_names):
                return [relative]
        elif isinstance(node, ast.Call) \
                and _call_name(node.func) in _PARAMETRIZE_NAMES \
                and _mentions_platform(node, platform_names):
            # `parametrize("x", [] if sys.platform != "darwin" else [1])` — an
            # empty parameter set SKIPS the test off that platform, and the
            # same trick works through `fixture(params=…)`.
            return [relative]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test") \
                and _under_platform_condition(node, parents, platform_names):
            return [relative]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in _COLLECT_EXCLUSION_HOOKS \
                and _mentions_platform(node, platform_names, strings=False):
            # `pytest_ignore_collect` / `pytest_collection_modifyitems` that
            # filters on the platform: the test is never collected.
            return [relative]
        elif isinstance(node, ast.Name) \
                and node.id.startswith("collect_ignore") \
                and _under_platform_condition(node, parents, platform_names):
            return [relative]
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            # The binding itself, for the spellings where the condition is the
            # VALUE rather than an enclosing statement —
            # `collect_ignore = ["x"] if sys.platform != "darwin" else []` puts
            # the platform condition beside the target, not above it.
            targets = node.targets if isinstance(node, ast.Assign) \
                else [node.target]
            value = getattr(node, "value", None)
            guarding = _under_platform_condition(node, parents, platform_names)
            if value is not None and any(
                    isinstance(t, ast.Name)
                    and t.id.startswith("collect_ignore")
                    for t in targets) \
                    and (guarding or _mentions_platform(value,
                                                        platform_names)):
                return [relative]
            # `pytestmark = pytest.mark.skip` — a whole-module skip with no
            # CALL to see, so only the binding gives it away. The mark can be
            # nested (`[pytest.mark.skip]`) or conditional in the VALUE
            # (`… if sys.platform != "darwin" else []`), so the whole subtree is
            # searched — the same shape `collect_ignore` already handles.
            if value is not None and any(
                    (isinstance(t, ast.Name) and t.id == "pytestmark")
                    or (isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id == "pytestmark")
                    for t in targets) \
                    and _has_skip_mark(value, skip_names) \
                    and (guarding or _mentions_platform(value,
                                                        platform_names)):
                return [relative]
        elif isinstance(node, ast.Expr) \
                and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Attribute) \
                and node.value.func.attr in ("append", "extend", "insert") \
                and isinstance(node.value.func.value, ast.Name) \
                and node.value.func.value.id == "pytestmark" \
                and _has_skip_mark(node.value, skip_names) \
                and _under_platform_condition(node, parents, platform_names):
            # `pytestmark.append(pytest.mark.skip)` under a platform condition
            return [relative]
    return []


def test_platform_gated_tests_are_registered():
    """Every platform-gated test file (in the shapes the scan reads) is
    enumerated, with what CI cannot verify.

    An unregistered gate is not a bug in the gate — it is a silent hole in the
    EVIDENCE: the job stays green and nothing distinguishes "verified" from
    "skipped on this platform" (#4162). Registering it does not make CI run it;
    it makes the gap findable by the next lane that reads a green carve-out leg.

    The entry is keyed by FILE, deliberately: the gate usually lives in a
    fixture or helper, so a per-test key would mean call-graph analysis, and a
    second gated test added to an already-registered file is covered by the
    entry that is already there.

    `conftest.py` is scanned alongside the tests, and keys are PATHS relative to
    the tests root: a fixture gate in a conftest is exactly the "gate lives in a
    fixture" case this is keyed for, and three conftest.py files plus two
    same-named test modules exist, so a bare filename collides.
    """
    found: set[str] = set()
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        if not (path.name.startswith("test_")
                or path.name == "conftest.py"):
            continue
        found.update(_platform_gates(path))

    registered = set(PLATFORM_GATED_TESTS)
    assert not (found - registered), (
        "these files carry a `sys.platform` skip that runs nowhere on this "
        "platform (CI is ubuntu-latest for every job) and are not registered "
        f"in PLATFORM_GATED_TESTS: {sorted(found - registered)} — add it with "
        "what CI therefore does NOT verify (#4162)")
    assert not (registered - found), (
        "PLATFORM_GATED_TESTS lists a file with no platform gate any more — "
        f"delete the entry (or the gate was spelled differently): "
        f"{sorted(registered - found)}")
    for name, reason in PLATFORM_GATED_TESTS.items():
        assert isinstance(reason, str) and len(reason) > 80, (
            f"{name}: the registry entry must say what CI cannot verify, not "
            f"just that a gate exists: {reason!r}")


# Every spelling a review cycle demonstrated that `_platform_gates` missed, and
# every look-alike that must stay unflagged. They live here, as a test, because
# eight cycles each narrowed the scan and each fix was verified by hand — a
# battery that runs nowhere is how the next narrowing ships. When this reds, the
# scanner lost a shape it once had.
_PLATFORM_GATE_SPELLINGS: tuple[tuple[str, bool], ...] = (
    ('import sys, pytest\n@pytest.mark.skipif(sys.platform != "darwin", reason="m")\ndef test_x(): pass\n', True),
    ('import sys, unittest\n@unittest.skipIf(sys.platform != "darwin", "m")\ndef test_x(self): pass\n', True),
    ('import sys, unittest\n@unittest.skipUnless(sys.platform == "darwin", "m")\ndef test_x(self): pass\n', True),
    ('import sys, unittest\ndef f():\n    if sys.platform != "darwin":\n        raise unittest.SkipTest("m")\n', True),
    ('import pytest\n@pytest.mark.skipif("sys.platform != \'darwin\'", reason="m")\ndef test_x(): pass\n', True),
    ('import sys, pytest\npytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="m")\n', True),
    ('import pytest\nsk = pytest.mark.skipif\n@sk("sys.platform != \'darwin\'")\ndef test_x(): pass\n', True),
    ('import sys, pytest\nIS_DARWIN = sys.platform == "darwin"\ndef f():\n    if not IS_DARWIN:\n        pytest.skip("m")\n', True),
    ('import pytest\nfrom sys import platform as plat\ndef f():\n    if plat != "darwin":\n        pytest.skip("m")\n', True),
    ('import sys as s, pytest\ndef f():\n    if s.platform != "darwin":\n        pytest.skip("m")\n', True),
    ('import platform as p, pytest\ndef f():\n    if p.system() != "Darwin":\n        pytest.skip("m")\n', True),
    ('import sys, pytest\ndef f():\n    if sys.platform != "darwin":\n        pytest.importorskip("maconly")\n', True),
    ('import sys, pytest\ndef f():\n    match sys.platform:\n        case _:\n            pytest.skip("m")\n', True),
    ('import sys, pytest\ndef f():\n    sys.platform == "darwin" or pytest.skip("m")\n', True),
    ('import sys, pytest\ndef f():\n    pytest.skip("m") if sys.platform != "darwin" else None\n', True),
    ('import sys, pytest\nskipper = pytest.skip\ndef f():\n    if sys.platform != "darwin":\n        skipper("m")\n', True),
    ('import sys, pytest\ndef f():\n    if sys.platform != "darwin":\n        getattr(pytest, "skip")("m")\n', True),
    ('import sys, pytest\n@pytest.fixture(autouse=True)\ndef _f():\n    if sys.platform != "darwin":\n        pytest.skip("m")\n', True),
    ('import sys\ndef f():\n    if sys.platform == "darwin":\n        def test_x():\n            pass\n', True),
    ('import sys\ncollect_ignore = []\nif sys.platform != "darwin":\n    collect_ignore.append("t.py")\n', True),
    ('import sys\ncollect_ignore = ["t.py"] if sys.platform != "darwin" else []\n', True),
    ('import sys\ndef pytest_ignore_collect(collection_path, config):\n    if sys.platform != "darwin":\n        return True\n', True),
    ('import sys, pytest\nif sys.platform != "darwin":\n    pytestmark = pytest.mark.skip\n', True),
    ('import sys, pytest\npytestmark = pytest.mark.skip if sys.platform != "darwin" else []\n', True),
    ('import sys, pytest\npytestmark = [pytest.mark.skip] if sys.platform != "darwin" else []\n', True),
    ('import sys, pytest\n@pytest.mark.skipif(condition="sys.platform != \'darwin\'", reason="m")\ndef test_x(): pass\n', True),
    ('import sys, pytest\n@pytest.mark.parametrize("x", [] if sys.platform != "darwin" else [1])\ndef test_x(x): pass\n', True),
    ('import sys, pytest\n@pytest.fixture(params=[] if sys.platform != "darwin" else [1])\ndef f(request): pass\n', True),
    ('import sys, functools, pytest\nmacos_only = functools.partial(pytest.mark.skipif, reason="macOS")\n@macos_only(sys.platform != "darwin")\ndef test_x(): pass\n', True),
    ('import sys, functools, pytest\nskip_mac = functools.partial(pytest.skip, "needs macOS")\ndef f():\n    if sys.platform != "darwin":\n        skip_mac()\n', True),
    ('import sys, pytest\ndef f():\n    if getattr(sys, "platform") != "darwin":\n        pytest.skip("m")\n', True),
    ('import sys, pytest\nif sys.platform != "darwin":\n    pytestmark.append(pytest.mark.skip)\n', True),
    # Look-alikes: prose, plain non-platform gates, and unreadable source.
    ('import pytest\n@pytest.mark.skipif(True, reason="runs on every platform.")\ndef test_x(): pass\n', False),
    ('import pytest\npytestmark = pytest.mark.skip\n', False),
    ('import pytest\npytestmark = pytest.mark.skipif(True, reason="slow")\n', False),
    ('import pytest\n@pytest.mark.parametrize("x", [1, 2])\ndef test_x(x): pass\n', False),
    ('import functools, pytest\nonly_slow = functools.partial(pytest.mark.skipif, reason="slow")\n@only_slow(True)\ndef test_x(): pass\n', False),
    ('import pytest\n@pytest.mark.skipif(condition=True, reason="slow")\ndef test_x(): pass\n', False),
    ('import pytest\ndef f():\n    pytest.skip("not supported on this platform.")\n', False),
    ('import pytest\ndef f():\n    if 1 == 1:\n        pytest.skip("no")\n', False),
    ('import unittest\n@unittest.skip("no")\ndef test_x(self): pass\n', False),
    ('import slow\ncollect_ignore = ["t.py"] if slow.flag else []\n', False),
    ('collect_ignore = ["test_slow.py"]\n', False),
    ('def pytest_ignore_collect(collection_path, config):\n    return True\n', False),
    ('def broken(:\n', False),
)


def test_platform_gate_scan_covers_the_known_spellings(tmp_path):
    """Each spelling a review found, in the scanner's own regression net."""
    probe = tmp_path / "test_probe.py"
    missed, overreach = [], []
    for source, gated in _PLATFORM_GATE_SPELLINGS:
        probe.write_text(source)
        if bool(_platform_gates(probe)) != gated:
            (missed if gated else overreach).append(source)
    assert not missed, (
        "these platform gates are no longer detected, so the file would run "
        f"nowhere in CI unregistered: {missed}")
    assert not overreach, (
        f"these are not platform gates but are now flagged: {overreach}")
