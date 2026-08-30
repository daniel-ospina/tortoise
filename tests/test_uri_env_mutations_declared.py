# tests/test_uri_env_mutations_declared.py
"""Epic #1647 Task 7 Step 5: TORTOISE_DB_URI env-mutation routing guard.

Every delenv/setenv/os.environ mutation of TORTOISE_DB_URI in a NON-allowlist
(migrated) test file must be DECLARED in DELIBERATE_URI_MUTATIONS with its
lane intent. An undeclared mutation reds: a docker-half test that silently
delenvs the URI constructs embedded and passes green — O1/T1's "≥90% on
docker" math then counts it as docker-covered while it never touches the
server (the #942 vacuity vector, cycle-8 P1-3).

Lane intents:
  DELIBERATE_URI            — the site intentionally forces the docker lane
                              (module-level live probes, docker-lane fixture
                              setenv, E2E lane controls, deliberately-
                              unreachable URI probes).
  DELIBERATE_EMBEDDED_LANE  — the site intentionally forces the embedded lane
                              (a VISIBLE lane switch — the test's env control
                              IS the point: embedded-SDK constructions, the
                              relative-path reject probe, localhost-candidates
                              probe).

Carve-out files (TEST_NO_REDIRECT_STEMS, Task 5) are EXEMPT — their
mutations cannot flip a lane (no redirect fires for them by design). The
epic's OWN env-control seam unit surfaces (test_redirect_seam,
test_wipe_server, test_round_trip_parity, test_loopback_predicate_single_source)
are exempt too — env control IS the test input there. ep_e2e_patterns.py and
ep_diagnostic.py are helper scripts (not test_-prefixed modules) with
module-level URI sets — outside the pytest scan set.
"""
from __future__ import annotations

import re
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parent

# ── DELIBERATE_URI_MUTATIONS (cycle-8 P1-3) ─────────────────────────────────
# dict[file, list[regex]] — every mutation line in `file` must match at least
# one declared regex. The regexes are site-CLASS patterns (robust to line
# drift); the lane intent is documented per entry.
DELIBERATE_URI_MUTATIONS: dict[str, list[str]] = {
    # ── DELIBERATE_EMBEDDED_LANE: SDK-level tests force the embedded lane so
    #    the constructions never ride the URI (the delenv IS the point) ──────
    "test_audit.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',
                       r'monkeypatch\.setenv\(\s*$'],
    "test_billing.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_body_cap_sweep.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # #2032: embedded lane via delenv (the test_billing pattern — registry-lane determinism for register/agent mints)
    "test_bridge_mcp.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],
    "test_chain_enforcer.py": [r'monkeypatch\.delenv\("TORTOISE_DB_URI"'],
    "test_github_index_lifecycle.py": [r'monkeypatch\.delenv\("TORTOISE_DB_URI"',
                                        r'monkeypatch\.setenv\("TORTOISE_DB_URI",\s*"docker:'],
    "test_commit_schema.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_embedded_concurrency.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',
                                      r"os\.environ\.pop\(\s*['\"]TORTOISE_DB_URI['\"]"],
    "test_ep_directed_nand.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # MCP-tool + SDK shared-store contract (PR #1684)
    "test_extractor_reliability.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_hard_reject.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_hosted_api.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],  # #1686: register/provision journal tests force the embedded lane (the delenv IS the point)
    "test_index_cli.py": [r'os\.environ\.pop\(\s*["\']TORTOISE_DB_URI["\']'],  # embedded-file-contract module fixture (PR #1684)
    "test_index_restore.py": [r'os\.environ\.pop\(\s*["\']TORTOISE_DB_URI["\']'],  # embedded-file-contract module fixture (PR #1684)
    "test_mcp_client.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],
    "test_mcp_http.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_metering.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_migration_consumers.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_onboarding_integration.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_pack_state.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_quota.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_selfhost.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],
    "test_selfhost_rest.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI",\s*""'],
    "test_turnstile_signup.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    "test_value_extractor.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'],
    # ── DELIBERATE_URI: module-level live-FalkorDB probes (set + restore at
    #    import; the probe asserts the docker lane) ──────────────────────────
    "test_directional_impl.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_directional_impl_fix.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_ep_directional.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                               r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_event_provenance.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_hnsw_vector_index.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_ingest.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_integration_search.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_search_engine.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_session_capture_e2e.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_billing_upgrade.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    # ── DELIBERATE_URI: fixtures/tests that force the docker lane directly ──
    "test_consolidation_4way.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_doctor.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_namespace_uri_mode.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])',
                                     r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"',
                                     r'monkeypatch\.setenv\(\s*$'],
    "test_session_index_health.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    "test_tortoise_client.py": [r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    # ── E2E-8 conformance (Task 8): the leg env control IS the test input —
    #    the embedded leg delenvs the URI, the docker leg setenvs it (the
    #    E2E-1 pattern); declared so the guard stays green ────────────────
    "test_divergence_conformance.py": [r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"',
                                         r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # DELIBERATE_URI: the docker-calibrated cross-lens test (T8 D9) forces
    # the docker lane — its setenv IS the test input.
    "test_cross_lens.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
    # ── Mixed lanes: CLI/HTTP surfaces force BOTH lanes deliberately ────────
    "test_cli_context.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"',
                            r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    "test_cli_serve.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"'],
    "test_extractor_v2.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"'],
    "test_mcp_server.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"'],
    "test_pipeline_cli.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"'],
    "test_run_protocol.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"'],
    "test_search_engine_gaps.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"'],
    # ── test_config: config-resolution unit surface (carve-out stem at Task
    #    5; declared so the guard stays green in the interim) ────────────────
    "test_config.py": [r'monkeypatch\.(?:delenv|setenv)\(\s*"TORTOISE_DB_URI"',
                       r'os\.environ(?:\["TORTOISE_DB_URI"\]\s*=|\.pop\(\s*["\']TORTOISE_DB_URI["\']|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']\])'],
    # ── Task 4 tripwire surface: the probe-flips unit test forces the
    #    docker lane via monkeypatch (DELIBERATE_URI — the env control IS
    #    the test input); the subprocess session tests pass env through
    #    subprocess env= and never mutate this process's environment ────────
    "test_tripwire.py": [r'monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'],
}

# Carve-out TEST-MODULE stems (Task 5 wires these into TEST_NO_REDIRECT_STEMS;
# a carve-out's env mutation cannot flip a lane — no redirect fires for it).
_EXEMPT_FROM_ENV_MUTATION_GUARD = frozenset() | {
    # the epic's own env-control seam unit surfaces — env control IS the input
    "test_redirect_seam.py",
    "test_wipe_server.py",
    "test_round_trip_parity.py",
    "test_loopback_predicate_single_source.py",
    # documented lifecycle carve-outs (Task 9 17-file set)
    "test_embedded_lifecycle.py",
    "test_embedded_lifecycle_fast_close.py",
    "test_reaper.py",
    "test_reaper_orphan.py",
    "test_pre_migration_safety.py",
    "test_ops_safety.py",
    "test_migrate_db.py",
    # this task's own guard files (the uri_env fixture is the test input)
    "test_derived_names.py",
    "test_uri_env_mutations_declared.py",
    "test_markers.py",
}

_MUTATION_RE = re.compile(
    r'monkeypatch\.delenv\(\s*"TORTOISE_DB_URI"'
    r'|monkeypatch\.setenv\(\s*"TORTOISE_DB_URI"'
    r'|monkeypatch\.setenv\(\s*$'
    r'|os\.environ\["TORTOISE_DB_URI"\]\s*='
    r'|os\.environ\.pop\(\s*["\']TORTOISE_DB_URI["\']'
    r'|del\s+os\.environ\[["\']TORTOISE_DB_URI["\']]'
)


def _uri_mutation_sites():
    """Yield (file_name, line_number, line) for every mutation site in a
    NON-allowlist (migrated) test file. The executable census — the plan's
    Step 1 grep is the enumeration, this is its machine form."""
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    exempt = _EXEMPT_FROM_ENV_MUTATION_GUARD | set(TEST_NO_REDIRECT_STEMS)
    for f in sorted(_TESTS_ROOT.glob("test_*.py")) \
            + sorted((_TESTS_ROOT / "e2e").glob("test_*.py")):
        if f.name in exempt:
            continue
        src = f.read_text(encoding="utf-8")
        for m in _MUTATION_RE.finditer(src):
            line = src.count("\n", 0, m.start()) + 1
            yield f.name, line, src.split("\n")[line - 1].strip()


def test_uri_env_mutations_declared():
    """Cycle-8 P1-3: every TORTOISE_DB_URI mutation in a migrated test file
    is declared in DELIBERATE_URI_MUTATIONS — an undeclared delenv/setenv on
    a docker half silently constructs embedded and passes green (the #942
    vacuity vector). A NEW mutation in a migrated file reds until triaged."""
    undeclared = []
    for fname, lineno, line in _uri_mutation_sites():
        declared = DELIBERATE_URI_MUTATIONS.get(fname)
        if not declared or not any(re.search(p, line) for p in declared):
            undeclared.append(f"{fname}:{lineno}: {line}")
    assert not undeclared, (
        "undeclared TORTOISE_DB_URI mutation(s) — triage into "
        "DELIBERATE_URI_MUTATIONS (DELIBERATE_URI or "
        "DELIBERATE_EMBEDDED_LANE):\n" + "\n".join(undeclared))


def test_uri_mutations_declared_files_exist():
    """The table's file keys must resolve to real test modules — a typo'd
    key silently exempts nothing (and the guard's missing-file red would be
    the only signal)."""
    for fname in DELIBERATE_URI_MUTATIONS:
        assert (_TESTS_ROOT / fname).exists() \
            or (_TESTS_ROOT / "e2e" / fname).exists(), \
            f"DELIBERATE_URI_MUTATIONS key {fname!r} is not a test module"
